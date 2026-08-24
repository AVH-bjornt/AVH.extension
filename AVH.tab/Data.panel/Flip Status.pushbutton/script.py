# -*- coding: utf-8 -*-
"""Record whether each family instance is mirrored or flipped.

Revit knows whether an instance is mirrored and whether its flip
controls have been used, but will not let you schedule or filter on any
of it. This copies all three facts into parameters you own, so they can
be scheduled, filtered and checked.

Writes three Area parameters on Casework, Doors, Electrical Equipment,
Generic Models, Mechanical Equipment and Windows:

| Parameter | Source |
| --- | --- |
| `ElementFlippedOrMirrored` | `FamilyInstance.Mirrored` |
| `ElementHandFlipped` | `FamilyInstance.HandFlipped` |
| `ElementFacingFlipped` | `FamilyInstance.FacingFlipped` |

`1 SF` for true, `0 SF` for false. The first name and its values match
Engipedia's add in deliberately, so any schedule already built on that
parameter keeps working. The other two are what their add in never
recorded: it reads `Mirrored` alone and never touches the flip
controls, which is why a facing flipped door looks correct to it.

## Report only

This tool does not create or bind parameters, by decision. A parameter
that is missing, is not an Area, is read only, or does not vary across
group instances is reported with the category and parameter named, and
those elements are skipped. Nothing is guessed at and nothing is
created.

**Vary across group instances matters here.** Without it every instance
of a group shares one value, so a door mirrored in one group instance
and not in another cannot be recorded truthfully. `VariesAcrossGroups`
is read and reported, not set.

## Only writes what changed

A value already correct is left alone. Rewriting the same number to
every element marks the whole model as modified, which on a workshared
job turns a check into a sync. Written and unchanged are counted
separately so a run that changes nothing looks different from a run that
rewrites everything.

## Unverified

Not yet run in Revit: `FamilyInstance.Mirrored`, `HandFlipped` and
`FacingFlipped`, `Definition.GetDataType` against `SpecTypeId.Area`,
`InternalDefinition.VariesAcrossGroups`, and `ElementMulticategoryFilter`
over these six categories. Whether `Mirrored` is already true for a hand
flipped instance is the first thing to check on a door you know is
wrong, because if it is, the mirrored column and the hand column will
agree everywhere and one of them is redundant.
"""

__title__ = "Flip\nStatus"
__author__ = "AVH"
__doc__ = ("Record mirrored, hand flipped and facing flipped state into "
           "Area parameters on casework, doors, equipment, generic "
           "models and windows, so they can be scheduled and filtered. "
           "Reports anything the parameters cannot take.")

import os
import sys

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_flips import model                       # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Flip Status"


def collect_instances(doc):
    """Family instances in the six categories. Returns (elements, missing)."""
    from System.Collections.Generic import List

    built_ins = List[DB.BuiltInCategory]()
    missing = []
    for name, label in model.CATEGORIES:
        built_in = getattr(DB.BuiltInCategory, name, None)
        if built_in is None:
            missing.append(label)
            continue
        built_ins.Add(built_in)

    if not len(built_ins):
        return [], missing

    category_filter = DB.ElementMulticategoryFilter(built_ins)
    collector = DB.FilteredElementCollector(doc)
    collector = collector.OfClass(DB.FamilyInstance)
    collector = collector.WhereElementIsNotElementType()
    collector = collector.WherePasses(category_filter)
    return list(collector), missing


def category_label(element):
    try:
        category = element.Category
        if category is not None and category.Name:
            return to_text(category.Name)
    except BaseException:
        pass
    return u"unknown category"


def flip_state(element):
    """The three booleans. A property that will not answer is False.

    A family with no flip control does not raise on `HandFlipped` as far
    as the documentation goes, but it costs nothing to be sure, and not
    flipped is the honest reading either way.
    """
    state = {}
    for key, attribute in (("mirrored", "Mirrored"),
                           ("hand", "HandFlipped"),
                           ("facing", "FacingFlipped")):
        try:
            state[key] = bool(getattr(element, attribute))
        except BaseException as exc:
            logger.debug(to_text(exc))
            state[key] = False
    return state


