# -*- coding: utf-8 -*-
"""Rename chosen views and sheets as prefix + field + suffix.

Select views and sheets in the Project Browser (or pick them from a
list if nothing is selected). A window shows every chosen name split
into three parts:

    prefix   shared by all of them, typed once
    field    each one's own, edited per row
    suffix   shared by all of them, typed once

The prefix and suffix start as whatever the chosen names already have in
common, so editing the prefix changes every name and leaves each field
as it was. The fields can also be numbered (1, 01, 101 or A onwards).
The new name of each row is shown as you type, in red with the reason
if Revit would refuse it: a character Revit does not allow, two chosen
views landing on one name, or a name another view already has.

Sheets are renamed by their name; sheet numbers are left alone.

## Writing

All the renames go in one transaction, in two passes: every name is
first set to a temporary one, then to its new one, so names can be
swapped among the chosen (A to B and B to A) without either clashing
with the other on the way. Each name is then read back, and if any is
not what was asked, or Revit refuses one, or the commit comes back
rolled back, nothing is renamed and the dialog says which.

## Unverified

Not yet run in Revit: the window itself (lib/avh_rename/window.py),
setting `View.Name` on a sheet, and whether Revit's view name
uniqueness is per view type as the pre-check assumes. Revit refusing a
name the pre-check let through is caught and rolls the run back.
"""

__title__ = "Rename\nViews/Sheets"
__author__ = "AVH"
__doc__ = ("Rename chosen views and sheets: a shared prefix and suffix, "
           "and a field of their own for each.")

import os
import re
import sys
import uuid

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
from avh_rename import model                      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Rename Views and Sheets"

# Views that are not in the Project Browser as views people name.
SKIPPED_TYPES = set([u"Internal", u"ProjectBrowser", u"SystemBrowser",
                     u"Undefined"])


class Item(object):
    """A view or sheet, read once."""

    def __init__(self, element, is_sheet):
        self.element = element
        self.is_sheet = is_sheet
        self.name = to_text(element.Name)
        if is_sheet:
            self.number = to_text(element.SheetNumber)
            self.kind = u"Sheet " + self.number
            # Sheet names may repeat; only the number has to be unique.
            self.group = None
            self.order = (0, self.number, self.name)
        else:
            self.number = u""
            view_type = to_text(view_type_of(element))
            self.kind = re.sub(u"(?<=[a-z])(?=[A-Z])", u" ", view_type)
            self.group = view_type
            self.order = (1, self.kind, self.name)
        self.label = u"{0}  {1}".format(self.kind, self.name)


def view_type_of(view):
    try:
        return view.ViewType
    except BaseException:
        return u""


def candidates(doc):
    found = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            if view.IsTemplate:
                continue
            if to_text(view_type_of(view)) in SKIPPED_TYPES:
                continue
            if getattr(view, "IsTitleblockRevisionSchedule", False):
                continue
            found.append(Item(view, isinstance(view, DB.ViewSheet)))
        except BaseException as exc:
            logger.debug(to_text(exc))
    found.sort(key=lambda item: item.order)
    labels = {}
    for item in found:
        labels.setdefault(item.label, []).append(item)
    for same in labels.values():
        if len(same) > 1:
            for item in same:
                item.label = u"{0}  ({1})".format(item.label,
                                                  id_text(item.element.Id))
    return found


def id_text(element_id):
    # Revit 2024 replaced IntegerValue with Value.
    value = getattr(element_id, "Value", None)
    if value is None:
        value = getattr(element_id, "IntegerValue", u"?")
    return to_text(value)


def selected(found):
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except BaseException:
        return []
    by_id = dict((item.element.Id, item) for item in found)
    chosen = [by_id[i] for i in ids if i in by_id]
    chosen.sort(key=lambda item: item.order)
    return chosen


def pick(found):
    by_label = dict((item.label, item) for item in found)
    answer = forms.SelectFromList.show(
        [item.label for item in found], multiselect=True,
        title=TITLE + u": tick the views and sheets to rename",
        button_name=u"Rename ticked")
    if not answer:
        return []
    if not isinstance(answer, list):
        answer = [answer]
    return [by_label[to_text(a)] for a in answer if to_text(a) in by_label]


def ask(chosen, prefix, suffix, fields, evaluate):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_rename import window
    return window.ask([item.kind for item in chosen],
                      [item.name for item in chosen],
                      prefix, suffix, fields, evaluate)


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return (True if committed is None else status == committed), status


def write(doc, changes):
    """Rename every (item, row) pair. Returns u"" on success, otherwise
    why nothing was renamed."""
    token = uuid.uuid4().hex[:8]
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    current = None
    try:
        for index, (item, row) in enumerate(changes):
            current = item
            item.element.Name = model.temporary_name(index, token)
        for item, row in changes:
            current = item
            item.element.Name = row.new
        current = None
        wrong = [(item, row) for item, row in changes
                 if to_text(item.element.Name) != row.new]
        if wrong:
            transaction.RollBack()
            item, row = wrong[0]
            return (u"{0} came back as \"{1}\" instead of \"{2}\", so "
                    u"nothing was renamed.".format(
                        item.label, to_text(item.element.Name), row.new))
    except BaseException as exc:
        transaction.RollBack()
        where = u" at {0}".format(current.label) if current else u""
        logger.error(to_text(exc))
        return (u"Revit refused a rename{0}, so nothing was renamed: "
                u"{1}".format(where, to_text(exc)))
    ok, status = commit_checked(transaction)
    if not ok:
        return (u"Revit rejected the change, so nothing was renamed "
                u"({0}).".format(to_text(status)))
    return u""


def report(changes):
    lines = [u"### {0}".format(TITLE),
             u"{0} renamed.".format(len(changes)), u"",
             u"| | Was | Now |", u"| --- | --- | --- |"]
    for item, row in changes:
        lines.append(u"| {0} {1} | {2} | {3} |".format(
            output.linkify(item.element.Id), item.kind,
            row.old.replace(u"|", u"/"), row.new.replace(u"|", u"/")))
    output.print_md(u"\n".join(lines))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    found = candidates(doc)
    if not found:
        forms.alert(u"This model has no views or sheets to rename.",
                    title=TITLE)
        return

    chosen = selected(found) or pick(found)
    if not chosen:
        return

    chosen_ids = set(item.element.Id for item in chosen)
    taken = [(item.group, item.name) for item in found
             if item.group is not None and item.element.Id not in chosen_ids]
    pairs = [(item.name, item.group) for item in chosen]

    def evaluate(prefix, suffix, fields):
        return model.plan(pairs, prefix, suffix, fields, taken)

    prefix, suffix = model.affixes([item.name for item in chosen])
    fields = [model.field_of(item.name, prefix, suffix) for item in chosen]
    answer = ask(chosen, prefix, suffix, fields, evaluate)
    if answer is None:
        return

    rows = evaluate(*answer)
    stopped = model.blocking(rows)
    if stopped:
        forms.alert(u"Nothing was renamed: \"{0}\" cannot be used, {1}.".format(
            stopped[0].new, stopped[0].problem), title=TITLE)
        return
    changes = [(item, row) for item, row in zip(chosen, rows) if row.changes]
    if not changes:
        forms.alert(u"No name would change.", title=TITLE)
        return

    why = write(doc, changes)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(changes)


if __name__ == "__main__":
    run()
