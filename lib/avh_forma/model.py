# -*- coding: utf-8 -*-
"""What the Forma view is called and what gets switched off in it.

No Revit here on purpose. Everything in this module is a string or a
tuple, so the naming rules can be tested without opening Revit, which is
where every other rule in this repository has ended up eventually.
"""

import os

from avh_schedules.compat import to_text

# Revit rejects these in an element name. The message it shows is
# "Names cannot contain any of the following characters: \\ : { } [ ] | ;
# < > ? ` ~". A Windows file name can legally contain most of them, so a
# name taken from the file name has to be cleaned before it is written.
INVALID_NAME_CHARS = u"\\:{}[]|;<>?`~"

# Hidden by name rather than by category type. Coordination models are
# not RVT links and are not covered by hiding the RVT Links category, so
# they are named separately.
HIDDEN_CATEGORIES = (
    (u"OST_RvtLinks", u"linked models"),
    (u"OST_Coordination_Model", u"coordination models"),
    (u"OST_Lines", u"lines"),
)

# The "Imports in Families" row on the Imported Categories tab, and the
# reason 2.24.0 exists.
#
# 2.12.1 assumed every imported file was a subcategory of this one, and
# switched the whole branch off. Revit does not work that way. Each
# imported file is its own category, and this one carries geometry that
# was imported *into a family*: sanitary ware, ironmongery and most
# manufacturer content is built that way. Hiding it takes that content
# out of the view and out of the Forma export, with nothing on screen to
# say anything went missing. It cost Bjoern an afternoon on 18 Sep 2026.
#
# It is never hidden. Imported files are found through the elements that
# use them instead, which cannot make the same mistake.
IMPORTS_IN_FAMILIES = u"OST_ImportObjectStyles"

# Whole families of categories, switched off through the same properties
# the Visibility/Graphics dialog uses for its checkboxes. Each entry is
# (property, CategoryType for the per category fallback, label).
#
# Imports are deliberately not here. There is no property that hides
# imported files without also hiding Imports in Families, which is the
# whole of the 2.12.1 bug, so they have their own route.
HIDDEN_CATEGORY_GROUPS = (
    (u"AreAnnotationCategoriesHidden", u"Annotation",
     u"annotation categories"),
    (u"AreAnalyticalModelCategoriesHidden", u"AnalyticalModel",
     u"analytical categories"),
)


def import_categories_to_hide(category_ids, protected_id):
    """Which category ids to switch off for imported files.

    `category_ids` is the category of every imported or linked CAD
    instance in the model, duplicates and all. `protected_id` is the
    Imports in Families category, which is never returned even if an
    element claims it.

    Order is preserved so two runs report the same way, and duplicates
    are dropped so a model with forty imports of one file is one write.
    """
    keep = []
    seen = set()
    for category_id in category_ids:
        if category_id is None:
            continue
        if protected_id is not None and category_id == protected_id:
            continue
        if category_id in seen:
            continue
        seen.add(category_id)
        keep.append(category_id)
    return keep


def sanitize_view_name(name):
    """Make a Revit legal view name out of an arbitrary string.

    Illegal characters become an underscore rather than disappearing, so
    two models whose names differ only in punctuation do not collapse
    onto one view name.
    """
    text = to_text(name)
    cleaned = []
    for character in text:
        if character in INVALID_NAME_CHARS or ord(character) < 32:
            cleaned.append(u"_")
        else:
            cleaned.append(character)
    return u"".join(cleaned).strip()


def base_name(path):
    """The last component of a path, splitting on both separators.

    `os.path.basename` splits on backslash only when it is running on
    Windows, so a Revit path tested on any other machine would come back
    whole and then have its backslashes turned into underscores by
    `sanitize_view_name`. Doing it by hand keeps the rule the same
    wherever it runs, which is the only way it can be tested at all.
    """
    text = to_text(path)
    for separator in (u"\\", u"/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text


def view_name_from_document(path_name, title=None):
    """The model's own name, as the view should be called.

    `path_name` is `Document.PathName`, empty for a model that has never
    been saved. `title` is `Document.Title`, which carries the extension
    on some Revit versions and not on others, so it is stripped either
    way. Returns an empty string if neither yields anything, which the
    caller has to treat as a stop rather than inventing a name.
    """
    base = u""
    path = to_text(path_name)
    if path:
        base = base_name(path)
    if not base:
        base = to_text(title)
    if not base:
        return u""
    root, extension = os.path.splitext(base)
    if extension.lower() in (u".rvt", u".rte", u".rfa"):
        base = root
    return sanitize_view_name(base)
