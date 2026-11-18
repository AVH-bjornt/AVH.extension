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

LAYERS = [model.Layer(0, 92 * MM, 1, u"brick", u"Finish1"),
          model.Layer(1, 75 * MM, 2, u"air", u"Insulation"),
          model.Layer(3, 19.5 * MM, 3, u"ply", u"Substrate"),
          model.Layer(4, 150 * MM, 4, u"stud", u"Structure"),
          model.Layer(6, 12.5 * MM, 5, u"gyp", u"Finish2")]
check("core layer: the thickest inside the core, not the thickest",
      model.core_layer(LAYERS, 4, 4).index == 4 and
      model.core_layer(LAYERS, 3, 3).index == 3)
check("core layer: a two layer core takes its thicker",
      model.core_layer(LAYERS, 3, 4).index == 4 and
      model.core_layer(LAYERS, 0, 1).index == 0)
check("core layer: no core layer with a thickness takes the thickest",
      model.core_layer(LAYERS, -1, -1).index == 4 and
      model.core_layer(LAYERS, 2, 2).index == 4 and
      model.core_layer(LAYERS, None, None).index == 4)
check("core layer: a tie goes to the first",
      model.core_layer([model.Layer(0, 50 * MM, 1, u"a", u"Structure"),
                        model.Layer(1, 50 * MM, 1, u"b", u"Structure")],
                       0, 1).index == 0)

# The Shift-click split: exterior, core, interior.
EXT = [model.Layer(0, 92 * MM, 1, u"Brick", u"Finish1"),
       model.Layer(1, 75 * MM, 2, u"Air", u"Insulation"),
       model.Layer(2, 0.0, 3, u"Foil", u"Membrane"),
       model.Layer(3, 19.5 * MM, 4, u"Ply", u"Substrate"),
       model.Layer(4, 150 * MM, 5, u"Stud", u"Structure"),
       model.Layer(5, 0.0, 3, u"Foil", u"Membrane"),
       model.Layer(6, 12.5 * MM, 6, u"Gyp", u"Finish2")]
EXT_W = [layer.width for layer in EXT]
made, thin = model.parts(EXT, 4, 4)
check("parts: exterior 4 layers, core 1, interior 2, membranes kept in",
      [(p.side, len(p.layers)) for p in made] ==
      [(u"Exterior", 4), (u"Core", 1), (u"Interior", 2)] and not thin,
      [(p.side, len(p.layers)) for p in made])
check("parts: widths 186.5, 150, 12.5 mm",
      all(close(p.width, w * MM) for p, w in zip(made, (186.5, 150, 12.5))))
check("parts: centres +81.25, -87, -168.25 mm from the wall centre",
      all(close(model.part_shift(EXT_W, p, 0.0), c * MM)
          for p, c in zip(made, (81.25, -87.0, -168.25))),
      [model.part_shift(EXT_W, p, 0.0) / MM for p in made])
check("parts: shifts are measured from the location line",
      close(model.part_shift(EXT_W, made[1], 174.5 * MM), -261.5 * MM))
check("parts: the core part is the core layer",
      model.core_layer(made, 4, 4) is made[1])
check("parts: the material column names the thick layers and membranes",
      made[0].material_name ==
      u"Brick 92 mm + Air 75 mm + Ply 19.5 mm + 1 membrane(s)",
      made[0].material_name)
made, thin = model.parts(EXT[:6], 4, 4)
check("parts: a side of membranes only is not made, and is returned",
      [p.side for p in made] == [u"Exterior", u"Core"] and
      [layer.index for layer in thin] == [5])
for first, last, why in ((0, 6, u"outside its core"),
                         (-1, -1, u"has no core"),
                         (None, None, u"has no core")):
    try:
        model.parts(EXT, first, last)
        refused = u""
    except model.Refusal as exc:
        refused = u"{0}".format(exc)
    check(u"parts: core {0}..{1} refused, '{2}'".format(first, last, why),
          why in refused, refused)
