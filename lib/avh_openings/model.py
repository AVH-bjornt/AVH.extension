# -*- coding: utf-8 -*-
"""How big an opening is, and where it goes. No Revit.

Everything here is in Revit's internal unit, decimal feet, except the
settings, which the user types in millimetres and which are converted
once, on the way in. Points and vectors are plain (x, y, z) tuples.

## The size of the hole

A pipe crossing a wall or slab square on needs a hole the size of the
pipe. A pipe crossing at an angle needs more, and getting that wrong
gives a hole the pipe does not fit through, which looks fine in plan
and is found on site.

The hole is worked out exactly for a straight slab of any thickness.
Where the pipe's axis meets each face, the pipe's cut is an ellipse
whose semi axes are r and r / cos(angle). The region the pipe occupies
inside the slab, seen along the slab's normal, is that ellipse swept
along the axis from one face to the other. So:

- the opening is centred on the midpoint of the two points where the
  axis meets the faces, projected onto the face;
- in any direction w lying in the face, it reaches out from those two
  points by r * sqrt(1 + ((d.w) / (d.n))^2), where d is the pipe's
  direction and n the slab's normal (`reach` below);
- a circle has to hold the whole swept ellipse, so its radius is half
  the projected axis plus the ellipse's long semi axis, r / |d.n|.

Then the clearance is added on every side and the size is rounded up,
never down, to the step the user chose.

## What is refused

A pipe running nearly along a wall rather than through it would need an
opening that is long, thin and almost certainly not what anyone wants.
Past `MAX_SKEW_DEGREES` off square the crossing is reported and left
alone rather than cut.
"""

import math

from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8

RECTANGULAR = u"rectangular"
CIRCULAR = u"circular"
SHAPES = (RECTANGULAR, CIRCULAR)

GAP = u"gap"             # millimetres of clearance on every side
PERCENT = u"percent"     # the opening is this much larger than the pipe
MODES = (GAP, PERCENT)

WALL = u"wall"
FLOOR = u"floor"
ROOF = u"roof"

# Past this angle off square a crossing is reported, not cut.
MAX_SKEW_DEGREES = 75.0

# Two axis segments closer than this are one crossing. A compound wall
# can come back from Revit as one solid per layer, and the pipe then
# crosses each in turn with no gap at all between them.
JOIN_TOLERANCE_FT = 1.0 / MM_PER_FOOT

DEFAULTS = {
    "shape": RECTANGULAR,
    "mode": GAP,
    "amount": 25.0,
    "round_to": 10.0,
    "links": True,
}

# Sensible limits on what is typed. A clearance of a metre is almost
# certainly a slip of the keyboard, and refusing it is kinder than
# cutting it.
MAX_GAP_MM = 500.0
MAX_PERCENT = 300.0
MAX_ROUND_MM = 200.0


class SettingsError(Exception):
    """A setting that cannot be used. The message says which and why."""


class Settings(object):
    def __init__(self, shape=RECTANGULAR, mode=GAP, amount=25.0,
                 round_to=10.0, links=True):
        self.shape = shape
        self.mode = mode
        self.amount = float(amount)
        self.round_to = float(round_to)
        self.links = bool(links)

    def as_dict(self):
        return {"shape": self.shape, "mode": self.mode,
                "amount": self.amount, "round_to": self.round_to,
                "links": self.links}

    def describe(self):
        shape = u"Circular" if self.shape == CIRCULAR else u"Rectangular"
        if self.mode == PERCENT:
            size = u"{0}% larger than the pipe".format(
                format_number(self.amount))
        else:
            size = u"{0} mm clear on every side".format(
                format_number(self.amount))
        rounding = u", rounded up to {0} mm".format(
            format_number(self.round_to)) if self.round_to > 0 else u""
        return u"{0}, {1}{2}".format(shape, size, rounding)


def format_number(value):
    """1.0 -> "1", 12.5 -> "12.5". For display only."""
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return u"{0}".format(int(round(value)))
    return u"{0:.1f}".format(value)


def parse_number(text, label):
    """A typed number, either decimal separator. Raises SettingsError."""
    raw = to_text(text).strip().replace(u" ", u"").replace(u"\u00a0", u"")
    if raw.endswith(u"%"):
        raw = raw[:-1]
    if raw.lower().endswith(u"mm"):
        raw = raw[:-2]
    if not raw:
        raise SettingsError(u"{0} is empty.".format(label))
    if raw.count(u",") == 1 and u"." not in raw:
        raw = raw.replace(u",", u".")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SettingsError(u"{0} is not a number: {1}".format(
            label, to_text(text)))
    if value != value or value in (float("inf"), float("-inf")):
        raise SettingsError(u"{0} is not a number: {1}".format(
            label, to_text(text)))
    return value


