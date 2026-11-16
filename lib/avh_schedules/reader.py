# -*- coding: utf-8 -*-
"""
Get a ScheduleTable out of Revit.

Approach note, because this differs from the obvious one. The direct
route is ViewSchedule.GetTableData(), which hands back merged cell
geometry. It is also the route whose row and column semantics shift
between Revit versions, and it cannot be exercised outside Revit at all.

This module instead drives ViewSchedule.Export() with a tab field
delimiter and parses the result. Three reasons:

1. A tab delimiter removes the decimal comma collision that corrupts
   AVH's comma delimited exports, at the source.
2. The resulting layout is the one already validated against four real
   a live project exports, including the multi level door schedule headers
   and Revit's rendered group footer lines.
3. parse_delimited() below is a pure function, so the whole parsing path
   is testable on CSV fixtures without opening Revit. Given that the
   Revit API surface here cannot be tested before delivery, shrinking it
   to Export() plus one metadata call is the safer trade.

The one thing lost is explicit merged cell geometry, which is recovered
from the blank cell pattern Revit writes for grouped headers. That
reconstruction is verified against the real door schedules in tests.
"""

import datetime
import os
import shutil
import tempfile

from .compat import element_id_int, is_number_text, read_text, to_text
from .model import (
    Column, ScheduleTable, apply_parsing, confirm_render_row,
    detect_column_types, find_level_column, is_render_candidate,
)
from .applier import find_parameter
from .matching import UNKNOWN, match_rows
from .stamp import is_stamp_name
from .roundtrip import (
    KIND_INSTANCE, KIND_OTHER, KIND_TYPE, TEMP_HEADING, FieldSpec,
    extract_id_column, row_id,
)

TAB = "\t"


# --- pure parsing ------------------------------------------------------

def _split_lines(text, delimiter):
    rows = []
    for line in text.splitlines():
        if line.strip() == "" and not rows:
            continue
        rows.append(line.split(delimiter))
    return rows


def _find_header_depth(rows, max_scan=8):
    """Header rows run from row 1 up to the first fully blank row.

    Revit writes a blank separator line between the column headers and
    the data. Verified on all four a live project exports: one header row
    for the room schedule, three for the door schedules.
    """
    for i in range(1, min(len(rows), max_scan)):
        if all(cell.strip() == "" for cell in rows[i]):
            return i - 1, i + 1
    return 1, 2


def _build_columns(header_rows, n_cols):
    """Rebuild the merged header from Revit's blank cell pattern.

    A blank in a header row means 'same as the cell to my left', which is
    how Revit renders a horizontal merge. Level two only forward fills
    inside its own level one span, so a group label never bleeds into the
    next group.
    """
    def norm(rows, i, j):
        if i >= len(rows) or j >= len(rows[i]):
            return None
        value = rows[i][j].strip()
        return value or None

    level1 = [norm(header_rows, 0, j) for j in range(n_cols)]
    for j in range(1, n_cols):
        if level1[j] is None:
            level1[j] = level1[j - 1]

    level2 = [norm(header_rows, 1, j) for j in range(n_cols)]
    span_start = 0
    for j in range(1, n_cols + 1):
        if j == n_cols or level1[j] != level1[span_start]:
            for k in range(span_start + 1, j):
                if level2[k] is None:
                    level2[k] = level2[k - 1]
            span_start = j

    level3 = [norm(header_rows, 2, j) for j in range(n_cols)]

    return [Column(j, level1[j], level2[j], level3[j]) for j in range(n_cols)]


