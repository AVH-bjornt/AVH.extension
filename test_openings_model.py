# -*- coding: utf-8 -*-
"""
The arithmetic behind Pipe Openings, outside Revit.

The check that matters is that the pipe fits. A hole sized by a formula
that is subtly wrong for a pipe at an angle looks right in plan and is
found on site. So the sizes are not only compared with the formula that
made them: points are sampled over the surface of the actual pipe,
inside the actual slab, and every one must land inside the opening.

Run outside Revit:

    python test_openings_model.py
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_openings import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


MM = 1.0 / model.MM_PER_FOOT


def settings(**kwargs):
    values = dict(shape=model.RECTANGULAR, mode=model.GAP, amount=0.0,
                  round_to=0.0, links=True)
    values.update(kwargs)
    return model.Settings(**values)


# --------------------------------------------------------------------------
# Rounding
# --------------------------------------------------------------------------

check("round up: 151 mm to 160", close(model.round_up(151 * MM, 10),
                                      160 * MM))
check("round up: 160 mm stays 160, not 170",
      close(model.round_up(160 * MM, 10), 160 * MM))
check("round up: 160 mm through feet and back stays 160",
      close(model.round_up((160.0 / 304.8) * 1.0, 10), 160 * MM))
check("round up: a step of 0 leaves it alone",
      close(model.round_up(151.3 * MM, 0), 151.3 * MM))
check("round up: never down", model.round_up(150.001 * MM, 10) > 150 * MM)

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

parsed = model.parse_settings(model.CIRCULAR, model.GAP, u"25", u"10", True)
check("settings: plain", parsed.shape == model.CIRCULAR and
      parsed.amount == 25.0 and parsed.round_to == 10.0)
parsed = model.parse_settings(model.RECTANGULAR, model.GAP, u"12,5", u"5",
                              False)
check("settings: decimal comma", parsed.amount == 12.5 and not parsed.links)
parsed = model.parse_settings(model.RECTANGULAR, model.PERCENT, u"20 %",
                              u"", True)
check("settings: percent sign and blank rounding",
      parsed.amount == 20.0 and parsed.round_to == 0.0)
parsed = model.parse_settings(model.RECTANGULAR, model.GAP, u"30mm", u"10",
                              True)
check("settings: a trailing mm", parsed.amount == 30.0)


def refuses(*args):
    try:
        model.parse_settings(*args)
    except model.SettingsError as exc:
        return u"{0}".format(exc)
    return None


check("settings: empty refused",
      refuses(model.RECTANGULAR, model.GAP, u"", u"10", True))
check("settings: text refused",
      refuses(model.RECTANGULAR, model.GAP, u"abc", u"10", True))
check("settings: negative refused",
      refuses(model.RECTANGULAR, model.GAP, u"-5", u"10", True))
check("settings: a metre of clearance refused",
      refuses(model.RECTANGULAR, model.GAP, u"1000", u"10", True))
check("settings: 500% refused",
      refuses(model.RECTANGULAR, model.PERCENT, u"500", u"10", True))
check("settings: unknown shape refused",
      refuses(u"oval", model.GAP, u"25", u"10", True))
check("settings: nan refused",
      refuses(model.RECTANGULAR, model.GAP, u"nan", u"10", True))
message = refuses(model.RECTANGULAR, model.PERCENT, u"x", u"10", True)
check("settings: the message names the field",
      message and u"percentage" in message, message)

restored = model.settings_from_dict({"shape": model.CIRCULAR,
                                     "amount": u"40", "links": False})
check("remembered: kept what was there", restored.shape == model.CIRCULAR
      and restored.amount == 40.0 and not restored.links)
restored = model.settings_from_dict({"amount": u"garbage"})
check("remembered: a broken value falls back to the defaults",
      restored.amount == model.DEFAULTS["amount"])
restored = model.settings_from_dict(None)
check("remembered: nothing stored gives the defaults",
      restored.shape == model.DEFAULTS["shape"])

check("describe: gap", u"25 mm clear" in settings(amount=25).describe())
check("describe: percent",
      u"20% larger" in settings(mode=model.PERCENT, amount=20).describe())

# --------------------------------------------------------------------------
# Gap
# --------------------------------------------------------------------------

check("gap: millimetres", close(model.gap_feet(settings(amount=25),
                                               110 * MM), 25 * MM))
check("gap: 20% of a 100 mm pipe is 10 mm a side",
      close(model.gap_feet(settings(mode=model.PERCENT, amount=20),
                           100 * MM), 10 * MM))

# --------------------------------------------------------------------------
# Square crossings, the everyday case
# --------------------------------------------------------------------------

floor = model.slab_frame()
entry = (10.0, 5.0, 0.0)
exit = (10.0, 5.0, 250 * MM)
opening, reason = model.size_opening(entry, exit, 110 * MM, floor,
                                     settings(amount=25, round_to=10))
check("floor, square: 110 mm pipe, 25 mm gap -> 160 x 160",
      opening and close(opening.width, 160 * MM) and
      close(opening.height, 160 * MM),
      opening and opening.size_text())
check("floor, square: centred on the pipe",
      opening and close(opening.center[0], 10.0) and
      close(opening.center[1], 5.0))
check("floor, square: centre on the slab's middle plane",
      opening and close(opening.center[2], 125 * MM))
check("floor, square: no skew", opening and close(opening.skew, 0.0))

opening, reason = model.size_opening(entry, exit, 110 * MM, floor,
                                     settings(shape=model.CIRCULAR,
                                              amount=25, round_to=10))
check("floor, circular: 160", opening and
      close(opening.diameter, 160 * MM), opening and opening.size_text())

opening, reason = model.size_opening(
    entry, exit, 100 * MM, floor,
    settings(mode=model.PERCENT, amount=20, round_to=0))
check("floor, percent: 100 mm pipe 20% larger -> 120",
      opening and close(opening.width, 120 * MM))

# A wall running along X, the pipe through it along Y.
wall = model.wall_frame((1.0, 0.0, 0.0))
check("wall frame: orthonormal",
      close(model.dot(wall.u, wall.v), 0) and
      close(model.dot(wall.u, wall.n), 0) and
      close(model.dot(wall.v, wall.n), 0) and
      close(model.length(wall.n), 1))
check("wall frame: n is through the wall", close(abs(wall.n[1]), 1.0))
opening, reason = model.size_opening((3.0, 0.0, 4.0), (3.0, 1.0, 4.0),
                                     110 * MM, wall,
                                     settings(amount=25, round_to=10))
check("wall, square: 160 x 160", opening and
      close(opening.width, 160 * MM) and close(opening.height, 160 * MM))
corners = opening.corners()
check("wall, square: corners lie in the wall's middle plane",
      all(close(c[1], 0.5) for c in corners), corners)
check("wall, square: corners span the width along the wall",
      close(max(c[0] for c in corners) - min(c[0] for c in corners),
            160 * MM))
check("wall, square: corners span the height upward",
      close(max(c[2] for c in corners) - min(c[2] for c in corners),
            160 * MM))
check("wall frame: a vertical direction has no frame",
      model.wall_frame((0.0, 0.0, 1.0)) is None)

# --------------------------------------------------------------------------
# The pipe must fit: sample it
# --------------------------------------------------------------------------


def perpendiculars(direction):
    helper = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
    first = model.unit(model.cross(direction, helper))
    second = model.cross(direction, first)
    return first, second


def pipe_points_in_slab(entry, exit, radius, frame, face=None):
    """Points on the pipe's surface lying inside the slab, which is the
    region between the planes through entry and exit square to `face`,
    the faces' normal (frame.n when not given). A sloped roof cut
    vertically has faces that are not square to frame.n."""
    m = model.unit(face) if face is not None else frame.n
    direction = model.unit(model.sub(exit, entry))
    first, second = perpendiculars(direction)
    low = model.dot(entry, m)
    high = model.dot(exit, m)
    low, high = min(low, high), max(low, high)
    # Only the stretch of axis whose surface can be inside the slab is
    # sampled, finely: a pipe at a steep angle to a sloped face reaches
    # far past where its axis does, and a fixed range spread its samples
    # too thin to find the tip of the cut.
    along = model.dot(direction, m)
    start = model.dot(entry, m)
    ends = [(low - start - radius) / along, (high - start + radius) / along]
    first_t, last_t = min(ends), max(ends)
    found = []
    for i in range(401):
        t = first_t + (last_t - first_t) * i / 400.0
        base = model.add(entry, model.scale(direction, t))
        for k in range(180):
            angle = 2.0 * math.pi * k / 180.0
            offset = model.add(model.scale(first, radius * math.cos(angle)),
                               model.scale(second, radius * math.sin(angle)))
            p = model.add(base, offset)
            depth = model.dot(p, m)
            if low - 1e-9 <= depth <= high + 1e-9:
                found.append(p)
    return found


def fits(opening, points, frame):
    cu, cv, _ = frame.local(opening.center)
    worst = 0.0
    for p in points:
        u, v, _ = frame.local(p)
        if opening.shape == model.CIRCULAR:
            over = math.hypot(u - cu, v - cv) - opening.diameter / 2.0
        else:
            over = max(abs(u - cu) - opening.width / 2.0,
                       abs(v - cv) - opening.height / 2.0)
        worst = max(worst, over)
    return worst <= 1e-6, worst


def tight(opening, points, frame):
    """The opening is no more than 2 mm larger than the pipe needs, so a
    formula that is safe only because it is huge is caught too."""
    cu, cv, _ = frame.local(opening.center)
    if opening.shape == model.CIRCULAR:
        need = max(math.hypot(frame.local(p)[0] - cu,
                              frame.local(p)[1] - cv) for p in points)
        return opening.diameter / 2.0 - need < 2 * MM
    need_u = max(abs(frame.local(p)[0] - cu) for p in points)
    need_v = max(abs(frame.local(p)[1] - cv) for p in points)
    return (opening.width / 2.0 - need_u < 2 * MM and
            opening.height / 2.0 - need_v < 2 * MM)


def roof_case(slope, tilt, heading, thickness=300 * MM):
    """A pipe through a roof sloping `slope` degrees down towards -x,
    leaning `tilt` degrees from vertical towards `heading` degrees in
    plan (0 is +x, up the slope). Returns (entry, exit, face normal)."""
    s = math.tan(math.radians(slope))
    m = model.unit((-s, 0.0, 1.0))
    t = math.radians(tilt)
    h = math.radians(heading)
    d = (math.sin(t) * math.cos(h), math.sin(t) * math.sin(h), math.cos(t))
    # the faces are m.p = 0 and m.p = thickness
    k = thickness / model.dot(d, m)
    return (0.0, 0.0, 0.0), model.scale(d, k), m


cases = [
    (u"wall, 30 degrees in plan", wall, (3.0, 0.0, 4.0),
     (3.0 + math.tan(math.radians(30)) * 1.0, 1.0, 4.0), None),
    (u"wall, 45 degrees falling", wall, (3.0, 0.0, 4.0),
     (3.0, 1.0, 3.0), None),
    (u"wall, compound 20 and 35", wall, (3.0, 0.0, 4.0),
     (3.0 + math.tan(math.radians(20)), 1.0,
      4.0 - math.tan(math.radians(35))), None),
    (u"floor, 40 degrees off vertical", floor, (0.0, 0.0, 0.0),
     (math.tan(math.radians(40)) * 0.8, 0.0, 0.8), None),
    (u"floor, 60 degrees diagonal", floor, (0.0, 0.0, 0.0),
     (math.tan(math.radians(60)) * 0.5 / math.sqrt(2),
      math.tan(math.radians(60)) * 0.5 / math.sqrt(2), 0.5), None),
]
# Sloped roofs, cut vertically. The two leaning with the slope were 46
# and 126 mm short a side at 2.47.0, which sized every host as if its
# faces were square to the cut.
for slope, tilt, heading in ((30, 0, 0), (30, 30, 0), (30, 30, 180),
                             (45, 30, 0), (45, 30, 180), (30, 25, 90),
                             (35, 20, 45)):
    entry, exit, m = roof_case(slope, tilt, heading)
    cases.append((u"roof {0}, pipe {1} towards {2}".format(
        slope, tilt, heading), floor, entry, exit, m))

for label, frame, entry, exit, face in cases:
    for shape in model.SHAPES:
        opening, reason = model.size_opening(
            entry, exit, 160 * MM, frame,
            settings(shape=shape, amount=0, round_to=0), face, face)
        points = pipe_points_in_slab(entry, exit, 80 * MM, frame, face)
        ok, worst = fits(opening, points, frame) if opening else (False, 0)
        check(u"fits: {0}, {1}".format(label, shape), ok,
              u"{0:.3f} mm outside".format(worst * model.MM_PER_FOOT))
        if shape == model.RECTANGULAR and opening:
            check(u"tight: {0}, rectangle".format(label),
                  tight(opening, points, frame), opening.size_text())

# The skew a 30 degree pipe reports
opening, _ = model.size_opening(cases[0][2], cases[0][3], 160 * MM, wall,
                                settings())
check("skew: 30 degrees reported", opening and
      abs(opening.skew - 30.0) < 1e-6, opening and opening.skew)

# --------------------------------------------------------------------------
# Refused
# --------------------------------------------------------------------------

opening, reason = model.size_opening(
    (0.0, 0.0, 4.0), (math.tan(math.radians(80)), 1.0, 4.0), 110 * MM, wall,
    settings())
check("refused: 80 degrees off square", opening is None and
      u"80 degrees" in reason, reason)
opening, reason = model.size_opening((0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                                     110 * MM, wall, settings())
check("refused: no length", opening is None and reason)
opening, reason = model.size_opening((0.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                                     0.0, wall, settings())
check("refused: no diameter", opening is None and reason)

# --------------------------------------------------------------------------
# Joining segments
# --------------------------------------------------------------------------

layers = [((0.0, 0.3, 0.0), (0.0, 0.6, 0.0)),
          ((0.0, 0.0, 0.0), (0.0, 0.3, 0.0)),
          ((0.0, 0.9, 0.0), (0.0, 0.6, 0.0))]
joined = model.join_segments(layers)
check("join: three touching layers in any order are one crossing",
      len(joined) == 1 and close(joined[0][0][1], 0.0) and
      close(joined[0][1][1], 0.9), joined)
joined = model.join_segments([((0.0, 0.0, 0.0), (0.0, 0.3, 0.0)),
                              ((0.0, 5.0, 0.0), (0.0, 5.3, 0.0))])
check("join: two separate crossings stay two", len(joined) == 2)
check("join: nothing in, nothing out", model.join_segments([]) == [])
check("boxes: touching", model.boxes_touch(((0, 0, 0), (1, 1, 1)),
                                           ((1, 1, 1), (2, 2, 2))))
check("boxes: apart", not model.boxes_touch(((0, 0, 0), (1, 1, 1)),
                                            ((1.1, 0, 0), (2, 1, 1))))

# --------------------------------------------------------------------------
# Shafts: the band a shaft must span
# --------------------------------------------------------------------------


def faces_under(opening, entry, exit, m, frame):
    """Heights of the two faces (planes through entry and exit, normal
    m) at points across the opening's footprint in plan."""
    cu, cv, _ = frame.local(opening.center)
    if opening.shape == model.CIRCULAR:
        half_u = half_v = opening.diameter / 2.0
    else:
        half_u, half_v = opening.width / 2.0, opening.height / 2.0
    heights = []
    for i in range(-20, 21):
        for j in range(-20, 21):
            if opening.shape == model.CIRCULAR and \
                    math.hypot(i, j) > 20.0 + 1e-9:
                continue            # outside the circle
            x = cu + half_u * i / 20.0
            y = cv + half_v * j / 20.0
            for point in (entry, exit):
                # m . (p - point) = 0 for p = (x, y, z)
                z = point[2] - (m[0] * (x - point[0]) +
                                m[1] * (y - point[1])) / m[2]
                heights.append(z)
    return heights


