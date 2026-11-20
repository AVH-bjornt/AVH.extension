# -*- coding: utf-8 -*-
"""Making dependent views in a batch: their names. No Revit here.

Every chosen parent view gets the same number of dependent views. Each
one's name is built the way Rename Views builds a name, prefix + field +
suffix:

    prefix   e.g. "{parent} - ", the same for every dependent
    field    each dependent's own, counting up per parent (1, 01, 101
             or a letter) and editable per row
    suffix   the same for every dependent

"{parent}" in the prefix or suffix stands for each parent view's own name,
so one prefix gives "Grunnmynd 1. haed - 1" under one parent and
"Grunnmynd 2. haed - 1" under the next. The braces are characters Revit
refuses in a name, so a mistyped token is shown in red rather than
landing in the model.

Uniqueness: a dependent has its parent's view type, and Revit refuses
two views of one view type with the same name (the same assumption
Rename Views makes, see lib/avh_rename/model.py). Revit is the final
word: the script still catches its refusal and rolls the run back.
"""

from avh_rename import model as naming
from avh_sheets import model as batch
from avh_schedules.compat import to_text

PARENT = u"{parent}"
DEFAULT_PREFIX = PARENT + u" - "
DEFAULT_START = u"1"
MAX_PER_PARENT = 50
MAX_VIEWS = 200


def count_problem(text, parents):
    """Why a typed number of dependents per parent cannot be used, or
    u""."""
    text = to_text(text).strip()
    if not text.isdigit():
        return u"type how many per view, as a number"
    count = int(text)
    if count < 1:
        return u"make at least one per view"
    if count > MAX_PER_PARENT:
        return u"at most {0} per view".format(MAX_PER_PARENT)
    if count * max(parents, 1) > MAX_VIEWS:
        return u"at most {0} views at a time ({1} views x {2})".format(
            MAX_VIEWS, parents, count)
    return u""


def fill_in(text, parent_name):
    """`text` with PARENT replaced by the parent view's name."""
    return to_text(text).replace(PARENT, to_text(parent_name))


def fields(count, start, typed):
    """The fields for each parent: `typed` is a list per parent of the
    fields typed so far. Counted from `start` when it is something to
    count from, otherwise each parent's typed fields cut or padded."""
    return [batch.fields(count, start, list(own), u"") for own in typed]


def counts_from(start):
    return batch.counts_from(start)


class Row(object):
    """One dependent view to be made."""

    def __init__(self, parent, field, name, group, problem=u""):
        self.parent = parent
        self.field = field
        self.name = name
        self.group = group
        self.problem = problem


def plan(parents, prefix, fields_per_parent, suffix, taken=()):
    """The rows, parent by parent.

    parents             (parent name, group) pairs, in order
    fields_per_parent   one list of fields per parent
    taken               (group, name) pairs of views already in the model
    """
    taken = set((group, to_text(name)) for group, name in taken)
    rows = []
    for index, ((parent, group), own) in enumerate(
            zip(parents, fields_per_parent)):
        parent = to_text(parent)
        for field in own:
            name = naming.compose(fill_in(prefix, parent), field,
                                  fill_in(suffix, parent))
            rows.append(Row(index, to_text(field), name, group,
                            naming.problem(name)))
    seen = {}
    for row in rows:
        seen[(row.group, row.name)] = seen.get((row.group, row.name), 0) + 1
    for row in rows:
        if row.problem:
            continue
        if seen[(row.group, row.name)] > 1:
            row.problem = u"two new views would both be called this"
        elif (row.group, row.name) in taken:
            row.problem = u"a view of this type is already called this"
    return rows


def blocking(rows):
    return [r for r in rows if r.problem]
