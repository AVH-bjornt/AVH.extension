# -*- coding: utf-8 -*-
"""
Runs the real Align Titles script against a mocked Revit.

What is tested is where each title ends up on its sheet. The fake keeps
what Revit measures LabelOffset from to itself: each viewport's title
sits at its box corner, plus the offset, plus a hidden amount of its
own, the way grid heads and tags push a real viewport box out. A script
that copied the source's offset, or assumed what it is measured from,
puts the titles in different places and fails.

The fake lies the way Revit is expected to:

- The title outline follows the offset and line length as of the last
  regeneration, not as of the last set.
- A longer line widens the outline to the right but not to the left.
- A viewport type can switch its title off, and the title follows a
  type change only after regeneration.

Run outside Revit:

    python test_align_titles_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Views.panel",
                      "Align.pulldown", "Align Titles.pushbutton",
                      "script.py")

from avh_viewports import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


MM = 1.0 / 304.8
BOTH = u"Position and line length"
POSITION = u"Position only"
MATCH = u"Match viewport type"


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeXYZ(object):
    def __init__(self, x, y, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


SHOW_LABEL = "VIEWPORT_ATTR_SHOW_LABEL"


class FakeType(object):
    def __init__(self, doc, value, show):
        self.Id = FakeId(value)
        self.show = show
        doc.elements[value] = self

    def get_Parameter(self, which):
        if which != SHOW_LABEL:
            return None
        return Namespace(AsInteger=lambda: self.show)


class FakeRow(object):
    """pyRevit's TemplateListItem."""

    def __init__(self, orig_item, checked=False, checkable=True,
                 name_attr=None):
        self.item = orig_item
        self.state = checked


class FakeNamed(object):
    def __init__(self, doc, value, **kwargs):
        self.Id = FakeId(value)
        for key, item in kwargs.items():
            setattr(self, key, item)
        doc.elements[value] = self


class FakeViewport(object):
    """`box_min` is the corner of the viewport box on the sheet and
    `hidden` the private amount Revit adds; neither is visible to the
    script except through GetLabelOutline."""

    TEXT_WIDTH = 60 * MM
    TEXT_HEIGHT = 8 * MM

    def __init__(self, doc, value, view, sheet, vp_type, box_min, hidden,
                 offset=(0.0, 0.0), line=50 * MM, pinned=False):
        self.doc = doc
        self.Id = FakeId(value)
        self.ViewId = view.Id
        self.SheetId = sheet.Id
        self.type = vp_type
        self.box_min = box_min
        self.hidden = hidden
        self._offset = offset
        self._line = line
        self.Pinned = pinned
        self.regenerated = (offset, line, vp_type)
        doc.viewports.append(self)
        doc.elements[value] = self

    def GetTypeId(self):
        return self.type.Id

    def ChangeTypeId(self, type_id):
        if self.Pinned:
            raise Exception("pinned")
        self.doc.type_changes += 1
        if self.doc.change_type_works:
            self.type = self.doc.GetElement(type_id)

    def _get_offset(self):
        return FakeXYZ(self._offset[0], self._offset[1])

    def _set_offset(self, point):
        if self.Pinned:
            raise Exception("pinned")
        self._offset = (point.X, point.Y)

    LabelOffset = property(_get_offset, _set_offset)

    def _get_line(self):
        return self._line

    def _set_line(self, value):
        if self.Pinned:
            raise Exception("pinned")
        self._line = value

    LabelLineLength = property(_get_line, _set_line)

    def outline(self, offset, line):
        x = self.box_min[0] + offset[0] + self.hidden[0]
        y = self.box_min[1] + offset[1] + self.hidden[1]
        return (x, y), (x + max(self.TEXT_WIDTH, line), y + self.TEXT_HEIGHT)

    def GetLabelOutline(self):
        offset, line, vp_type = self.regenerated
        if vp_type.show == 0:
            raise Exception("no label")
        low, high = self.outline(offset, line)
        return Namespace(MinimumPoint=FakeXYZ(*low),
                         MaximumPoint=FakeXYZ(*high))

    def true_corner(self):
        return self.outline(self._offset, self._line)[0]