check("parts: two layers in a part give two layer keys, one gives the "
      "layer's own key",
      model.structure_key(EXT[:1]) == model.type_key(EXT[0]) and
      model.structure_key(EXT[5:]) == (model.type_key(EXT[5]),
                                       model.type_key(EXT[6])))
check("parts: a part of several is named after the wall type, of one "
      "after its layer",
      model.part_type_name(made[0], u"Útveggur") == u"Útveggur - exterior"
      and model.part_type_name(made[1], u"Útveggur") == u"Stud 150 mm")


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


def CompoundStructureLayer(width, function, material_id):
    """Revit's constructor order: width, function, material."""
    return CSLayer(width, material_id, function)


class ShellLayerType(object):
    Exterior = "Exterior"
    Interior = "Interior"


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

    @staticmethod
    def CreateSimpleCompoundStructure(layers):
        """As far as the fake knows Revit, the core it starts with is
        not all the layers: here the first only, so the script has to
        set the shells itself."""
        layers = list(layers)
        if not layers:
            raise Exception("no layers")
        return CompoundStructure(layers, 0, 0)

    def SetNumberOfShellLayers(self, side, number):
        if side == ShellLayerType.Exterior:
            self.first = number
        else:
            self.last = len(self.layers) - 1 - number

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
        return [e.Id for e in sorted(self.doc.elements.values(),
                                     key=lambda e: e.Id.Value)
                if getattr(e, "host_id", None) == self.Id.Value]

    def cutters(self):
        """What cuts this wall, by Revit's rules as measured with the
        probe: whatever is in it, and the doors and windows (not the
        wall openings) of every wall joined to it."""
        doc = self.doc
        found = [i.Value for i in self.FindInserts()]
        if doc.joins_do_not_cut:
            return found
        for pair in doc.joins:
            if self.Id.Value not in pair:
                continue
            other = [v for v in pair if v != self.Id.Value][0]
            for element in doc.elements.values():
                if isinstance(element, FamilyInstance) and \
                        element.host_id == other:
                    found.append(element.Id.Value)
        return found

    def get_Geometry(self, options):
        if self.doc.geometry_raises:
            raise Exception("no geometry")
        return [Solid(self)]

    def GetGeneratingElementIds(self, face):
        return [ElementId(face.source)]

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


class Embedded(Wall):
    """A curtain wall embedded in another wall: an insert, not a wall
    to split. type() is not Wall, so doc.walls() leaves it out."""

    def __init__(self, doc, wall_type, host):
        Wall.__init__(self, doc, wall_type)
        self.host_id = host.Id.Value


class FamilyInstance(Element):
    """A door or window. Revit cannot build it in a wall thinner than
    `min_width`, and refuses the commit then."""

    def __init__(self, doc, name, host, min_width=0.0):
        Element.__init__(self, doc)
        self.Category = Category(name)
        self.host_id = host.Id.Value
        self.min_width = min_width


class Opening(Element):
    """A wall opening made with the Opening tool."""

    def __init__(self, doc, host, corners, rect=True):
        Element.__init__(self, doc)
        self.Category = Category(u"Rectangular Straight Wall Opening")
        self.host_id = host.Id.Value
        self.corners = corners
        self.IsRectBoundary = rect

    @property
    def BoundaryRect(self):
        if not self.IsRectBoundary:
            raise Exception("not a rectangle")
        return [XYZ(*c) for c in self.corners]


class Face(object):
    def __init__(self, source):
        self.source = source


class Solid(object):
    def __init__(self, wall):
        self.Volume = 1.0
        self.Faces = [Face(wall.Id.Value)] + [Face(v)
                                              for v in wall.cutters()]


class GeometryInstance(object):
    pass


class Options(object):
    pass


