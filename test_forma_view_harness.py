# -*- coding: utf-8 -*-
"""
Runs the real Make Forma View script against a mocked Revit.

Same reasoning as `test_remove_level_harness.py`: the failures in this
repository happen in the thin pushbutton scripts, not in the libraries
underneath them, so the script itself is what gets run here.

The fake document is behavioural rather than scripted:

- a view with a view template applied refuses category visibility
  changes, the way Revit does, so the question the script asks before it
  starts is load bearing rather than decorative
- `RollBack` restores the state captured when the transaction started,
  so a view created and then abandoned genuinely disappears
- `CreateIsometric` applies the view type's default template, so the
  case where a brand new view arrives with a template already on it is
  a real path through the code and not a hypothetical one

Run outside Revit:

    python test_forma_view_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Forma.panel",
                      "Make Forma View.pushbutton", "script.py")

from avh_forma import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

CATEGORY_NAMES = (
    # (BuiltInCategory name, display name, CategoryType)
    ("OST_Walls", u"Veggir", "Model"),
    ("OST_Floors", u"Gólf", "Model"),
    ("OST_Lines", u"Lines", "Model"),
    ("OST_RvtLinks", u"RVT Links", "Model"),
    ("OST_Coordination_Model", u"Coordination Model", "Model"),
    ("OST_Dimensions", u"Málsetningar", "Annotation"),
    ("OST_TextNotes", u"Text Notes", "Annotation"),
    ("OST_Grids", u"Grids", "Annotation"),
    ("OST_AnalyticalMember", u"Analytical Members", "AnalyticalModel"),
    ("OST_AnalyticalPanel", u"Analytical Panels", "AnalyticalModel"),
    ("OST_ImportObjectStyles", u"Imports in Families", "Model"),
)

_next_id = [100]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


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


INVALID_ID = FakeId(-1)


class FakeCategory(object):
    def __init__(self, built_in_name, name, category_type):
        self.BuiltInName = built_in_name
        self.Name = name
        self.CategoryType = category_type
        self.Id = FakeId(next_id())
        self.SubCategories = []


class FakeViewFamilyType(object):
    def __init__(self, view_family="ThreeDimensional", default_template=None):
        self.Id = FakeId(next_id())
        self.ViewFamily = view_family
        self.DefaultTemplateId = default_template or INVALID_ID


class FakeView3D(object):
    """A 3D view that behaves like one under a view template."""

    # Set for one run at a time, so the failure-in-the-middle scenario
    # can make Revit reject the name the way a clash would.
    raise_on_name = [False]

    def __init__(self, name, is_template=False, template_id=None,
                 unhideable=(), group_modes=None):
        self.Id = FakeId(next_id())
        self._name = name
        self._raise_on_name = False
        self.IsTemplate = is_template
        self.ViewTemplateId = template_id or INVALID_ID
        self.hidden = set()
        self.unhideable = set(unhideable)
        # Categories whose hide is accepted and then quietly not applied.
        self.silent_categories = set()
        self.group_modes = dict(group_modes or {})
        self._annotation_hidden = False
        self._analytical_hidden = False
        self._import_hidden = False

    @staticmethod
    def CreateIsometric(document, view_type_id):
        view_type = document.GetElement(view_type_id)
        view = FakeView3D(u"{3D} new")
        view._raise_on_name = FakeView3D.raise_on_name[0]
        if view_type is not None:
            view.ViewTemplateId = view_type.DefaultTemplateId
        document.elements.append(view)
        return view

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if self._raise_on_name:
            raise Exception("the name ELG_CC01_K is already in use")
        self._name = value

    # -- the Visibility/Graphics checkboxes ----------------------------

    def _mode(self, key):
        return self.group_modes.get(key, "normal")

    def _guard_template(self):
        if self.ViewTemplateId != INVALID_ID:
            raise Exception(
                "The visibility settings are controlled by the view template")

    @property
    def AreAnnotationCategoriesHidden(self):
        return self._annotation_hidden

    @AreAnnotationCategoriesHidden.setter
    def AreAnnotationCategoriesHidden(self, value):
        self._guard_template()
        mode = self._mode("annotation")
        if mode == "raise":
            raise AttributeError("property has no setter")
        if mode == "silent":
            return
        self._annotation_hidden = bool(value)

    @property
    def AreAnalyticalModelCategoriesHidden(self):
        return self._analytical_hidden

    @AreAnalyticalModelCategoriesHidden.setter
    def AreAnalyticalModelCategoriesHidden(self, value):
        self._guard_template()
        mode = self._mode("analytical")
        if mode == "raise":
            raise AttributeError("property has no setter")
        if mode == "silent":
            return
        self._analytical_hidden = bool(value)

    @property
    def AreImportCategoriesHidden(self):
        return self._import_hidden

    @AreImportCategoriesHidden.setter
    def AreImportCategoriesHidden(self, value):
        self._guard_template()
        mode = self._mode("imports")
        if mode == "raise":
            raise AttributeError("property has no setter")
        if mode == "silent":
            return
        self._import_hidden = bool(value)

    # -- per category visibility -------------------------------------------

    def CanCategoryBeHidden(self, category_id):
        return category_id not in self.unhideable

    def SetCategoryHidden(self, category_id, hidden):
        self._guard_template()
        if category_id in self.unhideable:
            raise Exception("category cannot be hidden in this view")
        if category_id in self.silent_categories:
            return
        if hidden:
            self.hidden.add(category_id)
        else:
            self.hidden.discard(category_id)

    def GetCategoryHidden(self, category_id):
        return category_id in self.hidden

    # -- snapshot support ---------------------------------------------------

    def snapshot(self):
        return (self._name, self.ViewTemplateId, set(self.hidden),
                self._annotation_hidden, self._analytical_hidden,
                self._import_hidden)

    def restore(self, state):
        (self._name, self.ViewTemplateId, hidden,
         self._annotation_hidden, self._analytical_hidden,
         self._import_hidden) = state
        self.hidden = set(hidden)


class FakeSettings(object):
    def __init__(self, categories):
        self.Categories = categories


class FakeDocument(object):
    def __init__(self, path_name=u"", title=u"", views=(), view_types=(),
                 missing_categories=(), missing_from_enum=(), imports=()):
        self.PathName = path_name
        self.Title = title
        # A category absent from the model is not the same thing as one
        # absent from this Revit's BuiltInCategory enum, and the script
        # is entitled to treat them differently.
        self.missing_from_enum = set(missing_from_enum)
        self.categories = [
            FakeCategory(built_in, name, category_type)
            for built_in, name, category_type in CATEGORY_NAMES
            if built_in not in missing_categories
        ]
        # Each imported DWG is a subcategory of the import parent, which
        # is how Revit models them: there is no CategoryType for imports.
        parent = self.category("OST_ImportObjectStyles")
        for import_name in imports:
            child = FakeCategory("OST_ImportObjectStyles", import_name,
                                 "Model")
            self.categories.append(child)
            if parent is not None:
                parent.SubCategories.append(child)
        self.Settings = FakeSettings([
            category for category in self.categories
            if category.BuiltInName != "OST_ImportObjectStyles"
            or category is parent])
        self.elements = list(views) + list(view_types)
        self.in_transaction = False

    def category(self, built_in_name):
        for category in self.categories:
            if category.BuiltInName == built_in_name:
                return category
        return None

    def views(self):
        return [e for e in self.elements if isinstance(e, FakeView3D)]

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def snapshot(self):
        return (list(self.elements),
                [(view, view.snapshot()) for view in self.views()])

    def restore(self, state):
        elements, view_states = state
        self.elements = list(elements)
        for view, view_state in view_states:
            view.restore(view_state)


class FakeTransaction(object):
    """Snapshots at Start, not at construction, and restores on RollBack."""

    committed = []
    rolled_back = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = self.doc.snapshot()
        self.doc.in_transaction = True

    def Commit(self):
        self.doc.in_transaction = False
        if FakeTransaction.commit_status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        self.doc.in_transaction = False
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.cls = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def __iter__(self):
        return iter([e for e in self.doc.elements if isinstance(e, self.cls)])


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def build_db(doc, name_setter_raises=False):
    """The fake `DB` module the script sees."""

    # The collector filters with isinstance, so `DB.View3D` has to be the
    # very class the existing views are instances of. Wrapping it in a
    # subclass here made every already placed view invisible to the
    # script, which read exactly like a bug in the script.
    FakeView3D.raise_on_name = [name_setter_raises]
    View3D = FakeView3D

    built_in = Namespace(**dict(
        (built_in_name, built_in_name)
        for built_in_name, _, _ in CATEGORY_NAMES
        if built_in_name not in doc.missing_from_enum))

    category_module = Namespace(
        GetCategory=staticmethod(lambda document, built_in_name:
                                 document.category(built_in_name)))

    return Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        ElementId=Namespace(InvalidElementId=INVALID_ID),
        FilteredElementCollector=FakeCollector,
        View3D=View3D,
        ViewFamilyType=FakeViewFamilyType,
        ViewFamily=Namespace(ThreeDimensional="ThreeDimensional",
                             FloorPlan="FloorPlan"),
        Category=category_module,
        BuiltInCategory=built_in,
        CategoryType=Namespace(Model="Model", Annotation="Annotation",
                               AnalyticalModel="AnalyticalModel"),
    )


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.answers = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes") or kwargs.get("no"):
            return self.answers.pop(0) if self.answers else False
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, answers=(), commit_status="Committed",
               name_setter_raises=False, uidoc=None):
    """Execute the real script against the fake Revit. Returns the Recorder."""
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.commit_status = commit_status

    recorder = Recorder()
    recorder.answers = list(answers)

    uidoc = uidoc if uidoc is not None else Namespace(ActiveView=None)

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=uidoc)
    pyrevit.DB = build_db(doc, name_setter_raises=name_setter_raises)
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

    recorder.uidoc = uidoc
    return recorder


def hidden_names(doc, view):
    return set(category.BuiltInName for category in doc.categories
               if category.Id in view.hidden)


def hidden_labels(doc, view):
    """Display names, so imported DWGs can be told apart from each other."""
    return set(category.Name for category in doc.categories
               if category.Id in view.hidden)


# --------------------------------------------------------------------------
# The naming rules, no Revit at all
# --------------------------------------------------------------------------

check("name: plain file path",
      model.view_name_from_document(
          u"C:\\Projects\\ELG_CC01_K.rvt") == u"ELG_CC01_K")
check("name: network path",
      model.view_name_from_document(
          u"\\\\server\\share\\ELG_DA11_K.rvt") == u"ELG_DA11_K")
check("name: Icelandic characters survive",
      model.view_name_from_document(u"C:\\Verk\\Hæð_módel.rvt") ==
      u"Hæð_módel")
check("name: illegal characters replaced, not dropped",
      model.view_name_from_document(u"C:\\x\\ELG[K01];v2.rvt") ==
      u"ELG_K01__v2")
check("name: only the extension is stripped",
      model.view_name_from_document(u"C:\\x\\ELG.CC01.K.rvt") ==
      u"ELG.CC01.K")
check("name: falls back to Title when the path is empty",
      model.view_name_from_document(u"", u"Project1.rvt") == u"Project1")
check("name: Title without an extension is used as is",
      model.view_name_from_document(u"", u"Project1") == u"Project1")
check("name: nothing at all gives an empty string",
      model.view_name_from_document(u"", u"") == u"")
check("name: surrounding spaces stripped",
      model.sanitize_view_name(u"  ELG  ") == u"ELG")
check("name: control characters replaced",
      model.sanitize_view_name(u"ELG\tK01") == u"ELG_K01")
check("plan: coordination models are named separately from RVT links",
      set(n for n, _ in model.HIDDEN_CATEGORIES) ==
      set([u"OST_RvtLinks", u"OST_Coordination_Model", u"OST_Lines"]))


# --------------------------------------------------------------------------
# 1. The ordinary case: nothing there yet
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()],
                   imports=[u"ELG_lóð.dwg", u"Survey.dwg"])
recorder = run_script(doc)
views = doc.views()

check("create: exactly one view made", len(views) == 1, str(len(views)))
view = views[0] if views else None
check("create: named after the model file",
      view is not None and view.Name == u"ELG_CC01_K",
      view.Name if view else u"no view")
check("create: annotation categories off",
      view is not None and view.AreAnnotationCategoriesHidden)
check("create: analytical categories off",
      view is not None and view.AreAnalyticalModelCategoriesHidden)
check("create: imported categories off",
      view is not None and view.AreImportCategoriesHidden)
check("create: linked models off",
      view is not None and u"OST_RvtLinks" in hidden_names(doc, view))
check("create: coordination models off",
      view is not None and
      u"OST_Coordination_Model" in hidden_names(doc, view))
check("create: lines off",
      view is not None and u"OST_Lines" in hidden_names(doc, view))
check("create: walls left alone",
      view is not None and u"OST_Walls" not in hidden_names(doc, view))
check("create: committed once", len(FakeTransaction.committed) == 1)
check("create: nothing rolled back", not FakeTransaction.rolled_back)
check("create: the view is made active",
      recorder.uidoc.ActiveView is view)
check("create: no alert on the happy path", not recorder.alerts,
      u" | ".join(recorder.alerts))
check("create: report says created",
      u"Created" in recorder.text())


# --------------------------------------------------------------------------
# 2. Run it again: the existing view is refreshed, not duplicated
# --------------------------------------------------------------------------

existing = FakeView3D(u"ELG_CC01_K")
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[existing, FakeView3D(u"{3D} bjornt")],
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc)

check("reuse: no second view created", len(doc.views()) == 2,
      str(len(doc.views())))
check("reuse: settings applied to the existing view",
      existing.AreAnnotationCategoriesHidden and
      u"OST_Lines" in hidden_names(doc, existing))
check("reuse: the other 3D view is untouched",
      not doc.views()[1].hidden if doc.views()[1] is not existing else True)
check("reuse: report says refreshed", u"Refreshed" in recorder.text())

# A view template on a *template* view of the same name must not be
# mistaken for the target.
template_view = FakeView3D(u"ELG_CC01_K", is_template=True)
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[template_view],
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc)
check("reuse: a view template of the same name is not reused",
      len(doc.views()) == 2 and not template_view.hidden,
      str(len(doc.views())))


# --------------------------------------------------------------------------
# 3. A view template is in the way and the user says no
# --------------------------------------------------------------------------

template = FakeView3D(u"AVH 3D standard", is_template=True)
existing = FakeView3D(u"ELG_CC01_K", template_id=template.Id)
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[template, existing],
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc, answers=[False])

check("template declined: nothing hidden", not existing.hidden)
check("template declined: annotation left alone",
      not existing.AreAnnotationCategoriesHidden)
check("template declined: template still applied",
      existing.ViewTemplateId == template.Id)
check("template declined: nothing committed",
      not FakeTransaction.committed)
check("template declined: the template is named in the question",
      u"AVH 3D standard" in recorder.text())
check("template declined: user told nothing changed",
      u"Nothing was changed" in recorder.text())


# --------------------------------------------------------------------------
# 4. A view template is in the way and the user says yes
# --------------------------------------------------------------------------

template = FakeView3D(u"AVH 3D standard", is_template=True)
existing = FakeView3D(u"ELG_CC01_K", template_id=template.Id)
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[template, existing],
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc, answers=[True])

check("template accepted: template removed",
      existing.ViewTemplateId == INVALID_ID)
check("template accepted: settings applied",
      existing.AreAnnotationCategoriesHidden and
      hidden_names(doc, existing) >= set([u"OST_RvtLinks", u"OST_Lines"]))
check("template accepted: committed", len(FakeTransaction.committed) == 1)


# --------------------------------------------------------------------------
# 5. A brand new view arrives with the view type's default template on it
# --------------------------------------------------------------------------

template = FakeView3D(u"AVH 3D standard", is_template=True)
view_type = FakeViewFamilyType(default_template=template.Id)
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[template], view_types=[view_type])
recorder = run_script(doc, answers=[True])
new_views = [v for v in doc.views() if v is not template]

check("default template: the user was asked",
      u"AVH 3D standard" in recorder.text())
check("default template: removed from the new view",
      len(new_views) == 1 and new_views[0].ViewTemplateId == INVALID_ID)
check("default template: settings applied",
      len(new_views) == 1 and new_views[0].AreAnnotationCategoriesHidden)

# Declining leaves no half made view behind.
template = FakeView3D(u"AVH 3D standard", is_template=True)
view_type = FakeViewFamilyType(default_template=template.Id)
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   views=[template], view_types=[view_type])
recorder = run_script(doc, answers=[False])
check("default template declined: no view left behind",
      len(doc.views()) == 1, str(len(doc.views())))


# --------------------------------------------------------------------------
# 6. Revit rejects the changes on commit
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc, commit_status="RolledBack")

check("commit rejected: user is told nothing changed",
      u"nothing was changed" in recorder.text().lower(),
      u" | ".join(recorder.alerts))
check("commit rejected: not reported as created",
      u"Created" not in recorder.text())
check("commit rejected: no view survives", not doc.views(),
      str(len(doc.views())))


# --------------------------------------------------------------------------
# 7. A category the view will not let go of
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])
lines_id = doc.category("OST_Lines").Id


class StubbornView(FakeView3D):
    pass


original_create = None
saved = FakeView3D.__init__


def stubborn_init(self, *args, **kwargs):
    saved(self, *args, **kwargs)
    self.unhideable = set([lines_id])


FakeView3D.__init__ = stubborn_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

view = doc.views()[0] if doc.views() else None
check("stubborn category: the run still commits",
      len(FakeTransaction.committed) == 1)
check("stubborn category: everything else still hidden",
      view is not None and
      u"OST_RvtLinks" in hidden_names(doc, view) and
      view.AreAnnotationCategoriesHidden)
check("stubborn category: lines reported as a problem",
      u"lines" in recorder.text().lower() and
      u"will not allow" in recorder.text())
check("stubborn category: the user is warned rather than left to notice",
      any(u"could not be applied" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))


# --------------------------------------------------------------------------
# 7b. A category hide that is accepted and then quietly not applied
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])
links_id = doc.category("OST_RvtLinks").Id


def silent_category_init(self, *args, **kwargs):
    saved(self, *args, **kwargs)
    self.silent_categories = set([links_id])


FakeView3D.__init__ = silent_category_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

view = doc.views()[0] if doc.views() else None
check("silent category: caught by the read back",
      u"the hide did not take" in recorder.text(), recorder.text()[:200])
check("silent category: reported against linked models",
      u"linked models: the hide did not take" in recorder.text())
check("silent category: lines still hidden",
      view is not None and u"OST_Lines" in hidden_names(doc, view))
check("silent category: the user is warned",
      any(u"could not be applied" in alert for alert in recorder.alerts))


# --------------------------------------------------------------------------
# 8. A property with no setter falls back to hiding categories one by one
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])


def raising_init(self, *args, **kwargs):
    saved(self, *args, **kwargs)
    self.group_modes = {"analytical": "raise"}


FakeView3D.__init__ = raising_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

view = doc.views()[0] if doc.views() else None
hidden = hidden_names(doc, view) if view else set()
check("property missing: analytical categories hidden individually",
      set([u"OST_AnalyticalMember", u"OST_AnalyticalPanel"]) <= hidden,
      u", ".join(sorted(hidden)))
check("property missing: annotation still went through the property",
      view is not None and view.AreAnnotationCategoriesHidden)
check("property missing: model categories not swept up",
      u"OST_Walls" not in hidden)
check("property missing: the fallback is reported, not hidden",
      u"one at a time" in recorder.text())
check("property missing: still committed", len(FakeTransaction.committed) == 1)


# --------------------------------------------------------------------------
# 8b. No import property: every imported DWG hidden one at a time
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()],
                   imports=[u"ELG_lóð.dwg", u"Survey.dwg"])


def import_raising_init(self, *args, **kwargs):
    saved(self, *args, **kwargs)
    self.group_modes = {"imports": "raise"}


FakeView3D.__init__ = import_raising_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

view = doc.views()[0] if doc.views() else None
labels = hidden_labels(doc, view) if view else set()
check("no import property: each imported DWG hidden",
      set([u"ELG_lóð.dwg", u"Survey.dwg"]) <= labels,
      u", ".join(sorted(labels)))
check("no import property: the parent category hidden too",
      u"Imports in Families" in labels)
check("no import property: annotation still went through its property",
      view is not None and view.AreAnnotationCategoriesHidden)
check("no import property: model categories not swept up",
      u"OST_Walls" not in hidden_names(doc, view))
check("no import property: still committed",
      len(FakeTransaction.committed) == 1)
check("no import property: no warning, the fallback did the job",
      not any(u"could not be applied" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))

# A model with no imports at all is the ordinary case, not a failure.
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()],
                   missing_categories=("OST_ImportObjectStyles",))
FakeView3D.__init__ = import_raising_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

check("no imports in the model: not reported as a failure",
      not any(u"could not be applied" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))
check("no imports in the model: said so rather than claiming a hide",
      u"there being none in this model" in recorder.text())
check("no imports in the model: still committed",
      len(FakeTransaction.committed) == 1)

# A Revit with no import category at all is a different thing, and with
# the property already unusable there is then nothing left to hide them
# with, so it has to be said out loud.
doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()],
                   missing_from_enum=("OST_ImportObjectStyles",),
                   missing_categories=("OST_ImportObjectStyles",))
FakeView3D.__init__ = import_raising_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

check("no import category in this Revit: warned about",
      any(u"could not be applied" in alert for alert in recorder.alerts),
      u" | ".join(recorder.alerts))
check("no import category in this Revit: named in the report",
      u"OST_ImportObjectStyles not in this Revit version" in recorder.text())


# --------------------------------------------------------------------------
# 9. A property that accepts the value and does not keep it
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])


def silent_init(self, *args, **kwargs):
    saved(self, *args, **kwargs)
    self.group_modes = {"annotation": "silent"}


FakeView3D.__init__ = silent_init
try:
    recorder = run_script(doc)
finally:
    FakeView3D.__init__ = saved

view = doc.views()[0] if doc.views() else None
hidden = hidden_names(doc, view) if view else set()
check("silent property: caught by the read back",
      u"did not take the value" in recorder.text())
check("silent property: annotation categories hidden the other way",
      set([u"OST_Dimensions", u"OST_TextNotes", u"OST_Grids"]) <= hidden,
      u", ".join(sorted(hidden)))


# --------------------------------------------------------------------------
# 10. A Revit without the coordination model category
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()],
                   missing_from_enum=("OST_Coordination_Model",),
                   missing_categories=("OST_Coordination_Model",))
recorder = run_script(doc)
view = doc.views()[0] if doc.views() else None

check("missing category: run still commits",
      len(FakeTransaction.committed) == 1)
check("missing category: reported as absent, not as a failure",
      u"not in this" in recorder.text() and
      not any(u"could not be applied" in alert for alert in recorder.alerts))
check("missing category: links and lines still hidden",
      view is not None and
      set([u"OST_RvtLinks", u"OST_Lines"]) <= hidden_names(doc, view))


# --------------------------------------------------------------------------
# 11. Models and documents that cannot be worked with
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"", title=u"",
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc)
check("unsaved model: stopped with a message",
      u"Save the model first" in recorder.text())
check("unsaved model: no view created", not doc.views())
check("unsaved model: no transaction", not FakeTransaction.committed)

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType(view_family="FloorPlan")])
recorder = run_script(doc)
check("no 3D view type: stopped with a message",
      u"no 3D view type" in recorder.text())
check("no 3D view type: nothing committed", not FakeTransaction.committed)


# --------------------------------------------------------------------------
# 12. Something throws in the middle of the write
# --------------------------------------------------------------------------

doc = FakeDocument(path_name=u"C:\\Verk\\ELG_CC01_K.rvt",
                   view_types=[FakeViewFamilyType()])
recorder = run_script(doc, name_setter_raises=True)

check("mid run failure: rolled back", len(FakeTransaction.rolled_back) == 1)
check("mid run failure: nothing committed", not FakeTransaction.committed)
check("mid run failure: no half made view left",
      not doc.views(), str(len(doc.views())))
check("mid run failure: the Revit message reaches the user",
      u"already in use" in recorder.text(), u" | ".join(recorder.alerts))


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
