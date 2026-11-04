# -*- coding: utf-8 -*-
"""Turning point cloud samples into toposolid points. No Revit here.

The tool reads a linked point cloud in tiles, averages what it reads into
a grid, and hands one point per occupied cell to `Toposolid.Create`. All
of the arithmetic between "points came back" and "points go in" lives
here, so it can be tested without Revit.

## What the API leaves unsaid, and how this module copes

Two things the Revit API documentation does not state, both of which put
a toposolid somewhere plausible and wrong if guessed:

1. **Which coordinate system `GetPoints` returns.** The filter is
   documented as model coordinates; the points are not documented at
   all. Rather than guess, `pick_frame` tests both readings against the
   cloud's own bounding box in the model and keeps the one that fits.
2. **Whether the Z of a toposolid point is absolute or relative to its
   level.** `z_mode` compares what Revit built with both readings after
   the fact, so the script can correct a relative reading or refuse.

Both are measured in Revit on every run rather than settled once here.
"""

import math

METRES_PER_FOOT = 0.3048

# The most GetPoints is asked for per call. The documentation says "The
# number of points read must range from 1 to 1000000", and Revit refused
# exactly 1000000 with that same message on the first live run
# (2026-11-04). The upper bound is exclusive in practice, whatever the
# sentence says.
MAX_POINTS_PER_CALL = 999999

# averageDistance is asked for at half the grid spacing, so a fully
# covered cell is fed about four samples. More only costs time in
# IronPython, which iterates every one of them.
SAMPLES_PER_SPACING = 2.0

# A tile is this many grid cells on a side. At two samples per spacing
# that is 800 x 800 = 640 000 samples for a fully covered tile, under the
# per call cap with room to spare. A tile that comes back full is split.
TILE_CELLS = 400

# A tile that still comes back at the cap after this many splits is read
# as it is and reported, rather than recursing forever.
MAX_SPLITS = 3

# The confirmation warns above this many toposolid points. Not a Revit
# limit: a number past which a toposolid gets slow to edit and regenerate.
WARN_POINTS = 20000

# The ground filter, for clouds that still hold people, vehicles and
# plants. See `LowGrid` and `ground_filter`.
LOW_RANK = 3
# The filter reads the cloud twice as densely as averaging does, about
# sixteen samples to a fully covered cell, because the ground under a
# shrub or beside a wheel is only a few of them.
FILTER_SAMPLES_PER_SPACING = 4.0
WINDOW_MIN_M = 0.5
WINDOW_MAX_M = 30.0
WINDOW_DEFAULT_M = 4.0
# How far a cell may stand above the opened ground and still count as
# ground. Fixed at half a metre because AVH's sites are rough lava with
# steps and slopes (Bjorn, 2026-11-04): a tighter limit would cut real
# lava, a looser one would keep low cars and shrubs. A step or a steady
# slope never reaches it, since an opening leaves both unchanged.
FILTER_LIMIT_FT = 0.5 / METRES_PER_FOOT
# The confirmation warns when the filter takes more than this share of
# the cells, which usually means the window is wider than the terrain's
# own features.
FILTER_WARN_SHARE = 0.25

SPACING_MIN_M = 0.1
SPACING_MAX_M = 50.0

# A read is taken to belong to a frame when this share of its points
# falls inside the cloud's bounding box, with FRAME_TOLERANCE_FT slack.
FRAME_SHARE = 0.9
FRAME_TOLERANCE_FT = 1.0 / METRES_PER_FOOT

# How close the built toposolid's top has to be to the highest point.
#
# This check is there to catch errors of metres: points read in the wrong
# frame, or heights read relative to a level 26 m down. It is not a
# survey tolerance. The first toposolid Revit built (2026-11-04) had its
# top 65 mm under the highest point given, and a 10 mm tolerance threw
# it away. How far Revit's own vertices sit from the cloud is reported
# separately, by `vertex_gaps`, rather than judged here.
Z_TOLERANCE_FT = 0.5 / METRES_PER_FOOT

