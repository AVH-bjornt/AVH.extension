# -*- coding: utf-8 -*-
"""Isolate everything in the model that Revit has a warning about.

One click. If the active view is already in temporary hide/isolate, the
click clears it instead, so the same button is on and off.

Shift click opens a list of the warning kinds in this model with counts,
so a single kind can be isolated on its own. Most models are mostly
"joined but do not intersect", and isolating all of it hides the two
warnings that actually matter.

## What it isolates, and what that looks like

Every element named by `Document.GetWarnings()`, model wide, not just
those in the current view. Elements that are not in the active view
simply do not appear, so a plan view can isolate three hundred elements
and look almost empty. That is confusing enough that the tool counts how
many of them the view can actually show and says so when the answer is
none.

The isolate is **temporary**, the same mode as Revit's own Temporary
Hide/Isolate. Nothing is written to the view's permanent visibility, and
closing the view clears it. That also means the button clears a
temporary hide somebody else set up, which is the price of one button
doing both directions.

## Deliberately not included

`FailureMessage.GetAdditionalElements`, the related elements Revit lists
alongside the cause. For "joined but do not intersect" both elements are
failing elements anyway, and for the rest the additional elements are
context rather than the thing that is wrong.

## Unverified

Not yet run in Revit: `Document.GetWarnings`, `GetFailingElements`,
`GetDescriptionText`, `View.IsTemporaryHideIsolateActive`,
`IsolateElementsTemporary`, `DisableTemporaryViewMode` and
`CanUseTemporaryVisibilityModes`. The run is one transaction whose commit
status is checked, so a rejected write cannot report success.
"""

__title__ = "Isolate\nWarnings"
__author__ = "AVH"
__doc__ = ("Isolate every element Revit has a warning about, in the "
           "active view. Click again to clear it. Shift click to pick "
           "which kind of warning to isolate.")

import os
import sys

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_warnings import model                    # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Isolate Warnings"


def shift_clicked():
    """True when the button was shift clicked.

    Two routes because pyRevit has offered two. `EXEC_PARAMS.config_mode`
    is the current one; the `__shiftclick__` global is what older scripts
    read and it is still injected. Whichever answers first wins, and
    neither is allowed to raise.
    """
    try:
        from pyrevit import EXEC_PARAMS
        value = getattr(EXEC_PARAMS, "config_mode", None)
        if value is not None:
            return bool(value)
    except BaseException:
        pass

    for source in (globals(), sys.modules["__main__"].__dict__):
        value = source.get("__shiftclick__")
        if value is not None:
            return bool(value)

    return False


def id_collection(ids):
    """A .NET ICollection<ElementId>, which is what the API wants."""
    from System.Collections.Generic import List
    collection = List[DB.ElementId]()
    for element_id in ids:
        collection.Add(element_id)
    return collection


def warning_groups(doc):
    """Warnings gathered by description, noisiest kind first."""
    entries = []
    for failure in doc.GetWarnings():
        try:
            description = to_text(failure.GetDescriptionText())
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        try:
            ids = list(failure.GetFailingElements())
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        entries.append((description, ids))
    return model.merge_by_description(entries)


def existing_only(doc, ids):
    """Drop ids the document will not resolve.

    A warning names elements that exist, so this should never remove
    anything. It costs one lookup each and it means a stale id cannot
    turn the isolate into an exception.
    """
    kept = []
    for element_id in ids:
        try:
            if doc.GetElement(element_id) is not None:
                kept.append(element_id)
        except BaseException:
            continue
    return kept


def visible_count(doc, view, ids):
    """How many of `ids` the active view can actually show.

    Isolating elements that are not in this view is legal and leaves the
    view looking empty, which reads as a broken tool rather than as a
    plan view being asked to show something on another level.
    """
    try:
        in_view = set()
        collector = DB.FilteredElementCollector(doc, view.Id)
        for element_id in collector.ToElementIds():
            in_view.add(model.id_key(element_id))
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None

    return len([element_id for element_id in ids
                if model.id_key(element_id) in in_view])


