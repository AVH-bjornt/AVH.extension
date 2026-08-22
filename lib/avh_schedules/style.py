# -*- coding: utf-8 -*-
"""
The AVH house style, expressed against the local xlsx writer.

Confirmed with Björn on the Eldisgarður room and door schedules:

    Calibri throughout.
    Header and section rows: solid #195784, white bold text.
    Title text: brand navy #293960.
    Quantities as real numbers with the unit named in the column header.
    AVH logo at A1, about 190px wide, aspect preserved.
    Subtotals as live SUM formulas, never hardcoded values.

No openpyxl: it requires Python 3.8+, which means pyRevit's CPython engine,
which fails to initialise in this environment. Everything here runs on
IronPython 2.7.
"""

from . import xlsx

# --- brand -------------------------------------------------------------

# Brand navy, used for the title text.
NAVY = "293960"

# Header and section row background. Set separately from NAVY because
# Björn picked it off a colour swatch for the bands specifically; the
# title text stays brand navy.
HEADER_BG = "195784"

WHITE = "FFFFFF"
FONT_NAME = "Calibri"
HAIR_COLOR = "999999"

# Zebra striping on data rows. Deliberately very faint: over a 43 column
# door schedule the point is to help the eye track one record across the
# page, not to draw attention. Anything darker starts competing with the
# navy and with the dashed row rules.
STRIPE = "F2F4F7"

NUMBER_FORMAT_1DP = "#,##0.0"

# --- fonts -------------------------------------------------------------

FONT_TITLE = xlsx.Font(FONT_NAME, 14, bold=True, color=NAVY)
FONT_HEADER = xlsx.Font(FONT_NAME, 9, bold=True, color=WHITE)
FONT_SECTION = xlsx.Font(FONT_NAME, 11, bold=True, color=WHITE)
FONT_BODY = xlsx.Font(FONT_NAME, 11)
FONT_SUBTOTAL = xlsx.Font(FONT_NAME, 10, bold=True, italic=True)
FONT_TOTAL = xlsx.Font(FONT_NAME, 11, bold=True)

FILL_NAVY = xlsx.Fill(NAVY)
FILL_HEADER = xlsx.Fill(HEADER_BG)

BORDER_HAIR = xlsx.Border("hair", "hair", "hair", "hair", color=HAIR_COLOR)

# Data rows: hair verticals to divide the columns, a coarse dashed rule
# underneath to divide the rows. No top edge, because the row above
# already draws it and doubling them reads as a solid line.
BORDER_ROW = xlsx.Border(left="hair", right="hair", bottom="dashed",
                         color=HAIR_COLOR)
BORDER_TOP_THIN = xlsx.Border(top="thin")
BORDER_TOP_DOUBLE = xlsx.Border(top="double")

# A section row sits directly under the navy header block, and two navy
# fills touching read as one thick band. A white rule along the top of the
# section row separates them without costing a whole spreadsheet row.
BORDER_TOP_WHITE = xlsx.Border(top="thin", color=WHITE)

CENTER = xlsx.Alignment("center", "center", wrap=True)
CENTER_NOWRAP = xlsx.Alignment("center", "center")
LEFT = xlsx.Alignment("left", "center")
RIGHT = xlsx.Alignment("right", "center")
SECTION_ALIGN = xlsx.Alignment("left", "center", indent=1)


# --- composed cell styles ----------------------------------------------

TITLE = xlsx.Style(font=FONT_TITLE, alignment=LEFT)

HEADER = xlsx.Style(font=FONT_HEADER, fill=FILL_HEADER, border=BORDER_HAIR,
                    alignment=CENTER)

SECTION = xlsx.Style(font=FONT_SECTION, fill=FILL_HEADER,
                     border=BORDER_TOP_WHITE, alignment=SECTION_ALIGN)

FILL_STRIPE = xlsx.Fill(STRIPE)


def _body(alignment=None, number_format=None, striped=False):
    """One data cell style. Striping only varies the fill, so the six
    combinations stay in step with each other automatically."""
    return xlsx.Style(font=FONT_BODY, border=BORDER_ROW,
                      fill=FILL_STRIPE if striped else None,
                      alignment=alignment, number_format=number_format)


BODY = _body()
BODY_CENTER = _body(alignment=CENTER_NOWRAP)
BODY_QUANTITY = _body(alignment=CENTER_NOWRAP,
                      number_format=NUMBER_FORMAT_1DP)

BODY_STRIPED = _body(striped=True)
BODY_CENTER_STRIPED = _body(alignment=CENTER_NOWRAP, striped=True)
BODY_QUANTITY_STRIPED = _body(alignment=CENTER_NOWRAP,
                              number_format=NUMBER_FORMAT_1DP, striped=True)

# kind -> (plain, striped). The writer picks a kind per column and a
# stripe per row, so it never has to know the six names.
BODY_STYLES = {
    "text": (BODY, BODY_STRIPED),
    "center": (BODY_CENTER, BODY_CENTER_STRIPED),
    "quantity": (BODY_QUANTITY, BODY_QUANTITY_STRIPED),
}


def body_style(kind, striped):
    return BODY_STYLES[kind][1 if striped else 0]

SUBTOTAL_LABEL = xlsx.Style(font=FONT_SUBTOTAL, border=BORDER_TOP_THIN,
                            alignment=RIGHT)

SUBTOTAL_VALUE = xlsx.Style(font=FONT_SUBTOTAL, border=BORDER_TOP_THIN,
                            alignment=CENTER_NOWRAP)

SUBTOTAL_QUANTITY = xlsx.Style(font=FONT_SUBTOTAL, border=BORDER_TOP_THIN,
                               alignment=CENTER_NOWRAP,
                               number_format=NUMBER_FORMAT_1DP)

TOTAL_LABEL = xlsx.Style(font=FONT_TOTAL, border=BORDER_TOP_DOUBLE,
                         alignment=RIGHT)

TOTAL_VALUE = xlsx.Style(font=FONT_TOTAL, border=BORDER_TOP_DOUBLE,
                         alignment=CENTER_NOWRAP)

TOTAL_QUANTITY = xlsx.Style(font=FONT_TOTAL, border=BORDER_TOP_DOUBLE,
                            alignment=CENTER_NOWRAP,
                            number_format=NUMBER_FORMAT_1DP)

RULE_ONLY = xlsx.Style(border=BORDER_TOP_THIN)
RULE_ONLY_DOUBLE = xlsx.Style(border=BORDER_TOP_DOUBLE)


def insert_logo(sheet, logo_path, row=1, col=1, width_px=190, row_height=78):
    """Place the AVH logo and size its row to fit.

    Title text belongs far enough right of the anchor to clear it, roughly
    width_px / 7 spreadsheet width units.
    """
    sheet.add_image(logo_path, row=row, col=col, width_px=width_px)
    sheet.row_height(row, row_height)


def write_section_header(sheet, row, label, n_cols):
    """A full width navy divider row for a level or type group."""
    if n_cols > 1:
        sheet.merge(row, 1, row, n_cols)
    sheet.cell(row, 1, label, SECTION)
    # the merge only carries the anchor cell's fill, so paint the rest
    for col in range(2, n_cols + 1):
        sheet.cell(row, col, None, SECTION)
