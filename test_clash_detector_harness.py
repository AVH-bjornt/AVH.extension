# -*- coding: utf-8 -*-
"""
Runs the real Clash Detector script and service against a mocked Revit.

The fake models the rules rather than replaying answers: every element
is a box, a solid intersection is the box two boxes share, a linked
model sits somewhere else and has to be moved into place, a section box
can only be set inside a transaction, and Revit can roll a commit back.
So a clash the service reports is one the fake geometry really has, and
its volume can be checked by arithmetic.

What this cannot prove is anything about Revit or WebView2 themselves.
The window is replaced by a stand in that plays the page's part.

Run outside Revit:

    python test_clash_detector_harness.py
"""

import base64
import os
import runpy
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Check.panel",
                      "Clash Detector.pushbutton", "script.py")

from avh_clash import model     # noqa: E402
from avh_clash import reader    # noqa: E402
from avh_clash import service   # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


M = 1.0 / model.FEET_TO_M           # one metre, in feet


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


class XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class Transform(object):
    """A move. Enough to put a linked model somewhere else."""

    def __init__(self, dx=0.0, dy=0.0, dz=0.0):
        self.d = (dx, dy, dz)
        self.IsIdentity = self.d == (0.0, 0.0, 0.0)

    def OfPoint(self, p):
        return XYZ(p.X + self.d[0], p.Y + self.d[1], p.Z + self.d[2])

    def move(self, box):
        d = self.d
        return (box[0] + d[0], box[1] + d[1], box[2] + d[2],
                box[3] + d[0], box[4] + d[1], box[5] + d[2])


class BoundingBoxXYZ(object):
    def __init__(self, box=None):
        self.Transform = None
        if box is not None:
            self.Min = XYZ(*box[:3])
            self.Max = XYZ(*box[3:])

    def tuple(self):
        return (self.Min.X, self.Min.Y, self.Min.Z,
                self.Max.X, self.Max.Y, self.Max.Z)


class Triangle(object):
    def __init__(self, corners):
        self.corners = corners

    def get_Index(self, n):
        return self.corners[n]


class Mesh(object):
    def __init__(self, points, triangles):
        self.Vertices = [XYZ(*p) for p in points]
        self.triangles = triangles
        self.NumTriangles = len(triangles)

    def get_Triangle(self, n):
        return Triangle(self.triangles[n])


TRIANGULATE_CALLS = []


class Face(object):
    def __init__(self, points, wants_no_argument=False):
        self.points = points
        self.wants_no_argument = wants_no_argument

    def Triangulate(self, *args):
        TRIANGULATE_CALLS.append(args)
        if args and self.wants_no_argument:
            raise Exception("this face takes no level of detail")
        return Mesh(self.points, [(0, 1, 2), (0, 2, 3)])


class FaceArray(list):
    @property
    def Size(self):
        return len(self)


def box_faces(box, wants_no_argument=False):
    x0, y0, z0, x1, y1, z1 = box
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    order = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return FaceArray(Face([c[i] for i in face], wants_no_argument)
                     for face in order)


class Solid(object):
    """An axis aligned box, in feet. `broken` makes Revit's boolean
    refuse it, as it does for some real geometry."""

    def __init__(self, box, broken=False, wants_no_argument=False):
        self.box = box
        self.broken = broken
        self.wants_no_argument = wants_no_argument

    @property
    def Volume(self):
        if self.box is None:
            return 0.0
        b = self.box
        return (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])

    @property
    def Faces(self):
        if self.box is None:
            return FaceArray()
        return box_faces(self.box, self.wants_no_argument)


class GeometryInstance(object):
    def __init__(self, children):
        self.children = children

    def GetInstanceGeometry(self):
        return list(self.children)


def box_as_mesh(box):
    """The same box as one raw Mesh: drawable, not a solid."""
    points = []
    triangles = []
    for face in box_faces(box):
        base = len(points)
        points.extend(face.points)
        triangles.append((base, base + 1, base + 2))
        triangles.append((base, base + 2, base + 3))
    return Mesh(points, triangles)


BOOLEANS = []


class BooleanOperationsUtils(object):
    @staticmethod
    def ExecuteBooleanOperation(a, b, kind):
        BOOLEANS.append((a, b, kind))
        if a.broken or b.broken:
            raise Exception("Failed to perform the Boolean operation")
        shared = model.intersection(a.box, b.box)
        if shared is None or min(model.extents(shared)) <= 0:
            return Solid(None)
        return Solid(shared)


class SolidUtils(object):
    refuse = False

    @staticmethod
    def CreateTransformed(solid, transform):
        if SolidUtils.refuse:
            raise Exception("the solid could not be transformed")
        return Solid(transform.move(solid.box), solid.broken,
                     solid.wants_no_argument)


class Category(object):
    def __init__(self, cat_id, name, kind="Model"):
        self.Id = ElementId(cat_id)
        self.Name = name
        self.CategoryType = kind


WALLS = Category(-2000011, u"Veggir")
FLOORS = Category(-2000032, u"Gólf")
PIPES = Category(-2008044, u"Pipes")
GENERIC = Category(-2000151, u"Generic Models")
ROOMS = Category(-2000160, u"Rooms")
TEXT = Category(-2000300, u"Text Notes", kind="Annotation")

NEXT_ID = [1000]


