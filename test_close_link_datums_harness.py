# -*- coding: utf-8 -*-
"""
Runs the real Close Link Datums script against a mocked Revit.

The fake link type is behavioural: LoadFrom really applies the
WorksetConfiguration it is given to the link's worksets, so "every other
workset stays as it was" is checked against what the configuration
actually did, not against what the script meant it to do. A link whose
reload ignores the configuration is how the read-back check is proven.

Run outside Revit:

    python test_close_link_datums_harness.py
"""

import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Worksets.panel",
                      "Close Link Datums.pushbutton", "script.py")

import io        # noqa: E402
import shutil    # noqa: E402
import tempfile  # noqa: E402

from avh_worksets import links      # noqa: E402
from avh_schedules import crashlog  # noqa: E402

results = []

# The trace and crash log go to a throwaway folder, never to Documents.
LOG_DIR = tempfile.mkdtemp(prefix="avh_cld_")
crashlog.log_path = lambda extra=None, name=crashlog.LOG_NAME: \
    os.path.join(LOG_DIR, name)
TRACE = os.path.join(LOG_DIR, "AVH_close_link_datums_trace.log")
CRASH = os.path.join(LOG_DIR, "AVH_close_link_datums_crash.log")


def read_log(path):
    if not os.path.exists(path):
        return u""
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --------------------------------------------------------------------------
# The fake Revit
# --------------------------------------------------------------------------

_next_id = [900]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


class FakeId(object):
    def __init__(self, value=None):
        self.Value = value if value is not None else next_id()
        self.IntegerValue = self.Value

    def __eq__(self, other):
        return isinstance(other, FakeId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


class FakeWorksetId(FakeId):
    pass


class FakeWorkset(object):
    def __init__(self, key, name, is_open=True):
        self.Id = FakeWorksetId(key)
        self.Name = name
        self.IsOpen = is_open


class FakeCloudPath(object):
    def __init__(self, text):
        self.text = text


class FakeLinkDocument(object):
    def __init__(self, worksets, workshared=True, cloud=False):
        self.worksets = list(worksets)
        self.IsWorkshared = workshared
        self.IsModelInCloud = cloud

    def GetCloudModelPath(self):
        if not self.IsModelInCloud:
            raise Exception("not a cloud model")
        return FakeCloudPath(u"cloud")

    def state(self):
        return dict((w.Id.IntegerValue, w.IsOpen) for w in self.worksets)


class FakeConfiguration(object):
    def __init__(self, option):
        self.option = option
        self.opened = []

    def Open(self, ids):
        self.opened.extend(ids)


class FakeLoadResult(object):
    def __init__(self, value):
        self.LoadResult = value


class FakePath(object):
    def __init__(self, text):
        self.text = text


class FakeReference(object):
    def __init__(self, path):
        self.path = path

    def GetPath(self):
        return self.path


class FakePair(object):
    def __init__(self, value):
        self.Value = value


class FakeNetType(object):
    def __init__(self, full_name):
        self.FullName = full_name


class FakeNetException(object):
    def __init__(self, full_name, message):
        self._type = FakeNetType(full_name)
        self.Message = message

    def GetType(self):
        return self._type


class FakeRevitError(Exception):
    """What IronPython hands Python for a .NET exception: a terse str and
    the original on clsException."""

    def __init__(self, short, full_name, message):
        Exception.__init__(self, short)
        self.clsException = FakeNetException(full_name, message)


class FakeLinkType(object):
    def __init__(self, name, link_doc=None, nested=False,
                 path_raises=False, result="LinkLoaded",
                 ignores_config=False, no_path=False, no_file_path=False,
                 refuse=None, unloads_on_failure=False, cloud_raises=None):
        self.Id = FakeId()
        self.Name = name
        self.IsNestedLink = nested
        self.link_doc = link_doc
        self.path_raises = path_raises
        self.result = result
        self.ignores_config = ignores_config
        self.no_path = no_path
        self.no_file_path = no_file_path
        # route kind ("file", "resource", "cloud") -> LoadResult it gives
        self.refuse = refuse or {}
        self.unloads_on_failure = unloads_on_failure
        self.cloud_raises = cloud_raises
        self.loads = []

    def GetExternalFileReference(self):
        if self.no_path:
            raise Exception("no external file reference")
        if self.no_file_path:
            return None     # what every ACC link gave on a live project
        return FakeReference(FakePath(self.Name))

    def GetExternalResourceReferences(self):
        if self.no_path:
            return None
        return [FakePair(u"resource:" + self.Name)]

    def LoadFrom(self, path, config):
        self.loads.append((path, config))
        if self.path_raises and isinstance(path, FakePath):
            raise Exception("cloud path not accepted")
        kind = ("file" if isinstance(path, FakePath) else
                "cloud" if isinstance(path, FakeCloudPath) else "resource")
        if kind == "cloud" and self.cloud_raises is not None:
            raise self.cloud_raises
        refused = self.refuse.get(kind)
        if self.result != "LinkLoaded" or refused:
            if self.unloads_on_failure:
                self.link_doc = None
            return FakeLoadResult(refused or self.result)
        if not self.ignores_config:
            opened = set(i.IntegerValue for i in config.opened)
            for workset in self.link_doc.worksets:
                workset.IsOpen = workset.Id.IntegerValue in opened
        return FakeLoadResult("LinkLoaded")


class FakeLinkInstance(object):
    def __init__(self, link_type):
        self.Id = FakeId()
        self.link_type = link_type

    def GetTypeId(self):
        return self.link_type.Id

    def GetLinkDocument(self):
        return self.link_type.link_doc


class FakeDocument(object):
    def __init__(self, link_types, placed=None, family=False,
                 collector_raises=False):
        self.link_types = list(link_types)
        placed = self.link_types if placed is None else placed
        self.instances = [FakeLinkInstance(t) for t in placed]
        self.IsFamilyDocument = family
        self.collector_raises = collector_raises


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.items = []

    def OfClass(self, cls):
        if cls is FakeLinkType and self.doc.collector_raises:
            raise Exception("the collector failed")
        if cls is FakeLinkType:
            self.items = list(self.doc.link_types)
        elif cls is FakeLinkInstance:
            self.items = list(self.doc.instances)
        return self

    def __iter__(self):
        return iter(self.items)


class FakeWorksetCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfKind(self, kind):
        return self

    def __iter__(self):
        return iter(self.doc.worksets)


class FakeTransaction(object):
    started = 0

    def __init__(self, *args):
        pass

    def Start(self):
        FakeTransaction.started += 1


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class Recorder(object):
    def __init__(self):
        self.alerts = []
        self.printed = []
        self.pickers = []

    def alert(self, message, **kwargs):
        self.alerts.append(message)

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.printed)


