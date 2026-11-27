#!/usr/bin/env python3
"""Stop / PreCompact (snapshot before compaction + context-threshold handover trigger), agy and Claude Code CLI:
1. Writes <memory_dir>/before-compaction.md (gitignored) with branch, last commits, and changed files.
2. Inspects the session's transcript.jsonl for latest (input_tokens + cache_read_tokens).
   When an orchestrator reaches >= kit.orchestrator.context_limit_k * 1000 (default 380k)
   or a clerk reaches >= kit.clerk.context_limit_k * 1000 (default 80k), denies Stop ONCE
   (guarded by /tmp/kit-handover-<conv_id>.flag) with exact handover / rotation instructions.
   Claude Code CLI (Stop): the same limits, read from the transcript's `usage`; the session then saves its hand-over
   and runs `.kit/handover.py`, which RESETS the context (a fresh session under the same name, `up` reopens the
   orchestrator in the same terminal) instead of letting Claude Code compact it."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, memory_dir, project_dir  # noqa: E402


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


def _claude_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    return ""


def inspect_claude_transcript(path):
    """Return (latest context tokens, role) from a Claude Code transcript (.jsonl)."""
    tokens, role = 0, None
    if not path or not os.path.isfile(path):
        return 0, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                m = e.get("message") if isinstance(e, dict) else None
                if not isinstance(m, dict):
                    continue
                u = m.get("usage")
                if e.get("type") == "assistant" and isinstance(u, dict) and not e.get("isSidechain"):
                    t = sum(int(u.get(f) or 0) for f in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                    if t > 0:
                        tokens = t
                if role is None and e.get("type") == "user" and not e.get("isMeta"):
                    low = _claude_text(m.get("content")).lower()
                    if not low or low.startswith("<"):
                        continue
                    if re.search(r"\.kit/(?:claude-cli/)?clerk\.md", low) or "you are the board clerk" in low:
                        role = "clerk"
                    elif re.search(r"start as the orchestrator|\.kit/(?:claude-cli/|claude-web/)?(?:orchestrator|takeover)\.md", low):
                        role = "orchestrator"
                    else:
                        role = "other"
    except OSError:
        return 0, None
    return tokens, (None if role == "other" else role)


def _usage_module(root):
    """The kit's usage.py (.kit/usage.py of the project, else the kit repo's scripts/usage.py), or None."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(root, ".kit", "usage.py"), os.path.join(here, "..", "usage.py"),
              os.path.join(here, "..", "..", "scripts", "usage.py")):
        if os.path.isfile(p):
            try:
                spec = importlib.util.spec_from_file_location("kit_usage_role", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "session_role"):
                    return mod
            except Exception:
                continue
    return None


def session_role(data, root, fallback=None):
    """M12: the one role function (usage.py `session_role`, the one the usage row uses): the `_orchestrator` post's
    session_id, a hand-over line, a takeover mid-session (`kit.py resume --role orchestrator`, launch|land, a write of
    `_orchestrator`), else the first prompt, else KIT_ROLE. Without usage.py: KIT_ROLE, then `fallback` (first message)."""
    tp = str(data.get("transcript_path") or data.get("transcriptPath") or "")
    mod = _usage_module(root)
    if mod and tp and os.path.isfile(tp):
        try:
            sid = data.get("session_id") or data.get("conversation_id") or data.get("conversationId")
            role = mod.session_role(tp, root, sid)
            if role and role != "other":
                return role
        except Exception:
            pass
    return os.environ.get("KIT_ROLE") or fallback


def project_rules(k, role):
    """CL61: a project's own hand-over rules (kit.<role>.handover_rules, what install.py --update folded out of a fork
    of this hook), said after the kit's steps; the kit's hook stays the one that decides."""
    rules = [str(r).strip() for r in ((k.get(role) or {}).get("handover_rules") or []) if str(r).strip()]
    return (" This project's rules: " + " ".join(r if r[-1] in ".!?" else r + "." for r in rules)) if rules else ""


