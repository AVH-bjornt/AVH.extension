# Build notes

Why this extension is built the way it is: the failures that shaped it,
the rules that came out of them, and what each test suite is actually
protecting.

**None of this is needed to use the tools.** The README covers
installation and what each button does. This file is for whoever has to
change the code, including a future version of whoever wrote it.

It was the README until 2.18.2, when the README was cut back to
installation, the commands and the version history.

---

A pyRevit extension holding AVH's in house Revit tools.

**Schedules.** Exports Revit schedules to AVH styled Excel workbooks:
Calibri, `#195784` headers, the AVH logo, grouping taken from each
schedule's own Revit settings, and live `SUM` subtotals.

**Worksets.** Creates one workset per linked model and assigns the links
to it. Datums to Workset puts every grid and level into one chosen
workset.

**Tools.** Flip Grid Ends toggles which end bubble(s) show on selected
grids. Remove Level moves everything off a level and then offers to
delete it. Isolate Warnings isolates everything Revit has a warning
about, and clears the isolate on the next click. Under the **Selection**
pulldown, Zoom to Selection zooms the active view to whatever is
selected.

**Data.** Room Data Sync copies each room's CCI location ID onto the
furniture, casework, doors and equipment inside it. Under the **Doors**
pulldown, Flip Status records whether each family instance is mirrored or
flipped into parameters that can be scheduled and filtered, and Door Room
Check draws a plan showing which room each door actually takes its ID
from.

Built from the formatting worked out on the a live project room and door
schedules.

**Version 2.18.1.** Runs on IronPython. openpyxl is gone, replaced by a
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
its traceback goes to the pyRevit output window and the rest still
export.

Export Diagnostics, a button that probed each layer and wrote
`AVH_export_diagnostics.txt`, was removed in 2.38.0 (see "No files on
users' machines" below). It is in the git history if a new machine ever
needs it again.

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

**AVH > Worksets > Datums to Workset**

Pick a workset and it moves every grid and every level into it. AVH's is
`Shared Views, Levels, Grids`, which the picker offers first and marks as
the usual one, but the list is whatever the model actually has, so a
project that names it differently still works.

**Views are not touched, and that is settled.** 2.18.0 moved graphical
views as well, on the reasoning that the API might allow what the
interface does not. On a live project every one of them refused. Rather than
leave a category in that reports a failure on every run, views came out
entirely at 2.18.1, which also makes the name literally right: a datum is
a grid or a level. If a way to move views ever turns up, it belongs in
its own tool with its own report, not as a permanently failing third of
this one.

**Elements somebody else has checked out are never attempted.**
`WorksharingUtils.GetCheckoutStatus` is asked first, they are skipped and
named. Attempting them is how a run dies half way through a model.

Already in the target workset means left alone, counted separately.
Rewriting the same value onto every grid marks the whole model as
modified, which on a workshared job turns a tidy up into a sync.

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

Confirmed working on a live project at 2.8.2: railings and stairs moved
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
  number, which on a live project is schedule data.

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

Room 7555204 on a live project had a **read only base level** and a
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

**AVH > Data > Doors > Flip Status**

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

**AVH > Data > Doors > Door Room Check**

Makes a plan of one level showing which room each door actually takes its
CCI ID from. Pick a level, and every door gets an arrow pointing into its
`ToRoom`, with the room numbers on both sides.

Green means Room Data Sync got the ID from the ToRoom, the normal case.
Amber and red mean it did not, and the arrow colour says why: ToRoom
blank so it fell through to FromRoom, no ToRoom at all, both sides the
same room, no room on either side, or rooms on both sides with no ID
anywhere.

**Which side is which is invisible in the model**, which is why a wrong
value looks like a bug in the sync when it is usually a door facing the
wrong way or a room boundary that is missing. `ToRoom` is the room on the
side the door **faces**, so the arrow is `FacingOrientation`. Flip a
door's facing and the two rooms swap, which is exactly the mistake this
view finds.

**The preference rule is not restated here.** It comes from
`avh_rooms.model.choose_source`, the same function Room Data Sync uses,
so the drawing cannot disagree with the sync about which room wins.

Nothing permanent is created. The arrows and text are view specific
detail curves and text notes that live only in this view, so deleting the
view deletes the marks. Running it again on the same level wipes what it
drew last time and redraws.

Sizes are millimetres on paper multiplied by the view scale, so the marks
stay the same size on the sheet at 1:50 or 1:200. Drawing a fixed model
length instead is what makes an annotation tool useless on the second
project.

**Phase, which 2.15.0 got wrong.** Rooms exist per phase, and asking the
wrong one gives no rooms at all rather than an error. 2.15.0 used the
document's last phase without asking. On a live project that is Phase 2, the
rooms are not in it, and the result was a plan reading "no room" at every
single door: a confident drawing of nothing.

The phase is now **asked, every run**, with the number of placed rooms in
each phase on the label, because the phase to pick is whichever one the
rooms are in and nobody should have to know that in advance. The view is
then put on that phase, so the drawing cannot disagree with its own
labels. If every door still comes back with no room and another phase
does hold rooms, the report names it.

**The arrows that did not draw.** At 2.15.0 the text notes appeared and
the arrows did not. A text note is an annotation category; a detail line
lives in the model `Lines` category. A view template on the new view, or
a hidden `Lines` category, produces exactly that. Both are now corrected
**and reported**, and the arrow count is in the report, so the next run
says which it was instead of it taking another screenshot. That part is a
probe as much as a fix, and it is labelled as one in the script.

**Ownership is the view name**, `AVH Door Rooms - <level> - <phase>`. A
view of any other name is never found, so it is never cleared and never
drawn into. Name your own plan exactly that and it will be refreshed,
which is the one case worth knowing about.

**AVH > Tools > Selection > Zoom to Selection**

Zooms the active view to whatever is selected, as one box around all of
it, with a margin. It stays in the view you are in: nothing opens another
view, nothing switches, and Revit's own "no good view" dialog never
appears.

**No transaction anywhere.** This changes what you are looking at, not
the model, so there is nothing to roll back and it is safe on a
workshared model with everything checked out by somebody else. A check in
the harness fails if a transaction is ever opened.

**The margin** is the larger of 15 percent of the selection's biggest
dimension and 500 mm. Without a floor under it, zooming to a door handle
fills the screen with a 20 mm object and you cannot tell where in the
building you are. Below about 3.3 m the floor is what applies, which is
most things anyone selects.

**An element the view does not draw**, because it is on another level,
hidden, or outside the crop, has no bounding box in that view. Its
position in the model is used instead and the report says how many needed
that, since zooming to nothing looks like a broken button.

## Import Schedule: the round trip (2.40.0)

Export is a report and does not say which element a row is. So every
workbook now carries a hidden column of element ids (right of the table,
so it travels with its row through a sort) and a hidden `AVH_Roundtrip`
sheet: schedule and model, one `COL` line per column (kind, parameter id,
display unit) and one `ROW` line per element holding every cell as
exported. Layout and rules are in `lib/avh_schedules/roundtrip.py`.

**A change is a cell that differs from its own snapshot, not from the
model.** The display text Revit exports is rounded, so comparing with the
live model would flag every rounded value. The price: a change made in
Revit after the export is invisible to the comparison. The applier catches
that for text and whole numbers (conflict, skipped, both values shown) and
cannot for numbers with a unit. Said in the README rather than hidden.

**Where the ids come from.** Revit's export has none and a schedule
cannot be asked. The Element Id field is added to a *duplicate* schedule
inside a `TransactionGroup` that is always rolled back, never to the one
the person is looking at: editing the original would take a worksharing
checkout on it and could leave it changed. If anything about this fails,
`read_schedule` falls back to the plain export and records why in
`roundtrip_note`, so a failure here can never cost the export. A test
checks the rollback happens on every path, including a failed export.

**First real run (Revit 2025): no Element Id field.** A window schedule
reported `this schedule offers no Element Id field`. The diagnostic showed
1289 schedulable fields, `ID_PARAM` = -1002100, and nothing with that id,
so Revit 2025 does not expose Element Id as a schedule field through the
API and the duplicate-schedule route cannot work there. It is kept for
Revit versions that do offer it. The route that runs on 2025 is
`lib/avh_schedules/matching.py`: collect the elements the schedule shows
(`FilteredElementCollector(doc, scheduleViewId)`), read each column's value
from them (doubles converted to the display unit with
`ConvertFromInternalUnits`, never through a string), and give a row an id
only when EXACTLY ONE element fits. Ambiguous, unmatched and contested
rows get no id and are ignored by the import; the Export report says how
many of the rows can be imported again. Text columns must be equal and
number columns agree to the rounding the column shows; a row nothing fits
is retried on its text columns alone. `test_matching.py` (20 checks,
mutation tested) covers it, `test_roundtrip.py` covers the Revit side
against a mock.

**UNVERIFIED for matching:** that the collector returns exactly the
elements the schedule shows, that Level style (ElementId storage) columns
equal `AsValueString()`, and that grouped or filtered schedules behave.
A schedule with no unique column (no Mark, nothing distinguishing) will
match few rows, by design.

**Element ID parameter (2.41.0).** The matching route cannot separate
rows that are identical in every column, which a window schedule without a
Mark is (24 of 27 ambiguous on the first real run). The fix chosen was the
one AVH already used for doors in Dynamo: stamp each element's id into an
`Element ID` parameter. `stamper.py` plans and writes it (never creates the
parameter; skips groups, other users' elements, read only, an id too big
for Integer; writes only what differs). The export finds the parameter by
NAME among the schedulable fields (`stamp.is_stamp_name`, normalised, so
Element ID / ElementID / AVH Element ID all count), adds it to the
temporary duplicate, and verifies every id in `reader._verify_stamped`: it
must be an element the schedule shows and appear on exactly one row, then
rows left without one are matched by value against the unclaimed
elements. A copied element keeps its source's stamp, so duplicates lose it
for both rows by design. Tests: `test_stamping.py` (33 checks) and section
8 of `test_roundtrip.py`; the stale copy, wrong element, blank stamp and
value fallback are each mutation tested.

**UNVERIFIED:** that GetSchedulableFields lists the project parameter on
the schedule's category, `ParameterBindings` iteration, `OfCategoryId`.

**Not written, with a reason each:** type parameters (the user chose skip
and report), calculated/count/room columns, group members (Revit's
unignorable Ungroup dialog, as Flip Status found), elements owned by
another user, read only parameters, conflicts, a cleared number cell (a
blank must not become a zero), a unit that is unknown.

**Numbers never pass through a string.** A cell is a real number from the
file, converted with `UnitUtils.ConvertToInternalUnits` from the unit
recorded at export and set as a double.

**UNVERIFIED IN REVIT. Never run against a real model.** The calls that
carry the risk, to check first: (1) that `GetSchedulableFields` offers
`ID_PARAM`; (2) that `Parameter.Id` equals `ScheduleField.ParameterId`
(a by-name fallback exists); (3) `ScheduleField.GetSpecTypeId` and
`FormatOptions.GetUnitTypeId`, Revit 2021+; (4) `Element.GroupId`;
(5) that exported columns equal the visible fields one to one (checked at
run time, and refused if not). The mock cannot answer any of these, and
`test_roundtrip.py`'s mock was written from the same assumptions as the
code. Run on a copy.

Tests: `python test_roundtrip.py` (78 checks). It edits real workbooks
with openpyxl, which saves the way Excel does (shared strings), so the
reader meets the real shape. Mutation tested: ignoring the commit status,
ignoring what `Set` returns, writing type parameters, writing group
members, skipping the conflict check, rewriting an equal number, ignoring
duplicate ids, treating every cell as changed, not rolling the temporary
group back, and exporting the original instead of the duplicate each fail
checks. The Import button script is covered by
`test_import_schedule_harness.py` (22 checks, mocked pyrevit and Revit):
confirm and decline, cancelled picker, unedited workbook, type-only edit,
non-AVH and corrupt files, another model, rolled-back commit, conflict.
Mutation tested: skipping the confirmation, the model prompt, the dry run
report and the no-changes exit each fail checks.

No icon yet: `ImportSchedule.pushbutton` has none, so it shows pyRevit's
default. Make one the way the others were (96px, light interior).

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
| 2.14.0 | Data panel: Flip Status | Ran into the ungroup dialog on a live project |
| 2.14.1 | Grouped elements handled instead of walked into | Shipped |
| 2.15.0 | Data panel: Door Room Check | pyRevit refused three scripts at startup |
| 2.15.1 | Pushbutton scripts made ASCII; the dead guard actually removed | Worked |
| 2.15.2 | Door Room Check: the phase is asked for, not assumed | Worked |
| 2.16.0 | Flip Status and Door Room Check moved into a Doors pulldown | Worked |
| 2.17.0 | Zoom to Selection, on its own panel | Panel was the wrong home |
| 2.17.1 | Moved into a Selection pulldown on Tools | Shipped |
| 2.18.0 | Worksets panel: Datums to Workset | Views refused, as suspected |
| 2.18.1 | Views dropped: grids and levels only | Current |

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

## Unicode: the rules, all learned by breaking them

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

### A fifth: never `json.dumps` on IronPython

Learned at 2.44.1, from the Clash Detector's model list never arriving:

```
File "json\encoder.py", line 47, in py_encode_basestring_ascii
UnicodeDecodeError: 'unknown' codec can't decode byte 0xf6
```

IronPython 2.7's `json` encoder checks whether a `str` holds bytes above
0x7f and, if so, decodes it as UTF-8. On CPython 2 that is a byte
string. On IronPython `str` and `unicode` are one type, so every
non-ASCII name gets "decoded" again and the first "ö" ends it. CPython
3, where the tests run, has no such step, so no test run can see it.

`avh_clash.model.to_json` writes the messages instead, escaping every
character outside printable ASCII as `\uXXXX`. The Clash Detector
harness reads `lib/avh_clash` and fails if `json.dumps` appears there
again, which is the only check that can catch it outside Revit.
`json.loads` is believed safe (text that is already unicode is never
decoded), and the window reports a message it cannot read rather than
dropping it.

### A fourth, which contradicts the first three: pushbutton scripts are ASCII

Learned at 2.15.1, from pyRevit refusing three buttons at startup:

```
ERROR [pyrevit.extensions.genericcomps] Error while parsing file:
...\Make Forma View.pushbutton\script.py
Error type: UnicodeDecodeError
```

**pyRevit parses every bundle's `script.py` itself, at startup**, to pull
out the title and the tooltip, and that parser cannot cope with non-ASCII
bytes in the file. The `# -*- coding: utf-8 -*-` declaration governs the
Python interpreter, not pyRevit's own reader, so a file that runs
perfectly well still fails to load as a button. Three scripts shipped
with `Björn` and `a live project` in their module docstrings and all three
were rejected.

What was observed is narrower than what is now enforced. All three
failures had their non-ASCII inside the **module docstring**, and
`ExportSchedule.pushbutton`, whose only non-ASCII sits at line 235 inside
a function docstring, has never complained. The rule is nonetheless the
whole file, because "somewhere above the imports" is not something anyone
can apply by eye at review time, and nothing in a pushbutton script needs
an accent. Icelandic text belongs in the library modules, which pyRevit
never parses and which rules 1 to 3 still cover.

`test_ironpython_compat.py` enforces this too, checked by putting
`a live project` back into a script and watching it fail.

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
level, per house style. On the five a live project schedules this reproduces
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

The failure dialog names the stage it reached, and the full traceback is
in the pyRevit output window. Nothing is written to disk.

## No files on users' machines

Since 2.38.0 no button writes a log, trace or report file. The extension
is shared with colleagues, and files appearing in their Documents and
`%TEMP%` folders were not wanted. Until then Export Schedule and Close
Link Datums kept a stage trace and a crash log in Documents, the
Diagnostics button wrote a report, and `lib/avh_schedules/crashlog.py`
did the writing. All of that is gone; errors go to the output window.

Two files Revit itself needs are still made, and now cleaned up: the
schedule export `ViewSchedule.Export` writes (an `avh_sched_*` folder in
`%TEMP%`, removed after reading, even when the export fails), and Stair
Ratio's temporary shared parameter file (removed once the user's own is
restored). The Export Schedule, Close Link Datums and Stair Ratio
harnesses each check that nothing is left in the home folder or TEMP.

