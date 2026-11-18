# -*- coding: utf-8 -*-
"""Split each selected wall into one wall per layer of its structure.

A wall of 12.5 mm gypsum / 95 mm timber / 12.5 mm gypsum becomes three
walls, 12.5, 95 and 12.5 mm thick, side by side where the layers were.
Each has a one layer type, "Gypsum 12.5 mm" and so on, reused when the
model already has one of the same thickness, material and function, and
made by duplicating the wall's own type when not.

Nothing changes until the list has been shown in the output window and
confirmed.

## How

The original wall becomes its core layer: the thickest layer inside the
core. It keeps its id, so its doors, windows and wall openings stay in
it with their tags, marks and schedule rows. Every other layer is a copy
of the original (`ElementTransformUtils.CopyElement`), so it keeps the
original's level, base and top constraints, offsets, height, phase,
workset and comments. Each wall is given its layer's type, its location
line set to the wall centreline, and its curve set to the original's
moved across by `layer centre - location line offset` along
`Wall.Orientation`. That arithmetic is in `avh_walllayers.model`,
tested without Revit.

## Doors, windows and wall openings

Measured in Revit with a Probe Wall Joins button (2.49.1, Revit 2025):

- `CopyElement` copies a wall's doors and windows with it but does not
  return them; asking the copy for its inserts finds them. Left in a
  12.5 mm copy, a door makes Revit refuse the commit with "Can't make
  type". So each copy is asked, and everything in it deleted.
- A door or window in the core wall cuts every layer wall joined to it
  (`JoinGeometryUtils.JoinGeometry`), including layers that do not
  touch the core: a brick skin 94.5 mm out, behind air and sheathing.
  Join order made no difference. The cut follows the door if it is
  moved or resized later.
- A wall opening (the Opening tool) cuts only its own wall, joined or
  not. So each is copied into every other layer with `NewOpening`, its
  rectangle moved across to the layer. A copy does not follow the
  original if that is moved later.

After the run every layer wall is read back: whether it is joined to
the core, whether a copied door was left in it, and whether each door,
window and opening really cut it, from the faces of its solid
(`GetGeneratingElementIds`). A layer not cut is reported, whatever
Revit said.

## Shift-click: exterior, core, interior

Shift-click splits each wall into at most three walls instead: every
layer outside the core as one, the core as one, every layer inside it as
one. Each has a type of those layers, in order, all of them core layers,
named after the wall's type ("Exterior wall - exterior"), or the one
layer name when a side has only one layer. Membranes stay in their
part's type. A side with no thickness is not made; a wall with nothing
thick outside its core, or no core, is refused. The core part is the
original wall, and everything above about doors, windows, openings,
joins and the check after the run is the same.

## What is refused

Curtain and stacked walls, curved walls, walls with layers that change
up their height, walls in a group, pinned walls, walls with only one
layer that has a thickness, walls with a wall opening that is not a
rectangle, and walls with anything in them other than family inserts
(doors, windows) and wall openings, such as an embedded curtain wall.
Each is listed with its reason and left alone. Membrane layers (no
thickness) are not made into walls, and are listed.

## Safety

What else sits on each wall (dimensions, tags) is found by deleting it
in a transaction that is then rolled back; after the run each is looked
for, and any Revit removed as the wall narrowed is listed. The types
are made in one transaction and each wall is split in one of its own,
with Revit's failures collected, so a wall Revit refuses is left whole
and the rest still go. All of it is one transaction group: one Ctrl+Z
takes the whole run back. Every commit's status is checked.
"""

__title__ = "Split Wall\nLayers"
__author__ = "AVH"
__doc__ = "Split each selected wall into one wall per layer: 12.5 gypsum / 95 timber / 12.5 gypsum becomes three walls side by side, each with a one layer type. The original becomes the core and keeps its doors and windows, which cut the other layers through joins. Shift-click: three walls instead, the exterior layers, the core and the interior layers. Shows the list and asks before changing anything."

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
from avh_walllayers import model                  # noqa: E402
from avh_openings import writer                   # noqa: E402
from avh_openings.reader import id_value          # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Split Wall Layers"

