# -*- coding: utf-8 -*-
"""
Runs the real Datums to Workset script against a mocked Revit.

The interesting cases are all refusals. A grid moves without argument; a
view may not be movable at all, an element somebody else has checked out
must not even be attempted, and a parameter that returns False from Set
must not be counted as moved. Each one is a scenario, and each asserts
both that nothing was written and that the report says why.

Run outside Revit:

    python test_datums_workset_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Worksets.panel",
                      "Datums to Workset.pushbutton", "script.py")

from avh_worksets import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

_next_id = [500]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


class FakeId(object):
    def __init__(self, value=None):
        self.Value = value if value is not None else next_id()
        self.IntegerValue = self.Value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


class FakeWorksetParameter(object):
    def __init__(self, value, read_only=False, refuses=False, raises=False):
        self.value = value
        self.IsReadOnly = read_only
        self.refuses = refuses
        self.raises = raises
        self.writes = 0

    def AsInteger(self):
        return self.value

    def Set(self, value):
        if self.raises:
            raise Exception("this element cannot be moved")
        if self.refuses:
            return False
        self.value = value
        self.writes += 1
        return True


class FakeElement(object):
    """A grid or a level. Both behave the same for this tool."""

    def __init__(self, name, workset=1, read_only=False, refuses=False,
                 raises=False, no_parameter=False, owned=False):
        self.Id = FakeId()
        self.Name = name
        self.owned = owned
        self.parameter = None if no_parameter else FakeWorksetParameter(
            workset, read_only, refuses, raises)

    def get_Parameter(self, built_in):
        if built_in == "ELEM_PARTITION_PARAM":
            return self.parameter
        return None

    def workset(self):
        return self.parameter.value if self.parameter else None


class FakeGrid(FakeElement):
    pass


class FakeLevel(FakeElement):
    pass


class FakeView(FakeElement):
    def __init__(self, name, workset=1, view_type="FloorPlan",
                 is_template=False, **kwargs):
        FakeElement.__init__(self, name, workset, **kwargs)
        self.ViewType = view_type
        self.IsTemplate = is_template


class FakeSheet(FakeView):
    pass


class FakeSchedule(FakeView):
    pass


class FakeWorkset(object):
    def __init__(self, name, value):
        self.Name = name
        self.Id = FakeId(value)


class FakeDocument(object):
    def __init__(self, elements=(), worksets=(), workshared=True):
        self.elements = list(elements)
        self.worksets = list(worksets)
        self.IsWorkshared = workshared
        self.owned_ids = set(element.Id for element in self.elements
                             if getattr(element, "owned", False))


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.elements = list(doc.elements)

    def OfClass(self, cls):
        self.elements = [element for element in self.elements
                         if isinstance(element, cls)]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        return iter(self.elements)


class FakeWorksetCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfKind(self, kind):
        return self

    def __iter__(self):
        return iter(self.doc.worksets)


class FakeTransaction(object):
    committed = []
    rolled_back = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = [(element, element.workset())
                      for element in self.doc.elements
                      if element.parameter is not None]

    def Commit(self):
        if FakeTransaction.commit_status != "Committed":
            for element, value in self.state:
                element.parameter.value = value
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        for element, value in self.state:
            element.parameter.value = value
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.pickers = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, picked=None, commit_status="Committed"):
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.commit_status = commit_status

    recorder = Recorder()

    def show(items, **kwargs):
        recorder.pickers.append(list(items))
        return picked

    def checkout_status(document, element_id):
        return ("OwnedByOtherUser" if element_id in document.owned_ids
                else "NotOwned")

    db = Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        FilteredElementCollector=FakeCollector,
        FilteredWorksetCollector=FakeWorksetCollector,
        WorksetKind=Namespace(UserWorkset="UserWorkset"),
        Grid=FakeGrid,
        Level=FakeLevel,
        View=FakeView,
        ViewSheet=FakeSheet,
        ViewSchedule=FakeSchedule,
        ViewType=Namespace(FloorPlan="FloorPlan", Section="Section",
                           ThreeD="ThreeD", Legend="Legend",
                           Internal="Internal",
                           ProjectBrowser="ProjectBrowser",
                           SystemBrowser="SystemBrowser",
                           DrawingSheet="DrawingSheet",
                           Schedule="Schedule", Undefined="Undefined"),
        BuiltInParameter=Namespace(
            ELEM_PARTITION_PARAM="ELEM_PARTITION_PARAM"),
        WorksharingUtils=Namespace(GetCheckoutStatus=checkout_status),
        CheckoutStatus=Namespace(OwnedByOtherUser="OwnedByOtherUser",
                                 NotOwned="NotOwned"),
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(ActiveView=None))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=show))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md,
                                     linkify=lambda i: u"[id]"),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit

    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)

    return recorder


TARGET = FakeWorkset(model.CONVENTIONAL, 42)
OTHER = FakeWorkset(u"Workset1", 1)
TARGET_LABEL = model.CONVENTIONAL + u"  (the usual one)"


# --------------------------------------------------------------------------
# The plain data, no Revit
# --------------------------------------------------------------------------

labels, mapping = model.workset_labels([u"Workset1", model.CONVENTIONAL,
                                        u"Shared Levels and Grids"])
check("picker: the usual workset is offered first",
      labels[0].startswith(model.CONVENTIONAL), u" | ".join(labels))
check("picker: and it is marked as the usual one",
      u"the usual one" in labels[0])
check("picker: every label maps back to a plain name",
      set(mapping.values()) == set([u"Workset1", model.CONVENTIONAL,
                                    u"Shared Levels and Grids"]))
check("picker: a model without the usual name still lists everything",
      len(model.workset_labels([u"A", u"B"])[0]) == 2)

check("move: an element elsewhere needs moving", model.needs_move(1, 42))
check("move: an element already there does not",
      not model.needs_move(42, 42))
check("move: an unreadable workset counts as needing it",
      model.needs_move(None, 42))

tally = model.Tally()
tally.count_moved(model.GRIDS)
tally.count_moved(model.GRIDS)
tally.count_already(model.LEVELS)
tally.count_skipped(model.LEVELS, model.READ_ONLY, u"E01")
tally.count_skipped(model.LEVELS, model.READ_ONLY, u"E02")
check("tally: counted per kind",
      tally.rows()[0] == (u"Grids", 2, 0, 0), str(tally.rows()))
check("tally: totals add up",
      tally.total_moved() == 2 and tally.total_already() == 1 and
      tally.total_skipped() == 2)
check("tally: one reason with a count, not two lines",
      tally.reasons() == [(u"Levels", model.READ_ONLY, 2)],
      str(tally.reasons()))


# --------------------------------------------------------------------------
# 1. The ordinary run
# --------------------------------------------------------------------------

grid = FakeGrid(u"A", workset=1)
level = FakeLevel(u"E01", workset=1)
doc = FakeDocument([grid, level], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)

check("run: the grid moved", grid.workset() == 42)
check("run: the level moved", level.workset() == 42)
check("run: committed once", len(FakeTransaction.committed) == 1)
check("run: the report names the target workset",
      model.CONVENTIONAL in recorder.text())
check("run: two moved", u"**2 moved**" in recorder.text(),
      recorder.text()[-300:])
check("run: no alert when nothing refused", not recorder.alerts,
      u" | ".join(recorder.alerts))

# Cancelling the picker changes nothing.
grid = FakeGrid(u"A", workset=1)
doc = FakeDocument([grid], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=None)
check("cancelled: nothing moved", grid.workset() == 1)
check("cancelled: nothing committed", not FakeTransaction.committed)


# --------------------------------------------------------------------------
# 2. Nothing needless is written
# --------------------------------------------------------------------------

already = FakeGrid(u"A", workset=42)
moving = FakeGrid(u"B", workset=1)
doc = FakeDocument([already, moving], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)

check("already there: not written again",
      already.parameter.writes == 0, str(already.parameter.writes))
check("already there: counted separately",
      u"**1 moved**, 1 already there" in recorder.text(),
      recorder.text()[-300:])


# --------------------------------------------------------------------------
# 3. Views are not touched at all
# --------------------------------------------------------------------------

# 2.18.0 moved graphical views too, and Revit refused every one on
# a live project. They are out entirely now, so the check is that a model
# full of them comes away untouched rather than reporting failures.
grid = FakeGrid(u"A", workset=1)
plan = FakeView(u"Level 1", workset=1)
legend = FakeView(u"Legend", workset=1, view_type="Legend")
sheet = FakeSheet(u"A101", workset=1, view_type="DrawingSheet")
schedule = FakeSchedule(u"Door schedule", workset=1, view_type="Schedule")
doc = FakeDocument([grid, plan, legend, sheet, schedule],
                   worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)

check("views: a plan is left where it is", plan.workset() == 1)
check("views: a legend is left where it is", legend.workset() == 1)
check("views: a sheet is left where it is", sheet.workset() == 1)
check("views: a schedule is left where it is", schedule.workset() == 1)
check("views: not written to at all",
      plan.parameter.writes == 0 and legend.parameter.writes == 0)
# Not just "Views" anywhere: the workset is called Shared Views, Levels,
# Grids, so the plain substring matches the target name and the check
# would have failed for the wrong reason.
check("views: no Views row in the table",
      u"| Views |" not in recorder.text(), recorder.text()[:400])
check("views: and no reason listed against them",
      u"Views:" not in recorder.text())
check("views: the grid still moved", grid.workset() == 42)
check("views: one moved, and only one", u"**1 moved**" in recorder.text(),
      recorder.text()[-300:])


# --------------------------------------------------------------------------
# 4. Everything that can refuse
# --------------------------------------------------------------------------

read_only = FakeLevel(u"E01", workset=1, read_only=True)
doc = FakeDocument([read_only], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("read only: not moved", read_only.workset() == 1)
check("read only: reported with the reason",
      model.READ_ONLY in recorder.text())
check("read only: and the element named",
      u"E01" in recorder.text())
check("read only: the user is told, not left to notice",
      any(u"could not be" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))

refusing = FakeGrid(u"A", workset=1, refuses=True)
doc = FakeDocument([refusing], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("Set returning false: not counted as moved",
      u"**0 moved**" in recorder.text(), recorder.text()[-300:])
check("Set returning false: reported as refused",
      model.REFUSED in recorder.text())

raising = FakeGrid(u"A", workset=1, raises=True)
doc = FakeDocument([raising], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("Set raising: the run still finishes",
      len(FakeTransaction.committed) == 1)
check("Set raising: reported rather than lost",
      model.REFUSED in recorder.text())

owned = FakeGrid(u"A", workset=1, owned=True)
mine = FakeGrid(u"B", workset=1)
doc = FakeDocument([owned, mine], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("checked out by someone else: never even attempted",
      owned.parameter.writes == 0 and owned.workset() == 1)
check("checked out by someone else: named in the report",
      model.OWNED in recorder.text())
check("checked out by someone else: the rest still move",
      mine.workset() == 42)

no_param = FakeGrid(u"A", no_parameter=True)
doc = FakeDocument([no_param], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("no workset parameter: reported",
      model.NO_PARAMETER in recorder.text())


# --------------------------------------------------------------------------
# 5. Models this cannot run on
# --------------------------------------------------------------------------

doc = FakeDocument([FakeGrid(u"A")], worksets=[TARGET], workshared=False)
recorder = run_script(doc, picked=TARGET_LABEL)
check("not workshared: refused with a plain message",
      u"not workshared" in recorder.text())
check("not workshared: nothing committed", not FakeTransaction.committed)

doc = FakeDocument([FakeGrid(u"A")], worksets=[])
recorder = run_script(doc, picked=TARGET_LABEL)
check("no user worksets: said so", u"no user worksets" in recorder.text())

doc = FakeDocument([], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL)
check("nothing to move: said so",
      u"No grids or levels" in recorder.text())

grid = FakeGrid(u"A", workset=1)
doc = FakeDocument([grid], worksets=[OTHER, TARGET])
recorder = run_script(doc, picked=TARGET_LABEL, commit_status="RolledBack")
check("commit rejected: the workset is back as it was", grid.workset() == 1)
check("commit rejected: user told nothing moved",
      u"nothing was moved" in recorder.text().lower())
check("commit rejected: no report claiming moves",
      u"moved**" not in recorder.text())


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