class JoinGeometryUtils(object):
    @staticmethod
    def AreElementsJoined(doc, a, b):
        return frozenset((a.Id.Value, b.Id.Value)) in doc.joins

    @staticmethod
    def JoinGeometry(doc, a, b):
        if doc.open is None:
            doc.writes_outside += 1
        if doc.join_raises:
            raise Exception("The elements cannot be joined")
        pair = frozenset((a.Id.Value, b.Id.Value))
        if pair in doc.joins:
            raise Exception("already joined")
        if not doc.join_ignored:
            doc.joins.add(pair)


class Creator(object):
    def __init__(self, doc):
        self.doc = doc

    def NewOpening(self, wall, start, end):
        if self.doc.open is None:
            self.doc.writes_outside += 1
        if self.doc.new_opening_none:
            return None
        return Opening(self.doc, wall, [start.t(), end.t()])


class Annotation(Element):
    """A dimension or tag on some elements: it goes when any of them
    does. A `fragile` one is on its wall's exterior face, and Revit
    removes it when that face moves."""

    def __init__(self, doc, name, refs, fragile=False):
        Element.__init__(self, doc)
        self.Category = Category(name)
        self.refs = [r.Id.Value for r in refs]
        self.fragile = fragile
        if fragile:
            self.face = refs[0].span(refs[0].orientation)[0]


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
        # As in Revit 2025 (the probe, 2.49.1): the doors, windows and
        # openings come along with the copy, but only the wall is
        # returned.
        for element in list(doc.elements.values()):
            if getattr(element, "host_id", None) != source.Id.Value:
                continue
            if isinstance(element, FamilyInstance):
                FamilyInstance(doc, element.Category.Name, copy,
                               element.min_width)
            elif isinstance(element, Opening):
                Opening(doc, copy, [add(c, vector.t())
                                    for c in element.corners],
                        element.IsRectBoundary)
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
        doc = self.doc
        errors = []
        before = self.saved[0]
        for value, text in doc.refuse_deleting.items():
            if value in before and value not in doc.elements:
                errors.append(text)
        for value, text in doc.refuse_changing.items():
            if value in doc.elements and \
                    doc.elements[value].__dict__ != self.saved[1][value]:
                errors.append(text)
        for element in list(doc.elements.values()):
            if isinstance(element, FamilyInstance):
                host = doc.elements.get(element.host_id)
                if host is not None and host.Width < element.min_width:
                    errors.append(u"Can't make type \"{0}\".".format(
                        element.Category.Name))
        for key, element in list(doc.elements.items()):
            if getattr(element, "fragile", False):
                wall = doc.elements.get(element.refs[0])
                if wall is not None and not close(
                        wall.span(wall.orientation)[0], element.face,
                        1e-9):
                    del doc.elements[key]      # Revit's own warning

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
        self.refuse_changing = {}
        self.joins = set()
        self.joins_do_not_cut = False
        self.join_raises = False
        self.join_ignored = False
        self.geometry_raises = False
        self.new_opening_none = False
        self.regenerations = 0
        self.Create = Creator(self)

    def Regenerate(self):
        self.regenerations += 1

    def new_id(self):
        self.next += 1
        if self.open is None and self.next > 100 and self.started:
            self.writes_outside += 1
        return ElementId(self.next)

    started = False

    def save(self):
        # The joins are state too: one left out of the snapshot would
        # outlive the rollback that should have undone it.
        return (dict(self.elements),
                dict((k, dict(e.__dict__)) for k, e in self.elements.items()),
                set(self.joins))

    def restore(self, saved):
        elements, states, joins = saved
        self.elements = dict(elements)
        for key, state in states.items():
            self.elements[key].__dict__.clear()
            self.elements[key].__dict__.update(state)
        self.joins = set(joins)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def Delete(self, element_id):
        if self.open is None:
            self.writes_outside += 1
        if self.delete_ignored:
            return [element_id]
        target = self.elements.pop(element_id.Value)
        gone = [target.Id]
        # What sits on it goes with it, and what sits on that: a door's
        # tag goes with the door that goes with the wall.
        changed = True
        while changed:
            changed = False
            values = set(e.Value for e in gone)
            for key, element in list(self.elements.items()):
                if getattr(element, "host_id", None) in values or \
                        values & set(getattr(element, "refs", [])):
                    del self.elements[key]
                    gone.append(element.Id)
                    changed = True
        self.joins = set(p for p in self.joins
                         if not p & set(e.Value for e in gone))
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
            CompoundStructureLayer=CompoundStructureLayer,
            ShellLayerType=ShellLayerType,
            FilteredElementCollector=Collector,
            ElementTransformUtils=ElementTransformUtils,
            Transaction=Transaction, TransactionGroup=TransactionGroup,
            TransactionStatus=TransactionStatus,
            IFailuresPreprocessor=IFailuresPreprocessor,
            FamilyInstance=FamilyInstance, Opening=Opening, Solid=Solid,
            GeometryInstance=GeometryInstance, Options=Options,
            JoinGeometryUtils=JoinGeometryUtils).items():
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


