# -*- coding: utf-8 -*-
"""Rise and going of every stair, against the rules for its use.

Reads each stair's riser, going and nosing and checks them against
Byggingarregluger\u00f0 112/2012, 6.4.9 and 6.4.10, or, for a stair
marked To_Machinery, against IST EN ISO 14122 (part 1 for which kind of
access the pitch makes it, part 3 for stairs and step ladders, part 4
for fixed ladders). Nothing else about the stair is checked: not width,
landings, headroom or railings.

The rules live in `avh_stairs.rules` and are tested without Revit.

## The stair's use decides the rules

Each stair carries its use in the text parameter AVH_Stair_Use:
almennur, annar, uti or To_Machinery. The codes used by Stair Check
2.33 (ibud, fyrirtaeki, uti_adal and the rest) are still read. If the
model has no such parameter the tool offers to add it, and stairs
without a value can be set from a list before the check runs.

## Unverified

Confirmed in Revit (2.33.1): ActualRiserHeight, ActualTreadDepth,
GetStairsRuns, run elevations, StairsRunType.NosingLength, and
IsInEditMode being a method. Not yet confirmed: StairsRun
ActualRisersNumber, used to compare the riser of each flight.
"""

__author__ = "AVH"
__doc__ = "Check the rise and going of every stair: Byggingarreglugerd 6.4.9 and 6.4.10, or IST EN ISO 14122 for stairs set to To_Machinery."

import os
import sys
import tempfile

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
from avh_stairs import rules                      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"G\u00f6nguhlutfall stiga"
METRES_PER_FOOT = 0.3048

# A fixed GUID, so the parameter is the same shared parameter in every
# model it is added to. Unchanged from Stair Check 2.33.
PARAMETER_GUID = "f03e1b0f-96ae-4317-ba74-010f759f77eb"


def metres(feet):
    if feet is None:
        return None
    try:
        return float(feet) * METRES_PER_FOOT
    except BaseException:
        return None


def read(obj, name, default=None):
    """A property's value, or default when it is missing or raises.

    Also takes a member that turns out to be a method, and calls it.
    Stairs.IsInEditMode is one: read as a property it is a bound method,
    which is truthy, so every stair looked as if it were being edited and
    the first live run (2026-11-11) found "no stairs" in a model full of
    them. Guessing property or method per member is how that happened,
    so this no longer depends on the guess.
    """
    try:
        value = getattr(obj, name)
    except BaseException as exc:
        logger.debug(u"{0}: {1}".format(name, to_text(exc)))
        return default
    if callable(value) and not isinstance(value, type):
        try:
            value = value()
        except BaseException as exc:
            logger.debug(u"{0}(): {1}".format(name, to_text(exc)))
            return default
    return value


def call(obj, name, *args):
    try:
        return getattr(obj, name)(*args)
    except BaseException as exc:
        logger.debug(u"{0}(): {1}".format(name, to_text(exc)))
        return None


def id_value(element_id):
    value = read(element_id, "Value")
    if value is None:
        value = read(element_id, "IntegerValue")
    return value


def valid(element_id):
    try:
        return element_id is not None and \
            element_id != DB.ElementId.InvalidElementId
    except BaseException:
        return False


def ids(collection):
    if collection is None:
        return []
    try:
        return list(collection)
    except BaseException:
        return []


# --------------------------------------------------------------------------
# The parameter
# --------------------------------------------------------------------------

def use_parameter(stair):
    try:
        return stair.LookupParameter(rules.PARAMETER)
    except BaseException:
        return None


def use_value(stair):
    parameter = use_parameter(stair)
    if parameter is None:
        return None
    try:
        return parameter.AsString()
    except BaseException:
        return None