for slope, tilt, heading in ((0, 0, 0), (30, 0, 0), (37, 0, 0),
                             (30, 30, 0), (45, 20, 90)):
    entry, exit, m = roof_case(slope, tilt, heading)
    for shape in model.SHAPES:
        opening, _ = model.size_opening(entry, exit, 152.4 * MM, floor,
                                        settings(shape=shape, amount=25,
                                                 round_to=10), m, m)
        low, high = model.shaft_band(entry, exit, opening, [m, m])
        heights = faces_under(opening, entry, exit, m, floor)
        label = u"shaft band: roof {0}, pipe {1} towards {2}, {3}".format(
            slope, tilt, heading, shape)
        check(label + u", holds both faces under the opening",
              low <= min(heights) + 1e-9 and high >= max(heights) - 1e-9,
              (low / MM, min(heights) / MM, high / MM, max(heights) / MM))
        check(label + u", no more than the margin beyond them",
              min(heights) - low <= model.SHAFT_MARGIN_FT + 1 * MM and
              high - max(heights) <= model.SHAFT_MARGIN_FT + 1 * MM,
              ((min(heights) - low) / MM, (high - max(heights)) / MM))

square = model.Opening(model.RECTANGULAR, floor, (0.0, 0.0, 0.0),
                       width=200 * MM, height=100 * MM)
