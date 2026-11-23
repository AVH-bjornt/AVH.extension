# -*- coding: utf-8 -*-
"""
Runs the real Rename Types script against a mocked Revit.

The window (lib/avh_typenames/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person would.
Everything the window shows comes from the `evaluate` callable the
script passes it, and that is exercised here.

The fake behaves the way Revit is expected to:

- type names persist across runs, so a second identical run must write
  nothing;
- setting a type's name to one another type in its family already has
  (ignoring case) raises, the way Revit refuses a duplicate type name,
  so swapping two names only works through a temporary name;
- a rolled back transaction, or a commit that comes back RolledBack,
  puts every name back;
- at commit a configured warning or error is posted. With a failures
  preprocessor attached they go through it; without one each is
  recorded as a dialog Revit would have shown. `doc.dialogs` must stay
  empty.

At the end the script is run again with its safety lines taken out one
at a time (in memory, nothing written), and each such mutation has to
make at least one check fail.

Run outside Revit:

    python test_rename_types_harness.py   (-v: how many checks each
                                           mutation fails)
"""

import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel",
                      "Rename Types.pushbutton", "script.py")
SOURCE = io.open(SCRIPT, encoding="utf-8").read()

import avh_typenames                    # noqa: E402
from avh_typenames import model         # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


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


INVALID = FakeId(-1)


class FakeParameter(object):
    """`value` is what AsString / AsValueString give; for an ElementId
    parameter it is the element pointed at."""

    def __init__(self, name, value=u"", storage="String"):
        self.Definition = Namespace(Name=name)
        self.StorageType = storage
        self.value = value

    @property
    def HasValue(self):
        return self.value is not None and self.value != u""

    def AsString(self):
        return self.value if self.StorageType == "String" else None

    def AsValueString(self):
        if self.StorageType == "ElementId":
            return u"<id {0}>".format(self.value.Id.Value)
        return self.value

    def AsElementId(self):
        return self.value.Id if self.value is not None else INVALID


class FakeElement(object):
    def __init__(self, doc, name, category=u"Hurðir"):
        doc.next_id += 1
        self.Id = FakeId(doc.next_id)
        self.name = name
        self.Category = Namespace(Name=category, Id=FakeId(-2000023))
        self.Parameters = []
        doc.elements.append(self)

    @property
    def Name(self):
        return self.name

    def get_Parameter(self, which):
        return None


class FakeElementType(FakeElement):
    """A type in `family` (a Namespace with Id, Name, IsInPlace), or in a
    system family called `family_name` when family is None."""

    def __init__(self, doc, family, name, params=(), family_name=None,
                 category=u"Hurðir"):
        FakeElement.__init__(self, doc, name, category)
        self.doc = doc
        if family is not None:
            self.Family = family
        self.FamilyName = family.Name if family is not None else family_name
        self.Parameters = list(params)
        self.raises = False
        self.mangle = False
        self.writes = 0

    def key(self):
        if hasattr(self, "Family"):
            return ("family", self.Family.Id.Value)
        return ("system", self.Category.Name, self.FamilyName)

    @property
    def Name(self):
        return self.name

    @Name.setter
    def Name(self, value):
        if self.raises:
            raise Exception(u"Revit segir nei")
        if not isinstance(value, type(u"")):
            raise Exception("Name given " + repr(value))
        for other in self.doc.types():
            if other is not self and other.key() == self.key() and \
                    other.name.lower() == value.lower():
                raise Exception(u"The name '{0}' is already in use"
                                .format(value))
        self.name = value.upper() if self.mangle else value
        self.writes += 1


class FakeInstance(FakeElement):
    def __init__(self, doc, element_type):
        FakeElement.__init__(self, doc, u"instance")
        self.type_id = element_type.Id

    def GetTypeId(self):
        return self.type_id


