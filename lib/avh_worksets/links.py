# -*- coding: utf-8 -*-
"""What the Close Link Datums tool decides, on plain values.

No Revit here. Which workset in a link counts as the datum workset,
whether a link can be offered at all, which worksets stay open after the
reload, and how the picker is labelled, are all decided on plain data so
they can be tested outside Revit.

A link's worksets reach this module as (key, name, is_open) tuples. The
key is anything that compares with ==; the script uses the workset id's
integer value.
"""

from avh_schedules.compat import to_text
from avh_worksets.model import CONVENTIONAL

# Names the datum workset goes by. AVH's own comes first; the others are
# what Revit's older templates and other offices call it. Matching is on
# a normalised form, so case and stray spaces do not matter.
DATUM_NAMES = (
    CONVENTIONAL,
    u"Shared Levels and Grids",
    u"Shared Levels & Grids",
    u"Shared Views Levels Grids",
)

# What can be done with each link. Only CAN_CLOSE is offered.
CAN_CLOSE = u"can close"
ALREADY_CLOSED = u"already closed"
NO_SUCH_WORKSET = u"has no Shared Views, Levels, Grids workset"
NOT_WORKSHARED = u"is not workshared, so it has no worksets"
NOT_LOADED = u"is not loaded"
NESTED = u"is nested inside another link"
STATE_UNKNOWN = u"which worksets are open could not be read"

# Outcomes of the reload itself.
CLOSED = u"closed"
STILL_OPEN = u"Revit reloaded it but the workset is still open"
OTHERS_CHANGED = u"closed, but other worksets changed too"
RELOAD_FAILED = u"the reload failed"
NO_PATH = u"its file path could not be read"


def normalise(name):
    """Lower case, '&' as 'and', commas dropped, spaces collapsed."""
    text = to_text(name).lower().replace(u"&", u" and ").replace(u",", u" ")
    return u" ".join(text.split())


_DATUM_KEYS = [normalise(name) for name in DATUM_NAMES]


def is_datum_workset(name):
    return normalise(name) in _DATUM_KEYS


def find_datum_workset(worksets):
    """The (key, name, is_open) of the datum workset, or None.

    When a link somehow has more than one, the earlier name in
    DATUM_NAMES wins, so AVH's own is preferred.
    """
    best = None
    best_rank = None
    for entry in worksets:
        key = normalise(entry[1])
        if key not in _DATUM_KEYS:
            continue
        rank = _DATUM_KEYS.index(key)
        if best is None or rank < best_rank:
            best = entry
            best_rank = rank
    return best


class LinkInfo(object):
    """What the script read about one link type.

    worksets is a list of (key, name, is_open), or None when it could not
    be read at all.
    """

    def __init__(self, name, loaded=True, nested=False, workshared=True,
                 worksets=None, handle=None):
        self.name = to_text(name)
        self.loaded = loaded
        self.nested = nested
        self.workshared = workshared
        self.worksets = worksets
        self.handle = handle


def assess(link):
    """One of the states above, for one link."""
    if link.nested:
        return NESTED
    if not link.loaded:
        return NOT_LOADED
    if not link.workshared:
        return NOT_WORKSHARED
    if link.worksets is None:
        return STATE_UNKNOWN
    target = find_datum_workset(link.worksets)
    if target is None:
        return NO_SUCH_WORKSET
    if target[2] is None:
        return STATE_UNKNOWN
    if not target[2]:
        return ALREADY_CLOSED
    return CAN_CLOSE


def keep_open(worksets, target_key):
    """Keys of every workset to open on reload: what is open now, less one.

    The reload starts from everything closed and opens exactly these, so
    a workset somebody already closed in that link stays closed.
    """
    return [key for key, _name, is_open in worksets
            if is_open and key != target_key]


def verify(before, after, target_key):
    """Compare a link's worksets before and after the reload.

    Returns CLOSED, STILL_OPEN or OTHERS_CHANGED. 'after' is read back
    from Revit, so this is the independent check, not the tool agreeing
    with itself.
    """
    after_state = dict((key, is_open) for key, _name, is_open in after)
    if after_state.get(target_key, True):
        return STILL_OPEN
    for key, _name, is_open in before:
        if key == target_key:
            continue
        if key in after_state and bool(after_state[key]) != bool(is_open):
            return OTHERS_CHANGED
    return CLOSED


def picker_labels(names):
    """Labels for the picker, unique even when two links share a name.

    Returns (labels, mapping) where mapping is label to index into names.
    """
    labels = []
    mapping = {}
    seen = {}
    for index, name in enumerate(names):
        text = to_text(name)
        count = seen.get(text, 0) + 1
        seen[text] = count
        label = text if count == 1 else u"{0} ({1})".format(text, count)
        labels.append(label)
        mapping[label] = index
    return labels, mapping
