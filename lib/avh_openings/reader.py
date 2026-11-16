# -*- coding: utf-8 -*-
"""Revit elements -> hosts, pipes, and where one passes through the other.

`DB` (Autodesk.Revit.DB) is passed in rather than imported, so the
harness can hand over a stand in.

## How a crossing is found

Not by bounding boxes and not by Revit's intersection filters, which
only work inside one document and the pipes are usually in a link. The
pipe's centre line is put through the host's actual solid with
`Solid.IntersectWithCurve`, which returns the pieces of the line that
lie inside. Their end points are where the axis meets the host's faces,
which is exactly what `model.size_opening` needs.

A useful side effect: once a hole has been cut, the axis runs through
the hole and no longer meets the solid. So a pipe that already has an
opening is not offered a second one, and running the tool twice on the
same wall does nothing the second time.

A pipe that grazes a host without its centre line crossing it is not
found. That is a clash, not a penetration, and the Clash Detector is
the tool for it.

## Unverified until run in Revit

Read from the API documentation only:

    Element.get_Geometry, GeometryInstance.GetInstanceGeometry
    Solid.IntersectWithCurve with SolidCurveIntersectionOptions,
        SegmentCount and GetCurveSegment
    BuiltInParameter.RBS_PIPE_OUTER_DIAMETER,
        RBS_CONDUIT_OUTER_DIAM_PARAM, RBS_REFERENCE_INSULATION_THICKNESS
    RevitLinkInstance.GetLinkDocument / GetTotalTransform,
        Transform.OfPoint
"""

from avh_openings import model
from avh_schedules.compat import to_text


def id_value(element_id):
    """Revit 2024 replaced ElementId.IntegerValue with Value."""
    value = getattr(element_id, "Value", None)
    if value is None:
        value = element_id.IntegerValue
    return int(value)


def xyz(point):
    return (point.X, point.Y, point.Z)


# --------------------------------------------------------------------------
# Hosts
# --------------------------------------------------------------------------

class Host(object):
    def __init__(self, element, kind, frame, solids, box):
        self.element = element
        self.kind = kind
        self.frame = frame
        self.solids = solids
        self.box = box

    @property
    def id(self):
        return id_value(self.element.Id)


def host_kind(DB, element):
    """(kind, reason). kind is None for something that is not a wall,
    floor or roof, or one that cannot take an opening this way.

    Matched on the class, never the category name, which follows the
    interface language."""
    if isinstance(element, DB.Wall):
        try:
            kind = element.WallType.Kind
            if kind == DB.WallKind.Curtain:
                return None, u"a curtain wall, which takes panels, not " \
                             u"openings"
        except BaseException:
            pass
        return model.WALL, u""
    if isinstance(element, DB.Floor):
        return model.FLOOR, u""
    if isinstance(element, DB.RoofBase):
        return model.ROOF, u""
    return None, u""


def host_frame(DB, element, kind):
    """(frame, reason)."""
    if kind != model.WALL:
        return model.slab_frame(), u""
    try:
        curve = element.Location.Curve
    except BaseException:
        curve = None
    if curve is None or not isinstance(curve, DB.Line):
        return None, u"a curved wall, which this tool cannot open yet"
    frame = model.wall_frame(xyz(curve.Direction))
    if frame is None:
        return None, u"its location line has no direction"
    return frame, u""


def solids_of(DB, element):
    """Every solid with volume in the element's geometry."""
    found = []
    try:
        options = DB.Options()
        options.ComputeReferences = False
        options.DetailLevel = DB.ViewDetailLevel.Fine
        geometry = element.get_Geometry(options)
    except BaseException:
        return found
    if geometry is None:
        return found

    def walk(items):
        for item in items:
            if isinstance(item, DB.Solid):
                try:
                    if item.Volume > 0:
                        found.append(item)
                except BaseException:
                    continue
            elif isinstance(item, DB.GeometryInstance):
                try:
                    walk(item.GetInstanceGeometry())
                except BaseException:
                    continue
    walk(geometry)
    return found


def box_of_element(DB, element, transform=None):
    """(low, high) in the host model's coordinates, or None."""
    try:
        box = element.get_BoundingBox(None)
    except BaseException:
        box = None
    if box is None:
        return None
    corners = model.box_corners(xyz(box.Min), xyz(box.Max))
    if transform is not None:
        try:
            corners = [xyz(transform.OfPoint(DB.XYZ(*corner)))
                       for corner in corners]
        except BaseException:
            return None
    return model.box_of(corners)


def read_host(DB, element):
    """(Host, reason). Host is None, with the reason, when it cannot be
    opened by this tool."""
    kind, reason = host_kind(DB, element)
    if kind is None:
        return None, reason
    frame, reason = host_frame(DB, element, kind)
    if frame is None:
        return None, reason
    solids = solids_of(DB, element)
    if not solids:
        return None, u"Revit returned no solid geometry for it"
    box = box_of_element(DB, element)
    return Host(element, kind, frame, solids, box), u""


