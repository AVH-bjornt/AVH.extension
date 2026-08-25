# -*- coding: utf-8 -*-
"""
Runs the real Zoom to Selection script against a mocked Revit.

The thing worth testing here is the rectangle it asks Revit to zoom to,
because a zoom to the wrong box looks like the button not working and
tells you nothing about why. Every scenario checks the numbers.

There is no transaction anywhere in this tool, and one check asserts
that: it changes what you look at, not the model.

Run outside Revit:

    python test_zoom_selection_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Selection.panel",
                      "Zoom To Selection.pushbutton", "script.py")

from avh_selection import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-9):
    return abs(a - b) < tolerance


MM = 1.0 / model.MM_PER_FOOT


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X = x
        self.Y = y
        self.Z = z

    def tuple(self):
        return (self.X, self.Y, self.Z)


class FakeBox(object):
    def __init__(self, low, high):
        self.Min = FakeXYZ(*low)
        self.Max = FakeXYZ(*high)


class FakeElement(object):
    def __init__(self, view_box=None, model_box=None, raises=False):
        self.Id = FakeId(id(self) % 100000)
        self.view_box = view_box
        self.model_box = model_box
        self.raises = raises

    def get_BoundingBox(self, view):
        if self.raises:
            raise Exception("no geometry on this element")
        box = self.view_box if view is not None else self.model_box
        if box is None:
            return None
        return FakeBox(*box)


class FakeView(object):
    def __init__(self, name=u"Level 1"):
        self.Id = FakeId(1)
        self.Name = name


class FakeUIView(object):
    def __init__(self, view_id, raises=False):
        self.ViewId = view_id
        self.raises = raises
        self.zoomed = None

    def ZoomAndCenterRectangle(self, low, high):
        if self.raises:
            raise Exception("this view cannot be zoomed")
        self.zoomed = (low.tuple(), high.tuple())


class FakeSelection(object):
    def __init__(self, ids, raises=False):
        self.ids = list(ids)
        self.raises = raises

    def GetElementIds(self):
        if self.raises:
            raise Exception("the selection is unavailable")
        return list(self.ids)


class FakeDocument(object):
    def __init__(self, elements, view=None):
        self.by_id = dict((element.Id, element) for element in elements)
        self.ActiveView = view if view is not None else FakeView()

    def GetElement(self, element_id):
        return self.by_id.get(element_id)


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


# A transaction here would be a bug: this tool changes the camera, not
# the model. Anything that touches it trips this.
TRANSACTIONS = []


class ForbiddenTransaction(object):
    def __init__(self, doc, name):
        TRANSACTIONS.append(name)

    def Start(self):
        TRANSACTIONS.append("start")


def run_script(doc, selected=(), ui_views=None, no_active_view=False,
               selection_raises=False):
    del TRANSACTIONS[:]
    recorder = Recorder()

    view = doc.ActiveView
    if no_active_view:
        doc.ActiveView = None
        view = None

    if ui_views is None:
        ui_views = [FakeUIView(view.Id)] if view is not None else []

    uidoc = Namespace(
        Selection=FakeSelection([element.Id for element in selected],
                                raises=selection_raises),
        GetOpenUIViews=lambda: list(ui_views),
    )

    db = Namespace(XYZ=FakeXYZ, Transaction=ForbiddenTransaction)

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=uidoc)
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert)
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit

    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)

    recorder.ui_views = ui_views
    return recorder


def zoomed(recorder):
    for ui_view in recorder.ui_views:
        if ui_view.zoomed is not None:
            return ui_view.zoomed
    return None


# --------------------------------------------------------------------------
# The arithmetic, no Revit at all
# --------------------------------------------------------------------------

check("combine: nothing gives nothing", model.combine([]) is None)
check("combine: a single box is itself",
      model.combine([((0, 0, 0), (1, 2, 3))]) == ((0, 0, 0), (1, 2, 3)))
check("combine: two boxes give the box around both",
      model.combine([((0, 0, 0), (1, 1, 1)), ((5, -2, 3), (6, -1, 4))])
      == ((0, -2, 0), (6, 1, 4)))
check("combine: an empty box is skipped",
      model.combine([(None, None), ((0, 0, 0), (1, 1, 1))])
      == ((0, 0, 0), (1, 1, 1)))

big = model.pad(((0, 0, 0), (100, 100, 100)))
check("pad: a large box grows by the fraction",
      close(big[0][0], -15.0) and close(big[1][0], 115.0),
      str(big))
check("pad: the margin is equal on every axis",
      close(big[1][1] - 100.0, big[1][2] - 100.0))

tiny = model.pad(((0, 0, 0), (0.05, 0.05, 0.05)))
check("pad: a tiny element still gets the minimum margin",
      close(tiny[0][0], -model.MIN_MARGIN_MM * MM), str(tiny))
check("pad: and the minimum beats the fraction there",
      model.MIN_MARGIN_MM * MM > 0.05 * model.PADDING)
check("pad: nothing stays nothing", model.pad(None) is None)

check("describe: nothing to say when everything was in view",
      model.describe(3, 0) == u"")
check("describe: some out of view is counted",
      u"2 of 3" in model.describe(3, 2))
check("describe: all out of view says so plainly",
      u"None of the 3" in model.describe(3, 3))


# --------------------------------------------------------------------------
# 1. The ordinary click
# --------------------------------------------------------------------------

element = FakeElement(view_box=((0, 0, 0), (10, 10, 10)))
doc = FakeDocument([element])
recorder = run_script(doc, selected=[element])
box = zoomed(recorder)

check("zoom: the view was zoomed", box is not None)
# A 10 ft cube is about 3 m, and 15 percent of that is 457 mm, so the
# 500 mm floor is what applies here. Anything under 3.33 m is in the
# same position, which is most things anyone selects.
check("zoom: to the padded selection box, floor margin applied",
      box is not None and close(box[0][0], -model.MIN_MARGIN_MM * MM) and
      close(box[1][0], 10.0 + model.MIN_MARGIN_MM * MM), str(box))
check("zoom: no alert on the happy path", not recorder.alerts,
      u" | ".join(recorder.alerts))
check("zoom: nothing printed when there is nothing to say",
      not recorder.printed)
check("zoom: no transaction was ever opened", not TRANSACTIONS,
      str(TRANSACTIONS))

# Above about 3.33 m the fraction takes over from the floor.
big_element = FakeElement(view_box=((0, 0, 0), (100, 100, 100)))
doc = FakeDocument([big_element])
recorder = run_script(doc, selected=[big_element])
box = zoomed(recorder)
check("zoom: a large selection uses the fraction, not the floor",
      box is not None and close(box[0][0], -15.0), str(box))

# Several elements zoom to one box around all of them.
first = FakeElement(view_box=((0, 0, 0), (1, 1, 1)))
second = FakeElement(view_box=((20, 20, 0), (21, 21, 1)))
doc = FakeDocument([first, second])
recorder = run_script(doc, selected=[first, second])
box = zoomed(recorder)
check("zoom: several elements give one box around them all",
      box is not None and box[0][0] < 0 and box[1][0] > 21, str(box))


# --------------------------------------------------------------------------
# 2. Nothing to zoom to
# --------------------------------------------------------------------------

doc = FakeDocument([])
recorder = run_script(doc, selected=[])
check("empty selection: says select something first",
      u"Select something first" in recorder.text())
check("empty selection: nothing was zoomed", zoomed(recorder) is None)

element = FakeElement(raises=True)
doc = FakeDocument([element])
recorder = run_script(doc, selected=[element])
check("no geometry anywhere: says there is nowhere to zoom",
      u"nowhere to zoom" in recorder.text())
check("no geometry anywhere: nothing was zoomed", zoomed(recorder) is None)

element = FakeElement(view_box=((0, 0, 0), (1, 1, 1)))
doc = FakeDocument([element])
recorder = run_script(doc, selected=[element], ui_views=[])
check("view not open: explained rather than silent",
      u"cannot be zoomed" in recorder.text())

element = FakeElement(view_box=((0, 0, 0), (1, 1, 1)))
doc = FakeDocument([element])
stubborn = FakeUIView(doc.ActiveView.Id, raises=True)
recorder = run_script(doc, selected=[element], ui_views=[stubborn])
check("zoom refused: Revit's own words reach the user",
      u"would not zoom" in recorder.text())


# --------------------------------------------------------------------------
# 3. Elements the view does not draw
# --------------------------------------------------------------------------

hidden = FakeElement(view_box=None, model_box=((50, 50, 0), (51, 51, 1)))
doc = FakeDocument([hidden])
recorder = run_script(doc, selected=[hidden])
box = zoomed(recorder)

check("not in view: the model position is used instead",
      box is not None and box[0][0] < 50 and box[1][0] > 51, str(box))
check("not in view: and the report says so",
      u"None of the 1" in recorder.text(), recorder.text())

visible = FakeElement(view_box=((0, 0, 0), (1, 1, 1)))
hidden = FakeElement(view_box=None, model_box=((5, 5, 0), (6, 6, 1)))
doc = FakeDocument([visible, hidden])
recorder = run_script(doc, selected=[visible, hidden])
check("mixed: the count of out of view elements is right",
      u"1 of 2" in recorder.text(), recorder.text())
check("mixed: both still contribute to the box",
      zoomed(recorder)[1][0] > 6, str(zoomed(recorder)))

# The view box wins where both exist, because it is what is on screen.
element = FakeElement(view_box=((0, 0, 0), (1, 1, 1)),
                      model_box=((100, 100, 100), (200, 200, 200)))
doc = FakeDocument([element])
recorder = run_script(doc, selected=[element])
check("view box is preferred over the model box",
      zoomed(recorder)[1][0] < 50, str(zoomed(recorder)))
check("view box preferred: nothing reported, nothing was wrong",
      not recorder.printed)


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
