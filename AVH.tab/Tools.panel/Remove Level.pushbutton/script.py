# -*- coding: utf-8 -*-
"""Move every element off one or more levels, then delete those levels.

Rewritten from "Remove Level Safely BETA" in the pyApex pyRevit extension
(https://apex-project.github.io/pyApex). Almost none of the original
survives, because three separate things in it were wrong.

**The offset arithmetic.** It wrote the element's absolute elevation into
the offset parameter without subtracting the target level's elevation, so
every element landed high by exactly that elevation. Correct only for a
target level at 0.00, which its own transaction name, 'Change level to 0',
admitted. Fixed in `avh_levels.model.new_offset` and pinned by
`test_level_move.py`, which fails 16 checks if the subtraction is dropped.

**The unit handling.** It read elevations with `AsValueString()` and
parsed them with `float()`, then wrote back with
`SetValueString(str(value))`. AVH's Revit formats with a decimal comma, so
the read raises and the write pushes a period decimal into a comma locale.
Nothing here formats or parses a number: elevations come from
`Level.Elevation` and offsets from `AsDouble` / `Set`, all doubles in
internal units.

**The pickers.** Its option objects carried a `.state` flag, the
`SelectFromCheckBoxes` interface, but it called `SelectFromList`, which
returns only ticked items and never touches `.state`. On current pyRevit
it selected nothing and exited.

Behaviours deliberately different from the original:

- Nothing is written until a dry run has been printed and confirmed.
- The dialogs are load bearing. The schedule export treats a failed dialog
  as cosmetic and exports anyway, which is right for a tool that only
  writes a file. This one edits the model, so any UI failure aborts.
- An element whose level was changed but whose offset could not then be
  written is inconsistent, so that rolls the whole run back.

## How an element is classified, and why it is not a lookup table

The first version carried a hardcoded table of BuiltInParameter names.
Against a real model it reported 116 dependents on a level and could move
none of them, because the one piece of model content there was a railing,
whose level parameter was not in the table. Guessing more enum names would
only have moved the gap to the next element type.

So the level parameter is found by scanning: any writable parameter on the
element whose storage type is ElementId and whose current value is the
level being cleared. That is general, needs no table, and works for shared
and project parameters as well as built in ones.

The offset still needs a table, because knowing which offset parameter
belongs to which level parameter is semantic and cannot be derived. When
the pair is recognised the element moves and keeps its exact elevation.
When it is not, the element is classed as a shift, held back unless
explicitly opted into, and **the report names the parameters that were
found** so the pair can be added from evidence rather than invented here.

That same run showed most dependents were never candidates: floor plans,
viewports, sun paths, work plane grids, extent elements and unnamed sub
objects. They are counted as collateral rather than listed as failures,
and they do not block deletion, because they exist only because the level
does.

Still unverified: the offset pairs below marked as unconfirmed, and
whether a level parameter found by the scan is always safe to write.
"""

__title__ = "Remove Level\n(BETA)"
__author__ = "AVH"
__doc__ = ("Move all elements off the levels you pick onto a target "
           "level, keeping them at the same absolute elevation, then "
           "offer to delete the emptied levels. Prints a dry run and "
           "asks before writing anything.")

import os
import sys

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_levels import model                      # noqa: E402
# compat is generic rather than schedule specific, and it is already
# proven and covered by the IronPython suite, so it is reused rather than
# duplicated here.
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = __title__.replace("\n", " ")

MAX_LISTED = 25

# Which offset parameter belongs to which level constraint. Only used
# once the scan has already found the constraint, so a name that does not
# exist in this Revit simply never matches.
#
# Confirmed against a real model: railings and stairs, whose reported
# before and after offsets round trip to the same absolute elevation.
# Confirmed by use: walls, family instances, structural framing, rooms.
# Still unconfirmed, from the API docs: floors and roofs.
#
# **Top constraints belong here too.** An element can be constrained to
# the same level twice, base and top. The first version had only base
# entries, so a room's upper limit came back unpaired, went into the opt
# in shift group, and moving it dropped the limit below the room's own
# base. A top constraint with its offset corrected is as safe as a base
# one; a top constraint without is worse than a base one, because it
# inverts rather than translates.
LEVEL_OFFSET_PAIRS = (
    ("WALL_BASE_CONSTRAINT", "WALL_BASE_OFFSET"),
    ("WALL_HEIGHT_TYPE", "WALL_TOP_OFFSET"),
    ("FAMILY_LEVEL_PARAM", "FAMILY_BASE_LEVEL_OFFSET_PARAM"),
    ("FAMILY_LEVEL_PARAM", "INSTANCE_FREE_HOST_OFFSET_PARAM"),
    ("SCHEDULE_LEVEL_PARAM", "INSTANCE_FREE_HOST_OFFSET_PARAM"),
    ("ROOM_LEVEL_ID", "ROOM_LOWER_OFFSET"),
    ("ROOM_UPPER_LEVEL", "ROOM_UPPER_OFFSET"),
    ("LEVEL_PARAM", "FLOOR_HEIGHTABOVELEVEL_PARAM"),
    ("ROOF_BASE_LEVEL_PARAM", "ROOF_LEVEL_OFFSET_PARAM"),
    ("STAIRS_BASE_LEVEL_PARAM", "STAIRS_BASE_OFFSET"),
    ("STAIRS_TOP_LEVEL_PARAM", "STAIRS_TOP_OFFSET"),
    ("STAIRS_RAILING_BASE_LEVEL_PARAM", "STAIRS_RAILING_HEIGHT_OFFSET"),
)


def bip(name):
    """A BuiltInParameter by name, or None if this Revit lacks it."""
    return getattr(DB.BuiltInParameter, name, None)


def eid_value(element_id):
    """An ElementId as a plain number, across Revit versions.

    Revit 2024 added `ElementId.Value` as an Int64 and marked
    `IntegerValue` obsolete. Both exist on 2025, only one will on some
    later release. The original used `IntegerValue` throughout.
    """
    if element_id is None:
        return None
    value = getattr(element_id, "Value", None)
    if value is not None:
        return value
    return element_id.IntegerValue


def describe(element):
    """A readable label for an element. Never raises.

    Accessing `.Name` throws on several Revit element types and the
    category can be absent, so both are guarded individually.
    """
    if element is None:
        return u"unknown element"
    parts = []
    try:
        category = element.Category
        if category is not None:
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


