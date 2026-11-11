# -*- coding: utf-8 -*-
"""Check every stair in the model against Byggingarreglugerd 112/2012.

Reads each stair, its flights, landings, type and railings, measures
the headroom over every tread, and reports per stair what passes, what
fails, and what the model cannot answer, with the article for each.
The rules themselves live in `avh_stairs.rules` and are tested without
Revit.

## The stair's use decides the limits

A stair inside a dwelling, one serving several companies and outdoor
steps have different limits, and Revit knows none of that. Each stair
carries its use in the text parameter AVH_Stair_Use. If the model has no
such parameter the tool offers to add it, and if stairs have no value it
offers to set them from a list before checking. Stairs left without one
get only the rules that hold for every stair.

## Headroom

Rays are cast straight up from just above the middle of every tread, at
the centre of the flight and near both sides, in a 3D view made for the
purpose and thrown away afterwards. Linked models are included, since a
structural slab edge is the usual culprit. The stair's own railings are
left out; its own upper flights are not, since those are exactly what a
low headroom is usually made of.

## Unverified

Not yet run in Revit. Every API member below is read defensively and a
value that cannot be read is reported as "not checked", never as a
pass: `Stairs.ActualRiserHeight`, `ActualTreadDepth`, `GetStairsRuns`,
`GetStairsLandings`, `GetAssociatedRailings`; `StairsRun.ActualRunWidth`,
`StairsRunStyle`, `GetStairsPath`, `ActualTreadsNumber`;
`StairsLanding.GetFootprintBoundary`; `StairsRunType.HasRisers`,
`NosingLength`, `TreadThickness`; `RailingType.TopRailHeight`,
`PrimaryHandrailHeight`, `SecondaryHandrailHeight`; `ReferenceIntersector`
with `FindReferencesInRevitLinks`; and the shared parameter binding.
"""

__title__ = "Stair\nCheck"
__author__ = "AVH"
__doc__ = ("Check every stair in the model against Byggingarreglugerd "
           "112/2012: riser, going, slope, landings, width, headroom and "
           "railings, with the article for each result.")

import os
import sys
import tempfile

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
from avh_stairs import rules                      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Stair Check"
METRES_PER_FOOT = 0.3048

# A fixed GUID, so the parameter is the same shared parameter in every
# model it is added to.
PARAMETER_GUID = "f03e1b0f-96ae-4317-ba74-010f759f77eb"

# Rays start this far above the tread, and this far in from each side.
RAY_LIFT_M = 0.05
RAY_EDGE_M = 0.15

STATUS_LABEL = {
    rules.FAIL: u"**FAIL**",
    rules.CHECK: u"CHECK",
    rules.PASS: u"pass",
    rules.NOT_CHECKED: u"not checked",
}


def metres(feet):
    if feet is None:
        return None
    try:
        return float(feet) * METRES_PER_FOOT
    except BaseException:
        return None


def feet(value_m):
    return value_m / METRES_PER_FOOT


def read(obj, name, default=None):
    """A property's value, or default when it is missing or raises.

    Also takes a member that turns out to be a method, and calls it.
    Stairs.IsInEditMode is one: read as a property it is a bound method,
    which is truthy, so every stair looked as if it were being edited and
    the first live run (2026-11-11) found "no stairs" in a model full of
    them. Guessing property or method per member is how that happened,
    so this no longer depends on the guess.
    """
    try:
        value = getattr(obj, name)
    except BaseException as exc:
        logger.debug(u"{0}: {1}".format(name, to_text(exc)))
        return default
    if callable(value) and not isinstance(value, type):
        try:
            value = value()
        except BaseException as exc:
            logger.debug(u"{0}(): {1}".format(name, to_text(exc)))
            return default
    return value


def call(obj, name, *args):
    try:
        return getattr(obj, name)(*args)
    except BaseException as exc:
        logger.debug(u"{0}(): {1}".format(name, to_text(exc)))
        return None


def id_value(element_id):
    value = read(element_id, "Value")
    if value is None:
        value = read(element_id, "IntegerValue")
    return value


