# -*- coding: utf-8 -*-
"""
Runs the real Place at Coordinate script against a mocked Revit.

The thing worth testing is the number. A marker in the wrong place looks
exactly like a marker in the right place, so every scenario checks where
the instance actually landed, in shared coordinates, against what was
typed.

The fake project location is a real rotation about a real ISN93 sized
origin, not an identity, because an identity transform passes whether or
not the script inverts anything.

Run outside Revit:

    python test_place_coordinate_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Place at Coordinate.pushbutton", "script.py")

from avh_coords import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def safely(function, *args):
    """Call it, and turn a raise into a value that fails a check.

    A check that raises aborts the run and hides every check after it.
    Two mutations did exactly that here: inverting the transform the
    wrong way left no instance for a later check to index, and breaking
    the comma decimal raised out of the arithmetic section before any
    scenario ran. Both should be failures somebody can read, not a
    traceback that hides the other sixty.
    """
    try:
        return function(*args)
    except Exception as exc:
        return u"<raised {0}>".format(exc)


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


FEET = 1.0 / model.METRES_PER_FOOT

# A project rotated 30 degrees, origin somewhere in Reykjanes.
ANGLE = math.radians(30.0)
ORIGIN_E = 1352000.0 * FEET
ORIGIN_N = 1329000.0 * FEET
ORIGIN_Z = 12.0 * FEET


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X = x
        self.Y = y
        self.Z = z

    def tuple(self):
        return (self.X, self.Y, self.Z)


class FakePosition(object):
    def __init__(self, east, north, elevation):
        self.EastWest = east
        self.NorthSouth = north
        self.Elevation = elevation


class FakeProjectLocation(object):
    """A real rotation, so inverting it is real work."""

    def __init__(self, degenerate=False, raises=False):
        self.degenerate = degenerate
        self.raises = raises

    def GetProjectPosition(self, point):
        if self.raises:
            raise Exception("the project location is unavailable")
        if self.degenerate:
            return FakePosition(ORIGIN_E, ORIGIN_N, ORIGIN_Z)
        east = ORIGIN_E + point.X * math.cos(ANGLE) - point.Y * math.sin(ANGLE)
        north = ORIGIN_N + point.X * math.sin(ANGLE) + point.Y * math.cos(ANGLE)
        return FakePosition(east, north, ORIGIN_Z + point.Z)


class FakeParameter(object):
    def __init__(self, read_only=False, refuses=False):
        self.value = None
        self.IsReadOnly = read_only
        self.refuses = refuses

    def Set(self, value):
        if self.refuses:
            return False
        self.value = value
        return True


class FakeFamily(object):
    def __init__(self, name):
        self.Name = name


class FakeSymbol(object):
    def __init__(self, family_name):
        self.Family = FakeFamily(family_name)
        self.IsActive = False
        self.activated = 0

    def Activate(self):
        self.activated += 1
        self.IsActive = True


class FakeLocation(object):
    def __init__(self, point):
        self.Point = point


class FakeInstance(object):
    def __init__(self, point, symbol, level, missing_params=(), doc=None):
        self.Document = doc
        self.Id = id(self)
        self.Location = FakeLocation(point)
        self.symbol = symbol
        self.level = level
        self.parameters = {}
        for name in ("AVH_Easting", "AVH_Northing", "AVH_Elevation",
                     "AVH_Point_Name"):
            if name not in missing_params:
                self.parameters[name] = FakeParameter()

    def LookupParameter(self, name):
        return self.parameters.get(name)


class FakeLevel(object):
    def __init__(self, name, elevation_metres):
        self.Name = name
        self.Elevation = elevation_metres * FEET


class FakeCreate(object):
    def __init__(self, doc):
        self.doc = doc

    def NewFamilyInstance(self, point, symbol, level, structural_type):
        if self.doc.place_raises:
            raise Exception("Revit refused to place the instance")
        # A scenario can force the instance somewhere other than asked,
        # which is what a transform inverted the wrong way looks like.
        actual = point
        if self.doc.place_offset is not None:
            actual = FakeXYZ(point.X + self.doc.place_offset[0],
                             point.Y + self.doc.place_offset[1],
                             point.Z + self.doc.place_offset[2])
        instance = FakeInstance(actual, symbol, level,
                                self.doc.missing_params, doc=self.doc)
        if self.doc.no_location:
            instance.Location = None
        self.doc.instances.append(instance)
        return instance


class FakeTransformUtils(object):
    """Revit moving an element, and refusing to when a scenario says so."""

    doc = None

    @staticmethod
    def MoveElement(document, element_id, delta):
        doc = FakeTransformUtils.doc
        if doc is not None and doc.move_raises:
            raise Exception("this element cannot be moved")
        for instance in doc.instances:
            if instance.Id == element_id:
                point = instance.Location.Point
                instance.Location = FakeLocation(FakeXYZ(
                    point.X + delta.X, point.Y + delta.Y, point.Z + delta.Z))
                doc.moves.append((delta.X, delta.Y, delta.Z))
                return
        raise Exception("no such element")


class FakeDocument(object):
    def __init__(self, symbols=(), levels=(), degenerate=False,
                 location_raises=False, load_succeeds=True,
                 place_offset=None, place_raises=False,
                 missing_params=(), no_location=False,
                 commit_status="Committed", move_raises=False):
        self.ActiveProjectLocation = FakeProjectLocation(
            degenerate=degenerate, raises=location_raises)
        self.symbols = list(symbols)
        self.levels = list(levels)
        self.Create = FakeCreate(self)
        self.instances = []
        self.load_succeeds = load_succeeds
        self.load_calls = []
        self.place_offset = place_offset
        self.place_raises = place_raises
        self.missing_params = set(missing_params)
        self.no_location = no_location
        self.commit_status = commit_status
        self.move_raises = move_raises
        self.moves = []
        FakeTransformUtils.doc = self

    def LoadFamily(self, path):
        self.load_calls.append(path)
        if not self.load_succeeds:
            return False
        self.symbols.append(FakeSymbol(u"AVH_Coordinate_Marker"))
        return True

    def snapshot(self):
        return list(self.instances), list(self.symbols)

    def restore(self, state):
        self.instances, self.symbols = list(state[0]), list(state[1])


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.cls = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def __iter__(self):
        if self.cls is FakeSymbol:
            return iter(list(self.doc.symbols))
        if self.cls is FakeLevel:
            return iter(list(self.doc.levels))
        return iter([])


class FakeTransaction(object):
    log = []

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = self.doc.snapshot()
        FakeTransaction.log.append("start")

    def RollBack(self):
        # Behavioural: a rollback really undoes it, so a script that
        # rolls back and then claims success fails a check.
        self.doc.restore(self.state)
        FakeTransaction.log.append("rollback")

    def Commit(self):
        FakeTransaction.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.doc.restore(self.state)
        return self.doc.commit_status


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    def __init__(self, answers, confirm=True):
        self.answers = list(answers)
        self.confirm = confirm
        self.alerts = []
        self.printed = []
        self.prompts = []

    def ask_for_string(self, default=u"", prompt=u"", title=u"", **kwargs):
        self.prompts.append(prompt)
        if not self.answers:
            return None
        return self.answers.pop(0)

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes") and kwargs.get("no"):
            return self.confirm
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, answers=(), confirm=True):
    del FakeTransaction.log[:]
    recorder = Recorder(answers, confirm=confirm)

    db = Namespace(
        XYZ=FakeXYZ,
        FilteredElementCollector=FakeCollector,
        FamilySymbol=FakeSymbol,
        Level=FakeLevel,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        Structure=Namespace(
            StructuralType=Namespace(NonStructural="NonStructural")),
        ElementTransformUtils=FakeTransformUtils,
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=None)
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              ask_for_string=recorder.ask_for_string)
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)
    return recorder


def marker(family_name=u"AVH_Coordinate_Marker"):
    return FakeSymbol(family_name)


def levels():
    return [FakeLevel(u"DA11.E01", 0.0), FakeLevel(u"DA11.E02", 4.0),
            FakeLevel(u"DA11.E03", 8.0)]


def placed_shared(doc):
    """Where the instance really ended up, in metres."""
    if not doc.instances:
        return None
    point = doc.instances[-1].Location.Point
    position = FakeProjectLocation().GetProjectPosition(point)
    return tuple(model.feet_to_metres(value) for value in
                 (position.EastWest, position.NorthSouth, position.Elevation))




def _parse_error(text, label):
    try:
        model.parse_number(text, label)
    except model.ParseError as exc:
        return u"{0}".format(exc)
    return u""


def shared_for_internal(x_m, y_m, z_m):
    """Where an internal point sits in shared coordinates, in metres."""
    point = FakeXYZ(x_m * FEET, y_m * FEET, z_m * FEET)
    position = FakeProjectLocation().GetProjectPosition(point)
    return tuple(model.feet_to_metres(value) for value in
                 (position.EastWest, position.NorthSouth, position.Elevation))


def typed_for(target, comma=True):
    """The three strings a user would type for that point."""
    out = []
    for value in target:
        text = u"{0:.3f}".format(value)
        out.append(text.replace(u".", u",") if comma else text)
    return out


# Internal (10, 20, 6) metres. Level DA11.E02 is at internal 4 m, so the
# marker should host there with a 2 m offset.
TARGET = shared_for_internal(10.0, 20.0, 6.0)
ANSWERS = typed_for(TARGET) + [u"SP-014"]


# --------------------------------------------------------------------------
# The arithmetic, no Revit at all
# --------------------------------------------------------------------------

check("parse: a period decimal", safely(model.parse_number, u"412345.678") == 412345.678)
check("parse: a comma decimal", safely(model.parse_number, u"412345,678") == 412345.678)
check("parse: a space grouping with a comma decimal",
      safely(model.parse_number, u"412 345,678") == 412345.678)
check("parse: period grouping, comma decimal",
      safely(model.parse_number, u"412.345,678") == 412345.678)
check("parse: comma grouping, period decimal",
      safely(model.parse_number, u"412,345.678") == 412345.678)
check("parse: a non breaking space pasted from a sheet",
      safely(model.parse_number, u"412\u00a0345,678") == 412345.678)
check("parse: a negative", safely(model.parse_number, u"-4,25") == -4.25)

for bad in (u"", u"   ", u"twelve", u"1,2,3.4.5"):
    raised = False
    try:
        model.parse_number(bad, u"Easting")
    except model.ParseError:
        raised = True
    check("parse: {0!r} is refused".format(bad), raised)

check("parse: the field is named in the error",
      u"Easting" in _parse_error(u"nonsense", u"Easting"))

check("level: the nearest level at or below wins",
      model.choose_level(20.0, [("a", 0.0), ("b", 15.0), ("c", 30.0)])
      == ("b", 5.0, False))
check("level: a point exactly on a level hosts on it with no offset",
      model.choose_level(15.0, [("a", 0.0), ("b", 15.0)]) == ("b", 0.0, False))
check("level: below everything hosts on the lowest and says so",
      model.choose_level(-5.0, [("a", 0.0), ("b", 15.0)]) == ("a", -5.0, True))
check("level: no levels gives nothing",
      model.choose_level(1.0, []) is None)

frame = model.frame_from_positions(
    shared_for_internal(0, 0, 0), shared_for_internal(1 * model.METRES_PER_FOOT, 0, 0),
    shared_for_internal(0, 1 * model.METRES_PER_FOOT, 0))
check("frame: a rotated project is usable", frame.usable())
check("frame: a degenerate one is not",
      not model.Frame((0, 0, 0), (0, 0), (0, 0)).usable())
check("frame: an unusable frame converts nothing",
      model.to_internal((1, 2, 3), model.Frame((0, 0, 0), (0, 0), (0, 0)))
      is None)

check("agree: a quarter millimetre is agreement",
      model.agree((1, 2, 3), (1, 2, 3.00025))[0])
check("agree: two millimetres is not",
      not model.agree((1, 2, 3), (1, 2, 3.002))[0])
check("agree: the failing axis is named",
      u"elevation" in model.describe_deltas(
          model.agree((1, 2, 3), (1, 2, 3.002))[1]))


# --------------------------------------------------------------------------
# 1. The ordinary run
# --------------------------------------------------------------------------

doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=ANSWERS)

check("place: one instance exists", len(doc.instances) == 1,
      str(len(doc.instances)))
landed = placed_shared(doc)
check("place: it landed on the typed coordinate",
      landed is not None and all(close(landed[i], TARGET[i], 1e-3)
                                 for i in range(3)),
      u"{0} vs {1}".format(landed, TARGET))
check("place: hosted on the level below the point",
      doc.instances and doc.instances[0].level.Name == u"DA11.E02",
      doc.instances[0].level.Name if doc.instances else u"none")
check("place: the report names that level",
      u"DA11.E02" in recorder.text())
check("place: the offset is reported in metres, not feet",
      u"2.000" in recorder.text(), recorder.text())
check("place: the typed values were written to parameters",
      doc.instances and
      doc.instances[0].parameters["AVH_Easting"].value == ANSWERS[0])
check("place: and the point name",
      bool(doc.instances) and
      doc.instances[0].parameters["AVH_Point_Name"].value == u"SP-014")
check("place: the symbol was activated",
      bool(doc.symbols) and doc.symbols[0].activated == 1)
check("place: committed, not rolled back",
      FakeTransaction.log == ["start", "commit"], str(FakeTransaction.log))
check("place: nothing said about being below every level",
      u"below every level" not in recorder.text())

# A period decimal has to work as well, since surveys arrive both ways.
doc = FakeDocument(symbols=[marker()], levels=levels())
run_script(doc, answers=typed_for(TARGET, comma=False) + [u"SP-014"])
landed = placed_shared(doc)
check("place: period decimals land in the same spot",
      landed is not None and all(close(landed[i], TARGET[i], 1e-3)
                                 for i in range(3)))


# --------------------------------------------------------------------------
# 2. Nothing is placed when it should not be
# --------------------------------------------------------------------------

doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=[])
check("cancel at the first prompt: nothing placed", not doc.instances)
check("cancel at the first prompt: no transaction",
      not FakeTransaction.log, str(FakeTransaction.log))

doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=ANSWERS[:2])
check("cancel part way: nothing placed", not doc.instances)

doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=ANSWERS, confirm=False)
check("confirmation declined: nothing placed", not doc.instances)
check("confirmation declined: no transaction",
      not FakeTransaction.log, str(FakeTransaction.log))

doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=[u"nonsense", ANSWERS[1], ANSWERS[2], u""])
check("unparseable: nothing placed", not doc.instances)
check("unparseable: the field is named",
      u"Easting" in recorder.text(), recorder.text())
check("unparseable: no transaction was opened",
      not FakeTransaction.log, str(FakeTransaction.log))

doc = FakeDocument(symbols=[marker()], levels=[])
recorder = run_script(doc, answers=ANSWERS)
check("no levels: refused before asking anything",
      not recorder.prompts and u"no levels" in recorder.text().lower(),
      recorder.text())

doc = FakeDocument(symbols=[marker()], levels=levels(), degenerate=True)
recorder = run_script(doc, answers=ANSWERS)
check("degenerate project location: refused before asking",
      not recorder.prompts and u"shared coordinates" in recorder.text(),
      recorder.text())
check("degenerate project location: nothing placed", not doc.instances)

doc = FakeDocument(symbols=[marker()], levels=levels(), location_raises=True)
recorder = run_script(doc, answers=ANSWERS)
check("unreadable project location: refused, nothing placed",
      not doc.instances and not recorder.prompts)


# --------------------------------------------------------------------------
# 3. The family
# --------------------------------------------------------------------------

doc = FakeDocument(symbols=[], levels=levels(), load_succeeds=False)
recorder = run_script(doc, answers=ANSWERS)
check("family missing: nothing placed", not doc.instances)
check("family missing: the message says where to put it",
      u"AVH_Coordinate_Marker.rfa" in recorder.text(), recorder.text())
check("family missing: and what it has to be",
      u"Generic Model" in recorder.text())
check("family missing: rolled back rather than left open",
      "rollback" in FakeTransaction.log, str(FakeTransaction.log))

doc = FakeDocument(symbols=[], levels=levels(), load_succeeds=True)
recorder = run_script(doc, answers=ANSWERS)
check("family loadable: it was loaded", len(doc.load_calls) == 1)
check("family loadable: and then placed", len(doc.instances) == 1)

# A family of another name is not this family.
doc = FakeDocument(symbols=[marker(u"Some Other Family")], levels=levels(),
                   load_succeeds=False)
recorder = run_script(doc, answers=ANSWERS)
check("a different family is not mistaken for the marker",
      not doc.instances and u"AVH_Coordinate_Marker.rfa" in recorder.text())

# Missing parameters are a note, not a failure: the marker is still worth
# having, and refusing would make the tool hostage to the family.
doc = FakeDocument(symbols=[marker()], levels=levels(),
                   missing_params=("AVH_Point_Name",))
recorder = run_script(doc, answers=ANSWERS)
check("a missing parameter is reported but still placed",
      len(doc.instances) == 1 and u"AVH_Point_Name" in recorder.text(),
      recorder.text())


# --------------------------------------------------------------------------
# 4. The readback, which is the whole point
# --------------------------------------------------------------------------

# Revit snapping the instance to the level is what the first live run hit:
# the elevation came back exactly 3.000 m out. The tool moves it back and
# carries on, because it knows where the point belongs in internal
# coordinates.
doc = FakeDocument(symbols=[marker()], levels=levels(),
                   place_offset=(0.0, 0.0, -3.0 * FEET))
recorder = run_script(doc, answers=ANSWERS)
check("snapped placement: moved back and kept", len(doc.instances) == 1,
      recorder.text())
landed = placed_shared(doc)
check("snapped placement: and it ends up on the typed coordinate",
      landed is not None and all(close(landed[i], TARGET[i], 1e-3)
                                 for i in range(3)),
      u"{0} vs {1}".format(landed, TARGET))
check("snapped placement: the move is reported, not hidden",
      u"moved back" in recorder.text(), recorder.text())
check("snapped placement: exactly one move",
      len(doc.moves) == 1, str(doc.moves))

# Correcting the placement is not the same as trusting it. When the move
# cannot happen, the readback still has to catch the bad position.
doc = FakeDocument(symbols=[marker()], levels=levels(),
                   place_offset=(0.5 * FEET, 0.0, 0.0), move_raises=True)
recorder = run_script(doc, answers=ANSWERS)
check("wrong landing that cannot be corrected: rolled back",
      not doc.instances, str(len(doc.instances)))
check("wrong landing: the axis is named",
      u"easting off by" in recorder.text(), recorder.text())
check("wrong landing: the numbers are in the output window",
      u"What the conversion did" in recorder.text(), recorder.text())
check("wrong landing: the derived frame is in there",
      u"frame origin" in recorder.text() and
      u"determinant" in recorder.text())
check("wrong landing: and the point it actually landed on",
      u"placed at,int" in recorder.text())
check("wrong landing: no success report",
      u"Asked for" not in recorder.text())

# Nothing to correct means no move at all. A model marked as changed for
# nothing is a sync somebody has to do.
doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=ANSWERS)
check("an exact placement is not moved", not doc.moves, str(doc.moves))
check("an exact placement says nothing about moving",
      u"moved back" not in recorder.text())

doc = FakeDocument(symbols=[marker()], levels=levels(), no_location=True)
recorder = run_script(doc, answers=ANSWERS)
check("no location to read back: rolled back", not doc.instances)
check("no location to read back: said so",
      u"could not be read back" in recorder.text())

doc = FakeDocument(symbols=[marker()], levels=levels(), place_raises=True)
recorder = run_script(doc, answers=ANSWERS)
check("Revit refusing the placement: rolled back", not doc.instances)
check("Revit refusing the placement: its words reach the user",
      u"Revit refused to place" in recorder.text())

doc = FakeDocument(symbols=[marker()], levels=levels(),
                   commit_status="RolledBack")
recorder = run_script(doc, answers=ANSWERS)
check("a rejected commit is not reported as success",
      u"Asked for" not in recorder.text() and
      u"rejected" in recorder.text(), recorder.text())


# --------------------------------------------------------------------------
# 5. A point under the building
# --------------------------------------------------------------------------

deep = shared_for_internal(10.0, 20.0, -3.0)
doc = FakeDocument(symbols=[marker()], levels=levels())
recorder = run_script(doc, answers=typed_for(deep) + [u"SP-002"])
check("below every level: still placed", len(doc.instances) == 1)
check("below every level: hosted on the lowest",
      doc.instances and doc.instances[0].level.Name == u"DA11.E01")
check("below every level: and the report says so",
      u"below every level" in recorder.text(), recorder.text())
landed = placed_shared(doc)
check("below every level: and it landed where asked",
      landed is not None and all(close(landed[i], deep[i], 1e-3)
                                 for i in range(3)))


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
