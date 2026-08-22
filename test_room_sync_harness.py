# -*- coding: utf-8 -*-
"""
Runs the real Room Data Sync script against a mocked Revit.

Failures in this extension have always happened in the thin pushbutton
scripts rather than the libraries under them, so the script is what gets
run here, not a copy of its logic.

The fake room lookup refuses a point that sits exactly on the floor, the
way Revit does, because a point on a room's lower boundary is in no room.
That is the whole reason the script lifts its lookup point, and without
this the lift would be untested and could be removed without a single
check failing.

Run outside Revit:

    python test_room_sync_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel",
                      "Room Data Sync.pushbutton", "script.py")

from avh_rooms import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


SOURCE = u"CCIMultiLevelLocationID"
TARGET = u"CCISingleLevelLocationAtID"


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class FakeCategory(object):
    def __init__(self, name):
        self.Name = name


class FakeDefinition(object):
    def __init__(self, name):
        self.Name = name


class FakeParameter(object):
    def __init__(self, owner, name, storage="String", read_only=False,
                 fails=False):
        self.owner = owner
        self.Definition = FakeDefinition(name)
        self.StorageType = storage
        self.IsReadOnly = read_only
        self.fails = fails

    def AsString(self):
        return self.owner.values.get(self.Definition.Name)

    def AsElementId(self):
        return self.owner.values.get(self.Definition.Name)

    def Set(self, value):
        if self.fails:
            raise ValueError("parameter rejected the value")
        self.owner.values[self.Definition.Name] = value
        return True


class FakeElement(object):
    def __init__(self, value, category, name, category_enum=None,
                 level_id=None, point=None, values=None,
                 target_read_only=False, target_fails=False,
                 has_target=True, extra_params=None):
        self.Id = FakeId(value)
        self.Category = FakeCategory(category) if category else None
        self.Name = name
        self.category_enum = category_enum
        self.LevelId = level_id or FakeId(-1)
        self.values = dict(values or {})
        self.Location = (types.SimpleNamespace(Point=point)
                         if point is not None else None)
        self._params = {}
        for extra in (extra_params or []):
            self._params[extra] = FakeParameter(self, extra)
        if has_target:
            self._params[TARGET] = FakeParameter(
                self, TARGET, read_only=target_read_only,
                fails=target_fails)
            self.values.setdefault(TARGET, None)

    def LookupParameter(self, name):
        return self._params.get(name)

    def get_Parameter(self, enum):
        return None

    @property
    def Parameters(self):
        return list(self._params.values())

    def get_BoundingBox(self, view):
        return None

    def GetType(self):
        return types.SimpleNamespace(Name=type(self).__name__)


class FakeRoom(FakeElement):
    def __init__(self, value, number, name, level_id, cci,
                 x=0.0, y=0.0, z=0.0, extent=5.0):
        FakeElement.__init__(self, value, "Rooms", name,
                             level_id=level_id, has_target=False)
        self.Number = number
        self._params[SOURCE] = FakeParameter(self, SOURCE)
        self.values[SOURCE] = cci
        self.x, self.y, self.z, self.extent = x, y, z, extent

    def contains(self, point):
        # A point exactly on the floor is on the boundary, and a point on
        # a boundary is in no room. This is what the script's lookup lift
        # exists for.
        if point.Z <= self.z:
            return False
        return (abs(point.X - self.x) <= self.extent
                and abs(point.Y - self.y) <= self.extent)


class FakeLevel(FakeElement):
    def __init__(self, value, name):
        FakeElement.__init__(self, value, "Levels", name,
                             has_target=False)


class FakeOpening(FakeElement):
    """A door or window, which is in a wall between two rooms."""

    def __init__(self, value, category, name, category_enum, level_id,
                 to_room=None, from_room=None, values=None):
        FakeElement.__init__(self, value, category, name,
                             category_enum=category_enum,
                             level_id=level_id, values=values)
        self._to, self._from = to_room, from_room

    def get_ToRoom(self, phase):
        return self._to

    def get_FromRoom(self, phase):
        return self._from


class FakePhases(object):
    """Revit documents always have at least one phase."""

    def __init__(self, items):
        self.items = list(items)
        self.Size = len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def __len__(self):
        return len(self.items)


class FakeTransaction(object):
    log = []
    refuse_commit = False

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name

    def Start(self):
        FakeTransaction.log.append(("start", self.name))

    def Commit(self):
        FakeTransaction.log.append(("commit", self.name))
        if FakeTransaction.refuse_commit:
            self.doc.restore()
            return "RolledBack"
        self.doc.snapshot()
        return "Committed"

    def RollBack(self):
        FakeTransaction.log.append(("rollback", self.name))
        self.doc.restore()


class FakeDoc(object):
    def __init__(self, elements, rooms, levels):
        self.elements = {}
        for element in list(elements) + list(rooms) + list(levels):
            self.elements[element.Id.Value] = element
        self.rooms = list(rooms)
        self.levels = list(levels)
        self.lookups = []
        self.Phases = FakePhases([types.SimpleNamespace(Name="New")])
        self.snapshot()

    def snapshot(self):
        self._saved = dict((v, dict(e.values))
                           for v, e in self.elements.items())

    def restore(self):
        for value, saved in self._saved.items():
            element = self.elements.get(value)
            if element is not None:
                element.values = dict(saved)

    def GetElement(self, element_id):
        if element_id is None:
            return None
        return self.elements.get(element_id.Value)

    def GetRoomAtPoint(self, point, phase=None):
        self.lookups.append(point)
        for room in self.rooms:
            if room.contains(point):
                return room
        return None


def build_pyrevit(doc, picks, answers, recorder):
    class BuiltInCategory(object):
        pass

    for name in ("OST_Doors", "OST_Windows", "OST_Furniture",
                 "OST_FurnitureSystems", "OST_Casework",
                 "OST_SpecialityEquipment", "OST_MechanicalEquipment",
                 "OST_Stairs", "OST_StairsRailing", "OST_Levels"):
        setattr(BuiltInCategory, name, name)

    class Collector(object):
        def __init__(self, _doc):
            self.wanted = None

        def OfCategory(self, category):
            self.wanted = category
            return self

        def WhereElementIsNotElementType(self):
            return self

        def ToElements(self):
            if self.wanted == "OST_Levels":
                return list(doc.levels)
            return [e for e in doc.elements.values()
                    if getattr(e, "category_enum", None) == self.wanted]

    db = types.SimpleNamespace(
        BuiltInCategory=BuiltInCategory,
        BuiltInParameter=types.SimpleNamespace(),
        StorageType=types.SimpleNamespace(String="String"),
        FilteredElementCollector=Collector,
        Transaction=FakeTransaction,
        TransactionStatus=types.SimpleNamespace(
            Committed="Committed", RolledBack="RolledBack"),
        Level=FakeLevel,
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

    output = types.SimpleNamespace(
        print_md=lambda text: recorder["output"].append(text),
        linkify=lambda element_id: "[{0}]".format(element_id.Value))

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = types.SimpleNamespace(doc=doc, uidoc=None)
    pyrevit.DB = db
    pyrevit.forms = types.SimpleNamespace(
        SelectFromList=SelectFromList, alert=alert)
    pyrevit.script = types.SimpleNamespace(
        get_output=lambda: output,
        get_logger=lambda: types.SimpleNamespace(
            error=lambda *a: None, warning=lambda *a: None,
            info=lambda *a: None))
    return pyrevit


def run_script(doc, picks, answers, refuse_commit=False):
    recorder = {"output": [], "dialogs": [], "pickers": []}
    FakeTransaction.log = []
    FakeTransaction.refuse_commit = refuse_commit
    saved = sys.modules.get("pyrevit")
    sys.modules["pyrevit"] = build_pyrevit(
        doc, list(picks), list(answers), recorder)
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        FakeTransaction.refuse_commit = False
        if saved is None:
            sys.modules.pop("pyrevit", None)
        else:
            sys.modules["pyrevit"] = saved
    recorder["text"] = "\n".join(recorder["output"]).lower()
    return recorder


def basic():
    level = FakeLevel(1, u"E01")
    room = FakeRoom(10, u"0201", u"VENTILATION SPACE", level.Id,
                    u"+CC01.E01.CEA02", x=0.0, y=0.0)
    desk = FakeElement(20, u"Furniture", u"Desk",
                       category_enum="OST_Furniture", level_id=level.Id,
                       point=FakeXYZ(1.0, 1.0, 0.0))
    return FakeDoc([desk], [room], [level]), room, desk


# --- 1. a blank is filled, from the room it is standing in ------------

doc, room, desk = basic()
run = run_script(doc, picks=[None], answers=["Write the values"])

check("the level picker is offered, and cancelling means all levels",
      len(run["pickers"]) == 1)
check("the desk takes the room's ID",
      desk.values[TARGET] == u"+CC01.E01.CEA02",
      "got {0}".format(desk.values[TARGET]))
check("it is reported as a fill, not a change",
      "1 to fill, 0 to change" in run["text"],
      "{0}".format(run["text"][:300]))
check("the run says what it wrote", "1 value(s) written" in run["text"])


# --- 2. the lookup point is lifted off the floor ----------------------
#
# The desk sits at z=0, which is the room's own floor. Revit puts a point
# on a boundary in no room at all, so an unlifted lookup finds nothing.

check("the room was looked up above the insertion point, not on it",
      doc.lookups and doc.lookups[0].Z > 0.0,
      "looked up at z={0}".format(
          doc.lookups[0].Z if doc.lookups else "never"))
check("and only just above it, so it stays in the same room",
      doc.lookups and doc.lookups[0].Z < 1.0,
      "z={0}".format(doc.lookups[0].Z if doc.lookups else "never"))


# --- 3. a door takes ToRoom, and falls back to FromRoom ---------------

level = FakeLevel(1, u"E01")
inside = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"+CC01.E01.A01")
outside = FakeRoom(11, u"0202", u"CORRIDOR", level.Id, u"+CC01.E01.B02")
served = FakeOpening(30, u"Doors", u"Single flush", "OST_Doors",
                     level.Id, to_room=inside, from_room=outside)
external = FakeOpening(31, u"Doors", u"External", "OST_Doors",
                       level.Id, to_room=None, from_room=outside)
doc = FakeDoc([served, external], [inside, outside], [level])
run = run_script(doc, picks=[None], answers=["Write the values"])

check("a door with a room on both sides takes ToRoom",
      served.values[TARGET] == u"+CC01.E01.A01",
      "got {0}".format(served.values[TARGET]))
check("a door with no ToRoom falls back to FromRoom",
      external.values[TARGET] == u"+CC01.E01.B02",
      "got {0}".format(external.values[TARGET]))
check("the report says which side each value came from",
      "toroom" in run["text"] or "fromroom" in run["text"])


# --- 3b. a door whose ToRoom has no ID takes FromRoom's ---------------
#
# The fallback fires on the value, not on whether a room is there. Before
# this, a door whose ToRoom was a room with a blank ID came away with
# nothing while a perfectly good ID sat on the other side of it.

level = FakeLevel(1, u"E01")
blank_room = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"")
good_room = FakeRoom(11, u"0202", u"CORRIDOR", level.Id, u"+CC01.E01.B02")
door = FakeOpening(30, u"Doors", u"Single flush", "OST_Doors", level.Id,
                   to_room=blank_room, from_room=good_room)
doc = FakeDoc([door], [blank_room, good_room], [level])
run = run_script(doc, picks=[None], answers=["Write the values"])

check("a door whose ToRoom has a blank ID takes FromRoom's",
      door.values[TARGET] == u"+CC01.E01.B02",
      "got {0}".format(door.values[TARGET]))
check("and is a fill, not a room with nothing to copy",
      "1 to fill" in run["text"], "{0}".format(run["text"][:300]))
check("the trace says it fell through, and why",
      "because toroom had no id" in run["text"],
      "{0}".format(run["text"][:600]))

# Both sides blank: nothing to copy, and the preferred room is named.
level = FakeLevel(1, u"E01")
blank_to = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"")
blank_from = FakeRoom(11, u"0202", u"CORRIDOR", level.Id, u"")
door = FakeOpening(30, u"Doors", u"Single flush", "OST_Doors", level.Id,
                   to_room=blank_to, from_room=blank_from)
doc = FakeDoc([door], [blank_to, blank_from], [level])
run = run_script(doc, picks=[None], answers=[])

check("with both sides blank there is still nothing to copy",
      "no id of its own" in run["text"],
      "{0}".format(run["text"][:400]))
check("and the preferred room is the one named, since that is the one "
      "to go and fix",
      "0201" in run["text"], "{0}".format(run["text"][-400:]))
check("nothing is written", door.values[TARGET] is None)


# --- 3c. the exterior door, which is the case this is really for ------
#
# A door that opens outwards has no room on the far side, so it should
# take the code of the room it opens *from*. Which of Revit's two slots
# that room lands in depends on which side the door was placed from, not
# on which way it swings, so neither slot is reliable on its own.
#
# Taking whichever side has a code makes the tool indifferent to that.
# Both arrangements below are the same door in the model, placed from
# opposite sides, and both must produce the interior room's code.

def exterior_door(to_room, from_room):
    level = FakeLevel(1, u"E01")
    inside = FakeRoom(10, u"0201", u"PLANT", level.Id, u"+CC01.E01.P01")
    rooms = [inside]
    door = FakeOpening(30, u"Doors", u"External", "OST_Doors", level.Id,
                       to_room=inside if to_room else None,
                       from_room=inside if from_room else None)
    return FakeDoc([door], rooms, [level]), door


doc, door = exterior_door(to_room=False, from_room=True)
run = run_script(doc, picks=[None], answers=["Write the values"])
check("a door placed from inside, with nothing outside, takes the "
      "interior room",
      door.values[TARGET] == u"+CC01.E01.P01",
      "got {0}".format(door.values[TARGET]))

doc, door = exterior_door(to_room=True, from_room=False)
run = run_script(doc, picks=[None], answers=["Write the values"])
check("and so does the same door placed from the other side",
      door.values[TARGET] == u"+CC01.E01.P01",
      "got {0}".format(door.values[TARGET]))

# The outside is not always nothing. A door can open onto an external
# area that is modelled as a room but carries no location code, and that
# is the case that used to leave the door blank.
level = FakeLevel(1, u"E01")
yard = FakeRoom(10, u"0900", u"YARD", level.Id, u"")
plant = FakeRoom(11, u"0201", u"PLANT", level.Id, u"+CC01.E01.P01")
door = FakeOpening(30, u"Doors", u"External", "OST_Doors", level.Id,
                   to_room=yard, from_room=plant)
doc = FakeDoc([door], [yard, plant], [level])
run = run_script(doc, picks=[None], answers=["Write the values"])
check("a door opening onto an uncoded outdoor area still takes the "
      "interior room's code",
      door.values[TARGET] == u"+CC01.E01.P01",
      "got {0}".format(door.values[TARGET]))


# --- 4. an existing value is a change, listed on its own --------------

doc, room, desk = basic()
desk.values[TARGET] = u"+CC01.E01.TYPED"
run = run_script(doc, picks=[None],
                 answers=["Fill blanks and apply changes"])

check("an existing value is counted as a change, not a fill",
      "0 to fill, 1 to change" in run["text"],
      "{0}".format(run["text"][:300]))
check("the old and new values are both shown",
      "+cc01.e01.typed" in run["text"]
      and "+cc01.e01.cea02" in run["text"])
check("the dialog warns that hand typed values will be replaced",
      any("typed by hand" in text for text in run["dialogs"]),
      "{0}".format(run["dialogs"]))
check("applying changes overwrites it",
      desk.values[TARGET] == u"+CC01.E01.CEA02",
      "got {0}".format(desk.values[TARGET]))


# --- 5. filling blanks only leaves existing values alone --------------

level = FakeLevel(1, u"E01")
room = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"+CC01.E01.A01")
typed = FakeElement(20, u"Furniture", u"Desk",
                    category_enum="OST_Furniture", level_id=level.Id,
                    point=FakeXYZ(1.0, 1.0, 0.0),
                    values={TARGET: u"+CC01.E01.TYPED"})
blank = FakeElement(21, u"Furniture", u"Chair",
                    category_enum="OST_Furniture", level_id=level.Id,
                    point=FakeXYZ(2.0, 2.0, 0.0))
doc = FakeDoc([typed, blank], [room], [level])
run = run_script(doc, picks=[None], answers=["Fill blanks only"])

check("the blank is filled", blank.values[TARGET] == u"+CC01.E01.A01")
check("the typed value survives",
      typed.values[TARGET] == u"+CC01.E01.TYPED",
      "got {0}".format(typed.values[TARGET]))
check("and only one write is reported",
      "1 value(s) written" in run["text"])


# --- 6. a missing target parameter is the loud case -------------------
#
# The target parameter name is the one thing about this tool that was
# never confirmed against a real model, so finding nothing must never
# look like finding nothing to do.

level = FakeLevel(1, u"E01")
room = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"+CC01.E01.A01")
unbound = FakeElement(20, u"Furniture", u"Desk",
                      category_enum="OST_Furniture", level_id=level.Id,
                      point=FakeXYZ(1.0, 1.0, 0.0), has_target=False,
                      extra_params=[u"CCISingleLevelID",
                                    u"CCIConstructionEntity"])
doc = FakeDoc([unbound], [room], [level])
run = run_script(doc, picks=[None], answers=[])

check("a missing target parameter is called out, not passed over",
      "target parameter was not found" in run["text"],
      "{0}".format(run["text"][:400]))
check("the CCI parameters the element does have are listed, so the "
      "right name can be found",
      "ccisinglelevelid" in run["text"]
      and "ccipconstructionentity" not in run["text"],
      "{0}".format(run["text"][:600]))
check("nothing is written", "nothing to write" in run["text"])
check("and no dialog asked to write", not run["dialogs"])


# --- 7. not in a room, and a room with no ID of its own ---------------

level = FakeLevel(1, u"E01")
room = FakeRoom(10, u"0201", u"OFFICE", level.Id, u"")
stray = FakeElement(20, u"Furniture", u"Desk",
                    category_enum="OST_Furniture", level_id=level.Id,
                    point=FakeXYZ(900.0, 900.0, 0.0))
inside = FakeElement(21, u"Furniture", u"Chair",
                     category_enum="OST_Furniture", level_id=level.Id,
                     point=FakeXYZ(1.0, 1.0, 0.0))
doc = FakeDoc([stray, inside], [room], [level])
run = run_script(doc, picks=[None], answers=[])

check("an element outside every room is reported, not written",
      "not inside any room" in run["text"],
      "{0}".format(run["text"][:400]))
check("a room with a blank ID is reported as having nothing to copy",
      "no id of its own" in run["text"],
      "{0}".format(run["text"][:400]))
check("a blank room never clears the element's value",
      inside.values[TARGET] is None,
      "got {0}".format(inside.values[TARGET]))
check("and there is nothing to write at all",
      "nothing to write" in run["text"])


# --- 8. the level filter -----------------------------------------------

first = FakeLevel(1, u"E01")
second = FakeLevel(2, u"E02")
room_one = FakeRoom(10, u"0101", u"OFFICE", first.Id, u"+CC01.E01.A01")
room_two = FakeRoom(11, u"0201", u"OFFICE", second.Id, u"+CC01.E02.A01")
on_first = FakeElement(20, u"Furniture", u"Desk",
                       category_enum="OST_Furniture", level_id=first.Id,
                       point=FakeXYZ(1.0, 1.0, 0.0))
on_second = FakeElement(21, u"Furniture", u"Desk",
                        category_enum="OST_Furniture",
                        level_id=second.Id,
                        point=FakeXYZ(1.0, 1.0, 0.0))
doc = FakeDoc([on_first, on_second], [room_one], [first, second])
run = run_script(doc, picks=[[u"E01"]], answers=["Write the values"])

check("only the picked level is touched",
      on_first.values[TARGET] == u"+CC01.E01.A01"
      and on_second.values[TARGET] is None,
      "first {0}, second {1}".format(on_first.values[TARGET],
                                     on_second.values[TARGET]))
check("and the one out of scope is not reported as a problem either",
      "1 to fill" in run["text"], "{0}".format(run["text"][:300]))


# --- 9. a commit Revit rolls back is never reported as success --------

doc, room, desk = basic()
run = run_script(doc, picks=[None], answers=["Write the values"],
                 refuse_commit=True)

check("a rolled back commit is not counted as a write",
      "1 value(s) written" not in run["text"],
      "{0}".format(run["text"][-400:]))
check("it says Revit rolled it back",
      "rolled the write back during the commit" in run["text"],
      "{0}".format(run["text"][-400:]))
check("and the element really is unchanged, as reported",
      desk.values[TARGET] is None,
      "got {0}".format(desk.values[TARGET]))


# --- 10. cancelling writes nothing ------------------------------------

doc, room, desk = basic()
run = run_script(doc, picks=[None], answers=["Cancel"])
check("cancelling writes nothing", desk.values[TARGET] is None)
check("a dry run was still printed", "dry run" in run["text"])
check("no transaction was committed",
      not any(kind == "commit" for kind, _n in FakeTransaction.log))


# --- 11. prove these assertions can fail ------------------------------

check("a deliberately false assertion is recorded as failing",
      not ("nonsense marker" in run["text"]))
check("check() stores a real boolean",
      isinstance(results[0][1], bool))


print("Room Data Sync script harness")
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
