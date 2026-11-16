# -*- coding: utf-8 -*-
"""The parts of clash detection that need no Revit.

Everything here works on plain numbers, so it runs under CPython in
test_clash_model.py exactly as it runs under IronPython in Revit.

Units and frame: every position and box is in **metres, relative to a
run's own origin** (the middle of everything being tested). Revit works
in feet from the project's internal origin, which on a georeferenced job
can be far enough away to cost float32 its millimetres, and the viewer
holds positions as float32.

A box is a tuple (x0, y0, z0, x1, y1, z1). A mesh is two flat arrays:
positions (x, y, z, x, y, z, ...) and triangle indices (a, b, c, ...).
"""

import array
import base64

FEET_TO_M = 0.3048
CUBIC_FEET_TO_M3 = FEET_TO_M ** 3

# Boxes that only touch are not candidates: a wall standing on a floor
# shares a plane with it and nothing else. One hundredth of a millimetre.
TOUCHING = 1e-5

# How far inside a crossing must be before it counts, as a fraction of
# the segment and of the triangle. An edge that ends exactly on a face
# touches it, and so does one that passes exactly through a triangle's
# own edge: a floor's diagonal runs under the foot of every wall
# standing on it. Neither has gone through anything.
INSIDE = 1e-5


# --------------------------------------------------------------------------
# Typed arrays
# --------------------------------------------------------------------------

# The array type codes for a 4 byte float and a 4 byte unsigned integer.
# Spelled with chr() because the static IronPython check reads a quoted
# single f as the start of an f-string.
FLOAT32 = chr(102)
UINT32 = chr(73)


def new_floats():
    return array.array(FLOAT32)


def new_indices():
    return array.array(UINT32)


def raw_bytes(values):
    """The array's bytes: tobytes on Python 3, tostring on IronPython."""
    try:
        return values.tobytes()
    except AttributeError:
        return values.tostring()


def b64_text(values):
    data = base64.b64encode(raw_bytes(values))
    if isinstance(data, bytes) and not isinstance(data, type(u"")):
        data = data.decode("ascii")
    return data


# --------------------------------------------------------------------------
# Boxes
# --------------------------------------------------------------------------

def bounds(positions, start=0, stop=None):
    """Box around positions[start:stop], or None when there are none."""
    if stop is None:
        stop = len(positions)
    if stop - start < 3:
        return None
    x0 = x1 = positions[start]
    y0 = y1 = positions[start + 1]
    z0 = z1 = positions[start + 2]
    index = start + 3
    while index < stop:
        x = positions[index]
        y = positions[index + 1]
        z = positions[index + 2]
        if x < x0:
            x0 = x
        elif x > x1:
            x1 = x
        if y < y0:
            y0 = y
        elif y > y1:
            y1 = y
        if z < z0:
            z0 = z
        elif z > z1:
            z1 = z
        index += 3
    return (x0, y0, z0, x1, y1, z1)


def union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def intersection(a, b):
    """The box both share, or None when they do not overlap."""
    box = (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]),
           min(a[3], b[3]), min(a[4], b[4]), min(a[5], b[5]))
    if box[0] > box[3] or box[1] > box[4] or box[2] > box[5]:
        return None
    return box


def boxes_overlap(a, b, margin=TOUCHING):
    """True when the boxes go into each other by more than `margin`."""
    return (a[0] < b[3] - margin and b[0] < a[3] - margin and
            a[1] < b[4] - margin and b[1] < a[4] - margin and
            a[2] < b[5] - margin and b[2] < a[5] - margin)


