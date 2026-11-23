# -*- coding: utf-8 -*-
"""Renumbering an instance parameter as prefix + running number + suffix.
No Revit here.

Every chosen element gets

    prefix   the same for every element
    number   counting up from a start value by a step
    suffix   the same for every element

The start value counts the way Batch Sheets and Rename Views count:
"1" gives 1, 2, 3; "01" keeps its width, 01, 02 ... 10; "101" gives 101,
102; a single letter "A" gives A, B, C and stops at Z. The step applies to
letters too, so "A" with step 2 gives A, C, E.

The order the numbers go in is chosen: the order the elements were
picked, left to right or top to bottom by their position in plan, by the
value they hold now, or by element id. Positions are rounded to
`ROUND_FEET` first, so elements drawn in a line a few millimetres out of
true still count as level with one another and the other axis decides.

A start with a leading zero fixes the width: "001" means three digits,
so #QQC001 to #QQC999 and no further. Numbers that would outgrow it are
refused rather than written as #QQC1000, which would break the format
and sort before #QQC101 in a schedule. "1" and "101" have no fixed
width and simply grow.

"Only empty" numbers just the elements that hold no value; the others
keep theirs, and the numbers they already use are skipped, so a run
started at #QQC001 fills the gaps and then carries on after the
highest, without giving any number twice.

When the elements already hold values like #QQC014, `suggest` offers
their prefix, width and suffix as the window's starting point, so the
format is kept without retyping it.

A whole number (Integer) parameter takes digits only: no prefix, no
suffix, no letters. That is refused here, in the window, rather than by
Revit part way through the run.
"""

import re

from avh_schedules.compat import to_text

ORDER_PICKED = u"In the order picked"
ORDER_X = u"Left to right (then top to bottom)"
ORDER_Y = u"Top to bottom (then left to right)"
ORDER_VALUE = u"By the value they hold now"
ORDER_ID = u"By element id (oldest first)"

ORDERS_PICKED = (ORDER_PICKED, ORDER_X, ORDER_Y, ORDER_VALUE, ORDER_ID)
ORDERS_SELECTED = (ORDER_X, ORDER_Y, ORDER_VALUE, ORDER_ID)

TEXT = u"String"
INTEGER = u"Integer"

DEFAULT_START = u"1"
DEFAULT_STEP = u"1"
MAX_STEP = 1000

# 10 mm in Revit's internal feet.
ROUND_FEET = 10.0 / 304.8

_DIGITS = re.compile(u"(\\d+)")
_NUMBERED = re.compile(u"^(.*?)(\\d+)(\\D*)$")


class Item(object):
    """One element to number, as plain data.

    `index` is its place in the order it was picked or selected, `ident`
    its element id as an int, `x` and `y` its position in plan in feet or
    None when it has none, `current` the parameter's value now as text.
    """

    def __init__(self, index, ident, label, current, x=None, y=None):
        self.index = index
        self.ident = ident
        self.label = to_text(label)
        self.current = to_text(current)
        self.x = x
        self.y = y


class Row(object):
    """One element and the value it would get."""

    def __init__(self, item, new):
        self.item = item
        self.new = new

    @property
    def changes(self):
        return self.new != self.item.current


def _is_digit(char):
    return u"0" <= char <= u"9"


def _letter(code):
    try:
        return unichr(code)          # noqa: F821  Python 2
    except NameError:
        return chr(code)


def step_problem(step):
    """Why a typed step cannot be used, or u""."""
    step = to_text(step).strip()
    if not step or not all(_is_digit(c) for c in step):
        return u"the step is a whole number, 1 or more"
    if int(step) < 1:
        return u"the step is a whole number, 1 or more"
    if int(step) > MAX_STEP:
        return u"the step is at most {0}".format(MAX_STEP)
    return u""


def sequence(count, start, step=1):
    """`count` numbers counting up from `start` by `step`, or None when
    `start` is not something to count from or letters would run past Z.
    """
    start = to_text(start).strip()
    if start and all(_is_digit(c) for c in start):
        first = int(start)
        width = len(start)
        return [u"{0:0{1}d}".format(first + i * step, width)
                for i in range(count)]
    if len(start) == 1 and start.upper() in u"ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        base = ord(start)
        top = ord(u"Z") if start.isupper() else ord(u"z")
        if count and base + (count - 1) * step > top:
            return None
        return [_letter(base + i * step) for i in range(count)]
    return None


def fixed_width(start):
    """True when `start` pins the number of digits: a leading zero."""
    start = to_text(start).strip()
    return (len(start) > 1 and start[0] == u"0" and
            all(_is_digit(c) for c in start))


def suggest(values):
    """(prefix, start, suffix) in the format most of `values` already
    have, such as (u"#QQC", u"001", u"") from #QQC014, or None when none
    ends in a number. The width is kept only when some value is padded
    with a leading zero; otherwise the start is "1"."""
    groups = {}
    for value in values:
        match = _NUMBERED.match(to_text(value).strip())
        if not match:
            continue
        prefix, digits, suffix = match.groups()
        key = (prefix, len(digits), suffix)
        seen = groups.setdefault(key, [0, False])
        seen[0] += 1
        if len(digits) > 1 and digits[0] == u"0":
            seen[1] = True
    if not groups:
        return None
    (prefix, width, suffix), (_count, padded) = max(
        groups.items(), key=lambda pair: (pair[1][0], pair[1][1]))
    start = u"1".zfill(width) if padded else u"1"
    return prefix, start, suffix


