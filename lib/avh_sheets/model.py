# -*- coding: utf-8 -*-
"""Making a batch of sheets: numbers and names. No Revit here.

Each new sheet's number and name are built the way Rename Views builds a
name, prefix + field + suffix:

    number   e.g. "A-" + "101" + ""   the field counts up row by row
    name     e.g. "Grunnmynd " + "1. haed" + ""   the field per row

Sheet numbers must be unique across every sheet in the model, placeholder
sheets included. Whether Revit compares them ignoring case is not
confirmed, so they are compared ignoring case here, which can only refuse
more than Revit would, never less. Sheet names may repeat.
"""

import re

from avh_rename import model as naming
from avh_schedules.compat import to_text

MAX_SHEETS = 200
DEFAULT_START = u"101"

_TRAILING_NUMBER = re.compile(u"^(.*?)(\\d+)(\\D*)$")


def _natural(text):
    """Sort key that puts A-9 before A-10."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(u"(\\d+)", to_text(text))]


def suggest(existing_numbers):
    """(prefix, start, suffix) continuing the model's sheet numbering.

    The last existing number that has a number in it is taken apart, and
    the next one along is suggested: after A-105 comes "A-", "106", "".
    The width is kept, so 009 is followed by 010. A model with no sheets,
    or none with a number in them, starts at 101.
    """
    numbered = [n for n in (to_text(x) for x in existing_numbers)
                if _TRAILING_NUMBER.match(n)]
    if not numbered:
        return u"", DEFAULT_START, u""
    last = sorted(numbered, key=_natural)[-1]
    prefix, digits, suffix = _TRAILING_NUMBER.match(last).groups()
    start = u"{0:0{1}d}".format(int(digits) + 1, len(digits))
    return prefix, start, suffix


def count_problem(text):
    """Why a typed sheet count cannot be used, or u""."""
    text = to_text(text).strip()
    if not text.isdigit():
        return u"type how many sheets, as a number"
    count = int(text)
    if count < 1:
        return u"make at least one sheet"
    if count > MAX_SHEETS:
        return u"at most {0} sheets at a time".format(MAX_SHEETS)
    return u""


class Row(object):
    """One sheet to be made."""

    def __init__(self, number, name, problem=u""):
        self.number = number
        self.name = name
        self.problem = problem


def _key(number):
    return to_text(number).strip().lower()


def plan(number_prefix, number_fields, number_suffix,
         name_prefix, name_fields, name_suffix, taken_numbers=()):
    """The rows for a batch. `taken_numbers` are the numbers sheets in
    the model already have."""
    taken = set(_key(n) for n in taken_numbers)
    rows = []
    for number_field, name_field in zip(number_fields, name_fields):
        number = naming.compose(number_prefix, number_field, number_suffix)
        name = naming.compose(name_prefix, name_field, name_suffix)
        problem = naming.problem(number)
        if problem:
            problem = u"number: " + problem
        else:
            name_problem = naming.problem(name)
            if name_problem:
                problem = u"name: " + name_problem
        rows.append(Row(number, name, problem))
    counts = {}
    for row in rows:
        counts[_key(row.number)] = counts.get(_key(row.number), 0) + 1
    for row in rows:
        if row.problem:
            continue
        if counts[_key(row.number)] > 1:
            row.problem = u"two new sheets would both be numbered this"
        elif _key(row.number) in taken:
            row.problem = u"a sheet is already numbered this"
    return rows


def blocking(rows):
    return [r for r in rows if r.problem]


def number_fields(count, start):
    """Number fields counting up from `start`, or None if `start` is not
    something to count from (1, 01, 101 or a single letter)."""
    return naming.numbers(count, start)


def fields(count, start, typed, fill):
    """The fields for `count` rows: counting up from `start` when it is
    something to count from (1, 01, 101 or a single letter), otherwise
    the fields typed so far, cut or padded with `fill`. Used alike for
    the number fields and the name fields."""
    counted = naming.numbers(count, start)
    if counted is not None:
        return counted
    return resize(typed, count, fill)


def counts_from(start):
    """Whether `start` is something rows can be numbered from."""
    return naming.numbers(1, start) is not None


def collection_problem(name, existing_names):
    """Why a new sheet collection cannot be called `name`, or u"".

    Whether Revit requires collection names to be unique, and whether it
    compares them ignoring case, is not confirmed; refusing a name that
    matches an existing one ignoring case can only refuse more than Revit
    would, and a new collection beside an old one of the same name would
    be confusing in the Project Browser anyway."""
    name = to_text(name).strip()
    if not name:
        return u"type a name for the new sheet collection"
    problem = naming.problem(name)
    if problem:
        return problem
    if name.lower() in set(to_text(n).strip().lower()
                           for n in existing_names):
        return u"a sheet collection already has this name"
    return u""


def resize(values, count, fill):
    """`values` cut or padded to `count`, padding with `fill`."""
    values = list(values)[:count]
    return values + [fill] * (count - len(values))
