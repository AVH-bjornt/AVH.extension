# -*- coding: utf-8 -*-
"""
Python 2 / 3 compatibility, for running on IronPython 2.7 inside Revit.

The CPython 3 engine in this environment fails during initialisation, so
everything here has to work on IronPython 2.7 as well as on CPython 3,
where the tests run.

Two things actually bite:

1. `open(path, encoding=...)` is Python 3 only. `io.open` accepts it on
   both.
2. On IronPython 2.7 `str` is bytes, so `str(u'LÁGSPENNURÝMI')` raises
   UnicodeEncodeError. Every conversion of Revit data to text must go
   through `to_text`, never through `str`. This matters throughout: the
   schedules are full of Icelandic names and m² / m³ units.
"""

import io

try:
    text_type = unicode          # noqa: F821  Python 2
    binary_type = str
except NameError:
    text_type = str              # Python 3
    binary_type = bytes

PY2 = text_type is not str


def to_text(value, encoding="utf-8"):
    """Convert anything to text without ever going via Python 2's str()."""
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, AttributeError):
            return value.decode(encoding, "replace")
    try:
        return text_type(value)
    except Exception:
        return u""


def open_text(path, mode="r", encoding="utf-8", errors="replace"):
    """Text mode open that behaves the same on both, always unicode."""
    return io.open(path, mode, encoding=encoding, errors=errors)


def read_text(path, encodings=("utf-8-sig", "utf-16", "latin-1")):
    """Read a text file, trying encodings in order. Returns unicode.

    Revit's schedule export encoding varies, so the caller cannot know it
    up front.
    """
    last = None
    for encoding in encodings:
        try:
            handle = io.open(path, "r", encoding=encoding)
            try:
                return handle.read()
            finally:
                handle.close()
        except (UnicodeDecodeError, UnicodeError, ValueError) as exc:
            last = exc
            continue
    raise IOError("Could not decode {0}: {1}".format(path, last))


def write_text(path, text, mode="w", encoding="utf-8"):
    handle = io.open(path, mode, encoding=encoding, errors="replace")
    try:
        handle.write(to_text(text))
    finally:
        handle.close()
    return path


def is_number_text(value):
    """True if the text is a plain integer, without Python 2 unicode traps.

    `u'123'.isdigit()` is True on IronPython, but so is `u'\\u0663'` (an
    Arabic-Indic digit), which int() then rejects. Checking the characters
    explicitly avoids relying on locale aware behaviour.
    """
    text = to_text(value).strip()
    if not text:
        return False
    if text[0] in u"+-":
        text = text[1:]
    if not text:
        return False
    return all(ch in u"0123456789" for ch in text)
