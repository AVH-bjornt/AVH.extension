# -*- coding: utf-8 -*-
"""Rename family types from their own parameter values.

Select elements or types first (in the model, a schedule or the Project
Browser); the types of what is selected are renamed. With nothing
selected, a list of the families in the model is offered and every type
of the ticked ones is renamed.

A window takes the name as a pattern: plain text with parameter names in
braces, such as {Width}x{Height} {Material} or D-{Type Mark}. The list
on the left holds every type parameter the chosen types have, with an
example value; double-click one to insert it where the cursor is. Each
type's new name is shown as you type: grey when it would not change, red
with the reason when it cannot be used.

Values are read the way Revit shows them, in the project's units, and a
parameter that points at an element (a material) gives that element's
name. Characters Revit does not allow in a name (such as : and ;) are
replaced by - when they come from a value. A parameter a type does not
have gives nothing, and the spaces it leaves are closed up.

## Writing

Type names must be unique within a family. A name that another type in
the family already has, or that two of the chosen types would both get,
is shown red and nothing can be written until it is fixed. Chosen types
may swap names: those are moved to a temporary name first.

All names are written in one transaction and read back. If Revit refuses
any of them, a name reads back different, or the commit comes back
rolled back, nothing is renamed and the dialog says which type. Types
already called what the pattern gives are not touched, so running it
twice changes nothing the second time.

Left out, and listed: types of in-place families, and types another
user has checked out.

## Unverified

Tested against a fake Revit only. Not yet seen in Revit: setting
ElementType.Name and reading it back through SYMBOL_NAME_PARAM, the
ElementId value read as the element's name, whether Revit compares type
names ignoring case (assumed so, the stricter choice), the uniqueness key
for system families (category + Family Name), and the window's ListBox
and insert at the cursor.
"""

__title__ = "Rename\nTypes"
__author__ = "AVH"
__doc__ = "Rename family types from their own parameter values with a pattern such as {Width}x{Height} {Material}. Uses the types of the selected elements, or every type of the families you tick. Shows every new name before writing."

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
from avh_typenames import model                   # noqa: E402
from avh_schedules.compat import element_id_int, to_text  # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Rename Types"
MAX_LISTED = 200
MAX_EXAMPLE = 30


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


# -- names ---------------------------------------------------------------

def name_of(element):
    """An element's name. ElementType.Name cannot always be read directly
    from IronPython, so the type name parameter is tried first."""
    for which in ("SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME"):
        built_in = safe(lambda: getattr(DB.BuiltInParameter, which))
        if built_in is None:
            continue
        parameter = safe(lambda: element.get_Parameter(built_in))
        text = safe(lambda: parameter.AsString())
        if text:
            return to_text(text)
    getter = safe(lambda: DB.Element.Name.__get__)
    if getter is not None:
        text = safe(lambda: getter(element))
        if text:
            return to_text(text)
    return to_text(safe(lambda: element.Name, u""))


def set_name(element, name):
    """Raises when Revit refuses the name."""
    try:
        element.Name = name
    except AttributeError:
        DB.Element.Name.__set__(element, name)


def family_name_of(element_type):
    name = safe(lambda: element_type.FamilyName)
    if not name:
        name = safe(lambda: element_type.Family.Name)
    return to_text(name or u"")


def category_of(element_type):
    return to_text(safe(lambda: element_type.Category.Name, u""))


def family_key(element_type):
    """What a type's name must be unique within: its family, or for a
    system family its category and Family Name."""
    family_id = safe(lambda: element_type.Family.Id)
    if family_id is not None:
        return u"family {0}".format(element_id_int(family_id))
    category = safe(lambda: element_type.Category.Id)
    category = element_id_int(category) if category is not None else u"-"
    return u"system {0} {1}".format(category,
                                    family_name_of(element_type).lower())


def family_label(element_type):
    category = category_of(element_type)
    family = family_name_of(element_type) or u"?"
    return u"{0}: {1}".format(category, family) if category else family


# -- which types ---------------------------------------------------------

def all_types(doc):
    return safe(lambda: list(DB.FilteredElementCollector(doc)
                             .WhereElementIsElementType()
                             .ToElements()), [])


