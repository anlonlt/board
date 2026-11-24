#!/usr/bin/env python3
"""Claude Code hook (PostToolUse ArtifactData): phone notifications for an Artifact board.

The local board's server notifies by itself; an Artifact has none, so the session that writes a post or an answer there
does it, by the same rules and to the same topic (.kit/notify.py). A post's lane before the write is what the last sync
saw (.kit/run/artifact/base). Off while no topic is set. Never blocks; a notification that did not leave is told to the
session (a web environment whose network does not allow the ntfy host)."""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402


def notify_module(root):
    here = os.path.dirname(os.path.abspath(__file__))
    up3 = os.path.dirname(os.path.dirname(os.path.dirname(here)))           # .claude/hooks/kit -> the project
    for p in (os.path.join(root, ".kit", "notify.py"), os.path.join(os.path.dirname(here), "notify.py"),
              os.path.join(up3, ".kit", "notify.py")):
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("kit_notify_rules", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def writes(ti):
    items = ti.get("writes") if ti.get("action") == "batch" else [ti]
    out = []
    for w in items or []:
        if not isinstance(w, dict) or w.get("op", ti.get("action")) not in ("set", "update"):
            continue
        data = w.get("data")
        if data is None and w.get("file_path"):
            try:
                with open(w["file_path"], encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
        out.append((str(w.get("collection") or ""), str(w.get("doc_id") or ""), data if isinstance(data, dict) else {}))
    return out


def lane_before(n, root, project, coll, doc_id):
    """The lane the last sync saw for this post, or None. This project's own board is saved without its repos/<id>/."""
    parts = [x for x in coll.split("/") if x and x != ".."]
    if len(parts) == 3 and parts[1] in (n.slug(project), n.slug(os.path.basename(root))):
        parts = parts[2:]
    p = os.path.join(root, ".kit", "run", "artifact", "base", *parts, os.path.basename(doc_id) + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("column")
    except (OSError, ValueError, AttributeError):
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    out = {}
    if isinstance(data, dict) and data.get("tool_name") == "ArtifactData":
        try:
            root = project_dir(data)
            n = notify_module(root)
            cfg = n.settings(root) if n else {}
            why, sent = "", 0
            for coll, doc_id, d in writes(data.get("tool_input") or {}) if cfg.get("url") else []:
                if sent >= 5:
                    break
                ok, w = n.announce(root, cfg, coll, doc_id, d, prev=lane_before(n, root, cfg["project"], coll, doc_id))
                sent += ok
                why = why or w
                if w:
                    break                       # the same host again would cost its timeout each time
            if why:
                out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                       "The phone notification did not leave (%s): the owner was not told of this write." % why}}
        except Exception:  # never fail the tool call for a notification
            out = {}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
