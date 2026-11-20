# -*- coding: utf-8 -*-
"""Make a number of dependent views under each chosen view at once.

Select the parent views in the Project Browser (or tick them in a list
if nothing that can take dependents is selected). A window asks how many
dependents each one gets and builds their names the way Rename
Views/Sheets builds a name:

    prefix   the same for every new view; {parent} in it stands for
             each parent's own name, so the default "{parent} - " gives
             "Grunnmynd - 1", "Grunnmynd - 2" under Grunnmynd
    field    each new view's own, counting up per parent from a start
             value (1, 01, 101 or a letter) and editable per row
    suffix   the same for every new view, {parent} allowed too

Only views Revit says can be duplicated as dependent are offered; a
dependent view itself cannot have dependents. Every new name is shown as
you type, in red with the reason if Revit would refuse it: a character
Revit does not allow, two new views landing on one name, or a name
another view of the same view type already has.

## Writing

All the views are made in one transaction: each parent is duplicated as
dependent, the copy is named, and then every name and parent is read
back. If any is not what was asked, Revit refuses one, or the commit
comes back rolled back, no view is made and the dialog says which.
Nothing is written to disk.

## Unverified

Not yet run in Revit: the window (lib/avh_dependents/window.py, built
like the Batch Sheets window, which has run), `View.CanViewBeDuplicated`
and `View.Duplicate` with `ViewDuplicateOption.AsDependent`,
`View.GetPrimaryViewId` on the new view, and whether Revit's view name
uniqueness is per view type as the pre-check assumes (Rename Views makes
the same assumption). Revit refusing a name the pre-check let through is
caught and rolls the run back.
"""

__title__ = "Batch\nDependents"
__author__ = "AVH"
__doc__ = "Make a number of dependent views under each chosen view at once, named as prefix + field + suffix, where {parent} stands for the parent view's name."

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
from avh_dependents import model                  # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Batch Dependents"


def safe(call, default=None):
    try:
        return call()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return default


def id_text(element_id):
    # Revit 2024 replaced IntegerValue with Value.
    value = getattr(element_id, "Value", None)
    if value is None:
        value = getattr(element_id, "IntegerValue", u"?")
    return to_text(value)


class Parent(object):
    """A view that can take dependent views, read once."""

    def __init__(self, view):
        self.element = view
        self.name = to_text(view.Name)
        self.group = to_text(view.ViewType)
        self.kind = re.sub(u"(?<=[a-z])(?=[A-Z])", u" ", self.group)
        self.label = u"{0}  {1}".format(self.kind, self.name)


def can_take_dependents(view):
    if safe(lambda: view.IsTemplate, True):
        return False
    primary = safe(lambda: view.GetPrimaryViewId())
    if primary is not None and primary != DB.ElementId.InvalidElementId:
        return False
    return bool(safe(lambda: view.CanViewBeDuplicated(
        DB.ViewDuplicateOption.AsDependent), False))


def scan(doc):
    """(parents, taken): the views that can take dependents, sorted, and
    (view type, name) of every view in the model."""
    parents, taken = [], []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if safe(lambda: view.IsTemplate, True):
            continue
        name = safe(lambda: view.Name)
        group = safe(lambda: view.ViewType)
        if name is None or group is None:
            continue
        taken.append((to_text(group), to_text(name)))
        if can_take_dependents(view):
            parents.append(Parent(view))
    parents.sort(key=lambda p: (p.kind.lower(), p.name.lower()))
    labels = {}
    for parent in parents:
        labels.setdefault(parent.label, []).append(parent)
    for same in labels.values():
        if len(same) > 1:
            for parent in same:
                parent.label = u"{0}  ({1})".format(
                    parent.label, id_text(parent.element.Id))
    return parents, taken


def selected(doc, parents):
    """(chosen, skipped): the selected views that can take dependents,
    and the names of selected views that cannot."""
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except BaseException:
        return [], []
    by_id = dict((p.element.Id, p) for p in parents)
    chosen, skipped = [], []
    for element_id in ids:
        if element_id in by_id:
            chosen.append(by_id[element_id])
            continue
        element = safe(lambda: doc.GetElement(element_id))
        if element is not None and isinstance(element, DB.View):
            skipped.append(to_text(safe(lambda: element.Name, u"?")))
    order = dict((id(p), i) for i, p in enumerate(parents))
    chosen.sort(key=lambda p: order[id(p)])
    return chosen, skipped


