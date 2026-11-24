#!/usr/bin/env python3
"""Unified kit hook dispatcher: `python3 -I .kit/hook.py <Event>`.

Single entry point for both Claude Code and agy lifecycle hooks so projects do not drift across
16 duplicated hook files in `.claude/hooks/kit/` and `.agents/hooks/kit/`.

Reads stdin JSON once, locates the canonical hook scripts (`.kit/hooks/`, `.claude/hooks/kit/`,
`.agents/hooks/kit/`, or `kit/hooks/`), invokes every hook registered for `<Event>` whose tool
matcher matches the payload, and merges their JSON outputs:
  - Deny/block beats allow (first deny reason wins).
  - `additionalContext` / `injectSteps` are concatenated in order.
  - Non-zero exit codes from blocking hooks are preserved.
"""
import json
import os
import re
import subprocess
import sys

# Event -> list of (tool_matcher_regex_or_None, hook_filename)
CLAUDE_EVENTS = {
    "SessionStart": [(None, "kit_session_start.py")],
    "PreToolUse": [
        (r"^Bash$", "kit_guard.py"),
        (r"^(mcp__claude-code-remote__send_message|SendMessage)$", "kit_say_it_once.py"),
        (r"^(ArtifactData|mcp__claude-code-remote__create_session)$", "kit_orchestrator_guard.py"),
        (r"^(ArtifactData|Bash)$", "kit_brief_guard.py"),
        (r"^(Edit|Write|MultiEdit)$", "kit_memory_jit.py"),
    ],
    "PostToolUse": [
        (r"^Bash$", "kit_brief_guard.py"),
        (r"^(ArtifactData|mcp__claude-code-remote__send_message|SendMessage|Bash)$", "kit_say_it_once.py"),
        (r"^(ArtifactData|mcp__claude-code-remote__create_session)$", "kit_cards_reminder.py"),
        (r"^ArtifactData$", "kit_notify.py"),
        (r"^(TaskCreate|TaskUpdate)$", "kit_task_train.py"),
        (r"^(Bash|mcp__claude-code-remote__create_session)$", "kit_board_guard.py"),
    ],
    "UserPromptSubmit": [
        (None, "kit_say_it_once.py"),
        (None, "kit_board_typing.py"),
        (None, "kit_cards_reminder.py"),
    ],
    "Stop": [
        (None, "kit_board_guard.py"),
        (None, "kit_stop.py"),
        (None, "kit_say_it_once.py"),
        (None, "kit_usage.py"),
    ],
    "SessionEnd": [(None, "kit_usage.py")],
    "PreCompact": [(None, "kit_pre_compact.py")],
}

AGY_EVENTS = {
    "SessionStart": [(None, "kit_session_start.py")],
    "PreToolUse": [
        (r"^run_command$", "kit_guard.py"),
        (r"^send_message$", "kit_say_it_once.py"),
        (r"^run_command$", "kit_brief_guard.py"),
        (r"^(edit_file|write_file)$", "kit_memory_jit.py"),
    ],
    "PostToolUse": [
        (r"^run_command$", "kit_brief_guard.py"),
        (r"^(send_message|run_command)$", "kit_say_it_once.py"),
        (r"^run_command$", "kit_notify.py"),
    ],
    "PreInvocation": [
        (None, "kit_say_it_once.py"),
        (None, "kit_board_typing.py"),
    ],
    "PostInvocation": [(None, "kit_cards_reminder.py")],
    "Stop": [
        (None, "kit_stop.py"),
        (None, "kit_say_it_once.py"),
        (None, "kit_pre_compact.py"),
        (None, "kit_usage.py"),
    ],
}


def _find_root(data):
    for cand in (
        data.get("cwd") if isinstance(data, dict) else None,
        (data.get("workspacePaths") or [None])[0] if isinstance(data, dict) else None,
        os.environ.get("KIT_PROJECT_DIR"),
        os.environ.get("CLAUDE_PROJECT_DIR"),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.getcwd(),
    ):
        if cand and os.path.isdir(cand):
            return cand
    return os.getcwd()


def _is_claude(data):
    if isinstance(data, dict):
        if "hook_event_name" in data or "tool_input" in data or "stop_hook_active" in data or "prompt" in data:
            return True
        if "hookEventName" in data or "toolCall" in data or "workspacePaths" in data:
            return False
    return bool(os.environ.get("CLAUDE_PROJECT_DIR") and not os.environ.get("KIT_PROJECT_DIR"))


def _tool_name(data, claude):
    if not isinstance(data, dict):
        return ""
    if claude:
        return str(data.get("tool_name") or "")
    tc = data.get("toolCall")
    return str(tc.get("name") or "") if isinstance(tc, dict) else ""


def _resolve_hook(root, filename):
    for rel in (".kit/hooks", ".claude/hooks/kit", ".agents/hooks/kit", "kit/hooks"):
        p = os.path.join(root, rel, filename)
        if os.path.isfile(p):
            return p
    return None


def dispatch(event, data, root=None):
    """Run all matching hooks for `event` and return (merged_dict_or_None, exit_code)."""
    root = root or _find_root(data)
    claude = _is_claude(data)
    table = CLAUDE_EVENTS if claude else AGY_EVENTS
    specs = table.get(event) or []
    tname = _tool_name(data, claude)
    raw = json.dumps(data)

    contexts = []
    inject_steps = []
    stderr_parts = []
    deny_out = None
    block_out = None
    rc = 0

    for matcher, fname in specs:
        if matcher and not re.match(matcher, tname):
            continue
        hp = _resolve_hook(root, fname)
        if not hp:
            continue
        try:
            r = subprocess.run([sys.executable, "-I", hp], input=raw, capture_output=True, text=True, timeout=30, cwd=root)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.stderr and r.stderr.strip():
            stderr_parts.append(r.stderr.strip())
        if r.returncode != 0 and rc == 0:
            rc = r.returncode
        out_s = (r.stdout or "").strip()
        if not out_s:
            continue
        try:
            parsed = json.loads(out_s)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        # Claude Code deny/block
        hso = parsed.get("hookSpecificOutput")
        if isinstance(hso, dict):
            if hso.get("permissionDecision") == "deny" and deny_out is None:
                deny_out = parsed
            if hso.get("additionalContext"):
                contexts.append(str(hso["additionalContext"]))
        if parsed.get("decision") in ("block", "deny") and block_out is None:
            block_out = parsed
        if parsed.get("injectSteps"):
            inject_steps.extend(parsed["injectSteps"])

    err_txt = "\n\n".join(stderr_parts)
    if deny_out is not None:
        return deny_out, rc, err_txt
    if block_out is not None:
        return block_out, rc, err_txt
    if rc == 2 and err_txt:
        return None, 2, err_txt
    if claude:
        if contexts:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n\n".join(contexts)}}, rc, err_txt
        return None, rc, err_txt
    if inject_steps:
        return {"decision": "allow", "injectSteps": inject_steps}, rc, err_txt
    return None, rc, err_txt


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if argv else ""
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not event:
        event = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    if not event:
        return 0
    merged, rc, err_txt = dispatch(event, data)
    if err_txt and rc != 0:
        print(err_txt, file=sys.stderr)
    if merged is not None:
        print(json.dumps(merged))
    return rc


if __name__ == "__main__":
    sys.exit(main())