def run_script(doc, picked=None, picker_raises=False, print_raises=False):
    FakeTransaction.started = 0
    for path in (TRACE, CRASH):
        if os.path.exists(path):
            os.remove(path)
    recorder = Recorder()

    def show(items, **kwargs):
        if picker_raises:
            raise Exception("picker window failed")
        recorder.pickers.append(list(items))
        if callable(picked):
            return picked(list(items))
        return picked

    db = Namespace(
        Transaction=FakeTransaction,
        FilteredElementCollector=FakeCollector,
        FilteredWorksetCollector=FakeWorksetCollector,
        WorksetKind=Namespace(UserWorkset="UserWorkset"),
        RevitLinkType=FakeLinkType,
        RevitLinkInstance=FakeLinkInstance,
        WorksetId=FakeWorksetId,
        WorksetConfiguration=FakeConfiguration,
        WorksetConfigurationOption=Namespace(
            CloseAllWorksets="CloseAllWorksets",
            OpenAllWorksets="OpenAllWorksets"),
        LinkLoadResultType=Namespace(LinkLoaded="LinkLoaded"),
        BuiltInParameter=Namespace(SYMBOL_NAME_PARAM="SYMBOL_NAME_PARAM"),
    )

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=doc)
    pyrevit.DB = db
    pyrevit.forms = Namespace(alert=recorder.alert,
                              SelectFromList=Namespace(show=show))
    def print_md(text):
        if print_raises:
            raise Exception("output window failed")
        recorder.print_md(text)

    pyrevit.script = Namespace(
        get_output=lambda: Namespace(print_md=print_md,
                                     update_progress=lambda *a: None),
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None))
    sys.modules["pyrevit"] = pyrevit
    try:
        runpy.run_path(SCRIPT, run_name="__main__")
    finally:
        sys.modules.pop("pyrevit", None)
    return recorder


