#!/usr/bin/env python3
"""kit: a git copy of the board (kit version in anlonlt/board; origin anlonlt/harmonize scripts/kit.py; names in --config)

kit: a git copy of the board, and the lookups agents ask of it (part BD9, the kit's first step; docs/WORKFLOW-KIT.md).

The copy lives in a worktree of the branch `board-mirror`, NEVER on `main` (review CR-WF1: 2.5 MB and a commit per
sync would land in every session's merge). Default place: `../board-mirror` next to the repository (`--mirror` or
$KIT_MIRROR to change). Layout: board/posts/<id>.json, board/comments/<id>.json (the fields as the board stores them,
keys sorted), board/sync.json (the cursor and a log of syncs).

    python3 -I scripts/kit.py init                      # the worktree (from origin/board-mirror, else a new orphan branch)
    python3 -I scripts/kit.py sync DIR --at ISO [--full] [--commit]
    python3 -I scripts/kit.py sync-db [--db PATH] [--at ISO] [--full] [--commit]
    python3 -I scripts/kit.py receive [--board-url URL] [--token TOK] [--branch B]
    python3 -I scripts/kit.py publish [--board-url URL] [--token TOK] [--branch B] [--db PATH] [--at ISO]
    python3 -I scripts/kit.py diff DUMP [--window ISO ISO]
    python3 -I scripts/kit.py post <id> [--head N]
    python3 -I scripts/kit.py open [--since ISO]
    python3 -I scripts/kit.py since <ISO>
    python3 -I scripts/kit.py session <post>
    python3 -I scripts/kit.py reads [--at ISO]         # prints the board reads one sync needs
    python3 -I scripts/kit.py welcome [--runtime agy|claude-cli|claude|arena|generic] [--post]

STATE SEMANTICS:
- Comment `read` / `read_at` represents "delivered/read by the watching agent (e.g. agy clerk)", independent of the
  owner's `board-seen` UI dots. Non-watching runtimes (e.g. embedded preview / Arena) leave `delivered: false` until
  a watching runtime or manual `receive` processes them.
- Owner seen-state (`data/users/owner/board-seen`) is exported to `board/seen/owner.json` by `sync-db` and reconciled
  by `receive` using monotonic max-timestamp deep-merge so two devices/runtimes never lose read state.

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
import fnmatch
import importlib.util
import contextlib
import json
import os
import re
import shutil
import tempfile
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
try:
    import fcntl
except ImportError:
    fcntl = None

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


def board_docs(m, coll):
    """A collection of the board's git copy, or, with no git copy (an Artifact board's local copy), of the local board."""
    if os.path.isdir(os.path.join(m, "board", coll)):
        return load_dir(m + "/board", coll)
    posts, comments = _load_board_docs(m)
    return posts if coll == "posts" else comments


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


def _export_seen_from_db(conn, m):
    """Export data/users/*/board-seen docs to board/seen/<user>.json (if changed)."""
    sdir = os.path.join(m, "board", "seen")
    old_s = load_dir(m + "/board", "seen") if os.path.isdir(sdir) else {}
    rows = conn.execute(
        "SELECT collection, data FROM docs WHERE doc_id='board-seen' AND collection LIKE 'data/users/%'"
    ).fetchall()
    written = 0
    for coll, raw in rows:
        user = coll[len("data/users/"):].strip("/")
        if not user or "/" in user:
            continue
        doc = json.loads(raw)
        if old_s.get(user) != doc:
            write_doc(os.path.join(sdir, user + ".json"), doc)
            written += 1
    return written


def _export_blobs_to_mirror(db_path, m, max_bytes=5 * 1024 * 1024):
    """Copy local blobs (under max_bytes) and their .json metadata into <mirror>/board/blobs/."""
    src_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "blobs")
    if not os.path.isdir(src_dir):
        return 0
    dst_dir = os.path.join(m, "board", "blobs")
    os.makedirs(dst_dir, exist_ok=True)
    copied = 0
    for fn in os.listdir(src_dir):
        if not re.fullmatch(r"[a-fA-F0-9]{16,64}", fn):
            continue
        sp = os.path.join(src_dir, fn)
        if not os.path.isfile(sp) or os.path.getsize(sp) > max_bytes:
            continue
        dp = os.path.join(dst_dir, fn)
        if not os.path.exists(dp) or os.path.getsize(dp) != os.path.getsize(sp):
            shutil.copy2(sp, dp)
            copied += 1
        meta_s = sp + ".json"
        if os.path.isfile(meta_s):
            shutil.copy2(meta_s, dp + ".json")
    return copied


def sync_db(m, db_path=None, at=None, full=False, with_blobs=False):
    """Sync directly from the local SQLite board.db (O(delta) when changes log cursor exists, else full)."""
    import sqlite3
    import tempfile

    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    if not os.path.isfile(db):
        raise SystemExit("kit sync-db: %s does not exist" % db)
    now = at or zstamp(datetime.now(timezone.utc))
    st = read_state(m)
    last_seq = int(st.get("db_seq") or 0)
    conn = sqlite3.connect(db, timeout=5)
    try:
        latest_seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
        min_seq = conn.execute("SELECT COALESCE(MIN(seq), 0) FROM changes").fetchone()[0]
        can_delta = (not full) and bool(st.get("cursor")) and last_seq > 0 and (min_seq == 0 or last_seq >= min_seq - 1)
        if not can_delta:
            tmp = tempfile.mkdtemp(prefix="kit-sync-db-")
            try:
                for coll in ("posts", "comments"):
                    os.makedirs(os.path.join(tmp, coll), exist_ok=True)
                    for doc_id, raw in conn.execute("SELECT doc_id, data FROM docs WHERE collection=?", (coll,)):
                        write_doc(os.path.join(tmp, coll, doc_id + ".json"), json.loads(raw))
                res = sync(m, tmp, now, full=True)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            _export_seen_from_db(conn, m)
            if with_blobs:
                _export_blobs_to_mirror(db, m)
            st2 = read_state(m)
            st2["db_seq"] = latest_seq
            write_doc(os.path.join(m, "board", "sync.json"), st2)
            return res

        rows = conn.execute(
            "SELECT DISTINCT collection, doc_id FROM changes WHERE seq>? "
            "AND (collection IN ('posts', 'comments') OR (collection LIKE 'data/users/%' AND doc_id='board-seen'))",
            (last_seq,),
        ).fetchall()
        pdir, cdir = os.path.join(m, "board", "posts"), os.path.join(m, "board", "comments")
        sdir = os.path.join(m, "board", "seen")
        old_p, old_c = load_dir(m + "/board", "posts"), load_dir(m + "/board", "comments")
        old_s = load_dir(m + "/board", "seen") if os.path.isdir(sdir) else {}
        pw = pr = new = changed = removed = 0
        for coll, doc_id in rows:
            r = conn.execute("SELECT data FROM docs WHERE collection=? AND doc_id=?", (coll, doc_id)).fetchone()
            if coll == "posts":
                target = os.path.join(pdir, doc_id + ".json")
                if not r:
                    if doc_id in old_p and os.path.exists(target):
                        os.remove(target)
                        pr += 1
                else:
                    doc = json.loads(r[0])
                    if old_p.get(doc_id) != doc:
                        write_doc(target, doc)
                        pw += 1
            elif coll == "comments":
                target = os.path.join(cdir, doc_id + ".json")
                if not r:
                    if doc_id in old_c and os.path.exists(target):
                        os.remove(target)
                        removed += 1
                else:
                    doc = json.loads(r[0])
                    if doc_id not in old_c:
                        new += 1
                        write_doc(target, doc)
                    elif old_c[doc_id] != doc:
                        changed += 1
                        write_doc(target, doc)
            elif coll.startswith("data/users/") and doc_id == "board-seen":
                user = coll[len("data/users/"):].strip("/")
                if user and "/" not in user and r:
                    doc = json.loads(r[0])
                    if old_s.get(user) != doc:
                        write_doc(os.path.join(sdir, user + ".json"), doc)
        st["cursor"] = now
        st["db_seq"] = latest_seq
        st["log"].append({"at": now, "full": False, "db_seq": latest_seq, "posts": len(old_p) - pr,
                          "posts_written": pw, "posts_removed": pr, "comments_read": len(rows),
                          "new": new, "changed": changed, "removed": removed})
        write_doc(os.path.join(m, "board", "sync.json"), st)
        return pw, pr, new, changed, removed
    finally:
        conn.close()


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
    cs = [dict(v, doc_id=i) for i, v in board_docs(m, "comments").items() if v.get("post_id") == post_id]
    return sorted(cs, key=lambda c: c.get("arrived_at") or c.get("created_at") or "")


def cut(s, n):
    s = s or ""
    return s if n is None or len(s) <= n else s[:n] + " [… %d more chars]" % (len(s) - n)


def post(m, pid, head=None):
    d = board_docs(m, "posts").get(pid)
    if d is None:
        return "no post %s in the copy" % pid
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
        # a review is not an answer to him (it may be under the same post, later): only a reply to the owner's comment is
        return (c.get("from") not in (self.owner, self.clerk) and not c.get("like")
                and not self.CLERK_LINE.match(c.get("text") or "")
                and (c.get("kind") != "review" or bool(c.get("reply_to"))))


def answer_delay():
    f = CONFIG.get("fields", {})
    return _Waits(f.get("owner", "owner"), f.get("clerk", "clerk"))


def open_(m, since="2000-01-01"):
    return _open_of(board_docs(m, "comments"), since)


def _open_of(comments, since="2000-01-01", empty=True):
    """The owner's comments with no answer after them, from a {doc_id: comment} map (the host's or an attached board's)."""
    ad = answer_delay()
    cs = [dict(v, doc_id=i) for i, v in comments.items() if v.get("created_at") and v.get("post_id")]
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
    if not empty:
        return lines
    return "\n".join(lines) if lines else "nothing of his open since %s" % since


def since(m, t):
    rows = []
    for i, d in board_docs(m, "posts").items():
        for s in ("created_at", "updated_at", "done_at"):
            if (d.get(s) or "") >= t:
                rows.append((d[s], "post %s %s (%s) %s" % (i, s.replace("_at", ""), d.get("column"), d.get("title", "")[:70])))
                break
    for i, c in board_docs(m, "comments").items():
        s = max((c.get(k) or "") for k in STAMPS)
        if s >= t:
            what = "👍" if c.get("like") else ("[answer] " + c["answer"]) if c.get("answer") else (c.get("text") or "").replace("\n", " ")[:90]
            rows.append((s, "comment %s on %s from %s: %s" % (i, c.get("post_id"), c.get("from"), what)))
    rows.sort()
    return "\n".join("%s %s" % (s[5:16], r) for s, r in rows) or "nothing since %s" % t


def session(m, pid):
    d = board_docs(m, "posts").get(pid)
    if d is None:
        return "no post %s in the copy" % pid
    return d.get("session_id") or "no session_id on %s" % pid


# ---------------------------------------------------------------- warm handoff (receive / publish)
# CL30: the reads that need a fresh copy of an Artifact board (pull + load first)
STALE_GATED = ("resume", "train", "post", "open", "since", "session", "search", "queue", "sections", "report")


def tar_name(n):
    """A path without its leading "./" (CL30: `lstrip("./")` also ate the dot of `.story/` and `.kit/`)."""
    n = str(n or "")
    while n.startswith("./"):
        n = n[2:]
    return n


class Conflict(Exception):
    pass


def _resolve_board_url(explicit=None):
    u = (explicit or os.environ.get("KIT_BOARD_URL") or CONFIG.get("board_url") or "http://127.0.0.1:8765").rstrip("/")
    return local_board_url() if "claude.ai/" in u else u   # an Artifact board: the scripts use its local copy


def _resolve_board_token(explicit=None):
    if explicit:
        return explicit
    env_tok = os.environ.get("KIT_BOARD_TOKEN")
    if env_tok:
        return env_tok
    tok_path = os.path.join(ROOT, ".kit", "board_data", "token")
    if os.path.isfile(tok_path):
        try:
            tok = open(tok_path, encoding="utf-8").read().strip()
            if tok:
                return tok
        except OSError:
            pass
    return None


def _board_request(url, path, body=None, token=None, method=None):
    hdr = {"Content-Type": "application/json"}
    if token:
        hdr["X-Kit-Token"] = token
    req = urllib.request.Request(
        url.rstrip("/") + "/_api/" + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=hdr,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.load(res)


def merge_board_seen(local_doc, incoming_doc):
    """Monotonic max-timestamp deep-merge of board-seen documents so two devices never lose read state."""
    loc = local_doc if isinstance(local_doc, dict) else {}
    inc = incoming_doc if isinstance(incoming_doc, dict) else {}
    merged = dict(loc)
    for k, v in inc.items():
        if k == "posts":
            continue
        if isinstance(v, str) and isinstance(merged.get(k), str):
            merged[k] = max(merged[k], v)
        elif k not in merged:
            merged[k] = v
    loc_posts = loc.get("posts") if isinstance(loc.get("posts"), dict) else {}
    inc_posts = inc.get("posts") if isinstance(inc.get("posts"), dict) else {}
    posts = {pid: (dict(e) if isinstance(e, dict) else e) for pid, e in loc_posts.items()}
    for pid, inc_entry in inc_posts.items():
        cur = posts.get(pid)
        if not isinstance(cur, dict) or not isinstance(inc_entry, dict):
            if pid not in posts:
                posts[pid] = dict(inc_entry) if isinstance(inc_entry, dict) else inc_entry
            continue
        loc_at, inc_at = str(cur.get("at") or ""), str(inc_entry.get("at") or "")
        loc_bt, inc_bt = str(cur.get("bt") or ""), str(inc_entry.get("bt") or "")
        if inc_at > loc_at:
            out = dict(cur)
            out.update(inc_entry)
        elif loc_at > inc_at:
            out = dict(inc_entry)
            out.update(cur)
        else:
            out = dict(cur)
            for k, v in inc_entry.items():
                if k not in out:
                    out[k] = v
            if inc_bt > loc_bt and inc_entry.get("sig"):
                out["sig"] = inc_entry["sig"]
        max_bt = max(loc_bt, inc_bt)
        if max_bt:
            out["bt"] = max_bt
        posts[pid] = out
    merged["posts"] = posts
    return merged


def _git_checked(m, *args):
    r = git(m, *args)
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, ["git", "-C", m] + list(args), r.stdout, r.stderr)
    return r.stdout


def receive(m, url=None, branch=None, token=None):
    """Fetch origin/<branch>, 3-way check changed board docs against running board server, apply atomically."""
    url = _resolve_board_url(url)
    token = _resolve_board_token(token)
    branch = branch or BRANCH
    if git(m, "check-ref-format", "refs/heads/" + branch).returncode != 0:
        raise Conflict("Invalid branch name: " + branch)
    ref = "refs/remotes/origin/" + branch
    r_fetch = git(m, "fetch", "--no-tags", "origin", "refs/heads/" + branch + ":" + ref)
    if r_fetch.returncode != 0:
        msg = (r_fetch.stdout + r_fetch.stderr).strip()
        if "non-fast-forward" in msg.lower() or "rejected" in msg.lower():
            raise Conflict("Remote history was rewritten (non-fast-forward fetch); refusing to apply: " + msg)
        raise subprocess.CalledProcessError(
            r_fetch.returncode, ["git", "-C", m, "fetch", "--no-tags", "origin", "refs/heads/" + branch + ":" + ref],
            r_fetch.stdout, r_fetch.stderr,
        )
    head = _git_checked(m, "rev-parse", ref).strip()
    receipt_key = "mirror-" + branch.encode("utf-8").hex()
    receipt = _board_request(url, "doc/kit-receipts/" + receipt_key, token=token)
    base = (receipt.get("data") or {}).get("sha")
    if head == base:
        return {"sha": head, "applied": 0, "status": "up to date"}
    dirs = ["board/posts", "board/comments", "board/seen"]
    if base:
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(base)):
            raise Conflict("Invalid local receipt")
        if git(m, "merge-base", "--is-ancestor", base, head).returncode != 0:
            raise Conflict("Receipt is not an ancestor of the remote tip; refusing to apply")
        paths = _git_checked(m, "diff", "--name-only", "--no-renames", "-z", base, head, "--", *dirs)
    else:
        paths = _git_checked(m, "ls-tree", "-r", "--name-only", "-z", head, "--", *dirs)

    def doc_at(rev, path):
        if not rev or not _git_checked(m, "ls-tree", rev, "--", ":(literal)" + path):
            return None
        obj = json.loads(_git_checked(m, "show", rev + ":" + path))
        if not isinstance(obj, dict):
            raise ValueError("Not a board document: " + path)
        return obj

    writes = []
    for path in filter(None, paths.split("\0")):
        if not path.endswith(".json"):
            continue
        match = re.fullmatch(r"board/(posts|comments|seen)/([^/]+)\.json", path)
        if not match:
            raise Conflict("Unsupported mirror path: " + path)
        kind, key = match.groups()
        old, new = doc_at(base, path), doc_at(head, path)
        if kind == "seen":
            coll = "data/users/" + key
            doc_id = "board-seen"
            api_path = "doc/" + "/".join(urllib.parse.quote(p, safe="") for p in (coll.split("/") + [doc_id]))
            d = _board_request(url, api_path, token=token)
            local = d["data"] if d.get("exists") else None
            if new is None:
                if local is None or new == old:
                    continue
                raise Conflict("Hard deletion requires reconciliation: " + path)
            merged = merge_board_seen(local, new)
            if merged == local:
                continue
            writes.append({"op": "set", "collection": coll, "doc_id": doc_id, "data": merged, "if_version": d["version"]})
            continue

        coll, doc_id = kind, key
        d = _board_request(url, "doc/" + coll + "/" + urllib.parse.quote(doc_id, safe=""), token=token)
        local = d["data"] if d.get("exists") else None
        if new == local or new == old:
            continue
        if new is None:
            raise Conflict("Hard deletion requires reconciliation: " + path)
        if local != old:
            raise Conflict("Divergent record: " + path)
        writes.append({"op": "set", "collection": coll, "doc_id": doc_id, "data": new, "if_version": d["version"]})

    count = len(writes)
    writes.append({"op": "set", "collection": "kit-receipts", "doc_id": receipt_key,
                   "data": {"sha": head}, "if_version": receipt["version"]})
    try:
        _board_request(url, "db/batch", {"writes": writes}, token=token)
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise Conflict("Board changed during receive; batch rolled back. Retry.") from e
        raise
    return {"sha": head, "applied": count, "status": "received"}


def publish(m, url=None, db_path=None, branch=None, token=None, at=None):
    """Receive remote changes first, fast-forward mirror, full-export SQLite, normal push, verify tip, advance receipt."""
    url = _resolve_board_url(url)
    token = _resolve_board_token(token)
    branch = branch or BRANCH
    rec = receive(m, url=url, branch=branch, token=token)
    r_ff = git(m, "merge", "--ff-only", "origin/" + branch)
    if r_ff.returncode != 0:
        raise Conflict("Mirror worktree cannot fast-forward to origin/%s: %s" % (branch, (r_ff.stdout + r_ff.stderr).strip()))
    at_val = at or zstamp(datetime.now(timezone.utc))
    sync_db(m, db_path=db_path, at=at_val, full=True)
    if git(m, "status", "--porcelain", "board").stdout.strip():
        commit(m, "kit publish %s (full)" % at_val, branch=branch)
    r_push = git(m, "push", "-u", "origin", branch)
    if r_push.returncode != 0:
        raise Conflict("Push rejected (never force-push): " + (r_push.stdout + r_push.stderr).strip())
    sha = _git_checked(m, "rev-parse", "HEAD").strip()
    ls_out = _git_checked(m, "ls-remote", "origin", "refs/heads/" + branch).strip()
    remote_sha = (ls_out.split() or [""])[0]
    if remote_sha != sha:
        raise Conflict("Remote tip mismatch after push: local %s != remote %s" % (sha, remote_sha))
    receive(m, url=url, branch=branch, token=token)
    return {"sha": sha, "received": rec, "status": "published"}



def _git_board_docs(repo, ref):
    """{(coll, id): (data, commit time)} of posts, comments and seen at ref, read from git objects only."""
    import io
    import tarfile
    arc = subprocess.run(["git", "-C", repo, "archive", "--format=tar", ref, "board"], capture_output=True, timeout=180)
    docs = {}
    if arc.returncode != 0 or not arc.stdout:
        return docs
    with tarfile.open(fileobj=io.BytesIO(arc.stdout), mode="r:*") as tf:
        for mem in tf.getmembers():
            parts = tar_name(mem.name).split("/")
            if not mem.isfile() or len(parts) != 3 or parts[1] not in ("posts", "comments", "seen") or not parts[2].endswith(".json"):
                continue
            try:
                data = json.loads(tf.extractfile(mem).read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                continue
            if isinstance(data, dict):
                docs[(parts[1], parts[2][:-5])] = [data, ""]
    # when each file last changed in git: the first time `git log` (newest first) names it
    log = subprocess.run(["git", "-C", repo, "log", "--format=@%cI", "--name-only", ref, "--", "board/posts",
                          "board/comments", "board/seen"], capture_output=True, text=True, timeout=180).stdout
    when = ""
    for line in log.splitlines():
        if line.startswith("@"):
            when = _utc(line[1:])
            continue
        parts = line.strip().split("/")
        if len(parts) == 3 and parts[2].endswith(".json"):
            k = (parts[1], parts[2][:-5])
            if k in docs and not docs[k][1]:
                docs[k][1] = when
    return {k: (v[0], v[1]) for k, v in docs.items()}


def _utc(ts):
    """An ISO time as UTC 'YYYY-MM-DDTHH:MM:SSZ' (comparable as text); '' if unreadable."""
    try:
        t = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconcile(m, url=None, token=None, branch=None, db_path=None, push=True):
    """The board here against its git copy (origin/<branch>), document by document; the more recent copy wins.

    Run by the terminal keyword (`up`) before the orchestrator opens, and by web_takeover.py: work saved to git
    elsewhere (a web session during a usage limit, another computer) comes back here, and what only this board has
    stays and goes to git. A post or comment in git only is added here; one in both, different, is taken from git
    when git changed it after this board did (git: the commit that last touched the file; here: the board's own
    write time); read marks are merged. Nothing is deleted. Then the git copy is brought up to date and pushed."""
    import sqlite3
    url = _resolve_board_url(url)
    token = _resolve_board_token(token)
    branch = branch or BRANCH
    repo = m if os.path.isdir(os.path.join(m, ".git")) or os.path.isfile(os.path.join(m, ".git")) else ROOT
    ref = "refs/remotes/origin/" + branch
    git(repo, "fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:%s" % (branch, ref))
    if git(repo, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
        return {"status": "no git copy (origin/%s)" % branch, "from_git": 0, "added": 0, "kept_here": 0}
    head = _git_checked(repo, "rev-parse", ref).strip()
    remote = _git_board_docs(repo, ref)
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    local_t = {}
    if os.path.isfile(db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            for coll, did, ut in conn.execute("SELECT collection, doc_id, updated_at FROM docs WHERE collection IN "
                                              "('posts', 'comments') OR (collection LIKE 'data/users/%' AND doc_id='board-seen')"):
                if coll.startswith("data/users/"):
                    local_t[("seen", coll[len("data/users/"):])] = _utc(ut)
                else:
                    local_t[(coll, did)] = _utc(ut)
        finally:
            conn.close()
    writes, n_git, n_add, n_here = [], 0, 0, 0
    for (kind, key), (data, gt) in sorted(remote.items()):
        if kind == "seen":
            coll, doc_id = "data/users/" + key, "board-seen"
        else:
            coll, doc_id = kind, key
        api = "doc/" + "/".join(urllib.parse.quote(x, safe="") for x in coll.split("/") + [doc_id])
        d = _board_request(url, api, token=token)
        local = d["data"] if d.get("exists") else None
        if kind == "seen":
            new = merge_board_seen(local, data)
            if new != local:
                writes.append({"op": "set", "collection": coll, "doc_id": doc_id, "data": new, "if_version": d["version"]})
            continue
        if local == data:
            continue
        if local is None:
            n_add += 1
        elif gt and gt > local_t.get((kind, key), ""):
            n_git += 1
        else:
            n_here += 1
            continue
        writes.append({"op": "set", "collection": coll, "doc_id": doc_id, "data": data, "if_version": d["version"]})
    # one document at a time: a write can touch another (a step post updates the trains card), so each is pinned to
    # the version read just before it; one that moved meanwhile is read again once
    for w in writes:
        for attempt in (1, 2):
            try:
                _board_request(url, "db/batch", {"writes": [w]}, token=token)
                break
            except urllib.error.HTTPError as e:
                if e.code != 409 or attempt == 2:
                    raise Conflict("The board kept changing under %s/%s; run it again." % (w["collection"], w["doc_id"])) from e
                api = "doc/" + "/".join(urllib.parse.quote(x, safe="") for x in w["collection"].split("/") + [w["doc_id"]])
                cur = _board_request(url, api, token=token)
                if w["collection"].startswith("data/users/"):
                    w["data"] = merge_board_seen(cur.get("data") if cur.get("exists") else None, w["data"])
                w["if_version"] = cur["version"]
    receipt_key = "mirror-" + branch.encode("utf-8").hex()
    rc = _board_request(url, "doc/kit-receipts/" + receipt_key, token=token)
    _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": "kit-receipts", "doc_id": receipt_key,
                                                 "data": {"sha": head}, "if_version": rc.get("version", 0)}]}, token=token)
    pushed = ""
    if repo == m and push:
        # the git copy: origin's history first, then this board in full on top (it now holds both sides)
        if git(m, "status", "--porcelain", "board").stdout.strip():
            commit(m, "kit reconcile: this board before taking origin", branch=branch)
        if git(m, "merge", "--ff-only", "origin/" + branch).returncode != 0:
            r = git(m, "merge", "--no-edit", "-X", "ours", "origin/" + branch)
            if r.returncode != 0:
                git(m, "merge", "--abort")
        if os.path.isfile(db):
            sync_db(m, db_path=db, at=zstamp(datetime.now(timezone.utc)), full=True)
            if git(m, "status", "--porcelain", "board").stdout.strip():
                commit(m, "kit reconcile: local and git, the newer copy of each post", branch=branch)
        r = git(m, "push", "-q", "origin", "HEAD:" + branch)
        pushed = "pushed" if r.returncode == 0 else "push refused: " + (r.stdout + r.stderr).strip()[-200:]
    return {"status": "reconciled with origin/%s" % branch, "from_git": n_git, "added": n_add, "kept_here": n_here,
            "git": pushed or "git copy not updated here"}


# ---------------------------------------------------------------- Artifact mode (a claude.ai Artifact board)
# The board lives in the Artifact's database, which only the ArtifactData tool reaches. The kit's scripts (board_cli,
# `kit.py step`, the task-train hook, kit_cards, resume and every lookup) keep working against a LOCAL copy of the
# board (the local board server), and two commands carry it across in as few tool calls as possible:
#   artifact pull  -> the ArtifactData list calls that save the board to files, then `artifact load`
#   artifact push  -> what changed here, as batch files: one ArtifactData batch call each, then `artifact sent`
# A write to a document that already exists needs its version: versions are tracked from what this copy sent, and a
# refused pin names the document's real version (`artifact pin <collection>/<id>=<version>`, then push again).
ART_COLLS = ("posts", "comments")


def _art_dir():
    return os.environ.get("KIT_ARTIFACT_DIR") or os.path.join(ROOT, ".kit", "run", "artifact")


def _art_state():
    p = os.path.join(_art_dir(), "state.json")
    try:
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st.setdefault("versions", {})
    return st


def _art_save(st):
    os.makedirs(_art_dir(), exist_ok=True)
    write_doc(os.path.join(_art_dir(), "state.json"), st)


def attached_board():
    """The Artifact this repo's board lives in, from config alone (CL13): {"url", "prefix"} or None.
    `kit.artifact: {"url": <claude.ai/artifact/...>, "prefix": "repos/<id>/"}` (or `"repo": "<id>"`) says the board is
    an attached board inside another project's Artifact (the kit repo's board lives in Harmonize's, under
    repos/board/); a claude.ai `board_url` is a board of its own (prefix ""). $KIT_ARTIFACT_URL / $KIT_ARTIFACT_PREFIX
    override both."""
    c = _kit_config()
    k = c.get("kit") or {}
    a = k.get("artifact") if isinstance(k.get("artifact"), dict) else {}
    url = os.environ.get("KIT_ARTIFACT_URL") or str(a.get("url") or "")
    prefix = a.get("prefix")
    if prefix is None and a.get("repo"):
        prefix = "repos/%s/" % re.sub(r"[^a-z0-9_-]+", "-", str(a["repo"]).lower()).strip("-")
    if not url:
        for u in (CONFIG.get("board_url"), c.get("board_url"), (CONFIG.get("kit") or {}).get("artifact_url"), k.get("artifact_url")):
            if u and "claude.ai/" in str(u):
                url, prefix = str(u), prefix or ""
                break
    if not url:
        return None
    prefix = os.environ.get("KIT_ARTIFACT_PREFIX", prefix or "")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return {"url": url.rstrip("/"), "prefix": prefix}


def _art_prefix():
    a = attached_board()
    return a["prefix"] if a else ""


def _art_key(coll_or_key):
    """`repos/board/posts/x` (the Artifact's path) -> `posts/x` (this copy's key); a key without the prefix is kept."""
    pre = _art_prefix()
    return coll_or_key[len(pre):] if pre and coll_or_key.startswith(pre) else coll_or_key


def artifact_url():
    a = attached_board()
    return a["url"] if a else ""


def local_board_url():
    """The local board server (the copy the scripts use), even when the config's board_url is the Artifact."""
    u = os.environ.get("KIT_BOARD_URL") or str(CONFIG.get("board_url") or "")
    if u and "claude.ai/" not in u:
        return u.rstrip("/")
    return "http://127.0.0.1:%d" % int(os.environ.get("KIT_BOARD_PORT") or (CONFIG.get("kit") or {}).get("board_port") or 8765)


def _base_path(coll, did):
    return os.path.join(_art_dir(), "base", coll, did + ".json")


def _read_json(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def artifact_pull(full=False):
    """The calls that bring the board here. After a first full pull, only what changed since the last load (a full
    list of a big board costs ~20 tokens a document in the tool's reply: 1,500 documents, ~30k)."""
    board = artifact_url() or "<the board's claude.ai/artifact url>"
    d = os.path.join(_art_dir(), "in")
    since = "" if full else str(_art_state().get("loaded_at") or "")
    pre = _art_prefix()
    attached = [] if pre else _attached_ids()          # before the delta pull clears in/kit_repos/_index.json
    if since:
        t = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(minutes=10)
        since = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        for f in glob.glob(os.path.join(d, "*", "*.json")):
            os.remove(f)                                   # only the changes are loaded this time
    lines = ["# The board from the Artifact to this copy (%s)%s" % (board, " — changes since %s" % since if since else ""), ""]
    for c in ART_COLLS:
        c = pre + c
        if since:
            field = "updated_at" if c.endswith("posts") else "created_at"
            lines.append("- `ArtifactData` `query`, `url` %s, `collection: \"%s\"`, `query: {where: [[\"%s\", \">\", \"%s\"]], "
                         "limit: 1000}`, `out_dir: \"%s\"`." % (board, c, field, since, d))
        else:
            lines.append("- `ArtifactData` `list`, `url` %s, `collection: \"%s\"`, `query: {limit: 1000}`, `out_dir: \"%s\"`; "
                         "while it returns a `next_cursor`, again with `query.cursor` set to it." % (board, c, d))
    # CL30: the map of the files already in the Artifact ({path: /_blob/<id>}), so `load` puts the pictures the posts
    # name in the local copy from this repo's own files, without downloading them
    lines.append("- `ArtifactData` `get`, `url` %s, `collection: \"%skit_docs\"`, `doc_id: \"_index\"`, `out_dir: \"%s\"` "
                 "(the files' map; \"No document\" is fine)." % (board, pre, d))
    if not pre:
        # CL24: the boards attached to this one (repos/<id>/... in the same Artifact) come with the same pull, so the
        # kit way covers them too (the orchestrator guard refuses a raw list of repos/<id>/posts|comments)
        lines.append("- `ArtifactData` `get`, `url` %s, `collection: \"kit_repos\"`, `doc_id: \"_index\"`, `out_dir: \"%s\"` "
                     "(the attached boards)." % (board, d))
        for rid in attached:
            for c in ART_COLLS:
                c = "repos/%s/%s" % (rid, c)
                if since:
                    field = "updated_at" if c.endswith("posts") else "created_at"
                    lines.append("- `ArtifactData` `query`, `url` %s, `collection: \"%s\"`, `query: {where: [[\"%s\", \">\", "
                                 "\"%s\"]], limit: 1000}`, `out_dir: \"%s\"`." % (board, c, field, since, d))
                else:
                    lines.append("- `ArtifactData` `list`, `url` %s, `collection: \"%s\"`, `query: {limit: 1000}`, "
                                 "`out_dir: \"%s\"`; while it returns a `next_cursor`, again with `query.cursor` set to it."
                                 % (board, c, d))
    if since:
        lines.append("(A post changed on the page without a new `updated_at`, or a comment edited later, comes with "
                     "`kit.py artifact pull --full`: do it once a day or when something looks stale.)")
    lines += ["", "Then: `python3 -I .kit/kit.py artifact load` (starts the local board if needed; your own unsent changes stay)."]
    return "\n".join(lines) + "\n"


def _ensure_local_board(url):
    try:
        _board_request(url, "health")
        return True
    except Exception:
        pass
    wt = os.path.join(ROOT, ".kit", "web_takeover.py")
    if os.path.isfile(wt):
        spec = importlib.util.spec_from_file_location("kit_web_takeover", wt)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.start_board()
    try:
        _board_request(url, "health")
        return True
    except Exception:
        return False


def _merge_fields(remote, local, base):
    """Remote, with every field this copy changed since the base laid over it (a field-level three-way merge)."""
    out = dict(remote or {})
    for k, v in (local or {}).items():
        if (base or {}).get(k) != v:
            out[k] = v
    return out


_MISSING = object()
_ROW_KEYS = ("key", "id", "part", "post", "label", "name")
_STAMPS = ("updated_at", "created_at", "at_utc")


def _row_key(row):
    if not isinstance(row, dict):
        return None
    k = tuple((f, json.dumps(row[f], sort_keys=True)) for f in _ROW_KEYS if f in row)
    return k or None


def _keyed(rows):
    """{key: row} for a list of dicts each with its own key (None when a row has none or two share one)."""
    if not isinstance(rows, list) or not rows:
        return None
    out = {}
    for r in rows:
        k = _row_key(r)
        if k is None or k in out:
            return None
        out[k] = r
    return out


def _merge_deep(live, here, base, name="", lost=None, path=""):
    """CL31: a card (posts/_dashboard, posts/_trains) three-way: what this copy changed since the base, laid into the
    live card, field by field and row by row (rows matched by their key: key/part+post/...). What the live card gained
    since the base (a row's store list, a hand-written note) is kept; a value both sides changed keeps the live one,
    except a timestamp, which keeps the later."""
    if here == base:
        return live
    if live == base or live is _MISSING:
        return here
    if here == live:
        return here
    if isinstance(live, dict) and isinstance(here, dict):
        b = base if isinstance(base, dict) else {}
        out = {}
        for k in list(live) + [k for k in here if k not in live]:
            v = _merge_deep(live.get(k, _MISSING), here.get(k, _MISSING), b.get(k, _MISSING), k, lost,
                            "%s.%s" % (path, k) if path else str(k))
            if v is not _MISSING:
                out[k] = v
        return out
    if isinstance(live, list) and isinstance(here, list):
        kl, kh = _keyed(live), _keyed(here)
        kb = _keyed(base) if isinstance(base, list) and base else {}
        if kl is not None and kh is not None and kb is not None:
            out = []
            for k, row in kl.items():                      # the live order, then the rows added here
                v = _merge_deep(row, kh.get(k, _MISSING), kb.get(k, _MISSING), "", lost,
                                "%s[%s]" % (path, ",".join(x[1] for x in k)))
                if v is not _MISSING:
                    out.append(v)
            out += [row for k, row in kh.items() if k not in kl and k not in kb]
            return out
    if name in _STAMPS and isinstance(live, str) and isinstance(here, str):
        return max(live, here)
    if isinstance(live, list) and isinstance(here, list) and lost is not None:
        # O53: rows without their own key (or two with one) cannot be merged row by row: this copy's change to
        # them would be dropped without a word; said, so the pin refuses and load keeps this copy
        lost.append(path or name or "(the document)")
    return live


def _is_card(did):
    return str(did).startswith("_")


def _card_older(here, other):
    """CL31 (O53: Harmonize's local board seeded its cards at start, older than the live ones): a card here whose
    updated_at is before the other copy's is never sent over it."""
    a, b = (here or {}).get("updated_at"), (other or {}).get("updated_at")
    return isinstance(a, str) and isinstance(b, str) and a < b


def _merge_doc(did, remote, local, base, lost=None):
    """The local document laid over the remote one: cards deep (CL31), posts and comments by field. `lost` gets the
    paths of the lists whose changes here could not be merged (rows without a key)."""
    if _is_card(did):
        if _card_older(local, remote):
            return remote                      # an older (or seeded) card here: the live one stands
        return _merge_deep(remote, local, base, lost=lost)
    return _merge_fields(remote, local, base)


def artifact_load(url=None, token=None, in_dir=None):
    url = url or local_board_url()
    if not _ensure_local_board(url):
        raise Conflict("no local board at %s (run `python3 -I .kit/web_takeover.py` first)" % url)
    token = _resolve_board_token(token)            # after the start: a first start writes the token
    in_dir = in_dir or os.path.join(_art_dir(), "in")
    st = _art_state()
    taken = kept = merged = 0
    unmerged = []
    pre = _art_prefix()
    for coll in ART_COLLS:
        # ArtifactData's out_dir writes <out_dir>/<collection path>/<doc_id>.json: under the prefix for an attached board
        for f in sorted(glob.glob(os.path.join(in_dir, *(pre + coll).split("/"), "*.json"))):
            did = os.path.basename(f)[:-5]
            remote = _read_json(f)
            if not isinstance(remote, dict):
                continue
            base = _read_json(_base_path(coll, did))
            d = _board_request(url, "doc/%s/%s" % (coll, urllib.parse.quote(did, safe="")), token=token)
            local = d["data"] if d.get("exists") else None
            if local is not None and base is not None and local != base:       # changed here since the last sync
                lost = []
                new = _merge_doc(did, remote, local, base, lost) if remote != base else local
                if lost:                       # O53: not mergeable: this copy and its base stay, the pin will refuse
                    unmerged.append("%s/%s: %s" % (coll, did, ", ".join(lost)))
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                    continue
                merged += new != local
                kept += new == local
            else:
                new = remote
                taken += local != remote
            if new != local:
                _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did, "data": new,
                                                             "if_version": d.get("version", 0)}]}, token=token)
            os.makedirs(os.path.dirname(_base_path(coll, did)), exist_ok=True)
            write_doc(_base_path(coll, did), remote)
            # CL30: a pulled file is loaded once; left there, a later load without a pull took that OLD copy over
            # this copy's newer post (and its base), as on 11-23 after a container restart
            try:
                os.remove(f)
            except OSError:
                pass
    # what this copy held that the Artifact does not (a seed from git, an old copy): never sent unless changed here
    stale = set(st.get("not_there") or [])
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            key = "%s/%s" % (coll, row["id"])
            if not os.path.exists(_base_path(coll, row["id"])) and key not in st["versions"]:
                stale.add(key)
                os.makedirs(os.path.dirname(_base_path(coll, row["id"])), exist_ok=True)
                write_doc(_base_path(coll, row["id"]), row.get("data") or {})
    st["not_there"] = sorted(stale)
    st["loaded_at"] = zstamp(datetime.now(timezone.utc))
    _art_save(st)
    restored, missing = _load_blobs(url, token, in_dir)
    attached = _load_attached(url, token, in_dir)
    return {"taken": taken, "kept_here": kept, "merged": merged, "not_there": len(stale), "blobs_restored": restored,
            "blobs_missing": missing, "unmerged": unmerged, "attached": attached}


def _load_attached(url, token, in_dir):
    """CL32: the Artifact's repos/<id>/ (another repo's board attached to this one) into that board's store on the
    local board server (attached as .kit/attached/<id> when this container has no clone of it), so `kit.py launch`
    and `resume` find its posts. Read-only here: never pushed from this copy. {id: documents loaded}."""
    own = _art_prefix().strip("/")
    out = {}
    for d in sorted(glob.glob(os.path.join(in_dir, "repos", "*"))):
        rid = os.path.basename(d)
        if not os.path.isdir(d) or ("repos/%s" % rid) == own or rid == "all":
            continue
        files = [(coll, f) for coll in ART_COLLS for f in sorted(glob.glob(os.path.join(d, coll, "*.json")))]
        if not files:
            continue
        try:
            _board_request(url, "attach", {"id": rid}, token=token)
        except Exception:
            out[rid] = "not attached (the local board server predates CL32: restart it)"
            continue
        n = 0
        for coll, f in files:
            doc = _read_json(f)
            if isinstance(doc, dict) and isinstance(doc.get("data"), dict) and "id" in doc:
                doc = doc["data"]
            if not isinstance(doc, dict):
                continue
            did = os.path.basename(f)[:-5]
            try:
                _board_request(url, "doc/%s/%s?repo=%s" % (coll, urllib.parse.quote(did, safe=""), urllib.parse.quote(rid)),
                               {"data": doc}, token=token, method="PUT")
                n += 1
            except Exception:
                continue
        out[rid] = n
    return out


BLOB_RE = re.compile(r"/_blob/([0-9a-f]{32})\b")
BLOB_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
              ".svg": "image/svg+xml", ".pdf": "application/pdf", ".mp4": "video/mp4", ".webm": "video/webm"}


def _local_blob(url, bid, token):
    try:
        req = urllib.request.Request(url.rstrip("/") + "/_blob/" + bid, headers={"X-Kit-Token": token or "", "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:  # noqa: BLE001 - 404 or no server: not here
        return False


def _put_blob(url, bid, raw, name, token):
    req = urllib.request.Request(url.rstrip("/") + "/_api/blobs", data=raw, method="POST", headers={
        "X-Kit-Token": token or "", "X-Blob-Id": bid, "X-Filename": urllib.parse.quote(name),
        "Content-Type": BLOB_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")})
    with urllib.request.urlopen(req, timeout=30) as res:
        meta = json.load(res)
    if meta.get("id") != bid:          # a board server older than CL30 saves it under a new id: not restored
        raise Conflict("the local board server kept no blob id (restart it with the current kit)")
    return meta


def _load_blobs(url, token, in_dir):
    """CL30 (Harmonize's parity check: a local board from git showed none of the Artifact's pictures, /_blob 404): every
    /_blob/<id> the posts and comments here name is put in the local blob store, from the file `Artifact` `read`
    saved (<in>/blobs/<id>.<ext>) or from this repo's file the Artifact's map names; the rest are returned, for the
    read calls `artifact_blobs_text` prints."""
    ids = []
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            for bid in BLOB_RE.findall(json.dumps(row.get("data") or {})):
                if bid not in ids:
                    ids.append(bid)
    if not ids:
        return 0, []
    by_id = {}
    idx = _read_json(os.path.join(in_dir, *(_art_prefix() + "kit_docs").split("/"), "_index.json")) or {}
    idx = idx.get("data") if isinstance(idx.get("data"), dict) else idx
    kas = _kas()
    fst = kas.load_files_state(ROOT, _art_into()) if kas else {"assets": {}}
    amap = dict((idx.get("assets") or {}) if isinstance(idx, dict) else {})
    amap.update({k: v.get("url") for k, v in (fst.get("assets") or {}).items() if isinstance(v, dict) and v.get("url")})
    for path, u in amap.items():
        m = BLOB_RE.search(str(u or ""))
        if m:
            by_id[m.group(1)] = path
    restored, missing = 0, []
    for bid in ids:
        if _local_blob(url, bid, token):
            continue
        got = sorted(glob.glob(os.path.join(in_dir, "blobs", bid + ".*")) + glob.glob(os.path.join(in_dir, "blobs", bid)))
        src = got[0] if got else (os.path.join(ROOT, by_id[bid]) if bid in by_id else "")
        if src and os.path.isfile(src):
            try:
                _put_blob(url, bid, open(src, "rb").read(), os.path.basename(by_id.get(bid) or src), token)
                restored += 1
                continue
            except Exception:  # noqa: BLE001 - counted missing, named below
                pass
        missing.append(bid)
    return restored, missing


def artifact_blobs_text(r):
    """The `Artifact` `read` calls that bring the pictures this copy still lacks, then load again."""
    miss = r.get("blobs_missing") or []
    if not miss:
        return ""
    d = os.path.join(_art_dir(), "in", "blobs")
    lines = ["%d picture(s) the posts name are not in this copy yet: for each, one `Artifact` `read` (one asset a call, "
             "its id as `path`; it saves <id>.<ext>); then `python3 -I .kit/kit.py artifact load` again:" % len(miss)]
    for bid in miss:
        lines.append("- `Artifact` `read` %s" % json.dumps({"url": artifact_url(), "path": bid, "out_dir": d}))
    return "\n".join(lines) + "\n"


def stale_copy():
    """CL30: '' when the scripts may read the local copy, else why not (an Artifact board whose copy was never loaded,
    or loaded longer ago than kit.artifact.fresh_min, 15). $KIT_STALE_OK=1 reads it anyway."""
    if not attached_board() or os.environ.get("KIT_STALE_OK"):
        return ""
    k = _kit_config().get("kit") or {}
    try:
        cap = float(((k.get("artifact") if isinstance(k.get("artifact"), dict) else {}) or {}).get("fresh_min", 15))
    except (TypeError, ValueError):
        cap = 15.0
    at = str(_art_state().get("loaded_at") or "")
    way = ("run `python3 -I .kit/kit.py artifact pull`, make the ArtifactData calls it prints, then `python3 -I "
           ".kit/kit.py artifact load`; then this command again (KIT_STALE_OK=1 reads the old copy anyway)")
    if not at:
        return "STALE: this copy of the Artifact board (%s) was never loaded here: %s." % (artifact_url(), way)
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               ).total_seconds() / 60.0
    except ValueError:
        age = cap + 1
    if age <= cap:
        return ""
    return "STALE: this copy of the Artifact board was loaded %d min ago (more than %d): %s." % (age, cap, way)


def artifact_unsent(url=None, token=None):
    """CL30: how many documents of the local copy differ from what the Artifact last had, and the batch files built
    but never recorded sent: {"changed": n, "batches": [paths]}."""
    url = url or local_board_url()
    token = _resolve_board_token(token)
    n = 0
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            base = _read_json(_base_path(coll, row["id"]))
            data = row.get("data") or {}
            if base != data and not (_is_card(row["id"]) and base is not None and _card_older(data, base)):
                n += 1                         # CL31: an older card here is dropped at push, not unsent
    return {"changed": n, "batches": sorted(glob.glob(os.path.join(_art_dir(), "out", "batch-*.json")))}


def launch_mark(pid, prompt):
    """CL30: the mark `kit.py launch --web` leaves for the orchestrator guard (this part's create_session may go out);
    prompt None removes it (the second half, --session-id, ran)."""
    p = os.path.join(ROOT, ".kit", "run", "launch", pid + ".json")
    if prompt is None:
        try:
            os.remove(p)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    write_doc(p, {"prompt": prompt, "at": time.time()})


def artifact_push(url=None, token=None, out_dir=None):
    if attached_board() and not _art_state().get("loaded_at"):
        # CL28's open item: a web part's copy came from git; its push built 78 writes without if_version
        raise Conflict("this copy was never loaded from the Artifact, so a push would overwrite the board with an old "
                       "copy: run `python3 -I .kit/kit.py artifact pull`, the calls it prints, `kit.py artifact load`, "
                       "then push")
    url = url or local_board_url()
    token = _resolve_board_token(token)
    out_dir = out_dir or os.path.join(_art_dir(), "out")
    os.makedirs(os.path.join(out_dir, "docs"), exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "batch-*.json")) + glob.glob(os.path.join(out_dir, "docs", "*.json")):
        os.remove(f)
    st = _art_state()
    entries, need_pin, every, dropped = [], [], [], []
    for coll in ART_COLLS:
        res = _board_request(url, "db/%s" % coll, token=token)
        for row in res.get("docs") or []:
            did, data = row["id"], row.get("data") or {}
            every.append(data)
            base = _read_json(_base_path(coll, did))
            if base == data:
                continue
            key = "%s/%s" % (coll, did)
            if _is_card(did) and base is not None and _card_older(data, base):
                # CL31: an older card here (a seed, a stale rebuild): put the copy the Artifact had back, never send it
                _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did,
                                                             "data": base, "if_version": row.get("version", 0)}]}, token=token)
                dropped.append(key)
                every[-1] = base
                continue
            e = {"op": "set", "collection": _art_prefix() + coll, "doc_id": did}
            if key in (st.get("not_there") or []) and key not in st["versions"]:
                base = None                              # never in the Artifact: a create
            if base is not None or key in st["versions"]:
                if key not in st["versions"]:
                    need_pin.append(key)
                    continue
                e["if_version"] = int(st["versions"][key])
            fp = os.path.join(os.path.abspath(out_dir), "docs", "%04d.json" % len(entries))
            write_doc(fp, data)
            e["file_path"] = fp
            entries.append(e)
    # CL30 (the owner, 10:3x: "push must also send every file a post references"): the files of EVERY post and comment
    # here, not only of those sent now; the documents are mirrored by this push itself (no second command)
    files_text, mirror = _artifact_files(every, os.path.join(out_dir, "files"),
                                         changed=[(e["doc_id"], _read_json(e["file_path"])) for e in entries])
    pending = {}
    for did, ref, sha, data, ver in mirror:
        fp = os.path.join(os.path.abspath(out_dir), "docs", "%04d.json" % len(entries))
        write_doc(fp, data)
        e = {"op": "set", "collection": _art_prefix() + "kit_docs", "doc_id": did, "file_path": fp}
        if ver:
            e["if_version"] = int(ver)
        entries.append(e)
        pending[did] = {"ref": ref, "sha": sha}
    write_doc(os.path.join(out_dir, "kit_docs.json"), pending)
    batches, cur, size = [], [], 0
    for e in entries:
        n = os.path.getsize(e["file_path"]) + 200
        if cur and (len(cur) == 50 or size + n > 900 * 1024):
            batches.append(cur); cur, size = [], 0
        cur.append(e); size += n
    if cur:
        batches.append(cur)
    paths = []
    for i, b in enumerate(batches):
        p = os.path.join(out_dir, "batch-%02d.json" % (i + 1))
        write_doc(p, b)
        paths.append(p)
    return {"batches": paths, "need_pin": need_pin, "writes": len(entries), "board": artifact_url(), "files": files_text,
            "dropped": dropped}


def _kas():
    """kit_artifact_sync.py: the kit's own (kit/claude), an install's (.kit), or the one beside this kit.py."""
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(ROOT, "kit", "claude"), os.path.join(ROOT, ".kit"), os.path.join(os.path.dirname(here), "kit", "claude"), here):
        if os.path.isfile(os.path.join(d, "kit_artifact_sync.py")):
            spec = importlib.util.spec_from_file_location("kit_artifact_sync_files", os.path.join(d, "kit_artifact_sync.py"))
            kas = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(kas)
            return kas
    return None


def _art_into():
    """The attached board's id (repos/<id>/ prefix) for kit_artifact_sync's --into, '' for a board of its own."""
    m = re.match(r"^repos/([^/]+)/$", _art_prefix())
    return m.group(1) if m else ""


def _artifact_files(docs, out, changed=None):
    """CL10 (the owner, 2026-11-23: "All the pictures don't show up here"): the files these posts and comments name that
    the board's viewers open. (text, mirror): the steps for the pictures to upload (`Artifact` `publish` `asset: true`,
    then `kit_artifact_sync.py files-sent`), and CL30, the documents to mirror in this push's own batches:
    [(doc_id, path, sha, document, if_version)]."""
    kas = _kas()
    if kas is None:
        return "", []
    into = _art_into()
    st = kas.load_files_state(ROOT, into)
    # the Artifact's own map (pulled with the board): a file mapped there is not uploaded again from a new container
    idx = _read_json(os.path.join(_art_dir(), "in", *(_art_prefix() + "kit_docs").split("/"), "_index.json")) or {}
    idx = idx.get("data") if isinstance(idx.get("data"), dict) else idx
    for path, u in ((idx.get("assets") or {}) if isinstance(idx, dict) else {}).items():
        full = os.path.join(ROOT, path)
        if path not in st["assets"] and u and os.path.isfile(full):
            st["assets"][path] = {"url": u, "sha": kas.sha256_of(full)}
    plan = kas.plan_files(ROOT, [x for x in docs if isinstance(x, dict)], st)
    mirror = []
    for ref in plan["mirror"]:
        data = kas.mirror_doc(ROOT, ref)
        if data:
            did = kas.doc_id(ref)
            mirror.append((did, ref, kas.sha256_of(os.path.join(ROOT, ref)), data, (st["docs"].get(did) or {}).get("version")))
    plan["mirror"] = []                                 # in the push's batches, not a second step
    if changed is not None:                             # "stays a repo link": said for the posts sent now only
        by = {}                                         # CL33 (O54): each link names the post that cites it
        for did, d in changed:
            if isinstance(d, dict):
                for ref, why in kas.plan_files(ROOT, [d], st)["links"]:
                    by.setdefault((ref, why), []).append(str(d.get("post_id") or did))
        plan["links"] = [[ref, why, ids] for (ref, why), ids in by.items()]
    if not (plan["upload"] or plan["links"]):
        return "", mirror
    os.makedirs(out, exist_ok=True)
    write_doc(os.path.join(out, "plan.json"), plan)
    return kas.plan_text(plan, artifact_url() or "<the board's url>", out, into), mirror


def artifact_push_text(r):
    board = r.get("board") or artifact_url() or "<the board's url>"     # CL32: a launch whose push failed has none
    out = []
    if r.get("files"):
        out.append(r["files"].rstrip("\n") + "\n(The posts' own batches below can go before or after; the page shows a file once it is mapped.)")
    if r.get("dropped"):
        out.append("Not sent, older than the Artifact's copy (put back here): %s." % ", ".join(r["dropped"]))
    if r.get("need_pin"):
        out.append("First, their versions (changed here, never sent from this copy): for each, `ArtifactData` `get`, `url` %s, "
                   "that collection and doc_id, `out_dir: \"%s\"` (its reply names the version N; that file is not "
                   "used), then the same `get` again with `out_dir: \"%s\"` (the pin merges this copy into the live "
                   "document saved there: a newer live card is never written over); then one command: `python3 -I "
                   ".kit/kit.py artifact pin %s` with each N; then push again." % (
                       board, os.path.join(_pin_dir(), "probe"), os.path.join(_pin_dir(), "v<N>"),
                       " ".join("%s=<N>" % k for k in r["need_pin"])))
        pre = _art_prefix()
        if pre:
            out[-1] = out[-1].replace("that collection and doc_id", "collection `%s<collection>` and the doc_id" % pre)
    for p in r.get("batches") or []:
        out.append("`ArtifactData` `batch`, `url` %s, `writes` = the array in %s; when it is committed: "
                   "`python3 -I .kit/kit.py artifact sent %s`." % (board, p, p))
    if not out:
        out.append("Nothing to send: the Artifact has every change made here.")
    out.append("A refused batch names a document's real version N: `ArtifactData` `get` it with `out_dir: \"%s\"`, then "
               "`python3 -I .kit/kit.py artifact pin <collection>/<id>=<N>`, then push again." % os.path.join(_pin_dir(), "v<N>"))
    return "\n".join(out) + "\n"


def _files_sent(batch, batch_path):
    """CL30: the documents a push mirrored (kit_docs), recorded with their version once their batch is sent."""
    docs = [e for e in batch if str(e.get("collection") or "").endswith("kit_docs") and e.get("doc_id") != "_index"]
    kas = _kas() if docs else None
    if not kas:
        return
    pending = _read_json(os.path.join(os.path.dirname(os.path.abspath(batch_path)), "kit_docs.json")) or {}
    into = _art_into()
    fst = kas.load_files_state(ROOT, into)
    for e in docs:
        fst["docs"][e["doc_id"]] = {"version": int(e.get("if_version") or 0) + 1,
                                    "sha": (pending.get(e["doc_id"]) or {}).get("sha", "")}
    p = kas.files_state_path(ROOT, into)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    write_doc(p, fst)


def artifact_sent(batch_path):
    st = _art_state()
    batch = _read_json(batch_path) or []
    _files_sent(batch, batch_path)
    for e in batch:
        if str(e.get("collection") or "").endswith("kit_docs"):
            continue
        coll = _art_key(e["collection"])
        key = "%s/%s" % (coll, e["doc_id"])
        if e.get("op") == "delete":
            st["versions"].pop(key, None)
            continue
        st["versions"][key] = int(e.get("if_version") or 0) + 1
        data = _read_json(e.get("file_path") or "")
        if isinstance(data, dict):
            os.makedirs(os.path.dirname(_base_path(coll, e["doc_id"])), exist_ok=True)
            write_doc(_base_path(coll, e["doc_id"]), data)
    _art_save(st)
    try:
        os.remove(batch_path)
    except OSError:
        pass
    return len(batch)


def _pin_dir():
    return os.path.join(_art_dir(), "pin")


def _pinned_live(coll, did, ver):
    """CL31: the live document the `get` before a pin saved in this project's pin directory (never a shared one), in
    the folder of the version the pin names (O53: `ArtifactData` `out_dir` saves the bare data, so the version is
    tied to the file by its folder, v<version>/), its file and the version the file itself names (None when bare);
    (None, None, None). A get made after a probe that named N can only be newer than N: its pin at N is refused by
    the Artifact, never written over newer content."""
    pre = _art_prefix()
    for c in dict.fromkeys((pre + coll, coll)):
        f = os.path.join(_pin_dir(), "v%s" % ver, *c.split("/"), did + ".json")
        doc = _read_json(f)
        if isinstance(doc, dict):
            ver = None
            if isinstance(doc.get("data"), dict) and ("version" in doc or "doc_id" in doc or len(doc) <= 3):
                ver = doc.get("version")
                doc = doc["data"]
            return doc, f, ver
    return None, None, None


PIN_FRESH_S = 30 * 60


def artifact_pin(pairs, url=None, token=None, report=None):
    """A document's version, for the next push. CL31: the version a fresh `get` names belongs to the live copy that get
    saved (in _pin_dir()): when that copy is not this copy's base (the live document changed since), this copy's
    changes are merged into it first, so the push never writes over what the live copy gained (a card's store lists,
    hand-written notes). A pin of a document this copy changed, without that live copy, is refused."""
    st = _art_state()
    n = 0
    report = report if report is not None else []
    for p in pairs:
        k, _, v = p.partition("=")
        k = _art_key(k)
        if k.count("/") == 1 and not k.startswith("kit_docs/") and v.strip().isdigit():
            coll, did = k.split("/")
            base = _read_json(_base_path(coll, did))
            live, f, fver = _pinned_live(coll, did, v.strip())
            if live is not None and fver is not None and str(fver) != v.strip():
                report.append("%s: not pinned: the saved live copy is version %s, the pin names %s: `get` it again"
                              % (k, fver, v.strip()))
                continue
            if live is not None and time.time() - os.path.getmtime(f) > PIN_FRESH_S:
                report.append("%s: not pinned: the saved live copy is older than %d min (%s): `get` it again"
                              % (k, PIN_FRESH_S // 60, f))
                continue
            if live is None:
                if base is not None:
                    report.append("%s: not pinned: no live copy at version %s in %s (`ArtifactData` `get` it with that "
                                  "`out_dir` first, then pin again): a pin without it would write this copy over the live "
                                  "one" % (k, v.strip(), os.path.join(_pin_dir(), "v" + v.strip())))
                    continue
            elif live != base:
                u = url or local_board_url()
                tok = _resolve_board_token(token)
                d = _board_request(u, "doc/%s/%s" % (coll, urllib.parse.quote(did, safe="")), token=tok)
                here = d["data"] if d.get("exists") else None
                lost = []
                new = live if here is None else _merge_doc(did, live, here, base, lost)
                if lost:
                    report.append("%s: not pinned: this copy changed rows the live copy changed too, and they have no "
                                  "key to merge them by (%s): rebuild it from the live copy (kit-cards) or merge by hand"
                                  % (k, ", ".join(lost)))
                    continue
                if new != here:
                    _board_request(u, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did, "data": new,
                                                               "if_version": d.get("version", 0)}]}, token=tok)
                os.makedirs(os.path.dirname(_base_path(coll, did)), exist_ok=True)
                write_doc(_base_path(coll, did), live)
                report.append("%s: the live copy was newer: this copy's changes merged into it" % k)
            if f:
                try:
                    os.remove(f)                   # used once: a later pin needs a fresh get
                except OSError:
                    pass
        if k.startswith("kit_docs/") and v.strip().isdigit():
            # CL30: a mirrored document's version lives with the files state (a refused push names it)
            kas = _kas()
            if kas:
                fst = kas.load_files_state(ROOT, _art_into())
                fst["docs"].setdefault(k[len("kit_docs/"):], {})["version"] = int(v)
                fst["docs"][k[len("kit_docs/"):]]["sha"] = ""          # its content is unknown: sent again
                fp = kas.files_state_path(ROOT, _art_into())
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                write_doc(fp, fst)
                n += 1
            continue
        if k.count("/") == 1 and v.strip().isdigit():
            st["versions"][k] = int(v)
            n += 1
    _art_save(st)
    return n

# ---------------------------------------------------------------- runtime welcomes
WELCOME_RUNTIMES = ("agy", "claude-cli", "claude", "arena", "generic")

_RUNTIME_LABELS = {
    "agy": "agy local",
    "claude-cli": "Claude Code CLI local",
    "claude": "Claude Code Web",
    "arena": "Arena / embedded preview",
    "generic": "local CLI",
}


def welcome_comment_id(runtime):
    if runtime not in WELCOME_RUNTIMES:
        raise ValueError("Unsupported runtime %r (expected one of %s)" % (runtime, ", ".join(WELCOME_RUNTIMES)))
    return "welcome-%s-1" % runtime


def _resolve_memory_dir(explicit=None):
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(ROOT, explicit)
    cfg_md = (CONFIG.get("kit") or {}).get("memory_dir")
    if cfg_md:
        return cfg_md if os.path.isabs(cfg_md) else os.path.join(ROOT, cfg_md)
    for cand in (".board/memory", ".story"):
        p = os.path.join(ROOT, cand)
        if os.path.isdir(p):
            return p
    return os.path.join(ROOT, ".board", "memory")


def _read_welcome_memory(memory_dir):
    """Read state/*.md (title, priority, Next:, Blocked by:, Runs in:) and awaits.md bullets."""
    projects = []
    state_dir = os.path.join(memory_dir, "state")
    if os.path.isdir(state_dir):
        for path in sorted(glob.glob(os.path.join(state_dir, "*.md"))):
            wid = os.path.basename(path)[:-3]
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), wid)
            nxt = next((line[5:].strip() for line in text.splitlines() if line.startswith("Next:")), "")
            pm = re.search(r"^Priority:\s*([123])", text, re.M)
            prio = int(pm.group(1)) if pm else 2
            bm = re.search(r"^Blocked by:\s*(.+)$", text, re.M | re.I)
            blockers = [b.strip() for b in bm.group(1).split(",") if b.strip() and b.strip().lower() != "none"] if bm else []
            rm = re.search(r"^Runs in:\s*(.+)$", text, re.M | re.I)
            runner = rm.group(1).strip() if rm else ""
            projects.append({
                "id": wid,
                "title": title,
                "next": nxt,
                "priority": prio,
                "blocked_by": blockers,
                "runner": runner,
            })
    projects.sort(key=lambda p: (p["priority"], p["id"]))

    awaits = []
    awaits_ids = set()
    awaits_path = os.path.join(memory_dir, "awaits.md")
    if os.path.isfile(awaits_path):
        try:
            with open(awaits_path, encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith("- ") or s.startswith("* "):
                        item = s[2:].strip()
                        if item:
                            awaits.append(item)
                            m_id = re.match(r"^\[([^\]]+)\]", item)
                            if m_id:
                                awaits_ids.add(m_id.group(1).strip())
        except OSError:
            pass
    return projects, awaits, awaits_ids


def _runtime_capability_lines(runtime, board_url, cmd, mem_rel):
    if runtime == "agy":
        return [
            "- **Local board + phone access:** served by `board_server.py` at `%s` with Tailscale/PWA access (Android Private DNS / NextDNS users: use the direct Tailscale IP link if `*.ts.net` does not resolve)." % board_url,
            "- **One-word launcher:** type `%s` in any terminal to start the board, print your link, and launch `agy --mode auto` with `kit_guard.py` (unattended mode — destructive commands stay blocked)." % cmd,
            "- **Live forwarding & parallel workers:** a `flash_lite` clerk auto-forwards your comments, Likes, and tapped answers; parallel git-worktree subagents (`Workspace=\"share\"`) isolate each part's edits.",
            "- **Warm Git sync:** `kit.py receive` / `kit.py publish` sync board state cleanly with other runtimes.",
        ]
    if runtime == "claude-cli":
        return [
            "- **Local board + phone access:** served by `board_server.py` at `%s` with Tailscale/PWA access (Android Private DNS / NextDNS users: use the direct Tailscale IP link if `*.ts.net` does not resolve)." % board_url,
            "- **One-word launcher:** type `%s` in any terminal to start the board, print your link, and open Claude Code (`claude --permission-mode auto`) as the orchestrator, with `kit_guard.py` guarding every command (destructive commands stay blocked); `%s --agy` opens agy instead." % (cmd, cmd),
            "- **Live forwarding & parallel workers:** a background listener wakes the orchestrator (0 tokens while waiting) on your comments, 👍 Likes, and tapped answers and passes them to the part that wrote the post; parts run as subagents in their own git worktrees.",
            "- **Warm Git sync:** `kit.py receive` / `kit.py publish` sync board state cleanly with other runtimes.",
        ]
    if runtime == "claude":
        return [
            "- **Cloud Artifact board:** hosted on `claude.ai` (`%s`) with `window.storage` persistence." % board_url,
            "- **Auto-wakeup on comment:** comments and Likes wake the clerk/orchestrator automatically.",
            "- **Cloud subagents & hybrid bridge:** remote subagents run parts in parallel, and the `board-mirror` Git bridge (`kit.py sync` / `git_watch.py`) connects local computer runners.",
        ]
    if runtime == "arena":
        return [
            "- **Embedded preview mode:** runs `board_server.py --preview --embed-mode` (`KIT_EMBED_PREVIEW=1`) at `%s` with zero-config sandbox proxy binding, auto-seeded `_orchestrator`, cookie-independent `X-Kit-Token` unlock, 5s change polling, and iframe-safe Copy Markdown / `/_api/export` Share fallback." % board_url,
            "- **30-min live listener + Pull-on-turn (no background clerk / no auto-wake across turns):** during an active turn, `board_cli.py comments wait --timeout 1750` catches your comments and Likes live for ~29 minutes without needing `.` in chat. Once a turn ends, there is no 0-token background wake — ping the Arena chat (e.g. `.`) whenever you want the agent to read `board_cli.py inbox` and reply.",
            "- **Handoff:** warm Git sync (`kit.py receive` / `kit.py publish`) when a remote is configured, or a downloadable patch/zip when the sandbox has no push credentials.",
        ]
    return [
        "- **Local board server:** served by `board_server.py` at `%s`, driven via `board_cli.py` (manual or polled)." % board_url,
        "- **Memory & handoff:** project state lives in `%s/`, and warm Git sync (`kit.py receive` / `kit.py publish`) shares board state across runtimes." % mem_rel,
    ]


def _runtime_setup_line(runtime):
    if runtime == "agy":
        return "Local `computer` runner is ready out of the box in this `agy` session (clerk active, worktree subagents available)."
    if runtime == "claude-cli":
        return "Local `computer` runner is ready out of the box in this Claude Code session (listener active, worktree subagents available)."
    if runtime == "claude":
        return "`cloud` runner is ready in Claude Code Web; connect a `computer` runner via `board-mirror` + `git_watch.py` if local execution is needed."
    if runtime == "arena":
        return "Embedded preview (`--embed-mode`) is active; pull-on-turn workflow is ready (reply in Arena chat to trigger the next turn)."
    return "Local CLI runner (`board_cli.py`) is ready; poll or run `kit.py` commands between turns."


def render_welcome(runtime=None, project=None, board_url=None, memory_dir=None, cmd=None, talk=None):
    """Render a two-layer (universal + runtime-specific) welcome message for _orchestrator."""
    rt = runtime or (CONFIG.get("kit") or {}).get("runtime") or "agy"
    if rt in ("both", "local"):
        rt = "agy"
    if rt not in WELCOME_RUNTIMES:
        raise ValueError("Unsupported runtime %r (expected one of %s)" % (rt, ", ".join(WELCOME_RUNTIMES)))
    proj = project or CONFIG.get("project") or os.path.basename(ROOT)
    url = _resolve_board_url(board_url)
    mdir = _resolve_memory_dir(memory_dir)
    mem_rel = os.path.relpath(mdir, ROOT) if os.path.commonpath([os.path.abspath(mdir), os.path.abspath(ROOT)]) == os.path.abspath(ROOT) else mdir
    launcher = cmd or (CONFIG.get("kit") or {}).get("cmd") or "./.kit/up"
    projects, awaits, awaits_ids = _read_welcome_memory(mdir)

    lines = [
        "# Welcome to the %s board (%s)" % (proj, _RUNTIME_LABELS[rt]),
        "",
        "## 1. What the board does",
        "- Columns: **`todo`** (for you to do), **`question`** (choices for you), **`info`** (reports & news), and **`done`** (finished).",
        "- Commenting under any post reaches the agent that wrote it and replies come back in that thread; `question` posts support one-tap answers; priorities run `P1`–`P3`.",
        "- The **\"Where we are\"** card at the top tracks every workstream, and this **`_orchestrator`** thread (\"Ask the orchestrator\") is for anything not tied to one post.",
        "",
        "## 2. How this runtime works (`%s`)" % rt,
    ]
    lines.extend(_runtime_capability_lines(rt, url, launcher, mem_rel))

    lines.extend(["", "## 3. Everything open now"])
    if projects:
        for p in projects:
            nxt = p["next"] or "(no next step recorded)"
            extra = " _(blocked by: %s)_" % ", ".join(p["blocked_by"]) if p["blocked_by"] else ""
            lines.append("- **%s** (`P%d`): %s%s" % (p["title"], p["priority"], nxt, extra))
    else:
        lines.append("- No open workstreams recorded in `%s/state/` yet." % mem_rel)
    if awaits:
        lines.append("")
        lines.append("**Waiting on you (`awaits.md`):**")
        for item in awaits:
            lines.append("- %s" % item)

    lines.extend([
        "",
        "## 4. Setup & runners",
        "- " + _runtime_setup_line(rt),
        "",
        "## 5. What can run now",
    ])
    runnable = [p for p in projects if p["next"] and not p["blocked_by"] and p["id"] not in awaits_ids]
    if runnable:
        for p in runnable:
            lines.append("- **%s** (`P%d`): %s" % (p["title"], p["priority"], p["next"]))
    else:
        lines.append("- Ready for your next instruction — tell me which task to start.")
    dl = device_line()
    if dl:
        lines.append("- " + dl + " Past that, steps wait their turn instead of crashing the computer.")
    lines.extend(["", "A Like on this message launches them all; or tell me which."])
    return "\n".join(lines) + "\n"


def post_welcome(runtime=None, url=None, token=None, project=None, memory_dir=None, cmd=None, talk=None, at=None):
    """Render and idempotently post welcome-<runtime>-1 under _orchestrator."""
    rt = runtime or (CONFIG.get("kit") or {}).get("runtime") or "agy"
    if rt in ("both", "local"):
        rt = "agy"
    cid = welcome_comment_id(rt)
    text = render_welcome(runtime=rt, project=project, board_url=url, memory_dir=memory_dir, cmd=cmd, talk=talk)
    burl = _resolve_board_url(url)
    tok = _resolve_board_token(token)
    existing = _board_request(burl, "doc/comments/" + urllib.parse.quote(cid, safe=""), token=tok)
    if existing.get("exists"):
        return {"id": cid, "runtime": rt, "posted": False, "status": "already exists", "version": existing.get("version")}
    doc = {
        "post_id": "_orchestrator",
        "from": "orchestrator",
        "runtime": rt,
        "text": text,
        "created_at": at or zstamp(datetime.now(timezone.utc)),
    }
    try:
        res = _board_request(
            burl,
            "db/comments/" + urllib.parse.quote(cid, safe=""),
            {"data": doc, "if_version": 0},
            token=tok,
            method="PUT",
        )
        return {"id": cid, "runtime": rt, "posted": True, "status": "posted", "version": res.get("version")}
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return {"id": cid, "runtime": rt, "posted": False, "status": "already exists"}
        raise


# ---------------------------------------------------------------- init / commit
def git(m, *args):
    return subprocess.run(["git", "-C", m] + list(args), capture_output=True, text=True)


@contextlib.contextmanager
def _sync_lock(m):
    """Advisory file lock around mirror sync operations to prevent concurrent corruption."""
    if fcntl is None or not os.path.isdir(m):
        yield
        return
    lock_path = os.path.join(m, ".kit-sync.lock")
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def init(m):
    if os.path.exists(os.path.join(m, ".git")):
        br = git(m, "symbolic-ref", "--short", "HEAD").stdout.strip() or BRANCH
        return "init: mirror already exists at %s (branch %s). Next: python3 -I .kit/kit.py sync-db --commit" % (m, br)
    subprocess.run(["git", "-C", ROOT, "fetch", "origin", BRANCH], capture_output=True)
    has = subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "origin/" + BRANCH], capture_output=True).returncode == 0
    if has:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "-B", BRANCH, m, "origin/" + BRANCH], capture_output=True, text=True)
        src = "origin/" + BRANCH
    else:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "--orphan", "-b", BRANCH, m], capture_output=True, text=True)
        src = "new orphan branch " + BRANCH
    if r.returncode != 0:
        return (r.stdout + r.stderr).strip()
    return "init: created worktree %s from %s. Next: python3 -I .kit/kit.py sync-db --commit" % (m, src)


def doctor(m, db_path=None):
    """Verify SQLite integrity, mirror worktree health, sync freshness, and referenced blobs."""
    issues = []
    lines = []
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    blob_dir = os.path.join(os.path.dirname(db), "blobs")
    if os.path.isfile(db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            ic = conn.execute("PRAGMA integrity_check").fetchone()
            ok = bool(ic and str(ic[0]).lower() == "ok")
            lines.append("db: %s (%s)" % (db, "ok" if ok else str(ic)))
            if not ok:
                issues.append("db integrity_check failed: %s" % (ic,))
            counts = dict(conn.execute("SELECT collection, COUNT(*) FROM docs GROUP BY collection").fetchall())
            lines.append("docs: %d posts, %d comments" % (counts.get("posts", 0), counts.get("comments", 0)))
            missing_blobs = []
            for coll, did, raw in conn.execute("SELECT collection, doc_id, data FROM docs WHERE collection IN ('posts','comments')"):
                for bid in set(re.findall(r"/_blob/([a-fA-F0-9]{16,64})", raw or "")):
                    if not os.path.isfile(os.path.join(blob_dir, bid)):
                        missing_blobs.append("%s/%s -> %s" % (coll, did, bid))
            if missing_blobs:
                issues.append("%d missing blob(s)" % len(missing_blobs))
                lines.append("blobs: MISSING %d (%s)" % (len(missing_blobs), ", ".join(missing_blobs[:5])))
            else:
                lines.append("blobs: ok")
        finally:
            conn.close()
    else:
        lines.append("db: not found at %s" % db)
    if os.path.exists(os.path.join(m, ".git")):
        br = git(m, "symbolic-ref", "--short", "HEAD").stdout.strip()
        st = read_state(m)
        last = st.get("last_sync") or "(never)"
        lines.append("mirror: %s (branch %s, last_sync %s)" % (m, br or "?", last))
        if br != BRANCH:
            issues.append("mirror branch is %r, expected %r" % (br, BRANCH))
    else:
        lines.append("mirror: not initialized at %s (run: python3 -I .kit/kit.py init)" % m)
    lines.append("doctor: %s" % ("OK" if not issues else ("%d issue(s): %s" % (len(issues), "; ".join(issues)))))
    return "\n".join(lines), (1 if issues else 0)


def _is_card_blocked(doc, posts):
    """Return True if doc declares blocked_by items that are not yet done in posts."""
    raw = doc.get("blocked_by")
    if not raw:
        return False
    items = [raw] if isinstance(raw, str) else (list(raw) if isinstance(raw, (list, tuple)) else [])
    for b in items:
        s = str(b or "").strip()
        if not s:
            continue
        ref = posts.get(s)
        if not ref:
            # Also match by part id (case-insensitive)
            sl = s.lower()
            ref = next((p for p in posts.values() if str(p.get("part") or "").strip().lower() == sl), None)
        if ref is None or ref.get("column") != "done":
            return True
    return False


def queue_cards(m, runner=None, for_owner=None, db_path=None, unblocked_only=False):
    """List open cards attributed to a runner (e.g. lmarena, claude-chrome) or person/role from SQLite or mirror."""
    rf = (runner or "").strip().lower()
    ff = (for_owner or "").strip().lower()
    posts = {}
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    if os.path.isfile(db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            for did, raw in conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts'"):
                if did.startswith("_"):
                    continue
                try:
                    posts[did] = json.loads(raw)
                except ValueError:
                    pass
        finally:
            conn.close()
    elif os.path.isdir(os.path.join(m, "board", "posts")):
        for did, doc in load_dir(os.path.join(m, "board"), "posts").items():
            if not did.startswith("_"):
                posts[did] = doc
    rows = []
    for pid, d in posts.items():
        if d.get("removed") or d.get("column") == "done":
            continue
        if unblocked_only and _is_card_blocked(d, posts):
            continue
        pr = str(d.get("runner") or "").strip()
        pf = str(d.get("for") or d.get("assignee") or "").strip()
        if rf and pr.lower() != rf:
            continue
        if ff and pf.lower() != ff:
            continue
        if not rf and not ff and not pr and not pf:
            continue
        prio = int(d.get("priority") or 3)
        rows.append((prio, pid, pr, pf, d.get("column") or "todo", str(d.get("title") or "(untitled)"), str(d.get("body") or "")))
    rows.sort(key=lambda x: (x[0], x[1]))
    if not rows:
        who = ("runner=%s" % runner if runner else "") + (" for=%s" % for_owner if for_owner else "")
        return "queue%s: 0 open cards" % ((" (" + who.strip() + ")") if who.strip() else "")
    out = []
    for prio, pid, pr, pf, col, title, body in rows:
        tags = []
        if pr:
            tags.append("@" + pr)
        if pf:
            tags.append("for:" + pf)
        first = body.strip().splitlines()[0][:100] if body.strip() else ""
        out.append("P%d [%s] %s (%s) — %s%s" % (prio, col, pid, " ".join(tags) or "unassigned", title, (" | " + first) if first else ""))
    return "\n".join(out) + "\n(%d card%s)" % (len(rows), "" if len(rows) == 1 else "s")


_AWAIT_POINT = re.compile(r"^- \[([^\]]+)\]")
_SETTLED = re.compile(r"^(done|closed|archived|finished|merged|completed?|shipped|nothing|none|obsolete|abandoned|"
                      r"superseded|dropped)\b", re.I)


def _awaits_points(text):
    """Split an awaits file into blocks [workstream | None | False, [lines]]: a point is its `- ` line and the indented
    lines under it (None: no [workstream] tag); False marks any other line. Harmonize's story checker reads it so."""
    blocks, cur = [], None
    for ln in text.split("\n"):
        if ln.startswith("- "):
            m = _AWAIT_POINT.match(ln)
            cur = [m.group(1) if m else None, [ln]]
            blocks.append(cur)
        elif ln.startswith("  ") and cur is not None:
            cur[1].append(ln)
        else:
            cur = None
            blocks.append([False, [ln]])
    return blocks


def _next_step(text):
    """A state's next step: Harmonize's `## Next step` section (its first line), else the kit's `Next:` line."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("## next step"):
            for x in lines[i + 1:]:
                if x.startswith("## "):
                    break
                if x.strip():
                    return x.strip()
            return ""
    for ln in lines:
        if ln.strip().lower().startswith("next:"):
            return ln.strip()[5:].strip()
    return ""


def _settled(step):
    return bool(_SETTLED.match(re.sub(r"^[\s>*_`\-–—:]+", "", step or "")))


def _memory_checker(base, cfg_kit, root):
    """The project's memory checker run on `root`: `kit.memory_check` in config (a command; {root} becomes the tree
    to check), else a story/memory skill's scripts/check.py (Harmonize: .claude/skills/story/scripts/check.py)."""
    import shlex
    cmd = cfg_kit.get("memory_check")
    if cmd:
        return [a.replace("{root}", root) for a in (shlex.split(cmd) if isinstance(cmd, str) else list(cmd))]
    for rt in (".claude", ".agents", ".agent"):
        for sk in ("story", "memory"):
            p = os.path.join(rt, "skills", sk, "scripts", "check.py")
            if os.path.isfile(os.path.join(base, p)):
                return [sys.executable, "-I", os.path.join(root, p), root]
    return None


def _shadow_tree(base, rel_m, tmp):
    """A copy of the repo at `tmp` for the checker: the memory dir copied, every other entry a symlink to the real one,
    so the plan is checked against the real files without touching them."""
    parts = os.path.normpath(rel_m).split(os.sep)
    src, dst = base, tmp
    for i, part in enumerate(parts):
        for e in os.listdir(src):
            if e != part:
                os.symlink(os.path.join(src, e), os.path.join(dst, e))
        src, dst = os.path.join(src, part), os.path.join(dst, part)
        if i == len(parts) - 1:
            shutil.copytree(src, dst, symlinks=True)
        else:
            os.makedirs(dst)


def _apply_compaction(mdir, ops):
    for op in ops:
        if op[0] == "move":
            os.makedirs(os.path.dirname(os.path.join(mdir, op[2])), exist_ok=True)
            os.replace(os.path.join(mdir, op[1]), os.path.join(mdir, op[2]))
        elif op[0] == "write":
            os.makedirs(os.path.dirname(os.path.join(mdir, op[1])), exist_ok=True)
            with open(os.path.join(mdir, op[1]), "w", encoding="utf-8") as fh:
                fh.write(op[2])


# ---------------------------------------------------------------- CL33: open work goes to the board before it leaves memory
_DEFAULT = re.compile(r"\bDefault:\s*(.*?)(?:\s+·\s+deadline\s+(\d{4}-\d\d-\d\d))?\s*$", re.S)
_DEADLINE = re.compile(r"·\s*deadline\s+(\d{4}-\d\d-\d\d)\s*$")


def _norm_text(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


class _LocalBoard:
    """The board compact posts to: this repo's board through board_cli (the local copy on an Artifact board, sent
    by `kit.py artifact push`)."""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.artifact = bool(attached_board())

    def posts(self):
        return _all_posts(self.db_path)

    def write(self, pid, doc):
        return _board_write("set", "posts", pid, doc)

    def comment(self, cid, doc):
        return _board_write("set", "comments", cid, doc)

    def has_comment(self, cid):
        return cid in _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=self.db_path)[1]

    def sent(self, pid):
        if not self.artifact:
            return True
        posts, comments = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=self.db_path)
        if pid in comments:
            return _read_json(_base_path("comments", pid)) == comments.get(pid)
        return _read_json(_base_path("posts", pid)) == posts.get(pid)

    def push_hint(self):
        return ("`python3 -I .kit/kit.py artifact push`, the ArtifactData batches it prints, `kit.py artifact sent "
                "<batch>`") if self.artifact else ""


def _await_point(lines, mem_rel, fname, today, since=None):
    """One awaits point -> its question post (CL33): its text, default (the one-tap answer), deadline, workstream,
    the date the point was written (`since`), and for an OVERDUE point that the default applies and where.
    The workstream is a field, never the part or train: as a part it made pseudo-trains and false 'blocks' (O54)."""
    import hashlib
    s = " ".join(x.strip() for x in lines)
    m = _AWAIT_POINT.match(s)
    ws = m.group(1).strip() if m else ""
    rest = s[m.end():].strip() if m else s[2:].strip()
    md = _DEFAULT.search(rest)
    text = rest[:md.start()].strip() if md else rest
    default = (md.group(1).strip() if md else "")
    dl = _DEADLINE.search(rest)
    deadline = dl.group(1) if dl else ""
    if not md and dl:
        text = rest[:dl.start()].strip()
    ref = ""
    mref = re.search(r"\s+[\u2014\u2013]\s+(.*)$", default)   # its trailing reference: "— board E11-window", "— `docs/..` §"
    if mref:
        default, ref = default[:mref.start()].strip(), mref.group(1).strip()
    overdue = bool(deadline and deadline < today)
    where = "%s/state/%s.md" % (mem_rel, ws) if ws else "%s/%s" % (mem_rel, fname)
    body = [text, "", "Default: %s · deadline %s%s" % (default or "(none written)", deadline or "(none)",
                                                      " · workstream %s" % ws if ws else "")]
    if overdue:
        body += ["", "**OVERDUE** (deadline %s passed): the default applies (%s); record it where it acts: `%s`."
                 % (deadline, default or "none written", where)]
    if ref:
        body += ["", "Reference: %s" % ref]
    if not since:
        body += ["", "Written: date unknown (no commit dates this line in this clone)."]
    body += ["", "The point as written in `%s/%s` (archived by `kit.py compact`; answer here):" % (mem_rel, fname),
             "", "> " + s]
    now = _now_z()
    title = text or s
    if len(title) > 200:
        title = title[:200].rsplit(" ", 1)[0]
        if title.count("(") > title.count(")"):
            title = title[:title.rfind("(")].rstrip()
        title += "\u2026"
    doc = {"title": title, "kind": "question", "column": "question", "status": "open",
           "workstream": ws, "default": default, "deadline": deadline, "body": "\n".join(body),
           "source": "%s/%s" % (mem_rel, fname), "source_key": hashlib.sha1(_norm_text(s).encode()).hexdigest()[:12],
           "from": "kit compact", "created_at": since or now, "posted_at": now}
    if not since:
        doc["date_unknown"] = True     # never now, never a date guessed from the text (O54)
    if default:
        doc["default_option"] = default
        doc["options"] = [default, "Something else (reply under this post)"]
    if overdue:
        doc["overdue"] = True
    pid = "aw-%s-%s" % (_slug(ws or "point", 16), _slug(text, 32))
    return pid, doc, s


_STOP = set("the a an of in on at to for and or is are be it its this that with by from as do does did not no yes "
            "which what when how who why should can will we he she they them his her our your my me i you yours it's "
            "there here then than but so if into onto out up down over under about also just only".split())


def _sig_words(s):
    return {w for w in _norm_text(s).split() if len(w) >= 3 and w not in _STOP}


def _point_dates(base, path):
    """Each line of an awaits file -> the ISO date its last change was committed (git blame); {} outside git."""
    try:
        r = subprocess.run(["git", "-C", base, "blame", "--line-porcelain", "--", os.path.relpath(path, base)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    # a shallow clone's cut is not when a line was written (O54: every point "17h old"); nor is an uncommitted line
    cut = set()
    try:
        sp = subprocess.run(["git", "-C", base, "rev-parse", "--git-path", "shallow"], capture_output=True, text=True,
                            timeout=10).stdout.strip()
        sp = sp if os.path.isabs(sp) else os.path.join(base, sp)
        if os.path.isfile(sp):
            cut = {x.strip() for x in open(sp, encoding="utf-8") if x.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    out, t, sha = {}, None, ""
    for ln in r.stdout.split("\n"):
        if re.match(r"^[0-9a-f]{40} \d+ \d+", ln):
            sha, t = ln.split()[0], None
        elif ln.startswith("author-time "):
            t = int(ln.split()[1])
        elif ln.startswith("\t"):
            if t is not None and sha not in cut and sha.strip("0"):
                out.setdefault(ln[1:], datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return out


def _issue_post(fn, text, mem_rel):
    lines = text.split("\n")
    title = (lines[0] if lines else fn).lstrip("# ").strip()[:160] or fn
    rest = "\n".join(lines[1:]).strip()
    doc = {"title": title, "kind": "issue", "column": "todo", "status": "open",
           "body": rest + "\n\nFrom `%s/issues/%s`, archived by `kit.py compact`." % (mem_rel, fn),
           "source": "%s/issues/%s" % (mem_rel, fn), "from": "kit compact", "created_at": _now_z(),
           "posted_at": _now_z()}
    m = re.search(r"^status:\s*open\W+(\d{4}-\d\d-\d\d)", text, re.I | re.M)   # the date the issue was opened
    if m:
        doc["created_at"] = m.group(1) + "T00:00:00Z"
    else:
        doc["date_unknown"] = True
    return "issue-%s" % _slug(fn[:-3], 48), doc


def _is_open_question(pid, p):
    """An open question post: kind question or the question column, not done, closed or removed. A step or report
    post a point names is never its match (O54: 9 points matched step posts and were archived with no question)."""
    if pid.startswith("_") or not isinstance(p, dict) or p.get("removed"):
        return False
    if not (p.get("kind") == "question" or p.get("column") == "question"):
        return False
    return p.get("column") != "done" and step_state(p) not in ("done", "closed")


_NAMED_ID = re.compile(r"`([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)`|(?<![\w/.-])([A-Za-z]+\d+[a-z]?(?:-[a-z0-9]+)+|"
                       r"[a-z][a-z0-9]*(?:-[a-z0-9]+){2,})(?![\w/-])")


def _id_words(pid):
    return _sig_words(pid.replace("-", " ").replace("_", " "))


def _match_post(posts, doc, kind, stem=None, text=""):
    """A post already on the board for this point or issue (CL33, never doubled). A point: the same source_key; an
    open question post it names; one whose id or title words (2 or more) are all in a post id the point names
    (`q-look-aids-in-the-night` -> F5-aids-in-the-night); or one whose title words are all in the point. An issue:
    an issue post naming the file or with the same title."""
    nt = _norm_text(doc.get("title"))
    words = _sig_words(text or doc.get("title"))
    for pid, p in posts.items():
        if pid.startswith("_") or not isinstance(p, dict) or p.get("removed"):
            continue
        if doc.get("source_key") and p.get("source_key") == doc["source_key"]:
            return pid
    if kind == "question":
        named = [a or b for a, b in _NAMED_ID.findall(text or "")]
        opens = {k: p for k, p in posts.items() if _is_open_question(k, p)}
        for n in named:
            if n in opens:
                return n
        for n in named:
            nw = _id_words(n)
            for k, p in opens.items():
                for cand in (_id_words(k), _sig_words(p.get("title"))):
                    if len(cand & nw) >= 2 and (cand <= nw or nw <= cand):
                        return k
        for k, p in opens.items():
            if nt and _norm_text(p.get("title")) == nt:
                return k
            tw = _sig_words(p.get("title"))
            if len(tw) >= 2 and tw <= words:
                return k
        return None
    for pid, p in posts.items():
        if pid.startswith("_") or not isinstance(p, dict) or p.get("removed"):
            continue
        if not (p.get("kind") == "issue" or re.match(r"^(issue|iss)[-_]", pid)):
            continue
        if stem and (stem in str(p.get("source") or "") or stem in pid or stem in str(p.get("body") or "")):
            return pid
        if nt and _norm_text(p.get("title")) == nt:
            return pid
    return None


def _possible_doubles(posts, planned):
    """Planned posts that look like a post already there by a looser test (half their significant words shared):
    listed for a look by hand, never posted silently twice."""
    out = []
    for pid, d in planned:
        w = _sig_words(d.get("title"))
        for k, p in posts.items():
            if k.startswith("_") or not isinstance(p, dict) or p.get("removed"):
                continue
            if p.get("kind") not in ("question", "issue") and p.get("column") != "question":
                continue
            for tw in (_sig_words(p.get("title")), _id_words(k)):
                if w and tw and len(w & tw) >= 2 and len(w & tw) * 2 >= min(len(w), len(tw)):
                    break
            else:
                continue
            if True:
                out.append("%s ~ %s" % (pid, k))
                break
    return out


def compact_memory(days=30, dry_run=False, root_dir=None, board=None):
    """Archive into <memory_dir>/archive/ (+ digest.md) only what is settled (CL28, CL29):
    - a workstream state file, but state/orchestrator.md, a state whose next step is not settled (done, closed...), and a
      state whose workstream still has an open point in an awaits file (`awaits*.md`, config `kit.awaits_files`);
      an awaits point is open until it is removed, so it is never moved, and an awaits file is archived only empty;
    - a closed issue, unless a file that stays cites it; lessons past the 15 newest older than `days`.
    The plan is checked first on a shadow copy of the repo by the project's memory checker; red: nothing moves. A dry
    run prints the plan and the checker's result and never touches the tree. An open issue stays, listed."""
    import time
    base = os.path.abspath(root_dir or ROOT)
    cfg_kit = {}
    try:
        with open(os.path.join(base, ".kit", "config.json"), encoding="utf-8") as fh:
            cfg_kit = json.load(fh).get("kit") or {}
    except (OSError, ValueError):
        pass

    def read(p):
        with open(p, encoding="utf-8", errors="ignore") as fh:
            return fh.read()

    if board is None and root_dir is None:
        board = _LocalBoard()
    elif board is False:
        board = None
    rel_m = cfg_kit.get("memory_dir")
    if not rel_m:
        rel_m = ".story" if os.path.isdir(os.path.join(base, ".story")) else ".board/memory"
    mdir = os.path.join(base, rel_m)
    if not os.path.isdir(mdir):
        return "compact: no memory directory at %s" % mdir
    cutoff = time.time() - max(0, int(days)) * 86400
    state_dir = os.path.join(mdir, "state")
    lessons_dir = os.path.join(mdir, "lessons")
    digest_path = os.path.join(mdir, "archive", "digest.md")
    existing_digest = read(digest_path) if os.path.isfile(digest_path) else ""
    # Awaits files: every open point protects its workstream's state
    names = sorted(fn for fn in os.listdir(mdir) if fn.startswith("awaits") and fn.endswith(".md"))
    for n in cfg_kit.get("awaits_files") or []:
        n = os.path.basename(str(n))
        if n not in names and os.path.isfile(os.path.join(mdir, n)):
            names.append(n)
    open_points = {}  # workstream -> count
    n_points = 0
    to_move = []  # (kind, file name, summary)
    points = []  # (awaits file, point lines)
    for n in names:
        blocks = _awaits_points(read(os.path.join(mdir, n)))
        pts = [b for b in blocks if b[0] is not False]
        n_points += len(pts)
        for b in pts:
            points.append((n, b[1]))
            if b[0]:
                open_points[b[0]] = open_points.get(b[0], 0) + 1
        if not pts:
            to_move.append((".", n, "an awaits file with no open point"))
    issues_dir = os.path.join(mdir, "issues")
    open_issue_files = []  # (file name, text)
    if os.path.isdir(issues_dir):
        for fn in sorted(os.listdir(issues_dir)):
            if fn.endswith(".md"):
                t = read(os.path.join(issues_dir, fn))
                if not re.search(r"^status:\s*closed", t, re.I | re.M):
                    open_issue_files.append((fn, t))
    # CL33: every open awaits point and open issue becomes a board post first; then it no longer holds memory
    on_board = False
    planned, matched, post_lines, issue_post, notes = [], [], [], {}, []
    if board is not None and (points or open_issue_files):
        existing = board.posts()
        taken = set(existing)
        today = datetime.now(timezone.utc).date().isoformat()
        dates = {}
        for n, lines in points:
            if n not in dates:
                dates[n] = _point_dates(base, os.path.join(mdir, n))
            pid, doc, s = _await_point(lines, rel_m.replace(os.sep, "/"), n, today, since=dates[n].get(lines[0]))
            hit = _match_post(existing, doc, "question", text=s)
            if hit:
                matched.append(hit)
                if existing[hit].get("from") == "kit compact" or existing[hit].get("source_key") == doc["source_key"]:
                    continue                    # its own post from an earlier run: nothing to add
                # what the point adds (its reference, default, deadline) goes under that post as one comment, never
                # into its body (O54: F5's matched point left its refs and deadline on no post)
                ref = re.search(r"^Reference: (.*)$", doc["body"], re.M)
                note = ["From `%s/%s` (archived by `kit.py compact`), the open point this post answers:" % (
                    rel_m.replace(os.sep, "/"), n), "", "> " + s, "",
                    "Default: %s · deadline %s%s" % (doc.get("default") or "(none written)",
                                                     doc.get("deadline") or "(none)",
                                                     " · reference: " + ref.group(1) if ref else "")]
                if doc.get("overdue"):
                    note += ["", "**OVERDUE**: the default applies."]
                notes.append(("cmt-%s" % doc["source_key"], {"post_id": hit, "text": "\n".join(note),
                                                             "from": "kit compact", "created_at": _now_z(),
                                                             "delivered": False, "read": False}))
                continue
            base_id, k = pid, 2
            while pid in taken:
                pid, k = "%s-%d" % (base_id, k), k + 1
            taken.add(pid)
            planned.append((pid, doc))
            post_lines.append("  - %s: %s%s" % (pid, s[2:][:150], "  [OVERDUE: the default applies]"
                                                 if doc.get("overdue") else ""))
        n_q = len(planned)
        for fn, t in open_issue_files:
            pid, doc = _issue_post(fn, t, rel_m.replace(os.sep, "/"))
            hit = _match_post(existing, doc, "issue", stem=fn[:-3])
            if hit:
                matched.append(hit)
                issue_post[fn] = hit
                continue
            base_id, k = pid, 2
            while pid in taken:
                pid, k = "%s-%d" % (base_id, k), k + 1
            taken.add(pid)
            planned.append((pid, doc))
            issue_post[fn] = pid
            post_lines.append("  - %s: issue %s" % (pid, doc["title"][:150]))
        n_i = len(planned) - n_q
        doubles = _possible_doubles(existing, planned)
        posts_head = ("\n%d question post(s) %s (one per open awaits point), %d issue post(s) %s, %d already on the "
                      "board (matched, not posted again: %s), %d possible double(s) to check by hand%s" % (
                          n_q, "planned" if dry_run else "posted", n_i, "planned" if dry_run else "posted",
                          len(matched), ", ".join(matched) or "none", len(doubles),
                          (": " + "; ".join(doubles)) if doubles else ""))
        on_board = True
        for n in names:
            if any(pn == n for pn, _ in points):
                k = sum(1 for pn, _ in points if pn == n)
                to_move.append((".", n, "%d open point(s), each now a board question post" % k))
        open_points = {}
    else:
        posts_head = ""
    # 1. State files
    kept_states = []
    if os.path.isdir(state_dir):
        for fn in sorted(os.listdir(state_dir)):
            if not fn.endswith(".md") or fn == "orchestrator.md":
                continue
            ws, text = fn[:-3], read(os.path.join(state_dir, fn))
            step = _next_step(text)
            if open_points.get(ws):
                kept_states.append("  - state/%s: %d open awaits point(s)" % (fn, open_points[ws]))
            elif not _settled(step):
                kept_states.append("  - state/%s: next step not settled (%s)" % (fn, (step or "none written")[:70]))
            else:
                to_move.append(("state", fn, (text.splitlines() or [fn])[0].strip()))
    # 2. Aged lessons (keep 15 newest; archive older than `days`)
    if os.path.isdir(lessons_dir):
        lfiles = [os.path.join(lessons_dir, fn) for fn in os.listdir(lessons_dir) if fn.endswith(".md")]
        lfiles.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for fp in lfiles[15:]:
            if os.path.getmtime(fp) < cutoff:
                first = next((ln.strip().lstrip("# ").strip()[:120] for ln in read(fp).splitlines() if ln.strip()), "")
                to_move.append(("lessons", os.path.basename(fp), first or os.path.basename(fp)))
    # 3. Issues: closed ones go, unless a file that stays cites it
    open_issues, closed = [], []
    issue_writes = []  # CL33: an open issue now on the board is marked closed, then archived like a closed one
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if os.path.isdir(issues_dir):
        for fn in sorted(os.listdir(issues_dir)):
            if not fn.endswith(".md"):
                continue
            text = read(os.path.join(issues_dir, fn))
            title = (text.splitlines() or [fn])[0].lstrip("# ").strip()[:120]
            if re.search(r"^status:\s*closed", text, re.I | re.M):
                closed.append((fn, title))
            elif on_board and fn in issue_post:
                line = "Status: closed — %s, posted as board post %s" % (stamp, issue_post[fn])
                new, k = re.subn(r"^status:.*$", line, text, count=1, flags=re.I | re.M)
                if not k:
                    ls = text.split("\n")
                    new = "\n".join(ls[:1] + [line] + ls[1:])
                issue_writes.append(("write", os.path.join("issues", fn), new))
                closed.append((fn, title))
            else:
                open_issues.append("  - issues/%s: %s" % (fn, title))
    # A settled state or closed issue that a staying file cites stays (the checker reports a dead pointer otherwise);
    # repeated until nothing changes, since a state kept so may cite another
    closed_paths = {os.path.join(issues_dir, fn) for fn, _ in closed}
    while True:
        moving = {os.path.normpath(os.path.join(mdir, k, fn)) for k, fn, _ in to_move}
        staying_text = ""
        for r, ds, fs in os.walk(mdir):
            ds[:] = [x for x in ds if os.path.join(r, x) != os.path.join(mdir, "archive")]
            for f in fs:
                p = os.path.normpath(os.path.join(r, f))
                if f.endswith(".md") and p not in moving and p not in closed_paths:
                    staying_text += read(p) + "\n"
        cited = [t for t in to_move if t[0] == "state" and re.search(r"state/%s\b" % re.escape(t[1]), staying_text)]
        if not cited:
            break
        for t in cited:
            to_move.remove(t)
            kept_states.append("  - state/%s: settled, but cited by a file that stays" % t[1])
    kept_cited = []
    for fn, title in closed:
        if fn[:-3] in staying_text:
            kept_cited.append("  - issues/%s: cited by a file that stays" % fn)
        else:
            to_move.append(("issues", fn, title))
    tail = posts_head
    if post_lines:
        tail += ":\n" + "\n".join(post_lines)
    if kept_states:
        tail += "\n%d state(s) stay (open work):\n" % len(kept_states) + "\n".join(kept_states)
    if n_points and not on_board:
        tail += "\n%d open awaits point(s) stay in %s" % (n_points, ", ".join(names))
    if open_issues:
        tail += ("\n%d open issue(s) stay: post each on the board (`kind: \"issue\"`, `status: \"open\"`), then mark the "
                 "file `Status: closed (posted as <post id>)` and compact again:\n" % len(open_issues) + "\n".join(open_issues))
    if kept_cited:
        tail += "\n%d closed issue(s) stay, cited by a file that stays:\n" % len(kept_cited) + "\n".join(kept_cited)
    if not to_move:
        return "compact: nothing settled to archive" + tail
    ops = issue_writes + [("move", os.path.join(k, fn), os.path.join("archive", k, fn)) for k, fn, _ in to_move]
    added = ["- `%s` (%s): %s" % (os.path.normpath(os.path.join(k, fn)), stamp, s) for k, fn, s in to_move
             if os.path.normpath(os.path.join(k, fn)) not in existing_digest]
    if added:
        ops.append(("write", os.path.join("archive", "digest.md"),
                    (existing_digest or "# Archived Memory Digest\n\n") + "\n".join(added) + "\n"))
    plan = "\n".join("  - %s: %s" % (os.path.normpath(os.path.join(k, fn)), s) for k, fn, s in to_move)
    # Check the plan on a shadow copy: the real tree is touched only once the checker is green
    tmp = tempfile.mkdtemp(prefix="kit-compact-")
    try:
        _shadow_tree(base, rel_m, tmp)
        _apply_compaction(os.path.join(tmp, rel_m), ops)
        cmd = _memory_checker(base, cfg_kit, tmp)
        if cmd:
            try:
                r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=300)
                ok, res = r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip().replace(tmp, ".")
            except (OSError, subprocess.SubprocessError) as e:
                ok, res = False, "the checker did not run: %s" % e
            check = "memory checker (%s): %s" % (os.path.relpath(cmd[-2], tmp) if cmd[-2].startswith(tmp) else
                                                 " ".join(cmd), res or ("green" if ok else "red"))
        else:
            ok, check = True, "no memory checker (kit.memory_check in .kit/config.json names one)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not ok:
        return ("%s, the project's memory checker is red on the compacted tree (nothing moved); fix what it names and "
                "compact again:\n%s\n%s" % ("compact (dry-run): would be REFUSED" if dry_run else "compact: REFUSED",
                                            check, plan)) + tail
    if dry_run:
        return "compact (dry-run): would archive %d file(s):\n%s\n%s" % (len(to_move), plan, check) + tail
    if on_board:
        # the posts first: nothing leaves memory until every point and issue is on the board (and sent there)
        failed = [pid for pid, doc in planned if not board.write(pid, doc)]
        has = getattr(board, "has_comment", None)
        failed += [cid for cid, c in notes if not (has and has(cid)) and not board.comment(cid, c)]
        if failed:
            return ("compact: the board did not take %d post(s) (%s); nothing archived: is the board server running?"
                    % (len(failed), ", ".join(failed[:5]))) + tail
        unsent = [pid for pid in [p for p, _ in planned] + matched + [c for c, _ in notes] if not board.sent(pid)]
        if unsent:
            return ("compact: %d post(s) or comment(s) are in the board's local copy, not yet on the Artifact; nothing archived. Send "
                    "them: %s; then `kit.py compact` again." % (len(unsent), board.push_hint() or "kit.py artifact push")
                    ) + tail
    _apply_compaction(mdir, ops)
    return "compact: archived %d file(s) into %s (updated archive/digest.md)\n%s" % (
        len(to_move), os.path.relpath(os.path.join(mdir, "archive"), base), check) + tail


def report_metrics(m, db_path=None):
    """Compute weekly ROI & board self-audit metrics (posts, open questions, owner wait p50/p95, DB/blob health)."""
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    posts, comments = {}, []
    db_kb = 0
    blob_count = missing_blobs = 0
    if os.path.isfile(db):
        db_kb = round(os.path.getsize(db) / 1024.0, 1)
        blob_dir = os.path.join(os.path.dirname(db), "blobs")
        if os.path.isdir(blob_dir):
            blob_count = sum(1 for fn in os.listdir(blob_dir) if re.fullmatch(r"[a-fA-F0-9]{16,64}", fn))
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            ref_blobs = set()
            for coll, did, raw in conn.execute("SELECT collection, doc_id, data FROM docs WHERE collection IN ('posts','comments')"):
                for bid in re.findall(r"/_blob/([a-fA-F0-9]{16,64})", raw or ""):
                    ref_blobs.add(bid)
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if coll == "posts" and not did.startswith("_"):
                    posts[did] = d
                elif coll == "comments":
                    d["_id"] = did
                    comments.append(d)
            missing_blobs = sum(1 for b in ref_blobs if not os.path.isfile(os.path.join(blob_dir, b)))
        finally:
            conn.close()
    by_col = {"todo": 0, "question": 0, "info": 0, "done": 0}
    oldest_q_age_h = 0.0
    oldest_q_id = ""
    now_dt = datetime.now(timezone.utc)
    for pid, p in posts.items():
        if p.get("removed"):
            continue
        col = str(p.get("column") or "info")
        by_col[col] = by_col.get(col, 0) + 1
        if col == "question" and p.get("created_at"):
            try:
                age_h = (now_dt - iso(str(p["created_at"]))).total_seconds() / 3600.0
                if age_h > oldest_q_age_h:
                    oldest_q_age_h, oldest_q_id = age_h, pid
            except ValueError:
                pass
    by_post = {}
    owner_cmts = 0
    for c in comments:
        by_post.setdefault(str(c.get("post_id") or ""), []).append(c)
        if c.get("from") in ("owner", "viewer"):
            owner_cmts += 1
    waits_min = []
    for cs in by_post.values():
        cs.sort(key=lambda x: str(x.get("created_at") or ""))
        pending_t = None
        for c in cs:
            ts = str(c.get("created_at") or "")
            if not ts:
                continue
            try:
                dt = iso(ts)
            except ValueError:
                continue
            if c.get("from") in ("owner", "viewer") and not c.get("like"):
                if pending_t is None:
                    pending_t = dt
            elif c.get("from") not in ("owner", "viewer", "clerk") and pending_t is not None:
                diff = (dt - pending_t).total_seconds() / 60.0
                if 0 <= diff <= 1440:
                    waits_min.append(diff)
                pending_t = None
    waits_min.sort()
    def pct(arr, q):
        if not arr:
            return None
        idx = min(len(arr) - 1, int(round((len(arr) - 1) * q)))
        return round(arr[idx], 1)
    p50, p95 = pct(waits_min, 0.5), pct(waits_min, 0.95)
    st = read_state(m)
    last_sync = st.get("last_sync") or "(never)"
    lines = [
        "# Board Self-Audit & ROI Report",
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Posts (Action / Decisions / Updates / Done) | %d (%d / %d / %d / %d) |" % (sum(by_col.values()), by_col["todo"], by_col["question"], by_col["info"], by_col["done"]),
        "| Comments (total / owner) | %d / %d |" % (len(comments), owner_cmts),
        "| Owner reply wait (p50 / p95, n=%d) | %s / %s min |" % (len(waits_min), p50 if p50 is not None else "n/a", p95 if p95 is not None else "n/a"),
        "| Oldest open question | %s |" % (("%s (%.1fh)" % (oldest_q_id, oldest_q_age_h)) if oldest_q_id else "none"),
        "| SQLite DB size | %.1f KB |" % db_kb,
        "| Blobs (local / missing refs) | %d / %d |" % (blob_count, missing_blobs),
        "| Mirror last sync | %s |" % last_sync,
    ]
    return "\n".join(lines) + "\n"


def portfolio_summary(config_path=None, roots=None):
    """Aggregate open counts across multiple project boards (multi-project federation, issue #3)."""
    projects = []
    if config_path and os.path.isfile(config_path):
        cfg = json.load(open(config_path, encoding="utf-8"))
        for item in cfg.get("projects") or []:
            if isinstance(item, dict) and item.get("path"):
                projects.append((item.get("name") or os.path.basename(item["path"]), item["path"]))
            elif isinstance(item, str):
                projects.append((os.path.basename(item), item))
    for r in (roots or []):
        ap = os.path.abspath(r)
        projects.append((os.path.basename(ap), ap))
    if not projects:
        return "portfolio: pass --config portfolio.json or --roots <dir1> <dir2> ..."
    now_dt = datetime.now(timezone.utc)
    lines = ["# Portfolio Board Index", "", "| Project | Action | Decisions | Updates | Done | Aging >24h |", "|---|---|---|---|---|---|"]
    tot = [0, 0, 0, 0, 0]
    for name, root in projects:
        db = os.path.join(root, ".kit", "board_data", "board.db")
        c_todo = c_q = c_info = c_done = c_age = 0
        if os.path.isfile(db):
            conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
            try:
                for did, raw in conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts'"):
                    if did.startswith("_"):
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if d.get("removed"):
                        continue
                    col = str(d.get("column") or "info")
                    if col == "todo": c_todo += 1
                    elif col == "question": c_q += 1
                    elif col == "done": c_done += 1
                    else: c_info += 1
                    if col in ("todo", "question") and d.get("created_at"):
                        try:
                            if (now_dt - iso(str(d["created_at"]))).total_seconds() >= 86400:
                                c_age += 1
                        except ValueError:
                            pass
            finally:
                conn.close()
        tot[0] += c_todo; tot[1] += c_q; tot[2] += c_info; tot[3] += c_done; tot[4] += c_age
        lines.append("| %s | %d | %d | %d | %d | %d |" % (name, c_todo, c_q, c_info, c_done, c_age))
    lines.append("| **Total** | **%d** | **%d** | **%d** | **%d** | **%d** |" % tuple(tot))
    return "\n".join(lines) + "\n"


def _load_board_docs(m, db_path=None):
    """Load posts and comments dicts from local SQLite DB, falling back to mirror worktree."""
    posts, comments = {}, {}
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    if os.path.isfile(db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            for coll, did, raw in conn.execute("SELECT collection, doc_id, data FROM docs WHERE collection IN ('posts','comments')"):
                try:
                    doc = json.loads(raw)
                except ValueError:
                    continue
                if coll == "posts":
                    posts[did] = doc
                else:
                    comments[did] = doc
        finally:
            conn.close()
        return posts, comments
    for coll, target in (("posts", posts), ("comments", comments)):
        d = os.path.join(m, "board", coll)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                target[fn[:-5]] = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return posts, comments


def _first_summary_line(body):
    for line in str(body or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not s.startswith("|") and not s.startswith("```"):
            return s[:160]
    return ""


# ---------------------------------------------------------------- the post format and reviews (Claude Code CLI)
DENOM = re.compile(r"\d\s*/\s*\d|\bof\b|\bout of\b|\bper\b|\bvs\.?\b|[<>=\u2264\u2265]")


