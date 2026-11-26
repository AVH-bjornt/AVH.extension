# -*- coding: utf-8 -*-
"""
Runs the real Batch Sheets script against a mocked Revit.

The window (lib/avh_sheets/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person
would. Everything the window shows comes from the `evaluate` callable
the script passes it, and that is exercised here.

The fake lies the way Revit is expected to:

- `ViewSheet.Create` makes a sheet with a number of Revit's own choosing.
- Setting a sheet number another sheet has raises, ignoring case.
- Forbidden characters raise.
- A rolled back transaction, or a commit that comes back RolledBack,
  removes every sheet made in it.

Run outside Revit:

    python test_batch_sheets_harness.py
"""

import io
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Views.panel", "Sheets.stack",
                      "Batch Sheets.pushbutton", "script.py")

import avh_sheets                       # noqa: E402
from avh_sheets import model            # noqa: E402
from avh_rename import model as naming  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


INVALID = FakeId(-1)


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


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
        return [FakeXYZ(self.a[0], self.a[1], 0.0),
                FakeXYZ(self.b[0], self.b[1], 0.0)]


def rectangle(x0, y0, x1, y1):
    return [FakeLine((x0, y0), (x1, y0)), FakeLine((x1, y0), (x1, y1)),
            FakeLine((x1, y1), (x0, y1)), FakeLine((x0, y1), (x0, y0))]


class FakeView(object):
    """`crops`: model rectangles in feet; `scale`: the view's scale."""

    def __init__(self, doc, value, name, view_type, template=False,
                 crops=((0.0, 0.0, 30.0, 20.0),), scale=100, crop=True):
        self.Id = FakeId(value)
        self.Name = name
        self.ViewType = view_type
        self.IsTemplate = template
        self.crops = crops
        self.scale = scale
        self.CropBoxActive = crop
        doc.views.append(self)

    def GetModelToProjectionTransforms(self):
        return [Namespace(
            GetModelToProjectionTransform=lambda s=self.scale:
                Shift(1.0 / s, 0.0, 0.0),
            GetBoundary=lambda c=c: rectangle(*c)) for c in self.crops]


class FakeViewport(object):
    """`box_offset`: how far the box centre sits from where the crop is
    drawn from, in sheet feet: annotations reaching past the crop."""

    def __init__(self, doc, sheet_id, view_id, point, box_offset=(0.0, 0.0),
                 regenerated=False):
        self.doc = doc
        self.SheetId = sheet_id
        self.ViewId = view_id
        self.point = point if point is not None else FakeXYZ(0.0, 0.0, 0.0)
        self.box_offset = box_offset
        # Revit works the sheet transform out on regeneration.
        self.regenerated = ((self.point.X, self.point.Y) if regenerated
                            else None)
        doc.viewports.append(self)

    def GetBoxCenter(self):
        return FakeXYZ(self.point.X, self.point.Y, 0.0)

    def SetBoxCenter(self, point):
        self.point = FakeXYZ(point.X, point.Y, 0.0)

    def GetProjectionToSheetTransform(self):
        if self.regenerated is None:
            raise Exception("The viewport has not been regenerated.")
        return Shift(1.0, self.regenerated[0] - self.box_offset[0],
                     self.regenerated[1] - self.box_offset[1])

    def crop_top_left(self):
        """Where the crop's top left truly is now."""
        view = [v for v in self.doc.views if v.Id == self.ViewId][0]
        points = []
        for crop in view.crops:
            for x, y in ((crop[0], crop[3]), (crop[2], crop[1])):
                points.append((x / view.scale + self.point.X -
                               self.box_offset[0],
                               y / view.scale + self.point.Y -
                               self.box_offset[1]))
        return (min(p[0] for p in points), max(p[1] for p in points))

    @staticmethod
    def CanAddViewToSheet(doc, sheet_id, view_id):
        if view_id in doc.unplaceable:
            return False
        return not any(v.ViewId == view_id for v in doc.viewports)

    @staticmethod
    def Create(doc, sheet_id, view_id, point):
        if not FakeViewport.CanAddViewToSheet(doc, sheet_id, view_id):
            raise Exception("The view cannot be added to the sheet.")
        if doc.viewport_none:
            return None
        return FakeViewport(doc, sheet_id, view_id, point)


class FakeSymbol(object):
    def __init__(self, doc, value, family, name, active=True,
                 size=(2.0, 1.0)):
        self.size = size
        self.Id = FakeId(value)
        self.FamilyName = family
        self.Name = name
        self.IsActive = active
        doc.symbols.append(self)

    def Activate(self):
        self.IsActive = True


