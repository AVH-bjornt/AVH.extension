# -*- coding: utf-8 -*-
"""Cut openings in the selected walls, floors and roofs wherever a pipe
or conduit passes through them.

Select the hosts, click, and a window asks for the shape (rectangular or
circular), how much larger than the pipe the hole should be (millimetres
clear on every side, or a percentage of the pipe), a step to round the
size up to, and whether to look at pipes in linked models too. The last
settings are remembered.

Then nothing is cut until the list of openings has been shown in the
output window and confirmed.

## How

Each pipe's centre line is put through the host's solid, which gives
the points where it meets the faces (`avh_openings.reader`). The size
comes from those points and the pipe's outer diameter, insulation
included, and allows for a pipe crossing at an angle
(`avh_openings.model`, tested without Revit).

- Walls, rectangular: `Document.Create.NewOpening(wall, corner, corner)`,
  Revit's own wall opening.
- Floors and roofs, either shape: `NewOpening(host, profile, False)`,
  a vertical cut from a rectangle or two half circles.
- Walls, circular: Revit has no round wall opening, so this places the
  wall hosted void family `AVH_Round_Opening` with its `AVH_Diameter`
  set. It is taken from the model if loaded, else from
  `assets/AVH_Round_Opening.rfa`. **That family is not in the
  repository yet.** Until it is, round wall openings are listed as
  skipped and say why; floors and roofs are unaffected.

## Safety

Each opening is its own transaction with Revit's failures collected
rather than shown, so one that Revit refuses is reported and the rest
still go in. All of them sit in one transaction group, so one Ctrl+Z
takes the whole run back. Every commit's status is checked.

After the run each host's solid is read again and each pipe put through
it once more. A pipe whose axis still meets the host where the opening
was meant to be is reported as not cut, whatever Revit said.

## Unverified

Not yet run in Revit: everything listed in `avh_openings.reader`, plus
`NewOpening` in both forms, `Arc.Create`, `CurveArray`,
`NewFamilyInstance` on a wall host, `TransactionGroup.Assimilate`, and
the settings window.
"""

__title__ = "Pipe\nOpenings"
__author__ = "AVH"
__doc__ = "Cut rectangular or circular openings in the selected walls, floors and roofs wherever a pipe or conduit passes through, sized from the pipe with the clearance you choose. Shows the list and asks before cutting anything."

import math
import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_openings import model, reader            # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Pipe Openings"

FAMILY_NAME = u"AVH_Round_Opening"
ASSET_NAME = "AVH_Round_Opening.rfa"
PARAM_DIAMETER = u"AVH_Diameter"

# Where a placed round opening may sit off the point asked for, in feet,
# before it is moved back. Half a millimetre.
PLACE_TOLERANCE = 0.5 / model.MM_PER_FOOT

HOST_WORDS = {model.WALL: u"Wall", model.FLOOR: u"Floor",
              model.ROOF: u"Roof"}


class Row(object):
    """One crossing: what it is, what will be made, and what happened."""

    def __init__(self, host, pipe, entry, exit, opening, reason):
        self.host = host
        self.pipe = pipe
        self.entry = entry
        self.exit = exit
        self.opening = opening
        self.reason = reason          # why it will not be made, or u""
        self.done = False
        self.result = u""
        self.warning = u""            # made, but worth a look
        self.normals = (None, None)   # the faces' normals, entry and exit


# Shown on a floor or roof row whose face normals could not be read, so
# the size assumed flat faces. That is exact for a flat slab and too
# small for a pipe leaning with a sloped roof, and nothing else in the
# report would show the difference.
FLAT_WARNING = u"face slope not read, sized as if flat: check it if " \
               u"the host slopes"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def remembered():
    stored = {}
    try:
        config = script.get_config()
        for key in model.DEFAULTS:
            value = config.get_option(key, None)
            if value is not None:
                stored[key] = value
    except BaseException as exc:
        logger.debug(to_text(exc))
    if "links" in stored:
        stored["links"] = to_text(stored["links"]).lower() in (
            u"true", u"1", u"yes")
    return model.settings_from_dict(stored)