def is_collateral(element):
    """True for things that were never on a level to begin with.

    Views, viewports, anything owned by a view, element types and
    internal sub objects with no category. Revit lists them as dependents
    of a level because they exist because of it, not because they sit on
    it. They cannot be moved and they must not count as blockers, or no
    level is ever deletable.
    """
    try:
        if isinstance(element, DB.View):
            return True
    except BaseException:
        pass
    # A sketch plane holds no geometry anyone can see. Once the curves
    # that sat on it have been rehosted it is dead weight, and leaving it
    # classed as a blocker means rehosting frees the curve and still does
    # not free the level. Anything else that still uses it turns up as a
    # blocker in its own right, so nothing is hidden by this.
    for type_name in ("Viewport", "ElementType", "SketchPlane"):
        cls = getattr(DB, type_name, None)
        if cls is not None:
            try:
                if isinstance(element, cls):
                    return True
            except BaseException:
                pass
    try:
        if getattr(element, "ViewSpecific", False):
            return True
    except BaseException:
        pass
    try:
        owner = getattr(element, "OwnerViewId", None)
        invalid = eid_value(getattr(DB.ElementId, "InvalidElementId", None))
        if owner is not None and eid_value(owner) != invalid:
            return True
    except BaseException:
        pass
    try:
        if element.Category is None:
            return True
    except BaseException:
        return True
    return False


def param_name(param):
    """A stable name for a parameter, preferring the BuiltInParameter.

    The built in name is what would go into LEVEL_OFFSET_PAIRS, so that
    is what the diagnostics report. The user facing definition name is
    localised and no use for that purpose.
    """
    try:
        definition = param.Definition
    except BaseException:
        return u"unknown parameter"
    enum = getattr(definition, "BuiltInParameter", None)
    invalid = bip("INVALID")
    if enum is not None and not (invalid is not None and enum == invalid):
        return to_text(enum)
    try:
        return u"(shared) " + to_text(definition.Name)
    except BaseException:
        return u"unknown parameter"


def all_params(element):
    """Every instance parameter, never raising."""
    try:
        return [param for param in element.Parameters]
    except BaseException:
        return []


def writable_params(element):
    """Every writable instance parameter, never raising."""
    out = []
    for param in all_params(element):
        try:
            if not param.IsReadOnly:
                out.append(param)
        except BaseException:
            continue
    return out


def points_at(param, level_id):
    """True if this is an ElementId parameter holding this level."""
    try:
        if param.StorageType != DB.StorageType.ElementId:
            return False
        return eid_value(param.AsElementId()) == eid_value(level_id)
    except BaseException:
        return False


def level_params_pointing_at(element, level_id):
    """Writable ElementId parameters whose value is this level.

    This replaces the hardcoded table the first version used. An element
    can have more than one, a wall or stair constrained both base and top
    to the same level being the obvious case, and every one of them has
    to be handled.
    """
    return [param for param in writable_params(element)
            if points_at(param, level_id)]


def locked_params_pointing_at(element, level_id):
    """Read only parameters still holding this level.

    An element with one of these can never be freed, so touching whatever
    else it has is churn that changes geometry for no gain.

    A room is the case that made this necessary. Its base level is read
    only while its upper limit is writable, so the scan happily repointed
    the limit and left the room with its top below its bottom, and the
    level was still blocked afterwards because the base had not moved.
    """
    out = []
    for param in all_params(element):
        try:
            if not param.IsReadOnly:
                continue
        except BaseException:
            continue
        if points_at(param, level_id):
            out.append(param)
    return out


def offset_for(element, level_name):
    """The offset parameter paired with this level constraint, or None."""
    for candidate_level, offset_name in LEVEL_OFFSET_PAIRS:
        if candidate_level != level_name:
            continue
        enum = bip(offset_name)
        if enum is None:
            continue
        try:
            candidate = element.get_Parameter(enum)
            if candidate is None or candidate.IsReadOnly:
                continue
            if candidate.StorageType != DB.StorageType.Double:
                continue
        except BaseException:
            continue
        return candidate
    return None


def pair_constraints(element, level_params):
    """Pair every level constraint with its own offset.

    Returns (pairs, unpaired), where pairs is a list of
    (level_param, offset_param, name).

    Every constraint pointing at the level has to be paired, not just the
    first one recognised. The first version repointed all of them and
    corrected a single offset, which on an element constrained twice
    silently moved the constraint it had not paired.
    """
    pairs = []
    unpaired = []
    for param in level_params:
        name = param_name(param)
        offset_param = offset_for(element, name)
        if offset_param is None:
            unpaired.append(name)
        else:
            pairs.append((param, offset_param, name))
    return pairs, sorted(set(unpaired))


def sketch_plane_of(element):
    """The sketch plane a curve sits on, or None.

    Only curve elements are considered. A floor's boundary sketch also
    has a plane, but it belongs to the floor and is not ours to move.
    """
    curve_class = getattr(DB, "CurveElement", None)
    if curve_class is None:
        return None
    try:
        if not isinstance(element, curve_class):
            return None
        return element.SketchPlane
    except BaseException:
        return None


def can_rehost(element, doomed_ids):
    """True if this element is tied to the level only by its work plane.

    `doomed_ids` is the set the probe said would be deleted with the
    level. If the element's own sketch plane is in that set, the plane is
    what binds it, and an equivalent plane that is not derived from a
    level frees the element without moving it.

    Checking membership rather than inspecting the plane means no
    guessing about how a sketch plane records its level: Revit has
    already told us what dies with the level.
    """
    plane = sketch_plane_of(element)
    if plane is None:
        return False
    try:
        return eid_value(plane.Id) in doomed_ids
    except BaseException:
        return False


def rehost_curve(doc, element, cache):
    """Give a curve an equivalent plane that no level owns.

    Returns a short description of what happened. The new plane is built
    from the old one's own normal and origin, so the curve does not move.
    Planes are cached per run, because a level's worth of model lines
    would otherwise leave a scatter of identical sketch planes behind.
    """
    old = sketch_plane_of(element)
    if old is None:
        raise RuntimeError("no sketch plane to replace")

    geometry = old.GetPlane()
    normal = geometry.Normal
    origin = geometry.Origin
    key = (round(normal.X, 9), round(normal.Y, 9), round(normal.Z, 9),
           round(origin.X, 9), round(origin.Y, 9), round(origin.Z, 9))

    if key not in cache:
        maker = getattr(DB.Plane, "CreateByNormalAndOrigin", None)
        if maker is None:
            raise RuntimeError(
                "this Revit has no Plane.CreateByNormalAndOrigin")
        cache[key] = DB.SketchPlane.Create(doc, maker(normal, origin))

    element.SketchPlane = cache[key]
    return u"replaced its work plane with a free one at the same origin"


