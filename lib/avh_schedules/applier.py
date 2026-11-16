# -*- coding: utf-8 -*-
"""
Write the changes found by roundtrip.find_changes back into the model.

Two steps, so a person sees everything before anything is written:

    plan_changes   looks at each change against the live model, decides
                   whether it can be written, and writes NOTHING
    execute_plan   writes the ones that can, in one transaction

A change is skipped, with a reason, rather than forced, in every case
where forcing it could do damage: a type parameter (changes every element
of the type), an element inside a group (Revit's unignorable "Ungroup"
dialog), an element somebody else has checked out, a value that has moved
in the model since the export, a parameter that is read only.

UNVERIFIED IN REVIT as of 2.40.0. Everything here touches the Revit API
and has been run only against a mock. The calls that carry the risk:
Parameter.Id matching ScheduleField.ParameterId, UnitUtils with a
ForgeTypeId (Revit 2021+), and GroupId on the element.

Numbers never pass through a string on the way in. A cell is a real number
read from the file, converted to Revit's internal units with UnitUtils, and
set as a double. AVH's Revit uses a decimal comma, and SetValueString on a
period decimal is how a rejected write used to go unnoticed.
"""

from . import roundtrip as rt
from .compat import element_id_int, to_text


class Planned(object):
    """A change that will be written."""

    def __init__(self, change, element, parameter, storage, value, current):
        self.change = change
        self.element = element
        self.parameter = parameter
        self.storage = storage    # u"String", u"Integer" or u"Double"
        self.value = value        # what will be passed to Set
        self.current = current    # what the model holds now, for display


class Plan(object):
    def __init__(self):
        self.ready = []           # [Planned]
        self.skipped = []         # [(Change, reason, detail)]
        self.already = []         # [Change] the model already has that value

    def count_skipped(self, reason):
        return len([s for s in self.skipped if s[1] == reason])


class Result(object):
    def __init__(self):
        self.written = []         # [Planned]
        self.failed = []          # [(Planned, reason)]
        self.committed = False


def _is_group_member(element):
    """True when the element belongs to a group.

    UNVERIFIED: Element.GroupId is -1 (ElementId.InvalidElementId) for an
    element in no group.
    """
    try:
        group_id = element_id_int(element.GroupId)
    except Exception:
        return False
    return group_id is not None and group_id != -1


def _owned_by_someone_else(DB, doc, element):
    """True only when Revit says so. An unavailable check is not a no."""
    try:
        if not doc.IsWorkshared:
            return False
        status = DB.WorksharingUtils.GetCheckoutStatus(doc, element.Id)
        return status == DB.CheckoutStatus.OwnedByOtherUser
    except Exception:
        return False


def find_parameter(element, field):
    """The element's own parameter behind a schedule column, or None.

    By id first: it is what the schedule field holds and cannot be
    confused by a renamed column. By name as the fallback, which is how a
    shared parameter is told apart from a built in one of the same name.
    """
    if field.param_id is not None:
        try:
            for parameter in element.Parameters:
                if element_id_int(parameter.Id) == field.param_id:
                    return parameter
        except Exception:
            pass
    if field.param_name:
        try:
            return element.LookupParameter(field.param_name)
        except Exception:
            return None
    return None


def _storage_name(DB, parameter):
    storage = parameter.StorageType
    for name in (u"String", u"Integer", u"Double", u"ElementId"):
        if storage == getattr(DB.StorageType, name, object()):
            return name
    return to_text(storage)


def _to_internal(DB, number, unit_id):
    """A number in the column's display unit, in Revit's internal units."""
    return DB.UnitUtils.ConvertToInternalUnits(
        float(number), DB.ForgeTypeId(unit_id))


