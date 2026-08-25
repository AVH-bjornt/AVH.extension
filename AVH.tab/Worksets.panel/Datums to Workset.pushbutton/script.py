# -*- coding: utf-8 -*-
"""Put every grid, level and view into one workset.

Pick a workset and the tool moves all grids, all levels and all
graphical views into it. AVH's is called "Shared Views, Levels, Grids",
which the picker offers first and marks, but the list is whatever the
model actually has, so a project that names it differently still works.

## Views may refuse

A grid's and a level's workset are ordinary editable parameters. **A
view's may not be.** Revit does not let you change a view's workset in
the interface at all, and whether the API allows it varies. So every
write is checked, both its return value and for an exception, and
anything refused is reported by kind and reason rather than silently
skipped. If views turn out to be immovable on your Revit, the report
will say so plainly and the grids and levels still move.

Graphical views only: plans, sections, elevations, 3D, drafting and
legends. Sheets, schedules and view templates are left where they are,
by decision, because moving sheets to a datum workset is a bigger change
than it sounds.

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

Not yet run in Revit: whether `ELEM_PARTITION_PARAM` is writable on a
view, `WorksharingUtils.GetCheckoutStatus`, and
`FilteredWorksetCollector`. The whole run is one transaction whose
commit status is checked, so a rejected write cannot report success.
"""

__title__ = "Datums to\nWorkset"
__author__ = "AVH"
__doc__ = ("Put every grid, level and graphical view into one workset, "
           "normally Shared Views, Levels, Grids. Reports anything that "
           "refuses, including views, which Revit may not allow.")

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

# View kinds that are not drawings and should be left alone. Checked by
# name through getattr, so a Revit without one of them simply drops it.
#
# Sheets and schedules are deliberately **not** here: they are excluded
# by class below, and listing them in both places meant removing either
# guard changed nothing, which is how a check becomes decoration.
# ColumnSchedule and PanelSchedule stay, because those are not
# necessarily ViewSchedule instances and the class test may miss them.
SKIP_VIEW_TYPES = ("Internal", "ProjectBrowser", "SystemBrowser",
                   "ColumnSchedule", "PanelSchedule", "Undefined")


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


def is_graphical_view(view):
    try:
        if view.IsTemplate:
            return False
    except BaseException:
        return False
    for class_name in ("ViewSheet", "ViewSchedule"):
        cls = getattr(DB, class_name, None)
        if cls is not None and isinstance(view, cls):
            return False
    try:
        view_type = view.ViewType
    except BaseException:
        return False
    for name in SKIP_VIEW_TYPES:
        wanted = getattr(DB.ViewType, name, None)
        if wanted is not None and view_type == wanted:
            return False
    return True


def collect(doc):
    """(kind, element) for everything this tool moves."""
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

    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        for view in collector:
            if is_graphical_view(view):
                found.append((model.VIEWS, view))
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
        forms.alert(u"No grids, levels or views were found.", title=TITLE)
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
