# -*- coding: utf-8 -*-
"""
Runs the real Renumber script against a mocked Revit.

The window (lib/avh_renumber/window.py) is WPF and cannot run here; a
stand in records what the script hands it and answers as a person would.
Everything the window shows comes from the `evaluate` callable the
script passes it, and that is exercised here.

The fake behaves the way Revit is expected to:

- parameter values persist across runs, so a second identical run must
  write nothing;
- an Integer parameter refuses text and a text parameter refuses a
  number, by raising;
- a rolled back transaction, or a commit that comes back RolledBack,
  puts every value back;
- at commit, two elements holding the same Mark post a warning, and a
  configured error posts an error. With a failures preprocessor attached
  they go through it; without one each is recorded as a dialog Revit
  would have shown. `doc.dialogs` must stay empty.

At the end the script is run again with its safety lines taken out one
at a time (in memory, nothing written), and each such mutation has to
make at least one check fail.

Run outside Revit:

    python test_renumber_harness.py      (-v: how many checks each
                                          mutation fails)
"""

import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Data.panel",
                      "Renumber.pushbutton", "script.py")
SOURCE = io.open(SCRIPT, encoding="utf-8").read()

import avh_renumber                     # noqa: E402
from avh_renumber import model          # noqa: E402

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
YES_NO = "SpecTypeId.Boolean.YesNo"
TEXT_SPEC = "SpecTypeId.String.Text"
INT_SPEC = "SpecTypeId.Int.Integer"


class Cancelled(Exception):
    """Autodesk.Revit.Exceptions.OperationCanceledException"""


class FakeDefinition(object):
    def __init__(self, name, data_type, varies):
        self.Name = name
        self.data_type = data_type
        self.VariesAcrossGroups = varies

    def GetDataType(self):
        return self.data_type


class FakeParameter(object):
    def __init__(self, name, value=u"", storage="String", read_only=False,
                 yes_no=False, varies=False, refuse=False, mangle=False,
                 raises=False):
        self.value = value
        self.StorageType = storage
        self.IsReadOnly = read_only
        spec = YES_NO if yes_no else (TEXT_SPEC if storage == "String"
                                      else INT_SPEC)
        self.Definition = FakeDefinition(name, spec, varies)
        self.refuse = refuse
        self.mangle = mangle
        self.raises = raises
        self.writes = 0

    @property
    def HasValue(self):
        return self.value is not None and self.value != u""

    def AsString(self):
        return self.value if self.StorageType == "String" else None

    def AsInteger(self):
        if self.StorageType != "Integer" or self.value is None:
            return 0
        return self.value

    def Set(self, value):
        if self.IsReadOnly:
            raise Exception("The parameter is read-only")
        if self.raises:
            raise Exception(u"Revit segir nei")
        if self.refuse:
            return False
        if self.StorageType == "Integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise Exception("Integer parameter given " + repr(value))
        elif not isinstance(value, type(u"")):
            raise Exception("Text parameter given " + repr(value))
        self.value = value.lower() if self.mangle else value
        self.writes += 1
        return True


class FakePoint(object):
    def __init__(self, x, y):
        self.X = x
        self.Y = y


class FakeElement(object):
    def __init__(self, doc, name, params, x=None, y=None, group=False,
                 category=u"Hurðir"):
        doc.next_id += 1
        self.Id = FakeId(doc.next_id)
        self.Name = name
        self.Category = Namespace(Name=category)
        self.Parameters = list(params)
        self.Location = (Namespace(Point=FakePoint(x, y))
                         if x is not None else None)
        self.GroupId = FakeId(900) if group else INVALID
        doc.elements.append(self)

    def LookupParameter(self, name):
        for parameter in self.Parameters:
            if parameter.Definition.Name == name:
                return parameter
        return None

    def get_BoundingBox(self, view):
        return None

    def param(self, name):
        return self.LookupParameter(name).value


class FakeElementType(FakeElement):
    pass


