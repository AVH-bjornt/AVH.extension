# -*- coding: utf-8 -*-
"""Hide or unhide the categories of the current selection in view templates.

Select something, click, pick the templates, pick Hide or Unhide. The
categories of whatever is selected are hidden in those templates, and
every view using a template follows it.

## Why categories and not elements

A view template carries category visibility, filters and V/G overrides.
It carries no per element hide list, because an element hide is stored
on the view. So there is no such thing as hiding one door in a template,
and this tool works on the categories of the selection instead. Hiding
specific elements across views is a different job and a different
button.

## The dangerous part

A template only changes what its views show for the parameters it
controls. If a template does not control Visibility / Graphics, writing
a category hide into it changes nothing anyone can see, and a tool that
wrote anyway would report a change that does not exist.

Switching that parameter on fixes it and is destructive: the template
takes over category visibility for every view using it, and whatever
those views show or hide today is replaced. So the run counts the views
first, names the templates and the parameters in the dialog, and only
proceeds if the offer is accepted. Declining skips those templates and
still writes the rest.

That is the same shape as Flip Status and the vary across groups flag:
the one thing this tool changes beyond the obvious, on request, because
it is the difference between the tool working and the tool lying.

## Dialogs abort here

Isolate Warnings falls back to doing everything when its picker breaks,
because it only changes what is visible. This tool writes to the model,
so a dialog that fails means nobody confirmed anything and the run
stops. Same codebase, opposite correct answer.

## Unverified

Not yet run in Revit: View.IsTemplate, GetTemplateParameterIds,
GetNonControlledTemplateParameterIds, SetNonControlledTemplateParameterIds,
CanCategoryBeHidden, GetCategoryHidden, SetCategoryHidden, and
Category.CategoryType. Both write transactions check their commit
status, and the second carries a failure preprocessor, so a rejected
write cannot report success.
"""

__title__ = "Hide in\nTemplate"
__author__ = "AVH"
__doc__ = ("Hide or unhide the categories of the selected elements in "
           "view templates you pick. Every view using a template "
           "follows it.")

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels. A button nested one deeper, in a pulldown, was
# enough to break the fixed count.
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

TITLE = "Hide in Template"

# The two view parameters a template uses to take over category
# visibility. Model and annotation categories are governed separately,
# and a selection can easily contain both.
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
    """The ElementId of a built in view parameter, or None."""
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
    """Which visibility parameter governs this category."""
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
               if u"ViewType" in note or u"Category" in note
               or u"carry that name" in note or u"names a view" in note
               or u"more than one kind of view" in note]
    if not reasons:
        return ""
    return "\n\n" + "\n".join(reasons)


def element_class(element):
    """The .NET class name, for a note that names what was really got."""
    try:
        return to_text(element.GetType().Name)
    except BaseException:
        try:
            return to_text(type(element).__name__)
        except BaseException:
            return u"an unknown element"


def parameters_of(element):
    """Every parameter on the element, or an empty list."""
    try:
        return list(element.Parameters)
    except BaseException as exc:
        logger.debug(to_text(exc))
        return []


def parameter_name(parameter):
    try:
        return to_text(parameter.Definition.Name)
    except BaseException:
        return u"?"


def builtin_of(parameter):
    """Which BuiltInParameter this is, when it is one."""
    try:
        return to_text(parameter.Definition.BuiltInParameter)
    except BaseException:
        return u""


def view_candidates(doc, element):
    """Every view this element points at, and the parameter that said so.

    Two routes, both measured rather than named. A parameter whose
    `AsElementId` resolves to something with a `ViewType` is a candidate,
    and so is one whose string matches exactly one view name.

    **View templates are excluded.** A template has a `ViewType` like any
    view, so an id parameter pointing at one used to be accepted as the
    answer, which is one of the two ways this returned Elevation for a
    section.

    Returns (candidates, ambiguous). A candidate is (view, parameter
    name). `ambiguous` are parameters that matched several views, which
    is only worth a dialog when nothing else resolved: beside a
    successful resolution it reads as a warning about something that did
    not go wrong, and a dialog that cries wolf stops being read.
    """
    found = []
    ambiguous = []
    parameters = parameters_of(element)
    if not parameters:
        return found, ambiguous

    for parameter in parameters:
        try:
            target = doc.GetElement(parameter.AsElementId())
        except BaseException:
            continue
        if target is None:
            continue
        if getattr(target, "ViewType", None) is None:
            continue
        try:
            if target.IsTemplate:
                continue
        except BaseException:
            pass
        found.append((target, parameter_name(parameter)))

    by_name = {}
    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
            try:
                if view.IsTemplate:
                    continue
                by_name.setdefault(to_text(view.Name), []).append(view)
            except BaseException:
                continue
    except BaseException as exc:
        logger.debug(to_text(exc))
        return found, ambiguous

    for parameter in parameters:
        try:
            value = to_text(parameter.AsString())
        except BaseException:
            continue
        if not value or value not in by_name:
            continue
        matched = by_name[value]
        if len(matched) != 1:
            ambiguous.append(
                u"The parameter {0} names a view {1}, and {2} views carry "
                u"that name, so it was not followed.".format(
                    parameter_name(parameter), value, len(matched)))
            continue
        found.append((matched[0], parameter_name(parameter)))

    return found, ambiguous


