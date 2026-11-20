#!/usr/bin/env python3
"""kit: a git copy of the board (kit version in anlonlt/board; origin anlonlt/harmonize scripts/kit.py; names in --config)

kit: a git copy of the board, and the lookups agents ask of it (part BD9, the kit's first step; docs/WORKFLOW-KIT.md).

The copy lives in a worktree of the branch `board-mirror`, NEVER on `main` (review CR-WF1: 2.5 MB and a commit per
sync would land in every session's merge). Default place: `../board-mirror` next to the repository (`--mirror` or
$KIT_MIRROR to change). Layout: board/posts/<id>.json, board/comments/<id>.json (the fields as the board stores them,
keys sorted), board/sync.json (the cursor and a log of syncs).

    python3 -I scripts/kit.py init                      # the worktree (from origin/board-mirror, else a new orphan branch)
    python3 -I scripts/kit.py sync DIR --at ISO [--full] [--commit]
    python3 -I scripts/kit.py diff DUMP [--window ISO ISO]
    python3 -I scripts/kit.py post <id> [--head N]
    python3 -I scripts/kit.py open [--since ISO]
    python3 -I scripts/kit.py since <ISO>
    python3 -I scripts/kit.py session <post>
    python3 -I scripts/kit.py reads [--at ISO]         # prints the board reads one sync needs

THE SYNC (one way, board -> git). DIR holds what ArtifactData reads saved with `out_dir` (DIR/posts/*.json,
DIR/comments/*.json). `kit reads` prints them: every post (`list`, posts carry no reliable update stamp: 12 of 173), and
the comments whose created_at, read_at, delivered_at, arrived_at or updated_at is within the last hour before the
previous sync (the overlap: a session's typed stamp can lag the truth by minutes, CR-WF1 found one 4 min before the
comment it answers; his read marks come within 1.5 min of his comment, measured on 1,286 comments 2026-11-20).
The `version` of a document is not in the saved files and cannot be filtered on, so changes are found by those
stamps, not by version. Not found by a sync: a field written later WITHOUT a stamp more than an hour after the last
stamp (`became_post`, a `read` flip without read_at, the 7 such are from 11-12), and a deleted comment. The gate
(`kit diff` against a full dump) is what says whether that matters.
`sync --at` is the time taken BEFORE the reads (the next cursor); `--full` means DIR is a full dump (comments not
in it are removed). Posts are always replaced in full.

`kit diff DUMP` compares the copy with a full dump, every field of every post and comment, and exits 1 on any
difference. `--window A B` (the minutes the two reads spanned) names a difference whose document carries a stamp
inside it a race (it was written between the reads), counted apart; it still exits 1.

`kit open` counts as scripts/answer-delay.py does (its is_his / is_answer): his comment with words, answered by a
session comment with `reply_to` his doc_id or thread id, else one on the same post from his minute. One fix: an
answer without reply_to does not answer his comment that has no thread_id (answer-delay lets it: 1 of 398).
"""
import argparse
import re
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = {}
BRANCH = "board-mirror"
STAMPS = ("created_at", "read_at", "delivered_at", "arrived_at", "updated_at")
OVERLAP = timedelta(hours=1)


def configure(path):
    """--config config/<project>.json: the mirror's branch, path, overlap and the stamps a sync reads by."""
    global CONFIG, BRANCH, STAMPS, OVERLAP
    with open(path, encoding="utf-8") as fh:
        CONFIG = json.load(fh)
    f, mi = CONFIG.get("fields", {}), CONFIG.get("mirror", {})
    BRANCH = mi.get("branch", BRANCH)
    STAMPS = tuple([f.get("created", "created_at")] + list(f.get("later_stamps", STAMPS[1:])))
    OVERLAP = timedelta(hours=mi.get("overlap_hours", 1))


def mirror_dir(a):
    mp = CONFIG.get("mirror", {}).get("path")
    return a.mirror or os.environ.get("KIT_MIRROR") or (os.path.join(ROOT, mp) if mp else os.path.join(os.path.dirname(ROOT), "board-mirror"))


