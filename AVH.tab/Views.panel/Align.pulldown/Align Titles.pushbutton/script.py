# -*- coding: utf-8 -*-
"""Align view titles on other sheets to one view's title.

Select a viewport on a sheet (or pick a placed view from a list), then
tick the views whose titles should match it. Each ticked title is moved
so that its bottom left lands on the sheet exactly where the chosen
title's does, and, if asked, its line is given the chosen title's
length. A switch in the same dialog gives them the chosen viewport's
type first, so the title family and its settings match too.

Meant to follow Align Views: once the drawings line up, so do their
titles. The title is placed at the same point on the sheet, not at the
same distance from its own drawing.

## Why the title's outline and not its offset

`Viewport.LabelOffset` (Revit 2022) is measured from the viewport's own
box, and that box grows with every grid head, tag or dimension past the
crop. Two views with the same offset can have their titles in different
places. So the script reads where each title actually is
(`Viewport.GetLabelOutline`), shifts its offset by the difference, and
reads it back.

## And then it is checked

After the change the document is regenerated, every title is read
again, and one that did not land within 0.1 mm, or whose line is not
the chosen length, is reported. If any missed, the whole run is rolled
back, so the titles are either all aligned or all as they were.

## Unverified

Ran in Revit 2025: position, line length and the viewport type switch
all worked. Listed before that run: `Viewport.LabelOffset` and `LabelLineLength`
(get and set), `GetLabelOutline` and whether it is current before a
regenerate (the code assumes not and regenerates), and whether a title
switched off in the viewport type still gives an outline. The readback
catches each of these as a miss rather than a false success.
"""

__title__ = "Align\nTitles"
__author__ = "AVH"
__doc__ = "Align view titles on other sheets to one chosen title: same place on the sheet, and optionally the same title line length and viewport type."

import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_viewports import model                   # noqa: E402
from avh_viewports import picker                  # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Align Titles"
BOTH = u"Position and line length"
POSITION = u"Position only"
MATCH_TYPE = u"Match viewport type"


class Placed(object):
    """A viewport, its view and its sheet, read once."""

    def __init__(self, viewport, view, sheet, collection=None):
        self.viewport = viewport
        self.collection = collection
        self.view = view
        self.sheet = sheet
        self.label = model.label(sheet.SheetNumber, sheet.Name, view.Name)


def placed_views(doc):
    found = []
    for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
        try:
            view = doc.GetElement(viewport.ViewId)
            sheet = doc.GetElement(viewport.SheetId)
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        if view is None or sheet is None:
            continue
        found.append(Placed(viewport, view, sheet,
                            picker.collection_name(doc, sheet)))
    found.sort(key=lambda p: p.label)
    return found


def selected_viewport(placed):
    """The placed view whose viewport is selected, or None."""
    try:
        ids = list(revit.uidoc.Selection.GetElementIds())
    except BaseException:
        return None
    by_id = dict((p.viewport.Id, p) for p in placed)
    chosen = [by_id[i] for i in ids if i in by_id]
    return chosen[0] if len(chosen) == 1 else None


def pick(placed, multiselect, title, button):
    """The picker, with a sheet collection dropdown where the model has
    collections."""
    return picker.pick(forms, placed, multiselect, title, button)


def title_hidden(doc, viewport):
    """True when the viewport type has Show Title set to No."""
    try:
        vp_type = doc.GetElement(viewport.GetTypeId())
        param = vp_type.get_Parameter(
            DB.BuiltInParameter.VIEWPORT_ATTR_SHOW_LABEL)
        return param is not None and param.AsInteger() == 0
    except BaseException as exc:
        logger.debug(to_text(exc))
        return False


def title_corner(doc, placed):
    """(x, y) on the sheet of the bottom left of the view's title, or
    (None, reason)."""
    if title_hidden(doc, placed.viewport):
        return None, u"its viewport type does not show a title"
    try:
        outline = placed.viewport.GetLabelOutline()
        low = outline.MinimumPoint
    except BaseException as exc:
        return None, u"Revit would not give its title outline: {0}".format(
            to_text(exc))
    corner = model.title_corner((low.X, low.Y))
    if corner is None:
        return None, u"its title outline could not be read"
    return corner, u""


def line_length(placed):
    try:
        return placed.viewport.LabelLineLength
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def unpack_choice(picked):
    """(mode, match_type) from CommandSwitchWindow. With switches it
    returns (option, {switch: bool}); a closed window may come back as
    None or as (None, {...})."""
    switches = {}
    if isinstance(picked, tuple):
        mode = picked[0] if picked else None
        if len(picked) > 1 and picked[1]:
            switches = picked[1]
    else:
        mode = picked
    return mode, bool(switches.get(MATCH_TYPE, False))


def type_id(placed):
    try:
        return placed.viewport.GetTypeId()
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def is_pinned(element):
    try:
        return bool(element.Pinned)
    except BaseException:
        return False


