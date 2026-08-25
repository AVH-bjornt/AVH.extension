# -*- coding: utf-8 -*-
"""Draw a plan showing which room each door takes its CCI ID from.

Room Data Sync gives a door the ID from its `ToRoom`, and falls back to
`FromRoom` when the ToRoom has no ID. Which side is which is invisible
in the model, so a wrong value looks like a bug in the sync when it is
usually a door facing the wrong way or a room boundary that is missing.

This makes a plan view of one level and draws, at every door, an arrow
pointing into the ToRoom with the room numbers on both sides. Green
means the sync got its ID from the ToRoom. Amber and red mean it did
not, and why.

## What it draws

`ToRoom` is the room on the side the door **faces**, so the arrow is
just `FamilyInstance.FacingOrientation`. Flip a door's facing and the
two rooms swap, which is exactly the mistake this view is for finding.

Arrow, head and text are sized in millimetres on paper and multiplied by
the view scale, so they stay the same size on the sheet at 1:50 or
1:200. Drawing a fixed model length instead is what makes an annotation
tool useless on the second project.

## Nothing permanent

Everything drawn is view specific: detail curves and text notes that
exist only in this view. Delete the view and the marks go with it.
Running it again on the same level wipes what it drew last time and
redraws, so the view is always current rather than accumulating.

**Ownership is the view name**, `AVH Door Rooms - <level> - <phase>`. A
view of any other name is never found, so it is never cleared and never
drawn into.

## Phase, which 2.15.0 got wrong

Rooms exist per phase, and asking the wrong one gives no rooms at all
rather than an error. 2.15.0 used the document's last phase without
asking. On Eldisgardur that is Phase 2, the rooms are not in it, and the
result was a plan reading "no room" at every single door: a confident
drawing of nothing.

So the phase is now **asked, every run**, with the number of placed rooms
in each phase on the label, because the phase to pick is whichever one
the rooms are in and nobody should have to know that in advance. The
view is then put on that same phase, so the drawing cannot disagree with
its own labels, and the phase stays in the view name.

If every door still comes back with no room and another phase does hold
rooms, the report says so by name rather than leaving it to a screenshot.

## The missing arrows

At 2.15.0 the text notes drew and the arrows did not. A text note is an
annotation category; a detail line lives in the model `Lines` category.
A view template on the new view, or a hidden `Lines` category, produces
exactly that: labels and no arrows.

`make_marks_visible` corrects both and **reports both**, so the next run
says which it was. It is a probe as much as a fix, and the arrow count
is now in the report for the same reason.

## Unverified

Not yet run to completion in Revit: `ViewPlan.Create`, `NewDetailCurve`,
`TextNote.Create`, `SetElementOverrides` on detail curves,
`BuiltInParameter.VIEW_PHASE` and `ROOM_PHASE`, and whether the plan
view's `Origin` sits at a Z the detail curves will accept. The room
lookup is the same `get_ToRoom(phase)` route Room Data Sync runs on
Eldisgardur, and the phase failure above is confirmed.
"""

__title__ = "Door Room\nCheck"
__author__ = "AVH"
__doc__ = ("Make a plan of one level with an arrow at every door "
           "pointing into its ToRoom, room numbers on both sides, and "
           "the problem doors in red. Rerun to refresh it.")

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
from avh_doorcheck import model                   # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Door Room Check"
VIEW_PREFIX = u"AVH Door Rooms"

# The same parameter Room Data Sync reads. If these two ever disagree,
# this view is lying about what the sync will do.
SOURCE_PARAM = u"CCIMultiLevelLocationID"

MAX_LISTED = 30


def id_value(element_id):
    if element_id is None:
        return None
    value = getattr(element_id, "Value", None)
    if value is None:
        value = getattr(element_id, "IntegerValue", None)
    return value


def all_phases(doc):
    phases = []
    try:
        collection = doc.Phases
        if collection and collection.Size:
            for index in range(collection.Size):
                phases.append(collection[index])
    except BaseException as exc:
        logger.debug(to_text(exc))
    return phases


def room_counts_by_phase(doc):
    """How many placed rooms each phase holds, keyed by phase id value.

    This is what makes the phase picker usable: the phase to pick is
    whichever one the rooms are in, and nobody should have to know that
    in advance. Unplaced rooms are skipped, since a room with no location
    tells you nothing about which phase is the working one.
    """
    counts = {}
    try:
        collector = DB.FilteredElementCollector(doc)
        collector = collector.OfCategory(DB.BuiltInCategory.OST_Rooms)
        collector = collector.WhereElementIsNotElementType()
        for room in collector:
            try:
                if room.Location is None:
                    continue
            except BaseException:
                pass
            try:
                parameter = room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE)
                if parameter is None:
                    continue
                key = id_value(parameter.AsElementId())
            except BaseException:
                continue
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
    except BaseException as exc:
        logger.debug(to_text(exc))
    return counts