def describe_candidates(candidates, ambiguous=()):
    """Print what was resolved and from where, every time.

    This ran once, resolved to the wrong view, said only the category
    name in the confirmation, and was approved. A resolution that cannot
    be checked afterwards is one nobody can correct.
    """
    try:
        output.print_md("### {0}: what the marker points at".format(TITLE))
        for view, source in candidates:
            output.print_md("- parameter **{0}** points at *{1}* "
                            "({2})".format(
                                source, to_text(view.Name),
                                model.view_type_name(
                                    getattr(view, "ViewType", u""))))
        for line in ambiguous:
            output.print_md("- _{0}_".format(line))
    except BaseException as exc:
        logger.debug(to_text(exc))


def describe_element(doc, element):
    """Print what this element actually is, to the output window.

    Reached only when the marker could not be followed. Four releases
    were spent reasoning about an element nobody had looked at, so when
    this route fails it prints the element rather than another sentence
    about it.
    """
    try:
        output.print_md("### {0}: what was selected".format(TITLE))
        output.print_md("- class: **{0}**".format(element_class(element)))
        category = getattr(element, "Category", None)
        if category is not None:
            output.print_md("- category: **{0}**".format(
                to_text(category.Name)))
        rows = []
        for parameter in parameters_of(element):
            builtin = builtin_of(parameter)
            value = u""
            for reader in ("AsString", "AsValueString"):
                try:
                    value = to_text(getattr(parameter, reader)())
                except BaseException:
                    value = u""
                if value:
                    break
            rows.append(u"- {0}{1}{2}".format(
                parameter_name(parameter),
                u"  [{0}]".format(builtin) if builtin else u"",
                u" = {0}".format(value) if value else u""))
        if rows:
            output.print_md("Parameters:")
            for row in rows[:60]:
                output.print_md(row)
        else:
            output.print_md("_It carries no readable parameters._")
    except BaseException as exc:
        logger.debug(to_text(exc))


def view_type_of_marker(doc, element, notes):
    """The ViewType behind a selected marker, or None.

    Selecting a section marker in a plan does **not** hand back the
    ViewSection. It hands back a separate element in the Views category
    which is not a View, so it has no ViewType. What it does carry is
    read off it rather than assumed.
    """
    view_type = getattr(element, "ViewType", None)
    if view_type is not None:
        return view_type

    candidates, ambiguous = view_candidates(doc, element)
    if not candidates:
        # Nothing resolved, so every near miss is worth saying out loud.
        for line in ambiguous:
            notes.add(line)
        notes.add(u"{0} has no ViewType, and nothing it carries points at "
                  u"a view, so the marker could not be followed. What it "
                  u"does carry is listed in the output window.".format(
                      element_class(element)))
        describe_element(doc, element)
        return None

    describe_candidates(candidates, ambiguous)

    kinds = {}
    for view, source in candidates:
        kind = model.view_type_name(getattr(view, "ViewType", u""))
        kinds.setdefault(kind, []).append((view, source))

    # Taking the first of several is how this switched off Elevations for
    # a section. If the parameters disagree, nothing is written and all
    # of them are named, because the tool cannot tell which is meant and
    # neither can a dialog that only shows the winner.
    if len(kinds) > 1:
        notes.add(u"{0} points at more than one kind of view: {1}. It is "
                  u"not clear which marker was meant, so nothing was "
                  u"changed. The parameters are listed in the output "
                  u"window.".format(
                      element_class(element),
                      u"; ".join(
                          u"{0} via {1}".format(
                              kind, u", ".join(source for _v, source
                                               in entries))
                          for kind, entries in sorted(kinds.items()))))
        return None

    kind = list(kinds.keys())[0]
    view, source = kinds[kind][0]
    notes.add(u"{0} was followed to the view {1} ({2}) through the "
              u"parameter {3}.".format(
                  element_class(element), to_text(view.Name), kind, source))
    return getattr(view, "ViewType", None)