def add_parameter(doc):
    """Bind AVH_Stair_Use to Stairs as a shared instance parameter.

    Revit only creates shared parameters through a shared parameter
    file, so a temporary one is used and the user's own file name is put
    back afterwards, whatever happens.
    """
    app = doc.Application
    original = None
    path = os.path.join(tempfile.gettempdir(), "AVH_Stair_Use_params.txt")
    try:
        original = app.SharedParametersFilename
    except BaseException:
        pass
    transaction = DB.Transaction(doc, TITLE + u": add " + rules.PARAMETER)
    try:
        handle = open(path, "w")
        handle.close()
        app.SharedParametersFilename = path
        definition_file = app.OpenSharedParameterFile()
        group = definition_file.Groups.Create("AVH")
        options = DB.ExternalDefinitionCreationOptions(
            rules.PARAMETER, DB.SpecTypeId.String.Text)
        from System import Guid
        options.GUID = Guid(PARAMETER_GUID)
        definition = group.Definitions.Create(options)
        categories = app.Create.NewCategorySet()
        categories.Insert(DB.Category.GetCategory(
            doc, DB.BuiltInCategory.OST_Stairs))
        binding = app.Create.NewInstanceBinding(categories)
        transaction.Start()
        inserted = doc.ParameterBindings.Insert(
            definition, binding, DB.GroupTypeId.IdentityData)
        if not inserted:
            transaction.RollBack()
            return u"Revit would not bind the parameter."
        status = transaction.Commit()
        if status != DB.TransactionStatus.Committed:
            return u"Revit rejected the parameter ({0}).".format(
                to_text(status))
        return u""
    except BaseException as exc:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except BaseException:
            pass
        return u"The parameter could not be added: {0}".format(to_text(exc))
    finally:
        try:
            if original is not None:
                app.SharedParametersFilename = original
        except BaseException:
            pass
        # The file only exists for Revit to read the definition from;
        # nothing is left behind in %TEMP%.
        try:
            if os.path.exists(path):
                os.remove(path)
        except BaseException:
            pass


