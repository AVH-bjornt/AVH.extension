# -*- coding: utf-8 -*-
"""
Runs the real Split Wall Layers script against a mocked Revit, and
tests the arithmetic in lib/avh_walllayers/model.py.

The fake is behavioural, not scripted. A wall has faces: where they are
follows from its curve, its location line and its type's layers, worked
out here independently of the script's own arithmetic. Changing a
wall's type keeps its location line where it is, and changing its
location line keeps its faces where they are, both as in Revit. So the
main check is geometric: after the run, the new walls must sit exactly
where the original's layers were, each of the right thickness and
material, whatever order the script did things in.

Deleting a wall takes the dimensions and tags on it with it, a
transaction rolled back restores everything it changed, and a commit
can be refused, the way Revit refuses one.

Run outside Revit:

    python test_split_wall_layers_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Model.panel",
                      "Split Wall Layers.pushbutton", "script.py")

from avh_walllayers import model  # noqa: E402

results = []
MM = 1.0 / model.MM_PER_FOOT


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-7):
    return abs(a - b) < tolerance


# --------------------------------------------------------------------------
# The arithmetic, without Revit
# --------------------------------------------------------------------------

W = [12.5 * MM, 95 * MM, 12.5 * MM]

check("centres: 12.5/95/12.5 at +53.75, 0, -53.75 mm",
      all(close(a, b * MM) for a, b in zip(model.layer_centres(W),
                                           (53.75, 0.0, -53.75))),
      [c / MM for c in model.layer_centres(W)])
check("location: centreline is 0",
      model.location_offset(W, 1, 1, model.CENTRE) == 0.0)
check("location: exterior finish face is +60 mm",
      close(model.location_offset(W, 1, 1, model.FINISH_EXTERIOR),
            60 * MM))
check("location: interior finish face is -60 mm",
      close(model.location_offset(W, 1, 1, model.FINISH_INTERIOR),
            -60 * MM))
check("location: core faces are +47.5 and -47.5 mm",
      close(model.location_offset(W, 1, 1, model.CORE_EXTERIOR),
            47.5 * MM) and
      close(model.location_offset(W, 1, 1, model.CORE_INTERIOR),
            -47.5 * MM))
check("shifts from the exterior face: -6.25, -60, -113.75 mm",
      all(close(a, b * MM) for a, b in zip(
          model.shifts(W, 1, 1, model.FINISH_EXTERIOR),
          (-6.25, -60.0, -113.75))))

# An asymmetric wall: 15 finish, 140 core, 50 insulation, 10 finish,
# core is the 140 only. Total 215, centre of the core at
# 107.5 - 15 - 70 = +22.5 mm.
A = [15 * MM, 140 * MM, 50 * MM, 10 * MM]
check("asymmetric: core centreline at +22.5 mm",
      close(model.location_offset(A, 1, 1, model.CORE_CENTRE), 22.5 * MM))
check("asymmetric: core interior face at -47.5 mm",
      close(model.location_offset(A, 1, 1, model.CORE_INTERIOR),
            -47.5 * MM))
check("asymmetric: a two layer core spans both",
      close(model.location_offset(A, 1, 2, model.CORE_INTERIOR),
            -97.5 * MM))

try:
    model.location_offset(W, -1, -1, model.CORE_CENTRE)
    no_core = False
except model.Refusal:
    no_core = True
check("no core and a core location line is refused", no_core)
check("no core is fine on a finish face location line",
      close(model.location_offset(W, -1, -1, model.FINISH_INTERIOR),
            -60 * MM))
try:
    model.location_offset(W, 1, 1, 9)
    odd = False
except model.Refusal:
    odd = True
check("an unknown location line is refused", odd)

check("format_mm: 12.5", model.format_mm(12.5 * MM) == u"12.5")
check("format_mm: 95", model.format_mm(95 * MM) == u"95")
check("format_mm: 0.25", model.format_mm(0.25 * MM) == u"0.25")
check("type name", model.type_name(model.Layer(
    0, 12.5 * MM, 7, u"Gipsplata", u"Finish1")) == u"Gipsplata 12.5 mm")
check("type name without a material", model.type_name(model.Layer(
    0, 95 * MM, -1, u"", u"Structure")) == u"No material 95 mm")
check("unique name", model.unique_name(u"A", set([u"A", u"A (2)"])) ==
      u"A (3)")
check("membranes are not walls", model.wall_layers([
    model.Layer(0, 12.5 * MM, 1, u"a", u"Finish1"),
    model.Layer(1, 0.0, 2, u"b", u"Membrane"),
    model.Layer(2, 95 * MM, 3, u"c", u"Structure")])[1][0].index == 1)
try:
    model.wall_layers([model.Layer(0, 95 * MM, 3, u"c", u"Structure"),
                       model.Layer(1, 0.0, 2, u"b", u"Membrane")])
    one = False
except model.Refusal:
    one = True
check("one layer with a thickness is refused", one)
check("type key: same width to a thousandth of a mm",
      model.type_key(model.Layer(0, 12.5 * MM, 1, u"", u"Finish1")) ==
      model.type_key(model.Layer(5, 12.5004 * MM, 1, u"x", u"Finish1")))
check("type key: function matters",
      model.type_key(model.Layer(0, 12.5 * MM, 1, u"", u"Finish1")) !=
      model.type_key(model.Layer(0, 12.5 * MM, 1, u"", u"Finish2")))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

class ElementId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


ElementId.InvalidElementId = ElementId(-1)


class XYZ(object):
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)

    def t(self):
        return (self.X, self.Y, self.Z)


def add(a, b, scale=1.0):
    return tuple(a[i] + b[i] * scale for i in range(3))


def dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


class Line(object):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    @staticmethod
    def CreateBound(a, b):
        if dot(add(b.t(), a.t(), -1), add(b.t(), a.t(), -1)) < 1e-12:
            raise Exception("curve too short")
        return Line(a.t(), b.t())

    def GetEndPoint(self, index):
        return XYZ(*(self.a if index == 0 else self.b))


class Arc(object):
    def GetEndPoint(self, index):
        return XYZ(0, 0, 0)


class Category(object):
    def __init__(self, name):
        self.Name = name


class WallKind(object):
    Basic = "Basic"
    Curtain = "Curtain"
    Stacked = "Stacked"


class BuiltInParameter(object):
    WALL_KEY_REF_PARAM = "WALL_KEY_REF_PARAM"


class Element(object):
    def __init__(self, doc):
        self.doc = doc
        self.Id = doc.new_id()
        doc.elements[self.Id.Value] = self
        self.Category = None


class Material(Element):
    def __init__(self, doc, name):
        Element.__init__(self, doc)
        self.Name = name
        self.Category = Category(u"Materials")


class CSLayer(object):
    def __init__(self, width, material_id, function):
        self.Width = width
        self.MaterialId = material_id
        self.Function = function


class CompoundStructure(object):
    # Seen in Revit at 2.49.0: a plain partition, the same layers top to
    # bottom, reported IsVerticallyCompound True and was refused. So the
    # fake says True for every structure, and only one whose layers
    # really change up the wall says IsVerticallyHomogeneous() False.
    old_api = False      # True: no IsVerticallyHomogeneous to call

    def __init__(self, layers, first_core, last_core, vertical=False):
        self.layers = layers
        self.first = first_core
        self.last = last_core
        self.vertical = vertical
        self.IsVerticallyCompound = True

    def IsVerticallyHomogeneous(self):
        if CompoundStructure.old_api:
            raise AttributeError("IsVerticallyHomogeneous")
        return not self.vertical

    @staticmethod
    def CreateSingleLayerCompoundStructure(function, width, material_id):
        if width <= 0:
            raise Exception("width must be positive")
        return CompoundStructure([CSLayer(width, material_id, function)],
                                 0, 0)

    def GetLayers(self):
        return list(self.layers)

    @property
    def LayerCount(self):
        return len(self.layers)

    def GetWidth(self):
        return sum(layer.Width for layer in self.layers)

    def GetFirstCoreLayerIndex(self):
        return self.first

    def GetLastCoreLayerIndex(self):
        return self.last


class WallType(Element):
    def __init__(self, doc, name, structure, kind=WallKind.Basic):
        Element.__init__(self, doc)
        self.Name = name
        self.structure = structure
        self.Kind = kind
        self.Category = Category(u"Walls")

    def GetCompoundStructure(self):
        return self.structure

    def SetCompoundStructure(self, structure):
        if self.doc.lossy_types:
            return
        self.structure = structure

    def Duplicate(self, name):
        if any(getattr(e, "Name", None) == name and isinstance(e, WallType)
               for e in self.doc.elements.values()):
            raise Exception("The name is already in use")
        return WallType(self.doc, name, self.structure, self.Kind)

    @property
    def Width(self):
        return self.structure.GetWidth()


def offset_of(structure, key):
    """Where a location line sits from the wall centre, exterior
    positive. Written here from the layer faces, apart from the
    script's model.location_offset, so the geometric checks do not
    just repeat the script's own arithmetic."""
    faces = [structure.GetWidth() / 2.0]
    for layer in structure.GetLayers():
        faces.append(faces[-1] - layer.Width)
    first, last = structure.first, structure.last
    by_key = {0: 0.0, 2: faces[0], 3: faces[-1]}
    if first is not None and first >= 0:
        by_key[4] = faces[first]
        by_key[5] = faces[last + 1]
        by_key[1] = (faces[first] + faces[last + 1]) / 2.0
    return by_key[key]


