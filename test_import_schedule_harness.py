# -*- coding: utf-8 -*-
"""
Run the real Import Schedule button script against a mocked Revit and a
mocked pyrevit.

test_roundtrip.py covers the library. This covers the button: the order of
its steps, what it asks, and above all that it writes nothing unless the
person says yes. Workbooks are made with the real writer and edited with
openpyxl, which saves the way Excel does.

Run: python test_import_schedule_harness.py
"""

import os
import runpy
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel",
                      "ImportSchedule.pushbutton", "script.py")

from openpyxl import load_workbook                         # noqa: E402

from avh_schedules import roundtrip as rt, writer           # noqa: E402
from avh_schedules.model import Column, ScheduleTable       # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def table_problems(printed, header, data_rows=None):
    """Why the markdown table that starts with `header` did not go out
    whole in one print_md call, or an empty list when it did. pyRevit
    renders each print_md call as a document of its own, so a table
    printed a line at a time comes out as one table per row and the
    columns do not line up. Each call recorded is one item of `printed`.
    """
    tables = [text for text in printed if text.startswith(header)]
    if len(tables) != 1:
        return [u"{0} calls start with the header".format(len(tables))]
    rows = tables[0].split(u"\n")
    problems = []
    if len(rows) < 3:
        problems.append(u"header without rows in its call")
    if data_rows is not None and len(rows) - 2 != data_rows:
        problems.append(u"{0} data rows, wanted {1}".format(
            len(rows) - 2, data_rows))
    # Every column left aligned. pyRevit's output window right aligns a
    # ---: header but leaves its cells left aligned, so the header drifts
    # off its column (seen in Revit at 2.47.1); a bare --- centres the
    # header. Only :--- lines up.
    if len(rows) > 1 and [cell.strip() for cell in
                          rows[1].split(u"|")[1:-1]] != [u":---"] * (
                              header.count(u"|") - 1):
        problems.append(u"not every column :--- in the rule: " + rows[1])
    columns = header.count(u"|")
    for row in rows:
        if row.count(u"|") != columns:
            problems.append(u"not the header's column count: " + row)
        if u"" in [cell.strip() for cell in row.split(u"|")[1:-1]]:
            problems.append(u"empty cell: " + row)
    # Any other call that is table lines but not a whole table: a row,
    # or a header and rule, printed on its own.
    strays = [text for text in printed
              if text.lstrip().startswith(u"|") and not
              (len(text.split(u"\n")) >= 3 and
               set(text.split(u"\n")[1]) <= set(u"|-: "))]
    if strays:
        problems.append(u"table lines in calls of their own: {0}".format(
            len(strays)))
    return problems


# --- the mocked Revit ---------------------------------------------------

class ElementId(object):
    def __init__(self, value):
        self.Value = value


class StorageType(object):
    String = "String"
    Integer = "Integer"
    Double = "Double"
    ElementId = "ElementId"


class TransactionStatus(object):
    Committed = "Committed"
    RolledBack = "RolledBack"


class Param(object):
    def __init__(self, pid, name, value):
        self.Id = ElementId(pid)
        self.Name = name
        self.StorageType = StorageType.String
        self.IsReadOnly = False
        self.value = value
        self.sets = 0

    def AsString(self):
        return self.value

    def Set(self, value):
        self.value = value
        self.sets += 1
        return True


class Element(object):
    def __init__(self, ident, mark):
        self.Id = ElementId(ident)
        self.GroupId = ElementId(-1)
        self.Parameters = [Param(-1001, u"Mark", mark)]

    def LookupParameter(self, name):
        return self.Parameters[0] if name == u"Mark" else None


class Tx(object):
    status = TransactionStatus.Committed
    opened = 0

    def __init__(self, doc, name):
        pass

    def Start(self):
        Tx.opened += 1

    def Commit(self):
        return Tx.status

    def RollBack(self):
        pass


class Doc(object):
    IsWorkshared = False

    def __init__(self, title, elements):
        self.Title = title
        self.PathName = ""
        self.elements = dict((e.Id.Value, e) for e in elements)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)


