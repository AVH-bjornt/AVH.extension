# -*- coding: utf-8 -*-
"""Which scope boxes in a link are new, changed or already copied.

No Revit here. A scope box is a name and eight corners, and everything
this tool decides is a comparison of those, so all of it is testable
outside Revit.

## Why matching is by name

There is no hidden id tying a copied scope box to the one it came from,
and scope boxes take no custom parameters to record one. So the name
carries it: every copy is named after its link, `ELG_ARK - DA11`, and the
tool only ever touches boxes whose names start with that link's prefix.
A scope box you made yourself is never matched, never replaced, never
deleted.

## Why the comparison is on corners, not a bounding box

A bounding box is axis aligned, so a scope box rotated a few degrees
about its centre can keep the same bounding box while its corners move.
Comparing the eight real corners catches a rotation; comparing the box
around them would report it unchanged.
"""

from avh_forma.model import sanitize_view_name
from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8

# Joins the link label to the box name. Revit refuses `|` and `:` in
# element names, which ruled out the obvious separators.
SEPARATOR = u" - "

# Two corners closer than this are the same corner. Tighter than any
# scope box is ever drawn to, and loose enough that a copy through a
# transform never trips it on rounding.
TOLERANCE_MM = 1.0

NEW = "new"
CHANGED = "changed"
SAME = "same"
ORPHAN = "orphan"
DUPLICATE = "duplicate"

STATES = (
    (NEW, u"new in the link, will be copied in"),
    (CHANGED, u"changed in the link, will be replaced"),
    (SAME, u"unchanged"),
    (ORPHAN, u"no longer in the link, left alone"),
    (DUPLICATE, u"name used twice in the link, skipped"),
)
STATE_LABELS = dict(STATES)


def link_label(type_name):
    """A short, legal label for a link, from its type name.

    `ELG_CC01_ARK.rvt` becomes `ELG_CC01_ARK`. The extension is stripped
    whatever its case, and anything Revit refuses in a name becomes an
    underscore rather than disappearing.
    """
    text = to_text(type_name).strip()
    if text.lower().endswith(u".rvt"):
        text = text[:-4]
    return sanitize_view_name(text).strip()


def prefix_of(label):
    return to_text(label) + SEPARATOR


def prefixed(label, box_name):
    """The name a copy of `box_name` from this link gets in the host."""
    return prefix_of(label) + sanitize_view_name(to_text(box_name)).strip()


def is_ours(name, label):
    """True when a host scope box was made by this tool for this link."""
    return to_text(name).startswith(prefix_of(label))


def _close(a, b, tolerance_feet):
    return all(abs(a[i] - b[i]) <= tolerance_feet for i in range(3))


def corners_from_edges(edges, tolerance_mm=TOLERANCE_MM):
    """The distinct endpoints of a set of edges.

    `edges` is [((x, y, z), (x, y, z))] in feet. A scope box has twelve
    edges and eight corners; each corner is the end of three edges, so
    the raw endpoints repeat and are merged here within the tolerance.
    """
    tolerance = tolerance_mm / MM_PER_FOOT
    corners = []
    for start, end in edges:
        for point in (start, end):
            point = tuple(float(value) for value in point)
            if not any(_close(point, seen, tolerance) for seen in corners):
                corners.append(point)
    return corners


def apply_transform(transform, point):
    """A point through a transform given as (origin, x, y, z) basis tuples.

    The same arithmetic as Revit's `Transform.OfPoint`, written out so
    the comparison against a link's own geometry can be tested.
    """
    origin, basis_x, basis_y, basis_z = transform
    x, y, z = (float(value) for value in point)
    return tuple(origin[i] + x * basis_x[i] + y * basis_y[i] + z * basis_z[i]
                 for i in range(3))


def same_shape(corners_a, corners_b, tolerance_mm=TOLERANCE_MM):
    """Do two corner sets describe the same box?

    Each corner of one has to pair with a distinct corner of the other.
    Pairing greedily with removal is enough for eight well separated
    points, and it is what stops one corner of `a` matching two of `b`.
    """
    if len(corners_a) != len(corners_b) or not corners_a:
        return False
    tolerance = tolerance_mm / MM_PER_FOOT
    remaining = list(corners_b)
    for corner in corners_a:
        match = None
        for index, candidate in enumerate(remaining):
            if _close(corner, candidate, tolerance):
                match = index
                break
        if match is None:
            return False
        remaining.pop(match)
    return True


def largest_shift_mm(corners_a, corners_b):
    """How far the worst corner moved, in millimetres. For the report.

    Each corner of `a` is paired with its nearest in `b`. Returns None
    when either side has no corners, since there is then nothing to
    measure.
    """
    if not corners_a or not corners_b:
        return None
    worst = 0.0
    for corner in corners_a:
        nearest = min(
            sum((corner[i] - other[i]) ** 2 for i in range(3)) ** 0.5
            for other in corners_b)
        worst = max(worst, nearest)
    return worst * MM_PER_FOOT


class Box(object):
    """A scope box as this module sees it: a name, an id, its corners.

    `corners` are always in host coordinates. A link box's corners are
    put through the link transform before it reaches here.
    """

    def __init__(self, name, element_id, corners):
        self.name = to_text(name)
        self.element_id = element_id
        self.corners = list(corners)


def classify(link_boxes, host_boxes, label):
    """Decide what to do with every box, in one plan.

    Returns [(state, link_box, host_box, target_name)], link boxes in
    their own order first, then host boxes left over. `host_boxes` may be
    every scope box in the host; only this link's prefixed ones are ever
    matched or reported.
    """
    plan = []

    counts = {}
    for box in link_boxes:
        counts[box.name] = counts.get(box.name, 0) + 1

    ours = dict((box.name, box) for box in host_boxes
                if is_ours(box.name, label))
    claimed = set()

    for box in link_boxes:
        target = prefixed(label, box.name)
        claimed.add(target)
        if counts[box.name] > 1:
            plan.append((DUPLICATE, box, None, target))
            continue
        existing = ours.get(target)
        if existing is None:
            plan.append((NEW, box, None, target))
        elif same_shape(box.corners, existing.corners):
            plan.append((SAME, box, existing, target))
        else:
            plan.append((CHANGED, box, existing, target))

    for name in sorted(ours):
        if name not in claimed:
            plan.append((ORPHAN, None, ours[name], name))

    return plan


def writes(plan):
    """The entries that change the model, in the order they run."""
    return [entry for entry in plan if entry[0] in (NEW, CHANGED)]


def summary(plan):
    """[(label, count)] for every state present, in report order."""
    counts = {}
    for entry in plan:
        counts[entry[0]] = counts.get(entry[0], 0) + 1
    return [(label, counts[key]) for key, label in STATES if key in counts]