def run_script(doc, selected, confirm=True, script=None, shift=False):
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
    pyrevit.EXEC_PARAMS = types.SimpleNamespace(config_mode=shift)
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
dimension = Annotation(doc, u"Dimensions", [wall], fragile=True)
tag = Annotation(doc, u"Wall Tags", [wall])
span = wall.span((0.0, 1.0, 0.0))
original_id = wall.Id.Value
rec = run_script(doc, [wall])

check("S1: ran without raising", rec.raised is None, rec.raised)
walls = doc.walls()
check("S1: three walls, the original one of them, as the timber",
      len(walls) == 3 and original_id in doc.elements and
      wall.WallType.Name == u"Timbur 95 mm",
      [(w.Id.Value, w.WallType.Name) for w in walls])
check("S1: both gypsum walls joined to the original",
      doc.joins == set(frozenset((original_id, w.Id.Value))
                       for w in walls if w is not wall), doc.joins)
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
check("S1: the dimension on the moved face went, the tag stayed",
      dimension.Id.Value not in doc.elements and
      tag.Id.Value in doc.elements)
check("S1: both were named before the question",
      u"has 1 Dimensions, 1 Wall Tags on it" in rec.text() and
      u"2 dimension(s), tag(s)" in rec.questions[0], rec.text())
check("S1: the lost dimension is reported after",
      u"Revit removed 1 Dimensions as the wall narrowed" in rec.text(),
      rec.text())
check("S1: one question, asked once", len(rec.questions) == 1)
check("S1: one group, assimilated",
      len(doc.groups) == 1 and doc.groups[0].state == "assimilated")
check("S1: nothing written outside a transaction",
      doc.writes_outside == 0, doc.writes_outside)
check("S1: the report says 1 wall split",
      u"1 wall(s) split." in rec.alerts[-1], rec.alerts)
check("S1: two rows made, the original kept",
      rec.text().count(u"| made: [") == 2 and
      u"| kept: [{0}], the original wall |".format(original_id)
      in rec.text(), rec.text())
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
odd_opening = Wall(doc, wall_type)
Opening(doc, odd_opening, [(1, 0, 0), (2, 0, 2)], rect=False)
embedding = Wall(doc, wall_type)
Embedded(doc, WallType(doc, u"Gler 2", CompoundStructure([], -1, -1),
                       WallKind.Curtain), embedding)
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
refused = [curtain, curved, odd_opening, embedding, grouped, pinned, single,
           stacked_up, no_core]
before = dict(doc.elements)
rec = run_script(doc, refused + [not_a_wall])
check("S6: refused walls: nothing changed",
      doc.elements == before and doc.commits == 0, doc.commits)
check("S6: refused walls: not asked", not rec.questions, rec.questions)
for words in (u"curtain or stacked", u"is curved", u"not a rectangle",
              u"has Walls {0} in it".format(embedding.Id.Value + 2),
              u"in a group", u"is pinned", u"only one layer",
              u"change up its height", u"has no core"):
    check(u"S6: refusal says '{0}'".format(words), words in rec.text(),
          rec.text())