def clear_isolation(doc, view):
    """Turn the temporary mode off. Returns True if it committed."""
    transaction = DB.Transaction(doc, TITLE + u" off")
    transaction.Start()
    try:
        view.DisableTemporaryViewMode(
            DB.TemporaryViewMode.TemporaryHideIsolate)
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"The temporary isolate could not be cleared: {0}".format(
                to_text(exc)),
            title=TITLE)
        return False

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected clearing the isolate ({0}).".format(
                to_text(status)),
            title=TITLE)
        return False
    return True


def choose_groups(groups):
    """The shift click picker. Returns the groups to isolate, or None."""
    labels, mapping = model.picker_labels(groups)
    try:
        chosen = forms.SelectFromList.show(
            labels, multiselect=True, button_name="Isolate",
            title=TITLE + u": pick the warnings to isolate")
    except BaseException as exc:
        # This tool only changes what is visible, so a broken dialog is
        # not worth aborting over the way a write to the model would be.
        # Everything is the honest fallback, and the report says so.
        logger.debug(to_text(exc))
        output.print_md(u"_The picker was unavailable, so everything is "
                        u"isolated._")
        return groups

    if not chosen:
        return None

    wanted = [mapping[label] for label in chosen if label in mapping]
    return [group for group in groups if group[0] in wanted]


def report(groups, isolated, seen_in_view):
    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"**Isolated {0} element(s)** from {1} warning "
                    u"kind(s).".format(isolated, len(groups)))
    for description, ids in groups[:model.MAX_LISTED]:
        output.print_md(u"- {0}: **{1}**".format(
            model.truncate(description, 110), len(ids)))
    if len(groups) > model.MAX_LISTED:
        output.print_md(u"- _and {0} more kind(s) not listed_".format(
            len(groups) - model.MAX_LISTED))

    if seen_in_view is not None and seen_in_view < isolated:
        output.print_md(
            u"_{0} of them are in this view. The rest are elsewhere in "
            u"the model._".format(seen_in_view))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    view = doc.ActiveView
    if view is None:
        forms.alert(u"There is no active view.", title=TITLE)
        return

    try:
        usable = view.CanUseTemporaryVisibilityModes()
    except BaseException:
        usable = False
    if not usable:
        forms.alert(
            u"This kind of view cannot be temporarily isolated. Open a "
            u"plan, section or 3D view and try again.",
            title=TITLE)
        return

    # The toggle. Off first, so the button is its own undo.
    try:
        already_isolated = view.IsTemporaryHideIsolateActive()
    except BaseException:
        already_isolated = False
    if already_isolated:
        if clear_isolation(doc, view):
            output.print_md(u"### {0}".format(TITLE))
            output.print_md(u"Temporary isolate cleared.")
        return

    groups = warning_groups(doc)
    if not groups:
        forms.alert(u"This model has no warnings.", title=TITLE)
        return

    if shift_clicked():
        groups = choose_groups(groups)
        if groups is None:
            return
        if not groups:
            forms.alert(u"Nothing was picked.", title=TITLE)
            return

    ids = existing_only(doc, model.all_ids(groups))
    if not ids:
        forms.alert(
            u"The warnings in this model name no elements that still "
            u"exist, so there is nothing to isolate.",
            title=TITLE)
        return

    seen_in_view = visible_count(doc, view, ids)

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        view.IsolateElementsTemporary(id_collection(ids))
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was isolated. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the isolate, so nothing changed ({0}).".format(
                to_text(status)),
            title=TITLE)
        return

    report(groups, len(ids), seen_in_view)

    if seen_in_view == 0:
        forms.alert(
            u"{0} element(s) were isolated, but none of them are in this "
            u"view, so it looks empty. A 3D view will show them.".format(
                len(ids)),
            title=TITLE)


if __name__ == "__main__":
    run()
