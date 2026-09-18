# -*- coding: utf-8 -*-
"""Place an AVH marker at a typed survey coordinate.

Type an easting, a northing and an elevation in shared coordinates, and
the tool puts one AVH_Coordinate_Marker there, hosted on the level below
the point, carrying what was typed in its own parameters.

## The transform is derived, not composed

Building the shared coordinate transform out of a rotation angle means
guessing a sign convention, and a sign guessed wrong puts the marker
somewhere plausible and wrong. That is the failure that reaches a
drawing, because nothing about it looks like an error.

So nothing is guessed. Revit is asked for the shared position of three
internal points it already knows, the origin and one foot along each of
internal X and Y, and the map is derived from its own answers. The
arithmetic lives in `avh_coords.model` and is tested without Revit.

## And then it is checked anyway

After placing, the instance's real location is read back and put through
Revit's own `GetProjectPosition`, and that is compared with what was
typed. Agreement inside a millimetre is reported; disagreement rolls the
whole thing back and names the axis. The forward calculation is AVH's
arithmetic and the readback is Revit's own answer, so a mistake in the
former cannot hide in the latter.

## Separators

AVH's Revit runs in a decimal comma locale and surveys arrive in every
shape, so both separators are accepted and both groupings. Where both
appear, the last one is the decimal. Where only one appears it is the
decimal, which makes "412.345" ambiguous and unfixable by any rule, so
the parsed values are shown back for confirmation before anything is
placed.

## Unverified

Not yet run in Revit: `ProjectLocation.GetProjectPosition`,
`Document.LoadFamily`, `Document.Create.NewFamilyInstance` with a level,
and whether the offset parameter needs setting after a placement that
already carried a Z.

**The family does not exist yet.** It has to be authored in Revit and
saved to `assets/AVH_Coordinate_Marker.rfa`. Until it is, the button
explains what is missing and stops.
"""

__title__ = "Place at\nCoordinate"
__author__ = "AVH"
__doc__ = ("Place an AVH marker at a typed survey coordinate, hosted on "
           "the level below it, and check afterwards that it landed "
           "where it was asked to.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. A button nested one deeper, in a pulldown, was enough
# to break the fixed count, and it breaks at import time with a message
# about a module nobody has heard of.
_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_coords import model                      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Place at Coordinate"

FAMILY_NAME = u"AVH_Coordinate_Marker"
ASSET_NAME = "AVH_Coordinate_Marker.rfa"

PARAM_EASTING = u"AVH_Easting"
PARAM_NORTHING = u"AVH_Northing"
PARAM_ELEVATION = u"AVH_Elevation"
PARAM_NAME = u"AVH_Point_Name"

FIELDS = (
    (u"easting", PARAM_EASTING, u"Easting (E), in metres"),
    (u"northing", PARAM_NORTHING, u"Northing (N), in metres"),
    (u"elevation", PARAM_ELEVATION, u"Elevation (Z), in metres"),
)


def asset_path():
    return os.path.join(_EXT_DIR, "assets", ASSET_NAME)


def find_symbol(doc):
    """The marker's family symbol, or None."""
    try:
        collector = DB.FilteredElementCollector(doc)
        collector = collector.OfClass(DB.FamilySymbol)
        for symbol in collector:
            try:
                if to_text(symbol.Family.Name) == FAMILY_NAME:
                    return symbol
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


def load_marker_family(doc):
    """Load the marker from the extension. True when it is available.

    Called inside the transaction, because LoadFamily writes.
    """
    path = asset_path()
    if not os.path.isfile(path):
        return False
    try:
        return bool(doc.LoadFamily(path))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False


def project_frame(doc):
    """The shared coordinate map, read out of Revit rather than composed."""
    try:
        location = doc.ActiveProjectLocation
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None
    if location is None:
        return None

    def position(x, y, z):
        result = location.GetProjectPosition(DB.XYZ(x, y, z))
        return (result.EastWest, result.NorthSouth, result.Elevation)

    try:
        return model.frame_from_positions(
            position(0.0, 0.0, 0.0),
            position(1.0, 0.0, 0.0),
            position(0.0, 1.0, 0.0))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def shared_of(doc, point):
    """Revit's own answer for the shared position of an internal point."""
    try:
        result = doc.ActiveProjectLocation.GetProjectPosition(point)
        return (result.EastWest, result.NorthSouth, result.Elevation)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def model_levels(doc):
    """[(level, elevation in feet)] for every level in the model."""
    found = []
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.Level)
        for level in collector:
            try:
                found.append((level, level.Elevation))
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return found


def ask_values():
    """The four typed values, or None when cancelled.

    Four prompts rather than one box, because a single field separated by
    commas is unusable in a decimal comma locale, which is the locale this
    runs in.
    """
    values = {}
    for index, (key, _param, prompt) in enumerate(FIELDS):
        answer = forms.ask_for_string(
            default=u"",
            prompt=prompt,
            title=u"{0}  ({1} of 4)".format(TITLE, index + 1))
        if answer is None:
            return None
        values[key] = answer

    name = forms.ask_for_string(
        default=u"",
        prompt=u"Point name or ID from the survey (optional)",
        title=u"{0}  (4 of 4)".format(TITLE))
    if name is None:
        return None
    values["name"] = name
    return values