class FakeDoc(object):
    def __init__(self, commit_status="Committed", regenerate_works=True,
                 change_type_works=True):
        self.elements = {}
        self.viewports = []
        self.commit_status = commit_status
        self.regenerate_works = regenerate_works
        self.log = []
        self.change_type_works = change_type_works
        self.type_changes = 0
        self.shown = FakeType(self, 9001, 1)
        self.unshown = FakeType(self, 9002, 0)
        self.other = FakeType(self, 9003, 1)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def Regenerate(self):
        if self.regenerate_works:
            for viewport in self.viewports:
                viewport.regenerated = (viewport._offset, viewport._line,
                                        viewport.type)

    def place(self, value, number, box_min, hidden, offset=(0.0, 0.0),
              line=50 * MM, show=True, pinned=False, vp_type=None):
        view = FakeNamed(self, value * 10 + 1, Name=u"Plan " + number)
        sheet = FakeNamed(self, value * 10 + 2, SheetNumber=number,
                          Name=u"Grunnmynd")
        if vp_type is None:
            vp_type = self.shown if show else self.unshown
        return FakeViewport(self, value, view, sheet, vp_type, box_min,
                            hidden, offset, line, pinned)


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = [(v, v._offset, v._line, v.type, v.regenerated)
                      for v in self.doc.viewports]
        self.doc.log.append("start")

    def RollBack(self):
        for viewport, offset, line, vp_type, regenerated in self.saved:
            viewport._offset = offset
            viewport._line = line
            viewport.type = vp_type
            viewport.regenerated = regenerated
        self.doc.log.append("rollback")

    def Commit(self):
        self.doc.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        return iter(list(self.doc.viewports))


class Recorder(object):
    def __init__(self, source=None, ticks=(), selected=(), mode=BOTH,
                 match=False, closed_as=None):
        self.match = match
        self.closed_as = closed_as
        self.source = source
        self.ticks = list(ticks)
        self.selected = list(selected)
        self.mode = mode
        self.alerts = []
        self.printed = []
        self.pickers = []
        self.switches = []
        self.groups = []

    def show(self, items, multiselect=False, title=u"", button_name=u"",
             **kwargs):
        if isinstance(items, dict):
            # Grouped, like pyRevit 7.1: rows across every group, once.
            self.groups.append(list(items))
            seen, rows = set(), []
            for group in items.values():
                for row in group:
                    if id(row) not in seen:
                        seen.add(id(row))
                        rows.append(row)
            items = [row.item for row in rows]
        self.pickers.append((title, list(items)))
        if multiselect:
            picked = [i for i in items if any(t in i for t in self.ticks)]
            return picked or None
        for item in items:
            if self.source and self.source in item:
                return item
        return None

    def switch(self, options, message=u"", switches=None, **kwargs):
        """Like pyRevit: with switches, (option, {switch: bool})."""
        self.switches.append(list(options))
        self.switch_names = list(switches or [])
        if switches is None:
            return self.mode
        if self.mode is None and self.closed_as == "none":
            return None
        return (self.mode, dict((k, self.match if k == MATCH else v)
                                for k, v in dict(switches).items()))

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder):
    db = Namespace(
        FilteredElementCollector=FakeCollector,
        Viewport=FakeViewport,
        XYZ=FakeXYZ,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        BuiltInParameter=Namespace(VIEWPORT_ATTR_SHOW_LABEL=SHOW_LABEL),
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(Selection=Namespace(
        GetElementIds=lambda: [FakeId(v) for v in recorder.selected])))
    pyrevit.DB = db
    pyrevit.forms = Namespace(
        alert=recorder.alert,
        SelectFromList=Namespace(show=recorder.show),
        TemplateListItem=FakeRow,
        CommandSwitchWindow=Namespace(show=recorder.switch))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        sys.modules.pop("pyrevit", None)
    return recorder


def close(a, b, tolerance=1e-9):
    return a is not None and b is not None and \
        abs(a[0] - b[0]) < tolerance and abs(a[1] - b[1]) < tolerance


def site(**changes):
    """Three viewports on three sheets: boxes in different places, each
    with its own hidden amount and its own offset and line."""
    doc = FakeDoc(**changes)
    a = doc.place(1, u"A-101", (0.10, 0.12), (0.00, -0.02),
                  offset=(0.01, 0.00), line=180 * MM)
    b = doc.place(2, u"A-102", (0.13, 0.09), (0.03, -0.05),
                  offset=(0.00, 0.02), line=90 * MM)
    c = doc.place(3, u"A-103", (0.08, 0.15), (-0.02, 0.01),
                  offset=(0.04, -0.01), line=40 * MM)
    return doc, a, b, c


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------

check("title corner: bottom left from the minimum point",
      model.title_corner((1.5, 2.5)) == (1.5, 2.5))
check("title corner: nothing has none", model.title_corner(None) is None)
check("new offset: moves by the difference",
      model.new_offset((1.0, 1.0), (3.0, 5.0), (4.0, 2.0)) == (2.0, -2.0))