def parse_settings(shape, mode, amount_text, round_text, links):
    """Settings from what the window holds. Raises SettingsError."""
    if shape not in SHAPES:
        raise SettingsError(u"Choose rectangular or circular.")
    if mode not in MODES:
        raise SettingsError(u"Choose how the size is given.")
    label = u"The percentage" if mode == PERCENT else u"The clearance"
    amount = parse_number(amount_text, label)
    if amount < 0:
        raise SettingsError(u"{0} cannot be negative.".format(label))
    limit = MAX_PERCENT if mode == PERCENT else MAX_GAP_MM
    if amount > limit:
        raise SettingsError(u"{0} of {1} is more than the {2} allowed. "
                            u"Check the number.".format(
                                label, format_number(amount),
                                format_number(limit)))
    if to_text(round_text).strip() == u"":
        round_to = 0.0
    else:
        round_to = parse_number(round_text, u"The rounding step")
    if round_to < 0 or round_to > MAX_ROUND_MM:
        raise SettingsError(u"The rounding step has to be between 0 and "
                            u"{0} mm.".format(format_number(MAX_ROUND_MM)))
    return Settings(shape, mode, amount, round_to, links)


def settings_from_dict(stored):
    """Remembered settings, with anything missing or broken replaced by
    the default. Never raises: a bad remembered value must not stop the
    window opening."""
    values = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key in values:
            if key in stored and stored[key] is not None:
                values[key] = stored[key]
    try:
        return parse_settings(values["shape"], values["mode"],
                              to_text(values["amount"]),
                              to_text(values["round_to"]),
                              bool(values["links"]))
    except (SettingsError, TypeError, ValueError):
        return Settings(**DEFAULTS)


# --------------------------------------------------------------------------
# Vectors
# --------------------------------------------------------------------------

def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, factor):
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(dot(a, a))


def unit(a):
    size = length(a)
    if size < 1e-12:
        return None
    return scale(a, 1.0 / size)


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------

class Frame(object):
    """Two directions in the host's face and its normal, orthonormal.

    A point's (u, v, w) are its components along them, so to_world puts
    one back exactly.
    """

    def __init__(self, u, v, n):
        self.u = u
        self.v = v
        self.n = n

    def local(self, point):
        return (dot(point, self.u), dot(point, self.v), dot(point, self.n))

    def to_world(self, u, v, w):
        return add(add(scale(self.u, u), scale(self.v, v)), scale(self.n, w))


def wall_frame(direction):
    """A straight wall's frame from its location line's direction.

    u runs along the wall, level; v is up; n is through the wall. n is
    built here rather than read from Wall.Orientation, so the three are
    square to each other whatever Revit reports for a flipped wall.
    Returns None for a direction with nothing horizontal in it.
    """
    flat = unit((direction[0], direction[1], 0.0))
    if flat is None:
        return None
    up = (0.0, 0.0, 1.0)
    return Frame(flat, up, cross(flat, up))


