# -*- coding: utf-8 -*-
"""
Runs the real Stair Check script against a mocked Revit, and the rules
module against Byggingarreglugerð 112/2012 directly.

The rules are checked against figures taken from the regulation text
(version in force from 1 May 2026), on both sides of every limit, so a
limit typed wrong fails in either direction.

The fake lies the way Revit is likely to:

- Rays only work while the 3D view made for them exists. After the
  probe transaction is rolled back, the intersector raises.
- Obstacles in linked models are only hit when the intersector is told
  to look in links.
- A stair's own handrails hang over the edge rays, so a probe that does
  not leave them out reports headroom that is not there.
- An element type's Name raises, as ElementType.Name can on IronPython.
- Parameter.Set returns False on a read only parameter rather than
  raising.

Run outside Revit:

    python test_stair_check_harness.py
"""

import math
import os
import runpy
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

SCRIPT = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Stair Check.pushbutton", "script.py")

from avh_stairs import rules  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def safely(function, *args):
    try:
        return function(*args)
    except Exception as exc:
        return u"<raised {0}>".format(exc)


FT = 1.0 / 0.3048


def statuses(found, rule):
    return [r.status for r in found if r.rule == rule]


def status_of(stair, rule, wells=None):
    _use, found = rules.check(stair, wells or rules.stairwells([stair]))
    got = statuses(found, rule)
    return got[0] if len(got) == 1 else tuple(got)


def good(**kwargs):
    """An office stair that meets every rule for `fyrirtaeki`."""
    values = dict(
        element_id=1, use_value=u"fyrirtaeki", riser=0.170, going=0.280,
        base=0.0, top=3.40,
        runs=[rules.Run(11, 1.20, 1.70), rules.Run(12, 1.20, 1.70)],
        landings=[rules.Landing(21, 1.40)], has_risers=True, nosing=0.020,
        tread_thickness=0.05,
        railings=[rules.Railing(31, 0.90, [0.90]), rules.Railing(32, 0.90)],
        headroom=2.50, plan=(0.0, 0.0, 3.0, 5.0))
    values.update(kwargs)
    return rules.Stair(**values)


# --------------------------------------------------------------------------
# Uses and their codes
# --------------------------------------------------------------------------

def code_of(text):
    use = rules.parse_use(text)[0]
    return use.code if use is not None else None


check("use: a plain code is read", code_of(u"ibud") == u"ibud")
check("use: Icelandic letters and capitals fold to the code",
      code_of(u"Fyrirtæki") == u"fyrirtaeki")
check("use: Íbúð folds to ibud", code_of(u"Íbúð") == u"ibud")
check("use: spaces fold to underscores",
      code_of(u"uti adal") == u"uti_adal")
check("use: blank is no use and no problem",
      rules.parse_use(u"  ") == (None, u""))
check("use: an unknown word is named in the problem",
      u"skrifstofa" in rules.parse_use(u"skrifstofa")[1])
check("use: every code folds to itself",
      all(rules.fold(use.code) == use.code for use in rules.USES))

# --------------------------------------------------------------------------
# 6.4.9 first paragraph: stairs for general use indoors
# --------------------------------------------------------------------------

check("good stair: passes everything",
      rules.worst(rules.check(good(), rules.stairwells([good()]))[1])
      == rules.PASS,
      [r for r in rules.check(good(), rules.stairwells([good()]))[1]
       if r.status != rules.PASS])

check("riser 180 mm passes", status_of(good(riser=0.180, going=0.270),
                                       u"Riser") == rules.PASS)
check("riser 181 mm fails", status_of(good(riser=0.181, going=0.270),
                                      u"Riser") == rules.FAIL)
check("riser 120 mm passes", status_of(good(riser=0.120), u"Riser")
      == rules.PASS)
check("riser 119 mm fails", status_of(good(riser=0.119), u"Riser")
      == rules.FAIL)
check("going 240 mm passes between two floors",
      status_of(good(going=0.240), u"Going") == rules.PASS)
check("going 239 mm fails", status_of(good(going=0.239), u"Going")
      == rules.FAIL)
check("2h + b 640 mm passes", status_of(good(riser=0.180, going=0.280),
                                        u"2h + b") == rules.PASS)
check("2h + b 641 mm fails", status_of(good(riser=0.180, going=0.281),
                                       u"2h + b") == rules.FAIL)
check("2h + b 599 mm fails", status_of(good(riser=0.160, going=0.279),
                                       u"2h + b") == rules.FAIL)
check("slope 37 degrees fails",
      status_of(good(riser=0.180, going=0.180 / math.tan(math.radians(37))),
                u"Slope") == rules.FAIL)
check("slope 29 degrees fails",
      status_of(good(riser=0.150, going=0.150 / math.tan(math.radians(29))),
                u"Slope") == rules.FAIL)
check("nosing: going 280 with none fails",
      status_of(good(nosing=0.0), u"Nosing") == rules.FAIL)