def standard_doc(datum_name=u"Shared Views, Levels, Grids", datum_open=True):
    """Architecture, structure, a datum workset, and one already closed."""
    return FakeLinkDocument([
        FakeWorkset(1, u"Workset1", True),
        FakeWorkset(2, datum_name, datum_open),
        FakeWorkset(3, u"Furniture", False),
        FakeWorkset(4, u"Exterior", True),
    ])


# --------------------------------------------------------------------------
# The plain data, no Revit
# --------------------------------------------------------------------------

check("name: AVH's own", links.is_datum_workset(u"Shared Views, Levels, Grids"))
check("name: Revit's older default",
      links.is_datum_workset(u"Shared Levels and Grids"))
check("name: ampersand and case do not matter",
      links.is_datum_workset(u"shared levels & GRIDS"))
check("name: stray spaces do not matter",
      links.is_datum_workset(u"  Shared Views,  Levels, Grids "))
check("name: an ordinary workset is not it",
      not links.is_datum_workset(u"Workset1"))
check("name: a workset merely containing the words is not it",
      not links.is_datum_workset(u"Shared Levels and Grids OLD"))

both = [(5, u"Shared Levels and Grids", True),
        (6, u"Shared Views, Levels, Grids", True)]
check("find: AVH's name preferred when both exist",
      links.find_datum_workset(both)[0] == 6)
check("find: none", links.find_datum_workset([(1, u"A", True)]) is None)

worksets = [(1, u"A", True), (2, u"Shared Views, Levels, Grids", True),
            (3, u"B", False)]
check("keep open: what is open, less the datum workset",
      links.keep_open(worksets, 2) == [1])

check("verify: closed and nothing else changed",
      links.verify(worksets, [(1, u"A", True), (2, u"S", False),
                              (3, u"B", False)], 2) == links.CLOSED)
check("verify: still open",
      links.verify(worksets, worksets, 2) == links.STILL_OPEN)
check("verify: a workset that was closed came back open",
      links.verify(worksets, [(1, u"A", True), (2, u"S", False),
                              (3, u"B", True)], 2) == links.OTHERS_CHANGED)
check("verify: missing from the read-back counts as still open",
      links.verify(worksets, [(1, u"A", True)], 2) == links.STILL_OPEN)

labels, mapping = links.picker_labels([u"ARK", u"BUR", u"ARK"])
check("labels: two links with one name stay distinguishable",
      len(set(labels)) == 3 and mapping[labels[2]] == 2, str(labels))

check("assess: nested", links.assess(links.LinkInfo(u"x", nested=True))
      == links.NESTED)
check("assess: unloaded", links.assess(links.LinkInfo(u"x", loaded=False))
      == links.NOT_LOADED)
check("assess: unreadable open state is not guessed",
      links.assess(links.LinkInfo(u"x", worksets=[
          (2, u"Shared Views, Levels, Grids", None)])) == links.STATE_UNKNOWN)


# --------------------------------------------------------------------------
# 1. The ordinary run
# --------------------------------------------------------------------------

ark_doc = standard_doc()
bur_doc = standard_doc()
ark = FakeLinkType(u"ARK.rvt", ark_doc)
bur = FakeLinkType(u"BUR.rvt", bur_doc)
doc = FakeDocument([bur, ark])
recorder = run_script(doc, picked=[u"ARK.rvt"])

check("run: both links offered, sorted",
      recorder.pickers and recorder.pickers[0] == [u"ARK.rvt", u"BUR.rvt"],
      str(recorder.pickers))
check("run: the datum workset is closed in the chosen link",
      ark_doc.state()[2] is False)
check("run: the other open worksets stay open",
      ark_doc.state()[1] is True and ark_doc.state()[4] is True,
      str(ark_doc.state()))
