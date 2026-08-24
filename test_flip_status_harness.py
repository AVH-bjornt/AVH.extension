# -*- coding: utf-8 -*-
"""
Runs the real Flip Status script against a mocked Revit.

The fake parameters hold their values across a run, so the test that
matters most here is possible at all: running twice must write nothing
the second time.

It also models groups the way Revit does, which 2.14.0's version did
not. Writing an instance parameter onto a grouped element whose
parameter does not vary across group instances posts a failure, and a
run with no failure preprocessor is recorded as having reached the
Ungroup dialog. `UNGROUP_DIALOG` must be False at the end of every
scenario. That omission is why 2.14.0 shipped a tool that offered to
dissolve Björn's groups.

Writing twice matters for a different reason: a tool that rewrites the
same number to every element on every run marks the whole model as
modified, and on a workshared job that turns a check into a sync.

Rollback restores the values captured when the transaction started, so a
commit Revit rejects genuinely leaves the model alone.

Run outside Revit:

    python test_flip_status_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel",
                      "Flip Status.pushbutton", "script.py")

from avh_flips import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

AREA = "SpecTypeId.Area"
LENGTH = "SpecTypeId.Length"


class FakeDefinition(object):
    def __init__(self, data_type=AREA, varies=True, has_data_type=True,
                 set_vary_raises=False):
        self.data_type = data_type
        self.VariesAcrossGroups = varies
        self.has_data_type = has_data_type
        self.set_vary_raises = set_vary_raises
        self.vary_calls = 0

    def GetDataType(self):
        if not self.has_data_type:
            raise Exception("GetDataType is not available in this API")
        return self.data_type

    def SetAllowVaryBetweenGroups(self, doc, value):
        if self.set_vary_raises:
            raise Exception("the parameter binding is not editable")
        self.VariesAcrossGroups = bool(value)
        self.vary_calls += 1


class FakeParameter(object):
    def __init__(self, value=0.0, storage="Double", data_type=AREA,
                 read_only=False, varies=True, has_data_type=True,
                 set_returns=True, set_raises=False,
                 set_vary_raises=False):
        self.value = value
        self.StorageType = storage
        self.IsReadOnly = read_only
        self.Definition = FakeDefinition(data_type, varies, has_data_type,
                                         set_vary_raises)
        self.set_returns = set_returns
        self.set_raises = set_raises
        self.writes = 0
        # Set when the element builds its parameters, so a write can tell
        # whether it is editing something inside a group.
        self.element = None

    def AsDouble(self):
        return self.value

    def Set(self, value):
        if self.set_raises:
            raise Exception("the element is owned by another user")
        if not self.set_returns:
            return False
        # Revit's own rule: changing an instance parameter inside a group
        # is a change to the group unless the parameter varies across
        # group instances. The failure is posted, not raised.
        if (self.element is not None and self.element.grouped and
                not self.Definition.VariesAcrossGroups):
            FAILURES.append(
                u"Changes to groups are allowed only in group edit mode.")
        self.value = value
        self.writes += 1
        return True


class FakeId(object):
    def __init__(self, value):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


INVALID_ID = FakeId(-1)

# Posted by a write Revit would refuse, and drained by the transaction.
FAILURES = []

# Set when a transaction with failures had no preprocessor to catch them,
# which is the moment Revit would show the user the Ungroup dialog. It
# must never be True.
UNGROUP_DIALOG = [False]


class FakeCategory(object):
    def __init__(self, name):
        self.Name = name


class FakeElement(object):
    def __init__(self, category, mirrored=False, hand=False, facing=False,
                 parameters=None, missing=(), raises_on=(), grouped=False):
        self.Category = FakeCategory(category)
        self.grouped = grouped
        self.GroupId = FakeId(77) if grouped else INVALID_ID
        self._state = {"Mirrored": mirrored, "HandFlipped": hand,
                       "FacingFlipped": facing}
        self.raises_on = set(raises_on)
        self.parameters = {}
        for name, _key, _label in model.PARAMETERS:
            if name in missing:
                continue
            if parameters and name in parameters:
                self.parameters[name] = parameters[name]
            else:
                self.parameters[name] = FakeParameter()
            self.parameters[name].element = self

    def _read(self, attribute):
        if attribute in self.raises_on:
            raise Exception("{0} is not available".format(attribute))
        return self._state[attribute]

    @property
    def Mirrored(self):
        return self._read("Mirrored")

    @property
    def HandFlipped(self):
        return self._read("HandFlipped")

    @property
    def FacingFlipped(self):
        return self._read("FacingFlipped")

    def LookupParameter(self, name):
        return self.parameters.get(name)

    def snapshot(self):
        return dict((name, parameter.value)
                    for name, parameter in self.parameters.items())

    def restore(self, state):
        for name, value in state.items():
            self.parameters[name].value = value

    def values(self):
        return dict((name, parameter.value)
                    for name, parameter in self.parameters.items())

    def total_writes(self):
        return sum(parameter.writes
                   for parameter in self.parameters.values())


class FakeDocument(object):
    def __init__(self, elements=(), is_family=False):
        self.elements = list(elements)
        self.IsFamilyDocument = is_family

    def snapshot(self):
        return [(element, element.snapshot()) for element in self.elements]

    def restore(self, state):
        for element, values in state:
            element.restore(values)


class FakeFailureMessage(object):
    def __init__(self, text):
        self.text = text

    def GetDescriptionText(self):
        return self.text


class FakeFailuresAccessor(object):
    def __init__(self, messages):
        self.messages = messages

    def GetFailureMessages(self):
        return [FakeFailureMessage(text) for text in self.messages]

    def GetSeverity(self):
        return "Error"


class FakeFailureOptions(object):
    def __init__(self):
        self.preprocessor = None
        self.clear_after_rollback = False

    def SetFailuresPreprocessor(self, preprocessor):
        self.preprocessor = preprocessor
        return self

    def SetClearAfterRollback(self, value):
        self.clear_after_rollback = bool(value)
        return self


class FakeTransaction(object):
    committed = []
    rolled_back = []
    commit_status = "Committed"

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.state = None
        self.options = FakeFailureOptions()

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options

    def Start(self):
        self.state = self.doc.snapshot()
        del FAILURES[:]

    def Commit(self):
        # Failures posted during the transaction are resolved at commit.
        # With no preprocessor Revit puts the dialog in front of the user,
        # which is the thing this tool must never do.
        if FAILURES:
            messages = list(FAILURES)
            del FAILURES[:]
            preprocessor = self.options.preprocessor
            if preprocessor is None:
                UNGROUP_DIALOG[0] = True
                self.doc.restore(self.state)
                FakeTransaction.rolled_back.append(self.name)
                return "RolledBack"
            result = preprocessor.PreprocessFailures(
                FakeFailuresAccessor(messages))
            if result == "ProceedWithRollBack":
                self.doc.restore(self.state)
                FakeTransaction.rolled_back.append(self.name)
                return "RolledBack"

        if FakeTransaction.commit_status != "Committed":
            self.doc.restore(self.state)
            FakeTransaction.rolled_back.append(self.name)
            return FakeTransaction.commit_status
        FakeTransaction.committed.append(self.name)
        return "Committed"

    def RollBack(self):
        del FAILURES[:]
        self.doc.restore(self.state)
        FakeTransaction.rolled_back.append(self.name)
        return "RolledBack"


class FakeMulticategoryFilter(object):
    def __init__(self, built_ins):
        self.categories = set(built_ins)


class FakeCollector(object):
    """Behavioural: WherePasses actually filters by category label."""

    LABELS = dict((name, label) for name, label in model.CATEGORIES)

    def __init__(self, doc):
        self.doc = doc
        self.elements = list(doc.elements)

    def OfClass(self, cls):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def WherePasses(self, element_filter):
        wanted = set(FakeCollector.LABELS.get(name, name)
                     for name in element_filter.categories)
        self.elements = [element for element in self.elements
                         if element.Category.Name in wanted]
        return self

    def __iter__(self):
        return iter(self.elements)


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


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.questions = []
        self.answers = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)
        if kwargs.get("yes") or kwargs.get("no"):
            self.questions.append(message)
            return self.answers.pop(0) if self.answers else False
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, commit_status="Committed", missing_from_enum=(),
               answers=()):
    FakeTransaction.committed = []
    FakeTransaction.rolled_back = []
    FakeTransaction.commit_status = commit_status
    del FAILURES[:]

    recorder = Recorder()
    recorder.answers = list(answers)
    install_dotnet()

    built_in = Namespace(**dict(
        (name, name) for name, _label in model.CATEGORIES
        if name not in missing_from_enum))

    db = Namespace(
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed",
                                    RolledBack="RolledBack"),
        FilteredElementCollector=FakeCollector,
        ElementMulticategoryFilter=FakeMulticategoryFilter,
        BuiltInCategory=built_in,
        FamilyInstance=FakeElement,
        StorageType=Namespace(Double="Double", Integer="Integer",
                              String="String"),
        SpecTypeId=Namespace(Area=AREA, Length=LENGTH),
        ElementId=Namespace(InvalidElementId=INVALID_ID),
        IFailuresPreprocessor=object,
        FailureProcessingResult=Namespace(
            Continue="Continue",
            ProceedWithRollBack="ProceedWithRollBack"),
        FailureSeverity=Namespace(Error="Error", Warning="Warning"),
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc, uidoc=Namespace(ActiveView=None))
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

    return recorder


# --------------------------------------------------------------------------
# The plain data, no Revit
# --------------------------------------------------------------------------

values = model.desired_values(
    {"mirrored": True, "hand": False, "facing": True})
check("values: mirrored true is 1", values[model.MIRRORED] == 1.0)
check("values: hand false is 0", values[model.HAND] == 0.0)
check("values: facing true is 1", values[model.FACING] == 1.0)
check("values: a state that could not be read counts as not flipped",
      model.desired_values({})[model.MIRRORED] == 0.0)

check("write: the same value is not rewritten",
      not model.needs_write(1.0, 1.0))
check("write: a different value is written",
      model.needs_write(0.0, 1.0))
check("write: an unreadable value is written",
      model.needs_write(None, 0.0))
check("write: floating point noise is not a change",
      not model.needs_write(1.0 + 1e-12, 1.0))

tally = model.Tally()
tally.count_element(u"Doors", {"mirrored": True, "hand": True})
tally.count_element(u"Doors", {})
tally.count_element(u"Windows", {"facing": True})
check("tally: rows in the order categories appeared",
      [row[0] for row in tally.rows()] == [u"Doors", u"Windows"])
check("tally: counts per state", tally.rows()[0] == (u"Doors", 2, 1, 1, 0),
      str(tally.rows()[0]))
check("tally: a model with nothing flipped is recognised",
      not model.Tally().any_flipped())

problems = model.Problems()
problems.add(u"Doors", model.HAND, model.Problems.MISSING)
problems.add(u"Doors", model.HAND, model.Problems.MISSING)
problems.add(u"Windows", model.HAND, model.Problems.MISSING)
check("problems: the same problem is reported once", len(problems) == 2)
check("problems: the line names category and parameter",
      u"Doors" in problems.lines()[0] and model.HAND in problems.lines()[0])


# --------------------------------------------------------------------------
# 1. The ordinary run
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True, hand=True)
window = FakeElement(u"Windows", facing=True)
casework = FakeElement(u"Casework")
doc = FakeDocument([door, window, casework])
recorder = run_script(doc)

check("run: mirrored written as 1", door.values()[model.MIRRORED] == 1.0)
check("run: hand flip written as 1", door.values()[model.HAND] == 1.0)
check("run: facing not flipped stays 0", door.values()[model.FACING] == 0.0)
check("run: facing flip recorded on the window",
      window.values()[model.FACING] == 1.0)
check("run: an element with nothing flipped is not written",
      casework.total_writes() == 0)
check("run: committed once", len(FakeTransaction.committed) == 1)
check("run: the table names each category",
      u"Doors" in recorder.text() and u"Windows" in recorder.text())
check("run: counts updated and already correct separately",
      u"2 element(s) updated, 1 already correct" in recorder.text(),
      recorder.text()[-300:])
check("run: no alert when nothing is wrong", not recorder.alerts,
      u" | ".join(recorder.alerts))

# The whole point of separating the three: Engipedia records only the
# first, so a hand flipped door that is not mirrored looks correct there.
hand_only = FakeElement(u"Doors", mirrored=False, hand=True)
doc = FakeDocument([hand_only])
recorder = run_script(doc)
check("hand flip without a mirror is still recorded",
      hand_only.values()[model.HAND] == 1.0 and
      hand_only.values()[model.MIRRORED] == 0.0)


# --------------------------------------------------------------------------
# 2. Running it twice must not touch the model again
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True)
doc = FakeDocument([door])
run_script(doc)
first = door.total_writes()
recorder = run_script(doc)

check("idempotent: the first run wrote", first > 0, str(first))
check("idempotent: the second run wrote nothing",
      door.total_writes() == first, str(door.total_writes()))
check("idempotent: and said everything was already correct",
      u"0 element(s) updated, 1 already correct" in recorder.text(),
      recorder.text()[-200:])


# --------------------------------------------------------------------------
# 3. Parameters that cannot take the value
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True, missing=(model.HAND,))
doc = FakeDocument([door])
recorder = run_script(doc)
check("missing parameter: the others are still written",
      door.values()[model.MIRRORED] == 1.0)
check("missing parameter: reported with category and name",
      u"not bound to this category" in recorder.text() and
      model.HAND in recorder.text())
check("missing parameter: the user is told, not left to notice",
      any(u"parameter problem" in alert for alert in recorder.alerts))

door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(data_type=LENGTH)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("length parameter: refused rather than written",
      door.values()[model.MIRRORED] == 0.0)
check("length parameter: reported as not an Area parameter",
      u"not an Area parameter" in recorder.text())

door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(read_only=True)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("read only parameter: reported", u"read only" in recorder.text())
check("read only parameter: nothing written to it",
      door.values()[model.MIRRORED] == 0.0)

# Older API with no GetDataType: fall back to the storage type.
door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(has_data_type=False)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("no GetDataType: still written on the storage type alone",
      door.values()[model.MIRRORED] == 1.0)
check("no GetDataType: not reported as a problem",
      u"not an Area parameter" not in recorder.text())

# A model with no groups is not made better by a dialog about groups.
door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(varies=False)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("no groups in the model: not asked about the vary flag",
      not recorder.questions, u" | ".join(recorder.questions))
check("no groups in the model: written normally",
      door.values()[model.MIRRORED] == 1.0)


# --------------------------------------------------------------------------
# 3b. Groups, which is what 2.14.0 walked into
# --------------------------------------------------------------------------

def grouped_model():
    """One grouped door and one loose door, sharing a definition state."""
    parameters = dict(
        (name, FakeParameter(varies=False))
        for name, _key, _label in model.PARAMETERS)
    grouped = FakeElement(u"Doors", mirrored=True, grouped=True,
                          parameters=parameters)
    loose = FakeElement(u"Doors", mirrored=True)
    return grouped, loose, FakeDocument([grouped, loose])


# Answering yes sets the flag, and then everything is written.
grouped, loose, doc = grouped_model()
recorder = run_script(doc, answers=[True])
definition = grouped.parameters[model.MIRRORED].Definition

check("grouped, yes: the user was asked once", len(recorder.questions) == 1,
      str(len(recorder.questions)))
check("grouped, yes: the question names the parameters",
      model.MIRRORED in recorder.questions[0] if recorder.questions else False)
check("grouped, yes: the flag was set", definition.VariesAcrossGroups)
check("grouped, yes: in its own transaction before the write",
      len(FakeTransaction.committed) == 2, str(FakeTransaction.committed))
check("grouped, yes: the grouped element was written",
      grouped.values()[model.MIRRORED] == 1.0)
check("grouped, yes: the loose element was written",
      loose.values()[model.MIRRORED] == 1.0)
check("grouped, yes: the report says the flag was switched on",
      u"Vary across group instances was switched on" in recorder.text())
check("grouped, yes: no ungroup dialog", not UNGROUP_DIALOG[0])

# Answering no skips the grouped elements and writes the rest.
grouped, loose, doc = grouped_model()
recorder = run_script(doc, answers=[False])
definition = grouped.parameters[model.MIRRORED].Definition

check("grouped, no: the flag is left alone", not definition.VariesAcrossGroups)
check("grouped, no: the grouped element is not written",
      grouped.values()[model.MIRRORED] == 0.0)
check("grouped, no: the loose element is still written",
      loose.values()[model.MIRRORED] == 1.0)
check("grouped, no: the skip is counted and explained",
      u"inside groups were skipped" in recorder.text(), recorder.text()[-400:])
check("grouped, no: committed", len(FakeTransaction.committed) == 1)
check("grouped, no: no ungroup dialog", not UNGROUP_DIALOG[0])

# The flag already on: no question, everything written.
parameters = dict((name, FakeParameter(varies=True))
                  for name, _key, _label in model.PARAMETERS)
grouped = FakeElement(u"Doors", mirrored=True, grouped=True,
                      parameters=parameters)
doc = FakeDocument([grouped])
recorder = run_script(doc)
check("grouped, flag already on: not asked", not recorder.questions)
check("grouped, flag already on: written",
      grouped.values()[model.MIRRORED] == 1.0)
check("grouped, flag already on: no ungroup dialog", not UNGROUP_DIALOG[0])

# Setting the flag fails: fall back to skipping rather than pressing on.
parameters = dict(
    (name, FakeParameter(varies=False, set_vary_raises=True))
    for name, _key, _label in model.PARAMETERS)
grouped = FakeElement(u"Doors", mirrored=True, grouped=True,
                      parameters=parameters)
loose = FakeElement(u"Doors", mirrored=True)
doc = FakeDocument([grouped, loose])
recorder = run_script(doc, answers=[True])

check("flag cannot be set: the user is told",
      u"could not be set" in recorder.text(), u" | ".join(recorder.alerts))
check("flag cannot be set: the grouped element is skipped, not forced",
      grouped.values()[model.MIRRORED] == 0.0)
check("flag cannot be set: the loose element is still written",
      loose.values()[model.MIRRORED] == 1.0)
check("flag cannot be set: no ungroup dialog", not UNGROUP_DIALOG[0])

# The guard itself: a group failure that gets past every check must roll
# the run back rather than reach the user. Modelled by a definition that
# claims to vary and refuses anyway, which is what a nested group or an
# attached detail group can do.
class LyingParameter(FakeParameter):
    def Set(self, value):
        FAILURES.append(
            u"Changes to groups are allowed only in group edit mode.")
        self.value = value
        self.writes += 1
        return True


parameters = dict((name, LyingParameter(varies=True))
                  for name, _key, _label in model.PARAMETERS)
grouped = FakeElement(u"Doors", mirrored=True, grouped=True,
                      parameters=parameters)
doc = FakeDocument([grouped])
recorder = run_script(doc)

check("guard: the run was rolled back", len(FakeTransaction.rolled_back) == 1)
check("guard: nothing was written",
      grouped.values()[model.MIRRORED] == 0.0)
check("guard: Revit's own words reach the user",
      u"group edit mode" in recorder.text(), recorder.text()[-300:])
check("guard: the ungroup dialog was never reached", not UNGROUP_DIALOG[0])


# --------------------------------------------------------------------------
# 4. Writes Revit refuses
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(set_returns=False)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("Set returning false: counted as a rejected write",
      u"had a write rejected" in recorder.text(), recorder.text()[-300:])
check("Set returning false: not counted as updated",
      u"0 element(s) updated" in recorder.text())

door = FakeElement(u"Doors", mirrored=True, parameters={
    model.MIRRORED: FakeParameter(set_raises=True)})
doc = FakeDocument([door])
recorder = run_script(doc)
check("Set raising: Revit's own words reach the report",
      u"owned by another user" in recorder.text())
check("Set raising: the run still finishes and commits",
      len(FakeTransaction.committed) == 1)


# --------------------------------------------------------------------------
# 5. Revit refusing the commit
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True)
doc = FakeDocument([door])
recorder = run_script(doc, commit_status="RolledBack")
check("commit rejected: the value is back as it was",
      door.values()[model.MIRRORED] == 0.0)
check("commit rejected: user told nothing was written",
      u"nothing was written" in recorder.text().lower())
check("commit rejected: no report claiming updates",
      u"element(s) updated" not in recorder.text())


# --------------------------------------------------------------------------
# 6. Documents and categories that do not apply
# --------------------------------------------------------------------------

doc = FakeDocument([FakeElement(u"Doors")], is_family=True)
recorder = run_script(doc)
check("family document: refused",
      u"not on a family document" in recorder.text())
check("family document: nothing committed", not FakeTransaction.committed)

doc = FakeDocument([FakeElement(u"Walls")])
recorder = run_script(doc)
check("nothing in scope: said so",
      u"No family instances" in recorder.text())
check("nothing in scope: walls are not touched",
      not FakeTransaction.committed)

door = FakeElement(u"Doors", mirrored=True)
doc = FakeDocument([door, FakeElement(u"Casework")])
recorder = run_script(doc, missing_from_enum=("OST_Casework",))
check("category missing from this Revit: noted",
      u"Not in this Revit version" in recorder.text() and
      u"Casework" in recorder.text())
check("category missing from this Revit: the rest still run",
      door.values()[model.MIRRORED] == 1.0)


# --------------------------------------------------------------------------
# 7. A property that will not answer
# --------------------------------------------------------------------------

door = FakeElement(u"Doors", mirrored=True, hand=True,
                   raises_on=("HandFlipped",))
doc = FakeDocument([door])
recorder = run_script(doc)
check("unreadable flip property: treated as not flipped",
      door.values()[model.HAND] == 0.0)
check("unreadable flip property: the rest of the element still written",
      door.values()[model.MIRRORED] == 1.0)


# --------------------------------------------------------------------------

check("no scenario ever reached the ungroup dialog", not UNGROUP_DIALOG[0])

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
