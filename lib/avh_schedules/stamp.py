# -*- coding: utf-8 -*-
"""
The Element ID parameter: what it is called and what goes in it.

Revit 2025 offers no Element Id schedule field, so an element's id is
copied into a parameter of its own, which a schedule can show. AVH already
did this with a Dynamo script for doors. The Stamp Element IDs button does
it for any category the parameter is bound to, and the schedule export
reads it back.

The parameter is an ordinary project or shared parameter and this code
never creates or binds it: that is a change to the model's configuration
and stays a person's decision. It has to exist already.

Pure Python, no Revit.
"""

from .compat import to_text

# Compared after normalising, so "Element ID", "ElementID", "Element_Id"
# and "AVH Element ID" are all the same parameter.
STAMP_NAMES = (u"elementid", u"avhelementid")

MAX_INTEGER = 2147483647


def normalize_name(name):
    text = to_text(name).strip().lower()
    return u"".join(ch for ch in text if ch not in u" _-")


def is_stamp_name(name):
    return normalize_name(name) in STAMP_NAMES


def stamp_value(element_id, storage):
    """What to write for an element id: text for a text parameter, an int
    for an integer parameter, or None when it does not fit.

    Ids from Revit 2024 on can exceed a 32 bit integer, and an Integer
    parameter then cannot hold them. That is reported, never truncated.
    """
    if storage == u"String":
        return u"{0}".format(int(element_id))
    if storage == u"Integer":
        if 0 <= int(element_id) <= MAX_INTEGER:
            return int(element_id)
        return None
    return None


def already_stamped(current, element_id, storage):
    """True when the parameter already holds this element's id."""
    if storage == u"String":
        return to_text(current).strip() == u"{0}".format(int(element_id))
    if storage == u"Integer":
        return current == int(element_id)
    return False