def slab_frame():
    """Floors and roofs are cut vertically, so their frame is plan."""
    return Frame((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# --------------------------------------------------------------------------
# Segments and boxes
# --------------------------------------------------------------------------

def join_segments(segments, tolerance=JOIN_TOLERANCE_FT):
    """Axis segments inside a host, joined where they touch.

    `segments` is [(start, end)] in any order and either way round.
    Returns them ordered along the first one's direction, with every run
    of touching segments made one. Separate runs stay separate: a pipe
    that crosses a wall twice needs two holes.
    """
    cleaned = [(a, b) for a, b in segments if length(sub(b, a)) > 1e-9]
    if not cleaned:
        return []
    direction = unit(sub(cleaned[0][1], cleaned[0][0]))
    ordered = []
    for a, b in cleaned:
        if dot(sub(b, a), direction) < 0:
            a, b = b, a
        ordered.append((a, b))
    ordered.sort(key=lambda pair: dot(pair[0], direction))
    joined = [ordered[0]]
    for a, b in ordered[1:]:
        last_a, last_b = joined[-1]
        if dot(sub(a, last_b), direction) <= tolerance:
            if dot(b, direction) > dot(last_b, direction):
                joined[-1] = (last_a, b)
        else:
            joined.append((a, b))
    return joined


def box_corners(low, high):
    return [(x, y, z) for x in (low[0], high[0])
            for y in (low[1], high[1]) for z in (low[2], high[2])]


def box_of(points):
    """(low, high) of some points, or None for none."""
    points = list(points)
    if not points:
        return None
    return (tuple(min(p[i] for p in points) for i in range(3)),
            tuple(max(p[i] for p in points) for i in range(3)))


def boxes_touch(first, second, pad=0.0):
    """Whether two (low, high) boxes overlap, with `pad` of slack."""
    if first is None or second is None:
        return False
    for i in range(3):
        if first[0][i] > second[1][i] + pad:
            return False
        if second[0][i] > first[1][i] + pad:
            return False
    return True


# --------------------------------------------------------------------------
# Sizes
# --------------------------------------------------------------------------

def gap_feet(settings, pipe_diameter):
    """Clearance on each side, in feet, for a pipe of this diameter."""
    if settings.mode == PERCENT:
        return pipe_diameter * settings.amount / 200.0
    return settings.amount / MM_PER_FOOT


def round_up(size_feet, step_mm):
    """Up to the next whole step, never down. A size already on a step
    stays where it is: floating point error must not add a whole step to
    an opening that was exactly right."""
    if step_mm <= 0:
        return size_feet
    mm = size_feet * MM_PER_FOOT
    steps = math.ceil(mm / step_mm - 1e-6)
    return steps * step_mm / MM_PER_FOOT


def skew_degrees(direction, normal):
    """How far the pipe is off square to the face, in degrees."""
    cosine = min(1.0, abs(dot(unit(direction), unit(normal))))
    return math.degrees(math.acos(cosine))


def reach(radius, direction, normal, along):
    """How far the pipe's cut at a face reaches along `along`, which lies
    in the face. r for a pipe square to the face, more when it leans."""
    d_n = dot(direction, normal)
    d_w = dot(direction, along)
    return radius * math.sqrt(1.0 + (d_w / d_n) ** 2)


class Opening(object):
    """One opening, ready to be made.

    `center` is a world point on the middle plane of the host. A
    rectangle's `width` runs along frame.u, `height` along frame.v; a
    circle has `diameter`. `pipe_diameter` is what it was sized for,
    insulation included.
    """

    def __init__(self, shape, frame, center, width=0.0, height=0.0,
                 diameter=0.0, pipe_diameter=0.0, skew=0.0):
        self.shape = shape
        self.frame = frame
        self.center = center
        self.width = width
        self.height = height
        self.diameter = diameter
        self.pipe_diameter = pipe_diameter
        self.skew = skew

    def corners(self):
        """The rectangle's four corners, world points, in order round."""
        u, v, w = self.frame.local(self.center)
        half_w = self.width / 2.0
        half_h = self.height / 2.0
        return [self.frame.to_world(u - half_w, v - half_h, w),
                self.frame.to_world(u + half_w, v - half_h, w),
                self.frame.to_world(u + half_w, v + half_h, w),
                self.frame.to_world(u - half_w, v + half_h, w)]

    def size_text(self):
        if self.shape == CIRCULAR:
            return u"\u00f8{0}".format(format_number(
                self.diameter * MM_PER_FOOT))
        return u"{0} \u00d7 {1}".format(
            format_number(self.width * MM_PER_FOOT),
            format_number(self.height * MM_PER_FOOT))


def size_opening(entry, exit, pipe_diameter, frame, settings):
    """The opening for a pipe whose axis meets the host's faces at
    `entry` and `exit`. Returns (Opening, "") or (None, reason)."""
    axis = sub(exit, entry)
    direction = unit(axis)
    if direction is None:
        return None, u"the pipe only touches the host"
    if pipe_diameter <= 0:
        return None, u"the pipe has no diameter"
    skew = skew_degrees(direction, frame.n)
    if skew > MAX_SKEW_DEGREES:
        return None, (u"it runs {0} degrees off square, more along the "
                      u"host than through it".format(int(round(skew))))

    radius = pipe_diameter / 2.0
    gap = gap_feet(settings, pipe_diameter)
    a = frame.local(entry)
    b = frame.local(exit)
    center = frame.to_world((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0,
                            (a[2] + b[2]) / 2.0)

    if settings.shape == CIRCULAR:
        span = math.hypot(b[0] - a[0], b[1] - a[1])
        long_semi = radius / abs(dot(direction, frame.n))
        diameter = span + 2.0 * long_semi + 2.0 * gap
        return Opening(CIRCULAR, frame, center,
                       diameter=round_up(diameter, settings.round_to),
                       pipe_diameter=pipe_diameter, skew=skew), u""

    span_u = abs(b[0] - a[0])
    span_v = abs(b[1] - a[1])
    width = span_u + 2.0 * reach(radius, direction, frame.n, frame.u) \
        + 2.0 * gap
    height = span_v + 2.0 * reach(radius, direction, frame.n, frame.v) \
        + 2.0 * gap
    return Opening(RECTANGULAR, frame, center,
                   width=round_up(width, settings.round_to),
                   height=round_up(height, settings.round_to),
                   pipe_diameter=pipe_diameter, skew=skew), u""