# How far a new wall's curve or width may be from the plan, in feet,
# before it is reported. A hundredth of a millimetre.
TOLERANCE = 0.01 / model.MM_PER_FOOT


def shift_clicked():
    """True when the button was shift clicked. Two routes because
    pyRevit has offered two; neither is allowed to raise."""
    try:
        from pyrevit import EXEC_PARAMS
        value = getattr(EXEC_PARAMS, "config_mode", None)
        if value is not None:
            return bool(value)
    except BaseException:
        pass
    for source in (globals(), sys.modules["__main__"].__dict__):
        value = source.get("__shiftclick__")
        if value is not None:
            return bool(value)
    return False


class Need(object):
    """A one layer wall type that some layer needs."""

    def __init__(self, layer, source_type):
        self.layer = layer
        self.source_type = source_type    # duplicated when made
        self.name = u""
        self.type_id = None               # set once found or made
        self.existing = False


class Row(object):
    """One layer of one wall: what it becomes, and what happened."""

    def __init__(self, layer, shift):
        self.layer = layer
        self.shift = shift                # feet along Orientation
        self.need = None
        self.new_id = None                # the original's id for the core
        self.opening_ids = []             # wall openings copied into it
        self.join_error = u""
        self.result = u""
        self.done = False


class Hole(object):
    """A rectangular wall opening in the original wall."""

    def __init__(self, element_id, start, end):
        self.element_id = element_id
        self.start = start                # (x, y, z), diagonal corners
        self.end = end


class Plan(object):
    """One selected wall."""

    def __init__(self, wall):
        self.wall = wall
        self.wall_id = wall.Id
        self.type_name = u""
        self.location = u""
        self.ends = None                  # ((x, y, z), (x, y, z))
        self.orientation = None           # (x, y, z), to the exterior
        self.rows = []
        self.core = None                  # the Row the original becomes
        self.membranes = []
        self.inserts = []                 # doors, windows: stay in core
        self.openings = []                # [Hole]: copied to each layer
        self.reason = u""                 # why it will not be split
        self.dependents = []              # [(ElementId, category name)]
        self.lost = []                    # category names, after the run
        self.done = False


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def name_of(element):
    try:
        return to_text(DB.Element.Name.GetValue(element))
    except BaseException:
        pass
    try:
        return to_text(element.Name)
    except BaseException:
        return u""


def category_of(element):
    category = getattr(element, "Category", None)
    return to_text(category.Name) if category is not None else u"element"


def material_name(doc, material_id):
    try:
        if id_value(material_id) < 0:
            return u""
        material = doc.GetElement(material_id)
        return name_of(material) if material is not None else u""
    except BaseException:
        return u""


def read_layers(doc, structure):
    layers = []
    for index, raw in enumerate(structure.GetLayers()):
        layer = model.Layer(index, raw.Width, id_value(raw.MaterialId),
                            material_name(doc, raw.MaterialId),
                            to_text(raw.Function))
        layer.raw_function = raw.Function
        layer.material_id = raw.MaterialId
        layers.append(layer)
    return layers


def varies_up(structure):
    """u"" when every layer runs the full height of the wall, else the
    reason, with what Revit said, so a wrong refusal names its cause.

    2.49.0 asked `IsVerticallyCompound` and refused a plain partition in
    Revit with it: that flag says how the structure is stored, not that
    any layer changes. `IsVerticallyHomogeneous()` is the question
    itself, true when each layer is one region from bottom to top."""
    try:
        if structure.IsVerticallyHomogeneous():
            return u""
        return u"its layers change up its height " \
               u"(IsVerticallyHomogeneous: False)"
    except BaseException as exc:
        logger.debug(to_text(exc))
    compound = getattr(structure, "IsVerticallyCompound", None)
    if compound:
        return u"its layers may change up its height " \
               u"(IsVerticallyHomogeneous could not be read; " \
               u"IsVerticallyCompound: True)"
    return u""


