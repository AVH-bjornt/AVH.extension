# -*- coding: utf-8 -*-
"""Put every grid and level into one workset.

Pick a workset and the tool moves all grids and all levels into it.
AVH's is called "Shared Views, Levels, Grids", which the picker offers
first and marks, but the list is whatever the model actually has, so a
project that names it differently still works.

## Views are not touched, and that is settled

2.18.0 moved graphical views as well. On Eldisgardur they refused, which
is what the Revit interface implies: it offers no way to change a view's
workset, and the API declines to either. Rather than leave a category in
that reports a failure on every run, views are out entirely as of
2.18.1. The name is now literally right: a datum is a grid or a level.

If a way to move views ever turns up, it belongs in its own tool with
its own report, not as a permanently failing third of this one.

## Nothing needless is written

An element already in the target workset is left alone and counted
separately. Rewriting the same value onto every grid marks the whole
model as modified, which on a workshared job turns a tidy up into a
sync.

## Elements somebody else has

An element checked out by another user cannot be changed, and trying is
how a run dies half way through. Each one is checked first through
`WorksharingUtils.GetCheckoutStatus`, skipped, and named in the report.

## Unverified

`WorksharingUtils.GetCheckoutStatus` and `FilteredWorksetCollector` have
not been exercised on a model with several people in it. The whole run
is one transaction whose commit status is checked, so a rejected write
cannot report success.
"""

__title__ = "Datums to\nWorkset"
__author__ = "AVH"
__doc__ = ("Put every grid and level into one workset, normally Shared "
           "Views, Levels, Grids. Views are not touched: Revit does not "
           "allow their workset to be changed.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. A button nested one deeper, in a pulldown, was enough
# to break the fixed count, and it breaks at import time with a message
# about a module nobody has heard of.
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
from avh_worksets import model                    # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Datums to Workset"

MAX_LISTED = 20


def element_name(element):
    if element is None:
        return u""
    try:
        return to_text(element.Name)
    except BaseException:
        return u""


def user_worksets(doc):
    worksets = []
    try:
        collector = DB.FilteredWorksetCollector(doc)
        collector = collector.OfKind(DB.WorksetKind.UserWorkset)
        for workset in collector:
            worksets.append(workset)
    except BaseException as exc:
        logger.debug(to_text(exc))
    return worksets


def pick_workset(doc):
    worksets = user_worksets(doc)
    if not worksets:
        forms.alert(u"This model has no user worksets.", title=TITLE)
        return None

    by_name = {}
    for workset in worksets:
        by_name.setdefault(element_name(workset), workset)

    labels, mapping = model.workset_labels(sorted(by_name.keys()))
    chosen = forms.SelectFromList.show(
        labels, multiselect=False,
        title=TITLE + u": which workset?", button_name="Move them there")
    if not chosen:
        return None
    if isinstance(chosen, list):
        chosen = chosen[0] if chosen else None
    return by_name.get(mapping.get(to_text(chosen)))


def collect(doc):
    """(kind, element) for everything this tool moves.

    Grids and levels, and nothing else. Views were collected here until
    2.18.1 and refused every time.
    """
    found = []
    for kind, cls_name in ((model.GRIDS, "Grid"), (model.LEVELS, "Level")):
        cls = getattr(DB, cls_name, None)
        if cls is None:
            continue
        try:
            collector = DB.FilteredElementCollector(doc).OfClass(cls)
            collector = collector.WhereElementIsNotElementType()
            for element in collector:
                found.append((kind, element))
        except BaseException as exc:
            logger.debug(to_text(exc))
    return found


def owned_by_someone_else(doc, element):
    """True only when Revit says so. An unavailable check is not a no."""
    try:
        status = DB.WorksharingUtils.GetCheckoutStatus(doc, element.Id)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False
    try:
        return status == DB.CheckoutStatus.OwnedByOtherUser
    except BaseException:
        return False


def workset_parameter(element):
    try:
        return element.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def move(doc, kind, element, target_id, tally):
    """Move one element. Everything it decides goes into the tally."""
    name = element_name(element)

    parameter = workset_parameter(element)
    if parameter is None:
        tally.count_skipped(kind, model.NO_PARAMETER, name, element.Id)
        return

    try:
        current = parameter.AsInteger()
    except BaseException:
        current = None

    if not model.needs_move(current, target_id):
        tally.count_already(kind)
        return

    if parameter.IsReadOnly:
        tally.count_skipped(kind, model.READ_ONLY, name, element.Id)
        return

    if owned_by_someone_else(doc, element):
        tally.count_skipped(kind, model.OWNED, name, element.Id)
        return

    try:
        if not parameter.Set(target_id):
            tally.count_skipped(kind, model.REFUSED, name, element.Id)
            return
    except BaseException as exc:
        logger.debug(to_text(exc))
        tally.count_skipped(kind, model.REFUSED, name, element.Id)
        return

    tally.count_moved(kind)


def report(tally, workset_name):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"Target workset: **{0}**".format(workset_name))

    rows = tally.rows()
    if rows:
        output.print_md(u"| | Moved | Already there | Left alone |")
        output.print_md(u"| --- | ---: | ---: | ---: |")
        for label, moved, already, skipped in rows:
            output.print_md(u"| {0} | {1} | {2} | {3} |".format(
                label, moved, already, skipped))

    output.print_md(u"**{0} moved**, {1} already there.".format(
        tally.total_moved(), tally.total_already()))

    reasons = tally.reasons()
    if reasons:
        output.print_md(u"#### Left alone")
        for label, reason, count in reasons:
            output.print_md(u"- {0}: {1} ({2})".format(label, reason, count))

        output.print_md(u"_Which ones:_")
        for kind, reason, name, element_id in tally.problems[:MAX_LISTED]:
            output.print_md(u"- {0} {1}: {2}".format(
                output.linkify(element_id), name or u"unnamed", reason))
        if len(tally.problems) > MAX_LISTED:
            output.print_md(u"- _and {0} more_".format(
                len(tally.problems) - MAX_LISTED))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    try:
        workshared = doc.IsWorkshared
    except BaseException:
        workshared = False
    if not workshared:
        forms.alert(
            u"This model is not workshared, so it has no worksets.",
            title=TITLE)
        return

    workset = pick_workset(doc)
    if workset is None:
        return
    workset_name = element_name(workset)

    try:
        target_id = workset.Id.IntegerValue
    except BaseException as exc:
        forms.alert(
            u"That workset's id could not be read: {0}".format(to_text(exc)),
            title=TITLE)
        return

    elements = collect(doc)
    if not elements:
        forms.alert(u"No grids or levels were found.", title=TITLE)
        return

    tally = model.Tally()

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        for kind, element in elements:
            move(doc, kind, element, target_id, tally)
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was changed. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the changes, so nothing was moved "
            u"({0}).".format(to_text(status)),
            title=TITLE)
        return

    report(tally, workset_name)

    if tally.total_skipped():
        forms.alert(
            u"Done. {0} moved, and {1} could not be. See the output "
            u"window for which and why.".format(
                tally.total_moved(), tally.total_skipped()),
            title=TITLE)


if __name__ == "__main__":
    run()
