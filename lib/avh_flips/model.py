# -*- coding: utf-8 -*-
"""What gets written where, for the flip and mirror status tool.

No Revit here. The three states, the values they map to, the decision
about whether a write is needed at all, and the shape of the report are
all plain data so they can be tested outside Revit.

## Why an Area parameter

Björn's answer, and it is the whole reason this is not a Yes/No:

- an Area parameter can be set to vary across group instances, so two
  instances of the same group can hold different values. A door mirrored
  in one group instance and not in another is exactly the case that
  matters.
- it can be used in formulas, which Yes/No cannot in the places this
  gets used, so schedules and view filters can compute from it.

Revit stores areas internally in square feet, so writing 1.0 through
`Parameter.Set` shows as `1 SF`. That is the same value Engipedia's
add in writes, so a schedule built on their parameter keeps working.
"""

from avh_schedules.compat import to_text

# The parameters written, in report order. The first name matches
# Engipedia's, deliberately, so anything already built on it keeps
# working. The other two are what their add in never recorded: it reads
# only FamilyInstance.Mirrored and never touches the flip controls.
MIRRORED = u"ElementFlippedOrMirrored"
HAND = u"ElementHandFlipped"
FACING = u"ElementFacingFlipped"

# (parameter name, state key, label for the report)
PARAMETERS = (
    (MIRRORED, "mirrored", u"mirrored"),
    (HAND, "hand", u"hand flipped"),
    (FACING, "facing", u"facing flipped"),
)

# The six categories Engipedia covers, kept identical so the parameter
# binding does not have to change.
CATEGORIES = (
    (u"OST_Casework", u"Casework"),
    (u"OST_Doors", u"Doors"),
    (u"OST_ElectricalEquipment", u"Electrical Equipment"),
    (u"OST_GenericModel", u"Generic Models"),
    (u"OST_MechanicalEquipment", u"Mechanical Equipment"),
    (u"OST_Windows", u"Windows"),
)

# Areas are doubles. Comparing them with == is how a tool ends up
# rewriting every element on every run, marking the whole model as
# modified and making a sync out of nothing.
TOLERANCE = 1e-9

TRUE_VALUE = 1.0
FALSE_VALUE = 0.0


def desired_values(state):
    """The value each parameter should hold, from the three booleans.

    `state` is a dict with mirrored, hand and facing keys. A state that
    could not be read is False rather than absent: not flipped is the
    honest reading of a family that has no flip control.
    """
    values = {}
    for name, key, _ in PARAMETERS:
        values[name] = TRUE_VALUE if state.get(key) else FALSE_VALUE
    return values


def needs_write(current, target):
    """True when the parameter does not already hold the target value.

    `current` is None when the value could not be read, which counts as
    needing a write: better to write it than to assume.
    """
    if current is None:
        return True
    return abs(current - target) > TOLERANCE


class Tally(object):
    """Counts per category, for the report.

    Written and unchanged are counted separately on purpose. A run that
    writes nothing because everything was already correct is a good
    result, and one that writes every element every time is a
    worksharing problem, so the two must not look alike.
    """

    def __init__(self):
        self.categories = []
        self._rows = {}
        self.written = 0
        self.unchanged = 0
        self.skipped = 0
        self.failed = 0

    def _row(self, category):
        if category not in self._rows:
            self._rows[category] = {
                "elements": 0, "mirrored": 0, "hand": 0, "facing": 0}
            self.categories.append(category)
        return self._rows[category]

    def count_element(self, category, state):
        row = self._row(category)
        row["elements"] += 1
        for _, key, _label in PARAMETERS:
            if state.get(key):
                row[key] += 1

    def count_write(self, written):
        if written:
            self.written += 1
        else:
            self.unchanged += 1

    def count_skipped(self):
        self.skipped += 1

    def count_failed(self):
        self.failed += 1

    def rows(self):
        """(category, elements, mirrored, hand, facing) in report order."""
        return [(category,
                 self._rows[category]["elements"],
                 self._rows[category]["mirrored"],
                 self._rows[category]["hand"],
                 self._rows[category]["facing"])
                for category in self.categories]

    def any_flipped(self):
        return any(row[2] or row[3] or row[4] for row in self.rows())


class Problems(object):
    """Parameters that could not be written, gathered by reason.

    Report only, by Björn's decision: nothing here creates or binds a
    parameter. So the report has to be specific enough to act on, which
    means naming the category and the parameter, not just saying that
    something was wrong.
    """

    MISSING = u"not bound to this category"
    WRONG_TYPE = u"not an Area parameter"
    READ_ONLY = u"read only"
    NOT_VARYING = u"does not vary across group instances"
    WRITE_FAILED = u"the write was rejected"

    def __init__(self):
        self.entries = []
        self._seen = set()

    def add(self, category, parameter, reason, detail=u""):
        key = (to_text(category), to_text(parameter), to_text(reason))
        if key in self._seen:
            return
        self._seen.add(key)
        self.entries.append((to_text(category), to_text(parameter),
                             to_text(reason), to_text(detail)))

    def __len__(self):
        return len(self.entries)

    def lines(self):
        """One readable line per problem, category first."""
        lines = []
        for category, parameter, reason, detail in sorted(self.entries):
            line = u"{0}: `{1}` {2}".format(category, parameter, reason)
            if detail:
                line += u" ({0})".format(detail)
            lines.append(line)
        return lines