# A toposolid vertex and a given point are the same point when they sit
# within this of each other in plan.
VERTEX_MATCH_FT = 0.001 / METRES_PER_FOOT

ABSOLUTE = u"absolute"
RELATIVE = u"relative"
RAW = u"raw"
TRANSFORMED = u"transformed"


def metres_to_feet(metres):
    return float(metres) / METRES_PER_FOOT


def feet_to_metres(feet):
    return float(feet) * METRES_PER_FOOT


def spacing_error(metres):
    """Why a spacing is unusable, or u"" when it is fine."""
    if metres is None or metres != metres:
        return u"The spacing is not a number."
    if metres < SPACING_MIN_M:
        return u"A spacing under {0} m would make far too many points.".format(
            SPACING_MIN_M)
    if metres > SPACING_MAX_M:
        return u"A spacing over {0} m would lose the terrain.".format(
            SPACING_MAX_M)
    return u""


def window_error(metres, spacing_m):
    """Why a filter window is unusable, or u"" when it is fine."""
    if metres is None or metres != metres:
        return u"The size is not a number."
    if metres < WINDOW_MIN_M:
        return u"Objects under {0} m are below what the filter can " \
               u"see.".format(WINDOW_MIN_M)
    if metres > WINDOW_MAX_M:
        return u"Over {0} m the filter would flatten the terrain " \
               u"itself.".format(WINDOW_MAX_M)
    if metres < spacing_m:
        return u"The object size has to be at least the point spacing " \
               u"({0} m).".format(spacing_m)
    return u""


def average_distance(spacing_ft, samples=None):
    """The averageDistance to ask GetPoints for, in feet."""
    return spacing_ft / (samples or SAMPLES_PER_SPACING)


def tile_side(spacing_ft, samples=None):
    """A tile's side in feet, keeping a full tile's samples the same
    whatever the sampling density: TILE_CELLS cells at the default."""
    samples = samples or SAMPLES_PER_SPACING
    return spacing_ft * TILE_CELLS * SAMPLES_PER_SPACING / samples


def tiles(min_x, min_y, max_x, max_y, side):
    """Rectangles of at most `side` covering the extent, row by row.

    Each is (x0, y0, x1, y1). Neighbours share an edge; a sample on that
    edge can come back from both, which only weights it twice in one
    cell's average.
    """
    if side <= 0 or max_x < min_x or max_y < min_y:
        return []
    found = []
    columns = max(1, int(math.ceil((max_x - min_x) / side)))
    rows = max(1, int(math.ceil((max_y - min_y) / side)))
    for row in range(rows):
        y0 = min_y + row * side
        y1 = min(max_y, y0 + side)
        for column in range(columns):
            x0 = min_x + column * side
            x1 = min(max_x, x0 + side)
            found.append((x0, y0, x1, y1))
    return found


def quarters(tile):
    """A tile split in four, for one that came back at the point cap."""
    x0, y0, x1, y1 = tile
    mx = (x0 + x1) / 2.0
    my = (y0 + y1) / 2.0
    return [(x0, y0, mx, my), (mx, y0, x1, my),
            (x0, my, mx, y1), (mx, my, x1, y1)]


def tile_planes(tile):
    """The four vertical planes bounding a tile, as (normal, origin).

    CreateMultiPlaneFilter keeps the points on the positive side of every
    plane, so each normal points into the tile. Z is left open.
    """
    x0, y0, x1, y1 = tile
    return [((1.0, 0.0, 0.0), (x0, y0, 0.0)),
            ((-1.0, 0.0, 0.0), (x1, y0, 0.0)),
            ((0.0, 1.0, 0.0), (x0, y0, 0.0)),
            ((0.0, -1.0, 0.0), (x0, y1, 0.0))]


def apply_transform(transform, x, y, z):
    """A point through a transform given as (origin, x, y, z) tuples.

    Written out rather than calling Transform.OfPoint because it runs
    once per sample, and building a .NET XYZ for each is the slow part.
    """
    origin, bx, by, bz = transform
    return (origin[0] + x * bx[0] + y * by[0] + z * bz[0],
            origin[1] + x * bx[1] + y * by[1] + z * bz[1],
            origin[2] + x * bx[2] + y * by[2] + z * bz[2])


