# -*- coding: utf-8 -*-
"""Probe this Revit environment layer by layer and write a report.

Runs on IronPython, like the export button, with no `#! python3` line.
The CPython engine fails to initialise here, which is what caused three
straight failures; probe buttons identical apart from that one line
confirmed it.

Keeps a CPython probe in the list anyway, so the day the engine is fixed
this button says so.

Design rules, learned from those failures:

- every probe is independently guarded, so one failure never stops the
  rest and the report always covers every layer.
- the report is written to disk BEFORE any dialog is attempted, because
  TaskDialog is itself now under suspicion and must not be able to lose
  the findings.
- TaskDialog gets its own probe, and the final dialog is best effort.
"""

__title__ = "Export\nDiagnostics"
__author__ = "AVH"
__doc__ = ("Test each layer this extension depends on (Python engine, "
           "the xlsx writer, TaskDialog, WinForms, WPF, the Revit API) "
           "and write a report showing what works here and what does not. "
           "Run this if Export Schedule fails.")

import os
import sys
import tempfile
import traceback

STAGE = "bootstrap"
SCRIPT = "ExportDiagnostics"
RESULTS = []

try:
    _u = unicode          # noqa: F821  Python 2
except NameError:
    _u = str

EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
LIB_DIR = os.path.join(EXT_DIR, "lib")
LOGO_PATH = os.path.join(EXT_DIR, "assets", "avh_logo.png")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

TEMP = tempfile.mkdtemp(prefix="avh_diag_")


def probe(name):
    """Run a check now, record PASS/FAIL and detail, never raise."""
    def wrap(func):
        try:
            detail = func()
            RESULTS.append((name, "PASS", detail or ""))
        except BaseException as exc:
            RESULTS.append((name, "FAIL", "{0}: {1}\n{2}".format(
                type(exc).__name__, exc, traceback.format_exc())))
        return func
    return wrap


def revit_uiapp():
    uiapp = globals().get("__revit__")
    if uiapp is None:
        uiapp = sys.modules["__main__"].__dict__.get("__revit__")
    return uiapp


def revit_doc():
    uiapp = revit_uiapp()
    if uiapp is None:
        raise RuntimeError("__revit__ not available")
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        raise RuntimeError("ActiveUIDocument is None")
    doc = uidoc.Document
    if doc is None:
        raise RuntimeError("ActiveUIDocument.Document is None")
    return doc


# --- 1. engine ---------------------------------------------------------

@probe("python engine")
def _python():
    import platform
    return "version={0}\nimplementation={1}\nexecutable={2}".format(
        sys.version.replace("\n", " "),
        platform.python_implementation(),
        sys.executable or "(none)")


@probe("clr / pythonnet / .NET runtime")
def _clr():
    import clr  # noqa: F401
    import System
    return "clr OK\nEnvironment.Version={0}\nFrameworkDescription={1}".format(
        System.Environment.Version,
        System.Runtime.InteropServices.RuntimeInformation
        .FrameworkDescription)


@probe("__revit__ injection")
def _revit_injected():
    in_globals = globals().get("__revit__") is not None
    in_main = sys.modules["__main__"].__dict__.get("__revit__") is not None
    uiapp = revit_uiapp()
    return ("present in module globals: {0}\npresent in __main__: {1}\n"
            "type: {2}".format(in_globals, in_main,
                               type(uiapp).__name__ if uiapp else "None"))


# --- 2. Excel stack ----------------------------------------------------

@probe("xlsx writer: import")
def _xlsx_import():
    from avh_schedules import xlsx
    return "avh_schedules.xlsx loaded from {0}".format(xlsx.__file__)