The trade-off: a run torn down without unwinding, which the append-as-
you-go trace was built to catch, now leaves nothing behind. If that kind
of failure comes back, the Revit journal
(`%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit 20xx\Journals`) and the
Windows Application event log (".NET Runtime" and "Application Error")
still record it, without the extension writing anything.

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
  lib/avh_doorcheck/
    model.py    door states, arrow geometry and view naming, no Revit
  lib/avh_visibility/
    model.py    which templates and views a hide can reach, no Revit
  lib/avh_selection/
    model.py    bounding box arithmetic and margins, no Revit
  lib/avh_worksets/
    model.py    what moves, picker labels and the tally, no Revit
    links.py    which link workset is the datum one, what stays open, no Revit
  lib/avh_renumber/
    model.py    running numbers, order and what changes, no Revit
    window.py   the Renumber window, WPF
  AVH.tab/
    Coordination.panel/
      Links.stack/
        Create Worksets From Links.pushbutton/
        Datums to Workset.pushbutton/
        Close Link Datums.pushbutton/
      Sync Scope Boxes.pushbutton/
      Make Forma View.pushbutton/
    Views.panel/
      Sheets.stack/
        Batch Sheets.pushbutton/
        Rename Views.pushbutton/
        Batch Dependents.pushbutton/
      Align.pulldown/          a big button of its own
        Align Views.pushbutton/
        Align Titles.pushbutton/
      Visibility.stack/
        Hide.pulldown/
          Hide Across Views.pushbutton/
          Hide in Template.pushbutton/
        Flip Grid Ends.pushbutton/
        Zoom To Selection.pushbutton/
    Model.panel/
      Place at Coordinate.pushbutton/
      Point Cloud to Toposolid.pushbutton/
      Remove Level.pushbutton/
    Check.panel/
      Clash Detector.pushbutton/
      Diagnose Invisibility.pushbutton/
      Checks.stack/
        Isolate Warnings.pushbutton/
        Stair Ratio.pushbutton/
        Door Room Check.pushbutton/
    Data.panel/
      ExportSchedule.pushbutton/
      ImportSchedule.pushbutton/
      Parameters.stack/
        StampElementIds.pushbutton/
        Room Data Sync.pushbutton/
        Flip Status.pushbutton/
      Renumber.pushbutton/
```

Regrouped at 2.42.0 by what the user is doing: set up the project
(Coordination), work on views and sheets (Views), change the model in
bulk (Model), check it (Check), or move data in and out (Data). Order on
the ribbon comes from the `layout:` list in each `bundle.yaml`, at tab,
panel, stack and pulldown level; a folder missing from a `layout:` list
is appended after the listed ones, so add new buttons to the list.

A `.stack` holds at most three items, shown small and on top of one
another, so every button in a stack needs a legible 16 px icon. A
`.pulldown` shares one ribbon slot: the folder name is the label and
`icon.png` beside the buttons is its icon. A pulldown can sit inside a
stack, as `Hide.pulldown` does. **Nesting a button one level
deeper broke how every script found `lib`**, which they all did by
walking up a fixed number of directories from `__file__`. They now search
upward for the folder that contains `lib`, which does not care how deep
the button sits, and rule 6 in `test_ironpython_compat.py` enforces it.
The harnesses cannot: each puts `lib` on `sys.path` itself before running
the script, so the script's own resolution is never exercised.

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

Import Schedule is Export's table turned round: the same green arrow, but
on the left and pointing into the table, and the highlighted row is green
for the edited cells being written back. Stamp Element IDs is the table
with an amber rubber stamp pressing on its lower right, cut out from the
table by a transparent gap so the two do not merge at 16 px. Both were
drawn in code (no image library on this machine) and checked at 96, 32
and 16 px on Revit's light and dark ribbon greys. They sit in or beside
a stack, where they show at 16 px.

Update AVH is a green download arrow into a navy tray with a white
body, the same green as Export's and Import's arrows. A thin white halo
round the arrow keeps it apart from the tray where they overlap, and
from the dark ribbon grey. Checked at 96, 48, 32, 24 and 16 px on both
greys beside Import Schedule's icon.

After moving buttons between panels or into a stack, restart Revit
rather than reloading pyRevit. Revit cannot remove a ribbon item, and a
stack is added in one call that fails if any name in it is still on the
ribbon: at 2.42.0 a reload logged "Stack 'Parameters' skipped: The name
already exists:StampElementIds".

They are **96x96**, which is pyRevit's stated maximum. They shipped at
128px until 2.5.1 and pyRevit logged a warning on every startup, since
oversized icons have to be rescaled for screen scaling at load time.

## Tests

Run from inside `AVH.extension`, outside Revit:

```
python test_ironpython_compat.py
python test_update_harness.py
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
python test_door_room_check_harness.py
python test_zoom_selection_harness.py
python test_datums_workset_harness.py
python test_close_link_datums_harness.py
python test_roundtrip.py
python test_matching.py
python test_stamping.py
python test_import_schedule_harness.py
python test_hide_in_template_harness.py
python test_hide_across_views_harness.py
python test_clash_model.py
python test_clash_detector_harness.py
python test_openings_model.py
python test_pipe_openings_harness.py
```

`test_edge_cases.py` and `test_script_harness.py` need **openpyxl**
installed in the desktop Python you run them with: they load the written
workbooks back through independent code, which is the point. Nothing
about that reaches Revit, where openpyxl cannot run at all.

`test_ironpython_compat.py` is 529 static checks that every shipped file
can actually run on IronPython 2.7: encoding declarations, u-prefixed
literals, no f-strings or `open(encoding=)` or bare `str()`, no
`#! python3`, and a parse of every file under the **Python 2 grammar**
itself via lib2to3. It caught a real `"hæð"` byte-literal bug the moment it
was written.