class Element(object):
    def __init__(self, category, box, name=u"", geometry="solid",
                 broken=False, view_specific=False,
                 wants_no_argument=False):
        NEXT_ID[0] += 1
        self.Id = ElementId(NEXT_ID[0])
        self.Category = category
        self.box = box
        self.Name = name or u"Type {0}".format(NEXT_ID[0])
        self.kind = geometry
        self.broken = broken
        self.ViewSpecific = view_specific
        self.wants_no_argument = wants_no_argument
        self.geometry_reads = 0

    def get_BoundingBox(self, view):
        return None if self.box is None else BoundingBoxXYZ(self.box)

    def get_Geometry(self, options):
        self.geometry_reads += 1
        if self.box is None:
            return None
        if self.kind == "mesh":
            return [box_as_mesh(self.box)]
        solid = Solid(self.box, self.broken, self.wants_no_argument)
        if self.kind == "instance":
            return [None, GeometryInstance([solid])]
        return [solid]


class Level(object):
    def __init__(self, name, elevation):
        self.Name = name
        self.Elevation = elevation
        self.Id = ElementId(hash(name) % 100000)
        self.Category = None
        self.ViewSpecific = False


class RevitLinkInstance(object):
    def __init__(self, link_doc, transform):
        NEXT_ID[0] += 1
        self.Id = ElementId(NEXT_ID[0])
        self.link_doc = link_doc
        self.transform = transform
        self.Category = None
        self.ViewSpecific = False

    def GetLinkDocument(self):
        return self.link_doc

    def GetTotalTransform(self):
        return self.transform


class View(object):
    def __init__(self, name, shows=None):
        NEXT_ID[0] += 1
        self.Id = ElementId(NEXT_ID[0])
        self.Name = name
        self.shows = shows          # None: everything
        self.IsTemplate = False


class View3D(View):
    def __init__(self, name, shows=None, section=None, refuses=False):
        View.__init__(self, name, shows)
        self.section = section
        self.refuses = refuses
        self.doc = None

    @property
    def IsSectionBoxActive(self):
        return self.section is not None

    def GetSectionBox(self):
        return BoundingBoxXYZ(self.section)

    def SetSectionBox(self, box):
        if self.refuses:
            raise Exception("the view is controlled by a template")
        if not self.doc.open_transaction:
            raise Exception("Attempt to modify the model outside of "
                            "transaction")
        self.doc.pending.append((self, box.tuple()))


class ViewPlan(View):
    pass


class ViewSheet(View):
    pass


class Document(object):
    def __init__(self, title, elements, view=None, levels=()):
        self.Title = title
        self.elements = list(elements)
        self.levels = list(levels)
        self.ActiveView = view
        self.IsValidObject = True
        self.IsFamilyDocument = False
        self.open_transaction = False
        self.pending = []
        self.commit_rolls_back = False
        if isinstance(view, View3D):
            view.doc = self

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def Equals(self, other):
        return other is self


class FilteredElementCollector(object):
    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.items = list(doc.elements) + list(doc.levels)
        if view_id is not None:
            view = doc.ActiveView
            if isinstance(view, ViewSheet):
                raise Exception("viewId is not valid for element iteration")
            if view.shows is not None:
                self.items = [e for e in self.items if e in view.shows]

    def WhereElementIsNotElementType(self):
        return self

    def OfClass(self, kind):
        self.items = [e for e in self.items if isinstance(e, kind)]
        return self

    def __iter__(self):
        return iter(self.items)


TRANSACTIONS = []


class Transaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        TRANSACTIONS.append(self)
        self.outcome = None

    def Start(self):
        self.doc.open_transaction = True
        self.doc.pending = []

    def Commit(self):
        self.doc.open_transaction = False
        if self.doc.commit_rolls_back:
            self.doc.pending = []
            self.outcome = "RolledBack"
            return "RolledBack"
        for view, box in self.doc.pending:
            view.section = box
        self.doc.pending = []
        self.outcome = "Committed"
        return "Committed"

    def RollBack(self):
        self.doc.open_transaction = False
        self.doc.pending = []
        self.outcome = "RolledBack"


class Reference(object):
    def __init__(self, element):
        self.element = element
        self.instance = None

    def CreateLinkReference(self, instance):
        linked = Reference(self.element)
        linked.instance = instance
        return linked


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


DB = Namespace(
    ElementId=ElementId, XYZ=XYZ, BoundingBoxXYZ=BoundingBoxXYZ,
    Solid=Solid, Mesh=Mesh, GeometryInstance=GeometryInstance,
    Options=lambda: Namespace(), ViewDetailLevel=Namespace(Fine="Fine"),
    BooleanOperationsUtils=BooleanOperationsUtils,
    BooleanOperationsType=Namespace(Intersect="Intersect"),
    SolidUtils=SolidUtils, CategoryType=Namespace(Model="Model"),
    BuiltInCategory=Namespace(OST_Rooms=-2000160, OST_Walls=-2000011,
                              OST_Floors=-2000032, OST_PipeCurves=-2008044),
    FilteredElementCollector=FilteredElementCollector,
    RevitLinkInstance=RevitLinkInstance, Level=Level, View3D=View3D,
    Transaction=Transaction,
    TransactionStatus=Namespace(Committed="Committed"),
    Reference=Reference,
)


class Selection(object):
    def __init__(self, ids=(), references=True):
        self.ids = list(ids)
        self.set_ids = None
        self.set_references = None
        if references:
            self.SetReferences = self._set_references

    def GetElementIds(self):
        return list(self.ids)

    def SetElementIds(self, ids):
        self.set_ids = list(ids)

    def _set_references(self, references):
        self.set_references = list(references)


class UIView(object):
    def __init__(self, view_id):
        self.ViewId = view_id
        self.zoomed = None

    def ZoomAndCenterRectangle(self, low, high):
        self.zoomed = (low.X, low.Y, low.Z, high.X, high.Y, high.Z)


