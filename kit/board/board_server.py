#!/usr/bin/env python3
"""Local HTTP/SSE server for the board kit in agy on a computer (zero external dependencies).

    python3 -I .kit/board_server.py [--host 127.0.0.1] [--port 8765] [--root .]

Serves .kit/board.html with an injected window.claude shim (/_shim.js) backed by local SQLite
(.kit/board_data/board.db) and local blobs (.kit/board_data/blobs/), with Server-Sent Events
(/_api/events) for live onSnapshot updates and a local comment-watch queue for the board clerk.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SHIM_JS = r"""(function () {
  "use strict";
  var subs = [];
  var es = null;
  var reconnectTimer = null;

  function api(method, path, body, headers) {
    var opts = { method: method, headers: Object.assign({}, headers || {}) };
    if (body !== undefined && body !== null) {
      if (body instanceof Blob || body instanceof ArrayBuffer || body instanceof Uint8Array) {
        opts.body = body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
    }
    return fetch(path, opts).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          var err = new Error(j.error || ("HTTP " + r.status));
          err.code = j.code || j.error || ("http_" + r.status);
          err.status = r.status;
          throw err;
        });
      }
      return r.json();
    });
  }

  function makeSnap(docs) {
    return {
      metadata: { fromCache: false },
      docs: docs.map(function (d) {
        return { id: d.id, data: function () { return d.data; } };
      })
    };
  }

  function refreshSub(s) {
    if (s.kind === "coll") {
      var qs = [];
      if (s.where.length) qs.push("where=" + encodeURIComponent(JSON.stringify(s.where)));
      if (s.orderBy) {
        qs.push("order_by=" + encodeURIComponent(s.orderBy.field));
        qs.push("dir=" + encodeURIComponent(s.orderBy.dir));
      }
      if (s.limit) qs.push("limit=" + encodeURIComponent(String(s.limit)));
      var url = "/_api/db/" + encodeURIComponent(s.coll) + (qs.length ? "?" + qs.join("&") : "");
      api("GET", url).then(function (res) {
        s.cb(makeSnap(res.docs || []));
      }, function (e) {
        if (s.errCb) s.errCb(e);
      });
    } else if (s.kind === "doc") {
      var u = "/_api/doc/" + s.path.split("/").map(encodeURIComponent).join("/");
      api("GET", u).then(function (res) {
        s.cb({
          id: res.id,
          exists: !!res.exists,
          metadata: { fromCache: false },
          data: function () { return res.data || {}; }
        });
      }, function (e) {
        if (s.errCb) s.errCb(e);
      });
    }
  }

  function connectEvents() {
    if (es) return;
    try {
      es = new EventSource("/_api/events");
      es.addEventListener("change", function (ev) {
        var info = {};
        try { info = JSON.parse(ev.data || "{}"); } catch (e) {}
        subs.forEach(function (s) {
          if (s.kind === "coll" && (!info.collection || info.collection === s.coll)) {
            refreshSub(s);
          } else if (s.kind === "doc" && (!info.path || info.path === s.path)) {
            refreshSub(s);
          }
        });
      });
      es.onerror = function () {
        if (es) { try { es.close(); } catch (e) {} es = null; }
        if (!reconnectTimer) {
          reconnectTimer = setTimeout(function () { reconnectTimer = null; connectEvents(); }, 2000);
        }
      };
    } catch (e) {}
  }

  function makeQuery(coll, where, orderBy, limit) {
    return {
      where: function (f, op, v) { return makeQuery(coll, where.concat([[f, op, v]]), orderBy, limit); },
      orderBy: function (field, dir) { return makeQuery(coll, where, { field: field, dir: dir || "asc" }, limit); },
      limit: function (n) { return makeQuery(coll, where, orderBy, n); },
      onSnapshot: function (cb, errCb) {
        var sub = { kind: "coll", coll: coll, where: where, orderBy: orderBy, limit: limit, cb: cb, errCb: errCb };
        subs.push(sub);
        connectEvents();
        refreshSub(sub);
        return function () { subs = subs.filter(function (x) { return x !== sub; }); };
      }
    };
  }

  function genId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "").slice(0, 20);
    return "id" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function docRef(path) {
    var parts = path.split("/");
    var enc = parts.map(encodeURIComponent).join("/");
    return {
      id: parts[parts.length - 1],
      get: function () {
        return api("GET", "/_api/doc/" + enc).then(function (res) {
          return { id: res.id, exists: !!res.exists, data: function () { return res.data || {}; } };
        });
      },
      set: function (data, opts) {
        var body = { data: data };
        if (opts && opts.merge) return api("PATCH", "/_api/doc/" + enc, body);
        return api("PUT", "/_api/doc/" + enc, body);
      },
      update: function (data) {
        return api("PATCH", "/_api/doc/" + enc, { data: data });
      },
      onSnapshot: function (cb, errCb) {
        var sub = { kind: "doc", path: path, cb: cb, errCb: errCb };
        subs.push(sub);
        connectEvents();
        refreshSub(sub);
        return function () { subs = subs.filter(function (x) { return x !== sub; }); };
      }
    };
  }

  var db = {
    collection: function (name) {
      var q = makeQuery(name, [], null, null);
      q.doc = function (id) { return docRef(name + "/" + (id || genId())); };
      q.add = function (data) {
        var id = genId();
        return docRef(name + "/" + id).set(data).then(function () { return { id: id }; });
      };
      return q;
    },
    doc: function (path) { return docRef(path); }
  };

  var comments = {
    canSendToClaude: function () {
      return api("GET", "/_api/comments/status").then(function (r) {
        return r.status || "available";
      }, function () { return "off"; });
    },
    anchorFor: function (el) {
      return Promise.resolve({
        tag: el && el.tagName ? el.tagName.toLowerCase() : "",
        id: el && el.id ? el.id : "",
        text: el && el.textContent ? el.textContent.trim().slice(0, 80) : ""
      });
    },
    sendToClaude: function (req) {
      return api("POST", "/_api/comments/send", { anchor: req.anchor || {}, text: req.text || "" }).then(function (r) {
        return { threadId: r.thread_id };
      });
    }
  };

  var assets = {
    upload: function (file) {
      var headers = {
        "Content-Type": file.type || "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name || "file")
      };
      return api("POST", "/_api/blobs", file, headers);
    }
  };

  var downloads = {
    save: function (f) {
      try {
        var blob = new Blob([f.data || ""], { type: "text/markdown;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = f.filename || "board-export.md";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        return Promise.resolve();
      } catch (e) {
        return Promise.reject({ code: "error", message: String(e) });
      }
    }
  };

  var user = {
    isOwner: function () { return Promise.resolve(true); },
    id: function () { return Promise.resolve("owner"); }
  };

  window.claude = {
    use: function (cap) {
      if (cap === "db") return Promise.resolve(db);
      if (cap === "comments") return Promise.resolve(comments);
      if (cap === "assets") return Promise.resolve(assets);
      if (cap === "downloads") return Promise.resolve(downloads);
      if (cap === "user") return Promise.resolve(user);
      return Promise.resolve(null);
    }
  };
})();
"""


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


class BoardStore:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.blob_dir = os.path.join(self.data_dir, "blobs")
        os.makedirs(self.blob_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "board.db")
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
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
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS threads ("
            "  thread_id TEXT PRIMARY KEY,"
            "  anchor TEXT NOT NULL,"
            "  text TEXT NOT NULL,"
            "  status TEXT NOT NULL,"
            "  created_at TEXT NOT NULL,"
            "  dispatched INTEGER NOT NULL DEFAULT 0,"
            "  replies TEXT NOT NULL DEFAULT '[]'"
            ")"
        )
        self.conn.commit()
        self.sse_clients = set()
        self.sse_lock = threading.Lock()
        self.watchers = set()
        self.comment_cond = threading.Condition()

    def close(self):
        with self.lock:
            self.conn.close()

    def notify_sse(self, collection, doc_id):
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

    def get_doc(self, collection, doc_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
        if not row:
            return {"exists": False, "id": doc_id, "version": 0, "data": {}}
        return {"exists": True, "id": doc_id, "version": row[0], "data": json.loads(row[1])}

    def list_docs(self, collection, where=None, order_by=None, direction="asc", limit=1000, cursor=0):
        with self.lock:
            rows = self.conn.execute(
                "SELECT doc_id, version, data FROM docs WHERE collection=? ORDER BY doc_id ASC",
                (collection,),
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
        lim = max(1, int(limit or 1000))
        page = docs[start:start + lim]
        next_cursor = str(start + lim) if (start + lim) < len(docs) else None
        return {"docs": page, "next_cursor": next_cursor, "total": len(docs)}

    def set_doc(self, collection, doc_id, data, if_version=None):
        now = utc_now()
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
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        return {"id": doc_id, "version": new_ver, "data": data}, None

    def update_doc(self, collection, doc_id, patch, if_version=None):
        now = utc_now()
        with self.lock:
            row = self.conn.execute(
                "SELECT version, data FROM docs WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            ).fetchone()
            cur_ver = row[0] if row else 0
            if if_version is not None and int(if_version) != cur_ver:
                return None, cur_ver
            cur_data = json.loads(row[1]) if row else {}
            cur_data.update(patch)
            new_ver = cur_ver + 1
            self.conn.execute(
                "INSERT INTO docs(collection, doc_id, version, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(collection, doc_id) DO UPDATE SET version=excluded.version, data=excluded.data, updated_at=excluded.updated_at",
                (collection, doc_id, new_ver, json.dumps(cur_data, ensure_ascii=False), now),
            )
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        return {"id": doc_id, "version": new_ver, "data": cur_data}, None

    def str_replace(self, collection, doc_id, field, old_str, new_str, if_version=None):
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
            new_ver = cur_ver + 1
            self.conn.execute(
                "UPDATE docs SET version=?, data=?, updated_at=? WHERE collection=? AND doc_id=?",
                (new_ver, json.dumps(cur_data, ensure_ascii=False), utc_now(), collection, doc_id),
            )
            self.conn.commit()
        self.notify_sse(collection, doc_id)
        return {"id": doc_id, "version": new_ver, "data": cur_data}, None

    def save_blob(self, raw_bytes, content_type="application/octet-stream", filename="file"):
        if len(raw_bytes) > 20 * 1024 * 1024:
            raise ValueError("blob exceeds 20 MB")
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

    def send_comment(self, anchor, text):
        tid = "thr_" + uuid.uuid4().hex[:12]
        now = utc_now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO threads(thread_id, anchor, text, status, created_at, dispatched, replies) VALUES (?, ?, ?, ?, ?, 0, '[]')",
                (tid, json.dumps(anchor or {}), text, "sent to you — awaiting reply", now),
            )
            self.conn.commit()
        with self.comment_cond:
            self.comment_cond.notify_all()
        return {"thread_id": tid, "created_at": now}

    def wait_comment(self, timeout=300):
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            with self.lock:
                row = self.conn.execute(
                    "SELECT thread_id, anchor, text, status, created_at, replies FROM threads WHERE dispatched=0 ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if row:
                    self.conn.execute("UPDATE threads SET dispatched=1 WHERE thread_id=?", (row[0],))
                    self.conn.commit()
                    return {
                        "event": "comment",
                        "thread_id": row[0],
                        "anchor": json.loads(row[1]),
                        "text": row[2],
                        "status": row[3],
                        "created_at": row[4],
                        "replies": json.loads(row[5]),
                    }
            rem = deadline - time.monotonic()
            if rem <= 0:
                return {"event": "timeout"}
            with self.comment_cond:
                self.comment_cond.wait(timeout=min(rem, 5.0))

    def get_thread(self, thread_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT thread_id, anchor, text, status, created_at, replies FROM threads WHERE thread_id=?",
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
                "UPDATE threads SET replies=?, status=? WHERE thread_id=?",
                (json.dumps(replies, ensure_ascii=False), "replied", thread_id),
            )
            self.conn.commit()
        return {"thread_id": thread_id, "replies": replies, "status": "replied"}


def make_handler(root_dir, store):
    html_path = os.path.join(root_dir, ".kit", "board.html")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass  # keep daemon stdout/stderr quiet

        def _send_json(self, code, obj):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                if not os.path.isfile(html_path):
                    self._send_json(404, {"error": "board.html not found"})
                    return
                html = open(html_path, encoding="utf-8").read()
                tag = '<script src="/_shim.js"></script>'
                if "</head>" in html:
                    html = html.replace("</head>", tag + "\n</head>", 1)
                else:
                    html = tag + "\n" + html
                raw = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_shim.js":
                raw = SHIM_JS.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_api/health":
                self._send_json(200, {"ok": True, "pid": os.getpid(), "root": root_dir})
                return

            if path == "/_api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = queue.Queue(maxsize=100)
                with store.sse_lock:
                    store.sse_clients.add(q)
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=20)
                            self.wfile.write(("event: change\ndata: " + msg + "\n\n").encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with store.sse_lock:
                        store.sse_clients.discard(q)
                return

            if path.startswith("/_blob/"):
                blob_id = path[len("/_blob/"):].strip("/")
                data, meta = store.read_blob(blob_id)
                if data is None:
                    self._send_json(404, {"error": "blob not found"})
                    return
                ctype = meta.get("contentType") or mimetypes.guess_type(meta.get("name", ""))[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=31536000")
                self.end_headers()
                self.wfile.write(data)
                return

            if path == "/_api/comments/status":
                with store.comment_cond:
                    w = sorted(store.watchers)
                self._send_json(200, {"watching": bool(w), "status": "available" if w else "no_session", "watchers": w})
                return

            if path == "/_api/comments/wait":
                timeout = float((qs.get("timeout") or ["300"])[0])
                res = store.wait_comment(timeout=timeout)
                self._send_json(200, res)
                return

            if path.startswith("/_api/comments/threads/"):
                tid = urllib.parse.unquote(path[len("/_api/comments/threads/"):].strip("/"))
                t = store.get_thread(tid)
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
                self._send_json(200, store.get_doc(coll, doc_id))
                return

            if path.startswith("/_api/db/"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):].strip("/").split("/") if x]
                if len(rest) == 1:
                    where = json.loads(qs["where"][0]) if "where" in qs else None
                    order_by = (qs.get("order_by") or [None])[0]
                    direction = (qs.get("dir") or ["asc"])[0]
                    limit = int((qs.get("limit") or ["1000"])[0])
                    cursor = int((qs.get("cursor") or ["0"])[0])
                    self._send_json(200, store.list_docs(rest[0], where=where, order_by=order_by, direction=direction, limit=limit, cursor=cursor))
                    return
                if len(rest) == 2:
                    self._send_json(200, store.get_doc(rest[0], rest[1]))
                    return

            self._send_json(404, {"error": "not found"})

        def do_PUT(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            try:
                body = self._read_json()
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
            res, err = store.set_doc(coll, doc_id, body.get("data", {}), if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            self._send_json(200, res)

        def do_PATCH(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            try:
                body = self._read_json()
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
            res, err = store.update_doc(coll, doc_id, body.get("data", {}), if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            self._send_json(200, res)

        def do_POST(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if path == "/_api/blobs":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                ctype = self.headers.get("Content-Type") or "application/octet-stream"
                fname = urllib.parse.unquote(self.headers.get("X-Filename") or "file")
                try:
                    meta = store.save_blob(raw, content_type=ctype, filename=fname)
                except ValueError as e:
                    self._send_json(413, {"error": str(e), "code": "too_large"})
                    return
                self._send_json(200, meta)
                return

            try:
                body = self._read_json()
            except ValueError:
                self._send_json(400, {"error": "invalid json"})
                return

            if path == "/_api/comments/watch":
                watchers = store.set_watch(str(body.get("watcher") or "clerk"), on=bool(body.get("on", True)))
                self._send_json(200, {"watching": bool(watchers), "watchers": watchers})
                return

            if path == "/_api/comments/send":
                res = store.send_comment(body.get("anchor"), str(body.get("text") or ""))
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

            if path == "/_api/db/batch":
                ops = body.get("writes") if isinstance(body, dict) else body
                results = []
                for w in ops or []:
                    op = w.get("op") or "set"
                    coll, doc_id = w["collection"], w["doc_id"]
                    if op == "set":
                        res, err = store.set_doc(coll, doc_id, w.get("data", {}), if_version=w.get("if_version"))
                    elif op == "update":
                        res, err = store.update_doc(coll, doc_id, w.get("data", {}), if_version=w.get("if_version"))
                    elif op == "str_replace":
                        res, err = store.str_replace(coll, doc_id, w["field"], w["old_str"], w["new_str"], if_version=w.get("if_version"))
                    else:
                        self._send_json(400, {"error": "unknown op: " + str(op)})
                        return
                    if err is not None:
                        self._send_json(409, {"error": "batch_conflict", "doc_id": doc_id, "detail": err})
                        return
                    results.append({"collection": coll, "doc_id": doc_id, "version": res["version"]})
                self._send_json(200, {"ok": True, "results": results})
                return

            if path.startswith("/_api/db/") and path.endswith("/str_replace"):
                rest = [urllib.parse.unquote(x) for x in path[len("/_api/db/"):-len("/str_replace")].strip("/").split("/") if x]
                if len(rest) == 2:
                    res, err = store.str_replace(
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

    return Handler


def seed_from_mirror(root_dir, store, branch=None, force=False):
    """Import board/posts/*.json and board/comments/*.json from git mirror branch into SQLite.

    Reads directly from git objects (origin/<branch>, falling back to <branch>) without modifying
    the working tree. Existing docs in SQLite are preserved unless force=True.
    """
    if not branch:
        cfg_path = os.path.join(root_dir, ".kit", "config.json")
        try:
            branch = json.load(open(cfg_path, encoding="utf-8")).get("mirror", {}).get("branch") or "board-mirror"
        except (OSError, ValueError):
            branch = "board-mirror"

    def git(*a):
        return subprocess.run(["git", "-C", root_dir] + list(a), capture_output=True, text=True, timeout=60)

    git("fetch", "-q", "origin", branch)
    ref = None
    for cand in ("origin/" + branch, branch):
        if git("rev-parse", "--verify", "--quiet", cand).returncode == 0:
            ref = cand
            break
    if not ref:
        return {"ref": None, "imported": 0, "skipped": 0}
    r = git("ls-tree", "-r", "--name-only", ref, "board/posts", "board/comments")
    if r.returncode != 0:
        return {"ref": ref, "imported": 0, "skipped": 0}
    imported = skipped = 0
    for path in r.stdout.splitlines():
        if not path.endswith(".json"):
            continue
        parts = path.split("/")
        if len(parts) != 3 or parts[1] not in ("posts", "comments"):
            continue
        coll, doc_id = parts[1], parts[2][:-5]
        if not force and store.get_doc(coll, doc_id).get("exists"):
            skipped += 1
            continue
        raw = git("show", "%s:%s" % (ref, path))
        if raw.returncode != 0:
            continue
        try:
            data = json.loads(raw.stdout)
        except ValueError:
            continue
        store.set_doc(coll, doc_id, data)
        imported += 1
    return {"ref": ref, "imported": imported, "skipped": skipped}


def start_server(root_dir, host="127.0.0.1", port=8765):
    data_dir = os.path.join(root_dir, ".kit", "board_data")
    store = BoardStore(data_dir)
    srv = ThreadingHTTPServer((host, port), make_handler(root_dir, store))
    srv.daemon_threads = True
    return srv, store


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local board server for agy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=None, help="Project root (default: parent of .kit/)")
    ap.add_argument("--seed-from-mirror", action="store_true", help="Import posts/comments from git board-mirror on startup")
    ap.add_argument("--mirror-branch", default=None, help="Override mirror branch name (default from .kit/config.json)")
    ap.add_argument("--force-seed", action="store_true", help="Overwrite existing local SQLite docs when seeding")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root) if a.root else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srv, store = start_server(root, a.host, a.port)
    if a.seed_from_mirror:
        info = seed_from_mirror(root, store, branch=a.mirror_branch, force=a.force_seed)
        print("seeded from %s: %d imported, %d skipped" % (info["ref"] or "(none)", info["imported"], info["skipped"]), flush=True)
    actual_port = srv.server_address[1]
    print("board server listening on http://%s:%d (root: %s)" % (a.host, actual_port, root), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
