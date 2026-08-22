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

# The parent of every imported DWG, DXF and SAT category. Imports appear
# as its subcategories, one per imported file, so there is no CategoryType
# that gathers them the way Annotation gathers annotation categories.
IMPORT_PARENT_CATEGORY = u"OST_ImportObjectStyles"

# Whole families of categories, switched off through the same properties
# the Visibility/Graphics dialog uses for its checkboxes. Each entry is
# (property, CategoryType for the fallback, parent category for the
# fallback, label). Exactly one of the two fallback fields is set: the
# imported categories have a parent and no CategoryType, the other two
# have a CategoryType and no parent.
HIDDEN_CATEGORY_GROUPS = (
    (u"AreAnnotationCategoriesHidden", u"Annotation", u"",
     u"annotation categories"),
    (u"AreAnalyticalModelCategoriesHidden", u"AnalyticalModel", u"",
     u"analytical categories"),
    (u"AreImportCategoriesHidden", u"", IMPORT_PARENT_CATEGORY,
     u"imported categories"),
)


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
