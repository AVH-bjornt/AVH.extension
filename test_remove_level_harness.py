# -*- coding: utf-8 -*-
"""
Runs the real Remove Level script against a mocked Revit.

The lesson this repository keeps relearning is that failures happen in the
thin pushbutton scripts, not in the libraries underneath them. The
arithmetic in `avh_levels.model` is covered by `test_level_move.py`; this
suite covers the wiring around it.

Scenario 6 is the one that earned its keep. The first version against a
real model reported 116 dependents on a level whose entire model content
was a railing, moved none of them, and refused to delete because it
counted floor plans and viewports as blockers. That shape is now a test.

The fake document is behavioural rather than scripted: `Delete` works out
a level's dependents by looking at which elements currently sit on it, so
moving an element genuinely clears the level and the delete phase sees the
real consequence.

Run outside Revit:

    python test_remove_level_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Remove Level.pushbutton", "script.py")

from avh_levels import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-9):
    return abs(a - b) < tolerance


MM = 1.0 / model.MM_PER_FOOT

# Every BuiltInParameter the script names, plus a couple it does not, so
# the unpaired case can be exercised. The fake enum values are the names
# themselves, which is what a .NET enum's ToString gives, and is what
# `param_name` reports and `LEVEL_OFFSET_PAIRS` is keyed on.
PARAM_NAMES = (
    "INVALID",
    "WALL_BASE_CONSTRAINT", "WALL_BASE_OFFSET",
    "FAMILY_LEVEL_PARAM", "FAMILY_BASE_LEVEL_OFFSET_PARAM",
    "INSTANCE_FREE_HOST_OFFSET_PARAM",
    "SCHEDULE_LEVEL_PARAM", "ROOM_LEVEL_ID", "ROOM_LOWER_OFFSET",
    "LEVEL_PARAM", "FLOOR_HEIGHTABOVELEVEL_PARAM",
    "ROOF_BASE_LEVEL_PARAM", "ROOF_LEVEL_OFFSET_PARAM",
    "STAIRS_BASE_LEVEL_PARAM", "STAIRS_BASE_OFFSET",
    "STAIRS_RAILING_BASE_LEVEL_PARAM", "STAIRS_RAILING_HEIGHT_OFFSET",
    "STAIRS_TOP_LEVEL_PARAM", "STAIRS_TOP_OFFSET",
    "WALL_HEIGHT_TYPE", "WALL_TOP_OFFSET",
    "ROOM_UPPER_LEVEL", "ROOM_UPPER_OFFSET",
    "SOME_UNPAIRED_LEVEL_PARAM", "SOME_UNRELATED_OFFSET",
)

_next_param_id = [1000]


def next_param_id():
    _next_param_id[0] += 1
    return _next_param_id[0]


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "<Id {0}>".format(self.Value)


INVALID_ID = FakeId(-1)


class FakeCategory(object):
    def __init__(self, name):
        self.Name = name


class FakeDefinition(object):
    def __init__(self, bip_name):
        self.BuiltInParameter = bip_name or "INVALID"
        self.Name = bip_name or "Shared parameter"


class FakeParameter(object):
    """A parameter that writes back into its owning element."""

    def __init__(self, element, attribute, bip_name, storage,
                 read_only=False, fails_on_set=False):
        self.element = element
        self.attribute = attribute
        self.Definition = FakeDefinition(bip_name)
        self.StorageType = storage
        self.IsReadOnly = read_only
        self.fails_on_set = fails_on_set
        self.Id = FakeId(next_param_id())

    def AsDouble(self):
        return getattr(self.element, self.attribute)

    def AsElementId(self):
        return getattr(self.element, self.attribute)

    def Set(self, value):
        if self.fails_on_set:
            raise ValueError("parameter rejected the value")
        setattr(self.element, self.attribute, value)
        return True


class FakeElement(object):
    def __init__(self, value, category, name, level_param=None,
                 offset_param=None, level_id=None, offset=0.0,
                 offset_read_only=False, offset_fails=False,
                 owner_view_id=None, top_level_param=None,
                 top_offset_param=None, top_level_id=None,
                 top_offset=0.0, locked_level_param=None,
                 view_specific=False):
        self.Id = FakeId(value)
        self.Category = FakeCategory(category) if category else None
        self.Name = name
        self.level_id = level_id
        self.offset = offset
        self.top_level_id = top_level_id
        self.top_offset = top_offset
        # Only set when a read only level parameter is asked for.
        # Defaulting it to level_id pinned every element to its
        # level forever and failed four checks.
        self.locked_level_id = None
        self.ViewSpecific = view_specific
        self.OwnerViewId = owner_view_id or INVALID_ID
        self._params = {}
        if level_param:
            self._add(level_param, "level_id", "ElementId")
        if offset_param:
            self._add(offset_param, "offset", "Double",
                      read_only=offset_read_only, fails=offset_fails)
        if top_level_param:
            self._add(top_level_param, "top_level_id", "ElementId")
        if top_offset_param:
            self._add(top_offset_param, "top_offset", "Double")
        if locked_level_param:
            self.locked_level_id = level_id
            self._add(locked_level_param, "locked_level_id", "ElementId",
                      read_only=True)

    def _add(self, bip_name, attribute, storage, read_only=False,
             fails=False):
        self._params[bip_name] = FakeParameter(
            self, attribute, bip_name, storage,
            read_only=read_only, fails_on_set=fails)

    @property
    def Parameters(self):
        return list(self._params.values())

    def get_Parameter(self, enum):
        return self._params.get(enum)

    def GetType(self):
        return types.SimpleNamespace(Name=type(self).__name__)


class FakeLevel(FakeElement):
    def __init__(self, value, name, elevation):
        FakeElement.__init__(self, value, "Levels", name)
        self.Elevation = elevation


class FakeView(FakeElement):
    """A floor plan. Deleted with its level, never moved."""

    def __init__(self, value, name, level_id):
        FakeElement.__init__(self, value, "Views", name)
        self.level_id = level_id


class FakeViewport(FakeElement):
    def __init__(self, value, name, level_id):
        FakeElement.__init__(self, value, "Viewports", name)
        self.level_id = level_id


class FakeElementType(FakeElement):
    def __init__(self, value, level_id):
        FakeElement.__init__(self, value, None, None)
        self.level_id = level_id


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z

    def GetLength(self):
        return (self.X ** 2 + self.Y ** 2 + self.Z ** 2) ** 0.5


class FakePlane(object):
    def __init__(self, normal, origin):
        self.Normal = normal
        self.Origin = origin

    @staticmethod
    def CreateByNormalAndOrigin(normal, origin):
        return FakePlane(normal, origin)


class FakeSketchPlane(FakeElement):
    """A work plane. One owned by a level dies with it."""

    created = []

    def __init__(self, value, level_id=None, elevation=0.0):
        FakeElement.__init__(self, value, "Work Planes", "Sketch Plane")
        self.level_id = level_id
        self.elevation = elevation

    def GetPlane(self):
        return FakePlane(FakeXYZ(0.0, 0.0, 1.0),
                         FakeXYZ(0.0, 0.0, self.elevation))

    @staticmethod
    def Create(doc, plane):
        value = 90000 + len(FakeSketchPlane.created)
        # A plane built from geometry belongs to no level, which is the
        # whole point: that is what frees the curve.
        made = FakeSketchPlane(value, level_id=None,
                               elevation=plane.Origin.Z)
        FakeSketchPlane.created.append(made)
        doc.elements[value] = made
        # A rollback undoes creation as well as modification. Without
        # this the feasibility probe left a phantom plane in the document
        # and the harness counted three where Revit would show two.
        doc._pending_creates.append(value)
        return made


class FakeCurveElement(FakeElement):
    def __init__(self, value, category, name, sketch_plane,
                 owned_by_sketch=False):
        FakeElement.__init__(self, value, category, name)
        self._sketch_plane = sketch_plane
        # A sketch line belonging to a stair looks identical from the
        # outside and throws only when you try to reassign it. This is
        # the case that made the dry run promise five rehosts and deliver
        # none.
        self._owned_by_sketch = owned_by_sketch

    def _get_sketch_plane(self):
        return self._sketch_plane

    def _set_sketch_plane(self, value):
        if self._owned_by_sketch:
            raise Exception("The curve belongs to a sketch-based "
                            "element, and cannot be modified "
                            "independently.")
        self._sketch_plane = value

    SketchPlane = property(_get_sketch_plane, _set_sketch_plane)


class FakeUV(object):
    def __init__(self, u, v):
        self.U, self.V = u, v


class FakeCircuit(object):
    """One enclosed area on a level."""

    def __init__(self, x, y, occupied=False, refuses=False, reach=5.0,
                 refuse_from_call=None):
        self.x, self.y = x, y
        self.IsRoomLocated = occupied
        self.refuses = refuses
        # How far from its centre this area still encloses a room.
        self.reach = reach
        # Refuse from the Nth attempt onward, so a room can pass the
        # feasibility probe and then fail for real. That is the only way
        # to reach the partial failure rollback, and without it the
        # rollback was untested and a mutation removing it survived.
        self.refuse_from_call = refuse_from_call
        self.calls = 0

    def GetPointInside(self):
        return FakeUV(self.x, self.y)


class FakePlanTopology(object):
    def __init__(self, circuits):
        self.Circuits = circuits


class FakeRoom(FakeElement):
    """A room. Its level is read only, as Björn's own model proved."""

    def __init__(self, value, name, level_id, x=0.0, y=0.0,
                 upper_level_param="ROOM_UPPER_LEVEL",
                 base_offset=0.0, upper_offset=2500.0 * MM):
        FakeElement.__init__(self, value, "Rooms", name,
                             locked_level_param="ROOM_LEVEL_ID",
                             offset_param="ROOM_LOWER_OFFSET",
                             top_level_param=upper_level_param,
                             top_offset_param="ROOM_UPPER_OFFSET",
                             level_id=level_id, top_level_id=level_id,
                             offset=base_offset, top_offset=upper_offset)
        self.Number = "101"
        self.Area = 25.0
        self.Location = types.SimpleNamespace(Point=FakeXYZ(x, y, 0.0))
        self.circuit = None

    def Unplace(self):
        self.Location = types.SimpleNamespace(Point=None)
        self.Area = 0.0
        if self.circuit is not None:
            self.circuit.IsRoomLocated = False
        self.circuit = None


