# -*- coding: utf-8 -*-
"""Copy scope boxes in from a linked model, and keep them current.

Pick a link. Every scope box in it is compared with the copies already
in this model: new ones are copied in, changed ones are replaced,
unchanged ones are left alone. Nothing is written until a dry run has
been shown and confirmed.

## Copies are named after their link

There is no hidden id tying a copy to its source, so the name carries
it: `ELG_ARK - DA11`. The tool only ever touches scope boxes whose names
start with the chosen link's prefix. A scope box made by hand in this
model is never matched, replaced or deleted. Boxes that have disappeared
from the link are reported and left alone, because deleting one strips
it from every view and datum that uses it.

## Replacing is the only way to update

The Revit API cannot create a scope box, and cannot resize or move an
existing one. It can copy one between documents. So a changed box is
replaced, in this order inside one transaction:

1. copy the new box in from the link,
2. move every view, grid, level and reference plane from the old box to
   the new one,
3. delete the old box, which by now nothing uses,
4. rename the new box to the old name.

Doing it in that order means nothing points at a missing box at any
point, and any refusal rolls the whole run back. The replaced box has a
new element id: anything that referred to the old one by id rather than
through a view or datum parameter is not followed.

## Checked afterwards

Every box written is read back after regeneration and its eight corners
compared with the link's, through the link's own transform. A copy that
lands anywhere else rolls the run back and prints the working.

## Unverified

Not yet run in Revit: `ElementTransformUtils.CopyElements` from a linked
document, renaming a scope box through `Element.Name`, and writing
`VIEWER_VOLUME_OF_INTEREST_CROP` and `DATUM_VOLUME_OF_INTEREST`. The
first real run is the test of all four, and each is read back rather
than trusted.
"""