`test_against_real_exports.py` runs the real exports through
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

`test_door_room_check_harness.py` runs the **real** Door Room Check
script against a mocked Revit, 91 checks. The arrow geometry is checked
as arithmetic in every direction, including diagonal, because an arrow
pointing at the wrong room is a drawing that lies quietly. The rest
covers the six door states, rerunning without doubling the marks, a view
the tool did not make being left alone, a level picker cancelled, no
doors, no text note type, a family document, and a commit Revit rolls
back.

Mutation tested. Reversing the arrow fails 9 checks, drawing at a fixed
model size instead of scaling to the view 2, never clearing the previous
run 4, reusing any plan view rather than ours by name 1, preferring the
unphased room over the phased one 1, treating a blank ToRoom as fine 4,
missing the both-sides-same-room case 3, and collecting doors from every
level 2. On the 2.15.2 behaviour: going back to the last phase without
asking fails 9, dropping the room counts from the picker labels 12,
never setting the view's phase 2, ignoring a read only view phase 1,
leaving a view template in place 1, leaving `Lines` hidden 1, saying
nothing when the chosen phase holds no rooms 2, and not counting the
arrows 1.

The fake door now answers only for the phase its rooms are in, which is
the only way the picker can be tested rather than merely exercised.

**Two of those checks started out as decoration and had to be rebuilt.**
A prefix guard inside the cleanup could never fire, since the only view
ever passed to it was the one found by that exact name, so removing it
changed nothing: it is gone as of 2.15.1, and ownership now lives in one
place. It was described as gone at 2.15.0 while it was still there,
because the edit that removed it was applied by a script that did not
check whether its replacement had matched anything, and it had not. The
tests could not catch that, since the guard was unreachable either way.
**An edit that is not verified is a claim, not a change.** And
the foreign view scenario passed under mutation for the wrong reason,
because a run that threw and rolled back restored the annotation it would
otherwise have deleted. Asserting only that nothing was destroyed is not
enough; the positive has to be asserted beside it.

The mock was also wrong twice here, both times in the direction that
hides bugs rather than invents them. `DB.TextNote` was handed to the
script as a namespace rather than a class, so the cleanup collector
matched nothing and the missing cleanup looked like a script bug. And a
door's unphased property returned the same room as its phased getter, so
dropping the phased route entirely changed no result.

`test_zoom_selection_harness.py` runs the **real** Zoom to Selection
script against a mocked Revit, 31 checks. Every scenario asserts the
actual rectangle handed to Revit, because a zoom to the wrong box looks
like the button not working and says nothing about why.

Mutation tested. Dropping the padding fails 7 checks, dropping the
minimum margin 2, using only the first selected element 3, preferring the
model box over the view box 2, dropping the fallback for elements the
view does not draw 3, zooming with an empty selection 1, never reporting
out of view elements 2, and ignoring a missing UIView 1.

One expectation in it was wrong on the first run, and the code was right:
a 3 m element gets the 500 mm floor rather than 15 percent. The check now
says which rule applies and there is a second one for a selection large
enough that the fraction takes over.

`test_datums_workset_harness.py` runs the **real** Datums to Workset
script against a mocked Revit, 48 checks. The interesting cases are all
refusals: a read only parameter, a `Set` that returns False, a `Set` that
raises, an element checked out by another user, and one with no workset
parameter at all. Each asserts both that nothing was written and that the
report says why.

Mutation tested. Collecting views again fails 6 checks, dropping levels
5, ignoring read only 4, writing when the element is already there 2,
ignoring what `Set` returns 2, attempting elements owned by others 2,
ignoring the commit status 2, not offering the usual workset first 2, and
running on a model that is not workshared 2.

One assertion in it was wrong in a way worth remembering: checking that
the word `Views` never appears in the report failed, because the target
workset is called Shared **Views**, Levels, Grids. It now matches the
table row rather than the bare word.

`test_close_link_datums_harness.py` runs the **real** Close Link Datums
script against a mocked Revit, 74 checks. Its fake link type is
behavioural: `LoadFrom` applies the `WorksetConfiguration` it is given to
the link's worksets, so "every other workset stays as it was" is checked
against what the configuration did. One fake link reports `LinkLoaded`
and ignores the configuration, which is the only way the read back after
the reload can be tested at all.

Mutation tested. Opening every workset but the datum one, rather than
what was open, fails 8 checks; skipping the read back 3; ignoring the
`LoadResult` 1; dropping the external resource fallback 3; offering
links where it is already closed 5; offering nested links 3.

**It reports its own failures**, because its first real run will be on
ACC links, where the reload is the least certain step. Anything that
fails, including a `LoadFrom` that raised on one route before the next
was tried, goes to the output window with the link, the route and the
traceback. (Until 2.38.0 it also kept a trace and a crash log in
Documents.) Links that will not list are reported as unreadable, never
as "no links", and a picker that fails to open is reported, never
treated as a cancel.

**First live run, on a live project's ACC links:** no link offered a file
path, the external resource route closed 5 of 6, and the sixth came
back `LinkNotFound`. The link's own `Document.GetCloudModelPath()` is now
tried after the external resource, read before any reload so that a
refusal which unloads the link cannot take the path with it. A refused
reload that leaves the link unloaded says so. **The cloud path did not
fix the sixth link, and nothing in the tool could have**: it was linked
from another ACC project that had not been bridged to this one, so
Revit showed it as loaded but could not reach the model to reload it.
Relinking it from within the project was the fix. `LinkNotFound` on a
cloud link that Manage Links shows as loaded points at the link, not the
tool. Removing the cloud route fails 5
checks, and dropping the unloaded warning fails 1.

**Second live run:** the cloud route raised too, and two reporting
faults hid why. The report said only `path`, which was the .NET
exception's short text; failures now carry the exception's full type and
`Message` through `clsException`. And the crash log showed a `TypeError`
from its own timestamp instead of the `LoadFrom` traceback. The timestamp
used an `isoformat` argument that exists only on Python 3, and on Python
2 `format_exc` reports the most recently handled exception, so the
logger's own caught error replaced the one being logged. That bug was in
the shared `crashlog.write` and would have hidden every schedule export
crash the same way. It now takes the traceback first and uses
`strftime`. Python 3 cannot reproduce the overwrite, so the harness checks
the call order instead. Taking the traceback late again fails 1 check,
and ignoring `clsException` fails 2.

`crashlog.write` took a `name` argument for this. The schedule export
still uses the default, `AVH_export_crash.log`.

**Not yet run in Revit, and three things are from the API docs only**,
all named in the script's docstring: that a cloud link's
`GetExternalFileReference().GetPath()` is a path `LoadFrom` accepts,
that `Workset.IsOpen` on a linked document reflects what the link was
loaded with, and that a `WorksetId` read from the loaded link is valid
in a configuration for its file. The report names which route each link
was reloaded by, so the first real run on ACC links answers the first.

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

## Hide in Template, and the parameter it has to take over

A view template carries category visibility, filters and V/G overrides.
It carries **no per element hide list**, because an element hide is
stored on the view. So there is no way to hide one door in a template,
and this tool works on the categories of the selection instead.

The expensive part is that a template only governs the parameters it
controls. Writing a category hide into a template that does not control
Visibility / Graphics is accepted by Revit and changes nothing anybody
can see, so a tool that wrote and reported success would be lying about
a drawing set.

Switching that parameter on is the fix and it is destructive: the
template takes over category visibility for every view using it, and
whatever those views show or hide today is replaced. It cannot be undone
by unticking the box afterwards.

So the run, in order:

1. Reads which parameters each picked template controls, and which ones
   it does not even offer. A template that does not offer the parameter
   is unreachable and its categories are reported, never attempted.
2. Puts the templates that would be no-ops into a separate offer that
   names them, names the parameter and **counts the views**, because the
   view count is the number that should stop somebody.
3. Takes control in its own transaction, committed before anything is
   written, so the write either lands somewhere visible or does not
   happen.
4. Declining skips those templates and still writes the rest.

Same shape as the vary across groups flag in Flip Status, and the same
reason: **reporting a hazard and then walking into it is worse than not
checking for it.**

### Dialogs abort here, unlike Isolate Warnings

Isolate Warnings falls back to isolating everything when its picker
breaks, because losing the filter is an inconvenience while aborting is a
dead button. This tool writes to the model, so a dialog that fails means
nobody confirmed anything and the run stops. Third instance of the same
rule producing opposite answers in one codebase, and it is decided by
whether the tool writes.

### What the mutations proved

The suite is 58 checks. Breaking the behaviour on purpose:

- write to templates that do not control V/G anyway: 11 fail
- pass blocked categories to Revit anyway: 6 fail
- ignore the commit status: 4 fail
- write every pair regardless of its current state: 2 fail
- never attach a failure preprocessor: 1 fails
- count a template's views every time it appears: 1 fails

The first mutation originally **aborted the suite** with a `ValueError`
instead of failing a check, because an ordering assertion used
`list.index` on a transaction that the mutation stopped happening. A
check that crashes the run hides every check after it, so that one is now
a function that returns False.

The empty transaction was a real find rather than a test artefact. The
first version opened and committed a transaction even when every picked
template was already in the wanted state, which leaves an undo step that
undoes nothing. The state is now worked out by reading, before any
transaction is opened.

## Hide Across Views, and why it is a second button

Hide in Template reaches categories through a template. This one reaches
**elements**, which a template cannot carry at all, so the two are not
two scopes on one job. They are two jobs against two different parts of
the API, and neither can do the other's.

The element hide is stored on the view and no template controls it, so
the element route works on every view picked. The category route, on
shift click, is the one that collides: a view whose template owns
Visibility / Graphics accepts the write and keeps showing the category.
Those views are separated out, never written to, and the report names
Hide in Template as the button that can reach them.

### Eligibility is asked late, on purpose

Whether a given element can be hidden in a given view is one API call per
element per view. Asking that of two hundred views before the picker
opens would make the button feel broken. The picker is built from cheap
per view data, and `CanBeHidden` and `IsHidden` are asked only of the
views actually picked.

### All or nothing across views

One view refusing rolls the entire run back rather than leaving thirty
nine views changed and one not. A reported failure is recoverable; a
drawing set half changed by a tool whose effect is invisible in the
browser is not. The spec written before the build said the opposite, on
the grounds that this tool "only changes visibility" and so could follow
the Isolate Warnings rule. That was wrong: a permanent hide is a write to
the model, and the Isolate Warnings reasoning only ever applied because
its isolate is temporary.

### What the mutations proved

The suite is 48 checks. Breaking the behaviour on purpose:

- write categories onto template owned views anyway: 5 fail
- ignore what a view can actually show: 4 fail
- write regardless of the current hidden state: 4 fail
- ignore the commit status: 3 fail
- commit the views that worked instead of rolling back: 2 fail
- offer view templates in the picker: 2 fail
- offer sheets, schedules and legends: 1 fails
- never attach a failure preprocessor: 1 fails

Ignoring the commit status originally failed **one** check, because the
fake restores state on a rollback whether or not the script noticed, so
only the wording of the report gave it away. Two checks were added that
the report does not claim a view was changed. A false success is the most
expensive failure this codebase has had, and one check was too thin a net
for it.

### The first Revit run: every view came back blocked

Shift click on a real model produced "None of the views you picked can
take that", with no template advice line, meaning every picked view had
landed in `blocked`. That set is filled from one call,
`View.CanCategoryBeHidden`, and the code did this:

```
try:
    if not view.CanCategoryBeHidden(DB.ElementId(category["key"])):
        blocked.add(category["key"])
except BaseException:
    blocked.add(category["key"])          # wrong
```

**A guard that cannot run must not silently refuse everything.** That
except branch makes a broken call produce exactly the same sentence as
Revit legitimately refusing, so the dialog could not tell the two apart
and neither could the person reading it. It is the "silence is the
dangerous outcome" rule in its purest form: the failure and the correct
answer were made to look identical.

Two changes at 2.21.1, both correct regardless of what the root cause
turns out to be:

1. **Only a clear no blocks.** If the question cannot be asked, the
   reason is recorded and the category goes through. That is safe here
   because the write is one transaction with a failure preprocessor and a
   checked commit, so a genuine refusal rolls the whole run back and
   reports Revit's own words. Letting Revit answer beats a guard that
   cannot.
2. **The real `Category.Id` is carried through** instead of being rebuilt
   with `DB.ElementId(value)` later. `ElementId` is overloaded and
   `Value` is an Int64 on Revit 2024 and newer, so the reconstruction was
   a round trip that could fail on its own. There was never a reason to
   take it: the original object was already in hand.

The dialog now names which of the three reasons applied: Revit refused
it, a template owns it, or the guard itself fell over and here is the
exception. Whichever comes back next names the cause.

Both buttons had this bug. Hide in Template shipped first and would have
hit the same wall.

### A section marker is the Views category, and that is a dead end

Shift click with a section selected in a plan reported "The selection is
only Views, and a view cannot hide those". Correct, and useless.

Selecting a section marker hands back the **ViewSection itself**, whose
category is Views. `CategoryType` for Views is neither Model nor
Annotation, so no view and no template can switch it off. The switch
anybody would reach for is **Sections, under Annotation Categories**,
which is a different category the selection cannot lead to.

So the tool was right and the message still left the reader believing the
thing they wanted was impossible, when the plain click does it: the
element route hides the selected markers themselves, which is exactly
Revit's own Hide in View, Elements. Both buttons now say that.

**A refusal that does not name the route that works is only half a
message.** The same failure as the guard above, one layer up: the tool
knew enough to be useful and reported a wall instead.

### Then Björn said that is exactly what he wants

At 2.22.0 the swap is made: a selected view whose category is Views
resolves to the annotation category governing its marker.

| ViewType | BuiltInCategory | Confirmed |
| --- | --- | --- |
| Section | `OST_Sections` | Yes, by hand against the V/G dialog |
| Elevation | `OST_Elev` | No, read off the same list |
| Detail | `OST_Callouts` | No, read off the same list |

Keyed on **ViewType** and valued with **BuiltInCategory names**, both of
which are English in the API whatever the interface language. Matching a
displayed category name would break on the first non English
installation, which is not hypothetical here.

**What made this safe to ship without verifying all three.** The
resolved category's real name goes into the confirmation dialog before
anything is written, so a wrong row is visible and cancellable rather
than discovered afterwards in a drawing set. That turns an unverified
mapping from a guess into a proposal a human approves. The two unconfirmed
rows are marked as such in the table above rather than implied to be
tested.

**The swap is a change of scope and is stated as one.** Selecting one
section and switching off every section in a drawing set is not what
"hide this" sounds like, so the dialog says so in as many words before
the write. Silently widening scope is the kind of thing noticed after the
drawings go out.

A view kind with no entry in the table, a 3D view say, still gets the
clean refusal from 2.21.2.

### 2.22.0 did nothing, and could not say why

The swap shipped, the file was confirmed on disk at the right commit, and
shift click on a section produced the **same 2.21.2 dialog**, which is
what appears when the selection has no reachable category. So the swap
ran and failed, and `marker_category` had **four** ways to fail, every one
of them a bare `return None, None`:

- the element has no `ViewType`
- `ViewType` is not in the table
- the `BuiltInCategory` is missing from this Revit
- `Category.GetCategory` raised or returned nothing

Four silent paths behind one dialog. That is how the same message appeared
twice in this file with two different causes behind it, and it is the
third time the rule has had to be relearned here:

**A path that gives up must say which path it was.** Not to a log nobody
reads: to the dialog the person is already looking at.

2.22.1 does not fix the cause, because the cause is not known. It makes
the next click name it. Each of the four bails now writes a specific line
into the refusal, including the `ViewType` string it actually saw, which
also settles whether IronPython renders the enum bare or fully qualified.

One thing was fixed rather than instrumented: the lookup now takes the
last dotted segment of the ViewType text, so
`Autodesk.Revit.DB.ViewType.Section` matches as well as `Section`. That is
one of the four candidates removed rather than diagnosed. The note still
reports the string it saw either way, so the answer arrives whether or not
that was it.

**Do not add a fifth silent return here.** Four mutations confirm the
notes bite: restoring any one of the bare returns fails a check, dropping
the reasons from the dialog fails four.

### The premise was wrong: a section marker is a Viewer

2.22.1's instrumentation answered in one click: **the selected element
has no ViewType.**

Selecting a section marker in a plan does not hand back the
`ViewSection`. It hands back a **`Viewer`**, which sits in the Views
category and is **not a View at all**, so it has no `ViewType` and never
did. Three releases were aimed at an object that never arrives:

- 2.21.2 explained the Views category to the user
- 2.22.0 mapped ViewType to a marker category
- 2.22.1 instrumented why that mapping never ran

All three were reasoning about a `ViewSection`. Only the fourth asked
Revit what it had actually been handed, and the answer took one dialog.

**The lesson is not "add diagnostics", it is where they go.** Every one
of those releases could have been skipped by reporting the element's
class the first time the swap failed. The cheapest diagnostic in this
codebase is `element.GetType().Name`, and it was missing from a function
whose entire job was deciding what kind of element it had.

### Crossing from a Viewer to its view

`VIEWER_VIEW_NAME` on the Viewer carries the name of the view it stands
for, and that is the route across.

**Matching by name is not something to be pleased about.** It is taken
here because the parameter holds a name and nothing else, the match is
against views in this same document rather than against anything a user
typed, and an ambiguous match **refuses rather than guessing**: two views
sharing a name produce "2 view(s) carry that name" and no write. A tool
that picked the first match would eventually switch off Elevations
because a section happened to share a name.

Every failure along that route names the element's class first, so the
next report starts from what Revit handed over rather than from what the
code hoped for.

### Three guesses at a parameter, then stop guessing

2.22.2 followed the marker through `BuiltInParameter.VIEWER_VIEW_NAME`.
Revit's answer: **that parameter does not exist here.** The class came
back as plain `Element`, not `Viewer`.

That was the third guess in a row about an object nobody had looked at:
`ViewSection`, then `ViewType`, then a parameter name recalled from
memory. Each one cost a release.

**Naming a parameter is a guess. Reading what the element carries is a
measurement, and it costs the same.** 2.22.3 does that:

1. If the element has a `ViewType`, it is a view already.
2. Otherwise every parameter is read, and any whose `AsElementId`
   resolves to something with a `ViewType` **is** the view, whatever that
   parameter happens to be called.
3. Failing that, any parameter whose string matches exactly one view name
   is the view. Two views sharing the name refuse rather than pick one.
4. Failing all of that, the element itself is printed to the output
   window: class, category, and every parameter with its
   BuiltInParameter and value.

Step 2 is the one that ends this. It works whatever the parameter is
called, on any Revit version, in any interface language, because it never
names anything.

Step 4 is the lesson from the four releases before it. When a route
fails, print the object, not another sentence about the object.

### It resolved, and it resolved to the wrong view

2.22.3 read the parameters, found a view, and switched off **Elevations**
for a section. The route worked; the answer was wrong; and the
confirmation said only "Hide Elevations in 6 view(s)", which was approved
because there was nothing in it to disagree with.

Two defects, both visible in the code once the symptom named the shape:

**A view template was an acceptable answer.** The id route accepted
anything with a `ViewType`, and a view template has one. An id parameter
pointing at an elevation template was therefore a perfectly good match.
Templates are now excluded.

**The first candidate won.** Several parameters can resolve to views, and
the code returned the first without ever noticing the others. It now
gathers all of them, and **if they disagree on view kind it writes
nothing** and names every candidate with the parameter it came from.
Picking one of two silently is the failure that produced this report.

**The confirmation named only the winner.** "Hide Elevations" is not
something a person can check. "followed to the view East (Elevation)
through the parameter Other" is. The resolved view, its kind and the
parameter are now in the confirmation, and the whole resolution goes to
the output window on every run whether it succeeds or not.

**A resolution that cannot be checked afterwards is one nobody can
correct.** That is the same rule as the silent returns, arrived at from
the opposite direction: the first version said too little when it failed,
this one said too little when it succeeded.

### What the working run established

Confirmed against Eldisgardur on 8 Sep 2026. The dialog reported:

```
Element names a view Snid, and 6 views carry that name, so it is ambiguous.
Element was followed to the view Langsnid - Dependent 2 (Section)
through the parameter View Name.
```

Three facts worth keeping, none of which were guessable:

**The id route finds nothing.** The marker carries no parameter holding
an ElementId that resolves to a view. Every attempt that assumed a
typed link to the view was aimed at something that does not exist. What
works is the **string route**, through a parameter literally called
**View Name**.

**Dependent views resolve fine.** The answer was
`Langsnid - Dependent 2`, a dependent view, and its ViewType is Section
like its parent. Nothing special was needed for that.

**A second parameter matches many views by accident.** Something on the
marker holds `Snid`, the Icelandic word for section, and six views in the
model are named that. The ambiguity refusal did its job: it declined that
candidate instead of picking one of six, and the run still succeeded on
the parameter that was unambiguous.

That last one is why the ambiguity check refuses per candidate rather
than aborting the whole resolution. Aborting would have failed a run that
had a perfectly good answer sitting in the next parameter.

**Fixed at 2.22.5.** That ambiguity line appeared in the confirmation
next to a successful resolution, where it read as a warning about
something that did not go wrong. A near miss now goes to the output
window when another parameter resolved, and stays in the dialog when
nothing did, which is the only case where it is the whole story.