def parse_values(values):
    """(metres dict, error). Exactly one of the two is meaningful."""
    parsed = {}
    for key, _param, prompt in FIELDS:
        label = prompt.split(u",")[0]
        try:
            parsed[key] = model.parse_number(values[key], label)
        except model.ParseError as exc:
            return None, to_text(exc)
    return parsed, u""


def set_text(instance, name, value):
    """Write a text parameter. Returns a note when it could not be."""
    try:
        parameter = instance.LookupParameter(name)
    except BaseException as exc:
        logger.debug(to_text(exc))
        parameter = None
    if parameter is None:
        return u"{0} is not a parameter of this family".format(name)
    try:
        if parameter.IsReadOnly:
            return u"{0} is read only".format(name)
        if not parameter.Set(value):
            return u"{0} would not take the value".format(name)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"{0} refused: {1}".format(name, to_text(exc))
    return u""


def move_to(instance, wanted, placed):
    """Shift the instance from where Revit put it to where it belongs.

    Revit may not honour the Z of the point handed to
    `NewFamilyInstance` when a level is given, snapping the instance to
    the level and keeping the difference as an offset.

    That was blamed for the first live run's 3.000 m error and was the
    wrong culprit: the real cause was reading the position before the
    document was regenerated. This is kept anyway, because a snap is a
    real Revit behaviour and the guard costs nothing once the position
    being read is true. On a correct placement the delta is zero and
    nothing happens.

    Correcting by moving is not the same as trusting the move. The
    readback still runs afterwards against Revit's own answer, so a
    conversion that is actually wrong still fails.

    Returns a note, empty when nothing needed doing.
    """
    delta = (wanted[0] - placed.X, wanted[1] - placed.Y, wanted[2] - placed.Z)
    if all(abs(value) < 1e-9 for value in delta):
        return u""
    try:
        DB.ElementTransformUtils.MoveElement(
            instance.Document, instance.Id, DB.XYZ(*delta))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"Revit placed it off the asked point and would not move " \
               u"it back: {0}".format(to_text(exc))
    return u"Revit placed it {0} m off the asked point and it was moved " \
           u"back.".format(model.format_metres(
               model.feet_to_metres(max(abs(value) for value in delta))))


def diagnose(frame, shared_feet, internal, placed, actual_shared_feet):
    """Everything the conversion did, in one block, for when it is wrong.

    Printed only on a mismatch. Two numbers in a dialog are not enough to
    work out which step went wrong, and the alternative is guessing from
    them, which is how this tool got shipped with a fault in it.
    """
    output.print_md(u"#### What the conversion did")
    output.print_md(u"_All in feet unless said otherwise._")
    output.print_md(u"```")
    output.print_md(u"frame origin  E {0}  N {1}  Z {2}".format(
        *[repr(value) for value in frame.origin]))
    output.print_md(u"frame x_axis  {0}".format(repr(frame.x_axis)))
    output.print_md(u"frame y_axis  {0}".format(repr(frame.y_axis)))
    output.print_md(u"determinant   {0}".format(repr(frame.determinant())))
    output.print_md(u"asked, shared {0}".format(repr(tuple(shared_feet))))
    output.print_md(u"computed, int {0}".format(repr(tuple(internal))))
    if placed is not None:
        output.print_md(u"placed at,int ({0}, {1}, {2})".format(
            repr(placed.X), repr(placed.Y), repr(placed.Z)))
    output.print_md(u"read back,shr {0}".format(repr(tuple(actual_shared_feet))))
    output.print_md(u"```")


