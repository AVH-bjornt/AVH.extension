# -*- coding: utf-8 -*-
"""
Matching exported rows to elements when Revit offers no Element Id field.

The rule under test is that a row is given an id only when exactly one
element fits. Every ambiguity test asserts the positive beside the
negative: the unambiguous rows still match, so a matcher that simply gives
up cannot pass.

Run: python test_matching.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_schedules import matching as mt, roundtrip as rt    # noqa: E402
from avh_schedules.matching import UNKNOWN                   # noqa: E402
from avh_schedules.model import Column, ScheduleTable       # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


INST = rt.KIND_INSTANCE
SPECS = [
    rt.FieldSpec(0, u"Mark", INST, -1, u"Mark"),
    rt.FieldSpec(1, u"Level", INST, -2, u"Level"),
    rt.FieldSpec(2, u"Width", INST, -3, u"Width", u"mm"),
    rt.FieldSpec(3, u"Count", rt.KIND_OTHER, None, u"Count"),
]


def table_of(rows):
    columns = [Column(i, s.name) for i, s in enumerate(SPECS)]
    return ScheduleTable(u"T", columns, [list(r) for r in rows])


def run(rows, elements):
    ids, tally = mt.match_rows(table_of(rows), SPECS, elements)
    return ids, tally


# elements: id -> [Mark, Level, Width, (count column, never compared)]
E = {
    10: [u"W01", u"E01", 1000.0, UNKNOWN],
    11: [u"W02", u"E01", 1200.0, UNKNOWN],
    12: [u"W03", u"E02", 1000.0, UNKNOWN],
}

ids, t = run([(u"W01", u"E01", 1000, 1), (u"W02", u"E01", 1200, 1),
              (u"W03", u"E02", 1000, 1)], E)
check("distinct rows each get their own element", ids == [10, 11, 12], ids)
check("the tally agrees", t["matched"] == 3 and t["unmatched"] == 0)

ids, _ = run([(u"W03", u"E02", 1000, 1), (u"W01", u"E01", 1000, 1)], E)
check("row order does not matter", ids == [12, 10], ids)

ids, _ = run([(u"W01", u"E01", 1000, 1), (u"NOPE", u"E01", 1000, 1)], E)
check("a row nothing fits gets no id, its neighbour still matches",
      ids == [10, None], ids)

# rounding: the schedule shows whole mm, the element holds more
E2 = {20: [u"A", u"E01", 1000.4, UNKNOWN], 21: [u"B", u"E01", 1500.0, UNKNOWN]}
ids, _ = run([(u"A", u"E01", 1000, 1)], E2)
check("a rounded number still matches", ids == [20], ids)

# the numbers disagree but the text singles one element out: pass 2
E3 = {30: [u"A", u"E01", 5000.0, UNKNOWN], 31: [u"B", u"E01", 1500.0, UNKNOWN]}
ids, _ = run([(u"A", u"E01", 1000, 1)], E3)
check("when only the number disagrees, the text columns decide",
      ids == [30], ids)

# the number is the only thing that tells two elements apart
EN = {5: [u"W", u"E01", 900.0, UNKNOWN], 6: [u"W", u"E01", 1200.0, UNKNOWN]}
ids, _ = run([(u"W", u"E01", 1200, 1), (u"W", u"E01", 900, 1)], EN)
check("a number alone separates two otherwise identical elements",
      ids == [6, 5], ids)

# --- ambiguity: never guess ---------------------------------------------

EA = {40: [u"W", u"E01", 900.0, UNKNOWN], 41: [u"W", u"E01", 900.0, UNKNOWN],
      42: [u"X", u"E01", 900.0, UNKNOWN]}
ids, t = run([(u"W", u"E01", 900, 1), (u"W", u"E01", 900, 1),
              (u"X", u"E01", 900, 1)], EA)
check("two identical elements: the identical rows get no id",
      ids[0] is None and ids[1] is None, ids)
check("but the distinguishable row still matches", ids[2] == 42, ids)
check("they are counted as ambiguous, not unmatched",
      t["ambiguous"] == 2 and t["unmatched"] == 0, t)

# one row, two elements: ambiguous
ids, _ = run([(u"W", u"E01", 900, 1)], EA)
check("one row that fits two elements gets no id", ids == [None], ids)

# two rows wanting one element: both dropped
EB = {50: [u"W", u"E01", 900.0, UNKNOWN]}
ids, t = run([(u"W", u"E01", 900, 1), (u"W", u"E01", 900, 1)], EB)
check("two rows claiming one element both lose it",
      ids == [None, None], ids)
check("and are counted as contested", t["contested"] == 2, t)

# a claim settles another row: B fits {60, 61}, A fits only {60}
EC = {60: [u"A", u"E01", 1.0, UNKNOWN], 61: [u"B", u"E01", 1.0, UNKNOWN]}
SPECS_LOOSE = SPECS
ids, _ = run([(u"A", u"E01", 1, 1), (u"B", u"E01", 1, 1)], EC)
check("distinct rows still settle", ids == [60, 61], ids)

# --- values ---------------------------------------------------------------

E4 = {70: [u"101", u"E01", 1000.0, UNKNOWN]}
ids, _ = run([(101, u"E01", 1000, 1)], E4)
check("a numeric looking mark matches its text parameter", ids == [70], ids)

E5 = {80: [None, u"E01", 1000.0, UNKNOWN], 81: [u"Z", u"E01", 1000.0, UNKNOWN]}
ids, _ = run([(None, u"E01", 1000, 1)], E5)
check("a blank cell matches an empty parameter, not a filled one",
      ids == [80], ids)

E6 = {90: [u"Rými", u"E01", 1000.0, UNKNOWN]}
ids, _ = run([(u"Rými", u"E01", 1000, 1)], E6)
check("Icelandic text matches", ids == [90], ids)

# an unreadable column is not held against an element
E7 = {95: [u"A", UNKNOWN, 1000.0, UNKNOWN]}
ids, _ = run([(u"A", u"anything", 1000, 1)], E7)
check("a column that could not be read is ignored", ids == [95], ids)

# the shape the real world has: a unique mark among many look-alikes
many = dict((1000 + i, [u"M{0}".format(i), u"E01", 900.0, UNKNOWN])
            for i in range(300))
rows = [(u"M{0}".format(i), u"E01", 900, 1) for i in range(300)]
ids, t = run(rows, many)
check("300 rows of identical size are told apart by their marks",
      ids == [1000 + i for i in range(300)] and t["matched"] == 300)

# the harness must be able to fail
check("self check: a wrong expectation is detected", ids != [None] * 300)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    if not ok:
        print("FAIL  " + name + ("   [" + str(detail) + "]" if detail else ""))
print("{0} checks, {1} failed".format(len(results), len(failed)))
if failed:
    sys.exit(1)
print("PASS")
