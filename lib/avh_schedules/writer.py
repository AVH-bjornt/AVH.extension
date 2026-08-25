# -*- coding: utf-8 -*-
"""
Turn a ScheduleTable into an AVH styled workbook.

The formatting confirmed against the a live project room schedule and the
interior, exterior and industrial door schedules. Subtotals are real SUM
formulas, not precomputed numbers, so the sheet stays live when someone
edits a row.

Column order is always the source's order. That is the user's layout and
is not ours to rearrange; only the unit moves, out of the cell value and
into the column header.
"""

import os

from . import style, xlsx
from .compat import to_text
from .model import find_column

MIN_WIDTH = 8
MAX_WIDTH = 34
WIDE_FIRST_COLUMN = 22

# Units whose columns are quantities: one decimal, and candidates for a
# meaningful subtotal.
QUANTITY_UNITS = (u"m²", u"m2", u"m³", u"m3")


def _is_quantity(column):
    return column.unit in QUANTITY_UNITS


def subtotal_columns(table):
    """Which columns are meaningful to sum per group.

    A Count column wins when the schedule has one, matching how Revit's
    own grouped door schedules total themselves. Otherwise every quantity
    column is summed, which is what the CC01 room schedule needed for Area
    and Volume together. A schedule with neither gets section rows but no
    subtotal, rather than a meaningless sum of door widths.
    """
    count_col = find_column(table.columns, "count", u"fjöldi", "fjoldi")
    if count_col is not None and count_col.is_numeric:
        return [count_col.index]

    quantities = [c.index for c in table.columns
                  if c.is_numeric and _is_quantity(c)]
    return quantities


def _leaf_label(column):
    """The header label confined to this one column.

    Only the deepest label counts for width. A level 1 label such as
    "Measurements (mm)" is merged across several columns, so sizing each
    of them to fit it would make the sheet absurdly wide.
    """
    labels = [l for l in column.header_tuple() if l]
    return to_text(labels[-1]) if labels else u""


def _column_widths(table):
    widths = {}
    for col in table.columns:
        # Measure the label as displayed, which carries the unit
        # ("Volume (m³)"), not the bare field name ("Volume"). Measuring
        # the bare name left every unit column a few characters short, so
        # the header wrapped onto a second line.
        longest = len(_leaf_label(col))
        for row in table.rows:
            if col.index < len(row) and row[col.index] is not None:
                longest = max(longest, len(to_text(row[col.index])))
        # Excel's column width unit is character widths in the workbook
        # default font, which it assumes is Calibri 11. These sheets are
        # now set in Calibri too, so the character count is a fair
        # estimate and only needs an allowance for cell padding. It was
        # +4 while the sheets were Times New Roman, whose accented
        # Icelandic capitals run wider than Excel's assumption.
        widths[col.index] = max(MIN_WIDTH, min(MAX_WIDTH, longest + 2))
    return widths


def write_workbook(table, output_path, logo_path=None, sheet_name=None):
    """Write table to output_path. Returns the path written."""
    n_cols = table.n_cols
    if not n_cols:
        raise ValueError("Schedule has no columns")

    book = xlsx.Workbook()
    sheet = book.add_sheet(to_text(sheet_name or table.title
                                   or u"Schedule")[:31])

    widths = _column_widths(table)
    for col in table.columns:
        sheet.column_width(col.index + 1, widths[col.index])
    first = table.columns[0]
    sheet.column_width(first.index + 1,
                       max(widths[first.index], WIDE_FIRST_COLUMN))

    # Row 1: logo left, title clear of it. 190px needs roughly 27 width
    # units of clearance.
    title_col = 1
    if logo_path and os.path.exists(logo_path):
        style.insert_logo(sheet, logo_path, row=1, col=1, width_px=190)
        used = 0.0
        for col in table.columns:
            used += widths[col.index]
            title_col += 1
            if used >= 27:
                break
        title_col = min(title_col, n_cols)
    sheet.cell(1, title_col, to_text(table.title), style.TITLE)

    row = 3
    header_last = _write_header(sheet, table, row)
    row = header_last + 1

    sum_cols = subtotal_columns(table)
    quantity_cols = set(c.index for c in table.columns if _is_quantity(c))
    group_ranges = []
    has_sections = table.group_column is not None

    for label, group_rows in table.groups():
        if has_sections:
            style.write_section_header(sheet, row, to_text(label), n_cols)
            row += 1
        start = row

        # Stripe parity restarts inside each group, so every section
        # begins with an unstriped row and the banding reads as
        # deliberate rather than drifting against the section headers.
        for offset, data_row in enumerate(group_rows):
            striped = bool(offset % 2)
            for col in table.columns:
                value = (data_row[col.index]
                         if col.index < len(data_row) else None)
                if col.is_numeric and col.index in quantity_cols:
                    kind = "quantity"
                elif col.is_numeric or value == u"X":
                    kind = "center"
                else:
                    kind = "text"
                sheet.cell(row, col.index + 1, value,
                           style.body_style(kind, striped))
            row += 1

        end = row - 1
        if end < start:
            continue
        group_ranges.append((start, end))

        # A 'Subtotal' label with no number beside it is just noise, so a
        # schedule with nothing worth summing gets section rows only.
        if has_sections and sum_cols:
            _write_total_row(sheet, row, n_cols, sum_cols, [(start, end)],
                             u"Subtotal", quantity_cols, grand=False)
            sheet.row_height(row, 16)
            row += 1

    # A sheet with anything summable should always end in a total.
    #
    # Grouped with several groups: subtotals plus a grand total.
    # Grouped with exactly one group: the subtotal already is the total,
    #   so a grand total would just repeat it two rows apart.
    # Not grouped at all: no subtotals were written, so this is the only
    #   total the sheet gets. Missing this case left schedules with no
    #   level or Hæð column, which have nothing to group by, showing no
    #   total whatsoever.
    wants_total = bool(sum_cols) and (len(group_ranges) > 1
                                      or not has_sections)
    if wants_total:
        _write_total_row(sheet, row, n_cols, sum_cols, group_ranges,
                         to_text(table.grand_total_label), quantity_cols,
                         grand=True)
        row += 1

    sheet.freeze_panes(header_last + 1, 1)
    sheet.landscape = True
    sheet.fit_to_width = 1
    sheet.print_area("A1:{0}{1}".format(xlsx.column_letter(n_cols), row - 1))

    book.save(output_path)
    return output_path


