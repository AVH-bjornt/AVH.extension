# -*- coding: utf-8 -*-
"""Bring the openings Pipe Openings made back in line with their pipes.

Every opening Pipe Openings makes (from 2.48.0) carries a tag: which
pipe or conduit it was cut for, in which host, with which settings, and
where and how big it was (`avh_openings.tags`, kept in Revit's
extensible storage by `avh_openings.storage`). This button reads those
tags and sorts every tagged opening, in the selection or, with nothing
selected, in the whole model:

- unchanged: its pipe still needs exactly this opening; left alone;
- changed: the pipe moved, changed size or gained insulation; the old
  opening is deleted and a new one cut with the same settings;
- orphaned: the pipe is gone or no longer passes through the host;
  deleted only if you say so, in a question of its own that defaults
  to no;
- cannot check: the pipe's link is not loaded, or the opening cannot be
  changed (checked out by someone else, say); left alone.

Nothing changes until the list has been shown and confirmed.

## How an opening is checked

A pipe that runs through its own opening no longer meets the host's
solid, so the crossing cannot be measured with the opening in place.
Each one is therefore deleted inside a transaction that is always
rolled back, the crossing measured on the whole host, and the opening
sized exactly as Pipe Openings would. That is compared with the tag.

The update itself deletes the old opening and makes the new one in the
same transaction, using the same code as Pipe Openings
(`avh_openings.writer`), so a new opening Revit refuses leaves the old
one where it was. All of it is one transaction group: one Ctrl+Z.

## Unverified

The tag storage calls listed in `avh_openings.storage`, and
`Document.Delete` and `GetElement` by UniqueId, are from the API
documentation only.
"""

__title__ = "Update\nOpenings"
__author__ = "AVH"
__doc__ = "Check the openings Pipe Openings made against their pipes: re-cut the ones whose pipe moved or changed size, and list the ones whose pipe is gone. Shows the list and asks before changing anything."

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
from avh_openings import model, reader, storage   # noqa: E402
from avh_openings import tags, writer             # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Update Openings"

SAME = u"unchanged"
CHANGED = u"changed"
ORPHAN = u"orphaned"
CANNOT = u"cannot check"


class Item(object):
    """One tagged opening: what it is, what was found, what happened.
    Shaped like a Pipe Openings row, so the shared writer can make its
    replacement: host, opening, entry, exit, normals."""

    def __init__(self, element, tag):
        self.element = element
        # Kept now, because once the opening is re-cut or deleted Revit
        # refuses to give its Id ("The referenced object is not valid"),
        # and 2.48.0's report crashed asking for it after the work was
        # done. Everything after the first delete uses this instead.
        self.element_id = element.Id
        self.tag = tag
        self.status = CANNOT
        self.reason = u""
        self.host = None
        self.pipe = None
        self.opening = None
        self.entry = None
        self.exit = None
        self.normals = (None, None)
        self.result = u""
        self.done = False


# --------------------------------------------------------------------------
# Finding the tagged openings
# --------------------------------------------------------------------------

def is_round_family(element):
    try:
        return to_text(element.Symbol.Family.Name) == writer.FAMILY_NAME
    except BaseException:
        return False


def tagged_openings(doc):
    """(items, untagged, unreadable). untagged counts openings with no
    tag at all; unreadable is [(element, why)] for tags that would not
    read."""
    items = []
    untagged = 0
    unreadable = []
    for cls in (DB.Opening, DB.FamilyInstance):
        try:
            elements = list(DB.FilteredElementCollector(doc).OfClass(cls))
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        for element in elements:
            if cls is DB.FamilyInstance and not is_round_family(element):
                continue
            try:
                text = storage.read(DB, element)
            except BaseException as exc:
                unreadable.append((element, to_text(exc)))
                continue
            if not text:
                untagged += 1
                continue
            try:
                items.append(Item(element, tags.decode(text)))
            except tags.TagError as exc:
                unreadable.append((element, to_text(exc)))
    return items, untagged, unreadable


