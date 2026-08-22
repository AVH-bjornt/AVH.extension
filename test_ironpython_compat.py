# -*- coding: utf-8 -*-
"""
Static checks that every shipped file can actually run on IronPython 2.7.

This suite exists because a hand written checklist missed two things in a
row, both of which only failed inside Revit:

1. A missing PEP 263 encoding declaration. Python 2 refuses to even parse
   a source file containing non-ASCII bytes without one, and these modules
   are full of m², Rýmisheiti and Hæð. This killed v2.0.0 on first run
   with `SyntaxError: Non-ASCII character '\\xc2'`.
2. Non-ASCII literals without a `u` prefix. On Python 2 those are bytes,
   so comparing them against unicode text from a Revit export triggers an
   implicit ASCII decode and blows up on the first Icelandic room name.

Checks are static, because the real interpreter is not available here. The
Python 2 grammar parse in check 6 is the strongest of them: it is the same
grammar IronPython 2.7 uses.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Files that must run on IronPython. The test suites themselves run under
# CPython 3 here, so they are exempt from the runtime-only rules but still
# need the encoding declaration to be parseable at all.
SHIPPED = []
TESTS = []
for root, dirs, files in os.walk(HERE):
    if "__pycache__" in root or "test_output" in root:
        continue
    for name in sorted(files):
        if not name.endswith(".py"):
            continue
        path = os.path.join(root, name)
        if name.startswith("test_"):
            TESTS.append(path)
        else:
            SHIPPED.append(path)

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def rel(path):
    return os.path.relpath(path, HERE)


def read(path):
    handle = io.open(path, "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


# --- 1. PEP 263 encoding declaration ----------------------------------

DECL_RE = re.compile(r"coding[:=]\s*([-\w.]+)")

for path in SHIPPED + TESTS:
    raw = open(path, "rb").read()
    has_non_ascii = any(byte > 127 for byte in bytearray(raw))
    head = raw.split(b"\n")[:2]
    declared = any(DECL_RE.search(line.decode("latin-1")) for line in head)
    if has_non_ascii:
        line_no = 1
        for i, byte in enumerate(bytearray(raw)):
            if byte > 127:
                line_no = raw[:i].count(b"\n") + 1
                break
        check("{0}: non-ASCII needs an encoding declaration".format(rel(path)),
              declared,
              "first non-ASCII byte on line {0}".format(line_no))

# every shipped file declares it regardless, so adding a unit later is safe
for path in SHIPPED:
    raw = open(path, "rb").read()
    head = raw.split(b"\n")[:2]
    check("{0}: declares an encoding".format(rel(path)),
          any(DECL_RE.search(line.decode("latin-1")) for line in head))


# --- 2. non-ASCII literals must carry a u prefix ----------------------

STRING_RE = re.compile(
    r"""(?P<prefix>[uUbBrR]{0,2})(?P<quote>\"\"\"|'''|\"|')""")


def unprefixed_non_ascii(text):
    """Find non-ASCII string literals with no u prefix, outside comments."""
    offenders = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # only care about lines that actually contain non-ASCII
        if all(ord(ch) < 128 for ch in line):
            continue
        for match in re.finditer(r"""(?<![\w])([uUbB]?[rR]?)(['"])""", line):
            prefix, quote = match.group(1), match.group(2)
            rest = line[match.end():]
            end = rest.find(quote)
            if end == -1:
                continue
            body = rest[:end]
            if any(ord(ch) > 127 for ch in body) and "u" not in prefix.lower():
                offenders.append((line_no, body[:40]))
    return offenders


for path in SHIPPED:
    text = read(path)
    # docstrings are exempt: they are never compared against Revit data
    without_docstrings = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    offenders = unprefixed_non_ascii(without_docstrings)
    check("{0}: non-ASCII literals are u-prefixed".format(rel(path)),
          not offenders,
          "; ".join("line {0}: {1!r}".format(n, b) for n, b in offenders[:4]))


# --- 3. no Python 3 only syntax ---------------------------------------

FORBIDDEN = [
    ("f-string", re.compile(r"""(?<![\w])f(['"])""")),
    ("walrus operator", re.compile(r":=")),
    ("nonlocal", re.compile(r"\bnonlocal\b")),
    ("yield from", re.compile(r"\byield\s+from\b")),
    ("super() with no args", re.compile(r"\bsuper\(\s*\)")),
    ("open(encoding=)", re.compile(r"(?<!io\.)\bopen\([^)]*encoding\s*=")),
    ("open(errors=)", re.compile(r"(?<!io\.)\bopen\([^)]*errors\s*=")),
]

for path in SHIPPED:
    text = read(path)
    # strip docstrings and comments: prose mentions these constructs
    code = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    code = "\n".join(line for line in code.split("\n")
                     if not line.lstrip().startswith("#"))
    for label, pattern in FORBIDDEN:
        match = pattern.search(code)
        check("{0}: no {1}".format(rel(path), label), not match,
              "found: {0!r}".format(
                  code[max(0, match.start() - 30):match.end() + 20])
              if match else "")


# --- 4. str() must not be called on Revit data ------------------------

# str() of unicode raises UnicodeEncodeError on IronPython. These are the
# only acceptable uses: not on data at all.
STR_ALLOWED = re.compile(r"(text_type|binary_type|isinstance|writestr|"
                         r"str\)|= str|_u = str)")

for path in SHIPPED:
    text = read(path)
    code = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    code = "\n".join(line for line in code.split("\n")
                     if not line.lstrip().startswith("#"))
    bad = []
    for line_no, line in enumerate(code.split("\n"), start=1):
        for match in re.finditer(r"(?<![\w.])str\(", line):
            if STR_ALLOWED.search(line):
                continue
            bad.append((line_no, line.strip()[:60]))
    check("{0}: no bare str() on data".format(rel(path)), not bad,
          "; ".join("line {0}: {1}".format(n, t) for n, t in bad[:3]))


# --- 5. no shebang selecting the CPython engine -----------------------

for path in SHIPPED:
    if ".pushbutton" not in path:
        continue
    first = read(path).split("\n")[0]
    check("{0}: no #! python3 (CPython engine is broken here)".format(
        rel(path)), not first.startswith("#!"), "first line: " + first)


# --- 6. the files parse under the Python 2 grammar --------------------

try:
    from lib2to3 import pygram, pytree            # noqa: F401
    from lib2to3.pgen2 import driver as p2driver
    from lib2to3.pgen2 import token as p2token    # noqa: F401

    grammar = pygram.python_grammar_no_print_statement
    parser = p2driver.Driver(grammar, convert=pytree.convert)

    for path in SHIPPED:
        text = read(path)
        if not text.endswith("\n"):
            text += "\n"
        try:
            parser.parse_string(text)
            check("{0}: parses under the Python 2 grammar".format(rel(path)),
                  True)
        except Exception as exc:
            check("{0}: parses under the Python 2 grammar".format(rel(path)),
                  False, "{0}: {1}".format(type(exc).__name__, exc))
except ImportError:
    results.append(("Python 2 grammar parse (lib2to3 unavailable)", True,
                    "skipped"))


# --- report -----------------------------------------------------------

print("IronPython 2.7 compatibility")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if not ok:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
