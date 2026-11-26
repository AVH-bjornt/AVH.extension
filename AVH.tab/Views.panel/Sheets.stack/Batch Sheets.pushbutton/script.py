# -*- coding: utf-8 -*-
"""Make a batch of sheets with one title block and numbered as you choose.

A window asks for the title block, how many sheets, and each sheet's
number and name, built the way Rename Views/Sheets builds a name:

    number   prefix + field + suffix, the field counting up from a start
             value (1, 01, 101 or a letter) and editable per row
    name     prefix + field + suffix, the field typed per row, or
             counting up from a start value the same way

The sheets can go into a sheet collection (Revit 2025 and later): none,
one already in the model, or a new one named in the window, made in the
same transaction as the sheets.

The numbering starts where the model's own leaves off: after A-105 it
offers "A-", "106". The title block offered first is the one most used
on the sheets already in the model. Every sheet number and name is shown
as you type, in red with the reason if Revit would refuse it: a
character Revit does not allow, a number another sheet already has, or
two new sheets landing on one number.

## Shift-click: sheets from views

Shift-click makes one sheet per view and places the view in the middle
of it, to be moved into place by hand. The views are the ones selected
in the Project Browser, or, with none selected, ticked in a list. Views
already on a sheet are left out and named; schedules, legends, sheets
and templates are not offered. Each sheet's name starts as its view's
name, and the window is otherwise the same, with the count fixed at one
sheet per view.

"Place views" can instead align the new views to a view already on
another sheet, picked from a list after Create sheets (with a sheet
collection dropdown, as in Align Views). Each view is placed, then moved
so the top left of its crop region is where the picked view's is, the
way Align Views does it (lib/avh_viewports/corners.py). A view whose
crop region is off stays in the middle and is reported. One that is
moved but does not land within 0.1 mm makes the whole run roll back.

## Writing

All the sheets are made in one transaction. Each one's number and name
are read back, and if any is not what was asked, Revit refuses one, or
the commit comes back rolled back, no sheet is made and the dialog says
which. Nothing is written to disk.

## Unverified

Not yet run in Revit: the window (lib/avh_sheets/window.py, built like
the Rename Views window, which has run), `ViewSheet.Create`, and
`FamilySymbol.Activate` on a title block type that is not yet active,
`SheetCollection.Create` and setting `ViewSheet.SheetCollectionId`,
and for shift-click `Viewport.CanAddViewToSheet`, `Viewport.Create` and
the middle of the sheet taken from the title block's bounding box in
the sheet (falling back to `ViewSheet.Outline`, then the origin). The
crop corner arithmetic is Align Views', which has run in Revit 2025. The
collection each sheet ends up in is read back, so a set that Revit
ignores rolls the run back rather than passing silently.
Whether Revit compares sheet numbers ignoring case is not confirmed; the
check here ignores case, so it can only refuse more than Revit would.
"""

__title__ = "Batch\nSheets"
__author__ = "AVH"
__doc__ = "Make a number of sheets at once with a chosen title block, numbered and named as prefix + field + suffix, into a new or existing sheet collection if you like. Shift-click: one sheet per selected view, the view placed in the middle."

import os
import re
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
from avh_sheets import model                      # noqa: E402
from avh_viewports import corners                 # noqa: E402
from avh_viewports import model as viewports      # noqa: E402
from avh_viewports import picker                  # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Batch Sheets"
NO_TITLEBLOCK = u"(no title block)"

# View types that are not placed as a viewport: sheets themselves,
# schedules and reports (placed another way), legends (which may go on
# many sheets), and views that are not in the Project Browser as views.
NOT_PLACED = set([u"DrawingSheet", u"Schedule", u"ColumnSchedule",
                  u"PanelSchedule", u"Legend", u"Report", u"CostReport",
                  u"LoadsReport", u"PresureLossReport", u"Internal",
                  u"ProjectBrowser", u"SystemBrowser", u"Undefined"])


def safe(call, default=None):
    try:
        return call()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return default


