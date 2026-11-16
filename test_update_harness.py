# -*- coding: utf-8 -*-
"""
Update AVH: the library against real git repositories, and the button
script against a mocked pyRevit.

Part 1 builds an "origin" repository and a clone of it in a temp folder
and drives avh_update with the git command line standing in for
LibGit2Sharp. The outcomes are therefore measured on real clones that
really are behind, ahead, dirty, or not clones at all, not on canned
answers.

Part 2 runs the real pushbutton script with pyrevit mocked, to check the
wiring: that it reloads only after an update landed, that a dialog which
throws does not stop the reload, and that repositories are disposed.

Run: python test_update_harness.py
"""

import io
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
SCRIPT = os.path.join(HERE, "AVH.tab", "Update.panel",
                      "Update AVH.pushbutton", "script.py")

import avh_update as up                                   # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


# --- part 1: real repositories ------------------------------------------

def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    out = subprocess.run(["git", "-c", "init.defaultBranch=main",
                          "-c", "core.autocrlf=false"] + list(args),
                         cwd=cwd, env=env, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return out.stdout.strip()


class CliGit(object):
    """The four operations, on the git command line."""

    def __init__(self):
        self.pulls = 0

    def discover(self, path):
        try:
            return git(path, "rev-parse", "--show-toplevel")
        except RuntimeError:
            return None

    def head(self, repo):
        return git(repo, "rev-parse", "HEAD")

    def divergence(self, repo):
        try:
            out = git(repo, "rev-list", "--left-right", "--count",
                      "HEAD...@{u}")
        except RuntimeError:
            return None
        ahead, behind = out.split()
        return int(ahead), int(behind)

    def pull(self, repo):
        self.pulls += 1
        git(repo, "pull", "--no-rebase", "--no-edit")


def write_version(folder, version):
    path = up.version_file(folder)
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(u'"""x"""\n__version__ = "{0}"\n'.format(version))


def commit_all(folder, msg):
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", msg)


def make_world():
    """origin (bare), a clone the user has, and a dev clone that pushes."""
    root = tempfile.mkdtemp(prefix="avh_update_")
    origin = os.path.join(root, "origin.git")
    dev = os.path.join(root, "dev")
    user = os.path.join(root, "AVH.extension")
    git(root, "init", "-q", "--bare", origin)
    git(root, "clone", "-q", origin, dev)
    write_version(dev, "2.42.1")
    commit_all(dev, "first")
    git(dev, "push", "-q", "origin", "HEAD:main")
    git(root, "clone", "-q", "--branch", "main", origin, user)
    return root, origin, dev, user


def publish(dev, version):
    write_version(dev, version)
    commit_all(dev, "release " + version)
    git(dev, "push", "-q", "origin", "HEAD:main")


worlds = []

# up to date
root, origin, dev, user = make_world()
worlds.append(root)
g = CliGit()
o = up.update(user, g)
check("up to date: status", o.status == up.UP_TO_DATE, repr(o))
check("up to date: no reload", not o.should_reload)
check("up to date: version read", o.new_version == u"2.42.1", o.new_version)
check("up to date: message says nothing changed",
      u"Nothing was changed" in up.message(o)[1])

# behind by one release
publish(dev, "2.43.0")
before = git(user, "rev-parse", "HEAD")
o = up.update(user, g)
check("behind: status updated", o.status == up.UPDATED, repr(o) + o.detail)
check("behind: reload", o.should_reload)
check("behind: head really moved",
      git(user, "rev-parse", "HEAD") == git(dev, "rev-parse", "HEAD"))
check("behind: old sha recorded", o.old_sha == before)
check("behind: versions", (o.old_version, o.new_version)
      == (u"2.42.1", u"2.43.0"), (o.old_version, o.new_version))
check("behind: message names both versions",
      u"2.42.1 to 2.43.0" in up.message(o)[1], up.message(o)[1])

# behind by a commit that does not change the version: shas, not 2.43.0 twice
git(dev, "commit", "-q", "--allow-empty", "-m", "docs only")
git(dev, "push", "-q", "origin", "HEAD:main")
o = up.update(user, g)
body = up.message(o)[1]
check("same version: updated", o.status == up.UPDATED, repr(o))
check("same version: message uses shas, not the version twice",
      u"2.43.0 to 2.43.0" not in body and o.new_sha[:7] in body, body)

# local commits: refused before any pull
git(user, "commit", "-q", "--allow-empty", "-m", "local work")
pulls = g.pulls
local_head = git(user, "rev-parse", "HEAD")
o = up.update(user, g)
check("local commits: refused", o.status == up.LOCAL_COMMITS, repr(o))
check("local commits: no pull attempted", g.pulls == pulls)
check("local commits: head untouched",
      git(user, "rev-parse", "HEAD") == local_head)
check("local commits: no reload", not o.should_reload)

# a hand edited file that the update also changes: pull fails, head stays
root, origin, dev, user = make_world()
worlds.append(root)
publish(dev, "2.44.0")
with io.open(up.version_file(user), "a", encoding="utf-8") as handle:
    handle.write(u"# edited by hand\n")
head = git(user, "rev-parse", "HEAD")
o = up.update(user, CliGit())
check("dirty conflict: failed", o.status == up.FAILED, repr(o))
check("dirty conflict: head unchanged", git(user, "rev-parse", "HEAD") == head)
check("dirty conflict: detail carries git's reason", len(o.detail) > 0)
check("dirty conflict: says nothing changed, which is true",
      u"Nothing was changed" in up.message(o)[1])
check("dirty conflict: no reload", not o.should_reload)

# not a clone
plain = tempfile.mkdtemp(prefix="avh_plain_")
worlds.append(plain)
write_version(plain, "2.10.0")
o = up.update(plain, CliGit())
check("not a clone: status", o.status == up.NOT_A_CLONE, repr(o))
check("not a clone: points at the installer",
      u"Install_AVH_Extensions.bat" in up.message(o)[1])

# no tracked branch at all: divergence None must not crash or lie
root, origin, dev, user = make_world()
worlds.append(root)
git(user, "branch", "--unset-upstream")


class NoUpstreamPull(CliGit):
    def pull(self, repo):
        raise RuntimeError("There is no tracking information")


o = up.update(user, NoUpstreamPull())
check("no upstream: reported as failed, not up to date",
      o.status == up.FAILED, repr(o))


# --- fakes whose pull lies ----------------------------------------------

class LyingGit(object):
    """pull returns cleanly but the branch is still behind: the case
    pyRevit's git_pull hides by discarding MergeResult."""

    def discover(self, path):
        return path

    def head(self, repo):
        return "a" * 40

    def __init__(self):
        self.calls = 0

    def divergence(self, repo):
        self.calls += 1
        return (0, 2)

    def pull(self, repo):
        pass


o = up.update(plain, LyingGit())
check("silent no-op pull: not reported as up to date",
      o.status == up.NOT_APPLIED, repr(o))
check("silent no-op pull: no reload", not o.should_reload)


class HalfPull(LyingGit):
    """pull moves the head and then raises."""

    def __init__(self):
        LyingGit.__init__(self)
        self.sha = "a" * 40

    def head(self, repo):
        return self.sha

    def divergence(self, repo):
        return (0, 0)

    def pull(self, repo):
        self.sha = "b" * 40
        raise RuntimeError("network dropped")


o = up.update(plain, HalfPull())
check("half pull: does not claim nothing changed",
      u"Nothing was changed" not in up.message(o)[1], up.message(o)[1])

# --- the check itself can fail -------------------------------------------
check("probe", 0)
_probe = results.pop()
check("self-check: check() stores a real False", _probe[1] is False)


# --- part 2: the button script against a mocked pyrevit -----------------

class FakeDiv(object):
    def __init__(self, ahead, behind):
        self.AheadBy, self.BehindBy = ahead, behind


class FakeRepo(object):
    disposed = 0

    def Dispose(self):
        FakeRepo.disposed += 1


class FakeInfo(object):
    def __init__(self, state):
        self.repo = FakeRepo()
        self.last_commit_hash = state["head"]
        self.state = state


def install_fakes(state, alert_raises=False):
    calls = {"alerts": [], "reloads": 0}

    libgit = types.ModuleType("pyrevit.coreutils.git")
    libgit.libgit = types.SimpleNamespace(Repository=types.SimpleNamespace(
        Discover=lambda path: os.path.join(path, ".git")))
    libgit.get_repo = lambda path: FakeInfo(state)
    libgit.compare_branch_heads = lambda info: (
        None if state.get("no_upstream") else FakeDiv(state["ahead"],
                                                     state["behind"]))

    updater = types.ModuleType("pyrevit.versionmgr.updater")

    def update_repo(info):
        if state.get("pull_raises"):
            raise Exception(state["pull_raises"])
        if state["behind"]:
            state["head"] = "f" * 40
            state["behind"] = 0
    updater.update_repo = update_repo

    def alert(msg, title=None, warn_icon=True, **kw):
        calls["alerts"].append((title, msg, warn_icon))
        if alert_raises:
            raise Exception("dialog broke")

    forms = types.ModuleType("pyrevit.forms")
    forms.alert = alert
    script = types.ModuleType("pyrevit.script")
    script.get_logger = lambda: types.SimpleNamespace(
        debug=lambda *a, **k: None)

    sessionmgr = types.ModuleType("pyrevit.loader.sessionmgr")

    def reload_pyrevit():
        calls["reloads"] += 1
    sessionmgr.reload_pyrevit = reload_pyrevit

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.forms, pyrevit.script = forms, script
    coreutils = types.ModuleType("pyrevit.coreutils")
    coreutils.git = libgit
    versionmgr = types.ModuleType("pyrevit.versionmgr")
    versionmgr.updater = updater
    loader = types.ModuleType("pyrevit.loader")
    loader.sessionmgr = sessionmgr
    sys.modules.update({
        "pyrevit": pyrevit, "pyrevit.forms": forms, "pyrevit.script": script,
        "pyrevit.coreutils": coreutils, "pyrevit.coreutils.git": libgit,
        "pyrevit.versionmgr": versionmgr,
        "pyrevit.versionmgr.updater": updater,
        "pyrevit.loader": loader, "pyrevit.loader.sessionmgr": sessionmgr})
    return calls


def run_button(state, alert_raises=False):
    calls = install_fakes(state, alert_raises)
    FakeRepo.disposed = 0
    runpy.run_path(SCRIPT, run_name="__main__")
    return calls


calls = run_button({"head": "a" * 40, "ahead": 0, "behind": 3})
check("button, behind: reloads once", calls["reloads"] == 1, calls)
check("button, behind: one dialog, no warning icon",
      len(calls["alerts"]) == 1 and calls["alerts"][0][2] is False, calls)
check("button: every repository opened is disposed",
      FakeRepo.disposed >= 4, FakeRepo.disposed)

calls = run_button({"head": "a" * 40, "ahead": 0, "behind": 0})
check("button, up to date: no reload", calls["reloads"] == 0, calls)
check("button, up to date: says so",
      calls["alerts"] and u"up to date" in calls["alerts"][0][0], calls)

calls = run_button({"head": "a" * 40, "ahead": 2, "behind": 1})
check("button, local commits: no reload", calls["reloads"] == 0)
check("button, local commits: warning icon",
      calls["alerts"] and calls["alerts"][0][2] is True)

calls = run_button({"head": "a" * 40, "ahead": 0, "behind": 1,
                    "pull_raises": "failed to send request"})
check("button, pull fails: no reload", calls["reloads"] == 0)
check("button, pull fails: git's reason shown",
      calls["alerts"] and u"failed to send request" in calls["alerts"][0][1])

calls = run_button({"head": "a" * 40, "ahead": 0, "behind": 1},
                   alert_raises=True)
check("button, dialog throws: update still loaded", calls["reloads"] == 1)

calls = run_button({"head": "a" * 40, "no_upstream": True, "behind": 0})
check("button, no upstream: runs and does not reload",
      calls["reloads"] == 0)


# --- report -------------------------------------------------------------

for folder in worlds:
    shutil.rmtree(folder, ignore_errors=True)

print("Update AVH")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if not ok:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
