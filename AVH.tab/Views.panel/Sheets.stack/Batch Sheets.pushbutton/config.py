# -*- coding: utf-8 -*-
"""Shift-click: runs this button's own script.py.

pyRevit runs config.py instead of script.py when the button is shift
clicked, and marks every button that has one with a dot after its name.
The script reads the shift itself (EXEC_PARAMS.config_mode, or
__shiftclick__ on older pyRevit), so this file only hands over to it.
Every copy is the same; test_shift_click_dots.py checks that each button
whose script reads the shift has one, and that no other button does.
"""

import os

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "script.py")
with open(_SCRIPT, "rb") as _handle:
    _CODE = compile(_handle.read(), _SCRIPT, "exec")
exec(_CODE)