def in_selection(items, doc, uidoc):
    """The items the selection asks about: openings selected, or every
    opening in a selected host. All of them when nothing is selected.
    Returns (items, anything_selected)."""
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        ids = []
    if not ids:
        return items, False
    chosen_ids = set(reader.id_value(element_id) for element_id in ids)
    chosen_uids = set()
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is not None:
            chosen_uids.add(to_text(element.UniqueId))
    kept = [item for item in items
            if reader.id_value(item.element_id) in chosen_ids or
            item.tag.host in chosen_uids]
    return kept, True


# --------------------------------------------------------------------------
# Checking each one
# --------------------------------------------------------------------------

def find_pipe(doc, tag):
    """(Pipe, status, reason). Pipe is None unless it was found."""
    source = u""
    transform = None
    if tag.link:
        instance = doc.GetElement(tag.link)
        if instance is None:
            return None, CANNOT, u"its pipe's link is no longer in the model"
        try:
            link_doc = instance.GetLinkDocument()
        except BaseException:
            link_doc = None
        if link_doc is None:
            return None, CANNOT, u"its pipe's link is not loaded"
        try:
            transform = instance.GetTotalTransform()
        except BaseException:
            transform = None
        source = to_text(link_doc.Title)
        element = link_doc.GetElement(tag.pipe)
        if element is None:
            return None, ORPHAN, u"its {0} is no longer in {1}".format(
                tag.pipe_kind.lower(), source)
    else:
        element = doc.GetElement(tag.pipe)
        if element is None:
            return None, ORPHAN, u"its {0} has been deleted".format(
                tag.pipe_kind.lower())
    pipe = reader.pipe_of(DB, element, tag.pipe_kind, source, transform,
                          tag.link)
    if pipe is None:
        return None, CANNOT, u"its {0} is no longer straight".format(
            tag.pipe_kind.lower())
    return pipe, None, u""


def nearest(found, tag):
    """The crossing nearest where the opening was: a pipe can cross the
    same host twice."""
    def distance(crossing):
        middle = model.scale(model.add(crossing[1], crossing[2]), 0.5)
        return model.length(model.sub(middle, tag.center))
    return min(found, key=distance) if found else None


def check(doc, item, symbol):
    """Fill in the item's status, measuring with its opening deleted in
    a transaction that is always rolled back."""
    tag = item.tag
    pipe, status, reason = find_pipe(doc, tag)
    if pipe is None:
        item.status, item.reason = status, reason
        return
    item.pipe = pipe
    host_element = doc.GetElement(tag.host)
    if host_element is None:
        item.status = ORPHAN
        item.reason = u"its host has been deleted"
        return
    if tag.kind == tags.ROUND and symbol is None:
        item.status = CANNOT
        item.reason = u"the {0} family is not loaded".format(
            writer.FAMILY_NAME)
        return

    transaction = DB.Transaction(doc, TITLE + u" (check)")
    transaction.Start()
    try:
        try:
            doc.Delete(item.element_id)
        except BaseException as exc:
            item.status = CANNOT
            item.reason = u"it cannot be changed: {0}".format(to_text(exc))
            return
        doc.Regenerate()
        host, why = reader.read_host(DB, host_element)
        if host is None:
            item.status = CANNOT
            item.reason = u"its host cannot be read: {0}".format(why)
            return
        item.host = host
        found = nearest(reader.crossings(DB, host, [pipe]), tag)
        if found is None:
            item.status = ORPHAN
            item.reason = u"its {0} no longer passes through the " \
                          u"{1}".format(tag.pipe_kind.lower(),
                                        writer.HOST_WORDS[host.kind].lower())
            return
        _, item.entry, item.exit, entry_normal, exit_normal = found
        item.normals = (entry_normal, exit_normal)
        opening, why = model.size_opening(
            item.entry, item.exit, pipe.diameter, host.frame,
            tag.settings(), entry_normal, exit_normal)
        if opening is None:
            item.status = CANNOT
            item.reason = u"a new opening would be skipped: " + why
            return
        item.opening = opening
        if tags.same(tag, opening):
            item.status = SAME
        else:
            item.status = CHANGED
            item.reason = describe_change(tag, opening)
    finally:
        transaction.RollBack()


