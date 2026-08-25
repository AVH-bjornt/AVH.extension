# -*- coding: utf-8 -*-
"""What a door's two sides mean, and where the arrow goes.

No Revit here. The classification and the arrow geometry are plain
arithmetic on plain values, so both can be tested outside Revit, which
matters more than usual: an arrow pointing at the wrong room is a
drawing that lies, and it lies quietly.

The states mirror what Room Data Sync actually does, and the preference
rule is shared with it through `avh_rooms.model.choose_source` rather
than restated here. Two tools disagreeing about which room a door
belongs to would be worse than having neither.
"""

import math

from avh_rooms.model import choose_source
from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8

# Drawn sizes, in millimetres on paper. Multiplied by the view scale at
# draw time so the marks stay the same size on the sheet whether the plan
# is 1:50 or 1:200. Getting this wrong is what makes an annotation tool
# useless on the second project.
ARROW_LENGTH_MM = 6.0
ARROW_HEAD_MM = 2.0
ARROW_GAP_MM = 1.5
TEXT_GAP_MM = 1.5

# State keys, in report order. The colour is what the arrow is drawn in.
OK = "ok"
FELL_THROUGH = "fell_through"
NO_TO_ROOM = "no_to_room"
SAME_ROOM = "same_room"
NO_ROOMS = "no_rooms"
NO_VALUE = "no_value"

GREEN = (0, 140, 70)
GREY = (130, 130, 130)
RED = (200, 40, 40)
AMBER = (200, 130, 20)

STATES = (
    (OK, GREEN, u"ToRoom has an ID"),
    (FELL_THROUGH, AMBER, u"ToRoom blank, took FromRoom's ID"),
    (NO_TO_ROOM, AMBER, u"no ToRoom, only FromRoom"),
    (SAME_ROOM, RED, u"both sides are the same room"),
    (NO_ROOMS, RED, u"no room on either side"),
    (NO_VALUE, RED, u"rooms on both sides, neither has an ID"),
)

STATE_COLOURS = dict((key, colour) for key, colour, _label in STATES)
STATE_LABELS = dict((key, label) for key, _colour, label in STATES)


def classify(to_present, from_present, to_value, from_value, same_room):
    """Which state a door is in.

    `to_present` and `from_present` say whether a room was found on that
    side at all. The values are that room's CCI ID, blank or otherwise.
    `same_room` means both sides resolved to the same room, which is
    usually a room boundary somebody forgot to draw.
    """
    if not to_present and not from_present:
        return NO_ROOMS
    if same_room:
        return SAME_ROOM
    if not to_present:
        return NO_TO_ROOM

    # The preference rule itself comes from the sync, not from a second
    # copy of it here. Two tools disagreeing about which room a door
    # belongs to would be worse than having neither.
    values = [to_value]
    if from_present:
        values.append(from_value)
    index, fell_through = choose_source(values)

    if fell_through:
        return FELL_THROUGH
    if normalise(values[index]):
        return OK
    return NO_VALUE


def normalise(value):
    return to_text(value).strip()


def is_problem(state):
    """Anything that is not a clean ToRoom hit wants looking at."""
    return state != OK


def paper_mm(millimetres, view_scale):
    """Millimetres on paper as a model length in feet.

    A view at 1:100 has a scale of 100, so 6 mm on paper is 600 mm in the
    model. Drawing a fixed model length instead is the mistake that makes
    the marks invisible at 1:200 and enormous at 1:20.
    """
    try:
        scale = float(view_scale)
    except (TypeError, ValueError):
        scale = 100.0
    if scale <= 0:
        scale = 100.0
    return millimetres * scale / MM_PER_FOOT


def unit(vector):
    """A 2D unit vector from (x, y). Returns None when there is no length."""
    x, y = vector
    length = math.sqrt(x * x + y * y)
    if length < 1e-9:
        return None
    return (x / length, y / length)


