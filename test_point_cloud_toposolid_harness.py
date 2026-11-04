# -*- coding: utf-8 -*-
"""
Runs the real Point Cloud to Toposolid script against a mocked Revit.

The thing worth testing is where the surface ends up. A toposolid built
from points read in the wrong coordinate system, or at the wrong height,
looks exactly like a correct one until somebody sections through it. So
the fake cloud samples a known sloping plane, and every scenario checks
each toposolid point against that plane at its own X and Y.

The fake lies the way Revit is likely to, and in the places the API
documentation is silent it lies both ways, in separate scenarios:

- GetPoints returns points in the cloud's own coordinates in one
  scenario and in the model's in another. The cloud is rotated and moved
  well away from the origin, so the wrong reading fails loudly.
- Toposolid.Create reads Z as absolute in one scenario and relative to
  the level in another.
- A new toposolid has no bounding box until the document regenerates.
- GetPoints refuses 1 000 000 points or more. The documentation says
  "from 1 to 1000000", but Revit refused exactly 1 000 000 on the first
  live run, and this fake allowed it, which is how that shipped. Past
  the number asked for, it returns the first ones in scan order: a dense
  tile read without splitting covers a strip of itself, not the whole.
- A toposolid type's Name raises, as ElementType.Name can on IronPython.

Run outside Revit:

    python test_point_cloud_toposolid_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Point Cloud to Toposolid.pushbutton", "script.py")

from avh_topo import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def safely(function, *args):
    """Call it, and turn a raise into a value that fails a check."""
    try:
        return function(*args)
    except Exception as exc:
        return u"<raised {0}>".format(exc)


FEET = 1.0 / model.METRES_PER_FOOT

# The terrain, in model coordinates and feet: a plane rising east and
# north, so a point at the wrong X or Y is also at the wrong height.
Z0 = 20.0 * FEET
SLOPE_X = 0.08
SLOPE_Y = -0.03


def terrain(x, y):
    return Z0 + SLOPE_X * x + SLOPE_Y * y


# The cloud: 60 m by 40 m in its own coordinates, rotated 25 degrees
# and placed 500 m east and 300 m north of the model origin.
CLOUD_W = 60.0 * FEET
CLOUD_D = 40.0 * FEET
ANGLE = math.radians(25.0)
OFFSET = (500.0 * FEET, 300.0 * FEET, 2.0 * FEET)


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class FakeTransform(object):
    def __init__(self, angle, offset):
        c, s = math.cos(angle), math.sin(angle)
        self.Origin = FakeXYZ(*offset)
        self.BasisX = FakeXYZ(c, s, 0.0)
        self.BasisY = FakeXYZ(-s, c, 0.0)
        self.BasisZ = FakeXYZ(0.0, 0.0, 1.0)
        self.angle = angle
        self.offset = offset

    def to_model(self, u, v, w):
        c, s = math.cos(self.angle), math.sin(self.angle)
        return (self.offset[0] + u * c - v * s,
                self.offset[1] + u * s + v * c,
                self.offset[2] + w)

    def to_cloud(self, x, y, z):
        c, s = math.cos(self.angle), math.sin(self.angle)
        dx, dy = x - self.offset[0], y - self.offset[1]
        return (dx * c + dy * s, -dx * s + dy * c, z - self.offset[2])


class FakeBox(object):
    def __init__(self, low, high):
        self.Min = FakeXYZ(*low)
        self.Max = FakeXYZ(*high)


class FakeCloudPoint(object):
    def __init__(self, x, y, z):
        self.X = x
        self.Y = y
        self.Z = z
        self.Color = 0


class FakePlane(object):
    def __init__(self, normal, origin):
        self.normal = (normal.X, normal.Y, normal.Z)
        self.origin = (origin.X, origin.Y, origin.Z)

    @staticmethod
    def CreateByNormalAndOrigin(normal, origin):
        return FakePlane(normal, origin)

    def keeps(self, point):
        return sum((point[i] - self.origin[i]) * self.normal[i]
                   for i in range(3)) >= -1e-9


class FakeFilter(object):
    def __init__(self, planes):
        self.planes = list(planes)


class FakeFilterFactory(object):
    @staticmethod
    def CreateMultiPlaneFilter(planes):
        if not isinstance(planes, FakeList):
            raise TypeError("expected IList<Plane>")
        return FakeFilter(planes)


class FakeElementId(object):
    def __init__(self, value):
        self.Value = value


_NEXT_ID = [1000]


def new_id():
    _NEXT_ID[0] += 1
    return FakeElementId(_NEXT_ID[0])


class FakePointCloudInstance(object):
    """A cloud sampling `terrain` on a lattice at the asked distance.

    `returns` is "cloud" (points in the cloud's own coordinates),
    "model", or "nowhere" (10 km off, fitting neither reading).
    """

    # Refused from here up. Observed in Revit, not read in the docs.
    REFUSED_FROM = 1000000

    def __init__(self, name, returns="cloud", transform=None, raises=False,
                 empty=False, ground=None, objects=(), strays=False):
        """`ground` is the terrain, `terrain` unless given. `objects` are
        (x0, y0, x1, y1, height, solid) in model metres: a solid one
        hides the ground under it, as a truck roof does; a porous one
        lets every other sample through, as a shrub or a person in a
        crowd does. `strays` adds one return half a metre under the
        ground at scattered places, as scanners do."""
        self.ground = ground or terrain
        self.objects = list(objects)
        self.strays = strays
        self.Name = name
        self.Id = new_id()
        self.returns = returns
        self.transform = transform or FakeTransform(ANGLE, OFFSET)
        self.raises = raises
        self.empty = empty
        self.calls = []

    def GetTotalTransform(self):
        return self.transform

    def model_points(self, step):
        found = []
        rows = int(CLOUD_D / step) + 1
        columns = int(CLOUD_W / step) + 1
        for row in range(rows):
            for column in range(columns):
                x, y, _z = self.transform.to_model(column * step,
                                                   row * step, 0.0)
                z = self.ground(x, y)
                for x0, y0, x1, y1, height, solid in self.objects:
                    if (x0 * FEET <= x <= x1 * FEET and
                            y0 * FEET <= y <= y1 * FEET):
                        if solid or (row + column) % 2 == 0:
                            z += height * FEET
                found.append((x, y, z))
                # About one return in forty, at most two to a cell at the
                # filter's density: rare, as real ones are, and within
                # what the third lowest sample is there to absorb.
                if self.strays and (row * 7 + column * 3) % 41 == 0:
                    found.append((x, y, self.ground(x, y) - 0.5 * FEET))
        return found

    def get_BoundingBox(self, view):
        corners = [self.transform.to_model(u, v, 0.0)
                   for u in (0.0, CLOUD_W) for v in (0.0, CLOUD_D)]
        zs = [self.ground(c[0], c[1]) for c in corners]
        tallest = max([0.0] + [o[4] for o in self.objects]) * FEET
        zs = [min(zs) - 0.5 * FEET - 3.0 * FEET,
              max(zs) + tallest + 3.0 * FEET]
        return FakeBox((min(c[0] for c in corners),
                        min(c[1] for c in corners), min(zs)),
                       (max(c[0] for c in corners),
                        max(c[1] for c in corners), max(zs)))

    def GetPoints(self, cloud_filter, average_distance, count):
        self.calls.append((average_distance, count))
        if count < 1 or count >= self.REFUSED_FROM:
            raise ValueError("The number of points read must range from 1 "
                             "to 1000000.")
        if self.raises:
            raise Exception("point cloud engine unavailable")
        if self.empty:
            return []
        found = []
        for point in self.model_points(average_distance):
            if all(plane.keeps(point) for plane in cloud_filter.planes):
                if self.returns == "cloud":
                    found.append(FakeCloudPoint(
                        *self.transform.to_cloud(*point)))
                elif self.returns == "nowhere":
                    found.append(FakeCloudPoint(
                        point[0] + 33000.0, point[1], point[2]))
                else:
                    found.append(FakeCloudPoint(*point))
                if len(found) >= count:
                    break
        return found


class FakeParameter(object):
    def __init__(self, text):
        self.text = text

    def AsString(self):
        return self.text


class FakeToposolidType(object):
    """ElementType.Name can raise on IronPython. This one always does."""

    def __init__(self, name, thickness_m=1.0, structure=True):
        self.label = name
        self.Id = new_id()
        self.thickness = thickness_m * FEET
        self.structure = structure

    @property
    def Name(self):
        raise AttributeError("Name")

    def GetCompoundStructure(self):
        if not self.structure:
            raise Exception("no structure")
        return Namespace(GetWidth=lambda: self.thickness)

    def get_Parameter(self, which):
        if which == "SYMBOL_NAME_PARAM":
            return FakeParameter(self.label)
        return None


class FakeLevel(object):
    def __init__(self, name, elevation_metres):
        self.Name = name
        self.Elevation = elevation_metres * FEET
        self.Id = new_id()


class FakeLine(object):
    def __init__(self, a, b):
        self.a = (a.X, a.Y, a.Z)
        self.b = (b.X, b.Y, b.Z)

    @staticmethod
    def CreateBound(a, b):
        return FakeLine(a, b)


class FakeCurveLoop(object):
    def __init__(self):
        self.lines = []

    def Append(self, line):
        self.lines.append(line)


class FakeList(list):
    def Add(self, item):
        self.append(item)


class FakeListFactory(object):
    def __getitem__(self, item_type):
        return FakeList


class FakeToposolid(object):
    def __init__(self, doc, points, level):
        self.Id = new_id()
        self.doc = doc
        self.given = points
        self.level = level
        self.regenerated = False
        lift = level.Elevation if doc.z_relative else 0.0
        lift += doc.z_junk
        self.points = [(p[0], p[1], p[2] + lift) for p in points]
        # Revit's surface does not pass exactly through every point: on
        # the first live build its top sat 65 mm under the highest point
        # given. `top_sag` lowers the highest vertex by that much.
        if doc.top_sag and self.points:
            high = max(range(len(self.points)),
                       key=lambda i: self.points[i][2])
            x, y, z = self.points[high]
            self.points[high] = (x, y, z - doc.top_sag)

    def GetSlabShapeEditor(self):
        if self.doc.vertices_raise:
            raise Exception("no shape editor")
        return Namespace(SlabShapeVertices=[
            Namespace(Position=FakeXYZ(*p)) for p in self.points])

    def get_BoundingBox(self, view):
        if not self.regenerated:
            return None
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        zs = [p[2] for p in self.points]
        return FakeBox((min(xs), min(ys), min(zs) - 1.0),
                       (max(xs), max(ys), max(zs)))


class FakeToposolidClass(object):
    doc = None

    @staticmethod
    def Create(document, profiles, points, type_id, level_id):
        doc = FakeToposolidClass.doc
        doc.creates += 1
        if doc.create_raises:
            raise Exception("Input curves build invalid sketch.")
        if not isinstance(profiles, FakeList) or not isinstance(points,
                                                                FakeList):
            raise TypeError("expected IList")
        level = None
        for candidate in doc.levels:
            if candidate.Id is level_id:
                level = candidate
        if level is None:
            raise ValueError("The ElementId levelId is not a Level.")
        if not any(t.Id is type_id for t in doc.types):
            raise ValueError("Toposolid type is not valid.")
        if len(profiles) != 1:
            raise ValueError("expected one profile")
        lines = profiles[0].lines
        for index, line in enumerate(lines):
            following = lines[(index + 1) % len(lines)]
            if any(abs(line.b[i] - following.a[i]) > 1e-9 for i in range(3)):
                raise ValueError("The curve loop is not closed.")
            if abs(line.a[2] - line.b[2]) > 1e-9:
                raise ValueError("The curve loop is not horizontal.")
        xs = [line.a[0] for line in lines]
        ys = [line.a[1] for line in lines]
        for point in points:
            if not (min(xs) - 1e-6 <= point.X <= max(xs) + 1e-6 and
                    min(ys) - 1e-6 <= point.Y <= max(ys) + 1e-6):
                raise ValueError("A point lies outside the boundary.")
        made = FakeToposolid(doc, [(p.X, p.Y, p.Z) for p in points], level)
        made.outline = lines
        made.type_id = type_id
        doc.toposolids.append(made)
        return made


class FakeDocument(object):
    def __init__(self, clouds=None, types_=None, levels=None,
                 z_relative=False, z_junk=0.0, create_raises=False,
                 commit_status="Committed", top_sag=0.0,
                 vertices_raise=False):
        self.clouds = list(clouds if clouds is not None else
                           [FakePointCloudInstance(u"ELG_Site_Ground")])
        self.types = list(types_ if types_ is not None else
                          [FakeToposolidType(u"Generic 300mm"),
                           FakeToposolidType(u"AVH Landslag 500mm")])
        self.levels = list(levels if levels is not None else
                           [FakeLevel(u"DA11.E02", 4.0),
                            FakeLevel(u"DA11.E01", 0.0),
                            FakeLevel(u"DA11.E03", 8.0)])
        self.z_relative = z_relative
        self.z_junk = z_junk
        self.top_sag = top_sag
        self.vertices_raise = vertices_raise
        self.dialogs = []
        self.create_raises = create_raises
        self.commit_status = commit_status
        self.toposolids = []
        self.creates = 0
        self.deleted = []
        self.regenerations = 0
        FakeToposolidClass.doc = self

    def Regenerate(self):
        self.regenerations += 1
        for toposolid in self.toposolids:
            toposolid.regenerated = True

    def Delete(self, element_id):
        for toposolid in list(self.toposolids):
            if toposolid.Id is element_id:
                self.toposolids.remove(toposolid)
                self.deleted.append(element_id)
                return
        raise Exception("no such element")

    def snapshot(self):
        return list(self.toposolids)

    def restore(self, state):
        self.toposolids = list(state)


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.cls = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def __iter__(self):
        if self.cls is FakePointCloudInstance:
            return iter(list(self.doc.clouds))
        if self.cls is FakeToposolidType:
            return iter(list(self.doc.types))
        if self.cls is FakeLevel:
            return iter(list(self.doc.levels))
        return iter([])


class FakeFailureId(object):
    def __init__(self, guid):
        self.Guid = guid


TOO_THIN = FakeFailureId("guid-too-thin")
OTHER_FAILURE = FakeFailureId("guid-other")


class FakeFailureMessage(object):
    def __init__(self, failure_id):
        self.failure_id = failure_id

    def GetFailureDefinitionId(self):
        # A fresh object each time, as Revit hands back: compare by Guid.
        return FakeFailureId(self.failure_id.Guid)


class FakeFailuresAccessor(object):
    def __init__(self, messages):
        self.messages = messages

    def GetFailureMessages(self):
        return list(self.messages)


class FakeFailureOptions(object):
    def __init__(self):
        self.preprocessor = None

    def SetFailuresPreprocessor(self, preprocessor):
        self.preprocessor = preprocessor


class FakeTransaction(object):
    log = []

    def __init__(self, doc, name):
        self.doc = doc
        self.state = None
        self.options = FakeFailureOptions()

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options

    def failures(self):
        """Too thin, the way the first live run met it: a point under the
        level less the type's thickness. Posted at commit."""
        found = []
        for made in self.doc.toposolids:
            kind = [t for t in self.doc.types if t.Id is made.type_id]
            if not kind:
                continue
            bottom = made.level.Elevation - kind[0].thickness
            if min(p[2] for p in made.points) < bottom - 1e-9:
                found.append(FakeFailureMessage(TOO_THIN))
        return found

    def Start(self):
        self.state = self.doc.snapshot()
        FakeTransaction.log.append("start")

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.log.append("rollback")

    def Commit(self):
        FakeTransaction.log.append("commit")
        messages = self.failures()
        if messages:
            catcher = self.options.preprocessor
            result = None
            if catcher is not None:
                result = catcher.PreprocessFailures(
                    FakeFailuresAccessor(messages))
            if result != "ProceedWithRollBack":
                # Revit's own dialog; the user can only cancel.
                self.doc.dialogs.append("too thin")
            self.doc.restore(self.state)
            return "RolledBack"
        if self.doc.commit_status != "Committed":
            self.doc.restore(self.state)
        return self.doc.commit_status


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    """Answers the dialogs.

    `picks` maps a word in a picker's title to a word in the label to
    pick, or to None to cancel that picker.
    """

    def __init__(self, spacing=u"1", picks=None, confirm=True, window=u"4"):
        self.spacing = spacing
        self.window = window
        self.prompts = []
        self.picks = picks or {}
        self.confirm = confirm
        self.alerts = []
        self.questions = []
        self.printed = []
        self.pickers = []

    def ask_for_string(self, default=u"", prompt=u"", title=u"", **kwargs):
        self.prompts.append(prompt)
        if u"Largest object" in prompt:
            self.pickers.append(u"window")
            return self.window
        self.pickers.append(u"spacing")
        return self.spacing

    def show(self, items, multiselect=False, title=u"", button_name=u"",
             **kwargs):
        self.pickers.append(title)
        for word, wanted in self.picks.items():
            if word in title:
                if wanted is None:
                    return None
                for item in items:
                    if wanted in item:
                        return item
                return None
        return items[0] if items else None

    def alert(self, message, **kwargs):
        if kwargs.get("yes") and kwargs.get("no"):
            self.questions.append(message)
            return self.confirm
        self.alerts.append(message)
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.questions + self.printed)