class World(object):
    """A host model, optionally a linked model, and the Revit UI."""

    def __init__(self, host, selected=(), references=True):
        self.doc = host
        self.selection = Selection([e.Id for e in selected], references)
        self.ui_view = UIView(host.ActiveView.Id) \
            if host.ActiveView is not None else None
        self.uidoc = Namespace(
            Document=host, Selection=self.selection,
            ActiveView=host.ActiveView,
            GetOpenUIViews=lambda: [self.ui_view] if self.ui_view else [])
        self.uiapp = Namespace(ActiveUIDocument=self.uidoc,
                               MainWindowHandle=0)
        self.uidoc.Application = self.uiapp


class Page(object):
    """Plays the web page: collects what the service posts."""

    def __init__(self, clash_service, cancel_after=None):
        self.service = clash_service
        self.messages = []
        self.pumps = 0
        self.cancel_after = cancel_after
        clash_service.attach(self.messages.append, self.pump)

    def pump(self):
        self.pumps += 1
        if self.cancel_after is not None and self.pumps >= self.cancel_after:
            self.service.handle({u"type": u"cancel"})

    def send(self, message):
        del self.messages[:]
        if self.service.needs_api(message.get(u"type")):
            self.service.handle_api(message, self.service.uiapp)
        else:
            self.service.handle(message)
        return list(self.messages)

    def of(self, kind):
        return [m for m in self.messages if m["type"] == kind]

    def result(self):
        found = self.of("result")
        return found[0] if found else None


def new_service(world):
    made = service.Service(DB, world.uiapp, version=u"test")
    made.build_inventory()
    return made


def every_category(init, *kinds):
    return [{u"source": source["key"],
             u"cats": [cat["id"] for cat in source["cats"]]}
            for source in init["sources"] if source["kind"] in kinds]


def run_config(init, a=("host",), b=("link",), **more):
    config = {u"a": every_category(init, *a),
              u"b": every_category(init, *b),
              u"scope": u"model", u"detail": u"medium", u"context": True}
    config.update(more)
    return {u"type": u"run", u"config": config}


