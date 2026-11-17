# -*- coding: utf-8 -*-
"""Split each selected wall into one wall per layer of its structure.

A wall of 12.5 mm gypsum / 95 mm timber / 12.5 mm gypsum becomes three
walls, 12.5, 95 and 12.5 mm thick, side by side where the layers were.
Each new wall has a one layer type, "Gypsum 12.5 mm" and so on, reused
when the model already has one of the same thickness, material and
function, and made by duplicating the wall's own type when not.

Nothing changes until the list has been shown in the output window and
confirmed.

## How

Each new wall is a copy of the original (`ElementTransformUtils.
CopyElement`), so it keeps the original's level, base and top
constraints, offsets, height, phase, workset and comments. The copy is
given its layer's type, its location line set to the wall centreline,
and its curve set to the original's moved across by
`layer centre - location line offset` along `Wall.Orientation`. That
arithmetic is in `avh_walllayers.model`, tested without Revit. Then the
original is deleted.

## What is refused

Curtain and stacked walls, curved walls, walls with layers that change
up their height, walls with doors, windows or openings in them, walls
in a group, pinned walls, and walls with only one layer that has a
thickness. Each is listed with its reason and left alone. Membrane
layers (no thickness) are not made into walls, and are listed.

## Safety

What else deleting each original takes with it (dimensions, tags) is
found by deleting it in a transaction that is then rolled back, and is
listed before the question. The types are made in one transaction and
each wall is split in one of its own with Revit's failures collected,
so a wall Revit refuses is left whole and the rest still go. All of it
is one transaction group: one Ctrl+Z takes the whole run back. Every
commit's status is checked.

After the run each new wall is read back, its width and its curve
compared with the plan, and the original looked for. A wall that does
not match is reported as needing a check, whatever Revit said.

## Unverified

Not yet run in Revit: `CopyElement` on a wall, `ChangeTypeId`,
`WALL_KEY_REF_PARAM.Set`, setting `LocationCurve.Curve`,
`WallType.Duplicate`, `CompoundStructure.IsVerticallyHomogeneous`,
`CompoundStructure.
CreateSingleLayerCompoundStructure` with a function other than
Structure, `Wall.FindInserts`, and that `Wall.Orientation` points to
the exterior of a flipped wall.
"""

__title__ = "Split Wall\nLayers"
__author__ = "AVH"
__doc__ = "Split each selected wall into one wall per layer: 12.5 gypsum / 95 timber / 12.5 gypsum becomes three walls side by side, each with a one layer type. Shows the list and asks before changing anything."

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
        self.new_id = None
        self.result = u""
        self.done = False


class Plan(object):
    """One selected wall."""

    def __init__(self, wall):
        self.wall = wall
        self.wall_id = wall.Id            # kept: the wall is deleted
        self.type_name = u""
        self.location = u""
        self.ends = None                  # ((x, y, z), (x, y, z))
        self.orientation = None           # (x, y, z), to the exterior
        self.rows = []
        self.membranes = []
        self.reason = u""                 # why it will not be split
        self.dependents = []              # [category name] deleted too
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
    """Why this wall cannot be split, or u"" when it can be."""
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
    try:
        inserts = list(wall.FindInserts(True, True, True, True))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"its doors and windows could not be read"
    if inserts:
        return u"has {0} door(s), window(s) or opening(s) in it; split " \
               u"it by hand".format(len(inserts))
    return u""


def read_wall(doc, wall):
    plan = Plan(wall)
    plan.type_name = name_of(wall.WallType)
    reason = refusal(wall)
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
    try:
        param = wall.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM)
        key = param.AsInteger()
        plan.location = model.LOCATION_WORDS.get(key, to_text(key))
        moves = model.shifts(widths, structure.GetFirstCoreLayerIndex(),
                             structure.GetLastCoreLayerIndex(), key)
        made, plan.membranes = model.wall_layers(layers)
    except model.Refusal as exc:
        plan.reason = to_text(exc)
        return plan
    curve = wall.Location.Curve
    plan.ends = (xyz(curve.GetEndPoint(0)), xyz(curve.GetEndPoint(1)))
    plan.orientation = xyz(wall.Orientation)
    plan.rows = [Row(layer, moves[layer.index]) for layer in made]
    return plan