def run_script(doc, recorder=None, revit_2023=False):
    del FakeTransaction.log[:]
    recorder = recorder or Recorder()

    db = Namespace(
        XYZ=FakeXYZ,
        Plane=FakePlane,
        PointClouds=Namespace(PointCloudFilterFactory=FakeFilterFactory),
        PointCloudInstance=FakePointCloudInstance,
        ToposolidType=FakeToposolidType,
        Level=FakeLevel,
        CurveLoop=FakeCurveLoop,
        Line=FakeLine,
        FilteredElementCollector=FakeCollector,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        BuiltInParameter=Namespace(SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM"),
        IFailuresPreprocessor=object,
        BuiltInFailures=Namespace(SlabShapeFailures=Namespace(
            SlabShapeFailedTooThin=TOO_THIN)),
        FailureProcessingResult=Namespace(
            Continue="Continue", ProceedWithRollBack="ProceedWithRollBack"),
    )
    if not revit_2023:
        db.Toposolid = FakeToposolidClass

    system = types.ModuleType("System")
    collections = types.ModuleType("System.Collections")
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = FakeListFactory()
    collections.Generic = generic
    system.Collections = collections
    sys.modules["System"] = system
    sys.modules["System.Collections"] = collections
    sys.modules["System.Collections.Generic"] = generic

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=None)
    pyrevit.DB = db
    pyrevit.forms = Namespace(
        alert=recorder.alert,
        ask_for_string=recorder.ask_for_string,
        SelectFromList=Namespace(show=recorder.show))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        for name in ("pyrevit", "System", "System.Collections",
                     "System.Collections.Generic"):
            sys.modules.pop(name, None)
    return recorder


