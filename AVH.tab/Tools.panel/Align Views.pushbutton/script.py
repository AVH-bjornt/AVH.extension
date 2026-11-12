# -*- coding: utf-8 -*-
"""Align views on other sheets to one view, by the top left of the crop.

Select a viewport on a sheet (or pick a placed view from a list), then
tick the views to line up with it. Each ticked viewport is moved on its
sheet so that the top left corner of its view's crop region lands where
the chosen view's does.

## Why the crop region and not the viewport box

The viewport box grows with any tag, dimension or text that reaches
past the crop, so two views with identical crops can have different
boxes, and aligning boxes would put the drawings themselves out of
line. The crop region is where the drawing is.

## How the corner is found

Revit 2023 added two transforms: `View.GetModelToProjectionTransforms`
gives, for each crop region of a view, its boundary in the model and the
map from model to the view's projection; `Viewport.
GetProjectionToSheetTransform` maps that projection onto the sheet.
Every point of the crop boundary goes through both, and the top left of
the result is the corner. A view split into several crop regions uses
all of them.

## And then it is checked

After moving, the document is regenerated and every corner is worked
out again from scratch and compared with the source's. A viewport that
did not land within 0.1 mm is reported, and if any did not land the
whole run is rolled back, so the sheets are either all aligned or all
as they were.

## Unverified

Not yet run in Revit: `View.GetModelToProjectionTransforms`,
`TransformWithBoundary.GetBoundary` and `GetModelToProjectionTransform`,
`Viewport.GetProjectionToSheetTransform`, and whether the sheet
transform is current before a regenerate (the code assumes not and
regenerates). The approach follows a forum post that reports it working
(Autodesk Revit API forum, "Align viewports by their cropbox using new
API methods", 2023).
"""

__title__ = "Align\nViews"
__author__ = "AVH"
__doc__ = ("Align views on other sheets to one view: the top left corner "
           "of each crop region is moved onto the chosen view's.")

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
from avh_viewports import model                   # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Align Views"


class Placed(object):
    """A viewport, its view and its sheet, read once."""

    def __init__(self, viewport, view, sheet):
        self.viewport = viewport
        self.view = view
        self.sheet = sheet
        self.label = model.label(sheet.SheetNumber, sheet.Name, view.Name)


def placed_views(doc):
    found = []
    for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
        try:
            view = doc.GetElement(viewport.ViewId)
            sheet = doc.GetElement(viewport.SheetId)
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        if view is None or sheet is None:
            continue
        found.append(Placed(viewport, view, sheet))
    found.sort(key=lambda p: p.label)
    return found


def selected_viewport(placed):
    """The placed view whose viewport is selected, or None."""
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except BaseException:
        return None
    by_id = dict((p.viewport.Id, p) for p in placed)
    chosen = [by_id[i] for i in ids if i in by_id]
    return chosen[0] if len(chosen) == 1 else None


def pick(placed, multiselect, title, button):
    by_label = dict((p.label, p) for p in placed)
    answer = forms.SelectFromList.show(
        [p.label for p in placed], multiselect=multiselect, title=title,
        button_name=button)
    if not answer:
        return [] if multiselect else None
    if not isinstance(answer, list):
        answer = [answer]
    chosen = [by_label[to_text(a)] for a in answer if to_text(a) in by_label]
    if multiselect:
        return chosen
    return chosen[0] if chosen else None


def crop_corner(placed):
    """(x, y) on the sheet of the top left of the view's crop, or a
    reason it could not be found, as (None, reason)."""
    view = placed.view
    try:
        if not view.CropBoxActive:
            return None, u"its crop region is off"
    except BaseException:
        return None, u"it has no crop region"
    try:
        regions = list(view.GetModelToProjectionTransforms())
        to_sheet = placed.viewport.GetProjectionToSheetTransform()
    except BaseException as exc:
        return None, u"Revit would not give its transforms: {0}".format(
            to_text(exc))
    points = []
    for region in regions:
        try:
            to_projection = region.GetModelToProjectionTransform()
            boundary = region.GetBoundary()
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        if boundary is None:
            continue
        for curve in boundary:
            try:
                for point in curve.Tessellate():
                    on_sheet = to_sheet.OfPoint(to_projection.OfPoint(point))
                    points.append((on_sheet.X, on_sheet.Y))
            except BaseException as exc:
                logger.debug(to_text(exc))
    corner = model.top_left(points)
    if corner is None:
        return None, u"its crop boundary could not be read"
    return corner, u""


def is_pinned(element):
    try:
        return bool(element.Pinned)
    except BaseException:
        return False


def report(source, source_corner, rows):
    lines = [
        u"### {0}".format(TITLE),
        u"Aligned to **{0}**, crop top left at X {1}, Y {2} on its "
        u"sheet.".format(source.label, model.mm(source_corner[0]),
                         model.mm(source_corner[1])),
        u"",
        u"| View | Result |",
        u"| --- | --- |",
    ]
    for placed, text in rows:
        lines.append(u"| {0} {1} | {2} |".format(
            output.linkify(placed.sheet.Id), placed.label.replace(u"|", u"/"),
            text))
    output.print_md(u"\n".join(lines))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    placed = placed_views(doc)
    if len(placed) < 2:
        forms.alert(u"Fewer than two views are placed on sheets, so there "
                    u"is nothing to align.", title=TITLE)
        return

    source = selected_viewport(placed)
    if source is None:
        source = pick(placed, False, TITLE + u": align to which view?",
                      u"Align to this")
    if source is None:
        return
    source_corner, why = crop_corner(source)
    if source_corner is None:
        forms.alert(u"{0} cannot be aligned to: {1}.".format(
            source.label, why), title=TITLE)
        return

    others = [p for p in placed if p is not source]
    targets = pick(others, True,
                   TITLE + u": tick the views to line up with " +
                   source.label, u"Align ticked")
    if not targets:
        return

    rows = []
    movable = []
    for target in targets:
        if is_pinned(target.viewport):
            rows.append((target, u"skipped: the viewport is pinned"))
            continue
        corner, why = crop_corner(target)
        if corner is None:
            rows.append((target, u"skipped: " + why))
            continue
        movable.append((target, corner))

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        for target, corner in movable:
            dx, dy = model.shift(source_corner, corner)
            centre = target.viewport.GetBoxCenter()
            target.viewport.SetBoxCenter(DB.XYZ(centre.X + dx,
                                                centre.Y + dy, centre.Z))
        # The sheet transform is worked out from the viewport's place,
        # which Revit only brings up to date on regeneration.
        doc.Regenerate()
        missed = 0
        for target, corner in movable:
            after, why = crop_corner(target)
            if after is None or not model.landed(source_corner, after):
                missed += 1
                rows.append((target, u"**did not land**: {0}".format(
                    why or u"corner at X {0}, Y {1}".format(
                        model.mm(after[0]), model.mm(after[1])))))
            else:
                dx, dy = model.shift(source_corner, corner)
                rows.append((target, u"moved {0} across, {1} up".format(
                    model.mm(dx), model.mm(dy))))
        if missed:
            transaction.RollBack()
            report(source, source_corner, rows)
            forms.alert(u"{0} view(s) did not land on the corner, so "
                        u"nothing was moved. The output window lists "
                        u"them.".format(missed), title=TITLE)
            return
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(u"Nothing was moved. The run stopped with: {0}".format(
            to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(u"Revit rejected the change, so nothing was moved "
                    u"({0}).".format(to_text(status)), title=TITLE)
        return
    report(source, source_corner, rows)


if __name__ == "__main__":
    run()
