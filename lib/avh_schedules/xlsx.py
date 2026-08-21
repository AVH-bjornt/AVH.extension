# -*- coding: utf-8 -*-
"""
A minimal .xlsx writer: zip plus OOXML, standard library only.

Why this exists. openpyxl needs Python 3.8+, which means pyRevit's CPython
engine, and that engine fails to initialise in this environment: the
script never runs and the failure surfaces as a Revit level dialog with no
Python output (see pyRevit issue 3341 for the same signature). IronPython
is pyRevit's default engine and does work, so the spreadsheet layer has to
run on IronPython 2.7.

Scope is deliberately narrow: exactly the features the AVH house style
needs, nothing more.

    values          text, int, float, formulas
    fonts           name, size, bold, italic, colour
    fills           solid colour
    borders         hair, thin, double, per edge
    alignment       horizontal, vertical, wrap, indent
    number formats  custom, e.g. #,##0.0
    merged cells, column widths, row heights, freeze panes
    one embedded PNG image
    landscape print setup with fit to width, and a print area

Written to run on IronPython 2.7 and CPython 3 alike: no f-strings, no
encoding= keyword on open(), explicit UTF-8 encoding of byte payloads.

Strings are written as inline strings rather than a shared string table.
Slightly larger files, considerably less to get wrong.
"""

import zipfile

from .compat import to_text

EMU_PER_PIXEL = 9525

# Excel requires fill 0 to be none and fill 1 to be gray125, and border 0
# to be empty. Those slots are written by hand in _styles_xml, so the
# registries must reserve them or every index comes out one too low. That
# off by one silently shifted every border in the workbook: the first
# style registered rendered with no border at all, and each later one
# picked up its predecessor's.
_RESERVED_FILLS = 2
_RESERVED_BORDERS = 1
_FIRST_CUSTOM_NUMFMT = 164


def escape(text):
    """XML-escape text for element content and attribute values.

    Goes through to_text, never str(): on IronPython 2.7 str() of an
    Icelandic room name raises UnicodeEncodeError.
    """
    if text is None:
        return u""
    out = to_text(text)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = out.replace('"', "&quot;").replace("'", "&apos;")
    # control characters are illegal in XML 1.0 and Revit data can carry them
    return u"".join(ch for ch in out
                    if ch in u"\t\n\r" or ord(ch) >= 32)


def column_letter(index):
    """1 -> A, 27 -> AA."""
    if index < 1:
        raise ValueError("column index is 1 based")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_ref(row, col):
    return "{0}{1}".format(column_letter(col), row)


class Font(object):
    def __init__(self, name="Calibri", size=11, bold=False, italic=False,
                 color=None):
        self.name = name
        self.size = size
        self.bold = bold
        self.italic = italic
        self.color = color

    def key(self):
        return (self.name, self.size, self.bold, self.italic, self.color)

    def xml(self):
        parts = ["<font>"]
        if self.bold:
            parts.append("<b/>")
        if self.italic:
            parts.append("<i/>")
        parts.append('<sz val="{0}"/>'.format(self.size))
        if self.color:
            parts.append('<color rgb="FF{0}"/>'.format(self.color))
        parts.append('<name val="{0}"/>'.format(escape(self.name)))
        parts.append("</font>")
        return "".join(parts)


class Fill(object):
    def __init__(self, color=None):
        self.color = color

    def key(self):
        return self.color

    def xml(self):
        if not self.color:
            return '<fill><patternFill patternType="none"/></fill>'
        return ('<fill><patternFill patternType="solid">'
                '<fgColor rgb="FF{0}"/><bgColor indexed="64"/>'
                '</patternFill></fill>').format(self.color)


class Border(object):
    """Edge styles: None, or one of hair, thin, medium, double."""

    def __init__(self, left=None, right=None, top=None, bottom=None,
                 color="000000"):
        self.left = left
        self.right = right
        self.top = top
        self.bottom = bottom
        self.color = color

    def key(self):
        return (self.left, self.right, self.top, self.bottom, self.color)

    def xml(self):
        parts = ["<border>"]
        for edge in ("left", "right", "top", "bottom"):
            style = getattr(self, edge)
            if style:
                parts.append('<{0} style="{1}"><color rgb="FF{2}"/></{0}>'
                             .format(edge, style, self.color))
            else:
                parts.append("<{0}/>".format(edge))
        parts.append("<diagonal/></border>")
        return "".join(parts)