class Param(object):
    def __init__(self, wall):
        self.wall = wall
        self.IsReadOnly = wall.doc.key_read_only

    def AsInteger(self):
        return self.wall.key

    def Set(self, value):
        """As in Revit, the faces stay put and the curve moves."""
        wall = self.wall
        structure = wall.WallType.GetCompoundStructure()
        delta = offset_of(structure, value) - offset_of(structure, wall.key)
        wall.ends = (add(wall.ends[0], wall.Orientation.t(), delta),
                     add(wall.ends[1], wall.Orientation.t(), delta))
        wall.key = value
        return True


class LocationCurve(object):
    def __init__(self, wall):
        self.__dict__["wall"] = wall

    @property
    def Curve(self):
        if self.wall.curved:
            return Arc()
        return Line(*self.wall.ends)

    def __setattr__(self, name, value):
        if name != "Curve":
            raise AttributeError(name)
        if self.wall.doc.curve_ignored:
            return
        self.wall.ends = (value.a, value.b)


class Wall(Element):
    def __init__(self, doc, wall_type, start=(0.0, 0.0, 0.0),
                 end=(10.0, 0.0, 0.0), orientation=(0.0, 1.0, 0.0),
                 key=model.CENTRE):
        Element.__init__(self, doc)
        self.type_id = wall_type.Id
        self.ends = (start, end)
        self.orientation = orientation
        self.key = key
        self.Pinned = False
        self.GroupId = ElementId.InvalidElementId
        self.inserts = []
        self.curved = False
        self.Category = Category(u"Walls")
        self.comment = u"made by hand"

    @property
    def WallType(self):
        return self.doc.elements[self.type_id.Value]

    @property
    def Location(self):
        return LocationCurve(self)

    @property
    def Orientation(self):
        return XYZ(*self.orientation)

    @property
    def Width(self):
        return self.WallType.Width

    def get_Parameter(self, which):
        if which == BuiltInParameter.WALL_KEY_REF_PARAM:
            return Param(self)
        return None

    def FindInserts(self, *flags):
        return list(self.inserts)

    def ChangeTypeId(self, type_id):
        """As in Revit, the location line stays where it is."""
        if self.doc.change_type_raises:
            raise Exception("Revit refused the type")
        self.type_id = type_id
        return type_id

    def span(self, normal):
        """(exterior, interior) face positions along `normal`."""
        structure = self.WallType.GetCompoundStructure()
        line = dot(self.ends[0], normal)
        sign = dot(self.orientation, normal)
        centre = line - sign * offset_of(structure, self.key)
        half = structure.GetWidth() / 2.0
        return centre + sign * half, centre - sign * half