__title__ = "Sync Scope\nBoxes"
__author__ = "AVH"
__doc__ = ("Copy scope boxes in from a linked model, replace the ones "
           "that changed there, and keep views and datums assigned to "
           "them. Shows a dry run first.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. A button nested one deeper, in a pulldown, was enough
# to break the fixed count, and it breaks at import time with a message
# about a module nobody has heard of.
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
from avh_scopebox import model                    # noqa: E402
from avh_worksets import model as worksets_model  # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Sync Scope Boxes"
MAX_LISTED = 25

# Every kind of element that can be assigned to a scope box. Grids,
# levels and reference planes all follow a box for their 3D extents;
# missing one here would strip its assignment silently on replacement.
DATUM_CLASSES = ("Grid", "Level", "ReferencePlane")


class Abort(Exception):
    """A refusal that rolls the whole run back, carrying what to say."""


def element_name(element):
    if element is None:
        return u""
    try:
        return to_text(element.Name)
    except BaseException:
        return u""


def id_collection(ids):
    """An ICollection<ElementId>, which CopyElements insists on."""
    try:
        from System.Collections.Generic import List
        return List[DB.ElementId](ids)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return list(ids)


def same_id(a, b):
    if a is None or b is None:
        return False
    try:
        return a.IntegerValue == b.IntegerValue
    except BaseException:
        return a == b


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def loaded_links(doc):
    """[(label, instance, link_doc, type_name)] for every loaded link."""
    found = []
    try:
        collector = DB.FilteredElementCollector(doc)
        collector = collector.OfClass(DB.RevitLinkInstance)
        for instance in collector:
            try:
                link_doc = instance.GetLinkDocument()
            except BaseException:
                link_doc = None
            if link_doc is None:
                continue
            try:
                type_name = element_name(doc.GetElement(instance.GetTypeId()))
            except BaseException:
                type_name = u""
            if not type_name:
                type_name = element_name(instance)
            found.append((type_name, instance, link_doc, type_name))
    except BaseException as exc:
        logger.debug(to_text(exc))

    # Two instances of the same file need telling apart in the picker.
    seen = {}
    labelled = []
    for label, instance, link_doc, type_name in found:
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = u"{0}  (instance {1})".format(label, seen[label])
        labelled.append((label, instance, link_doc, type_name))
    return labelled


def pick_link(doc):
    links = loaded_links(doc)
    if not links:
        forms.alert(u"No loaded Revit links in this model.", title=TITLE)
        return None
    by_label = dict((entry[0], entry) for entry in links)
    chosen = forms.SelectFromList.show(
        sorted(by_label.keys()), multiselect=False,
        title=TITLE + u": which link?", button_name="Compare")
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    return by_label.get(to_text(chosen))


def transform_tuple(transform):
    """A Revit Transform as (origin, x, y, z) tuples for the model."""
    def point(xyz):
        return (xyz.X, xyz.Y, xyz.Z)
    return (point(transform.Origin), point(transform.BasisX),
            point(transform.BasisY), point(transform.BasisZ))


def edges_of(element):
    """The edges of a scope box as point pairs, or [] when unreadable."""
    edges = []
    try:
        geometry = element.get_Geometry(DB.Options())
    except BaseException as exc:
        logger.debug(to_text(exc))
        return edges
    if geometry is None:
        return edges
    try:
        items = list(geometry)
    except BaseException:
        return edges
    for item in items:
        try:
            start = item.GetEndPoint(0)
            end = item.GetEndPoint(1)
        except BaseException:
            continue
        edges.append(((start.X, start.Y, start.Z), (end.X, end.Y, end.Z)))
    return edges


def read_box(element, transform=None):
    """A model.Box, its corners put through `transform` when given."""
    corners = model.corners_from_edges(edges_of(element))
    if transform is not None:
        corners = [model.apply_transform(transform, corner)
                   for corner in corners]
    return model.Box(element_name(element), element.Id, corners)


def scope_boxes(document):
    found = []
    try:
        collector = DB.FilteredElementCollector(document)
        collector = collector.OfCategory(
            DB.BuiltInCategory.OST_VolumeOfInterest)
        collector = collector.WhereElementIsNotElementType()
        for element in collector:
            found.append(element)
    except BaseException as exc:
        logger.debug(to_text(exc))
    return found


def assignments(doc, box_id):
    """[(element, parameter, kind)] for everything assigned to a box."""
    found = []

    def check(element, built_in, kind):
        try:
            parameter = element.get_Parameter(built_in)
        except BaseException:
            parameter = None
        if parameter is None:
            return
        try:
            current = parameter.AsElementId()
        except BaseException:
            return
        if same_id(current, box_id):
            found.append((element, built_in, kind))

    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
            try:
                if view.IsTemplate:
                    continue
            except BaseException:
                pass
            check(view, DB.BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP,
                  u"view")
    except BaseException as exc:
        logger.debug(to_text(exc))

    for class_name in DATUM_CLASSES:
        cls = getattr(DB, class_name, None)
        if cls is None:
            continue
        try:
            for datum in DB.FilteredElementCollector(doc).OfClass(cls):
                check(datum, DB.BuiltInParameter.DATUM_VOLUME_OF_INTEREST,
                      class_name.lower())
        except BaseException as exc:
            logger.debug(to_text(exc))
    return found


def conventional_workset_id(doc):
    """The id of Shared Views, Levels, Grids, or None."""
    try:
        if not doc.IsWorkshared:
            return None
    except BaseException:
        return None
    try:
        collector = DB.FilteredWorksetCollector(doc)
        collector = collector.OfKind(DB.WorksetKind.UserWorkset)
        for workset in collector:
            if element_name(workset) == worksets_model.CONVENTIONAL:
                return workset.Id.IntegerValue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


# ---------------------------------------------------------------------------
# Writing. Every helper raises Abort rather than carrying on half done.
# ---------------------------------------------------------------------------

def copy_in(doc, link_doc, source, transform):
    try:
        new_ids = DB.ElementTransformUtils.CopyElements(
            link_doc, id_collection([source.Id]), doc, transform,
            DB.CopyPasteOptions())
    except BaseException as exc:
        raise Abort(u"Revit would not copy {0} in from the link: {1}".format(
            element_name(source), to_text(exc)))
    new_ids = list(new_ids) if new_ids is not None else []
    if len(new_ids) != 1:
        raise Abort(u"Copying {0} produced {1} elements instead of one."
                    .format(element_name(source), len(new_ids)))
    copy = doc.GetElement(new_ids[0])
    if copy is None:
        raise Abort(u"The copy of {0} could not be found after copying."
                    .format(element_name(source)))
    return copy


def reassign(items, new_id):
    """Point every assignment at the new box, and read each one back."""
    for element, built_in, kind in items:
        name = element_name(element)
        try:
            parameter = element.get_Parameter(built_in)
            if parameter is None or parameter.IsReadOnly:
                raise Abort(u"The {0} {1} cannot have its scope box "
                            u"changed.".format(kind, name))
            if not parameter.Set(new_id):
                raise Abort(u"The {0} {1} refused the new scope "
                            u"box.".format(kind, name))
            if not same_id(parameter.AsElementId(), new_id):
                raise Abort(u"The {0} {1} accepted the new scope box and "
                            u"then did not hold it.".format(kind, name))
        except Abort:
            raise
        except BaseException as exc:
            raise Abort(u"The {0} {1} could not be reassigned: {2}".format(
                kind, name, to_text(exc)))


def rename(element, name):
    try:
        element.Name = name
    except BaseException as exc:
        raise Abort(u"The copied scope box could not be renamed to {0}: "
                    u"{1}".format(name, to_text(exc)))
    if element_name(element) != name:
        raise Abort(u"The copied scope box was renamed but reads back as "
                    u"{0}, not {1}.".format(element_name(element), name))


def to_workset(element, workset_id):
    """Move to the conventional workset. A note, never fatal."""
    if workset_id is None:
        return u""
    try:
        parameter = element.get_Parameter(
            DB.BuiltInParameter.ELEM_PARTITION_PARAM)
        if parameter is None or parameter.IsReadOnly:
            return u"workset could not be set"
        if parameter.AsInteger() == workset_id:
            return u""
        if not parameter.Set(workset_id):
            return u"workset was refused"
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"workset could not be set"
    return u""


def diagnose(target, transform, expected, actual):
    """Print the working when a copy lands in the wrong place."""
    output.print_md(u"#### What the copy of {0} did".format(target))
    output.print_md(u"_All in feet, host coordinates._")
    output.print_md(u"```")
    output.print_md(u"link transform origin {0}".format(repr(transform[0])))
    output.print_md(u"link transform x axis {0}".format(repr(transform[1])))
    output.print_md(u"link transform y axis {0}".format(repr(transform[2])))
    output.print_md(u"expected corners {0}".format(repr(expected)))
    output.print_md(u"copy read back   {0}".format(repr(actual)))
    output.print_md(u"```")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_plan(plan, label):
    output.print_md(u"### {0}: {1}".format(TITLE, label))
    for state_label, count in model.summary(plan):
        output.print_md(u"- {0}: **{1}**".format(state_label, count))
    shown = 0
    for state, link_box, host_box, target in plan:
        if state == model.SAME:
            continue
        if shown >= MAX_LISTED:
            output.print_md(u"- _and more_")
            break
        detail = u""
        if state == model.CHANGED:
            shift = model.largest_shift_mm(host_box.corners, link_box.corners)
            if shift is not None:
                detail = u", worst corner moved {0:.0f} mm".format(shift)
        output.print_md(u"- {0}: {1}{2}".format(
            target, model.STATE_LABELS[state], detail))
        shown += 1


def confirmation(plan, uses):
    lines = []
    new = [entry for entry in plan if entry[0] == model.NEW]
    changed = [entry for entry in plan if entry[0] == model.CHANGED]
    if new:
        lines.append(u"Copy in {0} new scope box(es).".format(len(new)))
    if changed:
        lines.append(u"Replace {0} changed scope box(es):".format(
            len(changed)))
        for _state, _link, _host, target in changed[:10]:
            lines.append(u"   {0}, {1} view(s) and datum(s) moved "
                         u"across".format(target, len(uses.get(target, []))))
        lines.append(u"")
        lines.append(u"A replaced box gets a new element id. Views, grids, "
                     u"levels and reference planes are moved to it; "
                     u"anything else that pointed at the old box is not.")
    lines.append(u"")
    lines.append(u"Go ahead?")
    return u"\n".join(lines)


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    picked = pick_link(doc)
    if picked is None:
        return
    _label, instance, link_doc, type_name = picked
    label = model.link_label(type_name)
    if not label:
        forms.alert(u"That link has no name to label its scope boxes with.",
                    title=TITLE)
        return

    try:
        revit_transform = instance.GetTotalTransform()
        transform = transform_tuple(revit_transform)
    except BaseException as exc:
        forms.alert(u"The link's position could not be read: {0}".format(
            to_text(exc)), title=TITLE)
        return

    link_elements = scope_boxes(link_doc)
    if not link_elements:
        forms.alert(u"{0} has no scope boxes.".format(type_name), title=TITLE)
        return

    link_boxes = [read_box(element, transform) for element in link_elements]
    unreadable = [box.name for box in link_boxes if len(box.corners) != 8]
    if unreadable:
        forms.alert(u"These scope boxes in the link could not be read as "
                    u"boxes, so nothing was done:\n\n{0}".format(
                        u"\n".join(unreadable[:MAX_LISTED])), title=TITLE)
        return

    host_boxes = [read_box(element) for element in scope_boxes(doc)]
    plan = model.classify(link_boxes, host_boxes, label)
    print_plan(plan, label)

    to_write = model.writes(plan)
    if not to_write:
        forms.alert(u"Every scope box from {0} is already here and "
                    u"unchanged.".format(label), title=TITLE)
        return

    # Every assignment is read before anything is written, so the
    # confirmation can say how much each replacement touches.
    uses = {}
    for state, _link_box, host_box, target in to_write:
        if state == model.CHANGED:
            uses[target] = assignments(doc, host_box.element_id)

    if not forms.alert(confirmation(plan, uses), title=TITLE,
                       yes=True, no=True):
        return

    workset_id = conventional_workset_id(doc)
    notes = []
    done = []

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        for state, link_box, host_box, target in to_write:
            source = link_doc.GetElement(link_box.element_id)
            if source is None:
                raise Abort(u"{0} could not be found in the link.".format(
                    link_box.name))

            copy = copy_in(doc, link_doc, source, revit_transform)
            # Precaution, not proven necessary. Nothing read below needs
            # regenerated geometry, but views about to be pointed at this
            # box may compute their crop from it the moment they are, and
            # that cannot be tested outside Revit. Cheap to keep.
            doc.Regenerate()

            if state == model.CHANGED:
                reassign(uses.get(target, []), copy.Id)
                try:
                    doc.Delete(host_box.element_id)
                except BaseException as exc:
                    raise Abort(u"The old {0} could not be deleted: "
                                u"{1}".format(target, to_text(exc)))

            rename(copy, target)
            note = to_workset(copy, workset_id)
            if note:
                notes.append(u"{0}: {1}".format(target, note))

            # This is the regenerate the readback depends on. A new element
            # reports no geometry until the document is regenerated, and a
            # readback that sees nothing is a readback that fails; Place at
            # Coordinate lost two builds to the same lie. Removing this and
            # the one above fails 19 checks.
            doc.Regenerate()
            actual = read_box(copy).corners
            if not model.same_shape(actual, link_box.corners):
                diagnose(target, transform, link_box.corners, actual)
                raise Abort(u"The copy of {0} did not land on the link's "
                            u"box. The numbers are in the output window."
                            .format(target))
            done.append((state, target, len(uses.get(target, []))))
    except Abort as exc:
        transaction.RollBack()
        forms.alert(u"Nothing was changed.\n\n{0}".format(to_text(exc)),
                    title=TITLE)
        return
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(u"Nothing was changed. The run stopped with: {0}".format(
            to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(u"Revit rejected the changes, so nothing was copied "
                    u"({0}).".format(to_text(status)), title=TITLE)
        return

    output.print_md(u"#### Done")
    for state, target, moved in done:
        if state == model.NEW:
            output.print_md(u"- {0}: copied in".format(target))
        else:
            output.print_md(u"- {0}: replaced, {1} view(s) and datum(s) "
                            u"moved across".format(target, moved))
    if workset_id is None:
        output.print_md(u"_Not placed on {0}: the model is not workshared "
                        u"or has no such workset._".format(
                            worksets_model.CONVENTIONAL))
    for note in notes:
        output.print_md(u"- {0}".format(note))


if __name__ == "__main__":
    run()
