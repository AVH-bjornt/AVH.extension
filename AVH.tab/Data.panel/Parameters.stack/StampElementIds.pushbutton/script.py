# -*- coding: utf-8 -*-
"""Copy each element's id into its Element ID parameter.

Revit 2025 offers no Element Id field for schedules, so an element's id is
copied into a parameter that a schedule can show. Export Schedule reads
that parameter back, so an exported workbook can be imported again with
every row tied to its element.

The parameter is NOT created here. It must already exist in the model as a
project or shared parameter called Element ID (a text parameter is best)
and be bound to the categories you want. If it is missing, this says so
and stops.

Pick the categories, read the report in the pyRevit output window, then
confirm. Only elements that do not already hold their own id are written,
so running it again changes nothing, and on a workshared model does not
turn a tidy up into a sync. Skipped, with the reason listed: elements in
groups, elements another user has checked out, read only parameters.

Run it again after copying or adding elements. A copy keeps the id of what
it was copied from until this is run, and Export Schedule ignores an id
that appears twice rather than trust it.

Unverified: run on a copy of the model first.
"""

__title__ = "Stamp\nElement IDs"
__author__ = "AVH"
__doc__ = "Copy each element's id into its Element ID parameter so schedules can show it and Import Schedule can find the element. The parameter must already exist."

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
from avh_schedules import stamper                 # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Stamp Element IDs"
MAX_LISTED = 100


def pick_categories(categories):
    by_name = {}
    for category in categories:
        by_name.setdefault(to_text(category.Name), category)
    chosen = forms.SelectFromList.show(
        sorted(by_name.keys()), multiselect=True,
        title=TITLE + u": which categories?", button_name="Stamp these")
    if not chosen:
        return []
    if not isinstance(chosen, list):
        chosen = [chosen]
    return [by_name[n] for n in chosen if n in by_name]


def link(element_id):
    try:
        return output.linkify(DB.ElementId(element_id))
    except BaseException:
        return to_text(element_id)


def report_plan(plan, parameter_name):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(
        u"Parameter **{0}**: {1} to write, {2} already correct, {3} "
        u"left alone.".format(parameter_name, len(plan.ready),
                              plan.already, len(plan.skipped)))
    if plan.skipped:
        reasons = {}
        for _ident, _category, reason in plan.skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason in sorted(reasons):
            output.print_md(u"- **{0}**: {1}".format(reason, reasons[reason]))
        for ident, category, reason in plan.skipped[:MAX_LISTED]:
            output.print_md(u"- {0} {1}: {2}".format(
                link(ident), category, reason))
        if len(plan.skipped) > MAX_LISTED:
            output.print_md(u"_and {0} more_".format(
                len(plan.skipped) - MAX_LISTED))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    name, categories = stamper.stamp_parameter_name(DB, doc)
    if name is None:
        forms.alert(
            u"This model has no Element ID parameter. Create a project or "
            u"shared parameter called Element ID (text) and bind it to the "
            u"categories you want, then run this again. This tool never "
            u"creates parameters.", title=TITLE)
        return
    if not categories:
        forms.alert(
            u"The parameter {0} exists but is not bound to any "
            u"category.".format(name), title=TITLE)
        return

    chosen = pick_categories(categories)
    if not chosen:
        return

    elements = []
    for category in chosen:
        elements.extend(stamper.collect_elements(DB, doc, category))
    if not elements:
        forms.alert(u"There are no elements in those categories.",
                    title=TITLE)
        return

    plan = stamper.plan_stamps(DB, doc, elements, name)
    report_plan(plan, name)

    if not plan.ready:
        forms.alert(
            u"Nothing to write: {0} already correct, {1} left alone. The "
            u"output window says why.".format(plan.already,
                                              len(plan.skipped)),
            title=TITLE)
        return

    go = forms.alert(
        u"Write the Element ID of {0} element(s)?\n\n{1} are already "
        u"correct and {2} will be left alone.".format(
            len(plan.ready), plan.already, len(plan.skipped)),
        title=TITLE, yes=True, no=True)
    if not go:
        return

    try:
        result = stamper.execute_stamps(DB, doc, plan)
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(u"Nothing was written. The run stopped with: "
                    u"{0}".format(to_text(exc)), title=TITLE)
        return

    if not result.committed:
        forms.alert(u"Revit rejected the changes, so nothing was written.",
                    title=TITLE)
        return
    output.print_md(u"**{0} written.**".format(result.written))
    for ident, reason in result.failed[:MAX_LISTED]:
        output.print_md(u"- {0}: {1}".format(link(ident), reason))
    forms.alert(u"{0} written, {1} failed.".format(
        result.written, len(result.failed)), title=TITLE)


if __name__ == "__main__":
    run()