class Annotation(Element):
    """A dimension or tag on some walls: it goes when any of them does."""

    def __init__(self, doc, name, refs):
        Element.__init__(self, doc)
        self.Category = Category(name)
        self.refs = [r.Id.Value for r in refs]


class Collector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        self.result = [e for e in self.doc.elements.values()
                       if isinstance(e, cls)]
        return self

    def __iter__(self):
        return iter(self.result)


class ElementTransformUtils(object):
    @staticmethod
    def CopyElement(doc, element_id, vector):
        if doc.copy_makes_nothing:
            return []
        source = doc.elements[element_id.Value]
        copy = Wall(doc, source.WallType, add(source.ends[0], vector.t()),
                    add(source.ends[1], vector.t()), source.orientation,
                    source.key)
        copy.comment = source.comment
        doc.copies += 1
        return [copy.Id]


class TransactionStatus(object):
    Committed = "Committed"
    RolledBack = "RolledBack"


class IFailuresPreprocessor(object):
    """A real class of its own, not `object`: the skill's warning about
    an interface stubbed as object hiding a method resolution bug."""


class FailureOptions(object):
    def SetFailuresPreprocessor(self, p):
        self.preprocessor = p

    def SetClearAfterRollback(self, value):
        pass

    def SetForcedModalHandling(self, value):
        pass


class FailureMessage(object):
    def __init__(self, text):
        self.text = text

    def GetSeverity(self):
        return "Error"

    def GetDescriptionText(self):
        return self.text


