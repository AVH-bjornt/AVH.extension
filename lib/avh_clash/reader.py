# -*- coding: utf-8 -*-
"""Revit elements -> solids -> triangles, and the exact clash test.

`DB` (Autodesk.Revit.DB) is passed in rather than imported, so the
harness can hand over a stand in.

Unverified until run in Revit, every one of them read from the API
documentation only:

    Element.get_Geometry with Options at Fine detail
    GeometryInstance.GetInstanceGeometry
    Face.Triangulate(level of detail), Mesh.Vertices / get_Triangle
    SolidUtils.CreateTransformed, for elements in a linked model
    BooleanOperationsUtils.ExecuteBooleanOperation(a, b, Intersect)
    RevitLinkInstance.GetLinkDocument / GetTotalTransform
    View3D.GetSectionBox and its Transform

The harness models all of these with boxes, which proves the wiring and
the arithmetic and nothing about Revit.
"""

from avh_clash import model
from avh_schedules.compat import to_text

# Categories that are Model categories to Revit and have nothing solid
# to clash: spaces, lines, the links themselves, reference geometry.
# Matched on the enum, never on the name, which follows the interface
# language. A name missing from this Revit version is skipped.
EXCLUDED_CATEGORIES = (
    "OST_Rooms", "OST_Areas", "OST_MEPSpaces", "OST_HVAC_Zones",
    "OST_Lines", "OST_RoomSeparationLines", "OST_AreaSchemeLines",
    "OST_MEPSpaceSeparationLines", "OST_RvtLinks", "OST_Cameras",
    "OST_IOSModelGroups", "OST_Assemblies", "OST_ProjectBasePoint",
    "OST_SharedBasePoint", "OST_VolumeOfInterest", "OST_SectionBox",
    "OST_Materials", "OST_ProjectInformation", "OST_CLines",
    "OST_PointClouds", "OST_CoordinationModel", "OST_ShaftOpening",
    "OST_SWallRectOpening", "OST_ArcWallRectOpening",
    "OST_FloorOpening", "OST_CeilingOpening", "OST_RoofOpening",
    "OST_StructuralFramingOpening", "OST_ColumnOpening",
    "OST_SitePropertyLineSegment", "OST_SiteProperty", "OST_Sheets",
    "OST_Views", "OST_Schedules", "OST_Viewers", "OST_Levels",
    "OST_Grids",
)

# Which discipline heading each category is listed under in the setup,
# matched on the enum, never on the name, for the same reason as above.
# In this order on the page; anything not here goes under "Other". A
# name missing from this Revit version is skipped.
DISCIPLINES = (
    (u"Architecture", (
        "OST_Ceilings", "OST_Columns", "OST_CurtainWallPanels",
        "OST_CurtainWallMullions", "OST_Doors", "OST_Floors",
        "OST_GenericModel", "OST_Ramps", "OST_Roofs", "OST_RoofSoffit",
        "OST_Fascia", "OST_Gutter", "OST_Stairs", "OST_StairsRuns",
        "OST_StairsLandings", "OST_StairsSupports", "OST_Walls",
        "OST_Cornices", "OST_Reveals", "OST_Windows", "OST_Mass",
        "OST_Parts")),
    (u"Interior", (
        "OST_Casework", "OST_Furniture", "OST_FurnitureSystems",
        "OST_StairsRailing", "OST_StairsRailingBaluster",
        "OST_RailingHandRail", "OST_RailingSupport", "OST_RailingTopRail",
        "OST_RailingTermination", "OST_SpecialityEquipment",
        "OST_FoodServiceEquipment", "OST_Signage", "OST_Entourage")),
    (u"Structure", (
        "OST_EdgeSlab", "OST_StructuralColumns", "OST_StructConnections",
        "OST_StructuralFoundation", "OST_StructuralFraming",
        "OST_StructuralTruss", "OST_StructuralStiffener", "OST_Rebar",
        "OST_FabricReinforcement", "OST_AreaRein", "OST_PathRein",
        "OST_Coupler", "OST_BridgeAbutments", "OST_BridgePiers",
        "OST_BridgeGirders", "OST_BridgeDecks")),
    (u"MEP - Mechanical", (
        "OST_DuctCurves", "OST_DuctFitting", "OST_DuctAccessory",
        "OST_DuctTerminal", "OST_FlexDuctCurves", "OST_DuctInsulations",
        "OST_DuctLinings", "OST_PlaceHolderDucts",
        "OST_MechanicalEquipment", "OST_MechanicalControlDevices",
        "OST_FabricationDuctwork", "OST_MEPAncillaryFraming")),
    (u"MEP - Plumbing", (
        "OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory",
        "OST_FlexPipeCurves", "OST_PipeInsulations", "OST_PlaceHolderPipes",
        "OST_PlumbingFixtures", "OST_PlumbingEquipment", "OST_Sprinklers",
        "OST_FabricationPipework")),
    (u"MEP - Electrical", (
        "OST_CableTray", "OST_CableTrayFitting", "OST_Conduit",
        "OST_ConduitFitting", "OST_ElectricalEquipment",
        "OST_ElectricalFixtures", "OST_LightingFixtures",
        "OST_LightingDevices", "OST_CommunicationDevices",
        "OST_DataDevices", "OST_FireAlarmDevices", "OST_SecurityDevices",
        "OST_NurseCallDevices", "OST_TelephoneDevices",
        "OST_AudioVisualDevices", "OST_FireProtection",
        "OST_FabricationContainment")),
    (u"Site", (
        "OST_Topography", "OST_Toposolid", "OST_Site", "OST_Parking",
        "OST_Planting", "OST_Roads", "OST_Hardscape")),
)
OTHER = u"Other"

