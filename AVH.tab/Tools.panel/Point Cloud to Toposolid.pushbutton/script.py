# -*- coding: utf-8 -*-
"""Build a toposolid from a linked point cloud.

Pick a point cloud, a grid spacing, a toposolid type and a level. The
cloud is read in tiles, every sample is averaged into a square grid at
that spacing, and one point per occupied cell goes into
`Toposolid.Create`, bounded by the rectangle through the outermost
points.

## Average, or filter for ground

Revit hands a point back as X, Y, Z and a colour. The ground, vegetation
and building classes a survey carries are not exposed, so the tool
cannot be told which points are ground. Each run asks how to treat the
cloud:

- **Average all points**, for a cloud already cleaned down to ground.
- **Ground filter**, for a cloud that still holds people, vehicles and
  plants. Each cell keeps its third lowest point, then a cell standing
  more than 0.5 m above the ground around it, within a window the user
  sizes to the largest object, is dropped and left for Revit to fill.
  See `avh_topo.model.ground_filter` for what that gets wrong: a real
  lump of ground narrower than the window is flattened too.

A dedicated classifier (CloudCompare's CSF, or the surveyor's software)
does this better. The filter is for when that has not been done.

## Two things are measured on every run, not assumed

The API documentation does not say which coordinate system `GetPoints`
returns its points in, nor whether a toposolid point's Z is absolute or
relative to the level. Either one guessed wrong builds a toposolid in a
plausible wrong place. So:

1. The first tile read is tested against the cloud's bounding box in
   the model, both as returned and put through the cloud's transform,
   and the reading that fits is used for the rest. If neither fits the
   run stops and prints both.
2. After the toposolid is built its top is compared with the highest
   point given. If it sits one level elevation too high, the points were
   read as relative: it is rebuilt with that subtracted and checked
   again. A disagreement over half a metre rolls everything back; that
   check is for errors of metres. How closely Revit's own vertices follow
   the cloud is printed afterwards, point by point.

## Too thin for its type

Revit refuses a toposolid whose points go below its flat bottom, taken
here as the level less the type's thickness (a reading of one live run,
not a documented rule). The confirmation warns when the lowest point is
under it and says which level would do, and the refusal is caught and
explained instead of ending in Revit's error dialog.

## Unverified

Not yet run in Revit: `PointCloudInstance.GetPoints` through a
`CreateMultiPlaneFilter`, `GetTotalTransform` on a point cloud,
`Toposolid.Create` with points, whether the outline belongs at the
level's elevation (the documentation only asks for horizontal loops),
and whether a toposolid's bounding box top is its highest point.
"""

__title__ = "Point Cloud\nto Toposolid"
__author__ = "AVH"
__doc__ = "Build a toposolid from a linked point cloud, thinned to a grid spacing you choose. Expects a cloud cleaned down to ground."

import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_topo import model                        # noqa: E402
from avh_coords.model import (                    # noqa: E402
    parse_number, ParseError, format_metres)
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Point Cloud to Toposolid"


class Abort(Exception):
    """Stop the run with a message for the user. Nothing is kept."""


def dotnet_list(item_type, items):
    from System.Collections.Generic import List
    collection = List[item_type]()
    for item in items:
        collection.Add(item)
    return collection


def element_label(element):
    try:
        name = to_text(element.Name)
        if name:
            return name
    except BaseException:
        pass
    try:
        parameter = element.get_Parameter(
            DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if parameter is not None:
            return to_text(parameter.AsString())
    except BaseException as exc:
        logger.debug(to_text(exc))
    return u"(unnamed)"


def element_id_text(element):
    try:
        element_id = element.Id
        value = getattr(element_id, "Value", None)
        if value is None:
            value = element_id.IntegerValue
        return to_text(value)
    except BaseException:
        return u"?"


def collect(doc, cls):
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(cls))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return []


def pick_one(items, label_of, title, button):
    """One item from a single select list, or None when cancelled.

    Strings go in and are mapped back, so this does not depend on how
    SelectFromList hands back objects.
    """
    by_label = {}
    for item in items:
        label = label_of(item)
        if label in by_label:
            label = u"{0}   [id {1}]".format(label, element_id_text(item))
        by_label[label] = item
    if len(by_label) == 1:
        return list(by_label.values())[0]
    chosen = forms.SelectFromList.show(
        sorted(by_label.keys()), multiselect=False, title=title,
        button_name=button)
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    return by_label.get(to_text(chosen))