class FakeDoc(object):
    def __init__(self, commit_status="Committed", commit_error=None,
                 commit_warning=None, workshared=False):
        self.elements = []
        self.next_id = 100
        self.commit_status = commit_status
        self.commit_error = commit_error
        self.commit_warning = commit_warning
        self.IsWorkshared = workshared
        self.owned = set()
        self.selection = []
        self.log = []
        self.dialogs = []

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def types(self):
        return [e for e in self.elements if isinstance(e, FakeElementType)]

    def failures(self):
        found = []
        if self.commit_warning:
            found.append(("Warning", self.commit_warning))
        if self.commit_error:
            found.append(("Error", self.commit_error))
        return found


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def WhereElementIsElementType(self):
        return self

    def ToElements(self):
        return self.doc.types()


class FakeFailure(object):
    def __init__(self, severity, text):
        self.severity = severity
        self.text = text

    def GetSeverity(self):
        return self.severity

    def GetDescriptionText(self):
        return self.text


class FakeAccessor(object):
    def __init__(self, failures):
        self.failures = list(failures)

    def GetFailureMessages(self):
        return list(self.failures)

    def DeleteWarning(self, failure):
        if failure.severity != "Warning":
            raise Exception("only warnings can be deleted")
        self.failures.remove(failure)


class FakeOptions(object):
    def __init__(self):
        self.preprocessor = None

    def SetFailuresPreprocessor(self, preprocessor):
        self.preprocessor = preprocessor
        return self

    def SetClearAfterRollback(self, value):
        return self


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.options = FakeOptions()

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options

    def Start(self):
        self.saved = [(t, t.name) for t in self.doc.types()]
        self.doc.log.append("start")

    def RollBack(self):
        for element_type, name in self.saved:
            element_type.name = name
        self.doc.log.append("rollback")

    def Commit(self):
        self.doc.log.append("commit")
        failures = [FakeFailure(s, t) for s, t in self.doc.failures()]
        guard = self.options.preprocessor
        if failures and guard is not None:
            accessor = FakeAccessor(failures)
            answer = guard.PreprocessFailures(accessor)
            if answer == "ProceedWithRollBack":
                self.RollBack()
                return "RolledBack"
            failures = accessor.failures
        for failure in failures:
            self.doc.dialogs.append(failure.text)
        if any(f.severity == "Error" for f in failures):
            self.RollBack()
            return "RolledBack"
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class Recorder(object):
    """`answer(window)` plays the person at the window and returns the
    pattern; None is Cancel. `families`: the labels ticked in the family
    list."""

    def __init__(self, answer=None, families=()):
        self.answer = answer
        self.families = list(families)
        self.alerts = []
        self.printed = []
        self.window = None
        self.offered = None

    def ask(self, heading, count, parameters, pattern, evaluate):
        self.window = Namespace(heading=heading, count=count,
                                parameters=list(parameters),
                                pattern=pattern, evaluate=evaluate)
        if self.answer is None:
            return None
        return self.answer(self.window)

    def select(self, labels, **kwargs):
        self.offered = list(labels)
        return [label for label in labels if label in self.families]

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, recorder, source=SOURCE):
    db = Namespace(
        ElementType=FakeElementType,
        ElementId=FakeId,
        FilteredElementCollector=FakeCollector,
        BuiltInParameter=Namespace(SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM",
                                   ALL_MODEL_TYPE_NAME="ALL_MODEL_TYPE"),
        StorageType=Namespace(String="String", Integer="Integer",
                              Double="Double", ElementId="ElementId"),
        IFailuresPreprocessor=object,
        FailureSeverity=Namespace(Warning="Warning", Error="Error"),
        FailureProcessingResult=Namespace(
            Continue="Continue", ProceedWithRollBack="ProceedWithRollBack"),
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        WorksharingUtils=Namespace(
            GetCheckoutStatus=lambda d, i: ("OwnedByOtherUser"
                                            if i in d.owned else "Owned")),
        CheckoutStatus=Namespace(OwnedByOtherUser="OwnedByOtherUser"),
    )
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(
        doc=doc, uidoc=Namespace(Selection=Namespace(
            GetElementIds=lambda: [e.Id for e in doc.selection])))
    pyrevit.DB = db
    pyrevit.forms = Namespace(
        alert=recorder.alert,
        SelectFromList=Namespace(show=recorder.select))
    pyrevit.script = Namespace(
        get_output=lambda: Namespace(
            print_md=recorder.print_md,
            linkify=lambda element_id, **k: u"[{0}]".format(
                element_id.Value)),
        get_logger=lambda: Namespace(debug=lambda *a: None,
                                     error=lambda *a: None))
    window = types.ModuleType("avh_typenames.window")
    window.ask = recorder.ask
    stand_ins = {"pyrevit": pyrevit, "avh_typenames.window": window}
    sys.modules.update(stand_ins)
    avh_typenames.window = window
    try:
        code = compile(source, SCRIPT, "exec")
        exec(code, {"__name__": "__main__", "__file__": SCRIPT})
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        for name in stand_ins:
            sys.modules.pop(name, None)
        del avh_typenames.window
    return recorder