class FakeTitleBlock(object):
    def __init__(self, type_id, doc=None, owner=None, size=(2.0, 1.0)):
        self.type_id = type_id
        self.doc = doc
        self.owner = owner
        self.size = size

    def get_BoundingBox(self, view):
        # Like Revit: a title block placed since the last regenerate has
        # no size yet.
        if self.doc is not None and self.doc.dirty:
            return None
        return Namespace(Min=FakeXYZ(-self.size[0], 0.0, 0.0),
                         Max=FakeXYZ(0.0, self.size[1], 0.0))

    def GetTypeId(self):
        return self.type_id


class FakeSheetCollection(object):
    created = 0

    def __init__(self, doc, name):
        FakeSheetCollection.created += 1
        self.Id = FakeId(5000 + FakeSheetCollection.created)
        self.Name = name
        doc.collections.append(self)

    @staticmethod
    def Create(doc, name):
        if any(c in name for c in naming.FORBIDDEN):
            raise Exception("Name contains prohibited characters")
        if any(c.Name.lower() == name.lower() for c in doc.collections):
            raise Exception("Sheet collection name is already in use")
        return FakeSheetCollection(doc, name)


class FakeViewSheet(object):
    created = 0

    def __init__(self, doc, number, name, type_id):
        self.doc = doc
        FakeViewSheet.created += 1
        self.Id = FakeId(1000 + FakeViewSheet.created)
        self._number = number
        self._name = name
        self.type_id = type_id
        self._collection = INVALID
        self.ViewType = u"DrawingSheet"
        self.IsTemplate = False
        doc.sheets.append(self)

    @staticmethod
    def Create(doc, type_id):
        if type_id != INVALID:
            symbol = [s for s in doc.symbols if s.Id == type_id][0]
            if not symbol.IsActive:
                raise Exception("The symbol is not active.")
        auto = u"auto-{0}".format(FakeViewSheet.created + 1)
        sheet = FakeViewSheet(doc, auto, u"Unnamed", type_id)
        if type_id != INVALID:
            doc.placed.append(FakeTitleBlock(type_id, doc, sheet.Id,
                                             symbol.size))
            doc.dirty = True
        return sheet

    @property
    def SheetNumber(self):
        return self._number

    @SheetNumber.setter
    def SheetNumber(self, number):
        if any(c in number for c in naming.FORBIDDEN):
            raise Exception("Sheet number contains prohibited characters")
        if number in self.doc.refuse:
            raise Exception("Revit says no to " + number)
        if any(s is not self and s._number.lower() == number.lower()
               for s in self.doc.sheets):
            raise Exception("Sheet number is already in use")
        self._number = number.upper() if self.doc.mangle else number

    @property
    def SheetCollectionId(self):
        return self._collection

    @SheetCollectionId.setter
    def SheetCollectionId(self, collection_id):
        if collection_id != INVALID and not any(
                c.Id == collection_id for c in self.doc.collections):
            raise Exception("Not a sheet collection")
        if not self.doc.ignore_collection:
            self._collection = collection_id

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, name):
        if any(c in name for c in naming.FORBIDDEN):
            raise Exception("Name contains prohibited characters")
        self._name = name


class FakeDoc(object):
    def __init__(self, commit_status="Committed", family=False,
                 refuse=(), mangle=False, has_collections=True,
                 ignore_collection=False):
        self.sheets = []
        self.collections = []
        self.views = []
        self.viewports = []
        self.selected = []
        self.unplaceable = set()
        self.viewport_none = False
        self.regenerate_works = True
        self.regenerations_left = None
        self.dirty = False
        self.has_collections = has_collections
        self.ignore_collection = ignore_collection
        self.symbols = []
        self.placed = []
        self.commit_status = commit_status
        self.IsFamilyDocument = family
        self.refuse = set(refuse)
        self.mangle = mangle
        self.log = []

    def sheet(self, number, name, symbol=None):
        sheet = FakeViewSheet(self, number, name,
                              symbol.Id if symbol else INVALID)
        if symbol is not None:
            self.placed.append(FakeTitleBlock(symbol.Id))
        return sheet

    def Regenerate(self):
        self.dirty = False
        if self.regenerate_works:
            if self.regenerations_left is not None:
                if self.regenerations_left <= 0:
                    return
                self.regenerations_left -= 1
            for viewport in self.viewports:
                viewport.regenerated = (viewport.point.X, viewport.point.Y)

    def GetElement(self, element_id):
        for element in self.views + self.sheets + self.collections:
            if element.Id == element_id:
                return element
        return None

    def numbers(self):
        return sorted(s._number for s in self.sheets)


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc

    def Start(self):
        self.saved = (list(self.doc.sheets),
                      [(s, s._number, s._name, s._collection)
                       for s in self.doc.sheets],
                      [(s, s.IsActive) for s in self.doc.symbols],
                      list(self.doc.collections),
                      list(self.doc.placed), list(self.doc.viewports),
                      [(v, v.point, v.regenerated)
                       for v in self.doc.viewports])
        self.doc.log.append("start")

    def RollBack(self):
        (sheets, values, symbols, collections, placed,
         viewports, spots) = self.saved
        for viewport, point, regenerated in spots:
            viewport.point, viewport.regenerated = point, regenerated
        self.doc.sheets[:] = sheets
        self.doc.collections[:] = collections
        self.doc.placed[:] = placed
        self.doc.viewports[:] = viewports
        for sheet, number, name, collection in values:
            sheet._number, sheet._name = number, name
            sheet._collection = collection
        for symbol, active in symbols:
            symbol.IsActive = active
        self.doc.log.append("rollback")

    def Commit(self):
        self.doc.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeCollector(object):
    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.view_id = view_id
        self.category = None

    def OfCategory(self, category):
        self.category = category
        return self

    def WhereElementIsElementType(self):
        return iter(list(self.doc.symbols))

    def WhereElementIsNotElementType(self):
        return iter([b for b in self.doc.placed
                     if self.view_id is None or b.owner == self.view_id])

    def OfClass(self, cls):
        if cls is FakeSheetCollection:
            return iter(list(self.doc.collections))
        if cls is FakeViewport:
            return iter(list(self.doc.viewports))
        if cls is FakeView:
            # Revit's View class includes sheets.
            return iter(list(self.doc.views) + list(self.doc.sheets))
        return iter(list(self.doc.sheets))