Björn's reason for wanting it moved is the right one and worth writing
down: **other people will use these buttons, and a warning about nothing
teaches them to stop reading the dialog.** Every safeguard in both tools
is a sentence in that dialog, so the cost of one false alarm is not the
alarm, it is every real one after it.

### Known duplication

`selection()` here and `selected_categories()` in Hide in Template do the
same job. They are deliberately not shared yet: folding them into one
reader moves both buttons at once and neither has been run in Revit.
Fold them once both are confirmed.

## Clash Detector (2.44.0)

Elements -> solids -> triangles -> a three.js scene in a WebView2, with
the clash test itself done by Revit on the solids.

```
lib/avh_clash/
  model.py     boxes, candidate pairs, the triangle test, mesh packing. No Revit
  reader.py    Revit elements -> solids -> triangles; the exact test
  service.py   what the window can ask for: inventory, run, select, section box
  window.py    WPF window + WebView2 + the ExternalEvent
  web/         index.html, app.css, app.js (the page), viewer.js (the scene),
               demo.js (a made up building, browser only), vendor/ (three.js)
```

**The test is two phases.** Bounding boxes first, by a sweep along x
(`model.candidate_pairs`), then each surviving pair for real. Boxes that
only touch are not candidates: a wall standing on a floor shares a plane
with it, and skipping it there saves a boolean per wall.

**Revit does the real test, not the triangles.**
`BooleanOperationsUtils.ExecuteBooleanOperation(a, b, Intersect)` gives
the shared solid, so a clash has a volume, a depth (the thinnest
dimension of the overlap's box, which over-reads on rotated geometry
and so never hides a real clash behind the tolerance) and a mesh to draw
in red. An overlap under one cubic centimetre is treated as touching.

**The triangle test is the fallback, and says so.** A boolean that
raises, a raw `Mesh` (many IFC links), an open shell, or a linked solid
that will not transform is tested by looking for an edge of one mesh
going through a triangle of the other. It reports "approximate", with no
volume and no depth, because surfaces crossing says where, not how much.
Two things in it were learned from the test suite rather than thought of:

- A crossing has to be strictly inside the triangle, not on its edge.
  The first version allowed the edge, and a floor clashed with every
  wall standing on it: the floor's top face is two triangles, and their
  shared diagonal runs under the wall's foot, exactly through the bottom
  edge of its side faces.
- One element wholly inside another crosses no surface at all, so that
  is a separate ray count.

**Coordinates are metres from the middle of the run**, not feet from the
internal origin. The viewer holds positions as float32, and a
georeferenced model far from its origin would lose its millimetres.
`Service.last` keeps each clash's box in feet for going back to Revit.

**Geometry travels as base64 inside JSON messages**
(`PostWebMessageAsJson`), float32 positions and uint32 indices, several
meshes to a message of about a megabyte. No temp files. The viewer
merges everything into one mesh per set, so the frame costs two draw
calls however many elements there are, and renders only when something
changed. Elements that clash with nothing are sent last and stop at 1.5
million triangles; clashing ones are always sent whole.

**The window is modeless and that is the risky part.** A modeless window
is outside the API context, so `run`, `select` and `sectionbox` are
queued and run from an `ExternalEvent`, and the button sets
`__persistentengine__` so the engine that owns the window is not thrown
away when the script ends. Shift click opens it with `ShowDialog`
instead, where the handlers are still inside the command and need no
event: the way round if the event route fails on some machine. During a
run the dispatcher is pumped between pairs so progress paints and Cancel
arrives, the way pyRevit's own progress bar does it.

**A persistent engine keeps its modules.** `__persistentengine__` keeps
one IronPython engine for the button so the window outlives the click,
and that engine keeps everything it has imported, through a pyRevit
reload. Only `script.py` is read again on each click. So 2.44.2's fix to
`window.py` sat on disk while Revit kept running the 2.44.0 copy, and
the traceback named a line of code that was no longer in the file. When
a traceback disagrees with the file, this is the first thing to suspect.

Since 2.44.3 the button reloads `lib/avh_clash` (model, reader, service,
window, in that order) every time it opens a new window. It does not
when it only brings an open window forward, because that window is
still running the code it was made with; close it first to pick up a
change. The harness proves both: a stale `Service` planted in the loaded
module must be replaced by a new window and left alone by bringing one
forward.

**The setup is two models and one list (2.45.0).** Asked for after the
first working run, from a mock-up: pick model A and model B, then an A
and a B button per category. The page keeps a model choice and a set of
category ids per side, and the categories survive a change of model, so
switching B from one link to another keeps what was on. "All links" and
"This model and all links" are page-side choices only: `runConfig`
expands each into one entry per real source, carrying the ticked
categories that source has, so the service is unchanged and a pair
between two links is tested like any other.

The discipline headings come from `reader.DISCIPLINES`, `BuiltInCategory`
names grouped by hand, sent per category as `group` with the heading
order as `groups`. They are matched on the enum, not the name, so they
hold in any interface language and for any link; a category not in the
table is listed under Other rather than dropped. The grouping is a
judgement (railings under Interior, slab edges under Structure, as in
the mock-up), so move entries freely.

**Clearance zones are measured, not modelled (2.46.0).** A soft clash
is two elements that do not intersect but whose surfaces are closer
than the clearance of one of them. The gap is measured between the same
triangles the viewer draws (`model.mesh_distance`: point to triangle and
edge to edge, Ericson's closest-point routines, with a sweep along x
that drops triangle pairs whose boxes are already further apart than
the best gap so far). Building an inflated solid in Revit was the other
road; it would need a different construction per kind of geometry
(a swept circle for a pipe, nothing general for a fitting) and could not
say how close. A distance works for any two meshes and gives the two
nearest points, which the viewer draws as the gap.

The zone grows each zoned element's box by the clearance before the
broad phase, so a near miss becomes a candidate pair. Each pair then
gets the hard test if the real boxes overlap, and the gap test only if
that found nothing, so a pipe through a wall is one hard clash, not a
hard and a soft.

Tessellation draws a round pipe as facets inside the true surface, so a
gap to a pipe reads up to r(1 - cos(pi / n)) wide: under 1 mm for DN100
at medium detail. Choose Fine detail when a zone is tight.

**Built together is never a clash.** Without this every pipe joint is
a soft clash: a pipe and its fitting meet face to face, and insulation
wraps its pipe. `reader.joined_ids` collects what an element's MEP
connectors are connected to and an insulation or lining's host, and the
pair is skipped, hard or soft, and counted in `stats.joined`. Only
within one model; a link's pipe is never connected to the host's. The
connector calls (`ConnectorManager`, `AllRefs`, `Owner`,
`HostElementId`) are read from the API docs and unverified in Revit.

The zone is drawn by pushing the element's surface out along the
average of the face normals at each corner, scaled so every face moves
the full clearance. Square corners stay square, pipes stay round; it is
a picture of the zone, not what the gap is measured against.

Mutations: zoned boxes not grown for pairing (crash: the near miss is
never paired), built-together pairs not skipped (4), the zone only
counted on side B (1), the insulation host ignored (3), a sweep that
drops triangles still in reach (1). Counting a gap exactly equal to the
clearance survives, because the box test before it already excludes
that case.

**WebView2 comes from Revit.** 2024 to 2027 all ship
`Microsoft.Web.WebView2.{Core,Wpf}.dll` in the install folder, so
nothing is bundled but three.js. The page is served through a virtual
host (`https://avh-clash.example/`) mapped to `lib/avh_clash/web`,
because a `file://` page may not load JavaScript modules.

**One exception to "no files on users' machines".** WebView2 will not
start without a browser profile folder, and its default is beside
Revit.exe in Program Files, which is not writable. It goes in
`%TEMP%\avh_clash_wv2_<process id>`, is deleted when the window closes,
and one left by a Revit that has gone is deleted at the next start.

**Working on the viewer needs no Revit.** `python -m http.server` in
`lib/avh_clash/web` and open it in a browser: with no WebView2 host
behind it, `app.js` loads `demo.js`, which answers the same messages
with a three storey box of walls, pipes and ducts. That is how the page
was checked at 2.44.0, in light and dark.

test_clash_model.py covers the arithmetic, the gap and the JSON (112 checks); the harness (137)
runs the real script and service against a fake Revit made of boxes,
where a boolean is the box two boxes share and a link sits somewhere
else. Mutations caught: commit status ignored (1), link solids not
moved into place (crash), link boxes not transformed (crash), volume
left in cubic feet (1), an element paired with itself (6), touching
counted as going through (4), cancel ignored (2), section box ignored
(1), a run after the window closed (1).

### The first run in Revit (2.44.0 -> 2.44.1)

The window opened, so WebView2, the virtual host and three.js all
loaded. But the Set A and Set B lists stayed empty, and Run answered
"Set A is empty". That is exactly what a page that never received
`init` does: the trees stay blank, and Run, which was only disabled
once `init` had arrived, sends two empty sets. The Python side posted
`init` inside a `try` that swallowed every failure, so whatever stopped
it said nothing.

2.44.1 did not guess at the cause; it made the next run say, and the
first run of it did: IronPython's `json.dumps` raised on an "ö" in a
category name (see "A fifth: never `json.dumps` on IronPython" under
Unicode). 2.44.2 writes the messages with `model.to_json`. What 2.44.1
added stays, because it is what found this:

- `window.post` no longer swallows: a message that cannot be sent is
  logged to the pyRevit output and an error naming it goes to the page.
- The page reports its own exceptions (`window.onerror`, rejected
  promises, a throw in `applyInit`) as a red box and sends them to the
  pyRevit log through a `log` message.
- Run starts disabled. If no `init` arrives within six seconds the page
  says so, and **Refresh from Revit** asks for it again through the
  ExternalEvent.
- A set that comes back empty carries a tally (`why`): entries sent,
  categories ticked, elements looked at, matched, without a 3D extent,
  and outside the section box. The page turns it into a sentence that
  names the step that emptied it.

The same release adds what the first test asked for: quick setups
(this model against its links, against itself, the Revit selection
against everything) and the refresh, which also picks up a selection
made after the window opened.

Unverified in Revit beyond the window opening. The calls that carry
the risk are listed at the top of `reader.py` and `window.py`. If the
window opens blank or not at all, shift click first: that separates a
WebView2 problem from an ExternalEvent one.

Not done: clearance (near miss) tests, clash status that survives
closing the window, a report to Excel, and view visibility for linked
elements (a link is tested whole unless the view has a section box).

## Pipe Openings (2.47.0 to 2.47.2)

Select walls, floors or roofs; a window asks for the shape and the
clearance; every pipe and conduit through them gets an opening.

### Found through the solid, not the box

A crossing is found by putting the pipe's centre line through the
host's actual solid (`Solid.IntersectWithCurve`), not with bounding
boxes or `ElementIntersectsElementFilter`. The filter only works inside
one document and the pipes are usually in a link; boxes find a pipe
that passes beside a wall. The solid gives the two points where the
axis meets the faces, which is exactly what the sizing needs.