class FakeCreator(object):
    """Document.Create, enough of it for NewRoom."""

    def __init__(self, doc):
        self.doc = doc

    def NewRoom(self, room, circuit):
        circuit.calls += 1
        if circuit.refuses or (circuit.refuse_from_call is not None
                               and circuit.calls >= circuit.refuse_from_call):
            raise Exception("circuit refused the room")
        room.Location = types.SimpleNamespace(
            Point=FakeXYZ(circuit.x, circuit.y, 0.0))
        room.circuit = circuit
        circuit.IsRoomLocated = True
        room.level_id = self.doc.level_of_circuit.get(id(circuit))
        room.locked_level_id = room.level_id
        room.Area = 25.0
        return room


class FakeTransformUtils(object):
    @staticmethod
    def MoveElement(doc, element_id, delta):
        element = doc.elements.get(element_id.Value)
        point = element.Location.Point
        moved = FakeXYZ(point.X + delta.X, point.Y + delta.Y, point.Z)
        element.Location = types.SimpleNamespace(Point=moved)
        # A room dragged out of the area that encloses it reports zero
        # area, which is what tells a real placement from a nominal one.
        circuit = element.circuit
        if circuit is not None:
            near = (abs(moved.X - circuit.x) < circuit.reach
                    and abs(moved.Y - circuit.y) < circuit.reach)
            element.Area = 25.0 if near else 0.0


class FakePreprocessorBase(object):
    """Stands in for the IFailuresPreprocessor interface."""


class FakeFailure(object):
    def __init__(self, text, severity="Error"):
        self.text = text
        self.severity = severity

    def GetSeverity(self):
        return self.severity

    def GetDescriptionText(self):
        return self.text


class FakeAccessor(object):
    def __init__(self, failures):
        self.failures = failures

    def GetFailureMessages(self):
        return list(self.failures)

    def DeleteWarning(self, failure):
        if failure in self.failures:
            self.failures.remove(failure)


class FakeFailureOptions(object):
    def __init__(self, transaction):
        self.transaction = transaction
        self.preprocessor = None

    def SetFailuresPreprocessor(self, preprocessor):
        self.preprocessor = preprocessor

    def SetClearAfterRollback(self, value):
        pass

    def SetForcedModalHandling(self, value):
        pass


