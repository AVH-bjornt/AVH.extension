# -*- coding: utf-8 -*-
"""
Runs the locale fallback AVH added to Diagnose Invisibility against the
real ResourceDictionary files, with pyRevit stubbed out.

The tool itself is pyRevit's and is not tested here. What is tested is the
stand in for `applocales.get_locale_string_from_xaml`, which exists only
because that function is on pyRevit's develop branch and not in the
release AVH runs. The first click raised AttributeError.

This matters more than its size suggests. A lookup that raises is a dead
button and you find out at once. A lookup that quietly returns the key
gives you a dialog reading `select_view_title` instead of "Select View",
which looks like a translation bug rather than a broken fallback.

Run outside Revit:

    python test_diagnose_fallback_harness.py
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
    pyrevit.DB = Namespace()
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


# ---------------------------------------------------------------------------

failed = [entry for entry in results if not entry[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}{1}".format(
            name, u"  [{0}]".format(detail) if detail else u""))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