class FakeDoc(object):
    def __init__(self, commit_status="Committed", commit_error=None,
                 workshared=False):
        self.elements = []
        self.next_id = 100
        self.commit_status = commit_status
        self.commit_error = commit_error
        self.IsWorkshared = workshared
        self.owned = set()
        self.selection = []
        self.picks = []
        self.log = []
        self.dialogs = []

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def all_parameters(self):
        for element in self.elements:
            for parameter in element.Parameters:
                yield element, parameter

    def failures(self):
        """What Revit posts at commit: (severity, text)."""
        found = []
        marks = {}
        for element, parameter in self.all_parameters():
            if parameter.Definition.Name == u"Mark" and parameter.value:
                marks.setdefault(parameter.value, []).append(element)
        if any(len(same) > 1 for same in marks.values()):
            found.append(("Warning",
                          u"Elements have duplicate 'Mark' values."))
        if self.commit_error:
            found.append(("Error", self.commit_error))
        return found


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
        self.saved = [(p, p.value) for _e, p in self.doc.all_parameters()]
        self.doc.log.append("start")

    def RollBack(self):
        for parameter, value in self.saved:
            parameter.value = value
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
    """`answer(heading, count, orders, prefix, start, step, suffix,
    evaluate)` plays the person at the window; None is Cancel.
    `parameter`: the name ticked in the parameter list."""

    def __init__(self, answer=None, parameter=u"Mark"):
        self.answer = answer
        self.parameter = parameter
        self.alerts = []
        self.printed = []
        self.window = None
        self.offered = None

    def ask(self, heading, count, orders, prefix, start, step, suffix,
            evaluate):
        self.window = Namespace(heading=heading, count=count,
                                orders=list(orders), prefix=prefix,
                                start=start, step=step, suffix=suffix,
                                evaluate=evaluate)
        if self.answer is None:
            return None
        return self.answer(self.window)

    def select(self, labels, **kwargs):
        self.offered = list(labels)
        for label in labels:
            if label.split(u"  (")[0] == self.parameter:
                return label
        return None

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
        StorageType=Namespace(String="String", Integer="Integer",
                              Double="Double"),
        SpecTypeId=Namespace(Boolean=Namespace(YesNo=YES_NO)),
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
    picks = list(doc.picks)

    def pick_object(kind, prompt):
        if not picks:
            raise Cancelled()
        return Namespace(ElementId=picks.pop(0).Id)

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(
        doc=doc, uidoc=Namespace(Selection=Namespace(
            GetElementIds=lambda: [e.Id for e in doc.selection],
            PickObject=pick_object)))
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
    window = types.ModuleType("avh_renumber.window")
    window.ask = recorder.ask
    autodesk = types.ModuleType("Autodesk")
    selection = types.ModuleType("Autodesk.Revit.UI.Selection")
    selection.ObjectType = Namespace(Element="Element")
    exceptions = types.ModuleType("Autodesk.Revit.Exceptions")
    exceptions.OperationCanceledException = Cancelled
    stand_ins = {
        "pyrevit": pyrevit,
        "avh_renumber.window": window,
        "Autodesk": autodesk,
        "Autodesk.Revit": types.ModuleType("Autodesk.Revit"),
        "Autodesk.Revit.UI": types.ModuleType("Autodesk.Revit.UI"),
        "Autodesk.Revit.UI.Selection": selection,
        "Autodesk.Revit.Exceptions": exceptions,
    }
    sys.modules.update(stand_ins)
    avh_renumber.window = window
    try:
        code = compile(source, SCRIPT, "exec")
        exec(code, {"__name__": "__main__", "__file__": SCRIPT})
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        for name in stand_ins:
            sys.modules.pop(name, None)
        del avh_renumber.window
    return recorder


def answer(order=None, prefix=u"", start=u"1", step=u"1", suffix=u"",
           only_empty=False):
    """A person who sets these and presses Write, keeping the offered
    order unless told otherwise."""
    def press(window):
        return (order or window.orders[0], prefix, start, step, suffix,
                only_empty)
    return press


