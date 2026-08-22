# -*- coding: utf-8 -*-
"""Copy each room's CCI location ID onto the things inside it.

Reads `CCIMultiLevelLocationID` from a room and writes it into
`CCISingleLevelLocationAtID` on the furniture, casework, doors and
equipment found in that room. Schedules and facilities management both
read the result, so the value has to be right rather than merely present.

Nothing is written until a dry run has been printed and confirmed, and
every value that would *change* is listed separately from every blank
that would be filled. Filling a blank is uncontroversial; overwriting
something a person typed is not, and burying the second inside the first
is how a sync tool quietly destroys hand corrections.

## Which room an element is in

Not one question but three, and the answer says which was used so a
suspicious value can be traced.

**Doors and windows** are in a wall between two rooms, so they are in
neither. `ToRoom` is preferred, being the room the opening serves, and
`FromRoom` is the fallback. That fallback fires on the **value**, not on
whether a room is there: a door whose ToRoom is a room with a blank CCI
ID takes FromRoom's ID rather than coming away with nothing. The report
says when it fell through, and why.

**Everything else** is asked directly first, through the room the family
instance reports. That only answers when the family has a room
calculation point, which many do not, so the fallback is the element's
own position looked up against the model.

**Position needs care.** An insertion point usually sits exactly on the
floor, which is the room's own lower boundary, and a point on a boundary
belongs to nothing. The lookup is done slightly above it.

Phase matters too: rooms exist per phase, and an element is asked about
its own phase rather than whichever one happens to be last.

## Unverified

The parameter names below. `CCIMultiLevelLocationID` is confirmed from
AVH's room schedule exports. `CCISingleLevelLocationAtID` is not: the
door schedule carries `CCISingleLevelID`, which holds a per door tag
rather than a level. If the target parameter is absent the run stops and
lists the CCI parameters it did find, rather than reporting that it
changed nothing, which would look like success.
"""

__title__ = "Room Data\nSync"
__author__ = "AVH"
__doc__ = ("Copy CCIMultiLevelLocationID from each room into "
           "CCISingleLevelLocationAtID on the furniture, casework, "
           "doors and equipment inside it. Prints a dry run and asks "
           "before writing.")

import os
import sys

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_rooms import model                       # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = "Room Data Sync"

SOURCE_PARAM = u"CCIMultiLevelLocationID"
TARGET_PARAM = u"CCISingleLevelLocationAtID"

# Used only to help when the target parameter is missing, so the report
# can say what the model does have instead of shrugging.
DIAGNOSTIC_PREFIX = u"CCI"

MAX_LISTED = 30

# Enough above the insertion point to be inside the room rather than on
# its floor, and low enough to stay in the same room. 100 mm.
LOOKUP_LIFT = 100.0 / 304.8

# Doors and windows are asked about ToRoom and FromRoom; everything else
# is asked where it is. Names are resolved through getattr so a category
# this Revit does not have simply drops out.
OPENING_CATEGORIES = ("OST_Doors", "OST_Windows")
CONTAINED_CATEGORIES = (
    "OST_Furniture",
    "OST_FurnitureSystems",
    "OST_Casework",
    "OST_SpecialityEquipment",
    "OST_MechanicalEquipment",
    "OST_Stairs",
    "OST_StairsRailing",
)


def bic(name):
    return getattr(DB.BuiltInCategory, name, None)


def eid_value(element_id):
    if element_id is None:
        return None
    value = getattr(element_id, "Value", None)
    if value is not None:
        return value
    return element_id.IntegerValue


def describe(element):
    """A readable label. Never raises."""
    if element is None:
        return u"unknown element"
    parts = []
    try:
        category = element.Category
        if category is not None and category.Name:
            parts.append(to_text(category.Name))
    except BaseException:
        pass
    try:
        name = element.Name
        if name:
            parts.append(to_text(name))
    except BaseException:
        pass
    if not parts:
        try:
            parts.append(to_text(element.GetType().Name))
        except BaseException:
            return u"unknown element"
    return u" / ".join(parts)


