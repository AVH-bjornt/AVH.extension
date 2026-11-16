# -*- coding: utf-8 -*-
"""Find elements that occupy the same space, and look at them in 3D.

Click it and a window opens beside Revit. Choose what to test against
what: two sets, each any mix of the host model's categories and the
categories of each linked model. Elements selected before the click are
offered as a set of their own.

Each pair whose bounding boxes overlap is then tested for real. Revit
intersects the two solids, so a clash comes with the volume the two
share, and that volume is drawn in red in the viewer. Geometry Revit
cannot intersect as solids (meshes, as in many IFC links) is tested on
its triangles instead and marked approximate.

The viewer lists the clashes, flies to the one you pick, and can pull
the two elements apart, cut through them, tour the list, and select the
pair in Revit or put the active 3D view's section box round it.

## Writing

Nothing is written to the model except by "Section box in Revit", which
changes the active 3D view's section box in one transaction whose
commit status is checked. Undo in Revit puts it back.

## The window

It is modeless, so Revit stays usable while it is open, and it reaches
the Revit API through an ExternalEvent. Shift click opens it modal
instead, which needs no ExternalEvent: the way round if the normal mode
does not respond on some machine.

## Unverified

Not yet run in Revit: any of it. See lib/avh_clash/window.py and
lib/avh_clash/reader.py for the API calls that carry the risk.
"""

__title__ = "Clash\nDetector"
__author__ = "AVH"
__doc__ = "Find elements that occupy the same space, between any categories of this model and its links, and review them in a 3D viewer. Shift click to open the window modal."
__persistentengine__ = True

import os
import sys

_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import revit, DB, forms, script      # noqa: E402
from avh_schedules.compat import to_text          # noqa: E402

try:
    from importlib import reload as _reload       # CPython 3
except ImportError:
    _reload = reload                              # noqa: F821  IronPython 2.7

# The persistent engine keeps every module it has imported, through a
# pyRevit reload and between clicks, so without this a fix to
# lib/avh_clash never reaches Revit until Revit restarts. Learned at
# 2.44.2: the fixed window.py was on disk, and Revit went on running the
# copy it had loaded first. Dependencies first, so each module reloads
# against the fresh copies of the ones it imports.
CLASH_MODULES = ("avh_clash.model", "avh_clash.reader",
                 "avh_clash.service", "avh_clash.window")

logger = script.get_logger()

TITLE = u"Clash Detector"


def shift_clicked():
    """True when the button was shift clicked. Two routes because
    pyRevit has offered two; neither is allowed to raise."""
    try:
        from pyrevit import EXEC_PARAMS
        value = getattr(EXEC_PARAMS, "config_mode", None)
        if value is not None:
            return bool(value)
    except BaseException:
        pass
    for source in (globals(), sys.modules["__main__"].__dict__):
        value = source.get("__shiftclick__")
        if value is not None:
            return bool(value)
    return False


def version():
    try:
        import avh_schedules
        return to_text(avh_schedules.__version__)
    except BaseException:
        return u""


def window_module():
    """The window, kept behind one function so the harness can stand in
    for it."""
    from avh_clash import window
    return window


def fresh(name):
    """The module `name` as it is on disk now. A module with no file
    behind it (one the harness put in its place) is used as it is."""
    __import__(name)
    module = sys.modules[name]
    if getattr(module, "__file__", None) is None:
        return module
    return _reload(module)


def fresh_modules():
    """(service, window), read again from disk."""
    loaded = {}
    for name in CLASH_MODULES:
        loaded[name] = fresh(name)
    return loaded["avh_clash.service"], loaded["avh_clash.window"]


def run():
    uidoc = revit.uidoc
    if uidoc is None or revit.doc is None:
        forms.alert(u"No active Revit document.", title=TITLE)
        return
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(u"Open a project: there is nothing to clash in the "
                    u"family editor.", title=TITLE)
        return

    try:
        # An open window lives in the module it was made by, so ask the
        # loaded copy before reloading anything.
        if window_module().bring_forward():
            return
        service, window = fresh_modules()
    except BaseException as exc:
        logger.error(to_text(exc))
        forms.alert(u"The viewer window could not be loaded: {0}".format(
            to_text(exc)), title=TITLE)
        return

    uiapp = uidoc.Application
    clash = service.Service(DB, uiapp, version=version(), logger=logger)
    clash.build_inventory()
    window.show(clash, uiapp, modal=shift_clicked(),
                title=u"{0} - {1}".format(TITLE, to_text(revit.doc.Title)))


if __name__ == "__main__":
    run()
