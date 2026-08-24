# AVH Revit tools

A pyRevit extension holding AVH's in house Revit tools.

**Schedules.** Exports Revit schedules to AVH styled Excel workbooks:
Calibri, `#195784` headers, the AVH logo, grouping taken from each
schedule's own Revit settings, and live `SUM` subtotals.

**Worksets.** Creates one workset per linked model and assigns the links
to it.

**Tools.** Flip Grid Ends toggles which end bubble(s) show on selected
grids. Remove Level moves everything off a level and then offers to
delete it. Isolate Warnings isolates everything Revit has a warning
about, and clears the isolate on the next click.

**Data.** Room Data Sync copies each room's CCI location ID onto the
furniture, casework, doors and equipment inside it. Flip Status records
whether each family instance is mirrored or flipped into parameters that
can be scheduled and filtered.

Built from the formatting worked out on the Eldisgarður room and door
schedules.

**Version 2.14.1.** Runs on IronPython. openpyxl is gone, replaced by a
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

**Or double click `Install_AVH_Extensions.bat`**, which runs the same
command, and runs `pyrevit extensions update AVH` instead on every later
run. It is the version to hand to colleagues who would rather not open a
command prompt, and it explains itself if pyRevit is missing.

Because either route installs the extension as a git clone, pyRevit's own
Update tool keeps it current from then on. Push to this repository and it
reaches everyone.

No dependencies at all. Nothing to `pip install`, nothing bundled: the
whole extension is standard library plus the Revit API.

### It will not appear in the Extension Manager

That window lists a *catalogue* of extensions pyRevit knows about,
annotated with whether each is installed. It is not an inventory of what
you have. An extension cloned directly, as this one is, gets no row in it,
and that is expected. `pyrevit extensions` on the command line is what
lists what is actually installed.

Publishing an `extensions.json` and registering it with
`pyrevit extensions sources add` was tried at 2.6.1 and removed at 2.6.2.
The source registered correctly and the file was served correctly, but AVH
never appeared, in the window or in `pyrevit extensions search`. The
documentation only ever promises that an added source shows up in search
results, so the Extension Manager may not read additional sources at all.
Do not spend another afternoon on it without new evidence.

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

**AVH > Tools > Flip Grid Ends**

Flips visibility of the bubbles at the ends of selected grids. If both
bubbles were visible, one is hidden and the other is left. If one end was
visible, the visible end swaps to the other side. If neither is visible,
both are shown.

Works on whatever grids are already selected in the model. If nothing is
selected it prompts you to pick some on screen, filtered to grids only.

