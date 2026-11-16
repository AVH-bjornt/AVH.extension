# -*- coding: utf-8 -*-
"""
The export / edit / import round trip, outside Revit.

Two halves. The first writes a workbook with the real writer, edits it the
way a person does, and reads the changes back out. The edit is made with
OPENPYXL, not by hand, on purpose: openpyxl saves like Excel does, turning
every inline string into a shared string and rewriting the whole file, so
the reader is tested against the shape it will really meet. openpyxl is
needed here only, never inside Revit.

The second half runs the applier against a mocked Revit whose parameters
keep their values, so "only what changed is written" is checked against
state rather than asserted.

Run: python test_roundtrip.py
"""

import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from openpyxl import load_workbook                      # noqa: E402

from avh_schedules import applier, roundtrip as rt, writer, xlsxread  # noqa: E402,E501
from avh_schedules.model import Column, ScheduleTable   # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --- a table that looks like what the reader produces -------------------

def make_table():
    columns = [Column(0, u"Mark"), Column(1, u"Name"),
               Column(2, u"Width"), Column(3, u"Fire rating"),
               Column(4, u"Level")]
    columns[2].is_numeric = True
    columns[2].unit = u"mm"
    for c in columns:
        c.is_empty = False

    # the trailing value on each row is the element id
    rows = [
        [u"D101", u"Entrance door", 1000, u"EI30", u"E01", 1001],
        [u"D102", u"Rými A", 900, u"EI60", u"E01", 1002],
        [u"D103", u"Store", 800.5, None, u"E02", 1003],
        [u"D104", u"Plant", 900, u"EI30", u"E02", None],   # no element
    ]
    table = ScheduleTable(u"Doors", columns, rows, group_column=4)
    table.id_index = 5
    table.fields = [
        rt.FieldSpec(0, u"Mark", rt.KIND_INSTANCE, -1001, u"Mark", u""),
        rt.FieldSpec(1, u"Name", rt.KIND_INSTANCE, 5001, u"RoomName", u""),
        rt.FieldSpec(2, u"Width", rt.KIND_INSTANCE, -1002, u"Width",
                     u"autodesk.unit.unit:millimeters-1.0.1"),
        rt.FieldSpec(3, u"Fire rating", rt.KIND_TYPE, -1003, u"Fire",
                     u""),
        rt.FieldSpec(4, u"Level", rt.KIND_OTHER, None, u"Level", u""),
    ]
    table.meta = {"schedule_id": 777, "model": u"ELG_CC01",
                  "exported": u"2026-11-13 09:00"}
    return table


tmp = tempfile.mkdtemp(prefix="avh_roundtrip_")
original = os.path.join(tmp, "doors.xlsx")
writer.write_workbook(make_table(), original)


def snapshot_of(path):
    return rt.read_snapshot(xlsxread.read_workbook(path))


# --- 1. the workbook carries what it needs -------------------------------

wb = load_workbook(original)
check("a hidden snapshot sheet exists",
      rt.SNAPSHOT_SHEET in wb.sheetnames
      and wb[rt.SNAPSHOT_SHEET].sheet_state == "hidden",
      str(wb.sheetnames))
check("the visible sheet is first and visible",
      wb.sheetnames[0] == "Doors" and wb["Doors"].sheet_state == "visible")
check("the id column is hidden",
      wb["Doors"].column_dimensions["F"].hidden)
check("the print area stops before the hidden id column",
      "$F" not in str(wb["Doors"].print_area)
      and "$E$" in str(wb["Doors"].print_area),
      str(wb["Doors"].print_area))

snap, visible = snapshot_of(original)
check("snapshot reads back the schedule name and id",
      snap.schedule_name == u"Doors" and snap.schedule_id == 777)
check("snapshot reads back the model", snap.model == u"ELG_CC01")
check("snapshot holds a row per element, none for the row with no id",
      sorted(snap.originals) == [1001, 1002, 1003], str(sorted(snap.originals)))
check("snapshot keeps the column kinds",
      [f.kind for f in snap.fields]
      == [rt.KIND_INSTANCE, rt.KIND_INSTANCE, rt.KIND_INSTANCE,
          rt.KIND_TYPE, rt.KIND_OTHER])
check("snapshot keeps the unit of a numeric column",
      snap.fields[2].unit_id == u"autodesk.unit.unit:millimeters-1.0.1")
check("snapshot keeps Icelandic text intact",
      snap.originals[1002][1] == u"Rými A", str(snap.originals[1002]))
check("a blank cell is blank in the snapshot", snap.originals[1003][3] is None)

# an untouched workbook has no changes
diff = rt.find_changes(snap, visible)
check("an untouched workbook has no changes", not diff.changes,
      str(diff.changes))
check("an untouched workbook compares three rows", diff.rows_seen == 3)

# --- 2. edit it the way Excel would -------------------------------------