check("run: a workset already closed stays closed",
      ark_doc.state()[3] is False)
check("run: reloaded once", len(ark.loads) == 1)
check("run: started from everything closed",
      ark.loads[0][1].option == "CloseAllWorksets")
check("run: the link not picked is not reloaded", not bur.loads)
check("run: and keeps its workset open", bur_doc.state()[2] is True)
check("run: no transaction opened", FakeTransaction.started == 0)
check("run: reported as closed",
      u"| ARK.rvt | closed | file path |" in recorder.text(),
      recorder.text())
check("run: summary line", u"**1 of 1 closed.**" in recorder.text())
check("run: no alert when everything worked", not recorder.alerts,
      u" | ".join(recorder.alerts))

# Several at once.
ark_doc = standard_doc()
bur_doc = standard_doc()
ark = FakeLinkType(u"ARK.rvt", ark_doc)
bur = FakeLinkType(u"BUR.rvt", bur_doc)
recorder = run_script(FakeDocument([ark, bur]),
                      picked=[u"ARK.rvt", u"BUR.rvt"])
check("several: both closed",
      ark_doc.state()[2] is False and bur_doc.state()[2] is False)
check("several: summary", u"**2 of 2 closed.**" in recorder.text())

# The older name is found too.
old_doc = standard_doc(datum_name=u"Shared Levels and Grids")
old = FakeLinkType(u"OLD.rvt", old_doc)
recorder = run_script(FakeDocument([old]), picked=[u"OLD.rvt"])
check("older name: closed", old_doc.state()[2] is False)

# Cancelling changes nothing.
ark_doc = standard_doc()
ark = FakeLinkType(u"ARK.rvt", ark_doc)
recorder = run_script(FakeDocument([ark]), picked=None)
check("cancelled: not reloaded", not ark.loads)
check("cancelled: still open", ark_doc.state()[2] is True)

# Duplicate names: the second one picked is the second one closed.
first_doc = standard_doc()
second_doc = standard_doc()
first = FakeLinkType(u"ARK.rvt", first_doc)
second = FakeLinkType(u"ARK.rvt", second_doc)
recorder = run_script(FakeDocument([first, second]),
                      picked=lambda items: [items[1]])
check("same name: only the one picked is reloaded",
      len(first.loads) + len(second.loads) == 1 and
      first_doc.state()[2] != second_doc.state()[2])


# --------------------------------------------------------------------------
# 2. Links that are not offered, and why
# --------------------------------------------------------------------------

closed_doc = standard_doc(datum_open=False)
no_datum_doc = FakeLinkDocument([FakeWorkset(1, u"Workset1", True)])
plain_doc = FakeLinkDocument([], workshared=False)
good_doc = standard_doc()

closed = FakeLinkType(u"CLOSED.rvt", closed_doc)
no_datum = FakeLinkType(u"NODATUM.rvt", no_datum_doc)
plain = FakeLinkType(u"PLAIN.rvt", plain_doc)
unloaded = FakeLinkType(u"UNLOADED.rvt", None)
nested = FakeLinkType(u"NESTED.rvt", standard_doc(), nested=True)
good = FakeLinkType(u"GOOD.rvt", good_doc)
doc = FakeDocument([closed, no_datum, plain, unloaded, nested, good])
recorder = run_script(doc, picked=[u"GOOD.rvt"])

check("not offered: only the one that can close is in the picker",
      recorder.pickers and recorder.pickers[0] == [u"GOOD.rvt"],
      str(recorder.pickers))
for link, reason in ((u"CLOSED.rvt", links.ALREADY_CLOSED),
                     (u"NODATUM.rvt", links.NO_SUCH_WORKSET),
                     (u"PLAIN.rvt", links.NOT_WORKSHARED),
                     (u"UNLOADED.rvt", links.NOT_LOADED),
                     (u"NESTED.rvt", links.NESTED)):
    check(u"not offered: {0} named with its reason".format(link),
          u"- {0}: {1}".format(link, reason) in recorder.text(),
          recorder.text())
check("not offered: none of them reloaded",
      not (closed.loads or no_datum.loads or plain.loads or nested.loads))

