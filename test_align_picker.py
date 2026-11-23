# -*- coding: utf-8 -*-
"""
The placed-view picker of Align Views and Align Titles: the sheet
collection dropdown.

The fake SelectFromList behaves like pyRevit 7.1's, read in the
installed source: a dict of groups shows a dropdown, rows that are
already TemplateListItems are used as they are, and a multiselect
returns ticked rows from every group, each row once (by identity).
A second fake behaves like an older pyRevit that returns only the
ticked rows of the group on screen.

Run outside Revit:

    python test_align_picker.py
"""

import os
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_viewports import model, picker  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeRow(object):
    """pyRevit's TemplateListItem: wraps an item, carries a tick."""

    def __init__(self, orig_item, checked=False, checkable=True,
                 name_attr=None):
        self.item = orig_item
        self.state = checked
        self.checkable = checkable


class FakeSelect(object):
    """`ticks`: labels the user ticks, under whichever group shows them.
    `shown`: the group on screen when the button is pressed. `older`:
    return only that group's ticks, like pyRevit before 7.1."""

    def __init__(self, ticks=(), pick_one=None, shown=None, older=False,
                 cancel=False):
        self.ticks = set(ticks)
        self.pick_one = pick_one
        self.shown = shown
        self.older = older
        self.cancel = cancel
        self.calls = []

    def rows(self, items):
        if isinstance(items, dict):
            seen, rows = set(), []
            for group in items.values():
                for row in group:
                    if id(row) not in seen:
                        seen.add(id(row))
                        rows.append(row)
            return rows
        return [FakeRow(i) if not isinstance(i, FakeRow) else i
                for i in items]

    def show(self, items, multiselect=False, **kwargs):
        self.calls.append((items, multiselect, kwargs))
        if self.cancel:
            return None
        rows = self.rows(items)
        if not multiselect:
            for row in rows:
                if row.item == self.pick_one:
                    return row.item
            return None
        for row in rows:
            if row.item in self.ticks:
                row.state = True
        if self.older and isinstance(items, dict):
            group = items[self.shown or kwargs.get("default_group")]
            return [r.item for r in group if r.state] or None
        return [r.item for r in rows if r.state] or None


def forms_with(select, template=True):
    forms = Namespace(SelectFromList=select)
    if template:
        forms.TemplateListItem = FakeRow
    return forms


def placed(label, collection):
    return Namespace(label=label, collection=collection)


SITE = [placed(u"A-101  Grunnmynd  /  E01", u"Arkitekt"),
        placed(u"A-102  Grunnmynd  /  E02", u"Arkitekt"),
        placed(u"B-201  Snið  /  S1", u"Burðarvirki"),
        placed(u"X-001  Yfirlit  /  Afstaða", None)]


# --------------------------------------------------------------------------
# collection_groups
# --------------------------------------------------------------------------

groups = model.collection_groups([(p.label, p.collection) for p in SITE])
check("groups: all first, collections by name, then none",
      [g[0] for g in groups] == [model.ALL_SHEETS, u"Arkitekt",
                                 u"Burðarvirki", model.NO_COLLECTION],
      [g[0] for g in groups])
check("groups: all holds every label in list order",
      groups[0][1] == [p.label for p in SITE])
check("groups: each collection holds its own",
      groups[1][1] == [SITE[0].label, SITE[1].label] and
      groups[2][1] == [SITE[2].label] and groups[3][1] == [SITE[3].label])
check("groups: no collections, no groups",
      model.collection_groups([(u"a", None), (u"b", None)]) is None)
check("groups: no 'not in a collection' group when every sheet is in one",
      [g[0] for g in model.collection_groups([(u"a", u"K")])] ==
      [model.ALL_SHEETS, u"K"])
check("groups: names sorted without regard to case",
      [g[0] for g in model.collection_groups(
          [(u"a", u"b-hópur"), (u"b", u"A-hópur")])][1:] ==
      [u"A-hópur", u"b-hópur"])
check("unique: order kept, each once",
      model.unique([3, 1, 3, 2, 1]) == [3, 1, 2])


# --------------------------------------------------------------------------
# collection_name
# --------------------------------------------------------------------------