check("shaft band: a flat or unknown face does not rise",
      model.face_spread(square, None) == 0.0 and
      model.face_spread(square, (0.0, 0.0, 1.0)) == 0.0)
check("shaft band: a 45 degree face rises one for one across the width",
      close(model.face_spread(square, (-1.0, 0.0, 1.0)), 100 * MM))

levels = [0.0, 10.0, 20.0]
check("shaft levels: between two levels",
      model.shaft_levels(levels, 12.0, 13.0) == (1, 2))
check("shaft levels: on a level is that level",
      model.shaft_levels(levels, 10.0, 13.0) == (1, 2))
check("shaft levels: above every level uses the top one twice",
      model.shaft_levels(levels, 22.0, 23.0) == (2, 2))
check("shaft levels: below every level uses the bottom one twice",
      model.shaft_levels(levels, -3.0, -2.0) == (0, 0))
check("shaft levels: no levels", model.shaft_levels([], 0.0, 1.0) is None)

# --------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------

from avh_openings import tags  # noqa: E402

made = settings(shape=model.RECTANGULAR, amount=25, round_to=10)
opening, _ = model.size_opening((10.0, 5.0, 0.0), (10.0, 5.0, 250 * MM),
                                110 * MM, floor, made)
tag = tags.from_opening(opening, made, u"pipe-uid-1", u"link-uid-2",
                        u"Pipe", u"host-uid-3", tags.OPENING)