def on_terrain(toposolid, tolerance=1e-6):
    """The worst miss of a toposolid's given points off the terrain."""
    worst = 0.0
    for x, y, z in toposolid.given:
        worst = max(worst, abs(z - terrain(x, y)))
    return worst <= tolerance, worst


def covers_cloud(toposolid, cloud, spacing_ft):
    """Do the points reach every side of the cloud's box, within a cell?"""
    box = cloud.get_BoundingBox(None)
    xs = [p[0] for p in toposolid.given]
    ys = [p[1] for p in toposolid.given]
    misses = (min(xs) - box.Min.X, box.Max.X - max(xs),
              min(ys) - box.Min.Y, box.Max.Y - max(ys))
    return all(0.0 <= miss <= spacing_ft for miss in misses), misses


def expected_cells(cloud, spacing_ft):
    """Occupied cells worked out independently of the script."""
    box = cloud.get_BoundingBox(None)
    step = spacing_ft / 2.0
    keys = set()
    for x, y, _z in cloud.model_points(step):
        keys.add((int(math.floor((x - box.Min.X) / spacing_ft)),
                  int(math.floor((y - box.Min.Y) / spacing_ft))))
    return len(keys)


def survivor(doc):
    return doc.toposolids[0] if len(doc.toposolids) == 1 else None


