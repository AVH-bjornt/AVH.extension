# -*- coding: utf-8 -*-
"""Get the newest AVH tools from GitHub, then reload pyRevit.

Only this extension is updated, and only for the person who clicks. It
is the same pull pyRevit's own Update button does, through pyRevit's own
updater, but without updating pyRevit itself or anyone's other
extensions along with it.

The logic, and the reason every outcome is measured rather than trusted,
is in lib/avh_update. This file is wiring: the four git operations it
needs, the dialog, and the reload.

## When it reloads

Only when the head commit actually moved. Up to date, refused, or
failed: nothing changed on disk, so a reload would only cost the user
the wait.

## Unverified

Not yet run in Revit: `LibGit2Sharp.Repository.Discover`, the
`HistoryDivergence` returned by `compare_branch_heads`, and
`updater.update_repo` called on a single repository. All three are the
calls pyRevit's own updater makes, read from pyrevitlib on this machine.
"""

__title__ = "Update\nAVH"
__author__ = "AVH"
__doc__ = "Get the newest AVH tools from GitHub and reload pyRevit. Updates only this extension, only on your computer. Does nothing if you already have the newest version."

import os
import sys

# Walk up until the extension root turns up, rather than counting
# directory levels.
_EXT_DIR = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_EXT_DIR, "lib")):
    _PARENT = os.path.dirname(_EXT_DIR)
    if _PARENT == _EXT_DIR:
        break
    _EXT_DIR = _PARENT
_LIB_DIR = os.path.join(_EXT_DIR, "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from pyrevit import forms, script                 # noqa: E402
import avh_update                                 # noqa: E402

logger = script.get_logger()


class PyRevitGit(object):
    """The four operations avh_update needs, on pyRevit's own git layer.

    Each call opens the repository afresh and disposes it, so no
    LibGit2Sharp handle is still holding the folder when pyRevit reloads.
    """

    def __init__(self):
        from pyrevit.coreutils import git as libgit
        from pyrevit.versionmgr import updater
        self._libgit = libgit
        self._updater = updater

    def _open(self, repo):
        return self._libgit.get_repo(repo)

    @staticmethod
    def _close(info):
        try:
            info.repo.Dispose()
        except Exception:
            pass

    def discover(self, path):
        return self._libgit.libgit.Repository.Discover(path)

    def head(self, repo):
        info = self._open(repo)
        try:
            return info.last_commit_hash
        finally:
            self._close(info)

    def divergence(self, repo):
        info = self._open(repo)
        try:
            div = self._libgit.compare_branch_heads(info)
            if div is None:
                return None
            return (int(div.AheadBy or 0), int(div.BehindBy or 0))
        finally:
            self._close(info)

    def pull(self, repo):
        info = self._open(repo)
        try:
            self._updater.update_repo(info)
        finally:
            self._close(info)


def main():
    outcome = avh_update.update(_EXT_DIR, PyRevitGit())
    logger.debug("Update outcome: %s %s", outcome, outcome.detail)

    title, body = avh_update.message(outcome)
    try:
        forms.alert(body, title=title,
                    warn_icon=outcome.status not in (avh_update.UPDATED,
                                                     avh_update.UP_TO_DATE))
    except Exception:
        # A dialog that throws must not stop an update that already landed
        # from being loaded.
        print(u"{0}\n{1}".format(title, body))

    if outcome.should_reload:
        from pyrevit.loader import sessionmgr
        sessionmgr.reload_pyrevit()


main()