def decode_meshes(messages):
    """key -> (positions, indices) from the mesh messages."""
    found = {}
    for message in messages:
        if message["type"] != "mesh":
            continue
        raw_pos = base64.b64decode(message["pos"])
        raw_idx = base64.b64decode(message["idx"])
        pos = struct.unpack("<{0}f".format(len(raw_pos) // 4), raw_pos)
        idx = struct.unpack("<{0}I".format(len(raw_idx) // 4), raw_idx)
        for item in message["items"]:
            found[(item["kind"], item["key"])] = (
                pos[item["v"][0]:item["v"][0] + item["v"][1]],
                idx[item["i"][0]:item["i"][0] + item["i"][1]])
    return found


def building(link_at=(0.0, 0.0, 0.0), view=None, **options):
    """A wall and a floor in the host, pipes in a link. In host space:
    pipe 1 runs through the wall, pipe 2 is clear of everything, pipe 3
    lies on top of the floor, touching it."""
    wall = Element(WALLS, (0, 0, 0, 5 * M, 0.2 * M, 3 * M), u"Veggur 200")
    floor = Element(FLOORS, (-1 * M, -1 * M, -0.25 * M, 6 * M, 2 * M, 0),
                    u"Plata 250")
    room = Element(ROOMS, (0, 0, 0, 5 * M, 5 * M, 3 * M), u"Herbergi")
    note = Element(TEXT, (0, 0, 0, 1, 1, 0), u"Texti")
    detail = Element(GENERIC, (0, 0, 0, 1, 1, 1), u"Detail",
                     view_specific=True)
    back = Transform(-link_at[0], -link_at[1], -link_at[2])

    def placed(box):
        return back.move(box)

    pipe1 = Element(PIPES, placed((2 * M, -1 * M, 1 * M,
                                   2.1 * M, 1 * M, 1.1 * M)), u"DN100",
                    **options)
    pipe2 = Element(PIPES, placed((2 * M, 5 * M, 1 * M,
                                   2.1 * M, 7 * M, 1.1 * M)), u"DN100")
    pipe3 = Element(PIPES, placed((1 * M, 1 * M, 0,
                                   4 * M, 1.1 * M, 0.1 * M)), u"DN100")
    link_doc = Document(u"Lagnir.rvt", [pipe1, pipe2, pipe3])
    instance = RevitLinkInstance(link_doc, Transform(*link_at))
    if view is None:
        view = View3D(u"{3D}")
    host = Document(u"Hús.rvt", [wall, floor, room, note, detail,
                                      instance],
                    view=view,
                    levels=[Level(u"01 Hæð", 3.4 * M),
                            Level(u"00 Jarðhæð", 0.0)])
    return Namespace(host=host, link=link_doc, instance=instance, wall=wall,
                     floor=floor, room=room, pipe1=pipe1, pipe2=pipe2,
                     pipe3=pipe3)


def reset():
    del BOOLEANS[:]
    del TRANSACTIONS[:]
    del TRIANGULATE_CALLS[:]
    SolidUtils.refuse = False


# --------------------------------------------------------------------------
# 1. The click: the real script, with a stand in for the window
# --------------------------------------------------------------------------

class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.shown = []
        self.open_already = False

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def bring_forward(self):
        return self.open_already

    def show(self, clash_service, uiapp, modal=False, title=u""):
        self.shown.append(Namespace(service=clash_service, uiapp=uiapp,
                                    modal=modal, title=title))


def run_script(world, shift=False, open_already=False, no_document=False):
    recorder = Recorder()
    recorder.open_already = open_already

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(
        doc=None if no_document else world.doc,
        uidoc=None if no_document else world.uidoc)
    pyrevit.DB = DB
    pyrevit.forms = Namespace(alert=recorder.alert)
    pyrevit.script = Namespace(
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None))
    pyrevit.EXEC_PARAMS = Namespace(config_mode=shift)
    window = types.ModuleType("avh_clash.window")
    window.bring_forward = recorder.bring_forward
    window.show = recorder.show

    import avh_clash
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_clash.window"] = window
    avh_clash.window = window
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_clash.window", None)
        del avh_clash.window
    return recorder


reset()
site = building()
world = World(site.host)
recorder = run_script(world)

check("click: the window was shown once", len(recorder.shown) == 1)
check("click: an ordinary click opens it modeless",
      recorder.shown and recorder.shown[0].modal is False)
check("click: the title names the model",
      recorder.shown and u"Hús.rvt" in recorder.shown[0].title)
check("click: no dialog", recorder.alerts == [])
check("click: opening the window reads no geometry",
      site.wall.geometry_reads == 0 and BOOLEANS == [])
check("click: and writes nothing", TRANSACTIONS == [])

shown_service = recorder.shown[0].service
page = Page(shown_service)
init = page.send({u"type": u"ready"})[0]
check("ready: the page gets the inventory", init["type"] == "init")
check("init: the host and the link, host first",
      [s["kind"] for s in init["sources"]] == ["host", "link"],
      repr([s["key"] for s in init["sources"]]))
check("init: the link is keyed by its instance",
      init["sources"][1]["key"] == u"link:{0}".format(
          site.instance.Id.Value))
host_cats = dict((c["name"], c["count"]) for c in init["sources"][0]["cats"])
check("init: host categories counted, with their own names",
      host_cats == {u"Veggir": 1, u"Gólf": 1}, repr(host_cats))
check("init: rooms, annotation and view specific elements are left out",
      u"Rooms" not in host_cats and u"Text Notes" not in host_cats and
      u"Generic Models" not in host_cats)
check("init: the link's pipes are counted, under their discipline",
      init["sources"][1]["cats"] == [
          {"id": -2008044, "name": u"Pipes", "count": 3,
           "group": u"MEP - Plumbing"}], repr(init["sources"][1]["cats"]))
host_groups = dict((c["name"], c["group"]) for c in init["sources"][0]["cats"])
check("init: disciplines come from the category, not its name",
      host_groups == {u"Veggir": u"Architecture",
                      u"Gólf": u"Architecture"}, repr(host_groups))
check("init: the headings come in page order, Other last",
      init["groups"][0] == u"Architecture" and init["groups"][-1] == u"Other")
check("init: a 3D view is usable and known to be 3D",
      init["view"]["usable"] and init["view"]["is3d"] and
      not init["view"]["sectionBox"])
import avh_schedules  # noqa: E402
check("init: the extension's version is passed on",
      init["version"] == avh_schedules.__version__ and init["version"])

check("click: shift opens it modal",
      run_script(World(building().host), shift=True).shown[0].modal is True)
again = run_script(World(building().host), open_already=True)
check("click: a window already open is brought forward, not duplicated",
      again.shown == [])

# The persistent engine keeps imported modules, so 2.44.2's fix sat on
# disk while Revit ran the old window.py. A stale copy is played here by
# replacing Service in the loaded module; a new window must get the one
# on disk back.
real_service_class = service.Service


class StaleService(object):
    def __init__(self, *args, **kwargs):
        raise AssertionError("the cached module was used")


service.Service = StaleService
fresh_click = run_script(World(building().host))
check("click: a new window reloads the clash modules from disk",
      len(fresh_click.shown) == 1 and
      type(fresh_click.shown[0].service).__name__ == "Service" and
      service.Service is not StaleService)

service.Service = StaleService
open_click = run_script(World(building().host), open_already=True)
check("click: bringing an open window forward reloads nothing",
      open_click.shown == [] and service.Service is StaleService)
service.Service = real_service_class
nothing = run_script(World(building().host), no_document=True)
check("click: no document is said, and nothing opens",
      nothing.shown == [] and len(nothing.alerts) == 1)


# --------------------------------------------------------------------------
# 2. A run: host against a link that sits somewhere else
# --------------------------------------------------------------------------

reset()
site = building(link_at=(30 * M, -12 * M, 4 * M))
world = World(site.host)
page = Page(new_service(world))
init = page.send({u"type": u"ready"})[0]
messages = page.send(run_config(init))
result = page.result()

check("run: it ends with a result", result is not None and
      messages[-1]["type"] == "result")
check("run: progress was reported", len(page.of("progress")) >= 3)
check("run: no error", page.of("error") == [], repr(page.of("error")))
check("run: one clash, the pipe through the wall",
      result is not None and len(result["clashes"]) == 1,
      repr(result and result["clashes"]))

clash = result["clashes"][0]
wall_key = u"host:{0}".format(site.wall.Id.Value)
pipe_key = u"link:{0}:{1}".format(site.instance.Id.Value,
                                  site.pipe1.Id.Value)
check("run: it names the wall as A and the linked pipe as B",
      clash["a"] == wall_key and clash["b"] == pipe_key, repr(clash))
check("run: it is exact", clash["exact"] is True)
check("run: the volume is pipe section times wall thickness",
      close(clash["vol"], 0.1 * 0.1 * 0.2, 1e-9), repr(clash["vol"]))
check("run: the depth is the thinnest way through",
      close(clash["depth"], 0.1, 1e-5), repr(clash["depth"]))
check("run: a pipe lying on the floor is touching, not clashing",
      all(site.pipe3.Id.Value != int(c["b"].split(u":")[-1])
          for c in result["clashes"]))
check("run: the pipe on the floor was never even tested as solids",
      len(BOOLEANS) == 1, "{0} booleans".format(len(BOOLEANS)))
check("run: stats count both sets and the pairs",
      result["stats"]["a"] == 2 and result["stats"]["b"] == 3 and
      result["stats"]["pairs"] == 1 and result["stats"]["tested"] == 1,
      repr(result["stats"]))

meshes = decode_meshes(messages)
check("run: the overlap itself is sent as a mesh",
      ("c", 0) in meshes and len(meshes[("c", 0)][1]) == 36)
check("run: both clashing elements are sent",
      ("e", wall_key) in meshes and ("e", pipe_key) in meshes)
check("run: and the rest as surroundings",
      len([k for k in meshes if k[0] == "e"]) == 5, repr(sorted(meshes)))
sent_keys = sorted(e["key"] for e in result["elements"])
check("run: every mesh sent is described, and nothing else",
      sent_keys == sorted(k[1] for k in meshes if k[0] == "e"))

# The wall is 5 x 0.2 x 3 m wherever the origin is put.
wall_pos = meshes[("e", wall_key)][0]
wall_box = model.bounds(wall_pos)
check("run: positions are metres",
      close(wall_box[3] - wall_box[0], 5.0, 1e-4) and
      close(wall_box[4] - wall_box[1], 0.2, 1e-4) and
      close(wall_box[5] - wall_box[2], 3.0, 1e-4), repr(wall_box))
pipe_box = model.bounds(meshes[("e", pipe_key)][0])
check("run: the linked pipe is drawn where the link puts it",
      close(pipe_box[0] - wall_box[0], 2.0, 1e-4) and
      close(pipe_box[2] - wall_box[2], 1.0, 1e-4), repr(pipe_box))
check("run: the clash sits inside the wall",
      wall_box[0] < clash["c"][0] < wall_box[3] and
      wall_box[1] < clash["c"][1] < wall_box[4])
overlap_box = model.bounds(meshes[("c", 0)][0])
check("run: the clash box is the overlap mesh's box",
      all(close(p, q, 1e-4) for p, q in zip(overlap_box, clash["box"])))

described = dict((e["key"], e) for e in result["elements"])
check("run: an element is described by category, name, id and set",
      described[wall_key]["cat"] == u"Veggir" and
      described[wall_key]["name"] == u"Veggur 200" and
      described[wall_key]["id"] == site.wall.Id.Value and
      described[wall_key]["set"] == 1 and described[pipe_key]["set"] == 2)
levels = result["levels"]
check("run: levels come lowest first, in the same frame as the meshes",
      [l["name"] for l in levels] == [u"00 Jarðhæð",
                                      u"01 Hæð"] and
      close(levels[0]["z"], wall_box[2], 1e-4) and
      close(levels[1]["z"] - levels[0]["z"], 3.4, 1e-4), repr(levels))
check("run: nothing was written to the model", TRANSACTIONS == [])

reset()
page.send(run_config(init, context=False))
check("run: without surroundings only the clashing pair is sent",
      sorted(e["key"] for e in page.result()["elements"])
      == sorted([wall_key, pipe_key]))
check("run: and elements that clash with nothing are not read at all",
      site.floor.geometry_reads == 1 and site.pipe2.geometry_reads == 1,
      "floor {0}, pipe2 {1}".format(site.floor.geometry_reads,
                                    site.pipe2.geometry_reads))


# --------------------------------------------------------------------------
# 3. Geometry Revit will not intersect as solids
# --------------------------------------------------------------------------

reset()
site = building(broken=True)
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
result = page.result()
check("fallback: a failed boolean still finds the clash, on triangles",
      len(result["clashes"]) == 1 and result["clashes"][0]["exact"] is False,
      repr(result["clashes"]))
check("fallback: an approximate clash claims no volume or depth",
      result["clashes"][0]["vol"] is None and
      result["clashes"][0]["depth"] is None)
check("fallback: no overlap mesh is invented for it",
      not any(k[0] == "c" for k in decode_meshes(page.messages)))
check("fallback: the failure is counted", result["stats"]["failed"] == 1)

reset()
site = building(geometry="mesh")
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
result = page.result()
check("mesh only: an element with no solid is tested on its triangles",
      len(result["clashes"]) == 1 and result["clashes"][0]["exact"] is False)
check("mesh only: without asking Revit for a boolean it cannot do",
      BOOLEANS == [])

reset()
site = building(link_at=(3 * M, 0, 0))
SolidUtils.refuse = True
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
result = page.result()
check("link: a solid that will not transform is still found, on triangles",
      len(result["clashes"]) == 1 and result["clashes"][0]["exact"] is False,
      repr(result["clashes"]))
SolidUtils.refuse = False

reset()
site = building(geometry="instance")
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
check("instance: geometry inside a GeometryInstance is found",
      len(page.result()["clashes"]) == 1 and
      page.result()["clashes"][0]["exact"] is True)

reset()
site = building(wants_no_argument=True)
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
check("triangulate: a face that refuses a level of detail is retried bare",
      len(page.result()["clashes"]) == 1 and
      () in TRIANGULATE_CALLS and
      ("e", u"link:{0}:{1}".format(site.instance.Id.Value,
                                   site.pipe1.Id.Value))
      in decode_meshes(page.messages))


# --------------------------------------------------------------------------
# 4. A model against itself, and the selection
# --------------------------------------------------------------------------

reset()
beam = Element(GENERIC, (1 * M, -0.5 * M, 2.9 * M, 1.2 * M, 0.7 * M, 3.5 * M),
               u"Biti")
site = building()
site.host.elements.append(beam)
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init, a=("host",), b=("host",)))
result = page.result()
pairs = sorted(tuple(sorted((c["a"], c["b"]))) for c in result["clashes"])
check("self: wall against beam once, never an element against itself",
      pairs == [tuple(sorted((u"host:{0}".format(site.wall.Id.Value),
                              u"host:{0}".format(beam.Id.Value))))],
      repr(pairs))
check("self: elements in both sets are marked as both",
      all(e["set"] == 3 for e in result["elements"]))

reset()
site = building()
world = World(site.host, selected=[site.wall, site.room])
page = Page(new_service(world))
init = page.send({u"type": u"ready"})[0]
check("selection: offered first, as a source of its own",
      init["sources"][0]["kind"] == "selection" and
      [(c["id"], c["count"]) for c in init["sources"][0]["cats"]]
      == [(-2000011, 1)],
      repr(init["sources"][0]))
page.send(run_config(init, a=("selection",), b=("host", "link")))
result = page.result()
check("selection: the selected wall against everything else",
      len(result["clashes"]) == 1 and result["stats"]["a"] == 1 and
      result["clashes"][0]["a"] == u"host:{0}".format(site.wall.Id.Value),
      repr(result["clashes"]))


# --------------------------------------------------------------------------
# 5. Scope
# --------------------------------------------------------------------------

reset()
site = building()
site.host.ActiveView.shows = [site.floor]       # the wall is hidden
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init, scope=u"view"))
check("view: a host element the view does not show is not tested",
      page.result()["clashes"] == [] and page.result()["stats"]["a"] == 1)