class FakeTransaction(object):
    log = []

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.preprocessor = None

    def GetFailureHandlingOptions(self):
        return FakeFailureOptions(self)

    def SetFailureHandlingOptions(self, options):
        self.preprocessor = options.preprocessor

    def Start(self):
        FakeTransaction.log.append(("start", self.name))

    def Commit(self):
        FakeTransaction.log.append(("commit", self.name))
        # Revit validates on commit and can roll back there, returning
        # RolledBack rather than raising. Ignoring this return value is
        # what let the tool report a room as moved that Revit had thrown
        # away, so the fake has to be able to do it too.
        errors = self.doc.validate()
        if errors:
            if self.preprocessor is not None:
                self.preprocessor.PreprocessFailures(FakeAccessor(errors))
            self.doc.restore()
            return "RolledBack"
        self.doc.commit()
        return "Committed"

    def RollBack(self):
        FakeTransaction.log.append(("rollback", self.name))
        self.doc.restore()


class FakeDoc(object):
    """A document that undoes only what the open transaction changed.

    The first version of this fake snapshotted once in __init__ and never
    again, so the rollback of a later probe transaction reverted a move
    that had already been committed. Revit rolls back one transaction,
    not the document's whole history, and the harness reported the
    difference as thirteen failures until the snapshot moved to commit.
    """

    def __init__(self, elements):
        self.elements = {}
        for element in elements:
            self.elements[element.Id.Value] = element
        self.deleted = []
        self._pending_deletes = []
        self._pending_creates = []
        self.Create = FakeCreator(self)
        self.topologies = {}
        self.level_of_circuit = {}
        # Plan circuits are derived state, not elements, so they were
        # invisible to the snapshot and the feasibility probe left every
        # one of them marked occupied. Fourth bug in this fake and the
        # same family as the other three: state that outlives the
        # transaction that changed it. Revit rebuilds plan topology, so
        # the fake has to restore it.
        self.circuits = []
        self._snapshot()

    def _snapshot(self):
        self._saved = dict(
            (value, (element.level_id, element.offset,
                     element.top_level_id, element.top_offset,
                     element.locked_level_id,
                     getattr(element, "SketchPlane", None),
                     getattr(element, "Location", None),
                     getattr(element, "Area", None),
                     getattr(element, "circuit", None)))
            for value, element in self.elements.items())
        self._saved_circuits = dict(
            (id(circuit), circuit.IsRoomLocated)
            for circuit in self.circuits)

    def restore(self):
        for circuit in self.circuits:
            if id(circuit) in self._saved_circuits:
                circuit.IsRoomLocated = self._saved_circuits[id(circuit)]
        for value, state in self._saved.items():
            element = self.elements.get(value)
            if element is not None:
                (element.level_id, element.offset, element.top_level_id,
                 element.top_offset, element.locked_level_id,
                 sketch, location, area, circuit) = state
                if location is not None:
                    element.Location = location
                if area is not None:
                    element.Area = area
                if hasattr(element, "circuit"):
                    element.circuit = circuit
                if sketch is not None:
                    # Bypass the property, a rollback is not a user edit.
                    if hasattr(element, "_sketch_plane"):
                        element._sketch_plane = sketch
                    else:
                        element.SketchPlane = sketch
        for value in self._pending_creates:
            self.elements.pop(value, None)
        self._pending_creates = []
        self._pending_deletes = []

    def validate(self):
        """Revit's own check: a room must have a height greater than 0.

        This is the error Björn's model raised, and it arrived during the
        commit rather than from any call the tool made, which is exactly
        why it went unnoticed.
        """
        errors = []
        for element in self.elements.values():
            if not isinstance(element, FakeRoom):
                continue
            if element.Location.Point is None:
                continue
            if self.room_height(element) <= 0:
                errors.append(FakeFailure(
                    "Room must have a height greater than 0."))
        return errors

    def room_height(self, room):
        base = self.elements.get(
            room.level_id.Value) if room.level_id else None
        upper = self.elements.get(
            room.top_level_id.Value) if room.top_level_id else base
        if base is None or upper is None:
            return 1.0
        return ((upper.Elevation + room.top_offset)
                - (base.Elevation + room.offset))

    def commit(self):
        for value in self._pending_deletes:
            self.elements.pop(value, None)
            self.deleted.append(value)
        self._pending_deletes = []
        # Committed creations are permanent. Forgetting to clear this
        # meant the next rollback deleted a sketch plane that had already
        # been committed, which read as the rehost silently undoing
        # itself. Third bug in this fake, all three the same shape:
        # transaction bookkeeping that outlives its transaction.
        self._pending_creates = []
        self._snapshot()

    def set_topology(self, level, circuits):
        self.topologies[level.Id.Value] = FakePlanTopology(circuits)
        for circuit in circuits:
            self.level_of_circuit[id(circuit)] = level.Id
            self.circuits.append(circuit)
        self._snapshot()

    def get_PlanTopology(self, level):
        found = self.topologies.get(level.Id.Value)
        if found is None:
            return FakePlanTopology([])
        return found

    def Regenerate(self):
        pass

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def Delete(self, element_id):
        """Ids Revit would remove with this one, worked out live."""
        target = element_id.Value
        removed = [element_id]
        for value, element in sorted(self.elements.items()):
            if value == target:
                continue
            attached = False
            for held in (element.level_id, element.top_level_id,
                         element.locked_level_id):
                if held is not None and held.Value == target:
                    attached = True
            if attached:
                removed.append(element.Id)

        # A curve dies with the level when the plane it sits on does.
        # This second pass is what makes rehosting observable: give the
        # curve a plane that is not on this list and it stops appearing.
        doomed = set(eid.Value for eid in removed)
        for value, element in sorted(self.elements.items()):
            if value == target or value in doomed:
                continue
            plane = getattr(element, "SketchPlane", None)
            if plane is not None and plane.Id.Value in doomed:
                removed.append(element.Id)

        self._pending_deletes.extend(eid.Value for eid in removed)
        return removed