def _write_header(sheet, table, start_row):
    """Write a 1 to 3 level header, merging as the source implies."""
    spec = table.header_spec()
    n_cols = len(spec)
    depth = 1
    for entry in spec:
        if entry[2]:
            depth = 3
            break
        if entry[1]:
            depth = max(depth, 2)

    rows = [start_row + i for i in range(depth)]

    # paint the whole block first so merged areas carry the navy fill
    for r in rows:
        for c in range(1, n_cols + 1):
            sheet.cell(r, c, None, style.HEADER)
        sheet.row_height(r, 20 if depth > 1 else 26)

    if depth == 1:
        for c in range(n_cols):
            sheet.cell(rows[0], c + 1, to_text(spec[c][0]), style.HEADER)
        return rows[0]

    c = 0
    while c < n_cols:
        c2 = c
        while c2 + 1 < n_cols and spec[c2 + 1][0] == spec[c][0]:
            c2 += 1
        if c2 > c:
            sheet.merge(rows[0], c + 1, rows[0], c2 + 1)
        sheet.cell(rows[0], c + 1, to_text(spec[c][0]), style.HEADER)

        if spec[c][1] is None:
            # nothing below: merge this column down through the block
            sheet.merge(rows[0], c + 1, rows[-1], c + 1)
            c = c2 + 1
            continue

        cc = c
        while cc <= c2:
            cc2 = cc
            while cc2 + 1 <= c2 and spec[cc2 + 1][1] == spec[cc][1]:
                cc2 += 1
            if cc2 > cc:
                sheet.merge(rows[1], cc + 1, rows[1], cc2 + 1)
            sheet.cell(rows[1], cc + 1, to_text(spec[cc][1]), style.HEADER)
            if spec[cc][2] is None:
                if depth > 2:
                    sheet.merge(rows[1], cc + 1, rows[-1], cc + 1)
            else:
                for cci in range(cc, cc2 + 1):
                    sheet.cell(rows[2], cci + 1, to_text(spec[cci][2]),
                               style.HEADER)
            cc = cc2 + 1
        c = c2 + 1

    return rows[-1]


def _write_total_row(sheet, row, n_cols, sum_cols, ranges, label,
                     quantity_cols, grand):
    """One subtotal or grand total line.

    ranges is a list of (start, end) so a grand total adds up the same body
    rows the subtotals covered, without double counting the subtotal rows
    sitting between them.
    """
    label_style = style.TOTAL_LABEL if grand else style.SUBTOTAL_LABEL
    rule_style = style.RULE_ONLY_DOUBLE if grand else style.RULE_ONLY

    label_span = max(1, min(3, n_cols))
    if sum_cols:
        label_span = max(1, min(label_span, min(sum_cols)))

    for col in range(1, n_cols + 1):
        sheet.cell(row, col, None, rule_style)

    if label_span > 1:
        sheet.merge(row, 1, row, label_span)
    sheet.cell(row, 1, label, label_style)

    for col_index in sum_cols:
        letter = xlsx.column_letter(col_index + 1)
        terms = u"+".join(
            u"SUM({0}{1}:{0}{2})".format(letter, start, end)
            for start, end in ranges)
        if col_index in quantity_cols:
            value_style = (style.TOTAL_QUANTITY if grand
                           else style.SUBTOTAL_QUANTITY)
        else:
            value_style = style.TOTAL_VALUE if grand else style.SUBTOTAL_VALUE
        sheet.cell(row, col_index + 1, None, value_style,
                   formula=u"=" + terms)