page.send(run_config(init, scope=u"model"))
check("view: the whole model still finds it",
      len(page.result()["clashes"]) == 1)

reset()
far = (20 * M, 20 * M, 0, 25 * M, 25 * M, 3 * M)
site = building(view=View3D(u"{3D}", section=far))
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
check("section box: the page is told the view has one",
      init["view"]["sectionBox"] is True)
page.send(run_config(init, scope=u"view"))
result = page.result()
check("section box: elements outside it are left out, links included",
      result["clashes"] == [] and result["stats"]["clipped"] == 5 and
      result.get("empty") == u"A", repr(result["stats"]))

reset()
site = building(view=ViewSheet(u"A-101"))
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
check("sheet: a view that shows no elements is not offered",
      init["view"]["usable"] is False)
page.send(run_config(init, scope=u"view"))
check("sheet: asked for anyway, the whole model is tested and it says so",
      len(page.result()["clashes"]) == 1 and
      any(u"whole model" in n["text"] for n in page.of("notice")))

reset()
site = building()
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send({u"type": u"run", u"config": {
    u"a": every_category(init, "host"), u"b": [], u"scope": u"model"}})
check("empty: an empty set is named, and nothing is tested",
      page.result().get("empty") == u"B" and BOOLEANS == [])


# --------------------------------------------------------------------------
# 6. Stopping part way
# --------------------------------------------------------------------------

