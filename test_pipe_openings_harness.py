# -*- coding: utf-8 -*-
"""
Runs the real Pipe Openings script against a mocked Revit.

The fake is behavioural, not scripted. A host is a real slab with a
real thickness, a pipe's axis is clipped against it, and an opening the
script makes is cut into it: afterwards the axis really does run
through the hole, or really does not. So the script's own check after
the run, and a second run finding nothing, are both tested against
geometry rather than against canned answers.

The window (lib/avh_openings/window.py) is WPF and cannot run here; a
stand in returns the settings a person would have chosen.

Run outside Revit:

    python test_pipe_openings_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Model.panel",
                      "Pipe Openings.pushbutton", "script.py")

from avh_openings import model  # noqa: E402
import avh_openings  # noqa: E402

results = []
MM = 1.0 / model.MM_PER_FOOT


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

class XYZ(object):
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)

    def t(self):
        return (self.X, self.Y, self.Z)


XYZ.BasisX = XYZ(1, 0, 0)
XYZ.BasisY = XYZ(0, 1, 0)
XYZ.BasisZ = XYZ(0, 0, 1)


class Curve(object):
    pass


class Line(Curve):
    def __init__(self, a, b):
        self.a = a
        self.b = b

    @staticmethod
    def CreateBound(a, b):
        if model.length(model.sub(b.t(), a.t())) < 1e-9:
            raise Exception("curve too short")
        return Line(a, b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b

    @property
    def Direction(self):
        return XYZ(*model.unit(model.sub(self.b.t(), self.a.t())))


class Arc(Curve):
    def __init__(self, center, radius, start, end):
        self.center = center
        self.radius = radius
        self.start = start
        self.end = end

    @staticmethod
    def Create(center, radius, start, end, x_axis, y_axis):
        return Arc(center, radius, start, end)


class CurveArray(object):
    def __init__(self):
        self.items = []

    def Append(self, curve):
        self.items.append(curve)


class Transform(object):
    def __init__(self, offset):
        self.offset = offset
        self.IsIdentity = False

    def OfPoint(self, point):
        return XYZ(*model.add(point.t(), self.offset))


class Segment(object):
    def __init__(self, a, b):
        self.a = XYZ(*a)
        self.b = XYZ(*b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b


class IntersectResult(object):
    def __init__(self, segments):
        self.segments = segments
        self.SegmentCount = len(segments)

    def GetCurveSegment(self, index):
        return self.segments[index]


def interval_rect(p0, delta, low, high):
    """Parameters t in [0, 1] where low <= p0 + t delta <= high."""
    if abs(delta) < 1e-12:
        return (0.0, 1.0) if low <= p0 <= high else None
    t1 = (low - p0) / delta
    t2 = (high - p0) / delta
    return (min(t1, t2), max(t1, t2))


def intersect(a, b):
    if a is None or b is None:
        return None
    low, high = max(a[0], b[0]), min(a[1], b[1])
    return (low, high) if high > low else None


class UV(object):
    def __init__(self, u, v):
        self.U = u
        self.V = v


class ProjectResult(object):
    def __init__(self, distance, uv):
        self.Distance = distance
        self.UVPoint = uv


class Face(object):
    """One of a slab's six faces: the plane where local coordinate
    `axis` (0 u, 1 v, 2 n) equals `value`. The four edges matter: the
    reader has to pick the face the axis actually crossed, and an edge's
    normal is horizontal."""

    def __init__(self, solid, axis, value):
        self.solid = solid
        self.axis = axis
        self.value = value

    def Project(self, point):
        local = self.solid.frame.local(point.t())
        others = [local[i] for i in range(3) if i != self.axis]
        return ProjectResult(abs(local[self.axis] - self.value),
                             UV(others[0], others[1]))

    def ComputeNormal(self, uv):
        f = self.solid.frame
        return XYZ(*(f.u, f.v, f.n)[self.axis])


class Solid(object):
    """A slab: a box in a frame (u, v, n), with holes cut along `cut`'s n.

    `holes` are ("rect", cu, cv, half_u, half_v) or ("circle", cu, cv, r)
    in the cut frame's (u, v). The cut frame is the slab's own unless
    given: a sloped roof is a box in its own tilted frame but is cut
    vertically, so its holes are prisms in plan.
    """

    def __init__(self, frame, low, high, cut=None):
        self.frame = frame
        self.cut = cut or frame
        self.low = low
        self.high = high
        self.holes = []
        self.Volume = 1.0
        self.faces_unreadable = False

    @property
    def Faces(self):
        if self.faces_unreadable:
            raise Exception("no faces here")
        # The big faces first, so a reader that takes the last face it
        # sees rather than the nearest gets an edge.
        return [Face(self, axis, value) for axis in (2, 0, 1)
                for value in (self.low[axis], self.high[axis])]

    def IntersectWithCurve(self, line, options):
        f = self.frame
        a = f.local(line.a.t())
        b = f.local(line.b.t())
        d = model.sub(b, a)
        span = (0.0, 1.0)
        for i in range(3):
            span = intersect(span, interval_rect(a[i], d[i], self.low[i],
                                                 self.high[i]))
        if span is None:
            return IntersectResult([])
        pieces = [span]
        ca = self.cut.local(line.a.t())
        cd = model.sub(self.cut.local(line.b.t()), ca)
        for hole in self.holes:
            inside = self._inside(hole, ca, cd)
            if inside is None:
                continue
            cut = []
            for low, high in pieces:
                if inside[1] <= low or inside[0] >= high:
                    cut.append((low, high))
                    continue
                if inside[0] > low:
                    cut.append((low, inside[0]))
                if inside[1] < high:
                    cut.append((inside[1], high))
            pieces = cut
        segments = []
        for low, high in pieces:
            if high - low < 1e-9:
                continue
            p = model.add(line.a.t(), model.scale(
                model.sub(line.b.t(), line.a.t()), low))
            q = model.add(line.a.t(), model.scale(
                model.sub(line.b.t(), line.a.t()), high))
            segments.append(Segment(p, q))
        return IntersectResult(segments)

    @staticmethod
    def _inside(hole, a, d):
        if hole[0] == "rect":
            _, cu, cv, hu, hv = hole
            span = (-1e9, 1e9)
            span = intersect(span, interval_rect(a[0], d[0], cu - hu,
                                                 cu + hu))
            span = intersect(span, interval_rect(a[1], d[1], cv - hv,
                                                 cv + hv))
            return span
        _, cu, cv, r = hole
        px, py = a[0] - cu, a[1] - cv
        qa = d[0] ** 2 + d[1] ** 2
        qb = 2 * (px * d[0] + py * d[1])
        qc = px ** 2 + py ** 2 - r ** 2
        if qa < 1e-12:
            return (-1e9, 1e9) if qc < 0 else None
        disc = qb ** 2 - 4 * qa * qc
        if disc <= 0:
            return None
        root = math.sqrt(disc)
        return ((-qb - root) / (2 * qa), (-qb + root) / (2 * qa))


class GeometryInstance(object):
    pass


class Options(object):
    pass


# --------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------

class ElementId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.Value == self.Value

    def __hash__(self):
        return hash(self.Value)


class BoundingBox(object):
    def __init__(self, low, high):
        self.Min = XYZ(*low)
        self.Max = XYZ(*high)


class Element(object):
    _next = [1000]

    def __init__(self):
        Element._next[0] += 1
        self._id = ElementId(Element._next[0])
        self.gone = False         # deleted: Revit refuses its Id then
        self.UniqueId = u"uid-{0}".format(Element._next[0])
        self.entities = {}
        self.cuts = []            # [(host, hole)] this element has cut
        self.locked = False       # checked out by someone else

    @property
    def Id(self):
        """A deleted element's Id raises, as in Revit (2.48.0's report
        crashed on it, reading the Id of an opening it had re-cut)."""
        if self.gone:
            raise Exception(u"The referenced object is not valid, "
                            u"possibly because it has been deleted from "
                            u"the database, or its creation was undone.")
        return self._id

    def get_BoundingBox(self, view):
        return None

    def SetEntity(self, entity):
        if ExtensibleStorage.broken:
            raise Exception(u"extensible storage is unavailable")
        if ExtensibleStorage.lossy:
            return                    # says nothing, keeps nothing
        self.entities[entity.schema.guid] = entity

    def GetEntity(self, schema):
        return self.entities.get(schema.guid) or ESEntity(None)


class Opening(Element):
    """What NewOpening returns: a wall, floor or roof opening."""


# --------------------------------------------------------------------------
# Extensible storage
# --------------------------------------------------------------------------

class _Generic(object):
    """entity.Set[String](...) and entity.Get[String](...) in IronPython
    are a generic method indexed by a type."""

    def __init__(self, function):
        self.function = function

    def __getitem__(self, kind):
        return self.function


class ESSchema(object):
    registry = {}

    def __init__(self, guid, fields):
        self.guid = guid
        self.fields = fields

    @staticmethod
    def Lookup(guid):
        return ESSchema.registry.get(guid)


class SchemaBuilder(object):
    def __init__(self, guid):
        self.guid = guid
        self.fields = {}
        self.name = None

    def SetReadAccessLevel(self, level):
        pass

    def SetWriteAccessLevel(self, level):
        pass

    def SetSchemaName(self, name):
        self.name = name

    def AddSimpleField(self, name, kind):
        self.fields[name] = kind

    def Finish(self):
        if self.guid in ESSchema.registry:
            raise Exception(u"a schema with this GUID already exists")
        schema = ESSchema(self.guid, dict(self.fields))
        ESSchema.registry[self.guid] = schema
        return schema


class ESEntity(object):
    def __init__(self, schema):
        self.schema = schema
        self.values = {}
        self.Set = _Generic(self._set)
        self.Get = _Generic(self._get)

    def _set(self, name, value):
        if self.schema is None or name not in self.schema.fields:
            raise Exception(u"no field {0}".format(name))
        self.values[name] = value

    def _get(self, name):
        return self.values.get(name)

    def IsValid(self):
        return self.schema is not None


ExtensibleStorage = types.SimpleNamespace(
    Schema=ESSchema, SchemaBuilder=SchemaBuilder, Entity=ESEntity,
    AccessLevel=types.SimpleNamespace(Public="public"), broken=False,
    lossy=False)


class Host(Element):
    def __init__(self, frame, low, high):
        Element.__init__(self)
        self.solid = Solid(frame, low, high)
        self.params = {}
        # Where Revit insists a vertical opening's sketch lies, and how
        # it says no: "raise" from NewOpening, or "failure" at commit.
        # None takes a sketch at any height. Seen in Revit 2025 on a
        # roof: "Can't change plane of Opening Cut Sketch."
        self.sketch_z = None
        self.sketch_refusal = "raise"

    def get_Parameter(self, name):
        return self.params.get(name)

    def get_Geometry(self, options):
        return [self.solid]

    def get_BoundingBox(self, view):
        f = self.solid.frame
        corners = [f.to_world(*c) for c in
                   model.box_corners(self.solid.low, self.solid.high)]
        low, high = model.box_of(corners)
        return BoundingBox(low, high)


class WallKind(object):
    Basic = "basic"
    Curtain = "curtain"


class WallType(object):
    def __init__(self, kind):
        self.Kind = kind


class Location(object):
    def __init__(self, curve=None, point=None):
        self.Curve = curve
        self.Point = point


class Wall(Host):
    """A straight wall along X from x0 to x1 at y = 0, `thickness` thick,
    from z0 to z1."""

    def __init__(self, x0, x1, thickness, z0=0.0, z1=10.0, curtain=False,
                 curved=False):
        frame = model.wall_frame((1.0, 0.0, 0.0))
        # n = u x v = (1,0,0) x (0,0,1) = (0,-1,0): depth is -y.
        Host.__init__(self, frame, (x0, z0, -thickness / 2.0),
                      (x1, z1, thickness / 2.0))
        self.WallType = WallType(WallKind.Curtain if curtain
                                 else WallKind.Basic)
        curve = Arc(XYZ(0, 0, 0), 1.0, 0, 1) if curved else \
            Line(XYZ(x0, 0, z0), XYZ(x1, 0, z0))
        self.Location = Location(curve=curve)
        self.LevelId = ElementId(1)


class LayeredWall(Wall):
    """A compound wall Revit hands back as one solid per layer. The
    layers share their holes, as an opening cuts every layer."""

    def __init__(self, x0, x1, thickness):
        Wall.__init__(self, x0, x1, thickness)
        whole = self.solid
        self.solid = Solid(whole.frame, whole.low,
                           (whole.high[0], whole.high[1], 0.0))
        self.outer = Solid(whole.frame, (whole.low[0], whole.low[1], 0.0),
                           whole.high)
        self.outer.holes = self.solid.holes

    def get_Geometry(self, options):
        return [self.solid, self.outer]


class Floor(Host):
    def __init__(self, low, high):
        Host.__init__(self, model.slab_frame(), low, high)


class RoofBase(Host):
    def __init__(self, low, high):
        Host.__init__(self, model.slab_frame(), low, high)


class SlopedRoof(RoofBase):
    """A roof `thickness` thick rising `slope` degrees towards +x, its
    underside through the origin, cut vertically like any roof."""

    def __init__(self, slope, thickness, extent=20.0):
        RoofBase.__init__(self, (0, 0, 0), (1, 1, 1))
        angle = math.radians(slope)
        frame = model.Frame((math.cos(angle), 0.0, math.sin(angle)),
                            (0.0, 1.0, 0.0),
                            (-math.sin(angle), 0.0, math.cos(angle)))
        self.solid = Solid(frame, (-extent, -extent, 0.0),
                           (extent, extent, thickness),
                           cut=model.slab_frame())


class Parameter(object):
    def __init__(self, value):
        self.value = value
        self.HasValue = True
        self.IsReadOnly = False

    def AsDouble(self):
        return self.value

    def Set(self, value):
        self.value = value
        return True


class BuiltInParameter(object):
    RBS_PIPE_OUTER_DIAMETER = "pipe_od"
    RBS_CONDUIT_OUTER_DIAM_PARAM = "conduit_od"
    RBS_REFERENCE_INSULATION_THICKNESS = "insulation"
    ROOF_LEVEL_OFFSET_PARAM = "roof_offset"
    WALL_BASE_OFFSET = "shaft_base"
    WALL_TOP_OFFSET = "shaft_top"
    FLOOR_HEIGHTABOVELEVEL_PARAM = "floor_offset"


class Level(Element):
    def __init__(self, elevation):
        Element.__init__(self)
        self.Elevation = elevation


class Shaft(Opening):
    """A shaft opening from `base` to `top`, carried by its offsets. It
    cuts every floor and roof it reaches within that band: the band is
    run as a vertical line through the shaft's centre in plan, and any
    host whose solid that line meets is cut, as a real shaft would cut
    it. So a shaft left running from level to level visibly cuts a floor
    below the roof it was meant for."""

    def __init__(self, base, top, curves, refuse=False):
        """`refuse`: False, "refuse" (Set returns False) or "ignore"
        (Set returns True and keeps 0, which a read back must catch)."""
        Element.__init__(self)
        self.base = base
        self.top = top
        self.curves = curves
        base_offset = Parameter(0.0)
        if refuse == "refuse" or refuse is True:
            base_offset.Set = lambda value: False
        elif refuse == "ignore":
            base_offset.Set = lambda value: True
        self.params = {"shaft_base": base_offset,
                       "shaft_top": Parameter(0.0)}
        self.cut = []

    def get_Parameter(self, name):
        return self.params.get(name)

    def band(self):
        return (self.base.Elevation + self.params["shaft_base"].value,
                self.top.Elevation + self.params["shaft_top"].value)

    def plan(self):
        """("rect", x, y, hx, hy) or ("circle", x, y, r) in plan."""
        arcs = [c for c in self.curves.items if isinstance(c, Arc)]
        if arcs:
            return ("circle", arcs[0].center.X, arcs[0].center.Y,
                    arcs[0].radius)
        xs = [c.a.X for c in self.curves.items]
        ys = [c.a.Y for c in self.curves.items]
        return ("rect", (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2)

    def reaches(self, host):
        if not isinstance(host, (Floor, RoofBase)):
            return False
        low, high = self.band()
        if high - low < 1e-9:
            return False
        shape = self.plan()
        line = Line(XYZ(shape[1], shape[2], low), XYZ(shape[1], shape[2],
                                                     high))
        return host.solid.IntersectWithCurve(line, None).SegmentCount > 0

    def hole(self, host):
        return self.plan()


class BuiltInCategory(object):
    OST_PipeCurves = "pipes"
    OST_Conduit = "conduits"


class Pipe(Element):
    def __init__(self, a, b, outer_mm, insulation_mm=0.0, conduit=False):
        Element.__init__(self)
        self.category = "conduits" if conduit else "pipes"
        self.Location = Location(curve=Line(XYZ(*a), XYZ(*b)))
        self.params = {
            ("conduit_od" if conduit else "pipe_od"): Parameter(
                outer_mm * MM)}
        if insulation_mm:
            self.params["insulation"] = Parameter(insulation_mm * MM)
        self.a = a
        self.b = b

    def get_Parameter(self, name):
        return self.params.get(name)

    def get_BoundingBox(self, view):
        low, high = model.box_of([self.a, self.b])
        r = 0.5
        return BoundingBox(model.sub(low, (r, r, r)),
                           model.add(high, (r, r, r)))


class Family(object):
    def __init__(self, name):
        self.Name = name


class FamilySymbol(Element):
    def __init__(self, name):
        Element.__init__(self)
        self.Family = Family(name)
        self.IsActive = False

    def Activate(self):
        self.IsActive = True


class FamilyInstance(Element):
    def __init__(self, doc, point, symbol, wall, off_by=None):
        Element.__init__(self)
        self.doc = doc
        self.Symbol = symbol
        self.wall = wall
        self.off_by = off_by
        self._point = point.t()
        if off_by:
            self._point = model.add(self._point, off_by)
        self.Location = Location(point=XYZ(0, 0, 0))   # until regenerated
        self.params = {u"AVH_Diameter": Parameter(0.0)}

    def LookupParameter(self, name):
        return self.params.get(name)

    def refresh(self):
        self.Location = Location(point=XYZ(*self._point))

    def cut(self):
        f = self.wall.solid.frame
        u, v, _ = f.local(self._point)
        return ("circle", u, v, self.params[u"AVH_Diameter"].value / 2.0)


class RevitLinkInstance(Element):
    def __init__(self, link_doc, offset, name=u"MEP.rvt"):
        Element.__init__(self)
        self.link_doc = link_doc
        self.offset = offset
        self.Name = name

    def GetLinkDocument(self):
        return self.link_doc

    def GetTotalTransform(self):
        return Transform(self.offset)


# --------------------------------------------------------------------------
# Document and transactions
# --------------------------------------------------------------------------

class Create(object):
    def __init__(self, doc):
        self.doc = doc

    def NewOpening(self, host, a, b):
        """NewOpening(wall, XYZ, XYZ), NewOpening(host, CurveArray,
        bool) or NewOpening(level, level, CurveArray), a shaft: the three
        overloads the script uses."""
        doc = self.doc
        if isinstance(host, Level):
            return self.new_shaft(host, a, b)
        perpendicular = b if isinstance(a, CurveArray) else None
        doc.calls.append(("NewOpening", host, a, b, perpendicular))
        if host in doc.refuse_hosts:
            doc.pending_errors.append(u"Can't cut instance out of element")
        f = host.solid.cut
        if isinstance(a, CurveArray):
            if perpendicular is not False:
                raise Exception("expected a vertical cut")
            heights = [c.center.Z if isinstance(c, Arc) else c.a.Z
                       for c in a.items]
            doc.sketch_heights.append(heights[0])
            if host.sketch_z is not None and \
                    any(abs(z - host.sketch_z) > 1e-9 for z in heights):
                message = u"Can't change plane of Opening Cut Sketch."
                if host.sketch_refusal == "raise":
                    raise Exception(message)
                doc.pending_errors.append(message)
            arcs = [c for c in a.items if isinstance(c, Arc)]
            if arcs:
                u, v, _ = f.local(arcs[0].center.t())
                hole = ("circle", u, v, arcs[0].radius)
            else:
                points = [c.a.t() for c in a.items]
                us = [f.local(p)[0] for p in points]
                vs = [f.local(p)[1] for p in points]
                hole = ("rect", (min(us) + max(us)) / 2,
                        (min(vs) + max(vs)) / 2,
                        (max(us) - min(us)) / 2, (max(vs) - min(vs)) / 2)
        else:
            if not isinstance(host, Wall):
                raise Exception("the two point form is for walls")
            p, q = f.local(a.t()), f.local(b.t())
            hole = ("rect", (p[0] + q[0]) / 2, (p[1] + q[1]) / 2,
                    abs(q[0] - p[0]) / 2, abs(q[1] - p[1]) / 2)
        if doc.misplace:
            hole = (hole[0], hole[1] + 5.0) + hole[2:]
        opening = Opening()
        doc.pending.append((host, hole, opening))
        return opening

    def new_shaft(self, base, top, curves):
        doc = self.doc
        if doc.no_shafts:
            raise Exception(u"Revit would not make a shaft here")
        shaft = Shaft(base, top, curves, refuse=doc.shaft_params_refuse)
        doc.pending_shafts.append(shaft)
        return shaft

    def NewFamilyInstance(self, point, symbol, host, level, structural):
        doc = self.doc
        if not symbol.IsActive:
            raise Exception("symbol not active")
        instance = FamilyInstance(doc, point, symbol, host,
                                  off_by=doc.place_off_by)
        doc.instances.append(instance)
        doc.pending_instances.append(instance)
        return instance


class Document(object):
    def __init__(self, hosts=(), pipes=(), links=(), symbols=(),
                 title=u"Project"):
        self.hosts = list(hosts)
        self.pipes = list(pipes)
        self.links = list(links)
        self.symbols = list(symbols)
        self.Title = title
        self.IsFamilyDocument = False
        self.Create = Create(self)
        self.calls = []
        self.pending = []
        self.pending_instances = []
        self.pending_errors = []
        self.instances = []
        self.refuse_hosts = set()
        self.pending_shafts = []
        self.shafts = []
        self.openings = []        # committed openings and shafts
        self.deleted = []
        self.undo = []
        self.no_shafts = False
        self.shaft_params_refuse = False
        self.levels = []
        self.sketch_heights = []
        self.misplace = False
        self.place_off_by = None
        self.transactions = []
        self.groups = []
        self.loaded_from = []

    def GetElement(self, element_id):
        everything = (self.hosts + self.pipes + self.instances +
                      self.levels + self.openings + self.links)
        for element in everything:
            if isinstance(element_id, str):
                if element.UniqueId == element_id:
                    return element
            elif element.Id == element_id:
                return element
        return None

    def Regenerate(self):
        for instance in self.pending_instances:
            instance.refresh()

    def LoadFamily(self, path):
        self.loaded_from.append(path)
        return False

    # transaction plumbing
    def commit(self):
        if self.pending_errors:
            self.pending_errors = []
            self.rollback()
            return False
        for host, hole, opening in self.pending:
            host.solid.holes.append(hole)
            opening.cuts = [(host, hole)]
            self.openings.append(opening)
        for shaft in self.pending_shafts:
            self.shafts.append(shaft)
            self.openings.append(shaft)
            for host in self.hosts:
                if shaft.reaches(host):
                    hole = shaft.hole(host)
                    host.solid.holes.append(hole)
                    shaft.cut.append(host)
                    shaft.cuts.append((host, hole))
        self.pending_shafts = []
        for instance in self.pending_instances:
            instance.refresh()
            hole = instance.cut()
            instance.wall.solid.holes.append(hole)
            instance.cuts = [(instance.wall, hole)]
        self.pending = []
        self.pending_instances = []
        self.undo = []
        return True

    def Delete(self, element_id):
        """Delete an opening, shaft or round family instance, taking its
        holes out of the hosts at once, so a regenerate sees the host
        whole. Undone by a rollback: state that outlived a rolled back
        transaction is the harness bug this kind of fake has had most."""
        found = None
        for collection in (self.openings, self.instances):
            for element in collection:
                if element.Id == element_id:
                    found = (collection, element)
        if found is None:
            raise Exception(u"no element {0} to delete".format(
                element_id.Value))
        collection, element = found
        if element.locked:
            raise Exception(u"it is checked out by another user")
        self.deleted.append(element)
        element.gone = True
        index = collection.index(element)
        collection.remove(element)
        removed = []
        for host, hole in element.cuts:
            if hole in host.solid.holes:
                position = host.solid.holes.index(hole)
                host.solid.holes.pop(position)
                removed.append((host, position, hole))

        def put_back():
            element.gone = False
            collection.insert(index, element)
            for host, position, hole in reversed(removed):
                host.solid.holes.insert(position, hole)
            self.deleted.remove(element)
        self.undo.append(put_back)
        return [element_id]

    def rollback(self):
        for put_back in reversed(self.undo):
            put_back()
        self.undo = []
        self.pending_shafts = []
        for instance in self.pending_instances:
            self.instances.remove(instance)
        self.pending = []
        self.pending_instances = []
        self.pending_errors = []


class TransactionStatus(object):
    Committed = "Committed"
    RolledBack = "RolledBack"


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
        self.started = True

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.preprocessor = getattr(options, "preprocessor", None)

    def RollBack(self):
        self.doc.rollback()
        self.status = TransactionStatus.RolledBack
        return self.status

    def Commit(self):
        if self.doc.pending_errors and self.preprocessor is not None:
            self.preprocessor.PreprocessFailures(Accessor(
                [FailureMessage(t) for t in self.doc.pending_errors]))
        ok = self.doc.commit()
        self.status = TransactionStatus.Committed if ok \
            else TransactionStatus.RolledBack
        return self.status


class TransactionGroup(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.state = None
        doc.groups.append(self)

    def Start(self):
        self.state = "started"

    def RollBack(self):
        self.state = "rolled back"
        # Undo every committed hole made in the group.
        for host in self.doc.hosts:
            host.solid.holes = list(self.snapshot.get(host, []))

    def Assimilate(self):
        self.state = "assimilated"
        return self.doc.assimilate_status


class Collector(object):
    def __init__(self, doc):
        self.doc = doc
        self.result = []

    def OfCategory(self, category):
        self.result = [p for p in self.doc.pipes if p.category == category]
        return self

    def OfClass(self, cls):
        if cls is RevitLinkInstance:
            self.result = list(self.doc.links)
        elif cls is FamilySymbol:
            self.result = list(self.doc.symbols)
        elif cls is Level:
            self.result = list(self.doc.levels)
        elif cls is Opening:
            self.result = list(self.doc.openings)
        elif cls is FamilyInstance:
            self.result = list(self.doc.instances)
        else:
            self.result = []
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        return iter(self.result)


class ElementTransformUtils(object):
    @staticmethod
    def MoveElement(doc, element_id, delta):
        for instance in doc.instances:
            if instance.Id == element_id:
                if doc.move_refused:
                    return
                instance._point = model.add(instance._point, delta.t())
                instance.refresh()


def make_db():
    db = types.ModuleType("Autodesk.Revit.DB")
    for name, value in dict(
            XYZ=XYZ, Line=Line, Arc=Arc, CurveArray=CurveArray,
            Solid=Solid, GeometryInstance=GeometryInstance,
            Options=Options, Wall=Wall, Floor=Floor, RoofBase=RoofBase,
            Level=Level, Opening=Opening, FamilyInstance=FamilyInstance,
            ExtensibleStorage=ExtensibleStorage,
            WallKind=WallKind, BuiltInParameter=BuiltInParameter,
            BuiltInCategory=BuiltInCategory, FamilySymbol=FamilySymbol,
            RevitLinkInstance=RevitLinkInstance,
            FilteredElementCollector=Collector,
            SolidCurveIntersectionOptions=object,
            Transaction=Transaction, TransactionGroup=TransactionGroup,
            TransactionStatus=TransactionStatus,
            ElementTransformUtils=ElementTransformUtils,
            IFailuresPreprocessor=object).items():
        setattr(db, name, value)
    db.ViewDetailLevel = types.SimpleNamespace(Fine="fine")
    db.FailureSeverity = types.SimpleNamespace(Warning="Warning")
    db.FailureProcessingResult = types.SimpleNamespace(
        ProceedWithRollBack="rollback", Continue="continue")
    db.Structure = types.SimpleNamespace(
        StructuralType=types.SimpleNamespace(NonStructural="ns"))
    return db


# --------------------------------------------------------------------------
# pyRevit stand ins and the runner
# --------------------------------------------------------------------------

class Config(object):
    def __init__(self, stored=None):
        self.__dict__.update(stored or {})

    def get_option(self, key, default):
        return self.__dict__.get(key, default)


class Recorder(object):
    def __init__(self, settings, confirm=True):
        self.settings = settings
        self.confirm = confirm
        self.alerts = []
        self.lines = []
        self.asked = []
        self.config = Config()
        self.saved = 0
        self.questions = []

    def ask(self, settings, hosts_line):
        self.asked.append((settings, hosts_line))
        return self.settings

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes"):
            self.questions.append(message)
            if isinstance(self.confirm, list):
                # One answer per yes/no question, in order. A question
                # nobody expected gets "no", and is recorded.
                return self.confirm.pop(0) if self.confirm else False
            return self.confirm
        return True

    def print_md(self, text):
        self.lines.append(text)

    def text(self):
        return u"\n".join(self.lines)


def run_script(doc, selected, settings, confirm=True, recorder=None,
               script=None):
    rec = recorder or Recorder(settings, confirm)
    rec.settings = settings
    rec.confirm = confirm
    db = make_db()

    for host in doc.hosts:
        doc.__dict__.setdefault("assimilate_status",
                                TransactionStatus.Committed)
    doc.__dict__.setdefault("assimilate_status", TransactionStatus.Committed)
    doc.__dict__.setdefault("move_refused", False)

    original_start = TransactionGroup.Start

    def start(group):
        group.snapshot = dict((h, list(h.solid.holes)) for h in doc.hosts)
        original_start(group)
    TransactionGroup.Start = start

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

    def save_config():
        rec.saved += 1

    pyrevit.script = types.SimpleNamespace(
        get_output=lambda: output, get_logger=lambda: logger,
        get_config=lambda: rec.config, save_config=save_config)
    window = types.ModuleType("avh_openings.window")
    window.ask = rec.ask
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_openings.window"] = window
    avh_openings.window = window
    try:
        runpy.run_path(script or SCRIPT, run_name="__main__")
    except Exception as exc:
        rec.raised = exc
    finally:
        TransactionGroup.Start = original_start
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_openings.window", None)
        del avh_openings.window
    return rec


def settings(**kwargs):
    values = dict(shape=model.RECTANGULAR, mode=model.GAP, amount=25.0,
                  round_to=10.0, links=True)
    values.update(kwargs)
    return model.Settings(**values)


def crossing_count(host, pipe):
    a, b = pipe.Location.Curve.a, pipe.Location.Curve.b
    return host.solid.IntersectWithCurve(Line(a, b), None).SegmentCount


def last_hole(host):
    return host.solid.holes[-1] if host.solid.holes else None


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

# 1. A floor, a vertical pipe through it, rectangular.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
rec = run_script(doc, [floor], settings())
hole = last_hole(floor)
check("floor rect: no exception", not hasattr(rec, "raised"),
      getattr(rec, "raised", ""))
check("floor rect: one opening cut", len(floor.solid.holes) == 1,
      floor.solid.holes)
check("floor rect: 160 x 160, centred on the pipe",
      hole and hole[0] == "rect" and close(hole[1], 5.0) and
      close(hole[2], 6.0) and close(hole[3], 80 * MM) and
      close(hole[4], 80 * MM), hole)
check("floor rect: the pipe no longer meets the floor",
      crossing_count(floor, pipe) == 0)
check("floor rect: vertical cut asked for",
      doc.calls and doc.calls[0][4] is False)
check("floor rect: one group, assimilated",
      len(doc.groups) == 1 and doc.groups[0].state == "assimilated")
check("floor rect: reported as made", u"| made |" in rec.text(),
      rec.text())
check("floor rect: confirmed before cutting",
      any(u"Cut 1 opening" in a for a in rec.alerts), rec.alerts)
check("floor rect: settings remembered", rec.saved == 1 and
      rec.config.__dict__.get("shape") == model.RECTANGULAR)
check("floor rect: the window was told what is selected",
      rec.asked and u"1 floor" in rec.asked[0][1])

# 2. Run it again: the pipe runs through the hole, nothing to do.
rec2 = run_script(doc, [floor], settings())
check("rerun: nothing new cut", len(floor.solid.holes) == 1)
check("rerun: says nothing passes through",
      any(u"No pipe or conduit passes" in a for a in rec2.alerts),
      rec2.alerts)

# 3. Circular in a floor, with insulation.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 60, insulation_mm=20)
doc = Document(hosts=[floor], pipes=[pipe])
rec = run_script(doc, [floor], settings(shape=model.CIRCULAR))
hole = last_hole(floor)
check("floor circle: insulation counted, 60 + 2x20 + 2x25 = 150",
      hole and hole[0] == "circle" and close(hole[3], 75 * MM), hole)
check("floor circle: profile is two arcs",
      doc.calls and isinstance(doc.calls[0][2], CurveArray) and
      len([c for c in doc.calls[0][2].items if isinstance(c, Arc)]) == 2)
check("floor circle: pipe clear", crossing_count(floor, pipe) == 0)

# 4. A wall, a pipe square through it, and one at 30 degrees.
wall = Wall(0.0, 30.0, 300 * MM)
square = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
skew = 30.0
slanted = Pipe((15.0 - 3 * math.tan(math.radians(skew)), -3.0, 4.0),
               (15.0 + 3 * math.tan(math.radians(skew)), 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[square, slanted])
rec = run_script(doc, [wall], settings(round_to=0))
holes = sorted(wall.solid.holes, key=lambda h: h[1])
check("wall: two openings", len(holes) == 2, holes)
check("wall: wall openings use the two point form",
      all(isinstance(c[2], XYZ) for c in doc.calls))
if len(holes) == 2:
    check("wall square: 160 x 160 at the pipe",
          close(holes[0][1], 5.0) and close(holes[0][2], 4.0) and
          close(holes[0][3], 80 * MM) and close(holes[0][4], 80 * MM),
          holes[0])
    t = math.tan(math.radians(skew))
    expected_half = (300 * MM * t + 2 * 55 * MM * math.sqrt(1 + t * t)
                     + 2 * 25 * MM) / 2
    check("wall 30 degrees: wider along the wall, by the formula",
          close(holes[1][3], expected_half), (holes[1][3], expected_half))
    check("wall 30 degrees: height unchanged",
          close(holes[1][4], 80 * MM), holes[1])
check("wall: both pipes clear", crossing_count(wall, square) == 0 and
      crossing_count(wall, slanted) == 0)
check("wall: skew reported", u"30\u00b0" in rec.text())

# 5. A pipe almost along the wall is skipped, and said so.
wall = Wall(0.0, 30.0, 300 * MM)
along = Pipe((5.0 - 3 * math.tan(math.radians(80)), -3.0, 4.0),
             (5.0 + 3 * math.tan(math.radians(80)), 3.0, 4.0), 110)
fine = Pipe((25.0, -3.0, 4.0), (25.0, 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[along, fine])
rec = run_script(doc, [wall], settings())
check("skew 80: only the square one cut", len(wall.solid.holes) == 1)
check("skew 80: the reason is in the report",
      u"skipped: it runs 80 degrees" in rec.text(), rec.text())
check("skew 80: the dialog says one is skipped",
      any(u"1 crossing(s) will be skipped" in a for a in rec.alerts))

# 6. Circular in a wall with no family: walls skipped, floors still cut.
wall = Wall(0.0, 30.0, 300 * MM)
floor = Floor((0.0, 0.0, 20.0), (20.0, 20.0, 20.0 + 250 * MM))
through_wall = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
through_floor = Pipe((5.0, 6.0, 17.0), (5.0, 6.0, 23.0), 110)
doc = Document(hosts=[wall, floor], pipes=[through_wall, through_floor])
rec = run_script(doc, [wall, floor], settings(shape=model.CIRCULAR))
check("no family: the wall is not cut", not wall.solid.holes)
check("no family: the floor is", len(floor.solid.holes) == 1)
check("no family: the reason names the family",
      u"needs the AVH_Round_Opening family" in rec.text(), rec.text())

# 7. Circular in a wall with the family loaded.
wall = Wall(0.0, 30.0, 300 * MM)
pipe = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
symbol = FamilySymbol(u"AVH_Round_Opening")
doc = Document(hosts=[wall], pipes=[pipe], symbols=[symbol])
rec = run_script(doc, [wall], settings(shape=model.CIRCULAR))
check("round wall: one instance", len(doc.instances) == 1,
      getattr(rec, "raised", ""))
check("round wall: symbol activated", symbol.IsActive)
check("round wall: diameter set to 160",
      doc.instances and close(
          doc.instances[0].params[u"AVH_Diameter"].value, 160 * MM))
check("round wall: pipe clear", crossing_count(wall, pipe) == 0)

# 8. Revit puts the round opening 200 mm along the wall: moved back.
wall = Wall(0.0, 30.0, 300 * MM)
pipe = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[pipe],
               symbols=[FamilySymbol(u"AVH_Round_Opening")])
doc.place_off_by = (200 * MM, 0.0, 0.0)
rec = run_script(doc, [wall], settings(shape=model.CIRCULAR))
check("round wall moved: back on the pipe",
      doc.instances and close(doc.instances[0]._point[0], 5.0),
      doc.instances and doc.instances[0]._point)
check("round wall moved: pipe clear", crossing_count(wall, pipe) == 0)

# ...and when Revit will not move it, it is not kept.
wall = Wall(0.0, 30.0, 300 * MM)
pipe = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[pipe],
               symbols=[FamilySymbol(u"AVH_Round_Opening")])
doc.place_off_by = (200 * MM, 0.0, 0.0)
doc.move_refused = True
rec = run_script(doc, [wall], settings(shape=model.CIRCULAR))
check("round wall stuck: nothing kept", not doc.instances and
      not wall.solid.holes)
check("round wall stuck: said so", u"would not move it back" in rec.text(),
      rec.text())

# 9. A pipe in a link, moved by the link's transform.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
linked = Pipe((1.0, 1.0, -3.0), (1.0, 1.0, 3.0), 110)
link_doc = Document(pipes=[linked], title=u"MEP_Lagnir")
doc = Document(hosts=[floor], links=[RevitLinkInstance(link_doc,
                                                       (4.0, 5.0, 0.0))])
rec = run_script(doc, [floor], settings())
hole = last_hole(floor)
check("link: cut where the link puts the pipe",
      hole and close(hole[1], 5.0) and close(hole[2], 6.0), hole)
check("link: named in the report", u"in MEP_Lagnir" in rec.text())

# ...and not when links are switched off.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
doc = Document(hosts=[floor], links=[RevitLinkInstance(link_doc,
                                                       (4.0, 5.0, 0.0))])
rec = run_script(doc, [floor], settings(links=False))
check("links off: nothing cut", not floor.solid.holes)
check("links off: the message says this model only",
      any(u"this model only" in a for a in rec.alerts), rec.alerts)

# An unloaded link is named.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe],
               links=[RevitLinkInstance(None, (0, 0, 0), u"Raflagnir.rvt")])
rec = run_script(doc, [floor], settings())
check("unloaded link: named", u"Raflagnir.rvt" in rec.text(), rec.text())

# 10. Revit refuses one host: reported, the other still cut.
floor_a = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
floor_b = Floor((0.0, 0.0, 20.0), (20.0, 20.0, 20.0 + 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 23.0), 110)
doc = Document(hosts=[floor_a, floor_b], pipes=[pipe])
doc.refuse_hosts.add(floor_a)
rec = run_script(doc, [floor_a, floor_b], settings())
check("refused: the refused one is not cut", not floor_a.solid.holes)
check("refused: the other is", len(floor_b.solid.holes) == 1)
check("refused: Revit's own words reported",
      u"Can't cut instance out of element" in rec.text(), rec.text())
check("refused: summary counts it",
      any(u"1 opening(s) cut. 1 could not be" in a for a in rec.alerts),
      rec.alerts)

# 11. An opening made in the wrong place is caught by the check after.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
doc.misplace = True
rec = run_script(doc, [floor], settings())
check("misplaced: flagged, not reported as made",
      u"does not cut where the pipe is" in rec.text() and
      u"| made |" not in rec.text(), rec.text())
check("misplaced: not counted as cut",
      any(u"0 opening(s) cut" in a for a in rec.alerts), rec.alerts)

# 12. Cancel at the window, and no at the question: nothing written.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
rec = run_script(doc, [floor], None)
check("cancel window: nothing", not floor.solid.holes and
      not doc.transactions and rec.saved == 0)
rec = run_script(doc, [floor], settings(), confirm=False)
check("answer no: nothing", not floor.solid.holes and
      not doc.transactions and not doc.groups)

# 13. Nothing usable selected.
doc = Document(hosts=[], pipes=[])
rec = run_script(doc, [], settings())
check("no selection: asks for one",
      any(u"Select the walls, floors or roofs" in a for a in rec.alerts))
curtain = Wall(0.0, 10.0, 100 * MM, curtain=True)
curved = Wall(0.0, 10.0, 100 * MM, curved=True)
doc = Document(hosts=[curtain, curved])
rec = run_script(doc, [curtain, curved], settings())
check("curtain and curved: both refused with reasons",
      any(u"curtain wall" in a and u"curved wall" in a
          for a in rec.alerts), rec.alerts)
check("curtain and curved: window never opened", not rec.asked)

# 14. Conduits are found too, by their own outer diameter.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
conduit = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 25, conduit=True)
doc = Document(hosts=[floor], pipes=[conduit])
rec = run_script(doc, [floor], settings(mode=model.PERCENT, amount=100,
                                        round_to=5))
hole = last_hole(floor)
check("conduit: 25 mm, 100% larger -> 50",
      hole and close(hole[3], 25 * MM), hole)
check("conduit: named as a conduit", u"Conduit" in rec.text())

# 15. A roof is cut vertically like a floor.
roof = RoofBase((0.0, 0.0, 30.0), (20.0, 20.0, 30.0 + 300 * MM))
vent = Pipe((3.0, 3.0, 28.0), (3.0, 3.0, 33.0), 110)
doc = Document(hosts=[roof], pipes=[vent])
rec = run_script(doc, [roof], settings())
check("roof: cut", len(roof.solid.holes) == 1 and
      crossing_count(roof, vent) == 0)

# 16. Revit rolls the group back on assimilate: nothing claimed.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
doc.assimilate_status = TransactionStatus.RolledBack
rec = run_script(doc, [floor], settings())
check("group rolled back: not reported as made",
      u"| made |" not in rec.text() and
      any(u"0 opening(s) cut" in a for a in rec.alerts), rec.alerts)

# 17. A compound wall in two solids is one crossing, sized for the whole
#     thickness, not two half openings.
wall = LayeredWall(0.0, 30.0, 300 * MM)
slanted = Pipe((15.0 - 3 * math.tan(math.radians(30)), -3.0, 4.0),
               (15.0 + 3 * math.tan(math.radians(30)), 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[slanted])
rec = run_script(doc, [wall], settings(round_to=0))
check("layered wall: one opening, not one per layer",
      len(wall.solid.holes) == 1, wall.solid.holes)
t = math.tan(math.radians(30))
expected_half = (300 * MM * t + 2 * 55 * MM * math.sqrt(1 + t * t)
                 + 2 * 25 * MM) / 2
check("layered wall: sized for the full thickness",
      wall.solid.holes and close(wall.solid.holes[0][3], expected_half),
      wall.solid.holes)

# 18. A pipe leaning with a 30 degree roof. 2.47.0 sized this as if the
#     faces were flat, 242 mm a side where the pipe needs 288 (sampled
#     over the pipe's surface in test_openings_model.py and in a probe).
def leaning_pipe(tilt, length=3.0):
    t = math.radians(tilt)
    d = (math.sin(t), 0.0, math.cos(t))
    return Pipe(model.scale(d, -length), model.scale(d, length), 160)


roof = SlopedRoof(30, 300 * MM)
vent = leaning_pipe(30)
doc = Document(hosts=[roof], pipes=[vent])
rec = run_script(doc, [roof], settings(amount=0, round_to=0))
hole = last_hole(roof)
check("sloped roof: cut", hole is not None and
      crossing_count(roof, vent) == 0, getattr(rec, "raised", hole))
check("sloped roof: wide enough for the leaning pipe (needs 288.3 a side)",
      hole and 288.3 * MM <= hole[3] <= 290 * MM,
      hole and hole[3] / MM)
check("sloped roof: no wider across the slope than the pipe",
      hole and close(hole[4], 80 * MM), hole and hole[4] / MM)
check("sloped roof: the off square angle is to the roof, 60 degrees",
      u"| 60\u00b0 |" in rec.text(), rec.text())
check("sloped roof: no flat warning when the faces were read",
      u"sized as if flat" not in rec.text())

# ...and a vertical vent through the same roof stays pipe sized.
roof = SlopedRoof(30, 300 * MM)
vent = leaning_pipe(0)
doc = Document(hosts=[roof], pipes=[vent])
rec = run_script(doc, [roof], settings(amount=0, round_to=0))
hole = last_hole(roof)
check("sloped roof, vertical vent: 160 x 160",
      hole and close(hole[3], 80 * MM) and close(hole[4], 80 * MM), hole)

# 19. Faces Revit will not give up: still cut, sized flat, and said so.
roof = SlopedRoof(30, 300 * MM)
roof.solid.faces_unreadable = True
vent = leaning_pipe(30)
doc = Document(hosts=[roof], pipes=[vent])
rec = run_script(doc, [roof], settings(amount=0, round_to=0))
check("faces unreadable: still cut", len(roof.solid.holes) == 1,
      getattr(rec, "raised", ""))
check("faces unreadable: the row says it was sized as flat",
      u"sized as if flat" in rec.text(), rec.text())

# 20. The table is printed whole: header and rows in one print_md, or
#     pyRevit renders each row as a table of its own and nothing lines
#     up (seen in Revit at 2.47.0).
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipes = [Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110),
         Pipe((9.0, 6.0, -3.0), (9.0, 6.0, 3.0), 50)]
doc = Document(hosts=[floor], pipes=pipes)
rec = run_script(doc, [floor], settings())
tables = [line for line in rec.lines if line.startswith(u"| Host |")]
check("table: printed twice, before and after",
      len(tables) == 2, len(tables))
check("table: header, rule and both rows in one call",
      tables and all(table.count(u"\n") == 3 for table in tables),
      tables)
check("table: no row printed on its own",
      not [line for line in rec.lines
           if line.startswith(u"| ") and not line.startswith(u"| Host |")])
check("table: every row has the header's six columns",
      tables and all(row.count(u"|") == 7
                     for table in tables for row in table.split(u"\n")),
      tables)
check("table: every column left aligned, as the output window only "
      "right-aligns a header",
      tables and all((table.split(u"\n") + [u""])[1] ==
                     u"| :--- | :--- | :--- | :--- | :--- | :--- |"
                     for table in tables), tables)
check("table: before the run each row says what will happen",
      tables and tables[0].count(u"will be cut") == 2, tables)

# 21. Revit wants a roof's opening sketch on one particular plane. Each
#     likely height is tried in its own transaction and the first it
#     keeps is used; the hole is the same whichever, being vertical.
def roof_on_level(wanted, refusal="raise", offset=1.0, elevation=-1.0):
    roof = SlopedRoof(30, 300 * MM)
    level = Level(elevation)
    roof.LevelId = level.Id
    roof.params["roof_offset"] = Parameter(offset)
    roof.sketch_refusal = refusal
    roof.sketch_z = {"level and offset": elevation + offset,
                     "level": elevation,
                     "nowhere": 99.0}.get(wanted, wanted)
    vent = leaning_pipe(0)
    doc = Document(hosts=[roof], pipes=[vent])
    doc.levels.append(level)
    return roof, vent, doc


roof, vent, doc = roof_on_level("level and offset")
rec = run_script(doc, [roof], settings())
check("sketch plane: level and offset tried first, and kept",
      doc.sketch_heights == [0.0] and len(roof.solid.holes) == 1,
      doc.sketch_heights)
check("sketch plane: reported plainly when the first try works",
      u"| made |" in rec.text(), rec.text())

roof, vent, doc = roof_on_level("level")
rec = run_script(doc, [roof], settings())
check("sketch plane: refused at level and offset, kept at the level",
      doc.sketch_heights == [0.0, -1.0] and len(roof.solid.holes) == 1,
      doc.sketch_heights)
check("sketch plane: the pipe is clear", crossing_count(roof, vent) == 0)
check("sketch plane: the report says where it was sketched",
      u"made, sketched at its level" in rec.text(), rec.text())
check("sketch plane: nothing left over from the refused try",
      not doc.pending and not doc.pending_errors)

roof, vent, doc = roof_on_level("level", refusal="failure")
rec = run_script(doc, [roof], settings())
check("sketch plane: a refusal at commit is also tried again",
      len(roof.solid.holes) == 1 and
      u"made, sketched at its level" in rec.text(), rec.text())

# Offset 0.5, so level and offset (-0.5) is not the project's zero.
# No shaft either, so nothing at all is accepted.
roof, vent, doc = roof_on_level("nowhere", offset=0.5)
doc.no_shafts = True
rec = run_script(doc, [roof], settings())
check("sketch plane: nowhere accepted, every height tried once",
      len(doc.sketch_heights) == 4 and not roof.solid.holes,
      doc.sketch_heights)
check("sketch plane: Revit's words reported once, not four times",
      rec.text().count(u"Can't change plane") == 1, rec.text())
check("sketch plane: the report names every height tried",
      u"(sketch tried at its level and offset, its level, the middle of "
      u"the host, the project's zero)" in rec.text(), rec.text())
check("sketch plane: counted as not cut",
      any(u"0 opening(s) cut" in a for a in rec.alerts), rec.alerts)
check("sketch plane: the shaft's refusal is reported too",
      u"shaft: Revit would not make a shaft here" in rec.text(),
      rec.text())

# A roof on a level at the project's zero, no offset: its level and the
# project's zero are one height, and it is tried once.
roof, vent, doc = roof_on_level("nowhere", offset=0.0, elevation=0.0)
rec = run_script(doc, [roof], settings())
check("sketch plane: the same height is not tried twice",
      len(doc.sketch_heights) == len(set(doc.sketch_heights)) == 2,
      doc.sketch_heights)

# 22. A roof that refuses a vertical cut at every height, as one did in
#     Revit 2025, gets a shaft kept to the roof's own thickness. The
#     levels are far apart and a floor sits between them under the
#     pipe, so a shaft run from level to level would cut it.
def shaft_case(slope=37, shape=model.RECTANGULAR, refuse=False):
    roof = SlopedRoof(slope, 300 * MM)
    low_level, high_level = Level(-15.0), Level(10.0)
    lowest = Level(-20.0)
    roof.LevelId = low_level.Id
    roof.sketch_z = 99.0                     # every height refused
    floor = Floor((-5.0, -5.0, -6.0), (5.0, 5.0, -6.0 + 250 * MM))
    vent = Pipe((0.0, 0.0, -1.0), (0.0, 0.0, 3.0), 152.4)
    doc = Document(hosts=[roof, floor], pipes=[vent])
    # Deliberately unsorted, and in an order where taking the last
    # level below the band without sorting first gives the lowest.
    doc.levels.extend([low_level, high_level, lowest])
    doc.shaft_params_refuse = refuse
    rec = run_script(doc, [roof], settings(shape=shape))
    return roof, floor, vent, doc, rec


for shape in model.SHAPES:
    roof, floor, vent, doc, rec = shaft_case(shape=shape)
    label = u"shaft, {0}: ".format(shape)
    check(label + u"made, and says it is a shaft",
          len(doc.shafts) == 1 and
          u"made as a shaft opening through the roof only" in rec.text(),
          rec.text())
    check(label + u"the roof is cut and the pipe is clear",
          len(roof.solid.holes) == 1 and crossing_count(roof, vent) == 0,
          roof.solid.holes)
    check(label + u"the floor below is not cut", not floor.solid.holes,
          floor.solid.holes)
    if doc.shafts:
        shaft = doc.shafts[0]
        low, high = shaft.band()
        check(label + u"hung from the level below and the level above",
              shaft.base.Elevation == -15.0 and shaft.top.Elevation == 10.0,
              (shaft.base.Elevation, shaft.top.Elevation))
        # 300 mm at 37 degrees is 376 mm deep vertically, and over the
        # 210 mm opening the faces rise 105 tan 37 either side of its
        # centre; a 10 mm margin top and bottom.
        slope = math.radians(37)
        expected = (300 / math.cos(slope) + 2 * 105 * math.tan(slope) +
                    20) * MM
        check(label + u"its band is the roof's thickness, not a storey",
              abs((high - low) - expected) < 1 * MM,
              ((high - low) / MM, expected / MM))
        check(label + u"the sketch is drawn at the base level",
              all((c.center.Z if isinstance(c, Arc) else c.a.Z) == -15.0
                  for c in shaft.curves.items))
    check(label + u"counted as cut",
          any(u"1 opening(s) cut" in a for a in rec.alerts), rec.alerts)

# A shaft whose offset will not take its value is not kept: left at its
# levels it would cut everything from one to the other.
roof, floor, vent, doc, rec = shaft_case(refuse=True)
check("shaft offsets refused: nothing kept", not doc.shafts and
      not roof.solid.holes and not floor.solid.holes, doc.shafts)
check("shaft offsets refused: said so",
      u"shaft: the shaft would not take WALL_BASE_OFFSET" in rec.text(),
      rec.text())

# One that says yes and keeps something else is caught by reading back.
roof, floor, vent, doc, rec = shaft_case(refuse="ignore")
check("shaft offset not kept: nothing kept", not doc.shafts and
      not roof.solid.holes and not floor.solid.holes, doc.shafts)
check("shaft offset not kept: said so",
      u"did not keep its value" in rec.text(), rec.text())

# A wall never gets a shaft: its openings are not vertical.
wall = Wall(0.0, 30.0, 300 * MM)
pipe = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[pipe])
doc.refuse_hosts.add(wall)
doc.levels.append(Level(0.0))
rec = run_script(doc, [wall], settings())
check("wall refused: no shaft tried", not doc.shafts and
      u"shaft" not in rec.text(), rec.text())

# ==========================================================================
# Tagging, and Update Openings
# ==========================================================================

from avh_openings import storage, tags  # noqa: E402

UPDATE = os.path.join(HERE, "AVH.tab", "Model.panel",
                      "Update Openings.pushbutton", "script.py")


def first(elements):
    """The first element, or None. A scenario that indexes an empty list
    crashes the run and hides every check after it; two mutations did
    exactly that before this."""
    return elements[0] if elements else None


def hole_at(host, index):
    """A coordinate of the host's first hole, or NaN when it has none,
    which fails close() rather than crashing the run."""
    holes = host.solid.holes
    return holes[0][index] if holes else float("nan")


def tag_of(element):
    text = storage.read(make_db(), element)
    return tags.decode(text) if text else None


def move_pipe(pipe, dx=0.0, dy=0.0, dz=0.0):
    """Move a fake pipe, as if someone dragged it in Revit."""
    pipe.a = model.add(pipe.a, (dx, dy, dz))
    pipe.b = model.add(pipe.b, (dx, dy, dz))
    pipe.Location = Location(curve=Line(XYZ(*pipe.a), XYZ(*pipe.b)))


def floor_with_opening(pipe_mm=110, at=(5.0, 6.0)):
    floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
    pipe = Pipe((at[0], at[1], -3.0), (at[0], at[1], 3.0), pipe_mm)
    doc = Document(hosts=[floor], pipes=[pipe])
    run_script(doc, [floor], settings())
    return floor, pipe, doc


def run_update(doc, selected=(), confirm=None):
    return run_script(doc, list(selected), None,
                      confirm=list(confirm or []), script=UPDATE)


def snapshot(doc):
    return ([list(h.solid.holes) for h in doc.hosts], list(doc.openings),
            list(doc.instances))


# U1. Pipe Openings tags what it makes.
floor, pipe, doc = floor_with_opening()
opening = doc.openings[0] if doc.openings else None
tag = tag_of(opening) if opening else None
check("tag: the new opening carries a tag", tag is not None)
check("tag: it names the pipe and the host",
      tag and tag.pipe == pipe.UniqueId and tag.host == floor.UniqueId and
      tag.link == u"" and tag.pipe_kind == u"Pipe",
      tag and tag.__dict__)
check("tag: it records how it was made and how big",
      tag and tag.kind == tags.OPENING and tag.shape == model.RECTANGULAR
      and close(tag.width, 160 * MM) and close(tag.cx, 5.0) and
      close(tag.amount, 25.0), tag and tag.__dict__)

# U2. Nothing has changed: nothing is asked and nothing is touched, and
#     the delete each check makes is rolled back.
before = snapshot(doc)
rec = run_update(doc)
check("unchanged: no exception", not hasattr(rec, "raised"),
      getattr(rec, "raised", ""))
check("unchanged: reported as unchanged",
      u"unchanged, left alone" in rec.text(), rec.text())
check("unchanged: nothing asked, nothing to do",
      not rec.questions and any(u"Nothing to do" in a for a in rec.alerts),
      rec.alerts)
check("unchanged: the model is exactly as it was",
      snapshot(doc) == before and not doc.deleted,
      (snapshot(doc), before))

# U3. The pipe moved 300 mm: the old opening goes, a new one is cut and
#     tagged where the pipe is now, and the pipe is clear.
old = first(doc.openings)
move_pipe(pipe, dx=300 * MM)
rec = run_update(doc, confirm=[True])
check("moved: reported as moved 300 mm",
      u"re-cut: moved 300 mm" in rec.text(), rec.text())
check("moved: the old opening is deleted", old in doc.deleted)
check("moved: one opening in the floor, at the pipe",
      len(floor.solid.holes) == 1 and
      close(floor.solid.holes[0][1], 5.0 + 300 * MM), floor.solid.holes)
check("moved: the pipe is clear", crossing_count(floor, pipe) == 0)
new = doc.openings[-1] if doc.openings else None
check("moved: the new opening is tagged where the pipe is now",
      new is not None and new is not old and tag_of(new) and
      close(tag_of(new).cx, 5.0 + 300 * MM))
check("moved: the result says updated", u"| updated |" in rec.text(),
      rec.text())

# ...and running it again finds it unchanged.
rec = run_update(doc)
check("moved, then again: unchanged",
      u"unchanged, left alone" in rec.text() and not rec.questions,
      rec.text())

# U4. The pipe got bigger: re-cut bigger.
floor, pipe, doc = floor_with_opening()
pipe.params["pipe_od"] = Parameter(160 * MM)
rec = run_update(doc, confirm=[True])
check("resized: says the pipe size changed",
      u"pipe 160 mm, was 110" in rec.text(), rec.text())
check("resized: re-cut at 210",
      len(floor.solid.holes) == 1 and close(floor.solid.holes[0][3],
                                            105 * MM), floor.solid.holes)

# U5. The pipe was deleted: orphaned, and kept unless you say delete.
floor, pipe, doc = floor_with_opening()
doc.pipes.remove(pipe)
rec = run_update(doc, confirm=[False])
check("pipe deleted: orphaned, and said why",
      u"orphaned: its pipe has been deleted" in rec.text(), rec.text())
check("pipe deleted: asked about the orphans only",
      len(rec.questions) == 1 and u"Delete them?" in rec.questions[0],
      rec.questions)
check("pipe deleted, answer no: the opening stays",
      len(floor.solid.holes) == 1 and not doc.deleted)
rec = run_update(doc, confirm=[True])
check("pipe deleted, answer yes: the opening is deleted",
      not floor.solid.holes and len(doc.deleted) == 1, floor.solid.holes)
check("pipe deleted, answer yes: reported", u"| deleted |" in rec.text(),
      rec.text())

# U6. The pipe moved right off the floor: orphaned.
floor, pipe, doc = floor_with_opening()
move_pipe(pipe, dx=50.0)
rec = run_update(doc, confirm=[False])
check("moved away: no longer passes through the floor",
      u"no longer passes through the floor" in rec.text(), rec.text())

# U7. A pipe in a link: not loaded means cannot check; loaded and moved
#     means re-cut where the link now puts it.
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
linked = Pipe((1.0, 1.0, -3.0), (1.0, 1.0, 3.0), 110)
link_doc = Document(pipes=[linked], title=u"MEP_Lagnir")
instance = RevitLinkInstance(link_doc, (4.0, 5.0, 0.0))
doc = Document(hosts=[floor], links=[instance])
run_script(doc, [floor], settings())
tag = tag_of(first(doc.openings)) if doc.openings else None
check("link: the tag names the link instance",
      tag and tag.link == instance.UniqueId and tag.pipe == linked.UniqueId,
      tag and tag.__dict__)
instance.link_doc = None
rec = run_update(doc)
check("link unloaded: cannot check, left alone",
      u"left alone: its pipe's link is not loaded" in rec.text() and
      len(floor.solid.holes) == 1, rec.text())
instance.link_doc = link_doc
move_pipe(linked, dy=200 * MM)
rec = run_update(doc, confirm=[True])
check("link moved: re-cut where the link puts the pipe now",
      len(floor.solid.holes) == 1 and close(floor.solid.holes[0][2],
                                            6.0 + 200 * MM),
      floor.solid.holes)

# U8. An opening someone else has checked out is left alone.
floor, pipe, doc = floor_with_opening()
if doc.openings:
    doc.openings[0].locked = True
move_pipe(pipe, dx=300 * MM)
rec = run_update(doc)
check("checked out: cannot be changed, said so",
      u"it cannot be changed: it is checked out by another user"
      in rec.text(), rec.text())
check("checked out: untouched", close(hole_at(floor, 1), 5.0) and
      not doc.deleted and not rec.questions)

# U9. Revit refuses the new opening: the old one stays where it was.
floor, pipe, doc = floor_with_opening()
old = first(doc.openings)
move_pipe(pipe, dx=300 * MM)
doc.refuse_hosts.add(floor)
doc.no_shafts = True
rec = run_update(doc, confirm=[True])
check("re-cut refused: the old opening is still there",
      old in doc.openings and old not in doc.deleted and
      len(floor.solid.holes) == 1 and close(floor.solid.holes[0][1], 5.0),
      floor.solid.holes)
check("re-cut refused: said so", u"left as it was" in rec.text(),
      rec.text())

# U10. A selection limits the check to those hosts.
floor_a = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
floor_b = Floor((0.0, 0.0, 20.0), (20.0, 20.0, 20.0 + 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 23.0), 110)
doc = Document(hosts=[floor_a, floor_b], pipes=[pipe])
run_script(doc, [floor_a, floor_b], settings())
move_pipe(pipe, dx=300 * MM)
rec = run_update(doc, selected=[floor_b], confirm=[True])
check("selection: only the selected floor's opening is re-cut",
      close(hole_at(floor_a, 1), 5.0) and
      close(hole_at(floor_b, 1), 5.0 + 300 * MM),
      (floor_a.solid.holes, floor_b.solid.holes))
check("selection: the summary says so",
      any(u"1 tagged opening(s) checked in the selection" in q
          for q in rec.questions), rec.questions)

# U11. Openings with no tag are counted, not touched.
floor, pipe, doc = floor_with_opening()
by_hand = Opening()
doc.openings.append(by_hand)
rec = run_update(doc)
check("untagged: counted in the report",
      u"1 opening(s) in the model carry no Pipe Openings tag" in rec.text(),
      rec.text())

# ...and a model with none tagged says there is nothing to update.
doc = Document(hosts=[], pipes=[])
doc.openings.append(Opening())
rec = run_update(doc)
check("none tagged: nothing to update",
      any(u"nothing to update" in a for a in rec.alerts), rec.alerts)

# A tag this version cannot read is reported and left alone.
floor, pipe, doc = floor_with_opening()
if doc.openings:
    storage.write(make_db(), doc.openings[0], u"version=9")
rec = run_update(doc)
check("unreadable tag: reported, left alone",
      u"tag version 9 is not one this version" in rec.text() and
      not doc.deleted, rec.text())

# U12. A shaft through a sloped roof moves with its pipe: the roof still
#      refuses a vertical cut at every height, the delete is redone and
#      undone with each refused try, and a new shaft is kept.
roof, floor, vent, doc, rec = shaft_case()
old = doc.shafts[0] if doc.shafts else None
check("shaft tag: kind shaft", old is not None and tag_of(old) and
      tag_of(old).kind == tags.SHAFT)
move_pipe(vent, dx=400 * MM)
rec = run_update(doc, confirm=[True])
check("shaft moved: re-cut as a shaft",
      u"updated, made as a shaft opening" in rec.text(), rec.text())
check("shaft moved: the old shaft deleted, one new one",
      old in doc.deleted and len([s for s in doc.openings
                                  if isinstance(s, Shaft)]) == 1,
      doc.openings)
check("shaft moved: one hole in the roof, the pipe clear, the floor whole",
      len(roof.solid.holes) == 1 and crossing_count(roof, vent) == 0 and
      not floor.solid.holes, (roof.solid.holes, floor.solid.holes))

# U13. A round wall opening follows its pipe along the wall.
wall = Wall(0.0, 30.0, 300 * MM)
pipe = Pipe((5.0, -3.0, 4.0), (5.0, 3.0, 4.0), 110)
doc = Document(hosts=[wall], pipes=[pipe],
               symbols=[FamilySymbol(u"AVH_Round_Opening")])
run_script(doc, [wall], settings(shape=model.CIRCULAR))
old = doc.instances[0] if doc.instances else None
check("round tag: kind round", old is not None and tag_of(old) and
      tag_of(old).kind == tags.ROUND)
move_pipe(pipe, dx=1.0)
rec = run_update(doc, confirm=[True])
check("round moved: one instance, at the pipe",
      len(doc.instances) == 1 and doc.instances[0] is not old and
      close(doc.instances[0]._point[0], 6.0), doc.instances)
check("round moved: the pipe is clear", crossing_count(wall, pipe) == 0)

# U14. Saying no to the re-cut changes nothing.
floor, pipe, doc = floor_with_opening()
move_pipe(pipe, dx=300 * MM)
before = snapshot(doc)
rec = run_update(doc, confirm=[False])
check("declined: nothing changed", snapshot(doc) == before and
      not doc.deleted)

# U15. A tag that will not go on is noted on the row, the opening kept.
ExtensibleStorage.broken = True
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
rec = run_script(doc, [floor], settings())
ExtensibleStorage.broken = False
check("storage broken: the opening is still made",
      len(floor.solid.holes) == 1)
check("storage broken: the report says it is not tagged",
      u"not tagged, so Update Openings will not see it" in rec.text(),
      rec.text())

# U18. Revit throws the whole update run away: nothing claimed.
floor, pipe, doc = floor_with_opening()
move_pipe(pipe, dx=300 * MM)
doc.assimilate_status = TransactionStatus.RolledBack
rec = run_update(doc, confirm=[True])
check("update rolled back: not reported as updated",
      u"| updated |" not in rec.text() and
      u"not kept: Revit rolled the run back" in rec.text(), rec.text())
check("update rolled back: counted as nothing re-cut",
      any(u"0 opening(s) re-cut" in a for a in rec.alerts), rec.alerts)

# U16. Storage that quietly keeps nothing is caught by reading back.
ExtensibleStorage.lossy = True
floor = Floor((0.0, 0.0, 0.0), (20.0, 20.0, 250 * MM))
pipe = Pipe((5.0, 6.0, -3.0), (5.0, 6.0, 3.0), 110)
doc = Document(hosts=[floor], pipes=[pipe])
rec = run_script(doc, [floor], settings())
ExtensibleStorage.lossy = False
check("storage lossy: the report says it is not tagged",
      u"the tag did not read back as written" in rec.text(), rec.text())

# U17. A re-cut opening that lands off the pipe is caught by the check
#      after, not reported as updated.
floor, pipe, doc = floor_with_opening()
move_pipe(pipe, dx=300 * MM)
doc.misplace = True
rec = run_update(doc, confirm=[True])
check("re-cut misplaced: flagged, not reported as updated",
      u"does not clear the pipe" in rec.text() and
      u"| updated |" not in rec.text(), rec.text())
check("re-cut misplaced: not counted",
      any(u"0 opening(s) re-cut" in a for a in rec.alerts), rec.alerts)

# --------------------------------------------------------------------------
# The checks can fail
# --------------------------------------------------------------------------

check("self-check: a crossing is seen before cutting",
      crossing_count(Floor((0, 0, 0), (1, 1, 1)),
                     Pipe((0.5, 0.5, -1), (0.5, 0.5, 2), 10)) == 1)
check("self-check: check stores a real boolean",
      all(isinstance(passed, bool) for _, passed, _ in results))

failed = [r for r in results if not r[1]]
for name, passed, detail in results:
    print(u"{0}  {1}{2}".format("PASS" if passed else "FAIL", name,
                                u"  ({0})".format(detail)
                                if not passed and detail != "" else u""))
print(u"\n{0} checks, {1} failed".format(len(results), len(failed)))
sys.exit(1 if failed else 0)