def load_dir(d, coll):
    out = {}
    for f in glob.glob(os.path.join(d, coll, "*.json")):
        with open(f, encoding="utf-8") as fh:
            out[os.path.basename(f)[:-5]] = json.load(fh)
    return out


def write_doc(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")[:19]).replace(tzinfo=timezone.utc)


def zstamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(m):
    p = os.path.join(m, "board", "sync.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {"cursor": None, "log": []}


# ---------------------------------------------------------------- sync
def sync(m, d, at, full=False):
    """Overlay DIR on the copy. Returns (posts written, posts removed, comments new, comments changed, comments removed)."""
    posts = load_dir(d, "posts")
    if not posts:
        raise SystemExit("kit sync: %s/posts is empty: the posts must be listed in full at every sync" % d)
    pdir, cdir = os.path.join(m, "board", "posts"), os.path.join(m, "board", "comments")
    old_p = load_dir(m + "/board", "posts")
    for i in old_p:
        if i not in posts:
            os.remove(os.path.join(pdir, i + ".json"))
    pw = 0
    for i, doc in posts.items():
        if old_p.get(i) != doc:
            write_doc(os.path.join(pdir, i + ".json"), doc)
            pw += 1
    comments = load_dir(d, "comments")
    old_c = load_dir(m + "/board", "comments")
    new = changed = removed = 0
    for i, doc in comments.items():
        if i not in old_c:
            new += 1
        elif old_c[i] != doc:
            changed += 1
        else:
            continue
        write_doc(os.path.join(cdir, i + ".json"), doc)
    if full:
        for i in old_c:
            if i not in comments:
                os.remove(os.path.join(cdir, i + ".json"))
                removed += 1
    st = read_state(m)
    st["cursor"] = at
    st["log"].append({"at": at, "full": full, "posts": len(posts), "posts_written": pw,
                      "posts_removed": sum(i not in posts for i in old_p), "comments_read": len(comments),
                      "new": new, "changed": changed, "removed": removed})
    write_doc(os.path.join(m, "board", "sync.json"), st)
    return pw, sum(i not in posts for i in old_p), new, changed, removed


def reads(m, at=None):
    """The ArtifactData reads one sync needs, as lines (the agent runs them with out_dir)."""
    st = read_state(m)
    if not st["cursor"]:
        return ["no cursor: bootstrap with a full dump (list posts, list comments: every page), then `kit sync DIR --at <time before the reads> --full`"]
    since = zstamp(iso(st["cursor"]) - OVERLAP)
    out = ["list posts limit 1000 (every page)"]
    out += ['query comments where [["%s", ">=", "%s"]] limit 1000' % (f, since) for f in STAMPS]
    out.append("then: kit sync DIR --at %s" % (at or "<the UTC time taken before these reads>"))
    return out


# ---------------------------------------------------------------- diff
def diff(m, dump, window=None):
    """[(kind, collection, id, field-or-None)] between the copy and a full dump; kind in missing, extra, changed."""
    out = []
    for coll in ("posts", "comments"):
        a, b = load_dir(m + "/board", coll), load_dir(dump, coll)
        for i in sorted(set(a) | set(b)):
            if i not in a:
                out.append(("missing", coll, i, None))  # on the board, not in the copy
            elif i not in b:
                out.append(("extra", coll, i, None))    # in the copy, not on the board
            else:
                for k in sorted(set(a[i]) | set(b[i])):
                    if a[i].get(k, KeyError) != b[i].get(k, KeyError):
                        out.append(("changed", coll, i, k))
    if window:
        lo, hi = window
        docs = {}
        for coll in ("posts", "comments"):
            docs.update({(coll, i): v for i, v in load_dir(dump, coll).items()})
            docs.update({(coll, i): v for i, v in load_dir(m + "/board", coll).items() if (coll, i) not in docs})
        tagged = []
        for kind, coll, i, k in out:
            doc = docs.get((coll, i), {})
            race = any(isinstance(doc.get(s), str) and lo <= doc[s] <= hi for s in STAMPS + ("done_at",))
            tagged.append((kind + (" (race)" if race else ""), coll, i, k))
        out = tagged
    return out


# ---------------------------------------------------------------- queries
def comments_of(m, post_id):
    cs = [dict(v, doc_id=i) for i, v in load_dir(m + "/board", "comments").items() if v.get("post_id") == post_id]
    return sorted(cs, key=lambda c: c.get("arrived_at") or c.get("created_at") or "")


def cut(s, n):
    s = s or ""
    return s if n is None or len(s) <= n else s[:n] + " [… %d more chars]" % (len(s) - n)


def post(m, pid, head=None):
    p = os.path.join(m, "board", "posts", pid + ".json")
    if not os.path.exists(p):
        return "no post %s in the copy" % pid
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    lines = ["%s · %s · %s%s · part %s · %s · created %s%s" % (
        pid, d.get("title", ""), d.get("column", "?"), (" (%s)" % d["status"]) if d.get("status") else "",
        d.get("part", "?"), d.get("session_id", "?"), (d.get("created_at") or "?")[:16],
        (" · done " + d["done_at"][:16]) if d.get("done_at") else "")]
    if d.get("options"):
        lines.append("options: %s (default %s)" % (" | ".join(d["options"]), d.get("default_option")))
    lines.append(cut(d.get("body"), head))
    cs = comments_of(m, pid)
    if cs:
        lines.append("--- %d comments" % len(cs))
    for c in cs:
        what = "👍" if c.get("like") else ("[answer] " + c["answer"]) if c.get("answer") else cut(c.get("text"), head)
        to = (" → " + c["reply_to"]) if c.get("reply_to") else ""
        lines.append("[%s %s %s%s] %s" % (c.get("created_at", "")[5:16], c.get("from"), c["doc_id"], to, what))
    return "\n".join(lines)


class _Waits:
    """His comment with words / a session's answer: as Harmonize's scripts/answer-delay.py, names from the config."""
    CLERK_LINE = re.compile(r"^\s*(passed to|not answered yet)", re.I)

    def __init__(self, owner="owner", clerk="clerk"):
        self.owner, self.clerk = owner, clerk

    def ts(self, s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")[:19]).replace(tzinfo=timezone.utc)

    def is_his(self, c):
        return (c.get("from") == self.owner and not c.get("like") and not c.get("answer")
                and (c.get("text") or "").strip() not in ("", "\U0001F44D"))

    def is_answer(self, c):
        return (c.get("from") not in (self.owner, self.clerk) and not c.get("like")
                and not self.CLERK_LINE.match(c.get("text") or ""))


def answer_delay():
    f = CONFIG.get("fields", {})
    return _Waits(f.get("owner", "owner"), f.get("clerk", "clerk"))


def open_(m, since="2026-11-12"):
    ad = answer_delay()
    cs = [dict(v, doc_id=i) for i, v in load_dir(m + "/board", "comments").items() if v.get("created_at") and v.get("post_id")]
    answers = [c for c in cs if ad.is_answer(c)]
    lines = []
    for h in sorted(cs, key=lambda c: c["created_at"]):
        if not (ad.is_his(h) and h["created_at"] >= since):
            continue
        t0 = ad.ts(h["created_at"]).replace(second=0)
        # as answer-delay.waits, except that an answer with NO reply_to never matches his comment without a thread_id
        # (answer-delay's `reply_to in (doc_id, None)` does: 1 of his 398 comments since 11-12, xc66fupt…, BD9)
        if any((o.get("reply_to") and o["reply_to"] in (h["doc_id"], h.get("thread_id")))
               or (o["post_id"] == h["post_id"] and ad.ts(o["created_at"]) >= t0) for o in answers):
            continue
        lines.append("%s on %s, his %s: %s" % (h["doc_id"], h["post_id"], h["created_at"][:16].replace("T", " "),
                                               (h.get("text") or "").replace("\n", " ")[:100]))
    return "\n".join(lines) if lines else "nothing of his open since %s" % since


def since(m, t):
    rows = []
    for i, d in load_dir(m + "/board", "posts").items():
        for s in ("created_at", "updated_at", "done_at"):
            if (d.get(s) or "") >= t:
                rows.append((d[s], "post %s %s (%s) %s" % (i, s.replace("_at", ""), d.get("column"), d.get("title", "")[:70])))
                break
    for i, c in load_dir(m + "/board", "comments").items():
        s = max((c.get(k) or "") for k in STAMPS)
        if s >= t:
            what = "👍" if c.get("like") else ("[answer] " + c["answer"]) if c.get("answer") else (c.get("text") or "").replace("\n", " ")[:90]
            rows.append((s, "comment %s on %s from %s: %s" % (i, c.get("post_id"), c.get("from"), what)))
    rows.sort()
    return "\n".join("%s %s" % (s[5:16], r) for s, r in rows) or "nothing since %s" % t


def session(m, pid):
    p = os.path.join(m, "board", "posts", pid + ".json")
    if not os.path.exists(p):
        return "no post %s in the copy" % pid
    with open(p, encoding="utf-8") as fh:
        return json.load(fh).get("session_id") or "no session_id on %s" % pid


# ---------------------------------------------------------------- init / commit
def git(m, *args):
    return subprocess.run(["git", "-C", m] + list(args), capture_output=True, text=True)


def init(m):
    if os.path.exists(os.path.join(m, ".git")):
        return "%s exists" % m
    subprocess.run(["git", "-C", ROOT, "fetch", "origin", BRANCH], capture_output=True)
    has = subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "origin/" + BRANCH], capture_output=True).returncode == 0
    if has:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "-B", BRANCH, m, "origin/" + BRANCH], capture_output=True, text=True)
    else:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "--orphan", "-b", BRANCH, m], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def commit(m, msg):
    if git(m, "symbolic-ref", "--short", "HEAD").stdout.strip() != BRANCH:
        raise SystemExit("kit: %s is not on branch %s; refusing to commit the copy anywhere else" % (m, BRANCH))
    git(m, "add", "-A", "board")
    r = git(m, "commit", "-q", "-m", msg)
    return (r.stdout + r.stderr).strip() or "committed"