reset()
elements = []
for n in range(40):
    x = n * 2 * M
    elements.append(Element(WALLS, (x, 0, 0, x + M, M, M)))
    elements.append(Element(GENERIC, (x + 0.5 * M, 0, 0, x + 1.5 * M, M, M)))
host = Document(u"Margir.rvt", elements, view=View3D(u"{3D}"))
world = World(host)
clash_service = new_service(world)
service.PROGRESS_EVERY = 0.0
page = Page(clash_service, cancel_after=12)
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init, a=("host",), b=("host",)))
result = page.result()
check("cancel: stopped while collecting is stopped, not an empty set",
      result is not None and result["stats"]["cancelled"] is True and
      "empty" not in result and BOOLEANS == [], repr(result))
page.pumps = 0
page.cancel_after = 175             # past collecting, into the pairs
page.send(run_config(init, a=("host",), b=("host",)))
result = page.result()
check("cancel: a stopped run still returns what it found",
      result is not None and result["stats"]["cancelled"] is True and
      0 < len(result["clashes"]) < 40, repr(result and result["stats"]))
check("cancel: and says how far it got",
      result["stats"]["tested"] < result["stats"]["pairs"] == 40)
page.cancel_after = None
page.send(run_config(init, a=("host",), b=("host",)))
check("cancel: the next run is not still cancelled",
      len(page.result()["clashes"]) == 40 and
      page.result()["stats"]["cancelled"] is False)
service.PROGRESS_EVERY = 0.12


# --------------------------------------------------------------------------
# 7. Select in Revit
# --------------------------------------------------------------------------

reset()
site = building(link_at=(30 * M, -12 * M, 4 * M))
world = World(site.host)
page = Page(new_service(world))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
page.send({u"type": u"select", u"index": 0, u"zoom": True})
picked = world.selection.set_references
check("select: both elements, the linked one as a link reference",
      picked is not None and len(picked) == 2 and
      picked[0].element is site.wall and picked[0].instance is None and
      picked[1].element is site.pipe1 and
      picked[1].instance is site.instance)
zoom = world.ui_view.zoomed
overlap_feet = (2 * M, 0, 1 * M, 2.1 * M, 0.2 * M, 1.1 * M)
check("select: the view is zoomed to a box round the clash, in feet",
      zoom is not None and
      all(zoom[n] < overlap_feet[n] for n in range(3)) and
      all(zoom[n] > overlap_feet[n] for n in range(3, 6)) and
      zoom[3] - zoom[0] < 4 * M, repr(zoom))
check("select: it says so", page.of("notice")[0]["kind"] == u"ok")
check("select: it writes nothing", TRANSACTIONS == [])