def level_label(level):
    return u"{0}   ({1} m)".format(
        element_label(level),
        format_metres(model.feet_to_metres(level.Elevation)))


def pick_level(doc):
    levels = sorted(collect(doc, DB.Level), key=lambda lv: lv.Elevation)
    if not levels:
        raise Abort(u"This model has no levels to put a toposolid on.")
    by_label = []
    for level in levels:
        by_label.append((level_label(level), level))
    chosen = forms.SelectFromList.show(
        [label for label, _level in by_label], multiselect=False,
        title=TITLE + u": which level?", button_name=u"Use this level")
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    for label, level in by_label:
        if label == to_text(chosen):
            return level
    return None


def ask_spacing():
    """The spacing in feet, or None when cancelled."""
    answer = forms.ask_for_string(
        default=u"1",
        prompt=u"Point spacing in metres. One toposolid point per square "
               u"of this size. Larger is lighter, smaller follows the "
               u"ground more closely.",
        title=TITLE)
    if answer is None:
        return None
    try:
        metres = parse_number(answer, u"The spacing")
    except ParseError as exc:
        raise Abort(to_text(exc))
    error = model.spacing_error(metres)
    if error:
        raise Abort(error)
    return model.metres_to_feet(metres)


AVERAGE = u"Average all points: the cloud is already cleaned down to ground"
FILTER = (u"Ground filter: the cloud still has people, vehicles, plants "
          u"or the like")

WINDOW_PROMPT = (
    u"Largest object to remove, in metres.\n\n"
    u"Each point is compared with the ground around it, within a square "
    u"this wide, and dropped if it stands more than 0.5 m above it. "
    u"Revit fills the gap from the ground around.\n\n"
    u"Guide:\n"
    u"   2  people, shrubs, poles, signs\n"
    u"   4  cars, vans, small machines\n"
    u"  10  trucks, containers, site huts\n\n"
    u"Bigger is not safer. Real ground narrower than this, such as a lava "
    u"ridge, a boulder or the crest of a bank, is flattened too. Use the "
    u"smallest size that covers the objects. Anything wider, such as a "
    u"building, stays in.")


def ask_method():
    """True for the ground filter, False for averaging, None if cancelled."""
    chosen = forms.SelectFromList.show(
        [AVERAGE, FILTER], multiselect=False,
        title=TITLE + u": what is in the scan?",
        button_name=u"Use this")
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    chosen = to_text(chosen)
    if chosen == FILTER:
        return True
    if chosen == AVERAGE:
        return False
    return None


def ask_window(spacing_ft):
    """The filter window in feet, or None when cancelled."""
    answer = forms.ask_for_string(
        default=u"{0:g}".format(model.WINDOW_DEFAULT_M),
        prompt=WINDOW_PROMPT, title=TITLE)
    if answer is None:
        return None
    try:
        metres = parse_number(answer, u"The object size")
    except ParseError as exc:
        raise Abort(to_text(exc))
    error = model.window_error(metres, model.feet_to_metres(spacing_ft))
    if error:
        raise Abort(error)
    return model.metres_to_feet(metres)


def transform_of(cloud):
    transform = None
    for name in ("GetTotalTransform", "GetTransform"):
        try:
            transform = getattr(cloud, name)()
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        if transform is not None:
            break
    if transform is None:
        raise Abort(u"The point cloud's position in the model could not "
                    u"be read.")

    def point(xyz):
        return (xyz.X, xyz.Y, xyz.Z)
    return (point(transform.Origin), point(transform.BasisX),
            point(transform.BasisY), point(transform.BasisZ))


def box_of(cloud):
    try:
        box = cloud.get_BoundingBox(None)
    except BaseException as exc:
        logger.debug(to_text(exc))
        box = None
    if box is None:
        raise Abort(u"The point cloud has no extent in the model. Is it "
                    u"loaded?")
    return ((box.Min.X, box.Min.Y, box.Min.Z),
            (box.Max.X, box.Max.Y, box.Max.Z))


