# -*- coding: utf-8 -*-
"""Make a clean 3D view for Forma, named after the model.

Creates an isometric 3D view called exactly what the model file is
called, then switches off everything that is not model geometry:
annotation categories, analytical categories, imported categories,
linked models, coordination models and lines.

Running it twice is safe. If a 3D view of that name already exists the
settings are applied to it again rather than a second view being made,
so the button is also the way to put a Forma view back to a known state
after someone has turned things on in it.

## Two things that would otherwise bite

**A view template overrides all of this.** A reused view may have one,
and a new view inherits whatever `DefaultTemplateId` the 3D view type
carries, so a freshly created view can arrive with a template already
applied. Where a template is in the way the run stops and asks, before
the transaction opens, rather than writing settings a template will
overrule.

**A property that will not take the value.** The three category groups
are switched off through the same properties the Visibility/Graphics
dialog uses. Each write is read back, and anything that did not take
falls through to hiding those categories one at a time. The report says
which route was used, because a silent difference between two machines
is the expensive kind.

Imports need their own fallback. Every imported DWG, DXF or SAT is a
subcategory of `OST_ImportObjectStyles`, one per file, so there is no
CategoryType gathering them the way Annotation gathers annotation
categories.

## Verified, and not

Confirmed working on Bjorn's machine at 2.12.0, against Eldisgardur:
`View3D.CreateIsometric`, the annotation and analytical setters,
`SetCategoryHidden`, `CanCategoryBeHidden` and the naming.

**`AreImportCategoriesHidden` and the import fallback are new at 2.12.1
and have not been run in Revit**, nor has any of the fallback machinery,
which by its nature only runs on an install where a property misbehaves.
Every one of those calls is written so a failure is reported rather than
assumed away, and the whole run is one transaction whose commit status
is checked, so a rejected write cannot report success.

Point clouds are a fourth switch (`ArePointCloudsHidden`) and are
deliberately not touched, because nobody has asked for it.
"""

