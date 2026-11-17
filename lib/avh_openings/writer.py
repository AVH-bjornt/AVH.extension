# -*- coding: utf-8 -*-
"""Making openings in Revit, shared by Pipe Openings and Update Openings.

`DB` (Autodesk.Revit.DB) is passed in rather than imported, so the
harness can hand over a stand in.

A job is anything with `host` (a reader.Host), `opening` (a
model.Opening), `entry`, `exit` and `normals`. `Writer.make_one` tries
each way of making its opening, every try in a transaction of its own,
and keeps the first Revit accepts:

- a wall: `NewOpening(wall, corner, corner)`, or the AVH_Round_Opening
  family for a circle;
- a floor or roof: `NewOpening(host, profile, False)`, a vertical cut,
  its sketch tried at each height in `sketch_heights`, and failing all
  of those a shaft opening kept to the host's own thickness.

Revit 2025 refused a vertical cut into a sloped roof at every sketch
height ("Can't change plane of Opening Cut Sketch"); the shaft made it.
See NOTES.md, Pipe Openings.

`before` runs first inside each try (Update Openings deletes the old
opening there, so a failed try leaves it in place), and `after` gets
the new element before the try commits (both buttons tag it there).
"""

import math

from avh_openings import model, reader
from avh_openings.tags import OPENING, SHAFT, ROUND
from avh_schedules.compat import to_text

FAMILY_NAME = u"AVH_Round_Opening"
PARAM_DIAMETER = u"AVH_Diameter"

# Where a placed round opening may sit off the point asked for, in feet,
# before it is moved back. Half a millimetre.
PLACE_TOLERANCE = 0.5 / model.MM_PER_FOOT

HOST_WORDS = {model.WALL: u"Wall", model.FLOOR: u"Floor",
              model.ROOF: u"Roof"}

# The offset of a floor or roof above its level, by class of host.
LEVEL_OFFSETS = {model.FLOOR: "FLOOR_HEIGHTABOVELEVEL_PARAM",
                 model.ROOF: "ROOF_LEVEL_OFFSET_PARAM"}

# A shaft opening's offsets from its base and top levels. Shafts carry
# the same base and top parameters as walls. Read from the API
# documentation; the write is read back, so a wrong guess fails that
# opening rather than cutting a shaft from level to level. Confirmed
# working on a sloped roof in Revit 2025 at 2.47.4.
SHAFT_OFFSETS = ("WALL_BASE_OFFSET", "WALL_TOP_OFFSET")


class Failures(object):
    def __init__(self):
        self.errors = []