def marker_category(doc, element, notes):
    """The annotation category governing a view element's marker.

    Returns (Category, source_name) or (None, None). Resolved through
    BuiltInCategory rather than a category name, because category names
    follow the interface language and this model is not in English.
    """
    view_type = view_type_of_marker(doc, element, notes)
    if view_type is None:
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


def selected_categories(doc, uidoc, notes):
    """(categories, category_ids, substitutions) for the selection.

    The real `Category.Id` is carried through rather than rebuilt from
    its number later. Reconstructing an ElementId from a value is a round
    trip that can fail on its own.
    """
    try:
        selected = list(uidoc.Selection.GetElementIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        return [], {}, []

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
        category = getattr(element, "Category", None)
        if category is None:
            continue

        # See Hide Across Views: a marker selects the view itself, whose
        # category nothing can switch off. Swap in the annotation
        # category that governs the marker, and record the swap.
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
    return model.merge_categories(entries), category_ids, substitutions


def view_counts(doc):
    """How many real views use each template, keyed by template id."""
    counts = {}
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        for view in collector:
            try:
                if view.IsTemplate:
                    continue
                template_id = view.ViewTemplateId
            except BaseException:
                continue
            marker = model.key_of(template_id)
            counts[marker] = counts.get(marker, 0) + 1
    except BaseException as exc:
        logger.debug(to_text(exc))
    return counts


def controlled_kinds(view, categories):
    """(controls, unavailable) for one template.

    `controls` is the kinds the template already takes over. A kind whose
    parameter is not offered by this template at all cannot be switched
    on either, so its categories are unreachable here and are reported
    rather than attempted.
    """
    controls = set()
    unavailable = set()

    try:
        available = set(model.key_of(pid)
                        for pid in view.GetTemplateParameterIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        available = None

    try:
        not_controlled = set(
            model.key_of(pid)
            for pid in view.GetNonControlledTemplateParameterIds())
    except BaseException as exc:
        logger.debug(to_text(exc))
        not_controlled = set()

    for kind in model.needed_kinds(categories):
        parameter = PARAMETER_IDS.get(kind)
        if parameter is None:
            unavailable.add(kind)
            continue
        marker = model.key_of(parameter)
        if available is not None and marker not in available:
            unavailable.add(kind)
            continue
        if marker not in not_controlled:
            controls.add(kind)
    return controls, unavailable


def gather_templates(doc, categories, category_ids, active_template_id,
                     notes):
    """Every view template in the model, described for the model layer."""
    counts = view_counts(doc)
    active_marker = model.key_of(active_template_id)
    templates = []

    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        views = list(collector)
    except BaseException as exc:
        logger.error(to_text(exc))
        return []

    for view in views:
        try:
            if not view.IsTemplate:
                continue
        except BaseException:
            continue

        controls, unavailable = controlled_kinds(view, categories)

        blocked = set()
        for category in model.supported(categories):
            if category["kind"] in unavailable:
                blocked.add(category["key"])
                continue
            category_id = category_ids.get(category["key"])
            if category_id is None:
                continue
            # Only a clear no blocks. A guard that cannot run must not
            # silently refuse everything, which turns a broken call into
            # "nothing can take it" and hides the real reason. The write
            # rolls back on failure and reports Revit's own words, so
            # letting it through is the safer of the two.
            try:
                allowed = view.CanCategoryBeHidden(category_id)
            except BaseException as exc:
                notes.add(u"CanCategoryBeHidden could not be asked "
                          u"({0}), so it was not used as a guard".format(
                              to_text(exc)))
                continue
            if not allowed:
                blocked.add(category["key"])

        marker = model.key_of(view.Id)
        templates.append({
            "key": marker,
            "name": to_text(view.Name),
            "views": counts.get(marker, 0),
            "controls": controls,
            "blocked": blocked,
            "active": marker == active_marker,
            "view": view,
        })

    return model.order_templates(templates)


def choose_templates(templates, categories):
    """The picker. Returns the chosen templates, or None to stop.

    A broken dialog aborts. This tool writes to the model, so carrying on
    without a choice would mean writing to every template in the file.
    """
    labels, mapping = model.template_labels(templates, categories)
    try:
        chosen = forms.SelectFromList.show(
            labels, multiselect=True, button_name="Continue",
            title=TITLE + ": pick the templates")
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The template picker could not be shown, so nothing was "
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
            message="Hide or unhide these categories in the picked "
                    "templates?")
    except BaseException as exc:
        logger.debug(to_text(exc))

    try:
        answer = forms.alert(
            "Hide or unhide these categories in the picked templates?",
            title=TITLE, options=["Hide", "Unhide"])
        return answer
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The Hide or Unhide question could not be asked, so nothing "
            "was changed: {0}".format(to_text(exc)), title=TITLE)
        return None