class Accessor(object):
    def __init__(self, messages):
        self.messages = messages

    def GetFailureMessages(self):
        return self.messages

    def DeleteWarning(self, failure):
        pass


class Transaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.options = FailureOptions()
        self.preprocessor = None
        doc.transactions.append(self)

    def Start(self):
        # State as it was when this transaction began, so a rollback
        # restores exactly this and not some older picture.
        self.saved = self.doc.save()
        self.doc.open = self

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.preprocessor = getattr(options, "preprocessor", None)

    def RollBack(self):
        self.doc.restore(self.saved)
        self.doc.open = None
        self.status = TransactionStatus.RolledBack
        return self.status

    def Commit(self):
        errors = []
        before = self.saved[0]
        for value, text in self.doc.refuse_deleting.items():
            if value in before and value not in self.doc.elements:
                errors.append(text)
        if errors:
            if self.preprocessor is not None:
                self.preprocessor.PreprocessFailures(Accessor(
                    [FailureMessage(t) for t in errors]))
            self.doc.restore(self.saved)
            self.status = TransactionStatus.RolledBack
        else:
            self.doc.commits += 1
            self.status = TransactionStatus.Committed
        self.doc.open = None
        return self.status


class TransactionGroup(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.state = None
        doc.groups.append(self)

    def Start(self):
        self.saved = self.doc.save()
        self.state = "started"

    def RollBack(self):
        self.doc.restore(self.saved)
        self.state = "rolled back"

    def Assimilate(self):
        self.state = "assimilated"
        if self.doc.assimilate_status != TransactionStatus.Committed:
            self.doc.restore(self.saved)
        return self.doc.assimilate_status


class Document(object):
    def __init__(self):
        self.elements = {}
        self.next = 100
        self.transactions = []
        self.groups = []
        self.open = None
        self.commits = 0
        self.copies = 0
        self.deletes = []
        self.IsFamilyDocument = False
        self.assimilate_status = TransactionStatus.Committed
        self.refuse_deleting = {}
        self.lossy_types = False
        self.curve_ignored = False
        self.change_type_raises = False
        self.copy_makes_nothing = False
        self.key_read_only = False
        self.delete_ignored = False
        self.writes_outside = 0

    def new_id(self):
        self.next += 1
        if self.open is None and self.next > 100 and self.started:
            self.writes_outside += 1
        return ElementId(self.next)

    started = False

    def save(self):
        return (dict(self.elements),
                dict((k, dict(e.__dict__)) for k, e in self.elements.items()))

    def restore(self, saved):
        elements, states = saved
        self.elements = dict(elements)
        for key, state in states.items():
            self.elements[key].__dict__.clear()
            self.elements[key].__dict__.update(state)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def Delete(self, element_id):
        if self.open is None:
            self.writes_outside += 1
        if self.delete_ignored:
            return [element_id]
        target = self.elements.pop(element_id.Value)
        gone = [target.Id]
        for key, element in list(self.elements.items()):
            if element_id.Value in getattr(element, "refs", []):
                del self.elements[key]
                gone.append(element.Id)
        self.deletes.append(element_id.Value)
        return gone

    # helpers for the tests ------------------------------------------------

    def walls(self):
        return [e for e in self.elements.values()
                if type(e) is Wall]

    def wall_types(self):
        return [e for e in self.elements.values()
                if isinstance(e, WallType)]


def make_db():
    db = types.ModuleType("Autodesk.Revit.DB")
    for name, value in dict(
            XYZ=XYZ, Line=Line, Wall=Wall, WallType=WallType,
            WallKind=WallKind, BuiltInParameter=BuiltInParameter,
            CompoundStructure=CompoundStructure, ElementId=ElementId,
            FilteredElementCollector=Collector,
            ElementTransformUtils=ElementTransformUtils,
            Transaction=Transaction, TransactionGroup=TransactionGroup,
            TransactionStatus=TransactionStatus,
            IFailuresPreprocessor=IFailuresPreprocessor).items():
        setattr(db, name, value)
    db.FailureSeverity = types.SimpleNamespace(Warning="Warning")
    db.FailureProcessingResult = types.SimpleNamespace(
        ProceedWithRollBack="rollback", Continue="continue")
    return db


# --------------------------------------------------------------------------
# pyRevit stand ins and the runner
# --------------------------------------------------------------------------

class Recorder(object):
    def __init__(self, confirm=True):
        self.confirm = confirm
        self.alerts = []
        self.lines = []
        self.questions = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes"):
            self.questions.append(message)
            return self.confirm
        return True

    def print_md(self, text):
        self.lines.append(text)

    def text(self):
        return u"\n".join(self.lines)

    def tables(self):
        return [line for line in self.lines if line.startswith(u"| Wall")]


def run_script(doc, selected, confirm=True, script=None):
    rec = Recorder(confirm)
    rec.raised = None
    db = make_db()
    doc.started = True
    uidoc = types.SimpleNamespace(
        Selection=types.SimpleNamespace(
            GetElementIds=lambda: [e.Id for e in selected]),
        Document=doc)
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = types.SimpleNamespace(doc=doc, uidoc=uidoc)
    pyrevit.DB = db
    pyrevit.forms = types.SimpleNamespace(alert=rec.alert)
    output = types.SimpleNamespace(
        print_md=rec.print_md,
        linkify=lambda element_id: u"[{0}]".format(element_id.Value))
    logger = types.SimpleNamespace(debug=lambda *a: None,
                                   error=lambda *a: None)
    pyrevit.script = types.SimpleNamespace(
        get_output=lambda: output, get_logger=lambda: logger)
    sys.modules["pyrevit"] = pyrevit
    try:
        runpy.run_path(script or SCRIPT, run_name="__main__")
    except Exception as exc:
        rec.raised = exc
    finally:
        sys.modules.pop("pyrevit", None)
    doc.started = False
    return rec


# --------------------------------------------------------------------------
# Building models
# --------------------------------------------------------------------------

def partition(doc, name=u"Gips 12.5 / Timbur 95 / Gips 12.5"):
    gypsum = Material(doc, u"Gipsplata")
    timber = Material(doc, u"Timbur")
    structure = CompoundStructure([
        CSLayer(12.5 * MM, gypsum.Id, "Finish2"),
        CSLayer(95 * MM, timber.Id, "Structure"),
        CSLayer(12.5 * MM, gypsum.Id, "Finish2")], 1, 1)
    return WallType(doc, name, structure), gypsum, timber


def layers_in_place(normal, original_span, made, widths, z=0.0):
    """Do the walls in `made` tile the original's layers, exterior first,
    with the right thicknesses? Checked by faces, along the original's
    exterior direction `normal`, with nothing from the script."""
    face = original_span[0]
    spans = sorted((w.span(normal) for w in made), key=lambda s: -s[0])
    if len(spans) != len(widths):
        return False, (len(spans), len(widths))
    for (exterior, interior), width in zip(spans, widths):
        if not (close(exterior, face, 1e-6) and
                close(interior, face - width, 1e-6)):
            return False, [(s[0] / MM, s[1] / MM) for s in spans]
        face -= width
    for wall in made:
        along = add(wall.ends[1], wall.ends[0], -1)
        if not close(dot(along, normal), 0.0, 1e-9):
            return False, "a wall turned"
        if not (close(wall.ends[0][2], z) and close(wall.ends[1][2], z)):
            return False, "a wall moved vertically"
    return True, ""


def table_ok(rec):
    tables = rec.tables()
    for table in tables:
        rows = table.split(u"\n")
        bars = rows[0].count(u"|")
        if rows[1].replace(u" ", u"").strip(u"|").split(u"|") != \
                [u":---"] * (bars - 1):
            return False
        if any(row.count(u"|") != bars for row in rows):
            return False
        if any(u"|  |" in row for row in rows):
            return False
    return bool(tables)


# --------------------------------------------------------------------------
# S1. The case asked for: 12.5 gypsum / 95 timber / 12.5 gypsum
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
dimension = Annotation(doc, u"Dimensions", [wall])
span = wall.span((0.0, 1.0, 0.0))
original_id = wall.Id.Value
rec = run_script(doc, [wall])

check("S1: ran without raising", rec.raised is None, rec.raised)
walls = doc.walls()
check("S1: three walls, the original gone",
      len(walls) == 3 and original_id not in doc.elements,
      [w.Id.Value for w in walls])
ok, detail = layers_in_place((0.0, 1.0, 0.0), span, walls,
                             [12.5 * MM, 95 * MM, 12.5 * MM])
check("S1: the new walls sit exactly where the layers were", ok, detail)
check("S1: each new wall is on its centreline",
      all(w.key == model.CENTRE for w in walls))
names = sorted(w.WallType.Name for w in walls)
check("S1: types Gipsplata 12.5 mm (twice) and Timbur 95 mm",
      names == [u"Gipsplata 12.5 mm", u"Gipsplata 12.5 mm",
                u"Timbur 95 mm"], names)
check("S1: two new types, not three",
      len(doc.wall_types()) == 3, [t.Name for t in doc.wall_types()])
check("S1: a new type keeps the layer's material and function",
      all(w.WallType.GetCompoundStructure().GetLayers()[0].MaterialId ==
          (timber.Id if w.Width > 50 * MM else gypsum.Id) for w in walls))
check("S1: copies keep the original's other data",
      all(w.comment == u"made by hand" for w in walls))
check("S1: the dimension went with the original",
      dimension.Id.Value not in doc.elements)
check("S1: the dimension was named before the question",
      u"also deletes 1 Dimensions" in rec.text() and
      u"1 other element(s)" in rec.questions[0], rec.text())
check("S1: one question, asked once", len(rec.questions) == 1)
check("S1: one group, assimilated",
      len(doc.groups) == 1 and doc.groups[0].state == "assimilated")
check("S1: nothing written outside a transaction",
      doc.writes_outside == 0, doc.writes_outside)
check("S1: the report says 1 wall split",
      u"1 wall(s) split." in rec.alerts[-1], rec.alerts)
check("S1: every row made", rec.text().count(u"| made: [") == 3,
      rec.text())
check("S1: the plan names the shifts",
      u"53.75 mm outside" in rec.text() and
      u"53.75 mm inside" in rec.text() and
      u"on the location line" in rec.text(), rec.text())
check("S1: tables printed whole, left aligned, no empty cell",
      table_ok(rec) and len(rec.tables()) == 2, rec.tables())

# --------------------------------------------------------------------------
# S2. Location line at the exterior finish face
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type, key=model.FINISH_EXTERIOR)
span = wall.span((0.0, 1.0, 0.0))
rec = run_script(doc, [wall])
ok, detail = layers_in_place((0.0, 1.0, 0.0), span, doc.walls(),
                             [12.5 * MM, 95 * MM, 12.5 * MM])
