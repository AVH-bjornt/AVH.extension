# -*- coding: utf-8 -*-
"""Write the changes made in an exported schedule workbook back to Revit.

Export Schedule leaves two things in every workbook that nobody needs to
look at: a hidden column with the Revit element id of each row, and a
hidden sheet, AVH_Roundtrip, holding every cell as it was exported. This
button compares the workbook with that snapshot. A cell that differs is a
change; every other cell is left alone, so importing a workbook in which
you edited three cells touches three parameters and nothing else.

## Nothing is written until you have seen it

The changes are read, checked against the live model and printed to the
pyRevit output window, with what would be skipped and why. Only then is
there a confirmation, and one transaction does the writing.

## What is never written

- type parameters: one cell would change every element of the type
- calculated, count and room columns
- elements inside a group, which Revit refuses with an unignorable dialog
- elements another user has checked out
- a text or whole number value that has changed in the model since the
  export: the workbook is out of date for that cell
- a number cell that was cleared, so a blank cannot turn into a zero

Rows you delete are ignored. This tool never deletes an element.

## Unverified

Run on a copy of the model first. Everything that touches the Revit API
has been exercised only against a mock: that Parameter.Id matches the
schedule field's ParameterId, the unit conversion, and GroupId.
"""

__title__ = "Import\nSchedule"
__author__ = "AVH"
__doc__ = "Write the cells you changed in an exported schedule workbook back to Revit. Only edited cells are written, and you see every change before anything is."

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels.
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
from avh_schedules import applier, roundtrip, xlsxread   # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Import Schedule"
MAX_LISTED = 300


def default_folder(doc):
    """The Schedules folder beside the model, where Export put the file."""
    try:
        base = os.path.dirname(doc.PathName or u"")
        candidate = os.path.join(base, u"Schedules")
        if base and os.path.isdir(candidate):
            return candidate
        if base and os.path.isdir(base):
            return base
    except BaseException:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def pick_workbook(doc):
    try:
        return forms.pick_file(file_ext="xlsx",
                               init_dir=default_folder(doc))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def show(value):
    if value is None or to_text(value).strip() == u"":
        return u"(blank)"
    if isinstance(value, float):
        return roundtrip.number_text(value)
    return to_text(value)


def link(element_id):
    try:
        return output.linkify(DB.ElementId(element_id))
    except BaseException:
        return to_text(element_id)


def report_plan(snapshot, diff, plan):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"Schedule **{0}**, exported {1}.".format(
        snapshot.schedule_name, snapshot.exported or u"(time unknown)"))
    output.print_md(
        u"{0} row(s) read, **{1} cell(s) changed**: {2} can be written, "
        u"{3} are already that value, {4} cannot be written.".format(
            diff.rows_seen, len(diff.changes), len(plan.ready),
            len(plan.already), len(plan.skipped)))

    if plan.ready:
        output.print_md(u"#### Will be written")
        output.print_md(u"| Element | Column | Was | Becomes |")
        output.print_md(u"| --- | --- | --- | --- |")
        for planned in plan.ready[:MAX_LISTED]:
            change = planned.change
            output.print_md(u"| {0} | {1} | {2} | {3} |".format(
                link(change.element_id), change.field.name,
                show(change.old), show(change.new)))
        if len(plan.ready) > MAX_LISTED:
            output.print_md(u"_and {0} more_".format(
                len(plan.ready) - MAX_LISTED))

    if plan.skipped:
        output.print_md(u"#### Cannot be written")
        reasons = {}
        for _change, reason, _detail in plan.skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason in sorted(reasons):
            output.print_md(u"- **{0}**: {1}".format(reason, reasons[reason]))
        output.print_md(u"_Which ones:_")
        for change, reason, detail in plan.skipped[:MAX_LISTED]:
            output.print_md(u"- {0} {1}: {2}{3}".format(
                link(change.element_id), change.field.name, reason,
                u" ({0})".format(detail) if detail else u""))
        if len(plan.skipped) > MAX_LISTED:
            output.print_md(u"_and {0} more_".format(
                len(plan.skipped) - MAX_LISTED))

    notes = []
    if diff.unknown:
        notes.append(u"{0} row(s) carry an id that is not in the export "
                     u"and were ignored.".format(len(diff.unknown)))
    if diff.duplicates:
        notes.append(u"{0} element(s) appear on more than one row and were "
                     u"ignored, because it is not clear which row is "
                     u"meant.".format(len(diff.duplicates)))
    if diff.rows_missing:
        notes.append(u"{0} exported row(s) are no longer in the sheet. "
                     u"Nothing is deleted from the model.".format(
                         diff.rows_missing))
    for note in notes:
        output.print_md(u"- _{0}_".format(note))


