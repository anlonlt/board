#!/usr/bin/env python3
"""End a session's work ON the trunk (origin: anlonlt/harmonize .claude/skills/story/scripts/end-session.sh).

    python3 -I .kit/end_session.py [--branch-only]

Every session starts from the trunk (`kit.main_branch`, default main) and ends on it, so the next session, the board
and the owner see one history, not a pile of branches. In order, stopping at the first fault (nothing is pushed then):
  1. no uncommitted change (commit by name first);
  2. push this branch (`-u`), with retries;
  3. unless --branch-only: fetch the trunk, merge it into this branch if it moved (a conflict stops everything: the
     merge is aborted and the conflicting files are named), then push this branch's HEAD to the trunk, never forced;
     if the trunk moved during the push, again, up to 8 times; then this branch is deleted from origin (every commit
     of it is on the trunk; `kit.keep_branches: true` keeps it).
The owner's standing permission to push the trunk is asked once at install (README step 2) and written in CLAUDE.md;
without it, --branch-only is the default (`kit.trunk_push: false` in .kit/config.json).
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = ("board-mirror", "kit-usage")


def git(*a, check=False):
    r = subprocess.run(["git", "-C", ROOT] + list(a), capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit("end-session: git %s failed: %s" % (" ".join(a), (r.stderr or r.stdout).strip()[-300:]))
    return r


def push(*a):
    for i in range(5):
        r = git("push", *a)
        if r.returncode == 0:
            return True
        time.sleep(2 ** i)
    print((r.stderr or r.stdout).strip()[-300:])
    return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        k = json.load(open(os.path.join(ROOT, ".kit", "config.json"), encoding="utf-8")).get("kit", {})
    except (OSError, ValueError):
        k = {}
    trunk = k.get("main_branch", "main")
    branch_only = "--branch-only" in argv or not k.get("trunk_push", False)
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        raise SystemExit("end-session: detached HEAD: nothing to push")
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        print("end-session: changes are not committed; commit them by name, then run again:\n" + "\n".join(
            "    " + l for l in dirty.splitlines()[:20]))
        return 1
    usage = os.path.join(ROOT, ".kit", "usage.py")
    if os.path.isfile(usage):  # CL37: this session's usage rows onto the ledger branch (the working tree is untouched)
        try:
            u = subprocess.run([sys.executable, "-I", usage, "--root", ROOT, "push"], capture_output=True, text=True,
                               timeout=120)
            print("end-session: usage ledger: " + (u.stdout.strip() or u.stderr.strip()[-200:] or "?"))
        except (OSError, subprocess.SubprocessError):
            pass
    if branch == trunk and branch_only:
        print("end-session: refusing to push directly to trunk '%s' (--branch-only, or no standing permission: kit.trunk_push is false). Commit on a feature branch instead." % trunk)
        return 1
    if branch != trunk:
        if not push("-u", "origin", branch):
            print("end-session: pushing %s failed; nothing else done" % branch)
            return 1
        print("end-session: %s pushed (%s)" % (branch, git("rev-parse", "--short", "HEAD").stdout.strip()))
        if branch_only:
            print("end-session: the trunk is not moved (--branch-only, or no standing permission: kit.trunk_push)")
            return 0
    for _ in range(8):
        if git("fetch", "-q", "origin", trunk).returncode:
            print("end-session: cannot fetch %s; the trunk is not moved" % trunk)
            return 1
        if git("merge-base", "--is-ancestor", "origin/" + trunk, "HEAD").returncode:
            m = git("merge", "-q", "--no-edit", "origin/" + trunk)
            if m.returncode:
                files = git("diff", "--name-only", "--diff-filter=U").stdout.split()
                if files:
                    git("merge", "--abort")
                    print("end-session: CONFLICT merging %s: %s. Merge it by hand, then run again." % (trunk, " ".join(files)))
                else:
                    print("end-session: git merge origin/%s failed: %s" % (trunk, (m.stderr or m.stdout).strip()[-300:]))
                return 1
            if branch != trunk and not push("origin", branch):
                return 1
        if git("push", "origin", "HEAD:" + trunk).returncode == 0:
            print("end-session: %s is at %s" % (trunk, git("rev-parse", "--short", "HEAD").stdout.strip()))
            if branch != trunk and branch not in KEEP and not k.get("keep_branches"):
                # CL38 (the owner's Like, o54-branches-0225): the branch is on the trunk now, every commit of it;
                # origin drops it (a later push of this session makes it again). 40 such branches piled up by CL38.
                dr = git("push", "-q", "origin", "--delete", branch)
                if dr.returncode == 0:
                    git("branch", "--unset-upstream")
                    print("end-session: origin/%s deleted (all of it is on %s)" % (branch, trunk))
                else:   # a Claude Code web session's git proxy may refuse it: said, never silent
                    print("end-session: origin/%s NOT deleted (%s); `kit.py branches --delete` from a session that may"
                          % (branch, (dr.stderr or dr.stdout).strip().splitlines()[-1:] or "?"))
            return 0
        time.sleep(3)
    print("end-session: the trunk kept moving; run again")
    return 1


if __name__ == "__main__":
    sys.exit(main())
