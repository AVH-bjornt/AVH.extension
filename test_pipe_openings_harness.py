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


class Solid(object):
    """A slab: a box in a frame (u, v, n), with holes cut along n.

    `holes` are ("rect", cu, cv, half_u, half_v) or ("circle", cu, cv, r)
    in the slab's own (u, v). For a floor the frame is plan; for a wall it
    is the wall's own, so a wall's rectangle hole is in wall coordinates.
    """

    def __init__(self, frame, low, high):
        self.frame = frame
        self.low = low
        self.high = high
        self.holes = []
        self.Volume = 1.0

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
        for hole in self.holes:
            inside = self._inside(hole, a, d)
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
        self.Id = ElementId(Element._next[0])

    def get_BoundingBox(self, view):
        return None


class Host(Element):
    def __init__(self, frame, low, high):
        Element.__init__(self)
        self.solid = Solid(frame, low, high)

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
        """NewOpening(wall, XYZ, XYZ) or NewOpening(host, CurveArray,
        bool), the two overloads the script uses."""
        doc = self.doc
        perpendicular = b if isinstance(a, CurveArray) else None
        doc.calls.append(("NewOpening", host, a, b, perpendicular))
        if host in doc.refuse_hosts:
            doc.pending_errors.append(u"Can't cut instance out of element")
        f = host.solid.frame
        if isinstance(a, CurveArray):
            if perpendicular is not False:
                raise Exception("expected a vertical cut")
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
        opening = Element()
        doc.pending.append((host, hole, opening))
        return opening

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
        self.misplace = False
        self.place_off_by = None
        self.transactions = []
        self.groups = []
        self.loaded_from = []

    def GetElement(self, element_id):
        for element in self.hosts + self.pipes + self.instances:
            if element.Id == element_id:
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
        for host, hole, _ in self.pending:
            host.solid.holes.append(hole)
        for instance in self.pending_instances:
            instance.refresh()
            instance.wall.solid.holes.append(instance.cut())
        self.pending = []
        self.pending_instances = []
        return True

    def rollback(self):
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

    def ask(self, settings, hosts_line):
        self.asked.append((settings, hosts_line))
        return self.settings

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes"):
            return self.confirm
        return True

    def print_md(self, text):
        self.lines.append(text)

    def text(self):
        return u"\n".join(self.lines)


def run_script(doc, selected, settings, confirm=True, recorder=None):
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
        runpy.run_path(SCRIPT, run_name="__main__")
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