def build_pyrevit(doc, levels, picks, answers, recorder):
    """A pyrevit module good enough to run the script against."""

    class BuiltInParameter(object):
        pass

    for name in PARAM_NAMES:
        setattr(BuiltInParameter, name, name)

    class BuiltInCategory(object):
        OST_Levels = "OST_Levels"

    class StorageType(object):
        Double = "Double"
        ElementId = "ElementId"
        Integer = "Integer"
        String = "String"

    class ElementId(object):
        InvalidElementId = INVALID_ID

    class Collector(object):
        def __init__(self, _doc):
            pass

        def OfCategory(self, _category):
            return self

        def WhereElementIsNotElementType(self):
            return self

        def ToElements(self):
            return list(levels)

    db = types.SimpleNamespace(
        BuiltInParameter=BuiltInParameter,
        BuiltInCategory=BuiltInCategory,
        StorageType=StorageType,
        ElementId=ElementId,
        FilteredElementCollector=Collector,
        Transaction=FakeTransaction,
        Level=FakeLevel,
        View=FakeView,
        Viewport=FakeViewport,
        ElementType=FakeElementType,
        CurveElement=FakeCurveElement,
        Architecture=types.SimpleNamespace(Room=FakeRoom),
        ElementTransformUtils=FakeTransformUtils,
        FailureSeverity=types.SimpleNamespace(Warning="Warning"),
        FailureProcessingResult=types.SimpleNamespace(
            Continue="Continue",
            ProceedWithRollBack="ProceedWithRollBack"),
        # A distinct class, not object. Using object made the tool's
        # `class Collector(DB.IFailuresPreprocessor)` unable to build a
        # method resolution order, the whole thing fell into its except
        # branch, and Revit's own error text never reached the report.
        IFailuresPreprocessor=FakePreprocessorBase,
        TransactionStatus=types.SimpleNamespace(
            Committed="Committed", RolledBack="RolledBack"),
        SketchPlane=FakeSketchPlane,
        Plane=FakePlane,
        XYZ=FakeXYZ,
    )

    class SelectFromList(object):
        @staticmethod
        def show(items, title=None, button_name=None, multiselect=False):
            recorder["pickers"].append(title)
            return picks.pop(0) if picks else None

    def alert(message, title=None, options=None, **kwargs):
        recorder["dialogs"].append(message)
        return answers.pop(0) if answers else None

    forms = types.SimpleNamespace(SelectFromList=SelectFromList,
                                  alert=alert)
    output = types.SimpleNamespace(
        print_md=lambda text: recorder["output"].append(text),
        linkify=lambda element_id: "[{0}]".format(element_id.Value),
    )
    logger = types.SimpleNamespace(
        error=lambda *args: recorder["errors"].append(args),
        warning=lambda *args: None,
        info=lambda *args: None,
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = types.SimpleNamespace(doc=doc, uidoc=None)
    pyrevit.DB = db
    pyrevit.forms = forms
    pyrevit.script = types.SimpleNamespace(
        get_output=lambda: output, get_logger=lambda: logger)
    return pyrevit


def run_script(doc, levels, picks, answers):
    recorder = {"output": [], "dialogs": [], "pickers": [], "errors": []}
    FakeTransaction.log = []
    saved = sys.modules.get("pyrevit")
    sys.modules["pyrevit"] = build_pyrevit(
        doc, levels, list(picks), list(answers), recorder)
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        if saved is None:
            sys.modules.pop("pyrevit", None)
        else:
            sys.modules["pyrevit"] = saved
    recorder["transactions"] = list(FakeTransaction.log)
    recorder["text"] = "\n".join(recorder["output"]).lower()
    return recorder


def two_levels():
    return (FakeLevel(100, u"E01", 0.0),
            FakeLevel(200, u"E02", 3000.0 * MM))


def scenario(offset_fails=False, offset_read_only=False, extra=None):
    """Two levels 3000 mm apart, a wall and a door on the lower one."""
    lower, upper = two_levels()
    wall = FakeElement(
        1, u"Walls", u"Basic Wall",
        level_param="WALL_BASE_CONSTRAINT",
        offset_param="WALL_BASE_OFFSET",
        level_id=lower.Id, offset=500.0 * MM,
        offset_read_only=offset_read_only, offset_fails=offset_fails)
    door = FakeElement(
        2, u"Doors", u"M_Single-Flush",
        level_param="FAMILY_LEVEL_PARAM",
        offset_param="FAMILY_BASE_LEVEL_OFFSET_PARAM",
        level_id=lower.Id, offset=0.0)
    elements = [lower, upper, wall, door] + list(extra or [])
    return FakeDoc(elements), [lower, upper], wall, door


# --- 1. the happy path, and the arithmetic that reaches the model -----

doc, levels, wall, door = scenario()
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move elements", "Delete levels"])

check("both pickers were shown", len(run["pickers"]) == 2)
check("the wall moved onto the target level",
      wall.level_id is not None and wall.level_id.Value == 200,
      "level_id {0}".format(wall.level_id))

expected = model.new_offset(0.0, 500.0 * MM, 3000.0 * MM)
check("the wall's new offset is the one the arithmetic gives",
      close(wall.offset, expected),
      "got {0}, expected {1}".format(wall.offset, expected))
check("which is minus 2500 mm, keeping it at 500 mm absolute",
      close(model.feet_to_mm(wall.offset), -2500.0),
      "got {0} mm".format(model.feet_to_mm(wall.offset)))
check("so the wall did not physically move",
      close(500.0, 3000.0 + model.feet_to_mm(wall.offset)),
      "ends at {0} mm".format(3000.0 + model.feet_to_mm(wall.offset)))
check("the door, at zero offset, lands at minus 3000 mm",
      close(model.feet_to_mm(door.offset), -3000.0),
      "got {0} mm".format(model.feet_to_mm(door.offset)))
check("the emptied level was deleted", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))
check("the target level was not deleted", 200 not in doc.deleted)

buggy = 0.0 + 500.0 * MM
check("the value written is not the one the original would have written",
      not close(wall.offset, buggy),
      "written {0}, original {1}".format(wall.offset, buggy))


# --- 2. the generic scan finds a parameter no table listed ------------
#
# The hardcoded table this replaced could not see railings, which is what
# the first real run tripped over.

lower, upper = two_levels()
railing = FakeElement(
    5, u"Railings", u"1100mm - iðnaður - ker",
    level_param="STAIRS_RAILING_BASE_LEVEL_PARAM",
    offset_param="STAIRS_RAILING_HEIGHT_OFFSET",
    level_id=lower.Id, offset=0.0)
doc = FakeDoc([lower, upper, railing])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move elements", "Delete levels"])

check("a railing is found by the parameter scan and moved",
      railing.level_id.Value == 200,
      "level_id {0}".format(railing.level_id))
check("and it keeps its absolute elevation",
      close(model.feet_to_mm(railing.offset), -3000.0),
      "got {0} mm".format(model.feet_to_mm(railing.offset)))
check("so the level it was on can now be deleted", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))


# --- 3. a level parameter with no paired offset is a shift ------------

lower, upper = two_levels()
odd = FakeElement(
    6, u"Generic Models", u"Something unusual",
    level_param="SOME_UNPAIRED_LEVEL_PARAM",
    offset_param="SOME_UNRELATED_OFFSET",
    level_id=lower.Id, offset=250.0 * MM)
doc = FakeDoc([lower, upper, odd])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move, leave those alone"])

check("an unrecognised pair is reported as a shift, not moved silently",
      "physically move" in run["text"])