def find_row(ws, text):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == text:
                return cell.row
    raise AssertionError("no row with " + text)


def edit(path, out, edits, delete_rows=(), copy_row=None):
    wb = load_workbook(path)
    ws = wb["Doors"]
    for mark, col, value in edits:
        ws.cell(row=find_row(ws, mark), column=col, value=value)
    if copy_row:
        src = find_row(ws, copy_row)
        dst = ws.max_row + 1
        for c in range(1, 7):
            ws.cell(row=dst, column=c, value=ws.cell(row=src, column=c).value)
    for mark in delete_rows:
        ws.delete_rows(find_row(ws, mark))
    wb.save(out)
    return out


edited = edit(original, os.path.join(tmp, "edited.xlsx"), [
    (u"D101", 1, u"D101-A"),      # Mark: text
    (u"D102", 3, 950),            # Width: number
    (u"D103", 4, u"EI90"),        # Fire rating: filling a blank, a type param
])
snap, visible = snapshot_of(edited)
diff = rt.find_changes(snap, visible)
got = sorted((c.element_id, c.field.name, c.new) for c in diff.changes)
check("exactly the three edited cells are changes",
      got == [(1001, u"Mark", u"D101-A"), (1002, u"Width", 950),
              (1003, u"Fire rating", u"EI90")], str(got))
check("the old value comes from the snapshot",
      [c.old for c in diff.changes if c.element_id == 1002] == [900])
check("an Excel resave keeps the snapshot readable",
      sorted(snap.originals) == [1001, 1002, 1003])

# a number typed over a text cell, and a text cell retyped identically
edited2 = edit(original, os.path.join(tmp, "edited2.xlsx"), [
    (u"D101", 1, u"D101"),        # retyped, same value: not a change
    (u"D102", 3, 900.0),          # float, same value: not a change
])
snap2, visible2 = snapshot_of(edited2)
check("retyping the same value is not a change",
      not rt.find_changes(snap2, visible2).changes)

# a mark that looks like a number comes back as a number
edited3 = edit(original, os.path.join(tmp, "edited3.xlsx"),
               [(u"D101", 1, 101)])
snap3, visible3 = snapshot_of(edited3)
changes3 = rt.find_changes(snap3, visible3).changes
check("101 typed over a text mark is a change to the text '101'",
      len(changes3) == 1
      and rt.as_text_value(changes3[0].new) == u"101", str(changes3))

# deleted rows are ignored, never turned into deletions
edited4 = edit(original, os.path.join(tmp, "edited4.xlsx"), [],
               delete_rows=[u"D103"])
snap4, visible4 = snapshot_of(edited4)
diff4 = rt.find_changes(snap4, visible4)
check("a deleted row is reported as missing and nothing else",
      diff4.rows_missing == 1 and not diff4.changes,
      "{0} {1}".format(diff4.rows_missing, diff4.changes))

# a copied row is ambiguous and must be set aside, not guessed at
edited5 = edit(original, os.path.join(tmp, "edited5.xlsx"),
               [(u"D101", 1, u"X")], copy_row=u"D102")
snap5, visible5 = snapshot_of(edited5)
diff5 = rt.find_changes(snap5, visible5)
check("a row copied in Excel is ignored as a duplicate id",
      diff5.duplicates == [1002], str(diff5.duplicates))
check("the original edit alongside it still comes through",
      [(c.element_id, c.new) for c in diff5.changes] == [(1001, u"X")],
      str(diff5.changes))

# sorting must not detach a row from its element
wb6 = load_workbook(original)
ws6 = wb6["Doors"]
r1, r2 = find_row(ws6, u"D101"), find_row(ws6, u"D102")
for c in range(1, 7):
    a, b = ws6.cell(row=r1, column=c).value, ws6.cell(row=r2, column=c).value
    ws6.cell(row=r1, column=c, value=b)
    ws6.cell(row=r2, column=c, value=a)
sorted_path = os.path.join(tmp, "sorted.xlsx")
wb6.save(sorted_path)
snap6, visible6 = snapshot_of(sorted_path)
check("swapping two rows is not a change: rows are matched by id",
      not rt.find_changes(snap6, visible6).changes)

# --- 3. things that are not round trip workbooks ------------------------

plain = os.path.join(tmp, "plain.xlsx")
wb7 = load_workbook(original)
del wb7[rt.SNAPSHOT_SHEET]
wb7.save(plain)
try:
    rt.read_snapshot(xlsxread.read_workbook(plain))
    refused = False
except rt.NotARoundtripWorkbook:
    refused = True
check("a workbook with no snapshot sheet is refused", refused)

# a table that cannot round trip writes no hidden parts at all
bare = make_table()
bare.fields = None
bare_path = os.path.join(tmp, "bare.xlsx")
writer.write_workbook(bare, bare_path)
wb8 = load_workbook(bare_path)
check("a table that cannot round trip carries no snapshot or id column",
      wb8.sheetnames == ["Doors"]
      and not wb8["Doors"].column_dimensions["F"].hidden)