world.ui_view.zoomed = None
page.send({u"type": u"select", u"index": 0, u"zoom": False})
check("select: without zoom the view is left alone",
      world.ui_view.zoomed is None)

world_old = World(site.host, references=False)      # before Revit 2023
page_old = Page(new_service(world_old))
page_old.send(run_config(page_old.send({u"type": u"ready"})[0]))
page_old.send({u"type": u"select", u"index": 0})
check("select: without SetReferences the link instance stands in",
      world_old.selection.set_ids == [site.wall.Id, site.instance.Id])

page.send({u"type": u"select", u"index": 7})
check("select: a clash that is not there is refused, not raised",
      page.of("error") == [] and page.of("notice")[0]["kind"] == u"warn")
site.host.elements.remove(site.wall)
page.send({u"type": u"select", u"index": 0})
check("select: a deleted element is reported",
      u"no longer exists" in page.of("notice")[0]["text"])


# --------------------------------------------------------------------------
# 8. Section box in Revit: the one write
# --------------------------------------------------------------------------

reset()
site = building()
world = World(site.host)
page = Page(new_service(world))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init))
check("section: nothing written before it is asked for", TRANSACTIONS == [])
page.send({u"type": u"sectionbox", u"index": 0})
view = site.host.ActiveView
check("section: one transaction, committed",
      len(TRANSACTIONS) == 1 and TRANSACTIONS[0].outcome == "Committed")
check("section: the view's box now surrounds the clash, with a margin",
      view.section is not None and
      all(view.section[n] < overlap_feet[n] - 0.5 * M for n in range(3)) and
      all(view.section[n] > overlap_feet[n] + 0.5 * M for n in range(3, 6)),
      repr(view.section))
check("section: it says which view, and that undo reverses it",
      u"{3D}" in page.of("notice")[0]["text"] and
      page.of("notice")[0]["kind"] == u"ok")
check("section: no transaction left open",
      site.host.open_transaction is False)

reset()
before = view.section
site.host.commit_rolls_back = True
page.send({u"type": u"sectionbox", u"index": 0})
check("section: a commit Revit rolls back is reported, not claimed",
      page.of("notice")[0]["kind"] == u"warn" and view.section == before,
      repr(page.of("notice")))
site.host.commit_rolls_back = False

reset()
view.refuses = True
page.send({u"type": u"sectionbox", u"index": 0})
check("section: Revit refusing rolls back and says why",
      TRANSACTIONS[0].outcome == "RolledBack" and
      u"template" in page.of("notice")[0]["text"] and
      site.host.open_transaction is False)
view.refuses = False

reset()
site = building(view=ViewPlan(u"Level 1"))
world = World(site.host)
page = Page(new_service(world))
page.send(run_config(page.send({u"type": u"ready"})[0]))
page.send({u"type": u"sectionbox", u"index": 0})
check("section: in a plan view it asks for a 3D view and writes nothing",
      TRANSACTIONS == [] and u"3D view" in page.of("notice")[0]["text"])


# --------------------------------------------------------------------------
# 9. When things go wrong
# --------------------------------------------------------------------------

reset()
site = building()
world = World(site.host)
page = Page(new_service(world))
init = page.send({u"type": u"ready"})[0]
other = Document(u"Annad.rvt", [], view=View3D(u"{3D}"))
world.uidoc.Document = other
page.send(run_config(init))
check("document: another active model stops the run with an explanation",
      page.result() is None and len(page.of("error")) == 1 and
      page.of("aborted") != [] and BOOLEANS == [])
world.uidoc.Document = site.host


def explode(*args):
    raise RuntimeError("geometry engine fell over")


real_pairs = model.candidate_pairs
model.candidate_pairs = explode
page.send(run_config(init))
model.candidate_pairs = real_pairs
check("error: an exception in a run reaches the page with its traceback",
      len(page.of("error")) == 1 and
      u"RuntimeError" in page.of("error")[0]["message"] and
      u"Traceback" in page.of("error")[0]["detail"])
check("error: and the page is told the run is over",
      page.of("aborted") != [])
page.send(run_config(init))
check("error: the service still works afterwards",
      len(page.result()["clashes"]) == 1)

unreadable = building()
unreadable.pipe1.get_Geometry = explode
page = Page(new_service(World(unreadable.host)))
page.send(run_config(page.send({u"type": u"ready"})[0]))
check("unreadable: one element whose geometry raises does not stop the run",
      page.result() is not None and
      page.result()["stats"]["unreadable"] == 1 and page.of("error") == [])

clash_service = new_service(World(building().host))
page = Page(clash_service)
init = page.send({u"type": u"ready"})[0]
clash_service.detach()
del page.messages[:]
reset()
clash_service.handle_api(run_config(init))
check("closed: a run after the window has gone does no work and raises "
      "nothing", page.messages == [] and BOOLEANS == [])

check("ids: an ElementId is built from a number",
      reader.make_id(DB, 42) == ElementId(42))
check("ids: read from Value, or IntegerValue before Revit 2024",
      reader.id_value(ElementId(7)) == 7 and
      reader.id_value(Namespace(IntegerValue=9)) == 9)
check("excluded: names this Revit does not have are skipped",
      reader.excluded_ids(DB) == set([-2000160]))


# --------------------------------------------------------------------------
# 10. Refresh, logging, and an empty set that says why
#
# Added at 2.44.1. In Revit the setup lists came up empty and Run said
# "Set A is empty", which is what an init that never arrives looks like.
# These pin down the pieces that now make that visible.
# --------------------------------------------------------------------------

class Logger(object):
    def __init__(self):
        self.lines = []

    def error(self, text):
        self.lines.append(text)