def doors(**kwargs):
    """Three doors out of order on the plan, a door type, and a door not
    chosen that already has Mark D-01."""
    doc = FakeDoc(**kwargs)

    def params(mark, count=0):
        return [FakeParameter(u"Mark", mark),
                FakeParameter(u"Comments", u""),
                FakeParameter(u"Fjöldi", count, "Integer"),
                FakeParameter(u"Eldvarin", 0, "Integer", yes_no=True),
                FakeParameter(u"Level", u"1. hæð", read_only=True)]
    right = FakeElement(doc, u"H1", params(u"x"), x=30.0, y=0.0)
    left = FakeElement(doc, u"H1", params(u""), x=0.0, y=0.0)
    middle = FakeElement(doc, u"H1", params(u"D-9"), x=15.0, y=0.0)
    kind = FakeElementType(doc, u"H1 type", params(u""))
    doc.selection = [right, kind, left, middle]
    return doc, left, middle, right


def table_ok(text, header):
    rows = text.split(u"\n")
    columns = header.count(u"|")
    return (text.startswith(header) and len(rows) >= 3 and
            [c.strip() for c in rows[1].split(u"|")[1:-1]] ==
            [u":---"] * (columns - 1) and
            all(r.count(u"|") == columns for r in rows) and
            all(c.strip() for r in rows for c in r.split(u"|")[1:-1]))