# --- 4. value rules ------------------------------------------------------

check("same_value: blank and None match", rt.same_value(None, u""))
check("same_value: 900 and 900.0 match", rt.same_value(900, 900.0))
check("same_value: 900 and 901 differ", not rt.same_value(900, 901))
check("same_value: text '12' and number 12 match", rt.same_value(u"12", 12))
check("same_value: surrounding spaces are not a change",
      rt.same_value(u" EI30 ", u"EI30"))
check("same_value: a clear is a change", not rt.same_value(u"EI30", None))
check("number_text: 101.0 is '101'", rt.number_text(101.0) == u"101")
check("number_text: 1.5 is '1.5'", rt.number_text(1.5) == u"1.5")
check("as_number reads a decimal comma", rt.as_number(u"30,5") == 30.5)
check("as_number drops a unit", rt.as_number(u"30 m²") == 30)
check("as_number refuses words", rt.as_number(u"wide") is None)
check("as_yes_no: Yes, Já, Nei, 1, 0",
      [rt.as_yes_no(v) for v in (u"Yes", u"Já", u"Nei", 1, 0)]
      == [1, 1, 0, 1, 0])
check("as_yes_no refuses 2", rt.as_yes_no(2) is None)


# --- 5. the applier against a mocked Revit ------------------------------

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


class CheckoutStatus(object):
    OwnedByOtherUser = "other"
    NotOwned = "none"


class ForgeTypeId(object):
    def __init__(self, text):
        self.TypeId = text


class UnitUtils(object):
    @staticmethod
    def ConvertToInternalUnits(value, unit):
        if "millimeters" in unit.TypeId:
            return value / 304.8
        raise Exception("unknown unit " + unit.TypeId)


class FakeParameter(object):
    def __init__(self, pid, name, storage, value, read_only=False,
                 refuse=False):
        self.Id = ElementId(pid)
        self.Name = name
        self.StorageType = storage
        self.IsReadOnly = read_only
        self.value = value
        self.refuse = refuse
        self.sets = 0

    def AsString(self):
        return self.value

    def AsInteger(self):
        return self.value

    def AsDouble(self):
        return self.value

    def Set(self, value):
        if self.refuse:
            return False
        self.value = value
        self.sets += 1
        return True


class FakeElement(object):
    def __init__(self, ident, parameters, group=-1):
        self.Id = ElementId(ident)
        self.Parameters = parameters
        self.GroupId = ElementId(group)

    def LookupParameter(self, name):
        for p in self.Parameters:
            if p.Name == name:
                return p
        return None


class FakeTransaction(object):
    commit_status = TransactionStatus.Committed
    opened = 0

    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        FakeTransaction.opened += 1

    def Commit(self):
        return FakeTransaction.commit_status

    def RollBack(self):
        pass


class WorksharingUtils(object):
    owners = {}

    @staticmethod
    def GetCheckoutStatus(doc, element_id):
        return WorksharingUtils.owners.get(element_id.Value, "none")


DB = types.SimpleNamespace(
    ElementId=ElementId, StorageType=StorageType,
    TransactionStatus=TransactionStatus, Transaction=FakeTransaction,
    CheckoutStatus=CheckoutStatus, WorksharingUtils=WorksharingUtils,
    ForgeTypeId=ForgeTypeId, UnitUtils=UnitUtils)


class FakeDoc(object):
    IsWorkshared = True

    def __init__(self, elements):
        self.elements = dict((e.Id.Value, e) for e in elements)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)


def fresh_model():
    """Three doors, each with a Mark, a Width and a Fire rating."""
    return FakeDoc([
        FakeElement(1001, [
            FakeParameter(-1001, u"Mark", StorageType.String, u"D101"),
            FakeParameter(-1002, u"Width", StorageType.Double, 1000 / 304.8),
        ]),
        FakeElement(1002, [
            FakeParameter(-1001, u"Mark", StorageType.String, u"D102"),
            FakeParameter(-1002, u"Width", StorageType.Double, 900 / 304.8),
        ]),
        FakeElement(1003, [
            FakeParameter(-1001, u"Mark", StorageType.String, u"D103"),
            FakeParameter(-1002, u"Width", StorageType.Double, 800.5 / 304.8),
        ]),
    ])


def param(doc, element_id, name):
    return doc.elements[element_id].LookupParameter(name)


MARK = rt.FieldSpec(0, u"Mark", rt.KIND_INSTANCE, -1001, u"Mark")
WIDTH = rt.FieldSpec(2, u"Width", rt.KIND_INSTANCE, -1002, u"Width",
                     u"autodesk.unit.unit:millimeters-1.0.1")