check("nosing: none at all is named as missing, not as too short",
      any(u"no nosing" in r.message for r in
          rules.check(good(nosing=0.0), rules.stairwells([good()]))[1]))
check("nosing: going 280 plus 15 fails",
      status_of(good(nosing=0.015), u"Nosing") == rules.FAIL)
check("nosing: going 300 needs none",
      status_of(good(going=0.300, riser=0.165, nosing=0.0), u"Nosing")
      == rules.PASS)
check("nosing: unreadable is not checked, not passed",
      status_of(good(nosing=None), u"Nosing") == rules.NOT_CHECKED)

# --------------------------------------------------------------------------
# Stairwells
# --------------------------------------------------------------------------

stack = [good(element_id=1, base=0.0, top=3.40, going=0.250, riser=0.170),
         good(element_id=2, base=3.40, top=6.80, going=0.250, riser=0.170),
         good(element_id=3, base=6.80, top=10.20, going=0.250, riser=0.170)]
wells = rules.stairwells(stack)
check("stairwell: three stacked flights are one group",
      len(wells[1]) == 3 and wells[1] is wells[3])
check("stairwell: going 250 fails for a stair serving four floors",
      status_of(stack[0], u"Going", wells) == rules.FAIL)
check("stairwell: going 250 alone, between two floors, passes",
      status_of(stack[0], u"Going") == rules.PASS)
apart = good(element_id=9, base=3.40, top=6.80, plan=(10.0, 0.0, 13.0, 5.0))
check("stairwell: a stair elsewhere in plan is not stacked",
      len(rules.stairwells([stack[0], apart])[1]) == 1)
gapped = good(element_id=8, base=4.00, top=7.40)
check("stairwell: a stair starting 0.6 m higher is not stacked",
      len(rules.stairwells([stack[0], gapped])[1]) == 1)
odd = [good(element_id=1), good(element_id=2, base=3.40, top=6.80,
                                riser=0.175)]
check("stairwell: a flight with other steps fails on both",
      status_of(odd[0], u"Same steps on every floor",
                rules.stairwells(odd)) == rules.FAIL and
      status_of(odd[1], u"Same steps on every floor",
                rules.stairwells(odd)) == rules.FAIL)
check("stairwell: matching flights pass",
      status_of(stack[0], u"Same steps on every floor", wells) == rules.PASS)

# --------------------------------------------------------------------------
# Landings, width, headroom, open risers
# --------------------------------------------------------------------------

check("flight 3.30 m passes",
      status_of(good(runs=[rules.Run(11, 1.2, 3.30)]),
                u"Rise without a landing") == rules.PASS)
check("flight 3.31 m fails",
      status_of(good(runs=[rules.Run(11, 1.2, 3.31)]),
                u"Rise without a landing") == rules.FAIL)
public = dict(use_value=u"almenningur")
check("public: flight 2.10 m fails",
      status_of(good(runs=[rules.Run(11, 1.2, 2.10)], **public),
                u"Rise without a landing") == rules.FAIL)
check("public: flight 2.00 m passes",
      status_of(good(runs=[rules.Run(11, 1.2, 2.00)], **public),
                u"Rise without a landing") == rules.PASS)
check("public: landing 1.29 m fails",
      status_of(good(landings=[rules.Landing(21, 1.29)], **public),
                u"Landing length") == rules.FAIL)
check("public: landing 1.30 m passes",
      status_of(good(landings=[rules.Landing(21, 1.30)], **public),
                u"Landing length") == rules.PASS)
check("public: a short landing measured without a direction is a question",
      status_of(good(landings=[rules.Landing(21, 1.20, False)], **public),
                u"Landing length") == rules.CHECK)
check("public: width has no fixed minimum, so it is a question",
      status_of(good(**public), u"Width") == rules.CHECK)
check("offices: landing length is not a rule",
      status_of(good(landings=[rules.Landing(21, 0.5)]), u"Landing length")
      == ())

for code, least in ((u"ibud", 0.90), (u"fjolbyli_lyfta", 1.00),
                    (u"fjolbyli", 1.20), (u"fyrirtaeki", 1.20),
                    (u"uti_adal", 1.20), (u"uti", 0.90),
                    (u"uti_svalir", 0.60)):
    over = good(use_value=code, runs=[rules.Run(11, least, 1.5)],
                riser=0.150, going=0.300)
    under = good(use_value=code, runs=[rules.Run(11, least - 0.01, 1.5)],
                 riser=0.150, going=0.300)
    check(u"width {0}: {1} m passes, 1 cm less fails".format(code, least),
          status_of(over, u"Width") == rules.PASS and
          status_of(under, u"Width") == rules.FAIL)

check("headroom 2.10 m passes", status_of(good(headroom=2.10), u"Headroom")
      == rules.PASS)
check("headroom 2.09 m fails", status_of(good(headroom=2.09), u"Headroom")
      == rules.FAIL)
check("headroom not measured is not checked",
      status_of(good(headroom=None), u"Headroom") == rules.NOT_CHECKED)
check("open risers: 120 mm gap fails",
      status_of(good(has_risers=False), u"Open risers") == rules.FAIL)
