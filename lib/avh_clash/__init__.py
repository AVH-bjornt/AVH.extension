# -*- coding: utf-8 -*-
"""Clash Detector: which elements occupy the same space, shown in 3D.

    model.py    boxes, candidate pairs, the triangle test, mesh packing.
                No Revit, no .NET. Tested by test_clash_model.py.
    reader.py   Revit elements -> solids -> triangles, and the exact
                solid against solid test.
    service.py  what the window can ask for: the inventory, a run,
                select in Revit, section box. Revit, but no WPF.
    window.py   the WPF window holding a WebView2, and the ExternalEvent
                that lets a modeless window reach the Revit API.
    web/        the viewer itself: HTML, CSS and JavaScript on three.js.
"""
