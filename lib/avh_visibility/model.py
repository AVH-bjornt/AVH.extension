# -*- coding: utf-8 -*-
"""Working out what hiding a category in a view template would do.

No Revit here on purpose. Everything this tool decides is bookkeeping
over plain dictionaries: which templates control the visibility
parameter the category needs, which ones refuse the category outright,
and how many views each answer reaches. All of that is testable outside
Revit, and it is where the tool can be wrong in a way that costs a
drawing set.

A template is a dict:

    {"key": 41823, "name": u"AVH Plan 1:100", "views": 37,
     "controls": set([MODEL]), "blocked": set([cat_key, ...]),
     "active": True}

`controls` holds the kinds whose visibility parameter the template
already takes over. `blocked` holds the categories that particular view
will not hide, which Revit answers per view rather than per document.

A category is a dict:

    {"key": -2000011, "name": u"Walls", "kind": MODEL}
"""

from avh_schedules.compat import to_text

# The two kinds of category a view template controls through separate
# parameters. Anything else, analytical and internal categories, is not
# reachable this way at all.
MODEL = u"model"
ANNOTATION = u"annotation"
OTHER = u"other"

# Template names are long and the picker has to stay readable.
MAX_LABEL = 60

# A model with ninety templates is exactly the model where a ninety line
# report helps nobody.
MAX_LISTED = 25


def key_of(value):
    """A hashable stand in for an ElementId.

    `Value` is what identifies it on Revit 2024 and newer, `IntegerValue`
    before that. Reading both keeps this working whether the caller
    passes ids or plain numbers, which is what lets the tests run
    outside Revit.
    """
    marker = getattr(value, "Value", None)
    if marker is None:
        marker = getattr(value, "IntegerValue", None)
    if marker is None:
        return value
    return marker


def truncate(text, limit=MAX_LABEL):
    """Shorten for display, marking that it was shortened."""
    value = to_text(text)
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + u"…"


def merge_categories(entries):
    """One entry per category, ordered by name.

    `entries` is an iterable of (key, name, kind). A selection of forty
    doors carries the Doors category forty times, and hiding it forty
    times would be forty writes and a report that overstates the change.
    """
    seen = {}
    order = []
    for key, name, kind in entries:
        marker = key_of(key)
        if marker in seen:
            continue
        seen[marker] = True
        order.append({"key": marker, "name": to_text(name), "kind": kind})
    order.sort(key=lambda category: (category["name"], category["key"]))
    return order


def needed_kinds(categories):
    """The visibility parameters these categories actually need."""
    return set(category["kind"] for category in categories
               if category["kind"] != OTHER)


def supported(categories):
    return [category for category in categories
            if category["kind"] != OTHER]


def unsupported(categories):
    return [category for category in categories
            if category["kind"] == OTHER]


def missing_kinds(template, categories):
    """Kinds this template would have to take over before a write lands.

    This is the whole reason the tool cannot simply write and hope. A
    template that does not control V/G takes the write and the views
    using it carry on showing the category, so the tool would report a
    change nobody can see.
    """
    return needed_kinds(categories) - set(template.get("controls", ()))


def split_for(template, categories):
    """(writable, blocked) for one template.

    Blocked is Revit's own answer to `CanCategoryBeHidden`, which is per
    view. Passing a blocked category to `SetCategoryHidden` raises and
    takes the transaction with it, so it is filtered rather than tried.
    """
    blocked_keys = set(template.get("blocked", ()))
    writable = []
    blocked = []
    for category in supported(categories):
        if category["key"] in blocked_keys:
            blocked.append(category)
        else:
            writable.append(category)
    return writable, blocked


def plan(templates, categories, enable=False):
    """What the run would do, split by why.

    `enable` is the answer to the offer. False is the dry run: templates
    that do not control the parameter sit in `pending` and nothing is
    written to them. True moves them into `ready` and records the kinds
    that have to be switched on first.

    Returns a dict of lists:

        ready       [(template, categories, kinds_to_enable)]
        pending     [(template, categories, kinds_to_enable)]
        blocked     [(template, categories)]
        unsupported [categories]
    """
    result = {"ready": [], "pending": [], "blocked": [],
              "unsupported": unsupported(categories)}

    for template in templates:
        writable, blocked = split_for(template, categories)
        if blocked:
            result["blocked"].append((template, blocked))
        if not writable:
            continue

        missing = missing_kinds(template, writable)
        entry = (template, writable, missing)
        if not missing:
            result["ready"].append((template, writable, set()))
        elif enable:
            result["ready"].append(entry)
        else:
            result["pending"].append(entry)

    return result


def views_touched(entries):
    """How many views the listed templates reach, counted once each.

    A view uses one template, so templates cannot double count, but the
    number is the whole point of the confirmation and it should not
    depend on that being remembered.
    """
    seen = set()
    total = 0
    for entry in entries:
        template = entry[0]
        if template["key"] in seen:
            continue
        seen.add(template["key"])
        total += int(template.get("views", 0))
    return total


def writes_count(entries):
    """One write per category per template."""
    return sum(len(entry[1]) for entry in entries)


def template_labels(templates, categories):
    """Labels for the picker, and the map back.

    The label carries the view count, because picking a template that
    thirty seven views use is a different decision from picking one that
    two use, and it carries a marker when the template does not yet
    control the parameter, so the offer later is not a surprise.

    Returns (labels, mapping). The mapping is label to template, because
    a truncated label cannot be parsed back into the name it came from.
    """
    labels = []
    mapping = {}
    for template in templates:
        note = u""
        if missing_kinds(template, categories):
            note = u"  [does not control V/G yet]"
        if template.get("active"):
            note = note + u"  [active view]"
        label = u"{0}  ({1} view(s)){2}".format(
            truncate(template["name"]), int(template.get("views", 0)), note)
        suffix = 2
        while label in mapping and mapping[label] is not template:
            label = u"{0}  ({1} view(s)){2} [{3}]".format(
                truncate(template["name"]), int(template.get("views", 0)),
                note, suffix)
            suffix += 1
        labels.append(label)
        mapping[label] = template
    return labels, mapping


def order_templates(templates):
    """Active view's template first, then by name.

    The template you are looking at is the one you almost always mean,
    and a picker that buries it under eighty alphabetical siblings is a
    picker you scroll every single time.
    """
    return sorted(templates,
                  key=lambda template: (0 if template.get("active") else 1,
                                        to_text(template["name"])))


def category_names(categories):
    return u", ".join(category["name"] for category in categories)


def offer_text(pending, action):
    """The wording of the destructive offer.

    Named parameters and named templates, with the view count, because a
    dialog that says "some views may be affected" is not a warning. The
    count is the number that should stop somebody.
    """
    lines = [
        u"{0} of the templates you picked do not control Visibility / "
        u"Graphics yet, so {1} them there would change nothing.".format(
            len(pending), action),
        u"",
        u"Switching that on makes each template take over category "
        u"visibility for every view that uses it. Whatever those views "
        u"show or hide today is replaced by the template's own settings, "
        u"and that cannot be undone by unticking the box afterwards.",
        u"",
    ]
    for template, categories, kinds in pending[:MAX_LISTED]:
        lines.append(u"  {0}  -  {1} view(s)  -  {2}".format(
            template["name"], int(template.get("views", 0)),
            u", ".join(sorted(kinds))))
    if len(pending) > MAX_LISTED:
        lines.append(u"  and {0} more".format(len(pending) - MAX_LISTED))
    lines.append(u"")
    lines.append(u"{0} view(s) would be affected in total.".format(
        views_touched(pending)))
    return u"\n".join(lines)