def confirm(entries, categories, hide, notes=()):
    """The dry run. Returns True to go ahead."""
    action = "Hide" if hide else "Unhide"
    lines = [
        "{0} {1} in {2} template(s).".format(
            action, model.category_names(categories), len(entries)),
        "",
        "{0} view(s) use those templates and will follow the "
        "change.".format(model.views_touched(entries)),
        "",
    ]
    for template, cats, _kinds in entries[:model.MAX_LISTED]:
        lines.append("  {0}  -  {1} view(s)  -  {2}".format(
            template["name"], template["views"],
            model.category_names(cats)))
    if len(entries) > model.MAX_LISTED:
        lines.append("  and {0} more".format(len(entries) - model.MAX_LISTED))

    for note in sorted(notes):
        lines = lines + ["", note]

    try:
        return bool(forms.alert("\n".join(lines), title=TITLE,
                                yes=True, no=True))
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(
            "The confirmation could not be shown, so nothing was "
            "changed: {0}".format(to_text(exc)), title=TITLE)
        return False


def offer_control(pending, hide):
    """Ask before making templates take over V/G. True if accepted."""
    action = "hiding" if hide else "unhiding"
    try:
        return bool(forms.alert(
            model.offer_text(pending, action),
            title=TITLE + ": take over Visibility / Graphics",
            yes=True, no=True))
    except BaseException as exc:
        logger.error(to_text(exc))
        return False


def commit_checked(transaction):
    """Commit, and say whether Revit actually kept it."""
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


def take_control(doc, pending):
    """Make the pending templates control the parameters they need.

    Its own transaction, committed before anything is written, so the
    write below either lands where it can be seen or does not happen.
    Returns the templates that are now controlled.
    """
    transaction = DB.Transaction(doc, TITLE + ": control V/G")
    transaction.Start()
    granted = []
    try:
        for template, cats, kinds in pending:
            view = template["view"]
            wanted = set()
            for kind in kinds:
                parameter = PARAMETER_IDS.get(kind)
                if parameter is not None:
                    wanted.add(model.key_of(parameter))
            keep = [pid for pid
                    in view.GetNonControlledTemplateParameterIds()
                    if model.key_of(pid) not in wanted]
            view.SetNonControlledTemplateParameterIds(id_collection(keep))
            granted.append((template, cats, kinds))
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            "No template was changed. Taking over Visibility / Graphics "
            "stopped with: {0}".format(to_text(exc)), title=TITLE)
        logger.error(to_text(exc))
        return []

    if not commit_checked(transaction):
        forms.alert(
            "Revit rejected the change to the templates, so nothing was "
            "hidden.", title=TITLE)
        return []
    return granted


def pending_writes(entries, category_ids, hide):
    """(todo, already) worked out by reading, before any transaction.

    Only write what changes. Rewriting the same state onto every template
    marks them all as modified, which on a workshared job turns a check
    into a sync, and a run where nothing changes should not leave an
    empty undo step behind either.
    """
    todo = []
    already = 0
    for template, cats, _kinds in entries:
        view = template["view"]
        for category in cats:
            try:
                current = view.GetCategoryHidden(
                    category_ids[category["key"]])
            except BaseException:
                current = None
            if current is not None and bool(current) == bool(hide):
                already += 1
                continue
            todo.append((template, category))
    return todo, already


def apply_hides(doc, todo, category_ids, hide):
    """The write. Returns (changed, errors) or None if refused."""
    transaction = DB.Transaction(doc, TITLE)
    transaction.Start()
    record = collect_failures(transaction)

    changed = []
    try:
        for template, category in todo:
            template["view"].SetCategoryHidden(
                category_ids[category["key"]], bool(hide))
            changed.append((template["name"], category["name"],
                            template["views"]))
    except BaseException as exc:
        transaction.RollBack()
        forms.alert(
            "Nothing was changed. The run stopped with: {0}".format(
                to_text(exc)), title=TITLE)
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

    return changed, (record.errors if record is not None else [])