def report_result(result):
    output.print_md(u"#### Result")
    if not result.committed:
        output.print_md(u"**Revit rolled the whole change back. Nothing "
                        u"was written.**")
        return
    output.print_md(u"**{0} written.**".format(len(result.written)))
    if result.failed:
        output.print_md(u"{0} failed:".format(len(result.failed)))
        for planned, reason in result.failed[:MAX_LISTED]:
            output.print_md(u"- {0} {1}: {2}".format(
                link(planned.change.element_id), planned.change.field.name,
                reason))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    path = pick_workbook(doc)
    if not path:
        return

    try:
        sheets = xlsxread.read_workbook(path)
        snapshot, visible = roundtrip.read_snapshot(sheets)
    except roundtrip.NotARoundtripWorkbook as exc:
        forms.alert(to_text(exc), title=TITLE)
        return
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            u"The workbook could not be read. If it is open in Excel, save "
            u"it and try again.\n\n{0}".format(to_text(exc)), title=TITLE)
        return

    try:
        model_title = to_text(doc.Title)
    except BaseException:
        model_title = u""
    if (snapshot.model and model_title
            and snapshot.model.strip().lower() != model_title.strip().lower()):
        proceed = forms.alert(
            u"This workbook was exported from the model \"{0}\", and the "
            u"open model is \"{1}\".\n\nElement ids mean something different "
            u"in another model. Continue only if this is the same model, "
            u"for example a local copy.".format(snapshot.model, model_title),
            title=TITLE, yes=True, no=True)
        if not proceed:
            return

    diff = roundtrip.find_changes(snapshot, visible)
    if not diff.changes:
        forms.alert(
            u"No cells were changed since the export, so there is nothing "
            u"to import ({0} rows compared).".format(diff.rows_seen),
            title=TITLE)
        return

    plan = applier.plan_changes(DB, doc, diff.changes)
    report_plan(snapshot, diff, plan)

    if not plan.ready:
        forms.alert(
            u"{0} cell(s) were changed but none can be written. The output "
            u"window says why.".format(len(diff.changes)), title=TITLE)
        return

    elements = len(set(p.change.element_id for p in plan.ready))
    go = forms.alert(
        u"Write {0} change(s) to {1} element(s)?\n\n{2} other changed "
        u"cell(s) will be left alone. The list is in the output "
        u"window.".format(len(plan.ready), elements,
                          len(plan.skipped) + len(plan.already)),
        title=TITLE, yes=True, no=True)
    if not go:
        return

    try:
        result = applier.execute_plan(DB, doc, plan)
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(u"Nothing was written. The run stopped with: "
                    u"{0}".format(to_text(exc)), title=TITLE)
        return

    report_result(result)
    if not result.committed:
        forms.alert(u"Revit rejected the changes, so nothing was written.",
                    title=TITLE)
    elif result.failed:
        forms.alert(u"{0} written, {1} failed. See the output window."
                    .format(len(result.written), len(result.failed)),
                    title=TITLE)
    else:
        forms.alert(u"{0} change(s) written.".format(len(result.written)),
                    title=TITLE)


if __name__ == "__main__":
    run()