check("same length: within a tenth of a millimetre",
      model.same_length(1.0, 1.0 + 0.09 * MM) and
      not model.same_length(1.0, 1.0 + 0.11 * MM) and
      not model.same_length(1.0, None))


# --------------------------------------------------------------------------
# 1. Selected viewport, two targets, position and line
# --------------------------------------------------------------------------

doc, a, b, c = site()
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-102", u"A-103"], selected=[1]))
check("run: the selected viewport is the source, no source picker",
      not any(u"which title" in t for t, _ in rec.pickers), rec.pickers)
check("run: the target list leaves the source out",
      rec.pickers and not any(u"A-101" in i for i in rec.pickers[-1][1]))
check("run: the mode is asked", rec.switches == [[BOTH, POSITION]],
      rec.switches)
check("run: second title lands on the first's",
      close(b.true_corner(), want), (b.true_corner(), want))
check("run: third title lands on the first's",
      close(c.true_corner(), want), (c.true_corner(), want))
check("run: line lengths copied",
      b._line == a._line and c._line == a._line, (b._line, c._line))
check("run: the source is untouched",
      a._offset == (0.01, 0.00) and a._line == 180 * MM)
check("run: offsets differ, because the boxes do",
      abs(b._offset[0] - c._offset[0]) > 0.01)
check("run: committed once", doc.log == ["start", "commit"], doc.log)
check("run: the report says how far each moved and the line",
      u"moved" in rec.text() and u"line set" in rec.text(), rec.printed)
check("run: one table in one print_md",
      len(rec.printed) == 1 and rec.printed[0].count(u"| :--- |") == 1,
      rec.printed)

# 2. Position only: the lines are left alone.
doc, a, b, c = site()
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1],
                               mode=POSITION))
check("position only: title lands",
      close(b.true_corner(), want), (b.true_corner(), want))
check("position only: line untouched", b._line == 90 * MM, b._line)
check("position only: report does not claim a line",
      u"line set" not in rec.text())

# 3. Nothing selected: the source comes from the list.
doc, a, b, c = site()
want = c.true_corner()
rec = run_script(doc, Recorder(source=u"A-103", ticks=[u"A-101"]))
check("list: the source is picked from the list",
      any(u"which title" in t for t, _ in rec.pickers))
check("list: the first title moves onto the third's",
      close(a.true_corner(), want), (a.true_corner(), want))

# 4. Two viewports selected is not a choice: the list is shown.
doc, a, b, c = site()
rec = run_script(doc, Recorder(source=u"A-103", ticks=[u"A-101"],
                               selected=[1, 2]))
check("selection: two selected viewports fall back to the list",
      any(u"which title" in t for t, _ in rec.pickers))

# 5. Pinned and title switched off: skipped and said so, rest aligned.
doc = FakeDoc()
a = doc.place(1, u"A-101", (0.1, 0.1), (0.0, 0.0))
b = doc.place(2, u"A-102", (0.2, 0.1), (0.01, 0.0), pinned=True)
c = doc.place(3, u"A-103", (0.1, 0.2), (0.0, 0.01), show=False)
d = doc.place(4, u"A-104", (0.15, 0.15), (0.02, -0.01))
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-102", u"A-103", u"A-104"],
                               selected=[1]))
check("pinned: not changed, and reported",
      b._offset == (0.0, 0.0) and u"pinned" in rec.text())
check("no title: not changed, and reported",
      c._offset == (0.0, 0.0) and u"does not show a title" in rec.text())
check("the rest: still aligned", close(d.true_corner(), want))

# 6. A source without a title cannot be aligned to.
doc = FakeDoc()
a = doc.place(1, u"A-101", (0.1, 0.1), (0.0, 0.0), show=False)
b = doc.place(2, u"A-102", (0.2, 0.1), (0.0, 0.0))
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("source without title: said so, nothing changed",
      u"cannot be aligned to" in rec.text() and doc.log == [] and
      not rec.switches)

# 7. Every target skipped: no transaction, and said so.
doc = FakeDoc()
a = doc.place(1, u"A-101", (0.1, 0.1), (0.0, 0.0))
b = doc.place(2, u"A-102", (0.2, 0.1), (0.0, 0.0), pinned=True)
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("all skipped: no transaction, said so",
      doc.log == [] and u"None of the ticked" in rec.text(), rec.text())

# 8. If the titles do not land, nothing is kept.
doc, a, b, c = site(regenerate_works=False)
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("readback: a title that did not land rolls everything back",
      b._offset == (0.00, 0.02) and b._line == 90 * MM and
      doc.log == ["start", "rollback"], (b._offset, doc.log))
