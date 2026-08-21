# -*- coding: utf-8 -*-
"""
AVH schedule export.

Turns Revit schedules into AVH styled Excel workbooks: Calibri,
navy #293960 headers, the AVH logo, grouping mirrored from the schedule's
own Revit settings, and real SUM formula subtotals.

Layered so the Revit dependency stays in one place:

    model    plain data + value parsing, no Revit, no spreadsheet code
    reader   Revit ViewSchedule -> ScheduleTable
    writer   ScheduleTable -> styled .xlsx
    style    the AVH house style itself
"""

from .model import ScheduleTable, Column  # noqa: F401
from .writer import write_workbook  # noqa: F401

__version__ = "2.6.0"

# reader imports Autodesk.Revit only inside its Revit specific functions,
# so importing this package outside Revit stays safe for testing.
