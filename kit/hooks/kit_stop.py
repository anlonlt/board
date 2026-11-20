#!/usr/bin/env python3
"""Stop: blocks a stop ONCE while work is uncommitted or unpushed (origin: anlonlt/harmonize stop.sh).

A container is reclaimed after a while; what is not pushed is lost. Blocks with exit 2 and the reason on stderr,
unless Claude Code says the stop hook is already active (the second stop goes through).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import in_setup, project_dir  # noqa: E402


def git(*a):
    r = subprocess.run(["git", "-C", project_dir()] + list(a), capture_output=True, text=True, timeout=15)
    return r.returncode, r.stdout.strip()


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    if data.get("stop_hook_active") or in_setup():
        return 0  # the second stop, or a setup turn waiting on the owner or an agent (README step 8 ends it)
    try:
        rc, _ = git("rev-parse", "--is-inside-work-tree")
        if rc:
            return 0
        _, dirty = git("status", "--porcelain", "--untracked-files=no")
        rc, ahead = git("rev-list", "--count", "@{u}..HEAD")
    except (OSError, subprocess.SubprocessError):
        return 0
    why = []
    if dirty:
        why.append("%d tracked files changed and not committed" % len(dirty.splitlines()))
    if rc == 0 and ahead not in ("", "0"):
        why.append("%s commits not pushed" % ahead)
    if not why:
        return 0
    print("Before stopping: %s. Commit by name, then `python3 -I .kit/end_session.py` (it pushes, and merges onto the "
          "trunk when the owner allowed it), or say why not." % "; ".join(why), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