Two things fall out of that for free. A pipe that already has an
opening runs through the hole and no longer meets the solid, so a
second run cuts nothing. And after the run the same test is the
independent check: each pipe is put through its host again and one
that still meets it where the opening should be is flagged, whatever
Revit reported.

A compound wall can come back as one solid per layer. The segments are
joined where they touch (`model.join_segments`), or the wall would get
one half size opening per layer. A mutation that removes the join is
caught by the layered wall scenario in the harness.

### The size of a hole for a pipe at an angle

The pipe inside the host is the convex hull of its cut ellipses at the
two faces, so the opening covers both ellipses, seen along the cut,
and everything between them (`model.size_opening`). The arithmetic
needs each face's own normal, read from Revit with `Face.Project` and
`Face.ComputeNormal` at the points where the axis leaves the solid.

2.47.0 used the cut direction as the face normal everywhere. That is
exact for walls and flat slabs, where the faces are square to the cut,
and wrong for a sloped roof, which is cut vertically through faces
that lean. For a 160 mm pipe 30 degrees off vertical through a 300 mm
roof it was 46 mm short a side when the pipe leaned with a 30 degree
slope, 126 mm short at 45, and oversized the other way. Vertical vents,
the common case, were right. Fixed in 2.47.1.

`test_openings_model.py` does not trust the formula: it samples the
real pipe surface inside the real slab, faces sloped or not, and checks
every point lands inside the opening and the rectangle is within 2 mm
of the tightest that fits, at five wall and floor angles and seven roof
slope and lean combinations. The sampling covers only the stretch of
axis whose surface can be inside the slab. A fixed range spread the
samples too thin to find the tip of a long ellipse and read 4 mm short
at 75 degrees through a 45 degree roof, where sampling 70 times finer
put the formula within 0.1 mm.

Where a face cannot be read, the size falls back to the cut direction,
which is what 2.47.0 did, and the row in the report says so, because on
a sloped roof that is too small and nothing else would show it.

The 75 degree limit is measured against the face, not the cut: a vent
square to a 30 degree roof reports 30 degrees off square. A face within
5 degrees of parallel to the cut is refused.

### Circular wall openings need a family

`Document.Create.NewOpening` makes rectangles in walls and any profile
in floors and roofs, so a round wall opening has to be a family:
wall hosted Generic Model `AVH_Round_Opening`, a void cylinder through
the wall, a length instance parameter `AVH_Diameter`. Building one at
run time from Revit's template was considered and rejected: a family
document, a void extrusion and a load, all unverified, to save making
one file once by hand. Until the file exists the wall rows are skipped
with that reason and the rest of the run goes ahead.

### What the mutations proved

Twenty-eight mutations, each caught. Added at 2.47.4: shaft offsets
not set (8), not read back (2), Set's result ignored (1), no shaft
fallback (9), the band ignoring the slope (10) or measured at the axis
rather than the opening's centre (2), levels not sorted (4), columns
right aligned again (1). Added at
2.47.2: no second sketch height (9), the same height tried twice (1),
the level offset ignored (6), the sketch height ignored (7). From
2.47.1: the face
lean ignored (22), the circle sized on the ellipse's short axis (9),
only the entry ellipse used (4), rounding down (8), no skew limit (4),
face normals ignored (2), the normal taken from the last face seen
rather than the nearest (45), no flat warning (1), the table printed a
line at a time (3), insulation ignored (1), link transform ignored (1),
commit status ignored (1), no check after (2), no move back for a
misplaced family (4), group assimilate status ignored (1), segments not
joined (2).

Two needed the harness made more like Revit before they were caught.
Segments not joined survived until a wall came back as one solid per
layer. The wrong face survived until the fake slab had its four edge
faces, whose normals are horizontal; with only top and bottom, which
share a normal, any face was the right one.

### Where a floor or roof opening's sketch goes (2.47.2)

`NewOpening(host, profile, False)` takes the outline as curves, and
2.47.0 and 2.47.1 drew them at the middle of the host's thickness. In
Revit 2025 a roof refused that at commit: "Can't change plane of
Opening Cut Sketch." The cut is vertical through the whole host, so the
height of the outline changes nothing about the hole, but Revit wants
the sketch on its own plane and the API documentation does not say
which.

So it is asked rather than guessed: `sketch_heights` lists the host's
level plus its offset (`ROOF_LEVEL_OFFSET_PARAM` or
`FLOOR_HEIGHTABOVELEVEL_PARAM`), its level, its middle and zero, with
duplicates removed, and `attempt` tries each in a transaction of its
own until one commits. The report says "made, sketched at ..." when the
first was not the one, so the next live run shows which plane Revit
wanted. Once that is known, the list can be cut to it. The refusals
from the failed tries are reported once each if none is kept.

The harness roof refuses every height but one, either by raising from
`NewOpening` or as a failure at commit, since which of the two Revit
did is not known from the dialog text alone.

### When every sketch height is refused: a shaft (2.47.4)

The second Revit 2025 run, with the report naming every height tried,
settled it: the roof refused a vertical cut at its level, its middle
and zero alike. The plane was not the cause, so no choice of plane
will fix it, and the bridge that would have let the alternatives be
probed in Revit would not pair (401 after every pairing, app restart
included).

So a floor or roof that refuses every height now gets a shaft
opening, `NewOpening(base level, top level, profile)`, in a transaction
of its own. A shaft cuts every floor, roof and ceiling between its
ends, so it is never left running from level to level: its base and
top offsets (`WALL_BASE_OFFSET`, `WALL_TOP_OFFSET`, read from the API
documentation only) are set to `model.shaft_band`, which holds both of
the host's faces over the whole opening plus 10 mm, and each is read
back. A shaft whose offsets are refused or do not keep their value is
rolled back, and the reason reported. The report says "made as a shaft
opening" so it can be checked in a section.

The harness's shaft cuts any floor or roof that a vertical line through
its centre meets within its band, and a floor sits under the roof
between two distant levels, so a shaft left at its levels visibly cuts
it. `test_openings_model.py` checks the band against the faces sampled
across the opening at five slopes and leans, both ways: it must hold
them, and by no more than the margin.

### The output table is printed in one call

The first Revit run showed the report's columns out of line. pyRevit
renders each `print_md` call as a document of its own, and the table
was printed a line at a time, so the header was one table and each row
another, every one sizing its own columns. It is now built whole and
printed once, cells escaped for `|` and line breaks, and no cell left
empty. The harness checks the header and rows arrive in one call with
six columns each, all left aligned: pyRevit marks a `---:` column right
aligned on header and cells, but the output window right-aligns only
the header, so the heading drifted off its values (seen at 2.47.1). The other buttons that printed tables a line at a
time were fixed in 2.47.3.

### Unverified

Everything crossing into Revit, read from the API documentation only:
`Solid.IntersectWithCurve`, `NewOpening` in both forms, `Arc.Create`,
`NewFamilyInstance` on a wall host, `TransactionGroup.Assimilate`, the
MEP diameter and insulation parameters, `Face.Project` and
`Face.ComputeNormal`, and the WPF settings window.

## Update Openings and the tag (2.48.0)

Pipe Openings writes a tag onto every opening it makes, and Update
Openings reads it. Until 2.48.0 an opening knew nothing of the pipe it
was cut for, so a moved pipe left a hole in the wrong place with no
way to find it.

### The tag

`avh_openings.tags` builds and reads it: the pipe's and host's
UniqueIds, the link instance's when the pipe is in a link, what kind of
opening it is (`opening`, `shaft`, `round`), the shape and clearance,
and the centre and size it was made at. Plain `key=value` lines, ASCII
only, numbers written with `repr` so a decimal comma locale cannot
reach them, and a version that a later format must change. Not JSON:
IronPython's `json.dumps` raised on non-ASCII text in the Clash
Detector, and nothing here needs it.