def valid(element_id):
    try:
        return element_id is not None and \
            element_id != DB.ElementId.InvalidElementId
    except BaseException:
        return False


def ids(collection):
    if collection is None:
        return []
    try:
        return list(collection)
    except BaseException:
        return []


# --------------------------------------------------------------------------
# The parameter
# --------------------------------------------------------------------------

def use_parameter(stair):
    try:
        return stair.LookupParameter(rules.PARAMETER)
    except BaseException:
        return None


def use_value(stair):
    parameter = use_parameter(stair)
    if parameter is None:
        return None
    try:
        return parameter.AsString()
    except BaseException:
        return None


def add_parameter(doc):
    """Bind AVH_Stair_Use to Stairs as a shared instance parameter.

    Revit only creates shared parameters through a shared parameter
    file, so a temporary one is used and the user's own file name is put
    back afterwards, whatever happens.
    """
    app = doc.Application
    original = None
    path = os.path.join(tempfile.gettempdir(), "AVH_Stair_Use_params.txt")
    try:
        original = app.SharedParametersFilename
    except BaseException:
        pass
    transaction = DB.Transaction(doc, TITLE + u": add " + rules.PARAMETER)
    try:
        handle = open(path, "w")
        handle.close()
        app.SharedParametersFilename = path
        definition_file = app.OpenSharedParameterFile()
        group = definition_file.Groups.Create("AVH")
        options = DB.ExternalDefinitionCreationOptions(
            rules.PARAMETER, DB.SpecTypeId.String.Text)
        from System import Guid
        options.GUID = Guid(PARAMETER_GUID)
        definition = group.Definitions.Create(options)
        categories = app.Create.NewCategorySet()
        categories.Insert(DB.Category.GetCategory(
            doc, DB.BuiltInCategory.OST_Stairs))
        binding = app.Create.NewInstanceBinding(categories)
        transaction.Start()
        inserted = doc.ParameterBindings.Insert(
            definition, binding, DB.GroupTypeId.IdentityData)
        if not inserted:
            transaction.RollBack()
            return u"Revit would not bind the parameter."
        status = transaction.Commit()
        if status != DB.TransactionStatus.Committed:
            return u"Revit rejected the parameter ({0}).".format(
                to_text(status))
        return u""
    except BaseException as exc:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except BaseException:
            pass
        return u"The parameter could not be added: {0}".format(to_text(exc))
    finally:
        try:
            if original is not None:
                app.SharedParametersFilename = original
        except BaseException:
            pass


def level_of(doc, stair):
    """The stair's base level name, or u"" when it cannot be read."""
    try:
        level = doc.GetElement(stair.get_Parameter(
            DB.BuiltInParameter.STAIRS_BASE_LEVEL_PARAM).AsElementId())
        return to_text(level.Name) if level is not None else u""
    except BaseException:
        return u""


def stair_label(doc, stair):
    base = metres(read(stair, "BaseElevation"))
    top = metres(read(stair, "TopElevation"))
    risers = read(stair, "ActualRisersNumber")
    level_name = level_of(doc, stair)
    parts = []
    if level_name:
        parts.append(u"from {0}".format(level_name))
    if base is not None and top is not None:
        parts.append(u"{0:.2f} to {1:.2f} m".format(base, top))
    if risers is not None:
        parts.append(u"{0} risers".format(risers))
    parts.append(u"id {0}".format(id_value(stair.Id)))
    return u", ".join(parts)


