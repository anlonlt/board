#!/usr/bin/env python3
"""kit import: a person's state, gathered from their claude.ai chats (step0/export-chat.md) or from a Claude Code
project's repository and session transcripts (step0/import-code.md), into the project's memory and board.

    python3 -I .kit/kit_import.py STATE.json [--out DIR] [--now ISO] [--update] [--no-posts]

A Claude Code state names, per project, `sources` (the files and sessions it was read from) and `source_of_truth` (the
repository's own docs that stay authoritative: the state file points to them). --no-posts: memory only (no board).

STATE.json, or the artifact's HTML saved by `Artifact` `read` (the JSON in its <pre id="kit-state">), written by the chat side, `step0/export-chat.md`, inside a claude.ai artifact):
  {"owner_language": "French", "unread": [...], "approved": [...], "never": [...],
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


def memory_dir(root=ROOT):
    try:
        md = json.load(open(os.path.join(root, ".kit", "config.json"), encoding="utf-8")).get("kit", {}).get("memory_dir")
        if md:
            return os.path.join(root, md)
    except (OSError, ValueError):
        pass
    if os.path.isdir(os.path.join(root, ".story")):
        return os.path.join(root, ".story")
    if os.path.isdir(os.path.join(root, ".board", "memory")):
        return os.path.join(root, ".board", "memory")
    return os.path.join(root, ".story")


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


def _text(x):
    """A chat export writes what it likes: a string, or an object ({title, date}, {name, value}): one line of text."""
    if isinstance(x, dict):
        vals = [str(v) for v in x.values() if v not in (None, "")]
        return " · ".join(vals)
    return str(x)


def normalise(p):
    """ids: an object, or a list of strings / {name, value} objects -> an object; lists of anything -> lists of text."""
    ids = p.get("ids") or {}
    if isinstance(ids, list):
        d = {}
        for i, x in enumerate(ids, 1):
            if isinstance(x, dict) and len(x) >= 2:
                k = x.get("name") or x.get("key") or x.get("label") or list(x.values())[0]
                rest = [str(v) for kk, v in x.items() if v != k]
                d[str(k)] = " · ".join(rest)
            else:
                d[_text(x)] = ""  # a bare name: the name is the id
        ids = d
    p["ids"] = {str(k): _text(v) for k, v in ids.items()}
    if p.get("pitfalls") and not p.get("lessons"):
        p["lessons"] = p["pitfalls"]
    try:
        p["priority"] = min(3, max(1, int(p.get("priority") or 2)))
    except (TypeError, ValueError):
        p["priority"] = 2
    ppl = p.get("people") or {}
    if isinstance(ppl, list):
        ppl = {(x.get("role") or str(i)) if isinstance(x, dict) else str(i): _text({k: v for k, v in x.items() if k != "role"}) if isinstance(x, dict) else _text(x)
               for i, x in enumerate(ppl, 1)}
    p["people"] = {str(k): _text(v) for k, v in ppl.items()}
    for f in ("where", "decisions", "chats", "sources", "source_of_truth", "resume", "technical", "accounts", "tests", "risks", "lessons", "needs_me"):
        v = p.get(f) or []
        p[f] = [_text(x) for x in (v if isinstance(v, list) else [v])]
    w = p.get("waiting") or []
    p["waiting"] = [x if isinstance(x, dict) else {"text": _text(x)} for x in (w if isinstance(w, list) else [w])]
    return p


def load_state(path):
    """A JSON file, or the artifact's HTML as `Artifact` `read` saved it: the JSON inside <pre id="kit-state">."""
    import html as _html
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    m = re.search(r'<pre[^>]*id=["\']kit-state["\'][^>]*>(.*?)</pre>', raw, re.S)
    return json.loads(_html.unescape(m.group(1)) if m else raw)


RUNS = {"claude-code": "cloud (Claude Code sessions launched by the orchestrator)",  # older exports' words
        "cloud": "cloud (Claude Code sessions launched by the orchestrator)",
        "chat": "chat (a claude.ai chat: the orchestrator posts a card, the owner pastes it)",
        "both": "cloud for code, another runner for the rest (see the project's notes)"}


def runs_in(p, runners):
    v = str(p.get("runs_in") or "")
    if v in runners:
        return "%s (%s runner, README § Runners)" % (v, runners[v].get("kind", "?"))
    return RUNS.get(v, "unknown: check before launching anything")


def workflow_md(w):
    out = ["# How the work gets done (imported from the owner's chats)", ""]
    rs = w.get("runners") or []
    if rs:
        out += ["## Runners (README § Runners)"]
        for r in rs:
            r = r if isinstance(r, dict) else {"name": _text(r)}
            out.append("- **%s** (%s): %s" % (r.get("name", "?"), r.get("kind", "?"), "; ".join(
                _text(r[k]) for k in ("reaches", "tools", "accounts", "setup") if r.get(k))))
        out.append("")
    for key, head in (("tools", "Tools and methods"), ("accounts", "Accounts and browser profiles"),
                      ("cycle", "The cycle of a change"), ("limits", "Limits")):
        v = w.get(key) or []
        v = v if isinstance(v, list) else [v]
        if v:
            out += ["## " + head] + ["- " + _text(x) for x in v] + [""]
    return "\n".join(out)