`avh_openings.storage` keeps it in extensible storage, under a schema
with a fixed GUID (`6f1c2a0e-...`), one string field, public access.
It is read back after every write. A tag that will not go on is noted
on that row of the Pipe Openings report ("not tagged, so Update
Openings will not see it") and never costs the opening.

### Checking without breaking

A pipe running through its own opening does not meet the host's solid
(that is how a second Pipe Openings run skips it), so the crossing
cannot be measured with the opening there. Update Openings deletes each
opening inside a transaction it always rolls back, measures on the
whole host, sizes exactly as Pipe Openings does, and compares with the
tag: the same within half a millimetre in the face and in size is
unchanged. Depth is not compared, because it moves when a pipe tilts
without the hole needing to.

The re-cut deletes the old opening and makes the new one in the same
transaction, through `avh_openings.writer`, which both buttons now
share: it is the Pipe Openings code moved out of the script, with a
`before` hook for that delete and an `after` hook for the tag. Every
way of making an opening is tried in a transaction of its own, so the
delete is redone with each try and undone with each refusal, and when
Revit refuses them all the old opening is still there.

Orphans are asked about in a question of their own that is only asked
when there are some, and No keeps them.

### The harness

The fake document gained UniqueIds, extensible storage, and `Delete`
that takes an opening's holes out of its hosts at once and puts them
back on rollback. That last is the kind of state that outlives a
transaction, which is the bug fakes like this have had most, so the
unchanged scenario checks the model is exactly as it was after every
check has deleted and rolled back. Scenarios cover a moved, resized
and deleted pipe, a pipe moved off its host, a linked pipe unloaded and
moved, a checked out opening, a re-cut Revit refuses, a selection, an
untagged and an unreadable tag, a shaft through a sloped roof and a
round wall opening following their pipes, a declined re-cut, storage
that raises or silently keeps nothing, a re-cut that lands off the
pipe, and a run Revit rolls back.

Thirty-nine mutations, each caught. Two first crashed the harness
rather than failing a check, by indexing an opening that was not
there; `first` and `hole_at` turn that into a failed check, so a crash
can no longer hide the checks after it. The mutation runner now also
tells a crash from a mutation that is not valid Python, which an
indentation slip in the runner itself had briefly passed off as caught.

### The first Revit run (2.48.0 -> 2.48.1)

The tags read back and both moved pipes were found, 1077 and 631 mm
off, so the storage calls work. Then the report after the re-cut
crashed: "The referenced object is not valid, possibly because it has
been deleted from the database". It asked each old opening for its Id,
and Revit refuses that once an element is deleted. The re-cut itself
had already committed; only the report and the summary were lost.

The harness had not seen it because its deleted openings still
answered for their Id. Now `Element.Id` raises once an element is
deleted, and recovers on rollback, as in Revit, and the harness failed
seven checks until the script kept each opening's Id at the start
(`Item.element_id`) and used only that after the first delete.

**Never touch an element after deleting it, even to name it in a
report.** Keep what the report needs before the delete.

### Unverified

The extensible storage calls in `avh_openings.storage`, including
IronPython's `entity.Set[String]` generic call, and `Document.Delete`
and `GetElement` by UniqueId, are from the API documentation only.

## Split Wall Layers (2.49.0)

Built without access to Revit: the MCP bridge was not paired, so
nothing in it has been measured against a real wall yet.

**Where a layer goes is arithmetic, kept out of Revit.** A wall's curve
is its location line, which can be any of six places across the wall
(`WALL_KEY_REF_PARAM`, the `WallLocationLine` enumeration). Each new
wall goes at `layer centre - location line offset` along
`Wall.Orientation`, both measured from the wall centre with the
exterior positive. `avh_walllayers.model` does this and is tested
directly, including an asymmetric wall located by its core face.

**The copy, then everything set explicitly.** Each new wall is
`CopyElement` of the original, so it keeps the parameters nobody wants
to list one by one. It is then given its type, then its location line
(centreline), then its curve, in that order. In Revit, changing a
wall's type keeps the location line where it is, and changing the
location line keeps the faces where they are and moves the curve; the
harness's fake does both. Setting the curve last makes the result
independent of either side effect. Setting it before the location line
puts the wall a location line offset out, which the harness catches
(mutation: key set after the curve, 2 checks fail).

**The harness checks faces, not the script's numbers.** The fake works
out each wall's faces from its own curve, location line and layers,
with code of its own, and the main check is that the new walls tile
the original's layers exactly. A check that compared the new curves
with `model.shifts` would only be the script agreeing with itself.

**Doors and windows were refused in 2.49, not moved.** See 2.50.0
below for how they are split now.

**The first Revit run (2.49.0 -> 2.49.1).** A plain partition was
refused: "its layers change up its height". 2.49.0 asked
`CompoundStructure.IsVerticallyCompound`, which says how the structure
is stored, and was True for that wall. The question meant is
`IsVerticallyHomogeneous()`, true when each layer is one region from
bottom to top. The fake now says IsVerticallyCompound True for every
structure, as Revit did, and putting the 2.49.0 check back fails 38
checks. The refusal now quotes the value it acted on, so if this is
still wrong the next report names it. The run also printed a table
with a header and no rows, since every wall was refused; that table is
now left out.

**Dependents are found by deleting and rolling back**, with their
categories read *after* the rollback: a deleted element cannot be
looked at inside the transaction that deleted it.

Mutations, each failing the suite: no location offset (3), the curve
on the wrong side (6), key set after the curve (2), assimilate status
ignored (1), a refused wall's commit ignored (3), no check after the
run (2), an original still in the model not flagged (1), inserts not
refused (4), membranes made into walls (2), core faces off by one (5),
existing types ignored (3), a new type's width not read back (1).

Unverified, to look at on the first run: `CopyElement` on a wall,
`ChangeTypeId`, `WALL_KEY_REF_PARAM.Set`, `LocationCurve.Curve`,
`WallType.Duplicate`, `CreateSingleLayerCompoundStructure` for a
finish or insulation layer, `Wall.FindInserts`, `Wall.Orientation` on
a flipped wall, and how the new walls join their neighbours once the
original is gone. Every new wall keeps the original's structural flag,
gypsum included.

## Split Wall Layers: doors, windows and openings (2.50.0)

**The original becomes the core.** Deleting it and placing new doors
would lose every door's id, and with it its tags, marks and schedule
rows; Revit's API cannot move a door to another wall. So the original
is kept as the thickest layer inside the core (`model.core_layer`), the
doors stay in it, and only the other layers are copies.

**Measured before it was built**, with a throwaway Probe Wall Joins
button that did the split in a transaction group, measured, and rolled
back. Three runs in Revit 2025:

1. "Can't make type M_Door-Passage-Double-Flush" twice and nothing
   kept. `CopyElement` on a wall copies its doors with it but returns
   only the wall, so the probe never deleted them, and Revit could not
   build a 1800 mm door in a 12.5 mm gypsum copy. Asking the copy for
   its inserts (`FindInserts` after `Regenerate`) finds them.
2. A 16 / 93 / 16 partition with a door: after `JoinGeometryUtils.
   JoinGeometry` with the core both gypsum walls were cut, their
   volume down by 16 mm times the door's opening. The door stayed in
   its wall and did not move.
3. An exterior wall, brick 92 / air 75 / membrane / plywood 19.5 /
   stud 150 core / membrane / gypsum 12.5, located by its centreline so
   the core is 87 mm off it, with a double door, a fixed window and a
   rectangular wall opening. Every layer joined straight to the core,
   including the brick 94.5 mm away from it, was cut by the door and
   the window, with no warning. Joining neighbours as well, or
   switching the join order, changed nothing. The wall opening cut only
   the core, whatever was joined. The door and window moved -87 mm
   across with their wall and nothing along it.

So doors and windows are cut through joins, and wall openings are
copied into each layer with `NewOpening` (the call Pipe Openings uses),
their `BoundaryRect` moved across by the layer's shift. A wall opening
that is not a rectangle, or anything in a wall other than a family
instance or a wall opening (an embedded curtain wall), is refused.

**The check after the run reads faces.** For every layer wall: is it
joined to the core, was a copied door left in it, and did each door,
window and opening make a face of its solid
(`GetGeneratingElementIds`)? The probe showed that the face check and
the volume agree. Geometry that cannot be read is reported as not
checked, never as passed.

**Dimensions are looked for after the run.** The original is no longer
deleted, but a dimension to a face that moves can be. Everything that
deleting the wall would take, less its own doors, windows and
openings, is counted before the question, and any gone afterwards is
listed.

**The fake.** Doors, windows and wall openings hosted in a wall; a copy
that carries them but returns the wall alone; joins kept in the
transaction snapshot (one left outside it would outlive a rollback);
faces made by the wall's own inserts and by the doors and windows of
the walls joined to it, not their openings; "Can't make type" when a
door sits in a wall thinner than it needs; and dimensions on a face
removed when the face moves.

Mutations, each failing the suite: copied doors found only from what
`CopyElement` returned (5), no joins (11), openings not copied (2), an
opening not moved to its layer (1), the core as the first layer (9),
the core range ignored (2), no cut check (2), no joined check (1), no
check for a copied door left behind (1), a refused join ignored (1),
removed dimensions not looked for (1), doors counted as on the wall
(1), unreadable geometry taken as no cuts (1), a non-rectangular
opening accepted (11), an embedded wall accepted (4). Two survive and
are expected to: doing the core before the copies (the curves are set
absolutely, so the order does not matter) and dropping `Regenerate`,
which the fake cannot model; the probe regenerated.

Unverified until the release is run: a round AVH pipe opening (a
family instance, so it should cut through joins like a door), a door
whose family sizes its frame to the wall's thickness, and walls joined
at their ends to neighbours.

### Shift-click: exterior, core, interior

Shift-click (`EXEC_PARAMS.config_mode`, or the older `__shiftclick__`,
as in Clash Detector) groups the layers by the core boundaries instead
of splitting every one: `model.parts` returns up to three `Part`s,
which stand in for a `Layer` wherever the script only needs a width,
an index and a shift, so copying, joining, opening copies and the check
after the run are the same code. `model.part_shift` puts each part's
centre where its layers were.

A part of several layers gets a type from
`CompoundStructure.CreateSimpleCompoundStructure` with every layer made
core (`SetNumberOfShellLayers` 0 on both sides): what core the simple
structure starts with is not documented, so it is set rather than
assumed, and a side split off to stand alone has no shell of its own.
The location line is set to the centreline anyway, so the core only
matters for joins and dimensions to core faces later. Membranes stay
inside their part's type, so in this mode only a side made of nothing
but membranes is left out. A part of one layer is keyed and named as a
one layer type, so it shares types with a normal click. Existing types
are now matched layer by layer whatever their count
(`model.structure_key`), so a second run reuses the part types the
first made; core boundaries, wrapping and function priority are not
compared.

Mutations, failing checks: shells not set (1), the location line offset
ignored (2), a part measured from its last layer (2), Shift never read
(7) or not passed to `read_wall` (7), part types keyed by their first
layer (1), a side of membranes made into a wall (1), only one layer
types matched (1), a part named as a layer (3), an all core wall
accepted (2). One survives and is expected to: a one layer part built
through `CreateSimpleCompoundStructure` gives the same type.

Unverified in Revit: whether `CreateSimpleCompoundStructure` accepts a
membrane as the first or last layer of a structure that is all core.
If Revit refuses, the types transaction fails, nothing changes, and
"The wall types could not be made" names Revit's reason.

## Align Titles (2.52.0)

Asked for as a companion to Align Views: choose one viewport title and
put the titles on other sheets at the same point on the sheet, with the
same title line length if wanted.

### Measured, not computed from the offset

`Viewport.LabelOffset` (Revit 2022) is relative to the viewport box, and
the box grows with grid heads, tags and dimensions past the crop. In the
sheet that prompted this, the title sat left of the crop frame because
the grid heads stuck out to the left. Copying one viewport's offset to
another therefore puts the titles in different places. The script reads
each title's real bottom left from `GetLabelOutline().MinimumPoint`,
moves the offset by the difference, regenerates, and reads it back. The
only assumption is that adding to the offset moves the title the same
way on the sheet. The harness hides a private amount inside each fake
viewport so a script that assumed more fails.

Bottom left, because a title is left aligned: its left edge and baseline
stay put when the view name or the line length changes.

Line lengths are set first, then the document is regenerated, then the
offsets are worked out from fresh outlines, in case a longer line moves
the outline's minimum.

### Match viewport type

A switch in the mode dialog (`CommandSwitchWindow` with `switches`,
which then returns `(option, {switch: bool})`; a closed window is
handled whether it comes back as `None` or `(None, {...})`). When on,
each target gets the source's type through `ChangeTypeId` before
anything is measured, and the document is regenerated, because another
title family moves the title's outline. A target whose old type hides
the title is therefore not skipped. Viewports already of that type are
not touched. The readback checks the type as well as the place.

Mutations: not changing the type, 5 checks fail; checking for a hidden
title before the change, 3; switch always on, 3; no type readback, 1;
changing a type that already matches, 1; no regenerate after the type
change, 1 (only once a Position only case was added: the line step's
regenerate hid it).

### The sheet collection dropdown (2.54.0)

Both Align buttons pick placed views through `lib/avh_viewports/
picker.py`. With sheet collections in the model, `SelectFromList` is
given an `OrderedDict` of groups (`[All sheets]`, each collection by
name, `[Not in a collection]`), `sort_groups="unsorted"` to keep that
order, and `default_group` on all sheets. Without collections, or on a
Revit before 2025 (no `ViewSheet.SheetCollectionId`), it stays the plain
list.

Read in the installed pyRevit 7.1 source rather than assumed: rows that
are already `TemplateListItem`s are used as they are, and a multiselect
returns ticked rows from every group, each row once by identity
(`_get_all_ctx`). So every group is built from the **same** row objects:
a tick under a collection shows under `[All sheets]` and comes back once.
The rows' own `state` is read after the dialog as well, so a pyRevit that
returns only the group on screen still keeps ticks made under another.

`test_align_picker.py` fakes both behaviours. Mutations: fresh rows per
group, 3 checks fail; no dedupe, 2; ignoring the rows' ticks, letting
pyRevit sort the groups, no default group, 1 each.

Ran in Revit 2025 and worked: `SheetCollectionId` read through
`doc.GetElement`, and the dropdown on screen.

### Layout

The Sheets stack was full (three items), so Align Views moved into an
`Align.pulldown` with Align Titles. Restart Revit after updating rather
than reloading pyRevit: the old Align Views button is still on the
ribbon until then. In 2.53.0 the Align pulldown moved out of the stack to
a big button of its own (see Batch Dependents, Layout).

### What the mutations proved

Using the source's corner as the offset: 9 checks fail. Not setting the
line: 7. No regenerate before the readback: 9. Ignoring pinned: 3.
Ignoring the commit status: 1. Ignoring a hidden title: 1.

### Unverified

Position, line length and the viewport type switch ran in Revit 2025
and worked. Not yet seen in Revit: whether a viewport type with Show Title = No
still gives an outline, and whether `VIEWPORT_ATTR_SHOW_LABEL` is the
Show Title parameter with 0 for No.
Each of these shows up as a skipped or "did not land" row rather than
a false success.

## Batch Dependents (2.53.0)

Asked for as a companion to Batch Sheets: many dependent views at once,
named the way Batch Sheets and Rename Views name things, prefix + field +
suffix, checked live in the window.

### Names

The model is `lib/avh_dependents/model.py`, built on `avh_rename.model`
(compose, problem, FORBIDDEN) and `avh_sheets.model.fields` (count from a
start value, or keep what was typed). With several parents one prefix
cannot be "the parent's name", so the prefix and suffix may hold
`{parent}`, replaced per parent. Braces are characters Revit refuses in a
name, so a mistyped token is shown in red and never reaches the model.
The fields restart at the start value for each parent. Names are unique
per view type, the same assumption Rename Views makes; a dependent has its
parent's view type.

### Which views

`View.CanViewBeDuplicated(ViewDuplicateOption.AsDependent)`, not a
template, and `GetPrimaryViewId()` invalid. The last is there because
whether Revit answers no for a view that is already a dependent is not
confirmed; the harness's fake answers yes so the script's own check is
what keeps it out. Selected views that do not qualify are named in the
report; a selection with none that qualify falls back to the tick list.

Scope boxes per dependent were offered in the design and left out at
Björn's choice.

### Writing

One transaction: `Duplicate(AsDependent)`, `doc.GetElement`, set `Name`,
then every name and `GetPrimaryViewId()` read back. Any miss, refusal or
a commit that does not come back Committed rolls everything back.

### What the mutations proved

The harness runs the script again with each safety line taken out (in
memory) and needs at least one check to fail; `-v` prints how many.
Name readback skipped: 1. Parent readback skipped: 1. Commit status
ignored: 1. No roll back on a refusal: 3. Pre-check skipped: 2.
Dependents of dependents let through: 1 (it survived until the fake's
`CanViewBeDuplicated` stopped refusing dependents itself).
`CanViewBeDuplicated` ignored: 3. Taken names not passed: 2. Selection
ignored: 15.

### Layout

At Björn's request the Sheets stack is Batch Sheets, Rename Views and
Batch Dependents, and `Align.pulldown` (Align Views, Align Titles) moved
out of the stack to the panel itself, where it shows as a big dropdown
button with its 96 px icon. Restart Revit after updating rather than
reloading pyRevit: Revit cannot remove ribbon items, and the stack is
added in one call that fails while a name in it is still on the ribbon.

### Unverified

Tested by Björn in Revit 2025 before release; which cases were tried was
not recorded. Still unconfirmed: what `CanViewBeDuplicated` answers for a
dependent, per view type name uniqueness, and the roll back on a refusal
or a readback miss, which only the harness has exercised.

## Renumber (2.55.0)

One instance parameter, many elements, prefix + running number +
suffix. The counting is Batch Sheets' (1, 01, 101 or a letter) with a
step added, written again in `lib/avh_renumber/model.py` rather than
reused, because `avh_rename.model.numbers` has no step.

### Keeping a format: #QQC001 to #QQC999

Björn's case: values like `#QQC001` up to `#QQC999`. A start with a
leading zero pins the width, so `001` with 1000 elements is refused in
the window ("only 999 fit") instead of producing `#QQC1000`, which
breaks the format and sorts before `#QQC101` as text. "1" and "101" have
no pinned width and grow as before. The prefix, width and suffix are
also suggested from the values the elements already hold (the most
common prefix + digits + suffix shape; zero padded only if some value
is), so an existing format is kept without retyping it.

### Only elements with no value

A tick box in the window. Elements that already hold a value keep it
(their row shows unchanged and is not written) and only the empty ones
are numbered, in the chosen order, skipping every value the others hold
so no number is given twice. Starting at `001` therefore fills the gaps
first and then carries on after the highest. Kept elements still sit in
the order, so they still decide where the empty ones fall in it.

Empty means no text, only spaces, or `HasValue` false. The last matters
for a whole number parameter that was never set: it reads 0 through
`AsInteger()`, and a 0 that someone typed is a value and is kept.

What is skipped is what the chosen elements hold, not the whole model:
a `#QQC005` on an element left out of the selection is not seen, and
Revit's duplicate Mark warning in the report is where that shows up.

### Which elements, in which order

The selection if there is one, otherwise picked one at a time with
`PickObject` until Esc. Revit's selection has no reliable order, so a
selection is numbered by position (or value, or id) and only picked
elements offer "as picked". Picking an element twice is ignored, and
each pick is added to the selection so the picks so far stay visible.

Positions come from `Location.Point`, the middle of `Location.Curve`,
`GetBoxCenter()` (viewports), or the bounding box, in that order, and are
rounded to 10 mm before sorting, so a row of doors drawn a few
millimetres out of line still numbers along the row. Elements with no
position go last, in the order they were picked.

### Which parameters

Offered: text and whole number instance parameters that every chosen
element has, editable on all of them, with the same storage. Yes/No is
stored as an Integer and is left out through `GetDataType()`. A whole
number parameter takes no prefix, suffix or letters; the window refuses
that before Write is pressed.

### Writing

Only values that change are written. One transaction, every `Set`
result checked and every value read back; any refusal, mismatch or
rolled back commit and nothing is written. A failure preprocessor
deletes warnings (they are listed in the report instead: two doors on
one Mark is a warning Revit shows after the commit, and the person
asked for exactly that) and rolls back on an error, so no Revit dialog
appears mid-run.

Grouped elements are left out unless the parameter varies across group
instances, for the same reason as Flip Status: Revit would offer to
ungroup. Elements checked out by another user are left out. Both are
listed after the write and kept out of the numbering, so the numbers
have no gaps.

### Known limit

A parameter Revit insists is unique, such as Sheet Number or a
viewport's Detail Number, fails when renumbering swaps two values
(1 and 2 become 2 and 1): the first write lands on a value the second
element still holds. The whole run rolls back and the dialog names the
element. A two pass write through temporary values would fix it; not
done until someone needs it.

### What the mutations proved

`test_renumber_harness.py` runs the real script on a fake Revit whose
values persist, whose transactions roll back, and which posts a warning
for a duplicated Mark at commit. Seventeen mutations, each caught: commit
status ignored, read back skipped, a `Set` refusal ignored, no roll back
on an exception, the guard not attached, warnings not deleted, unchanged
values written, the group and owner checks removed, Yes/No and read only
parameters offered, element types kept, the selection ignored, a second
pick kept, the existing format not suggested, "only empty" not passed
from the window to the plan, and `HasValue` ignored. The width rule lives in
the model and is checked there directly.

### Unverified

Tried by Björn in Revit 2025 before release; which cases were tried was
not recorded. Still unconfirmed: `PickObject` in a loop and its Esc, keeping the
picks selected, `DeleteWarning` in the preprocessor,
`Definition.VariesAcrossGroups`, `GetDataType()` against
`SpecTypeId.Boolean.YesNo`, `GetBoxCenter()`, `Parameter.HasValue`, and
the window's order ComboBox and "only empty" CheckBox (the rest of the window is built like Batch Dependents', which
has run).

