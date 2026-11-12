# -*- coding: utf-8 -*-
"""Close the Shared Views, Levels, Grids workset in chosen links.

Pick the links from a list and each one is reloaded with that workset
closed, so the link's grids and levels stop showing in this model. Every
other workset in the link stays as it was: what is open now is read
from the loaded link, and the reload opens exactly that, less one.

## How

The Revit API has no way to close one workset in a loaded link. The
route is the same one Manage Links takes: reload the link type with a
WorksetConfiguration. `RevitLinkType.LoadFrom(path, config)` with a
configuration that starts from everything closed and opens the list
kept. It cannot run inside a transaction, and none is opened.

## Checked, not assumed

`LoadFrom` returns a LinkLoadResult, which is checked. Then the link's
worksets are read again from Revit and compared with what they were, so
a reload that came back "loaded" but left the workset open is reported
as exactly that.

## Unverified

Written for Revit 2025 and links on Autodesk Construction Cloud, and
not yet run in Revit. Three things are from the API documentation only:

- that `GetExternalFileReference().GetPath()` gives a path `LoadFrom`
  accepts for a cloud link. If it does not, the external resource route
  is tried next, and the report says which route each link took.
- that `Workset.IsOpen` on a linked document reports what was opened
  when the link loaded.
- that a WorksetId read from the loaded link is valid in a
  WorksetConfiguration for that link's file.
"""