check("open risers: 95 mm gap fails",
      status_of(good(has_risers=False, tread_thickness=0.075),
                u"Open risers") == rules.FAIL)
check("open risers: 89 mm gap passes",
      status_of(good(has_risers=False, tread_thickness=0.081),
                u"Open risers") == rules.PASS)
check("closed risers: no open riser rule",
      status_of(good(), u"Open risers") == ())

# --------------------------------------------------------------------------
# Outdoor steps and other stairs
# --------------------------------------------------------------------------

out = dict(use_value=u"uti_adal", riser=0.150, going=0.300)
check("outdoor: riser 160 passes, 161 fails",
      status_of(good(**dict(out, riser=0.160)), u"Riser") == rules.PASS and
      status_of(good(**dict(out, riser=0.161)), u"Riser") == rules.FAIL)
check("outdoor: going 280 passes, 279 fails",
      status_of(good(**dict(out, going=0.280)), u"Going") == rules.PASS and
      status_of(good(**dict(out, going=0.279)), u"Going") == rules.FAIL)
check("outdoor: steep steps are a question, since the rule says generally",
      status_of(good(**dict(out, riser=0.160, going=0.250)), u"Slope")
      == rules.CHECK)
check("outdoor: a flight over 1.5 m asks about a landing",
      status_of(good(runs=[rules.Run(11, 1.2, 1.6)], **out),
                u"Rise without a landing") == rules.CHECK)
check("outdoor: handrail at 0.85 m fails, 0.90 needed",
      status_of(good(railings=[rules.Railing(31, 0.85),
                               rules.Railing(32, 0.85)], **out),
                u"Handrail height") == (rules.FAIL, rules.FAIL))
check("indoor: handrail at 0.85 m passes",
      status_of(good(railings=[rules.Railing(31, 0.85),
                               rules.Railing(32, 0.85)]),
                u"Handrail height") == (rules.PASS, rules.PASS))

other = dict(use_value=u"annar")
steep = good(riser=0.210, going=0.210 / math.tan(math.radians(50)), **other)
check("other stair at 50 degrees: band c, riser 210 passes",
      status_of(steep, u"Rise and going for the slope") == rules.PASS)
steep_bad = good(riser=0.230, going=0.230 / math.tan(math.radians(50)),
                 **other)
check("other stair at 50 degrees: riser 230 fails",
      status_of(steep_bad, u"Rise and going for the slope") == rules.FAIL)
shallow = good(riser=0.120, going=0.480, **other)
check("other stair under 30 degrees: band a, 4h + b = 960 passes",
      status_of(shallow, u"Rise and going for the slope") == rules.PASS)
ladder = good(riser=0.320, going=0.050, runs=[rules.Run(11, 0.60, 3.0)],
              **other)
check("other stair over 75 degrees: riser 320 passes, width over 500 asks",
      status_of(ladder, u"Rise and going for the slope") == rules.PASS and
      status_of(ladder, u"Ladder width") == rules.CHECK)
band_d = good(riser=0.283, going=0.132, **other)
check("other stair 60 to 75 degrees: band d, 4/3h + b = 509 passes",
      status_of(band_d, u"Rise and going for the slope") == rules.PASS)
check("other stair: no general use riser rule",
      status_of(steep, u"Riser") == ())

# --------------------------------------------------------------------------
# Railings
# --------------------------------------------------------------------------

check("one railing on a 1.2 m stair fails",
      status_of(good(railings=[rules.Railing(31, 0.9)]),
                u"Handrails both sides") == rules.FAIL)
check("one railing on a 0.9 m stair asks about the wall",
      status_of(good(railings=[rules.Railing(31, 0.9)], use_value=u"ibud",
                     runs=[rules.Run(11, 0.90, 1.7)]),
                u"Handrails both sides") == rules.CHECK)
check("no railings fails",
      status_of(good(railings=[]), u"Handrails both sides") == rules.FAIL)
check("2.8 m wide with two railings needs a middle one",
      status_of(good(runs=[rules.Run(11, 2.8, 1.7)]), u"Middle handrail")
      == rules.FAIL)
check("handrail at 0.95 m fails",
      status_of(good(railings=[rules.Railing(31, 1.10, [0.95]),
                               rules.Railing(32, 0.90)]),
                u"Handrail height") == (rules.FAIL, rules.PASS))
check("a 1.2 m guard with a 0.9 m handrail passes",
      status_of(good(railings=[rules.Railing(31, 1.20, [0.90]),
                               rules.Railing(32, 0.90)]),
                u"Handrail height") == (rules.PASS, rules.PASS))
check("a top rail under 0.90 m asks whether it is on a wall",
      rules.CHECK in status_of(good(railings=[rules.Railing(31, 0.85),
                                              rules.Railing(32, 0.90)]),
                               u"Guard height"))
check("a stairwell reaching a 3rd floor asks about 1.20 m guards",
      status_of(stack[0], u"Guard height", wells) == rules.CHECK)