def answer(pattern):
    return lambda window: pattern


def family(doc, name, in_place=False):
    doc.next_id += 1
    return Namespace(Id=FakeId(doc.next_id), Name=name, IsInPlace=in_place)


def door_types(**kwargs):
    """A door family with three types (two of them placed and selected),
    a material, and a wall system family with one type."""
    doc = FakeDoc(**kwargs)
    oak = FakeElement(doc, u"Eik", category=u"Efni")
    door = family(doc, u"Hurð einföld")

    def params(width, height, mark, material=oak):
        return [FakeParameter(u"Width", width, "Double"),
                FakeParameter(u"Height", height, "Double"),
                FakeParameter(u"Type Mark", mark),
                FakeParameter(u"Material", material, "ElementId"),
                FakeParameter(u"Lýsing", u"")]
    small = FakeElementType(doc, door, u"Type 1",
                            params(u"800", u"2100", u"H1"))
    large = FakeElementType(doc, door, u"Type 2",
                            params(u"900", u"2100", u"H2"))
    other = FakeElementType(doc, door, u"Type 3",
                            params(u"1000", u"2100", u"H3"))
    wall = FakeElementType(doc, None, u"Veggur 200",
                           [FakeParameter(u"Width", u"200", "Double")],
                           family_name=u"Basic Wall", category=u"Veggir")
    doc.selection = [FakeInstance(doc, small), FakeInstance(doc, large),
                     FakeInstance(doc, small)]
    return doc, small, large, other, wall


def table_ok(text, header):
    rows = text.split(u"\n")
    columns = header.count(u"|")
    return (text.startswith(header) and len(rows) >= 3 and
            [c.strip() for c in rows[1].split(u"|")[1:-1]] ==
            [u":---"] * (columns - 1) and
            all(r.count(u"|") == columns for r in rows) and
            all(c.strip() for r in rows for c in r.split(u"|")[1:-1]))


def item(ident, current, values, family_key=u"f"):
    return model.Item(ident, ident, family_key, u"Hurð", current, values)