class Recorder(object):
    """`answer(labels, chosen, number, count, evaluate)` plays the person
    at the window; returning None is pressing Cancel."""

    def __init__(self, answer=None, ticks=None, align_to=None):
        self.answer = answer
        self.ticks = ticks
        self.align_to = align_to
        self.align_offered = None
        self.picked_from = None
        self.alerts = []
        self.printed = []
        self.window = None

    def ask(self, labels, chosen, number, count, evaluate, collections,
            views=None, names=None):
        self.window = Namespace(labels=labels, chosen=chosen, number=number,
                                count=count, evaluate=evaluate,
                                collections=collections, views=views,
                                names=names)
        if self.answer is None:
            return None
        return self.answer(labels, chosen, number, count, evaluate)

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def pick(self, labels, **kwargs):
        if u"align" in kwargs.get("title", u""):
            self.align_offered = list(labels)
            if self.align_to is None:
                return None
            return [label for label in labels if self.align_to in label]
        self.picked_from = list(labels)
        if self.ticks is None:
            return None
        return [label for label in labels
                if any(tick in label for tick in self.ticks)]

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder, shift=False):
    db = Namespace(
        FilteredElementCollector=FakeCollector,
        BuiltInCategory=Namespace(OST_TitleBlocks="OST_TitleBlocks"),
        BuiltInParameter=Namespace(SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM"),
        ViewSheet=FakeViewSheet,
        View=FakeView,
        Viewport=FakeViewport,
        XYZ=FakeXYZ,
        ElementId=Namespace(InvalidElementId=INVALID),
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
    )
    if doc.has_collections:
        db.SheetCollection = FakeSheetCollection
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(
        Selection=Namespace(GetElementIds=lambda: list(doc.selected))))
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=recorder.pick))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    window = types.ModuleType("avh_sheets.window")
    window.ask = recorder.ask
    sys.modules["pyrevit"] = pyrevit
    sys.modules["avh_sheets.window"] = window
    avh_sheets.window = window
    try:
        runpy.run_path(SCRIPT, run_name="__main__",
                       init_globals={"__shiftclick__": shift})
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        sys.modules.pop("pyrevit", None)
        sys.modules.pop("avh_sheets.window", None)
        del avh_sheets.window
    return recorder


def batch(count, start=None, names=None, titleblock=None,
          number_prefix=None, number_suffix=None,
          name_prefix=u"", name_suffix=u"", collection=None):
    """A person who keeps the suggested numbering unless told otherwise."""
    def answer(labels, chosen, number, _count, evaluate):
        fields = naming.numbers(count, start or number[1])
        return dict(
            titleblock=chosen if titleblock is None else titleblock,
            values=(number[0] if number_prefix is None else number_prefix,
                    fields,
                    number[2] if number_suffix is None else number_suffix,
                    name_prefix,
                    names or [u"Unnamed"] * count,
                    name_suffix),
            collection=collection)
    return answer


def model_with_sheets(**kwargs):
    doc = FakeDoc(**kwargs)
    a3 = FakeSymbol(doc, 11, u"AVH Titill", u"A3", size=(1.38, 0.97))
    a1 = FakeSymbol(doc, 12, u"AVH Titill", u"A1", size=(2.76, 1.95))
    # A3 is the most used, and sorts second, so "most used" and "first
    # in the list" give different answers.
    doc.sheet(u"A-101", u"Grunnmynd 1", a3)
    doc.sheet(u"A-102", u"Grunnmynd 2", a3)
    doc.sheet(u"A-901", u"Deili", a1)
    doc.sheet(u"A-9", u"Yfirlit", a3)
    return doc, a1, a3