def type_name(symbol):
    name = safe(lambda: symbol.Name)
    if not name:
        parameter = safe(lambda: symbol.get_Parameter(
            DB.BuiltInParameter.SYMBOL_NAME_PARAM))
        name = safe(lambda: parameter.AsString()) if parameter else None
    return to_text(name)


def titleblock_types(doc):
    """[(label, symbol)] sorted by label."""
    found = []
    collector = DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType()
    for symbol in collector:
        family = to_text(safe(lambda: symbol.FamilyName, u""))
        label = u"{0} : {1}".format(family, type_name(symbol)) if family \
            else type_name(symbol)
        found.append((label, symbol))
    found.sort(key=lambda pair: pair[0].lower())
    return found


def most_used(doc, types):
    """Index in `types` of the title block on the most sheets, else 0."""
    counts = {}
    collector = DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType()
    for instance in collector:
        type_id = safe(lambda: instance.GetTypeId())
        if type_id is not None:
            counts[type_id] = counts.get(type_id, 0) + 1
    best, best_count = 0, 0
    for index, (_label, symbol) in enumerate(types):
        count = counts.get(symbol.Id, 0)
        if count > best_count:
            best, best_count = index, count
    return best


def sheet_numbers(doc):
    numbers = []
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        number = safe(lambda: sheet.SheetNumber)
        if number:
            numbers.append(to_text(number))
    return numbers


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


class ViewChoice(object):
    """A view that could go on a new sheet, read once."""

    def __init__(self, view):
        self.element = view
        self.name = to_text(view.Name)
        view_type = to_text(safe(lambda: view.ViewType, u""))
        self.kind = re.sub(u"(?<=[a-z])(?=[A-Z])", u" ", view_type)
        self.label = u"{0}  {1}".format(self.kind, self.name)
        self.order = (self.kind, self.name)


def placed_on(doc):
    """{view id: number of the sheet it is on}."""
    numbers = {}
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        numbers[sheet.Id] = to_text(safe(lambda: sheet.SheetNumber, u""))
    found = {}
    for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
        view_id = safe(lambda: viewport.ViewId)
        if view_id is not None:
            found[view_id] = numbers.get(safe(lambda: viewport.SheetId),
                                         u"?")
    return found


def placeable_views(doc):
    """Every view that could be placed on a sheet, sorted."""
    found = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if isinstance(view, DB.ViewSheet):
            continue
        if safe(lambda: view.IsTemplate, True):
            continue
        if to_text(safe(lambda: view.ViewType, u"")) in NOT_PLACED:
            continue
        found.append(ViewChoice(view))
    found.sort(key=lambda choice: choice.order)
    labels = {}
    for choice in found:
        labels.setdefault(choice.label, []).append(choice)
    for same in labels.values():
        if len(same) > 1:
            for choice in same:
                value = getattr(choice.element.Id, "Value", None)
                if value is None:
                    value = getattr(choice.element.Id, "IntegerValue", u"?")
                choice.label = u"{0}  ({1})".format(choice.label,
                                                    to_text(value))
    return found


def choose_views(doc):
    """(views to place, [(view, sheet number)] left out because they are
    already on a sheet). The selected views, else those ticked in a
    list of views not yet on a sheet."""
    found = placeable_views(doc)
    on_sheet = placed_on(doc)
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except BaseException:
        ids = []
    by_id = dict((choice.element.Id, choice) for choice in found)
    chosen = [by_id[i] for i in ids if i in by_id]
    if not chosen:
        free = [c for c in found if c.element.Id not in on_sheet]
        if not free:
            return [], []
        by_label = dict((c.label, c) for c in free)
        answer = forms.SelectFromList.show(
            [c.label for c in free], multiselect=True,
            title=TITLE + u": tick the views to put on sheets",
            button_name=u"Make sheets")
        if not answer:
            return [], []
        if not isinstance(answer, list):
            answer = [answer]
        chosen = [by_label[to_text(a)] for a in answer
                  if to_text(a) in by_label]
    chosen.sort(key=lambda choice: choice.order)
    left_out = [(c, on_sheet[c.element.Id]) for c in chosen
                if c.element.Id in on_sheet]
    views = [c for c in chosen if c.element.Id not in on_sheet]
    return views, left_out


