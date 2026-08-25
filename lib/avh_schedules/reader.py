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

import os
import tempfile

from .compat import is_number_text, read_text, to_text
from .model import (
    Column, ScheduleTable, apply_parsing, confirm_render_row,
    detect_column_types, find_level_column, is_render_candidate,
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

    view_schedule.Export(temp_dir, file_name, options)
    path = os.path.join(temp_dir, file_name)
    # Revit's export encoding varies by version, so try in order.
    return read_text(path)


def _no_text_qualifier():
    from Autodesk.Revit.DB import ExportTextQualifier
    return ExportTextQualifier.None_ if hasattr(ExportTextQualifier, "None_") \
        else getattr(ExportTextQualifier, "None")


def read_schedule(view_schedule):
    """Read one Revit ViewSchedule into a ScheduleTable."""
    text = export_schedule_text(view_schedule, TAB)
    return parse_delimited(
        text,
        delimiter=TAB,
        title=view_schedule.Name,
        group_field_name=get_group_field_name(view_schedule),
    )
