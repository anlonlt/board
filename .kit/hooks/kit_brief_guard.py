#!/usr/bin/env python3
"""The orchestrator answers the owner only from a fresh read of the board, `kit.py resume` (CL28). Claude Code (CLI and web) and agy.

The owner, 2026-11-23, after orchestrator 51 told the owner A3 waited on their choice (a speaker memory line unchanged since
11-16, A3 held since then) and missed two open decision posts: "Isn't there a hook, or a memory, or something that's
supposed to keep you up to date on stuff, no matter who or where you are as an orchestrator? What does the kit make
you check before answering?" This hook is that check:

  - PreToolUse, for the orchestrator only: a reply to the owner (ArtifactData set/update/batch of a comment with
    `reply_to` in comments or repos/<id>/comments; `board_cli.py reply`, or any board_cli write naming `reply_to`
    (CL30), in Bash / run_command; agy's MCP `board_reply` checks the same stamp, .kit/mcp_stdio.py) is refused unless
    `kit.py resume` ran in this session in the last `kit.brief.max_age_min` minutes (15); the refusal names the command.
  - PostToolUse on Bash / run_command: a `kit.py resume` that ran stamps the session (.kit/run/brief/<session>).
Everyone else (parts, the clerk, the owner's own sessions) is never refused. Exit 2 with the reason on stderr (Claude
Code), {"decision": "deny"} (agy).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, project_dir  # noqa: E402

COMMENTS = re.compile(r"^(repos/[^/]+/)?comments$")
REPLY_CMD = re.compile(r"board_cli\.py\b[^\n;&|]*\breply\b")
# CL30: a reply written by any board_cli write (set / update / str-replace / batch of a comment naming reply_to)
REPLY_WRITE = re.compile(r"board_cli\.py\b[^\n;&|]*\b(?:set|update|str-replace|batch)\b[^\n;&|]*reply[_-]to")
BRIEF_CMD = re.compile(r"\bkit\.py\b[^\n;&|]*\bresume\b")
DEFAULT_MAX_AGE_MIN = 15
WAY = "python3 -I .kit/kit.py resume"


def sid_of(data):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or data.get("conversationId")
                                             or data.get("conversation_id") or ""))


def stamp_dir(data):
    return os.path.join(project_dir(data), ".kit", "run", "brief")


def max_age_min(data):
    try:
        return float(((config(data).get("kit") or {}).get("brief") or {}).get("max_age_min", DEFAULT_MAX_AGE_MIN))
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_MIN


def brief_age_min(data):
    """Minutes since this session's last `kit.py resume` (its stamp), None if never."""
    sid = sid_of(data)
    path = os.path.join(stamp_dir(data), sid or "last")
    try:
        return (datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)) / 60.0
    except OSError:
        return None


def is_orchestrator(data):
    role = os.environ.get("KIT_ROLE") or ""
    if role:
        return role == "orchestrator"
    try:
        import kit_orchestrator_guard
        return kit_orchestrator_guard.is_orchestrator(data)
    except Exception:  # noqa: BLE001 - the guard module missing or failing never blocks a reply
        return False


def _file_json(path, root):
    try:
        p = path if os.path.isabs(path) else os.path.join(root, path)
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_has_reply(w, root):
    if not COMMENTS.match(str(w.get("collection") or "")):
        return False
    if str(w.get("op") or w.get("action") or "") not in ("set", "update"):
        return False
    d = w.get("data") if isinstance(w.get("data"), dict) else _file_json(str(w.get("file_path") or ""), root)
    return bool(d.get("reply_to")) and str(d.get("from") or "") not in ("owner", "viewer")


def is_reply(data):
    """What the call is: a reply to the owner's comment, or not."""
    tc = data.get("toolCall") or {}
    tname = str(tc.get("name") or data.get("tool_name") or "")
    ti = data.get("tool_input") or tc.get("args") or {}
    root = project_dir(data)
    if tname.endswith("ArtifactData"):
        act = str(ti.get("action") or "")
        if act == "batch":
            return any(_write_has_reply(w, root) for w in (ti.get("writes") or []) if isinstance(w, dict))
        return _write_has_reply(dict(ti, op=act), root)
    cmd = str(ti.get("CommandLine") or ti.get("command") or "")
    return bool(REPLY_CMD.search(cmd) or REPLY_WRITE.search(cmd))


def refuse(claude, msg):
    if claude:
        print(msg, file=sys.stderr)
        return 2
    print(json.dumps({"decision": "deny", "reason": msg}, ensure_ascii=False))
    return 0


def allow(claude):
    if not claude:
        print(json.dumps({"decision": "allow"}))
    return 0


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    claude = is_claude_payload(data)
    ev = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    tc = data.get("toolCall") or {}
    ti = data.get("tool_input") or tc.get("args") or {}
    if ev in ("PostToolUse", "PostInvocation"):
        cmd = str(ti.get("CommandLine") or ti.get("command") or "")
        resp = data.get("tool_response") or {}
        failed = isinstance(resp, dict) and (resp.get("interrupted") or resp.get("exit_code") not in (None, 0))
        if BRIEF_CMD.search(cmd) and not failed:
            # with no session id in the payload (an agy hook may send none), the stamp is `last`, the one the check
            # below reads for such a payload: so a resume there unlocks the reply (CL28, O52's review)
            try:
                os.makedirs(stamp_dir(data), exist_ok=True)
                with open(os.path.join(stamp_dir(data), sid_of(data) or "last"), "w", encoding="utf-8") as fh:
                    fh.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
            except OSError:
                pass
        if not claude:
            print("{}")
        return 0
    if ev and ev != "PreToolUse":
        return allow(claude)
    if not is_reply(data) or not is_orchestrator(data):
        return allow(claude)
    age, cap = brief_age_min(data), max_age_min(data)
    if age is not None and age <= cap:
        return allow(claude)
    return refuse(claude, (
        "NOT SENT: you answer the owner only from a fresh read of the board (the kit's guard, CL28). Last read in this "
        "session: %s. Run `%s` first: every train's open steps from their posts, questions included, and the owner's "
        "unanswered comments. Then answer from it, and send this reply again."
        % ("never" if age is None else "%d min ago (more than %d)" % (age, cap), WAY)))


if __name__ == "__main__":
    sys.exit(main())
