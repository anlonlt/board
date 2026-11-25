#!/usr/bin/env python3
"""Local HTTP/SSE server for the board kit in agy on a computer (zero external dependencies).

    python3 -I .kit/board_server.py [--host 127.0.0.1] [--port 8765] [--root .]

Serves .kit/board.html with an injected window.claude shim (/_shim.js) backed by local SQLite
(.kit/board_data/board.db) and local blobs (.kit/board_data/blobs/), with Server-Sent Events
(/_api/events) for live onSnapshot updates and a local comment-watch queue for the board clerk.
"""
import argparse
import difflib
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SHIM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shim.js")


def tar_name(n):
    """A path without its leading "./" (CL30: `lstrip("./")` also ate the dot of `.story/` and `.kit/`)."""
    n = str(n or "")
    while n.startswith("./"):
        n = n[2:]
    return n


def load_shim_js():
    try:
        with open(_SHIM_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


SHIM_JS = load_shim_js()


def _merge_board_seen_posts(dst_posts, src_posts):
    """Monotonic max-timestamp merge for board-seen posts maps."""
    for pid, inc_entry in src_posts.items():
        cur = dst_posts.get(pid)
        if not isinstance(cur, dict) or not isinstance(inc_entry, dict):
            if pid not in dst_posts or not isinstance(cur, dict):
                dst_posts[pid] = dict(inc_entry) if isinstance(inc_entry, dict) else inc_entry
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
        dst_posts[pid] = out


def _is_seen_doc(coll, doc_id):
    return doc_id == "board-seen" and str(coll or "").startswith("data/users/")


def _seen_doc_all_boards(primary, stores_map, primary_id, coll, doc_id):
    """CL21: his read marks for every board live in the primary store's doc, an attached board's post under
    "repos/<board>/<post>" (the page's seenKey). Marks an attached board kept in its own store before CL21 are
    shown under that prefix until the page writes its own (the primary doc is never rewritten here)."""
    res = primary.get_doc(coll, doc_id)
    data = dict(res.get("data") or {}) if res.get("exists") else {}
    posts = dict(data.get("posts") or {}) if isinstance(data.get("posts"), dict) else {}
    added = False
    for rid, st in stores_map.items():
        if rid == primary_id or st is primary:
            continue
        old = st.get_doc(coll, doc_id)
        old_posts = (old.get("data") or {}).get("posts") if old.get("exists") else None
        if not isinstance(old_posts, dict):
            continue
        for pid, mark in old_posts.items():
            key = "repos/" + rid + "/" + pid
            if key not in posts:
                posts[key] = mark
                added = True
    if not added:
        return res
    data["posts"] = posts
    out = dict(res)
    out["data"] = data
    return out


def _deep_merge_doc(dst, src, doc_id=None):
    for k, v in src.items():
        if doc_id == "board-seen" and k == "posts" and isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_board_seen_posts(dst[k], v)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge_doc(dst[k], v)
        else:
            dst[k] = v


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_path(path):
    """Split a doc path into (collection, doc_id). Supports 'posts/abc' and 'data/users/owner/board-seen'."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("invalid doc path: %s" % path)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "/".join(parts[:-1]), parts[-1]


def match_where(doc, clauses):
    for clause in clauses or []:
        if not isinstance(clause, (list, tuple)) or len(clause) != 3:
            continue
        field, op, val = clause
        cur = doc.get(field)
        if op == "==":
            if cur != val:
                return False
        elif op == "!=":
            if cur == val:
                return False
        elif op == ">":
            if cur is None or not (cur > val):
                return False
        elif op == ">=":
            if cur is None or not (cur >= val):
                return False
        elif op == "<":
            if cur is None or not (cur < val):
                return False
        elif op == "<=":
            if cur is None or not (cur <= val):
                return False
        elif op == "in":
            if not isinstance(val, list) or cur not in val:
                return False
    return True


ACTOR = threading.local()        # who this thread writes for: set per request, kept in the change log


class BoardStore:
    MAX_CHANGES_LOG = 10000
    COMMENT_LEASE_SEC = 60.0

    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.blob_dir = os.path.join(self.data_dir, "blobs")
        os.makedirs(self.blob_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "board.db")
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        try:
            qc = self.conn.execute("PRAGMA quick_check").fetchone()
            if qc and str(qc[0]).lower() != "ok":
                print("board_server: WARNING: SQLite quick_check reported: %s" % qc[0], file=sys.stderr)
        except sqlite3.Error as e:
            print("board_server: WARNING: SQLite quick_check failed: %s" % e, file=sys.stderr)
        self._has_json1 = True
        try:
            self.conn.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        except sqlite3.OperationalError:
            self._has_json1 = False
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS docs ("
            "  collection TEXT NOT NULL,"
            "  doc_id TEXT NOT NULL,"
            "  version INTEGER NOT NULL DEFAULT 1,"
            "  data TEXT NOT NULL,"
            "  updated_at TEXT NOT NULL,"
            "  PRIMARY KEY (collection, doc_id)"
            ")"
        )
        if self._has_json1:
            for idx_sql in (
                "CREATE INDEX IF NOT EXISTS idx_docs_post_id ON docs(collection, json_extract(data, '$.post_id'))",
                "CREATE INDEX IF NOT EXISTS idx_docs_thread_id ON docs(collection, json_extract(data, '$.thread_id'))",
                "CREATE INDEX IF NOT EXISTS idx_docs_column ON docs(collection, json_extract(data, '$.column'))",
                "CREATE INDEX IF NOT EXISTS idx_docs_created_at ON docs(collection, json_extract(data, '$.created_at'))",
                "CREATE INDEX IF NOT EXISTS idx_docs_runner ON docs(collection, json_extract(data, '$.runner'))",
                "CREATE INDEX IF NOT EXISTS idx_docs_for ON docs(collection, json_extract(data, '$.for'))",
            ):
                try:
                    self.conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS shares ("
            "  token TEXT PRIMARY KEY,"
            "  post_id TEXT NOT NULL,"
            "  repo TEXT NOT NULL DEFAULT '',"
            "  allow_write INTEGER NOT NULL DEFAULT 1,"
            "  files_json TEXT NOT NULL DEFAULT '[]',"
            "  expires_at TEXT NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  revoked INTEGER NOT NULL DEFAULT 0,"
            "  submissions INTEGER NOT NULL DEFAULT 0,"
            "  max_submissions INTEGER NOT NULL DEFAULT 5"
            ")"
        )
        try:
            self.conn.execute("ALTER TABLE shares ADD COLUMN repo TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS threads ("
            "  thread_id TEXT PRIMARY KEY,"
            "  anchor TEXT NOT NULL,"
            "  text TEXT NOT NULL,"
            "  status TEXT NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  dispatched INTEGER NOT NULL DEFAULT 0,"
            "  dispatched_at REAL NOT NULL DEFAULT 0,"
            "  replies TEXT NOT NULL DEFAULT '[]'"
            ")"
        )
        for col in ("threads ADD COLUMN dispatched_at REAL NOT NULL DEFAULT 0", "threads ADD COLUMN by_user TEXT NOT NULL DEFAULT ''",
                    "shares ADD COLUMN created_by TEXT NOT NULL DEFAULT ''"):
            try:
                self.conn.execute("ALTER TABLE " + col)
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS changes ("
            "  seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  collection TEXT NOT NULL,"
            "  doc_id TEXT NOT NULL,"
            "  op TEXT NOT NULL,"
            "  version INTEGER NOT NULL,"
            "  at TEXT NOT NULL,"
            "  actor TEXT NOT NULL DEFAULT ''"
            ")"
        )
        try:
            self.conn.execute("ALTER TABLE changes ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS links ("
            "  post_id TEXT PRIMARY KEY,"
            "  part TEXT NOT NULL,"
            "  train TEXT NOT NULL,"
            "  parent_part TEXT NOT NULL DEFAULT '',"
            "  title TEXT NOT NULL DEFAULT '',"
            "  column_name TEXT NOT NULL DEFAULT 'info',"
            "  status TEXT NOT NULL DEFAULT '',"
            "  priority INTEGER NOT NULL DEFAULT 2,"
            "  session_id TEXT NOT NULL DEFAULT '',"
            "  created_at TEXT NOT NULL DEFAULT '',"
            "  updated_at TEXT NOT NULL DEFAULT ''"
            ")"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_links_train ON links(train, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_links_parent ON links(parent_part)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "  user_id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  role TEXT NOT NULL DEFAULT 'member',"
            "  token TEXT NOT NULL UNIQUE,"
            "  repo TEXT NOT NULL DEFAULT '',"
            "  created_at TEXT NOT NULL,"
            "  revoked INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        try:
            self.conn.execute("ALTER TABLE users ADD COLUMN repo TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS artifacts ("
            "  artifact_id TEXT PRIMARY KEY,"
            "  post_id TEXT NOT NULL,"
            "  path TEXT NOT NULL,"
            "  base_path TEXT NOT NULL DEFAULT '',"
            "  summary TEXT NOT NULL DEFAULT '',"
            "  content TEXT NOT NULL,"
            "  size INTEGER NOT NULL DEFAULT 0,"
            "  by_user TEXT NOT NULL DEFAULT 'owner',"
            "  agent_meta TEXT NOT NULL DEFAULT '{}',"
            "  created_at TEXT NOT NULL"
            ")"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_post ON artifacts(post_id, created_at)")
        self.conn.commit()
        self.sse_clients = set()
        self.sse_lock = threading.Lock()
        self.watchers = set()
        self.comment_cond = threading.Condition()
        self._auto_sync_cond = threading.Condition()
        self._auto_sync_dirty = False
        self._auto_sync_closed = False
        self._auto_sync_thread = None
        self.auto_sync_count = 0

    def enable_auto_sync(self, root_dir, interval_sec=15.0, push=False):
        interval = float(interval_sec or 0)
        if interval <= 0:
            return
        self._auto_sync_root = os.path.abspath(root_dir)
        self._auto_sync_interval = max(0.05, interval)
        self._auto_sync_push = bool(push)
        if self._auto_sync_thread is None:
            self._auto_sync_thread = threading.Thread(target=self._auto_sync_worker, daemon=True)
            self._auto_sync_thread.start()

    def _resolve_mirror_dir(self):
        root = getattr(self, "_auto_sync_root", None)
        if not root:
            return None
        if os.environ.get("KIT_MIRROR"):
            return os.environ["KIT_MIRROR"]
        cfg_path = os.path.join(root, ".kit", "config.json")
        mp = None
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                mp = json.load(fh).get("mirror", {}).get("path")
        except (OSError, ValueError):
            pass
        if mp:
            return mp if os.path.isabs(mp) else os.path.normpath(os.path.join(root, mp))
        return os.path.join(os.path.dirname(root), "board-mirror")

    def _run_auto_sync_once(self):
        root = getattr(self, "_auto_sync_root", None)
        if not root:
            return False
        m = self._resolve_mirror_dir()
        if not m or not os.path.exists(os.path.join(m, ".git")):
            return False
        here = os.path.dirname(os.path.abspath(__file__))
        kit_candidates = [
            os.path.join(root, ".kit", "kit.py"),
            os.path.join(root, "scripts", "kit.py"),
            os.path.join(here, "kit.py"),
            os.path.join(os.path.dirname(os.path.dirname(here)), "scripts", "kit.py"),
        ]
        kit_py = next((p for p in kit_candidates if os.path.isfile(p)), None)
        if not kit_py:
            return False
        try:
            r = subprocess.run(
                [sys.executable, "-I", kit_py, "--mirror", m, "sync-db", "--db", self.db_path, "--commit"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode == 0:
                self.auto_sync_count += 1
                if getattr(self, "_auto_sync_push", False):
                    pr = subprocess.run(
                        [sys.executable, "-I", kit_py, "--mirror", m, "push"],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    # refused: git moved (a web session saved there). Reconcile, at most every 5 minutes: the newer
                    # copy of each post comes here, then the union goes to git (a plain push never overwrites git).
                    url = getattr(self, "_auto_sync_url", "")
                    if pr.returncode != 0 and url and time.time() - getattr(self, "_auto_reconcile_at", 0) > 300:
                        self._auto_reconcile_at = time.time()
                        env = dict(os.environ, KIT_BOARD_URL=url)
                        if getattr(self, "_auto_sync_token", ""):
                            env["KIT_BOARD_TOKEN"] = self._auto_sync_token
                        subprocess.run([sys.executable, "-I", kit_py, "--mirror", m, "reconcile", "--db", self.db_path],
                                       cwd=root, capture_output=True, text=True, timeout=180, env=env)
                return True
        except Exception:
            pass
        return False

    def _auto_sync_worker(self):
        while True:
            with self._auto_sync_cond:
                while not self._auto_sync_dirty and not self._auto_sync_closed:
                    self._auto_sync_cond.wait()
                if self._auto_sync_closed:
                    return
                self._auto_sync_cond.wait(timeout=self._auto_sync_interval)
                if self._auto_sync_closed:
                    return
                self._auto_sync_dirty = False
            self._run_auto_sync_once()

    def save_artifact(self, post_id, rel_path, content, summary="", base_path="", by_user="owner", agent_meta=None):
        pid = str(post_id or "").strip()
        rp = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not pid or not rp or ".." in rp.split("/"):
            return None, "invalid_path"
        txt = str(content or "")
        raw_b = txt.encode("utf-8")
        if len(raw_b) > MAX_REPO_DOC_BYTES:
            return None, "too_large"
        bp = str(base_path or "").strip().replace("\\", "/").lstrip("/")
        if ".." in bp.split("/"):
            bp = ""
        meta = agent_meta if isinstance(agent_meta, dict) else {}
        clean_meta = {
            "harness": str(meta.get("harness") or "")[:40],
            "model": str(meta.get("model") or "")[:64],
            "effort": str(meta.get("effort") or "")[:24],
            "user": str(meta.get("user") or by_user or "owner")[:32],
        }
        clean_meta = {k: v for k, v in clean_meta.items() if v}
        aid = "art-" + secrets.token_hex(6)
        now = utc_now()
        sm = str(summary or "").strip()[:240]
        with self.lock:
            self.conn.execute(
                "INSERT INTO artifacts(artifact_id, post_id, path, base_path, summary, content, size, by_user, agent_meta, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, pid, rp, bp, sm, txt, len(raw_b), str(by_user or "owner")[:32], json.dumps(clean_meta, ensure_ascii=False), now),
            )
            self.conn.commit()
        return {
            "artifact_id": aid,
            "post_id": pid,
            "path": rp,
            "base_path": bp,
            "summary": sm,
            "size": len(raw_b),
            "by_user": str(by_user or "owner")[:32],
            "agent_meta": clean_meta,
            "created_at": now,
        }, None

    def list_artifacts(self, post_id=None):
        with self.lock:
            if post_id:
                rows = self.conn.execute(
                    "SELECT artifact_id, post_id, path, base_path, summary, size, by_user, agent_meta, created_at "
                    "FROM artifacts WHERE post_id=? ORDER BY created_at DESC",
                    (str(post_id),),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT artifact_id, post_id, path, base_path, summary, size, by_user, agent_meta, created_at "
                    "FROM artifacts ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
        out = []
        for r in rows:
            try:
                am = json.loads(r[7] or "{}")
            except ValueError:
                am = {}
            out.append({
                "artifact_id": r[0], "post_id": r[1], "path": r[2], "base_path": r[3],
                "summary": r[4], "size": r[5], "by_user": r[6], "agent_meta": am, "created_at": r[8],
            })
        return out

    def get_artifact(self, artifact_id):
        with self.lock:
            r = self.conn.execute(
                "SELECT artifact_id, post_id, path, base_path, summary, content, size, by_user, agent_meta, created_at "
                "FROM artifacts WHERE artifact_id=?",
                (str(artifact_id or ""),),
            ).fetchone()
        if not r:
            return None
        try:
            am = json.loads(r[8] or "{}")
        except ValueError:
            am = {}
        return {
            "artifact_id": r[0], "post_id": r[1], "path": r[2], "base_path": r[3],
            "summary": r[4], "content": r[5], "size": r[6], "by_user": r[7], "agent_meta": am, "created_at": r[9],
        }

    def delete_artifact(self, artifact_id):
        with self.lock:
            cur = self.conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (str(artifact_id or ""),))
            self.conn.commit()
            return cur.rowcount > 0

    def create_user(self, name, role="member", repo=""):
        nm = str(name or "").strip()[:48]
        if not nm:
            return None
        r = str(role or "member").strip().lower()
        if r not in ("viewer", "member", "owner"):
            r = "member"
        rp = str(repo or "").strip()[:64]
        base = re.sub(r"[^a-z0-9_-]+", "-", nm.lower()).strip("-")[:24] or "user"
        if base in (ACCESS.RESERVED if ACCESS else ("owner", "all", "system")) or base.startswith(("orchestrator", "owner", "session")):
            base = "u-" + base[:17] + "-" + secrets.token_hex(2)
        now = utc_now()
        tok = secrets.token_hex(16)
        with self.lock:
            uid = base
            if self.conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
                uid = base[:19] + "-" + secrets.token_hex(2)
            self.conn.execute(
                "INSERT INTO users(user_id, name, role, token, repo, created_at, revoked) VALUES(?,?,?,?,?,?,0)",
                (uid, nm, r, tok, rp, now),
            )
            self.conn.commit()
        return {"user_id": uid, "name": nm, "role": r, "token": tok, "repo": rp, "created_at": now}

    def list_users(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT user_id, name, role, token, repo, created_at FROM users WHERE revoked=0 ORDER BY created_at DESC"
            ).fetchall()
        return [{"user_id": r[0], "name": r[1], "role": r[2], "token": r[3], "repo": r[4] or "", "created_at": r[5]} for r in rows]

    def revoke_user(self, user_id):
        uid = str(user_id or "")
        with self.lock:
            cur = self.conn.execute("UPDATE users SET revoked=1 WHERE user_id=? AND revoked=0", (uid,))
            if cur.rowcount > 0:
                rows = self.conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts'").fetchall()
                for did, raw in rows:
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if d.get("working_by") == uid:
                        d["working_by"] = ""
                        self.set_doc("posts", did, d)
                self.conn.execute("UPDATE shares SET revoked=1 WHERE created_by=?", (uid,))     # the links they gave out go too
            self.conn.commit()
            return cur.rowcount > 0

    def lookup_user_by_token(self, tok):
        t = str(tok or "").strip()
        if not t:
            return None
        with self.lock:
            rows = self.conn.execute("SELECT user_id, name, role, token, repo FROM users WHERE revoked=0").fetchall()
        for r in rows:
            if hmac.compare_digest(str(r[3]), t):
                return {"id": r[0], "name": r[1], "role": r[2], "token": r[3], "repo": r[4] or ""}
        return None

    def create_share(self, post_id, ttl_hours=24.0, allow_write=True, files=None, max_submissions=5, repo="", created_by=""):
        tok = secrets.token_urlsafe(24)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        exp = (now_dt + timedelta(hours=max(0.1, min(float(ttl_hours or 24.0), 720.0)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_files = []
        for f in (files or [])[:10]:
            if isinstance(f, dict) and f.get("name"):
                safe_files.append({"name": str(f["name"])[:120], "content": str(f.get("content") or "")[:200000]})
        with self.lock:
            self.conn.execute(
                "INSERT INTO shares(token, post_id, repo, allow_write, files_json, expires_at, created_at, revoked, submissions, max_submissions, created_by) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
                (tok, post_id, str(repo or ""), 1 if allow_write else 0, json.dumps(safe_files, ensure_ascii=False), exp, now,
                 max(1, min(int(max_submissions or 5), 100)), str(created_by or "")),
            )
            self.conn.commit()
        return {"token": tok, "post_id": post_id, "repo": str(repo or ""), "path": "/_share/" + tok, "allow_write": bool(allow_write), "expires_at": exp, "created_at": now}

    def get_share(self, tok):
        with self.lock:
            row = self.conn.execute(
                "SELECT token, post_id, repo, allow_write, files_json, expires_at, created_at, revoked, submissions, max_submissions, created_by FROM shares WHERE token=?",
                (tok,),
            ).fetchone()
        if not row:
            return None
        return {
            "token": row[0], "post_id": row[1], "repo": row[2] or "", "allow_write": bool(row[3]),
            "files": json.loads(row[4] or "[]"), "expires_at": row[5], "created_at": row[6],
            "revoked": bool(row[7]), "submissions": int(row[8]), "max_submissions": int(row[9]), "created_by": row[10] or "",
        }

    def list_shares(self, post_id=None):
        with self.lock:
            if post_id:
                rows = self.conn.execute(
                    "SELECT token, post_id, allow_write, expires_at, created_at, revoked, submissions, max_submissions, created_by FROM shares WHERE post_id=? ORDER BY created_at DESC",
                    (post_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT token, post_id, allow_write, expires_at, created_at, revoked, submissions, max_submissions, created_by FROM shares ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        return [
            {"token": r[0], "post_id": r[1], "path": "/_share/" + r[0], "allow_write": bool(r[2]),
             "expires_at": r[3], "created_at": r[4], "revoked": bool(r[5]), "submissions": int(r[6]), "max_submissions": int(r[7]), "created_by": r[8] or ""}
            for r in rows
        ]

    def revoke_share(self, tok):
        with self.lock:
            cur = self.conn.execute("UPDATE shares SET revoked=1 WHERE token=?", (tok,))
            self.conn.commit()
            return cur.rowcount > 0

    def increment_share_submissions(self, tok):
        with self.lock:
            self.conn.execute("UPDATE shares SET submissions=submissions+1 WHERE token=?", (tok,))
            self.conn.commit()

    def close(self):
        with self._auto_sync_cond:
            self._auto_sync_closed = True
            self._auto_sync_cond.notify_all()
        with self.lock:
            self.conn.close()

    def notify_sse(self, collection, doc_id):
        if self._auto_sync_thread is not None and (collection in ("posts", "comments") or (collection.startswith("data/users/") and doc_id == "board-seen")):
            with self._auto_sync_cond:
                self._auto_sync_dirty = True
                self._auto_sync_cond.notify_all()
        payload = json.dumps({"collection": collection, "doc_id": doc_id, "path": f"{collection}/{doc_id}"})
        with self.sse_lock:
            dead = []
            for q in self.sse_clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.sse_clients.discard(q)

    def notify_ui_reload(self, reason="ui"):
        payload = json.dumps({"ui_reload": True, "reason": str(reason or "ui")})
        with self.sse_lock:
            dead = []
            for q in self.sse_clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.sse_clients.discard(q)

    def get_doc(self, collection, doc_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
        if not row:
            return {"exists": False, "id": doc_id, "version": 0, "data": {}}
        return {"exists": True, "id": doc_id, "version": row[0], "data": json.loads(row[1])}

    def _sql_where_pushdown(self, where):
        """Translate safe simple where clauses into SQLite json_extract predicates."""
        if not self._has_json1 or not where:
            return "", []
        sql_parts = []
        params = []
        for clause in where:
            if not isinstance(clause, (list, tuple)) or len(clause) != 3:
                continue
            field, op, val = clause
            if not isinstance(field, str) or not re.match(r"^[A-Za-z0-9_]+$", field):
                continue
            jpath = "$." + field
            if op == "==" and isinstance(val, (str, int, float)) and not isinstance(val, bool):
                sql_parts.append("json_extract(data, ?) = ?")
                params.extend([jpath, val])
            elif op in (">", ">=", "<", "<=") and isinstance(val, (str, int, float)) and not isinstance(val, bool):
                sql_parts.append("json_extract(data, ?) %s ?" % op)
                params.extend([jpath, val])
        if not sql_parts:
            return "", []
        return " AND " + " AND ".join(sql_parts), params

    def list_docs(self, collection, where=None, order_by=None, direction="asc", limit=None, cursor=0):
        extra_sql, extra_params = self._sql_where_pushdown(where)
        with self.lock:
            rows = self.conn.execute(
                "SELECT doc_id, version, data FROM docs WHERE collection=?" + extra_sql + " ORDER BY doc_id ASC",
                [collection] + extra_params,
            ).fetchall()
        docs = []
        for doc_id, ver, raw in rows:
            d = json.loads(raw)
            if where and not match_where(d, where):
                continue
            docs.append({"id": doc_id, "version": ver, "data": d})
        if order_by:
            rev = str(direction).lower() == "desc"
            docs.sort(key=lambda x: str(x["data"].get(order_by) or ""), reverse=rev)
        start = max(0, int(cursor or 0))
        if limit is None:
            page = docs[start:]
            next_cursor = None
        else:
            lim = max(1, int(limit))
            page = docs[start:start + lim]
            next_cursor = str(start + lim) if (start + lim) < len(docs) else None
        return {"docs": page, "next_cursor": next_cursor, "total": len(docs)}

    def _record_change(self, collection, doc_id, op, version, now):
        cur = self.conn.execute(
            "INSERT INTO changes(collection, doc_id, op, version, at, actor) VALUES (?, ?, ?, ?, ?, ?)",
            (collection, doc_id, op, version, now, str(getattr(ACTOR, "name", "") or "")[:80]),
        )
        seq = cur.lastrowid or 0
        if self.MAX_CHANGES_LOG and seq > self.MAX_CHANGES_LOG and (seq % 100 == 0 or self.MAX_CHANGES_LOG < 200):
            self.conn.execute("DELETE FROM changes WHERE seq <= ?", (seq - self.MAX_CHANGES_LOG,))

    def list_changes(self, since=0, limit=1000):
        with self.lock:
            rows = self.conn.execute(
                "SELECT seq, collection, doc_id, op, version, at, actor FROM changes WHERE seq>? ORDER BY seq ASC LIMIT ?",
                (int(since or 0), max(1, int(limit or 1000))),
            ).fetchall()
            latest = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
        items = [{"seq": r[0], "collection": r[1], "doc_id": r[2], "op": r[3], "version": r[4], "at": r[5], "actor": r[6]} for r in rows]
        return {"changes": items, "latest_seq": latest}

    def comment_counts(self):
        """Compact per-post comment counts for fast board scaling at 1,000+ comments."""
        if self._has_json1:
            with self.lock:
                rows = self.conn.execute(
                    "SELECT json_extract(data, '$.post_id') AS pid, COUNT(*), MAX(COALESCE(json_extract(data, '$.created_at'), '')) "
                    "FROM docs WHERE collection='comments' "
                    "AND COALESCE(json_extract(data, '$.removed'), 0) = 0 "
                    "AND COALESCE(json_extract(data, '$.post_id'), '') != '' "
                    "GROUP BY pid"
                ).fetchall()
            return {"counts": {str(r[0]): {"total": int(r[1]), "latest_at": str(r[2] or "")} for r in rows if r[0]}}
        with self.lock:
            rows = self.conn.execute("SELECT data FROM docs WHERE collection='comments'").fetchall()
        counts = {}
        for (raw,) in rows:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("removed"):
                continue
            pid = d.get("post_id") or ""
            if not pid:
                continue
            cur = counts.setdefault(pid, {"total": 0, "latest_at": ""})
            cur["total"] += 1
            at = str(d.get("created_at") or "")
            if at > cur["latest_at"]:
                cur["latest_at"] = at
        return {"counts": counts}

    def _ack_thread_if_seen(self, collection, data):
        if collection == "comments" and isinstance(data, dict) and data.get("read") and data.get("thread_id"):
            self.conn.execute(
                "UPDATE threads SET dispatched=1, dispatched_at=0 WHERE thread_id=?",
                (str(data["thread_id"]),),
            )

    def _sync_post_link_and_cards(self, doc_id, data, now):
        """Maintain the `links` DAG table and auto-materialize posts/_dashboard and posts/_trains on any post write."""
        if not doc_id or doc_id.startswith("_") or not isinstance(data, dict):
            return []
        if data.get("removed"):
            self.conn.execute("DELETE FROM links WHERE post_id=?", (doc_id,))
        else:
            part = str(data.get("part") or doc_id)
            train = str(data.get("train") or "").strip()
            if train.startswith("P-"):
                train = train[2:]
            parent_part = str(data.get("parent") or "").strip()
            if not train and parent_part:
                par_clean = parent_part[2:] if parent_part.startswith("P-") else parent_part
                prow = self.conn.execute(
                    "SELECT train FROM links WHERE part=? OR post_id=? OR part=? OR post_id=? LIMIT 1",
                    (parent_part, parent_part, par_clean, "P-" + par_clean),
                ).fetchone()
                if prow and prow[0]:
                    train = str(prow[0])
                elif parent_part.startswith("P-"):
                    train = par_clean
            title = str(data.get("title") or doc_id)
            col = str(data.get("column") or "info")
            st = str(data.get("status") or "")
            prio = int(data.get("priority")) if str(data.get("priority") or "") in ("1", "2", "3") else 2
            sid = str(data.get("session_id") or "")
            cat = str(data.get("created_at") or now)
            if train:
                self.conn.execute(
                    "INSERT INTO links(post_id, part, train, parent_part, title, column_name, status, priority, session_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(post_id) DO UPDATE SET part=excluded.part, train=excluded.train, parent_part=excluded.parent_part, "
                    "title=excluded.title, column_name=excluded.column_name, status=excluded.status, priority=excluded.priority, "
                    "session_id=excluded.session_id, updated_at=excluded.updated_at",
                    (doc_id, part, train, parent_part, title, col, st, prio, sid, cat, now),
                )
            else:
                self.conn.execute("DELETE FROM links WHERE post_id=?", (doc_id,))

        return self._materialize_cards_locked(now)

    def _materialize_cards_locked(self, now):
        """Rebuild posts/_dashboard and posts/_trains in-transaction from memory + links table."""
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            if here not in sys.path:
                sys.path.insert(0, here)
            import kit_cards as _kc
        except Exception:
            return []

        rows = self.conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts'").fetchall()
        posts_map = {}
        for pid, raw in rows:
            try:
                posts_map[pid] = json.loads(raw)
            except ValueError:
                pass

        root = getattr(self, "root_dir", None) or os.path.dirname(os.path.dirname(self.data_dir))
        prev_root = _kc.ROOT
        try:
            _kc.ROOT = root
            dash_doc = _kc.build(now, board=posts_map)
            trains_doc = _kc.build_trains(posts_map, dash_doc["rows"], now)
            for r in dash_doc["rows"]:
                if r["state"] in ("ready", "idle") and any(x["state"] == "running" for x in trains_doc["rows"] if x["parent"] == r["train"]):
                    r["state"] = "running"
        except Exception:
            return []
        finally:
            _kc.ROOT = prev_root

        # Preserve existing curated _dashboard and _trains rows (e.g. Harmonize's 20 rich rows & 198 board-trains rows)
        # and only merge/update rows that correspond to actual linked trains in `links`.
        cur_dash = posts_map.get("_dashboard")
        cur_rows = (cur_dash.get("rows") or []) if isinstance(cur_dash, dict) else []
        new_rows = dash_doc.get("rows") or []
        if cur_rows:
            if not new_rows:
                return []
            placeholders = sum(1 for r in new_rows if _kc.is_placeholder_row(r))
            if placeholders * 2 > len(new_rows):
                return []
            # If existing _dashboard has legacy curated rows that carry no `train` field (e.g. Harmonize's SCOREBOARD import),
            # preserve those legacy rows and merge/update any train rows from `new_rows`!
            has_curated_rows = any(not r.get("train") and r.get("state") in ("you", "running", "done") for r in cur_rows)
            if has_curated_rows:
                by_new_train = {str(nr.get("train")): nr for nr in new_rows if nr.get("train")}
                merged_dash_rows = []
                seen_trains = set()
                for cr in cur_rows:
                    t_id = str(cr.get("train") or "")
                    if t_id and t_id in by_new_train:
                        merged_dash_rows.append(by_new_train[t_id])
                        seen_trains.add(t_id)
                    else:
                        merged_dash_rows.append(cr)
                        if t_id:
                            seen_trains.add(t_id)
                for nr in new_rows:
                    t_id = str(nr.get("train") or "")
                    if t_id and t_id not in seen_trains and any(lk[0] == t_id[2:] for lk in self.conn.execute("SELECT DISTINCT train FROM links").fetchall()):
                        merged_dash_rows.append(nr)
                        seen_trains.add(t_id)
                dash_doc["rows"] = merged_dash_rows

        cur_trains = posts_map.get("_trains")
        cur_t_rows = (cur_trains.get("rows") or []) if isinstance(cur_trains, dict) else []
        new_t_rows = trains_doc.get("rows") or []
        if cur_t_rows and len(cur_t_rows) > len(new_t_rows):
            by_existing_part = {str(r.get("part")): dict(r) for r in cur_t_rows if r.get("part")}
            for tr in new_t_rows:
                pt = str(tr.get("part") or "")
                if not pt:
                    continue
                if pt not in by_existing_part:
                    if not pt.startswith("P-") or tr.get("children"):
                        cur_t_rows.append(tr)
                        by_existing_part[pt] = tr
                elif tr.get("children"):
                    ex = by_existing_part[pt]
                    ex_kids = list(ex.get("children") or [])
                    for k in tr["children"]:
                        if k not in ex_kids:
                            ex_kids.append(k)
                    ex["children"] = ex_kids
                    if tr.get("state") in ("you", "running"):
                        ex["state"] = tr["state"]
            trains_doc["rows"] = cur_t_rows

        touched = []
        for special_id, special_doc in (("_dashboard", dash_doc), ("_trains", trains_doc)):
            if not new_rows and not (trains_doc.get("rows") or []) and special_id not in posts_map:
                continue
            v_row = self.conn.execute("SELECT version, data FROM docs WHERE collection='posts' AND doc_id=?", (special_id,)).fetchone()
            if v_row:
                try:
                    old_d = json.loads(v_row[1])
                    if old_d.get("rows") == special_doc.get("rows"):
                        continue
                except ValueError:
                    pass
            new_v = (v_row[0] if v_row else 0) + 1
            self.conn.execute(
                "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES ('posts', ?, ?, ?, ?) "
                "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                (special_id, new_v, json.dumps(special_doc, ensure_ascii=False), now),
            )
            self._record_change("posts", special_id, "auto_links", new_v, now)
            touched.append(("posts", special_id))
        return touched

    def list_links(self, train=None):
        """Return the DAG links table + train summary for compact agent queries."""
        with self.lock:
            if train:
                t_clean = train[2:] if train.startswith("P-") else train
                rows = self.conn.execute(
                    "SELECT post_id, part, train, parent_part, title, column_name, status, priority, session_id, created_at, updated_at "
                    "FROM links WHERE train=? ORDER BY created_at ASC, post_id ASC",
                    (t_clean,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT post_id, part, train, parent_part, title, column_name, status, priority, session_id, created_at, updated_at "
                    "FROM links ORDER BY train ASC, created_at ASC, post_id ASC"
                ).fetchall()
        links = []
        by_train = {}
        for r in rows:
            item = {
                "post_id": r[0], "part": r[1], "train": r[2], "parent": r[3],
                "title": r[4], "column": r[5], "status": r[6], "priority": r[7],
                "session_id": r[8], "created_at": r[9], "updated_at": r[10],
            }
            links.append(item)
            by_train.setdefault(r[2], []).append(item["part"])
        return {"total": len(links), "trains": by_train, "links": links}

    def set_doc(self, collection, doc_id, data, if_version=None):
        now = utc_now()
        extra_notifies = []
        with self.lock:
            row = self.conn.execute(
                "SELECT version FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
            cur_ver = row[0] if row else 0
            if if_version is not None and int(if_version) != cur_ver:
                return None, cur_ver
            new_ver = cur_ver + 1
            self.conn.execute(
                "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                (collection, doc_id, new_ver, json.dumps(data, ensure_ascii=False), now),
            )
            self._ack_thread_if_seen(collection, data)
            self._record_change(collection, doc_id, "set", new_ver, now)
            if collection == "posts" and not doc_id.startswith("_"):
                extra_notifies = self._sync_post_link_and_cards(doc_id, data, now)
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        for c_ext, d_ext in extra_notifies:
            self.notify_sse(c_ext, d_ext)
        return {"id": doc_id, "version": new_ver, "data": data}, None

    def update_doc(self, collection, doc_id, patch, if_version=None):
        now = utc_now()
        extra_notifies = []
        with self.lock:
            row = self.conn.execute(
                "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
            cur_ver = row[0] if row else 0
            if if_version is not None and int(if_version) != cur_ver:
                return None, cur_ver
            cur_data = json.loads(row[1]) if row else {}
            _deep_merge_doc(cur_data, patch, doc_id=doc_id)
            if collection == "posts" and isinstance(patch, dict) and "updated_at" not in patch:
                cur_data["updated_at"] = now
            new_ver = cur_ver + 1
            self.conn.execute(
                "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                (collection, doc_id, new_ver, json.dumps(cur_data, ensure_ascii=False), now),
            )
            self._ack_thread_if_seen(collection, cur_data)
            self._record_change(collection, doc_id, "update", new_ver, now)
            if collection == "posts" and not doc_id.startswith("_"):
                extra_notifies = self._sync_post_link_and_cards(doc_id, cur_data, now)
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        for c_ext, d_ext in extra_notifies:
            self.notify_sse(c_ext, d_ext)
        return {"id": doc_id, "version": new_ver, "data": cur_data}, None

    def str_replace(self, collection, doc_id, field, old_str, new_str, if_version=None):
        now = utc_now()
        with self.lock:
            row = self.conn.execute(
                "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
            if not row:
                return None, "not_found"
            cur_ver, raw = row
            if if_version is not None and int(if_version) != cur_ver:
                return None, cur_ver
            cur_data = json.loads(raw)
            val = cur_data.get(field)
            if not isinstance(val, str) or old_str not in val:
                return None, "old_str_not_found"
            cur_data[field] = val.replace(old_str, new_str, 1)
            if collection == "posts":
                cur_data["updated_at"] = now
            new_ver = cur_ver + 1
            self.conn.execute(
                "UPDATE docs SET version=?, data=?, updated_at=? WHERE collection=? AND doc_id=?",
                (new_ver, json.dumps(cur_data, ensure_ascii=False), now, collection, doc_id),
            )
            self._record_change(collection, doc_id, "str_replace", new_ver, now)
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        return {"id": doc_id, "version": new_ver, "data": cur_data}, None

    def batch_ops(self, ops):
        now = utc_now()
        results = []
        notifies = []
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                for w in ops or []:
                    op = w.get("op") or "set"
                    coll, doc_id = w["collection"], w["doc_id"]
                    if_version = w.get("if_version")
                    if op == "set":
                        row = self.conn.execute(
                            "SELECT version FROM docs WHERE collection=? AND doc_id=?",
                            (coll, doc_id),
                        ).fetchone()
                        cur_ver = row[0] if row else 0
                        if if_version is not None and int(if_version) != cur_ver:
                            self.conn.rollback()
                            return None, ("conflict", doc_id, cur_ver)
                        new_ver = cur_ver + 1
                        data = w.get("data", {})
                        self.conn.execute(
                            "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                            (coll, doc_id, new_ver, json.dumps(data, ensure_ascii=False), now),
                        )
                        self._record_change(coll, doc_id, "set", new_ver, now)
                    elif op == "update":
                        row = self.conn.execute(
                            "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                            (coll, doc_id),
                        ).fetchone()
                        cur_ver = row[0] if row else 0
                        if if_version is not None and int(if_version) != cur_ver:
                            self.conn.rollback()
                            return None, ("conflict", doc_id, cur_ver)
                        cur_data = json.loads(row[1]) if row else {}
                        patch = w.get("data", {})
                        _deep_merge_doc(cur_data, patch, doc_id=doc_id)
                        if coll == "posts" and isinstance(patch, dict) and "updated_at" not in patch:
                            cur_data["updated_at"] = now
                        new_ver = cur_ver + 1
                        self.conn.execute(
                            "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                            (coll, doc_id, new_ver, json.dumps(cur_data, ensure_ascii=False), now),
                        )
                        self._ack_thread_if_seen(coll, cur_data)
                        self._record_change(coll, doc_id, "update", new_ver, now)
                    elif op == "str_replace":
                        row = self.conn.execute(
                            "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                            (coll, doc_id),
                        ).fetchone()
                        if not row:
                            self.conn.rollback()
                            return None, ("conflict", doc_id, "not_found")
                        cur_ver, raw = row
                        if if_version is not None and int(if_version) != cur_ver:
                            self.conn.rollback()
                            return None, ("conflict", doc_id, cur_ver)
                        cur_data = json.loads(raw)
                        val = cur_data.get(w["field"])
                        if not isinstance(val, str) or w["old_str"] not in val:
                            self.conn.rollback()
                            return None, ("conflict", doc_id, "old_str_not_found")
                        cur_data[w["field"]] = val.replace(w["old_str"], w["new_str"], 1)
                        if coll == "posts":
                            cur_data["updated_at"] = now
                        new_ver = cur_ver + 1
                        self.conn.execute(
                            "UPDATE docs SET version=?, data=?, updated_at=? WHERE collection=? AND doc_id=?",
                            (new_ver, json.dumps(cur_data, ensure_ascii=False), now, coll, doc_id),
                        )
                        self._record_change(coll, doc_id, "str_replace", new_ver, now)
                    else:
                        self.conn.rollback()
                        return None, ("unknown_op", doc_id, str(op))
                    results.append({"collection": coll, "doc_id": doc_id, "version": new_ver})
                    notifies.append((coll, doc_id))
                    if coll == "posts" and not doc_id.startswith("_"):
                        post_data_for_link = data if op == "set" else cur_data
                        notifies.extend(self._sync_post_link_and_cards(doc_id, post_data_for_link, now))
                self.conn.commit()
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise
        for coll, doc_id in notifies:
            self.notify_sse(coll, doc_id)
        return results, None

    def save_blob(self, raw_bytes, content_type="application/octet-stream", filename="file", blob_id=None):
        if len(raw_bytes) > 20 * 1024 * 1024:
            raise ValueError("blob exceeds 20 MB")
        # CL30: `kit.py artifact load` keeps an Artifact picture's own id, so the posts' /_blob/<id> links open here
        if not (blob_id and re.fullmatch(r"[0-9a-f]{32}", blob_id)):
            blob_id = hashlib.md5(raw_bytes + os.urandom(8)).hexdigest()
        with open(os.path.join(self.blob_dir, blob_id), "wb") as fh:
            fh.write(raw_bytes)
        meta = {"id": blob_id, "contentType": content_type, "name": filename, "size": len(raw_bytes)}
        with open(os.path.join(self.blob_dir, blob_id + ".json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        return meta

    def read_blob(self, blob_id):
        if not blob_id or len(blob_id) != 32 or any(c not in "0123456789abcdef" for c in blob_id.lower()):
            return None, None
        p = os.path.join(self.blob_dir, blob_id)
        mp = p + ".json"
        if not os.path.isfile(p):
            return None, None
        meta = json.load(open(mp, encoding="utf-8")) if os.path.isfile(mp) else {"contentType": "application/octet-stream"}
        return open(p, "rb").read(), meta

    def set_watch(self, watcher_id, on=True):
        with self.comment_cond:
            if on:
                self.watchers.add(watcher_id)
            else:
                self.watchers.discard(watcher_id)
            return sorted(self.watchers)

    def send_comment(self, anchor, text, by="owner", quiet=False):
        """A wake for the board's agents. `by`: the person it comes from; `quiet`: kept, never handed to the clerk."""
        tid = "thr_" + uuid.uuid4().hex[:12]
        now = utc_now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO threads(thread_id, anchor, text, status, created_at, dispatched, replies, by_user) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)",
                (tid, json.dumps(anchor or {}), text, "sent to you — awaiting reply", now, 1 if quiet else 0, by),
            )
            self.conn.commit()
        with self.comment_cond:
            self.comment_cond.notify_all()
        return {"thread_id": tid, "created_at": now}

    def wait_comment(self, timeout=300, lease_sec=None):
        lease = float(self.COMMENT_LEASE_SEC if lease_sec is None else lease_sec)
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            now_ts = time.time()
            with self.lock:
                row = self.conn.execute(
                    "SELECT thread_id, anchor, text, status, created_at, replies, by_user FROM threads "
                    "WHERE status=? AND (dispatched=0 OR (dispatched=2 AND dispatched_at > 0 AND (? - dispatched_at) >= ?)) "
                    "ORDER BY created_at ASC LIMIT 1",
                    ("sent to you — awaiting reply", now_ts, lease),
                ).fetchone()
                if row:
                    self.conn.execute(
                        "UPDATE threads SET dispatched=2, dispatched_at=? WHERE thread_id=?",
                        (now_ts, row[0]),
                    )
                    self.conn.commit()
                    return {
                        "event": "comment",
                        "thread_id": row[0],
                        "anchor": json.loads(row[1]),
                        "text": row[2],
                        "status": row[3],
                        "created_at": row[4],
                        "replies": json.loads(row[5]),
                        "by": row[6] or "owner",
                    }
            rem = deadline - time.monotonic()
            if rem <= 0:
                return {"event": "timeout"}
            with self.comment_cond:
                self.comment_cond.wait(timeout=min(rem, 5.0))

    def ack_thread(self, thread_id):
        with self.lock:
            row = self.conn.execute("SELECT thread_id, status FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            if not row:
                return None
            self.conn.execute("UPDATE threads SET dispatched=1, dispatched_at=0 WHERE thread_id=?", (thread_id,))
            self.conn.commit()
            return {"thread_id": row[0], "status": row[1], "dispatched": 1}

    def get_thread(self, thread_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT thread_id, anchor, text, status, created_at, replies, by_user FROM threads WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "thread_id": row[0],
            "anchor": json.loads(row[1]),
            "text": row[2],
            "status": row[3],
            "created_at": row[4],
            "replies": json.loads(row[5]),
            "by": row[6] or "owner",
        }

    def reply_thread(self, thread_id, text):
        now = utc_now()
        with self.lock:
            row = self.conn.execute("SELECT replies FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            if not row:
                return None
            replies = json.loads(row[0])
            replies.append({"text": text, "created_at": now})
            self.conn.execute(
                "UPDATE threads SET replies=?, status=?, dispatched=1, dispatched_at=0 WHERE thread_id=?",
                (json.dumps(replies, ensure_ascii=False), "replied", thread_id),
            )
            self.conn.commit()
        return {"thread_id": thread_id, "replies": replies, "status": "replied"}


def _sibling(*names):
    """A module of the kit installed beside this file (its source sits beside this folder in the kit's repo)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for n in names:
        for p in (os.path.join(here, n), os.path.join(os.path.dirname(here), n)):
            if os.path.isfile(p):
                import importlib.util
                spec = importlib.util.spec_from_file_location("kit_" + n[:-3] + "_rules", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
    return None


NOTIFY = _sibling("notify.py")          # the one rule for phone notifications
ACCESS = _sibling("access.py")          # who may do what; without it, only the owner's token opens anything
GATE = _sibling("post_gate.py")


def read_git_diff(root_dir, ref):
    if not ref or not re.match(r"^[0-9a-fA-F]{6,40}$", ref):
        return None, "invalid_ref"
    r_stat = subprocess.run(["git", "-C", root_dir, "show", "--stat", "--format=%h %s (%an, %cr)", ref],
                            capture_output=True, text=True, timeout=10)
    if r_stat.returncode != 0:
        return None, "not_found"
    r_diff = subprocess.run(["git", "-C", root_dir, "show", "--format=", ref],
                            capture_output=True, text=True, timeout=10)
    diff = (r_diff.stdout or "")[:32768]
    if len(r_diff.stdout or "") > 32768:
        diff += "\n... (truncated at 32 KB)"
    return {"ref": ref, "stat": r_stat.stdout.strip(), "diff": diff}, None


REPO_DOC_SKIP_DIRS = {".git", "node_modules", ".pytest_cache", "data-cache", "__pycache__", ".venv", "venv", "build", "dist", "board_data"}
MAX_REPO_DOC_BYTES = 2 * 1024 * 1024
REPO_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"}
REPO_HTML_EXTS = {".html", ".htm"}
REPO_ASSET_CTYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
# what a shared HTML page may load from its own folder
PAGE_PARTS = {".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico", ".woff", ".woff2"}
REPO_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}
REPO_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}


def _repo_file_kind(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".md":
        return "md"
    if ext in REPO_IMAGE_EXTS:
        return "img"
    if ext in REPO_HTML_EXTS:
        return "html"
    if ext == ".pdf":
        return "pdf"
    if ext in REPO_AUDIO_EXTS:
        return "audio"
    if ext in REPO_VIDEO_EXTS:
        return "video"
    return ""


def _classify_repo_doc(rel):
    kind = _repo_file_kind(rel)
    if kind == "img":
        return "images"
    if kind == "html":
        return "html"
    if kind in ("pdf", "audio", "video"):
        return "media"
    r = rel.replace("\\", "/")
    if r.startswith(".story/") or r.startswith(".board/memory/"):
        return "memory"
    if r.startswith("docs/prompts/"):
        return "prompts"
    if r.startswith("docs/"):
        return "docs"
    if r.startswith(".kit/"):
        return "kit"
    if "/" not in r:
        return "root"
    return "other"


def list_repo_docs(root_dir, query="", limit=2500):
    root_real = os.path.realpath(root_dir)
    ql = (query or "").strip().lower()
    pinned_set = set()
    try:
        cfg = json.load(open(os.path.join(root_real, ".kit", "config.json"), encoding="utf-8"))
        for p in (cfg.get("kit", {}).get("pinned_docs") or []):
            if isinstance(p, str) and p.strip():
                pinned_set.add(p.strip().replace("\\", "/").lstrip("/"))
    except (OSError, ValueError):
        pass
    items = []
    for dirpath, dirs, files in os.walk(root_real):
        dirs[:] = sorted(d for d in dirs if d not in REPO_DOC_SKIP_DIRS)
        for fn in sorted(files):
            kind = _repo_file_kind(fn)
            if not kind:
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root_real).replace("\\", "/")
            if ql and ql not in rel.lower():
                continue
            try:
                st = os.stat(fp)
            except OSError:
                continue
            items.append({
                "path": rel,
                "kind": kind,
                "category": _classify_repo_doc(rel),
                "pinned": rel in pinned_set,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    items.sort(key=lambda x: (0 if x.get("pinned") else 1, -(datetime.strptime(x["mtime"], "%Y-%m-%dT%H:%M:%SZ").timestamp()), x["path"].lower()))
    return {"docs": items, "total": len(items)}


def toggle_pinned_doc(root_dir, rel_path, pinned=None):
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/") or not _repo_file_kind(rel):
        return None, "invalid_path"
    root_real = os.path.realpath(root_dir)
    target = os.path.realpath(os.path.join(root_real, rel))
    if not (target.startswith(root_real + os.sep) and os.path.isfile(target)):
        return None, "not_found"
    norm = os.path.relpath(target, root_real).replace("\\", "/")
    cfg_path = os.path.join(root_real, ".kit", "config.json")
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.isfile(cfg_path) else {}
    except (OSError, ValueError):
        cfg = {}
    kit_cfg = cfg.setdefault("kit", {})
    cur = [str(x).strip().replace("\\", "/").lstrip("/") for x in (kit_cfg.get("pinned_docs") or []) if isinstance(x, str) and x.strip()]
    want = (norm not in cur) if pinned is None else bool(pinned)
    if want and norm not in cur:
        cur.append(norm)
    elif not want and norm in cur:
        cur = [x for x in cur if x != norm]
    kit_cfg["pinned_docs"] = cur
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1, ensure_ascii=False)
    return {"path": norm, "pinned": want, "pinned_docs": cur}, None


def _repo_github_slugs(root_dir):
    slugs = set()
    try:
        r = subprocess.run(["git", "-C", root_dir, "remote", "-v"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for m in re.finditer(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?(?:\s|$|/)", r.stdout or ""):
                slugs.add(m.group(1).lower())
    except Exception:
        pass
    return sorted(slugs)


def read_repo_asset(root_dir, rel_path):
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None, None, "invalid_path"
    ext = os.path.splitext(rel)[1].lower()
    if ext not in REPO_ASSET_CTYPES:
        return None, None, "unsupported_type"
    root_real = os.path.realpath(root_dir)
    target = os.path.realpath(os.path.join(root_real, rel))
    if not (target.startswith(root_real + os.sep) and os.path.isfile(target)):
        return None, None, "not_found"
    try:
        st = os.stat(target)
        if st.st_size > MAX_BLOB_BYTES:
            return None, None, "too_large"
        with open(target, "rb") as fh:
            return fh.read(), REPO_ASSET_CTYPES[ext], None
    except OSError:
        return None, None, "read_error"


def extract_pdf_headings(file_path):
    """Extract PDF bookmarks/outline titles or page headings (zero-dep pure Python, with pdftotext fallback)."""
    headings = []
    try:
        raw = open(file_path, "rb").read(2 * 1024 * 1024)
    except OSError:
        return headings
    # Count /Type /Page objects (excluding /Pages)
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
    # Extract bookmark /Title (...) entries from PDF outline dictionaries
    titles = []
    for m in re.finditer(rb"/Title\s*\(([^()]{1,120})\)", raw):
        try:
            t = m.group(1).decode("latin-1", errors="ignore").strip()
            t = re.sub(r"\\([()\\])", r"\1", t).strip()
            if t and len(t) >= 2 and t not in titles:
                titles.append(t)
        except Exception:
            pass
    if titles:
        for idx, t in enumerate(titles[:60]):
            pg = min(idx + 1, max(1, pages)) if pages else (idx + 1)
            headings.append({"id": "pdf-p-%d" % pg, "text": "%s (p. %d)" % (t, pg), "level": 1, "slug": "page-%d" % pg, "page": pg})
        return headings
    if pages > 0:
        for pg in range(1, min(pages, 100) + 1):
            headings.append({"id": "pdf-p-%d" % pg, "text": "Page %d" % pg, "level": 1, "slug": "page-%d" % pg, "page": pg})
    return headings


def read_repo_doc(root_dir, rel_path, section="", repo_id="primary"):
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    kind = _repo_file_kind(rel)
    if not rel or ".." in rel.split("/") or not kind:
        return None, "invalid_path"
    root_real = os.path.realpath(root_dir)
    target = os.path.realpath(os.path.join(root_real, rel))
    if not (target.startswith(root_real + os.sep) and os.path.isfile(target)):
        return None, "not_found"
    try:
        st = os.stat(target)
    except OSError:
        return None, "read_error"
    norm = os.path.relpath(target, root_real).replace("\\", "/")
    mtime_iso = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind in ("img", "pdf", "audio", "video"):
        max_sz = (100 * 1024 * 1024) if kind in ("audio", "video") else MAX_BLOB_BYTES
        if st.st_size > max_sz:
            return None, "too_large"
        pdf_hs = extract_pdf_headings(target) if kind == "pdf" else []
        return {
            "path": norm,
            "kind": kind,
            "url": "/_api/repo-asset?path=" + urllib.parse.quote(norm, safe="/") + ("&repo=" + urllib.parse.quote(repo_id) if repo_id else ""),
            "size": st.st_size,
            "mtime": mtime_iso,
            "headings": pdf_hs,
            "match_heading": "",
            "content": "",
        }, None
    if st.st_size > MAX_REPO_DOC_BYTES:
        return None, "too_large"
    try:
        with open(target, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None, "read_error"
    if kind == "html":
        rid_seg = urllib.parse.quote(repo_id or "primary", safe="")
        rel_seg = urllib.parse.quote(norm, safe="/")
        return {
            "path": norm,
            "kind": "html",
            "url": "/_repo/" + rid_seg + "/" + rel_seg,
            "size": st.st_size,
            "mtime": mtime_iso,
            "headings": [],
            "match_heading": "",
            "content": content,
        }, None
    headings = []
    sec_q = (section or "").strip().lstrip("§").lstrip("#").strip().lower()
    sec_slug = re.sub(r"[^a-z0-9]+", "-", sec_q).strip("-")
    match_h = ""
    in_fence = False
    for i, line in enumerate(content.splitlines()):
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", line)
        hm = re.search(r"<h([1-4])\b[^>]*>(.*?)</h\1>", line, re.I) if not m else None
        if not m and not hm:
            continue
        if m:
            lvl = len(m.group(1))
            txt = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        else:
            lvl = int(hm.group(1))
            txt = re.sub(r"<[^>]+>", "", hm.group(2)).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")[:48] or "sec"
        hid = "h-%d-%s" % (i, slug)
        headings.append({"level": lvl, "text": txt, "id": hid, "slug": slug})
        if sec_q and not match_h:
            tl = txt.lower()
            if sec_q == tl or (sec_slug and sec_slug == slug) or re.search(r"(?:^|[\s§(])" + re.escape(sec_q) + r"(?:\b|[\s.:)])", tl) or sec_q in tl:
                match_h = hid
    return {
        "path": os.path.relpath(target, root_real).replace("\\", "/"),
        "kind": "md",
        "category": _classify_repo_doc(rel),
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "headings": headings,
        "match_heading": match_h,
        "content": content,
    }, None


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_INLINE_BLOB_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif",
    "application/pdf",
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/flac", "audio/aac", "audio/opus", "audio/webm",
    "video/mp4", "video/webm", "video/quicktime",
}
MAX_BLOB_BYTES = 20 * 1024 * 1024
SERVER_BOOT_ID = secrets.token_hex(4)

MAX_JSON_BYTES = 5 * 1024 * 1024

PWA_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="112" fill="#0b111e"/>'
    '<rect x="76" y="108" width="100" height="296" rx="20" fill="#38bdf8"/>'
    '<rect x="206" y="108" width="100" height="216" rx="20" fill="#818cf8"/>'
    '<rect x="336" y="108" width="100" height="260" rx="20" fill="#34d399"/>'
    '</svg>\n'
)

PWA_SW_JS = (
    'self.addEventListener("install", function(e) { self.skipWaiting(); });\n'
    'self.addEventListener("activate", function(e) { e.waitUntil(self.clients.claim()); });\n'
    'self.addEventListener("fetch", function(e) {\n'
    '  if (e.request.method !== "GET" || e.request.url.indexOf("/_api/events") >= 0) return;\n'
    '  e.respondWith(fetch(e.request));\n'
    '});\n'
)


class BoardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class PayloadTooLarge(Exception):
    pass


def make_handler(root_dir, store, token=None, notify_url=None, embed_mode=False, extra_stores=None):
    html_path = os.path.join(root_dir, ".kit", "board.html")
    project_name = "Board"
    notify_detail_init = False
    tailscale_users = {}
    try:
        _cfg_root = json.load(open(os.path.join(root_dir, ".kit", "config.json"), encoding="utf-8"))
        project_name = _cfg_root.get("project") or "Board"
        notify_detail_init = bool((_cfg_root.get("kit") or {}).get("notify_detail", False))
        raw_tsu = (_cfg_root.get("kit") or {}).get("tailscale_users") or {}
        if isinstance(raw_tsu, dict):
            for k_ts, v_ts in raw_tsu.items():
                kl = str(k_ts or "").strip().lower()
                if not kl:
                    continue
                if isinstance(v_ts, str):
                    tailscale_users[kl] = {"role": v_ts.strip().lower(), "repo": "", "id": ""}
                elif isinstance(v_ts, dict):
                    tailscale_users[kl] = {
                        "role": str(v_ts.get("role") or "member").strip().lower(),
                        "repo": str(v_ts.get("repo") or "").strip(),
                        "id": str(v_ts.get("id") or "").strip(),
                    }
    except (OSError, ValueError):
        pass
    notify_cfg = {"url": str(notify_url or "").strip(), "detail": notify_detail_init}

    def _notify_settings():
        cfg = NOTIFY.settings(root_dir) if NOTIFY else {}
        cfg.update(url=notify_cfg.get("url") or "", detail=bool(notify_cfg.get("detail")))
        return cfg

    def _lane(tstore, coll, doc_id):
        """(a post's lane now, whether it exists): what a notification compares with."""
        if coll != "posts" or not notify_cfg.get("url"):
            return None, False
        cur = tstore.get_doc("posts", doc_id)
        return ((cur.get("data") or {}).get("column"), True) if cur.get("exists") else (None, False)

    def _maybe_push(coll, doc_id, d, before=(None, False), repo="", port=None):
        if not NOTIFY or not notify_cfg.get("url") or coll not in ("posts", "comments") or not isinstance(d, dict):
            return
        rid = "" if repo in ("", primary_repo_id) else repo
        threading.Thread(target=NOTIFY.announce, args=(root_dir, _notify_settings(), coll, doc_id, dict(d)), daemon=True,
                         kwargs={"prev": before[0], "known": before[1], "port": port, "repo": rid}).start()
    primary_repo_id = re.sub(r"[^a-z0-9_-]+", "-", project_name.lower()).strip("-") or "primary"
    stores_map = {primary_repo_id: store}
    roots_map = {primary_repo_id: root_dir}
    repo_meta = [{"id": primary_repo_id, "name": project_name, "primary": True, "github_slugs": _repo_github_slugs(root_dir)}]
    for rid, rinfo in (extra_stores or {}).items():
        if rid and rid != primary_repo_id and isinstance(rinfo, dict) and rinfo.get("store"):
            stores_map[rid] = rinfo["store"]
            r_root = rinfo.get("root")
            if r_root:
                roots_map[rid] = r_root
            repo_meta.append({
                "id": rid,
                "name": rinfo.get("name") or rid,
                "primary": False,
                "github_slugs": _repo_github_slugs(r_root) if r_root else [],
            })

    def _build_embed_unlock_html():
        return (
            "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=theme-color content=#0b111e><title>Unlock Board (Preview)</title></head>"
            "<body style='background:#0b111e;color:#e6ebf2;font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px'>"
            "<form id=f style='background:#141d2e;border:1px solid #233049;border-radius:16px;padding:24px;max-width:360px;width:100%'>"
            "<h2 style='margin:0 0 8px;font-size:1.2rem'>Unlock board preview</h2>"
            "<p style='margin:0 0 14px;color:#9aa6b8;font-size:.9rem'>Enter your board token for this preview session. Held in page memory only (no cookies).</p>"
            "<input id=t type=password placeholder='Board token' autocomplete=off required style='width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;border:1px solid #233049;background:#0b111e;color:#e6ebf2;font-size:1rem;margin-bottom:12px'>"
            "<button id=b type=submit style='width:100%;padding:10px;border-radius:999px;border:0;background:#e8894a;color:#1a1206;font-weight:700;font-size:.95rem;cursor:pointer'>Unlock</button>"
            "<p id=err role=alert style='margin:12px 0 0;color:#f87171;font-size:.88rem;min-height:1.2em'></p>"
            "</form>"
            "<script>(function(){"
            "var f=document.getElementById('f'),t=document.getElementById('t'),b=document.getElementById('b'),err=document.getElementById('err');"
            "f.addEventListener('submit',function(e){"
            "e.preventDefault();var v=(t.value||'').trim();if(!v)return;"
            "b.disabled=true;err.textContent='';"
            "fetch('./',{method:'GET',headers:{'X-Kit-Token':v,'Accept':'application/x-kit-board'},credentials:'omit'})"
            ".then(function(r){"
            "if(!r.ok||r.headers.get('X-Kit-Board-Doc')!=='1'){"
            "err.textContent=r.status===401?'Invalid board token. Check the token and try again.':'Could not load board (HTTP '+r.status+').';"
            "b.disabled=false;return;"
            "}"
            "return r.text().then(function(html){"
            "var boot='<script>window.__KIT_TOKEN__='+JSON.stringify(v)+';window.__KIT_EMBED__=true;<\\/script>';"
            "var out=html.indexOf('<head>')>=0?html.replace('<head>','<head>\\n'+boot):boot+html;"
            "document.open();document.write(out);document.close();"
            "});"
            "},function(){"
            "err.textContent='Network error while contacting the board server.';b.disabled=false;"
            "});"
            "});"
            "})();</script>"
            "</body></html>"
        ).encode("utf-8")

    def _build_embed_board_html(inject_embed_flag=False):
        with open(html_path, encoding="utf-8") as fh:
            html = fh.read()
        inline_shim = load_shim_js().replace("</script>", "<\\/script>")
        flag_js = "window.__KIT_EMBED__=true;\n" if inject_embed_flag else ""
        embed_tags = '<meta name="theme-color" content="#0b111e">\n<script>\n' + flag_js + inline_shim + "\n</script>"
        head_inject = '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">\n<style>html,body{margin:0;padding:0;-webkit-font-smoothing:antialiased;}</style>\n' + embed_tags
        if "</head>" in html:
            if 'name="viewport"' in html.lower():
                head_inject = embed_tags
            html = html.replace("</head>", head_inject + "\n</head>", 1)
        else:
            html = '<!doctype html>\n<html lang="en">\n<head>\n' + head_inject + "\n</head>\n<body>\n" + html + "\n</body>\n</html>\n"
        return html.encode("utf-8")

    HTML_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https: /_blob/ /_api/repo-asset; "
        "media-src 'self' blob: /_blob/ /_api/repo-asset /_repo/; "
        "frame-src 'self' blob: /_blob/ /_api/repo-asset /_repo/; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'"
    )

    unlock_fails = {}
    unlock_lock = threading.Lock()
    MAX_UNLOCK_FAILS = 10
    UNLOCK_WINDOW_SEC = 600.0

    def _share_allowed_assets(sh):
        tstore = stores_map.get(sh.get("repo") or "") or store
        post_doc = tstore.get_doc("posts", sh["post_id"])
        if not post_doc.get("exists"):
            return set(), set(), set()
        p = post_doc["data"] or {}
        cmts = [
            c for c in tstore.list_docs("comments")["docs"]
            if (c["data"] or {}).get("post_id") == sh["post_id"]
            and not (c["data"] or {}).get("removed")
            and not (c["data"] or {}).get("like")
        ]
        blob_ids = set()
        repo_paths = set()
        html_dirs = set()

        def _scan_text(txt):
            s = str(txt or "")
            for bm in re.finditer(r"/_blob/([0-9a-f]{32})", s):
                blob_ids.add(bm.group(1))
            for am in re.finditer(r"/_api/repo-asset\?path=([^&\s)\"']+)", s):
                rp = urllib.parse.unquote(am.group(1)).strip().replace("\\", "/").lstrip("/")
                if rp and ".." not in rp.split("/"):
                    repo_paths.add(rp)
            for fm in re.finditer(r"(?:`|\(|\[)([A-Za-z0-9_./-]+\.(?:md|html?|png|jpe?g|gif|webp|avif|svg))(?:\s+§[^`]+)?(?:`|\)|\])", s, re.I):
                rp = fm.group(1).strip().replace("\\", "/").lstrip("/")
                if rp and ".." not in rp.split("/") and not rp.startswith("http"):
                    repo_paths.add(rp)
                    if rp.lower().endswith((".html", ".htm")) and os.path.dirname(rp):
                        html_dirs.add(os.path.dirname(rp))      # a page's own pictures and styles; never the repo's root

        # What the card shows its guest is what the owner's side put there: never a file a guest names in a comment, nor
        # one a collaborator names (their own words are in the brief; the repo's files are the owner's to hand out).
        theirs = lambda d: bool(d.get("by"))  # noqa: E731
        if theirs(p) or sh.get("created_by"):
            for f in (p.get("files") or []):
                if isinstance(f, dict) and re.match(r"^[0-9a-f]{32}$", str(f.get("id") or "")):
                    blob_ids.add(str(f["id"]))
            for im in [x for x in (p.get("images") or []) if isinstance(x, str)] + [str(p.get("body") or "")]:
                blob_ids.update(re.findall(r"/_blob/([0-9a-f]{32})", im))
            return blob_ids, set(), set()
        cmts = [c for c in cmts if not theirs(c["data"] or {}) and not str(c.get("id") or "").startswith("ext-")]
        for im in (p.get("images") or []):
            if isinstance(im, str):
                _scan_text(im)
        for f in (p.get("files") or []):
            if isinstance(f, dict) and re.match(r"^[0-9a-f]{32}$", str(f.get("id") or "")):
                blob_ids.add(str(f["id"]))
        _scan_text(p.get("body"))
        for c in cmts:
            cd = c["data"] or {}
            _scan_text(cd.get("text"))
            _scan_text(cd.get("image"))
            for f in (cd.get("files") or []):
                if isinstance(f, dict) and re.match(r"^[0-9a-f]{32}$", str(f.get("id") or "")):
                    blob_ids.add(str(f["id"]))
        return blob_ids, repo_paths, html_dirs

    def _render_share_brief(sh, base_origin):
        tstore = stores_map.get(sh.get("repo") or "") or store
        post_doc = tstore.get_doc("posts", sh["post_id"])
        if not post_doc.get("exists"):
            return None
        p = post_doc["data"] or {}
        cmts = [
            c for c in tstore.list_docs("comments")["docs"]
            if (c["data"] or {}).get("post_id") == sh["post_id"]
            and not (c["data"] or {}).get("removed")
            and not (c["data"] or {}).get("like")
        ]
        cmts.sort(key=lambda c: str((c["data"] or {}).get("created_at") or ""))
        blob_ids, repo_paths, _ = _share_allowed_assets(sh)
        max_sub = int(sh.get("max_submissions") or 5)
        used_sub = int(sh.get("submissions") or 0)
        rem_sub = max(0, max_sub - used_sub)
        lines = [
            "# External Task Brief — %s" % (p.get("title") or sh["post_id"]),
            "",
            "- **Card ID:** `%s`" % sh["post_id"],
            "- **Column:** `%s`" % (p.get("column") or "info"),
            "- **Train / Part:** `%s` / `%s`" % (p.get("train") or "-", p.get("part") or "-"),
            "- **Expires:** `%s`" % sh["expires_at"],
            "- **Write scope:** %s" % ("scoped to this card only (cannot read or modify other cards or the repository)" if sh["allow_write"] else "read-only"),
            "- **Submissions remaining:** `%d/%d`" % (rem_sub, max_sub),
            "",
            "**For the AI reading this:** when you finish (or as soon as you are blocked), return your result to the board using **Option A** (direct POST) or **Option B** (Markdown block) at the end of this brief.",
            "",
            "## Task & Context",
            "",
            str(p.get("body") or "").strip(),
        ]
        if blob_ids or repo_paths:
            origin_p = base_origin.rstrip("/") if base_origin else ""
            lines.extend(["", "## Shared Assets & Attachments (Token-Scoped)"])
            for bid in sorted(blob_ids):
                lines.append("- Blob `%s`: %s/_share/%s/blob/%s" % (bid, origin_p, sh["token"], bid))
            for rp in sorted(repo_paths):
                lines.append("- Repo file `%s`: %s/_share/%s/repo/%s" % (rp, origin_p, sh["token"], urllib.parse.quote(rp, safe="/")))
        if cmts:
            lines.extend(["", "## Recent Comments (%d)" % len(cmts)])
            for c in cmts[-10:]:
                cd = c["data"] or {}
                txt = str(cd.get("text") or "").strip()
                if txt:
                    lines.append("- **%s**: %s" % (cd.get("from") or "unknown", txt.replace("\n", "\n  ")))
        if sh.get("files"):
            lines.extend(["", "## Attached Reference Files"])
            for f in sh["files"]:
                lines.extend(["", "### `%s`" % f["name"], "```", f.get("content") or "", "```"])
        lines.extend(["", "## How to Return Your Findings", ""])
        if sh["allow_write"]:
            submit_url = (base_origin.rstrip("/") if base_origin else "") + "/_share/" + sh["token"] + "/submit"
            lines.extend([
                "### Option A — Direct write-back (default when outbound HTTP is available)",
                "If the user asked you to complete this card and you have outbound HTTP, POST JSON directly to `%s` as your final step (use Option B only if HTTP POST is blocked):" % submit_url,
                "```json",
                json.dumps({
                    "from": "lmarena",
                    "mode": "comment",
                    "text": "<your concise Markdown findings here>",
                }, indent=2),
                "```",
                "*(Set `\"mode\": \"child_post\"` and `\"title\": \"...\"` to create a linked result card instead of a comment; pass an optional `\"idempotency_key\": \"<slug>\"` to update the same child card across re-runs without duplicates.)*",
                "",
            ])
        lines.extend([
            "### Option B — Copy-paste Markdown card (for 1-click import)",
            "Output a single Markdown block starting with this YAML front-matter:",
            "```markdown",
            "---",
            "id: %s-research" % sh["post_id"],
            "title: %s — Research" % (p.get("title") or sh["post_id"]),
            "column: info",
            "parent: %s" % sh["post_id"],
            "train: %s" % (p.get("train") or ""),
            "runner: lmarena",
            "---",
            "",
            "<your findings here>",
            "```",
            "",
        ])
        return "\n".join(lines)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _full(self):
            u = getattr(self, "_auth_user", None) or {"role": "owner"}
            return u.get("role") == "owner"

        def _boards(self):
            """{board: its store} as this person may open them: the stores themselves for the owner, a View of each for
            anyone else (access.py). The first one is theirs by default."""
            if self._full():
                return stores_map
            u = getattr(self, "_auth_user", None) or {}
            if not ACCESS:
                return {}
            return {rid: ACCESS.View(stores_map[rid], u, gate=GATE, now=utc_now) for rid in ACCESS.boards(u, stores_map, primary_repo_id)}

        def _scoped_user_repo(self):
            """The one board this person is held to, or ""."""
            if self._full():
                return ""
            mine = list(self._boards())
            return mine[0] if len(mine) == 1 else ""

        def _is_all_repos(self, parsed):
            qs = urllib.parse.parse_qs(parsed.query)
            rid = (self.headers.get("X-Kit-Repo") or (qs.get("repo") or [""])[0] or "").strip().lower()
            return rid == "all" and len(self._boards()) > 1

        def _store_for_doc(self, parsed, coll, doc_id):
            mine = self._boards()
            if _is_seen_doc(coll, doc_id) and self._full():
                return store, primary_repo_id   # CL21: one read doc for every board (keys say the board)
            if self._is_all_repos(parsed):
                for rid, st in mine.items():
                    try:
                        if st.get_doc(coll, doc_id).get("exists"):
                            return st, rid
                    except Exception as e:
                        if not (ACCESS and isinstance(e, ACCESS.Denied)):
                            raise
                rid = next(iter(mine))
                return mine[rid], rid
            rid = self._repo_id(parsed)
            return mine[rid], rid

        def _repo_id(self, parsed):
            mine = self._boards()
            qs = urllib.parse.parse_qs(parsed.query)
            rid = (self.headers.get("X-Kit-Repo") or (qs.get("repo") or [""])[0] or "").strip()
            return rid if rid in mine else next(iter(mine))

        def _store(self, parsed):
            return self._boards()[self._repo_id(parsed)]

        def _main_store(self, parsed):
            """The server's own board for the owner; for anyone else, the board they opened, as they may see it."""
            return store if self._full() else self._store(parsed)

        def _repo_root(self, parsed):
            return roots_map.get(self._repo_id(parsed)) or root_dir

        def _send_security_headers(self, is_html=False):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if is_html:
                self.send_header("Content-Security-Policy", HTML_CSP)

        def _send_range_bytes(self, data, ctype, cache_hdr, disp=None):
            total = len(data)
            rng = (self.headers.get("Range") or "").strip()
            if rng.startswith("bytes=") and total > 0:
                spec = rng[6:].split(",")[0].strip()
                if "-" in spec:
                    s_a, s_b = spec.split("-", 1)
                    try:
                        if s_a == "":
                            n_tail = int(s_b)
                            start = max(0, total - n_tail)
                            end = total - 1
                        else:
                            start = int(s_a)
                            end = int(s_b) if s_b else (total - 1)
                        if 0 <= start <= end < total:
                            chunk = data[start:end + 1]
                            self.send_response(206)
                            self.send_header("Content-Type", ctype)
                            self.send_header("X-Content-Type-Options", "nosniff")
                            self.send_header("Accept-Ranges", "bytes")
                            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
                            self.send_header("Content-Length", str(len(chunk)))
                            if disp:
                                self.send_header("Content-Disposition", disp)
                            self.send_header("Cache-Control", cache_hdr)
                            self.end_headers()
                            self.wfile.write(chunk)
                            return
                    except ValueError:
                        pass
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Accept-Ranges", "bytes")
            if disp:
                self.send_header("Content-Disposition", disp)
            self.send_header("Cache-Control", cache_hdr)
            self.send_header("Content-Length", str(total))
            self.end_headers()
            self.wfile.write(data)

        def _send_html_bytes(self, code, raw, extra_headers=None, head_only=False):
            ae = (self.headers.get("Accept-Encoding") or "").lower()
            use_gzip = "gzip" in ae and len(raw) >= 1024
            payload = gzip.compress(raw, compresslevel=6) if use_gzip else raw
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_security_headers(is_html=True)
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            if extra_headers:
                for k, v in extra_headers:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def do_HEAD(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/_api/health":
                raw = json.dumps({"ok": True, "pid": os.getpid(), "auth": bool(token), "embed_mode": bool(embed_mode)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path in ("/", "/index.html"):
                if not embed_mode:
                    ok, _ = self._check_auth(parsed)
                    if not ok:
                        self.send_response(401)
                        self._send_security_headers()
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                if not os.path.isfile(html_path):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self._send_security_headers(is_html=True)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path.startswith("/_share/") and not path.endswith("/submit"):
                rest_sh = path[len("/_share/"):].strip("/")
                stok = urllib.parse.unquote(rest_sh.split("/", 1)[0])
                sh = store.get_share(stok)
                if not sh:
                    self.send_response(404)
                    self._send_security_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if sh["revoked"] or sh["expires_at"] < utc_now():
                    self.send_response(410)
                    self._send_security_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
                host_hdr = self.headers.get("Host") or ""
                origin = ("%s://%s" % (proto, host_hdr)) if host_hdr else ""
                brief = _render_share_brief(sh, origin)
                if brief is None:
                    self.send_response(404)
                    self._send_security_headers()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                raw_sh = brief.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self._send_security_headers()
                self.send_header("Content-Length", str(len(raw_sh)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path in ("/_shim.js", "/_manifest.webmanifest", "/_icon.svg", "/_sw.js", "/_vendor/pdf.min.js", "/_vendor/pdf.worker.min.js"):
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(404)
            self._send_security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            pass  # keep daemon stdout/stderr quiet

        def _cur_uid(self):
            u = getattr(self, "_auth_user", None) or {}
            return str(u.get("id") or "owner")

        def _scope_list_docs(self, coll, res):
            """The owner's lists leave out the collaborators' private channels (a View has its own rule)."""
            if not self._full():
                return res
            if coll == "posts":
                res["docs"] = [d for d in (res.get("docs") or []) if not str(d.get("id") or "").startswith("_orchestrator_")]
            elif coll == "comments":
                res["docs"] = [d for d in (res.get("docs") or []) if not str((d.get("data") or {}).get("post_id") or "").startswith("_orchestrator_")]
            return res

        def _signed(self, data, new):
            """A co-owner's own write says who made it; the owner's token (the owner and every agent of theirs) says nothing."""
            uid = self._cur_uid()
            if self._full() and uid != "owner" and new and isinstance(data, dict):
                return dict(data, by=uid)
            return data

        def _unproved(self, st, writes):
            """True when one of these writes [(op, collection, id, body)] is refused (403 sent): a step completed, or a proof
            written, past `kit.py done` (post_gate.check_proof). A member's writes are gated by their View (access.py)."""
            how = self.headers.get("X-Kit-Gate") or ""
            if not self._full() or not hasattr(GATE, "check_proof") or how == "copy":
                return False
            seen = {}
            for op, coll, did, b in writes:
                if coll != "posts" or not did or not isinstance(b, dict) or str(did).startswith("_"):
                    continue
                old = seen[did] if did in seen else (st.get_doc(coll, did).get("data") or {})
                new = json.loads(json.dumps(old))
                if op == "set":
                    new = b.get("data")
                elif op == "update" and isinstance(b.get("data"), dict):
                    _deep_merge_doc(new, json.loads(json.dumps(b["data"])), doc_id=did)
                elif op == "str_replace" and isinstance(new.get(b.get("field") or "body"), str):
                    f = b.get("field") or "body"
                    new[f] = new[f].replace(str(b.get("old_str") or ""), str(b.get("new_str") or ""), 1)
                why = GATE.check_proof(did, new, old, may_prove=how == "done")
                if why:
                    self._send_json(403, {"error": "forbidden", "why": why})
                    return True
                seen[did] = new if isinstance(new, dict) else {}
            return False

        def _match_token(self, candidate):
            c = str(candidate or "").strip()
            if not c:
                return None
            if token and hmac.compare_digest(c, token):
                return {"id": "owner", "name": "owner", "role": "owner", "token": token}
            return store.lookup_user_by_token(c)

        def _check_auth(self, parsed):
            self._auth_user = {"id": "owner", "name": "owner", "role": "owner", "token": token or ""}
            if not token:
                return True, False
            if not embed_mode:
                qs = urllib.parse.parse_qs(parsed.query)
                qtok = (qs.get("token") or [""])[0]
                u = self._match_token(qtok)
                if u:
                    self._auth_user = u
                    return True, True
            hdr = self.headers.get("X-Kit-Token") or ""
            u = self._match_token(hdr)
            if u:
                self._auth_user = u
                return True, False
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                u = self._match_token(auth[7:].strip())
                if u:
                    self._auth_user = u
                    return True, False
            if not embed_mode:
                raw_cookie = self.headers.get("Cookie") or ""
                if raw_cookie:
                    c = SimpleCookie()
                    try:
                        c.load(raw_cookie)
                        if "kit_token" in c:
                            u = self._match_token(c["kit_token"].value)
                            if u:
                                self._auth_user = u
                                return True, False
                    except Exception:
                        pass
            if tailscale_users:
                peer_ip = self.client_address[0] if self.client_address else ""
                ts_login = (self.headers.get("Tailscale-User-Login") or "").strip().lower()
                if peer_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and not self.headers.get("Tailscale-Funnel-Request") and ts_login in tailscale_users:
                    cfg_u = tailscale_users[ts_login]
                    role_ts = cfg_u["role"] if cfg_u["role"] in ("viewer", "member", "owner") else "member"
                    uid_ts = cfg_u["id"] or re.sub(r"[^a-z0-9_-]+", "-", ts_login.split("@")[0]).strip("-")[:24] or "user"
                    nm_ts = (self.headers.get("Tailscale-User-Name") or "").strip() or uid_ts
                    self._auth_user = {"id": uid_ts, "name": nm_ts, "role": role_ts, "repo": cfg_u.get("repo") or "", "token": "", "via": "tailscale"}
                    return True, False
            self._auth_user = None
            return False, False

        def _require_auth(self, parsed, min_role="viewer"):
            ok, set_cookie = self._check_auth(parsed)
            if ok:
                ranks = {"viewer": 1, "member": 2, "owner": 3}
                u = getattr(self, "_auth_user", None) or {"id": "owner", "role": "owner"}
                label = re.sub(r"[^A-Za-z0-9._:-]", "", self.headers.get("X-Kit-Actor") or "")[:48] if self._full() else ""
                ACTOR.name = str(u.get("id") or "owner") + ((" (" + label + ")") if label else "")
                if not self._full() and not (ACCESS and ACCESS.may_call(u, self.command, parsed.path)):
                    need = (ACCESS.route_role(self.command, parsed.path) if ACCESS else None) or "owner"
                    self._send_json(403, {"error": "forbidden", "required_role": need})
                    return None
                if not self._full() and not self._boards():
                    self._send_json(403, {"error": "forbidden", "why": "the board you were invited to is not open here"})
                    return None
                if ranks.get(u.get("role", "viewer"), 1) < ranks.get(min_role, 1):
                    self._send_json(403, {"error": "forbidden", "required_role": min_role})
                    return None
                # CL32: a read or write for a board this server does not hold is refused; it used to fall back to the
                # primary board, so `--repo board` in Harmonize's cloud clone read Harmonize's own posts
                rq = (self.headers.get("X-Kit-Repo") or (urllib.parse.parse_qs(parsed.query).get("repo") or [""])[0] or "").strip()
                if rq and parsed.path.startswith("/_api/") and rq not in self._boards() and rq.lower() != "all":
                    self._send_json(404, {"error": "unknown_repo", "repo": rq, "boards": sorted(self._boards()),
                                          "hint": "this board server holds no board %r: put its clone next to this repo "
                                                  "(or run `kit.py artifact load` after a pull of repos/%s), then restart it" % (rq, rq)})
                    return None
                return set_cookie
            accept = (self.headers.get("Accept") or "").lower()
            if (not embed_mode) and parsed.path in ("/", "/index.html") and "text/html" in accept:
                # Auto-restore from localStorage via POST /_api/unlock (never puts secret in URL)
                body = (
                    '<!doctype html><html lang="en"><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
                    "<meta name=theme-color content=#0b111e><title>Unlock Board</title></head>"
                    "<body style='background:#0b111e;color:#e6ebf2;font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px'>"
                    "<form id=uf style='background:#141d2e;border:1px solid #233049;border-radius:16px;padding:24px;max-width:360px;width:100%'>"
                    "<h2 style='margin:0 0 8px;font-size:1.2rem'>Unlock board</h2>"
                    "<p style='margin:0 0 14px;color:#9aa6b8;font-size:.9rem'>Paste your board token once — it will be saved permanently on this device.</p>"
                    "<input id=t type=password placeholder='Board token' required style='width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;border:1px solid #233049;background:#0b111e;color:#e6ebf2;font-size:1rem;margin-bottom:12px'>"
                    "<button type=submit style='width:100%;padding:10px;border-radius:999px;border:0;background:#e8894a;color:#1a1206;font-weight:700;font-size:.95rem;cursor:pointer'>Unlock</button>"
                    "<div id=err style='color:#f08a78;font-size:.85rem;margin-top:10px'></div></form>"
                    "<script>(function(){function go(v){return fetch('/_api/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:v})}).then(function(r){if(!r.ok)throw 0;try{localStorage.setItem('kit_board_token',v);}catch(e){}location.replace('/');});}"
                    "try{var s=localStorage.getItem('kit_board_token');if(s){go(s).catch(function(){});}}catch(e){}"
                    "document.getElementById('uf').addEventListener('submit',function(e){e.preventDefault();var v=document.getElementById('t').value.trim();if(!v)return;document.getElementById('err').textContent='';go(v).catch(function(){document.getElementById('err').textContent='Invalid board token.';});});})();</script>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return None
            self._send_json(401, {"error": "unauthorized", "code": "unauthorized"})
            return None

        def _send_json(self, code, obj):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("invalid content-length")
            if length < 0:
                raise ValueError("invalid content-length")
            if length > MAX_JSON_BYTES:
                raise PayloadTooLarge("json body exceeds 5 MB")
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/_api/health":
                self._send_json(200, {"ok": True, "pid": os.getpid(), "auth": bool(token), "embed_mode": bool(embed_mode)})
                return

            if path.startswith("/_invite/") and path.endswith(".md"):
                itok = urllib.parse.unquote(path[len("/_invite/"):-len(".md")]).strip("/")
                iu = store.lookup_user_by_token(itok)
                if not iu:
                    self._send_json(410, {"error": "invite_invalid_or_revoked"})
                    return
                proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
                host_hdr = self.headers.get("Host") or ""
                origin = ("%s://%s" % (proto, host_hdr)) if host_hdr else ""
                uid = iu["id"]
                role = iu["role"]
                req_repo = str(iu.get("repo") or (qs.get("repo") or [""])[0] or "").strip()
                target_repo = req_repo if (req_repo and req_repo in stores_map) else ""
                target_name = project_name
                if target_repo:
                    for rm in repo_meta:
                        if rm.get("id") == target_repo:
                            target_name = rm.get("name") or target_repo
                            break
                is_non_primary = bool(target_repo and target_repo != primary_repo_id)
                orch_id = "_orchestrator_" + uid
                env_lines = [
                    'export KIT_BOARD_URL="%s"' % origin,
                    'export KIT_BOARD_TOKEN="%s"' % itok,
                ]
                if is_non_primary:
                    env_lines.append('export KIT_BOARD_REPO="%s"' % target_repo)
                browser_link = "%s/?token=%s%s" % (origin, itok, ("&repo=" + urllib.parse.quote(target_repo)) if is_non_primary else "")
                brief = "\n".join([
                    "# Board Collaborator AI Bootstrap — %s (@%s)" % (target_name, uid),
                    "",
                    "You are connecting as collaborator **`@%s`** (role: **`%s`**) on **%s**'s live board." % (uid, role, target_name),
                    "No GitHub repository access is required — everything runs over HTTPS using your scoped token.",
                    "",
                    "## 0. In Claude Code Web (a claude.ai cloud session): the token as an environment secret first",
                    "Do not paste the token into commands or files. Before any call to the board:",
                    "1. Call `request_credential` with `host: \"%s\"`, `header_name: \"X-Kit-Token\"`, `display_name: \"%s board (@%s)\"`, and a reason naming this board. The owner pastes the token below into the secure form once; the network proxy then adds the header to every request to this host, from this session and the environment's later ones (it also opens the host)." % (urllib.parse.urlparse(origin).hostname or origin, target_name, uid),
                    "2. Then use the steps below with `export KIT_BOARD_TOKEN=\"proxy\"` (a placeholder the proxy replaces), never the real value.",
                    "3. So the session can act without asking each time, the owner adds a permission rule to the project's `.claude/settings.json` (`permissions.allow`): `Bash(curl -sS * %s/_api/*)` and `Bash(python3 /tmp/board_cli.py *)`, and writes in its memory that posting to this board is approved." % origin,
                    "4. On 401/403 (token revoked or expired): stop and say so on your own board; never look for another token.",
                    "",
                    "## 1. Environment",
                    "```bash",
                ] + env_lines + [
                    "```",
                    "",
                    "## 2. Fetch the Zero-Dependency CLI (1 command)",
                    "```bash",
                    'curl -sf -H "X-Kit-Token: $KIT_BOARD_TOKEN" "$KIT_BOARD_URL/_api/client/board_cli.py" -o /tmp/board_cli.py',
                    "```",
                    "",
                    "## 3. Your Private Orchestrator Channel & Shared Board Rules",
                    "- **Private Ask-the-Orchestrator (`@o`):** `%s` (isolated from the owner's `_orchestrator`)." % orch_id,
                    "- **Shared project posts:** read/list all posts with `python3 /tmp/board_cli.py list posts`. You write your own posts and comments, and the state of a post that is `for` you; everything else is refused (403 says why).",
                    "- **Attribution & AI metadata:** the board signs what you write (`from` and `by`: `%s`; an agent may add its name: `\"from\": \"%s/codex\"`). Take a post with `\"working_by\": \"%s\"`, and set `\"agent_meta\": {\"harness\": \"<cli>\", \"model\": \"<model>\", \"effort\": \"<low|medium|high>\"}` on posts/comments." % (uid, uid, uid),
                    "- **Submit scripts, improved docs & HTML (no GitHub write access needed):** `POST $KIT_BOARD_URL/_api/posts/<post_id>/artifacts` with JSON `{\"path\": \"docs/foo.md\", \"base_path\": \"docs/foo.md\", \"summary\": \"1-line summary\", \"content\": \"...\", \"agent_meta\": {\"harness\": \"...\", \"model\": \"...\", \"effort\": \"...\"}}`. Your deliverables are stored safely in the board, diffed against `base_path`, previewable in the Document/HTML Viewer, and queryable in compact manifests (`GET /_api/posts/<post_id>/artifacts`).",
                    "- **Permissions (`%s`):** %s" % (role, "read-only" if role == "viewer" else ("read, post and comment as yourself, change your own posts and the state of posts for you" if role == "member" else "full admin")),
                    "",
                    "## 4. Browser / Phone Link",
                    "Open `%s` on desktop or phone to view the board as `@%s`." % (browser_link, uid),
                ])
                raw = brief.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self._send_security_headers()
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path.startswith("/_share/") and not path.endswith("/submit"):
                rest_sh = path[len("/_share/"):].strip("/")
                sh_parts = rest_sh.split("/", 2)
                stok = urllib.parse.unquote(sh_parts[0])
                sh = store.get_share(stok)
                if not sh:
                    self._send_json(404, {"error": "share_not_found"})
                    return
                if sh["revoked"] or sh["expires_at"] < utc_now():
                    self._send_json(410, {"error": "share_expired_or_revoked"})
                    return
                if len(sh_parts) == 3 and sh_parts[1] == "blob":
                    bid = urllib.parse.unquote(sh_parts[2]).strip("/")
                    allowed_blobs, _, _ = _share_allowed_assets(sh)
                    if bid not in allowed_blobs:
                        self._send_json(403, {"error": "asset_not_in_share"})
                        return
                    tstore = stores_map.get(sh.get("repo") or "") or store
                    data, meta = tstore.read_blob(bid)
                    if data is None:
                        self._send_json(404, {"error": "blob_not_found"})
                        return
                    raw_ctype = (meta.get("contentType") or mimetypes.guess_type(meta.get("name", ""))[0] or "application/octet-stream").split(";")[0].strip().lower()
                    self.send_response(200)
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Type", raw_ctype if raw_ctype in SAFE_INLINE_BLOB_TYPES else "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if len(sh_parts) == 3 and sh_parts[1] == "repo":
                    rrel = urllib.parse.unquote(sh_parts[2]).strip().replace("\\", "/").lstrip("/")
                    _, allowed_repo, html_dirs = _share_allowed_assets(sh)
                    rdir = os.path.dirname(rrel)
                    if rrel not in allowed_repo and not (rdir in html_dirs and os.path.splitext(rrel)[1].lower() in PAGE_PARTS):
                        self._send_json(403, {"error": "asset_not_in_share"})
                        return
                    if not (ACCESS and ACCESS.file_ok(rrel)):
                        self._send_json(403, {"error": "asset_not_in_share"})
                        return
                    rroot = roots_map.get(sh.get("repo") or "") or root_dir
                    adata, actype, aerr = read_repo_asset(rroot, rrel)
                    if aerr:
                        self._send_json(404 if aerr == "not_found" else 400, {"error": aerr})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", actype)
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", str(len(adata)))
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.end_headers()
                    self.wfile.write(adata)
                    return
                proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
                host_hdr = self.headers.get("Host") or ""
                origin = ("%s://%s" % (proto, host_hdr)) if host_hdr else ""
                brief = _render_share_brief(sh, origin)
                if brief is None:
                    self._send_json(404, {"error": "post_not_found"})
                    return
                raw = brief.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self._send_security_headers()
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if embed_mode and path in ("/", "/index.html"):
                accept = (self.headers.get("Accept") or "").lower()
                if "application/x-kit-board" in accept or not token:
                    if token:
                        ok, _ = self._check_auth(parsed)
                        if not ok:
                            self._send_json(401, {"error": "unauthorized", "code": "unauthorized"})
                            return
                    if not os.path.isfile(html_path):
                        self._send_json(404, {"error": "board.html not found"})
                        return
                    raw = _build_embed_board_html(inject_embed_flag=not bool(token))
                    self._send_html_bytes(200, raw, extra_headers=[("X-Kit-Board-Doc", "1")])
                    return
                body = _build_embed_unlock_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/_manifest.webmanifest":
                manifest = {
                    "name": "%s Board" % project_name if project_name != "Board" else "Board",
                    "short_name": project_name[:16],
                    "start_url": "/",
                    "scope": "/",
                    "display": "standalone",
                    "background_color": "#0b111e",
                    "theme_color": "#0b111e",
                    "icons": [{"src": "/_icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
                }
                raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_icon.svg":
                raw = PWA_ICON_SVG.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_sw.js":
                raw = PWA_SW_JS.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Service-Worker-Allowed", "/")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path in ("/_vendor/pdf.min.js", "/_vendor/pdf.worker.min.js"):
                vname = path.rsplit("/", 1)[-1]
                vpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", vname)
                if os.path.isfile(vpath):
                    with open(vpath, "rb") as vf:
                        raw = vf.read()
                    self.send_response(200)
                    self._send_security_headers()
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self._send_json(404, {"error": "vendor asset not found"})
                return

            set_cookie = self._require_auth(parsed)
            if set_cookie is None:
                return
            if set_cookie and path in ("/", "/index.html"):
                proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
                own_tok = str((getattr(self, "_auth_user", None) or {}).get("token") or "")     # the link's own rights, never the owner's
                cookie = "kit_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % own_tok
                if proto == "https":
                    cookie += "; Secure"
                accept = (self.headers.get("Accept") or "").lower()
                if "text/html" in accept:
                    # Browser / PWA: persist token in localStorage AND 1-year cookie, then strip from URL
                    body = (
                        "<!doctype html><html><head><meta charset=utf-8><meta name=theme-color content=#0b111e></head>"
                        "<body style='background:#0b111e;color:#e6ebf2;font-family:system-ui;padding:24px'>"
                        "<script>try{localStorage.setItem('kit_board_token',%s);}catch(e){}location.replace('/');</script>"
                        "</body></html>" % json.dumps(own_tok)
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Set-Cookie", cookie)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(302)
                self.send_header("Set-Cookie", cookie)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if path in ("/", "/index.html"):
                if not os.path.isfile(html_path):
                    self._send_json(404, {"error": "board.html not found"})
                    return
                with open(html_path, encoding="utf-8") as fh:
                    html = fh.read()
                pwa_tags = (
                    '<link rel="manifest" href="/_manifest.webmanifest">\n'
                    '<link rel="icon" type="image/svg+xml" href="/_icon.svg">\n'
                    '<link rel="apple-touch-icon" href="/_icon.svg">\n'
                    '<meta name="theme-color" content="#0b111e">\n'
                    '<meta name="apple-mobile-web-app-capable" content="yes">\n'
                    '<meta name="mobile-web-app-capable" content="yes">\n'
                    '<script src="/_shim.js"></script>\n'
                    '<script>if("serviceWorker"in navigator){navigator.serviceWorker.register("/_sw.js").catch(function(){});}</script>'
                )
                head_inject = '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">\n<style>html,body{margin:0;padding:0;-webkit-font-smoothing:antialiased;}</style>\n' + pwa_tags
                if "</head>" in html:
                    if 'name="viewport"' in html.lower():
                        head_inject = pwa_tags
                    html = html.replace("</head>", head_inject + "\n</head>", 1)
                else:
                    html = '<!doctype html>\n<html lang="en">\n<head>\n' + head_inject + "\n</head>\n<body>\n" + html + "\n</body>\n</html>\n"
                raw = html.encode("utf-8")
                self._send_html_bytes(200, raw)
                return

            if path == "/_shim.js":
                raw = load_shim_js().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_api/me":
                u = getattr(self, "_auth_user", None) or {"id": "owner", "name": "owner", "role": "owner"}
                collabs = store.list_users()
                self._send_json(200, {
                    "id": u["id"],
                    "name": u["name"],
                    "role": u["role"],
                    "has_collaborators": len(collabs) > 0,
                    "collaborators": [{"id": x["user_id"], "name": x["name"], "role": x["role"]} for x in collabs] if collabs else [],
                })
                return

            if path == "/_api/client/board_cli.py":
                cli_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_cli.py")
                if not os.path.isfile(cli_path):
                    self._send_json(404, {"error": "board_cli_not_found"})
                    return
                with open(cli_path, "rb") as fh:
                    raw_cli = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/x-python; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(raw_cli)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw_cli)
                return

            if path == "/_api/users":
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if u.get("role") != "owner":
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                self._send_json(200, {"users": store.list_users()})
                return

            if path == "/_api/notify":
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if u.get("role") != "owner":
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                self._send_json(200, {"notify_url": notify_cfg.get("url") or "", "notify_detail": bool(notify_cfg.get("detail"))})
                return

            if path.startswith("/_api/posts/") and path.endswith("/artifacts"):
                pid_a = urllib.parse.unquote(path[len("/_api/posts/"):-len("/artifacts")]).strip("/")
                tstore, _ = self._store_for_doc(parsed, "posts", pid_a)
                self._send_json(200, {"post_id": pid_a, "artifacts": tstore.list_artifacts(post_id=pid_a)})
                return

            if path.startswith("/_api/artifacts/"):
                aid_req = urllib.parse.unquote(path[len("/_api/artifacts/"):]).strip("/")
                art = self._store(parsed).get_artifact(aid_req)
                if not art:
                    for st_a in self._boards().values():
                        art = st_a.get_artifact(aid_req)
                        if art:
                            break
                if not art:
                    self._send_json(404, {"error": "artifact_not_found"})
                    return
                if (qs.get("raw") or ["0"])[0] == "1":
                    ext = os.path.splitext(art["path"])[1].lower()
                    ct = REPO_ASSET_CTYPES.get(ext, "text/plain; charset=utf-8")
                    raw_a = art["content"].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", "sandbox allow-scripts")      # someone else's page: never this board's origin
                    self.send_header("Content-Length", str(len(raw_a)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw_a)
                    return
                diff_txt = ""
                bp = art.get("base_path") or ""
                if bp:
                    rroot = self._repo_root(parsed)
                    orig_b, _, oerr = read_repo_asset(rroot, bp)
                    if not oerr and orig_b is not None:
                        orig_lines = orig_b.decode("utf-8", errors="replace").splitlines(True)
                        new_lines = art["content"].splitlines(True)
                        diff_txt = "".join(difflib.unified_diff(orig_lines, new_lines, fromfile="a/" + bp, tofile="b/" + art["path"], n=3))[:65536]
                self._send_json(200, {**art, "diff": diff_txt})
                return

            if path == "/_api/repos":
                scoped = self._scoped_user_repo()
                if scoped:
                    only = [dict({k: v for k, v in rm.items() if k != "github_slugs"}, primary=True) for rm in repo_meta if rm.get("id") == scoped]
                    self._send_json(200, {"primary": scoped, "repos": only or [{"id": scoped, "name": scoped, "primary": True}]})
                    return
                self._send_json(200, {"primary": primary_repo_id, "repos": repo_meta})
                return

            if path == "/_api/repo-docs":
                qstr = (qs.get("q") or [""])[0]
                self._send_json(200, list_repo_docs(self._repo_root(parsed), query=qstr))
                return

            if path == "/_api/repo-doc":
                rpath = (qs.get("path") or [""])[0]
                sec = (qs.get("section") or [""])[0]
                if not self._full() and not ACCESS.file_ok(rpath):
                    self._send_json(403, {"error": "forbidden"})
                    return
                rid = (qs.get("repo") or [primary_repo_id])[0]
                if rid == "all" or rid not in roots_map or rid not in self._boards():
                    rid = self._repo_id(parsed)
                doc_res, derr = read_repo_doc(self._repo_root(parsed), rpath, section=sec, repo_id=rid)
                if derr:
                    code = 404 if derr == "not_found" else (413 if derr == "too_large" else 400)
                    self._send_json(code, {"error": derr})
                    return
                self._send_json(200, doc_res)
                return

            if path.startswith("/_repo/"):
                rest_repo = path[len("/_repo/"):].lstrip("/")
                parts_r = rest_repo.split("/", 1)
                if len(parts_r) != 2:
                    self._send_json(400, {"error": "invalid_path"})
                    return
                rid_req = urllib.parse.unquote(parts_r[0])
                rrel = urllib.parse.unquote(parts_r[1])
                if not self._full() and not ACCESS.file_ok(rrel):
                    self._send_json(403, {"error": "forbidden"})
                    return
                if rid_req not in self._boards():
                    if not self._full():
                        self._send_json(403, {"error": "forbidden"})
                        return
                    rid_req = primary_repo_id
                rroot = roots_map.get(rid_req) or root_dir
                adata, actype, aerr = read_repo_asset(rroot, rrel)
                if aerr:
                    code = 404 if aerr == "not_found" else (413 if aerr == "too_large" else 400)
                    self._send_json(code, {"error": aerr})
                    return
                self.send_response(200)
                self.send_header("Content-Type", actype)
                self.send_header("X-Content-Type-Options", "nosniff")
                if token and set_cookie:
                    self.send_header("Set-Cookie", "kit_token=%s; Path=/; HttpOnly; SameSite=Lax" % str(self._auth_user.get("token") or ""))
                self.send_header("Content-Length", str(len(adata)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(adata)
                return

            if path == "/_api/repo-asset":
                rpath = (qs.get("path") or [""])[0]
                if not self._full() and not ACCESS.file_ok(rpath):
                    self._send_json(403, {"error": "forbidden"})
                    return
                adata, actype, aerr = read_repo_asset(self._repo_root(parsed), rpath)
                if aerr == "not_found" and len(roots_map) > 1 and not self._scoped_user_repo():
                    for r_root in [roots_map[r] for r in self._boards() if r in roots_map]:
                        adata2, actype2, aerr2 = read_repo_asset(r_root, rpath)
                        if not aerr2:
                            adata, actype, aerr = adata2, actype2, aerr2
                            break
                if aerr:
                    code = 404 if aerr == "not_found" else (413 if aerr == "too_large" else 400)
                    self._send_json(code, {"error": aerr})
                    return
                self._send_range_bytes(adata, actype, "public, max-age=3600")
                return

            if path == "/_api/changes":
                since = int((qs.get("since") or ["0"])[0])
                limit = int((qs.get("limit") or ["1000"])[0])
                self._send_json(200, self._store(parsed).list_changes(since=since, limit=limit))
                return

            if path == "/_api/git/diff":
                ref = (qs.get("ref") or [""])[0].strip()
                res, err = read_git_diff(root_dir if self._full() else self._repo_root(parsed), ref)
                if err:
                    self._send_json(400 if err == "invalid_ref" else 404, {"error": err})
                    return
                self._send_json(200, res)
                return

            if path == "/_api/db/comments/counts":
                self._send_json(200, self._main_store(parsed).comment_counts())
                return

            if path == "/_api/links":
                tr = (qs.get("train") or [None])[0]
                self._send_json(200, self._main_store(parsed).list_links(train=tr))
                return

            if path == "/_api/shares":
                pid = (qs.get("post") or [None])[0]
                rows_sh = store.list_shares(post_id=pid)
                self._send_json(200, {"shares": rows_sh if self._full() else [r for r in rows_sh if r["created_by"] == self._cur_uid()]})
                return

            if path == "/_api/export":
                pid = (qs.get("post") or [""])[0].strip()
                fmt = (qs.get("format") or ["md"])[0].strip().lower()
                raw_view = (qs.get("raw") or ["0"])[0].strip().lower() in ("1", "true", "yes")
                if fmt == "json":
                    data_obj, fname = export_json_bundle(self._main_store(parsed), post_id=pid or None)
                    if data_obj is None:
                        self._send_json(404, {"error": "not_found"})
                        return
                    body = json.dumps(data_obj, ensure_ascii=False, indent=2).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif fmt == "rss":
                    rss_text, fname = export_rss(self._main_store(parsed), project_name=project_name)
                    body = rss_text.encode("utf-8")
                    ctype = "application/rss+xml; charset=utf-8"
                else:
                    md_text, fname = export_markdown(self._main_store(parsed), post_id=pid or None)
                    if md_text is None:
                        self._send_json(404, {"error": "not_found"})
                        return
                    body = md_text.encode("utf-8")
                    ctype = "text/markdown; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self._send_security_headers()
                self.send_header("Content-Length", str(len(body)))
                disp = "inline" if (raw_view or fmt == "rss") else ("attachment; filename=\"%s\"" % fname)
                self.send_header("Content-Disposition", disp)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/_api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = queue.Queue(maxsize=100)
                view = None if self._full() else self._store(parsed)
                feed = view.store if view else store
                with feed.sse_lock:
                    feed.sse_clients.add(q)
                try:
                    self.wfile.write(("event: boot\ndata: " + json.dumps({"boot_id": SERVER_BOOT_ID}) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=20)
                            if view:
                                ch = json.loads(msg)
                                if "collection" in ch and not view.sees_event(str(ch["collection"]), str(ch["doc_id"])):
                                    continue
                            self.wfile.write(("event: change\ndata: " + msg + "\n\n").encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with feed.sse_lock:
                        feed.sse_clients.discard(q)
                return

            if path.startswith("/_blob/"):
                blob_id = path[len("/_blob/"):].strip("/")
                data, meta = store.read_blob(blob_id)
                if data is None:
                    self._send_json(404, {"error": "blob not found"})
                    return
                raw_ctype = (meta.get("contentType") or mimetypes.guess_type(meta.get("name", ""))[0] or "application/octet-stream").split(";")[0].strip().lower()
                if raw_ctype in SAFE_INLINE_BLOB_TYPES:
                    self._send_range_bytes(data, raw_ctype, "public, max-age=31536000")
                else:
                    fname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(meta.get("name") or blob_id)) or blob_id
                    self._send_range_bytes(data, "application/octet-stream", "private, no-store", disp='attachment; filename="%s"' % fname)
                return

            if path == "/_api/comments/status":
                with store.comment_cond:
                    w = sorted(store.watchers)
                self._send_json(200, {
                    "watching": bool(w),
                    "status": "available" if w else "no_session",
                    "message": "agent session watching" if w else "no agent session is watching the board",
                    "watchers": w,
                })
                return

            if path == "/_api/comments/wait":
                timeout = float((qs.get("timeout") or ["300"])[0])
                lease = float(qs["lease"][0]) if "lease" in qs and qs["lease"][0] else None
                res = store.wait_comment(timeout=timeout, lease_sec=lease)
                self._send_json(200, res)
                return

            if path.startswith("/_api/comments/threads/"):
                tid = urllib.parse.unquote(path[len("/_api/comments/threads/"):].strip("/"))
                t = self._main_store(parsed).get_thread(tid)
                if not t:
                    self._send_json(404, {"error": "thread not found"})
                    return
                self._send_json(200, t)
                return

            if path.startswith("/_api/doc/"):
                doc_path = urllib.parse.unquote(path[len("/_api/doc/"):])
                try:
                    coll, doc_id = split_path(doc_path)
                except ValueError as e:
                    self._send_json(400, {"error": str(e)})
                    return
                if _is_seen_doc(coll, doc_id) and self._full():
                    self._send_json(200, _seen_doc_all_boards(store, stores_map, primary_repo_id, coll, doc_id))
                    return
                self._send_json(200, self._store(parsed).get_doc(coll, doc_id))
                return

            if path.startswith("/_api/db/"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):].strip("/").split("/") if x]
                if len(rest) == 1 and self._is_all_repos(parsed):
                    where = json.loads(qs["where"][0]) if "where" in qs else None
                    order_by = (qs.get("order_by") or [None])[0]
                    direction = (qs.get("dir") or ["asc"])[0]
                    merged = []
                    seen_ids = set()
                    dash_rows, train_rows = [], []
                    dash_doc, train_doc = None, None
                    for rid, st in self._boards().items():
                        res = self._scope_list_docs(rest[0], st.list_docs(rest[0], where=where, order_by=order_by, direction=direction))
                        for d in res.get("docs") or []:
                            did = d.get("id") or ""
                            if rest[0] == "posts" and did == "_dashboard":
                                if dash_doc is None:
                                    dash_doc = dict(d)
                                dash_rows.extend((d.get("data") or {}).get("rows") or [])
                                continue
                            if rest[0] == "posts" and did == "_trains":
                                if train_doc is None:
                                    train_doc = dict(d)
                                train_rows.extend((d.get("data") or {}).get("rows") or [])
                                continue
                            if did in seen_ids:
                                continue
                            seen_ids.add(did)
                            dd = dict(d.get("data") or {})
                            if not did.startswith("_"):
                                dd["_repo"] = rid
                            merged.append({**d, "data": dd})
                    if dash_doc is not None:
                        dd = dict(dash_doc.get("data") or {})
                        dd["rows"] = dash_rows
                        merged.append({**dash_doc, "data": dd})
                    if train_doc is not None:
                        td = dict(train_doc.get("data") or {})
                        td["rows"] = train_rows
                        merged.append({**train_doc, "data": td})
                    self._send_json(200, {"docs": merged, "next_cursor": None})
                    return
                cur_store = self._store(parsed)
                if len(rest) == 1:
                    where = json.loads(qs["where"][0]) if "where" in qs else None
                    order_by = (qs.get("order_by") or [None])[0]
                    direction = (qs.get("dir") or ["asc"])[0]
                    limit = int(qs["limit"][0]) if "limit" in qs and qs["limit"][0] else None
                    cursor = int((qs.get("cursor") or ["0"])[0])
                    res_list = cur_store.list_docs(rest[0], where=where, order_by=order_by, direction=direction, limit=limit, cursor=cursor)
                    self._send_json(200, self._scope_list_docs(rest[0], res_list))
                    return
                if len(rest) == 2:
                    tstore, _ = self._store_for_doc(parsed, rest[0], rest[1])
                    self._send_json(200, tstore.get_doc(rest[0], rest[1]))
                    return

            self._send_json(404, {"error": "not found"})

        def do_PUT(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed, min_role="member") is None:
                return
            path = parsed.path
            try:
                body = self._read_json()
            except PayloadTooLarge as e:
                self.close_connection = True
                self._send_json(413, {"error": str(e), "code": "too_large"})
                return
            except ValueError:
                self._send_json(400, {"error": "invalid json"})
                return
            coll = doc_id = None
            if path.startswith("/_api/doc/"):
                coll, doc_id = split_path(urllib.parse.unquote(path[len("/_api/doc/"):]))
            elif path.startswith("/_api/db/"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):].strip("/").split("/") if x]
                if len(rest) == 2:
                    coll, doc_id = rest
            if not coll:
                self._send_json(404, {"error": "not found"})
                return
            tstore, nrepo = self._store_for_doc(parsed, coll, doc_id)
            if coll == "comments" and self._is_all_repos(parsed) and (body.get("data") or {}).get("post_id"):
                tstore, nrepo = self._store_for_doc(parsed, "posts", str(body["data"]["post_id"]))
            before = _lane(tstore, coll, doc_id)
            put_data = self._signed(body.get("data", {}), not before[1])
            if self._unproved(tstore, [("set", coll, doc_id, {"data": put_data})]):
                return
            res, err = tstore.set_doc(coll, doc_id, put_data, if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            _maybe_push(coll, res["id"], res["data"], before, nrepo, self.server.server_address[1])
            self._send_json(200, res)

        def do_PATCH(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed, min_role="member") is None:
                return
            path = parsed.path
            try:
                body = self._read_json()
            except PayloadTooLarge as e:
                self.close_connection = True
                self._send_json(413, {"error": str(e), "code": "too_large"})
                return
            except ValueError:
                self._send_json(400, {"error": "invalid json"})
                return
            coll = doc_id = None
            if path.startswith("/_api/doc/"):
                coll, doc_id = split_path(urllib.parse.unquote(path[len("/_api/doc/"):]))
            elif path.startswith("/_api/db/"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):].strip("/").split("/") if x]
                if len(rest) == 2:
                    coll, doc_id = rest
            if not coll:
                self._send_json(404, {"error": "not found"})
                return
            tstore, nrepo = self._store_for_doc(parsed, coll, doc_id)
            patch_data = body.get("data", {})
            before = _lane(tstore, coll, doc_id)
            if self._unproved(tstore, [("update", coll, doc_id, body)]):
                return
            res, err = tstore.update_doc(coll, doc_id, patch_data, if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            if coll == "posts" and isinstance(patch_data, dict) and patch_data.get("column") in ("todo", "question"):
                _maybe_push(coll, res["id"], res["data"], before, nrepo, self.server.server_address[1])
            self._send_json(200, res)

        def do_DELETE(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed) is None:
                return
            path = parsed.path
            if path.startswith("/_api/artifacts/"):
                aid_d = urllib.parse.unquote(path[len("/_api/artifacts/"):]).strip("/")
                ok = store.delete_artifact(aid_d)
                self._send_json(200 if ok else 404, {"deleted": ok, "artifact_id": aid_d})
                return
            if path.startswith("/_api/users/"):
                uid = urllib.parse.unquote(path[len("/_api/users/"):]).strip("/")
                ok = store.revoke_user(uid)
                self._send_json(200 if ok else 404, {"revoked": ok, "user_id": uid})
                return
            if path.startswith("/_api/shares/"):
                stok = urllib.parse.unquote(path[len("/_api/shares/"):]).strip("/")
                if not self._full() and (store.get_share(stok) or {}).get("created_by") != self._cur_uid():
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                ok = store.revoke_share(stok)
                self._send_json(200 if ok else 404, {"revoked": ok, "token": stok})
                return
            self._send_json(404, {"error": "not_found"})

        def do_POST(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path.startswith("/_share/") and path.endswith("/submit"):
                stok = urllib.parse.unquote(path[len("/_share/"):-len("/submit")]).strip("/")
                sh = store.get_share(stok)
                if not sh:
                    self._send_json(404, {"error": "share_not_found"})
                    return
                if sh["revoked"] or sh["expires_at"] < utc_now():
                    self._send_json(410, {"error": "share_expired_or_revoked"})
                    return
                if not sh["allow_write"]:
                    self._send_json(403, {"error": "share_read_only"})
                    return
                if sh["submissions"] >= sh["max_submissions"]:
                    self._send_json(429, {"error": "share_submission_limit_reached"})
                    return
                try:
                    body = self._read_json()
                except (PayloadTooLarge, ValueError):
                    self._send_json(400, {"error": "invalid json"})
                    return
                text = str(body.get("text") or body.get("body") or "").strip()
                if not text:
                    self._send_json(400, {"error": "empty text"})
                    return
                if len(text) > 500000:
                    self._send_json(413, {"error": "text exceeds 500 KB"})
                    return
                mode = str(body.get("mode") or "comment").strip().lower()
                now = utc_now()
                target_repo = sh.get("repo") or primary_repo_id
                tstore = stores_map.get(target_repo) or store
                parent_doc = tstore.get_doc("posts", sh["post_id"])
                pdata = parent_doc.get("data") or {}
                taken = {u["user_id"] for u in store.list_users()}
                for pd_ in tstore.list_docs("posts")["docs"]:
                    taken.update(str((pd_["data"] or {}).get(k) or "") for k in ("part", "session_id"))
                taken.discard("")
                if not ACCESS:
                    self._send_json(403, {"error": "forbidden"})
                    return
                sender = ACCESS.guest_name(body.get("from"), taken - {str(pdata.get("runner") or "")})
                ACTOR.name = "guest:" + sender
                if mode == "child_post":
                    idem_raw = str(body.get("idempotency_key") or "").strip()[:64]
                    if idem_raw:
                        idem_slug = re.sub(r"[^a-z0-9_-]+", "-", idem_raw.lower()).strip("-")[:16] or "run"
                        idem_hash = hashlib.sha256((stok + ":" + idem_raw).encode("utf-8")).hexdigest()[:6]
                        new_id = "%s-%s-%s" % (sh["post_id"][:36], idem_slug, idem_hash)
                    else:
                        new_id = "%s-%s-%s" % (sh["post_id"][:40], sender[:12], secrets.token_hex(3))
                    existing_child = tstore.get_doc("posts", new_id) if idem_raw else {"exists": False}
                    created_ts = (existing_child.get("data") or {}).get("created_at") if existing_child.get("exists") else now
                    title = str(body.get("title") or ((pdata.get("title") or sh["post_id"]) + " — " + sender))[:180]
                    res, _ = tstore.set_doc("posts", new_id, {
                        "title": title,
                        "body": text,
                        "column": "info",
                        "part": sender,
                        "runner": sender,
                        "by": "guest",
                        "parent": sh["post_id"],
                        "train": pdata.get("train") or "",
                        "priority": 2,
                        "created_at": created_ts,
                        "updated_at": now,
                    })
                    store.increment_share_submissions(stok)
                    self._send_json(200, {"ok": True, "mode": "child_post", "repo": target_repo, "post_id": new_id, "updated": bool(existing_child.get("exists")), "version": res["version"]})
                    return
                cid = "ext-%s-%s" % (sender[:10], secrets.token_hex(4))
                res, _ = tstore.set_doc("comments", cid, {
                    "post_id": sh["post_id"],
                    "from": sender,
                    "by": "guest",
                    "text": text,
                    "created_at": now,
                })
                store.increment_share_submissions(stok)
                self._send_json(200, {"ok": True, "mode": "comment", "repo": target_repo, "comment_id": cid, "post_id": sh["post_id"], "version": res["version"]})
                return
            if path == "/_api/unlock":
                ip = ((self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()) or (self.client_address[0] if self.client_address else "unknown")
                now_ts = time.time()
                with unlock_lock:
                    recent = [t for t in unlock_fails.get(ip, []) if now_ts - t < UNLOCK_WINDOW_SEC]
                    unlock_fails[ip] = recent
                    if len(recent) >= MAX_UNLOCK_FAILS:
                        retry_after = max(1, int(UNLOCK_WINDOW_SEC - (now_ts - recent[0])))
                        raw = json.dumps({"error": "too_many_attempts", "retry_after": retry_after}).encode("utf-8")
                        self.send_response(429)
                        self.send_header("Retry-After", str(retry_after))
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self._send_security_headers()
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                try:
                    body = self._read_json()
                except (PayloadTooLarge, ValueError):
                    self._send_json(400, {"error": "invalid json"})
                    return
                cand = str(body.get("token") or "")
                u_match = self._match_token(cand)
                if token and not u_match:
                    with unlock_lock:
                        unlock_fails.setdefault(ip, []).append(now_ts)
                    self._send_json(401, {"error": "unauthorized", "code": "unauthorized"})
                    return
                with unlock_lock:
                    unlock_fails.pop(ip, None)
                raw = json.dumps({"ok": True, "user": ({"id": u_match["id"], "name": u_match["name"], "role": u_match["role"]} if u_match else {"id": "owner", "name": "owner", "role": "owner"})}).encode("utf-8")
                self.send_response(200)
                if token and not embed_mode and u_match:
                    proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
                    cookie = "kit_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % u_match["token"]
                    if proto == "https":
                        cookie += "; Secure"
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if self._require_auth(parsed, min_role="member") is None:
                return

            if path == "/_api/blobs":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._send_json(400, {"error": "invalid content-length"})
                    return
                if length < 0:
                    self._send_json(400, {"error": "invalid content-length"})
                    return
                if length > MAX_BLOB_BYTES:
                    self.close_connection = True
                    self._send_json(413, {"error": "blob exceeds 20 MB", "code": "too_large"})
                    return
                raw = self.rfile.read(length)
                ctype = self.headers.get("Content-Type") or "application/octet-stream"
                fname = urllib.parse.unquote(self.headers.get("X-Filename") or "file")
                want = (self.headers.get("X-Blob-Id") or "").strip().lower()
                if want and not self._full():
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                try:
                    meta = self._main_store(parsed).save_blob(raw, content_type=ctype, filename=fname, blob_id=want or None)
                except ValueError as e:
                    self._send_json(413, {"error": str(e), "code": "too_large"})
                    return
                self._send_json(200, meta)
                return

            try:
                body = self._read_json()
            except PayloadTooLarge as e:
                self.close_connection = True
                self._send_json(413, {"error": str(e), "code": "too_large"})
                return
            except ValueError:
                self._send_json(400, {"error": "invalid json"})
                return

            if path == "/_api/repo-docs/pin":
                rpath = str(body.get("path") or "")
                want = body.get("pinned") if "pinned" in body else None
                res_p, perr = toggle_pinned_doc(self._repo_root(parsed), rpath, pinned=want)
                if perr:
                    self._send_json(404 if perr == "not_found" else 400, {"error": perr})
                    return
                self._send_json(200, res_p)
                return

            if path.startswith("/_api/posts/") and path.endswith("/artifacts"):
                pid_a = urllib.parse.unquote(path[len("/_api/posts/"):-len("/artifacts")]).strip("/")
                tstore, _ = self._store_for_doc(parsed, "posts", pid_a)
                if not pid_a or not tstore.get_doc("posts", pid_a).get("exists"):
                    self._send_json(404, {"error": "post_not_found"})
                    return
                res_a, aerr = tstore.save_artifact(
                    pid_a,
                    body.get("path"),
                    body.get("content", ""),
                    summary=body.get("summary", ""),
                    base_path=body.get("base_path", ""),
                    by_user=self._cur_uid(),
                    agent_meta=body.get("agent_meta"),
                )
                if aerr:
                    self._send_json(413 if aerr == "too_large" else 400, {"error": aerr})
                    return
                self._send_json(200, res_a)
                return

            if path == "/_api/attach":
                # CL32: `kit.py artifact load` attaches a board this container has no clone of (its posts come from the
                # Artifact's repos/<id>/): a store under .kit/attached/<id>, never pushed anywhere
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if u.get("role") != "owner":
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                aid = re.sub(r"[^a-z0-9_-]+", "-", str(body.get("id") or "").lower()).strip("-")
                if not aid or aid == "all" or aid == primary_repo_id:
                    self._send_json(400, {"error": "bad_repo_id", "repo": aid})
                    return
                if aid not in stores_map:
                    adir = attached_store_dir(root_dir, aid)
                    stores_map[aid] = BoardStore(os.path.join(adir, ".kit", "board_data"))
                    roots_map[aid] = adir
                    repo_meta.append({"id": aid, "name": str(body.get("name") or aid), "primary": False, "github_slugs": []})
                self._send_json(200, {"ok": True, "repo": aid, "boards": sorted(stores_map)})
                return

            if path == "/_api/users":
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if u.get("role") != "owner":
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                req_repo = str(body.get("repo") or "").strip()
                if not req_repo and not self._is_all_repos(parsed):
                    cand = self._repo_id(parsed)
                    if cand != primary_repo_id:
                        req_repo = cand
                # the board they are invited to, said in full: the one open here, or every one when asked from "all"
                if not req_repo:
                    req_repo = "all" if self._is_all_repos(parsed) else primary_repo_id
                if req_repo != "all" and req_repo not in stores_map:
                    self._send_json(400, {"error": "unknown_repo", "repo": req_repo, "boards": sorted(stores_map)})
                    return
                created = store.create_user(body.get("name"), role=body.get("role", "member"), repo=req_repo)
                if not created:
                    self._send_json(400, {"error": "name_required"})
                    return
                proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
                host_hdr = self.headers.get("Host") or ""
                origin = ("%s://%s" % (proto, host_hdr)) if host_hdr else ""
                is_np = req_repo not in ("all", primary_repo_id)
                created["link"] = origin + "/?token=" + created["token"] + (("&repo=" + urllib.parse.quote(req_repo)) if is_np else "")
                created["ai_env"] = 'export KIT_BOARD_URL="%s" KIT_BOARD_TOKEN="%s"%s' % (
                    origin, created["token"], (' KIT_BOARD_REPO="%s"' % req_repo) if is_np else ""
                )
                self._send_json(200, created)
                return

            if path == "/_api/shares":
                pid = str(body.get("post_id") or "").strip()
                target_repo = str(body.get("repo") or "").strip()
                mine = self._boards()
                if target_repo and target_repo in mine:
                    tstore = mine[target_repo]
                else:
                    tstore, target_repo = self._store_for_doc(parsed, "posts", pid)
                if pid and not tstore.get_doc("posts", pid).get("exists"):
                    for rid_k, st_k in mine.items():
                        if st_k.get_doc("posts", pid).get("exists"):
                            tstore, target_repo = st_k, rid_k
                            break
                if not pid or not tstore.get_doc("posts", pid).get("exists"):
                    self._send_json(404, {"error": "post_not_found"})
                    return
                if not self._full() and pid.startswith("_"):
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                res = store.create_share(
                    pid,
                    ttl_hours=body.get("ttl_hours", 24),
                    allow_write=bool(body.get("allow_write", True)),
                    files=body.get("files") or [],
                    max_submissions=body.get("max_submissions", 5),
                    repo=target_repo,
                    created_by="" if self._full() else self._cur_uid(),
                )
                self._send_json(200, res)
                return

            if path == "/_api/comments/watch":
                watchers = store.set_watch(str(body.get("watcher") or "clerk"), on=bool(body.get("on", True)))
                self._send_json(200, {"watching": bool(watchers), "watchers": watchers})
                return

            if path == "/_api/comments/send":
                if self._full():
                    res = store.send_comment(body.get("anchor"), str(body.get("text") or ""), by=self._cur_uid())
                else:
                    res = self._main_store(parsed).send_comment(body.get("anchor"), str(body.get("text") or ""))
                self._send_json(200, res)
                return

            if path.startswith("/_api/comments/threads/") and path.endswith("/ack"):
                tid = urllib.parse.unquote(path[len("/_api/comments/threads/"):-len("/ack")].strip("/"))
                res = store.ack_thread(tid)
                if not res:
                    self._send_json(404, {"error": "thread not found"})
                    return
                self._send_json(200, res)
                return

            if path.startswith("/_api/comments/threads/") and path.endswith("/reply"):
                tid = urllib.parse.unquote(path[len("/_api/comments/threads/"):-len("/reply")].strip("/"))
                res = store.reply_thread(tid, str(body.get("text") or ""))
                if not res:
                    self._send_json(404, {"error": "thread not found"})
                    return
                self._send_json(200, res)
                return

            if path == "/_api/notify":
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if u.get("role") != "owner":
                    self._send_json(403, {"error": "forbidden", "required_role": "owner"})
                    return
                new_url = str(body.get("notify_url") or "").strip()
                if new_url and not new_url.startswith(("http://", "https://")):
                    self._send_json(400, {"error": "invalid_notify_url"})
                    return
                notify_cfg["url"] = new_url
                if "notify_detail" in body:
                    notify_cfg["detail"] = bool(body.get("notify_detail"))
                # the topic is as good as a password: kept out of the committed config (kit.notify_url is the web sessions'
                # way to it in a private repo; $KIT_NOTIFY_URL in a public one)
                try:
                    tf = NOTIFY.topic_file(root_dir) if NOTIFY else os.path.join(root_dir, ".kit", "board_data", "notify_url")
                    os.makedirs(os.path.dirname(tf), exist_ok=True)
                    with open(tf, "w", encoding="utf-8") as fh:
                        fh.write(new_url + "\n")
                    os.chmod(tf, 0o600)
                    cfg_p = os.path.join(root_dir, ".kit", "config.json")
                    with open(cfg_p, encoding="utf-8") as fh:
                        cfg_obj = json.load(fh)
                    if bool((cfg_obj.get("kit") or {}).get("notify_detail")) != bool(notify_cfg.get("detail")):
                        cfg_obj.setdefault("kit", {})["notify_detail"] = bool(notify_cfg.get("detail"))
                        with open(cfg_p, "w", encoding="utf-8") as fh:
                            json.dump(cfg_obj, fh, indent=1, ensure_ascii=False)
                            fh.write("\n")
                except (OSError, ValueError):
                    pass
                sent = False
                if body.get("test") and new_url and NOTIFY:
                    tap, buttons = NOTIFY.links(_notify_settings(), "", port=self.server.server_address[1])
                    sent = NOTIFY.send(new_url, "%s: test" % project_name, "Phone notifications are on.", tap, buttons) is None
                self._send_json(200, {"ok": True, "notify_url": new_url, "notify_detail": bool(notify_cfg.get("detail")), "sent": sent})
                return

            if path == "/_api/db/batch":
                ops = body.get("writes") if isinstance(body, dict) else body
                bstore = self._main_store(parsed)
                # `sync`: a copy from git or the Artifact, whose own writer notified already
                synced = bool(isinstance(body, dict) and body.get("sync") and self._full())
                lanes = {} if synced else {str(op.get("doc_id")): _lane(bstore, "posts", op.get("doc_id")) for op in (ops or [])
                                           if isinstance(op, dict) and op.get("collection") == "posts" and op.get("doc_id")}
                if not synced and self._unproved(bstore, [(op.get("op") or "set", op.get("collection"), op.get("doc_id"), op)
                                                          for op in (ops or []) if isinstance(op, dict)]):
                    return
                results, err = bstore.batch_ops(ops or [])
                if err is not None:
                    kind, doc_id, detail = err
                    if kind == "unknown_op":
                        self._send_json(400, {"error": "unknown op: " + str(detail)})
                    else:
                        self._send_json(409, {"error": "batch_conflict", "doc_id": doc_id, "detail": detail})
                    return
                for op in () if synced else (ops or []):
                    if isinstance(op, dict) and op.get("op") in ("set", "update") and op.get("collection") in ("posts", "comments"):
                        coll_, did_ = op.get("collection"), str(op.get("doc_id"))
                        now_ = (bstore.get_doc(coll_, did_).get("data") if op.get("op") == "update" else None) or op.get("data") or {}
                        _maybe_push(coll_, did_, now_, lanes.get(did_, (None, False)) if coll_ == "posts" else (None, False), "",
                                    self.server.server_address[1])
                self._send_json(200, {"ok": True, "results": results})
                return

            if path.startswith("/_api/db/") and path.endswith("/str_replace"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):-len("/str_replace")].strip("/").split("/") if x]
                if len(rest) == 2:
                    if self._unproved(self._main_store(parsed), [("str_replace", rest[0], rest[1], body)]):
                        return
                    res, err = self._main_store(parsed).str_replace(
                        rest[0], rest[1], body.get("field", "body"),
                        body.get("old_str", ""), body.get("new_str", ""),
                        if_version=body.get("if_version"),
                    )
                    if err is not None:
                        code = 404 if err in ("not_found", "old_str_not_found") else 409
                        self._send_json(code, {"error": str(err)})
                        return
                    self._send_json(200, res)
                    return

            self._send_json(404, {"error": "not found"})

    def guarded(fn):
        def run(self):
            ACTOR.name = ""
            try:
                return fn(self)
            except Exception as e:
                if ACCESS and isinstance(e, ACCESS.Denied):
                    self._send_json(e.code, {"error": "taken" if e.code == 409 else "forbidden", "why": e.why})
                    return None
                raise
        return run

    for verb in ("do_GET", "do_PUT", "do_PATCH", "do_DELETE", "do_POST"):
        setattr(Handler, verb, guarded(getattr(Handler, verb)))
    return Handler


def _base36(n):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = []
    while n > 0:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def _post_sig(q):
    """Match board.html sigOf(q) bit-for-bit (UTF-16 DJB2 hash + base36 length)."""
    def s(v):
        return v if isinstance(v, str) else ""
    imgs = [s(x) for x in q.get("images")] if isinstance(q.get("images"), list) else []
    caps = [s(x) for x in q.get("captions")] if isinstance(q.get("captions"), list) else []
    t = "\x01".join([s(q.get("title")), s(q.get("body")), s(q.get("column")), ",".join(imgs), "\x02".join(caps)])
    raw = t.encode("utf-16-le")
    n_units = len(raw) // 2
    h = 5381
    for i in range(0, len(raw), 2):
        code = raw[i] | (raw[i + 1] << 8)
        h = ((h * 33) ^ code) & 0xFFFFFFFF
    return _base36(h) + "." + _base36(n_units)


def seed_from_mirror(root_dir, store, branch=None, force=False):
    """Import board/posts/*.json and board/comments/*.json from git mirror branch into SQLite.

    Reads directly from git objects (origin/<branch>, falling back to <branch>) in one `git archive`
    packfile fetch without modifying the working tree. Existing docs in SQLite are preserved unless
    force=True. Also imports board/seen.json if present and seeds data/users/owner/board-seen for
    imported historical posts so they do not appear unread on first boot.
    """
    import io
    import tarfile

    if not branch:
        cfg_path = os.path.join(root_dir, ".kit", "config.json")
        try:
            branch = json.load(open(cfg_path, encoding="utf-8")).get("mirror", {}).get("branch") or "board-mirror"
        except (OSError, ValueError):
            branch = "board-mirror"

    def git(*a):
        return subprocess.run(["git", "-C", root_dir] + list(a), capture_output=True, text=True, timeout=60)

    # Explicit refspec ensures single-branch clones populate refs/remotes/origin/<branch>.
    git("fetch", "-q", "origin", "%s:refs/remotes/origin/%s" % (branch, branch))
    ref = None
    for cand in ("origin/" + branch, branch):
        if git("rev-parse", "--verify", "--quiet", cand).returncode == 0:
            ref = cand
            break
    if not ref:
        return {"ref": None, "imported": 0, "skipped": 0}
    arc = subprocess.run(
        ["git", "-C", root_dir, "archive", "--format=tar", ref, "board"],
        capture_output=True,
        timeout=180,
    )
    if arc.returncode != 0 or not arc.stdout:
        return {"ref": ref, "imported": 0, "skipped": 0}

    imported = skipped = blobs_restored = 0
    sync_cursor = ""
    mirror_seen = {}
    imported_posts = {}
    referenced_blobs = set()
    with tarfile.open(fileobj=io.BytesIO(arc.stdout), mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = tar_name(member.name)
            parts = name.split("/")
            if len(parts) == 3 and parts[0] == "board" and parts[1] == "blobs":
                bfn = parts[2]
                if re.fullmatch(r"[a-fA-F0-9]{16,64}(\.json)?", bfn):
                    dst = os.path.join(store.blob_dir, bfn)
                    if force or not os.path.exists(dst):
                        fobj = tf.extractfile(member)
                        if fobj is not None:
                            with open(dst, "wb") as out_fh:
                                out_fh.write(fobj.read())
                            if not bfn.endswith(".json"):
                                blobs_restored += 1
                continue
            if not member.name.endswith(".json"):
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            raw_bytes = fobj.read()
            try:
                raw_text = raw_bytes.decode("utf-8")
                data = json.loads(raw_text)
            except (UnicodeDecodeError, ValueError):
                continue
            for bid in re.findall(r"/_blob/([a-fA-F0-9]{16,64})", raw_text):
                referenced_blobs.add(bid)
            if name == "board/sync.json" and isinstance(data, dict):
                sync_cursor = str(data.get("cursor") or "")
                continue
            if name in ("board/seen.json", "board/seen/owner.json") and isinstance(data, dict):
                seen_map = data.get("posts") if isinstance(data.get("posts"), dict) else data
                _merge_board_seen_posts(mirror_seen, seen_map)
                continue
            if len(parts) != 3 or parts[0] != "board" or parts[1] not in ("posts", "comments"):
                continue
            coll, doc_id = parts[1], parts[2][:-5]
            if not force and store.get_doc(coll, doc_id).get("exists"):
                skipped += 1
                continue
            store.set_doc(coll, doc_id, data)
            imported += 1
            if coll == "posts":
                imported_posts[doc_id] = data

    # Preserve/seed notification read state so historical mirror posts don't flood as unread.
    cur_seen = store.get_doc("data/users/owner", "board-seen")
    seen_posts = dict(((cur_seen.get("data") or {}).get("posts") or {}))
    seen_changed = False
    for pid, entry in mirror_seen.items():
        if isinstance(entry, dict) and (force or pid not in seen_posts):
            seen_posts[pid] = entry
            seen_changed = True
    for pid, pdata in imported_posts.items():
        if pid in ("_dashboard", "_trains"):
            continue
        if pid not in seen_posts:
            at = sync_cursor or str(pdata.get("updated_at") or pdata.get("created_at") or utc_now())
            seen_posts[pid] = {"at": at, "sig": _post_sig(pdata), "bt": at}
            seen_changed = True
    if seen_changed:
        store.set_doc("data/users/owner", "board-seen", {"posts": seen_posts})

    missing_blobs = sorted(b for b in referenced_blobs if not os.path.isfile(os.path.join(store.blob_dir, b)))
    if missing_blobs:
        print("seed_from_mirror: WARNING: %d referenced blob(s) missing locally (first 5: %s)" % (len(missing_blobs), ", ".join(missing_blobs[:5])), file=sys.stderr)
    return {"ref": ref, "imported": imported, "skipped": skipped, "blobs_restored": blobs_restored, "missing_blobs": len(missing_blobs)}


def export_json_bundle(store, post_id=None):
    """Export one post or all non-internal posts + comments as a portable JSON bundle."""
    if post_id:
        doc = store.get_doc("posts", post_id)
        if not doc.get("exists"):
            return None, None
        posts = [{"id": post_id, "data": doc["data"]}]
        fname = re.sub(r"[^A-Za-z0-9._-]+", "-", post_id.lstrip("_") or "post") + ".json"
    else:
        posts = [p for p in store.list_docs("posts")["docs"] if p["id"] not in ("_dashboard", "_trains") and not (p["data"] or {}).get("removed")]
        fname = "board-export.json"
    pids = {p["id"] for p in posts}
    comments = [c for c in store.list_docs("comments")["docs"] if (c["data"] or {}).get("post_id") in pids]
    return {"exported_at": utc_now(), "posts": posts, "comments": comments}, fname


def export_rss(store, project_name="Board"):
    """Render recent non-internal posts as an RSS 2.0 XML feed."""
    from xml.sax.saxutils import escape as xesc
    posts = [p for p in store.list_docs("posts")["docs"] if not p["id"].startswith("_") and not (p["data"] or {}).get("removed")]
    posts.sort(key=lambda p: str((p["data"] or {}).get("created_at") or ""), reverse=True)
    items = []
    for p in posts[:50]:
        d = p["data"] or {}
        title = xesc(str(d.get("title") or p["id"]))
        guid = xesc(p["id"])
        desc = xesc(str(d.get("body") or "")[:1000])
        col = xesc(str(d.get("column") or "info"))
        pub = xesc(str(d.get("created_at") or ""))
        items.append(
            "    <item>\n"
            "      <title>%s</title>\n"
            "      <guid isPermaLink=\"false\">%s</guid>\n"
            "      <category>%s</category>\n"
            "      <pubDate>%s</pubDate>\n"
            "      <description>%s</description>\n"
            "    </item>" % (title, guid, col, pub, desc)
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '  <channel>\n'
        '    <title>%s Board</title>\n'
        '    <description>Updates and decisions from %s</description>\n'
        '%s\n'
        '  </channel>\n'
        '</rss>\n' % (xesc(project_name), xesc(project_name), "\n".join(items))
    )
    return xml, "board-feed.xml"


def export_markdown(store, post_id=None):
    """Render one post or all non-internal posts + comments as Markdown for /_api/export."""
    if post_id:
        doc = store.get_doc("posts", post_id)
        if not doc.get("exists"):
            return None, None
        posts = [{"id": post_id, "data": doc["data"]}]
        fname = re.sub(r"[^A-Za-z0-9._-]+", "-", post_id.lstrip("_") or "post") + ".md"
    else:
        all_posts = store.list_docs("posts")["docs"]
        posts = [p for p in all_posts if p["id"] not in ("_dashboard", "_trains") and not (p["data"] or {}).get("removed")]
        posts.sort(key=lambda p: str((p["data"] or {}).get("created_at") or ""))
        fname = "board-export.md"
    all_comments = store.list_docs("comments")["docs"]
    by_post = {}
    for c in all_comments:
        pid = (c["data"] or {}).get("post_id")
        if pid:
            by_post.setdefault(pid, []).append(c)
    blocks = []
    for p in posts:
        d = p["data"] or {}
        lines = [
            "# %s" % (d.get("title") or p["id"]),
            "*%s · part %s · P%s · post `%s`*" % (d.get("column") or "info", d.get("part") or "?", d.get("priority") or 2, p["id"]),
            "",
            (d.get("body") or "").strip(),
        ]
        cs = sorted(by_post.get(p["id"], []), key=lambda c: str((c["data"] or {}).get("created_at") or ""))
        if cs:
            lines.extend(["", "## Comments (%d)" % len(cs)])
            for c in cs:
                cd = c["data"] or {}
                who = cd.get("from") or "unknown"
                when = str(cd.get("created_at") or "")[:16].replace("T", " ")
                txt = "👍" if cd.get("like") else (("[answer] " + str(cd["answer"])) if cd.get("answer") else str(cd.get("text") or ""))
                lines.append("- **%s** (%s) `[id: %s]`: %s" % (who, when, c["id"], txt.replace("\n", "\n  ")))
        blocks.append("\n".join(lines).strip())
    return ("\n\n---\n\n".join(blocks) + "\n") if blocks else "# Board export\n\n(No posts yet.)\n", fname


def attached_store_dir(root_dir, aid):
    """Where an attached board with no clone in this container keeps its posts (CL32): what `kit.py artifact load`
    puts there from the Artifact's repos/<id>/, so `kit.py launch` finds the board's steps."""
    return os.path.join(os.path.abspath(root_dir), ".kit", "attached", aid)


def resolve_attached(root_dir, item):
    """(id, path or None, why) of one portfolio entry, in any container (CL32). The entry's path names a folder on
    the computer it was written on (Harmonize's names /home/anon/board); a cloud clone has none, and the board then
    attached nothing, silently. In order: the path when it exists; a clone next to this repo named after its github
    repo, its name or its id; the store `kit.py artifact load` made for it (.kit/attached/<id>). Else None and why."""
    if not isinstance(item, dict):
        return "", None, "not an entry"
    gh = str(item.get("github") or "")
    gh = (gh[len("github:"):] if gh.startswith("github:") else gh).strip("/")
    raw = str(item.get("path") or "")
    aname = str(item.get("name") or (gh.split("/")[-1] if gh else "") or os.path.basename(os.path.abspath(raw)) if (item.get("name") or gh or raw) else "")
    aid = re.sub(r"[^a-z0-9_-]+", "-", aname.lower()).strip("-")
    if not aid:
        return "", None, "no name, github or path"
    up = os.path.dirname(os.path.abspath(root_dir))
    cands = [raw] if raw else []
    cands += [os.path.join(up, n) for n in (gh.split("/")[-1] if gh else "", aname, aid) if n]
    for c in cands:
        c = os.path.abspath(os.path.expanduser(c))
        if os.path.isdir(c) and c != os.path.abspath(root_dir) and (os.path.isdir(os.path.join(c, ".git")) or os.path.isdir(os.path.join(c, ".kit"))):
            return aid, c, ""
    made = attached_store_dir(root_dir, aid)
    if os.path.isdir(os.path.join(made, ".kit", "board_data")):
        return aid, made, ""
    return aid, None, ("no folder %s here and no clone next to this repo (%s); clone %s next to it, or pull the Artifact's "
                       "repos/%s and run `kit.py artifact load`" % (raw or "(none named)", up, gh or aid, aid))


def start_server(root_dir, host="127.0.0.1", port=8765, token=None, notify_url=None, embed_mode=False, preview=False, default_watcher=None, auto_sync_interval=0.0, auto_sync_push=False, attach=None):
    if host.strip().lower() not in LOOPBACK_HOSTS and not token and not preview:
        raise ValueError("refusing to bind to non-loopback host %r without --token / KIT_BOARD_TOKEN" % host)
    data_dir = os.path.join(root_dir, ".kit", "board_data")
    store = BoardStore(data_dir)
    if auto_sync_interval and float(auto_sync_interval) > 0:
        store.enable_auto_sync(root_dir, interval_sec=float(auto_sync_interval), push=auto_sync_push)
        if port:
            store._auto_sync_url = "http://127.0.0.1:%d" % int(port)
            store._auto_sync_token = token or ""
    extra_stores = {}
    attach_specs = dict(attach or {})
    cfg_path = os.path.join(root_dir, ".kit", "config.json")
    try:
        cfg_data = json.load(open(cfg_path, encoding="utf-8"))
    except (OSError, ValueError):
        cfg_data = {}
    unresolved = {}
    for item in (cfg_data.get("portfolio") or []):
        aid, apath, why = resolve_attached(root_dir, item)
        if aid and aid not in attach_specs:
            if apath:
                attach_specs[aid] = apath
            else:
                unresolved[aid] = why
    for aid, why in unresolved.items():
        print("board_server: attached board %r NOT attached: %s" % (aid, why), file=sys.stderr, flush=True)
    for aid, apath in attach_specs.items():
        ap_abs = os.path.abspath(apath)
        if os.path.isdir(ap_abs):
            ast = BoardStore(os.path.join(ap_abs, ".kit", "board_data"))
            if not ast.list_docs("posts").get("docs"):
                try:
                    sinfo = seed_from_mirror(ap_abs, ast)
                    if sinfo.get("imported"):
                        print("board_server: auto-seeded attached repo %r from %s (%d docs)" % (aid, sinfo["ref"], sinfo["imported"]), flush=True)
                except Exception:
                    pass
            if auto_sync_interval and float(auto_sync_interval) > 0:
                # CL32: never push another repo's board-mirror from here: this clone does not own that board (an
                # orchestrator's Harmonize clone pushed seeded posts to anlonlt/board's board-mirror on 11-23 22:04)
                ast.enable_auto_sync(ap_abs, interval_sec=float(auto_sync_interval), push=False)
            aname = aid
            try:
                aname = json.load(open(os.path.join(ap_abs, ".kit", "config.json"), encoding="utf-8")).get("project") or aid
            except (OSError, ValueError):
                pass
            extra_stores[aid] = {"name": aname, "root": ap_abs, "store": ast}
    if preview:
        if not token:
            print(
                "board_server: WARNING: --preview disables authentication! "
                "Anyone who can reach http://%s:%d can read and write this board." % (host, port),
                file=sys.stderr,
                flush=True,
            )
        if not store.get_doc("posts", "_orchestrator").get("exists"):
            store.set_doc("posts", "_orchestrator", {
                "title": "Ask the orchestrator",
                "column": "info",
                "part": "orchestrator",
                "priority": 3,
                "created_at": utc_now(),
                "body": "Write here for anything that is not about one post.",
            })
        if default_watcher is None:
            default_watcher = "agent"
    if default_watcher:
        store.set_watch(default_watcher, True)
    if notify_url is None:
        notify_url = (NOTIFY.topic(root_dir, cfg_data.get("kit") or {}) if NOTIFY else cfg_data.get("kit", {}).get("notify_url")) or None
    srv = BoardHTTPServer((host, port), make_handler(root_dir, store, token=token, notify_url=notify_url, embed_mode=embed_mode, extra_stores=extra_stores))
    srv._extra_stores = extra_stores
    return srv, store


def _file_sig(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _funnel_allowed(root_dir):
    import shutil
    import tempfile
    if os.environ.get("KIT_NO_FUNNEL") == "1" or not shutil.which("tailscale"):
        return False
    if os.path.abspath(root_dir).startswith(os.path.abspath(tempfile.gettempdir()) + os.sep):
        return False
    try:
        with open(os.path.join(root_dir, ".kit", "config.json"), encoding="utf-8") as fh:
            return json.load(fh).get("kit", {}).get("funnel") is not False
    except (OSError, ValueError):
        return True


def _take_funnel(port):
    try:
        subprocess.run(["tailscale", "funnel", "--bg", str(port)], capture_output=True, text=True, timeout=15)
    except Exception:
        pass


def _answers(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
            return True
    except OSError:
        return False


def _funnel_lost(port):
    """True when the funnel exists and its root points at a port of this computer where nothing answers (the newest
    board stopped): a board that runs takes it back. A live port, someone else's use of it, or no funnel: left alone."""
    got = NOTIFY.funnel_target(NOTIFY.funnel_status(max_age=0)) if NOTIFY else None
    return bool(got and got[1] != int(port) and not _answers(got[1]))


def _ensure_funnel_async(port, root_dir, every=60):
    """The funnel follows the newest board: taken at startup and reload, then taken back when the board it points at
    is gone. Best effort, never blocks."""
    if not _funnel_allowed(root_dir):
        return

    def _run():
        _take_funnel(port)
        while True:
            time.sleep(every)
            try:
                if _funnel_lost(port):
                    _take_funnel(port)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def _reload_argv():
    """Build argv for in-place os.execv reload, stripping one-shot seed flags."""
    skip = {"--seed-from-mirror", "--force-seed"}
    args = [a for a in sys.argv[1:] if a not in skip]
    cmd = [sys.executable]
    if sys.flags.isolated:
        cmd.append("-I")
    cmd.append(os.path.abspath(__file__))
    cmd.extend(args)
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local board server for agy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=None, help="Project root (default: parent of .kit/)")
    ap.add_argument("--token", default=None, help="Require auth token (or set $KIT_BOARD_TOKEN)")
    ap.add_argument("--notify-url", default=None, help="Phone push webhook URL (e.g. https://ntfy.sh/<topic>)")
    ap.add_argument("--embed-mode", action="store_true", help="Cookie-independent embedded preview mode (or set KIT_EMBED_PREVIEW=1)")
    ap.add_argument("--preview", "--unsafe-no-auth", dest="preview", action="store_true", help="Opt-in sandbox preview mode: allows 0.0.0.0 without --token, auto-seeds _orchestrator, and registers default watcher")
    ap.add_argument("--default-watcher", default=None, help="Auto-register a comment watcher name on startup (default: 'agent' when --preview is set)")
    ap.add_argument("--auto-sync-interval", type=float, default=None, help="Debounced interval in seconds to auto-run kit.py sync-db --commit when board-mirror worktree exists (0 to disable)")
    ap.add_argument("--auto-sync-push", action="store_true", help="Also run kit.py push after auto-sync-db commits")
    ap.add_argument("--seed-from-mirror", action="store_true", help="Import posts/comments from git board-mirror on startup")
    ap.add_argument("--mirror-branch", default=None, help="Override mirror branch name (default from .kit/config.json)")
    ap.add_argument("--force-seed", action="store_true", help="Overwrite existing local SQLite docs when seeding")
    ap.add_argument("--attach", action="append", default=[], help="Attach another local repo board as name=path (or path) for cross-repo viewing & routed /_share")
    ap.add_argument("--auto-reload", action="store_true", help="Watch board_server.py and .kit/config.json and reload in-place via os.execv on changes")
    ap.add_argument("--reload-interval", type=float, default=1.5, help="Polling interval in seconds for --auto-reload (default: 1.5)")
    ap.add_argument("--funnel", action="store_true", help="Refresh `tailscale funnel --bg <port>` on startup/reload when kit.funnel is enabled")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root) if a.root else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tok = a.token or os.environ.get("KIT_BOARD_TOKEN") or None
    if a.host.strip().lower() not in LOOPBACK_HOSTS and not tok and not a.preview:
        print("board_server: refusing to bind to non-loopback host %r without --token or KIT_BOARD_TOKEN" % a.host, file=sys.stderr)
        return 2
    nurl = a.notify_url or os.environ.get("KIT_NOTIFY_URL") or None
    embed = bool(a.embed_mode or os.environ.get("KIT_EMBED_PREVIEW", "").strip().lower() in ("1", "true", "yes", "on"))
    extra = {}
    if a.preview:
        extra["preview"] = True
    if a.default_watcher:
        extra["default_watcher"] = a.default_watcher
    auto_sync = a.auto_sync_interval
    if auto_sync is None and os.environ.get("KIT_AUTO_SYNC_INTERVAL"):
        try:
            auto_sync = float(os.environ["KIT_AUTO_SYNC_INTERVAL"])
        except ValueError:
            auto_sync = 0.0
    if auto_sync and auto_sync > 0:
        extra["auto_sync_interval"] = auto_sync
    if a.auto_sync_push or os.environ.get("KIT_AUTO_SYNC_PUSH", "").strip().lower() in ("1", "true", "yes", "on"):
        extra["auto_sync_push"] = True
    if a.attach:
        att = {}
        for spec in a.attach:
            if "=" in spec:
                k, v = spec.split("=", 1)
                att[re.sub(r"[^a-z0-9_-]+", "-", k.strip().lower()).strip("-")] = v.strip()
            else:
                v = os.path.abspath(spec.strip())
                att[re.sub(r"[^a-z0-9_-]+", "-", os.path.basename(v).lower()).strip("-")] = v
        extra["attach"] = att
    srv, store = start_server(
        root, a.host, a.port, token=tok, notify_url=nurl,
        embed_mode=embed, **extra,
    )
    if a.seed_from_mirror:
        info = seed_from_mirror(root, store, branch=a.mirror_branch, force=a.force_seed)
        print("seeded from %s: %d imported, %d skipped" % (info["ref"] or "(none)", info["imported"], info["skipped"]), flush=True)
    actual_port = srv.server_address[1]
    auto_reload = bool(a.auto_reload or os.environ.get("KIT_BOARD_AUTO_RELOAD", "").strip().lower() in ("1", "true", "yes", "on"))
    if a.funnel or (auto_reload and a.host.strip().lower() not in LOOPBACK_HOSTS):
        _ensure_funnel_async(actual_port, root)
    reload_requested = [False]
    stop_watch = threading.Event()
    if auto_reload:
        script_path = os.path.abspath(__file__)
        cfg_path = os.path.join(root, ".kit", "config.json")
        html_watch = os.path.join(root, ".kit", "board.html")
        ui_watches = {html_watch, _SHIM_PATH}
        watched = [script_path, cfg_path, html_watch, _SHIM_PATH]
        interval = max(0.1, float(a.reload_interval or 1.5))

        def _watcher():
            import py_compile
            sigs = {p: _file_sig(p) for p in watched}
            while not stop_watch.wait(interval):
                changed = [p for p in watched if _file_sig(p) != sigs[p]]
                if not changed:
                    continue
                time.sleep(0.2)
                new_sigs = {p: _file_sig(p) for p in watched}
                if all(p in ui_watches for p in changed):
                    for p in changed:
                        sigs[p] = new_sigs[p]
                    names = ", ".join(os.path.basename(p) for p in changed)
                    print("board_server: detected UI update (%s), pushing live browser reload" % names, flush=True)
                    store.notify_ui_reload(names)
                    continue
                if script_path in changed:
                    try:
                        py_compile.compile(script_path, doraise=True)
                    except Exception as exc:
                        print("board_server: syntax error in %s (%s); keeping current server running" % (script_path, exc), file=sys.stderr, flush=True)
                        sigs[script_path] = new_sigs[script_path]
                        continue
                print("board_server: detected update (%s), reloading in-place..." % ", ".join(os.path.basename(p) for p in changed), flush=True)
                reload_requested[0] = True
                srv.shutdown()
                return

        threading.Thread(target=_watcher, daemon=True).start()
    print("board server listening on http://%s:%d (root: %s)" % (a.host, actual_port, root), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_watch.set()
        srv.server_close()
        store.close()
        for rinfo in (getattr(srv, "_extra_stores", None) or {}).values():
            try:
                rinfo["store"].close()
            except Exception:
                pass
    if reload_requested[0]:
        cmd = _reload_argv()
        os.execv(cmd[0], cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
