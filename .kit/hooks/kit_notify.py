#!/usr/bin/env python3
"""Claude Code hook (PostToolUse ArtifactData): phone push notifications (ntfy or any webhook) for an Artifact board.

The local board server pushes to `kit.notify_url` when a post lands in To do / Waiting on you, and when a session
answers under a post (board_server.py send_push). An Artifact board has no server, so the session that writes the
post or the comment pushes it, with the same rule:
  - posts set/update with column "todo" or "question" (not the `_` cards),
  - comments from a session (from not in owner/viewer/clerk).
Privacy (an ntfy.sh topic is public to whoever knows its name, and ntfy keeps messages for a while): by default the
push says only WHAT happened and links to the board, never the post's text; `kit.notify_detail: true` adds the
title and the first 240 characters, as the local server does. Off when `kit.notify_url` is empty. Never blocks."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config  # noqa: E402

TEXT = {"en": {"todo": "To do", "question": "Waiting on you", "post": "A post needs you.", "replied": "%s replied",
               "answered": "A session answered under a post."},
        "fr": {"todo": "À faire", "question": "En attente de toi", "post": "Une publication t'attend.", "replied": "%s a répondu",
               "answered": "Une session a répondu sous une publication."}}
COLS = ("todo", "question")


def writes(ti):
    items = ti.get("writes") if ti.get("action") == "batch" else [ti]
    out = []
    for w in items or []:
        if not isinstance(w, dict) or w.get("op", ti.get("action")) not in ("set", "update"):
            continue
        data = w.get("data")
        if data is None and w.get("file_path"):
            try:
                data = json.load(open(w["file_path"], encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        out.append((w.get("collection"), str(w.get("doc_id") or ""), data if isinstance(data, dict) else {}))
    return out


def pushes(ti, k):
    detail, board = bool(k.get("notify_detail")), k.get("board_url") or ""
    project = k.get("project") or "board"
    t = TEXT["fr" if str(k.get("talk") or "").lower().startswith(("fr", "fran")) else "en"]
    out = []
    for coll, doc_id, d in writes(ti):
        if coll == "posts" and d.get("column") in COLS and not doc_id.startswith("_"):
            title = "%s: %s" % (project, t[d["column"]])
            body = (str(d.get("title") or doc_id) + "\n" + str(d.get("body") or "")[:240]) if detail else t["post"]
            out.append((title, body, board + "#/post/" + doc_id))
        elif coll == "comments" and str(d.get("from") or "") not in ("", "owner", "viewer", "clerk"):
            title = "%s: %s" % (project, t["replied"] % d.get("from"))
            body = str(d.get("text") or "")[:240] if detail else t["answered"]
            out.append((title, body, board + ("#/post/" + str(d.get("post_id")) if d.get("post_id") else "")))
    return out


def send(url, title, body, click, again=True):
    """One retry after 3 s on 429: anonymous ntfy.sh limits by IP, and cloud sessions share their egress IP."""
    try:
        _send(url, title, body, click)
    except urllib.error.HTTPError as e:
        if e.code != 429 or not again:
            raise
        time.sleep(3)
        _send(url, title, body, click)


def _send(url, title, body, click):
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title[:120].encode("utf-8").decode("latin-1", "ignore"),
                                          "Content-Type": "text/plain; charset=utf-8", "Click": click, "Tags": "clipboard"})
    with urllib.request.urlopen(req, timeout=5):
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    cfg = config(data)
    k = dict(cfg.get("kit", {}))
    k.setdefault("board_url", cfg.get("board_url") or "")
    k.setdefault("project", cfg.get("project") or "")
    k.setdefault("talk", (cfg.get("language") or {}).get("talk") or "")
    url = k.get("notify_url") or ""
    if not url or data.get("tool_name") != "ArtifactData":
        print("{}")
        return 0
    sent = 0
    for title, body, click in pushes(data.get("tool_input") or {}, k)[:5]:
        try:
            send(url, title, body, click)
            sent += 1
        except Exception:  # never fail the tool call for a push
            pass
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