def main(argv=None):
    ap = argparse.ArgumentParser(description="kit: the board's git copy and its lookups")
    ap.add_argument("--mirror", help="the copy's worktree (default $KIT_MIRROR, the config's mirror.path, or ../board-mirror)")
    ap.add_argument("--config", help="config/<project>.json (branch, stamps, owner and clerk names)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    s = sp.add_parser("sync"); s.add_argument("dir"); s.add_argument("--at", required=True)
    s.add_argument("--full", action="store_true"); s.add_argument("--commit", action="store_true")
    s = sp.add_parser("diff"); s.add_argument("dump"); s.add_argument("--window", nargs=2)
    s = sp.add_parser("post"); s.add_argument("id"); s.add_argument("--head", type=int)
    s = sp.add_parser("open"); s.add_argument("--since", default="2026-11-12")
    s = sp.add_parser("since"); s.add_argument("t")
    s = sp.add_parser("session"); s.add_argument("post")
    s = sp.add_parser("reads"); s.add_argument("--at")
    a = ap.parse_args(argv)
    if a.config:
        configure(a.config)
    m = mirror_dir(a)
    if a.cmd == "init":
        print(init(m)); return 0
    if a.cmd == "sync":
        r = sync(m, a.dir, a.at, a.full)
        print("sync %s: posts %d written, %d removed; comments %d new, %d changed, %d removed" % ((a.at,) + r))
        if a.commit:
            print(commit(m, "kit sync %s%s" % (a.at, " (full)" if a.full else "")))
        return 0
    if a.cmd == "diff":
        ds = diff(m, a.dump, a.window)
        for kind, coll, i, k in ds:
            print("%s %s %s%s" % (kind, coll, i, (" ." + k) if k else ""))
        print("%d differences (%d races)" % (len(ds), sum("race" in d[0] for d in ds)))
        return 1 if ds else 0
    print({"post": lambda: post(m, a.id, a.head), "open": lambda: open_(m, a.since), "since": lambda: since(m, a.t),
           "session": lambda: session(m, a.post), "reads": lambda: "\n".join(reads(m, a.at))}[a.cmd]())
    return 0


if __name__ == "__main__":
    sys.exit(main())
