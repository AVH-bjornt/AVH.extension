# -*- coding: utf-8 -*-
"""Naming family types from their own parameter values. No Revit here.

A name is written as a pattern, plain text with parameter names in
braces:

    {Width}x{Height} {Material}       ->  900x2100 Eik
    D-{Type Mark}                     ->  D-H12

Each type's own values go in. Parameter names are matched ignoring case,
so {width} finds Width. The values arrive here as text, already the way
Revit shows them (project units, decimal comma), so 900 mm reads 900.

What a value may not bring into a name: the characters Revit refuses in
an element name (\\ : { } [ ] | ; < > ? ` ~) are replaced by "-" when
they come from a value, and refused outright when typed in the pattern,
where they can only be a mistake. Runs of spaces left by an empty value
become one and the ends are trimmed. The window shows every name exactly
as it will be written, so nothing is changed unseen.

Uniqueness: Revit refuses two types with one name in the same family
(for system families, such as walls, the same Family Name in the same
category). Each item carries a `family` key, and `taken` holds the names
of the types in each family that are not being renamed. A name is
compared ignoring case, which is stricter than may be needed and never
looser. Two chosen types may swap names; the script writes through
temporary names for that.
"""

import re

from avh_schedules.compat import to_text
from avh_rename.model import FORBIDDEN

REPLACEMENT = u"-"
FAMILY_NAME = u"Family Name"
TYPE_NAME = u"Type Name"
CATEGORY = u"Category"
DEFAULT_PATTERN = u"{" + TYPE_NAME + u"}"
MAX_LENGTH = 250

_TOKEN = re.compile(u"\\{([^{}]*)\\}")
_SPACES = re.compile(u"\\s+")


class Item(object):
    """One type to rename, as plain data.

    `ident` its element id as an int, `family` the key of the family its
    name must be unique in, `family_label` that family as shown, `current`
    its name now, `values` {parameter name: value as text}.
    """

    def __init__(self, index, ident, family, family_label, current, values):
        self.index = index
        self.ident = ident
        self.family = family
        self.family_label = to_text(family_label)
        self.current = to_text(current)
        self.values = dict((to_text(k), to_text(v))
                           for k, v in values.items())


class Row(object):
    """One type, the name it would get, and why it cannot (u"" if it
    can)."""

    def __init__(self, item, new, problem=u""):
        self.item = item
        self.new = new
        self.problem = problem

    @property
    def changes(self):
        return self.new != self.item.current


def tokens(pattern):
    """The parameter names in `pattern`, in order, as typed."""
    return [name.strip() for name in _TOKEN.findall(to_text(pattern))]


def literal_text(pattern):
    """`pattern` with its {tokens} taken out."""
    return _TOKEN.sub(u"", to_text(pattern))


def lookup(values, name):
    """The value of parameter `name` in `values`, ignoring case, or None
    when there is no such parameter."""
    name = to_text(name).strip()
    if name in values:
        return values[name]
    lowered = name.lower()
    for key, value in values.items():
        if key.lower() == lowered:
            return value
    return None


def clean_value(value):
    """A value made safe to put in a name."""
    value = to_text(value)
    for char in FORBIDDEN:
        value = value.replace(char, REPLACEMENT)
    return value.replace(u"\r", u" ").replace(u"\n", u" ")


def tidy(name):
    return _SPACES.sub(u" ", name).strip()


def build(pattern, values):
    """The name `pattern` gives a type with these `values`. A parameter
    the type does not have gives nothing."""
    def fill(match):
        found = lookup(values, match.group(1))
        return clean_value(found) if found else u""
    return tidy(_TOKEN.sub(fill, to_text(pattern)))


def available(items):
    """[(name, count)]: every parameter name any item has, with how many
    of the items have it, the ones all have first, then by name."""
    counts, spelling = {}, {}
    for item in items:
        seen = set()
        for name in item.values:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
            spelling.setdefault(key, name)
    total = len(items)
    keys = sorted(counts, key=lambda k: (counts[k] != total, k))
    return [(spelling[k], counts[k]) for k in keys]


def pattern_problem(pattern, items):
    """Why `pattern` cannot be used for any of `items`, or u""."""
    pattern = to_text(pattern)
    if not pattern.strip():
        return u"type a name pattern, such as {Width}x{Height}"
    depth = 0
    for char in pattern:
        depth += 1 if char == u"{" else (-1 if char == u"}" else 0)
        if depth not in (0, 1):
            return u"braces do not pair up: each {name} opens and closes"
    if depth:
        return u"braces do not pair up: each {name} opens and closes"
    names = tokens(pattern)
    if any(not name for name in names):
        return u"{} is empty: put a parameter name between the braces"
    for name in names:
        if not any(lookup(item.values, name) is not None for item in items):
            return u"none of the types has a parameter called {0}".format(
                name)
    bad = sorted(set(c for c in literal_text(pattern) if c in FORBIDDEN))
    if bad:
        return u"Revit does not allow {0} in a type name".format(
            u" ".join(bad))
    return u""


def plan(items, pattern, taken=None):
    """(rows, problem): the name each type gets, in the order given, and
    why nothing can be written (u"" when it can). `taken` is
    {family: names of the types there that are not being renamed}.

    A row whose name cannot be used carries its own problem, and then so
    does the plan: one bad name stops the run, so a family is never left
    half renamed.
    """
    taken = taken or {}
    problem = pattern_problem(pattern, items)
    if problem:
        return [], problem
    rows = [Row(item, build(pattern, item.values)) for item in items]

    others = {}
    for family, names in taken.items():
        others[family] = set(to_text(n).lower() for n in names)
    claimed = {}
    for row in rows:
        key = (row.item.family, row.new.lower())
        claimed.setdefault(key, []).append(row)

    for row in rows:
        if not row.new:
            row.problem = u"the name would be empty"
        elif len(row.new) > MAX_LENGTH:
            row.problem = u"longer than {0} characters".format(MAX_LENGTH)
        elif row.new.lower() in others.get(row.item.family, ()):
            row.problem = (u"another type in {0} is already called "
                           u"that".format(row.item.family_label))
        elif len(claimed[(row.item.family, row.new.lower())]) > 1:
            row.problem = u"{0} types in {1} would get this name".format(
                len(claimed[(row.item.family, row.new.lower())]),
                row.item.family_label)

    bad = [row for row in rows if row.problem]
    if bad:
        first = bad[0]
        summary = u"{0} would be {1}: {2}".format(
            first.item.current, first.new or u"(empty)", first.problem)
        if len(bad) > 1:
            summary = u"{0} names cannot be used. First, {1}".format(
                len(bad), summary)
        return rows, summary
    return rows, u""


def changed(rows):
    return [row for row in rows if row.changes]


def blockers(rows):
    """The changing rows whose current name another changing row wants in
    the same family. Those go through a temporary name first, so names
    can be swapped or shifted along."""
    changing = changed(rows)
    wanted = set((row.item.family, row.new.lower()) for row in changing)
    return [row for row in changing
            if (row.item.family, row.item.current.lower()) in wanted]


def temporary_name(row):
    """A name nothing else will have, kept valid: the old name and the
    element id."""
    return u"{0} (AVH {1})".format(row.item.current, row.item.ident)
