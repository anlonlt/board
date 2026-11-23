#!/usr/bin/env python3
"""The board clerk's watch on the owner's unanswered comments (the kit; origin: anlonlt/harmonize scripts/clerk-pending.py).

Names and the wait come from <project>/.kit/config.json (fields.owner, fields.clerk, kit.clerk.wait_minutes).

His rule: every comment of his gets an answer. The clerk keeps one line per comment with words it passed on, in
`<dir>/pending.txt`:  `<his doc_id> <his thread_id> <his created_at> <post id> <part> [told=<ISO>]` (his time is
re-read from his own saved row `<doc_id>.json` when it is there: what the clerk typed may be cut to the minute)
and, before running this, saves each pending post's comments with `ArtifactData` `query` `comments`
`where [["post_id","==","<post id>"]]`, `out_dir: "<dir>/t/<his doc_id>"`.

    python3 -I scripts/clerk-pending.py [--dir /tmp/clerk] [--now ISO]

Prints one action per line, and rewrites pending.txt to match:
  ANSWERED <doc_id>                                   a session answered: the line is dropped.
  TELL <doc_id> <post> <thread> <his hh:mm> <part>    5 min and no answer: tell the orchestrator (one line); marked told.
  WRITE <doc_id> <post> <told hh:mm>                  5 min after telling the orchestrator, still none: the clerk writes
                                                      ONE comment under his: "Not answered yet: the orchestrator has
                                                      been silent since <told hh:mm>." and sends the orchestrator a
                                                      `priority: "now"` message; the line is dropped (once per comment).
  NOSAVE <doc_id>                                     no saved comments for it: query first (nothing changes).
  NEXT <minutes>                                      lines remain: `send_later` that many minutes, "clerk: check pending".
An answer: a comment not his and not the clerk's (nor a "Passed to"/"Not answered yet" line), not a Like, with
`reply_to` his doc_id or his thread id, or later than his on the same post. (M1e's first form queried by thread id,
which no session's answer carries: 0 of 484 session comments on 2026-11-16. It read every comment as unanswered.)
"""
import argparse, glob, json, math, os, re, sys
from datetime import datetime, timedelta, timezone

WAIT = timedelta(minutes=5)
CLERK = re.compile(r"^\s*(passed to|not answered yet)", re.I)
OWNER, CLERK_NAME = "owner", "clerk"
_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
if os.path.exists(_cfg):
    with open(_cfg, encoding="utf-8") as _fh:
        _c = json.load(_fh)
    OWNER = _c.get("fields", {}).get("owner", OWNER)
    CLERK_NAME = _c.get("fields", {}).get("clerk", CLERK_NAME)
    WAIT = timedelta(minutes=_c.get("kit", {}).get("clerk", {}).get("wait_minutes", 5))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")[:19]).replace(tzinfo=timezone.utc)


def answered(doc_id, thread, at, comments):
    t0 = ts(at)
    for c in comments:
        if c.get("from") in (OWNER, "viewer", CLERK_NAME) or c.get("like") or CLERK.match(c.get("text") or ""):
            continue
        if c.get("reply_to") in (doc_id, thread):
            return True
        # A review (an automatic one, under the same post) is not an answer to him unless it replies to his comment.
        if c.get("kind") != "review" and c.get("created_at") and ts(c["created_at"]) > t0:
            return True
    return False


def run(d, now):
    if isinstance(now, str):  # an ISO string from a caller is read as UTC
        now = ts(now)
    path = os.path.join(d, "pending.txt")
    lines = [l.split() for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
    keep, out = [], []
    for f in lines:
        if len(f) < 5:
            continue
        doc, thread, at, post, part = f[:5]
        told = next((x[5:] for x in f[5:] if x.startswith("told=")), None)
        files = glob.glob(os.path.join(d, "t", doc, "comments", "*.json"))
        if not files:
            out.append(f"NOSAVE {doc}"); keep.append(f); continue
        cs = [json.load(open(x, encoding="utf-8")) for x in files]
        # his time from his saved row, not from what the clerk typed (review CR-M1f-the-watch: a line typed to the
        # minute let a session comment from before his comment count as its answer)
        mine = next((c for c, x in zip(cs, files) if os.path.basename(x) == doc + ".json"), None)
        if mine and mine.get("created_at"):
            at = mine["created_at"]
        if answered(doc, thread, at, cs):
            out.append(f"ANSWERED {doc}"); continue
        if told is None:
            if now - ts(at) >= WAIT:
                stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                out.append(f"TELL {doc} {post} {thread} {ts(at):%H:%M} {part}")
                keep.append(f[:5] + [f"told={stamp}"])
            else:
                keep.append(f)
        elif now - ts(told) >= WAIT:
            out.append(f"WRITE {doc} {post} {ts(told):%H:%M}")
        else:
            keep.append(f)
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(" ".join(f) + "\n" for f in keep))
    if keep:
        due = []
        for f in keep:
            told = next((x[5:] for x in f[5:] if x.startswith("told=")), None)
            due.append((ts(told or f[2]) + WAIT - now).total_seconds() / 60)
        out.append(f"NEXT {max(1, math.ceil(min(due) - 1e-9))}")  # rounded up, no extra minute: the line at 10 min
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/tmp/clerk")
    ap.add_argument("--now", help="ISO UTC (default: the clock)")
    a = ap.parse_args(argv)
    now = ts(a.now) if a.now else datetime.now(timezone.utc)
    for line in run(a.dir, now):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
