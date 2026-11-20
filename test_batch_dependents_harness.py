# -*- coding: utf-8 -*-
"""
Runs the real Batch Dependents script against a mocked Revit.

The window (lib/avh_dependents/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person
would. Everything the window shows comes from the `evaluate` callable
the script passes it, and that is exercised here.

The fake lies the way Revit is expected to:

- `View.Duplicate(AsDependent)` makes a view named by Revit's own
  choosing, refused for a template, a dependent, or a view marked so.
- Setting a name another view of the same view type has raises.
- Forbidden characters raise.
- A rolled back transaction, or a commit that comes back RolledBack,
  removes every view made in it.

At the end the script is run again with its safety lines taken out one
at a time (in memory, nothing written), and each such mutation has to
make at least one check fail; a mutation that survives means the
harness does not guard that line.

Run outside Revit:

    python test_batch_dependents_harness.py      (-v: how many checks
                                                  each mutation fails)
"""

import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Views.panel", "Sheets.stack",
                      "Batch.pulldown", "Batch Dependents.pushbutton",
                      "script.py")
SOURCE = io.open(SCRIPT, encoding="utf-8").read()

import avh_dependents                   # noqa: E402
from avh_dependents import model        # noqa: E402
from avh_rename import model as naming  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "Id({0})".format(self.Value)


INVALID = FakeId(-1)
AS_DEPENDENT = "AsDependent"


class FakeView(object):
    def __init__(self, doc, name, view_type="FloorPlan", template=False,
                 primary=None, duplicable=True):
        self.doc = doc
        doc.next_id += 1
        self.Id = FakeId(doc.next_id)
        self._name = name
        self.ViewType = view_type
        self.IsTemplate = template
        self.primary = primary.Id if primary is not None else INVALID
        self.duplicable = duplicable
        doc.views.append(self)

    def GetPrimaryViewId(self):
        return self.primary

    def CanViewBeDuplicated(self, option):
        # Whether Revit answers no for a view that is itself a dependent
        # is not confirmed, so the fake answers yes and the script has to
        # leave dependents out on its own (GetPrimaryViewId).
        return (option == AS_DEPENDENT and self.duplicable and
                not self.IsTemplate)

    def Duplicate(self, option):
        if not self.CanViewBeDuplicated(option) or self.primary != INVALID:
            raise Exception("The view cannot be duplicated as dependent")
        if self._name in self.doc.refuse_parent:
            raise Exception("Revit says no to " + self._name)
        number = 1
        while self.doc.taken(self.ViewType, u"{0} - Dependent {1}".format(
                self._name, number)):
            number += 1
        view = FakeView(self.doc, u"{0} - Dependent {1}".format(
            self._name, number), self.ViewType)
        view.primary = (self.Id if not self.doc.orphan else INVALID)
        return view.Id

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, name):
        if any(c in name for c in naming.FORBIDDEN):
            raise Exception("Name contains prohibited characters")
        if name in self.doc.refuse:
            raise Exception("Revit says no to " + name)
        if self.doc.taken(self.ViewType, name, besides=self):
            raise Exception("The name is already in use")
        self._name = name.upper() if self.doc.mangle else name


class FakeDoc(object):
    def __init__(self, commit_status="Committed", family=False, refuse=(),
                 refuse_parent=(), mangle=False, orphan=False,
                 selection=()):
        self.views = []
        self.next_id = 100
        self.commit_status = commit_status
        self.IsFamilyDocument = family
        self.refuse = set(refuse)
        self.refuse_parent = set(refuse_parent)
        self.mangle = mangle
        self.orphan = orphan
        self.selection = list(selection)
        self.log = []

    def taken(self, view_type, name, besides=None):
        return any(v is not besides and v.ViewType == view_type and
                   not v.IsTemplate and v._name == name
                   for v in self.views)

    def GetElement(self, element_id):
        for view in self.views:
            if view.Id == element_id:
                return view
        return None

    def names(self):
        return sorted(v._name for v in self.views)

    def dependents_of(self, parent):
        return [v._name for v in self.views if v.primary == parent.Id]


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = (list(self.doc.views),
                      [(v, v._name) for v in self.doc.views])
        self.doc.log.append("start")

    def RollBack(self):
        views, names = self.saved
        self.doc.views[:] = views
        for view, name in names:
            view._name = name
        self.doc.log.append("rollback")

    def Commit(self):
        self.doc.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        return iter(list(self.doc.views))