def classify(doc, stairs):
    """Offer to set AVH_Stair_Use on stairs that have no value.

    One round: pick stairs, pick a use, write it; then again for those
    left, until none are left or the user stops.
    """
    while True:
        blank = [s for s in stairs if use_parameter(s) is not None and
                 not to_text(use_value(s)).strip()]
        if not blank:
            return
        labels = {}
        for stair in blank:
            labels[stair_label(doc, stair)] = stair
        picked = forms.SelectFromList.show(
            sorted(labels.keys()), multiselect=True,
            title=TITLE + u": {0} stair(s) have no use. Tick some, or "
                  u"cancel to check without".format(len(blank)),
            button_name=u"Set use for ticked")
        if not picked:
            return
        if not isinstance(picked, list):
            picked = [picked]
        chosen = [labels[to_text(p)] for p in picked if to_text(p) in labels]
        if not chosen:
            return
        options = [u"{0}  -  {1}".format(use.code, use.label)
                   for use in rules.USES]
        answer = forms.SelectFromList.show(
            options, multiselect=False,
            title=TITLE + u": use of the {0} ticked stair(s)".format(
                len(chosen)),
            button_name=u"Set")
        if not answer:
            return
        if isinstance(answer, list):
            answer = answer[0] if answer else None
        code = to_text(answer).split(u"  -  ")[0].strip()
        if code not in rules.BY_CODE:
            return
        transaction = DB.Transaction(doc, TITLE + u": set stair use")
        transaction.Start()
        refused = 0
        try:
            for stair in chosen:
                parameter = use_parameter(stair)
                if parameter is None or parameter.IsReadOnly or \
                        not parameter.Set(code):
                    refused += 1
        except BaseException as exc:
            transaction.RollBack()
            forms.alert(u"The use could not be set: {0}".format(
                to_text(exc)), title=TITLE)
            return
        status = transaction.Commit()
        if status != DB.TransactionStatus.Committed:
            forms.alert(u"Revit rejected the change ({0}).".format(
                to_text(status)), title=TITLE)
            return
        if refused:
            forms.alert(u"{0} stair(s) would not take the value.".format(
                refused), title=TITLE)
            return


# --------------------------------------------------------------------------
# Reading a stair
# --------------------------------------------------------------------------

def curve_points(curves):
    """Plan points along a set of curves, arcs included."""
    points = []
    for curve in curves:
        try:
            for point in curve.Tessellate():
                points.append((point.X, point.Y))
        except BaseException:
            try:
                start = curve.GetEndPoint(0)
                points.append((start.X, start.Y))
            except BaseException:
                pass
    return points


def path_curves(element):
    loop = call(element, "GetStairsPath")
    return ids(loop)


def run_direction(run):
    """Plan direction of travel of a flight, from its path, or None."""
    curves = path_curves(run)
    if not curves:
        return None
    try:
        start = curves[0].GetEndPoint(0)
        end = curves[-1].GetEndPoint(1)
    except BaseException:
        return None
    dx, dy = end.X - start.X, end.Y - start.Y
    if abs(dx) + abs(dy) < 1e-9:
        return None
    return (dx, dy)


STYLE_STRAIGHT = ("Straight",)


def run_style(run):
    style = read(run, "StairsRunStyle")
    if style is None:
        return u"straight"
    name = to_text(style)
    if name.split(".")[-1] in STYLE_STRAIGHT:
        return u"straight"
    return name.split(".")[-1].lower()


def read_runs(doc, stair):
    runs = []
    elements = []
    for run_id in ids(call(stair, "GetStairsRuns")):
        run = doc.GetElement(run_id)
        if run is None:
            continue
        elements.append(run)
        base = read(run, "BaseElevation")
        top = read(run, "TopElevation")
        rise = None
        if base is not None and top is not None:
            rise = metres(top - base)
        runs.append(rules.Run(id_value(run_id),
                              width=metres(read(run, "ActualRunWidth")),
                              rise=rise, style=run_style(run)))
    return runs, elements


def read_landings(doc, stair, run_elements):
    landings = []
    for landing_id in ids(call(stair, "GetStairsLandings")):
        landing = doc.GetElement(landing_id)
        if landing is None:
            continue
        points = curve_points(ids(call(landing, "GetFootprintBoundary")))
        if len(points) < 3:
            landings.append(rules.Landing(id_value(landing_id)))
            continue
        direction = None
        base = read(landing, "BaseElevation")
        for run in run_elements:
            top = read(run, "TopElevation")
            if base is not None and top is not None and \
                    abs(top - base) < feet(0.02):
                direction = run_direction(run)
                break
        if direction is not None:
            length = rules.extent_along(points, direction)
            landings.append(rules.Landing(id_value(landing_id),
                                          length=metres(length)))
        else:
            landings.append(rules.Landing(
                id_value(landing_id),
                length=metres(rules.smallest_width(points)),
                measured_along=False))
    return landings