def read_tile(cloud, tile, distance):
    """Raw samples inside a tile, as (x, y, z) tuples, untransformed."""
    planes = []
    for normal, origin in model.tile_planes(tile):
        planes.append(DB.Plane.CreateByNormalAndOrigin(
            DB.XYZ(*normal), DB.XYZ(*origin)))
    factory = DB.PointClouds.PointCloudFilterFactory
    cloud_filter = factory.CreateMultiPlaneFilter(
        dotnet_list(DB.Plane, planes))
    collection = cloud.GetPoints(
        cloud_filter, distance, model.MAX_POINTS_PER_CALL)
    found = []
    if collection is None:
        return found
    for sample in collection:
        found.append((sample.X, sample.Y, sample.Z))
    return found


class Reading(object):
    def __init__(self, grid, transform, box):
        self.grid = grid
        self.transform = transform
        self.box = box
        self.frame = None
        self.shares = (None, None)
        self.tiles_read = 0
        self.capped = 0
        self.first_raw = []


def feed(reading, raw):
    if not raw:
        return
    if reading.frame is None:
        frame, raw_share, moved_share = model.pick_frame(
            raw[:5000], reading.transform, reading.box[0], reading.box[1])
        reading.shares = (raw_share, moved_share)
        if frame is None:
            reading.first_raw = raw[:5]
            problem = Abort(u"The points read back fit the cloud's place "
                            u"in the model neither as returned nor through "
                            u"the cloud's transform, so the tool cannot "
                            u"tell where they are. Nothing was built. The "
                            u"numbers are in the output window.")
            problem.reading = reading
            raise problem
        reading.frame = frame
    add = reading.grid.add
    if reading.frame == model.TRANSFORMED:
        transform = reading.transform
        for x, y, z in raw:
            px, py, pz = model.apply_transform(transform, x, y, z)
            add(px, py, pz)
    else:
        for x, y, z in raw:
            add(x, y, z)


def read_cloud(cloud, spacing_ft, filtering=False):
    transform = transform_of(cloud)
    box = box_of(cloud)
    if filtering:
        samples = model.FILTER_SAMPLES_PER_SPACING
        grid = model.LowGrid(box[0][0], box[0][1], spacing_ft)
    else:
        samples = model.SAMPLES_PER_SPACING
        grid = model.Grid(box[0][0], box[0][1], spacing_ft)
    reading = Reading(grid, transform, box)
    distance = model.average_distance(spacing_ft, samples)

    def visit(tile, depth):
        try:
            raw = read_tile(cloud, tile, distance)
        except Abort:
            raise
        except BaseException as exc:
            raise Abort(u"Revit would not read the point cloud, asked "
                        u"for at most {0} points: {1}".format(
                            model.MAX_POINTS_PER_CALL, to_text(exc)))
        reading.tiles_read += 1
        if len(raw) >= model.MAX_POINTS_PER_CALL:
            if depth < model.MAX_SPLITS:
                for quarter in model.quarters(tile):
                    visit(quarter, depth + 1)
                return
            reading.capped += 1
        feed(reading, raw)

    side = model.tile_side(spacing_ft, samples)
    for tile in model.tiles(box[0][0], box[0][1], box[1][0], box[1][1],
                            side):
        visit(tile, 0)
    return reading


def print_frame_problem(reading):
    output.print_md(u"#### Where the points came back")
    output.print_md(u"```")
    output.print_md(u"cloud box min  {0}".format(repr(reading.box[0])))
    output.print_md(u"cloud box max  {0}".format(repr(reading.box[1])))
    output.print_md(u"transform      {0}".format(repr(reading.transform)))
    output.print_md(u"share inside, as returned      {0}".format(
        repr(reading.shares[0])))
    output.print_md(u"share inside, transformed      {0}".format(
        repr(reading.shares[1])))
    for sample in reading.first_raw:
        output.print_md(u"sample         {0}".format(repr(sample)))
    output.print_md(u"```")


def type_thickness(topo_type):
    """The type's total thickness in feet, or None when unreadable."""
    try:
        structure = topo_type.GetCompoundStructure()
        if structure is None:
            return None
        return structure.GetWidth()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def too_thin_advice(lowest, level, thickness):
    """Why this level is too high for the ground, or u"" when it is not."""
    if thickness is None:
        return u""
    under = model.below_bottom(lowest, level.Elevation, thickness)
    if under <= 0.0:
        return u""
    return (u"The lowest point is {0} m under the bottom of this toposolid "
            u"({1} m: the level less the type's {2} m thickness). Revit "
            u"refuses that as too thin for its type. Pick a level at or "
            u"below {3} m.".format(
                format_metres(model.feet_to_metres(under), 2),
                format_metres(model.feet_to_metres(
                    level.Elevation - thickness), 2),
                format_metres(model.feet_to_metres(thickness), 2),
                format_metres(model.feet_to_metres(lowest + thickness), 2)))