Adapted from the "Flip Grid Ends" tool in the pyApex pyRevit extension
(https://apex-project.github.io/pyApex). The version here drops pyApex's
fallback for pyRevit older than 4.5, which AVH does not run, and replaces
its category name match (`"Grid" in element.Category.Name`, which breaks
on any non English Revit UI) with a plain `isinstance(element, DB.Grid)`
check. It also fixes a real bug carried over from the original: the
no-op branch called `Transaction.Rollback()`, lowercase b, which is not a
method the Revit API exposes (it's `RollBack`), so a run that changed
nothing would have raised instead of rolling back cleanly.

**AVH > Tools > Remove Level (BETA)**

Confirmed working on Eldisgarður as of 2.8.2: railings and stairs moved
onto a level 3740 mm lower and were checked in section afterwards, and had
not shifted. The offset pairs for those are now proven against geometry
rather than against the tool's own arithmetic. Floors and roofs are still
only from the API docs.

Pick one or more levels to clear, pick a level to move their contents
onto, and everything that can be moved is moved while keeping its
absolute elevation. Then it offers to delete the levels it managed to
empty.

Nothing is written until a dry run has been printed to the pyRevit output
window and you have confirmed it. The dry run lists, per level, what will
move and by how much, what has no offset parameter and so would
physically shift, and what cannot be moved at all. Element ids are
clickable.

**The level parameter is found by scanning, not from a lookup table.**
The first version carried a hardcoded list of `BuiltInParameter` names.
Against a real model it found 116 dependents on a level and could move
none of them, because the only model content there was a railing, whose
level parameter was not on the list. Guessing more names would just have
moved the gap to the next element type. So it now looks for any writable
parameter whose storage type is `ElementId` and whose current value is
the level being cleared, which is general and covers shared and project
parameters too.

The *offset* still needs a table, because which offset belongs to which
level parameter is semantic and cannot be derived. Four classes result:

- a level parameter found and its offset recognised, so it moves and
  stays exactly where it is. The normal case.
- **any** constraint on it with no offset paired to it. All or nothing:
  even the constraints that *were* paired are left alone, because
  repointing some and not others splits the element across two levels.
  Held back unless you explicitly opt in, and both the report and the
  confirmation dialog name the parameter, so the pair gets added from
  evidence rather than invented.
- held to the level by a **read only** parameter, so it can never be
  freed whatever else is writable on it. Reported as stuck and left
  entirely alone. **Rooms are the one exception**, below.
- a **room**. A room's level parameter is read only in the API and in the
  interface alike, so no parameter can move one. The route a person takes
  is cut and paste; the tool takes the same route by a better road. It
  unplaces the room, places it again in a free enclosed area on the
  target level, and nudges it back to its own coordinates. Placing the
  *same* room element rather than copying it is what keeps the room
  number, which on Eldisgarður is schedule data.

  Two things make this different from everything else here. It is the
  only operation that deliberately **changes elevation**, because a room
  sits at its level. And it depends entirely on the model: the target
  level needs bounding geometry where the room lands, or there is nowhere
  to put it. So feasibility is measured, not assumed. Each room move is
  performed in full inside a transaction that is rolled back, and only
  the ones Revit accepted are offered. A failure during the real run
  rolls the whole room step back, because an unplaced room is worse than
  a room that never moved.
- bound to the level not by a parameter but by the **work plane** it sits
  on. Offered as a separate rehost step: the curve is given an equivalent
  plane at the same origin that no level owns, so it is freed without
  moving. Confirmed separately from the move, because it is a different
  operation with a different failure mode.

  **Whether a curve can be rehosted is measured, not inferred.** A sketch
  line belonging to a stair is a `CurveElement` sitting on a plane that
  dies with the level, indistinguishable from a free model line until you
  try to reassign it, at which point Revit says *"The curve belongs to a
  sketch-based element, and cannot be modified independently"*. Version
  2.9.0 offered five and failed all five. The dry run now performs every
  rehost for real inside a transaction it rolls back, and reports only
  what Revit accepted, demoting the rest to stuck with Revit's own
  message as the reason. It is the same probe-and-rollback trick the tool
  already uses on the level itself, applied to the operation.
- nothing pointing at the level at all, usually meaning it is hosted on a
  work plane rather than a level. Reported as stuck; these are what block
  the delete.
- collateral: views, viewports, anything owned by a view, element types
  and unnamed internal sub objects. Counted, not listed, and **not
  treated as blockers**. They were never on a level: they exist because
  the level does, and Revit removes them with it. Counting them as
  blockers is why the first run said 115 elements were attached to a
  level whose real content was one railing.

A level is deleted only if nothing but its own views and internals still
depends on it, and the confirmation names how many go with it.

If real elements are still on a level after everything movable has moved,
it now offers to **delete the level anyway**, one level at a time, with
the remaining elements listed by kind and count. Revit's own delete
cascades, so this needs no extra deletion step: removing the level
removes what is left on it. That is exactly why it gets its own
confirmation rather than being folded into the ordinary one, and why a
level whose dependency check raised is never offered. Anything else still
attached leaves the level in place, with the blocking elements listed.

If an element has its level changed but the matching offset then cannot
be written, the whole run is rolled back rather than committed, because
that element would otherwise sit at the wrong elevation with nothing
saying so.

It is marked BETA in the button title because **it has never been run
against a real Revit model.** Everything below the arithmetic is covered
only by mocks. Use it on a detached copy first.

Adapted from "Remove Level Safely BETA" in the pyApex pyRevit extension
(https://apex-project.github.io/pyApex), though almost nothing of the
original survives. Three things in it were wrong:

*The offset arithmetic.* It computed the element's absolute elevation and
wrote that straight into the offset parameter, without subtracting the
target level's elevation. That is correct only when the target level sits
at 0.00, which its own transaction name, `'Change level to 0'`, admits.
Any other target moved every element upward by exactly the target level's
elevation. `test_level_move.py` fails 16 checks if the subtraction is ever
dropped again.

*The unit handling.* It read elevations with `AsValueString()`, stripped
spaces and called `float()`, then wrote back with
`SetValueString(str(value))`. AVH's Revit formats with a decimal comma, so
the read raises `ValueError` and the write pushes a period decimal into a
comma locale, where it is either rejected or read as a thousands
separator. `SetValueString` returns a bool the original discarded, so a
rejected write was silent. This is the same defect class as the CSV
decimal comma problem that made the schedule export use tab delimiters.
Nothing in the rewrite formats or parses a number: elevations come from
`Level.Elevation` and offsets from `AsDouble` and `Set`, all doubles in
internal units.

### The room that produced two of the rules

Worth recording, because both rules came from one element and neither was
obvious from reading code.

Room 7555204 on Eldisgarður had a **read only base level** and a
**writable `ROOM_UPPER_LEVEL`**. Version 2.8.1 saw only the writable one,
found no offset paired with it, put it in the opt in shift group, and on
opt in repointed the room's upper limit from a level at 11740 mm to one
at 8000 mm while correcting nothing. The room ended up with its ceiling
3740 mm below its own floor, and the level was still blocked afterwards
because the base had never moved.

Two rules followed:

1. **A read only parameter pointing at the level means the element cannot
   be freed.** Moving whatever else it has is churn that changes geometry
   for no gain. Check for these first and stop.
2. **Every constraint needs its own paired offset, or none of them
   move.** Repointing all the constraints found while correcting a single
   offset is the same bug in a different shape, and a stair with base and
   top on one level hits it without any read only parameter involved.

And one wording lesson: the dialog said these elements "would move
vertically", which is true of a base constraint and a serious
understatement of an upper one. It now names the parameter and says a top
constraint can end up below the element's own base.

*The pickers.* Its option objects carried a `.state` flag, which is the
`SelectFromCheckBoxes` interface, but it called `SelectFromList`, which
returns only the ticked items and never touches `.state`. On current
pyRevit it selected nothing and exited, so it could not have been tested
as delivered. This uses the same name based `SelectFromList` call
ExportSchedule already relies on.

Also dropped: `__beta__ = True`, which hides a button unless pyRevit's
beta tools are switched on. The title carries the warning instead, so the
button is actually visible.

**AVH > Tools > Isolate Warnings**

Isolates every element Revit has a warning about, in the active view.
Click it again and the isolate clears: one button, both directions.

Shift click opens a list of the warning kinds in the model with counts,
so one kind can be isolated on its own. Most models are mostly
"highlighted elements are joined but do not intersect", and isolating all
of it buries the two warnings that actually matter.

It isolates warnings from the **whole model**, not only those in the
current view, so a plan view can isolate three hundred elements and look
almost empty. Rather than leave that looking like a broken tool, it
counts how many of them the view can show, says so in the report, and
puts a dialog up when the answer is none.

The isolate is **temporary**, the same mode as Revit's own Temporary
Hide/Isolate. Nothing touches the view's permanent visibility. The price
of one button doing both directions is that it also clears a temporary
hide somebody set up by hand.

`FailureMessage.GetAdditionalElements` is deliberately not included. For
"joined but do not intersect" both elements are failing elements anyway,
and elsewhere the additional elements are context rather than the thing
that is wrong.

**AVH > Data > Room Data Sync (BETA)**

Reads `CCIMultiLevelLocationID` from each room and writes it into
`CCISingleLevelLocationAtID` on the furniture, furniture systems,
casework, doors, windows, specialty and mechanical equipment, stairs and
railings found inside it. Both schedules and facilities management read
the result.

Pick the levels to cover, or cancel that picker to do the whole model.
Nothing is written until a dry run has been printed and confirmed.

**Blanks being filled and values being changed are counted, listed and
confirmed separately.** Filling a blank is uncontroversial. Overwriting
something a person typed is not, and burying the second inside the first
is how a sync tool quietly destroys hand corrections. Every change shows
its old and new value, and the confirmation offers "fill blanks only" as
a way to take the safe half of the work when the change list looks wrong.

Working out which room an element is in is three questions, not one, and
the report says which answer was used so a suspicious value can be
traced:

- **Doors and windows** sit in a wall between two rooms, so they are in
  neither. `ToRoom` is preferred, being the room the opening serves, and
  `FromRoom` is the fallback.

  **That fallback fires on the value, not on whether a room is there.**
  Until 2.11.1 it fell back only when there was no ToRoom at all, so a
  door whose ToRoom was a room with a blank CCI ID came away with nothing
  while a perfectly good ID sat on the other side of it. It now takes the
  first side that actually has a value, and the report says when it fell
  through and why. If both sides are blank the preferred side is still
  named, because that is the room somebody has to go and fix.
- **Everything else** is asked directly first, through the room the
  family instance reports. That only answers when the family has a room
  calculation point, which many do not, so the fallback is the element's
  own position.
- **Position needs care.** An insertion point usually sits exactly on the
  floor, which is the room's own lower boundary, and a point on a
  boundary is in no room at all. The lookup happens 100 mm above it.
  Removing that lift fails 14 checks in the harness.

Rooms exist per phase, so each element is asked about *its own* phase
rather than whichever happens to be last. When the phase cannot be
resolved the unphased properties answer instead, because the first
version gave up in that case and reported nothing to do, which looks
exactly like a model that is already correct.

**The target parameter name is not confirmed.**
`CCIMultiLevelLocationID` is proven from AVH's room schedule exports.
`CCISingleLevelLocationAtID` is not: the door schedule carries
`CCISingleLevelID`, which holds a per door tag rather than a level. So a
missing target parameter is the loudest thing in the report, and it lists
the CCI parameters those elements *do* have, rather than reporting that
nothing needed doing.

**AVH > Forma > Make Forma View**

Makes a 3D view named exactly what the model file is named, with
everything that is not model geometry switched off: annotation
categories, analytical categories, imported categories, linked models,
coordination models and lines. It is the view to export to Autodesk Forma
from.

Run it again on the same model and it refreshes the view it made last
time rather than making a second one, so it doubles as the way to put a
Forma view back to a known state after somebody has turned things on in
it. The view is opened when it is done.

The name comes from the file name with the extension removed.
Characters Revit refuses in an element name (`\ : { } [ ] | ; < > ? \` ~`)
become underscores rather than being dropped, so two models whose names
differ only in punctuation cannot collapse onto one view. A model that
has never been saved has no file name to use, and the tool says so and
stops rather than inventing one. **On a workshared local file the file
name carries the username suffix Revit adds**, so the view is named after
the local copy. Say if that should be stripped.

Two things it handles that would otherwise waste an afternoon:

- **A view template overrides all of this.** A reused view may have one,
  and a new view inherits whatever `DefaultTemplateId` the 3D view type
  carries, so even a freshly created view can arrive with a template
  already applied. The tool asks, by name, before the transaction opens,
  and stops if the answer is no. Writing settings that a template
  overrules would look like the tool silently doing nothing.
- **Coordination models are not RVT links.** Hiding the RVT Links
  category leaves a Navisworks or IFC coordination model on screen, so
  that category is named separately. A Revit that does not have it is
  reported as a note, not as a failure.

Annotation, analytical and imported categories go off through the same
three properties the Visibility/Graphics checkboxes use. Each write is
read back, and one that did not stick falls through to hiding those
categories one at a time, with the report saying which route was taken. A
silent difference between two machines is the expensive kind.

**Imports need their own fallback.** Annotation and analytical categories
can be gathered by `CategoryType`, but imports cannot: every imported
DWG, DXF or SAT is a *subcategory* of `OST_ImportObjectStyles`, one per
file. So that fallback hides the parent and then each subcategory under
it, rather than trusting the parent to carry them. A model with no
imports says so; a Revit with no import category at all, with the
property already unusable, is a warning, because at that point nothing is
hiding them.

Point clouds are a fourth switch (`ArePointCloudsHidden`) and are
deliberately left alone, because nobody has asked for it.

**AVH > Data > Flip Status**

Records whether each family instance is mirrored or flipped, into
parameters that can be scheduled and filtered. Revit knows all three
facts and will not let you schedule or filter on any of them.

Three Area parameters, on Casework, Doors, Electrical Equipment, Generic
Models, Mechanical Equipment and Windows:

| Parameter | Source |
| --- | --- |
| `ElementFlippedOrMirrored` | `FamilyInstance.Mirrored` |
| `ElementHandFlipped` | `FamilyInstance.HandFlipped` |
| `ElementFacingFlipped` | `FamilyInstance.FacingFlipped` |

`1 SF` for true, `0 SF` for false.

**Why Area and not Yes/No.** An Area parameter can be set to vary across
group instances, so two instances of the same group can hold different
values, which is exactly the case that matters: a door mirrored in one
group instance and not in another. It can also be used in formulas,
which is what makes it usable in schedules and view filters.

**The first parameter name and its values match Engipedia's add in**, on
purpose, so a schedule already built on theirs keeps working. The other
two are what that add in never recorded. It reads `Mirrored` alone and
never touches `HandFlipped` or `FacingFlipped`, which was confirmed by
reading the assembly: neither property appears anywhere in it. A facing
flipped door therefore looks correct to it.

**Groups, which 2.14.0 got wrong.** Writing an instance parameter onto
an element inside a group is a change to the group, unless that
parameter is flagged to vary across group instances. Revit refuses with
*"Changes to groups are allowed only in group edit mode"*, an error that
cannot be ignored, and the only way it offers to proceed is **Ungroup**,
which dissolves every group instance the run touched.

2.14.0 read `VariesAcrossGroups`, reported that it was off, and wrote
anyway, reasoning that the value is still right outside groups. That
reasoning walked a real model into that dialog on the first run. Three
things now stand between the tool and it:

1. When the flag is off **and elements are actually in groups**, the run
   offers to set it, naming the parameters. Setting it is its own
   transaction, committed before anything else is written. A model with
   no groups gets no dialog about groups.
2. If the offer is declined, or the flag cannot be set, elements inside
   groups are skipped and counted. Everything outside a group is still
   written.
3. An `IFailuresPreprocessor` on the write transaction rolls the whole
   run back if that failure appears anyway, so the Ungroup option is
   never presented. It is inherited alone: mixing it with another base
   class breaks the method resolution order and the handler silently
   never runs, which cost a session on Remove Level already.

**Report only otherwise.** Nothing creates or binds a parameter. A
parameter that is missing, is not an Area, or is read only is reported
with the category and the parameter named, and those elements are
skipped. The vary across groups flag on an existing binding is the one
thing the tool will change, on request, because that is the difference
between the tool working and the tool being dangerous.

**Only what changed is written.** A value already correct is left alone,
and the report counts updated and already correct separately. Rewriting
the same number onto every element marks the whole model as modified,
which on a workshared job turns a check into a sync. Running it twice in
a row must write nothing the second time, and three checks in the
harness say so.

**First thing to check in Revit**: whether `Mirrored` is already true for
a hand flipped instance. If it is, the mirrored and hand columns will
agree everywhere and one of them is redundant. That is a question about
Revit's behaviour that no test outside Revit can answer.

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
| 2.14.0 | Data panel: Flip Status | Ran into the ungroup dialog on Eldisgarður |
| 2.14.1 | Grouped elements handled instead of walked into | Current |

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

## Never ignore what a write returns

Two rules, both learned by breaking the model, and the second is the more
important one.

**Numbers must not become strings on the way into or out of Revit.**
`AsValueString` and `SetValueString` format and parse per the Revit UI
locale, and AVH's Revit uses a decimal comma, so `float()` on a read
value raises and writing `str(3500.0)` pushes a period decimal into a
comma locale where it is rejected or read as a thousands separator. Use
`AsDouble`, `Set` and `Level.Elevation`, all doubles in internal units,
and format to millimetres for display only. Same defect class as the CSV
problem that made the schedule export use a tab delimiter.

**And that includes `Commit`.** `Transaction.Commit()` returns a
`TransactionStatus`. Revit validates on commit and can roll the whole
transaction back there, returning `RolledBack` rather than raising.
Version 2.10.0 ignored it, so a room move Revit had thrown away was
reported as "1 room(s) moved" three lines above the same room being
listed as still on the level about to be deleted. The level was deleted
on the strength of that report and the room went with it.

This is the same defect the tool exists to fix. The original pyApex
script discarded the bool from `SetValueString` and so wrote values Revit
had silently rejected; this README said so, and then the tool made the
identical mistake one level up. **A rejected write that reports success
is worse than a crash.**

Two things follow, beyond checking the status.

*Revit's objections are captured, not predicted.* An
`IFailuresPreprocessor` on the room transactions swallows warnings,
records the text of any error, and rolls back, so a modal dialog in the
middle of a batch becomes a line in the report.

*A rollback never validates.* This tool tests feasibility by doing the
work in a transaction it discards, which is a good trick with a hard
limit: anything Revit checks only at commit time is invisible to it. Room
height is exactly that, so it is computed instead, in
`avh_levels.room_height_after`, with no Revit involved. **When a probe
cannot see something, arithmetic that can is worth more than a better
probe.**

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
  lib/avh_levels/
    model.py    level move arithmetic and classification, no Revit
  lib/avh_rooms/
    model.py    what the room sync decides, no Revit
  lib/avh_forma/
    model.py    view naming rules and what gets switched off, no Revit
  lib/avh_warnings/
    model.py    warning grouping and picker labels, no Revit
  lib/avh_flips/
    model.py    flip state to parameter values, no Revit
  AVH.tab/
    Schedules.panel/
      ExportSchedule.pushbutton/
      Diagnostics.pushbutton/
    Worksets.panel/
      Create Worksets From Links.pushbutton/
    Tools.panel/
      Flip Grid Ends.pushbutton/
      Remove Level.pushbutton/
      Isolate Warnings.pushbutton/
    Data.panel/
      Room Data Sync.pushbutton/
      Flip Status.pushbutton/
    Forma.panel/
      Make Forma View.pushbutton/
```

No `bundle.yaml` on any button: it is optional, it is parsed before any
Python runs, and it was eliminated while hunting the engine failure.
Titles come from `__title__`. The one that remains is
`AVH.tab/bundle.yaml`, which does nothing but order the panels on the
ribbon, and every new panel has to be added to its `layout` list.

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

Room Data Sync follows the same idea: the navy band is the room's
location code and the teal blocks below are the things in the room taking
it. Its body is **white**, like Export's and Diagnostics', and that is
not decoration. A navy filled shape on Revit's dark ribbon becomes a dark
mass on dark grey, which is the same trap that got the navy arrow
rejected. The light interior is what carries it on both themes, and the
first two attempts at this icon were redone for exactly that reason.

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
python test_level_move.py
python test_remove_level_harness.py
python test_room_sync.py
python test_room_sync_harness.py
python test_forma_view_harness.py
python test_isolate_warnings_harness.py
python test_flip_status_harness.py
```

`test_edge_cases.py` and `test_script_harness.py` need **openpyxl**
installed in the desktop Python you run them with: they load the written
workbooks back through independent code, which is the point. Nothing
about that reaches Revit, where openpyxl cannot run at all.

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

`test_level_move.py` is 58 checks on the Remove Level arithmetic. It
asserts that absolute elevation is preserved for a spread of level and
offset combinations, and it keeps a copy of the original pyApex formula
so it can assert the fixed one disagrees with it by exactly the target
elevation everywhere except at 0.00. Reintroducing the missing
subtraction fails 16 of them, which was checked by actually doing it
rather than assumed.

`test_remove_level_harness.py` runs the **real** Remove Level script
against a mocked Revit, 122 checks. The fake document is behavioural
rather than scripted: `Delete` works out a level's dependents from which
elements currently sit on it, so a move genuinely clears the level and
the delete phase sees the consequence. It covers the happy path with the
offsets checked in millimetres, cancelling at the dry run, a read only
offset being left alone and then opted into, an element that blocks its
level from deletion, and an offset write failing after the level write
has already succeeded, which must roll the whole run back.

`test_forma_view_harness.py` runs the **real** Make Forma View script
against a mocked Revit, 84 checks, plus the naming rules on their own. Its
fake view behaves like one under a view template: it refuses category
visibility changes while a template is applied, so the question the script
asks before it starts is load bearing rather than decorative. It covers
creating, refreshing an existing view rather than duplicating it, a
template declined and accepted, a new view arriving with the view type's
default template on it, a commit Revit rolls back, a category the view
will not hide, a property with no setter, a property that accepts the
value and quietly drops it, a Revit with no coordination model category,
an unsaved model, a model with no 3D view type, and a failure mid write
that has to leave nothing behind.

Its imported DWGs are subcategories of the import parent, as they are in
Revit, so the import fallback is exercised on the real shape rather than
on a flat list. It also separates **a category missing from this Revit's
`BuiltInCategory` enum** from **a category this model simply has none
of**, which the script is entitled to treat differently: no imports in
the model is the ordinary case and says so, while no import category at
all, with the property already unusable, is a warning because nothing is
then hiding them. Collapsing the two, which the fake did at first, makes
every model without a DWG look like a failure.

Every behavioural rule in it is mutation tested. Ignoring the commit
status fails 2 checks, skipping the read back after setting a category
group 2, dropping the per category fallback 2, never asking about the
view template 9, skipping `CanCategoryBeHidden` 1, treating a view
template as a reusable view 1, dropping the read back after
`SetCategoryHidden` 3, and dropping the name sanitising 1. On the import
side: dropping the group 6, sending imports through the CategoryType loop
that cannot gather them 6, skipping the subcategories 1, skipping the
parent category 1, calling a model with no imports a failure 2, and
staying quiet about a missing import category 2.

The naming checks earned their place immediately: the first version used
`os.path.basename`, which splits on a backslash only when it is running on
Windows, so every Revit path came back whole off Windows and had its
backslashes turned into underscores. Fourteen checks failed and the rule
is now split by hand, which is also the only way it can be tested at all.

`test_isolate_warnings_harness.py` runs the **real** Isolate Warnings
script against a mocked Revit, 57 checks. Its fake view is behavioural:
isolating sets the temporary mode, so the toggle genuinely toggles and
the second half of a one button on/off tool can be tested at all. It
covers the plain click, the same button clearing the isolate, shift click
through both routes pyRevit has offered for it, a cancelled picker, a
broken picker, a schedule or sheet that cannot be isolated, a model with
no warnings, warning ids that no longer resolve, a warning whose API
calls raise, an isolate where nothing is in the active view, a commit
Revit rolls back, and a view that refuses both the isolate and the clear.

Every rule is mutation tested. Ignoring the commit status fails 2 checks,
never checking whether the view is already isolated 5, skipping
`CanUseTemporaryVisibilityModes` 2, missing the shift click 8, reading it
only from `EXEC_PARAMS` 2, ignoring which kinds were picked 2, dropping
the stale id filter 4, never counting what the view can show 3, dropping
the dedup inside a warning kind 2, and letting two long descriptions
truncate onto the same picker label 3. That last one matters more than it
sounds: two collided labels means picking one kind silently isolates the
other.

`test_flip_status_harness.py` runs the **real** Flip Status script
against a mocked Revit, 78 checks. Its fake parameters keep their values
across a run, so the test that matters most is possible at all: running
twice must write nothing the second time. It also covers a parameter
missing, of the wrong type, read only, not varying across groups, an
older API with no `GetDataType`, a `Set` that returns false, a `Set` that
raises the way a workshared element does, a commit Revit rolls back, a
family document, a category absent from this Revit, and a flip property
that will not answer.

Every rule is mutation tested. Writing regardless of the current value
fails 4 checks, comparing the doubles with `!=` instead of a tolerance 1,
skipping the Area type check 2, ignoring read only 2, ignoring what `Set`
returns 2, ignoring the commit status 2, treating an unreadable flip
property as flipped 1, and running on family documents 2. Cutting it back
to `Mirrored` alone, which is what Engipedia's add in does, does not fail
the suite so much as crash it, which is loud enough.

**The group scenarios are there because the first version shipped
without them.** The fake now models Revit's rule: writing an instance
parameter onto a grouped element whose parameter does not vary across
group instances posts a failure, and a transaction that has failures and
no preprocessor is recorded as having reached the Ungroup dialog.
`UNGROUP_DIALOG` must be False at the end of every scenario, and there is
a check at the end of the file that says so.

Restoring 2.14.0's behaviour, writing grouped elements anyway, fails 4
checks. Never attaching the failure guard fails 3, the guard returning
`Continue` instead of `ProceedWithRollBack` fails 3, never detecting
group membership fails 12, setting the flag inside the write transaction
rather than its own fails 7, and asking about groups in a model that has
none fails 1.

**This is the fifth time a mock here has been wrong, and the first time
by omission.** The other four invented state that did not exist and made
working code look broken. This one modelled no groups at all, so nothing
in it could ever refuse a write for the reason Revit does, and a tool
that walks into an unignorable error looked fully covered. A mock that
cannot fail the way production fails is not a test of that failure.

One scenario in it is a reconstruction of the first real run: a single
railing buried in twenty pieces of view infrastructure. Before the
rework that shape reported 116 unmovable elements and 115 blockers and
deleted nothing. It now moves the railing and deletes the level, and
narrowing the collateral test back to views only fails five checks.

That fake has now been wrong four times, and all four were the same
shape: **state that outlived the transaction that changed it.** It
snapshotted the document once and restored to that snapshot on every
rollback, so a later probe reverted an already committed move. It
defaulted a read only level id onto every element rather than only the
ones asked for, pinning everything to its level. It never cleared its
pending-creations list on commit, so the next rollback deleted a sketch
plane that had already been committed. And plan circuits were not
elements, so they sat outside the snapshot entirely and the room
feasibility probe left every one of them marked occupied, which made the
real run report that the target level had nowhere to put anything.

Each time a batch of unrelated checks failed together and every one of
them pointed at the script. Revit rolls back one transaction, not the
document's history. **A mock that is wrong in the safe direction hides
bugs; one wrong in this direction invents them, and costs a debugging
session aimed at code that was fine. When several unrelated checks fail
at once, suspect the harness before the code.**

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

That goes double for **Remove Level**, which has never been run in Revit
at all. Its arithmetic is proven and its wiring is covered by a harness,
but every Revit call in it is unverified: `Document.Delete` and the
rollback trick used to enumerate a level's dependents, `Level.Elevation`,
`Parameter.AsDouble` and `Parameter.Set`, and whether a `Parameter` read
during the dry run is still the right one to write to afterwards, which
is why the plan stores parameter names and re-fetches rather than holding
the objects. Run it on a detached copy before running it on anything that
matters.

Its offset pair table is now confirmed for railings and stairs against a
real model, where the reported before and after offsets round tripped to
the same absolute elevation, as well as by use for walls, family
instances, structural framing and rooms. The entries for floors and roofs
are still **only from the API docs**. If one of them is wrong the element moves vertically instead of
staying put, which is exactly why an unrecognised pair is a shift held
behind an opt in rather than a silent move.

Group headers are matched against other columns' values, so a schedule
grouped on a field whose values appear nowhere else, with headers shown but
footers hidden, may leave its header rows in as data. Turning group footers
on resolves it.
