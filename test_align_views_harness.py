# -*- coding: utf-8 -*-
"""
Runs the real Align Views script against a mocked Revit.

The thing worth testing is where each crop region ends up on its sheet.
Each fake viewport's box is offset from its crop by its own amount, the
way tags and dimensions past the crop push a real viewport box out, so
aligning boxes (or box centres) instead of crops fails the checks.

The fake lies the way Revit is expected to:

- The projection to sheet transform follows the viewport's place as of
  the last regeneration, not as of the last SetBoxCenter.
- A view can be split into two crop regions.
- Views at different scales map the model differently onto the sheet.

Run outside Revit:

    python test_align_views_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Align Views.pushbutton", "script.py")

from avh_viewports import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


MM = 1.0 / 304.8


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


class Shift(object):
    """A transform that scales then translates, in plan."""

    def __init__(self, scale, dx, dy):
        self.scale, self.dx, self.dy = scale, dx, dy

    def OfPoint(self, point):
        return FakeXYZ(point.X * self.scale + self.dx,
                       point.Y * self.scale + self.dy, 0.0)


class FakeLine(object):
    def __init__(self, a, b):
        self.a, self.b = a, b

    def Tessellate(self):
        return [FakeXYZ(*self.a), FakeXYZ(*self.b)]


def rectangle(x0, y0, x1, y1):
    return [FakeLine((x0, y0), (x1, y0)), FakeLine((x1, y0), (x1, y1)),
            FakeLine((x1, y1), (x0, y1)), FakeLine((x0, y1), (x0, y0))]


class FakeView(object):
    def __init__(self, doc, value, name, crops, scale=100, active=True):
        self.Id = FakeId(value)
        self.Name = name
        self.crops = crops            # model rectangles, feet
        self.scale = scale
        self.CropBoxActive = active
        doc.elements[value] = self

    def GetModelToProjectionTransforms(self):
        return [Namespace(
            GetModelToProjectionTransform=lambda s=self.scale:
                Shift(1.0 / s, 0.0, 0.0),
            GetBoundary=lambda c=crop: rectangle(*c)) for crop in self.crops]


class FakeSheet(object):
    def __init__(self, doc, value, number, name):
        self.Id = FakeId(value)
        self.SheetNumber = number
        self.Name = name
        doc.elements[value] = self


class FakeViewport(object):
    """`box_offset` is how far the box centre sits from the crop's own
    centre, in sheet feet: annotations reaching past the crop."""

    def __init__(self, doc, value, view, sheet, centre, box_offset=(0, 0),
                 pinned=False):
        self.doc = doc
        self.Id = FakeId(value)
        self.ViewId = view.Id
        self.SheetId = sheet.Id
        self.view = view
        self.centre = centre
        self.regenerated_centre = centre
        self.box_offset = box_offset
        self.Pinned = pinned
        doc.viewports.append(self)
        doc.elements[value] = self

    def GetBoxCenter(self):
        return FakeXYZ(self.centre[0], self.centre[1])

    def SetBoxCenter(self, point):
        if self.Pinned:
            raise Exception("pinned")
        self.centre = (point.X, point.Y)

    def GetProjectionToSheetTransform(self):
        # Stale until the document regenerates.
        cx, cy = self.regenerated_centre
        bx, by = self.box_offset
        return Shift(1.0, cx - bx, cy - by)

    def crop_top_left(self):
        """Where the crop's top left truly is, now and regenerated."""
        to_sheet = Shift(1.0, self.centre[0] - self.box_offset[0],
                         self.centre[1] - self.box_offset[1])
        points = []
        for crop in self.view.crops:
            for x, y in ((crop[0], crop[3]), (crop[2], crop[1])):
                p = to_sheet.OfPoint(Shift(1.0 / self.view.scale, 0, 0)
                                     .OfPoint(FakeXYZ(x, y)))
                points.append((p.X, p.Y))
        return model.top_left(points)


