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
    python3 -I scripts/kit.py welcome [--runtime agy|claude|arena|generic] [--post]

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
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
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


def sync_db(m, db_path=None, at=None, full=False):
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


def open_(m, since="2000-01-01"):
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


# ---------------------------------------------------------------- warm handoff (receive / publish)
class Conflict(Exception):
    pass


def _resolve_board_url(explicit=None):
    return (explicit or os.environ.get("KIT_BOARD_URL") or CONFIG.get("board_url") or "http://127.0.0.1:8765").rstrip("/")


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


# ---------------------------------------------------------------- runtime welcomes
WELCOME_RUNTIMES = ("agy", "claude", "arena", "generic")

_RUNTIME_LABELS = {
    "agy": "agy local",
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
            "- **Live forwarding & parallel workers:** a `flash_lite` clerk auto-forwards your comments, 👍 Likes, and tapped answers; parallel git-worktree subagents (`Workspace=\"share\"`) isolate each part's edits.",
            "- **Warm Git sync:** `kit.py receive` / `kit.py publish` sync board state cleanly with other runtimes.",
        ]
    if runtime == "claude":
        return [
            "- **Cloud Artifact board:** hosted on `claude.ai` (`%s`) with `window.storage` persistence." % board_url,
            "- **Auto-wakeup on comment:** comments and 👍 Likes wake the clerk/orchestrator automatically.",
            "- **Cloud subagents & hybrid bridge:** remote subagents run parts in parallel, and the `board-mirror` Git bridge (`kit.py sync` / `git_watch.py`) connects local computer runners.",
        ]
    if runtime == "arena":
        return [
            "- **Embedded preview mode:** runs `board_server.py --embed-mode` (`KIT_EMBED_PREVIEW=1`) at `%s` with cookie-independent `X-Kit-Token` unlock and 5s change polling for sandboxed preview iframes." % board_url,
            "- **Pull-on-turn (no background clerk / no auto-wake):** your comments and 👍 Likes save to the board immediately, but there is no background watcher or automatic wakeup — ping the Arena chat whenever you want the agent to read new comments and reply.",
            "- **Handoff:** warm Git sync (`kit.py receive` / `kit.py publish`) when a remote is configured, or a downloadable patch/zip when the sandbox has no push credentials.",
        ]
    return [
        "- **Local board server:** served by `board_server.py` at `%s`, driven via `board_cli.py` (manual or polled)." % board_url,
        "- **Memory & handoff:** project state lives in `%s/`, and warm Git sync (`kit.py receive` / `kit.py publish`) shares board state across runtimes." % mem_rel,
    ]


def _runtime_setup_line(runtime):
    if runtime == "agy":
        return "Local `computer` runner is ready out of the box in this `agy` session (clerk active, worktree subagents available)."
    if runtime == "claude":
        return "`cloud` runner is ready in Claude Code Web; connect a `computer` runner via `board-mirror` + `git_watch.py` if local execution is needed."
    if runtime == "arena":
        return "Embedded preview (`--embed-mode`) is active; pull-on-turn workflow is ready (reply in Arena chat to trigger the next turn)."
    return "Local CLI runner (`board_cli.py`) is ready; poll or run `kit.py` commands between turns."


def render_welcome(runtime=None, project=None, board_url=None, memory_dir=None, cmd=None, talk=None):
    """Render a two-layer (universal + runtime-specific) welcome message for _orchestrator."""
    rt = runtime or (CONFIG.get("kit") or {}).get("runtime") or "agy"
    if rt == "both":
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
    lines.extend(["", "A 👍 on this message launches them all; or tell me which."])
    return "\n".join(lines) + "\n"


def post_welcome(runtime=None, url=None, token=None, project=None, memory_dir=None, cmd=None, talk=None, at=None):
    """Render and idempotently post welcome-<runtime>-1 under _orchestrator."""
    rt = runtime or (CONFIG.get("kit") or {}).get("runtime") or "agy"
    if rt == "both":
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
    s.add_argument("--full", action="store_true"); s.add_argument("--commit", action="store_true")
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
    if a.cmd == "init":
        print(init(m)); return 0
    if a.cmd == "push":
        r = git(m, "push", "-u", "origin", BRANCH)
        print((r.stdout + r.stderr).strip() or "pushed"); return r.returncode
    if a.cmd == "sync":
        r = sync(m, a.dir, a.at, a.full)
        print("sync %s: posts %d written, %d removed; comments %d new, %d changed, %d removed" % ((a.at,) + r))
        if a.commit:
            print(commit(m, "kit sync %s%s" % (a.at, " (full)" if a.full else "")))
        return 0
    if a.cmd == "sync-db":
        at_val = a.at or zstamp(datetime.now(timezone.utc))
        r = sync_db(m, db_path=a.db, at=at_val, full=a.full)
        print("sync-db %s: posts %d written, %d removed; comments %d new, %d changed, %d removed" % ((at_val,) + r))
        if a.commit:
            print(commit(m, "kit sync-db %s%s" % (at_val, " (full)" if a.full else "")))
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
