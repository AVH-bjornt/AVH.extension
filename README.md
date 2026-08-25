# AVH Revit tools

A pyRevit extension holding AVH's in house Revit tools: schedule export
to AVH styled Excel, workset housekeeping, and a set of modelling and
checking tools.

No dependencies. Nothing to `pip install`, nothing bundled: the whole
extension is the Python standard library plus the Revit API. It runs on
pyRevit's IronPython engine.

**Version 2.18.2.** The authoritative number is `__version__` in
`lib/avh_schedules/__init__.py`; this line is a copy and can drift.

## Install

pyRevit is required first, from the official installer.

**If you have just installed pyRevit, sign out of Windows and back in**,
or reboot, before going further. The installer adds `pyrevit` to your
PATH and an already open session does not see it. The symptom is
"The pyrevit command was not found" seconds after installing pyRevit,
and it is the most common thing to go wrong here.

Then either double click **`Install_AVH_Extensions.bat`**, which does the
whole thing and explains itself if pyRevit is missing, or open Command
Prompt and run:

```
pyrevit extend ui AVH "https://github.com/AVH-bjornt/AVH.extension.git" --branch=main
```

`--branch=main` is not optional. Without it the command fails with
`reference 'refs/remotes/origin/master' not found`.

Then start Revit, or click Reload on the pyRevit tab if it is already
open.

### Updates

The extension installs as a git clone, so pyRevit's own Update tool
brings in new versions. Nobody needs a new file after their first
install.

### It will not appear in the Extension Manager

That window lists a catalogue of extensions pyRevit knows about, not an
inventory of what you have installed. An extension cloned directly, as
this one is, gets no row in it. That is expected.
`pyrevit extensions` on the command line lists what is actually
installed.

## The commands

### Schedules

**Export Schedule.** Tick the schedules you want and get one AVH styled
`.xlsx` per schedule, written to a `Schedules` folder beside the model,
which opens when it finishes. Calibri, `#195784` headers, the AVH logo,
grouping taken from each schedule's own Revit sort and group settings,
quantities as real numbers with the unit in the header, and subtotals as
live `SUM` formulas. One schedule failing never stops the others.

**Export Diagnostics.** Run this if the export fails. It tests each
layer independently and writes `AVH_export_diagnostics.txt` beside the
model. Its value is on a new machine, or after a Revit or pyRevit
upgrade. Not needed day to day.

### Worksets

**Create Worksets from Links.** One workset per linked model, named
`Link_RVT_<name>` or `Link_IFC_<name>`, with both the link instance and
its link type assigned to it. Existing worksets are reused rather than
duplicated. The model has to be workshared.

**Datums to Workset.** Pick a workset and every grid and level moves
into it. `Shared Views, Levels, Grids` is offered first and marked as
the usual one, but the list is whatever the model has. Anything already
there is left alone, and anything checked out by somebody else is
skipped and named rather than attempted. Views are not included: Revit
does not allow a view's workset to be changed.

### Tools

**Flip Grid Ends.** Toggles which end bubbles show on the selected
grids. Both visible becomes one, one visible swaps ends, none visible
becomes both. Works on the current selection, or prompts you to pick.

**Remove Level (BETA).** Moves everything off one or more levels onto a
target level, keeping absolute elevations, then offers to delete the
levels it emptied. Nothing is written until a dry run has been printed
and confirmed, listing what will move and by how much, what would
physically shift, and what cannot be moved at all.

**Isolate Warnings.** Isolates every element Revit has a warning about
in the active view. Click again and the isolate clears. Shift click to
pick which kind of warning to isolate, since most models are mostly
"joined but do not intersect".

**Selection > Zoom to Selection.** Zooms the active view to whatever is
selected, as one box with a margin. Stays in the view you are in and
changes nothing in the model.

### Data

**Room Data Sync (BETA).** Copies each room's `CCIMultiLevelLocationID`
onto the furniture, casework, doors, windows and equipment inside it.
Pick the levels to cover, or cancel that picker for the whole model.
Nothing is written until a dry run has been confirmed, and blanks being
filled are listed separately from values being changed.

**Doors > Flip Status.** Records whether each family instance is
mirrored, hand flipped or facing flipped into three Area parameters, so
all three can be scheduled and filtered. Reports anything the parameters
cannot take, and offers to switch on Vary Across Group Instances where
that is what is blocking it.

**Doors > Door Room Check.** Makes a plan of one level with an arrow at
every door pointing into the room its CCI ID comes from, room numbers on
both sides, and the problem doors in red. Rerun to refresh it.

### Forma

**Make Forma View.** Creates or refreshes a 3D view named after the
model file, with annotation, analytical and imported categories, linked
models, coordination models and lines switched off. The view to export
to Autodesk Forma from.

## Version history

| Version | Change | Outcome |
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
| 2.6.1 | Extension Manager catalogue via `extensions.json` | Never appeared |
| 2.6.2 | Catalogue removed, installer renamed | Worked |
| 2.7.0 | Tools panel: Flip Grid Ends added | Worked |
| 2.8.0 | Remove Level, rewritten from pyApex with the offset maths fixed | Ran, classified nothing |
| 2.8.1 | Level parameters found by scanning; view infrastructure no longer counted as a blocker | Moved a room's upper limit below its base |
| 2.8.2 | Read only constraints block; every constraint needs its own paired offset; top constraint pairs added | Ran clean, geometry confirmed |
| 2.9.0 | Rehost work plane elements; offer to delete a level that still has content | Force delete worked; rehost offered 5 and failed 5 |
| 2.9.1 | Rehost feasibility measured in a throwaway transaction instead of inferred | Worked |
| 2.10.0 | Rooms moved by unplace and re-place, keeping their number | Reported a room moved that Revit had rolled back |
| 2.10.1 | Commit status checked everywhere; room height predicted; Revit's errors captured | Worked |
| 2.11.0 | Data panel: Room Data Sync | Worked |
| 2.11.1 | Door fallback keyed on the value rather than the room | Worked |
| 2.12.0 | Forma panel: Make Forma View | Worked, first time |
| 2.12.1 | Imported categories switched off too | Shipped, imports untested in Revit |
| 2.13.0 | Tools panel: Isolate Warnings | Shipped, untested in Revit |
| 2.14.0 | Data panel: Flip Status | Ran into the ungroup dialog on a live project |
| 2.14.1 | Grouped elements handled instead of walked into | Shipped |
| 2.15.0 | Data panel: Door Room Check | pyRevit refused three scripts at startup |
| 2.15.1 | Pushbutton scripts made ASCII; the dead guard actually removed | Worked |
| 2.15.2 | Door Room Check: the phase is asked for, not assumed | Worked |
| 2.16.0 | Flip Status and Door Room Check moved into a Doors pulldown | Worked |
| 2.17.0 | Zoom to Selection, on its own panel | Panel was the wrong home |
| 2.17.1 | Moved into a Selection pulldown on Tools | Shipped |
| 2.18.0 | Worksets panel: Datums to Workset | Views refused, as suspected |
| 2.18.1 | Views dropped: grids and levels only | Worked |
| 2.18.2 | README cut back to install, commands and versions | Current |

## Notes for whoever maintains this

`NOTES.md` holds the reasoning: why the extension runs on IronPython,
the unicode rules every file has to follow, what each test suite
protects, and the failures that produced those rules. None of it is
needed to use the tools.

Run the test suites from inside the extension folder, outside Revit,
with a desktop Python 3. `test_ironpython_compat.py` is the one to run
before shipping anything.
