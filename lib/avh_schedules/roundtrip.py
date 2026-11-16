# -*- coding: utf-8 -*-
"""
Everything about getting an edited workbook back into Revit that does not
need Revit: what an export remembers, and what counts as a change.

## The idea

An export is a report, and a report does not say which element each row
came from. So every workbook now carries two things the person never has
to look at:

* a hidden column, right of the table, holding the Revit element id of
  each row. It travels with its row, so sorting or filtering in Excel
  cannot detach a row from its element.
* a hidden sheet, AVH_Roundtrip, holding a snapshot of every cell exactly
  as it was exported, plus what each column is (which parameter, instance
  or type, which unit).

## What counts as a change

A cell is changed when it differs from the snapshot, and only then. Not
"differs from what Revit holds now": the display text Revit gave at export
is rounded, so comparing against the live model would flag every rounded
value as an edit and rewrite half the schedule. Comparing the sheet with
its own snapshot flags exactly what the person touched, nothing more.

The price is that a change made in Revit between export and import is
invisible to this module. The applier catches that separately, for the
value types where it can, and says so when it cannot.

Pure Python, no Revit. Runs on IronPython 2.7 and CPython 3 alike.
"""

from .compat import to_text
from .model import parse_measurement

SNAPSHOT_SHEET = u"AVH_Roundtrip"
FORMAT_VERSION = 1

# The heading given to the temporary Element Id field Revit exports. It
# must not clash with anything a person would call a column.
TEMP_HEADING = u"AVH_ELEMENT_ID"

# The heading of the hidden id column in the workbook itself.
ID_HEADING = u"AVH ID"

KIND_INSTANCE = u"instance"
KIND_TYPE = u"type"
KIND_OTHER = u"other"

# Why a change was not written. Each is a whole sentence fragment because
# it goes straight into the report.
SKIP_TYPE = u"type parameter: it would change every element of that type"
SKIP_OTHER = u"calculated, count or room column: not an editable parameter"
SKIP_GONE = u"element is no longer in the model"
SKIP_GROUP = u"element is inside a group"
SKIP_NO_PARAMETER = u"parameter not found on the element"
SKIP_READ_ONLY = u"parameter is read only"
SKIP_OWNED = u"element is checked out by another user"
SKIP_CONFLICT = u"changed in the model since the export"
SKIP_BAD_VALUE = u"value does not fit the parameter"
SKIP_BLANK_NUMBER = u"cleared a number: not written, so it cannot zero it"
SKIP_UNIT = u"unit of the column is unknown"
SKIP_STORAGE = u"parameter holds a kind of value this tool does not write"
SKIP_REFUSED = u"Revit refused the value"
SKIP_UNKNOWN_ID = u"row has no matching element in the export"
SKIP_DUPLICATE = u"element appears on more than one row"
SKIP_ROLLED_BACK = u"Revit rolled the whole change back"


class FieldSpec(object):
    """What one column is, as Revit knew it at export."""

    def __init__(self, index, name, kind=KIND_OTHER, param_id=None,
                 param_name=u"", unit_id=u""):
        self.index = index
        self.name = name
        self.kind = kind
        self.param_id = param_id
        self.param_name = param_name
        self.unit_id = unit_id

    def __repr__(self):
        return "<FieldSpec {0} {1!r} {2}>".format(
            self.index, self.name, self.kind)


class Change(object):
    """One edited cell."""

    def __init__(self, element_id, row, field, old, new):
        self.element_id = element_id
        self.row = row            # the sheet row, for messages
        self.field = field
        self.old = old
        self.new = new


class Snapshot(object):
    """The hidden sheet, read back."""

    def __init__(self):
        self.schedule_name = u""
        self.schedule_id = None
        self.model = u""
        self.exported = u""
        self.n_cols = 0
        self.id_col = 0           # 1 based column of the ids in the sheet
        self.fields = []
        self.originals = {}       # element id -> list of values


# --- values ------------------------------------------------------------

def _is_blank(value):
    return value is None or to_text(value).strip() == u""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number_text(number):
    """A number as the text a person would have typed, with no locale.

    An integral float is written without a decimal point, because a Mark
    typed as 101 comes back from Excel as the number 101 and must read as
    '101', not '101.0'. A period is always the decimal mark here, which is
    right for the text of an identifier and which no code path turns back
    into a number.
    """
    if isinstance(number, bool):
        return to_text(number)
    if isinstance(number, int):
        return u"{0}".format(number)
    if number == int(number) and abs(number) < 1e15:
        return u"{0}".format(int(number))
    return to_text(repr(number))


