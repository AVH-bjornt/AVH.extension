# -*- coding: utf-8 -*-
"""Box arithmetic for zooming to a selection.

No Revit here. A bounding box is six numbers, and everything this tool
decides is arithmetic on them, so it can be tested outside Revit.
"""

MM_PER_FOOT = 304.8

# How much air to leave around the selection, as a fraction of its
# largest dimension.
PADDING = 0.15

# And a floor under that, in millimetres. Without it, zooming to a door
# handle fills the screen with a 20 mm object and you cannot tell where
# in the building you are. With it, the smallest thing you can select
# still arrives with half a metre of context around it.
MIN_MARGIN_MM = 500.0


def combine(boxes):
    """One box containing all of them. Returns None for nothing.

    `boxes` is an iterable of (min_xyz, max_xyz), each a 3 tuple.
    """
    lows = []
    highs = []
    for low, high in boxes:
        if low is None or high is None:
            continue
        lows.append(low)
        highs.append(high)

    if not lows:
        return None

    low = tuple(min(point[axis] for point in lows) for axis in range(3))
    high = tuple(max(point[axis] for point in highs) for axis in range(3))
    return low, high


def pad(box, padding=PADDING, min_margin_mm=MIN_MARGIN_MM):
    """Grow a box so the selection is not jammed against the screen edge.

    The margin is the larger of a fraction of the biggest dimension and
    a fixed minimum, applied equally on every axis so the zoom does not
    end up off centre.
    """
    if box is None:
        return None
    low, high = box
    spans = [high[axis] - low[axis] for axis in range(3)]
    margin = max(max(spans) * padding, min_margin_mm / MM_PER_FOOT)
    return (tuple(low[axis] - margin for axis in range(3)),
            tuple(high[axis] + margin for axis in range(3)))


def describe(count, not_in_view):
    """One line for the report. Empty when there is nothing worth saying."""
    if not_in_view <= 0:
        return u""
    if not_in_view == count:
        return (u"None of the {0} selected element(s) draw anything in "
                u"this view, so the zoom used their position in the "
                u"model instead.".format(count))
    return (u"{0} of {1} selected element(s) draw nothing in this view, "
            u"so their position in the model was used "
            u"instead.".format(not_in_view, count))
