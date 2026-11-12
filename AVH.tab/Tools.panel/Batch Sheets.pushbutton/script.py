# -*- coding: utf-8 -*-
"""Make a batch of sheets with one title block and numbered as you choose.

A window asks for the title block, how many sheets, and each sheet's
number and name, built the way Rename Views/Sheets builds a name:

    number   prefix + field + suffix, the field counting up from a start
             value (1, 01, 101 or a letter) and editable per row
    name     prefix + field + suffix, the field typed per row

The numbering starts where the model's own leaves off: after A-105 it
offers "A-", "106". The title block offered first is the one most used
on the sheets already in the model. Every sheet number and name is shown
as you type, in red with the reason if Revit would refuse it: a
character Revit does not allow, a number another sheet already has, or
two new sheets landing on one number.

## Writing

All the sheets are made in one transaction. Each one's number and name
are read back, and if any is not what was asked, Revit refuses one, or
the commit comes back rolled back, no sheet is made and the dialog says
which. Nothing is written to disk.

## Unverified

Not yet run in Revit: the window (lib/avh_sheets/window.py, built like
the Rename Views window, which has run), `ViewSheet.Create`, and
`FamilySymbol.Activate` on a title block type that is not yet active.
Whether Revit compares sheet numbers ignoring case is not confirmed; the
check here ignores case, so it can only refuse more than Revit would.
"""

__title__ = "Batch\nSheets"
__author__ = "AVH"
__doc__ = ("Make a number of sheets at once with a chosen title block, "
           "numbered and named as prefix + field + suffix.")

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
from avh_sheets import model                      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Batch Sheets"
NO_TITLEBLOCK = u"(no title block)"


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


def ask(titleblocks, chosen, number, count, evaluate):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_sheets import window
    return window.ask(titleblocks, chosen, number, count, evaluate)


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return (True if committed is None else status == committed), status


def create(doc, symbol, rows):
    """Make a sheet per row. Returns (sheets, why): the sheets made, or
    [] and why none were."""
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
        for row in rows:
            current = row
            sheet = DB.ViewSheet.Create(doc, type_id)
            sheet.SheetNumber = row.number
            sheet.Name = row.name
            made.append((sheet, row))
        current = None
        for sheet, row in made:
            got = (to_text(sheet.SheetNumber), to_text(sheet.Name))
            if got != (row.number, row.name):
                transaction.RollBack()
                return [], (u"Sheet {0} came back as {1} \"{2}\", so no "
                            u"sheet was made.".format(row.number, got[0],
                                                      got[1]))
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


def report(made, titleblock):
    lines = [u"### {0}".format(TITLE),
             u"{0} sheet(s) made with {1}.".format(len(made), titleblock),
             u"", u"| | Number | Name |", u"| --- | --- | --- |"]
    for sheet, row in made:
        lines.append(u"| {0} | {1} | {2} |".format(
            output.linkify(sheet.Id), row.number.replace(u"|", u"/"),
            row.name.replace(u"|", u"/")))
    output.print_md(u"\n".join(lines))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if safe(lambda: doc.IsFamilyDocument, False):
        forms.alert(u"This is a family document; it has no sheets.",
                    title=TITLE)
        return

    types = titleblock_types(doc)
    chosen = most_used(doc, types) if types else 0
    labels = [label for label, _symbol in types] + [NO_TITLEBLOCK]
    taken = sheet_numbers(doc)

    def evaluate(*values):
        return model.plan(*values, taken_numbers=taken)

    answer = ask(labels, chosen, model.suggest(taken), 1, evaluate)
    if answer is None:
        return

    rows = evaluate(*answer["values"])
    if not rows:
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

    made, why = create(doc, symbol, rows)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(made, label)


if __name__ == "__main__":
    run()
