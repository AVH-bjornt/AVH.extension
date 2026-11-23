# -*- coding: utf-8 -*-
"""Number an instance parameter across many elements: prefix + running
number + suffix.

Select the elements first, or run with nothing selected and pick them one
at a time in the order they should be numbered (Esc when done). Then
choose the parameter: any text or whole number instance parameter that
every chosen element has and that can be edited on all of them, such as
Mark, Comments or a shared parameter of your own.

A window sets the prefix, the start value (1, 01, 101 or a letter), the
step and the suffix, filled in from the values the elements already hold
when they share a format such as #QQC014 (prefix #QQC, start 001). A
start with a leading zero keeps its width: 001 numbers up to 999 and
refuses to go past it, and the order the numbers go in: as picked, left to
right, top to bottom, by the value they hold now, or by element id. Every
new value is shown as you type, and those that would not change are grey.

Tick "Only elements with no value" to leave every value already there
alone and number only the empty ones. The numbers the others already use
(among the chosen elements) are skipped, so starting at 001 fills the
gaps and then carries on after the highest.

## Writing

All values are written in one transaction and read back. If Revit refuses
any of them, a value reads back different, or the commit comes back
rolled back, nothing is written and the dialog says which element. Values
that already match are not written, so running it twice changes nothing
the second time.

Revit's warnings during the write, such as two elements now sharing a
Mark, do not stop it; they are listed under the report instead of
popping up. An error rolls the whole run back.

Left out, and listed: elements inside a group when the parameter does not
vary across group instances (Revit would offer to ungroup them), and
elements another user has checked out.

## Unverified

Tried by the user in Revit 2025 before 2.55.0 was released. Which cases
were tried was not recorded, so these are still unconfirmed and tested
against a fake Revit only: PickObject in a loop and its Esc, the failure
preprocessor deleting warnings, Definition.VariesAcrossGroups, the Yes/No
check through GetDataType, Parameter.HasValue, and the window's order
ComboBox and only-empty CheckBox.
"""

__title__ = "Renumber"
__author__ = "AVH"
__doc__ = "Number a text or whole number instance parameter (Mark, Comments, a shared parameter) across selected or picked elements as prefix + running number + suffix, in the order picked or by position. Shows every new value before writing."

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
from avh_renumber import model                    # noqa: E402
from avh_schedules.compat import element_id_int, to_text  # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Renumber"
MAX_LISTED = 100


def safe(call, default=None):
    try:
        return call()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return default


class WriteGuard(DB.IFailuresPreprocessor):
    """Warnings are recorded and dismissed, errors roll the run back.

    Inherited alone: mixing IFailuresPreprocessor with another base class
    breaks the method resolution order and the method never runs.
    """

    def __init__(self):
        self.warnings = []
        self.errors = []

    def PreprocessFailures(self, accessor):
        try:
            for failure in list(accessor.GetFailureMessages()):
                text = to_text(safe(lambda: failure.GetDescriptionText(),
                                    u"?"))
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    self.warnings.append(text)
                    accessor.DeleteWarning(failure)
                else:
                    self.errors.append(text)
            if self.errors:
                return DB.FailureProcessingResult.ProceedWithRollBack
        except BaseException as exc:
            self.errors.append(to_text(exc))
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def guard_transaction(transaction, guard):
    try:
        options = transaction.GetFailureHandlingOptions()
        options = options.SetFailuresPreprocessor(guard)
        options = options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
        return True
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return (True if committed is None else status == committed), status


# -- which elements ------------------------------------------------------

def usable(element):
    if element is None:
        return False
    if isinstance(element, DB.ElementType):
        return False
    return True


def selected_elements(doc):
    ids = safe(lambda: list(revit.uidoc.Selection.GetElementIds()), [])
    found = []
    for element_id in ids:
        element = safe(lambda: doc.GetElement(element_id))
        if usable(element):
            found.append(element)
    return found


def show_picked(picked):
    """Keep what has been picked so far selected, so it can be seen."""
    try:
        from System.Collections.Generic import List
        ids = List[DB.ElementId]()
        for element in picked:
            ids.Add(element.Id)
        revit.uidoc.Selection.SetElementIds(ids)
    except BaseException as exc:
        logger.debug(to_text(exc))


def pick_in_order(doc):
    """Elements picked one at a time until Esc, in the order picked.
    Picking one again does nothing."""
    from Autodesk.Revit.UI.Selection import ObjectType
    from Autodesk.Revit.Exceptions import OperationCanceledException
    picked, seen = [], set()
    while True:
        try:
            reference = revit.uidoc.Selection.PickObject(
                ObjectType.Element,
                u"Renumber: pick element {0} in numbering order, Esc when "
                u"done".format(len(picked) + 1))
        except OperationCanceledException:
            break
        element = doc.GetElement(reference.ElementId)
        if not usable(element):
            continue
        key = element_id_int(element.Id)
        if key in seen:
            continue
        seen.add(key)
        picked.append(element)
        show_picked(picked)
    return picked


# -- which parameter -----------------------------------------------------