text = tag.encode()
back = tags.decode(text)
check("tag: encodes to plain ASCII", all(ord(c) < 127 for c in text), text)
check("tag: reads back field for field",
      back.__dict__ == tag.__dict__, (back.__dict__, tag.__dict__))
check("tag: numbers survive exactly (repr, not a locale)",
      back.cx == tag.cx and back.width == tag.width and
      back.pipe_diameter == tag.pipe_diameter)
check("tag: the settings come back", back.settings().shape ==
      model.RECTANGULAR and back.settings().amount == 25.0)
check("tag: the same opening is the same",
      tags.same(back, opening))


def moved(opening, du=0.0, dv=0.0, dw=0.0, grow=0.0):
    u, v, w = opening.frame.local(opening.center)
    return model.Opening(opening.shape, opening.frame,
                         opening.frame.to_world(u + du, v + dv, w + dw),
                         width=opening.width + grow,
                         height=opening.height, diameter=opening.diameter)


check("tag: 0.4 mm off is the same", tags.same(back, moved(opening, 0.4 * MM)))
check("tag: 0.6 mm off is not", not tags.same(back, moved(opening, 0.6 * MM)))
check("tag: 0.6 mm off the other way is not",
      not tags.same(back, moved(opening, dv=-0.6 * MM)))
