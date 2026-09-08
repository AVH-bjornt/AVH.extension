# -*- coding: utf-8 -*-
"""
Runs the real Hide in Template script against a mocked Revit.

The fake templates are behavioural. A template holds its own hidden
category state and its own set of non controlled parameter ids, and a
write only shows up in that state if the script actually made it, so
"wrote to a template that does not control V/G" is a thing the harness
can see rather than a thing the reader has to notice.

A rollback restores every view to the state it had when that transaction
started, so a refused write genuinely leaves the model alone.

Run outside Revit:

    python test_hide_in_template_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Hide in Template.pushbutton", "script.py")

from avh_visibility import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


VIS_MODEL = "VIS_GRAPHICS_MODEL"
VIS_ANNOTATION = "VIS_GRAPHICS_ANNOTATION"

WALLS = -2000011
DOORS = -2000023
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
VIEWS = -2000279

MARKER_CATEGORIES = {"OST_Sections": (SECTIONS, u"Sections")}


def get_category(doc, builtin):
    found = MARKER_CATEGORIES.get(builtin)
    if found is None:
        return None
    return FakeCategory(found[0], found[1], "Annotation")


class FakeElement(object):
    def __init__(self, category, view_type=None, name=u""):
        self.Category = category
        self.Name = name
        if view_type is not None:
            self.ViewType = view_type


class FakeView(object):
    """A view, or a view template when IsTemplate is True."""

    def __init__(self, key, name, is_template=False, template_id=None,
                 controls=(), available=(VIS_MODEL, VIS_ANNOTATION),
                 hidden=None, refuses=(), set_raises=False,
                 control_raises=False):
        self.Id = FakeId(key)
        self.Name = name
        self.IsTemplate = is_template
        self.ViewTemplateId = FakeId(template_id if template_id is not None
                                     else -1)
        self.available = list(available)
        # Revit stores the ones NOT controlled, so this is the inverse.
        self.non_controlled = [FakeId(name_)
                               for name_ in self.available
                               if name_ not in controls]
        self.hidden = dict(hidden or {})
        self.refuses = set(refuses)
        self.set_raises = set_raises
        self.control_raises = control_raises

    def GetTemplateParameterIds(self):
        return [FakeId(name) for name in self.available]

    def GetNonControlledTemplateParameterIds(self):
        return list(self.non_controlled)

    def SetNonControlledTemplateParameterIds(self, ids):
        if self.control_raises:
            raise Exception("the template refused the parameter change")
        self.non_controlled = list(ids)

    def controls(self, parameter_name):
        return parameter_name not in [pid.Value
                                      for pid in self.non_controlled]

    def CanCategoryBeHidden(self, category_id):
        return category_id.Value not in self.refuses

    def GetCategoryHidden(self, category_id):
        return self.hidden.get(category_id.Value, False)

    def SetCategoryHidden(self, category_id, value):
        if self.set_raises:
            raise Exception("the template refused the category")
        self.hidden[category_id.Value] = bool(value)

    def snapshot(self):
        return (list(self.non_controlled), dict(self.hidden))

    def restore(self, state):
        self.non_controlled = list(state[0])
        self.hidden = dict(state[1])


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
        return self.elements.get(element_id.Value)

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
    commit_status = {}
    preprocessors = {}

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
        FakeTransaction.preprocessors[self.name] = self.preprocessor
        status = FakeTransaction.commit_status.get(self.name, "Committed")
        if status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.items = list(doc.views)

    def OfClass(self, cls):
        return self

    def __iter__(self):
        return iter(self.items)


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
    """A distinct class, not object.

    Stubbing an interface as `object` once hid a real method resolution
    order bug in another tool, so this one is its own type and the
    harness checks that a preprocessor actually reached the write.
    """


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.confirms = []
        self.picker_items = []
        self.switch_calls = []

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)

    def confirm_text(self):
        return u"\n".join(self.confirms)


def run_script(doc, uidoc, picked=None, pick_all=False, direction="Hide",
               confirm_answer=True, offer_answer=True,
               picker_raises=False, switch_raises=False,
               alert_options_raises=False, confirm_raises=False,
               commit_status=None):
    """Execute the real script against the fake Revit."""
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.started = []
    FakeTransaction.preprocessors = {}
    FakeTransaction.commit_status = dict(commit_status or {})

    recorder = Recorder()
    install_dotnet()

    def show_list(items, **kwargs):
        recorder.picker_items = list(items)
        if picker_raises:
            raise Exception("the picker is unavailable")
        if pick_all:
            return list(items)
        if picked is None:
            return None
        return [item for item in items
                if any(want in item for want in picked)]

    def show_switch(options, **kwargs):
        recorder.switch_calls.append(list(options))
        if switch_raises:
            raise Exception("the switch window is unavailable")
        return direction

    def alert(message, **kwargs):
        title = kwargs.get("title", "")
        if "options" in kwargs:
            if alert_options_raises:
                raise Exception("options dialog unavailable")
            return direction
        if kwargs.get("yes"):
            if "take over" in title:
                return offer_answer
            if confirm_raises:
                raise Exception("confirmation unavailable")
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
        BuiltInCategory=Namespace(OST_Sections="OST_Sections",
                                  OST_Elev="OST_Elev",
                                  OST_Callouts="OST_Callouts"),
        FailureSeverity=Namespace(Warning="Warning"),
        FailureProcessingResult=Namespace(
            ProceedWithRollBack="RollBack", Continue="Continue"),
        IFailuresPreprocessor=IFailuresPreprocessor,
    )

    forms = Namespace(
        SelectFromList=Namespace(show=show_list),
        CommandSwitchWindow=Namespace(show=show_switch),
        alert=alert,
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=uidoc)
    pyrevit.DB = db
    pyrevit.forms = forms
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=recorder.print_md),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None),
    )
    sys.modules["pyrevit"] = pyrevit

    runpy.run_path(SCRIPT, run_name="__main__")
    return recorder


def committed_before(first, second):
    """True when both committed, in that order.

    Written as a check rather than an index lookup because a mutation
    that stops one of them happening should fail this check, not abort
    the whole suite with a ValueError before the later checks run.
    """
    committed = FakeTransaction.committed
    if first not in committed or second not in committed:
        return False
    return committed.index(first) < committed.index(second)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def walls_category():
    return FakeCategory(WALLS, u"Walls", "Model")


def doors_category():
    return FakeCategory(DOORS, u"Doors", "Model")


def tags_category():
    return FakeCategory(TAGS, u"Door Tags", "Annotation")


def analytical_category():
    return FakeCategory(ANALYTICAL, u"Analytical Walls", "AnalyticalModel")


def build(selection_categories, templates, plan_counts=None):
    """A document with templates and, optionally, views using them."""
    elements = {}
    ids = []
    for index, category in enumerate(selection_categories, start=1):
        elements[index] = FakeElement(category)
        ids.append(FakeId(index))

    views = list(templates)
    counter = 9000
    for template in templates:
        for _ in range((plan_counts or {}).get(template.Name, 0)):
            counter += 1
            views.append(FakeView(counter, u"plan {0}".format(counter),
                                  template_id=template.Id.Value))

    active = FakeView(8000, u"active", template_id=templates[0].Id.Value)
    views.append(active)
    doc = FakeDocument(views, elements, active=active)
    return doc, FakeUIDoc(ids)


def controlled_template(key=100, name=u"AVH Plan 1:100", **kwargs):
    kwargs.setdefault("controls", (VIS_MODEL, VIS_ANNOTATION))
    return FakeView(key, name, is_template=True, **kwargs)


def uncontrolled_template(key=200, name=u"AVH Section", **kwargs):
    kwargs.setdefault("controls", ())
    return FakeView(key, name, is_template=True, **kwargs)


# --------------------------------------------------------------------------
# The pure model
# --------------------------------------------------------------------------

merged = model.merge_categories(
    [(FakeId(WALLS), u"Walls", model.MODEL)] * 40
    + [(FakeId(DOORS), u"Doors", model.MODEL)])
check("forty doors and walls collapse to two categories", len(merged) == 2)
check("categories come back sorted by name",
      [entry["name"] for entry in merged] == [u"Doors", u"Walls"])

cats = model.merge_categories([
    (FakeId(WALLS), u"Walls", model.MODEL),
    (FakeId(TAGS), u"Door Tags", model.ANNOTATION),
    (FakeId(ANALYTICAL), u"Analytical Walls", model.OTHER)])
check("both kinds are needed when the selection mixes them",
      model.needed_kinds(cats) == set([model.MODEL, model.ANNOTATION]))
check("an analytical category is not something a template can reach",
      [entry["name"] for entry in model.unsupported(cats)]
      == [u"Analytical Walls"])

t_ok = {"key": 1, "name": u"ok", "views": 3,
        "controls": set([model.MODEL]), "blocked": set()}
t_no = {"key": 2, "name": u"no", "views": 7, "controls": set(),
        "blocked": set()}
t_blocked = {"key": 3, "name": u"blocked", "views": 2,
             "controls": set([model.MODEL]), "blocked": set([WALLS])}
walls_only = model.merge_categories([(FakeId(WALLS), u"Walls", model.MODEL)])

dry = model.plan([t_ok, t_no, t_blocked], walls_only, enable=False)
check("a controlling template is ready", len(dry["ready"]) == 1)
check("a template that does not control V/G waits for the offer",
      len(dry["pending"]) == 1)
check("a category the view refuses is set aside",
      len(dry["blocked"]) == 1 and not any(
          entry[0]["key"] == 3 for entry in dry["ready"]))

enabled = model.plan([t_ok, t_no], walls_only, enable=True)
check("accepting the offer moves it into ready", len(enabled["ready"]) == 2)
check("and records which parameter has to be switched on",
      any(entry[2] == set([model.MODEL]) for entry in enabled["ready"]))

check("the view count is what the confirmation shows",
      model.views_touched(dry["ready"] + dry["pending"]) == 10)
check("a template counted twice is still counted once",
      model.views_touched([(t_ok, [], set()), (t_ok, [], set())]) == 3)

labels, mapping = model.template_labels([t_ok, t_no], walls_only)
check("the label carries the view count", u"(7 view(s))" in labels[1])
check("and warns before the offer does",
      u"does not control V/G" in labels[1]
      and u"does not control V/G" not in labels[0])
check("the label maps back to the template it came from",
      mapping[labels[1]] is t_no)

ordered = model.order_templates([
    {"name": u"zulu", "active": False}, {"name": u"alpha", "active": False},
    {"name": u"mike", "active": True}])
check("the active view's template is offered first",
      [entry["name"] for entry in ordered] == [u"mike", u"alpha", u"zulu"])

text = model.offer_text([(t_no, walls_only, set([model.MODEL]))], u"hiding")
check("the offer names the template", u"no" in text)
check("the offer states the view count", u"7 view(s)" in text)
check("the offer says the change cannot be undone by unticking",
      u"cannot be undone" in text)


# --------------------------------------------------------------------------
# The script
# --------------------------------------------------------------------------

template = controlled_template()
doc, uidoc = build([walls_category()], [template], {u"AVH Plan 1:100": 5})
recorder = run_script(doc, uidoc, pick_all=True)
check("a controlling template takes the hide", template.hidden.get(WALLS))
check("the write is committed", "Hide in Template" in
      FakeTransaction.committed)
check("the report names the view count, the active view included",
      u"6 view(s)" in recorder.text())
check("a preprocessor reached the write transaction",
      FakeTransaction.preprocessors.get("Hide in Template") is not None)

template = controlled_template(hidden={WALLS: True})
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, direction="Unhide")
check("unhide clears a hide rather than setting one",
      template.hidden.get(WALLS) is False)

template = controlled_template(hidden={WALLS: True})
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("a template already in that state is left alone",
      u"already in that state" in recorder.text())
check("and no empty transaction is opened for it",
      "Hide in Template" not in FakeTransaction.started)

# The offer
template = uncontrolled_template()
doc, uidoc = build([walls_category()], [template], {u"AVH Section": 12})
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=False)
check("offer declined: nothing is written to that template",
      not template.hidden)
check("offer declined: the template still does not control V/G",
      not template.controls(VIS_MODEL))
check("offer declined: the user is told why it was skipped",
      u"declined" in recorder.text() or u"Nothing was left" in
      recorder.text())

template = uncontrolled_template()
doc, uidoc = build([walls_category()], [template], {u"AVH Section": 12})
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=True)
check("offer accepted: the template takes over V/G",
      template.controls(VIS_MODEL))
check("offer accepted: and the hide then lands",
      template.hidden.get(WALLS))
check("taking control is committed before the write",
      committed_before("Hide in Template: control V/G",
                       "Hide in Template"))

template = uncontrolled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=True,
                      commit_status={"Hide in Template: control V/G":
                                     "RolledBack"})
check("control rejected: nothing is hidden", not template.hidden)
check("control rejected: the user is told nothing was hidden",
      u"nothing was hidden" in recorder.text())

template = uncontrolled_template(control_raises=True)
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=True)
check("control raises: rolled back", FakeTransaction.rolled_back)
check("control raises: nothing hidden", not template.hidden)
check("control raises: Revit's own words reach the user",
      u"refused the parameter change" in recorder.text())

# A mixed pick: one template can take it, one cannot, offer declined
good = controlled_template(key=100, name=u"AVH Plan 1:100")
bad = uncontrolled_template(key=200, name=u"AVH Section")
doc, uidoc = build([walls_category()], [good, bad])
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=False)
check("declining the offer does not cost the templates that were fine",
      good.hidden.get(WALLS) and not bad.hidden)

# Blocked and unsupported
template = controlled_template(refuses=(WALLS,))
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("a category the view refuses is never passed to Revit",
      WALLS not in template.hidden)
check("and the refusal is reported",
      u"will not hide" in recorder.text() or u"Nothing was left" in
      recorder.text())

template = controlled_template()
doc, uidoc = build([analytical_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("an analytical only selection stops before any picker",
      u"not a category any template can control" in recorder.text())
check("and names the button that can do it instead",
      u"Hide Across Views" in recorder.text())
check("and writes nothing", not template.hidden)

# A template that does not offer the parameter at all
template = FakeView(300, u"AVH Schedule", is_template=True,
                    available=(), controls=())
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("a template with no V/G parameter is not offered the switch",
      not template.hidden and u"take over" not in u"".join(recorder.alerts))

# Annotation
template = controlled_template(controls=(VIS_MODEL,))
doc, uidoc = build([tags_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, offer_answer=False)
check("annotation is judged on its own parameter, not the model one",
      not template.hidden)

template = controlled_template(controls=(VIS_MODEL, VIS_ANNOTATION))
doc, uidoc = build([tags_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("and lands when that parameter is controlled",
      template.hidden.get(TAGS))

# A section selected in a plan resolves to the Sections annotation
# category, the same swap Hide Across Views makes.
template = controlled_template()
doc, uidoc = build([FakeCategory(VIEWS, u"Views", "Internal")], [template])
doc.elements[1] = FakeElement(FakeCategory(VIEWS, u"Views", "Internal"),
                              view_type="Section", name=u"Section 79")
recorder = run_script(doc, uidoc, pick_all=True)
check("a selected section switches off the Sections category in the "
      "template", template.hidden.get(SECTIONS) is True,
      repr(template.hidden))
check("the Views category itself is never written to",
      VIEWS not in template.hidden)
check("the change of scope is stated before the write",
      u"marker categories are used instead" in recorder.confirm_text())


class GuardlessTemplate(FakeView):
    """A template where CanCategoryBeHidden cannot be asked."""

    def CanCategoryBeHidden(self, category_id):
        raise Exception("CanCategoryBeHidden is not available")


template = GuardlessTemplate(100, u"AVH Plan 1:100", is_template=True,
                             controls=(VIS_MODEL, VIS_ANNOTATION))
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("a guard that cannot be asked does not block the write",
      template.hidden.get(WALLS))
check("and the reason is reported rather than swallowed",
      u"CanCategoryBeHidden could not be asked" in recorder.text())


# Write failures
template = controlled_template(set_raises=True)
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("write raises: rolled back", FakeTransaction.rolled_back)
check("write raises: Revit's own words reach the user",
      u"refused the category" in recorder.text())
check("write raises: nothing reported as changed",
      u"Hidden in" not in recorder.text())

template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True,
                      commit_status={"Hide in Template": "RolledBack"})
check("commit rejected: the template is back as it was",
      not template.hidden)
check("commit rejected: the user is told nothing changed",
      u"nothing changed" in recorder.text())
check("commit rejected: nothing reported as changed",
      u"Hidden in" not in recorder.text())

# Dialogs abort, because this tool writes
template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, picker_raises=True)
check("a broken picker writes nothing, unlike Isolate Warnings",
      not template.hidden and not FakeTransaction.committed)
check("and says so", u"could not be shown" in recorder.text())

template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, switch_raises=True,
                      alert_options_raises=True)
check("no way to ask hide or unhide: nothing is written",
      not template.hidden and not FakeTransaction.committed)

template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, switch_raises=True)
check("the switch window falling back to alert still works",
      template.hidden.get(WALLS))

template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, pick_all=True, confirm_answer=False)
check("declining the confirmation writes nothing",
      not template.hidden and not FakeTransaction.committed)

template = controlled_template()
doc, uidoc = build([walls_category()], [template])
recorder = run_script(doc, uidoc, picked=None)
check("picking nothing writes nothing", not template.hidden)

# No selection at all
template = controlled_template()
doc, uidoc = build([], [template])
recorder = run_script(doc, uidoc, pick_all=True)
check("nothing selected: the user is told what to select",
      u"Select the elements" in recorder.text())
check("nothing selected: no transaction is opened",
      not FakeTransaction.started)


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