def pick(parents):
    by_label = dict((p.label, p) for p in parents)
    answer = forms.SelectFromList.show(
        [p.label for p in parents], multiselect=True,
        title=TITLE + u": tick the views to make dependents of",
        button_name=u"Use ticked")
    if not answer:
        return []
    if not isinstance(answer, list):
        answer = [answer]
    return [by_label[to_text(a)] for a in answer if to_text(a) in by_label]


def ask(chosen, prefix, start, suffix, count, evaluate):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_dependents import window
    return window.ask([p.label for p in chosen], prefix, start, suffix,
                      count, evaluate)


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return (True if committed is None else status == committed), status


def create(doc, chosen, rows):
    """Make a dependent view per row. Returns (made, why): the (view,
    row) pairs made, or [] and why none were."""
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    current = None
    made = []
    try:
        for row in rows:
            current = row
            parent = chosen[row.parent].element
            new_id = parent.Duplicate(DB.ViewDuplicateOption.AsDependent)
            view = doc.GetElement(new_id)
            view.Name = row.name
            made.append((view, row))
        current = None
        for view, row in made:
            parent = chosen[row.parent]
            got = to_text(view.Name)
            if got != row.name:
                transaction.RollBack()
                return [], (u"\"{0}\" came back as \"{1}\", so no view was "
                            u"made.".format(row.name, got))
            if view.GetPrimaryViewId() != parent.element.Id:
                transaction.RollBack()
                return [], (u"\"{0}\" did not come out as a dependent of "
                            u"{1}, so no view was made.".format(
                                row.name, parent.name))
    except BaseException as exc:
        transaction.RollBack()
        where = u" at \"{0}\"".format(current.name) if current else u""
        logger.error(to_text(exc))
        return [], (u"Revit refused{0}, so no view was made: {1}".format(
            where, to_text(exc)))
    ok, status = commit_checked(transaction)
    if not ok:
        return [], (u"Revit rejected the change, so no view was made "
                    u"({0}).".format(to_text(status)))
    return made, u""


def report(made, chosen, skipped):
    lines = [u"### {0}".format(TITLE),
             u"{0} dependent view(s) made under {1} view(s).".format(
                 len(made), len(chosen))]
    if skipped:
        lines.append(u"Selected but left out, as they cannot take "
                     u"dependent views: {0}.".format(u", ".join(skipped)))
    lines += [u"", u"| | Parent | New view |", u"| :--- | :--- | :--- |"]
    for view, row in made:
        lines.append(u"| {0} | {1} | {2} |".format(
            output.linkify(view.Id),
            chosen[row.parent].name.replace(u"|", u"/"),
            row.name.replace(u"|", u"/")))
    output.print_md(u"\n".join(lines))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if safe(lambda: doc.IsFamilyDocument, False):
        forms.alert(u"This is a family document; it has no views to make "
                    u"dependents of.", title=TITLE)
        return
    parents, taken = scan(doc)
    if not parents:
        forms.alert(u"No view in this model can take dependent views.",
                    title=TITLE)
        return

    chosen, skipped = selected(doc, parents)
    if not chosen:
        skipped = []
        chosen = pick(parents)
    if not chosen:
        return

    pairs = [(p.name, p.group) for p in chosen]

    def evaluate(prefix, fields, suffix):
        return model.plan(pairs, prefix, fields, suffix, taken)

    answer = ask(chosen, model.DEFAULT_PREFIX, model.DEFAULT_START, u"", 1,
                 evaluate)
    if answer is None:
        return

    rows = evaluate(*answer)
    if not rows:
        return
    stopped = model.blocking(rows)
    if stopped:
        forms.alert(u"No view was made: \"{0}\" cannot be used, {1}.".format(
            stopped[0].name, stopped[0].problem), title=TITLE)
        return

    made, why = create(doc, chosen, rows)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(made, chosen, skipped)


if __name__ == "__main__":
    run()