check("S6: refusals ran without raising", rec.raised is None, rec.raised)
check("S6: none can be split, said so",
      bool(rec.alerts) and u"None of the 9 selected" in rec.alerts[-1],
      rec.alerts)

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
doc.refuse_changing = {bad.Id.Value: u"Can't change a joined wall"}
bad_span = bad.span((0.0, 1.0, 0.0))
rec = run_script(doc, [good, bad])
check("S8: the refused wall is still there, whole",
      bad.Id.Value in doc.elements and bad.WallType is wall_type and
      bad.span((0.0, 1.0, 0.0)) == bad_span)
check("S8: no stray copies or joins from the refused wall",
      len(doc.walls()) == 4 and len(doc.joins) == 2,
      (len(doc.walls()), doc.joins))
check("S8: Revit's words reach the report",
      u"Can't change a joined wall" in rec.text(), rec.text())
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

def door_wall(**flags):
    """A partition with a door that fits any of its layers, so Revit
    accepts the commit and only the check after the run can tell."""
    doc = Document()
    wall_type, gypsum, timber = partition(doc)
    wall = Wall(doc, wall_type)
    door = FamilyInstance(doc, u"Doors", wall)
    for name, value in flags.items():
        setattr(doc, name, value)
    return doc, wall, door, run_script(doc, [wall])


doc, wall, door, rec = door_wall(delete_ignored=True)
check("S11: a copied door left in a layer is flagged",
      u"1 copied door(s), window(s) or opening(s) were left in it"
      in rec.text() and u"0 wall(s) split. 1 could not be" in
      rec.alerts[-1], rec.text())

doc, wall, door, rec = door_wall(joins_do_not_cut=True)
check("S11: a layer the door does not cut is flagged, by name",
      rec.text().count(u"not cut by Doors {0}".format(door.Id.Value)) == 2
      and u"0 wall(s) split. 1 could not be" in rec.alerts[-1],
      rec.text())

doc, wall, door, rec = door_wall(join_ignored=True)
check("S11: a join Revit did not make is flagged",
      rec.text().count(u"it is not joined to the core") == 2, rec.text())

doc, wall, door, rec = door_wall(join_raises=True)
check("S11: a join Revit refuses is flagged, the walls still split",
      rec.text().count(u"could not be joined to the core: The elements "
                       u"cannot be joined") == 2 and
      len(doc.walls()) == 3, rec.text())

doc, wall, door, rec = door_wall(geometry_raises=True)
check("S11: unreadable geometry is flagged, not passed",
      u"whether the doors, windows and openings cut it could not be read"
      in rec.text() and u"| made: [" not in rec.text(), rec.text())

doc, wall, door, rec = door_wall()
check("S11: the same wall with nothing wrong passes",
      u"1 wall(s) split." in rec.alerts[-1] and
      door.Id.Value in doc.elements and
      [i.Value for i in wall.FindInserts()] == [door.Id.Value],
      rec.text())

# --------------------------------------------------------------------------
# S12. The wall from the probe: an exterior wall, core off centre, with a
# door, a window and a wall opening
# --------------------------------------------------------------------------

doc = Document()
brick = Material(doc, u"Brick, Common")
air = Material(doc, u"Air")
foil = Material(doc, u"Vapour Retarder")
ply = Material(doc, u"Plywood, Sheathing")
stud = Material(doc, u"Metal Stud Layer")
board = Material(doc, u"Gypsum Wall Board")
structure = CompoundStructure([
    CSLayer(92 * MM, brick.Id, "Finish1"),
    CSLayer(75 * MM, air.Id, "Insulation"),
    CSLayer(0.0, foil.Id, "Membrane"),
    CSLayer(19.5 * MM, ply.Id, "Substrate"),
    CSLayer(150 * MM, stud.Id, "Structure"),
    CSLayer(0.0, foil.Id, "Membrane"),
    CSLayer(12.5 * MM, board.Id, "Finish2")], 4, 4)
