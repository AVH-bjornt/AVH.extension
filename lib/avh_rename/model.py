# -*- coding: utf-8 -*-
"""Renaming views and sheets as prefix + field + suffix. No Revit here.

Every chosen name is built from three parts:

    prefix   the same for every chosen view or sheet
    field    each one's own, the part that changes between them
    suffix   the same for every chosen view or sheet

When the tool opens, the prefix and suffix are the start and end that
the chosen names already share, and each field is what is left of its
name. Changing the prefix then changes every name at once while each
one keeps its own field.

Uniqueness: Revit refuses two views of the same view type with the same
name, but lets a floor plan and a ceiling plan share one ("Level 1").
Sheet names may repeat freely (the sheet number is what is unique).
Each item carries a `group`; two names clash only within a group, and a
group of None never clashes. Revit itself is the final word: the script
still catches its refusal and rolls the whole run back.
"""

from avh_schedules.compat import to_text

# Characters Revit will not accept in a view or sheet name.
FORBIDDEN = u"\\:{}[]|;<>?`~"


def _is_digit(char):
    return u"0" <= char <= u"9"


def common_prefix(names):
    """The longest start shared by every name, not ending part way
    through a number: "A-101" and "A-102" share "A-", not "A-10", so the
    field is the whole number."""
    if len(names) < 2:
        return u""
    shortest = min(len(n) for n in names)
    length = 0
    while length < shortest and all(n[length] == names[0][length]
                                    for n in names):
        length += 1
    while length > 0 and _is_digit(names[0][length - 1]) and any(
            length < len(n) and _is_digit(n[length]) for n in names):
        length -= 1
    return names[0][:length]


def common_suffix(names, prefix_length=0):
    """The longest end shared by every name, not starting part way
    through a number, and never reaching back into the prefix."""
    if len(names) < 2:
        return u""
    room = min(len(n) for n in names) - prefix_length
    length = 0
    while length < room and all(n[-1 - length] == names[0][-1 - length]
                                for n in names):
        length += 1
    while length > 0 and _is_digit(names[0][-length]) and any(
            len(n) - length - 1 >= prefix_length and
            _is_digit(n[-length - 1]) for n in names):
        length -= 1
    return names[0][len(names[0]) - length:] if length else u""


def affixes(names):
    """(prefix, suffix) the names already share."""
    names = [to_text(n) for n in names]
    prefix = common_prefix(names)
    return prefix, common_suffix(names, len(prefix))


def field_of(name, prefix, suffix):
    """What is left of a name once its prefix and suffix are taken off.
    A part it does not have is left on."""
    name = to_text(name)
    if prefix and name.startswith(prefix):
        name = name[len(prefix):]
    if suffix and name.endswith(suffix) and len(name) >= len(suffix):
        name = name[:len(name) - len(suffix)]
    return name


def compose(prefix, field, suffix):
    return to_text(prefix) + to_text(field) + to_text(suffix)


def problem(name):
    """Why Revit would refuse a name, or u"" if it would not."""
    if not name.strip():
        return u"the name is empty"
    bad = [c for c in FORBIDDEN if c in name]
    if bad:
        return u"Revit does not allow {0} in a name".format(u" ".join(bad))
    return u""


def numbers(count, start):
    """`count` field values counting up from `start`, or None if `start`
    is not something to count from.

    "1" gives 1, 2, 3; "01" keeps its width, 01, 02 ... 10; "101" gives
    101, 102; a single letter "A" gives A, B, C up to Z.
    """
    start = to_text(start).strip()
    if start and all(_is_digit(c) for c in start):
        first = int(start)
        width = len(start)
        return [u"{0:0{1}d}".format(first + i, width) for i in range(count)]
    if len(start) == 1 and start.isalpha() and start.upper() in \
            u"ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        base = ord(start)
        top = ord(u"Z") if start.isupper() else ord(u"z")
        if base + count - 1 > top:
            return None
        return [unichr_(base + i) for i in range(count)]
    return None


def unichr_(code):
    try:
        return unichr(code)          # noqa: F821  Python 2
    except NameError:
        return chr(code)


class Row(object):
    """One chosen view or sheet and what it would become."""

    def __init__(self, old, new, group, problem_text=u""):
        self.old = old
        self.new = new
        self.group = group
        self.problem = problem_text

    @property
    def changes(self):
        return self.new != self.old


def plan(items, prefix, suffix, fields, taken=()):
    """The rows for a set of chosen items.

    items   (old name, group) pairs, in order
    fields  one field per item
    taken   (group, name) pairs belonging to views that are not being
            renamed, which a new name must not land on
    """
    taken = set(taken)
    rows = []
    for (old, group), field in zip(items, fields):
        new = compose(prefix, field, suffix)
        rows.append(Row(to_text(old), new, group, problem(new)))
    seen = {}
    for row in rows:
        if row.group is None:
            continue
        seen.setdefault((row.group, row.new), []).append(row)
    for row in rows:
        if row.problem or row.group is None:
            continue
        if len(seen[(row.group, row.new)]) > 1:
            row.problem = u"two of the chosen would both be called this"
        elif row.changes and (row.group, row.new) in taken:
            row.problem = u"another view is already called this"
    return rows


def blocking(rows):
    return [r for r in rows if r.problem]


def temporary_name(index, token):
    """A name no real view has, held for a moment while names are
    swapped, so A -> B and B -> A can both happen."""
    return u"AVH rename {0} {1}".format(token, index)