FIRE = rt.FieldSpec(3, u"Fire rating", rt.KIND_TYPE, -1003, u"Fire")
CALC = rt.FieldSpec(4, u"Level", rt.KIND_OTHER, None, u"Level")


def run(doc, changes, status=TransactionStatus.Committed):
    FakeTransaction.commit_status = status
    plan = applier.plan_changes(DB, doc, changes)
    return plan, applier.execute_plan(DB, doc, plan)


# a real edit writes, and only the edited parameters are touched
doc = fresh_model()
plan, result = run(doc, [
    rt.Change(1001, 5, MARK, u"D101", u"D101-A"),
    rt.Change(1002, 6, WIDTH, 900, 950),
])
check("a text edit is written",
      param(doc, 1001, u"Mark").value == u"D101-A")
check("a number edit is converted from millimetres to internal units",
      abs(param(doc, 1002, u"Width").value - 950 / 304.8) < 1e-12,
      str(param(doc, 1002, u"Width").value))
check("untouched parameters are never Set",
      param(doc, 1001, u"Width").sets == 0
      and param(doc, 1003, u"Mark").sets == 0
      and param(doc, 1002, u"Mark").sets == 0)
check("the result says both were written and committed",
      len(result.written) == 2 and result.committed and not result.failed)

# type and calculated columns are refused, loudly
doc = fresh_model()
plan, result = run(doc, [
    rt.Change(1001, 5, FIRE, None, u"EI90"),
    rt.Change(1001, 5, CALC, u"E01", u"E02"),
])
reasons = sorted(s[1] for s in plan.skipped)
check("a type parameter edit is skipped with its reason",
      rt.SKIP_TYPE in reasons, str(reasons))
check("a calculated column edit is skipped with its reason",
      rt.SKIP_OTHER in reasons, str(reasons))
check("nothing is written when nothing is writable",
      not result.written and result.committed)

# a changed model value is a conflict, and is not overwritten
doc = fresh_model()
param(doc, 1001, u"Mark").value = u"CHANGED-IN-REVIT"
plan, result = run(doc, [rt.Change(1001, 5, MARK, u"D101", u"D101-A")])
check("a text value changed in the model since the export is a conflict",
      plan.skipped and plan.skipped[0][1] == rt.SKIP_CONFLICT
      and param(doc, 1001, u"Mark").value == u"CHANGED-IN-REVIT",
      str(plan.skipped))

# a value that is already right is not rewritten
doc = fresh_model()
param(doc, 1001, u"Mark").value = u"D101"
plan, result = run(doc, [rt.Change(1001, 5, MARK, u"D101", u"D101")])
check("a value the model already holds is counted, not written",
      len(plan.already) == 1 and param(doc, 1001, u"Mark").sets == 0,
      str(plan.skipped))
doc = fresh_model()
plan, result = run(doc, [rt.Change(1001, 5, MARK, u"D101", u"D101 ")])
check("a trailing space is not a write",
      param(doc, 1001, u"Mark").sets == 0 and len(plan.already) == 1,
      str(plan.skipped))

# groups, ownership, read only, missing element, missing parameter
doc = fresh_model()
doc.elements[1001].GroupId = ElementId(55)
WorksharingUtils.owners = {1002: "other"}
param(doc, 1003, u"Mark").IsReadOnly = True
plan, result = run(doc, [
    rt.Change(1001, 5, MARK, u"D101", u"X"),
    rt.Change(1002, 6, MARK, u"D102", u"X"),
    rt.Change(1003, 7, MARK, u"D103", u"X"),
    rt.Change(9999, 8, MARK, u"D999", u"X"),
])
WorksharingUtils.owners = {}
got = sorted(s[1] for s in plan.skipped)
check("group member, other user's element, read only and missing element "
      "are each skipped for their own reason",
      got == sorted([rt.SKIP_GROUP, rt.SKIP_OWNED, rt.SKIP_READ_ONLY,
                     rt.SKIP_GONE]), str(got))
check("none of them was written",
      not result.written
      and all(param(doc, i, u"Mark").sets == 0 for i in (1001, 1002, 1003)))

# number edge cases
doc = fresh_model()
plan, result = run(doc, [
    rt.Change(1001, 5, WIDTH, 1000, None),          # cleared
    rt.Change(1002, 6, WIDTH, 900, u"wide"),        # not a number
    rt.Change(1003, 7, WIDTH, 800.5, u"1 100,5"),   # decimal comma and space
])
got = sorted(s[1] for s in plan.skipped)
check("a cleared number and a word are refused",
      got == sorted([rt.SKIP_BLANK_NUMBER, rt.SKIP_BAD_VALUE]), str(got))
check("a decimal comma number is read as a number, never via a string Set",
      abs(param(doc, 1003, u"Width").value - 1100.5 / 304.8) < 1e-12)