class Filtering(object):
    """What the ground filter did, for the confirmation and the report."""

    def __init__(self, window_ft, cells, removed):
        self.window_ft = window_ft
        self.cells = cells
        self.removed = removed

    def share(self):
        return float(self.removed) / self.cells if self.cells else 0.0

    def line(self):
        return (u"Ground filter: {0} of {1} squares dropped as objects "
                u"(largest object {2} m, more than 0.5 m above the ground "
                u"around). Revit fills them in.".format(
                    self.removed, self.cells,
                    format_metres(model.feet_to_metres(self.window_ft), 1)))


def confirmation(cloud, numbers, topo_type, level, reading, advice=u"",
                 filtering=None):
    lines = [
        u"Build a toposolid from {0}?".format(element_label(cloud)),
        u"",
        u"{0} points, one per {1} m square".format(
            numbers["count"], format_metres(numbers["spacing"], 2)),
        u"{0} m by {1} m".format(format_metres(numbers["width"], 1),
                                 format_metres(numbers["depth"], 1)),
        u"Lowest {0} m, highest {1} m, measured from the internal "
        u"origin".format(format_metres(numbers["low"], 2),
                         format_metres(numbers["high"], 2)),
        u"Type {0}, on level {1}".format(element_label(topo_type),
                                         element_label(level)),
        u"",
        u"The outline is the rectangle through the outermost points. "
        u"Where a corner has no ground under it, Revit fills in the "
        u"height.",
    ]
    if filtering is not None:
        lines += [u"", filtering.line()]
        if filtering.share() > model.FILTER_WARN_SHARE:
            lines += [u"That is more than a quarter of the site. The object "
                      u"size may be larger than the terrain's own ridges "
                      u"and bumps, which are being flattened with the "
                      u"objects. A smaller size keeps more ground."]
    else:
        lines += [u"", u"All points averaged: anything standing on the "
                       u"ground is part of the surface."]
    if advice:
        lines += [u"", advice]
    if numbers["count"] > model.WARN_POINTS:
        lines += [u"",
                  u"That is a lot of points for a toposolid. It may be "
                  u"slow to build and to edit. A larger spacing would "
                  u"help."]
    if reading.capped:
        lines += [u"",
                  u"{0} part(s) of the cloud were too dense to read in "
                  u"full and were sampled. A larger spacing reads them "
                  u"completely.".format(reading.capped)]
    return u"\n".join(lines)


def outline_loop(corners, z):
    loop = DB.CurveLoop()
    count = len(corners)
    for index in range(count):
        a = corners[index]
        b = corners[(index + 1) % count]
        loop.Append(DB.Line.CreateBound(DB.XYZ(a[0], a[1], z),
                                        DB.XYZ(b[0], b[1], z)))
    return loop


def build(doc, points, corners, type_id, level):
    profiles = dotnet_list(DB.CurveLoop,
                           [outline_loop(corners, level.Elevation)])
    xyzs = dotnet_list(DB.XYZ, [DB.XYZ(p[0], p[1], p[2]) for p in points])
    return DB.Toposolid.Create(doc, profiles, xyzs, type_id, level.Id)


def top_of(doc, toposolid):
    try:
        box = toposolid.get_BoundingBox(None)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None
    if box is None:
        return None
    return box.Max.Z