exterior_type = WallType(doc, u"Exterior - Brick on Mtl. Stud", structure)
normal = (0.0, 1.0, 0.0)
wall = Wall(doc, exterior_type)
door = FamilyInstance(doc, u"Doors", wall, min_width=100 * MM)
window = FamilyInstance(doc, u"Windows", wall, min_width=100 * MM)
hole = Opening(doc, wall, [(6.0, 0.0, 1.0), (7.0, 0.0, 3.0)])
door_tag = Annotation(doc, u"Door Tags", [door])
span = wall.span(normal)
rec = run_script(doc, [wall])

walls = doc.walls()
others = [w for w in walls if w is not wall]
check("S12: ran, five walls in place",
      rec.raised is None and layers_in_place(
          normal, span, walls,
          [92 * MM, 75 * MM, 19.5 * MM, 150 * MM, 12.5 * MM])[0],
      (rec.raised, layers_in_place(normal, span, walls,
                                   [92 * MM, 75 * MM, 19.5 * MM, 150 * MM,
                                    12.5 * MM])[1]))
check("S12: the original is the 150 mm stud core, off centre",
      wall.WallType.Name == u"Metal Stud Layer 150 mm" and
      not close(dot(wall.ends[0], normal), 0.0), wall.WallType.Name)
check("S12: the door, window and opening are still in the original",
      [i.Value for i in wall.FindInserts()] ==
      [door.Id.Value, window.Id.Value, hole.Id.Value])
check("S12: the door's tag survived",
      door_tag.Id.Value in doc.elements)
check("S12: no copied door or window left anywhere",
      len([e for e in doc.elements.values()
           if isinstance(e, FamilyInstance)]) == 2)
check("S12: every other layer joined to the core",
      all(frozenset((wall.Id.Value, w.Id.Value)) in doc.joins
          for w in others) and len(doc.joins) == 4, doc.joins)


def opening_on_its_layer(layer_wall):
    """One opening in the layer, the original's rectangle moved across
    onto the layer's own centre, by the faces."""
    inserts = [doc.elements[i.Value] for i in layer_wall.FindInserts()]
    if len(inserts) != 1 or not isinstance(inserts[0], Opening):
        return False
    exterior, interior = layer_wall.span(normal)
    centre = (exterior + interior) / 2.0
    for made, original in zip(inserts[0].corners, hole.corners):
        if not close(dot(made, normal), centre, 1e-6):
            return False
        if not (close(made[0], original[0]) and
                close(made[2], original[2])):
            return False
    return True


check("S12: each other layer has its own copy of the opening, in place",
      all(opening_on_its_layer(w) for w in others))
check("S12: every layer is cut by the door, the window and its opening",
      all(set([door.Id.Value, window.Id.Value]) <= set(w.cutters())
          for w in walls))
check("S12: reported: all made, one kept, split",
      rec.text().count(u"| made: [") == 4 and
      rec.text().count(u"| kept: [") == 1 and
      u"1 wall(s) split." in rec.alerts[-1], rec.text())
check("S12: the plan says what happens to the door and the opening",
      u"its 2 door(s) and window(s) stay in it, the 150 mm Metal Stud "
      u"Layer layer" in rec.text() and
      u"its 1 wall opening(s) are copied" in rec.text() and
      u"1 wall opening(s) will be copied" in rec.questions[0], rec.text())
check("S12: the door and window are not counted as on the wall, the "
      "door's tag is",
      u"has 1 Door Tags on it" in rec.text(), rec.text())
check("S12: two membranes listed",
      u"2 membrane layer(s)" in rec.text(), rec.text())

# --------------------------------------------------------------------------
# S13. A door too wide for the core: Revit refuses, the wall stays whole
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
door = FamilyInstance(doc, u"Doors", wall, min_width=110 * MM)
span = wall.span((0.0, 1.0, 0.0))
rec = run_script(doc, [wall])
check("S13: the wall is whole, the door in it, nothing joined",
      len(doc.walls()) == 1 and wall.WallType is wall_type and
      wall.span((0.0, 1.0, 0.0)) == span and
      [i.Value for i in wall.FindInserts()] == [door.Id.Value] and
      not doc.joins)