def type_of(doc, element):
    if element is None:
        return None
    if isinstance(element, DB.ElementType):
        return element
    type_id = safe(lambda: element.GetTypeId())
    if type_id is None or element_id_int(type_id) in (None, -1):
        return None
    found = safe(lambda: doc.GetElement(type_id))
    if isinstance(found, DB.ElementType):
        return found
    return None


def selected_types(doc):
    """The types of the selected elements, each once, in selection
    order."""
    ids = safe(lambda: list(revit.uidoc.Selection.GetElementIds()), [])
    found, seen = [], set()
    for element_id in ids:
        element_type = type_of(doc, safe(lambda: doc.GetElement(element_id)))
        if element_type is None:
            continue
        key = element_id_int(element_type.Id)
        if key in seen:
            continue
        seen.add(key)
        found.append(element_type)
    return found


def chosen_from_families(doc, types):
    """Every type of the families ticked in a list."""
    by_label = {}
    for element_type in types:
        if not family_name_of(element_type):
            continue
        by_label.setdefault(family_label(element_type), []).append(
            element_type)
    if not by_label:
        return []
    labels = sorted(by_label, key=lambda label: label.lower())
    answer = forms.SelectFromList.show(
        labels, multiselect=True,
        title=TITLE + u": rename the types of which families?",
        button_name=u"Choose these")
    if not answer:
        return []
    if not isinstance(answer, list):
        answer = [answer]
    chosen = []
    for label in answer:
        chosen.extend(by_label.get(to_text(label), []))
    return chosen


# -- reading -------------------------------------------------------------

def value_text(doc, parameter):
    """A parameter's value as Revit shows it, or u""."""
    if safe(lambda: parameter.HasValue, True) is False:
        return u""
    storage = safe(lambda: parameter.StorageType)
    if storage == DB.StorageType.String:
        return to_text(safe(lambda: parameter.AsString(), u"") or u"")
    if storage == DB.StorageType.ElementId:
        element_id = safe(lambda: parameter.AsElementId())
        if element_id is not None and \
                element_id_int(element_id) not in (None, -1):
            element = safe(lambda: doc.GetElement(element_id))
            if element is not None:
                return name_of(element)
    return to_text(safe(lambda: parameter.AsValueString(), u"") or u"")


def values_of(doc, element_type):
    """{parameter name: value as text} of a type, with its family name,
    type name and category always there."""
    values = {}
    for parameter in safe(lambda: list(element_type.Parameters), []):
        name = to_text(safe(lambda: parameter.Definition.Name, u""))
        if not name or name in values:
            continue
        values[name] = value_text(doc, parameter)
    values.setdefault(model.FAMILY_NAME, family_name_of(element_type))
    values.setdefault(model.TYPE_NAME, name_of(element_type))
    values.setdefault(model.CATEGORY, category_of(element_type))
    return values


def owned_by_someone_else(doc, element):
    if not safe(lambda: doc.IsWorkshared, False):
        return False
    status = safe(lambda: DB.WorksharingUtils.GetCheckoutStatus(
        doc, element.Id))
    return status is not None and status == \
        DB.CheckoutStatus.OwnedByOtherUser


def prepare(doc, chosen):
    """(items, elements, skipped): plain items to rename, the type behind
    each item's ident, and (label, reason) of those left out."""
    items, elements, skipped = [], {}, []
    for element_type in chosen:
        label = u"{0}: {1}".format(family_label(element_type),
                                   name_of(element_type))
        if safe(lambda: element_type.Family.IsInPlace, False):
            skipped.append((label, u"in-place family"))
            continue
        if owned_by_someone_else(doc, element_type):
            skipped.append((label, u"checked out by another user"))
            continue
        ident = element_id_int(element_type.Id)
        items.append(model.Item(len(items), ident, family_key(element_type),
                                family_label(element_type),
                                name_of(element_type),
                                values_of(doc, element_type)))
        elements[ident] = element_type
    return items, elements, skipped


def taken_names(types, items):
    """{family key: names of the types there that are not chosen}."""
    chosen = set(item.ident for item in items)
    families = set(item.family for item in items)
    taken = {}
    for element_type in types:
        if element_id_int(element_type.Id) in chosen:
            continue
        key = family_key(element_type)
        if key in families:
            taken.setdefault(key, []).append(name_of(element_type))
    return taken