class Alignment(object):
    def __init__(self, horizontal=None, vertical=None, wrap=False, indent=0):
        self.horizontal = horizontal
        self.vertical = vertical
        self.wrap = wrap
        self.indent = indent

    def key(self):
        return (self.horizontal, self.vertical, self.wrap, self.indent)

    def xml(self):
        attrs = []
        if self.horizontal:
            attrs.append('horizontal="{0}"'.format(self.horizontal))
        if self.vertical:
            attrs.append('vertical="{0}"'.format(self.vertical))
        if self.wrap:
            attrs.append('wrapText="1"')
        if self.indent:
            attrs.append('indent="{0}"'.format(self.indent))
        if not attrs:
            return ""
        return "<alignment {0}/>".format(" ".join(attrs))


class Style(object):
    """A cell format: any combination of the pieces above."""

    def __init__(self, font=None, fill=None, border=None, alignment=None,
                 number_format=None):
        self.font = font
        self.fill = fill
        self.border = border
        self.alignment = alignment
        self.number_format = number_format

    def key(self):
        return (self.font.key() if self.font else None,
                self.fill.key() if self.fill else None,
                self.border.key() if self.border else None,
                self.alignment.key() if self.alignment else None,
                self.number_format)


class _Registry(object):
    """Dedupes style pieces and hands back stable indices."""

    def __init__(self, reserved=0):
        self.items = []
        self.index = {}
        self.reserved = reserved

    def add(self, item):
        if item is None:
            return 0
        key = item.key()
        if key in self.index:
            return self.index[key]
        position = len(self.items) + self.reserved
        self.items.append(item)
        self.index[key] = position
        return position


