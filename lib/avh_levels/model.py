# -*- coding: utf-8 -*-
"""
Level move arithmetic and classification. No Revit, no .NET.

This module exists because of one specific defect in the pyApex script
this tool was adapted from. Keeping the arithmetic here, away from the
Revit API, is what lets `test_level_move.py` prove it outside Revit.

Everything here works in Revit internal units (feet, as doubles). Nothing
in this module parses or formats a number for Revit to read back, which
is deliberate: see `new_offset`.
"""

# What can be done with one element found on a level being cleared.
MOVE = u"move"    # every constraint has a paired offset, element stays put
SHIFT = u"shift"  # a constraint with no paired offset, element would move
SKIP = u"skip"    # a real element that cannot be freed from the level
REHOST = u"rehost"  # freed by moving its work plane, not a level parameter
RECREATE = u"recreate"  # a room, which has to be taken out and put back
COLLATERAL = u"collateral"  # not a candidate at all, see below

# RECREATE is rooms, and only rooms. A room's level parameter is read
# only in the API and in the interface alike, so no parameter can move
# one. The route a person takes is to cut it and paste it onto another
# level, and the tool takes the same route by a better road: unplace the
# room, place it again in an empty plan circuit on the target level, and
# put it back at its own coordinates. That preserves the room number,
# which a copy and paste does not, and on Eldisgarður the room number is
# schedule data.
#
# Unlike every other class here a room does **not** keep its absolute
# elevation. A room sits at its level, so moving it to a lower one really
# does move it down. That is an architectural change rather than a
# repositioning, which is why it is confirmed on its own.

# REHOST covers elements that sit on a sketch plane which is itself
# derived from the level, model lines being the case that turned up.
# Nothing on them points at the level, so no parameter can move them, but
# giving them an equivalent plane that is not tied to a level frees them
# without moving them a millimetre. It is a different operation from a
# move, with a different failure mode, so it gets its own class and its
# own confirmation rather than being folded in.

# COLLATERAL is views, viewports, anything living inside a view, and
# internal sub objects with no category. They cannot be moved to another
# level because they were never on one: they exist because the level
# does, and Revit removes them with it.
#
# The first run against a real model returned 116 dependents for a level
# whose entire model content was one railing. Everything else was a floor
# plan, a viewport, a sun path, a work plane grid, extent elements and
# unnamed sub objects. Reporting those as failures buried the one line
# that mattered, and counting them as blockers meant the level could
# never be deleted.

MM_PER_FOOT = 304.8


def feet_to_mm(value):
    """Revit internal units to millimetres, for display only.

    Never feed the result back to Revit. Set parameters with the raw
    internal value through `Parameter.Set`, not through a formatted
    string.
    """
    return value * MM_PER_FOOT


def new_offset(source_elevation, current_offset, target_elevation):
    """Offset from the target level that leaves the element where it is.

    All three arguments are in internal units. The element's absolute
    elevation is `source_elevation + current_offset`. Expressed against
    the target level instead, that same absolute elevation is:

        (source_elevation + current_offset) - target_elevation

    Two things the original pyApex script got wrong here, both of which
    this signature makes impossible to repeat.

    **The subtraction was missing.** It computed the absolute elevation
    and wrote it straight back into the offset parameter, so the element
    ended up at `target_elevation` above where it should be. That is
    correct only when the target level sits at 0.00, which is exactly
    what its transaction name, 'Change level to 0', was telling us. Any
    other target moved every element by the target level's elevation.

    **It went through strings.** It read elevations with
    `AsValueString()`, stripped spaces, and called `float()` on the
    result, then wrote back with `SetValueString(str(value))`. AVH's
    Revit formats with a decimal comma, so the read raises ValueError and
    the write pushes a period decimal into a comma locale, where it is
    either rejected or misread as a thousands separator. `SetValueString`
    returns a bool the original discarded, so a rejected write was
    silent. Doubles in internal units have no locale at all.
    """
    return (source_elevation + current_offset) - target_elevation