def _strip_render_rows(data_rows):
    """Remove Revit's rendered group and grand total lines.

    Two passes, because a row cannot be judged in isolation. The first
    pass separates rows merely shaped like group lines from certain data
    rows, and collects the values those certain rows hold outside the
    first column. The second pass confirms each candidate against that
    evidence, and anything unconfirmed goes back in as data.

    Returns (real_rows, group_labels, grand_total_label). The labels are
    kept because they reveal which column Revit grouped on, and what it
    called the grand total, which is 'Samtals' on the Icelandic
    schedules and 'Total' on the industrial one.
    """
    candidates = []   # (position, text)
    certain = []      # (position, row)

    for position, row in enumerate(data_rows):
        if not any(cell.strip() for cell in row):
            continue
        if is_render_candidate(row):
            candidates.append((position, row[0].strip()))
        else:
            certain.append((position, row))

    # values any real row carries outside its first column, which is what
    # a Revit group header line repeats
    known_values = set()
    for _position, row in certain:
        for cell in row[1:]:
            text = cell.strip()
            if text:
                known_values.add(text)

    kept = list(certain)
    labels = []
    for position, text in candidates:
        is_render, label = confirm_render_row(text, known_values)
        if is_render:
            labels.append((position, label))
        else:
            row = data_rows[position]
            kept.append((position, row))

    kept.sort(key=lambda pair: pair[0])
    real = [row for _position, row in kept]

    # Revit prints the grand total last, so the final label is not a group
    grand_total_label = None
    if labels:
        grand_total_label = labels.pop()[1]

    return real, [label for _position, label in labels], grand_total_label


