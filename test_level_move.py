# -*- coding: utf-8 -*-
"""
Tests for the level move arithmetic behind AVH > Tools > Remove Level.

This suite exists for one reason. The pyApex script this tool was adapted
from computed the element's absolute elevation and wrote it straight into
the offset parameter, without subtracting the target level's elevation.
That is correct only when the target level sits at 0.00, and its
transaction name, 'Change level to 0', shows the author knew which case
he had written it for. Every other target moved every element upward by
exactly the target level's elevation.

It is pure arithmetic with no Revit in it, which is precisely why it
belongs in a test rather than in a pushbutton script. Run outside Revit:

    python test_level_move.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_levels import model  # noqa: E402

results = []

# Revit internal units are feet. Working in millimetres here would hide
# the fact that nothing in the module formats or parses a number.
FOOT = 1.0
MM = 1.0 / model.MM_PER_FOOT


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-9):
    return abs(a - b) < tolerance


# --- 1. the offset that keeps an element where it is ------------------

check("target at zero: offset becomes the absolute elevation",
      close(model.new_offset(10.0, 2.0, 0.0), 12.0),
      "got {0}".format(model.new_offset(10.0, 2.0, 0.0)))

check("target above the source: offset goes negative",
      close(model.new_offset(10.0, 2.0, 20.0), -8.0),
      "got {0}".format(model.new_offset(10.0, 2.0, 20.0)))

check("target below the source: offset grows",
      close(model.new_offset(10.0, 2.0, 5.0), 7.0),
      "got {0}".format(model.new_offset(10.0, 2.0, 5.0)))

check("same level in and out: offset is unchanged",
      close(model.new_offset(10.0, 2.0, 10.0), 2.0),
      "got {0}".format(model.new_offset(10.0, 2.0, 10.0)))

check("negative source elevation, below ground",
      close(model.new_offset(-4.0, 1.0, 3.0), -6.0),
      "got {0}".format(model.new_offset(-4.0, 1.0, 3.0)))


# --- 2. absolute elevation is preserved, which is the whole point -----

def absolute(level_elevation, offset):
    return level_elevation + offset


CASES = [
    # source elevation, current offset, target elevation
    (0.0, 0.0, 0.0),
    (10.0, 2.0, 20.0),
    (10.0, -2.0, 3.5),
    (-4.0, 1.25, 17.0),
    (32.8084, 0.5, 6.5617),
]

for source, offset, target in CASES:
    result = model.new_offset(source, offset, target)
    before = absolute(source, offset)
    after = absolute(target, result)
    check("absolute elevation preserved for "
          "source {0}, offset {1}, target {2}".format(source, offset,
                                                      target),
          close(before, after),
          "before {0}, after {1}".format(before, after))


# --- 3. the original bug, encoded so it cannot come back --------------
#
# These assert that the fixed function does NOT agree with the broken
# one wherever the target level is not at zero. Without this the
# subtraction could be dropped again and every other check above that
# uses a non zero target would be the only thing standing in the way.

def pyapex_original(source_elevation, current_offset, _target_elevation):
    """What the original wrote: the absolute elevation, nothing else."""
    return source_elevation + current_offset


for source, offset, target in CASES:
    fixed = model.new_offset(source, offset, target)
    broken = pyapex_original(source, offset, target)
    if close(target, 0.0):
        check("original agrees with the fix only at target 0.00",
              close(fixed, broken),
              "fixed {0}, original {1}".format(fixed, broken))
    else:
        check("original differs from the fix at target {0}".format(target),
              not close(fixed, broken),
              "fixed {0}, original {1}".format(fixed, broken))
        check("original is wrong by exactly the target elevation "
              "at target {0}".format(target),
              close(broken - fixed, target),
              "difference {0}, target elevation {1}".format(
                  broken - fixed, target))


# --- 4. millimetres, for the report only ------------------------------

check("one foot is 304.8 mm", close(model.feet_to_mm(1.0), 304.8),
      "got {0}".format(model.feet_to_mm(1.0)))
check("zero stays zero", close(model.feet_to_mm(0.0), 0.0))
check("negative offsets survive the conversion",
      close(model.feet_to_mm(-2.0), -609.6),
      "got {0}".format(model.feet_to_mm(-2.0)))
check("a 3000 mm level round trips",
      close(model.feet_to_mm(3000.0 * MM), 3000.0),
      "got {0}".format(model.feet_to_mm(3000.0 * MM)))


# --- 5. classification bookkeeping ------------------------------------

entries = [
    model.PlanEntry(1, model.MOVE, u"Walls / Basic Wall"),
    model.PlanEntry(2, model.MOVE, u"Doors / M_Single-Flush"),
    model.PlanEntry(3, model.SHIFT, u"Railings / 1100mm"),
    model.PlanEntry(4, model.SKIP, u"Reference Planes"),
    model.PlanEntry(5, model.SKIP, u"Model Lines"),
    model.PlanEntry(6, model.COLLATERAL, u"Views / Plan"),
    model.PlanEntry(7, model.COLLATERAL, u"Viewports / Title w Line"),
    model.PlanEntry(8, model.COLLATERAL, u"Sun Path"),
]

counts = model.summarise(entries)
check("summarise counts moves", counts[model.MOVE] == 2,
      "got {0}".format(counts[model.MOVE]))
check("summarise counts shifts", counts[model.SHIFT] == 1,
      "got {0}".format(counts[model.SHIFT]))
check("summarise counts skips", counts[model.SKIP] == 2,
      "got {0}".format(counts[model.SKIP]))
check("summarise counts collateral separately from skips",
      counts[model.COLLATERAL] == 3,
      "got {0}".format(counts[model.COLLATERAL]))
check("summarise of nothing is all zero",
      model.summarise([]) == model.empty_counts(),
      "got {0}".format(model.summarise([])))
check("empty_counts knows about all six classes",
      sorted(model.empty_counts().keys()) ==
      sorted([model.MOVE, model.SHIFT, model.SKIP, model.REHOST,
              model.RECREATE, model.COLLATERAL]))

default_run = model.actionable(entries)
check("a default run writes only the clean moves",
      [e.element_id for e in default_run] == [1, 2],
      "got {0}".format([e.element_id for e in default_run]))

opted_in = model.actionable(entries, include_shift=True)
check("opting in adds the shifting elements",
      [e.element_id for e in opted_in] == [1, 2, 3],
      "got {0}".format([e.element_id for e in opted_in]))

check("a skipped element is never written, either way",
      all(e.action != model.SKIP for e in opted_in))
check("collateral is never written either, since it was never on a "
      "level to begin with",
      all(e.action != model.COLLATERAL for e in opted_in))

rehost_entries = entries + [
    model.PlanEntry(30, model.REHOST, u"Lines / Model Lines")]
check("rehosting is never part of a move, it is its own step",
      all(e.action != model.REHOST
          for e in model.actionable(rehost_entries, include_shift=True)))
check("and rehostable() picks out exactly those",
      [e.element_id for e in model.rehostable(rehost_entries)] == [30],
      "got {0}".format(
          [e.element_id for e in model.rehostable(rehost_entries)]))
check("rehostable() of a plan with none is empty",
      model.rehostable(entries) == [])

room_entries = entries + [
    model.PlanEntry(31, model.RECREATE, u"Rooms / Skrifstofa")]
check("a room is never part of a move either",
      all(e.action != model.RECREATE
          for e in model.actionable(room_entries, include_shift=True)))
check("nor of a rehost, since it is a third kind of operation",
      all(e.action != model.RECREATE
          for e in model.rehostable(room_entries)))
check("recreatable() picks out exactly the rooms",
      [e.element_id for e in model.recreatable(room_entries)] == [31],
      "got {0}".format(
          [e.element_id for e in model.recreatable(room_entries)]))

check("moves_geometry is true only for a shift",
      [e.moves_geometry for e in entries] ==
      [False, False, True, False, False, False, False, False])

check("writes default to an empty list, never None",
      model.PlanEntry(9, model.SKIP, u"x").writes == [])
check("diagnostics default to empty text",
      model.PlanEntry(9, model.SKIP, u"x").diagnostics == u"")


# --- 6. one constraint, one offset -------------------------------------
#
# An element can be constrained to the same level twice, base and top.
# The first version stored a single offset per element and repointed every
# constraint it found, which on a real model moved a room's upper limit
# with nothing to correct it and left the limit below the room's base.

base = model.LevelWrite(11, offset_param_id=12, current_offset=1.0,
                        target_offset=-2.0,
                        label=u"STAIRS_BASE_LEVEL_PARAM")
top = model.LevelWrite(13, offset_param_id=14, current_offset=5.0,
                       target_offset=2.0,
                       label=u"STAIRS_TOP_LEVEL_PARAM")
unpaired = model.LevelWrite(15, label=u"ROOM_UPPER_LEVEL")

check("a paired write knows it corrects an offset", base.corrects_offset)
check("an unpaired write knows it does not", not unpaired.corrects_offset)

stair = model.PlanEntry(20, model.MOVE, u"Stairs / Stair",
                        writes=[base, top])
check("both constraints are carried, not just the first",
      len(stair.writes) == 2)
check("each carries its own offset, so neither is left behind",
      [w.offset_param_id for w in stair.writes] == [12, 14])
check("and the report can name them",
      stair.constraint_labels ==
      [u"STAIRS_BASE_LEVEL_PARAM", u"STAIRS_TOP_LEVEL_PARAM"])

# Both constraints shifted by the same level difference, so the element's
# own height is unchanged. This is what correcting only one would break.
height_before = 5.0 - 1.0
height_after = 2.0 - (-2.0)
check("correcting every offset leaves the element's height alone",
      close(height_before, height_after),
      "before {0}, after {1}".format(height_before, height_after))

only_base = 5.0 - (-2.0)
check("correcting only the base offset would have changed it",
      not close(height_before, only_base),
      "would become {0}".format(only_base))


# --- 6. prove these assertions can actually fail ----------------------
#
# Two test bugs were found in this repository by asking exactly this,
# one suite passing strings where booleans were expected and a regex that
# could never miss. A check that cannot fail is worse than no check.

check("a deliberately false assertion is recorded as failing",
      not bool(close(1.0, 2.0)),
      "close(1.0, 2.0) must be False")
check("check() records a real boolean, not a truthy string",
      isinstance(results[0][1], bool),
      "got {0}".format(type(results[0][1]).__name__))


# --- 7. room height after a move, which Revit only checks on commit ---
#
# The Eldisgarður failure. Revit validates room height when the
# transaction commits, and this tool checks feasibility by rolling one
# back, so the probe is blind to it. Arithmetic is not.

check("an upper limit anchored to the level being cleared comes across, "
      "so the height is preserved exactly",
      close(model.room_height_after(10.0, 3.0, 0.0, 10.0, 8.0, True),
            8.0),
      "got {0}".format(
          model.room_height_after(10.0, 3.0, 0.0, 10.0, 8.0, True)))

check("and that holds however far the room moves",
      close(model.room_height_after(10.0, -50.0, 1.0, 10.0, 9.0, True),
            8.0),
      "got {0}".format(
          model.room_height_after(10.0, -50.0, 1.0, 10.0, 9.0, True)))

check("an upper limit anchored elsewhere stays put while the floor moves",
      close(model.room_height_after(0.0, 3.0, 0.0, 6.0, 0.0, False), 3.0),
      "got {0}".format(
          model.room_height_after(0.0, 3.0, 0.0, 6.0, 0.0, False)))

check("so moving the floor above that anchor gives a negative height",
      model.room_height_after(0.0, 16.4, 0.0, 6.56, 0.0, False) < 0,
      "got {0}".format(
          model.room_height_after(0.0, 16.4, 0.0, 6.56, 0.0, False)))

check("which is exactly the height Revit refuses, not merely a small one",
      model.room_height_after(0.0, 6.56, 0.0, 6.56, 0.0, False) == 0.0,
      "got {0}".format(
          model.room_height_after(0.0, 6.56, 0.0, 6.56, 0.0, False)))

check("a base offset raises the floor and eats into the height",
      close(model.room_height_after(0.0, 0.0, 1.0, 6.0, 0.0, False), 5.0),
      "got {0}".format(
          model.room_height_after(0.0, 0.0, 1.0, 6.0, 0.0, False)))

# The case that actually shipped: floor moves down, anchor stays above.
# It survives, which is why the guard has to compute rather than assume
# that moving down is always safe and moving up always dangerous.
check("moving a room down, under an anchor that stays, is fine",
      model.room_height_after(38.5, 26.2, 0.0, 38.5, 8.2, False) > 0,
      "got {0}".format(
          model.room_height_after(38.5, 26.2, 0.0, 38.5, 8.2, False)))


# --- report -----------------------------------------------------------

print("Level move arithmetic")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if ok:
        print("  [  ok] {0}".format(name))
    else:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