def refusal(wall):
    """Why this wall cannot be split, or u"" when it can be. What is in
    it is sorted by read_inserts."""
    wall_type = wall.WallType
    if wall_type.Kind != DB.WallKind.Basic:
        return u"is a curtain or stacked wall"
    if getattr(wall, "Pinned", False):
        return u"is pinned"
    try:
        if id_value(wall.GroupId) > 0:
            return u"is in a group"
    except BaseException as exc:
        logger.debug(to_text(exc))
    curve = getattr(wall.Location, "Curve", None)
    if not isinstance(curve, DB.Line):
        return u"is curved; only straight walls are split"
    return u""


def all_inserts(wall):
    return list(wall.FindInserts(True, True, True, True))


def read_inserts(doc, plan):
    """Sort what is in the wall into plan.inserts (doors, windows and
    other family instances, which stay in the core) and plan.openings
    (rectangular wall openings, copied into each layer). Returns u"" or
    why the wall cannot be split."""
    try:
        ids = all_inserts(plan.wall)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"its doors and windows could not be read"
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, DB.Opening):
            corners = None
            try:
                if element.IsRectBoundary:
                    corners = [xyz(p) for p in element.BoundaryRect]
            except BaseException as exc:
                logger.debug(to_text(exc))
            if not corners or len(corners) != 2:
                return u"has a wall opening ({0}) that is not a " \
                       u"rectangle".format(id_value(element_id))
            plan.openings.append(Hole(element_id, corners[0], corners[1]))
        elif isinstance(element, DB.FamilyInstance):
            plan.inserts.append(element_id)
        else:
            return u"has {0} {1} in it; only doors, windows and wall " \
                   u"openings are split with a wall".format(
                       category_of(element), id_value(element_id))
    return u""


def read_wall(doc, wall, three=False):
    plan = Plan(wall)
    plan.type_name = name_of(wall.WallType)
    reason = refusal(wall) or read_inserts(doc, plan)
    if reason:
        plan.reason = reason
        return plan
    structure = wall.WallType.GetCompoundStructure()
    if structure is None:
        plan.reason = u"its type has no layers"
        return plan
    reason = varies_up(structure)
    if reason:
        plan.reason = reason
        return plan
    layers = read_layers(doc, structure)
    widths = [layer.width for layer in layers]
    first = structure.GetFirstCoreLayerIndex()
    last = structure.GetLastCoreLayerIndex()
    try:
        param = wall.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM)
        key = param.AsInteger()
        plan.location = model.LOCATION_WORDS.get(key, to_text(key))
        offset = model.location_offset(widths, first, last, key)
        if three:
            made, plan.membranes = model.parts(layers, first, last)
            moves = [model.part_shift(widths, part, offset)
                     for part in made]
        else:
            made, plan.membranes = model.wall_layers(layers)
            every = model.shifts(widths, first, last, key)
            moves = [every[layer.index] for layer in made]
    except model.Refusal as exc:
        plan.reason = to_text(exc)
        return plan
    curve = wall.Location.Curve
    plan.ends = (xyz(curve.GetEndPoint(0)), xyz(curve.GetEndPoint(1)))
    plan.orientation = xyz(wall.Orientation)
    plan.rows = [Row(layer, move) for layer, move in zip(made, moves)]
    core = model.core_layer(made, first, last)
    plan.core = [row for row in plan.rows if row.layer is core][0]
    return plan


def xyz(point):
    return (point.X, point.Y, point.Z)


def selected_plans(doc, uidoc, three=False):
    """(plans, not_walls) from the selection."""
    plans = []
    not_walls = 0
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        ids = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        if isinstance(element, DB.Wall):
            plans.append(read_wall(doc, element, three))
        elif element is not None:
            not_walls += 1
    return plans, not_walls


# --------------------------------------------------------------------------
# Wall types
# --------------------------------------------------------------------------

def existing_types(doc):
    """({structure_key: (name, id)}, every wall type name) for the basic
    wall types already in the model, keyed by their layers."""
    found = {}
    taken = set()
    types = []
    for wall_type in DB.FilteredElementCollector(doc).OfClass(DB.WallType):
        name = name_of(wall_type)
        taken.add(name)
        types.append((name, wall_type))
    for name, wall_type in sorted(types, key=lambda pair: pair[0]):
        try:
            if wall_type.Kind != DB.WallKind.Basic:
                continue
            structure = wall_type.GetCompoundStructure()
            if structure is None or structure.LayerCount < 1 or \
                    varies_up(structure):
                continue
            layers = read_layers(doc, structure)
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        found.setdefault(model.structure_key(layers), (name, wall_type.Id))
    return found, taken