class FakeDoc(object):
    def __init__(self, elements):
        self.elements = elements

    def GetElement(self, element_id):
        return self.elements.get(element_id)


doc = FakeDoc({7: Namespace(Name=u"Arkitekt")})
check("collection name: read from the sheet's collection",
      picker.collection_name(doc, Namespace(SheetCollectionId=7)) ==
      u"Arkitekt")
check("collection name: not in one (invalid id gives no element)",
      picker.collection_name(doc, Namespace(SheetCollectionId=-1)) is None)
check("collection name: Revit before 2025 has no SheetCollectionId",
      picker.collection_name(doc, Namespace()) is None)


# --------------------------------------------------------------------------
# pick
# --------------------------------------------------------------------------

select = FakeSelect(ticks=[SITE[0].label, SITE[2].label])
chosen = picker.pick(forms_with(select), SITE, True, u"t", u"b")
items, multi, options = select.calls[0]
check("dropdown: the list is given as groups",
      isinstance(items, OrderedDict) and list(items) ==
      [model.ALL_SHEETS, u"Arkitekt", u"Burðarvirki", model.NO_COLLECTION],
      items)
check("dropdown: titled, opens on all sheets, groups kept in order",
      options.get("group_selector_title") == u"Sheet collection:" and
      options.get("default_group") == model.ALL_SHEETS and
      options.get("sort_groups") == "unsorted", options)
check("dropdown: groups share row objects, so ticks carry across",
      items[u"Arkitekt"][0] is items[model.ALL_SHEETS][0])
check("dropdown: rows are checkable in a multiselect",
      all(r.checkable for r in items[model.ALL_SHEETS]))
check("pick: the ticked views, each once",
      [p.label for p in chosen] == [SITE[0].label, SITE[2].label],
      [p.label for p in chosen])

# Older pyRevit: only the shown group is returned; the rows' ticks still
# bring back a view ticked under another collection.
select = FakeSelect(ticks=[SITE[0].label, SITE[2].label],
                    shown=u"Burðarvirki", older=True)
chosen = picker.pick(forms_with(select), SITE, True, u"t", u"b")
check("older pyRevit: a tick under another collection is kept",
      sorted(p.label for p in chosen) == sorted([SITE[0].label,
                                                 SITE[2].label]),
      [p.label for p in chosen])

# Single pick, for the source view.
select = FakeSelect(pick_one=SITE[2].label)
chosen = picker.pick(forms_with(select), SITE, False, u"t", u"b")
check("single: the picked view", chosen is SITE[2])
check("single: also grouped",
      isinstance(select.calls[0][0], OrderedDict))
check("single: rows not checkable",
      not any(r.checkable for r in select.calls[0][0][model.ALL_SHEETS]))

# Cancelled.
select = FakeSelect(cancel=True)
check("cancel: nothing, as a list for multiselect",
      picker.pick(forms_with(select), SITE, True, u"t", u"b") == [])
check("cancel: None for single",
      picker.pick(forms_with(select), SITE, False, u"t", u"b") is None)

# No collections: the plain list as before, no dropdown options.
plain = [placed(p.label, None) for p in SITE]
select = FakeSelect(ticks=[SITE[1].label])
chosen = picker.pick(forms_with(select), plain, True, u"t", u"b")
check("no collections: a plain list of labels, no dropdown",
      select.calls[0][0] == [p.label for p in plain] and
      u"group_selector_title" not in select.calls[0][2], select.calls[0])
check("no collections: the ticked view", [p.label for p in chosen] ==
      [SITE[1].label])

# A forms without TemplateListItem: plain list, nothing breaks.
select = FakeSelect(ticks=[SITE[1].label])
chosen = picker.pick(forms_with(select, template=False), SITE, True,
                     u"t", u"b")
check("no TemplateListItem: a plain list", isinstance(select.calls[0][0],
                                                     list))

# --------------------------------------------------------------------------

check("harness: a wrong group order is caught",
      [model.NO_COLLECTION, model.ALL_SHEETS] !=
      [model.ALL_SHEETS, model.NO_COLLECTION])
check("harness: check stores a real boolean", results[-1][1] is True)

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
