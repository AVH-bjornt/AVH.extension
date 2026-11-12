# AVH Revit tools

A pyRevit extension holding AVH's in house Revit tools: schedule export
to AVH styled Excel, workset housekeeping, and a set of modelling and
checking tools.

No dependencies. Nothing to `pip install`, nothing bundled: the whole
extension is the Python standard library plus the Revit API. It runs on
pyRevit's IronPython engine.

**Version 2.39.0.** The authoritative number is `__version__` in
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
live `SUM` formulas. One schedule failing never stops the others; its
error is shown in the pyRevit output window.

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

**Close Link Datums.** Tick the links you want and `Shared Views,
Levels, Grids` is closed in each, so their grids and levels stop showing
in this model. `Shared Levels and Grids` is recognised too. Each link is
reloaded with that one workset closed; every other workset in it stays
open or closed as it was. Links where it is already closed, links
without it, unloaded, nested and non workshared links are not offered,
and the output window says which and why. After each reload the link's
worksets are read back from Revit, so a reload that left the workset
open is reported as exactly that. Reloading takes as long as Manage
Links does, so large links take a while. If anything fails, the output
window says which link, which step and why.

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

**Zoom to Selection.** Zooms the active view to whatever is
selected, as one box with a margin. Stays in the view you are in and
changes nothing in the model.

**Place at Coordinate.** Type a northing, an easting and an elevation in
shared survey coordinates and it puts an `AVH_Coordinate_Marker` there,
hosted on the level below the point, with what you typed written into the
marker's own parameters. Both decimal separators are accepted. After
placing, it reads the marker's real shared coordinates back and compares
them with what you asked for: a disagreement over a millimetre rolls the
whole thing back rather than leaving a marker in the wrong place.

The marker ships with the extension as `assets/AVH_Coordinate_Marker.rfa`
and is loaded into the model on first use. It is a Generic Model, not
hosted and not workplane based, origin at the intersection of the two
reference planes, with text parameters `AVH_Easting`, `AVH_Northing`,
`AVH_Elevation` and `AVH_Point_Name`. A parameter the family does not
have is reported rather than fatal, so a renamed one shows up in the
output instead of stopping the run.

**Sync Scope Boxes.** Pick a linked model and its scope boxes are
compared with the copies already in this model. The new and changed ones
are offered in a tick list, nothing ticked to start with, and only what
you tick is written: new boxes are copied in, changed ones replaced. A
changed box says how far it moved. Ticking a replacement asks once more
before anything is written; ticking only new boxes does not. Copies are
named after their link, for example `ELG_ARK - DA11`, and the tool only
ever touches boxes carrying that link's prefix, so scope boxes you made
yourself are never matched or replaced.

Revit cannot resize a scope box, so a changed one is **replaced**: the
new box is copied in, every view, grid, level and reference plane
assigned to the old one is moved across to it, then the old one is
deleted and the new one renamed. The replacement has a new element id.
Boxes that have disappeared from the link are reported and left in
place. Copies go on `Shared Views, Levels, Grids` when the model has it.

**Point Cloud to Toposolid.** Pick a linked point cloud, a point
spacing in metres, a toposolid type and a level. The cloud is read in
tiles, every sample is averaged into a square grid at that spacing, and
one point per occupied cell goes into a new toposolid, outlined by the
rectangle through the outermost points. The count, extent and height
range are shown before anything is built. Revit 2024 or newer.

Each run asks what is in the scan. **Average all points** is for a cloud
already cleaned down to ground. **Ground filter** is for one that still
has people, vehicles or plants: each square keeps its third lowest point
instead of the average, and anything standing more than 0.5 m above the
ground around it is dropped and filled in by Revit. You give the size of
the largest object to remove, and the prompt carries a guide (2 m people
and shrubs, 4 m cars, 10 m trucks and containers).