class Recorder(object):
    """`answer(parents, prefix, start, suffix, count, evaluate)` plays the
    person at the window; returning None is pressing Cancel. `ticks`:
    the labels ticked in the list when nothing usable is selected."""

    def __init__(self, answer=None, ticks=None):
        self.answer = answer
        self.ticks = ticks
        self.alerts = []
        self.printed = []
        self.window = None
        self.offered = None

    def ask(self, parents, prefix, start, suffix, count, evaluate):
        self.window = Namespace(parents=parents, prefix=prefix, start=start,
                                suffix=suffix, count=count,
                                evaluate=evaluate)
        if self.answer is None:
            return None
        return self.answer(parents, prefix, start, suffix, count, evaluate)

    def select(self, labels, **kwargs):
        self.offered = list(labels)
        if self.ticks is None:
            return None
        return [label for label in labels
                if any(label.endswith(t) for t in self.ticks)]

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder, source=SOURCE):
    db = Namespace(
        FilteredElementCollector=FakeCollector,
        View=FakeView,
        ViewDuplicateOption=Namespace(AsDependent=AS_DEPENDENT),
        ElementId=Namespace(InvalidElementId=INVALID),
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(
        doc=doc, uidoc=Namespace(Selection=Namespace(
            GetElementIds=lambda: [v.Id for v in doc.selection])))
    pyrevit.DB = db
    pyrevit.forms = Namespace(
        alert=recorder.alert,
        SelectFromList=Namespace(show=recorder.select))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    window = types.ModuleType("avh_dependents.window")
    window.ask = recorder.ask
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_dependents.window"] = window
    avh_dependents.window = window
    try:
        code = compile(source, SCRIPT, "exec")
        exec(code, {"__name__": "__main__", "__file__": SCRIPT})
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_dependents.window", None)
        del avh_dependents.window
    return recorder


def make(count, prefix=None, suffix=u"", start=None, fields=None):
    """A person who sets how many per view, keeping the offered prefix
    unless told otherwise."""
    def answer(parents, offered_prefix, offered_start, offered_suffix,
               _count, evaluate):
        if fields is not None:
            own = fields
        else:
            own = model.fields(count, start or offered_start,
                               [[] for _ in parents])
        return (offered_prefix if prefix is None else prefix, own, suffix)
    return answer


HAED = u"hæð"


def plans(**kwargs):
    """A model: two floor plans, a ceiling plan sharing a name, a
    template, a dependent, a section that cannot take dependents."""
    doc = FakeDoc(**kwargs)
    one = FakeView(doc, u"Grunnmynd 1. " + HAED)
    two = FakeView(doc, u"Grunnmynd 2. " + HAED)
    ceiling = FakeView(doc, u"Grunnmynd 1. " + HAED, u"CeilingPlan")
    FakeView(doc, u"Sniðmát", template=True)
    FakeView(doc, u"Grunnmynd 1. " + HAED + u" - A", primary=one)
    section = FakeView(doc, u"Snið A", u"Section", duplicable=False)
    return doc, one, two, ceiling, section


def run_all(source=SOURCE):
    """Every scenario. Returns nothing; adds to `results`."""

    # -- the rules ------------------------------------------------------

    check("count: a number from 1 to 50 per view, 200 in all",
          model.count_problem(u"3", 2) == u"" and
          model.count_problem(u"0", 1) and
          model.count_problem(u"51", 1) and
          model.count_problem(u"tveir", 1) and
          model.count_problem(u"50", 4) == u"" and
          u"200" in model.count_problem(u"41", 5))
    check("fill_in: {parent} becomes the parent's name, every time",
          model.fill_in(u"{parent} - {parent}", u"A") == u"A - A" and
          model.fill_in(u"{parnt} - ", u"A") == u"{parnt} - ")
    check("fields: counted per parent from the start",
          model.fields(3, u"1", [[], []]) == [[u"1", u"2", u"3"]] * 2 and
          model.fields(2, u"A", [[u"x"]]) == [[u"A", u"B"]] and
          model.fields(2, u"01", [[]]) == [[u"01", u"02"]])
    check("fields: no start keeps each parent's typed fields, padded",
          model.fields(2, u"", [[u"Norður", u"Suður", u"Vestur"], [u"x"]])
          == [[u"Norður", u"Suður"], [u"x", u""]])
    rows = model.plan([(u"G1", u"FloorPlan"), (u"G2", u"FloorPlan")],
                      model.DEFAULT_PREFIX, [[u"1", u"2"], [u"1", u"2"]],
                      u"")
    check("plan: names per parent, in order",
          [r.name for r in rows] == [u"G1 - 1", u"G1 - 2", u"G2 - 1",
                                     u"G2 - 2"] and
          [r.parent for r in rows] == [0, 0, 1, 1] and
          not model.blocking(rows), [r.name for r in rows])
    rows = model.plan([(u"G1", u"FloorPlan")], u"Hluti ", [[u"1", u"1"]],
                      u"")
    check("plan: two new views on one name are both refused",
          all(u"both" in r.problem for r in rows))
    rows = model.plan([(u"G1", u"FloorPlan"), (u"G1", u"CeilingPlan")],
                      u"Hluti ", [[u"1"], [u"1"]], u"")
    check("plan: the same name under two view types is fine",
          not model.blocking(rows))
    rows = model.plan([(u"G1", u"FloorPlan")], u"", [[u"G1 - 1"]], u"",
                      taken=[(u"FloorPlan", u"G1 - 1")])
    check("plan: a name another view of the type has is refused",
          u"already" in rows[0].problem, rows[0].problem)
    rows = model.plan([(u"G1", u"FloorPlan")], u"", [[u"G1 - 1"]], u"",
                      taken=[(u"CeilingPlan", u"G1 - 1")])
    check("plan: another view type's name is not in the way",
          not model.blocking(rows))
    rows = model.plan([(u"G1", u"FloorPlan")], u"{parnt} - ", [[u"1"]], u"")
    check("plan: a mistyped {parent} is red, not written",
          u"{" in rows[0].problem, rows[0].problem)
    rows = model.plan([(u"G1", u"FloorPlan")], u"", [[u""]], u"")
    check("plan: an empty name", u"empty" in rows[0].problem)

    WINDOW_SRC = io.open(os.path.join(HERE, "lib", "avh_dependents",
                                      "window.py"), encoding="utf-8").read()
    check("window: count and start renumber, prefix and suffix refresh",
          u"self.count_box.TextChanged += self.on_rebuild" in WINDOW_SRC and
          u"self.start_box.TextChanged += self.on_rebuild" in WINDOW_SRC and
          u"box.TextChanged += self.on_changed" in WINDOW_SRC)
    check("window: create stays off while any row is red",
          u"self.create_button.IsEnabled = bool(rows) and not problems"
          in WINDOW_SRC)
    check("window: {parent} shown literally in the XAML, not as markup",
          u'Text="{}{parent}' in WINDOW_SRC)

    # -- 1. Two selected plans, two dependents each ---------------------

    doc, one, two, ceiling, section = plans()
    doc.selection = [two, one]
    rec = run_script(doc, Recorder(make(2)), source)
    check("window: the selected views, in Project Browser order",
          rec.window and rec.window.parents == [
              u"Floor Plan  Grunnmynd 1. " + HAED,
              u"Floor Plan  Grunnmynd 2. " + HAED],
          rec.window and rec.window.parents)
    check("window: offered '{parent} - ' from 1, one per view",
          rec.window and (rec.window.prefix, rec.window.start,
                          rec.window.suffix, rec.window.count) ==
          (u"{parent} - ", u"1", u"", 1))
    check("made: two dependents under each, named",
          doc.dependents_of(one) == [u"Grunnmynd 1. " + HAED + u" - A",
                                     u"Grunnmynd 1. " + HAED + u" - 1",
                                     u"Grunnmynd 1. " + HAED + u" - 2"] and
          doc.dependents_of(two) == [u"Grunnmynd 2. " + HAED + u" - 1",
                                     u"Grunnmynd 2. " + HAED + u" - 2"],
          (doc.dependents_of(one), doc.dependents_of(two), rec.text()))
    check("made: one transaction, committed",
          doc.log == ["start", "commit"], doc.log)
    check("made: the report lists them",
          rec.printed and u"4 dependent view(s)" in rec.printed[0] and
          u"Grunnmynd 2. " + HAED + u" - 2" in rec.printed[0] and
          u"| :--- | :--- | :--- |" in rec.printed[0], rec.printed)

    # -- 2. Nothing selected: ticked from a list of those that qualify --

    doc, one, two, ceiling, section = plans()
    rec = run_script(doc, Recorder(make(1, prefix=u"Hluti "),
                                   ticks=[u"Grunnmynd 1. " + HAED]), source)
    labels = rec.offered or []
    check("list: only views that can take dependents are offered",
          len(labels) == 3 and not any(u"Snið" in l for l in labels) and
          not any(l.endswith(u" - A") for l in labels), labels)
    check("list: a floor plan and ceiling plan of one name told apart",
          any(l.startswith(u"Ceiling Plan") for l in labels) and
          any(l.startswith(u"Floor Plan") for l in labels), labels)
    check("list: both ticked views get one, by their own view type",
          doc.dependents_of(one)[-1:] == [u"Hluti 1"] and
          doc.dependents_of(ceiling) == [u"Hluti 1"],
          (doc.dependents_of(one), doc.dependents_of(ceiling), rec.text()))

    doc, one, two, ceiling, section = plans()
    rec = run_script(doc, Recorder(make(1)), source)
    check("list: nothing ticked, nothing made, no window",
          rec.window is None and doc.log == [] and not rec.alerts)

    # -- 3. A selection that mixes in views that cannot ----------------

    doc, one, two, ceiling, section = plans()
    doc.selection = [one, section]
    rec = run_script(doc, Recorder(make(1)), source)
    check("mixed: the section is left out and named in the report",
          rec.window and len(rec.window.parents) == 1 and
          u"Snið A" in rec.text() and u"left out" in rec.text(),
          rec.text())

    # -- 4. A name another view has: shown red, refused before Revit ----

    doc, one, two, ceiling, section = plans()
    doc.selection = [one]
    rec = run_script(doc, Recorder(make(1, prefix=u"{parent} - ",
                                        start=u"A")), source)
    shown = rec.window.evaluate(u"{parent} - ", [[u"A"]], u"") \
        if rec.window else []
    check("taken: the window would show it in red",
          shown and u"already" in shown[0].problem)
    check("taken: no view made, no transaction, said why",
          doc.log == [] and u"already" in rec.text() and
          len(doc.views) == 6, rec.text())

    # -- 5. Revit refusing one: none are made ---------------------------

    doc, one, two, ceiling, section = plans(
        refuse=[u"Grunnmynd 2. " + HAED + u" - 2"])
    doc.selection = [one, two]
    rec = run_script(doc, Recorder(make(2)), source)
    check("refused: no view left behind",
          len(doc.views) == 6 and doc.dependents_of(two) == [],
          doc.names())
    check("refused: rolled back, and the view named",
          doc.log == ["start", "rollback"] and u" - 2" in rec.text() and
          u"no view was made" in rec.text(), (doc.log, rec.text()))

    doc, one, two, ceiling, section = plans(
        refuse_parent=[u"Grunnmynd 2. " + HAED])
    doc.selection = [one, two]
    rec = run_script(doc, Recorder(make(1)), source)
    check("refused duplicate: nothing left, rolled back",
          len(doc.views) == 6 and doc.log == ["start", "rollback"],
          (doc.log, rec.text()))

    # -- 6. Read back: a name that came back different, a lost parent ---

    doc, one, two, ceiling, section = plans(mangle=True)
    doc.selection = [one]
    rec = run_script(doc, Recorder(make(1)), source)
    check("readback name: rolled back and said so",
          len(doc.views) == 6 and u"came back" in rec.text() and
          doc.log == ["start", "rollback"], (doc.log, rec.text()))

    doc, one, two, ceiling, section = plans(orphan=True)
    doc.selection = [one]
    rec = run_script(doc, Recorder(make(1)), source)
    check("readback parent: not a dependent, rolled back and said so",
          len(doc.views) == 6 and u"dependent of" in rec.text() and
          doc.log == ["start", "rollback"], (doc.log, rec.text()))

    # -- 7. The commit coming back rolled back --------------------------

    doc, one, two, ceiling, section = plans(commit_status="RolledBack")
    doc.selection = [one]
    rec = run_script(doc, Recorder(make(2)), source)
    check("rejected: reported, not claimed as done",
          u"rejected" in rec.text() and not rec.printed and
          len(doc.views) == 6, rec.text())

    # -- 8. Cancel, a family document, nothing to make dependents of ----

    doc, one, two, ceiling, section = plans()
    doc.selection = [one]
    rec = run_script(doc, Recorder(None), source)
    check("cancel: nothing made, no transaction",
          len(doc.views) == 6 and doc.log == [] and not rec.alerts)
    doc = FakeDoc(family=True)
    rec = run_script(doc, Recorder(make(1)), source)
    check("family document: said so, no window",
          u"family document" in rec.text() and rec.window is None)
    doc = FakeDoc()
    FakeView(doc, u"Snið A", u"Section", duplicable=False)
    rec = run_script(doc, Recorder(make(1)), source)
    check("no parents: said so, no window",
          u"No view" in rec.text() and rec.window is None, rec.text())

    # -- 9. Forbidden characters stop the run before Revit is asked -----

    doc, one, two, ceiling, section = plans()
    doc.selection = [one]
    rec = run_script(doc, Recorder(make(1, fields=[[u"A;B"]])), source)
    check("forbidden: refused before Revit is asked",
          doc.log == [] and u";" in rec.text(), rec.text())

    # -- 10. Per-row fields typed by hand, and a suffix with {parent} ---

    doc, one, two, ceiling, section = plans()
    doc.selection = [one, two]
    rec = run_script(doc, Recorder(make(
        2, prefix=u"", suffix=u" ({parent})",
        fields=[[u"Norður", u"Suður"], [u"Norður", u"Suður"]])), source)
    check("typed: each row's own field, {parent} in the suffix",
          doc.dependents_of(two) == [u"Norður (Grunnmynd 2. " + HAED + u")",
                                     u"Suður (Grunnmynd 2. " + HAED + u")"],
          (doc.dependents_of(two), rec.text()))


run_all()

# --------------------------------------------------------------------------
# Mutations: each safety line taken out must make some check fail.
# --------------------------------------------------------------------------

MUTATIONS = [
    ("name read back skipped",
     u"            if got != row.name:", u"            if False:"),
    ("parent read back skipped",
     u"            if view.GetPrimaryViewId() != parent.element.Id:",
     u"            if False:"),
    ("commit status ignored",
     u"    if not ok:\n        return [], (u\"Revit rejected",
     u"    if False:\n        return [], (u\"Revit rejected"),
    ("no roll back on a refusal",
     u"    except BaseException as exc:\n        transaction.RollBack()",
     u"    except BaseException as exc:\n        pass"),
    ("pre-check skipped",
     u"    stopped = model.blocking(rows)",
     u"    stopped = []"),
    ("dependents of dependents let through",
     u"    if primary is not None and primary != "
     u"DB.ElementId.InvalidElementId:\n        return False",
     u"    if False:\n        return False"),
    ("CanViewBeDuplicated ignored",
     u"    return bool(safe(lambda: view.CanViewBeDuplicated(",
     u"    return True or bool(safe(lambda: view.CanViewBeDuplicated("),
    ("taken names not passed to the plan",
     u"model.plan(pairs, prefix, fields, suffix, taken)",
     u"model.plan(pairs, prefix, fields, suffix, [])"),
    ("selection ignored",
     u"    chosen, skipped = selected(doc, parents)",
     u"    chosen, skipped = [], []"),
]

for label, old, new in MUTATIONS:
    if old not in SOURCE:
        check("mutation '{0}': its line is in the script".format(label),
              False, old)
        continue
    saved = list(results)
    del results[:]
    run_all(SOURCE.replace(old, new, 1))
    caught = [name for name, ok, _ in results if not ok]
    results[:] = saved
    if "-v" in sys.argv:
        print(u"mutation '{0}': {1} check(s) fail".format(label,
                                                          len(caught)))
    check("mutation '{0}' is caught".format(label), caught,
          u"every check still passed")

check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
