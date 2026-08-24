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

## Groups, which is what 2.14.0 got wrong

Writing an instance parameter onto an element inside a group is a change
to the group, unless that parameter is flagged to vary across group
instances. Revit refuses with "Changes to groups are allowed only in
group edit mode", an error that cannot be ignored, and the only way it
offers to proceed is **Ungroup**, which dissolves every group instance
the run touched.

2.14.0 read `VariesAcrossGroups`, reported that it was off, and wrote
anyway on the reasoning that the value is still right outside groups.
That reasoning walked a real model into that dialog. Three things now
stand between the tool and it:

1. When the flag is off and elements are actually in groups, the run
   offers to set it, naming the parameters. Setting it is its own
   transaction, committed before anything else is written.
2. If the offer is declined, elements inside groups are skipped and
   counted. Everything outside a group is still written.
3. A `IFailuresPreprocessor` on the write transaction rolls the whole
   thing back if that failure appears anyway, so the Ungroup option is
   never presented. It is inherited alone, because mixing
   `IFailuresPreprocessor` with a plain base class breaks the method
   resolution order and the handler silently never runs. That cost a
   session on Remove Level already.

## Report only, still

Nothing here creates or binds a parameter. A parameter that is missing,
is not an Area, or is read only is reported with the category and
parameter named, and those elements are skipped. The one thing the tool
will change on request is the vary across groups flag on a binding that
already exists, because that is the difference between the tool working
and the tool being dangerous.

## Only writes what changed

A value already correct is left alone. Rewriting the same number to
every element marks the whole model as modified, which on a workshared
job turns a check into a sync.

## Unverified