# Nothing to offer at all.
closed = FakeLinkType(u"CLOSED.rvt", standard_doc(datum_open=False))
recorder = run_script(FakeDocument([closed]), picked=[u"CLOSED.rvt"])
check("nothing to offer: no picker", not recorder.pickers)
check("nothing to offer: said so",
      any(u"nothing to close" in a for a in recorder.alerts))
check("nothing to offer: and why, per link",
      links.ALREADY_CLOSED in recorder.text())

recorder = run_script(FakeDocument([]), picked=None)
check("no links: said so", u"no Revit links" in recorder.text())

recorder = run_script(FakeDocument([], family=True), picked=None)
check("family document: refused", u"family document" in recorder.text())


# --------------------------------------------------------------------------
# 3. Reloads that go wrong
# --------------------------------------------------------------------------

# The file path is refused (the cloud case this is most uncertain about);
# the external resource route is tried next and named in the report.
cloud_doc = standard_doc()
cloud = FakeLinkType(u"CLOUD.rvt", cloud_doc, path_raises=True)
recorder = run_script(FakeDocument([cloud]), picked=[u"CLOUD.rvt"])
check("path refused: falls back to the external resource",
      cloud_doc.state()[2] is False)
check("path refused: tried twice", len(cloud.loads) == 2)
check("path refused: the route is reported",
      u"| CLOUD.rvt | closed | external resource |" in recorder.text(),
      recorder.text())

# Revit says the load failed.
fail_doc = standard_doc()
fail = FakeLinkType(u"FAIL.rvt", fail_doc, result="LinkNotFound")
recorder = run_script(FakeDocument([fail]), picked=[u"FAIL.rvt"])
check("load failed: not reported as closed",
      u"| FAIL.rvt | closed" not in recorder.text())
check("load failed: Revit's result is named",
      u"LinkNotFound" in recorder.text(), recorder.text())
check("load failed: the user is told",
      any(u"could not be closed" in a for a in recorder.alerts))

# Revit says loaded but ignored the configuration: only the read-back
# catches this.
deaf_doc = standard_doc()
deaf = FakeLinkType(u"DEAF.rvt", deaf_doc, ignores_config=True)
recorder = run_script(FakeDocument([deaf]), picked=[u"DEAF.rvt"])
check("ignored configuration: reported as still open",
      links.STILL_OPEN in recorder.text(), recorder.text())
check("ignored configuration: not counted as closed",
      u"**0 of 1 closed.**" in recorder.text())
check("ignored configuration: the user is told",
      bool(recorder.alerts))

# No path at all.
lost = FakeLinkType(u"LOST.rvt", standard_doc(), no_path=True)
recorder = run_script(FakeDocument([lost]), picked=[u"LOST.rvt"])
check("no path: reported", links.NO_PATH in recorder.text())
check("no path: not attempted", not lost.loads)

# One failure does not stop the rest.
fail = FakeLinkType(u"FAIL.rvt", standard_doc(), result="LinkNotFound")
ok_doc = standard_doc()
ok = FakeLinkType(u"OK.rvt", ok_doc)
recorder = run_script(FakeDocument([fail, ok]),
                      picked=[u"FAIL.rvt", u"OK.rvt"])
check("one fails: the next is still closed", ok_doc.state()[2] is False)
check("one fails: summary counts it right",
      u"**1 of 2 closed.**" in recorder.text())


# --------------------------------------------------------------------------
# 3b. ACC links, as a live project's behaved
# --------------------------------------------------------------------------

def acc_doc():
    doc_ = standard_doc()
    doc_.IsModelInCloud = True
    return doc_


# No file path, the external resource route works: 5 of 6 on a live project.
ok_doc = acc_doc()
ok = FakeLinkType(u"ACC_OK.rvt", ok_doc, no_file_path=True)
recorder = run_script(FakeDocument([ok]), picked=[u"ACC_OK.rvt"])
check("acc: closed by the external resource, as on the live project",
      u"| ACC_OK.rvt | closed | external resource |" in recorder.text(),
      recorder.text())
check("acc: one reload, the cloud path not needed", len(ok.loads) == 1)