## Update AVH: measured, not trusted

The button reuses pyRevit's own updater (`updater.update_repo`, which is
`git_pull` on LibGit2Sharp) on this one repository, so the pull path is
the one `pyrevit extend ui` and pyRevit's Update button already use.

`git_pull` discards LibGit2Sharp's `MergeResult`, so a pull that merged
nothing looks the same as one that worked. `lib/avh_update` therefore
decides the outcome from the head commit before and after and from how
far the branch is still behind its remote after the pull's fetch. Still
behind is reported as "did not go through", never as "up to date".

A clone with commits that are not on GitHub is refused before any pull.
On a user's machine that cannot happen; on the development clone a pull
would create a merge commit signed with the Revit user's name. Use git
there.

It reloads only when the head moved. A reload cannot remove ribbon items,
so after a release that moves buttons between panels the dialog says to
restart Revit (see the 2.42.0 stack failure above).

test_update_harness.py drives the library against real temporary git
repositories (behind, ahead, hand edited, not a clone, no upstream) with
the git command line standing in for LibGit2Sharp, then runs the button
script against a mocked pyrevit. Mutations caught: ignoring "still
behind" (1 check), dropping the local commit refusal (4), reloading
always (4), not re-measuring the head after a failed pull (1), not
disposing repositories (1).

Unverified in Revit: `Repository.Discover`, `compare_branch_heads` on a
clone made by `pyrevit extend ui`, and `update_repo` called directly.

## Working on this

Two rules earned the hard way.

**Probe the environment before building on it.** Four failures were all
runtime environment assumptions, not logic errors: an engine that will not
start, a UI module that will not import, a serialiser disabled by default.
None were visible from reading code. (A Diagnostics button did this
until 2.38.0; it is in the git history.)

**Test the script layer, not just the library.** Every failure happened in
the thin pushbutton scripts, which had no tests for three releases while
the library underneath was thoroughly covered.

And the standing constraint: **this runs on IronPython 2.7.** No f-strings,
no `open(encoding=)`, no `str()` on Revit data, nothing needing Python 3.

**A markdown table goes out in one `print_md` call.** pyRevit renders each
call as a document of its own, so a table printed a line at a time comes
out as one table per row and the columns do not line up (found in Pipe
Openings, fixed in Close Link Datums, Datums to Workset, ImportSchedule,
Flip Status, Place at Coordinate and Point Cloud to Toposolid in 2.47.3).
Cells go through `table_cell`, which turns `|` and line breaks into
something harmless and an empty cell into `-`; each harness checks the
table arrives whole with the header's column count (`table_problems`).
Every column is `:---`. pyRevit's markdown puts `align="right"` on both
the header and the cells of a `---:` column, but the output window
right aligns only the header, so it drifts off its values, worse the
wider the window (seen on Pipe Openings 2.47.1). The harness check
refuses any other rule.

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