# --------------------------------------------------------------------------
# The arithmetic, no Revit at all
# --------------------------------------------------------------------------

found = model.tiles(0.0, 0.0, 10.0, 5.0, 4.0)
check("tiles: a 10 by 5 extent at 4 is 3 by 2", len(found) == 6, repr(found))
check("tiles: they reach the far corner",
      max(t[2] for t in found) == 10.0 and max(t[3] for t in found) == 5.0)
check("tiles: none is larger than asked",
      all(t[2] - t[0] <= 4.0 and t[3] - t[1] <= 4.0 for t in found))
check("tiles: an extent smaller than a tile is one tile",
      len(model.tiles(0.0, 0.0, 1.0, 1.0, 4.0)) == 1)
check("tiles: a reversed extent is nothing",
      model.tiles(5.0, 0.0, 1.0, 1.0, 4.0) == [])

parts = model.quarters((0.0, 0.0, 4.0, 2.0))
check("quarters: four of them", len(parts) == 4)
check("quarters: they tile the original exactly",
      abs(sum((p[2] - p[0]) * (p[3] - p[1]) for p in parts) - 8.0) < 1e-12
      and min(p[0] for p in parts) == 0.0 and max(p[2] for p in parts) == 4.0
      and min(p[1] for p in parts) == 0.0 and max(p[3] for p in parts) == 2.0)


def passes(tile, point):
    for normal, origin in model.tile_planes(tile):
        if sum((point[i] - origin[i]) * normal[i] for i in range(3)) < 0:
            return False
    return True


check("planes: a point inside the tile passes all four",
      passes((0.0, 0.0, 10.0, 10.0), (5.0, 5.0, 99.0)))
check("planes: a point west of it fails",
      not passes((0.0, 0.0, 10.0, 10.0), (-1.0, 5.0, 0.0)))
check("planes: a point east of it fails",
      not passes((0.0, 0.0, 10.0, 10.0), (11.0, 5.0, 0.0)))
check("planes: a point south of it fails",
      not passes((0.0, 0.0, 10.0, 10.0), (5.0, -1.0, 0.0)))
check("planes: a point north of it fails",
      not passes((0.0, 0.0, 10.0, 10.0), (5.0, 11.0, 0.0)))

shift = ((10.0, 20.0, 3.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0),
         (0.0, 0.0, 1.0))
check("transform: a quarter turn and a shift",
      model.apply_transform(shift, 1.0, 0.0, 0.0) == (10.0, 21.0, 3.0))
check("transform: identity is recognised",
      model.is_identity(((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))))
check("transform: a shift is not identity", not model.is_identity(shift))

