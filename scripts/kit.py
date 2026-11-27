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
import hashlib
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

def _test_guard():
    """CL48: in a test run (KIT_TEST set) a write to a board, a mirror or a remote outside a temp folder is refused
    (kit/test_guard.py, every git command checked first); None outside a test run."""
    if not os.environ.get("KIT_TEST"):
        return None
    if sys.modules.get("kit_test_guard"):
        return sys.modules["kit_test_guard"]
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for f in [os.path.join(d, "test_guard.py") for d in (here, os.path.join(here, "..", "kit"), os.path.join(here, ".."))] + \
            [os.environ.get("KIT_TEST_GUARD") or ""]:     # a test's hand-made copy of a few scripts: the runner's
        if f and os.path.isfile(f):
            spec = importlib.util.spec_from_file_location("kit_test_guard", f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["kit_test_guard"] = mod
            spec.loader.exec_module(mod)
            mod.install()
            return mod
    raise SystemExit("KIT_TEST: REFUSED: test_guard.py is not next to %s: a test run writes nothing without it" % __file__)


_TEST_GUARD = _test_guard()


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
    if _TEST_GUARD:     # CL48: a test run writes a board's git copy under a temp folder only
        _TEST_GUARD.check_path(m, "to a board's git copy")
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
    if _TEST_GUARD:     # CL48: a test run writes a board's git copy under a temp folder only
        _TEST_GUARD.check_path(m, "to a board's git copy")
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
            # CL44 (item 7): a full sync never deletes a mirror file that git added or changed unless the board
            # explicitly deleted it (its changes log). Missing files not deleted by the board are kept and imported.
            try:
                deleted = {(c, i) for c, i in conn.execute(
                    "SELECT DISTINCT collection, doc_id FROM changes WHERE op='delete' AND collection IN ('posts','comments')"
                ).fetchall()}
            except sqlite3.Error:
                deleted = set()
            tmp = tempfile.mkdtemp(prefix="kit-sync-db-")
            imported_from_mirror = 0
            try:
                for coll in ("posts", "comments"):
                    os.makedirs(os.path.join(tmp, coll), exist_ok=True)
                    db_docs = {}
                    for doc_id, raw in conn.execute("SELECT doc_id, data FROM docs WHERE collection=?", (coll,)):
                        doc = json.loads(raw)
                        db_docs[doc_id] = doc
                        write_doc(os.path.join(tmp, coll, doc_id + ".json"), doc)
                    old_mirror = load_dir(m + "/board", coll)
                    for doc_id, mdoc in old_mirror.items():
                        if doc_id not in db_docs and (coll, doc_id) not in deleted:
                            write_doc(os.path.join(tmp, coll, doc_id + ".json"), mdoc)
                            conn.execute(
                                "INSERT OR IGNORE INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, 1, ?, ?)",
                                (coll, doc_id, json.dumps(mdoc, ensure_ascii=False), now),
                            )
                            imported_from_mirror += 1
                if imported_from_mirror:
                    conn.commit()
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


class PartStop(Exception):
    """A part cannot start safely (git or the board unreadable here): resume says why and exits non-zero, never a
    task printed as if all were well (the owner, 2026-11-26: an agy part's sandbox hid git and the board, resume
    printed the task and exited 0, and the part redid saved work)."""


BOARD_ERRORS = []   # why the last read of the board server failed (empty when it answered)


def _sandbox_hint():
    return ("On agy: run this same command again with BypassSandbox: true (its sandbox hides the main checkout's .git and "
            "the board). If it still fails, do not start: message the orchestrator with this message.")


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
        return env_tok.strip() or None
    for bt_path in (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_token.py"),
        os.path.join(ROOT, ".kit", "board_token.py"),
        os.path.join(ROOT, "kit", "board", "board_token.py"),
    ):
        if os.path.isfile(bt_path):
            try:
                spec = importlib.util.spec_from_file_location("kit_board_token", bt_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "read_token"):
                    return mod.read_token(ROOT)
            except Exception:
                pass
            break
    for tok_path in (
        os.path.join(ROOT, ".kit", "board_data", "token"),
        os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                     "board-kit", "board_token"),
    ):
        if os.path.isfile(tok_path):
            try:
                tok = open(tok_path, encoding="utf-8").read().strip()
                if tok:
                    return tok
            except OSError:
                pass
    return None


def _board_request(url, path, body=None, token=None, method=None):
    if _TEST_GUARD:     # CL48: a test run writes only to a test's board, never to the real one
        _TEST_GUARD.check_board(url, method or ("POST" if body is not None else "GET"))
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
        _board_request(url, "db/batch", {"writes": writes, "sync": True}, token=token)
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
    remote_ref = "refs/remotes/origin/" + branch
    local_ref = "refs/heads/" + branch
    git(repo, "fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:%s" % (branch, remote_ref))
    has_remote = git(repo, "rev-parse", "--verify", "--quiet", remote_ref).returncode == 0
    has_local = git(repo, "rev-parse", "--verify", "--quiet", local_ref).returncode == 0
    if not has_remote and not has_local:
        return {"status": "no git copy (origin/%s)" % branch, "from_git": 0, "added": 0, "kept_here": 0}
    remote_head = _git_checked(repo, "rev-parse", remote_ref).strip() if has_remote else ""
    local_head = _git_checked(repo, "rev-parse", local_ref).strip() if has_local else ""
    head = remote_head or local_head
    # CL44 (item 7): read the newest of BOTH origin/<branch> and local <branch>, never origin alone, so unpushed
    # commits on the local mirror branch are preserved and brought onto the board instead of being overwritten.
    remote = _git_board_docs(repo, remote_ref) if has_remote else {}
    if has_local and local_head != remote_head:
        for k, (ldata, lgt) in _git_board_docs(repo, local_ref).items():
            if k not in remote:
                remote[k] = (ldata, lgt)
            elif k[0] == "seen":
                remote[k] = (merge_board_seen(remote[k][0], ldata), max(remote[k][1], lgt))
            elif lgt and (not remote[k][1] or lgt > remote[k][1]):
                remote[k] = (ldata, lgt)
        if not has_remote or git(repo, "merge-base", "--is-ancestor", remote_ref, local_ref).returncode == 0:
            head = local_head
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
                _board_request(url, "db/batch", {"writes": [w], "sync": True}, token=token)
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


# ---------------------------------------------------------------- Platform detection & rules table (R5)
# One answer to "where does this session run" (_platform()), never inferred from .kit/config.json (a config naming an
# Artifact, cli or runtime says what exists or was installed, not where this session runs). Every rule that differs by
# platform declares a row in PLATFORM_RULES and reads it via _rule(name, platform); tests/test_platform_matrix.py
# checks resume, done, launch and step new under all five platforms against this table and refuses un-rowed checks.
PLATFORMS = ("web", "cli", "agy", "arena", "generic")
_PLATFORM_ALIASES = {"claude": "cli", "claude-cli": "cli", "claude-web": "web", "lmarena": "arena"}

_OWNER_PAGE = "read, look at pictures and documents, play audio, Like, tap an answer, comment under a post"
_OWNER_ASK = "A question for him is a question post with its options, a default and a deadline: one tap answers it and reaches you."

PLATFORM_RULES = {
    # Board sync / Artifact steps (CL30, CL44, CL51): the Artifact is Claude Code web's alone.
    "artifact_steps": {
        "web": True,
        "cli": False,
        "agy": False,
        "arena": False,
        "generic": False,
    },
    # Gates (CL15, CL30, CL44, CL51):
    # - stale_gate: refuses reads until the Artifact copy is loaded/reconciled.
    "stale_gate": {
        "web": True,
        "cli": False,
        "agy": False,
        "arena": False,
        "generic": False,
    },
    # - device_cap_gate: local machine RAM/CPU session cap (.kit/device.py --gate).
    "device_cap_gate": {
        "web": False,
        "cli": True,
        "agy": True,
        "arena": False,
        "generic": False,
    },
    # Real-project dry run (CL32, CL38, CL39, R4):
    # "orchestrator" on web (launched web session cannot attach repos; uses Artifact kit_docs);
    # "part" on computer runners (cli/agy: part runs on the consuming project in a scratch local clone or read-only,
    # never pushing, and writes real_project before Done; kit.py done refuses without it);
    # "computer" on arena/generic (local dry run pending, no ArtifactData/add_repo).
    "real_project_run": {
        "web": "orchestrator",
        "cli": "part",
        "agy": "part",
        "arena": "computer",
        "generic": "computer",
    },
    # Cards (_dashboard, _trains) builder (106bba6): web keeps its kit-cards agent & reminder; local platforms use the server.
    "cards": {
        "web": "agent",
        "cli": "server",
        "agy": "server",
        "arena": "server",
        "generic": "server",
    },
    # How a part/review/successor session is launched (CL26):
    "launcher": {
        "web": "create_session",
        "cli": "claude_bg",
        "agy": "invoke_subagent",
        "arena": "self",
        "generic": "self",
    },
    # Inter-session reply tool & hook response schema (CL44 item 9, CL49):
    "reply_format": {
        "web": "claude_remote",
        "cli": "claude_cli",
        "agy": "agy",
        "arena": "none",
        "generic": "none",
    },
    # Hook runtime row in kit/hook.py TABLE (None: no hooks on this platform):
    "hook_runtime": {
        "web": "web",
        "cli": "cli",
        "agy": "agy",
        "arena": None,
        "generic": None,
    },
    # Orchestrator task list mechanism in resume / workflow:
    "task_list": {
        "web": "claude_tasks",
        "cli": "claude_tasks",
        "agy": "agy_step",
        "arena": "step_cli",
        "generic": "step_cli",
    },
    # How resume checks whether running part sessions are still alive:
    "alive_check": {
        "web": "claude_agents",
        "cli": "claude_agents",
        "agy": "subagents",
        "arena": "none",
        "generic": "none",
    },
    # Role template resolution order under .kit/:
    "part_template": {
        "web": ("claude-web/part.md", "claude-cli/part.md", "part.md"),
        "cli": ("claude-cli/part.md", "part.md", "claude-web/part.md"),
        "agy": ("part.md", "claude-cli/part.md", "claude-web/part.md"),
        "arena": ("part.md", "claude-cli/part.md", "claude-web/part.md"),
        "generic": ("part.md", "claude-cli/part.md", "claude-web/part.md"),
    },
    "orchestrator_guide": {
        "web": ("claude-web/orchestrator.md", "claude-cli/takeover.md", "orchestrator.md"),
        "cli": ("claude-cli/orchestrator.md", "orchestrator.md"),
        "agy": ("orchestrator.md",),
        "arena": ("orchestrator.md",),
        "generic": ("orchestrator.md",),
    },
    "consultant_template": {
        "web": ("claude-cli/consultant.md", "consultant.md"),
        "cli": ("claude-cli/consultant.md", "consultant.md"),
        "agy": ("consultant.md", "claude-cli/consultant.md"),
        "arena": ("consultant.md", "claude-cli/consultant.md"),
        "generic": ("consultant.md", "claude-cli/consultant.md"),
    },
    # Fresh-trunk check (CL53): web clones start fresh (no refetch); local platforms fetch & move on start / --again.
    "checkout_refetch": {
        "web": False,
        "cli": True,
        "agy": True,
        "arena": True,
        "generic": True,
    },
    # Welcome comment runtime prefix (welcome-<rt>-<n>):
    "welcome_runtime": {
        "web": "claude",
        "cli": "claude-cli",
        "agy": "agy",
        "arena": "arena",
        "generic": "generic",
    },
    # What the owner can and cannot do where he is (owner_lines):
    "owner_surface": {
        "web": (
            ["on the board page, usually from his phone: " + _OWNER_PAGE],
            ["run a command, open a file of this container, attach a repository or approve a tool for you"],
            _OWNER_ASK,
        ),
        "cli": (
            ["on the board page (this computer, or his phone over his private network): " + _OWNER_PAGE,
             "type in the orchestrator's terminal, only when he is at the computer"],
            ["see a path under /tmp or in a part's worktree, or answer in a part's chat"],
            _OWNER_ASK,
        ),
        "agy": (
            ["on the board page (this computer, or his phone over his private network): " + _OWNER_PAGE,
             "type in the agy terminal, only when he is at the computer"],
            ["see a path under /tmp or in a subagent's worktree, or answer in a subagent's chat"],
            _OWNER_ASK,
        ),
        "arena": (["type in this chat while a turn runs; use the embedded board"], [], ""),
        "generic": (["use the board page"], [], ""),
    },
}


def _norm_platform(name):
    s = str(name or "").strip().lower()
    s = _PLATFORM_ALIASES.get(s, s)
    return s if s in PLATFORMS else ""


def _truthy_env(name):
    return str(os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


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


def _platform(explicit=None):
    """web | cli | agy | arena | generic: the one answer to "where does this session run".
    Never inferred from .kit/config.json (a config naming an Artifact, cli or runtime says what exists or was
    installed, not where this session runs): detected from the session's environment and parent process only."""
    p = _norm_platform(explicit)
    if p:
        return p
    p = _norm_platform(os.environ.get("KIT_PLATFORM"))
    if p:
        return p
    if _truthy_env("CLAUDE_CODE_REMOTE") or os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID"):
        return "web"
    if _truthy_env("KIT_EMBED_PREVIEW") or _truthy_env("LMARENA") or _truthy_env("ARENA_SANDBOX"):
        return "arena"
    if os.environ.get("AGY_PID"):
        return "agy"
    if (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CLAUDE_PID") or os.environ.get("CLAUDE_PROJECT_DIR")):
        return "cli"
    p = _norm_platform(os.environ.get("KIT_CLI"))
    if p:
        return p
    if _truthy_env("ANTIGRAVITY_AGENT") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID"):
        return "agy"
    if _ancestor("agy"):
        return "agy"
    if _ancestor("claude"):
        return "cli"
    return "generic"


def _rule(name, platform=None):
    """Read one row of PLATFORM_RULES for `platform` (default: _platform()). Refuses an unknown rule name."""
    pl = _platform(platform)
    return PLATFORM_RULES[name][pl]


def _is_web():
    return _platform() == "web"


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


def artifact_here():
    """CL51 / R5: the Artifact is Claude Code web's alone (PLATFORM_RULES['artifact_steps']). On a computer, a session
    works with the local board, which reaches GitHub (board-mirror), and never writes to or waits on the Artifact: no
    STALE gate, no push at launch or adopt, no "not on the board yet" at done. The attached board on the web, else None."""
    return attached_board() if _rule("artifact_steps") else None


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


SCOPE_CARDS = ("_orchestrator", "_trains", "_dashboard", "_prefs", "_files")   # CL71: _files, the Artifact's files map


def _scope_post(post=None):
    """M11: the post a part's first pull is scoped to: --post, else the one `resume --role part --post X` named here."""
    return post or str(_art_state().get("scope_post") or "")


def _scoped_pull_lines(board, d, pre, post):
    """M11: a part's first pull in a fresh copy: only the documents it works on (its post, its comments, the cards,
    its train's post), each by id, instead of a list of the whole board (~10k characters a call in the reply, 48
    calls in 20 of 20 sessions on 11-26). The rest of the copy is the git seed; later pulls bring every change."""
    ids = [post] + list(SCOPE_CARDS)
    try:
        doc = _board_request(local_board_url(), "doc/posts/%s" % urllib.parse.quote(post, safe=""),
                             token=_resolve_board_token(None))
        tr = _norm_train((doc.get("data") or {}).get("train")) if doc.get("exists") else ""
        if tr:
            ids.append("_train-" + tr)
    except Exception:
        pass
    lines = ["# The board from the Artifact to this copy (%s): only post %s and what it needs (a first pull; "
             "`--full` for the whole board)" % (board, post), ""]
    for did in ids:
        lines.append("- `ArtifactData` `get`, `url` %s, `collection: \"%sposts\"`, `doc_id: \"%s\"`, `out_dir: \"%s\"` "
                     "(\"No document\" is fine)." % (board, pre, did, d))
    lines.append("- `ArtifactData` `query`, `url` %s, `collection: \"%scomments\"`, `query: {where: [[\"post_id\", \"==\", "
                 "\"%s\"]], limit: 1000}`, `out_dir: \"%s\"`." % (board, pre, post, d))
    lines.append("- `ArtifactData` `get`, `url` %s, `collection: \"%skit_docs\"`, `doc_id: \"_index\"`, `out_dir: \"%s\"` "
                 "(the files' map; \"No document\" is fine)." % (board, pre, d))
    lines += ["", "Then: `python3 -I .kit/kit.py artifact load` (starts the local board if needed; your own unsent changes "
              "stay). Every later pull brings what changed on the whole board since this one."]
    return "\n".join(lines) + "\n"


PULL_CALL = re.compile(r"`ArtifactData` `(list|query)`, `url` \S+, `collection: \"([^\"]+)\"`, `query: \{(?:where: (\[\[.*?\]\]), )?"
                       r"limit: (\d+)\}`")


def _pull_record(text):
    """M11: each whole-collection call this pull printed, whole (action, collection, where, limit), for the guard:
    kit_orchestrator_guard.py lets each through once, in the next 30 minutes (version 2 of the record)."""
    calls = []
    for a, c, w, lim in PULL_CALL.findall(text):
        try:
            where = json.loads(w) if w else None
        except ValueError:
            continue
        calls.append({"action": a, "collection": c, "where": where, "limit": int(lim)})
    try:
        os.makedirs(_art_dir(), exist_ok=True)
        write_doc(os.path.join(_art_dir(), "pull.json"), {"v": 2, "at": time.time(), "calls": calls})
    except OSError:
        pass
    return text


def _pending_record(r):
    """M11 (CR-m11): the documents the batches of this push name (and those it asks to pin), so the guard lets only
    `get` of them through until `artifact sent` records the last batch; removed when nothing is out."""
    pre = _art_prefix()
    ids = ["%s%s" % (pre, k) for k in r.get("need_pin") or []]
    for b in r.get("batches") or []:
        for e in _read_json(b) or []:
            if isinstance(e, dict):
                ids.append("%s/%s" % (e.get("collection"), e.get("doc_id")))
    p = os.path.join(_art_dir(), "pending.json")
    try:
        if ids:
            write_doc(p, {"at": time.time(), "ids": sorted(set(ids))})
        elif os.path.exists(p):
            os.remove(p)
    except OSError:
        pass
    return r


def artifact_pull(full=False, post=None):
    """The calls that bring the board here. After a first full pull, only what changed since the last load (a full
    list of a big board costs ~20 tokens a document in the tool's reply: 1,500 documents, ~30k). M11: a part's first
    pull (its post known: --post or its resume) gets only its own documents by id."""
    board = artifact_url() or "<the board's claude.ai/artifact url>"
    d = os.path.join(_art_dir(), "in")
    since = "" if full else str(_art_state().get("loaded_at") or "")
    pre = _art_prefix()
    scope = "" if full or since else _scope_post(post)
    if scope:
        for f in glob.glob(os.path.join(d, "*", "*.json")) + glob.glob(os.path.join(d, "repos", "*", "*", "*.json")):
            os.remove(f)
        return _scoped_pull_lines(board, d, pre, scope)
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
    if _rule("artifact_steps"):
        # CL45: a web session's start reconciles the Artifact with GitHub (board-mirror), not only loads it
        lines += ["", "Then: `python3 -I .kit/kit.py artifact reconcile` (it runs `artifact load`, then the Artifact against "
                  "origin/board-mirror both ways, and prints the batches to send; your own unsent changes stay)."]
    else:
        lines += ["", "Then: `python3 -I .kit/kit.py artifact load` (starts the local board if needed; your own unsent changes stay)."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CL72: one command for every board read and write
# The owner, 2026-11-27: "No session may do anything by hand." `kit.py artifact sync` is the one door: each run does what
# it can itself (load, push, pin, record sent) and prints at most ONE set of ArtifactData calls, each as the exact tool
# input (JSON) to pass, nothing to fill in; the kit records them (kit_config.ledger_record) and the guard lets only those
# through. The versions the replies name are read by the PostToolUse hook (kit_config.seen_record), never copied by hand.


def _kc():
    """The kit's shared hook helpers (kit_config.py): the one list of the calls the kit allows (CL72)."""
    for d in (os.path.join(ROOT, ".kit", "hooks"), os.path.join(ROOT, "kit", "hooks"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit", "hooks"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")):
        p = os.path.join(d, "kit_config.py")
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("kit_config_cl72", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "ledger_record"):
                return mod
    raise Conflict("the kit's hooks/kit_config.py (CL72) is missing: `python3 install.py . --update`")


def _record_printed(calls, by):
    """CL72: a call the kit prints outside `artifact sync` (a step post on an attached board) is recorded the same way,
    so the guard lets exactly it through; a kit without the shared list records nothing (the guard then refuses)."""
    try:
        _kc().ledger_record(_art_dir(), calls, by, url=artifact_url(), step="write")
    except (Conflict, OSError):
        pass


def _pull_calls(full=False, post=None):
    """The pull's calls as ArtifactData tool inputs (what `artifact pull` prints as text)."""
    board = artifact_url()
    d = os.path.join(_art_dir(), "in")
    since = "" if full else str(_art_state().get("loaded_at") or "")
    pre = _art_prefix()
    scope = "" if full or since else _scope_post(post)
    calls = []
    if scope:
        ids = [scope] + list(SCOPE_CARDS)
        try:
            doc = _board_request(local_board_url(), "doc/posts/%s" % urllib.parse.quote(scope, safe=""),
                                 token=_resolve_board_token(None))
            tr = _norm_train((doc.get("data") or {}).get("train")) if doc.get("exists") else ""
            if tr:
                ids.append("_train-" + tr)
        except Exception:
            pass
        calls += [{"action": "get", "url": board, "collection": pre + "posts", "doc_id": i, "out_dir": d} for i in ids]
        calls.append({"action": "query", "url": board, "collection": pre + "comments",
                      "query": {"where": [["post_id", "==", scope]], "limit": 1000}, "out_dir": d})
        calls.append({"action": "get", "url": board, "collection": pre + "kit_docs", "doc_id": "_index", "out_dir": d})
        return calls
    if since:
        t = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(minutes=10)
        since = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    colls = [pre + c for c in ART_COLLS]
    if not pre:
        colls += ["repos/%s/%s" % (rid, c) for rid in _attached_ids() for c in ART_COLLS]
    for c in colls:
        if since:
            field = "updated_at" if c.endswith("posts") else "created_at"
            calls.append({"action": "query", "url": board, "collection": c,
                          "query": {"where": [[field, ">", since]], "limit": 1000}, "out_dir": d})
        else:
            calls.append({"action": "list", "url": board, "collection": c, "query": {"limit": 1000}, "out_dir": d})
    calls.append({"action": "get", "url": board, "collection": pre + "kit_docs", "doc_id": "_index", "out_dir": d})
    if not pre:
        calls.append({"action": "get", "url": board, "collection": "kit_repos", "doc_id": "_index", "out_dir": d})
    return calls


def _sync_path():
    return os.path.join(_art_dir(), "sync.json")


def _calls_text(calls, what):
    lines = ["# %s: make these %d ArtifactData call(s), each with exactly this input (copy it whole; nothing to fill in)"
             % (what, len(calls)), ""]
    for c in calls:
        lines.append("- `ArtifactData` %s" % json.dumps(c, ensure_ascii=False))
        if c.get("action") == "list":
            lines.append("  (while it returns a `next_cursor`: the same input with `query.cursor` set to it)")
        if c.get("action") == "get":
            lines.append("  (\"No document\" is fine)")
    lines += ["", "Then: `python3 -I .kit/kit.py artifact sync` again (it reads the versions from the replies itself). "
              "Any other board call is refused (CL72)."]
    return "\n".join(lines) + "\n"


def _print_calls(calls, by, what, step):
    _kc().ledger_record(_art_dir(), calls, by, url=artifact_url(), step=step)
    return _calls_text(calls, what)


def _own_docs(post):
    """CL72: a part sends only its own documents: its post, the comments under it, its activity line."""
    if not post:
        return None

    def own(coll, did, data):
        if coll == "posts":
            return did == post
        if coll == "comments":
            return str((data or {}).get("post_id") or "") == post
        return did == post
    return own


def _seen_versions(kc, since):
    """{this copy's key: version} of the documents the replies named since `since` (the PostToolUse hook's record)."""
    out = {}
    for k, v in (kc.seen_read(_art_dir()).get("versions") or {}).items():
        if isinstance(v, dict) and float(v.get("at") or 0) >= since - 1:
            out[_art_key(k)] = v
    return out


def artifact_sync(post=None, full=False, url=None, token=None):
    """`kit.py artifact sync`: the next step of pull, load, push (a part: its own documents), pin, retry, sent.
    -> (text, done?)."""
    if not artifact_url():
        raise Conflict("no Artifact board in .kit/config.json: the local board is the board here")
    kc = _kc()
    base = _art_dir()
    sy = _read_json(_sync_path()) or {}
    post = post or _scope_post() or ""
    log = []

    def save(**kw):
        sy.update(kw)
        os.makedirs(base, exist_ok=True)
        write_doc(_sync_path(), sy)

    def waiting():
        left = [c for c, used in kc.ledger_calls(base) if not used]
        if left:
            return _calls_text(left, "Not made yet (the guard lets exactly these through)")
        return None

    phase = sy.get("phase") or "idle"
    if phase in ("pull", "pin"):
        w = waiting()
        if w:
            return w, False
    if phase == "pull":
        r = artifact_load(url=url, token=token)
        st = _art_state()
        for k, v in _seen_versions(kc, float(sy.get("at") or 0)).items():
            if _read_json(_base_path(*k.split("/", 1))) is not None and k.count("/") == 1:
                st["versions"][k] = int(v["version"])        # the version the pull's reply named: no pin round trip
        _art_save(st)
        log.append("Loaded: %d taken, %d kept from here, %d merged." % (r["taken"], r["kept_here"], r["merged"]))
        phase = "push"
    elif phase == "pin":
        pairs = []
        for key, v in _seen_versions(kc, float(sy.get("at") or 0)).items():
            if key not in (sy.get("pins") or []) or not v.get("path"):
                continue
            coll, did = key.split("/", 1)
            dst = os.path.join(_pin_dir(), "v%d" % int(v["version"]), *(_art_prefix() + coll).split("/"), did + ".json")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.abspath(v["path"]) != os.path.abspath(dst) and os.path.isfile(v["path"]):
                shutil.copyfile(v["path"], dst)
            pairs.append("%s=%d" % (key, int(v["version"])))
        missing = [k for k in (sy.get("pins") or []) if not any(p.startswith(k + "=") for p in pairs)]
        st = _art_state()
        for k in missing:                                  # "No document": not in the Artifact, a create
            if k not in st.get("not_there", []):
                st.setdefault("not_there", []).append(k)
            st["versions"].pop(k, None)
        _art_save(st)
        rep = []
        artifact_pin(pairs, url=url, token=token, report=rep)
        log += rep
        phase = "push"
    elif phase == "batch" and (kc._ledger_read(base) or {}).get("by") != "artifact sync":
        phase = "push"                     # another kit command rebuilt the batches since: build them again
    if phase == "batch":
        b = sy.get("batch") or ""
        first = (_read_json(b) or [{}])[0] if os.path.isfile(b) else {}
        out = (kc.seen_read(base).get("batches") or {}).get(os.path.realpath(str(first.get("file_path") or "")))
        if out is None or float(out.get("at") or 0) < float(sy.get("at") or 0) - 1:
            w = waiting()
            if w:
                return w, False
            out = {"ok": False}                      # made, its reply unseen (a refusal comes back as an error, which
            #                                          the hook may not get): re-read its documents; what went in matches
        if out.get("ok"):
            n = artifact_sent(b)
            log.append("Sent: %d writes (%s)." % (n, os.path.basename(b)))
            rest = [p for p in (sy.get("batches") or []) if p != b and os.path.isfile(p)]
            if rest:
                save(phase="batch", batch=rest[0], batches=rest, at=time.time())
                return "\n".join(log) + "\n" + _print_calls(
                    [{"action": "batch", "url": artifact_url(), "writes": _read_json(rest[0])}], "artifact sync",
                    "Send the next batch", "batch"), False
            for f in glob.glob(os.path.join(base, "out", "batch-*.json")):
                os.remove(f)
            save(phase="idle", at=time.time(), batch=None, batches=[])
            kc.ledger_record(base, [], "artifact sync", url=artifact_url(), step="idle")
            return "\n".join(log + ["In sync: the Artifact has every change of yours."]) + "\n", True
        # refused: re-read the documents it names, pin them (merging this copy into the live one), push again
        keys = [_art_key("%s/%s" % (e.get("collection"), e.get("doc_id"))) for e in (_read_json(b) or [])]
        return _pin_step(keys, save, log + ["The batch was refused (a document changed there): re-reading it."]), False
    if phase == "idle":
        calls = _pull_calls(full=full, post=post if not _art_state().get("loaded_at") else None)
        artifact_pull(full=full, post=post)            # its side effects: the old pulled files go
        save(phase="pull", at=time.time(), post=post)
        return _print_calls(calls, "artifact sync", "Pull the board's changes", "pull"), False
    # push
    r = artifact_push(url=url, token=token, own=_own_docs(post))
    if r.get("files"):
        log.append(r["files"].rstrip("\n"))
    if r.get("dropped"):
        log.append("Not sent, older than the Artifact's copy (put back here): %s." % ", ".join(r["dropped"]))
    if r.get("need_pin"):
        for f in r.get("batches") or []:
            os.remove(f)
        return _pin_step(r["need_pin"], save, log), False
    if not r.get("batches"):
        save(phase="idle", at=time.time(), batch=None, batches=[])
        kc.ledger_record(base, [], "artifact sync", url=artifact_url(), step="idle")
        return "\n".join(log + ["In sync: the Artifact has every change of yours."]) + "\n", True
    _pending_record(r)
    save(phase="batch", batch=r["batches"][0], batches=r["batches"], at=time.time())
    return "\n".join(log) + ("\n" if log else "") + _print_calls(
        [{"action": "batch", "url": artifact_url(), "writes": _read_json(r["batches"][0])}], "artifact sync",
        "Send your changes", "batch"), False


def _pin_step(keys, save, log):
    """Re-read each document to pin (its live copy and version, saved by the hook), then sync merges and pushes again."""
    d = os.path.join(_pin_dir(), "live")
    calls = []
    for k in dict.fromkeys(keys):
        coll, did = k.split("/", 1)
        calls.append({"action": "get", "url": artifact_url(), "collection": _art_prefix() + coll, "doc_id": did,
                      "out_dir": d})
    save(phase="pin", pins=list(dict.fromkeys(keys)), at=time.time())
    return "\n".join(log) + ("\n" if log else "") + _print_calls(calls, "artifact sync",
                                                                 "Read the live copies to merge into", "pin")


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


_CLOSED = ("complete", "completed", "done", "dropped", "shipped", "merged", "closed")


def _status_rank(st):
    st = str(st or "").strip().lower()
    if not st:
        return None
    if st in _CLOSED:
        return 3
    if "review" in st:
        return 2
    if "progress" in st or st in ("running", "doing", "held", "blocked"):
        return 1
    return 0 if st in ("planned", "open", "todo", "queued") else None


def keep_newer(incoming, here):
    """CL51 (the 20:23Z overwrite: CL44's local part loaded the Artifact's older copies over 11 newer posts of the local
    board, CL43 back to planned): the copy a load writes. The newer of the two by updated_at stands, with only the fields
    it lacks taken from the other; a status (or a done column) is never lowered."""
    if not isinstance(here, dict) or not isinstance(incoming, dict):
        return incoming if incoming is not None else here
    a, b = here.get("updated_at"), incoming.get("updated_at")
    newer_here = isinstance(a, str) and (not isinstance(b, str) or a > b)
    first, other = (here, incoming) if newer_here else (incoming, here)
    out = dict(first)
    for k, v in other.items():
        if k not in out:
            out[k] = v
    rh, ro = _status_rank(here.get("status")), _status_rank(out.get("status"))
    if rh is not None and (ro is None or ro < rh):
        out["status"] = here["status"]
    if here.get("column") == "done" and out.get("column") != "done":
        out["column"] = "done"
    return out


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
                new = keep_newer(new, local)
                merged += new != local
                kept += new == local
            else:
                new = keep_newer(remote, local)       # CL51: an older copy there never goes back over a newer one here
                taken += new != local
                kept += local is not None and new == local and local != remote
            if new != local:
                _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did, "data": new,
                                                             "if_version": d.get("version", 0)}], "sync": True}, token=token)
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
    # CL72: only what is older than the last load is a seed; a post or comment made here since (a sync pulls before
    # it pushes) is this session's new work and goes out with the next push
    last = str(st.get("loaded_at") or "")
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            key = "%s/%s" % (coll, row["id"])
            stamp = _doc_stamp(row.get("data"))
            if last and (not stamp or stamp >= last):
                continue
            if not os.path.exists(_base_path(coll, row["id"])) and key not in st["versions"]:
                stale.add(key)
                os.makedirs(os.path.dirname(_base_path(coll, row["id"])), exist_ok=True)
                write_doc(_base_path(coll, row["id"]), row.get("data") or {})
    st["not_there"] = sorted(stale)
    st["loaded_at"] = zstamp(datetime.now(timezone.utc))
    _art_save(st)
    files_card_sync(url, token)                         # CL71: the files map from the board, in a clone that has none
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
    if _TEST_GUARD:     # CL48
        _TEST_GUARD.check_board(url, "POST")
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
    """CL30 / R5: '' when the scripts may read the local copy, else why not (an Artifact board whose copy was never loaded,
    or loaded longer ago than kit.artifact.fresh_min, 15). $KIT_STALE_OK=1 reads it anyway.
    Gated by PLATFORM_RULES['stale_gate']: only platforms that use the Artifact are ever held by it."""
    if not _rule("stale_gate") or not artifact_here() or os.environ.get("KIT_STALE_OK"):
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
        if _rule("artifact_steps"):      # CL45: a web session's first load is its reconcile with GitHub
            way = way.replace("kit.py artifact load`", "kit.py artifact reconcile` (the Artifact against GitHub, both ways)")
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
    own = _own_docs(_scope_post())                 # CL72: a part answers for its own documents only (what it sends)
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            base = _read_json(_base_path(coll, row["id"]))
            data = row.get("data") or {}
            if own is not None and not own(coll, row["id"], data):
                continue
            if base != data and not (_is_card(row["id"]) and base is not None and _card_older(data, base)):
                n += 1                         # CL31: an older card here is dropped at push, not unsent
    return {"changed": n, "batches": sorted(glob.glob(os.path.join(_art_dir(), "out", "batch-*.json")))}


def launch_mark(pid, prompt, inline=None):
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
    write_doc(p, dict({"prompt": prompt, "at": time.time()}, **({"inline": inline} if inline else {})))


def artifact_push(url=None, token=None, out_dir=None, own=None):
    if attached_board() and not _art_state().get("loaded_at"):
        # CL28's open item: a web part's copy came from git; its push built 78 writes without if_version
        raise Conflict("this copy was never loaded from the Artifact, so a push would overwrite the board with an old "
                       "copy: run `python3 -I .kit/kit.py artifact pull`, the calls it prints, `kit.py artifact load`, "
                       "then push")
    url = url or local_board_url()
    token = _resolve_board_token(token)
    out_dir = out_dir or os.path.join(_art_dir(), "out")
    if own is None:
        own = _own_docs(_scope_post())               # CL72: a part's push (any command's) sends only its own documents
    os.makedirs(os.path.join(out_dir, "docs"), exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "batch-*.json")) + glob.glob(os.path.join(out_dir, "docs", "*.json")):
        os.remove(f)
    files_card_sync(url, token)                         # CL71: what changed in the files map rides this push
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
            if own is not None and not own(coll, did, data):
                continue                                 # CL72: a part sends only its own documents
            key = "%s/%s" % (coll, did)
            if _is_card(did) and base is not None and _card_older(data, base):
                # CL31: an older card here (a seed, a stale rebuild): put the copy the Artifact had back, never send it
                _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did,
                                                             "data": base, "if_version": row.get("version", 0)}], "sync": True}, token=token)
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
    # CL41: what a running step's session does now (activity/<post>, kit_activity.py, no model call) rides along: a web part's
    # hook writes it to this copy only, and the page reads it from the Artifact. Never pulled, never "unsent": a create the
    # first time, then pinned to the version this copy last sent.
    for row in _board_request(url, "db/activity", token=token).get("docs") or []:
        did, data = row["id"], row.get("data") or {}
        key = "activity/%s" % did
        if not isinstance(data, dict) or _read_json(_base_path("activity", did)) == data:
            continue
        if own is not None and not own("activity", did, data):
            continue
        e = {"op": "set", "collection": _art_prefix() + "activity", "doc_id": did}
        if key in st["versions"]:
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


FILES_CARD = "_files"


def _merge_files_state(a, b):
    """Two copies of the files map: every asset and document of both; a document's newer version wins, an asset's
    copy in `b` (this clone's) wins."""
    out = {"assets": dict((a or {}).get("assets") or {}), "docs": dict((a or {}).get("docs") or {})}
    out["assets"].update((b or {}).get("assets") or {})
    for did, v in ((b or {}).get("docs") or {}).items():
        cur = out["docs"].get(did) or {}
        if int((v or {}).get("version") or 0) >= int(cur.get("version") or 0):
            out["docs"][did] = v
    for k in ("index_version",):
        if (b or {}).get(k) or (a or {}).get(k):
            out[k] = max(int((a or {}).get(k) or 0), int((b or {}).get(k) or 0))
    return out


def files_card_sync(url=None, token=None):
    """CL71: the Artifact's files map (.kit/artifact-files/: what it holds of this repo's files, each mirrored document's
    version) is not in git any more (every branch rewrote it, every merge conflicted). It rides the board instead, as
    the card posts/_files, which pull, load and push already carry with their versions: a clone without the file
    rebuilds it from the card; a clone whose file gained entries puts them on the card, for this push to send."""
    kas = _kas()
    if kas is None:
        return None
    url = url or local_board_url()
    token = _resolve_board_token(token)
    path = kas.files_state_path(ROOT, _art_into())
    try:
        d = _board_request(url, "doc/posts/%s" % FILES_CARD, token=token)
    except Exception:  # noqa: BLE001
        return None
    card = (d.get("data") or {}) if isinstance(d, dict) and d.get("exists") else {}
    here = _read_json(path) if os.path.isfile(path) else None
    merged = _merge_files_state(card.get("files"), here)
    if not merged["assets"] and not merged["docs"]:
        return None
    if merged != here:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_doc(path, merged)
    if merged != card.get("files"):
        doc = {"title": "Files map (the kit's)", "files": merged, "updated_at": _now_z(),
               "body": "What the Artifact holds of this repo's files (CL71: out of git, rebuilt from here)."}
        _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": "posts", "doc_id": FILES_CARD, "data": doc,
                                                     "if_version": d.get("version", 0) if isinstance(d, dict) else 0}],
                                         "sync": True}, token=token)
    return merged


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
        # CL72: never a version copied by hand: sync re-reads them, the hook reads the versions, sync pins and pushes
        out.append("Changed here, never sent from this copy (their versions are not known here): %s. Run `python3 -I "
                   ".kit/kit.py artifact sync`: it prints the reads, then pins and sends them itself." % ", ".join(r["need_pin"]))
    calls = [{"action": "batch", "url": board, "writes": _read_json(p) or []} for p in r.get("batches") or []]
    for p, c in zip(r.get("batches") or [], calls):
        out.append("`ArtifactData` `batch`, `url` %s, `writes` = the array in %s, exactly (the guard refuses an edited "
                   "one); when it is committed: `python3 -I .kit/kit.py artifact sent %s`." % (board, p, p))
    if calls and artifact_url():
        with contextlib.suppress(Conflict, OSError):
            _kc().ledger_record(_art_dir(), calls, "artifact push", url=artifact_url(), step="batch")
    if not out:
        out.append("Nothing to send: the Artifact has every change made here.")
    out.append("A refused batch: `python3 -I .kit/kit.py artifact sync` (it re-reads what changed there, merges, pins "
               "and sends again; CL72).")
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


# ---------------------------------------------------------------- CL45: a web session reconciles its Artifact with git
# The Artifact (the web's record) and origin/board-mirror (the computer's record) each gain posts the other lacks. At a
# web session's start, after the pull: load the Artifact here, then each post and comment against git: in one side only,
# it goes to the other; changed on one side since the last reconcile, that side wins; changed on BOTH (the base, kept in
# git as board/artifact-reconciled.json, says what both had), the newer copy shows and the other is kept in a comment.
ART_RECONCILED = "board/artifact-reconciled.json"


def _doc_hash(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _doc_stamp(d, fallback=""):
    d = d if isinstance(d, dict) else {}
    return _utc(d.get("updated_at") or d.get("created_at") or "") or fallback or ""


def _conflict_comment(kind, did, kept, kept_side, other, other_side):
    """The copy that lost a both-sides change, kept as a comment under its post (the board shows it there)."""
    cid = "conflict-%s" % _doc_hash([kind, did, other])
    post = did if kind == "posts" else str((other or {}).get("post_id") or (kept or {}).get("post_id") or did)
    excerpt = str((other or {}).get("body") or (other or {}).get("text") or (other or {}).get("title") or "")
    if len(excerpt) > 1500:
        excerpt = excerpt[:1500] + "…"
    text = ("Conflict: %s %s changed both on the %s and on the %s since the last reconcile. The board shows the %s copy "
            "(newer, %s); the %s copy (%s) is kept here, whole, in this comment's `conflict.other`; nothing was lost. "
            "Pick one and write it back to the %s." % (
                "post" if kind == "posts" else "comment", did, "Artifact", "GitHub copy (board-mirror)", kept_side,
                _doc_stamp(kept) or "no date", other_side, _doc_stamp(other) or "no date", kind[:-1]))
    if excerpt:
        text += "\n\nThe %s copy:\n\n> %s" % (other_side, excerpt.replace("\n", "\n> "))
    return cid, {"post_id": post, "from": "kit", "kind": "conflict", "text": text, "created_at": _now_z(),
                 "conflict": {"doc": "%s/%s" % (kind, did), "shown": kept_side, "other_side": other_side, "other": other}}


def artifact_reconcile(m, url=None, token=None, branch=None, push=True):
    """`kit.py artifact reconcile` (after `artifact pull` and its ArtifactData calls): the Artifact against
    origin/<branch>, both ways, nothing lost. Returns the counts and the artifact push (the batches to send)."""
    if not attached_board():
        raise Conflict("this board has no Artifact (kit.artifact / a claude.ai board_url in .kit/config.json)")
    url = url or local_board_url()
    if not _ensure_local_board(url):
        raise Conflict("no local board at %s (run `python3 -I .kit/web_takeover.py` first)" % url)
    tok0 = _resolve_board_token(token)
    # CL45+CL51 (the review's redirect): the Artifact's side is its raw pulled copy, taken BEFORE the load: CL51's load
    # keeps the newer of the Artifact's copy and this board's (a git seed at a web start) and never lowers a status, so
    # after it a both-sides change or a reopen made on the Artifact could no longer be seen here
    pre = _art_prefix()
    raw, base_before, here_before = {}, {}, {}
    for coll in ART_COLLS:
        for f in glob.glob(os.path.join(_art_dir(), "in", *(pre + coll).split("/"), "*.json")):
            d = _read_json(f)
            if isinstance(d, dict):
                raw[(coll, os.path.basename(f)[:-5])] = d
        for f in glob.glob(os.path.join(_art_dir(), "base", coll, "*.json")):
            d = _read_json(f)
            if isinstance(d, dict):
                base_before[(coll, os.path.basename(f)[:-5])] = d
        for row in _board_request(url, "db/%s" % coll, token=tok0).get("docs") or []:
            here_before[(coll, row["id"])] = row.get("data") or {}
    not_there_before = set(_art_state().get("not_there") or [])
    # a review never writes the git copy (CR-cl45: a web review, launched by create_session, has no KIT_ROLE; the post
    # it reviews names it in review_session)
    me = whoami()
    reviewing = sorted(k[1] for k, d in list(raw.items()) + list(here_before.items())
                       if me and k[0] == "posts" and isinstance(d, dict) and d.get("review_session") == me)
    loaded = artifact_load(url=url, token=token)
    token = _resolve_board_token(token)
    branch = branch or BRANCH
    repo = m if os.path.exists(os.path.join(m, ".git")) else ROOT
    remote_ref = "refs/remotes/origin/" + branch
    git(repo, "fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:%s" % (branch, remote_ref))
    has_remote = git(repo, "rev-parse", "--verify", "--quiet", remote_ref).returncode == 0
    gdocs = {k: v for k, v in (_git_board_docs(repo, remote_ref) if has_remote else {}).items() if k[0] in ART_COLLS}
    base = {}
    if has_remote:
        r = git(repo, "show", "%s:%s" % (remote_ref, ART_RECONCILED))
        try:
            base = (json.loads(r.stdout) or {}).get("docs") or {} if r.returncode == 0 else {}
        except ValueError:
            base = {}
    st = _art_state()
    not_there = set(st.get("not_there") or [])
    here, art, side = {}, {}, {}
    for coll in ART_COLLS:
        for row in _board_request(url, "db/%s" % coll, token=token).get("docs") or []:
            k = (coll, row["id"])
            here[k] = (row.get("data") or {}, row.get("version", 0))
    for k in set(here) | set(raw) | set(base_before):
        key = "%s/%s" % k
        if k in raw:
            art[k] = raw[k]                    # pulled now: the Artifact's copy as it is
        elif k in base_before and key not in not_there_before:
            art[k] = base_before[k]            # not in a delta pull: what it last had
    for k in set(here) | set(art):
        a = art.get(k)
        hb, bb, g = here_before.get(k), base_before.get(k), gdocs.get(k, (None,))[0]
        if hb is not None and bb is not None and hb != bb and hb != g and "%s/%s" % k not in not_there_before:
            # this session's own edit, not yet sent (a git seed equals git's copy, and is no edit)
            side[k] = _merge_doc(k[1], a, hb, bb) if a is not None and a != bb else hb
        elif a is not None:
            side[k] = a
        elif k in here:
            side[k] = here[k][0]               # only on this board
    writes, to_git, conflicts, decided_by_date = {}, {}, [], 0
    final, got = {}, set()
    for k in sorted(set(side) | set(gdocs)):
        kind, did = k
        l = side.get(k)
        g, gt = gdocs.get(k, (None, ""))
        key = "%s/%s" % k
        if g is None:
            final[k] = l
            if key not in not_there or k in art:
                to_git[k] = l                  # the Artifact's (or this session's) document: git gets it
            continue
        if l is None:
            writes[k] = g                      # in git only: here, then the Artifact
            final[k] = g
            got.add(k)
            continue
        if l == g:
            final[k] = l
            continue
        b = base.get(key)
        hl, hg = _doc_hash(l), _doc_hash(g)
        if b and hg == b:
            win = "here"
        elif b and hl == b:
            win = "git"
        else:
            sl, sg = _doc_stamp(l), _doc_stamp(g, gt)
            win = "git" if sg > sl else "here"
            if b:                              # both changed since the last reconcile: never silent
                kept, other = (g, l) if win == "git" else (l, g)
                cid, cdoc = _conflict_comment(kind, did, kept, "GitHub" if win == "git" else "Artifact", other,
                                              "Artifact" if win == "git" else "GitHub")
                if ("comments", cid) not in here and ("comments", cid) not in gdocs:
                    writes[("comments", cid)] = cdoc
                    to_git[("comments", cid)] = cdoc
                    final[("comments", cid)] = cdoc
                conflicts.append(key)
            else:
                decided_by_date += 1
        if win == "git":
            writes[k] = g
            final[k] = g
            got.add(k)
        else:
            to_git[k] = l
            final[k] = l
    # the board here shows each final copy, whatever the load kept (CL51's keep_newer is for a plain load)
    for k, f in final.items():
        if f is not None and k not in writes and here.get(k, (None,))[0] != f:
            writes[k] = f
    # here: one document at a time, pinned to the version read before it (a post's write can touch a card)
    for (coll, did), data in sorted(writes.items()):
        api = "doc/%s/%s" % (coll, urllib.parse.quote(did, safe=""))
        for attempt in (1, 2):
            cur = _board_request(url, api, token=token)
            try:
                _board_request(url, "db/batch", {"writes": [{"op": "set", "collection": coll, "doc_id": did, "data": data,
                                                             "if_version": cur.get("version", 0)}], "sync": True}, token=token)
                break
            except urllib.error.HTTPError as e:
                if e.code != 409 or attempt == 2:
                    raise Conflict("the board kept changing under %s/%s; run it again" % (coll, did)) from e
        key = "%s/%s" % (coll, did)
        if key in not_there and (coll, did) not in art:
            # O53 kept a document the Artifact never had from being sent; one git holds goes now, as a create
            not_there.discard(key)
            try:
                os.remove(_base_path(coll, did))
            except OSError:
                pass
    # what this copy held that only git also holds (a git seed made before this): the Artifact gets it too
    for k, (g, _gt) in gdocs.items():
        key = "%s/%s" % k
        if key in not_there and k not in art and k not in writes and here.get(k, (None,))[0] == g:
            not_there.discard(key)
            try:
                os.remove(_base_path(*k))
            except OSError:
                pass
    st = _art_state()
    st["not_there"] = sorted(not_there)
    _art_save(st)
    # git: the documents it lacks or holds older, and the base (each document both sides will hold), in one commit
    pushed = "git copy not updated (--no-push)"
    if push and reviewing:
        pushed = "git copy not updated (this session reviews %s: a review never writes it)" % reviewing[0]
    elif push:
        if not os.path.exists(os.path.join(m, ".git")):
            pushed = "git copy not updated (no board-mirror worktree at %s)" % m
        else:
            git(m, "fetch", "-q", "--no-tags", "origin", branch)
            if has_remote and git(m, "merge", "--ff-only", "origin/" + branch).returncode != 0:
                if git(m, "merge", "--no-edit", "-X", "ours", "origin/" + branch).returncode != 0:
                    git(m, "merge", "--abort")
            new_base = dict(base)
            for k, f in final.items():
                if f is not None and art.get(k) == f and (to_git.get(k, gdocs.get(k, (None,))[0]) == f):
                    new_base["%s/%s" % k] = _doc_hash(f)   # both sides hold it: the next reconcile's base
            for (coll, did), data in to_git.items():
                write_doc(os.path.join(m, "board", coll, did + ".json"), data)
            write_doc(os.path.join(m, ART_RECONCILED), {"docs": dict(sorted(new_base.items())), "at": _now_z()})
            if git(m, "status", "--porcelain", "board").stdout.strip():
                git(m, "add", "board")
                git(m, "commit", "-q", "-m", "kit artifact reconcile: %d to git, %d conflict(s)" % (len(to_git), len(conflicts)))
                r = git(m, "push", "-q", "origin", "HEAD:" + branch)
                pushed = "pushed to origin/%s" % branch if r.returncode == 0 else \
                    "push to origin/%s refused: %s" % (branch, (r.stdout + r.stderr).strip()[-200:])
            else:
                pushed = "git copy already current"
    out = artifact_push(url=url, token=token)
    from_git = sorted("%s/%s" % k for k in got)
    return {"loaded": loaded, "from_git": from_git, "to_git": sorted("%s/%s" % k for k in to_git), "conflicts": conflicts,
            "by_date": decided_by_date, "git": pushed, "push": out}


def artifact_reconcile_line(r):
    """The one line: what came from GitHub, what went to it, the conflicts kept."""
    def few(xs):
        return ("" if not xs else " (%s%s)" % (", ".join(x.split("/", 1)[1] for x in xs[:6]), ", …" if len(xs) > 6 else ""))
    p = r.get("push") or {}
    return ("Artifact vs GitHub: %d from GitHub%s; %d to GitHub%s, %s; %d conflict(s)%s%s; to the Artifact: %d write(s) in "
            "%d batch(es)." % (len(r["from_git"]), few(r["from_git"]), len(r["to_git"]), few(r["to_git"]), r["git"],
                               len(r["conflicts"]), few(r["conflicts"]),
                               " (both kept, see each post's conflict comment)" if r["conflicts"] else "",
                               p.get("writes", 0), len(p.get("batches") or [])))


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
                                                               "if_version": d.get("version", 0)}], "sync": True}, token=tok)
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
            "- **One-word launcher:** type `%s` in any terminal to start the board, print your link, and open Claude Code (every check off once you accepted Claude Code's bypass mode yourself, else auto: `kit.py permission-mode`) as the orchestrator, with `kit_guard.py` guarding every command (destructive commands stay blocked); `%s --agy` opens agy instead." % (cmd, cmd),
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
    g = _post_gate()
    lines.extend(["", "These start now; say which to hold." if g and g.autonomy(_kit_config()) == "full_auto" else
                  "A Like on this message launches them all; or tell me which."])
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
    if _TEST_GUARD:     # CL48: a test run writes a board's git copy under a temp folder only
        _TEST_GUARD.check_path(m, "to a board's git copy")
    if os.path.exists(os.path.join(m, ".git")):
        br = git(m, "symbolic-ref", "--short", "HEAD").stdout.strip() or BRANCH
        return "init: mirror already exists at %s (branch %s). Next: python3 -I .kit/kit.py sync-db --commit" % (m, br)
    subprocess.run(["git", "-C", ROOT, "fetch", "origin", BRANCH], capture_output=True)
    has = subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "origin/" + BRANCH], capture_output=True).returncode == 0
    if has:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "-B", BRANCH, m, "origin/" + BRANCH], capture_output=True, text=True)
        src = "origin/" + BRANCH
    elif subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "--quiet", "refs/heads/" + BRANCH], capture_output=True).returncode == 0:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", m, BRANCH], capture_output=True, text=True)     # made here, never pushed
        src = "the local branch " + BRANCH
    else:
        r = subprocess.run(["git", "-C", ROOT, "worktree", "add", "--orphan", "-b", BRANCH, m], capture_output=True, text=True)
        src = "new orphan branch " + BRANCH
        if r.returncode != 0:                                 # git before 2.42 has no --orphan here (Debian 12: 2.39)
            for c in (["-C", ROOT, "worktree", "add", "--detach", "--no-checkout", m, "HEAD"],
                      ["-C", m, "symbolic-ref", "HEAD", "refs/heads/" + BRANCH], ["-C", m, "read-tree", "--empty"]):
                r = subprocess.run(["git"] + c, capture_output=True, text=True)
                if r.returncode != 0:
                    break
    if r.returncode != 0:
        return "FAILED: " + ((r.stdout + r.stderr).strip() or "git worktree add")
    return "init: created worktree %s from %s. Next: python3 -I .kit/kit.py sync-db --commit" % (m, src)



# ---------------------------------------------------------------- CL38: attaching another repo on Claude Code on the web
ADD_REPO_RULE = "mcp__claude-code-remote__add_repo"


def add_repo_allowed(root=None):
    """(needs it, has it): a project with Claude Code settings and an attached board (portfolio, an Artifact board, or
    a kit repo) needs mcp__claude-code-remote__add_repo in .claude/settings.json permissions.allow, else auto mode's
    classifier refuses add_repo ("Unauthorized Access") in every web session it starts."""
    root = root or ROOT
    st = _read_json(os.path.join(root, ".claude", "settings.json"))
    cfg = _read_json(os.path.join(root, ".kit", "config.json")) or {}
    kit = cfg.get("kit") or {}
    attached = bool(cfg.get("portfolio") or (kit.get("artifact") or {}).get("url")
                    or os.path.isfile(os.path.join(root, "install.py")))
    if st is None or not attached:
        return False, False
    allow = ((st.get("permissions") or {}).get("allow") or [])
    return True, ADD_REPO_RULE in allow


WEB_ENV_POST = "KIT-web-env"


def web_env(cfg=None):
    """CL39: kit.web_env from .kit/config.json ({} when the project does not manage several repos on the web)."""
    we = ((cfg if cfg is not None else _kit_config()).get("kit") or {}).get("web_env") or {}
    return we if we.get("on", True) and we else {}


def web_env_call(we, cfg=None):
    """The create_environment call the orchestrator makes once, on the owner's Like (never in chat)."""
    cfg = cfg if cfg is not None else _kit_config()
    return {"name": we.get("name") or _slug(cfg.get("project") or "project"), "scope": "personal",
            "reason": "One environment holding every repo of %s, its hooks installed at user level."
                      % (cfg.get("project") or "the project"),
            "repositories": list(we.get("repos") or []), "setup_script": we.get("setup_script") or "",
            "switch_session": "none"}


def web_env_state(posts=None, comments=None, cfg=None):
    """(state, text): 'off', 'ready' (an environment_id), 'unasked' (no post), 'asked' (no Like yet), 'liked'."""
    we = web_env(cfg)
    if not we:
        return "off", ""
    if we.get("environment_id"):
        return "ready", ("Web environment %s holds %s: the hand-over and the clerk start the orchestrator there "
                         "(`environment_id`), its setup script installs the hooks at user level; nobody calls add_repo."
                         % (we["environment_id"], ", ".join(we.get("repos") or [])))
    if posts is None:
        posts, comments = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"))
    if WEB_ENV_POST not in (posts or {}):
        return "unasked", ("Web environment for %s not created yet: post the question once, `python3 -I .kit/kit.py "
                           "web-env ask` (the owner's Like on it is the go; never ask in chat)."
                           % ", ".join(we.get("repos") or []))
    liked = any(c.get("like") and c.get("post_id") == WEB_ENV_POST and c.get("from") in ("owner", None)
                for c in (comments or {}).values())
    if not liked:
        return "asked", ""          # an open question post: resume lists it with the others
    return "liked", ("The owner liked %s: make the claude-code-remote `create_environment` call `python3 -I .kit/kit.py "
                     "web-env` prints (once), then `python3 -I .kit/kit.py web-env --id <its env_ id>`." % WEB_ENV_POST)


def web_env_cmd(action=None, env_id=None):
    """`kit.py web-env [ask] [--id env_...]`. Returns (text, rc)."""
    p = os.path.join(ROOT, ".kit", "config.json")
    cfg = _read_json(p) or {}
    we = web_env(cfg)
    if not we:
        return ("web-env: this project does not manage several repos on the web (kit.web_env is off): "
                "install.py <project> --update --web-multi yes [--web-repos owner/a,owner/b]"), 1
    if env_id:
        if not re.match(r"^env_[A-Za-z0-9]+$", env_id):
            return "web-env: --id takes the env_... id create_environment returned (got %r)" % env_id, 2
        cfg["kit"]["web_env"]["environment_id"] = env_id
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        return ("web-env: environment %s recorded in .kit/config.json (commit it): the hand-over and the clerk now start "
                "the orchestrator there." % env_id), 0
    call = web_env_call(we, cfg)
    if action == "ask":
        body = ["Like this post to create it: the orchestrator then makes the call below once.", "",
                "## Why",
                "A web session started on one repo runs only that repo's hooks, and reaching another repo needs add_repo, "
                "which a launched session cannot make. One environment holding every repo, with a setup script that "
                "installs the project's hooks at user level (`.kit/user_hooks.py install`), gives every orchestrator "
                "all the repos and the hooks from its first turn.", "",
                "## The environment",
                "- repositories: %s" % ", ".join(call["repositories"]),
                "- setup script:", "", "```", call["setup_script"], "```", "",
                "- scope: personal; this session stays where it is (`switch_session: none`)."]
        doc = {"title": "Create the web environment holding %s?" % ", ".join(call["repositories"]),
               "kind": "question", "column": "question", "status": "open", "from": "kit web-env",
               "default": "Like: create it", "body": "\n".join(body), "posted_at": _now_z(), "created_at": _now_z()}
        ok = _board_write("set", "posts", WEB_ENV_POST, doc)
        return ("web-env: question posted as %s (with an Artifact board: `kit.py artifact push`); the owner's Like is the "
                "go." % WEB_ENV_POST if ok else "web-env: could not write post %s (no board here?)" % WEB_ENV_POST), (0 if ok else 1)
    state, text = web_env_state(cfg=cfg)
    out = ["# Web environment (several repos, CL39): %s" % state]
    if text:
        out.append(text)
    out += ["", "The claude-code-remote `create_environment` call (made once, after the owner's Like on %s):" % WEB_ENV_POST,
            json.dumps(call, ensure_ascii=False, indent=1)]
    return "\n".join(out), 0


def attach_repo(spec, root=None, clone=True):
    """`kit.py attach <owner/repo>`: the add_repo call to make, a clone next to this project, the register_repo_root
    call, and the repo recorded in .kit/config.json portfolio. Returns (text, rc)."""
    root = root or ROOT
    m_ = re.match(r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", spec.strip())
    if not m_:
        return "attach: name the repo as owner/repo (got %r)" % spec, 2
    owner, repo = m_.group(1), m_.group(2)
    gh = "%s/%s" % (owner, repo)
    dest = os.path.join(os.path.dirname(os.path.abspath(root)), repo)
    out = ["# Attach %s to this board" % gh]
    need, has = add_repo_allowed(root)
    if need and not has:
        out.append("WARNING: .claude/settings.json does not allow %s: run `python3 -I install.py <this project> --update` "
                   "from the kit, commit it, and start a NEW session (a running one never rereads its settings)" % ADD_REPO_RULE)
    if not os.path.isdir(os.path.join(dest, ".git")):
        if clone:
            r = subprocess.run(["git", "clone", "-q", "https://github.com/%s.git" % gh, dest],
                               capture_output=True, text=True, timeout=600)
            ok = r.returncode == 0
        else:
            ok = False
        if not ok:
            out += ["1. Make this call (Claude Code on the web; on a computer, clone it yourself):",
                    "   `mcp__claude-code-remote__add_repo` {\"owner\": \"%s\", \"repo\": \"%s\", \"access\": \"read\"}"
                    % (owner, repo),
                    "   (\"push\" only if this session must push to it).",
                    "2. Then run this command again: it clones %s into %s and records it." % (gh, dest)]
            return "\n".join(out), 1
        out.append("cloned: %s -> %s" % (gh, dest))
    else:
        out.append("clone: %s (already there)" % dest)
    p = os.path.join(root, ".kit", "config.json")
    cfg = _read_json(p)
    if cfg is None:
        return "\n".join(out + ["attach: no .kit/config.json in %s (install the kit first)" % root]), 2
    port = cfg.setdefault("portfolio", [])
    row = next((x for x in port if isinstance(x, dict) and (x.get("github") == gh or
                                                             os.path.abspath(x.get("path") or "/-") == dest)), None)
    if row is None:
        port.append({"name": repo, "path": dest, "github": gh})
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        out.append("recorded: .kit/config.json portfolio += %s (commit it)" % gh)
    else:
        changed = False
        for k_, v_ in (("github", gh), ("path", dest)):
            if not row.get(k_):
                row[k_] = v_
                changed = True
        if changed:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
        out.append("recorded: already in .kit/config.json portfolio%s" % (" (filled in)" if changed else ""))
    out.append("Last (on the web): `mcp__claude-code-remote__register_repo_root` {\"owner\": \"%s\", \"repo\": \"%s\", "
               "\"directory\": \"%s\"} so its CLAUDE.md and skills load." % (owner, repo, dest))
    return "\n".join(out), 0


# ---------------------------------------------------------------- CL38: branches fully on the trunk, swept
KEEP_BRANCHES = ("board-mirror", "kit-usage")


def merged_branches(root=None, delete=False):
    """`kit.py branches [--delete]`: origin's branches whose every commit is on origin/<trunk> (a part's `cl<N>`, an
    old `part-<post>`), listed; with --delete, removed from origin in one push. The trunk, the board mirror, the usage
    ledger and the branch checked out here are never touched; a branch with a commit not on the trunk is kept and
    named. Returns (text, rc)."""
    root = root or ROOT

    def g(*a):
        return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=300)
    cfg = _read_json(os.path.join(root, ".kit", "config.json")) or {}
    trunk = str((cfg.get("kit") or {}).get("main_branch") or "main")
    keep = {trunk, "HEAD", (cfg.get("mirror") or {}).get("branch") or "board-mirror"} | set(KEEP_BRANCHES)
    keep.add(g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip())
    try:              # a part still at work keeps its branch, even fully merged (it pushes there again)
        busy = {str(p.get("branch")) for p in _all_posts().values() if isinstance(p, dict) and p.get("branch")
                and str(p.get("status") or "").lower() not in ("done", "dropped")}
        keep |= busy | {"part-" + _slug(pid, 60) for pid, p in _all_posts().items() if isinstance(p, dict)
                        and str(p.get("status") or "").lower() not in ("done", "dropped") and p.get("branch")}
    except Exception:
        pass
    if g("fetch", "-q", "--prune", "origin").returncode:
        return "branches: cannot fetch origin", 1
    names = [x[len("origin/"):] for x in g("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").stdout.split()
             if x.startswith("origin/")]
    done_, kept = [], []
    for b in sorted(names):
        if b in keep:
            continue
        (done_ if g("merge-base", "--is-ancestor", "origin/" + b, "origin/" + trunk).returncode == 0 else kept).append(b)
    out = ["# Branches on origin (%d): %d fully on %s, %d with work not on it" % (len(names), len(done_), trunk, len(kept)),
           "fully on %s: %s" % (trunk, " ".join(done_) or "none"),
           "kept (work not on %s): %s" % (trunk, " ".join(kept) or "none")]
    if not delete:
        out.append("Delete those fully on %s: `python3 -I .kit/kit.py branches --delete` (the owner's go)." % trunk)
        return "\n".join(out), 0
    if not done_:
        return "\n".join(out + ["nothing to delete"]), 0
    r = g("push", "origin", "--delete", *done_)
    if r.returncode:
        return "\n".join(out + ["delete failed: %s" % (r.stderr or r.stdout).strip()[-300:]]), 1
    return "\n".join(out + ["deleted %d branch(es) from origin" % len(done_)]), 0


def whoami(name=""):
    """`kit.py whoami [name]`: the session id a post's session_id names. A Claude Code web session: `session_<id>`
    (from CLAUDE_CODE_REMOTE_SESSION_ID `cse_<id>`, the id send_message and get_session take); else KIT_SESSION_ID;
    else CLAUDE_CODE_SESSION_ID (a CLI session's real id); else the name given (`claude --bg -n <name>`, no id known);
    else nothing (O55: never a made-up id)."""
    rid = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or ""
    if rid:
        return "session_" + rid.split("_", 1)[1] if rid.startswith("cse_") else rid
    return os.environ.get("KIT_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or name or ""


# CL49: a part reports to the orchestrator that started it, on the device that started it, unless another claimed it.
# A fixed name (`<slug>-orchestrator`) matched dead sessions on another computer; the post now records its owner.
OWNER_KEYS = ("orchestrator", "orchestrator_name", "orchestrator_device")
REVIEW_OWNER_KEYS = ("review_orchestrator", "review_orchestrator_name", "review_orchestrator_device")


def my_device():
    """The device this session runs on: `web` for a Claude Code cloud session (send_message reaches it by its id from
    anywhere), else KIT_DEVICE, else the host name."""
    if _platform() == "web":
        return "web"
    if os.environ.get("KIT_DEVICE"):
        return os.environ["KIT_DEVICE"]
    import socket
    return socket.gethostname() or "unknown"


def orchestrator_here():
    """This orchestrator's address as a post records it: {orchestrator: its id (whoami), orchestrator_name: the session
    name a CLI SendMessage takes, orchestrator_device}. Empty when no id is known (never a made-up one)."""
    slug = _slug(_kit_config().get("project") or "project")
    dev = my_device()
    name = "" if dev == "web" else "%s-orchestrator" % slug
    addr = whoami(name)
    if not addr:
        return {}
    return {"orchestrator": addr, "orchestrator_name": name or addr, "orchestrator_device": dev}


def step_owner(p, posts=None, review=False):
    """Who a step's part (review=True: its review) reports to: {address, name, device, how}. The post's own record
    (written by `launch`, `review` or `claim`); a post from before CL49 has none: the board's `_orchestrator`."""
    p = p if isinstance(p, dict) else {}
    if review and p.get("review_orchestrator"):
        return {"address": str(p["review_orchestrator"]), "name": str(p.get("review_orchestrator_name") or p["review_orchestrator"]),
                "device": str(p.get("review_orchestrator_device") or ""), "how": "it launched the review"}
    if p.get("orchestrator"):
        return {"address": str(p["orchestrator"]), "name": str(p.get("orchestrator_name") or p["orchestrator"]),
                "device": str(p.get("orchestrator_device") or ""),
                "how": "it claimed the step" if p.get("claimed_at") else "it launched the step"}
    o = (posts or {}).get("_orchestrator") if isinstance(posts, dict) else None
    o = o if isinstance(o, dict) else {}
    sid = str(o.get("session_id") or "")
    return {"address": sid, "name": sid, "device": str(o.get("device") or ""),
            "how": "no owner on the post (launched before CL49): the board's orchestrator" if sid else "nobody named"}


def report_lines(pid, p, posts=None, review=False):
    """`resume`'s "Where you report": the step's owner now, how to reach that one session, and what to do when it fails."""
    o = step_owner(p, posts, review=review)
    who = "your review" if review else "your step"
    if not o["address"]:
        return ["", "## Where you report", "- No orchestrator owns %s and the board names none: `python3 -I .kit/kit.py unreached %s "
                "--why \"no owner named\"`, then say so in your post; do not wait." % (who, pid), LAST_MESSAGE_LINE]
    dev, here = o["device"], my_device()
    rfmt = _rule("reply_format")
    if dev == "web" or o["address"].startswith(("session_", "cse_")):
        how = "`send_message` (claude-code-remote) with `session_id` `%s`" % o["address"]
    elif rfmt == "agy":
        how = "`send_message` to `%s`" % o["address"]
    elif rfmt == "none":
        how = "the board post (no inter-session message tool on this platform)"
    elif dev and dev != here:
        how = ("`SendMessage` to `%s`, only the row of device `%s` (a Remote Control row of that machine); a row of another "
               "machine with the same name is NOT it" % (o["name"], dev))
    else:
        how = ("`SendMessage` to `%s`, the session on this computer (`%s`); a row of another machine, or a cloud row, with the "
               "same name is NOT it" % (o["name"], here))
    return ["", "## Where you report",
            "- The orchestrator that owns %s: `%s`%s, device `%s` (%s)." % (
                who, o["address"], (" (session name `%s`)" % o["name"]) if o["name"] != o["address"] else "", dev or "not recorded", o["how"]),
            "- Reach it with %s: `Done. Check post %s.`" % (how, pid),
            "- The send fails, or no live session matches (ListAgents shows none on that device, or only stale ones): "
            "`python3 -I .kit/kit.py unreached %s --why \"<what failed>\"`; your post then says so and every orchestrator's resume "
            "lists it to claim. Do not wait, do not send to a look-alike." % pid, LAST_MESSAGE_LINE]


# CL60: said once, in the report section of a part's and a review's resume, before the closing message (a Stop hook only
# refuses it after it is on screen); the PostToolUse hook repeats it after each last step.
LAST_MESSAGE_LINE = ("- After that send (or `unreached`), your next and last chat message is a single \"\u00b7\": nothing "
                     "before it, no summary; the post holds the result.")


def _owner_write(pid, fields, url=None, token=None, drop=()):
    """Merge `fields` into a post of the local board (`drop`: written as null, which reads as absent); with an attached
    Artifact board on the web, the batch too."""
    url = url or local_board_url()
    token = _resolve_board_token(token)
    try:
        data = _local_update(url, token, "posts", pid, dict(fields, **{k: None for k in drop}))
    except Exception as e:
        return {"ok": False, "post": pid, "error": "cannot write the board at %s: %s" % (url, e)}
    if data is None:
        return {"ok": False, "post": pid, "error": "no post %s on this board" % pid}
    out = {"ok": True, "post": pid, "data": data}
    if artifact_here():
        try:
            r = artifact_push(url=url, token=token)
            out.update({"batches": r["batches"], "need_pin": r["need_pin"]})
        except Exception as e:
            out["push_error"] = str(e)
    return out


def claim(pid, url=None, token=None):
    """`kit.py claim <post>`: this orchestrator takes a step explicitly (CL49): writes its own address on the post (the
    part's and the review's owner) and says what to tell the part. Taking over the board alone claims nothing."""
    me = orchestrator_here()
    if not me:
        return {"ok": False, "post": pid, "error": "no session id here (kit.py whoami prints none): a claim needs your address"}
    fields = dict(me, claimed_at=_now_z(), review_orchestrator=me["orchestrator"],
                  review_orchestrator_name=me["orchestrator_name"], review_orchestrator_device=me["orchestrator_device"])
    r = _owner_write(pid, fields, url=url, token=token, drop=("orchestrator_unreached",))
    if r.get("ok"):
        sid = str(r["data"].get("session_id") or "")
        r["tell"] = ("message the part%s: \"%s on %s claimed post %s: report to it from now on (`kit.py resume --role part` "
                     "names it).\"" % ((" (session `%s`)" % sid) if sid and not sid.startswith("pending") else "",
                                        me["orchestrator"], me["orchestrator_device"], pid))
    return r


def unreached(pid, why="", url=None, token=None):
    """`kit.py unreached <post>`: the part could not reach its owner (the send failed, or no live session matches): its
    post says so (`orchestrator_unreached`), and every orchestrator's resume lists it to claim (CL49)."""
    try:
        d = _board_request(url or local_board_url(), "doc/posts/" + urllib.parse.quote(pid, safe=""), token=_resolve_board_token(token))
        p = d.get("data") if isinstance(d.get("data"), dict) else {}
        o = _board_request(url or local_board_url(), "doc/posts/_orchestrator", token=_resolve_board_token(token)).get("data")
    except Exception:
        p, o = {}, None
    o = step_owner(p, {"_orchestrator": o} if isinstance(o, dict) else None)
    note = {"at": _now_z(), "to": o["address"], "device": o["device"], "why": str(why or "the send failed")[:300]}
    return _owner_write(pid, {"orchestrator_unreached": note}, url=url, token=token)


def owner_text(cmd, r):
    if not r.get("ok"):
        return "Not %s: %s\n" % ("claimed" if cmd == "claim" else "written", r.get("error"))
    d = r["data"]
    if cmd == "claim":
        out = ["claimed: %s is yours (%s on %s)" % (r["post"], d.get("orchestrator"), d.get("orchestrator_device")), r.get("tell", "")]
    else:
        u = d.get("orchestrator_unreached") or {}
        out = ["written: %s could not reach %s (%s); every orchestrator's resume lists it to claim" % (
            r["post"], u.get("to") or "its orchestrator", u.get("why"))]
    for b in r.get("batches") or []:
        out.append("`ArtifactData` `batch` %s, then `python3 -I .kit/kit.py artifact sent %s`" % (b, b))
    if r.get("push_error"):
        out.append("artifact push failed (%s): run `kit.py artifact push`" % r["push_error"])
    return "\n".join(x for x in out if x) + "\n"


def unreached_lines(posts):
    """Every orchestrator's resume: the open steps whose part could not reach its owner, to claim (CL49)."""
    out = []
    for pid, p in sorted((posts or {}).items()):
        if pid.startswith("_") or not isinstance(p, dict) or not isinstance(p.get("orchestrator_unreached"), dict):
            continue
        if str(p.get("status") or "").lower() in ("dropped",) or p.get("removed"):
            continue
        u = p["orchestrator_unreached"]
        out.append("- `%s` (%s): could not reach `%s`%s at %s (%s): `python3 -I .kit/kit.py claim %s`, then message its part" % (
            pid, p.get("part") or pid, u.get("to") or "?", (" on " + u["device"]) if u.get("device") else "", u.get("at") or "?",
            u.get("why") or "?", pid))
    return ["", "## Parts that could not reach their orchestrator (claim one to answer it)"] + out if out else []


HANDOVER_FROM = os.path.join(".kit", "run", "handover-from.json")


def inherit_lines(posts, url=None, token=None):
    """A successor orchestrator on the same device, after a hand-over (handover.py wrote who it replaces), inherits every
    part its predecessor owned there (CL49): their posts get its address. Another device's parts are never taken."""
    path = os.path.join(ROOT, HANDOVER_FROM)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            h = json.load(fh)
    except (OSError, ValueError):
        return []
    me = orchestrator_here()
    old, dev = str(h.get("from") or ""), str(h.get("device") or "")
    if not me or not old or old == me["orchestrator"]:
        return []
    if dev != me["orchestrator_device"]:
        return ["", "## Hand-over", "- %s names a predecessor on device `%s`, not this one (`%s`): nothing inherited" % (
            HANDOVER_FROM, dev, me["orchestrator_device"])]
    took, failed = [], []
    for pid, p in sorted((posts or {}).items()):
        if pid.startswith("_") or not isinstance(p, dict) or p.get("_board"):
            continue
        f = {}
        if str(p.get("orchestrator") or "") == old and str(p.get("orchestrator_device") or "") == dev:
            f.update(me)
        if str(p.get("review_orchestrator") or "") == old and str(p.get("review_orchestrator_device") or "") == dev:
            f.update(review_orchestrator=me["orchestrator"], review_orchestrator_name=me["orchestrator_name"],
                     review_orchestrator_device=me["orchestrator_device"])
        if not f:
            continue
        r = _owner_write(pid, dict(f, inherited_from=old), url=url, token=token)
        (took if r.get("ok") else failed).append(pid)
    if not failed:
        try:
            os.remove(path)
        except OSError:
            pass
    return ["", "## Hand-over: the parts your predecessor owned here are yours",
            "- from `%s` on `%s`: %s%s" % (old, dev, ", ".join("`%s`" % x for x in took) or "none open",
                                          ("; NOT written (run resume again): " + ", ".join(failed)) if failed else "")]


def _is_kit_source_repo(root):
    return os.path.isfile(os.path.join(root, "install.py")) and os.path.isdir(os.path.join(root, "kit", "hooks"))


def _read_json_file(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _read_bytes_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def _doctor_unified(root, issues, lines):
    """--unified-hooks: the settings hold exactly one dispatcher line per event of the table, and no per-hook line."""
    spec = importlib.util.spec_from_file_location("kit_hook_table", os.path.join(root, ".kit", "hook.py"))
    try:
        table = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(table)
    except (OSError, SyntaxError) as e:
        lines.append("hooks: .kit/hook.py cannot be read (%s)" % e)
        issues.append("unified hooks without a working .kit/hook.py: run `install.py . --update`")
        return
    bad = []
    for rel, runtimes in ((os.path.join(".claude", "settings.json"), ("web", "cli")), (os.path.join(".agents", "hooks.json"), ("agy",))):
        s = _read_json_file(os.path.join(root, rel))
        groups = (s.get("kit-board") if runtimes == ("agy",) else s.get("hooks")) or {}
        if not groups and not os.path.isfile(os.path.join(root, rel)):
            continue
        cmds = [(ev, str(h.get("command") or "")) for ev, gs in groups.items() for g in gs for h in (g.get("hooks") or [g])]
        rt = next((r for r in runtimes if any(".kit/hook.py" in c and (" %s " % r) in c for _, c in cmds)), None)
        if rt is None:
            bad.append("%s runs no .kit/hook.py" % rel)
            continue
        for ev in table.events(rt):
            n = sum(e == ev and c.rstrip().endswith(".kit/hook.py\" %s %s" % (rt, ev) if rt != "agy" else ".kit/hook.py agy %s" % ev) for e, c in cmds)
            # without the board an event whose hooks are all the board's has no line: nothing of it is installed
            if n != 1 and (n or any(os.path.isfile(os.path.join(root, ".kit", "hooks", r[2])) for r in table.rows(rt, ev))):
                bad.append("%s: %d lines for %s" % (rel, n, ev))
        for ev, gs in groups.items():                        # a tool missing from the line is a hook that never runs
            for g in gs:
                if any(".kit/hook.py" in str(h.get("command") or "") for h in (g.get("hooks") or [g])):
                    miss = set((table.tools(rt, ev) or "").split("|")) - set(str(g.get("matcher") or "").split("|"))
                    if g.get("matcher") and miss:
                        bad.append("%s %s misses %s" % (rel, ev, ", ".join(sorted(miss))))
        bad += ["%s %s still runs %s" % (rel, e, c) for e, c in cmds if "hooks/kit/kit_" in c]
    lines.append("hooks: one line per event (.kit/hook.py)%s" % ("" if not bad else ": " + "; ".join(bad[:5])))
    if bad:
        issues.append("unified hooks miswired: %s (run `install.py . --update`)" % "; ".join(bad[:5]))


def _doctor_hook_parity(root, issues, lines, fix=False):
    """Check cross-runtime hook parity (.claude/hooks/kit vs .agents/hooks/kit) and guard registration."""
    if (_read_json_file(os.path.join(root, ".kit", "config.json")).get("kit") or {}).get("unified_hooks"):
        return _doctor_unified(root, issues, lines)
    c_dir = os.path.join(root, ".claude", "hooks", "kit")
    a_dir = os.path.join(root, ".agents", "hooks", "kit")
    has_c, has_a = os.path.isdir(c_dir), os.path.isdir(a_dir)
    if has_c and has_a:
        c_files = {f for f in os.listdir(c_dir) if f.endswith(".py") and f != "test_hooks.py"}
        a_files = {f for f in os.listdir(a_dir) if f.endswith(".py") and f != "test_hooks.py"}
        drifted, fixed = [], []
        manifest = _manifest(root)
        for f in sorted(c_files | a_files):
            cp, ap = os.path.join(c_dir, f), os.path.join(a_dir, f)
            if not os.path.isfile(cp) or not os.path.isfile(ap):
                # a hook one runtime does not have is the project's choice (CL30), unless the kit installed it there
                here = cp if not os.path.isfile(cp) else ap
                if os.path.relpath(here, root).replace(os.sep, "/") not in manifest:
                    continue
            elif _read_bytes_file(cp) == _read_bytes_file(ap):
                continue
            # The kit's own copy is the one whose hash the manifest records, never the newest (a checkout sets any date):
            # with exactly one such copy the other is restored from it, else nothing is touched.
            good = [p for p in (cp, ap) if os.path.isfile(p) and hashlib.sha256(_read_bytes_file(p)).hexdigest()
                    == manifest.get(os.path.relpath(p, root).replace(os.sep, "/"))]
            if fix and len(good) == 1:
                shutil.copy2(good[0], ap if good[0] == cp else cp)
                fixed.append(f)
            else:
                drifted.append(f)
        if fixed:
            lines.append("hooks parity: FIXED %d drifted file(s) from the kit's recorded copy (%s)" % (len(fixed), ", ".join(fixed[:5])))
        if drifted:
            if fix:
                lines.append("hooks parity: NOT FIXED %d file(s) (%s): no single copy is the kit's recorded one" % (len(drifted), ", ".join(drifted[:5])))
            else:
                lines.append("hooks parity: DRIFT in %d file(s) (%s)" % (len(drifted), ", ".join(drifted[:5])))
            issues.append("hook drift between .claude/hooks/kit and .agents/hooks/kit (%s); run `install.py . --update`%s" % (
                ", ".join(drifted[:5]), "" if fix else " or `kit.py doctor --fix`"))
        elif not fixed:
            lines.append("hooks parity: ok (.claude and .agents match)")
    # Check Claude Code settings.json guard wiring when .claude/hooks/kit exists
    s_path = os.path.join(root, ".claude", "settings.json")
    if has_c and os.path.isfile(s_path):
        s = _read_json_file(s_path)
        hooks = s.get("hooks") or {}
        all_cmds = []
        for groups in hooks.values():
            for g in (groups or []):
                for h in (g.get("hooks") or []):
                    all_cmds.append((str(g.get("matcher") or ""), str(h.get("command") or "")))
        cfg = _read_json_file(os.path.join(root, ".kit", "config.json"))
        is_art = any("claude.ai/" in str(u or "") for u in (
            cfg.get("board_url"), (cfg.get("kit") or {}).get("artifact_url"),
            ((cfg.get("kit") or {}).get("artifact") or {}).get("url")))
        # the installer wires the orchestrator guard for the web hooks only (never in --runtime local)
        web = is_art and (cfg.get("kit") or {}).get("runtime") != "local"
        guards = ("kit_brief_guard.py", "kit_board_guard.py") + (("kit_orchestrator_guard.py",) if web else ())
        missing_g = [g for g in guards if os.path.isfile(os.path.join(c_dir, g))
                     and not any(g in cmd for _, cmd in all_cmds)]
        if missing_g:
            lines.append("hooks wiring: MISSING in .claude/settings.json (%s)" % ", ".join(missing_g))
            issues.append("unwired kit guard(s) in .claude/settings.json: %s (run `install.py . --update`)" % ", ".join(missing_g))
        if is_art:
            brief_m = [m for m, cmd in all_cmds if "kit_brief_guard.py" in cmd]
            if brief_m and not any((not m) or ("ArtifactData" in m) for m in brief_m):
                lines.append("hooks wiring: kit_brief_guard.py does not match ArtifactData")
                issues.append("kit_brief_guard.py in .claude/settings.json misses ArtifactData matcher")


def _doctor_conflicts(root, issues, lines):
    """Detect legacy/shadow scripts and hooks when installed onto an existing project."""
    if not _is_kit_source_repo(root):
        if os.path.isfile(os.path.join(root, ".kit", "kit.py")) and os.path.isfile(os.path.join(root, "scripts", "kit.py")):
            lines.append("conflicts: legacy scripts/kit.py alongside .kit/kit.py")
            issues.append("legacy scripts/kit.py shadows .kit/kit.py (remove or archive scripts/kit.py)")
    s_path = os.path.join(root, ".claude", "settings.json")
    if os.path.isfile(s_path):
        s = _read_json_file(s_path)
        overlaps = []
        for ev, groups in (s.get("hooks") or {}).items():
            cmds = [str(h.get("command") or "") for g in (groups or []) for h in (g.get("hooks") or [])]
            has_kit = any("/hooks/kit/" in c or "/.kit/hook.py" in c for c in cmds)
            legacy = [c for c in cmds if has_kit and any(x in c for x in ("stop.sh", "session-start.sh", "end-session.sh"))]
            if legacy:
                overlaps.append("%s (%s)" % (ev, ", ".join(os.path.basename(x.split()[-1].strip('"\'')) for x in legacy)))
        if overlaps:
            lines.append("conflicts: overlapping legacy hooks: %s" % "; ".join(overlaps))
            issues.append("overlapping legacy hooks alongside kit hooks: %s" % "; ".join(overlaps))
    # Archived awaits file still referenced by custom hooks/skills (the kit's own files, in its manifest, are not)
    manifest = _manifest(root)
    for mrel in (".story", os.path.join(".board", "memory")):
        arch = glob.glob(os.path.join(root, mrel, "archive", "awaits*.md"))
        live = glob.glob(os.path.join(root, mrel, "awaits*.md"))
        if arch and not live:
            stale_refs = []
            for pat in (".claude/hooks/*.sh", ".claude/skills/**/*.md"):
                for fp in glob.glob(os.path.join(root, pat), recursive=True):
                    if os.path.relpath(fp, root).replace(os.sep, "/") in manifest:
                        continue
                    try:
                        with open(fp, encoding="utf-8", errors="ignore") as fh:
                            if "awaits" in fh.read().lower():
                                stale_refs.append(os.path.relpath(fp, root))
                    except OSError:
                        pass
            if stale_refs:
                lines.append("conflicts: archived awaits still referenced in %s" % ", ".join(stale_refs[:4]))
                issues.append("archived awaits file still referenced by %s" % ", ".join(stale_refs[:4]))


def _doctor_portfolio_and_sync(root, issues, lines, fix=False):
    """Check portfolio portability (hardcoded local paths) and unsent Artifact batches."""
    cfg_p = os.path.join(root, ".kit", "config.json")
    if os.path.isfile(cfg_p):
        cfg = _read_json_file(cfg_p)
        port = cfg.get("portfolio") or []
        changed = False
        bad_paths = []
        for item in port:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            p = str(item["path"])
            if os.path.isdir(p):
                continue
            name = str(item.get("name") or os.path.basename(p.rstrip("/")))
            sib = os.path.join(os.path.dirname(os.path.abspath(root)), name)
            att = os.path.join(root, ".kit", "attached", name)
            if os.path.isdir(sib):
                if fix:
                    item["path"] = sib
                    changed = True
                else:
                    bad_paths.append("%s (%s -> sibling %s)" % (name, p, sib))
            elif not os.path.isdir(att):
                bad_paths.append("%s (%s)" % (name, p))
        if changed and fix:
            with open(cfg_p, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
            lines.append("portfolio: FIXED stale local path(s) to sibling clone(s)")
        elif bad_paths:
            lines.append("portfolio: stale local path(s): %s" % ", ".join(bad_paths))
            issues.append("hardcoded/stale portfolio path(s): %s (run `kit.py doctor --fix` or `kit.py attach`)" % ", ".join(bad_paths))
    art_out = os.path.join(os.environ.get("KIT_ARTIFACT_DIR") or os.path.join(root, ".kit", "run", "artifact"), "out")
    batches = sorted(glob.glob(os.path.join(art_out, "batch-*.json")))
    if batches:
        lines.append("artifact sync: %d unsent batch file(s) in %s" % (len(batches), os.path.relpath(art_out, root)))
        issues.append("%d unsent Artifact batch(es): send them and run `kit.py artifact sent <batch>`" % len(batches))


def doctor(m, db_path=None, fix=False, root=None):
    """Verify SQLite integrity, mirror worktree health, blobs, hook parity, legacy conflicts, and sync health."""
    root = os.path.abspath(root or ROOT)
    issues = []
    lines = []
    db = db_path or os.path.join(root, ".kit", "board_data", "board.db")
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
    need, has = add_repo_allowed(root)
    if need:
        if not has and fix:
            s_path = os.path.join(root, ".claude", "settings.json")
            s = _read_json_file(s_path)
            allow = s.setdefault("permissions", {}).setdefault("allow", [])
            if ADD_REPO_RULE not in allow:
                allow.append(ADD_REPO_RULE)
            os.makedirs(os.path.dirname(s_path), exist_ok=True)
            with open(s_path, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            has = True
            lines.append("add_repo: FIXED (added %s to .claude/settings.json)" % ADD_REPO_RULE)
        else:
            lines.append("add_repo: %s" % ("allowed" if has else "NOT allowed in .claude/settings.json permissions.allow"))
        if not has:
            issues.append("%s missing from .claude/settings.json permissions.allow: web sessions cannot attach repos "
                          "(install.py --update or `kit.py doctor --fix` adds it; start a new session after)" % ADD_REPO_RULE)
    _doctor_hook_parity(root, issues, lines, fix=fix)
    _doctor_conflicts(root, issues, lines)
    _doctor_portfolio_and_sync(root, issues, lines, fix=fix)
    lines += git_copy_lines(m, db_path, root)
    _doctor_prompts(root, issues, lines, db_path)
    g = _post_gate()
    if g and root == os.path.abspath(ROOT):
        k = _kit_config().get("kit") or {}
        skipped = str(k.get("survey") or "").startswith("skipped")
        lines.append("autonomy: %s%s" % (g.autonomy(_kit_config()), " (the default: step 0 was skipped, nobody chose it; `install.py . --update "
                     "--autonomy full_auto|balanced|gated`)" if skipped and g.autonomy(_kit_config()) == "balanced" else ""))
    lines.append("doctor: %s" % ("OK" if not issues else ("%d issue(s): %s" % (len(issues), "; ".join(issues)))))
    return "\n".join(lines), (1 if issues else 0)


_CLOSED_CARD_STATUSES = {"complete", "completed", "done", "dropped", "shipped", "merged", "closed", "abandoned", "superseded"}


def _is_card_done(doc):
    if not isinstance(doc, dict):
        return False
    if str(doc.get("column") or "").strip().lower() == "done":
        return True
    return str(doc.get("status") or "").strip().lower() in _CLOSED_CARD_STATUSES


def _doctor_prompts(root, issues, lines, db_path=None):
    """CL54: a prompt file that says a task its post does not: an issue for an open step (its next launch would tell the
    part something else), a line for a finished one (history: what it was told then)."""
    try:
        posts = _all_posts(db_path)
    except Exception:
        return
    open_, old = [], 0
    for pid, p in sorted(posts.items()):
        if pid.startswith("_") or not isinstance(p, dict) or p.get("removed") or not str(p.get("task") or "").strip():
            continue
        why = prompt_drift(pid, p, root=root)
        if not why:
            continue
        if step_state(p) in ("idle", "running"):
            open_.append(why)
        else:
            old += 1
    for why in open_:
        issues.append("prompt differs from its post: " + why)
    lines.append("prompts: %s%s" % ("%d differ from their open step's post" % len(open_) if open_ else "ok (each open step's prompt says its post's task)",
                                   "; %d finished step(s) were told a task their post no longer says (history, kept)" % old if old else ""))


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
        if ref is None or not _is_card_done(ref):
            return True
    return False


def post_person(d):
    """The person a post is for: `for` (the menu on the page, the queue, MCP); `assignee` and `assigned_to` are its old names."""
    return str(d.get("for") or d.get("assignee") or d.get("assigned_to") or "").strip()


def queue_cards(m, runner=None, for_owner=None, db_path=None, unblocked_only=False):
    """List open cards and planned/in-progress train steps attributed to a runner or person/role from SQLite, live server, or mirror."""
    rf = (runner or "").strip().lower()
    ff = (for_owner or "").strip().lower()
    posts = {}
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    has_local_db = os.path.isfile(db)
    has_mirror_dir = bool(m) and os.path.isdir(os.path.join(m, "board", "posts"))
    # In a worktree with no local board.db and no checked-out mirror dir, prefer the live board server when reachable,
    # then fall back to _load_board_docs (which reads local SQLite -> mirror worktree -> git board-mirror).
    if db_path is None and not has_local_db and not has_mirror_dir and os.path.isfile(os.path.join(ROOT, ".kit", "config.json")):
        try:
            r = _board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")), "db/posts?limit=5000", token=_resolve_board_token(None))
            posts = {d["id"]: d.get("data") or {} for d in r.get("docs") or [] if not str(d.get("id") or "").startswith("_")}
        except Exception:
            posts = {}
    if not posts:
        loaded_posts, _ = _load_board_docs(m, db_path=db_path)
        posts = {did: doc for did, doc in loaded_posts.items() if not str(did).startswith("_")}
        trains_doc = loaded_posts.get("_trains")
    else:
        trains_doc = None
    # Inherit runner from _trains rows if a step post omits it
    train_runners = {}
    if isinstance(trains_doc, dict):
        for trow in trains_doc.get("rows") or []:
            if isinstance(trow, dict) and trow.get("post") and trow.get("runner"):
                train_runners[str(trow["post"]).strip()] = str(trow["runner"]).strip()
    rows = []
    for pid, d in posts.items():
        if d.get("removed") or _is_card_done(d):
            continue
        if unblocked_only and _is_card_blocked(d, posts):
            continue
        pr = str(d.get("runner") or train_runners.get(pid) or "").strip()
        pf = post_person(d)
        if rf and pr.lower() != rf:
            continue
        if ff and pf.lower() != ff:
            continue
        if not rf and not ff and not pr and not pf:
            continue
        prio = int(d.get("priority") or 3)
        col = str(d.get("column") or "todo")
        st = str(d.get("status") or "").strip()
        train = str(d.get("train") or "").strip()
        part = str(d.get("part") or "").strip()
        rows.append((prio, pid, pr, pf, col, st, train, part, str(d.get("title") or "(untitled)"), str(d.get("body") or ""),
                     step_where(d) if pr else ""))
    rows.sort(key=lambda x: (x[0], x[1]))
    if not rows:
        who = ("runner=%s" % runner if runner else "") + (" for=%s" % for_owner if for_owner else "")
        return "queue%s: 0 open cards" % ((" (" + who.strip() + ")") if who.strip() else "")
    out = []
    for prio, pid, pr, pf, col, st, train, part, title, body, where in rows:
        tags = []
        if pr:
            tags.append("@" + pr)
        if pf:
            tags.append("for:" + pf)
        if train:
            tags.append("train:" + train + ("/" + part if part else ""))
        elif part:
            tags.append(part)
        col_label = "%s/%s" % (col, st) if (train and st) else col
        first = body.strip().splitlines()[0][:100] if body.strip() else ""
        out.append("P%d [%s] %s (%s) — %s%s" % (prio, col_label, pid, " ".join(tags) or "unassigned", title, (" | " + first) if first else ""))
        if where:                                  # CL54: a card a platform started by hand works from says where first
            out.append("    where: " + where)
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
        self.artifact = bool(artifact_here())

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
        return ("`python3 -I .kit/kit.py artifact sync` (it prints the ArtifactData calls to make, exactly; again until "
                "\"in sync\")") if self.artifact else ""


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
    """Load posts and comments dicts from local SQLite DB, falling back to mirror worktree (or local git board-mirror)
    when board.db is missing or has 0 docs."""
    posts, comments = {}, {}
    db = db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")
    if os.path.isfile(db):
        try:
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
        except sqlite3.Error:
            posts, comments = {}, {}
        if posts or comments:
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
    if posts or comments:
        return posts, comments
    # CL44: if local SQLite has 0 docs and no mirror worktree is checked out on disk, read local git board-mirror
    # objects (offline, no network fetch) unless caller passed an explicit --db or suppressed mirror fallback.
    if db_path is None and os.path.basename(str(m or "")) != "board-mirror-missing":
        branch = str((CONFIG.get("mirror") or {}).get("branch") or BRANCH or "board-mirror")
        try:
            ref = next((r for r in (branch, "origin/" + branch)
                        if subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "--quiet", r],
                                          capture_output=True, timeout=5).returncode == 0), None)
            if ref:
                arc = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", ref, "board"],
                                     capture_output=True, timeout=15)
                if arc.returncode == 0 and arc.stdout:
                    import io
                    import tarfile
                    with tarfile.open(fileobj=io.BytesIO(arc.stdout), mode="r:*") as tf:
                        for mem in tf.getmembers():
                            parts = tar_name(mem.name).split("/")
                            if not mem.isfile() or len(parts) != 3 or parts[0] != "board" or parts[1] not in ("posts", "comments") or not parts[2].endswith(".json"):
                                continue
                            try:
                                data = json.loads(tf.extractfile(mem).read().decode("utf-8"))
                            except (ValueError, UnicodeDecodeError, AttributeError):
                                continue
                            if isinstance(data, dict) and isinstance(data.get("data"), dict) and set(data) <= {"id", "data", "version", "updated_at"}:
                                data = data["data"]
                            if isinstance(data, dict):
                                (posts if parts[1] == "posts" else comments)[parts[2][:-5]] = data
        except (OSError, subprocess.SubprocessError):
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
        r = _board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")), "doc/posts/" + urllib.parse.quote(pid, safe=""),
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
        r = _board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")), "doc/comments/" + urllib.parse.quote(cid, safe=""),
                           token=_resolve_board_token(None))
        if r.get("exists") is not False and isinstance(r.get("data"), dict):
            return r["data"]
    except Exception:
        pass
    _, comments = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    return comments.get(cid)


def field_text(moment, role="part", train=None):
    """The fields this moment opens, as text ("" when none)."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "open_fields"):
        return ""
    try:
        lines = led.field_lines(led.open_fields(_resolve_memory_dir(), role, moment=moment, kit_root=ROOT, train=train), ROOT)
    except (OSError, ValueError, KeyError):
        return ""
    return "\n".join(lines).strip()


def _post_gate():
    for d in (os.path.join(ROOT, ".kit"), os.path.join(ROOT, "kit", "board")):
        p = os.path.join(d, "post_gate.py")
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("kit_post_gate", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _post_comments(pid, db_path=None):
    """{id: comment} under a post: from the board server (a part's worktree has no database), else the local database."""
    try:
        r = _board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")),
                           "db/comments?" + urllib.parse.urlencode({"where": json.dumps([["post_id", "==", pid]]), "limit": "500"}),
                           token=_resolve_board_token(None))
        if isinstance(r.get("docs"), list):
            return {d["id"]: d.get("data") or {} for d in r["docs"]}
    except Exception:
        pass
    try:
        _, comments = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    except Exception:
        return {}
    return {i: c for i, c in comments.items() if c.get("post_id") == pid}


def unanswered_under(comments):
    """His words under a post with no later word of anyone else in their thread: [(id, text)]."""
    out = []
    for i, c in comments.items():
        if c.get("from") != "owner" or c.get("like") or c.get("answer") or c.get("removed") or not str(c.get("text") or "").strip() or \
                str(c["text"]).startswith("Answer:") or str(c["text"]).strip() == "\U0001F44D":
            continue
        ids = {i, str(c.get("thread_id") or ""), str(c.get("reply_to") or "")} - {""}
        if not any(o.get("from") != "owner" and not o.get("like") and str(o.get("created_at") or "") >= str(c.get("created_at") or "") and
                   ids & {str(o.get("reply_to") or ""), str(o.get("thread_id") or "")} - {""} for o in comments.values()):
            out.append((i, re.sub(r"\s+", " ", str(c["text"])).strip()))
    return out


def check_his_words(pid, p, db_path=None):
    """(errors, notes): each comment of his that started the step (`sources`) is in `asked`, so a bar answers it; each one
    under the post has an answer. A step that proves its own test and misses what he asked is what this refuses."""
    err, notes = [], []
    asked = {str(a.get("id")) for a in _as_list(p.get("asked")) if isinstance(a, dict)}
    for s in [str(x) for x in _as_list(p.get("sources"))]:
        c = _get_comment(s, db_path=db_path)
        if c is None:
            notes.append("source %s: not on this board, not checked" % s)
        elif c.get("from") == "owner" and s not in asked and not c.get("like"):
            err.append("asked: his comment %s started this step and nothing answers it: add {\"id\": \"%s\", \"quote\": \"<his words>\"} to "
                       "`asked` and name it in the bar that shows it done (\"... (asked %s)\"): %r" % (s, s, s, str(c.get("text") or "")[:80]))
    for i, t in unanswered_under(_post_comments(pid, db_path=db_path)):
        err.append("his comment %s has no answer: `python3 -I .kit/board_cli.py reply %s --comment-id %s --from %s --text ...`: %r" % (
            i, pid, i, p.get("part") or "agent", t[:80]))
    return err, notes


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
        pass                                             # said below: a step has a check
    if not str(p.get("check") or "").strip():
        err.append("check: missing (the one command that shows the task is done, or `manual: <what the review does and must see>`): "
                   "a step with no check proves nothing")
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


def check_trunk_merged(root=ROOT):
    """CL38: Done is judged by the kit as it is on the trunk now, not as it was when the part started (CL37 started
    before CL36's rule and skipped it): the work must contain origin/<trunk>. Only a repo with no origin at all is
    skipped (a local repo, a test); a fetch that fails or times out is refused (O55)."""
    def g(*a, timeout=60):
        return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=timeout)
    trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    try:
        if g("remote", "get-url", "origin").returncode:
            return []
        if g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == trunk:
            return []
        fetched = g("fetch", "-q", "origin", trunk, timeout=180).returncode == 0
    except (OSError, subprocess.SubprocessError):
        fetched = False
    if not fetched:
        return ["cannot fetch origin/%s to check that your work contains it: fix the network or the remote, then done "
                "again (Done is never judged on an unknown trunk)" % trunk]
    if g("merge-base", "--is-ancestor", "origin/" + trunk, "HEAD").returncode:
        return ["the trunk moved since you started: merge it first (`git fetch origin %s && git merge --no-edit "
                "origin/%s`), rerun the tests, then done again: Done is checked against the kit on %s now" % (trunk, trunk, trunk)]
    return []


# ---------------------------------------------------------------- the form: a step's result, filled in and sent by `done`
FORM_PARTS = ("lead", "verdict", "bars", "asked", "artifacts", "not_checked", "check", "breaks", "handoff", "body")
FORM_LISTS = ("bars", "artifacts", "not_checked")
_BLANK = re.compile(r"<fill[^>]*>")


def form_path(pid):
    return os.path.join(ROOT, ".kit", "run", "form", pid + ".md")


def form(pid, doing="", db_path=None):
    """`kit.py form <post>`: the step's result as a file with what the kit knows filled in; `kit.py done <post>` sends and
    checks it. `doing`: the step is in progress, in this session's name (turn 1). An existing form is never overwritten."""
    p = _get_post(pid, db_path=db_path)
    if not p:
        return 1, "no post %s on the board" % pid
    out = []
    if doing:
        prj = "%s-%s" % (_slug(_kit_config().get("project") or "project"), _slug(p.get("part") or pid, 24))
        ok = _board_write("update", "posts", pid, {"session_id": whoami(prj), "status": "in progress", "body": doing, "started_at": _now_z()})
        out.append("post: in progress, yours" if ok else "post: NOT updated (is the board server running?)")
    pieces_file(pid, p)
    path, rel = form_path(pid), os.path.relpath(form_path(pid), ROOT)
    if os.path.isfile(path):
        return 0, "\n".join(out + ["%s exists: fill it, then `python3 -I .kit/kit.py done %s`" % (rel, pid)])
    asked = []
    for s in [str(x) for x in _as_list(p.get("sources"))]:
        c = _get_comment(s, db_path=db_path)
        if c and c.get("from") == "owner" and not c.get("like"):
            asked.append("- %s: %s" % (s, re.sub(r"\s+", " ", str(c.get("text") or "")).strip()))
    ids = " ".join("(asked %s)" % a[2:].split(":")[0] for a in asked)
    test = _test_command()
    say = {
        "lead": "<fill: what he must know or do, 1 to 3 sentences in his words: what changed for him; done refuses a file name, hash, command or `code` here and in the verdict>",
        "verdict": "<fill: ONE plain sentence that answers the task, at most 240 characters>",
        "bars": "- <fill: one measured number a line, with its denominator or reference: 41 of 50 takes> %s" % ids,
        "asked": "\n".join(asked) or "(nothing of his named as this step's source)",
        "artifacts": "- auto",
        "not_checked": "- <fill: what you did NOT check, or: nothing left unchecked>",
        "check": "<fill: one command that passes only if the task is done%s>" % ("" if test.startswith("(") else "; the project's tests: " + test),
        "breaks": "<fill: a command that plants a fault so that check fails, or `revert`, or `manual: what the review does`>",
        "handoff": "<fill: what the next step needs from yours>",
        "body": "<fill: the rest, under ### headings (they fold). Picture ![what](path.png), diagram a mermaid block, audio its path in a "
                "code span. A choice for him is not here: kit.py ask>"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Replace each <fill...>, keep the ## lines (artifacts `auto`: the files your branch changes). Then: python3 -I .kit/kit.py done %s (it sends this and checks it).\n\n%s" % (
            pid, "".join("## %s\n%s\n\n" % (k, say[k]) for k in FORM_PARTS)))
    return 0, "\n".join(out + ["%s: fill it as you go, then `python3 -I .kit/kit.py done %s`" % (rel, pid)])


def read_form(text):
    """(fields, errors) of a form. The body is everything after its `## body` line."""
    cut = re.search(r"(?m)^## body[ \t]*$", text)
    head, body = (text[:cut.start()], text[cut.end():].strip()) if cut else (text, "")
    got = {k: v.strip() for k, v in re.findall(r"(?ms)^## (\w+)[ \t]*\n(.*?)(?=^## \w+[ \t]*$|\Z)", head)}
    got["body"] = body
    err = ["form: `## %s` is missing" % k for k in FORM_PARTS if not got.get(k)]
    err += ["form: `## %s` still has %s" % (k, _BLANK.search(got[k]).group(0)[:40] + "...") for k in FORM_PARTS if _BLANK.search(got.get(k) or "")]
    if err:
        return None, err
    items = lambda t: [re.sub(r"^[-*]\s+", "", x).strip() for x in re.split(r"(?m)^(?=[-*]\s)", t) if x.strip()]  # noqa: E731
    f = {k: items(got[k]) if k in FORM_LISTS else got[k] for k in ("verdict", "bars", "artifacts", "not_checked", "check", "breaks", "handoff")}
    for k in ("check", "breaks"):
        f[k] = re.sub(r"\A```\w*\n(.*?)\n?```\Z", r"\1", f[k], flags=re.S).strip()
        if re.match(r"\A`[^`\n]+`\Z", f[k]):
            f[k] = f[k][1:-1]
    if "auto" in f["artifacts"]:                              # the files this branch changes, known when it is sent
        skip = ("docs/prompts/", ".kit/", ".claude/", ".agents/")
        f["artifacts"] = [x for x in f["artifacts"] if x != "auto"] + [
            x for x in _changed_files() if not x.startswith(skip) and x not in f["artifacts"] and os.path.isfile(os.path.join(ROOT, x))]
    if got.get("fixes") and not _BLANK.search(got["fixes"]):     # M10, optional: `<kind>: <what was broken>`
        f["fixes"] = re.sub(r"\s+", " ", got["fixes"]).strip()
    f["verdict"] = re.sub(r"\s+", " ", f["verdict"])
    f["body"] = got["lead"] + "\n\n" + got["body"]
    asked = [re.match(r"([\w.-]+):\s*(.+)", x, re.S) for x in items(got["asked"]) if not x.startswith("(nothing")]
    if any(m is None for m in asked):
        return None, ["form: `## asked`: each line is `- <his comment's id>: <his words>`"]
    if asked:
        f["asked"] = [{"id": m.group(1), "quote": re.sub(r"\s+", " ", m.group(2)).strip()} for m in asked]
    return f, []


def send_form(pid):
    """The filled form onto the post, once for each state of it: errors, or [] (also when there is no form)."""
    path = form_path(pid)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        with open(path + ".sent", encoding="utf-8") as fh:
            if fh.read().strip() == key:
                return []
    except OSError:
        pass
    f, err = read_form(text)
    if err:
        return err
    if not _board_write("update", "posts", pid, f):
        return ["form: the board did not take it (is the board server running?)"]
    with open(path + ".sent", "w", encoding="utf-8") as fh:
        fh.write(key)
    return []


_TECH = re.compile(r"`[^`\n]+`|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|[\w./-]*\w\.(?:py|js|cjs|mjs|ts|tsx|jsx|css|html|md|sh|json|yml|yaml|toml|kt)\b")
_PRODUCT = re.compile(r"\b(?:Node|Next|Nuxt|Vue|Three|Chart|Express|Ember|Backbone|Socket|D3|P5|Pixi|Paper)\.js\b")   # CL41: a name, not a file
_MEDIA = re.compile(r"`[^`\n]+\.(?:wav|mp3|ogg|m4a|flac|mp4|webm|png|jpe?g|gif|svg|webp)`", re.I)


def plain_notes(p):
    """What in a post's first words is for agents, not for him (design principles 9): errors. A name he used himself (his quoted words, the
    title), a link's target, and a picture or a sound to open are not."""
    out = []
    lead = re.split(r"(?m)^#{1,4} ", str(p.get("body") or ""), maxsplit=1)[0]
    lead = re.sub(r"(?ms)^\s*```mermaid.*?^\s*```\s*$", "", lead)
    his = " ".join([str(p.get("title") or "")] + [str(a.get("quote") or "") for a in _as_list(p.get("asked")) if isinstance(a, dict)]).lower()
    for k, t in (("verdict", str(p.get("verdict") or "")), ("lead", lead)):
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)|\]\([^)]*\)|https?://\S+", " ", _PRODUCT.sub(" ", _MEDIA.sub(" ", t)))
        hit = [h for h in dict.fromkeys(_TECH.findall(t)) if h.strip("`").strip().lower() not in his]
        if hit:
            out.append("%s: he reads this first, and it names %s: say what that does for him in plain words, the name goes under a heading of the body"
                       % (k, ", ".join(hit)[:120]))
    return out


def _mirror_path():
    mp = (CONFIG.get("mirror") or {}).get("path")
    return os.environ.get("KIT_MIRROR") or (os.path.join(ROOT, mp) if mp else os.path.join(os.path.dirname(ROOT), "board-mirror"))


def git_copy_has(pid, p, strip, m=None):
    """CL51: done on a computer: the post as the local board has it is in the board's git copy (board-mirror); if not,
    it is kept there now (`kit.py keep`, what end_session does) and read again. [] when it is there, or when this clone
    has no git copy (said by `resume`); else why not."""
    m = m or _mirror_path()
    f = os.path.join(m, "board", "posts", pid + ".json")
    if not os.path.isdir(os.path.join(m, "board")):
        return []
    if strip(_read_json(f)) == strip(p):
        return []
    txt, ok = keep(m)
    if strip(_read_json(f)) == strip(p):
        return []
    return ["the post is not in the board's git copy yet (%s): `python3 -I .kit/kit.py keep` said: %s; then done again"
            % (BRANCH, (txt or "nothing")[-200:])]


def _step_changed(root):
    """M3: the files this step changed: its commits since it left the trunk, and what is not committed yet. None when
    there is no trunk to compare with (a repo with no trunk ref, the trunk itself)."""
    g = lambda *a: subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=60)  # noqa: E731
    trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    try:
        if g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == trunk:
            return None
        base = ""
        for ref in ("origin/" + trunk, trunk):
            r = g("merge-base", "HEAD", ref)
            if r.returncode == 0 and r.stdout.strip():
                base = r.stdout.strip()
                break
        if not base:
            return None
        names = g("diff", "--name-only", base, "HEAD").stdout.splitlines()
        names += g("diff", "--name-only", "HEAD").stdout.splitlines()
        names += g("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({n.strip() for n in names if n.strip()})


def check_map(root):
    """M3: Done keeps the map. Rebuild it, rerun every link's check (refused on one that fails, the link named),
    refuse a file this step changed that is not on the map and an added thing with no check. (errors, notes)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import kit_map  # noqa: E402
    except ImportError:
        return [], ["map: kit_map.py is not next to kit.py: the map was not checked (`python3 -I install.py . --update`)"]
    changed = _step_changed(root)
    errors, notes, secs = kit_map.stretch(root, changed or [])
    errors = ["map: " + e for e in errors]
    notes = ["map: " + n for n in notes]
    notes.append("map: checked in %.1f s%s" % (secs, "" if changed is not None else " (no trunk to compare with: changed files not checked)"))
    return errors, notes


def done(pid, db_path=None, root=None):
    p = _get_post(pid, db_path=db_path)
    if not p:
        return 1, "NOT READY: no post %s on the board" % pid
    bad = send_form(pid)
    if bad:
        return 1, "\n".join(["NOT READY: %s" % pid] + ["  fix: " + e for e in bad])
    p = _get_post(pid, db_path=db_path) or p
    k = _kit_config().get("kit") or {}
    root = root or ROOT
    err, warn = check_done(p, root=root, body_max=int(k.get("post_body_max", 1500)))
    e_a, n_a = check_asked(p, db_path=db_path)
    err += e_a
    warn += n_a
    e_h, n_h = check_his_words(pid, p, db_path=db_path)
    err += e_h + plain_notes(p)
    warn += n_h
    err += pieces_unchecked(p)                       # CL42: a piece done that not_checked says was not done
    err += record_step_fix(p, pid)                   # M10: a step that fixes a bug is one fix of its kind
    try:
        sc = scan_fix_commits(root=root)
    except Exception as ex:                          # never hidden: said, and done goes on
        sc = {"error": str(ex)}
    if sc.get("error"):
        warn.append("fix commits not read: %s" % sc["error"])
    for u in sc.get("unnamed") or []:
        warn.append("commit %s says it fixes a bug without its kind: add `Fix-kind: <kind>` to its message, or `fixes:` to the form (%s)" % (u["commit"], u["subject"][:60]))
    err += decision_breaks(_changed_files(root))     # M10: a step never breaks the owner's decision silently
    left = pieces_open(p)
    if left:
        err.append("pieces still open: %s. Finished: `python3 -I .kit/kit.py sub %s %s --status done`; not needed: `--status drop --why \"...\"`" % (
            "; ".join("%d. %s" % (x["n"], x["title"][:60]) for x in left)[:300], pid, " ".join(str(x["n"]) for x in left)))
    pending_real = check_real_project(p, root=root)
    if _rule("real_project_run") == "part":
        err += pending_real
    else:
        warn += pending_real     # CL32/CL38: a real project too (pending)
    err += check_trunk_merged(root)                  # CL38: a part that started on an older kit merges the trunk first
    e_m, n_m = check_map(root)                       # M3: the part's stretch of the map, and what it built on it
    err += e_m
    warn += n_m
    strip = lambda d: {k: v for k, v in (d or {}).items() if k not in ("proof", "updated_at")}  # noqa: E731
    if not artifact_here():
        # CL51 / R5: on a platform without artifact_steps, the board is the local one and git carries it
        err += git_copy_has(pid, p, strip)
    elif strip(_read_json(_base_path("posts", pid))) != strip(p):
        # CL28: a part updated its post on the local copy only and said "Done": the live board (the Artifact) still
        # showed it planned, with none of its results (the owner: "How come you said you posted when you didn't?")
        err.append("the post is not on the board yet (%s): its local copy differs from what the Artifact last had. Run "
                   "`python3 -I .kit/kit.py artifact sync` (CL72: make the calls it prints, exactly; again until \"in sync\"); "
                   "then done again" % (artifact_url() or "the Artifact"))
    if not err:
        e2, notes = prove_check(p, root=root, timeout=int(k.get("check_timeout_s", 900)))
        err += e2
        warn += notes
    g = _post_gate()
    if not err and g and not (g.proved(p) and (p.get("proof") or {}).get("needs_real") == bool(pending_real)):
        head = _git_run("rev-parse", "--short", "HEAD").stdout.strip()
        if not _board_write("update", "posts", pid, {"proof": {"key": g.proof_key(p), "at": _now_z(), "commit": head, "sha": _git_run("rev-parse", "HEAD").stdout.strip(),
                                                           "branch": _git_run("symbolic-ref", "--short", "-q", "HEAD").stdout.strip(),
                                                           "needs_real": bool(pending_real)}}, env={"KIT_GATE": "done"}):
            warn.append("proof: the board did not take it (is the board server running?): the step cannot be completed without it")
        elif artifact_here():
            warn.append("proof: written here; send it: `python3 -I .kit/kit.py artifact push`")
    lines = (["NOT READY: %s" % pid] if err else ["OK: %s follows the post format" % pid])
    lines += ["  fix: " + e for e in err] + ["  note: " + w for w in warn]
    if not err:
        rfmt = _rule("reply_format")
        if rfmt == "none":
            lines.append("Next: commit and push, run .kit/end_session.py (post %s is complete on the board)." % pid)
        else:
            tool = {"claude_cli": "SendMessage", "claude_remote": "send_message", "agy": "send_message"}[rfmt]
            lines.append("Next: commit and push, run .kit/end_session.py, then %s the orchestrator: Done. Check post %s." % (
                tool, pid))
    return (1 if err else 0), "\n".join(lines)


def _active_cli(explicit=None):
    pl = _platform(explicit)
    return "agy" if pl == "agy" else "claude"


def _launcher(explicit=None):
    """CL26 / R5: the runtime a launcher (review, part, clerk) starts a session with: read from
    PLATFORM_RULES['launcher'] for _platform(), never a binary that is absent."""
    pl = _platform(explicit)
    mode = _rule("launcher", pl)
    if mode == "create_session":
        return "web"
    have = lambda b: shutil.which(b) is not None  # noqa: E731
    if mode == "invoke_subagent":
        return "agy" if have("agy") else ("claude" if have("claude") else None)
    if mode == "claude_bg":
        return "claude" if have("claude") else ("agy" if have("agy") else None)
    if pl == "arena":
        return None
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


def _review_branch(name, rel, text, bases):
    """A branch of origin holding one more file on top of the first base that is here, made without touching the working
    tree, the index or any branch. Its name, or "" when origin did not take it."""
    base = next((b for b in bases if b and _git_run("rev-parse", "-q", "--verify", b + "^{commit}").returncode == 0), "")
    if not base:
        return ""
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(d, "i"))
        run = lambda *a, **k: subprocess.run(["git", "-C", ROOT, "-c", "user.name=board kit", "-c", "user.email=kit@localhost"] + list(a),   # noqa: E731
                                             capture_output=True, text=True, timeout=180, env=env, **k)
        blob = run("hash-object", "-w", "--stdin", input=text).stdout.strip()
        if not blob or run("read-tree", base).returncode or run("update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, rel)).returncode:
            return ""
        c = run("commit-tree", run("write-tree").stdout.strip(), "-p", base, "-m", "review prompt").stdout.strip()
        old = run("ls-remote", "origin", "refs/heads/" + name).stdout.split()
        if old and c:                                        # a second review: on top of the first, never forced
            run("fetch", "-q", "origin", "refs/heads/" + name)
            c = run("commit-tree", c + "^{tree}", "-p", c, "-p", old[0], "-m", "review prompt").stdout.strip() or c
        return name if c and run("push", "-q", "origin", "%s:refs/heads/%s" % (c, name)).returncode == 0 else ""


def review(pid=None, train=None, dry_run=False, force=False, db_path=None, cli=None):
    """Write a review prompt and start it as a background session (Claude Code CLI or agy): a step review
    (pid) or a whole-train review (train). Idempotent: a review already started is not started again."""
    c = _kit_config()
    k = c.get("kit") or {}
    pl = _platform(cli)
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
        if artifact_here():   # M11 (CR-m11) / R5: web review's first pull reads only the step's documents
            scope += (" The board, on the web: `python3 -I .kit/kit.py artifact pull --post %s` (only this step's documents, "
                      "by id), the calls it prints, `python3 -I .kit/kit.py artifact load`." % pid)
        br = str((p.get("proof") or {}).get("branch") or p.get("branch") or "")
        if br and lands_after_review() and force and not dry_run:
            # CL71: a review started again reads the branch as it is now: that commit is what it lands (CL57's third review)
            _git_run("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), timeout=300)
            tip = _git_run("rev-parse", "-q", "--verify", "refs/remotes/origin/%s^{commit}" % br).stdout.strip()
            if tip and tip != p.get("review_commit") and _board_write("update", "posts", pid, {"review_commit": tip}):
                p = dict(p, review_commit=tip)
        if br and lands_after_review():
            scope += (" Its work is on branch `%s`%s, not on the trunk: read and run it there (`git fetch origin %s && git worktree add "
                      "/tmp/review-%s origin/%s`)." % (br, (" at %s" % str(p["review_commit"])[:9]) if p.get("review_commit") else "", br, _slug(pid, 30), br))
    if force:
        # a second review of the same step is a new review: its own id and comment, the first one kept
        base, n = rid, 2
        while os.path.exists(os.path.join(ROOT, ".kit", "run", rid + ".md")):
            rid = "%s-%d" % (base, n)
            n += 1
    session = "%s-review-%s" % (slug, _slug(rid[3:], 30))
    # a dry run shows what an explicitly named CLI would run (nothing runs, so its binary need not be here)
    run = cli if (dry_run and cli in ("agy", "claude")) else _launcher(cli)
    # the guide of the runtime that runs the review (PLATFORM_RULES['consultant_template'])
    tpl_pl = run if run in ("agy", "claude") else pl
    cands = tuple(os.path.join(ROOT, ".kit", rel) for rel in _rule("consultant_template", tpl_pl))
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
    # CL49: the review reports to the orchestrator that launched it (this session), unless another claims the step
    launcher = orchestrator_here()
    orch_id = launcher.get("orchestrator") or step_owner(_get_post(pid, db_path=db_path) if pid else {},
                                                         {"_orchestrator": orch_doc}, review=True)["address"] or ("%s-orchestrator" % slug)
    if launcher.get("orchestrator_name") and launcher["orchestrator_name"] != orch_id:
        orch_id = "%s (session name `%s`, device `%s`)" % (orch_id, launcher["orchestrator_name"], launcher["orchestrator_device"])
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
        # .kit/run is not in git: a cloud session never had this prompt (no web review ever read one). It goes on a branch
        # of its own, on top of the work it reviews.
        rel, rev = "docs/prompts/%s.md" % rid, ""
        if not dry_run:
            rev = _review_branch("review-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", pid if pid and not train else rid)[:60], rel, tpl,
                                 [str((_get_post(pid, db_path=db_path) or {}).get("review_commit") or ""), "HEAD"])
            if not rev and not os.environ.get("KIT_NO_LAUNCH"):
                return 1, ("review %s NOT STARTED: its prompt could not be pushed to a branch of origin, and a web session reads only what "
                           "is there (push access?): `kit.py review %s --force` again" % (rid, pid or "--train %s" % train))
            text = "Your prompt is %s: read it and do it." % rel
        call = {"title": "Review %s" % rid, "source_url": _github_url(),
                "source_revision": rev or _git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main",
                "prompt": text, "tags": ["review"]}      # CL30: the guard checks a tagged launch's prompt
        start = ("review %s NOT STARTED by the kit (a web session cannot start one): prompt %s written; start it "
                 "now with the claude-code-remote `create_session` tool: %s then `python3 -I .kit/kit.py review %s --session-id <its id>`"
                 % (rid, os.path.relpath(out, ROOT), json.dumps(call, ensure_ascii=False), pid or "--train %s" % train))
    elif run == "agy":
        cmd = ["agy"] + _agy_mode_args(k) + ["-p", text]
    elif run is None:
        start = None
    else:
        mode = permission_mode(k)
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
    if launcher and pid and not pid.startswith("_"):     # CL49: who this review reports to, on the step's post
        _board_write("update", "posts", pid, {"review_orchestrator": launcher["orchestrator"],
                                              "review_orchestrator_name": launcher["orchestrator_name"],
                                              "review_orchestrator_device": launcher["orchestrator_device"]})
    if run == "web":
        return 0, start
    if run is None:
        return 1, "review %s %s. Its prompt is %s: start it by hand." % (rid, NO_RUNTIME, os.path.relpath(out, ROOT))
    try:
        subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True,
                         env=dict({kk: vv for kk, vv in os.environ.items() if kk != "CLAUDE_CODE_TASK_LIST_ID"}, KIT_ROLE="review"))
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


def _board_write(cmd, coll, doc_id, data, env=None):
    cli = os.path.join(ROOT, ".kit", "board_cli.py")
    if not os.path.isfile(cli):
        return False
    try:
        r = subprocess.run([sys.executable, "-I", cli, cmd, coll, doc_id, "--data", json.dumps(data, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=20, cwd=ROOT, env=dict(os.environ, **(env or {})))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _all_posts(db_path=None):
    posts, _ = _load_board_docs(os.path.join(ROOT, "board-mirror-missing"), db_path=db_path)
    if not posts and not db_path and os.path.isfile(os.path.join(ROOT, ".kit", "config.json")):
        try:                                      # a part's worktree (or empty local DB): the board server holds it
            r = _board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")), "db/posts?limit=5000", token=_resolve_board_token(None))
            posts = {d["id"]: d.get("data") or {} for d in r.get("docs") or []}
        except Exception as e:  # noqa: BLE001 - kept, never swallowed: resume --role part stops on it
            BOARD_ERRORS.append("the board server refused or failed: %s" % e)
    return posts


def _kit_cards_mod():
    """kit_cards (.kit/kit_cards.py, kit/board/kit_cards.py in the kit's own repo); None if missing."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "kit_cards.py"), os.path.join(here, "..", "kit", "board", "kit_cards.py")):
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("kit_cards_cmd", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.ROOT = ROOT
            return mod
    return None


def cards(db_path=None, url=None, token=None, now=None):
    """`kit.py cards`: "Where we are" (_dashboard) and its trains (_trains) as the posts say now, and the sessions to
    archive, in one line. A script, not an agent: the local board server already rebuilds both cards at each write, so
    this writes only a card that differs (a board the server did not build, a copy); the list of posts never reaches
    the caller's context. (Was the kit-cards subagent on agy and the CLI: a model run at each launch and Done.)"""
    kc = _kit_cards_mod()
    if kc is None:
        return 2, "cards: FAILED no kit_cards.py here: reinstall the kit"
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts = _all_posts(db_path)
    real = {k: v for k, v in posts.items() if isinstance(v, dict) and not k.startswith("_")}
    dash = kc.build(now, board=dict(real))
    trains = kc.build_trains(dict(real), dash["rows"], now)
    for r in dash["rows"]:                       # same rule as kit_cards.main: a row whose train runs, runs
        if r["state"] in ("ready", "idle") and any(x["state"] == "running" for x in trains["rows"] if x["parent"] == r["train"]):
            r["state"] = "running"
    wrote = []
    for did, doc in (("_dashboard", dash), ("_trains", trains)):
        if (posts.get(did) or {}).get("rows") == doc.get("rows"):
            continue
        try:
            _board_request(_resolve_board_url(url or _kit_config().get("board_url")), "db/posts/" + did, {"data": doc},
                           token=token or _resolve_board_token(None), method="PUT")
            wrote.append(did)
        except Exception as e:  # noqa: BLE001
            return 1, "cards: FAILED writing %s: %s" % (did, e)
    try:
        done = set(open(os.path.join(ROOT, ".kit", "archived.txt"), encoding="utf-8").read().split())
    except OSError:
        done = set()
    todo = []
    for p in real.values():                      # any platform's session id (agy's are not session_*)
        sid = str(p.get("session_id") or "")
        if p.get("column") == "done" and sid and not sid.startswith("pending") and sid not in done and sid not in todo:
            todo.append(sid)
    return 0, "cards: %d rows, trains %d (%s); to archive: %s" % (
        len(dash["rows"]), len(trains["rows"]), ("rewrote " + ", ".join(wrote)) if wrote else "already current",
        " ".join(todo) if todo else "none")


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
    mod.AGENTS = _claude_agents
    return mod


def device_line(survey=None):
    d = _device()
    if d is None:
        return None
    try:
        return d.summary(survey or d.survey())
    except Exception as e:
        return "Device: could not measure (%s)." % e


def permission_mode(k=None):
    """The permission mode the kit starts a Claude Code session in. `best` (the default): every check off
    (bypassPermissions) once the owner has accepted that mode himself (Claude Code records it in his settings as
    skipDangerousModePermissionPrompt; the kit never writes it), else auto. The kit's guard hooks and deny rules still apply in
    bypassPermissions (Claude Code's docs). Claude Code refuses it as root; Claude Code on the web ignores it."""
    k = k if k is not None else (_kit_config().get("kit") or {})
    m = str(os.environ.get("KIT_CLAUDE_PERMISSION_MODE") or k.get("claude_permission_mode") or "best").strip()
    if m != "best":
        return m.rstrip("!")                   # "auto!": auto, kept by --update
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "auto"
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(base, "settings.json"), encoding="utf-8") as fh:
            ok = json.load(fh).get("skipDangerousModePermissionPrompt") is True
    except (OSError, ValueError, AttributeError):
        ok = False
    return "bypassPermissions" if ok else "auto"


def _agy_mode_args(k=None):
    """CLI mode flags when launching agy (reviews, etc.): honors KIT_AGY_MODE / kit.agy_mode, and falls back to
    --dangerously-skip-permissions under full_auto (or best) when the cached probe says --mode auto is unavailable."""
    k = k if k is not None else (_kit_config().get("kit") or {})
    want = str(os.environ.get("KIT_AGY_MODE") or k.get("agy_mode") or "auto").strip()
    if want in ("skip-permissions", "bypass", "dangerously-skip-permissions"):
        return ["--dangerously-skip-permissions"]
    if want == "accept-edits":
        return ["--mode", "accept-edits"]
    if want in ("interactive", "default"):
        return []
    autonomy = str(((k.get("autonomy") or {}).get("level") or "")).strip()
    if want == "best" or autonomy == "full_auto":
        cache = os.environ.get("KIT_AGY_MODE_CACHE") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
            "board-kit", "agy-mode-auto")
        try:
            with open(cache, encoding="utf-8") as fh:
                if json.load(fh).get("state") == "unavailable":
                    return ["--dangerously-skip-permissions"]
        except (OSError, ValueError, AttributeError):
            pass
    return ["--mode", "auto"]


def part_list(pid):
    return "%s-%s" % (_slug(_kit_config().get("project") or "project"), _slug(pid, 60))


def _launch_text(rel):
    """A part's start message: the task is in git at `rel`, and one command prints it with how a part works here."""
    return "Your prompt is %s: run `python3 -I .kit/kit.py resume --role part --post %s`, which prints it, and do it." % (rel, os.path.basename(rel)[:-3])


def _part_template(cli=None):
    """This platform's part prompt (PLATFORM_RULES['part_template']): its text, or None."""
    kit_dir = os.path.join(ROOT, ".kit")
    for rel in _rule("part_template", cli):
        cand = os.path.join(kit_dir, rel)
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                return fh.read()
    return None


HOW_HEAD = "## How you work"


def _fill_part(tpl, pid, doc):
    c = _kit_config()
    orch_doc = _get_post("_orchestrator") or {}
    sources = _as_list(doc.get("sources"))
    src = ("\n".join("- comment `%s`: read it with `python3 -I .kit/board_cli.py get comments %s --out-dir /tmp/src`" % (x, x)
                      for x in sources) if sources else
           "(none named: the task is the orchestrator's; ask under your post if it is unclear)")
    # a source is the owner's comment or a review's (a redirect becomes a step that answers it)
    vals = {"<PART>": str(doc.get("part") or pid), "<TITLE>": str(doc.get("title") or pid), "<TRAIN>": str(doc.get("train") or ""), "<POST_ID>": pid,
            "<AFTER>": (", after `%s`" % doc["parent"]) if doc.get("parent") else "",
            "<TASK>": str(doc.get("task") or doc.get("body") or "").strip() or str(doc.get("title") or pid), "<SOURCES>": src,
            "<ORCH_ID>": step_owner(doc, {"_orchestrator": orch_doc})["address"] or "<orchestrator conversationId>",
            # CL38: the session_id a part writes is its real session (a web part: `session_<id>`), worked out by the
            # part's own shell at turn 1; the `<project>-<part>` name only where no session id exists (O54/O55)
            "<SESSION>": "'\"$(python3 -I .kit/kit.py whoami %s-%s)\"'" % (_slug(c.get("project") or "project"), _slug(str(doc.get("part") or pid), 24))}
    for kk, vv in vals.items():
        tpl = tpl.replace(kk, vv)
    return tpl


def part_prompt(pid, p, cli=None):
    """What a part starts from: its task (docs/prompts/<pid>.md, in git) and how a part works on this platform (the kit's, never
    copied into the file). A file from before 2026-11-25 holds both."""
    rel = "docs/prompts/%s.md" % pid
    path, tpl = os.path.join(ROOT, rel), _part_template(cli)
    task = ""
    if str(p.get("task") or "").strip():       # CL54: the post is where the task is written; the file is its copy
        task = (_render_step_prompt(pid, p, cli=cli, path=path if os.path.isfile(path) else None) or "").strip()
        task = re.sub(r"(?m)^Run `python3 -I \.kit/kit\.py resume --role part[^\n]*\n+", "", task)
    if task:
        pass
    elif os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            task = fh.read().strip()
        # the task in git, without the line that sends a reader of the file here
        task = re.sub(r"(?m)^Run `python3 -I \.kit/kit\.py resume --role part[^\n]*\n+", "", task)
    elif p.get("task"):
        task = "## The task\n%s" % str(p["task"]).strip()
    out = [task or "(`%s` is NOT in this clone and the post holds no task: fetch its branch)" % rel]
    if tpl and HOW_HEAD in tpl and HOW_HEAD not in task:
        out += ["", _fill_part(HOW_HEAD + tpl.split(HOW_HEAD, 1)[1], pid, p).strip()]
    return out


def _launch_line(part, rel, model=None, cli=None, post=None):
    c = _kit_config()
    k = c.get("kit") or {}
    lmode = _rule("launcher", cli)
    mode = permission_mode(k)
    name = "%s-%s" % (_slug(c.get("project") or "project"), _slug(part, 24))
    post = post or (os.path.basename(rel)[:-3] if rel and rel.endswith(".md") else None)
    if lmode == "invoke_subagent":
        agy_model = str(model or ((k.get("roles") or {}).get("part") or {}).get("model") or "inherit")
        pid = os.path.basename(rel)[:-3] if rel and rel.endswith(".md") else ""
        # CL54: the prompt is generated from the post first (and committed), then the part starts from it
        return ('%sinvoke_subagent(TypeName="self", Role="Part: %s", Model="%s", Workspace="share", '
                'Prompt="%s")') % (("python3 -I .kit/kit.py step prompt %s, then " % pid) if pid else "", part, agy_model, _launch_text(rel))
    if lmode == "self":
        return ("`python3 -I .kit/kit.py step prompt %s` (the prompt from its post, committed), then work on a branch of its own "
                "from %s (`python3 -I .kit/kit.py resume --role part --post %s`)" % (post or part, rel, post or part))
    model = str(model or ((k.get("claude_cli_roles") or {}).get("part") or {}).get("model") or "inherit")
    if post:   # CL13: one launch command writes the post and the Trains row, then starts the part (CLI or web)
        return "python3 -I .kit/kit.py launch %s%s" % (post, (" --model %s" % model) if model != "inherit" else "")
    gate = "python3 -I .kit/device.py --gate && " if (_rule("device_cap_gate", cli) and _device() is not None) else ""
    return "%sCLAUDE_CODE_TASK_LIST_ID= KIT_ROLE=part claude --bg -n %s -w %s --permission-mode %s%s \"%s\"" % (
        gate, name, _slug(part, 24), mode, (" --model %s" % model) if model != "inherit" else "", _launch_text(rel).replace("`", "\\`"))


def _render_step_prompt(pid, doc, cli=None, path=None):
    """CL54: the prompt a step's post makes, as text (None: no part template here). The task is written once, on the
    post; this file is generated from it at launch, never edited by hand. Its "What this does differently" is the
    post's `differs`, else the one an older file holds (`path`)."""
    tpl = _part_template(cli)
    if tpl is None:
        return None
    head, sep, _ = tpl.partition(HOW_HEAD)
    if sep:
        first, _, rest = head.partition("\n")
        head = ("%s\n\nRun `python3 -I .kit/kit.py resume --role part --post <POST_ID>`: it prints this and how a part works here. "
                "If it fails or prints STOP, do not start: do what it says.\n%s" % (first, rest))
    text = _fill_part(head.rstrip() + "\n", pid, dict(doc, sources=_as_list(doc.get("sources"))))
    where = step_where(doc)
    if where and "\n## The task" in text:
        text = text.replace("\n## The task", "\n## Where to work\n%s\n\n## The task" % where, 1)
    differs = str(doc.get("differs") or "").strip() or (_differs_of({}, path) if path else "")
    if differs:
        text = text.rstrip() + "\n\n" + CIRCLE_HEAD + "\n" + differs + "\n"
    return text


def step_where(post):
    """CL54: where a step works, said first: its repo and branch, with the clone command ('' : the post names no branch).
    R1 and R2 said "clone the repo" with no branch; a session started by hand found the right one only by luck."""
    br = str(post.get("work_branch") or "").strip()
    if not br:
        return ""
    repo = str(post.get("repo") or "").strip()
    url = repo if repo.startswith("http") else ("https://github.com/%s" % repo if repo else "")
    if not url:
        return "Branch `%s` of this repo: `git fetch origin %s && git checkout %s`." % (br, br, br)
    name = url.rstrip("/").split("/")[-1].replace(".git", "")
    return ("Repo %s, branch `%s`: `git clone -b %s %s && cd %s` (already cloned: `git fetch origin %s && git checkout %s`)."
            % (url.replace("https://github.com/", ""), br, br, url, name, br, br))


def _write_step_prompt(pid, doc, sources, cli=None):
    """docs/prompts/<pid>.md written from the post (the step's task and his words); how a part works is the kit's
    (part_prompt prints it). Never overwrites one that exists: a launch regenerates it (sync_step_prompt)."""
    rel = "docs/prompts/%s.md" % pid
    path = os.path.join(ROOT, rel)
    if os.path.exists(path):
        return rel, False
    text = _render_step_prompt(pid, dict(doc, sources=list(sources)), cli=cli)
    if text is None:
        return None, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel, True


def _prompt_task(text):
    """The `## The task` section of a prompt, its spaces folded (None: the text has none)."""
    m = re.search(r"(?ms)^## The task[ \t]*\n(.*?)(?=^## |\Z)", text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def post_task(post, pid=None):
    """CL54: a step's task as its post holds it (the one place it is written)."""
    return str(post.get("task") or post.get("body") or "").strip() or str(post.get("title") or pid or "")


def prompt_drift(pid, post, root=None):
    """CL54: None, or how docs/prompts/<pid>.md differs from its post's task: edited by hand, or the post changed and
    no launch has regenerated it yet."""
    rel = "docs/prompts/%s.md" % pid
    try:
        with open(os.path.join(root or ROOT, rel), encoding="utf-8") as fh:
            have = _prompt_task(fh.read())
    except OSError:
        return None
    want = re.sub(r"\s+", " ", post_task(post, pid)).strip()
    if have is None or not want or have == want:
        return None
    return "%s says a task its post %s does not (%s); a prompt is generated from its post at launch, never edited by hand: " \
           "put the task on the post (or launch again to regenerate the file)" % (rel, pid, _short(have, 80))


def sync_step_prompt(pid, post, cli=None, commit=True):
    """CL54: docs/prompts/<pid>.md generated from the post as it is now, and committed (that path only) when it is new
    or differs: the record of what each part was told stays in git, each version its own commit. Returns {rel, written,
    commit, regenerated} or {rel, error}."""
    rel = "docs/prompts/%s.md" % pid
    path = os.path.join(ROOT, rel)
    res = {"rel": rel, "written": False, "commit": None, "regenerated": False}
    if os.path.isfile(path) and not str(post.get("task") or "").strip():
        return res        # a post from before CL54 that holds no task: its file is the task, kept as it is
    text = _render_step_prompt(pid, post, cli=cli, path=path if os.path.isfile(path) else None)
    if text is None:
        return res if os.path.isfile(path) else dict(res, error="no part template here to generate %s from (reinstall the kit)" % rel)
    cur = _read_text(path) if os.path.isfile(path) else None
    if cur != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        res.update(written=True, regenerated=cur is not None)
    if not commit:
        return res
    tracked = _git_run("ls-files", "--error-unmatch", rel).returncode == 0
    if tracked and _git_run("diff", "--quiet", "HEAD", "--", rel).returncode == 0:
        return res
    _git_run("add", "--", rel)
    msg = "prompt(%s): %s from post %s" % (str(post.get("part") or pid), "regenerated, the post changed" if tracked else "generated", pid)
    r = _git_run("commit", "-q", "--no-verify", "-m", msg, "--", rel)
    if r.returncode != 0:
        return dict(res, error="could not commit %s: %s" % (rel, (r.stderr or r.stdout).strip()[:300]))
    res["commit"] = _git_run("rev-parse", "--short", "HEAD").stdout.strip()
    res["regenerated"] = tracked
    return res


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


TRAIN_MAX_STEPS = 12     # a train is one goal: past this many steps it is several (the owner, 2026-11-26: `cli` held 46)
TRAIN_MAX_DAYS = 4       # a train open longer than this, with steps still open, says so to the orchestrator
STALE_STEP_HOURS = 24    # a step "in progress" with no change for this long: its session is likely gone


def train_record(posts, train):
    """The train's own record, `_train-<id>`: {goal, status open|closed, why}; None for a train started before records."""
    r = posts.get("_train-" + _norm_train(train))
    return r if isinstance(r, dict) else None


def trains_new(train, goal, db_path=None):
    """`kit.py trains new <id> --goal "..."`: a train is one goal, said in one sentence, that it can finish."""
    t = _norm_train(train)
    if not t or not str(goal or "").strip():
        return {"error": "a train needs an id and a goal (--goal \"what is true when it is finished\")"}
    posts = _all_posts(db_path)
    if not posts and BOARD_ERRORS:
        return {"error": "cannot read the board: %s" % BOARD_ERRORS[-1]}
    if train_record(posts, t):
        return {"error": "train %s exists: %s" % (t, train_record(posts, t).get("goal"))}
    doc = {"title": "Train %s" % t, "kind": "train", "train": t, "goal": str(goal).strip(), "status": "open",
           "column": "info", "from": "orchestrator", "created_at": _now_z()}
    if not _board_write("set", "posts", "_train-" + t, doc):
        return {"error": "the board did not take train %s (is the board server running?)" % t}
    return {"train": t, "goal": doc["goal"], "ok": True}


def trains_close(train, why="", db_path=None):
    """`kit.py trains close <id> --why "..."`: no new step goes on it; its open steps stay where they are."""
    t = _norm_train(train)
    posts = _all_posts(db_path)
    if not posts and BOARD_ERRORS:
        return {"error": "cannot read the board: %s" % BOARD_ERRORS[-1]}
    rec = dict(train_record(posts, t) or {"title": "Train %s" % t, "kind": "train", "train": t, "goal": "",
                                          "column": "info", "created_at": _now_z()})
    rec.update({"status": "closed", "why_closed": str(why or ""), "closed_at": _now_z()})
    if not _board_write("set", "posts", "_train-" + t, rec):
        return {"error": "the board did not take the close of train %s" % t}
    still = [q.get("part") or pid for pid, q in train_steps(posts, t) if _step_open(q)]
    return {"train": t, "ok": True, "closed": True, "open_steps_kept": still}


def _step_open(q):
    st = str(q.get("status") or "").lower()
    return not (st.startswith("complete") or st in ("done", "dropped", "removed") or str(q.get("column") or "") == "done"
                or st.startswith("checked"))


def train_check(posts, train):
    """Why a new step may not go on this train ("" when it may): closed, or already as long as a train gets."""
    t = _norm_train(train)
    rec = train_record(posts, t)
    if rec and str(rec.get("status")) == "closed":
        return "train %s is closed (%s): start a train for this goal with `kit.py trains new <id> --goal \"...\"`" % (
            t, rec.get("why_closed") or "its goal is met")
    steps = train_steps(posts, t)
    if not steps and not rec:
        return ("train %s does not exist yet: start it with `kit.py trains new %s --goal \"what is true when it is "
                "finished\"`, then this step" % (t, t))
    if len(steps) >= TRAIN_MAX_STEPS:
        return ("train %s has %d steps (a train is one goal, at most %d): close it (`kit.py trains close %s`) and start "
                "one for this step's goal" % (t, len(steps), TRAIN_MAX_STEPS, t))
    return ""


def train_lines(posts, now=None):
    """For the orchestrator's resume: trains past their size or age, and steps "in progress" nobody has touched."""
    now = now or _now_z()
    def age_h(ts):
        try:
            a = datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"); b = datetime.strptime(str(now)[:19], "%Y-%m-%dT%H:%M:%S")
            return (b - a).total_seconds() / 3600
        except ValueError:
            return None
    names = sorted({_norm_train(q.get("train")) for pid, q in posts.items() if isinstance(q, dict) and not pid.startswith("_")
                    and str(q.get("train") or "").strip()})
    out, stale = [], []
    for t in names:
        rec = train_record(posts, t)
        steps = train_steps(posts, t)
        opened = [q for _, q in steps if _step_open(q)]
        if rec and str(rec.get("status")) == "closed":
            continue
        first = min((str(q.get("created_at") or "") for _, q in steps if q.get("created_at")), default="")
        days = (age_h(first) or 0) / 24 if first else 0
        why = []
        if len(steps) > TRAIN_MAX_STEPS:
            why.append("%d steps (at most %d)" % (len(steps), TRAIN_MAX_STEPS))
        if opened and days > TRAIN_MAX_DAYS:
            why.append("open %d days with %d steps still open" % (days, len(opened)))
        if not rec:
            why.append("no goal written")
        if why and (len(steps) > TRAIN_MAX_STEPS or (opened and days > TRAIN_MAX_DAYS)):
            out.append("- train `%s`: %s: close it (`kit.py trains close %s --why ...`) and start one train per goal "
                       "for what is still open (`kit.py trains new`, then move those steps)" % (t, "; ".join(why), t))
    for pid, q in sorted(posts.items()):
        if pid.startswith("_") or not isinstance(q, dict) or "progress" not in str(q.get("status") or "").lower():
            continue
        h = age_h(q.get("updated_at") or q.get("started_at") or q.get("created_at"))
        if h is not None and h > STALE_STEP_HOURS:
            stale.append("- `%s` (%s): \"in progress\" with no change for %d h: is its session alive? If not: status "
                         "stalled (continue it from its branch) or complete (its work landed)" % (pid, q.get("part") or pid, h))
    res = []
    if out:
        res += ["", "## Trains that are several goals"] + out
    if stale:
        res += ["", "## Steps marked in progress that nobody is working on"] + stale
    return res


SPLIT_MAX_PIECES = 6     # more pieces than this in one step: split it (the owner, 2026-11-26: CL44 held 14 unrelated items)
SPLIT_MAX_GROUPS = 2     # more separate groups of files than this: they can run as parallel steps


def split_groups(pieces):
    """The pieces in groups that share files ([[piece n, ...], ...]). A file most pieces name (a hub such as kit.py, when
    4+ pieces) joins nothing: different functions of one big file merge cleanly. A piece naming no file (run the tests, the
    live check) is the whole step's, not a group."""
    ps = [x for x in pieces or [] if isinstance(x, dict)]
    count = {}
    for x in ps:
        for fl in set(x.get("files") or []):
            count[fl] = count.get(fl, 0) + 1
    hub = {fl for fl, c in count.items() if len(ps) >= 4 and c * 2 > len(ps)}
    groups = []                                    # [(set of files, [n])]
    for x in ps:
        fs = set(x.get("files") or []) - hub
        if not fs:
            continue
        hit = [g for g in groups if g[0] & fs]
        merged = (set(fs), [x.get("n")])
        for g in hit:
            merged[0].update(g[0]); merged[1][:0] = g[1]
            groups.remove(g)
        groups.append(merged)
    return [sorted(n for n in g[1] if n is not None) for g in groups], hub


def split_check(pieces):
    """Why a step should be several steps ("" when it is fine): too many pieces, or pieces in separate groups of files
    that parallel parts could take side by side. The proposal names the groups."""
    ps = [x for x in pieces or [] if isinstance(x, dict)]
    if not ps:
        return ""
    groups, hub = split_groups(ps)
    if len(ps) <= SPLIT_MAX_PIECES and len(groups) <= SPLIT_MAX_GROUPS:
        return ""
    title = {x.get("n"): str(x.get("title") or "")[:50] for x in ps}
    files = {x.get("n"): set(x.get("files") or []) - hub for x in ps}
    rows = ["  - pieces %s: %s%s" % (", ".join(map(str, g)), "; ".join(title[n] for n in g[:3]) + ("; ..." if len(g) > 3 else ""),
                                     (" (%s)" % ", ".join(sorted(set().union(*[files[n] for n in g]))[:4])) if any(files[n] for n in g) else "")
            for g in groups]
    return ("%d pieces in %d separate groups of files (at most %d pieces and %d groups a step): one step per group, run "
            "side by side where memory allows%s:\n%s" % (len(ps), len(groups), SPLIT_MAX_PIECES, SPLIT_MAX_GROUPS,
                                                         (" (shared by most, ignored: %s)" % ", ".join(sorted(hub))) if hub else "",
                                                         "\n".join(rows)))


# ---------------------------------------------------------------- R6: the queue, re-ranked at each new step
# The owner, 2026-11-26: every new step is measured against every open step, for its place in the queue and for what can
# run beside it; never ordered by date. On 11-26 M5 to M8 (features) were launched while CL51 waited, and CL51's bug then
# overwrote 11 posts. Facts per pair: does one repair damage; does one wait on the other; do they share files or an area;
# is one a bug in what the other builds. Order: damage first, then what unblocks the most, then bugs before features, date
# only to break a tie. Two steps that share files or an area never run at the same time.
QUEUE_DAMAGE = re.compile(r"\b(overwr(?:o|i)te\w*|overwrit\w*|data (?:loss|lost|at risk)|(?:lost|loses?|losing|dropp?(?:ed|s)?) "
                          r"(?:data|posts?|comments?|work|rows?|cards?|answers?)|corrupt\w*|wiped?|wipes|erased?|erases|"
                          r"deleted? (?:posts?|comments?|data|rows?)|wrong data|damage[sd]?|"
                          r"(?:posts?|comments?|data|rows?|work|cards?|answers?) (?:were |was |are |is |got )?"
                          r"(?:lost|overwritten|wiped|erased|deleted|dropped))\b", re.I)
QUEUE_NOT = re.compile(r"\b(?:never|not|no|without|nor|cannot|can't|won't|doesn't|don't)\b(?:\s+\S+){0,2}\s*$", re.I)
QUEUE_BLOCKS_ALL = re.compile(r"\b(?:every|all|each)\s+(?:other\s+)?steps?\b[^.]{0,60}\bwait|\bnothing\s+(?:reaches|lands|merges|"
                              r"goes)\b[^.]{0,40}\buntil\b", re.I)
QUEUE_BUG = re.compile(r"\b(bug|bugs|fix|fixe[sd]|broken|regress\w*|crash\w*|wrong|wrongly|leaks?)\b", re.I)
QUEUE_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|js|json|md|html|sh|ts|css|toml|ya?ml))\b")


def _q_text(q):
    return "%s\n%s" % (q.get("title") or "", q.get("task") or q.get("body") or "")


def _q_files(q):
    """The files a step touches, by name (kit.py, .kit/kit.py and scripts/kit.py are one file: the kit copies it)."""
    fs = set()
    for x in q.get("subtasks") or []:
        if isinstance(x, dict):
            fs.update(str(f) for f in x.get("files") or [])
    fs.update(str(f) for f in q.get("files") or [])
    fs.update(QUEUE_PATH.findall(_q_text(q)))
    return {os.path.basename(f.strip().strip("`")) for f in fs if f.strip()}


def _q_paths(q):
    fs = set()
    for x in q.get("subtasks") or []:
        if isinstance(x, dict):
            fs.update(str(f) for f in x.get("files") or [])
    fs.update(str(f) for f in q.get("files") or [])
    fs.update(QUEUE_PATH.findall(_q_text(q)))
    return {f.strip().strip("`") for f in fs if f.strip()}


def _q_areas(q, areas=None):
    """The areas of the map (M1: `map.areas` globs in .kit/config.json) a step's files fall in; without a map, none
    (the shared files alone decide)."""
    if areas is None:
        areas = ((_kit_config().get("map") or {}).get("areas") or {}) if os.path.isfile(os.path.join(ROOT, ".kit", "config.json")) else {}
    out = set()
    for f in _q_paths(q):
        for name, globs in (areas or {}).items():
            if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in ([globs] if isinstance(globs, str) else globs or [])):
                out.add(str(name))
    return out


QUEUE_WAIT_BEFORE = (r"(?:after|builds? on|built on|on top of|waits? (?:on|for|behind)|waiting (?:on|for|behind)|once|needs|"
                     r"requires|depends on|until|blocked by|behind)\s+(?:the\s+|step\s+|part\s+)?")
QUEUE_WAIT_AFTER = r"(?:'s)?\s+(?:first|lands|landed|is done|must land|merges|is merged)\b"


def _q_names(q, part):
    """Does this step say it waits on that part ("waits on M1", "after CL51", "builds on M3", "once CL47 lands")? A mere
    mention is history (CL51's task tells what CL44's part did), not a wait. A part has a digit and two characters."""
    part = str(part or "")
    if not re.search(r"\d", part) or len(part) < 2:
        return False
    p = r"(?<![\w-])%s(?![\w-])" % re.escape(part)
    return re.search(QUEUE_WAIT_BEFORE + p + "|" + p + QUEUE_WAIT_AFTER, _q_text(q), re.I) is not None


def _q_lead(q):
    """The title and the task's first two sentences: what the step is for, not the history it tells."""
    t = re.sub(r"\s+", " ", str(q.get("task") or q.get("body") or "")).strip()
    return "%s. %s" % (q.get("title") or "", " ".join(re.split(r"(?<=[.!?])\s+", t)[:2]))


def _q_damage(text):
    """The first damage word the text says happened (not "never overwrites": a negation two words before it is none)."""
    for m in QUEUE_DAMAGE.finditer(text):
        if not QUEUE_NOT.search(text[max(0, m.start() - 40):m.start()]):
            return m.group(0)
    return ""


def step_facts(pid, q, areas=None):
    """The facts the queue reads about one step: damage, bug, files, areas, the parts it says it comes after."""
    text = _q_lead(q)
    kind = str(q.get("kind") or "").lower()
    said = _q_damage(text) if kind != "feature" else ""
    damage = bool(q.get("damage")) or bool(said)
    bug = damage or kind == "bug" or (kind != "feature" and QUEUE_BUG.search(text) is not None)
    after = q.get("after") or q.get("blocked_by") or []
    after = [after] if isinstance(after, str) else list(after)
    st = str(q.get("status") or "").lower()
    return {"id": pid, "part": str(q.get("part") or pid), "damage": damage, "bug": bug, "files": _q_files(q),
            "areas": _q_areas(q, areas), "after": [str(x) for x in after], "created": str(q.get("created_at") or ""),
            "running": "progress" in st, "damage_why": str(q.get("damage") or said), "held": step_hold(q),
            "blocks_all": QUEUE_BLOCKS_ALL.search(_q_text(q)) is not None}


def step_pair(a, b, qa, qb):
    """What two open steps are to each other, from facts: {first: id that must go first or None, why, shares: files,
    areas: areas}. One waits on the other when it says so (after/blocked_by), names the other's part (it builds on it),
    or is a feature on the files of the other's bug fix."""
    first, why = None, ""
    a_on_b = b["part"] in a["after"] or b["id"] in a["after"] or _q_names(qa, b["part"])
    b_on_a = a["part"] in b["after"] or a["id"] in b["after"] or _q_names(qb, a["part"])
    if a_on_b and b_on_a:                     # each names the other: the older one is what the newer builds on
        a_on_b, b_on_a = a["created"] > b["created"], a["created"] <= b["created"]
    shares = sorted(a["files"] & b["files"])
    areas = sorted(a["areas"] & b["areas"])
    if b_on_a:
        first, why = a["id"], "%s builds on it (names %s)" % (b["part"], a["part"])
    elif a_on_b:
        first, why = b["id"], "%s builds on it (names %s)" % (a["part"], b["part"])
    elif (shares or areas) and a["bug"] != b["bug"]:
        bug, feat = (a, b) if a["bug"] else (b, a)
        first, why = bug["id"], "a bug in what %s builds (%s)" % (feat["part"], ", ".join(shares or areas)[:60])
    return {"first": first, "why": why, "shares": shares, "areas": areas}


def _q_is_step(q):
    """A step, not a question, an issue or a report filed under a train (Harmonize's F5, VR5, HL5, O56 are questions)."""
    return str(q.get("kind") or "").lower() not in ("question", "issue", "report", "awaits") and \
        str(q.get("column") or "").lower() != "question"


def queue_facts(posts, areas=None):
    """(open steps, their facts, the hub): a file or area most open steps name (more than half, 4 steps or more: kit.py
    here) is not shared by itself, as in the split check (different functions of one big file merge cleanly);
    `kit.queue.strict_files: true` in .kit/config.json counts it too."""
    open_ = {pid: q for pid, q in posts.items() if isinstance(q, dict) and not pid.startswith("_") and not q.get("removed")
             and str(q.get("train") or "").strip() and _step_open(q) and _q_is_step(q)}
    f = {pid: step_facts(pid, q, areas) for pid, q in open_.items()}
    strict = bool(((_kit_config().get("kit") or {}).get("queue") or {}).get("strict_files")) if os.path.isfile(
        os.path.join(ROOT, ".kit", "config.json")) else False
    count = {}
    for x in f.values():
        for k in x["files"] | {"area " + a for a in x["areas"]}:
            count[k] = count.get(k, 0) + 1
    hub = set() if strict else {k for k, c in count.items() if len(f) >= 4 and c * 2 > len(f)}
    for x in f.values():
        x["files"] -= hub
        x["areas"] -= {k[5:] for k in hub if k.startswith("area ")}
    return open_, f, hub


def queue_rank(posts, areas=None):
    """Every open train step, ranked: [{id, part, place, why, running, can_start, waits}] (place 1 is launched first).
    Re-ranked whole at each call: nothing is kept between two rankings."""
    open_, f, _hub = queue_facts(posts, areas)
    deps = {pid: {} for pid in open_}          # pid -> {id it waits on: why}
    clash = {pid: {} for pid in open_}         # pid -> {id it cannot run beside: files/areas}
    ids = sorted(open_)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            p = step_pair(f[a], f[b], open_[a], open_[b])
            if p["first"]:
                later = b if p["first"] == a else a
                deps[later][p["first"]] = p["why"]
            if p["shares"] or p["areas"]:
                clash[a][b] = clash[b][a] = ", ".join(p["shares"] + ["area " + x for x in p["areas"]])
    waiters = {pid: set() for pid in open_}    # who waits on pid, directly or not
    for pid in open_:
        seen, todo = set(), list(deps[pid])
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(deps.get(x) or [])
        for x in seen:
            waiters[x].add(pid)
    for pid in open_:                          # "every step since 11-24 waits behind it" (CL47): it unblocks them all
        if f[pid]["blocks_all"]:
            waiters[pid] |= set(open_) - {pid}
    key = lambda pid: (0 if f[pid]["damage"] else 1, -len(waiters[pid]), 0 if f[pid]["bug"] else 1, f[pid]["created"], pid)  # noqa: E731
    order, left = [], set(open_)
    while left:
        ready = [p for p in left if not (set(deps[p]) & left)] or list(left)    # a cycle: the best of what is left
        nxt = min(ready, key=key)
        order.append(nxt)
        left.discard(nxt)
    running = {p for p in open_ if f[p]["running"]}
    rows, started = [], set(running)
    for n, pid in enumerate(order, 1):
        why = []
        if f[pid]["damage"]:
            why.append("repairs damage (%s)" % _short(f[pid]["damage_why"], 40))
        before = [x for x in order[n:] if pid in deps[x]]
        if before:
            why.append("before %s: %s" % (", ".join(f[x]["part"] for x in before[:4]) + (" ..." if len(before) > 4 else ""),
                                           deps[before[0]][pid]))
        if deps[pid]:
            why.append("after %s" % ", ".join(f[x]["part"] for x in deps[pid] if x in open_))
        if f[pid]["blocks_all"]:
            why.append("every other step waits behind it (its task says so)")
        if not why:
            why.append("bug before features" if f[pid]["bug"] else "feature, by date (no fact sets it apart)")
        waits = []
        if pid not in running and not f[pid]["held"]:
            waits += ["%s first (%s)" % (f[x]["part"], deps[pid][x]) for x in deps[pid] if x in open_]
            waits += ["%s %s (shares %s)" % (f[x]["part"], "runs" if x in running else "goes first", clash[pid][x])
                      for x in clash[pid] if x in started]
        can = pid not in running and not waits and not f[pid]["held"]   # CL62: a held step starts nothing, blocks nothing
        if can:
            started.add(pid)                   # what can run beside it is measured against it from here on
        rows.append({"id": pid, "part": f[pid]["part"], "place": n, "why": "; ".join(why), "running": pid in running,
                     "can_start": can, "waits": waits, "damage": f[pid]["damage"], "bug": f[pid]["bug"],
                     "held": f[pid]["held"]})
    return rows


def queue_lines(posts, areas=None, limit=40):
    rows = queue_rank(posts, areas)
    if not rows:
        return ["No open step."]
    out = []
    for r in rows[:limit]:
        tag = "running" if r["running"] else ("held: " + r["held"][:120] if r.get("held") else
                                              "start now" if r["can_start"] else "waits: " + "; ".join(r["waits"])[:160])
        out.append("%d. %s `%s` [%s] %s" % (r["place"], r["part"], r["id"], tag, r["why"]))
    if len(rows) > limit:
        out.append("(%d more)" % (len(rows) - limit))
    return out


def _resume_queue(posts):
    """R6, the orchestrator's resume: launch from the top; every new step re-ranks it."""
    return ["", "## The queue (launch from the top; `kit.py queue` for all of it, a launch out of order is refused)"] + \
        queue_lines(posts, limit=12)


def queue_text(posts, areas=None):
    hub = queue_facts(posts, areas)[2]
    return "\n".join(["# The queue (every open step against every other: damage first, then what unblocks the most, "
                      "then bugs before features, date only for a tie; launch from the top)"] + queue_lines(posts, areas)
                     + (["(Named by most open steps, not a clash by itself: %s; `kit.queue.strict_files` counts it.)"
                         % ", ".join(sorted(hub))] if hub else [])) + "\n"


def order_problem(posts, pid, areas=None):
    """None, or why launching this step now is out of order: a step ranked before it could start now, it waits on an
    open step, or it shares files with a running one. The step to launch instead is named."""
    rows = queue_rank(posts, areas)
    me = next((r for r in rows if r["id"] == pid), None)
    if me is None or me["running"]:
        return None
    if me["waits"]:
        return "%s waits: %s" % (me["part"], "; ".join(me["waits"]))
    first = next((r for r in rows if r["place"] < me["place"] and r["can_start"]), None)
    if first:
        return "%s goes first (place %d, %s is %d): %s; launch `%s`" % (first["part"], first["place"], me["part"], me["place"],
                                                                      first["why"], first["id"])
    return None


# CL62: "start now" is enforced, not read. Orchestrator 2 on the web read [start now] for M2, M3, M4 at 02:28, followed
# a stale hand-over line ("M2 and M3 wait on M1", M1 had landed at 02:19) and launched them at 03:09 when the owner
# asked. The orchestrator's Stop is refused once while a start-now step is neither launched nor held with a reason.
def step_hold(q):
    """Why this step is held ("" when it is not): `kit.py hold` records {why, at, by} in `held`; a status "held: ..."
    written by hand counts too."""
    h = q.get("held") if isinstance(q, dict) else None
    if isinstance(h, dict) and str(h.get("why") or "").strip():
        return str(h["why"]).strip()
    if isinstance(h, str) and h.strip():
        return h.strip()
    st = str((q or {}).get("status") or "")
    if STEP_HELD.match(st):
        return st.split(":", 1)[1].strip() if ":" in st else st.strip()
    return ""


def split_owners(posts, me=""):
    """{post id or part (lower case): the other orchestrator that owns it} from the split recorded on `_orchestrator`:
    `split: {<orchestrator address>: [<post id or part>, ...]}`. What this orchestrator (`me`) owns is not in it."""
    o = (posts or {}).get("_orchestrator")
    sp = o.get("split") if isinstance(o, dict) else None
    out = {}
    for who, items in (sp.items() if isinstance(sp, dict) else []):
        if me and str(who) == str(me):
            continue
        for x in _as_list(items):
            out[str(x).strip().lower()] = str(who)
    return out


def _launch_marked(pid, root=None):
    return os.path.isfile(os.path.join(root or ROOT, ".kit", "run", "launch", pid + ".json"))


def start_now_unlaunched(posts, me=None, areas=None, root=None):
    """The queue's [start now] steps that this orchestrator has neither launched nor held: [{id, part, place, why}].
    Launched: running on the board, or `kit.py launch` marked it here (the board copy may lag). Held: `kit.py hold`.
    A step the split on `_orchestrator` gives another orchestrator counts as held for this one."""
    me = orchestrator_here().get("orchestrator", "") if me is None else me
    others = split_owners(posts, me)
    out = []
    for r in queue_rank(posts, areas):
        if not r["can_start"] or r["running"] or r.get("held"):
            continue
        if r["id"].lower() in others or r["part"].lower() in others or _launch_marked(r["id"], root):
            continue
        out.append({"id": r["id"], "part": r["part"], "place": r["place"], "why": r["why"]})
    return out


def start_now_refusal(rows):
    """The Stop hook's text for these unlaunched start-now steps: the list and the exact commands ("" when none)."""
    if not rows:
        return ""
    out = ["Before you stop (CL62): the queue says start now and you have neither launched nor held these:"]
    for r in rows:
        out.append("- %s `%s` (place %d, %s): `python3 -I .kit/kit.py launch %s`" % (r["part"], r["id"], r["place"],
                                                                                  _short(r["why"], 80), r["id"]))
    out.append("Launch each (on the web `launch` prints the create_session call, then `launch <post> --session-id <id>`), "
               "or hold it with the reason the owner will read: `python3 -I .kit/kit.py hold <post> --why \"...\"`. "
               "A hand-over line or your own plan never overrides the queue.")
    return "\n".join(out)


def hold(pid, why="", release=False, url=None, token=None):
    """`kit.py hold <post> --why "..."`: the orchestrator does not launch this start-now step yet, and says why. It is
    recorded on the post (`held`, status "held: <why>", shown on the board, in resume and the queue); `--release` undoes it."""
    url = url or local_board_url()
    token = _resolve_board_token(token)
    try:
        d = _board_request(url, "doc/posts/" + urllib.parse.quote(pid, safe=""), token=token)
        p = d.get("data") if isinstance(d.get("data"), dict) else None
    except Exception as e:
        return {"ok": False, "post": pid, "error": "cannot read the board at %s: %s" % (url, e)}
    if p is None:
        return {"ok": False, "post": pid, "error": "no post %s on this board" % pid}
    if release:
        prev = str(p.get("held_prev_status") or "planned")
        return _owner_write(pid, {"status": prev}, url=url, token=token, drop=("held", "held_prev_status"))
    why = re.sub(r"\s+", " ", str(why or "")).strip()
    if len(why) < 8:
        return {"ok": False, "post": pid, "error": "a hold needs its reason, in words the owner reads: --why \"...\""}
    st = str(p.get("status") or "planned")
    fields = {"held": {"why": why[:300], "at": _now_z(), "by": orchestrator_here().get("orchestrator", "")},
              "status": "held: " + why[:120]}
    if not STEP_HELD.match(st):
        fields["held_prev_status"] = st
    return _owner_write(pid, fields, url=url, token=token)


HANDOVER_WAIT = re.compile(r"((?:[A-Z]{1,4}\d+[a-z]?)(?:\s*(?:,|and|&|\+)\s*(?:[A-Z]{1,4}\d+[a-z]?))*)\s+(?:still\s+)?"
                           r"(?:waits?|waiting|are waiting|is waiting|stay(?:s)? behind|blocked)\s+(?:on|for|behind|by)\s+"
                           r"([A-Z]{1,4}\d+[a-z]?)\b")


def _hhmm(z):
    m = re.search(r"T(\d\d:\d\d)", str(z or ""))
    return m.group(1) if m else str(z or "?")


def stale_handover_lines(posts, comments, limit=40, hours=48, areas=None):
    """CL62: a hand-over line never overrides the queue. Each "X waits on Y" in the orchestrators' comments under
    `_orchestrator` (the last `hours` before the newest, at most `limit`) is compared with the board; a claim whose Y
    has landed (or is complete) is printed, first. A claim that still holds prints nothing."""
    notes = sorted((c for c in (comments or {}).values() if isinstance(c, dict) and c.get("post_id") == "_orchestrator"
                    and str(c.get("from") or c.get("author") or "").lower() not in ("owner", "you", "")),
                   key=lambda c: str(c.get("created_at") or ""))[-limit:]
    if notes:
        try:
            newest = iso(str(notes[-1].get("created_at")))
            notes = [c for c in notes if (newest - iso(str(c.get("created_at")))).total_seconds() <= hours * 3600]
        except Exception:
            pass
    by_part = {}
    for pid, p in (posts or {}).items():
        if isinstance(p, dict) and not pid.startswith("_") and p.get("part"):
            by_part.setdefault(str(p["part"]).lower(), (pid, p))
    rank = {r["part"].lower(): r for r in queue_rank(posts, areas)}
    out, seen = [], set()
    for c in reversed(notes):
        for m in HANDOVER_WAIT.finditer(str(c.get("text") or c.get("body") or "")):
            on = m.group(2)
            hit = by_part.get(on.lower())
            if not hit:
                continue
            _pid, y = hit
            if _step_open(y) and not y.get("landed"):
                continue                                    # the claim still holds
            gone = ("landed %s" % _hhmm(y.get("landed_at") or y.get("completed_at") or y.get("updated_at"))) if y.get("landed") \
                else "%s %s" % (str(y.get("status") or "done").split(":")[0], _hhmm(y.get("completed_at") or y.get("updated_at")))
            for x in re.findall(r"[A-Z]{1,4}\d+[a-z]?", m.group(1)):
                if (x, on) in seen:
                    continue
                seen.add((x, on))
                r = rank.get(x.lower())
                now = ("start now: `python3 -I .kit/kit.py launch %s`" % r["id"]) if r and r["can_start"] else \
                    ("running" if r and r["running"] else ("held: " + r["held"]) if r and r.get("held") else
                     ("waits: " + "; ".join(r["waits"])[:120]) if r else "not an open step")
                out.append("- %s waits on %s (said %s): %s %s, %s" % (x, on, _hhmm(c.get("created_at")), on, gone, now))
    return ["## First: hand-over lines the board contradicts (the queue wins)"] + out if out else []


def pause_messages(posts, new_pid, areas=None):
    """A new damage step: the running steps it would undo or redo (they build on it, or share its files) are asked to
    pause at their next save point. [{post, part, session_id, message}]."""
    q = posts.get(new_pid) or {}
    open_, f, _hub = queue_facts(posts, areas)
    fn = f.get(new_pid) or step_facts(new_pid, q, areas)
    if not fn["damage"]:
        return []
    out = []
    for pid, r in open_.items():
        fr = f[pid]
        if pid == new_pid or not fr["running"]:
            continue
        p = step_pair(fn, fr, q, r)
        if p["first"] == new_pid or p["shares"] or p["areas"]:
            why = p["why"] or "it shares %s" % ", ".join(p["shares"] + p["areas"])
            out.append({"post": pid, "part": fr["part"], "session_id": str(r.get("session_id") or ""),
                        "message": "Pause at your next save point (commit and push your branch, update your post): %s repairs "
                                   "damage (%s) and %s. Continue when it is done; `kit.py queue` shows the order."
                                   % (fn["part"], _short(fn["damage_why"], 50), why)})
    return out


def step_new(train, part, title, task="", parent=None, sources=(), model=None, review=True, db_path=None, cli=None,
             board=None, keep_whole=None, keep_train=None, damage=None, after=(), kind=None):
    """A train step: its post (adopted when the train already has one for that part, never duplicated) and its
    generated prompt. Returns {post, adopted, prompt, prompt_written, launch} or {error}. On an attached board (CL25)
    also {board, artifact_write}: the post is a file under .kit/run/artifact/out/repos/<id>/posts/, sent by the one
    ArtifactData call `artifact_write` names (the orchestrator guard lets that file through)."""
    posts = _all_posts(db_path)
    if not posts and BOARD_ERRORS:
        return {"error": "cannot read the board: %s" % BOARD_ERRORS[-1]}
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
        rel = "docs/prompts/%s.md" % found      # CL54: written from the post at launch
        res = {"post": found, "adopted": True, "prompt": rel, "prompt_written": False,
               "launch": _launch_line(part, rel, model, cli=cli)}
        if att:
            res["board"] = att
        return res
    why_train = train_check(posts, train) if not keep_train else ""
    if why_train:
        return {"error": "step %s: %s. (A step that must go on this train anyway: --keep-train \"<why>\".)" % (part, why_train),
                "train_full": True}
    why_split = split_check(task_pieces(task))
    if why_split and not keep_whole:
        return {"error": "step %s is several steps: %s\nPlan one `step new` per group, or keep it whole with --keep-whole "
                         "\"<why these must be one part>\" (recorded on the post)." % (part, why_split), "split": True}
    title = re.sub(r"^\s*%s\b[\s:.-]*" % re.escape(part), "", title or part, flags=re.I) or title or part
    base = "%s-%s" % (_slug(part, 12), _slug(title))
    pid, n = base, 2
    while pid in posts:
        pid = "%s-%d" % (base, n)
        n += 1
    doc = {"title": title, "part": part, "train": train, "column": "info", "status": "planned", "body": task or "", "task": task or "",
           "from": "orchestrator", "planned_by": "orchestrator", "created_at": _now_z()}
    if task_pieces(task):
        doc["subtasks"] = task_pieces(task)
    if parent:
        doc["parent"] = parent
    if why_split and keep_whole:
        doc["kept_whole"] = str(keep_whole)
    if keep_train:
        doc["kept_on_train"] = str(keep_train)
    if sources:
        doc["sources"] = list(sources)
    if review is False:
        doc["review_auto"] = False
    if damage:                                     # R6: what was lost or put at risk, and who found it
        doc["damage"] = str(damage)
    if after:
        doc["after"] = [str(x) for x in after]
    if kind in ("bug", "feature"):
        doc["kind"] = kind
    if not att:                                    # CL54: where to work, in the post and so in its prompt
        here = _git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if here and here != "HEAD":
            doc["work_branch"] = here
            gh = _github_url()
            if gh:
                doc["repo"] = gh.replace("https://github.com/", "")
    write = None
    if att:
        fp = os.path.join(os.path.abspath(_art_dir()), "out", "repos", att, "posts", pid + ".json")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        write_doc(fp, doc)
        write = {"action": "set", "url": artifact_url() or str(CONFIG.get("board_url") or ""),
                 "collection": "repos/%s/posts" % att, "doc_id": pid, "file_path": fp}
    elif not _board_write("set", "posts", pid, doc):
        return {"error": "the board did not take step %s (is the board server running?)" % part}
    # CL54: the task is written once, on the post; `kit.py launch` generates docs/prompts/<pid>.md from it and commits it
    rel, made = "docs/prompts/%s.md" % pid, False
    rec = train_record(posts, train)
    res = {"post": pid, "adopted": False, "pieces": len(doc.get("subtasks") or []), "prompt": rel, "prompt_written": made,
           "train_goal": (rec or {}).get("goal") or "(none written: `kit.py trains new`)",
           "launch": _launch_line(part, rel, model, cli=cli) if rel else None}
    # R6: the new step against every open one: the whole queue re-ranked, and the running steps it would undo told to pause
    ranked = dict(posts, **{pid: doc})
    res["queue"] = queue_lines(ranked, limit=12)
    pauses = pause_messages(ranked, pid)
    if pauses:
        res["pause"] = pauses
        rfmt = _rule("reply_format", cli)
        pause_how = {
            "claude_remote": "send_message (claude-code-remote) to its session_id",
            "claude_cli": "SendMessage to its session_id",
            "agy": "send_message to its conversation",
            "none": "post a comment on its step (no inter-session message on this platform)",
        }[rfmt]
        res["next_pause"] = "for each `pause` entry: %s with its message; it pauses at its next save point" % pause_how
    if att:
        _record_printed([write], "kit.py step new")
        res.update({"board": att, "artifact_write": write,
                    "next": "send the post: ArtifactData %s (no other way: a hand-written step post is refused)"
                            % json.dumps(write, ensure_ascii=False)})
    return res


def step_find(part, train=None, db_path=None):
    """The post of a step by its part (and train): how a task finds its step again without the session that made it."""
    part = str(part or "").strip().lower()
    hits = [x for x in [(pid, q) for pid, q in _all_posts(db_path).items() if not pid.startswith("_") and not q.get("removed")]
            if part and str(x[1].get("train") or "").strip() and str(x[1].get("part") or "").strip().lower() == part
            and (not train or _norm_train(x[1].get("train")) == _norm_train(train))]
    if len(hits) != 1:
        return {"error": "%d steps with part %s%s" % (len(hits), part, (" on train %s" % train) if train else "")}
    return {"post": hits[0][0], "train": _norm_train(hits[0][1]["train"])}


def step_start(pid):
    return {"post": pid, "ok": _board_write("update", "posts", pid, {"status": "in progress"})}


def step_drop(pid, db_path=None):
    p = _all_posts(db_path).get(pid) or {}
    if str(p.get("status") or "") != "planned" or p.get("session_id"):
        return {"post": pid, "ok": False, "why": "kept: a part already started it"}
    return {"post": pid, "ok": _board_write("update", "posts", pid, {"removed": True})}


# ---------------------------------------------------------------- launch (CL13): a launched part is always on the board
# ---------------------------------------------------------------- CL53: a session starts on the newest code from GitHub
STALE_BRANCH_DAYS = 2      # ~/harmonize sat two days on a merged branch while its trunk moved (11-22 to 11-24)


def checkout_fresh(root=None, trunk=None, fetch=True, move=True, days=None, now=None):
    """Fetch origin; say in one line when this checkout is behind its trunk, or on a stale branch; move it forward by
    itself only when that loses nothing: clean, on the trunk itself, with no commit of its own (CR-cl53). Returns
    (lines, blocked): `blocked` is True when it stays behind and the session has something to do first."""
    root = root or ROOT
    k = _kit_config().get("kit") or {}
    trunk = trunk or str(k.get("main_branch") or "main")
    days = STALE_BRANCH_DAYS if days is None else days
    now = now or time.time()

    def g(*a, **kw):
        try:
            r = subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=kw.get("timeout", 30))
            return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)

    if g("rev-parse", "--is-inside-work-tree")[1] != "true" or g("remote", "get-url", "origin")[0]:
        return [], False
    up = "refs/remotes/origin/" + trunk
    if fetch:
        rc, _, err = g("fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:%s" % (trunk, up), timeout=25)
        if rc:
            return ["CHECKOUT: could not fetch origin (%s): this checkout may be on old code." % (err.splitlines() or ["?"])[-1][:160]], False
    if g("rev-parse", "-q", "--verify", up)[0]:
        return [], False
    branch = g("symbolic-ref", "-q", "--short", "HEAD")[1]
    behind = int(g("rev-list", "--count", "HEAD.." + up)[1] or 0)
    own = int(g("rev-list", "--count", up + "..HEAD")[1] or 0)
    if not behind:
        return [], False
    newest = g("log", "-1", "--format=%h %cs %s", up)[1][:90]
    what = "%d newer commit%s on origin/%s (newest %s)" % (behind, "" if behind == 1 else "s", trunk, newest)
    dirty = bool(g("status", "--porcelain", "--untracked-files=no")[1]) or any(
        os.path.exists(os.path.join(g("rev-parse", "--absolute-git-dir")[1] or os.path.join(root, ".git"), f)) for f in ("MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply"))
    here = "branch %s" % branch if branch else "a detached HEAD"
    if not own:                                   # the trunk itself, or a branch merged into it: nothing of its own
        if dirty:
            return ["CHECKOUT: %s is behind: %s. Not moved: it has uncommitted changes; commit or stash them, then "
                    "`git checkout %s && git merge --ff-only origin/%s`." % (here, what, trunk, trunk)], True
        if branch != trunk:
            # CR-cl53: no commit of its own is not "merged": a part's new worktree branch or a review's detached HEAD
            # looks the same, and switching it would put its work on the trunk. Only the trunk moves by itself.
            return ["CHECKOUT: %s is behind: %s; it has no commit of its own: `git checkout %s && git merge --ff-only "
                    "origin/%s` if it is done (not switched: it may be a new branch)." % (here, what, trunk, trunk)], False
        if not move or "/.claude/worktrees/" in os.path.abspath(root).replace(os.sep, "/") + "/":
            return ["CHECKOUT: %s is behind: %s." % (here, what)], True
        rc, _, err = g("merge", "-q", "--ff-only", up)
        if rc:
            return ["CHECKOUT: %s is behind (%s) and could not be moved (`git merge --ff-only origin/%s`: %s): do it by "
                    "hand." % (here, what, trunk, (err.splitlines() or ["?"])[-1][:160])], True
        return ["CHECKOUT: moved %s forward: %s." % (trunk, what)], False
    last = int(g("log", "-1", "--format=%ct", "HEAD")[1] or now)
    trunk_last = int(g("log", "-1", "--format=%ct", up)[1] or 0)
    age = int((now - last) // 86400)
    if branch == trunk:
        return ["CHECKOUT: %s has %d commit%s of its own not on origin, and is behind: %s. Not moved: `git pull --no-rebase`, "
                "then push." % (trunk, own, "" if own == 1 else "s", what)], True
    if age >= days and trunk_last > last:
        return ["CHECKOUT: %s is stale: no commit for %d days while %s moved: %s. Not moved (%d commit%s of its own%s): merge "
                "the trunk (`git merge --no-edit origin/%s`) or finish it, before you work here." % (
                    here, age, trunk, what, own, "" if own == 1 else "s", ", uncommitted changes" if dirty else "", trunk)], True
    return ["CHECKOUT: %s is behind its trunk: %s; its %d commit%s are its own: merge `origin/%s` before you build on what "
            "changed there." % (here, what, own, "" if own == 1 else "s", trunk)], False


def checkout_fresh_once(session=None, root=None, now=None):
    """Once a session (the SessionStart hook or its first resume, whichever comes first): fetch and move. Later calls
    look again without fetching and repeat only a line that still asks something. A web clone is fresh: nothing."""
    if not _rule("checkout_refetch") or str(os.environ.get("KIT_REMOTE") or os.environ.get("AGY_REMOTE") or "").lower() in ("1", "true", "yes"):
        return [], False
    root = root or ROOT
    now = now or time.time()
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(session or os.environ.get("CLAUDE_CODE_SESSION_ID")
                                                  or os.environ.get("KIT_SESSION_ID") or ""))
    d = os.path.join(root, ".kit", "run", "fresh")
    mark = os.path.join(d, (sid or "_nosession") + ".done")
    try:
        first = not os.path.exists(mark) or (not sid and now - os.path.getmtime(mark) > 600)
    except OSError:
        first = True
    if not first:
        lines, blocked = checkout_fresh(root=root, fetch=False, move=False, now=now)
        return (lines, blocked) if blocked else ([], False)
    lines, blocked = checkout_fresh(root=root, now=now)
    try:
        os.makedirs(d, exist_ok=True)
        with open(mark, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return lines, blocked


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
    if post.get("runner"):
        row["runner"] = post["runner"]
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


def _prepare_agy_branch(branch, base, reset=False, take=False):
    """R7: prepare or adopt `branch` for an agy subagent (`Workspace=\"share\"`) without switching the orchestrator's
    working tree or locking `branch` in a `.claude/worktrees/` checkout. Returns (status, error)."""
    if _git_run("remote", "get-url", "origin").returncode == 0:
        _git_run("fetch", "-q", "origin", branch, timeout=20)
    has_local = _git_run("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0
    has_remote = _git_run("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch).returncode == 0
    wt = _worktree_for(branch)
    if reset:
        if wt:
            dirty = [ln for ln in _git_run("status", "--porcelain", cwd=wt).stdout.splitlines()
                     if not ln[3:].strip().startswith(".kit/run/")]
            if dirty:
                return None, "worktree %s (branch %s) has uncommitted work: commit or keep it, then launch again" % (wt, branch)
            r = _git_run("reset", "-q", "--hard", base, cwd=wt)
        else:
            r = _git_run("branch", "-f", branch, base)
        return ("reset", None) if r.returncode == 0 else (None, (r.stderr or r.stdout).strip() or "git branch reset failed")
    if not has_local:
        start = ("origin/" + branch) if has_remote else base
        r = _git_run("branch", branch, start)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout).strip() or "git branch failed"
        return ("adopted_remote" if has_remote else "created"), None
    if has_remote and not wt:
        loc_sha = _git_run("rev-parse", "refs/heads/" + branch).stdout.strip()
        rem_sha = _git_run("rev-parse", "refs/remotes/origin/" + branch).stdout.strip()
        if loc_sha != rem_sha and _git_run("merge-base", "--is-ancestor", "refs/heads/" + branch, "refs/remotes/origin/" + branch).returncode == 0:
            r = _git_run("branch", "-f", branch, "refs/remotes/origin/" + branch)
            if r.returncode != 0:
                return None, (r.stderr or r.stdout).strip() or "git branch fast-forward failed"
            return "adopted_remote", None
    own = _git_run("rev-list", "--count", "%s..%s" % (base, branch)).stdout.strip()
    if own in ("", "0") and not wt:
        r = _git_run("branch", "-f", branch, base)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout).strip() or "git branch update failed"
        return "prepared", None
    return "adopted", None


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


def _prompt_branch(clone, pid, g):
    """CL38: the prompt goes on the clone's trunk when its owner gave the standing permission to push it (no
    `part-<post>` branch left behind, 40 of them on anlonlt/board by CL38); else on `part-<post>` as before. Never
    when the clone's local trunk holds commits origin does not have (checkout -B would drop them)."""
    k = (_read_json(os.path.join(clone, ".kit", "config.json")) or {}).get("kit") or {}
    trunk = str(k.get("main_branch") or "main")
    if k.get("trunk_push"):
        g("fetch", "-q", "origin", trunk)
        local = g("rev-parse", "--verify", "--quiet", "refs/heads/" + trunk).returncode == 0
        if g("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + trunk).returncode == 0 and (
                not local or g("merge-base", "--is-ancestor", trunk, "origin/" + trunk).returncode == 0):
            return trunk
    return "part-%s" % _slug(pid, 60)


def _launch_attached(pid, b, post, session_id=None, model=None, web=None, reset=False, cli=None, take=False):
    """Launch a step of an attached board from its own clone (the kit way, no hand step): the prompt this project
    wrote goes on a `part-<post>` branch of that clone (committed, pushed), then that clone's kit.py launch runs with
    the post, no seeded board and no board-mirror push (KIT_ATTACHED_CLONE), and the consuming projects to test on."""
    out = {"ok": False, "post": pid, "board": b["id"], "part": str(post.get("part") or pid)}
    clone = b.get("clone")
    if not clone or not os.path.isfile(os.path.join(clone, ".kit", "kit.py")):
        gh = b.get("github") or "<owner>/%s" % b["id"]
        return dict(out, error="step %s is on board %s, which has no clone here with the kit: `git clone https://github.com/%s "
                    "%s` (read-only is enough, nothing to attach: a launch whose push is refused carries the prompt in its start message, "
                    "CL40), then launch again" % (pid, b["id"], gh, os.path.join(os.path.dirname(ROOT), gh.split("/")[-1])))
    rel = "docs/prompts/%s.md" % pid
    mine = os.path.join(ROOT, rel)

    def g(*a, **kw):
        return subprocess.run(["git", "-C", clone] + list(a), capture_output=True, text=True, timeout=kw.get("timeout", 120))
    branch = _prompt_branch(clone, pid, g)
    if not session_id:
        has_task = bool(str(post.get("task") or "").strip()) or not os.path.isfile(mine)
        text = _render_step_prompt(pid, post, cli=cli, path=mine if os.path.isfile(mine) else None) if has_task else None
        if text is not None and (not os.path.isfile(mine) or _read_text(mine) != text):   # CL54: from the post as it is now
            os.makedirs(os.path.dirname(mine), exist_ok=True)
            with open(mine, "w", encoding="utf-8") as fh:
                fh.write(text)
        why = circle_gate(b.get("posts") or {}, pid, post, mine if os.path.isfile(mine) else os.path.join(clone, rel))
        if why:
            return dict(out, error=why)
    restore = []      # the branch the clone was on: it goes back there whatever happens
    try:
        return _launch_in_clone(pid, b, post, clone, rel, mine, branch, g, out, restore, session_id, model, web, reset, take, cli=cli)
    finally:
        if restore:
            g("checkout", "-q", restore[0])


# CL40: a push refused for want of access (the session's repository set, GitHub 403, no credentials), not a network fault
_PUSH_DENIED = re.compile(r"authorized repository set|permission to \S+ denied|permission denied|"
                          r"(?:returned error|http|status)\W{0,3}403\b|\b403 forbidden|not authorized|"
                          r"authentication failed|could not read username|access denied|write access", re.I)


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _inline_prompt(text, rel, body):
    """CL40: the create_session prompt that carries the prompt's full text (the board could not be pushed): the new
    session commits it to `rel` on its branch first, so the step still starts from git."""
    m = re.match(r"Your prompt is %s: .*?do it\.\s*" % re.escape(rel), text)
    rest = text[m.end():].strip() if m else ""
    return ("Your prompt follows; first commit it to %s on your branch (it could not be pushed to the board), then read "
            "it and do it.%s\n\n<prompt path=\"%s\">\n%s\n</prompt>" % (rel, (" " + rest) if rest else "", rel, body.rstrip()))


def _launch_in_clone(pid, b, post, clone, rel, mine, branch, g, out, restore, session_id, model, web, reset, take=False, cli=None):
    inline = None

    def stale():   # CL54: the clone's prompt is missing or differs from the one this post makes now
        there = os.path.join(clone, rel)
        return os.path.isfile(mine) and (not os.path.isfile(there) or _read_text(there) != _read_text(mine))
    if not session_id and (not os.path.isfile(os.path.join(clone, rel)) or stale()):
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
        made = stale()
        if made:
            os.makedirs(os.path.dirname(os.path.join(clone, rel)), exist_ok=True)
            shutil.copyfile(mine, os.path.join(clone, rel))
            g("add", rel)
            r = g("commit", "-q", "-m", "prompt(%s): generated from post %s" % (out["part"], pid))
            if r.returncode != 0:
                return dict(out, error="could not commit the prompt in %s: %s" % (clone, (r.stderr or r.stdout).strip()[:300]))
        r = g("push", "-q", "-u", "origin", branch, timeout=180)
        if r.returncode != 0:
            why = (r.stderr or r.stdout).strip()
            if not _PUSH_DENIED.search(why):
                if made:          # O57: a failed push never leaves the clone's branch ahead of origin
                    g("reset", "-q", "--hard", "HEAD~1")
                return dict(out, error="could not push %s of %s (the new session clones it): %s" % (
                    branch, b.get("github") or clone, why[:300]))
            # CL40: a read-only clone (a public board, no push access): the prompt rides in the start message
            inline = {"denied": why[:300], "made": made, "text": _read_text(mine),
                      # O57: the session starts from origin/<branch> when it exists (it may hold earlier work), else the trunk
                      "trunk": branch if has else (trunk.split("/", 1)[-1] if trunk.startswith("origin/") else "main")}
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
    if take:
        cmd.append("--take")
    if cli:
        cmd += ["--cli", cli]
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
    finally:
        if inline and inline["made"]:   # the prompt commit could not leave this clone: never keep it on a branch origin lacks
            g("reset", "-q", "--hard", "HEAD~1")
    try:
        res = json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        return dict(out, error="the launch in %s answered no result: %s" % (clone, (r.stderr or r.stdout).strip()[-400:]))
    res["attached"] = {"id": b["id"], "clone": clone}
    for k in ("batches", "need_pin", "push_error", "board", "dropped", "files"):
        res.pop(k, None)              # the clone's own copy was never loaded: its push text would only mislead
    if not res.get("ok"):
        return res
    if inline:
        res["prompt_inline"] = {"reason": inline["denied"]}
        res.pop("warning", None)      # "branch not pushed": expected, the session starts from the trunk
        cs = res.get("create_session")
        if isinstance(cs, dict):
            cs["source_revision"] = inline["trunk"]
            cs["prompt"] = _inline_prompt(cs.get("prompt") or "", rel, inline["text"])
            launch_mark(pid, cs["prompt"], inline=rel)      # the orchestrator guard lets out this very text and no other
    # the post on the Artifact (repos/<id>/posts) and this copy of it: one ArtifactData update, pinned to the version
    # the last pull saw, so the board shows the launch (the orchestrator guard lets this file through)
    fields = {"status": "in progress", "session_id": res.get("session_id"), "branch": res.get("branch"),
              "updated_at": _now_z()}
    if res.get("runner"):
        fields["runner"] = res["runner"]
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
    _record_printed([w], "kit.py step")
    try:
        url, token = local_board_url(), _resolve_board_token(None)
        cur = _board_request(url, "doc/posts/%s?repo=%s" % (urllib.parse.quote(pid, safe=""), urllib.parse.quote(b["id"])), token=token)
        if cur.get("exists"):
            _board_request(url, "doc/posts/%s?repo=%s" % (urllib.parse.quote(pid, safe=""), urllib.parse.quote(b["id"])),
                           {"data": dict(cur["data"], **fields)}, token=token, method="PUT")
    except Exception:
        pass
    return res


def _launch_posts(post):
    """The posts the circle check reads: the board's copy here (none: nothing to show, the launch goes on)."""
    try:
        return _all_posts()
    except Exception:
        return {}


# The runners `kit.py launch` starts itself; any other (lmarena, claude-chrome, owner...) takes the step as its card.
LAUNCH_RUNNERS = ("", "agy", "claude", "claude-cli", "claude-web", "cli", "web", "local", "computer", "cloud", "scheduled")
CLAIM_MIN = 30           # a session that gave no sign of life for this long is quiet (as in resume)


def _my_person(url=None, token=None):
    """(the person this session works for, the people of this board): the board's own answer to this token, else
    $KIT_PERSON, else the owner."""
    me = {}
    try:
        me = _board_request(url or local_board_url(), "me", token=_resolve_board_token(token)) or {}
    except Exception:
        pass
    people = {"owner"} | {str(c.get("id")) for c in me.get("collaborators") or [] if isinstance(c, dict) and c.get("id")}
    return str(os.environ.get("KIT_PERSON") or me.get("id") or "owner"), people


def _mins_since(stamp):
    last = _utc(str(stamp or ""))
    if not last:
        return None
    return max(0, int((datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                       .replace(tzinfo=timezone.utc)).total_seconds() // 60))


def _saved_mins(branch):
    """Minutes since a session last saved its work on wip/<branch> at GitHub (None: never, or not reachable): the sign
    of life every platform shares, newer than this copy of the board."""
    # CL55: a part's saves are checkpoint commits on its branch itself, another session's on wip/<branch>: the newest counts
    best = None
    for src in ("wip/" + branch, branch):
        ref = "refs/remotes/origin/" + src
        try:
            _git_run("fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:%s" % (src, ref), timeout=20)
            t = _git_run("log", "-1", "--format=%ct", ref, timeout=10)
            if t.returncode == 0 and t.stdout.strip() and (src != branch or _git_run("log", "-1", "--format=%s", ref).stdout.startswith("checkpoint(")):
                m = max(0, int((time.time() - int(t.stdout.strip())) // 60))
                best = m if best is None else min(best, m)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return best


def _public_board_base(url):
    """The board's address from outside this computer: $KIT_FUNNEL_URL, `kit.funnel_url`, the Tailscale funnel that
    points at this board's port, else the board's own address ('' when none)."""
    k = _kit_config().get("kit") or {}
    got = str(os.environ.get("KIT_FUNNEL_URL") or k.get("funnel_url") or "").rstrip("/")
    if got:
        return got
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "notify.py"), os.path.join(here, "..", "kit", "notify.py")):
        if os.path.isfile(path) and shutil.which("tailscale"):
            try:
                spec = importlib.util.spec_from_file_location("kit_notify_link", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                port = urllib.parse.urlsplit(url).port
                ft = mod.funnel_target(mod.funnel_status(max_age=0), port)
                if ft:
                    return ft[0]
            except Exception:
                pass
            break
    return str(url or "").rstrip("/")


def outside_link(pid, url=None, token=None, hours=72):
    """CL56: the one link an outside AI (a runner card: lmarena, antigravity-web, claude-chrome...) gets for a step:
    the board's invite scoped to its post, `/_invite/<token>.md`: how to attach files to the post (its drop box) and
    write its result come first. {"link": ...} or {"error": ...}."""
    url = url or local_board_url()
    try:
        r = _board_request(url, "shares", {"post_id": pid, "ttl_hours": hours, "allow_write": True, "max_submissions": 20},
                           token=_resolve_board_token(token))
    except Exception as e:
        return {"error": "no link: the board at %s did not answer (%s)" % (url, str(e)[:120])}
    base = _public_board_base(url)
    out = {"link": base + (r.get("invite") or "/_invite/%s.md" % r.get("token")), "expires_at": r.get("expires_at")}
    if re.match(r"^https?://(127\.|localhost|0\.0\.0\.0|\[::1\])", base):
        out["warning"] = "this address is this computer's own: start the funnel (or set kit.funnel_url) so the runner can reach it"
    return out


def step_holder(pid, p, me=None, people=(), alive=None, saved=None, session=None):
    """Who holds this step besides the one asking, as text ('' : nobody). From what the post already carries: `for` and
    `working_by` (people), `runner` (who executes it), `session_id` (the session on it)."""
    runner = str(p.get("runner") or "").strip().lower()
    if runner not in LAUNCH_RUNNERS:
        return "it is %s's: its card, not a session to launch (`runner`)" % runner
    person, busy = post_person(p), str(p.get("working_by") or "").strip()
    if me:
        if busy and busy != me:
            return "@%s is working on it (`working_by`)" % busy
        if person and person != me and person in people:          # a role (designer, reviewer) holds nothing
            return "it is @%s's (`for`)" % person
    sess = str(p.get("session_id") or "").strip()
    if session and sess == session:
        return ""
    quiet = [m for m in (_mins_since(p.get("updated_at")), _mins_since(p.get("launched_at")), saved) if m is not None]
    quiet = min(quiet) if quiet else None
    if step_state(p) != "running" or not sess:
        return ("a session saved work on its branch %d min ago, though this copy of the board shows none on it: "
                "update the board first" % saved) if saved is not None and saved < CLAIM_MIN and step_state(p) == "idle" else ""
    if sess.startswith("pending"):
        m = _mins_since(p.get("launched_at"))
        return ("its launch was prepared %d min ago: finish it with `launch %s --session-id <the id create_session returned>`"
                % (m, pid)) if m is not None and m < 10 else ""
    if alive is not None and sess in alive:
        return "session %s is running on this computer" % sess
    if saved is None or saved >= CLAIM_MIN:
        if alive is not None and not sess.startswith("session_") and _launch_traces(pid, p):
            return ""                        # it started on this computer and no longer runs: continue or start again
    seen = "no sign of life dated" if quiet is None else "last sign of life %d min ago" % quiet
    return "session %s is on it (%s)%s" % (sess, seen, ": check it with get_session" if sess.startswith("session_") else "")


def _continue_worktree(branch, base):
    """CL17: a worktree on the part's own branch, as it was left (its commits, and its uncommitted files when the old
    worktree is still there): never reset. A branch only on origin is taken from there. No branch: a fresh one."""
    path = _worktree_for(branch)
    if path and os.path.isdir(path):
        return path, None
    _git_run("worktree", "prune")
    path = os.path.join(ROOT, ".claude", "worktrees", branch)
    if _git_run("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0:
        r = _git_run("worktree", "add", "-q", path, branch)
    else:
        _git_run("fetch", "-q", "origin", branch, timeout=120)
        if _git_run("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch).returncode != 0:
            return _make_worktree(branch, base)
        r = _git_run("worktree", "add", "-q", "-b", branch, path, "origin/" + branch)
    return (path, None) if r.returncode == 0 else (None, r.stderr.strip() or "git worktree add failed")


def checklist(p):
    """CL17: a part's checklist is its post's pieces (`kit.py sub`): {done, of, doing, next} or None (no pieces)."""
    rows = [x for x in pieces_of(p) if x.get("status") != "dropped"]
    if not rows:
        return None
    doing = [x for x in rows if x.get("status") == "doing"]
    todo = [x for x in rows if x.get("status") not in ("done", "doing")]
    return {"done": sum(x.get("status") == "done" for x in rows), "of": len(rows),
            "doing": ("%d. %s" % (doing[0]["n"], doing[0]["title"])) if doing else "",
            "next": ("%d. %s" % (todo[0]["n"], todo[0]["title"])) if todo else ""}


def checklist_text(p):
    c = checklist(p)
    if not c:
        return ""
    return "checklist %d/%d done%s%s" % (c["done"], c["of"], (" · doing: %s" % _short(c["doing"], 70)) if c["doing"] else "",
                                          (" · next: %s" % _short(c["next"], 70)) if c["next"] else "")


def continue_text(p, branch):
    """What a relaunched part is told after its prompt line (CL17)."""
    ck = p.get("checkpoint") if isinstance(p.get("checkpoint"), dict) else {}
    c = checklist(p)
    return (" You continue a part that stopped (crash or closed session): continue from the checklist in your post "
            "(`kit.py sub`%s); your branch `%s` has your work%s. Do not start again." % (
                ("; next: %s" % (c["doing"] or c["next"])) if c and (c["doing"] or c["next"]) else "", branch,
                (" (last checkpoint %s, %s)" % (ck.get("sha"), str(ck.get("at") or "")[:16].replace("T", " "))) if ck.get("sha") else ""))


WAIT_RE = re.compile(r"\b(wait(s|ing)? (on|for) (the )?owner|asks? the owner|held for (the )?owner|needs the owner)\b", re.I)


def waits_on_owner(pid, p, posts=None):
    """CL17: why this step is waiting on the owner ('' : it is not), so a crashed part is relaunched without asking only
    when nothing of his is pending: its column (question / todo), its status, or an open question post naming it."""
    if step_state(p) == "you":
        return "its column is %s" % p.get("column")
    st = str(p.get("status") or "")
    if WAIT_RE.search(st) or p.get("waits_on_owner"):
        return "its status: %s" % _short(st or "waits_on_owner", 60)
    part = str(p.get("part") or "")
    for qid, q in (posts or {}).items():
        if qid == pid or not isinstance(q, dict) or q.get("column") != "question" or step_state(q) != "you" or q.get("removed"):
            continue
        if q.get("parent") == pid or q.get("post") == pid or (len(part) >= 3 and re.search(
                r"(?<![\w-])%s(?![\w])" % re.escape(part), str(q.get("title") or ""))):
            return "question `%s` is open" % qid
    return ""


def _transcript_of(sess):
    """A Claude Code transcript of this session id on this machine, or ''."""
    if not sess or not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", sess):
        return ""
    hits = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", sess + ".jsonl"))
    return hits[0] if hits else ""


def relaunch_lines(pid, p, posts=None, unknown=False):
    """CL17: under a step whose session is not running: its last checkpoint, its checklist, and the relaunch line (or why
    it waits on the owner). `unknown`: a web session whose life only get_session knows."""
    out = []
    ck = p.get("checkpoint") if isinstance(p.get("checkpoint"), dict) else None
    if ck and ck.get("sha"):
        out.append("  last checkpoint `%s` on `%s` (%s, %s)" % (ck["sha"], ck.get("branch") or p.get("branch") or "?",
                   str(ck.get("at") or "")[:16].replace("T", " "), "pushed" if ck.get("pushed") else "NOT pushed"))
    cl = checklist_text(p)
    out.append("  " + (cl or "no checklist on its post (no pieces): the relaunched part reads its branch's commits"))
    wait = waits_on_owner(pid, p, posts)
    if wait:
        out.append("  waits on the owner (%s): do not relaunch; his answer first" % wait)
        return out
    out.append("  relaunch%s: `python3 -I .kit/kit.py launch %s --continue`" % (
        " (once its session is confirmed ended: get_session on the web)" if unknown else "", pid))
    tr = _transcript_of(str(p.get("session_id") or ""))
    if tr:
        out.append("  or here, same machine, its transcript still exists: `claude --resume %s`" % p["session_id"])
    return out


def launch(pid, url=None, token=None, web=None, session_id=None, model=None, reset=False, gate=True, cli=None, post_file=None,
           take=False, anyway=None, cont=False):
    """`kit.py launch <post>`: the one way a part starts (CL13, CL17, R7). Writes status in progress, session_id, runner,
    launched_at and branch on its post and marks its Trains row running (the local copy; with an attached Artifact
    board, the batch that carries both there), then starts it: CLI, `claude --bg` inside a worktree made from the
    current branch; agy, checks device capacity, prepares or adopts the step branch, and prints `invoke_subagent`;
    web, the create_session call to make (a script cannot call it), and `launch <post> --session-id <id>` once it
    answered. Refuses a prompt that is not committed."""
    url = url or local_board_url()
    token = _resolve_board_token(token)
    pl = _platform(cli)
    lmode = _rule("launcher", pl)
    run = _launcher(cli)
    if web is True or (web is None and lmode == "create_session"):
        mode = "create_session"
    elif web is False and cli is None:
        mode = "claude_bg" if (run is not None or lmode != "self") else "self"
    elif lmode == "invoke_subagent":
        mode = "invoke_subagent"
    elif lmode == "claude_bg":
        mode = "claude_bg"
    else:
        mode = "self"
    web = (mode == "create_session")
    runner = {"create_session": "claude-web", "invoke_subagent": "agy", "claude_bg": "claude-cli"}.get(mode)
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
            return _launch_attached(pid, b, ap, session_id=session_id, model=model, web=web, reset=reset, cli=cli, take=take)
        return {"ok": False, "post": pid, "error": "no post %s on this board%s, nor on an attached board (%s), nor in the "
                "Artifact pull (.kit/run/artifact/in/repos/<id>/posts): run `python3 -I .kit/kit.py artifact pull`, make its "
                "calls, then launch again" % (pid, (" (no local board at %s)" % url) if d.get("_error") else "",
                                              ", ".join(_attached_ids()) or "none attached")}
    if not runner and session_id:
        runner = "claude-web" if str(session_id).startswith("session_") else str(post.get("runner") or "")
    part = str(post.get("part") or pid)
    rel = "docs/prompts/%s.md" % pid
    branch = str(post.get("branch") or "").strip() if (session_id or take or cont) else ""
    branch = branch or _slug(part, 24)
    c = _kit_config()
    name = "%s-%s" % (_slug(c.get("project") or "project"), _slug(part, 24))
    out = {"ok": True, "post": pid, "part": part, "branch": branch, "web": web, "prompt": rel}
    if runner:
        out["runner"] = runner
    base = _git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    text = _launch_text(rel)
    me, people = _my_person(url, token)
    alive = _alive_sessions() if (mode == "claude_bg" or _rule("alive_check", pl) == "claude_agents") else None
    if cont and not session_id:                     # CL17: a crashed part continues on its branch, from its post's checklist
        old = str(post.get("session_id") or "")
        if old and old in (alive or ()):
            return dict(out, ok=False, error="%s's session %s still runs: nothing to continue" % (pid, old))
        wait = waits_on_owner(pid, post)
        if wait:
            return dict(out, ok=False, error="%s waits on the owner (%s): answer that, then continue it" % (pid, wait))
        text += continue_text(post, branch)
        out["continued"] = True
    if not session_id and not take and not cont:
        held = step_holder(pid, post, me=me, people=people, alive=alive, saved=_saved_mins(branch))
        if held:
            post_runner = str(post.get("runner") or "").strip().lower()
            if post_runner not in LAUNCH_RUNNERS and not isinstance(given, dict):
                lk = outside_link(pid, url, token)          # CL56: its card's runner gets one link, the post's invite
                if lk.get("link"):
                    return dict(out, ok=False, link_for_ai=lk["link"], error="%s is %s's card: give %s this one link (its post, "
                                "its drop box for files, its result): %s%s" % (pid, post_runner, post_runner, lk["link"],
                                (" (%s)" % lk["warning"]) if lk.get("warning") else ""))
            return dict(out, ok=False, error="%s is taken: %s. If that session ended or its person agrees, "
                        "`kit.py launch %s --take`" % (pid, held, pid))
    cons = consumers(post.get("consumers"))
    if cons:     # CL32 / R5 / R4: consumer note follows PLATFORM_RULES['real_project_run']
        rp_who = _rule("real_project_run", pl)
        if rp_who == "orchestrator":
            # CL38: a launched web session's classifier refuses add_repo unless the owner types it; parts never attach.
            text += (" Never attach a repo: the real-project run on %s is the orchestrator's (done says pending until then)."
                     % ", ".join(c["github"] or c["name"] for c in cons))
        out["consumers"] = [c["github"] or c["name"] for c in cons]
        out["real_project_run"] = rp_who
    mark = os.path.join(ROOT, ".kit", "run", "launch", pid + ".json")
    marked = os.path.isfile(mark)
    if session_id:                                   # the second half of a web launch: create_session answered
        sess = session_id
        launch_mark(pid, None)
    else:
        lp = _launch_posts(post)
        if not isinstance(given, dict) and not os.environ.get("KIT_ATTACHED_CLONE"):
            # CL54: the prompt is generated from the post as it is now and committed (a new commit when the post changed)
            sync = sync_step_prompt(pid, post, cli=cli)
            if sync.get("error"):
                return dict(out, ok=False, error=sync["error"])
            out["prompt_sync"] = sync
        why = _prompt_problem(rel) or circle_gate(lp, pid, post, os.path.join(ROOT, rel))
        if why:
            return dict(out, ok=False, error=why)
        # R6: launched from the top of the queue; out of order only with the reason recorded
        late = None if (post.get("launched_at") or take or cont) else order_problem(dict(lp, **{pid: post}), pid)
        if late and not anyway:
            return dict(out, ok=False, error="out of order: %s. (Launch it anyway: `kit.py launch %s --anyway \"<why>\"`, "
                                             "recorded on its post.)" % (late, pid), queue=True)
        if late:
            out["out_of_order"] = late
        if mode == "create_session":
            if _git_run("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + base).returncode != 0 and base != "HEAD":
                out["warning"] = "branch %s is not pushed: push it first, the new session clones it from GitHub" % base
            src = base
            if cont and _git_run("rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch).returncode == 0:
                src = branch                           # CL17: the new session clones the part's own branch, its work
            prompt_text, inline = text, None
            if (out.get("prompt_sync") or {}).get("commit") and base != "HEAD":
                # CL54: the new session clones `src` from GitHub: the prompt commit goes there first (on `base`); a
                # continuation from the part's own branch gets it in its start message
                pr = _git_run("push", "-q", "origin", "HEAD:refs/heads/%s" % base, timeout=180) if src == base else None
                if pr is None or pr.returncode != 0:      # CL40: the prompt rides in the start message, committed by the session
                    inline = ((pr.stderr or pr.stdout).strip()[:300] or "push refused") if pr is not None else \
                        "the session continues from branch %s, which lacks the regenerated prompt" % branch
                    prompt_text = _inline_prompt(text, rel, _read_text(os.path.join(ROOT, rel)))
                    out["prompt_inline"] = {"reason": inline}
                else:
                    out.pop("warning", None)
            out["create_session"] = {"title": ("%s %s" % (part, str(post.get("title") or "")[:60])).strip(),
                                     "source_url": _github_url(), "source_revision": src, "outcome_branch": branch,
                                     "prompt": prompt_text}
            sess = "pending: create_session (%s)" % name
            if inline:
                launch_mark(pid, prompt_text, inline=rel)
            else:
                launch_mark(pid, text)                 # CL30: the orchestrator guard lets this create_session out
        elif mode == "invoke_subagent":
            if not shutil.which("agy"):                # CL26 / R7: never an OSError on an absent binary
                return dict(out, ok=False, error=(NO_RUNTIME.replace("neither `claude` nor `agy`", "no `agy`") +
                            "; an agy orchestrator starts a part with: %s" % _launch_line(part, rel, model, cli="agy")))
            if gate and _rule("device_cap_gate", pl) and _device() is not None:
                try:
                    ok, why = _device().gate(_device().survey())
                except Exception:
                    ok, why = True, ""
                if not ok:
                    return dict(out, ok=False, error="device cap: " + why)
            bstatus, why = _prepare_agy_branch(branch, base, reset=reset, take=take or cont)
            if why:
                return dict(out, ok=False, error=why)
            out["branch_status"] = bstatus
            k = c.get("kit") or {}
            agy_model = str(model or ((k.get("roles") or {}).get("part") or {}).get("model") or "inherit")
            out["invoke_subagent"] = ('invoke_subagent(TypeName="self", Role="Part: %s", Model="%s", Workspace="share", '
                                      'Prompt="%s")') % (part, agy_model, text)
            sess = "pending: invoke_subagent (%s)" % name
        elif mode == "claude_bg":
            if not shutil.which("claude"):             # CL26 / R5: never an OSError on an absent binary
                return dict(out, ok=False, error=(NO_RUNTIME.replace("neither `claude` nor `agy`", "no `claude`") +
                            "; an agy orchestrator starts a part with: %s" % _launch_line(part, rel, model, cli="agy")))
            if gate and _rule("device_cap_gate", pl) and _device() is not None:
                try:
                    ok, why = _device().gate(_device().survey())
                except Exception:
                    ok, why = True, ""
                if not ok:
                    return dict(out, ok=False, error="device cap: " + why)
            wt, why = _continue_worktree(branch, base) if cont and not reset else _make_worktree(branch, base, reset=reset)
            if why:
                return dict(out, ok=False, error=why)
            out["worktree"] = wt
            # CL50: the worktree records its step before the session starts: `claude --bg` can run the work in a warm
            # process made for an earlier launch, whose KIT_POST is that launch's; the hooks read this file first
            _write_step(wt, pid)
            k = c.get("kit") or {}
            m = str(model or ((k.get("claude_cli_roles") or {}).get("part") or {}).get("model") or "inherit")
            cmd = ["claude", "--bg", "-n", name, "--permission-mode", permission_mode(k)]
            if m != "inherit":
                cmd += ["--model", m]
            cmd.append(text)
            try:
                # its own task list, named after its step (the orchestrator's is in this environment): a session that
                # replaces it on this computer opens the same one
                r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=120,
                                   env=dict(os.environ, KIT_ROLE="part", KIT_POST=pid, CLAUDE_CODE_TASK_LIST_ID=part_list(pid)))
            except (OSError, subprocess.SubprocessError) as e:
                return dict(out, ok=False, error="could not start claude: %s" % e)
            if r.returncode != 0:
                return dict(out, ok=False, error="claude --bg failed: %s" % (r.stderr or r.stdout).strip()[:300])
            sess = name
        else:
            return dict(out, ok=False, error=(NO_RUNTIME.replace("neither `claude` nor `agy`", "no `claude`") +
                        "; an agy orchestrator starts a part with: %s" % _launch_line(part, rel, model, cli="agy")))
    now = _now_z()
    fields = {"status": "in progress", "session_id": sess, "branch": branch, "working_by": me, "updated_at": now}
    if runner:
        fields["runner"] = runner
    if out.get("out_of_order"):
        fields["launched_out_of_order"] = {"why": str(anyway), "was": out["out_of_order"], "at": now}
        out["launched_out_of_order"] = fields["launched_out_of_order"]
    owner = orchestrator_here()       # CL49: the part reports to this session, on this device, unless another claims it
    if owner:
        fields.update(owner)
        out["orchestrator"] = owner
    else:
        out["warning_owner"] = "no session id here (kit.py whoami): the post names no owner; the part reports to `_orchestrator`"
    had = str(post.get("session_id") or "")
    if session_id and not take and had and had != session_id and not had.startswith("pending") and not marked:
        out["warning"] = "%s already named session %s: if it still runs, two are on this step; stop one" % (pid, had)
    if not session_id or not post.get("launched_at"):
        fields["launched_at"] = now
    if cont:
        fields["relaunched"] = {"at": now, "from": str(post.get("session_id") or ""), "n": int((post.get("relaunched") or {}).get("n") or 0) + 1
                                if isinstance(post.get("relaunched"), dict) else 1}
    out["session_id"] = sess
    if isinstance(given, dict):
        return dict(out, trains=False)
    if owner and post.get("orchestrator_unreached"):
        fields["orchestrator_unreached"] = None      # a fresh launch is reached by its launcher
    _local_update(url, token, "posts", pid, fields)
    tr = _local_update(url, token, "posts", "_trains", lambda t: _trains_mark(t, pid, dict(post, **fields), sess, rel))
    out["session_id"] = sess
    out["trains"] = tr is not None
    if artifact_here():                        # CL51: on a computer, launch sends nothing to the Artifact
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
    runner_note = (", runner %s" % r["runner"]) if r.get("runner") else ""
    lines = ["%s launched: post %s in progress, session %s, branch %s%s%s." % (
        r["part"], r["post"], r["session_id"], r["branch"], runner_note,
        "" if r.get("trains") else " (no Trains card here to mark)")]
    if r.get("attached"):
        lines.append("It is a step of board %s: launched from its clone %s (no seeded board, no board-mirror push there)."
                     % (r["attached"]["id"], r["attached"]["clone"]))
    if r.get("consumers"):
        rp_who = r.get("real_project_run") or _rule("real_project_run")
        if rp_who == "orchestrator":
            lines.append("Its session tests on: %s; it never attaches them: their real-project run is yours (CL38)." % ", ".join(r["consumers"]))
        elif rp_who == "part":
            lines.append("Its session tests on: %s; the part runs its real-project check in a scratch local clone or read-only (never push) before Done." % ", ".join(r["consumers"]))
        else:
            lines.append("Its session tests on: %s; dry run in a local clone (never push) goes in `real_project`." % ", ".join(r["consumers"]))
    if r.get("worktree"):
        lines.append("claude --bg runs in %s (made from the current branch)." % r["worktree"])
    if r.get("warning"):
        lines.append("Warning: " + r["warning"])
    if r.get("prompt_inline"):
        lines.append("The prompt could not be pushed to the board (%s): the start message carries its full text and the "
                     "session commits it on its branch (CL40)." % r["prompt_inline"]["reason"][:160])
    if r.get("invoke_subagent"):
        lines.append("Now start it: %s" % r["invoke_subagent"])
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


def lands_after_review():
    """Work reaches the trunk after its review (kit.land), where the kit may push the trunk and reviews run."""
    k = _kit_config().get("kit") or {}
    if k.get("land"):
        return k["land"] == "after_review"
    return bool(k.get("trunk_push")) and str((k.get("review") or {}).get("auto", "all")) != "off"


def landing_steps(posts, git=True):
    """[{post, part, branch, commit, seen, check, review, landed, held, why}] of the steps that have work of their own on a
    branch: which may reach the trunk, and why one may not yet."""
    g, hold, out = _post_gate(), lands_after_review(), []
    for pid, p in sorted(posts.items()):
        if pid.startswith("_") or not isinstance(p, dict) or p.get("removed") or g is None or not g.is_step(p):
            continue
        pr = p.get("proof") if isinstance(p.get("proof"), dict) else {}
        br = str(pr.get("branch") or p.get("branch") or "")
        st, rv = str(p.get("status") or "").lower(), str(p.get("review") or "").lower()
        if not br or p.get("landed") or str(p.get("column") or "") == "done" or st in ("done", "dropped", "planned"):
            if not (br and p.get("landed")):
                continue
        why = ""
        if hold and not p.get("landed"):
            if not g.is_complete(p):
                # a step from before the proofs is nobody's to hold: only one under way, or one `done` proved
                why = "it is not complete yet (status %s)" % (p.get("status") or "?") if pr or "progress" in st else ""
            elif not pr:
                why = ""                                     # complete before this rule: its work landed the old way
            elif p.get("review_auto") is False:
                why = ""
            elif rv != "continue":
                why = "its review has not said `continue` (%s)" % (("it said " + rv) if rv else "no verdict yet")
        seen, landed, newer = str(p.get("review_commit") or ""), str(p.get("landed") or ""), []
        if not landed and git:
            seen = reviewed_commit(pid, p, seen)
            landed, newer = landed_on_trunk(br, seen)
            if landed:
                why = ""                                 # on the trunk is landed, however it got there
        out.append({"post": pid, "part": p.get("part"), "branch": br, "commit": pr.get("sha") or "", "seen": seen,
                    "check": p.get("check") or "", "review": rv, "landed": landed, "held": bool(why), "why": why,
                    "found": bool(landed and not p.get("landed")), "newer": newer,
                    "ready": bool(hold and pr and g.is_complete(p) and not why and not landed)})
    return out


GENERATED = (".kit/manifest.json", ".kit/artifact-files/")          # CL71: out of git; never a reason to review again


def _is_generated(f):
    return any(f == g or (g.endswith("/") and f.startswith(g)) for g in GENERATED)


def _only_generated(sha):
    """The commit changes only generated files (CL71): never a reason to review again."""
    return all(_is_generated(f) for f in _git_run("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).stdout.split() if f)


def _git_ok(*a):
    return _git_run(*a).returncode == 0


def _trunk_ref():
    t = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    return ("origin/" + t) if _git_ok("rev-parse", "-q", "--verify", "refs/remotes/origin/" + t) else t


_MERGES = {}


def _trunk_merges(tref):
    """[(merged commit, subject)] of the trunk's merges (first parent: the trunk's own), newest first; read once."""
    if tref not in _MERGES:
        rows = []
        for ln in _git_run("log", tref, "--merges", "--first-parent", "-n", "3000", "--format=%P%x09%s").stdout.splitlines():
            par, _, subj = ln.partition("\t")
            ps = par.split()
            if len(ps) >= 2:
                rows.append((ps[1], subj))
        _MERGES[tref] = rows
    return _MERGES[tref]


def reviewed_commit(pid, p, seen=""):
    """CL71 (was CL65): the commit the step's last review read: its post's review_commit, or the `commit` of its newest
    review comment (CR-<post>, CR-<post>-2, ...) when that one is newer (a re-review whose commit never reached the post)."""
    try:
        cs = _post_comments(pid)
    except Exception:  # noqa: BLE001
        cs = {}
    rows = sorted((str(c.get("created_at") or ""), str(c.get("commit") or "")) for i, c in cs.items()
                  if isinstance(c, dict) and (str(i).startswith("CR-") or c.get("kind") == "review") and c.get("commit"))
    for _, c in reversed(rows):
        if not _git_ok("cat-file", "-e", c + "^{commit}"):
            continue
        if not seen or (c != seen and _git_ok("merge-base", "--is-ancestor", seen, c)):
            return c
        break
    return seen


def landed_on_trunk(br, seen):
    """CL71 (was CL70): (the commit that put the step on the trunk, [later commits of its branch, not reviewed]) or
    ("", []). On the trunk: the commit its review read; with none recorded, its branch's tip, or a trunk merge that names
    its branch (a hand merge, a shared branch). Local refs only (what the last fetch saw): cheap enough for resume."""
    tref = _trunk_ref()
    tip = _git_run("rev-parse", "-q", "--verify", "refs/remotes/origin/%s^{commit}" % br).stdout.strip() \
        or _git_run("rev-parse", "-q", "--verify", "refs/heads/%s^{commit}" % br).stdout.strip()
    sha = ""
    if seen:
        if _git_ok("cat-file", "-e", seen + "^{commit}") and _git_ok("merge-base", "--is-ancestor", seen, tref):
            sha = seen
    elif tip and _git_ok("merge-base", "--is-ancestor", tip, tref):
        sha = tip
    else:
        b = re.escape(br)
        rx = re.compile(r"^Merge (?:remote-tracking )?branch '(?:origin/)?%s'|^Merge (?:origin/)?%s(?: |$)(?!.* into (?:origin/)?%s\b)"
                        r"|^Merge pull request #\d+ from [^/\s]+/%s$" % (b, b, b, b))
        for merged, subj in _trunk_merges(tref):
            if rx.search(subj):
                sha = merged
                break
    if not sha:
        return "", []
    newer = []
    if tip and tip != sha:
        newer = [c for c in _git_run("rev-list", "--no-merges", tip, "^" + sha, "^" + tref).stdout.split()
                 if c and not _only_generated(c)]
    return sha, newer


def land_lines(posts, train=None, skip=None):
    """The steps whose work is not on the trunk yet, for a session that would build on it or has to land it."""
    out = []
    for s in landing_steps(posts):
        if s.get("found") and not (train and _norm_train(posts[s["post"]].get("train")) != _norm_train(train)):
            mark_landed(s)                                # CL71: on the trunk already, said once and marked
            out.append("- %s (`%s`) is on the trunk (at %s): marked landed%s" % (
                s["part"], s["post"], s["landed"][:9], "; %d newer commit(s) on `%s`, not reviewed" % (len(s["newer"]), s["branch"])
                if s["newer"] else ""))
            continue
        if s["landed"] or s["post"] == skip or not (s["held"] or s["ready"]) or (train and _norm_train(posts[s["post"]].get("train")) != _norm_train(train)):
            continue
        out.append("- %s (`%s`) on branch `%s`: %s" % (s["part"], s["post"], s["branch"], s["why"] if s["held"] else
                                                      "reviewed: `python3 -I .kit/kit.py land %s`" % s["post"]))
    return out


def _manifest(root):
    """CL71: .kit/manifest.json, rebuilt from the tree when this clone has none (it is not in git)."""
    for cand in (os.path.join(root, ".kit", "kit_manifest.py"), os.path.join(root, "kit", "kit_manifest.py")):
        if os.path.isfile(cand):
            try:
                spec = importlib.util.spec_from_file_location("kit_manifest", cand)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.load(root)
            except Exception:  # noqa: BLE001
                break
    return _read_json_file(os.path.join(root, ".kit", "manifest.json"))


def reviewed(pid, verdict, review_id, commit=None):
    """CL71 (was CL65): a review's verdict on its step's post, with the commit it read, whatever started the review:
    `review_commit` on the post and `commit` on its comment, so `land` merges what was read (CL57 needed a third review
    because the second's commit was never recorded)."""
    p = _get_post(pid) or {}
    if not p:
        return {"ok": False, "why": "no post %s on this board" % pid}
    br = str((p.get("proof") or {}).get("branch") or p.get("branch") or "")
    sha = ""
    if commit:
        sha = _git_run("rev-parse", "-q", "--verify", commit + "^{commit}").stdout.strip()
        if not sha:
            return {"ok": False, "why": "%s is no commit here (fetch it first)" % commit}
    elif br:
        _git_run("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), timeout=300)
        sha = _git_run("rev-parse", "-q", "--verify", "refs/remotes/origin/%s^{commit}" % br).stdout.strip()
    fields = {"review": verdict, "review_id": review_id}
    if sha:
        fields["review_commit"] = sha
    ok = _board_write("update", "posts", pid, fields)
    c_ok = _board_write("update", "comments", review_id, {"commit": sha}) if sha else False
    return {"ok": bool(ok), "post": pid, "review": verdict, "review_commit": sha, "comment_marked": bool(c_ok),
            "why": "" if sha else "its post names no branch: no commit recorded"}


def mark_landed(s):
    fields = {"landed": s["landed"], "landed_at": _now_z()}
    if s.get("newer"):
        fields["landed_newer"] = [c[:9] for c in s["newer"]]
    return _board_write("update", "posts", s["post"], fields)


def _landing(*args):
    p = os.path.join(ROOT, ".kit", "landing.py")
    if not os.path.isfile(p):
        return 1, ".kit/landing.py is missing: `install.py . --update`"
    try:
        r = subprocess.run([sys.executable, "-I", p] + list(args), capture_output=True, text=True, timeout=2400, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "landing: %s" % e
    return r.returncode, (r.stdout + r.stderr).strip()


def step_gate(pid, db_path=None):
    """Why this step cannot be completed yet, or "": the proof `kit.py done` left is for the post as it is, and no word of
    his under it waits. Cheap: nothing is rerun (the part's own machine ran the check and its break)."""
    p = _all_posts(db_path).get(pid) or _get_post(pid, db_path=db_path)
    if not p:
        return ("no post %s on this board (a step of an attached board is completed in its own clone, or by its part's `done` and the "
                "ArtifactData update of its post)" % pid)
    g = _post_gate()
    if g is None:
        return ".kit/post_gate.py is missing, so nothing can say this step is proved: `install.py . --update`"
    if not g.is_step(p) or g.is_complete(p):                 # complete already (an older step): its review may still be due
        return ""
    if (p.get("proof") or {}).get("key") == g.proof_key(p) and not g.proved(p):
        return ("%s: its run on a real project is pending: run it and write {\"project\", \"command\", \"output\"} in its `real_project`" % pid)
    if not g.proved(p):
        return ("%s: `python3 -I .kit/kit.py done %s` must say OK first, where the work is (its part runs it); %s" % (
            pid, pid, "the post changed since it did" if p.get("proof") else "it never did"))
    left = unanswered_under(_post_comments(pid, db_path=db_path))
    if left:
        return "%s: %d comment(s) of his under it have no answer: %s" % (pid, len(left), "; ".join("%s %r" % (i, t[:60]) for i, t in left[:3]))
    return ""


def ask(title, body="", options=(), default="", deadline="", needs="", about="", train="", priority=1, db_path=None):
    """One question post, in his lane, whole: what he chooses between, what happens if he does not answer, and by when."""
    g = _post_gate()
    if len(options) < 2 or default not in options:
        return {"error": "a question has at least two --option, and a --default that is one of them"}
    if not deadline:
        return {"error": "a question has a --deadline (a date or time): then its default applies"}
    doc = {"title": title, "body": (body or title) + ("\n\nAbout `%s`." % about if about else ""), "kind": "question", "column": "question",
           "options": list(options), "default_option": default, "deadline": deadline, "status": "open", "priority": int(priority),
           "from": "orchestrator", "created_at": _now_z()}
    if train:
        doc["train"] = train
    if needs:
        doc["needs"] = needs
    err = g.check_post("q", doc, None, _kit_config())[2] if g else None
    if err:
        return {"error": err}
    posts, n = _all_posts(db_path), 2
    pid = base = "q-" + _slug(title, 40)
    while pid in posts:
        pid, n = "%s-%d" % (base, n), n + 1
    return {"post": pid, "ok": True} if _board_write("set", "posts", pid, doc) else {"error": "the board did not take it (is the board server running?)"}


def step_complete(pid, db_path=None, cli=None):
    """Status complete (the owner moves it to Done), then its review and, every kit.review.train_every completed
    steps of its train, a cross review. Reviews are idempotent (one per step, one per count)."""
    why = step_gate(pid, db_path=db_path)
    if why:
        return {"post": pid, "ok": False, "why": why, "reviews": []}
    fields = {"status": "complete", "completed_at": _now_z()}
    before = _all_posts(db_path).get(pid) or {}
    br = str((before.get("proof") or {}).get("branch") or before.get("branch") or "")
    if br and lands_after_review():                          # what the review reads, so nothing after it lands unread
        _git_run("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br))
        tip = _git_run("rev-parse", "-q", "--verify", "refs/remotes/origin/" + br).stdout.strip()
        if tip:
            fields["review_commit"] = tip
    out = {"post": pid, "ok": _board_write("update", "posts", pid, fields), "reviews": []}
    if out["ok"] and lands_after_review() and before.get("review_auto") is False:
        out["landed"] = _landing("land", pid)[1]
    rv = (_kit_config().get("kit") or {}).get("review") or {}
    if str(rv.get("auto", "all")) == "off":
        return out
    posts = _all_posts(db_path)
    p = posts.get(pid) or {}
    if p.get("review_auto") is not False and not (p.get("review") or p.get("review_id") or p.get("review_started")):
        rc, msg = review(pid, db_path=db_path, cli=cli)
        out["reviews"].append(msg)
        if rc == 0 and "NOT STARTED" not in msg and "already started" not in msg and not os.environ.get("KIT_NO_LAUNCH"):
            _board_write("update", "posts", pid, {"review_started": _now_z()})    # on the post: the next orchestrator sees it
    train, every = p.get("train"), int(rv.get("train_every", 4) or 0)
    if train and every > 0:
        n = sum(1 for q in posts.values() if not q.get("removed") and _norm_train(q.get("train")) == _norm_train(train)
                and (str(q.get("status") or "") == "complete" or q.get("column") == "done"))
        if n and n % every == 0:
            rc, msg = review(None, train=str(train), db_path=db_path, cli=cli)
            out["reviews"].append(msg)
    return out


_agents = {"at": None, "out": None}


def _claude_agents(fresh=5.0):
    """What `claude agents --json` prints ('' : nothing, or no `claude`). It takes about a second, up to five when cold,
    and one `resume` needs it twice: asked once, kept a few seconds."""
    now = time.monotonic()
    if _agents["at"] is None or now - _agents["at"] > fresh:
        try:
            out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            out = None
        _agents.update(at=time.monotonic(), out=out)
    return _agents["out"] or ""


def _alive_sessions():
    """Names (and ids) of the Claude Code sessions running on this computer, from `claude agents --json`."""
    out = _claude_agents()
    if _agents["out"] is None:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    alive = set()
    for r in rows:
        for k in ("name", "id", "sessionId"):
            if r.get(k):
                alive.add(str(r[k]))
    return alive


def _step_work_locations(pid, p):
    """CL44 (item 11): find where a step's work actually lives: matching local/remote branches, last checkpoint commit,
    and any worktree (including agy subagent worktrees) holding uncommitted changes for this step."""
    trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    part_slug = _slug(str(p.get("part") or ""), 24)
    named_br = str(p.get("branch") or "").strip()
    pat = re.compile(r"(^|[-/_])%s($|[-/_])" % re.escape(part_slug), re.I) if len(part_slug) >= 2 else None

    def _matches(br):
        b = str(br or "").strip()
        if not b or b in (trunk, "main", "master", "HEAD", "board-mirror", "kit-usage") or b.startswith("wip/"):
            return False
        if named_br and b == named_br:
            return True
        if pid and pid.lower() in b.lower():
            return True
        return bool(pat and pat.search(b))

    branches = []
    seen_br = set()
    try:
        r = _git_run("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)\t%(objectname:short)\t%(committerdate:iso-strict)\t%(subject)",
                     "refs/heads", "refs/remotes/origin", timeout=15)
        for line in (r.stdout or "").splitlines():
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            ref, sha, when, subj = parts
            short_br = ref[len("origin/"):] if ref.startswith("origin/") else ref
            if not _matches(short_br) or short_br in seen_br:
                continue
            seen_br.add(short_br)
            # Only report last checkpoint if the branch has at least one commit beyond trunk (or is the named branch)
            has_own = _git_run("rev-list", "--count", "%s..%s" % (trunk, ref), timeout=10).stdout.strip() not in ("", "0")
            if has_own or (named_br and short_br == named_br):
                branches.append({"ref": ref, "branch": short_br, "sha": sha, "when": when[:16].replace("T", " "),
                                 "subject": subj[:80], "has_own": has_own})
    except (OSError, subprocess.SubprocessError):
        pass

    worktrees = []
    try:
        r = _git_run("worktree", "list", "--porcelain", timeout=15)
        cur_wt, cur_br = None, ""
        entries = []
        for line in (r.stdout or "").splitlines() + [""]:
            if line.startswith("worktree "):
                cur_wt, cur_br = line[9:].strip(), ""
            elif line.startswith("branch refs/heads/"):
                cur_br = line[len("branch refs/heads/"):].strip()
            elif not line.strip() and cur_wt:
                entries.append((cur_wt, cur_br))
                cur_wt, cur_br = None, ""
        for wt, br in entries:
            if not (_matches(br) or (part_slug and pat and pat.search(os.path.basename(wt)))):
                continue
            if not os.path.isdir(wt):
                continue
            st = _git_run("status", "--porcelain", "--untracked-files=all", cwd=wt, timeout=15).stdout
            dirty = [ln[3:].strip() for ln in st.splitlines() if ln.strip()]
            if dirty:
                worktrees.append({"path": wt, "branch": br, "dirty": dirty})
    except (OSError, subprocess.SubprocessError):
        pass
    return {"branches": branches, "worktrees": worktrees}


def _step_location_lines(pid, p, skip_wt=None):
    """Formatted lines naming a step's branch, last checkpoint commit, and dirty worktrees."""
    loc = _step_work_locations(pid, p)
    out = []
    for b in loc["branches"][:3]:
        ck = (" · last checkpoint `%s` (%s): %s" % (b["sha"], b["when"], b["subject"])) if b["has_own"] else " · no commits ahead of trunk yet"
        out.append("  branch `%s`%s" % (b["ref"], ck))
    abs_skip = os.path.abspath(skip_wt) if skip_wt else None
    for w in loc["worktrees"]:
        if abs_skip and os.path.abspath(w["path"]) == abs_skip:
            continue
        rel = os.path.relpath(w["path"], ROOT)
        shown_path = rel if not rel.startswith("..") else w["path"]
        out.append("  worktree `%s` (branch `%s`): %d uncommitted file(s): %s" % (
            shown_path, w["branch"] or "?", len(w["dirty"]), ", ".join(w["dirty"][:6]) + (" ..." if len(w["dirty"]) > 6 else "")))
    return out


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
        agy_hit = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain", sess,
                               ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(agy_hit):
            hits.append(agy_hit)
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
        open_steps = [x for x in steps if step_state(x[1]) not in ("done", "closed") or review_due(x[1])]
        if not open_steps:
            continue
        n_active += 1
        out.append("")
        out.append("%s Train %s (%d/%d done)" % (h, t, len(steps) - len(open_steps), len(steps)))
        for pid, p in steps:
            col = str(p.get("column") or "info").lower()
            st = str(p.get("status") or "").lower()
            if review_due(p):
                out.append("- [r] %s `%s` %s · finished, its review never started: `python3 -I .kit/kit.py step complete %s`"
                           " (it starts it), or its task completed" % (p.get("part") or pid, pid, str(p.get("title") or "")[:80], pid))
                continue
            if step_state(p) in ("done", "closed"):
                continue                                 # what it found is under "Found so far"
            box = step_box(p)
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
            elif st in ("planned", "in progress") or "progress" in st:
                out.extend(_step_location_lines(pid, p))
                if "progress" in st and sess and not sess.startswith("pending"):
                    if alive is None or sess.startswith("session_"):        # a web session: only get_session knows
                        out.extend(relaunch_lines(pid, p, posts, unknown=True))
                    elif sess not in alive:
                        out.extend(relaunch_lines(pid, p, posts))
        got = found_so_far(posts, t)
        if got:
            out.append("%s# Found so far (do not repeat it; a new step near one says what it does differently)" % h)
            out.extend(got)
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


BOXES = {"done": "[x]", "closed": "[x]", "held": "[h]", "running": "[~]", "idle": "[ ]"}


def step_box(p):
    """The one tick of a step, in every view (the train view used to tick only the owner's Done column: the same train
    showed 21 open steps there and 6 in resume)."""
    col = str(p.get("column") or "").lower()
    if step_state(p) == "you":
        return "[?]" if col == "question" else "[!]"
    return BOXES[step_state(p)]


def review_due(p):
    """A finished step whose review never started: its orchestrator was replaced before it ticked the task (its task
    list dies with it) and the next one, shown open steps only, had nothing to tick. Only steps made since
    kit.review.since (install.py stamps it): a project's past is not reviewed again."""
    rv = (_kit_config().get("kit") or {}).get("review") or {}
    since = str(rv.get("since") or "")
    return bool(since and str(rv.get("auto", "all")) != "off" and str(p.get("created_at") or "") >= since
                and step_state(p) == "done" and str(p.get("column") or "") not in ("done", "question", "todo")
                and (p.get("session_id") or p.get("verdict")) and p.get("review_auto") is not False
                and not (p.get("review") or p.get("review_id") or p.get("review_started")))


def step_brief(pid, p, width=150):
    """A step in two lines: what it is, what it found and what the next one must know."""
    found = _short(p.get("verdict") or _first_summary_line(p.get("body")), width)
    nxt = _short(p.get("handoff"), 110)
    line = "- %s %s `%s` %s" % (step_box(p), p.get("part") or pid, pid, _short(p.get("title"), 60))
    tail = " · next: ".join(x for x in (found, nxt) if x)
    return line + (("\n  " + tail) if tail else "")


def train_steps(posts, train):
    """[(id, post)] of a train's steps, oldest first."""
    rows = [(pid, q) for pid, q in posts.items() if not pid.startswith("_") and isinstance(q, dict) and not q.get("removed")
            and str(q.get("train") or "").strip() and _norm_train(q.get("train")) == _norm_train(train)]
    return sorted(rows, key=lambda x: (str(x[1].get("created_at") or ""), x[0]))


def train_questions(posts, pid, post, limit=8):
    """The owner's open questions that bear on a step: on its train, or naming one of its train's parts. A part never
    decides one, and never works past it."""
    parts = {str(q.get("part") or i) for i, q in train_steps(posts, post.get("train") or "")} | {str(post.get("part") or pid)}
    parts = {x for x in parts if re.search(r"\d", x) and len(x) >= 2}        # a bare letter or word is no name
    wr = re.compile(r"(?<![\w-])(%s)(?![\w])" % "|".join(sorted(map(re.escape, parts), key=len, reverse=True))) if parts else None
    out = []
    for i, q in sorted(posts.items()):
        if i == pid or i.startswith("_") or not isinstance(q, dict) or q.get("removed") or q.get("column") != "question" or step_state(q) != "you":
            continue
        same = str(q.get("train") or "").strip() and _norm_train(q["train"]) == _norm_train(post.get("train") or "")
        text = "%s %s" % (q.get("title") or "", str(q.get("body") or "")[:1200])
        if same or (wr and wr.search(text)):
            dflt = q.get("default_option") or ""
            out.append("- `%s` %s%s" % (i, re.sub(r"\s+", " ", str(q.get("title") or i)).strip(), (" · default: " + _short(dflt, 60)) if dflt else ""))
    return out[:limit] + (["- (%d more: `kit.py resume`)" % (len(out) - limit)] if len(out) > limit else [])


def found_so_far(posts, train, skip=None, last=8):
    """The last finished steps of a train, briefly, and how many came before them."""
    rows = [(pid, q) for pid, q in train_steps(posts, train) if pid != skip and step_state(q) == "done"
            and (q.get("verdict") or q.get("body"))]
    if not rows:
        return []
    out = [step_brief(pid, q) for pid, q in rows[-last:]]
    if len(rows) > last:
        out.append("- (%d earlier: `python3 -I .kit/kit.py train %s --brief`)" % (len(rows) - last, _norm_train(train)))
    return out


# ---------------------------------------------------------------- the circle check: a step says what it does differently
CIRCLE_HEAD = "## What this does differently"


def _differs_of(post, rel):
    """What a step says it does differently: on its post, else in its prompt."""
    got = str(post.get("differs") or "").strip()
    if not got:
        try:
            with open(rel, encoding="utf-8") as fh:
                parts = fh.read().split(CIRCLE_HEAD, 1)
            got = parts[1].strip() if len(parts) == 2 else ""
        except OSError:
            pass
    return got


def differs_problem(text, rows):
    """None, or what is missing: a sentence that names at least one earlier step by its part, or says none of them bears on it."""
    if len(text) < 30:
        return "a sentence of at least 30 characters"
    named = [q for q, d in rows if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(str(d.get("part") or q)), text, re.I)]
    if not named and not re.match(r"none of (them|these)\b", text, re.I):
        return "the earlier step it builds on or departs from, by its part (or a text that starts \"None of them\")"
    return None


def circle_gate(posts, pid, post, path):
    """None, or why a step is not launched yet: its train has finished steps, and it does not say which of them it
    builds on and what it does differently. Every earlier conclusion is shown, not the ones a script finds alike: the
    circle this is for (Harmonize's B14 left out what B9 and B12 had found, B16 found it again) shares no words with
    them. A step already started (a relaunch, a continuation) is not asked again."""
    rows = [(q, d) for q, d in train_steps(posts, post.get("train") or "") if q != pid and step_state(d) == "done"
            and (d.get("verdict") or d.get("body"))]
    if len(rows) < 2 or post.get("launched_at") or post.get("session_id"):
        return None
    why = differs_problem(_differs_of(post, path), rows)
    if not why:
        return None
    return ("train %s has already found:\n%s\nBefore this step starts, say which of these it builds on and what it does differently "
            "(missing: %s): `python3 -I .kit/kit.py step differs %s --task \"<PART>: ...\"` (it goes on the post), launch again. If it "
            "repeats one of them, do not launch it." % (
                _norm_train(post.get("train")), "\n".join("- %s: %s" % (d.get("part") or q, _short(
                    d.get("verdict") or _first_summary_line(d.get("body")), 130)) for q, d in rows), why, pid))


def step_differs(pid, text, db_path=None):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < 30:
        return {"error": "step differs needs --task \"<PART it builds on>: what it does differently\" (a sentence at least)"}
    rel = "docs/prompts/%s.md" % pid
    path = os.path.join(ROOT, rel)
    res = {"post": pid, "prompt": None}
    on_post = pid in _all_posts(db_path) and _board_write("update", "posts", pid, {"differs": text})
    if on_post:          # CL54: written once, on the post; the launch regenerates the prompt with it
        return dict(res, ok=True, next="launch it: the prompt is generated from the post")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            cur = fh.read().split("\n" + CIRCLE_HEAD, 1)[0].rstrip()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cur + "\n\n" + CIRCLE_HEAD + "\n" + text + "\n")
        res.update(prompt=rel, next="commit %s" % rel)
    res["ok"] = bool(res["prompt"])
    if not res["ok"]:
        res["error"] = "no post %s the board here takes, and no prompt %s" % (pid, rel)
    return res


def step_open(db_path=None):
    """Every step an orchestrator's task list holds, on this board and the attached ones: the open ones and the
    finished ones whose review never started. [{post, part, train, board, state, title}]"""
    out = []

    def add(posts, board):
        for pid, q in sorted(posts.items(), key=lambda x: (str(x[1].get("created_at") or "") if isinstance(x[1], dict) else "", x[0])):
            if pid.startswith("_") or not isinstance(q, dict) or q.get("removed") or not str(q.get("train") or "").strip():
                continue
            due = review_due(q)
            if step_state(q) in ("idle", "running") or due:
                out.append({"post": pid, "part": str(q.get("part") or pid), "train": _norm_train(q["train"]), "board": board,
                            "state": "review due" if due else str(q.get("status") or step_state(q)), "title": str(q.get("title") or "")[:80]})
    add(_all_posts(db_path), "")
    if not db_path:
        try:
            for b in _attached_boards():
                add(b["posts"], b["id"])
        except Exception:      # an attached board that cannot be read never hides this one's steps
            pass
    return out


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
        if not isinstance(c, dict) or c.get("from") != owner or not c.get("created_at"):
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
    if artifact_here():
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
# bypassed artifact load). `kit.py commands <word>` is the index; it comes to a session when it edits the kit's own files
# (lesson K3, resume and kit_memory_jit.py), never to every part (2026-11-25: six of six read 6,600 characters of it for
# a script that had nothing to do with the kit); a test
# keeps every kit.py command in it, so it cannot go stale.
COMMANDS = [
    # (need, command, what it does)
    ("read the board", "kit.py resume", "every train's open steps (the task list), live sessions, open questions (age, default, what each blocks), the owner's decisions of the last 3 days, the owner's unanswered comments, attached boards; run it before answering the owner; again in the same session it prints only what changed since the last one (`--full`: all of it)"),
    ("read the board", "kit.py guide [case]", "one case of the orchestrator's guide, when it comes up (resume lists them)"),
    ("ask him", "kit.py ask --title T --option A --option B --default A --deadline D [--needs N]", "one question post in his lane, whole; never a question in a thread"),
    ("launch and finish", "kit.py land <post>", "a reviewed step's branch onto the trunk, its check run on the two together; never forced"),
    ("launch and finish", "kit.py reviewed <post> <continue|redirect|stop> --review-id <CR-...>",
     "a review's verdict on the post, with the commit it read (review_commit, and on its comment): land merges that one (CL71)"),
    ("git copy", "kit.py landing | save | checkpoint", "which steps may reach the trunk; save work in progress to wip/<branch> or checkpoint on the step's branch (CL17)"),
    ("save the board", "kit.py keep", "the board here into git, committed and pushed (git is the record)"),
    ("read the board", "kit.py resume --role orchestrator|part|clerk|review [--post P]", "what that role needs and nothing else: a session starts from it alone (lessons, launch workflow, tests, how to report)"),
    ("memory", "kit.py lessons [--role R] [--files F...] | lessons check [--ask]", "the lessons a role needs / refuse untagged ones, ones only a project doc holds, a \"be careful\" remedy, and run every entry's check (test:, hook:, code:, refusal:): flags one whose test is gone or fails or whose code no longer exists, lists the unchecked (M7); flags two live entries that contradict and a second claim of authority, and asks which wins (M8)"),
    ("memory", "kit.py lessons replace <wins> <goes> | lesson add ... --replaces <id> | ledger observe ... --replaces <id>", "an entry replaces another: the old one goes to the archive with a pointer to its successor"),
    ("memory", "kit.py lessons review [--post] [--days N] | lessons keep <id>", "entries unconfirmed for more than kit.memory.review_days, as a question post on the board (never deleted) / say one still holds"),
    ("memory", "kit.py lesson add <id> --roles R,.. --title T --text X [--files G,..] [--from \"doc id\"]", "a lesson into the memory, tagged with its roles"),
    ("read the board", "kit.py train <train> [--zoom <part>] [--brief | --sections [PART..] | --full PART..]", "a train's steps at three depths: two lines each; their seven fields; a step whole"),
    ("read the board", "kit.py post <id> [--head N]", "one post and its comments"),
    ("plan", "kit.py trains [list | new <train> --goal G | close <train> --why W]", "the trains and their one goal each; a new train's goal before its first step; a train closed when its goal is met (step new refuses a closed or full one)"),
    ("read the board", "kit.py open [--since ISO]", "the owner's comments not answered yet"),
    ("read the board", "kit.py since <ISO>", "what changed on the board since a time"),
    ("read the board", "kit.py session <post>", "the session a post names, and what it left"),
    ("read the board", "kit.py search <words>", "find posts and comments by words"),
    ("read the board", "kit.py queue [--runner R] [--gate]", "the steps waiting for a runner / --gate: the start-now steps not launched nor held (the orchestrator's Stop refuses on them, CL62)"),
    ("launch and finish", "kit.py link <post> [--hours H]", "the one link an outside AI (a runner card, LMArena, another account's app) gets for a post: its invite, scoped to the post: attach files to it (its drop box) and write its result, first (CL56)"),
    ("read the board", "kit.py sections", "which ## sections the owner opens and folds"),
    ("read the board", "kit.py report / doctor [--fix]", "the board's size, columns, oldest question, missing files / full install, hook parity, conflict & sync health"),
    ("sessions", "kit.py permission-mode", "the mode the kit starts Claude Code sessions in: every check off once he accepted it, else auto"),
    ("read the board", "kit.py portfolio", "every project of this owner and where each stands"),
    ("write the board", "kit.py sub <post> <number>... | \"<piece>\" [--status doing|done|drop] [--why \"...\"]", "a step's pieces by number (the task's numbered lines) or title, lines on its post; done refuses an open one; a piece the task names drops only with --why"),
    ("launch and finish", "kit.py form <post> [--doing \"...\"]", "a step's result as a file, half filled; `done` sends and checks it"),
    ("launch and finish", "kit.py whoami [name]", "this session's id for a post's session_id (web: session_<id>; else the name given)"),
    ("git copy", "kit.py fresh [--again]", "fetch origin: say when it is behind its trunk or on a stale branch, move the trunk forward when clean (CL53; the SessionStart hook and the first resume run it once a session)"),
    ("launch and finish", "kit.py branches [--delete]", "origin's branches fully on the trunk (finished parts), and with --delete removes them"),
    ("launch and finish", "kit.py web-env [ask] [--id env_...]", "several repos on the web (CL39): the create_environment call (every repo, hooks at user level), its question post, its id for the hand-over"),
    ("launch and finish", "kit.py attach <owner/repo>", "attach another repo (web: the add_repo call, clone next to this project, register, record in the portfolio)"),
    ("read the board", "board_cli.py get|list|query <coll> ...", "raw documents of the local board (prefer the lookups above)"),
    ("read the board", "board_cli.py inbox", "unread owner comments, Likes and option taps, compact"),
    ("write the board", "TaskCreate {train, part} (Claude Code) / kit.py step new", "plan a step: its post (the task, written once); launch generates docs/prompts/<id>.md from it, never by hand"),
    ("write the board", "kit.py step start|complete|drop", "move a step on its train"),
    ("write the board", "kit.py adopt", "once: give posts made before the trains their train (from their part), then artifact push"),
    ("write the board", "board_cli.py set|update|batch <coll> <id> --data JSON", "write a post or a comment on the local board"),
    ("write the board", "board_cli.py reply <post> <comment> --text T", "answer the owner's comment in the owner's thread (marks it read)"),
    ("write the board", "kit_cards.py (agent kit-cards)", "rebuild the Where we are card and the Trains card, never by hand"),
    ("launch and finish", "kit.py launch <post>", "start a part: post in progress, Trains row running, then claude --bg or the create_session call"),
    ("launch and finish", "kit.py claim <post>", "take a step explicitly: your address on its post; its part and review report to you (CL49)"),
    ("launch and finish", "kit.py hold <post> --why W [--release]", "a start-now step not launched yet, with the reason the owner reads (on the post, in resume and the queue, CL62)"),
    ("launch and finish", "kit.py unreached <post> --why W", "a part (or review) could not reach its orchestrator: the post says so, every orchestrator's resume lists it"),
    ("launch and finish", "kit.py capacity / device.py run -- <cmd>", "the device's session cap / run a heavy job when it fits in memory"),
    ("launch and finish", "kit.py done <post>", "check a step's post: format, artifacts, check passes, breaks makes it fail"),
    ("launch and finish", "kit.py review <post> | --train T", "start the review of a step or a train"),
    ("launch and finish", "kit.py pair <post>", "a blind before/after picture pair for a visual step"),
    ("launch and finish", "end_session.py", "commit check, push, merge onto the trunk when allowed"),
    ("launch and finish", "handover.py --role R", "a context reset: a fresh session under the same name"),
    ("Artifact and web", "web_takeover.py [save]", "a web session: the board from git and the steps to continue / the board back to git"),
    ("Artifact and web", "kit.py artifact pull|load|push|sent|pin", "the Artifact board to the local copy (starts the local board) and back, as pinned batches"),
    ("Artifact and web", "kit.py artifact reconcile", "a web session's start, after the pull: the Artifact vs origin/board-mirror, both ways, newer wins, a both-sides change kept twice"),
    ("Artifact and web", "kit.py reconcile", "the board here vs its git copy: the newer copy of each document wins"),
    ("Artifact and web", "kit_artifact_sync.py", "the repo's documents and files into the Artifact for its viewers"),
    ("Artifact and web", "update_board.py", "the Artifact's page to the kit's current page"),
    ("git copy", "kit.py init|sync|sync-db|reads|receive|publish|diff|push", "the board's git copy (board-mirror): make it, fill it, compare it, send it"),
    ("memory", "kit.py compact", "post every open awaits point (a question post) and open issue (an issue post, never doubled) on the board, then archive the awaits file, the states and issues the board now holds, and old lessons; on an Artifact board nothing leaves memory until `artifact push` sent the posts; refused if the project's memory checker goes red (--dry-run lists the posts)"),
    ("memory", "kit.py ledger show|observe|decide|distil|propose|field|broke|moves|open|confirm|check|init", "3-layer memory ledger: silent scoped observations -> mechanical invariants (hooks) -> owner-confirmed distilled principles"),
    ("setup", "kit.py welcome [--post]", "the welcome post: what the board does and what can run now"),
    ("setup", "install.py <project> [--update]", "install or update the kit in a project (every present runtime)"),
    ("setup", "kit_import.py / step0_scan.py", "a person's state from their chats / a project's step 0 profile"),
    ("measure", "kit.py usage compare --before <kit commit|date> --after <...> [--role part|orchestrator|clerk|review]", "tokens, cost, turns, tool calls, board bytes per role, median and total with n, before vs after a kit change (CL37)"),
    ("measure", "kit.py usage show --commands [--role R] [--transcripts GLOB] / compare ... --commands", "cost per command: each result's size and its carry (tokens x calls after it, cache read price), top commands per role, runs of commands repeated in 3+ sessions as script candidates (M6)"),
    ("measure", "kit.py usage record|show|push|import-ccr", "one session's usage row from its transcript (the Stop/SessionEnd hook does it, no model call) / the rows / onto the ledger branch kit-usage / rows from saved get_session records"),
    ("read the code", "kit.py map [--root DIR] [--out DIR] [--config F] [--check]", "a map of any project generated from its code (M1): overview.md in one screen, a card per area and feature, joins.md (UI fields sent vs schema accepted, handler, tables, tests; dropped fields, write routes with no CSRF/auth, uncalled handlers, files over 1,500 lines), map.json things/links/flags; --check fails when the code changed or a link check fails"),
    ("tests", "run_all_tests.py [--all] | --clean [--yes] [--older H]", "the kit's tests, each suite in its own temp folder that fails it if anything is left there (CL52) / the folders past test runs left in the temp folder: a dry run, --yes removes them"),
    ("index", "kit.py commands [word|all]", "the topics / the commands a word finds / every one"),
]


def commands_text(word=None):
    if not word:                                              # the topics; a word (or `all`) gives the commands
        topics = list(dict.fromkeys(n for n, _, _ in COMMANDS))
        return "# The kit's commands: `python3 -I .kit/kit.py commands <word>` (or `all`)\nTopics: %s\n" % ", ".join(topics)
    word = None if word == "all" else word
    rows = [r for r in COMMANDS if not word or word.lower() in " ".join(r).lower()]
    out = ["# The kit's commands (python3 -I .kit/<script>; reuse, never rebuild)"]
    need = None
    for n, cmd, what in rows:
        if n != need:
            out += ["", "## " + n]
            need = n
        out.append("- `%s`: %s" % (cmd, what))
    if not rows:
        out.append("nothing matches %r: `kit.py commands all` lists them" % word)
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
    pl = _platform(cli)
    posts, comments = _load_board_docs(m, db_path=db_path)
    if alive is None:
        alive = _alive_sessions() if _rule("alive_check", pl) == "claude_agents" else None
    out = ["# Resume (from the board; nothing else carries over)"]
    lines, n_active = _train_lines(posts, alive)
    out.extend(lines)
    if not n_active:
        out.append("")
        out.append("No train has an open step.")
    else:
        out += _resume_queue(posts)
    out.extend(_resume_extras(posts, comments, now=now, session=session))
    dl = device_line()
    if dl:
        out.append("")
        out.append("## This device")
        out.append(dl + ("" if (dl.startswith("Device: cloud") or not _rule("device_cap_gate", pl)) else " A launch past the cap is refused "
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
                   (" No clone here: %s is not in this session's environment; the orchestrator starts in the one holding "
                    "every repo (`kit.py web-env`)." % (b.get("github") or b["id"])) if web_env() else
                   " No clone here: clone %s next to this repo (`kit.py web-env` on the web: one environment holding "
                   "every repo), run resume again." % (b.get("github") or b["id"])))
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
    if boards and artifact_here() and not glob.glob(os.path.join(_art_dir(), "in", "repos", "*")):
        out.append("")
        out.append("(Comments written on the Artifact page on an attached board live only there: `python3 -I .kit/kit.py "
                   "artifact pull` brings them (changes only), then `kit.py artifact load`, then resume again.)")
    out.append("")
    tlist = _rule("task_list", pl)
    if tlist == "agy_step":
        out.append("Resume in agy: cross-check each session UUID above with `manage_subagents(Action=\"list\")`. "
                   "Plan and update steps with `python3 -I .kit/kit.py step new|start|complete|drop` (the train IS your "
                   "task list). A step whose session is not running and not done: check its branch (`git branch -a`) "
                   "and continue it with `invoke_subagent(..., Workspace=\"share\")` or ask the owner."
                   + (" Each step belongs to a board: this one (\"%s\") or the attached board named in its heading; "
                      "an attached board's step is planned, updated and continued in that board's clone (its relaunch "
                      "line), and a reply to the owner's comment there goes to repos/<id>/comments."
                      % _repo_id(_kit_config().get("project") or os.path.basename(ROOT)) if boards else ""))
    elif tlist == "step_cli":
        out.append("Manage steps with `python3 -I .kit/kit.py step new|start|complete|drop` (the train IS your task list; "
                   "no session can be started here): do the next open step yourself on a branch of its own.")
    elif boards:
        out.append("Rebuild your task list: one TaskCreate per open step above ([r] too: its task completed starts its review), subject \"<PART> <title>\", metadata "
                   "{\"board\": \"<id>\", \"train\": \"<train>\", \"part\": \"<PART>\"} (board: this board's id \"%s\", or the "
                   "attached board's id from its heading; the existing post is adopted, not duplicated); TaskUpdate "
                   "in_progress for each [~]. A step whose session is \"not running\" and not done: relaunch it with its relaunch "
                   "line (`kit.py launch <post> --continue`; an attached board's step from its own clone), within the device "
                   "cap, without asking; only a step that waits on the owner waits for his answer (CL17)."
                   % (_repo_id(_kit_config().get("project") or os.path.basename(ROOT))))
    else:
        out.append("Rebuild your task list: one TaskCreate per open step above ([r] too: its task completed starts its review), subject \"<PART> <title>\", metadata "
                   "{\"train\": \"<train>\", \"part\": \"<PART>\"} (the existing post is adopted, not duplicated); "
                   "TaskUpdate in_progress for each [~]. A step whose session is \"not running\" and not done: relaunch it with its "
                   "relaunch line (`kit.py launch <post> --continue`), within the device cap, without asking; only a step that "
                   "waits on the owner waits for his answer (CL17).")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- M5: a resume prints only what changed
# A session that already ran `resume` gets what changed since its last one (steps, sessions, questions, the owner's
# comments, other writers), or one line; `--full` prints it whole. The first resume of a session, a hand-over (a new
# generation in .kit/handover-<role>.gen) and a takeover (a new session id) print it whole; a compaction clears the
# session's state (kit_session_start.py), so the summary-holder reads it whole again.
RESUME_DELTA_ROLES = (None, "orchestrator")


def resume_state_path(session, role=None):
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(session or ""))
    return os.path.join(ROOT, ".kit", "run", "resume", "%s-%s.json" % (role or "orchestrator", sid)) if sid else ""


def _handover_gen(role=None):
    try:
        with open(os.path.join(ROOT, ".kit", "handover-%s.gen" % (role or "orchestrator")), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _resume_snapshot(posts, comments, alive=None):
    """What a delta compares: per post its state, per comment who and what, his open comments, the running sessions."""
    ad = answer_delay()
    ps = {}
    for pid, p in (posts or {}).items():
        if not isinstance(p, dict):
            continue
        ps[pid] = {k: str(p.get(k) or "") for k in ("title", "part", "train", "status", "column", "kind", "session_id",
                                                     "updated_at", "done_at", "from", "_board")}
    cs = {}
    for cid, c in (comments or {}).items():
        if isinstance(c, dict) and c.get("post_id"):
            cs[cid] = {"post_id": str(c.get("post_id") or ""), "from": str(c.get("from") or ""),
                       "his": bool(ad.is_his(c)), "like": bool(c.get("like")), "answer": str(c.get("answer") or ""),
                       "text": (c.get("text") or "").replace("\n", " ")[:100], "board": str(c.get("_board") or "")}
    try:
        opn = sorted(x.split(" ", 1)[0] for x in _open_of({k: v for k, v in (comments or {}).items()
                                                            if isinstance(v, dict)}, empty=False))
    except Exception:  # noqa: BLE001 - a malformed comment never loses the delta
        opn = []
    return {"posts": ps, "comments": cs, "open": opn, "alive": sorted(alive) if alive is not None else None}


# CL58: the orchestrator's resume sections that posts, comments and sessions do not hold, so a change in one shows in
# a delta (CL39's web environment record printed "nothing new"). Checked and left out: train_lines (it ages with the
# clock: every delta would show it), owner_lines and autonomy_lines (config text, no action), audit_lines and
# memory_line (reports, not something to act on), split/start/inherit lines (from posts and comments: covered).
RESUME_SECTIONS = (("web_env", "Web environment (several repos)"),
                   ("land", "Work not on the trunk yet (it lands after its review)"),
                   ("unreached", "Parts that could not reach their orchestrator (to claim)"),
                   ("git_copy", "The record is git, and it is behind"))


def _resume_sections(m, posts, comments, db_path=None):
    """{key: [lines]} of RESUME_SECTIONS as the whole orchestrator resume would print them; a section that fails is
    left out (never lose the delta over one)."""
    out = {}
    makers = {"web_env": lambda: [x for x in [web_env_state(posts, comments)[1]] if x],
              "land": lambda: land_lines(posts),
              "unreached": lambda: unreached_lines(posts),
              "git_copy": lambda: ["- " + x for x in git_copy_lines(m, db_path)]}
    for key, _ in RESUME_SECTIONS:
        try:
            out[key] = [str(x) for x in (makers[key]() or []) if str(x).strip()]
        except Exception:  # noqa: BLE001
            pass
    return out


def resume_section_lines(old, new):
    """The RESUME_SECTIONS whose text changed between two snapshots, each printed whole as it is now."""
    os_, ns = old.get("sections"), new.get("sections")
    if not isinstance(os_, dict) or not isinstance(ns, dict):
        return []                                       # a snapshot from before CL58: nothing to compare
    out = []
    for key, head in RESUME_SECTIONS:
        if key not in os_ or key not in ns or os_[key] == ns[key]:
            continue
        out += ["", "## %s" % head] + (ns[key] or ["- (cleared since)"])
    return out


def _resume_docs(m, db_path=None):
    """The host's posts and comments, and each attached board's under `repo:<id>/` (so a change there shows too)."""
    posts, comments = _load_board_docs(m, db_path=db_path)
    posts, comments = dict(posts or {}), dict(comments or {})
    try:
        for b in _attached_boards()[0]:
            for pid, p in (b.get("posts") or {}).items():
                posts["repo:%s/%s" % (b["id"], pid)] = dict(p, _board=b["id"])
            for cid, c in (b.get("comments") or {}).items():
                comments["repo:%s/%s" % (b["id"], cid)] = dict(c, _board=b["id"])
    except Exception:  # noqa: BLE001 - never lose the host's delta over an attached board
        pass
    return posts, comments


def _is_q(p):
    return p.get("kind") == "question" or p.get("column") == "question"


def resume_delta_lines(old, new, since_hhmm, me=None):
    """What changed between two snapshots, grouped; [] when nothing did."""
    op, np_ = old.get("posts") or {}, new.get("posts") or {}
    steps, qs, sess, his, others = [], [], [], [], []

    def name(pid, p):
        return "%s%s" % (("%s " % p["part"]) if p.get("part") else "", (p.get("title") or pid)[:70]) + " (`%s`)" % pid

    for pid in sorted(set(op) | set(np_)):
        a, b = op.get(pid), np_.get(pid)
        if a == b:
            continue
        p = b or a
        bucket = qs if _is_q(p) else steps if (p.get("part") or p.get("train")) and not pid.split("/")[-1].startswith("_") else others
        if a is None:
            bucket.append("- new: %s, %s" % (name(pid, b), b.get("status") or b.get("column") or "open"))
            continue
        if b is None:
            bucket.append("- gone: %s" % name(pid, a))
            continue
        what = []
        for k in ("status", "column"):
            if a.get(k) != b.get(k):
                what.append("%s %s -> %s" % (k, a.get(k) or "-", b.get(k) or "-"))
        if a.get("session_id") != b.get("session_id"):
            sess.append("- %s: session %s -> %s" % (name(pid, b), a.get("session_id") or "none", b.get("session_id") or "none"))
        if not what and a.get("updated_at") != b.get("updated_at"):
            what.append("updated%s" % ((" by %s" % b["from"]) if b.get("from") and b.get("from") != me else ""))
        if what:
            bucket.append("- %s: %s" % (name(pid, b), ", ".join(what)))
    oa, na = old.get("alive"), new.get("alive")
    if oa is not None and na is not None and oa != na:
        sess += ["- now running: %s" % x for x in sorted(set(na) - set(oa))]
        sess += ["- ended: %s" % x for x in sorted(set(oa) - set(na))]
    oc, nc = old.get("comments") or {}, new.get("comments") or {}
    per_post = {}
    for cid in sorted(set(nc) - set(oc)):
        c = nc[cid]
        if c["his"]:
            his.append("- %s on %s: %s" % (cid, c["post_id"], c["text"]))
        elif c["from"] == "owner":
            his.append("- %s on %s: %s" % (cid, c["post_id"], "a Like" if c["like"] else "answered %s" % c["answer"] if c["answer"] else c["text"]))
        else:
            per_post.setdefault((c["post_id"], c["from"]), []).append(cid)
    for (pid, frm), ids in sorted(per_post.items()):
        others.append("- %s wrote %d comment%s on %s (%s)" % (frm or "someone", len(ids), "" if len(ids) == 1 else "s", pid,
                                                               ", ".join(ids[:3]) + (", ..." if len(ids) > 3 else "")))
    gone_open = sorted(set(old.get("open") or []) - set(new.get("open") or []))
    if gone_open:
        his.append("- answered since: %s" % ", ".join(gone_open))
    still = sorted(set(new.get("open") or []) & set(old.get("open") or []))
    out = resume_section_lines(old, new)
    for head, rows in (("Steps", steps), ("Sessions", sess), ("Questions", qs), ("The owner's comments", his),
                       ("Other writers", others)):
        if rows:
            out += ["", "## %s" % head] + rows
    if out and still:
        out += ["", "Still unanswered of his (from before): %s" % ", ".join(still)]
    return out


def resume_delta(m, role=None, session=None, db_path=None, cli=None, full=False, now=None, alive=None):
    """-> the text to print instead of the whole resume, or None (print it whole). Saves this read's snapshot either
    way, so the next resume of this session prints only what changed since this one."""
    if role not in RESUME_DELTA_ROLES:
        return None
    path = resume_state_path(session, role)
    if not path:
        return None                                     # no session id: every resume is whole
    now = now or datetime.now(timezone.utc)
    posts, comments = _resume_docs(m, db_path=db_path)
    if alive is None:
        alive = _alive_sessions() if _rule("alive_check", cli) == "claude_agents" else None
    snap = _resume_snapshot(posts, comments, alive)
    if role == "orchestrator":
        snap["sections"] = _resume_sections(m, posts, comments, db_path=db_path)
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None
    gen = _handover_gen(role)
    state = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "gen": gen, "db": db_path or "", "snap": snap}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        return None
    if full or not isinstance(old, dict) or old.get("gen", "") != gen or old.get("db", "") != (db_path or ""):
        return None                                     # the first of this session, a hand-over, or asked whole
    hhmm = str(old.get("at") or "")[11:16] or "?"
    rows = resume_delta_lines(old.get("snap") or {}, snap, hhmm, me=role or "orchestrator")
    if not rows:
        return "Resume: nothing new since %s UTC (the whole: `kit.py resume%s --full`).\n" % (
            hhmm, " --role %s" % role if role else "")
    return "\n".join(["# Resume: what changed since %s UTC (the whole: `kit.py resume%s --full`)" % (
        hhmm, " --role %s" % role if role else "")] + rows) + "\n"


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
    """[{id, title, roles, files, from, where, check, text, source}] from lesson Markdown: `## <id>: <title>`, then `roles:`,
    optional `files:` (globs), `from:` (where it was first written), `where: attached` (only a project that has another
    repo attached, or the kit's own) and `check:` (what enforces it, M7: test:/hook:/code:/refusal:) lines, then the
    lesson itself."""
    out, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = LESSON_HEAD.match(line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "roles": [], "files": [], "from": "", "where": "", "check": "", "replaces": "",
                   "replaced_by": "", "confirmed": "", "text": [], "source": source, "_file": source, "line": n}
            out.append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^(roles|files|from|where|check|replaces|replaced_by|confirmed)\s*:\s*(.*)$", line.strip(), re.I)
        if kv and not cur["text"]:
            k, v = kv.group(1).lower(), kv.group(2).strip()
            cur[k] = [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()] if k in ("roles", "files") else v
            continue
        if line.strip() or cur["text"]:
            cur["text"].append(line.rstrip())
    for c in out:
        c["roles"] = [r.lower() for r in c["roles"]]
        c["text"] = "\n".join(c["text"]).strip()
    return out


def all_lessons():
    """The live lessons: a lesson the archive holds as replaced is gone, even from an installed .kit/lessons.md (M8)."""
    out = []
    for p in _kit_lessons_paths():
        try:
            with open(p, encoding="utf-8") as fh:
                out += parse_lessons(fh.read(), os.path.relpath(p, ROOT))
        except OSError:
            continue
    gone = {x["id"] for x in archived_lessons() if x.get("replaced_by")}
    return [x for x in out if x["id"] not in gone]


def _lessons_archive():
    return os.path.join(_resolve_memory_dir(), "archive", "lessons.md")


def archived_lessons():
    try:
        with open(_lessons_archive(), encoding="utf-8") as fh:
            return parse_lessons(fh.read(), os.path.relpath(_lessons_archive(), ROOT))
    except OSError:
        return []


def _touches(globs, files):
    return any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in globs for f in files)


def _attaches_repos():
    """This project works on another repo too (one is attached), or it is the kit's own."""
    return bool(_is_kit_source_repo(ROOT) or _kit_config().get("portfolio") or os.path.isdir(os.path.join(ROOT, ".kit", "attached")))


def lessons_for(role, files=None):
    """The lessons a role needs: tagged with it (or `all`); for a part with files, a lesson with `files:` only when it
    touches one of them (a lesson with no files applies to every part)."""
    out = []
    for les in all_lessons():
        if role not in les["roles"] and "all" not in les["roles"]:
            continue
        if les["where"] == "attached" and not _attaches_repos():
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
    for x in les:
        why = _led_call("check_error", x.get("check"))
        if why:
            err.append("lesson %s (%s): %s" % (x["id"], x["source"], why))
        vague = _led_call("vague_remedy", x["text"], x.get("check"))
        if vague:
            err.append("lesson %s (%s): its remedy is %r: a lesson names the test, hook, refusal or code that keeps the mistake "
                       "from coming back (`check:` under its heading)" % (x["id"], x["source"], vague))
    for i, rel in _doc_only_lessons(les):
        err.append("lesson %s lives only in %s: move it into the memory, tagged with its roles: "
                   "`python3 -I .kit/kit.py lesson add %s --roles <roles> --from \"%s %s\" --title ... --text ...`"
                   % (i, rel, i, rel, i))
    return err


def _led_call(name, *args, **kw):
    """A kit/ledger.py function, or None when this install has no ledger (an old copy)."""
    led = _load_ledger_mod()
    f = getattr(led, name, None) if led is not None else None
    return f(*args, **kw) if f else None


def _whole_file_lessons():
    """The memory's lesson files written as one lesson a file (`lessons/<date>-<slug>.md`: symptom, cause, remedy; no
    `## <id>:` heading): each is an entry, its id the file's name, its check a `check:` line in it, its date in its name."""
    out = []
    for p in sorted(glob.glob(os.path.join(_resolve_memory_dir(), "lessons", "*.md"))):
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if parse_lessons(text):
            continue
        m = re.search(r"(?mi)^\s*check\s*:\s*(.+?)\s*$", text)
        t = re.search(r"(?m)^#+\s*(.+?)\s*$", text)
        out.append({"id": os.path.splitext(os.path.basename(p))[0], "title": t.group(1) if t else "", "roles": [], "files": [],
                    "from": "", "where": "", "check": m.group(1) if m else "", "text": text.strip(),
                    "source": os.path.relpath(p, ROOT), "_file": os.path.relpath(p, ROOT)})
    return out


def memory_entries():
    """M7: every memory entry a check can hold: the lessons (a heading each, or one a file), and the ledger's live rows
    (fields excepted)."""
    out = list(all_lessons()) + _whole_file_lessons()
    led = _load_ledger_mod()
    if led is not None:
        try:
            rows = led.load_entries(_resolve_memory_dir(), kit_root=ROOT)
        except (OSError, ValueError):
            rows = []
        out += [r for r in rows if r.get("status") == "active" and r.get("kind") in getattr(led, "CHECKED_KINDS", ())]
    return out


def memory_checks(run=False):
    """[{id, status: ok|fail|unchecked, why, since, checks}] for every memory entry; run=True runs the tests and keeps
    their results (.kit/run/memory-checks.json) for the resume and the hook, which never run one."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "check_entry"):
        return []
    ents = memory_entries()
    cache = led.read_checks_cache(ROOT)
    res = [led.check_entry(ROOT, e, run=run, cache=cache) for e in ents]
    if run:
        led.write_checks_cache(ROOT, res, {e["id"]: led.entry_check_value(e) or "" for e in ents})
    return res


def alarm_lines():
    """M10: (first, then) lines for the orchestrator's resume; a broken ledger is said, never hidden."""
    try:
        r = _led_call("alarm_lines", _resolve_memory_dir(), kit_root=ROOT)
    except Exception as e:
        return ["- the ledger's fix kinds and rules could not be read: %s" % e], []
    return r or ([], [])


def design_gate(cmd):
    """CL72: `kit.py launch` and `kit.py land` refuse while an item of resume's "First: what keeps coming back" has no
    `kit.py ledger design` entry: '' when none, else the refusal (each item, its ids, the command that records it)."""
    md = _resolve_memory_dir()
    own = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.environ.get("KIT_TEST") and os.path.abspath(md).startswith(own + os.sep):
        return ""                            # a test run never reads the real repo's ledger (its own temp one: yes)
    try:
        items = _led_call("open_items", md, kit_root=ROOT) or []
    except Exception as e:                   # a broken ledger is said, never hidden; it blocks nothing by itself
        print("(the ledger could not be read for the design gate: %s)" % e, file=sys.stderr)
        return ""
    if not items:
        return ""
    lines = ["NOT %s: %d item(s) of \"First: what keeps coming back\" have no design step yet (CL72: a section skipped "
             "is now a gate). Plan the step for each (TaskCreate {train, part}), record it, then %s again:" % (
                 cmd.upper(), len(items), cmd)]
    for it in items:
        lines.append("- %s (%s): `%s`" % (it["what"], ", ".join(it["ids"]) or it["kind"], it["command"]))
    return "\n".join(lines)


def memory_line():
    """The orchestrator's one line: entries checked, failing, unchecked."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "memory_line"):
        return ""
    return led.memory_line(memory_checks(run=False), led.read_checks_cache(ROOT).get("at", ""))


def _doc_only_lessons(les):
    """[(id, doc)] of the lessons a project doc names that the memory does not hold."""
    out = []
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
                out.append((i, rel))
    return out


def _refused_lines():
    """What `resume` says of refused lessons: one line a doc (`kit.py lessons check` names each with its command)."""
    docs = {}
    for i, rel in _doc_only_lessons(all_lessons()):
        docs.setdefault(rel, []).append(i)
    out = ["- " + e for e in lessons_check() if " lives only in " not in e]
    for rel, ids in docs.items():
        out.append("- %d only in %s (%s): `python3 -I .kit/kit.py lessons check` gives the command for each" % (len(ids), rel, ", ".join(ids)))
    return out


def lesson_add(lid, roles, title, text, files=(), frm="", check="", replaces=""):
    res = _lesson_add(lid, roles, title, text, files, frm, check)
    if res.get("ok") and replaces:
        r = memory_replace(res["lesson"], replaces)
        res.update({"replaced": r} if r.get("ok") else {"error": r.get("error")})
    return res


def _lesson_add(lid, roles, title, text, files=(), frm="", check=""):
    """A lesson into the memory. It names its check (M7): without one it is written "unchecked" and the answer proposes
    one; a remedy that is only "be careful" with no check is refused."""
    roles = [r.strip().lower() for r in roles if r.strip()]
    if not roles:
        return {"error": "a lesson needs its roles (--roles %s): one no role reads is never read" % ",".join(ROLES)}
    bad = [r for r in roles if r not in ROLES and r != "all"]
    if bad:
        return {"error": "unknown role %s (roles: %s, all)" % (", ".join(bad), ", ".join(ROLES))}
    lid = re.sub(r"[^\w.-]+", "-", str(lid)).strip("-")
    if not lid or not str(title).strip() or not str(text).strip():
        return {"error": "a lesson needs an id, a title and its text"}
    check = str(check or "").strip()
    why = _led_call("check_error", check)
    if why:
        return {"error": why}
    proposed = "" if check else (_led_call("propose_check", ROOT, title, text, list(files)) or "")
    vague = _led_call("vague_remedy", "%s %s" % (title, text), check)
    if vague:
        return {"error": "the remedy is %r: name what keeps the mistake from coming back (a test, a hook, a refusal in a "
                         "command, the code) with --check%s" % (vague, (", e.g. --check \"%s\"" % proposed) if proposed else ""),
                "proposed_check": proposed}
    if check:
        r = _led_call("check_entry", ROOT, {"id": lid, "check": check}, run=False)
        if r and r["status"] == "fail":
            return {"error": "--check %s: %s" % (check, r["why"])}
    d = os.path.join(_resolve_memory_dir(), "lessons")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.md" % lid)
    body = ["## %s: %s" % (lid, title.strip()), "roles: %s" % ", ".join(roles)]
    if files:
        body.append("files: %s" % ", ".join(files))
    if frm:
        body.append("from: %s" % frm)
    if check:
        body.append("check: %s" % check)
    body.append("confirmed: %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d"))     # M8: dated from the day it is written
    body += ["", text.strip(), ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body))
    out = {"ok": True, "lesson": lid, "path": os.path.relpath(path, ROOT)}
    if not check:
        out["unchecked"] = ("nothing runs it: it is given to sessions as unchecked. Add its check: a `check:` line under its "
                            "heading%s" % ((", proposed: `check: %s`" % proposed) if proposed else ""))
        out["proposed_check"] = proposed
    return out


def _lesson_lines(les):
    out = []
    led = _load_ledger_mod()
    note = getattr(led, "check_note", None) if led is not None else None
    cache = led.read_checks_cache(ROOT) if note else {}
    for x in les:
        out.append("- **%s** %s%s: %s%s" % (x["id"], x["title"], (" (files %s)" % ", ".join(x["files"])) if x["files"] else "",
                                            x["text"].replace("\n", " "), note(ROOT, x, cache) if note else ""))
    return out or ["- (none tagged for this role)"]


# ---------------------------------------------------------------- M8: replacing and forgetting in memory
MEMORY_REVIEW_DAYS = 90
MEMORY_REVIEW_POST = "q-memory-to-review"


def _memory_review_days():
    mem = (_kit_config().get("kit") or {}).get("memory") or {}
    try:
        return int(mem.get("review_days", MEMORY_REVIEW_DAYS))
    except (TypeError, ValueError):
        return MEMORY_REVIEW_DAYS


def _lesson_file(lid):
    """(path, lines, start, end) of the file holding lesson `lid`'s section, or None."""
    for p in _kit_lessons_paths():
        try:
            with open(p, encoding="utf-8") as fh:
                lines = fh.read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            m = LESSON_HEAD.match(line)
            if m and m.group(1) == lid:
                j = next((k for k in range(i + 1, len(lines)) if LESSON_HEAD.match(lines[k])), len(lines))
                return p, lines, i, j
    return None


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def _set_lesson_key(lid, key, value):
    """Put `key: value` among lesson `lid`'s meta lines (replacing one there). False: no file of the memory holds it."""
    hit = _lesson_file(lid)
    if not hit:
        return False
    p, lines, i, j = hit
    k = i + 1
    while k < j and re.match(r"^(roles|files|from|where|replaces|replaced_by|confirmed)\s*:", lines[k].strip(), re.I):
        if lines[k].strip().lower().startswith(key + ":"):
            lines[k] = "%s: %s" % (key, value)
            _write_lines(p, lines)
            return True
        k += 1
    lines.insert(k, "%s: %s" % (key, value))
    _write_lines(p, lines)
    return True


def memory_replace(winner, loser):
    """`winner` stays, `loser` leaves the memory for the archive with `replaced_by: winner` (a lesson or a ledger entry
    either side). A lesson leaves its file (the installed .kit/lessons.md keeps its text but `all_lessons` skips it)."""
    if not winner or not loser or winner == loser:
        return {"error": "replace needs two different ids: the one that wins, then the one that goes"}
    led, mdir = _load_ledger_mod(), _resolve_memory_dir()
    ledger_ids = {e["id"] for e in led.load_entries(mdir)} if led else set()
    lessons = {x["id"] for x in all_lessons()}
    if winner not in ledger_ids | lessons:
        return {"error": "%s: no live lesson or ledger entry has that id" % winner}
    if loser in ledger_ids and winner in ledger_ids:
        return led.replace(mdir, winner, loser)
    if loser in ledger_ids:                                          # a lesson wins over a ledger row
        entries = led.load_entries(mdir)
        led._retire(mdir, entries, loser, winner)
        led.save_entries(mdir, entries)
    elif loser in lessons:
        hit = _lesson_file(loser)
        if not hit:
            return {"error": "%s: its file was not found" % loser}
        p, lines, i, j = hit
        section = [l for l in lines[i:j]]
        while section and not section[-1].strip():
            section.pop()
        section.insert(1, "replaced_by: %s" % winner)
        installed = os.path.normpath(p) == os.path.normpath(os.path.join(ROOT, ".kit", "lessons.md")) and not _is_kit_source_repo(ROOT)
        rest = lines[:i] + lines[j:]
        if not installed and not any(l.strip() for l in rest) and \
                os.path.realpath(p).startswith(os.path.realpath(_resolve_memory_dir()) + os.sep):
            os.remove(p)                                             # a memory file that held only it
        elif not installed:                                          # the kit's update would put it back: the archive hides it
            _write_lines(p, rest)
        arch = _lessons_archive()
        os.makedirs(os.path.dirname(arch), exist_ok=True)
        old = open(arch, encoding="utf-8").read().rstrip("\n") if os.path.isfile(arch) else \
            "# Lessons archive (replaced, each with its successor)"
        with open(arch, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(old + "\n\n" + "\n".join(section) + "\n")
    else:
        return {"error": "%s: no live lesson or ledger entry has that id" % loser}
    if winner in lessons:
        _set_lesson_key(winner, "replaces", loser)
    else:
        entries = led.load_entries(mdir)
        for e in entries:
            if e["id"] == winner:
                e["replaces"] = loser
        led.save_entries(mdir, entries)
    return {"ok": True, "kept": winner, "archived": loser}


def _blame_dates(path):
    """{line number: commit date (YYYY-MM-DD)} for a file, from one `git blame`; {} outside git."""
    r = subprocess.run(["git", "-C", ROOT, "blame", "--line-porcelain", "--", os.path.relpath(path, ROOT)],
                       capture_output=True, text=True)
    out, n, when = {}, 0, ""
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        m = re.match(r"^[0-9a-f]{40} \d+ (\d+)", line)
        if m:
            n = int(m.group(1))
        elif line.startswith("committer-time "):
            when = datetime.fromtimestamp(int(line.split()[1]), tz=timezone.utc).strftime("%Y-%m-%d")
        elif line.startswith("\t"):
            out[n] = when
    return out


def memory_items():
    """Every live entry of the memory, lessons and ledger rows alike, as {id, text, files, theme, source, confirmed}:
    `confirmed` is its `confirmed:` date, else the date its `from:` names, else the day git last touched its heading."""
    out, blames = [], {}
    for x in all_lessons():
        when = x.get("confirmed") or (x["from"] if re.search(r"\d{4}-\d{2}-\d{2}", x.get("from") or "") else "")
        if not when:
            p = os.path.join(ROOT, x["source"])
            if p not in blames:
                blames[p] = _blame_dates(p)
            when = blames[p].get(x.get("line") or 0, "")
            if not when and os.path.isfile(p):                       # not committed yet: the day the file was written
                when = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"id": x["id"], "text": "%s. %s" % (x["title"], x["text"]), "files": x["files"], "theme": "",
                    "source": x["source"], "confirmed": when, "kind": "lesson"})
    led = _load_ledger_mod()
    if led:
        out += [dict(i, kind="ledger", source=os.path.relpath(i["source"], ROOT) if i["source"] else "")
                for i in led.entry_items(led.load_entries(_resolve_memory_dir(), kit_root=ROOT))]
    return out


def _guide_paths():
    """The guides and memory files that may say what ranks first: CLAUDE.md, AGENTS.md and the other top-level .md,
    docs/ (not the prompts), the kit's own guides (kit/ in the kit's repo, .kit/ in a project), the memory's .md."""
    ps = glob.glob(os.path.join(ROOT, "*.md"))
    ps += [p for p in glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
           if os.sep + "prompts" + os.sep not in p and os.sep + "posts" + os.sep not in p]
    kit_dir = os.path.join(ROOT, "kit") if _is_kit_source_repo(ROOT) else os.path.join(ROOT, ".kit")
    ps += glob.glob(os.path.join(kit_dir, "*.md")) + glob.glob(os.path.join(kit_dir, "skills", "**", "*.md"), recursive=True)
    mdir = _resolve_memory_dir()
    ps += [p for p in glob.glob(os.path.join(mdir, "**", "*.md"), recursive=True) if os.sep + "archive" + os.sep not in p]
    return sorted(set(ps))


def memory_conflicts():
    """What the memory says twice in disagreement: [{kind, ids|where, why, ask}] (M8). `contradiction`: two live entries
    on the same subject, opposite verbs or one setting with two values; `still-live`: an entry another names in
    `replaces:`; `authority`: two docs each ranking first; `repeat`: one authority claimed in several files."""
    led = _load_ledger_mod()
    if not led:
        return []
    items = memory_items()
    out = []
    live = {i["id"] for i in items}
    for x in all_lessons():
        if x.get("replaces") and x["replaces"] in live:
            out.append({"kind": "still-live", "ids": [x["id"], x["replaces"]],
                        "why": "%s replaces %s, which is still live" % (x["id"], x["replaces"]),
                        "ask": "python3 -I .kit/kit.py lessons replace %s %s" % (x["id"], x["replaces"])})
    for c in led.contradictions(items):
        out.append({"kind": "contradiction", "ids": [c["a"], c["b"]], "why": c["why"],
                    "where": [c["a_source"], c["b_source"]],
                    "ask": "which one wins? `python3 -I .kit/kit.py lessons replace %s %s` (%s wins) or `... replace %s %s`"
                           % (c["a"], c["b"], c["a"], c["b"], c["a"])})
    for c in led.authority_conflicts(led.authority_claims(_guide_paths(), root=ROOT)):
        out.append({"kind": "authority" if c["kind"] == "conflict" else "repeat", "where": c["where"], "why": c["why"],
                    "ask": "which one ranks first? Keep that claim in one file, the others point to it"})
    return out


def memory_stale(days=None):
    days = _memory_review_days() if days is None else int(days)
    led = _load_ledger_mod()
    return days, (led.stale(memory_items(), days) if led else [])


def memory_keep(eid):
    """`eid` still holds: a lesson gets `confirmed: <today>`, a ledger row `confirmed_at`."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(x["id"] == eid for x in all_lessons()):
        return {"ok": True, "entry": eid, "confirmed": today} if _set_lesson_key(eid, "confirmed", today) else \
            {"error": "%s: its file was not found" % eid}
    led = _load_ledger_mod()
    return led.keep(_resolve_memory_dir(), eid) if led else {"error": "ledger.py not found"}


def memory_review_post(days=None, db_path=None):
    """The entries nobody confirmed for more than `days` days, as one question post on the board (kept, not doubled:
    an open one is updated). Nothing is deleted: the default is to keep them."""
    days, old = memory_stale(days)
    if not old:
        return {"ok": True, "post": None, "count": 0, "days": days}
    body = ("%d memory entries nobody confirmed for more than %d days (kit config `kit.memory.review_days`). Nothing "
            "leaves the memory unless you say so.\n\n" % (len(old), days)) + "\n".join(
        "- **%s** (%s, %s): %s. Still true: `python3 -I .kit/kit.py lessons keep %s`; replaced: `python3 -I .kit/kit.py "
        "lessons replace <new> %s`" % (i["id"], i["source"] or i["kind"], ("%d days" % i["age"]) if i["age"] is not None
                                        else "never dated", _short(i["text"], 110), i["id"], i["id"]) for i in old)
    title = "Memory to review: %d entries unconfirmed for %d+ days" % (len(old), days)
    cur = _all_posts(db_path).get(MEMORY_REVIEW_POST)
    if cur and str(cur.get("status") or "open") == "open":
        ok = _board_write("update", "posts", MEMORY_REVIEW_POST, {"title": title, "body": body, "updated_at": _now_z()})
        return {"ok": ok, "post": MEMORY_REVIEW_POST, "count": len(old), "days": days, "updated": True}
    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    g = _post_gate()
    doc = {"title": title, "body": body, "kind": "question", "column": "question", "options": ["Keep them all", "Review each"],
           "default_option": "Keep them all", "deadline": deadline, "status": "open", "priority": 2, "train": "memory",
           "from": "orchestrator", "created_at": _now_z(), "needs": "direction"}
    err = g.check_post("q", doc, None, _kit_config())[2] if g else None
    if err:
        return {"error": err}
    ok = _board_write("set", "posts", MEMORY_REVIEW_POST, doc)
    return {"ok": ok, "post": MEMORY_REVIEW_POST, "count": len(old), "days": days} if ok else \
        {"error": "the board did not take it (is the board server running?)"}


def memory_check_text(days=None):
    """`kit.py lessons check`'s report: refused lessons, then what disagrees (each with the question it asks), then the
    count of entries waiting for a review."""
    out = ["REFUSED: " + e for e in lessons_check()]
    for c in memory_conflicts():
        label = {"contradiction": "CONTRADICTS", "still-live": "STILL LIVE", "authority": "AUTHORITY", "repeat": "SAID TWICE"}[c["kind"]]
        out.append("%s: %s. %s" % (label, c["why"], c["ask"][:1].upper() + c["ask"][1:]))
    days, old = memory_stale(days)
    if old:
        out.append("REVIEW: %d entries unconfirmed for more than %d days (%s): `python3 -I .kit/kit.py lessons review --post` "
                   "lists them on the board" % (len(old), days, ", ".join(i["id"] for i in old[:8]) + (", ..." if len(old) > 8 else "")))
    return out


def _changed_files(root=ROOT, base=None):
    """The files this branch changes against the trunk (committed and not)."""
    files = set()
    trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    for ref in ([base] if base else []) + ["origin/" + trunk, trunk, "origin/main", "origin/master", "main"]:
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
    what it printed. Who runs it comes from PLATFORM_RULES['real_project_run']."""
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
    who = _rule("real_project_run")
    if who == "orchestrator":
        return ["real_project: your change touches %s; the run on a real project (%s) is pending: the orchestrator runs it "
                "from its session (it holds both repos) and writes {\"project\", \"command\", \"output\"} in the post's "
                "real_project; a part never attaches a repo (CL38)" % (", ".join(areas), cons)]
    if who == "part":
        return ["real_project: your change touches %s; run the changed command on %s in a scratch local clone or read-only "
                "(never push) and write {\"project\", \"command\", \"output\"} in the post's real_project "
                "(`python3 -I .kit/board_cli.py update posts <post> --data '{\"real_project\": ...}'`) before Done"
                % (", ".join(areas), cons)]
    return ["real_project: your change touches %s; the dry run on a real project (%s) is pending: run it in a local clone "
            "(never push) and write {\"project\", \"command\", \"output\"} in the post's real_project"
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
    mine, _ = _own_step()                       # CL50: the worktree's recorded step first; KIT_POST only when it agrees
    if mine in posts:
        return mine
    br = _git_run("branch", "--show-current").stdout.strip().lower()
    if not br:
        return None
    hits = [pid for pid, p in posts.items() if isinstance(p, dict) and str(p.get("branch") or "").lower() == br]
    if not hits:
        hits = [pid for pid, p in posts.items() if isinstance(p, dict) and not pid.startswith("_") and (
            pid.lower() in br or (p.get("part") and re.search(r"(^|[-/_])%s($|[-/_])" % re.escape(_slug(str(p["part"]), 24)), br)))]
    return hits[0] if len(hits) == 1 else None


def _acceptance(rel, post=None):
    """The acceptance lines of a step's task (`Acceptance:` or `bars`), for its review: from its post (CL54), else its prompt."""
    text = str((post or {}).get("task") or "").strip()
    if not text:
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
    """The orchestrator's launch workflow for its platform (read from PLATFORM_RULES), attached-board steps included."""
    pl = _platform()
    lmode = _rule("launcher", pl)
    tlist = _rule("task_list", pl)
    art = _rule("artifact_steps", pl)
    cards_mode = _rule("cards", pl)
    alone = lmode == "self"
    head = {"create_session": "web: Claude Code on the web", "claude_bg": "claude", "invoke_subagent": "agy"}.get(
        lmode, "%s: no session can be started here" % pl)
    lines = ["## How you launch (%s)" % head]
    lines.append("1. Plan: %s`python3 -I .kit/kit.py step new --train T --part P --title ... --task-file F`: the task is written once, "
                 "on the post; its prompt is generated from the post at launch and committed (CL54): to change a task, edit the post only."
                 % ("TaskCreate {train, part} (the hook writes the post) or " if tlist == "claude_tasks" else ""))
    if lmode == "create_session":
        lines.append("2. `python3 -I .kit/kit.py launch <post>`: it marks the post and prints the `create_session` call "
                     "(claude-code-remote); make it, then `python3 -I .kit/kit.py launch <post> --session-id <its id>`.")
    elif alone:
        lines.append("2. You do the step yourself, from its prompt, on a branch of its own.")
    elif lmode == "invoke_subagent":
        lines.append("2. `step new` prints the `invoke_subagent` call after `step prompt <post>` (it generates the prompt from "
                     "the post and commits it): run that first, then make the call (the device cap: "
                     "`python3 -I .kit/kit.py capacity`, past it the step waits planned).")
    else:
        lines.append("2. `python3 -I .kit/kit.py launch <post>`: it marks the post and starts `claude --bg` in a worktree "
                     "(the device cap refuses past it: the step waits planned).")
    if _attaches_repos():
        real_note = (" The session never attaches a repo (CL38): the real-project run on the projects it tests on is yours."
                     if _rule("real_project_run", pl) == "orchestrator" else "")
        lines.append("3. A step on an attached board (another repo's train, e.g. the kit's `cli`): the same command from here. "
                 "launch finds the post on that board and runs the launch in that repo's clone next to this one (missing: it "
                 "says the `git clone`); that clone starts no seeded board and never auto-pushes a board-mirror. A public board is "
                 "cloned read-only, nothing to attach: when its push is refused, the create_session call launch prints carries the "
                 "prompt's text (CL40)." + real_note)
    if art and (_attaches_repos() or attached_board()):
        lines.append("4. With an Artifact board (yours or an attached one), make the ArtifactData batch launch prints (the post and the Trains row).")
    if cards_mode == "agent":
        lines.append("5. Cards (`_dashboard`, `_trains`): rebuild with the `kit-cards` agent at each launch and Done.")
    else:
        lines.append("5. Cards (`_dashboard`, `_trains`): the local board server rebuilds them on each write (`python3 -I .kit/kit.py cards` checks them and lists sessions to archive).")
    lines.append("6. Done%s: `kit.py done <post>` must say OK%s; then %s: %s."
                 % ("" if alone else " comes as a message",
                    " (a change to compact, install, resume, hooks or artifact needs its real-project dry run)" if _attaches_repos() else "",
                    "TaskUpdate completed" if tlist == "claude_tasks" else "`python3 -I .kit/kit.py step complete <post>`",
                    "its review's prompt is written (`.kit/run`), and nothing else here can run it" if alone else "its review starts by itself"))
    return lines[:1] + [re.sub(r"^\d+\.", "%d." % n, x) for n, x in enumerate(lines[1:], 1)]


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


def _map_cmd(a):
    """kit.py map: scripts/kit_map.py (the kit's repo) or .kit/kit_map.py (an installed kit), next to this file."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import kit_map  # noqa: E402
    except ImportError:
        print("STOP: kit_map.py is not next to kit.py (%s): `python3 -I install.py . --update`" % here)
        return 2
    argv = ["--root", a.root or ROOT]
    for flag, val in (("--out", a.out), ("--config", a.map_config)):
        if val:
            argv += [flag, val]
    argv += (["--check"] if a.check else []) + (["--no-generators"] if a.no_generators else [])
    argv += ["--steps", str(a.steps)] if getattr(a, "steps", 0) else []
    return kit_map.main(argv)


def scan_fix_commits(since=None, root=ROOT):
    """M10: the commits that say which kind of bug they fix (`Fix-kind: <kind>` in the message) go into the ledger, once
    each; {ok, recorded, unnamed}: `unnamed` the commits that say they fix a bug without naming its kind. `since`: a ref
    (default: this branch's own commits, from where it left the trunk)."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "commit_fixes"):
        return {"error": "ledger.py has no fix kinds (an old copy)"}
    base = since
    if not base:
        trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
        for ref in ("origin/" + trunk, trunk):
            r = subprocess.run(["git", "-C", root, "merge-base", "HEAD", ref], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                base = r.stdout.strip()
                break
    if not base:
        return {"ok": True, "recorded": [], "unnamed": [], "why": "no trunk to start from: --since <ref>"}
    r = subprocess.run(["git", "-C", root, "log", "--format=%H%x1f%B%x1e", "%s..HEAD" % base], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": "git log %s..HEAD: %s" % (base, (r.stderr or "").strip()[:200])}
    named, unnamed = led.commit_fixes(r.stdout)
    rec = []
    for sha, kind, subject in named:
        res = led.record_fix(_resolve_memory_dir(), kind, subject, "commit " + sha[:9])
        if res.get("error"):
            return res
        if not res.get("existing"):
            rec.append(res["entry"]["id"])
    return {"ok": True, "recorded": rec, "unnamed": [{"commit": sha[:9], "subject": subj[:100]} for sha, subj in unnamed]}


def record_step_fix(p, pid):
    """M10: `done` for a step that fixes a bug: its post's `fixes` (`<kind>: <what was broken>`) is one fix of that
    kind in the ledger. [] or the errors."""
    v = str(p.get("fixes") or "").strip()
    if not v or v.lower() in ("none", "no", "-", "(none)"):
        return []
    kind, _, what = v.partition(":")
    if not what.strip():
        return ["fixes: `<kind>: <what was broken>` (the kind a short theme: platform leak, silent error, two copies out of sync)"]
    res = _led_call("record_fix", _resolve_memory_dir(), kind, what.strip(), pid)
    return [res["error"]] if isinstance(res, dict) and res.get("error") else []


def decision_breaks(files, root=ROOT):
    """M10: the owner's decisions over the files this branch changes whose check fails now: done refuses the step."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "decisions_for_files"):
        return []
    out = []
    for d in led.decisions_for_files(_resolve_memory_dir(), files, kit_root=root):
        if not d["check"]:
            continue
        r = led.check_entry(root, d, run=True)
        if r["status"] == "fail":
            out.append("the owner's decision %s (%s) does not hold with this branch: %s. Make it hold, or ask him (`kit.py ask`) before changing it"
                       % (d["id"], d["summary"][:120], r["why"][:200]))
    return out


def plan_design(theme, post="", train="", db_path=None):
    """M10: the step for a kind fixed three times (or a rule he said twice): `post` names one already planned, else
    `train` makes it (`step new`); its fixes and rules then name it and the kind stops being raised."""
    led = _load_ledger_mod()
    if led is None or not hasattr(led, "fix_kinds"):
        return {"error": "ledger.py has no fix kinds (an old copy)"}
    mdir, th = _resolve_memory_dir(), led.kind_key(theme)
    if not th:
        return {"error": "design needs the kind: `kit.py ledger design \"<kind>\" --post <step>|--train <train>`"}
    made = None
    if not post:
        if not train:
            return {"error": "design: --post <the step planned for it> or --train <train> (it makes the step)"}
        k = next((x for x in led.fix_kinds(mdir, kit_root=ROOT) if x["kind"] == th and x["count"]), None)
        r = next((x for x in led.owner_rules(mdir, kit_root=ROOT) if x["theme"] == th), None)
        if k:
            title, task = "Design: make the %s kind of bug impossible" % th, led.design_task(k)
        elif r:
            title = "Check: %s" % r["summary"][:60]
            task = ("The owner said it %d time(s): %s. Nothing enforces it: make it a check (a hook, a refusal, a test) that fails "
                    "when it is broken, never another memory line." % (r["count"], "; ".join('"%s"' % q for q in r["quotes"])))
        else:
            return {"error": "no open fix or rule of kind %r (`kit.py ledger kinds`)" % th}
        made = step_new(train, "D-" + _slug(th, 20), title, task=task, db_path=db_path)
        if made.get("error"):
            return made
        post = made.get("post")
    res = led.mark_designed(mdir, th, post)
    if made:
        res["step"] = made
    return res


def _load_ledger_mod():
    for d in (os.path.join(ROOT, ".kit"), os.path.join(ROOT, "kit"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit")):
        p = os.path.join(d, "ledger.py")
        if os.path.isfile(p):
            if d not in sys.path:
                sys.path.insert(0, d)
            import ledger as _ledger  # noqa: E402
            return _ledger
    return None


def _not_his_yes(m, cid, db_path=None):
    """Why this comment is not the owner's yes to a principle (None: it is his, on the board's copy here)."""
    if not cid:
        return "confirm needs --by <id of the owner's comment or Like that says yes>"
    c = _load_board_docs(m, db_path=db_path)[1].get(cid)
    if not isinstance(c, dict):
        return "no comment %s on the board's copy here (an Artifact board: pull it first)" % cid
    if c.get("from") != answer_delay().owner:
        return "comment %s is from %r, not the owner" % (cid, c.get("from"))
    said = re.sub(r"^\s*answer\s*:\s*", "", str(c.get("answer") or c.get("text") or ""), flags=re.I).strip()
    if re.match(r"(?:leave them|no|not yet|don't|stop)\b", said, re.I):           # `ledger moves --ask` offers "Leave them"
        return "comment %s is his no: %r" % (cid, said[:60])
    return None


def _principle_lines(role, train=None, files=None):
    lines = []
    led = _load_ledger_mod()
    if led is not None:
        lines.extend(led.format_resume(_resolve_memory_dir(), role, train=train, files=files, kit_root=ROOT))
    pr = principles_for(role, train)
    if pr:
        lines += ["", "## Principles (scope: all, %s%s)" % (role, (", " + train) if train else "")] + pr
    return lines


# ---------------------------------------------------------------- one prompt: resume prints what this session needs
_MARK = re.compile(r"^<!--\s*(start|case)\s*:\s*(.+?)\s*-->\s*$", re.M)


def orchestrator_guide(platform=None):
    """The orchestrator's guide of this platform (PLATFORM_RULES['orchestrator_guide']), or None."""
    for rel in _rule("orchestrator_guide", platform):
        p = os.path.join(ROOT, ".kit", rel)
        if os.path.isfile(p):
            return p
    return None


def guide_parts(text):
    """(start [(situations, text)], cases [(id, title, text)]) of a guide: its blocks, each under its marker."""
    marks = list(_MARK.finditer(text))
    start, cases = [], []
    for k, mk in enumerate(marks):
        body = text[mk.end():marks[k + 1].start() if k + 1 < len(marks) else len(text)].strip("\n")
        if mk.group(1) == "start":
            start.append(([x.strip() for x in mk.group(2).split(",")], body))
        else:
            cid, _, title = mk.group(2).partition("|")
            cases.append((cid.strip(), title.strip(), body))
    return start, cases


def guide(case=None):
    p = orchestrator_guide()
    if not p:
        return "no orchestrator guide for this platform (%s): install.py --update\n" % _platform()
    with open(p, encoding="utf-8") as fh:
        _, cases = guide_parts(fh.read())
    hit = [c for c in cases if c[0] == str(case or "").strip().lower()]
    if hit:
        return "# %s\n%s\n" % (hit[0][1], hit[0][2])
    return "\n".join(["%s: %s" % (c, t) for c, t, _ in cases] or ["(this guide has no cases: read %s)" % os.path.relpath(p, ROOT)]) + "\n"


def situation(posts, comments, session=None, platform=None):
    """What this orchestrator session is: `first` (no welcome of this platform on the board), `takeover` (another session
    holds the board: a successor after a context limit, or the first here of a project running elsewhere), `same` (it
    started already and runs resume again; a compaction never makes one: the hooks hand over before it, kit_pre_compact.py). A platform has one first and any number of takeovers."""
    pl = platform or _platform()
    rt = _rule("welcome_runtime", pl)
    named = str((posts.get("_orchestrator") or {}).get("session_id") or "")
    # a board older than the welcome has none: the session it names says a session of this platform was here
    was_here = {"web": named.startswith(("session_", "cse_")), "cli": named.endswith("-orchestrator")}.get(pl, False)
    out = [] if was_here or any(re.match(r"welcome-%s-\d+$" % re.escape(rt), str(k)) for k in comments) else ["first"]
    if named and session and named == session:
        return out + ["same"]
    return out + (["takeover"] if named or not out else [])


def split_lines(posts):
    """Open steps the split check flags (written before it, or grown by comments since): split before launching more."""
    out = []
    for pid, p in sorted(posts.items()):
        if pid.startswith("_") or not isinstance(p, dict) or p.get("kept_whole") or p.get("removed"):
            continue
        if str(p.get("status") or "").lower() in ("complete", "done", "dropped") or str(p.get("column") or "") == "done":
            continue
        why = split_check(p.get("subtasks"))
        if why:
            out.append("- `%s` (%s): %s" % (pid, p.get("part") or pid, why.replace("\n", "\n  ")))
    return ["", "## Steps that are several steps (split them: `kit.py step new` per group, then trim the step)"] + out if out else []


def start_lines(posts, comments, session=None):
    p = orchestrator_guide()
    if not p:
        return []
    with open(p, encoding="utf-8") as fh:
        start, cases = guide_parts(fh.read())
    if not start:
        return ["", "## Your start", "Read `%s` (this platform's guide has no sections)." % os.path.relpath(p, ROOT)]
    sit = situation(posts, comments, session)
    say = {"first": "the first session on this platform here", "takeover": "taking over from another session",
           "same": "you already started: only what always holds"}
    out = ["", "## Your start (%s)" % ", ".join(say[x] for x in sit)]
    out += [t for when, t in start if "always" in when or set(when) & set(sit)]
    if cases:
        out += ["", "## When a case comes up: `python3 -I .kit/kit.py guide <case>`"] + ["- %s: %s" % (c, t) for c, t, _ in cases]
    return out


def owner_lines(platform=None):
    """What the owner can do where he is (PLATFORM_RULES['owner_surface']), then the project's own (kit.owner_can, kit.owner_cannot)."""
    k = _kit_config().get("kit") or {}
    can, cannot, how = _rule("owner_surface", platform)
    can = list(can) + [str(x) for x in _as_list(k.get("owner_can"))]
    cannot = list(cannot) + [str(x) for x in _as_list(k.get("owner_cannot"))]
    out = ["", "## What the owner can do here", "- He can: " + "; ".join(can) + "."]
    if cannot:
        out.append("- He cannot: " + "; ".join(cannot) + ". Never ask him to.")
    return out + (["- " + how] if how else [])


def autonomy_lines():
    g = _post_gate()
    return ["", "## How much you decide alone (kit.autonomy)"] + g.autonomy_lines(_kit_config()) if g else []


def _board_answers():
    """The board server of this project answers (a part's worktree has no database of its own)."""
    try:
        return bool(_board_request(_resolve_board_url(os.environ.get("KIT_BOARD_URL") or _kit_config().get("board_url")), "db/posts?limit=1", token=_resolve_board_token(None)))
    except Exception:
        return False


def git_copy_lines(m, db_path=None, root=None):
    """How far git's copy of the board is behind what is here: git is the record, the Artifact and the local server are
    two ways to use it. Nothing is fetched; it reads this clone. A board attached to another project's Artifact is
    that project's session to save (web_takeover.owns_board): nothing here."""
    root = root or ROOT
    a = attached_board()
    if a and a.get("prefix"):
        return []
    db = db_path or os.path.join(root, ".kit", "board_data", "board.db")
    if not os.path.isfile(db):
        if git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + BRANCH).returncode == 0 and not _board_answers():
            return ["git copy: THIS CLONE HAS NO BOARD YET and git has one (origin/%s): everything above is empty because it was not "
                    "read. `python3 -I .kit/web_takeover.py` first (on the owner's computer: `./.kit/up`), then this command again; "
                    "answer nothing before" % BRANCH]
        return []
    if not os.path.isdir(os.path.join(m, "board")):
        return ["git copy: none in this clone (`python3 -I .kit/kit.py init`): what is written here is not in git yet"]
    out, here = [], {}
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        for coll, did, raw in conn.execute("SELECT collection, doc_id, data FROM docs WHERE collection IN ('posts','comments')"):
            here[(coll, did)] = json.loads(raw)
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    there = {(c, i): d for c in ("posts", "comments") for i, d in load_dir(os.path.join(m, "board"), c).items()}
    behind = sum(1 for k, d in here.items() if there.get(k) != d)
    if behind:
        out.append("git copy: %d document(s) here are not in git (last copy %s): `python3 -I .kit/kit.py keep`" % (
            behind, read_state(m).get("cursor") or "never"))
    n = _unpushed(m)
    if n:
        out.append("git copy: %d commit(s) of the board not on the remote: `python3 -I .kit/kit.py keep`" % n)
    return out


def _unpushed(m):
    """Commits of the mirror its remote does not have, by this clone's remote-tracking ref (written by each push). No
    ref: 0, not a guess (a clone that fetches only main keeps an old one, or none)."""
    r = git(m, "rev-list", "--count", "refs/remotes/origin/%s..HEAD" % BRANCH)
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def keep(m, db_path=None):
    """`kit.py keep`: the board here into git, committed and pushed, the same way the web's save does it (never for a
    board that is not this session's: web_takeover.owns_board). -> (text, ok)"""
    if not os.path.isfile(db_path or os.path.join(ROOT, ".kit", "board_data", "board.db")):
        return "git copy: nothing to keep (no local copy of the board here)", True
    w = os.path.join(ROOT, ".kit", "web_takeover.py")
    if not os.path.isfile(w):
        return "git copy: NOT KEPT (.kit/web_takeover.py is missing: install.py --update)", False
    try:
        r = subprocess.run([sys.executable, "-I", w, "save"], capture_output=True, text=True, timeout=330, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as e:
        return "git copy: NOT KEPT (%s)" % e, False
    txt = (r.stdout + r.stderr).strip()
    return txt or "git copy: kept", r.returncode == 0


SUB_STATES = ("open", "doing", "done")


def task_pieces(text):
    """The pieces a task names itself: its lines numbered 1. 2. 3. (2 to 30, in order, not indented, not in a code block). Anything
    else is prose, and prose is never cut into pieces."""
    text = re.sub(r"(?ms)^\s*```.*?^\s*```\s*$", "", str(text or ""))
    rows = re.findall(r"(?m)^(\d{1,2})[.)][ \t]+(\S.*)$", text)
    if not 2 <= len(rows) <= 30 or [int(n) for n, _ in rows] != list(range(1, len(rows) + 1)):
        return []
    out = []
    for n, t in rows:
        line = t
        t = re.sub(r"\s+", " ", t.replace("**", "").replace("`", "")).strip()
        t = re.split(r"(?<=[^.\s]{3}[.:;])\s", t, maxsplit=1)[0].rstrip(" .:;") or t
        if len(t) > 100:
            t = t[:100].rsplit(" ", 1)[0] + "..."
        if any(t.lower() == x["title"].lower() for x in out):
            t = "%s (%s)" % (t, n)
        files = sorted(set(re.findall(r"(?<![\w/.-])(?:(?:[\w.-]+/)+[\w-][\w.-]*|[\w-][\w.-]+)\.[A-Za-z]\w{0,5}\b", re.sub(r"https?://\S+", "", line))))
        out.append(dict({"n": int(n), "title": t, "status": "open", "from": "task"}, **({"files": files[:6]} if files else {})))
    return out


def pieces_of(p):
    """A step's pieces, each with the number that names it (kept for good: a piece that goes never renumbers the others)."""
    rows = [dict(x) for x in _as_list(p.get("subtasks")) if isinstance(x, dict) and x.get("title")]
    used = {x["n"] for x in rows if isinstance(x.get("n"), int) and x["n"] > 0}
    for i, x in enumerate(rows):
        if not (isinstance(x.get("n"), int) and x["n"] > 0):
            # CL41: never a number a later piece already holds (an unnumbered row before a numbered one made two "1.")
            x["n"] = max([i] + [y["n"] for y in rows[:i]] + list(used)) + 1
            used.add(x["n"])
    return rows


def pieces_open(p):
    return [x for x in pieces_of(p) if x.get("status") not in ("done", "dropped")]


_PLAIN_WORDS = set("""that this with from what when where which while each every their there them they then than your have here
other about into over under only also just more most some such both does done make made must were will would could should after
before again once very much many like said says thing things time times part step post piece pieces run runs""".split())


def _words(t):
    return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
            for w in re.findall(r"[a-z]{4,}", str(t or "").lower())} - _PLAIN_WORDS


def pieces_unchecked(p):
    """A piece marked done that the post's own not_checked names (CL42: piece 3 asked for "another repository, another environment,
    network off", and not_checked listed exactly those while the piece was done). Errors: finish it, or drop it with the reason."""
    task = re.sub(r"(?ms)^\s*```.*?^\s*```\s*$", "", str(p.get("task") or ""))
    lines = {int(n): t for n, t in re.findall(r"(?m)^(\d{1,2})[.)][ \t]+(\S.*)$", task)}
    out = []
    for x in pieces_of(p):
        if x.get("status") != "done":
            continue
        mine = _words(lines.get(x["n"]) if x.get("from") == "task" and lines.get(x["n"]) else x.get("title"))
        for nc in _as_list(p.get("not_checked")):
            both = mine & _words(nc)
            if len(both) >= 3 and len(both) >= 0.4 * len(mine):
                out.append("piece %d is marked done, but not_checked says %r (both name: %s). Do it, or say it is not done: "
                           "`python3 -I .kit/kit.py sub %s %d --status drop --why \"...\"`" % (
                               x["n"], str(nc)[:90], ", ".join(sorted(both)[:5]), p.get("id") or "<post>", x["n"]))
                break
    return out


def subtask(pid, title, status="open", was="", db_path=None, why="", known=False):
    """`kit.py sub <post> "<piece>" | <number>... [--status open|doing|done|drop]`: the pieces of a step, lines on its post (no post,
    prompt, review or proof of their own). A piece is its number or its title: the same title again is the same line, whichever session
    writes it. One that the task names is dropped with a reason he can read. `known`: from a task list, whose own tasks are no new piece
    where the task has named them."""
    names = [title] if isinstance(title, str) else [str(x) for x in title]
    if not all(x.isdigit() for x in names):
        names = [re.sub(r"\s+", " ", " ".join(names)).strip()[:120]]
    if not all(names) or not names or status not in SUB_STATES + ("drop",):
        return {"error": "a piece has a number or a title, and a status: %s or drop" % ", ".join(SUB_STATES)}
    p = _get_post(pid, db_path=db_path)
    if not p:
        return {"error": "no post %s on the board" % pid}
    rows = pieces_of(p)
    same = lambda a, b: a.strip().lower() == b.strip().lower()  # noqa: E731
    for t in names:
        hit = next((x for x in rows if t.isdigit() and x["n"] == int(t)), None) or next(
            (x for x in rows if same(x["title"], was or t)), None) or next((x for x in rows if same(x["title"], t)), None)
        if t.isdigit() and not hit:
            return {"error": "%s has no piece %s: %s" % (pid, t, ", ".join(str(x["n"]) for x in rows) or "none")}
        fixed = bool(hit) and hit.get("from") == "task"
        if status == "drop":
            if fixed and not str(why or "").strip():
                return {"error": "piece %d is the task's: drop it with --why \"<why it is not needed>\" (he reads it)" % hit["n"]}
            if fixed:
                hit.update(status="dropped", why=re.sub(r"\s+", " ", why).strip()[:200])
            else:
                rows = [x for x in rows if x is not hit]
        elif hit:
            # a piece named again by a session that takes over is not undone by it; the task's words for a piece stay
            hit.update(title=hit["title"] if fixed or t.isdigit() else t, status=hit.get("status") if status == "open" else status)
        elif known and any(x.get("from") == "task" for x in rows):
            continue
        elif len(rows) >= 30:
            return {"error": "%s has 30 pieces: a step that needs more is several steps" % pid}
        else:
            rows.append({"n": max([x["n"] for x in rows] + [0]) + 1, "title": t, "status": status})
    if rows != _as_list(p.get("subtasks")) and not _board_write("update", "posts", pid, {"subtasks": rows}):
        return {"error": "the board did not take it (is the board server running?)"}
    res = {"post": pid, "done": sum(x["status"] == "done" for x in rows), "of": len([x for x in rows if x["status"] != "dropped"])}
    # CL44 (item 10): auto-checkpoint on the step's own branch when a piece is finished so progress is committed in git.
    if (status == "done" and db_path is None and os.environ.get("KIT_AUTO_CHECKPOINT") != "0"
            and (_kit_config().get("kit") or {}).get("auto_checkpoint", True) is not False):
        cur_br = _git_run("branch", "--show-current").stdout.strip()
        named_br = str(p.get("branch") or "").strip()
        part_slug = _slug(str(p.get("part") or ""), 24)
        on_mine = bool(cur_br) and (
            (named_br and cur_br == named_br)
            or (len(part_slug) >= 2 and re.search(r"(^|[-/_])%s($|[-/_])" % re.escape(part_slug), cur_br, re.I))
        )
        if on_mine:
            ck = checkpoint(post=pid, message="piece %s done" % ", ".join(names), quiet=True, on_step_branch=True)
            if ck.get("committed"):
                res["checkpoint"] = ck["sha"]
    return res


_CK_SECRETS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "keystore.properties",
               "id_rsa*", "id_ed25519*", "*credentials*.json", "*secret*", ".netrc", ".npmrc", ".pypirc")
_CK_MAX_FILE = 2 * 1024 * 1024


def checkpoint(post=None, message=None, push=False, quiet=False, on_step_branch=False, stamp=False, url=None, token=None):
    """CL44 (item 10 / CL17): commit in-progress work on the step's branch (never on the trunk or detached HEAD)
    so a session limit or crash never leaves work only uncommitted in a worktree. `stamp` (CL17, `kit.py checkpoint
    <post>`): the post then carries `checkpoint` {branch, sha, at, pushed}, what a relaunched part continues from."""
    res = _checkpoint_commit(post=post, message=message, push=push, on_step_branch=on_step_branch)
    if stamp and post and res.get("ok") and res.get("branch"):
        sha = res.get("sha") or _git_run("rev-parse", "--short", "HEAD").stdout.strip()
        pushed = res.get("pushed")
        if pushed is None and not res.get("committed"):          # a clean tree: is this commit on origin already?
            pushed = _git_run("branch", "-r", "--contains", "HEAD", "--list", "origin/" + res["branch"]).stdout.strip() != ""
        ck = {"branch": res["branch"], "sha": sha, "at": _now_z(), "pushed": bool(pushed)}
        fields = {"checkpoint": ck, "branch": res["branch"], "updated_at": ck["at"]}
        try:
            done = _local_update(url or local_board_url(), _resolve_board_token(token), "posts", post, fields) is not None
        except Exception:
            done = False
        if not done:
            done = _board_write("update", "posts", post, fields)
        res = dict(res, stamped=ck if done else False)
    return res


def _checkpoint_commit(post=None, message=None, push=False, on_step_branch=False):
    cfg = _kit_config()
    k = cfg.get("kit") or {}
    trunk = str(k.get("main_branch") or "main")
    br = _git_run("branch", "--show-current").stdout.strip()
    if not br:
        return {"ok": False, "committed": False, "why": "detached HEAD: switch to your step branch first"}
    if br in (trunk, "main", "master", "board-mirror", "kit-usage") or br.startswith("wip/"):
        if on_step_branch:
            return {"ok": True, "committed": False, "why": "skipped on trunk/system branch %s" % br}
        return {"ok": False, "committed": False, "why": "refusing to checkpoint on branch %s (use a step branch)" % br}
    st = _git_run("status", "--porcelain", "--untracked-files=all")
    if st.returncode != 0 or not st.stdout.strip():
        res = {"ok": True, "committed": False, "branch": br, "why": "working tree clean"}
        if (push or k.get("checkpoint_push")) and _git_run("rev-list", "-1", "HEAD", "--not", "--remotes=origin").stdout.strip():
            pushed = _checkpoint_push(br, _landing_mod())
            return dict(res, **pushed) if isinstance(pushed, dict) else dict(res, pushed=pushed)
        return res
    never = tuple((k.get("guard") or {}).get("never_add") or ()) + _CK_SECRETS
    # Stage tracked modifications and safe untracked files (skip secrets, oversized files, .kit/run/, .kit/board_data/)
    # what is staged already is committed as it is (CL71: an ignored file staged for removal, `git rm --cached`, would
    # make `git add` fail); the rest is added
    staged = set(_git_run("diff", "--cached", "--name-only").stdout.splitlines())
    tracked = [x for x in _git_run("diff", "--name-only").stdout.splitlines() + sorted(staged)
               if x not in staged or os.path.exists(os.path.join(ROOT, x)) and not _git_ok("check-ignore", "-q", "--no-index", x)]
    untracked = _git_run("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    to_add, left = [], []
    for f in sorted(set(x for x in tracked + untracked if x)):
        low = f.lower().replace("\\", "/")
        if low.startswith(".kit/run/") or low.startswith(".kit/board_data/"):
            continue
        if any(fnmatch.fnmatch(low, p.lower()) or fnmatch.fnmatch(os.path.basename(low), p.lower()) for p in never):
            if not low.startswith(".kit/"):
                left.append(f)
            continue
        fp = os.path.join(ROOT, f)
        if os.path.isfile(fp):
            try:
                if os.path.getsize(fp) > _CK_MAX_FILE:
                    continue
            except OSError:
                continue
        to_add.append(f)
    if not to_add:
        return {"ok": True, "committed": False, "branch": br, "why": "nothing safe to stage"}
    add_r = _git_run("add", "--", *to_add)
    if add_r.returncode != 0:
        return {"ok": False, "committed": False, "branch": br, "why": add_r.stderr.strip() or "git add failed"}
    if not _git_run("diff", "--cached", "--name-only").stdout.strip():
        return {"ok": True, "committed": False, "branch": br, "why": "nothing staged"}
    la = _landing_mod()
    marks = la.conflict_hits("HEAD", cached=True, cwd=ROOT) if la and hasattr(la, "conflict_hits") else []
    if marks:                                  # CL71: a file with a merge's conflict markers is never committed
        _git_run("reset", "-q", "--", *to_add)
        return {"ok": False, "committed": False, "refused": True, "branch": br, "why": la.conflict_text(marks)}
    if la and (push or k.get("checkpoint_push")):
        # CL55: what would leave for GitHub is read first; a secret-looking value commits nothing and pushes nothing
        hits = la.secret_hits(la.push_base("HEAD"), cached=True, cwd=ROOT)
        if hits:
            _git_run("reset", "-q", "--", *to_add)
            return {"ok": False, "committed": False, "refused": True, "branch": br, "why": la.hits_text(hits)}
    note = ""
    if not post:                               # CL50: the worktree's recorded step, KIT_POST only when it agrees
        post, note = _own_step()
    label = str(post or br)
    msg = "checkpoint(%s): %s" % (label, str(message or "save progress").strip()[:120])
    if left:
        msg += "\n\nLeft out (secret-named, never committed): %s" % ", ".join(left[:20])
    c_args = []
    if not _git_run("config", "user.name").stdout.strip():
        c_args += ["-c", "user.name=board kit"]
    if not _git_run("config", "user.email").stdout.strip():
        c_args += ["-c", "user.email=kit@localhost"]
    cr = _git_run(*c_args, "commit", "-q", "-m", msg)
    if cr.returncode != 0:
        return {"ok": False, "committed": False, "branch": br, "why": (cr.stderr or cr.stdout).strip() or "git commit failed"}
    sha = _git_run("rev-parse", "--short", "HEAD").stdout.strip()
    try:
        stamp = os.path.join(ROOT, ".kit", "run", "checkpoint-last")
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        open(stamp, "a").close()
        os.utime(stamp, None)
    except OSError:
        pass
    pushed = _checkpoint_push(br, la) if (push or k.get("checkpoint_push")) else False
    if isinstance(pushed, dict):
        return dict(pushed, committed=True, sha=sha, message=msg, **({"step_note": note} if note else {}))
    res = {"ok": True, "committed": True, "branch": br, "sha": sha, "message": msg, "pushed": pushed, "left_out": left}
    return dict(res, step_note=note) if note else res


def _write_step(root, pid):
    """CL50: `.kit/run/step` in `root` names `pid`: the step this worktree is for."""
    try:
        path = os.path.join(root, ".kit", "run", "step")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(pid).strip() + "\n")
    except OSError:
        pass


def _own_step(branch_post=""):
    """CL50: (this worktree's step, a once-only note when KIT_POST names another), from .kit/landing.py; without it,
    KIT_POST as before."""
    la = _landing_mod()
    if la and hasattr(la, "own_step"):
        try:
            return la.own_step(ROOT, branch_post)
        except Exception:
            pass
    return str(os.environ.get("KIT_POST") or branch_post or ""), ""


def _landing_mod():
    """.kit/landing.py as a module (its secret check), or None."""
    for d in (os.path.join(ROOT, ".kit"), os.path.dirname(os.path.abspath(__file__)), os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit")):
        f = os.path.join(d, "landing.py")
        if os.path.isfile(f):
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("kit_landing_mod", f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.ROOT = ROOT
                return mod
            except Exception:
                return None
    return None


def _checkpoint_push(br, la=None):
    """The step's branch to origin, never forced; True, False (origin did not take it), or a refusal (a secret in commits
    origin lacks: the session's own, made before this checkpoint)."""
    if la:
        hits = la.secret_hits(la.push_base("HEAD"), "HEAD", cwd=ROOT)
        if hits:
            return {"ok": False, "refused": True, "branch": br, "pushed": False, "why": la.hits_text(hits)}
    return _git_run("push", "-q", "-u", "origin", "HEAD:refs/heads/%s" % br, timeout=120).returncode == 0


def pieces_file(pid, p):
    """.kit/run/pieces/<post>.json: the files each piece names, for the activity hook (it shows the piece being worked on)."""
    rows = [{"n": x["n"], "files": x["files"]} for x in pieces_of(p) if x.get("files")]
    if rows:
        try:
            path = os.path.join(ROOT, ".kit", "run", "pieces", pid + ".json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
        except OSError:
            pass


def subtask_lines(p, taking_over=False):
    rows = pieces_of(p)
    if not rows:
        return []
    pieces_file(str(p.get("id") or ""), p)
    pid = p.get("id") or "<post>"
    n = [x["status"] for x in rows]
    mark = {"done": "x", "doing": "~", "dropped": "-"}
    out = ["", "## Its pieces (%d of %d done)" % (n.count("done"), len(n) - n.count("dropped"))]
    out += ["- [%s] %d. %s" % (mark.get(x.get("status"), " "), x["n"], x["title"]) for x in rows]
    if pieces_open(p):
        out.append("He sees these under your step. As each is finished: `python3 -I .kit/kit.py sub %s <number> --status done` (several numbers: "
                   "`1 2`). `done` refuses the step while one is open; one that is not needed: `--status drop --why \"...\"`. Go on from the "
                   "first that is not done." % pid)
    return out


def take_step_branch(p):
    """The step's branch brought in (`_take_branch`), then its last save looked at (CL55)."""
    lines = _take_branch(p)
    br = str(p.get("branch") or "")
    cur = _git_run("branch", "--show-current").stdout.strip()
    if br and not any(x.startswith("## FIRST") for x in lines) and (cur == br or (_slug(str(p.get("part") or ""), 24) or "\0") in cur.lower()):
        try:
            lines += take_last_save(br)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return lines


def _take_branch(p):
    """A part starts on the newest work of its step (CL44 item 13), whatever started it (agy, claude --bg -w, the web):
    when the post names a branch whose commits this checkout lacks, bring them in now (fast-forward, else a merge);
    when that cannot be done safely (uncommitted changes, a conflict), the first lines say so and what to run."""
    br = str(p.get("branch") or "")
    if not br:
        return []
    have = lambda x: _git_run("rev-parse", "--verify", "--quiet", x).returncode == 0  # noqa: E731
    if have("origin/" + br):
        _git_run("fetch", "-q", "origin", br, timeout=60)
    refs = [x for x in ("origin/" + br, br) if have(x)]
    if not refs:
        return ["## FIRST: your post names branch `%s`, and this checkout has no such branch (nor origin/%s)" % (br, br),
                "- if the step was started before, its work is there: find it (`git fetch origin %s`, another clone, the "
                "orchestrator) before you write anything; a new step: create it with `git checkout -b %s`" % (br, br), ""]
    missing = []
    for x in refs:
        r = _git_run("merge-base", "--is-ancestor", x, "HEAD")
        if r.returncode not in (0, 1):
            raise PartStop("git could not compare `%s` with this checkout: %s %s" % (x, (r.stderr or "").strip()[:300], _sandbox_hint()))
        if r.returncode == 1:
            missing.append(x)
    if not missing:
        return []
    ref = missing[0] if len(missing) == 1 or _git_run("merge-base", "--is-ancestor", missing[1], missing[0]).returncode == 0 else missing[1]
    head = _git_run("rev-parse", "--short", "HEAD").stdout.strip()
    stop = lambda why: ["## FIRST: this checkout lacks your step's work on `%s` (%s)" % (ref, why),  # noqa: E731
                        "- run `git merge --no-edit %s` (commit or stash your changes first) before anything else; "
                        "never redo what it holds" % ref, ""]
    cur = _git_run("branch", "--show-current").stdout.strip()
    mine = cur == br or (_slug(str(p.get("part") or ""), 24) or "\0") in cur.lower()
    if not mine:
        return stop("this checkout is on `%s`, not the part's own branch, so nothing was changed here" % (cur or "a detached HEAD"))
    if _git_run("status", "--porcelain", "--untracked-files=no").stdout.strip():
        return stop("uncommitted changes here, so it was not merged for you")
    r = _git_run("merge", "--ff-only", "-q", ref, timeout=60)
    how = "fast-forward"
    if r.returncode != 0:
        r = _git_run("merge", "--no-edit", "-q", ref, timeout=60)
        how = "merge"
        if r.returncode != 0:
            _git_run("merge", "--abort")
            return stop("a merge conflict, so nothing was changed")
    return ["## Your step's work is here: `%s` brought into this checkout (%s, %s -> %s)" % (
        ref, how, head, _git_run("rev-parse", "--short", "HEAD").stdout.strip()), ""]


def mark_part_branch(pid, p):
    """CL55: this clone's branch is a part's: the save hook commits its work there (CL44's checkpoint) and pushes it,
    instead of a snapshot on wip/<branch>."""
    cur = _git_run("branch", "--show-current").stdout.strip()
    trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
    if not cur or cur in (trunk, "main", "master", "board-mirror", "kit-usage") or cur.startswith("wip/"):
        return
    try:
        path = os.path.join(ROOT, ".kit", "run", "part-branch.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"branch": cur, "post": pid, "step_branch": str(p.get("branch") or "")}, fh)
    except OSError:
        pass
    _write_step(ROOT, pid)                       # CL50: the part named its own step: the hooks read it before KIT_POST


def take_last_save(br):
    """CL55: a takeover starts from the newest of the step's branch and its last save (wip/<branch>, at origin or in this
    clone), and says which. A save that continues this checkout's commit and is newer is put in the working tree."""
    have = lambda x: _git_run("rev-parse", "--verify", "--quiet", x).returncode == 0  # noqa: E731
    _git_run("fetch", "-q", "--no-tags", "origin", "+refs/heads/wip/%s:refs/remotes/origin/wip/%s" % (br, br), timeout=30)
    saves = [x for x in ("refs/remotes/origin/wip/" + br, "refs/wip/" + br) if have(x)]
    when = lambda x: int(_git_run("log", "-1", "--format=%ct", x).stdout.strip() or 0)  # noqa: E731
    stamp = lambda x: datetime.fromtimestamp(when(x), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")  # noqa: E731
    head = _git_run("rev-parse", "--short", "HEAD").stdout.strip()
    tree = _git_run("rev-parse", "HEAD^{tree}").stdout.strip()
    if not saves:
        return []                                         # no save of this step anywhere: its branch is all there is
    fresh = [x for x in saves if _git_run("rev-parse", x + "^{tree}").stdout.strip() != tree and when(x) > when("HEAD")]
    if not fresh:
        last = max(saves, key=when)
        return ["## Started from your step's branch `%s` (%s, %s): the newest; its last save: %s" % (
            br, head, stamp("HEAD"), "`%s`, %s, older or the same" % (last.replace("refs/remotes/", "").replace("refs/", ""), stamp(last))), ""]
    s = max(fresh, key=when)
    name = s.replace("refs/remotes/", "").replace("refs/", "")
    cont = _git_run("merge-base", "--is-ancestor", "HEAD", s).returncode == 0
    clean = not _git_run("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if cont and clean and _git_run("restore", "--source", s, "--worktree", "--", ".").returncode == 0:
        stat = _git_run("diff", "--shortstat").stdout.strip()
        return ["## Started from your step's last save `%s` (%s), newer than its branch `%s` (%s, %s): its changes are in this "
                "working tree, not committed (%s): read `git diff`, then commit what holds" % (name, stamp(s), br, head, stamp("HEAD"), stat or "?"), ""]
    why = "uncommitted changes here" if not clean else "the branch has commits the save lacks"
    return ["## Your step's last save `%s` (%s) is newer than its branch `%s` (%s, %s), and was not put in (%s): read "
            "`git diff HEAD %s`, then `git restore --source %s --worktree -- <files>` for what holds" % (
                name, stamp(s), br, head, stamp("HEAD"), why, s, s), ""]


def continues_lines(p, session=None, pid=None):
    """A part that continues a step another session started (a context limit, a crash): what is already there,
    including its branch, last checkpoint commit, and any worktree with uncommitted changes (CL44 item 11)."""
    prev = str(p.get("session_id") or "")
    if not prev or (session and prev == session) or prev.startswith("pending"):
        return []
    br = str(p.get("branch") or "")
    have = lambda x: _git_run("rev-parse", "--verify", "--quiet", x).returncode == 0  # noqa: E731
    ref = next((x for x in ("origin/" + br, br) if br and have(x)), "")
    out = []
    if ref:
        trunk = str((_kit_config().get("kit") or {}).get("main_branch") or "main")
        base = next((x for x in ("origin/" + trunk, trunk) if have(x)), None)
        log = _git_run("log", "--format=%h %s", "-6", ref, *(["^" + base] if base else [])).stdout.strip().splitlines()
        out += (["- its branch `%s` holds:" % br] + ["  - " + _short(x, 110) for x in log]) if log else [
            "- its branch `%s` holds no commit of its own yet" % br]
    elif "progress" not in str(p.get("status") or ""):
        return []
    for line in _step_location_lines(pid or str(p.get("id") or ""), p, skip_wt=ROOT):
        s = line.strip()
        if not (ref and s.startswith("branch `%s`" % br)):
            out.append("- " + s)
    if br:
        rc, w = _landing("wip", br)
        out += [x for x in w.splitlines() if x.startswith("- ")] if rc == 0 else []
    return ["", "## This step names another session (%s): if that is not you, continue its work, never restart it" % prev] + out + [
        "- its post's body says where it stopped; put your own session id on the post first"]


def held_lines(pid, p, session=None):
    """A part about to work on a step that a session of ANOTHER platform still holds (a web session and one on the
    owner's computer): every platform reads this, agy too, which starts its parts without `kit.py launch`. On one
    platform a new session under a step is a hand-over, and its id may be a name here and an id there: never compared."""
    sess = str(p.get("session_id") or "")
    if not sess or sess.startswith("pending") or step_state(p) != "running":
        return []
    web = (_platform() == "web") or str(session or "").startswith("session_")
    if sess.startswith("session_") == web:
        return []
    mins = [m for m in (_mins_since(p.get("updated_at")), _saved_mins(str(p.get("branch") or "") or _slug(p.get("part") or pid, 24)))
            if m is not None]
    if not mins or min(mins) >= CLAIM_MIN:
        return []
    return ["", "## First: session %s (%s) gave a sign of life %d min ago" % (
        sess, "on the web" if sess.startswith("session_") else "on a computer", min(mins)),
            "- It ended (a usage limit, closed, replaced on purpose): go on. It still runs: stop here and tell the "
            "orchestrator; two sessions never work on one step."]


def resume_role(m, role, post=None, db_path=None, alive=None, cli=None, session=None, now=None):
    """`kit.py resume --role R`: what that role needs and nothing else (CL32), so a session starts from this alone."""
    if role not in ROLES:
        return "Unknown role %r: one of %s.\n" % (role, ", ".join(ROLES))
    posts, comments = _load_board_docs(m, db_path=db_path)
    posts = posts or _all_posts(db_path)
    try:
        for b in _attached_boards()[0]:
            for pid, p in (b.get("posts") or {}).items():
                posts.setdefault(pid, dict(p, _board=b["id"]))
    except Exception:
        pass
    out = []
    if role == "orchestrator":
        stale = stale_handover_lines(posts, comments)
        if stale:                                     # CL62: before anything else, a hand-over never overrides the queue
            out += stale + [""]
        first, then = alarm_lines()
        if first:                                     # M10: his rule said twice, a kind fixed three times: first
            out += ["## First: what keeps coming back (a check to plan, never another patch or memory line)"] + first + [""]
        out.append(resume(m, db_path=db_path, alive=alive, cli=cli, session=session, now=now).rstrip("\n"))
        st, txt = web_env_state(posts, comments)
        if txt:                                       # CL39: several repos on the web
            out += ["", "## Web environment (several repos)", txt]
        nl = land_lines(posts)
        if nl:
            out += ["", "## Work not on the trunk yet (it lands after its review)"] + nl
        gl = git_copy_lines(m, db_path)
        if gl:
            out += ["", "## The record is git, and it is behind"] + ["- " + x for x in gl]
        out += split_lines(posts) + train_lines(posts)
        out += start_lines(posts, comments, session or whoami())
        out += inherit_lines(posts) + unreached_lines(posts)
        out += owner_lines() + autonomy_lines()
        out += [""] + _launch_workflow()
        out += ["", "## Parts: their context and the save points",
                "- A part reads only `kit.py resume --role part` (its post, prompt, lessons, tests, how to report); never "
                "paste a reading list into a prompt.",
                "- At each check (your timer, a hand-over, a wake): a running part whose post has not changed for 30 min: "
                "message it \"Save point: commit and push your work now, write in your post where you stopped.\" A part "
                "not running and not complete: post status stalled, continue it from its branch.",
                "- Before you answer the owner: this resume, under 15 minutes old (the brief guard refuses otherwise); run again "
                "in this session, it prints only what changed since your last one, and that counts as fresh (`--full`: all of it)."]
        al = audit_lines()
        if al:
            out += ["", "## Kit commands skipped (last 24 h; all of it: `kit.py usage audit --since <time>`)"] + al
        out += ["", "## Lessons for the orchestrator"] + _lesson_lines(lessons_for("orchestrator"))
        ml = memory_line()
        if ml:
            out += ["", ml]
        if then:
            out += ["", "## His rules and decisions with no check yet (plan each check now)"] + then[:8]
        out += _principle_lines("orchestrator")
        pref = os.path.join(_resolve_memory_dir(), "preferences.md")
        if os.path.isfile(pref):
            with open(pref, encoding="utf-8") as fh:
                out += ["", "## The owner's preferences (%s)" % os.path.relpath(pref, ROOT), fh.read().strip()]
        bad = _refused_lines()
        if bad:
            out += ["", "## Lessons refused (fix: they reach no role)"] + bad
        try:
            odd = [l for l in memory_check_text() if not l.startswith("REFUSED: ")]
        except Exception as e:                       # a broken check never stops the orchestrator's resume
            odd = ["- memory check failed: %s" % e]
        if odd:
            out += ["", "## Memory that disagrees or waits for a review (M8)"] + ["- " + l for l in odd[:8]]
    elif role == "part":
        g = _git_run("rev-parse", "--verify", "HEAD")
        if g.returncode != 0:
            raise PartStop("git does not work in this checkout (%s): %s %s" % (ROOT, (g.stderr or g.stdout).strip()[:300], _sandbox_hint()))
        pid = _my_post(posts, post)
        if not pid:
            if post:
                why = ("; ".join(BOARD_ERRORS[-2:]) + ". " + _sandbox_hint()) if BOARD_ERRORS else (
                    "the board answered but holds no post `%s`: load it (`python3 -I .kit/web_takeover.py` where no board "
                    "runs), or ask the orchestrator which post is yours" % post)
                raise PartStop("cannot read your post `%s`, so you would start without its branch, its saved work and its "
                               "comments: %s" % (post, why))
            return ("No post found for this session: `kit.py resume --role part --post <your post id>` (your prompt names "
                    "it), or KIT_POST=<id>.\n")
        p = posts[pid]
        rel = "docs/prompts/%s.md" % pid
        took = take_step_branch(p)
        if db_path is None:                               # a test's board never marks this clone
            mark_part_branch(pid, p)
        files = sorted(set(_changed_files()) | {str(a).split(" §")[0].strip("` ") for a in _as_list(p.get("artifacts"))})
        out += took + ["# Resume: part %s (post `%s`)" % (p.get("part") or pid, pid),
                "%s · train `%s` · status %s%s" % (p.get("title") or pid, p.get("train") or "?", p.get("status") or "?",
                                                    (" · board %s" % p["_board"]) if p.get("_board") else ""),
                ""] + part_prompt(pid, p) + ["", "## Your post now"]
        for k in ("verdict", "bars", "check", "handoff"):
            if p.get(k):
                out.append("- %s: %s" % (k, "; ".join(map(str, _as_list(p[k])))[:400]))
        if len(out) and out[-1] == "## Your post now":
            out.append("- (nothing yet)")
        o = step_owner(p, posts)
        out.append("- your Done goes to the orchestrator that owns your step, `%s`%s (see Where you report)" % (
            o["address"] or "named by no post yet", (" on `%s`" % o["device"]) if o["device"] else ""))
        if isinstance(p.get("orchestrator_unreached"), dict):
            out.append("- orchestrator_unreached: %s (%s): wait for a claim; a message from the new owner names it" % (
                p["orchestrator_unreached"].get("at"), p["orchestrator_unreached"].get("why")))
        out += report_lines(pid, p, posts)
        out += held_lines(pid, p, session or whoami())
        out += continues_lines(p, session or whoami(), pid=pid)
        out += subtask_lines(dict(p, id=post))
        out += owner_lines() + autonomy_lines()
        out += ["", "## Lessons that touch your files (%d files changed)" % len(files)] + _lesson_lines(lessons_for("part", files))
        out += _principle_lines("part", p.get("train"), files=files)
        got = found_so_far(posts, p.get("train") or "", skip=pid, last=12) if p.get("train") else []
        if got:
            out += ["", "## Found so far on train %s (build on it, never redo it; `kit.py train %s --sections <PART>` for one step's "
                    "bars, check and scripts, `--full <PART>` for all of it)" % (_norm_train(p["train"]), _norm_train(p["train"]))] + got
        qs = train_questions(posts, pid, p) if p.get("train") else []
        if qs:
            out += ["", "## Waiting on the owner, on this train (never decide one, never work past it)"] + qs
        nl = land_lines(posts, train=p.get("train"), skip=pid) if p.get("train") else []
        if nl:
            out += ["", "## Not on the trunk yet (if you build on one: `git fetch origin <branch> && git merge --no-edit origin/<branch>`)"] + nl
        if p.get("differs"):
            out += ["", "## What this step does differently", str(p["differs"])]
        out += ["", "## Your tests", "- `%s` (0 failed before you commit)" % _test_command()]
        areas = real_project_areas()
        cons = consumers(p.get("consumers"))
        if areas or cons:
            touched = ("your change touches " + ", ".join(areas)) if areas else "nothing of compact/install/resume/hooks/artifact touched yet"
            cnames = ", ".join(c["github"] or c["name"] for c in cons) or "a project that uses the kit"
            if _rule("real_project_run") == "orchestrator":
                out.append("- Real project: %s. A change to %s gets a dry run on %s, run by the orchestrator from its session "
                           "(it holds both repos) and written in your post's `real_project`; you never attach a repo (CL38). "
                           "A file of that project you need: the Artifact's `kit_docs` (`ArtifactData get`, doc_id = its "
                           "path with `/` written `+2F~`), else ask under your post." % (touched, "/".join(REAL_PROJECT_AREAS), cnames))
            elif _rule("real_project_run") == "part":
                out.append("- Real project: %s. A change to %s gets a dry run on %s: run the changed command yourself in a "
                           "scratch local clone or read-only (never push) and write `{\"project\", \"command\", \"output\"}` "
                           "in your post's `real_project` before `kit.py done` (done refuses without it)."
                           % (touched, "/".join(REAL_PROJECT_AREAS), cnames))
            else:
                out.append("- Real project: %s. A change to %s gets a dry run on %s in a local clone (never push), "
                           "written in your post's `real_project`." % (touched, "/".join(REAL_PROJECT_AREAS), cnames))
        if lands_after_review():
            out += ["", "## When you are done", "- your branch reaches the trunk after its review: `end_session.py` pushes it, never onto the trunk"]
    elif role == "clerk":
        od = posts.get("_orchestrator") or {}
        orch = od.get("session_id") or "(none on _orchestrator)"
        here = orchestrator_here()   # CL49: the clerk reports to the orchestrator that started it, on its own device
        out += ["# Resume: the clerk (comment routing only)", "",
                "A comment on a post goes to the session that post names; none or not reachable: to the orchestrator.",
                "", "## Session ids", "- orchestrator: %s%s" % (orch, (" (device `%s`)" % od["device"]) if od.get("device") else ""),
                "- reach it on this device (`%s`) only: a row with the same name on another machine, or a stale one, is not it; "
                "the send fails: reply in the comment's thread \"Not passed: no orchestrator reached\" (`board_cli.py comments reply`) and go on" % (
                    here.get("orchestrator_device") or my_device())]
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
        acc = _acceptance(rel, p)
        out += ["", "## Its acceptance conditions (from its post's task)" if str(p.get("task") or "").strip() else
                "## Its acceptance conditions (from `%s`)" % rel] + (["- " + a for a in acc] or ["- (none written: judge against the task)"])
        rp = p.get("real_project")
        cons = consumers(p.get("consumers"))
        cnames = ", ".join(c["github"] or c["name"] for c in cons) or "a project that uses the kit"
        if _rule("real_project_run") == "orchestrator":
            how_real = ("from the orchestrator's session (it holds both repos; a launched session cannot attach one), "
                        "by subagent; write the run in the post's real_project.")
        else:
            how_real = "in a scratch local clone or read-only (never push); write the run in the post's real_project."
        out += ["", "## The real-project check",
                "- Rerun its `check`, then its claim on a real project: %s, %s" % (cnames, how_real),
                "- Its post says: %s" % (json.dumps(rp, ensure_ascii=False)[:500] if rp else "NO real-project result")]
        ft = field_text("review", "review", p.get("train"))
        out += ["", ft] if ft else []
        out += report_lines(pid, p, posts, review=True)
        out += owner_lines()
        out += ["", "## Lessons for a review"] + _lesson_lines(lessons_for("review"))
        out += _principle_lines("review", p.get("train"))
    return "\n".join(out) + "\n"


def train_context(m, target, zoom=None, db_path=None, as_json=False, level="brief", only=()):
    """A train as a task list, at the depth asked for. `brief` (two lines a step) and the current step; `sections` (the
    seven fields of each step, or of the steps in `only`); `full` (the steps in `only`, whole, with their comments)."""
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
            if step_state(p) not in ("done", "closed"):
                zoom_pid = pid
                break
    if not zoom_pid and not zoom_missing:
        zoom_pid = matched[-1][0]

    steps_out = []
    for pid, p in matched:
        col = str(p.get("column") or "info").lower()
        st = str(p.get("status") or "").lower()
        box = step_box(p)
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

    want = {str(x).strip().lower() for x in only or () if str(x).strip()}
    picked = [s for s in steps_out if not want or s["id"].lower() in want or s["part"].lower() in want]
    if want and not picked:
        return "train %r: no step %s (its steps: %s)\n" % (tq, ", ".join(sorted(want)), ", ".join(s["part"] for s in steps_out))
    if level == "full":
        if not want:
            return "train --full needs the step: kit.py train %s --full <PART>\n" % tq
        out = []
        for s in picked:
            q = posts[s["id"]]
            out += ["# %s `%s` %s %s" % (s["part"], s["id"], s["box"], s["title"])]
            for k in ("status", "verdict", "bars", "artifacts", "issues", "not_checked", "check", "breaks", "handoff", "differs",
                      "parent", "branch", "session_id"):
                v = [str(x) for x in _as_list(q.get(k)) if str(x).strip()]
                if v:
                    out.append("- %s: %s" % (k, "; ".join(v)))
            out += ["", str(q.get("body") or "").strip(), ""]
            for cid, c in by_post.get(s["id"], []):
                out.append("- **%s%s** (%s, `%s`): %s" % (c.get("from") or "?", (" [%s]" % c["kind"]) if c.get("kind") else "",
                                                          str(c.get("created_at") or "")[:16], cid, str(c.get("text") or "").strip()))
            out.append("")
        return "\n".join(out).rstrip() + "\n"
    if level == "brief":
        lines = ["# Train: %s (%d steps · zoom: %s)" % (tq, len(steps_out), zoom_pid), "",
                 "## Tasklist (brief; `--sections <PART>` the seven fields, `--full <PART>` all of it)"]
        for s in steps_out:
            lines.append(step_brief(s["id"], posts[s["id"]]) .replace("\n", " **← CURRENT**\n", 1) if s["is_zoom"]
                         and "\n" in step_brief(s["id"], posts[s["id"]]) else step_brief(s["id"], posts[s["id"]]) + (" **← CURRENT**" if s["is_zoom"] else ""))
        picked = []
    else:
        lines = ["# Train: %s (%d of %d steps · zoom: %s)" % (tq, len(picked), len(steps_out), zoom_pid), "", "## Tasklist (sections)"]
    for s in picked:
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

    if level == "sections" and want:
        return "\n".join(lines) + "\n"
    lines.extend(["", "## Current Focus (Zoom-in: `%s`)" % zoom_pid])
    if prompt_file:
        lines.append("- **Prompt:** `%s`" % prompt_file)
    lines.append("- **Column:** %s | **Title:** %s" % (zpost.get("column") or "info", zpost.get("title") or zoom_pid))
    body_txt = str(zpost.get("body") or "").strip()
    if body_txt:
        lines.extend(["", body_txt[:4000] + ("\n...(cut; all of it: `kit.py train %s --full %s`)" % (tq, zpart) if len(body_txt) > 4000 else "")])
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


def _usage_self():
    """CL37: a session whose project hooks do not run (started on two repos) still records its usage row: every kit
    command offers it, detached and at most once a minute (.kit/usage.py self). No model call; never in the way."""
    try:
        if not os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIT_USAGE_SELF") == "0":
            return
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(root, ".kit", "usage.py")
        if not os.path.isfile(script):
            return
        stamp = os.path.join(root, ".kit", "run", "usage", ".self")
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 60:
            return
        import subprocess as _sp
        _sp.Popen([sys.executable, "-I", script, "--root", root, "self"], stdin=_sp.DEVNULL, stdout=_sp.DEVNULL,
                  stderr=_sp.DEVNULL, start_new_session=True, cwd=root)
    except Exception:
        pass


def audit_lines(n=3, hours=24):
    """M11: resume --role orchestrator: the top n manual actions where a kit command exists, from the usage ledger
    (`kit.py usage audit`), the last `hours`. [] when the ledger has none or cannot be read."""
    here = os.path.dirname(os.path.abspath(__file__))
    script = next((p for p in (os.path.join(here, "usage.py"), os.path.join(here, "..", "scripts", "usage.py"))
                   if os.path.isfile(p)), None)
    if not script:
        return []
    try:
        spec = importlib.util.spec_from_file_location("kit_usage_audit", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
        return mod.audit_top(mod.all_rows(ROOT), n=n, since=since, conf=mod.kit_conf(ROOT))
    except Exception:
        return []


def main(argv=None):
    _usage_self()
    _argv = sys.argv[1:] if argv is None else list(argv)
    if _argv and _argv[0] == "usage":  # CL37: the usage ledger lives in its own script (installed as .kit/usage.py)
        import subprocess as _sp
        here = os.path.dirname(os.path.abspath(__file__))
        script = next((p for p in (os.path.join(here, "usage.py"), os.path.join(here, "..", "scripts", "usage.py"))
                       if os.path.isfile(p)), None)
        if not script:
            print("kit.py usage: .kit/usage.py is missing (install.py --update puts it there)")
            return 1
        return _sp.call([sys.executable, "-I", script] + _argv[1:])
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
    s = sp.add_parser("doctor"); s.add_argument("--db", default=None); s.add_argument("--fix", action="store_true", help="repair missing add_repo, hook drift, and stale sibling portfolio paths")
    s = sp.add_parser("ledger", help="3-layer memory ledger: show|observe|decide|distil|propose|confirm|check|init")
    s.add_argument("action", choices=["show", "observe", "decide", "distil", "propose", "field", "broke", "moves", "open", "confirm", "check", "init",
                                       "fix", "kinds", "design", "scan"])
    s.add_argument("--post", default="", help="design: the step planned for that kind (else --train makes it)"); s.add_argument("--train", default="")
    s.add_argument("--since", default="", help="scan: the commits after this ref (default: this branch's own)")
    s.add_argument("--keeps", default="", help="propose: exact rows that stay, under the principle"); s.add_argument("--not-for", dest="not_for", default="")
    s.add_argument("--refines", default="", help="the principle or field this one hardens"); s.add_argument("--opens", default="", help="field: start, files:<glob>, moment:<m>")
    s.add_argument("--moment", default=""); s.add_argument("--ask", action="store_true", help="moves: one question post for all that is proposed"); s.add_argument("--deadline", default="")
    s.add_argument("target", nargs="?", default=None, help="id for confirm/propose or summary for observe/decide")
    s.add_argument("--summary", default=""); s.add_argument("--quote", default=""); s.add_argument("--source", default="")
    s.add_argument("--scope", default=None, help="all|project|role:<r>|train:<t>|files:<glob> (observe: project; propose: the rows' own)"); s.add_argument("--theme", default=""); s.add_argument("--kind", default="observation")
    s.add_argument("--enforced-by", dest="enforced_by", default="review"); s.add_argument("--because", default=""); s.add_argument("--rejected", default="")
    s.add_argument("--replaces", default=""); s.add_argument("--subsumes", default="", help="comma-separated observation/rule ids")
    s.add_argument("--min-cluster", dest="min_cluster", type=int, default=3); s.add_argument("--role", choices=list(ROLES), default=None)
    s.add_argument("--widen", action="store_true", help="propose: a scope wider than the rows'")
    s.add_argument("--by", default="", help="confirm: the id of the owner's comment or Like that says yes"); s.add_argument("--db", default=None)
    s.add_argument("--json", dest="as_json", action="store_true")
    s = sp.add_parser("attach"); s.add_argument("repo"); s.add_argument("--no-clone", action="store_true")
    s = sp.add_parser("web-env"); s.add_argument("action", nargs="?", choices=["ask"]); s.add_argument("--id", dest="env_id")
    s = sp.add_parser("branches"); s.add_argument("--delete", action="store_true")
    s = sp.add_parser("whoami"); s.add_argument("name", nargs="?", default="")
    s = sp.add_parser("compact"); s.add_argument("--days", type=int, default=30); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-board", action="store_true", help="post nothing: open awaits points and issues stay in memory")
    s = sp.add_parser("report"); s.add_argument("--db", default=None)
    s = sp.add_parser("portfolio"); s.add_argument("--config", dest="portfolio_config", default=None); s.add_argument("--roots", nargs="*", default=[])
    s = sp.add_parser("link", help="CL56: the one link an outside AI gets for a post (its invite: drop box, result)"); s.add_argument("post"); s.add_argument("--hours", type=float, default=72); s.add_argument("--url", default=None); s.add_argument("--token", default=None)
    s = sp.add_parser("queue"); s.add_argument("--runner", default=None); s.add_argument("--for", dest="for_owner", default=None); s.add_argument("--db", default=None); s.add_argument("--unblocked", action="store_true", help="Exclude cards with unresolved blocked_by dependencies")
    s.add_argument("--gate", action="store_true", help="CL62: the start-now steps this orchestrator neither launched nor held (exit 1 when any), with the commands")
    s = sp.add_parser("search"); s.add_argument("query"); s.add_argument("--db", default=None); s.add_argument("--limit", type=int, default=25)
    s = sp.add_parser("train"); s.add_argument("target"); s.add_argument("--zoom", default=None); s.add_argument("--db", default=None); s.add_argument("--json", dest="as_json", action="store_true")
    s.add_argument("--brief", action="store_true", help="two lines a step and the current one (the default)")
    s.add_argument("--sections", nargs="*", default=None, metavar="PART", help="verdict, bars, artifacts, issues, check, breaks, handoff of these steps (none named: of all)")
    s.add_argument("--full", nargs="+", default=None, metavar="PART", help="these steps whole, with their comments, uncut")
    s = sp.add_parser("ask", help="one question post in his lane: options, default, deadline")
    s.add_argument("--title", required=True); s.add_argument("--body", default=""); s.add_argument("--option", action="append", default=[])
    s.add_argument("--default", default=""); s.add_argument("--deadline", default=""); s.add_argument("--needs", default="")
    s.add_argument("--about", default=""); s.add_argument("--train", default=""); s.add_argument("--priority", type=int, default=1)
    s = sp.add_parser("land", help="a reviewed step's branch onto the trunk"); s.add_argument("post")
    s = sp.add_parser("reviewed", help="a review's verdict, with the commit it read (CL71)"); s.add_argument("post")
    s.add_argument("verdict", choices=("continue", "redirect", "stop")); s.add_argument("--review-id", required=True)
    s.add_argument("--commit", default=None, help="the commit the review read (default: its branch's tip on origin)")
    s = sp.add_parser("landing", help="JSON: which steps may reach the trunk"); s.add_argument("--post", default=None)
    s.add_argument("--branch", default=None); s.add_argument("--mark", default=None, help="landed at this commit"); s.add_argument("--db", default=None)
    sp.add_parser("save", help="the work in progress to wip/<branch> now")
    s = sp.add_parser("checkpoint", help="commit in-progress work on the step's branch (never on trunk); with the post: "
                      "pushed, and the post stamped with branch, commit and time (CL17)")
    s.add_argument("post_id", nargs="?", default=None, help="the step's post: push and stamp it (CL17)")
    s.add_argument("--post", default=None); s.add_argument("-m", "--message", default=None)
    s.add_argument("--push", action="store_true"); s.add_argument("--no-push", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--on-step-branch", action="store_true")
    sp.add_parser("keep", help="the board here into git: committed and pushed")
    s = sp.add_parser("guide", help="a case of the orchestrator's guide (no case: their list)"); s.add_argument("case", nargs="?", default=None)
    s = sp.add_parser("fresh", help="CL53: fetch origin; say when this checkout is behind its trunk or on a stale branch, "
                      "and move the trunk forward when that loses nothing (once a session; --again: now)")
    s.add_argument("--session", default=None); s.add_argument("--again", action="store_true")
    s = sp.add_parser("resume"); s.add_argument("--db", default=None); s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s.add_argument("--session", default=None, help="your session id: resume flags an _orchestrator post naming another")
    s.add_argument("--role", choices=list(ROLES), default=None, help="what that role needs, nothing else (CL32)")
    s.add_argument("--post", default=None, help="--role part|review: the step's post (default: this branch's)")
    s.add_argument("--full", action="store_true", help="the whole resume, not only what changed since this session's last one (M5)")
    s = sp.add_parser("lessons", help="the lessons a role needs (--role), or `check`: refuse untagged lessons and "
                      "lessons only a project doc holds")
    s.add_argument("action", nargs="?", choices=["list", "check", "replace", "keep", "review"], default="list")
    s.add_argument("ids", nargs="*", default=[], help="replace: the one that wins, then the one that goes; keep: the one still true")
    s.add_argument("--role", choices=list(ROLES)); s.add_argument("--files", nargs="*", default=None)
    s.add_argument("--days", type=int, default=None, help="check/review: unconfirmed for more than this (kit.memory.review_days)")
    s.add_argument("--post", action="store_true", help="review: the list as one question post on the board")
    s.add_argument("--ask", action="store_true", help="check: one question post: which one of each pair wins")
    s.add_argument("--db", default=None)
    s = sp.add_parser("lesson", help="add a lesson to the memory, tagged with its roles")
    s.add_argument("action", choices=["add"]); s.add_argument("id"); s.add_argument("--roles", required=True)
    s.add_argument("--title", required=True); s.add_argument("--text", required=True)
    s.add_argument("--files", default=""); s.add_argument("--from", dest="frm", default="")
    s.add_argument("--replaces", default="", help="the lesson or ledger entry this one replaces: it goes to the archive")
    s.add_argument("--check", default="", help="what enforces it: test:<file>[::Class.test] | hook:<name> | code:<file>[#text] | "
                   "refusal:<file>#<text> (`;` between several); without it the lesson is unchecked and a check is proposed")
    s = sp.add_parser("map", help="a map of the project from its code: overview, area cards, joins, map.json (--check: still true?)")
    s.add_argument("--root", default=None); s.add_argument("--out", default=None); s.add_argument("--config", dest="map_config", default=None)
    s.add_argument("--check", action="store_true"); s.add_argument("--no-generators", action="store_true")
    s.add_argument("--steps", type=int, default=0, help="M3: how many of the last N steps' changed files the map covers")
    s = sp.add_parser("commands", help="the kit's commands: the topics, or those a word finds (`all`)")
    s.add_argument("word", nargs="?", default=None)
    s = sp.add_parser("adopt", help="once: give every post made before the kit's trains its train (from its part)")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--db", default=None)
    s = sp.add_parser("artifact", help="an Artifact board: sync (the one door, CL72) | pull | load | push | sent <batch> | pin <coll/id=version>... | reconcile")
    s.add_argument("action", choices=["sync", "pull", "load", "push", "sent", "pin", "status", "reconcile"]); s.add_argument("args", nargs="*")
    s.add_argument("--no-push", action="store_true", help="reconcile: the git copy is not committed nor pushed")
    s.add_argument("--branch", default=None, help="reconcile: the git copy's branch (default: the mirror's)")
    s.add_argument("--full", action="store_true", help="pull: every document, not only what changed")
    s.add_argument("--post", default=None, help="pull: a first pull reads only this post's documents (M11)")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s = sp.add_parser("reconcile", help="the board here vs its git copy: the newer copy of each post and comment wins")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None); s.add_argument("--branch", default=None)
    s.add_argument("--db", default=None); s.add_argument("--no-push", action="store_true")
    s = sp.add_parser("cards", help="rebuild \"Where we are\" and the trains if they differ from the posts; one line, "
                      "with the sessions to archive (no agent)")
    s.add_argument("--db", default=None)
    s = sp.add_parser("trains", help="a train is one goal: new <id> --goal, close <id> --why, or list them")
    s.add_argument("action", nargs="?", choices=["list", "new", "close"], default="list"); s.add_argument("train", nargs="?")
    s.add_argument("--goal", default=""); s.add_argument("--why", default=""); s.add_argument("--db", default=None)
    s = sp.add_parser("capacity", help="the device's session cap (.kit/device.py): --gate exits 1 when no slot is free")
    s.add_argument("--gate", action="store_true"); s.add_argument("--json", action="store_true")
    s = sp.add_parser("launch", help="start a part: its post in progress + Trains row running, then claude --bg (CLI), "
                      "invoke_subagent (agy), or the create_session call (web)")
    s.add_argument("post"); s.add_argument("--session-id", default=None, help="web: the id create_session returned")
    s.add_argument("--web", action="store_true", default=None); s.add_argument("--cli-run", dest="web", action="store_false")
    s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s.add_argument("--model", default=None); s.add_argument("--reset", action="store_true")
    s.add_argument("--take", action="store_true", help="the step's other session ended (checked) or its person agreed: take it over")
    s.add_argument("--anyway", default=None, help="R6: launch out of the queue's order; the reason is recorded on the post")
    s.add_argument("--continue", dest="cont", action="store_true", help="CL17: a crashed part, again on its own branch "
                   "(its work and checkpoints), told to continue from the checklist in its post")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None); s.add_argument("--json", action="store_true")
    s.add_argument("--post-file", default=None, help=argparse.SUPPRESS)
    s = sp.add_parser("claim", help="this orchestrator takes a step: its address on the post, the part and its review report to it (CL49)")
    s.add_argument("post"); s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s.add_argument("--json", action="store_true")
    s = sp.add_parser("hold", help="CL62: do not launch this start-now step yet, and say why (on the post, in resume, to the owner)")
    s.add_argument("post"); s.add_argument("--why", default=""); s.add_argument("--release", action="store_true")
    s.add_argument("--board-url", default=None); s.add_argument("--token", default=None)
    s = sp.add_parser("unreached", help="a part's report did not reach its orchestrator: its post says so, every resume lists it (CL49)")
    s.add_argument("post"); s.add_argument("--why", default=""); s.add_argument("--board-url", default=None)
    s.add_argument("--token", default=None); s.add_argument("--json", action="store_true")
    sp.add_parser("permission-mode", help="the permission mode the kit starts Claude Code sessions in")
    s = sp.add_parser("sub", help="a step's pieces, lines on its post: by number or title"); s.add_argument("post"); s.add_argument("title", nargs="+")
    s.add_argument("--why", default="", help="why a piece the task names is dropped"); s.add_argument("--known", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--status", default="open"); s.add_argument("--was", default="", help="its title until now"); s.add_argument("--db", default=None)
    s = sp.add_parser("form", help="a step's result as a file, half filled: fill it, `done` sends and checks it"); s.add_argument("post")
    s.add_argument("--doing", default="", help="turn 1: the step is in progress, in this session's name"); s.add_argument("--db", default=None)
    s = sp.add_parser("done"); s.add_argument("post"); s.add_argument("--db", default=None)
    s = sp.add_parser("pair"); s.add_argument("post"); s.add_argument("--answer", default=None); s.add_argument("--db", default=None)
    s = sp.add_parser("sections"); s.add_argument("--db", default=None)
    s = sp.add_parser("step", help="the train is the task list: new | start | complete | drop")
    s.add_argument("action", choices=["new", "start", "complete", "gate", "drop", "open", "differs", "find", "prompt"]); s.add_argument("post", nargs="?")
    s.add_argument("--train"); s.add_argument("--part"); s.add_argument("--title", default="")
    s.add_argument("--task", default=""); s.add_argument("--task-file"); s.add_argument("--parent")
    s.add_argument("--source", action="append", default=[]); s.add_argument("--model")
    s.add_argument("--no-review", action="store_true"); s.add_argument("--db", default=None)
    s.add_argument("--keep-whole", default=None, help="why a step the split check flags must stay one part (recorded)")
    s.add_argument("--keep-train", default=None, help="why a step goes on a train the train check refuses (recorded)")
    s.add_argument("--damage", default=None, help="R6: what was lost or put at risk, and who found it (a review, a done, "
                   "the owner): the step goes to the top of the queue")
    s.add_argument("--after", action="append", default=[], help="R6: a part this step waits on (it builds on its work)")
    s.add_argument("--kind", choices=["bug", "feature"], default=None, help="R6: when the task's words do not say it")
    s.add_argument("--cli", choices=["agy", "claude"], default=None)
    s.add_argument("--board", default=None, help="an attached board's id (repos/<id>/ in the Artifact): the step goes "
                   "on its train (default: this board, or the one attached board that holds the train)")
    s = sp.add_parser("review"); s.add_argument("post", nargs="?"); s.add_argument("--train", default=None)
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--force", action="store_true"); s.add_argument("--db", default=None)
    s.add_argument("--session-id", default=None, help="web: the review's session, once create_session answered")
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
    if a.cmd == "fresh":
        sess = a.session
        if not sess and not sys.stdin.isatty():      # a SessionStart line of its own: Claude Code's payload on stdin
            try:
                sess = str((json.loads(sys.stdin.read() or "{}") or {}).get("session_id") or "") or None
            except (ValueError, OSError, AttributeError):
                sess = None
        lines, _ = checkout_fresh() if a.again and _rule("checkout_refetch") else checkout_fresh_once(session=sess)
        if lines:
            print("\n".join(lines))
        return 0
    if a.cmd == "resume" and not getattr(a, "db", None):
        # CL53: before anything else, this checkout is on the newest code (or the first line says why not)
        try:
            lines, _ = checkout_fresh_once(session=getattr(a, "session", None))
        except Exception:
            lines = []
        if lines:
            print("\n".join(lines) + "\n")
    m = mirror_dir(a)
    if a.cmd in STALE_GATED and not getattr(a, "db", None):   # --db: a named database, not the Artifact's copy
        why = stale_copy()
        if why and a.cmd == "resume" and getattr(a, "role", "") == "part" and getattr(a, "post", None) and attached_board():
            st = _art_state()                          # M11: the first pull here reads only this post's documents
            if st.get("scope_post") != a.post:
                st["scope_post"] = a.post
                _art_save(st)
        if why:
            if a.cmd == "resume" and getattr(a, "role", "") == "part" and getattr(a, "post", None):
                # a part starts before its board is loaded here: its task is in git, and how to load the board is in how it works
                print("\n".join(part_prompt(a.post, {})) + "\n\n## The board here")
            print(why)
            return 3
    if a.cmd == "init":
        msg = init(m); print(msg)
        return 1 if msg.startswith("FAILED") else 0
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
    if a.cmd == "map":
        return _map_cmd(a)
    if a.cmd == "whoami":
        print(whoami(a.name))
        return 0
    if a.cmd == "branches":
        out, rc = merged_branches(delete=a.delete)
        print(out)
        return rc
    if a.cmd == "web-env":
        out, rc = web_env_cmd(a.action, a.env_id)
        print(out)
        return rc
    if a.cmd == "attach":
        out, rc = attach_repo(a.repo, clone=not a.no_clone)
        print(out)
        return rc
    if a.cmd == "doctor":
        out, rc = doctor(m, db_path=a.db, fix=a.fix)
        print(out)
        return rc
    if a.cmd == "ledger":
        led = _load_ledger_mod()
        if led is None:
            print(json.dumps({"error": "ledger.py not found in .kit/ or kit/"}))
            return 1
        mdir = _resolve_memory_dir()
        if a.action == "init":
            res = led.init_ledger(mdir)
        elif a.action == "show":
            if a.role:
                lines = led.format_resume(mdir, a.role, kit_root=ROOT)
                print("\n".join(lines).strip() or "(empty ledger for role %s)" % a.role)
                return 0
            entries = led.load_entries(mdir, kit_root=ROOT)
            if a.as_json:
                print(json.dumps(entries, indent=2, ensure_ascii=False))
            else:
                print(led.render_ledger(entries) if entries else "(empty ledger)")
            return 0
        elif a.action in ("observe", "decide"):
            kind = "decision" if a.action == "decide" else a.kind
            summary = a.summary or a.target or ""
            res = led.observe(mdir, summary, quote=a.quote, source=a.source, scope=a.scope or "project", theme=a.theme,
                              kind=kind, enforced_by=a.enforced_by, because=a.because, rejected=a.rejected,
                              replaces=a.replaces)
        elif a.action == "fix":                          # M10: one fix, its kind
            res = led.record_fix(mdir, a.theme or a.kind if a.kind != "observation" else a.theme, a.summary or a.target or "", a.source, quote=a.quote)
        elif a.action == "kinds":
            res = {"fixes": led.fix_kinds(mdir, kit_root=ROOT), "rules": led.owner_rules(mdir, kit_root=ROOT),
                   "decisions": [{"id": d["id"], "summary": d["summary"], "check": d["check"], "files": d["files"]} for d in led.decisions(mdir, kit_root=ROOT)]}
        elif a.action == "scan":
            res = scan_fix_commits(since=a.since or None)
        elif a.action == "design":
            res = plan_design(a.target or a.theme, post=a.post, train=a.train, db_path=a.db)
        elif a.action == "distil":
            res = {"clusters": led.distil_candidates(mdir, min_cluster=a.min_cluster)}
        elif a.action == "propose":
            sub_ids = [x.strip() for x in a.subsumes.split(",") if x.strip()]
            res = led.propose_principle(mdir, a.summary or a.target or "", sub_ids, scope=a.scope, theme=a.theme, widen=a.widen,
                                        keeps=[x.strip() for x in a.keeps.split(",") if x.strip()], not_for=a.not_for, refines=a.refines)
        elif a.action == "field":
            res = led.propose_field(mdir, a.summary or a.target or "", [x.strip() for x in a.subsumes.split(",") if x.strip()], a.theme, a.opens,
                                    not_for=a.not_for, refines=a.refines)
        elif a.action == "broke":
            res = led.broke(mdir, a.target or "", a.source)
        elif a.action == "open":
            print(field_text(a.moment or a.target or "", a.role or "part") or "(no field opens then)")
            return 0
        elif a.action == "moves" and a.ask:
            prop = [e for e in led.load_entries(mdir) if e["status"] == "proposed" and e["kind"] in ("principle", "field")]
            if not prop:
                res = {"error": "nothing is proposed: `kit.py ledger moves`, then propose or field"}
            else:
                body = "\n".join("- **%s** (%s, from %s%s%s): %s" % (e["id"], e["kind"], ", ".join(e["subsumes"]), ("; keeps exactly " + ", ".join(e["keeps"])) if e.get("keeps") else "",
                                  ("; opens at " + e["opens"]) if e.get("opens") else "", e["summary"]) for e in prop)
                res = ask("Your rules: %d proposed move(s)" % len(prop), "Nothing loads differently until you say yes.\n\n" + body,
                          ["Leave them", "Yes to all"], "Leave them", a.deadline, needs="direction", priority=3)
        elif a.action == "moves":
            res = {"moves": led.moves(mdir, min_cluster=a.min_cluster)}
        elif a.action == "confirm":
            why = _not_his_yes(m, a.by, db_path=a.db)
            res = {"error": why} if why else led.confirm_principle(mdir, a.target or "", a.by, summary=a.summary or None)
        else:   # check
            res = led.check_ledger(mdir, root=ROOT)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 1 if res.get("error") or res.get("ok") is False else 0
    if a.cmd == "compact":
        print(compact_memory(days=a.days, dry_run=a.dry_run, board=False if a.no_board else None))
        return 0
    if a.cmd == "report":
        print(report_metrics(m, db_path=a.db), end="")
        return 0
    if a.cmd == "portfolio":
        print(portfolio_summary(config_path=a.portfolio_config, roots=a.roots), end="")
        return 0
    if a.cmd == "queue" and a.gate:                     # CL62: what the orchestrator's Stop hook asks
        txt = start_now_refusal(start_now_unlaunched(_all_posts(a.db)))
        print(txt or "OK: every start-now step is launched or held.")
        return 1 if txt else 0
    if a.cmd == "hold":
        r = hold(a.post, why=a.why, release=a.release, url=a.board_url, token=a.token)
        if not r.get("ok"):
            print("Not held: %s" % r.get("error"))
            return 1
        print("released: %s goes back in the queue" % a.post if a.release else
              "held: %s (%s); resume, the queue and the board show it" % (a.post, (r["data"].get("held") or {}).get("why")))
        for b in r.get("batches") or []:
            print("`ArtifactData` `batch` %s, then `python3 -I .kit/kit.py artifact sent %s`" % (b, b))
        return 0
    if a.cmd == "link":
        r = outside_link(a.post, a.url, a.token, hours=a.hours)
        if r.get("error"):
            print(r["error"])
            return 1
        print(r["link"] + (("\nWarning: " + r["warning"]) if r.get("warning") else ""))
        return 0
    if a.cmd == "queue":
        if not a.runner and not a.for_owner:          # R6: the steps, ranked, each place with its why
            posts = _all_posts(a.db)
            print(queue_text(posts), end="")
            cards = queue_cards(m, db_path=a.db, unblocked_only=a.unblocked)
            if not cards.endswith(": 0 open cards"):
                print("\n# Cards for a runner or a person\n" + cards)
            return 0
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
                           model=a.model, review=not a.no_review, db_path=a.db, cli=a.cli, board=a.board, keep_whole=a.keep_whole, keep_train=a.keep_train,
                           damage=a.damage, after=a.after, kind=a.kind)
        elif a.action == "open":
            res = {"steps": step_open(a.db)}
        elif a.action == "find":
            res = step_find(a.part or a.post, a.train, db_path=a.db)
        elif not a.post:
            res = {"error": "step %s needs a post id" % a.action}
        elif a.action == "differs":
            res = step_differs(a.post, a.task, db_path=a.db)
        elif a.action == "start":
            res = step_start(a.post)
        elif a.action == "drop":
            res = step_drop(a.post, db_path=a.db)
        elif a.action == "prompt":     # CL54: the prompt generated from the post, committed (agy, a step done by hand)
            p = _get_post(a.post, db_path=a.db)
            res = sync_step_prompt(a.post, p, cli=a.cli) if isinstance(p, dict) else {"error": "no post %s" % a.post}
            res["post"] = a.post
        elif a.action == "gate":
            why = step_gate(a.post, db_path=a.db)
            res = {"post": a.post, "ok": not why, "why": why}
        else:
            res = step_complete(a.post, db_path=a.db, cli=a.cli)
        print(json.dumps(res, ensure_ascii=False))
        return 1 if res.get("error") or res.get("ok") is False else 0
    if a.cmd == "sections":
        print(sections(db_path=a.db))
        return 0
    if a.cmd == "permission-mode":
        print(permission_mode())
        return 0
    if a.cmd == "sub":
        res = subtask(a.post, a.title, a.status, was=a.was, db_path=a.db, why=a.why, known=a.known)
        print(json.dumps(res, ensure_ascii=False))
        return 1 if res.get("error") else 0
    if a.cmd == "checkpoint":
        res = checkpoint(post=a.post_id or a.post, message=a.message, push=(a.push or bool(a.post_id)) and not a.no_push,
                         quiet=a.quiet, on_step_branch=a.on_step_branch, stamp=bool(a.post_id))
        if not a.quiet:
            print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    if a.cmd == "form":
        rc, msg = form(a.post, doing=a.doing, db_path=a.db)
        print(msg)
        return rc
    if a.cmd == "done":
        ft = field_text("report")                         # before its verdict: what a report must be, while it can change
        if ft:
            print(ft + "\n")
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
        if a.session_id and a.post:
            ok = _board_write("update", "posts", a.post, {"review_started": _now_z(), "review_session": a.session_id})
            print("review of %s: %s" % (a.post, "started (session %s)" % a.session_id if ok else "NOT RECORDED: the board did not take it"))
            return 0 if ok else 1
        rc, msg = review(a.post, train=a.train, dry_run=a.dry_run, force=a.force, db_path=a.db, cli=a.cli)
        print(msg)
        return rc
    if a.cmd == "artifact":
        if a.action in ("sync", "pull", "load", "push", "sent", "pin") and not _rule("artifact_steps") and not os.environ.get("KIT_ARTIFACT_ON_COMPUTER"):
            # CL51 / R5: the Artifact is Claude Code web's alone (PLATFORM_RULES['artifact_steps'])
            print("not on a computer: the Artifact is the Claude Code web session's. Here the local board is the board "
                  "(the git copy, board-mirror, carries it); the web session reconciles its Artifact with GitHub.")
            return 2
        try:
            if a.action == "sync":
                txt, ok = artifact_sync(post=a.post, full=a.full, url=a.board_url, token=a.token)
                print(txt, end="")
                if ok and not glob.glob(os.path.join(_art_dir(), "out", "batch-*.json")):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(_art_dir(), "pending.json"))
                    print(keep(m)[0])
                return 0
            if a.action == "pull":
                calls = _pull_calls(full=a.full, post=a.post) if artifact_url() else []
                print(_pull_record(artifact_pull(full=a.full, post=a.post)), end="")
                if calls:
                    _kc().ledger_record(_art_dir(), calls, "artifact pull", url=artifact_url(), step="pull")
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
            elif a.action == "reconcile":
                if not _rule("artifact_steps") and not os.environ.get("KIT_ARTIFACT_RECONCILE_LOCAL"):
                    print("artifact reconcile: not in a local session (a computer reconciles its own board with git: "
                          "`kit.py reconcile`); it runs at a web session's start.")
                    return 0
                with _sync_lock(m) if os.path.isdir(m) else contextlib.nullcontext():
                    # a review, a check or KIT_NO_BOARD_PUSH never writes the git copy (web_takeover.owns_board's rule)
                    quiet = str(os.environ.get("KIT_ROLE") or "").strip().lower() in ("review", "check", "consultant") or \
                        str(os.environ.get("KIT_NO_BOARD_PUSH") or "").lower() in ("1", "true", "yes")
                    r = artifact_reconcile(m, url=a.board_url, token=a.token, branch=a.branch, push=not (a.no_push or quiet))
                print(artifact_reconcile_line(r))
                for u in r["loaded"].get("unmerged") or []:
                    print("NOT MERGED (rows without a key changed on both sides; this copy kept, its pin will refuse): " + u)
                print(artifact_push_text(r["push"]), end="")
            elif a.action == "push":
                print(artifact_push_text(_pending_record(artifact_push(url=a.board_url, token=a.token))), end="")
            elif a.action == "sent":
                print("recorded: %d writes" % sum(artifact_sent(p) for p in a.args))
                if not glob.glob(os.path.join(_art_dir(), "out", "batch-*.json")):      # the last batch: git, the record
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(_art_dir(), "pending.json"))       # M11: nothing out, reads open again
                    print(keep(m)[0])
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
                      "never recorded sent (%s). Run `python3 -I .kit/kit.py artifact sync` and make the calls it prints, "
                      "exactly, until it says \"in sync\" (CL72)." % (r["changed"], len(r["batches"]),
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
    if a.cmd == "ask":
        res = ask(a.title, a.body, a.option, a.default, a.deadline, a.needs, a.about, a.train, a.priority)
        print(json.dumps(res, ensure_ascii=False))
        print(field_text("ask", "orchestrator"), file=sys.stderr)
        return 1 if res.get("error") else 0
    if a.cmd in ("launch", "land") and not getattr(a, "session_id", None):
        gate = design_gate(a.cmd)
        if gate:
            print(gate)
            return 1
    if a.cmd == "reviewed":
        r = reviewed(a.post, a.verdict, a.review_id, a.commit)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    if a.cmd == "land":
        rc, txt = _landing("land", a.post)
        print(txt)
        return rc
    if a.cmd == "save":
        rc, txt = _landing("save")
        print(txt)
        return rc
    if a.cmd == "landing":
        if a.mark and a.post:
            ok = _board_write("update", "posts", a.post, {"landed": a.mark, "landed_at": _now_z()})
            print(json.dumps({"ok": ok, "steps": []}))
            return 0 if ok else 1
        got = landing_steps(_all_posts(a.db))
        for s in got:
            if s.get("found"):
                mark_landed(s)                              # CL71: the step is on the trunk: marked, here too
        print(json.dumps({"steps": [s for s in got if (not a.post or s["post"] == a.post) and (not a.branch or s["branch"] == a.branch)]}, ensure_ascii=False))
        return 0
    if a.cmd == "guide":
        print(guide(a.case), end="")
        return 0
    if a.cmd == "keep":
        txt, ok = keep(m)
        print(txt)
        return 0 if ok else 1
    if a.cmd == "resume":
        # the _orchestrator check is the orchestrator's (a part's own session is never named there)
        sess = a.session or (os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIT_SESSION_ID")
                             if os.environ.get("KIT_ROLE") == "orchestrator" else None)
        dsess = sess or a.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("KIT_SESSION_ID")
        delta = resume_delta(m, role=a.role, session=dsess, db_path=a.db, cli=a.cli, full=a.full)
        if delta is not None:
            print(delta, end="")
            return 0
        if a.role:
            try:
                print(resume_role(m, a.role, post=a.post, db_path=a.db, cli=a.cli, session=sess), end="")
            except PartStop as e:
                print("STOP: do not start the work. %s" % e)
                print("STOP: %s" % e, file=sys.stderr)
                return 4
        else:
            print(resume(m, db_path=a.db, cli=a.cli, session=sess), end="")
        return 0
    if a.cmd == "lessons":
        if a.action == "check":
            err = lessons_check()
            res = memory_checks(run=True)
            bad = [r for r in res if r["status"] == "fail"]
            err += ["%s: its check fails or what it names is gone: %s" % (r["id"], r["why"]) for r in bad]
            odd = [l for l in memory_check_text(a.days) if not l.startswith("REFUSED: ")]
            print("\n".join(["REFUSED: " + e for e in err] + odd) if err or odd else
                  "OK: every lesson is in the memory, tagged with its roles, every check passes, nothing in it disagrees")
            for r in res:
                if r["status"] == "unchecked":
                    print("UNCHECKED: %s since %s: nothing runs it (add `check:` naming its test, hook, refusal or code)" % (r["id"], r["since"]))
            if res:
                print(memory_line())
            if a.ask:
                pairs = [c for c in memory_conflicts() if c["kind"] in ("contradiction", "authority")]
                if pairs:
                    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
                    body = "Two live rules that disagree: a session would meet both. Until you choose, both stay, flagged.\n\n" + \
                        "\n".join("- %s. %s" % (c["why"], c["ask"]) for c in pairs)
                    print(json.dumps(ask("Memory: %d pair(s) disagree, which one wins?" % len(pairs), body,
                                         ["Leave both flagged", "I answer under the post"], "Leave both flagged", deadline,
                                         needs="direction", train="memory", priority=2), ensure_ascii=False))
            return 1 if err or [l for l in odd if not l.startswith("REVIEW:")] else 0
        if a.action == "replace":
            if len(a.ids) != 2:
                print(json.dumps({"error": "lessons replace <the one that wins> <the one that goes>"}))
                return 2
            res = memory_replace(a.ids[0], a.ids[1])
            print(json.dumps(res, ensure_ascii=False))
            return 1 if res.get("error") else 0
        if a.action == "keep":
            res = [memory_keep(i) for i in a.ids] or [{"error": "lessons keep <id>...: the entries still true"}]
            print(json.dumps(res if len(res) > 1 else res[0], ensure_ascii=False))
            return 1 if any(r.get("error") for r in res) else 0
        if a.action == "review":
            if a.post:
                res = memory_review_post(a.days, db_path=a.db)
                print(json.dumps(res, ensure_ascii=False))
                return 1 if res.get("error") else 0
            days, old = memory_stale(a.days)
            print("\n".join("- %s (%s, %s): %s" % (i["id"], i["source"] or i["kind"], ("%d days" % i["age"]) if i["age"] is not None
                                                   else "never dated", _short(i["text"], 100)) for i in old)
                  or "(nothing unconfirmed for more than %d days)" % days)
            return 0
        les = lessons_for(a.role, a.files) if a.role else all_lessons()
        print("\n".join(_lesson_lines(les)))
        return 0
    if a.cmd == "lesson":
        res = lesson_add(a.id, a.roles.split(","), a.title, a.text, [f for f in a.files.split(",") if f.strip()], a.frm, a.check,
                         replaces=a.replaces)
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
    if a.cmd in ("claim", "unreached"):
        r = (claim(a.post, url=a.board_url, token=a.token) if a.cmd == "claim"
             else unreached(a.post, why=a.why, url=a.board_url, token=a.token))
        print(json.dumps({k: v for k, v in r.items() if k != "data"}, ensure_ascii=False, indent=1) if a.json else owner_text(a.cmd, r), end="")
        if a.json:
            print()
        return 0 if r.get("ok") else 1
    if a.cmd == "launch":
        r = launch(a.post, url=a.board_url, token=a.token, web=a.web, session_id=a.session_id, model=a.model, reset=a.reset,
                   cli=a.cli, post_file=a.post_file, take=a.take, anyway=a.anyway, cont=a.cont)
        print(field_text("launch", "orchestrator"), file=sys.stderr)
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else launch_text(r), end="" if not a.json else "\n")
        return 0 if r.get("ok") else 1
    if a.cmd == "cards":
        rc, line = cards(db_path=a.db)
        print(line)
        return rc
    if a.cmd == "trains":
        if a.action == "new":
            res = trains_new(a.train, a.goal, db_path=a.db)
        elif a.action == "close":
            res = trains_close(a.train, a.why, db_path=a.db)
        else:
            posts = _all_posts(a.db)
            if not posts and BOARD_ERRORS:
                print(json.dumps({"error": "cannot read the board: %s" % BOARD_ERRORS[-1]}))
                return 1
            names = sorted({_norm_train(q.get("train")) for k, q in posts.items() if isinstance(q, dict) and str(q.get("train") or "").strip()})
            res = {"trains": [{"train": t, "steps": len(train_steps(posts, t)),
                               "open": sum(1 for _, q in train_steps(posts, t) if _step_open(q)),
                               "goal": (train_record(posts, t) or {}).get("goal") or "",
                               "status": (train_record(posts, t) or {}).get("status") or "open (no record)"} for t in names]}
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res.get("error") else 0
    if a.cmd == "capacity":
        d = _device()
        if d is None:
            print("no .kit/device.py: reinstall the kit", file=sys.stderr)
            return 2
        return d.main((["--gate"] if a.gate else []) + (["--json"] if a.json else []))
    if a.cmd == "train":
        lv, only = ("full", a.full) if a.full else ("sections", a.sections) if a.sections is not None else ("brief", ())
        print(train_context(m, a.target, zoom=a.zoom, db_path=a.db, as_json=a.as_json, level=lv, only=only), end="")
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
