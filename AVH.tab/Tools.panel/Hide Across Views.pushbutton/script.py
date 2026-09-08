# -*- coding: utf-8 -*-
"""Hide or unhide the selection in views you pick from a list.

Select something, click, pick the views, pick Hide or Unhide. The
selected elements are hidden in every view you picked, in one
transaction, so one undo reverses the lot.

Shift click works on the categories of the selection instead of the
elements.

## Permanent, because temporary cannot cross views

Temporary Hide/Isolate belongs to one view and evaporates. So this is
the permanent hide, the same thing as right click, Hide in View, applied
to every view you picked. It writes to the model.

## The element route and the category route are not the same job

Hiding an element is stored on the view and no view template controls
it, so the element route works on every view you pick.

Category visibility is usually owned by the view template. Writing a
category hide onto a view whose template owns it is accepted by Revit
and changes nothing anyone can see, so those views are separated out and
reported, pointing at Hide in Template, which is the button that can
actually reach them.

## Where this is the wrong tool

If the rule is "these elements should never appear in this family of
views", a view filter on a parameter is the maintainable answer. Per
view hides are invisible in the browser, do not travel with a new view,
and leave the next person unable to tell why something vanished. This is
for one off cleanup, not for a standing rule.

## Unverified

Not yet run in Revit: View.HideElements, UnhideElements,
Element.CanBeHidden, Element.IsHidden, View.SetCategoryHidden,
GetCategoryHidden, CanCategoryBeHidden, View.ViewType and
GetNonControlledTemplateParameterIds. The run is one transaction whose
commit status is checked and which carries a failure preprocessor, so a
rejected write cannot report success.
"""

__title__ = "Hide Across\nViews"
__author__ = "AVH"
__doc__ = ("Hide or unhide the selected elements in views you pick. "
           "Shift click to work on their categories instead.")

import os
import sys

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
from avh_visibility import model                  # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

output = script.get_output()
logger = script.get_logger()

TITLE = "Hide Across Views"

# View kinds that cannot take a hide at all. Enum names are English in
# the API whatever the interface language, so matching on the name is
# safe where matching on a displayed string would not be.
EXCLUDED_TYPES = set([
    "Schedule", "ColumnSchedule", "PanelSchedule", "DrawingSheet",
    "Legend", "ProjectBrowser", "SystemBrowser", "Internal", "Undefined",
    "Report", "CostReport", "LoadsReport", "PresureLossReport",
])

KIND_PARAMETERS = (
    (model.MODEL, "VIS_GRAPHICS_MODEL"),
    (model.ANNOTATION, "VIS_GRAPHICS_ANNOTATION"),
)


def id_collection(ids):
    """A .NET ICollection<ElementId>, which is what the API wants."""
    from System.Collections.Generic import List
    collection = List[DB.ElementId]()
    for element_id in ids:
        collection.Add(element_id)
    return collection


def parameter_id(name):
    builtin = getattr(DB.BuiltInParameter, name, None)
    if builtin is None:
        return None
    try:
        return DB.ElementId(builtin)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


PARAMETER_IDS = {}
for _kind, _name in KIND_PARAMETERS:
    PARAMETER_IDS[_kind] = parameter_id(_name)


def category_kind(category):
    try:
        kind = category.CategoryType
    except BaseException:
        return model.OTHER
    if kind == getattr(DB.CategoryType, "Model", None):
        return model.MODEL
    if kind == getattr(DB.CategoryType, "Annotation", None):
        return model.ANNOTATION
    return model.OTHER


def why_no_marker(notes):
    """The reason the marker swap did not happen, if there is one.

    Four things can stop it and every one of them used to return None
    without a word, which is how the same dialog appeared twice with two
    different causes behind it. Whichever line comes back names the step.
    """
    reasons = [note for note in sorted(notes)
               if u"ViewType" in note or u"Category" in note]
    if not reasons:
        return ""
    return "\n\n" + "\n".join(reasons)