# a number the model already holds is not rewritten
doc = fresh_model()
plan, result = run(doc, [rt.Change(1001, 5, WIDTH, 999, 1000)])
check("a number the model already holds is counted, not written",
      len(plan.already) == 1 and param(doc, 1001, u"Width").sets == 0,
      str(plan.skipped))

# a column with no known unit is not guessed at
nounit = rt.FieldSpec(2, u"Width", rt.KIND_INSTANCE, -1002, u"Width", u"")
doc = fresh_model()
plan, result = run(doc, [rt.Change(1001, 5, nounit, 1000, 1200)])
check("a numeric edit in a column of unknown unit is refused",
      plan.skipped and plan.skipped[0][1] == rt.SKIP_UNIT
      and param(doc, 1001, u"Width").sets == 0)

# a refused Set and a rolled back commit are failures, not successes
doc = fresh_model()
param(doc, 1001, u"Mark").refuse = True
plan, result = run(doc, [rt.Change(1001, 5, MARK, u"D101", u"X"),
                         rt.Change(1002, 6, MARK, u"D102", u"Y")])
check("a Set that returns False is reported as failed",
      len(result.failed) == 1 and result.failed[0][1] == rt.SKIP_REFUSED
      and len(result.written) == 1, str(result.failed))

doc = fresh_model()
plan, result = run(doc, [rt.Change(1001, 5, MARK, u"D101", u"X")],
                   status=TransactionStatus.RolledBack)
check("a transaction Revit rolls back is not reported as success",
      not result.committed and not result.written
      and result.failed and result.failed[0][1] == rt.SKIP_ROLLED_BACK,
      str(result.failed))
FakeTransaction.commit_status = TransactionStatus.Committed

# no transaction is opened for a plan with nothing to write
before = FakeTransaction.opened
run(fresh_model(), [rt.Change(1001, 5, FIRE, None, u"X")])
check("no transaction is opened when nothing is writable",
      FakeTransaction.opened == before)

# yes/no and whole number parameters
YESNO = rt.FieldSpec(0, u"Fire", rt.KIND_INSTANCE, -2001, u"Fire")
doc = FakeDoc([FakeElement(1, [
    FakeParameter(-2001, u"Fire", StorageType.Integer, 0)])])
plan, result = run(doc, [rt.Change(1, 5, YESNO, u"No", u"Já")])
check("an Icelandic Ja writes 1 to a yes/no parameter",
      param(doc, 1, u"Fire").value == 1, str(param(doc, 1, u"Fire").value))
doc = FakeDoc([FakeElement(1, [
    FakeParameter(-2001, u"Fire", StorageType.Integer, 4)])])
plan, result = run(doc, [rt.Change(1, 5, YESNO, 3, 5)])
check("a whole number conflict is caught",
      plan.skipped and plan.skipped[0][1] == rt.SKIP_CONFLICT)
plan, result = run(doc, [rt.Change(1, 5, YESNO, 4, 5.5)])
check("a fractional number is refused for a whole number parameter",
      plan.skipped and plan.skipped[0][1] == rt.SKIP_BAD_VALUE)

# a parameter found by name when the id does not match
byname = rt.FieldSpec(0, u"Mark", rt.KIND_INSTANCE, 123456, u"Mark")
doc = fresh_model()
plan, result = run(doc, [rt.Change(1001, 5, byname, u"D101", u"N")])
check("a parameter is found by name when the id does not match",
      param(doc, 1001, u"Mark").value == u"N")

# --- 6. the Revit side of the export, against a mock --------------------
#
# The point of this half: the original schedule is never edited, the
# temporary duplicate is always rolled back, and a schedule that cannot
# carry ids still exports instead of failing.

from avh_schedules import reader                          # noqa: E402

LOG = []


class SchedulableField(object):
    def __init__(self, pid, name=u"Other"):
        self.ParameterId = ElementId(pid)
        self._name = name

    def GetName(self, doc):
        return self._name


class Field(object):
    def __init__(self, heading, name, ftype, pid, hidden=False):
        self.ColumnHeading = heading
        self._name = name
        self.FieldType = ftype
        self.ParameterId = ElementId(pid)
        self.IsHidden = hidden

    def GetName(self):
        return self._name

    def GetFormatOptions(self):
        raise Exception("no format options on this mock")


class FakeDefinition(object):
    def __init__(self, fields, itemized=True, offers_id=True):
        self.fields = list(fields)
        self.IsItemized = itemized
        self.offers_id = offers_id
        self.added = []

    def GetFieldOrder(self):
        return list(range(len(self.fields)))

    def GetField(self, i):
        return self.fields[i]

    def GetSortGroupFields(self):
        return []

    def GetSchedulableFields(self):
        out = [SchedulableField(-1001)]
        if self.offers_id == "by name":
            # Revit 2025: the field exists but not under ID_PARAM's id
            out.append(SchedulableField(-1002100, u"Element Id"))
        elif self.offers_id:
            out.append(SchedulableField(-1))      # ID_PARAM
        return out

    def AddField(self, schedulable):
        LOG.append("add field")
        field = Field(u"Element Id", u"Element Id", u"Instance",
                      schedulable.ParameterId.Value)
        self.fields.append(field)
        self.added.append(field)
        return field