def assign_types(doc, plans):
    """Give every row a Need, shared between rows of the same layer, and
    name the types that will be made. Returns the list of Needs."""
    found, taken = existing_types(doc)
    needs = {}
    ordered = []
    for plan in plans:
        for row in plan.rows:
            key = model.structure_key(model.layers_of(row.layer))
            need = needs.get(key)
            if need is None:
                need = Need(row.layer, plan.wall.WallType)
                if key in found:
                    need.name, need.type_id = found[key]
                    need.existing = True
                else:
                    need.name = model.unique_name(
                        model.part_type_name(row.layer, plan.type_name),
                        taken)
                    taken.add(need.name)
                needs[key] = need
                ordered.append(need)
            row.need = need
    return ordered


def net_list(items):
    """A .NET List of CompoundStructureLayer, or the plain list where
    there is no .NET (the harness)."""
    try:
        from System.Collections.Generic import List
    except ImportError:
        return list(items)
    result = List[DB.CompoundStructureLayer]()
    for item in items:
        result.Add(item)
    return result


def structure_of(layer):
    """The CompoundStructure for a row's type: one layer, or a part's
    layers in order, every one of them core, since a part is split off
    to stand as a wall of its own."""
    layers = model.layers_of(layer)
    if len(layers) == 1:
        one = layers[0]
        return DB.CompoundStructure.CreateSingleLayerCompoundStructure(
            one.raw_function, one.width, one.material_id)
    structure = DB.CompoundStructure.CreateSimpleCompoundStructure(
        net_list(DB.CompoundStructureLayer(one.width, one.raw_function,
                                           one.material_id)
                 for one in layers))
    structure.SetNumberOfShellLayers(DB.ShellLayerType.Exterior, 0)
    structure.SetNumberOfShellLayers(DB.ShellLayerType.Interior, 0)
    return structure


def make_types(doc, needs, made_by):
    """Every type not already in the model, in one transaction.
    Returns u"" or the reason none were made."""
    todo = [need for need in needs if need.type_id is None]
    if not todo:
        return u""

    def work():
        for need in todo:
            new_type = need.source_type.Duplicate(need.name)
            new_type.SetCompoundStructure(structure_of(need.layer))
            width = new_type.GetCompoundStructure().GetWidth()
            if abs(width - need.layer.width) > TOLERANCE:
                raise Exception(u"type {0} came out {1} mm thick".format(
                    need.name, model.format_mm(width)))
            need.type_id = new_type.Id
        return todo

    kept, reason = made_by.attempt(work)
    if kept is None:
        for need in todo:
            need.type_id = None
        return reason
    return u""


# --------------------------------------------------------------------------
# What else sits on each wall
# --------------------------------------------------------------------------

def find_dependents(doc, plans):
    """Delete each wall in a transaction that is rolled back, and keep
    everything else that went with it, other than its doors, windows
    and openings, which stay in it. The wall itself is not deleted by
    the split, but a dimension to a face that moves as it narrows can
    be; after the run each is looked for.

    The categories are read after the rollback: a deleted element cannot
    be looked at, and the rollback brings every one of them back."""
    for plan in plans:
        if plan.reason:
            continue
        transaction = DB.Transaction(doc, TITLE + u": check")
        transaction.Start()
        deleted = []
        try:
            deleted = list(doc.Delete(plan.wall_id))
        except BaseException as exc:
            logger.debug(to_text(exc))
        finally:
            transaction.RollBack()
        own = set([id_value(plan.wall_id)])
        own.update(id_value(e) for e in plan.inserts)
        own.update(id_value(hole.element_id) for hole in plan.openings)
        found = []
        for element_id in deleted:
            if id_value(element_id) in own:
                continue
            element = doc.GetElement(element_id)
            if element is not None:
                found.append((element_id, category_of(element)))
        plan.dependents = found


