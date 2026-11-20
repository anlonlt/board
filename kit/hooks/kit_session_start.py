#!/usr/bin/env python3
"""SessionStart: serves the memory instead of hoping it is read (origin: anlonlt/harmonize session-start.sh).

Prints: the board rule (one place per result), the project's own lines (`kit.session_start_lines` in .kit/config.json),
whether this clone is behind its remote, each workstream's `Next:` line from .story/state/*.md, how many points await
the owner (.story/awaits.md lines starting "- "), open issues' titles (.story/issues/*.md without "Status: closed"),
and the lessons' count. Plain stdout: Claude Code adds it to the session's context.
"""
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, project_dir  # noqa: E402


def first(path, prefix):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().lower().startswith(prefix.lower()):
                    return line.strip()[len(prefix):].strip()
    except OSError:
        pass
    return None


def main():
    c, root = config(), project_dir()
    k = c.get("kit", {})
    out = []
    if k.get("setup"):
        out.append("SETUP: the kit's install is in progress (README § Install); the stop hooks are quiet until step 8 runs "
                   "`install.py . --setup-done`.")
    url = c.get("board_url")
    if url:
        out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s), made at turn 1 and "
                   "updated in full. Every chat turn then ends with a single \"·\"; to the orchestrator: \"Done. Check post <id>.\"" % url)
    talk = c.get("language", {}).get("talk")
    if talk:
        out.append("LANGUAGE: talk to the owner in %s." % talk)
    out += k.get("session_start_lines", [])
    trunk = k.get("main_branch", "main")
    out.append("END: finish with `python3 -I .kit/end_session.py`: %s" % (
        "it merges your work onto %s and pushes it, never forced (the owner's standing permission)" % trunk
        if k.get("trunk_push") else "it pushes your branch (the owner has not given the permission to push %s)" % trunk))
    try:
        b = subprocess.run(["git", "-C", root, "rev-list", "--count", "HEAD..@{u}"], capture_output=True, text=True, timeout=10)
        if b.returncode == 0 and b.stdout.strip() not in ("", "0"):
            out.append("CLONE: %s commits behind its remote: git pull --no-rebase first." % b.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    if os.path.exists(os.path.join(root, ".story", "workflow.md")):
        out.append("WORKFLOW: how the work gets done (tools, accounts and browser profiles, cycle, limits): .story/workflow.md; "
                   "each project's state file says where it runs (Runs in:).")
    pref = os.path.join(root, ".story", "preferences.md")
    if os.path.exists(pref):
        with open(pref, encoding="utf-8") as fh:
            out += ["PREFERENCES (.story/preferences.md):"] + ["  " + l.strip() for l in fh if l.startswith("- ")][:15]
    states = sorted(glob.glob(os.path.join(root, ".story", "state", "*.md")))
    if states:
        out.append("MEMORY (.story/, skill story): each workstream's next step:")
        for s in states:
            out.append("  %s: %s" % (os.path.basename(s)[:-3], (first(s, "Next:") or "(no Next: line)")[:160]))
    aw = os.path.join(root, ".story", "awaits.md")
    if os.path.exists(aw):
        with open(aw, encoding="utf-8") as fh:
            n = sum(1 for l in fh if l.startswith("- "))
        out.append("AWAITS THE OWNER: %d points (.story/awaits.md)." % n)
    issues = []
    for f in sorted(glob.glob(os.path.join(root, ".story", "issues", "*.md"))):
        with open(f, encoding="utf-8") as fh:
            t = fh.read()
        if "status: closed" not in t.lower():
            issues.append("  " + (t.splitlines()[0].lstrip("# ") if t else os.path.basename(f))[:110])
    if issues:
        out.append("OPEN ISSUES (%d): a logged defect is not diagnosed again:" % len(issues))
        out += issues[:20]
    ls = glob.glob(os.path.join(root, ".story", "lessons", "*.md"))
    if ls:
        out.append("LESSONS (%d): grep -ril '<word>' .story/lessons/" % len(ls))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