check("S2: exterior face location line: layers in place", ok, detail)

# --------------------------------------------------------------------------
# S3. Flipped, asymmetric, core face location line, at an angle
# --------------------------------------------------------------------------

doc = Document()
plaster = Material(doc, u"Múrhúð")
concrete = Material(doc, u"Steypa")
wool = Material(doc, u"Steinull")
board = Material(doc, u"Klæðning")
structure = CompoundStructure([
    CSLayer(15 * MM, plaster.Id, "Finish1"),
    CSLayer(140 * MM, concrete.Id, "Structure"),
    CSLayer(50 * MM, wool.Id, "Insulation"),
    CSLayer(10 * MM, board.Id, "Finish2")], 1, 1)
outer = WallType(doc, u"Útveggur", structure)
direction = (0.6, 0.8, 0.0)
exterior = (0.8, -0.6, 0.0)            # flipped: the other side of +90
wall = Wall(doc, outer, (2.0, 3.0, 1.0),
            add((2.0, 3.0, 1.0), direction, 12.0), exterior,
            model.CORE_INTERIOR)
span = wall.span(exterior)
rec = run_script(doc, [wall])
ok, detail = layers_in_place(exterior, span, doc.walls(),
                             [15 * MM, 140 * MM, 50 * MM, 10 * MM], z=1.0)