def counted(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return u", ".join(u"{0} {1}".format(counts[name], name)
                      for name in sorted(counts))


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def point(values):
    return DB.XYZ(values[0], values[1], values[2])


def moved(values, direction, distance):
    return tuple(values[i] + direction[i] * distance for i in range(3))


def place(wall, plan, row):
    """Give a wall its layer's type, centreline and curve, in that
    order: a type change keeps the location line where it is."""
    wall.ChangeTypeId(row.need.type_id)
    key = wall.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM)
    if key is None or key.IsReadOnly or not key.Set(model.CENTRE):
        raise Exception(u"the location line of a wall could not be set")
    wall.Location.Curve = DB.Line.CreateBound(
        point(moved(plan.ends[0], plan.orientation, row.shift)),
        point(moved(plan.ends[1], plan.orientation, row.shift)))


def copy_layer(doc, plan, row):
    """A copy of the original, emptied, placed as this layer, with the
    original's wall openings copied into it."""
    vector = point(moved((0.0, 0.0, 0.0), plan.orientation, row.shift))
    new = None
    returned = []
    for element_id in DB.ElementTransformUtils.CopyElement(
            doc, plan.wall_id, vector):
        element = doc.GetElement(element_id)
        if isinstance(element, DB.Wall) and new is None:
            new = element
        else:
            returned.append(element_id)
    if new is None:
        raise Exception(u"copying it made no wall")
    # Revit 2025: the copy carries the doors and windows but CopyElement
    # returns the wall alone. The copy itself is asked, once regenerated.
    doc.Regenerate()
    for element_id in returned + all_inserts(new):
        if doc.GetElement(element_id) is not None:
            doc.Delete(element_id)
    place(new, plan, row)
    for hole in plan.openings:
        opening = doc.Create.NewOpening(
            new, point(moved(hole.start, plan.orientation, row.shift)),
            point(moved(hole.end, plan.orientation, row.shift)))
        if opening is None:
            raise Exception(u"a wall opening could not be copied into "
                            u"the {0} mm layer".format(
                                model.format_mm(row.layer.width)))
        row.opening_ids.append(opening.Id)
    row.new_id = new.Id


def join(doc, core_wall, layer_wall, row):
    """Join a layer to the core, so the core's doors and windows cut it.
    A join Revit refuses does not stop the split; the check after the
    run reports the layer."""
    try:
        if not DB.JoinGeometryUtils.AreElementsJoined(doc, core_wall,
                                                      layer_wall):
            DB.JoinGeometryUtils.JoinGeometry(doc, core_wall, layer_wall)
    except BaseException as exc:
        row.join_error = to_text(exc) or u"Revit refused it"


def split_one(doc, plan, made_by):
    """Copy the other layers, make the original the core, join. One
    transaction."""
    def work():
        others = [row for row in plan.rows if row is not plan.core]
        for row in others:
            copy_layer(doc, plan, row)
        original = doc.GetElement(plan.wall_id)
        place(original, plan, plan.core)
        plan.core.new_id = plan.wall_id
        for row in others:
            join(doc, original, doc.GetElement(row.new_id), row)
        return plan.rows

    kept, reason = made_by.attempt(work)
    if kept is None:
        for row in plan.rows:
            row.new_id = None
            row.opening_ids = []
            row.join_error = u""
            row.result = u"not split: " + reason
        return False
    plan.done = True
    for row in plan.rows:
        row.done = True
    return True


def split_all(doc, plans, needs):
    """Types, then each wall, all in one group. Returns
    (walls split, u"" or why nothing could start)."""
    made_by = writer.Writer(DB, doc, TITLE, None, logger)
    group = DB.TransactionGroup(doc, TITLE)
    group.Start()
    split = 0
    try:
        reason = make_types(doc, needs, made_by)
        if reason:
            group.RollBack()
            return 0, u"The wall types could not be made: " + reason
        for plan in plans:
            if not plan.reason and split_one(doc, plan, made_by):
                split += 1
    except BaseException:
        group.RollBack()
        raise
    if not split:
        group.RollBack()
        return 0, u""
    status = group.Assimilate()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is not None and status != committed:
        for plan in plans:
            if plan.done:
                plan.done = False
                for row in plan.rows:
                    row.done = False
                    row.new_id = None
                    row.opening_ids = []
                    row.result = u"not kept: Revit rolled the run back"
        return 0, u""
    return split, u""