def xyz(point):
    return (point.X, point.Y, point.Z)


def selected_plans(doc, uidoc):
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
            plans.append(read_wall(doc, element))
        elif element is not None:
            not_walls += 1
    return plans, not_walls


# --------------------------------------------------------------------------
# Wall types
# --------------------------------------------------------------------------

def existing_types(doc):
    """({type_key: (name, id)}, every wall type name) for the one layer
    basic wall types already in the model."""
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
            if structure is None or structure.LayerCount != 1 or \
                    varies_up(structure):
                continue
            layer = read_layers(doc, structure)[0]
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        found.setdefault(model.type_key(layer), (name, wall_type.Id))
    return found, taken


def assign_types(doc, plans):
    """Give every row a Need, shared between rows of the same layer, and
    name the types that will be made. Returns the list of Needs."""
    found, taken = existing_types(doc)
    needs = {}
    ordered = []
    for plan in plans:
        for row in plan.rows:
            key = model.type_key(row.layer)
            need = needs.get(key)
            if need is None:
                need = Need(row.layer, plan.wall.WallType)
                if key in found:
                    need.name, need.type_id = found[key]
                    need.existing = True
                else:
                    need.name = model.unique_name(
                        model.type_name(row.layer), taken)
                    taken.add(need.name)
                needs[key] = need
                ordered.append(need)
            row.need = need
    return ordered


def make_types(doc, needs, made_by):
    """Every type not already in the model, in one transaction.
    Returns u"" or the reason none were made."""
    todo = [need for need in needs if need.type_id is None]
    if not todo:
        return u""

    def work():
        for need in todo:
            layer = need.layer
            new_type = need.source_type.Duplicate(need.name)
            structure = DB.CompoundStructure.\
                CreateSingleLayerCompoundStructure(
                    layer.raw_function, layer.width, layer.material_id)
            new_type.SetCompoundStructure(structure)
            width = new_type.GetCompoundStructure().GetWidth()
            if abs(width - layer.width) > TOLERANCE:
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
# What else goes with each wall
# --------------------------------------------------------------------------

