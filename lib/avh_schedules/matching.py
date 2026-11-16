# -*- coding: utf-8 -*-
"""
Work out which element each exported row is, when Revit will not say.

Revit 2025 offers no Element Id schedule field through the API (the first
real run on a window schedule found none among 1289 offered fields), so the
id cannot simply be exported. This module recovers it: the elements the
schedule shows are collected, the value of every parameter column is read
from each of them, and a row is matched to the one element whose values
agree with it.

## The safety rule

A row is given an id ONLY when exactly one element fits. Two elements that
fit, or none, and the row gets no id: it stays in the workbook, and the
import simply ignores it. A row that cannot be edited is an inconvenience.
A row attached to the wrong element would write somebody's edit onto
another element, so ambiguity always loses.

Two rows claiming one element are both dropped for the same reason.

## Comparing

The export holds display text, rounded as the schedule rounds it, and the
element holds the exact value. So text columns must be equal and number
columns must agree to within the rounding the column shows. A first strict
pass requires every column to agree. A row that no element fits is retried
ignoring number columns, because the likeliest reason for a miss is a
number formatted differently, and the text columns alone often single the
element out.

Pure Python, no Revit. Runs on IronPython 2.7 and CPython 3 alike.
"""

from . import roundtrip as rt
from .compat import text_type, to_text


class _Unknown(object):
    """An element value that could not be read."""

    def __repr__(self):
        return "UNKNOWN"


UNKNOWN = _Unknown()


def _text_key(value):
    if value is None:
        return u""
    if rt._is_number(value):
        return rt.number_text(value)
    return to_text(value).strip()


def _decimals(values):
    """The most decimal places any value in a column shows."""
    most = 0
    for value in values:
        if isinstance(value, float):
            text = repr(value)
            if "." in text and "e" not in text:
                most = max(most, len(text.split(".")[1].rstrip("0")))
    return most


def _number_agrees(cell, element_value, tolerance):
    """Whether a number from the element is what the cell shows."""
    if rt._is_blank(cell):
        return element_value is None or element_value == 0
    if element_value is None:
        return False
    number = rt.as_number(cell)
    if number is None:
        number = rt.as_yes_no(cell)
    if number is None:
        return False
    return abs(number - element_value) <= tolerance


class Matcher(object):
    def __init__(self, table, specs, element_values):
        self.table = table
        self.specs = specs
        self.values = element_values
        n = len(specs)

        comparable = [s.kind in (rt.KIND_INSTANCE, rt.KIND_TYPE)
                      for s in specs]
        self.text_cols = []
        self.num_cols = []
        for c in range(n):
            if not comparable[c]:
                continue
            seen = [ev[c] for ev in element_values.values()
                    if ev[c] is not UNKNOWN]
            if not seen:
                continue
            if all(v is None or isinstance(v, text_type) for v in seen):
                self.text_cols.append(c)
            else:
                self.num_cols.append(c)

        self.tolerance = {}
        for c in self.num_cols:
            column = [row[c] for row in table.rows if c < len(row)]
            self.tolerance[c] = 0.5 * (10 ** -_decimals(column)) + 1e-9

    def fits(self, row, element_id, with_numbers):
        ev = self.values[element_id]
        for c in self.text_cols:
            if ev[c] is UNKNOWN:
                continue
            cell = row[c] if c < len(row) else None
            if _text_key(cell) != _text_key(ev[c]):
                return False
        if with_numbers:
            for c in self.num_cols:
                if ev[c] is UNKNOWN:
                    continue
                cell = row[c] if c < len(row) else None
                if not _number_agrees(cell, ev[c], self.tolerance[c]):
                    return False
        return True

    def candidates(self, row, pool, with_numbers):
        return [e for e in pool if self.fits(row, e, with_numbers)]


def match_rows(table, specs, element_values):
    """One element id (or None) per row of the table, plus a tally.

    element_values maps element id -> one value per column: UNKNOWN for a
    column that cannot be compared, else None, text or a number in the
    column's display unit.
    """
    matcher = Matcher(table, specs, element_values)
    rows = table.rows
    everyone = sorted(element_values)
    ids = [None] * len(rows)
    options = [None] * len(rows)

    # pass 1: every column must agree
    for i, row in enumerate(rows):
        options[i] = matcher.candidates(row, everyone, True)
    # pass 2: a row nothing fits is retried on its text columns alone
    for i, row in enumerate(rows):
        if not options[i]:
            options[i] = matcher.candidates(row, everyone, False)

    # an element taken by a row that is certain is not a candidate for the
    # others. Repeat, because each claim can settle another row.
    #
    # Each round, every undecided row with exactly one live candidate
    # asks for it. An element asked for by one row is given to it. An
    # element asked for by several rows is given to none of them and is
    # taken out of play: two rows cannot both be right.
    claimed = set()
    dropped = set()
    contested = 0
    changed = True
    while changed:
        changed = False
        wants = {}
        for i, found in enumerate(options):
            if ids[i] is not None or i in dropped:
                continue
            live = [e for e in found if e not in claimed]
            if len(live) == 1:
                wants.setdefault(live[0], []).append(i)
        for element_id, askers in wants.items():
            claimed.add(element_id)
            changed = True
            if len(askers) == 1:
                ids[askers[0]] = element_id
            else:
                dropped.update(askers)
                contested += len(askers)

    matched = len([i for i in ids if i is not None])
    unmatched = len([o for o in options if not o])
    return ids, {
        "rows": len(rows),
        "matched": matched,
        "unmatched": unmatched,
        "ambiguous": len(rows) - matched - unmatched,
        "contested": contested,
        # which columns told rows apart, so a report can say why a
        # schedule matched poorly
        "compared": [specs[c].name for c in
                     sorted(matcher.text_cols + matcher.num_cols)],
    }