# The categories that carry a clearance zone unless the page says
# otherwise: pipes and what is fitted to them.
CLEARANCE_DEFAULT = (
    "OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory",
    "OST_FlexPipeCurves", "OST_PipeInsulations")


def enum_ids(DB, names):
    """The category ids of `names` this Revit version has."""
    found = set()
    for name in names:
        member = getattr(DB.BuiltInCategory, name, None)
        if member is None:
            continue
        try:
            found.add(int(member))
        except BaseException:
            pass
    return found


def joined_ids(element):
    """Ids of the elements this one is built together with, which are
    never a clash with it, hard or soft: whatever its MEP connectors are
    connected to (a pipe and its fittings), and the host of insulation or
    lining. Without this every pipe joint is a soft clash.

    Unverified until run in Revit: ConnectorManager.Connectors,
    Connector.AllRefs and .Owner, InsulationLiningBase.HostElementId."""
    found = set()
    manager = getattr(element, "ConnectorManager", None)
    if manager is None:
        mep = getattr(element, "MEPModel", None)
        manager = getattr(mep, "ConnectorManager", None) \
            if mep is not None else None
    if manager is not None:
        try:
            connectors = list(manager.Connectors)
        except BaseException:
            connectors = []
        for connector in connectors:
            try:
                for other in connector.AllRefs:
                    owner = getattr(other, "Owner", None)
                    if owner is not None:
                        found.add(id_value(owner.Id))
            except BaseException:
                continue
    host = getattr(element, "HostElementId", None)
    if host is not None:
        try:
            found.add(id_value(host))
        except BaseException:
            pass
    try:
        found.discard(id_value(element.Id))
    except BaseException:
        pass
    return found


def discipline_ids(DB):
    """{category id: discipline heading} for the categories this Revit
    version has, and the headings in page order."""
    found = {}
    for label, names in DISCIPLINES:
        for name in names:
            member = getattr(DB.BuiltInCategory, name, None)
            if member is None:
                continue
            try:
                found.setdefault(int(member), label)
            except BaseException:
                pass
    return found, [label for label, _ in DISCIPLINES] + [OTHER]


# Face.Triangulate takes 0 (coarsest) to 1 (finest). Flat faces come out
# the same at any setting; this only decides how round a pipe looks.
DETAIL = {"low": 0.15, "medium": 0.5, "high": 0.9}

# An overlap smaller than this is the solids touching, with rounding.
# One cubic centimetre, in cubic feet.
MIN_CLASH_FT3 = 1e-6 / model.CUBIC_FEET_TO_M3