def state_md(p):
    out = ["# %s" % p.get("title", p["id"]), "Next: %s" % (p.get("next") or "(none given)"),
           "Priority: %s%s" % (p["priority"], (" (%s)" % p["why"]) if p.get("why") else ""),
           "Runs in: %s%s" % (runs_in(p, p.get("_runners") or {}),
                              (" (%s)" % _text(p["runs_in_why"])) if p.get("runs_in_why") else ""), "", "## Where it stands"]
    out += ["- " + w for w in p.get("where", [])] or ["- (nothing given)"]
    if p.get("decisions"):
        out += ["", "## Decisions"] + ["- " + d for d in p["decisions"]]
    if p.get("ids"):
        out += ["", "## Ids"] + [("- %s: %s" % (k, v)) if v else "- " + k for k, v in p["ids"].items()]
    for key, head in (("resume", "How to resume"), ("technical", "Technical details"), ("accounts", "Accounts"),
                      ("tests", "Tests"), ("risks", "Known risks"), ("lessons", "Lessons (traps found)"),
                      ("needs_me", "Still needs the owner")):
        if p.get(key):
            out += ["", "## " + head] + ["- " + x for x in p[key]]
    if p.get("people"):
        out += ["", "## People (role: who)"] + ["- %s: %s" % (k, v) for k, v in p["people"].items()]
    if p.get("source_of_truth"):
        out += ["", "## Source of truth (read it; this file only points)"] + ["- " + c for c in p["source_of_truth"]]
    if p.get("sources"):
        out += ["", "## Imported from the repository and sessions"] + ["- " + c for c in p["sources"]]
    if p.get("chats"):
        out += ["", "## Imported from chats"] + ["- " + c for c in p["chats"]]
    return "\n".join(out) + "\n"


def run(state, out, now, update, session="", posts=True):
    log, writes = [], []
    sd = memory_dir(ROOT)
    wr = (state.get("workflow") or {}).get("runners") if isinstance(state.get("workflow"), dict) else None
    runners = {r["name"]: r for r in (wr or []) if isinstance(r, dict) and r.get("name")}
    for p in state.get("projects", []):
        normalise(p)
        p["_runners"] = runners
    state["projects"] = sorted(state.get("projects", []), key=lambda p: p["priority"])  # the most urgent first
    for p in state.get("projects", []):
        normalise(p)
        p["id"] = slug(p.get("id") or p.get("title"))
        put(os.path.join(sd, "state", p["id"] + ".md"), state_md(p), update, log)
    aw = os.path.join(sd, "awaits.md")
    cur = open(aw, encoding="utf-8").read() if os.path.exists(aw) else "# What awaits the owner\n\n"
    added = 0
    for p in state.get("projects", []):
        for w in p.get("waiting", []):
            line = "- [%s] P%s %s (default: %s)" % (p["id"], w.get("priority") or p["priority"], w["text"], w.get("default") or "none")
            if w["text"] not in cur:
                cur += line + "\n"
                added += 1
    if added:
        os.makedirs(sd, exist_ok=True)
        open(aw, "w", encoding="utf-8").write(cur)
        log.append("awaits.md: +%d" % added)
    lists = [(k, [_text(x) for x in (state.get(k) or [])]) for k in ("preferences", "approved", "never", "unread")]
    if any(v for _, v in lists):
        heads = {"preferences": "", "approved": "Approved once (do not ask again): ", "never": "Never without the owner: ",
                 "unread": "Not read by the import: "}
        text = "# Standing preferences\n\n" + "".join("- %s%s\n" % (heads[k], x) for k, v in lists for x in v)
        put(os.path.join(sd, "preferences.md"), text, update, log)
    w = state.get("workflow")
    if isinstance(w, dict) and any(w.values()):
        put(os.path.join(sd, "workflow.md"), workflow_md(w), update, log)
    off = ["%s -> %s" % (p.get("title") or p["id"], p.get("runs_in")) for p in state.get("projects", [])
           if p.get("runs_in") and p.get("runs_in") not in ("cloud", "claude-code")]
    if off:
        log.append("not in a cloud session (their runner must be set up, README § Runners): " + ", ".join(off))
    pd = os.path.join(out, "posts")
    if posts:
        os.makedirs(pd, exist_ok=True)
    for p in (state.get("projects", []) if posts else []):
        body = state_md(p).split("\n", 1)[1]
        docs = [("P-" + p["id"], {"title": p.get("title", p["id"]), "column": "info", "part": "P-" + p["id"], "priority": p["priority"],
                                   "body": body, "created_at": now, "session_id": session})]
        for i, w in enumerate(p.get("waiting", []), 1):
            try:
                wp = min(3, max(1, int(w.get("priority") or p["priority"])))
            except (TypeError, ValueError):
                wp = p["priority"]
            d = {"title": w["text"], "column": "question", "part": "P-" + p["id"], "priority": wp, "created_at": now,
                 "session_id": session, "body": w["text"] + (("\n\n→ %s" % str(w["default"]).rstrip(". ")) if w.get("default") else "")}
            if w.get("options"):
                d["options"] = [_text(o) for o in w["options"]]
                if w.get("default") in d["options"]:
                    d["default_option"] = w["default"]  # only a real option is marked; never the first one by guess
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
    ap.add_argument("--no-posts", action="store_true", help="memory only: no board posts (an install without the board)")
    a = ap.parse_args(argv)
    state = load_state(a.state)
    log, writes = run(state, a.out, a.now, a.update, a.session, posts=not a.no_posts)
    print("\n".join(log))
    if a.no_posts:
        print("no posts (--no-posts): the memory is written; commit %s and push" % os.path.relpath(memory_dir(ROOT), ROOT))
        return 0
    print("writes (ArtifactData batch, up to 50 per call; posts that exist need if_version):")
    print(json.dumps(writes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
