# -*- coding: utf-8 -*-
"""
Crash logging that cannot itself fail.

Three runs in Revit have produced errors visible only in a Revit dialog
whose detail pane was cut off, so the traceback never reached anyone who
could act on it. This module exists so the next failure writes itself to
disk regardless of what Revit chooses to display.

Deliberate constraints:

- stdlib only, no Revit and no .NET, so it can be imported before
  anything that might fail.
- every function swallows its own exceptions. A logger that raises while
  reporting a crash turns one problem into two.
- callers keep a stage string and pass it in, so even a traceback that
  points somewhere useless still tells us how far execution got.
"""

import io
import os
import sys
import traceback

LOG_NAME = "AVH_export_crash.log"

try:
    _u = unicode          # noqa: F821  Python 2
except NameError:
    _u = str


def candidate_dirs(extra=None):
    """Places to try writing, best first."""
    out = []
    if extra:
        out.append(extra)
    home = os.path.expanduser("~")
    out.append(os.path.join(home, "Documents"))
    out.append(home)
    for key in ("TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            out.append(value)
    return [d for d in out if d]


def log_path(extra=None, name=LOG_NAME):
    for directory in candidate_dirs(extra):
        try:
            if os.path.isdir(directory):
                return os.path.join(directory, name)
        except Exception:
            continue
    return None


def environment():
    """Everything about this process worth knowing, cheaply and safely."""
    lines = []

    def add(label, getter):
        try:
            lines.append("  {0}: {1}".format(label, getter()))
        except Exception as exc:
            lines.append("  {0}: <unavailable: {1}>".format(label, exc))

    add("python", lambda: sys.version.replace("\n", " "))
    add("executable", lambda: sys.executable or "(none)")
    add("cwd", os.getcwd)

    def dotnet():
        import System
        return "{0} / {1}".format(
            System.Environment.Version,
            System.Runtime.InteropServices.RuntimeInformation
            .FrameworkDescription)
    add("dotnet", dotnet)

    def revit():
        uiapp = sys.modules["__main__"].__dict__.get("__revit__")
        if uiapp is None:
            return "(no __revit__ in __main__)"
        app = uiapp.Application
        return "{0} build {1}".format(app.VersionNumber, app.VersionBuild)
    add("revit", revit)

    add("sys.path[0:6]", lambda: "\n    " + "\n    ".join(sys.path[:6]))
    return "\n".join(lines)


def write(stage, script_name, extra_dir=None, note=None):
    """Append a crash record. Returns the path written, or None.

    Call this from an `except BaseException` block. It captures whatever
    exception is currently being handled.
    """
    try:
        path = log_path(extra_dir)
        if not path:
            return None

        try:
            stamp = __import__("datetime").datetime.now().isoformat(
                sep=" ", timespec="seconds")
        except Exception:
            stamp = "(no timestamp)"

        body = [
            "",
            "=" * 72,
            "{0}  {1}".format(stamp, script_name),
            "reached stage: {0}".format(stage),
        ]
        if note:
            body.append("note: {0}".format(note))
        body.append("")
        body.append("environment:")
        body.append(environment())
        body.append("")
        body.append("traceback:")
        try:
            body.append(traceback.format_exc())
        except Exception:
            body.append("  <traceback unavailable>")

        text = "\n".join(body)

        with io.open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(_u(text))

        try:
            print(text)
        except Exception:
            pass

        return path
    except Exception:
        # a failing logger must never mask the original failure
        return None