def id_value(element_id):
    """Revit 2024 replaced ElementId.IntegerValue with Value."""
    value = getattr(element_id, "Value", None)
    if value is None:
        value = element_id.IntegerValue
    return int(value)


def make_id(DB, value):
    """ElementId from a number. The Int32 constructor went in Revit 2026
    and IronPython will not pick the Int64 one for a small number."""
    try:
        from System import Int64
        return DB.ElementId(Int64(value))
    except BaseException:
        return DB.ElementId(int(value))


def excluded_ids(DB):
    found = set()
    for name in EXCLUDED_CATEGORIES:
        member = getattr(DB.BuiltInCategory, name, None)
        if member is None:
            continue
        try:
            found.add(int(member))
        except BaseException:
            pass
    return found


class Source(object):
    """Somewhere elements come from: the host model, one placed link, or
    the elements that were selected when the button was clicked."""

    def __init__(self, key, label, kind, doc, transform=None,
                 instance=None, element_ids=None):
        self.key = key
        self.label = label
        self.kind = kind                    # "host", "link", "selection"
        self.doc = doc
        self.transform = transform          # None for the host model
        self.instance = instance            # the RevitLinkInstance
        self.element_ids = element_ids      # selection only
        # Selected elements are host elements, and must key the same
        # way or one element could be tested against itself.
        self.prefix = key if kind == "link" else u"host"


class Item(object):
    """One element taking part in a run."""

    def __init__(self, key, source, element, category, box_feet):
        self.key = key
        self.source = source
        self.element = element
        self.category = category
        self.box_feet = box_feet
        self.box = None                     # metres from the origin
        self.sets = 0                       # 1 = A, 2 = B, 3 = both
        self.cat_id = None
        self.clear = 0.0                    # clearance zone, metres
        self.joined = None                  # joined_ids, read when needed
        self.geometry = None
        self.sent = False


class Geometry(object):
    """An element's triangles, and its solids for the exact test."""

    def __init__(self):
        self.pos = model.new_floats()
        self.idx = model.new_indices()
        # (solid or None, box). None means the part can be drawn and
        # triangle tested but not intersected as a solid: a mesh, an
        # open shell, or a linked solid that would not transform.
        self.parts = []
        self.box = None


def link_sources(DB, doc):
    """A Source for every loaded link instance in the host model."""
    found = []
    try:
        instances = list(DB.FilteredElementCollector(doc)
                         .OfClass(DB.RevitLinkInstance))
    except BaseException:
        return found
    titles = {}
    loaded = []
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except BaseException:
            link_doc = None
        if link_doc is None:
            continue                        # unloaded
        title = to_text(link_doc.Title)
        titles[title] = titles.get(title, 0) + 1
        loaded.append((instance, link_doc, title))
    seen = {}
    for instance, link_doc, title in loaded:
        label = title
        if titles[title] > 1:
            seen[title] = seen.get(title, 0) + 1
            label = u"{0} ({1})".format(title, seen[title])
        try:
            transform = instance.GetTotalTransform()
            if getattr(transform, "IsIdentity", False):
                transform = None
        except BaseException:
            transform = None
        key = u"link:{0}".format(id_value(instance.Id))
        found.append(Source(key, label, u"link", link_doc,
                            transform=transform, instance=instance))
    return found


def qualifies(DB, element, excluded):
    """The element's category when it is something that can clash:
    a Model category, not excluded, not drawn in one view only.
    Otherwise None."""
    try:
        category = element.Category
        if category is None:
            return None
        if category.CategoryType != DB.CategoryType.Model:
            return None
        if id_value(category.Id) in excluded:
            return None
        if getattr(element, "ViewSpecific", False):
            return None
        return category
    except BaseException:
        return None


def model_elements(DB, doc, excluded, view_id=None):
    """Every element that qualifies, as (element, category). `view_id`
    limits it to what that view shows."""
    if view_id is not None:
        collector = DB.FilteredElementCollector(doc, view_id)
    else:
        collector = DB.FilteredElementCollector(doc)
    for element in collector.WhereElementIsNotElementType():
        category = qualifies(DB, element, excluded)
        if category is not None:
            yield element, category