def arrow_points(origin, facing, view_scale):
    """The arrow, as plain 2D points.

    Returns a dict with `shaft`, `head_left`, `head_right`, `to_text` and
    `from_text`, or None when the facing vector has no direction to
    speak of. The shaft starts clear of the door leaf and points the way
    the door faces, which is the side `ToRoom` is on.
    """
    direction = unit(facing)
    if direction is None:
        return None

    dx, dy = direction
    ox, oy = origin

    gap = paper_mm(ARROW_GAP_MM, view_scale)
    length = paper_mm(ARROW_LENGTH_MM, view_scale)
    head = paper_mm(ARROW_HEAD_MM, view_scale)
    text_gap = paper_mm(TEXT_GAP_MM, view_scale)

    start = (ox + dx * gap, oy + dy * gap)
    end = (ox + dx * (gap + length), oy + dy * (gap + length))

    # The head is two short lines swept back from the tip at 30 degrees.
    back = math.cos(math.radians(30.0)) * head
    across = math.sin(math.radians(30.0)) * head
    ex, ey = end
    head_left = ((ex - dx * back - dy * across), (ey - dy * back + dx * across))
    head_right = ((ex - dx * back + dy * across),
                  (ey - dy * back - dx * across))

    return {
        "shaft": (start, end),
        "head_left": (end, head_left),
        "head_right": (end, head_right),
        "to_text": (ex + dx * text_gap, ey + dy * text_gap),
        "from_text": (ox - dx * (gap + text_gap),
                      oy - dy * (gap + text_gap)),
    }


def label_for(state, to_label, from_label):
    """The two pieces of text, ToRoom side first."""
    to_text_value = to_label or u"no room"
    from_text_value = from_label or u"no room"
    if is_problem(state):
        to_text_value = u"! " + to_text_value
    return to_text_value, from_text_value


def phase_labels(entries):
    """Picker labels for the phases, and the map back.

    `entries` is [(name, room_count)] in document order. The count is on
    the label because the phase to pick is the one the rooms are in, and
    nobody should have to know that in advance. The first version of this
    tool asked the document's last phase without asking anyone, got no
    rooms at all on a live project, and drew a plan that said "no room" at
    every door.

    Returns (labels, mapping) where mapping is label to name.
    """
    labels = []
    mapping = {}
    for name, count in entries:
        text = to_text(name)
        if count is None:
            label = text
        elif count == 1:
            label = u"{0}  (1 room)".format(text)
        else:
            label = u"{0}  ({1} rooms)".format(text, count)
        suffix = 2
        while label in mapping and mapping[label] != text:
            label = u"{0} [{1}]".format(label, suffix)
            suffix += 1
        labels.append(label)
        mapping[label] = text
    return labels, mapping


def busiest_phase(entries):
    """The name of the phase holding the most rooms, or empty.

    Only used to say so in the report when the chosen phase has none,
    which is the whole of what went wrong the first time.
    """
    best_name = u""
    best_count = 0
    for name, count in entries:
        if count and count > best_count:
            best_count = count
            best_name = to_text(name)
    return best_name


def view_name(prefix, level_name, phase_name):
    """A stable name, so a rerun finds the view it made last time."""
    parts = [to_text(prefix), to_text(level_name)]
    if phase_name:
        parts.append(to_text(phase_name))
    return u" - ".join(part for part in parts if part)


class Tally(object):
    def __init__(self):
        self.counts = {}
        self.problems = []

    def add(self, state, element_id=None, description=u""):
        self.counts[state] = self.counts.get(state, 0) + 1
        if is_problem(state) and element_id is not None:
            self.problems.append((state, element_id, to_text(description)))

    def total(self):
        return sum(self.counts.values())

    def rows(self):
        """(label, count) for every state that occurred, in report order."""
        return [(STATE_LABELS[key], self.counts[key])
                for key, _colour, _label in STATES if key in self.counts]
