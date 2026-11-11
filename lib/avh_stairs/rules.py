# -*- coding: utf-8 -*-
"""Stairs against Byggingarreglugerð 112/2012. No Revit here.

The rules come from the version in force from 1 May 2026 (document dated
6 Nov 2026), articles 6.4.6 to 6.4.10 and 6.5.1 to 6.5.5. Every result
carries the article it comes from, so a reader can check the reading
against the text rather than trust this module.

Everything is in metres and degrees. The script converts from Revit's
feet before anything reaches here.

## Three outcomes, not two

PASS and FAIL where the model holds what the rule needs. CHECK where
the rule depends on something the model does not say: which side of a
stair has a wall, whether the public uses outdoor steps, where doors
open. A CHECK is a question for the designer, never a quiet pass.
NOT_CHECKED where the value could not be read at all, or the rule does
not apply to the stair's use.

## Which rules apply depends on the stair's use

Revit does not know whether a stair is inside a dwelling, serves several
companies or is outdoors, and those decide the limits. Each stair
carries its use in the instance parameter AVH_Stair_Use, as one of the
codes in USES. A stair without one gets only the rules that hold for
every stair.
"""

import math

from avh_schedules.compat import to_text

PASS = u"pass"
FAIL = u"fail"
CHECK = u"check"
NOT_CHECKED = u"not checked"

PARAMETER = u"AVH_Stair_Use"

# Headroom over a stair with nothing at all above it.
OPEN = 999.0

# Revit rounds what it reports; a limit is met within half a millimetre.
TOLERANCE = 0.0005
ANGLE_TOLERANCE = 0.05


class Use(object):
    """One stair use, and the limits the regulation ties to it."""

    def __init__(self, code, label, kind, min_width=None, max_rise=3.30,
                 landing_length=None, public=False):
        self.code = code
        self.label = label
        # "general": indoor stair for general use, 6.4.9 first paragraph.
        # "other": other indoor stair, 6.4.9 second paragraph.
        # "outdoor": outdoor steps, 6.4.10 second paragraph.
        self.kind = kind
        self.min_width = min_width
        self.max_rise = max_rise
        self.landing_length = landing_length
        self.public = public


USES = [
    Use(u"ibud", u"Innan íbúðar", u"general", min_width=0.90),
    Use(u"fjolbyli_lyfta",
        u"Fleiri en ein íbúð, lyfta tekur sjúkrabörur", u"general",
        min_width=1.00),
    Use(u"fjolbyli", u"Fleiri en ein íbúð, engin sjúkrabörulyfta",
        u"general", min_width=1.20),
    Use(u"fyrirtaeki", u"Fleiri en eitt fyrirtæki", u"general",
        min_width=1.20),
    Use(u"almenningur", u"Bygging sem almenningur hefur aðgang að",
        u"general", max_rise=2.0, landing_length=1.30, public=True),
    Use(u"annar", u"Annar stigi innanhúss, t.d. tæknistigi", u"other"),
    Use(u"uti_adal", u"Útitröppur að aðalinngangi", u"outdoor",
        min_width=1.20, max_rise=None),
    Use(u"uti", u"Útitröppur, bakinngangur o.þ.h.", u"outdoor",
        min_width=0.90, max_rise=None),
    Use(u"uti_svalir", u"Tröppur af svölum íbúðar niður í garð",
        u"outdoor", min_width=0.60, max_rise=None),
]

BY_CODE = dict((use.code, use) for use in USES)

_FOLD = {u"á": u"a", u"é": u"e", u"í": u"i", u"ó": u"o",
         u"ú": u"u", u"ý": u"y", u"ö": u"o", u"æ": u"ae",
         u"ð": u"d", u"þ": u"th"}


def fold(text):
    """Lower case, Icelandic letters to ASCII, spaces and dashes to _."""
    text = to_text(text).strip().lower()
    out = []
    for char in text:
        char = _FOLD.get(char, char)
        if char in (u" ", u"-"):
            char = u"_"
        out.append(char)
    return u"".join(out)


def parse_use(value):
    """(Use or None, problem). A blank value is no use and no problem."""
    if value is None or not to_text(value).strip():
        return None, u""
    use = BY_CODE.get(fold(value))
    if use is None:
        return None, u"\"{0}\" is not a stair use code".format(
            to_text(value))
    return use, u""


# --------------------------------------------------------------------------
# Plain data the script fills in
# --------------------------------------------------------------------------

class Railing(object):
    def __init__(self, element_id, top=None, handrails=()):
        self.element_id = element_id
        self.top = top                      # top rail height, m, or None
        self.handrails = list(handrails)    # handrail heights, m