check("tag: a different depth is still the same opening",
      tags.same(back, moved(opening, dw=100 * MM)))
check("tag: 1 mm wider is not the same",
      not tags.same(back, moved(opening, grow=1 * MM)))
circle = model.Opening(model.CIRCULAR, floor, opening.center,
                       diameter=160 * MM)
check("tag: a circle where a rectangle was is not the same",
      not tags.same(back, circle))


def refused(text):
    try:
        tags.decode(text)
    except tags.TagError as exc:
        return u"{0}".format(exc)
    return None


check("tag: another version is refused, and says so",
      u"version 2" in (refused(text.replace(u"version=1", u"version=2"))
                       or u""))
check("tag: a missing field is refused",
      refused(u"\n".join(line for line in text.split(u"\n")
                         if not line.startswith(u"width="))))
check("tag: a broken number is refused",
      refused(text.replace(u"cx=", u"cx=abc")))
check("tag: an unknown kind is refused",
      refused(text.replace(u"kind=opening", u"kind=hole")))
check("tag: nothing at all is refused", refused(u""))
odd = tags.from_opening(opening, made, u"pipe\u00e6", u"", u"Pipe",
                        u"host", tags.OPENING)
try:
    odd.encode()
    stored = True
except tags.TagError:
    stored = False
check("tag: will not store something that cannot read back", not stored)

# --------------------------------------------------------------------------
# The checks can fail
# --------------------------------------------------------------------------

bad = model.Opening(model.RECTANGULAR, floor, (0.0, 0.0, 0.0),
                    width=100 * MM, height=100 * MM)
points = pipe_points_in_slab((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 80 * MM,
                             floor)
check("self-check: a too small opening does not fit",
      not fits(bad, points, floor)[0])
big = model.Opening(model.RECTANGULAR, floor, (0.0, 0.0, 0.0),
                    width=300 * MM, height=300 * MM)
check("self-check: an oversized opening is not tight",
      not tight(big, points, floor))
check("self-check: check stores a real boolean",
      all(isinstance(passed, bool) for _, passed, _ in results))

failed = [r for r in results if not r[1]]
for name, passed, detail in results:
    print(u"{0}  {1}{2}".format("PASS" if passed else "FAIL", name,
                                u"  ({0})".format(detail)
                                if not passed and detail != "" else u""))
print(u"\n{0} checks, {1} failed".format(len(results), len(failed)))
sys.exit(1 if failed else 0)