def read_type(doc, stair):
    """(has_risers, nosing m, tread thickness m), None where unreadable."""
    try:
        stair_type = doc.GetElement(stair.GetTypeId())
        run_type = doc.GetElement(stair_type.RunType)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None, None, None
    if run_type is None:
        return None, None, None
    has_risers = read(run_type, "HasRisers")
    has_treads = read(run_type, "HasTreads")
    nosing = metres(read(run_type, "NosingLength"))
    if has_treads is False:
        nosing = 0.0
    return has_risers, nosing, metres(read(run_type, "TreadThickness"))


def read_railings(doc, stair):
    railings = []
    parts = []
    for railing_id in ids(call(stair, "GetAssociatedRailings")):
        railing = doc.GetElement(railing_id)
        if railing is None:
            continue
        parts.append(railing_id)
        for extra in ids(call(railing, "GetHandRails")):
            parts.append(extra)
        top_rail = read(railing, "TopRail")
        if top_rail is not None and valid(top_rail):
            parts.append(top_rail)
        railing_type = doc.GetElement(railing.GetTypeId())
        top = None
        grips = []
        if railing_type is not None:
            if valid(read(railing_type, "TopRailType")):
                top = metres(read(railing_type, "TopRailHeight"))
            for kind, height in (("PrimaryHandrailType",
                                  "PrimaryHandrailHeight"),
                                 ("SecondaryHandrailType",
                                  "SecondaryHandrailHeight")):
                if valid(read(railing_type, kind)):
                    value = metres(read(railing_type, height))
                    if value is not None:
                        grips.append(value)
        railings.append(rules.Railing(id_value(railing_id), top=top,
                                      handrails=grips))
    return railings, parts


def plan_of(stair):
    try:
        box = stair.get_BoundingBox(None)
    except BaseException:
        box = None
    if box is None:
        return None
    return (metres(box.Min.X), metres(box.Min.Y),
            metres(box.Max.X), metres(box.Max.Y))


class Reading(object):
    def __init__(self, element, data, run_elements, railing_parts,
                 level=u""):
        self.element = element
        self.data = data
        self.run_elements = run_elements
        self.railing_parts = railing_parts
        self.level = level


def read_stair(doc, stair):
    runs, run_elements = read_runs(doc, stair)
    has_risers, nosing, thickness = read_type(doc, stair)
    railings, parts = read_railings(doc, stair)
    data = rules.Stair(
        id_value(stair.Id), name=to_text(read(stair, "Name", u"")),
        use_value=use_value(stair),
        riser=metres(read(stair, "ActualRiserHeight")),
        going=metres(read(stair, "ActualTreadDepth")),
        base=metres(read(stair, "BaseElevation")),
        top=metres(read(stair, "TopElevation")),
        runs=runs, landings=read_landings(doc, stair, run_elements),
        has_risers=has_risers, nosing=nosing, tread_thickness=thickness,
        railings=railings, plan=plan_of(stair))
    return Reading(stair, data, run_elements, parts, level_of(doc, stair))


# --------------------------------------------------------------------------
# Headroom
# --------------------------------------------------------------------------

def make_view(doc):
    for view_type in DB.FilteredElementCollector(doc).OfClass(
            DB.ViewFamilyType):
        try:
            if view_type.ViewFamily == DB.ViewFamily.ThreeDimensional:
                return DB.View3D.CreateIsometric(doc, view_type.Id)
        except BaseException:
            continue
    return None