# --------------------------------------------------------------------------
# Pipes and conduits
# --------------------------------------------------------------------------

class Pipe(object):
    """A straight pipe or conduit, in the host model's coordinates."""

    def __init__(self, element, source, start, end, diameter, box,
                 kind):
        self.element = element
        self.source = source          # u"" for this model, else the link
        self.start = start
        self.end = end
        self.diameter = diameter      # outer, insulation included, feet
        self.box = box
        self.kind = kind              # u"Pipe" or u"Conduit"

    @property
    def id(self):
        return id_value(self.element.Id)


def _double(DB, element, name):
    member = getattr(DB.BuiltInParameter, name, None)
    if member is None:
        return None
    try:
        parameter = element.get_Parameter(member)
        if parameter is None or not parameter.HasValue:
            return None
        return parameter.AsDouble()
    except BaseException:
        return None


def pipe_diameter(DB, element, kind):
    """Outer diameter in feet, insulation included, or 0 when unknown."""
    if kind == u"Conduit":
        outer = _double(DB, element, "RBS_CONDUIT_OUTER_DIAM_PARAM")
    else:
        outer = _double(DB, element, "RBS_PIPE_OUTER_DIAMETER")
    if not outer:
        try:
            outer = float(element.Diameter)
        except BaseException:
            outer = 0.0
    insulation = 0.0
    if kind != u"Conduit":
        insulation = _double(DB, element,
                             "RBS_REFERENCE_INSULATION_THICKNESS") or 0.0
    return outer + 2.0 * insulation


CATEGORIES = (("OST_PipeCurves", u"Pipe"), ("OST_Conduit", u"Conduit"))


def read_pipes(DB, doc, source=u"", transform=None):
    """Every straight pipe and conduit in `doc`, moved by `transform`
    into the host model when `doc` is a link."""
    found = []
    for name, kind in CATEGORIES:
        member = getattr(DB.BuiltInCategory, name, None)
        if member is None:
            continue
        try:
            elements = list(DB.FilteredElementCollector(doc)
                            .OfCategory(member)
                            .WhereElementIsNotElementType())
        except BaseException:
            continue
        for element in elements:
            try:
                curve = element.Location.Curve
            except BaseException:
                continue
            if curve is None or not isinstance(curve, DB.Line):
                continue
            try:
                start = curve.GetEndPoint(0)
                end = curve.GetEndPoint(1)
                if transform is not None:
                    start = transform.OfPoint(start)
                    end = transform.OfPoint(end)
            except BaseException:
                continue
            diameter = pipe_diameter(DB, element, kind)
            box = box_of_element(DB, element, transform)
            found.append(Pipe(element, source, xyz(start), xyz(end),
                              diameter, box, kind))
    return found


def link_pipes(DB, doc):
    """Pipes and conduits from every loaded link, and the titles of any
    links that are not loaded, which the report names so nobody assumes
    those were looked at."""
    found = []
    unloaded = []
    try:
        instances = list(DB.FilteredElementCollector(doc)
                         .OfClass(DB.RevitLinkInstance))
    except BaseException:
        return found, unloaded
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except BaseException:
            link_doc = None
        if link_doc is None:
            unloaded.append(to_text(getattr(instance, "Name", u"")))
            continue
        try:
            transform = instance.GetTotalTransform()
        except BaseException:
            transform = None
        found.extend(read_pipes(DB, link_doc, to_text(link_doc.Title),
                                transform))
    return found, unloaded


# --------------------------------------------------------------------------
# Crossings
# --------------------------------------------------------------------------

def axis_segments(DB, solids, start, end):
    """[(entry, exit)] where the line from start to end is inside the
    solids, joined where they touch."""
    try:
        line = DB.Line.CreateBound(DB.XYZ(*start), DB.XYZ(*end))
    except BaseException:
        return []
    pieces = []
    for solid in solids:
        try:
            result = solid.IntersectWithCurve(
                line, DB.SolidCurveIntersectionOptions())
            for index in range(result.SegmentCount):
                segment = result.GetCurveSegment(index)
                pieces.append((xyz(segment.GetEndPoint(0)),
                               xyz(segment.GetEndPoint(1))))
        except BaseException:
            continue
    return model.join_segments(pieces)


def crossings(DB, host, pipes):
    """[(pipe, entry, exit)] for every pipe whose axis passes through
    the host. A pipe that crosses the same host twice gives two."""
    found = []
    for pipe in pipes:
        if pipe.diameter <= 0:
            continue
        if host.box is not None and pipe.box is not None and \
                not model.boxes_touch(host.box, pipe.box):
            continue
        for entry, exit in axis_segments(DB, host.solids, pipe.start,
                                         pipe.end):
            found.append((pipe, entry, exit))
    return found