# The sixth: the resource route says LinkNotFound, the cloud path works.
odd_doc = acc_doc()
odd = FakeLinkType(u"ACC_ODD.rvt", odd_doc, no_file_path=True,
                   refuse={"resource": "LinkNotFound"})
recorder = run_script(FakeDocument([odd]), picked=[u"ACC_ODD.rvt"])
check("acc: LinkNotFound falls through to the cloud model path",
      odd_doc.state()[2] is False)
check("acc: and the report names that route",
      u"| ACC_ODD.rvt | closed | cloud model path |" in recorder.text(),
      recorder.text())
check("acc: the order was resource, then cloud",
      [type(p).__name__ for p, _c in odd.loads] == ["str", "FakeCloudPath"],
      str([type(p).__name__ for p, _c in odd.loads]))

# Both refuse and the first refusal unloads the link: the cloud path was
# read beforehand, so it is still tried, and the report says the link is
# now unloaded.
bad_doc = acc_doc()
bad = FakeLinkType(u"ACC_BAD.rvt", bad_doc, no_file_path=True,
                   refuse={"resource": "LinkNotFound",
                           "cloud": "LinkNotFound"},
                   unloads_on_failure=True)
recorder = run_script(FakeDocument([bad]), picked=[u"ACC_BAD.rvt"])
check("acc: the cloud path is tried even after the link unloaded",
      len(bad.loads) == 2, str(len(bad.loads)))
check("acc: each route's refusal is named",
      u"external resource: LinkNotFound" in recorder.text() and
      u"cloud model path: LinkNotFound" in recorder.text(), recorder.text())
check("acc: the user is told the link is now unloaded",
      u"now unloaded" in recorder.text())

# The live run's second failure: the cloud route raised, and the report
# said only "path". The type and full .NET message must come through.
terse_doc = acc_doc()
terse = FakeLinkType(
    u"ELG_XX02.rvt", terse_doc, no_file_path=True,
    refuse={"resource": "LinkNotFound"},
    cloud_raises=FakeRevitError(
        "path", "Autodesk.Revit.Exceptions.ArgumentException",
        u"The model path is not valid for this link.\nParameter name: path"))
recorder = run_script(FakeDocument([terse]), picked=[u"ELG_XX02.rvt"])
check("exception: the .NET type is in the report",
      u"Autodesk.Revit.Exceptions.ArgumentException" in recorder.text(),
      recorder.text())
check("exception: the full message, not just 'path'",
      u"not valid for this link" in recorder.text())
check("exception: the crash log keeps the LoadFrom traceback",
      u"FakeRevitError" in read_log(CRASH), read_log(CRASH)[-600:])

# A plain Python exception still reads sensibly.
plain_doc = acc_doc()
plain_err = FakeLinkType(u"PLAIN_ERR.rvt", plain_doc, no_file_path=True,
                         refuse={"resource": "LinkNotFound"},
                         cloud_raises=ValueError("bad value"))
recorder = run_script(FakeDocument([plain_err]), picked=[u"PLAIN_ERR.rvt"])
check("exception: a Python one gives its message and type",
      u"bad value (ValueError)" in recorder.text(), recorder.text())

# A link not in the cloud never gets a cloud route.
local = FakeLinkType(u"LOCAL.rvt", standard_doc(),
                     refuse={"file": "LinkNotFound",
                             "resource": "LinkNotFound"})
recorder = run_script(FakeDocument([local]), picked=[u"LOCAL.rvt"])
check("local: no cloud route attempted",
      not any(isinstance(p, FakeCloudPath) for p, _c in local.loads))
check("local: a failed reload that left it loaded does not say unloaded",
      u"now unloaded" not in recorder.text())


# --------------------------------------------------------------------------
# 4. Failures report themselves
# --------------------------------------------------------------------------

# An ordinary run leaves a trace that ends at done, and no crash log.
ark = FakeLinkType(u"ARK.rvt", standard_doc())
recorder = run_script(FakeDocument([ark]), picked=[u"ARK.rvt"])
trace_text = read_log(TRACE)
check("trace: written as the run goes",
      u"reload ARK.rvt by file path" in trace_text, trace_text)