@probe("xlsx writer: write a styled file with a formula")
def _xlsx_write():
    from avh_schedules import style, xlsx
    book = xlsx.Workbook()
    sheet = book.add_sheet("probe")
    sheet.cell(1, 1, u"tex\u00ads\u00ad\u00e6", style.HEADER)
    sheet.cell(2, 1, 1.5, style.BODY_QUANTITY)
    sheet.cell(3, 1, None, style.SUBTOTAL_QUANTITY, formula="=SUM(A2:A2)")
    sheet.merge(4, 1, 4, 3)
    path = os.path.join(TEMP, "probe.xlsx")
    book.save(path)
    return "wrote {0} bytes".format(os.path.getsize(path))


@probe("xlsx writer: embed the AVH logo")
def _logo():
    from avh_schedules import style, xlsx
    book = xlsx.Workbook()
    sheet = book.add_sheet("probe")
    style.insert_logo(sheet, LOGO_PATH)
    path = os.path.join(TEMP, "probe_logo.xlsx")
    book.save(path)
    return "logo embedded, {0} bytes".format(os.path.getsize(path))


@probe("unicode handling (Icelandic room names)")
def _unicode():
    from avh_schedules.compat import to_text
    sample = u"L\u00c1GSPENNUR\u00ddMI 30 m\u00b2"
    return "to_text round trip OK: {0} chars".format(len(to_text(sample)))


@probe("avh_schedules package")
def _package():
    import avh_schedules
    from avh_schedules import (compat, model, reader,  # noqa: F401
                               style, writer, xlsx)
    return "version={0}".format(avh_schedules.__version__)


@probe("pyrevit.revit (documented host access)")
def _pyrevit_revit():
    from pyrevit import revit as pyrevit_revit
    doc = getattr(pyrevit_revit, "doc", None)
    uidoc = getattr(pyrevit_revit, "uidoc", None)
    return "doc={0}\nuidoc={1}".format(
        getattr(doc, "Title", doc), uidoc is not None)


@probe("pyrevit.HOST_APP")
def _pyrevit_hostapp():
    from pyrevit import HOST_APP
    uiapp = getattr(HOST_APP, "uiapp", None) or HOST_APP
    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    return "uiapp={0}\nuidoc={1}".format(type(uiapp).__name__,
                                         uidoc is not None)


@probe("pyrevit.forms (the multi select picker)")
def _pyrevit_forms():
    from pyrevit import forms
    return "forms imported, SelectFromList present: {0}".format(
        hasattr(forms, "SelectFromList"))


@probe("__revit__ location sweep")
def _revit_sweep():
    """v2.0.1 failed here: it only checked globals and __main__."""
    found = []
    try:
        if globals().get("__revit__") is not None:
            found.append("module globals")
    except BaseException:
        pass
    try:
        if sys.modules["__main__"].__dict__.get("__revit__") is not None:
            found.append("__main__")
    except BaseException:
        pass
    for name in ("__builtin__", "builtins"):
        try:
            if getattr(__import__(name), "__revit__", None) is not None:
                found.append(name)
        except BaseException:
            pass
    return "__revit__ found in: {0}".format(", ".join(found) or "nowhere")


# --- 3. UI stacks under suspicion --------------------------------------

@probe("Revit TaskDialog: import and construct")
def _taskdialog_construct():
    from Autodesk.Revit.UI import TaskDialog
    dialog = TaskDialog("AVH probe")
    dialog.MainInstruction = "probe"
    dialog.MainContent = "probe content"
    return "constructed (not shown)"


@probe("Revit TaskDialog: set Title property")
def _taskdialog_title():
    """Separate from construction: assigning Title was one unguarded
    call in the version that raised NullReferenceException."""
    from Autodesk.Revit.UI import TaskDialog
    dialog = TaskDialog("AVH probe")
    dialog.Title = "probe title"
    return "Title assigned OK"


@probe("Revit TaskDialog: CommonButtons enum")
def _taskdialog_buttons():
    from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
    dialog = TaskDialog("AVH probe")
    dialog.CommonButtons = TaskDialogCommonButtons.Close
    return "CommonButtons assigned OK"