HAED = u"hæð"

# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------

check("suggest: continues after the highest, naturally sorted",
      model.suggest([u"A-101", u"A-105", u"A-9"]) == (u"A-", u"106", u""))
check("suggest: keeps the width",
      model.suggest([u"009"]) == (u"", u"010", u""))
check("suggest: keeps a suffix",
      model.suggest([u"A-101a"]) == (u"A-", u"102", u"a"))
check("suggest: no sheets starts at 101",
      model.suggest([]) == (u"", u"101", u""))
check("suggest: no numbered sheets starts at 101",
      model.suggest([u"Forsida"]) == (u"", u"101", u""))
check("count: a number from 1 to 200",
      model.count_problem(u"5") == u"" and model.count_problem(u"0") and
      model.count_problem(u"201") and model.count_problem(u"tveir"))

rows = model.plan(u"A-", [u"101", u"103", u"103"], u"", u"", [u"a", u"b",
                                                               u"c"], u"",
                  taken_numbers=[u"a-101"])
check("plan: a number already taken is refused, ignoring case",
      u"already" in rows[0].problem, rows[0].problem)
check("plan: two new sheets on one number are both refused",
      u"both" in rows[1].problem and u"both" in rows[2].problem,
      [r.problem for r in rows])
rows = model.plan(u"A:", [u"1"], u"", u"", [u"x"], u"")
check("plan: a forbidden character in the number",
      rows[0].problem.startswith(u"number"), rows[0].problem)
rows = model.plan(u"A-", [u"1"], u"", u"", [u""], u"")
check("plan: an empty name", rows[0].problem.startswith(u"name"),
      rows[0].problem)
rows = model.plan(u"A-", [u"1", u"2"], u"", u"Grunnmynd ", [u"1. " + HAED,
                                                            u"2. " + HAED],
                  u"")
check("plan: names may repeat and carry Icelandic",
      not model.blocking(rows) and rows[1].name == u"Grunnmynd 2. " + HAED)
check("resize: pads and cuts",
      model.resize([u"a", u"b"], 3, u"z") == [u"a", u"b", u"z"] and
      model.resize([u"a", u"b"], 1, u"z") == [u"a"])
check("fields: a start value numbers the names like the numbers",
      model.fields(3, u"1", [u"x"], u"Unnamed") == [u"1", u"2", u"3"] and
      model.fields(2, u"01", [], u"Unnamed") == [u"01", u"02"] and
      model.fields(2, u"A", [], u"Unnamed") == [u"A", u"B"])
check("fields: no start value keeps what was typed, padded",
      model.fields(3, u"", [u"Snid A", u"Snid B"], u"Unnamed") ==
      [u"Snid A", u"Snid B", u"Unnamed"])
check("fields: a start that cannot count keeps what was typed",
      model.fields(2, u"Grunnmynd", [u"a", u"b", u"c"], u"Unnamed") ==
      [u"a", u"b"])
check("counts_from: numbers and single letters only",
      model.counts_from(u"1") and model.counts_from(u"A") and
      not model.counts_from(u"") and not model.counts_from(u"1a"))
rows = model.plan(u"A-", model.fields(3, u"101", [], u""), u"",
                  u"Grunnmynd ", model.fields(3, u"1", [], u"Unnamed"),
                  u". " + HAED)
check("plan: numbered names come out in order",
      [r.name for r in rows] == [u"Grunnmynd 1. " + HAED,
                                 u"Grunnmynd 2. " + HAED,
                                 u"Grunnmynd 3. " + HAED] and
      not model.blocking(rows), [r.name for r in rows])

# The window cannot run here; check its source wires the name start up the
# same way as the number start.
WINDOW_SRC = io.open(os.path.join(HERE, "lib", "avh_sheets", "window.py"),
                     encoding="utf-8").read()
check("window: a name start box, renumbering on change like the number's",
      u'x:Name="name_start_box"' in WINDOW_SRC and
      u"self.name_start_box.TextChanged += self.on_start" in WINDOW_SRC)
check("window: name fields come from model.fields with the name start",
      u"model.fields(count, to_text(self.name_start_box.Text)" in WINDOW_SRC)


# --------------------------------------------------------------------------
# 1. Three sheets, numbering carried on, the usual title block
# --------------------------------------------------------------------------

doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(3, names=[u"Snid A", u"Snid B",
                                               u"Snid C"])))
