# -*- coding: utf-8 -*-
"""
AVH level tools.

Supports the Remove Level button. Deliberately thin: everything that can
be worked out without Revit lives in `model`, so the arithmetic that the
original pyApex script got wrong is covered by a test that runs outside
Revit. The Revit interaction itself stays in the pushbutton script.

    model    offset arithmetic and element classification, no Revit

Importing this package outside Revit is safe, which is what makes
`test_level_move.py` possible.
"""

from .model import (  # noqa: F401
    COLLATERAL,
    MOVE,
    RECREATE,
    REHOST,
    SHIFT,
    SKIP,
    MM_PER_FOOT,
    LevelWrite,
    PlanEntry,
    actionable,
    empty_counts,
    feet_to_mm,
    new_offset,
    recreatable,
    rehostable,
    room_height_after,
    summarise,
)

__version__ = "1.0.0"