@probe("WinForms: load assembly")
def _winforms_load():
    import clr
    clr.AddReference("System.Windows.Forms")
    import System.Windows.Forms as WinForms
    return "System.Windows.Forms imported, Form type = {0}".format(
        WinForms.Form)


@probe("WinForms: construct a Form")
def _winforms_form():
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import Form
    form = Form()
    form.Text = "probe"
    form.Dispose()
    return "Form constructed and disposed"


@probe("WinForms: CheckedListBox, python str items")
def _winforms_list_pystr():
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import CheckedListBox
    box = CheckedListBox()
    box.Items.Add("plain python str")
    count = box.Items.Count
    box.Dispose()
    return "added {0} item(s) as python str".format(count)


@probe("WinForms: CheckedListBox, explicit System.String items")
def _winforms_list_netstr():
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import CheckedListBox
    from System import String
    box = CheckedListBox()
    box.Items.Add(String("explicit System.String"))
    count = box.Items.Count
    box.Dispose()
    return "added {0} item(s) as System.String".format(count)


@probe("WinForms: FolderBrowserDialog construct")
def _winforms_folder():
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import FolderBrowserDialog
    dialog = FolderBrowserDialog()
    dialog.Description = "probe"
    dialog.Dispose()
    return "constructed (not shown)"


@probe("WPF: load assemblies")
def _wpf_load():
    import clr
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    return "PresentationFramework, PresentationCore, WindowsBase loaded"


@probe("WPF: construct Window with a checkbox list")
def _wpf_window():
    import clr
    clr.AddReference("PresentationFramework")
    from System.Windows import Window
    from System.Windows.Controls import CheckBox, ListBox, StackPanel
    window = Window()
    window.Title = "probe"
    panel = StackPanel()
    listbox = ListBox()
    for text in ("one", "two", "three"):
        item = CheckBox()
        item.Content = text
        listbox.Items.Add(item)
    panel.Children.Add(listbox)
    window.Content = panel
    return "Window with {0} checkbox items constructed".format(
        listbox.Items.Count)


# --- 4. Revit schedule API ---------------------------------------------

@probe("Revit: document and version")
def _revit_doc_info():
    uiapp = revit_uiapp()
    app = uiapp.Application
    doc = revit_doc()
    return "Revit {0} build {1}\ndocument={2}\npath={3}".format(
        app.VersionNumber, app.VersionBuild, doc.Title,
        doc.PathName or "(unsaved)")


@probe("Revit: active view")
def _revit_active_view():
    uiapp = revit_uiapp()
    view = uiapp.ActiveUIDocument.ActiveView
    return "active view = {0} (type {1})".format(
        getattr(view, "Name", "?"), type(view).__name__)


@probe("Revit: collect schedules")
def _revit_collect():
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSchedule
    doc = revit_doc()
    views = [v for v in FilteredElementCollector(doc).OfClass(ViewSchedule)
             if not v.IsTemplate]
    return "{0} schedules. First few:\n  {1}".format(
        len(views), "\n  ".join(v.Name for v in views[:10]))


def _first_exportable():
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSchedule
    doc = revit_doc()
    for view in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if view.IsTemplate:
            continue
        try:
            if view.Definition.IsKeySchedule:
                continue
        except Exception:
            pass
        return view
    return None


@probe("Revit: GetSortGroupFields")
def _revit_sortgroup():
    view = _first_exportable()
    if view is None:
        return "no schedules to test"
    definition = view.Definition
    parts = []
    for sort_field in definition.GetSortGroupFields():
        field = definition.GetField(sort_field.FieldId)
        parts.append("{0} header={1} footer={2}".format(
            field.GetName(), sort_field.ShowHeader, sort_field.ShowFooter))
    return "'{0}': {1}".format(view.Name,
                               "; ".join(parts) or "no sort/group fields")