check("window: the title blocks are offered, then none",
      rec.window and rec.window.labels == [u"AVH Titill : A1",
                                           u"AVH Titill : A3",
                                           u"(no title block)"],
      rec.window and rec.window.labels)
check("window: the most used title block is chosen first",
      rec.window and rec.window.chosen == 1, rec.window and
      rec.window.chosen)
check("window: numbering carries on from the highest number",
      rec.window and rec.window.number == (u"A-", u"902", u""),
      rec.window and rec.window.number)
new = [s for s in doc.sheets if s.Id.Value > 1004]
check("made: three sheets, numbered on from A-901",
      [s.SheetNumber for s in new] == [u"A-902", u"A-903", u"A-904"],
      [s.SheetNumber for s in new])
check("made: named per row", [s.Name for s in new] ==
      [u"Snid A", u"Snid B", u"Snid C"])
check("made: with the chosen title block",
      all(s.type_id == a3.Id for s in new))
check("made: one transaction, committed",
      doc.log == ["start", "commit"], doc.log)
check("made: the report lists them",
      rec.printed and u"A-904" in rec.printed[0] and u"3 sheet(s)" in
      rec.printed[0] and u"AVH Titill : A3" in rec.printed[0], rec.printed)

# 2. Another title block, and none.
doc, a1, a3 = model_with_sheets()
run_script(doc, Recorder(batch(1, titleblock=0, number_prefix=u"B-",
                               start=u"1")))
check("title block: the one picked is used, not the usual one",
      doc.sheets[-1].type_id == a1.Id and doc.sheets[-1].SheetNumber ==
      u"B-1")
doc, a1, a3 = model_with_sheets()
run_script(doc, Recorder(batch(1, titleblock=2, number_prefix=u"X")))
check("title block: none makes a sheet without one",
      doc.sheets[-1].type_id == INVALID)

# 3. A title block type not yet active is activated.
doc = FakeDoc()
idle = FakeSymbol(doc, 11, u"AVH Titill", u"A0", active=False)
rec = run_script(doc, Recorder(batch(2)))
check("inactive: activated and used",
      idle.IsActive and len(doc.sheets) == 2 and
      doc.sheets[0].SheetNumber == u"101", (doc.numbers(), rec.text()))

# 4. A number another sheet has: shown to the window, refused by the script.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(2, start=u"101")))
shown = rec.window.evaluate(u"A-", [u"101", u"102"], u"", u"", [u"x", u"y"],
                            u"") if rec.window else []
check("taken: the window would show both in red",
      shown and all(u"already" in r.problem for r in shown))
check("taken: no sheet made, no transaction, said why",
      len(doc.sheets) == 4 and doc.log == [] and u"already" in rec.text(),
      rec.text())

# 5. Revit refusing one: none are made.
doc, a1, a3 = model_with_sheets(refuse=[u"A-903"])
rec = run_script(doc, Recorder(batch(3)))
check("refused: no sheet left behind",
      doc.numbers() == [u"A-101", u"A-102", u"A-9", u"A-901"],
      doc.numbers())
check("refused: rolled back, and the sheet named",
      doc.log == ["start", "rollback"] and u"A-903" in rec.text() and
      u"no sheet was made" in rec.text(), (doc.log, rec.text()))

# 6. A number that came back different.
doc, a1, a3 = model_with_sheets(mangle=True)
rec = run_script(doc, Recorder(batch(1, number_prefix=u"b-")))
check("readback: rolled back and said so",
      len(doc.sheets) == 4 and u"came back" in rec.text(), rec.text())

# 7. The commit coming back rolled back.
doc, a1, a3 = model_with_sheets(commit_status="RolledBack")
rec = run_script(doc, Recorder(batch(2)))
check("rejected: reported, not claimed as done",
      u"rejected" in rec.text() and not rec.printed and
      len(doc.sheets) == 4, rec.text())

# 8. Cancel, a family document, a model with no sheets or title blocks.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(None))
check("cancel: nothing made, no transaction",
      len(doc.sheets) == 4 and doc.log == [] and not rec.alerts)
doc = FakeDoc(family=True)
rec = run_script(doc, Recorder(batch(1)))
check("family document: said so, no window",
      u"family document" in rec.text() and rec.window is None)
doc = FakeDoc()
rec = run_script(doc, Recorder(batch(1)))
check("empty model: only 'no title block' offered, starts at 101",
      rec.window and rec.window.labels == [u"(no title block)"] and
      rec.window.number == (u"", u"101", u""))
check("empty model: the sheet is made",
      doc.numbers() == [u"101"], doc.numbers())

# 9. Forbidden characters stop the run before Revit is asked.
doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(1, names=[u"Snid; A"])))
check("forbidden: refused before Revit is asked",
      doc.log == [] and u";" in rec.text(), rec.text())