check("declining leaves it where it was", odd.level_id.Value == 100,
      "level_id {0}".format(odd.level_id))
check("and the report names the parameter that was found, so the pair "
      "can be added from evidence",
      "some_unpaired_level_param" in run["text"],
      "{0}".format(run["text"][-400:]))
check("along with the length parameters it could have used",
      "some_unrelated_offset" in run["text"])


# --- 4. view infrastructure is collateral, not a blocker --------------
#
# The real run counted 115 blockers, of which the only genuine model
# element was a railing. Everything else was a floor plan, a viewport, a
# work plane grid or an unnamed sub object.

lower, upper = two_levels()
collateral = [
    FakeView(10, u"DA43.E01 - Efri hæð", lower.Id),
    FakeViewport(11, u"Title w Line", lower.Id),
    FakeElementType(12, lower.Id),
    FakeElement(13, None, None, level_id=lower.Id),
    FakeElement(14, u"Sun Path", u"", level_id=lower.Id,
                owner_view_id=FakeId(10)),
]
doc = FakeDoc([lower, upper] + collateral)
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Delete levels"])

check("a level holding only views and internals reports nothing to move",
      "nothing can be moved" in run["text"])
check("none of it is listed as stuck",
      "**stuck (" not in run["text"],
      "{0}".format(run["text"][:400]))
check("and the level is deleted rather than reported as blocked",
      100 in doc.deleted, "deleted {0}".format(doc.deleted))
check("the confirmation says what goes with it",
      any("internal object" in text for text in run["dialogs"]),
      "{0}".format(run["dialogs"]))


# --- 5. a genuine blocker still blocks --------------------------------

lower, upper = two_levels()
reference_plane = FakeElement(20, u"Reference Planes", u"Reference Plane",
                              level_id=lower.Id)
doc = FakeDoc([lower, upper, reference_plane,
               FakeView(21, u"Plan", lower.Id)])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Delete levels"])

check("an element with nothing pointing at the level is stuck, not "
      "collateral",
      "**stuck (" in run["text"],
      "{0}".format(run["text"][:400]))
check("the level is not deleted", 100 not in doc.deleted,
      "deleted {0}".format(doc.deleted))
check("and the count excludes the view, so it reads as 1 not 2",
      "1 real element" in run["text"],
      "{0}".format(run["text"][-300:]))
check("the report explains it is probably work plane hosted",
      "work plane" in run["text"])


# --- 6. the DA43 shape, end to end ------------------------------------
#
# One railing, buried in view infrastructure. Before the rework this
# reported 116 unmovable and 115 blockers, and deleted nothing.

lower, upper = two_levels()
railing = FakeElement(
    30, u"Railings", u"1100mm - iðnaður - ker",
    level_param="STAIRS_RAILING_BASE_LEVEL_PARAM",
    offset_param="STAIRS_RAILING_HEIGHT_OFFSET",
    level_id=lower.Id, offset=0.0)
noise = [FakeView(40 + i, u"Plan {0}".format(i), lower.Id)
         for i in range(6)]
noise += [FakeViewport(60 + i, u"Viewport {0}".format(i), lower.Id)
          for i in range(4)]
noise += [FakeElement(80 + i, None, None, level_id=lower.Id)
          for i in range(10)]
doc = FakeDoc([lower, upper, railing] + noise)
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move elements", "Delete levels"])

check("the one real element among twenty is found",
      "1 to move" in run["text"], "{0}".format(run["text"][:400]))
check("the other twenty are counted as collateral, not listed as "
      "failures",
      "20 views and internals" in run["text"],
      "{0}".format(run["text"][:400]))
check("the railing moves", railing.level_id.Value == 200,
      "level_id {0}".format(railing.level_id))
check("and the level is finally deletable", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))


# --- 6b. the room that broke, and the two rules it produced -----------
#
# Room 7555204 on a live project. Its base level parameter is read only and
# its ROOM_UPPER_LEVEL is writable, so the scan repointed the upper limit
# to a level 3740 mm lower and corrected nothing, leaving the room's top
# below its own base. The level was still blocked afterwards, because the
# base had never moved. Two rules came out of it.

lower, upper = two_levels()
room = FakeElement(
    50, u"Rooms", u"Skrifstofa",
    locked_level_param="ROOM_LEVEL_ID",
    top_level_param="ROOM_UPPER_LEVEL",
    top_offset_param="ROOM_UPPER_OFFSET",
    level_id=lower.Id, top_level_id=lower.Id, top_offset=2000.0 * MM)
doc = FakeDoc([lower, upper, room])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move, including those", "Delete levels"])

check("a read only level parameter makes the element stuck, whatever "
      "else is writable on it",
      "**stuck (" in run["text"], "{0}".format(run["text"][:500]))
check("the report names the read only parameter",
      "room_level_id" in run["text"])
check("its upper limit is left exactly where it was, even though the "
      "run was told to include shifting elements",
      room.top_level_id.Value == 100,
      "top_level_id {0}".format(room.top_level_id))
check("and its upper offset is untouched",
      close(room.top_offset, 2000.0 * MM),
      "top_offset {0}".format(room.top_offset))
check("the level is not deleted, since the room really is still on it",
      100 not in doc.deleted, "deleted {0}".format(doc.deleted))


# --- 6c. an element constrained to the level twice --------------------
#
# The other half of the room bug: repointing every constraint found while
# correcting only one offset. A stair with base and top on the same level
# is the case that shows it.

lower, upper = two_levels()
stair = FakeElement(
    51, u"Stairs", u"Stair",
    level_param="STAIRS_BASE_LEVEL_PARAM",
    offset_param="STAIRS_BASE_OFFSET",
    top_level_param="STAIRS_TOP_LEVEL_PARAM",
    top_offset_param="STAIRS_TOP_OFFSET",
    level_id=lower.Id, offset=-1420.2 * MM,
    top_level_id=lower.Id, top_offset=1600.0 * MM)
doc = FakeDoc([lower, upper, stair])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move elements", "Delete levels"])

check("both constraints are repointed", stair.level_id.Value == 200
      and stair.top_level_id.Value == 200,
      "base {0}, top {1}".format(stair.level_id, stair.top_level_id))
check("the base offset is corrected",
      close(model.feet_to_mm(stair.offset), -1420.2 - 3000.0),
      "got {0} mm".format(model.feet_to_mm(stair.offset)))
check("and so is the top offset, so the stair keeps its height",
      close(model.feet_to_mm(stair.top_offset), 1600.0 - 3000.0),
      "got {0} mm".format(model.feet_to_mm(stair.top_offset)))