DB = types.SimpleNamespace(
    ElementId=ElementId, StorageType=StorageType,
    TransactionStatus=TransactionStatus, Transaction=Tx,
    CheckoutStatus=types.SimpleNamespace(OwnedByOtherUser="other"),
    WorksharingUtils=types.SimpleNamespace(
        GetCheckoutStatus=lambda doc, eid: "none"),
    ForgeTypeId=lambda text: text,
    UnitUtils=types.SimpleNamespace(
        ConvertToInternalUnits=lambda v, u: v / 304.8))


class Forms(object):
    picked = None
    answers = []          # yes/no answers, consumed in order
    alerts = []

    @staticmethod
    def pick_file(**kwargs):
        return Forms.picked

    @staticmethod
    def alert(message, title=None, yes=False, no=False, **kwargs):
        Forms.alerts.append((message, yes and no))
        if yes and no:
            return Forms.answers.pop(0) if Forms.answers else False
        return None


class Output(object):
    def __init__(self):
        self.lines = []

    def print_md(self, text):
        self.lines.append(text)

    def linkify(self, element_id):
        return u"#" + str(element_id.Value)


class Logger(object):
    def debug(self, *a):
        pass

    def error(self, *a):
        pass


OUTPUT = Output()


def install(doc):
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = types.SimpleNamespace(doc=doc)
    pyrevit.DB = DB
    pyrevit.forms = Forms
    pyrevit.script = types.SimpleNamespace(
        get_output=lambda: OUTPUT, get_logger=lambda: Logger())
    sys.modules["pyrevit"] = pyrevit
    for name in [m for m in sys.modules if m.startswith("avh_schedules")
                 and m not in ("avh_schedules",)]:
        pass


def run_button(doc, path, answers):
    install(doc)
    Forms.picked = path
    Forms.answers = list(answers)
    Forms.alerts = []
    OUTPUT.lines = []
    runpy.run_path(SCRIPT, run_name="__main__")


# --- workbooks ----------------------------------------------------------

def make_table(model=u"ELG_CC01"):
    columns = [Column(0, u"Mark"), Column(1, u"Name")]
    for c in columns:
        c.is_empty = False
    rows = [[u"D101", u"Entrance", 1001], [u"D102", u"Store", 1002],
            [u"D103", u"Plant", 1003]]
    table = ScheduleTable(u"Doors", columns, rows)
    table.id_index = 2
    table.fields = [
        rt.FieldSpec(0, u"Mark", rt.KIND_INSTANCE, -1001, u"Mark"),
        rt.FieldSpec(1, u"Name", rt.KIND_TYPE, -1003, u"Type Name"),
    ]
    table.meta = {"schedule_id": 1, "model": model, "exported": u"today"}
    return table


tmp = tempfile.mkdtemp(prefix="avh_import_")
good = os.path.join(tmp, "good.xlsx")
writer.write_workbook(make_table(), good)


def edited(name, edits):
    path = os.path.join(tmp, name)
    wb = load_workbook(good)
    ws = wb["Doors"]
    for old, col, value in edits:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == old:
                    ws.cell(row=cell.row, column=col, value=value)
    wb.save(path)
    return path


def model():
    return Doc(u"ELG_CC01", [Element(1001, u"D101"), Element(1002, u"D102"),
                             Element(1003, u"D103")])


def mark(doc, ident):
    return doc.elements[ident].Parameters[0]


# --- 1. happy path: two edits, confirmed ---------------------------------

path = edited("two.xlsx", [(u"D101", 1, u"D101-A"), (u"D102", 1, u"D102-B")])
doc = model()
run_button(doc, path, [True])
check("a confirmed import writes both edits",
      mark(doc, 1001).value == u"D101-A" and mark(doc, 1002).value == u"D102-B")
check("the untouched element is never Set", mark(doc, 1003).sets == 0)
check("the plan was printed before anything was asked",
      any(u"Will be written" in l for l in OUTPUT.lines))
problems = table_problems(OUTPUT.lines, u"| Element | Column | Was | Becomes |", 2)
check("plan: the table goes out whole, one print_md, header's "
      "column count", not problems, u"; ".join(problems))
check("exactly one yes/no question was asked",
      len([a for a in Forms.alerts if a[1]]) == 1, str(Forms.alerts))
check("the final message says 2 were written",
      u"2 change(s) written" in Forms.alerts[-1][0], str(Forms.alerts[-1]))

# --- 2. declining writes nothing -----------------------------------------

