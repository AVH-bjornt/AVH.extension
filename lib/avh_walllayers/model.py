# -*- coding: utf-8 -*-
"""The arithmetic of splitting a wall into its layers. No Revit.

Distances are in Revit's internal unit, feet, and measured across the
wall from its centre, **positive towards the exterior** (the direction
of `Wall.Orientation`). Layers are listed exterior first, the order
`CompoundStructure.GetLayers()` returns them in.

A wall of 12.5 / 95 / 12.5 mm is 120 mm thick, so its exterior face is
at +60 mm and the layer centres sit at +53.75, 0 and -53.75 mm.

Where the location line sits is the one thing that cannot be read off
the wall's curve: the curve *is* the location line, and the location
line can be any of six places across the wall. `location_offset` turns
the `WALL_KEY_REF_PARAM` value into a distance, and each new wall is
put at `layer centre - location offset` from the original curve.
"""

from avh_schedules.compat import to_text

MM_PER_FOOT = 304.8

# Thinner than this is a membrane, not something to model as a wall.
# Revit gives membranes a width of exactly zero.
MIN_WIDTH = 0.01 / MM_PER_FOOT

# Two widths this close are the same layer when matching wall types.
SAME_WIDTH = 0.001 / MM_PER_FOOT

# WALL_KEY_REF_PARAM values, the WallLocationLine enumeration. From the
# API documentation; the enumeration has been stable since Revit 2011.
CENTRE = 0
CORE_CENTRE = 1
FINISH_EXTERIOR = 2
FINISH_INTERIOR = 3
CORE_EXTERIOR = 4
CORE_INTERIOR = 5

LOCATION_WORDS = {
    CENTRE: u"wall centreline",
    CORE_CENTRE: u"core centreline",
    FINISH_EXTERIOR: u"finish face: exterior",
    FINISH_INTERIOR: u"finish face: interior",
    CORE_EXTERIOR: u"core face: exterior",
    CORE_INTERIOR: u"core face: interior",
}

# MaterialFunctionAssignment names, as they come out of the enum, to
# the words Revit's Edit Assembly dialog uses.
FUNCTION_WORDS = {
    u"Structure": u"Structure [1]",
    u"Substrate": u"Substrate [2]",
    u"Insulation": u"Thermal/Air Layer [3]",
    u"Finish1": u"Finish 1 [4]",
    u"Finish2": u"Finish 2 [5]",
    u"Membrane": u"Membrane Layer",
    u"StructuralDeck": u"Structural Deck [1]",
}


class Layer(object):
    """One layer of a wall's structure, as plain data."""

    def __init__(self, index, width, material_key, material_name,
                 function):
        self.index = index                  # 0 is the exterior layer
        self.width = float(width)           # feet
        self.material_key = material_key    # the material's id, as int
        self.material_name = to_text(material_name)
        self.function = to_text(function)   # e.g. u"Finish1"

    @property
    def is_membrane(self):
        return self.width < MIN_WIDTH


class Refusal(Exception):
    """A wall that cannot be split, and the reason in words."""


def total(widths):
    return float(sum(widths))


def layer_centres(widths):
    """Each layer's centre, from the wall centre, exterior positive."""
    face = total(widths) / 2.0
    centres = []
    for width in widths:
        centres.append(face - width / 2.0)
        face -= width
    return centres


def core_faces(widths, first_core, last_core):
    """(exterior, interior) faces of the core, from the wall centre.
    Raises Refusal when the structure has no core."""
    if first_core is None or last_core is None or first_core < 0 or \
            last_core < first_core or last_core >= len(widths):
        raise Refusal(u"its structure has no core, and its location "
                      u"line is measured from the core")
    exterior = total(widths) / 2.0 - total(widths[:first_core])
    interior = exterior - total(widths[first_core:last_core + 1])
    return exterior, interior


def location_offset(widths, first_core, last_core, key):
    """Where the location line sits, from the wall centre."""
    half = total(widths) / 2.0
    if key == CENTRE:
        return 0.0
    if key == FINISH_EXTERIOR:
        return half
    if key == FINISH_INTERIOR:
        return -half
    if key in (CORE_CENTRE, CORE_EXTERIOR, CORE_INTERIOR):
        exterior, interior = core_faces(widths, first_core, last_core)
        if key == CORE_EXTERIOR:
            return exterior
        if key == CORE_INTERIOR:
            return interior
        return (exterior + interior) / 2.0
    raise Refusal(u"its location line is {0}, which is not one of the "
                  u"six Revit has".format(to_text(key)))


def shifts(widths, first_core, last_core, key):
    """How far to move from the original curve to each layer's centre,
    along Orientation. One number per layer, exterior first."""
    offset = location_offset(widths, first_core, last_core, key)
    return [centre - offset for centre in layer_centres(widths)]


def mm(feet):
    return feet * MM_PER_FOOT


def format_mm(feet):
    """0.041 ft -> u"12.5". Up to two decimals, none when whole. For
    display and type names only; never parsed back."""
    value = round(mm(feet), 2)
    if abs(value - round(value)) < 1e-9:
        return u"{0}".format(int(round(value)))
    return (u"{0:.2f}".format(value)).rstrip(u"0").rstrip(u".")


def function_words(function):
    function = to_text(function)
    return FUNCTION_WORDS.get(function, function or u"-")


def type_name(layer):
    """The base name for a one layer wall type: u"Gypsum 12.5 mm"."""
    material = layer.material_name.strip() or u"No material"
    return u"{0} {1} mm".format(material, format_mm(layer.width))


def unique_name(base, taken):
    """base, or base (2), (3) ... whichever is not in taken."""
    if base not in taken:
        return base
    number = 2
    while u"{0} ({1})".format(base, number) in taken:
        number += 1
    return u"{0} ({1})".format(base, number)


def type_key(layer):
    """What makes two one layer wall types interchangeable."""
    return (int(round(layer.width / SAME_WIDTH)), layer.material_key,
            layer.function)


def wall_layers(layers):
    """(to make, membranes): the layers that become walls, and the ones
    too thin to. Raises Refusal when fewer than two have a thickness,
    because then there is nothing to split."""
    made = [layer for layer in layers if not layer.is_membrane]
    thin = [layer for layer in layers if layer.is_membrane]
    if len(made) < 2:
        raise Refusal(u"it has only one layer with a thickness")
    return made, thin