def tread_points(run, riser_ft):
    """(x, y, z, tangent) at the middle of each tread, from the path."""
    curves = path_curves(run)
    treads = read(run, "ActualTreadsNumber")
    base = read(run, "BaseElevation")
    if not curves or not treads or base is None or riser_ft is None:
        return []
    lengths = []
    for curve in curves:
        try:
            lengths.append(curve.Length)
        except BaseException:
            return []
    total = sum(lengths)
    if total <= 0:
        return []
    found = []
    step = total / treads
    for k in range(treads):
        target = (k + 0.5) * step
        for curve, length in zip(curves, lengths):
            if target <= length or curve is curves[-1]:
                try:
                    fraction = min(1.0, max(0.0, target / length))
                    frame = curve.ComputeDerivatives(fraction, True)
                    origin = frame.Origin
                    tangent = frame.BasisX
                except BaseException:
                    break
                z = base + (k + 1) * riser_ft
                found.append((origin.X, origin.Y, z, tangent))
                break
            target -= length
    return found


def measure_headroom(doc, readings):
    """Fill in `headroom` on each stair's data. Nothing is kept.

    The 3D view the rays need is made inside a transaction that is
    rolled back, so the model is left exactly as it was.
    """
    transaction = DB.Transaction(doc, TITLE + u": headroom probe")
    transaction.Start()
    try:
        view = make_view(doc)
        if view is None:
            return u"No 3D view type was found, so headroom was not measured."
        doc.Regenerate()
        from System.Collections.Generic import List
        for reading in readings:
            parts = List[DB.ElementId]()
            for part in reading.railing_parts:
                parts.Add(part)
            if parts.Count:
                element_filter = DB.ExclusionFilter(parts)
            else:
                element_filter = DB.ElementIsElementTypeFilter(True)
            intersector = DB.ReferenceIntersector(
                element_filter, DB.FindReferenceTarget.Element, view)
            intersector.FindReferencesInRevitLinks = True
            riser_ft = read(reading.element, "ActualRiserHeight")
            least = None
            for run, run_data in zip(reading.run_elements,
                                     reading.data.runs):
                width = feet(run_data.width) if run_data.width else 0.0
                side = max(0.0, width / 2.0 - feet(RAY_EDGE_M))
                for x, y, z, tangent in tread_points(run, riser_ft):
                    nx, ny = -tangent.Y, tangent.X
                    size = (nx * nx + ny * ny) ** 0.5 or 1.0
                    nx, ny = nx / size, ny / size
                    for offset in (0.0, side, -side):
                        origin = DB.XYZ(x + nx * offset, y + ny * offset,
                                        z + feet(RAY_LIFT_M))
                        hit = intersector.FindNearest(origin, DB.XYZ.BasisZ)
                        if hit is None:
                            continue
                        clearance = metres(hit.Proximity) + RAY_LIFT_M
                        if least is None or clearance < least:
                            least = clearance
            if least is None:
                # Nothing above any tread: open to the sky, or the path
                # could not be read. Only the first is a pass.
                if any(tread_points(run, riser_ft)
                       for run in reading.run_elements):
                    least = rules.OPEN
            reading.data.headroom = least
        return u""
    except BaseException as exc:
        logger.debug(to_text(exc))
        return u"Headroom could not be measured: {0}".format(to_text(exc))
    finally:
        try:
            transaction.RollBack()
        except BaseException:
            pass


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

# Every check's results, for the harness to read back. The report shows
# only the problems, so the passes are not in the output window.
RESULTS = []

ORDER = {rules.FAIL: 0, rules.CHECK: 1, rules.NOT_CHECKED: 2, rules.PASS: 3}

SUMMARY_LABEL = {
    rules.FAIL: u"FAIL",
    rules.CHECK: u"check",
    rules.NOT_CHECKED: u"incomplete",
    rules.PASS: u"pass",
}


LINE_LABEL = {
    rules.FAIL: u"FAIL",
    rules.CHECK: u"check",
    rules.NOT_CHECKED: u"not checked",
    rules.PASS: u"pass",
}


def cell(text):
    """Text safe inside a markdown table cell."""
    return to_text(text).replace(u"|", u"/").replace(u"\n", u" ")


