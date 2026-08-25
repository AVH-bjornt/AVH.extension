# -*- coding: utf-8 -*-
"""
Runs the real Door Room Check script against a mocked Revit.

Two things here are worth more than the rest.

The arrow geometry is checked as arithmetic, because an arrow pointing
at the wrong room is a drawing that lies quietly. Every direction is
tested, including a door facing diagonally, and the head has to sit
behind the tip rather than in front of it.

The cleanup is checked against a view the tool did not make. Deleting
view specific annotation out of somebody else's plan would be an
expensive bug, so the fake document records every delete and a scenario
asserts that a foreign view loses nothing.

Run outside Revit:

    python test_door_room_check_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel", "Doors.pulldown",
                      "Door Room Check.pushbutton", "script.py")

from avh_doorcheck import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

_next_id = [1000]



def next_id():
    _next_id[0] += 1
    return _next_id[0]


class FakeId(object):
    def __init__(self, value=None):
        self.Value = value if value is not None else next_id()
        self.IntegerValue = self.Value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "<Id {0}>".format(self.Value)


class FakeCategoryObject(object):
    def __init__(self, name):
        self.Name = name
        self.Id = FakeId()


class FakeXYZ(object):
    def __init__(self, x, y, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z


INVALID_ID = FakeId(-1)
LINES_CATEGORY = FakeCategoryObject(u"Lines")


class FakeLine(object):
    def __init__(self, start, end):
        self.start = start
        self.end = end

    @staticmethod
    def CreateBound(start, end):
        return FakeLine(start, end)


class FakeParameter(object):
    def __init__(self, value):
        self.value = value

    def AsString(self):
        return self.value


class FakeIdParameter(object):
    """A parameter holding an ElementId, such as a phase."""

    def __init__(self, element_id, read_only=False, refuses=False):
        self.element_id = element_id
        self.IsReadOnly = read_only
        self.refuses = refuses

    def AsElementId(self):
        return self.element_id

    def Set(self, value):
        if self.refuses:
            return False
        self.element_id = value
        return True


class FakeRoom(object):
    def __init__(self, number, cci=u"", name=u"", phase=None, placed=True):
        self.Id = FakeId()
        self.Name = name or number
        self.Location = FakeLocation(FakeXYZ(0, 0, 0)) if placed else None
        self.parameters = {u"Number": FakeParameter(number),
                           u"CCIMultiLevelLocationID": FakeParameter(cci)}
        self.phase_parameter = FakeIdParameter(
            phase.Id if phase is not None else None)

    def LookupParameter(self, name):
        return self.parameters.get(name)

    def get_Parameter(self, built_in):
        if built_in == "ROOM_PHASE":
            return self.phase_parameter
        return None


class FakeLocation(object):
    def __init__(self, point):
        self.Point = point


class FakeDoor(object):
    def __init__(self, level_id, origin=(0.0, 0.0), facing=(1.0, 0.0),
                 to_room=None, from_room=None, phased=True, name=u"Door",
                 unphased_to=None, unphased_from=None, rooms_in_phase=None):
        self.Id = FakeId()
        self.LevelId = level_id
        self.Location = FakeLocation(FakeXYZ(origin[0], origin[1], 0.0))
        self.FacingOrientation = (FakeXYZ(facing[0], facing[1], 0.0)
                                  if facing is not None else None)
        self.Name = name
        self._to = to_room
        self._from = from_room
        # What the unphased property answers, which in Revit is the last
        # phase and need not be the same room. Defaulting it to the same
        # room made the phased route untestable: dropping it entirely
        # changed no result.
        self._unphased_to = unphased_to if unphased_to is not None else to_room
        self._unphased_from = (unphased_from if unphased_from is not None
                               else from_room)
        self.phased = phased
        # None means the door answers for any phase. Set it and the door
        # answers only for that one, which is how Revit behaves and the
        # only way the phase picker can be tested at all.
        self.rooms_in_phase = rooms_in_phase

    def _answers_for(self, phase):
        if self.rooms_in_phase is None:
            return True
        return phase is not None and phase.Id == self.rooms_in_phase.Id

    # The phased getters are what Room Data Sync uses, so they are what
    # this uses. The unphased properties answer for the last phase.
    def get_ToRoom(self, phase):
        if not self.phased:
            raise Exception("no phased getter on this element")
        return self._to if self._answers_for(phase) else None

    def get_FromRoom(self, phase):
        if not self.phased:
            raise Exception("no phased getter on this element")
        return self._from if self._answers_for(phase) else None

    @property
    def ToRoom(self):
        if self.rooms_in_phase is not None:
            return None
        return self._unphased_to

    @property
    def FromRoom(self):
        if self.rooms_in_phase is not None:
            return None
        return self._unphased_from


class FakeLevel(object):
    def __init__(self, name, elevation=0.0):
        self.Id = FakeId()
        self.Name = name
        self.Elevation = elevation


class FakeViewFamilyType(object):
    def __init__(self, family="FloorPlan"):
        self.Id = FakeId()
        self.ViewFamily = family


class FakeTextNoteType(object):
    def __init__(self):
        self.Id = FakeId()


class FakeCurveElement(object):
    def __init__(self, view_id, line):
        self.Id = FakeId()
        self.OwnerViewId = view_id
        self.line = line


class FakeTextNote(object):
    def __init__(self, view_id, point, text):
        self.Id = FakeId()
        self.OwnerViewId = view_id
        self.point = point
        self.text = text


class FakeViewPlan(object):
    def __init__(self, name, level_id=None, scale=100, is_template=False,
                 template_id=None, hidden_categories=(),
                 unhideable=(), phase_read_only=False):
        self.Id = FakeId()
        self.Name = name
        self.LevelId = level_id
        self.Scale = scale
        self.IsTemplate = is_template
        self.Origin = FakeXYZ(0.0, 0.0, 0.0)
        self.overrides = {}
        self.ViewTemplateId = (template_id if template_id is not None
                               else INVALID_ID)
        self.hidden = set(hidden_categories)
        self.unhideable = set(unhideable)
        self.phase_parameter = FakeIdParameter(None,
                                               read_only=phase_read_only)

    def SetElementOverrides(self, element_id, overrides):
        self.overrides[element_id] = overrides

    def get_Parameter(self, built_in):
        if built_in == "VIEW_PHASE":
            return self.phase_parameter
        return None

    def GetCategoryHidden(self, category_id):
        return category_id in self.hidden

    def CanCategoryBeHidden(self, category_id):
        return category_id not in self.unhideable

    def SetCategoryHidden(self, category_id, hidden):
        if hidden:
            self.hidden.add(category_id)
        else:
            self.hidden.discard(category_id)


class FakeOverrides(object):
    def __init__(self):
        self.colour = None
        self.weight = None

    def SetProjectionLineColor(self, colour):
        self.colour = colour
        return self

    def SetProjectionLineWeight(self, weight):
        self.weight = weight
        return self


class FakeColor(object):
    def __init__(self, red, green, blue):
        self.Red = red
        self.Green = green
        self.Blue = blue

    def rgb(self):
        return (self.Red, self.Green, self.Blue)


class FakeCreate(object):
    def __init__(self, doc):
        self.doc = doc

    def NewDetailCurve(self, view, line):
        curve = FakeCurveElement(view.Id, line)
        self.doc.elements.append(curve)
        return curve


class FakeDocument(object):
    def __init__(self, elements=(), phases=(), is_family=False):
        self.elements = list(elements)
        self.IsFamilyDocument = is_family
        self.Phases = FakePhases(phases)
        self.Create = FakeCreate(self)
        self.deleted = []

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def Delete(self, ids):
        for element_id in list(ids):
            self.deleted.append(element_id)
            self.elements = [element for element in self.elements
                             if element.Id != element_id]

    def snapshot(self):
        return (list(self.elements), list(self.deleted))

    def restore(self, state):
        self.elements, self.deleted = list(state[0]), list(state[1])


class FakePhase(object):
    def __init__(self, name):
        self.Id = FakeId()
        self.Name = name


class FakePhases(object):
    def __init__(self, phases):
        self.phases = list(phases)
        self.Size = len(self.phases)

    def __getitem__(self, index):
        return self.phases[index]

    def __len__(self):
        return len(self.phases)


class FakeTransaction(object):
    committed = []
    rolled_back = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = self.doc.snapshot()

    def Commit(self):
        if FakeTransaction.commit_status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeCollector(object):
    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.view_id = view_id
        self.elements = list(doc.elements)

    def OfClass(self, cls):
        self.elements = [element for element in self.elements
                         if isinstance(element, cls)]
        if self.view_id is not None:
            self.elements = [element for element in self.elements
                             if getattr(element, "OwnerViewId", None) ==
                             self.view_id]
        return self

    def OfCategory(self, category):
        wanted = {"OST_Doors": FakeDoor, "OST_Rooms": FakeRoom}.get(category)
        if wanted is not None:
            self.elements = [element for element in self.elements
                             if isinstance(element, wanted)]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        return iter(self.elements)


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeDotNetList(object):
    def __init__(self):
        self.items = []

    def Add(self, item):
        self.items.append(item)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class ListFactory(object):
    def __getitem__(self, element_type):
        return FakeDotNetList


def install_dotnet():
    system = types.ModuleType("System")
    collections = types.ModuleType("System.Collections")
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = ListFactory()
    collections.Generic = generic
    system.Collections = collections
    sys.modules["System"] = system
    sys.modules["System.Collections"] = collections
    sys.modules["System.Collections.Generic"] = generic


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.pickers = []
        self.picker_titles = []
        self.phase_labels = []
        self.picked = None

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, picked=None, commit_status="Committed",
               picker_raises=False, no_text_type=False, phase_pick=None,
               new_view_template=None, new_view_hidden=(),
               new_view_unhideable=(), new_view_phase_read_only=False):
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.commit_status = commit_status

    recorder = Recorder()
    install_dotnet()

    created_notes = []

    def show(items, **kwargs):
        title = kwargs.get("title", u"")
        recorder.pickers.append(list(items))
        recorder.picker_titles.append(title)
        if picker_raises:
            raise Exception("the picker is unavailable")
        if u"phase" in title:
            recorder.phase_labels = list(items)
            return phase_pick
        return picked

    def create_note(document, view_id, point, text, type_id):
        note = FakeTextNote(view_id, point, text)
        document.elements.append(note)
        created_notes.append(note)
        return note

    def create_plan(document, view_type_id, level_id):
        view = FakeViewPlan(u"{3D} unnamed", level_id,
                            template_id=new_view_template,
                            hidden_categories=new_view_hidden,
                            unhideable=new_view_unhideable,
                            phase_read_only=new_view_phase_read_only)
        document.elements.append(view)
        return view

    db = Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        FilteredElementCollector=FakeCollector,
        BuiltInCategory=Namespace(OST_Doors="OST_Doors",
                                  OST_Rooms="OST_Rooms",
                                  OST_Lines="OST_Lines"),
        Level=FakeLevel,
        ViewPlan=Namespace(Create=create_plan),
        ViewFamilyType=FakeViewFamilyType,
        ViewFamily=Namespace(FloorPlan="FloorPlan", ThreeDimensional="3D"),
        TextNoteType=FakeTextNoteType,
        CurveElement=FakeCurveElement,
        Line=FakeLine,
        XYZ=FakeXYZ,
        ElementId=Namespace(InvalidElementId=INVALID_ID),
        OverrideGraphicSettings=FakeOverrides,
        Color=FakeColor,
        BuiltInParameter=Namespace(ROOM_PHASE="ROOM_PHASE",
                                   VIEW_PHASE="VIEW_PHASE"),
        Category=Namespace(GetCategory=staticmethod(
            lambda document, built_in:
            LINES_CATEGORY if built_in == "OST_Lines" else None)),
    )
    # ViewPlan and TextNote each have to be a class, so the cleanup
    # collector's isinstance test can match instances of them, and carry
    # a static Create. One object doing both. Handing the script a bare
    # namespace here made the cleanup collect nothing, and the missing
    # cleanup looked like a bug in the script.
    FakeViewPlan.Create = staticmethod(create_plan)
    db.ViewPlan = FakeViewPlan
    FakeTextNote.Create = staticmethod(create_note)
    db.TextNote = FakeTextNote

    if no_text_type:
        doc.elements = [element for element in doc.elements
                        if not isinstance(element, FakeTextNoteType)]

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(ActiveView=None))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=show))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md,
                                     linkify=lambda i: u"[id]"),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit

    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)

    recorder.notes = created_notes
    return recorder


def curves_in(doc, view):
    return [element for element in doc.elements
            if isinstance(element, FakeCurveElement) and
            element.OwnerViewId == view.Id]


def notes_in(doc, view):
    return [element for element in doc.elements
            if isinstance(element, FakeTextNote) and
            element.OwnerViewId == view.Id]


def the_view(doc, name=None):
    for element in doc.elements:
        if isinstance(element, FakeViewPlan) and not element.IsTemplate:
            if name is None or element.Name == name:
                return element
    return None


# --------------------------------------------------------------------------
# The arithmetic, no Revit at all
# --------------------------------------------------------------------------

check("scale: 6 mm at 1:100 is 600 mm in the model",
      close(model.paper_mm(6.0, 100), 600.0 / 304.8))
check("scale: the same mark is twice as long at 1:200",
      close(model.paper_mm(6.0, 200), 2 * model.paper_mm(6.0, 100)))
check("scale: a nonsense scale does not divide by zero",
      model.paper_mm(6.0, 0) > 0)

arrow = model.arrow_points((0.0, 0.0), (1.0, 0.0), 100)
(sx, sy), (ex, ey) = arrow["shaft"]
check("arrow: points the way the door faces", ex > sx and close(ey, sy))
check("arrow: starts clear of the door leaf", sx > 0)
check("arrow: the head sits behind the tip",
      arrow["head_left"][1][0] < ex and arrow["head_right"][1][0] < ex)
check("arrow: the head is symmetrical about the shaft",
      close(arrow["head_left"][1][1], -arrow["head_right"][1][1]))
check("arrow: the ToRoom text is beyond the tip",
      arrow["to_text"][0] > ex)
check("arrow: the FromRoom text is on the other side of the door",
      arrow["from_text"][0] < 0)

north = model.arrow_points((0.0, 0.0), (0.0, 1.0), 100)
check("arrow: a door facing north points north",
      close(north["shaft"][1][0], 0.0) and north["shaft"][1][1] > 0)

west = model.arrow_points((0.0, 0.0), (-1.0, 0.0), 100)
check("arrow: a door facing west points west",
      west["shaft"][1][0] < 0)

diagonal = model.arrow_points((0.0, 0.0), (1.0, 1.0), 100)
(dsx, dsy), (dex, dey) = diagonal["shaft"]
check("arrow: a diagonal door keeps its direction",
      close(dex - dsx, dey - dsy) and dex > dsx)
check("arrow: a diagonal arrow is not longer than a square one",
      close(math.sqrt((dex - dsx) ** 2 + (dey - dsy) ** 2),
            model.paper_mm(model.ARROW_LENGTH_MM, 100)))

check("arrow: a door with no facing direction draws nothing",
      model.arrow_points((0.0, 0.0), (0.0, 0.0), 100) is None)

check("state: ToRoom with an ID is the clean case",
      model.classify(True, True, u"+CC01.E01.A", u"+CC01.E01.B", False)
      == model.OK)
check("state: blank ToRoom with a usable FromRoom is the fall through",
      model.classify(True, True, u"", u"+CC01.E01.B", False)
      == model.FELL_THROUGH)
check("state: whitespace counts as blank",
      model.classify(True, True, u"   ", u"+CC01.E01.B", False)
      == model.FELL_THROUGH)
check("state: no ToRoom at all is its own case",
      model.classify(False, True, u"", u"+CC01.E01.B", False)
      == model.NO_TO_ROOM)
check("state: neither side has a room",
      model.classify(False, False, u"", u"", False) == model.NO_ROOMS)
check("state: both sides the same room",
      model.classify(True, True, u"+CC01.E01.A", u"+CC01.E01.A", True)
      == model.SAME_ROOM)
check("state: rooms on both sides and no ID anywhere",
      model.classify(True, True, u"", u"", False) == model.NO_VALUE)
check("state: only the clean case is not a problem",
      not model.is_problem(model.OK) and
      all(model.is_problem(key) for key, _c, _l in model.STATES
          if key != model.OK))
check("state: every state has a colour and a label",
      all(key in model.STATE_COLOURS and key in model.STATE_LABELS
          for key, _c, _l in model.STATES))

to_label, from_label = model.label_for(model.OK, u"105", u"106")
check("label: a clean door is not shouted about", to_label == u"105")
to_label, from_label = model.label_for(model.NO_VALUE, u"105", u"106")
check("label: a problem door is marked", to_label.startswith(u"!"))
check("label: a missing room says so rather than being blank",
      model.label_for(model.NO_ROOMS, u"", u"")[0].endswith(u"no room"))

check("name: level and phase both in the view name",
      model.view_name(u"AVH Door Rooms", u"E01", u"Phase 1")
      == u"AVH Door Rooms - E01 - Phase 1")
check("name: no phase, no trailing separator",
      model.view_name(u"AVH Door Rooms", u"E01", u"")
      == u"AVH Door Rooms - E01")


# --------------------------------------------------------------------------
# 1. The ordinary run
# --------------------------------------------------------------------------

def simple_model(doors=None, views=(), levels=None):
    level = FakeLevel(u"E01", 0.0)
    if levels is None:
        levels = [level]
    else:
        level = levels[0]
    if doors is None:
        doors = [FakeDoor(level.Id, (10.0, 0.0), (1.0, 0.0),
                          FakeRoom(u"105", u"+CC01.E01.A"),
                          FakeRoom(u"106", u"+CC01.E01.B"))]
    elements = list(levels) + list(doors) + list(views) + [
        FakeViewFamilyType(), FakeTextNoteType()]
    return level, FakeDocument(elements, phases=[FakePhase(u"Phase 1")])


level, doc = simple_model()
recorder = run_script(doc)
view = the_view(doc)

check("run: a view was made", view is not None)
check("run: named for the level and the phase",
      view is not None and view.Name == u"AVH Door Rooms - E01 - Phase 1",
      view.Name if view else u"none")
check("run: three lines per door, shaft and two head lines",
      len(curves_in(doc, view)) == 3, str(len(curves_in(doc, view))))
check("run: a note on each side",
      len(notes_in(doc, view)) == 2, str(len(notes_in(doc, view))))
check("run: the ToRoom number is on the far side",
      any(note.text == u"105" for note in notes_in(doc, view)))
check("run: the FromRoom number is behind the door",
      any(note.text == u"106" for note in notes_in(doc, view)))
check("run: the arrow is drawn in the clean colour",
      all(view.overrides[curve.Id].colour.rgb() == model.GREEN
          for curve in curves_in(doc, view)))
check("run: committed once", len(FakeTransaction.committed) == 1)
check("run: the view is opened", recorder.notes is not None)
check("run: the report names the phase", u"Phase 1" in recorder.text())
check("run: no alert on the happy path", not recorder.alerts,
      u" | ".join(recorder.alerts))

# The geometry that matters: the arrow must be on the ToRoom side.
shaft = [curve for curve in curves_in(doc, view)][0]
check("run: the arrow leaves the door in the facing direction",
      shaft.line.end.X > shaft.line.start.X)
check("run: everything is drawn in the view plane",
      all(curve.line.start.Z == 0.0 for curve in curves_in(doc, view)))


# --------------------------------------------------------------------------
# 2. The cases the view exists to find
# --------------------------------------------------------------------------

level = FakeLevel(u"E01", 0.0)
blank = FakeRoom(u"105", u"")
good = FakeRoom(u"106", u"+CC01.E01.B")
shared = FakeRoom(u"107", u"+CC01.E01.C")
doors = [
    FakeDoor(level.Id, (0.0, 0.0), (1.0, 0.0), blank, good),
    FakeDoor(level.Id, (5.0, 0.0), (1.0, 0.0), None, good),
    FakeDoor(level.Id, (10.0, 0.0), (1.0, 0.0), None, None),
    FakeDoor(level.Id, (15.0, 0.0), (1.0, 0.0), shared, shared),
]
level, doc = simple_model(doors=doors, levels=[level])
recorder = run_script(doc)
view = the_view(doc)

colours = [view.overrides[curve.Id].colour.rgb()
           for curve in curves_in(doc, view)]
check("problems: the fall through is drawn in amber",
      colours[0] == model.AMBER, str(colours[:1]))
check("problems: no ToRoom is amber", colours[3] == model.AMBER)
check("problems: no rooms at all is red", colours[6] == model.RED)
check("problems: both sides the same room is red", colours[9] == model.RED)
check("problems: every one is listed for follow up",
      u"Doors to look at" in recorder.text())
check("problems: the fall through is named in the report",
      u"took FromRoom" in recorder.text())
check("problems: the same room case is named",
      u"same room" in recorder.text())
check("problems: problem text is marked with a bang",
      any(note.text.startswith(u"!") for note in notes_in(doc, view)))


# --------------------------------------------------------------------------
# 3. Rerunning refreshes rather than accumulating
# --------------------------------------------------------------------------

level, doc = simple_model()
run_script(doc)
view = the_view(doc)
first_curves = len(curves_in(doc, view))
recorder = run_script(doc)

check("rerun: no second view", len([element for element in doc.elements
                                    if isinstance(element, FakeViewPlan)]) == 1)
check("rerun: the marks are not doubled",
      len(curves_in(doc, view)) == first_curves,
      str(len(curves_in(doc, view))))
check("rerun: the old marks were deleted", len(doc.deleted) > 0)
check("rerun: the report says it refreshed",
      u"Refreshed" in recorder.text())


# --------------------------------------------------------------------------
# 4. Somebody else's view is never touched
# --------------------------------------------------------------------------

level = FakeLevel(u"E01", 0.0)
foreign = FakeViewPlan(u"AVH Door Rooms - E01 - Phase 1", level.Id)
foreign.Name = u"E01 Furniture Plan"
existing_note = FakeTextNote(foreign.Id, FakeXYZ(0, 0, 0), u"do not delete")
level, doc = simple_model(levels=[level], views=[foreign])
doc.elements.append(existing_note)
recorder = run_script(doc)

check("foreign view: its annotation survives",
      existing_note in doc.elements)
# Asserting only that the note survived was not enough: a run that threw
# and rolled back restored it too, so the check passed while the tool was
# reusing somebody else's plan. The positive has to be asserted with it.
check("foreign view: our own view was made instead",
      the_view(doc, u"AVH Door Rooms - E01 - Phase 1") is not None)
check("foreign view: and the run finished normally",
      len(FakeTransaction.committed) == 1, str(FakeTransaction.committed))
check("foreign view: nothing was deleted from it",
      not [element_id for element_id in doc.deleted
           if element_id == existing_note.Id])

# And the guard itself: a view of ours by name is cleared, one renamed
# afterwards is not.
level = FakeLevel(u"E01", 0.0)
ours = FakeViewPlan(u"AVH Door Rooms - E01 - Phase 1", level.Id)
stale = FakeTextNote(ours.Id, FakeXYZ(0, 0, 0), u"from the last run")
level, doc = simple_model(levels=[level], views=[ours])
doc.elements.append(stale)
recorder = run_script(doc)
check("our own view: the previous run's marks are cleared",
      stale not in doc.elements)


# --------------------------------------------------------------------------
# 5. Things that stop the run
# --------------------------------------------------------------------------

level = FakeLevel(u"E01", 0.0)
level, doc = simple_model(doors=[], levels=[level])
recorder = run_script(doc)
check("no doors: said so", u"No doors were found" in recorder.text())
check("no doors: nothing committed", not FakeTransaction.committed)

level, doc = simple_model()
recorder = run_script(doc, no_text_type=True)
check("no text type: refuses rather than drawing unlabelled arrows",
      u"no text note type" in recorder.text().lower())
check("no text type: nothing committed", not FakeTransaction.committed)

level, doc = simple_model()
doc.IsFamilyDocument = True
recorder = run_script(doc)
check("family document: refused",
      u"not on a family document" in recorder.text())

level, doc = simple_model()
recorder = run_script(doc, commit_status="RolledBack")
check("commit rejected: user told nothing was drawn",
      u"nothing was drawn" in recorder.text().lower())
check("commit rejected: no view survives",
      the_view(doc) is None)


# --------------------------------------------------------------------------
# 6. Levels and phases
# --------------------------------------------------------------------------

first = FakeLevel(u"E01", 0.0)
second = FakeLevel(u"E02", 10.0)
here = FakeDoor(first.Id, (0.0, 0.0), (1.0, 0.0),
                FakeRoom(u"105", u"+A"), FakeRoom(u"106", u"+B"))
elsewhere = FakeDoor(second.Id, (0.0, 0.0), (1.0, 0.0),
                     FakeRoom(u"205", u"+A"), FakeRoom(u"206", u"+B"))
level, doc = simple_model(doors=[here, elsewhere], levels=[first, second])
recorder = run_script(doc, picked=u"E01")

check("levels: the picker offered both", len(recorder.pickers[0]) == 2
      if recorder.pickers else False)
check("levels: only the picked level's doors are marked",
      len(notes_in(doc, the_view(doc))) == 2,
      str(len(notes_in(doc, the_view(doc)))))
check("levels: the other level's rooms are not in the view",
      not any(note.text in (u"205", u"206")
              for note in notes_in(doc, the_view(doc))))

level, doc = simple_model(doors=[here, elsewhere], levels=[first, second])
recorder = run_script(doc, picked=None)
check("levels: cancelling the picker does nothing",
      not FakeTransaction.committed and the_view(doc) is None)

# The phased answer is preferred over the unphased one. The two are
# different rooms here, which is the only way this can be tested at all.
level = FakeLevel(u"E01", 0.0)
phased_door = FakeDoor(level.Id, (0.0, 0.0), (1.0, 0.0),
                       FakeRoom(u"105", u"+CC01.E01.A"),
                       FakeRoom(u"106", u"+B"),
                       unphased_to=FakeRoom(u"905", u"+WRONG"),
                       unphased_from=FakeRoom(u"906", u"+WRONG"))
level, doc = simple_model(doors=[phased_door], levels=[level])
recorder = run_script(doc)
texts = [note.text for note in notes_in(doc, the_view(doc))]
check("phase: the phased room is used, not the last phase one",
      u"105" in texts and u"905" not in texts, str(texts))

# A door whose phased getter refuses still gets read, through the
# unphased property, the way Room Data Sync learned to.
level = FakeLevel(u"E01", 0.0)
stubborn = FakeDoor(level.Id, (0.0, 0.0), (1.0, 0.0),
                    FakeRoom(u"105", u"+CC01.E01.A"),
                    FakeRoom(u"106", u"+B"), phased=False,
                    unphased_to=FakeRoom(u"905", u"+CC01.E01.A"),
                    unphased_from=FakeRoom(u"906", u"+B"))
level, doc = simple_model(doors=[stubborn], levels=[level])
recorder = run_script(doc)
check("phase: an element with no phased getter is still read",
      any(note.text == u"905" for note in notes_in(doc, the_view(doc))),
      str([note.text for note in notes_in(doc, the_view(doc))]))



# --------------------------------------------------------------------------
# 7. Phases, which is what 2.15.0 got wrong
# --------------------------------------------------------------------------

def phased_model(rooms_phase, other_phase, door_phase=None):
    """Two phases, rooms in one of them, doors answering only for it."""
    level = FakeLevel(u"E01", 0.0)
    to_room = FakeRoom(u"105", u"+CC01.E01.A", phase=rooms_phase)
    from_room = FakeRoom(u"106", u"+CC01.E01.B", phase=rooms_phase)
    spare = FakeRoom(u"107", u"+CC01.E01.C", phase=rooms_phase)
    door = FakeDoor(level.Id, (0.0, 0.0), (1.0, 0.0), to_room, from_room,
                    rooms_in_phase=door_phase or rooms_phase)
    elements = [level, door, to_room, from_room, spare,
                FakeViewFamilyType(), FakeTextNoteType()]
    doc = FakeDocument(elements, phases=[rooms_phase, other_phase])
    return level, door, doc


first_phase = FakePhase(u"Phase 1")
second_phase = FakePhase(u"Phase 2")
level, door, doc = phased_model(first_phase, second_phase)
recorder = run_script(doc, phase_pick=None)

check("phase: the picker is shown when there is more than one",
      any(u"phase" in title for title in recorder.picker_titles),
      u" | ".join(recorder.picker_titles))
check("phase: the room count is on the label",
      any(u"3 rooms" in label for label in recorder.phase_labels),
      u" | ".join(recorder.phase_labels))
check("phase: an empty phase says so, which is what warns you off it",
      any(u"Phase 2" in label and u"0 rooms" in label
          for label in recorder.phase_labels),
      u" | ".join(recorder.phase_labels))
check("phase: cancelling draws nothing",
      the_view(doc) is None and not FakeTransaction.committed)

# Picking the phase the rooms are in is the whole point.
level, door, doc = phased_model(first_phase, second_phase)
recorder = run_script(doc, phase_pick=u"Phase 1  (3 rooms)")
view = the_view(doc)
texts = [note.text for note in notes_in(doc, view)] if view else []

check("phase: picking the one with rooms finds them",
      u"105" in texts, str(texts))
check("phase: the view name carries the phase",
      view is not None and view.Name.endswith(u"Phase 1"),
      view.Name if view else u"none")
check("phase: the view is put on the chosen phase",
      view is not None and view.phase_parameter.element_id == first_phase.Id)
check("phase: the arrow count is reported",
      u"3 arrow line(s) drawn" in recorder.text(), recorder.text()[:400])

# Picking the wrong one reproduces 2.15.0 exactly, and now says so.
level, door, doc = phased_model(first_phase, second_phase)
recorder = run_script(doc, phase_pick=u"Phase 2  (0 rooms)")
view = the_view(doc)
texts = [note.text for note in notes_in(doc, view)] if view else []

check("wrong phase: the view was still drawn", view is not None)
check("wrong phase: every door comes back with no room",
      texts and all(u"no room" in text for text in texts), str(texts))
check("wrong phase: the report names the phase that does hold rooms",
      u"Phase 1" in recorder.text() and
      u"holds rooms" in recorder.text(), recorder.text()[-400:])
check("wrong phase: and tells you to run it again",
      u"pick that phase" in recorder.text())

# A view whose phase will not take the value is reported, not hidden.
level, door, doc = phased_model(first_phase, second_phase)


recorder = run_script(doc, phase_pick=u"Phase 1  (3 rooms)",
                      new_view_phase_read_only=True)
check("phase read only: reported rather than silently ignored",
      u"read only" in recorder.text(), recorder.text()[:400])
check("phase read only: the run still finishes",
      len(FakeTransaction.committed) == 1)


# --------------------------------------------------------------------------
# 8. Why 2.15.0 drew labels and no arrows
# --------------------------------------------------------------------------

template = FakeViewPlan(u"AVH plan standard", None, is_template=True)
level, doc = simple_model(views=[template])
recorder = run_script(doc, new_view_template=template.Id)
view = the_view(doc, u"AVH Door Rooms - E01 - Phase 1")

check("template on a new view: removed",
      view is not None and view.ViewTemplateId == INVALID_ID)
check("template on a new view: named in the report",
      u"AVH plan standard" in recorder.text())
check("template on a new view: the arrows are still drawn",
      len(curves_in(doc, view)) == 3, str(len(curves_in(doc, view))))

level, doc = simple_model()
recorder = run_script(doc, new_view_hidden=(LINES_CATEGORY.Id,))
view = the_view(doc, u"AVH Door Rooms - E01 - Phase 1")

check("hidden Lines category: switched back on",
      view is not None and LINES_CATEGORY.Id not in view.hidden)
check("hidden Lines category: reported, since that explains 2.15.0",
      u"no arrows" in recorder.text(), recorder.text()[:500])

# And when it cannot be switched on, say so rather than drawing invisibly.
level, doc = simple_model()


recorder = run_script(doc, new_view_hidden=(LINES_CATEGORY.Id,),
                      new_view_unhideable=(LINES_CATEGORY.Id,))
check("Lines cannot be shown: warned that the arrows will not appear",
      u"will not show" in recorder.text(), recorder.text()[:500])

# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
