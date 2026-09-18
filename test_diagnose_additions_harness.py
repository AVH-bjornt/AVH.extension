# -*- coding: utf-8 -*-
"""
Covers the two things AVH added to pyRevit's Diagnose Invisibility.

The tool itself is pyRevit's and is not tested here. These are:

1. The stand in for `applocales.get_locale_string_from_xaml`, which
   exists only because that function is on pyRevit's develop branch and
   not in the release AVH runs. The first click raised AttributeError.

   A lookup that raises is a dead button and you find out at once. One
   that quietly returns the key gives a dialog reading
   `select_view_title` instead of "Select View", which reads as a
   translation bug rather than a broken fallback.

2. `diagnose_partial_visibility`, which reports a visible element whose
   geometry draws on a hidden category.

   It exists because on 18 Sep 2026 this tool reported a toilet visible,
   and was right, while the toilet was not on screen. Its Fine geometry
   was imported into the family and drew on Imports in Families, a
   category nothing in the tool looked at. Being visible and being fully
   drawn are different things.

Run outside Revit:

    python test_diagnose_additions_harness.py
"""

import importlib.util
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(HERE, "AVH.tab", "Tools.panel",
                      "Diagnose Invisibility.pushbutton")
SCRIPT = os.path.join(BUNDLE, "script.py")

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def safely(function, *args):
    """Call it, and turn a raise into a value that fails a check.

    A check that raises aborts the suite and hides every check after it,
    which this codebase has paid for once already. The mutation that takes
    the getattr indirection back out raises exactly where the real button
    did, so this is not hypothetical.
    """
    try:
        return function(*args)
    except Exception as exc:
        return u"<raised {0}>".format(exc)