check("S3: flipped, angled, core face: four walls in place", ok, detail)
check("S3: Icelandic names survive into the types",
      set(t.Name for t in doc.wall_types()) >= set(
          [u"Múrhúð 15 mm", u"Steypa 140 mm", u"Steinull 50 mm",
           u"Klæðning 10 mm"]),
      [t.Name for t in doc.wall_types()])
check("S3: the base height is kept",
      all(close(w.ends[0][2], 1.0) for w in doc.walls()))

# --------------------------------------------------------------------------
# S4. A membrane is listed, not made into a wall
# --------------------------------------------------------------------------

doc = Document()
gypsum = Material(doc, u"Gipsplata")
foil = Material(doc, u"Rakavörn")
timber = Material(doc, u"Timbur")
structure = CompoundStructure([
    CSLayer(12.5 * MM, gypsum.Id, "Finish2"),
    CSLayer(0.0, foil.Id, "Membrane"),
    CSLayer(95 * MM, timber.Id, "Structure"),
    CSLayer(12.5 * MM, gypsum.Id, "Finish2")], 2, 2)
wall = Wall(doc, WallType(doc, u"Með rakavörn", structure))
span = wall.span((0.0, 1.0, 0.0))
rec = run_script(doc, [wall])
ok, detail = layers_in_place((0.0, 1.0, 0.0), span, doc.walls(),
                             [12.5 * MM, 95 * MM, 12.5 * MM])
check("S4: membrane: three walls in place", ok, detail)
check("S4: membrane: listed", u"1 membrane layer(s)" in rec.text(),
      rec.text())