def report(changed, already, plan_result, declined, hide, notes=()):
    action = "Hidden" if hide else "Unhidden"
    output.print_md("### {0}".format(TITLE))
    output.print_md("**{0} in {1} template/category pair(s).**".format(
        action, len(changed)))

    seen = {}
    for name, category, views in changed:
        seen[name] = views
    if seen:
        output.print_md("{0} view(s) follow those templates.".format(
            sum(seen.values())))

    for name, category, views in changed[:model.MAX_LISTED]:
        output.print_md("- {0}: **{1}** ({2} view(s))".format(
            name, category, views))
    if len(changed) > model.MAX_LISTED:
        output.print_md("- _and {0} more not listed_".format(
            len(changed) - model.MAX_LISTED))

    if already:
        output.print_md(
            "_{0} pair(s) were already in that state and were left "
            "alone._".format(already))

    if declined:
        output.print_md(
            "_{0} template(s) were skipped because they do not control "
            "Visibility / Graphics and the offer to change that was "
            "declined._".format(len(declined)))

    for template, cats in plan_result["blocked"]:
        output.print_md(
            "_{0}: Revit will not hide {1} from this template._".format(
                template["name"], model.category_names(cats)))

    if plan_result["unsupported"]:
        output.print_md(
            "_Not reachable from a view template: {0}._".format(
                model.category_names(plan_result["unsupported"])))

    for note in sorted(notes):
        output.print_md("_{0}_".format(note))


def run():
    doc = revit.doc
    if doc is None:
        forms.alert("No active Revit document.", title=TITLE)
        return

    uidoc = revit.uidoc
    if uidoc is None:
        forms.alert("No active Revit document.", title=TITLE)
        return

    notes = set()
    (categories, category_ids,
     substitutions) = selected_categories(doc, uidoc, notes)
    swap = model.substitution_note(substitutions)
    if swap:
        notes.add(swap)
    if not categories:
        forms.alert(
            "Select the elements whose categories you want hidden, then "
            "click again.", title=TITLE)
        return

    if not model.supported(categories):
        # See the note in Hide Across Views: a section marker selects the
        # ViewSection, whose category is Views, and no template controls
        # that. Naming the button that can do it beats a dead end.
        forms.alert(
            "The selection is only {0}, which is not a category any "
            "template can control.\n\n"
            "A section, callout or elevation marker belongs to the Views "
            "category, and the switch in Visibility / Graphics is "
            "Sections under Annotation Categories, a different category "
            "the selection cannot lead to.\n\n"
            "To hide these particular markers, use Hide Across Views "
            "without shift, which hides the selected elements "
            "themselves.".format(model.category_names(categories))
            + why_no_marker(notes),
            title=TITLE)
        return

    view = doc.ActiveView
    active_template_id = None
    if view is not None:
        active_template_id = getattr(view, "ViewTemplateId", None)

    templates = gather_templates(doc, categories, category_ids,
                                 active_template_id, notes)
    if not templates:
        forms.alert("This model has no view templates.", title=TITLE)
        return

    chosen = choose_templates(templates, categories)
    if not chosen:
        return

    direction = choose_direction()
    if not direction:
        return
    hide = to_text(direction).strip().lower() != "unhide"

    dry = model.plan(chosen, categories, enable=False)
    entries = list(dry["ready"])
    declined = []

    if dry["pending"]:
        if offer_control(dry["pending"], hide):
            entries = entries + take_control(doc, dry["pending"])
        else:
            declined = dry["pending"]

    if not entries:
        forms.alert(
            "Nothing was left to change once the templates that cannot "
            "take it were set aside.", title=TITLE)
        return

    todo, already = pending_writes(entries, category_ids, hide)
    if not todo:
        report([], already, dry, declined, hide, notes)
        forms.alert(
            "Every template you picked already shows those categories "
            "that way, so nothing was changed.", title=TITLE)
        return

    if not confirm(entries, categories, hide, notes):
        return

    written = apply_hides(doc, todo, category_ids, hide)
    if written is None:
        return

    changed, errors = written
    report(changed, already, dry, declined, hide, notes)
    for message in errors[:5]:
        output.print_md("_Revit reported: {0}_".format(message))


if __name__ == "__main__":
    run()