def run_all(source=SOURCE):
    # -- the rules ------------------------------------------------------

    check("sequence: 1, 01 keeps its width, 101, a step",
          model.sequence(3, u"1") == [u"1", u"2", u"3"] and
          model.sequence(3, u"08") == [u"08", u"09", u"10"] and
          model.sequence(2, u"101", 10) == [u"101", u"111"])
    check("sequence: letters, with a step, never past Z",
          model.sequence(3, u"A", 2) == [u"A", u"C", u"E"] and
          model.sequence(2, u"y") == [u"y", u"z"] and
          model.sequence(3, u"Y") is None and
          model.sequence(2, u"Á") is None and
          model.sequence(2, u"1a") is None)
    check("settings: a whole number parameter takes digits only",
          model.settings_problem(2, u"D-", u"1", u"1", u"", model.INTEGER)
          and model.settings_problem(2, u"", u"1", u"1", u"b",
                                     model.INTEGER)
          and model.settings_problem(2, u"", u"A", u"1", u"",
                                     model.INTEGER)
          and model.settings_problem(2, u"", u"1", u"1", u"",
                                     model.INTEGER) == u"")
    check("settings: step and start must be usable",
          model.settings_problem(2, u"", u"1", u"0", u"", model.TEXT) and
          model.settings_problem(2, u"", u"1", u"x", u"", model.TEXT) and
          model.settings_problem(2, u"", u"", u"1", u"", model.TEXT) and
          u"past Z" in model.settings_problem(30, u"", u"A", u"1", u"",
                                               model.TEXT))
    items = [model.Item(0, 5, u"a", u"D-10", 0.0, 10.0),
             model.Item(1, 3, u"b", u"D-2", 10.0, 10.0 + 3.0 / 304.8),
             model.Item(2, 9, u"c", u"", 0.0 + 4.0 / 304.8, 0.0),
             model.Item(3, 1, u"d", u"D-1")]
    check("order: left to right, columns within 10 mm count as one",
          [i.label for i in model.ordered(items, model.ORDER_X)] ==
          [u"a", u"c", u"b", u"d"],
          [i.label for i in model.ordered(items, model.ORDER_X)])
    check("order: top to bottom, rows within 10 mm count as one",
          [i.label for i in model.ordered(items, model.ORDER_Y)] ==
          [u"a", u"b", u"c", u"d"],
          [i.label for i in model.ordered(items, model.ORDER_Y)])
    check("order: by value is natural, empty last; by id; as picked",
          [i.label for i in model.ordered(items, model.ORDER_VALUE)] ==
          [u"d", u"b", u"a", u"c"] and
          [i.label for i in model.ordered(items, model.ORDER_ID)] ==
          [u"d", u"b", u"a", u"c"] and
          [i.label for i in model.ordered(items, model.ORDER_PICKED)] ==
          [u"a", u"b", u"c", u"d"])
    rows, problem = model.plan(items, model.ORDER_PICKED, u"D-", u"1",
                               u"1", u"")
    check("plan: prefix + number + suffix, and what changes",
          not problem and [r.new for r in rows] ==
          [u"D-1", u"D-2", u"D-3", u"D-4"] and
          [r.changes for r in rows] == [True, False, True, True])
    rows, problem = model.plan(items, model.ORDER_PICKED, u"D-", u"1",
                               u"1", u"", model.INTEGER)
    check("plan: a problem gives no rows", problem and rows == [])

    many = [model.Item(i, i, u"e", u"") for i in range(1000)]
    rows, problem = model.plan(many[:999], model.ORDER_PICKED, u"#QQC",
                               u"001", u"1", u"")
    check("width: 001 runs #QQC001 to #QQC999",
          not problem and rows[0].new == u"#QQC001" and
          rows[-1].new == u"#QQC999", problem)
    rows, problem = model.plan(many, model.ORDER_PICKED, u"#QQC", u"001",
                               u"1", u"")
    check("width: a 1000th element is refused, not written as #QQC1000",
          rows == [] and u"999" in problem and u"3 digits" in problem,
          problem)
    check("width: a step counts against it too; 1 and 101 just grow",
          not model.plan(many[:500], model.ORDER_PICKED, u"", u"001", u"2",
                         u"")[1] and
          model.plan(many[:501], model.ORDER_PICKED, u"", u"001", u"2",
                     u"")[1] and
          model.plan(many, model.ORDER_PICKED, u"", u"1", u"1",
                     u"")[1] == u"" and
          model.plan(many, model.ORDER_PICKED, u"", u"101", u"1",
                     u"")[1] == u"")
    check("suggest: the format the values already have",
          model.suggest([u"#QQC014", u"#QQC002", u"", u"x"]) ==
          (u"#QQC", u"001", u"") and
          model.suggest([u"D-9", u"D-10", u"D-11"]) == (u"D-", u"1", u"") and
          model.suggest([u"Hæð 01 a", u"Hæð 07 a"]) ==
          (u"Hæð ", u"01", u" a") and
          model.suggest([u"", u"abc"]) is None)

    mixed = [model.Item(i, i, u"e", v) for i, v in enumerate(
        [u"#QQC001", u"", u"#QQC003", u"", u"", u"  "])]
    rows, problem = model.plan(mixed, model.ORDER_PICKED, u"#QQC", u"001",
                               u"1", u"", only_empty=True)
    check("only empty: values kept, gaps filled, then after the highest",
          not problem and [r.new for r in rows] ==
          [u"#QQC001", u"#QQC002", u"#QQC003", u"#QQC004", u"#QQC005",
           u"#QQC006"] and [r.changes for r in rows] ==
          [False, True, False, True, True, True],
          (problem, [r.new for r in rows]))
    rows, problem = model.plan(mixed, model.ORDER_PICKED, u"#QQC", u"001",
                               u"2", u"")
    check("only empty off: every element numbered over again",
          [r.new for r in rows] == [u"#QQC001", u"#QQC003", u"#QQC005",
                                    u"#QQC007", u"#QQC009", u"#QQC011"],
          [r.new for r in rows])
    letters = [model.Item(i, i, u"e", v) for i, v in
               enumerate([u"A", u"", u"C", u""])]
    rows, problem = model.plan(letters, model.ORDER_PICKED, u"", u"A", u"1",
                               u"", only_empty=True)
    check("only empty: letters skip those in use too",
          [r.new for r in rows] == [u"A", u"B", u"C", u"D"], problem)
    full = [model.Item(i, i, u"e", u"#QQC{0:03d}".format(i + 1))
            for i in range(998)] + [model.Item(998, 998, u"e", u""),
                                    model.Item(999, 999, u"e", u"")]
    rows, problem = model.plan(full, model.ORDER_PICKED, u"#QQC", u"001",
                               u"1", u"", only_empty=True)
    check("only empty: still refused past #QQC999",
          rows == [] and u"3 digits" in problem, problem)
    rows, problem = model.plan(
        [model.Item(0, 0, u"e", u"7"), model.Item(1, 1, u"e", u"")],
        model.ORDER_PICKED, u"", u"7", u"1", u"", model.INTEGER, True)
    check("only empty: a whole number in use is skipped",
          [r.new for r in rows] == [u"7", u"8"], problem)

    WINDOW_SRC = io.open(os.path.join(HERE, "lib", "avh_renumber",
                                      "window.py"), encoding="utf-8").read()
    check("window: write stays off on a problem or when nothing changes",
          u"self.write_button.IsEnabled = False" in WINDOW_SRC and
          u"self.write_button.IsEnabled = bool(changing)" in WINDOW_SRC)
    check("window: the only empty box refreshes and is handed back",
          u"self.empty_box.Checked += self.on_changed" in WINDOW_SRC and
          u"self.empty_box.Unchecked += self.on_changed" in WINDOW_SRC and
          u"bool(self.empty_box.IsChecked))" in WINDOW_SRC)
    check("window: every box and the order refresh the preview",
          u"self.order_box.SelectionChanged += self.on_changed"
          in WINDOW_SRC and u"box.TextChanged += self.on_changed"
          in WINDOW_SRC)

    # -- 1. Selected doors, Mark, left to right -------------------------

    doc, left, middle, right = doors()
    rec = run_script(doc, Recorder(answer(prefix=u"D-", start=u"01")),
                     source)
    check("parameters: text and whole numbers offered; Yes/No and read "
          "only left out",
          rec.offered == [u"Comments  (text)", u"Fjöldi  (whole number)",
                          u"Mark  (text)"], rec.offered)
    check("window: three doors (the type left out), orders by position",
          rec.window and rec.window.count == 3 and
          rec.window.orders == list(model.ORDERS_SELECTED),
          rec.window and (rec.window.count, rec.window.orders))
    check("written: numbered left to right",
          (left.param(u"Mark"), middle.param(u"Mark"),
           right.param(u"Mark")) == (u"D-01", u"D-02", u"D-03"),
          (left.param(u"Mark"), middle.param(u"Mark"),
           right.param(u"Mark"), rec.text()))
    check("written: one transaction, committed", doc.log ==
          ["start", "commit"], doc.log)
    check("written: no dialog left for Revit to show", not doc.dialogs,
          doc.dialogs)
    tables = [t for t in rec.printed if t.startswith(u"| # |")]
    check("report: one whole table, every column :---",
          len(tables) == 1 and table_ok(tables[0], u"| # | Element | Was | "
                                        u"Now |") and
          len(tables[0].split(u"\n")) == 5, rec.printed)

    fdoc, fleft, fmiddle, fright = doors()
    for door, mark in ((fleft, u"#QQC003"), (fmiddle, u"#QQC001"),
                       (fright, u"")):
        door.LookupParameter(u"Mark").value = mark
    rec = run_script(fdoc, Recorder(None), source)
    check("format: the window starts from the values' own #QQC001",
          rec.window and (rec.window.prefix, rec.window.start,
                          rec.window.suffix) == (u"#QQC", u"001", u""),
          rec.window and (rec.window.prefix, rec.window.start))
    rec = run_script(fdoc, Recorder(None, parameter=u"Comments"), source)
    check("format: nothing to go on, an empty prefix from 1",
          rec.window and (rec.window.prefix, rec.window.start) ==
          (u"", u"1"))

    edoc, eleft, emiddle, eright = doors()
    eleft.LookupParameter(u"Mark").value = u"#QQC002"
    eright.LookupParameter(u"Mark").value = None
    rec = run_script(edoc, Recorder(answer(prefix=u"#QQC", start=u"001",
                                           only_empty=True)), source)
    check("only empty: kept #QQC002, numbered the others round it",
          (eleft.param(u"Mark"), emiddle.param(u"Mark"),
           eright.param(u"Mark")) == (u"#QQC002", u"D-9", u"#QQC001"),
          (eleft.param(u"Mark"), emiddle.param(u"Mark"),
           eright.param(u"Mark"), rec.text()))
    check("only empty: the kept value was not written",
          eleft.LookupParameter(u"Mark").writes == 0)

    ndoc, nleft, nmiddle, nright = doors()
    nmiddle.LookupParameter(u"Fjöldi").value = None
    nright.LookupParameter(u"Fjöldi").value = 4
    rec = run_script(ndoc, Recorder(answer(start=u"1", only_empty=True),
                                    parameter=u"Fjöldi"), source)
    check("only empty: an unset whole number counts as empty, 0 does not",
          (nleft.param(u"Fjöldi"), nmiddle.param(u"Fjöldi"),
           nright.param(u"Fjöldi")) == (0, 1, 4),
          (nleft.param(u"Fjöldi"), nmiddle.param(u"Fjöldi"),
           nright.param(u"Fjöldi"), rec.text()))

    # -- 2. The same run again writes nothing ---------------------------

    doc.log = []
    writes = sum(p.writes for _e, p in doc.all_parameters())
    rec = run_script(doc, Recorder(answer(prefix=u"D-", start=u"01")),
                     source)
    check("again: nothing written, no transaction",
          doc.log == [] and
          sum(p.writes for _e, p in doc.all_parameters()) == writes and
          any(u"already" in a for a in rec.alerts), (doc.log, rec.alerts))

    # -- 3. Nothing selected: picked one at a time, in order ------------

    doc, left, middle, right = doors()
    doc.selection = []
    doc.picks = [right, left, right, middle]
    rec = run_script(doc, Recorder(answer(start=u"A")), source)
    check("picked: offered 'as picked' first",
          rec.window and rec.window.orders == list(model.ORDERS_PICKED))
    check("picked: numbered in the order picked, a second pick ignored",
          (right.param(u"Mark"), left.param(u"Mark"),
           middle.param(u"Mark")) == (u"A", u"B", u"C"),
          (right.param(u"Mark"), left.param(u"Mark"),
           middle.param(u"Mark"), rec.text()))

    doc, left, middle, right = doors()
    doc.selection = []
    rec = run_script(doc, Recorder(answer()), source)
    check("picked: Esc at once does nothing", rec.window is None and
          doc.log == [] and not rec.alerts, rec.text())

    # -- 4. A whole number parameter ------------------------------------

    doc, left, middle, right = doors()
    rec = run_script(doc, Recorder(answer(start=u"5", step=u"5"),
                                   parameter=u"Fjöldi"), source)
    check("integer: 5, 10, 15 written as numbers",
          (left.param(u"Fjöldi"), middle.param(u"Fjöldi"),
           right.param(u"Fjöldi")) == (5, 10, 15), rec.text())

    doc, left, middle, right = doors()
    rec = run_script(doc, Recorder(answer(prefix=u"D-"),
                                   parameter=u"Fjöldi"), source)
    check("integer: a prefix is refused before anything is written",
          doc.log == [] and any(u"prefix" in a for a in rec.alerts) and
          left.param(u"Fjöldi") == 0, (doc.log, rec.alerts))

    # -- 5. Groups and checked out elements ------------------------------

    doc, left, middle, right = doors(workshared=True)
    grouped = FakeElement(doc, u"H2", [FakeParameter(u"Mark", u"")],
                          x=50.0, y=0.0, group=True)
    varying = FakeElement(doc, u"H2", [FakeParameter(u"Mark", u"",
                                                     varies=True)],
                          x=60.0, y=0.0, group=True)
    doc.owned.add(middle.Id)
    doc.selection = [left, middle, right, grouped, varying]
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("groups: grouped and not varying left out, varying written",
          grouped.param(u"Mark") == u"" and varying.param(u"Mark") ==
          u"D-3" and left.param(u"Mark") == u"D-1", rec.text())
    check("owned: checked out by another user left out",
          middle.param(u"Mark") == u"D-9" and
          right.param(u"Mark") == u"D-2", rec.text())
    check("left out: both listed with their reason",
          u"inside a group" in rec.text() and
          u"checked out by another user" in rec.text(), rec.text())

    # -- 6. Refusals roll everything back -------------------------------

    doc, left, middle, right = doors()
    middle.LookupParameter(u"Mark").refuse = True
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("refused: nothing written, the element named",
          (left.param(u"Mark"), right.param(u"Mark")) == (u"", u"x") and
          doc.log[-1:] == ["rollback"] and
          any(u"refused" in a and u"H1" in a for a in rec.alerts),
          (doc.log, rec.alerts))

    doc, left, middle, right = doors()
    middle.LookupParameter(u"Mark").raises = True
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("raised: nothing written, where and why said",
          (left.param(u"Mark"), right.param(u"Mark")) == (u"", u"x") and
          doc.log[-1:] == ["rollback"] and
          any(u"Revit segir nei" in a and u" at " in a for a in rec.alerts),
          (doc.log, rec.alerts))

    doc, left, middle, right = doors()
    right.LookupParameter(u"Mark").mangle = True
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("read back: a value that comes back different rolls back",
          (left.param(u"Mark"), middle.param(u"Mark")) == (u"", u"D-9")
          and any(u"read back" in a for a in rec.alerts), rec.alerts)

    doc, left, middle, right = doors(commit_status="RolledBack")
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("commit: rolled back by Revit is reported, nothing kept",
          left.param(u"Mark") == u"" and
          any(u"rejected" in a for a in rec.alerts) and
          not rec.printed, (rec.alerts, rec.printed))

    doc, left, middle, right = doors(commit_error=u"Villa í líkani")
    rec = run_script(doc, Recorder(answer(prefix=u"D-")), source)
    check("commit: an error rolls back through the guard, and is quoted",
          left.param(u"Mark") == u"" and not doc.dialogs and
          any(u"Villa í líkani" in a for a in rec.alerts),
          (doc.dialogs, rec.alerts))

    # -- 7. Warnings are listed, not shown ------------------------------

    doc, left, middle, right = doors()
    FakeElement(doc, u"H9", [FakeParameter(u"Mark", u"D-01")])
    rec = run_script(doc, Recorder(answer(prefix=u"D-", start=u"01")),
                     source)
    check("warning: duplicate Mark written anyway, listed, no dialog",
          left.param(u"Mark") == u"D-01" and not doc.dialogs and
          u"duplicate 'Mark'" in rec.text(), (doc.dialogs, rec.text()))

    # -- 8. Cancel, and nothing in common -------------------------------

    doc, left, middle, right = doors()
    rec = run_script(doc, Recorder(None), source)
    check("cancel: nothing written", doc.log == [] and
          left.param(u"Mark") == u"", doc.log)

    doc = FakeDoc()
    one = FakeElement(doc, u"A", [FakeParameter(u"Mark")])
    two = FakeElement(doc, u"B", [FakeParameter(u"Comments")])
    doc.selection = [one, two]
    rec = run_script(doc, Recorder(answer()), source)
    check("nothing in common: said so, no list",
          rec.offered is None and
          any(u"in common" in a for a in rec.alerts), rec.alerts)

    check("no run raised", True)


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
    ("Set refusal ignored",
     u"            if parameter.Set(value) is False:",
     u"            if parameter.Set(value) is None:"),
    ("no roll back on an exception",
     u"    except BaseException as exc:\n        transaction.RollBack()\n"
     u"        where",
     u"    except BaseException as exc:\n        pass\n        where"),
    ("guard not attached",
     u"    guard_transaction(transaction, guard)\n", u""),
    ("warnings not deleted",
     u"                    accessor.DeleteWarning(failure)\n", u""),
    ("unchanged values written too",
     u"    to_write = model.changed(rows)", u"    to_write = rows"),
    ("group check removed",
     u"        if in_group(element) and not safe(",
     u"        if False and not safe("),
    ("owner check removed",
     u"        if owned_by_someone_else(doc, element):",
     u"        if False:"),
    ("Yes/No offered",
     u"        if data_type is not None and data_type == yes_no:",
     u"        if False:"),
    ("read only offered",
     u"        if safe(lambda: parameter.IsReadOnly, True):",
     u"        if False:"),
    ("element types kept",
     u"    if isinstance(element, DB.ElementType):\n        return False",
     u"    if False:\n        return False"),
    ("selection ignored",
     u"    elements = selected_elements(doc)", u"    elements = []"),
    ("format not suggested",
     u"        if found is not None:", u"        if False:"),
    ("only empty not passed on",
     u"                          storage, only_empty)",
     u"                          storage, False)"),
    ("no value read as a value",
     u"    if safe(lambda: parameter.HasValue, True) is False:",
     u"    if False:"),
    ("second pick kept",
     u"        if key in seen:\n            continue",
     u"        if False:\n            continue"),
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