def describe_change(tag, opening):
    """What moved, in words, for the report."""
    frame = opening.frame
    then = frame.local(tag.center)
    now = frame.local(opening.center)
    moved = model.MM_PER_FOOT * ((then[0] - now[0]) ** 2 +
                                 (then[1] - now[1]) ** 2) ** 0.5
    parts = []
    if moved > 0.5:
        parts.append(u"moved {0} mm".format(model.format_number(
            round(moved))))
    if abs(opening.pipe_diameter - tag.pipe_diameter) * \
            model.MM_PER_FOOT > 0.5:
        parts.append(u"pipe {0} mm, was {1}".format(
            model.format_number(opening.pipe_diameter * model.MM_PER_FOOT),
            model.format_number(tag.pipe_diameter * model.MM_PER_FOOT)))
    if not parts:
        parts.append(u"size changed")
    return u", ".join(parts)


# --------------------------------------------------------------------------
# Changing them
# --------------------------------------------------------------------------

def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return True if committed is None else status == committed


def find_symbol(doc):
    try:
        for symbol in DB.FilteredElementCollector(doc).OfClass(
                DB.FamilySymbol):
            try:
                if to_text(symbol.Family.Name) == writer.FAMILY_NAME:
                    return symbol
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


def tagger(item):
    """Tag the replacement exactly as Pipe Openings would have."""
    def tag(element, kind):
        try:
            old = item.tag
            text = tags.from_opening(item.opening, old.settings(), old.pipe,
                                     old.link, old.pipe_kind, old.host,
                                     kind).encode()
            storage.write(DB, element, text)
        except BaseException as exc:
            logger.debug(to_text(exc))
            item.reason += u"; the new opening is not tagged: " + \
                to_text(exc)
    return tag


def update_all(doc, items, delete_orphans, symbol):
    """Re-cut the changed, delete the orphans if asked, in one group.
    Returns how many were changed."""
    made_by = writer.Writer(DB, doc, TITLE, symbol, logger)
    group = DB.TransactionGroup(doc, TITLE)
    group.Start()
    changed = 0
    try:
        for item in items:
            if item.status == CHANGED:

                def delete_old(item=item):
                    doc.Delete(item.element_id)
                    doc.Regenerate()

                element, kind, text = made_by.make_one(
                    item, before=delete_old, after=tagger(item))
                if element is None:
                    item.result = u"left as it was; the new one was " + text
                    continue
                item.done = True
                item.result = u"updated" if text == u"made" else \
                    u"updated, " + text
                changed += 1
            elif item.status == ORPHAN and delete_orphans:

                def delete(item=item):
                    doc.Delete(item.element_id)
                    return True

                done, why = made_by.attempt(delete)
                if done:
                    item.done = True
                    item.result = u"deleted"
                    changed += 1
                else:
                    item.result = u"not deleted: " + why
    except BaseException:
        group.RollBack()
        raise
    if not changed:
        group.RollBack()
        return 0
    status = group.Assimilate()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is not None and status != committed:
        for item in items:
            if item.done:
                item.done = False
                item.result = u"not kept: Revit rolled the run back"
        return 0
    return changed


def verify(items):
    """A re-cut opening must clear its pipe, whatever Revit reported."""
    for item in items:
        if item.done and item.status == CHANGED and \
                writer.axis_still_meets(DB, item.host.element, item.pipe,
                                        item.entry, item.exit):
            item.done = False
            item.result = u"re-cut, but it does not clear the pipe. " \
                          u"Check it."


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def link(element_id):
    try:
        return output.linkify(element_id)
    except BaseException:
        return to_text(reader.id_value(element_id))


def table_cell(text):
    return to_text(text).replace(u"|", u"/").replace(u"\n", u" ") or u"-"


def what_happens(item, after):
    if after and item.result:
        return item.result
    if item.status == SAME:
        return u"unchanged, left alone"
    if item.status == CHANGED:
        return u"re-cut: " + item.reason
    if item.status == ORPHAN:
        return u"orphaned: " + item.reason
    return u"left alone: " + item.reason