def claude_handover(data, root):
    """Claude Code Stop: at the role's context limit, block the stop ONCE with the hand-over steps."""
    if data.get("stop_hook_active"):
        return None
    tokens, first_role = inspect_claude_transcript(str(data.get("transcript_path") or ""))
    k = (config(data).get("kit") or {})
    limits = {r: int((k.get(r) or {}).get("context_limit_k", d)) for r, d in (("orchestrator", 380), ("clerk", 80))}
    if tokens < min(limits.values()) * 1000:
        return None                                    # below every limit: the role is not read (one cheap pass)
    role = session_role(data, root, first_role)
    if role not in ("orchestrator", "clerk"):
        return None
    limit_k = limits[role]
    if tokens < limit_k * 1000:
        return None
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", str(data.get("session_id") or "session"))
    flag = os.path.join(tempfile.gettempdir(), "kit-handover-%s.flag" % sid)
    if os.path.exists(flag):
        return None
    try:
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(str(tokens))
    except OSError:
        pass
    used_k = tokens // 1000
    if role == "orchestrator":
        rel_mem = k.get("memory_dir") or (".story" if os.path.isdir(os.path.join(root, ".story")) else ".board/memory")
        reason = (
            "HANDOVER REQUIRED (context at %dk >= %dk): a context RESET, not a compaction; your successor resumes from "
            "the board alone (`kit.py resume`). 1. Make the trains current: every step you plan is a task with metadata "
            "{train, part} (it is its train's post), every change made with TaskUpdate; a decision or plan that exists "
            "only in this conversation goes on the board now (a step, or a line under its post). 2. Only what the board "
            "cannot hold goes in %s/state/orchestrator.md (open timers); commit and push. 3. Post one line under "
            "_orchestrator: \"Hand-over: context reset at %dk; the next orchestrator takes over in a moment.\" 4. As your "
            "LAST action run `python3 -I .kit/handover.py --role orchestrator`: %s"
        ) % (used_k, limit_k, rel_mem, used_k, "it prints the `create_session` call that starts your successor: make it, then "
             "end your turn." if os.environ.get("CLAUDE_CODE_REMOTE") else "it ends this session and `up` opens your successor, "
             "same name, in this terminal.")
    else:
        reason = (
            "HANDOVER REQUIRED (context at %dk >= %dk): do NOT re-arm the listener. Run `python3 -I .kit/handover.py "
            "--role clerk` (a fresh clerk, same name, starts and arms its own listener; this session and its listener "
            "stop), then end your turn with \"·\"."
        ) % (used_k, limit_k)
    return {"decision": "block", "reason": reason + project_rules(k, role)}


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
    claude = is_claude_payload(data)
    if claude and data.get("hook_event_name") == "Stop":
        out = claude_handover(data, root)
        if out:
            print(json.dumps(out, ensure_ascii=False))
        return 0
    d = memory_dir(data)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "before-compaction.md"), "w", encoding="utf-8") as fh:
        fh.write("# Before compaction, %s\n\nbranch: %s\n\n## last commits\n%s\n\n## changed\n%s\n" % (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), git(root, "branch", "--show-current"),
            git(root, "log", "--oneline", "-8"), git(root, "status", "--short") or "(nothing)"))

    if claude:
        return 0

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
    if role not in ("orchestrator", "clerk") and tokens >= 80000:
        role = session_role(dict(data, transcript_path=tp), root, role)   # M12: a takeover mid-session counts on agy too
    if role not in ("orchestrator", "clerk"):
        print(json.dumps({"decision": "allow"}))
        return 0

    cfg = config(data)
    k = cfg.get("kit") or {}
    orch_limit_k = int((k.get("orchestrator") or {}).get("context_limit_k", 380))
    clerk_limit_k = int((k.get("clerk") or {}).get("context_limit_k", 80))
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
            "1. Make the trains current (`kit.py step`), write %s/state/orchestrator.md (open timers, and running parts + conversation IDs if any), commit and push. "
            "2. Post \"Hand-over: orchestrator <N+1>...\" under _orchestrator. "
            "3. If no part subagents are actively running, do a clean context RESET: run `python3 -I .kit/handover.py --role orchestrator --cli agy` as your last action (`up` opens a fresh orchestrator in this terminal that resumes via `kit.py resume`). "
            "If part subagents are mid-flight and must stay alive, spawn your successor via invoke_subagent("
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

    print(json.dumps({"decision": "deny", "reason": reason + project_rules(k, role)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
