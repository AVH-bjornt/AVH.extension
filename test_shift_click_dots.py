# -*- coding: utf-8 -*-
"""Every button that does something on shift-click shows pyRevit's dot.

pyRevit puts a dot after the name of a button that has a config.py, and
runs that file on shift-click. AVH buttons read the shift in their own
script.py, so each of them gets the same small config.py that runs it.

Checks:
- a button whose script.py reads the shift has a config.py, and a
  button with a config.py reads the shift (no dot that does nothing);
- every config.py is the same as the first, pure ASCII;
- run, config.py runs the script.py next to it, as __main__, with the
  globals pyRevit gave it (so the shift reaches the script) and with
  __file__ in the button folder (so the script finds lib).

Run: python test_shift_click_dots.py
"""

import glob
import io
import os
import runpy
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def reads_shift(script):
    text = io.open(script, encoding="utf-8").read()
    return "__shiftclick__" in text or "config_mode" in text


buttons = sorted(glob.glob(os.path.join(HERE, "AVH.tab", "**", "*.pushbutton"),
                           recursive=True))
shift = [b for b in buttons
         if os.path.isfile(os.path.join(b, "script.py")) and
         reads_shift(os.path.join(b, "script.py"))]
dotted = [b for b in buttons if os.path.isfile(os.path.join(b, "config.py"))]


def names(paths):
    return sorted(os.path.basename(p) for p in paths)


check("there are shift-click buttons to check", len(shift) >= 5,
      names(shift))
check("every shift-click button has a config.py",
      not [b for b in shift if b not in dotted],
      names([b for b in shift if b not in dotted]))
check("no config.py on a button that ignores the shift",
      not [b for b in dotted if b not in shift],
      names([b for b in dotted if b not in shift]))

texts = [io.open(os.path.join(b, "config.py"), "rb").read() for b in dotted]
check("every config.py is the same",
      texts and all(t == texts[0] for t in texts), names(dotted))
check("config.py is pure ASCII",
      texts and all(max(bytearray(t)) < 128 for t in texts))
check("config.py declares its encoding",
      texts and texts[0].startswith(b"# -*- coding: utf-8 -*-"))

# Run a config.py against a stand-in script.py in a button folder.
work = tempfile.mkdtemp()
try:
    button = os.path.join(work, u"Batch Sheets.pushbutton")
    os.makedirs(button)
    if dotted:
        shutil.copy(os.path.join(dotted[0], "config.py"), button)
    with io.open(os.path.join(button, "script.py"), "w",
                 encoding="utf-8") as handle:
        handle.write(u"# -*- coding: utf-8 -*-\n"
                     u"import os, sys\n"
                     u"RAN = dict(name=__name__,\n"
                     u"           folder=os.path.dirname("
                     u"os.path.abspath(__file__)),\n"
                     u"           shift=globals().get('__shiftclick__'),\n"
                     u"           text=u'h\\xe6\\xf0')\n")
    try:
        ran = runpy.run_path(os.path.join(button, "config.py"),
                             init_globals={"__shiftclick__": True},
                             run_name="__main__").get("RAN")
    except Exception as exc:
        ran = None
        print(u"config.py raised {0!r}".format(exc))
    check("config.py runs script.py", ran is not None)
    ran = ran or {}
    check("the script runs as __main__", ran.get("name") == "__main__",
          ran.get("name"))
    check("the script sees the shift pyRevit set",
          ran.get("shift") is True, ran.get("shift"))
    check("__file__ points into the button folder",
          os.path.normcase(ran.get("folder", "")) ==
          os.path.normcase(button), ran.get("folder"))
    check("non-ASCII in script.py comes through",
          ran.get("text") == u"h\xe6\xf0")
finally:
    shutil.rmtree(work, ignore_errors=True)

check("harness: check stores a real boolean", results[-1][1] is True)

failed = [r for r in results if not r[1]]
for name, ok, detail in results:
    if not ok:
        print(u"FAIL  {0}  [{1}]".format(name, detail))
print(u"{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