check("a single stair does not",
      status_of(good(), u"Guard height") == ())
check("a winder asks about the walking line",
      status_of(good(runs=[rules.Run(11, 1.2, 1.7, u"winder")]),
                u"Winders and spirals") == rules.CHECK)

# --------------------------------------------------------------------------
# Unclassified
# --------------------------------------------------------------------------

blank = good(use_value=None, riser=0.200)
_use, found = rules.check(blank, rules.stairwells([blank]))
check("unclassified: no use, says so",
      statuses(found, u"Use") == [rules.NOT_CHECKED])
check("unclassified: riser limits are not applied",
      statuses(found, u"Riser") == [])
check("unclassified: headroom and railings still are",
      statuses(found, u"Headroom") == [rules.PASS] and
      statuses(found, u"Handrails both sides") == [rules.PASS])

# --------------------------------------------------------------------------
# Plan geometry
# --------------------------------------------------------------------------

square = [(0.0, 0.0), (1.3, 0.0), (1.3, 2.4), (0.0, 2.4)]
check("landing: length along x", abs(rules.extent_along(square, (1, 0))
                                     - 1.3) < 1e-9)
check("landing: length along y", abs(rules.extent_along(square, (0, 5))
                                     - 2.4) < 1e-9)
check("landing: smallest width of a rotated rectangle",
      abs(rules.smallest_width([(math.cos(a) * x - math.sin(a) * y,
                                 math.sin(a) * x + math.cos(a) * y)
                                for x, y in square
                                for a in [0.4]]) - 1.3) < 1e-9)


# ==========================================================================
# The script, against a fake Revit
# ==========================================================================

class FakeXYZ(object):
    BasisZ = None

    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


FakeXYZ.BasisZ = FakeXYZ(0, 0, 1)


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


class FakeFrame(object):
    def __init__(self, origin, tangent):
        self.Origin = origin
        self.BasisX = tangent


class FakeLine(object):
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.Length = math.hypot(b[0] - a[0], b[1] - a[1])

    def GetEndPoint(self, index):
        p = self.a if index == 0 else self.b
        return FakeXYZ(p[0], p[1], 0.0)

    def Tessellate(self):
        return [self.GetEndPoint(0), self.GetEndPoint(1)]

    def ComputeDerivatives(self, fraction, normalized):
        x = self.a[0] + (self.b[0] - self.a[0]) * fraction
        y = self.a[1] + (self.b[1] - self.a[1]) * fraction
        return FakeFrame(FakeXYZ(x, y, 0.0), FakeXYZ(
            (self.b[0] - self.a[0]) / self.Length,
            (self.b[1] - self.a[1]) / self.Length, 0.0))


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
        self.type_id = type_id or INVALID
        doc.elements[value] = self

    def GetTypeId(self):
        return self.type_id


class FakeRunType(FakeElement):
    def __init__(self, doc, value, has_risers=True, nosing=0.02,
                 thickness=0.05):
        FakeElement.__init__(self, doc, value)
        self.HasRisers = has_risers
        self.HasTreads = True
        self.NosingLength = nosing * FT
        self.TreadThickness = thickness * FT


class FakeStairsType(FakeElement):
    def __init__(self, doc, value, run_type):
        FakeElement.__init__(self, doc, value)
        self.RunType = run_type.Id

    @property
    def Name(self):
        raise AttributeError("Name")


class FakeRailingType(FakeElement):
    def __init__(self, doc, value, top=0.90, handrail=0.90):
        FakeElement.__init__(self, doc, value)
        self.TopRailType = FakeId(1) if top is not None else INVALID
        self.TopRailHeight = (top or 0.0) * FT
        self.PrimaryHandrailType = FakeId(2) if handrail else INVALID
        self.PrimaryHandrailHeight = (handrail or 0.0) * FT
        self.SecondaryHandrailType = INVALID
        self.SecondaryHandrailHeight = 0.0


class FakeRailing(FakeElement):
    def __init__(self, doc, value, railing_type, line_x, y0, y1, z0, z1):
        FakeElement.__init__(self, doc, value, railing_type.Id)
        self.TopRail = INVALID
        # Where its handrail hangs, for the rays: a strip along x = line_x.
        self.strip = (line_x - 0.05 * FT, y0, line_x + 0.05 * FT, y1, z0, z1)

    def GetHandRails(self):
        return []


class FakeRun(FakeElement):
    """A straight flight running north along x = cx from y0."""

    def __init__(self, doc, value, cx, y0, base, risers, riser, going,
                 width, style="Straight"):
        FakeElement.__init__(self, doc, value)
        self.BaseElevation = base * FT
        self.TopElevation = (base + risers * riser) * FT
        self.ActualRunWidth = width * FT
        self.ActualTreadsNumber = risers - 1
        self.StairsRunStyle = style
        self.path = [FakeLine((cx * FT, y0 * FT),
                              (cx * FT, (y0 + (risers - 1) * going) * FT))]

    def GetStairsPath(self):
        return list(self.path)


