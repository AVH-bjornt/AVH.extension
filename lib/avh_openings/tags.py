# -*- coding: utf-8 -*-
"""What an opening remembers about why it was made. No Revit.

Pipe Openings writes a tag onto every opening it makes, and Update
Openings reads it back: which pipe, which host, the settings, and where
and how big the opening was. With that, a later run can tell whether
the pipe has moved, changed size or gone.

## The format

Plain `key=value` lines, ASCII only. Everything stored is ASCII anyway
(Revit UniqueIds, a few fixed words, numbers), and a format this plain
cannot be broken by IronPython's json module, which raised on non-ASCII
text in the Clash Detector. Numbers are written with `repr`, which
always uses a decimal point, so a decimal comma locale cannot reach
them. Lengths are in feet, Revit's internal unit.

A tag carries a version. One this code does not know is reported, not
guessed at.
"""

from avh_openings import model
from avh_schedules.compat import to_text

VERSION = u"1"

# Kinds of opening, by how they were made, because each is remade the
# same way.
OPENING = u"opening"      # NewOpening on a wall, floor or roof
SHAFT = u"shaft"          # a shaft opening kept to the host's thickness
ROUND = u"round"          # the AVH_Round_Opening family in a wall
KINDS = (OPENING, SHAFT, ROUND)

TEXT_FIELDS = ("version", "pipe", "link", "pipe_kind", "host", "kind",
               "shape", "mode")
NUMBER_FIELDS = ("amount", "round_to", "cx", "cy", "cz", "width",
                 "height", "diameter", "pipe_diameter")

# Two openings this close in position and size are the same opening.
SAME_FT = 0.5 / model.MM_PER_FOOT


class TagError(Exception):
    """A tag that cannot be read. The message says why."""


class Tag(object):
    def __init__(self, pipe, link, pipe_kind, host, kind, settings,
                 center, width=0.0, height=0.0, diameter=0.0,
                 pipe_diameter=0.0, shape=None):
        self.version = VERSION
        self.pipe = to_text(pipe)               # pipe or conduit UniqueId
        self.link = to_text(link or u"")        # link instance UniqueId
        self.pipe_kind = to_text(pipe_kind)     # u"Pipe" or u"Conduit"
        self.host = to_text(host)               # host UniqueId
        self.kind = to_text(kind)
        self.shape = to_text(shape or settings.shape)
        self.mode = to_text(settings.mode)
        self.amount = float(settings.amount)
        self.round_to = float(settings.round_to)
        self.cx, self.cy, self.cz = [float(v) for v in center]
        self.width = float(width)
        self.height = float(height)
        self.diameter = float(diameter)
        self.pipe_diameter = float(pipe_diameter)

    @property
    def center(self):
        return (self.cx, self.cy, self.cz)

    def settings(self):
        """The Settings the opening was made with. Links do not matter
        once the pipe is known, so they are left on."""
        return model.Settings(self.shape, self.mode, self.amount,
                              self.round_to, True)

    def size_text(self):
        if self.shape == model.CIRCULAR:
            return u"\u00f8{0}".format(model.format_number(
                self.diameter * model.MM_PER_FOOT))
        return u"{0} \u00d7 {1}".format(
            model.format_number(self.width * model.MM_PER_FOOT),
            model.format_number(self.height * model.MM_PER_FOOT))

    def encode(self):
        """The tag as text. Raises TagError if anything in it is not
        plain ASCII, rather than storing something that will not read
        back."""
        lines = []
        for name in TEXT_FIELDS:
            value = getattr(self, name)
            lines.append(u"{0}={1}".format(name, value))
        for name in NUMBER_FIELDS:
            lines.append(u"{0}={1}".format(name, repr(getattr(self, name))))
        text = u"\n".join(lines)
        for line in lines:
            if line.count(u"=") < 1 or any(ord(c) > 126 or ord(c) < 32
                                            for c in line):
                raise TagError(u"cannot store {0!r}".format(line))
        return text


def from_opening(opening, settings, pipe, link, pipe_kind, host, kind):
    """The tag for an opening just made from `opening` (a model.Opening)
    for that pipe in that host."""
    if kind not in KINDS:
        raise TagError(u"unknown kind of opening: {0}".format(kind))
    return Tag(pipe, link, pipe_kind, host, kind, settings,
               opening.center, width=opening.width, height=opening.height,
               diameter=opening.diameter,
               pipe_diameter=opening.pipe_diameter, shape=opening.shape)


def decode(text):
    """A Tag from stored text. Raises TagError, saying why."""
    values = {}
    for line in to_text(text).split(u"\n"):
        line = line.strip()
        if not line:
            continue
        if u"=" not in line:
            raise TagError(u"a line with no value: {0}".format(line))
        key, value = line.split(u"=", 1)
        values[key.strip()] = value.strip()
    version = values.get("version")
    if version != VERSION:
        raise TagError(u"tag version {0} is not one this version of the "
                       u"tool reads".format(version or u"missing"))
    for name in TEXT_FIELDS + NUMBER_FIELDS:
        if name not in values:
            raise TagError(u"the tag has no {0}".format(name))
    numbers = {}
    for name in NUMBER_FIELDS:
        try:
            numbers[name] = float(values[name])
        except ValueError:
            raise TagError(u"{0} is not a number: {1}".format(
                name, values[name]))
    if values["kind"] not in KINDS:
        raise TagError(u"unknown kind of opening: {0}".format(
            values["kind"]))
    if values["shape"] not in model.SHAPES or \
            values["mode"] not in model.MODES:
        raise TagError(u"unknown shape or size setting")
    settings = model.Settings(values["shape"], values["mode"],
                              numbers["amount"], numbers["round_to"])
    return Tag(values["pipe"], values["link"], values["pipe_kind"],
               values["host"], values["kind"], settings,
               (numbers["cx"], numbers["cy"], numbers["cz"]),
               width=numbers["width"], height=numbers["height"],
               diameter=numbers["diameter"],
               pipe_diameter=numbers["pipe_diameter"],
               shape=values["shape"])


def same(tag, opening):
    """Whether a freshly sized opening is the one the tag describes:
    same shape, centre within half a millimetre in the host's face, and
    the same size within half a millimetre.

    The centre is compared in the face only. Its depth is the middle of
    the axis inside the host, which moves when a pipe tilts without its
    hole needing to, and the size already reflects any tilt that does
    matter."""
    if opening.shape != tag.shape:
        return False
    frame = opening.frame
    then = frame.local(tag.center)
    now = frame.local(opening.center)
    if abs(then[0] - now[0]) > SAME_FT or abs(then[1] - now[1]) > SAME_FT:
        return False
    if opening.shape == model.CIRCULAR:
        return abs(opening.diameter - tag.diameter) <= SAME_FT
    return (abs(opening.width - tag.width) <= SAME_FT and
            abs(opening.height - tag.height) <= SAME_FT)
