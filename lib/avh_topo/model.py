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

SPACING_MIN_M = 0.1
SPACING_MAX_M = 50.0

# A read is taken to belong to a frame when this share of its points
# falls inside the cloud's bounding box, with FRAME_TOLERANCE_FT slack.
FRAME_SHARE = 0.9
FRAME_TOLERANCE_FT = 1.0 / METRES_PER_FOOT

# How close the built toposolid's top has to be to the highest point.
Z_TOLERANCE_FT = 0.01 / METRES_PER_FOOT

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


def average_distance(spacing_ft):
    """The averageDistance to ask GetPoints for, in feet."""
    return spacing_ft / SAMPLES_PER_SPACING


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

    ABSOLUTE when the built top matches the highest point given, RELATIVE
    when it matches that plus the level's elevation, None when neither.
    On a level at zero both readings agree and ABSOLUTE is returned.
    """
    if built_top is None:
        return None
    if abs(built_top - highest_given) <= tolerance:
        return ABSOLUTE
    if abs(built_top - (highest_given + level_elevation)) <= tolerance:
        return RELATIVE
    return None


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