class FakeLanding(FakeElement):
    def __init__(self, doc, value, base, x0, y0, x1, y1):
        FakeElement.__init__(self, doc, value)
        self.BaseElevation = base * FT
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        self.loop = [FakeLine((pts[i][0] * FT, pts[i][1] * FT),
                              (pts[(i + 1) % 4][0] * FT,
                               pts[(i + 1) % 4][1] * FT)) for i in range(4)]

    def GetFootprintBoundary(self):
        return list(self.loop)


class FakeStairs(FakeElement):
    def __init__(self, doc, value, stairs_type, use=None, riser=0.170,
                 going=0.280, base=0.0, width=1.20, risers=20, x=2.0,
                 railings=2, top_rail=0.90, handrail=0.90, landing=1.40,
                 has_param=True, read_only=False):
        FakeElement.__init__(self, doc, value, stairs_type.Id)
        self.IsInEditMode = False
        self.ActualRiserHeight = riser * FT
        self.ActualTreadDepth = going * FT
        self.ActualRisersNumber = risers
        self.BaseElevation = base * FT
        self.TopElevation = (base + risers * riser) * FT
        self.param = FakeParam(use or u"", read_only) if has_param else None
        half = risers // 2
        self.runs = [FakeRun(doc, value * 10 + 1, x, 0.0, base, half, riser,
                             going, width),
                     FakeRun(doc, value * 10 + 2, x, (half - 1) * going +
                             landing, base + half * riser, risers - half,
                             riser, going, width)]
        y_land = (half - 1) * going
        self.landings = [FakeLanding(doc, value * 10 + 3, base + half * riser,
                                     x - width / 2, y_land, x + width / 2,
                                     y_land + landing)]
        railing_type = FakeRailingType(doc, value * 10 + 4, top_rail,
                                       handrail)
        y_end = y_land + landing + (risers - half - 1) * going
        self.railings = []
        for k in range(railings):
            side = x + (width / 2 - 0.03) * (1 if k == 0 else -1)
            self.railings.append(FakeRailing(
                doc, value * 10 + 5 + k, railing_type, side * FT, 0.0,
                y_end * FT, (base + 0.8) * FT, (base + risers * riser + 1.0)
                * FT))
        self.x, self.width, self.y_end = x, width, y_end

    def LookupParameter(self, name):
        if name == u"AVH_Stair_Use" and self.param is not None:
            return self.param
        if name == u"AVH_Stair_Use" and self.doc.bound:
            self.param = FakeParam(u"")
            return self.param
        return None

    def get_Parameter(self, which):
        return None

    def GetStairsRuns(self):
        return [run.Id for run in self.runs]

    def GetStairsLandings(self):
        return [landing.Id for landing in self.landings]

    def GetAssociatedRailings(self):
        return [railing.Id for railing in self.railings]

    def get_BoundingBox(self, view):
        return Namespace(
            Min=FakeXYZ((self.x - self.width / 2) * FT, 0.0,
                        self.BaseElevation),
            Max=FakeXYZ((self.x + self.width / 2) * FT, self.y_end * FT,
                        self.TopElevation))


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeDoc(object):
    def __init__(self, commit_status="Committed"):
        self.elements = {}
        self.stairs = []
        self.slabs = []          # (x0, y0, x1, y1, z) in feet, host
        self.linked_slabs = []   # same, in a linked model
        self.views = []
        self.bound = False
        self.commit_status = commit_status
        self.transactions = []
        self.Application = FakeApp(self)
        self.ParameterBindings = FakeBindings(self)
        run_type = FakeRunType(self, 900)
        self.stairs_type = FakeStairsType(self, 901, run_type)
        self.open_type = FakeStairsType(
            self, 902, FakeRunType(self, 903, has_risers=False))

    def GetElement(self, element_id):
        return self.elements.get(element_id.Value)

    def add(self, value, **kwargs):
        open_risers = kwargs.pop("open_risers", False)
        stair = FakeStairs(self, value, self.open_type if open_risers
                           else self.stairs_type, **kwargs)
        self.stairs.append(stair)
        return stair

    def slab(self, x0, y0, x1, y1, z, linked=False):
        (self.linked_slabs if linked else self.slabs).append(
            (x0 * FT, y0 * FT, x1 * FT, y1 * FT, z * FT))

    def Regenerate(self):
        pass


class FakeBindings(object):
    def __init__(self, doc):
        self.doc = doc

    def Insert(self, definition, binding, group):
        self.doc.bound = True
        self.inserted = (definition, binding, group)
        return True


class FakeDefinitions(object):
    def Create(self, options):
        return Namespace(Name=options.name, GUID=options.GUID)