def _kit_config():
    try:
        with open(os.path.join(ROOT, ".kit", "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return CONFIG or {}


def _slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:n].strip("-") or "x"


def _get_post(pid, db_path=None):
    """A post's data: from the board server (works from a part's worktree), else the local database."""
    try:
        r = _board_request(_resolve_board_url(_kit_config().get("board_url")), "doc/posts/" + urllib.parse.quote(pid, safe=""),
                           token=_resolve_board_token(None))
        if r.get("exists") is not False and isinstance(r.get("data"), dict):
            return r["data"]
    except Exception:
        pass
    posts, _ = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    return posts.get(pid)


def _get_comment(cid, db_path=None):
    """A comment's data, from the board server or the local database; None when it is not on this board."""
    try:
        r = _board_request(_resolve_board_url(_kit_config().get("board_url")), "doc/comments/" + urllib.parse.quote(cid, safe=""),
                           token=_resolve_board_token(None))
        if r.get("exists") is not False and isinstance(r.get("data"), dict):
            return r["data"]
    except Exception:
        pass
    _, comments = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    return comments.get(cid)


def check_asked(p, db_path=None):
    """(errors, notes) of a post's `asked`: his words that started the step, [{"id": <comment id>, "quote": ...}].
    Each must be answered by a bar or a not_checked item that names `asked <id>`, and each quote must be in that
    comment's text when the comment is on this board (a few hundred tokens, written once at turn 1)."""
    err, notes = [], []
    asked = _as_list(p.get("asked"))
    if not asked:
        return err, notes
    said = " \n ".join(str(x) for x in _as_list(p.get("bars")) + _as_list(p.get("not_checked")))
    norm = lambda t: re.sub(r"\s+", " ", str(t or "")).strip().lower()
    for a in asked:
        a = a if isinstance(a, dict) else {"quote": str(a)}
        cid, quote = str(a.get("id") or "").strip(), str(a.get("quote") or "").strip()
        if not cid or not quote:
            err.append("asked: each entry needs his comment's id and his words: {\"id\": ..., \"quote\": ...}")
            continue
        if not re.search(r"asked\s+%s\b" % re.escape(cid), said):
            err.append("asked %s: answered by no bar and no not_checked (name it: \"... (asked %s)\"): %r"
                       % (cid, cid, quote[:80]))
        c = _get_comment(cid, db_path=db_path)
        if c is None:
            notes.append("asked %s: comment not on this board, the quote is not verified" % cid)
        elif norm(quote) not in norm(c.get("text")):
            err.append("asked %s: the quote is not in that comment (copy his words as he wrote them): %r" % (cid, quote[:80]))
    return err, notes


def _as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


EMBED_RE = re.compile(r"`((?:\./)?docs/[^`\s]+\.md)\s*\u00a7[^`]*`")


def part_docs(root, part):
    """The repo docs/*.md files a part wrote: docs/<PART>.md (any case), and every docs/*.md added on this branch since
    it left main (committed or not yet), docs/prompts/ aside (the kit writes those)."""
    out = set()
    part = str(part or "").strip().lower()
    base = ""
    for ref in ("origin/main", "main"):
        r = _git(root, "merge-base", "HEAD", ref, text=True)
        if r.returncode == 0 and r.stdout.strip():
            base = r.stdout.strip()
            break
    names = []
    if base:
        names += _git(root, "diff", "--name-only", "--diff-filter=A", base, "HEAD", "--", "docs", text=True).stdout.split()
    names += _git(root, "ls-files", "--others", "--exclude-standard", "--", "docs", text=True).stdout.split()
    for n in names:
        if n.endswith(".md") and not n.startswith("docs/prompts/"):
            out.add(n)
    for n in glob.glob(os.path.join(root, "docs", "*.md")):
        if part and os.path.splitext(os.path.basename(n))[0].lower() == part:
            out.add("docs/" + os.path.basename(n))
    return out


def check_body_in_post(p, root=ROOT):
    """CL36 (the owner, 2026-11-24: "why sessions are writing out documents and linking them inside posts section by
    section instead of keeping everything in a post?"): a part's result lives in its post body; a body that is mostly
    `docs/X.md \u00a7 Section` embeds of a docs file the part wrote is refused. Embeds of files it did not write (the
    kit's guides, an earlier part's report) stay allowed, and so do posts written before this rule (done is not rerun)."""
    body = str(p.get("body") or "")
    hits = EMBED_RE.findall(body)
    if not hits:
        return []
    mine = part_docs(root, p.get("part"))
    own = [h for h in hits if h.lstrip("./") in mine]
    if not own:
        return []
    lines, infence = [], False
    for l in body.splitlines():
        t = l.strip()
        if t.startswith("```"):
            infence = not infence
            lines.append("fence")
            continue
        if infence or not t or re.match(r"#{1,6}\s", t):
            continue
        lines.append(t)
    embed_lines = [l for l in lines if any(o in l for o in own) and len(EMBED_RE.sub("", l).strip(" -*:.,;")) < 40]
    if len(own) >= 2 and len(embed_lines) * 2 >= len(lines):
        files = sorted({o.lstrip("./") for o in own})
        return ["body: %d of its %d lines only embed sections of %s, a docs file this step wrote: the result lives in "
                "the post body (CL36). Put the text in the body under ## sections, pictures as ![what](path.png), "
                "diagrams as ```mermaid or ```svg blocks; then drop the file" % (len(embed_lines), len(lines), ", ".join(files))]
    return []


def check_done(p, root=ROOT, body_max=1500):
    """(errors, warnings) of a post against the kit's post format (.kit/BOARD.md § The post format)."""
    err, warn = [], []
    verdict = str(p.get("verdict") or "").strip()
    if not verdict:
        err.append("verdict: missing (one plain sentence that answers the task; the train view and the card show it)")
    elif len(verdict) > 240 or "\n" in verdict:
        err.append("verdict: %d characters; one sentence of at most 240 (the detail goes in the body or docs/)" % len(verdict))
    bars = [str(b).strip() for b in _as_list(p.get("bars")) if str(b).strip()]
    if not bars:
        err.append("bars: missing (each measured number with its denominator, or [\"none: <why nothing was measured>\"])")
    for b in bars:
        if b.lower().startswith("none"):
            continue
        if not re.search(r"\d", b):
            err.append("bars: %r has no number" % b[:60])
        elif not DENOM.search(b):
            warn.append("bars: %r has no denominator or reference (n of N, x/y, per, vs, <= bar)" % b[:60])
    for a in _as_list(p.get("artifacts")):
        a = str(a).strip()
        if not a or a.startswith(("http://", "https://", "/_blob/")):
            continue
        path = a.split(" \u00a7 ")[0].split(" \u00a7")[0].strip().strip("`")
        if not os.path.exists(os.path.join(root, path)):
            err.append("artifacts: %s does not exist in the repo (commit it, or link it as /_blob/<id>)" % path)
    if not _as_list(p.get("not_checked")):
        err.append("not_checked: missing (what you did NOT check; [\"nothing left unchecked\"] if so)")
    body = str(p.get("body") or "")
    if len(body) > body_max:
        # Not an error: the post is where the detail goes. Only a nudge to keep the answer first and the sections clear.
        warn.append("body: %d characters (over %d): fine if it is the result itself, with ## sections (they fold); "
                    "pictures and diagrams go in it too" % (len(body), body_max))
    err += check_body_in_post(p, root=root)
    # Say it once: a body that embeds a repo doc points to its sections; it does not repeat them.
    docs = set(re.findall(r"`([^`\s]+\.md)(?:\s*\u00a7[^`]*)?`", body)) | {
        str(a).split(" \u00a7")[0].strip("` ") for a in _as_list(p.get("artifacts")) if str(a).split(" \u00a7")[0].strip("` ").endswith(".md")}
    blines = {l.strip() for l in body.splitlines() if len(l.strip()) >= 40}
    for doc in sorted(docs):
        try:
            with open(os.path.join(root, doc), encoding="utf-8") as fh:
                dlines = {l.strip() for l in fh if len(l.strip()) >= 40}
        except OSError:
            continue
        same = len(blines & dlines)
        if same >= 3:
            warn.append("body: %d lines repeat %s: point to its sections (`%s \u00a7 Section`) instead of copying them"
                        % (same, doc, doc))
    if len(body) > 1200 and not re.search(r"^#{2,4}\s", body, re.M):
        warn.append("body: %d characters and no ## sections: put what he must know first (the lead always shows), "
                    "the rest under ## headings (they fold; see `kit.py sections`)" % len(body))
    if any(re.search(r"\d", b) for b in bars if not b.lower().startswith("none")) and not str(p.get("check") or "").strip():
        warn.append("check: missing (the one command that reproduces the main number; the orchestrator and the review rerun it)")
    if str(p.get("check") or "").strip() and not str(p.get("breaks") or "").strip():
        err.append("breaks: missing (how to make `check` fail: a command that plants a fault or feeds a bad input, e.g. "
                   "`sed -i 's/a + b/a - b/' calc.py`, or `manual: <what the review does>` for a browser step); "
                   "a check that cannot fail proves nothing")
    if not str(p.get("handoff") or "").strip():
        warn.append("handoff: missing (what the next step needs from yours)")
    return err, warn


def sections(db_path=None):
    """What the board learned from the owner's opening and folding of ## sections (posts/_prefs.sections)."""
    p = _get_post("_prefs", db_path=db_path) or {}
    secs = p.get("sections") or {}
    opens = sorted((k for k, v in secs.items() if (v.get("score") or 0) >= 2), key=lambda k: -secs[k]["score"])
    folds = sorted((k for k, v in secs.items() if (v.get("score") or 0) <= -2), key=lambda k: secs[k]["score"])
    if not secs:
        return ("Nothing learned yet: sections open by default; audit-like ones (What I checked, Not checked, Commits, "
                "Details, Log) fold; a heading ending in {fold} folds.")
    return "\n".join([
        "He opens (shown open everywhere): " + (", ".join(opens) or "none yet"),
        "He folds (folded everywhere): " + (", ".join(folds) or "none yet"),
        "Still learning: " + (", ".join(sorted(k for k in secs if k not in opens and k not in folds)) or "none"),
        "Put what he opens in the lead or in those sections; never put what he must know under a heading he folds."])


MANUAL = "manual:"


def _as_command(v):
    """A `check` or `breaks` value as a shell command, or None when it is `manual: ...` (the review performs it)."""
    s = str(v or "").strip()
    if len(s) >= 2 and s[0] == s[-1] == "`":
        s = s.strip("`").strip()
    if not s or s.lower().startswith(MANUAL):
        return None
    return s


def _git(root, *args, **kw):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, **kw)