def run_all(source=SOURCE):
    # -- 1. The rules ---------------------------------------------------

    check("tokens: names between braces, trimmed",
          model.tokens(u"{Width}x{ Height } {Material}") ==
          [u"Width", u"Height", u"Material"])
    values = {u"Width": u"900", u"Height": u"2100", u"Lýsing": u"a:b;c",
              u"Tómt": u""}
    check("build: values in, case ignored, Icelandic kept",
          model.build(u"{width}x{HEIGHT} hurð", values) ==
          u"900x2100 hurð")
    check("build: forbidden characters from a value become -",
          model.build(u"{Lýsing}", values) == u"a-b-c")
    check("build: an empty or missing value closes up its spaces",
          model.build(u"D {Tómt} {Nothing}  {Width} ", values) == u"D 900")
    items = [item(1, u"A", values)]
    check("pattern: braces must pair",
          u"pair" in model.pattern_problem(u"{Width", items) and
          u"pair" in model.pattern_problem(u"Width}", items) and
          u"pair" in model.pattern_problem(u"{{Width}}", items))
    check("pattern: an unknown parameter is named",
          u"Breidd" in model.pattern_problem(u"{Breidd}", items))
    check("pattern: forbidden characters typed are refused",
          u":" in model.pattern_problem(u"{Width}:{Height}", items))
    check("pattern: empty and {} refused, a good one passes",
          model.pattern_problem(u"  ", items) and
          model.pattern_problem(u"{}", items) and
          model.pattern_problem(u"{Width}x{Height}", items) == u"")

    a = item(1, u"A", {u"M": u"X"})
    b = item(2, u"B", {u"M": u"x"})
    rows, problem = model.plan([a, b], u"{M}")
    check("plan: two chosen types getting one name (ignoring case)",
          problem and all(r.problem for r in rows), problem)
    c = item(3, u"C", {u"M": u"Z"}, family_key=u"g")
    rows, problem = model.plan([a, c], u"{M}", {u"f": [u"x"]})
    check("plan: a name a type not chosen already has",
          problem and rows[0].problem and not rows[1].problem, problem)
    rows, problem = model.plan([a, c], u"{M}", {u"g": [u"x"]})
    check("plan: another family's names do not clash",
          problem == u"" and [r.new for r in rows] == [u"X", u"Z"], problem)
    rows, problem = model.plan([item(1, u"A", {u"M": u""})], u"{M}")
    check("plan: an empty name is refused", u"empty" in problem, problem)
    swap = [item(1, u"A", {u"M": u"B"}), item(2, u"B", {u"M": u"A"}),
            item(3, u"C", {u"M": u"C"})]
    rows, problem = model.plan(swap, u"{M}")
    check("plan: a swap is allowed, both are blockers, unchanged is not",
          problem == u"" and
          [r.item.current for r in model.blockers(rows)] == [u"A", u"B"],
          problem)
    check("available: shared parameters first, counted",
          model.available([item(1, u"A", {u"W": u"1", u"Z": u"2"}),
                           item(2, u"B", {u"w": u"3"})]) ==
          [(u"W", 2), (u"Z", 1)])

    # -- 2. Renaming from several parameters ----------------------------

    doc, small, large, other, wall = door_types()
    pattern = u"{Width}x{Height} {Material}"
    rec = run_script(doc, Recorder(answer(pattern)), source)
    check("rename: the selected instances' types, values gathered",
          (small.name, large.name, other.name) ==
          (u"800x2100 Eik", u"900x2100 Eik", u"Type 3"), rec.text())
    check("rename: each type offered once, not once per instance",
          rec.window is not None and rec.window.count == 2,
          rec.window and rec.window.count)
    check("rename: one committed transaction, no dialogs",
          doc.log == ["start", "commit"] and not doc.dialogs, doc.log)
    names = [n for n, _l in rec.window.parameters] if rec.window else []
    check("window: parameters offered, with the type name",
          u"Width" in names and u"Type Mark" in names and
          model.TYPE_NAME in names and
          any(u"e.g. 800" in l for _n, l in rec.window.parameters),
          rec.window and rec.window.parameters)
    start = (rec.window.evaluate(rec.window.pattern) if rec.window
             else ([], u"no window"))
    check("window: the starting pattern changes nothing",
          start[1] == u"" and start[0] and not model.changed(start[0]),
          start[1])
    printed = [p for p in rec.printed if p.startswith(u"| Type")]
    check("report: one table, every column :---, no empty cells",
          len(printed) == 1 and table_ok(printed[0],
                                         u"| Type | Family | Was | Now |"),
          rec.printed)
    check("report: Icelandic family name in it",
          u"Hurð einföld" in rec.text(), rec.text())

    before = (small.writes, large.writes)
    rec = run_script(doc, Recorder(answer(pattern)), source)
    check("again: nothing written the second time",
          (small.writes, large.writes) == before and
          any(u"already called" in a for a in rec.alerts), rec.alerts)

    # -- 3. A swap, through temporary names -----------------------------

    doc, small, large, other, wall = door_types()
    small.Parameters[2].value = u"Type 2"
    large.Parameters[2].value = u"Type 1"
    rec = run_script(doc, Recorder(answer(u"{Type Mark}")), source)
    check("swap: two chosen types exchange names",
          (small.name, large.name) == (u"Type 2", u"Type 1") and
          doc.log == ["start", "commit"], (doc.log, rec.alerts))

    # -- 4. A clash with a type not chosen ------------------------------

    doc, small, large, other, wall = door_types()
    small.Parameters[2].value = u"type 3"
    rec = run_script(doc, Recorder(answer(u"{Type Mark}")), source)
    shown = rec.window.evaluate(u"{Type Mark}") if rec.window else ([], u"")
    check("clash: the window shows it before anything is written",
          shown[1] and shown[0][0].problem, shown[1])
    check("clash: pressed anyway, nothing written, said why",
          doc.log == [] and small.name == u"Type 1" and
          any(u"Nothing was renamed" in a for a in rec.alerts),
          (doc.log, rec.alerts))

    # -- 5. Nothing selected: families ticked ---------------------------

    doc, small, large, other, wall = door_types()
    doc.selection = []
    rec = run_script(doc, Recorder(answer(u"{Width}"),
                                   families=[u"Veggir: Basic Wall"]),
                     source)
    check("families: offered by category and name",
          rec.offered and u"Hurðir: Hurð einföld" in rec.offered and
          u"Veggir: Basic Wall" in rec.offered, rec.offered)
    check("families: every type of the ticked one, nothing else",
          wall.name == u"200" and small.name == u"Type 1", rec.text())

    doc, small, large, other, wall = door_types()
    doc.selection = [small, large, other]
    rec = run_script(doc, Recorder(answer(u"D-{Type Mark}")), source)
    check("types selected directly (Project Browser) are used",
          (small.name, large.name, other.name) == (u"D-H1", u"D-H2",
                                                   u"D-H3"), rec.text())

    # -- 6. Left out ----------------------------------------------------

    doc, small, large, other, wall = door_types(workshared=True)
    built_in = family(doc, u"Innbyggð", in_place=True)
    in_place = FakeElementType(doc, built_in, u"Innbyggð",
                               [FakeParameter(u"Width", u"5", "Double")])
    doc.owned.add(large.Id)
    doc.selection = [small, large, in_place]
    rec = run_script(doc, Recorder(answer(u"{Width}")), source)
    check("left out: checked out and in-place untouched, rest renamed",
          small.name == u"800" and large.name == u"Type 2" and
          in_place.name == u"Innbyggð", rec.text())
    check("left out: both listed with their reason",
          u"checked out by another user" in rec.text() and
          u"in-place family" in rec.text(), rec.text())

    # -- 7. Refusals roll everything back -------------------------------

    doc, small, large, other, wall = door_types()
    large.raises = True
    rec = run_script(doc, Recorder(answer(u"{Width}")), source)
    check("raised: nothing renamed, where and why said",
          small.name == u"Type 1" and doc.log[-1:] == ["rollback"] and
          any(u"Revit segir nei" in a and u"Type 2" in a
              for a in rec.alerts), (doc.log, rec.alerts))

    doc, small, large, other, wall = door_types()
    large.mangle = True
    rec = run_script(doc, Recorder(answer(u"d{Width}")), source)
    check("read back: a name that comes back different rolls back",
          (small.name, large.name) == (u"Type 1", u"Type 2") and
          any(u"read back" in a for a in rec.alerts), rec.alerts)

    doc, small, large, other, wall = door_types(commit_status="RolledBack")
    rec = run_script(doc, Recorder(answer(u"{Width}")), source)
    check("commit: rolled back by Revit is reported, nothing kept",
          small.name == u"Type 1" and
          any(u"rejected" in a for a in rec.alerts) and
          not rec.printed, (rec.alerts, rec.printed))

    doc, small, large, other, wall = door_types(
        commit_error=u"Villa í líkani")
    rec = run_script(doc, Recorder(answer(u"{Width}")), source)
    check("commit: an error rolls back through the guard, and is quoted",
          small.name == u"Type 1" and not doc.dialogs and
          any(u"Villa í líkani" in a for a in rec.alerts),
          (doc.dialogs, rec.alerts))

    doc, small, large, other, wall = door_types(
        commit_warning=u"Viðvörun um tegund")
    rec = run_script(doc, Recorder(answer(u"{Width}")), source)
    check("warning: written anyway, listed, no dialog",
          small.name == u"800" and not doc.dialogs and
          u"Viðvörun um tegund" in rec.text(), (doc.dialogs, rec.text()))

    # -- 8. Cancel ------------------------------------------------------

    doc, small, large, other, wall = door_types()
    rec = run_script(doc, Recorder(None), source)
    check("cancel: nothing written", doc.log == [] and
          small.name == u"Type 1", doc.log)

    check("no run raised",
          not any(u"<script raised" in a for a in rec.alerts))


