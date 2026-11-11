# -*- coding: utf-8 -*-
"""
Runs the real Gönguhlutfall stiga script against a mocked Revit, and the
rules module against its two sources directly:

- Byggingarreglugerð 112/2012, in force from 1 May 2026, 6.4.9 and
  6.4.10;
- ÍST EN ISO 14122:2016 parts 1, 3 and 4, for To_Machinery.

Every limit is tested just inside and just outside, so a figure typed
wrong fails in either direction.

The fake lies the way Revit does where that is known:

- Stairs.IsInEditMode is a method, not a property (2.33.0 skipped every
  stair by reading it as one).
- An element type's Name raises, as ElementType.Name can on IronPython.
- Parameter.Set returns False on a read only parameter.

Run outside Revit:

    python test_stair_ratio_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Stair Ratio.pushbutton", "script.py")

from avh_stairs import rules  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


FT = 1.0 / 0.3048


def status_of(stair, rule, wells=None):
    _use, found = rules.check(stair, wells or rules.stairwells([stair]))
    got = [r.status for r in found if r.rule == rule]
    return got[0] if len(got) == 1 else tuple(got)


def message_of(stair, rule):
    _use, found = rules.check(stair, rules.stairwells([stair]))
    return u" ".join(r.message for r in found if r.rule == rule)


def stair(**kwargs):
    """A general stair that meets every rule: 170 / 280, nosing 20."""
    values = dict(element_id=1, use_value=u"almennur", riser=0.170,
                  going=0.280, base=0.0, top=3.40, nosing=0.020,
                  runs=[rules.Run(11, 1.70, 10), rules.Run(12, 1.70, 10)],
                  plan=(0.0, 0.0, 3.0, 5.0))
    values.update(kwargs)
    return rules.Stair(**values)


def at(slope, riser, use=u"To_Machinery", **kwargs):
    """A stair at a given pitch with a given riser."""
    going = riser / math.tan(math.radians(slope))
    kwargs.setdefault("runs", [rules.Run(11, riser * 10, 10)])
    return stair(use_value=use, riser=riser, going=going, **kwargs)


def code_of(text):
    use = rules.parse_use(text)[0]
    return use.code if use is not None else None


# --------------------------------------------------------------------------
# Use codes
# --------------------------------------------------------------------------

check("use: the four codes", [u.code for u in rules.USES] ==
      [u"almennur", u"annar", u"uti", u"To_Machinery"])
check("use: To_Machinery as typed", code_of(u"To_Machinery")
      == u"To_Machinery")
check("use: to machinery in any case and with a space",
      code_of(u"to machinery") == u"To_Machinery")
check("use: Icelandic spelling folds", code_of(u"Útí") == u"uti")
for old, new in ((u"ibud", u"almennur"), (u"fyrirtaeki", u"almennur"),
                 (u"Fyrirtæki", u"almennur"), (u"almenningur", u"almennur"),
                 (u"fjolbyli_lyfta", u"almennur"), (u"fjolbyli", u"almennur"),
                 (u"uti_adal", u"uti"), (u"uti_svalir", u"uti"),
                 (u"annar", u"annar")):
    check(u"use: 2.33 code {0} reads as {1}".format(old, new),
          code_of(old) == new)
check("use: an unknown word is named",
      u"skrifstofa" in rules.parse_use(u"skrifstofa")[1])
check("use: blank is no use and no problem",
      rules.parse_use(u" ") == (None, u""))

# --------------------------------------------------------------------------
# Byggingarreglugerð 6.4.9, first paragraph
# --------------------------------------------------------------------------

_use, found = rules.check(stair(), rules.stairwells([stair()]))
check("general: the good stair passes", rules.worst(found) == rules.PASS,
      [r for r in found if r.status != rules.PASS])
check("general: rules are riser, going, slope, 2h + b, nosing, same steps",
      [r.rule for r in found] == [u"Riser", u"Going", u"Slope", u"2h + b",
                                  u"Nosing", u"Same steps throughout"],
      [r.rule for r in found])
check("general: nothing but rise and going is checked",
      not any(r.rule in (u"Width", u"Headroom", u"Handrails both sides",
                         u"Landing length", u"Open risers")
              for r in found))
check("general: riser 180 passes, 181 fails",
      status_of(stair(riser=0.180, going=0.270), u"Riser") == rules.PASS and
      status_of(stair(riser=0.181, going=0.270), u"Riser") == rules.FAIL)
check("general: riser 120 passes, 119 fails",
      status_of(stair(riser=0.120), u"Riser") == rules.PASS and
      status_of(stair(riser=0.119), u"Riser") == rules.FAIL)
check("general: going 240 passes, 239 fails",
      status_of(stair(going=0.240), u"Going") == rules.PASS and
      status_of(stair(going=0.239), u"Going") == rules.FAIL)
check("general: 2h + b 640 passes, 641 fails",
      status_of(stair(riser=0.180, going=0.280), u"2h + b") == rules.PASS
      and status_of(stair(riser=0.180, going=0.281), u"2h + b")
      == rules.FAIL)
check("general: 2h + b 599 fails",
      status_of(stair(riser=0.160, going=0.279), u"2h + b") == rules.FAIL)
check("general: 37 and 29 degrees fail",
      status_of(at(37, 0.180, u"almennur"), u"Slope") == rules.FAIL and
      status_of(at(29, 0.150, u"almennur"), u"Slope") == rules.FAIL)
check("general: nosing none fails with going 280",
      status_of(stair(nosing=0.0), u"Nosing") == rules.FAIL and
      u"no nosing" in message_of(stair(nosing=0.0), u"Nosing"))
check("general: going 280 plus 15 fails, plus 20 passes",
      status_of(stair(nosing=0.015), u"Nosing") == rules.FAIL and
      status_of(stair(nosing=0.020), u"Nosing") == rules.PASS)
check("general: going 300 needs no nosing",
      status_of(stair(going=0.300, riser=0.165, nosing=0.0), u"Nosing")
      == rules.PASS)
check("general: unreadable nosing is not checked",
      status_of(stair(nosing=None), u"Nosing") == rules.NOT_CHECKED)
check("general: a flight with another riser fails same steps",
      status_of(stair(runs=[rules.Run(11, 1.70, 10),
                            rules.Run(12, 1.75, 10)]),
                u"Same steps throughout") == rules.FAIL)

stack = [stair(element_id=1, going=0.250),
         stair(element_id=2, base=3.40, top=6.80, going=0.250),
         stair(element_id=3, base=6.80, top=10.20, going=0.250)]
wells = rules.stairwells(stack)
check("stairwell: three stacked stairs are one group", len(wells[1]) == 3)
check("stairwell: going 250 fails for four floors",
      status_of(stack[0], u"Going", wells) == rules.FAIL)
check("stairwell: going 250 between two floors passes",
      status_of(stack[0], u"Going") == rules.PASS)
check("stairwell: a stair elsewhere is not stacked",
      len(rules.stairwells([stack[0], stair(element_id=9, base=3.4,
                                            top=6.8,
                                            plan=(9, 0, 12, 5))])[1]) == 1)
check("stairwell: a stair 0.6 m higher is not stacked",
      len(rules.stairwells([stack[0], stair(element_id=8, base=4.0,
                                            top=7.4)])[1]) == 1)
odd = [stair(element_id=1), stair(element_id=2, base=3.4, top=6.8,
                                  going=0.270, riser=0.170,
                                  nosing=0.030)]
check("stairwell: another going on another floor fails both",
      status_of(odd[0], u"Same steps throughout", rules.stairwells(odd))
      == rules.FAIL and
      status_of(odd[1], u"Same steps throughout", rules.stairwells(odd))
      == rules.FAIL)

# --------------------------------------------------------------------------
# 6.4.9 second paragraph, 6.4.10
# --------------------------------------------------------------------------

other = dict(use=u"annar")
check("other: band a, 4h + b 960 passes",
      status_of(stair(use_value=u"annar", riser=0.120, going=0.480),
                u"Rise and going for the slope") == rules.PASS)
check("other: band c at 50 degrees, riser 210 passes, 230 fails",
      status_of(at(50, 0.210, **other), u"Rise and going for the slope")
      == rules.PASS and
      status_of(at(50, 0.230, **other), u"Rise and going for the slope")
      == rules.FAIL)
check("other: band d, 4/3h + b 509 passes",
      status_of(stair(use_value=u"annar", riser=0.283, going=0.132),
                u"Rise and going for the slope") == rules.PASS)
check("other: band e, riser 320 passes, 300 fails",
      status_of(at(80, 0.320, **other), u"Rise and going for the slope")
      == rules.PASS and
      status_of(at(80, 0.300, **other), u"Rise and going for the slope")
      == rules.FAIL)
out = dict(use_value=u"uti", riser=0.150, going=0.300)
check("outdoor: riser 160 passes, 161 fails",
      status_of(stair(**dict(out, riser=0.160)), u"Riser") == rules.PASS and
      status_of(stair(**dict(out, riser=0.161)), u"Riser") == rules.FAIL)
check("outdoor: going 280 passes, 279 fails",
      status_of(stair(**dict(out, going=0.280)), u"Going") == rules.PASS and
      status_of(stair(**dict(out, going=0.279)), u"Going") == rules.FAIL)
check("outdoor: steep steps are a question",
      status_of(stair(**dict(out, riser=0.160, going=0.250)), u"Slope")
      == rules.CHECK)

# --------------------------------------------------------------------------
# ÍST EN ISO 14122
# --------------------------------------------------------------------------

check("ISO: pitch decides the kind of access",
      [rules.access_kind(a) for a in (15, 20, 21, 45, 46, 75, 76)] ==
      [u"ramp", u"ramp", u"stair", u"stair", u"step ladder", u"step ladder",
       u"fixed ladder"])
check("ISO: a ramp fails as a stair",
      status_of(at(18, 0.100), u"Kind of access") == rules.FAIL)
iso = dict(use_value=u"To_Machinery", runs=[rules.Run(11, 1.8, 10)])
good_iso = stair(riser=0.180, going=0.270, nosing=0.010, **iso)
_use, found = rules.check(good_iso, rules.stairwells([good_iso]))
check("ISO stair: 180 / 270 with 10 mm overlap passes",
      rules.worst(found) == rules.PASS, found)
check("ISO stair: Byggingarreglugerð is not applied",
      not any(u"Byggingarregluger" in r.source for r in found))
check("ISO stair: rules are kind, g + 2h, going, overlap, pitch, same steps",
      [r.rule for r in found] == [u"Kind of access", u"g + 2h", u"Going",
                                  u"Overlap", u"Pitch",
                                  u"Same steps throughout"],
      [r.rule for r in found])
check("ISO stair: g + 2h 660 passes, 661 fails",
      status_of(stair(riser=0.180, going=0.300, **iso), u"g + 2h")
      == rules.PASS and
      status_of(stair(riser=0.180, going=0.301, **iso), u"g + 2h")
      == rules.FAIL)
check("ISO stair: g + 2h 600 passes, 599 fails",
      status_of(stair(riser=0.170, going=0.260, **iso), u"g + 2h")
      == rules.PASS and
      status_of(stair(riser=0.170, going=0.259, **iso), u"g + 2h")
      == rules.FAIL)
check("ISO stair: going 210 passes, 209 fails",
      status_of(stair(riser=0.195, going=0.210, **iso), u"Going")
      == rules.PASS and
      status_of(stair(riser=0.195, going=0.209, **iso), u"Going")
      == rules.FAIL)
check("ISO stair: going 310 passes, 311 fails",
      status_of(stair(riser=0.150, going=0.310, **iso), u"Going")
      == rules.PASS and
      status_of(stair(riser=0.150, going=0.311, **iso), u"Going")
      == rules.FAIL)
check("ISO stair: overlap 10 passes, 9 fails",
      status_of(stair(nosing=0.010, **iso), u"Overlap") == rules.PASS and
      status_of(stair(nosing=0.009, **iso), u"Overlap") == rules.FAIL)
check("ISO stair: a 40 degree stair is a question, recommended 30 to 38",
      status_of(at(40, 0.200), u"Pitch") == rules.CHECK and
      status_of(at(34, 0.180), u"Pitch") == rules.PASS)
check("ISO stair: a flight with another riser fails 5.4",
      status_of(stair(riser=0.180, going=0.270, use_value=u"To_Machinery",
                      runs=[rules.Run(11, 1.8, 10), rules.Run(12, 1.9, 10)]),
                u"Same steps throughout") == rules.FAIL)
check("ISO step ladder: 50 degrees, rise 150 to 200",
      status_of(at(50, 0.200), u"Rise") == rules.PASS and
      status_of(at(50, 0.201), u"Rise") == rules.FAIL and
      status_of(at(50, 0.149), u"Rise") == rules.FAIL)
check("ISO step ladder: 70 degrees, rise 230 to 300, and steep is a "
      "question", status_of(at(70, 0.300), u"Rise") == rules.PASS and
      status_of(at(70, 0.229), u"Rise") == rules.FAIL and
      status_of(at(70, 0.250), u"Pitch") == rules.CHECK)
narrow = stair(riser=0.150, going=0.079, **iso)
check("ISO step ladder: a going of 79 mm fails 6.1",
      rules.access_kind(rules.slope_of(0.150, 0.079)) == u"step ladder" and
      status_of(narrow, u"Going") == rules.FAIL)
check("ISO step ladder: a going of 80 mm passes 6.1",
      status_of(stair(riser=0.150, going=0.080, **iso), u"Going")
      == rules.PASS)
check("ISO step ladder: uneven rises are only a question (6.5 says "
      "wherever possible)",
      status_of(at(50, 0.180, runs=[rules.Run(11, 1.8, 10),
                                    rules.Run(12, 1.9, 10)]),
                u"Same steps throughout") == rules.CHECK)
check("ISO fixed ladder: rung spacing 225 to 300",
      status_of(at(80, 0.250), u"Rung spacing") == rules.PASS and
      status_of(at(80, 0.301), u"Rung spacing") == rules.FAIL and
      status_of(at(80, 0.224), u"Rung spacing") == rules.FAIL)
check("ISO: a fixed ladder cites part 4",
      u"14122-4" in u" ".join(r.source for r in
                              rules.check(at(80, 0.25), {})[1]))

# --------------------------------------------------------------------------
# No use, nothing to read
# --------------------------------------------------------------------------

_use, found = rules.check(stair(use_value=None), {})
check("no use: one not checked result, and no rules applied",
      [r.status for r in found] == [rules.NOT_CHECKED])
_use, found = rules.check(stair(riser=None), {})
check("unreadable riser: not checked, not passed",
      [r.status for r in found] == [rules.NOT_CHECKED])


# ==========================================================================
# The script, against a fake Revit
# ==========================================================================

class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeXYZ(object):
    def __init__(self, x, y, z):
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


class FakeParam(object):
    def __init__(self, value=u"", read_only=False):
        self.value = value
        self.IsReadOnly = read_only

    def AsString(self):
        return self.value

    def Set(self, value):
        if self.IsReadOnly:
            return False
        self.value = value
        return True


class FakeElement(object):
    def __init__(self, doc, value, type_id=None):
        self.doc = doc
        self.Id = FakeId(value)
        self.type_id = type_id
        doc.elements[value] = self

    def GetTypeId(self):
        return self.type_id


class FakeRunType(FakeElement):
    def __init__(self, doc, value, nosing):
        FakeElement.__init__(self, doc, value)
        self.HasTreads = True
        self.NosingLength = nosing * FT


class FakeStairsType(FakeElement):
    def __init__(self, doc, value, run_type):
        FakeElement.__init__(self, doc, value)
        self.RunType = run_type.Id

    @property
    def Name(self):
        raise AttributeError("Name")


class FakeRun(FakeElement):
    def __init__(self, doc, value, base, risers, riser):
        FakeElement.__init__(self, doc, value)
        self.BaseElevation = base * FT
        self.TopElevation = (base + risers * riser) * FT
        self.ActualRisersNumber = risers


class FakeStairs(FakeElement):
    def __init__(self, doc, value, use=u"", riser=0.170, going=0.280,
                 nosing=0.020, base=0.0, risers=20, x=2.0,
                 has_param=True, read_only=False, level=u"DA11.E01",
                 last_riser=None):
        run_type = FakeRunType(doc, value * 10 + 7, nosing)
        stairs_type = FakeStairsType(doc, value * 10 + 8, run_type)
        FakeElement.__init__(self, doc, value, stairs_type.Id)
        self.editing = False
        self.ActualRiserHeight = riser * FT
        self.ActualTreadDepth = going * FT
        self.ActualRisersNumber = risers
        self.BaseElevation = base * FT
        self.TopElevation = (base + risers * riser) * FT
        self.param = FakeParam(use, read_only) if has_param else None
        self.level = FakeElement(doc, value * 10 + 9)
        self.level.Name = level
        half = risers // 2
        self.runs = [FakeRun(doc, value * 10 + 1, base, half, riser),
                     FakeRun(doc, value * 10 + 2, base + half * riser,
                             risers - half, last_riser or riser)]
        self.x = x

    def IsInEditMode(self):
        return self.editing

    def GetType(self):
        return Namespace(Name=u"Stairs")

    def LookupParameter(self, name):
        if name != u"AVH_Stair_Use":
            return None
        if self.param is None and self.doc.bound:
            self.param = FakeParam(u"")
        return self.param

    def get_Parameter(self, which):
        level = self.level
        return Namespace(AsElementId=lambda: level.Id)

    def GetStairsRuns(self):
        return [run.Id for run in self.runs]

    def get_BoundingBox(self, view):
        return Namespace(Min=FakeXYZ((self.x - 0.6) * FT, 0.0,
                                     self.BaseElevation),
                         Max=FakeXYZ((self.x + 0.6) * FT, 6.0 * FT,
                                     self.TopElevation))


class FakeDoc(object):
    def __init__(self, commit_status="Committed"):
        self.elements = {}
        self.stairs = []
        self.bound = False
        self.commit_status = commit_status
        self.transactions = []
        self.Application = Namespace(
            SharedParametersFilename=u"C:\\AVH\\shared.txt",
            OpenSharedParameterFile=lambda: Namespace(Groups=Namespace(
                Create=lambda name: Namespace(Definitions=Namespace(
                    Create=lambda options: Namespace(GUID=options.GUID))))),
            Create=Namespace(
                NewCategorySet=lambda: Namespace(Insert=lambda c: None),
                NewInstanceBinding=lambda cats: u"instance"))
        doc = self

        def insert(definition, binding, group):
            doc.bound = True
            doc.inserted = (definition, binding, group)
            return True
        self.ParameterBindings = Namespace(Insert=insert)
        self.inserted = (None, None, None)

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def add(self, value, **kwargs):
        made = FakeStairs(self, value, **kwargs)
        self.stairs.append(made)
        return made


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False

    def Start(self):
        self.started = True
        self.saved = [(s, s.param.value if s.param else None)
                      for s in self.doc.stairs], self.doc.bound
        self.doc.transactions.append([self.name, "start"])

    def HasStarted(self):
        return self.started

    def RollBack(self):
        for each, value in self.saved[0]:
            if each.param is not None and value is not None:
                each.param.value = value
        self.doc.bound = self.saved[1]
        self.doc.transactions[-1].append("rollback")

    def Commit(self):
        self.doc.transactions[-1].append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.cls = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def OfCategory(self, category):
        self.cls = "category"
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        if self.cls is FakeStairs or self.cls == "category":
            return iter(list(self.doc.stairs))
        return iter([])


class Recorder(object):
    def __init__(self, add=False, tick=None, use=None):
        self.add = add
        self.tick = tick
        self.use = use
        self.alerts = []
        self.questions = []
        self.printed = []
        self.pickers = []
        self.results = []

    def alert(self, message, **kwargs):
        if kwargs.get("yes") and kwargs.get("no"):
            self.questions.append(message)
            return self.add
        self.alerts.append(message)

    def show(self, items, multiselect=False, title=u"", button_name=u"",
             **kwargs):
        self.pickers.append((title, list(items)))
        if multiselect:
            if self.tick is None:
                return None
            picked = list(items) if self.tick == "ALL" else [
                i for i in items if any(t in i for t in self.tick)]
            self.tick = None          # one round, then stop
            return picked or None
        if self.use is None:
            return None
        for item in items:
            if item.startswith(self.use + u"  -  "):
                return item
        return None

    def print_md(self, text):
        self.printed.append(text)

    def lines(self):
        return u"\n".join(self.printed).split(u"\n")


def run_script(doc, recorder=None):
    recorder = recorder or Recorder()
    db = Namespace(
        Architecture=Namespace(Stairs=FakeStairs),
        FilteredElementCollector=FakeCollector,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        BuiltInParameter=Namespace(STAIRS_BASE_LEVEL_PARAM="base"),
        BuiltInCategory=Namespace(OST_Stairs="OST_Stairs"),
        RevitLinkInstance="RevitLinkInstance",
        ElementId=Namespace(InvalidElementId=FakeId(-1)),
        ExternalDefinitionCreationOptions=lambda name, spec: Namespace(
            name=name, GUID=None),
        SpecTypeId=Namespace(String=Namespace(Text="text")),
        GroupTypeId=Namespace(IdentityData="identity"),
        Category=Namespace(GetCategory=lambda d, c: "Stairs"),
    )
    system = types.ModuleType("System")
    system.Guid = lambda text: u"guid:" + text
    sys.modules["System"] = system
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc)
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
        found = runpy.run_path(SCRIPT, run_name="__main__")
        recorder.results = list(found.get("RESULTS", []))
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        for name in ("pyrevit", "System"):
            sys.modules.pop(name, None)
    return recorder


def results_for(recorder, stair_id):
    for reading, use, found in recorder.results:
        if reading.data.element_id == stair_id:
            return use, found
    return None, []


def rule_status(recorder, stair_id, rule):
    return [r.status for r in results_for(recorder, stair_id)[1]
            if r.rule == rule]


# 1. A passing stair and a failing one.
doc = FakeDoc()
doc.add(1, use=u"almennur", x=20.0)
doc.add(2, use=u"almennur", riser=0.190, going=0.260)
rec = run_script(doc)
check("script: both stairs checked, the edit mode method honoured",
      len(rec.results) == 2, rec.alerts)
check("script: riser read in metres from feet",
      any(u"190 mm" in r.message for r in results_for(rec, 2)[1]
          if r.rule == u"Riser"))
check("script: the failing stair fails on riser",
      rule_status(rec, 2, u"Riser") == [rules.FAIL])
check("script: the passing stair passes",
      rules.worst(results_for(rec, 1)[1]) == rules.PASS,
      results_for(rec, 1)[1])
summary = [line for line in rec.lines() if line.startswith(u"| [")]
check("report: one summary row per stair, failing first",
      len(summary) == 2 and summary[0].startswith(u"| [2]"), summary)
check("report: the summary shows riser, going and pitch",
      summary and u"| 190 | 260 | 36.2\u00b0 |" in summary[0], summary[:1])
check("report: the summary is printed in one call",
      any(chunk.count(u"\n| [") == 2 for chunk in rec.printed))
check("report: problems one per line, with the source",
      any(line.startswith(u"- **FAIL** Byggingarregluger\u00f0 6.4.9 1. mgr. "
                          u"Riser: 190 mm") for line in rec.lines()),
      [l for l in rec.lines() if l.startswith(u"- ")])
check("report: the passing stair has no problem section",
      not any(line.startswith(u"#### [1]") for line in rec.lines()))
check("report: the title is the new name",
      rec.printed and u"G\u00f6nguhlutfall stiga" in rec.printed[0])

# 2. To_Machinery uses ISO 14122, not the regulation.
doc = FakeDoc()
doc.add(1, use=u"To_Machinery", riser=0.180, going=0.270, nosing=0.010)
doc.add(2, use=u"almennur", riser=0.180, going=0.270, nosing=0.010, x=20.0)
rec = run_script(doc)
check("machinery: the same steps pass ISO 14122",
      rules.worst(results_for(rec, 1)[1]) == rules.PASS,
      results_for(rec, 1)[1])
check("machinery: and fail Byggingarreglugerð as a general stair",
      rules.FAIL in rule_status(rec, 2, u"Nosing"),
      results_for(rec, 2)[1])
check("machinery: the summary shows the code as typed",
      any(u"| To_Machinery |" in line for line in rec.lines()))

# 3. A flight with another riser.
doc = FakeDoc()
doc.add(1, use=u"almennur", last_riser=0.175)
rec = run_script(doc)
check("flights: another riser in the second flight fails",
      rule_status(rec, 1, u"Same steps throughout") == [rules.FAIL],
      results_for(rec, 1)[1])

# 4. No use, an old code, an unknown code.
doc = FakeDoc()
doc.add(1, use=u"")
doc.add(2, use=u"fyrirtaeki", x=8.0)
doc.add(3, use=u"skrifstofa", x=14.0)
rec = run_script(doc)
check("no use: not checked, but its riser and going are still shown",
      any(line.startswith(u"| [1]") and u"| 170 | 280 |" in line and
          u"(none)" in line for line in rec.lines()), rec.lines())
check("old code: fyrirtaeki is checked as almennur",
      results_for(rec, 2)[0] is not None and
      results_for(rec, 2)[0].code == u"almennur")
check("unknown code: named in the report",
      any(u"skrifstofa" in line for line in rec.lines()))

# 5. No parameter: add it, classify one stair as To_Machinery.
doc = FakeDoc()
doc.add(1, has_param=False)
doc.add(2, has_param=False, x=8.0)
rec = run_script(doc, Recorder(add=True, tick=[u"id 1"],
                               use=u"To_Machinery"))
check("parameter: offered and bound with AVH's GUID",
      doc.bound and getattr(doc.inserted[0], "GUID", None) ==
      u"guid:f03e1b0f-96ae-4317-ba74-010f759f77eb")
check("parameter: the user's shared parameter file is put back",
      doc.Application.SharedParametersFilename == u"C:\\AVH\\shared.txt")
check("classify: To_Machinery is offered",
      any(u"To_Machinery  -  " in item for title, items in rec.pickers
          for item in items))
check("classify: the ticked stair is set to To_Machinery, as typed",
      getattr(doc.stairs[0].param, "value", None) == u"To_Machinery",
      getattr(doc.stairs[0].param, "value", None))
check("classify: and checked by ISO",
      results_for(rec, 1)[0] is not None and
      results_for(rec, 1)[0].kind == u"machinery")

doc = FakeDoc(commit_status="RolledBack")
doc.add(1, use=u"")
rec = run_script(doc, Recorder(tick="ALL", use=u"annar"))
check("classify: a rejected commit is reported and not kept",
      doc.stairs[0].param.value == u"" and
      any(u"rejected" in a for a in rec.alerts), rec.alerts)
doc = FakeDoc()
doc.add(1, use=u"", read_only=True)
rec = run_script(doc, Recorder(tick="ALL", use=u"annar"))
check("classify: a read only parameter is reported",
      any(u"would not take" in a for a in rec.alerts), rec.alerts)

# 6. Edit mode and no stairs.
doc = FakeDoc()
doc.add(1, use=u"almennur")
doc.add(2, use=u"almennur", x=8.0)
doc.stairs[1].editing = True
rec = run_script(doc)
check("edit mode: the stair being edited is skipped, the other checked",
      len(rec.results) == 1 and rec.results[0][0].data.element_id == 1)
doc = FakeDoc()
rec = run_script(doc)
check("no stairs: said so", any(u"No Revit stairs" in a for a in rec.alerts),
      rec.alerts)

# --------------------------------------------------------------------------

check("harness: a check that should fail does",
      status_of(stair(riser=0.300), u"Riser") == rules.FAIL)
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
