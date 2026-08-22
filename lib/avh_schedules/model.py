# -*- coding: utf-8 -*-
"""
A Revit independent description of a schedule, plus the value parsing that
turns Revit's display strings into real spreadsheet types.

Keeping this module free of any Revit import is deliberate: it is the part
that can be exercised on plain CSV fixtures without opening Revit, which
is the only way to test most of this extension's behaviour.
"""

import re

from .compat import to_text

# Level short codes seen on Eldisgardur (E01 to E04, EK2, EKL, EM1, EST).
# A project with a different coding convention should widen this pattern
# rather than have the fallback silently return Unassigned.
LEVEL_CODE_RE = re.compile(r"^(E\d{2}|EK\d|EKL|EM\d|EST)$")

# Units AVH schedules actually emit. Anything matching is lifted out of the
# value and into the column header, per house style.
KNOWN_UNITS = (u"m\u00b2", u"m2", u"m\u00b3", u"m3", u"mm", u"cm", u"m",
               u"kg", u"db", u"\u00b0")


def parse_measurement(raw):
    """'30 m²' -> (30.0, 'm²'). '980' -> (980, ''). 'Skra olaest' -> (text, '').

    AVH's Revit exports use a comma as the decimal separator, so both
    comma and period are accepted here. This is also why a comma
    delimited CSV from these templates corrupts itself, and why this
    extension reads the Revit API directly instead.
    """
    if raw is None:
        return None, ""
    text = to_text(raw).strip()
    if not text:
        return None, ""

    unit = ""
    body = text
    for candidate in KNOWN_UNITS:
        if body.endswith(candidate):
            stripped = body[: -len(candidate)].strip()
            # only treat it as a unit if what precedes it looks numeric
            if stripped and _as_number(stripped) is not None:
                unit = candidate
                body = stripped
                break

    number = _as_number(body)
    if number is None:
        return text, ""
    return number, unit


def _as_number(text):
    """Return int or float for a numeric string, else None."""
    candidate = text.strip().replace(" ", "")
    if not candidate:
        return None
    # a comma is a decimal separator in these exports, never a thousands
    # separator, so a bare swap to period is safe
    candidate = candidate.replace(",", ".")
    if candidate.count(".") > 1:
        return None
    try:
        if "." in candidate:
            return float(candidate)
        return int(candidate)
    except ValueError:
        return None


def extract_level_code(text, location_id=None):
    """Pull a short level code out of a CCI location id or a descriptive
    level string such as 'Fire Rating / E04 - Ventilation floor'.

    Which column carries the short code is not consistent across AVH's
    own schedule types, so both sources are tried.
    """
    if location_id:
        match = re.search(r"\.([A-Z0-9]{2,3})\.", to_text(location_id))
        if match:
            return match.group(1)
    if text:
        for token in re.split(r"[\s/\-]+", to_text(text)):
            if LEVEL_CODE_RE.match(token):
                return token
    return None


class Column(object):
    """One output column: its header path, detected unit and type."""

    def __init__(self, index, level1, level2=None, level3=None):
        self.index = index
        self.level1 = level1
        self.level2 = level2
        self.level3 = level3
        self.unit = ""
        self.is_numeric = False
        self.is_empty = True

    @property
    def name(self):
        """The most specific label this column carries."""
        for label in (self.level3, self.level2, self.level1):
            if label:
                return label
        return ""

    def header_tuple(self):
        """Header spec entry, with the unit appended to the leaf label."""
        labels = [self.level1, self.level2, self.level3]
        if self.unit:
            for i in (2, 1, 0):
                if labels[i]:
                    labels[i] = "{0} ({1})".format(labels[i], self.unit)
                    break
        return tuple(labels)

    def __repr__(self):
        return "<Column {0} {1!r} unit={2!r} numeric={3}>".format(
            self.index, self.name, self.unit, self.is_numeric)