def build_checked(doc, points, corners, type_id, level, advice=u""):
    """Build it and check its top. Returns (toposolid, how Z was read).

    Raises Abort when the result cannot be trusted. The caller rolls
    back.
    """
    highest = max(p[2] for p in points)
    try:
        toposolid = build(doc, points, corners, type_id, level)
    except BaseException as exc:
        raise Abort(u"Revit would not build the toposolid: {0}".format(
            to_text(exc)))
    if toposolid is None:
        raise Abort(u"Revit returned no toposolid.")
    # A new element has no geometry, and so no bounding box, until the
    # document regenerates.
    doc.Regenerate()
    top = top_of(doc, toposolid)
    mode = model.z_mode(highest, top, level.Elevation)
    if mode == model.ABSOLUTE:
        return toposolid, mode
    if mode == model.RELATIVE:
        doc.Delete(toposolid.Id)
        lowered = model.shifted(points, -level.Elevation)
        try:
            toposolid = build(doc, lowered, corners, type_id, level)
        except BaseException as exc:
            raise Abort(u"Revit would not rebuild the toposolid: "
                        u"{0}".format(to_text(exc)))
        doc.Regenerate()
        top = top_of(doc, toposolid)
        if top is not None and abs(top - highest) <= model.Z_TOLERANCE_FT:
            return toposolid, mode
    raise Abort(
        u"The toposolid did not come out at the height of the cloud, so "
        u"nothing was kept.\n\nHighest point given {0} m, toposolid top "
        u"{1} m, level at {2} m.".format(
            format_metres(model.feet_to_metres(highest)),
            u"unreadable" if top is None else
            format_metres(model.feet_to_metres(top)),
            format_metres(model.feet_to_metres(level.Elevation)))
        + (u"\n\n" + advice if advice else u""))


def vertices_of(toposolid):
    """Revit's own vertices of the top face, as (x, y, z), or []."""
    found = []
    try:
        editor = toposolid.GetSlabShapeEditor()
        for vertex in editor.SlabShapeVertices:
            position = vertex.Position
            found.append((position.X, position.Y, position.Z))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return []
    return found


class FailureCatcher(DB.IFailuresPreprocessor):
    """Turns "too thin for its given type" into a rollback and a count.

    Without it, Revit stops the run with its own error dialog, which
    names the element but not the numbers behind it. Every other failure
    is left to Revit as before.
    """

    def __init__(self):
        self.too_thin = 0

    def PreprocessFailures(self, accessor):
        try:
            target = (DB.BuiltInFailures.SlabShapeFailures
                      .SlabShapeFailedTooThin)
            for message in accessor.GetFailureMessages():
                found = message.GetFailureDefinitionId()
                if found.Guid == target.Guid:
                    self.too_thin += 1
        except BaseException as exc:
            logger.debug(to_text(exc))
        if self.too_thin:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def attach(transaction, catcher):
    """Hand the catcher to the transaction. False when Revit would not."""
    try:
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(catcher)
        transaction.SetFailureHandlingOptions(options)
        return True
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False


def report_vertices(points, toposolid):
    gaps = model.vertex_gaps(points, vertices_of(toposolid))
    if gaps is None:
        output.print_md(u"- Revit's vertices could not be read, so the "
                        u"surface was not compared with the cloud.")
        return
    output.print_md(u"#### The surface against the cloud")
    output.print_md(u"| | |")
    output.print_md(u"| --- | --- |")
    output.print_md(u"| Points kept as vertices | {0} of {1} |".format(
        gaps["matched"], len(points)))
    output.print_md(u"| Points with no vertex | {0} |".format(
        gaps["missing"]))
    output.print_md(u"| Vertices with no point | {0} |".format(gaps["extra"]))
    output.print_md(u"| Height difference, worst | {0} mm |".format(
        format_metres(model.feet_to_metres(gaps["worst"]) * 1000.0, 0)))
    output.print_md(u"| Height difference, average | {0} mm |".format(
        format_metres(model.feet_to_metres(gaps["mean"]) * 1000.0, 0)))
    if gaps["worst_at"] is not None:
        output.print_md(u"| Worst at (internal, m) | X {0}, Y {1} |".format(
            format_metres(model.feet_to_metres(gaps["worst_at"][0]), 1),
            format_metres(model.feet_to_metres(gaps["worst_at"][1]), 1)))


