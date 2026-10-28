# -*- coding: utf-8 -*-
"""
Runs the real Sync Scope Boxes script against a mocked Revit.

The fake is built to lie the way Revit lies, because a fake that answers
honestly straight away tests a Revit that does not exist. Place at
Coordinate passed 67 checks over a tool that could not place a marker
for exactly that reason. So here:

- a scope box copied in has no geometry until the document regenerates,
- deleting a scope box strips it from every view and datum assigned to
  it, the way Revit does,
- a rollback really restores the model, so a script that rolls back and
  then claims success fails a check,
- the link sits under a real rotation and translation, so a copy made
  without the link's transform lands somewhere visibly wrong.

Run outside Revit:

    python test_sync_scope_boxes_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Sync Scope Boxes.pushbutton", "script.py")

from avh_scopebox import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def safely(function, *args):
    """Call it, and turn a raise into a value that fails a check.

    Written first this time. A check that raises aborts the run and hides
    every check after it, and this codebase has now paid for that three
    times.
    """
    try:
        return function(*args)
    except Exception as exc:
        return u"<raised {0}>".format(exc)


FEET = 1.0 / 0.3048
LABEL = u"ELG_ARK"


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

_next = [1000]


def next_int():
    _next[0] += 1
    return _next[0]


class FakeId(object):
    def __init__(self, value=None):
        self.IntegerValue = next_int() if value is None else value

    def __eq__(self, other):
        return (isinstance(other, FakeId) and
                other.IntegerValue == self.IntegerValue)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "FakeId({0})".format(self.IntegerValue)


INVALID = FakeId(-1)


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X = x
        self.Y = y
        self.Z = z


class FakeLine(object):
    def __init__(self, start, end):
        self._ends = (FakeXYZ(*start), FakeXYZ(*end))

    def GetEndPoint(self, index):
        return self._ends[index]


class FakeTransform(object):
    """Rotation about Z plus a translation, the way a placed link sits."""

    def __init__(self, degrees=0.0, offset=(0.0, 0.0, 0.0)):
        angle = math.radians(degrees)
        self.Origin = FakeXYZ(*offset)
        self.BasisX = FakeXYZ(math.cos(angle), math.sin(angle), 0.0)
        self.BasisY = FakeXYZ(-math.sin(angle), math.cos(angle), 0.0)
        self.BasisZ = FakeXYZ(0.0, 0.0, 1.0)

    def apply(self, point):
        o, bx, by, bz = self.Origin, self.BasisX, self.BasisY, self.BasisZ
        x, y, z = point
        return (o.X + x * bx.X + y * by.X + z * bz.X,
                o.Y + x * bx.Y + y * by.Y + z * bz.Y,
                o.Z + x * bx.Z + y * by.Z + z * bz.Z)


IDENTITY = FakeTransform()
LINK_TRANSFORM = FakeTransform(degrees=30.0, offset=(500.0, -200.0, 12.0))


def box_corners(origin, size):
    """Eight corners of an axis aligned box in its own document."""
    ox, oy, oz = origin
    sx, sy, sz = size
    return [(ox + dx * sx, oy + dy * sy, oz + dz * sz)
            for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]


def edges_of_corners(corners):
    """The twelve edges of a box, from its eight corners.

    `box_corners` orders corners so that two indices joined by an edge
    differ in exactly one bit. That stays true after a rotation, whereas
    comparing coordinates would not, so edges are chosen by index.
    """
    edges = []
    for i in range(8):
        for j in range(i + 1, 8):
            if bin(i ^ j).count("1") == 1:
                edges.append((corners[i], corners[j]))
    return edges


class FakeParameter(object):
    def __init__(self, value, read_only=False, refuses=False,
                 does_not_hold=False):
        self.value = value
        self.IsReadOnly = read_only
        self.refuses = refuses
        self.does_not_hold = does_not_hold

    def AsElementId(self):
        return self.value

    def AsInteger(self):
        return self.value

    def Set(self, value):
        if self.refuses:
            return False
        if not self.does_not_hold:
            self.value = value
        return True


class BuiltIns(object):
    VIEWER_VOLUME_OF_INTEREST_CROP = "VIEWER_VOLUME_OF_INTEREST_CROP"
    DATUM_VOLUME_OF_INTEREST = "DATUM_VOLUME_OF_INTEREST"
    ELEM_PARTITION_PARAM = "ELEM_PARTITION_PARAM"


class FakeElement(object):
    def __init__(self, name=u""):
        self.Id = FakeId()
        self._name = name
        self.parameters = {}

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        self._name = value

    def get_Parameter(self, built_in):
        return self.parameters.get(built_in)


class FakeScopeBox(FakeElement):
    def __init__(self, name, corners, regenerated=True, workset=0,
                 rename_mode="ok"):
        FakeElement.__init__(self, name)
        self.corners = list(corners)
        self.regenerated = regenerated
        self.rename_mode = rename_mode
        self.parameters[BuiltIns.ELEM_PARTITION_PARAM] = FakeParameter(workset)

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if self.rename_mode == "raises":
            raise Exception("the name is in use")
        if self.rename_mode == "ignored":
            return
        self._name = value

    def get_Geometry(self, options):
        # Revit's lie: a new element has nothing true to say about its
        # shape until the document regenerates.
        if not self.regenerated:
            return []
        return [FakeLine(a, b) for a, b in edges_of_corners(self.corners)]


class FakeView(FakeElement):
    def __init__(self, name, box_id=None, template=False, refuses=False):
        FakeElement.__init__(self, name)
        self.IsTemplate = template
        self.parameters[BuiltIns.VIEWER_VOLUME_OF_INTEREST_CROP] = \
            FakeParameter(box_id if box_id is not None else INVALID,
                          refuses=refuses)


class FakeDatum(FakeElement):
    def __init__(self, name, box_id=None):
        FakeElement.__init__(self, name)
        self.parameters[BuiltIns.DATUM_VOLUME_OF_INTEREST] = \
            FakeParameter(box_id if box_id is not None else INVALID)


class FakeGrid(FakeDatum):
    pass


class FakeLevel(FakeDatum):
    pass


class FakeReferencePlane(FakeDatum):
    pass


class FakeLinkType(FakeElement):
    pass


class FakeLinkDocument(object):
    def __init__(self, boxes):
        self.boxes = list(boxes)

    def GetElement(self, element_id):
        for box in self.boxes:
            if box.Id == element_id:
                return box
        return None


class FakeLinkInstance(FakeElement):
    def __init__(self, link_type, link_doc, transform=LINK_TRANSFORM):
        FakeElement.__init__(self, link_type.Name + u" : 1")
        self.link_type = link_type
        self.link_doc = link_doc
        self.transform = transform

    def GetLinkDocument(self):
        return self.link_doc

    def GetTypeId(self):
        return self.link_type.Id

    def GetTotalTransform(self):
        return self.transform


class FakeWorkset(object):
    def __init__(self, name, value):
        self.Name = name
        self.Id = FakeId(value)


class FakeDocument(object):
    def __init__(self, elements=(), workshared=True, worksets=None,
                 copy_ignores_transform=False, delete_raises=False,
                 commit_status="Committed"):
        self.elements = list(elements)
        self.IsWorkshared = workshared
        self.worksets = (worksets if worksets is not None else
                         [FakeWorkset(u"Workset1", 1),
                          FakeWorkset(u"Shared Views, Levels, Grids", 7)])
        self.copy_ignores_transform = copy_ignores_transform
        self.delete_raises = delete_raises
        self.commit_status = commit_status
        self.events = []
        self.regenerations = 0

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def Regenerate(self):
        self.regenerations += 1
        for element in self.elements:
            if isinstance(element, FakeScopeBox):
                element.regenerated = True

    def Delete(self, element_id):
        if self.delete_raises:
            raise Exception("this scope box cannot be deleted")
        target = self.GetElement(element_id)
        if target is None:
            raise Exception("no such element")
        self.events.append(("delete", target.Name))
        # Revit's behaviour: deleting a scope box strips it from every
        # view and datum that used it.
        for element in self.elements:
            for parameter in element.parameters.values():
                if parameter.value == element_id:
                    parameter.value = INVALID
        self.elements.remove(target)

    def snapshot(self):
        state = []
        for element in self.elements:
            state.append((element, element._name,
                          dict((k, p.value) for k, p in
                               element.parameters.items()),
                          getattr(element, "corners", None)))
        return state

    def restore(self, state):
        self.elements = []
        for element, name, values, corners in state:
            element._name = name
            for key, value in values.items():
                element.parameters[key].value = value
            if corners is not None:
                element.corners = corners
            self.elements.append(element)


class FakeCollector(object):
    def __init__(self, document):
        self.document = document
        self.cls = None
        self.category = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def OfCategory(self, category):
        self.category = category
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        if isinstance(self.document, FakeLinkDocument):
            source = self.document.boxes
        else:
            source = self.document.elements
        if self.category == "OST_VolumeOfInterest":
            return iter([e for e in source if isinstance(e, FakeScopeBox)])
        if self.cls is not None:
            return iter([e for e in source if isinstance(e, self.cls)])
        return iter([])


class FakeWorksetCollector(object):
    def __init__(self, document):
        self.document = document

    def OfKind(self, kind):
        return self

    def __iter__(self):
        return iter(list(self.document.worksets))


class FakeCopyUtils(object):
    host = None

    @staticmethod
    def CopyElements(source_doc, ids, destination, transform, options):
        new_ids = []
        for element_id in list(ids):
            source = source_doc.GetElement(element_id)
            if destination.copy_ignores_transform:
                corners = list(source.corners)
            else:
                corners = [transform.apply(c) for c in source.corners]
            copy = FakeScopeBox(source.Name, corners, regenerated=False,
                                rename_mode=destination.rename_mode_for_copies)
            destination.elements.append(copy)
            destination.events.append(("copy", source.Name))
            new_ids.append(copy.Id)
        return new_ids


class FakeTransaction(object):
    log = []

    def __init__(self, doc, name):
        self.doc = doc
        self.state = None

    def Start(self):
        self.state = self.doc.snapshot()
        FakeTransaction.log.append("start")

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.log.append("rollback")

    def Commit(self):
        FakeTransaction.log.append("commit")
        if self.doc.commit_status != "Committed":
            self.doc.restore(self.state)
        return self.doc.commit_status


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    def __init__(self, pick=None, confirm=True):
        self.pick = pick
        self.confirm = confirm
        self.alerts = []
        self.printed = []
        self.offered = None

    def show(self, items, **kwargs):
        self.offered = list(items)
        if self.pick is None:
            return items[0] if items else None
        return self.pick

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes") and kwargs.get("no"):
            return self.confirm
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, pick=None, confirm=True, rename_mode="ok"):
    del FakeTransaction.log[:]
    doc.rename_mode_for_copies = rename_mode
    recorder = Recorder(pick=pick, confirm=confirm)

    db = Namespace(
        FilteredElementCollector=FakeCollector,
        FilteredWorksetCollector=FakeWorksetCollector,
        WorksetKind=Namespace(UserWorkset="UserWorkset"),
        RevitLinkInstance=FakeLinkInstance,
        View=FakeView,
        Grid=FakeGrid,
        Level=FakeLevel,
        ReferencePlane=FakeReferencePlane,
        BuiltInCategory=Namespace(OST_VolumeOfInterest="OST_VolumeOfInterest"),
        BuiltInParameter=BuiltIns,
        ElementTransformUtils=FakeCopyUtils,
        CopyPasteOptions=lambda: None,
        Options=lambda: None,
        ElementId=FakeId,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=None)
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=recorder.show))
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
    return recorder


# --------------------------------------------------------------------------
# Building scenarios
# --------------------------------------------------------------------------

def link_box(name, origin=(0, 0, 0), size=(100, 60, 40)):
    return FakeScopeBox(name, box_corners(origin, size))


def host_copy_of(box, transform=LINK_TRANSFORM, name=None):
    """What a previous run would have left in the host."""
    corners = [transform.apply(c) for c in box.corners]
    return FakeScopeBox(name or model.prefixed(LABEL, box.Name), corners,
                        workset=7)


def setup(link_boxes, host_extra=(), workshared=True, **doc_kwargs):
    link_type = FakeLinkType(u"ELG_ARK.rvt")
    link_doc = FakeLinkDocument(link_boxes)
    instance = FakeLinkInstance(link_type, link_doc)
    doc = FakeDocument([link_type, instance] + list(host_extra),
                       workshared=workshared, **doc_kwargs)
    return doc, link_doc


def boxes_in(doc):
    return [e for e in doc.elements if isinstance(e, FakeScopeBox)]


def box_named(doc, name):
    for box in boxes_in(doc):
        if box.Name == name:
            return box
    return None


def on_link_box(doc, name, source):
    """Is the host box `name` exactly where the link shows `source`?"""
    box = box_named(doc, name)
    if box is None:
        return False
    expected = [LINK_TRANSFORM.apply(c) for c in source.corners]
    return model.same_shape(box.corners, expected)


# --------------------------------------------------------------------------
# The arithmetic, no Revit at all
# --------------------------------------------------------------------------

check("label: the extension is stripped whatever its case",
      model.link_label(u"ELG_ARK.RVT") == u"ELG_ARK")
check("label: characters Revit refuses become underscores",
      model.link_label(u"ELG:ARK|v2.rvt") == u"ELG_ARK_v2")
check("prefixed: link label, separator, box name",
      model.prefixed(u"ELG_ARK", u"DA11") == u"ELG_ARK - DA11")
check("prefixed: the separator itself is a legal name",
      not any(c in model.SEPARATOR for c in u"\\:{}[]|;<>?`~"))
check("ours: a prefixed name is recognised",
      model.is_ours(u"ELG_ARK - DA11", u"ELG_ARK"))
check("ours: a hand made box of the same short name is not",
      not model.is_ours(u"DA11", u"ELG_ARK"))
check("ours: another link's copy is not",
      not model.is_ours(u"ELG_RAF - DA11", u"ELG_ARK"))

cube = box_corners((0, 0, 0), (10, 6, 4))
check("corners: twelve edges reduce to eight corners",
      len(model.corners_from_edges(edges_of_corners(cube))) == 8)
check("shape: a box matches itself in any order",
      model.same_shape(cube, list(reversed(cube))))
shifted = [(x + 0.01, y, z) for x, y, z in cube]
check("shape: a 3 mm shift is a change", not model.same_shape(cube, shifted))
tiny = [(x + 0.0003, y, z) for x, y, z in cube]
check("shape: a tenth of a millimetre is not",
      model.same_shape(cube, tiny))

# The reason for comparing corners rather than a bounding box: a square
# box rotated 90 degrees about its centre has the same bounding box and a
# different set of edges only if it is not square, so use a long box
# rotated 180 degrees as the trap that must NOT fire, and a few degrees
# as the change that MUST.
spin = FakeTransform(degrees=5.0)
centre = (5.0, 3.0, 2.0)
rotated = []
for x, y, z in cube:
    px, py, pz = x - centre[0], y - centre[1], z - centre[2]
    rx, ry, rz = spin.apply((px, py, pz))
    rotated.append((rx + centre[0], ry + centre[1], rz + centre[2]))
check("shape: five degrees of rotation about the centre is a change",
      not model.same_shape(cube, rotated))



def spun_about_centre(corners, degrees):
    turn = FakeTransform(degrees=degrees)
    cx = sum(c[0] for c in corners) / 8.0
    cy = sum(c[1] for c in corners) / 8.0
    out = []
    for x, y, z in corners:
        rx, ry, rz = turn.apply((x - cx, y - cy, z))
        out.append((rx + cx, ry + cy, rz))
    return out


def bounding(corners):
    return tuple((min(c[i] for c in corners), max(c[i] for c in corners))
                 for i in range(3))


# The case a bounding box genuinely cannot see. A rectangle turned +20
# degrees and one turned -20 degrees about the same centre have exactly
# the same bounding box and different corners. Five degrees, above,
# changes the bounding box too, so it never tested this at all; the
# mutation that swapped corners for a bounding box survived until this
# was added.
long_box = box_corners((0, 0, 0), (40, 10, 4))
plus = spun_about_centre(long_box, 20.0)
minus = spun_about_centre(long_box, -20.0)
same_bounds = all(abs(bounding(plus)[i][j] - bounding(minus)[i][j]) < 1e-9
                  for i in range(3) for j in range(2))
check("shape: the mirrored rotation trap is real, same bounding box",
      same_bounds, repr((bounding(plus), bounding(minus))))
check("shape: and corners still tell the two apart",
      not model.same_shape(plus, minus))
plan = model.classify([model.Box(u"DA11", 1, minus)],
                      [model.Box(u"ELG_ARK - DA11", 2, plus)], u"ELG_ARK")
check("classify: a box re-angled to the mirror rotation is CHANGED",
      [e[0] for e in plan] == [model.CHANGED], str([e[0] for e in plan]))

check("shift: reported in millimetres",
      abs(model.largest_shift_mm(cube, shifted) - 3.048) < 0.01)
check("shift: nothing to measure is None, not a crash",
      model.largest_shift_mm([], cube) is None)

through = model.apply_transform(
    ((500.0, -200.0, 12.0), (math.cos(math.radians(30)), math.sin(
        math.radians(30)), 0.0), (-math.sin(math.radians(30)),
                                  math.cos(math.radians(30)), 0.0),
     (0.0, 0.0, 1.0)), (10.0, 0.0, 0.0))
check("transform: matches Revit's OfPoint arithmetic",
      all(abs(through[i] - LINK_TRANSFORM.apply((10.0, 0.0, 0.0))[i]) < 1e-9
          for i in range(3)))

a = model.Box(u"DA11", 1, cube)
b = model.Box(u"DA11", 2, cube)
plan = model.classify([a, b], [], u"ELG_ARK")
check("classify: a name used twice in the link is skipped, both times",
      [e[0] for e in plan] == [model.DUPLICATE, model.DUPLICATE])
plan = model.classify([], [model.Box(u"ELG_ARK - GONE", 3, cube),
                           model.Box(u"MINE", 4, cube)], u"ELG_ARK")
check("classify: a copy no longer in the link is an orphan",
      [(e[0], e[3]) for e in plan] == [(model.ORPHAN, u"ELG_ARK - GONE")])
check("classify: a hand made box is not even reported",
      all(e[3] != u"MINE" for e in plan))


# --------------------------------------------------------------------------
# 1. First run: everything is new
# --------------------------------------------------------------------------

da11 = link_box(u"DA11", (0, 0, 0))
da12 = link_box(u"DA12", (200, 0, 0))
doc, link_doc = setup([da11, da12])
recorder = run_script(doc)

check("first run: both copied in", len(boxes_in(doc)) == 2,
      str([b.Name for b in boxes_in(doc)]))
check("first run: named after the link",
      box_named(doc, u"ELG_ARK - DA11") is not None and
      box_named(doc, u"ELG_ARK - DA12") is not None,
      str([b.Name for b in boxes_in(doc)]))
check("first run: DA11 landed where the link shows it",
      on_link_box(doc, u"ELG_ARK - DA11", da11))
check("first run: DA12 too", on_link_box(doc, u"ELG_ARK - DA12", da12))
check("first run: on Shared Views, Levels, Grids",
      box_named(doc, u"ELG_ARK - DA11") is not None and
      box_named(doc, u"ELG_ARK - DA11").parameters[
          BuiltIns.ELEM_PARTITION_PARAM].value == 7)
check("first run: committed, nothing rolled back",
      FakeTransaction.log == ["start", "commit"], str(FakeTransaction.log))
check("first run: regenerated before reading anything back",
      doc.regenerations >= 2, str(doc.regenerations))
check("first run: the dry run named what it would do",
      u"new in the link" in recorder.text(), recorder.text())
check("first run: the link was offered by its file name",
      recorder.offered == [u"ELG_ARK.rvt"], str(recorder.offered))

# A second run over the result has nothing to do.
recorder = run_script(doc)
check("second run: nothing written, no transaction",
      not FakeTransaction.log, str(FakeTransaction.log))
check("second run: says everything is current",
      u"already here and unchanged" in recorder.text(), recorder.text())
check("second run: still exactly two boxes", len(boxes_in(doc)) == 2)


# --------------------------------------------------------------------------
# 2. A box changed in the link
# --------------------------------------------------------------------------

old_da11 = link_box(u"DA11", (0, 0, 0))
old_copy = host_copy_of(old_da11)
moved_da11 = link_box(u"DA11", (15, 0, 0))   # moved 15 ft in the link

view_a = FakeView(u"Plan A", old_copy.Id)
view_b = FakeView(u"Section B", old_copy.Id)
view_other = FakeView(u"Plan C")
template = FakeView(u"Template", old_copy.Id, template=True)
grid = FakeGrid(u"Grid 1", old_copy.Id)
level = FakeLevel(u"DA11.E01", old_copy.Id)
plane = FakeReferencePlane(u"Ref", old_copy.Id)

doc, link_doc = setup([moved_da11], [old_copy, view_a, view_b, view_other,
                                     template, grid, level, plane])
recorder = run_script(doc)

new_copy = box_named(doc, u"ELG_ARK - DA11")
check("changed: exactly one DA11 afterwards", len(boxes_in(doc)) == 1,
      str([b.Name for b in boxes_in(doc)]))
check("changed: it is a new element, not the old one",
      new_copy is not None and new_copy is not old_copy)
check("changed: it sits where the link now shows it",
      on_link_box(doc, u"ELG_ARK - DA11", moved_da11))
check("changed: the old box is gone", old_copy not in doc.elements)


def assigned(element, built_in):
    return element.parameters[built_in].value


VIEW_PARAM = BuiltIns.VIEWER_VOLUME_OF_INTEREST_CROP
DATUM_PARAM = BuiltIns.DATUM_VOLUME_OF_INTEREST
check("changed: both views moved to the new box",
      new_copy is not None and
      assigned(view_a, VIEW_PARAM) == new_copy.Id and
      assigned(view_b, VIEW_PARAM) == new_copy.Id)
check("changed: a view that used no box still uses none",
      assigned(view_other, VIEW_PARAM) == INVALID)
check("changed: the grid followed",
      new_copy is not None and assigned(grid, DATUM_PARAM) == new_copy.Id)
check("changed: the level followed",
      new_copy is not None and assigned(level, DATUM_PARAM) == new_copy.Id)
check("changed: the reference plane followed",
      new_copy is not None and assigned(plane, DATUM_PARAM) == new_copy.Id)
check("changed: the confirmation counted what would move",
      u"5 view(s) and datum(s)" in recorder.text(), recorder.text())
check("changed: and warned about the new element id",
      u"new element id" in recorder.text())
check("changed: the report says how far it moved",
      u"worst corner moved" in recorder.text(), recorder.text())
check("changed: copied before the old one was deleted",
      [e[0] for e in doc.events] == ["copy", "delete"], str(doc.events))
check("changed: committed", FakeTransaction.log == ["start", "commit"],
      str(FakeTransaction.log))


# --------------------------------------------------------------------------
# 3. Things that must never be touched
# --------------------------------------------------------------------------

da11 = link_box(u"DA11")
mine = FakeScopeBox(u"DA11", box_corners((900, 900, 0), (5, 5, 5)))
orphan = FakeScopeBox(u"ELG_ARK - GONE", box_corners((50, 50, 0), (5, 5, 5)))
other_link = FakeScopeBox(u"ELG_RAF - DA11",
                          box_corners((70, 70, 0), (5, 5, 5)))
doc, link_doc = setup([da11], [mine, orphan, other_link])
recorder = run_script(doc)

check("untouched: a hand made box of the same short name survives",
      mine in doc.elements and mine.Name == u"DA11" and
      mine.corners == box_corners((900, 900, 0), (5, 5, 5)))
check("untouched: an orphaned copy is left in place",
      orphan in doc.elements)
check("untouched: and reported, not silently ignored",
      u"no longer in the link" in recorder.text(), recorder.text())
check("untouched: another link's copy is left alone",
      other_link in doc.elements and u"ELG_RAF" not in recorder.text())
check("untouched: the new box still arrived alongside them",
      box_named(doc, u"ELG_ARK - DA11") is not None)

dup_a = link_box(u"DA11", (0, 0, 0))
dup_b = link_box(u"DA11", (300, 0, 0))
doc, link_doc = setup([dup_a, dup_b, link_box(u"DA12", (600, 0, 0))])
recorder = run_script(doc)
check("duplicates: neither copy of the doubled name is brought in",
      box_named(doc, u"ELG_ARK - DA11") is None)
check("duplicates: the rest still are",
      box_named(doc, u"ELG_ARK - DA12") is not None)
check("duplicates: reported", u"name used twice" in recorder.text())


# --------------------------------------------------------------------------
# 4. Every refusal rolls the whole run back
# --------------------------------------------------------------------------

# A copy that ignores the link's transform lands at the link's internal
# coordinates. That is what forgetting the transform looks like, and it
# is invisible without the readback.
doc, link_doc = setup([link_box(u"DA11")], copy_ignores_transform=True)
recorder = run_script(doc)
check("wrong landing: nothing kept", not boxes_in(doc),
      str([b.Name for b in boxes_in(doc)]))
check("wrong landing: rolled back", "rollback" in FakeTransaction.log)
check("wrong landing: the working is printed",
      u"What the copy of ELG_ARK - DA11 did" in recorder.text() and
      u"expected corners" in recorder.text(), recorder.text())
check("wrong landing: no success report", u"#### Done" not in recorder.text())

# A view that refuses its new box. Replacing anyway would leave it with
# none, which is invisible until a drawing is cropped wrongly.
old_da11 = link_box(u"DA11")
old_copy = host_copy_of(old_da11)
stubborn = FakeView(u"Plan Stubborn", old_copy.Id, refuses=True)
doc, link_doc = setup([link_box(u"DA11", (15, 0, 0))], [old_copy, stubborn])
recorder = run_script(doc)
check("refused reassignment: the old box is still there",
      old_copy in doc.elements, str([b.Name for b in boxes_in(doc)]))
check("refused reassignment: and the view still points at it",
      assigned(stubborn, VIEW_PARAM) == old_copy.Id)
check("refused reassignment: the view is named",
      u"Plan Stubborn" in recorder.text(), recorder.text())
check("refused reassignment: only one box afterwards",
      len(boxes_in(doc)) == 1)

old_copy = host_copy_of(link_box(u"DA11"))
doc, link_doc = setup([link_box(u"DA11", (15, 0, 0))], [old_copy],
                      delete_raises=True)
recorder = run_script(doc)
check("delete refused: rolled back with the old box intact",
      old_copy in doc.elements and len(boxes_in(doc)) == 1)

doc, link_doc = setup([link_box(u"DA11")])
recorder = run_script(doc, rename_mode="raises")
check("rename refused: nothing kept, since an unnamed copy would be "
      "copied again next run", not boxes_in(doc))

doc, link_doc = setup([link_box(u"DA11")])
recorder = run_script(doc, rename_mode="ignored")
check("rename silently ignored: caught by reading the name back",
      not boxes_in(doc) and u"reads back as" in recorder.text(),
      recorder.text())

doc, link_doc = setup([link_box(u"DA11")], commit_status="RolledBack")
recorder = run_script(doc)
# Two separate witnesses. Checking that the model is empty afterwards
# would be decoration: a rejected commit leaves it empty whether the
# script noticed or not, so that check could never fail.
check("rejected commit: no success report",
      u"#### Done" not in recorder.text(), recorder.text())
check("rejected commit: the user is told it was rejected",
      any(u"rejected" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))


# --------------------------------------------------------------------------
# 5. Nothing written when it should not be
# --------------------------------------------------------------------------

doc, link_doc = setup([link_box(u"DA11")])
recorder = run_script(doc, confirm=False)
check("declined: nothing copied", not boxes_in(doc))
check("declined: no transaction", not FakeTransaction.log,
      str(FakeTransaction.log))
check("declined: the dry run was still shown",
      u"new in the link" in recorder.text())

doc = FakeDocument([])
recorder = run_script(doc)
check("no links: said so", u"No loaded Revit links" in recorder.text())

doc, link_doc = setup([])
recorder = run_script(doc)
check("a link with no scope boxes: said so",
      u"has no scope boxes" in recorder.text(), recorder.text())

doc, link_doc = setup([link_box(u"DA11")], workshared=False)
recorder = run_script(doc)
check("not workshared: still copied",
      box_named(doc, u"ELG_ARK - DA11") is not None)
check("not workshared: and says why it is not on the workset",
      u"not workshared" in recorder.text(), recorder.text())


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