check("readback: and says so", u"did not land" in rec.text(), rec.text())

# 9. Revit rejecting the change.
doc, a, b, c = site(commit_status="RolledBack")
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("rejected: reported, not claimed as done",
      u"rejected" in rec.text() and not rec.printed, rec.text())

# 10. Cancelling at each step, and nothing to align.
doc, a, b, c = site()
rec = run_script(doc, Recorder(ticks=[], selected=[1]))
check("cancel targets: nothing changed, no transaction",
      doc.log == [] and b._offset == (0.00, 0.02))
doc, a, b, c = site()
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1], mode=None))
check("cancel mode: nothing changed, no target list",
      doc.log == [] and len(rec.pickers) == 0)
doc, a, b, c = site()
rec = run_script(doc, Recorder())
check("cancel source: nothing changed", doc.log == [])
doc = FakeDoc()
doc.place(1, u"A-101", (0.1, 0.1), (0.0, 0.0))
rec = run_script(doc, Recorder())
check("one placed view: said so", u"Fewer than two" in rec.text())

# 11. Match viewport type.
doc, a, b, c = site()
b.type = doc.other
c.type = doc.unshown
c.regenerated = (c._offset, c._line, c.type)
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-102", u"A-103"], selected=[1],
                               match=True))
check("type: the switch is offered, off by default",
      rec.switch_names == [MATCH], rec.switch_names)
check("type: both targets take the source's type",
      b.type is doc.shown and c.type is doc.shown, (b.type.Id.Value,
                                                    c.type.Id.Value))
check("type: a title hidden by its old type is aligned, not skipped",
      close(c.true_corner(), want) and u"does not show" not in rec.text(),
      rec.text())
check("type: the other lands too", close(b.true_corner(), want))
check("type: reported", rec.text().count(u"type set") == 2, rec.printed)
check("type: committed once", doc.log == ["start", "commit"], doc.log)

# 12. Switch off: types are left alone, a hidden title still skipped.
doc, a, b, c = site()
b.type = doc.other
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("type off: type untouched", b.type is doc.other and
      doc.type_changes == 0)
check("type off: no type claim", u"type set" not in rec.text())

# 13. Already the same type: not changed for nothing.
doc, a, b, c = site()
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1], match=True))
check("type same: no ChangeTypeId call", doc.type_changes == 0,
      doc.type_changes)

# 14. A type change Revit ignores: rolled back and said so.
doc, a, b, c = site(change_type_works=False)
b.type = doc.other
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1], match=True))
check("type ignored: rolled back and reported",
      doc.log == ["start", "rollback"] and u"type not set" in rec.text()
      and b._offset == (0.00, 0.02), (doc.log, rec.text()))

# 17. Sheet collections: the pickers get the dropdown, the run is the same.
doc, a, b, c = site()
doc.elements[8001] = FakeNamed(doc, 8001, Name=u"Arkitekt")
for vp in (a, b):
    doc.elements[vp.SheetId.Value].SheetCollectionId = FakeId(8001)
doc.elements[c.SheetId.Value].SheetCollectionId = FakeId(-1)
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-102"], source=u"A-101"))
check("collections: both pickers have the dropdown",
      rec.groups == [[u"[All sheets]", u"Arkitekt",
                      u"[Not in a collection]"],
                     [u"[All sheets]", u"Arkitekt",
                      u"[Not in a collection]"]], rec.groups)
check("collections: the ticked title still lands",
      close(b.true_corner(), want) and doc.log == ["start", "commit"],
      (doc.log, rec.text()))

# 16. Position only with the type switch: the title is read after the
# type change has been regenerated, with no line step to do it.
doc, a, b, c = site()
c.type = doc.unshown
c.regenerated = (c._offset, c._line, c.type)
want = a.true_corner()
rec = run_script(doc, Recorder(ticks=[u"A-103"], selected=[1],
                               mode=POSITION, match=True))
check("type, position only: hidden title shown and aligned",
      c.type is doc.shown and close(c.true_corner(), want) and
      c._line == 40 * MM and doc.log == ["start", "commit"],
      (doc.log, rec.text()))

# 15. Closing the dialog either way pyRevit reports it.
for closed in ("none", "tuple"):
    doc, a, b, c = site()
    rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1],
                                   mode=None, closed_as=closed))
    check("dialog closed ({0}): nothing changed".format(closed),
          doc.log == [] and not rec.pickers and
          u"raised" not in rec.text(), rec.text())

# --------------------------------------------------------------------------

check("harness: a title off by a millimetre is caught",
      not close((0.0, 0.0), (1 * MM, 0.0)))
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