@contextlib.contextmanager
def throwaway_copy(root=ROOT):
    """A copy of the work as it stands (commits, uncommitted edits and new files): a detached git worktree when root
    is a git checkout with a commit, else a plain directory copy. Removed afterwards; root itself is never touched."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kit-breaks-")
    dst = os.path.join(tmp, "work")
    top = _git(root, "rev-parse", "--show-toplevel", text=True)
    use_git = (top.returncode == 0 and os.path.realpath(top.stdout.strip()) == os.path.realpath(root)
               and _git(root, "rev-parse", "--verify", "-q", "HEAD").returncode == 0)
    try:
        if use_git:
            r = _git(root, "worktree", "add", "--detach", "-q", dst, "HEAD", text=True)
            if r.returncode != 0:
                raise RuntimeError("git worktree add failed: " + (r.stderr or "").strip()[-300:])
            diff = _git(root, "diff", "HEAD", "--binary").stdout
            if diff.strip():
                r = subprocess.run(["git", "-C", dst, "apply", "--whitespace=nowarn", "-"], input=diff, capture_output=True)
                if r.returncode != 0:
                    raise RuntimeError("could not carry uncommitted edits into the copy: "
                                       + r.stderr.decode("utf-8", "replace").strip()[-300:])
            new = _git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout.decode("utf-8", "replace")
            for rel in filter(None, new.split("\0")):
                src, out = os.path.join(root, rel), os.path.join(dst, rel)
                if os.path.isfile(src) or os.path.islink(src):
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    shutil.copy2(src, out, follow_symlinks=False)
        else:
            shutil.copytree(root, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        yield dst
    finally:
        if use_git:
            _git(root, "worktree", "remove", "--force", dst)
            _git(root, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def _run_in(cmd, cwd, timeout, extra_env=None):
    """(exit code, last lines of its output) of a shell command run in cwd; a timeout counts as exit 124."""
    env = dict(os.environ, KIT_BREAKS_COPY="1", **(extra_env or {}))
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "timed out after %d s" % timeout
    tail = " / ".join([l.strip() for l in out.splitlines() if l.strip()][-3:])
    return rc, tail[-300:]


REVERT = re.compile(r"^revert(?:\s+(\S+))?\s*$", re.I)


def _revert_base(root, arg=None):
    """The commit a step's work started from: `arg` when given, else where HEAD left the trunk (kit.main_branch)."""
    if arg:
        return arg
    trunk = (_kit_config().get("kit") or {}).get("main_branch") or "main"
    for ref in ("origin/" + trunk, trunk):
        r = _git(root, "merge-base", "HEAD", ref, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return trunk


def _revert_in(work, root, arg=None):
    """Undo the step's own change in the throwaway copy `work` (committed, uncommitted and new files alike), so the
    old version is the made-to-fail one: no new art, about 0 tokens. (ok, message)."""
    if not os.path.isdir(os.path.join(work, ".git")) and not os.path.isfile(os.path.join(work, ".git")):
        return False, "breaks: revert needs a git checkout (this work is not one)"
    base = _revert_base(root, arg)
    if _git(work, "rev-parse", "--verify", "-q", base + "^{commit}").returncode != 0:
        return False, "breaks: revert base %r is not a commit here (write `revert <base>`)" % base
    _git(work, "add", "-A")
    diff = _git(work, "diff", "--cached", "--binary", base).stdout
    if not diff.strip():
        return False, ("breaks: revert has nothing to revert (no change since %s): commit the step on its branch, or "
                       "write `revert <base>`" % base[:12])
    r = subprocess.run(["git", "-C", work, "apply", "-R", "--index", "--whitespace=nowarn", "-"], input=diff,
                       capture_output=True)
    if r.returncode != 0:
        return False, "breaks: could not revert the step's change: " + r.stderr.decode("utf-8", "replace").strip()[-300:]
    return True, "reverted to %s" % base[:12]


def prove_check(p, root=ROOT, timeout=900):
    """(errors, notes) of running a post's `check` and `breaks` in a throwaway copy of the work: `check` must pass,
    then, after `breaks` is applied, it must fail. A `manual: ...` check or breaks is left to the review."""
    err, notes = [], []
    raw_check, raw_breaks = str(p.get("check") or "").strip(), str(p.get("breaks") or "").strip()
    if not raw_check or not raw_breaks:
        return err, notes  # check_done already says what is missing
    check, breaks = _as_command(raw_check), _as_command(raw_breaks)
    if check is None:
        notes.append("check is manual: the review performs it, and the breaks, by hand")
        return err, notes
    if os.environ.get("KIT_BREAKS_COPY"):
        notes.append("check not run: already inside a breaks copy")
        return err, notes
    try:
        with throwaway_copy(root) as work:
            rc, tail = _run_in(check, work, timeout)
            if rc != 0:
                err.append("check: fails before breaks (exit %d) in a copy of the work: %s" % (rc, tail or "no output"))
                return err, notes
            if breaks is None:
                notes.append("check passes (exit 0); breaks is manual: the review performs it and sees check fail")
                return err, notes
            m_rev = REVERT.match(breaks)
            if m_rev:
                ok, msg = _revert_in(work, root, m_rev.group(1))
                rc_b, tail_b = (0, msg) if ok else (1, msg)
                if not ok:
                    err.append(msg)
                    return err, notes
            else:
                rc_b, tail_b = _run_in(breaks, work, timeout)
            if rc_b != 0:
                err.append("breaks: did not apply (exit %d): %s. Write it as a command that plants the fault, "
                           "or as `manual: <what the review does>`" % (rc_b, tail_b or "no output"))
                return err, notes
            rc2, _ = _run_in(check, work, timeout)
            if rc2 == 0:
                err.append("check: still passes after breaks: a check that cannot fail is refused (make check test what "
                           "breaks plants, or make breaks plant a fault that check sees)")
            else:
                notes.append("check passes (exit 0) and fails after breaks (exit %d): it can fail" % rc2)
    except (OSError, RuntimeError) as e:
        err.append("breaks: no throwaway copy of the work: %s" % e)
    return err, notes


def pair(pid, answer=None, db_path=None, root=None, timeout=900):
    """A blind before/after pair for a visual step (3 to 5k tokens for the reviewer, against 30k+ for a full review):
    the post's `shot` (a command that writes a PNG to $KIT_SHOT) renders the work as it is and as reverted (the old
    version, as `breaks: revert` does), saved as A and B in random order under .kit/run/pairs/<post>/, the key aside.
    The brief holds his quoted words and the two pictures, nothing else. `answer` scores the reviewer's pick."""
    import secrets
    root = root or ROOT
    d = os.path.join(root, ".kit", "run", "pairs", _slug(pid, 60))
    key_path = os.path.join(d, "key.json")
    if answer:
        try:
            with open(key_path, encoding="utf-8") as fh:
                key = json.load(fh)
        except (OSError, ValueError):
            return 1, "no pair for %s yet: run `kit.py pair %s` first" % (pid, pid)
        pick = str(answer).strip().upper()
        ok = pick == key.get("new")
        key.setdefault("answers", []).append({"pick": pick, "right": ok, "at": _now_z()})
        with open(key_path, "w", encoding="utf-8") as fh:
            json.dump(key, fh)
        if ok:
            return 0, "right: %s is the new version (the one his words asked for); %s is the old one" % (pick, key.get("old"))
        return 1, ("wrong: %s is the old version. The reviewer could not tell the new work from what was there before: "
                   "the change does not answer his words where it shows, or the reviewer did not look" % pick)
    p = _get_post(pid, db_path=db_path)
    if not p:
        return 1, "no post %s on the board" % pid
    shot = _as_command(p.get("shot"))
    if not shot:
        return 1, ("shot: missing on %s (a command that renders the step's picture and writes a PNG to $KIT_SHOT, e.g. "
                   "`node scripts/shot.js --scene night --out \"$KIT_SHOT\"`)" % pid)
    m_rev = REVERT.match(_as_command(p.get("breaks")) or "")
    base = m_rev.group(1) if m_rev else None
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kit-pair-")
    new_png, old_png = os.path.join(tmp, "new.png"), os.path.join(tmp, "old.png")
    try:
        with throwaway_copy(root) as work:
            rc, tail = _run_in(shot, work, timeout, {"KIT_SHOT": new_png})
            if rc != 0 or not os.path.isfile(new_png):
                return 1, "shot: did not write a picture of the work (exit %d): %s" % (rc, tail or "no output")
            ok, msg = _revert_in(work, root, base)
            if not ok:
                return 1, msg
            rc, tail = _run_in(shot, work, timeout, {"KIT_SHOT": old_png})
            if rc != 0 or not os.path.isfile(old_png):
                return 1, "shot: did not write a picture of the old version (exit %d): %s" % (rc, tail or "no output")
        with open(new_png, "rb") as fh:
            nb = fh.read()
        with open(old_png, "rb") as fh:
            ob = fh.read()
        if nb == ob:
            return 1, ("the change is not visible: the step's picture is the same before and after (%s). A visual claim "
                       "needs a shot that shows it" % msg)
        os.makedirs(d, exist_ok=True)
        new = secrets.choice("AB")
        old = "B" if new == "A" else "A"
        for lab, data in ((new, nb), (old, ob)):
            with open(os.path.join(d, lab + ".png"), "wb") as fh:
                fh.write(data)
        with open(key_path, "w", encoding="utf-8") as fh:
            json.dump({"post": pid, "new": new, "old": old, "made_at": _now_z()}, fh)
    except (OSError, RuntimeError) as e:
        return 1, "pair: no throwaway copy of the work: %s" % e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rel = os.path.relpath(d, root)
    lines = ["Blind pair for %s. Read only this, his words and the two pictures (not the code, not the board)." % pid]
    asked = [a for a in _as_list(p.get("asked")) if isinstance(a, dict)]
    if asked:
        lines.append("His words:")
        lines += ['- "%s" (comment %s)' % (str(a.get("quote") or "").strip(), a.get("id")) for a in asked]
    else:
        lines.append("His words: none on the post (add `asked` so the reviewer judges against them)")
    lines += ["Picture A: %s/A.png" % rel, "Picture B: %s/B.png" % rel,
              "Which picture answers his words? Answer: python3 -I .kit/kit.py pair %s --answer A|B" % pid,
              "Then say in the review what you saw. Whether it is what he meant stays his call."]
    return 0, "\n".join(lines)


def done(pid, db_path=None, root=None):
    p = _get_post(pid, db_path=db_path)
    if not p:
        return 1, "NOT READY: no post %s on the board" % pid
    k = _kit_config().get("kit") or {}
    root = root or ROOT
    err, warn = check_done(p, root=root, body_max=int(k.get("post_body_max", 1500)))
    e_a, n_a = check_asked(p, db_path=db_path)
    err += e_a
    warn += n_a
    err += check_real_project(p, root=root)          # CL32: compact/install/resume/hooks/artifact: a real project too
    if attached_board() and _read_json(_base_path("posts", pid)) != p:
        # CL28: a part updated its post on the local copy only and said "Done": the live board (the Artifact) still
        # showed it planned, with none of its results (the owner: "How come you said you posted when you didn't?")
        err.append("the post is not on the board yet (%s): its local copy differs from what the Artifact last had. Run "
                   "`python3 -I .kit/kit.py artifact push`, make the ArtifactData batch it prints, then `kit.py artifact "
                   "sent <batch>`; then done again" % (artifact_url() or "the Artifact"))
    if not err:
        e2, notes = prove_check(p, root=root, timeout=int(k.get("check_timeout_s", 900)))
        err += e2
        warn += notes
    lines = (["NOT READY: %s" % pid] if err else ["OK: %s follows the post format" % pid])
    lines += ["  fix: " + e for e in err] + ["  note: " + w for w in warn]
    if not err:
        lines.append("Next: commit and push, run .kit/end_session.py, then SendMessage the orchestrator: Done. Check post %s." % pid)
    return (1 if err else 0), "\n".join(lines)


def _active_cli(explicit=None):
    if explicit in ("agy", "claude"):
        return explicit
    if _is_web():          # CL32: a web session is Claude Code whatever the project's cli (Harmonize's is agy)
        return "claude"
    env_cli = str(os.environ.get("KIT_CLI") or "").strip().lower()
    if env_cli in ("agy", "claude"):
        return env_cli
    k = _kit_config().get("kit") or {}
    cfg_cli = str(k.get("cli") or "").strip().lower()
    return cfg_cli if cfg_cli in ("agy", "claude") else "claude"


def _ancestor(comm_name):
    """True when a process named `comm_name` (e.g. agy) encloses this one (/proc; False where there is none)."""
    pid, seen = os.getppid(), set()
    while pid > 1 and pid not in seen and os.path.exists("/proc/%d/comm" % pid):
        seen.add(pid)
        try:
            with open("/proc/%d/comm" % pid, encoding="utf-8") as fh:
                if fh.read().strip() == comm_name:
                    return True
            with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
                st = fh.read()
            pid = int(st[st.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def _launcher(explicit=None):
    """CL26: the runtime a launcher (review, part, clerk) starts a session with: the one it runs in, never a binary
    that is absent. "web" (a Claude Code web/cloud session: a script cannot start one, the orchestrator makes the
    create_session call), "agy", "claude" (`claude --bg`), or None (nothing runnable here: the caller says so).
    Order: the web; inside agy with agy on PATH; inside Claude Code with claude on PATH; the preferred CLI (explicit,
    $KIT_CLI, kit.cli) when on PATH; any CLI on PATH."""
    if _is_web():
        return "web"
    have = lambda b: shutil.which(b) is not None  # noqa: E731
    if (os.environ.get("AGY_PID") or _ancestor("agy")) and have("agy"):
        return "agy"
    if (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")) and have("claude"):
        return "claude"
    pref = [explicit, os.environ.get("KIT_CLI"), (_kit_config().get("kit") or {}).get("cli")]
    for b in [str(x or "").strip().lower() for x in pref] + ["claude", "agy"]:
        if b in ("agy", "claude") and have(b):
            return b
    return None


NO_RUNTIME = ("NOT STARTED: no runtime to start a session here (not a Claude Code web session; neither `claude` nor "
              "`agy` on PATH)")


def review_sample(bars, seed=None):
    """The bars a step review reruns: one in five picked at random (at least one), plus every bar marked [visual].
    The part cannot know which, so cutting corners on any claim does not pay; the review stays a few thousand tokens."""
    import random
    bars = [str(b).strip() for b in _as_list(bars) if str(b).strip()]
    if seed is None and os.environ.get("KIT_SAMPLE_SEED"):
        seed = int(os.environ["KIT_SAMPLE_SEED"])
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    visual = [b for b in bars if "[visual]" in b.lower()]
    rest = [b for b in bars if b not in visual]
    n = max(1, (len(rest) + 4) // 5) if rest else 0
    picked = set(rng.sample(rest, n)) if rest else set()
    return [b for b in bars if b in picked or b in visual]


def review_brief(p, seed=None):
    """What a step review rechecks, written into its prompt: his words (`asked`) and the sampled bars."""
    lines = []
    asked = [a for a in _as_list(p.get("asked")) if isinstance(a, dict)]
    if asked:
        lines.append("His words that started this step (judge the work against them, not against the post's summary):")
        lines += ['- "%s" (comment %s)' % (str(a.get("quote") or "").strip(), a.get("id")) for a in asked]
    picked = review_sample(p.get("bars"), seed=seed)
    if picked:
        lines.append("Recheck these claims (a random sample: one bar in five, and every [visual] one); rerun, do not reread:")
        lines += ["- recheck: %s" % b for b in picked]
    if str(p.get("shot") or "").strip():
        lines.append("Visual: run `python3 -I .kit/kit.py pair %s`, look at the two pictures blind, answer with "
                     "`--answer A|B`, and say what you saw." % (p.get("_id") or "<post>"))
    return "\n" + "\n".join(lines) if lines else ""


def review(pid=None, train=None, dry_run=False, force=False, db_path=None, cli=None):
    """Write a review prompt and start it as a background session (Claude Code CLI or agy): a step review
    (pid) or a whole-train review (train). Idempotent: a review already started is not started again."""
    c = _kit_config()
    k = c.get("kit") or {}
    active = _active_cli(cli)
    slug = _slug(c.get("project") or "project")
    if train and not pid:
        posts, _ = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
        steps = sorted([(str(p.get("created_at") or ""), i) for i, p in posts.items()
                        if not p.get("removed") and _slug(p.get("train") or "").replace("p-", "", 1) == _slug(train).replace("p-", "", 1)])
        done_n = sum(1 for _, i in steps if str(posts[i].get("status") or "") == "complete" or posts[i].get("column") == "done")
        pid = steps[-1][1] if steps else "_orchestrator"
        rid = "CR-train-%s-%d" % (_slug(train, 24), done_n)
        part, project = "the whole train", train
        scope = ("This is a CROSS REVIEW of the whole train `%s` (%d steps complete): read every step, then answer the three "
                 "questions for the train as a whole; your comment goes under its latest post `%s`." % (train, done_n, pid))
    else:
        p = _get_post(pid, db_path=db_path) or {}
        rid = "CR-%s" % pid
        part, project = str(p.get("part") or pid), str(p.get("train") or "")
        scope = ("The step `%s` (part %s, train `%s`) is complete: review it AND the question its train serves, not only "
                 "this step." % (pid, part, project))
        scope = scope + review_brief(dict(p, _id=pid))
    if force:
        # a second review of the same step is a new review: its own id and comment, the first one kept
        base, n = rid, 2
        while os.path.exists(os.path.join(ROOT, ".kit", "run", rid + ".md")):
            rid = "%s-%d" % (base, n)
            n += 1
    session = "%s-review-%s" % (slug, _slug(rid[3:], 30))
    # a dry run shows what an explicitly named CLI would run (nothing runs, so its binary need not be here)
    run = cli if (dry_run and cli in ("agy", "claude")) else _launcher(cli)
    # the guide of the runtime that runs the review; on the web, kit.cli's (Harmonize: agy's send_message <ORCH_ID>)
    if (run if run in ("agy", "claude") else active) == "agy":
        cands = (os.path.join(ROOT, ".kit", "consultant.md"), os.path.join(ROOT, ".kit", "claude-cli", "consultant.md"))
    else:
        cands = (os.path.join(ROOT, ".kit", "claude-cli", "consultant.md"), os.path.join(ROOT, ".kit", "consultant.md"))
    tpl, tpl_path = None, cands[0]
    for cand in cands:
        if os.path.isfile(cand):
            tpl_path = cand
            with open(cand, encoding="utf-8") as fh:
                tpl = fh.read()
            break
    if tpl is None:
        return 1, "no %s (reinstall the kit)" % tpl_path
    orch_doc = _get_post("_orchestrator", db_path=db_path) or {}
    orch_id = str(orch_doc.get("session_id") or ("%s-orchestrator" % slug))
    for key, val in (("<SCOPE>", scope), ("<SESSION>", session), ("<REVIEW_ID>", rid), ("<POST_ID>", pid),
                     ("<PART>", part), ("<PROJECT_ID>", project or "(none)"), ("<ORCH_ID>", orch_id)):
        tpl = tpl.replace(key, val)
    run_dir = os.path.join(ROOT, ".kit", "run")
    out = os.path.join(run_dir, rid + ".md")
    if os.path.exists(out) and not force and not dry_run:
        return 0, "review %s already started (prompt %s); --force to start it again" % (rid, os.path.relpath(out, ROOT))
    text = "Your prompt is %s: read it and do it." % os.path.relpath(out, ROOT)
    # CL26: `run` is the runtime this runs in, never kit.cli's binary when it is absent
    if run == "web":
        call = {"title": "Review %s" % rid, "source_url": _github_url(),
                "source_revision": _git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main",
                "prompt": text, "tags": ["review"]}      # CL30: the guard checks a tagged launch's prompt
        start = ("review %s NOT STARTED by the kit (a web session cannot start one): prompt %s written; start it "
                 "now with the claude-code-remote `create_session` tool: %s" % (rid, os.path.relpath(out, ROOT),
                                                                               json.dumps(call, ensure_ascii=False)))
    elif run == "agy":
        cmd = ["agy", "--mode", "auto", "-p", text]
    elif run is None:
        start = None
    else:
        mode = k.get("claude_permission_mode") or "auto"
        model = ((k.get("claude_cli_roles") or {}).get("consultant") or {}).get("model") or "opus"
        cmd = ["claude", "--bg", "-n", session, "--permission-mode", mode]
        if model != "inherit":
            cmd += ["--model", model]
        cmd.append(text)
    if dry_run:
        how = " ".join(cmd) if run in ("agy", "claude") else ("create_session (web)" if run == "web" else NO_RUNTIME)
        return 0, "would write %s and run: %s\n\n%s" % (os.path.relpath(out, ROOT), how, tpl)
    os.makedirs(run_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(tpl)
    if os.environ.get("KIT_NO_LAUNCH"):
        return 0, "review %s prompt written (KIT_NO_LAUNCH: not started)" % rid
    if run == "web":
        return 0, start
    if run is None:
        return 1, "review %s %s. Its prompt is %s: start it by hand." % (rid, NO_RUNTIME, os.path.relpath(out, ROOT))
    try:
        subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(os.environ, KIT_ROLE="review"))
    except OSError as e:
        return 1, "review %s NOT STARTED: %s could not run (%s). Its prompt is %s." % (rid, cmd[0], e,
                                                                                        os.path.relpath(out, ROOT))
    return 0, "review %s started (%s)" % (rid, "agy -p" if run == "agy" else ("session " + session))


# ---------------------------------------------------------------- steps: the train is the task list (any CLI)
def _now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_train(t):
    t = str(t or "").strip().lower()
    return t[2:] if t.startswith("p-") else t


def _board_write(cmd, coll, doc_id, data):
    cli = os.path.join(ROOT, ".kit", "board_cli.py")
    if not os.path.isfile(cli):
        return False
    try:
        r = subprocess.run([sys.executable, "-I", cli, cmd, coll, doc_id, "--data", json.dumps(data, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=20, cwd=ROOT)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _all_posts(db_path=None):
    posts, _ = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    return posts


def _device():
    """.kit/device.py (scripts/device.py in the kit's own repo): the device's session cap (CL15); None if missing."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("kit_device", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # a broken probe never blocks the kit
        return None
    return mod


def device_line(survey=None):
    d = _device()
    if d is None:
        return None
    try:
        return d.summary(survey or d.survey())
    except Exception as e:
        return "Device: could not measure (%s)." % e


def _launch_line(part, rel, model=None, cli=None, post=None):
    c = _kit_config()
    k = c.get("kit") or {}
    active = _active_cli(cli)
    mode = k.get("claude_permission_mode") or "auto"
    name = "%s-%s" % (_slug(c.get("project") or "project"), _slug(part, 24))
    if active == "agy":
        agy_model = str(model or ((k.get("roles") or {}).get("part") or {}).get("model") or "inherit")
        return ('invoke_subagent(TypeName="self", Role="Part: %s", Model="%s", Workspace="share", '
                'Prompt="Your prompt is %s: read it and do it.")') % (part, agy_model, rel)
    model = str(model or ((k.get("claude_cli_roles") or {}).get("part") or {}).get("model") or "inherit")
    post = post or (os.path.basename(rel)[:-3] if rel and rel.endswith(".md") else None)
    if post:   # CL13: one launch command writes the post and the Trains row, then starts the part (CLI or web)
        return "python3 -I .kit/kit.py launch %s%s" % (post, (" --model %s" % model) if model != "inherit" else "")
    gate = "python3 -I .kit/device.py --gate && " if _device() is not None else ""
    return "%sKIT_ROLE=part claude --bg -n %s -w %s --permission-mode %s%s \"Your prompt is %s: read it and do it.\"" % (
        gate, name, _slug(part, 24), mode, (" --model %s" % model) if model != "inherit" else "", rel)


def _write_step_prompt(pid, doc, sources, cli=None):
    """docs/prompts/<pid>.md from .kit/part.md (agy) or .kit/claude-cli/part.md; never overwrites one that exists."""
    rel = "docs/prompts/%s.md" % pid
    path = os.path.join(ROOT, rel)
    if os.path.exists(path):
        return rel, False
    active = _active_cli(cli)
    if active == "agy":
        cands = (os.path.join(ROOT, ".kit", "part.md"), os.path.join(ROOT, ".kit", "claude-cli", "part.md"))
    else:
        cands = (os.path.join(ROOT, ".kit", "claude-cli", "part.md"), os.path.join(ROOT, ".kit", "part.md"))
    tpl = None
    for cand in cands:
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                tpl = fh.read()
            break
    if tpl is None:
        return None, False
    c = _kit_config()
    src = ("\n".join("- comment `%s`: read it with `python3 -I .kit/board_cli.py get comments %s --out-dir /tmp/src`" % (x, x)
                      for x in sources) if sources else
           "(none named: the task is the orchestrator's; ask under your post if it is unclear)")
    orch_doc = _get_post("_orchestrator") or {}
    orch_id = str(orch_doc.get("session_id") or "<orchestrator conversationId>")
    # a source is the owner's comment or a review's (a redirect becomes a step that answers it)
    vals = {"<PART>": doc["part"], "<TITLE>": doc["title"], "<TRAIN>": doc["train"], "<POST_ID>": pid,
            "<AFTER>": (", after `%s`" % doc["parent"]) if doc.get("parent") else "",
            "<TASK>": (doc.get("body") or "").strip() or doc["title"], "<SOURCES>": src,
            "<ORCH_ID>": orch_id,
            "<SESSION>": "%s-%s" % (_slug(c.get("project") or "project"), _slug(doc["part"], 24))}
    for kk, vv in vals.items():
        tpl = tpl.replace(kk, vv)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tpl)
    return rel, True


def _step_board(train, posts, board=None):
    """CL25: the attached board a step goes on, as (id, its posts), or (None, None) for this repo's own board.
    `board` names it (`step new --board <id>`); else a train this board has no post of, found on exactly one attached
    board (Harmonize's orchestrator plans the kit's `cli` train, which lives under repos/board/ in the Artifact)."""
    own = any(_norm_train(p.get("train")) == _norm_train(train) for k, p in posts.items()
              if not k.startswith("_") and isinstance(p, dict))
    if not board and own:
        return None, None
    try:
        shown, _ = _attached_boards()
    except Exception:  # an unreadable attached board never blocks a step on this one
        shown = []
    if board:
        rid = _repo_id(board)
        b = next((x for x in shown if x["id"] == rid), None)
        return rid, dict((b or {}).get("posts") or {})
    hits = [b for b in shown if any(_norm_train((p or {}).get("train")) == _norm_train(train)
                                    for p in (b.get("posts") or {}).values() if isinstance(p, dict))]
    return (hits[0]["id"], dict(hits[0]["posts"])) if len(hits) == 1 else (None, None)


def step_new(train, part, title, task="", parent=None, sources=(), model=None, review=True, db_path=None, cli=None,
             board=None):
    """A train step: its post (adopted when the train already has one for that part, never duplicated) and its
    generated prompt. Returns {post, adopted, prompt, prompt_written, launch} or {error}. On an attached board (CL25)
    also {board, artifact_write}: the post is a file under .kit/run/artifact/out/repos/<id>/posts/, sent by the one
    ArtifactData call `artifact_write` names (the orchestrator guard lets that file through)."""
    posts = _all_posts(db_path)
    att, att_posts = _step_board(train, posts, board)
    if att:
        posts = att_posts
    found = None
    for pid, p in posts.items():
        if pid.startswith("_") or p.get("removed"):
            continue
        if _norm_train(p.get("train")) == _norm_train(train) and str(p.get("part") or "").strip().lower() == part.lower():
            if found is None or str(p.get("created_at") or "") > str(posts[found].get("created_at") or ""):
                found = pid
    if found:
        rel = "docs/prompts/%s.md" % found
        has = os.path.exists(os.path.join(ROOT, rel))
        res = {"post": found, "adopted": True, "prompt": rel if has else None, "prompt_written": False,
               "launch": _launch_line(part, rel, model, cli=cli) if has else None}
        if att:
            res["board"] = att
        return res
    title = re.sub(r"^\s*%s\b[\s:.-]*" % re.escape(part), "", title or part, flags=re.I) or title or part
    base = "%s-%s" % (_slug(part, 12), _slug(title))
    pid, n = base, 2
    while pid in posts:
        pid = "%s-%d" % (base, n)
        n += 1
    doc = {"title": title, "part": part, "train": train, "column": "info", "status": "planned", "body": task or "",
           "from": "orchestrator", "planned_by": "orchestrator", "created_at": _now_z()}
    if parent:
        doc["parent"] = parent
    if sources:
        doc["sources"] = list(sources)
    if review is False:
        doc["review_auto"] = False
    write = None
    if att:
        fp = os.path.join(os.path.abspath(_art_dir()), "out", "repos", att, "posts", pid + ".json")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        write_doc(fp, doc)
        write = {"action": "set", "url": artifact_url() or str(CONFIG.get("board_url") or ""),
                 "collection": "repos/%s/posts" % att, "doc_id": pid, "file_path": fp}
    elif not _board_write("set", "posts", pid, doc):
        return {"error": "the board did not take step %s (is the board server running?)" % part}
    rel, made = _write_step_prompt(pid, doc, list(sources), cli=cli)
    res = {"post": pid, "adopted": False, "prompt": rel, "prompt_written": made,
           "launch": _launch_line(part, rel, model, cli=cli) if rel else None}
    if att:
        res.update({"board": att, "artifact_write": write,
                    "next": "send the post: ArtifactData %s (no other way: a hand-written step post is refused)"
                            % json.dumps(write, ensure_ascii=False)})
    return res


def step_start(pid):
    return {"post": pid, "ok": _board_write("update", "posts", pid, {"status": "in progress"})}


def step_drop(pid, db_path=None):
    p = _all_posts(db_path).get(pid) or {}
    if str(p.get("status") or "") != "planned" or p.get("session_id"):
        return {"post": pid, "ok": False, "why": "kept: a part already started it"}
    return {"post": pid, "ok": _board_write("update", "posts", pid, {"removed": True})}


# ---------------------------------------------------------------- launch (CL13): a launched part is always on the board
def _is_web():
    return str(os.environ.get("CLAUDE_CODE_REMOTE") or "").lower() in ("1", "true", "yes")


def _git_run(*args, cwd=None, timeout=60):
    return subprocess.run(["git", "-C", cwd or ROOT] + list(args), capture_output=True, text=True, timeout=timeout)


def _trains_mark(trains, pid, post, session, prompt_rel):
    """The Trains card's rows with this part running (its row made under its train's root when missing)."""
    rows = [dict(r) for r in (trains.get("rows") or []) if isinstance(r, dict)]
    by = {str(r.get("part")): r for r in rows}
    part = str(post.get("part") or pid)
    root = "P-" + _norm_train(post.get("train") or "") if str(post.get("train") or "").strip() else ""
    if root and root not in by:
        by[root] = {"part": root, "parent": "", "also": [], "children": [], "post": "", "prompt": "", "session": "",
                    "state": "idle", "store": []}
        rows.append(by[root])
    row = by.get(part)
    if row is None:
        row = {"part": part, "parent": root, "also": [], "children": [], "post": pid, "prompt": "", "session": "",
               "state": "idle", "store": []}
        rows.append(row)
        by[part] = row
    row.update({"post": pid, "session": session, "state": "running", "prompt": prompt_rel or row.get("prompt") or ""})
    par = by.get(str(row.get("parent") or ""))
    if par is not None:
        par["children"] = list(par.get("children") or [])
        if part not in par["children"]:
            par["children"].append(part)
        if par.get("state") not in ("you", "done"):
            par["state"] = "running"                     # a root runs while one of its parts does (kit_cards rule)
    out = dict(trains)
    out["rows"] = rows
    out["updated_at"] = _now_z()
    return out


def _local_update(url, token, coll, did, fields):
    """Merge fields into a document of the local board (pinned to the version read): the copy the scripts use."""
    d = _board_request(url, "doc/%s/%s" % (coll, urllib.parse.quote(did, safe="")), token=token)
    if not d.get("exists", True) or not isinstance(d.get("data"), dict):
        return None
    data = dict(d["data"])
    data.update(fields(data) if callable(fields) else fields)
    _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did, "data": data,
                                                 "if_version": d.get("version", 0)}]}, token=token)
    return data


def _prompt_problem(rel):
    if not os.path.isfile(os.path.join(ROOT, rel)):
        return "no prompt %s: write it (kit.py step new writes it) and commit it" % rel
    if _git_run("ls-files", "--error-unmatch", rel).returncode != 0:
        return "the prompt %s is not committed: commit it first (a part starts from git, not from this folder)" % rel
    if _git_run("diff", "--quiet", "HEAD", "--", rel).returncode != 0:
        return "the prompt %s has changes not committed: commit them first" % rel
    return None


def _worktree_for(branch):
    """The worktree path of a branch, from `git worktree list`, or None."""
    r = _git_run("worktree", "list", "--porcelain")
    cur = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("worktree "):
            cur = line[9:]
        elif line.strip() == "branch refs/heads/%s" % branch and cur:
            return cur
    return None


def _make_worktree(branch, base, reset=False):
    """A worktree for the part on `branch`, made from the current branch (which carries .kit, the hooks and the
    prompt; `claude -w` would start from main). An existing one is reset to the current branch only when it is clean
    and holds no commit of its own (or --reset). Returns (path, error)."""
    path = _worktree_for(branch)
    if path:
        if _git_run("status", "--porcelain", cwd=path).stdout.strip():
            return None, "worktree %s (branch %s) has uncommitted work: commit or keep it, then launch again" % (path, branch)
        own = _git_run("rev-list", "--count", "%s..%s" % (base, branch)).stdout.strip()
        if own not in ("", "0") and not reset:
            return None, ("branch %s has %s commits of its own: continue that part from its branch, or launch with "
                          "--reset to start it again from %s" % (branch, own, base))
        r = _git_run("reset", "-q", "--hard", base, cwd=path)
        return (path, None) if r.returncode == 0 else (None, r.stderr.strip())
    path = os.path.join(ROOT, ".claude", "worktrees", branch)
    exists = _git_run("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0
    if exists:
        own = _git_run("rev-list", "--count", "%s..%s" % (base, branch)).stdout.strip()
        if own not in ("", "0") and not reset:
            return None, ("branch %s has %s commits of its own: continue that part from its branch, or launch with "
                          "--reset to start it again from %s" % (branch, own, base))
    r = _git_run("worktree", "add", "-q", "-B", branch, path, base)
    return (path, None) if r.returncode == 0 else (None, r.stderr.strip() or "git worktree add failed")


def _github_url():
    c = _kit_config()
    gh = str((c.get("kit") or {}).get("github") or c.get("github") or "")
    if gh:
        return gh if gh.startswith("http") else "https://github.com/%s" % gh
    u = _git_run("remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", u) or re.search(r"/git/([^/]+/[^/]+?)(?:\.git)?$", u)
    return "https://github.com/%s" % m.group(1) if m else u


def _find_attached_post(pid):
    """(board row, post) of a post on an attached board, else (None, None) (CL32: O53's launch from Harmonize found no
    post for a step under repos/board/posts)."""
    try:
        shown, _ = _attached_boards()
    except Exception:
        shown = []
    for rid in sorted(os.path.basename(d) for d in glob.glob(os.path.join(_art_dir(), "in", "repos", "*"))):
        f = os.path.join(_art_dir(), "in", "repos", rid, "posts", pid + ".json")   # the pulled Artifact copy itself
        doc = _read_json(f)
        if isinstance(doc, dict) and isinstance(doc.get("data"), dict) and "id" in doc:
            doc = doc["data"]
        if isinstance(doc, dict) and _repo_id(rid) not in {_repo_id(_kit_config().get("project") or "")}:
            b = next((x for x in shown if x["id"] == _repo_id(rid)), None)
            if b is None:
                row = next((r for r in _attached_rows() if r["id"] == _repo_id(rid)), {"id": _repo_id(rid), "name": rid, "github": ""})
                up = os.path.dirname(ROOT)
                gh = (row.get("github") or "").split("/")[-1]
                clone = next((os.path.abspath(c) for c in (row.get("path"), os.path.join(up, gh) if gh else None,
                              os.path.join(up, rid)) if c and os.path.isdir(os.path.join(c, ".git")) and os.path.abspath(c) != ROOT), None)
                b = dict(row, clone=clone, posts={pid: doc})
            return b, doc
    for b in shown:
        p = (b.get("posts") or {}).get(pid)
        if isinstance(p, dict):
            return b, p
    return None, None


def _launch_attached(pid, b, post, session_id=None, model=None, web=None, reset=False, cli=None):
    """Launch a step of an attached board from its own clone (the kit way, no hand step): the prompt this project
    wrote goes on a `part-<post>` branch of that clone (committed, pushed), then that clone's kit.py launch runs with
    the post, no seeded board and no board-mirror push (KIT_ATTACHED_CLONE), and the consuming projects to test on."""
    out = {"ok": False, "post": pid, "board": b["id"], "part": str(post.get("part") or pid)}
    clone = b.get("clone")
    if not clone or not os.path.isfile(os.path.join(clone, ".kit", "kit.py")):
        gh = b.get("github") or "<owner>/%s" % b["id"]
        return dict(out, error="step %s is on board %s, which has no clone here with the kit: `git clone https://github.com/%s "
                    "%s` (add_repo %s first on the web), then launch again" % (
                        pid, b["id"], gh, os.path.join(os.path.dirname(ROOT), gh.split("/")[-1]), gh))
    rel = "docs/prompts/%s.md" % pid
    mine = os.path.join(ROOT, rel)

    def g(*a, **kw):
        return subprocess.run(["git", "-C", clone] + list(a), capture_output=True, text=True, timeout=kw.get("timeout", 120))
    branch = "part-%s" % _slug(pid, 60)
    restore = []      # the branch the clone was on: it goes back there whatever happens
    try:
        return _launch_in_clone(pid, b, post, clone, rel, mine, branch, g, out, restore, session_id, model, web, reset)
    finally:
        if restore:
            g("checkout", "-q", restore[0])


def _launch_in_clone(pid, b, post, clone, rel, mine, branch, g, out, restore, session_id, model, web, reset):
    if not session_id and not os.path.isfile(os.path.join(clone, rel)):
        if not os.path.isfile(mine):
            return dict(out, error="no prompt %s here or in %s: plan the step first (`kit.py step new --board %s`)" % (rel, clone, b["id"]))
        dirty = g("status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:     # O53: never switch a checkout that holds someone's work
            return dict(out, error="the clone %s has uncommitted changes (%s): commit or stash them there, then launch "
                        "again; the launch puts the prompt on its own branch and comes back" % (clone, dirty.splitlines()[0][:80]))
        orig = g("symbolic-ref", "-q", "--short", "HEAD").stdout.strip() or g("rev-parse", "HEAD").stdout.strip()
        g("fetch", "-q", "origin")
        trunk = next((r for r in ("origin/main", "origin/master") if g("rev-parse", "--verify", "--quiet", r).returncode == 0), "HEAD")
        has = g("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch).returncode == 0
        r = g("checkout", "-B", branch, ("origin/" + branch) if has else trunk)
        restore.append(orig)
        if r.returncode != 0:
            return dict(out, error="could not check out %s in %s: %s" % (branch, clone, (r.stderr or r.stdout).strip()[:300]))
        if not os.path.isfile(os.path.join(clone, rel)):
            os.makedirs(os.path.dirname(os.path.join(clone, rel)), exist_ok=True)
            shutil.copyfile(mine, os.path.join(clone, rel))
            g("add", rel)
            r = g("commit", "-q", "-m", "Prompt for %s" % out["part"])
            if r.returncode != 0:
                return dict(out, error="could not commit the prompt in %s: %s" % (clone, (r.stderr or r.stdout).strip()[:300]))
        r = g("push", "-q", "-u", "origin", branch, timeout=180)
        if r.returncode != 0:
            return dict(out, error="could not push %s of %s (the new session clones it): %s" % (
                branch, b.get("github") or clone, (r.stderr or r.stdout).strip()[:300]))
    pf = os.path.join(clone, ".kit", "run", "launch-post-%s.json" % _slug(pid, 60))
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    me = _github_url().replace("https://github.com/", "")
    write_doc(pf, dict(post, consumers=[c.get("github") or c.get("name") for c in consumers(post.get("consumers"))] +
                       ([me] if me and "/" in me and me not in (post.get("consumers") or []) else [])))
    cmd = [sys.executable, "-I", os.path.join(clone, ".kit", "kit.py"), "launch", pid, "--json", "--post-file", pf]
    if session_id:
        cmd += ["--session-id", session_id]
    if model:
        cmd += ["--model", model]
    if reset:
        cmd.append("--reset")
    if web is True:
        cmd.append("--web")
    elif web is False:
        cmd.append("--cli-run")
    env = dict(os.environ, KIT_ATTACHED_CLONE="1", KIT_HOST_ROOT=ROOT)
    for k in ("KIT_BOARD_URL", "KIT_BOARD_TOKEN", "KIT_BOARD_REPO"):
        env.pop(k, None)               # the clone's own local copy, never this project's board
    try:
        r = subprocess.run(cmd, cwd=clone, capture_output=True, text=True, timeout=600, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return dict(out, error="the launch in %s did not run: %s" % (clone, e))
    try:
        res = json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        return dict(out, error="the launch in %s answered no result: %s" % (clone, (r.stderr or r.stdout).strip()[-400:]))
    res["attached"] = {"id": b["id"], "clone": clone}
    for k in ("batches", "need_pin", "push_error", "board", "dropped", "files"):
        res.pop(k, None)              # the clone's own copy was never loaded: its push text would only mislead
    if not res.get("ok"):
        return res
    # the post on the Artifact (repos/<id>/posts) and this copy of it: one ArtifactData update, pinned to the version
    # the last pull saw, so the board shows the launch (the orchestrator guard lets this file through)
    fields = {"status": "in progress", "session_id": res.get("session_id"), "branch": res.get("branch"),
              "updated_at": _now_z()}
    if not post.get("launched_at"):
        fields["launched_at"] = fields["updated_at"]
    pulled = _read_json(os.path.join(_art_dir(), "in", "repos", b["id"], "posts", pid + ".json")) or {}
    fp = os.path.join(os.path.abspath(_art_dir()), "out", "repos", b["id"], "posts", pid + ".json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    write_doc(fp, fields)
    w = {"action": "update", "url": artifact_url() or "<the Artifact's url>", "collection": "repos/%s/posts" % b["id"],
         "doc_id": pid, "file_path": fp}
    if isinstance(pulled.get("version"), int):
        w["if_version"] = pulled["version"]
    res["artifact_write"] = w
    try:
        url, token = local_board_url(), _resolve_board_token(None)
        cur = _board_request(url, "doc/posts/%s?repo=%s" % (urllib.parse.quote(pid, safe=""), urllib.parse.quote(b["id"])), token=token)
        if cur.get("exists"):
            _board_request(url, "doc/posts/%s?repo=%s" % (urllib.parse.quote(pid, safe=""), urllib.parse.quote(b["id"])),
                           {"data": dict(cur["data"], **fields)}, token=token, method="PUT")
    except Exception:
        pass
    return res


def launch(pid, url=None, token=None, web=None, session_id=None, model=None, reset=False, gate=True, cli=None, post_file=None):
    """`kit.py launch <post>`: the one way a part starts (CL13). Writes status in progress, session_id, launched_at
    and branch on its post and marks its Trains row running (the local copy; with an attached Artifact board, the
    batch that carries both there), then starts it: CLI, `claude --bg` inside a worktree made from the current branch;
    web, the create_session call to make (a script cannot call it), and `launch <post> --session-id <id>` once it
    answered. Refuses a prompt that is not committed."""
    url = url or local_board_url()
    token = _resolve_board_token(token)
    run = _launcher(cli)
    web = (run == "web") if web is None else web
    given = _read_json(post_file) if post_file else None    # CL32: the host's copy of this board's post (from the Artifact)
    if isinstance(given, dict):
        # launched for another project: no board server here (none seeded, none to auto-push); the host writes the
        # post on the Artifact with the one ArtifactData update it prints
        d, post = {"exists": True}, given
    else:
        try:
            d = _board_request(url, "doc/posts/" + urllib.parse.quote(pid, safe=""), token=token)
        except Exception as e:
            d = {"exists": False, "_error": str(e)}
        post = d.get("data") if d.get("exists", True) else None
    if not isinstance(post, dict):
        b, ap = _find_attached_post(pid)                  # a step of another repo's train (the kit's cli from Harmonize)
        if b:
            return _launch_attached(pid, b, ap, session_id=session_id, model=model, web=web, reset=reset, cli=cli)
        return {"ok": False, "post": pid, "error": "no post %s on this board%s, nor on an attached board (%s), nor in the "
                "Artifact pull (.kit/run/artifact/in/repos/<id>/posts): run `python3 -I .kit/kit.py artifact pull`, make its "
                "calls, then launch again" % (pid, (" (no local board at %s)" % url) if d.get("_error") else "",
                                              ", ".join(_attached_ids()) or "none attached")}
    part = str(post.get("part") or pid)
    rel = "docs/prompts/%s.md" % pid
    branch = str(post.get("branch") or "") if session_id else ""
    branch = branch or _slug(part, 24)
    c = _kit_config()
    name = "%s-%s" % (_slug(c.get("project") or "project"), _slug(part, 24))
    out = {"ok": True, "post": pid, "part": part, "branch": branch, "web": web, "prompt": rel}
    base = _git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    text = "Your prompt is %s: read it and do it." % rel
    cons = consumers(post.get("consumers"))
    if cons:     # CL32: a kit part tests on the projects that use it; its session must reach them from its first step
        text += (" First: add_repo (read) %s, clone next to this repo, never push it: your real-project dry run."
                 % ", ".join(c["github"] or c["name"] for c in cons))
        out["consumers"] = [c["github"] or c["name"] for c in cons]
    if session_id:                                   # the second half of a web launch: create_session answered
        sess = session_id
        launch_mark(pid, None)
    else:
        why = _prompt_problem(rel)
        if why:
            return dict(out, ok=False, error=why)
        if web:
            if _git_run("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + base).returncode != 0 and base != "HEAD":
                out["warning"] = "branch %s is not pushed: push it first, the new session clones it from GitHub" % base
            out["create_session"] = {"title": ("%s %s" % (part, str(post.get("title") or "")[:60])).strip(),
                                     "source_url": _github_url(), "source_revision": base, "outcome_branch": branch,
                                     "prompt": text}
            sess = "pending: create_session (%s)" % name
            launch_mark(pid, text)                     # CL30: the orchestrator guard lets this create_session out
        else:
            if not shutil.which("claude"):     # CL26: never an OSError on an absent binary
                return dict(out, ok=False, error=(NO_RUNTIME.replace("neither `claude` nor `agy`", "no `claude`") +
                            "; an agy orchestrator starts a part with: %s" % _launch_line(part, rel, model, cli="agy")))
            if gate and _device() is not None:
                try:
                    ok, why = _device().gate(_device().survey())
                except Exception:
                    ok, why = True, ""
                if not ok:
                    return dict(out, ok=False, error="device cap: " + why)
            wt, why = _make_worktree(branch, base, reset=reset)
            if why:
                return dict(out, ok=False, error=why)
            out["worktree"] = wt
            k = c.get("kit") or {}
            m = str(model or ((k.get("claude_cli_roles") or {}).get("part") or {}).get("model") or "inherit")
            cmd = ["claude", "--bg", "-n", name, "--permission-mode", str(k.get("claude_permission_mode") or "auto")]
            if m != "inherit":
                cmd += ["--model", m]
            cmd.append(text)
            try:
                r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=120,
                                   env=dict(os.environ, KIT_ROLE="part"))
            except (OSError, subprocess.SubprocessError) as e:
                return dict(out, ok=False, error="could not start claude: %s" % e)
            if r.returncode != 0:
                return dict(out, ok=False, error="claude --bg failed: %s" % (r.stderr or r.stdout).strip()[:300])
            sess = name
    now = _now_z()
    fields = {"status": "in progress", "session_id": sess, "branch": branch, "updated_at": now}
    if not session_id or not post.get("launched_at"):
        fields["launched_at"] = now
    out["session_id"] = sess
    if isinstance(given, dict):
        return dict(out, trains=False)
    _local_update(url, token, "posts", pid, fields)
    tr = _local_update(url, token, "posts", "_trains", lambda t: _trains_mark(t, pid, dict(post, **fields), sess, rel))
    out["session_id"] = sess
    out["trains"] = tr is not None
    if attached_board():
        out.update({"batches": [], "need_pin": []})
        try:
            r = artifact_push(url=url, token=token)
            out.update({"batches": r["batches"], "need_pin": r["need_pin"], "board": r["board"]})
        except Exception as e:  # the local copy has it; the next `artifact push` carries it
            out["push_error"] = str(e)
    return out


def launch_text(r):
    if not r.get("ok"):
        return "Not launched: %s\n" % r.get("error")
    lines = ["%s launched: post %s in progress, session %s, branch %s%s." % (
        r["part"], r["post"], r["session_id"], r["branch"], "" if r.get("trains") else " (no Trains card here to mark)")]
    if r.get("attached"):
        lines.append("It is a step of board %s: launched from its clone %s (no seeded board, no board-mirror push there)."
                     % (r["attached"]["id"], r["attached"]["clone"]))
    if r.get("consumers"):
        lines.append("Its session tests on: %s (add_repo read in its first step, named in its prompt)." % ", ".join(r["consumers"]))
    if r.get("worktree"):
        lines.append("claude --bg runs in %s (made from the current branch)." % r["worktree"])
    if r.get("warning"):
        lines.append("Warning: " + r["warning"])
    if r.get("create_session"):
        lines.append("Now start it: the claude-code-remote `create_session` tool with %s; then "
                     "`python3 -I .kit/kit.py launch %s --session-id <the session_id it returned>`."
                     % (json.dumps(r["create_session"], ensure_ascii=False), r["post"]))
    if r.get("artifact_write"):
        lines.append("Then the post on the Artifact: `ArtifactData` %s." % json.dumps(r["artifact_write"], ensure_ascii=False))
    if "batches" in r:
        lines.append(artifact_push_text(r).rstrip())
    if r.get("push_error"):
        lines.append("The Artifact batch was not built (%s): `python3 -I .kit/kit.py artifact push` later." % r["push_error"])
    return "\n".join(lines) + "\n"


def step_complete(pid, db_path=None, cli=None):
    """Status complete (the owner moves it to Done), then its review and, every kit.review.train_every completed
    steps of its train, a cross review. Reviews are idempotent (one per step, one per count)."""
    out = {"post": pid, "ok": _board_write("update", "posts", pid, {"status": "complete", "completed_at": _now_z()}),
           "reviews": []}
    rv = (_kit_config().get("kit") or {}).get("review") or {}
    if str(rv.get("auto", "all")) == "off":
        return out
    posts = _all_posts(db_path)
    p = posts.get(pid) or {}
    if p.get("review_auto") is not False:
        rc, msg = review(pid, db_path=db_path, cli=cli)
        out["reviews"].append(msg)
    train, every = p.get("train"), int(rv.get("train_every", 4) or 0)
    if train and every > 0:
        n = sum(1 for q in posts.values() if not q.get("removed") and _norm_train(q.get("train")) == _norm_train(train)
                and (str(q.get("status") or "") == "complete" or q.get("column") == "done"))
        if n and n % every == 0:
            rc, msg = review(None, train=str(train), db_path=db_path, cli=cli)
            out["reviews"].append(msg)
    return out


def _alive_sessions():
    """Names (and ids) of the Claude Code sessions running on this computer, from `claude agents --json`."""
    try:
        out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=20).stdout
        rows = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    alive = set()
    for r in rows:
        for k in ("name", "id", "sessionId"):
            if r.get(k):
                alive.add(str(r[k]))
    return alive


def _launch_traces(pid, p):
    """What a launched part leaves behind (CL13): its branch, its worktree (and what is uncommitted there), its
    transcript. None when it left nothing (never launched)."""
    branch = str(p.get("branch") or "") or _slug(p.get("part") or pid, 24)
    found, out = [], {}
    try:
        if _git_run("rev-parse", "--verify", "--quiet", "refs/heads/" + branch, timeout=10).returncode == 0:
            found.append("branch %s" % branch)
        wt = _worktree_for(branch)
        if wt:
            found.append("worktree %s" % os.path.relpath(wt, ROOT))
            out["worktree"] = os.path.relpath(wt, ROOT)
            st = _git_run("status", "--porcelain", "--untracked-files=all", cwd=wt, timeout=20).stdout
            out["dirty"] = [line[3:] for line in st.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    sess = str(p.get("session_id") or "")
    if sess and not sess.startswith("pending"):
        hits = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", sess + ".jsonl"))
        if hits:
            found.append("transcript")
    if not found:
        return None
    out["found"] = found
    return out


def _train_lines(posts, alive, h="##", board=None):
    """(lines, number of active trains) for the trains of one board's posts. `board`: an attached board
    {id, clone, branches}, whose open steps also carry their branch, last checkpoint and relaunch line."""
    trains = {}
    for pid, p in posts.items():
        if pid.startswith("_") or p.get("removed") or not str(p.get("train") or "").strip():
            continue
        t = str(p["train"]).strip()
        t = t[2:] if t.lower().startswith("p-") else t
        trains.setdefault(t, []).append((pid, p))
    out = []
    n_active = 0
    for t in sorted(trains):
        nat = lambda v: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", str(v).lower())]
        steps = sorted(trains[t], key=lambda x: (str(x[1].get("created_at") or ""), nat(x[1].get("part") or x[0])))
        open_steps = [x for x in steps if step_state(x[1]) not in ("done", "closed")]
        if not open_steps:
            continue
        n_active += 1
        out.append("")
        out.append("%s Train %s (%d/%d done)" % (h, t, len(steps) - len(open_steps), len(steps)))
        for pid, p in steps:
            col = str(p.get("column") or "info").lower()
            st = str(p.get("status") or "").lower()
            if step_state(p) in ("done", "closed"):
                if (pid, p) not in steps[-2:]:
                    continue
                out.append("- [x] %s `%s` %s" % (p.get("part") or pid, pid, str(p.get("title") or "")[:80]))
                continue
            box = ("[h]" if step_state(p) == "held" else "[?]" if col == "question" else
                   ("[!]" if col == "todo" else ("[~]" if "progress" in st else "[ ]")))
            sess = str(p.get("session_id") or "")
            if sess:
                life = "unknown" if alive is None else ("running" if sess in alive else "not running")
                if life == "unknown" and sess.startswith(("session_", "pending")):
                    life = "web session: check it with get_session"
                who = " · session %s (%s)" % (sess, life)
            else:
                who = " · planned, not launched" if st == "planned" else ""
            if board is None and st in ("planned", "in progress") and not (sess and alive is not None and sess in alive):
                traces = _launch_traces(pid, p)
                if traces and not (sess.startswith(("session_", "pending")) and alive is None):
                    who = " · launched, died (%s; no live session)%s" % (", ".join(traces["found"]), (
                        ": uncommitted in %s: %s" % (traces["worktree"], ", ".join(traces["dirty"][:8]))
                        if traces.get("dirty") else ""))
            quiet = ""
            if "progress" in st:
                last = _utc(str(p.get("updated_at") or p.get("created_at") or ""))
                if last:
                    mins = int((datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                                .replace(tzinfo=timezone.utc)).total_seconds() // 60)
                    if mins >= 30 and "(not running)" in who:
                        quiet = " · quiet %d min, not running: stalled (continue it from its branch)" % mins
                    elif mins >= 30:
                        quiet = " · quiet %d min: send it a save point (commit and push now)" % mins
            out.append("- %s %s `%s` %s%s%s%s" % (box, p.get("part") or pid, pid, str(p.get("title") or "")[:80],
                                                  (" · " + st) if st and st != "planned" else "", who, quiet))
            if board is not None:
                out.extend(_attached_step_detail(board, pid, p, "running" not in who or "not running" in who))
    return out, n_active


# ---------------------------------------------------------------- step states (CL28)
# A post's status says more than its column: Harmonize's HL1 (column info, status "done") showed as running, and a held
# part (status "held: waits for VR5") had no state of its own. Same rules as kit_cards.post_state.
STEP_DONE = re.compile(r"^\s*(done|complete|completed|finished|merged)\s*($|[,:;.(\-])", re.I)
STEP_CLOSED = re.compile(r"^\s*(closed|dropped|superseded|abandoned)\b", re.I)
STEP_HELD = re.compile(r"^\s*(held|on[ -]?hold|hold|paused|parked)\b", re.I)


def step_state(p):
    """As kit_cards.post_state: done, closed, held, you, running, idle (a done post is never running)."""
    col, st = str(p.get("column") or ""), str(p.get("status") or "")
    if col == "done" or STEP_DONE.match(st):
        return "done"
    if STEP_CLOSED.match(st):
        return "closed"
    if STEP_HELD.match(st):
        return "held"
    if col in ("question", "todo"):
        return "you"
    return "running" if "progress" in st.lower() else "idle"


def _train_key(pid, p):
    t = str(p.get("train") or "").strip()
    if t:
        return t[2:] if t.lower().startswith("p-") else t
    mm = re.match(r"^([A-Za-z]+)\d", str(p.get("part") or pid))
    return mm.group(1).upper() if mm else ""


def _age(stamp, now):
    try:
        d = now - iso(stamp)
    except (ValueError, TypeError, AttributeError):
        return "?"
    h = int(d.total_seconds() // 3600)
    return "%dd %dh" % (h // 24, h % 24) if h >= 24 else "%dh" % h if h else "%dmin" % max(0, int(d.total_seconds() // 60))


def _short(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "\u2026"


def _resume_extras(posts, comments, now=None, days=3, limit=12, session=None):
    """CL28 (the owner: "fold them in to resume"): what the task list does not show. The `_orchestrator` post naming
    another session (the clerk sends his words there: a stale id made it start fresh orchestrators), each open
    question's age, default and the steps that name it, and the owner's decisions of the last `days` days (comments,
    Likes, answers; a chat decision is the comment the orchestrator wrote for it)."""
    now = now or datetime.now(timezone.utc)
    out = []
    orch = posts.get("_orchestrator") if isinstance(posts.get("_orchestrator"), dict) else {}
    named = str(orch.get("session_id") or "")
    if session and named != session:
        out += ["", "MISMATCH: `_orchestrator` names session %s, you are %s: write your id on it (and on each attached "
                "board's repos/<id>/posts/_orchestrator) before you answer." % (named or "none", session)]
    posts = {k: v for k, v in posts.items() if isinstance(v, dict) and not k.startswith("_") and not v.get("removed")}
    st = {k: step_state(v) for k, v in posts.items()}
    part = {k: str(v.get("part") or k) for k, v in posts.items()}
    qs = sorted((k for k in posts if st[k] == "you"), key=lambda k: str(posts[k].get("created_at") or ""))
    out += ["", "## Open questions (%d)" % len(qs)]
    for k in qs:
        p, pt = posts[k], part[k]
        wr = re.compile(r"(?<![\w-])(%s|%s)(?![\w])" % (re.escape(pt), re.escape(k)))
        blocks = []
        for j, q in posts.items():
            if j == k or part[j] == pt or st[j] in ("done", "closed"):
                continue
            text = " ".join(str(q.get(f) or "") for f in ("parent", "status", "blocked_by", "title"))
            if str(q.get("parent") or "") in (pt, k) or wr.search(text):
                blocks.append("%s (%s)" % (part[j], _short(q.get("status"), 40) if q.get("status") else st[j]))
        dflt = p.get("default_option") or ""
        age = "date unknown" if p.get("date_unknown") else _age(str(p.get("created_at") or ""), now) + " old"
        out.append("- %s %s: %s%s%s" % ("`%s`" % k if pt == k else "%s `%s`" % (pt, k), age, _short(p.get("title"), 90),
                                               (" · default: " + _short(dflt, 60)) if dflt else "",
                                               (" · blocks " + ", ".join(list(dict.fromkeys(blocks))[:4])) if blocks else ""))
    if not qs:
        out.append("none")
    owner = CONFIG.get("fields", {}).get("owner", "owner")
    since_t = now - timedelta(days=days)
    his = []
    for c in comments.values():
        if not isinstance(c, dict) or c.get("from") not in (owner, "viewer") or not c.get("created_at"):
            continue
        try:
            if iso(c["created_at"]) < since_t:
                continue
        except ValueError:
            continue
        if c.get("like"):
            what = "Like" + ((" on comment " + str(c["comment_id"])) if c.get("comment_id") else "")
        elif c.get("answer"):
            what = "Answer: " + _short(c.get("answer"), 80)
        else:
            what = '"%s"' % _short(c.get("text"), 140)
        his.append((c["created_at"], "%s on %s: %s" % (c["created_at"][5:16].replace("T", " "), c.get("post_id"), what)))
    his.sort()
    out += ["", "## The owner's decisions, last %d days (%d)" % (days, len(his))]
    if len(his) > limit:
        out.append("(the %d most recent; the rest: `kit.py since %s`)" % (limit, zstamp(since_t)))
    out.extend("- " + line for _, line in his[-limit:])
    if not his:
        out.append("none")
    return out

def adopt_trains(url=None, token=None, dry_run=False, db_path=None, m=None):
    """CL28, once per board: a post made before the kit's trains has a `part` but no `train`, so neither `resume` nor
    the orchestrator's task list sees it (Harmonize: HL6, VR5, HL5, the A3 posts). Each gets its train from its part's
    letters (HL6 -> HL, A3-data -> A), written as `launch` writes a post: on the local board (started if needed, as
    `artifact load` does), then `artifact push` makes the pinned batches when the board is an Artifact."""
    posts, _ = _load_board_docs(m or ROOT, db_path=db_path)
    plan = {}
    for pid, p in sorted(posts.items()):
        if pid.startswith("_") or not isinstance(p, dict) or p.get("removed") or str(p.get("train") or "").strip():
            continue
        t = _train_key(pid, p)
        if t:
            plan.setdefault(t, []).append(pid)
    if dry_run or not plan:
        return {"trains": plan, "written": 0}
    url = url or local_board_url()
    if not _ensure_local_board(url):
        raise Conflict("no local board at %s (run `python3 -I .kit/web_takeover.py` first)" % url)
    token = _resolve_board_token(token)
    n = 0
    for t, ids in plan.items():
        for pid in ids:
            if _local_update(url, token, "posts", pid, {"train": t}) is not None:
                n += 1
    out = {"trains": plan, "written": n}
    if attached_board():
        out["push"] = artifact_push(url=url, token=token)
    return out


def adopt_text(r):
    n = sum(len(v) for v in r["trains"].values())
    lines = ["%d post(s) without a train, in %d train(s): %s" % (n, len(r["trains"]), ", ".join(
        "%s (%d)" % (t, len(v)) for t, v in sorted(r["trains"].items())))]
    if r.get("written"):
        lines.append("%d written on the local board." % r["written"])
    if r.get("push"):
        lines.append(artifact_push_text(r["push"]).rstrip())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the command index (CL28)
# The owner, 2026-11-23: "Why are all the sessions skipping the kit's scripts???" A session asked to build something
# wrote new code before looking for the kit's own command (CL28 itself: a brief that repeated resume, an adoption that
# bypassed artifact load). `kit.py commands [word]` is the index a session reads BEFORE it writes any code; a test
# keeps every kit.py command in it, so it cannot go stale.
COMMANDS = [
    # (need, command, what it does)
    ("read the board", "kit.py resume", "every train's open steps (the task list), live sessions, open questions (age, default, what each blocks), the owner's decisions of the last 3 days, the owner's unanswered comments, attached boards; run it before answering the owner"),
    ("read the board", "kit.py resume --role orchestrator|part|clerk|review [--post P]", "what that role needs and nothing else: a session starts from it alone (lessons, launch workflow, tests, how to report)"),
    ("memory", "kit.py lessons [--role R] [--files F...] | lessons check", "the lessons a role needs / refuse untagged ones and ones only a project doc holds"),
    ("memory", "kit.py lesson add <id> --roles R,.. --title T --text X [--files G,..] [--from \"doc id\"]", "a lesson into the memory, tagged with its roles"),
    ("read the board", "kit.py train <train> [--zoom <part>]", "a train's steps: verdict, bars, check, breaks, handoff of each"),
    ("read the board", "kit.py post <id> [--head N]", "one post and its comments"),
    ("read the board", "kit.py open [--since ISO]", "the owner's comments not answered yet"),
    ("read the board", "kit.py since <ISO>", "what changed on the board since a time"),
    ("read the board", "kit.py session <post>", "the session a post names, and what it left"),
    ("read the board", "kit.py search <words>", "find posts and comments by words"),
    ("read the board", "kit.py queue [--runner R]", "the steps waiting for a runner"),
    ("read the board", "kit.py sections", "which ## sections the owner opens and folds"),
    ("read the board", "kit.py report / doctor", "the board's size, columns, oldest question, missing files / its health"),
    ("read the board", "kit.py portfolio", "every project of this owner and where each stands"),
    ("read the board", "board_cli.py get|list|query <coll> ...", "raw documents of the local board (prefer the lookups above)"),
    ("read the board", "board_cli.py inbox", "unread owner comments, Likes and option taps, compact"),
    ("write the board", "TaskCreate {train, part} (Claude Code) / kit.py step new", "plan a step: its post and its prompt docs/prompts/<id>.md, never by hand"),
    ("write the board", "kit.py step start|complete|drop", "move a step on its train"),
    ("write the board", "kit.py adopt", "once: give posts made before the trains their train (from their part), then artifact push"),
    ("write the board", "board_cli.py set|update|batch <coll> <id> --data JSON", "write a post or a comment on the local board"),
    ("write the board", "board_cli.py reply <post> <comment> --text T", "answer the owner's comment in the owner's thread (marks it read)"),
    ("write the board", "kit_cards.py (agent kit-cards)", "rebuild the Where we are card and the Trains card, never by hand"),
    ("launch and finish", "kit.py launch <post>", "start a part: post in progress, Trains row running, then claude --bg or the create_session call"),
    ("launch and finish", "kit.py capacity / device.py run -- <cmd>", "the device's session cap / run a heavy job when it fits in memory"),
    ("launch and finish", "kit.py done <post>", "check a step's post: format, artifacts, check passes, breaks makes it fail"),
    ("launch and finish", "kit.py review <post> | --train T", "start the review of a step or a train"),
    ("launch and finish", "kit.py pair <post>", "a blind before/after picture pair for a visual step"),
    ("launch and finish", "end_session.py", "commit check, push, merge onto the trunk when allowed"),
    ("launch and finish", "handover.py --role R", "a context reset: a fresh session under the same name"),
    ("Artifact and web", "web_takeover.py [save]", "a web session: the board from git and the steps to continue / the board back to git"),
    ("Artifact and web", "kit.py artifact pull|load|push|sent|pin", "the Artifact board to the local copy (starts the local board) and back, as pinned batches"),
    ("Artifact and web", "kit.py reconcile", "the board here vs its git copy: the newer copy of each document wins"),
    ("Artifact and web", "kit_artifact_sync.py", "the repo's documents and files into the Artifact for its viewers"),
    ("Artifact and web", "update_board.py", "the Artifact's page to the kit's current page"),
    ("git copy", "kit.py init|sync|sync-db|reads|receive|publish|diff|push", "the board's git copy (board-mirror): make it, fill it, compare it, send it"),
    ("memory", "kit.py compact", "post every open awaits point (a question post) and open issue (an issue post, never doubled) on the board, then archive the awaits file, the states and issues the board now holds, and old lessons; on an Artifact board nothing leaves memory until `artifact push` sent the posts; refused if the project's memory checker goes red (--dry-run lists the posts)"),
    ("setup", "kit.py welcome [--post]", "the welcome post: what the board does and what can run now"),
    ("setup", "install.py <project> [--update]", "install or update the kit in a project (every present runtime)"),
    ("setup", "kit_import.py / step0_scan.py", "a person's state from their chats / a project's step 0 profile"),
    ("index", "kit.py commands [word]", "this list"),
]


def commands_text(word=None):
    rows = [r for r in COMMANDS if not word or word.lower() in " ".join(r).lower()]
    out = ["# The kit's commands (python3 -I .kit/<script>; read this before writing any code: reuse, never rebuild)"]
    need = None
    for n, cmd, what in rows:
        if n != need:
            out += ["", "## " + n]
            need = n
        out.append("- `%s`: %s" % (cmd, what))
    if not rows:
        out.append("nothing matches %r: `kit.py commands` lists all" % word)
    return "\n".join(out) + "\n"

# ---------------------------------------------------------------- attached boards (cross-repo, kit_repos/_index)
def _repo_id(name):
    """Same id as board_server.py and kit_artifact_sync.py give an attached repo."""
    return re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")


def _read_mirror_board(clone):
    """{posts, comments} of a clone's board: its local board database, else its board-mirror branch (git objects,
    nothing checked out), as kit_artifact_sync.py boards reads it."""
    out = {"posts": {}, "comments": {}}
    db = os.path.join(clone, ".kit", "board_data", "board.db")
    if os.path.isfile(db):
        try:
            p, c = _load_board_docs(clone, db_path=db)
            if p or c:
                return {"posts": p, "comments": c}
        except sqlite3.Error:
            pass
    try:
        branch = json.load(open(os.path.join(clone, ".kit", "config.json"), encoding="utf-8")).get("mirror", {}).get("branch")
    except (OSError, ValueError, AttributeError):
        branch = None
    branch = branch or "board-mirror"

    def git(*args):
        return subprocess.run(["git", "-C", clone] + list(args), capture_output=True, timeout=60)
    try:
        git("fetch", "-q", "origin", "%s:refs/remotes/origin/%s" % (branch, branch))
        ref = next((r for r in ("origin/" + branch, branch) if git("rev-parse", "--verify", "--quiet", r).returncode == 0), None)
        if not ref:
            return out
        arc = git("archive", "--format=tar", ref, "board")
    except (OSError, subprocess.SubprocessError):
        return out
    if arc.returncode != 0 or not arc.stdout:
        return out
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(arc.stdout), mode="r:*") as tf:
        for mem in tf.getmembers():
            parts = tar_name(mem.name).split("/")
            if not mem.isfile() or len(parts) != 3 or parts[0] != "board" or parts[1] not in out or not parts[2].endswith(".json"):
                continue
            try:
                data = json.loads(tf.extractfile(mem).read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("data"), dict) and set(data) <= {"id", "data", "version", "updated_at"}:
                data = data["data"]
            if isinstance(data, dict):
                out[parts[1]][parts[2][:-5]] = data
    return out


def _attached_rows():
    """Which boards are attached to this one, without reading them: [{id, name, github, path}], from the config's
    portfolio, the Artifact's kit_repos/_index and the repos/<id>/ folders a pull saved in the local copy's in/."""
    cfg = _kit_config()
    me = {_repo_id(cfg.get("project") or os.path.basename(ROOT))}
    rows = []
    for item in cfg.get("portfolio") or []:
        if isinstance(item, dict):
            gh = str(item.get("github") or "")
            gh = gh[len("github:"):] if gh.startswith("github:") else gh
            rows.append({"name": str(item.get("name") or gh.split("/")[-1] or os.path.basename(str(item.get("path") or ""))),
                         "github": gh.strip("/"), "path": item.get("path")})
    in_dir = os.path.join(_art_dir(), "in")
    idx = _read_json(os.path.join(in_dir, "kit_repos", "_index.json")) or {}
    if isinstance(idx.get("data"), dict):
        idx = idx["data"]
    for r in idx.get("repos") or []:
        if isinstance(r, dict):
            rows.append({"name": str(r.get("name") or r.get("id") or ""), "id": r.get("id"), "github": str(r.get("github") or "")})
    for d in sorted(glob.glob(os.path.join(in_dir, "repos", "*")) + glob.glob(os.path.join(ROOT, ".kit", "attached", "*"))):
        if os.path.isdir(d):
            rows.append({"name": os.path.basename(d), "id": os.path.basename(d), "github": ""})
    boards, seen = [], {}
    for r in rows:
        rid = _repo_id(r.get("id") or r.get("name") or "")
        if not rid or rid == "all" or rid in me:
            continue
        if rid in seen:
            b = seen[rid]
            b["github"] = b["github"] or r.get("github") or ""
            b["path"] = b.get("path") or r.get("path")
            continue
        seen[rid] = dict(r, id=rid)
        boards.append(seen[rid])
    return boards


def _attached_ids():
    return [b["id"] for b in _attached_rows()]


def _attached_boards():
    """The boards attached to this one: [{id, name, github, clone, posts, comments, source}]. Which boards: the
    config's portfolio and the Artifact's kit_repos/_index (when `artifact pull` or an ArtifactData list saved it in
    the local copy's in/ folder). Their posts and comments: the board's clone (its local board, else its board-mirror
    branch), with what the Artifact holds under repos/<id>/ laid over it (comments written on the page land there)."""
    boards = _attached_rows()
    in_dir = os.path.join(_art_dir(), "in")
    up = os.path.dirname(ROOT)
    for b in boards:
        gh_name = b["github"].split("/")[-1] if b["github"] else ""
        cands = [b.get("path"), os.path.join(up, gh_name) if gh_name else None, os.path.join(up, b["name"]), os.path.join(up, b["id"])]
        b["clone"] = next((os.path.abspath(c) for c in cands if c and os.path.isdir(os.path.join(c, ".git"))
                           and os.path.abspath(c) != ROOT), None)
        store = os.path.join(ROOT, ".kit", "attached", b["id"])     # CL32: what `artifact load` put here, no clone
        b["store"] = store if os.path.isfile(os.path.join(store, ".kit", "board_data", "board.db")) else None
        docs = _read_mirror_board(b["clone"]) if b["clone"] else {"posts": {}, "comments": {}}
        sources = ["its clone %s" % b["clone"]] if b["clone"] and (docs["posts"] or docs["comments"]) else []
        if b["store"]:
            try:
                sp_, sc_ = _load_board_docs(b["store"], db_path=os.path.join(b["store"], ".kit", "board_data", "board.db"))
                for coll, got in (("posts", sp_), ("comments", sc_)):
                    for k, v in got.items():
                        docs[coll].setdefault(k, v)
                if sp_ or sc_:
                    sources.append("the Artifact's repos/%s loaded here (.kit/attached/%s)" % (b["id"], b["id"]))
            except sqlite3.Error:
                pass
        n_art = 0
        for coll in ("posts", "comments"):
            for f in sorted(glob.glob(os.path.join(in_dir, "repos", b["id"], coll, "*.json"))):
                d = _read_json(f)
                if isinstance(d, dict) and isinstance(d.get("data"), dict) and "id" in d:
                    d = d["data"]
                if isinstance(d, dict):
                    docs[coll][os.path.basename(f)[:-5]] = d
                    n_art += 1
        if n_art:
            sources.append("the Artifact's repos/%s (local copy)" % b["id"])
        b.update(docs)
        b["source"] = " + ".join(sources) or "nothing here"
        b["branches"] = []
        if b["clone"]:
            try:
                rb = subprocess.run(["git", "-C", b["clone"], "branch", "-r", "--format=%(refname:short)"],
                                    capture_output=True, text=True, timeout=30).stdout
                b["branches"] = [x.split("/", 1)[1] for x in rb.splitlines() if x.startswith("origin/") and "HEAD" not in x]
            except (OSError, subprocess.SubprocessError):
                pass
    shown = [b for b in boards if b["clone"] or b["store"] or b["posts"] or b["comments"]]
    skipped = [b["id"] for b in boards if b not in shown]
    return shown, skipped


def _attached_step_detail(board, pid, p, stopped):
    """Indented lines under an attached board's open step: branch, last checkpoint, relaunch line."""
    part = str(p.get("part") or "")
    s = re.escape(part.lower())
    brs = [x for x in board.get("branches") or [] if part and re.search(r"(^|[-/_])%s$" % s, x.lower())]
    if p.get("branch") and p["branch"] not in brs:
        brs.insert(0, str(p["branch"]))
    out = []
    if brs:
        ck = ""
        if board.get("clone"):
            try:
                ck = subprocess.run(["git", "-C", board["clone"], "log", "-1", "--format=%h %cI %s", "origin/" + brs[0]],
                                    capture_output=True, text=True, timeout=30).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                ck = ""
        out.append("  branch %s%s" % (", ".join("`%s`" % x for x in brs), (" · last checkpoint %s" % ck[:100]) if ck else ""))
    elif p.get("updated_at"):
        out.append("  no branch found · last update %s" % str(p["updated_at"])[:16].replace("T", " "))
    st = str(p.get("status") or "").lower()
    if stopped and ("progress" in st or st == "planned") and str(p.get("column") or "info").lower() == "info":
        prompt = "docs/prompts/%s.md" % pid
        has = bool(board.get("clone")) and os.path.exists(os.path.join(board["clone"], prompt))
        where = "in %s's clone (%s)" % (board["id"], board["clone"]) if board.get("clone") else "in a clone of %s" % (board.get("github") or board["id"])
        if has or brs:
            out.append("  relaunch %s: %s" % (where, _launch_line(part or pid, prompt) if has else
                       "continue from branch `%s` (no prompt file %s there)" % (brs[0], prompt)))
        elif p.get("session_id"):
            out.append("  relaunch %s: no prompt file and no branch for it; ask its orchestrator or the owner" % where)
    return out


def resume(m, db_path=None, alive=None, cli=None, session=None, now=None):
    """Everything a fresh orchestrator needs after a context reset, from the board alone: each active train's steps
    (the task list to rebuild, one TaskCreate each with metadata {train, part}), which part sessions still run, and
    what of the owner's waits for an answer; then the same for each attached board (kit_repos/_index), under its
    own heading, with its id so a reply goes to repos/<id>/comments."""
    active = _active_cli(cli)
    posts, comments = _load_board_docs(m, db_path=db_path)
    if alive is None:
        alive = None if active == "agy" else _alive_sessions()
    out = ["# Resume (from the board; nothing else carries over)"]
    lines, n_active = _train_lines(posts, alive)
    out.extend(lines)
    if not n_active:
        out.append("")
        out.append("No train has an open step.")
    out.extend(_resume_extras(posts, comments, now=now, session=session))
    dl = device_line()
    if dl:
        out.append("")
        out.append("## This device")
        out.append(dl + ("" if dl.startswith("Device: cloud") else " A launch past the cap is refused "
                   "(`.kit/device.py --gate`, the guard): the step waits, planned, until a session ends; launch it then."))
    out.append("")
    out.append("## The owner's unanswered comments")
    try:
        out.append(open_(m))
    except Exception as e:  # the copy may be mid-sync; the orchestrator can rerun `kit.py open`
        out.append("(could not read: %s)" % e)
    try:
        boards, skipped = _attached_boards()
    except Exception as e:  # never lose the host's resume over an attached board
        boards, skipped = [], []
        out.append("")
        out.append("(attached boards: could not read: %s)" % e)
    for b in boards:
        out.append("")
        out.append("## Attached board %s%s" % (b["id"], (" (%s)" % b["github"]) if b.get("github") else ""))
        out.append("Read from %s." % b["source"] + ("" if b.get("clone") else
                   " No clone here: add_repo %s, git clone it next to this repo, run resume again." % (b.get("github") or b["id"])))
        lines, n = _train_lines(b["posts"], alive, h="###", board=b)
        out.extend(lines)
        if not n:
            out.append("No train of %s has an open step." % b["id"])
        out.append("")
        out.append("### The owner's unanswered comments on %s (answer in repos/%s/comments)" % (b["id"], b["id"]))
        try:
            ol = _open_of(b["comments"], empty=False)
        except Exception as e:
            ol = ["(could not read: %s)" % e]
        out.extend(["[repo %s] %s" % (b["id"], x) for x in ol] or ["nothing of his open on %s" % b["id"]])
    if skipped:
        out.append("")
        out.append("(Not read: %s: listed as attached, but no clone here and nothing of it in the local copy.)"
                   % ", ".join(skipped))
    if boards and artifact_url() and not glob.glob(os.path.join(_art_dir(), "in", "repos", "*")):
        out.append("")
        out.append("(Comments written on the Artifact page on an attached board live only there: `ArtifactData` `list`, `url` "
                   "%s, `collection: \"repos/<id>/comments\"` (and `repos/<id>/posts`), `out_dir: \"%s\"`, then resume again.)"
                   % (artifact_url(), os.path.join(_art_dir(), "in", "repos", "<id>", "comments")))
    out.append("")
    if active == "agy":
        out.append("Resume in agy: cross-check each session UUID above with `manage_subagents(Action=\"list\")`. "
                   "Plan and update steps with `python3 -I .kit/kit.py step new|start|complete|drop` (the train IS your "
                   "task list). A step whose session is not running and not done: check its branch (`git branch -a`) "
                   "and continue it with `invoke_subagent(..., Workspace=\"share\")` or ask the owner."
                   + (" Each step belongs to a board: this one (\"%s\") or the attached board named in its heading; "
                      "an attached board's step is planned, updated and continued in that board's clone (its relaunch "
                      "line), and a reply to the owner's comment there goes to repos/<id>/comments."
                      % _repo_id(_kit_config().get("project") or os.path.basename(ROOT)) if boards else ""))
    elif boards:
        out.append("Rebuild your task list: one TaskCreate per open step above, subject \"<PART> <title>\", metadata "
                   "{\"board\": \"<id>\", \"train\": \"<train>\", \"part\": \"<PART>\"} (board: this board's id \"%s\", or the "
                   "attached board's id from its heading; the existing post is adopted, not duplicated); TaskUpdate "
                   "in_progress for each [~]. A step whose session is \"not running\" and not done: relaunch it (an attached "
                   "board's step from its own clone, with its relaunch line) or ask the owner."
                   % (_repo_id(_kit_config().get("project") or os.path.basename(ROOT))))
    else:
        out.append("Rebuild your task list: one TaskCreate per open step above, subject \"<PART> <title>\", metadata "
                   "{\"train\": \"<train>\", \"part\": \"<PART>\"} (the existing post is adopted, not duplicated); "
                   "TaskUpdate in_progress for each [~]. A step whose session is \"not running\" and not done: relaunch it or "
                   "ask the owner.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CL32: resume per role, the lessons, real-project tests
ROLES = ("orchestrator", "part", "clerk", "review")
LESSON_HEAD = re.compile(r"^##\s+([A-Za-z0-9][\w.-]*)\s*:\s*(.+?)\s*$")
DOC_LESSON = re.compile(r"(?im)(?:^\s*(?:[#>*_-]+\s*|\d+[.)]\s*)*(?:\*\*|__)?|\*\*|__)lesson\s+([0-9]+[a-z]?)\b")
# a change that touches one of these needs a dry run on a real project before `done` (CL32: CL29's compact passed the
# kit's suite and failed on Harmonize; a kit part's session had only anlonlt/board)
REAL_PROJECT_AREAS = ("compact", "install", "resume", "hook", "artifact")


def _kit_lessons_paths():
    """The kit's own lessons (shipped with the kit: kit/lessons.md here, .kit/lessons.md in a project), then this
    project's (the memory's lessons/*.md)."""
    out = [p for p in (os.path.join(ROOT, "kit", "lessons.md"), os.path.join(ROOT, ".kit", "lessons.md")) if os.path.isfile(p)]
    out = out[:1]                                    # the kit repo has both (the source and its install): read one
    out += sorted(glob.glob(os.path.join(_resolve_memory_dir(), "lessons", "*.md")))
    return out


def parse_lessons(text, source=""):
    """[{id, title, roles, files, from, text, source}] from lesson Markdown: `## <id>: <title>`, then `roles:`,
    optional `files:` (globs) and `from:` (where it was first written) lines, then the lesson itself."""
    out, cur = [], None
    for line in text.splitlines():
        m = LESSON_HEAD.match(line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "roles": [], "files": [], "from": "", "text": [], "source": source}
            out.append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^(roles|files|from)\s*:\s*(.*)$", line.strip(), re.I)
        if kv and not cur["text"]:
            k, v = kv.group(1).lower(), kv.group(2).strip()
            cur[k] = v if k == "from" else [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
            continue
        if line.strip() or cur["text"]:
            cur["text"].append(line.rstrip())
    for c in out:
        c["roles"] = [r.lower() for r in c["roles"]]
        c["text"] = "\n".join(c["text"]).strip()
    return out


def all_lessons():
    out = []
    for p in _kit_lessons_paths():
        try:
            with open(p, encoding="utf-8") as fh:
                out += parse_lessons(fh.read(), os.path.relpath(p, ROOT))
        except OSError:
            continue
    return out


def _touches(globs, files):
    return any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in globs for f in files)


def lessons_for(role, files=None):
    """The lessons a role needs: tagged with it (or `all`); for a part with files, a lesson with `files:` only when it
    touches one of them (a lesson with no files applies to every part)."""
    out = []
    for les in all_lessons():
        if role not in les["roles"] and "all" not in les["roles"]:
            continue
        if role == "part" and les["files"] and files is not None and not _touches(les["files"], files):
            continue
        out.append(les)
    return out


def lessons_check():
    """Errors: a lesson with no roles or an unknown role (refused: nobody would get it); a lesson a project doc
    names (`Lesson 51a` in docs/ORCHESTRATION.md) that the memory does not hold (refused: no resume reads docs)."""
    err = []
    les = all_lessons()
    for x in les:
        if not x["roles"]:
            err.append("lesson %s (%s): no roles: add `roles: %s` under its heading" % (x["id"], x["source"], "|".join(ROLES)))
        bad = [r for r in x["roles"] if r not in ROLES and r != "all"]
        if bad:
            err.append("lesson %s (%s): unknown role %s (roles: %s, all)" % (x["id"], x["source"], ", ".join(bad), ", ".join(ROLES)))
    known = {x["id"].lower() for x in les} | {x["from"].split()[-1].lower() for x in les if x["from"]}
    docs = [p for p in glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
            if "%sprompts%s" % (os.sep, os.sep) not in p] + glob.glob(os.path.join(ROOT, "*.md"))
    for p in sorted(set(docs)):
        try:
            with open(p, encoding="utf-8") as fh:
                ids = DOC_LESSON.findall(fh.read())
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(p, ROOT)
        for i in sorted(set(x.lower() for x in ids)):
            if i not in known:
                err.append("lesson %s lives only in %s: move it into the memory, tagged with its roles: "
                           "`python3 -I .kit/kit.py lesson add %s --roles <roles> --from \"%s %s\" --title ... --text ...`"
                           % (i, rel, i, rel, i))
    return err


def lesson_add(lid, roles, title, text, files=(), frm=""):
    roles = [r.strip().lower() for r in roles if r.strip()]
    if not roles:
        return {"error": "a lesson needs its roles (--roles %s): one no role reads is never read" % ",".join(ROLES)}
    bad = [r for r in roles if r not in ROLES and r != "all"]
    if bad:
        return {"error": "unknown role %s (roles: %s, all)" % (", ".join(bad), ", ".join(ROLES))}
    lid = re.sub(r"[^\w.-]+", "-", str(lid)).strip("-")
    if not lid or not str(title).strip() or not str(text).strip():
        return {"error": "a lesson needs an id, a title and its text"}
    d = os.path.join(_resolve_memory_dir(), "lessons")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.md" % lid)
    body = ["## %s: %s" % (lid, title.strip()), "roles: %s" % ", ".join(roles)]
    if files:
        body.append("files: %s" % ", ".join(files))
    if frm:
        body.append("from: %s" % frm)
    body += ["", text.strip(), ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body))
    return {"ok": True, "lesson": lid, "path": os.path.relpath(path, ROOT)}


def _lesson_lines(les):
    out = []
    for x in les:
        out.append("- **%s** %s%s: %s" % (x["id"], x["title"], (" (files %s)" % ", ".join(x["files"])) if x["files"] else "",
                                          x["text"].replace("\n", " ")))
    return out or ["- (none tagged for this role)"]


def _changed_files(root=ROOT, base=None):
    """The files this branch changes against the trunk (committed and not)."""
    files = set()
    for ref in ([base] if base else []) + ["origin/main", "origin/master", "main"]:
        r = subprocess.run(["git", "-C", root, "merge-base", "HEAD", ref], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            d = subprocess.run(["git", "-C", root, "diff", "--name-only", r.stdout.strip()], capture_output=True, text=True)
            files.update(x for x in d.stdout.splitlines() if x)
            break
    st = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout
    files.update(l[3:].split(" -> ")[-1] for l in st.splitlines() if len(l) > 3)
    return sorted(files)


def real_project_areas(root=ROOT, base=None):
    """Which of compact, install, resume, hooks, artifact this branch's change touches: by file (install.py,
    kit/hooks/, kit_artifact_sync.py...) and by the functions its diff of scripts/kit.py changes."""
    hit = set()
    files = _changed_files(root, base)
    for f in files:
        fl = f.lower()
        if fl.startswith("tests/") or fl.startswith("docs/"):
            continue
        for a in REAL_PROJECT_AREAS:
            if a in fl:
                hit.add(a)
    if any(f.endswith("kit.py") for f in files):
        r = subprocess.run(["git", "-C", root, "diff", "-U0", "origin/main", "--", "scripts/kit.py", ".kit/kit.py"],
                           capture_output=True, text=True)
        for m in re.finditer(r"^@@[^@]*@@\s*def\s+(\w+)|^[+-]\s*def\s+(\w+)", r.stdout, re.M):
            name = (m.group(1) or m.group(2) or "").lower()
            for a in REAL_PROJECT_AREAS:
                if a in name:
                    hit.add(a)
    return sorted(hit)


def check_real_project(p, root=ROOT):
    """A post whose change touches compact, install, resume, hooks or artifact names a dry run on a real project:
    `real_project: {project, command, output}` (or a list of them), the command run in a clone of that project and
    what it printed. Refused without it: the kit's own suite passed CL29 while Harmonize stayed red."""
    areas = real_project_areas(root)
    if not areas:
        return []
    rp = p.get("real_project")
    rows = rp if isinstance(rp, list) else [rp] if rp else []
    good = [r for r in rows if isinstance(r, dict) and str(r.get("project") or "").strip()
            and str(r.get("command") or "").strip() and str(r.get("output") or "").strip()]
    if good:
        return []
    cons = ", ".join(c.get("github") or c.get("name") for c in consumers()) or "a project that uses the kit"
    return ["real_project: your change touches %s; run it on a real project (%s: add_repo read, clone it next to this "
            "repo, never push it) and put {\"project\", \"command\", \"output\"} in the post's real_project"
            % (", ".join(areas), cons)]


def consumers(extra=None):
    """The projects a kit step is tested on (config kit.consumers: [{name, github, check}]), plus `extra` (the project
    that launched it)."""
    k = _kit_config().get("kit") or {}
    out, seen = [], set()
    for c in list(k.get("consumers") or []) + list(extra or []):
        if isinstance(c, str):
            c = {"github": c}
        if not isinstance(c, dict):
            continue
        gh = str(c.get("github") or "").replace("https://github.com/", "").strip("/")
        key = (gh or str(c.get("name") or "")).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(dict(c, github=gh, name=str(c.get("name") or gh.split("/")[-1])))
    return out


def _my_post(posts, post=None):
    """A part's own post: named, else KIT_POST, else the one whose branch is this branch (or whose part this branch
    names)."""
    if post:
        return post if post in posts else None
    if os.environ.get("KIT_POST") in posts:
        return os.environ["KIT_POST"]
    br = _git_run("branch", "--show-current").stdout.strip().lower()
    if not br:
        return None
    hits = [pid for pid, p in posts.items() if isinstance(p, dict) and str(p.get("branch") or "").lower() == br]
    if not hits:
        hits = [pid for pid, p in posts.items() if isinstance(p, dict) and not pid.startswith("_") and (
            pid.lower() in br or (p.get("part") and re.search(r"(^|[-/_])%s($|[-/_])" % re.escape(_slug(str(p["part"]), 24)), br)))]
    return hits[0] if len(hits) == 1 else None


def _acceptance(rel):
    """The acceptance lines of a step's prompt (`Acceptance:` or `bars`), for its review."""
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    task = text.split("## How you work")[0]
    out = [l.strip() for l in task.splitlines() if re.match(r"^\s*(acceptance|bars?|must)\b", l.strip(), re.I)]
    return out


def _test_command():
    k = _kit_config().get("kit") or {}
    if k.get("test_command"):
        return str(k["test_command"])
    for c in ("scripts/run_all_tests.py",):
        if os.path.isfile(os.path.join(ROOT, c)):
            return "python3 -I .kit/device.py run --kind tests -- python3 %s" % c
    return "(none set: kit.test_command in .kit/config.json; the project's AGENTS.md names its tests)"


def _launch_workflow():
    """The orchestrator's launch workflow for its runtime, attached-board steps included."""
    run = _launcher()
    lines = ["## How you launch (%s)" % ("web: Claude Code on the web" if run == "web" else run)]
    lines.append("1. Plan: TaskCreate {train, part} (the hook writes the post and its prompt) or "
                 "`python3 -I .kit/kit.py step new --train T --part P --title ... --task-file F`; commit and push the prompt.")
    if run == "web":
        lines.append("2. `python3 -I .kit/kit.py launch <post>`: it marks the post and prints the `create_session` call "
                     "(claude-code-remote); make it, then `python3 -I .kit/kit.py launch <post> --session-id <its id>`.")
    else:
        lines.append("2. `python3 -I .kit/kit.py launch <post>`: it marks the post and starts `claude --bg` in a worktree "
                     "(the device cap refuses past it: the step waits planned).")
    lines.append("3. A step on an attached board (another repo's train, e.g. the kit's `cli`): the same command from here. "
                 "launch finds the post on that board and runs the launch in that repo's clone next to this one (missing: it "
                 "says the `git clone`); that clone starts no seeded board and never auto-pushes a board-mirror. The session "
                 "starts with the projects it must test on (kit.consumers + this one), each an add_repo read in its first step.")
    lines.append("4. With an attached Artifact board, make the ArtifactData batch launch prints (the post and the Trains row).")
    lines.append("5. Done comes as a message: `kit.py done <post>` must say OK (a change to compact, install, resume, "
                 "hooks or artifact needs its real-project dry run); then TaskUpdate completed: its review starts by itself.")
    return lines


def principles_for(role, train=None):
    """The hook point a principles file plugs into (CL32; its format and flow are CL34's): lines of
    <memory>/principles.md whose `scope:` is all, this role, or this train. No file: nothing."""
    p = os.path.join(_resolve_memory_dir(), "principles.md")
    try:
        with open(p, encoding="utf-8") as fh:
            lines = [l.rstrip() for l in fh if l.strip().startswith(("-", "*"))]
    except OSError:
        return []
    out = []
    for l in lines:
        m = re.search(r"scope:\s*([\w ,.-]+)", l, re.I)
        scopes = {x.strip().lower() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()} if m else {"all"}
        if scopes & {"all", role, str(train or "").lower()} - {""}:
            out.append(l)
    return out


def _principle_lines(role, train=None):
    pr = principles_for(role, train)
    return (["", "## Principles (scope: all, %s%s)" % (role, (", " + train) if train else "")] + pr) if pr else []


def resume_role(m, role, post=None, db_path=None, alive=None, cli=None, session=None, now=None):
    """`kit.py resume --role R`: what that role needs and nothing else (CL32), so a session starts from this alone."""
    if role not in ROLES:
        return "Unknown role %r: one of %s.\n" % (role, ", ".join(ROLES))
    posts, comments = _load_board_docs(m, db_path=db_path)
    try:
        for b in _attached_boards()[0]:
            for pid, p in (b.get("posts") or {}).items():
                posts.setdefault(pid, dict(p, _board=b["id"]))
    except Exception:
        pass
    out = []
    if role == "orchestrator":
        out.append(resume(m, db_path=db_path, alive=alive, cli=cli, session=session, now=now).rstrip("\n"))
        out += [""] + _launch_workflow()
        out += ["", "## Parts: their context and the save points",
                "- A part reads only `kit.py resume --role part` (its post, prompt, lessons, tests, how to report); never "
                "paste a reading list into a prompt.",
                "- At each check (your timer, a hand-over, a wake): a running part whose post has not changed for 30 min: "
                "SendMessage it \"Save point: commit and push your work now, write in your post where you stopped.\" A part "
                "not running and not complete: post status stalled, continue it from its branch.",
                "- Before you answer the owner: this resume, under 15 minutes old (the brief guard refuses otherwise)."]
        out += ["", "## Lessons for the orchestrator"] + _lesson_lines(lessons_for("orchestrator"))
        out += _principle_lines("orchestrator")
        pref = os.path.join(_resolve_memory_dir(), "preferences.md")
        if os.path.isfile(pref):
            with open(pref, encoding="utf-8") as fh:
                out += ["", "## The owner's preferences (%s)" % os.path.relpath(pref, ROOT), fh.read().strip()]
        bad = lessons_check()
        if bad:
            out += ["", "## Lessons refused (fix: they reach no role)"] + ["- " + e for e in bad]
    elif role == "part":
        pid = _my_post(posts, post)
        if not pid:
            return ("No post found for this session: `kit.py resume --role part --post <your post id>` (your prompt names "
                    "it), or KIT_POST=<id>.\n")
        p = posts[pid]
        rel = "docs/prompts/%s.md" % pid
        files = sorted(set(_changed_files()) | {str(a).split(" §")[0].strip("` ") for a in _as_list(p.get("artifacts"))})
        out += ["# Resume: part %s (post `%s`)" % (p.get("part") or pid, pid),
                "%s · train `%s` · status %s%s" % (p.get("title") or pid, p.get("train") or "?", p.get("status") or "?",
                                                    (" · board %s" % p["_board"]) if p.get("_board") else ""),
                "", "## Your prompt", "`%s`%s: read it whole; the owner's words in it are the task." % (
                    rel, "" if os.path.isfile(os.path.join(ROOT, rel)) else " (NOT in this clone: fetch its branch)"),
                "", "## Your post now"]
        for k in ("verdict", "bars", "check", "handoff"):
            if p.get(k):
                out.append("- %s: %s" % (k, "; ".join(map(str, _as_list(p[k])))[:400]))
        if len(out) and out[-1] == "## Your post now":
            out.append("- (nothing yet: at turn 1 set session_id, status in progress and a one-line body)")
        out += ["", "## Lessons that touch your files (%d files changed)" % len(files)] + _lesson_lines(lessons_for("part", files))
        out += _principle_lines("part", p.get("train"))
        out += ["", "## Your tests", "- `%s` (0 failed before you commit)" % _test_command()]
        areas = real_project_areas()
        cons = consumers(p.get("consumers"))
        if areas or cons:
            out.append("- Real project: %s. A change to %s needs a dry run in a clone of %s (add_repo read; never push "
                       "it); its command and output go in the post's `real_project`, or `done` refuses." % (
                           ("your change touches " + ", ".join(areas)) if areas else "nothing of compact/install/resume/hooks/artifact touched yet",
                           "/".join(REAL_PROJECT_AREAS), ", ".join(c["github"] or c["name"] for c in cons) or "a project that uses the kit"))
        orch = (posts.get("_orchestrator") or {}).get("session_id") or "the orchestrator"
        out += ["", "## How you report Done",
                "0. Your result lives in your post body: the text under `##` sections, pictures as `![what](path.png)`, "
                "diagrams as ```mermaid or ```svg blocks (the page draws them); never a `docs/*.md` file for your text "
                "(`done` refuses a body made of its section embeds; CL36).",
                "1. The post in the kit's format (`.kit/BOARD.md` § The post format); `python3 -I .kit/kit.py done %s` says OK." % pid,
                "2. Commit, push your branch; `python3 -I .kit/end_session.py`.",
                "3. SendMessage %s: `Done. Check post %s.`" % (orch, pid)]
    elif role == "clerk":
        orch = (posts.get("_orchestrator") or {}).get("session_id") or "(none on _orchestrator)"
        out += ["# Resume: the clerk (comment routing only)", "",
                "A comment on a post goes to the session that post names; none or not reachable: to the orchestrator.",
                "", "## Session ids", "- orchestrator: %s" % orch]
        for pid, p in sorted(posts.items()):
            if pid.startswith("_") or not isinstance(p, dict) or not p.get("session_id"):
                continue
            if str(p.get("status") or "").lower() in ("complete", "done", "dropped") or str(p.get("column") or "") == "done":
                continue
            out.append("- %s%s -> %s" % (pid, (" [repo %s]" % p["_board"]) if p.get("_board") else "", p["session_id"]))
        out += ["", "Watch: `python3 -I .kit/board_cli.py comments next --timeout 300` (run_in_background); forward the "
                "message unchanged; re-arm. Nothing else enters your context."]
    else:   # review
        pid = post or _my_post(posts, None)
        if not pid or pid not in posts:
            return "Review: name the step: `kit.py resume --role review --post <post id>`.\n"
        p = posts[pid]
        rel = "docs/prompts/%s.md" % pid
        out += ["# Resume: review of %s (post `%s`)" % (p.get("part") or pid, pid),
                "%s · train `%s`" % (p.get("title") or pid, p.get("train") or "?"), "", "## The step"]
        for k in ("verdict", "bars", "check", "breaks", "not_checked"):
            if p.get(k):
                out.append("- %s: %s" % (k, "; ".join(map(str, _as_list(p[k])))[:600]))
        acc = _acceptance(rel)
        out += ["", "## Its acceptance conditions (from `%s`)" % rel] + (["- " + a for a in acc] or ["- (none written: judge against the task)"])
        rp = p.get("real_project")
        cons = consumers(p.get("consumers"))
        out += ["", "## The real-project check",
                "- Rerun its `check`, then its claim on a real project: %s (add_repo read, clone, never push)." % (
                    ", ".join(c["github"] or c["name"] for c in cons) or "a project that uses the kit"),
                "- Its post says: %s" % (json.dumps(rp, ensure_ascii=False)[:500] if rp else "NO real-project result")]
        out += ["", "## Lessons for a review"] + _lesson_lines(lessons_for("review"))
        out += _principle_lines("review", p.get("train"))
    return "\n".join(out) + "\n"


def train_context(m, target, zoom=None, db_path=None, as_json=False):
    """Build a low-token Train-as-Tasklist context pack: ancestor zoom-out + current-task zoom-in."""
    posts, comments = _load_board_docs(m, db_path=db_path)
    tq = (target or "").strip()
    tql = tq.lower()
    tql = tql[2:] if tql.startswith("p-") else tql  # a prefix, not a character set: "planner" stays "planner"
    matched = []
    for pid, p in posts.items():
        if pid.startswith("_") or p.get("removed"):
            continue
        tr = str(p.get("train") or "").strip().lower()
        tr_clean = tr[2:] if tr.startswith("p-") else tr
        part = str(p.get("part") or "").strip()
        part_l = part.lower()
        pid_l = pid.lower()
        fam = re.match(r"^([a-z]+)", part_l)
        fam_s = fam.group(1) if fam else ""
        if (
            tr_clean == tql or tr == tq.lower()
            or pid_l == tq.lower() or part_l == tq.lower()
            or (len(tql) >= 2 and fam_s == tql)
            or pid_l.startswith(tql + "-")
        ):
            matched.append((pid, p))
    if not matched:
        if as_json:
            return json.dumps({"train": tq, "steps": [], "zoom": None}, ensure_ascii=False, indent=2)
        return "train %r: no matching posts found" % tq
    matched.sort(key=lambda x: (str(x[1].get("created_at") or ""), x[0]))

    # Index comments by post_id
    by_post = {}
    for cid, c in comments.items():
        if c.get("removed") or c.get("like"):
            continue
        pid = str(c.get("post_id") or "")
        if pid:
            by_post.setdefault(pid, []).append((cid, c))
    for lst in by_post.values():
        lst.sort(key=lambda x: str(x[1].get("created_at") or ""))

    # Pick zoom target: explicit --zoom, else latest non-done post, else latest post
    zoom_pid = None
    zoom_missing = None
    if zoom:
        zl = zoom.strip().lower()
        for pid, p in matched:
            if pid.lower() == zl or str(p.get("part") or "").strip().lower() == zl:
                zoom_pid = pid
                break
        # A part asking before its own first post: do not zoom onto a sibling's post.
        if not zoom_pid:
            zoom_missing = zoom.strip()
    if not zoom_pid and not zoom_missing:
        for pid, p in reversed(matched):
            if str(p.get("column") or "info").lower() != "done":
                zoom_pid = pid
                break
    if not zoom_pid and not zoom_missing:
        zoom_pid = matched[-1][0]

    steps_out = []
    for pid, p in matched:
        col = str(p.get("column") or "info").lower()
        st = str(p.get("status") or "").lower()
        box = "[x]" if col == "done" else ("[?]" if col == "question" else ("[!]" if col == "todo" else ("[~]" if "progress" in st else "[ ]")))
        verdict = str(p.get("verdict") or "").strip() or _first_summary_line(p.get("body"))
        bars = p.get("bars") or []
        artifacts = p.get("artifacts") or []
        issues = p.get("issues") or []
        steps_out.append({
            "id": pid,
            "part": str(p.get("part") or pid),
            "title": str(p.get("title") or pid),
            "column": col,
            "status": st,
            "box": box,
            "verdict": verdict,
            "bars": bars if isinstance(bars, list) else [str(bars)],
            "artifacts": artifacts if isinstance(artifacts, list) else [str(artifacts)],
            "issues": issues if isinstance(issues, list) else [str(issues)],
            "check": str(p.get("check") or "").strip(),
            "breaks": str(p.get("breaks") or "").strip(),
            "handoff": str(p.get("handoff") or "").strip(),
            "comments_count": len(by_post.get(pid, [])),
            "is_zoom": pid == zoom_pid,
        })

    if zoom_missing:
        zoom_pid = zoom_missing
        zpost = {"part": zoom_missing, "title": "%s (no post yet: this is its first turn)" % zoom_missing, "column": "info"}
    else:
        zpost = posts[zoom_pid]
    zpart = str(zpost.get("part") or zoom_pid)
    # Find matching prompt file if present
    prompt_file = ""
    pdir = os.path.join(ROOT, "docs", "prompts")
    if os.path.isdir(pdir):
        for fn in sorted(os.listdir(pdir)):
            if fn.lower().startswith(zpart.lower() + "-") or fn.lower() == (zoom_pid.lower() + ".md"):
                prompt_file = "docs/prompts/" + fn
                break

    z_cmts = [
        {"id": cid, "from": str(c.get("from") or "?"), "kind": str(c.get("kind") or ""), "at": str(c.get("created_at") or "")[:16], "text": str(c.get("text") or "")[:600]}
        for cid, c in by_post.get(zoom_pid, [])[-12:]
    ]

    if as_json:
        return json.dumps({
            "train": tq,
            "steps": steps_out,
            "zoom": {
                "id": zoom_pid,
                "part": zpart,
                "title": str(zpost.get("title") or zoom_pid),
                "column": str(zpost.get("column") or "info"),
                "prompt_file": prompt_file,
                "body": str(zpost.get("body") or ""),
                "comments": z_cmts,
            },
        }, ensure_ascii=False, indent=2)

    lines = ["# Train: %s (%d steps · zoom: %s)" % (tq, len(steps_out), zoom_pid), "", "## Tasklist (Zoom-out)"]
    for s in steps_out:
        mark = " **← CURRENT**" if s["is_zoom"] else ""
        line = "- %s **%s** (`%s`): %s%s" % (s["box"], s["part"], s["id"], s["title"], mark)
        if s["verdict"]:
            line += "\n  - *Verdict:* %s" % s["verdict"]
        meta_bits = []
        if s["bars"]:
            meta_bits.append("bars: " + ", ".join(str(x) for x in s["bars"][:5]))
        if s["artifacts"]:
            meta_bits.append("artifacts: " + ", ".join(str(x) for x in s["artifacts"][:6]))
        if s["issues"]:
            meta_bits.append("issues: " + ", ".join(str(x) for x in s["issues"][:4]))
        if s["check"]:
            meta_bits.append("check: `%s`" % s["check"])
        if meta_bits:
            line += "\n  - " + " | ".join(meta_bits)
        if s["breaks"]:
            line += "\n  - *Breaks:* %s" % s["breaks"]
        if s["handoff"]:
            line += "\n  - *Handoff:* %s" % s["handoff"]

        lines.append(line)

    lines.extend(["", "## Current Focus (Zoom-in: `%s`)" % zoom_pid])
    if prompt_file:
        lines.append("- **Prompt:** `%s`" % prompt_file)
    lines.append("- **Column:** %s | **Title:** %s" % (zpost.get("column") or "info", zpost.get("title") or zoom_pid))
    body_txt = str(zpost.get("body") or "").strip()
    if body_txt:
        lines.extend(["", body_txt[:4000] + ("\n...(truncated; full post via `kit.py post %s`)" % zoom_pid if len(body_txt) > 4000 else "")])
    if z_cmts:
        lines.extend(["", "### Recent Comments & Reviews (%d)" % len(z_cmts)])
        for c in z_cmts:
            tag = (" [%s]" % c["kind"]) if c["kind"] else ""
            lines.append("- **%s%s** (%s): %s" % (c["from"], tag, c["at"], c["text"].replace("\n", " ")))
    return "\n".join(lines) + "\n"


def search_all(query, db_path=None, limit=25):
    """Search posts, comments (SQLite) and committed memory markdown files."""
    q = (query or "").strip()
    if not q:
        return "search: empty query"
    ql = q.lower()
    hits = []
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    if os.path.isfile(db):
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        try:
            for coll, did, raw in conn.execute("SELECT collection, doc_id, data FROM docs WHERE collection IN ('posts','comments') ORDER BY updated_at DESC"):
                if ql not in (raw or "").lower():
                    continue
                try:
                    doc = json.loads(raw)
                except ValueError:
                    continue
                if coll == "posts":
                    txt = str(doc.get("title") or "") + " — " + str(doc.get("body") or "")
                    idx = txt.lower().find(ql)
                    snip = txt[max(0, idx - 30): idx + 90].replace("\n", " ") if idx >= 0 else txt[:100].replace("\n", " ")
                    hits.append("[post %s] %s" % (did, snip))
                else:
                    txt = str(doc.get("text") or "")
                    idx = txt.lower().find(ql)
                    snip = txt[max(0, idx - 30): idx + 90].replace("\n", " ") if idx >= 0 else txt[:100].replace("\n", " ")
                    hits.append("[comment %s on %s] %s" % (did, doc.get("post_id") or "?", snip))
                if len(hits) >= limit:
                    break
        finally:
            conn.close()
    mem_dir = CONFIG.get("kit", {}).get("memory_dir") or ".story"
    mem_root = os.path.join(ROOT, mem_dir)
    if os.path.isdir(mem_root) and len(hits) < limit:
        for dirpath, _, files in os.walk(mem_root):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if ql in line.lower():
                                rel = os.path.relpath(fp, ROOT)
                                hits.append("[memory %s:%d] %s" % (rel, lineno, line.strip()[:110]))
                                if len(hits) >= limit:
                                    break
                except OSError:
                    pass
                if len(hits) >= limit:
                    break
    if not hits:
        return "search %r: 0 matches" % q
    return "\n".join(hits) + ("\n(%d match%s)" % (len(hits), "" if len(hits) == 1 else "es"))


def commit(m, msg, branch=None):
    target_branch = branch or BRANCH
    if git(m, "symbolic-ref", "--short", "HEAD").stdout.strip() != target_branch:
        raise SystemExit("kit: %s is not on branch %s; refusing to commit the copy anywhere else" % (m, target_branch))
    git(m, "add", "-A", "board")
    r = git(m, "commit", "-q", "-m", msg)
    return (r.stdout + r.stderr).strip() or "committed"


def main(argv=None):
    ap = argparse.ArgumentParser(description="kit: the board's git copy and its lookups")
    ap.add_argument("--mirror", help="the copy's worktree (default $KIT_MIRROR, the config's mirror.path, or ../board-mirror)")
    ap.add_argument("--config", help="config/<project>.json (branch, stamps, owner and clerk names)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    sp.add_parser("push")
    s = sp.add_parser("sync"); s.add_argument("dir"); s.add_argument("--at", required=True)
    s.add_argument("--full", action="store_true"); s.add_argument("--commit", action="store_true")
    s = sp.add_parser("sync-db"); s.add_argument("--db", default=None); s.add_argument("--at", default=None)
    s.add_argument("--full", action="store_true"); s.add_argument("--commit", action="store_true"); s.add_argument("--with-blobs", action="store_true")
    s = sp.add_parser("receive"); s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s.add_argument("--branch", default=None)
    s = sp.add_parser("publish"); s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s.add_argument("--branch", default=None); s.add_argument("--db", default=None); s.add_argument("--at", default=None)
    s = sp.add_parser("diff"); s.add_argument("dump"); s.add_argument("--window", nargs=2)
    s = sp.add_parser("post"); s.add_argument("id"); s.add_argument("--head", type=int)
    s = sp.add_parser("open"); s.add_argument("--since", default="2000-01-01")
    s = sp.add_parser("since"); s.add_argument("t")
    s = sp.add_parser("session"); s.add_argument("post")
    s = sp.add_parser("reads"); s.add_argument("--at")
    s = sp.add_parser("doctor"); s.add_argument("--db", default=None)
    s = sp.add_parser("compact"); s.add_argument("--days", type=int, default=30); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-board", action="store_true", help="post nothing: open awaits points and issues stay in memory")
    s = sp.add_parser("report"); s.add_argument("--db", default=None)
    s = sp.add_parser("portfolio"); s.add_argument("--config", dest="portfolio_config", default=None); s.add_argument("--roots", nargs="*", default=[])
    s = sp.add_parser("queue"); s.add_argument("--runner", default=None); s.add_argument("--for", dest="for_owner", default=None); s.add_argument("--db", default=None); s.add_argument("--unblocked", action="store_true", help="Exclude cards with unresolved blocked_by dependencies")
    s = sp.add_parser("search"); s.add_argument("query"); s.add_argument("--db", default=None); s.add_argument("--limit", type=int, default=25)
    s = sp.add_parser("train"); s.add_argument("target"); s.add_argument("--zoom", default=None); s.add_argument("--db", default=None); s.add_argument("--json", dest="as_json", action="store_true")
    s = sp.add_parser("resume"); s.add_argument("--db", default=None); s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s.add_argument("--session", default=None, help="your session id: resume flags an _orchestrator post naming another")
    s.add_argument("--role", choices=list(ROLES), default=None, help="what that role needs, nothing else (CL32)")
    s.add_argument("--post", default=None, help="--role part|review: the step's post (default: this branch's)")
    s = sp.add_parser("lessons", help="the lessons a role needs (--role), or `check`: refuse untagged lessons and "
                      "lessons only a project doc holds")
    s.add_argument("action", nargs="?", choices=["list", "check"], default="list"); s.add_argument("--role", choices=list(ROLES))
    s.add_argument("--files", nargs="*", default=None)
    s = sp.add_parser("lesson", help="add a lesson to the memory, tagged with its roles")
    s.add_argument("action", choices=["add"]); s.add_argument("id"); s.add_argument("--roles", required=True)
    s.add_argument("--title", required=True); s.add_argument("--text", required=True)
    s.add_argument("--files", default=""); s.add_argument("--from", dest="frm", default="")
    s = sp.add_parser("commands", help="the index of the kit's commands: read it before writing any code")
    s.add_argument("word", nargs="?", default=None)
    s = sp.add_parser("adopt", help="once: give every post made before the kit's trains its train (from its part)")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--db", default=None)
    s = sp.add_parser("artifact", help="an Artifact board: pull | load | push | sent <batch> | pin <coll/id=version>...")
    s.add_argument("action", choices=["pull", "load", "push", "sent", "pin", "status"]); s.add_argument("args", nargs="*")
    s.add_argument("--full", action="store_true", help="pull: every document, not only what changed")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s = sp.add_parser("reconcile", help="the board here vs its git copy: the newer copy of each post and comment wins")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None); s.add_argument("--branch", default=None)
    s.add_argument("--db", default=None); s.add_argument("--no-push", action="store_true")
    s = sp.add_parser("capacity", help="the device's session cap (.kit/device.py): --gate exits 1 when no slot is free")
    s.add_argument("--gate", action="store_true"); s.add_argument("--json", action="store_true")
    s = sp.add_parser("launch", help="start a part: its post in progress + Trains row running, then claude --bg (CLI) "
                      "or the create_session call (web)")
    s.add_argument("post"); s.add_argument("--session-id", default=None, help="web: the id create_session returned")
    s.add_argument("--web", action="store_true", default=None); s.add_argument("--cli-run", dest="web", action="store_false")
    s.add_argument("--model", default=None); s.add_argument("--reset", action="store_true")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None); s.add_argument("--json", action="store_true")
    s.add_argument("--post-file", default=None, help=argparse.SUPPRESS)
    s = sp.add_parser("done"); s.add_argument("post"); s.add_argument("--db", default=None)
    s = sp.add_parser("pair"); s.add_argument("post"); s.add_argument("--answer", default=None); s.add_argument("--db", default=None)
    s = sp.add_parser("sections"); s.add_argument("--db", default=None)
    s = sp.add_parser("step", help="the train is the task list: new | start | complete | drop")
    s.add_argument("action", choices=["new", "start", "complete", "drop"]); s.add_argument("post", nargs="?")
    s.add_argument("--train"); s.add_argument("--part"); s.add_argument("--title", default="")
    s.add_argument("--task", default=""); s.add_argument("--task-file"); s.add_argument("--parent")
    s.add_argument("--source", action="append", default=[]); s.add_argument("--model")
    s.add_argument("--no-review", action="store_true"); s.add_argument("--db", default=None)
    s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s.add_argument("--board", default=None, help="an attached board's id (repos/<id>/ in the Artifact): the step goes "
                   "on its train (default: this board, or the one attached board that holds the train)")
    s = sp.add_parser("review"); s.add_argument("post", nargs="?"); s.add_argument("--train", default=None)
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--force", action="store_true"); s.add_argument("--db", default=None)
    s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s = sp.add_parser("welcome")
    s.add_argument("--runtime", choices=WELCOME_RUNTIMES, default=None)
    s.add_argument("--post", action="store_true", help="Post welcome-<runtime>-1 under _orchestrator (idempotent)")
    s.add_argument("--board-url", default=None)
    s.add_argument("--token", default=None)
    s.add_argument("--project", default=None)
    s.add_argument("--memory-dir", default=None)
    s.add_argument("--launcher", dest="launcher", default=None, help="Override one-word launcher command")
    s.add_argument("--talk", default=None)
    s.add_argument("--at", default=None)
    a = ap.parse_args(argv)
    if a.config:
        configure(a.config)
    elif os.path.exists(os.path.join(ROOT, ".kit", "config.json")):  # an installed kit: <project>/.kit/kit.py
        configure(os.path.join(ROOT, ".kit", "config.json"))
    m = mirror_dir(a)
    if a.cmd in STALE_GATED and not getattr(a, "db", None):   # --db: a named database, not the Artifact's copy
        why = stale_copy()
        if why:
            print(why)
            return 3
    if a.cmd == "init":
        print(init(m)); return 0
    if a.cmd == "push":
        r = git(m, "push", "-u", "origin", BRANCH)
        print((r.stdout + r.stderr).strip() or "pushed"); return r.returncode
    if a.cmd == "sync":
        with _sync_lock(m):
            r = sync(m, a.dir, a.at, a.full)
            print("sync %s: posts %d written, %d removed; comments %d new, %d changed, %d removed" % ((a.at,) + r))
            if a.commit:
                print(commit(m, "kit sync %s%s" % (a.at, " (full)" if a.full else "")))
        return 0
    if a.cmd == "sync-db":
        at_val = a.at or zstamp(datetime.now(timezone.utc))
        with _sync_lock(m):
            r = sync_db(m, db_path=a.db, at=at_val, full=a.full, with_blobs=a.with_blobs)
            print("sync-db %s: posts %d written, %d removed; comments %d new, %d changed, %d removed" % ((at_val,) + r))
            if a.commit:
                print(commit(m, "kit sync-db %s%s" % (at_val, " (full)" if a.full else "")))
        return 0
    if a.cmd == "doctor":
        out, rc = doctor(m, db_path=a.db)
        print(out)
        return rc
    if a.cmd == "compact":
        print(compact_memory(days=a.days, dry_run=a.dry_run, board=False if a.no_board else None))
        return 0
    if a.cmd == "report":
        print(report_metrics(m, db_path=a.db), end="")
        return 0
    if a.cmd == "portfolio":
        print(portfolio_summary(config_path=a.portfolio_config, roots=a.roots), end="")
        return 0
    if a.cmd == "queue":
        print(queue_cards(m, runner=a.runner, for_owner=a.for_owner, db_path=a.db, unblocked_only=a.unblocked))
        return 0
    if a.cmd == "search":
        print(search_all(a.query, db_path=a.db, limit=a.limit))
        return 0
    if a.cmd == "step":
        if a.action == "new":
            if not a.train or not a.part:
                print(json.dumps({"error": "step new needs --train and --part"}))
                return 2
            task = a.task
            if a.task_file:
                with open(a.task_file, encoding="utf-8") as fh:
                    task = fh.read()
            res = step_new(a.train, a.part, a.title or a.part, task=task, parent=a.parent, sources=a.source,
                           model=a.model, review=not a.no_review, db_path=a.db, cli=a.cli, board=a.board)
        elif not a.post:
            res = {"error": "step %s needs a post id" % a.action}
        elif a.action == "start":
            res = step_start(a.post)
        elif a.action == "drop":
            res = step_drop(a.post, db_path=a.db)
        else:
            res = step_complete(a.post, db_path=a.db, cli=a.cli)
        print(json.dumps(res, ensure_ascii=False))
        return 1 if res.get("error") or res.get("ok") is False else 0
    if a.cmd == "sections":
        print(sections(db_path=a.db))
        return 0
    if a.cmd == "done":
        rc, msg = done(a.post, db_path=a.db)
        print(msg)
        return rc
    if a.cmd == "pair":
        rc, msg = pair(a.post, answer=a.answer, db_path=a.db)
        print(msg)
        return rc
    if a.cmd == "review":
        if not a.post and not a.train:
            print("kit.py review <post> | --train <train>")
            return 2
        rc, msg = review(a.post, train=a.train, dry_run=a.dry_run, force=a.force, db_path=a.db, cli=a.cli)
        print(msg)
        return rc
    if a.cmd == "artifact":
        try:
            if a.action == "pull":
                print(artifact_pull(full=a.full), end="")
            elif a.action == "load":
                r = artifact_load(url=a.board_url, token=a.token)
                print("Loaded from the Artifact: %d taken, %d kept from here (unsent), %d merged field by field. "
                      "The scripts now read and write the local copy; send its changes with `kit.py artifact push`." % (
                          r["taken"], r["kept_here"], r["merged"]))
                if r.get("blobs_restored"):
                    print("Pictures put in the local copy: %d." % r["blobs_restored"])
                for rid, n in sorted((r.get("attached") or {}).items()):
                    print("Attached board %s: %s" % (rid, ("%d documents from the Artifact's repos/%s (read-only here; "
                          "`kit.py launch` and `resume` find its steps)" % (n, rid)) if isinstance(n, int) else n))
                for u in r.get("unmerged") or []:
                    print("NOT MERGED (rows without a key changed on both sides; this copy kept, its pin will refuse): " + u)
                print(artifact_blobs_text(r), end="")
            elif a.action == "push":
                print(artifact_push_text(artifact_push(url=a.board_url, token=a.token)), end="")
            elif a.action == "sent":
                print("recorded: %d writes" % sum(artifact_sent(p) for p in a.args))
            elif a.action == "pin":
                rep = []
                print("pinned: %d" % artifact_pin(a.args, report=rep))
                for line in rep:
                    print(line)
            elif a.action == "status":
                r = artifact_unsent(url=a.board_url, token=a.token)
                if not (r["changed"] or r["batches"]):
                    print("sent: the Artifact has every change made here")
                    return 0
                print("NOT SENT: %d document(s) changed here since the Artifact last had them; %d batch file(s) built, "
                      "never recorded sent (%s). Run `python3 -I .kit/kit.py artifact push`, make each ArtifactData "
                      "batch, then `kit.py artifact sent <batch>`." % (r["changed"], len(r["batches"]),
                                                                       ", ".join(r["batches"]) or "none"))
                return 1
        except (Conflict, urllib.error.URLError, OSError) as e:
            print("artifact %s: NOT DONE (%s)" % (a.action, e))
            return 1
        return 0
    if a.cmd == "reconcile":
        try:
            with _sync_lock(m) if os.path.isdir(m) else contextlib.nullcontext():
                r = reconcile(m, url=a.board_url, token=a.token, branch=a.branch, db_path=a.db, push=not a.no_push)
        except (Conflict, urllib.error.URLError, OSError) as e:
            print("reconcile: NOT DONE (%s); the board here is unchanged." % e)
            return 1
        print("Board vs git: %s; %d from git (newer there), %d new from git, %d kept from here (newer here); %s."
              % (r["status"], r["from_git"], r["added"], r["kept_here"], r.get("git", "")))
        return 0
    if a.cmd == "resume":
        # the _orchestrator check is the orchestrator's (a part's own session is never named there)
        sess = a.session or (os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIT_SESSION_ID")
                             if os.environ.get("KIT_ROLE") == "orchestrator" else None)
        if a.role:
            print(resume_role(m, a.role, post=a.post, db_path=a.db, cli=a.cli, session=sess), end="")
        else:
            print(resume(m, db_path=a.db, cli=a.cli, session=sess), end="")
        return 0
    if a.cmd == "lessons":
        if a.action == "check":
            err = lessons_check()
            print("\n".join(["REFUSED: " + e for e in err]) if err else "OK: every lesson is in the memory, tagged with its roles")
            return 1 if err else 0
        les = lessons_for(a.role, a.files) if a.role else all_lessons()
        print("\n".join(_lesson_lines(les)))
        return 0
    if a.cmd == "lesson":
        res = lesson_add(a.id, a.roles.split(","), a.title, a.text, [f for f in a.files.split(",") if f.strip()], a.frm)
        print(json.dumps(res, ensure_ascii=False))
        return 1 if res.get("error") else 0
    if a.cmd == "commands":
        print(commands_text(a.word), end="")
        return 0
    if a.cmd == "adopt":
        try:
            r = adopt_trains(url=a.board_url if hasattr(a, "board_url") else None, dry_run=a.dry_run, db_path=a.db, m=m)
        except Conflict as e:
            print("adopt: %s" % e, file=sys.stderr)
            return 2
        print(adopt_text(r), end="")
        return 0
    if a.cmd == "launch":
        r = launch(a.post, url=a.board_url, token=a.token, web=a.web, session_id=a.session_id, model=a.model, reset=a.reset,
                   post_file=a.post_file)
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else launch_text(r), end="" if not a.json else "\n")
        return 0 if r.get("ok") else 1
    if a.cmd == "capacity":
        d = _device()
        if d is None:
            print("no .kit/device.py: reinstall the kit", file=sys.stderr)
            return 2
        return d.main((["--gate"] if a.gate else []) + (["--json"] if a.json else []))
    if a.cmd == "train":
        print(train_context(m, a.target, zoom=a.zoom, db_path=a.db, as_json=a.as_json), end="")
        return 0
    if a.cmd == "receive":
        try:
            res = receive(m, url=a.board_url, branch=a.branch, token=a.token)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        except Conflict as e:
            print("receive conflict: " + str(e), file=sys.stderr)
            return 2
    if a.cmd == "publish":
        try:
            res = publish(m, url=a.board_url, db_path=a.db, branch=a.branch, token=a.token, at=a.at)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        except Conflict as e:
            print("publish conflict: " + str(e), file=sys.stderr)
            return 2
    if a.cmd == "welcome":
        if a.post:
            res = post_welcome(
                runtime=a.runtime, url=a.board_url, token=a.token, project=a.project,
                memory_dir=a.memory_dir, cmd=a.launcher, talk=a.talk, at=a.at,
            )
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(render_welcome(
                runtime=a.runtime, project=a.project, board_url=a.board_url,
                memory_dir=a.memory_dir, cmd=a.launcher, talk=a.talk,
            ), end="")
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