def report(typed, actual, level_name, offset_feet, below_all, notes):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"| | Easting | Northing | Elevation |")
    output.print_md(u"| --- | ---: | ---: | ---: |")
    output.print_md(u"| Asked for | {0} | {1} | {2} |".format(
        *[model.format_metres(value) for value in typed]))
    output.print_md(u"| Placed at | {0} | {1} | {2} |".format(
        *[model.format_metres(value) for value in actual]))

    output.print_md(u"Hosted on **{0}**, offset {1} m.".format(
        level_name, model.format_metres(model.feet_to_metres(offset_feet))))
    if below_all:
        output.print_md(
            u"_That point is below every level in the model, so it hangs "
            u"under the lowest one._")
    for note in notes:
        output.print_md(u"- {0}".format(note))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    frame = project_frame(doc)
    if frame is None or not frame.usable():
        forms.alert(
            u"This model's shared coordinates could not be read, so a "
            u"survey coordinate cannot be converted. Check that the "
            u"project location and survey point are set up.",
            title=TITLE)
        return

    levels = model_levels(doc)
    if not levels:
        forms.alert(u"This model has no levels to host a marker on.",
                    title=TITLE)
        return

    values = ask_values()
    if values is None:
        return

    parsed, error = parse_values(values)
    if error:
        forms.alert(error, title=TITLE)
        return

    typed = (parsed["easting"], parsed["northing"], parsed["elevation"])
    shared_feet = tuple(model.metres_to_feet(value) for value in typed)
    internal = model.to_internal(shared_feet, frame)
    if internal is None:
        forms.alert(u"That coordinate could not be converted.", title=TITLE)
        return

    chosen = model.choose_level(
        internal[2], [(level, elevation) for level, elevation in levels])
    if chosen is None:
        forms.alert(u"No level could be chosen for that elevation.",
                    title=TITLE)
        return
    level, offset_feet, below_all = chosen

    if not forms.alert(
            u"Place {0} at\n\nE {1}\nN {2}\nZ {3}\n\non level {4}?\n\n"
            u"Check those numbers read the way you meant them.".format(
                FAMILY_NAME,
                model.format_metres(typed[0]),
                model.format_metres(typed[1]),
                model.format_metres(typed[2]),
                to_text(level.Name)),
            title=TITLE, yes=True, no=True):
        return

    notes = []
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        symbol = find_symbol(doc)
        if symbol is None:
            if not load_marker_family(doc):
                transaction.RollBack()
                forms.alert(
                    u"The marker family is missing.\n\n"
                    u"It has to be made once in Revit and saved as\n"
                    u"{0}\n\n"
                    u"Generic Model, not hosted, not workplane based, "
                    u"origin at the intersection of the two reference "
                    u"planes, with text parameters {1}, {2}, {3} and "
                    u"{4}.".format(asset_path(), PARAM_EASTING,
                                   PARAM_NORTHING, PARAM_ELEVATION,
                                   PARAM_NAME),
                    title=TITLE)
                return
            symbol = find_symbol(doc)
        if symbol is None:
            transaction.RollBack()
            forms.alert(u"The marker family loaded but no type was found "
                        u"in it.", title=TITLE)
            return

        try:
            if not symbol.IsActive:
                symbol.Activate()
        except BaseException as exc:
            logger.debug(to_text(exc))

        point = DB.XYZ(internal[0], internal[1], internal[2])
        instance = doc.Create.NewFamilyInstance(
            point, symbol, level, DB.Structure.StructuralType.NonStructural)

        # A new element does not report its position until the document
        # is regenerated. Without this, Location.Point answers (0, 0, 0),
        # which is not an error and not a null: it is a plausible point,
        # and every check downstream believed it. The first two live runs
        # both failed on that, once by refusing a correct placement and
        # once by "correcting" it to exactly twice the right vector.
        try:
            doc.Regenerate()
        except BaseException as exc:
            logger.debug(to_text(exc))

        for key, parameter_name, _prompt in FIELDS:
            note = set_text(instance, parameter_name, to_text(values[key]))
            if note:
                notes.append(note)
        note = set_text(instance, PARAM_NAME, to_text(values["name"]))
        if note:
            notes.append(note)

        try:
            placed = instance.Location.Point
        except BaseException as exc:
            logger.debug(to_text(exc))
            placed = None
        if placed is None:
            transaction.RollBack()
            forms.alert(u"The marker was placed but its position could "
                        u"not be read back, so nothing was kept.",
                        title=TITLE)
            return

        note = move_to(instance, internal, placed)
        if note:
            notes.append(note)
            try:
                placed = instance.Location.Point
            except BaseException as exc:
                logger.debug(to_text(exc))
                placed = None
            if placed is None:
                transaction.RollBack()
                forms.alert(u"The marker moved but its position could not "
                            u"be read back, so nothing was kept.",
                            title=TITLE)
                return

        actual_shared_feet = shared_of(doc, placed)
        if actual_shared_feet is None:
            transaction.RollBack()
            forms.alert(u"The marker's shared coordinates could not be "
                        u"read back, so nothing was kept.", title=TITLE)
            return

        actual = tuple(model.feet_to_metres(value)
                       for value in actual_shared_feet)
        ok, deltas = model.agree(typed, actual)
        if not ok:
            diagnose(frame, shared_feet, internal, placed, actual_shared_feet)
            transaction.RollBack()
            forms.alert(
                u"The marker did not land where it was asked to, so "
                u"nothing was kept.\n\n{0}\n\nThe numbers behind it are "
                u"in the output window. Send them on rather than reading "
                u"them: two deltas are not enough to say which step is "
                u"wrong.".format(model.describe_deltas(deltas)),
                title=TITLE)
            logger.error(u"coordinate mismatch: {0}".format(
                model.describe_deltas(deltas)))
            return
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was placed. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the change, so nothing was placed "
            u"({0}).".format(to_text(status)),
            title=TITLE)
        return

    report(typed, actual, to_text(level.Name), offset_feet, below_all, notes)


if __name__ == "__main__":
    run()