def report(source, corner, length, rows, match_type=False):
    head = u"Aligned to the title of **{0}**, bottom left at X {1}, Y {2} " \
           u"on its sheet".format(source.label.replace(u"|", u"/"),
                                  model.mm(corner[0]), model.mm(corner[1]))
    if length is not None:
        head += u", line {0} long".format(model.mm(length))
    if match_type:
        head += u", same viewport type"
    lines = [
        u"### {0}".format(TITLE),
        head + u".",
        u"",
        u"| View | Result |",
        u"| :--- | :--- |",
    ]
    for placed, text in rows:
        lines.append(u"| {0} {1} | {2} |".format(
            output.linkify(placed.sheet.Id), placed.label.replace(u"|", u"/"),
            text.replace(u"|", u"/").replace(u"\n", u" ")))
    output.print_md(u"\n".join(lines))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    placed = placed_views(doc)
    if len(placed) < 2:
        forms.alert(u"Fewer than two views are placed on sheets, so there "
                    u"is nothing to align.", title=TITLE)
        return

    source = selected_viewport(placed)
    if source is None:
        source = pick(placed, False, TITLE + u": align to which title?",
                      u"Align to this")
    if source is None:
        return
    source_corner, why = title_corner(doc, source)
    if source_corner is None:
        forms.alert(u"The title of {0} cannot be aligned to: {1}.".format(
            source.label, why), title=TITLE)
        return

    mode, match_type = unpack_choice(forms.CommandSwitchWindow.show(
        [BOTH, POSITION], switches={MATCH_TYPE: False},
        message=u"Match the title's place on the sheet, and its line "
                u"length too? Switch on Match viewport type to give the "
                u"titles the same viewport type first."))
    if not mode:
        return
    source_type = None
    if match_type:
        source_type = type_id(source)
        if source_type is None:
            forms.alert(u"Revit would not give the viewport type of "
                        u"{0}.".format(source.label), title=TITLE)
            return
    length = None
    if mode == BOTH:
        length = line_length(source)
        if length is None:
            forms.alert(u"Revit would not give the title line length of "
                        u"{0}.".format(source.label), title=TITLE)
            return

    others = [p for p in placed if p is not source]
    targets = pick(others, True,
                   TITLE + u": tick the views whose titles line up with " +
                   source.label, u"Align ticked")
    if not targets:
        return

    rows = []
    movable = []
    for target in targets:
        if is_pinned(target.viewport):
            rows.append((target, u"skipped: the viewport is pinned"))
            continue
        if match_type:
            # Its own type may hide the title; the source's shows it.
            # The title is read once the type has been changed.
            movable.append((target, None))
            continue
        corner, why = title_corner(doc, target)
        if corner is None:
            rows.append((target, u"skipped: " + why))
            continue
        movable.append((target, corner))
    if not movable:
        report(source, source_corner, length, rows, match_type)
        forms.alert(u"None of the ticked titles could be aligned. The "
                    u"output window says why.", title=TITLE)
        return

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        if source_type is not None:
            for target, _ in movable:
                if type_id(target) != source_type:
                    target.viewport.ChangeTypeId(source_type)
            # Another title family moves the title's outline.
            doc.Regenerate()
        if length is not None:
            for target, _ in movable:
                target.viewport.LabelLineLength = length
            # A longer line can widen the title's outline; read it fresh.
            doc.Regenerate()
        moves = []
        for target, before in movable:
            current, why = title_corner(doc, target)
            if current is None:
                current = before
            if current is None:
                moves.append(None)
                continue
            offset = target.viewport.LabelOffset
            x, y = model.new_offset((offset.X, offset.Y), current,
                                    source_corner)
            target.viewport.LabelOffset = DB.XYZ(x, y, offset.Z)
            moves.append(model.shift(source_corner, current))
        # The title outline follows the offset only after regeneration.
        doc.Regenerate()
        missed = 0
        for (target, _), move in zip(movable, moves):
            if source_type is not None and type_id(target) != source_type:
                missed += 1
                rows.append((target, u"**type not set**"))
                continue
            after, why = title_corner(doc, target)
            if (move is None or after is None or
                    not model.landed(source_corner, after)):
                missed += 1
                if after is not None:
                    why = u"title at X {0}, Y {1}".format(
                        model.mm(after[0]), model.mm(after[1]))
                rows.append((target, u"**did not land**: {0}".format(
                    why or u"its title could not be read")))
                continue
            if length is not None and not model.same_length(
                    length, line_length(target)):
                missed += 1
                rows.append((target, u"**line not set**: it is {0}".format(
                    model.mm(line_length(target) or 0.0))))
                continue
            text = u"moved {0} across, {1} up".format(model.mm(move[0]),
                                                      model.mm(move[1]))
            if length is not None:
                text += u", line set"
            if source_type is not None:
                text += u", type set"
            rows.append((target, text))
        if missed:
            transaction.RollBack()
            report(source, source_corner, length, rows, match_type)
            forms.alert(u"{0} title(s) did not come out right, so nothing "
                        u"was changed. The output window lists "
                        u"them.".format(missed), title=TITLE)
            return
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(u"Nothing was changed. The run stopped with: {0}".format(
            to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(u"Revit rejected the change, so nothing was changed "
                    u"({0}).".format(to_text(status)), title=TITLE)
        return
    report(source, source_corner, length, rows, match_type)


if __name__ == "__main__":
    run()
