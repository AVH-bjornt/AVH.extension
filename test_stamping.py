# -*- coding: utf-8 -*-
"""
Stamp Element IDs: the naming rules, the planning and the writing, against
a mocked Revit whose parameters keep their values (so running twice must
write nothing the second time, which is checked on state).

Run: python test_stamping.py
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_schedules import stamp, stamper                  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --- names and values -------------------------------------------------------

for name in (u"Element ID", u"ElementID", u"Element_Id", u"element id",
             u"AVH Element ID", u"Element-ID"):
    check("'{0}' is the Element ID parameter".format(name),
          stamp.is_stamp_name(name))
for name in (u"ID", u"Mark", u"Type Id", u"Element", u"IfcGUID", u""):
    check("'{0}' is not".format(name), not stamp.is_stamp_name(name))

check("a text parameter gets the id as text",
      stamp.stamp_value(123456, u"String") == u"123456")
check("an integer parameter gets an int",
      stamp.stamp_value(123456, u"Integer") == 123456)
check("an id too big for an Integer parameter is None, not truncated",
      stamp.stamp_value(3000000000, u"Integer") is None)
check("other storage types are refused", stamp.stamp_value(1, u"other") is None)
check("already_stamped: text with spaces", stamp.already_stamped(
    u" 77 ", 77, u"String"))
check("already_stamped: wrong id", not stamp.already_stamped(
    u"78", 77, u"String"))
check("already_stamped: blank", not stamp.already_stamped(None, 77,
                                                          u"String"))


# --- the mocked Revit -------------------------------------------------------

class ElementId(object):
    def __init__(self, value):
        self.Value = value


class StorageType(object):
    String = "String"
    Integer = "Integer"
    Double = "Double"


class TransactionStatus(object):
    Committed = "Committed"
    RolledBack = "RolledBack"


class Param(object):
    def __init__(self, storage, value, read_only=False, refuse=False):
        self.StorageType = storage
        self.value = value
        self.IsReadOnly = read_only
        self.refuse = refuse
        self.sets = 0

    def AsString(self):
        return self.value

    def AsInteger(self):
        return self.value

    def Set(self, value):
        if self.refuse:
            return False
        self.value = value
        self.sets += 1
        return True


class Cat(object):
    def __init__(self, ident, name):
        self.Id = ElementId(ident)
        self.Name = name


class Element(object):
    def __init__(self, ident, param, category=None, group=-1):
        self.Id = ElementId(ident)
        self.GroupId = ElementId(group)
        self.Category = category or Cat(-2000023, u"Doors")
        self.param = param

    def LookupParameter(self, name):
        return self.param if name == u"Element ID" else None


class Tx(object):
    status = TransactionStatus.Committed
    opened = 0

    def __init__(self, doc, name):
        pass

    def Start(self):
        Tx.opened += 1

    def Commit(self):
        return Tx.status

    def RollBack(self):
        pass


class Doc(object):
    IsWorkshared = True


DB = types.SimpleNamespace(
    StorageType=StorageType, TransactionStatus=TransactionStatus,
    Transaction=Tx,
    CheckoutStatus=types.SimpleNamespace(OwnedByOtherUser="other"),
    WorksharingUtils=types.SimpleNamespace(
        GetCheckoutStatus=lambda doc, eid: OWNERS.get(eid.Value, "none")))
OWNERS = {}


def string_el(ident, value=u"", **kw):
    return Element(ident, Param(StorageType.String, value), **kw)


def run(elements, tx_status=TransactionStatus.Committed):
    Tx.status = tx_status
    plan = stamper.plan_stamps(DB, Doc(), elements, u"Element ID")
    return plan, stamper.execute_stamps(DB, Doc(), plan)


# blank elements are stamped with their own id
els = [string_el(101), string_el(102), string_el(103)]
plan, result = run(els)
check("every blank element is stamped with its own id",
      [e.param.value for e in els] == [u"101", u"102", u"103"])
check("the result says 3 written and committed",
      result.written == 3 and result.committed)

# running it again writes nothing
sets_before = [e.param.sets for e in els]
Tx.opened = 0
plan, result = run(els)
check("a second run finds everything already correct",
      plan.already == 3 and not plan.ready)
check("a second run Sets nothing and opens no transaction",
      [e.param.sets for e in els] == sets_before and Tx.opened == 0)

# a copied element carries its source's id: it is corrected
els = [string_el(201, u"200")]
plan, result = run(els)
check("a stale id (a copy) is overwritten with the element's own",
      els[0].param.value == u"201")

# the skips
OWNERS.clear()
OWNERS[302] = "other"
grouped = string_el(301, group=55)
owned = string_el(302)
readonly = Element(303, Param(StorageType.String, u"", read_only=True))
noparam = Element(304, None)
dbl = Element(305, Param(StorageType.Double, 0.0))
huge = Element(3000000000, Param(StorageType.Integer, 0))
good = string_el(306)
plan, result = run([grouped, owned, readonly, noparam, dbl, huge, good])
reasons = sorted(s[2] for s in plan.skipped)
check("each kind of element is skipped for its own reason",
      reasons == sorted([stamper.SKIP_GROUP, stamper.SKIP_OWNED,
                         stamper.SKIP_READ_ONLY, stamper.SKIP_NO_PARAMETER,
                         stamper.SKIP_STORAGE, stamper.SKIP_TOO_BIG]),
      str(reasons))
check("and the one good element is still written",
      good.param.value == u"306" and result.written == 1)
check("none of the skipped ones was written",
      grouped.param.sets == 0 and owned.param.sets == 0
      and readonly.param.sets == 0 and dbl.param.sets == 0
      and huge.param.sets == 0)
OWNERS.clear()

# an integer parameter holds a small id
el = Element(77, Param(StorageType.Integer, 0))
run([el])
check("an Integer parameter gets an int", el.param.value == 77)

# a Set that returns False, and a transaction Revit rolls back
bad = Element(401, Param(StorageType.String, u"", refuse=True))
ok = string_el(402)
plan, result = run([bad, ok])
check("a Set that returns False is a failure, the other still written",
      len(result.failed) == 1 and result.failed[0][1] == stamper.SKIP_REFUSED
      and result.written == 1)

els = [string_el(501)]
plan, result = run(els, tx_status=TransactionStatus.RolledBack)
check("a rolled back commit is not success",
      not result.committed and result.written == 0
      and result.failed and result.failed[0][1] == stamper.SKIP_ROLLED_BACK)
Tx.status = TransactionStatus.Committed

Tx.opened = 0
run([string_el(601, u"601")])
check("no transaction when nothing needs writing", Tx.opened == 0)


# --- finding the parameter --------------------------------------------------

class Definition(object):
    def __init__(self, name):
        self.Name = name


class Binding(object):
    def __init__(self, categories):
        self.Categories = categories


class Iterator(object):
    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.i = -1

    def MoveNext(self):
        self.i += 1
        return self.i < len(self.pairs)

    @property
    def Key(self):
        return self.pairs[self.i][0]

    @property
    def Current(self):
        return self.pairs[self.i][1]


class BindDoc(object):
    def __init__(self, pairs):
        self.ParameterBindings = types.SimpleNamespace(
            ForwardIterator=lambda: Iterator(pairs))


doors, windows = Cat(1, u"Doors"), Cat(2, u"Windows")
found = stamper.stamp_parameter_name(DB, BindDoc([
    (Definition(u"Mark"), Binding([doors])),
    (Definition(u"Element ID"), Binding([doors, windows])),
]))
check("the parameter and its categories are found among the bindings",
      found[0] == u"Element ID" and [c.Name for c in found[1]]
      == [u"Doors", u"Windows"], str(found))
check("a model with no such parameter says so",
      stamper.stamp_parameter_name(DB, BindDoc([
          (Definition(u"Mark"), Binding([doors]))])) == (None, []))

failed = [r for r in results if not r[1]]
for name, ok_, detail in results:
    if not ok_:
        print("FAIL  " + name + ("   [" + str(detail) + "]" if detail else ""))
print("{0} checks, {1} failed".format(len(results), len(failed)))
if failed:
    sys.exit(1)
print("PASS")