class ScheduleTable(object):
    """A schedule reduced to what the writer needs.

    rows holds parsed python values, already stripped of Revit's own
    rendered group header and footer lines. Grouping is described rather
    than baked in, so the writer can rebuild it with real SUM formulas.
    """

    def __init__(self, title, columns, rows, group_column=None,
                 count_column=None, grand_total_label="Total"):
        self.title = title
        self.columns = columns
        self.rows = rows
        self.group_column = group_column
        self.count_column = count_column
        self.grand_total_label = grand_total_label

    @property
    def n_cols(self):
        return len(self.columns)

    def header_spec(self):
        return [col.header_tuple() for col in self.columns]

    def groups(self):
        """Contiguous runs of rows sharing a group value.

        Contiguous rather than sorted-and-bucketed on purpose: it
        preserves the order Revit already put the rows in, which is the
        order the schedule's own sort fields produced.
        """
        if self.group_column is None:
            return [(None, list(self.rows))]

        out = []
        current = object()
        for row in self.rows:
            value = row[self.group_column] if self.group_column < len(row) else None
            if value != current:
                out.append((value, []))
                current = value
            out[-1][1].append(row)
        return out


def detect_column_types(columns, rows, numeric_threshold=0.6):
    """Decide per column whether it is numeric, and what unit it carries.

    A column counts as numeric when most of its non empty values parse as
    numbers. The threshold tolerates a stray note typed into an otherwise
    numeric column, which happens in real models.
    """
    for col in columns:
        values = []
        for row in rows:
            if col.index < len(row):
                value = row[col.index]
                if value is not None and to_text(value).strip() != u"":
                    values.append(value)

        col.is_empty = not values
        if not values:
            continue

        numeric_count = 0
        units = {}
        for value in values:
            parsed, unit = parse_measurement(value)
            if isinstance(parsed, (int, float)):
                numeric_count += 1
                if unit:
                    units[unit] = units.get(unit, 0) + 1

        if numeric_count >= numeric_threshold * len(values):
            col.is_numeric = True
            if units:
                col.unit = max(units.items(), key=lambda kv: kv[1])[0]
    return columns


def apply_parsing(columns, rows):
    """Convert display strings to real values, in place, returning rows.

    Numeric columns lose their unit suffix here because the unit has
    moved into the column header. Text columns keep whatever Revit wrote,
    since that is the user's content, not ours to rewrite.
    """
    parsed_rows = []
    numeric_indices = set(col.index for col in columns if col.is_numeric)
    for row in rows:
        parsed = []
        for i, value in enumerate(row):
            if value is None or to_text(value).strip() == u"":
                parsed.append(None)
            elif i in numeric_indices:
                number, _unit = parse_measurement(value)
                parsed.append(number)
            else:
                parsed.append(to_text(value).strip())
        parsed_rows.append(parsed)
    return parsed_rows


def find_column(columns, *names):
    """First column whose any-level label case-insensitively matches."""
    wanted = set(to_text(n).strip().lower() for n in names)
    for col in columns:
        for label in (col.level1, col.level2, col.level3):
            if label and to_text(label).strip().lower() in wanted:
                return col
    return None


def find_level_column(columns):
    """The column most likely to hold a level, by name then by content."""
    return find_column(columns, u"level", u"h\u00e6\u00f0",
                   u"haed", u"hae\u00f0")


GROUP_FOOTER_RE = re.compile(r"^(?P<label>.+?):\s*\d+\s*$")


def is_render_candidate(cells):
    """A row shaped like one of Revit's rendered group lines.

    Shape only: exactly one populated cell, and it is the first one.
    Being shaped like a group line is not enough to drop a row, because
    a real record with only its first field filled in looks identical.
    Confirmation happens in confirm_render_row.
    """
    if not cells:
        return False
    populated = [c for c in cells
                 if c is not None and to_text(c).strip() != u""]
    if len(populated) != 1:
        return False
    first = cells[0]
    return (first is not None
            and to_text(first).strip() == to_text(populated[0]).strip())


def confirm_render_row(text, known_values):
    """Decide whether a candidate really is a group line, not a record.

    Two signatures count, and nothing else does:

    1. It ends in ': <count>', which is how Revit writes a group footer
       and the grand total, for example '%QQC001 / 980 x 2080mm / R: 9'
       and 'Samtals: 197'.
    2. It repeats verbatim a value that some other column holds on a
       real data row, which is how Revit writes a group header.

    Anything else is treated as data. That bias is deliberate: leaving a
    stray group header in the sheet is visible and easily fixed, whereas
    dropping a genuine row with only one field filled in is silent, and
    a room quietly missing from a schedule is a real problem on site.

    Returns (is_render_row, label).
    """
    text = to_text(text).strip()
    if not text:
        return False, None

    match = GROUP_FOOTER_RE.match(text)
    if match:
        return True, match.group("label").strip()

    if text in known_values:
        return True, text

    return False, None