def marker_category(doc, element, notes):
    """The annotation category governing a view element's marker.

    Returns (Category, source_name) or (None, None). Resolved through
    BuiltInCategory rather than a category name, because category names
    follow the interface language and this model is not in English.
    """
    view_type = getattr(element, "ViewType", None)
    if view_type is None:
        notes.add(u"The selected element has no ViewType, so its marker "
                  u"category could not be worked out.")
        return None, None

    seen = model.view_type_name(view_type)
    name = model.marker_category_name(view_type)
    if not name:
        notes.add(u"ViewType {0} has no marker category in the table. "
                  u"Section, Elevation and Detail are the ones "
                  u"listed.".format(seen))
        return None, None

    builtin = getattr(DB.BuiltInCategory, name, None)
    if builtin is None:
        notes.add(u"BuiltInCategory {0} is not available in this Revit "
                  u"(ViewType {1}).".format(name, seen))
        return None, None

    try:
        category = DB.Category.GetCategory(doc, builtin)
    except BaseException as exc:
        notes.add(u"Category.GetCategory failed for {0}: {1}".format(
            name, to_text(exc)))
        return None, None

    if category is None:
        notes.add(u"This model has no {0} category, so ViewType {1} "
                  u"cannot be redirected.".format(name, seen))
        return None, None

    return category, u"{0} ({1})".format(
        to_text(getattr(element, "Name", u"")), seen)


def selection(doc, uidoc, notes):
    """(elements, categories, category_ids, substitutions).

    The real `Category.Id` is carried through rather than rebuilt from
    its number later. Reconstructing an ElementId from a value is a round
    trip that can fail on its own, and there is no reason to take it when
    the original object is right here.

    Duplicated from Hide in Template on purpose. Folding the two into a
    shared reader means both buttons move at once, and neither has been
    run in Revit yet. Fold them once both are confirmed.
    """
    try:
        selected = list(uidoc.Selection.GetElementIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        return [], [], {}, []

    elements = []
    entries = []
    category_ids = {}
    substitutions = []
    for element_id in selected:
        try:
            element = doc.GetElement(element_id)
        except BaseException:
            continue
        if element is None:
            continue
        elements.append((model.key_of(element_id), element_id, element))
        category = getattr(element, "Category", None)
        if category is None:
            continue

        # A section, elevation or callout marker selects the view itself,
        # whose category is Views and which nothing can switch off. Swap
        # in the annotation category that actually governs the marker,
        # and record the swap so the dialog can show it.
        if category_kind(category) == model.OTHER:
            marker, source = marker_category(doc, element, notes)
            if marker is not None:
                category = marker
                substitutions.append((source, to_text(marker.Name)))

        try:
            entries.append((category.Id, category.Name,
                            category_kind(category)))
            category_ids[model.key_of(category.Id)] = category.Id
        except BaseException as exc:
            logger.debug(to_text(exc))

    return (elements, model.merge_categories(entries), category_ids,
            substitutions)


def template_controls(doc, view, cache):
    """The kinds this view's template takes over, cached per template."""
    try:
        template_id = view.ViewTemplateId
    except BaseException:
        return set()
    marker = model.key_of(template_id)
    if marker in cache:
        return cache[marker]

    controls = set()
    template = None
    try:
        template = doc.GetElement(template_id)
    except BaseException as exc:
        logger.debug(to_text(exc))
    if template is not None:
        try:
            not_controlled = set(
                model.key_of(pid)
                for pid in template.GetNonControlledTemplateParameterIds())
            for kind, _name in KIND_PARAMETERS:
                parameter = PARAMETER_IDS.get(kind)
                if parameter is None:
                    continue
                if model.key_of(parameter) not in not_controlled:
                    controls.add(kind)
        except BaseException as exc:
            logger.debug(to_text(exc))

    cache[marker] = controls
    return controls


def gather_views(doc, active_id):
    """Every view that can take a hide, described for the picker.

    Deliberately light. Whether a given element can be hidden in a given
    view is one API call per element per view, and asking that of two
    hundred views before the picker would make the button feel broken.
    It is asked of the views actually picked, in `enrich`.
    """
    views = []
    cache = {}
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        found = list(collector)
    except BaseException as exc:
        logger.error(to_text(exc))
        return []

    for view in found:
        try:
            if view.IsTemplate:
                continue
            vtype = to_text(view.ViewType)
        except BaseException:
            continue
        if vtype in EXCLUDED_TYPES:
            continue
        marker = model.key_of(view.Id)
        views.append({
            "key": marker,
            "name": to_text(view.Name),
            "vtype": vtype,
            "active": marker == model.key_of(active_id),
            "template_controls": template_controls(doc, view, cache),
            "blocked": set(),
            "eligible": set(),
            "hidden": set(),
            "view": view,
        })
    return model.order_views(views)


def enrich_for_elements(views, elements):
    """Ask Revit, per view, which of these elements it can hide."""
    for entry in views:
        view = entry["view"]
        eligible = set()
        hidden = set()
        for key, element_id, element in elements:
            try:
                if not element.CanBeHidden(view):
                    continue
            except BaseException as exc:
                logger.debug(to_text(exc))
                continue
            eligible.add(key)
            try:
                if element.IsHidden(view):
                    hidden.add(key)
            except BaseException as exc:
                logger.debug(to_text(exc))
        entry["eligible"] = eligible
        entry["hidden"] = hidden
    return views


def enrich_for_categories(views, categories, category_ids, notes):
    """Ask Revit, per view, which of these categories it refuses.

    Only a clear no blocks. If the question itself cannot be asked, the
    reason is recorded and the category goes through, because a guard
    that cannot run must not silently refuse everything: that turns a
    broken call into "None of the views you picked can take that", which
    is indistinguishable from Revit having refused.

    Letting it through is safe. The write sits in one transaction with a
    failure preprocessor and a checked commit, so if Revit does refuse,
    the whole run rolls back and says so in Revit's own words.
    """
    for entry in views:
        view = entry["view"]
        blocked = set()
        for category in model.supported(categories):
            category_id = category_ids.get(category["key"])
            if category_id is None:
                continue
            try:
                allowed = view.CanCategoryBeHidden(category_id)
            except BaseException as exc:
                notes.add(u"CanCategoryBeHidden could not be asked "
                          u"({0}), so it was not used as a guard".format(
                              to_text(exc)))
                continue
            if not allowed:
                blocked.add(category["key"])
        entry["blocked"] = blocked
    return views


def choose_views(views, categories):
    """The picker. Returns the chosen views, or None to stop.

    A broken dialog aborts. This tool writes to the model, so carrying on
    without a choice would mean writing to every view in the file.
    """
    labels, mapping = model.view_labels(views, categories)
    try:
        chosen = forms.SelectFromList.show(
            labels, multiselect=True, button_name="Continue",
            title=TITLE + ": pick the views")
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The view picker could not be shown, so nothing was "
            "changed: {0}".format(to_text(exc)), title=TITLE)
        return None

    if not chosen:
        return None
    return [mapping[label] for label in chosen if label in mapping]