def storage_of(parameter):
    storage = safe(lambda: parameter.StorageType)
    if storage == DB.StorageType.String:
        return model.TEXT
    if storage == DB.StorageType.Integer:
        # Yes/No is stored as an Integer too and takes only 0 and 1.
        data_type = safe(lambda: parameter.Definition.GetDataType())
        yes_no = safe(lambda: DB.SpecTypeId.Boolean.YesNo)
        if data_type is not None and data_type == yes_no:
            return None
        return model.INTEGER
    return None


def writable_parameters(element):
    """{name: storage} of the element's editable text and whole number
    parameters."""
    found = {}
    for parameter in safe(lambda: list(element.Parameters), []):
        if safe(lambda: parameter.IsReadOnly, True):
            continue
        storage = storage_of(parameter)
        if storage is None:
            continue
        name = to_text(safe(lambda: parameter.Definition.Name, u""))
        if not name:
            continue
        if found.get(name, storage) != storage:
            found[name] = None
            continue
        found[name] = storage
    return found


def common_parameters(elements):
    """{name: storage} of the parameters every element has editable,
    with the same storage."""
    common = None
    for element in elements:
        own = writable_parameters(element)
        if common is None:
            common = dict((n, s) for n, s in own.items() if s is not None)
            continue
        common = dict((n, s) for n, s in common.items()
                      if own.get(n) == s)
        if not common:
            break
    return common or {}


def choose_parameter(common):
    kinds = {model.TEXT: u"text", model.INTEGER: u"whole number"}
    labels = {}
    for name in sorted(common, key=lambda n: n.lower()):
        labels[u"{0}  ({1})".format(name, kinds[common[name]])] = name
    answer = forms.SelectFromList.show(
        sorted(labels, key=lambda label: label.lower()), multiselect=False,
        title=TITLE + u": which parameter?", button_name=u"Renumber this")
    if not answer:
        return None
    if isinstance(answer, list):
        answer = answer[0]
    return labels.get(to_text(answer))


# -- reading -------------------------------------------------------------

def label_of(element):
    category = to_text(safe(lambda: element.Category.Name, u""))
    name = to_text(safe(lambda: element.Name, u""))
    family = safe(lambda: element.Symbol.Family.Name)
    if family:
        name = u"{0}: {1}".format(to_text(family), name)
    text = u"{0} {1}".format(category, name).strip()
    return u"{0}  [{1}]".format(text or u"Element",
                                element_id_int(element.Id))


def position_of(element):
    """(x, y) in plan, in feet, or (None, None)."""
    location = safe(lambda: element.Location)
    point = safe(lambda: location.Point)
    if point is None:
        curve = safe(lambda: location.Curve)
        if curve is not None:
            point = safe(lambda: curve.Evaluate(0.5, True))
    if point is None:
        point = safe(lambda: element.GetBoxCenter())
    if point is not None:
        return safe(lambda: point.X), safe(lambda: point.Y)
    box = safe(lambda: element.get_BoundingBox(None))
    if box is not None:
        return ((box.Min.X + box.Max.X) / 2.0,
                (box.Min.Y + box.Max.Y) / 2.0)
    return None, None


def current_value(parameter, storage):
    if safe(lambda: parameter.HasValue, True) is False:
        return u""
    if storage == model.INTEGER:
        value = safe(lambda: parameter.AsInteger())
        return u"" if value is None else to_text(value)
    return to_text(safe(lambda: parameter.AsString(), u""))


def in_group(element):
    group_id = safe(lambda: element.GroupId)
    if group_id is None:
        return False
    return element_id_int(group_id) not in (None, -1)


def owned_by_someone_else(doc, element):
    if not safe(lambda: doc.IsWorkshared, False):
        return False
    status = safe(lambda: DB.WorksharingUtils.GetCheckoutStatus(
        doc, element.Id))
    return status is not None and status == \
        DB.CheckoutStatus.OwnedByOtherUser


def prepare(doc, elements, name, storage):
    """(items, parameters, skipped): plain items to number, the parameter
    behind each item's ident, and (label, reason) of those left out."""
    items, parameters, skipped = [], {}, []
    for element in elements:
        label = label_of(element)
        parameter = element.LookupParameter(name)
        if parameter is None:
            skipped.append((label, u"has no parameter called that"))
            continue
        if in_group(element) and not safe(
                lambda: parameter.Definition.VariesAcrossGroups, False):
            skipped.append((label, u"inside a group, and the parameter "
                                   u"does not vary across group instances"))
            continue
        if owned_by_someone_else(doc, element):
            skipped.append((label, u"checked out by another user"))
            continue
        ident = element_id_int(element.Id)
        x, y = position_of(element)
        items.append(model.Item(len(items), ident, label,
                                current_value(parameter, storage), x, y))
        parameters[ident] = parameter
    return items, parameters, skipped


def ask(heading, count, orders, prefix, start, step, suffix, evaluate):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_renumber import window
    return window.ask(heading, count, orders, prefix, start, step, suffix,
                      evaluate)


# -- writing -------------------------------------------------------------