class FakeApp(object):
    def __init__(self, doc):
        self.SharedParametersFilename = u"C:\\AVH\\shared.txt"
        self.Create = Namespace(
            NewCategorySet=lambda: Namespace(Insert=lambda c: None),
            NewInstanceBinding=lambda cats: Namespace(kind="instance"))

    def OpenSharedParameterFile(self):
        return Namespace(Groups=Namespace(
            Create=lambda name: Namespace(Definitions=FakeDefinitions())))


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False

    def Start(self):
        self.started = True
        self.saved = ([(s, s.param.value if s.param else None)
                       for s in self.doc.stairs], self.doc.bound,
                      list(self.doc.views))
        self.doc.transactions.append([self.name, "start"])

    def HasStarted(self):
        return self.started

    def RollBack(self):
        for stair, value in self.saved[0]:
            if stair.param is not None and value is not None:
                stair.param.value = value
        self.doc.bound = self.saved[1]
        self.doc.views = self.saved[2]
        self.doc.transactions[-1].append("rollback")

    def Commit(self):
        self.doc.transactions[-1].append("commit")
        if self.doc.commit_status != "Committed":
            self.RollBack()
        return self.doc.commit_status


class FakeView3D(object):
    def __init__(self, doc):
        self.doc = doc

    @staticmethod
    def CreateIsometric(doc, type_id):
        view = FakeView3D(doc)
        doc.views.append(view)
        return view


class FakeIntersector(object):
    """Hits the lowest slab or railing strip above a point.

    Only while its view still exists, and only links when told to.
    """

    def __init__(self, element_filter, target, view):
        self.excluded = set(getattr(element_filter, "ids", []))
        self.view = view
        self.FindReferencesInRevitLinks = False

    def FindNearest(self, origin, direction):
        doc = self.view.doc
        if self.view not in doc.views:
            raise Exception("The view is not valid")
        heights = []
        boxes = list(doc.slabs)
        if self.FindReferencesInRevitLinks:
            boxes += doc.linked_slabs
        for x0, y0, x1, y1, z in boxes:
            if x0 <= origin.X <= x1 and y0 <= origin.Y <= y1 and z > origin.Z:
                heights.append(z)
        for stair in doc.stairs:
            for railing in stair.railings:
                if railing.Id in self.excluded:
                    continue
                x0, y0, x1, y1, z0, z1 = railing.strip
                # A railing hangs over the edge of the flight: a ray at
                # the edge passes up through it.
                if x0 - 0.2 * FT <= origin.X <= x1 + 0.2 * FT and \
                        y0 <= origin.Y <= y1 and z1 > origin.Z:
                    heights.append(max(origin.Z + 0.01, z0))
        if not heights:
            return None
        return Namespace(Proximity=min(heights) - origin.Z)


class FakeExclusion(object):
    def __init__(self, ids):
        self.ids = list(ids)


class FakeList(list):
    def Add(self, item):
        self.append(item)

    @property
    def Count(self):
        return len(self)


class FakeCollector(object):
    def __init__(self, doc):
        self.doc = doc
        self.cls = None

    def OfClass(self, cls):
        self.cls = cls
        return self

    def WhereElementIsNotElementType(self):
        return self

    def __iter__(self):
        if self.cls is FakeStairs:
            return iter(list(self.doc.stairs))
        if self.cls == "ViewFamilyType":
            return iter([Namespace(ViewFamily="3D", Id=FakeId(5))])
        return iter([])


class Recorder(object):
    def __init__(self, add=True, tick=None, use=None, confirm=True):
        self.add = add
        self.tick = tick          # labels containing any of these, or ALL
        self.use = use
        self.alerts = []
        self.questions = []
        self.printed = []
        self.pickers = []

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
            if self.tick == "ALL":
                self.tick = None    # one round, then stop
                return list(items)
            picked = [i for i in items if any(t in i for t in self.tick)]
            self.tick = None        # one round, then stop
            return picked or None
        if self.use is None:
            return None
        for item in items:
            if item.startswith(self.use + u"  -  "):
                return item
        return None

    def print_md(self, text):
        self.printed.append(text)

    def text(self):
        return u"\n".join(self.alerts + self.questions + self.printed)


def run_script(doc, recorder=None):
    recorder = recorder or Recorder(add=False)
    db = Namespace(
        Architecture=Namespace(Stairs=FakeStairs),
        FilteredElementCollector=FakeCollector,
        ViewFamilyType="ViewFamilyType",
        ViewFamily=Namespace(ThreeDimensional="3D"),
        View3D=FakeView3D,
        ReferenceIntersector=FakeIntersector,
        FindReferenceTarget=Namespace(Element="Element"),
        ExclusionFilter=FakeExclusion,
        ElementIsElementTypeFilter=lambda inverted: FakeExclusion([]),
        ElementId=Namespace(InvalidElementId=INVALID),
        XYZ=FakeXYZ,
        Transaction=FakeTransaction,
        TransactionStatus=Namespace(Committed="Committed"),
        BuiltInParameter=Namespace(STAIRS_BASE_LEVEL_PARAM="base"),
        ExternalDefinitionCreationOptions=lambda name, spec: Namespace(
            name=name, spec=spec, GUID=None),
        SpecTypeId=Namespace(String=Namespace(Text="text")),
        GroupTypeId=Namespace(IdentityData="identity"),
        Category=Namespace(GetCategory=lambda d, c: "Stairs"),
        BuiltInCategory=Namespace(OST_Stairs="OST_Stairs"),
    )
    system = types.ModuleType("System")
    system.Guid = lambda text: u"guid:" + text
    collections = types.ModuleType("System.Collections")
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = Namespace(__getitem__=None)

    class _ListFactory(object):
        def __getitem__(self, item):
            return FakeList
    generic.List = _ListFactory()
    collections.Generic = generic
    system.Collections = collections
    for name, module in (("System", system),
                         ("System.Collections", collections),
                         ("System.Collections.Generic", generic)):
        sys.modules[name] = module
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
        runpy.run_path(SCRIPT, run_name="__main__")
    except Exception as exc:
        recorder.alerts.append(u"<script raised {0!r}>".format(exc))
    finally:
        for name in ("pyrevit", "System", "System.Collections",
                     "System.Collections.Generic"):
            sys.modules.pop(name, None)
    return recorder