# --------------------------------------------------------------------------
# The check after the run
# --------------------------------------------------------------------------

def near(a, b):
    return all(abs(a[i] - b[i]) <= TOLERANCE for i in range(3))


def cut_by(wall):
    """Every id that made a face of the wall's solid: its own, and the
    doors, windows and openings that cut it. None when unreadable."""
    found = set()
    try:
        geometry = wall.get_Geometry(DB.Options())
        stack = list(geometry) if geometry is not None else []
        while stack:
            item = stack.pop()
            if isinstance(item, DB.Solid):
                for face in item.Faces:
                    for element_id in wall.GetGeneratingElementIds(face):
                        found.add(id_value(element_id))
            elif isinstance(item, DB.GeometryInstance):
                stack.extend(list(item.GetInstanceGeometry()))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None
    return found


def named(doc, ids):
    words = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        words.append(u"{0} {1}".format(
            category_of(element) if element is not None else u"element",
            id_value(element_id)))
    return u", ".join(words)


def layer_problems(doc, plan, row, wall, core_wall):
    """What is wrong with a layer wall's doors, windows and openings."""
    problems = []
    is_core = row is plan.core
    if is_core:
        expected = list(plan.inserts) + [hole.element_id
                                         for hole in plan.openings]
    else:
        expected = list(plan.inserts) + list(row.opening_ids)
    try:
        hosted = set(id_value(e) for e in all_inserts(wall))
    except BaseException as exc:
        logger.debug(to_text(exc))
        hosted = None
    if hosted is None:
        problems.append(u"what is in it could not be read")
    elif is_core:
        gone = [e for e in expected if id_value(e) not in hosted]
        if gone:
            problems.append(u"{0} no longer in it".format(named(doc, gone)))
    else:
        own = set(id_value(e) for e in row.opening_ids)
        stray = len([v for v in hosted if v not in own])
        if stray:
            problems.append(u"{0} copied door(s), window(s) or opening(s) "
                            u"were left in it".format(stray))
        if own - hosted:
            problems.append(u"a copied wall opening is not in it")
    if not is_core:
        if row.join_error:
            problems.append(u"it could not be joined to the core: " +
                            row.join_error)
        else:
            try:
                joined = DB.JoinGeometryUtils.AreElementsJoined(
                    doc, core_wall, wall)
            except BaseException as exc:
                logger.debug(to_text(exc))
                joined = None
            if joined is None:
                problems.append(u"whether it is joined to the core could "
                                u"not be read")
            elif not joined:
                problems.append(u"it is not joined to the core")
    if expected:
        cut = cut_by(wall)
        if cut is None:
            problems.append(u"whether the doors, windows and openings cut "
                            u"it could not be read")
        else:
            uncut = [e for e in expected if id_value(e) not in cut]
            if uncut:
                problems.append(u"not cut by {0}".format(named(doc, uncut)))
    return problems