def pick_phase(doc):
    """Which phase to read rooms in. Returns (phase, entries) or (None, ...).

    Always asked, because getting it wrong is invisible: 2.15.0 used the
    document's last phase without asking, found no rooms in it on
    Eldisgardur, and drew a plan reading "no room" at every door.
    """
    phases = all_phases(doc)
    if not phases:
        return None, []

    counts = room_counts_by_phase(doc)
    entries = [(element_name(phase), counts.get(id_value(phase.Id), 0))
               for phase in phases]

    if len(phases) == 1:
        return phases[0], entries

    labels, mapping = model.phase_labels(entries)
    chosen = forms.SelectFromList.show(
        labels, multiselect=False, title=TITLE + u": which phase are the "
        u"rooms in?", button_name="Use this phase")
    if not chosen:
        return None, entries
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    wanted = mapping.get(to_text(chosen))
    for phase in phases:
        if element_name(phase) == wanted:
            return phase, entries
    return None, entries


def element_name(element):
    if element is None:
        return u""
    try:
        return to_text(element.Name)
    except BaseException:
        return u""


def pick_level(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    if not levels:
        return None

    def elevation(level):
        try:
            return level.Elevation
        except BaseException:
            return 0.0

    levels.sort(key=elevation)
    names = [element_name(level) for level in levels]
    by_name = {}
    for level, name in zip(levels, names):
        by_name.setdefault(name, level)

    if len(levels) == 1:
        return levels[0]

    chosen = forms.SelectFromList.show(
        names, multiselect=False, title=TITLE + u": pick a level",
        button_name="Make the view")
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    return by_name.get(to_text(chosen))


def plan_view_type(doc):
    for view_type in DB.FilteredElementCollector(doc).OfClass(
            DB.ViewFamilyType):
        try:
            if view_type.ViewFamily == DB.ViewFamily.FloorPlan:
                return view_type
        except BaseException:
            continue
    return None


def find_view(doc, name):
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        try:
            if view.IsTemplate:
                continue
        except BaseException:
            continue
        if element_name(view) == name:
            return view
    return None


def text_note_type(doc):
    for note_type in DB.FilteredElementCollector(doc).OfClass(
            DB.TextNoteType):
        return note_type
    return None


def clear_view(doc, view):
    """Delete what a previous run drew. Returns how many went.

    **Ownership is the view name, and `find_view` is the only place that
    decides it.** An earlier version repeated a prefix check here, which
    could never fire: the only view ever passed in was the one found by
    that exact name. A check whose result cannot change the outcome is
    decoration, and this repository has already paid once for treating
    decoration as a safeguard.

    The real protection is that a view of any other name is never found,
    so it is never cleared and never drawn into. A human who names their
    own plan `AVH Door Rooms - E01 - Phase 1` will have it refreshed,
    which is the one case worth knowing about.
    """
    from System.Collections.Generic import List

    ids = List[DB.ElementId]()
    count = 0
    for cls in (DB.CurveElement, DB.TextNote):
        try:
            collector = DB.FilteredElementCollector(doc, view.Id).OfClass(cls)
            for element in collector:
                ids.Add(element.Id)
                count += 1
        except BaseException as exc:
            logger.debug(to_text(exc))

    if count:
        try:
            doc.Delete(ids)
        except BaseException as exc:
            logger.debug(to_text(exc))
            return 0
    return count


def doors_on(doc, level_id):
    doors = []
    collector = DB.FilteredElementCollector(doc)
    collector = collector.OfCategory(DB.BuiltInCategory.OST_Doors)
    collector = collector.WhereElementIsNotElementType()
    for door in collector:
        try:
            if door.LevelId == level_id:
                doors.append(door)
        except BaseException:
            continue
    return doors


def room_on(door, side, phase):
    """`side` is "ToRoom" or "FromRoom". Same route as Room Data Sync."""
    getter = getattr(door, "get_" + side, None)
    if getter is not None and phase is not None:
        try:
            room = getter(phase)
            if room is not None:
                return room
        except BaseException:
            pass
    return getattr(door, side, None)


def room_value(room):
    if room is None:
        return u""
    try:
        parameter = room.LookupParameter(SOURCE_PARAM)
        if parameter is None:
            return u""
        return to_text(parameter.AsString())
    except BaseException:
        return u""


def room_label(room):
    if room is None:
        return u""
    number = u""
    try:
        parameter = room.LookupParameter(u"Number")
        if parameter is not None:
            number = to_text(parameter.AsString())
    except BaseException:
        pass
    return number or element_name(room)


def same_room(first, second):
    if first is None or second is None:
        return False
    try:
        return first.Id == second.Id
    except BaseException:
        return False


def door_origin(door):
    try:
        point = getattr(door.Location, "Point", None)
        if point is not None:
            return (point.X, point.Y)
    except BaseException:
        pass
    return None


def facing_of(door):
    try:
        facing = door.FacingOrientation
        return (facing.X, facing.Y)
    except BaseException:
        return None


def draw_line(doc, view, z, start, end, colour):
    line = DB.Line.CreateBound(DB.XYZ(start[0], start[1], z),
                               DB.XYZ(end[0], end[1], z))
    curve = doc.Create.NewDetailCurve(view, line)
    try:
        overrides = DB.OverrideGraphicSettings()
        revit_colour = DB.Color(colour[0], colour[1], colour[2])
        overrides.SetProjectionLineColor(revit_colour)
        overrides.SetProjectionLineWeight(4)
        view.SetElementOverrides(curve.Id, overrides)
    except BaseException as exc:
        logger.debug(to_text(exc))
    return curve


def draw_text(doc, view, z, point, text, type_id):
    if not text or type_id is None:
        return None
    try:
        return DB.TextNote.Create(doc, view.Id,
                                  DB.XYZ(point[0], point[1], z),
                                  text, type_id)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def set_view_phase(view, phase):
    """Put the view on the same phase the rooms were read in.

    A plan showing Phase 2 while the labels were read from Phase 1 would
    be a drawing that disagrees with itself. Returns a note for the
    report, empty when there was nothing to say.
    """
    if phase is None:
        return u""
    try:
        parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
    except BaseException as exc:
        return u"the view's phase could not be read ({0})".format(to_text(exc))
    if parameter is None:
        return u"this view has no phase parameter"
    if parameter.IsReadOnly:
        return u"the view's phase is read only"
    try:
        if not parameter.Set(phase.Id):
            return u"the view's phase would not take the value"
    except BaseException as exc:
        return u"the view's phase could not be set ({0})".format(to_text(exc))
    return u""


def make_marks_visible(doc, view):
    """Make sure the view will actually show detail lines.

    **This is a probe as much as a fix.** At 2.15.0 the text notes drew
    and the arrows did not, and the difference between them is that a
    text note is an annotation category while a detail line lives in the
    model `Lines` category. A view template on the new view, or a hidden
    Lines category, would produce exactly that: labels with no arrows.

    Both are corrected here and both are reported, so the next run says
    which one it was rather than leaving it to another screenshot.
    """
    notes = []

    try:
        template_id = view.ViewTemplateId
        if template_id is not None and template_id != DB.ElementId.InvalidElementId:
            name = element_name(doc.GetElement(template_id)) or u"a template"
            view.ViewTemplateId = DB.ElementId.InvalidElementId
            notes.append(u"removed the view template \"{0}\", which would "
                         u"have overruled the visibility settings".format(name))
    except BaseException as exc:
        logger.debug(to_text(exc))

    try:
        built_in = getattr(DB.BuiltInCategory, "OST_Lines", None)
        if built_in is not None:
            category = DB.Category.GetCategory(doc, built_in)
            if category is not None:
                if view.GetCategoryHidden(category.Id):
                    if view.CanCategoryBeHidden(category.Id):
                        view.SetCategoryHidden(category.Id, False)
                        notes.append(u"the Lines category was hidden in this "
                                     u"view and has been switched back on, "
                                     u"which is why 2.15.0 drew no arrows")
                    else:
                        notes.append(u"the Lines category is hidden in this "
                                     u"view and cannot be switched on, so "
                                     u"the arrows will not show")
    except BaseException as exc:
        logger.debug(to_text(exc))

    return notes


def view_plane_z(view, level):
    try:
        origin = view.Origin
        if origin is not None:
            return origin.Z
    except BaseException:
        pass
    try:
        return level.Elevation
    except BaseException:
        return 0.0


def report(tally, level_name, phase_name, view_label, cleared,
           curves, notes, entries):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"**{0}**, {1} door(s), phase {2}.".format(
        view_label, tally.total(), phase_name or u"unknown"))
    output.print_md(u"{0} arrow line(s) drawn.".format(curves))
    if cleared:
        output.print_md(u"_Refreshed: {0} mark(s) from the last run "
                        u"removed._".format(cleared))
    for note in notes:
        output.print_md(u"_{0}._".format(note))

    # The failure the phase picker exists to prevent, said out loud.
    if tally.counts.get(model.NO_ROOMS) == tally.total() and tally.total():
        busiest = model.busiest_phase(entries)
        if busiest and busiest != phase_name:
            output.print_md(
                u"**No door found a room in {0}, while {1} holds rooms.** "
                u"Run it again and pick that phase.".format(
                    phase_name or u"that phase", busiest))

    for label, count in tally.rows():
        output.print_md(u"- {0}: **{1}**".format(label, count))

    if tally.problems:
        output.print_md(u"#### Doors to look at")
        for state, element_id, description in tally.problems[:MAX_LISTED]:
            output.print_md(u"- {0} {1} {2}".format(
                output.linkify(element_id), description,
                model.STATE_LABELS[state]))
        if len(tally.problems) > MAX_LISTED:
            output.print_md(u"- _and {0} more_".format(
                len(tally.problems) - MAX_LISTED))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if doc.IsFamilyDocument:
        forms.alert(u"This works on a project, not on a family document.",
                    title=TITLE)
        return

    level = pick_level(doc)
    if level is None:
        return

    phase, phase_entries = pick_phase(doc)
    if phase is None:
        return
    phase_name = element_name(phase)
    level_name = element_name(level)
    name = model.view_name(VIEW_PREFIX, level_name, phase_name)

    doors = doors_on(doc, level.Id)
    if not doors:
        forms.alert(u"No doors were found on {0}.".format(level_name),
                    title=TITLE)
        return

    existing = find_view(doc, name)
    view_type = None
    if existing is None:
        view_type = plan_view_type(doc)
        if view_type is None:
            forms.alert(u"This model has no floor plan view type.",
                        title=TITLE)
            return

    note_type = text_note_type(doc)
    if note_type is None:
        forms.alert(
            u"This model has no text note type, so the room numbers "
            u"cannot be written. The arrows alone would say which side "
            u"is which, but not which room, so nothing was drawn.",
            title=TITLE)
        return

    tally = model.Tally()
    cleared = 0
    curves = 0
    notes = []

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        view = existing
        if view is None:
            view = DB.ViewPlan.Create(doc, view_type.Id, level.Id)
            view.Name = name
        else:
            cleared = clear_view(doc, view)

        notes.extend(make_marks_visible(doc, view))
        phase_note = set_view_phase(view, phase)
        if phase_note:
            notes.append(phase_note)

        try:
            scale = view.Scale
        except BaseException:
            scale = 100
        z = view_plane_z(view, level)

        for door in doors:
            to_room = room_on(door, "ToRoom", phase)
            from_room = room_on(door, "FromRoom", phase)
            state = model.classify(
                to_room is not None, from_room is not None,
                room_value(to_room), room_value(from_room),
                same_room(to_room, from_room))

            description = u"{0} {1}".format(
                to_text(getattr(door, "Name", u"")) or u"door",
                room_label(to_room) or u"")
            tally.add(state, door.Id, description.strip())

            origin = door_origin(door)
            facing = facing_of(door)
            if origin is None or facing is None:
                continue

            arrow = model.arrow_points(origin, facing, scale)
            if arrow is None:
                continue

            colour = model.STATE_COLOURS[state]
            for key in ("shaft", "head_left", "head_right"):
                start, end = arrow[key]
                if draw_line(doc, view, z, start, end, colour) is not None:
                    curves += 1

            to_label, from_label = model.label_for(
                state, room_label(to_room), room_label(from_room))
            draw_text(doc, view, z, arrow["to_text"], to_label, note_type.Id)
            draw_text(doc, view, z, arrow["from_text"], from_label,
                      note_type.Id)
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was drawn. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the changes, so nothing was drawn ({0}).".format(
                to_text(status)),
            title=TITLE)
        return

    report(tally, level_name, phase_name, name, cleared,
           curves, notes, phase_entries)

    try:
        uidoc = revit.uidoc
        if uidoc is not None:
            uidoc.ActiveView = view
    except BaseException as exc:
        logger.debug(to_text(exc))


if __name__ == "__main__":
    run()