def same_value(a, b):
    """Whether two cell values are the same to a person looking at them."""
    if _is_blank(a) and _is_blank(b):
        return True
    if _is_blank(a) or _is_blank(b):
        return False
    if _is_number(a) and _is_number(b):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if _is_number(a):
        a = number_text(a)
    if _is_number(b):
        b = number_text(b)
    return to_text(a).strip() == to_text(b).strip()


YES_WORDS = (u"yes", u"y", u"true", u"1", u"ja", u"já", u"x")
NO_WORDS = (u"no", u"n", u"false", u"0", u"nei")


def as_yes_no(value):
    """1 or 0 for a yes or no, else None. Icelandic and English."""
    if value is None or isinstance(value, bool):
        return None if value is None else (1 if value else 0)
    if _is_number(value):
        if value == 1:
            return 1
        if value == 0:
            return 0
        return None
    text = to_text(value).strip().lower()
    if text in YES_WORDS:
        return 1
    if text in NO_WORDS:
        return 0
    return None


def as_number(value):
    """A number from a cell, or None.

    A real number passes straight through. Text goes via parse_measurement,
    which reads a decimal comma and drops a unit, so '30,5 m2' is 30.5.
    """
    if value is None or isinstance(value, bool):
        return None
    if _is_number(value):
        return value
    number, _unit = parse_measurement(value)
    return number if _is_number(number) else None


def as_text_value(value):
    """What goes into a text parameter."""
    if _is_number(value):
        return number_text(value)
    return to_text(value).strip()


# --- the id column -----------------------------------------------------

def extract_id_column(table, heading=TEMP_HEADING):
    """Take the id column out of a parsed export.

    Revit exported it as the last column. It is removed from table.columns
    and left in each row as the trailing value, recorded as table.id_index,
    so every consumer that walks the columns is unaffected.
    Returns True when found.
    """
    column = None
    for candidate in table.columns:
        if to_text(candidate.name).strip() == heading:
            column = candidate
    if column is None:
        return False
    if column.index != len(table.columns) - 1:
        raise ValueError(u"the id column is not the last one")
    table.columns.pop()
    table.id_index = column.index
    return True


def row_id(table, row):
    """The element id carried by a table row, or None."""
    if table.id_index is None or table.id_index >= len(row):
        return None
    value = as_number(row[table.id_index])
    if value is None or value != int(value):
        return None
    return int(value)


# --- writing the snapshot ----------------------------------------------

def add_snapshot_sheet(book, table):
    """The hidden AVH_Roundtrip sheet for a table that can round trip."""
    n_cols = len(table.columns)
    sheet = book.add_sheet(SNAPSHOT_SHEET)
    sheet.hidden = True

    meta = table.meta or {}
    sheet.cell(1, 1, u"AVH_ROUNDTRIP")
    sheet.cell(1, 2, FORMAT_VERSION)
    sheet.cell(2, 1, u"SCHEDULE")
    sheet.cell(2, 2, to_text(table.title))
    sheet.cell(2, 3, meta.get("schedule_id"))
    sheet.cell(3, 1, u"MODEL")
    sheet.cell(3, 2, to_text(meta.get("model", u"")))
    sheet.cell(4, 1, u"EXPORTED")
    sheet.cell(4, 2, to_text(meta.get("exported", u"")))
    sheet.cell(5, 1, u"IDCOL")
    sheet.cell(5, 2, n_cols + 1)
    sheet.cell(6, 1, u"NCOLS")
    sheet.cell(6, 2, n_cols)

    row = 8
    for spec in table.fields:
        sheet.cell(row, 1, u"COL")
        sheet.cell(row, 2, spec.index)
        sheet.cell(row, 3, to_text(spec.name))
        sheet.cell(row, 4, spec.kind)
        sheet.cell(row, 5, spec.param_id)
        sheet.cell(row, 6, to_text(spec.unit_id))
        sheet.cell(row, 7, to_text(spec.param_name))
        row += 1

    for data_row in table.rows:
        ident = row_id(table, data_row)
        if ident is None:
            continue
        sheet.cell(row, 1, u"ROW")
        sheet.cell(row, 2, ident)
        for col in range(n_cols):
            value = data_row[col] if col < len(data_row) else None
            if value is not None:
                sheet.cell(row, 3 + col, value)
        row += 1
    return sheet