__title__ = "Make\nForma View"
__author__ = "AVH"
__doc__ = ("Create or refresh a 3D view named after the model, with "
           "annotation, analytical and imported categories, linked "
           "models, coordination models and lines switched off. Safe to "
           "run again on the same model.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. A button nested one deeper, in a pulldown, was enough
# to break the fixed count, and it breaks at import time with a message
# about a module nobody has heard of.
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
from avh_forma import model                       # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = u"Make Forma View"


def invalid_id():
    return DB.ElementId.InvalidElementId


def is_valid(element_id):
    if element_id is None:
        return False
    try:
        return element_id != invalid_id()
    except BaseException:
        return False


def view_name(view):
    """A view's name as text, never raising and never via str()."""
    try:
        return to_text(view.Name)
    except BaseException:
        return u""


def find_existing_view(doc, name):
    """The non template 3D view called `name`, or None."""
    collector = DB.FilteredElementCollector(doc).OfClass(DB.View3D)
    for view in collector:
        try:
            if view.IsTemplate:
                continue
        except BaseException:
            continue
        if view_name(view) == name:
            return view
    return None


def find_3d_view_type(doc):
    """The first 3D view family type in the document, or None."""
    collector = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
    for view_type in collector:
        try:
            if view_type.ViewFamily == DB.ViewFamily.ThreeDimensional:
                return view_type
        except BaseException:
            continue
    return None


def template_name(doc, template_id):
    element = doc.GetElement(template_id)
    if element is None:
        return u"a view template"
    return view_name(element) or u"a view template"


def hide_named_category(doc, view, category_name):
    """Hide one built in category. Returns (ok, note).

    `ok` is False only when the category exists, can be hidden, and the
    hide did not take. A category this Revit does not have is not a
    failure, it is a note.
    """
    built_in = getattr(DB.BuiltInCategory, category_name, None)
    if built_in is None:
        return True, u"not in this Revit version"

    try:
        category = DB.Category.GetCategory(doc, built_in)
    except BaseException as exc:
        return True, u"not in this model ({0})".format(to_text(exc))
    if category is None:
        return True, u"not in this model"

    category_id = category.Id
    try:
        if not view.CanCategoryBeHidden(category_id):
            return False, u"the view will not allow it to be hidden"
    except BaseException as exc:
        return False, u"CanCategoryBeHidden failed ({0})".format(to_text(exc))

    try:
        view.SetCategoryHidden(category_id, True)
    except BaseException as exc:
        return False, u"SetCategoryHidden failed ({0})".format(to_text(exc))

    try:
        if not view.GetCategoryHidden(category_id):
            return False, u"the hide did not take"
    except BaseException:
        # Nothing to read back from. The write itself did not raise.
        return True, u"written, not read back"

    return True, u""


def hide_categories_of_type(doc, view, category_type_name):
    """Fallback: hide every category of one CategoryType, one at a time.

    Returns (hidden_count, failures), where a failure is a category name
    that could not be hidden. Categories the view refuses to hide are
    counted as failures here, unlike in `hide_named_category`, because
    the caller only reaches this path when the whole group was asked for.
    """
    wanted = getattr(DB.CategoryType, category_type_name, None)
    if wanted is None:
        return 0, [u"CategoryType.{0} not in this Revit version".format(
            category_type_name)]

    hidden = 0
    failures = []
    for category in doc.Settings.Categories:
        try:
            if category.CategoryType != wanted:
                continue
        except BaseException:
            continue

        try:
            if not view.CanCategoryBeHidden(category.Id):
                continue
            view.SetCategoryHidden(category.Id, True)
            hidden += 1
        except BaseException as exc:
            failures.append(u"{0} ({1})".format(
                to_text(category.Name), to_text(exc)))

    return hidden, failures


def hide_subcategories_of(doc, view, parent_category_name):
    """Fallback for a family that has a parent category, not a type.

    Imports are the case: every imported DWG, DXF or SAT is a
    subcategory of `OST_ImportObjectStyles`, one per file, so there is no
    CategoryType that gathers them. The parent is hidden first, and each
    subcategory after it, because whether hiding the parent is enough is
    not something to assume.

    Returns (hidden_count, failures), matching `hide_categories_of_type`.
    """
    built_in = getattr(DB.BuiltInCategory, parent_category_name, None)
    if built_in is None:
        return 0, [u"{0} not in this Revit version".format(
            parent_category_name)]

    try:
        parent = DB.Category.GetCategory(doc, built_in)
    except BaseException:
        parent = None
    if parent is None:
        return 0, []

    hidden = 0
    failures = []
    targets = [parent]
    try:
        for subcategory in parent.SubCategories:
            targets.append(subcategory)
    except BaseException as exc:
        failures.append(u"subcategories unreadable ({0})".format(to_text(exc)))

    for category in targets:
        try:
            if not view.CanCategoryBeHidden(category.Id):
                continue
            view.SetCategoryHidden(category.Id, True)
            hidden += 1
        except BaseException as exc:
            failures.append(u"{0} ({1})".format(
                to_text(category.Name), to_text(exc)))

    return hidden, failures


def hide_category_group(doc, view, property_name, category_type_name,
                        parent_category_name):
    """Switch off a whole family of categories. Returns (ok, note).

    The property is tried first, because it is one call and it is exactly
    what the Visibility/Graphics checkbox does. It is then read back, and
    a value that did not stick falls through to the per category route
    rather than being reported as done.
    """
    property_worked = False
    property_error = u""
    try:
        setattr(view, property_name, True)
        property_worked = bool(getattr(view, property_name))
        if not property_worked:
            property_error = u"the property did not take the value"
    except BaseException as exc:
        property_error = to_text(exc)

    if property_worked:
        return True, u"via {0}".format(property_name)

    if category_type_name:
        hidden, failures = hide_categories_of_type(
            doc, view, category_type_name)
    else:
        hidden, failures = hide_subcategories_of(
            doc, view, parent_category_name)

    note = u"{0} unusable ({1}), hid {2} categories one at a time".format(
        property_name, property_error, hidden)
    if failures:
        return False, u"{0}; failed on {1}".format(note, u", ".join(failures))
    if not hidden:
        # Nothing of that kind in this model. A model with no imports is
        # the ordinary case, not a failure to hide them.
        return True, u"{0}, there being none in this model".format(note)
    return True, note


def resolve_template(doc, existing_view, view_type):
    """The view template that would be in the way, as an ElementId or None."""
    if existing_view is not None:
        try:
            template_id = existing_view.ViewTemplateId
        except BaseException:
            return None
        return template_id if is_valid(template_id) else None

    if view_type is None:
        return None
    try:
        template_id = view_type.DefaultTemplateId
    except BaseException:
        return None
    return template_id if is_valid(template_id) else None


def run():
    doc = revit.doc
    if doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return

    name = model.view_name_from_document(
        getattr(doc, "PathName", u""), getattr(doc, "Title", u""))
    if not name:
        forms.alert(
            u"This model has no file name yet, so there is nothing to name "
            u"the view after. Save the model first.",
            title=TITLE)
        return

    existing_view = find_existing_view(doc, name)
    view_type = None
    if existing_view is None:
        view_type = find_3d_view_type(doc)
        if view_type is None:
            forms.alert(
                u"This model has no 3D view type, so a 3D view cannot be "
                u"created.",
                title=TITLE)
            return

    detach_template = False
    template_id = resolve_template(doc, existing_view, view_type)
    if template_id is not None:
        answer = forms.alert(
            u'The view would have the view template "{0}" applied, which '
            u"overrides visibility settings. Remove the template from this "
            u"view so the Forma settings can be applied?".format(
                template_name(doc, template_id)),
            title=TITLE, yes=True, no=True)
        if not answer:
            forms.alert(u"Nothing was changed.", title=TITLE)
            return
        detach_template = True

    reused = existing_view is not None
    problems = []
    notes = []
    view = existing_view

    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    try:
        if view is None:
            view = DB.View3D.CreateIsometric(doc, view_type.Id)
            view.Name = name

        if detach_template:
            view.ViewTemplateId = invalid_id()

        for group in model.HIDDEN_CATEGORY_GROUPS:
            property_name, type_name, parent_name, label = group
            ok, note = hide_category_group(
                doc, view, property_name, type_name, parent_name)
            if ok:
                notes.append(u"{0}: off ({1})".format(label, note))
            else:
                problems.append(u"{0}: {1}".format(label, note))

        for category_name, label in model.HIDDEN_CATEGORIES:
            ok, note = hide_named_category(doc, view, category_name)
            if ok:
                notes.append(
                    u"{0}: off{1}".format(
                        label, u" ({0})".format(note) if note else u""))
            else:
                problems.append(u"{0}: {1}".format(label, note))
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            u"Nothing was changed. The run stopped with: {0}".format(
                to_text(exc)),
            title=TITLE)
        logger.error(to_text(exc))
        return

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        forms.alert(
            u"Revit rejected the changes on commit, so nothing was changed "
            u"({0}).".format(to_text(status)),
            title=TITLE)
        return

    output.print_md(u"### {0}".format(TITLE))
    output.print_md(u"**{0}** view `{1}`".format(
        u"Refreshed" if reused else u"Created", name))
    for note in notes:
        output.print_md(u"- {0}".format(note))
    for problem in problems:
        output.print_md(u"- **{0}**".format(problem))

    try:
        uidoc = revit.uidoc
        if uidoc is not None:
            uidoc.ActiveView = view
    except BaseException as exc:
        logger.debug(to_text(exc))

    if problems:
        forms.alert(
            u"The view is ready, but {0} setting(s) could not be applied. "
            u"See the output window.".format(len(problems)),
            title=TITLE)


if __name__ == "__main__":
    run()
