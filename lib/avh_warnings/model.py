# -*- coding: utf-8 -*-
"""Grouping and labelling of Revit warnings.

No Revit here on purpose, so the grouping, the ordering and the label
round trip can be tested outside it. Everything in this module takes
plain descriptions and plain element ids.
"""

from avh_schedules.compat import to_text

# Revit warning texts run long. The picker needs a label short enough to
# read at a glance, and the label has to map back to the full
# description afterwards, which is why the mapping is returned rather
# than the label being parsed back apart.
MAX_LABEL = 80

# How many lines of the summary go to the output window before it is cut
# short. A model with a hundred kinds of warning is exactly the model
# where a hundred line report helps nobody.
MAX_LISTED = 25


def truncate(text, limit=MAX_LABEL):
    """Shorten for display, marking that it was shortened."""
    value = to_text(text)
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + u"…"


def merge_by_description(entries):
    """Gather warnings that share a description into one group.

    `entries` is an iterable of (description, ids). Ids are deduplicated
    across the whole group, because one element can be named by several
    warnings of the same kind and counting it twice would overstate how
    much is wrong.

    Returns a list of (description, ids) ordered by count descending,
    then by description, so the noisiest kind is first and the order is
    stable between runs.
    """
    order = []
    grouped = {}
    seen = {}

    for description, ids in entries:
        key = to_text(description)
        if key not in grouped:
            grouped[key] = []
            seen[key] = set()
            order.append(key)
        for element_id in ids:
            marker = id_key(element_id)
            if marker in seen[key]:
                continue
            seen[key].add(marker)
            grouped[key].append(element_id)

    groups = [(key, grouped[key]) for key in order]
    groups.sort(key=lambda group: (-len(group[1]), group[0]))
    return groups


def id_key(element_id):
    """A hashable stand in for an ElementId.

    `ElementId` is hashable in the API, but its `Value` is what actually
    identifies it, and reading that keeps the dedup working whether the
    caller passes ids or plain numbers.
    """
    value = getattr(element_id, "Value", None)
    if value is None:
        value = getattr(element_id, "IntegerValue", None)
    if value is None:
        return element_id
    return value


def picker_labels(groups):
    """Labels for the shift click picker, and the map back.

    Returns (labels, mapping). The mapping is label to description,
    because a truncated label cannot be parsed back into the description
    it came from. Two descriptions that truncate to the same text keep
    their counts, which differ, so the labels stay distinct.
    """
    labels = []
    mapping = {}
    for description, ids in groups:
        label = u"{0}  ({1})".format(truncate(description), len(ids))
        suffix = 2
        while label in mapping and mapping[label] != description:
            label = u"{0}  ({1}) [{2}]".format(
                truncate(description), len(ids), suffix)
            suffix += 1
        labels.append(label)
        mapping[label] = description
    return labels, mapping


def ids_for(groups, descriptions):
    """Every id belonging to the named descriptions, deduplicated."""
    wanted = set(to_text(description) for description in descriptions)
    ids = []
    seen = set()
    for description, group_ids in groups:
        if description not in wanted:
            continue
        for element_id in group_ids:
            marker = id_key(element_id)
            if marker in seen:
                continue
            seen.add(marker)
            ids.append(element_id)
    return ids


def all_ids(groups):
    return ids_for(groups, [description for description, _ in groups])