# 10. Sheet collections.
check("collection rule: empty, forbidden, taken ignoring case, fine",
      model.collection_problem(u"  ", []) and
      model.collection_problem(u"A;B", []) and
      u"already" in model.collection_problem(u"utbod", [u"Utbod"]) and
      model.collection_problem(u"Utbo" + u"ð", [u"Utbod"]) == u"")

doc, a1, a3 = model_with_sheets()
FakeSheetCollection(doc, u"Verkteikningar")
FakeSheetCollection(doc, u"Aðalteikningar")
rec = run_script(doc, Recorder(batch(2, collection=(u"existing", 1))))
check("collection: the model's collections offered, sorted",
      rec.window and rec.window.collections == [u"Aðalteikningar",
                                                u"Verkteikningar"],
      rec.window and rec.window.collections)
new = [sh for sh in doc.sheets if sh._number in (u"A-902", u"A-903")]
check("collection: an existing one gets every new sheet",
      len(new) == 2 and all(sh._collection == doc.collections[0].Id
                            for sh in new),
      [sh._collection.Value for sh in new])
check("collection: older sheets are left where they were",
      all(sh._collection == INVALID for sh in doc.sheets if sh not in new))
check("collection: named in the report",
      u"Verkteikningar" in rec.text(), rec.text())

doc, a1, a3 = model_with_sheets()
rec = run_script(doc, Recorder(batch(2, collection=(u"new", u"Útboð"))))
made = [c for c in doc.collections if c.Name == u"Útboð"]
check("collection: a new one is made and gets the sheets",
      len(made) == 1 and sum(1 for sh in doc.sheets
                             if sh._collection == made[0].Id) == 2,
      [c.Name for c in doc.collections])

doc, a1, a3 = model_with_sheets()
FakeSheetCollection(doc, u"Útboð")
before = doc.numbers()
rec = run_script(doc, Recorder(batch(1, collection=(u"new", u"útboð"))))
check("collection: a new name another collection has is refused first",
      doc.log == [] and doc.numbers() == before and len(doc.collections) == 1
      and u"already" in rec.text(), rec.text())

doc, a1, a3 = model_with_sheets(refuse=[u"A-903"])
rec = run_script(doc, Recorder(batch(2, collection=(u"new", u"Útboð"))))
check("collection: Revit refusing a sheet undoes the new collection too",
      doc.collections == [] and u"A-903" in rec.text() and
      doc.numbers() == [u"A-101", u"A-102", u"A-9", u"A-901"],
      (rec.text(), [c.Name for c in doc.collections]))

doc, a1, a3 = model_with_sheets(ignore_collection=True)
rec = run_script(doc, Recorder(batch(1, collection=(u"new", u"Útboð"))))
check("collection: a set Revit ignores is caught on read back",
      u"did not go into" in rec.text() and doc.collections == [] and
      len(doc.numbers()) == 4, rec.text())

doc, a1, a3 = model_with_sheets(has_collections=False)
rec = run_script(doc, Recorder(batch(1)))
check("collection: a Revit without them offers none and still makes sheets",
      rec.window and rec.window.collections is None and
      len(doc.numbers()) == 5, rec.text())

# 11. Shift-click: a sheet for each view, the view in the middle.
def model_with_views(**kwargs):
    doc, a1, a3 = model_with_sheets(**kwargs)
    views = dict(
        haed1=FakeView(doc, 201, u"1. h\xe6\xf0", u"FloorPlan"),
        haed2=FakeView(doc, 202, u"2. h\xe6\xf0", u"FloorPlan"),
        snid=FakeView(doc, 203, u"Sni\xf0 A", u"Section"),
        legend=FakeView(doc, 204, u"Sk\xfdringar", u"Legend"),
        schedule=FakeView(doc, 205, u"Hur\xf0ir", u"Schedule"),
        template=FakeView(doc, 206, u"Sni\xf0m\xe1t", u"FloorPlan",
                          template=True),
        placed=FakeView(doc, 207, u"Grunnmynd", u"FloorPlan"))
    sheet_101 = [sh for sh in doc.sheets if sh._number == u"A-101"][0]
    FakeViewport(doc, sheet_101.Id, views["placed"].Id,
                 FakeXYZ(0.5, 0.4, 0.0), regenerated=True)
    return doc, a1, a3, views


def from_views(recorder, titleblock=None, align=False):
    """A person who keeps the suggested numbers and the views' names."""
    def answer(labels, chosen, number, count, evaluate):
        return dict(
            titleblock=chosen if titleblock is None else titleblock,
            values=(number[0], naming.numbers(count, number[1]),
                    number[2], u"", list(recorder.window.names), u""),
            collection=None, align=align)
    recorder.answer = answer
    return recorder


def viewport_of(doc, view):
    found = [v for v in doc.viewports if v.ViewId == view.Id]
    return found[0] if found else None