def parameter_choices(items):
    """[(name, label)] for the window's list: the name, an example value
    and, when not every type has it, how many do."""
    choices = []
    for name, count in model.available(items):
        example = u""
        for item in items:
            value = model.lookup(item.values, name)
            if value:
                example = value
                break
        if len(example) > MAX_EXAMPLE:
            example = example[:MAX_EXAMPLE - 3] + u"..."
        label = name
        if example:
            label += u"   e.g. " + example
        if count != len(items):
            label += u"   ({0} of {1})".format(count, len(items))
        choices.append((name, label))
    return choices


def ask(heading, count, parameters, pattern, evaluate):
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_typenames import window
    return window.ask(heading, count, parameters, pattern, evaluate)


# -- writing -------------------------------------------------------------

def write(doc, rows, elements):
    """Rename every row's type. Returns (written, why, warnings): why is
    u"" when it committed."""
    guard = WriteGuard()
    transaction = DB.Transaction(doc, TITLE)
    guard_transaction(transaction, guard)
    transaction.Start()
    current = None
    try:
        for row in model.blockers(rows):
            current = row
            set_name(elements[row.item.ident], model.temporary_name(row))
        for row in rows:
            current = row
            element = elements[row.item.ident]
            set_name(element, row.new)
            got = name_of(element)
            if got != row.new:
                transaction.RollBack()
                return 0, (u"{0} read back as {1} instead of {2}, so "
                           u"nothing was renamed.".format(
                               row.item.current, got or u"(empty)",
                               row.new)), []
        current = None
    except BaseException as exc:
        transaction.RollBack()
        where = u" at {0}".format(current.item.current) if current else u""
        logger.error(to_text(exc))
        return 0, (u"Revit refused{0}, so nothing was renamed: {1}".format(
            where, to_text(exc))), []
    ok, status = commit_checked(transaction)
    if not ok:
        said = u""
        if guard.errors:
            said = u" Revit said: " + u" / ".join(guard.errors)
        return 0, (u"Revit rejected the change, so nothing was renamed "
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


def report(pattern, rows, written, unchanged, skipped, warnings):
    lines = [u"### {0}: {1}".format(TITLE, table_cell(pattern)),
             u"{0} type(s) renamed, {1} already right.".format(
                 written, unchanged)]
    output.print_md(u"\n".join(lines))
    renamed = model.changed(rows)
    if renamed:
        table = [u"| Type | Family | Was | Now |",
                 u"| :--- | :--- | :--- | :--- |"]
        for row in renamed[:MAX_LISTED]:
            table.append(u"| {0} | {1} | {2} | {3} |".format(
                link(row.item.ident), table_cell(row.item.family_label),
                table_cell(row.item.current), table_cell(row.new)))
        output.print_md(u"\n".join(table))
        if len(renamed) > MAX_LISTED:
            output.print_md(u"_and {0} more_".format(
                len(renamed) - MAX_LISTED))
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

    types = all_types(doc)
    chosen = selected_types(doc)
    if not chosen:
        chosen = chosen_from_families(doc, types)
    if not chosen:
        return

    items, elements, skipped = prepare(doc, chosen)
    if not items:
        forms.alert(u"None of the types can be renamed:\n\n" +
                    u"\n".join(u"{0}: {1}".format(label, reason)
                               for label, reason in skipped[:20]),
                    title=TITLE)
        return
    taken = taken_names(types, items)

    def evaluate(pattern):
        return model.plan(items, pattern, taken)

    families = len(set(item.family for item in items))
    heading = u"{0} type(s) in {1} famil{2}".format(
        len(items), families, u"y" if families == 1 else u"ies")
    if skipped:
        heading += u", {0} left out (listed after renaming)".format(
            len(skipped))
    pattern = ask(heading, len(items), parameter_choices(items),
                  model.DEFAULT_PATTERN, evaluate)
    if pattern is None:
        return

    rows, problem = evaluate(pattern)
    if problem:
        forms.alert(u"Nothing was renamed: {0}.".format(problem),
                    title=TITLE)
        return
    to_write = model.changed(rows)
    if not to_write:
        forms.alert(u"Every type is already called this; nothing was "
                    u"renamed.", title=TITLE)
        return

    written, why, warnings = write(doc, to_write, elements)
    if why:
        forms.alert(why, title=TITLE)
        return
    report(pattern, rows, written, len(rows) - len(to_write), skipped,
           warnings)


if __name__ == "__main__":
    run()
