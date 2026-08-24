# -*- coding: utf-8 -*-
"""Export the active Revit schedule to an AVH styled Excel workbook.

Runs on IronPython, pyRevit's default engine. There is deliberately no
`#! python3` line: the CPython engine fails to initialise in this
environment, so the script never even starts and the failure surfaces as
a Revit dialog with no Python output at all. Confirmed by probe buttons
that were identical apart from that one line.

That is also why openpyxl is gone. It needs Python 3.8+, so the workbook
is written by avh_schedules.xlsx instead, a small OOXML writer that runs
on IronPython 2.7.

No UI framework either: pyrevit.forms cannot load on CPython, WinForms
failed with 'Invalid BinaryFormatter stream'. The only dialog is
Autodesk.Revit.UI.TaskDialog, and even that is treated as optional.

Everything below sits inside one outer harness that writes a full
traceback to AVH_export_crash.log on any failure, including a failure
during imports, and records how far execution got. Three failures in a
row have been diagnosed only from a Revit dialog with its detail pane cut
off; that is not a workable feedback loop.
"""

__title__ = "Export\nSchedule"
__author__ = "AVH"
__doc__ = ("Export the schedule view you currently have open to an AVH "
           "styled Excel workbook, saved in a Schedules folder next to "
           "the model.")

import os
import sys
import traceback

SCRIPT = "ExportSchedule"
_crashlog = None

# --- breadcrumb trace --------------------------------------------------
#
# Written with nothing but os, immediately, appending one line per stage.
# v1.0.3 wrapped every line and still produced a Revit level error with no
# crash log at all, which leaves two possibilities: the script never ran,
# or it died in a way Python could not catch. A crash log written in an
# except block cannot distinguish those. An append-as-you-go trace can:
# if the file does not exist, nothing ran; if it stops at a stage, that
# stage is where it died, even if the process was torn down without
# unwinding.

_TRACE_PATH = None


