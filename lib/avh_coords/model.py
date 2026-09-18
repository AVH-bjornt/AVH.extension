# -*- coding: utf-8 -*-
"""Reading a typed coordinate, and deciding where it lands.

No Revit here. Everything this tool gets wrong, it gets wrong in
arithmetic or in parsing, so all of it is testable outside Revit, which
matters more than usual: a marker placed in the wrong spot looks exactly
like a marker placed in the right spot.
"""

from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8
METRES_PER_FOOT = 0.3048

# The typed value and the value read back off the placed instance have to
# agree. One millimetre is tighter than any survey AVH receives and loose
# enough that a double precision round trip through feet never trips it.
TOLERANCE_MM = 1.0


class ParseError(Exception):
    """A number that cannot be read. Carries the text that failed."""


def parse_number(text, label=u""):
    """A typed number as a float, in whatever the user typed it in.

    AVH's Revit runs in a decimal comma locale and surveys arrive in
    every shape, so this takes both separators and both groupings:

        412345.678   412345,678   412 345,678   412.345,678   412,345.678

    **The rule when both separators appear is that the last one is the
    decimal.** That is what makes "412.345,678" and "412,345.678" both
    mean the same number. When only one appears it is the decimal, so
    "412.345" is four hundred and twelve point three four five, not four
    hundred and twelve thousand.

    That last case is genuinely ambiguous and no rule settles it. It is
    why the tool shows the parsed value back before it places anything:
    the user sees "412.345" and knows at once whether that is the number
    they meant. A range check would be guessing at a coordinate system.

    Raises ParseError, whose message names the field.
    """
    raw = to_text(text).strip()
    # Non breaking and thin spaces turn up when a number is pasted out of
    # a spreadsheet or a PDF, and they are invisible in the dialog.
    for space in (u" ", u" ", u" ", u" ", u"\t"):
        raw = raw.replace(space, u"")

    if not raw:
        raise ParseError(u"{0} is empty.".format(label or u"The value"))

    last_dot = raw.rfind(u".")
    last_comma = raw.rfind(u",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_at = max(last_dot, last_comma)
        other = u"," if decimal_at == last_dot else u"."
        cleaned = raw[:decimal_at].replace(other, u"") + u"." + raw[decimal_at + 1:]
    elif last_comma >= 0:
        cleaned = raw.replace(u",", u".")
    else:
        cleaned = raw

    try:
        return float(cleaned)
    except (TypeError, ValueError):
        raise ParseError(u"{0} is not a number: {1}".format(
            label or u"The value", raw))


def metres_to_feet(metres):
    return float(metres) / METRES_PER_FOOT


def feet_to_metres(feet):
    return float(feet) * METRES_PER_FOOT


def format_metres(value, places=3):
    """A number for the report, always with a period decimal.

    The report is read, not parsed, and mixing separators in one table is
    how somebody misreads a coordinate.
    """
    return u"{0:.{1}f}".format(float(value), places)


def choose_level(elevation_feet, levels):
    """Which level to host on, and the offset from it.

    `levels` is [(key, elevation_feet)] in any order. Returns
    (key, offset_feet, below_all) or None when there are no levels.

    The rule is the nearest level at or below the point, which is what
    somebody reading the model expects: a thing at +4.250 belongs to the
    storey it stands on, not the one above it. A point below every level
    hosts on the lowest with a negative offset, and says so, rather than
    refusing: refusing would make the tool useless for anything in the
    ground, which on a fish farm is most of it.
    """
    usable = [(key, float(value)) for key, value in levels if value is not None]
    if not usable:
        return None

    at_or_below = [item for item in usable if item[1] <= elevation_feet]
    if at_or_below:
        key, level_elevation = max(at_or_below, key=lambda item: item[1])
        return key, elevation_feet - level_elevation, False

    key, level_elevation = min(usable, key=lambda item: item[1])
    return key, elevation_feet - level_elevation, True


def agree(typed_xyz, actual_xyz, tolerance_mm=TOLERANCE_MM):
    """Do the typed point and the placed point match?

    Both are (easting, northing, elevation) in metres. Returns
    (ok, deltas_mm) with one delta per axis, so a report can name the
    axis that is wrong rather than only that something is.

    This exists because the direction of the shared coordinate transform
    is the single easiest thing to get backwards, and backwards is not
    obvious: the marker appears, in a plausible place, somewhere else.
    """
    deltas = []
    for index in range(3):
        deltas.append((float(actual_xyz[index]) - float(typed_xyz[index]))
                      * 1000.0)
    ok = all(abs(delta) <= tolerance_mm for delta in deltas)
    return ok, deltas


def describe_deltas(deltas):
    """The mismatch, in words, worst axis first."""
    names = (u"easting", u"northing", u"elevation")
    pairs = sorted(zip(names, deltas), key=lambda pair: -abs(pair[1]))
    return u", ".join(u"{0} off by {1} mm".format(name, format_metres(delta, 1))
                      for name, delta in pairs if abs(delta) > 0.05)


# --- the shared coordinate transform ---------------------------------------
#
# Composing this from a rotation angle means guessing a sign convention,
# and a sign guessed wrong puts the marker somewhere plausible and wrong,
# which is the failure mode that reaches a drawing.
#
# So it is not composed. Revit is asked for the shared coordinates of
# three internal points it already knows, the internal origin and one foot
# along each of internal X and Y, and the map is derived from its own
# answers. No convention is assumed, and the arithmetic below is testable
# without Revit.


class Frame(object):
    """The map between internal and shared coordinates, all in feet.

    Built from `Document.ActiveProjectLocation.GetProjectPosition` at
    three points. `origin` is the shared position of the internal origin,
    `x_axis` and `y_axis` are what one internal foot along each axis does
    to the shared easting and northing.
    """

    def __init__(self, origin, x_axis, y_axis):
        self.origin = tuple(float(value) for value in origin)
        self.x_axis = tuple(float(value) for value in x_axis)
        self.y_axis = tuple(float(value) for value in y_axis)

    def determinant(self):
        return (self.x_axis[0] * self.y_axis[1]
                - self.y_axis[0] * self.x_axis[1])

    def usable(self):
        """A degenerate frame cannot be inverted, so it must not be used.

        This is not hypothetical paranoia: it is what a model with no
        shared coordinates set up, or a broken project location, looks
        like from here.
        """
        return abs(self.determinant()) > 1e-9


def frame_from_positions(at_origin, at_x, at_y):
    """A Frame from three (easting, northing, elevation) readings.

    `at_origin` is the shared position of internal (0, 0, 0), `at_x` of
    internal (1, 0, 0) and `at_y` of internal (0, 1, 0), all in feet.
    """
    origin = (float(at_origin[0]), float(at_origin[1]), float(at_origin[2]))
    x_axis = (float(at_x[0]) - origin[0], float(at_x[1]) - origin[1])
    y_axis = (float(at_y[0]) - origin[0], float(at_y[1]) - origin[1])
    return Frame(origin, x_axis, y_axis)


def to_shared(internal, frame):
    """Internal (x, y, z) feet to shared (E, N, Z) feet."""
    x, y, z = (float(value) for value in internal)
    easting = frame.origin[0] + x * frame.x_axis[0] + y * frame.y_axis[0]
    northing = frame.origin[1] + x * frame.x_axis[1] + y * frame.y_axis[1]
    return easting, northing, frame.origin[2] + z


def to_internal(shared, frame):
    """Shared (E, N, Z) feet to internal (x, y, z) feet.

    Returns None when the frame cannot be inverted, rather than raising:
    the caller has a dialog to put that in.
    """
    if not frame.usable():
        return None
    easting, northing, elevation = (float(value) for value in shared)
    d_east = easting - frame.origin[0]
    d_north = northing - frame.origin[1]
    det = frame.determinant()
    x = (d_east * frame.y_axis[1] - frame.y_axis[0] * d_north) / det
    y = (frame.x_axis[0] * d_north - d_east * frame.x_axis[1]) / det
    return x, y, elevation - frame.origin[2]
