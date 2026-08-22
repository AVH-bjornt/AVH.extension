# -*- coding: utf-8 -*-
"""
Exercise the extension's parsing and writing path against the four real
Eldisgardur exports, with no Revit involved.

These are the actual files Bjorn handed over, so the expected values are
the ones confirmed on the delivered workbooks: row counts, the grouping
column chosen, and the grand totals Revit itself printed.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_schedules import reader, writer  # noqa: E402
from avh_schedules.writer import subtotal_columns  # noqa: E402

UPLOADS = "/root/.claude/uploads/0229f214-6cab-5076-81c9-61ec6de77d0f"
LOGO = os.path.join(HERE, "assets", "avh_logo.png")
OUT_DIR = os.path.join(HERE, "test_output")

CASES = [
    {
        "file": "99e1c254-ELG_DA11_K.K01_C08.csv",
        "label": "Room schedule (DA11)",
        "rows": 6,
        "group_by": "Level",
        "sum_total": 112.0,
    },
    {
        "file": "a4e9e9a2-ELG_CC01_K.K01_C08.03_001.csv",
        "label": "Interior doors (CC01)",
        "rows": 197,
        "group_by": "Type",
        "sum_total": 197,
        "grand_label": "Samtals",
    },
    {
        "file": "83a1b9d3-ELG_CC01_K.K01_C08.03_002.csv",
        "label": "Exterior doors (CC01)",
        "rows": 41,
        "group_by": "Type",
        "sum_total": 41,
        "grand_label": "Samtals",
    },
    {
        "file": "fdc4a22d-ELG_CC01_K.K01_C08.03_003.csv",
        "label": "Industrial doors (CC01)",
        "rows": 26,
        "group_by": "Type",
        "sum_total": 26,
        "grand_label": "Total",
    },
]


def check(condition, message, failures):
    if not condition:
        failures.append(message)
    return condition


def run():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    failures = []
    for case in CASES:
        path = os.path.join(UPLOADS, case["file"])
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()

        table = reader.parse_delimited(text, delimiter=";")

        group_name = None
        if table.group_column is not None:
            column = next(c for c in table.columns
                          if c.index == table.group_column)
            group_name = column.name

        sums = subtotal_columns(table)
        sum_name = None
        if sums:
            sum_name = next(c for c in table.columns
                            if c.index == sums[0]).name

        actual_total = 0
        for index in sums:
            for row in table.rows:
                value = row[index] if index < len(row) else None
                if isinstance(value, (int, float)):
                    actual_total += value

        print("\n{0}".format(case["label"]))
        print("  title        : {0}".format(table.title))
        print("  columns      : {0}".format(table.n_cols))
        print("  data rows    : {0}".format(len(table.rows)))
        print("  grouped by   : {0}".format(group_name))
        print("  groups       : {0}".format(len(table.groups())))
        print("  summed column: {0}".format(sum_name))
        print("  sum of column: {0}".format(actual_total))
        print("  total label  : {0}".format(table.grand_total_label))

        # Column order must match the source exactly. The CC01 room
        # schedule was once delivered with the columns rearranged, which
        # is the user's layout to decide, not ours. For a multi level
        # header the leaf label legitimately differs from row 1's group
        # label, so only single header row schedules compare by name;
        # every schedule is checked for positional integrity.
        raw_lines = text.splitlines()
        src_header = [h.strip() for h in raw_lines[1].split(";")]
        blank_at = next((i for i in range(1, min(len(raw_lines), 8))
                         if not raw_lines[i].strip(";").strip()), 2)
        header_depth = blank_at - 1

        check([c.index for c in table.columns] == list(range(table.n_cols)),
              "{0}: columns are not in positional order".format(
                  case["label"]), failures)

        if header_depth == 1:
            out_header = [c.name.split(" (")[0] for c in table.columns]
            check(out_header == src_header[:len(out_header)],
                  "{0}: column order changed\n  source: {1}\n  output: {2}"
                  .format(case["label"], src_header[:6], out_header[:6]),
                  failures)
        else:
            top = [(c.level1 or "").strip() for c in table.columns]
            filled = [h for h in src_header[:table.n_cols] if h]
            check(all(f in top for f in filled),
                  "{0}: level 1 header labels lost".format(case["label"]),
                  failures)

        check(len(table.rows) == case["rows"],
              "{0}: expected {1} rows, got {2}".format(
                  case["label"], case["rows"], len(table.rows)), failures)
        check(group_name == case["group_by"],
              "{0}: expected grouping by {1}, got {2}".format(
                  case["label"], case["group_by"], group_name), failures)
        check(abs(actual_total - case["sum_total"]) < 0.01,
              "{0}: expected total {1}, got {2}".format(
                  case["label"], case["sum_total"], actual_total), failures)
        if "grand_label" in case:
            check(table.grand_total_label == case["grand_label"],
                  "{0}: expected total label {1}, got {2}".format(
                      case["label"], case["grand_label"],
                      table.grand_total_label), failures)

        # Striping must land on alternating data rows only, never on a
        # section, subtotal or total row. Checked on the written file
        # rather than on the style objects, because an index bug between
        # the two would not show up any other way.
        probe_path = os.path.join(OUT_DIR, "_stripe_probe.xlsx")
        writer.write_workbook(table, probe_path, logo_path=LOGO)
        import zipfile as _zip
        _x = _zip.ZipFile(probe_path)
        _sheet = _x.read("xl/worksheets/sheet1.xml").decode("utf-8")
        _styles = _x.read("xl/styles.xml").decode("utf-8")
        _fills = re.findall(r"<fill>.*?</fill>", _styles, re.S)
        # Scope to cellXfs: cellStyleXfs holds an <xf> too, and counting
        # both shifts every index by one. Same trap as the border
        # registry bug, this time in the test rather than the writer.
        _cellxfs = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>",
                             _styles, re.S).group(1)
        _xfs = re.findall(r"<xf [^>]*?(?:/>|>.*?</xf>)", _cellxfs, re.S)

        def _row_fill(row_no):
            m = re.search(r'<row r="%d"[ >].*?</row>' % row_no,
                          _sheet, re.S)
            if not m:
                return None
            c = re.search(r'<c r="[A-Z]+%d" s="(\d+)"' % row_no, m.group(0))
            if not c:
                return None
            fid = int(re.search(r'fillId="(\d+)"', _xfs[int(c.group(1))])
                      .group(1))
            return _fills[fid]

        # Header depth varies: room schedules have one header row, door
        # schedules three, so the section and body rows move with it.
        spec = table.header_spec()
        depth = 1
        if any(e[2] for e in spec):
            depth = 3
        elif any(e[1] for e in spec):
            depth = 2
        header_last = 2 + depth              # header starts at row 3
        section_row = header_last + 1
        first_group = table.groups()[0]
        body_start = section_row + (1 if table.group_column is not None else 0)
        if len(first_group[1]) >= 2:
            even, odd = _row_fill(body_start), _row_fill(body_start + 1)
            check(even is not None and "none" in even,
                  "{0}: first data row should be unstriped, got {1}".format(
                      case["label"], even), failures)
            check(odd is not None and "F2F4F7" in odd,
                  "{0}: second data row should be striped, got {1}".format(
                      case["label"], odd), failures)
        if table.group_column is not None:
            sec = _row_fill(section_row)
            check(sec is not None and writer.style.HEADER_BG in sec,
                  "{0}: section row should keep the header fill, got {1}".format(
                      case["label"], sec), failures)
        os.remove(probe_path)

        out_path = os.path.join(
            OUT_DIR, os.path.splitext(case["file"])[0][9:] + ".xlsx")
        writer.write_workbook(table, out_path, logo_path=LOGO)
        print("  written      : {0}".format(os.path.basename(out_path)))

    print("\n" + "=" * 60)
    if failures:
        print("FAILURES ({0}):".format(len(failures)))
        for failure in failures:
            print("  - {0}".format(failure))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