box = ((100.0, 100.0, 0.0), (200.0, 200.0, 50.0))
inside_after = [(1.0, 1.0, 1.0), (2.0, 2.0, 2.0)]
move = ((150.0, 150.0, 0.0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
check("frame: points that fit only once transformed are TRANSFORMED",
      model.pick_frame(inside_after, move, box[0], box[1])[0]
      == model.TRANSFORMED)
check("frame: points that fit only as returned are RAW",
      model.pick_frame([(150.0, 150.0, 1.0)], move, box[0], box[1])[0]
      == model.RAW)
check("frame: points that fit neither are refused",
      model.pick_frame([(9000.0, 0.0, 0.0)], move, box[0], box[1])[0]
      is None)
check("frame: an identity transform is RAW",
      model.pick_frame([(9000.0, 0.0, 0.0)],
                       ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
                       box[0], box[1])[0] == model.RAW)

grid = model.Grid(0.0, 0.0, 2.0)
for point in ((0.1, 0.1, 1.0), (0.5, 0.3, 3.0), (2.5, 0.5, 7.0)):
    grid.add(*point)
check("grid: one point per occupied cell", len(grid.points()) == 2)
check("grid: each is the mean of its samples in all three axes",
      grid.points()[0] == (0.3, 0.2, 2.0), repr(grid.points()))
check("grid: samples are counted", grid.samples == 3)
check("grid: a sample on a cell edge goes to the upper cell",
      model.Grid(0.0, 0.0, 2.0).key(2.0, 0.0) == (1, 0))

ring = model.boundary([(0.0, 0.0, 0.0), (4.0, 1.0, 0.0), (2.0, 3.0, 0.0)])
check("boundary: the rectangle through the outermost points",
      ring == [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)], repr(ring))
check("boundary: points in a line have none",
      model.boundary([(0.0, 0.0, 0.0), (4.0, 0.0, 0.0)]) is None)
check("boundary: no points have none", model.boundary([]) is None)

check("z: the top at the highest point is ABSOLUTE",
      model.z_mode(10.0, 10.0, 13.0) == model.ABSOLUTE)
check("z: the top one level higher is RELATIVE",
      model.z_mode(10.0, 23.0, 13.0) == model.RELATIVE)
check("z: anything else is refused", model.z_mode(10.0, 16.0, 13.0) is None)
check("z: no top is refused", model.z_mode(10.0, None, 13.0) is None)

check("spacing: 1 m is fine", model.spacing_error(1.0) == u"")
check("spacing: 5 cm is refused", model.spacing_error(0.05) != u"")
check("spacing: 80 m is refused", model.spacing_error(80.0) != u"")
check("spacing: averageDistance is half the spacing",
      model.average_distance(2.0) == 1.0)


# --------------------------------------------------------------------------
# 1. The ordinary run: points in the cloud's own coordinates
# --------------------------------------------------------------------------

doc = FakeDocument()
cloud = doc.clouds[0]
recorder = run_script(doc, Recorder(picks={u"type": u"Landslag",
                                           u"level": u"DA11.E01"}))
made = survivor(doc)
check("run: one toposolid exists", made is not None,
      u"{0} / {1}".format(len(doc.toposolids), recorder.text()))
ok, worst = safely(on_terrain, made) if made else (False, None)
check("run: every point lies on the ground the cloud sampled", ok,
      repr(worst))
covered = safely(covers_cloud, made, cloud, FEET) if made else (False, None)
check("run: the points reach every side of the cloud", covered[0],
      repr(covered))
check("run: one point per occupied cell, no more and no fewer",
      made is not None and len(made.given) == expected_cells(cloud, FEET),
      u"{0} vs {1}".format(len(made.given) if made else None,
                           expected_cells(cloud, FEET)))
check("run: the picked type was used",
      made is not None and made.type_id is doc.types[1].Id)
check("run: built once", doc.creates == 1, doc.creates)
check("run: on the picked level", made is not None and
      made.level.Name == u"DA11.E01")
check("run: GetPoints was asked at half the spacing",
      cloud.calls and all(abs(call[0] - FEET / 2.0) < 1e-9
                          for call in cloud.calls), repr(cloud.calls[:2]))
check("run: and never for more than the cap",
      all(call[1] < 1000000 for call in cloud.calls))
check("run: the confirmation gives the point count",
      recorder.questions and
      u"{0} points".format(len(made.given) if made else -1)
      in recorder.questions[0], recorder.questions[:1])
check("run: the type's name came through the parameter fallback",
      recorder.questions and u"AVH Landslag" in recorder.questions[0])
check("run: the report says the points went through the transform",
      u"through the cloud's transform" in recorder.text())
check("run: committed", FakeTransaction.log == ["start", "commit"],
      repr(FakeTransaction.log))
check("run: no warning about point count for a small site",
      recorder.questions and u"a lot of points" not in recorder.questions[0])


# --------------------------------------------------------------------------
# 2. Points returned in model coordinates
# --------------------------------------------------------------------------

doc = FakeDocument(clouds=[FakePointCloudInstance(u"ELG", returns="model")])
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
made = survivor(doc)
ok, worst = safely(on_terrain, made) if made else (False, None)
check("model frame: every point lies on the ground", ok, repr(worst))
check("model frame: the report says as returned",
      u"| as returned |" in recorder.text(), recorder.printed)


# --------------------------------------------------------------------------
# 3. Points that fit nowhere
# --------------------------------------------------------------------------

doc = FakeDocument(clouds=[FakePointCloudInstance(u"ELG", returns="nowhere")])
recorder = run_script(doc)
check("nowhere: nothing built", not doc.toposolids and doc.creates == 0)
check("nowhere: the user is told why",
      u"neither as returned" in recorder.text(), recorder.alerts)
check("nowhere: and the numbers are printed",
      any(u"share inside" in line for line in recorder.printed))


# --------------------------------------------------------------------------
# 4. A level above zero, Z read as absolute
# --------------------------------------------------------------------------

doc = FakeDocument()
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E02"}))
made = survivor(doc)
ok, worst = safely(on_terrain, made) if made else (False, None)
check("absolute on E02: points given at their real height", ok, repr(worst))
check("absolute on E02: built once", doc.creates == 1, doc.creates)
check("absolute on E02: the outline sits at the level's elevation",
      made is not None and made.level.Elevation > 0 and
      all(abs(line.a[2] - made.level.Elevation) < 1e-9
          for line in made.outline))
check("absolute on E02: the report says absolute",
      u"| absolute |" in recorder.text())


# --------------------------------------------------------------------------
# 5. A level above zero, Z read as relative
# --------------------------------------------------------------------------

doc = FakeDocument(z_relative=True)
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E02"}))
made = survivor(doc)
check("relative: one toposolid survives", made is not None,
      u"{0} / {1}".format(len(doc.toposolids), recorder.alerts))
check("relative: rebuilt once", doc.creates == 2, doc.creates)
check("relative: the first one was deleted", len(doc.deleted) == 1)
check("relative: its points end up on the ground",
      made is not None and
      all(abs(p[2] - terrain(p[0], p[1])) < 1e-6 for p in made.points),
      repr(made.points[:2]) if made else None)
check("relative: the report says it was corrected",
      u"relative to the level, and corrected" in recorder.text())


# --------------------------------------------------------------------------
# 6. A toposolid at a height nobody can explain
# --------------------------------------------------------------------------

doc = FakeDocument(z_junk=3.0 * FEET)
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E02"}))
check("junk height: nothing kept", not doc.toposolids)
check("junk height: rolled back", "rollback" in FakeTransaction.log)
check("junk height: the user is told the heights",
      u"did not come out at the height" in recorder.text(), recorder.alerts)


# --------------------------------------------------------------------------
# 7. A dense cloud: tiles at the cap are split
# --------------------------------------------------------------------------

saved = (model.MAX_POINTS_PER_CALL, model.MAX_SPLITS, model.WARN_POINTS)
try:
    model.MAX_POINTS_PER_CALL = 2000
    doc = FakeDocument()
    cloud = doc.clouds[0]
    recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
    made = survivor(doc)
    check("dense: GetPoints was called more than once",
          len(cloud.calls) > 1, len(cloud.calls))
    check("dense: never asked for more than the cap",
          all(call[1] <= 2000 for call in cloud.calls))
    covered = safely(covers_cloud, made, cloud, FEET) if made else (False,)
    check("dense: still reaches every side of the cloud", covered[0],
          repr(covered))
    check("dense: still one point per occupied cell",
          made is not None and len(made.given) == expected_cells(cloud, FEET),
          u"{0} vs {1}".format(len(made.given) if made else None,
                               expected_cells(cloud, FEET)))
    check("dense: nothing reported as sampled",
          u"sampled" not in recorder.text())

    model.MAX_POINTS_PER_CALL = 40
    model.MAX_SPLITS = 1
    doc = FakeDocument()
    recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
    check("too dense: the confirmation says parts were sampled",
          recorder.questions and u"sampled" in recorder.questions[0],
          recorder.questions[:1])

    model.MAX_POINTS_PER_CALL = saved[0]
    model.MAX_SPLITS = saved[1]
    model.WARN_POINTS = 100
    doc = FakeDocument()
    recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
    check("many points: the confirmation warns",
          recorder.questions and u"a lot of points" in recorder.questions[0])
finally:
    model.MAX_POINTS_PER_CALL, model.MAX_SPLITS, model.WARN_POINTS = saved


# --------------------------------------------------------------------------
# 8. Spacing
# --------------------------------------------------------------------------

doc = FakeDocument()
cloud = doc.clouds[0]
recorder = run_script(doc, Recorder(spacing=u"2,5",
                                    picks={u"level": u"DA11.E01"}))
check("comma spacing: accepted", survivor(doc) is not None, recorder.alerts)
check("comma spacing: GetPoints asked at 1.25 m",
      cloud.calls and abs(cloud.calls[0][0] - 1.25 * FEET) < 1e-9,
      repr(cloud.calls[:1]))

for bad in (u"abc", u"0,05", u"80"):
    # An empty cloud, so a spacing that slips through is read quickly
    # and fails the check rather than grinding through a dense lattice.
    doc = FakeDocument(clouds=[FakePointCloudInstance(u"ELG", empty=True)])
    recorder = run_script(doc, Recorder(spacing=bad))
    check(u"spacing {0!r}: refused before reading".format(bad),
          not doc.clouds[0].calls and not doc.toposolids and recorder.alerts,
          recorder.alerts)


# --------------------------------------------------------------------------
# 9. Cancelling, and nothing to work with
# --------------------------------------------------------------------------

two = [FakePointCloudInstance(u"North"), FakePointCloudInstance(u"South")]
doc = FakeDocument(clouds=two)
recorder = run_script(doc, Recorder(picks={u"cloud": None}))
check("cancel cloud: nothing read or built",
      not two[0].calls and not two[1].calls and not doc.toposolids)

doc = FakeDocument(clouds=[FakePointCloudInstance(u"North"),
                           FakePointCloudInstance(u"South")])
recorder = run_script(doc, Recorder(picks={u"cloud": u"South",
                                           u"level": u"DA11.E01"}))
check("two clouds: the picked one was read",
      doc.clouds[1].calls and not doc.clouds[0].calls)

doc = FakeDocument()
recorder = run_script(doc, Recorder(spacing=None))
check("cancel spacing: nothing read", not doc.clouds[0].calls
      and not recorder.alerts)

doc = FakeDocument()
recorder = run_script(doc, Recorder(picks={u"type": None}))
check("cancel type: nothing read", not doc.clouds[0].calls)

doc = FakeDocument()
recorder = run_script(doc, Recorder(picks={u"level": None}))
check("cancel level: nothing read", not doc.clouds[0].calls)

doc = FakeDocument()
recorder = run_script(doc, Recorder(confirm=False))
check("decline: nothing built and no transaction",
      not doc.toposolids and FakeTransaction.log == [])

doc = FakeDocument(clouds=[])
recorder = run_script(doc)
check("no clouds: said so", u"no linked point clouds" in recorder.text())

doc = FakeDocument(types_=[])
recorder = run_script(doc)
check("no types: said so", u"no toposolid types" in recorder.text())

doc = FakeDocument(levels=[])
recorder = run_script(doc)
check("no levels: said so", u"no levels" in recorder.text(), recorder.alerts)

doc = FakeDocument()
recorder = run_script(doc, revit_2023=True)
check("Revit 2023: said so before anything else",
      u"2024" in recorder.text() and not doc.clouds[0].calls)


# --------------------------------------------------------------------------
# 10. Revit refusing
# --------------------------------------------------------------------------

doc = FakeDocument(clouds=[FakePointCloudInstance(u"ELG", raises=True)])
recorder = run_script(doc)
check("read fails: the user is told",
      u"would not read the point cloud" in recorder.text(), recorder.alerts)
check("read fails: nothing built", not doc.toposolids)

doc = FakeDocument(clouds=[FakePointCloudInstance(u"ELG", empty=True)])
recorder = run_script(doc)
check("empty read: the user is told",
      u"No usable points" in recorder.text(), recorder.alerts)

doc = FakeDocument(create_raises=True)
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
check("create fails: rolled back",
      FakeTransaction.log == ["start", "rollback"], FakeTransaction.log)
check("create fails: Revit's words reach the user",
      u"invalid sketch" in recorder.text(), recorder.alerts)

doc = FakeDocument(commit_status="RolledBack")
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
check("rejected commit: nothing kept", not doc.toposolids)
check("rejected commit: not reported as success",
      u"rejected" in recorder.text() and
      not any(u"Samples read" in line for line in recorder.printed))


# --------------------------------------------------------------------------
# 11. Revit's surface sags under the highest point, as it did live
# --------------------------------------------------------------------------

SAG = 0.065 * FEET
doc = FakeDocument(top_sag=SAG)
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
made = survivor(doc)
check("sag: a 65 mm sag is kept, not rolled back", made is not None,
      recorder.alerts)
check("sag: committed", FakeTransaction.log == ["start", "commit"],
      FakeTransaction.log)
check("sag: the report shows the worst gap as 65 mm",
      u"| Height difference, worst | 65 mm |" in recorder.printed,
      [line for line in recorder.printed if u"worst" in line])
check("sag: every point is matched to a vertex",
      made is not None and u"| Points kept as vertices | {0} of {0} |".format(
          len(made.given)) in recorder.printed)

doc = FakeDocument()
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
check("no sag: the worst gap is 0 mm",
      u"| Height difference, worst | 0 mm |" in recorder.printed)

doc = FakeDocument(vertices_raise=True)
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
check("vertices unreadable: still kept, and said so",
      survivor(doc) is not None and
      any(u"could not be read" in line for line in recorder.printed))


# --------------------------------------------------------------------------
# 12. Too thin for its type
# --------------------------------------------------------------------------

high_levels = [FakeLevel(u"DA11.ROOF", 100.0), FakeLevel(u"DA11.E01", 0.0)]
doc = FakeDocument(levels=high_levels)
recorder = run_script(doc, Recorder(picks={u"level": u"ROOF",
                                           u"type": u"Generic"}))
check("too thin: the confirmation warns",
      recorder.questions and u"too thin" in recorder.questions[0],
      recorder.questions[:1])
check("too thin: and names a level low enough",
      recorder.questions and u"at or below" in recorder.questions[0])
check("too thin: nothing kept", not doc.toposolids)
check("too thin: Revit's dialog never shown", not doc.dialogs, doc.dialogs)
check("too thin: the user is told why",
      any(u"too thin for its type" in alert for alert in recorder.alerts),
      recorder.alerts)
check("too thin: no success report",
      not any(u"Samples read" in line for line in recorder.printed))

doc = FakeDocument()
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
check("thick enough: no too thin warning",
      recorder.questions and u"too thin" not in recorder.questions[0])

# The advice must be right, not just present: build on the level it names.
doc = FakeDocument(levels=[FakeLevel(u"DA11.ROOF", 100.0)])
recorder = run_script(doc, Recorder(picks={u"type": u"Generic"}))
advised = None
if recorder.questions:
    tail = recorder.questions[0].split(u"at or below ")
    if len(tail) > 1:
        advised = float(tail[1].split(u" m")[0])
check("too thin: the advised level is the lowest point plus the thickness",
      advised is not None and abs(advised - (
          model.feet_to_metres(min(p[2] for p in
                                   doc.clouds[0].model_points(FEET / 2.0)
                                   )) + 1.0)) < 0.3,
      repr(advised))
if advised is not None:
    doc = FakeDocument(levels=[FakeLevel(u"ADVISED", advised - 0.01)])
    recorder = run_script(doc, Recorder(picks={u"type": u"Generic"}))
    check("too thin: a level just under the advice builds",
          survivor(doc) is not None, recorder.alerts)

thin = FakeDocument(levels=high_levels,
                    types_=[FakeToposolidType(u"Odd", structure=False)])
recorder = run_script(thin, Recorder(picks={u"level": u"ROOF"}))
check("thickness unreadable: no crash, no advice",
      recorder.questions and u"too thin" not in recorder.questions[0],
      recorder.alerts)


# --------------------------------------------------------------------------
# 13. The ground filter
# --------------------------------------------------------------------------

# Unit checks first.
low = model.LowGrid(0.0, 0.0, 1.0)
for z in (5.0, 1.0, 9.0, 2.0, 3.0):
    low.add(0.5, 0.5, z)
check("low grid: a cell yields its third lowest sample",
      low.points() == [(0.5, 0.5, 3.0)], repr(low.points()))
low = model.LowGrid(0.0, 0.0, 1.0)
low.add(0.5, 0.5, 7.0)
low.add(0.6, 0.5, 4.0)
check("low grid: a sparse cell yields its highest sample",
      low.points() == [(0.5, 0.5, 7.0)], repr(low.points()))
check("low grid: samples are counted", low.samples == 2)


def rough_cells(truck=True, hole=False):
    """Slope, a 2 m step, an irregular edge, and a 3 x 8 cell truck."""
    cells = {}
    for i in range(30):
        for j in range(20):
            if (i - 15) ** 2 + (j - 10) ** 2 > 140:
                continue
            if hole and 3 <= i <= 6 and 8 <= j <= 11:
                continue
            z = 0.3 * i + 0.2 * j + (2.0 if i >= 20 else 0.0)
            if truck and 10 <= i <= 12 and 5 <= j <= 12:
                z += 3.5
            cells[(i, j)] = (i + 0.5, j + 0.5, z)
    return cells


kept, removed = model.ground_filter(rough_cells(), 1.0, 5.0, 0.5)
check("filter: the truck goes, all 24 cells of it",
      sorted((int(p[0]), int(p[1])) for p in removed) ==
      [(i, j) for i in range(10, 13) for j in range(5, 13)],
      repr(sorted((int(p[0]), int(p[1])) for p in removed)))
kept, removed = model.ground_filter(rough_cells(truck=False), 1.0, 5.0, 0.5)
check("filter: a slope, a 2 m step and a ragged edge all stay",
      removed == [], repr(removed[:5]))
for flip_i, flip_j in ((True, False), (False, True), (True, True)):
    mirrored = dict(((29 - i if flip_i else i, 19 - j if flip_j else j),
                     (29 - i + 1.0 if flip_i else i + 0.5,
                      19 - j + 1.0 if flip_j else j + 0.5, point[2]))
                    for (i, j), point in rough_cells(truck=False).items())
    kept, removed = model.ground_filter(mirrored, 1.0, 5.0, 0.5)
    check(u"filter: the same site mirrored ({0}, {1}) loses no edge".format(
        flip_i, flip_j), removed == [], repr(removed[:3]))
kept, removed = model.ground_filter(rough_cells(truck=False, hole=True),
                                    1.0, 5.0, 0.5)
check("filter: the rim of a hole in the scan stays", removed == [],
      repr(removed[:5]))
kept, removed = model.ground_filter(rough_cells(), 1.0, 2.0, 0.5)
check("filter: a window narrower than the truck leaves its middle in",
      0 < len(removed) < 24, len(removed))
check("filter: the window is at least as wide as asked",
      (2 * model.window_cells(5.0, 1.0) + 1) * 1.0 >= 5.0 and
      model.window_cells(5.0, 1.0) == 3)
check("filter: a 30 cm bump stays, 70 cm goes",
      model.ground_filter({(0, 0): (0, 0, 0.3), (1, 0): (1, 0, 0.0),
                           (-1, 0): (-1, 0, 0.0)}, 1.0, 3.0, 0.5)[1] == []
      and len(model.ground_filter({(0, 0): (0, 0, 0.7), (1, 0): (1, 0, 0.0),
                                   (-1, 0): (-1, 0, 0.0)},
                                  1.0, 3.0, 0.5)[1]) == 1)
check("window: 4 m at 1 m spacing is fine",
      model.window_error(4.0, 1.0) == u"")
check("window: under the spacing is refused",
      model.window_error(1.0, 2.0) != u"")
check("window: 40 m is refused", model.window_error(40.0, 1.0) != u"")

# Then the script, on a rough site: a sloping plane with a 1.5 m lava
# step running across it, a truck, a shrub, and stray returns.
STEP_X = 520.0 * FEET


def lava(x, y):
    return terrain(x, y) + (1.5 * FEET if x >= STEP_X else 0.0)


# Two trucks parked side by side: 6 m by 8 m of roof.
TRUCK = (505.0, 315.0, 511.0, 323.0, 3.5, True)
SHRUB = (530.0, 310.0, 531.5, 311.5, 1.2, False)


def site(**kwargs):
    return FakePointCloudInstance(u"ELG_Scan", ground=lava,
                                  objects=[TRUCK, SHRUB], strays=True,
                                  **kwargs)


def on_lava(made, tolerance=1e-6):
    worst = 0.0
    for x, y, z in made.given:
        worst = max(worst, abs(z - lava(x, y)))
    return worst <= tolerance, worst


FILTER_PICK = {u"level": u"DA11.E01", u"scan": u"Ground filter"}

_probe = site()
_box = _probe.get_BoundingBox(None)
_strays = {}
for _x, _y, _z in _probe.model_points(FEET / 4.0):
    if _z < lava(_x, _y) - 0.4 * FEET:
        _key = (int(math.floor((_x - _box.Min.X) / FEET)),
                int(math.floor((_y - _box.Min.Y) / FEET)))
        _strays[_key] = _strays.get(_key, 0) + 1
check("fake: stray returns land in many cells, never more than two to one",
      len(_strays) > 50 and max(_strays.values()) <= 2,
      u"{0} cells, most {1}".format(len(_strays), max(_strays.values())))

doc = FakeDocument(clouds=[site()])
cloud = doc.clouds[0]
recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=u"10"))
made = survivor(doc)
check("site, filter: built", made is not None, recorder.alerts)
ok, worst = on_lava(made) if made else (False, None)
check("site, filter: every point is on the ground, none on the truck, "
      "the shrub or a stray", ok, repr(worst))