class FakeSchedule(object):
    """Serves tab delimited text, with ids when it has the id field."""

    def __init__(self, definition, doc, name=u"Doors", rows=None):
        self.Name = name
        self.Definition = definition
        self.Document = doc
        self.Id = ElementId(777)
        self.rows = rows or [(u"D101", u"Entrance", 1001),
                             (u"D102", u"Store", 1002)]
        self.exports = 0

    def Duplicate(self, option):
        LOG.append("duplicate")
        dup = type(self)(FakeDefinition(self.Definition.fields,
                                        self.Definition.IsItemized,
                                        self.Definition.offers_id),
                         self.Document, self.Name + u" Copy 1", self.rows)
        self.Document.created = dup
        return ElementId(900)

    def Export(self, folder, filename, options):
        self.exports += 1
        shown = [f for f in self.Definition.fields
                 if not f.IsHidden and getattr(f, "exported", True)]
        lines = [self.Name, u"\t".join(f.ColumnHeading for f in shown), u""]
        for mark, name, ident in self.rows:
            by_heading = {u"Mark": mark, u"Name": name,
                          rt.TEMP_HEADING: str(ident)}
            lines.append(u"\t".join(by_heading.get(f.ColumnHeading, u"x")
                                    for f in shown))
        with open(os.path.join(folder, filename), "w",
                  encoding="utf-8") as handle:
            handle.write(u"\n".join(lines))


class FakeGroup(object):
    def __init__(self, doc, name):
        pass

    def Start(self):
        LOG.append("group start")

    def RollBack(self):
        LOG.append("group rollback")


class ViewScheduleExportOptions(object):
    FieldDelimiter = None
    TextQualifier = None


class ExportTextQualifier(object):
    None_ = "none"


class BuiltInParameter(object):
    ID_PARAM = -1


class ViewDuplicateOption(object):
    Duplicate = "dup"


class RevitDoc(FakeDoc):
    Title = u"ELG_CC01"

    def __init__(self):
        FakeDoc.__init__(self, [])
        self.created = None

    def GetElement(self, element_id):
        if element_id.Value == 900:
            return self.created
        return FakeDoc.GetElement(self, element_id)


def install_db():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.Transaction = FakeTransaction
    db.TransactionGroup = FakeGroup
    db.TransactionStatus = TransactionStatus
    db.ViewDuplicateOption = ViewDuplicateOption
    db.ViewScheduleExportOptions = ViewScheduleExportOptions
    db.ExportTextQualifier = ExportTextQualifier
    db.BuiltInParameter = BuiltInParameter
    db.ElementId = lambda value: ElementId(int(value))
    for name, module in (("Autodesk", types.ModuleType("Autodesk")),
                         ("Autodesk.Revit", types.ModuleType("Autodesk.Revit")),
                         ("Autodesk.Revit.DB", db)):
        sys.modules[name] = module


def schedule_fields():
    return [Field(u"Mark", u"Mark", u"Instance", -1001),
            Field(u"Name", u"Room Name", u"Instance", 5001)]


install_db()
FakeTransaction.commit_status = TransactionStatus.Committed

doc6 = RevitDoc()
view = FakeSchedule(FakeDefinition(schedule_fields()), doc6)
LOG[:] = []
table = reader.read_schedule(view)
check("a schedule that can carry ids reads with them",
      table.can_roundtrip and table.roundtrip_note == u"",
      table.roundtrip_note)
check("each row carries its element id",
      [rt.row_id(table, r) for r in table.rows] == [1001, 1002],
      str(table.rows))
check("the id column is not among the columns",
      [c.name for c in table.columns] == [u"Mark", u"Name"])
check("the columns line up one to one with the schedule's fields",
      [f.param_id for f in table.fields] == [-1001, 5001]
      and [f.kind for f in table.fields] == [rt.KIND_INSTANCE] * 2)
check("the schedule's id and the model are recorded",
      table.meta["schedule_id"] == 777 and table.meta["model"] == u"ELG_CC01")
check("the field was added to a duplicate, never to the original",
      view.Definition.added == []
      and doc6.created.Definition.added != [])
check("the original schedule was never exported with the id field",
      view.exports == 0)
check("the temporary group is rolled back, once",
      LOG.count("group rollback") == 1 and LOG.count("group start") == 1,
      str(LOG))
check("the title is the real schedule's, not the duplicate's",
      table.title == u"Doors")