def section(recorder, stair_id):
    """The report lines for one stair."""
    lines = recorder.printed
    start = None
    for index, line in enumerate(lines):
        if line.startswith(u"#### [{0}]".format(stair_id)):
            start = index
        elif start is not None and line.startswith(u"#### "):
            return lines[start:index]
    return lines[start:] if start is not None else []


def row(recorder, stair_id, rule):
    return [line for line in section(recorder, stair_id)
            if line.startswith(u"| {0} |".format(rule))]


# 1. A good office stair under a slab well clear of it.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki")
doc.slab(0, 0, 10, 20, 3.40 + 2.6)
rec = run_script(doc)
check("script: the good stair passes",
      section(rec, 1) and u"pass" in section(rec, 1)[0] and
      u"FAIL" not in u"\n".join(section(rec, 1)), section(rec, 1))
check("script: the riser was read in metres from feet",
      any(u"170 mm" in line for line in row(rec, 1, u"Riser")),
      row(rec, 1, u"Riser"))
check("script: headroom was measured, not skipped",
      any(u"pass" in line for line in row(rec, 1, u"Headroom")),
      row(rec, 1, u"Headroom"))
check("script: the headroom view was thrown away",
      doc.views == [] and any(t[0].endswith(u"headroom probe") and
                              t[-1] == "rollback" for t in doc.transactions),
      doc.transactions)
check("script: the report names the regulation version",
      any(u"1. ma\u00ed 2026" in line for line in rec.printed))
check("script: the type name raising did not matter",
      not any(u"raised" in a for a in rec.alerts), rec.alerts)

# 2. A slab low over the flight: headroom fails.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki")
doc.slab(0, 0, 10, 20, 0.170 * 5 + 2.0)
rec = run_script(doc)
check("headroom: a slab 2.0 m over a tread fails",
      any(u"FAIL" in line for line in row(rec, 1, u"Headroom")),
      row(rec, 1, u"Headroom"))

# 3. The same slab, but in a linked model.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki")
doc.slab(0, 0, 10, 20, 0.170 * 5 + 2.0, linked=True)
rec = run_script(doc)
check("headroom: a low slab in a link is found too",
      any(u"FAIL" in line for line in row(rec, 1, u"Headroom")),
      row(rec, 1, u"Headroom"))

# 3b. A beam 2.08 m over the middle of one tread, and clear of the
# nosings either side. The rays have to be in the middle of the tread
# and the clearance measured from the tread, not from where the ray
# starts.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki")
doc.slab(0, 3.5 * 0.280 - 0.05, 10, 3.5 * 0.280 + 0.05, 4 * 0.170 + 2.08)
rec = run_script(doc)
check("headroom: a beam over one tread is found, at 2.08 m",
      any(u"FAIL" in line and u"2.08 m" in line
          for line in row(rec, 1, u"Headroom")),
      row(rec, 1, u"Headroom"))

# 4. Nothing above: the stair's own handrails must not count.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki")
rec = run_script(doc)
check("headroom: open above, and the handrails are not mistaken for a "
      "ceiling", any(u"nothing above" in line
                     for line in row(rec, 1, u"Headroom")),
      row(rec, 1, u"Headroom"))

# 5. A stair with real failures.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki", x=20.0)
doc.add(2, use=u"fyrirtaeki", riser=0.190, going=0.260, width=1.00)
rec = run_script(doc)
check("fails: riser 190 mm", any(u"FAIL" in line
                                 for line in row(rec, 2, u"Riser")))
check("fails: width 1.00 m for several companies",
      any(u"FAIL" in line for line in row(rec, 2, u"Width")))
check("fails: the failing stair is listed before the passing one",
      section(rec, 1) and section(rec, 2) and
      rec.printed.index(section(rec, 2)[0]) <
      rec.printed.index(section(rec, 1)[0]))
check("fails: the summary counts one fail and one pass",
      any(u"1 fail" in line and u"1 pass" in line for line in rec.printed),
      [line for line in rec.printed if u"stair(s)" in line])

