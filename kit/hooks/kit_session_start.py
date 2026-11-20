#!/usr/bin/env python3
"""agy SessionStart: serves the memory instead of hoping it is read.

Injects a systemMessage with: the board rule (one place per result), the project's own lines
(`kit.session_start_lines` in .kit/config.json), whether this clone is behind its remote, each
workstream's `Next:` line from .story/state/*.md, how many points await the owner (.story/awaits.md),
open issues' titles, and the lessons' count.
"""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, memory_dir, project_dir  # noqa: E402


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
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    c, root = config(data), project_dir(data)
    claude = is_claude_payload(data)
    k = c.get("kit", {})
    out = []
    if k.get("setup"):
        out.append("SETUP: the kit's install is in progress (README § Install); the stop hooks are quiet until step 8 runs "
                   "`install.py . --setup-done`.")
    url = c.get("board_url")
    mode = k.get("mode", "local")
    if mode == "hybrid" and not claude:
        out.append("MODE (hybrid): Claude Code Web owns the claude.ai Artifact board (%s); git (`board-mirror` + `.story/`) "
                   "is the bridge. Listen for new cards/comments from git with `python3 -I .kit/git_watch.py --runner computer` "
                   "(no local board server or Tailscale needed). Skills: `story`, `board`." % (url or "see .kit/config.json"))
    elif url:
        if claude:
            out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s), made at turn 1 and "
                       "updated in full. Every chat turn then ends with a single \"·\"; to the orchestrator: \"Done. Check post <id>.\"" % url)
        else:
            out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s, CLI `.kit/board_cli.py`), "
                       "made at turn 1 and updated in full. Every chat turn then ends with a single \"·\"; to the orchestrator: "
                       "\"Done. Check post <id>.\" Skills: `story`, `board`." % url)
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
    mdir = memory_dir(data)
    mrel = os.path.relpath(mdir, root)
    if os.path.exists(os.path.join(mdir, "workflow.md")):
        out.append("WORKFLOW: how the work gets done (tools, accounts and browser profiles, cycle, limits): %s/workflow.md; "
                   "each project's state file says where it runs (Runs in:)." % mrel)
    pref = os.path.join(mdir, "preferences.md")
    if os.path.exists(pref):
        with open(pref, encoding="utf-8") as fh:
            out += ["PREFERENCES (%s/preferences.md):" % mrel] + ["  " + l.strip() for l in fh if l.startswith("- ")][:15]
    states = sorted(glob.glob(os.path.join(mdir, "state", "*.md")))
    if states:
        out.append("MEMORY (%s/, skill memory): each workstream's next step:" % mrel)
        for s in states:
            out.append("  %s: %s" % (os.path.basename(s)[:-3], (first(s, "Next:") or "(no Next: line)")[:160]))
    aw = os.path.join(mdir, "awaits.md")
    if os.path.exists(aw):
        with open(aw, encoding="utf-8") as fh:
            n = sum(1 for l in fh if l.startswith("- "))
        out.append("AWAITS THE OWNER: %d points (%s/awaits.md)." % (n, mrel))
    issues = []
    for f in sorted(glob.glob(os.path.join(mdir, "issues", "*.md"))):
        with open(f, encoding="utf-8") as fh:
            t = fh.read()
        if "status: closed" not in t.lower():
            issues.append("  " + (t.splitlines()[0].lstrip("# ") if t else os.path.basename(f))[:110])
    if issues:
        out.append("OPEN ISSUES (%d): a logged defect is not diagnosed again:" % len(issues))
        out += issues[:20]
    ls = glob.glob(os.path.join(mdir, "lessons", "*.md"))
    if ls:
        out.append("LESSONS (%d): grep -ril '<word>' %s/lessons/" % (len(ls), mrel))
    msg = "\n".join(out)
    if claude:
        if msg:
            print(msg)
        return 0
    if msg:
        print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": msg}}]}, ensure_ascii=False))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