@probe("Revit: ViewSchedule.Export tab delimited")
def _revit_export():
    from Autodesk.Revit.DB import ViewScheduleExportOptions
    view = _first_exportable()
    if view is None:
        return "no schedules to test"
    options = ViewScheduleExportOptions()
    options.FieldDelimiter = "\t"
    view.Export(TEMP, "probe_export.txt", options)
    path = os.path.join(TEMP, "probe_export.txt")
    from avh_schedules.compat import read_text
    head = read_text(path).splitlines()[:6]
    return u"exported '{0}'\nfirst lines:\n{1}".format(
        view.Name, u"\n".join(u"    " + line for line in head if line))


@probe("Full pipeline on the first schedule")
def _pipeline():
    from avh_schedules import reader, writer
    view = _first_exportable()
    if view is None:
        return "no schedules to test"
    table = reader.read_schedule(view)
    path = os.path.join(TEMP, "probe_pipeline.xlsx")
    writer.write_workbook(table, path, logo_path=LOGO_PATH)
    group = "ungrouped"
    if table.group_column is not None:
        column = next((c for c in table.columns
                       if c.index == table.group_column), None)
        if column is not None:
            group = column.name
    return "'{0}': {1} cols, {2} rows, grouped by {3}".format(
        view.Name, table.n_cols, len(table.rows), group)


# --- report ------------------------------------------------------------

def build_report():
    lines = ["AVH schedule export diagnostics", "=" * 72, ""]
    try:
        from avh_schedules import crashlog
        lines.append("environment:")
        lines.append(crashlog.environment())
        lines.append("")
    except Exception as exc:
        lines.append("environment: <unavailable: {0}>".format(exc))
        lines.append("")
    for name, status, detail in RESULTS:
        lines.append("[{0}] {1}".format(status, name))
        for line in (detail or "").rstrip().splitlines():
            lines.append("      " + line)
        lines.append("")
    passed = sum(1 for _n, s, _d in RESULTS if s == "PASS")
    lines.append("=" * 72)
    lines.append("{0} passed, {1} failed".format(passed,
                                                 len(RESULTS) - passed))
    return "\n".join(lines)


def write_report(text):
    """Write beside the model if possible, else Documents, else temp."""
    directories = []
    try:
        base = os.path.dirname(revit_doc().PathName or "")
        if base and os.path.isdir(base):
            directories.append(base)
    except Exception:
        pass
    directories.append(os.path.join(os.path.expanduser("~"), "Documents"))
    directories.append(os.path.expanduser("~"))
    directories.append(TEMP)

    for directory in directories:
        try:
            if not os.path.isdir(directory):
                continue
            path = os.path.join(directory, "AVH_export_diagnostics.txt")
            import io
            with io.open(path, "w", encoding="utf-8",
                         errors="replace") as handle:
                handle.write(_u(text))
            return path
        except Exception:
            continue
    return None


STAGE = "build report"
text = build_report()

try:
    print(text)
except Exception:
    pass

STAGE = "write report to disk"
written = write_report(text)

STAGE = "show summary dialog"
failures = [n for n, s, _d in RESULTS if s == "FAIL"]
summary = "\n".join("  FAIL  " + n for n in failures) or "  everything passed"
message = ("{0}\n\nFull report:\n{1}\n\nSend that file back."
           .format(summary, written or "(could not be written)"))

try:
    from Autodesk.Revit.UI import TaskDialog
    dialog = TaskDialog("AVH")
    dialog.MainInstruction = "{0} of {1} checks passed".format(
        len(RESULTS) - len(failures), len(RESULTS))
    dialog.MainContent = message
    dialog.Show()
except BaseException:
    # TaskDialog is itself under suspicion, so a failure here must not
    # lose the report that is already safely on disk
    try:
        from avh_schedules import crashlog
        crashlog.write(STAGE, SCRIPT,
                       note="report was written to {0}".format(written))
    except Exception:
        pass
    try:
        print("TaskDialog failed. Report is at: {0}".format(written))
    except Exception:
        pass

try:
    if written:
        os.startfile(os.path.dirname(written))
except Exception:
    pass