def is_empty(value):
    """True when a parameter holds nothing: no text, or only spaces."""
    return not to_text(value).strip()


def _letters_room(start, step):
    """How many letters fit from a letter `start` up to Z (or z)."""
    top = ord(u"Z") if start.isupper() else ord(u"z")
    return max((top - ord(start)) // step + 1, 0)


def numbered(count, prefix, start, step, suffix, storage, taken=()):
    """(values, problem): `count` new values, skipping any in `taken`,
    or ([], why) when they cannot all be made. `start` and `step` are
    assumed already usable (see settings_problem)."""
    start = to_text(start).strip()
    step = int(to_text(step).strip())
    taken = set(to_text(t) for t in taken)
    size = count + len(taken)
    letters = not all(_is_digit(c) for c in start)
    if letters:
        size = min(size, _letters_room(start, step))
    pool = sequence(size, start, step) or []
    numbers, values = [], []
    for number in pool:
        if storage == INTEGER:
            value = to_text(int(number))
        else:
            value = to_text(prefix) + number + to_text(suffix)
        if value in taken:
            continue
        numbers.append(number)
        values.append(value)
        if len(values) == count:
            break
    if len(values) < count:
        return [], u"{0} value(s) counted from {1} run past Z".format(
            count, start)
    if storage != INTEGER and fixed_width(start) and numbers and             len(numbers[-1]) > len(start):
        if taken:
            return [], (u"{0} keeps {1} digits, and {2} new value(s) "
                        u"beside those already used would run to "
                        u"{3}".format(start, len(start), count,
                                      numbers[-1]))
        room = int(u"9" * len(start)) - int(start)
        fit = room // step + 1
        return [], (u"{0} keeps {1} digits, so only {2} element(s) fit, "
                    u"not {3} (the last would be {4})".format(
                        start, len(start), max(fit, 0), count, numbers[-1]))
    return values, u""


def settings_problem(count, prefix, start, step, suffix, storage,
                     taken=()):
    """Why these settings cannot give `count` elements a value, or u""."""
    problem = step_problem(step)
    if problem:
        return problem
    start = to_text(start).strip()
    if not start:
        return u"type a start value: 1, 01, 101 or a letter"
    if sequence(1, start) is None:
        return u"start at 1, 01, 101 or a single letter"
    if storage == INTEGER:
        if to_text(prefix) or to_text(suffix):
            return (u"this parameter holds a whole number, so it takes no "
                    u"prefix or suffix")
        if not all(_is_digit(c) for c in start):
            return u"this parameter holds a whole number, so no letters"
    return numbered(count, prefix, start, step, suffix, storage, taken)[1]


def natural_key(text):
    """Sorts "D-2" before "D-10", and an empty value last."""
    text = to_text(text).strip()
    parts = _DIGITS.split(text.lower())
    key = [(0, int(p), u"") if p.isdigit() else (1, 0, p) for p in parts
           if p != u""]
    return (0 if text else 1, key)


def _snap(value):
    return int(round(value / ROUND_FEET))


def ordered(items, order):
    """`items` in the order the numbers go in. Elements with no position
    go after those with one, in the order they were picked."""
    items = list(items)
    if order == ORDER_PICKED:
        return sorted(items, key=lambda i: i.index)
    if order == ORDER_ID:
        return sorted(items, key=lambda i: (i.ident, i.index))
    if order == ORDER_VALUE:
        return sorted(items, key=lambda i: (natural_key(i.current), i.index))
    placed = [i for i in items if i.x is not None and i.y is not None]
    unplaced = sorted([i for i in items if i.x is None or i.y is None],
                      key=lambda i: i.index)
    if order == ORDER_Y:
        placed.sort(key=lambda i: (-_snap(i.y), _snap(i.x), i.index))
    else:
        placed.sort(key=lambda i: (_snap(i.x), -_snap(i.y), i.index))
    return placed + unplaced


def plan(items, order, prefix, start, step, suffix, storage=TEXT,
         only_empty=False):
    """(rows, problem): the value each element gets, in numbering order,
    and why nothing can be written (u"" when it can). The rows are empty
    when there is a problem.

    `only_empty`: elements that already hold a value keep it (their row
    does not change) and only the empty ones are numbered, skipping every
    value the others already hold, so nothing is given twice. Taken
    values are those of the chosen elements only, not the whole model.
    """
    items = ordered(items, order)
    if only_empty:
        targets = [i for i in items if is_empty(i.current)]
        taken = [i.current for i in items if not is_empty(i.current)]
    else:
        targets, taken = items, []
    problem = settings_problem(len(targets), prefix, start, step, suffix,
                               storage, taken)
    if problem:
        return [], problem
    values = numbered(len(targets), prefix, start, step, suffix, storage,
                      taken)[0]
    given = dict((id(item), value) for item, value in zip(targets, values))
    rows = [Row(item, given.get(id(item), item.current)) for item in items]
    return rows, u""


def changed(rows):
    return [row for row in rows if row.changes]