class Run(object):
    def __init__(self, element_id, width=None, rise=None, style=u"straight"):
        self.element_id = element_id
        self.width = width
        self.rise = rise
        self.style = style


class Landing(object):
    def __init__(self, element_id, length=None, measured_along=True):
        self.element_id = element_id
        self.length = length
        # False when no run direction was found and `length` is the
        # landing's smallest dimension instead.
        self.measured_along = measured_along


class Stair(object):
    def __init__(self, element_id, name=u"", use_value=None, riser=None,
                 going=None, base=None, top=None, runs=(), landings=(),
                 has_risers=None, nosing=None, tread_thickness=None,
                 railings=(), headroom=None, plan=None, loose_railings=0):
        self.element_id = element_id
        self.name = name
        self.use_value = use_value
        self.riser = riser
        self.going = going
        self.base = base
        self.top = top
        self.runs = list(runs)
        self.landings = list(landings)
        self.has_risers = has_risers
        self.nosing = nosing
        self.tread_thickness = tread_thickness
        self.railings = list(railings)
        self.headroom = headroom
        # Railings next to the stair but not hosted on it: drawn along a
        # path rather than placed on the stair. Their side and height
        # are not read, but they are not nothing either.
        self.loose_railings = loose_railings
        # (min_x, min_y, max_x, max_y) in plan, for finding stacked
        # stairs in the same stairwell.
        self.plan = plan


class Result(object):
    def __init__(self, rule, article, status, message):
        self.rule = rule
        self.article = article
        self.status = status
        self.message = message

    def __repr__(self):
        return u"<{0} {1}: {2}>".format(self.status, self.rule, self.message)


def mm(value):
    return u"{0:.0f} mm".format(value * 1000.0)


def m(value):
    return u"{0:.2f} m".format(value)


def slope_of(riser, going):
    """Degrees. None when the going is unusable."""
    if riser is None or going is None or going <= 0:
        return None
    return math.degrees(math.atan2(riser, going))


def within(value, low, high, tolerance=TOLERANCE):
    return low - tolerance <= value <= high + tolerance


# --------------------------------------------------------------------------
# Stairwells: stairs stacked floor on floor
# --------------------------------------------------------------------------

def stairwells(stairs, gap=0.05):
    """Group stairs that continue one another up a stairwell.

    Two stairs belong together when their plans overlap and one starts
    within `gap` of where the other ends. Returns a dict from element id
    to the list of stairs in its group, lowest first. A stair without a
    plan or heights stands alone.
    """
    parent = dict((stair.element_id, stair.element_id) for stair in stairs)

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def overlap(a, b):
        return (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])

    usable = [s for s in stairs if s.plan is not None and
              s.base is not None and s.top is not None]
    for index, a in enumerate(usable):
        for b in usable[index + 1:]:
            if not overlap(a.plan, b.plan):
                continue
            if abs(a.top - b.base) <= gap or abs(b.top - a.base) <= gap:
                parent[find(a.element_id)] = find(b.element_id)

    groups = {}
    for stair in stairs:
        groups.setdefault(find(stair.element_id), []).append(stair)
    found = {}
    for members in groups.values():
        members.sort(key=lambda s: (s.base if s.base is not None else 0.0))
        for stair in members:
            found[stair.element_id] = members
    return found


# --------------------------------------------------------------------------
# Landing length along the direction of travel
# --------------------------------------------------------------------------

def extent_along(points, direction):
    """Length of a point set projected on a plan direction."""
    dx, dy = direction
    size = math.hypot(dx, dy)
    if not points or size == 0:
        return None
    dx, dy = dx / size, dy / size
    values = [p[0] * dx + p[1] * dy for p in points]
    return max(values) - min(values)


def smallest_width(points):
    """The smallest extent of a plan polygon over its own edge directions."""
    best = None
    count = len(points)
    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]
        dx, dy = b[0] - a[0], b[1] - a[1]
        if math.hypot(dx, dy) < 1e-9:
            continue
        # The extent across this edge.
        width = extent_along(points, (-dy, dx))
        if width is not None and (best is None or width < best):
            best = width
    return best


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------