def sheet_by_id(doc, sheet_id):
    return [sh for sh in doc.sheets if sh.Id == sheet_id][0]


doc, a1, a3, views = model_with_views()
doc.selected = [views["snid"].Id, views["haed2"].Id, views["haed1"].Id,
                views["legend"].Id, doc.sheets[0].Id]
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: the window gets one row per selected view, sorted",
      rec.window and rec.window.count == 3 and rec.window.views ==
      [u"Floor Plan  1. h\xe6\xf0", u"Floor Plan  2. h\xe6\xf0",
       u"Section  Sni\xf0 A"], rec.window and rec.window.views)
check("views: each sheet name starts as its view's name",
      rec.window and rec.window.names == [u"1. h\xe6\xf0",
                                          u"2. h\xe6\xf0",
                                          u"Sni\xf0 A"])
check("views: the selection was used, no list shown",
      rec.picked_from is None)
spots = [viewport_of(doc, views[k]) for k in (u"haed1", u"haed2", u"snid")]
check("views: every view placed on its own new sheet, in order",
      all(spots) and [sheet_by_id(doc, v.SheetId)._number for v in spots] ==
      [u"A-902", u"A-903", u"A-904"] and
      [sheet_by_id(doc, v.SheetId)._name for v in spots] ==
      [u"1. h\xe6\xf0", u"2. h\xe6\xf0", u"Sni\xf0 A"],
      [(sheet_by_id(doc, v.SheetId)._number) for v in spots if v])
check("views: placed in the middle of the title block",
      all(spots) and all(abs(v.point.X + 0.69) < 1e-9 and
                         abs(v.point.Y - 0.485) < 1e-9 for v in spots),
      [(v.point.X, v.point.Y) for v in spots if v and v.point])
check("views: legend and sheet in the selection are not placed",
      viewport_of(doc, views["legend"]) is None and
      len(doc.viewports) == 4)
check("views: the report lists the views",
      u"Sni\xf0 A" in rec.text() and u"middle" in rec.text(), rec.text())

doc, a1, a3, views = model_with_views()
rec = run_script(doc, from_views(Recorder(ticks=[u"2. h"])), shift=True)
check("views: nothing selected, a list of views not yet on a sheet",
      rec.picked_from == [u"Floor Plan  1. h\xe6\xf0",
                          u"Floor Plan  2. h\xe6\xf0",
                          u"Section  Sni\xf0 A"], rec.picked_from)
check("views: the ticked view gets a sheet",
      viewport_of(doc, views["haed2"]) is not None and
      viewport_of(doc, views["haed1"]) is None)

doc, a1, a3, views = model_with_views()
before = doc.numbers()
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: cancelling the list makes nothing",
      rec.window is None and doc.numbers() == before and doc.log == [])

doc, a1, a3, views = model_with_views()
doc.selected = [views["placed"].Id, views["haed1"].Id]
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: one already on a sheet is left out and named",
      rec.window and rec.window.views == [u"Floor Plan  1. h\xe6\xf0"] and
      u"Grunnmynd (sheet A-101)" in rec.text(), rec.text())
check("views: the other still gets its sheet",
      viewport_of(doc, views["haed1"]) is not None)

doc, a1, a3, views = model_with_views()
doc.selected = [views["placed"].Id]
before = doc.numbers()
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: every chosen view on a sheet: said so, no window",
      rec.window is None and doc.log == [] and doc.numbers() == before and
      u"every chosen view" in rec.text(), rec.text())

doc, a1, a3, views = model_with_views()
doc.selected = [views["haed1"].Id, views["haed2"].Id]
doc.unplaceable.add(views["haed2"].Id)
before = doc.numbers()
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: Revit refusing one view: no sheet, no viewport",
      doc.numbers() == before and len(doc.viewports) == 1 and
      u"2. h" in rec.text() and u"no sheet was made" in rec.text(),
      rec.text())

doc, a1, a3, views = model_with_views()
doc.selected = [views["haed1"].Id]
doc.viewport_none = True
before = doc.numbers()
rec = run_script(doc, from_views(Recorder()), shift=True)
check("views: a viewport Revit does not return is caught",
      doc.numbers() == before and u"did not go onto" in rec.text(),
      rec.text())

doc, a1, a3, views = model_with_views()
doc.selected = [views["haed1"].Id]
rec = run_script(doc, from_views(Recorder(), titleblock=2), shift=True)
spot = viewport_of(doc, views["haed1"])
check("views: no title block places the view at the origin",
      spot is not None and spot.point.X == 0.0 and spot.point.Y == 0.0,
      spot and (spot.point.X, spot.point.Y))