def plan_changes(DB, doc, changes):
    """Decide what can be written. Writes nothing."""
    plan = Plan()

    for change in changes:
        field = change.field

        if field.kind == rt.KIND_TYPE:
            plan.skipped.append((change, rt.SKIP_TYPE, u""))
            continue
        if field.kind != rt.KIND_INSTANCE:
            plan.skipped.append((change, rt.SKIP_OTHER, u""))
            continue

        element = doc.GetElement(DB.ElementId(change.element_id))
        if element is None:
            plan.skipped.append((change, rt.SKIP_GONE, u""))
            continue
        if _is_group_member(element):
            plan.skipped.append((change, rt.SKIP_GROUP, u""))
            continue

        parameter = find_parameter(element, field)
        if parameter is None:
            plan.skipped.append((change, rt.SKIP_NO_PARAMETER,
                                 to_text(field.param_name or field.name)))
            continue
        if parameter.IsReadOnly:
            plan.skipped.append((change, rt.SKIP_READ_ONLY, u""))
            continue
        if _owned_by_someone_else(DB, doc, element):
            plan.skipped.append((change, rt.SKIP_OWNED, u""))
            continue

        storage = _storage_name(DB, parameter)

        if storage == u"String":
            current = to_text(parameter.AsString())
            if current.strip() != rt.as_text_value(change.old):
                plan.skipped.append((
                    change, rt.SKIP_CONFLICT,
                    u"model now has \"{0}\", sheet was exported with "
                    u"\"{1}\"".format(current, rt.as_text_value(change.old))))
                continue
            value = rt.as_text_value(change.new)
            if value == current.strip():
                plan.already.append(change)
                continue
            plan.ready.append(Planned(change, element, parameter, storage,
                                      value, current))

        elif storage == u"Integer":
            current = int(parameter.AsInteger())
            old = rt.as_yes_no(change.old)
            if old is None:
                old = rt.as_number(change.old)
            if old is not None and old != current:
                plan.skipped.append((
                    change, rt.SKIP_CONFLICT,
                    u"model now has {0}, sheet was exported with "
                    u"{1}".format(current, to_text(change.old))))
                continue
            value = rt.as_yes_no(change.new)
            if value is None:
                number = rt.as_number(change.new)
                if number is None or number != int(number):
                    plan.skipped.append((change, rt.SKIP_BAD_VALUE,
                                         to_text(change.new)))
                    continue
                value = int(number)
            if value == current:
                plan.already.append(change)
                continue
            plan.ready.append(Planned(change, element, parameter, storage,
                                      value, current))

        elif storage == u"Double":
            if rt._is_blank(change.new):
                plan.skipped.append((change, rt.SKIP_BLANK_NUMBER, u""))
                continue
            number = rt.as_number(change.new)
            if number is None:
                plan.skipped.append((change, rt.SKIP_BAD_VALUE,
                                     to_text(change.new)))
                continue
            if not field.unit_id:
                plan.skipped.append((change, rt.SKIP_UNIT, u""))
                continue
            try:
                value = _to_internal(DB, number, field.unit_id)
            except Exception as exc:
                plan.skipped.append((change, rt.SKIP_UNIT, to_text(exc)))
                continue
            current = float(parameter.AsDouble())
            # The model is not compared with the sheet's original for a
            # number, because the sheet holds a rounded display value and
            # the model does not. Only an exact match to what is already
            # there is recognised, so nothing is rewritten needlessly.
            if abs(value - current) <= 1e-9:
                plan.already.append(change)
                continue
            plan.ready.append(Planned(change, element, parameter, storage,
                                      value, current))

        else:
            plan.skipped.append((change, rt.SKIP_STORAGE, storage))

    return plan


def execute_plan(DB, doc, plan, name=u"AVH import schedule"):
    """Write every planned change in one transaction.

    The commit status is checked and the outcome reported from it: a
    transaction Revit rolled back must not read as success.
    """
    result = Result()
    if not plan.ready:
        result.committed = True
        return result

    transaction = DB.Transaction(doc, name)
    transaction.Start()
    try:
        for planned in plan.ready:
            try:
                accepted = planned.parameter.Set(planned.value)
            except Exception as exc:
                result.failed.append((planned, to_text(exc)))
                continue
            if accepted is False:
                result.failed.append((planned, rt.SKIP_REFUSED))
                continue
            result.written.append(planned)
    except BaseException:
        transaction.RollBack()
        raise

    status = transaction.Commit()
    if status != DB.TransactionStatus.Committed:
        result.failed.extend((p, rt.SKIP_ROLLED_BACK) for p in result.written)
        result.written = []
        result.committed = False
        return result

    result.committed = True
    return result