class Namespace(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


INVALID_ID = "invalid-element-id"


class FakeOptions(object):
    """Revit's geometry options. The View matters: it carries the detail
    level, and which geometry exists at all depends on it."""

    def __init__(self):
        self.View = None


class FakeCategory(object):
    def __init__(self, category_id, name):
        self.Id = category_id
        self.Name = name


class FakeGraphicsStyle(object):
    def __init__(self, category):
        self.GraphicsStyleCategory = category


class FakeGeometryObject(object):
    """One piece of geometry, optionally a family instance holding more."""

    def __init__(self, style_id=None, nested=None, style_raises=False):
        self._style_id = style_id
        self._nested = nested
        self.style_raises = style_raises

    @property
    def GraphicsStyleId(self):
        if self.style_raises:
            raise Exception("no graphics style on this object")
        return self._style_id

    def GetInstanceGeometry(self):
        if self._nested is None:
            raise Exception("not a geometry instance")
        return list(self._nested)


class FakeGeometryDocument(object):
    def __init__(self, styles):
        self.styles = dict(styles)

    def GetElement(self, style_id):
        return self.styles.get(style_id)


class FakeGeometryElement(object):
    def __init__(self, document, category, geometry, raises=False):
        self.Document = document
        self.Category = category
        self._geometry = geometry
        self.raises = raises
        self.options_seen = []

    def get_Geometry(self, options):
        self.options_seen.append(options)
        if self.raises:
            raise Exception("geometry unavailable")
        return list(self._geometry)


class FakeGeometryView(object):
    def __init__(self, hidden=()):
        self.hidden = set(hidden)

    def GetCategoryHidden(self, category_id):
        return category_id in self.hidden


class FakeApplocale(object):
    def __init__(self, codes):
        self.locale_codes = list(codes)


# The locale the stub reports. Scenarios reassign it.
CURRENT = {"applocale": None, "raises": False}


def get_current_applocale():
    if CURRENT["raises"]:
        raise Exception("locale unavailable")
    return CURRENT["applocale"]


def load_script():
    """Import the real script with pyRevit faked, without running main()."""
    applocales = types.ModuleType("applocales")
    applocales.get_current_applocale = get_current_applocale
    # Deliberately no get_locale_string_from_xaml: that absence is the
    # whole reason the fallback exists.

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.revit = Namespace(doc=None, uidoc=None)
    pyrevit.DB = Namespace(
        Options=FakeOptions,
        ElementId=Namespace(InvalidElementId=INVALID_ID),
    )
    pyrevit.forms = Namespace(alert=lambda *a, **k: None)
    pyrevit.script = Namespace(
        get_logger=lambda: Namespace(error=lambda *a: None,
                                     debug=lambda *a: None,
                                     info=lambda *a: None,
                                     warning=lambda *a: None),
        get_output=lambda: Namespace(print_md=lambda *a: None,
                                     print_html=lambda *a: None,
                                     linkify=lambda *a, **k: u""),
        get_bundle_file=lambda name: os.path.join(BUNDLE, name),
    )

    coreutils = types.ModuleType("pyrevit.coreutils")
    coreutils.applocales = applocales
    revit_mod = types.ModuleType("pyrevit.revit")
    revit_mod.query = Namespace()
    compat = types.ModuleType("pyrevit.compat")
    compat.get_elementid_value_func = lambda: (lambda eid: eid)

    injected = {
        "pyrevit": pyrevit,
        "pyrevit.coreutils": coreutils,
        "pyrevit.coreutils.applocales": applocales,
        "pyrevit.revit": revit_mod,
        "pyrevit.compat": compat,
    }
    saved = dict((k, sys.modules.get(k)) for k in injected)
    sys.modules.update(injected)
    try:
        spec = importlib.util.spec_from_file_location(
            "diagnose_invisibility_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


CURRENT["applocale"] = FakeApplocale(["en_us"])
mod = load_script()
base = os.path.join(BUNDLE, "resources.xaml")

# --- the files really do parse ---------------------------------------------

check("en_us: a known key resolves to its English string",
      mod._locale_string_from_xaml(base, "select_view_title")
      == u"Select View",
      repr(mod._locale_string_from_xaml(base, "select_view_title")))

check("en_us: a formatting key keeps its placeholders",
      u"{}" in mod._locale_string_from_xaml(base, "summary_line"))

check("an unknown key comes back unchanged",
      mod._locale_string_from_xaml(base, "no_such_key_at_all")
      == "no_such_key_at_all")

# --- other locales ---------------------------------------------------------

CURRENT["applocale"] = FakeApplocale(["de_de"])
german = mod._locale_string_from_xaml(base, "select_view_title")
check("de_de: the German file is read, not the English one",
      german != u"Select View" and german != "select_view_title",
      repr(german))

CURRENT["applocale"] = FakeApplocale(["is_is"])
check("a locale with no file of its own falls back to en_us",
      mod._locale_string_from_xaml(base, "select_view_title")
      == u"Select View")

CURRENT["applocale"] = FakeApplocale(["is_is", "de_de"])
check("the first locale code with a file wins over later ones",
      mod._locale_string_from_xaml(base, "select_view_title") == german,
      repr(mod._locale_string_from_xaml(base, "select_view_title")))

# --- the locale lookup itself failing --------------------------------------

CURRENT["applocale"] = None
check("no locale at all still answers in English",
      mod._locale_string_from_xaml(base, "select_view_title")
      == u"Select View")

CURRENT["raises"] = True
check("a locale lookup that raises still answers in English",
      mod._locale_string_from_xaml(base, "select_view_title")
      == u"Select View")
CURRENT["raises"] = False
CURRENT["applocale"] = FakeApplocale(["en_us"])

# --- a broken file is not a crash ------------------------------------------

broken = os.path.join(HERE, "_broken_resources.xaml")
broken_dict = os.path.join(HERE, "_broken_resources.ResourceDictionary.en_us.xaml")
handle = io.open(broken_dict, "w", encoding="utf-8")
try:
    handle.write(u"<ResourceDictionary><not closed")
finally:
    handle.close()
try:
    check("a malformed resource file gives the key back, not an exception",
          mod._locale_string_from_xaml(broken, "select_view_title")
          == "select_view_title")
finally:
    os.remove(broken_dict)

check("a missing resource file gives the key back",
      mod._locale_string_from_xaml(
          os.path.join(HERE, "_absent"), "select_view_title")
      == "select_view_title")

# --- _t, which is what the tool actually calls ------------------------------

check("_t: returns the localised string",
      safely(mod._t, "select_view_title") == u"Select View",
      repr(safely(mod._t, "select_view_title")))
check("_t: an unknown key with a default returns the default",
      safely(mod._t, "no_such_key_at_all", "fallback text")
      == "fallback text")
check("_t: an unknown key with no default returns the key",
      safely(mod._t, "no_such_key_at_all") == "no_such_key_at_all")

# --- the reason the fallback is wired in at all -----------------------------

# _t must not go straight to pyRevit's own function, because on this
# release it is not there. Removing the getattr indirection fails here.
source = io.open(SCRIPT, encoding="utf-8").read()
check("_t looks the function up rather than calling it directly",
      'getattr(applocales, "get_locale_string_from_xaml"' in source)
check("and prefers pyRevit's own when it exists",
      "_locale_string_from_xaml)" in source)



# --------------------------------------------------------------------------
# diagnose_partial_visibility: visible is not the same as fully drawn
# --------------------------------------------------------------------------

PLUMBING = 101
IMPORTS_IN_FAMILIES = 99
CERAMIC = 77


def toilet(hidden=(), geometry=None, raises=False, own=PLUMBING):
    """A Plumbing Fixture whose Fine geometry came from an imported DWG."""
    plumbing = FakeCategory(PLUMBING, u"Plumbing Fixtures")
    imports = FakeCategory(IMPORTS_IN_FAMILIES, u"Imports in Families")
    ceramic = FakeCategory(CERAMIC, u"Ceramic")
    document = FakeGeometryDocument({
        "style-plumbing": FakeGraphicsStyle(plumbing),
        "style-imports": FakeGraphicsStyle(imports),
        "style-imports-cut": FakeGraphicsStyle(imports),
        "style-ceramic": FakeGraphicsStyle(ceramic),
        "style-orphan": None,
    })
    if geometry is None:
        geometry = [FakeGeometryObject("style-plumbing"),
                    FakeGeometryObject("style-imports")]
    own_category = FakeCategory(own, u"Plumbing Fixtures") if own else None
    element = FakeGeometryElement(document, own_category, geometry,
                                 raises=raises)
    return element, FakeGeometryView(hidden=hidden)


# The case that started it. The element is visible, Imports in Families is
# off, and the fixture is not on screen.
element, view = toilet(hidden=[IMPORTS_IN_FAMILIES])
notes, short = mod.diagnose_partial_visibility(element, view)
check("partial: the hidden geometry category is reported",
      len(notes) == 1, repr(notes))
check("partial: and it is named",
      notes and u"Imports in Families" in notes[0], repr(notes))
check("partial: with a short code for grouping",
      short == ["Geometry category hidden"], repr(short))

# Nothing hidden, nothing to say. A tool that warns on every element is a
# tool people stop reading.
element, view = toilet()
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: silent when no geometry category is hidden",
      notes == [], repr(notes))

# The element's own category is somebody else's job. If it were hidden the
# element would not be visible and diagnose_invisibility would have said so.
element, view = toilet(hidden=[PLUMBING])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: the element's own category is not reported here",
      notes == [], repr(notes))