check("S13: Revit's words reach the report",
      u"Can't make type \"Doors\"" in rec.text() and
      u"0 wall(s) split" in rec.alerts[-1], rec.text())

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
Opening(doc, wall, [(1.0, 0.0, 0.0), (2.0, 0.0, 2.0)])
doc.new_opening_none = True
rec = run_script(doc, [wall])
check("S13: an opening Revit will not copy leaves the wall whole",
      len(doc.walls()) == 1 and wall.WallType is wall_type and
      u"a wall opening could not be copied into the 12.5 mm layer"
      in rec.text(), rec.text())

# --------------------------------------------------------------------------
# S14. Shift-click: exterior, core, interior
# --------------------------------------------------------------------------

def exterior_wall(doc, at=0.0):
    """The wall from the probe: brick, air, membrane, ply | stud core |
    membrane, gypsum, with a door, a window and a wall opening."""
    names = (u"Brick, Common", u"Air", u"Vapour Retarder",
             u"Plywood, Sheathing", u"Metal Stud Layer",
             u"Gypsum Wall Board")
    found = dict((m.Name, m) for m in doc.elements.values()
                 if isinstance(m, Material))
    mats = [found.get(n) or Material(doc, n) for n in names]
    types = [t for t in doc.wall_types()
             if t.Name == u"Exterior - Brick on Mtl. Stud"]
    wall_type = types[0] if types else WallType(
        doc, u"Exterior - Brick on Mtl. Stud", CompoundStructure([
            CSLayer(92 * MM, mats[0].Id, "Finish1"),
            CSLayer(75 * MM, mats[1].Id, "Insulation"),
            CSLayer(0.0, mats[2].Id, "Membrane"),
            CSLayer(19.5 * MM, mats[3].Id, "Substrate"),
            CSLayer(150 * MM, mats[4].Id, "Structure"),
            CSLayer(0.0, mats[2].Id, "Membrane"),
            CSLayer(12.5 * MM, mats[5].Id, "Finish2")], 4, 4))
    wall = Wall(doc, wall_type, (0.0, at, 0.0), (10.0, at, 0.0),
                key=model.FINISH_INTERIOR)
    door = FamilyInstance(doc, u"Doors", wall, min_width=100 * MM)
    window = FamilyInstance(doc, u"Windows", wall, min_width=100 * MM)
    hole = Opening(doc, wall, [(6.0, at, 1.0), (7.0, at, 3.0)])
    return wall, door, window, hole


doc = Document()
wall, door, window, hole = exterior_wall(doc)
span = wall.span(normal)
rec = run_script(doc, [wall], shift=True)
walls = doc.walls()
others = [w for w in walls if w is not wall]
ok, detail = layers_in_place(normal, span, walls,
                             [186.5 * MM, 150 * MM, 12.5 * MM])
check("S14: shift: three walls where exterior, core and interior were",
      rec.raised is None and ok, (rec.raised, detail))
check("S14: shift: the original is the core, by its one layer name",
      wall.WallType.Name == u"Metal Stud Layer 150 mm", wall.WallType.Name)
by_name = dict((w.WallType.Name, w) for w in others)
check("S14: shift: the sides are named after the wall type",
      sorted(by_name) == [u"Exterior - Brick on Mtl. Stud - exterior",
                          u"Exterior - Brick on Mtl. Stud - interior"],
      sorted(by_name))
outside = by_name.get(u"Exterior - Brick on Mtl. Stud - exterior")
structure = outside.WallType.GetCompoundStructure() if outside else None
check("S14: shift: the exterior type holds its four layers in order, "
      "membrane too",
      structure is not None and
      [(round(l.Width / MM, 2), l.Function) for l in structure.GetLayers()]
      == [(92.0, "Finish1"), (75.0, "Insulation"), (0.0, "Membrane"),
          (19.5, "Substrate")])