__title__ = "Close Link\nDatums"
__author__ = "AVH"
__doc__ = ("Close the Shared Views, Levels, Grids workset in the links you "
           "pick, by reloading each one with that workset closed. Other "
           "worksets in the link are left as they are.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. See Datums to Workset for why.
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
from avh_worksets import links                    # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Close Link Datums"

# STAGE names the step in progress, so a failure can say where it was.
# Until 2.38.0 each stage also went to a trace file in Documents and each
# error to a crash log there; the button now writes no files, and errors
# go to the output window.
STAGE = u"start"


def trace(stage):
    global STAGE
    STAGE = stage


def show_error(note):
    """Print the exception being handled to the output window."""
    try:
        import traceback
        output.print_md(u"**{0}** failed at {1}".format(
            to_text(note), to_text(STAGE)))
        output.print_code(to_text(traceback.format_exc()))
    except BaseException:
        pass          # reporting an error must never raise another


def safe(call, default=None):
    try:
        return call()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return default


def describe(exc):
    """The exception's type and message.

    The message alone is not enough: on a live run the cloud route
    reported just "path". In IronPython a .NET exception carries the
    original as clsException, whose full type name and Message say more.
    """
    name = type(exc).__name__
    message = to_text(exc)
    net = getattr(exc, "clsException", None)
    if net is not None:
        name = to_text(safe(lambda: net.GetType().FullName, name))
        message = to_text(safe(lambda: net.Message, message))
    if message:
        return u"{0} ({1})".format(message, name)
    return name


def type_name(link_type):
    name = safe(lambda: link_type.Name)
    if not name:
        parameter = safe(lambda: link_type.get_Parameter(
            DB.BuiltInParameter.SYMBOL_NAME_PARAM))
        name = safe(lambda: parameter.AsString()) if parameter else None
    return to_text(name) or u"unnamed link"


def first_instance(doc, link_type):
    """A placed instance of this link type, or None."""
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(
            DB.RevitLinkInstance)
        for instance in collector:
            if safe(lambda: instance.GetTypeId()) == link_type.Id:
                return instance
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


def link_document(doc, link_type):
    instance = first_instance(doc, link_type)
    if instance is None:
        return None
    return safe(lambda: instance.GetLinkDocument())


def read_worksets(link_doc):
    """(key, name, is_open) for every user workset, or None if unreadable.

    is_open is None for a workset whose state would not read, which the
    model treats as unknown rather than as open or closed.
    """
    try:
        collector = DB.FilteredWorksetCollector(link_doc)
        collector = collector.OfKind(DB.WorksetKind.UserWorkset)
        found = []
        for workset in collector:
            key = workset.Id.IntegerValue
            name = to_text(workset.Name)
            is_open = safe(lambda: bool(workset.IsOpen))
            found.append((key, name, is_open))
        return found
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def read_link(doc, link_type):
    name = type_name(link_type)
    nested = bool(safe(lambda: link_type.IsNestedLink, False))
    link_doc = link_document(doc, link_type)
    if link_doc is None:
        return links.LinkInfo(name, loaded=False, nested=nested,
                              handle=link_type)
    workshared = bool(safe(lambda: link_doc.IsWorkshared, False))
    worksets = read_worksets(link_doc) if workshared else []
    return links.LinkInfo(name, loaded=True, nested=nested,
                          workshared=workshared, worksets=worksets,
                          handle=link_type)


def collect_links(doc):
    """Every link type, read. None when the links could not be listed.

    None and an empty list must stay different: "this model has no links"
    is not something to say about a model whose links would not read.
    A single link that fails to read is kept, with its worksets unknown,
    so it is reported rather than silently missing from the list.
    """
    trace(u"collect links")
    try:
        link_types = list(DB.FilteredElementCollector(doc).OfClass(
            DB.RevitLinkType))
    except BaseException:
        show_error(u"listing link types")
        return None
    found = []
    for link_type in link_types:
        name = type_name(link_type)
        trace(u"read link " + name)
        try:
            found.append(read_link(doc, link_type))
        except BaseException:
            show_error(u"reading link " + name)
            found.append(links.LinkInfo(name, worksets=None,
                                        handle=link_type))
    found.sort(key=lambda link: link.name.lower())
    return found


def net_ids(keys):
    """A .NET List[WorksetId] from integer keys; a plain list outside Revit."""
    ids = [DB.WorksetId(key) for key in keys]
    try:
        from System.Collections.Generic import List
        return List[DB.WorksetId](ids)
    except ImportError:
        return ids


def configuration(keys_to_open):
    config = DB.WorksetConfiguration(
        DB.WorksetConfigurationOption.CloseAllWorksets)
    if keys_to_open:
        config.Open(net_ids(keys_to_open))
    return config


def loaded_ok(result):
    if result is None:
        return False
    load_result = safe(lambda: result.LoadResult)
    expected = getattr(DB.LinkLoadResultType, "LinkLoaded", None)
    if expected is None:
        return load_result is not None
    return load_result == expected


def cloud_path(doc, link_type):
    """The loaded link's own cloud ModelPath, or None if it is not in one.

    Read before any reload is attempted, because a refused reload can
    leave the link unloaded and its document gone.
    """
    link_doc = link_document(doc, link_type)
    if link_doc is None:
        return None
    if not safe(lambda: link_doc.IsModelInCloud, False):
        return None
    return safe(lambda: link_doc.GetCloudModelPath())


def reload_routes(doc, link_type):
    """(label, path or reference) to try, in order.

    On a live project's ACC links (2.36.0) none offered a file path, and
    the external resource route closed 5 links of 6. The sixth came
    back as LinkNotFound, so the link's own cloud model path is tried
    after it.
    """
    routes = []
    reference = safe(lambda: link_type.GetExternalFileReference())
    if reference is not None:
        path = safe(lambda: reference.GetPath())
        if path is not None:
            routes.append((u"file path", path))
    resources = safe(lambda: link_type.GetExternalResourceReferences())
    if resources is not None:
        try:
            for pair in resources:
                value = getattr(pair, "Value", pair)
                if value is not None:
                    routes.append((u"external resource", value))
                    break
        except BaseException as exc:
            logger.debug(to_text(exc))
    path = cloud_path(doc, link_type)
    if path is not None:
        routes.append((u"cloud model path", path))
    return routes


def close_in(doc, link):
    """Reload one link with its datum workset closed.

    Returns (outcome, detail, route).
    """
    target = links.find_datum_workset(link.worksets)
    target_key = target[0]
    before = list(link.worksets)
    config = configuration(links.keep_open(before, target_key))

    routes = reload_routes(doc, link.handle)
    if not routes:
        return links.NO_PATH, u"", u""

    errors = []
    for label, path in routes:
        trace(u"reload {0} by {1}".format(link.name, label))
        try:
            result = link.handle.LoadFrom(path, config)
        except BaseException as exc:
            show_error(u"LoadFrom on {0} by {1}".format(link.name, label))
            errors.append(u"{0}: {1}".format(label, describe(exc)))
            continue
        trace(u"read back " + link.name)
        if not loaded_ok(result):
            status = safe(lambda: result.LoadResult)
            errors.append(u"{0}: {1}".format(label, to_text(status)))
            continue

        link_doc = link_document(doc, link.handle)
        after = read_worksets(link_doc) if link_doc is not None else None
        if after is None:
            return (links.RELOAD_FAILED,
                    u"reloaded, but the link could not be read back", label)
        return links.verify(before, after, target_key), u"", label

    if link_document(doc, link.handle) is None:
        errors.append(u"the link is now unloaded: reload it from Manage "
                      u"Links")
    return links.RELOAD_FAILED, u"; ".join(errors), u""


def progress(done, total):
    try:
        output.update_progress(done, total)
    except BaseException:
        pass


def pick(candidates):
    """The links ticked. [] when cancelled; None when the picker failed."""
    trace(u"picker")
    labels, mapping = links.picker_labels([link.name for link in candidates])
    try:
        chosen = forms.SelectFromList.show(
            labels, multiselect=True,
            title=TITLE + u": close Shared Views, Levels, Grids in which "
                          u"links?",
            button_name="Close it in these links")
    except BaseException:
        show_error(u"picker")
        return None
    if not chosen:
        return []
    if not isinstance(chosen, list):
        chosen = [chosen]
    picked = []
    for label in chosen:
        index = mapping.get(to_text(label))
        if index is not None:
            picked.append(candidates[index])
    return picked


def report(skipped, results):
    output.print_md(u"### {0}".format(TITLE))
    if results:
        output.print_md(u"| Link | Result | Route |")
        output.print_md(u"| --- | --- | --- |")
        for link, outcome, detail, route in results:
            text = outcome if not detail else u"{0}: {1}".format(
                outcome, detail)
            output.print_md(u"| {0} | {1} | {2} |".format(
                link.name, text, route or u"-"))
    closed = len([r for r in results if r[1] == links.CLOSED])
    output.print_md(u"**{0} of {1} closed.**".format(closed, len(results)))
    if skipped:
        output.print_md(u"#### Not offered")
        for link, state in skipped:
            output.print_md(u"- {0}: {1}".format(link.name, state))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if safe(lambda: doc.IsFamilyDocument, False):
        forms.alert(u"This is a family document; it has no links.",
                    title=TITLE)
        return

    found = collect_links(doc)
    if found is None:
        forms.alert(
            u"The links in this model could not be read, so nothing was "
            u"changed. The output window has the details.",
            title=TITLE)
        return
    if not found:
        forms.alert(u"This model has no Revit links.", title=TITLE)
        return

    candidates = []
    skipped = []
    for link in found:
        state = links.assess(link)
        if state == links.CAN_CLOSE:
            candidates.append(link)
        else:
            skipped.append((link, state))

    if not candidates:
        report(skipped, [])
        forms.alert(
            u"No link has Shared Views, Levels, Grids open, so there is "
            u"nothing to close. The output window says why for each link.",
            title=TITLE)
        return

    chosen = pick(candidates)
    if chosen is None:
        forms.alert(
            u"The link picker failed to open, so nothing was changed. "
            u"The output window has the details.",
            title=TITLE)
        return
    if not chosen:
        return

    results = []
    for index, link in enumerate(chosen):
        progress(index, len(chosen))
        try:
            outcome, detail, route = close_in(doc, link)
        except BaseException as exc:
            show_error(u"closing in " + link.name)
            outcome, detail, route = links.RELOAD_FAILED, describe(exc), u""
        results.append((link, outcome, detail, route))
    progress(len(chosen), len(chosen))

    trace(u"report")
    report(skipped, results)

    failed = [r for r in results if r[1] != links.CLOSED]
    if failed:
        forms.alert(
            u"{0} of {1} links could not be closed cleanly. See the output "
            u"window for which and why.".format(len(failed), len(results)),
            title=TITLE)


def main():
    try:
        run()
        trace(u"done")
    except BaseException:
        show_error(u"Close Link Datums")
        try:
            forms.alert(
                u"Close Link Datums stopped at: {0}\n\nThe output window "
                u"has the details.".format(STAGE),
                title=TITLE)
        except BaseException:
            pass


if __name__ == "__main__":
    main()
