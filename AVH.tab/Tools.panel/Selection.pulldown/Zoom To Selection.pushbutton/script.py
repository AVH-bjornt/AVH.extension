# -*- coding: utf-8 -*-
"""Zoom the active view to whatever is selected.

Works out one bounding box around the selection and zooms the current
view to it, with a margin. It stays in the view you are in: nothing
opens another view, nothing switches, and Revit's own "no good view"
dialog never appears.

## Nothing is written

This changes what you are looking at, not the model, so there is no
transaction here at all and nothing to roll back. That also means it is
safe on a workshared model with everything checked out by someone else.

## The margin

The larger of 15 percent of the selection's biggest dimension and 500
mm. Without a floor under it, zooming to a door handle fills the screen
with a 20 mm object and you cannot tell where in the building you are.

## Elements the view does not draw

A selected element can be on another level, hidden, or outside the crop.
Revit answers with no bounding box for the view in that case, so the
element's position in the model is used instead and the report says how
many needed that. The alternative, zooming to nothing, looks like a
broken button.

## Unverified

Not yet run in Revit: `UIDocument.GetOpenUIViews`,
`UIView.ZoomAndCenterRectangle`, and whether `get_BoundingBox(view)`
returns nothing for an element the view does not draw, which is what the
fallback above depends on.
"""

__title__ = "Zoom to\nSelection"
__author__ = "AVH"
__doc__ = ("Zoom the active view to the current selection, with a "
           "margin. Stays in the view you are in and changes nothing in "
           "the model.")

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
from avh_selection import model                   # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Zoom to Selection"


def selected_elements(doc, uidoc):
    elements = []
    try:
        ids = uidoc.Selection.GetElementIds()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return elements

    for element_id in ids:
        try:
            element = doc.GetElement(element_id)
        except BaseException:
            element = None
        if element is not None:
            elements.append(element)
    return elements


def box_of(element, view):
    """(low, high) for the element, and whether the view drew it.

    The view box is preferred, being what is actually on screen. An
    element the view does not draw answers with nothing, and then its
    position in the model is the honest second best.
    """
    for source, in_view in ((view, True), (None, False)):
        try:
            box = element.get_BoundingBox(source)
        except BaseException as exc:
            logger.debug(to_text(exc))
            box = None
        if box is None:
            continue
        try:
            return ((box.Min.X, box.Min.Y, box.Min.Z),
                    (box.Max.X, box.Max.Y, box.Max.Z)), in_view
        except BaseException as exc:
            logger.debug(to_text(exc))
    return None, False


def active_ui_view(uidoc, view):
    """The open UIView showing the active view, or None."""
    try:
        for ui_view in uidoc.GetOpenUIViews():
            try:
                if ui_view.ViewId == view.Id:
                    return ui_view
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    view = doc.ActiveView
    if view is None:
        forms.alert(u"There is no active view.", title=TITLE)
        return

    elements = selected_elements(doc, uidoc)
    if not elements:
        forms.alert(u"Select something first, then click the button.",
                    title=TITLE)
        return

    boxes = []
    not_in_view = 0
    for element in elements:
        box, in_view = box_of(element, view)
        if box is None:
            not_in_view += 1
            continue
        if not in_view:
            not_in_view += 1
        boxes.append(box)

    combined = model.pad(model.combine(boxes))
    if combined is None:
        forms.alert(
            u"Nothing in the selection has a size or a position Revit "
            u"will report, so there is nowhere to zoom to.",
            title=TITLE)
        return

    ui_view = active_ui_view(uidoc, view)
    if ui_view is None:
        forms.alert(
            u"The active view is not one of the open windows, so it "
            u"cannot be zoomed. This happens on a sheet or a schedule.",
            title=TITLE)
        return

    low, high = combined
    try:
        ui_view.ZoomAndCenterRectangle(DB.XYZ(low[0], low[1], low[2]),
                                       DB.XYZ(high[0], high[1], high[2]))
    except BaseException as exc:
        forms.alert(
            u"The view would not zoom: {0}".format(to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    note = model.describe(len(elements), not_in_view)
    if note:
        output.print_md(u"### {0}".format(TITLE))
        output.print_md(note)


if __name__ == "__main__":
    run()