def text_param(element, name):
    """A parameter by name, or None. Shared parameter names are stable.

    Looked up by name rather than by BuiltInParameter because these are
    shared parameters and have no built in enum. Unlike a category name a
    shared parameter name does not change with the interface language, so
    this is safe.
    """
    try:
        return element.LookupParameter(name)
    except BaseException:
        return None


def read_text(element, name):
    param = text_param(element, name)
    if param is None:
        return None
    try:
        return to_text(param.AsString())
    except BaseException:
        return None


def writable_text_param(element, name):
    param = text_param(element, name)
    if param is None:
        return None
    try:
        if param.IsReadOnly:
            return None
        if param.StorageType != DB.StorageType.String:
            return None
    except BaseException:
        return None
    return param


def cci_parameters(element):
    """Every CCI parameter this element has, for the diagnostic."""
    names = []
    try:
        params = element.Parameters
    except BaseException:
        return names
    for param in params:
        try:
            name = to_text(param.Definition.Name)
        except BaseException:
            continue
        if name.upper().startswith(DIAGNOSTIC_PREFIX):
            names.append(name)
    return sorted(set(names))


def phase_of(doc, element):
    """The element's own phase, falling back to the document's last.

    Rooms exist per phase, so asking which room an element is in without
    saying when is meaningless. Asking about the element's own phase is
    the answer that matches what a person sees on the sheet.
    """
    enum = getattr(DB.BuiltInParameter, "PHASE_CREATED", None)
    if enum is not None:
        try:
            param = element.get_Parameter(enum)
            if param is not None:
                phase = doc.GetElement(param.AsElementId())
                if phase is not None:
                    return phase
        except BaseException:
            pass
    try:
        phases = doc.Phases
        if phases and phases.Size:
            return phases[phases.Size - 1]
    except BaseException:
        pass
    return None


def room_of_opening(doc, element, phase):
    """A door or window's room. Returns (room, how it was found).

    ToRoom is preferred, being the room the opening serves. The fallback
    to FromRoom fires on the **value**, not on whether a room is there:
    a door whose ToRoom is a room with a blank CCI ID takes FromRoom's ID
    instead of coming away with nothing. The ID is the entire point of
    the sync, and refusing one that is sitting on the other side of the
    door helps nobody.

    When both sides are blank the preferred side is still returned, so
    the report names the room somebody has to go and fix.
    """
    sides = (("get_ToRoom", "ToRoom", u"ToRoom"),
             ("get_FromRoom", "FromRoom", u"FromRoom"))
    candidates = []
    for phased_name, plain_name, label in sides:
        room = None
        getter = getattr(element, phased_name, None)
        if getter is not None and phase is not None:
            try:
                room = getter(phase)
            except BaseException:
                room = None
        if room is None:
            # The unphased property answers for the last phase. Falling
            # back to it matters: the first version only ever asked the
            # phased getter, so a document whose phase could not be
            # resolved returned no room for any door at all and the run
            # reported nothing to do, which looks exactly like a model
            # that is already correct.
            room = getattr(element, plain_name, None)
        if room is not None:
            candidates.append((room, label))

    if not candidates:
        return None, u""

    values = [read_text(room, SOURCE_PARAM) for room, _label in candidates]
    index, fell_through = model.choose_source(values)
    room, label = candidates[index]
    if fell_through:
        label = u"{0}, because {1} had no ID".format(
            label, candidates[0][1])
    return room, label


def location_point(element):
    """A point inside the element, lifted clear of the floor.

    An insertion point sits on the room's lower boundary, and a point on
    a boundary is in no room at all, so everything here is asked slightly
    above where it actually is.
    """
    try:
        location = element.Location
        point = getattr(location, "Point", None)
        if point is not None:
            return DB.XYZ(point.X, point.Y, point.Z + LOOKUP_LIFT)
    except BaseException:
        pass
    # Stairs and railings have no location point, so use the middle of
    # the bounding box instead.
    try:
        box = element.get_BoundingBox(None)
        if box is not None:
            return DB.XYZ((box.Min.X + box.Max.X) / 2.0,
                          (box.Min.Y + box.Max.Y) / 2.0,
                          box.Min.Z + LOOKUP_LIFT)
    except BaseException:
        pass
    return None