# --------------------------------------------------------------------------
# S5. Types already in the model are reused; a name clash is avoided
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
existing = WallType(doc, u"Gipsplata eitt lag", CompoundStructure(
    [CSLayer(12.5 * MM, gypsum.Id, "Finish2")], 0, 0))
clash = WallType(doc, u"Timbur 95 mm", CompoundStructure(
    [CSLayer(95 * MM, timber.Id, "Finish1")], 0, 0))   # wrong function
first = Wall(doc, wall_type)
second = Wall(doc, wall_type, (0.0, 5.0, 0.0), (10.0, 5.0, 0.0))
rec = run_script(doc, [first, second])
names = sorted(set(w.WallType.Name for w in doc.walls()))
check("S5: the existing gypsum type is reused",
      u"Gipsplata eitt lag" in names, names)
check("S5: a clashing name gets (2)", u"Timbur 95 mm (2)" in names, names)
check("S5: one type made for two walls",
      len(doc.wall_types()) == 4, [t.Name for t in doc.wall_types()])
check("S5: both walls split, six walls",
      len(doc.walls()) == 6 and u"2 wall(s) split." in rec.alerts[-1],
      rec.alerts)
check("S5: the report says which type is in the model",
      u"(in the model)" in rec.text() and u"(new type)" in rec.text())

# --------------------------------------------------------------------------
# S6. Refusals
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
curtain = Wall(doc, WallType(doc, u"Gler", CompoundStructure([], -1, -1),
                             WallKind.Curtain))
curved = Wall(doc, wall_type)
curved.curved = True
with_door = Wall(doc, wall_type)
with_door.inserts = [ElementId(5)]
grouped = Wall(doc, wall_type)
grouped.GroupId = ElementId(77)
pinned = Wall(doc, wall_type)
pinned.Pinned = True
single = Wall(doc, WallType(doc, u"Steypa 200", CompoundStructure(
    [CSLayer(200 * MM, timber.Id, "Structure")], 0, 0)))
stacked_up = Wall(doc, WallType(doc, u"Lóðrétt", CompoundStructure(
    [CSLayer(10 * MM, timber.Id, "Structure"),
     CSLayer(10 * MM, timber.Id, "Structure")], 0, 1, vertical=True)))
no_core = Wall(doc, WallType(doc, u"Kjarnalaus", CompoundStructure(
    [CSLayer(10 * MM, timber.Id, "Finish1"),
     CSLayer(10 * MM, timber.Id, "Finish1")], -1, -1)),
    key=model.CORE_CENTRE)
not_a_wall = Material(doc, u"Ekki veggur")
refused = [curtain, curved, with_door, grouped, pinned, single, stacked_up,
           no_core]
before = dict(doc.elements)
rec = run_script(doc, refused + [not_a_wall])
check("S6: refused walls: nothing changed",
      doc.elements == before and doc.commits == 0, doc.commits)
check("S6: refused walls: not asked", not rec.questions, rec.questions)
for words in (u"curtain or stacked", u"is curved", u"door(s)",
              u"in a group", u"is pinned", u"only one layer",
              u"change up its height", u"has no core"):
    check(u"S6: refusal says '{0}'".format(words), words in rec.text(),
          rec.text())
check("S6: none can be split, said so",
      u"None of the 8 selected" in rec.alerts[-1], rec.alerts)

doc = Document()
rec = run_script(doc, [])
check("S6: nothing selected asks for a selection",
      u"Select the walls" in rec.alerts[0] and not rec.lines)

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
check("S6: the fake's plain wall says IsVerticallyCompound, as in Revit",
      wall_type.GetCompoundStructure().IsVerticallyCompound is True)
rec = run_script(doc, [Wall(doc, WallType(doc, u"Lóðrétt", CompoundStructure(
    [CSLayer(10 * MM, timber.Id, "Structure"),
     CSLayer(10 * MM, timber.Id, "Structure")], 0, 1, vertical=True)))])
check("S6: all refused: no empty table, a heading and the reason",
      not rec.tables() and u"nothing to split" in rec.text() and
      u"IsVerticallyHomogeneous: False" in rec.text(), rec.lines)

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
CompoundStructure.old_api = True
rec = run_script(doc, [wall])
CompoundStructure.old_api = False
check("S6: without IsVerticallyHomogeneous it refuses and says why",
      len(doc.walls()) == 1 and
      u"IsVerticallyCompound: True" in rec.text(), rec.text())