def add_id_column(sheet, table, row_ids_by_excel_row, header_row):
    """The hidden id column in the visible sheet."""
    id_col = len(table.columns) + 1
    sheet.cell(header_row, id_col, ID_HEADING)
    for excel_row, ident in row_ids_by_excel_row:
        sheet.cell(excel_row, id_col, ident)
    sheet.hide_column(id_col)


# --- reading the snapshot ----------------------------------------------

class NotARoundtripWorkbook(Exception):
    """The workbook has no snapshot, so there is nothing to compare with."""


def read_snapshot(sheets):
    """(Snapshot, visible Sheet) from the sheets of a workbook."""
    snapshot_sheet = None
    visible = None
    for sheet in sheets:
        if sheet.name == SNAPSHOT_SHEET:
            snapshot_sheet = sheet
        elif visible is None:
            visible = sheet
    if snapshot_sheet is None or visible is None:
        raise NotARoundtripWorkbook(
            u"This workbook has no AVH_Roundtrip sheet. Only workbooks "
            u"exported by Export Schedule can be imported, and the sheet "
            u"must not have been deleted.")
    if snapshot_sheet.get(1, 1) != u"AVH_ROUNDTRIP":
        raise NotARoundtripWorkbook(u"The AVH_Roundtrip sheet is not "
                                    u"in the expected format.")
    version = snapshot_sheet.get(1, 2)
    if version != FORMAT_VERSION:
        raise NotARoundtripWorkbook(
            u"The workbook was exported in format {0}; this version of "
            u"the tool reads format {1}.".format(version, FORMAT_VERSION))

    snap = Snapshot()
    snap.schedule_name = to_text(snapshot_sheet.get(2, 2))
    schedule_id = snapshot_sheet.get(2, 3)
    snap.schedule_id = int(schedule_id) if _is_number(schedule_id) else None
    snap.model = to_text(snapshot_sheet.get(3, 2))
    snap.exported = to_text(snapshot_sheet.get(4, 2))
    snap.id_col = int(snapshot_sheet.get(5, 2) or 0)
    snap.n_cols = int(snapshot_sheet.get(6, 2) or 0)
    if not snap.id_col or not snap.n_cols:
        raise NotARoundtripWorkbook(u"The AVH_Roundtrip sheet is damaged.")

    for r in range(8, snapshot_sheet.max_row + 1):
        tag = snapshot_sheet.get(r, 1)
        if tag == u"COL":
            pid = snapshot_sheet.get(r, 5)
            snap.fields.append(FieldSpec(
                index=int(snapshot_sheet.get(r, 2)),
                name=to_text(snapshot_sheet.get(r, 3)),
                kind=to_text(snapshot_sheet.get(r, 4)),
                param_id=int(pid) if _is_number(pid) else None,
                unit_id=to_text(snapshot_sheet.get(r, 6)),
                param_name=to_text(snapshot_sheet.get(r, 7))))
        elif tag == u"ROW":
            ident = snapshot_sheet.get(r, 2)
            if not _is_number(ident):
                continue
            snap.originals[int(ident)] = [
                snapshot_sheet.get(r, 3 + c) for c in range(snap.n_cols)]
    if len(snap.fields) != snap.n_cols:
        raise NotARoundtripWorkbook(u"The AVH_Roundtrip sheet is damaged.")
    return snap, visible


# --- finding the changes ------------------------------------------------

class Diff(object):
    def __init__(self):
        self.changes = []
        self.unknown = []         # (sheet row, id) not in the snapshot
        self.duplicates = []      # ids found on more than one row
        self.rows_seen = 0
        self.rows_missing = 0     # snapshot rows no longer in the sheet


def find_changes(snapshot, sheet):
    """Compare every row of the edited sheet with its snapshot."""
    diff = Diff()
    seen = {}
    for r in range(1, sheet.max_row + 1):
        raw = sheet.get(r, snapshot.id_col)
        if not _is_number(raw) or raw != int(raw):
            continue
        ident = int(raw)
        seen.setdefault(ident, []).append(r)

    for ident, rows in sorted(seen.items()):
        if len(rows) > 1:
            diff.duplicates.append(ident)
            continue
        r = rows[0]
        if ident not in snapshot.originals:
            diff.unknown.append((r, ident))
            continue
        diff.rows_seen += 1
        original = snapshot.originals[ident]
        for c in range(snapshot.n_cols):
            now = sheet.get(r, c + 1)
            if same_value(original[c], now):
                continue
            diff.changes.append(Change(ident, r, snapshot.fields[c],
                                       original[c], now))

    diff.rows_missing = len([i for i in snapshot.originals if i not in seen])
    return diff