def problems_of(results):
    return [r for r in results if r.status != rules.PASS]


def report(entries, note):
    """A summary table of every stair, then only what needs attention.

    Each table is printed in one call. pyRevit draws every print_md call
    as its own block, so a table printed a row at a time came out as a
    stack of one row tables, each with its own column widths, which is
    what made the first report unreadable.
    """
    counts = {}
    for _reading, _use, results in entries:
        status = rules.worst(results)
        counts[status] = counts.get(status, 0) + 1
    head = [
        u"### {0}".format(TITLE),
        u"Byggingarregluger\u00f0 112/2012, \u00fatg\u00e1fa \u00ed gildi "
        u"fr\u00e1 1. ma\u00ed 2026, 6.4.6 to 6.4.10 and 6.5.1 to 6.5.5.",
        u"",
        u"**{0} stair(s): {1} fail, {2} to check, {3} incomplete, {4} "
        u"pass.**".format(len(entries), counts.get(rules.FAIL, 0),
                          counts.get(rules.CHECK, 0),
                          counts.get(rules.NOT_CHECKED, 0),
                          counts.get(rules.PASS, 0)),
    ]
    if note:
        head += [u"", u"_{0}_".format(note)]
    output.print_md(u"\n".join(head))

    entries = sorted(entries, key=lambda e: (ORDER[rules.worst(e[2])],
                                             e[0].level, e[0].data.element_id))
    table = [u"| Stair | Level | Use | Result | Needs attention |",
             u"| --- | --- | --- | --- | --- |"]
    for reading, use, results in entries:
        names = []
        for result in problems_of(results):
            if result.rule not in names:
                names.append(result.rule)
        table.append(u"| {0} | {1} | {2} | {3} | {4} |".format(
            output.linkify(reading.element.Id), cell(reading.level),
            cell(use.code if use is not None else u"(none)"),
            SUMMARY_LABEL[rules.worst(results)],
            cell(u", ".join(names) or u"")))
    output.print_md(u"\n".join(table))

    for reading, use, results in entries:
        problems = problems_of(results)
        if not problems:
            continue
        problems.sort(key=lambda r: ORDER[r.status])
        lines = [u"#### {0} {1}, {2}".format(
            output.linkify(reading.element.Id), cell(reading.level),
            use.label if use is not None else u"no use set")]
        for result in problems:
            lines.append(u"- **{0}** {1}{2}: {3}".format(
                LINE_LABEL[result.status],
                result.article + u" " if result.article else u"",
                result.rule, result.message))
        passed = len(results) - len(problems)
        if passed:
            lines.append(u"- {0} other rule(s) pass".format(passed))
        output.print_md(u"\n".join(lines))

    output.print_md(
        u"_Not read from the model, so not checked: doors at landings "
        u"(6.4.7), handrail extensions of 0.30 m past the end steps "
        u"(6.5.2), nosing edges and materials, gaps beside the stair "
        u"(6.4.6), guard openings (6.5.3), and markings for the visually "
        u"impaired. Width is Revit's run width; the regulation measures "
        u"wall to handrail, which is narrower._")


def count_loose_railings(doc, readings):
    """Count railings drawn separately along each stair.

    A railing that is not hosted on any stair, whose box overlaps the
    stair's in plan (with 0.3 m to spare) and in height.
    """
    railing_class = getattr(DB.Architecture, "Railing", None)
    boxes = []
    try:
        railings = list(DB.FilteredElementCollector(doc).OfClass(
            railing_class).WhereElementIsNotElementType())
    except BaseException as exc:
        logger.debug(to_text(exc))
        return
    for railing in railings:
        # One unreadable railing must not cost the count for the rest.
        try:
            if valid(read(railing, "HostId")):
                continue
            box = railing.get_BoundingBox(None)
        except BaseException as exc:
            logger.debug(to_text(exc))
            continue
        if box is not None:
            boxes.append((metres(box.Min.X), metres(box.Min.Y),
                          metres(box.Min.Z), metres(box.Max.X),
                          metres(box.Max.Y), metres(box.Max.Z)))
    spare = 0.3
    for reading in readings:
        plan = reading.data.plan
        base, top = reading.data.base, reading.data.top
        if plan is None or base is None or top is None:
            continue
        reading.data.loose_railings = len([
            b for b in boxes
            if b[0] < plan[2] + spare and plan[0] - spare < b[3] and
            b[1] < plan[3] + spare and plan[1] - spare < b[4] and
            b[2] < top and base < b[5]])