doc = model()
Tx.opened = 0
run_button(doc, path, [False])
check("declining the confirmation writes nothing",
      mark(doc, 1001).value == u"D101" and mark(doc, 1002).value == u"D102")
check("declining opens no transaction", Tx.opened == 0)

# --- 3. cancelling the file picker ---------------------------------------

doc = model()
run_button(doc, None, [True])
check("cancelling the picker does nothing and says nothing",
      not Forms.alerts and mark(doc, 1001).sets == 0)

# --- 4. nothing changed ---------------------------------------------------

doc = model()
run_button(doc, good, [True])
check("an unedited workbook says there is nothing to import",
      Forms.alerts and u"No cells were changed" in Forms.alerts[0][0],
      str(Forms.alerts))
check("and asks no question", not any(a[1] for a in Forms.alerts))

# --- 5. only a type parameter edited --------------------------------------

path5 = edited("type.xlsx", [(u"Entrance", 2, u"Renamed")])
doc = model()
run_button(doc, path5, [True])
check("a type-only edit is reported as unwritable and asks nothing",
      any(u"none can be written" in a[0] for a in Forms.alerts)
      and not any(a[1] for a in Forms.alerts), str(Forms.alerts))
check("the skip reason appears in the output",
      any(rt.SKIP_TYPE in l for l in OUTPUT.lines))

# --- 6. a workbook that is not an AVH export ------------------------------

plain = os.path.join(tmp, "plain.xlsx")
wb = load_workbook(good)
del wb[rt.SNAPSHOT_SHEET]
wb.save(plain)
doc = model()
run_button(doc, plain, [True])
check("a workbook with no snapshot is refused with an explanation",
      Forms.alerts and u"AVH_Roundtrip" in Forms.alerts[0][0])
garbage = os.path.join(tmp, "garbage.xlsx")
with open(garbage, "w") as handle:
    handle.write("not a zip")
run_button(doc, garbage, [True])
check("a file that is not an xlsx is refused, not crashed on",
      Forms.alerts and u"could not be read" in Forms.alerts[0][0])

# --- 7. a different model --------------------------------------------------

other = Doc(u"SOMETHING_ELSE", [Element(1001, u"D101")])
run_button(other, path, [False, True])
check("a different model asks before going on",
      any(a[1] and u"SOMETHING_ELSE" in a[0] for a in Forms.alerts))
check("declining that stops with nothing written", mark(other, 1001).sets == 0)
other = Doc(u"SOMETHING_ELSE", [Element(1001, u"D101"), Element(1002, u"D102")])
run_button(other, path, [True, True])
check("accepting a different model carries on to the normal confirmation",
      mark(other, 1001).value == u"D101-A")
same = model()
same.Title = u"elg_cc01 "
run_button(same, path, [True])
check("a title that differs only in case and spaces is not questioned",
      len([a for a in Forms.alerts if a[1]]) == 1)

# --- 8. Revit rolls the transaction back -----------------------------------

doc = model()
Tx.status = TransactionStatus.RolledBack
run_button(doc, path, [True])
Tx.status = TransactionStatus.Committed
check("a rolled back commit is reported as nothing written",
      any(u"rejected" in a[0] for a in Forms.alerts)
      and not any(u"change(s) written" in a[0] for a in Forms.alerts),
      str(Forms.alerts[-1]))

# --- 9. a conflict is shown, not overwritten --------------------------------

doc = model()
mark(doc, 1001).value = u"CHANGED-IN-REVIT"
run_button(doc, path, [True])
check("a value changed in Revit since the export is left alone",
      mark(doc, 1001).value == u"CHANGED-IN-REVIT"
      and mark(doc, 1002).value == u"D102-B")
check("the conflict is named in the output",
      any(rt.SKIP_CONFLICT in l for l in OUTPUT.lines))

# --- 10. the script is ASCII (pyRevit parses it at startup) -----------------

raw = open(SCRIPT, "rb").read()
check("the button script is pure ASCII", all(b < 128 for b in raw))

# --- report -----------------------------------------------------------------

shutil.rmtree(tmp, ignore_errors=True)
failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    if not ok:
        print("FAIL  " + name + ("   [" + detail + "]" if detail else ""))
print("{0} checks, {1} failed".format(len(results), len(failed)))
if failed:
    sys.exit(1)
print("PASS")