covered = safely(covers_cloud, made, cloud, FEET) if made else (False,)
check("site, filter: still reaches every side of the scan", covered[0],
      repr(covered))
upper = [p for p in made.given if STEP_X <= p[0] < STEP_X + FEET] \
    if made else []
check("site, filter: the top edge of the lava step is kept",
      len(upper) >= 10, len(upper))
check("site, filter: the cloud was read at a quarter of the spacing",
      cloud.calls and abs(cloud.calls[0][0] - FEET / 4.0) < 1e-9,
      repr(cloud.calls[:1]))
check("site, filter: the confirmation says what was dropped",
      recorder.questions and u"Ground filter:" in recorder.questions[0] and
      u"largest object 10.0 m" in recorder.questions[0],
      recorder.questions[:1])
check("site, filter: a reasonable share dropped, no warning",
      recorder.questions and u"more than a quarter" not in
      recorder.questions[0])
check("site, filter: the report names the method",
      any(u"| Method | ground filter" in line for line in recorder.printed))
check("site, filter: the prompt guides the size",
      any(u"trucks" in p and u"people" in p and u"lava ridge" in p
          for p in recorder.prompts))

saved_tile = model.TILE_CELLS
try:
    # Smaller tiles, so the tile size is visible on a small fake site.
    model.TILE_CELLS = 40
    doc = FakeDocument(clouds=[site()])
    recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=u"10"))
    box = doc.clouds[0].get_BoundingBox(None)
    side = model.tile_side(FEET, model.FILTER_SAMPLES_PER_SPACING)
    expected = len(model.tiles(box.Min.X, box.Min.Y, box.Max.X, box.Max.Y,
                               side))
    check("site, filter: tiles shrink with the denser reading, so a full "
          "tile holds what it would at the averaging density",
          len(doc.clouds[0].calls) == expected and expected > 4,
          u"{0} calls, {1} expected".format(len(doc.clouds[0].calls),
                                            expected))