def element_box(DB, element, transform):
    """The element's box in host feet, or None when it has no extent."""
    try:
        box = element.get_BoundingBox(None)
    except BaseException:
        box = None
    if box is None:
        return None
    low, high = box.Min, box.Max
    if transform is None:
        return (low.X, low.Y, low.Z, high.X, high.Y, high.Z)
    return transformed_box(DB, (low.X, low.Y, low.Z,
                                high.X, high.Y, high.Z), transform)


def transformed_box(DB, box, transform):
    """The axis aligned box around a box after a transform. A rotated
    box grows, which only ever lets more through to the real test."""
    xs, ys, zs = [], [], []
    for x in (box[0], box[3]):
        for y in (box[1], box[4]):
            for z in (box[2], box[5]):
                point = transform.OfPoint(DB.XYZ(x, y, z))
                xs.append(point.X)
                ys.append(point.Y)
                zs.append(point.Z)
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def section_box_feet(DB, view):
    """The active section box of a 3D view in host feet, or None."""
    try:
        if not isinstance(view, DB.View3D):
            return None
        if not view.IsSectionBoxActive:
            return None
        box = view.GetSectionBox()
        raw = (box.Min.X, box.Min.Y, box.Min.Z,
               box.Max.X, box.Max.Y, box.Max.Z)
        transform = getattr(box, "Transform", None)
        if transform is None or getattr(transform, "IsIdentity", False):
            return raw
        return transformed_box(DB, raw, transform)
    except BaseException:
        return None