class FakeDoc(object):
    def __init__(self, commit_status="Committed", regenerate_works=True):
        self.elements = {}
        self.viewports = []
        self.commit_status = commit_status
        self.regenerate_works = regenerate_works
        self.log = []

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def Regenerate(self):
        if self.regenerate_works:
            for viewport in self.viewports:
                viewport.regenerated_centre = viewport.centre

    def place(self, value, number, view_name, crops, centre, box_offset=(0, 0),
              scale=100, active=True, pinned=False):
        view = FakeView(self, value * 10 + 1, view_name, crops, scale, active)
        sheet = FakeSheet(self, value * 10 + 2, number, u"Grunnmynd")
        return FakeViewport(self, value, view, sheet, centre, box_offset,
                            pinned)


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = [(v, v.centre, v.regenerated_centre)
                      for v in self.doc.viewports]
        self.doc.log.append("start")

    def RollBack(self):
        for viewport, centre, regenerated in self.saved:
            viewport.centre = centre
            viewport.regenerated_centre = regenerated
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
    def __init__(self, source=None, ticks=(), selected=()):
        self.source = source
        self.ticks = list(ticks)
        self.selected = list(selected)
        self.alerts = []
        self.printed = []
        self.pickers = []

    def show(self, items, multiselect=False, title=u"", button_name=u"",
             **kwargs):
        self.pickers.append((title, list(items)))
        if multiselect:
            picked = [i for i in items if any(t in i for t in self.ticks)]
            return picked or None
        for item in items:
            if self.source and self.source in item:
                return item
        return None

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
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(Selection=Namespace(
        GetElementIds=lambda: [FakeId(v) for v in recorder.selected])))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=recorder.show))
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


# The same crop on every floor: 40 m by 25 m of model, at 1:100.
CROP = (0.0, 0.0, 40.0 / 0.3048, 25.0 / 0.3048)


def site(**changes):
    """Three plans on three sheets, each placed somewhere else, each with
    its own annotation overhang pushing the box off the crop."""
    doc = FakeDoc(**changes)
    a = doc.place(1, u"A-101", u"E01", [CROP], (1.20, 0.90), (0.00, 0.00))
    b = doc.place(2, u"A-102", u"E02", [CROP], (1.05, 0.70), (0.08, -0.03))
    # E03's crop is taller: top left and bottom left are not the same
    # alignment once the crops differ.
    c = doc.place(3, u"A-103", u"E03", [(0.0, 0.0, 40.0 / 0.3048,
                                         31.0 / 0.3048)],
                  (1.40, 1.10), (-0.05, 0.06))
    return doc, a, b, c


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------

check("top left: least X, most Y",
      model.top_left([(1, 1), (3, 1), (3, 4), (1, 4)]) == (1, 4))
check("top left: nothing has none", model.top_left([]) is None)
check("shift: from target to source",
      model.shift((5.0, 7.0), (2.0, 10.0)) == (3.0, -3.0))
check("landed: within a tenth of a millimetre",
      model.landed((0.0, 0.0), (0.09 * MM, 0.0)) and
      not model.landed((0.0, 0.0), (0.11 * MM, 0.0)))
check("label: sheet number, sheet name, view",
      model.label(u"A-101", u"Grunnmynd", u"E01") ==
      u"A-101  Grunnmynd  /  E01")


# --------------------------------------------------------------------------
# 1. Selected viewport, two targets
# --------------------------------------------------------------------------

doc, a, b, c = site()
want = a.crop_top_left()
rec = run_script(doc, Recorder(ticks=[u"A-102", u"A-103"], selected=[1]))
check("run: the selected viewport is the source, no source picker",
      not any(u"align to which" in t for t, _ in rec.pickers), rec.pickers)
check("run: the target list leaves the source out",
      rec.pickers and not any(u"A-101" in i for i in rec.pickers[-1][1]))
check("run: second view's crop lands on the first's",
      close(b.crop_top_left(), want), (b.crop_top_left(), want))
check("run: third view's crop lands on the first's",
      close(c.crop_top_left(), want), (c.crop_top_left(), want))
check("run: the source did not move", a.centre == (1.20, 0.90))
check("run: committed once", doc.log == ["start", "commit"], doc.log)
check("run: the boxes themselves are not aligned, the crops are",
      abs(b.centre[0] - a.centre[0]) > 0.01)
check("run: the report says how far each moved",
      u"moved" in rec.text() and u"mm across" in rec.text(), rec.printed)

# 2. Nothing selected: the source comes from the list.
doc, a, b, c = site()
want = b.crop_top_left()
rec = run_script(doc, Recorder(source=u"A-102", ticks=[u"A-101"]))
check("list: the source is picked from the list",
      any(u"align to which" in t for t, _ in rec.pickers))
check("list: the first view moves onto the second's crop",
      close(a.crop_top_left(), want), (a.crop_top_left(), want))