reset()
site = building()
world = World(site.host)
logger = Logger()
clash_service = service.Service(DB, world.uiapp, version=u"test",
                                logger=logger)
page = Page(clash_service)
page.send({u"type": u"ready"})
check("ready: before the inventory, the page is told, not left waiting",
      page.of("error") != [] and page.of("init") == [])

clash_service.build_inventory()
init = page.send({u"type": u"ready"})[0]
check("refresh: no selection at the click, none offered",
      [s["kind"] for s in init["sources"]] == ["host", "link"])
world.selection.ids = [site.wall.Id]
page.send({u"type": u"refresh", u"useSelection": True})
fresh = page.of("init")
check("refresh: reads the selection made after the click",
      len(fresh) == 1 and fresh[0]["sources"][0]["kind"] == "selection" and
      fresh[0]["refreshed"] is True and fresh[0]["useSelection"] is True,
      repr(page.messages))
page.send(run_config(fresh[0], a=("selection",), b=("host", "link")))
check("refresh: and a run uses it",
      page.result() is not None and page.result()["stats"]["a"] == 1 and
      len(page.result()["clashes"]) == 1)
check("refresh: reads, never writes", TRANSACTIONS == [])

world.uidoc.Document = Document(u"Annad.rvt", [], view=View3D(u"{3D}"))
page.send({u"type": u"refresh"})
check("refresh: with another model active it explains instead of "
      "listing the wrong one",
      page.of("init") == [] and len(page.of("error")) == 1)
world.uidoc.Document = site.host

page.send({u"type": u"log", u"message": u"TypeError: x is undefined",
           u"detail": u"at applyInit (app.js:120)"})
check("log: a page error lands in the pyRevit log",
      any(u"TypeError" in line and u"applyInit" in line
          for line in logger.lines), repr(logger.lines))
check("log: and is not a Revit API message",
      not clash_service.needs_api(u"log") and
      clash_service.needs_api(u"refresh"))

# The empty set, three ways.
reset()
far = (20 * M, 20 * M, 0, 25 * M, 25 * M, 3 * M)
site = building(view=View3D(u"{3D}", section=far))
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init, scope=u"view"))
why = page.result().get("why") or {}
check("why: outside the section box is counted per set",
      why.get("scope") == u"view" and why.get("sectionBox") is True and
      why["a"]["matched"] == 2 and why["a"]["clipped"] == 2 and
      why["b"]["clipped"] == 3, repr(why))

reset()
site = building()
site.host.ActiveView.shows = []                 # a view showing nothing
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
page.send(run_config(init, scope=u"view"))
why = page.result()["why"]
check("why: a view that shows none of the host is told apart",
      page.result()["empty"] == u"A" and why["a"]["looked"] == 0 and
      why["a"]["categories"] == 2, repr(why))

page.send({u"type": u"run", u"config": {
    u"a": [{u"source": u"link:999", u"cats": [-2000011]}],
    u"b": every_category(init, "link"), u"scope": u"model"}})
why = page.result()["why"]
check("why: a model Revit no longer has is named",
      why["a"]["unknown"] == [u"link:999"], repr(why))

page.send({u"type": u"run", u"config": {
    u"a": [], u"b": every_category(init, "link"), u"scope": u"model"}})
why = page.result()["why"]
check("why: nothing sent for a set is not mistaken for an empty model",
      why["a"]["entries"] == 0 and why["a"]["looked"] == 0, repr(why))


# --------------------------------------------------------------------------
# 11. json.dumps must not come back
#
# On IronPython json.dumps raises on any non-ASCII text, and this harness
# runs on CPython, where it does not. So a run here cannot catch it being
# used again; reading the source can.
# --------------------------------------------------------------------------

import glob  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402

DUMPS = re.compile(r"\bjson\.dumps?\(")
for path in sorted(glob.glob(os.path.join(HERE, "lib", "avh_clash", "*.py"))):
    code = []
    for line in io.open(path, encoding="utf-8").read().split("\n"):
        if not line.lstrip().startswith("#"):
            code.append(line)
    found = [n + 1 for n, line in enumerate(code) if DUMPS.search(line)]
    check(u"no json.dumps in {0} (raises on IronPython for names with "
          u"accents)".format(os.path.basename(path)), not found,
          u"line(s) {0}".format(found))

window_source = io.open(os.path.join(HERE, "lib", "avh_clash", "window.py"),
                        encoding="utf-8").read()
check("window: every message to the page goes through model.to_json",
      window_source.count("PostWebMessageAsJson(model.to_json(") == 2 and
      window_source.count("PostWebMessageAsJson(") == 2)

# And the messages a run really produces, with non-ASCII names, encode.
reset()
site = building()
site.wall.Name = u"Veggur öðru vísi"
site.host.levels[0].Name = u"01 Hæð"
page = Page(new_service(World(site.host)))
init = page.send({u"type": u"ready"})[0]
messages = page.send(run_config(init))
import json  # noqa: E402
encoded = [model.to_json(message) for message in [init] + messages]
check("encode: every message of a run with accented names is plain ASCII",
      all(all(ord(c) < 128 for c in text) for text in encoded))
check("encode: and the page reads back what was sent",
      [json.loads(text) for text in encoded] == [init] + messages)


# --------------------------------------------------------------------------
# The check itself must be able to fail.
# --------------------------------------------------------------------------

before_count = len(results)
check("self-check", False)
if results[-1][1] is not False or len(results) != before_count + 1:
    results.append(("self-check: a false check is recorded as false",
                    False, ""))
else:
    results.pop()

print("Clash Detector: script and service")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if not ok:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