# How many enclosed areas on the target level to try before giving up.
# Sorted by distance from the room's own position, so the right one is
# almost always first and this is a backstop, not a search.
MAX_CIRCUIT_TRIES = 12

# Room location tolerance in internal units, about 0.3 mm.
PLACE_TOLERANCE = 0.001


def room_class():
    """Autodesk.Revit.DB.Architecture.Room, or None if unreachable."""
    architecture = getattr(DB, "Architecture", None)
    if architecture is not None:
        found = getattr(architecture, "Room", None)
        if found is not None:
            return found
    try:
        from Autodesk.Revit.DB.Architecture import Room
        return Room
    except BaseException:
        return None


def is_room(element):
    cls = room_class()
    if cls is None:
        return False
    try:
        return isinstance(element, cls)
    except BaseException:
        return False


def commit_checked(transaction):
    """Commit, and say whether Revit actually kept it.

    `Transaction.Commit` returns a `TransactionStatus`. Revit's failure
    handling can roll a transaction back *during* the commit, when an
    error rather than a warning is raised, and the call then returns
    RolledBack instead of Committed.

    The first version ignored that return value and reported a room as
    moved that Revit had thrown away, three lines above reporting the
    same room as still on the level being deleted. This is precisely the
    defect this tool was written to fix: the original pyApex script
    discarded the bool from `SetValueString` and so wrote values Revit
    had silently rejected. **Never ignore what a write returns.**
    """
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is None:
        return True
    return status == committed


class Failures(object):
    """Holds what Revit objected to, so the report can quote it."""

    def __init__(self):
        self.errors = []


