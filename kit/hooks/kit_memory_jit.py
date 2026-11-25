#!/usr/bin/env python3
"""Just-in-time (JIT) memory hook: PreToolUse on Edit/Write/MultiEdit (Claude Code) and edit_file/write_file (agy).

Instead of dumping every `files:<glob>` rule into Turn 1 of `kit.py resume`, this hook injects only
the 1-3 active ledger entries whose `files:<glob>` scope matches the file about to be edited, and the lessons
(`lessons.md`, the memory's `lessons/*.md`) whose `files:` do — once per (session, entry_id), right at the moment the agent needs them.
"""
import fnmatch
import glob
import hashlib
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kit_config import config, is_claude_payload, memory_dir, project_dir  # noqa: E402


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
           or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if not sid:
        return None          # no id: nothing to remember it by (a shared one would silence the rule for everyone)
    h = hashlib.sha1((str(root) + "|" + str(sid)).encode("utf-8")).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), "kit-memory-jit-%s.json" % h)


def _memory_dir(root, data):
    """The memory `kit.py resume` reads: kit.memory_dir, else .board/memory, else .story (the kit's order)."""
    if not config(data).get("kit", {}).get("memory_dir") and os.path.isdir(os.path.join(root, ".board", "memory")):
        return os.path.join(root, ".board", "memory")
    return memory_dir(data)


def _read_seen(path):
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def _write_seen(path, ids):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sorted(ids), fh)
    except OSError:
        pass


def lessons(root, mdir, rel_paths, role):
    """The lessons tied to files that match one of these (the same files `kit.py lessons` reads; a lesson with no `files:` is
    resume's, and `where: attached` is judged there too: an edited kit file says the rest)."""
    kit = [p for p in (os.path.join(root, "kit", "lessons.md"), os.path.join(root, ".kit", "lessons.md")) if os.path.isfile(p)][:1]
    out = []
    for p in kit + sorted(glob.glob(os.path.join(mdir, "lessons", "*.md"))):
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for m in re.finditer(r"(?ms)^## (\S+?):\s*([^\n]+)\n(.*?)(?=^## |\Z)", text):
            head = dict((k.lower(), v) for k, v in re.findall(r"(?mi)^(roles|files|where):[ \t]*(.*)$", m.group(3)))
            globs = [x for x in re.split(r"[,\s]+", head.get("files", "")) if x]
            roles = [x.lower() for x in re.split(r"[,\s]+", head.get("roles", "")) if x]
            if not globs or not (role in roles or "all" in roles):
                continue
            if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in globs for f in rel_paths):
                body = re.sub(r"(?mi)^(roles|files|from|where):.*$", "", m.group(3))
                out.append({"id": m.group(1), "kind": "lesson", "summary": m.group(2).strip() + ". " + " ".join(body.split())})
    return out


def handle(data):
    role = os.environ.get("KIT_ROLE", "").strip().lower() or None
    if role == "clerk":
        return None
    root = project_dir(data)
    raw_paths = _target_paths(data)
    if not raw_paths:
        return None
    rel_paths = []
    for p in raw_paths:
        rel_paths.append(os.path.relpath(p, root) if os.path.isabs(p) else p)
    ledger = _load_ledger_module(root)
    mdir = _memory_dir(root, data)
    hits = lessons(root, mdir, rel_paths, role or "part")
    if ledger is not None:
        hits += ledger.for_files(mdir, rel_paths, kit_root=root, role=role)
        have = {h["id"] for h in hits}
        for _, rows in ledger.open_fields(mdir, role or "part", files=rel_paths, kit_root=root):      # the fields these files open
            hits += [r for r in rows if r["id"] not in have and not have.add(r["id"])]
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
        q = (' — "%s"' % " ".join(h["quote"].split())[:120]) if h.get("quote") else ""
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