def print_items(items, after):
    """One table, printed whole, every column left aligned: see the
    pyrevit-scripts skill and NOTES.md, Pipe Openings."""
    lines = [u"| Opening | Host | Pipe | Was mm | Now mm | {0} |".format(
                 u"Result" if after else u"What happens"),
             u"| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for item in items:
        host = u"-"
        if item.host is not None:
            host = u"{0} {1}".format(writer.HOST_WORDS[item.host.kind],
                                     link(item.host.element.Id))
        pipe = u"-"
        if item.pipe is not None:
            pipe = u"{0} {1}".format(item.pipe.kind, item.pipe.id) \
                if item.pipe.source else \
                u"{0} {1}".format(item.pipe.kind, link(item.pipe.element.Id))
            if item.pipe.source:
                pipe += u" in " + item.pipe.source
        now = item.opening.size_text() if item.opening is not None \
            else u"-"
        lines.append(u"| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            link(item.element_id), host, table_cell(pipe),
            item.tag.size_text(), now,
            table_cell(what_happens(item, after))))
    output.print_md(u"### {0}: {1}".format(
        TITLE, u"result" if after else u"found"))
    output.print_md(u"\n".join(lines))


def count(items, status):
    return len([item for item in items if item.status == status])


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    items, untagged, unreadable = tagged_openings(doc)
    everything = len(items)
    items, selected = in_selection(items, doc, uidoc)

    for element, why in unreadable:
        output.print_md(u"- Opening {0} has a tag that cannot be read, so "
                        u"it was left alone: {1}".format(
                            link(element.Id), why))
    if untagged:
        output.print_md(
            u"- {0} opening(s) in the model carry no Pipe Openings tag: "
            u"made before 2.48.0 or by hand. They were not "
            u"looked at.".format(untagged))

    if not items:
        if selected and everything:
            message = (u"None of what is selected is a tagged opening or "
                       u"a wall, floor or roof with one. Select those, or "
                       u"nothing to check the whole model.")
        else:
            message = (u"This model has no openings made by Pipe Openings "
                       u"2.48.0 or later, so there is nothing to update.")
        forms.alert(message, title=TITLE)
        return

    symbol = find_symbol(doc)
    for item in items:
        try:
            check(doc, item, symbol)
        except BaseException as exc:
            item.status = CANNOT
            item.reason = u"it could not be checked: {0}".format(
                to_text(exc))
    print_items(items, after=False)

    changed = count(items, CHANGED)
    orphans = count(items, ORPHAN)
    summary = (u"{0} tagged opening(s) checked{1}: {2} unchanged, {3} to "
               u"re-cut, {4} orphaned, {5} that cannot be "
               u"checked.".format(len(items),
                                  u" in the selection" if selected else u"",
                                  count(items, SAME), changed, orphans,
                                  count(items, CANNOT)))
    if not changed and not orphans:
        forms.alert(summary + u"\n\nNothing to do.", title=TITLE)
        return

    # A tool that writes stops if the question cannot be asked: nobody
    # confirmed anything.
    update = False
    if changed:
        update = forms.alert(
            summary + u"\n\nRe-cut the {0} changed opening(s)? Each is "
            u"deleted and cut again for where its pipe is now, with the "
            u"settings it was made with.".format(changed),
            title=TITLE, yes=True, no=True)
    delete_orphans = False
    if orphans:
        delete_orphans = forms.alert(
            u"{0} opening(s) no longer have a pipe through them; the "
            u"output window says why for each.\n\nDelete them? No leaves "
            u"them where they are.".format(orphans),
            title=TITLE, yes=True, no=True)
    if not update:
        for item in items:
            if item.status == CHANGED:
                item.status = CANNOT
                item.reason = u"you chose not to re-cut"
    if not update and not delete_orphans:
        return

    done = update_all(doc, items, delete_orphans, symbol)
    try:
        verify(items)
    except BaseException as exc:
        logger.debug(to_text(exc))
    print_items(items, after=True)

    recut = len([item for item in items
                 if item.done and item.status == CHANGED])
    deleted = len([item for item in items
                   if item.done and item.status == ORPHAN])
    failed = len([item for item in items
                  if item.status in (CHANGED, ORPHAN) and not item.done and
                  (item.status == CHANGED or delete_orphans)])
    message = u"{0} opening(s) re-cut, {1} deleted.".format(recut, deleted)
    if failed:
        message += u" {0} could not be, or need checking; see the output " \
                   u"window.".format(failed)
    if done:
        message += u"\n\nOne undo takes the whole run back."
    forms.alert(message, title=TITLE)


if __name__ == "__main__":
    run()