def find_stairs(doc):
    """(stairs to check, how many are in edit mode, error text)."""
    stairs_class = getattr(DB.Architecture, "Stairs", None)
    found = []
    editing = 0
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(stairs_class)
        for stair in collector.WhereElementIsNotElementType():
            if read(stair, "IsInEditMode", False) is True:
                editing += 1
            else:
                found.append(stair)
    except BaseException as exc:
        return found, editing, to_text(exc)
    return found, editing, u""


def no_stairs_message(doc, editing, problem):
    """Say what there is instead, so "no stairs" is never a dead end."""
    lines = [u"No Revit stairs were found to check."]
    if problem:
        lines.append(u"Revit refused the search: {0}".format(problem))
    if editing:
        lines.append(u"{0} stair(s) are being edited. Finish the edit and "
                     u"run again.".format(editing))
    others = {}
    try:
        collector = DB.FilteredElementCollector(doc).OfCategory(
            DB.BuiltInCategory.OST_Stairs).WhereElementIsNotElementType()
        for element in collector:
            kind = to_text(read(element.GetType(), "Name", u"?"))
            if kind != u"Stairs":
                others[kind] = others.get(kind, 0) + 1
    except BaseException as exc:
        logger.debug(to_text(exc))
    if others:
        lines.append(u"The Stairs category holds {0}, which are not Revit "
                     u"stairs (stair families or older sketched stairs) "
                     u"and cannot be read this way.".format(u", ".join(
                         u"{0} {1}".format(count, kind)
                         for kind, count in sorted(others.items()))))
    linked = []
    try:
        for link in DB.FilteredElementCollector(doc).OfClass(
                DB.RevitLinkInstance):
            link_doc = link.GetLinkDocument()
            if link_doc is None:
                continue
            count = len(list(DB.FilteredElementCollector(link_doc).OfClass(
                getattr(DB.Architecture, "Stairs", None))
                .WhereElementIsNotElementType()))
            if count:
                linked.append(u"{0} in {1}".format(count, to_text(
                    read(link_doc, "Title", u"a link"))))
    except BaseException as exc:
        logger.debug(to_text(exc))
    if linked:
        lines.append(u"Linked models have stairs: {0}. This tool checks "
                     u"the open model only; open that model to check "
                     u"them.".format(u", ".join(linked)))
    return u"\n\n".join(lines)


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    stairs, editing, problem = find_stairs(doc)
    if not stairs:
        forms.alert(no_stairs_message(doc, editing, problem), title=TITLE)
        return

    if all(use_parameter(s) is None for s in stairs):
        if forms.alert(
                u"The stairs have no {0} parameter, so the tool cannot tell "
                u"a stair in a dwelling from outdoor steps, and only the "
                u"rules for every stair can be checked.\n\nAdd the "
                u"parameter to Stairs now?".format(rules.PARAMETER),
                title=TITLE, yes=True, no=True):
            problem = add_parameter(doc)
            if problem:
                forms.alert(problem, title=TITLE)
    classify(doc, stairs)

    readings = [read_stair(doc, stair) for stair in stairs]
    count_loose_railings(doc, readings)
    note = measure_headroom(doc, readings)
    wells = rules.stairwells([r.data for r in readings])
    entries = []
    for reading in readings:
        use, results = rules.check(reading.data, wells)
        entries.append((reading, use, results))
    del RESULTS[:]
    RESULTS.extend(entries)
    report(entries, note)


if __name__ == "__main__":
    run()