def level_of(doc, stair):
    """The stair's base level name, or u"" when it cannot be read."""
    try:
        level = doc.GetElement(stair.get_Parameter(
            DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId())
        return to_text(level.Name) if level is not None else u""
    except BaseException:
        return u""


def stair_label(doc, stair):
    base = metres(read(stair, "BaseElevation"))
    top = metres(read(stair, "TopElevation"))
    risers = read(stair, "ActualRisersNumber")
    level_name = level_of(doc, stair)
    parts = []
    if level_name:
        parts.append(u"from {0}".format(level_name))
    if base is not None and top is not None:
        parts.append(u"{0:.2f} to {1:.2f} m".format(base, top))
    if risers is not None:
        parts.append(u"{0} risers".format(risers))
    parts.append(u"id {0}".format(id_value(stair.Id)))
    return u", ".join(parts)


def classify(doc, stairs):
    """Offer to set AVH_Stair_Use on stairs that have no value.

    One round: pick stairs, pick a use, write it; then again for those
    left, until none are left or the user stops.
    """
    while True:
        blank = [s for s in stairs if use_parameter(s) is not None and
                 not to_text(use_value(s)).strip()]
        if not blank:
            return
        labels = {}
        for stair in blank:
            labels[stair_label(doc, stair)] = stair
        picked = forms.SelectFromList.show(
            sorted(labels.keys()), multiselect=True,
            title=TITLE + u": {0} stair(s) have no use. Tick some, or "
                  u"cancel to check without".format(len(blank)),
            button_name=u"Set use for ticked")
        if not picked:
            return
        if not isinstance(picked, list):
            picked = [picked]
        chosen = [labels[to_text(p)] for p in picked if to_text(p) in labels]
        if not chosen:
            return
        options = [u"{0}  -  {1}".format(use.code, use.label)
                   for use in rules.USES]
        answer = forms.SelectFromList.show(
            options, multiselect=False,
            title=TITLE + u": use of the {0} ticked stair(s)".format(
                len(chosen)),
            button_name=u"Set")
        if not answer:
            return
        if isinstance(answer, list):
            answer = answer[0] if answer else None
        use, _problem = rules.parse_use(
            to_text(answer).split(u"  -  ")[0].strip())
        if use is None:
            return
        code = use.code
        transaction = DB.Transaction(doc, TITLE + u": set stair use")
        transaction.Start()
        refused = 0
        try:
            for stair in chosen:
                parameter = use_parameter(stair)
                if parameter is None or parameter.IsReadOnly or \
                        not parameter.Set(code):
                    refused += 1
        except BaseException as exc:
            transaction.RollBack()
            forms.alert(u"The use could not be set: {0}".format(
                to_text(exc)), title=TITLE)
            return
        status = transaction.Commit()
        if status != DB.TransactionStatus.Committed:
            forms.alert(u"Revit rejected the change ({0}).".format(
                to_text(status)), title=TITLE)
            return
        if refused:
            forms.alert(u"{0} stair(s) would not take the value.".format(
                refused), title=TITLE)
            return


# --------------------------------------------------------------------------
# Reading a stair
# --------------------------------------------------------------------------

def read_runs(doc, stair):
    runs = []
    for run_id in ids(call(stair, "GetStairsRuns")):
        run = doc.GetElement(run_id)
        if run is None:
            continue
        base = read(run, "BaseElevation")
        top = read(run, "TopElevation")
        rise = None
        if base is not None and top is not None:
            rise = metres(top - base)
        runs.append(rules.Run(id_value(run_id), rise=rise,
                              risers=read(run, "ActualRisersNumber")))
    return runs


def read_nosing(doc, stair):
    """The nosing length in metres, 0 for steps without treads, or None."""
    try:
        stair_type = doc.GetElement(stair.GetTypeId())
        run_type = doc.GetElement(stair_type.RunType)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None
    if run_type is None:
        return None
    if read(run_type, "HasTreads") is False:
        return 0.0
    return metres(read(run_type, "NosingLength"))


def plan_of(stair):
    try:
        box = stair.get_BoundingBox(None)
    except BaseException:
        box = None
    if box is None:
        return None
    return (metres(box.Min.X), metres(box.Min.Y),
            metres(box.Max.X), metres(box.Max.Y))


class Reading(object):
    def __init__(self, element, data, level=u""):
        self.element = element
        self.data = data
        self.level = level


def read_stair(doc, stair):
    data = rules.Stair(
        id_value(stair.Id), use_value=use_value(stair),
        riser=metres(read(stair, "ActualRiserHeight")),
        going=metres(read(stair, "ActualTreadDepth")),
        base=metres(read(stair, "BaseElevation")),
        top=metres(read(stair, "TopElevation")),
        runs=read_runs(doc, stair), nosing=read_nosing(doc, stair),
        plan=plan_of(stair))
    return Reading(stair, data, level_of(doc, stair))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

# Every check's results, for the harness to read back. The report shows
# only the problems, so the passes are not in the output window.
RESULTS = []

ORDER = {rules.FAIL: 0, rules.CHECK: 1, rules.NOT_CHECKED: 2, rules.PASS: 3}

SUMMARY_LABEL = {
    rules.FAIL: u"FAIL",
    rules.CHECK: u"check",
    rules.NOT_CHECKED: u"incomplete",
    rules.PASS: u"pass",
}

LINE_LABEL = {
    rules.FAIL: u"FAIL",
    rules.CHECK: u"check",
    rules.NOT_CHECKED: u"not checked",
    rules.PASS: u"pass",
}


def cell(text):
    """Text safe inside a markdown table cell."""
    return to_text(text).replace(u"|", u"/").replace(u"\n", u" ")


def problems_of(results):
    return [r for r in results if r.status != rules.PASS]


def measured(data):
    """Riser, going and pitch as table cells, measured even when no rule
    could be applied."""
    riser = u"{0:.0f}".format(data.riser * 1000) if data.riser else u""
    going = u"{0:.0f}".format(data.going * 1000) if data.going else u""
    slope = rules.slope_of(data.riser, data.going)
    pitch = u"{0:.1f}\u00b0".format(slope) if slope is not None else u""
    return riser, going, pitch


def report(entries):
    """A summary table of every stair, then only what needs attention.

    Each table is printed in one call: pyRevit draws every print_md call
    as its own block, and a table printed a row at a time came out as a
    stack of one row tables with their own column widths.
    """
    counts = {}
    for _reading, _use, results in entries:
        status = rules.worst(results)
        counts[status] = counts.get(status, 0) + 1
    output.print_md(u"\n".join([
        u"### {0}".format(TITLE),
        u"Byggingarregluger\u00f0 112/2012 (\u00fatg\u00e1fa \u00ed gildi "
        u"fr\u00e1 1. ma\u00ed 2026) 6.4.9 and 6.4.10; IST EN ISO 14122:2016 "
        u"for stairs set to To_Machinery.",
        u"",
        u"**{0} stair(s): {1} fail, {2} to check, {3} incomplete, {4} "
        u"pass.**".format(len(entries), counts.get(rules.FAIL, 0),
                          counts.get(rules.CHECK, 0),
                          counts.get(rules.NOT_CHECKED, 0),
                          counts.get(rules.PASS, 0))]))

    entries = sorted(entries, key=lambda e: (ORDER[rules.worst(e[2])],
                                             e[0].level, e[0].data.element_id))
    table = [u"| Stair | Level | Use | Riser mm | Going mm | Pitch | Result "
             u"| Needs attention |",
             u"| --- | --- | --- | ---: | ---: | ---: | --- | --- |"]
    for reading, use, results in entries:
        names = []
        for result in problems_of(results):
            if result.rule not in names:
                names.append(result.rule)
        riser, going, pitch = measured(reading.data)
        table.append(u"| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} "
                     u"|".format(
                         output.linkify(reading.element.Id),
                         cell(reading.level),
                         cell(use.code if use is not None else u"(none)"),
                         riser, going, pitch,
                         SUMMARY_LABEL[rules.worst(results)],
                         cell(u", ".join(names))))
    output.print_md(u"\n".join(table))

    for reading, use, results in entries:
        problems = problems_of(results)
        if not problems:
            continue
        problems.sort(key=lambda r: ORDER[r.status])
        lines = [u"#### {0} {1}, {2}".format(
            output.linkify(reading.element.Id), cell(reading.level),
            use.label if use is not None else u"no use set")]
        for result in problems:
            lines.append(u"- **{0}** {1}{2}: {3}".format(
                LINE_LABEL[result.status],
                result.source + u" " if result.source else u"",
                result.rule, result.message))
        passed = len(results) - len(problems)
        if passed:
            lines.append(u"- {0} other rule(s) pass".format(passed))
        output.print_md(u"\n".join(lines))


def find_stairs(doc):
    """(stairs to check, how many are in edit mode, error text)."""
    stairs_class = getattr(DB.Architecture, "Stairs", None)
    found = []
    editing = 0
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(stairs_class)
        for stair in collector.WhereElementIsNotElementType():
            if read(stair, "IsInEditMode", False) is True:
                editing += 1
            else:
                found.append(stair)
    except BaseException as exc:
        return found, editing, to_text(exc)
    return found, editing, u""


def no_stairs_message(doc, editing, problem):
    """Say what there is instead, so "no stairs" is never a dead end."""
    lines = [u"No Revit stairs were found to check."]
    if problem:
        lines.append(u"Revit refused the search: {0}".format(problem))
    if editing:
        lines.append(u"{0} stair(s) are being edited. Finish the edit and "
                     u"run again.".format(editing))
    others = {}
    try:
        collector = DB.FilteredElementCollector(doc).OfCategory(
            DB.BuiltInCategory.OST_Stairs).WhereElementIsNotElementType()
        for element in collector:
            kind = to_text(read(element.GetType(), "Name", u"?"))
            if kind != u"Stairs":
                others[kind] = others.get(kind, 0) + 1
    except BaseException as exc:
        logger.debug(to_text(exc))
    if others:
        lines.append(u"The Stairs category holds {0}, which are not Revit "
                     u"stairs (stair families or older sketched stairs) "
                     u"and cannot be read this way.".format(u", ".join(
                         u"{0} {1}".format(count, kind)
                         for kind, count in sorted(others.items()))))
    linked = []
    try:
        for link in DB.FilteredElementCollector(doc).OfClass(
                DB.RevitLinkInstance):
            link_doc = link.GetLinkDocument()
            if link_doc is None:
                continue
            count = len(list(DB.FilteredElementCollector(link_doc).OfClass(
                getattr(DB.Architecture, "Stairs", None))
                .WhereElementIsNotElementType()))
            if count:
                linked.append(u"{0} in {1}".format(count, to_text(
                    read(link_doc, "Title", u"a link"))))
    except BaseException as exc:
        logger.debug(to_text(exc))
    if linked:
        lines.append(u"Linked models have stairs: {0}. This tool checks "
                     u"the open model only; open that model to check "
                     u"them.".format(u", ".join(linked)))
    return u"\n\n".join(lines)


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    stairs, editing, problem = find_stairs(doc)
    if not stairs:
        forms.alert(no_stairs_message(doc, editing, problem), title=TITLE)
        return

    if all(use_parameter(s) is None for s in stairs):
        if forms.alert(
                u"The stairs have no {0} parameter, so the tool cannot tell "
                u"which rules apply to which stair.\n\nAdd the parameter to "
                u"Stairs now?".format(rules.PARAMETER),
                title=TITLE, yes=True, no=True):
            problem = add_parameter(doc)
            if problem:
                forms.alert(problem, title=TITLE)
    classify(doc, stairs)

    readings = [read_stair(doc, stair) for stair in stairs]
    wells = rules.stairwells([r.data for r in readings])
    entries = []
    for reading in readings:
        use, results = rules.check(reading.data, wells)
        entries.append((reading, use, results))
    del RESULTS[:]
    RESULTS.extend(entries)
    report(entries)


if __name__ == "__main__":
    run()