# the group is rolled back even when the export fails
class Boom(FakeSchedule):
    def Export(self, folder, filename, options):
        if self.Name.endswith("Copy 1"):
            raise Exception("export failed on the duplicate")
        FakeSchedule.Export(self, folder, filename, options)


doc6b = RevitDoc()
LOG[:] = []
view_b = Boom(FakeDefinition(schedule_fields()), doc6b)
table_b = reader.read_schedule(view_b)
check("a failing duplicate export still rolls the group back",
      LOG.count("group rollback") == 1, str(LOG))
check("and falls back to a plain export instead of failing",
      not table_b.can_roundtrip and len(table_b.rows) == 2
      and "export failed" in table_b.roundtrip_note, table_b.roundtrip_note)

# a schedule that is not itemized, or offers no id field, still exports
for label, definition in (
        ("not itemized", FakeDefinition(schedule_fields(), itemized=False)),
        ("no id field", FakeDefinition(schedule_fields(), offers_id=False))):
    LOG[:] = []
    t = reader.read_schedule(FakeSchedule(definition, RevitDoc()))
    check("{0}: still exports, says it cannot round trip".format(label),
          not t.can_roundtrip and len(t.rows) == 2 and t.roundtrip_note,
          t.roundtrip_note)
    check("{0}: nothing left open".format(label),
          LOG.count("group start") == LOG.count("group rollback"), str(LOG))

# a hidden field is not a column
hidden = schedule_fields() + [Field(u"Secret", u"Secret", u"Instance", 9,
                                    hidden=True)]
t = reader.read_schedule(FakeSchedule(FakeDefinition(hidden), RevitDoc()))
check("hidden fields are not counted as columns",
      t.can_roundtrip and len(t.fields) == 2)

# fields and columns that do not line up are refused, not guessed
dropped = Field(u"Extra", u"Extra", u"Instance", 8)
dropped.exported = False          # Revit's export leaves this one out
short = FakeDefinition(schedule_fields() + [dropped])
t = reader.read_schedule(FakeSchedule(short, RevitDoc()))
check("a mismatch between fields and columns is not round trippable",
      not t.can_roundtrip and t.roundtrip_note)

# an old Revit with none of this API still exports
old_db = types.ModuleType("Autodesk.Revit.DB")
old_db.ViewScheduleExportOptions = ViewScheduleExportOptions
old_db.ExportTextQualifier = ExportTextQualifier
sys.modules["Autodesk.Revit.DB"] = old_db
t = reader.read_schedule(FakeSchedule(FakeDefinition(schedule_fields()),
                                      RevitDoc()))
check("a Revit without the transaction API still exports",
      not t.can_roundtrip and len(t.rows) == 2, t.roundtrip_note)

# --- 7. no Element Id field: rows matched to elements by value ------------
#
# The case on Revit 2025, where the first real run found no Element Id
# field among 1289 offered.


class TypedElement(FakeElement):
    def GetTypeId(self):
        return ElementId(-1)


class FakeCollector(object):
    shown = []

    def __init__(self, doc, view_id):
        pass

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        return iter(FakeCollector.shown)


def string_param(pid, name, value):
    return FakeParameter(pid, name, StorageType.String, value)


def room(ident, mark, name):
    return TypedElement(ident, [string_param(-1001, u"Mark", mark),
                                string_param(5001, u"Room Name", name)])


install_db()
db_m = sys.modules["Autodesk.Revit.DB"]
db_m.FilteredElementCollector = FakeCollector
db_m.StorageType = StorageType
db_m.ForgeTypeId = ForgeTypeId
db_m.UnitUtils = UnitUtils
FakeCollector.shown = [room(1001, u"D101", u"Entrance"),
                       room(1002, u"D102", u"Store")]

LOG[:] = []
view_m = FakeSchedule(FakeDefinition(schedule_fields(), offers_id=False),
                      RevitDoc())
table_m = reader.read_schedule(view_m)
check("no Element Id field: the rows are matched to elements instead",
      table_m.can_roundtrip, table_m.roundtrip_note)
check("each row got the right element",
      [rt.row_id(table_m, r) for r in table_m.rows] == [1001, 1002],
      str(table_m.rows))
check("the note says it matched by value and how many",
      u"matched to elements by their values" in table_m.roundtrip_note
      and u"2 of 2" in table_m.roundtrip_note, table_m.roundtrip_note)
check("the temporary group was never needed for the matching route",
      LOG.count("group start") == LOG.count("group rollback"), str(LOG))

# an unmatched row is kept, exported, and simply not importable
FakeCollector.shown = [room(1001, u"D101", u"Entrance")]
table_u = reader.read_schedule(
    FakeSchedule(FakeDefinition(schedule_fields(), offers_id=False),
                 RevitDoc()))
check("a row with no element is still exported, without an id",
      len(table_u.rows) == 2
      and [rt.row_id(table_u, r) for r in table_u.rows] == [1001, None],
      str(table_u.rows))