before = (0.0 + 1600.0) - (0.0 + -1420.2)
after = ((3000.0 + model.feet_to_mm(stair.top_offset))
         - (3000.0 + model.feet_to_mm(stair.offset)))
check("its overall height is unchanged, which is the point",
      close(before, after),
      "before {0} mm, after {1} mm".format(before, after))
check("the report lists both constraints by name, not just a count",
      "stairs_base_level_param" in run["text"]
      and "stairs_top_level_param" in run["text"])


# --- 6d. one constraint paired, one not, is all or nothing ------------

lower, upper = two_levels()
odd_stair = FakeElement(
    52, u"Stairs", u"Odd stair",
    level_param="STAIRS_BASE_LEVEL_PARAM",
    offset_param="STAIRS_BASE_OFFSET",
    top_level_param="SOME_UNPAIRED_LEVEL_PARAM",
    level_id=lower.Id, offset=100.0 * MM, top_level_id=lower.Id)
doc = FakeDoc([lower, upper, odd_stair])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move, leave those alone"])

check("an element with one paired and one unpaired constraint is a "
      "shift, not a partial move",
      odd_stair.level_id.Value == 100 and odd_stair.top_level_id.Value == 100,
      "base {0}, top {1}".format(odd_stair.level_id,
                                 odd_stair.top_level_id))
check("its paired offset is not written either, since the move did not "
      "happen at all",
      close(odd_stair.offset, 100.0 * MM),
      "offset {0}".format(odd_stair.offset))
check("the dialog names the unpaired constraint so the click is informed",
      any("SOME_UNPAIRED_LEVEL_PARAM" in text
          for text in run["dialogs"]),
      "{0}".format(run["dialogs"]))
check("and warns that it may be a top constraint",
      any("below the element" in text for text in run["dialogs"]))


# --- 6e. rehosting a curve off a level owned work plane ---------------
#
# Model line 7555172 on a live project. Nothing on it points at the level, so
# no parameter can move it; it is bound through the sketch plane it sits
# on. Replacing that plane with an equivalent one owned by no level frees
# it without moving it.

def rehost_scenario():
    lower, upper = two_levels()
    plane = FakeSketchPlane(70, level_id=lower.Id, elevation=0.0)
    line = FakeCurveElement(71, u"Lines", u"Model Lines", plane)
    return FakeDoc([lower, upper, plane, line]), [lower, upper], line


doc, levels, line = rehost_scenario()
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Replace work planes", "Delete levels"])

check("a curve held only by a level's work plane is offered for rehost",
      "replacing their work plane" in run["text"],
      "{0}".format(run["text"][:400]))
check("it is counted as a rehost, not as stuck",
      "1 to rehost" in run["text"], "{0}".format(run["text"][:400]))
check("its plane is replaced with a different one",
      line.SketchPlane.Id.Value != 70,
      "still on {0}".format(line.SketchPlane.Id))
check("the new plane belongs to no level, which is what frees it",
      line.SketchPlane.level_id is None,
      "level_id {0}".format(line.SketchPlane.level_id))
check("and it is at the same elevation, so the line has not moved",
      close(line.SketchPlane.elevation, 0.0),
      "elevation {0}".format(line.SketchPlane.elevation))
check("so the level can now be deleted", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))

doc, levels, line = rehost_scenario()
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Cancel", "Delete levels"])
check("declining the rehost leaves the plane alone",
      line.SketchPlane.Id.Value == 70,
      "now on {0}".format(line.SketchPlane.Id))
check("and the level stays, because the line still hangs off it",
      100 not in doc.deleted, "deleted {0}".format(doc.deleted))


# --- 6e2. a curve that only looks rehostable --------------------------
#
# Five model lines on a live project were offered for rehosting and all five
# failed: they were sketch lines belonging to the stairs and railings.
# Nothing about them is distinguishable from the outside, so the dry run
# now tries each one in a transaction it throws away, and reports only
# what Revit will actually accept.

lower, upper = two_levels()
plane = FakeSketchPlane(72, level_id=lower.Id, elevation=0.0)
sketch_line = FakeCurveElement(73, u"Lines", u"Model Lines", plane,
                               owned_by_sketch=True)
free_line = FakeCurveElement(74, u"Lines", u"Model Lines", plane)
doc = FakeDoc([lower, upper, plane, sketch_line, free_line])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Replace work planes", "Delete levels"])

check("only the curve that can actually be rehosted is offered",
      "1 to rehost" in run["text"], "{0}".format(run["text"][:400]))
check("the one that cannot is demoted to stuck",
      "1 stuck" in run["text"], "{0}".format(run["text"][:400]))
check("carrying Revit's own message as the reason",
      "sketch-based element" in run["text"])
check("the free curve is rehosted",
      free_line.SketchPlane.Id.Value != 72,
      "still on {0}".format(free_line.SketchPlane.Id))
check("the sketch line is left exactly as it was",
      sketch_line.SketchPlane.Id.Value == 72,
      "moved to {0}".format(sketch_line.SketchPlane.Id))
check("the probe rolled back, so it left no stray planes behind",
      len([e for e in doc.elements.values()
           if isinstance(e, FakeSketchPlane)]) == 2,
      "{0} planes".format(len([e for e in doc.elements.values()
                               if isinstance(e, FakeSketchPlane)])))
check("and the level is not deleted, since the sketch line still holds it",
      100 not in doc.deleted, "deleted {0}".format(doc.deleted))


# --- 6g. rooms, which cannot be moved by any parameter ----------------
#
# Björn's own model proved ROOM_LEVEL_ID is read only, in the API and in
# the interface. The route a person takes is cut and paste. The tool
# takes the same route by a better road: unplace the room, place it again
# in an empty enclosed area on the target level, put it back at its own
# coordinates. That keeps the room number, which a copy does not, and on
# a live project the room number is schedule data.

def room_scenario(circuits_on_target, x=10.0, y=10.0):
    lower, upper = two_levels()
    room = FakeRoom(60, u"Skrifstofa", lower.Id, x=x, y=y,
                    upper_level_param="ROOM_UPPER_LEVEL")
    doc = FakeDoc([lower, upper, room])
    doc.set_topology(upper, circuits_on_target)
    return doc, [lower, upper], room


doc, levels, room = room_scenario([FakeCircuit(10.0, 10.0)])
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move the rooms", "Delete levels"])

check("a room is offered as its own step, not as stuck",
      "1 room(s) to re-place" in run["text"],
      "{0}".format(run["text"][:500]))