def collect_failures(transaction):
    """Swallow warnings, record errors, roll back on an error.

    Warnings during a room move are noise: unplacing one always raises
    them. Errors are not, and an error left to Revit's own handling stops
    the run with a modal dialog in the middle of a transaction, which a
    batch tool must never do. Collecting the text and rolling back turns
    a blocking dialog into a line in the report.

    Returns a `Failures`, or None if this Revit will not take a
    preprocessor, in which case the dialogs appear as they did before.
    The class is built here rather than at module level because it has to
    inherit from a .NET interface, and mixing that with a plain base
    class breaks the method resolution order.
    """
    record = Failures()
    try:
        warning = DB.FailureSeverity.Warning
        rollback = DB.FailureProcessingResult.ProceedWithRollBack
        proceed = DB.FailureProcessingResult.Continue

        class Collector(DB.IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                stop = False
                try:
                    messages = accessor.GetFailureMessages()
                except BaseException:
                    return proceed
                for failure in messages:
                    try:
                        severity = failure.GetSeverity()
                    except BaseException:
                        continue
                    if severity == warning:
                        try:
                            accessor.DeleteWarning(failure)
                        except BaseException:
                            pass
                        continue
                    try:
                        record.errors.append(
                            to_text(failure.GetDescriptionText()))
                    except BaseException:
                        record.errors.append(u"an unnamed Revit error")
                    stop = True
                return rollback if stop else proceed

        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(Collector())
        options.SetClearAfterRollback(True)
        try:
            options.SetForcedModalHandling(False)
        except BaseException:
            pass
        transaction.SetFailureHandlingOptions(options)
        return record
    except BaseException:
        return None


def swallow_warnings(transaction):
    """Suppress the warnings unplacing a room raises.

    Taking a room out of the model posts "There are identical instances
    in the same place" and "Room is not in a properly enclosed region"
    style warnings. Left alone they stop the run with a dialog in the
    middle of a transaction, which is exactly what a batch tool must not
    do. Failing to install the handler is not fatal: the warnings then
    appear, which is ugly but not wrong.
    """
    try:
        severity = DB.FailureSeverity.Warning

        class Swallower(DB.IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                for failure in accessor.GetFailureMessages():
                    if failure.GetSeverity() == severity:
                        accessor.DeleteWarning(failure)
                return DB.FailureProcessingResult.Continue

        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(Swallower())
        options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
        return True
    except BaseException:
        return False


def circuit_distance(circuit, x, y):
    """Plan distance from a circuit's interior point to a coordinate."""
    try:
        point = circuit.GetPointInside()
    except BaseException:
        return float("inf")
    px = getattr(point, "U", None)
    py = getattr(point, "V", None)
    if px is None:
        px = getattr(point, "X", None)
        py = getattr(point, "Y", None)
    if px is None or py is None:
        return float("inf")
    return ((px - x) ** 2 + (py - y) ** 2) ** 0.5


def free_circuits(doc, level, x, y):
    """Enclosed areas on a level with no room in them, nearest first."""
    try:
        topology = doc.get_PlanTopology(level)
    except BaseException as exc:
        raise RuntimeError(
            "could not read the target level's plan topology: "
            + to_text(exc))
    out = []
    try:
        for circuit in topology.Circuits:
            try:
                if circuit.IsRoomLocated:
                    continue
            except BaseException:
                continue
            out.append(circuit)
    except BaseException as exc:
        raise RuntimeError("could not list enclosed areas: " + to_text(exc))
    out.sort(key=lambda c: circuit_distance(c, x, y))
    return out


def room_point(room):
    location = getattr(room, "Location", None)
    return getattr(location, "Point", None)


def settle_room(doc, room, x, y):
    """Nudge a freshly placed room back to its own coordinates.

    Returns True if it is still enclosed afterwards. A room dragged
    outside the area that encloses it reports zero area, which is the
    check that tells a real placement from a nominal one.
    """
    point = room_point(room)
    if point is None:
        return False
    delta = DB.XYZ(x - point.X, y - point.Y, 0.0)
    try:
        if delta.GetLength() > PLACE_TOLERANCE:
            DB.ElementTransformUtils.MoveElement(doc, room.Id, delta)
        doc.Regenerate()
        return room.Area > 0
    except BaseException:
        return False


def recreate_room(doc, room, source_level, target_level):
    """Take a room off one level and put it back on another.

    The only route there is. A room's level parameter is read only, so
    this is the API form of cut and paste, except that placing the same
    room element into a new circuit keeps its number and every other
    parameter, which a copy does not.

    Raises with a readable reason on any failure, so the caller can
    report it and carry on.
    """
    point = room_point(room)
    if point is None:
        raise RuntimeError("it is already unplaced, so there is nothing "
                           "to move")
    original_x, original_y = point.X, point.Y
    limits = read_room_limits(doc, room, source_level)
    base_offset, upper_on_source, height, upper_elev, upper_off = limits

    if height <= 0:
        raise RuntimeError(
            "its height already reads as {0:.1f} mm".format(
                model.feet_to_mm(height)))

    # Revit validates room height on commit, and a rollback never
    # commits, so the feasibility probe cannot see this one. Arithmetic
    # can, and needs no Revit at all.
    after = model.room_height_after(
        source_level.Elevation, target_level.Elevation, base_offset,
        upper_elev, upper_off, upper_on_source)
    if after <= 0:
        raise RuntimeError(
            "on the target level its height would be {0:.1f} mm, because "
            "its upper limit is anchored to a level that is not "
            "moving. Revit rejects a room with no height".format(
                model.feet_to_mm(after)))

    circuits = free_circuits(doc, target_level, original_x, original_y)
    if not circuits:
        raise RuntimeError(
            "the target level has no enclosed area free to receive it. "
            "Rooms need bounding geometry where they land")

    try:
        room.Unplace()
    except BaseException as exc:
        raise RuntimeError("could not unplace it: " + to_text(exc))

    creator = getattr(doc, "Create", None)
    if creator is None:
        raise RuntimeError("this document exposes no Create factory")

    tried = 0
    refusals = []
    for circuit in circuits[:MAX_CIRCUIT_TRIES]:
        tried += 1
        try:
            placed = creator.NewRoom(room, circuit)
        except BaseException as exc:
            # Swallowing these lost the real reason: the first version
            # reported "none of them held it at its own coordinates"
            # when what actually happened was that every circuit refused
            # outright, which is a different problem with a different
            # fix.
            refusals.append(to_text(exc))
            continue
        if placed is None:
            refusals.append(u"Revit returned no room")
            continue
        if settle_room(doc, placed, original_x, original_y):
            restore_room_height(placed, limits, target_level)
            return placed
        refusals.append(u"it would not stay enclosed at its own "
                        u"coordinates")
        try:
            placed.Unplace()
        except BaseException:
            pass

    detail = u""
    if refusals:
        unique = []
        for reason in refusals:
            if reason not in unique:
                unique.append(reason)
        detail = u". " + u"; ".join(unique[:3])
    raise RuntimeError(
        u"tried {0} enclosed area(s) on the target level and none of "
        u"them worked{1}".format(tried, detail))


def param_double(element, name, default=0.0):
    enum = bip(name)
    if enum is None:
        return default
    try:
        param = element.get_Parameter(enum)
        if param is None:
            return default
        return param.AsDouble()
    except BaseException:
        return default


def param_level_id(element, name):
    enum = bip(name)
    if enum is None:
        return None
    try:
        param = element.get_Parameter(enum)
        if param is None:
            return None
        return param.AsElementId()
    except BaseException:
        return None


def read_room_limits(doc, room, source_level):
    """What has to survive the move: the room's height.

    A room's top is anchored to a level plus an offset, so repointing
    that level alone moves the ceiling without moving the floor. The
    first version did exactly that. It dropped a room's upper limit from
    a level at 11740 mm to one at 8000 mm while the floor had only just
    arrived at 8000, and Revit refused the whole transaction with
    "Room must have a height greater than 0".

    Returns (base_offset, upper_on_source, height).
    """
    base_offset = param_double(room, "ROOM_LOWER_OFFSET")
    upper_offset = param_double(room, "ROOM_UPPER_OFFSET")
    upper_id = param_level_id(room, "ROOM_UPPER_LEVEL")

    upper_elevation = source_level.Elevation
    upper_on_source = False
    if upper_id is not None:
        if eid_value(upper_id) == eid_value(source_level.Id):
            upper_on_source = True
        else:
            other = doc.GetElement(upper_id)
            found = getattr(other, "Elevation", None)
            if found is not None:
                upper_elevation = found

    height = ((upper_elevation + upper_offset)
              - (source_level.Elevation + base_offset))
    return base_offset, upper_on_source, height, upper_elevation,\
        upper_offset


def restore_room_height(room, limits, target_level):
    """Bring the upper limit across, keeping the room the same height.

    Only touched when the limit was anchored to the level being cleared.
    A room whose ceiling hangs off some other level is left alone: its
    top is not this level's business, and it does not block the delete.
    """
    base_offset, upper_on_source, height = limits[0], limits[1], limits[2]
    if not upper_on_source:
        return u""
    if height <= 0:
        raise RuntimeError(
            "its height reads as {0:.1f} mm, so there is nothing sane to "
            "preserve".format(model.feet_to_mm(height)))

    enum_level = bip("ROOM_UPPER_LEVEL")
    enum_offset = bip("ROOM_UPPER_OFFSET")
    if enum_level is None or enum_offset is None:
        raise RuntimeError("this Revit has no room upper limit parameters")

    level_param = room.get_Parameter(enum_level)
    offset_param = room.get_Parameter(enum_offset)
    if level_param is None or offset_param is None:
        raise RuntimeError("the room has no upper limit to bring across")

    level_param.Set(target_level.Id)
    offset_param.Set(base_offset + height)
    return u"upper limit brought across, height kept at {0:.1f} mm".format(
        model.feet_to_mm(height))


def probe_dependents(doc, level):
    """Element ids Revit would delete along with this level.

    Deleting inside a transaction and rolling it back is the only
    reliable way to enumerate dependents. The ids stay valid afterwards
    because the rollback restores the elements.
    """
    transaction = DB.Transaction(doc, "AVH probe level dependents")
    transaction.Start()
    try:
        ids = doc.Delete(level.Id)
    finally:
        transaction.RollBack()
    if not ids:
        return []
    return [element_id for element_id in ids]


def classify(doc, element_id, source_level, target_level,
             doomed_ids):
    """Work out what can be done with one dependent. Writes nothing."""
    element = doc.GetElement(element_id)
    if element is None:
        return model.PlanEntry(element_id, model.COLLATERAL,
                               u"unknown element",
                               reason=u"no longer in the model")

    label = describe(element)

    if isinstance(element, DB.Level):
        return model.PlanEntry(element_id, model.COLLATERAL, label,
                               reason=u"another level")

    if is_collateral(element):
        return model.PlanEntry(element_id, model.COLLATERAL, label,
                               reason=u"lives inside a view, or is an "
                                      u"internal sub object")

    locked = locked_params_pointing_at(element, source_level.Id)
    if locked:
        names = u", ".join(sorted(set(param_name(p) for p in locked)))
        if is_room(element):
            # The one read only case with a way out. Feasibility is
            # settled by probe_recreate, not by this guess.
            return model.PlanEntry(
                element_id, model.RECREATE, label,
                target_level_id=target_level.Id,
                reason=u"a room's level is read only, so it has to be "
                       u"taken off this level and placed again on the "
                       u"target. It keeps its number, and unlike "
                       u"everything else here it really does change "
                       u"elevation",
                diagnostics=u"read only: " + names)
        return model.PlanEntry(
            element_id, model.SKIP, label,
            reason=u"held to this level by a read only parameter, so it "
                   u"cannot be freed no matter what else is moved",
            diagnostics=u"read only: " + names)

    level_params = level_params_pointing_at(element, source_level.Id)
    if not level_params:
        if can_rehost(element, doomed_ids):
            return model.PlanEntry(
                element_id, model.REHOST, label,
                reason=u"held only by a work plane that belongs to this "
                       u"level, so it can be given an equivalent plane "
                       u"that is not tied to one",
                diagnostics=summarise_params(element))
        return model.PlanEntry(
            element_id, model.SKIP, label,
            reason=u"nothing on it points at this level, so it is "
                   u"probably hosted on a work plane rather than a "
                   u"level, or it follows a parent that does",
            diagnostics=summarise_params(element))

    pairs, unpaired = pair_constraints(element, level_params)

    if unpaired:
        # Every constraint or none. Repointing the paired ones and
        # leaving an unpaired one behind would split the element across
        # two levels, which is worse than not touching it.
        return model.PlanEntry(
            element_id, model.SHIFT, label,
            writes=[model.LevelWrite(eid_value(p.Id), label=param_name(p))
                    for p in level_params],
            target_level_id=target_level.Id,
            reason=u"constraint with no offset paired to it: "
                   + u", ".join(unpaired)
                   + u". Repointing it moves the element, and if it is a "
                     u"top or upper constraint that can put it below its "
                     u"own base",
            diagnostics=summarise_params(element, doubles_only=True))

    writes = []
    for level_param, offset_param, name in pairs:
        try:
            current = offset_param.AsDouble()
        except BaseException as exc:
            return model.PlanEntry(
                element_id, model.SKIP, label,
                reason=u"could not read the offset for {0}: {1}".format(
                    name, to_text(exc)))
        writes.append(model.LevelWrite(
            eid_value(level_param.Id),
            offset_param_id=eid_value(offset_param.Id),
            current_offset=current,
            target_offset=model.new_offset(
                source_level.Elevation, current, target_level.Elevation),
            label=name))

    return model.PlanEntry(
        element_id, model.MOVE, label, writes=writes,
        target_level_id=target_level.Id)


def summarise_params(element, doubles_only=False):
    """Which writable parameters an element has, for the report.

    This is the feedback loop. An element the tool cannot act on says
    what it does have, so the next version of LEVEL_OFFSET_PAIRS comes
    from a real model rather than from guessing enum names.
    """
    names = []
    for param in writable_params(element):
        try:
            storage = param.StorageType
            if doubles_only and storage != DB.StorageType.Double:
                continue
            if not doubles_only \
                    and storage != DB.StorageType.ElementId:
                continue
        except BaseException:
            continue
        names.append(param_name(param))
    if not names:
        return u"no writable {0} parameters".format(
            u"length" if doubles_only else u"element id")
    kind = u"lengths" if doubles_only else u"element ids"
    return u"writable {0}: {1}".format(
        kind, u", ".join(sorted(set(names))[:8]))


def plan_for_level(doc, source_level, target_level):
    """Classify everything hanging off one level. Writes nothing."""
    entries = []
    source_id = eid_value(source_level.Id)
    dependents = probe_dependents(doc, source_level)
    doomed_ids = set(eid_value(i) for i in dependents)
    for element_id in dependents:
        if eid_value(element_id) == source_id:
            continue
        entries.append(classify(doc, element_id, source_level,
                                target_level, doomed_ids))
    return entries


def probe_rehost(doc, plans):
    """Try every rehost for real, in a transaction that is thrown away.

    Whether a curve can be given a new plane cannot be told by looking at
    it. A sketch line belonging to a stair is a CurveElement sitting on a
    plane that dies with the level, exactly like a free model line, and
    the first version offered five of them and failed all five with
    "The curve belongs to a sketch-based element, and cannot be modified
    independently".

    Revit knows the answer, so ask it. The tool already probes the level
    by deleting it and rolling back; this is the same trick applied to
    the operation itself, and it turns the dry run's rehost count from a
    guess into a measurement.

    Entries that cannot be rehosted are demoted to stuck, carrying
    Revit's own message as the reason.
    """
    candidates = []
    for _source_level, entries in plans:
        candidates.extend(model.rehostable(entries))
    if not candidates:
        return

    cache = {}
    transaction = DB.Transaction(doc, "AVH probe rehost")
    transaction.Start()
    try:
        for entry in candidates:
            element = doc.GetElement(entry.element_id)
            if element is None:
                entry.action = model.SKIP
                entry.reason = u"no longer in the model"
                continue
            try:
                rehost_curve(doc, element, cache)
            except BaseException as exc:
                entry.action = model.SKIP
                entry.reason = (u"its work plane cannot be replaced: "
                                + to_text(exc))
    finally:
        transaction.RollBack()


def each_recreatable(plans):
    out = []
    for source_level, entries in plans:
        for entry in model.recreatable(entries):
            out.append((source_level, entry))
    return out


def probe_recreate(doc, plans, target_level):
    """Try every room move for real, in a transaction that is discarded.

    None of this can be told by inspection. Whether the target level has
    an enclosed area free where the room needs to land depends entirely
    on the model, and the honest way to find out is to do it and look.
    Rooms that cannot be moved are demoted to stuck carrying the reason.
    """
    candidates = each_recreatable(plans)
    if not candidates:
        return

    transaction = DB.Transaction(doc, "AVH probe room moves")
    transaction.Start()
    collector = collect_failures(transaction)
    try:
        for source_level, entry in candidates:
            room = doc.GetElement(entry.element_id)
            if room is None:
                entry.action = model.SKIP
                entry.reason = u"no longer in the model"
                continue
            try:
                recreate_room(doc, room, source_level, target_level)
            except BaseException as exc:
                entry.action = model.SKIP
                entry.reason = (u"cannot be moved to the target level: "
                                + to_text(exc))
    finally:
        transaction.RollBack()

    # An error Revit raised during the probe, rather than one raised by
    # the calls themselves. "Room must have a height greater than 0"
    # arrives this way, and the first version never saw it.
    if collector is not None and collector.errors:
        detail = u"; ".join(collector.errors[:3])
        for _source_level, entry in candidates:
            if entry.action == model.RECREATE:
                entry.action = model.SKIP
                entry.reason = (u"Revit refused it: " + detail)


def apply_recreate(doc, plans, target_level):
    """Move the rooms. Separate transaction, separate undo step."""
    done = 0
    failed = []

    transaction = DB.Transaction(doc, "AVH move rooms to another level")
    transaction.Start()
    collector = collect_failures(transaction)
    try:
        for source_level, entry in each_recreatable(plans):
            room = doc.GetElement(entry.element_id)
            if room is None:
                failed.append((entry, u"no longer in the model"))
                continue
            try:
                recreate_room(doc, room, source_level, target_level)
                done += 1
            except BaseException as exc:
                failed.append((entry, to_text(exc)))
    except BaseException:
        transaction.RollBack()
        raise

    # A room left unplaced by a half finished attempt is worse than a
    # room that never moved, so a failure takes the whole step with it.
    if failed or not done:
        transaction.RollBack()
        done = 0
    elif not commit_checked(transaction):
        detail = u"Revit rolled it back during the commit"
        if collector is not None and collector.errors:
            detail += u": " + u"; ".join(collector.errors[:3])
        failed.append((None, detail))
        done = 0

    return done, failed


def report_group(entries, action, heading, show_diagnostics=False):
    """List one class of entry, capped so a big model stays readable."""
    group = [e for e in entries if e.action == action]
    if not group:
        return
    output.print_md(heading.format(len(group)))
    for entry in group[:MAX_LISTED]:
        line = "* {0} {1}, {2}".format(
            output.linkify(entry.element_id),
            entry.description, entry.reason)
        if show_diagnostics and entry.diagnostics:
            line += "  \n  `{0}`".format(entry.diagnostics)
        output.print_md(line)
    if len(group) > MAX_LISTED:
        output.print_md("* and {0} more".format(len(group) - MAX_LISTED))


def report_plan(plans, target_level):
    """Print the dry run. Returns the combined counts."""
    output.print_md("# Remove Level, dry run")
    output.print_md("Target level: **{0}** at {1:.1f} mm".format(
        to_text(target_level.Name),
        model.feet_to_mm(target_level.Elevation)))
    output.print_md("Nothing has been written to the model yet.")

    totals = model.empty_counts()

    for source_level, entries in plans:
        counts = model.summarise(entries)
        for key in totals:
            totals[key] += counts[key]

        output.print_md("## {0} at {1:.1f} mm".format(
            to_text(source_level.Name),
            model.feet_to_mm(source_level.Elevation)))
        output.print_md(
            "{0} to move, {1} would shift, {2} to rehost, {3} room(s) "
            "to re-place, {4} stuck, {5} views and internals that go "
            "with the level".format(
                counts[model.MOVE], counts[model.SHIFT],
                counts[model.REHOST], counts[model.RECREATE],
                counts[model.SKIP], counts[model.COLLATERAL]))

        for entry in entries:
            if entry.action != model.MOVE:
                continue
            output.print_md("* {0} {1}".format(
                output.linkify(entry.element_id), entry.description))
            for write in entry.writes:
                output.print_md(
                    "    * `{0}` offset {1:.1f} mm becomes "
                    "{2:.1f} mm".format(
                        write.label,
                        model.feet_to_mm(write.current_offset),
                        model.feet_to_mm(write.target_offset)))

        report_group(entries, model.SHIFT,
                     "**Would physically move ({0})**", True)
        report_group(entries, model.RECREATE,
                     "**Rooms, which have to be taken off and placed "
                     "again ({0})**  \nThey keep their number. They do "
                     "**not** keep their elevation: a room sits at its "
                     "level.", True)
        report_group(entries, model.REHOST,
                     "**Can be freed by replacing their work plane "
                     "({0})**  \nThey do not move; they get an "
                     "equivalent plane that no level owns.", True)
        report_group(entries, model.SKIP,
                     "**Stuck ({0})**  \nSome of these are sub parts of "
                     "elements above and free themselves once the "
                     "parent moves. The delete check re-runs afterwards "
                     "and reports what is really left.", True)

    return totals


def confirm(message, yes_label):
    """Blocking yes or no. True only on an explicit yes.

    Any failure returns False. This tool writes to the model, so a broken
    dialog has to stop the run rather than fall through to the work.
    """
    try:
        answer = forms.alert(message, title=TITLE,
                             options=[yes_label, "Cancel"])
    except BaseException:
        return False
    return answer == yes_label


def params_by_ids(element, wanted):
    """Re-fetch parameters by id, rather than holding them across a
    rollback and two dialogs."""
    out = []
    for param in writable_params(element):
        try:
            if eid_value(param.Id) in wanted:
                out.append(param)
        except BaseException:
            continue
    return out


def apply_plan(doc, plans, include_shift):
    """Write the moves in one transaction, so one undo step reverses it.

    Returns (moved, skipped, partial). A `partial` entry had its level
    changed but could not have its offset written, which leaves it
    somewhere it should not be. Any of those rolls the whole run back: a
    reported failure is recoverable, a silently relocated element is not.
    """
    moved = 0
    skipped = []
    partial = []

    transaction = DB.Transaction(doc, "AVH move elements off levels")
    transaction.Start()
    try:
        for _source_level, entries in plans:
            for entry in model.actionable(entries, include_shift):
                element = doc.GetElement(entry.element_id)
                if element is None:
                    skipped.append((entry, u"no longer in the model"))
                    continue

                wanted = set()
                for write in entry.writes:
                    wanted.add(write.level_param_id)
                    if write.offset_param_id is not None:
                        wanted.add(write.offset_param_id)
                found = {}
                for param in params_by_ids(element, wanted):
                    found[eid_value(param.Id)] = param

                if len(found) != len(wanted):
                    skipped.append(
                        (entry, u"parameters are no longer writable"))
                    continue

                written = 0
                try:
                    for write in entry.writes:
                        found[write.level_param_id].Set(
                            entry.target_level_id)
                        written += 1
                        if write.offset_param_id is not None:
                            found[write.offset_param_id].Set(
                                write.target_offset)
                    moved += 1
                except BaseException as exc:
                    if written:
                        partial.append((entry, to_text(exc)))
                    else:
                        skipped.append((entry, to_text(exc)))
    except BaseException:
        transaction.RollBack()
        raise

    if partial or not moved:
        transaction.RollBack()
        moved = 0
    elif not commit_checked(transaction):
        skipped.append((None, u"Revit rolled the move back during the "
                              u"commit"))
        moved = 0

    return moved, skipped, partial


def apply_rehost(doc, plans):
    """Replace level owned work planes. One transaction, one undo step.

    Separate from the move because it is a separate kind of change. A
    failure here is also less alarming than a half written move: the
    curve either gets a new plane or it does not, and either way it has
    not shifted, so a failure is reported and the rest carry on.
    """
    done = 0
    failed = []
    cache = {}

    transaction = DB.Transaction(doc, "AVH rehost work plane elements")
    transaction.Start()
    try:
        for _source_level, entries in plans:
            for entry in model.rehostable(entries):
                element = doc.GetElement(entry.element_id)
                if element is None:
                    failed.append((entry, u"no longer in the model"))
                    continue
                try:
                    rehost_curve(doc, element, cache)
                    done += 1
                except BaseException as exc:
                    failed.append((entry, to_text(exc)))
    except BaseException:
        transaction.RollBack()
        raise

    if not done:
        transaction.RollBack()
    elif not commit_checked(transaction):
        failed.append((None, u"Revit rolled it back during the commit"))
        done = 0

    return done, failed


def ask_to_force(doc, blocked):
    """Offer to delete a level that still has real elements on it.

    Revit's own delete cascades, so this needs no extra deletion step:
    removing the level removes what is left on it. That is exactly why it
    needs its own confirmation, listing what would go, rather than being
    folded into the ordinary one.

    Only levels that were checked successfully are offered. A level whose
    probe raised is never forced, because nobody knows what is on it.
    """
    forced = []
    for level, blockers, error in blocked:
        if error or not blockers:
            continue

        kinds = {}
        for element_id in blockers:
            label = describe(doc.GetElement(element_id))
            kinds[label] = kinds.get(label, 0) + 1
        listing = u"\n".join(
            u"  {0} x {1}".format(count, name)
            for name, count in sorted(kinds.items()))

        message = (u"{0} still has {1} real element(s) on it:\n\n{2}\n\n"
                   u"Deleting the level deletes these too. There is no "
                   u"way to keep them and remove the level. Ctrl+Z "
                   u"afterwards is the only way back.".format(
                       to_text(level.Name), len(blockers), listing))

        if confirm(message, "Delete the level and these"):
            forced.append((level, blockers))
            output.print_md(
                "**{0}** will be deleted along with {1} element(s), on "
                "your say so.".format(to_text(level.Name), len(blockers)))
    return forced


def delete_phase(doc, source_levels):
    """Offer to delete levels nothing real still depends on."""
    free = []
    blocked = []

    for level in source_levels:
        try:
            residual = probe_dependents(doc, level)
        except BaseException as exc:
            blocked.append((level, [], to_text(exc)))
            continue

        keep = eid_value(level.Id)
        collateral = []
        blockers = []
        for element_id in residual:
            if eid_value(element_id) == keep:
                continue
            element = doc.GetElement(element_id)
            if element is None:
                continue
            if is_collateral(element) or isinstance(element, DB.Level):
                collateral.append(element_id)
            else:
                blockers.append(element_id)

        if blockers:
            blocked.append((level, blockers, u""))
        else:
            free.append((level, collateral))

    output.print_md("# Deleting the emptied levels")

    for level, blockers, error in blocked:
        if error:
            output.print_md("**{0}** could not be checked: {1}".format(
                to_text(level.Name), error))
            continue
        output.print_md(
            "**{0}** still has {1} real element(s) on it, leaving it "
            "alone".format(to_text(level.Name), len(blockers)))
        for element_id in blockers[:MAX_LISTED]:
            output.print_md("* {0} {1}".format(
                output.linkify(element_id),
                describe(doc.GetElement(element_id))))
        if len(blockers) > MAX_LISTED:
            output.print_md("* and {0} more".format(
                len(blockers) - MAX_LISTED))

    # Levels that came through ask_to_force were confirmed one at a time,
    # with their contents listed. Asking again in the group prompt below
    # would be a second confirmation for the same decision, and the first
    # version did exactly that, so a forced level never got deleted.
    to_delete = [level for level, _blockers in ask_to_force(doc, blocked)]

    if free:
        collateral_count = sum(len(items) for _level, items in free)
        names = u", ".join(to_text(level.Name) for level, _items in free)
        message = (u"Delete {0} level(s)?\n\n{1}\n\nThis also removes "
                   u"{2} view(s) and internal object(s) belonging to "
                   u"them, including their floor plans. Ctrl+Z afterwards "
                   u"is the only way back.".format(
                       len(free), names, collateral_count))
        if confirm(message, "Delete levels"):
            to_delete.extend(level for level, _items in free)
        else:
            output.print_md("Left the emptied levels in place.")

    if not to_delete:
        output.print_md("No level was deleted.")
        return

    deleted = []
    errors = []
    transaction = DB.Transaction(doc, "AVH delete emptied levels")
    transaction.Start()
    try:
        for level in to_delete:
            name = to_text(level.Name)
            try:
                doc.Delete(level.Id)
                deleted.append(name)
            except BaseException as exc:
                errors.append(u"{0}: {1}".format(name, to_text(exc)))
    except BaseException:
        transaction.RollBack()
        raise
    if not commit_checked(transaction):
        output.print_md(
            "**Revit rolled the deletion back during the commit.** "
            "Nothing was removed.")
        return

    if deleted:
        output.print_md("Deleted {0} level(s): {1}".format(
            len(deleted), u", ".join(deleted)))
    for line in errors:
        output.print_md("Failed to delete {0}".format(line))


def pick_names(names, title, multiselect):
    """Name based picker, the pattern ExportSchedule already relies on.

    Level names are unique in Revit, so a name round trips to exactly one
    level. `SelectFromList` returns the ticked items themselves, and a
    single pick comes back unwrapped, so both shapes are handled.
    """
    chosen = forms.SelectFromList.show(
        sorted(names), title=title, button_name="OK",
        multiselect=multiselect)
    if not chosen:
        return []
    if isinstance(chosen, (list, tuple, set)):
        return [to_text(name) for name in chosen]
    return [to_text(chosen)]


def choose_levels(doc):
    """Pick the levels to clear and the level to move onto."""
    levels = (DB.FilteredElementCollector(doc)
              .OfCategory(DB.BuiltInCategory.OST_Levels)
              .WhereElementIsNotElementType()
              .ToElements())
    levels = [level for level in levels if isinstance(level, DB.Level)]

    if len(levels) < 2:
        forms.alert("This model needs at least two levels for this tool "
                    "to have anywhere to move elements to.", title=TITLE)
        return None, None

    by_name = {}
    for level in levels:
        by_name[to_text(level.Name)] = level

    source_names = pick_names(by_name.keys(),
                              "Levels to clear and delete", True)
    if not source_names:
        return None, None

    remaining = [name for name in by_name if name not in source_names]
    if not remaining:
        forms.alert("You picked every level, so there is nowhere to move "
                    "the elements to.", title=TITLE)
        return None, None

    target_names = pick_names(remaining, "Target level", False)
    if not target_names:
        return None, None

    return ([by_name[name] for name in source_names],
            by_name[target_names[0]])


def ask_to_move(totals, target_level, plans):
    """Confirm the move. Returns (go_ahead, include_shift)."""
    message = (u"Move {0} element(s) onto {1}?\n\nThey keep their "
               u"absolute elevation.".format(
                   totals[model.MOVE], to_text(target_level.Name)))

    if not totals[model.SHIFT]:
        return confirm(message, "Move elements"), False

    # Name the constraints in the dialog, not only in the report. The
    # first version said these "would move vertically", which is true of
    # a base constraint and a serious understatement of an upper one: a
    # room's upper limit repointed to a lower level ends up underneath
    # the room's own base. Whoever is clicking should see which parameter
    # they are agreeing to before they agree to it.
    labels = set()
    for _source_level, entries in plans:
        for entry in entries:
            if entry.action == model.SHIFT:
                for name in entry.constraint_labels:
                    labels.add(name)

    message += (u"\n\n{0} further element(s) have a constraint with no "
                u"offset paired to it:\n\n{1}\n\nRepointing those moves "
                u"the element. If any is a top or upper constraint it "
                u"can end up below the element's own base. Only include "
                u"them if you recognise the parameter and want "
                u"that.".format(totals[model.SHIFT],
                                u", ".join(sorted(labels)) or u"unnamed"))
    try:
        answer = forms.alert(
            message, title=TITLE,
            options=["Move, leave those alone",
                     "Move, including those",
                     "Cancel"])
    except BaseException:
        return False, False

    if answer == "Move, including those":
        return True, True
    return answer == "Move, leave those alone", False


def run():
    doc = revit.doc

    source_levels, target_level = choose_levels(doc)
    if not source_levels:
        return

    plans = []
    for source_level in source_levels:
        try:
            plans.append(
                (source_level,
                 plan_for_level(doc, source_level, target_level)))
        except BaseException as exc:
            logger.error("Could not read %s: %s",
                         to_text(source_level.Name), to_text(exc))

    if not plans:
        forms.alert("Nothing could be read from the levels you picked.",
                    title=TITLE)
        return

    # Measure what can actually be rehosted before reporting it, so the
    # dry run promises only what Revit will accept.
    probe_rehost(doc, plans)
    probe_recreate(doc, plans, target_level)

    totals = report_plan(plans, target_level)

    if not totals[model.MOVE] and not totals[model.SHIFT]:
        output.print_md("Nothing can be moved.")
    else:
        go_ahead, include_shift = ask_to_move(totals, target_level,
                                              plans)
        if not go_ahead:
            output.print_md("Cancelled, nothing was written.")
            return

        moved, skipped, partial = apply_plan(doc, plans, include_shift)

        output.print_md("# Result")
        if partial:
            output.print_md(
                "**Rolled the whole move back.** {0} element(s) had "
                "their level changed but could not have the matching "
                "offset written, which would have left them at the "
                "wrong elevation.".format(len(partial)))
            for entry, error in partial:
                output.print_md("* {0} {1}: {2}".format(
                    output.linkify(entry.element_id),
                    entry.description, error))
            return

        if not moved:
            output.print_md(
                "Nothing was written, so the move was rolled back.")

        output.print_md("{0} element(s) moved onto {1}.".format(
            moved, to_text(target_level.Name)))
        for entry, error in skipped:
            if entry is None:
                output.print_md("* {0}".format(error))
                continue
            output.print_md("* skipped {0} {1}: {2}".format(
                output.linkify(entry.element_id),
                entry.description, error))
        if not moved:
            return

    if totals[model.REHOST]:
        message = (u"Replace the work plane on {0} element(s)?\n\n"
                   u"They do not move. They are given an equivalent "
                   u"plane at the same origin that is not owned by a "
                   u"level, which is what frees them from it.".format(
                       totals[model.REHOST]))
        if confirm(message, "Replace work planes"):
            done, failed = apply_rehost(doc, plans)
            output.print_md("{0} element(s) rehosted.".format(done))
            for entry, error in failed:
                if entry is None:
                    output.print_md("* {0}".format(error))
                    continue
                output.print_md("* {0} {1} failed: {2}".format(
                    output.linkify(entry.element_id),
                    entry.description, error))
        else:
            output.print_md("Left the work planes alone.")

    if totals[model.RECREATE]:
        message = (u"Move {0} room(s) onto {1}?\n\nEach is taken out "
                   u"and placed again in the enclosed area at its own "
                   u"coordinates, so it keeps its number and every other "
                   u"parameter.\n\nUnlike everything else this tool "
                   u"does, a room really changes elevation: a room sits "
                   u"at its level. This has been tried already in a "
                   u"discarded transaction, so it is known to "
                   u"work.".format(totals[model.RECREATE],
                                   to_text(target_level.Name)))
        if confirm(message, "Move the rooms"):
            done, failed = apply_recreate(doc, plans, target_level)
            output.print_md("{0} room(s) moved.".format(done))
            for entry, error in failed:
                if entry is None:
                    output.print_md("* {0}".format(error))
                    continue
                output.print_md("* {0} {1} failed: {2}".format(
                    output.linkify(entry.element_id),
                    entry.description, error))
            if failed:
                output.print_md(
                    "**The room step was rolled back in full.** An "
                    "unplaced room is worse than one that never moved.")
        else:
            output.print_md("Left the rooms where they are.")

    delete_phase(doc, source_levels)


if __name__ == "__main__":
    run()