# --------------------------------------------------------------------------
# S7. Declined: nothing written
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
dimension = Annotation(doc, u"Dimensions", [wall])
before = dict(doc.elements)
rec = run_script(doc, [wall], confirm=False)
check("S7: declined: model unchanged, dimension restored after the check",
      doc.elements == before and doc.commits == 0 and
      dimension.Id.Value in doc.elements)
check("S7: declined: the plan was shown", len(rec.tables()) == 1)

# --------------------------------------------------------------------------
# S8. Revit refuses one wall on commit: it stays whole, the other splits
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
good = Wall(doc, wall_type)
bad = Wall(doc, wall_type, (0.0, 5.0, 0.0), (10.0, 5.0, 0.0))
doc.refuse_deleting = {bad.Id.Value: u"Can't delete a joined wall"}
rec = run_script(doc, [good, bad])
check("S8: the refused wall is still there, whole",
      bad.Id.Value in doc.elements and bad.WallType is wall_type)
check("S8: no stray copies from the refused wall",
      len(doc.walls()) == 4, len(doc.walls()))
check("S8: Revit's words reach the report",
      u"Can't delete a joined wall" in rec.text(), rec.text())
check("S8: counted honestly",
      u"1 wall(s) split. 1 could not be" in rec.alerts[-1], rec.alerts)

# --------------------------------------------------------------------------
# S9. The whole group rolled back: nothing reported as kept
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.assimilate_status = TransactionStatus.RolledBack
rec = run_script(doc, [wall])
check("S9: group rolled back: the original is there",
      wall.Id.Value in doc.elements and len(doc.walls()) == 1)
check("S9: group rolled back: reported as not kept",
      u"0 wall(s) split." in rec.alerts[-1] and
      u"not kept" in rec.text(), rec.alerts)

# --------------------------------------------------------------------------
# S10. A write that raises midway leaves nothing half done
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.change_type_raises = True
rec = run_script(doc, [wall])
check("S10: raised midway: original whole, no copies",
      len(doc.walls()) == 1 and wall.Id.Value in doc.elements)
check("S10: raised midway: the reason is reported",
      u"Revit refused the type" in rec.text(), rec.text())

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.copy_makes_nothing = True
rec = run_script(doc, [wall])
check("S10: a copy that makes nothing is a failure, not a success",
      wall.Id.Value in doc.elements and
      u"copying it made no wall" in rec.text(), rec.text())

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.key_read_only = True
rec = run_script(doc, [wall])
check("S10: a location line that will not set is a failure",
      wall.Id.Value in doc.elements and len(doc.walls()) == 1, rec.text())

# --------------------------------------------------------------------------
# S11. The check after the run: what Revit says is not taken on trust
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type, key=model.FINISH_EXTERIOR)
doc.curve_ignored = True            # Revit quietly keeps the old curve
rec = run_script(doc, [wall])
check("S11: a wall left in the wrong place is flagged",
      u"not where the layer was" in rec.text() and
      u"0 wall(s) split. 1 could not be" in rec.alerts[-1],
      rec.text())

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.lossy_types = True              # the new type keeps the old layers
rec = run_script(doc, [wall])
check("S11: a type that will not take its layer stops the run",
      len(doc.walls()) == 1 and len(doc.wall_types()) == 1 and
      u"wall types could not be made" in rec.alerts[-1], rec.alerts)

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
doc.delete_ignored = True           # Revit says deleted, keeps it
rec = run_script(doc, [wall])
check("S11: an original still in the model is flagged",
      u"the original wall is still there" in rec.text() and
      u"0 wall(s) split. 1 could not be" in rec.alerts[-1], rec.text())

# --------------------------------------------------------------------------
# The checks can fail
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
span = wall.span((0.0, 1.0, 0.0))
off = Wall(doc, wall_type, (0.0, 1 * MM, 0.0), (10.0, 1 * MM, 0.0))
check("self-check: the original alone does not pass as its layers",
      not layers_in_place((0.0, 1.0, 0.0), span, [wall],
                          [12.5 * MM, 95 * MM, 12.5 * MM])[0])
check("self-check: a wall 1 mm off fails the face check",
      not layers_in_place((0.0, 1.0, 0.0), span, [off],
                          [120 * MM])[0])
check("self-check: check stores a real boolean",
      all(isinstance(passed, bool) for _, passed, _ in results))

failed = [r for r in results if not r[1]]
for name, passed, detail in results:
    print(u"{0}  {1}{2}".format("PASS" if passed else "FAIL", name,
                                u"  ({0})".format(detail)
                                if not passed and detail != "" else u""))
print(u"\n{0} checks, {1} failed".format(len(results), len(failed)))
sys.exit(1 if failed else 0)