check("trace: ends at done", trace_text.strip().endswith(u"done"),
      trace_text)
check("trace: no crash log when nothing failed", not read_log(CRASH))

# The trace starts fresh on each click rather than growing forever.
recorder = run_script(FakeDocument([ark]), picked=None)
check("trace: a new click starts a new trace",
      u"reload ARK.rvt" not in read_log(TRACE), read_log(TRACE))

# A refused cloud path leaves its traceback behind, naming the link.
cloud = FakeLinkType(u"CLOUD.rvt", standard_doc(), path_raises=True)
recorder = run_script(FakeDocument([cloud]), picked=[u"CLOUD.rvt"])
crash_text = read_log(CRASH)
check("crash log: LoadFrom failure recorded with the link and route",
      u"LoadFrom on CLOUD.rvt by file path" in crash_text, crash_text[:400])
check("crash log: with the traceback",
      u"cloud path not accepted" in crash_text)

# Links that will not even list are not "no links".
recorder = run_script(FakeDocument([], collector_raises=True), picked=None)
check("unreadable links: not reported as a model with no links",
      u"no Revit links" not in recorder.text(), recorder.text())
check("unreadable links: said so",
      u"could not be read" in recorder.text(), recorder.text())
check("unreadable links: crash log written", bool(read_log(CRASH)))

# A picker that fails is not a cancel.
ark = FakeLinkType(u"ARK.rvt", standard_doc())
recorder = run_script(FakeDocument([ark]), picker_raises=True)
check("picker failed: said so",
      any(u"picker failed" in a for a in recorder.alerts),
      u" | ".join(recorder.alerts))
check("picker failed: nothing reloaded", not ark.loads)

# Anything unforeseen ends in a dialog naming the stage and the log.
ark_doc = standard_doc()
ark = FakeLinkType(u"ARK.rvt", ark_doc)
recorder = run_script(FakeDocument([ark]), picked=[u"ARK.rvt"],
                      print_raises=True)
check("unhandled: the user is told where it stopped",
      any(u"stopped at: report" in a for a in recorder.alerts),
      u" | ".join(recorder.alerts))
check("unhandled: and the log exists", bool(read_log(CRASH)))
check("unhandled: work already done is not undone",
      ark_doc.state()[2] is False)

# --------------------------------------------------------------------------
# 5. The crash logger itself
# --------------------------------------------------------------------------

# On Python 2 format_exc reports the most recently handled exception, so
# anything write() catches before taking the traceback replaces the one
# being logged. Python 3 restores the outer exception, so the bug cannot
# be reproduced here; the order of the calls is checked instead.
import traceback as _traceback  # noqa: E402

calls = []
real_format_exc = _traceback.format_exc
real_log_path = crashlog.log_path


def spy_format_exc(*args, **kwargs):
    calls.append("format_exc")
    return real_format_exc(*args, **kwargs)


def spy_log_path(*args, **kwargs):
    calls.append("log_path")
    return real_log_path(*args, **kwargs)


crashlog.traceback.format_exc = spy_format_exc
crashlog.log_path = spy_log_path
try:
    try:
        raise RuntimeError("the failure being logged")
    except RuntimeError:
        crashlog.write(u"stage", u"test", name="order_check.log")
finally:
    crashlog.traceback.format_exc = real_format_exc
    crashlog.log_path = real_log_path
check("crashlog: the traceback is taken before anything else runs",
      calls[:1] == ["format_exc"], str(calls))

with io.open(os.path.join(HERE, "lib", "avh_schedules", "crashlog.py"),
             encoding="utf-8") as handle:
    crashlog_source = handle.read()
check("crashlog: no Python 3 only isoformat(timespec=)",
      "timespec" not in crashlog_source)
order_text = read_log(os.path.join(LOG_DIR, "order_check.log"))
check("crashlog: the logged traceback is the real one",
      u"the failure being logged" in order_text, order_text[-300:])
check("crashlog: and it has a real timestamp",
      u"(no timestamp)" not in order_text)

shutil.rmtree(LOG_DIR, ignore_errors=True)


# --------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
