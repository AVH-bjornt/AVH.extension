# -*- coding: utf-8 -*-
"""Flip visibility of the bubbles at the ends of selected grids.

Adapted from the "Flip Grid Ends" tool in the pyApex pyRevit extension
(https://apex-project.github.io/pyApex), rewritten here for the current
pyrevit IronPython API. Two things from the original were dropped rather
than ported:

- the fallback branch for pyRevit releases older than 4.5, which reached
  the host document through `scriptutils`/`revitutils`. AVH does not run
  a pyRevit that old, and an untestable fallback path is a liability, not
  a safety net.
- category name matching ("Grid" as a substring of `Category.Name`) to
  decide what counts as a grid. `Category.Name` is localized, so this
  would misbehave on any non English Revit UI. Selection here checks
  `isinstance(element, DB.Grid)` instead, which is language independent.

Behaviour is otherwise unchanged: if both bubbles on a grid are visible,
one is hidden and the other is left. If only one end is visible, the
visible end swaps to the other side. If neither end is visible, both are
shown.
"""

__title__ = "Flip\nGrid Ends"
__author__ = "AVH"
__doc__ = ("Flip visibility of the bubbles at the ends of selected grids. "
           "If both bubbles were visible, only one is left. If one end was "
           "visible, the visible end swaps to the other side.")

from pyrevit import revit, DB, forms, script
from Autodesk.Revit import UI
from Autodesk.Revit.Exceptions import OperationCanceledException

output = script.get_output()
logger = script.get_logger()

TITLE = __title__.replace("\n", " ")


class GridSelectionFilter(UI.Selection.ISelectionFilter):
    """Restrict picking on screen to Grid elements only."""

    def AllowElement(self, element):
        return isinstance(element, DB.Grid)

    def AllowReference(self, reference, point):
        return False


def get_selected_grids(uidoc):
    """Grids already selected in the model, else prompt the user to pick."""
    selection = revit.get_selection()
    grids = [el for el in selection.elements if isinstance(el, DB.Grid)]
    if grids:
        return grids

    forms.alert(
        "Select the grids to flip, then click Finish on the ribbon.",
        title=TITLE,
    )
    try:
        refs = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            GridSelectionFilter(),
            "Select grids, then click Finish",
        )
    except OperationCanceledException:
        return []

    doc = uidoc.Document
    return [doc.GetElement(ref.ElementId) for ref in refs]


def flip_grid(grid, view):
    """Toggle which end bubble(s) show. Returns True if anything changed."""
    if not grid.CanBeVisibleInView(view):
        return False

    ends = (DB.DatumEnds.End0, DB.DatumEnds.End1)
    already_hid_one = False
    changed = False

    for end in ends:
        if grid.IsBubbleVisibleInView(end, view) and not already_hid_one:
            grid.HideBubbleInView(end, view)
            already_hid_one = True
            changed = True
        elif not grid.IsBubbleVisibleInView(end, view):
            grid.ShowBubbleInView(end, view)
            changed = True

    return changed


def run():
    doc = revit.doc
    uidoc = revit.uidoc

    grids = get_selected_grids(uidoc)
    if not grids:
        return

    active_view = doc.ActiveView
    flipped = 0

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    for grid in grids:
        if flip_grid(grid, active_view):
            flipped += 1

    if flipped:
        transaction.Commit()
    else:
        transaction.RollBack()

    if flipped == 0:
        forms.alert("Nothing flipped.", title=TITLE)
    elif flipped != len(grids):
        forms.alert(
            "{0} of {1} grids were flipped.".format(flipped, len(grids)),
            title=TITLE,
        )


if __name__ == "__main__":
    run()