# 3. Two viewports selected is not a choice: the list is shown.
doc, a, b, c = site()
rec = run_script(doc, Recorder(source=u"A-103", ticks=[u"A-101"],
                               selected=[1, 2]))
check("selection: two selected viewports fall back to the list",
      any(u"align to which" in t for t, _ in rec.pickers))

# 4. Different scale: the crop corner still lands.
doc = FakeDoc()
a = doc.place(1, u"A-101", u"E01", [CROP], (1.2, 0.9))
b = doc.place(2, u"A-501", u"E01 1:50", [(0.0, 0.0, 10.0, 8.0)],
              (0.6, 0.5), (0.02, 0.02), scale=50)
want = a.crop_top_left()
rec = run_script(doc, Recorder(ticks=[u"A-501"], selected=[1]))
check("scale: a 1:50 view's crop corner lands on a 1:100 view's",
      close(b.crop_top_left(), want), (b.crop_top_left(), want))

# 5. A view split into two crop regions: the corner of both together.
doc = FakeDoc()
a = doc.place(1, u"A-101", u"E01", [CROP], (1.2, 0.9))
# The second region reaches higher than the first, so the corner is
# made of the first region's left and the second region's top.
split = [(0.0, 0.0, 20.0, 25.0 / 0.3048), (30.0, 10.0, 40.0 / 0.3048,
                                           30.0 / 0.3048)]
b = doc.place(2, u"A-102", u"E02", split, (1.0, 0.8), (0.03, 0.0))
want = a.crop_top_left()
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("split: the top left of all crop regions together lands",
      close(b.crop_top_left(), want), (b.crop_top_left(), want))

# 6. Pinned, cropless: skipped and said so, the rest still aligned.
doc = FakeDoc()
a = doc.place(1, u"A-101", u"E01", [CROP], (1.2, 0.9))
b = doc.place(2, u"A-102", u"E02", [CROP], (1.0, 0.8), pinned=True)
c = doc.place(3, u"A-103", u"E03", [CROP], (1.1, 0.7), active=False)
d = doc.place(4, u"A-104", u"E04", [CROP], (1.3, 1.0), (0.02, 0.01))
want = a.crop_top_left()
rec = run_script(doc, Recorder(ticks=[u"A-102", u"A-103", u"A-104"],
                               selected=[1]))
check("pinned: not moved, and reported",
      b.centre == (1.0, 0.8) and u"pinned" in rec.text())
check("no crop: not moved, and reported",
      c.centre == (1.1, 0.7) and u"crop region is off" in rec.text())
check("the rest: still aligned", close(d.crop_top_left(), want))

# 7. A source without a crop cannot be aligned to.
doc = FakeDoc()
a = doc.place(1, u"A-101", u"E01", [CROP], (1.2, 0.9), active=False)
b = doc.place(2, u"A-102", u"E02", [CROP], (1.0, 0.8))
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("source without crop: said so, nothing moved",
      u"cannot be aligned to" in rec.text() and b.centre == (1.0, 0.8)
      and doc.log == [])

# 8. If the corners do not land, nothing is kept.
doc, a, b, c = site(regenerate_works=False)
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("readback: a corner that did not land rolls everything back",
      b.centre == (1.05, 0.70) and doc.log == ["start", "rollback"],
      (b.centre, doc.log))
check("readback: and says so",
      u"did not land" in rec.text(), rec.text())

# 9. Revit rejecting the change.
doc, a, b, c = site(commit_status="RolledBack")
rec = run_script(doc, Recorder(ticks=[u"A-102"], selected=[1]))
check("rejected: reported, not claimed as done",
      u"rejected" in rec.text() and not rec.printed, rec.text())

# 10. Cancelling and nothing to align.
doc, a, b, c = site()
rec = run_script(doc, Recorder(ticks=[], selected=[1]))
check("cancel targets: nothing moved, no transaction",
      doc.log == [] and b.centre == (1.05, 0.70))
doc, a, b, c = site()
rec = run_script(doc, Recorder())
check("cancel source: nothing moved", doc.log == [])
doc = FakeDoc()
doc.place(1, u"A-101", u"E01", [CROP], (1.2, 0.9))
rec = run_script(doc, Recorder())
check("one placed view: said so", u"Fewer than two" in rec.text())

# --------------------------------------------------------------------------

check("harness: a corner off by a millimetre is caught",
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