def _infer_group_column(columns, rows, group_labels):
    """Find which column Revit grouped on, from its own footer labels.

    A group footer reads '%QQC001 / 980 x 2080mm / R: 9', and the rows it
    covers carry '%QQC001 / 980 x 2080mm / R' in the grouped column. So
    the column whose values match the most labels is the group column.
    """
    if not group_labels or not rows:
        return None

    wanted = set(group_labels)
    best_col, best_hits = None, 0
    for col in columns:
        values = set()
        for row in rows:
            if col.index < len(row):
                value = to_text(row[col.index]).strip()
                if value:
                    values.add(value)
        hits = len(wanted & values)
        if hits > best_hits:
            best_col, best_hits = col, hits

    # require most groups to match before trusting it
    if best_col is not None and best_hits >= max(1, len(wanted) // 2):
        return best_col
    return None


def parse_delimited(text, delimiter=TAB, title=None, group_field_name=None):
    """Parse a Revit schedule export into a ScheduleTable.

    Pure function, no Revit import, so it can be tested on the real CSV
    exports this extension was built against.
    """
    rows = _split_lines(text, delimiter)
    if not rows:
        raise ValueError("Schedule export is empty")

    file_title = rows[0][0].strip() if rows[0] else ""
    header_depth, data_start = _find_header_depth(rows)
    header_rows = rows[1:1 + header_depth]
    if not header_rows:
        raise ValueError("Could not locate a header row in the export")

    n_cols = max(len(r) for r in header_rows)
    # trailing empties are Revit padding, not real columns
    while n_cols > 1 and all(
            (j >= len(r) or r[j].strip() == "")
            for r in rows[1:] for j in [n_cols - 1]):
        n_cols -= 1

    columns = _build_columns(header_rows, n_cols)
    raw_rows = [r + [""] * (n_cols - len(r)) for r in rows[data_start:]]
    raw_rows = [r[:n_cols] for r in raw_rows]

    real_rows, group_labels, grand_total_label = _strip_render_rows(raw_rows)
    if not real_rows:
        raise ValueError("Schedule '{0}' has no data rows".format(
            title or file_title))

    detect_column_types(columns, real_rows)

    group_col = None
    if group_field_name:
        for col in columns:
            if col.name.strip().lower() == group_field_name.strip().lower():
                group_col = col
                break
    if group_col is None:
        group_col = _infer_group_column(columns, real_rows, group_labels)
    if group_col is None:
        # House style falls back to grouping by level when the schedule
        # itself is ungrouped, which is what the room schedule needed.
        group_col = find_level_column(columns)

    parsed_rows = apply_parsing(columns, real_rows)

    return ScheduleTable(
        title=title or file_title or "Schedule",
        columns=columns,
        rows=parsed_rows,
        group_column=group_col.index if group_col is not None else None,
        grand_total_label=grand_total_label or "Total",
    )


# --- Revit side --------------------------------------------------------

def get_group_field_name(view_schedule):
    """Ask Revit what the schedule is grouped by, or None.

    Wrapped defensively: this is metadata that improves the result, and a
    version mismatch here should degrade to inference from the export,
    not fail the run.
    """
    try:
        definition = view_schedule.Definition
        for sort_field in definition.GetSortGroupFields():
            shows_group = False
            for attr in ("ShowHeader", "ShowFooter"):
                if getattr(sort_field, attr, False):
                    shows_group = True
            if shows_group:
                field = definition.GetField(sort_field.FieldId)
                return field.GetName()
    except Exception:
        return None
    return None


def export_schedule_text(view_schedule, delimiter=TAB):
    """Run ViewSchedule.Export() to a temp file and return its text."""
    from Autodesk.Revit.DB import ViewScheduleExportOptions

    temp_dir = tempfile.mkdtemp(prefix="avh_sched_")
    file_name = "schedule.txt"
    options = ViewScheduleExportOptions()
    options.FieldDelimiter = delimiter
    # Revit quotes text containing the delimiter; a tab delimiter means
    # that effectively never happens, so ask for no quoting at all.
    try:
        options.TextQualifier = _no_text_qualifier()
    except Exception:
        pass

    try:
        view_schedule.Export(temp_dir, file_name, options)
        path = os.path.join(temp_dir, file_name)
        # Revit's export encoding varies by version, so try in order.
        return read_text(path)
    finally:
        # Nothing is left behind in %TEMP% (until 2.38.0 every export
        # left an avh_sched_* folder there).
        shutil.rmtree(temp_dir, ignore_errors=True)


def _no_text_qualifier():
    from Autodesk.Revit.DB import ExportTextQualifier
    return ExportTextQualifier.None_ if hasattr(ExportTextQualifier, "None_") \
        else getattr(ExportTextQualifier, "None")


def read_schedule(view_schedule):
    """Read one Revit ViewSchedule into a ScheduleTable.

    Tries to read it so the workbook can be imported again, which means
    knowing the element behind every row. If that is not possible for any
    reason, the plain export is read instead and the table says why in
    roundtrip_note: a failure to add the ids must never cost the export.
    """
    note = u""
    try:
        return read_schedule_with_ids(view_schedule)
    except NoIdField as exc:
        # Revit 2025 offers no Element Id schedule field, so the ids are
        # recovered by matching each row to an element instead.
        note = to_text(exc)
        try:
            return read_schedule_by_matching(view_schedule)
        except NotRoundTrippable as inner:
            note = to_text(inner)
        except Exception as inner:
            note = u"{0}: {1}".format(type(inner).__name__, to_text(inner))
    except NotRoundTrippable as exc:
        note = to_text(exc)
    except Exception as exc:
        # Anything else, including a Revit that lacks an API used below.
        note = u"{0}: {1}".format(type(exc).__name__, to_text(exc))

    text = export_schedule_text(view_schedule, TAB)
    table = parse_delimited(
        text,
        delimiter=TAB,
        title=view_schedule.Name,
        group_field_name=get_group_field_name(view_schedule),
    )
    table.roundtrip_note = note or u"not available for this schedule"
    return table


# --- reading with element ids -----------------------------------------
#
# UNVERIFIED IN REVIT as of 2.40.0. Everything below touches the Revit API
# and has been exercised only against a mock. The calls that carry the
# risk are named where they are made.

class NotRoundTrippable(Exception):
    """This schedule cannot be exported in a form that imports again."""


class NoIdField(NotRoundTrippable):
    """Revit offers no Element Id field for this schedule."""


def field_specs(view_schedule):
    """One FieldSpec per exported column, from the schedule's own fields.

    The exported columns are the schedule's visible fields, in field
    order, so the two line up one to one. The caller checks the counts
    match rather than trusting that.
    """
    definition = view_schedule.Definition
    doc = view_schedule.Document
    specs = []
    for field_id in definition.GetFieldOrder():
        field = definition.GetField(field_id)
        if field.IsHidden:
            continue
        specs.append(_spec_for_field(len(specs), field, doc))
    return specs


def _field_type_name(field):
    try:
        return to_text(field.FieldType)
    except Exception:
        return u""


def _spec_for_field(index, field, doc):
    type_name = _field_type_name(field)
    if type_name == u"Instance":
        kind = KIND_INSTANCE
    elif type_name == u"ElementType":
        kind = KIND_TYPE
    else:
        kind = KIND_OTHER

    try:
        name = to_text(field.ColumnHeading)
    except Exception:
        name = u""
    try:
        param_name = to_text(field.GetName())
    except Exception:
        param_name = u""
    return FieldSpec(
        index=index,
        name=name or param_name,
        kind=kind,
        param_id=element_id_int(getattr(field, "ParameterId", None)),
        param_name=param_name,
        unit_id=_unit_id(field, doc))


def _unit_id(field, doc):
    """The unit the column is displayed in, as a ForgeTypeId string.

    What a person types into a numeric cell is in this unit, so it is what
    the value has to be converted from on import. Empty when it cannot be
    worked out, which makes a numeric edit to that column refused rather
    than guessed.

    UNVERIFIED: GetFormatOptions, UseDefault, GetSpecTypeId and
    GetUnitTypeId are the Revit 2021+ API.
    """
    try:
        options = field.GetFormatOptions()
        if options.UseDefault:
            options = doc.GetUnits().GetFormatOptions(field.GetSpecTypeId())
        return to_text(options.GetUnitTypeId().TypeId)
    except Exception:
        return u""


def _find_id_field(definition, doc):
    """(schedulable field, trusted), or (None, False).

    trusted is True for Revit's own Element Id field, whose values cannot
    be wrong. False for the Element ID PARAMETER that AVH's Stamp Element
    IDs button (or a Dynamo script) writes: it is only a number somebody
    pasted into a text field, and a copied element keeps the number it was
    copied from, so every id read from it is verified before it is used.

    UNVERIFIED: that Revit offers its own Element Id field. Revit 2025 does
    not: ID_PARAM was -1002100 and nothing carried that id.
    """
    from Autodesk.Revit.DB import BuiltInParameter, ElementId

    wanted = element_id_int(ElementId(BuiltInParameter.ID_PARAM))
    offered = list(definition.GetSchedulableFields())
    for schedulable in offered:
        if element_id_int(schedulable.ParameterId) == wanted:
            return schedulable, True
    for schedulable in offered:
        try:
            name = to_text(schedulable.GetName(doc))
        except Exception:
            continue
        if is_stamp_name(name):
            return schedulable, False
    return None, False


def read_schedule_with_ids(view_schedule):
    """Export a schedule with the element id of every row.

    Revit's export says nothing about which element a row is, and a
    schedule cannot be asked. The one reliable source is Revit itself, by
    adding the Element Id field. That cannot be done to the schedule the
    person is looking at, which would mean editing it, taking out a
    worksharing checkout on it and risking leaving it changed. So the work
    is done on a DUPLICATE inside a transaction group that is always
    rolled back: the duplicate exists for the length of one export and the
    original is never touched.
    """
    from Autodesk.Revit.DB import (
        Transaction, TransactionGroup, TransactionStatus,
        ViewDuplicateOption,
    )

    definition = view_schedule.Definition
    if not definition.IsItemized:
        raise NotRoundTrippable(
            u"the schedule is not itemized, so a row is not one element")

    doc = view_schedule.Document
    specs = field_specs(view_schedule)

    group = TransactionGroup(doc, u"AVH export element ids")
    group.Start()
    transaction = None
    text = None
    try:
        transaction = Transaction(doc, u"AVH duplicate schedule")
        transaction.Start()
        duplicate_id = view_schedule.Duplicate(ViewDuplicateOption.Duplicate)
        duplicate = doc.GetElement(duplicate_id)

        schedulable, trusted = _find_id_field(duplicate.Definition, doc)
        if schedulable is None:
            raise NoIdField(
                u"no Element Id field and no Element ID parameter on "
                u"this schedule's category")
        field = duplicate.Definition.AddField(schedulable)
        field.ColumnHeading = TEMP_HEADING

        if transaction.Commit() != TransactionStatus.Committed:
            raise NotRoundTrippable(
                u"Revit would not let the duplicate be edited")
        transaction = None

        text = export_schedule_text(duplicate, TAB)
    finally:
        if transaction is not None:
            try:
                transaction.RollBack()
            except Exception:
                pass
        # Always. The duplicate and its field must not outlive the export.
        group.RollBack()

    table = parse_delimited(
        text,
        delimiter=TAB,
        title=view_schedule.Name,
        group_field_name=get_group_field_name(view_schedule),
    )
    if not extract_id_column(table):
        raise NotRoundTrippable(u"the id column was missing from the export")
    if len(specs) != len(table.columns):
        raise NotRoundTrippable(
            u"{0} columns exported but the schedule has {1} visible "
            u"fields".format(len(table.columns), len(specs)))

    table.fields = specs
    table.meta = {
        "schedule_id": element_id_int(view_schedule.Id),
        "model": to_text(getattr(doc, "Title", u"")),
        "exported": to_text(datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M")),
    }
    if not trusted:
        _verify_stamped(view_schedule, table, specs)
    return table


class _Rows(object):
    """Just enough of a table for match_rows to work on a subset of rows."""

    def __init__(self, rows):
        self.rows = rows


def _verify_stamped(view_schedule, table, specs):
    """Keep only the stamped ids that can be trusted, then match the rest.

    An id read from the Element ID parameter is used only when it belongs
    to an element this schedule shows AND appears on exactly one row. A
    copied element carries its source's id, so the id turns up twice, and
    both rows lose it. An element created since the last stamping has no
    id at all. Every row left without one is matched by value against the
    elements nobody has claimed, which is the same rule as everywhere
    else: one fit or no id.
    """
    values = collect_element_values(view_schedule, specs)
    shown = set(values)
    slot = table.id_index

    counts = {}
    for row in table.rows:
        ident = row_id(table, row)
        if ident is not None:
            counts[ident] = counts.get(ident, 0) + 1

    stamped = 0
    for row in table.rows:
        ident = row_id(table, row)
        if ident is not None and ident in shown and counts[ident] == 1:
            row[slot] = ident
            stamped += 1
        else:
            row[slot] = None

    todo = [row for row in table.rows if row[slot] is None]
    by_value = 0
    if todo and values:
        used = set(row[slot] for row in table.rows if row[slot] is not None)
        remaining = dict((e, v) for e, v in values.items() if e not in used)
        ids, _tally = match_rows(_Rows(todo), specs, remaining)
        for row, ident in zip(todo, ids):
            if ident is not None:
                row[slot] = ident
                by_value += 1

    total = len(table.rows)
    left = total - stamped - by_value
    if not stamped and not by_value:
        raise NotRoundTrippable(
            u"no row carried a usable Element ID and none could be "
            u"matched by value ({0} rows)".format(total))
    table.roundtrip_note = (
        u"{0} of {1} rows identified by the Element ID parameter, {2} "
        u"more by their values; {3} cannot be imported again".format(
            stamped, total, by_value, left))


# --- reading by matching rows to elements -----------------------------
#
# UNVERIFIED IN REVIT. Used when Revit offers no Element Id field, which
# is the case on Revit 2025. Calls that carry the risk:
# FilteredElementCollector(doc, scheduleViewId) returning exactly the
# elements the schedule shows, and UnitUtils.ConvertFromInternalUnits.

def _element_value(DB, doc, element, type_element, spec):
    """One element's value for one column, in the column's display unit."""
    if spec.kind == KIND_INSTANCE:
        owner = element
    elif spec.kind == KIND_TYPE:
        owner = type_element
    else:
        return UNKNOWN
    if owner is None:
        return UNKNOWN
    parameter = find_parameter(owner, spec)
    if parameter is None:
        return UNKNOWN

    storage = parameter.StorageType
    if storage == DB.StorageType.String:
        return to_text(parameter.AsString())
    if storage == DB.StorageType.Integer:
        return int(parameter.AsInteger())
    if storage == DB.StorageType.Double:
        if not spec.unit_id:
            return UNKNOWN
        return float(DB.UnitUtils.ConvertFromInternalUnits(
            parameter.AsDouble(), DB.ForgeTypeId(spec.unit_id)))
    if storage == DB.StorageType.ElementId:
        # a Level, a Phase: shown by name, which is what the export holds
        return to_text(parameter.AsValueString())
    return UNKNOWN


def collect_element_values(view_schedule, specs):
    """{element id: [value per column]} for what the schedule shows."""
    import Autodesk.Revit.DB as DB

    doc = view_schedule.Document
    collector = DB.FilteredElementCollector(doc, view_schedule.Id)
    collector = collector.WhereElementIsNotElementType()

    values = {}
    for element in collector:
        ident = element_id_int(element.Id)
        if ident is None:
            continue
        try:
            type_element = doc.GetElement(element.GetTypeId())
        except Exception:
            type_element = None
        row = []
        for spec in specs:
            try:
                row.append(_element_value(DB, doc, element, type_element,
                                          spec))
            except Exception:
                row.append(UNKNOWN)
        values[ident] = row
    return values


def read_schedule_by_matching(view_schedule):
    """A table whose rows carry element ids found by matching values.

    Rows that fit no element, or more than one, get no id and are simply
    not importable. The note on the table says how many.
    """
    if not view_schedule.Definition.IsItemized:
        raise NotRoundTrippable(
            u"the schedule is not itemized, so a row is not one element")

    specs = field_specs(view_schedule)
    table = parse_delimited(
        export_schedule_text(view_schedule, TAB),
        delimiter=TAB,
        title=view_schedule.Name,
        group_field_name=get_group_field_name(view_schedule),
    )
    if len(specs) != len(table.columns):
        raise NotRoundTrippable(
            u"{0} columns exported but the schedule has {1} visible "
            u"fields".format(len(table.columns), len(specs)))

    values = collect_element_values(view_schedule, specs)
    if not values:
        raise NotRoundTrippable(u"no elements could be read from the "
                                u"schedule")

    ids, tally = match_rows(table, specs, values)
    if not tally["matched"]:
        raise NotRoundTrippable(
            u"no row could be matched to an element ({0} rows, {1} "
            u"elements read)".format(tally["rows"], len(values)))

    for row, ident in zip(table.rows, ids):
        row.append(ident)
    table.id_index = len(table.columns)
    table.fields = specs
    table.meta = {
        "schedule_id": element_id_int(view_schedule.Id),
        "model": to_text(getattr(view_schedule.Document, "Title", u"")),
        "exported": to_text(datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M")),
    }
    table.roundtrip_note = (
        u"rows matched to elements by their values: {0} of {1} can be "
        u"imported again ({2} not found, {3} ambiguous). {4} elements "
        u"read; compared on: {5}".format(
            tally["matched"], tally["rows"], tally["unmatched"],
            tally["ambiguous"], len(values),
            u", ".join(tally["compared"]) or u"nothing"))
    return table