run_all()
for name, ok, detail in list(results):
    if not ok and "-v" in sys.argv:
        print(u"  {0}: {1}".format(name, detail))

MUTATIONS = [
    ("commit status ignored",
     u"    if not ok:\n        said = u\"\"",
     u"    if False:\n        said = u\"\""),
    ("read back skipped",
     u"            if got != row.new:", u"            if False:"),
    ("no roll back on an exception",
     u"    except BaseException as exc:\n        transaction.RollBack()\n"
     u"        where",
     u"    except BaseException as exc:\n        pass\n        where"),
    ("guard not attached",
     u"    guard_transaction(transaction, guard)\n", u""),
    ("warnings not deleted",
     u"                    accessor.DeleteWarning(failure)\n", u""),
    ("unchanged names written too",
     u"    to_write = model.changed(rows)", u"    to_write = rows"),
    ("no temporary names",
     u"        for row in model.blockers(rows):",
     u"        for row in []:"),
    ("owner check removed",
     u"        if owned_by_someone_else(doc, element_type):",
     u"        if False:"),
    ("in-place check removed",
     u"        if safe(lambda: element_type.Family.IsInPlace, False):",
     u"        if False:"),
    ("selection ignored",
     u"    chosen = selected_types(doc)", u"    chosen = []"),
    ("a type counted once per instance",
     u"        if key in seen:\n            continue",
     u"        if False:\n            continue"),
    ("names of types not chosen ignored",
     u"        return model.plan(items, pattern, taken)",
     u"        return model.plan(items, pattern)"),
    ("element id read as its id, not its name",
     u"            if element is not None:\n                return name_of(",
     u"            if False:\n                return name_of("),
    ("problem not checked before writing",
     u"    if problem:\n        forms.alert(u\"Nothing was renamed",
     u"    if False:\n        forms.alert(u\"Nothing was renamed"),
]

for label, old, new in MUTATIONS:
    if old not in SOURCE:
        check("mutation '{0}': its line is in the script".format(label),
              False, old)
        continue
    saved = list(results)
    del results[:]
    run_all(SOURCE.replace(old, new, 1))
    caught = [name for name, ok, _ in results if not ok]
    results[:] = saved
    if "-v" in sys.argv:
        print(u"mutation '{0}': {1} check(s) fail".format(label,
                                                          len(caught)))
    check("mutation '{0}' is caught".format(label), caught,
          u"every check still passed")

check("harness: a false check is recorded as failed",
      (check("probe", 1 == 2) or results.pop()[1]) is False)
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