def _riser_going(stair, use, floors, results):
    riser, going = stair.riser, stair.going
    if riser is None or going is None:
        results.append(Result(u"Riser and going", u"6.4.9", NOT_CHECKED,
                              u"riser or going could not be read"))
        return
    slope = slope_of(riser, going)

    if use.kind == u"general":
        status = PASS if within(riser, 0.120, 0.180) else FAIL
        results.append(Result(
            u"Riser", u"6.4.9 1. mgr.", status,
            u"{0}, allowed 120 to 180 mm".format(mm(riser))))
        least = 0.260 if floors > 2 else 0.240
        status = PASS if going >= least - TOLERANCE else FAIL
        results.append(Result(
            u"Going", u"6.4.9 1. mgr.", status,
            u"{0}, at least {1} for a stair serving {2} floors".format(
                mm(going), mm(least), floors)))
        status = PASS if within(slope, 30.0, 36.0, ANGLE_TOLERANCE) else FAIL
        results.append(Result(
            u"Slope", u"6.4.9 1. mgr.", status,
            u"{0:.1f}°, allowed 30° to 36°".format(slope)))
        pace = 2 * riser + going
        status = PASS if within(pace, 0.600, 0.640) else FAIL
        results.append(Result(
            u"2h + b", u"6.4.9 1. mgr.", status,
            u"{0}, allowed 600 to 640 mm".format(mm(pace))))
        _nosing(stair, results)
        return

    if use.kind == u"outdoor":
        status = PASS if within(riser, 0.120, 0.160) else FAIL
        results.append(Result(
            u"Riser", u"6.4.10 2. mgr.", status,
            u"{0}, allowed 120 to 160 mm".format(mm(riser))))
        status = PASS if going >= 0.280 - TOLERANCE else FAIL
        results.append(Result(
            u"Going", u"6.4.10 2. mgr.", status,
            u"{0}, at least 280 mm".format(mm(going))))
        # "skal almennt vera": generally, so a question, not a failure.
        status = PASS if within(slope, 17.0, 30.0, ANGLE_TOLERANCE) else CHECK
        results.append(Result(
            u"Slope", u"6.4.10 2. mgr.", status,
            u"{0:.1f}°, generally 17° to 30°".format(slope)))
        return

    # Other indoor stairs: the band follows the slope.
    if slope < 30.0:
        value, low, high, text = 4 * riser + going, 0.940, 0.980, u"4h + b"
        band = u"a"
    elif slope < 45.0:
        value, low, high, text = 2 * riser + going, 0.600, 0.640, u"2h + b"
        band = u"b"
    elif slope < 60.0:
        value, low, high, text = riser, 0.200, 0.220, u"riser"
        band = u"c"
    elif slope <= 75.0:
        value, low, high, text = 4.0 / 3.0 * riser + going, 0.500, 0.520, \
            u"4/3h + b"
        band = u"d"
    else:
        value, low, high, text = riser, 0.310, 0.330, u"riser"
        band = u"e"
    status = PASS if within(value, low, high) else FAIL
    results.append(Result(
        u"Rise and going for the slope", u"6.4.9 2. mgr. {0}".format(band),
        status, u"{0:.1f}°: {1} is {2}, allowed {3} to {4}".format(
            slope, text, mm(value), mm(low), mm(high))))
    if band == u"e":
        widths = [run.width for run in stair.runs if run.width is not None]
        if widths:
            status = PASS if max(widths) <= 0.500 + TOLERANCE else CHECK
            results.append(Result(
                u"Ladder width", u"6.4.9 2. mgr. e", status,
                u"{0}, as a rule at most 500 mm".format(mm(max(widths)))))


def _nosing(stair, results):
    if stair.going >= 0.300 - TOLERANCE:
        results.append(Result(u"Nosing", u"6.4.9 1. mgr.", PASS,
                              u"going {0}, no nosing needed".format(
                                  mm(stair.going))))
        return
    if stair.nosing is None:
        results.append(Result(u"Nosing", u"6.4.9 1. mgr.", NOT_CHECKED,
                              u"the nosing length could not be read"))
        return
    if stair.nosing <= TOLERANCE:
        results.append(Result(
            u"Nosing", u"6.4.9 1. mgr.", FAIL,
            u"going {0} is under 300 mm and the steps have no "
            u"nosing".format(mm(stair.going))))
        return
    total = stair.going + stair.nosing
    status = PASS if total >= 0.300 - TOLERANCE else FAIL
    results.append(Result(
        u"Nosing", u"6.4.9 1. mgr.", status,
        u"going {0} plus nosing {1} is {2}, at least 300 mm".format(
            mm(stair.going), mm(stair.nosing), mm(total))))


