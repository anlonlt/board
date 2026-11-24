#!/usr/bin/env python3
"""Work reaches the trunk after its review, and reaches git while it is being done. No agent has to remember either.

    landing.py land POST          merge a reviewed step's branch onto the trunk (the review runs it on `continue`)
    landing.py save [--quiet]     a snapshot of the working tree on wip/<branch>; the tree, index and branch untouched
    landing.py pre-push           git's own hook, any agent: the trunk takes no step that still waits for its review
    landing.py setup              that hook in this clone (a hook of the project's own keeps running, before it)

Config (`kit` in .kit/config.json): `land`: "after_review" (the default where the kit may push the trunk and reviews run)
or "at_done"; `wip`: "auto" (the default: pushed only when the GitHub repo is private, else kept in this clone under
refs/wip/), "push", "local" or "off", and `wip_minutes` (5); `pre_push`: false leaves git's hooks alone.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

KIT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("KIT_LANDING_ROOT") or os.path.dirname(KIT)
MARK = "# board kit: pre-push"
SECRETS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "keystore.properties", "id_rsa*", "id_ed25519*",
           "*credentials*.json", "*secret*", ".netrc", ".npmrc", ".pypirc")
MAX_FILE = 2 * 1024 * 1024
SHIM = """#!/bin/sh
%s (landing.py setup wrote this; a hook of the project's own is pre-push.local, run first)
input=$(cat)
here=$(dirname "$0")
if [ -x "$here/pre-push.local" ]; then printf '%%s\\n' "$input" | "$here/pre-push.local" "$@" || exit $?; fi
root=$(git rev-parse --show-toplevel 2>/dev/null)
[ -f "$root/.kit/landing.py" ] || exit 0
printf '%%s\\n' "$input" | python3 -I "$root/.kit/landing.py" pre-push "$@"
""" % MARK


def git(*a, cwd=None, timeout=120, env=None, data=None):
    return subprocess.run(["git", "-C", cwd or ROOT] + list(a), capture_output=True, text=True, timeout=timeout,
                          input=data, env=dict(os.environ, **(env or {})))


def out(*a, **k):
    r = git(*a, **k)
    return r.stdout.strip() if r.returncode == 0 else ""


def kit_cfg():
    try:
        with open(os.path.join(ROOT, ".kit", "config.json"), encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("kit") or {}
    except (OSError, ValueError):
        return {}


def trunk():
    return str(kit_cfg().get("main_branch") or "main")


def steps(*args):
    """What the board says of the steps' landing (`kit.py landing`), or None when it cannot be read."""
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, ".kit", "kit.py"), "landing"] + list(args), capture_output=True,
                           text=True, timeout=60, cwd=ROOT)
        return json.loads(r.stdout)["steps"] if r.returncode == 0 else None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def contains(a, b):
    """b holds every commit of a."""
    return bool(a and b) and git("merge-base", "--is-ancestor", a, b).returncode == 0