class Placed(object):
    """A view already on a sheet, to align the new views to."""

    def __init__(self, doc, viewport, view, sheet):
        self.viewport = viewport
        self.view = view
        self.sheet = sheet
        self.collection = picker.collection_name(doc, sheet)
        self.label = viewports.label(sheet.SheetNumber, sheet.Name,
                                     view.Name)


def placed_views(doc):
    found = []
    for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
        view = safe(lambda: doc.GetElement(viewport.ViewId))
        sheet = safe(lambda: doc.GetElement(viewport.SheetId))
        if view is None or sheet is None:
            continue
        found.append(Placed(doc, viewport, view, sheet))
    found.sort(key=lambda p: p.label)
    return found


def choose_alignment(doc):
    """(corner, label) of the placed view the new views are aligned to,
    or (None, why not) with why u"" when the person cancelled."""
    placed = placed_views(doc)
    if not placed:
        return None, u"no view is on a sheet yet to align to"
    source = picker.pick(forms, placed, False,
                         TITLE + u": align the new views to which view?",
                         u"Align to this")
    if source is None:
        return None, u""
    corner, why = corners.crop_corner(source.view, source.viewport)
    if corner is None:
        return None, u"{0} cannot be aligned to: {1}".format(source.label,
                                                            why)
    return corner, source.label


def align_placed(doc, placed, corner, notes):
    """Move each placed (viewport, sheet, view, row) so its crop's top
    left is on `corner`. Returns why it failed, or u"". A view whose crop
    region is off has nothing to align by: it stays in the middle and is
    noted. Any other view whose corner cannot be read stops the run."""
    doc.Regenerate()
    moved = []
    for viewport, sheet, view, row in placed:
        if not safe(lambda: view.CropBoxActive, False):
            notes[row.number] = u"in the middle: its crop region is off"
            continue
        own, why = corners.crop_corner(view, viewport)
        if own is None:
            return (u"{0} could not be aligned, {1}, so no sheet was "
                    u"made.".format(to_text(view.Name), why))
        dx, dy = viewports.shift(corner, own)
        centre = viewport.GetBoxCenter()
        viewport.SetBoxCenter(DB.XYZ(centre.X + dx, centre.Y + dy,
                                     centre.Z))
        moved.append((viewport, view, row))
    # The sheet transform follows the viewport only after a regenerate.
    doc.Regenerate()
    for viewport, view, row in moved:
        after, why = corners.crop_corner(view, viewport)
        if after is None or not viewports.landed(corner, after):
            return (u"{0} did not land on the corner it was aligned to, "
                    u"so no sheet was made.".format(to_text(view.Name)))
        notes[row.number] = u"aligned"
    return u""