class Reader(object):
    """Reads geometry for one run. `origin` is in feet; everything it
    produces is in metres from there."""

    def __init__(self, DB, origin, detail=u"medium"):
        self.DB = DB
        self.origin = origin
        self.lod = DETAIL.get(detail, DETAIL["medium"])
        self.boolean_failures = 0
        self.unreadable = 0

    # -- element -> triangles ------------------------------------------

    def geometry(self, item):
        if item.geometry is None:
            try:
                item.geometry = self._read(item)
            except BaseException:
                self.unreadable += 1
                item.geometry = Geometry()
        return item.geometry

    def _options(self):
        DB = self.DB
        options = DB.Options()
        try:
            options.DetailLevel = DB.ViewDetailLevel.Fine
        except BaseException:
            pass
        return options

    def _read(self, item):
        DB = self.DB
        geometry = Geometry()
        source = item.element.get_Geometry(self._options())
        if source is None:
            return geometry
        solids, meshes = [], []
        self._walk(source, solids, meshes, 0)
        transform = item.source.transform

        for solid in solids:
            point_transform = None
            if transform is not None:
                try:
                    solid = DB.SolidUtils.CreateTransformed(solid, transform)
                except BaseException:
                    point_transform = transform
            start = len(geometry.pos)
            self.tessellate(solid, point_transform, geometry)
            box = model.bounds(geometry.pos, start)
            if box is None:
                continue
            try:
                closed = solid.Volume > MIN_CLASH_FT3
            except BaseException:
                closed = False
            usable = closed and point_transform is None
            geometry.parts.append((solid if usable else None, box))
            geometry.box = model.union(geometry.box, box)

        for mesh in meshes:
            start = len(geometry.pos)
            self._append(mesh, transform, geometry)
            box = model.bounds(geometry.pos, start)
            if box is None:
                continue
            geometry.parts.append((None, box))
            geometry.box = model.union(geometry.box, box)
        return geometry

    def _walk(self, source, solids, meshes, depth):
        DB = self.DB
        for part in source:
            if part is None:
                continue
            if isinstance(part, DB.Solid):
                try:
                    if part.Faces.Size > 0:
                        solids.append(part)
                except BaseException:
                    pass
            elif isinstance(part, DB.GeometryInstance):
                if depth < 8:
                    try:
                        inner = part.GetInstanceGeometry()
                    except BaseException:
                        inner = None
                    if inner is not None:
                        self._walk(inner, solids, meshes, depth + 1)
            elif isinstance(part, DB.Mesh):
                meshes.append(part)

    def tessellate(self, solid, transform, geometry):
        """Solid -> triangles, face by face, appended to `geometry`."""
        for face in solid.Faces:
            mesh = None
            try:
                mesh = face.Triangulate(self.lod)
            except BaseException:
                try:
                    mesh = face.Triangulate()
                except BaseException:
                    mesh = None
            if mesh is not None:
                self._append(mesh, transform, geometry)

    def _append(self, mesh, transform, geometry):
        pos = geometry.pos
        idx = geometry.idx
        base = len(pos) // 3
        ox, oy, oz = self.origin
        f = model.FEET_TO_M
        for point in mesh.Vertices:
            if transform is not None:
                point = transform.OfPoint(point)
            pos.append((point.X - ox) * f)
            pos.append((point.Y - oy) * f)
            pos.append((point.Z - oz) * f)
        for n in range(mesh.NumTriangles):
            triangle = mesh.get_Triangle(n)
            idx.append(base + int(triangle.get_Index(0)))
            idx.append(base + int(triangle.get_Index(1)))
            idx.append(base + int(triangle.get_Index(2)))

    # -- clearance -----------------------------------------------------

    def related(self, a, b):
        """True when a and b are built together (see joined_ids), which
        can only be within one model."""
        if a.source.prefix != b.source.prefix:
            return False
        for item in (a, b):
            if item.joined is None:
                try:
                    item.joined = joined_ids(item.element)
                except BaseException:
                    item.joined = set()
        return b.element_id in a.joined or a.element_id in b.joined

    def gap(self, a, b, limit):
        """How far apart the surfaces of a and b are, when under `limit`
        metres: model.mesh_distance's answer, or None."""
        ga = self.geometry(a)
        gb = self.geometry(b)
        if not len(ga.idx) or not len(gb.idx) or ga.box is None or \
                gb.box is None:
            return None
        return model.mesh_distance(ga.pos, ga.idx, ga.box,
                                   gb.pos, gb.idx, gb.box, limit)

    # -- the test ------------------------------------------------------

    def test(self, a, b):
        """Do items a and b clash? None, or a dict:

            exact    True from a solid intersection, False from triangles
            volume   cubic metres, or None when not exact
            depth    metres: the thinnest dimension of the overlap's
                     box. None when not exact.
            box, center   where, in metres from the origin
            pos, idx      the overlap itself as a mesh, when exact
            partial  True when the triangle search stopped at a limit
        """
        DB = self.DB
        ga = self.geometry(a)
        gb = self.geometry(b)
        if not ga.parts or not gb.parts:
            return None

        volume = 0.0
        pieces = []
        unresolved = False
        for solid_a, box_a in ga.parts:
            for solid_b, box_b in gb.parts:
                if not model.boxes_overlap(box_a, box_b):
                    continue
                if solid_a is None or solid_b is None:
                    unresolved = True
                    continue
                try:
                    piece = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid_a, solid_b, DB.BooleanOperationsType.Intersect)
                    size = piece.Volume if piece is not None else 0.0
                except BaseException:
                    self.boolean_failures += 1
                    unresolved = True
                    continue
                if size > MIN_CLASH_FT3:
                    volume += size
                    pieces.append(piece)

        if pieces:
            overlap = Geometry()
            for piece in pieces:
                self.tessellate(piece, None, overlap)
            box = model.bounds(overlap.pos)
            if box is None:
                box = model.intersection(ga.box, gb.box)
            if box is None:
                return None
            return {"kind": u"hard", "exact": True,
                    "volume": volume * model.CUBIC_FEET_TO_M3,
                    "depth": min(model.extents(box)),
                    "box": box, "center": model.center(box),
                    "pos": overlap.pos, "idx": overlap.idx,
                    "partial": False}

        if not unresolved:
            return None
        found = model.mesh_clash(ga.pos, ga.idx, ga.box,
                                 gb.pos, gb.idx, gb.box)
        if found is None:
            return None
        return {"kind": u"hard", "exact": False, "volume": None,
                "depth": None,
                "box": found["box"], "center": found["center"],
                "pos": None, "idx": None,
                "partial": not found["complete"]}
