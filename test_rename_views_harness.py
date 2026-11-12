# -*- coding: utf-8 -*-
"""
Runs the real Rename Views script against a mocked Revit.

The window (lib/avh_rename/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person
would. Everything the window shows comes from the `evaluate` callable
the script passes it, and that is exercised here.

The fake lies the way Revit is expected to:

- Setting a view's name to one another view of the same view type
  already has raises, at the moment of setting, so renames that swap
  names fail unless done through temporary names.
- A floor plan and a ceiling plan may share a name; sheets may share one.
- Forbidden characters raise.
- A transaction rolled back, or a commit that comes back RolledBack,
  puts every name back.

Run outside Revit:

    python test_rename_views_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Rename Views.pushbutton", "script.py")

import avh_rename                       # noqa: E402
from avh_rename import model            # noqa: E402

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


class FakeView(object):
    def __init__(self, doc, value, name, view_type=u"FloorPlan",
                 template=False, refuse=None, mangle=False):
        self.doc = doc
        self.Id = FakeId(value)
        self._name = name
        self.ViewType = view_type
        self.IsTemplate = template
        self.refuse = refuse          # a name Revit refuses for this view
        self.mangle = mangle          # Revit keeps something else
        self.sets = []
        doc.views.append(self)

    def _clash(self, name):
        return [v for v in self.doc.views
                if v is not self and not v.IsTemplate
                and not isinstance(v, FakeViewSheet)
                and v.ViewType == self.ViewType and v._name == name]

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, name):
        self.sets.append(name)
        if any(c in name for c in model.FORBIDDEN):
            raise Exception("Name contains prohibited characters")
        if name == self.refuse:
            raise Exception("Revit says no to " + name)
        if not isinstance(self, FakeViewSheet) and self._clash(name):
            raise Exception("Name \"{0}\" is already in use".format(name))
        self._name = name.upper() if self.mangle and \
            not name.startswith(u"AVH rename") else name


class FakeViewSheet(FakeView):
    def __init__(self, doc, value, number, name):
        FakeView.__init__(self, doc, value, name, u"DrawingSheet")
        self.SheetNumber = number


class FakeDoc(object):
    def __init__(self, commit_status="Committed"):
        self.views = []
        self.commit_status = commit_status
        self.log = []

    def names(self):
        return dict((v.Id.Value, v._name) for v in self.views)


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = [(v, v._name) for v in self.doc.views]
        self.doc.log.append("start")

    def RollBack(self):
        for view, name in self.saved:
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
    """`answer(prefix, suffix, fields, evaluate)` plays the person at the
    window; returning None is pressing Cancel."""

    def __init__(self, answer=None, ticks=(), selected=()):
        self.answer = answer
        self.ticks = list(ticks)
        self.selected = list(selected)
        self.alerts = []
        self.printed = []
        self.pickers = []
        self.window = None

    def show(self, items, multiselect=False, **kwargs):
        self.pickers.append(list(items))
        picked = [i for i in items if any(t in i for t in self.ticks)]
        return picked or None

    def ask(self, labels, olds, prefix, suffix, fields, evaluate):
        self.window = Namespace(labels=labels, olds=olds, prefix=prefix,
                                suffix=suffix, fields=list(fields),
                                evaluate=evaluate)
        if self.answer is None:
            return None
        return self.answer(prefix, suffix, list(fields), evaluate)

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder):
    db = Namespace(
        FilteredElementCollector=FakeCollector,
        View=FakeView,
        ViewSheet=FakeViewSheet,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(Selection=Namespace(
        GetElementIds=lambda: [FakeId(v) for v in recorder.selected])))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=recorder.show))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    window = types.ModuleType("avh_rename.window")
    window.ask = recorder.ask
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_rename.window"] = window
    avh_rename.window = window
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_rename.window", None)
        del avh_rename.window
    return recorder


def keep_fields(new_prefix=None, new_suffix=None):
    def answer(prefix, suffix, fields, evaluate):
        return (prefix if new_prefix is None else new_prefix,
                suffix if new_suffix is None else new_suffix, fields)
    return answer


def with_fields(values, new_prefix=None, new_suffix=None):
    def answer(prefix, suffix, fields, evaluate):
        return (prefix if new_prefix is None else new_prefix,
                suffix if new_suffix is None else new_suffix, list(values))
    return answer


# --------------------------------------------------------------------------
# The naming rules
# --------------------------------------------------------------------------

HAED = u"hæð"

check("affixes: shared start and end",
      model.affixes([u"Grunnmynd 1. " + HAED, u"Grunnmynd 2. " + HAED]) ==
      (u"Grunnmynd ", u". " + HAED))
check("affixes: a number is not cut in two at the start",
      model.affixes([u"A-101", u"A-102"]) == (u"A-", u""))
check("affixes: E09 and E10 share only E",
      model.affixes([u"E09", u"E10"]) == (u"E", u""))
check("affixes: a number is not cut in two at the end",
      model.affixes([u"a112", u"b212"]) == (u"", u""))
check("affixes: digits after a letter are a real shared end",
      model.affixes([u"x12", u"y12"]) == (u"", u"12"))
check("affixes: one name has none", model.affixes([u"E01"]) == (u"", u""))
check("affixes: prefix and suffix never overlap",
      model.affixes([u"abcab", u"abab"]) == (u"ab", u"ab"))
check("field: what is left between",
      model.field_of(u"Grunnmynd 1. " + HAED, u"Grunnmynd ",
                     u". " + HAED) == u"1")
check("field: a part the name lacks is left on",
      model.field_of(u"Snid A", u"Grunnmynd ", u"") == u"Snid A")
check("compose", model.compose(u"P ", u"x", u" S") == u"P x S")
check("problem: forbidden character", u":" in model.problem(u"E01: a"))
check("problem: empty", model.problem(u"  ") != u"")
check("problem: Icelandic is fine", model.problem(u"Snið " + HAED) == u"")
check("numbers: keeps width", model.numbers(3, u"01") ==
      [u"01", u"02", u"03"])
check("numbers: grows past width", model.numbers(2, u"9") == [u"9", u"10"])
check("numbers: from 101", model.numbers(2, u"101") == [u"101", u"102"])
check("numbers: letters", model.numbers(3, u"A") == [u"A", u"B", u"C"])
check("numbers: letters run out", model.numbers(3, u"Y") is None)
check("numbers: nonsense", model.numbers(3, u"x1") is None)

rows = model.plan([(u"E01", u"FloorPlan"), (u"E02", u"FloorPlan"),
                   (u"E01", u"CeilingPlan")], u"", u"", [u"X", u"X", u"X"])
check("plan: two floor plans landing on one name clash",
      rows[0].problem and rows[1].problem, [r.problem for r in rows])
check("plan: a ceiling plan may share a floor plan's name",
      not rows[2].problem)
rows = model.plan([(u"A", None), (u"B", None)], u"", u"", [u"X", u"X"])
check("plan: sheets may share a name", not model.blocking(rows))
rows = model.plan([(u"E01", u"FloorPlan")], u"", u"", [u"E09"],
                  taken=[(u"FloorPlan", u"E09")])
check("plan: a name another view has is refused",
      u"already" in rows[0].problem)
rows = model.plan([(u"E01", u"FloorPlan")], u"", u"", [u"E09"],
                  taken=[(u"CeilingPlan", u"E09")])
check("plan: ...but not one a different view type has",
      not rows[0].problem)


# --------------------------------------------------------------------------
# 1. Selected floor plans: change the prefix, keep each field
# --------------------------------------------------------------------------

doc = FakeDoc()
a = FakeView(doc, 1, u"Grunnmynd 1. " + HAED)
b = FakeView(doc, 2, u"Grunnmynd 2. " + HAED)
c = FakeView(doc, 3, u"Grunnmynd 3. " + HAED)
rec = run_script(doc, Recorder(keep_fields(new_prefix=u"GM "),
                               selected=[1, 2]))
check("selected: no picker", not rec.pickers)
check("selected: window opened with the shared prefix and suffix",
      rec.window and rec.window.prefix == u"Grunnmynd " and
      rec.window.suffix == u". " + HAED, rec.window and
      (rec.window.prefix, rec.window.suffix))
check("selected: fields are what differs",
      rec.window and rec.window.fields == [u"1", u"2"])
check("selected: both renamed with the new prefix",
      a.Name == u"GM 1. " + HAED and b.Name == u"GM 2. " + HAED,
      (a.Name, b.Name))
check("selected: the unchosen view is untouched",
      c.Name == u"Grunnmynd 3. " + HAED)
check("selected: committed once", doc.log == ["start", "commit"], doc.log)
check("selected: the report lists both",
      rec.printed and u"GM 1." in rec.printed[0] and u"GM 2." in
      rec.printed[0] and u"2 renamed" in rec.printed[0], rec.printed)

# 2. Nothing selected: picked from the list, templates and browsers left out.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
FakeView(doc, 2, u"E02")
FakeView(doc, 3, u"Template plan", template=True)
FakeView(doc, 4, u"Project Browser", view_type=u"ProjectBrowser")
s = FakeViewSheet(doc, 5, u"A-101", u"Grunnmynd")
rec = run_script(doc, Recorder(with_fields([u"Sheet one", u"E-one"]),
                               ticks=[u"E01", u"A-101"]))
listed = rec.pickers[0] if rec.pickers else []
check("picker: shown when nothing is selected", rec.pickers)
check("picker: templates and the browser are not offered",
      not any(u"Template" in i or u"Project Browser" in i for i in listed),
      listed)
check("picker: sheets come first and say their number",
      listed and listed[0].startswith(u"Sheet A-101"), listed)
check("picker: view type spelled out", u"Floor Plan  E01" in listed, listed)
check("picker: the sheet's name changed, its number did not",
      s.Name == u"Sheet one" and s.SheetNumber == u"A-101")
check("picker: the view renamed", a.Name == u"E-one")

# 3. Swapping names among the chosen works through temporary names.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
b = FakeView(doc, 2, u"E02")
rec = run_script(doc, Recorder(with_fields([u"02", u"01"]), selected=[1, 2]))
check("swap: E01 and E02 trade names", a.Name == u"E02" and
      b.Name == u"E01", (a.Name, b.Name, rec.text()))
check("swap: committed", doc.log == ["start", "commit"], doc.log)

# 4. A name another view has: the window is told, and the script refuses.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
FakeView(doc, 2, u"E02")
FakeView(doc, 3, u"E01", view_type=u"CeilingPlan")
rec = run_script(doc, Recorder(with_fields([u"E02"], new_prefix=u""),
                               selected=[1]))
shown = rec.window.evaluate(u"", u"", [u"E02"]) if rec.window else []
check("clash: the window would show it in red",
      shown and u"already" in shown[0].problem)
check("clash: nothing renamed, no transaction, and said why",
      a.Name == u"E01" and doc.log == [] and u"already" in rec.text(),
      rec.text())
shown = rec.window.evaluate(u"", u"", [u"E01-a"]) if rec.window else []
check("clash: a ceiling plan of the same name does not block it",
      shown and not shown[0].problem)

# 5. Revit refusing one rename: everything is put back.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
b = FakeView(doc, 2, u"E02", refuse=u"Plan 02")
rec = run_script(doc, Recorder(keep_fields(new_prefix=u"Plan "),
                               selected=[1, 2]))
check("refused: both names put back",
      a.Name == u"E01" and b.Name == u"E02", (a.Name, b.Name))
check("refused: rolled back, and the view named",
      doc.log == ["start", "rollback"] and u"E02" in rec.text() and
      u"nothing was renamed" in rec.text(), (doc.log, rec.text()))

# 6. Revit keeping something other than what was asked.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
b = FakeView(doc, 2, u"E02", mangle=True)
rec = run_script(doc, Recorder(keep_fields(new_prefix=u"plan "),
                               selected=[1, 2]))
check("readback: a name that came back different rolls back",
      a.Name == u"E01" and doc.log == ["start", "rollback"] and
      u"came back" in rec.text(), (a.Name, doc.log, rec.text()))

# 7. The commit coming back rolled back.
doc = FakeDoc(commit_status="RolledBack")
a = FakeView(doc, 1, u"E01")
FakeView(doc, 2, u"E02")
rec = run_script(doc, Recorder(keep_fields(new_prefix=u"P"),
                               selected=[1, 2]))
check("rejected: reported, not claimed as done",
      u"rejected" in rec.text() and not rec.printed and a.Name == u"E01",
      rec.text())

# 8. Cancel, and nothing changing.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
FakeView(doc, 2, u"E02")
rec = run_script(doc, Recorder(None, selected=[1, 2]))
check("cancel: nothing renamed, no transaction",
      doc.log == [] and not rec.alerts, rec.alerts)
rec = run_script(doc, Recorder(keep_fields(), selected=[1, 2]))
check("no change: said so, no transaction",
      doc.log == [] and u"No name would change" in rec.text())

# 9. Only the changed ones are written; forbidden characters stop the run.
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
b = FakeView(doc, 2, u"E02")
rec = run_script(doc, Recorder(with_fields([u"01", u"05"]), selected=[1, 2]))
check("unchanged: a name that stays is not written",
      a.sets == [] and b.Name == u"E05", (a.sets, b.Name))
doc = FakeDoc()
a = FakeView(doc, 1, u"E01")
FakeView(doc, 2, u"E02")
rec = run_script(doc, Recorder(keep_fields(new_prefix=u"E:"),
                               selected=[1, 2]))
check("forbidden: refused before Revit is asked",
      doc.log == [] and a.sets == [] and u":" in rec.text(), rec.text())

# 10. Two sheets may end with the same name.
doc = FakeDoc()
s1 = FakeViewSheet(doc, 1, u"A-101", u"Grunnmynd 1")
s2 = FakeViewSheet(doc, 2, u"A-102", u"Grunnmynd 2")
rec = run_script(doc, Recorder(with_fields([u"", u""]), selected=[1, 2]))
check("sheets: two sheets may share a name",
      s1.Name == u"Grunnmynd " and s2.Name == u"Grunnmynd " and
      doc.log == ["start", "commit"], (s1.Name, s2.Name, rec.text()))

# --------------------------------------------------------------------------

check("harness: a clashing name raises in the fake",
      FakeView(FakeDoc(), 9, u"x")._clash(u"y") == [])
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