def _uniform(stair, well, results):
    others = [s for s in well if s is not stair and s.riser is not None and
              s.going is not None]
    if stair.riser is None or stair.going is None or not others:
        return
    odd = [s for s in others if abs(s.riser - stair.riser) > TOLERANCE or
           abs(s.going - stair.going) > TOLERANCE]
    if not odd:
        results.append(Result(
            u"Same steps on every floor", u"6.4.9 1. mgr.", PASS,
            u"{0} other flight(s) in this stairwell match".format(
                len(others))))
        return
    results.append(Result(
        u"Same steps on every floor", u"6.4.9 1. mgr.", FAIL,
        u"differs from {0}".format(u", ".join(
            u"id {0} ({1} / {2})".format(s.element_id, mm(s.riser),
                                         mm(s.going)) for s in odd))))


def _open_risers(stair, results):
    if stair.has_risers is None:
        results.append(Result(u"Open risers", u"6.4.6", NOT_CHECKED,
                              u"whether the steps have risers could not "
                              u"be read"))
        return
    if stair.has_risers:
        return
    if stair.riser is None or stair.tread_thickness is None:
        results.append(Result(u"Open risers", u"6.4.6", NOT_CHECKED,
                              u"open steps, but the gap could not be "
                              u"worked out"))
        return
    gap = stair.riser - stair.tread_thickness
    status = PASS if gap <= 0.089 + TOLERANCE else FAIL
    results.append(Result(
        u"Open risers", u"6.4.6", status,
        u"gap between treads {0}, at most 89 mm".format(mm(gap))))


def _landings(stair, use, results):
    rises = [run.rise for run in stair.runs if run.rise is not None]
    if use.kind == u"outdoor":
        tall = [r for r in rises if r > 1.5 + TOLERANCE]
        if tall:
            results.append(Result(
                u"Rise without a landing", u"6.4.7 5. mgr.", CHECK,
                u"a flight rises {0}; outdoor steps the public uses need a "
                u"landing above 1.5 m".format(m(max(tall)))))
        elif rises:
            results.append(Result(
                u"Rise without a landing", u"6.4.7 5. mgr.", PASS,
                u"highest flight {0}, at most 1.5 m".format(m(max(rises)))))
    elif rises:
        limit = use.max_rise
        status = PASS if max(rises) <= limit + TOLERANCE else FAIL
        results.append(Result(
            u"Rise without a landing",
            u"6.4.7 2. mgr." if use.public else u"6.4.7 1. mgr.", status,
            u"highest flight {0}, at most {1}".format(m(max(rises)),
                                                      m(limit))))
    else:
        results.append(Result(u"Rise without a landing", u"6.4.7",
                              NOT_CHECKED, u"flight heights could not be "
                              u"read"))

    if use.landing_length is not None:
        for landing in stair.landings:
            if landing.length is None:
                results.append(Result(
                    u"Landing length", u"6.4.7 2. mgr.", NOT_CHECKED,
                    u"landing {0} could not be measured".format(
                        landing.element_id)))
                continue
            ok = landing.length >= use.landing_length - TOLERANCE
            status = PASS if ok else (FAIL if landing.measured_along
                                      else CHECK)
            how = u"along the direction of travel" if landing.measured_along \
                else u"its smallest dimension; direction unknown"
            results.append(Result(
                u"Landing length", u"6.4.7 2. mgr.", status,
                u"landing {0}: {1} ({2}), at least {3}".format(
                    landing.element_id, m(landing.length), how,
                    m(use.landing_length))))


def _width(stair, use, results):
    widths = [run.width for run in stair.runs if run.width is not None]
    if not widths:
        results.append(Result(u"Width", u"6.4.8", NOT_CHECKED,
                              u"run width could not be read"))
        return
    narrowest = min(widths)
    if use.min_width is None:
        results.append(Result(
            u"Width", u"6.4.8 3. mgr.", CHECK,
            u"narrowest flight {0}; no fixed minimum for this use, it must "
            u"match the corridor and the escape route (9. hluti)".format(
                m(narrowest))))
        return
    status = PASS if narrowest >= use.min_width - TOLERANCE else FAIL
    results.append(Result(
        u"Width", u"6.4.8", status,
        u"narrowest flight {0}, at least {1}".format(
            m(narrowest), m(use.min_width))))


def _headroom(stair, results):
    if stair.headroom is None:
        results.append(Result(u"Headroom", u"6.4.8 4. mgr.", NOT_CHECKED,
                              u"headroom was not measured"))
        return
    if stair.headroom >= OPEN:
        results.append(Result(u"Headroom", u"6.4.8 4. mgr.", PASS,
                              u"nothing above the treads"))
        return
    status = PASS if stair.headroom >= 2.10 - TOLERANCE else FAIL
    results.append(Result(
        u"Headroom", u"6.4.8 4. mgr.", status,
        u"least clearance over a tread {0}, at least 2.10 m".format(
            m(stair.headroom))))


