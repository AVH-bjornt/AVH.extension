# -*- coding: utf-8 -*-
"""Pull the newest AVH extension from GitHub into this user's clone.

No Revit, no .NET, no pyRevit here. Every git operation is passed in, so
the decision logic runs on desktop Python in test_update_harness.py and
the pushbutton script stays wiring only.

The clone is the one `pyrevit extend ui` made, so pyRevit's own updater
(`pyrevit.versionmgr.updater.update_repo`, LibGit2Sharp underneath) does
the pull, exactly as pyRevit's Update button does.

## Why the result is checked rather than trusted

pyRevit's `git_pull` throws away LibGit2Sharp's `MergeResult`, so a pull
that merged nothing returns the same as one that moved the head. The
outcome is therefore measured, not taken from the call: the head commit
before and after, and how far the branch is still behind its remote
after the pull's fetch. A pull that "succeeded" while the branch is still
behind is reported as not applied, never as up to date.

## Why a clone with local commits is refused

A pull onto local commits makes a merge commit, signed with the Revit
user's name, in what is supposed to be a plain copy of GitHub. On a
user's machine that never happens. On the machine the extension is
developed on it would, so the button stops and says to use git there.
"""

import io
import os
import re

from avh_schedules.compat import to_text

UPDATED = "updated"
UP_TO_DATE = "up to date"
NOT_A_CLONE = "not a clone"
LOCAL_COMMITS = "local commits"
FAILED = "failed"
NOT_APPLIED = "not applied"

_VERSION_RE = re.compile(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", re.M)


class Outcome(object):
    def __init__(self, status, old_sha=u"", new_sha=u"", old_version=u"",
                 new_version=u"", detail=u""):
        self.status = status
        self.old_sha = to_text(old_sha)
        self.new_sha = to_text(new_sha)
        self.old_version = to_text(old_version)
        self.new_version = to_text(new_version)
        self.detail = to_text(detail)

    @property
    def should_reload(self):
        return self.status == UPDATED

    def __repr__(self):
        return "<Outcome {0} {1}>".format(self.status, self.new_sha[:7])


def version_file(ext_dir):
    return os.path.join(ext_dir, "lib", "avh_schedules", "__init__.py")


def read_version(ext_dir):
    """The version on disk, read as text.

    Never imported: the running session already holds the old module, so
    an import after the pull would still answer with the old number.
    """
    try:
        with io.open(version_file(ext_dir), encoding="utf-8") as handle:
            match = _VERSION_RE.search(handle.read())
        return to_text(match.group(1)) if match else u""
    except Exception:
        return u""


def update(ext_dir, git):
    """Bring the clone holding ext_dir up to date.

    git supplies, each taking the repository path:
        discover(path)  -> repository path, or None if not in a clone
        head(repo)      -> sha of the current head commit
        divergence(repo)-> (ahead, behind) against the tracked remote
                           branch, or None if there is none
        pull(repo)      -> fetch and merge; raises on failure
    """
    old_version = read_version(ext_dir)
    try:
        repo = git.discover(ext_dir)
    except Exception as exc:
        return Outcome(NOT_A_CLONE, old_version=old_version,
                       detail=to_text(exc))
    if not repo:
        return Outcome(NOT_A_CLONE, old_version=old_version)

    old_sha = git.head(repo)

    before = git.divergence(repo)
    if before is not None and before[0] > 0:
        return Outcome(LOCAL_COMMITS, old_sha, old_sha, old_version,
                       old_version,
                       u"{0} commit(s) here are not on GitHub".format(
                           before[0]))

    try:
        git.pull(repo)
    except Exception as exc:
        # Measured again: "nothing was changed" is only said if it is true.
        try:
            moved = git.head(repo)
        except Exception:
            moved = old_sha
        return Outcome(FAILED, old_sha, moved, old_version,
                       read_version(ext_dir), to_text(exc))

    new_sha = git.head(repo)
    new_version = read_version(ext_dir)
    after = git.divergence(repo)
    behind = after[1] if after is not None else 0

    if behind > 0:
        return Outcome(NOT_APPLIED, old_sha, new_sha, old_version,
                       new_version,
                       u"still {0} commit(s) behind GitHub after the "
                       u"pull".format(behind))
    if new_sha == old_sha:
        return Outcome(UP_TO_DATE, old_sha, new_sha, old_version,
                       new_version)
    return Outcome(UPDATED, old_sha, new_sha, old_version, new_version)


def message(outcome):
    """(title, body) for the dialog."""
    o = outcome
    short_old, short_new = o.old_sha[:7], o.new_sha[:7]
    if o.status == UPDATED:
        frm = o.old_version or short_old
        to = o.new_version or short_new
        if o.old_version and o.old_version == o.new_version:
            frm, to = short_old, short_new
        return (u"AVH updated",
                u"Updated from {0} to {1}. pyRevit reloads now.\n\n"
                u"If a button that moved panels does not show, or shows "
                u"twice, restart Revit.".format(frm, to))
    if o.status == UP_TO_DATE:
        return (u"AVH is up to date",
                u"Already the newest version ({0}). Nothing was "
                u"changed.".format(o.new_version or short_new))
    if o.status == NOT_A_CLONE:
        return (u"AVH cannot update itself here",
                u"This copy was not installed from GitHub, so there is "
                u"nothing to pull from. Run Install_AVH_Extensions.bat "
                u"once to reinstall it, and this button works after "
                u"that.{0}".format(
                    u"\n\n" + o.detail if o.detail else u""))
    if o.status == LOCAL_COMMITS:
        return (u"AVH not updated",
                u"{0}, so pulling would make a merge. This looks like a "
                u"development copy: update it with git instead. Nothing "
                u"was changed.".format(o.detail))
    if o.status == NOT_APPLIED:
        return (u"AVH update did not go through",
                u"The pull returned without an error, but this copy is {0}. "
                u"Usually a file in the extension folder was changed by "
                u"hand. Run Install_AVH_Extensions.bat, which reinstalls "
                u"from scratch if it has to.".format(o.detail))
    changed = (u"Nothing was changed." if o.new_sha == o.old_sha else
               u"The copy moved from {0} to {1} before it failed, so "
               u"restart Revit after fixing it.".format(short_old,
                                                        short_new))
    return (u"AVH update failed",
            u"Could not get the update. {0}\n\n{1}\n\n"
            u"No internet, GitHub unreachable from this network, or a "
            u"file in the extension folder edited by hand are the usual "
            u"causes. Install_AVH_Extensions.bat is the fallback.".format(
                changed, o.detail))
