# -*- coding: utf-8 -*-
"""
What Room Data Sync decides, with no Revit in it.

Finding which room an element sits in needs the Revit API. Deciding what
to do once you know is pure text comparison, and that is where the rules
live, so it lives here and `test_room_sync.py` proves it outside Revit.
"""

# What to do with one element.
FILL = u"fill"         # target is blank, room has a value
CHANGE = u"change"     # target holds something else, room disagrees
MATCH = u"match"       # already correct, nothing to do
NO_ROOM = u"no room"   # not inside any room, so there is no value to copy
NO_SOURCE = u"no source"   # in a room, but the room's own ID is blank
NO_TARGET = u"no target"   # no writable parameter to copy into

# CHANGE is deliberately separate from FILL. Filling a blank is
# uncontroversial. Overwriting a value someone may have typed by hand is
# not, and lumping the two together hides the second inside the first.
# Every change is listed with its old and new value so the disagreement
# is visible before it is resolved.

WRITES = (FILL, CHANGE)


def normalise(value):
    """Text, trimmed, never None. Comparison happens on this form only.

    Revit hands back None for an unset text parameter and an empty string
    for one that was set and cleared, and trailing spaces survive a copy
    and paste into a schedule cell. Treating all three as the same blank
    is what stops the tool reporting changes that are not changes.
    """
    if value is None:
        return u""
    return value.strip()


def classify(room_value, current_value, has_room=True, has_target=True):
    """What should happen to one element. Order matters.

    The checks run cheapest first and, more importantly, in the order
    that produces the most useful answer: an element with no writable
    parameter is a modelling problem worth reporting whether or not it is
    in a room, while "not in a room" is worth knowing before anyone
    wonders why its ID is blank.
    """
    if not has_target:
        return NO_TARGET
    if not has_room:
        return NO_ROOM

    source = normalise(room_value)
    if not source:
        return NO_SOURCE

    current = normalise(current_value)
    if current == source:
        return MATCH
    if not current:
        return FILL
    return CHANGE


def choose_source(values):
    """Pick from candidate values in preference order.

    Returns (index, fell_through). `values` is the raw source value from
    each candidate room, most preferred first.

    This exists because "prefer ToRoom" turned out to mean two different
    things. The first version fell back to FromRoom only when there was
    no ToRoom **room** at all. What was wanted is a fallback on the
    **value**: a door whose ToRoom is a room with a blank ID should take
    FromRoom's ID rather than come away with nothing, because the ID is
    what the sync is for and one is sitting right there.

    When every candidate is blank the first is still returned, so the
    report names the preferred room when it says there is nothing to
    copy. That is the room somebody needs to go and fix.
    """
    if not values:
        return None, False
    for index, value in enumerate(values):
        if normalise(value):
            return index, index != 0
    return 0, False


class SyncEntry(object):
    """One element, the room it was found in, and what will happen."""

    def __init__(self, element_id, description, action, room_label=u"",
                 room_value=u"", current_value=u"", reason=u"",
                 found_by=u""):
        self.element_id = element_id
        self.description = description
        self.action = action
        self.room_label = room_label
        self.room_value = room_value
        self.current_value = current_value
        self.reason = reason
        # How the room was determined: a door's ToRoom and a chair's
        # location point are different kinds of evidence, and when a
        # value looks wrong the first question is always which one was
        # used.
        self.found_by = found_by

    @property
    def writes(self):
        return self.action in WRITES

    def __repr__(self):
        return "<SyncEntry {0} {1}>".format(self.element_id, self.action)


def empty_counts():
    return {FILL: 0, CHANGE: 0, MATCH: 0, NO_ROOM: 0, NO_SOURCE: 0,
            NO_TARGET: 0}


def summarise(entries):
    counts = empty_counts()
    for entry in entries:
        if entry.action in counts:
            counts[entry.action] += 1
    return counts


def writable(entries, include_changes=True):
    """The entries a run should actually write.

    Excluding changes leaves only the blanks being filled, which is the
    escape hatch when a dry run's change list looks wrong and you want
    the safe half of the work anyway.
    """
    if include_changes:
        return [e for e in entries if e.action in WRITES]
    return [e for e in entries if e.action == FILL]
