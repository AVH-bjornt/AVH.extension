# -*- coding: utf-8 -*-
"""Rise and going of stairs, against the rules that apply to them.

No Revit here. Two rule sets:

- **Byggingarreglugerð 112/2012**, the version in force from 1 May 2026,
  articles 6.4.9 and 6.4.10 (rise, going, 2h + b, slope, nosing).
- **ÍST EN ISO 14122**, 2016, for stairs giving access to machinery:
  part 1 clause 3 for which kind of access a pitch makes it, part 3
  clauses 5 and 6 for stairs and step ladders, part 4 5.2.2.2 for fixed
  ladders.

Only figures and clause numbers are taken from the standards here. The
standards themselves are licensed to AVH for one workstation and are
not part of this extension.

Everything is in metres and degrees; the script converts from feet.

## Results

PASS and FAIL where the model holds what the rule needs. CHECK where
the text recommends rather than requires ("almennt", "recommended",
"should"). NOT_CHECKED where a value could not be read or the stair has
no use set.
"""

import math

from avh_schedules.compat import to_text

PASS = u"pass"
FAIL = u"fail"
CHECK = u"check"
NOT_CHECKED = u"not checked"

PARAMETER = u"AVH_Stair_Use"

# Revit rounds what it reports; a limit is met within half a millimetre.
TOLERANCE = 0.0005
ANGLE_TOLERANCE = 0.05

BR = u"Byggingarreglugerð"
ISO = u"ÍST EN ISO 14122"


class Use(object):
    def __init__(self, code, label, kind):
        self.code = code
        self.label = label
        # "general", "other", "outdoor" or "machinery".
        self.kind = kind


USES = [
    Use(u"almennur", u"Stigi fyrir almenna umferð innanhúss", u"general"),
    Use(u"annar", u"Annar stigi innanhúss, t.d. tæknistigi", u"other"),
    Use(u"uti", u"Útitröppur", u"outdoor"),
    Use(u"To_Machinery", u"Aðgengi að vélbúnaði, ÍST EN ISO 14122",
        u"machinery"),
]

BY_CODE = dict((use.code.lower(), use) for use in USES)

# Codes from 2.33, which checked more than rise and going and so needed
# finer uses. For rise and going they come to these, and stairs already
# set keep working.
OLD_CODES = {
    u"ibud": u"almennur",
    u"fjolbyli_lyfta": u"almennur",
    u"fjolbyli": u"almennur",
    u"fyrirtaeki": u"almennur",
    u"almenningur": u"almennur",
    u"uti_adal": u"uti",
    u"uti_svalir": u"uti",
}

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
    code = fold(value)
    code = OLD_CODES.get(code, code)
    use = BY_CODE.get(code)
    if use is None:
        return None, u"\"{0}\" is not a stair use code".format(
            to_text(value))
    return use, u""


# --------------------------------------------------------------------------
# Plain data the script fills in
# --------------------------------------------------------------------------

class Run(object):
    def __init__(self, element_id, rise=None, risers=None):
        self.element_id = element_id
        self.rise = rise
        self.risers = risers

    def riser(self):
        if self.rise is None or not self.risers:
            return None
        return self.rise / self.risers


class Stair(object):
    def __init__(self, element_id, use_value=None, riser=None, going=None,
                 base=None, top=None, runs=(), nosing=None, plan=None):
        self.element_id = element_id
        self.use_value = use_value
        self.riser = riser
        self.going = going
        self.base = base
        self.top = top
        self.runs = list(runs)
        self.nosing = nosing
        # (min_x, min_y, max_x, max_y), for stacked stairs in a stairwell.
        self.plan = plan


class Result(object):
    def __init__(self, rule, source, status, message):
        self.rule = rule
        self.source = source
        self.status = status
        self.message = message

    def __repr__(self):
        return u"<{0} {1}: {2}>".format(self.status, self.rule, self.message)


def mm(value):
    return u"{0:.0f} mm".format(value * 1000.0)


def slope_of(riser, going):
    """Degrees, or None when there is no usable riser and going."""
    if riser is None or going is None or riser <= 0:
        return None
    if going <= 0:
        return 90.0
    return math.degrees(math.atan2(riser, going))


def within(value, low, high, tolerance=TOLERANCE):
    return low - tolerance <= value <= high + tolerance


def _range(rule, source, value, low, high, text=None):
    status = PASS if within(value, low, high) else FAIL
    return Result(rule, source, status, u"{0}, allowed {1} to {2}".format(
        text or mm(value), mm(low), mm(high)))


# --------------------------------------------------------------------------
# Stairwells: stairs stacked floor on floor
# --------------------------------------------------------------------------

