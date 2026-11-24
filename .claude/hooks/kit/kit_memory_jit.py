#!/usr/bin/env python3
"""Just-in-time (JIT) memory hook: PreToolUse on Edit/Write/MultiEdit (Claude Code) and edit_file/write_file (agy).

Instead of dumping every `files:<glob>` rule into Turn 1 of `kit.py resume`, this hook injects only
the 1-3 active ledger entries whose `files:<glob>` scope matches the file about to be edited — once
per (session, entry_id), right at the moment the agent needs them.
"""
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kit_config import is_claude_payload, memory_dir, project_dir  # noqa: E402


def _load_ledger_module(root):
    for d in (os.path.join(root, ".kit"), os.path.join(root, "kit")):
        p = os.path.join(d, "ledger.py")
        if os.path.isfile(p):
            if d not in sys.path:
                sys.path.insert(0, d)
            import ledger  # noqa: E402
            return ledger
    return None


def _target_paths(data):
    """Extract target file path(s) from Claude Code or agy PreToolUse payload."""
    paths = []
    ti = data.get("tool_input")
    if isinstance(ti, dict):
        for k in ("file_path", "path", "TargetFile", "Path"):
            v = ti.get(k)
            if isinstance(v, str) and v.strip():
                paths.append(v.strip())
    tc = data.get("toolCall")
    if isinstance(tc, dict):
        args = tc.get("args") or {}
        if isinstance(args, dict):
            for k in ("Path", "TargetFile", "file_path", "path"):
                v = args.get(k)
                if isinstance(v, str) and v.strip():
                    paths.append(v.strip())
    return paths


def _seen_file(root, data):
    sid = (data.get("session_id") or data.get("conversationId") or data.get("conversation_id")
           or os.environ.get("CLAUDE_CODE_SESSION_ID") or "default")
    h = hashlib.sha1((str(root) + "|" + str(sid)).encode("utf-8")).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), "kit-memory-jit-%s.json" % h)


def _read_seen(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def _write_seen(path, ids):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sorted(ids), fh)
    except OSError:
        pass


def handle(data):
    root = project_dir(data)
    raw_paths = _target_paths(data)
    if not raw_paths:
        return None
    rel_paths = []
    for p in raw_paths:
        rel_paths.append(os.path.relpath(p, root) if os.path.isabs(p) else p)
    ledger = _load_ledger_module(root)
    if ledger is None:
        return None
    mdir = memory_dir(data)
    hits = ledger.for_files(mdir, rel_paths, kit_root=root)
    if not hits:
        return None
    sf = _seen_file(root, data)
    seen = _read_seen(sf)
    fresh = [h for h in hits if h["id"] not in seen]
    if not fresh:
        return None
    seen.update(h["id"] for h in fresh)
    _write_seen(sf, seen)
    lines = ["Memory JIT for %s:" % ", ".join(rel_paths)]
    for h in fresh:
        q = (' — "%s"' % h["quote"][:120]) if h.get("quote") else ""
        lines.append("- **%s** (%s): %s%s" % (h["id"], h["kind"], h["summary"], q))
    msg = "\n".join(lines)
    if is_claude_payload(data):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}
    return {"decision": "allow", "injectSteps": [{"systemMessage": {"systemMessage": msg}}]}


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    out = handle(data)
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