def is_identity(transform, tolerance=1e-9):
    origin, bx, by, bz = transform
    wanted = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
              (0.0, 0.0, 1.0))
    for got, want in zip((origin, bx, by, bz), wanted):
        for a, b in zip(got, want):
            if abs(a - b) > tolerance:
                return False
    return True


def share_inside(points, box_min, box_max, tolerance=FRAME_TOLERANCE_FT):
    """The share of points inside a box, with slack. 0.0 for no points."""
    if not points:
        return 0.0
    inside = 0
    for point in points:
        if all(box_min[i] - tolerance <= point[i] <= box_max[i] + tolerance
               for i in range(3)):
            inside += 1
    return float(inside) / len(points)


def pick_frame(raw, transform, box_min, box_max):
    """Which reading of GetPoints' output fits the cloud's bounding box.

    Returns (RAW or TRANSFORMED or None, raw share, transformed share).

    Where both fit, which happens when the cloud's transform is small
    against its size, TRANSFORMED wins. That is a convention, not a
    measurement, and an unverified one: the documentation says nothing
    either way. Where neither fits, None, and the script stops rather
    than guess.
    """
    if is_identity(transform):
        return RAW, 1.0, 1.0
    moved = [apply_transform(transform, p[0], p[1], p[2]) for p in raw]
    raw_share = share_inside(raw, box_min, box_max)
    moved_share = share_inside(moved, box_min, box_max)
    if moved_share >= FRAME_SHARE and moved_share >= raw_share:
        return TRANSFORMED, raw_share, moved_share
    if raw_share >= FRAME_SHARE:
        return RAW, raw_share, moved_share
    return None, raw_share, moved_share


class Grid(object):
    """Samples averaged into square cells on the model's X and Y.

    Each occupied cell yields the mean of its samples in all three axes,
    not the cell centre with a mean height. On a slope the samples in a
    cell are rarely centred in it, and pairing their mean height with the
    centre's position tilts the surface by up to a quarter of the spacing
    times the slope. The mean of real points lies on the terrain.
    """

    def __init__(self, origin_x, origin_y, spacing):
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.spacing = float(spacing)
        self.cells = {}
        self.samples = 0

    def key(self, x, y):
        return (int(math.floor((x - self.origin_x) / self.spacing)),
                int(math.floor((y - self.origin_y) / self.spacing)))

    def add(self, x, y, z):
        key = self.key(x, y)
        cell = self.cells.get(key)
        if cell is None:
            self.cells[key] = [x, y, z, 1]
        else:
            cell[0] += x
            cell[1] += y
            cell[2] += z
            cell[3] += 1
        self.samples += 1

    def points(self):
        """One (x, y, z) per occupied cell, in a stable order."""
        found = []
        for key in sorted(self.cells):
            sx, sy, sz, count = self.cells[key]
            found.append((sx / count, sy / count, sz / count))
        return found


class LowGrid(object):
    """Samples sorted into square cells, keeping each cell's lowest few.

    For a cloud that still holds people, vehicles and plants. Each cell
    yields its LOW_RANK-th lowest sample, a real measured point, rather
    than a mean. Taking the very lowest would let one stray return under
    the ground, which scanners produce, drag the surface down; the third
    lowest needs three of them in one cell.

    A cell with fewer samples than that yields its highest one, so a
    sparse cell is not discarded, but it is also not protected.

    Same interface as Grid, so the reading code does not care which.
    """

    def __init__(self, origin_x, origin_y, spacing, rank=None):
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.spacing = float(spacing)
        self.rank = rank or LOW_RANK
        self.cells = {}
        self.samples = 0

    def key(self, x, y):
        return (int(math.floor((x - self.origin_x) / self.spacing)),
                int(math.floor((y - self.origin_y) / self.spacing)))

    def add(self, x, y, z):
        key = self.key(x, y)
        low = self.cells.get(key)
        self.samples += 1
        if low is None:
            self.cells[key] = [(z, x, y)]
            return
        if len(low) >= self.rank and z >= low[-1][0]:
            return
        low.append((z, x, y))
        low.sort()
        if len(low) > self.rank:
            low.pop()

    def by_cell(self):
        """{cell key: (x, y, z)}, the chosen sample of each cell."""
        found = {}
        for key, low in self.cells.items():
            z, x, y = low[-1]
            found[key] = (x, y, z)
        return found

    def points(self):
        chosen = self.by_cell()
        return [chosen[key] for key in sorted(chosen)]