check("and the note counts it", u"1 of 2" in table_u.roundtrip_note,
      table_u.roundtrip_note)

# nothing at all matches: not round trippable, and says why
FakeCollector.shown = [room(5, u"ZZZ", u"Nowhere")]
table_n = reader.read_schedule(
    FakeSchedule(FakeDefinition(schedule_fields(), offers_id=False),
                 RevitDoc()))
check("no row matching anything is reported, not shipped as importable",
      not table_n.can_roundtrip and u"no row could be matched"
      in table_n.roundtrip_note, table_n.roundtrip_note)

# the workbook written from a matched table is importable
import tempfile as _tf                                     # noqa: E402
FakeCollector.shown = [room(1001, u"D101", u"Entrance"),
                       room(1002, u"D102", u"Store")]
table_w = reader.read_schedule(
    FakeSchedule(FakeDefinition(schedule_fields(), offers_id=False),
                 RevitDoc()))
wdir = _tf.mkdtemp(prefix="avh_match_")
wpath = os.path.join(wdir, "m.xlsx")
writer.write_workbook(table_w, wpath)
snap_w, vis_w = rt.read_snapshot(xlsxread.read_workbook(wpath))
check("a matched export writes a snapshot with both elements",
      sorted(snap_w.originals) == [1001, 1002], str(sorted(snap_w.originals)))
shutil.rmtree(wdir, ignore_errors=True)

# --- 8. the stamped Element ID parameter, verified -----------------------
#
# Revit 2025 has no Element Id field, but the model has an Element ID
# parameter that Stamp Element IDs (or a Dynamo script) filled in. It is
# only a pasted number, so every id read from it is checked.

FakeCollector.shown = [room(1001, u"D101", u"Entrance"),
                       room(1002, u"D102", u"Store"),
                       room(1003, u"D103", u"Plant")]


def stamped_export(stamps, offers="by name"):
    rows = [(u"D101", u"Entrance", stamps[0]),
            (u"D102", u"Store", stamps[1]),
            (u"D103", u"Plant", stamps[2])]
    return reader.read_schedule(FakeSchedule(
        FakeDefinition(schedule_fields(), offers_id=offers), RevitDoc(),
        rows=rows))


t = stamped_export(["1001", "1002", "1003"])
check("a clean stamped parameter identifies every row",
      t.can_roundtrip
      and [rt.row_id(t, r) for r in t.rows] == [1001, 1002, 1003],
      t.roundtrip_note)
check("the note says the ids came from the parameter",
      u"3 of 3 rows identified by the Element ID parameter" in
      t.roundtrip_note, t.roundtrip_note)

# a copied element keeps the id it was copied from: both rows lose it, and
# are then recovered by value rather than trusted
t = stamped_export(["1001", "1001", "1003"])
check("an id found on two rows is not trusted for either",
      u"1 of 3 rows identified by the Element ID parameter" in
      t.roundtrip_note, t.roundtrip_note)
check("the rows that lost their id are recovered by value",
      [rt.row_id(t, r) for r in t.rows] == [1001, 1002, 1003]
      and u"2 more by their values" in t.roundtrip_note, str(t.rows))

# an id that belongs to an element the schedule does not show
t = stamped_export(["9999", "1002", "1003"])
check("an id for an element the schedule does not show is dropped",
      rt.row_id(t, t.rows[0]) == 1001 and
      u"2 of 3 rows identified" in t.roundtrip_note, t.roundtrip_note)

# an unstamped element (created since the last run) has a blank
t = stamped_export(["1001", "1002", ""])
check("a blank stamp is recovered by value, never guessed",
      [rt.row_id(t, r) for r in t.rows] == [1001, 1002, 1003]
      and u"2 of 3 rows identified" in t.roundtrip_note, t.roundtrip_note)

# nothing stamped and nothing matchable: refused, not shipped importable
FakeCollector.shown = [room(5, u"ZZZ", u"Nowhere")]
t = stamped_export(["", "", ""])
check("no usable stamp and no match is not round trippable",
      not t.can_roundtrip and t.roundtrip_note, t.roundtrip_note)

# the stamp must not be a different row's id: swapped stamps still verify
# against the shown set, so identical-looking rows are the residual risk
FakeCollector.shown = [room(1001, u"D101", u"Entrance"),
                       room(1002, u"D102", u"Store"),
                       room(1003, u"D103", u"Plant")]

# --- report ---------------------------------------------------------------

shutil.rmtree(tmp, ignore_errors=True)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    if not ok:
        print("FAIL  " + name + ("   [" + detail + "]" if detail else ""))
print("{0} checks, {1} failed".format(len(results), len(failed)))

# the harness must be able to fail
assert not rt.same_value(1, 2)
if failed:
    sys.exit(1)
print("PASS")