def _trace_targets():
    out = []
    try:
        home = os.path.expanduser("~")
        out.append(os.path.join(home, "Documents"))
        out.append(home)
    except Exception:
        pass
    for key in ("TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            out.append(value)
    return out


def _trace_init():
    global _TRACE_PATH
    for directory in _trace_targets():
        try:
            if os.path.isdir(directory):
                _TRACE_PATH = os.path.join(directory, "AVH_export_trace.log")
                import io
                with io.open(_TRACE_PATH, "w", encoding="utf-8",
                             errors="replace") as handle:
                    handle.write(u"AVH {0} trace\n".format(SCRIPT))
                return _TRACE_PATH
        except Exception:
            continue
    return None


def trace(stage):
    """Record a stage. Also updates STAGE for the crash logger."""
    global STAGE
    STAGE = stage
    if _TRACE_PATH:
        try:
            import io
            with io.open(_TRACE_PATH, "a", encoding="utf-8",
                         errors="replace") as handle:
                handle.write(u"  {0}\n".format(stage))
        except Exception:
            pass
    try:
        print("[AVH] " + stage)
    except Exception:
        pass


STAGE = "bootstrap"
_trace_init()
trace("script entered, stdlib imported")


def _fallback_log(text):
    """Last resort if even crashlog could not be imported."""
    for base in (os.path.join(os.path.expanduser("~"), "Documents"),
                 os.path.expanduser("~"), os.environ.get("TEMP", "")):
        try:
            if base and os.path.isdir(base):
                import io
                with io.open(os.path.join(base, "AVH_export_crash.log"),
                             "a", encoding="utf-8",
                             errors="replace") as handle:
                    handle.write(u"\n" + u"=" * 72 + u"\n")
                    handle.write(u"{0} (fallback logger)\nstage: {1}\n\n"
                                 u"{2}\n".format(SCRIPT, STAGE, text))
                return
        except Exception:
            continue


def _tell(title, instruction, content=""):
    """Best effort TaskDialog. Never raises: it is not load bearing."""
    try:
        from Autodesk.Revit.UI import TaskDialog
        dialog = TaskDialog("AVH")
        dialog.MainInstruction = instruction
        if content:
            dialog.MainContent = content
        dialog.Show()
        return True
    except BaseException:
        try:
            print("[{0}] {1}\n{2}".format(title, instruction, content))
        except Exception:
            pass
        return False


def resolve_host():
    """Find the active document. Returns (doc, uidoc, description).

    v2.0.1 only looked for `__revit__` in module globals and in __main__,
    and found neither, so nothing exported. Now that this runs on
    IronPython the whole pyrevit API is available, which is the documented
    route, so that is tried first and the raw object hunt is the fallback.

    Every source is reported back so a failure names what was tried
    instead of just saying no.
    """
    tried = []

    # 1. the documented pyrevit API. Unavailable on CPython, which is why
    #    it was not used before, but this script is IronPython now.
    try:
        from pyrevit import revit as pyrevit_revit
        doc = getattr(pyrevit_revit, "doc", None)
        uidoc = getattr(pyrevit_revit, "uidoc", None)
        if doc is not None:
            return doc, uidoc, "pyrevit.revit"
        tried.append("pyrevit.revit (imported, doc was None)")
    except BaseException as exc:
        tried.append("pyrevit.revit ({0})".format(type(exc).__name__))

    # 2. pyrevit.HOST_APP wraps the UIApplication
    try:
        from pyrevit import HOST_APP
        uiapp = getattr(HOST_APP, "uiapp", None) or HOST_APP
        uidoc = getattr(uiapp, "ActiveUIDocument", None)
        doc = getattr(uidoc, "Document", None) if uidoc else None
        if doc is not None:
            return doc, uidoc, "pyrevit.HOST_APP"
        tried.append("pyrevit.HOST_APP (no document)")
    except BaseException as exc:
        tried.append("pyrevit.HOST_APP ({0})".format(type(exc).__name__))

    # 3. the __revit__ object, wherever pyRevit happens to have put it
    candidates = []
    try:
        candidates.append(("globals", globals().get("__revit__")))
    except BaseException:
        pass
    try:
        candidates.append(("__main__",
                           sys.modules["__main__"].__dict__.get("__revit__")))
    except BaseException:
        pass
    for module_name in ("__builtin__", "builtins"):
        try:
            module = __import__(module_name)
            candidates.append((module_name, getattr(module, "__revit__", None)))
        except BaseException:
            pass

    for where, uiapp in candidates:
        if uiapp is None:
            tried.append("__revit__ in {0} (absent)".format(where))
            continue
        try:
            uidoc = getattr(uiapp, "ActiveUIDocument", None)
            doc = getattr(uidoc, "Document", None) if uidoc else None
            if doc is not None:
                return doc, uidoc, "__revit__ in " + where
            tried.append("__revit__ in {0} (no document)".format(where))
        except BaseException as exc:
            tried.append("__revit__ in {0} ({1})".format(
                where, type(exc).__name__))

    return None, None, "; ".join(tried)


def exportable_schedules(doc):
    """Real schedules only: no templates, key or revision schedules."""
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSchedule
    out = []
    for view in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if view.IsTemplate:
                continue
            if view.Definition.IsKeySchedule:
                continue
            if getattr(view, "IsTitleblockRevisionSchedule", False):
                continue
        except BaseException:
            continue
        out.append(view)
    return sorted(out, key=lambda v: v.Name)


def choose_schedules(doc, uidoc):
    """Which schedules to export. Returns (list, how).

    Tries pyrevit.forms for a multi select picker, which is what Bjorn
    originally asked for and which works on IronPython. If anything about
    it fails, falls back to the active schedule view, so a UI problem can
    never cost the export. That fallback is the whole of what v1.0.4 to
    v2.0.1 could do.
    """
    from Autodesk.Revit.DB import ViewSchedule

    try:
        from pyrevit import forms
        candidates = exportable_schedules(doc)
        if not candidates:
            _tell("AVH", "This model has no exportable schedules.")
            return [], "none found"

        by_name = {}
        for view in candidates:
            by_name.setdefault(view.Name, view)

        active = getattr(uidoc, "ActiveView", None) if uidoc else None
        active_name = (active.Name if isinstance(active, ViewSchedule)
                       else None)

        chosen_names = forms.SelectFromList.show(
            sorted(by_name.keys()),
            title="Export schedules to AVH Excel",
            button_name="Export",
            multiselect=True,
        )
        if not chosen_names:
            return [], "cancelled"
        if isinstance(chosen_names, str):
            chosen_names = [chosen_names]
        picked = [by_name[n] for n in chosen_names if n in by_name]
        if picked:
            return picked, "pyrevit.forms picker"
        if active_name:
            return [by_name[active_name]], "picker returned nothing"
        return [], "picker returned nothing"
    except BaseException as exc:
        trace("picker unavailable ({0}), using the active view".format(
            type(exc).__name__))

    active = getattr(uidoc, "ActiveView", None) if uidoc else None
    if active is None:
        _tell("AVH", "No active view.",
              "Open the schedule you want to export, then click again.")
        return [], "no active view"
    if not isinstance(active, ViewSchedule):
        _tell("AVH", "Open a schedule first.",
              "The schedule picker is unavailable, so this exports the "
              "schedule view you are currently in.\nCurrently active: "
              "{0}".format(getattr(active, "Name", "unknown")))
        return [], "active view is not a schedule"
    return [active], "active view"


def run():
    trace("resolve extension paths")
    ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    lib_dir = os.path.join(ext_dir, "lib")
    logo_path = os.path.join(ext_dir, "assets", "avh_logo.png")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    trace("import Revit API")
    from Autodesk.Revit.DB import ViewSchedule

    trace("import avh_schedules")
    from avh_schedules import reader, writer

    trace("resolve Revit host")
    doc, uidoc, source = resolve_host()
    if doc is None:
        _tell("AVH", "Could not reach Revit.",
              "None of the known ways to reach the active document worked.\n"
              "Tried: {0}\n\nRun Export Diagnostics and send the "
              "report.".format(source))
        return
    trace("Revit host resolved via {0}".format(source))

    trace("choose schedules")
    schedules, how = choose_schedules(doc, uidoc)
    if not schedules:
        return
    trace("{0} schedule(s) chosen via {1}".format(len(schedules), how))

    trace("resolve output folder")
    target_dir = output_folder(doc)

    written, failed = [], []
    for view in schedules:
        name = view.Name
        try:
            trace("read '{0}' via ViewSchedule.Export".format(name))
            table = reader.read_schedule(view)

            trace("write workbook for '{0}'".format(name))
            path = unique_path(target_dir, safe_filename(name))
            writer.write_workbook(table, path, logo_path=logo_path)
            written.append((name, path, table))
        except BaseException as exc:
            # One bad schedule must not lose the others. Its traceback goes
            # to the crash log; the run carries on.
            trace("FAILED on '{0}': {1}".format(name, type(exc).__name__))
            if _crashlog is not None:
                _crashlog.write("export '{0}'".format(name), SCRIPT)
            else:
                _fallback_log(traceback.format_exc())
            failed.append((name, exc))

    trace("report: {0} written, {1} failed".format(len(written), len(failed)))
    report(written, failed, target_dir)

    trace("finished")

    if written:
        try:
            os.startfile(target_dir)
        except Exception:
            pass


def output_folder(doc):
    """A Schedules folder beside the model, else beside Documents."""
    base = ""
    try:
        candidate = os.path.dirname(doc.PathName or "")
        if candidate and os.path.isdir(candidate):
            base = candidate
    except Exception:
        base = ""
    if not base:
        base = os.path.join(os.path.expanduser("~"), "Documents")
    target = os.path.join(base, "Schedules")
    if not os.path.isdir(target):
        os.makedirs(target)
    return target


def safe_filename(name):
    illegal = '<>:"/\\|?*'
    safe = u"".join(u"_" if ch in illegal else ch for ch in name)
    return safe.strip().rstrip(u".") or u"schedule"


def unique_path(directory, base_name):
    path = os.path.join(directory, base_name + ".xlsx")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(directory,
                            u"{0} ({1}).xlsx".format(base_name, counter))
        counter += 1
    return path


def report(written, failed, target_dir):
    lines = []
    if written:
        lines.append(u"Exported {0} schedule(s) to:".format(len(written)))
        lines.append(target_dir)
        lines.append(u"")
        for name, _path, table in written:
            group = u"ungrouped"
            if table.group_column is not None:
                column = None
                for c in table.columns:
                    if c.index == table.group_column:
                        column = c
                        break
                if column is not None:
                    group = u"by " + column.name
            lines.append(u"  {0}  ({1} rows, {2}, {3} groups)".format(
                name, len(table.rows), group, len(table.groups())))
    if failed:
        if lines:
            lines.append(u"")
        lines.append(u"Failed {0}:".format(len(failed)))
        for name, exc in failed:
            lines.append(u"  {0}: {1}".format(name, exc))
        lines.append(u"")
        lines.append(u"Tracebacks are in AVH_export_crash.log.")
    if not lines:
        lines.append(u"Nothing exported.")

    message = u"\n".join(lines)
    try:
        print(message)
    except Exception:
        pass
    heading = (u"Exported {0} schedule(s)".format(len(written)) if written
               else u"Export failed")
    _tell("AVH", heading, message)


try:
    trace("import crashlog")
    _ext = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    _lib = os.path.join(_ext, "lib")
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    from avh_schedules import crashlog as _crashlog
except BaseException:
    _crashlog = None

try:
    run()
except BaseException:
    written = None
    if _crashlog is not None:
        written = _crashlog.write(STAGE, SCRIPT)
    if written is None:
        _fallback_log(traceback.format_exc())
        written = "your Documents folder"
    trace("FAILED at the stage above")
    _tell("AVH", "Export failed.",
          "Stage reached: {0}\n\nTraceback written to:\n{1}\n"
          "Stage trace written to:\n{2}\n\nSend both files back."
          .format(STAGE, written, _TRACE_PATH or "(could not write)"))
