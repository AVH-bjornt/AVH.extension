# -*- coding: utf-8 -*-
"""
Write each element's own id into its Element ID parameter.

Two steps, as with the import: plan_stamps looks and writes nothing,
execute_stamps writes in one transaction whose commit status is checked.

Only what is wrong is written. An element that already holds its own id is
counted and left alone: rewriting the same value onto every door marks the
whole model as modified, which on a workshared job turns a tidy up into a
sync.

Left alone, with a reason: groups (Revit refuses an instance parameter
change on a group member with an unignorable Ungroup dialog), elements
another user has checked out, a read only parameter, an Integer parameter
that cannot hold the id, and a parameter that is not text or integer.

UNVERIFIED IN REVIT as of 2.41.0. ParameterBindings iteration, the category
filter and GroupId have been run against a mock only.
"""

from . import stamp
from .applier import _is_group_member, _owned_by_someone_else
from .compat import element_id_int, to_text

SKIP_GROUP = u"element is inside a group"
SKIP_OWNED = u"element is checked out by another user"
SKIP_READ_ONLY = u"parameter is read only"
SKIP_NO_PARAMETER = u"element has no Element ID parameter"
SKIP_STORAGE = u"parameter is neither text nor a whole number"
SKIP_TOO_BIG = u"the id does not fit an Integer parameter"
SKIP_REFUSED = u"Revit refused the value"
SKIP_ROLLED_BACK = u"Revit rolled the whole change back"


class Plan(object):
    def __init__(self):
        self.ready = []           # [(element, parameter, value)]
        self.already = 0
        self.skipped = []         # [(element id, category, reason)]


class Result(object):
    def __init__(self):
        self.written = 0
        self.failed = []          # [(element id, reason)]
        self.committed = False


def stamp_parameter_name(DB, doc):
    """(name, [Category]) of the Element ID parameter and where it is
    bound, or (None, []) when the model has none."""
    iterator = doc.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        definition = iterator.Key
        if not stamp.is_stamp_name(definition.Name):
            continue
        categories = []
        try:
            for category in iterator.Current.Categories:
                categories.append(category)
        except Exception:
            pass
        return to_text(definition.Name), categories
    return None, []


def collect_elements(DB, doc, category):
    """Every element (not type) of a category."""
    collector = DB.FilteredElementCollector(doc)
    collector = collector.OfCategoryId(category.Id)
    return list(collector.WhereElementIsNotElementType())


def _storage_name(DB, parameter):
    storage = parameter.StorageType
    for name in (u"String", u"Integer"):
        if storage == getattr(DB.StorageType, name, object()):
            return name
    return u"other"


def plan_stamps(DB, doc, elements, parameter_name):
    """Decide what to write. Writes nothing."""
    plan = Plan()
    for element in elements:
        ident = element_id_int(element.Id)
        try:
            category = to_text(element.Category.Name)
        except Exception:
            category = u""

        if _is_group_member(element):
            plan.skipped.append((ident, category, SKIP_GROUP))
            continue
        parameter = element.LookupParameter(parameter_name)
        if parameter is None:
            plan.skipped.append((ident, category, SKIP_NO_PARAMETER))
            continue

        storage = _storage_name(DB, parameter)
        if storage == u"other":
            plan.skipped.append((ident, category, SKIP_STORAGE))
            continue

        current = (parameter.AsString() if storage == u"String"
                   else parameter.AsInteger())
        if stamp.already_stamped(current, ident, storage):
            plan.already += 1
            continue

        if parameter.IsReadOnly:
            plan.skipped.append((ident, category, SKIP_READ_ONLY))
            continue
        if _owned_by_someone_else(DB, doc, element):
            plan.skipped.append((ident, category, SKIP_OWNED))
            continue
        value = stamp.stamp_value(ident, storage)
        if value is None:
            plan.skipped.append((ident, category, SKIP_TOO_BIG))
            continue
        plan.ready.append((element, parameter, value))
    return plan


def execute_stamps(DB, doc, plan, name=u"AVH stamp Element IDs"):
    result = Result()
    if not plan.ready:
        result.committed = True
        return result

    transaction = DB.Transaction(doc, name)
    transaction.Start()
    written = []
    try:
        for element, parameter, value in plan.ready:
            ident = element_id_int(element.Id)
            try:
                accepted = parameter.Set(value)
            except Exception as exc:
                result.failed.append((ident, to_text(exc)))
                continue
            if accepted is False:
                result.failed.append((ident, SKIP_REFUSED))
                continue
            written.append(ident)
    except BaseException:
        transaction.RollBack()
        raise

    if transaction.Commit() != DB.TransactionStatus.Committed:
        result.failed.extend((i, SKIP_ROLLED_BACK) for i in written)
        return result

    result.written = len(written)
    result.committed = True
    return result
