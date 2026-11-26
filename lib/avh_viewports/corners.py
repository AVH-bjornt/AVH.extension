# -*- coding: utf-8 -*-
"""Where a placed view's crop region lies on its sheet.

Shared by Align Views and Batch Sheets. Revit 2023 added two transforms:
`View.GetModelToProjectionTransforms` gives, for each crop region of a
view, its boundary in the model and the map from model to the view's
projection; `Viewport.GetProjectionToSheetTransform` maps that
projection onto the sheet. Every point of the crop boundary goes through
both, and the top left of the result is the corner. A view split into
several crop regions uses all of them.

The sheet transform follows the viewport's place only after Revit
regenerates, so callers regenerate after creating or moving a viewport
and before reading its corner.
"""

from avh_schedules.compat import to_text
from avh_viewports import model


def crop_corner(view, viewport):
    """(x, y) on the sheet of the top left of the view's crop, or a
    reason it could not be found, as (None, reason)."""
    try:
        if not view.CropBoxActive:
            return None, u"its crop region is off"
    except BaseException:
        return None, u"it has no crop region"
    try:
        regions = list(view.GetModelToProjectionTransforms())
        to_sheet = viewport.GetProjectionToSheetTransform()
    except BaseException as exc:
        return None, u"Revit would not give its transforms: {0}".format(
            to_text(exc))
    points = []
    for region in regions:
        try:
            to_projection = region.GetModelToProjectionTransform()
            boundary = region.GetBoundary()
        except BaseException:
            continue
        if boundary is None:
            continue
        for curve in boundary:
            try:
                for point in curve.Tessellate():
                    on_sheet = to_sheet.OfPoint(to_projection.OfPoint(point))
                    points.append((on_sheet.X, on_sheet.Y))
            except BaseException:
                continue
    corner = model.top_left(points)
    if corner is None:
        return None, u"its crop boundary could not be read"
    return corner, u""
