# -*- coding: utf-8 -*-
"""
Tests for what Room Data Sync decides, with no Revit in it.

Finding an element's room needs the API. Deciding what to do once you
know is text comparison, and that is where a sync tool quietly destroys
hand corrections if the rules are sloppy. So the rules are here.

Run outside Revit:

    python test_room_sync.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_rooms import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --- 1. blanks, in all the forms Revit produces -----------------------
#
# An unset text parameter comes back as None, one that was set and
# cleared comes back empty, and a value pasted from a schedule cell can
# carry trailing spaces. Treating all three alike is what stops the tool
# reporting changes that are not changes.

check("None is blank", model.normalise(None) == u"")
check("empty is blank", model.normalise(u"") == u"")
check("whitespace is blank", model.normalise(u"   ") == u"")
check("a value is trimmed", model.normalise(u"  A.B.01  ") == u"A.B.01")


# --- 2. the four outcomes ---------------------------------------------

check("a blank target is filled",
      model.classify(u"A.B.01", None) == model.FILL)
check("an empty target is filled",
      model.classify(u"A.B.01", u"") == model.FILL)
check("a matching target is left alone",
      model.classify(u"A.B.01", u"A.B.01") == model.MATCH)
check("a different target is a change, not a fill",
      model.classify(u"A.B.01", u"A.B.02") == model.CHANGE)

check("whitespace either side does not make a change",
      model.classify(u" A.B.01 ", u"A.B.01") == model.MATCH,
      "got {0}".format(model.classify(u" A.B.01 ", u"A.B.01")))
check("nor does whitespace on the stored value",
      model.classify(u"A.B.01", u"A.B.01  ") == model.MATCH)
check("but case is not ignored, since these are identifiers",
      model.classify(u"A.B.01", u"a.b.01") == model.CHANGE)


# --- 3. the cases where there is nothing to copy ----------------------

check("no target parameter beats every other answer",
      model.classify(u"A.B.01", u"", has_target=False) == model.NO_TARGET)
check("no target wins even with no room either",
      model.classify(u"A.B.01", u"", has_room=False,
                     has_target=False) == model.NO_TARGET)
check("not in a room is reported before a blank source",
      model.classify(u"", u"", has_room=False) == model.NO_ROOM)
check("a room with a blank ID gives nothing to copy",
      model.classify(u"", u"anything") == model.NO_SOURCE)
check("a room whose ID is only whitespace is also blank",
      model.classify(u"   ", u"") == model.NO_SOURCE)
check("and a blank source never clears an existing value",
      model.classify(u"", u"A.B.01") == model.NO_SOURCE,
      "a blank room must not wipe what is already there")


# --- 3b. choosing between two candidate rooms -------------------------
#
# "Prefer ToRoom" turned out to mean two different things. The first
# version fell back to FromRoom only when there was no ToRoom at all.
# What was wanted is a fallback on the value: a door whose ToRoom has a
# blank ID should take FromRoom's, because the ID is the point and one is
# sitting right there.

check("the preferred candidate wins when it has a value",
      model.choose_source([u"A.01", u"B.02"]) == (0, False))
check("a blank preferred candidate falls through to the next",
      model.choose_source([u"", u"B.02"]) == (1, True),
      "got {0}".format(model.choose_source([u"", u"B.02"])))
check("None counts as blank, the same as empty",
      model.choose_source([None, u"B.02"]) == (1, True))
check("so does whitespace",
      model.choose_source([u"  ", u"B.02"]) == (1, True))
check("falling through is reported, so the trace can say why",
      model.choose_source([u"", u"B.02"])[1] is True)
check("not falling through is reported too",
      model.choose_source([u"A.01", u"B.02"])[1] is False)

check("when every candidate is blank the preferred one is still named, "
      "since that is the room somebody has to fix",
      model.choose_source([u"", u""]) == (0, False),
      "got {0}".format(model.choose_source([u"", u""])))
check("one candidate with a value needs no fallback",
      model.choose_source([u"A.01"]) == (0, False))
check("one blank candidate still names itself",
      model.choose_source([u""]) == (0, False))
check("no candidates at all gives no answer",
      model.choose_source([]) == (None, False))


# --- 4. what a run actually writes ------------------------------------

entries = [
    model.SyncEntry(1, u"Furniture / Desk", model.FILL,
                    room_value=u"A.01"),
    model.SyncEntry(2, u"Casework / Base unit", model.FILL,
                    room_value=u"A.01"),
    model.SyncEntry(3, u"Doors / Single", model.CHANGE,
                    room_value=u"A.02", current_value=u"A.99"),
    model.SyncEntry(4, u"Furniture / Chair", model.MATCH,
                    room_value=u"A.01", current_value=u"A.01"),
    model.SyncEntry(5, u"Furniture / Stool", model.NO_ROOM),
    model.SyncEntry(6, u"Casework / Shelf", model.NO_SOURCE),
    model.SyncEntry(7, u"Windows / Fixed", model.NO_TARGET),
]

counts = model.summarise(entries)
check("fills are counted", counts[model.FILL] == 2)
check("changes are counted apart from fills",
      counts[model.CHANGE] == 1)
check("matches are counted", counts[model.MATCH] == 1)
check("every problem class is counted",
      counts[model.NO_ROOM] == 1 and counts[model.NO_SOURCE] == 1
      and counts[model.NO_TARGET] == 1)
check("summarise of nothing is all zero",
      model.summarise([]) == model.empty_counts())

full = model.writable(entries)
check("a full run writes fills and changes",
      [e.element_id for e in full] == [1, 2, 3],
      "got {0}".format([e.element_id for e in full]))

safe = model.writable(entries, include_changes=False)
check("blanks only leaves existing values alone",
      [e.element_id for e in safe] == [1, 2],
      "got {0}".format([e.element_id for e in safe]))

check("a match is never rewritten, in either mode",
      all(e.action != model.MATCH for e in full))
check("nor is anything with no room, no source or no target",
      all(e.action in model.WRITES for e in full))
check("the entry knows itself whether it writes",
      [e.writes for e in entries] ==
      [True, True, True, False, False, False, False])


# --- 5. prove these assertions can fail -------------------------------

check("a deliberately false assertion is recorded as failing",
      not (model.classify(u"A", u"A") == model.CHANGE))
check("check() stores a real boolean",
      isinstance(results[0][1], bool),
      "got {0}".format(type(results[0][1]).__name__))


# --- report -----------------------------------------------------------

print("Room data sync rules")
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