def _sliding(values, radius, pick, empty):
    """Min or max over a window of 2 * radius + 1, along one row."""
    count = len(values)
    found = [empty] * count
    for index in range(count):
        low = max(0, index - radius)
        high = min(count, index + radius + 1)
        best = empty
        for value in values[low:high]:
            if value is not empty and (best is empty or
                                       pick(value, best) == value):
                best = value
        found[index] = best
    return found


def _square(rows, radius, pick, empty):
    """Min or max over a square window, as two passes, rows then columns."""
    rows = [_sliding(row, radius, pick, empty) for row in rows]
    columns = [list(column) for column in zip(*rows)]
    columns = [_sliding(column, radius, pick, empty) for column in columns]
    return [list(row) for row in zip(*columns)]


def window_cells(window_ft, spacing_ft):
    """Half width, in cells, of a square window at least `window_ft` wide."""
    return max(1, int(math.ceil(window_ft / (2.0 * spacing_ft))))


def ground_filter(cells, spacing_ft, window_ft, limit_ft=None):
    """Drop cells standing above the ground around them.

    `cells` is {cell key: (x, y, z)}. Returns (kept points, removed
    points), each in a stable order.

    This is a morphological opening: every cell takes the lowest height
    within the window around it (erosion), then the highest of those
    within the same window (dilation). The result follows the ground but
    cannot rise into anything narrower than the window, so a truck,
    person or shrub narrower than it is cut off underneath. A cell more
    than `limit_ft` above that opened surface is taken to be an object.

    What it gets wrong, by its nature:

    - A real lump of ground narrower than the window is cut the same
      way: a lava ridge, a cairn, the crest of an embankment.
    - Anything wider than the window survives: a building, a row of
      parked trucks.
    - A step or a steady slope survives intact, because an opening
      leaves both unchanged; only things that stand up from both sides
      are cut.

    The edge of the scan and holes in it are not read as objects; see
    the margin below. A removed cell is simply not given to Revit, which
    fills it in from its neighbours.
    """
    if limit_ft is None:
        limit_ft = FILTER_LIMIT_FT
    if not cells:
        return [], []
    radius = window_cells(window_ft, spacing_ft)
    keys = list(cells.keys())
    # A margin of one half width all round. Without it, a plain slope
    # loses its uphill edge: the window there finds no ground beyond the
    # edge, so the opening comes back lower than the last cells and they
    # read as objects. With it, the erosion is also worked out in the
    # margin, from the cells it can reach, and the dilation then finds
    # the edge heights again. A hole in the scan is the same case and is
    # handled the same way, since empty cells inside are worked out too.
    min_i = min(k[0] for k in keys) - radius
    min_j = min(k[1] for k in keys) - radius
    width = max(k[0] for k in keys) + radius - min_i + 1
    depth = max(k[1] for k in keys) + radius - min_j + 1
    empty = None
    rows = [[empty] * width for _row in range(depth)]
    for key, point in cells.items():
        rows[key[1] - min_j][key[0] - min_i] = point[2]

    eroded = _square(rows, radius, min, empty)
    opened = _square(eroded, radius, max, empty)

    kept = []
    removed = []
    for key in sorted(cells):
        point = cells[key]
        floor = opened[key[1] - min_j][key[0] - min_i]
        if floor is not None and point[2] - floor > limit_ft:
            removed.append(point)
        else:
            kept.append(point)
    return kept, removed