# ---------------------------------------------------------------- land
def _fetch(*refs):
    return all(git("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (r, r), timeout=300).returncode == 0 for r in refs)


def _check(cmd, where, timeout):
    try:
        r = subprocess.run(cmd, shell=True, cwd=where, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, KIT_BREAKS_COPY="1", PYTHONDONTWRITEBYTECODE="1"))
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except subprocess.TimeoutExpired:
        return 124, "no answer in %d s" % timeout


class _Tree:
    """A throwaway worktree at a commit."""

    def __init__(self, rev):
        self.d = os.path.join(tempfile.mkdtemp(prefix="kit-land-"), "w")
        self.err = git("worktree", "add", "-q", "--detach", self.d, rev).stderr.strip()

    def __enter__(self):
        return self.d

    def __exit__(self, *a):
        git("worktree", "remove", "--force", self.d)
        try:
            os.rmdir(os.path.dirname(self.d))
        except OSError:
            pass


def land(pid):
    """(exit code, text). Merged on the trunk as it is now, never forced, nothing pushed on a conflict."""
    got = steps("--post", pid)
    if got is None:
        return 1, "land %s: NOT LANDED: the board cannot be read here" % pid
    s = next((x for x in got if x["post"] == pid), None)
    if s is None:
        return 1, "land %s: no such step on this board" % pid
    if s.get("landed"):
        return 0, "land %s: already on the trunk" % pid
    if s["held"]:
        return 1, "land %s: NOT LANDED: %s" % (pid, s["why"])
    t, br = trunk(), s.get("branch") or ""
    if not kit_cfg().get("trunk_push"):
        return 1, "land %s: NOT LANDED: the owner has not given the permission to push %s (kit.trunk_push)" % (pid, t)
    if not br or br == t:
        return 1, "land %s: NOT LANDED: its post names no branch of its own" % pid
    for turn in range(5):
        if not _fetch(t):
            return 1, "land %s: NOT LANDED: cannot fetch origin/%s" % (pid, t)
        if not _fetch(br):
            seen = s.get("seen") or ""
            if contains(seen, "origin/" + t):
                return _landed(pid, br, t, seen)
            return 1, "land %s: NOT LANDED: origin/%s is not there and its work is not on %s" % (pid, br, t)
        tip = out("rev-parse", "origin/" + br)
        if contains(tip, "origin/" + t):
            return _landed(pid, br, t, tip)
        seen = s.get("seen") or ""
        new = out("rev-list", "--no-merges", tip, "^" + seen, "^origin/" + t).split() if seen and out("cat-file", "-t", seen) else []
        if seen and not out("cat-file", "-t", seen):
            return 1, "land %s: NOT LANDED: the commit its review read (%s) is not on %s any more" % (pid, seen[:9], br)
        if new:
            return 1, ("land %s: NOT LANDED: %d commit(s) on %s after its review (%s ...): `kit.py done %s`, then `kit.py review %s --force`"
                       % (pid, len(new), br, new[0][:9], pid, pid))
        with _Tree("origin/" + t) as w:
            m = git("-c", "user.name=board kit", "-c", "user.email=kit@localhost", "merge", "-q", "--no-edit", "-m",
                    "Land %s (%s), reviewed" % (s.get("part") or pid, pid), tip, cwd=w)
            if m.returncode:
                files = out("diff", "--name-only", "--diff-filter=U", cwd=w).split()
                git("merge", "--abort", cwd=w)
                return 1, "land %s: NOT LANDED: conflict with %s in %s: its part merges origin/%s on %s, reruns its check, pushes, then land again" % (
                    pid, t, " ".join(files) or "?", t, br)
            head = out("rev-parse", "HEAD", cwd=w)
            cmd = str(s.get("check") or "").strip()
            if cmd and not cmd.lower().startswith("manual:") and head != tip:            # the trunk moved: the two together
                lim = int(kit_cfg().get("check_timeout_s") or 900)
                rc, tail = _check(cmd, w, lim)
                if rc:
                    with _Tree(tip) as alone:
                        rc0, _ = _check(cmd, alone, lim)
                    if rc0 == 0:
                        return 1, ("land %s: NOT LANDED: its check passes on %s and fails merged with %s (exit %d): %s" % (pid, br, t, rc, tail))
                    print("land %s: its check cannot run in a bare checkout (it fails on %s alone too): not a reason to hold it" % (pid, br))
            if git("push", "origin", "%s:refs/heads/%s" % (head, t), cwd=w, timeout=300).returncode == 0:
                return _landed(pid, br, t, head)
        time.sleep(2 + turn)
    return 1, "land %s: NOT LANDED: %s was refused at the push 5 times: run it again" % (pid, t)


def _landed(pid, br, t, sha):
    subprocess.run([sys.executable, "-I", os.path.join(ROOT, ".kit", "kit.py"), "landing", "--post", pid, "--mark", sha], capture_output=True,
                   text=True, timeout=60, cwd=ROOT)
    gone = []
    if not kit_cfg().get("keep_branches"):
        for b in (br, "wip/" + br, "review-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", pid)[:60]):
            if out("ls-remote", "--heads", "origin", "refs/heads/" + b) and git("push", "-q", "origin", "--delete", b).returncode == 0:
                gone.append(b)
    git("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (t, t))
    return 0, "land %s: on %s at %s%s" % (pid, t, sha[:9], ("; origin dropped " + ", ".join(gone)) if gone else "")


# ---------------------------------------------------------------- save
def _private():
    """True only when GitHub says a stranger cannot see this repo (404). Anything else (public, no network, rate limit,
    another host) is False: nothing is pushed on a guess."""
    url = out("remote", "get-url", "origin")
    m = re.search(r"(?:github\.com[:/]|/git/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    if not m:
        return False
    memo = os.path.join(ROOT, ".kit", "run", "wip-private.json")
    try:
        with open(memo, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("url") == url and time.time() - float(d.get("at") or 0) < 6 * 3600:
            return bool(d.get("private"))
    except (OSError, ValueError, TypeError):
        pass
    try:
        urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/%s/%s" % m.groups(), headers={"User-Agent": "git"}), timeout=8)
        private = False
    except urllib.error.HTTPError as e:
        private = e.code == 404
    except (OSError, ValueError):
        return False
    try:
        os.makedirs(os.path.dirname(memo), exist_ok=True)
        with open(memo, "w", encoding="utf-8") as fh:
            json.dump({"url": url, "private": private, "at": time.time()}, fh)
    except OSError:
        pass
    return private


def wip_name():
    br = out("symbolic-ref", "--short", "-q", "HEAD")
    if not br or br.startswith("wip/"):
        return ""
    if br == trunk():
        sid = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIT_SESSION_ID") or ""
        return "wip/%s-%s" % (br, re.sub(r"\W", "", sid)[-8:] or "here")
    return "wip/" + br


def _untracked(env):
    never = tuple((kit_cfg().get("guard") or {}).get("never_add") or ()) + SECRETS
    keep = []
    for f in git("ls-files", "--others", "--exclude-standard", "-z", env=env).stdout.split("\0"):
        low = f.lower()
        if not f or any(fnmatch.fnmatch(low, p.lower()) or fnmatch.fnmatch(os.path.basename(low), p.lower()) for p in never):
            continue
        try:
            if os.path.getsize(os.path.join(ROOT, f)) <= MAX_FILE:
                keep.append(f)
        except OSError:
            pass
    return keep


def save():
    """(exit code, text). One commit on wip/<branch>: parents HEAD and the last save, so nothing is ever forced."""
    mode = str(kit_cfg().get("wip") or "auto")
    name = wip_name()
    head = out("rev-parse", "-q", "--verify", "HEAD")
    if mode == "off" or not name or not head:
        return 0, "save: nothing (%s)" % ("kit.wip is off" if mode == "off" else "no branch here")
    with tempfile.TemporaryDirectory(prefix="kit-wip-") as d:
        env = {"GIT_INDEX_FILE": os.path.join(d, "index")}
        if git("read-tree", "HEAD", env=env).returncode or git("add", "-u", env=env).returncode:
            return 1, "save: FAILED: could not read the working tree"
        new = _untracked(env)
        if new:
            git("update-index", "--add", "-z", "--stdin", env=env, data="\0".join(new) + "\0")
        tree = out("write-tree", env=env)
    if not tree:
        return 1, "save: FAILED: could not write the tree"
    last = out("rev-parse", "-q", "--verify", "refs/" + name)
    if tree == out("rev-parse", "HEAD^{tree}") and out("branch", "-r", "--contains", head):
        return 0, "save: nothing (all of it is committed and on origin)"
    if not (last and out("rev-parse", last + "^{tree}") == tree and contains(head, last)):
        parents = ["-p", head] + (["-p", last] if last and not contains(last, head) else [])
        c = out("-c", "user.name=board kit", "-c", "user.email=kit@localhost", "commit-tree", tree, *parents, "-m", "wip: unfinished work on %s" % name[4:])
        if not c or git("update-ref", "refs/" + name, c).returncode:
            return 1, "save: FAILED: could not write refs/%s" % name
        last = c
    if mode == "local" or (mode != "push" and not _private()):
        return 0, "save: %s kept in this clone (refs/%s)%s" % (last[:9], name, "" if mode == "local" else "; not pushed: this repo is not known to be private")
    for _ in range(2):
        if git("push", "-q", "origin", "%s:refs/heads/%s" % (last, name), timeout=120, env={"KIT_WIP_PUSH": "1"}).returncode == 0:
            git("update-ref", "refs/remotes/origin/" + name, last)
            return 0, "save: %s on origin/%s" % (last[:9], name)
        if not _fetch(name):                                     # another clone saved there: keep its save too, under ours
            break
        theirs = out("rev-parse", "origin/" + name)
        if not theirs or contains(theirs, last):
            break
        last = out("-c", "user.name=board kit", "-c", "user.email=kit@localhost", "commit-tree", last + "^{tree}", "-p", last, "-p", theirs, "-m", "wip")
        git("update-ref", "refs/" + name, last)
    return 1, "save: %s kept in this clone; origin did not take it" % last[:9]


def wip_lines(branch):
    """What a session that continues `branch` finds saved beyond its last commit."""
    name = "wip/" + branch
    if out("ls-remote", "--heads", "origin", "refs/heads/" + name):
        _fetch(name)
    ref = next((r for r in ("refs/remotes/origin/" + name, "refs/" + name) if out("rev-parse", "-q", "--verify", r)), "")
    tip = next((r for r in ("refs/remotes/origin/" + branch, "refs/heads/" + branch) if out("rev-parse", "-q", "--verify", r)), "")
    if not ref or (tip and out("diff", "--stat", tip, ref) == ""):
        return []
    when = out("log", "-1", "--format=%cI", ref)
    what = out("diff", "--shortstat", tip, ref) if tip else ""
    return ["- unfinished work saved %s on `%s`%s: on that branch, `git restore --source %s --worktree -- .`, read the diff, then commit what holds"
            % (when[:16], ref.replace("refs/remotes/", "").replace("refs/", ""), (" (%s)" % what.strip()) if what else "", ref)]


# ---------------------------------------------------------------- pre-push
def pre_push(lines):
    """(exit code, text). Only the trunk is looked at: never deleted, never rewound, no step that waits for its review."""
    t = "refs/heads/" + trunk()
    for ln in lines:
        p = ln.split()
        if len(p) != 4 or p[2] != t:
            continue
        new, old = p[1], p[3]
        if not new.strip("0"):
            return 1, "pre-push: REFUSED: this deletes %s" % trunk()
        if old.strip("0") and out("cat-file", "-t", old) and not contains(old, new):
            return 1, "pre-push: REFUSED: this rewrites %s (what is on it would be lost): merge it instead" % trunk()
        got = steps()
        if got is None:
            print("pre-push: the board cannot be read here: the steps that wait for a review were not looked at", file=sys.stderr)
            continue
        for s in got:
            br = s.get("branch") or ""
            shas = {x for x in (s.get("seen"), s.get("commit"), out("rev-parse", "-q", "--verify", "refs/remotes/origin/" + br),
                                out("rev-parse", "-q", "--verify", "refs/heads/" + br)) if x and out("cat-file", "-t", x)}
            if s["held"] and any(contains(x, new) and not (old.strip("0") and contains(x, old)) for x in shas):
                return 1, ("pre-push: REFUSED: %s holds step %s (%s), which is not reviewed yet (%s). It lands by itself after its review: push "
                           "its branch only." % (trunk(), s.get("part") or s["post"], s["post"], s["why"]))
    return 0, ""


def setup():
    """(exit code, text): git's pre-push hook in this clone."""
    if kit_cfg().get("pre_push") is False:
        return 0, "pre-push: off (kit.pre_push)"
    hooks = out("rev-parse", "--git-path", "hooks")
    if not hooks:
        return 1, "pre-push: not a git checkout"
    hooks = os.path.normpath(os.path.join(ROOT, hooks))
    if out("config", "core.hooksPath"):
        own = os.path.join(hooks, "pre-push")
        try:
            with open(own, encoding="utf-8", errors="replace") as fh:
                if ".kit/landing.py" in fh.read():
                    return 0, "pre-push: in the project's own hooks"
        except OSError:
            pass
        return 1, ("pre-push: NOT SET: this project keeps its hooks in %s (core.hooksPath): add this line to its pre-push: "
                   "`python3 -I .kit/landing.py pre-push`" % os.path.relpath(hooks, ROOT))
    p = os.path.join(hooks, "pre-push")
    try:
        os.makedirs(hooks, exist_ok=True)
        cur = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                cur = fh.read()
        if cur == SHIM:
            return 0, "pre-push: set"
        if cur and MARK not in cur:
            if os.path.exists(p + ".local"):
                return 1, "pre-push: NOT SET: %s and its .local are both the project's" % p
            os.rename(p, p + ".local")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SHIM)
        os.chmod(p, 0o755)
    except OSError as e:
        return 1, "pre-push: NOT SET: %s" % e
    return 0, "pre-push: set"


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    cmd = a[0] if a else ""
    if cmd == "land" and len(a) > 1:
        rc, txt = land(a[1])
    elif cmd == "save":
        rc, txt = save()
        txt = "" if "--quiet" in a else txt
    elif cmd == "pre-push":
        rc, txt = pre_push(sys.stdin.read().splitlines())
        if txt:
            print(txt, file=sys.stderr)
        return rc
    elif cmd == "setup":
        rc, txt = setup()
    elif cmd == "wip" and len(a) > 1:
        rc, txt = 0, "\n".join(wip_lines(a[1]))
    else:
        rc, txt = 2, __doc__
    if txt:
        print(txt)
    return rc


if __name__ == "__main__":
    sys.exit(main())