def room_height_after(source_elevation, target_elevation, base_offset,
                      upper_elevation, upper_offset, upper_on_source):
    """The height a room would have once it is on the target level.

    A room must have a height greater than zero or Revit refuses it, and
    it refuses **during the commit**, not when the calls are made. That
    matters here: this tool checks feasibility by doing the work in a
    transaction it rolls back, and a rollback never validates. So a room
    move can pass the probe and still be thrown away on commit, which is
    exactly what happened on Eldisgarður: the run reported a room moved
    and then, three lines later, listed the same room as still on the
    level being deleted.

    The answer is not to probe harder but to do the arithmetic, which
    needs no Revit at all:

    - If the upper limit is anchored to the level being cleared, it comes
      across with the room and the height is preserved by construction.
    - If it is anchored to some other level, that anchor stays put while
      the room's floor moves. Move the floor above the anchor and the
      height goes negative.

    The second case is the trap, and it is silent: nothing about the room
    looks wrong until Revit rejects the whole transaction. Note that it
    is not simply "moving up is dangerous": a room moved down under an
    anchor that stays put is fine, so this has to be computed rather than
    assumed.
    """
    if upper_on_source:
        return ((upper_elevation + upper_offset)
                - (source_elevation + base_offset))
    return ((upper_elevation + upper_offset)
            - (target_elevation + base_offset))


class LevelWrite(object):
    """One level constraint to repoint, and the offset that holds it.

    An element can be constrained to the same level more than once, a
    wall or a stair with both its base and its top on it being the
    obvious case. Each constraint needs its own offset corrected.

    The first version stored a single offset for the whole element and
    repointed every level parameter it found. Against a real model that
    moved a room's upper limit while correcting nothing, leaving the
    limit below the room's own base. Pairing them one to one is what
    makes that impossible rather than merely unlikely.
    """

    def __init__(self, level_param_id, offset_param_id=None,
                 current_offset=None, target_offset=None, label=u""):
        self.level_param_id = level_param_id
        self.offset_param_id = offset_param_id
        self.current_offset = current_offset
        self.target_offset = target_offset
        # The BuiltInParameter name of the level constraint, so the
        # report can say which one it is rather than just that there was
        # one. ROOM_UPPER_LEVEL and WALL_BASE_CONSTRAINT deserve very
        # different amounts of alarm.
        self.label = label

    @property
    def corrects_offset(self):
        return self.offset_param_id is not None

    def __repr__(self):
        return "<LevelWrite {0}>".format(self.label or self.level_param_id)


class PlanEntry(object):
    """One element, and what the run intends to do with it.

    Built during the dry run and reused unchanged for the real run, so
    what gets reported and what gets written cannot drift apart.
    """

    def __init__(self, element_id, action, description, writes=None,
                 reason=u"", target_level_id=None, diagnostics=u""):
        self.element_id = element_id
        self.action = action
        self.description = description
        # Parameter *ids*, inside LevelWrite, not Parameter objects. The
        # dry run happens before a rollback and before two dialogs, and a
        # live Revit Parameter held across all of that is a stale
        # reference waiting to happen. The write step re-fetches by id.
        #
        # Ids rather than BuiltInParameter names because the scan that
        # finds these is generic: it looks for any writable ElementId
        # parameter pointing at the level, which can be a shared or
        # project parameter with no built in name at all.
        self.writes = list(writes or [])
        self.reason = reason
        # What the scan saw, for entries it could not act on. This is the
        # feedback loop: a dry run against a real model reports which
        # parameters an unmovable element actually has, so the next
        # version is designed from data rather than from guesswork.
        self.diagnostics = diagnostics
        # Carried on the entry so the write step never looks the target
        # up again and cannot pick up a different one than the dry run
        # reported.
        self.target_level_id = target_level_id

    @property
    def moves_geometry(self):
        return self.action == SHIFT

    @property
    def constraint_labels(self):
        return [write.label for write in self.writes if write.label]

    def __repr__(self):
        return "<PlanEntry {0} {1}>".format(self.element_id, self.action)


def empty_counts():
    return {MOVE: 0, SHIFT: 0, SKIP: 0, REHOST: 0, RECREATE: 0,
            COLLATERAL: 0}


def summarise(entries):
    """Count entries by action."""
    counts = empty_counts()
    for entry in entries:
        if entry.action in counts:
            counts[entry.action] += 1
    return counts


def actionable(entries, include_shift=False):
    """The entries a run should actually write, in a stable order.

    SHIFT entries are excluded unless the user has explicitly accepted
    them, because repointing a constraint with no offset to correct
    changes where the element physically sits.
    """
    wanted = [MOVE]
    if include_shift:
        wanted.append(SHIFT)
    return [e for e in entries if e.action in wanted]


def rehostable(entries):
    """Entries freed by replacing their work plane rather than by a move.

    Kept out of `actionable` on purpose. Repointing a level constraint
    and rebuilding a sketch plane are different operations that fail in
    different ways, so they are confirmed and written separately.
    """
    return [e for e in entries if e.action == REHOST]


def recreatable(entries):
    """Rooms that can be taken off the level and put back on another.

    Kept out of both `actionable` and `rehostable`: it is the only
    operation here that deliberately changes where something sits.
    """
    return [e for e in entries if e.action == RECREATE]