check("S14: shift: every layer of a part's type is core",
      all(w.WallType.GetCompoundStructure().first == 0 and
          w.WallType.GetCompoundStructure().last ==
          len(w.WallType.GetCompoundStructure().GetLayers()) - 1
          for w in others))
check("S14: shift: both sides joined to the core, the door and window "
      "cut all three",
      len(doc.joins) == 2 and
      all(frozenset((wall.Id.Value, w.Id.Value)) in doc.joins
          for w in others) and
      all(set([door.Id.Value, window.Id.Value]) <= set(w.cutters())
          for w in walls), doc.joins)
check("S14: shift: the opening is copied onto each side, in place",
      all(opening_on_its_layer(w) for w in others))
check("S14: shift: reported as three parts, no membrane left out",
      u"to split into exterior, core and interior" in rec.text() and
      u"| Exterior: 4 layer(s) |" in rec.text() and
      u"| Interior: 2 layer(s) |" in rec.text() and
      u"membrane layer(s)" not in rec.text() and
      u"the 150 mm core, and cut" in rec.text() and
      u"the exterior layers, the core and the interior" in
      rec.questions[0] and u"3 new wall type(s)" in rec.questions[0] and
      rec.text().count(u"| made: [") == 2 and
      u"1 wall(s) split." in rec.alerts[-1], rec.text())
check("S14: shift: tables whole and left aligned", table_ok(rec))

before = len(doc.wall_types())
second, door2, window2, hole2 = exterior_wall(doc, at=5.0)
rec = run_script(doc, [second], shift=True)
check("S14: shift: a second run reuses the part types it made",
      len(doc.wall_types()) == before and
      u"(new type)" not in rec.text() and
      len(doc.walls()) == 6, [t.Name for t in doc.wall_types()])

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
span = wall.span((0.0, 1.0, 0.0))
rec = run_script(doc, [wall], shift=True)
ok, detail = layers_in_place((0.0, 1.0, 0.0), span, doc.walls(),
                             [12.5 * MM, 95 * MM, 12.5 * MM])
check("S14: shift on a partition: the same three walls and one layer "
      "types as without",
      ok and sorted(w.WallType.Name for w in doc.walls()) ==
      [u"Gipsplata 12.5 mm", u"Gipsplata 12.5 mm", u"Timbur 95 mm"],
      detail)

doc = Document()
gypsum = Material(doc, u"Gipsplata")
all_core = WallType(doc, u"Tvöföld plata", CompoundStructure([
    CSLayer(12.5 * MM, gypsum.Id, "Structure"),
    CSLayer(12.5 * MM, gypsum.Id, "Structure")], 0, 1))
wall = Wall(doc, all_core)
rec = run_script(doc, [wall], shift=True)
check("S14: shift: a wall that is all core is refused, and says so",
      len(doc.walls()) == 1 and not rec.questions and
      u"no layers with a thickness outside its core" in rec.text(),
      rec.text())
rec = run_script(doc, [wall])
check("S14: the same wall splits without shift",
      len(doc.walls()) == 2, rec.text())

# --------------------------------------------------------------------------
# The checks can fail
# --------------------------------------------------------------------------

doc = Document()
wall_type, gypsum, timber = partition(doc)
wall = Wall(doc, wall_type)
door = FamilyInstance(doc, u"Doors", wall)
returned = ElementTransformUtils.CopyElement(doc, wall.Id,
                                             XYZ(0.0, 1.0, 0.0))
copy = doc.elements[returned[0].Value]
check("self-check: the fake copies the door but returns the wall alone, "
      "as Revit did",
      len(returned) == 1 and len(copy.FindInserts()) == 1)
doc.joins.add(frozenset((wall.Id.Value, copy.Id.Value)))
saved = doc.save()
doc.joins.clear()
doc.restore(saved)
check("self-check: a rollback restores the joins",
      len(doc.joins) == 1)

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