def remember(settings):
    try:
        config = script.get_config()
        for key, value in settings.as_dict().items():
            setattr(config, key, to_text(value))
        script.save_config()
    except BaseException as exc:
        logger.debug(to_text(exc))


def ask_settings(settings, hosts_line):
    """Kept behind one function so the harness can stand in for it."""
    from avh_openings import window
    return window.ask(settings, hosts_line)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def selected_hosts(doc, uidoc):
    """(hosts, refused) from the selection. refused is [(element, why)]
    for walls, floors and roofs that cannot be opened."""
    hosts = []
    refused = []
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        ids = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        host, reason = reader.read_host(DB, element)
        if host is not None:
            hosts.append(host)
        elif reason:
            refused.append((element, reason))
    return hosts, refused


def plan(hosts, pipes, settings, round_symbol_ok):
    rows = []
    for host in hosts:
        found = reader.crossings(DB, host, pipes)
        for pipe, entry, exit, entry_normal, exit_normal in found:
            opening, reason = model.size_opening(
                entry, exit, pipe.diameter, host.frame, settings,
                entry_normal, exit_normal)
            if opening is not None and host.kind == model.WALL and \
                    settings.shape == model.CIRCULAR and \
                    not round_symbol_ok:
                reason = u"needs the {0} family, which is not in this " \
                         u"model or the extension".format(FAMILY_NAME)
                opening = None
            row = Row(host, pipe, entry, exit, opening, reason)
            row.normals = (entry_normal, exit_normal)
            if opening is not None and host.kind != model.WALL and \
                    (entry_normal is None or exit_normal is None):
                row.warning = FLAT_WARNING
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# The round wall opening family
# --------------------------------------------------------------------------

def find_symbol(doc):
    try:
        for symbol in DB.FilteredElementCollector(doc).OfClass(
                DB.FamilySymbol):
            try:
                if to_text(symbol.Family.Name) == FAMILY_NAME:
                    return symbol
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    return None


def asset_path():
    return os.path.join(_EXT_DIR, "assets", ASSET_NAME)


def round_family_available(doc):
    """Whether the family is loaded or can be. Loading needs a
    transaction of its own, so it is done here, before the openings, and
    only when a round wall opening is actually wanted."""
    if find_symbol(doc) is not None:
        return True
    path = asset_path()
    if not os.path.isfile(path):
        return False
    transaction = DB.Transaction(doc, u"Load " + FAMILY_NAME)
    transaction.Start()
    try:
        loaded = bool(doc.LoadFamily(path))
    except BaseException as exc:
        logger.debug(to_text(exc))
        loaded = False
    if not loaded:
        transaction.RollBack()
        return False
    return commit_checked(transaction) and find_symbol(doc) is not None


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

class Failures(object):
    def __init__(self):
        self.errors = []