def write(doc, rows, parameters, storage, name):
    """Write every row's value. Returns (written, why, warnings): why is
    u"" when it committed."""
    guard = WriteGuard()
    transaction = DB.Transaction(doc, u"{0} {1}".format(TITLE, name))
    guard_transaction(transaction, guard)
    transaction.Start()
    current = None
    try:
        for row in rows:
            current = row
            parameter = parameters[row.item.ident]
            if storage == model.INTEGER:
                value = int(row.new)
            else:
                value = row.new
            if parameter.Set(value) is False:
                transaction.RollBack()
                return 0, (u"Revit refused {0} for {1}, so nothing was "
                           u"written.".format(row.new, row.item.label)), []
            got = current_value(parameter, storage)
            if got != row.new:
                transaction.RollBack()
                return 0, (u"{0} read back as {1} instead of {2}, so "
                           u"nothing was written.".format(
                               row.item.label, got or u"(empty)",
                               row.new)), []
        current = None
    except BaseException as exc:
        transaction.RollBack()
        where = u" at {0}".format(current.item.label) if current else u""
        logger.error(to_text(exc))
        return 0, (u"Revit refused{0}, so nothing was written: {1}".format(
            where, to_text(exc))), []
    ok, status = commit_checked(transaction)
    if not ok:
        said = u""
        if guard.errors:
            said = u" Revit said: " + u" / ".join(guard.errors)
        return 0, (u"Revit rejected the change, so nothing was written "
                   u"({0}).{1}".format(to_text(status), said)), []
    return len(rows), u"", guard.warnings


# -- report --------------------------------------------------------------

def link(ident):
    try:
        return output.linkify(DB.ElementId(ident))
    except BaseException:
        return to_text(ident)


def table_cell(text):
    text = to_text(text).replace(u"|", u"/")
    text = text.replace(u"\r", u" ").replace(u"\n", u" ").strip()
    return text or u"-"


def report(name, rows, written, unchanged, skipped, warnings):
    lines = [u"### {0}: {1}".format(TITLE, table_cell(name)),
             u"{0} value(s) written, {1} already right.".format(
                 written, unchanged)]
    output.print_md(u"\n".join(lines))
    table = [u"| # | Element | Was | Now |", u"| :--- | :--- | :--- | :--- |"]
    for number, row in enumerate(rows[:MAX_LISTED], 1):
        table.append(u"| {0} | {1} {2} | {3} | {4} |".format(
            number, link(row.item.ident),
            table_cell(row.item.label), table_cell(row.item.current),
            table_cell(row.new)))
    output.print_md(u"\n".join(table))
    if len(rows) > MAX_LISTED:
        output.print_md(u"_and {0} more_".format(len(rows) - MAX_LISTED))
    if skipped:
        output.print_md(u"**Left out ({0}):**\n\n".format(len(skipped)) +
                        u"\n".join(u"- {0}: {1}".format(label, reason)
                                   for label, reason in skipped))
    if warnings:
        output.print_md(u"**Revit warned:**\n\n" + u"\n".join(
            u"- {0}".format(text) for text in sorted(set(warnings))))


# -- run -----------------------------------------------------------------

def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    elements = selected_elements(doc)
    orders = model.ORDERS_SELECTED
    if not elements:
        elements = pick_in_order(doc)
        orders = model.ORDERS_PICKED
    if not elements:
        return

    common = common_parameters(elements)
    if not common:
        forms.alert(
            u"The {0} chosen element(s) have no editable text or whole "
            u"number parameter in common.".format(len(elements)),
            title=TITLE)
        return
    name = choose_parameter(common)
    if not name:
        return
    storage = common[name]

    items, parameters, skipped = prepare(doc, elements, name, storage)
    if not items:
        forms.alert(u"None of the elements can be written:\n\n" +
                    u"\n".join(u"{0}: {1}".format(label, reason)
                               for label, reason in skipped[:20]),
                    title=TITLE)
        return

    def evaluate(order, prefix, start, step, suffix, only_empty=False):
        return model.plan(items, order, prefix, start, step, suffix,
                          storage, only_empty)

    heading = u"{0}: {1} element(s)".format(name, len(items))
    if skipped:
        heading += u", {0} left out (listed after writing)".format(
            len(skipped))
    prefix, start, suffix = u"", model.DEFAULT_START, u""
    if storage == model.TEXT:
        found = model.suggest([item.current for item in items])
        if found is not None:
            prefix, start, suffix = found
    answer = ask(heading, len(items), orders, prefix, start,
                 model.DEFAULT_STEP, suffix, evaluate)
    if answer is None:
        return

    rows, problem = evaluate(*answer)
    if problem:
        forms.alert(u"Nothing was written: {0}.".format(problem),
                    title=TITLE)
        return
    to_write = model.changed(rows)
    if not to_write:
        forms.alert(u"Every value is already this; nothing was written.",
                    title=TITLE)
        return

    written, why, warnings = write(doc, to_write, parameters, storage, name)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(name, rows, written, len(rows) - len(to_write), skipped,
           warnings)


if __name__ == "__main__":
    run()