finally:
    model.TILE_CELLS = saved_tile
check("tiles: a full tile stays under the cap at either density",
      all((model.tile_side(1.0, n) / model.average_distance(1.0, n)) ** 2
          < model.MAX_POINTS_PER_CALL
          for n in (model.SAMPLES_PER_SPACING,
                    model.FILTER_SAMPLES_PER_SPACING)))

doc = FakeDocument(clouds=[site()])
recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=u"2"))
made = survivor(doc)
on_roof = [p for p in made.given if p[2] - lava(p[0], p[1]) > 3.0 * FEET] \
    if made else []
check("site, 2 m window: the truck's middle stays, as documented",
      len(on_roof) > 0, len(on_roof))

doc = FakeDocument(clouds=[site()])
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
made = survivor(doc)
ok, worst = on_lava(made) if made else (True, None)
check("site, averaged: objects end up in the surface", not ok, repr(worst))
check("site, averaged: and the confirmation says so",
      recorder.questions and u"All points averaged" in recorder.questions[0])

saved_share = model.FILTER_WARN_SHARE
try:
    model.FILTER_WARN_SHARE = 0.0
    doc = FakeDocument(clouds=[site()])
    recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=u"10"))
    check("site, filter: dropping too much is warned about",
          recorder.questions and u"more than a quarter" in
          recorder.questions[0])