# 6. Open steps.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki", open_risers=True)
rec = run_script(doc)
check("open steps: gap 120 mm fails",
      any(u"FAIL" in line and u"120 mm" in line
          for line in row(rec, 1, u"Open risers")),
      row(rec, 1, u"Open risers"))

# 7. A stairwell: three stacked stairs with going 250.
doc = FakeDoc()
for k in range(3):
    doc.add(k + 1, use=u"fyrirtaeki", going=0.250, base=k * 3.40)
rec = run_script(doc)
check("stairwell: going 250 fails for four floors on every flight",
      all(any(u"FAIL" in line for line in row(rec, k + 1, u"Going"))
          for k in range(3)), row(rec, 1, u"Going"))

# 8. Public building: a 1.2 m landing.
doc = FakeDoc()
doc.add(1, use=u"almenningur", risers=12, landing=1.20)
rec = run_script(doc)
check("public: the landing is measured along travel and fails",
      any(u"FAIL" in line and u"along the direction of travel" in line
          for line in row(rec, 1, u"Landing length")),
      row(rec, 1, u"Landing length"))
doc = FakeDoc()
doc.add(1, use=u"almenningur", risers=12, landing=1.30)
rec = run_script(doc)
check("public: a 1.30 m landing passes, though 1.20 m is the narrow side",
      any(u"pass" in line for line in row(rec, 1, u"Landing length")),
      row(rec, 1, u"Landing length"))

# 9. Railings.
doc = FakeDoc()
doc.add(1, use=u"fyrirtaeki", railings=1)
doc.add(2, use=u"fyrirtaeki", x=8.0, handrail=0.95, top_rail=1.10)
rec = run_script(doc)
check("railings: one railing on a 1.2 m stair fails",
      any(u"FAIL" in line for line in row(rec, 1, u"Handrails both sides")))
check("railings: a handrail at 0.95 m on a 1.10 m guard fails",
      any(u"FAIL" in line for line in row(rec, 2, u"Handrail height")),
      row(rec, 2, u"Handrail height"))

# 10. Unclassified and misclassified.
doc = FakeDoc()
doc.add(1, use=u"")
doc.add(2, use=u"skrifstofa", x=8.0)
doc.add(3, use=u"Fyrirtæki", x=14.0)
rec = run_script(doc)
check("unclassified: reported as such",
      any(u"no use set" in line for line in section(rec, 1)[:1]))
check("misclassified: the bad value is named",
      any(u"skrifstofa" in line for line in section(rec, 2)))
check("Icelandic spelling: classified",
      u"fyrirt" in section(rec, 3)[0], section(rec, 3)[:1])

# 11. No parameter: offer to add, add it, then classify.
doc = FakeDoc()
doc.add(1, has_param=False)
doc.add(2, has_param=False, x=8.0)
original = doc.Application.SharedParametersFilename
rec = run_script(doc, Recorder(add=True, tick=[u"id 1"],
                               use=u"fyrirtaeki"))
check("parameter: offered", any(u"Add the parameter" in q
                                 for q in rec.questions), rec.questions)
check("parameter: bound to the model", doc.bound)
check("parameter: bound with AVH's GUID and as text in Identity Data",
      doc.ParameterBindings.inserted[0].GUID ==
      u"guid:f03e1b0f-96ae-4317-ba74-010f759f77eb" and
      doc.ParameterBindings.inserted[2] == "identity")
check("parameter: the user's shared parameter file is put back",
      doc.Application.SharedParametersFilename == original)
check("classify: the ticked stair got the use",
      doc.stairs[0].param.value == u"fyrirtaeki",
      doc.stairs[0].param.value)
check("classify: the other stays blank",
      doc.stairs[1].param is None or doc.stairs[1].param.value == u"")
check("classify: and the check used it",
      u"fyrirt" in section(rec, 1)[0], section(rec, 1)[:1])

doc = FakeDoc()
doc.add(1, has_param=False)
rec = run_script(doc, Recorder(add=False))
check("parameter: declined, nothing bound, still checked",
      not doc.bound and section(rec, 1))

# 12. Classification refused by Revit, and a read only parameter.
doc = FakeDoc(commit_status="RolledBack")
doc.add(1, use=u"")
rec = run_script(doc, Recorder(tick="ALL", use=u"ibud"))
check("classify: a rejected commit is reported and not kept",
      doc.stairs[0].param.value == u"" and
      any(u"rejected" in a for a in rec.alerts), rec.alerts)
doc = FakeDoc()
doc.add(1, use=u"", read_only=True)
rec = run_script(doc, Recorder(tick="ALL", use=u"ibud"))
check("classify: a read only parameter is reported",
      any(u"would not take" in a for a in rec.alerts), rec.alerts)

# 13. No stairs.
doc = FakeDoc()
rec = run_script(doc)
check("no stairs: said so", any(u"no stairs" in a for a in rec.alerts))

# --------------------------------------------------------------------------

check("harness: a check that should fail does",
      status_of(good(riser=0.300), u"Riser") == rules.FAIL)
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