def verify(doc, plans):
    """Read every layer wall back and compare it with the plan."""
    for plan in plans:
        if not plan.done:
            continue
        core_wall = doc.GetElement(plan.wall_id)
        for row in plan.rows:
            problems = []
            wall = doc.GetElement(row.new_id) if row.new_id else None
            if wall is None:
                problems.append(u"the wall is not in the model")
            else:
                try:
                    if abs(wall.Width - row.layer.width) > TOLERANCE:
                        problems.append(u"it is {0} mm thick".format(
                            model.format_mm(wall.Width)))
                    curve = wall.Location.Curve
                    ends = (xyz(curve.GetEndPoint(0)),
                            xyz(curve.GetEndPoint(1)))
                    if not (near(ends[0], moved(plan.ends[0],
                                                plan.orientation,
                                                row.shift)) and
                            near(ends[1], moved(plan.ends[1],
                                                plan.orientation,
                                                row.shift))):
                        problems.append(u"it is not where the layer was")
                except BaseException as exc:
                    problems.append(u"it could not be read back: " +
                                    to_text(exc))
                problems.extend(layer_problems(doc, plan, row, wall,
                                               core_wall))
            if problems:
                row.done = False
                row.result = u"made, but {0}. Check it.".format(
                    u"; ".join(problems))
            elif row is plan.core:
                row.result = u"kept: {0}, the original wall".format(
                    link(row.new_id))
            else:
                row.result = u"made: " + link(row.new_id)
        plan.lost = [name for element_id, name in plan.dependents
                     if doc.GetElement(element_id) is None]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def link(element_id):
    try:
        return output.linkify(element_id)
    except BaseException:
        return to_text(id_value(element_id))


def cell(text):
    """Text that cannot break the table: a pipe character would start a
    new column, and a line break a new row. Never empty."""
    text = to_text(text).replace(u"|", u"/").replace(u"\n", u" ").strip()
    return text or u"-"


def layer_words(layer):
    """The Layer column: the function, or for a part its side and how
    many layers it holds."""
    if isinstance(layer, model.Part):
        return u"{0}: {1} layer(s)".format(layer.side, len(layer.layers))
    return model.function_words(layer.function)


def core_words(layer):
    """The core, in the sentence about doors staying in it."""
    if isinstance(layer, model.Part):
        return u"{0} mm {1}".format(model.format_mm(layer.width),
                                    layer.side.lower())
    return u"{0} mm {1} layer".format(model.format_mm(layer.width),
                                      layer.material_name or u"core")


def where(row):
    if abs(row.shift) <= TOLERANCE:
        return u"on the location line"
    return u"{0} mm {1} the location line".format(
        model.format_mm(abs(row.shift)),
        u"outside" if row.shift >= 0 else u"inside")


