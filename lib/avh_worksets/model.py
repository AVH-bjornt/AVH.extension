# -*- coding: utf-8 -*-
"""What moves where, for the datum workset tool.

No Revit here. Which elements are worth writing to, how the workset
picker is labelled, and how the run is counted, are all decided on plain
values so they can be tested outside Revit.
"""

from avh_schedules.compat import to_text

# The name AVH uses. It is not required to exist: the picker lists what
# the model actually has, and this only decides which entry is offered
# first and marked, so a model that names it differently still works.
CONVENTIONAL = u"Shared Views, Levels, Grids"

GRIDS = "grids"
LEVELS = "levels"
VIEWS = "views"

KINDS = ((GRIDS, u"Grids"), (LEVELS, u"Levels"), (VIEWS, u"Views"))
KIND_LABELS = dict(KINDS)

# Why an element was left alone. Each one is reported with the element
# named, because "12 skipped" tells nobody what to do next.
ALREADY_THERE = u"already in that workset"
NO_PARAMETER = u"has no workset parameter"
READ_ONLY = u"its workset is read only"
OWNED = u"checked out by someone else"
REFUSED = u"the write was refused"


def workset_labels(names, conventional=CONVENTIONAL):
    """Picker labels, the conventional name first and marked.

    Returns (labels, mapping) where mapping is label to workset name.
    Two worksets cannot share a name in Revit, so no suffixing is needed
    here, unlike the warning and phase pickers.
    """
    ordered = [name for name in names if to_text(name) == conventional]
    ordered += [name for name in names if to_text(name) != conventional]

    labels = []
    mapping = {}
    for name in ordered:
        text = to_text(name)
        label = text
        if text == conventional:
            label = u"{0}  (the usual one)".format(text)
        labels.append(label)
        mapping[label] = text
    return labels, mapping


def needs_move(current_id, target_id):
    """True when the element is not already in the target workset."""
    if current_id is None:
        return True
    return current_id != target_id


class Tally(object):
    """Counts per kind, and the reasons anything was left alone."""

    def __init__(self):
        self.moved = {}
        self.already = {}
        self.skipped = {}
        self.problems = []
        self._seen = set()

    def count_moved(self, kind):
        self.moved[kind] = self.moved.get(kind, 0) + 1

    def count_already(self, kind):
        self.already[kind] = self.already.get(kind, 0) + 1

    def count_skipped(self, kind, reason, name=u"", element_id=None):
        self.skipped[kind] = self.skipped.get(kind, 0) + 1
        key = (kind, to_text(reason))
        if key not in self._seen:
            self._seen.add(key)
        self.problems.append((kind, to_text(reason), to_text(name),
                              element_id))

    def total_moved(self):
        return sum(self.moved.values())

    def total_already(self):
        return sum(self.already.values())

    def total_skipped(self):
        return sum(self.skipped.values())

    def rows(self):
        """(label, moved, already there, skipped) per kind, in order."""
        rows = []
        for key, label in KINDS:
            moved = self.moved.get(key, 0)
            already = self.already.get(key, 0)
            skipped = self.skipped.get(key, 0)
            if moved or already or skipped:
                rows.append((label, moved, already, skipped))
        return rows

    def reasons(self):
        """(kind label, reason, count) for everything left alone."""
        counts = {}
        order = []
        for kind, reason, _name, _element_id in self.problems:
            key = (kind, reason)
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1
        return [(KIND_LABELS.get(kind, kind), reason, counts[(kind, reason)])
                for kind, reason in order]