def report(cloud, numbers, toposolid, mode, reading, filtering=None):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"| | |")
    output.print_md(u"| --- | --- |")
    output.print_md(u"| Point cloud | {0} |".format(element_label(cloud)))
    output.print_md(u"| Toposolid | id {0} |".format(
        element_id_text(toposolid)))
    output.print_md(u"| Points | {0} at {1} m spacing |".format(
        numbers["count"], format_metres(numbers["spacing"], 2)))
    output.print_md(u"| Samples read | {0} in {1} tile(s) |".format(
        reading.grid.samples, reading.tiles_read))
    output.print_md(u"| Extent | {0} m by {1} m |".format(
        format_metres(numbers["width"], 1),
        format_metres(numbers["depth"], 1)))
    output.print_md(u"| Points read back | {0} |".format(
        u"through the cloud's transform"
        if reading.frame == model.TRANSFORMED else u"as returned"))
    output.print_md(u"| Point heights read by Revit as | {0} |".format(
        u"relative to the level, and corrected" if mode == model.RELATIVE
        else u"absolute"))
    output.print_md(u"| Method | {0} |".format(
        u"all points averaged" if filtering is None else
        u"ground filter, largest object {0} m, {1} of {2} squares "
        u"dropped".format(
            format_metres(model.feet_to_metres(filtering.window_ft), 1),
            filtering.removed, filtering.cells)))
    if reading.capped:
        output.print_md(u"- {0} part(s) were sampled, not read in "
                        u"full.".format(reading.capped))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if getattr(DB, "Toposolid", None) is None:
        forms.alert(u"Toposolids need Revit 2024 or newer.", title=TITLE)
        return

    clouds = collect(doc, DB.PointCloudInstance)
    if not clouds:
        forms.alert(u"This model has no linked point clouds.", title=TITLE)
        return
    cloud = pick_one(clouds, element_label, TITLE + u": which point cloud?",
                     u"Use this cloud")
    if cloud is None:
        return

    try:
        spacing_ft = ask_spacing()
    except Abort as exc:
        forms.alert(to_text(exc), title=TITLE)
        return
    if spacing_ft is None:
        return

    filtering_on = ask_method()
    if filtering_on is None:
        return
    window_ft = None
    if filtering_on:
        try:
            window_ft = ask_window(spacing_ft)
        except Abort as exc:
            forms.alert(to_text(exc), title=TITLE)
            return
        if window_ft is None:
            return

    types = collect(doc, DB.ToposolidType)
    if not types:
        forms.alert(u"This model has no toposolid types.", title=TITLE)
        return
    topo_type = pick_one(types, element_label,
                         TITLE + u": which toposolid type?", u"Use this type")
    if topo_type is None:
        return

    try:
        level = pick_level(doc)
    except Abort as exc:
        forms.alert(to_text(exc), title=TITLE)
        return
    if level is None:
        return

    try:
        reading = read_cloud(cloud, spacing_ft, filtering_on)
    except Abort as exc:
        if getattr(exc, "reading", None) is not None:
            print_frame_problem(exc.reading)
        forms.alert(to_text(exc), title=TITLE)
        return

    filtering = None
    if filtering_on:
        cells = reading.grid.by_cell()
        points, removed = model.ground_filter(cells, spacing_ft, window_ft)
        filtering = Filtering(window_ft, len(cells), len(removed))
    else:
        points = reading.grid.points()
    corners = model.boundary(points)
    if corners is None:
        forms.alert(
            u"No usable points came back from the point cloud. Check that "
            u"it is loaded and visible, and that the spacing is not larger "
            u"than the cloud.", title=TITLE)
        return
    numbers = model.summary(points, spacing_ft)

    thickness = type_thickness(topo_type)
    advice = too_thin_advice(min(p[2] for p in points), level, thickness)

    if not forms.alert(confirmation(cloud, numbers, topo_type, level,
                                    reading, advice, filtering),
                       title=TITLE, yes=True, no=True):
        return

    transaction = DB.Transaction(doc, TITLE)
    catcher = FailureCatcher()
    attach(transaction, catcher)
    transaction.Start()
    try:
        toposolid, mode = build_checked(doc, points, corners, topo_type.Id,
                                        level, advice)
    except Abort as exc:
        transaction.RollBack()
        forms.alert(to_text(exc), title=TITLE)
        return
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(u"Nothing was built. The run stopped with: {0}".format(
            to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        if catcher.too_thin:
            forms.alert(
                u"Revit refused the toposolid as too thin for its type, so "
                u"nothing was built.\n\n{0}".format(
                    advice or u"The points are lower than this type allows "
                    u"on this level. Try a lower level."), title=TITLE)
            return
        forms.alert(u"Revit rejected the toposolid, so nothing was built "
                    u"({0}).".format(to_text(status)), title=TITLE)
        return

    report(cloud, numbers, toposolid, mode, reading, filtering)
    report_vertices(points, toposolid)


if __name__ == "__main__":
    run()