def sheet_middle(doc, sheet):
    """The middle of the sheet: of its title block, else of its outline,
    else the origin."""
    blocks = safe(lambda: list(
        DB.FilteredElementCollector(doc, sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()), [])
    for block in blocks:
        box = safe(lambda: block.get_BoundingBox(sheet))
        if box is not None and box.Max.X > box.Min.X:
            return DB.XYZ((box.Min.X + box.Max.X) / 2.0,
                          (box.Min.Y + box.Max.Y) / 2.0, 0.0)
    outline = safe(lambda: sheet.Outline)
    if outline is not None and outline.Max.U > outline.Min.U:
        return DB.XYZ((outline.Min.U + outline.Max.U) / 2.0,
                      (outline.Min.V + outline.Max.V) / 2.0, 0.0)
    return DB.XYZ(0.0, 0.0, 0.0)


def sheet_collections(doc):
    """[(name, collection)] sorted by name, or None where this Revit has
    no sheet collections (before 2025)."""
    cls = getattr(DB, "SheetCollection", None)
    if cls is None:
        return None
    found = []
    for collection in DB.FilteredElementCollector(doc).OfClass(cls):
        name = safe(lambda: collection.Name)
        if name is not None:
            found.append((to_text(name), collection))
    found.sort(key=lambda pair: pair[0].lower())
    return found


def ask(titleblocks, chosen, number, count, evaluate, collections,
        views=None, names=None):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_sheets import window
    return window.ask(titleblocks, chosen, number, count, evaluate,
                      collections, views, names)


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return (True if committed is None else status == committed), status


def create(doc, symbol, rows, collection=None, views=None, align=None,
           notes=None):
    """Make a sheet per row. `collection`: None, ("existing", element) or
    ("new", name) for the sheet collection they go into. `views`: None,
    or a view per row to place in the middle of its sheet; `align`:
    None, or the sheet point to align each view's crop top left to.
    `notes` gets how each view was placed, by sheet number. Returns
    (sheets, why): the sheets made, or [] and why none were."""
    if notes is None:
        notes = {}
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    current = None
    made = []
    try:
        if symbol is not None:
            type_id = symbol.Id
            if not safe(lambda: symbol.IsActive, True):
                symbol.Activate()
        else:
            type_id = DB.ElementId.InvalidElementId
        collection_id = None
        if collection is not None:
            kind, target = collection
            if kind == u"new":
                target = DB.SheetCollection.Create(doc, target)
            collection_id = target.Id
        for row in rows:
            current = row
            sheet = DB.ViewSheet.Create(doc, type_id)
            sheet.SheetNumber = row.number
            sheet.Name = row.name
            if collection_id is not None:
                sheet.SheetCollectionId = collection_id
            made.append((sheet, row))
        current = None
        placed = []
        if views is not None:
            # The new title blocks have no size until Revit regenerates.
            doc.Regenerate()
            for (sheet, row), view in zip(made, views):
                current = row
                if not DB.Viewport.CanAddViewToSheet(doc, sheet.Id,
                                                     view.Id):
                    transaction.RollBack()
                    return [], (u"Revit will not put {0} on sheet {1}, so "
                                u"no sheet was made.".format(
                                    to_text(view.Name), row.number))
                viewport = DB.Viewport.Create(doc, sheet.Id, view.Id,
                                              sheet_middle(doc, sheet))
                placed.append((viewport, sheet, view, row))
            current = None
        for viewport, sheet, view, row in placed:
            if (viewport is None or viewport.SheetId != sheet.Id or
                    viewport.ViewId != view.Id):
                transaction.RollBack()
                return [], (u"{0} did not go onto sheet {1}, so no sheet "
                            u"was made.".format(to_text(view.Name),
                                                row.number))
        if align is not None and placed:
            why = align_placed(doc, placed, align, notes)
            if why:
                transaction.RollBack()
                return [], why
        for sheet, row in made:
            got = (to_text(sheet.SheetNumber), to_text(sheet.Name))
            if got != (row.number, row.name):
                transaction.RollBack()
                return [], (u"Sheet {0} came back as {1} \"{2}\", so no "
                            u"sheet was made.".format(row.number, got[0],
                                                      got[1]))
            if (collection_id is not None and
                    sheet.SheetCollectionId != collection_id):
                transaction.RollBack()
                return [], (u"Sheet {0} did not go into the sheet "
                            u"collection, so no sheet was made.".format(
                                row.number))
    except BaseException as exc:
        transaction.RollBack()
        where = u" at sheet {0}".format(current.number) if current else u""
        logger.error(to_text(exc))
        return [], (u"Revit refused{0}, so no sheet was made: {1}".format(
            where, to_text(exc)))
    ok, status = commit_checked(transaction)
    if not ok:
        return [], (u"Revit rejected the change, so no sheet was made "
                    u"({0}).".format(to_text(status)))
    return made, u""


def report(made, titleblock, collection_name=None, views=None,
           left_out=(), aligned_to=None, notes=None):
    where = (u" in the sheet collection {0}".format(collection_name)
             if collection_name else u"")
    lines = [u"### {0}".format(TITLE),
             u"{0} sheet(s) made with {1}{2}.".format(len(made), titleblock,
                                                     where)]
    if views is not None:
        notes = notes or {}
        if aligned_to:
            lines[-1] += u" The views are aligned to {0}.".format(aligned_to)
            middle = u"aligned"
        else:
            lines[-1] += (u" Each view is in the middle of its sheet, to "
                          u"be moved into place.")
            middle = u"middle"
        lines += [u"", u"| | Number | Name | View | Placed |",
                  u"| :--- | :--- | :--- | :--- | :--- |"]
        for (sheet, row), view in zip(made, views):
            lines.append(u"| {0} | {1} | {2} | {3} | {4} |".format(
                output.linkify(sheet.Id), row.number.replace(u"|", u"/"),
                row.name.replace(u"|", u"/"),
                view.label.replace(u"|", u"/"),
                notes.get(row.number, middle).replace(u"|", u"/")))
    else:
        lines += [u"", u"| | Number | Name |", u"| :--- | :--- | :--- |"]
        for sheet, row in made:
            lines.append(u"| {0} | {1} | {2} |".format(
                output.linkify(sheet.Id), row.number.replace(u"|", u"/"),
                row.name.replace(u"|", u"/")))
    output.print_md(u"\n".join(lines))
    if left_out:
        output.print_md(left_out_text(left_out))


def left_out_text(left_out):
    return u"Left out, already on a sheet: " + u", ".join(
        u"{0} (sheet {1})".format(choice.label, number)
        for choice, number in left_out) + u"."


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if safe(lambda: doc.IsFamilyDocument, False):
        forms.alert(u"This is a family document; it has no sheets.",
                    title=TITLE)
        return

    views, left_out = None, []
    if shift_clicked():
        views, left_out = choose_views(doc)
        if not views:
            if left_out:
                forms.alert(u"No sheet was made: every chosen view is "
                            u"already on a sheet. " +
                            left_out_text(left_out), title=TITLE)
            return
        if left_out:
            forms.alert(left_out_text(left_out) + u" Sheets are made for "
                        u"the other {0}.".format(len(views)), title=TITLE)

    types = titleblock_types(doc)
    chosen = most_used(doc, types) if types else 0
    labels = [label for label, _symbol in types] + [NO_TITLEBLOCK]
    taken = sheet_numbers(doc)
    collections = sheet_collections(doc)

    def evaluate(*values):
        return model.plan(*values, taken_numbers=taken)

    answer = ask(labels, chosen, model.suggest(taken),
                 len(views) if views else 1, evaluate,
                 None if collections is None
                 else [name for name, _c in collections],
                 [v.label for v in views] if views else None,
                 [v.name for v in views] if views else None)
    if answer is None:
        return

    rows = evaluate(*answer["values"])
    if not rows:
        return
    if views and len(rows) != len(views):
        forms.alert(u"No sheet was made: {0} sheet(s) for {1} view(s)."
                    .format(len(rows), len(views)), title=TITLE)
        return
    stopped = model.blocking(rows)
    if stopped:
        forms.alert(u"No sheet was made: sheet {0} \"{1}\" cannot be made, "
                    u"{2}.".format(stopped[0].number, stopped[0].name,
                                   stopped[0].problem), title=TITLE)
        return

    index = answer["titleblock"]
    if 0 <= index < len(types):
        label, symbol = types[index]
    else:
        label, symbol = NO_TITLEBLOCK, None

    collection, collection_name = None, None
    choice = answer.get("collection")
    if choice is not None and collections is not None:
        kind, value = choice
        if kind == u"new":
            problem = model.collection_problem(
                value, [name for name, _c in collections])
            if problem:
                forms.alert(u"No sheet was made: the new sheet collection "
                            u"cannot be called \"{0}\", {1}.".format(
                                value, problem), title=TITLE)
                return
            collection, collection_name = (u"new", value), value
        elif 0 <= value < len(collections):
            collection_name, element = collections[value]
            collection = (u"existing", element)

    align, aligned_to = None, None
    if views and answer.get("align"):
        align, aligned_to = choose_alignment(doc)
        if align is None:
            if aligned_to:
                forms.alert(u"No sheet was made: " + aligned_to + u".",
                            title=TITLE)
            return

    notes = {}
    made, why = create(doc, symbol, rows, collection,
                       [v.element for v in views] if views else None,
                       align, notes)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(made, label, collection_name, views, left_out, aligned_to, notes)


if __name__ == "__main__":
    run()
