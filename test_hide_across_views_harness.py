# -*- coding: utf-8 -*-
"""
Runs the real Hide Across Views script against a mocked Revit.

The fake views are behavioural. A view owns the set of elements hidden in
it, HideElements and UnhideElements move keys in and out of that set, and
an element answers IsHidden by reading it. So "unhid something that was
not hidden" and "wrote to a view whose template owns the category" are
things the harness can see rather than things the reader has to notice.

A rollback restores every view to the state it had when that transaction
started, which is what makes the all or nothing decision testable.

Run outside Revit:

    python test_hide_across_views_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Hide Across Views.pushbutton", "script.py")

from avh_visibility import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


VIS_MODEL = "VIS_GRAPHICS_MODEL"
VIS_ANNOTATION = "VIS_GRAPHICS_ANNOTATION"

WALLS = -2000011
TAGS = -2000280
ANALYTICAL = -2009653


class FakeId(object):
    def __init__(self, value):
        self.Value = value
        self.IntegerValue = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)

    def __repr__(self):
        return "<Id {0}>".format(self.Value)


class FakeCategory(object):
    def __init__(self, key, name, kind):
        self.Id = FakeId(key)
        self.Name = name
        self.CategoryType = kind


SECTIONS = -2000200
ELEVATIONS = -2000201
CALLOUTS = -2000202

MARKER_CATEGORIES = {
    "OST_Sections": (SECTIONS, u"Sections"),
    "OST_Elev": (ELEVATIONS, u"Elevations"),
    "OST_Callouts": (CALLOUTS, u"Callouts"),
}


MISSING_CATEGORY = []


def get_category(doc, builtin):
    """Stands in for DB.Category.GetCategory(doc, BuiltInCategory)."""
    if MISSING_CATEGORY:
        return None
    found = MARKER_CATEGORIES.get(builtin)
    if found is None:
        return None
    return FakeCategory(found[0], found[1], "Annotation")


class FakeElement(object):
    def __init__(self, key, category, hideable_in=None, view_type=None,
                 name=u""):
        self.Id = FakeId(key)
        self.Category = category
        # None means "every view can hide me"; a set names the view keys.
        self.hideable_in = hideable_in
        self.Name = name
        if view_type is not None:
            self.ViewType = view_type

    def CanBeHidden(self, view):
        if self.hideable_in is None:
            return True
        return view.Id.Value in self.hideable_in

    def IsHidden(self, view):
        return self.Id.Value in view.hidden_elements


class FakeParameter(object):
    """Carries whichever of the two readers the route uses."""

    def __init__(self, name, builtin=u"", string=None, element_id=None):
        self.Definition = Namespace(Name=name, BuiltInParameter=builtin)
        self.string = string
        self.element_id = element_id

    def AsString(self):
        if self.string is None:
            raise Exception("not a string parameter")
        return self.string

    def AsValueString(self):
        return self.AsString()

    def AsElementId(self):
        if self.element_id is None:
            raise Exception("not an element id parameter")
        return self.element_id


class FakeViewer(object):
    """What Revit actually hands back when a section marker is selected.

    Category Views, class Element, no ViewType, and no VIEWER_VIEW_NAME:
    that parameter does not exist in this Revit. Whatever route there is
    has to be read off the parameters it does carry.
    """

    def __init__(self, key, category, parameters=(), name=u"Section 79"):
        self.Id = FakeId(key)
        self.Category = category
        self.Name = name
        self.Parameters = list(parameters)
        self.hideable_in = None

    def GetType(self):
        return Namespace(Name=u"Element")

    def CanBeHidden(self, view):
        return True

    def IsHidden(self, view):
        return self.Id.Value in view.hidden_elements


class FakeView(object):
    def __init__(self, key, name, vtype="FloorPlan", is_template=False,
                 template=None, hidden_elements=(), hidden_categories=None,
                 refuses=(), hide_raises=False):
        self.Id = FakeId(key)
        self.Name = name
        self.ViewType = vtype
        self.IsTemplate = is_template
        self.template = template
        self.ViewTemplateId = FakeId(template.Id.Value if template else -1)
        self.hidden_elements = set(hidden_elements)
        self.hidden_categories = dict(hidden_categories or {})
        self.refuses = set(refuses)
        self.hide_raises = hide_raises
        self.non_controlled = [FakeId(VIS_MODEL), FakeId(VIS_ANNOTATION)]

    def GetNonControlledTemplateParameterIds(self):
        return list(self.non_controlled)

    def HideElements(self, collection):
        if self.hide_raises:
            raise Exception("the view refused the hide")
        for element_id in collection:
            self.hidden_elements.add(element_id.Value)

    def UnhideElements(self, collection):
        if self.hide_raises:
            raise Exception("the view refused the unhide")
        for element_id in collection:
            self.hidden_elements.discard(element_id.Value)

    def CanCategoryBeHidden(self, category_id):
        return category_id.Value not in self.refuses

    def GetCategoryHidden(self, category_id):
        return self.hidden_categories.get(category_id.Value, False)

    def SetCategoryHidden(self, category_id, value):
        if self.hide_raises:
            raise Exception("the view refused the category")
        self.hidden_categories[category_id.Value] = bool(value)

    def snapshot(self):
        return (set(self.hidden_elements), dict(self.hidden_categories))

    def restore(self, state):
        self.hidden_elements = set(state[0])
        self.hidden_categories = dict(state[1])


def template_view(key, name, controls=()):
    """A view template. Revit stores the parameters NOT controlled."""
    view = FakeView(key, name, vtype="FloorPlan", is_template=True)
    view.non_controlled = [FakeId(name_)
                           for name_ in (VIS_MODEL, VIS_ANNOTATION)
                           if name_ not in controls]
    return view


class FakeSelection(object):
    def __init__(self, ids):
        self.ids = list(ids)

    def GetElementIds(self):
        return list(self.ids)


class FakeUIDoc(object):
    def __init__(self, ids):
        self.Selection = FakeSelection(ids)


class FakeDocument(object):
    def __init__(self, views, elements, active=None):
        self.views = list(views)
        self.elements = dict(elements)
        self.ActiveView = active

    def GetElement(self, element_id):
        if element_id.Value in self.elements:
            return self.elements[element_id.Value]
        for view in self.views:
            if view.Id.Value == element_id.Value:
                return view
        return None

    def snapshot(self):
        return [(view, view.snapshot()) for view in self.views]

    def restore(self, state):
        for view, saved in state:
            view.restore(saved)


class FakeOptions(object):
    def __init__(self, transaction):
        self.transaction = transaction

    def SetFailuresPreprocessor(self, processor):
        self.transaction.preprocessor = processor

    def SetClearAfterRollback(self, value):
        pass

    def SetForcedModalHandling(self, value):
        pass


class FakeTransaction(object):
    committed = []
    rolled_back = []
    started = []
    commit_status = "Committed"
    preprocessor_seen = None

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None
        self.preprocessor = None

    def Start(self):
        FakeTransaction.started.append(self.name)
        self.state = self.doc.snapshot()

    def GetFailureHandlingOptions(self):
        return FakeOptions(self)

    def SetFailureHandlingOptions(self, options):
        pass

    def Commit(self):
        FakeTransaction.preprocessor_seen = self.preprocessor
        if FakeTransaction.commit_status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        return self

    def __iter__(self):
        return iter(self.doc.views)


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeDotNetList(object):
    def __init__(self):
        self.items = []

    def Add(self, item):
        self.items.append(item)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class ListFactory(object):
    def __getitem__(self, element_type):
        return FakeDotNetList


def install_dotnet():
    system = types.ModuleType("System")
    collections = types.ModuleType("System.Collections")
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = ListFactory()
    collections.Generic = generic
    system.Collections = collections
    sys.modules["System"] = system
    sys.modules["System.Collections"] = collections
    sys.modules["System.Collections.Generic"] = generic


class IFailuresPreprocessor(object):
    """A distinct class, not object, so an MRO bug could not hide here."""


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.confirms = []
        self.picker_items = []

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)

    def confirm_text(self):
        return u"\n".join(self.confirms)


def run_script(doc, uidoc, pick=None, pick_all=False, direction="Hide",
               confirm_answer=True, shift=False, picker_raises=False,
               switch_raises=False, options_raises=False,
               commit_status="Committed", no_builtin=False,
               no_category=False):
    del MISSING_CATEGORY[:]
    if no_category:
        MISSING_CATEGORY.append(True)
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.started = []
    FakeTransaction.commit_status = commit_status
    FakeTransaction.preprocessor_seen = None

    recorder = Recorder()
    install_dotnet()

    def show_list(items, **kwargs):
        recorder.picker_items = list(items)
        if picker_raises:
            raise Exception("the picker is unavailable")
        if pick_all:
            return list(items)
        if pick is None:
            return None
        return [item for item in items if any(want in item for want in pick)]

    def show_switch(options, **kwargs):
        if switch_raises:
            raise Exception("the switch window is unavailable")
        return direction

    def alert(message, **kwargs):
        if "options" in kwargs:
            if options_raises:
                raise Exception("options dialog unavailable")
            return direction
        if kwargs.get("yes"):
            recorder.confirms.append(message)
            return confirm_answer
        recorder.alerts.append(message)
        return None

    db = Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        ElementId=FakeId,
        View=FakeView,
        FilteredElementCollector=FakeCollector,
        BuiltInParameter=Namespace(VIS_GRAPHICS_MODEL=VIS_MODEL,
                                   VIS_GRAPHICS_ANNOTATION=VIS_ANNOTATION),
        CategoryType=Namespace(Model="Model", Annotation="Annotation"),
        Category=Namespace(GetCategory=get_category),
        BuiltInCategory=(Namespace() if no_builtin
                         else Namespace(OST_Sections="OST_Sections",
                                        OST_Elev="OST_Elev",
                                        OST_Callouts="OST_Callouts")),
        FailureSeverity=Namespace(Warning="Warning"),
        FailureProcessingResult=Namespace(
            ProceedWithRollBack="RollBack", Continue="Continue"),
        IFailuresPreprocessor=IFailuresPreprocessor,
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=uidoc)
    pyrevit.DB = db
    pyrevit.forms = Namespace(
        SelectFromList=Namespace(show=show_list),
        CommandSwitchWindow=Namespace(show=show_switch),
        alert=alert)
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    pyrevit.EXEC_PARAMS = Namespace(config_mode=shift)
    sys.modules["pyrevit"] = pyrevit

    runpy.run_path(SCRIPT, run_name="__main__")
    return recorder


def walls():
    return FakeCategory(WALLS, u"Walls", "Model")


def build(elements, views, active=None):
    lookup = {}
    ids = []
    for element in elements:
        lookup[element.Id.Value] = element
        ids.append(element.Id)
    if active is None and views:
        active = views[0]
    return FakeDocument(views, lookup, active=active), FakeUIDoc(ids)


# --------------------------------------------------------------------------
# The pure model
# --------------------------------------------------------------------------

ordered = model.order_views([
    {"name": u"zulu", "vtype": u"Section", "active": False},
    {"name": u"alpha", "vtype": u"Section", "active": False},
    {"name": u"mike", "vtype": u"FloorPlan", "active": True}])
check("a bare enum name resolves",
      model.marker_category_name("Section") == u"OST_Sections")
check("a fully qualified enum name resolves the same way",
      model.marker_category_name("Autodesk.Revit.DB.ViewType.Section")
      == u"OST_Sections")
check("an unlisted view kind resolves to nothing",
      model.marker_category_name("ThreeD") is None)

check("the active view is offered first",
      [entry["name"] for entry in ordered] == [u"mike", u"alpha", u"zulu"])

v_free = {"key": 1, "name": u"Level 1", "vtype": u"FloorPlan",
          "template_controls": set(), "blocked": set(),
          "eligible": set([10, 11]), "hidden": set()}
v_owned = {"key": 2, "name": u"Level 2", "vtype": u"FloorPlan",
           "template_controls": set([model.MODEL]), "blocked": set(),
           "eligible": set([10]), "hidden": set([10])}
cats = model.merge_categories([(FakeId(WALLS), u"Walls", model.MODEL)])

labels, mapping = model.view_labels([v_free, v_owned], cats)
check("the label carries the view type", u"FloorPlan" in labels[0])
check("a template owned view is marked before it is picked",
      u"template owns V/G" in labels[1]
      and u"template owns V/G" not in labels[0])
check("the label maps back to the view it came from",
      mapping[labels[1]] is v_owned)
plain, _ = model.view_labels([v_owned], None)
check("no template marker on the element route", u"template owns" not in
      plain[0])

plan = model.element_plan([v_free, v_owned], [10, 11], True)
check("an element the view cannot show is skipped, not attempted",
      plan["skipped"] == 1)
check("an element already hidden there is left alone",
      plan["already"] == 1)
check("only the views with work to do are in the plan",
      len(plan["ready"]) == 1 and plan["ready"][0][0] is v_free)

unhide = model.element_plan([v_owned], [10], False)
check("unhide works on what is hidden",
      unhide["ready"] and unhide["ready"][0][1] == [10])
check("and never on what is not",
      model.element_plan([v_free], [10], False)["already"] == 1)

v_blocked = {"key": 3, "name": u"Level 3", "vtype": u"FloorPlan",
             "template_controls": set(), "blocked": set([WALLS]),
             "eligible": set(), "hidden": set()}
cplan = model.category_plan([v_free, v_owned, v_blocked], cats, True)
check("a view free of its template takes the category",
      len(cplan["ready"]) == 1 and cplan["ready"][0][0] is v_free)
check("a template owned view is separated, not written to",
      len(cplan["template"]) == 1)
check("a category the view refuses is separated too",
      len(cplan["blocked"]) == 1)

check("the advice names the button that can do it",
      u"Hide in Template" in model.template_advice(cplan["template"]))
check("no advice when there is nothing to advise about",
      model.template_advice([]) == u"")
check("view names are deduplicated",
      model.view_names([(v_free, []), (v_free, [])]) == [u"Level 1"])


# --------------------------------------------------------------------------
# The script, element route
# --------------------------------------------------------------------------

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
b = FakeView(2, u"Level 2")
doc, uidoc = build([element], [a, b])
recorder = run_script(doc, uidoc, pick_all=True)
check("the element is hidden in every picked view",
      10 in a.hidden_elements and 10 in b.hidden_elements)
check("one transaction, so one undo", len(FakeTransaction.started) == 1)
check("a preprocessor reached the write",
      FakeTransaction.preprocessor_seen is not None)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1", hidden_elements=(10,))
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True, direction="Unhide")
check("unhide clears the hide", 10 not in a.hidden_elements)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1", hidden_elements=(10,))
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True)
check("already hidden: no transaction is opened",
      not FakeTransaction.started)
check("already hidden: the user is told why", u"already" in recorder.text())

element = FakeElement(10, walls(), hideable_in=set([1]))
a = FakeView(1, u"Level 1")
b = FakeView(2, u"Level 2")
doc, uidoc = build([element], [a, b])
recorder = run_script(doc, uidoc, pick_all=True)
check("a view that cannot show the element is never written to",
      10 in a.hidden_elements and 10 not in b.hidden_elements)
check("and the skip is reported", u"skipped" in recorder.text())

# All or nothing
element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
b = FakeView(2, u"Level 2", hide_raises=True)
doc, uidoc = build([element], [a, b])
recorder = run_script(doc, uidoc, pick_all=True)
check("one view refusing rolls the whole run back",
      not a.hidden_elements and not b.hidden_elements)
check("and says nothing was changed in any view",
      u"Nothing was changed in any view" in recorder.text())
check("nothing is committed", not FakeTransaction.committed)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True, commit_status="RolledBack")
check("commit rejected: the view is back as it was",
      not a.hidden_elements)
check("commit rejected: the user is told nothing changed",
      u"nothing changed" in recorder.text())
check("commit rejected: nothing is reported as hidden",
      u"**Hid " not in recorder.text())
check("commit rejected: no view is listed as done",
      u"- Level 1" not in recorder.text())

# Dialogs abort, because this tool writes
element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, picker_raises=True)
check("a broken picker writes nothing", not a.hidden_elements)
check("and says so", u"could not be shown" in recorder.text())

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True, switch_raises=True,
                      options_raises=True)
check("no way to ask hide or unhide: nothing is written",
      not a.hidden_elements and not FakeTransaction.started)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True, switch_raises=True)
check("the switch window falling back to alert still works",
      10 in a.hidden_elements)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick_all=True, confirm_answer=False)
check("declining the confirmation writes nothing",
      not a.hidden_elements and not FakeTransaction.started)

element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
doc, uidoc = build([element], [a])
recorder = run_script(doc, uidoc, pick=None)
check("picking nothing writes nothing", not a.hidden_elements)

# What may not be picked
element = FakeElement(10, walls())
a = FakeView(1, u"Level 1")
sheet = FakeView(2, u"A101", vtype="DrawingSheet")
schedule = FakeView(3, u"Door Schedule", vtype="Schedule")
legend = FakeView(4, u"Legend", vtype="Legend")
tpl = template_view(5, u"AVH Plan")
doc, uidoc = build([element], [a, sheet, schedule, legend, tpl])
recorder = run_script(doc, uidoc, pick_all=True)
check("sheets, schedules and legends are never offered",
      len(recorder.picker_items) == 1, u"; ".join(recorder.picker_items))
check("view templates are never offered",
      not any(u"AVH Plan" in item for item in recorder.picker_items))

doc, uidoc = build([], [FakeView(1, u"Level 1")])
recorder = run_script(doc, uidoc, pick_all=True)
check("nothing selected: the user is told what to select",
      u"Select the elements" in recorder.text())
check("nothing selected: no transaction is opened",
      not FakeTransaction.started)


# --------------------------------------------------------------------------
# The script, category route on shift click
# --------------------------------------------------------------------------

element = FakeElement(10, walls())
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("shift click hides the category, not the element",
      free.hidden_categories.get(WALLS) and not free.hidden_elements)

element = FakeElement(10, walls())
owning = template_view(9, u"AVH Plan", controls=(VIS_MODEL,))
owned = FakeView(1, u"Level 1", template=owning)
doc, uidoc = build([element], [owned, owning])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a view whose template owns V/G is not written to",
      not owned.hidden_categories)
check("and the user is pointed at Hide in Template",
      u"Hide in Template" in recorder.text())

element = FakeElement(10, walls())
free = FakeView(1, u"Level 1", refuses=(WALLS,))
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a category the view refuses is never passed to Revit",
      not free.hidden_categories)

element = FakeElement(10, FakeCategory(ANALYTICAL, u"Analytical Walls",
                                       "AnalyticalModel"))
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("an unreachable category stops before the picker",
      u"not a category any view or template can switch off"
      in recorder.text())
check("and writes nothing", not free.hidden_categories)

# A section selected in a plan hands back the ViewSection, whose category
# is Views and whose CategoryType is neither Model nor Annotation. The
# marker is governed by the Sections annotation category instead.
VIEWS = -2000279
element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="Section", name=u"Section 79")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a selected section switches off the Sections category",
      free.hidden_categories.get(SECTIONS) is True,
      repr(free.hidden_categories))
check("the Views category itself is never written to",
      VIEWS not in free.hidden_categories)
check("the change of scope is stated before the write, not after",
      u"marker categories are used instead" in recorder.confirm_text())
check("and the resolved category is named so a wrong row is visible",
      u"Sections" in recorder.confirm_text())

element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="Elevation", name=u"East")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("an elevation resolves to its own category, not Sections",
      free.hidden_categories.get(ELEVATIONS) is True
      and SECTIONS not in free.hidden_categories)

# Every way the swap can fail names itself. Four silent returns are how
# the same dialog appeared twice with two different causes behind it.
element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="ThreeD", name=u"{3D}")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a view kind with no marker category still refuses cleanly",
      u"not a category any view or template can switch off"
      in recorder.text())
check("and writes nothing", not free.hidden_categories)
check("and the refusal names the ViewType it actually saw",
      u"ViewType ThreeD has no marker category" in recorder.text())

# The real thing: an element in the Views category with no ViewType and
# no VIEWER_VIEW_NAME. The route has to be read off what it carries.
section = FakeView(2, u"Section 79", vtype="Section")
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[FakeParameter(u"View", element_id=FakeId(2))])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, section])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a parameter holding the view's id is followed, whatever it is "
      "called", free.hidden_categories.get(SECTIONS) is True,
      repr(free.hidden_categories))
check("and the Views category is still never written to",
      VIEWS not in free.hidden_categories)

section = FakeView(2, u"Section 79", vtype="Section")
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[FakeParameter(u"Sheet Number",
                                              string=u"A101"),
                                FakeParameter(u"Name",
                                              string=u"Section 79")])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, section])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("failing that, a parameter naming the view is followed",
      free.hidden_categories.get(SECTIONS) is True)

twin_a = FakeView(2, u"Section 79", vtype="Section")
twin_b = FakeView(3, u"Section 79", vtype="Elevation")
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[FakeParameter(u"Name",
                                              string=u"Section 79")])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, twin_a, twin_b])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("two views sharing a name refuse rather than pick one",
      u"2 views carry that name" in recorder.text())
check("and write nothing", not free.hidden_categories)

# A view template has a ViewType like any view, so an id parameter
# pointing at one used to be taken as the answer. That is one of the two
# ways a section resolved to Elevations.
section = FakeView(2, u"Section 79", vtype="Section")
elevation_template = FakeView(3, u"AVH Elevation", vtype="Elevation",
                              is_template=True)
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[
                        FakeParameter(u"Template", element_id=FakeId(3)),
                        FakeParameter(u"View", element_id=FakeId(2))])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, section, elevation_template])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a view template is never taken as the view a marker stands for",
      free.hidden_categories.get(SECTIONS) is True,
      repr(free.hidden_categories))
check("so an elevation template cannot turn a section into Elevations",
      ELEVATIONS not in free.hidden_categories)

# Two parameters disagreeing is how Elevations got switched off for a
# section: the first match won and the dialog showed only the winner.
section = FakeView(2, u"Section 79", vtype="Section")
elevation = FakeView(3, u"East", vtype="Elevation")
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[
                        FakeParameter(u"Other", element_id=FakeId(3)),
                        FakeParameter(u"View", element_id=FakeId(2))])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, section, elevation])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("parameters disagreeing writes nothing at all",
      not free.hidden_categories)
check("and both kinds are named, not just the winner",
      u"Section" in recorder.text() and u"Elevation" in recorder.text())
check("and the parameters they came from are named too",
      u"Other" in recorder.text() and u"View" in recorder.text())

# A resolution that cannot be checked afterwards is one nobody can
# correct, so it is stated in the confirmation and in the output window.
section = FakeView(2, u"Section 79", vtype="Section")
viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[FakeParameter(u"View",
                                              element_id=FakeId(2))])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free, section])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("the confirmation names the view it resolved to",
      u"Section 79" in recorder.confirm_text())
check("and the parameter it came through",
      u"parameter View" in recorder.confirm_text())
check("and the output window records the resolution",
      u"what the marker points at" in recorder.text())

viewer = FakeViewer(10, FakeCategory(VIEWS, u"Views", "Internal"),
                    parameters=[FakeParameter(u"Scale", string=u"1:50",
                                              builtin=u"VIEW_SCALE")])
free = FakeView(1, u"Level 1")
doc, uidoc = build([viewer], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("an element that points at no view says so",
      u"nothing it carries points at a view" in recorder.text())
check("and prints what it actually is, rather than another sentence "
      "about it", u"what was selected" in recorder.text())
check("naming its class", u"Element" in recorder.text())
check("and listing the parameters it does carry",
      u"Scale" in recorder.text() and u"VIEW_SCALE" in recorder.text())
check("and writes nothing", not free.hidden_categories)

element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="Section", name=u"Section 79")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True,
                      no_builtin=True)
check("a BuiltInCategory this Revit lacks says which one",
      u"OST_Sections is not available" in recorder.text())

element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="Section", name=u"Section 79")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True,
                      no_category=True)
check("a model without that category says so rather than going quiet",
      u"has no OST_Sections category" in recorder.text())

# IronPython usually renders a .NET enum bare, but a qualified name must
# not silently miss the table.
element = FakeElement(10, FakeCategory(VIEWS, u"Views", "Internal"),
                      view_type="Autodesk.Revit.DB.ViewType.Section",
                      name=u"Section 79")
free = FakeView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a fully qualified ViewType still resolves",
      free.hidden_categories.get(SECTIONS) is True)

class GuardlessView(FakeView):
    """A view where CanCategoryBeHidden cannot be asked.

    This is the shape of the first real Revit run: every picked view came
    back blocked and the dialog said "None of the views you picked can
    take that", which is the same sentence Revit refusing would produce.
    """

    def CanCategoryBeHidden(self, category_id):
        raise Exception("CanCategoryBeHidden is not available")


element = FakeElement(10, walls())
free = GuardlessView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a guard that cannot be asked does not block the write",
      free.hidden_categories.get(WALLS))
check("and the reason is reported rather than swallowed",
      u"CanCategoryBeHidden could not be asked" in recorder.text())


class RefusingGuardView(FakeView):
    def CanCategoryBeHidden(self, category_id):
        return False


element = FakeElement(10, walls())
free = RefusingGuardView(1, u"Level 1")
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True)
check("a clear no from Revit still blocks", not free.hidden_categories)
check("and the dialog says Revit refused it, not just that it cannot",
      u"Revit refused" in recorder.text())


element = FakeElement(10, walls())
free = FakeView(1, u"Level 1", hidden_categories={WALLS: True})
doc, uidoc = build([element], [free])
recorder = run_script(doc, uidoc, pick_all=True, shift=True,
                      direction="Unhide")
check("shift click unhide clears a category hide",
      free.hidden_categories.get(WALLS) is False)


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
