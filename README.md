# AVH Revit tools

A pyRevit extension holding AVH's in house Revit tools.

**Schedules.** Exports Revit schedules to AVH styled Excel workbooks:
Calibri, `#195784` headers, the AVH logo, grouping taken from each
schedule's own Revit settings, and live `SUM` subtotals.

**Worksets.** Creates one workset per linked model and assigns the links
to it.

Built from the formatting worked out on the Eldisgarður room and door
schedules.

**Version 2.6.1.** Runs on IronPython. openpyxl is gone, replaced by a
small OOXML writer. See _Why the rewrite_ below. The authoritative version
number is `__version__` in `lib/avh_schedules/__init__.py`; this line is a
copy and can drift.

## Install

pyRevit is required, and its installer provides the `pyrevit` command used
here. If you have just installed pyRevit, sign out of Windows and back in
first, or the command will not be on your PATH yet.

Open Command Prompt and run:

```
pyrevit extend ui AVH "https://github.com/AVH-bjornt/AVH.extension.git" --branch=main
```

Then start Revit, or click Reload on the pyRevit tab if it is already open.

`--branch=main` is not optional. `pyrevit extend` defaults to `master`, so
without it the command fails with
`reference 'refs/remotes/origin/master' not found`.

**Or double click `Install_AVH_Schedules.bat`**, which runs the same
command, and runs `pyrevit extensions update AVH` instead on every later
run. It is the version to hand to colleagues who would rather not open a
command prompt, and it explains itself if pyRevit is missing.

Because either route installs the extension as a git clone, pyRevit's own
Update tool keeps it current from then on. Push to this repository and it
reaches everyone.

No dependencies at all. Nothing to `pip install`, nothing bundled: the
whole extension is standard library plus the Revit API.

### Showing up in the Extension Manager

pyRevit's Extension Manager lists a *catalogue*, not what is installed.
Extensions cloned directly, as this one is, get no row in it. Registering
`extensions.json` from this repository as a lookup source adds one:

```
pyrevit extensions sources add "https://raw.githubusercontent.com/AVH-bjornt/AVH.extension/main/extensions.json"
```

The installer bat does this for you, and checks first so a second run does
not list AVH twice.

It must be the `raw.githubusercontent.com` address. The ordinary
`github.com/.../blob/...` URL serves a web page rather than JSON.

This is cosmetic. It affects whether AVH appears in that window, with its
Enable and Disable buttons and its commit hash, and nothing else. The tools
work either way. Note also that `extensions.json`, plural, is not the same
thing as pyRevit's own per extension `extension.json` manifest, singular.

## Use

**AVH > Schedules > Export Schedule**

Click it and tick the schedules you want from the list. One `.xlsx` per
schedule, written to a `Schedules` folder beside the model, which opens
when done. If the model has no local path it falls back to Documents.

If the picker is unavailable for any reason it silently falls back to
exporting the schedule view you currently have open, so a UI problem can
never cost you the export. One schedule failing never stops the others:
its traceback goes to the crash log and the rest still export.

**AVH > Schedules > Export Diagnostics**

Run this if anything fails. It tests each layer independently and writes
`AVH_export_diagnostics.txt` beside the model.

It is a schedule tool only. It knows nothing about the Worksets panel.

It is not needed for day to day work. Its value is on a *new* machine or
after a Revit or pyRevit upgrade, because the failures that cost this
build five releases were all environment specific and would recur on a
different install. It is the cheapest way to tell a colleague's broken
setup apart from a broken script. If you would rather not have it on the
ribbon, delete
`AVH.tab/Schedules.panel/Diagnostics.pushbutton/` and reload; nothing
else depends on it.

**AVH > Worksets > Create Worksets from Links**

Creates one workset per linked model in the active document and puts both
the link instance and its link type on it. Names are
`Link_RVT_<name>` or `Link_IFC_<name>`. Existing worksets are left alone
rather than duplicated, and the run finishes with a report listing what
was created, skipped and assigned.

The model has to be workshared. If it is not, the tool says so and stops.

This one is Björn's, kept here so it ships with everything else. It is not
covered by the test suites the way the schedule code is, beyond the static
IronPython checks that every file in the extension has to pass.

## Why the rewrite

Four releases failed in Revit before the cause was found, and the cause was
not in this code at all.

| Version | Approach | Outcome |
| --- | --- | --- |
| 1.0.0 | CPython + `pyrevit.forms` | `PyRevitCPythonNotSupported`: IronPython only module |
| 1.0.1 | CPython + WinForms | `Invalid BinaryFormatter stream` |
| 1.0.2 | CPython + TaskDialog only | `NullReferenceException` |
| 1.0.3 | CPython, every line wrapped | Revit error, **and no crash log at all** |
| 1.0.4 | Two probes, identical apart from line one | The answer |
| 2.0.0 | IronPython + own xlsx writer | `SyntaxError`: no PEP 263 declaration |
| 2.0.1 | Encoding declarations + a static guard suite | `__revit__` not found |
| 2.0.2 | pyrevit API for host access, picker restored | Worked |
| 2.1.0 | Button icons | Worked |
| 2.2.0 | Calibri instead of Times New Roman | Worked |
| 2.3.0 | Border index fix, white section rule, unit column widths | Worked |
| 2.4.0 | Zebra striping and dashed row rules | Subtotals blank in Excel |
| 2.4.1 | `calcPr fullCalcOnLoad` so Excel computes them | Worked |
| 2.4.2 | Ungrouped schedules get a total row | Worked |
| 2.5.0 | Header and section background `#195784` | Worked |
| 2.5.1 | Icons cut from 128px to 96px | Worked |
| 2.6.0 | Worksets panel added | Worked |
| 2.6.1 | Listed in the Extension Manager via `extensions.json` | Current |