def stairwells(stairs, gap=0.05):
    """Group stairs that continue one another up a stairwell.

    Two stairs belong together when their plans overlap and one starts
    within `gap` of where the other ends. Returns a dict from element id
    to the list of stairs in its group, lowest first.
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
# Byggingarreglugerð 6.4.9 and 6.4.10
# --------------------------------------------------------------------------

def _general(stair, floors, well, results):
    riser, going = stair.riser, stair.going
    source = BR + u" 6.4.9 1. mgr."
    results.append(_range(u"Riser", source, riser, 0.120, 0.180))
    least = 0.260 if floors > 2 else 0.240
    results.append(Result(
        u"Going", source, PASS if going >= least - TOLERANCE else FAIL,
        u"{0}, at least {1} for a stair serving {2} floors".format(
            mm(going), mm(least), floors)))
    slope = slope_of(riser, going)
    results.append(Result(
        u"Slope", source,
        PASS if within(slope, 30.0, 36.0, ANGLE_TOLERANCE) else FAIL,
        u"{0:.1f}°, allowed 30° to 36°".format(slope)))
    results.append(_range(u"2h + b", source, 2 * riser + going, 0.600,
                          0.640))
    _innskot(stair, source, results)
    _same_steps(stair, well, source, results, with_going=True)


def _innskot(stair, source, results):
    if stair.going >= 0.300 - TOLERANCE:
        results.append(Result(u"Nosing", source, PASS,
                              u"going {0}, no nosing needed".format(
                                  mm(stair.going))))
        return
    if stair.nosing is None:
        results.append(Result(u"Nosing", source, NOT_CHECKED,
                              u"the nosing length could not be read"))
        return
    if stair.nosing <= TOLERANCE:
        results.append(Result(
            u"Nosing", source, FAIL,
            u"going {0} is under 300 mm and the steps have no "
            u"nosing".format(mm(stair.going))))
        return
    total = stair.going + stair.nosing
    results.append(Result(
        u"Nosing", source, PASS if total >= 0.300 - TOLERANCE else FAIL,
        u"going {0} plus nosing {1} is {2}, at least 300 mm".format(
            mm(stair.going), mm(stair.nosing), mm(total))))


def _other(stair, results):
    riser, going = stair.riser, stair.going
    slope = slope_of(riser, going)
    if slope < 30.0:
        value, low, high, text, band = 4 * riser + going, 0.940, 0.980, \
            u"4h + b", u"a"
    elif slope < 45.0:
        value, low, high, text, band = 2 * riser + going, 0.600, 0.640, \
            u"2h + b", u"b"
    elif slope < 60.0:
        value, low, high, text, band = riser, 0.200, 0.220, u"riser", u"c"
    elif slope <= 75.0:
        value, low, high, text, band = 4.0 / 3.0 * riser + going, 0.500, \
            0.520, u"4/3h + b", u"d"
    else:
        value, low, high, text, band = riser, 0.310, 0.330, u"riser", u"e"
    results.append(Result(
        u"Rise and going for the slope",
        BR + u" 6.4.9 2. mgr. {0}".format(band),
        PASS if within(value, low, high) else FAIL,
        u"{0:.1f}°: {1} is {2}, allowed {3} to {4}".format(
            slope, text, mm(value), mm(low), mm(high))))


def _outdoor(stair, results):
    riser, going = stair.riser, stair.going
    source = BR + u" 6.4.10 2. mgr."
    results.append(_range(u"Riser", source, riser, 0.120, 0.160))
    results.append(Result(
        u"Going", source, PASS if going >= 0.280 - TOLERANCE else FAIL,
        u"{0}, at least 280 mm".format(mm(going))))
    slope = slope_of(riser, going)
    # "skal almennt vera": generally, so a question, not a failure.
    results.append(Result(
        u"Slope", source,
        PASS if within(slope, 17.0, 30.0, ANGLE_TOLERANCE) else CHECK,
        u"{0:.1f}°, generally 17° to 30°".format(slope)))


# --------------------------------------------------------------------------
# ÍST EN ISO 14122
# --------------------------------------------------------------------------

def access_kind(slope):
    """ISO 14122-1 clause 3: which means of access a pitch makes."""
    if slope <= 20.0:
        return u"ramp"
    if slope <= 45.0:
        return u"stair"
    if slope <= 75.0:
        return u"step ladder"
    return u"fixed ladder"


def _machinery(stair, results):
    riser, going = stair.riser, stair.going
    slope = slope_of(riser, going)
    kind = access_kind(slope)
    results.append(Result(
        u"Kind of access", ISO + u"-1, 3", PASS if kind != u"ramp" else FAIL,
        u"{0:.1f}°: {1}{2}".format(
            slope, kind, u"; a pitch of 20° or less is a ramp, not a stair"
            if kind == u"ramp" else u"")))
    if kind == u"ramp":
        return

    if kind == u"stair":
        source = ISO + u"-3, 5"
        results.append(_range(u"g + 2h", source + u".1", going + 2 * riser,
                              0.600, 0.660))
        results.append(_range(u"Going", source + u".2", going, 0.210, 0.310))
        if stair.nosing is None:
            results.append(Result(u"Overlap", source + u".3", NOT_CHECKED,
                                  u"the nosing length could not be read"))
        else:
            results.append(Result(
                u"Overlap", source + u".3",
                PASS if stair.nosing >= 0.010 - TOLERANCE else FAIL,
                u"{0}, at least 10 mm".format(mm(stair.nosing))))
        results.append(Result(
            u"Pitch", ISO + u"-1, 6.4",
            PASS if within(slope, 30.0, 38.0, ANGLE_TOLERANCE) else CHECK,
            u"{0:.1f}°, recommended 30° to 38°".format(slope)))
        _same_steps(stair, None, source + u".4", results)
        return

    if kind == u"step ladder":
        source = ISO + u"-3, 6"
        results.append(Result(
            u"Going", source + u".1",
            PASS if going >= 0.080 - TOLERANCE else FAIL,
            u"{0}, at least 80 mm".format(mm(going))))
        if slope <= 60.0:
            results.append(_range(u"Rise", source + u".2, table 1", riser,
                                   0.150, 0.200))
        else:
            results.append(_range(u"Rise", source + u".2, table 1", riser,
                                   0.230, 0.300))
            results.append(Result(
                u"Pitch", ISO + u"-1, 6.5", CHECK,
                u"{0:.1f}°: a step ladder steeper than 60° should only be "
                u"chosen for lack of space or for the process".format(slope)))
        _same_steps(stair, None, source + u".5", results, strict=False)
        return

    source = ISO + u"-4, 5.2.2.2"
    results.append(_range(u"Rung spacing", source, riser, 0.225, 0.300))
    _same_steps(stair, None, source, results)


# --------------------------------------------------------------------------
# Same steps
# --------------------------------------------------------------------------

def _same_steps(stair, well, source, results, with_going=False,
                strict=True):
    """The same rise in every flight of the stair, and, with a stairwell,
    the same rise and going on every floor of it."""
    odd = []
    risers = [(run.element_id, run.riser()) for run in stair.runs
              if run.riser() is not None]
    for run_id, value in risers:
        if abs(value - stair.riser) > TOLERANCE:
            odd.append(u"flight {0} has a {1} riser".format(run_id,
                                                            mm(value)))
    others = []
    if well:
        others = [s for s in well if s is not stair and s.riser is not None
                  and s.going is not None]
        for other in others:
            if abs(other.riser - stair.riser) > TOLERANCE or (
                    with_going and abs(other.going - stair.going) > TOLERANCE):
                odd.append(u"stair {0} on another floor has {1} / {2}".format(
                    other.element_id, mm(other.riser), mm(other.going)))
    if not risers and not others:
        return
    if odd:
        results.append(Result(u"Same steps throughout", source,
                              FAIL if strict else CHECK, u"; ".join(odd)))
        return
    where = u"{0} flight(s)".format(len(risers))
    if others:
        where += u" and {0} other floor(s) of the stairwell".format(
            len(others))
    results.append(Result(u"Same steps throughout", source, PASS,
                          u"the same in " + where))


# --------------------------------------------------------------------------

def check(stair, wells):
    """Every result for one stair. Returns (use or None, [Result])."""
    results = []
    use, problem = parse_use(stair.use_value)
    if use is None:
        results.append(Result(
            u"Use", u"", NOT_CHECKED,
            problem or u"no {0} set, so it is not known which rules "
            u"apply".format(PARAMETER)))
        return use, results
    if stair.riser is None or stair.going is None or stair.riser <= 0:
        results.append(Result(u"Riser and going", u"", NOT_CHECKED,
                              u"riser or going could not be read"))
        return use, results
    well = wells.get(stair.element_id, [stair])
    if use.kind == u"general":
        _general(stair, len(well) + 1, well, results)
    elif use.kind == u"other":
        _other(stair, results)
    elif use.kind == u"outdoor":
        _outdoor(stair, results)
    else:
        _machinery(stair, results)
    return use, results


def worst(results):
    for status in (FAIL, CHECK, NOT_CHECKED):
        if any(r.status == status for r in results):
            return status
    return PASS
