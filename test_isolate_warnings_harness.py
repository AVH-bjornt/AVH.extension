# -*- coding: utf-8 -*-
"""
Runs the real Isolate Warnings script against a mocked Revit.

The fake view is behavioural: isolating sets the temporary mode, so the
toggle genuinely toggles, and a rollback puts the mode back to what it
was when the transaction started. Without that the second half of a one
button on/off tool cannot be tested at all.

Run outside Revit:

    python test_isolate_warnings_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Isolate Warnings.pushbutton", "script.py")

from avh_warnings import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

JOINED = u"Highlighted elements are joined but do not intersect"
ROOM = u"Room is not in a properly enclosed region"
IDENTICAL = u"There are identical instances in the same place"
LONG_A = u"A warning whose text runs on well past the label limit, " \
         u"describing in detail the first situation Revit disliked"
LONG_B = u"A warning whose text runs on well past the label limit, " \
         u"describing in detail the second situation Revit disliked"


class FakeId(object):
    def __init__(self, value):
        self.Value = value
        self.IntegerValue = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "<Id {0}>".format(self.Value)


def ids(*values):
    return [FakeId(value) for value in values]


class FakeFailure(object):
    def __init__(self, description, failing, raises_description=False,
                 raises_elements=False):
        self.description = description
        self.failing = list(failing)
        self.raises_description = raises_description
        self.raises_elements = raises_elements

    def GetDescriptionText(self):
        if self.raises_description:
            raise Exception("description unavailable")
        return self.description

    def GetFailingElements(self):
        if self.raises_elements:
            raise Exception("failing elements unavailable")
        return list(self.failing)


class FakeView(object):
    def __init__(self, name=u"Level 1", usable=True, isolated=False,
                 in_view=(), isolate_raises=False):
        self.Id = FakeId(1)
        self.Name = name
        self.usable = usable
        self.isolated = isolated
        self.isolated_ids = []
        self.in_view = list(in_view)
        self.isolate_raises = isolate_raises
        self.disabled_modes = []

    def CanUseTemporaryVisibilityModes(self):
        return self.usable

    def IsTemporaryHideIsolateActive(self):
        return self.isolated

    def IsolateElementsTemporary(self, collection):
        if self.isolate_raises:
            raise Exception("the view refused the isolate")
        self.isolated_ids = list(collection)
        self.isolated = True

    def DisableTemporaryViewMode(self, mode):
        self.disabled_modes.append(mode)
        self.isolated = False
        self.isolated_ids = []

    def snapshot(self):
        return (self.isolated, list(self.isolated_ids),
                list(self.disabled_modes))

    def restore(self, state):
        self.isolated, isolated_ids, disabled = state
        self.isolated_ids = list(isolated_ids)
        self.disabled_modes = list(disabled)


class FakeDocument(object):
    def __init__(self, warnings=(), view=None, missing=()):
        self.warnings = list(warnings)
        self.ActiveView = view if view is not None else FakeView()
        self.missing = set(FakeId(value).Value for value in missing)

    def GetWarnings(self):
        return list(self.warnings)

    def GetElement(self, element_id):
        if element_id.Value in self.missing:
            return None
        return object()

    def snapshot(self):
        return self.ActiveView.snapshot()

    def restore(self, state):
        self.ActiveView.restore(state)


class FakeTransaction(object):
    committed = []
    rolled_back = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = self.doc.snapshot()

    def Commit(self):
        if FakeTransaction.commit_status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeCollector(object):
    """FilteredElementCollector(doc, viewId): what that view can show."""

    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.view_id = view_id

    def ToElementIds(self):
        if self.view_id is None:
            return []
        return list(self.doc.ActiveView.in_view)


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeDotNetList(object):
    """Stands in for System.Collections.Generic.List[ElementId]."""

    def __init__(self):
        self.items = []

    def Add(self, item):
        self.items.append(item)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class ListFactory(object):
    def __getitem__(self, element_type):
        return FakeDotNetList


def install_dotnet():
    system = types.ModuleType("System")
    collections = types.ModuleType("System.Collections")
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = ListFactory()
    collections.Generic = generic
    system.Collections = collections
    sys.modules["System"] = system
    sys.modules["System.Collections"] = collections
    sys.modules["System.Collections.Generic"] = generic


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.picker_calls = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, shift=False, shift_via_global=False, picked=None,
               picker_raises=False, commit_status="Committed"):
    """Execute the real script against the fake Revit."""
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.commit_status = commit_status

    recorder = Recorder()
    install_dotnet()

    def show(items, **kwargs):
        recorder.picker_calls.append(list(items))
        if picker_raises:
            raise Exception("the picker is unavailable")
        return picked

    db = Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        ElementId=FakeId,
        FilteredElementCollector=FakeCollector,
        TemporaryViewMode=Namespace(
            TemporaryHideIsolate="TemporaryHideIsolate"),
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(ActiveView=None))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=show))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    # The shift click is read from EXEC_PARAMS where pyRevit offers it,
    # and from the injected global where it does not. Both are real.
    if not shift_via_global:
        pyrevit.EXEC_PARAMS = Namespace(config_mode=shift)

    sys.modules["pyrevit"] = pyrevit

    init = {"__shiftclick__": True} if shift_via_global else {}
    try:
        runpy.run_path(SCRIPT, run_name="__main__", init_globals=init)
    finally:
        sys.modules.pop("pyrevit", None)

    return recorder


def isolated_values(view):
    return sorted(element_id.Value for element_id in view.isolated_ids)


# --------------------------------------------------------------------------
# Grouping and labels, no Revit at all
# --------------------------------------------------------------------------

groups = model.merge_by_description([
    (ROOM, ids(5)),
    (JOINED, ids(1, 2)),
    (JOINED, ids(2, 3)),
])
check("group: descriptions merged", len(groups) == 2, str(len(groups)))
check("group: noisiest kind first", groups[0][0] == JOINED)
check("group: the same element twice in one kind counts once",
      len(groups[0][1]) == 3,
      str([element_id.Value for element_id in groups[0][1]]))

tied = model.merge_by_description([(IDENTICAL, ids(9)), (ROOM, ids(8))])
check("group: equal counts fall back to the description",
      [description for description, _ in tied] == sorted([IDENTICAL, ROOM]))

check("group: an element in two kinds is isolated once",
      sorted(element_id.Value for element_id in model.all_ids(
          model.merge_by_description([(JOINED, ids(1, 2)),
                                      (ROOM, ids(2, 7))]))) == [1, 2, 7])

check("label: short text is left alone",
      model.truncate(u"short") == u"short")
check("label: long text is cut and marked",
      len(model.truncate(LONG_A)) == model.MAX_LABEL and
      model.truncate(LONG_A).endswith(u"…"))
check("label: Icelandic text survives truncation",
      model.truncate(u"Rými " * 40).startswith(u"Rými"))

labels, mapping = model.picker_labels(
    model.merge_by_description([(JOINED, ids(1, 2)), (ROOM, ids(5))]))
check("picker: counts are on the labels",
      any(u"(2)" in label for label in labels), u" | ".join(labels))
check("picker: every label maps back to its full description",
      set(mapping.values()) == set([JOINED, ROOM]))

# Two long descriptions that truncate to the same text must not collapse
# onto one label, or picking one silently isolates the other.
collide = model.merge_by_description([(LONG_A, ids(1)), (LONG_B, ids(2))])
labels, mapping = model.picker_labels(collide)
check("picker: descriptions that truncate alike keep separate labels",
      len(set(labels)) == 2, u" | ".join(labels))
check("picker: and each still maps to its own description",
      len(set(mapping.values())) == 2)
check("picker: selecting one label returns only its ids",
      [element_id.Value for element_id in
       model.ids_for(collide, [mapping[labels[0]]])] ==
      [collide[0][1][0].Value])


# --------------------------------------------------------------------------
# 1. The ordinary click
# --------------------------------------------------------------------------

view = FakeView(in_view=ids(1, 2))
doc = FakeDocument(view=view, warnings=[
    FakeFailure(JOINED, ids(1, 2)),
    FakeFailure(JOINED, ids(2, 3)),
    FakeFailure(ROOM, ids(7)),
])
recorder = run_script(doc)

check("click: everything with a warning is isolated",
      isolated_values(view) == [1, 2, 3, 7], str(isolated_values(view)))
check("click: the view is now in temporary isolate", view.isolated)
check("click: committed once", len(FakeTransaction.committed) == 1)
check("click: no picker on a plain click", not recorder.picker_calls)
check("click: the report counts the kinds",
      u"2 warning kind(s)" in recorder.text(), recorder.text()[:160])
check("click: the noisy kind is reported with its count",
      u"**3**" in recorder.text())
check("click: says how many are in this view",
      u"2 of them are in this view" in recorder.text())
check("click: no alert when some are visible", not recorder.alerts,
      u" | ".join(recorder.alerts))


# --------------------------------------------------------------------------
# 2. The same button, off again
# --------------------------------------------------------------------------

view = FakeView(isolated=True)
view.isolated_ids = ids(1, 2)
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1))])
recorder = run_script(doc)

check("toggle off: the temporary mode is cleared",
      view.disabled_modes == ["TemporaryHideIsolate"],
      str(view.disabled_modes))
check("toggle off: nothing is isolated instead", not view.isolated)
check("toggle off: committed", len(FakeTransaction.committed) == 1)
check("toggle off: said so quietly, no dialog", not recorder.alerts)
check("toggle off: the warnings were never gathered",
      u"warning kind" not in recorder.text())


# --------------------------------------------------------------------------
# 3. Shift click, both ways of learning about it
# --------------------------------------------------------------------------

view = FakeView(in_view=ids(7))
doc = FakeDocument(view=view, warnings=[
    FakeFailure(JOINED, ids(1, 2, 3)),
    FakeFailure(ROOM, ids(7)),
])
labels, mapping = model.picker_labels(model.merge_by_description([
    (JOINED, ids(1, 2, 3)), (ROOM, ids(7))]))
room_label = [label for label in labels if mapping[label] == ROOM][0]
recorder = run_script(doc, shift=True, picked=[room_label])

check("shift: the picker was shown", len(recorder.picker_calls) == 1)
check("shift: every kind was offered",
      len(recorder.picker_calls[0]) == 2 if recorder.picker_calls else False)
check("shift: only the picked kind is isolated",
      isolated_values(view) == [7], str(isolated_values(view)))
check("shift: committed", len(FakeTransaction.committed) == 1)

# The older route: no EXEC_PARAMS, __shiftclick__ injected as a global.
view = FakeView(in_view=ids(7))
doc = FakeDocument(view=view, warnings=[
    FakeFailure(JOINED, ids(1, 2, 3)),
    FakeFailure(ROOM, ids(7)),
])
recorder = run_script(doc, shift_via_global=True, picked=[room_label])
check("shift via the injected global: picker still shown",
      len(recorder.picker_calls) == 1)
check("shift via the injected global: only the picked kind isolated",
      isolated_values(view) == [7], str(isolated_values(view)))

# Cancelling the picker must change nothing.
view = FakeView()
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1))])
recorder = run_script(doc, shift=True, picked=None)
check("shift cancelled: nothing isolated", not view.isolated)
check("shift cancelled: nothing committed", not FakeTransaction.committed)

# A broken picker falls back to everything, because this tool only
# changes what is visible. Remove Level does the opposite on purpose.
view = FakeView(in_view=ids(1))
doc = FakeDocument(view=view, warnings=[
    FakeFailure(JOINED, ids(1, 2)), FakeFailure(ROOM, ids(7))])
recorder = run_script(doc, shift=True, picker_raises=True)
check("picker broken: everything isolated rather than nothing",
      isolated_values(view) == [1, 2, 7], str(isolated_values(view)))
check("picker broken: the fallback is admitted in the report",
      u"picker was unavailable" in recorder.text())


# --------------------------------------------------------------------------
# 4. Nothing to do, and views that cannot do it
# --------------------------------------------------------------------------

view = FakeView()
doc = FakeDocument(view=view, warnings=[])
recorder = run_script(doc)
check("no warnings: said so", u"no warnings" in recorder.text().lower())
check("no warnings: nothing committed", not FakeTransaction.committed)

view = FakeView(usable=False)
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1))])
recorder = run_script(doc)
check("schedule or sheet: refused with a useful message",
      u"cannot be temporarily isolated" in recorder.text())
check("schedule or sheet: nothing committed", not FakeTransaction.committed)

# Every id stale. Reporting "isolated 0 elements" would look like success.
view = FakeView()
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1, 2))],
                   missing=(1, 2))
recorder = run_script(doc)
check("all ids stale: nothing isolated", not view.isolated)
check("all ids stale: said so rather than claiming success",
      u"no elements that still exist" in recorder.text())
check("all ids stale: nothing committed", not FakeTransaction.committed)

# One stale id among good ones is dropped, not fatal.
view = FakeView(in_view=ids(2))
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1, 2))],
                   missing=(1,))
recorder = run_script(doc)
check("one stale id: dropped, the rest isolated",
      isolated_values(view) == [2], str(isolated_values(view)))


# --------------------------------------------------------------------------
# 5. Warnings the API will not talk about
# --------------------------------------------------------------------------

view = FakeView(in_view=ids(7))
doc = FakeDocument(view=view, warnings=[
    FakeFailure(JOINED, ids(1), raises_description=True),
    FakeFailure(IDENTICAL, ids(2), raises_elements=True),
    FakeFailure(ROOM, ids(7)),
])
recorder = run_script(doc)
check("unreadable warning: skipped, the rest still isolated",
      isolated_values(view) == [7], str(isolated_values(view)))
check("unreadable warning: committed", len(FakeTransaction.committed) == 1)


# --------------------------------------------------------------------------
# 6. Isolated, but not one of them is in this view
# --------------------------------------------------------------------------

view = FakeView(in_view=[])
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1, 2))])
recorder = run_script(doc)
check("none in view: still isolated", isolated_values(view) == [1, 2])
check("none in view: the empty view is explained",
      any(u"none of them are in this view" in alert
          for alert in recorder.alerts), u" | ".join(recorder.alerts))
check("none in view: a 3D view is suggested",
      u"3D view" in recorder.text())


# --------------------------------------------------------------------------
# 7. Revit refusing the write
# --------------------------------------------------------------------------

view = FakeView(in_view=ids(1))
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1, 2))])
recorder = run_script(doc, commit_status="RolledBack")
check("commit rejected: user told nothing changed",
      u"nothing changed" in recorder.text().lower(),
      u" | ".join(recorder.alerts))
check("commit rejected: not reported as isolated",
      u"Isolated" not in recorder.text())
check("commit rejected: the view is back as it was", not view.isolated)

view = FakeView(isolate_raises=True)
doc = FakeDocument(view=view, warnings=[FakeFailure(JOINED, ids(1, 2))])
recorder = run_script(doc)
check("isolate raises: rolled back", len(FakeTransaction.rolled_back) == 1)
check("isolate raises: nothing committed", not FakeTransaction.committed)
check("isolate raises: Revit's own words reach the user",
      u"refused the isolate" in recorder.text())

view = FakeView(isolated=True)
doc = FakeDocument(view=view, warnings=[])


class RefusingView(FakeView):
    def DisableTemporaryViewMode(self, mode):
        raise Exception("the mode could not be disabled")


view = RefusingView(isolated=True)
doc = FakeDocument(view=view, warnings=[])
recorder = run_script(doc)
check("clearing raises: reported rather than swallowed",
      u"could not be cleared" in recorder.text())
check("clearing raises: nothing committed", not FakeTransaction.committed)


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