def center(box):
    return ((box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5,
            (box[2] + box[5]) * 0.5)


def extents(box):
    return (box[3] - box[0], box[4] - box[1], box[5] - box[2])


def pad(box, minimum, fraction=0.0):
    """The box grown on every side by `fraction` of its largest
    dimension, and by at least `minimum`."""
    margin = max(minimum, max(extents(box)) * fraction)
    return (box[0] - margin, box[1] - margin, box[2] - margin,
            box[3] + margin, box[4] + margin, box[5] + margin)


def to_local(box_feet, origin_feet):
    """A box in Revit feet -> metres from the run's origin."""
    ox, oy, oz = origin_feet
    f = FEET_TO_M
    return ((box_feet[0] - ox) * f, (box_feet[1] - oy) * f,
            (box_feet[2] - oz) * f, (box_feet[3] - ox) * f,
            (box_feet[4] - oy) * f, (box_feet[5] - oz) * f)


def to_feet(box_local, origin_feet):
    """The inverse of to_local."""
    ox, oy, oz = origin_feet
    f = FEET_TO_M
    return (box_local[0] / f + ox, box_local[1] / f + oy,
            box_local[2] / f + oz, box_local[3] / f + ox,
            box_local[4] / f + oy, box_local[5] / f + oz)


# --------------------------------------------------------------------------
# Broad phase: which pairs are worth a real test
# --------------------------------------------------------------------------

def candidate_pairs(set_a, set_b, margin=TOUCHING):
    """Pairs (key_a, key_b) whose boxes overlap, each pair once.

    `set_a` and `set_b` are lists of (key, box). A key may be in both
    sets; it is never paired with itself, and two keys that are both in
    both sets are paired once, not once each way round.

    Sweep along x: sorted by where each box starts, a box only has to be
    compared with the boxes of the other set that have not ended yet.
    """
    entries = []
    for key, box in set_a:
        entries.append((box[0], 0, key, box))
    for key, box in set_b:
        entries.append((box[0], 1, key, box))
    entries.sort(key=lambda entry: entry[0])

    active = ([], [])
    seen = set()
    pairs = []
    for low, side, key, box in entries:
        others = active[1 - side]
        alive = []
        for other in others:
            other_key, other_box = other
            if other_box[3] - margin <= low:
                continue                    # ended before this one starts
            alive.append(other)
            if other_key == key:
                continue
            if not (box[1] < other_box[4] - margin and
                    other_box[1] < box[4] - margin and
                    box[2] < other_box[5] - margin and
                    other_box[2] < box[5] - margin):
                continue
            ordered = (key, other_key) if key < other_key \
                else (other_key, key)
            if ordered in seen:
                continue
            seen.add(ordered)
            pairs.append((key, other_key) if side == 0
                         else (other_key, key))
        others[:] = alive
        active[side].append((key, box))
    return pairs


# --------------------------------------------------------------------------
# Narrow phase on triangles, for geometry Revit cannot intersect as solids
# --------------------------------------------------------------------------

def pierce(p, q, a, b, c):
    """Where the segment p-q goes through triangle a-b-c, or None.

    Going through means crossing strictly inside the triangle, strictly
    between the segment's ends. A segment that lies in the plane, stops
    on it, or only meets the triangle's edge touches it and does not
    count: that is two elements sharing a face, which is not a clash.
    A real crossing that happens to run exactly through an edge is
    still found by the other edges around it.
    """
    dx = q[0] - p[0]
    dy = q[1] - p[1]
    dz = q[2] - p[2]
    e1x = b[0] - a[0]
    e1y = b[1] - a[1]
    e1z = b[2] - a[2]
    e2x = c[0] - a[0]
    e2y = c[1] - a[1]
    e2z = c[2] - a[2]
    hx = dy * e2z - dz * e2y
    hy = dz * e2x - dx * e2z
    hz = dx * e2y - dy * e2x
    det = e1x * hx + e1y * hy + e1z * hz
    scale = ((e1x * e1x + e1y * e1y + e1z * e1z) *
             (e2x * e2x + e2y * e2y + e2z * e2z) *
             (dx * dx + dy * dy + dz * dz))
    if det * det <= 1e-14 * scale:
        return None                         # parallel, or degenerate
    inv = 1.0 / det
    sx = p[0] - a[0]
    sy = p[1] - a[1]
    sz = p[2] - a[2]
    u = inv * (sx * hx + sy * hy + sz * hz)
    if u <= INSIDE or u >= 1.0 - INSIDE:
        return None
    qx = sy * e1z - sz * e1y
    qy = sz * e1x - sx * e1z
    qz = sx * e1y - sy * e1x
    v = inv * (dx * qx + dy * qy + dz * qz)
    if v <= INSIDE or u + v >= 1.0 - INSIDE:
        return None
    t = inv * (e2x * qx + e2y * qy + e2z * qz)
    if t <= INSIDE or t >= 1.0 - INSIDE:
        return None
    return (p[0] + dx * t, p[1] + dy * t, p[2] + dz * t)


def _triangles(positions, indices, region):
    """Triangles touching `region`, each with its own box, sorted by x."""
    found = []
    rx0, ry0, rz0, rx1, ry1, rz1 = region
    for n in range(0, len(indices) - 2, 3):
        i = indices[n] * 3
        j = indices[n + 1] * 3
        k = indices[n + 2] * 3
        ax, ay, az = positions[i], positions[i + 1], positions[i + 2]
        bx, by, bz = positions[j], positions[j + 1], positions[j + 2]
        cx, cy, cz = positions[k], positions[k + 1], positions[k + 2]
        x0 = min(ax, bx, cx)
        x1 = max(ax, bx, cx)
        if x1 < rx0 or x0 > rx1:
            continue
        y0 = min(ay, by, cy)
        y1 = max(ay, by, cy)
        if y1 < ry0 or y0 > ry1:
            continue
        z0 = min(az, bz, cz)
        z1 = max(az, bz, cz)
        if z1 < rz0 or z0 > rz1:
            continue
        found.append((x0, x1, y0, y1, z0, z1,
                      (ax, ay, az), (bx, by, bz), (cx, cy, cz)))
    found.sort(key=lambda triangle: triangle[0])
    return found


def mesh_crossings(pos_a, idx_a, pos_b, idx_b, region,
                   max_points=600, max_tests=300000):
    """Points where the surface of mesh a crosses the surface of mesh b
    inside `region`. Returns (points, complete); complete is False when
    the work was cut short by one of the limits."""
    tris_a = _triangles(pos_a, idx_a, region)
    tris_b = _triangles(pos_b, idx_b, region)
    points = []
    tests = 0
    start = 0
    count_b = len(tris_b)
    for ta in tris_a:
        ax0, ax1 = ta[0], ta[1]
        # ax0 only grows, so a leading run of b that ends before it is
        # finished for good. The rest are skipped one at a time below.
        while start < count_b and tris_b[start][1] < ax0:
            start += 1
        for n in range(start, count_b):
            tb = tris_b[n]
            if tb[0] > ax1:
                break
            if tb[1] < ax0 or tb[3] < ta[2] or tb[2] > ta[3] or \
                    tb[5] < ta[4] or tb[4] > ta[5]:
                continue
            tests += 1
            a0, a1, a2 = ta[6], ta[7], ta[8]
            b0, b1, b2 = tb[6], tb[7], tb[8]
            for p, q in ((a0, a1), (a1, a2), (a2, a0)):
                hit = pierce(p, q, b0, b1, b2)
                if hit is not None:
                    points.append(hit)
            for p, q in ((b0, b1), (b1, b2), (b2, b0)):
                hit = pierce(p, q, a0, a1, a2)
                if hit is not None:
                    points.append(hit)
            if len(points) >= max_points or tests >= max_tests:
                return points, False
    return points, True


def point_inside(point, positions, indices, box):
    """True when `point` is inside the closed mesh: a ray from it leaves
    through an odd number of triangles. The ray is skewed off the axes
    so it does not run along the edges of orthogonal geometry."""
    reach = max(extents(box)) * 4.0 + 1.0
    far = (point[0] + reach, point[1] + reach * 0.3713,
           point[2] + reach * 0.2291)
    crossings = 0
    for n in range(0, len(indices) - 2, 3):
        i = indices[n] * 3
        j = indices[n + 1] * 3
        k = indices[n + 2] * 3
        a = (positions[i], positions[i + 1], positions[i + 2])
        b = (positions[j], positions[j + 1], positions[j + 2])
        c = (positions[k], positions[k + 1], positions[k + 2])
        if pierce(point, far, a, b, c) is not None:
            crossings += 1
    return crossings % 2 == 1


def _first_point(positions):
    return (positions[0], positions[1], positions[2])


def mesh_clash(pos_a, idx_a, box_a, pos_b, idx_b, box_b):
    """Do two triangle meshes go through each other?

    Returns None when they do not, otherwise a dict:
        box       around where the surfaces cross
        center    the middle of the crossing points
        complete  False when the search hit a limit and stopped early
        inside    True when one sits wholly inside the other, so the
                  surfaces never cross at all

    It finds where surfaces cross, which a solid intersection does not
    need, so it has no volume to report and cannot say how deep the
    clash is.
    """
    region = intersection(box_a, box_b)
    if region is None or not len(idx_a) or not len(idx_b):
        return None
    region = pad(region, 1e-4)
    points, complete = mesh_crossings(pos_a, idx_a, pos_b, idx_b, region)
    if points:
        flat = []
        sx = sy = sz = 0.0
        for x, y, z in points:
            flat.extend((x, y, z))
            sx += x
            sy += y
            sz += z
        count = float(len(points))
        return {"box": bounds(flat),
                "center": (sx / count, sy / count, sz / count),
                "complete": complete, "inside": False}
    if not complete:
        return None
    # No surface crosses the other. One may still be swallowed whole.
    if point_inside(_first_point(pos_b), pos_a, idx_a, box_a):
        inner = intersection(box_a, box_b)
        return {"box": inner, "center": center(inner),
                "complete": True, "inside": True}
    if point_inside(_first_point(pos_a), pos_b, idx_b, box_b):
        inner = intersection(box_a, box_b)
        return {"box": inner, "center": center(inner),
                "complete": True, "inside": True}
    return None


# --------------------------------------------------------------------------
# Distance between surfaces, for clearance zones (soft clashes)
#
# Two elements that do not clash can still be too close: a pipe needs
# room round it for insulation, hangers and hands. The gap is measured
# between the two triangle surfaces, the same triangles the viewer
# draws, so it works the same for a pipe, a fitting, a beam or a mesh.
#
# A curved surface is drawn as flat facets inside the true surface, so
# the gap to a pipe reads a little wide: at most r(1 - cos(pi / n)) for
# n facets round, under 1 mm for a DN100 pipe at medium detail.
# --------------------------------------------------------------------------

def _minus(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _along(a, d, t):
    return (a[0] + d[0] * t, a[1] + d[1] * t, a[2] + d[2] * t)


def _clamp(value):
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def closest_on_triangle(p, a, b, c):
    """The point of triangle a-b-c nearest to p (Ericson, Real-Time
    Collision Detection, 5.1.5)."""
    ab = _minus(b, a)
    ac = _minus(c, a)
    ap = _minus(p, a)
    d1 = _dot(ab, ap)
    d2 = _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a
    bp = _minus(p, b)
    d3 = _dot(ab, bp)
    d4 = _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0 and d1 != d3:
        return _along(a, ab, d1 / (d1 - d3))
    cp = _minus(p, c)
    d5 = _dot(ab, cp)
    d6 = _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0 and d2 != d6:
        return _along(a, ac, d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0 and \
            (d4 - d3) + (d5 - d6) != 0.0:
        return _along(b, _minus(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6)))
    total = va + vb + vc
    if total == 0.0:
        return a                            # a degenerate triangle
    v = vb / total
    w = vc / total
    return (a[0] + ab[0] * v + ac[0] * w, a[1] + ab[1] * v + ac[1] * w,
            a[2] + ab[2] * v + ac[2] * w)


def closest_on_segments(p1, q1, p2, q2):
    """The nearest points of segments p1-q1 and p2-q2 (Ericson 5.1.9)."""
    d1 = _minus(q1, p1)
    d2 = _minus(q2, p2)
    r = _minus(p1, p2)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    f = _dot(d2, r)
    tiny = 1e-18
    if a <= tiny and e <= tiny:
        return p1, p2
    if a <= tiny:
        s = 0.0
        t = _clamp(f / e)
    else:
        c = _dot(d1, r)
        if e <= tiny:
            t = 0.0
            s = _clamp(-c / a)
        else:
            b = _dot(d1, d2)
            denominator = a * e - b * b
            s = _clamp((b * f - c * e) / denominator) \
                if denominator > tiny else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = _clamp(-c / a)
            elif t > 1.0:
                t = 1.0
                s = _clamp((b - c) / a)
    return _along(p1, d1, s), _along(p2, d2, t)


def _squared(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return dx * dx + dy * dy + dz * dz


def triangle_distance(first, second):
    """(squared distance, point on first, point on second) between two
    triangles, each three points. Zero where they cross."""
    a0, a1, a2 = first
    b0, b1, b2 = second
    edges_a = ((a0, a1), (a1, a2), (a2, a0))
    edges_b = ((b0, b1), (b1, b2), (b2, b0))
    for p, q in edges_a:
        hit = pierce(p, q, b0, b1, b2)
        if hit is not None:
            return 0.0, hit, hit
    for p, q in edges_b:
        hit = pierce(p, q, a0, a1, a2)
        if hit is not None:
            return 0.0, hit, hit
    best = None
    for p in first:
        q = closest_on_triangle(p, b0, b1, b2)
        d = _squared(p, q)
        if best is None or d < best[0]:
            best = (d, p, q)
    for q in second:
        p = closest_on_triangle(q, a0, a1, a2)
        d = _squared(p, q)
        if d < best[0]:
            best = (d, p, q)
    for p1, q1 in edges_a:
        for p2, q2 in edges_b:
            c1, c2 = closest_on_segments(p1, q1, p2, q2)
            d = _squared(c1, c2)
            if d < best[0]:
                best = (d, c1, c2)
    return best


def mesh_distance(pos_a, idx_a, box_a, pos_b, idx_b, box_b, limit,
                  max_tests=400000):
    """The gap between two triangle surfaces, when it is under `limit`.

    Returns None when the surfaces are `limit` or more apart, otherwise
    a dict: distance, the nearest point on each, and complete (False
    when it stopped at `max_tests` triangle pairs, in which case the
    distance is the smallest found so far, so may be too large)."""
    if not len(idx_a) or not len(idx_b) or limit <= 0:
        return None
    if not boxes_overlap(pad(box_a, limit), box_b, 0.0):
        return None
    tris_a = _triangles(pos_a, idx_a, pad(box_b, limit))
    tris_b = _triangles(pos_b, idx_b, pad(box_a, limit))
    best = limit * limit
    found = None
    tests = 0
    start = 0
    count_b = len(tris_b)
    complete = True
    for ta in tris_a:
        reach = best ** 0.5
        # ta's x0 only grows and reach only shrinks, so a triangle of b
        # that ends before ta starts, by more than reach, is done with.
        while start < count_b and tris_b[start][1] < ta[0] - reach:
            start += 1
        for n in range(start, count_b):
            tb = tris_b[n]
            if tb[0] > ta[1] + reach:
                break
            gap_x = max(0.0, tb[0] - ta[1], ta[0] - tb[1])
            gap_y = max(0.0, tb[2] - ta[3], ta[2] - tb[3])
            gap_z = max(0.0, tb[4] - ta[5], ta[4] - tb[5])
            if gap_x * gap_x + gap_y * gap_y + gap_z * gap_z >= best:
                continue
            tests += 1
            squared, p, q = triangle_distance(ta[6:9], tb[6:9])
            if squared < best:
                best = squared
                reach = best ** 0.5
                found = (p, q)
                if best == 0.0:
                    return {"distance": 0.0, "a": p, "b": q,
                            "complete": True}
            if tests >= max_tests:
                complete = False
                break
        if not complete:
            break
    if found is None:
        return None
    return {"distance": best ** 0.5, "a": found[0], "b": found[1],
            "complete": complete}


# --------------------------------------------------------------------------
# JSON for the page
#
# IronPython's json.dumps cannot be used here. Its encoder decides a
# string might be UTF-8 bytes whenever it holds a character above 0x7f,
# and since str and unicode are one type on IronPython, that is every
# non-ASCII name. It then tries to decode it, and the first "o" with an
# umlaut ends the message:
#
#   UnicodeDecodeError: 'unknown' codec can't decode byte 0xf6
#
# That is how 2.44.1 failed in Revit: the list of models never reached
# the page, because one category name had an umlaut in it. CPython's json
# does not do this, which is why the tests did not see it.
#
# So the messages are written here instead. Every character outside
# printable ASCII is escaped as \uXXXX, so the text is plain ASCII on
# both Pythons and whatever the names hold.
# --------------------------------------------------------------------------

import math
import re

_PLAIN = re.compile(u"^[\\x20\\x21\\x23-\\x5b\\x5d-\\x7e]*$")
_SPECIAL = re.compile(u"[^\\x20\\x21\\x23-\\x5b\\x5d-\\x7e]")
# Backspace and form feed go out as \u0008 and \u000c, which JSON reads
# the same; written short, the static IronPython check reads "\f" as
# the start of an f-string.
_SHORT = {u'"': u'\\"', u"\\": u"\\\\", u"\n": u"\\n", u"\r": u"\\r",
          u"\t": u"\\t"}

try:
    _TEXT = unicode             # noqa: F821  IronPython 2.7
    _BYTES = str
    _INTEGERS = (int, long)     # noqa: F821
except NameError:
    _TEXT = str
    _BYTES = bytes
    _INTEGERS = (int,)


def _escape(match):
    character = match.group(0)
    short = _SHORT.get(character)
    if short is not None:
        return short
    code = ord(character)
    if code > 0xFFFF:
        # Only on CPython: IronPython's strings are UTF-16 already, so
        # a character beyond the first plane arrives as its two halves.
        code -= 0x10000
        return u"\\u{0:04x}\\u{1:04x}".format(0xD800 + (code >> 10),
                                              0xDC00 + (code & 0x3FF))
    return u"\\u{0:04x}".format(code)


def _quote(text):
    if _PLAIN.match(text):
        return u'"' + text + u'"'         # base64 geometry: the common case
    return u'"' + _SPECIAL.sub(_escape, text) + u'"'


def _text(value):
    if isinstance(value, _TEXT):
        return value
    if isinstance(value, _BYTES):
        return value.decode("utf-8", "replace")
    return _TEXT(value)


def _number(value):
    if value != value or value in (float("inf"), float("-inf")):
        return u"null"                    # JSON has no NaN or infinity
    if value == math.floor(value) and abs(value) < 2 ** 53:
        return u"{0:d}".format(int(value)) if isinstance(value, _INTEGERS) \
            else u"{0!r}".format(float(value))
    return u"{0!r}".format(float(value))


def to_json(value):
    """`value` as JSON text that is pure ASCII. Takes dicts, lists,
    tuples, text, numbers, booleans and None; anything else is written
    as a number if it is one, and as its text otherwise."""
    parts = []
    _write(value, parts)
    return u"".join(parts)


def _write(value, parts):
    if value is None:
        parts.append(u"null")
    elif value is True:
        parts.append(u"true")
    elif value is False:
        parts.append(u"false")
    elif isinstance(value, (_TEXT, _BYTES)):
        parts.append(_quote(_text(value)))
    elif isinstance(value, _INTEGERS):
        parts.append(u"{0:d}".format(value))
    elif isinstance(value, float):
        parts.append(_number(value))
    elif isinstance(value, dict):
        parts.append(u"{")
        first = True
        for key, item in value.items():
            if not first:
                parts.append(u",")
            first = False
            parts.append(_quote(_text(key)))
            parts.append(u":")
            _write(item, parts)
        parts.append(u"}")
    elif isinstance(value, (list, tuple)):
        parts.append(u"[")
        for index, item in enumerate(value):
            if index:
                parts.append(u",")
            _write(item, parts)
        parts.append(u"]")
    else:
        # A .NET number (Int64 from ElementId.Value, a Double) is not a
        # Python int or float on IronPython, but converts to one.
        try:
            number = float(value)
        except BaseException:
            parts.append(_quote(_text(value)))
            return
        if number == math.floor(number) and abs(number) < 2 ** 53:
            parts.append(u"{0:d}".format(int(number)))
        else:
            parts.append(_number(number))


# --------------------------------------------------------------------------
# Packing meshes for the viewer
# --------------------------------------------------------------------------

class MeshBatch(object):
    """Collects meshes into messages of a bounded size.

    The viewer gets geometry as base64 float32 and uint32 buffers inside
    ordinary JSON messages, several meshes to a message. `emit` is
    called with each finished message.
    """

    def __init__(self, emit, limit=240000):
        self.emit = emit
        self.limit = limit
        self.triangles = 0
        self._reset()

    def _reset(self):
        self.items = []
        self.pos = new_floats()
        self.idx = new_indices()

    def add(self, kind, key, positions, indices):
        """`kind` is "e" for an element and "c" for a clash volume."""
        if not len(indices):
            return
        self.items.append({"kind": kind, "key": key,
                           "v": [len(self.pos), len(positions)],
                           "i": [len(self.idx), len(indices)]})
        self.pos.extend(positions)
        self.idx.extend(indices)
        self.triangles += len(indices) // 3
        if len(self.pos) >= self.limit:
            self.flush()

    def flush(self):
        if not self.items:
            return
        self.emit({"type": "mesh", "items": self.items,
                   "pos": b64_text(self.pos), "idx": b64_text(self.idx)})
        self._reset()