def is_area_parameter(parameter):
    """(ok, reason). Area specifically, not merely a number.

    A Length parameter would take 1.0 happily and display 1' 0", which
    is wrong in a way nobody notices until a schedule is printed.
    """
    try:
        if parameter.StorageType != DB.StorageType.Double:
            return False, model.Problems.WRONG_TYPE
    except BaseException:
        return False, model.Problems.WRONG_TYPE

    definition = None
    try:
        definition = parameter.Definition
        data_type = definition.GetDataType()
    except BaseException:
        # Older API with no GetDataType. A double storage type is as far
        # as the check can go there.
        return True, u""

    try:
        if data_type != DB.SpecTypeId.Area:
            return False, model.Problems.WRONG_TYPE
    except BaseException:
        return True, u""

    return True, u""


def varies_across_groups(parameter):
    """True, False, or None when it cannot be determined."""
    try:
        definition = parameter.Definition
        value = getattr(definition, "VariesAcrossGroups", None)
        if value is None:
            return None
        return bool(value)
    except BaseException:
        return None


def write_state(element, label, state, problems):
    """Write the three parameters. Returns (wrote_any, skipped, failed)."""
    values = model.desired_values(state)
    wrote_any = False
    skipped = False
    failed = False

    for name, _key, _report_label in model.PARAMETERS:
        try:
            parameter = element.LookupParameter(name)
        except BaseException:
            parameter = None

        if parameter is None:
            problems.add(label, name, model.Problems.MISSING)
            skipped = True
            continue

        if parameter.IsReadOnly:
            problems.add(label, name, model.Problems.READ_ONLY)
            skipped = True
            continue

        ok, reason = is_area_parameter(parameter)
        if not ok:
            problems.add(label, name, reason)
            skipped = True
            continue

        if varies_across_groups(parameter) is False:
            # Reported once per category and parameter, then written
            # anyway: the value is still right for everything outside a
            # group, and refusing would help nobody.
            problems.add(label, name, model.Problems.NOT_VARYING)

        target = values[name]
        try:
            current = parameter.AsDouble()
        except BaseException:
            current = None

        if not model.needs_write(current, target):
            continue

        try:
            if not parameter.Set(target):
                problems.add(label, name, model.Problems.WRITE_FAILED)
                failed = True
                continue
        except BaseException as exc:
            problems.add(label, name, model.Problems.WRITE_FAILED,
                         to_text(exc))
            failed = True
            continue

        wrote_any = True

    return wrote_any, skipped, failed


def report(tally, problems, missing_categories):
    output.print_md(u"### {0}".format(TITLE))

    rows = tally.rows()
    if rows:
        output.print_md(u"| Category | Instances | Mirrored | Hand "
                        u"flipped | Facing flipped |")
        output.print_md(u"| --- | ---: | ---: | ---: | ---: |")
        for category, elements, mirrored, hand, facing in rows:
            output.print_md(u"| {0} | {1} | {2} | {3} | {4} |".format(
                category, elements, mirrored, hand, facing))

    output.print_md(u"**{0} element(s) updated, {1} already correct.**".format(
        tally.written, tally.unchanged))

    if not tally.any_flipped():
        output.print_md(u"_Nothing in the model is mirrored or flipped._")

    if tally.skipped:
        output.print_md(u"{0} element(s) were skipped because a parameter "
                        u"could not take the value.".format(tally.skipped))
    if tally.failed:
        output.print_md(u"**{0} element(s) had a write rejected.**".format(
            tally.failed))

    if missing_categories:
        output.print_md(u"_Not in this Revit version: {0}._".format(
            u", ".join(missing_categories)))

    if len(problems):
        output.print_md(u"#### Parameters that could not be used")
        for line in problems.lines():
            output.print_md(u"- {0}".format(line))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    if doc.IsFamilyDocument:
        forms.alert(
            u"This works on a project, not on a family document.",
            title=TITLE)
        return

    elements, missing_categories = collect_instances(doc)
    if not elements:
        forms.alert(
            u"No family instances were found in any of the six "
            u"categories this tool covers.",
            title=TITLE)
        return

    tally = model.Tally()
    problems = model.Problems()

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        for element in elements:
            label = category_label(element)
            state = flip_state(element)
            tally.count_element(label, state)

            wrote_any, skipped, failed = write_state(
                element, label, state, problems)
            if failed:
                tally.count_failed()
            elif skipped:
                tally.count_skipped()
            else:
                tally.count_write(wrote_any)
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was written. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the changes on commit, so nothing was "
            u"written ({0}).".format(to_text(status)),
            title=TITLE)
        return

    report(tally, problems, missing_categories)

    if len(problems):
        forms.alert(
            u"Done, but {0} parameter problem(s) were found. See the "
            u"output window: the parameters have to exist as Area "
            u"instance parameters on those categories.".format(
                len(problems)),
            title=TITLE)


if __name__ == "__main__":
    run()