Not yet run to completion in Revit. `FamilyInstance.Mirrored`,
`HandFlipped` and `FacingFlipped`, `Definition.GetDataType` against
`SpecTypeId.Area`, `InternalDefinition.SetAllowVaryBetweenGroups`,
`Element.GroupId`, and the failure preprocessor. The group failure
itself is confirmed: Björn hit it on Eldisgarður at 2.14.0.
"""

__title__ = "Flip\nStatus"
__author__ = "AVH"
__doc__ = ("Record mirrored, hand flipped and facing flipped state into "
           "Area parameters on casework, doors, equipment, generic "
           "models and windows, so they can be scheduled and filtered. "
           "Handles grouped elements rather than walking into the "
           "ungroup dialog.")

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


class GroupFailureGuard(DB.IFailuresPreprocessor):
    """Roll back rather than let Revit offer to ungroup anything.

    Inherited alone. Mixing `IFailuresPreprocessor` with another base
    class breaks the method resolution order, the interface method never
    runs, and Revit falls back to showing the user the dialog this class
    exists to prevent.
    """

    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, failures_accessor):
        try:
            for failure in failures_accessor.GetFailureMessages():
                try:
                    self.messages.append(to_text(failure.GetDescriptionText()))
                except BaseException:
                    pass
            if failures_accessor.GetSeverity() == DB.FailureSeverity.Error:
                return DB.FailureProcessingResult.ProceedWithRollBack
        except BaseException as exc:
            logger.debug(to_text(exc))
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def guard_transaction(transaction, guard):
    """Attach the failure guard. Returns True if it took."""
    try:
        options = transaction.GetFailureHandlingOptions()
        options = options.SetFailuresPreprocessor(guard)
        options = options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
        return True
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False


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


def in_group(element):
    """True when the element belongs to a group."""
    try:
        group_id = element.GroupId
    except BaseException:
        return False
    if group_id is None:
        return False
    try:
        return group_id != DB.ElementId.InvalidElementId
    except BaseException:
        return False


def flip_state(element):
    """The three booleans. A property that will not answer is False."""
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

    try:
        data_type = parameter.Definition.GetDataType()
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


def definition_of(parameter):
    try:
        return parameter.Definition
    except BaseException:
        return None


def varies_across_groups(definition):
    """True, False, or None when it cannot be determined."""
    if definition is None:
        return None
    try:
        value = getattr(definition, "VariesAcrossGroups", None)
        if value is None:
            return None
        return bool(value)
    except BaseException:
        return None


def survey_definitions(elements):
    """One definition per parameter, and whether it varies across groups.

    Sampled from the elements rather than walked out of
    `doc.ParameterBindings`, because what matters is the definition the
    write will actually go through.
    """
    found = {}
    for element in elements:
        for name, _key, _label in model.PARAMETERS:
            if name in found:
                continue
            try:
                parameter = element.LookupParameter(name)
            except BaseException:
                parameter = None
            if parameter is None:
                continue
            definition = definition_of(parameter)
            if definition is None:
                continue
            found[name] = (definition, varies_across_groups(definition))
        if len(found) == len(model.PARAMETERS):
            break
    return found


def allow_varying(doc, definitions, names):
    """Set the vary across groups flag. Returns (applied, failures)."""
    failures = []
    transaction = DB.Transaction(doc, TITLE + u": vary across groups")
    transaction.Start()
    try:
        for name in names:
            definition = definitions[name][0]
            definition.SetAllowVaryBetweenGroups(doc, True)
    except BaseException as exc:
        transaction.RollBack()
        return False, [u"{0}: {1}".format(name, to_text(exc))]

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        return False, [u"Revit rejected the change ({0})".format(
            to_text(status))]
    return True, failures


def write_state(element, label, state, problems, skip_names):
    """Write the three parameters. Returns (wrote_any, skipped, failed)."""
    values = model.desired_values(state)
    wrote_any = False
    skipped = False
    failed = False

    for name, _key, _report_label in model.PARAMETERS:
        if name in skip_names:
            skipped = True
            continue

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


def report(tally, problems, missing_categories, grouped_skipped, fixed_names):
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

    if fixed_names:
        output.print_md(
            u"_Vary across group instances was switched on for: {0}._".format(
                u", ".join(fixed_names)))

    if grouped_skipped:
        output.print_md(
            u"**{0} element(s) inside groups were skipped**, because the "
            u"parameter does not vary across group instances. Writing them "
            u"would have made Revit offer to ungroup them.".format(
                grouped_skipped))

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

    definitions = survey_definitions(elements)
    not_varying = [name for name, _key, _label in model.PARAMETERS
                   if definitions.get(name, (None, None))[1] is False]
    grouped = [element for element in elements if in_group(element)]

    fixed_names = []
    skip_names = set()

    # Only worth asking when it actually matters. A model with no groups
    # is not made better by a dialog about groups.
    if not_varying and grouped:
        answer = forms.alert(
            u"{0} of the elements are inside groups, and these parameters "
            u"are not set to vary across group instances:\n\n{1}\n\n"
            u"Writing to a grouped element without that flag makes Revit "
            u"refuse the change and offer to ungroup. Switch the flag on "
            u"now? Answering No skips the grouped elements and writes "
            u"everything else.".format(len(grouped), u"\n".join(not_varying)),
            title=TITLE, yes=True, no=True)

        if answer:
            applied, failures = allow_varying(doc, definitions, not_varying)
            if applied:
                fixed_names = list(not_varying)
            else:
                skip_names = set(not_varying)
                forms.alert(
                    u"The flag could not be set, so grouped elements are "
                    u"being skipped instead. {0}".format(
                        u" ".join(failures)),
                    title=TITLE)
        else:
            skip_names = set(not_varying)

    tally = model.Tally()
    problems = model.Problems()
    grouped_skipped = 0
    guard = GroupFailureGuard()

    transaction = DB.Transaction(doc, TITLE)
    guard_transaction(transaction, guard)
    transaction.Start()
    try:
        for element in elements:
            label = category_label(element)
            state = flip_state(element)
            tally.count_element(label, state)

            element_skip = set()
            if skip_names and in_group(element):
                element_skip = skip_names
                grouped_skipped += 1
                for name in skip_names:
                    problems.add(label, name, model.Problems.NOT_VARYING)

            wrote_any, skipped, failed = write_state(
                element, label, state, problems, element_skip)
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
        detail = u""
        if guard.messages:
            detail = u" Revit said: {0}".format(u" / ".join(guard.messages))
        forms.alert(
            u"Revit rejected the changes, so nothing was written ({0})."
            u"{1}".format(to_text(status), detail),
            title=TITLE)
        if guard.messages:
            output.print_md(u"### {0}".format(TITLE))
            output.print_md(u"**Rolled back.** Revit reported:")
            for message in guard.messages:
                output.print_md(u"- {0}".format(message))
        return

    report(tally, problems, missing_categories, grouped_skipped, fixed_names)

    if len(problems):
        forms.alert(
            u"Done, but {0} parameter problem(s) were found. See the "
            u"output window.".format(len(problems)),
            title=TITLE)


if __name__ == "__main__":
    run()