class Worksheet(object):
    def __init__(self, name, workbook):
        self.name = name
        self._wb = workbook
        self._cells = {}          # (row, col) -> (value, style_id, is_formula)
        self._merges = []
        self._col_widths = {}
        self._row_heights = {}
        self._freeze = None
        self._image = None
        self._print_area = None
        self.landscape = False
        self.fit_to_width = None
        self.max_row = 0
        self.max_col = 0

    def cell(self, row, col, value=None, style=None, formula=None):
        if row < 1 or col < 1:
            raise ValueError("rows and columns are 1 based")
        style_id = self._wb._style_id(style)
        if formula is not None:
            self._cells[(row, col)] = (formula, style_id, True)
        else:
            self._cells[(row, col)] = (value, style_id, False)
        if row > self.max_row:
            self.max_row = row
        if col > self.max_col:
            self.max_col = col

    def merge(self, row1, col1, row2, col2):
        self._merges.append((row1, col1, row2, col2))

    def column_width(self, col, width):
        self._col_widths[col] = width

    def row_height(self, row, height):
        self._row_heights[row] = height

    def freeze_panes(self, row, col):
        """Freeze everything above row and left of col."""
        self._freeze = (row, col)

    def add_image(self, png_path, row=1, col=1, width_px=190):
        """Anchor a PNG at a cell, scaled to width_px, aspect preserved."""
        import struct
        handle = open(png_path, "rb")
        try:
            head = handle.read(24)
        finally:
            handle.close()
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(u"not a PNG: " + to_text(png_path))
        native_w, native_h = struct.unpack(">II", head[16:24])
        height_px = int(round(width_px * native_h / float(native_w)))
        self._image = {
            "path": png_path,
            "row": row,
            "col": col,
            "width": int(width_px),
            "height": height_px,
        }

    def print_area(self, ref):
        self._print_area = ref

    # --- xml -----------------------------------------------------------

    def _sheet_xml(self):
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/'
                 'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                 'openxmlformats.org/officeDocument/2006/relationships">']

        if self.fit_to_width:
            parts.append('<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>')

        if self.max_row and self.max_col:
            parts.append('<dimension ref="A1:{0}"/>'.format(
                cell_ref(self.max_row, self.max_col)))

        parts.append("<sheetViews><sheetView workbookViewId=\"0\">")
        if self._freeze:
            row, col = self._freeze
            parts.append(
                '<pane xSplit="{0}" ySplit="{1}" topLeftCell="{2}" '
                'activePane="bottomRight" state="frozen"/>'.format(
                    col - 1, row - 1, cell_ref(row, col)))
        parts.append("</sheetView></sheetViews>")

        parts.append('<sheetFormatPr defaultRowHeight="15"/>')

        if self._col_widths:
            parts.append("<cols>")
            for col in sorted(self._col_widths):
                parts.append('<col min="{0}" max="{0}" width="{1}" '
                             'customWidth="1"/>'.format(
                                 col, self._col_widths[col]))
            parts.append("</cols>")

        parts.append("<sheetData>")
        rows = {}
        for (row, col), payload in self._cells.items():
            rows.setdefault(row, []).append((col, payload))
        for row in sorted(rows):
            attrs = ['r="{0}"'.format(row)]
            if row in self._row_heights:
                attrs.append('ht="{0}" customHeight="1"'.format(
                    self._row_heights[row]))
            parts.append("<row {0}>".format(" ".join(attrs)))
            for col, (value, style_id, is_formula) in sorted(rows[row]):
                parts.append(self._cell_xml(row, col, value, style_id,
                                            is_formula))
            parts.append("</row>")
        parts.append("</sheetData>")

        if self._merges:
            parts.append('<mergeCells count="{0}">'.format(len(self._merges)))
            for row1, col1, row2, col2 in self._merges:
                parts.append('<mergeCell ref="{0}:{1}"/>'.format(
                    cell_ref(row1, col1), cell_ref(row2, col2)))
            parts.append("</mergeCells>")

        setup = []
        if self.landscape:
            setup.append('orientation="landscape"')
        if self.fit_to_width:
            setup.append('fitToWidth="{0}" fitToHeight="0"'.format(
                self.fit_to_width))
        if setup:
            parts.append("<pageSetup {0}/>".format(" ".join(setup)))

        if self._image:
            parts.append('<drawing r:id="rId1"/>')

        parts.append("</worksheet>")
        return "".join(parts)

    def _cell_xml(self, row, col, value, style_id, is_formula):
        ref = cell_ref(row, col)
        style_attr = ' s="{0}"'.format(style_id) if style_id else ""

        if is_formula:
            formula = value
            if formula.startswith("="):
                formula = formula[1:]
            return '<c r="{0}"{1}><f>{2}</f></c>'.format(
                ref, style_attr, escape(formula))

        if value is None or value == "":
            return '<c r="{0}"{1}/>'.format(ref, style_attr)

        if isinstance(value, bool):
            return '<c r="{0}"{1} t="b"><v>{2}</v></c>'.format(
                ref, style_attr, 1 if value else 0)

        if isinstance(value, (int, float)):
            return '<c r="{0}"{1}><v>{2}</v></c>'.format(
                ref, style_attr, repr(value)
                if isinstance(value, float) else value)

        return ('<c r="{0}"{1} t="inlineStr"><is><t xml:space="preserve">'
                '{2}</t></is></c>').format(ref, style_attr, escape(value))

    def _drawing_xml(self):
        image = self._image
        col_offset = image["col"] - 1
        row_offset = image["row"] - 1
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/'
                'drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.'
                'openxmlformats.org/drawingml/2006/main">'
                '<xdr:oneCellAnchor>'
                '<xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff>'
                '<xdr:row>{row}</xdr:row><xdr:rowOff>0</xdr:rowOff>'
                '</xdr:from>'
                '<xdr:ext cx="{cx}" cy="{cy}"/>'
                '<xdr:pic>'
                '<xdr:nvPicPr>'
                '<xdr:cNvPr id="1" name="Logo"/>'
                '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/>'
                '</xdr:cNvPicPr>'
                '</xdr:nvPicPr>'
                '<xdr:blipFill><a:blip xmlns:r="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships" '
                'r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch>'
                '</xdr:blipFill>'
                '<xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
                '<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
                '</xdr:pic><xdr:clientData/></xdr:oneCellAnchor>'
                '</xdr:wsDr>').format(
                    col=col_offset, row=row_offset,
                    cx=image["width"] * EMU_PER_PIXEL,
                    cy=image["height"] * EMU_PER_PIXEL)