def choose_direction():
    """Hide or Unhide. Returns the string, or None to stop."""
    try:
        return forms.CommandSwitchWindow.show(
            ["Hide", "Unhide"],
            message="Hide or unhide this in the views you picked?")
    except BaseException as exc:
        logger.debug(to_text(exc))

    try:
        return forms.alert(
            "Hide or unhide this in the views you picked?",
            title=TITLE, options=["Hide", "Unhide"])
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The Hide or Unhide question could not be asked, so nothing "
            "was changed: {0}".format(to_text(exc)), title=TITLE)
        return None


def confirm(lines):
    try:
        return bool(forms.alert("\n".join(lines), title=TITLE,
                                yes=True, no=True))
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The confirmation could not be shown, so nothing was "
            "changed: {0}".format(to_text(exc)), title=TITLE)
        return False


def commit_checked(transaction):
    status = transaction.Commit()
    committed = getattr(DB.TransactionStatus, "Committed", None)
    if committed is None:
        return True
    return status == committed


def collect_failures(transaction):
    """Swallow warnings, record errors, roll back on an error."""
    class Record(object):
        def __init__(self):
            self.errors = []

    record = Record()
    try:
        warning = DB.FailureSeverity.Warning
        rollback = DB.FailureProcessingResult.ProceedWithRollBack
        proceed = DB.FailureProcessingResult.Continue

        # Inherited alone. Mixing this with another base class breaks the
        # method resolution order and the handler silently never runs.
        class Collector(DB.IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                stop = False
                for failure in accessor.GetFailureMessages():
                    if failure.GetSeverity() == warning:
                        accessor.DeleteWarning(failure)
                        continue
                    record.errors.append(
                        to_text(failure.GetDescriptionText()))
                    stop = True
                return rollback if stop else proceed

        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(Collector())
        options.SetClearAfterRollback(True)
        options.SetForcedModalHandling(False)
        transaction.SetFailureHandlingOptions(options)
        return record
    except BaseException as exc:
        logger.debug(to_text(exc))
        return None


def write(doc, apply_to_view, entries):
    """One transaction for the whole run, so one undo reverses it.

    A view that refuses rolls the entire run back rather than leaving
    thirty nine views changed and one not. A reported failure is
    recoverable; a drawing set half changed by a tool nobody can see the
    effect of is not.
    """
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    record = collect_failures(transaction)

    done = []
    try:
        for entry in entries:
            apply_to_view(entry)
            done.append(entry[0]["name"])
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            "Nothing was changed in any view. The run stopped with: "
            "{0}".format(to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return None

    if not commit_checked(transaction):
        errors = record.errors if record is not None else []
        if errors:
            forms.alert(
                "Revit refused it, so nothing changed:\n\n{0}".format(
                    "\n".join(errors[:5])), title=TITLE)
        else:
            forms.alert("Revit rejected the change, so nothing changed.",
                        title=TITLE)
        return None

    return done, (record.errors if record is not None else [])


def run_elements(doc, views, elements, hide):
    lookup = {}
    for key, element_id, _element in elements:
        lookup[key] = element_id

    plan = model.element_plan(views, [key for key, _i, _e in elements], hide)
    if not plan["ready"]:
        forms.alert(
            "None of the views you picked can {0} the selection. {1} "
            "element/view pair(s) were already that way and {2} could "
            "not be shown in those views at all.".format(
                "hide" if hide else "unhide",
                plan["already"], plan["skipped"]),
            title=TITLE)
        return

    pairs = sum(len(keys) for _view, keys in plan["ready"])
    if not confirm([
            "{0} {1} element(s) in {2} view(s).".format(
                "Hide" if hide else "Unhide", len(elements),
                len(plan["ready"])),
            "",
            "{0} element/view pair(s) will change.".format(pairs),
            "",
            "  " + "\n  ".join(model.view_names(plan["ready"])
                               [:model.MAX_LISTED])]):
        return

    def apply_to_view(entry):
        view, keys = entry
        collection = id_collection([lookup[key] for key in keys])
        if hide:
            view["view"].HideElements(collection)
        else:
            view["view"].UnhideElements(collection)

    written = write(doc, apply_to_view, plan["ready"])
    if written is None:
        return
    done, errors = written

    output.print_md("### {0}".format(TITLE))
    output.print_md("**{0} {1} element(s) in {2} view(s).**".format(
        "Hid" if hide else "Unhid", len(elements), len(done)))
    for name in done[:model.MAX_LISTED]:
        output.print_md("- {0}".format(name))
    if len(done) > model.MAX_LISTED:
        output.print_md("- _and {0} more_".format(
            len(done) - model.MAX_LISTED))
    if plan["already"]:
        output.print_md(
            "_{0} pair(s) were already that way and were left "
            "alone._".format(plan["already"]))
    if plan["skipped"]:
        output.print_md(
            "_{0} pair(s) were skipped: wrong level, phase or design "
            "option, or a category that view does not draw._".format(
                plan["skipped"]))
    for message in errors[:5]:
        output.print_md("_Revit reported: {0}_".format(message))


def run_categories(doc, views, categories, category_ids, hide, notes):
    plan = model.category_plan(views, categories, hide)

    if not plan["ready"]:
        # Say which of the three reasons it was. A bare "cannot take
        # that" is the same sentence whether Revit refused, a template
        # owns it, or the guard itself fell over, and those need three
        # different responses from whoever is reading it.
        parts = ["None of the views you picked can take that."]
        advice = model.template_advice(plan["template"])
        if advice:
            parts.append(advice)
        if plan["blocked"]:
            parts.append(
                "Revit refused {0} in: {1}".format(
                    model.category_names(plan["blocked"][0][1]),
                    ", ".join(model.view_names(plan["blocked"])
                              [:model.MAX_LISTED])))
        for note in sorted(notes):
            parts.append(note)
        forms.alert("\n\n".join(parts), title=TITLE)
        return

    lines = [
        "{0} {1} in {2} view(s).".format(
            "Hide" if hide else "Unhide",
            model.category_names(categories), len(plan["ready"])),
        "",
        "  " + "\n  ".join(model.view_names(plan["ready"])
                           [:model.MAX_LISTED]),
    ]
    # A redirected selection is a change of scope, so it is said before
    # the write, not reported after it.
    for note in sorted(notes):
        lines = lines + ["", note]
    advice = model.template_advice(plan["template"])
    if advice:
        lines = lines + ["", advice]
    if not confirm(lines):
        return

    def apply_to_view(entry):
        view, cats = entry
        for category in cats:
            category_id = category_ids[category["key"]]
            try:
                current = view["view"].GetCategoryHidden(category_id)
            except BaseException:
                current = None
            if current is not None and bool(current) == bool(hide):
                continue
            view["view"].SetCategoryHidden(category_id, bool(hide))

    written = write(doc, apply_to_view, plan["ready"])
    if written is None:
        return
    done, errors = written

    output.print_md("### {0}".format(TITLE))
    output.print_md("**{0} {1} in {2} view(s).**".format(
        "Hid" if hide else "Unhid", model.category_names(categories),
        len(done)))
    for name in done[:model.MAX_LISTED]:
        output.print_md("- {0}".format(name))
    if advice:
        output.print_md("_{0}_".format(advice))
    for view, cats in plan["blocked"]:
        output.print_md(
            "_{0}: Revit will not hide {1} in this view._".format(
                view["name"], model.category_names(cats)))
    if plan["unsupported"]:
        output.print_md("_Not a category a view can hide: {0}._".format(
            model.category_names(plan["unsupported"])))
    for note in sorted(notes):
        output.print_md("_{0}_".format(note))
    for message in errors[:5]:
        output.print_md("_Revit reported: {0}_".format(message))


def shift_clicked():
    """True when the button was shift clicked.

    Two routes because pyRevit has offered two, and neither is allowed to
    raise.
    """
    try:
        from pyrevit import EXEC_PARAMS
        value = getattr(EXEC_PARAMS, "config_mode", None)
        if value is not None:
            return bool(value)
    except BaseException:
        pass

    for source in (globals(), sys.modules["__main__"].__dict__):
        value = source.get("__shiftclick__")
        if value is not None:
            return bool(value)
    return False


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert("No active Revit document.", title=TITLE)
        return

    notes = set()
    (elements, categories, category_ids,
     substitutions) = selection(doc, uidoc, notes)
    swap = model.substitution_note(substitutions)
    if swap:
        notes.add(swap)
    if not elements:
        forms.alert(
            "Select the elements you want hidden, then click again.",
            title=TITLE)
        return

    by_category = shift_clicked()
    if by_category and not model.supported(categories):
        # Selecting a section marker in a plan hands back the ViewSection
        # itself, whose category is Views. Views is neither a model nor
        # an annotation category, so no view and no template can switch
        # it off, and the marker you can see is governed by Sections
        # under Annotation Categories, which is a different category the
        # selection cannot lead to. Saying only "a view cannot hide
        # those" is true and useless: it leaves the reader believing the
        # thing they want is impossible when the plain click does it.
        forms.alert(
            "The selection is only {0}, which is not a category any view "
            "or template can switch off.\n\n"
            "A section, callout or elevation marker belongs to the Views "
            "category. The switch you would reach for in Visibility / "
            "Graphics is Sections, under Annotation Categories, and that "
            "is a different category, so the selection cannot lead to "
            "it.\n\n"
            "To hide these particular markers, click without shift. The "
            "element route hides the selected elements themselves and "
            "works on sections.".format(
                model.category_names(categories))
            + why_no_marker(notes), title=TITLE)
        return

    active = doc.ActiveView
    active_id = active.Id if active is not None else None

    views = gather_views(doc, active_id)
    if not views:
        forms.alert("This model has no views that can take a hide.",
                    title=TITLE)
        return

    chosen = choose_views(views, categories if by_category else None)
    if not chosen:
        return

    direction = choose_direction()
    if not direction:
        return
    hide = to_text(direction).strip().lower() != "unhide"

    if by_category:
        run_categories(
            doc,
            enrich_for_categories(chosen, categories, category_ids, notes),
            categories, category_ids, hide, notes)
    else:
        run_elements(doc, enrich_for_elements(chosen, elements),
                     elements, hide)


if __name__ == "__main__":
    run()
