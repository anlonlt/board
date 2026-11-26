#!/usr/bin/env python3
"""agy SessionStart: serves the memory instead of hoping it is read.

Injects a systemMessage with: the board rule (one place per result), the project's own lines
(`kit.session_start_lines` in .kit/config.json), whether this clone is behind its remote, where things stand (the
board: `kit.py resume`; CL28: never a memory file's `Next:` line), a pointer when old state files remain, and the
lessons' count.
"""
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, memory_dir, project_dir  # noqa: E402
from kit_pre_compact import inspect_claude_transcript  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    c, root = config(data), project_dir(data)
    claude = is_claude_payload(data)
    k = c.get("kit", {})
    out = []
    if str((data or {}).get("source") or "") in ("compact", "clear"):
        # M5: a resume after this prints whole again (its delta is against a context this session no longer holds)
        sid = re.sub(r"[^A-Za-z0-9._-]", "_", str((data or {}).get("session_id") or ""))
        for p in (glob.glob(os.path.join(root, ".kit", "run", "resume", "*-%s.json" % sid)) if sid else []):
            try:
                os.remove(p)
            except OSError:
                pass
    if claude and str((data or {}).get("source") or "") == "compact":
        # a compaction is the safety net past the kit's limit, never how a role goes on: its context is now a summary
        role = os.environ.get("KIT_ROLE") or inspect_claude_transcript(str(data.get("transcript_path") or ""))[1]
        if role in ("orchestrator", "clerk"):
            out.append("COMPACTED: this session should have handed over before. Do it now, before anything else: commit and push, then "
                       "`python3 -I .kit/handover.py --role %s`; your successor starts from `kit.py resume --role %s`." % (role, role))
        else:
            out.append("COMPACTED: your context is a summary. Start again from `python3 -I .kit/kit.py resume --role %s`, not from it." % (
                role if role in ("part", "review") else "part"))
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
        if claude and k.get("runtime") == "local":
            out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s, CLI `.kit/board_cli.py`), "
                       "made at turn 1 and updated in full. A turn the board or a session opened ends with a single \"·\" (the owner typing in your chat gets a short answer in words); to the orchestrator: "
                       "\"Done. Check post <id>.\" Skills: `memory`, `board`. The orchestrator starts from "
                       "`python3 -I .kit/kit.py resume --role orchestrator`." % url)
            sid = str(data.get("session_id") or "") if isinstance(data, dict) else ""
            if sid:
                out.append("SESSION: your Claude Code session id is %s (the `session_id` of a post you write, unless the "
                           "orchestrator gave you an agent name to use)." % sid)
        elif claude:
            out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s), made at turn 1 and "
                       "updated in full. A turn the board or a session opened ends with a single \"·\" (the owner typing in your chat gets a short answer in words); to the orchestrator: \"Done. Check post <id>.\"" % url)
        else:
            out.append("BOARD: your result goes in ONE place, your board post (.kit/BOARD.md, board %s, CLI `.kit/board_cli.py`), "
                       "made at turn 1 and updated in full. A turn the board or a session opened ends with a single \"·\" (the owner typing in your chat gets a short answer in words); to the orchestrator: "
                       "\"Done. Check post <id>.\" Skills: `story`, `board`." % url)
    if any(str(os.environ.get(v) or "").lower() in ("1", "true", "yes")
           for v in ("CLAUDE_CODE_REMOTE", "KIT_REMOTE", "AGY_REMOTE")) and os.path.exists(
            os.path.join(root, ".kit", "web_takeover.py")):
        out.append("WEB: this is a remote/web session: no board server yet until you run "
                   "`python3 -I .kit/web_takeover.py` (the board from git + the steps to continue). The orchestrator then "
                   "starts from `python3 -I .kit/kit.py resume --role orchestrator`. A part continues on its "
                   "branch; the board goes back to git with `python3 -I .kit/web_takeover.py save` (the Stop hook runs it too).")
    landing = os.path.join(root, ".kit", "landing.py")
    if os.path.isfile(landing):
        try:                                       # a hook of git's own lives in each clone: this one gets it here
            r = subprocess.run([sys.executable, "-I", landing, "setup"], capture_output=True, text=True, timeout=15, cwd=root)
            if "NOT SET" in r.stdout:
                out.append("PRE-PUSH: " + r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    talk = c.get("language", {}).get("talk")
    if talk:
        out.append("LANGUAGE: talk to the owner in %s." % talk)
    out += k.get("session_start_lines", [])
    trunk = k.get("main_branch", "main")
    out.append("END: finish with `python3 -I .kit/end_session.py`: %s" % (
        "it merges your work onto %s and pushes it, never forced (the owner's standing permission)" % trunk
        if k.get("trunk_push") else "it pushes your branch (the owner has not given the permission to push %s)" % trunk))
    fresh = []                                     # CL53: the checkout on the newest code, said before anything else
    kitpy = os.path.join(root, ".kit", "kit.py")
    if os.path.isfile(kitpy):
        try:
            sid = str((data or {}).get("session_id") or "") if isinstance(data, dict) else ""
            r = subprocess.run([sys.executable, "-I", kitpy, "fresh"] + (["--session", sid] if sid else []),
                               capture_output=True, text=True, timeout=40, cwd=root)
            fresh = [l for l in r.stdout.splitlines() if l.startswith("CHECKOUT: ")]
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            b = subprocess.run(["git", "-C", root, "rev-list", "--count", "HEAD..@{u}"], capture_output=True, text=True, timeout=10)
            if b.returncode == 0 and b.stdout.strip() not in ("", "0"):
                fresh.append("CLONE: %s commits behind its remote: git pull --no-rebase first." % b.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    out[:0] = fresh
    mdir = memory_dir(data)
    mrel = os.path.relpath(mdir, root)
    if os.path.exists(os.path.join(mdir, "workflow.md")):
        out.append("WORKFLOW: how the work gets done (tools, accounts and browser profiles, cycle, limits): %s/workflow.md; "
                   "which runner runs which project: § Runners." % mrel)
    pref = os.path.join(mdir, "preferences.md")
    if os.path.exists(pref):
        with open(pref, encoding="utf-8") as fh:
            out += ["PREFERENCES (%s/preferences.md):" % mrel] + ["  " + l.strip() for l in fh if l.startswith("- ")][:15]
    role = os.environ.get("KIT_ROLE", "")
    # CL28 (the owner, 2026-11-23: "everything is on the board"): where things stand is never served from memory files.
    # A stale `Next:` line here once told the owner a decision waited on him that was long settled.
    if role in ("part", "clerk", "review"):
        out.append("PLAN: the trains on the board are the plan (`python3 -I .kit/kit.py train <train>`); your prompt says "
                   "which step is yours.")
    elif role == "orchestrator":
        out.append("PLAN: the trains on the board are your task list (`python3 -I .kit/kit.py resume` rebuilds it); "
                   "run it again before you answer the owner.")
    else:
        out.append("WHERE THINGS STAND: on the board, the one record: `python3 -I .kit/kit.py resume` (each train's "
                   "open steps from their posts, questions included, and the owner's unanswered comments).")
    if os.path.isfile(os.path.join(root, "install.py")) and os.path.isdir(os.path.join(root, "kit", "hooks")):
        out.append("KIT COMMANDS (this is the kit's own repo): `python3 -I .kit/kit.py commands <word>` before writing one: reuse, never rebuild.")
    legacy = [n for n in ("awaits.md",) if os.path.exists(os.path.join(mdir, n))]
    legacy += ["state/%s" % os.path.basename(f) for f in sorted(glob.glob(os.path.join(mdir, "state", "*.md")))
               if os.path.basename(f) != "orchestrator.md"]
    legacy += ["issues/"] if glob.glob(os.path.join(mdir, "issues", "*.md")) else []
    if legacy and role not in ("part", "clerk", "review"):
        out.append("OLD MEMORY (%d files, not read: the board holds where things stand): `python3 -I .kit/kit.py compact` "
                   "archives %s/state, awaits.md and issues/ once; post any open issue on the board first." % (len(legacy), mrel))
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