The probes settled it: a script with `#! python3` fails, the same script
without it works. **pyRevit's CPython engine fails to initialise in this
environment**, so the script never starts. That is why wrapping every line
in `except BaseException` changed nothing and no log was ever written.
pyRevit issue 3341 reports the same signature.

Every earlier fix was aimed at a plausible suspect rather than a confirmed
one. The lesson is in _Working on this_ below.

So 2.0.0 runs on IronPython, pyRevit's default engine. That forced one real
consequence: openpyxl needs Python 3.8+ and cannot run there, so the
spreadsheet layer was rewritten from scratch.

## The xlsx writer

`lib/avh_schedules/xlsx.py` writes `.xlsx` directly: a zip of OOXML parts,
standard library only. Scope is exactly what the house style needs.

- values: text, int, float, formulas
- fonts, solid fills, per edge borders (hair / thin / double), alignment
  with wrap and indent, custom number formats
- merged cells, column widths, row heights, freeze panes
- one embedded PNG, sized from the IHDR chunk, so no Pillow either
- landscape print setup with fit to width, and a print area

Strings are inline rather than a shared string table: slightly larger
files, considerably less to get wrong.

Verified by loading every generated workbook back through openpyxl and by
LibreOffice recalculation. The three level door header round trips exactly:
`L3:O3` for Measurements (mm), `L4:M4` Gatmál, `N4:O4` Rough Measurements,
Breidd / Hæð beneath.

## Unicode: three rules, all learned by breaking them

These schedules are full of Icelandic room names and m² / m³ units, and
Python 2 is unforgiving about all of it.

1. **Every source file starts with `# -*- coding: utf-8 -*-`.** Python 2
   refuses to parse a file containing non-ASCII bytes without it. Missing
   this killed v2.0.0 on its first run with `SyntaxError: Non-ASCII
   character '\xc2' in model.py`.
2. **Every non-ASCII literal carries a `u` prefix.** Without it the literal
   is bytes on Python 2, and comparing it against unicode from a Revit
   export triggers an implicit ASCII decode that fails on the first
   Icelandic name. `u"h\u00e6\u00f0"`, never `"hæð"`.
3. **Never `str()` on Revit data.** `str` is bytes on IronPython, so
   `str(u'LÁGSPENNURÝMI')` raises `UnicodeEncodeError`. Everything goes
   through `compat.to_text`. Files are opened with `io.open`, since
   `open(encoding=...)` is Python 3 only.

`test_ironpython_compat.py` enforces all three statically, because a hand
written checklist missed rules 1 and 2 in consecutive releases.

## What it decides, and how

**Grouping.** The schedule's own Revit sort and group settings are read
first: grouped by Type in Revit gives grouped by type in Excel. Failing
that, grouping is inferred from the group footer lines Revit renders into
the export. If the schedule is ungrouped, it falls back to grouping by
level, per house style. On the five Eldisgarður schedules this reproduces
the agreed layout with no configuration.

**Subtotals.** A `Count` column is summed when there is one. Otherwise
every quantity column is summed, which is what the CC01 room schedule
needed for Area and Volume together. A schedule with neither gets section
rows and no subtotal, rather than a meaningless sum of door widths. All
subtotals are real `SUM()` formulas.

Any sheet with something summable ends in a total. Grouped with several
groups gives subtotals plus a grand total; grouped with one group gives
just the subtotal, since a grand total would repeat it; ungrouped gives a
single total row. That last case matters for schedules carrying no `Level`
or `Hæð` column, which have nothing to group by and were previously
getting no total at all.

**Units.** Values arrive as text like `30 m²`. The unit moves into the
column header (`Area (m²)`) and the cell becomes a real number, one decimal
for quantities.

**Column order is never changed.** The order in the export is the user's
layout. The CC01 room schedule was once delivered with columns rearranged
and had to be redone; `test_against_real_exports.py` now asserts order
against the source.

**Rows are kept unless provably not data.** A Revit group footer reads
`%QQC001 / 980 x 2080mm / R: 9`; a real record with only its first field
filled looks identical in shape. A row is dropped only if it ends in
`: <count>` or repeats a value another column holds. An earlier version
dropped any row with a single populated first cell, which silently deleted
a room that had a name but nothing else. A stray group header is visible
and fixable; a missing room is a problem on site.