def find_dependents(doc, plans):
    """Delete each wall in a transaction that is rolled back, and keep
    the categories of everything else that went with it.

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
            plan.reason = u"Revit will not delete it: " + to_text(exc)
        finally:
            transaction.RollBack()
        names = []
        for element_id in deleted:
            if id_value(element_id) == id_value(plan.wall_id):
                continue
            element = doc.GetElement(element_id)
            category = getattr(element, "Category", None)
            if category is not None:
                names.append(to_text(category.Name))
        plan.dependents = names


def dependents_text(names):
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


def split_one(doc, plan, made_by):
    """Copy the wall once per layer, then delete it. One transaction."""
    def work():
        for row in plan.rows:
            vector = point(moved((0.0, 0.0, 0.0), plan.orientation,
                                 row.shift))
            new = None
            for element_id in DB.ElementTransformUtils.CopyElement(
                    doc, plan.wall_id, vector):
                element = doc.GetElement(element_id)
                if isinstance(element, DB.Wall):
                    new = element
                    break
            if new is None:
                raise Exception(u"copying it made no wall")
            new.ChangeTypeId(row.need.type_id)
            key = new.get_Parameter(DB.BuiltInParameter.WALL_KEY_REF_PARAM)
            if key is None or key.IsReadOnly or not key.Set(model.CENTRE):
                raise Exception(u"the location line of a new wall could "
                                u"not be set")
            new.Location.Curve = DB.Line.CreateBound(
                point(moved(plan.ends[0], plan.orientation, row.shift)),
                point(moved(plan.ends[1], plan.orientation, row.shift)))
            row.new_id = new.Id
        doc.Delete(plan.wall_id)
        return plan.rows

    kept, reason = made_by.attempt(work)
    if kept is None:
        for row in plan.rows:
            row.new_id = None
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
                    row.result = u"not kept: Revit rolled the run back"
        return 0, u""
    return split, u""


def near(a, b):
    return all(abs(a[i] - b[i]) <= TOLERANCE for i in range(3))


def verify(doc, plans):
    """Read every new wall back and compare it with the plan."""
    for plan in plans:
        if not plan.done:
            continue
        original = doc.GetElement(plan.wall_id)
        for row in plan.rows:
            problems = []
            wall = doc.GetElement(row.new_id) if row.new_id else None
            if wall is None:
                problems.append(u"the new wall is not in the model")
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
            if original is not None:
                problems.append(u"the original wall is still there")
            if problems:
                row.done = False
                row.result = u"made, but {0}. Check it.".format(
                    u"; ".join(problems))
            else:
                row.result = u"made: " + link(row.new_id)


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


def print_table(plans, after):
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
            else:
                note = u"new wall {0} mm {1} the location line".format(
                    model.format_mm(abs(row.shift)),
                    u"outside" if row.shift >= 0 else u"inside") \
                    if abs(row.shift) > TOLERANCE else \
                    u"new wall on the location line"
            lines.append(u"| {0} | {1} | {2} | {3} | {4} | {5} | {6} |"
                         .format(link(plan.wall_id), cell(plan.type_name),
                                 cell(model.function_words(layer.function)),
                                 model.format_mm(layer.width),
                                 cell(layer.material_name or
                                      u"<By Category>"),
                                 cell(becomes), cell(note)))
    if len(lines) == 2:
        return          # every wall refused: the notes say why
    output.print_md(u"### {0}: {1}".format(
        TITLE, u"result" if after else u"to split"))
    output.print_md(u"\n".join(lines))


def print_notes(plans, after):
    lines = []
    for plan in plans:
        if plan.reason:
            if not after:
                lines.append(u"- Not split: {0} {1}.".format(
                    link(plan.wall_id), plan.reason))
            continue
        if plan.membranes:
            lines.append(u"- {0}: {1} membrane layer(s) with no thickness "
                         u"{2} not made into walls.".format(
                             link(plan.wall_id), len(plan.membranes),
                             u"were" if after else u"will be"))
        if plan.dependents and not after:
            lines.append(u"- Deleting {0} also deletes {1}.".format(
                link(plan.wall_id), dependents_text(plan.dependents)))
        if not after:
            lines.append(u"- {0} is placed by its {1}.".format(
                link(plan.wall_id), plan.location))
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

    plans, not_walls = selected_plans(doc, uidoc)
    if not plans:
        forms.alert(u"Select the walls to split first, then click again.",
                    title=TITLE)
        return

    find_dependents(doc, plans)
    todo = [plan for plan in plans if not plan.reason]
    needs = assign_types(doc, todo)
    print_table(plans, after=False)
    print_notes(plans, after=False)

    if not todo:
        output.print_md(u"### {0}: nothing to split".format(TITLE))
        forms.alert(u"None of the {0} selected wall(s) can be split. The "
                    u"reasons are in the output window.".format(len(plans)),
                    title=TITLE)
        return

    layers = sum(len(plan.rows) for plan in todo)
    new_types = len([need for need in needs if not need.existing])
    message = u"Split {0} wall(s) into {1} wall(s), one per layer?".format(
        len(todo), layers)
    if new_types:
        message += u"\n\n{0} new one layer wall type(s) will be " \
                   u"made.".format(new_types)
    deleted = sum(len(plan.dependents) for plan in todo)
    if deleted:
        message += u"\n\nDeleting the original walls also deletes {0} " \
                   u"other element(s), such as dimensions and tags. The " \
                   u"output window lists them.".format(deleted)
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