def _railings(stair, use, well, results):
    count = len(stair.railings)
    widths = [run.width for run in stair.runs if run.width is not None]
    widest = max(widths) if widths else None
    narrowest = min(widths) if widths else None

    if count == 0 and stair.loose_railings:
        results.append(Result(
            u"Handrails both sides", u"6.5.1", CHECK,
            u"no railings are hosted on this stair, but {0} drawn "
            u"separately lie along it; their sides and heights are not "
            u"read".format(stair.loose_railings)))
    elif count >= 2:
        results.append(Result(u"Handrails both sides", u"6.5.1", PASS,
                              u"{0} railings".format(count)))
    elif count == 1 and narrowest is not None and \
            narrowest <= 0.90 + TOLERANCE:
        results.append(Result(
            u"Handrails both sides", u"6.5.1", CHECK,
            u"one railing; allowed on a stair 0.9 m or narrower only where "
            u"the other side is a wall"))
    else:
        results.append(Result(
            u"Handrails both sides", u"6.5.1", FAIL,
            u"{0} railing(s); both sides need one".format(count)))
    if widest is not None and widest > 2.70 + TOLERANCE and count < 3:
        results.append(Result(
            u"Middle handrail", u"6.5.1", FAIL,
            u"{0} wide; more than 2.70 m between handrails needs an extra "
            u"one".format(m(widest))))

    if not stair.railings:
        return
    if use is not None and use.kind == u"outdoor":
        low, high, where = 0.90, 0.90, u"6.5.2, 6.5.4"
    else:
        low, high, where = 0.80, 0.90, u"6.5.2"
    for railing in stair.railings:
        grips = list(railing.handrails)
        if railing.top is not None:
            grips.append(railing.top)
        if not grips:
            results.append(Result(u"Handrail height", where, NOT_CHECKED,
                                  u"railing {0}: heights could not be "
                                  u"read".format(railing.element_id)))
            continue
        good = [g for g in grips if within(g, low, high, 0.005)]
        if good:
            results.append(Result(
                u"Handrail height", where, PASS,
                u"railing {0}: grip at {1}".format(railing.element_id,
                                                   m(good[0]))))
        else:
            results.append(Result(
                u"Handrail height", where, FAIL,
                u"railing {0}: nothing to grip between {1} and {2} "
                u"({3})".format(railing.element_id, m(low), m(high),
                                u", ".join(m(g) for g in grips))))

    for railing in stair.railings:
        if railing.top is None:
            continue
        if railing.top < 0.90 - 0.005:
            results.append(Result(
                u"Guard height", u"6.5.4", CHECK,
                u"railing {0}: top {1}; fine as a handrail on a wall, but "
                u"where there is a drop the guard must be at least "
                u"0.90 m".format(railing.element_id, m(railing.top))))
    floors = len(well) + 1
    lower = [r for r in stair.railings
             if r.top is not None and r.top < 1.20 - 0.005]
    if floors >= 3 and lower:
        results.append(Result(
            u"Guard height", u"6.5.4 2. mgr.", CHECK,
            u"this stairwell reaches a 3rd floor; from there up, where the "
            u"gap between flights is over 0.30 m or the stair turns, guards "
            u"need 1.20 m ({0} railing(s) are lower)".format(len(lower))))


def _shapes(stair, results):
    curved = [run for run in stair.runs if run.style != u"straight"]
    if curved:
        results.append(Result(
            u"Winders and spirals", u"6.4.10 1. mgr.", CHECK,
            u"{0} flight(s) turn; the going 450 mm from the inner handrail "
            u"must be at least 150 mm, which is not measured".format(
                len(curved))))


def check(stair, wells):
    """Every result for one stair. Returns (use or None, [Result])."""
    results = []
    use, problem = parse_use(stair.use_value)
    well = wells.get(stair.element_id, [stair])
    floors = len(well) + 1
    if use is None:
        results.append(Result(
            u"Use", u"", NOT_CHECKED,
            problem or u"no {0}; only the rules for every stair were "
            u"checked".format(PARAMETER)))
    else:
        _riser_going(stair, use, floors, results)
        if use.kind == u"general":
            _uniform(stair, well, results)
        _landings(stair, use, results)
        _width(stair, use, results)
    _open_risers(stair, results)
    _headroom(stair, results)
    _railings(stair, use, well, results)
    _shapes(stair, results)
    return use, results


def worst(results):
    for status in (FAIL, CHECK, NOT_CHECKED):
        if any(r.status == status for r in results):
            return status
    return PASS