# A subcategory of its own category is a different matter and does count.
element, view = toilet(
    hidden=[CERAMIC],
    geometry=[FakeGeometryObject("style-plumbing"),
              FakeGeometryObject("style-ceramic")])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: a hidden subcategory is reported",
      len(notes) == 1 and u"Ceramic" in notes[0], repr(notes))

# Geometry inside a nested family still counts.
element, view = toilet(
    hidden=[IMPORTS_IN_FAMILIES],
    geometry=[FakeGeometryObject(
        "style-plumbing",
        nested=[FakeGeometryObject("style-imports")])])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: geometry one family deep is found",
      len(notes) == 1, repr(notes))

# But not forever. The cap is what stops a pathological family hanging the
# button, and a cap nothing tests is a cap that quietly becomes wrong.
deep = FakeGeometryObject("style-imports")
for _ in range(6):
    deep = FakeGeometryObject("style-plumbing", nested=[deep])
element, view = toilet(hidden=[IMPORTS_IN_FAMILIES], geometry=[deep])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: the depth cap holds and does not hang",
      notes == [], repr(notes))

# A category has a projection style and a cut style. Reporting it twice
# reads as two separate faults.
element, view = toilet(
    hidden=[IMPORTS_IN_FAMILIES],
    geometry=[FakeGeometryObject("style-imports"),
              FakeGeometryObject("style-imports-cut")])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: one category reported once, not once per style",
      len(notes) == 1, repr(notes))

# The view goes into the geometry options, because the detail level lives
# there and which geometry exists at all depends on it. This is the whole
# reason the toilet drew at Medium and not at Fine.
element, view = toilet(hidden=[IMPORTS_IN_FAMILIES])
mod.diagnose_partial_visibility(element, view)
check("partial: geometry is read at the view's own detail level",
      element.options_seen and element.options_seen[0].View is view)

# Nothing here may raise. This runs on the happy path, on elements that
# are fine, and a diagnostic that crashes on a healthy model is worse
# than one that says nothing.
element, view = toilet(hidden=[IMPORTS_IN_FAMILIES], raises=True)
check("partial: unreadable geometry is silence, not a crash",
      safely(mod.diagnose_partial_visibility, element, view) == ([], []))

element, view = toilet(
    hidden=[IMPORTS_IN_FAMILIES],
    geometry=[FakeGeometryObject(style_raises=True),
              FakeGeometryObject("style-imports")])
check("partial: one unreadable piece does not lose the others",
      safely(mod.diagnose_partial_visibility, element, view)[0] != [])

element, view = toilet(
    hidden=[IMPORTS_IN_FAMILIES],
    geometry=[FakeGeometryObject("style-orphan"),
              FakeGeometryObject("style-imports")])
check("partial: a style with no category is skipped, not fatal",
      safely(mod.diagnose_partial_visibility, element, view)[0] != [])

element, view = toilet(hidden=[IMPORTS_IN_FAMILIES], own=None)
check("partial: an element with no category still reports",
      safely(mod.diagnose_partial_visibility, element, view)[0] != [])

element, view = toilet(
    hidden=[IMPORTS_IN_FAMILIES],
    geometry=[FakeGeometryObject(None), FakeGeometryObject(INVALID_ID),
              FakeGeometryObject("style-imports")])
notes, _short = mod.diagnose_partial_visibility(element, view)
check("partial: missing and invalid style ids are skipped",
      len(notes) == 1, repr(notes))

# And it has to actually be wired in. The check above all pass on a
# function nobody calls.
source = io.open(SCRIPT, encoding="utf-8").read()
check("partial: main() calls it on the visible branch",
      "diagnose_partial_visibility(" in source.split("def main(")[-1])
check("partial: its string is in the en_us resource file",
      "note_geometry_category_hidden" in io.open(
          os.path.join(BUNDLE, "resources.ResourceDictionary.en_us.xaml"),
          encoding="utf-8").read())
check("partial: and _t is used rather than a hardcoded English string",
      '_t(\n                "note_geometry_category_hidden"' in source)


# ---------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