The filter cannot tell an object from real ground of the same size. A
lava ridge, boulder or crest narrower than the size you give is
flattened along with the trucks, and anything wider, such as a building,
stays in. Use the smallest size that covers the objects. Steps and
steady slopes are kept. A proper classifier (CloudCompare's CSF plugin,
or the surveyor's software) does this better when it is available.

Revit does not expose the ground and vegetation classes a survey
carries, which is why the tool has to work this out from heights alone.
Where the outline's corners have no ground under them, Revit fills in
the height.

Two things the Revit API documentation leaves open are measured on every
run instead of assumed: which coordinate system the points come back in,
and whether Revit reads a toposolid point's height as absolute or
relative to the level. If either is off by more than half a metre,
nothing is kept. Afterwards the output window compares Revit's own
vertices with the cloud points: how many it kept, and the worst and
average height difference.

**Pick a level low enough.** A toposolid has a flat bottom at its level
less the type's thickness, and Revit refuses one whose ground goes below
that as "too thin for its given type". The confirmation warns when that
will happen and names the highest level that works; if Revit refuses
anyway, the tool says so in plain words instead of Revit's error dialog.

**Gönguhlutfall stiga.** Checks the rise and going of every stair in
the model and nothing else: riser (uppstig), going (framstig), 2h + b,
slope, nosing, and the same steps in every flight and on every floor of
a stairwell. The rules depend on the stair's use, held in the text
parameter `AVH_Stair_Use`:

| Code | Checked against |
| --- | --- |
| `almennur` | Byggingarreglugerð 6.4.9, 1st paragraph: stairs for general use indoors |
| `annar` | 6.4.9, 2nd paragraph: other indoor stairs, by slope band a to e |
| `uti` | 6.4.10: outdoor steps |
| `To_Machinery` | ÍST EN ISO 14122:2016. The pitch decides the kind of access (part 1, clause 3): a stair (part 3, clause 5) from 20° to 45°, a step ladder (part 3, clause 6) up to 75°, a fixed ladder (part 4, 5.2.2.2) above that; 20° or less is a ramp |

The codes from Stair Check 2.33 still work: `ibud`, `fjolbyli_lyfta`,
`fjolbyli`, `fyrirtaeki` and `almenningur` read as `almennur`, and
`uti_adal` and `uti_svalir` as `uti`. If the model has no such parameter
the tool offers to add it, and stairs without a value can be set from a
list before the check runs.

The report is one summary table, a row per stair with link, level, use,
riser, going, pitch, result and the rules that need attention, failing
stairs first; then only the stairs with problems, one line per problem
with its source. Results are **pass**, **FAIL**, **check** where the
text recommends rather than requires, and **not checked** where a value
could not be read or no use is set. Changes nothing except the use
parameter.

The ISO 14122 standards are licensed to AVH for one workstation and are
not part of this extension; the code cites clause numbers and figures
only.

**Align Views.** Lines up views on different sheets. Select a
viewport on a sheet (or pick a placed view from a list if nothing is
selected), then tick the views to line up with it. Each ticked viewport
is moved on its sheet so that the top left corner of its view's crop
region lands exactly where the chosen view's does. The crop region is
used rather than the viewport box, because tags and dimensions reaching
past the crop make the box bigger. Views split into several crop regions
use the top left of all of them together. Pinned viewports and views
with the crop region off are skipped and listed. After moving, every
corner is worked out again and compared; if any is more than 0.1 mm out,
nothing is moved. Needs Revit 2023 or newer.

**Rename Views.** Renames views and sheets together. Select them in the
Project Browser (or tick them in a list if nothing is selected). A window
shows each name split into a prefix and a suffix shared by all of them,
and a field of each one's own between. The prefix and suffix start as
whatever the chosen names already have in common, so changing the prefix
changes every name while each keeps its own field; the fields can be
edited one by one, or numbered from 1, 01, 101 or A. Each new name is
shown as you type, in red with the reason if Revit would refuse it: a
character Revit does not allow, two chosen views landing on one name, or
a name another view of the same type already has. Sheets are renamed by
name; their numbers are left alone. Names can be swapped among the
chosen. If Revit refuses any rename, nothing is renamed.

**Batch Sheets.** Makes a number of sheets at once. A window asks for
the title block (the one most used in the model is offered first, or
none), how many sheets, and each sheet's number and name, built like
Rename Views as a prefix, a field of each sheet's own and a suffix. The
number fields count up from a start value (1, 01, 101 or A), and the
start is offered where the model's own numbering leaves off: after A-105
it offers A- and 106. Every field can still be edited per row. Each new
number and name is shown as you type, in red with the reason if Revit
would refuse it: a character Revit does not allow, a number another
sheet already has, or two new sheets landing on one number. If Revit
refuses any sheet, none are made.

**Diagnose Invisibility.** Select one or more elements, pick a view, and
it prints why each one is not visible there: workset, phase filter,
design option, category or subcategory hidden, explicitly hidden, a view
filter, the crop region, a 3D section box, the view range, a view
template, an element belonging to another view, or the view's
discipline. Reports only, changes nothing.

It also reports a **visible** element whose geometry draws on a hidden
category, which is how a toilet can be visible, selectable and not on
screen. That check is AVH's addition.

This one started as pyRevit's tool rather than AVH's, and AVH maintains
its copy from here on rather than tracking theirs. See `THIRD_PARTY.md`.

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
model file, with annotation and analytical categories, imported DWGs and
SATs, linked models, coordination models and lines switched off. The
view to export to Autodesk Forma from.

**Imports in Families is deliberately left on.** Sanitary ware,
ironmongery and most manufacturer content is built from geometry
imported into a family, and it draws on that category. Switching it off
takes those families out of the view and out of the Forma export with
nothing on screen to say so. Imported files are found through the
elements that place them instead, so a site DWG goes off and a toilet
does not.

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
| 2.18.2 | README cut back to install, commands and versions | Worked |
| 2.19.0 | Zoom to Selection moved out of the pulldown onto Tools | Worked |
| 2.20.0 | Tools panel: Hide in Template | Shipped, untested in Revit |
| 2.21.0 | Tools panel: Hide Across Views | Every view came back blocked on shift click |
| 2.21.1 | A guard that cannot run no longer refuses everything; real Category.Id carried through | Awaiting a rerun |
| 2.21.2 | An unreachable category now names the route that works instead of a dead end | Worked |
| 2.22.0 | Shift click on a section, elevation or callout switches off its marker category | Swap did not fire, silently |
| 2.22.1 | Every way the marker swap can fail now names itself in the dialog | Named it: no ViewType |
| 2.22.2 | A section marker is a Viewer, not a ViewSection; followed to its view | VIEWER_VIEW_NAME does not exist |
| 2.22.3 | The marker's own parameters are read instead of a parameter name being guessed | Ran, resolved to the wrong view |
| 2.22.4 | View templates excluded, disagreeing parameters refuse, the resolved view is named | Worked, Sections confirmed |
| 2.22.5 | A near miss beside a successful resolution goes to the output window, not the dialog | Shipped |
| 2.23.0 | Diagnose Invisibility copied in from pyRevit; extension licensed GPL v3 | AttributeError on first click |
| 2.23.1 | A locale helper that exists only on pyRevit's develop branch is stood in for | Worked |
| 2.24.0 | Imports in Families no longer hidden; imported files found through their elements | Worked |
| 2.25.0 | Diagnose Invisibility reports a visible element drawing on a hidden category | Worked |
| 2.25.1 | The copied tool is AVH's to maintain; no re-sync with pyRevit is promised | Worked |
| 2.26.0 | Tools panel: Place at Coordinate | Landed 3 m out in Z and 58 km out in plan |
| 2.26.1 | A snapped placement is moved back, and a mismatch prints the numbers | Doubled the position instead |
| 2.26.2 | The document is regenerated before a new element's position is read | Worked |
| 2.27.0 | Asked and reported northing first, to match Revit; the model's own origin is shown | Confirmed: lands on the survey point |
| 2.28.0 | Tools panel: Sync Scope Boxes | One yes or no for everything; all or nothing |
| 2.29.0 | Sync Scope Boxes: tick which boxes to copy or replace | Not yet run in Revit |
| 2.30.0 | Tools panel: Point Cloud to Toposolid | Revit refused 1 000 000 points per read |
| 2.30.1 | Point cloud read asks for 999 999 points at most | Read worked; too thin on the first level, 65 mm under the top on a lower one |
| 2.31.0 | Toposolid kept within 0.5 m and compared vertex by vertex; too thin warned and caught | Not yet run in Revit |
| 2.32.0 | Ground filter option for clouds with people, vehicles and plants | Not yet run in Revit |
| 2.33.0 | Tools panel: Stair Check against Byggingarreglugerð 112/2012 | Found no stairs: every stair read as being edited |
| 2.33.1 | Stair Check reads IsInEditMode as the method it is, and says what there is when it finds no stairs | Worked; report hard to read |
| 2.33.2 | Stair Check report: summary table, then only the problems; separately drawn railings found | Not yet run in Revit |
| 2.34.0 | Stair Check becomes Gönguhlutfall stiga: rise and going only; To_Machinery checked against ÍST EN ISO 14122 | Not yet run in Revit |
| 2.35.0 | Tools panel: Align Views, by the top left of the crop region | Worked |
| 2.36.0 | Worksets panel: Close Link Datums | Not yet run in Revit |
| 2.37.0 | Tools panel: Rename Views, a shared prefix and suffix and a field of each one's own | Worked (one crash after committing, not reproduced) |
| 2.38.0 | No files written on users' machines: Export Diagnostics removed, no trace or crash logs, temp files cleaned up | Not yet run in Revit |
| 2.39.0 | Tools panel: Batch Sheets, numbered and named like Rename Views | Current |

## Licence

GPL v3, in `LICENSE`. The extension carries that licence because parts
of it come from pyRevit and pyApex, which are both GPL v3.
`THIRD_PARTY.md` says which parts and where from. This places no limit
on AVH using the tools, internally or on paid work.

## Notes for whoever maintains this

`NOTES.md` holds the reasoning: why the extension runs on IronPython,
the unicode rules every file has to follow, what each test suite
protects, and the failures that produced those rules. None of it is
needed to use the tools.

Run the test suites from inside the extension folder, outside Revit,
with a desktop Python 3. `test_ironpython_compat.py` is the one to run
before shipping anything.
