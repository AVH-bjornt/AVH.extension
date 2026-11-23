# -*- coding: utf-8 -*-
"""The placed-view picker shared by Align Views and Align Titles.

With sheet collections in the model (Revit 2025), the list gets a
dropdown that shows one collection at a time. Every group is built from
the same row objects, so a view ticked under one collection is ticked
under "[All sheets]" too, and is returned once. pyRevit 7.1's
SelectFromList returns ticks from every group, not just the one shown
(`_get_all_ctx`, read in the installed source); the rows' own `state` is
read as well, so an older pyRevit that returns only the shown group
still keeps ticks made under another.

Takes `forms` as an argument so the scripts' own (mocked) import is used.
"""

from collections import OrderedDict

from avh_schedules.compat import to_text
from avh_viewports import model


def collection_name(doc, sheet):
    """The name of the sheet's collection, or None: not in one, or a
    Revit before 2025 without `ViewSheet.SheetCollectionId`."""
    try:
        collection = doc.GetElement(sheet.SheetCollectionId)
    except BaseException:
        return None
    if collection is None:
        return None
    try:
        return to_text(collection.Name) or None
    except BaseException:
        return None


def pick(forms, placed, multiselect, title, button):
    """The chosen placed views (a list when multiselect, else one or
    None). Each item of `placed` has `.label` and `.collection`."""
    by_label = dict((p.label, p) for p in placed)
    labels = [p.label for p in placed]
    items = labels
    rows = []
    options = {}
    groups = model.collection_groups([(p.label, p.collection)
                                      for p in placed])
    template = getattr(forms, "TemplateListItem", None)
    if groups and template is not None:
        row_of = OrderedDict((text, template(text, checkable=multiselect))
                             for text in labels)
        rows = list(row_of.values())
        items = OrderedDict((name, [row_of[text] for text in group])
                            for name, group in groups)
        options = dict(group_selector_title=u"Sheet collection:",
                       default_group=model.ALL_SHEETS,
                       sort_groups="unsorted")
    answer = forms.SelectFromList.show(
        items, multiselect=multiselect, title=title, button_name=button,
        **options)
    if not answer:
        return [] if multiselect else None
    if not isinstance(answer, list):
        answer = [answer]
    answer = [to_text(a) for a in answer]
    if multiselect:
        answer += [to_text(row.item) for row in rows
                   if getattr(row, "state", False)]
    chosen = [by_label[a] for a in model.unique(answer) if a in by_label]
    if multiselect:
        return chosen
    return chosen[0] if chosen else None
