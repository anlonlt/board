#!/usr/bin/env python3
"""Claude Code hook: remind the ORCHESTRATOR to rebuild the "Where we are" card and the trains (the kit-cards agent)
at the moments they go stale, so the card no longer depends on its memory of .kit/orchestrator.md (the owner,
2026-11-22: the card was a day old because no Done had called kit-cards).

Fires (PostToolUse / UserPromptSubmit), only in the orchestrator's session (it writes its id to
/tmp/kit-orchestrator-session at start, .kit/orchestrator.md step 1; or KIT_ROLE=orchestrator):
  - a part reports back: the prompt holds "Done. Check post <id>";
  - a post is written with column "done" or priority 1 (ArtifactData set/update/batch on posts);
  - a train with several steps is planned: one ArtifactData batch writes 2+ posts naming a train, or a post with
    `parent` (a step that continues another);
  - a part is launched (create_session).
Silent otherwise. At most once per 2 minutes, so a burst of writes gives one reminder."""
import json
import os
import re
import sys
import tempfile
import time

MARK = os.path.join(tempfile.gettempdir(), "kit-orchestrator-session")
LAST = os.path.join(tempfile.gettempdir(), "kit-cards-reminder-last")
QUIET_S = 120
REMINDER = ("CARDS: %s. Rebuild \"Where we are\" and the trains now: Agent tool, subagent_type \"kit-cards\", prompt "
            "\"rebuild the cards\" (.kit/orchestrator.md step 2); never list the posts yourself.")


def is_orchestrator(data=None):
    role = os.environ.get("KIT_ROLE") or ""
    if role:
        return role == "orchestrator"
    try:
        mine = open(MARK, encoding="utf-8").read().strip()
    except OSError:
        mine = ""
    cur = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if mine and (not cur or mine == cur or mine.endswith(cur) or cur.endswith(mine)):
        return True
    # CL30: the orchestrator guard's own test (HARMONIZE_ROLE, branch prefix, the first prompt): an orchestrator that
    # never wrote the mark (every web one) was never reminded
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import kit_orchestrator_guard
        return bool(data) and kit_orchestrator_guard.is_orchestrator(data)
    except Exception:  # noqa: BLE001 - a missing guard never breaks the reminder
        return False


def post_writes(tool_input):
    """[(doc_id, data)] of the posts an ArtifactData call writes (data read from file_path when given)."""
    ti = tool_input or {}
    items = ti.get("writes") if ti.get("action") == "batch" else [ti]
    out = []
    for w in items or []:
        if not isinstance(w, dict) or w.get("collection") != "posts" or w.get("op", ti.get("action")) not in ("set", "update"):
            continue
        data = w.get("data")
        if data is None and w.get("file_path"):
            try:
                data = json.load(open(w["file_path"], encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        if str(w.get("doc_id") or "").startswith("_"):
            continue
        out.append((w.get("doc_id"), data if isinstance(data, dict) else {}))
    return out


def why(event, data):
    if event == "UserPromptSubmit":
        m = re.search(r"Done\. Check post (\S+?)[.\s]", str(data.get("prompt") or "") + " ")
        return "part %s reported Done" % m.group(1) if m else ""
    name = str(data.get("tool_name") or "")
    if name.endswith("create_session"):
        return "a part was launched"
    if name != "ArtifactData":
        return ""
    ws = post_writes(data.get("tool_input"))
    done = [d for d, x in ws if x.get("column") == "done"]
    p1 = [d for d, x in ws if str(x.get("priority")) == "1"]
    trains = [d for d, x in ws if x.get("train")]
    cont = [d for d, x in ws if x.get("parent")]
    if done:
        return "post %s moved to Done" % ", ".join(done)
    if len(trains) >= 2 or cont:
        return "a train with several steps was planned (%s)" % ", ".join(trains or cont)
    if p1:
        return "priority-1 post %s written" % ", ".join(p1)
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    event = str(data.get("hook_event_name") or "")
    reason = why(event, data) if is_orchestrator(data) else ""
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or ""))
    last = "%s-%s" % (LAST, sid) if sid else LAST          # CL30: one quiet time per session
    if not reason:
        print("{}")
        return 0
    try:
        if time.time() - os.path.getmtime(last) < QUIET_S:
            print("{}")
            return 0
    except OSError:
        pass
    try:
        open(last, "w").write(reason)
    except OSError:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": REMINDER % reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
