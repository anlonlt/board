#!/usr/bin/env python3
"""agy Stop (snapshot before compaction + context-threshold handover trigger):
1. Writes <memory_dir>/before-compaction.md (gitignored) with branch, last commits, and changed files.
2. Inspects the session's transcript.jsonl for latest (input_tokens + cache_read_tokens).
   When an orchestrator reaches >= kit.orchestrator.context_limit_k * 1000 (default 300k)
   or a clerk reaches >= kit.clerk.context_limit_k * 1000 (default 200k), denies Stop ONCE
   (guarded by /tmp/kit-handover-<conv_id>.flag) with exact handover / rotation instructions."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, memory_dir, project_dir  # noqa: E402


def git(root, *a):
    try:
        return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def orchestrator_session_id(root):
    """Read _orchestrator.session_id from the local SQLite board DB if present."""
    db = os.path.join(root, ".kit", "board_data", "board.db")
    if not os.path.isfile(db):
        return ""
    try:
        conn = sqlite3.connect(db, timeout=1)
        try:
            row = conn.execute(
                "SELECT data FROM docs WHERE collection='posts' AND doc_id='_orchestrator'"
            ).fetchone()
            if row and row[0]:
                doc = json.loads(row[0])
                return str(doc.get("session_id") or "")
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def inspect_transcript(path):
    """Return (latest_tokens, detected_role) from an agy transcript.jsonl file."""
    tokens = 0
    prompt_role = None
    tool_role = None
    if not path or not os.path.isfile(path):
        return 0, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if "input_tokens" in e or "cache_read_tokens" in e:
                    t = int(e.get("input_tokens") or 0) + int(e.get("cache_read_tokens") or 0)
                    if t > 0:
                        tokens = t
                kind = e.get("type") or ""
                if prompt_role is None and kind in ("USER_INPUT", "SYSTEM_MESSAGE"):
                    content = str(e.get("content") or "")
                    low = content.lower()
                    if "you are the board clerk" in low or ".kit/clerk.md" in low:
                        prompt_role = "clerk"
                    elif "you are the orchestrator" in low or "state/orchestrator.md" in low:
                        prompt_role = "orchestrator"
                if tool_role is None and e.get("tool_calls"):
                    raw_tc = json.dumps(e.get("tool_calls"))
                    if "board_cli.py comments wait" in raw_tc or "comments watch --on --watcher clerk" in raw_tc:
                        tool_role = "clerk"
                    elif "update posts _orchestrator" in raw_tc:
                        tool_role = "orchestrator"
    except OSError:
        return 0, None
    return tokens, prompt_role or tool_role


def detect_role(data, cid, tp_role, orch_sid):
    if cid and orch_sid and cid == orch_sid:
        return "orchestrator"
    role_hint = str(data.get("role") or "").lower()
    if "orchestrator" in role_hint:
        return "orchestrator"
    if "clerk" in role_hint:
        return "clerk"
    return tp_role


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    root = project_dir(data)
    d = memory_dir(data)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "before-compaction.md"), "w", encoding="utf-8") as fh:
        fh.write("# Before compaction, %s\n\nbranch: %s\n\n## last commits\n%s\n\n## changed\n%s\n" % (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), git(root, "branch", "--show-current"),
            git(root, "log", "--oneline", "-8"), git(root, "status", "--short") or "(nothing)"))

    term = str(data.get("terminationReason") or "")
    if term == "USER_CANCELED":
        print(json.dumps({"decision": "allow"}))
        return 0

    cid = str(data.get("conversationId") or data.get("conversation_id") or "")
    tp = str(data.get("transcriptPath") or data.get("transcript_path") or "")
    if not tp and cid:
        tp = os.path.expanduser(
            "~/.gemini/antigravity-cli/brain/%s/.system_generated/logs/transcript.jsonl" % cid
        )

    tokens, tp_role = inspect_transcript(tp)
    orch_sid = orchestrator_session_id(root)
    role = detect_role(data, cid, tp_role, orch_sid)
    if role not in ("orchestrator", "clerk"):
        print(json.dumps({"decision": "allow"}))
        return 0

    cfg = config(data)
    k = cfg.get("kit") or {}
    orch_limit_k = int((k.get("orchestrator") or {}).get("context_limit_k", 300))
    clerk_limit_k = int((k.get("clerk") or {}).get("context_limit_k", 200))
    limit_k = orch_limit_k if role == "orchestrator" else clerk_limit_k

    if tokens < limit_k * 1000:
        print(json.dumps({"decision": "allow"}))
        return 0

    safe_cid = re.sub(r"[^a-zA-Z0-9._-]", "_", cid or os.path.basename(tp) or "session")
    flag = os.path.join(tempfile.gettempdir(), "kit-handover-%s.flag" % safe_cid)
    if os.path.exists(flag):
        print(json.dumps({"decision": "allow"}))
        return 0
    try:
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(str(tokens))
    except OSError:
        pass

    used_k = tokens // 1000
    if role == "orchestrator":
        rel_mem = k.get("memory_dir") or (".story" if os.path.isdir(os.path.join(root, ".story")) else ".board/memory")
        orch_model = ((k.get("roles") or {}).get("orchestrator") or {}).get("model") or "inherit"
        reason = (
            "HANDOVER REQUIRED (context at %dk >= %dk): "
            "1. Write %s/state/orchestrator.md (running parts + conversation IDs, clerk conversation ID, open timers, next step), commit and push. "
            "2. Post \"Hand-over: orchestrator <N+1>...\" under _orchestrator. "
            "3. Immediately spawn your successor via invoke_subagent("
            "TypeName=\"self\", Role=\"Orchestrator <N+1>\", Model=\"%s\", Workspace=\"inherit\", "
            "Prompt=\"Your prompt is %s/state/orchestrator.md: read it, rewrite _orchestrator.session_id to your conversation ID, "
            "tell the clerk and any running parts your new ID, post your takeover notice, and continue.\")."
        ) % (used_k, limit_k, rel_mem, orch_model, rel_mem)
    else:
        target = (" (%s)" % orch_sid) if orch_sid else ""
        reason = (
            "HANDOVER REQUIRED (context at %dk >= %dk): "
            "Send send_message to the orchestrator (session_id of _orchestrator%s): "
            "\"clerk context at %dk (>= %dk): ready for replacement\". "
            "Keep watching until the orchestrator tells you to stop, then run "
            "python3 -I .kit/board_cli.py comments watch --off --watcher clerk."
        ) % (used_k, limit_k, target, used_k, limit_k)

    print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
