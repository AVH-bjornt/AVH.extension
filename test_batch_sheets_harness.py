# -*- coding: utf-8 -*-
"""
Runs the real Batch Sheets script against a mocked Revit.

The window (lib/avh_sheets/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person
would. Everything the window shows comes from the `evaluate` callable
the script passes it, and that is exercised here.

The fake lies the way Revit is expected to:

- `ViewSheet.Create` makes a sheet with a number of Revit's own choosing.
- Setting a sheet number another sheet has raises, ignoring case.
- Forbidden characters raise.
- A rolled back transaction, or a commit that comes back RolledBack,
  removes every sheet made in it.

Run outside Revit:

    python test_batch_sheets_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Batch Sheets.pushbutton", "script.py")

import avh_sheets                       # noqa: E402
from avh_sheets import model            # noqa: E402
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


INVALID = FakeId(-1)


class FakeSymbol(object):
    def __init__(self, doc, value, family, name, active=True):
        self.Id = FakeId(value)
        self.FamilyName = family
        self.Name = name
        self.IsActive = active
        doc.symbols.append(self)

    def Activate(self):
        self.IsActive = True


class FakeTitleBlock(object):
    def __init__(self, type_id):
        self.type_id = type_id

    def GetTypeId(self):
        return self.type_id


class FakeViewSheet(object):
    created = 0

    def __init__(self, doc, number, name, type_id):
        self.doc = doc
        FakeViewSheet.created += 1
        self.Id = FakeId(1000 + FakeViewSheet.created)
        self._number = number
        self._name = name
        self.type_id = type_id
        doc.sheets.append(self)

    @staticmethod
    def Create(doc, type_id):
        if type_id != INVALID:
            symbol = [s for s in doc.symbols if s.Id == type_id][0]
            if not symbol.IsActive:
                raise Exception("The symbol is not active.")
        auto = u"auto-{0}".format(FakeViewSheet.created + 1)
        return FakeViewSheet(doc, auto, u"Unnamed", type_id)

    @property
    def SheetNumber(self):
        return self._number

    @SheetNumber.setter
    def SheetNumber(self, number):
        if any(c in number for c in naming.FORBIDDEN):
            raise Exception("Sheet number contains prohibited characters")
        if number in self.doc.refuse:
            raise Exception("Revit says no to " + number)
        if any(s is not self and s._number.lower() == number.lower()
               for s in self.doc.sheets):
            raise Exception("Sheet number is already in use")
        self._number = number.upper() if self.doc.mangle else number

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, name):
        if any(c in name for c in naming.FORBIDDEN):
            raise Exception("Name contains prohibited characters")
        self._name = name


class FakeDoc(object):
    def __init__(self, commit_status="Committed", family=False,
                 refuse=(), mangle=False):
        self.sheets = []
        self.symbols = []
        self.placed = []
        self.commit_status = commit_status
        self.IsFamilyDocument = family
        self.refuse = set(refuse)
        self.mangle = mangle
        self.log = []

    def sheet(self, number, name, symbol=None):
        sheet = FakeViewSheet(self, number, name,
                              symbol.Id if symbol else INVALID)
        if symbol is not None:
            self.placed.append(FakeTitleBlock(symbol.Id))
        return sheet

    def numbers(self):
        return sorted(s._number for s in self.sheets)


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = (list(self.doc.sheets),
                      [(s, s._number, s._name) for s in self.doc.sheets],
                      [(s, s.IsActive) for s in self.doc.symbols])
        self.doc.log.append("start")

    def RollBack(self):
        sheets, values, symbols = self.saved
        self.doc.sheets[:] = sheets
        for sheet, number, name in values:
            sheet._number, sheet._name = number, name
        for symbol, active in symbols:
            symbol.IsActive = active
        self.doc.log.append("rollback")

    def Commit(self):
        self.doc.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.category = None

    def OfCategory(self, category):
        self.category = category
        return self

    def WhereElementIsElementType(self):
        return iter(list(self.doc.symbols))

    def WhereElementIsNotElementType(self):
        return iter(list(self.doc.placed))

    def OfClass(self, cls):
        return iter(list(self.doc.sheets))


class Recorder(object):
    """`answer(labels, chosen, number, count, evaluate)` plays the person
    at the window; returning None is pressing Cancel."""

    def __init__(self, answer=None):
        self.answer = answer
        self.alerts = []
        self.printed = []
        self.window = None

    def ask(self, labels, chosen, number, count, evaluate):
        self.window = Namespace(labels=labels, chosen=chosen, number=number,
                                count=count, evaluate=evaluate)
        if self.answer is None:
            return None
        return self.answer(labels, chosen, number, count, evaluate)

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder):
    db = Namespace(
        FilteredElementCollector=FakeCollector,
        BuiltInCategory=Namespace(OST_TitleBlocks="OST_TitleBlocks"),
        BuiltInParameter=Namespace(SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM"),
        ViewSheet=FakeViewSheet,
        ElementId=Namespace(InvalidElementId=INVALID),
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc)
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert)
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    window = types.ModuleType("avh_sheets.window")
    window.ask = recorder.ask
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_sheets.window"] = window
    avh_sheets.window = window
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_sheets.window", None)
        del avh_sheets.window
    return recorder


def batch(count, start=None, names=None, titleblock=None,
          number_prefix=None, number_suffix=None,
          name_prefix=u"", name_suffix=u""):
    """A person who keeps the suggested numbering unless told otherwise."""
    def answer(labels, chosen, number, _count, evaluate):
        fields = naming.numbers(count, start or number[1])
        return dict(
            titleblock=chosen if titleblock is None else titleblock,
            values=(number[0] if number_prefix is None else number_prefix,
                    fields,
                    number[2] if number_suffix is None else number_suffix,
                    name_prefix,
                    names or [u"Unnamed"] * count,
                    name_suffix))
    return answer


def model_with_sheets(**kwargs):
    doc = FakeDoc(**kwargs)
    a3 = FakeSymbol(doc, 11, u"AVH Titill", u"A3")
    a1 = FakeSymbol(doc, 12, u"AVH Titill", u"A1")
    # A3 is the most used, and sorts second, so "most used" and "first
    # in the list" give different answers.
    doc.sheet(u"A-101", u"Grunnmynd 1", a3)
    doc.sheet(u"A-102", u"Grunnmynd 2", a3)
    doc.sheet(u"A-901", u"Deili", a1)
    doc.sheet(u"A-9", u"Yfirlit", a3)
    return doc, a1, a3


HAED = u"hæð"

# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------

check("suggest: continues after the highest, naturally sorted",
      model.suggest([u"A-101", u"A-105", u"A-9"]) == (u"A-", u"106", u""))
check("suggest: keeps the width",
      model.suggest([u"009"]) == (u"", u"010", u""))
check("suggest: keeps a suffix",
      model.suggest([u"A-101a"]) == (u"A-", u"102", u"a"))
check("suggest: no sheets starts at 101",
      model.suggest([]) == (u"", u"101", u""))
check("suggest: no numbered sheets starts at 101",
      model.suggest([u"Forsida"]) == (u"", u"101", u""))
check("count: a number from 1 to 200",
      model.count_problem(u"5") == u"" and model.count_problem(u"0") and
      model.count_problem(u"201") and model.count_problem(u"tveir"))

rows = model.plan(u"A-", [u"101", u"103", u"103"], u"", u"", [u"a", u"b",
                                                               u"c"], u"",
                  taken_numbers=[u"a-101"])
check("plan: a number already taken is refused, ignoring case",
      u"already" in rows[0].problem, rows[0].problem)
check("plan: two new sheets on one number are both refused",
      u"both" in rows[1].problem and u"both" in rows[2].problem,
      [r.problem for r in rows])
rows = model.plan(u"A:", [u"1"], u"", u"", [u"x"], u"")
check("plan: a forbidden character in the number",
      rows[0].problem.startswith(u"number"), rows[0].problem)
rows = model.plan(u"A-", [u"1"], u"", u"", [u""], u"")
check("plan: an empty name", rows[0].problem.startswith(u"name"),
      rows[0].problem)
rows = model.plan(u"A-", [u"1", u"2"], u"", u"Grunnmynd ", [u"1. " + HAED,
                                                            u"2. " + HAED],
                  u"")
check("plan: names may repeat and carry Icelandic",
      not model.blocking(rows) and rows[1].name == u"Grunnmynd 2. " + HAED)
check("resize: pads and cuts",
      model.resize([u"a", u"b"], 3, u"z") == [u"a", u"b", u"z"] and
      model.resize([u"a", u"b"], 1, u"z") == [u"a"])


# --------------------------------------------------------------------------
# 1. Three sheets, numbering carried on, the usual title block
# --------------------------------------------------------------------------

doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(3, names=[u"Snid A", u"Snid B",
                                               u"Snid C"])))
check("window: the title blocks are offered, then none",
      rec.window and rec.window.labels == [u"AVH Titill : A1",
                                           u"AVH Titill : A3",
                                           u"(no title block)"],
      rec.window and rec.window.labels)
check("window: the most used title block is chosen first",
      rec.window and rec.window.chosen == 1, rec.window and
      rec.window.chosen)
check("window: numbering carries on from the highest number",
      rec.window and rec.window.number == (u"A-", u"902", u""),
      rec.window and rec.window.number)
new = [s for s in doc.sheets if s.Id.Value > 1004]
check("made: three sheets, numbered on from A-901",
      [s.SheetNumber for s in new] == [u"A-902", u"A-903", u"A-904"],
      [s.SheetNumber for s in new])
check("made: named per row", [s.Name for s in new] ==
      [u"Snid A", u"Snid B", u"Snid C"])
check("made: with the chosen title block",
      all(s.type_id == a3.Id for s in new))
check("made: one transaction, committed",
      doc.log == ["start", "commit"], doc.log)
check("made: the report lists them",
      rec.printed and u"A-904" in rec.printed[0] and u"3 sheet(s)" in
      rec.printed[0] and u"AVH Titill : A3" in rec.printed[0], rec.printed)

# 2. Another title block, and none.
doc, a1, a3 = model_with_sheets()
run_script(doc, Recorder(batch(1, titleblock=0, number_prefix=u"B-",
                               start=u"1")))
check("title block: the one picked is used, not the usual one",
      doc.sheets[-1].type_id == a1.Id and doc.sheets[-1].SheetNumber ==
      u"B-1")
doc, a1, a3 = model_with_sheets()
run_script(doc, Recorder(batch(1, titleblock=2, number_prefix=u"X")))
check("title block: none makes a sheet without one",
      doc.sheets[-1].type_id == INVALID)

# 3. A title block type not yet active is activated.
doc = FakeDoc()
idle = FakeSymbol(doc, 11, u"AVH Titill", u"A0", active=False)
rec = run_script(doc, Recorder(batch(2)))
check("inactive: activated and used",
      idle.IsActive and len(doc.sheets) == 2 and
      doc.sheets[0].SheetNumber == u"101", (doc.numbers(), rec.text()))

# 4. A number another sheet has: shown to the window, refused by the script.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(2, start=u"101")))
shown = rec.window.evaluate(u"A-", [u"101", u"102"], u"", u"", [u"x", u"y"],
                            u"") if rec.window else []
check("taken: the window would show both in red",
      shown and all(u"already" in r.problem for r in shown))
check("taken: no sheet made, no transaction, said why",
      len(doc.sheets) == 4 and doc.log == [] and u"already" in rec.text(),
      rec.text())

# 5. Revit refusing one: none are made.
doc, a1, a3 = model_with_sheets(refuse=[u"A-903"])
rec = run_script(doc, Recorder(batch(3)))
check("refused: no sheet left behind",
      doc.numbers() == [u"A-101", u"A-102", u"A-9", u"A-901"],
      doc.numbers())
check("refused: rolled back, and the sheet named",
      doc.log == ["start", "rollback"] and u"A-903" in rec.text() and
      u"no sheet was made" in rec.text(), (doc.log, rec.text()))

# 6. A number that came back different.
doc, a1, a3 = model_with_sheets(mangle=True)
rec = run_script(doc, Recorder(batch(1, number_prefix=u"b-")))
check("readback: rolled back and said so",
      len(doc.sheets) == 4 and u"came back" in rec.text(), rec.text())

# 7. The commit coming back rolled back.
doc, a1, a3 = model_with_sheets(commit_status="RolledBack")
rec = run_script(doc, Recorder(batch(2)))
check("rejected: reported, not claimed as done",
      u"rejected" in rec.text() and not rec.printed and
      len(doc.sheets) == 4, rec.text())

# 8. Cancel, a family document, a model with no sheets or title blocks.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(None))
check("cancel: nothing made, no transaction",
      len(doc.sheets) == 4 and doc.log == [] and not rec.alerts)
doc = FakeDoc(family=True)
rec = run_script(doc, Recorder(batch(1)))
check("family document: said so, no window",
      u"family document" in rec.text() and rec.window is None)
doc = FakeDoc()
rec = run_script(doc, Recorder(batch(1)))
check("empty model: only 'no title block' offered, starts at 101",
      rec.window and rec.window.labels == [u"(no title block)"] and
      rec.window.number == (u"", u"101", u""))
check("empty model: the sheet is made",
      doc.numbers() == [u"101"], doc.numbers())

# 9. Forbidden characters stop the run before Revit is asked.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(1, names=[u"Snid; A"])))
check("forbidden: refused before Revit is asked",
      doc.log == [] and u";" in rec.text(), rec.text())

# --------------------------------------------------------------------------

check("harness: a taken number raises in the fake",
      not model.plan(u"", [u"x"], u"", u"", [u"y"], u"", [u"x"])[0]
      .problem == u"")
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
