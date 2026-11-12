# -*- coding: utf-8 -*-
"""Every button's tooltip must reach pyRevit whole.

pyRevit reads `__doc__` from the script's source without running it, and
of a value split over lines as ("..." "...") it keeps only the first
piece, so the tooltip in Revit stops mid sentence. Each `__doc__` must be
one string literal. Run: python test_button_tooltips.py
"""

import ast
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
failures = []
checked = 0
for path in sorted(glob.glob(os.path.join(HERE, "AVH.tab", "**", "*.py"),
                             recursive=True)):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "__doc__"):
            checked += 1
            if node.lineno != node.end_lineno:
                failures.append(os.path.relpath(path, HERE))

# The check itself must be able to fail.
probe = ast.parse('__doc__ = ("a "\n           "b")').body[0]
if probe.lineno == probe.end_lineno:
    failures.append("self-check: a split __doc__ was not caught")

print("{0} __doc__ checked".format(checked))
for failure in failures:
    print("FAIL  split __doc__ (tooltip cut short): " + failure)
if failures or checked < 20:
    sys.exit(1)
print("PASS")