def room_of_element(doc, element, phase):
    """Any other element's room. Returns (room, how it was found)."""
    getter = getattr(element, "get_Room", None)
    if getter is not None and phase is not None:
        try:
            room = getter(phase)
            if room is not None:
                return room, u"room calculation point"
        except BaseException:
            pass
    plain = getattr(element, "Room", None)
    if plain is not None:
        return plain, u"room property"

    point = location_point(element)
    if point is None:
        return None, u""
    try:
        if phase is not None:
            room = doc.GetRoomAtPoint(point, phase)
        else:
            room = doc.GetRoomAtPoint(point)
        if room is not None:
            return room, u"position"
    except BaseException:
        pass
    return None, u""


def collect(doc, category_names):
    """Every instance of these categories, types excluded."""
    found = []
    for name in category_names:
        enum = bic(name)
        if enum is None:
            continue
        try:
            collected = (DB.FilteredElementCollector(doc)
                         .OfCategory(enum)
                         .WhereElementIsNotElementType()
                         .ToElements())
        except BaseException:
            continue
        for element in collected:
            found.append(element)
    return found


def level_name_of(doc, element):
    try:
        level = doc.GetElement(element.LevelId)
        if level is not None:
            return to_text(level.Name)
    except BaseException:
        pass
    return u""


def plan(doc, levels_wanted):
    """Work out what would happen. Writes nothing.

    Returns (entries, missing_target), where missing_target lists the
    elements that have no writable target parameter along with the CCI
    parameters they do have. That list is the point of the first run: the
    target parameter name is the one thing here that has not been
    confirmed against a real model.
    """
    entries = []
    missing_target = []

    openings = collect(doc, OPENING_CATEGORIES)
    contained = collect(doc, CONTAINED_CATEGORIES)

    for element, is_opening in ([(e, True) for e in openings]
                                + [(e, False) for e in contained]):
        level_name = level_name_of(doc, element)
        if levels_wanted and level_name and level_name not in levels_wanted:
            continue

        label = describe(element)
        target = writable_text_param(element, TARGET_PARAM)
        if target is None:
            entry = model.SyncEntry(
                element.Id, label, model.NO_TARGET,
                reason=u"no writable text parameter named "
                       + TARGET_PARAM)
            entry.diagnostics = cci_parameters(element)
            entries.append(entry)
            missing_target.append(entry)
            continue

        phase = phase_of(doc, element)
        if is_opening:
            room, found_by = room_of_opening(doc, element, phase)
        else:
            room, found_by = room_of_element(doc, element, phase)

        current = read_text(element, TARGET_PARAM)

        if room is None:
            entries.append(model.SyncEntry(
                element.Id, label, model.NO_ROOM,
                current_value=current or u"",
                reason=u"not inside any room in its own phase"))
            continue

        room_value = read_text(room, SOURCE_PARAM)
        room_label = describe(room)
        try:
            number = to_text(room.Number)
            if number:
                room_label = number + u" " + room_label
        except BaseException:
            pass

        action = model.classify(room_value, current)
        entries.append(model.SyncEntry(
            element.Id, label, action,
            room_label=room_label,
            room_value=model.normalise(room_value),
            current_value=model.normalise(current),
            found_by=found_by,
            reason=(u"the room's " + SOURCE_PARAM + u" is blank"
                    if action == model.NO_SOURCE else u"")))

    return entries, missing_target