def extent(points):
    """(min_x, min_y, min_z, max_x, max_y, max_z), or None."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def boundary(points, minimum=1e-3):
    """The rectangle through the outermost points, counter clockwise.

    Four (x, y) corners, or None when the points do not span an area.
    Every point lies inside or on it, by construction, which is what
    Toposolid.Create is given. A corner with no point near it takes the
    height Revit interpolates, which on an irregular site is a guess.
    """
    box = extent(points)
    if box is None:
        return None
    min_x, min_y, _z0, max_x, max_y, _z1 = box
    if max_x - min_x < minimum or max_y - min_y < minimum:
        return None
    return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]


def shifted(points, dz):
    return [(p[0], p[1], p[2] + dz) for p in points]


def z_mode(highest_given, built_top, level_elevation,
           tolerance=Z_TOLERANCE_FT):
    """How Revit read the Z of the points it was given.

    ABSOLUTE when the built top is nearer the highest point given, RELATIVE
    when it is nearer that plus the level's elevation, None when it is
    within `tolerance` of neither. On a level near zero the two readings
    nearly agree and the nearer wins, which is right to within the
    level's elevation either way.
    """
    if built_top is None:
        return None
    off_absolute = abs(built_top - highest_given)
    off_relative = abs(built_top - (highest_given + level_elevation))
    if min(off_absolute, off_relative) > tolerance:
        return None
    if off_absolute <= off_relative:
        return ABSOLUTE
    return RELATIVE


def below_bottom(lowest, level_elevation, thickness):
    """How far the lowest point sits under the toposolid's flat bottom.

    0.0 when it does not. The bottom is taken to be the level's elevation
    less the type's thickness. That is a reading of one live run, not a
    documented rule: on its first level the build failed with "too thin
    for its given type", and on a level 26 m lower it built.
    """
    bottom = level_elevation - thickness
    return max(0.0, bottom - lowest)


def vertex_gaps(points, vertices, match=VERTEX_MATCH_FT):
    """How Revit's vertices compare with the points it was given.

    Pairs each point with a vertex at the same place in plan, then
    reports the height differences. None when there are no vertices.

    Returns a dict: `matched` points that have a vertex, `missing` points
    that do not, `extra` vertices that match no point (the outline's
    corners at least), `worst` and `mean` absolute height difference in
    feet over the matched ones, and `worst_at`, the point where the worst
    one is.
    """
    if not vertices:
        return None
    cells = {}
    for vertex in vertices:
        key = (int(math.floor(vertex[0] / match)),
               int(math.floor(vertex[1] / match)))
        cells.setdefault(key, []).append(vertex)

    used = set()
    gaps = []
    worst = 0.0
    worst_at = None
    missing = 0
    for point in points:
        kx = int(math.floor(point[0] / match))
        ky = int(math.floor(point[1] / match))
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for vertex in cells.get((kx + dx, ky + dy), ()):
                    if id(vertex) in used:
                        continue
                    if (abs(vertex[0] - point[0]) <= match and
                            abs(vertex[1] - point[1]) <= match):
                        found = vertex
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            missing += 1
            continue
        used.add(id(found))
        gap = abs(found[2] - point[2])
        gaps.append(gap)
        if gap >= worst:
            worst = gap
            worst_at = point
    return {
        "matched": len(gaps),
        "missing": missing,
        "extra": len(vertices) - len(used),
        "worst": worst,
        "mean": sum(gaps) / len(gaps) if gaps else 0.0,
        "worst_at": worst_at,
    }


def summary(points, spacing_ft):
    """Numbers for the confirmation, in metres."""
    box = extent(points)
    if box is None:
        return None
    min_x, min_y, min_z, max_x, max_y, max_z = box
    return {
        "count": len(points),
        "width": feet_to_metres(max_x - min_x),
        "depth": feet_to_metres(max_y - min_y),
        "low": feet_to_metres(min_z),
        "high": feet_to_metres(max_z),
        "spacing": feet_to_metres(spacing_ft),
    }
