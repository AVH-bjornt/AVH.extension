# -*- coding: utf-8 -*-
"""
A minimal .xlsx reader: zip plus OOXML, standard library only.

The counterpart of xlsx.py, for the same reason: openpyxl cannot run on
IronPython 2.7, which is what pyRevit runs scripts on here.

It reads what an edited AVH workbook can contain and nothing more: cell
values (text, numbers, booleans), through either of the two ways a file can
hold text. xlsx.py writes inline strings. Excel does not: the moment a
person saves, it rewrites every string into a shared string table, so the
reader has to cope with both or an edited workbook would read back empty.

Formulas are read as the value Excel last calculated, which is what a
person sees. A formula that was never calculated reads as None.
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from .compat import to_text

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
           "relationships")
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _q(tag):
    return "{" + _NS_MAIN + "}" + tag


def column_index(letters):
    """A -> 1, AA -> 27."""
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - 64)
    return index


def split_ref(ref):
    """'C12' -> (12, 3), as (row, column)."""
    match = _REF_RE.match(ref or "")
    if not match:
        raise ValueError("bad cell reference: {0!r}".format(ref))
    return int(match.group(2)), column_index(match.group(1))


class Sheet(object):
    """One worksheet: its name, whether it is hidden, and its cells."""

    def __init__(self, name, state, cells):
        self.name = name
        self.state = state
        self.cells = cells
        self.max_row = max([r for r, _c in cells] or [0])
        self.max_col = max([c for _r, c in cells] or [0])

    @property
    def hidden(self):
        return self.state in ("hidden", "veryHidden")

    def get(self, row, col):
        return self.cells.get((row, col))


def _text_of(element):
    """All the text in a shared string or inline string item.

    Rich text is a run of <r><t> pieces to be joined. Phonetic guides
    (<rPh>) are not part of the text and are skipped.
    """
    pieces = []

    def walk(node):
        for child in node:
            if child.tag == _q("t"):
                pieces.append(child.text or "")
            elif child.tag == _q("r"):
                walk(child)
    walk(element)
    return u"".join(to_text(p) for p in pieces)


def _number(text):
    """Excel stores numbers as plain decimal text, whatever the locale."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        return None
    if value == int(value) and abs(value) < 1e15 and "e" not in text.lower():
        return int(value)
    return value


def _shared_strings(archive):
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return [_text_of(item) for item in root.findall(_q("si"))]


def _sheet_targets(archive):
    """[(name, state, part name)] in workbook order."""
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    targets = {}
    for rel in rels.findall("{" + _NS_PKG_REL + "}Relationship"):
        target = rel.get("Target") or ""
        if target.startswith("/"):
            target = target[1:]
        else:
            target = "xl/" + target
        targets[rel.get("Id")] = target

    sheets = []
    container = workbook.find(_q("sheets"))
    if container is None:
        return sheets
    for sheet in container.findall(_q("sheet")):
        rid = sheet.get("{" + _NS_REL + "}id")
        part = targets.get(rid)
        if part is None:
            continue
        sheets.append((to_text(sheet.get("name")),
                       sheet.get("state") or "visible", part))
    return sheets


def _read_cells(archive, part, shared):
    cells = {}
    root = ET.fromstring(archive.read(part))
    data = root.find(_q("sheetData"))
    if data is None:
        return cells

    for row in data.findall(_q("row")):
        for cell in row.findall(_q("c")):
            ref = cell.get("r")
            if not ref:
                continue
            kind = cell.get("t") or "n"
            value = None

            if kind == "inlineStr":
                inline = cell.find(_q("is"))
                value = _text_of(inline) if inline is not None else u""
            else:
                v = cell.find(_q("v"))
                raw = v.text if v is not None else None
                if raw is None:
                    value = None
                elif kind == "s":
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = None
                elif kind == "str":
                    value = to_text(raw)
                elif kind == "b":
                    value = raw.strip() == "1"
                elif kind == "e":
                    value = None
                else:
                    value = _number(raw.strip())

            if value is None or value == u"":
                continue
            cells[split_ref(ref)] = value
    return cells


def read_workbook(path):
    """Every sheet in the workbook, in order, as a list of Sheet."""
    archive = zipfile.ZipFile(path, "r")
    try:
        shared = _shared_strings(archive)
        out = []
        for name, state, part in _sheet_targets(archive):
            out.append(Sheet(name, state, _read_cells(archive, part, shared)))
        return out
    finally:
        archive.close()