doc, a1, a3, views = model_with_views()
doc.selected = [views["haed1"].Id]
rec = run_script(doc, Recorder(batch(1)))
check("views: without shift, a selected view is not placed",
      rec.window and rec.window.views is None and
      viewport_of(doc, views["haed1"]) is None and len(doc.numbers()) == 5)

# 12. Shift-click, aligned to a view on another sheet.
def aligned_model(**kwargs):
    doc, a1, a3, views = model_with_views(**kwargs)
    # Other crops, scales and annotation offsets than the source's, so
    # lining up the boxes or the middles would not line up the crops.
    views["haed1"].crops = ((10.0, 5.0, 40.0, 25.0),)
    views["haed1"].scale = 50
    views["haed2"].crops = ((-20.0, -10.0, 15.0, 30.0),)
    return doc, a1, a3, views


SOURCE_CORNER = (0.5, 0.6)   # the placed view: centre (0.5, 0.4), crop top 0.2

doc, a1, a3, views = aligned_model()
doc.selected = [views["haed1"].Id, views["haed2"].Id]
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd"),
                                 align=True), shift=True)
check("align: the views on sheets are offered to align to",
      rec.align_offered and any(u"A-101" in label and u"Grunnmynd" in label
                                for label in rec.align_offered),
      rec.align_offered)
spots = [viewport_of(doc, views[k]) for k in (u"haed1", u"haed2")]
check("align: each new view's crop top left is on the picked view's",
      all(spots) and all(abs(v.crop_top_left()[0] - SOURCE_CORNER[0]) < 1e-6
                         and abs(v.crop_top_left()[1] - SOURCE_CORNER[1])
                         < 1e-6 for v in spots),
      [v.crop_top_left() for v in spots if v])
check("align: the sheets are made, the views on their own sheets",
      len(doc.numbers()) == 6 and
      [sheet_by_id(doc, v.SheetId)._number for v in spots if v] ==
      [u"A-902", u"A-903"])
check("align: the report says what they were aligned to",
      u"aligned to A-101" in rec.text() and u"| aligned |" in rec.text(),
      rec.text())

doc, a1, a3, views = aligned_model()
views["haed2"].CropBoxActive = False
doc.selected = [views["haed1"].Id, views["haed2"].Id]
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd"),
                                 align=True), shift=True)
spot = viewport_of(doc, views["haed2"])
check("align: a view with its crop off stays in the middle, noted",
      spot is not None and abs(spot.point.X + 0.69) < 1e-9 and
      u"in the middle: its crop region is off" in rec.text(), rec.text())
check("align: the others are still aligned",
      viewport_of(doc, views["haed1"]) is not None and
      abs(viewport_of(doc, views["haed1"]).crop_top_left()[1] -
          SOURCE_CORNER[1]) < 1e-6)

doc, a1, a3, views = aligned_model()
doc.selected = [views["haed1"].Id]
before = doc.numbers()
rec = run_script(doc, from_views(Recorder(), align=True), shift=True)
check("align: cancelling the pick makes nothing",
      rec.align_offered and doc.numbers() == before and doc.log == [],
      doc.log)

doc, a1, a3, views = aligned_model()
views["placed"].CropBoxActive = False
doc.selected = [views["haed1"].Id]
before = doc.numbers()
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd"),
                                 align=True), shift=True)
check("align: a picked view with no crop: said so, nothing made",
      doc.numbers() == before and doc.log == [] and
      u"cannot be aligned to" in rec.text(), rec.text())

doc, a1, a3, views = aligned_model()
doc.selected = [views["haed1"].Id]
# Regenerates for the placing, but not after the move: the corner read
# back is stale.
doc.regenerations_left = 2
before = doc.numbers()
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd"),
                                 align=True), shift=True)
check("align: a view that does not land rolls everything back",
      doc.numbers() == before and len(doc.viewports) == 1 and
      u"did not land" in rec.text(), rec.text())

doc, a1, a3, views = aligned_model()
doc.selected = [views["haed1"].Id]
doc.regenerate_works = False
before = doc.numbers()
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd"),
                                 align=True), shift=True)
check("align: a corner that cannot be read stops the run, not the middle",
      doc.numbers() == before and len(doc.viewports) == 1 and
      u"could not be aligned" in rec.text(), rec.text())

doc, a1, a3, views = aligned_model()
doc.selected = [views["haed1"].Id]
rec = run_script(doc, from_views(Recorder(align_to=u"Grunnmynd")),
                 shift=True)
spot = viewport_of(doc, views["haed1"])
check("align: not chosen, no pick and the view stays in the middle",
      rec.align_offered is None and spot is not None and
      abs(spot.point.X + 0.69) < 1e-9)

# --------------------------------------------------------------------------

check("harness: a taken number raises in the fake",
      not model.plan(u"", [u"x"], u"", u"", [u"y"], u"", [u"x"])[0]
      .problem == u"")
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
