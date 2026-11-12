# -*- coding: utf-8 -*-
"""Lining up views on different sheets by their crop region. No Revit here.

A view's crop region, not its viewport box, is what is aligned: the box
grows with any tag or dimension that reaches past the crop, so two
views with the same crop can have boxes that differ. The corner used is
the top left of the crop region as it lies on the sheet.

The script finds that corner by putting the crop boundary through two
transforms Revit provides (model to view projection, then projection to
sheet); this module does the arithmetic on the points that come out.
All sheet coordinates are in feet, as Revit gives them.
"""

from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8

# How close a corner must land to where it was sent. A tenth of a
# millimetre on the sheet, far finer than anyone can see and far coarser
# than the arithmetic's own error.
TOLERANCE_FT = 0.1 / MM_PER_FOOT


def top_left(points):
    """(x, y) of the top left of a set of sheet points: least X, most Y.

    For a rectangular crop that is its corner. For a rotated or shaped
    crop it is the corner of the box around it, which is still the same
    point on every view that shares the crop.
    """
    if not points:
        return None
    return (min(p[0] for p in points), max(p[1] for p in points))


def shift(source, target):
    """How far the target has to move to put its corner on the source's."""
    return (source[0] - target[0], source[1] - target[1])


def landed(source, target, tolerance=TOLERANCE_FT):
    return (abs(source[0] - target[0]) <= tolerance and
            abs(source[1] - target[1]) <= tolerance)


def mm(feet):
    return u"{0:.1f} mm".format(feet * MM_PER_FOOT)


def label(sheet_number, sheet_name, view_name):
    """How a placed view is shown in the pickers."""
    return u"{0}  {1}  /  {2}".format(to_text(sheet_number),
                                      to_text(sheet_name),
                                      to_text(view_name))