check("the report warns it does not keep its elevation",
      "not** keep their elevation" in run["text"]
      or "not keep their elevation" in run["text"])
check("the room ends up on the target level",
      room.level_id.Value == 200, "level_id {0}".format(room.level_id))
check("it keeps its number, which is why it is not a copy",
      room.Number == "101", "number {0}".format(room.Number))
check("it is back at its own coordinates",
      close(room.Location.Point.X, 10.0)
      and close(room.Location.Point.Y, 10.0),
      "at {0},{1}".format(room.Location.Point.X, room.Location.Point.Y))
check("it is enclosed where it landed", room.Area > 0,
      "area {0}".format(room.Area))
check("its upper limit followed it, or the level would still be held",
      room.top_level_id.Value == 200,
      "top_level_id {0}".format(room.top_level_id))
check("so the level is free and gets deleted", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))


# --- 6h. a target level with nowhere to put it ------------------------

doc, levels, room = room_scenario([])
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Delete levels"])

check("with no free enclosed area the room is demoted to stuck",
      "0 room(s) to re-place" in run["text"],
      "{0}".format(run["text"][:500]))
check("and the reason says rooms need bounding geometry",
      "bounding geometry" in run["text"])
check("the room is left placed exactly where it was",
      room.level_id.Value == 100 and room.Area > 0
      and close(room.Location.Point.X, 10.0),
      "level {0}, area {1}".format(room.level_id, room.Area))
check("and the level is not deleted", 100 not in doc.deleted)


# --- 6i. an area too far away to hold it at its own coordinates -------

doc, levels, room = room_scenario([FakeCircuit(400.0, 400.0, reach=1.0)])
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Delete levels"])

check("an area that cannot hold the room at its coordinates is rejected",
      "0 room(s) to re-place" in run["text"],
      "{0}".format(run["text"][:500]))
check("the reason names how many were tried and why each failed",
      "none of them worked" in run["text"]
      and "stay enclosed" in run["text"],
      "{0}".format(run["text"][-400:]))
check("and the probe left the room placed, not stranded",
      room.Area > 0 and room.Location.Point is not None,
      "area {0}".format(room.Area))
check("still on its original level",
      room.level_id.Value == 100, "level_id {0}".format(room.level_id))


# --- 6j. the nearest area is chosen, not the first ---------------------

# The far one can hold the room too, so only the ordering decides. With
# a smaller reach it would fail the coordinate check and the room would
# fall through to the near one anyway, and the sort would be untested.
near = FakeCircuit(11.0, 11.0)
far = FakeCircuit(300.0, 300.0, reach=1000.0)
doc, levels, room = room_scenario([far, near])
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move the rooms", "Delete levels"])

check("the room lands in the area nearest its own position",
      room.circuit is near, "landed in {0}".format(room.circuit))
check("and is nudged back to its exact coordinates",
      close(room.Location.Point.X, 10.0),
      "at {0}".format(room.Location.Point.X))


# --- 6j2. one room failing takes the whole room step with it ----------
#
# An unplaced room is worse than a room that never moved, so a failure
# after the probe has passed rolls everything back rather than leaving
# the model half done.

lower, upper = two_levels()
good_circuit = FakeCircuit(10.0, 10.0)
flaky_circuit = FakeCircuit(50.0, 50.0, refuse_from_call=2)
room_a = FakeRoom(62, u"Skrifstofa", lower.Id, x=10.0, y=10.0)
room_b = FakeRoom(63, u"Fundarherbergi", lower.Id, x=50.0, y=50.0)
doc = FakeDoc([lower, upper, room_a, room_b])
doc.set_topology(upper, [good_circuit, flaky_circuit])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Move the rooms", "Delete levels"])

check("both rooms pass the probe, so both are offered",
      "2 room(s) to re-place" in run["text"],
      "{0}".format(run["text"][:500]))
check("the room that fails for real is reported",
      "refused the room" in run["text"], "{0}".format(run["text"][-500:]))
check("the whole room step is rolled back, not partly applied",
      "rolled back in full" in run["text"],
      "{0}".format(run["text"][-400:]))
check("so the room that would have succeeded is left alone too",
      room_a.level_id.Value == 100,
      "level_id {0}".format(room_a.level_id))
check("and neither room is left unplaced",
      room_a.Location.Point is not None
      and room_b.Location.Point is not None)
check("no level is deleted after a rolled back room step",
      100 not in doc.deleted, "deleted {0}".format(doc.deleted))


# --- 6j3. the room Revit throws away on commit ------------------------
#
# The a live project failure, reconstructed. A room whose upper limit is
# anchored to a level that is not moving. Move its floor above that
# anchor and the height goes negative, and Revit rejects it **during the
# commit**, which a rollback based probe can never see. The tool
# reported the room as moved and then, three lines later, listed the
# same room as still on the level being deleted.

lower = FakeLevel(100, u"E01", 0.0)
target = FakeLevel(200, u"E02", 5000.0 * MM)
ceiling = FakeLevel(300, u"Loft", 2000.0 * MM)
circuit = FakeCircuit(10.0, 10.0)
room = FakeRoom(64, u"VENTILATION SPACE", lower.Id, x=10.0, y=10.0,
                base_offset=0.0, upper_offset=0.0)
room.top_level_id = ceiling.Id
doc = FakeDoc([lower, target, ceiling, room])
doc.set_topology(target, [circuit])
run = run_script(doc, [lower, target, ceiling],
                 picks=[[u"E01"], u"E02"], answers=["Delete levels"])

check("the room is refused before anything is written",
      "0 room(s) to re-place" in run["text"],
      "{0}".format(run["text"][:600]))
check("the reason gives the height it would have ended up with",
      "-3000.0 mm" in run["text"], "{0}".format(run["text"][:900]))
check("and says why: the upper limit is anchored elsewhere",
      "anchored to a level that is not moving" in run["text"])
check("nothing claims the room moved",
      "1 room(s) moved" not in run["text"])
check("the room is untouched on its original level",
      room.level_id.Value == 100 and room.top_level_id.Value == 300,
      "level {0}, upper {1}".format(room.level_id, room.top_level_id))


# --- 6j4. a commit Revit rolls back is never reported as success ------
#
# The guard above catches the case we understand. This covers the ones we
# do not: Transaction.Commit returns a status, and the first version
# ignored it, which is the same defect as the original pyApex script
# discarding the bool from SetValueString.

lower = FakeLevel(100, u"E01", 0.0)
target = FakeLevel(200, u"E02", 3000.0 * MM)
circuit = FakeCircuit(10.0, 10.0)
room = FakeRoom(65, u"Skrifstofa", lower.Id, x=10.0, y=10.0,
                upper_offset=2500.0 * MM)