def collect_failures(transaction):
    """Swallow warnings, record errors and roll back on one.

    The class is built in here and inherits the interface alone: mixing
    it with a plain base class breaks the method resolution order and
    the whole thing silently falls into the except branch."""
    record = Failures()
    try:
        warning = DB.FailureSeverity.Warning
        rollback = DB.FailureProcessingResult.ProceedWithRollBack
        proceed = DB.FailureProcessingResult.Continue

        class Collector(DB.IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                stop = False
                try:
                    messages = accessor.GetFailureMessages()
                except BaseException:
                    return proceed
                for failure in messages:
                    try:
                        severity = failure.GetSeverity()
                    except BaseException:
                        continue
                    if severity == warning:
                        try:
                            accessor.DeleteWarning(failure)
                        except BaseException:
                            pass
                        continue
                    try:
                        record.errors.append(
                            to_text(failure.GetDescriptionText()))
                    except BaseException:
                        record.errors.append(u"an unnamed Revit error")
                    stop = True
                return rollback if stop else proceed

        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(Collector())
        options.SetClearAfterRollback(True)
        try:
            options.SetForcedModalHandling(False)
        except BaseException:
            pass
        transaction.SetFailureHandlingOptions(options)
    except BaseException as exc:
        logger.debug(to_text(exc))
    return record


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    return True if committed is None else status == committed


def point(values):
    return DB.XYZ(values[0], values[1], values[2])


def at_height(values, z):
    return (values[0], values[1], values[2] if z is None else z)


def profile(opening, z=None):
    """A CurveArray for a floor or roof opening, drawn at height z (the
    opening's own centre when None)."""
    curves = DB.CurveArray()
    if opening.shape == model.CIRCULAR:
        center = point(at_height(opening.center, z))
        radius = opening.diameter / 2.0
        curves.Append(DB.Arc.Create(center, radius, 0.0, math.pi,
                                    DB.XYZ.BasisX, DB.XYZ.BasisY))
        curves.Append(DB.Arc.Create(center, radius, math.pi,
                                    2.0 * math.pi, DB.XYZ.BasisX,
                                    DB.XYZ.BasisY))
        return curves
    corners = [at_height(corner, z) for corner in opening.corners()]
    for index in range(4):
        curves.Append(DB.Line.CreateBound(
            point(corners[index]), point(corners[(index + 1) % 4])))
    return curves


# The offset of a floor or roof above its level, by class of host.
LEVEL_OFFSETS = {model.FLOOR: "FLOOR_HEIGHTABOVELEVEL_PARAM",
                 model.ROOF: "ROOF_LEVEL_OFFSET_PARAM"}


def sketch_heights(doc, row):
    """[(label, z)] heights to draw a floor or roof opening's sketch at,
    in the order they are tried. [(u"", None)] for a wall.

    The first Revit run (2.47.1, Revit 2025) refused a roof opening
    drawn at the middle of the roof with "Can't change plane of Opening
    Cut Sketch". Which plane Revit does want is not documented, so it
    is not guessed: each likely one is tried in a transaction of its
    own and the first Revit keeps is used, and the report says which.
    A vertical cut goes through the whole host whatever height its
    sketch is drawn at, so the height changes nothing about the hole.
    """
    host = row.host
    if host.kind == model.WALL:
        return [(u"", None)]
    found = []
    try:
        level = doc.GetElement(host.element.LevelId)
        elevation = level.Elevation
    except BaseException as exc:
        logger.debug(to_text(exc))
        level = None
        elevation = None
    if elevation is not None:
        offset = None
        member = getattr(DB.BuiltInParameter,
                         LEVEL_OFFSETS.get(host.kind, ""), None)
        if member is not None:
            try:
                parameter = host.element.get_Parameter(member)
                if parameter is not None and parameter.HasValue:
                    offset = parameter.AsDouble()
            except BaseException as exc:
                logger.debug(to_text(exc))
        if offset:
            found.append((u"its level and offset", elevation + offset))
        found.append((u"its level", elevation))
    found.append((u"the middle of the host", row.opening.center[2]))
    found.append((u"the project's zero", 0.0))
    unique = []
    for label, z in found:
        if all(abs(z - other) > 1e-4 for _, other in unique):
            unique.append((label, z))
    return unique


def place_round(doc, row, symbol):
    """The round wall opening family, placed and sized. Returns the
    instance; raises with a reason when it cannot be done right."""
    wall = row.host.element
    level = doc.GetElement(wall.LevelId)
    if not symbol.IsActive:
        symbol.Activate()
    instance = doc.Create.NewFamilyInstance(
        point(row.opening.center), symbol, wall, level,
        DB.Structure.StructuralType.NonStructural)
    parameter = instance.LookupParameter(PARAM_DIAMETER)
    if parameter is None or parameter.IsReadOnly:
        raise Exception(u"{0} has no instance parameter {1} that can be "
                        u"set".format(FAMILY_NAME, PARAM_DIAMETER))
    if not parameter.Set(row.opening.diameter):
        raise Exception(u"{0} would not take the value".format(
            PARAM_DIAMETER))
    # A new instance reports where it is only after a regenerate.
    doc.Regenerate()

    frame = row.opening.frame
    wanted = frame.local(row.opening.center)
    placed = frame.local(reader.xyz(instance.Location.Point))
    du = wanted[0] - placed[0]
    dv = wanted[1] - placed[1]
    if abs(du) > PLACE_TOLERANCE or abs(dv) > PLACE_TOLERANCE:
        delta = frame.to_world(du, dv, 0.0)
        DB.ElementTransformUtils.MoveElement(doc, instance.Id, point(delta))
        doc.Regenerate()
        placed = frame.local(reader.xyz(instance.Location.Point))
        if abs(wanted[0] - placed[0]) > PLACE_TOLERANCE or \
                abs(wanted[1] - placed[1]) > PLACE_TOLERANCE:
            raise Exception(u"Revit placed it off the pipe and would not "
                            u"move it back")
    return instance


def make(doc, row, symbol, z=None):
    """Make one opening, a floor or roof's sketched at height z. Returns
    the new element."""
    host = row.host
    opening = row.opening
    if host.kind == model.WALL:
        if opening.shape == model.CIRCULAR:
            return place_round(doc, row, symbol)
        corners = opening.corners()
        return doc.Create.NewOpening(host.element, point(corners[0]),
                                     point(corners[2]))
    return doc.Create.NewOpening(host.element, profile(opening, z), False)


def model_levels(doc):
    """[(elevation, level)] for every level, lowest first."""
    found = []
    try:
        for level in DB.FilteredElementCollector(doc).OfClass(DB.Level):
            try:
                found.append((level.Elevation, level))
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
    found.sort(key=lambda pair: pair[0])
    return found


# A shaft opening's offsets from its base and top levels. Shafts carry
# the same base and top parameters as walls. Read from the API
# documentation only; the write is read back, so a wrong guess fails
# that opening rather than cutting a shaft from level to level.
SHAFT_OFFSETS = ("WALL_BASE_OFFSET", "WALL_TOP_OFFSET")


def set_length(element, name, value):
    """Set a length parameter by BuiltInParameter name and read it
    back. Raises with the reason when either fails."""
    member = getattr(DB.BuiltInParameter, name, None)
    parameter = element.get_Parameter(member) if member is not None \
        else None
    if parameter is None or parameter.IsReadOnly:
        raise Exception(u"the shaft has no {0} that can be set".format(
            name))
    if not parameter.Set(value):
        raise Exception(u"the shaft would not take {0}".format(name))
    if abs(parameter.AsDouble() - value) > 1e-6:
        raise Exception(u"the shaft's {0} did not keep its value".format(
            name))


def make_shaft(doc, row, symbol=None, z=None):
    """A shaft opening spanning only the host's own thickness at the
    opening (`model.shaft_band`), for a floor or roof that refuses a
    vertical cut from a sketch at every height. Returns the shaft."""
    levels = model_levels(doc)
    bottom, top = model.shaft_band(row.entry, row.exit, row.opening,
                                   row.normals)
    chosen = model.shaft_levels([z for z, _ in levels], bottom, top)
    if chosen is None:
        raise Exception(u"the model has no levels to hang a shaft from")
    base_z, base_level = levels[chosen[0]]
    top_z, top_level = levels[chosen[1]]
    shaft = doc.Create.NewOpening(base_level, top_level,
                                  profile(row.opening, base_z))
    if shaft is None:
        raise Exception(u"Revit made no shaft")
    set_length(shaft, SHAFT_OFFSETS[0], bottom - base_z)
    set_length(shaft, SHAFT_OFFSETS[1], top - top_z)
    return shaft


def attempt(doc, row, symbol, z, maker=None):
    """One try at one opening in its own transaction. Returns the
    reason it failed, or u"" when Revit kept it."""
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    failures = collect_failures(transaction)
    try:
        element = (maker or make)(doc, row, symbol, z)
        if element is None:
            raise Exception(u"Revit made nothing")
    except BaseException as exc:
        transaction.RollBack()
        return to_text(exc) or u"Revit refused it"
    if not commit_checked(transaction):
        return u"; ".join(failures.errors) or u"Revit rolled it back"
    return u""


def make_all(doc, rows, symbol):
    """Each opening in its own transaction, all in one group."""
    group = DB.TransactionGroup(doc, TITLE)
    group.Start()
    made = 0
    try:
        for row in rows:
            if row.opening is None:
                continue
            heights = sketch_heights(doc, row)
            reasons = []
            for index, (label, z) in enumerate(heights):
                why = attempt(doc, row, symbol, z)
                if not why:
                    row.done = True
                    row.result = u"made"
                    if index > 0:
                        row.result = u"made, sketched at {0}".format(label)
                    made += 1
                    break
                if why not in reasons:
                    reasons.append(why)
            if not row.done and row.host.kind != model.WALL:
                # Revit 2025 refused a vertical cut into a sloped roof at
                # every sketch height ("Can't change plane of Opening
                # Cut Sketch"), so a shaft is the last resort.
                why = attempt(doc, row, symbol, None, maker=make_shaft)
                if not why:
                    row.done = True
                    row.result = (u"made as a shaft opening through the "
                                  u"{0} only; a vertical cut was refused "
                                  u"({1})".format(
                                      HOST_WORDS[row.host.kind].lower(),
                                      u"; ".join(reasons)))
                    made += 1
                elif why not in reasons:
                    reasons.append(u"shaft: " + why)
            if not row.done:
                # Say what was tried: a refusal at one height and at
                # four read the same otherwise, and so does a run of an
                # older version that tried only one.
                tried = u""
                if len(heights) > 1:
                    tried = u" (sketch tried at {0})".format(u", ".join(
                        label for label, _ in heights))
                row.result = u"not made{0}: {1}".format(
                    tried, u"; ".join(reasons))
    except BaseException:
        group.RollBack()
        raise
    if not made:
        group.RollBack()
        return 0
    status = group.Assimilate()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is not None and status != committed:
        for row in rows:
            if row.done:
                row.done = False
                row.result = u"not kept: Revit rolled the run back"
        return 0
    return made


def verify(rows):
    """Put every made opening's pipe through its host again. The axis
    should now run through the hole and meet nothing there."""
    solids = {}
    for row in rows:
        if not row.done:
            continue
        key = row.host.id
        if key not in solids:
            solids[key] = reader.solids_of(DB, row.host.element)
        middle = model.scale(model.add(row.entry, row.exit), 0.5)
        limit = max(row.pipe.diameter / 2.0, 1.0 / model.MM_PER_FOOT)
        for entry, exit in reader.axis_segments(
                DB, solids[key], row.pipe.start, row.pipe.end):
            other = model.scale(model.add(entry, exit), 0.5)
            if model.length(model.sub(other, middle)) <= limit:
                row.done = False
                row.result = u"made, but it does not cut where the pipe " \
                             u"is. Check it."
                break


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def link(element_id):
    try:
        return output.linkify(element_id)
    except BaseException:
        return to_text(reader.id_value(element_id))


def pipe_text(pipe):
    if pipe.source:
        return u"{0} {1} in {2}".format(pipe.kind, pipe.id, pipe.source)
    return u"{0} {1}".format(pipe.kind, link(pipe.element.Id))


def host_text(host):
    return u"{0} {1}".format(HOST_WORDS[host.kind], link(host.element.Id))


def table_cell(text):
    """Text that cannot break the table: a pipe character would start a
    new column, and a line break a new row."""
    return to_text(text).replace(u"|", u"/").replace(u"\n", u" ")


def print_rows(rows, settings, after):
    """The table, built whole and printed in one print_md.

    pyRevit renders each print_md call as a document of its own, so a
    table printed a line at a time comes out as a header table and then
    one separate table per row, each sizing its columns to its own
    contents, and nothing lines up. Seen in Revit at 2.47.0.
    """
    lines = [
        u"| Host | Pipe | Pipe \u00f8 mm | Opening mm | Off square | {0} |"
        .format(u"Result" if after else u"What happens"),
        # Every column left aligned. pyRevit marks a ---: column right
        # aligned on both header and cells, but its output window
        # right-aligns only the header, so the heading drifts away from
        # its values (seen in Revit 2025 at 2.47.1).
        u"| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for row in rows:
        size = row.opening.size_text() if row.opening else u"-"
        skew = u"-"
        if row.opening:
            skew = u"{0}\u00b0".format(int(round(row.opening.skew)))
        if after and row.result:
            note = row.result
        elif row.reason:
            note = u"skipped: " + row.reason
        else:
            note = u"will be cut"
        if row.warning:
            note = u"{0}; {1}".format(note, row.warning)
        lines.append(u"| {0} | {1} | {2} | {3} | {4} | {5} |".format(
            host_text(row.host), pipe_text(row.pipe),
            model.format_number(row.pipe.diameter * model.MM_PER_FOOT),
            size, skew, table_cell(note)))
    heading = u"{0}: result" if after else u"{0}: found"
    output.print_md(u"### {0}".format(heading.format(TITLE)))
    output.print_md(u"_{0}_".format(settings.describe()))
    output.print_md(u"\n".join(lines))


def hosts_line(hosts):
    counts = {}
    for host in hosts:
        counts[host.kind] = counts.get(host.kind, 0) + 1
    parts = []
    for kind, word in ((model.WALL, u"wall"), (model.FLOOR, u"floor"),
                       (model.ROOF, u"roof")):
        if counts.get(kind):
            parts.append(u"{0} {1}{2}".format(
                counts[kind], word, u"s" if counts[kind] > 1 else u""))
    return u"Openings will be cut in the selected {0} wherever a pipe or " \
           u"conduit passes through.".format(u", ".join(parts))


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if getattr(doc, "IsFamilyDocument", False):
        forms.alert(u"Open a project, not a family.", title=TITLE)
        return

    hosts, refused = selected_hosts(doc, uidoc)
    if not hosts:
        message = u"Select the walls, floors or roofs to open first, " \
                  u"then click again."
        if refused:
            message += u"\n\nOf what is selected:\n" + u"\n".join(
                u"- {0}: {1}".format(reader.id_value(element.Id), reason)
                for element, reason in refused)
        forms.alert(message, title=TITLE)
        return

    settings = ask_settings(remembered(), hosts_line(hosts))
    if settings is None:
        return
    remember(settings)

    pipes = reader.read_pipes(DB, doc)
    unloaded = []
    if settings.links:
        linked, unloaded = reader.link_pipes(DB, doc)
        pipes.extend(linked)

    wants_round = settings.shape == model.CIRCULAR and any(
        host.kind == model.WALL for host in hosts)
    round_ok = round_family_available(doc) if wants_round else True
    rows = plan(hosts, pipes, settings, round_ok)

    for element, reason in refused:
        output.print_md(u"- Not opened, {0} is {1}.".format(
            link(element.Id), reason))
    for name in unloaded:
        output.print_md(u"- Link **{0}** is not loaded, so its pipes "
                        u"were not looked at.".format(name))

    if not rows:
        forms.alert(
            u"No pipe or conduit passes through the selected {0}.\n\n"
            u"{1} pipes and conduits were checked{2}. One that already "
            u"has an opening is not counted, because it runs through the "
            u"hole.".format(
                u"element" if len(hosts) == 1 else u"elements",
                len(pipes),
                u", linked models included" if settings.links
                else u", this model only"),
            title=TITLE)
        return

    print_rows(rows, settings, after=False)
    todo = [row for row in rows if row.opening is not None]
    skipped = len(rows) - len(todo)
    if not todo:
        forms.alert(u"{0} crossing(s) found, and none can be opened. "
                    u"The reasons are in the output window.".format(
                        len(rows)), title=TITLE)
        return

    message = u"Cut {0} opening(s)?\n\n{1}".format(
        len(todo), settings.describe())
    if skipped:
        message += u"\n\n{0} crossing(s) will be skipped; the output " \
                   u"window says why.".format(skipped)
    if wants_round and round_ok:
        message += u"\n\nRound wall openings use the {0} family.".format(
            FAMILY_NAME)
    # A tool that writes stops if the question cannot be asked: nobody
    # confirmed anything.
    if not forms.alert(message, title=TITLE, yes=True, no=True):
        return

    symbol = find_symbol(doc) if wants_round and round_ok else None
    made = make_all(doc, rows, symbol)
    try:
        verify(rows)
    except BaseException as exc:
        logger.debug(to_text(exc))

    print_rows(rows, settings, after=True)
    kept = len([row for row in rows if row.done])
    failed = len(todo) - kept
    summary = u"{0} opening(s) cut.".format(kept)
    if failed:
        summary += u" {0} could not be, or need checking; see the " \
                   u"output window.".format(failed)
    if made:
        summary += u"\n\nOne undo takes the whole run back."
    forms.alert(summary, title=TITLE)


if __name__ == "__main__":
    run()
