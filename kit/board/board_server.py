#!/usr/bin/env python3
"""Local HTTP/SSE server for the board kit in agy on a computer (zero external dependencies).

    python3 -I .kit/board_server.py [--host 127.0.0.1] [--port 8765] [--root .]

Serves .kit/board.html with an injected window.claude shim (/_shim.js) backed by local SQLite
(.kit/board_data/board.db) and local blobs (.kit/board_data/blobs/), with Server-Sent Events
(/_api/events) for live onSnapshot updates and a local comment-watch queue for the board clerk.
"""
import argparse
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
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

SHIM_JS = r"""(function () {
  "use strict";
  var subs = [];
  var es = null;
  var reconnectTimer = null;
  var embedMode = !!(typeof window !== "undefined" && (window.__KIT_EMBED__ || window.__KIT_TOKEN__));

  function apiUrl(path) {
    if (embedMode && typeof path === "string" && path.charAt(0) === "/") {
      return "." + path;
    }
    return path;
  }

  var OUTBOX_KEY = "kit_offline_outbox";
  function readOutbox() {
    try { return JSON.parse(localStorage.getItem(OUTBOX_KEY) || "[]"); } catch (e) { return []; }
  }
  function writeOutbox(q) {
    try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(q.slice(-100))); } catch (e) {}
    var st = typeof document !== "undefined" && document.getElementById("status");
    if (st && q.length) { st.textContent = "Pending sync (" + q.length + ")"; st.className = "status"; }
  }
  var flushingOutbox = false;
  function flushOutbox() {
    if (flushingOutbox) return;
    var q = readOutbox();
    if (!q.length) return;
    flushingOutbox = true;
    (function next() {
      var cur = readOutbox();
      if (!cur.length) { flushingOutbox = false; refreshAllSubs(); return; }
      var item = cur[0];
      rawApi(item.method, item.path, item.body, item.headers).then(function () {
        var rest = readOutbox().slice(1);
        writeOutbox(rest);
        if (rest.length) next(); else { flushingOutbox = false; refreshAllSubs(); }
      }, function () { flushingOutbox = false; });
    })();
  }
  if (typeof window !== "undefined") {
    window.addEventListener("online", flushOutbox);
  }

  function rawApi(method, path, body, headers) {
    var opts = { method: method, headers: Object.assign({}, headers || {}) };
    if (typeof window !== "undefined" && window.__KIT_TOKEN__) {
      opts.headers["X-Kit-Token"] = window.__KIT_TOKEN__;
      opts.credentials = "omit";
    }
    if (typeof window !== "undefined" && window.__KIT_ACTIVE_REPO__) {
      opts.headers["X-Kit-Repo"] = window.__KIT_ACTIVE_REPO__;
    }
    if (body !== undefined && body !== null) {
      if (body instanceof Blob || body instanceof ArrayBuffer || body instanceof Uint8Array) {
        opts.body = body;
      } else {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
    }
    return fetch(apiUrl(path), opts).then(function (r) {
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

  function api(method, path, body, headers) {
    return rawApi(method, path, body, headers).catch(function (err) {
      var isWrite = (method === "PUT" || method === "PATCH" || method === "POST");
      var isJsonBody = !(body instanceof Blob || body instanceof ArrayBuffer || body instanceof Uint8Array);
      var isQueueable = isWrite && isJsonBody && (path.indexOf("/_api/db/") === 0 || path.indexOf("/_api/comments/send") === 0);
      if (isQueueable && !err.status) {
        var q = readOutbox();
        q.push({ method: method, path: path, body: body, headers: headers || {}, at: Date.now() });
        writeOutbox(q);
        return { ok: true, queued: true, id: (path.split("/").pop() || ""), version: 0 };
      }
      throw err;
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

  var lastSeq = 0;
  var knownBootId = "";
  var reloadPending = false;
  function isTypingNow() {
    if (typeof document === "undefined") return false;
    var ae = document.activeElement;
    if (!ae) return false;
    var tag = (ae.tagName || "").toUpperCase();
    return tag === "TEXTAREA" || tag === "INPUT" || !!ae.isContentEditable;
  }
  function triggerUiReload() {
    if (typeof window === "undefined" || !window.location) return;
    if (isTypingNow()) {
      if (!reloadPending) {
        reloadPending = true;
        var ae = document.activeElement;
        if (ae && ae.addEventListener) {
          ae.addEventListener("blur", function onDone() {
            ae.removeEventListener("blur", onDone);
            if (reloadPending) { reloadPending = false; window.location.reload(); }
          });
        }
      }
      return;
    }
    window.location.reload();
  }
  function refreshAllSubs() {
    subs.forEach(function (s) { refreshSub(s); });
  }
  function connectEvents() {
    if (embedMode) return;
    if (es) return;
    try {
      es = new EventSource("/_api/events");
      es.onopen = function () { flushOutbox(); refreshAllSubs(); };
      es.addEventListener("boot", function (ev) {
        var info = {};
        try { info = JSON.parse(ev.data || "{}"); } catch (e) {}
        if (!info.boot_id) return;
        if (knownBootId && info.boot_id !== knownBootId) {
          triggerUiReload();
          return;
        }
        knownBootId = info.boot_id;
      });
      es.addEventListener("change", function (ev) {
        var info = {};
        try { info = JSON.parse(ev.data || "{}"); } catch (e) {}
        if (info.ui_reload) {
          triggerUiReload();
          return;
        }
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
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { connectEvents(); refreshAllSubs(); }
    });
  }
  if (typeof window !== "undefined") {
    try {
      var savedRepo = localStorage.getItem("kit_active_repo") || "";
      if (savedRepo) window.__KIT_ACTIVE_REPO__ = savedRepo;
    } catch (e) {}
    function initRepoSwitcher() {
      rawApi("GET", "/_api/repos").then(function (info) {
        if (!info || !Array.isArray(info.repos) || info.repos.length <= 1) return;
        var hdr = document.querySelector("header.top");
        var h1 = hdr && hdr.querySelector("h1");
        if (!hdr || !h1 || document.getElementById("kit-repo-wrap")) return;
        var allOpts = info.repos.concat([{ id: "all", name: "All boards", primary: false }]);
        var cur = window.__KIT_ACTIVE_REPO__ || "";
        if (!allOpts.some(function (r) { return (r.primary ? "" : r.id) === cur; })) {
          cur = ""; window.__KIT_ACTIVE_REPO__ = "";
        }
        if (!document.getElementById("kit-repo-css")) {
          var st = document.createElement("style");
          st.id = "kit-repo-css";
          st.textContent =
            ".kit-repo-wrap{position:relative;display:inline-flex;align-items:center;gap:4px;}" +
            ".kit-repo-chev{background:none;border:0;padding:4px;margin-top:2px;color:var(--muted);border-radius:8px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;opacity:.65;transition:opacity .16s ease,transform .16s ease,background .15s ease;}" +
            ".kit-repo-chev:hover{opacity:1;color:var(--ink);background:var(--sunk);}" +
            ".kit-repo-chev[aria-expanded='true'] svg{transform:rotate(180deg);}" +
            ".kit-repo-wrap h1:focus + .kit-repo-chev{opacity:0;pointer-events:none;}" +
            ".kit-repo-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:190px;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow-lg,0 10px 28px rgba(0,0,0,.18));padding:4px;z-index:40;display:flex;flex-direction:column;gap:2px;}" +
            ".kit-repo-menu[hidden]{display:none;}" +
            ".kit-repo-item{background:none;border:0;text-align:left;padding:7px 12px;border-radius:8px;cursor:pointer;font-family:var(--display);font-weight:800;letter-spacing:-0.025em;font-size:1.35rem;line-height:1.2;color:var(--ink);white-space:nowrap;}" +
            ".kit-repo-item:hover{background:var(--sunk);}";
          document.head.appendChild(st);
        }
        var wrap = document.createElement("span");
        wrap.id = "kit-repo-wrap";
        wrap.className = "kit-repo-wrap";
        h1.parentNode.insertBefore(wrap, h1);
        wrap.appendChild(h1);

        var btn = document.createElement("button");
        btn.id = "kit-repo-chev";
        btn.className = "kit-repo-chev";
        btn.type = "button";
        btn.setAttribute("aria-label", "Switch board");
        btn.setAttribute("aria-haspopup", "menu");
        btn.setAttribute("aria-expanded", "false");
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
        wrap.appendChild(btn);

        var menu = document.createElement("div");
        menu.id = "kit-repo-menu";
        menu.className = "kit-repo-menu";
        menu.setAttribute("role", "menu");
        menu.hidden = true;
        wrap.appendChild(menu);

        function repoLabel(r) {
          var n = String(r.name || r.id || "Board");
          return /boards?$/i.test(n) ? n : (n + " board");
        }
        function applyRepoTitle() {
          var active = allOpts.filter(function (r) { return (r.primary ? "" : r.id) === (window.__KIT_ACTIVE_REPO__ || ""); })[0];
          if (active) {
            var lbl = repoLabel(active);
            window.__KIT_REPO_TITLE__ = lbl;
            if (document.activeElement !== h1) {
              h1.textContent = lbl;
              document.title = lbl;
            }
          }
        }
        function renderMenu() {
          menu.textContent = "";
          var activeKey = window.__KIT_ACTIVE_REPO__ || "";
          allOpts.forEach(function (r) {
            var key = r.primary ? "" : r.id;
            if (key === activeKey) return;
            var it = document.createElement("button");
            it.type = "button";
            it.className = "kit-repo-item";
            it.setAttribute("role", "menuitem");
            it.textContent = repoLabel(r);
            it.addEventListener("click", function () {
              window.__KIT_ACTIVE_REPO__ = key;
              try { localStorage.setItem("kit_active_repo", key); } catch (e) {}
              closeMenu();
              applyRepoTitle();
              lastSeq = 0;
              refreshAllSubs();
            });
            menu.appendChild(it);
          });
        }
        function openMenu() {
          renderMenu();
          menu.hidden = false;
          btn.setAttribute("aria-expanded", "true");
        }
        function closeMenu() {
          menu.hidden = true;
          btn.setAttribute("aria-expanded", "false");
        }
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          if (menu.hidden) openMenu(); else closeMenu();
        });
        h1.addEventListener("focus", closeMenu);
        document.addEventListener("click", function (e) { if (!wrap.contains(e.target)) closeMenu(); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });
        applyRepoTitle();
      }, function () {});
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initRepoSwitcher);
    else setTimeout(initRepoSwitcher, 100);
    window.addEventListener("focus", function () { connectEvents(); refreshAllSubs(); });
    setInterval(function () {
      if (typeof document !== "undefined" && document.hidden) return;
      api("GET", "/_api/changes?since=" + lastSeq + "&limit=50").then(function (res) {
        if (!res || typeof res.latest_seq !== "number") return;
        if (lastSeq > 0 && res.latest_seq > lastSeq && (res.changes || []).length) {
          var cols = {};
          res.changes.forEach(function (c) { if (c.collection) cols[c.collection] = true; });
          subs.forEach(function (s) {
            if (s.kind === "coll" && cols[s.coll]) refreshSub(s);
            else if (s.kind === "doc") refreshSub(s);
          });
        }
        lastSeq = res.latest_seq;
      }, function () {});
    }, 5000);
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

  function openExportModal(f) {
    var existing = document.getElementById("kit-export-modal");
    if (existing) existing.remove();
    var ov = document.createElement("div");
    ov.id = "kit-export-modal";
    ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px";
    var card = document.createElement("div");
    card.style.cssText = "background:#fff;color:#111;max-width:680px;width:100%;max-height:85vh;display:flex;flex-direction:column;gap:10px;padding:16px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.3);font-family:system-ui,-apple-system,sans-serif";
    var hdr = document.createElement("div");
    hdr.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px";
    var title = document.createElement("strong");
    title.textContent = "Export: " + (f.filename || "board-export.md");
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.textContent = "Close";
    closeBtn.style.cssText = "padding:4px 10px;border:1px solid #ccc;border-radius:6px;background:#f5f5f5;cursor:pointer";
    closeBtn.addEventListener("click", function () { ov.remove(); });
    hdr.appendChild(title);
    hdr.appendChild(closeBtn);
    var note = document.createElement("div");
    note.style.cssText = "font-size:12px;color:#555";
    note.textContent = "Embedded preview iframe detected: direct file downloads may be blocked. Copy the Markdown below or open raw export in a new tab.";
    var ta = document.createElement("textarea");
    ta.readOnly = true;
    ta.value = f.data || "";
    ta.style.cssText = "width:100%;min-height:240px;flex:1;font-family:ui-monospace,monospace;font-size:12px;padding:8px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box";
    var actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;align-items:center;flex-wrap:wrap";
    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.textContent = "Copy Markdown";
    copyBtn.style.cssText = "padding:6px 12px;border:0;border-radius:6px;background:#111;color:#fff;cursor:pointer";
    copyBtn.addEventListener("click", function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(ta.value).then(function () { copyBtn.textContent = "Copied!"; }, function () { ta.select(); document.execCommand("copy"); copyBtn.textContent = "Copied!"; });
      } else {
        ta.select();
        try { document.execCommand("copy"); copyBtn.textContent = "Copied!"; } catch (e) {}
      }
    });
    var rawLink = document.createElement("a");
    rawLink.href = "/_api/export?all=1&raw=1";
    rawLink.target = "_blank";
    rawLink.rel = "noopener";
    rawLink.textContent = "Open raw export";
    rawLink.style.cssText = "font-size:13px;color:#0b57d0;text-decoration:underline";
    actions.appendChild(copyBtn);
    actions.appendChild(rawLink);
    card.appendChild(hdr);
    card.appendChild(note);
    card.appendChild(ta);
    card.appendChild(actions);
    ov.appendChild(card);
    ov.addEventListener("click", function (e) { if (e.target === ov) ov.remove(); });
    document.body.appendChild(ov);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(ta.value).then(function () { copyBtn.textContent = "Copied to clipboard!"; }).catch(function () {});
    }
  }

  var downloads = {
    save: function (f) {
      try {
        var inIframe = false;
        try { inIframe = window.self !== window.top; } catch (e) { inIframe = true; }
        if (inIframe) {
          openExportModal(f || {});
          return Promise.resolve();
        }
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

  /* UX enhancements: Cmd+K / '/' quick search, Esc to close sheet, 1-tap column switcher, inline git diff */
  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement && document.activeElement.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || tag === "select" || (document.activeElement && document.activeElement.isContentEditable);
    if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") || (!typing && e.key === "/")) {
      var q = document.getElementById("q") || document.querySelector("input[type='search'], input[placeholder*='Search']");
      if (q) {
        e.preventDefault();
        var sw = document.getElementById("kit-search-wrap");
        if (sw) sw.classList.add("open");
        q.focus(); q.select && q.select();
      }
    } else if (e.key === "Escape") {
      var back = document.getElementById("back");
      if (back && back.offsetParent !== null) back.click();
    }
  });

  function enhancePostSheet() {
    var sheet = document.getElementById("sheet");
    if (!sheet || sheet.hidden) return;
    var postId = "";
    try { postId = sessionStorage.getItem("board-open") || ""; } catch (e) {}
    var moveBtn = document.getElementById("move");
    var existingSel = document.getElementById("kit-col-move");
    if (!postId || postId.charAt(0) === "_" || (moveBtn && moveBtn.hidden)) {
      if (existingSel) existingSel.hidden = true;
      return;
    }
    if (existingSel) existingSel.hidden = false;

    /* 1-tap column switcher (fallback when native split-button #kit-move-wrap is absent) */
    var bar = sheet.querySelector(".p-actions, .bar") || sheet.firstElementChild;
    var barRow = (bar && bar.querySelector(".row")) || bar;
    if (barRow && !existingSel && !document.getElementById("kit-move-wrap")) {
      var sel = document.createElement("select");
      sel.id = "kit-col-move";
      sel.title = "Move card to column";
      sel.style.cssText = "background:var(--surface,#1a2335);color:var(--ink,#e6ebf2);border:1px solid var(--line,#2c3850);border-radius:10px;padding:5px 10px;font:inherit;font-size:.82rem;cursor:pointer;flex:none;";
      [["", "Move column…"], ["todo", "For you to do"], ["question", "Waiting on you"], ["info", "News"], ["done", "Done"]].forEach(function (pair) {
        var o = document.createElement("option"); o.value = pair[0]; o.textContent = pair[1]; sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        var col = sel.value; if (!col) return;
        var pid = ""; try { pid = sessionStorage.getItem("board-open") || ""; } catch (e) {}
        if (!pid) return;
        db.doc("posts/" + pid).update({ column: col }).catch(function () {});
        sel.value = "";
      });
      barRow.appendChild(sel);
    }

    /* Inline git diff loader: only shown when the post body names a real commit in this repo */
    var bodyEl = document.getElementById("p-body");
    if (bodyEl && bodyEl.dataset.kitDiffPost !== postId) {
      bodyEl.dataset.kitDiffPost = postId;
      var oldDiff = document.getElementById("kit-git-diff");
      if (oldDiff) oldDiff.remove();
      if (postId && postId !== "_orchestrator") {
        var m = (bodyEl.textContent || "").match(/\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/);
        if (m) {
          var sha = m[0];
          api("GET", "/_api/git/diff?ref=" + encodeURIComponent(sha)).then(function (r) {
            if (!r || (!r.stat && !r.diff) || bodyEl.dataset.kitDiffPost !== postId) return;
            var curOld = document.getElementById("kit-git-diff");
            if (curOld) curOld.remove();
            var wrap = document.createElement("details");
            wrap.id = "kit-git-diff";
            wrap.style.cssText = "margin:14px 0;border:1px solid var(--line,#2c3850);border-radius:10px;padding:8px 12px;background:var(--sunk,#0c121d);";
            var sum = document.createElement("summary");
            sum.textContent = "Inspect git diff (" + sha.slice(0, 7) + ")";
            sum.style.cssText = "cursor:pointer;font-weight:600;color:var(--info,#78b4e3);";
            var pre = document.createElement("pre");
            pre.style.cssText = "overflow:auto;max-height:360px;font-family:var(--mono,monospace);font-size:12px;line-height:1.4;margin:8px 0 0;white-space:pre;";
            pre.textContent = (r.stat ? r.stat + "\n\n" : "") + (r.diff || "");
            wrap.appendChild(sum); wrap.appendChild(pre);
            bodyEl.insertAdjacentElement("afterend", wrap);
          }, function () {});
        }
      }
    }
    /* Link inline .md / § references inside open post & comments to the Repo Docs viewer */
    linkRepoDocs(sheet);
  }

  /* Read-only Repo Docs & Memory Browser + "Comment on this section" -> Ask the Orchestrator */
  function initRepoDocBrowser() {
    var tools = document.querySelector("header.top .tools");
    if (!tools || document.getElementById("kit-docs-btn")) return;
    if (!document.getElementById("kit-docs-css")) {
      var st = document.createElement("style");
      st.id = "kit-docs-css";
      st.textContent =
        ".kit-doc-link{color:var(--ember);text-decoration:underline dotted;cursor:pointer;border-radius:4px;padding:0 2px;}" +
        ".kit-doc-link:hover{background:var(--sunk);}" +
        ".kit-docs-modal{position:fixed;inset:20px;z-index:55;background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 64px rgba(0,0,0,.42);display:grid;grid-template-rows:auto 1fr;overflow:hidden;max-width:1180px;margin:auto;}" +
        ".kit-docs-modal[hidden]{display:none !important;}" +
        ".kit-docs-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--sunk);flex-wrap:wrap;}" +
        ".kit-docs-hdr b{font-family:var(--display);font-size:1.1rem;margin-right:4px;}" +
        ".kit-docs-hdr input{flex:1 1 180px;min-width:120px;font:inherit;font-size:.88rem;padding:5px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);}" +
        ".kit-docs-cats{display:flex;gap:4px;flex-wrap:wrap;}" +
        ".kit-docs-cats button{border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:999px;padding:3px 9px;font-size:.78rem;cursor:pointer;}" +
        ".kit-docs-cats button.on{background:var(--ink);color:var(--bg);border-color:var(--ink);font-weight:700;}" +
        ".kit-docs-body{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:0;}" +
        ".kit-docs-list{border-right:1px solid var(--line);overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:2px;}" +
        ".kit-docs-item{text-align:left;background:none;border:0;border-radius:8px;padding:6px 8px;cursor:pointer;color:var(--ink);font-size:.83rem;display:flex;flex-direction:column;gap:1px;}" +
        ".kit-docs-item:hover,.kit-docs-item.on{background:var(--sunk);}" +
        ".kit-docs-item .dp{font-family:var(--mono);font-size:.76rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
        ".kit-docs-item .dm{font-size:.7rem;color:var(--muted);}" +
        ".kit-docs-view{overflow-y:auto;padding:0 20px 20px;min-width:0;position:relative;}" +
        ".kit-docs-vbar{position:sticky;top:0;z-index:3;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 0;background:var(--surface);border-bottom:1px solid var(--line);margin-bottom:10px;flex-wrap:wrap;}" +
        ".kit-docs-toc{display:flex;flex-wrap:wrap;gap:5px;margin:0 0 12px;padding-bottom:10px;border-bottom:1px solid var(--line);}" +
        ".kit-docs-toc button{border:1px solid var(--line);background:var(--sunk);color:var(--ink);border-radius:999px;padding:2px 8px;font-size:.74rem;cursor:pointer;}" +
        ".kit-docs-toc button:hover{border-color:var(--ember);color:var(--ember);}" +
        ".kit-docs-md{font-size:.94rem;line-height:1.55;overflow-wrap:anywhere;}" +
        ".kit-docs-md h1,.kit-docs-md h2,.kit-docs-md h3,.kit-docs-md h4{scroll-margin-top:58px;}" +
        ".kit-docs-md .kit-sec-hit{background:color-mix(in srgb, var(--ember) 22%, transparent);border-radius:6px;padding:2px 6px;}" +
        "@media (max-width:760px){.kit-docs-modal{inset:8px;}.kit-docs-body{grid-template-columns:1fr;grid-template-rows:190px minmax(0,1fr);}.kit-docs-list{border-right:0;border-bottom:1px solid var(--line);}}";
      document.head.appendChild(st);
    }
    var btn = document.createElement("button");
    btn.id = "kit-docs-btn"; btn.type = "button"; btn.className = "btn small kit-icon-btn";
    btn.title = "Repo docs & memory"; btn.setAttribute("aria-label", "Repo docs & memory");
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
    var askEl = document.getElementById("ask");
    if (askEl && askEl.parentNode === tools) tools.insertBefore(btn, askEl); else tools.appendChild(btn);

    var modal = document.createElement("section");
    modal.id = "kit-docs-modal"; modal.className = "kit-docs-modal"; modal.hidden = true;
    modal.setAttribute("role", "dialog"); modal.setAttribute("aria-label", "Repo docs & memory");
    modal.innerHTML =
      '<div class="kit-docs-hdr"><b>Repo Docs</b><input type="search" id="kit-docs-q" placeholder="Filter .md files..."><span class="kit-docs-cats" id="kit-docs-cats"></span><button type="button" class="btn small" id="kit-docs-close">Close</button></div>' +
      '<div class="kit-docs-body"><div class="kit-docs-list" id="kit-docs-list"></div><div class="kit-docs-view" id="kit-docs-view"><div class="note" style="padding-top:14px">Select a document to read it directly from the repository.</div></div></div>';
    document.body.appendChild(modal);

    var allDocs = [], activeCat = "all", activePath = "", activeHeadings = [];
    var cats = [["all", "All"], ["memory", "Memory"], ["docs", "Docs"], ["prompts", "Prompts"], ["root", "Root"]];
    var catsEl = document.getElementById("kit-docs-cats");
    var qEl = document.getElementById("kit-docs-q");
    var listEl = document.getElementById("kit-docs-list");
    var viewEl = document.getElementById("kit-docs-view");

    function renderCats() {
      catsEl.textContent = "";
      cats.forEach(function (pair) {
        var b = document.createElement("button"); b.type = "button"; b.textContent = pair[1];
        if (pair[0] === activeCat) b.className = "on";
        b.addEventListener("click", function () { activeCat = pair[0]; renderCats(); renderList(); });
        catsEl.appendChild(b);
      });
    }
    function fmtKb(n) { return n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B"; }
    function visibleHeadingText() {
      if (!activeHeadings.length) return "";
      var vTop = viewEl.getBoundingClientRect().top + 72;
      var best = "";
      for (var i = 0; i < activeHeadings.length; i++) {
        var el = document.getElementById(activeHeadings[i].id);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= vTop) best = activeHeadings[i].text;
        else break;
      }
      return best || (activeHeadings[0] && activeHeadings[0].text) || "";
    }
    function renderList() {
      var ql = (qEl.value || "").trim().toLowerCase();
      listEl.textContent = "";
      var shown = allDocs.filter(function (d) {
        if (activeCat !== "all" && d.category !== activeCat) return false;
        return !ql || d.path.toLowerCase().indexOf(ql) >= 0;
      });
      if (!shown.length) { listEl.innerHTML = '<div class="note">No matching Markdown files.</div>'; return; }
      shown.slice(0, 300).forEach(function (d) {
        var it = document.createElement("button"); it.type = "button";
        it.className = "kit-docs-item" + (d.path === activePath ? " on" : "");
        it.innerHTML = '<span class="dp"></span><span class="dm"></span>';
        it.querySelector(".dp").textContent = (d.pinned ? "📌 " : "") + d.path;
        it.querySelector(".dm").textContent = (d.pinned ? "pinned · " : "") + d.category + " · " + fmtKb(d.size);
        it.addEventListener("click", function () { openRepoDoc(d.path, ""); });
        listEl.appendChild(it);
      });
    }
    function openRepoDoc(relPath, section) {
      modal.hidden = false;
      activePath = relPath || "";
      activeHeadings = [];
      if (!allDocs.length) {
        api("GET", "/_api/repo-docs").then(function (r) { allDocs = (r && r.docs) || []; renderCats(); renderList(); }, function () {});
      } else { renderList(); }
      if (!relPath) return;
      viewEl.innerHTML = '<div class="note" style="padding-top:14px">Loading ' + relPath + '...</div>';
      var u = "/_api/repo-doc?path=" + encodeURIComponent(relPath) + (section ? "&section=" + encodeURIComponent(section) : "");
      api("GET", u).then(function (res) {
        viewEl.textContent = "";
        activeHeadings = res.headings || [];
        var bar = document.createElement("div"); bar.className = "kit-docs-vbar";
        var left = document.createElement("span"); left.style.cssText = "display:inline-flex;align-items:baseline;gap:8px;min-width:0;";
        var pt = document.createElement("code"); pt.textContent = res.path; pt.style.fontWeight = "700";
        var mt = document.createElement("span"); mt.className = "note"; mt.textContent = fmtKb(res.size);
        left.appendChild(pt); left.appendChild(mt); bar.appendChild(left);
        var cBtn = document.createElement("button"); cBtn.type = "button"; cBtn.className = "btn ember small";
        cBtn.textContent = "Comment on this section";
        cBtn.title = "Start an orchestrator discussion anchored to the currently visible section";
        cBtn.addEventListener("click", function () {
          var secTxt = visibleHeadingText();
          var selTxt = "";
          try {
            var sel = window.getSelection ? window.getSelection() : null;
            if (sel && sel.toString().trim() && viewEl.contains(sel.anchorNode)) {
              selTxt = sel.toString().trim().slice(0, 260);
            }
          } catch (e) {}
          var prefill = "Re `" + res.path + (secTxt ? " § " + secTxt : "") + "`:\n";
          if (selTxt) prefill += "> " + selTxt.replace(/\n+/g, "\n> ") + "\n\n";
          modal.hidden = true;
          if (typeof window._kitOpenOrchNew === "function") {
            window._kitOpenOrchNew(prefill);
          }
        });
        bar.appendChild(cBtn);
        viewEl.appendChild(bar);
        var mdBox = document.createElement("div"); mdBox.className = "kit-docs-md body";
        if (typeof window._kitRenderBody === "function") {
          mdBox.innerHTML = window._kitRenderBody(res.content || "");
        } else {
          var pre = document.createElement("pre"); pre.style.whiteSpace = "pre-wrap"; pre.textContent = res.content || ""; mdBox.appendChild(pre);
        }
        var hNodes = mdBox.querySelectorAll("h1,h2,h3,h4");
        for (var i = 0; i < hNodes.length && i < activeHeadings.length; i++) { hNodes[i].id = activeHeadings[i].id; }
        if (activeHeadings.length > 1) {
          var toc = document.createElement("div"); toc.className = "kit-docs-toc";
          activeHeadings.slice(0, 30).forEach(function (h) {
            var tb = document.createElement("button"); tb.type = "button"; tb.textContent = h.text.slice(0, 42);
            tb.addEventListener("click", function () {
              var el = document.getElementById(h.id);
              if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
            });
            toc.appendChild(tb);
          });
          viewEl.appendChild(toc);
        }
        viewEl.appendChild(mdBox);
        if (res.match_heading) {
          var target = document.getElementById(res.match_heading);
          if (target) {
            target.classList.add("kit-sec-hit");
            setTimeout(function () { target.scrollIntoView({ behavior: "smooth", block: "start" }); }, 30);
          }
        }
      }, function (e) {
        viewEl.innerHTML = '<div class="note" style="padding-top:14px">Could not load ' + relPath + ' (' + (e && e.message || "error") + ').</div>';
      });
    }
    window._kitOpenRepoDoc = openRepoDoc;
    btn.addEventListener("click", function () {
      modal.hidden = false;
      api("GET", "/_api/repo-docs").then(function (r) { allDocs = (r && r.docs) || []; renderCats(); renderList(); }, function () {});
      qEl.focus();
    });
    document.getElementById("kit-docs-close").addEventListener("click", function () { modal.hidden = true; });
    qEl.addEventListener("input", renderList);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) modal.hidden = true; });
    renderCats();
  }

  function linkRepoDocs(root) {
    if (!root || typeof window._kitOpenRepoDoc !== "function") return;
    var codes = root.querySelectorAll("#p-body code, .cmt .txt code");
    var mdRe = /^(?:docs\/|\.story\/|\.board\/|\.kit\/)?[A-Za-z0-9_./-]+\.md$/;
    for (var i = 0; i < codes.length; i++) {
      var c = codes[i];
      if (c.dataset.kitDocLink) continue;
      var raw = (c.textContent || "").trim();
      var sec = "";
      var sm = raw.match(/^([A-Za-z0-9_./-]+\.md)\s+§\s*([A-Za-z0-9._-]+)$/);
      if (sm) { raw = sm[1]; sec = sm[2]; }
      if (!mdRe.test(raw)) continue;
      c.dataset.kitDocLink = "1";
      c.classList.add("kit-doc-link");
      c.title = "Open " + raw + (sec ? " § " + sec : "") + " from repo";
      (function (fp, sc) {
        c.addEventListener("click", function (ev) { ev.stopPropagation(); window._kitOpenRepoDoc(fp, sc); });
      })(raw, sec);
    }
  }

  if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    var _kitMoTimer = null;
    var _kitMo = new MutationObserver(function () {
      if (_kitMoTimer) return;
      _kitMoTimer = setTimeout(function () { _kitMoTimer = null; enhancePostSheet(); }, 50);
    });
    var _attachMo = function () {
      initRepoDocBrowser();
      var target = document.getElementById("sheet") || document.body || document.documentElement;
      if (target) {
        _kitMo.observe(target, { attributes: true, childList: true, subtree: true, attributeFilter: ["hidden"] });
        enhancePostSheet();
      }
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _attachMo);
    else _attachMo();
  } else {
    setInterval(enhancePostSheet, 600);
  }
})();
"""


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
        try:
            self.conn.execute("ALTER TABLE threads ADD COLUMN dispatched_at REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS changes ("
            "  seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  collection TEXT NOT NULL,"
            "  doc_id TEXT NOT NULL,"
            "  op TEXT NOT NULL,"
            "  version INTEGER NOT NULL,"
            "  at TEXT NOT NULL"
            ")"
        )
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
                    subprocess.run(
                        [sys.executable, "-I", kit_py, "--mirror", m, "push"],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
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

    def create_share(self, post_id, ttl_hours=24.0, allow_write=True, files=None, max_submissions=5, repo=""):
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
                "INSERT INTO shares(token, post_id, repo, allow_write, files_json, expires_at, created_at, revoked, submissions, max_submissions) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
                (tok, post_id, str(repo or ""), 1 if allow_write else 0, json.dumps(safe_files, ensure_ascii=False), exp, now, max(1, int(max_submissions or 5))),
            )
            self.conn.commit()
        return {"token": tok, "post_id": post_id, "repo": str(repo or ""), "path": "/_share/" + tok, "allow_write": bool(allow_write), "expires_at": exp, "created_at": now}

    def get_share(self, tok):
        with self.lock:
            row = self.conn.execute(
                "SELECT token, post_id, repo, allow_write, files_json, expires_at, created_at, revoked, submissions, max_submissions FROM shares WHERE token=?",
                (tok,),
            ).fetchone()
        if not row:
            return None
        return {
            "token": row[0], "post_id": row[1], "repo": row[2] or "", "allow_write": bool(row[3]),
            "files": json.loads(row[4] or "[]"), "expires_at": row[5], "created_at": row[6],
            "revoked": bool(row[7]), "submissions": int(row[8]), "max_submissions": int(row[9]),
        }

    def list_shares(self, post_id=None):
        with self.lock:
            if post_id:
                rows = self.conn.execute(
                    "SELECT token, post_id, allow_write, expires_at, created_at, revoked, submissions, max_submissions FROM shares WHERE post_id=? ORDER BY created_at DESC",
                    (post_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT token, post_id, allow_write, expires_at, created_at, revoked, submissions, max_submissions FROM shares ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        return [
            {"token": r[0], "post_id": r[1], "path": "/_share/" + r[0], "allow_write": bool(r[2]),
             "expires_at": r[3], "created_at": r[4], "revoked": bool(r[5]), "submissions": int(r[6]), "max_submissions": int(r[7])}
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
            "INSERT INTO changes(collection, doc_id, op, version, at) VALUES (?, ?, ?, ?, ?)",
            (collection, doc_id, op, version, now),
        )
        seq = cur.lastrowid or 0
        if self.MAX_CHANGES_LOG and seq > self.MAX_CHANGES_LOG and (seq % 100 == 0 or self.MAX_CHANGES_LOG < 200):
            self.conn.execute("DELETE FROM changes WHERE seq <= ?", (seq - self.MAX_CHANGES_LOG,))

    def list_changes(self, since=0, limit=1000):
        with self.lock:
            rows = self.conn.execute(
                "SELECT seq, collection, doc_id, op, version, at FROM changes WHERE seq>? ORDER BY seq ASC LIMIT ?",
                (int(since or 0), max(1, int(limit or 1000))),
            ).fetchall()
            latest = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]
        items = [{"seq": r[0], "collection": r[1], "doc_id": r[2], "op": r[3], "version": r[4], "at": r[5]} for r in rows]
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

    def wait_comment(self, timeout=300, lease_sec=None):
        lease = float(self.COMMENT_LEASE_SEC if lease_sec is None else lease_sec)
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            now_ts = time.time()
            with self.lock:
                row = self.conn.execute(
                    "SELECT thread_id, anchor, text, status, created_at, replies FROM threads "
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
                "UPDATE threads SET replies=?, status=?, dispatched=1, dispatched_at=0 WHERE thread_id=?",
                (json.dumps(replies, ensure_ascii=False), "replied", thread_id),
            )
            self.conn.commit()
        return {"thread_id": thread_id, "replies": replies, "status": "replied"}


def send_push(notify_url, title, body):
    """Fire-and-forget phone push notification (works with free ntfy.sh or any webhook)."""
    if not notify_url:
        return
    def _go():
        try:
            r = urllib.request.Request(
                notify_url,
                data=body.encode("utf-8"),
                headers={"Title": title[:120], "Content-Type": "text/plain; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(r, timeout=5):
                pass
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


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


def _classify_repo_doc(rel):
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


def list_repo_docs(root_dir, query="", limit=1000):
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
            if not fn.lower().endswith(".md"):
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
                "category": _classify_repo_doc(rel),
                "pinned": rel in pinned_set,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    cat_order = {"memory": 0, "root": 1, "docs": 2, "prompts": 3, "kit": 4, "other": 5}
    items.sort(key=lambda x: (0 if x.get("pinned") else 1, cat_order.get(x["category"], 9), x["path"].lower()))
    return {"docs": items, "total": len(items)}


def read_repo_doc(root_dir, rel_path, section=""):
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/") or not rel.lower().endswith(".md"):
        return None, "invalid_path"
    root_real = os.path.realpath(root_dir)
    target = os.path.realpath(os.path.join(root_real, rel))
    if not (target.startswith(root_real + os.sep) and os.path.isfile(target)):
        return None, "not_found"
    try:
        st = os.stat(target)
        if st.st_size > MAX_REPO_DOC_BYTES:
            return None, "too_large"
        with open(target, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None, "read_error"
    headings = []
    sec_q = (section or "").strip().lstrip("§").strip().lower()
    match_h = ""
    for i, line in enumerate(content.splitlines()):
        m = re.match(r"^(#{1,4})\s+(.+?)\s*#*$", line)
        if not m:
            continue
        lvl = len(m.group(1))
        txt = m.group(2).strip()
        hid = "h-%d-%s" % (i, re.sub(r"[^a-z0-9]+", "-", txt.lower()).strip("-")[:48] or "sec")
        headings.append({"level": lvl, "text": txt, "id": hid})
        if sec_q and not match_h:
            tl = txt.lower()
            if sec_q == tl or re.search(r"(?:^|[\s§(])" + re.escape(sec_q) + r"(?:\b|[\s.:)])", tl) or sec_q in tl:
                match_h = hid
    return {
        "path": os.path.relpath(target, root_real).replace("\\", "/"),
        "category": _classify_repo_doc(rel),
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "headings": headings,
        "match_heading": match_h,
        "content": content,
    }, None


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_INLINE_BLOB_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
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


class PayloadTooLarge(Exception):
    pass


def make_handler(root_dir, store, token=None, notify_url=None, embed_mode=False, extra_stores=None):
    html_path = os.path.join(root_dir, ".kit", "board.html")
    project_name = "Board"
    try:
        project_name = json.load(open(os.path.join(root_dir, ".kit", "config.json"), encoding="utf-8")).get("project") or "Board"
    except (OSError, ValueError):
        pass
    primary_repo_id = re.sub(r"[^a-z0-9_-]+", "-", project_name.lower()).strip("-") or "primary"
    stores_map = {primary_repo_id: store}
    roots_map = {primary_repo_id: root_dir}
    repo_meta = [{"id": primary_repo_id, "name": project_name, "primary": True}]
    for rid, rinfo in (extra_stores or {}).items():
        if rid and rid != primary_repo_id and isinstance(rinfo, dict) and rinfo.get("store"):
            stores_map[rid] = rinfo["store"]
            if rinfo.get("root"):
                roots_map[rid] = rinfo["root"]
            repo_meta.append({"id": rid, "name": rinfo.get("name") or rid, "primary": False})

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
        inline_shim = SHIM_JS.replace("</script>", "<\\/script>")
        flag_js = "window.__KIT_EMBED__=true;\n" if inject_embed_flag else ""
        embed_tags = '<meta name="theme-color" content="#0b111e">\n<script>\n' + flag_js + inline_shim + "\n</script>"
        head_inject = '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<style>html,body{margin:0;padding:0;-webkit-font-smoothing:antialiased;}</style>\n' + embed_tags
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
        "img-src 'self' data: blob: /_blob/; "
        "media-src 'self' /_blob/; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'"
    )

    unlock_fails = {}
    unlock_lock = threading.Lock()
    MAX_UNLOCK_FAILS = 10
    UNLOCK_WINDOW_SEC = 600.0

    def _render_share_brief(sh, base_origin):
        tstore = stores_map.get(sh.get("repo") or "") or store
        post_doc = tstore.get_doc("posts", sh["post_id"])
        if not post_doc.get("exists"):
            return None
        p = post_doc["data"] or {}
        cmts = [c for c in tstore.list_docs("comments")["docs"] if (c["data"] or {}).get("post_id") == sh["post_id"]]
        cmts.sort(key=lambda c: str((c["data"] or {}).get("created_at") or ""))
        lines = [
            "# External Task Brief — %s" % (p.get("title") or sh["post_id"]),
            "",
            "- **Card ID:** `%s`" % sh["post_id"],
            "- **Column:** `%s`" % (p.get("column") or "info"),
            "- **Train / Part:** `%s` / `%s`" % (p.get("train") or "-", p.get("part") or "-"),
            "- **Expires:** `%s`" % sh["expires_at"],
            "",
            "## Task & Context",
            "",
            str(p.get("body") or "").strip(),
        ]
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
                "### Option A — Direct write-back (if you have outbound HTTP)",
                "POST JSON to `%s`:" % submit_url,
                "```json",
                json.dumps({
                    "from": "lmarena",
                    "mode": "comment",
                    "text": "<your concise Markdown findings here>",
                }, indent=2),
                "```",
                "*(Set `\"mode\": \"child_post\"` and `\"title\": \"...\"` to create a linked result card instead of a comment.)*",
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

        def _is_all_repos(self, parsed):
            qs = urllib.parse.parse_qs(parsed.query)
            rid = (self.headers.get("X-Kit-Repo") or (qs.get("repo") or [""])[0] or "").strip().lower()
            return rid == "all" and len(stores_map) > 1

        def _store_for_doc(self, parsed, coll, doc_id):
            if self._is_all_repos(parsed):
                for rid, st in stores_map.items():
                    if st.get_doc(coll, doc_id).get("exists"):
                        return st, rid
                return store, primary_repo_id
            rid = self._repo_id(parsed)
            return (stores_map.get(rid) or store), rid

        def _repo_id(self, parsed):
            qs = urllib.parse.parse_qs(parsed.query)
            rid = (self.headers.get("X-Kit-Repo") or (qs.get("repo") or [""])[0] or "").strip()
            return rid if rid in stores_map else primary_repo_id

        def _store(self, parsed):
            return stores_map.get(self._repo_id(parsed)) or store

        def _repo_root(self, parsed):
            return roots_map.get(self._repo_id(parsed)) or root_dir

        def _send_security_headers(self, is_html=False):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if is_html:
                self.send_header("Content-Security-Policy", HTML_CSP)

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
            if path in ("/_shim.js", "/_manifest.webmanifest", "/_icon.svg", "/_sw.js"):
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

        def _check_auth(self, parsed):
            if not token:
                return True, False
            if not embed_mode:
                qs = urllib.parse.parse_qs(parsed.query)
                qtok = (qs.get("token") or [""])[0]
                if qtok and hmac.compare_digest(qtok, token):
                    return True, True
            hdr = self.headers.get("X-Kit-Token") or ""
            if hdr and hmac.compare_digest(hdr, token):
                return True, False
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), token):
                return True, False
            if not embed_mode:
                raw_cookie = self.headers.get("Cookie") or ""
                if raw_cookie:
                    c = SimpleCookie()
                    try:
                        c.load(raw_cookie)
                        if "kit_token" in c and hmac.compare_digest(c["kit_token"].value, token):
                            return True, False
                    except Exception:
                        pass
            return False, False

        def _require_auth(self, parsed):
            ok, set_cookie = self._check_auth(parsed)
            if ok:
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

            if path.startswith("/_share/") and not path.endswith("/submit"):
                stok = urllib.parse.unquote(path[len("/_share/"):]).strip("/")
                sh = store.get_share(stok)
                if not sh:
                    self._send_json(404, {"error": "share_not_found"})
                    return
                if sh["revoked"] or sh["expires_at"] < utc_now():
                    self._send_json(410, {"error": "share_expired_or_revoked"})
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

            set_cookie = self._require_auth(parsed)
            if set_cookie is None:
                return
            if set_cookie and path in ("/", "/index.html"):
                proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
                cookie = "kit_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % token
                if proto == "https":
                    cookie += "; Secure"
                accept = (self.headers.get("Accept") or "").lower()
                if "text/html" in accept:
                    # Browser / PWA: persist token in localStorage AND 1-year cookie, then strip from URL
                    body = (
                        "<!doctype html><html><head><meta charset=utf-8><meta name=theme-color content=#0b111e></head>"
                        "<body style='background:#0b111e;color:#e6ebf2;font-family:system-ui;padding:24px'>"
                        "<script>try{localStorage.setItem('kit_board_token',%s);}catch(e){}location.replace('/');</script>"
                        "</body></html>" % json.dumps(token)
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
                head_inject = '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<style>html,body{margin:0;padding:0;-webkit-font-smoothing:antialiased;}</style>\n' + pwa_tags
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
                raw = SHIM_JS.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return

            if path == "/_api/repos":
                self._send_json(200, {"primary": primary_repo_id, "repos": repo_meta})
                return

            if path == "/_api/repo-docs":
                qstr = (qs.get("q") or [""])[0]
                self._send_json(200, list_repo_docs(self._repo_root(parsed), query=qstr))
                return

            if path == "/_api/repo-doc":
                rpath = (qs.get("path") or [""])[0]
                sec = (qs.get("section") or [""])[0]
                doc_res, derr = read_repo_doc(self._repo_root(parsed), rpath, section=sec)
                if derr:
                    code = 404 if derr == "not_found" else (413 if derr == "too_large" else 400)
                    self._send_json(code, {"error": derr})
                    return
                self._send_json(200, doc_res)
                return

            if path == "/_api/changes":
                since = int((qs.get("since") or ["0"])[0])
                limit = int((qs.get("limit") or ["1000"])[0])
                self._send_json(200, self._store(parsed).list_changes(since=since, limit=limit))
                return

            if path == "/_api/git/diff":
                ref = (qs.get("ref") or [""])[0].strip()
                res, err = read_git_diff(root_dir, ref)
                if err:
                    self._send_json(400 if err == "invalid_ref" else 404, {"error": err})
                    return
                self._send_json(200, res)
                return

            if path == "/_api/db/comments/counts":
                self._send_json(200, store.comment_counts())
                return

            if path == "/_api/links":
                tr = (qs.get("train") or [None])[0]
                self._send_json(200, store.list_links(train=tr))
                return

            if path == "/_api/shares":
                pid = (qs.get("post") or [None])[0]
                self._send_json(200, {"shares": store.list_shares(post_id=pid)})
                return

            if path == "/_api/export":
                pid = (qs.get("post") or [""])[0].strip()
                fmt = (qs.get("format") or ["md"])[0].strip().lower()
                raw_view = (qs.get("raw") or ["0"])[0].strip().lower() in ("1", "true", "yes")
                if fmt == "json":
                    data_obj, fname = export_json_bundle(store, post_id=pid or None)
                    if data_obj is None:
                        self._send_json(404, {"error": "not_found"})
                        return
                    body = json.dumps(data_obj, ensure_ascii=False, indent=2).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif fmt == "rss":
                    rss_text, fname = export_rss(store, project_name=project_name)
                    body = rss_text.encode("utf-8")
                    ctype = "application/rss+xml; charset=utf-8"
                else:
                    md_text, fname = export_markdown(store, post_id=pid or None)
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
                with store.sse_lock:
                    store.sse_clients.add(q)
                try:
                    self.wfile.write(("event: boot\ndata: " + json.dumps({"boot_id": SERVER_BOOT_ID}) + "\n\n").encode("utf-8"))
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
                raw_ctype = (meta.get("contentType") or mimetypes.guess_type(meta.get("name", ""))[0] or "application/octet-stream").split(";")[0].strip().lower()
                self.send_response(200)
                self.send_header("X-Content-Type-Options", "nosniff")
                if raw_ctype in SAFE_INLINE_BLOB_TYPES:
                    self.send_header("Content-Type", raw_ctype)
                    self.send_header("Cache-Control", "public, max-age=31536000")
                else:
                    self.send_header("Content-Type", "application/octet-stream")
                    fname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(meta.get("name") or blob_id)) or blob_id
                    self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
                    self.send_header("Cache-Control", "private, no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
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
                    for rid, st in stores_map.items():
                        res = st.list_docs(rest[0], where=where, order_by=order_by, direction=direction)
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
                    self._send_json(200, cur_store.list_docs(rest[0], where=where, order_by=order_by, direction=direction, limit=limit, cursor=cursor))
                    return
                if len(rest) == 2:
                    tstore, _ = self._store_for_doc(parsed, rest[0], rest[1])
                    self._send_json(200, tstore.get_doc(rest[0], rest[1]))
                    return

            self._send_json(404, {"error": "not found"})

        def do_PUT(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed) is None:
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
            tstore, _ = self._store_for_doc(parsed, coll, doc_id)
            if coll == "comments" and self._is_all_repos(parsed) and (body.get("data") or {}).get("post_id"):
                tstore, _ = self._store_for_doc(parsed, "posts", str(body["data"]["post_id"]))
            res, err = tstore.set_doc(coll, doc_id, body.get("data", {}), if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            if notify_url:
                d = body.get("data") or {}
                if coll == "posts" and d.get("column") in ("todo", "question") and not doc_id.startswith("_"):
                    send_push(notify_url, "Board (%s): %s" % (d.get("column"), d.get("title") or doc_id), str(d.get("body") or "")[:240])
                elif coll == "comments" and d.get("from") not in (None, "", "owner", "viewer"):
                    send_push(notify_url, "%s replied on %s" % (d.get("from"), d.get("post_id") or "board"), str(d.get("text") or "")[:240])
            self._send_json(200, res)

        def do_PATCH(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed) is None:
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
            tstore, _ = self._store_for_doc(parsed, coll, doc_id)
            res, err = tstore.update_doc(coll, doc_id, body.get("data", {}), if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            self._send_json(200, res)

        def do_DELETE(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed) is None:
                return
            path = parsed.path
            if path.startswith("/_api/shares/"):
                stok = urllib.parse.unquote(path[len("/_api/shares/"):]).strip("/")
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
                sender = re.sub(r"[^A-Za-z0-9._-]", "", str(body.get("from") or "external-ai"))[:32] or "external-ai"
                mode = str(body.get("mode") or "comment").strip().lower()
                now = utc_now()
                target_repo = sh.get("repo") or primary_repo_id
                tstore = stores_map.get(target_repo) or store
                parent_doc = tstore.get_doc("posts", sh["post_id"])
                pdata = parent_doc.get("data") or {}
                if mode == "child_post":
                    new_id = "%s-%s-%s" % (sh["post_id"][:40], sender[:12], secrets.token_hex(3))
                    title = str(body.get("title") or ((pdata.get("title") or sh["post_id"]) + " — " + sender))[:180]
                    res, _ = tstore.set_doc("posts", new_id, {
                        "title": title,
                        "body": text,
                        "column": "info",
                        "part": sender,
                        "runner": sender,
                        "parent": sh["post_id"],
                        "train": pdata.get("train") or "",
                        "priority": 2,
                        "created_at": now,
                    })
                    store.increment_share_submissions(stok)
                    self._send_json(200, {"ok": True, "mode": "child_post", "repo": target_repo, "post_id": new_id, "version": res["version"]})
                    return
                cid = "ext-%s-%s" % (sender[:10], secrets.token_hex(4))
                res, _ = tstore.set_doc("comments", cid, {
                    "post_id": sh["post_id"],
                    "from": sender,
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
                if token and not hmac.compare_digest(cand, token):
                    with unlock_lock:
                        unlock_fails.setdefault(ip, []).append(now_ts)
                    self._send_json(401, {"error": "unauthorized", "code": "unauthorized"})
                    return
                with unlock_lock:
                    unlock_fails.pop(ip, None)
                raw = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                if token and not embed_mode:
                    proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
                    cookie = "kit_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % token
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
            if self._require_auth(parsed) is None:
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
                try:
                    meta = store.save_blob(raw, content_type=ctype, filename=fname)
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

            if path == "/_api/shares":
                pid = str(body.get("post_id") or "").strip()
                target_repo = str(body.get("repo") or "").strip()
                if target_repo and target_repo in stores_map:
                    tstore = stores_map[target_repo]
                else:
                    tstore, target_repo = self._store_for_doc(parsed, "posts", pid)
                if not pid or not tstore.get_doc("posts", pid).get("exists"):
                    self._send_json(404, {"error": "post_not_found"})
                    return
                res = store.create_share(
                    pid,
                    ttl_hours=body.get("ttl_hours", 24),
                    allow_write=bool(body.get("allow_write", True)),
                    files=body.get("files") or [],
                    max_submissions=body.get("max_submissions", 5),
                    repo=target_repo,
                )
                self._send_json(200, res)
                return

            if path == "/_api/comments/watch":
                watchers = store.set_watch(str(body.get("watcher") or "clerk"), on=bool(body.get("on", True)))
                self._send_json(200, {"watching": bool(watchers), "watchers": watchers})
                return

            if path == "/_api/comments/send":
                res = store.send_comment(body.get("anchor"), str(body.get("text") or ""))
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

            if path == "/_api/db/batch":
                ops = body.get("writes") if isinstance(body, dict) else body
                results, err = store.batch_ops(ops or [])
                if err is not None:
                    kind, doc_id, detail = err
                    if kind == "unknown_op":
                        self._send_json(400, {"error": "unknown op: " + str(detail)})
                    else:
                        self._send_json(409, {"error": "batch_conflict", "doc_id": doc_id, "detail": detail})
                    return
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
            name = member.name.lstrip("./")
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


def start_server(root_dir, host="127.0.0.1", port=8765, token=None, notify_url=None, embed_mode=False, preview=False, default_watcher=None, auto_sync_interval=0.0, auto_sync_push=False, attach=None):
    if host.strip().lower() not in LOOPBACK_HOSTS and not token and not preview:
        raise ValueError("refusing to bind to non-loopback host %r without --token / KIT_BOARD_TOKEN" % host)
    data_dir = os.path.join(root_dir, ".kit", "board_data")
    store = BoardStore(data_dir)
    if auto_sync_interval and float(auto_sync_interval) > 0:
        store.enable_auto_sync(root_dir, interval_sec=float(auto_sync_interval), push=auto_sync_push)
    extra_stores = {}
    attach_specs = dict(attach or {})
    cfg_path = os.path.join(root_dir, ".kit", "config.json")
    try:
        cfg_data = json.load(open(cfg_path, encoding="utf-8"))
    except (OSError, ValueError):
        cfg_data = {}
    for item in (cfg_data.get("portfolio") or []):
        if isinstance(item, dict) and item.get("path"):
            aname = str(item.get("name") or os.path.basename(os.path.abspath(item["path"])))
            aid = re.sub(r"[^a-z0-9_-]+", "-", aname.lower()).strip("-")
            if aid and aid not in attach_specs:
                attach_specs[aid] = item["path"]
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
                ast.enable_auto_sync(ap_abs, interval_sec=float(auto_sync_interval), push=auto_sync_push)
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
        notify_url = cfg_data.get("kit", {}).get("notify_url") or None
    srv = ThreadingHTTPServer((host, port), make_handler(root_dir, store, token=token, notify_url=notify_url, embed_mode=embed_mode, extra_stores=extra_stores))
    srv.daemon_threads = True
    srv._extra_stores = extra_stores
    return srv, store


def _file_sig(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _ensure_funnel_async(port, root_dir):
    """Best-effort, non-blocking Tailscale Funnel refresh on startup/reload."""
    import shutil
    import tempfile
    if os.environ.get("KIT_NO_FUNNEL") == "1":
        return
    if os.path.abspath(root_dir).startswith(os.path.abspath(tempfile.gettempdir()) + os.sep):
        return
    if not shutil.which("tailscale"):
        return
    cfg_path = os.path.join(root_dir, ".kit", "config.json")
    try:
        if json.load(open(cfg_path, encoding="utf-8")).get("kit", {}).get("funnel") is False:
            return
    except (OSError, ValueError):
        pass

    def _run():
        try:
            subprocess.run(
                ["tailscale", "funnel", "--bg", str(port)],
                capture_output=True, text=True, timeout=15,
            )
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
        watched = [script_path, cfg_path, html_watch]
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
                if changed == [html_watch]:
                    sigs[html_watch] = new_sigs[html_watch]
                    print("board_server: detected UI update (board.html), pushing live browser reload", flush=True)
                    store.notify_ui_reload("board.html")
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