finally:
    model.FILTER_WARN_SHARE = saved_share

for bad in (u"0,2", u"50", u"abc"):
    doc = FakeDocument(clouds=[site()])
    recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=bad))
    check(u"window {0!r}: refused before reading".format(bad),
          not doc.clouds[0].calls and recorder.alerts, recorder.alerts)

doc = FakeDocument(clouds=[site()])
recorder = run_script(doc, Recorder(picks=FILTER_PICK, window=None))
check("cancel window: nothing read", not doc.clouds[0].calls)

doc = FakeDocument(clouds=[site()])
recorder = run_script(doc, Recorder(picks={u"scan": None}))
check("cancel method: nothing read", not doc.clouds[0].calls)


# --------------------------------------------------------------------------
# 14. An identity cloud
# --------------------------------------------------------------------------

flat = FakePointCloudInstance(u"ELG", returns="cloud",
                              transform=FakeTransform(0.0, (0.0, 0.0, 0.0)))
doc = FakeDocument(clouds=[flat])
recorder = run_script(doc, Recorder(picks={u"level": u"DA11.E01"}))
made = survivor(doc)
ok, worst = safely(on_terrain, made) if made else (False, None)
check("identity: points on the ground", ok, repr(worst))


# --------------------------------------------------------------------------

class _Probe(object):
    given = [(0.0, 0.0, Z0 + 0.01), (CLOUD_W, CLOUD_D, terrain(CLOUD_W, CLOUD_D))]


check("harness: the ground check catches a point a centimetre off",
      not on_terrain(_Probe)[0])
check("harness: the coverage check catches points short of the cloud",
      not covers_cloud(_Probe, FakePointCloudInstance(u"probe"), FEET)[0])
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