def report(entries, missing_target):
    """Print the dry run. Returns the counts."""
    counts = model.summarise(entries)

    output.print_md("# Room Data Sync, dry run")
    output.print_md("Copying `{0}` into `{1}`.".format(
        SOURCE_PARAM, TARGET_PARAM))
    output.print_md("Nothing has been written to the model yet.")
    output.print_md(
        "{0} to fill, {1} to change, {2} already correct, {3} not in a "
        "room, {4} in a room with no ID, {5} with no target "
        "parameter".format(
            counts[model.FILL], counts[model.CHANGE], counts[model.MATCH],
            counts[model.NO_ROOM], counts[model.NO_SOURCE],
            counts[model.NO_TARGET]))

    if missing_target:
        output.print_md(
            "## The target parameter was not found on {0} element(s)"
            .format(len(missing_target)))
        output.print_md(
            "This is the one thing about this tool that had not been "
            "checked against a real model. If it is a naming problem "
            "rather than a binding problem, the CCI parameters these "
            "elements *do* have are listed below.")
        seen = {}
        for entry in missing_target:
            key = u", ".join(getattr(entry, "diagnostics", []) or
                             [u"none"])
            seen.setdefault(key, []).append(entry.description)
        for key in sorted(seen):
            names = sorted(set(seen[key]))
            output.print_md("* `{0}`  \n  on {1} element(s), e.g. "
                            "{2}".format(key, len(seen[key]),
                                         u"; ".join(names[:3])))

    changes = [e for e in entries if e.action == model.CHANGE]
    if changes:
        output.print_md("## Values that would change ({0})".format(
            len(changes)))
        output.print_md(
            "These already hold something different. Read them before "
            "confirming: a value here was either set by an earlier sync "
            "and is now stale, or typed by hand and about to be lost.")
        for entry in changes[:MAX_LISTED]:
            output.print_md(
                "* {0} {1}  \n  `{2}` becomes `{3}`, from room {4} "
                "(found by {5})".format(
                    output.linkify(entry.element_id), entry.description,
                    entry.current_value, entry.room_value,
                    entry.room_label, entry.found_by))
        if len(changes) > MAX_LISTED:
            output.print_md("* and {0} more".format(
                len(changes) - MAX_LISTED))

    fills = [e for e in entries if e.action == model.FILL]
    if fills:
        output.print_md("## Blanks that would be filled ({0})".format(
            len(fills)))
        by_room = {}
        for entry in fills:
            by_room.setdefault(entry.room_value, []).append(entry)
        for value in sorted(by_room):
            group = by_room[value]
            # How the room was decided belongs here as well as on the
            # changes. For a door it is the difference between ToRoom and
            # FromRoom, which is the first thing anyone asks when a value
            # looks wrong, and the first version only printed it for
            # values that changed.
            methods = sorted(set(e.found_by for e in group if e.found_by))
            trace = u", found by " + u", ".join(methods) if methods else u""
            output.print_md("* `{0}`, {1} element(s), room {2}{3}".format(
                value, len(group), group[0].room_label, trace))

    report_problem(entries, model.NO_ROOM,
                   "## Not inside any room ({0})",
                   "These get nothing. Either they sit outside every "
                   "room, or the room they are in does not exist in "
                   "their phase.")
    report_problem(entries, model.NO_SOURCE,
                   "## In a room that has no ID of its own ({0})",
                   "The room is the source of truth and this one is "
                   "blank, so there is nothing to copy. Fix the room.")
    return counts


def report_problem(entries, action, heading, note):
    group = [e for e in entries if e.action == action]
    if not group:
        return
    output.print_md(heading.format(len(group)))
    output.print_md(note)
    for entry in group[:MAX_LISTED]:
        extra = u""
        if entry.room_label:
            extra = u", room {0}".format(entry.room_label)
        output.print_md("* {0} {1}{2}".format(
            output.linkify(entry.element_id), entry.description, extra))
    if len(group) > MAX_LISTED:
        output.print_md("* and {0} more".format(len(group) - MAX_LISTED))


def commit_checked(transaction):
    """Commit, and say whether Revit actually kept it.

    `Transaction.Commit` returns a status, and Revit's failure handling
    can roll a transaction back during the commit. Ignoring that return
    value is how Remove Level once reported a room as moved that Revit
    had thrown away, so nothing in this extension commits blind again.
    """
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is None:
        return True
    return status == committed