class Workbook(object):
    def __init__(self):
        self._fonts = _Registry()
        self._fills = _Registry(reserved=_RESERVED_FILLS)
        self._borders = _Registry(reserved=_RESERVED_BORDERS)
        self._numfmts = {}
        self._xfs = []
        self._xf_index = {}
        self.sheets = []

        # slot 0 must be the default format
        self._fonts.add(Font())
        self._xfs.append((0, 0, 0, None, 0))
        self._xf_index[None] = 0

    def add_sheet(self, name):
        sheet = Worksheet(name, self)
        self.sheets.append(sheet)
        return sheet

    def _style_id(self, style):
        if style is None:
            return 0
        key = style.key()
        if key in self._xf_index:
            return self._xf_index[key]

        font_id = self._fonts.add(style.font) if style.font else 0
        fill_id = self._fills.add(style.fill) if style.fill else 0
        border_id = self._borders.add(style.border) if style.border else 0

        numfmt_id = 0
        if style.number_format:
            if style.number_format not in self._numfmts:
                self._numfmts[style.number_format] = (
                    _FIRST_CUSTOM_NUMFMT + len(self._numfmts))
            numfmt_id = self._numfmts[style.number_format]

        self._xfs.append((font_id, fill_id, border_id, style.alignment,
                          numfmt_id))
        position = len(self._xfs) - 1
        self._xf_index[key] = position
        return position

    def _styles_xml(self):
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<styleSheet xmlns="http://schemas.openxmlformats.org/'
                 'spreadsheetml/2006/main">']

        if self._numfmts:
            parts.append('<numFmts count="{0}">'.format(len(self._numfmts)))
            for code, numfmt_id in sorted(self._numfmts.items(),
                                          key=lambda kv: kv[1]):
                parts.append('<numFmt numFmtId="{0}" formatCode="{1}"/>'
                             .format(numfmt_id, escape(code)))
            parts.append("</numFmts>")

        parts.append('<fonts count="{0}">'.format(len(self._fonts.items)))
        for font in self._fonts.items:
            parts.append(font.xml())
        parts.append("</fonts>")

        # the two reserved fills are mandatory and must come first
        parts.append('<fills count="{0}">'.format(
            len(self._fills.items) + _RESERVED_FILLS))
        parts.append('<fill><patternFill patternType="none"/></fill>')
        parts.append('<fill><patternFill patternType="gray125"/></fill>')
        for fill in self._fills.items:
            parts.append(fill.xml())
        parts.append("</fills>")

        parts.append('<borders count="{0}">'.format(
            len(self._borders.items) + _RESERVED_BORDERS))
        parts.append(Border().xml())
        for border in self._borders.items:
            parts.append(border.xml())
        parts.append("</borders>")

        parts.append('<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" '
                     'fillId="0" borderId="0"/></cellStyleXfs>')

        parts.append('<cellXfs count="{0}">'.format(len(self._xfs)))
        for font_id, fill_id, border_id, alignment, numfmt_id in self._xfs:
            attrs = ['numFmtId="{0}"'.format(numfmt_id),
                     'fontId="{0}"'.format(font_id),
                     'fillId="{0}"'.format(fill_id),
                     'borderId="{0}"'.format(border_id),
                     'xfId="0"']
            if numfmt_id:
                attrs.append('applyNumberFormat="1"')
            if font_id:
                attrs.append('applyFont="1"')
            if fill_id:
                attrs.append('applyFill="1"')
            if border_id:
                attrs.append('applyBorder="1"')
            alignment_xml = alignment.xml() if alignment else ""
            if alignment_xml:
                attrs.append('applyAlignment="1"')
                parts.append("<xf {0}>{1}</xf>".format(" ".join(attrs),
                                                       alignment_xml))
            else:
                parts.append("<xf {0}/>".format(" ".join(attrs)))
        parts.append("</cellXfs>")

        parts.append('<cellStyles count="1"><cellStyle name="Normal" '
                     'xfId="0" builtinId="0"/></cellStyles>')
        parts.append('<dxfs count="0"/>')
        parts.append('<tableStyles count="0"/>')
        parts.append("</styleSheet>")
        return "".join(parts)

    def _workbook_xml(self):
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<workbook xmlns="http://schemas.openxmlformats.org/'
                 'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                 'openxmlformats.org/officeDocument/2006/relationships">'
                 "<sheets>"]
        for i, sheet in enumerate(self.sheets, start=1):
            parts.append('<sheet name="{0}" sheetId="{1}" r:id="rId{1}"/>'
                         .format(escape(sheet.name[:31]), i))
        parts.append("</sheets>")

        areas = [(i, s) for i, s in enumerate(self.sheets, start=1)
                 if s._print_area]
        if areas:
            parts.append("<definedNames>")
            for i, sheet in areas:
                parts.append('<definedName name="_xlnm.Print_Area" '
                             'localSheetId="{0}">\'{1}\'!{2}</definedName>'
                             .format(i - 1, escape(sheet.name[:31]),
                                     sheet._print_area))
            parts.append("</definedNames>")

        # Formula cells are written as <f> with no cached <v>, because
        # computing them here would mean implementing Excel. Without this
        # element Excel has nothing to display and no instruction to
        # calculate, so every SUM shows blank. LibreOffice recalculates on
        # open regardless, which is exactly why this survived a suite that
        # only ever checked with LibreOffice and openpyxl.
        # calcPr belongs after definedNames in the CT_Workbook sequence.
        parts.append('<calcPr calcId="0" fullCalcOnLoad="1"/>')
        parts.append("</workbook>")
        return "".join(parts)

    def _content_types_xml(self):
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/'
                 'package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/'
                 'vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Default Extension="png" ContentType="image/png"/>'
                 '<Override PartName="/xl/workbook.xml" ContentType='
                 '"application/vnd.openxmlformats-officedocument.'
                 'spreadsheetml.sheet.main+xml"/>'
                 '<Override PartName="/xl/styles.xml" ContentType='
                 '"application/vnd.openxmlformats-officedocument.'
                 'spreadsheetml.styles+xml"/>']
        for i, sheet in enumerate(self.sheets, start=1):
            parts.append('<Override PartName="/xl/worksheets/sheet{0}.xml" '
                         'ContentType="application/vnd.openxmlformats-'
                         'officedocument.spreadsheetml.worksheet+xml"/>'
                         .format(i))
            if sheet._image:
                parts.append('<Override PartName="/xl/drawings/drawing{0}.xml"'
                             ' ContentType="application/vnd.openxmlformats-'
                             'officedocument.drawing+xml"/>'.format(i))
        parts.append("</Types>")
        return "".join(parts)

    def save(self, path):
        archive = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        try:
            def write(name, text):
                archive.writestr(name, text.encode("utf-8"))

            write("[Content_Types].xml", self._content_types_xml())
            write("_rels/.rels",
                  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Relationships xmlns="http://schemas.openxmlformats.org/'
                  'package/2006/relationships">'
                  '<Relationship Id="rId1" Type="http://schemas.'
                  'openxmlformats.org/officeDocument/2006/relationships/'
                  'officeDocument" Target="xl/workbook.xml"/>'
                  "</Relationships>")

            rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/relationships">']
            for i, _sheet in enumerate(self.sheets, start=1):
                rels.append('<Relationship Id="rId{0}" Type="http://schemas.'
                            'openxmlformats.org/officeDocument/2006/'
                            'relationships/worksheet" Target="worksheets/'
                            'sheet{0}.xml"/>'.format(i))
            rels.append('<Relationship Id="rId{0}" Type="http://schemas.'
                        'openxmlformats.org/officeDocument/2006/'
                        'relationships/styles" Target="styles.xml"/>'
                        .format(len(self.sheets) + 1))
            rels.append("</Relationships>")
            write("xl/_rels/workbook.xml.rels", "".join(rels))

            write("xl/workbook.xml", self._workbook_xml())
            write("xl/styles.xml", self._styles_xml())

            for i, sheet in enumerate(self.sheets, start=1):
                write("xl/worksheets/sheet{0}.xml".format(i),
                      sheet._sheet_xml())

                if not sheet._image:
                    continue

                write("xl/worksheets/_rels/sheet{0}.xml.rels".format(i),
                      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      '<Relationships xmlns="http://schemas.openxmlformats.'
                      'org/package/2006/relationships">'
                      '<Relationship Id="rId1" Type="http://schemas.'
                      'openxmlformats.org/officeDocument/2006/relationships/'
                      'drawing" Target="../drawings/drawing{0}.xml"/>'
                      "</Relationships>".format(i))

                write("xl/drawings/drawing{0}.xml".format(i),
                      sheet._drawing_xml())

                write("xl/drawings/_rels/drawing{0}.xml.rels".format(i),
                      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      '<Relationships xmlns="http://schemas.openxmlformats.'
                      'org/package/2006/relationships">'
                      '<Relationship Id="rId1" Type="http://schemas.'
                      'openxmlformats.org/officeDocument/2006/relationships/'
                      'image" Target="../media/image{0}.png"/>'
                      "</Relationships>".format(i))

                handle = open(sheet._image["path"], "rb")
                try:
                    archive.writestr("xl/media/image{0}.png".format(i),
                                     handle.read())
                finally:
                    handle.close()
        finally:
            archive.close()
        return path