doc = FakeDoc([lower, target, room])
doc.set_topology(target, [circuit])
# Something outside the tool's arithmetic makes the commit invalid: an
# unrelated room already in the model with no height.
stowaway = FakeRoom(66, u"Ógilt", target.Id, x=900.0, y=900.0,
                    base_offset=0.0, upper_offset=0.0)
doc.elements[66] = stowaway
doc._snapshot()
run = run_script(doc, [lower, target], picks=[[u"E01"], u"E02"],
                 answers=["Move the rooms", "Delete levels"])

check("a rolled back commit is never counted as a move",
      "1 room(s) moved" not in run["text"],
      "{0}".format(run["text"][-600:]))
check("it says Revit rolled it back",
      "rolled it back during the commit" in run["text"],
      "{0}".format(run["text"][-600:]))
check("carrying Revit's own words",
      "height greater than 0" in run["text"],
      "{0}".format(run["text"][-600:]))
check("and the room really is still on its old level, as reported",
      room.level_id.Value == 100, "level_id {0}".format(room.level_id))


# --- 6k. declining leaves the room alone ------------------------------

doc, levels, room = room_scenario([FakeCircuit(10.0, 10.0)])
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Cancel", "Delete levels"])

check("declining leaves the room on its level",
      room.level_id.Value == 100, "level_id {0}".format(room.level_id))
check("placed and enclosed, not unplaced by the probe",
      room.Area > 0 and room.Location.Point is not None)
check("and the level survives", 100 not in doc.deleted)
check("which the report says", "left the rooms where they are"
      in run["text"])


# --- 6f. deleting a level that still has real elements on it ----------
#
# Revit's delete cascades, so forcing needs no extra deletion step. It
# needs a confirmation that says what goes.

lower, upper = two_levels()
stubborn = FakeElement(80, u"Rooms", u"Skrifstofa",
                       locked_level_param="ROOM_LEVEL_ID",
                       level_id=lower.Id)
doc = FakeDoc([lower, upper, stubborn])
run = run_script(doc, [lower, upper], picks=[[u"E01"], u"E02"],
                 answers=["Delete the level and these"])

check("a blocked level is offered for deletion anyway",
      any("still has 1 real element" in text for text in run["dialogs"]),
      "{0}".format(run["dialogs"]))
check("the offer names what would be lost, by kind and count",
      any("1 x Rooms / Skrifstofa" in text for text in run["dialogs"]),
      "{0}".format(run["dialogs"]))
check("the offer says the elements go with it",
      any("deletes these too" in text for text in run["dialogs"]))
check("saying yes deletes the level", 100 in doc.deleted,
      "deleted {0}".format(doc.deleted))
check("and the report records that it was on your say so",
      "on your say so" in run["text"])

doc = FakeDoc([FakeLevel(100, u"E01", 0.0), FakeLevel(200, u"E02", 3000.0 * MM),
               FakeElement(81, u"Rooms", u"Skrifstofa",
                           locked_level_param="ROOM_LEVEL_ID",
                           level_id=FakeId(100))])
levels = [doc.elements[100], doc.elements[200]]
run = run_script(doc, levels, picks=[[u"E01"], u"E02"], answers=["Cancel"])
check("declining leaves the level and its elements alone",
      100 not in doc.deleted, "deleted {0}".format(doc.deleted))
check("and it is reported as left alone",
      "leaving it alone" in run["text"])
check("and the run says plainly that nothing was deleted",
      "no level was deleted" in run["text"],
      "{0}".format(run["text"][-300:]))


# --- 7. the dry run is a gate, not a formality ------------------------

doc, levels, wall, door = scenario()
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Cancel"])

check("cancelling writes nothing to the elements",
      wall.level_id.Value == 100 and close(wall.offset, 500.0 * MM))
check("cancelling deletes nothing", not doc.deleted,
      "deleted {0}".format(doc.deleted))
check("a dry run was still printed", "dry run" in run["text"])
check("no transaction was ever committed",
      not any(kind == "commit" for kind, _n in run["transactions"]))
check("the probe rolled itself back",
      any(kind == "rollback" for kind, _n in run["transactions"]))


# --- 8. a half written element rolls the whole run back ---------------

doc, levels, wall, door = scenario(offset_fails=True)
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move elements", "Delete levels"])

check("an element whose offset write fails is rolled back, not left on "
      "the new level", wall.level_id.Value == 100,
      "level_id {0}".format(wall.level_id))
check("and the element that did succeed is rolled back with it",
      door.level_id.Value == 100, "level_id {0}".format(door.level_id))
check("the move transaction was never committed",
      not any(kind == "commit" and "move" in name
              for kind, name in run["transactions"]))
check("the rollback is reported rather than passed over",
      "rolled the whole move back" in run["text"])
check("nothing was deleted after a rolled back move", not doc.deleted)


# --- 9. read only offset, declined then opted into --------------------

doc, levels, wall, door = scenario(offset_read_only=True)
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move, leave those alone", "Delete levels"])
check("a read only offset makes the element a shift",
      "physically move" in run["text"])
check("declining leaves it on its original level",
      wall.level_id.Value == 100)
check("while the element that could move cleanly still moved",
      door.level_id.Value == 200)

doc, levels, wall, door = scenario(offset_read_only=True)
run = run_script(doc, levels, picks=[[u"E01"], u"E02"],
                 answers=["Move, including those", "Delete levels"])
check("opting in moves it", wall.level_id.Value == 200)
check("its offset was left untouched, which is why it is a shift",
      close(wall.offset, 500.0 * MM))


# --- 10. picking nothing does nothing ---------------------------------

doc, levels, wall, door = scenario()
run = run_script(doc, levels, picks=[None], answers=[])
check("cancelling the first picker stops immediately",
      wall.level_id.Value == 100 and not doc.deleted)
check("and no dialog was shown", not run["dialogs"])

doc, levels, wall, door = scenario()
run = run_script(doc, levels, picks=[[u"E01"], None], answers=[])
check("cancelling the target picker stops before any write",
      wall.level_id.Value == 100 and not doc.deleted)


# --- 11. prove these assertions can fail ------------------------------

check("a deliberately false assertion is recorded as failing",
      not close(1.0, 2.0))
check("check() stores a real boolean", isinstance(results[0][1], bool),
      "got {0}".format(type(results[0][1]).__name__))


# --- report -----------------------------------------------------------

print("Remove Level script harness")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if ok:
        print("  [  ok] {0}".format(name))
    else:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