def print_table(plans, after, three=False):
    """One table, built whole and printed in one print_md, every column
    left aligned. See Pipe Openings for why both matter."""
    lines = [
        u"| Wall | Wall type | Layer | Thickness mm | Material | "
        u"Becomes | {0} |".format(u"Result" if after else u"What happens"),
        u"| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for plan in plans:
        if plan.reason:
            continue
        for row in plan.rows:
            layer = row.layer
            becomes = row.need.name + (u" (in the model)"
                                       if row.need.existing
                                       else u" (new type)")
            if after:
                note = row.result
            elif row is plan.core:
                note = u"the original wall, {0}".format(where(row))
            else:
                note = u"new wall {0}, joined to the original".format(
                    where(row))
            lines.append(u"| {0} | {1} | {2} | {3} | {4} | {5} | {6} |"
                         .format(link(plan.wall_id), cell(plan.type_name),
                                 cell(layer_words(layer)),
                                 model.format_mm(layer.width),
                                 cell(layer.material_name or
                                      u"<By Category>"),
                                 cell(becomes), cell(note)))
    if len(lines) == 2:
        return          # every wall refused: the notes say why
    output.print_md(u"### {0}: {1}".format(
        TITLE, u"result" if after else
        u"to split into exterior, core and interior" if three else
        u"to split"))
    output.print_md(u"\n".join(lines))


def print_notes(plans, after):
    lines = []
    for plan in plans:
        wall = link(plan.wall_id)
        if plan.reason:
            if not after:
                lines.append(u"- Not split: {0} {1}.".format(
                    wall, plan.reason))
            continue
        if plan.membranes:
            lines.append(u"- {0}: {1} membrane layer(s) with no thickness "
                         u"{2} not made into walls.".format(
                             wall, len(plan.membranes),
                             u"were" if after else u"will be"))
        if after:
            if plan.done and plan.lost:
                lines.append(u"- {0}: Revit removed {1} as the wall "
                             u"narrowed.".format(wall, counted(plan.lost)))
            continue
        if plan.inserts:
            lines.append(u"- {0}: its {1} door(s) and window(s) stay in "
                         u"it, the {2}, and cut the other "
                         u"layers through joins.".format(
                             wall, len(plan.inserts),
                             core_words(plan.core.layer)))
        if plan.openings:
            lines.append(u"- {0}: its {1} wall opening(s) are copied into "
                         u"each other layer. A copy does not follow the "
                         u"original if that is moved later.".format(
                             wall, len(plan.openings)))
        if plan.dependents:
            lines.append(u"- {0} has {1} on it. Any on a face that moves "
                         u"as the wall narrows may be removed; the result "
                         u"lists them.".format(
                             wall, counted(name for _, name
                                           in plan.dependents)))
        lines.append(u"- {0} is placed by its {1}.".format(
            wall, plan.location))
    if lines:
        output.print_md(u"\n".join(lines))


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if getattr(doc, "IsFamilyDocument", False):
        forms.alert(u"Open a project, not a family.", title=TITLE)
        return

    three = shift_clicked()
    plans, not_walls = selected_plans(doc, uidoc, three)
    if not plans:
        forms.alert(u"Select the walls to split first, then click again.",
                    title=TITLE)
        return

    find_dependents(doc, plans)
    todo = [plan for plan in plans if not plan.reason]
    needs = assign_types(doc, todo)
    print_table(plans, after=False, three=three)
    print_notes(plans, after=False)

    if not todo:
        output.print_md(u"### {0}: nothing to split".format(TITLE))
        forms.alert(u"None of the {0} selected wall(s) can be split. The "
                    u"reasons are in the output window.".format(len(plans)),
                    title=TITLE)
        return

    layers = sum(len(plan.rows) for plan in todo)
    new_types = len([need for need in needs if not need.existing])
    if three:
        message = u"Split {0} wall(s) into {1} wall(s): the exterior " \
                  u"layers, the core and the interior layers? Each " \
                  u"original wall becomes its core and keeps its doors " \
                  u"and windows.".format(len(todo), layers)
    else:
        message = u"Split {0} wall(s) into {1} wall(s), one per layer? " \
                  u"Each original wall becomes its core layer and keeps " \
                  u"its doors and windows.".format(len(todo), layers)
    if new_types:
        message += u"\n\n{0} new {1}wall type(s) will be made.".format(
            new_types, u"" if three else u"one layer ")
    holes = sum(len(plan.openings) for plan in todo)
    if holes:
        message += u"\n\n{0} wall opening(s) will be copied into the " \
                   u"other layers.".format(holes)
    on_walls = sum(len(plan.dependents) for plan in todo)
    if on_walls:
        message += u"\n\n{0} dimension(s), tag(s) or other element(s) " \
                   u"are on these walls. Revit may remove those on a " \
                   u"face that moves; the result lists any.".format(
                       on_walls)
    skipped = len(plans) - len(todo)
    if skipped:
        message += u"\n\n{0} wall(s) will be left as they are; the " \
                   u"output window says why.".format(skipped)
    if not_walls:
        message += u"\n\n{0} selected element(s) are not walls and are " \
                   u"ignored.".format(not_walls)
    # A tool that writes stops if the question cannot be asked: nobody
    # confirmed anything.
    if not forms.alert(message, title=TITLE, yes=True, no=True):
        return

    split, reason = split_all(doc, todo, needs)
    if reason:
        forms.alert(reason, title=TITLE)
        return
    try:
        verify(doc, todo)
    except BaseException as exc:
        logger.debug(to_text(exc))
        for plan in todo:
            for row in plan.rows:
                if row.done and not row.result:
                    row.done = False
                    row.result = u"made, but it could not be checked: " + \
                        to_text(exc)

    print_table(plans, after=True)
    print_notes(plans, after=True)
    good = len([plan for plan in todo
                if plan.done and all(row.done for row in plan.rows)])
    summary = u"{0} wall(s) split.".format(good)
    if len(todo) - good:
        summary += u" {0} could not be, or need checking; see the " \
                   u"output window.".format(len(todo) - good)
    if split:
        summary += u"\n\nOne undo takes the whole run back."
    forms.alert(summary, title=TITLE)


if __name__ == "__main__":
    run()