class Writer(object):
    def __init__(self, DB, doc, title, symbol=None, logger=None):
        self.DB = DB
        self.doc = doc
        self.title = title
        self.symbol = symbol          # AVH_Round_Opening, or None
        self.logger = logger

    def debug(self, exc):
        if self.logger is not None:
            try:
                self.logger.debug(to_text(exc))
            except BaseException:
                pass

    # -- transactions ---------------------------------------------------

    def collect_failures(self, transaction):
        """Swallow warnings, record errors and roll back on one.

        The class is built in here and inherits the interface alone:
        mixing it with a plain base class breaks the method resolution
        order and the whole thing silently falls into the except
        branch."""
        DB = self.DB
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
            self.debug(exc)
        return record

    def commit_checked(self, transaction):
        status = transaction.Commit()
        committed = getattr(self.DB.TransactionStatus, "Committed", None)
        return True if committed is None else status == committed

    def attempt(self, work):
        """Run `work` in a transaction of its own. Returns
        (element, u"") when Revit kept it, (None, reason) when not."""
        transaction = self.DB.Transaction(self.doc, self.title)
        transaction.Start()
        failures = self.collect_failures(transaction)
        try:
            element = work()
            if element is None:
                raise Exception(u"Revit made nothing")
        except BaseException as exc:
            transaction.RollBack()
            return None, to_text(exc) or u"Revit refused it"
        if not self.commit_checked(transaction):
            return None, (u"; ".join(failures.errors) or
                          u"Revit rolled it back")
        return element, u""

    # -- geometry --------------------------------------------------------

    def point(self, values):
        return self.DB.XYZ(values[0], values[1], values[2])

    def profile(self, opening, z=None):
        """A CurveArray for a floor or roof opening, drawn at height z
        (the opening's own centre when None)."""
        DB = self.DB

        def at(values):
            return (values[0], values[1], values[2] if z is None else z)

        curves = DB.CurveArray()
        if opening.shape == model.CIRCULAR:
            center = self.point(at(opening.center))
            radius = opening.diameter / 2.0
            curves.Append(DB.Arc.Create(center, radius, 0.0, math.pi,
                                        DB.XYZ.BasisX, DB.XYZ.BasisY))
            curves.Append(DB.Arc.Create(center, radius, math.pi,
                                        2.0 * math.pi, DB.XYZ.BasisX,
                                        DB.XYZ.BasisY))
            return curves
        corners = [at(corner) for corner in opening.corners()]
        for index in range(4):
            curves.Append(DB.Line.CreateBound(
                self.point(corners[index]),
                self.point(corners[(index + 1) % 4])))
        return curves

    def sketch_heights(self, job):
        """[(label, z)] heights to draw a floor or roof opening's sketch
        at, in the order they are tried.

        The first Revit run (2.47.1, Revit 2025) refused a roof opening
        drawn at the middle of the roof with "Can't change plane of
        Opening Cut Sketch", and the second refused every height here,
        so the plane is not what that roof objected to. They are still
        tried, being the native opening, before the shaft."""
        DB = self.DB
        host = job.host
        found = []
        try:
            elevation = self.doc.GetElement(host.element.LevelId).Elevation
        except BaseException as exc:
            self.debug(exc)
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
                    self.debug(exc)
            if offset:
                found.append((u"its level and offset", elevation + offset))
            found.append((u"its level", elevation))
        found.append((u"the middle of the host", job.opening.center[2]))
        found.append((u"the project's zero", 0.0))
        unique = []
        for label, z in found:
            if all(abs(z - other) > 1e-4 for _, other in unique):
                unique.append((label, z))
        return unique

    def levels(self):
        """[(elevation, level)] for every level, lowest first."""
        found = []
        try:
            for level in self.DB.FilteredElementCollector(self.doc).OfClass(
                    self.DB.Level):
                try:
                    found.append((level.Elevation, level))
                except BaseException:
                    continue
        except BaseException as exc:
            self.debug(exc)
        found.sort(key=lambda pair: pair[0])
        return found

    # -- the ways of making one -----------------------------------------

    def make_wall(self, job):
        corners = job.opening.corners()
        return self.doc.Create.NewOpening(job.host.element,
                                          self.point(corners[0]),
                                          self.point(corners[2]))

    def make_vertical(self, job, z):
        return self.doc.Create.NewOpening(
            job.host.element, self.profile(job.opening, z), False)

    def place_round(self, job):
        """The round wall opening family, placed and sized. Returns the
        instance; raises with a reason when it cannot be done right."""
        DB = self.DB
        doc = self.doc
        symbol = self.symbol
        if symbol is None:
            raise Exception(u"the {0} family is not loaded".format(
                FAMILY_NAME))
        wall = job.host.element
        level = doc.GetElement(wall.LevelId)
        if not symbol.IsActive:
            symbol.Activate()
        instance = doc.Create.NewFamilyInstance(
            self.point(job.opening.center), symbol, wall, level,
            DB.Structure.StructuralType.NonStructural)
        parameter = instance.LookupParameter(PARAM_DIAMETER)
        if parameter is None or parameter.IsReadOnly:
            raise Exception(u"{0} has no instance parameter {1} that can "
                            u"be set".format(FAMILY_NAME, PARAM_DIAMETER))
        if not parameter.Set(job.opening.diameter):
            raise Exception(u"{0} would not take the value".format(
                PARAM_DIAMETER))
        # A new instance reports where it is only after a regenerate.
        doc.Regenerate()

        frame = job.opening.frame
        wanted = frame.local(job.opening.center)
        placed = frame.local(reader.xyz(instance.Location.Point))
        du = wanted[0] - placed[0]
        dv = wanted[1] - placed[1]
        if abs(du) > PLACE_TOLERANCE or abs(dv) > PLACE_TOLERANCE:
            delta = frame.to_world(du, dv, 0.0)
            DB.ElementTransformUtils.MoveElement(doc, instance.Id,
                                                 self.point(delta))
            doc.Regenerate()
            placed = frame.local(reader.xyz(instance.Location.Point))
            if abs(wanted[0] - placed[0]) > PLACE_TOLERANCE or \
                    abs(wanted[1] - placed[1]) > PLACE_TOLERANCE:
                raise Exception(u"Revit placed it off the pipe and would "
                                u"not move it back")
        return instance

    def set_length(self, element, name, value):
        """Set a length parameter by BuiltInParameter name and read it
        back. Raises with the reason when either fails."""
        member = getattr(self.DB.BuiltInParameter, name, None)
        parameter = element.get_Parameter(member) if member is not None \
            else None
        if parameter is None or parameter.IsReadOnly:
            raise Exception(u"the shaft has no {0} that can be set".format(
                name))
        if not parameter.Set(value):
            raise Exception(u"the shaft would not take {0}".format(name))
        if abs(parameter.AsDouble() - value) > 1e-6:
            raise Exception(u"the shaft's {0} did not keep its "
                            u"value".format(name))

    def make_shaft(self, job):
        """A shaft opening spanning only the host's own thickness at the
        opening (`model.shaft_band`). Returns the shaft."""
        levels = self.levels()
        bottom, top = model.shaft_band(job.entry, job.exit, job.opening,
                                       job.normals)
        chosen = model.shaft_levels([z for z, _ in levels], bottom, top)
        if chosen is None:
            raise Exception(u"the model has no levels to hang a shaft "
                            u"from")
        base_z, base_level = levels[chosen[0]]
        top_z, top_level = levels[chosen[1]]
        shaft = self.doc.Create.NewOpening(
            base_level, top_level, self.profile(job.opening, base_z))
        if shaft is None:
            raise Exception(u"Revit made no shaft")
        self.set_length(shaft, SHAFT_OFFSETS[0], bottom - base_z)
        self.set_length(shaft, SHAFT_OFFSETS[1], top - top_z)
        return shaft

    def ways(self, job):
        """[(label, kind, maker)] in the order they are tried. The label
        names a sketch height, u"shaft", or u"" for the only way."""
        if job.host.kind == model.WALL:
            if job.opening.shape == model.CIRCULAR:
                return [(u"", ROUND, lambda: self.place_round(job))]
            return [(u"", OPENING, lambda: self.make_wall(job))]
        found = []
        for label, z in self.sketch_heights(job):
            found.append((label, OPENING,
                          (lambda height: lambda: self.make_vertical(
                              job, height))(z)))
        found.append((u"shaft", SHAFT, lambda: self.make_shaft(job)))
        return found

    def make_one(self, job, before=None, after=None):
        """Make the job's opening, trying each way in turn. Returns
        (element, kind, result text); element is None when nothing was
        kept, and the text says what was tried and why it failed."""
        ways = self.ways(job)
        reasons = []
        for index, (label, kind, maker) in enumerate(ways):

            def work(maker=maker, kind=kind):
                if before is not None:
                    before()
                element = maker()
                if element is not None and after is not None:
                    after(element, kind)
                return element

            element, why = self.attempt(work)
            if element is not None:
                if kind == SHAFT:
                    text = (u"made as a shaft opening through the {0} only; "
                            u"a vertical cut was refused ({1})".format(
                                HOST_WORDS[job.host.kind].lower(),
                                u"; ".join(reasons)))
                elif index > 0:
                    text = u"made, sketched at {0}".format(label)
                else:
                    text = u"made"
                return element, kind, text
            if kind == SHAFT:
                why = u"shaft: " + why
            if why not in reasons:
                reasons.append(why)
        heights = [label for label, kind, _ in ways if kind == OPENING]
        tried = u""
        if len(heights) > 1:
            # Say what was tried: a refusal at one height and at four
            # read the same otherwise.
            tried = u" (sketch tried at {0})".format(u", ".join(heights))
        return None, None, u"not made{0}: {1}".format(tried,
                                                     u"; ".join(reasons))


def axis_still_meets(DB, host_element, pipe, entry, exit):
    """Whether the pipe's axis still meets the host near where it
    crossed it, which after cutting an opening there means the opening
    does not clear the pipe."""
    middle = model.scale(model.add(entry, exit), 0.5)
    limit = max(pipe.diameter / 2.0, 1.0 / model.MM_PER_FOOT)
    solids = reader.solids_of(DB, host_element)
    for start, end in reader.axis_segments(DB, solids, pipe.start,
                                           pipe.end):
        other = model.scale(model.add(start, end), 0.5)
        if model.length(model.sub(other, middle)) <= limit:
            return True
    return False
