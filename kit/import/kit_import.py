#!/usr/bin/env python3
"""kit import: a person's state, gathered from their claude.ai chats, into the project's memory and board.

    python3 -I .kit/kit_import.py STATE.json [--out DIR] [--now ISO] [--update]

STATE.json (written by the chat side, `step0/export-chat.md`, inside a claude.ai artifact):
  {"owner_language": "French",
   "preferences": ["short steps", "ask before any real action"],
   "projects": [{"id": "bulletin", "title": "Weekly bulletin", "next": "draft the 24 Nov edition",
                 "where": ["published every Friday on the intranet"], "decisions": ["no emojis"],
                 "ids": {"intranet page": "News/Bulletin"},
                 "waiting": [{"text": "approve the new layout?", "default": "keep the old one",
                              "options": ["New layout", "Keep the old one"]}],
                 "chats": ["2026-11-14 Bulletin 46"]}]}
Writes (never over a file it did not create, unless --update):
  .story/state/<id>.md       line 1 "Next: ...", then where it stands, decisions, ids, the chats it came from
  .story/awaits.md           + "- [<id>] <text> (default: <default>)" for each waiting item not already there
  .story/preferences.md      the standing preferences (the SessionStart hook prints them)
  DIR/posts/<id>.json        one board post per project (column `info`), and one `question` post per waiting item
                             with options: give them to `ArtifactData` `batch` (the printed `writes` line).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40] or "x"


def put(path, text, update, log):
    if os.path.exists(path) and not update:
        log.append("KEPT " + os.path.relpath(path, ROOT))
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    log.append("wrote " + os.path.relpath(path, ROOT))


def state_md(p):
    out = ["# %s" % p.get("title", p["id"]), "Next: %s" % (p.get("next") or "(none given)"), "", "## Where it stands"]
    out += ["- " + w for w in p.get("where", [])] or ["- (nothing given)"]
    if p.get("decisions"):
        out += ["", "## Decisions"] + ["- " + d for d in p["decisions"]]
    if p.get("ids"):
        out += ["", "## Ids"] + ["- %s: %s" % (k, v) for k, v in p["ids"].items()]
    if p.get("chats"):
        out += ["", "## Imported from chats"] + ["- " + c for c in p["chats"]]
    return "\n".join(out) + "\n"


def run(state, out, now, update, session=""):
    log, writes = [], []
    sd = os.path.join(ROOT, ".story")
    for p in state.get("projects", []):
        p["id"] = slug(p.get("id") or p.get("title"))
        put(os.path.join(sd, "state", p["id"] + ".md"), state_md(p), update, log)
    aw = os.path.join(sd, "awaits.md")
    cur = open(aw, encoding="utf-8").read() if os.path.exists(aw) else "# What awaits the owner\n\n"
    added = 0
    for p in state.get("projects", []):
        for w in p.get("waiting", []):
            line = "- [%s] %s (default: %s)" % (p["id"], w["text"], w.get("default") or "none")
            if w["text"] not in cur:
                cur += line + "\n"
                added += 1
    if added:
        os.makedirs(sd, exist_ok=True)
        open(aw, "w", encoding="utf-8").write(cur)
        log.append("awaits.md: +%d" % added)
    if state.get("preferences"):
        put(os.path.join(sd, "preferences.md"), "# Standing preferences\n\n" + "".join("- %s\n" % x for x in state["preferences"]), update, log)
    pd = os.path.join(out, "posts")
    os.makedirs(pd, exist_ok=True)
    for p in state.get("projects", []):
        body = state_md(p).split("\n", 1)[1]
        docs = [("P-" + p["id"], {"title": p.get("title", p["id"]), "column": "info", "part": "P-" + p["id"], "priority": 2,
                                   "body": body, "created_at": now, "session_id": session})]
        for i, w in enumerate(p.get("waiting", []), 1):
            d = {"title": w["text"], "column": "question", "part": "P-" + p["id"], "priority": 1, "created_at": now,
                 "session_id": session, "body": "%s\n\nIf you say nothing: %s." % (w["text"], w.get("default") or "nothing happens")}
            if w.get("options"):
                d["options"], d["default_option"] = w["options"], w.get("default") if w.get("default") in w["options"] else w["options"][0]
            docs.append(("Q-%s-%d" % (p["id"], i), d))
        for doc_id, d in docs:
            f = os.path.join(pd, doc_id + ".json")
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
            writes.append({"op": "set", "collection": "posts", "doc_id": doc_id, "file_path": f})
    return log, writes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("state")
    ap.add_argument("--out", default="/tmp/kit-import")
    ap.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--session", default="", help="the session_id the posts name (the orchestrator's)")
    ap.add_argument("--update", action="store_true", help="rewrite state files the import wrote before")
    a = ap.parse_args(argv)
    with open(a.state, encoding="utf-8") as fh:
        state = json.load(fh)
    log, writes = run(state, a.out, a.now, a.update, a.session)
    print("\n".join(log))
    print("writes (ArtifactData batch, up to 50 per call; posts that exist need if_version):")
    print(json.dumps(writes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