**Export path** is `ViewSchedule.Export()` with a **tab** delimiter, not
`GetTableData()`. The tab removes the decimal comma collision that corrupts
AVH's comma delimited exports, where `15,1 m²` splits across two fields and
shifts every later column. It also keeps the Revit API surface tiny and
makes the parsing testable outside Revit.

## If something fails

Three files, all written to Documents, your user folder and `%TEMP%`:

- **`AVH_export_trace.log`** one line per stage, appended as each stage
  *begins*. Absent means nothing ran; stopping partway shows where it died.
- **`AVH_export_crash.log`** full traceback plus Python, .NET and Revit
  versions.
- **`AVH_export_diagnostics.txt`** from the Diagnostics button.

## Layout

```
AVH.extension/
  extension.yaml
  assets/avh_logo.png
  lib/avh_schedules/
    compat.py   Python 2/3 text and file handling
    model.py    plain data and value parsing, no Revit, no spreadsheet code
    reader.py   Revit ViewSchedule -> ScheduleTable
    xlsx.py     minimal OOXML writer, stdlib only
    style.py    the AVH house style
    writer.py   ScheduleTable -> styled .xlsx
    crashlog.py crash logging that cannot itself fail
  AVH.tab/
    Schedules.panel/
      ExportSchedule.pushbutton/
      Diagnostics.pushbutton/
    Worksets.panel/
      Create Worksets From Links.pushbutton/
```

No `bundle.yaml`: it is optional, it is parsed before any Python runs, and
it was eliminated while hunting the engine failure. Titles come from
`__title__`.

Icons returned in 2.1.0 now that the engine problem is understood and the
extension is known to work. They are also parsed before any Python runs,
so if an unexplained startup failure ever appears, deleting the two
`icon.png` files is a cheap first thing to rule out.

The icons echo the workbook itself: a navy `#293960` header band over a
table. Export carries a green arrow, Diagnostics a muted header and an
amber magnifier so the ribbon shows which one is the everyday tool.
Checked at 96, 48, 32, 24 and 16 px against both Revit's light and dark
ribbon greys. A navy arrow was rejected because it disappears on the dark
theme.

They are **96x96**, which is pyRevit's stated maximum. They shipped at
128px until 2.5.1 and pyRevit logged a warning on every startup, since
oversized icons have to be rescaled for screen scaling at load time.

## Tests

Run from inside `AVH.extension`, outside Revit:

```
python test_ironpython_compat.py
python test_against_real_exports.py
python test_edge_cases.py
python test_script_harness.py
```

`test_ironpython_compat.py` is 118 static checks that every shipped file
can actually run on IronPython 2.7: encoding declarations, u-prefixed
literals, no f-strings or `open(encoding=)` or bare `str()`, no
`#! python3`, and a parse of every file under the **Python 2 grammar**
itself via lib2to3. It caught a real `"hæð"` byte-literal bug the moment it
was written.

`test_against_real_exports.py` runs the real Eldisgarður exports through
parser and writer, asserting row counts, grouping column, totals, formula
counts and column order against the workbooks that were hand built and
signed off: 197 interior doors, 41 exterior, 26 industrial, and the room
schedules.

`test_edge_cases.py` covers shapes the real files do not: ungrouped,
nothing summable, single column, a sparse data row that must not be
mistaken for a group line, a numeric column polluted by a typed note, an
empty schedule, tab delimited input.

`test_script_harness.py` runs the **real** ExportSchedule script against a
mocked Revit, with a fake `ViewSchedule` serving the actual CC01 room
schedule through `Export()`. Twenty one checks: the happy path, a failure
inside the read, a TaskDialog that raises, a non schedule view, and both
log files.

## Working on this

Two rules earned the hard way.

**Probe the environment before building on it.** Four failures were all
runtime environment assumptions, not logic errors: an engine that will not
start, a UI module that will not import, a serialiser disabled by default.
None were visible from reading code. Use the Diagnostics button first.

**Test the script layer, not just the library.** Every failure happened in
the thin pushbutton scripts, which had no tests for three releases while
the library underneath was thoroughly covered.

And the standing constraint: **this runs on IronPython 2.7.** No f-strings,
no `open(encoding=)`, no `str()` on Revit data, nothing needing Python 3.

## Reaching the active document

`resolve_host()` tries, in order: `pyrevit.revit`, `pyrevit.HOST_APP`, then
`__revit__` in module globals, `__main__`, `__builtin__` and `builtins`. It
returns which one worked, and on total failure reports every route it tried
rather than just saying no.

v2.0.1 checked only two of those six and found neither, so nothing
exported. Running on IronPython means the whole pyrevit API is available,
so the documented route is now tried first, and it is also what makes
`pyrevit.forms` usable for the picker.

## Known limits

Everything crossing into .NET is exercised against mocks only, never a real
Revit: `ViewSchedule.Export`, `GetSortGroupFields`, the schedule collector,
TaskDialog.

Group headers are matched against other columns' values, so a schedule
grouped on a field whose values appear nowhere else, with headers shown but
footers hidden, may leave its header rows in as data. Turning group footers
on resolves it.