def apply_plan(doc, entries, include_changes):
    """Write the values. One transaction, so one undo step."""
    written = 0
    failed = []

    transaction = DB.Transaction(doc, "AVH room data sync")
    transaction.Start()
    try:
        for entry in model.writable(entries, include_changes):
            element = doc.GetElement(entry.element_id)
            if element is None:
                failed.append((entry, u"no longer in the model"))
                continue
            param = writable_text_param(element, TARGET_PARAM)
            if param is None:
                failed.append((entry, u"the parameter is no longer "
                                      u"writable"))
                continue
            try:
                param.Set(entry.room_value)
                written += 1
            except BaseException as exc:
                failed.append((entry, to_text(exc)))
    except BaseException:
        transaction.RollBack()
        raise

    if not written:
        transaction.RollBack()
    elif not commit_checked(transaction):
        failed.append((None, u"Revit rolled the write back during the "
                             u"commit"))
        written = 0

    return written, failed


def confirm(message, yes_label):
    """Blocking yes or no. True only on an explicit yes."""
    try:
        answer = forms.alert(message, title=TITLE,
                             options=[yes_label, "Cancel"])
    except BaseException:
        return False
    return answer == yes_label


def choose_levels(doc):
    """Which levels to include. Empty means every level."""
    levels = (DB.FilteredElementCollector(doc)
              .OfCategory(DB.BuiltInCategory.OST_Levels)
              .WhereElementIsNotElementType()
              .ToElements())
    names = sorted(set(to_text(level.Name) for level in levels
                       if isinstance(level, DB.Level)))
    if not names:
        return set()
    try:
        chosen = forms.SelectFromList.show(
            names, title="Levels to sync, or cancel for all",
            button_name="Sync these", multiselect=True)
    except BaseException:
        return set()
    if not chosen:
        return set()
    if isinstance(chosen, (list, tuple, set)):
        return set(to_text(name) for name in chosen)
    return set([to_text(chosen)])


def ask_to_write(counts):
    """Confirm. Returns (go_ahead, include_changes)."""
    total = counts[model.FILL] + counts[model.CHANGE]
    message = (u"Write {0} value(s)?\n\n{1} blank(s) filled, {2} "
               u"existing value(s) changed.".format(
                   total, counts[model.FILL], counts[model.CHANGE]))

    if not counts[model.CHANGE]:
        return confirm(message, "Write the values"), False

    message += (u"\n\nThe {0} change(s) are listed in the report above "
                u"with their old and new values. Anything typed by hand "
                u"there will be replaced.".format(counts[model.CHANGE]))
    try:
        answer = forms.alert(
            message, title=TITLE,
            options=["Fill blanks and apply changes",
                     "Fill blanks only",
                     "Cancel"])
    except BaseException:
        return False, False

    if answer == "Fill blanks and apply changes":
        return True, True
    if answer == "Fill blanks only":
        return True, False
    return False, False


def run():
    doc = revit.doc

    levels_wanted = choose_levels(doc)
    entries, missing_target = plan(doc, levels_wanted)

    if not entries:
        forms.alert("No furniture, casework, doors or equipment found "
                    "on the levels you picked.", title=TITLE)
        return

    counts = report(entries, missing_target)

    if not counts[model.FILL] and not counts[model.CHANGE]:
        output.print_md("Nothing to write.")
        return

    go_ahead, include_changes = ask_to_write(counts)
    if not go_ahead:
        output.print_md("Cancelled, nothing was written.")
        return

    written, failed = apply_plan(doc, entries, include_changes)

    output.print_md("# Result")
    output.print_md("{0} value(s) written.".format(written))
    for entry, error in failed:
        if entry is None:
            output.print_md("* {0}".format(error))
            continue
        output.print_md("* {0} {1} failed: {2}".format(
            output.linkify(entry.element_id), entry.description, error))
    if not written:
        output.print_md("Nothing was written, so it was rolled back.")


if __name__ == "__main__":
    run()
