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
      var urlRepo = new URLSearchParams(location.search).get("repo") || "";
      if (urlRepo) {
        window.__KIT_ACTIVE_REPO__ = urlRepo;
        localStorage.setItem("kit_active_repo", urlRepo);
      } else {
        var savedRepo = localStorage.getItem("kit_active_repo") || "";
        if (savedRepo) window.__KIT_ACTIVE_REPO__ = savedRepo;
      }
    } catch (e) {}
    function initRepoSwitcher() {
      rawApi("GET", "/_api/repos").then(function (info) {
        if (info && Array.isArray(info.repos)) window.__KIT_REPOS_INFO__ = info.repos;
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
    /* Upgrade Ask the Orchestrator popup to an OS-style window + add Pop-out window icon button on posts */
    if (sheet.classList.contains("orch-popup")) {
      makeWindow(sheet, sheet.querySelector(".bar"), { minW: 360, minH: 320, winKey: "orch" });
      applySavedWindowGeom(sheet, "orch", { minW: 360, minH: 320 });
      linkRepoDocs(sheet);
      syncUrlWorkspaceState();
      return;
    }
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
    /* Collaboration assignee & working-on controls (auto-hidden when 0 collaborators) */
    syncPostCollabControls(sheet, postId, barRow);
    renderPostArtifacts(sheet, postId);
    /* Link inline .md / § references inside open post & comments to the Repo Docs viewer */
    linkRepoDocs(sheet);
  }

  /* OS-style Window Manager: header drag, 8-edge resize, edge/corner snap preview, z-index focus, proportional cross-device geometry persistence, and full URL workspace state */
  var _kitWinZ = 56;
  var _kitSnapGhost = null;
  var _kitSharedGeoms = {};
  var _kitRestoringUrl = false;
  var _kitDocsState = { open: false, path: "", section: "", zoom: 100 };

  function syncDocsScale(modalEl) {
    var md = modalEl || document.getElementById("kit-docs-modal");
    if (!md) return;
    var r = md.getBoundingClientRect();
    if (!r.width) return;
    var autoScale = isPhoneScreen() ? 1 : Math.max(0.85, Math.min(1.65, r.width / 880));
    var userScale = (_kitDocsState.zoom || 100) / 100;
    var totalScale = Number((autoScale * userScale).toFixed(3));
    md.style.setProperty("--docs-scale", String(totalScale));
  }

  function isPhoneScreen() {
    return typeof window !== "undefined" && (window.innerWidth < 720 || (window.matchMedia && window.matchMedia("(max-width: 719px)").matches));
  }
  function screenAspectRatio() {
    var vw = Math.max(1, window.innerWidth || 1), vh = Math.max(1, window.innerHeight || 1);
    return Number((vw / vh).toFixed(3));
  }
  function isSimilarAspectRatio(ar1, ar2) {
    if (!ar1 || !ar2) return false;
    return Math.abs(ar1 - ar2) / Math.max(ar1, ar2) <= 0.25;
  }
  function localGeomStoreKey(winKey) {
    return "kit_win_geom:" + winKey;
  }
  function readLocalGeoms(winKey) {
    try {
      var raw = JSON.parse(localStorage.getItem(localGeomStoreKey(winKey)) || "null");
      if (Array.isArray(raw)) return raw;
      if (raw && typeof raw === "object" && raw.wr) return [raw];
    } catch (e) {}
    return [];
  }
  function writeLocalGeom(winKey, entry) {
    var list = readLocalGeoms(winKey).filter(function (x) { return !isSimilarAspectRatio(x.ar, entry.ar); });
    list.unshift(entry);
    try { localStorage.setItem(localGeomStoreKey(winKey), JSON.stringify(list.slice(0, 6))); } catch (e) {}
  }
  function recordWindowGeom(winEl, winKey) {
    if (!winEl || !winKey || isPhoneScreen()) return;
    var vw = Math.max(1, window.innerWidth), vh = Math.max(1, window.innerHeight);
    var r = winEl.getBoundingClientRect();
    if (r.width < 100 || r.height < 100) return;
    var entry = {
      ar: screenAspectRatio(),
      lr: Number((r.left / vw).toFixed(4)),
      tr: Number((r.top / vh).toFixed(4)),
      wr: Number((r.width / vw).toFixed(4)),
      hr: Number((r.height / vh).toFixed(4)),
      at: new Date().toISOString()
    };
    writeLocalGeom(winKey, entry);
    var curList = Array.isArray(_kitSharedGeoms[winKey]) ? _kitSharedGeoms[winKey] : [];
    var nextList = [entry].concat(curList.filter(function (x) { return !isSimilarAspectRatio(x && x.ar, entry.ar); })).slice(0, 8);
    _kitSharedGeoms[winKey] = nextList;
    var patch = { win_geoms: {} };
    patch.win_geoms[winKey] = nextList;
    api("PATCH", "/_api/doc/data/users/" + encodeURIComponent(curUserId()) + "/board-ui", { data: patch }).catch(function () {});
  }
  function findMatchingGeom(winKey) {
    if (!winKey || isPhoneScreen()) return null;
    var ar = screenAspectRatio();
    var localList = readLocalGeoms(winKey);
    for (var i = 0; i < localList.length; i++) {
      if (isSimilarAspectRatio(localList[i].ar, ar)) return localList[i];
    }
    var sharedList = _kitSharedGeoms[winKey];
    if (Array.isArray(sharedList)) {
      for (var j = 0; j < sharedList.length; j++) {
        if (sharedList[j] && isSimilarAspectRatio(sharedList[j].ar, ar)) return sharedList[j];
      }
    } else if (sharedList && typeof sharedList === "object" && isSimilarAspectRatio(sharedList.ar, ar)) {
      return sharedList;
    }
    return null;
  }
  function applyProportionalRect(winEl, g, opts) {
    if (!winEl || !g || isPhoneScreen()) return false;
    opts = opts || {};
    var minW = opts.minW || 320, minH = opts.minH || 240;
    var vw = window.innerWidth, vh = window.innerHeight;
    var w = Math.max(minW, Math.min(vw - 16, Math.round((g.wr || 0.5) * vw)));
    var h = Math.max(minH, Math.min(vh - 16, Math.round((g.hr || 0.6) * vh)));
    var l = Math.max(8, Math.min(vw - w - 8, Math.round((g.lr != null ? g.lr : 0.1) * vw)));
    var t = Math.max(8, Math.min(vh - h - 8, Math.round((g.tr != null ? g.tr : 0.08) * vh)));
    winEl.style.inset = "auto";
    winEl.style.margin = "0";
    winEl.style.transform = "none";
    winEl.style.left = l + "px";
    winEl.style.top = t + "px";
    winEl.style.width = w + "px";
    winEl.style.height = h + "px";
    if (winEl.id === "kit-docs-modal") syncDocsScale(winEl);
    return true;
  }
  function applySavedWindowGeom(winEl, winKey, opts) {
    if (!winEl || !winKey || isPhoneScreen()) return false;
    if (winEl.dataset.kitUrlGeom === "1") return true;
    var g = findMatchingGeom(winKey);
    if (!g) return false;
    return applyProportionalRect(winEl, g, opts);
  }
  function loadSharedWindowGeoms() {
    api("GET", "/_api/doc/data/users/" + encodeURIComponent(curUserId()) + "/board-ui").then(function (res) {
      if (res && res.exists && res.data && res.data.win_geoms && typeof res.data.win_geoms === "object") {
        _kitSharedGeoms = res.data.win_geoms;
        var dm = document.getElementById("kit-docs-modal");
        if (dm && !dm.dataset.kitUserGeom && !dm.dataset.kitUrlGeom) applySavedWindowGeom(dm, "docs", { minW: 400, minH: 300 });
        var sh = document.getElementById("sheet");
        if (sh && sh.classList.contains("orch-popup") && !sh.dataset.kitUserGeom && !sh.dataset.kitUrlGeom) applySavedWindowGeom(sh, "orch", { minW: 360, minH: 320 });
        var wins = document.querySelectorAll(".kit-post-win");
        if (wins.length === 1 && !wins[0].dataset.kitUserGeom && !wins[0].dataset.kitUrlGeom) {
          var pid = wins[0].dataset.pid || "";
          if (!applySavedWindowGeom(wins[0], "post:" + pid, { minW: 320, minH: 240 })) {
            applySavedWindowGeom(wins[0], "post", { minW: 320, minH: 240 });
          }
        }
      }
    }, function () {});
  }
  window.addEventListener("resize", function () {
    if (isPhoneScreen()) return;
    var dm = document.getElementById("kit-docs-modal");
    if (dm && !dm.hidden) applySavedWindowGeom(dm, "docs", { minW: 400, minH: 300 });
    var sh = document.getElementById("sheet");
    if (sh && !sh.hidden && sh.classList.contains("orch-popup")) applySavedWindowGeom(sh, "orch", { minW: 360, minH: 320 });
    var wins = document.querySelectorAll(".kit-post-win");
    if (wins.length === 1 && !wins[0].dataset.kitUrlGeom) {
      var pid = wins[0].dataset.pid || "";
      if (!applySavedWindowGeom(wins[0], "post:" + pid, { minW: 320, minH: 240 })) {
        applySavedWindowGeom(wins[0], "post", { minW: 320, minH: 240 });
      }
    } else if (wins.length > 1) {
      layoutPostWindows();
    }
  });

  function rectPercentages(el) {
    if (!el || isPhoneScreen()) return "";
    var vw = Math.max(1, window.innerWidth), vh = Math.max(1, window.innerHeight);
    var r = el.getBoundingClientRect();
    if (r.width < 80 || r.height < 80) return "";
    return [
      (r.left / vw * 100).toFixed(1),
      (r.top / vh * 100).toFixed(1),
      (r.width / vw * 100).toFixed(1),
      (r.height / vh * 100).toFixed(1)
    ].join(",");
  }
  function parseRectPercentages(str) {
    if (!str) return null;
    var parts = String(str).split(",").map(Number);
    if (parts.length < 4 || parts.some(isNaN)) return null;
    if (parts[2] <= 5 || parts[3] <= 5) return null;
    return { lr: parts[0] / 100, tr: parts[1] / 100, wr: parts[2] / 100, hr: parts[3] / 100 };
  }
  function currentOrchDiscFromDom() {
    var sh = document.getElementById("sheet");
    if (!sh || sh.hidden || !sh.classList.contains("orch-popup")) return "";
    var backB = document.getElementById("back");
    if (backB && (backB.textContent || "").indexOf("Discussions") >= 0) {
      try {
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.indexOf("board-orch-disc") === 0) return localStorage.getItem(k) || "";
        }
      } catch (e) {}
      return sessionStorage.getItem("kit-orch-disc") || "";
    }
    return "";
  }
  function buildWorkspaceHash() {
    var raw = (location.hash || "").replace(/^#\/?/, "");
    var basePath = raw.split("?")[0] || "lanes";
    var wrap = document.querySelector(".wrap");
    var curView = (wrap && wrap.getAttribute("data-view")) || "lanes";
    var sh = document.getElementById("sheet");
    var openPid = "";
    try { openPid = sessionStorage.getItem("board-open") || ""; } catch (e) {}
    if (sh && !sh.hidden && openPid) {
      basePath = "post/" + encodeURIComponent(openPid);
    } else if (basePath.indexOf("post/") === 0 && (!sh || sh.hidden)) {
      basePath = curView;
    } else if (["lanes", "trains", "feed", "focus"].indexOf(basePath) >= 0) {
      basePath = curView;
    }
    var params = [];
    var wins = Array.prototype.slice.call(document.querySelectorAll(".kit-post-win"));
    if (wins.length) {
      var wSpecs = wins.map(function (w) {
        var pid = w.dataset.pid || "";
        var rp = rectPercentages(w);
        var z = w.style.zIndex || "56";
        return encodeURIComponent(pid) + (rp ? "@" + rp + "," + z : "");
      });
      params.push("wins=" + wSpecs.join(";"));
    }
    var dm = document.getElementById("kit-docs-modal");
    if (dm && !dm.hidden) {
      var dSpec = _kitDocsState.path ? encodeURIComponent(_kitDocsState.path) : "1";
      if (_kitDocsState.section) dSpec += "~" + encodeURIComponent(_kitDocsState.section);
      var drp = rectPercentages(dm);
      if (drp) dSpec += "@" + drp + "," + (dm.style.zIndex || "55") + "," + (_kitDocsState.zoom || 100);
      params.push("doc=" + dSpec);
    }
    if (sh && !sh.hidden && sh.classList.contains("orch-popup")) {
      var disc = sessionStorage.getItem("kit-orch-disc") || "";
      if (disc) params.push("disc=" + encodeURIComponent(disc));
      var orp = rectPercentages(sh);
      if (orp) params.push("og=" + orp + "," + (sh.style.zIndex || "25"));
    }
    return "#/" + basePath + (params.length ? "?" + params.join("&") : "");
  }
  function syncUrlWorkspaceState() {
    if (_kitRestoringUrl) return;
    try {
      var nextHash = buildWorkspaceHash();
      try { sessionStorage.setItem("kit_workspace_hash", nextHash); } catch (e) {}
      if (location.hash !== nextHash && history && history.replaceState) {
        history.replaceState(null, "", nextHash);
      }
    } catch (e) {}
  }
  window._kitSyncUrlState = syncUrlWorkspaceState;

  function restoreWorkspaceFromHash(hashStr) {
    var h = String(hashStr || location.hash || "");
    if (h.indexOf("?") < 0) {
      try {
        var savedH = sessionStorage.getItem("kit_workspace_hash") || "";
        if (savedH && savedH.indexOf("?") >= 0) h = savedH;
      } catch (e) {}
    }
    var qIdx = h.indexOf("?");
    if (qIdx < 0) return;
    var qs = h.slice(qIdx + 1);
    var map = {};
    qs.split("&").forEach(function (pair) {
      var eq = pair.indexOf("=");
      if (eq > 0) map[pair.slice(0, eq)] = pair.slice(eq + 1);
    });
    _kitRestoringUrl = true;
    try {
      if (map.wins) {
        map.wins.split(";").forEach(function (spec) {
          if (!spec) return;
          var atIdx = spec.indexOf("@");
          var pid = decodeURIComponent(atIdx >= 0 ? spec.slice(0, atIdx) : spec);
          var gStr = atIdx >= 0 ? spec.slice(atIdx + 1) : "";
          var g = parseRectPercentages(gStr);
          var zParts = gStr ? gStr.split(",") : [];
          var z = zParts.length >= 5 ? Number(zParts[4]) : 0;
          if (pid) {
            openPostWindow(pid, { urlGeom: g, zIndex: z, skipLayout: !!g });
          }
        });
      }
      if (map.doc && typeof window._kitOpenRepoDoc === "function") {
        var dAt = map.doc.indexOf("@");
        var dMain = dAt >= 0 ? map.doc.slice(0, dAt) : map.doc;
        var dGStr = dAt >= 0 ? map.doc.slice(dAt + 1) : "";
        var tIdx = dMain.indexOf("~");
        var dPath = decodeURIComponent(tIdx >= 0 ? dMain.slice(0, tIdx) : dMain);
        if (dPath === "1") dPath = "";
        var dSec = tIdx >= 0 ? decodeURIComponent(dMain.slice(tIdx + 1)) : "";
        var dg = parseRectPercentages(dGStr);
        var dzParts = dGStr ? dGStr.split(",") : [];
        var dz = dzParts.length >= 5 ? Number(dzParts[4]) : 0;
        var dzoom = dzParts.length >= 6 ? Number(dzParts[5]) : 0;
        if (dzoom >= 60 && dzoom <= 240) {
          _kitDocsState.zoom = dzoom;
          try { localStorage.setItem("kit_docs_zoom", String(dzoom)); } catch (e) {}
        }
        window._kitOpenRepoDoc(dPath, dSec);
        var dm = document.getElementById("kit-docs-modal");
        if (dm && dg) {
          dm.dataset.kitUrlGeom = "1";
          applyProportionalRect(dm, dg, { minW: 400, minH: 300 });
          if (dz > 0) { dm.style.zIndex = String(dz); if (dz > _kitWinZ) _kitWinZ = dz; }
        }
        syncDocsScale(dm);
      }
      if (map.og) {
        var og = parseRectPercentages(map.og);
        var ozParts = map.og.split(",");
        var oz = ozParts.length >= 5 ? Number(ozParts[4]) : 0;
        var applyOg = function () {
          var sh = document.getElementById("sheet");
          if (sh && sh.classList.contains("orch-popup") && og) {
            sh.dataset.kitUrlGeom = "1";
            applyProportionalRect(sh, og, { minW: 360, minH: 320 });
            if (oz > 0) { sh.style.zIndex = String(oz); if (oz > _kitWinZ) _kitWinZ = oz; }
          }
        };
        applyOg();
        setTimeout(applyOg, 80);
      }
      if (map.disc) {
        var wantDisc = decodeURIComponent(map.disc);
        try { sessionStorage.setItem("kit-orch-disc", wantDisc); } catch (e) {}
        setTimeout(function () {
          if (typeof window._kitOpenOrchDisc === "function") window._kitOpenOrchDisc(wantDisc);
        }, 60);
      }
    } finally {
      _kitRestoringUrl = false;
      syncUrlWorkspaceState();
    }
  }

  function getSnapGhost() {
    if (_kitSnapGhost) return _kitSnapGhost;
    _kitSnapGhost = document.createElement("div");
    _kitSnapGhost.id = "kit-snap-ghost";
    _kitSnapGhost.style.cssText = "position:fixed;z-index:54;pointer-events:none;border-radius:14px;border:2px solid var(--ember);background:color-mix(in srgb, var(--ember) 14%, transparent);transition:all .12s ease;display:none;";
    document.body.appendChild(_kitSnapGhost);
    return _kitSnapGhost;
  }
  function countOpenFloatingWindows() {
    var n = document.querySelectorAll(".kit-post-win").length;
    var docs = document.querySelectorAll(".kit-docs-modal");
    for (var i = 0; i < docs.length; i++) {
      if (!docs[i].hidden) n++;
    }
    var sh = document.getElementById("sheet");
    if (sh && sh.classList.contains("orch-popup") && !sh.hidden) n++;
    return n;
  }
  function snapRectFor(x, y) {
    var w = window.innerWidth, h = window.innerHeight, m = 10, edge = 26, cEdge = 80;
    if (w < 720) return null;
    var openN = Math.max(1, countOpenFloatingWindows());
    var left = x <= edge, right = x >= w - edge, top = y <= 24;
    var nearTop = y <= cEdge, nearBot = y >= h - cEdge;
    if (left && nearTop) return { l: m, t: m, w: Math.floor(w / 2 - m * 1.5), h: Math.floor(h / 2 - m * 1.5) };
    if (left && nearBot) return { l: m, t: Math.floor(h / 2 + m * 0.5), w: Math.floor(w / 2 - m * 1.5), h: Math.floor(h / 2 - m * 1.5) };
    if (right && nearTop) return { l: Math.floor(w / 2 + m * 0.5), t: m, w: Math.floor(w / 2 - m * 1.5), h: Math.floor(h / 2 - m * 1.5) };
    if (right && nearBot) return { l: Math.floor(w / 2 + m * 0.5), t: Math.floor(h / 2 + m * 0.5), w: Math.floor(w / 2 - m * 1.5), h: Math.floor(h / 2 - m * 1.5) };
    if (openN >= 3 && w >= 1100) {
      var thirdW = Math.floor((w - m * 4) / 3);
      if (left) return { l: m, t: m, w: thirdW, h: h - m * 2 };
      if (right) return { l: w - m - thirdW, t: m, w: thirdW, h: h - m * 2 };
      if (top && x > w / 3 && x < (2 * w) / 3 && y > 10) return { l: m * 2 + thirdW, t: m, w: thirdW, h: h - m * 2 };
    }
    if (left) return { l: m, t: m, w: Math.floor(w / 2 - m * 1.5), h: h - m * 2 };
    if (right) return { l: Math.floor(w / 2 + m * 0.5), t: m, w: Math.floor(w / 2 - m * 1.5), h: h - m * 2 };
    if (top) return { l: m, t: m, w: w - m * 2, h: h - m * 2 };
    return null;
  }
  function placeWindowLeftOfOthers(winEl, defaultW, defaultH, winKey, alsoKey, minW, minH) {
    if (!winEl || window.innerWidth < 720) return;
    var vw = window.innerWidth, vh = window.innerHeight, gap = 14;
    var others = [];
    var allW = document.querySelectorAll(".kit-post-win, .kit-docs-modal, #sheet.orch-popup");
    for (var i = 0; i < allW.length; i++) {
      var o = allW[i];
      if (o === winEl || o.hidden) continue;
      var or = o.getBoundingClientRect();
      if (or.width > 40 && or.height > 40) others.push(or);
    }
    var w = Math.min(defaultW, vw - 24), h = Math.min(defaultH, vh - 48);
    var saved = findMatchingGeom(winKey) || (alsoKey ? findMatchingGeom(alsoKey) : null);
    if (saved && saved.w > 0 && saved.h > 0) {
      w = Math.max(minW || 320, Math.min(vw - 24, Math.round(saved.w * vw)));
      h = Math.max(minH || 240, Math.min(vh - 32, Math.round(saved.h * vh)));
    }
    var l, t;
    if (!others.length) {
      if (saved && typeof saved.l === "number" && typeof saved.t === "number") {
        l = Math.max(12, Math.min(vw - w - 12, Math.round(saved.l * vw)));
        t = Math.max(12, Math.min(vh - h - 12, Math.round(saved.t * vh)));
      } else {
        l = Math.max(12, Math.min(vw - w - 12, Math.round(vw - w - Math.max(28, vw * 0.06))));
        t = Math.max(16, Math.min(vh - h - 16, Math.round((vh - h) / 2)));
      }
    } else {
      var leftmost = others[0];
      for (var j = 1; j < others.length; j++) {
        if (others[j].left < leftmost.left) leftmost = others[j];
      }
      var candL = Math.round(leftmost.left - w - gap);
      t = Math.max(12, Math.min(vh - h - 12, Math.round(leftmost.top)));
      if (candL >= 12) {
        l = candL;
      } else {
        l = Math.max(12, Math.min(vw - w - 12, Math.round(leftmost.left - 34)));
        t = Math.max(12, Math.min(vh - h - 12, Math.round(leftmost.top + 26)));
        if (l === Math.round(leftmost.left)) l = Math.max(12, Math.min(vw - w - 12, 18 + (others.length % 5) * 28));
      }
    }
    winEl.style.inset = "auto";
    winEl.style.margin = "0";
    winEl.style.transform = "none";
    winEl.style.left = l + "px";
    winEl.style.top = t + "px";
    winEl.style.width = w + "px";
    winEl.style.height = h + "px";
  }
  function trapWheelScroll(winEl) {
    if (!winEl || winEl.dataset.kitWheelTrap) return;
    winEl.dataset.kitWheelTrap = "1";
    winEl.addEventListener("wheel", function (e) {
      var cur = e.target;
      while (cur && cur !== winEl) {
        var st = window.getComputedStyle(cur);
        var oy = st.overflowY;
        if ((oy === "auto" || oy === "scroll") && cur.scrollHeight > cur.clientHeight + 1) {
          var atTop = cur.scrollTop <= 0 && e.deltaY < 0;
          var atBot = cur.scrollTop + cur.clientHeight >= cur.scrollHeight - 1 && e.deltaY > 0;
          if (!atTop && !atBot) return;
          e.preventDefault();
          return;
        }
        cur = cur.parentNode;
      }
      e.preventDefault();
    }, { passive: false });
  }
  if (typeof window !== "undefined") {
    window._kitMakeWindow = makeWindow;
    window._kitPlaceWindow = placeWindowLeftOfOthers;
  }
  function makeWindow(winEl, hdrEl, opts) {
    if (!winEl || winEl.dataset.kitWinReady) return;
    winEl.dataset.kitWinReady = "1";
    opts = opts || {};
    var minW = opts.minW || 340, minH = opts.minH || 260, edgePx = 8, winKey = opts.winKey || "";
    trapWheelScroll(winEl);
    function bringFront() { _kitWinZ += 1; winEl.style.zIndex = String(_kitWinZ); }
    winEl.addEventListener("mousedown", function () { bringFront(); syncUrlWorkspaceState(); });
    function saveWinGeom() {
      if (winKey) recordWindowGeom(winEl, winKey);
      if (opts.alsoKey) recordWindowGeom(winEl, opts.alsoKey);
      syncUrlWorkspaceState();
    }
    function ensureGeom() {
      var r = winEl.getBoundingClientRect();
      winEl.style.inset = "auto";
      winEl.style.margin = "0";
      winEl.style.transform = "none";
      winEl.style.left = Math.round(r.left) + "px";
      winEl.style.top = Math.round(r.top) + "px";
      winEl.style.width = Math.round(r.width) + "px";
      winEl.style.height = Math.round(r.height) + "px";
      return r;
    }
    function hitEdges(e) {
      if (window.innerWidth < 680) return "";
      var r = winEl.getBoundingClientRect();
      var x = e.clientX - r.left, y = e.clientY - r.top;
      var s = "";
      if (y <= edgePx) s += "n"; else if (y >= r.height - edgePx) s += "s";
      if (x <= edgePx) s += "w"; else if (x >= r.width - edgePx) s += "e";
      return s;
    }
    var curMap = { n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize" };
    winEl.addEventListener("mousemove", function (e) {
      if (winEl.dataset.kitDragging) return;
      var ed = hitEdges(e);
      winEl.style.cursor = ed ? curMap[ed] : "";
    });
    winEl.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      var ed = hitEdges(e);
      if (!ed) return;
      e.preventDefault();
      bringFront();
      var r0 = ensureGeom(), sx = e.clientX, sy = e.clientY;
      winEl.dataset.kitDragging = "1";
      function onMove(ev) {
        winEl.dataset.kitUserGeom = "1";
        var dx = ev.clientX - sx, dy = ev.clientY - sy;
        var l = r0.left, t = r0.top, w = r0.width, h = r0.height;
        if (ed.indexOf("e") >= 0) w = Math.max(minW, Math.min(window.innerWidth - l - 4, r0.width + dx));
        if (ed.indexOf("s") >= 0) h = Math.max(minH, Math.min(window.innerHeight - t - 4, r0.height + dy));
        if (ed.indexOf("w") >= 0) {
          var nw = Math.max(minW, r0.width - dx);
          l = Math.max(4, r0.right - nw); w = r0.right - l;
        }
        if (ed.indexOf("n") >= 0) {
          var nh = Math.max(minH, r0.height - dy);
          t = Math.max(4, r0.bottom - nh); h = r0.bottom - t;
        }
        winEl.style.left = Math.round(l) + "px"; winEl.style.top = Math.round(t) + "px";
        winEl.style.width = Math.round(w) + "px"; winEl.style.height = Math.round(h) + "px";
        if (winEl.classList.contains("kit-docs-modal")) syncDocsScale(winEl);
      }
      function onUp() {
        delete winEl.dataset.kitDragging;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        saveWinGeom();
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    if (hdrEl) {
      hdrEl.style.cursor = "grab";
      hdrEl.addEventListener("mousedown", function (e) {
        if (e.button !== 0 || hitEdges(e)) return;
        if (e.target && e.target.closest && e.target.closest("button,input,select,textarea,a,[contenteditable='true']")) return;
        e.preventDefault();
        bringFront();
        var r0 = ensureGeom(), sx = e.clientX, sy = e.clientY, lastSnap = null;
        hdrEl.style.cursor = "grabbing";
        winEl.dataset.kitDragging = "1";
        var ghost = getSnapGhost();
        function onMove(ev) {
          winEl.dataset.kitUserGeom = "1";
          var nl = Math.max(4 - r0.width + 80, Math.min(window.innerWidth - 80, r0.left + (ev.clientX - sx)));
          var nt = Math.max(4, Math.min(window.innerHeight - 44, r0.top + (ev.clientY - sy)));
          winEl.style.left = Math.round(nl) + "px";
          winEl.style.top = Math.round(nt) + "px";
          lastSnap = snapRectFor(ev.clientX, ev.clientY);
          if (lastSnap) {
            ghost.style.display = "block";
            ghost.style.left = lastSnap.l + "px"; ghost.style.top = lastSnap.t + "px";
            ghost.style.width = lastSnap.w + "px"; ghost.style.height = lastSnap.h + "px";
          } else {
            ghost.style.display = "none";
          }
        }
        function onUp() {
          delete winEl.dataset.kitDragging;
          hdrEl.style.cursor = "grab";
          ghost.style.display = "none";
          if (lastSnap) {
            winEl.style.left = lastSnap.l + "px"; winEl.style.top = lastSnap.t + "px";
            winEl.style.width = lastSnap.w + "px"; winEl.style.height = lastSnap.h + "px";
            if (winEl.classList.contains("kit-docs-modal")) syncDocsScale(winEl);
          }
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          saveWinGeom();
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    }
  }

  window._kitMakeWindow = makeWindow;
  window._kitPlaceWindow = placeWindowLeftOfOthers;
  /* Multi-post floating windows (open as many posts side-by-side as needed, movable/resizable/snappable; no auto-fullscreen tiling) */
  var _kitWinOffset = 0;
  function layoutPostWindows() {
    syncUrlWorkspaceState();
  }
  function openPostWindow(postId, openOpts) {
    if (!postId) return;
    openOpts = openOpts || {};
    var existing = document.querySelector('.kit-post-win[data-pid="' + postId.replace(/"/g, "") + '"]');
    if (existing) {
      if (openOpts.urlGeom) {
        existing.dataset.kitUrlGeom = "1";
        applyProportionalRect(existing, openOpts.urlGeom, { minW: 320, minH: 240 });
      }
      if (openOpts.zIndex > 0) {
        existing.style.zIndex = String(openOpts.zIndex);
        if (openOpts.zIndex > _kitWinZ) _kitWinZ = openOpts.zIndex;
      } else {
        _kitWinZ += 1; existing.style.zIndex = String(_kitWinZ);
      }
      syncUrlWorkspaceState();
      return;
    }
    var SVG_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    var SVG_ATT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
    var SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
    if (!document.getElementById("kit-post-win-css")) {
      var pwSt = document.createElement("style");
      pwSt.id = "kit-post-win-css";
      pwSt.textContent =
        ".kit-post-win{position:fixed;background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:0 18px 48px rgba(0,0,0,.32);display:flex;flex-direction:column;overflow:hidden;min-width:320px;min-height:240px;}" +
        ".kit-post-win-hdr{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;border-bottom:1px solid var(--line);background:var(--sunk);flex:none;min-width:0;}" +
        ".kit-post-win-ttl{flex:1 1 0;min-width:0;font-family:var(--display);font-size:1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
        ".kit-post-win-body{flex:1 1 0;min-height:0;min-width:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:16px 18px;}" +
        ".kit-post-win-body .body{max-width:none !important;width:100%;min-width:0;overflow-wrap:anywhere;}" +
        ".kit-post-win-foot{flex:none;padding:8px 12px 10px;border-top:1px solid var(--line);background:var(--surface);min-width:0;}" +
        ".kit-post-win-foot .composer{position:relative !important;bottom:auto !important;margin-top:0 !important;}";
      document.head.appendChild(pwSt);
    }
    var win = document.createElement("section");
    win.className = "kit-post-win"; win.dataset.pid = postId;
    win.setAttribute("role", "dialog");
    var off = (_kitWinOffset++ % 6) * 26;
    var w = Math.min(540, window.innerWidth - 24), h = Math.min(640, window.innerHeight - 64);
    var l = Math.max(16, Math.min(window.innerWidth - w - 16, Math.round((window.innerWidth - w) / 2) + off));
    var t = Math.max(16, Math.min(window.innerHeight - h - 16, 48 + off));
    _kitWinZ += 1;
    win.style.left = l + "px"; win.style.top = t + "px"; win.style.width = w + "px"; win.style.height = h + "px"; win.style.zIndex = String(_kitWinZ);
    var hdr = document.createElement("div"); hdr.className = "kit-post-win-hdr";
    var ttl = document.createElement("b"); ttl.className = "kit-post-win-ttl"; ttl.textContent = postId;
    var cls = document.createElement("button"); cls.type = "button"; cls.className = "btn small kit-icon-btn"; cls.style.flex = "none"; cls.title = "Close"; cls.setAttribute("aria-label", "Close"); cls.innerHTML = SVG_X;
    cls.addEventListener("click", function () { win.remove(); layoutPostWindows(); });
    hdr.appendChild(ttl); hdr.appendChild(cls);
    var body = document.createElement("div"); body.className = "kit-post-win-body";
    body.innerHTML = '<div class="note">Loading...</div>';
    var footWrap = document.createElement("div"); footWrap.className = "kit-post-win-foot";
    var foot = document.createElement("form"); foot.className = "composer";
    var filesBox = document.createElement("div"); filesBox.className = "files";
    var inp = document.createElement("textarea"); inp.maxLength = 4000; inp.placeholder = "Write a comment";
    var drKey = "board-draft:" + postId + ":0";
    try {
      var savedDr = JSON.parse(localStorage.getItem(drKey) || "null");
      if (savedDr && typeof savedDr.t === "string" && savedDr.t) {
        inp.value = savedDr.t;
        foot.classList.add("has-text");
      }
    } catch (e) {}
    var fileIn = document.createElement("input"); fileIn.type = "file"; fileIn.multiple = true; fileIn.hidden = true;
    var attBtn = document.createElement("button"); attBtn.type = "button"; attBtn.id = "c-attach"; attBtn.className = "btn small"; attBtn.title = "Attach a picture or a file"; attBtn.setAttribute("aria-label", "Attach a picture or a file"); attBtn.innerHTML = SVG_ATT;
    var snd = document.createElement("button"); snd.type = "submit"; snd.id = "c-send"; snd.className = "btn ember"; snd.title = "Send comment"; snd.setAttribute("aria-label", "Send comment"); snd.innerHTML = SVG_SEND;
    var noteEl = document.createElement("span"); noteEl.id = "c-note"; noteEl.className = "note";
    var winPendingFiles = [];
    function syncWinComposer() {
      var val = (inp.value || "").trim();
      foot.classList.toggle("has-text", !!(val || winPendingFiles.some(function (x) { return !!x.id; })));
      try {
        if (inp.value) localStorage.setItem(drKey, JSON.stringify({ t: inp.value, r: "", at: new Date().toISOString() }));
        else localStorage.removeItem(drKey);
      } catch (e) {}
    }
    function renderWinFiles() {
      filesBox.textContent = "";
      winPendingFiles.forEach(function (x) {
        var c = document.createElement("span"); c.className = "file";
        c.textContent = x.name + (x.error ? ": " + x.error : (x.id ? "" : " (uploading...)"));
        var rm = document.createElement("button"); rm.type = "button"; rm.textContent = "\u00d7"; rm.setAttribute("aria-label", "Remove " + x.name);
        rm.addEventListener("click", function () { winPendingFiles = winPendingFiles.filter(function (y) { return y !== x; }); renderWinFiles(); syncWinComposer(); });
        c.appendChild(rm); filesBox.appendChild(c);
      });
    }
    attBtn.addEventListener("click", function () { fileIn.click(); });
    fileIn.addEventListener("change", function () {
      Array.prototype.slice.call(fileIn.files || []).forEach(function (file) {
        var x = { name: file.name, type: file.type || "application/octet-stream", id: null, error: "" };
        winPendingFiles.push(x);
        if (file.size > 20 * 1024 * 1024) { x.error = "over 20 MB, not sent"; renderWinFiles(); return; }
        assets.upload(file).then(function (r) {
          x.id = (r && r.id) || r;
          if (r && r.contentType) x.type = r.contentType;
          renderWinFiles(); syncWinComposer();
        }, function () { x.error = "upload failed"; renderWinFiles(); });
      });
      fileIn.value = ""; renderWinFiles();
    });
    inp.addEventListener("input", syncWinComposer);
    inp.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing || !(window.matchMedia && matchMedia("(hover: hover) and (pointer: fine)").matches)) return;
      e.preventDefault();
      if (foot.requestSubmit) foot.requestSubmit(snd); else snd.click();
    });
    var attachRow = document.createElement("div"); attachRow.id = "c-attach-row";
    attachRow.appendChild(attBtn); attachRow.appendChild(fileIn);
    var sendRow = document.createElement("div"); sendRow.className = "row";
    sendRow.appendChild(snd); sendRow.appendChild(noteEl);
    foot.appendChild(inp); foot.appendChild(attachRow); foot.appendChild(filesBox); foot.appendChild(sendRow);
    footWrap.appendChild(foot);
    win.appendChild(hdr); win.appendChild(body); win.appendChild(footWrap);
    document.body.appendChild(win);
    makeWindow(win, hdr, { minW: 320, minH: 240, winKey: "post:" + postId, alsoKey: "post" });
    if (openOpts.urlGeom) {
      win.dataset.kitUrlGeom = "1";
      applyProportionalRect(win, openOpts.urlGeom, { minW: 320, minH: 240 });
      if (openOpts.zIndex > 0) {
        win.style.zIndex = String(openOpts.zIndex);
        if (openOpts.zIndex > _kitWinZ) _kitWinZ = openOpts.zIndex;
      }
    } else {
      placeWindowLeftOfOthers(win, 640, 700, "post:" + postId, "post", 320, 240);
    }
    syncUrlWorkspaceState();

    function loadPostWin() {
      Promise.all([
        api("GET", "/_api/db/posts/" + encodeURIComponent(postId)),
        api("GET", "/_api/db/comments?where=" + encodeURIComponent(JSON.stringify([["post_id", "==", postId]])))
      ]).then(function (pair) {
        var doc = pair[0] && pair[0].data ? pair[0].data : {};
        var cmts = ((pair[1] && pair[1].docs) || []).map(function (x) { return Object.assign({ id: x.id }, x.data || {}); }).filter(function (c) { return !c.removed && !c.like; });
        cmts.sort(function (a, b) { return String(a.created_at || "").localeCompare(String(b.created_at || "")); });
        ttl.textContent = doc.title || postId;
        ttl.title = doc.title || postId;
        body.textContent = "";
        var md = document.createElement("div"); md.className = "body";
        md.innerHTML = typeof window._kitRenderBody === "function" ? window._kitRenderBody(doc.body || "") : (doc.body || "");
        body.appendChild(md);
        var docImgs = Array.isArray(doc.images) ? doc.images.filter(Boolean) : [];
        if (docImgs.length) {
          var caps = Array.isArray(doc.captions) ? doc.captions : [];
          var grps = Array.isArray(doc.image_groups) && doc.image_groups.length ? doc.image_groups : [{ title: "", count: docImgs.length }];
          var idxImg = 0;
          grps.forEach(function (g) {
            var cnt = Math.max(1, Math.min(docImgs.length - idxImg, Math.floor(Number(g && g.count) || (docImgs.length - idxImg))));
            if (cnt <= 0) return;
            if (g && g.title) {
              var gh = document.createElement("h4"); gh.style.cssText = "margin:12px 0 6px;font-size:.9rem;color:var(--muted);";
              gh.textContent = g.title; body.appendChild(gh);
            }
            var grid = document.createElement("div"); grid.className = "kit-inline-cmp";
            var cols = cnt <= 2 ? cnt : (cnt <= 4 ? 2 : 3);
            grid.style.gridTemplateColumns = "repeat(" + cols + ", minmax(0, 1fr))";
            for (var k = 0; k < cnt && idxImg < docImgs.length; k++, idxImg++) {
              var uImg = String(docImgs[idxImg] || "");
              var srcImg = uImg.indexOf("/_blob/") === 0 || uImg.indexOf("/_api/") === 0 || /^https?:\/\//i.test(uImg) ? uImg : resolveAssetUrl(uImg, "");
              var cell = document.createElement("div"); cell.className = "kit-inline-cmp-item";
              var cb = document.createElement("div"); cb.className = "icbody";
              var imEl = document.createElement("img"); imEl.src = srcImg; imEl.alt = caps[idxImg] || ("Picture " + (idxImg + 1));
              cb.appendChild(imEl); cell.appendChild(cb);
              if (caps[idxImg]) {
                var cp = document.createElement("div"); cp.className = "iccap"; cp.textContent = caps[idxImg];
                cell.appendChild(cp);
              }
              grid.appendChild(cell);
            }
            body.appendChild(grid);
          });
        }
        if (cmts.length) {
          var ch = document.createElement("div"); ch.style.cssText = "margin-top:18px;padding-top:12px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:10px;";
          cmts.forEach(function (c) {
            var item = document.createElement("div"); item.style.cssText = "background:var(--sunk);border-radius:10px;padding:8px 11px;font-size:.88rem;";
            var wh = document.createElement("div"); wh.style.cssText = "font-size:.75rem;font-weight:700;color:var(--muted);margin-bottom:3px;";
            wh.textContent = (c.from === "owner" ? "You" : (c.from || "?")) + " · " + String(c.created_at || "").slice(0, 16).replace("T", " ");
            var tx = document.createElement("div"); tx.className = "body"; tx.style.fontSize = ".88rem";
            tx.innerHTML = typeof window._kitRenderBody === "function" ? window._kitRenderBody(c.text || "") : (c.text || "");
            item.appendChild(wh); item.appendChild(tx); ch.appendChild(item);
          });
          body.appendChild(ch);
        }
        linkRepoDocs(win);
      }, function () { body.innerHTML = '<div class="note">Could not load post.</div>'; });
    }
    foot.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (winPendingFiles.some(function (x) { return !x.id && !x.error; })) { noteEl.textContent = "Wait: a file is still uploading."; return; }
      var txt = (inp.value || "").trim();
      var readyFiles = winPendingFiles.filter(function (x) { return !!x.id; }).map(function (x) { return { id: x.id, name: x.name, type: x.type }; });
      if (!txt && !readyFiles.length) return;
      if (!txt) txt = "(see the attached file)";
      inp.value = ""; winPendingFiles = []; renderWinFiles(); syncWinComposer();
      noteEl.textContent = "Sending...";
      var cid = "c-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      var rowData = { post_id: postId, text: txt, from: curUserId(), created_at: new Date().toISOString(), delivered: false, delivery_error: "", read: false };
      if (readyFiles.length) rowData.files = readyFiles;
      var told = "[post " + postId + "] " + txt + readyFiles.map(function (x) { return "\n[attached: " + x.name + " (" + x.type + ") at /_blob/" + x.id + "]"; }).join("");
      var sendP = comments.sendToClaude({ anchor: { tag: "b", id: "", text: (ttl.textContent || postId).slice(0, 80) }, text: told }).then(function (r) {
        return { delivered: true, delivery_error: "", delivered_at: new Date().toISOString(), thread_id: String((r && r.threadId) || "") };
      }, function () {
        return { delivered: false, delivery_error: "not_sent" };
      });
      api("PUT", "/_api/db/comments/" + encodeURIComponent(cid), { data: rowData }).then(function () {
        return sendP.then(function (df) {
          return api("PATCH", "/_api/db/comments/" + encodeURIComponent(cid), { data: df }).catch(function () {});
        });
      }).then(function () {
        noteEl.textContent = "";
        loadPostWin();
      }, function () {
        noteEl.textContent = "Could not save comment.";
      });
    });
    loadPostWin();
  }
  window._kitOpenPostWindow = openPostWindow;

  /* Read-only Repo Docs & Memory Reader (pointer-aware left doc-list & right outline, OS window drag/resize/snap, proportional zoom, rich GitHub Markdown/HTML renderer, user pins, post-install new dots, zero emojis) */
  function withKitToken(url) {
    var u = String(url || "");
    if (!u || /^(?:https?:\/\/|data:|blob:)/i.test(u) || u.indexOf("token=") >= 0) return u;
    var tok = window.__KIT_TOKEN__ || (function () { try { return localStorage.getItem("kit_board_token") || ""; } catch (e) { return ""; } })();
    if (!tok) return u;
    return u + (u.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(tok);
  }
  function buildHtmlSrcdoc(rawHtml, baseUrl) {
    var html = String(rawHtml || "");
    var cleanBase = String(baseUrl || "").split("?")[0].split("#")[0];
    var dirBase = cleanBase ? cleanBase.replace(/[^/]*$/, "") : "/";
    if (dirBase && !/<base\b/i.test(html)) {
      var baseTag = '<base href="' + dirBase.replace(/"/g, "&quot;") + '">';
      if (/<head\b[^>]*>/i.test(html)) html = html.replace(/<head\b[^>]*>/i, function (m) { return m + baseTag; });
      else html = baseTag + html;
    }
    return html;
  }
  function resolveAssetUrl(rawSrc, baseDocPath, repoOverride) {
    var s = String(rawSrc || "").trim();
    if (!s) return "";
    if (/^(?:https?:\/\/|data:|\/_blob\/|\/_api\/|\/_repo\/)/i.test(s)) return withKitToken(s);
    var clean = s.split("#")[0].split("?")[0].replace(/^\.\//, "");
    if (baseDocPath && clean.charAt(0) !== "/") {
      var dirParts = String(baseDocPath).split("/");
      dirParts.pop();
      var segs = dirParts.concat(clean.split("/"));
      var norm = [];
      for (var i = 0; i < segs.length; i++) {
        if (!segs[i] || segs[i] === ".") continue;
        if (segs[i] === "..") norm.pop();
        else norm.push(segs[i]);
      }
      clean = norm.join("/");
    } else {
      clean = clean.replace(/^\/+/, "");
    }
    var u = "/_api/repo-asset?path=" + encodeURIComponent(clean);
    var rk = repoOverride !== undefined ? repoOverride : (window.__KIT_ACTIVE_REPO__ || "");
    if (rk && rk !== "all") u += "&repo=" + encodeURIComponent(rk);
    return withKitToken(u);
  }

  function resolveRelativeDocPath(hrefPath, baseDocPath) {
    var clean = String(hrefPath || "").trim().replace(/^\.\//, "");
    if (!clean) return "";
    if (baseDocPath && clean.charAt(0) !== "/") {
      var dirParts = String(baseDocPath).split("/");
      dirParts.pop();
      var segs = dirParts.concat(clean.split("/"));
      var norm = [];
      for (var i = 0; i < segs.length; i++) {
        if (!segs[i] || segs[i] === ".") continue;
        if (segs[i] === "..") norm.pop();
        else norm.push(segs[i]);
      }
      return norm.join("/");
    }
    return clean.replace(/^\/+/, "");
  }

  function parseAssociatedDocLink(href, baseDocPath) {
    var raw = String(href || "").trim();
    if (!raw || raw.indexOf("javascript:") === 0 || raw.indexOf("mailto:") === 0) return null;
    if (raw.charAt(0) === "#") {
      return { anchorOnly: true, section: raw.slice(1) };
    }
    var ghMatch = /^https?:\/\/(?:www\.)?github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?(?:\/(tree|blob|raw)\/[^\/#?]+(?:\/([^#?]*))?)?(?:[?#](.*))?$/i.exec(raw);
    var rawGhMatch = !ghMatch && /^https?:\/\/raw\.githubusercontent\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/[^\/#?]+\/([^#?]+)(?:[?#](.*))?$/i.exec(raw);
    if (ghMatch || rawGhMatch) {
      var slug = (ghMatch ? ghMatch[1] : rawGhMatch[1]).toLowerCase();
      var subPath = ghMatch ? (ghMatch[3] || "") : (rawGhMatch[2] || "");
      var frag = "";
      var hashIdx = raw.indexOf("#");
      if (hashIdx >= 0) frag = decodeURIComponent(raw.slice(hashIdx + 1));
      var repos = window.__KIT_REPOS_INFO__ || [];
      var matchedRepo = null;
      for (var i = 0; i < repos.length; i++) {
        var r = repos[i];
        var slugs = Array.isArray(r.github_slugs) ? r.github_slugs : [];
        var rName = String(r.name || r.id || "").toLowerCase();
        if (slugs.indexOf(slug) >= 0 || slug.split("/")[1] === rName || slug.split("/")[1] === String(r.id || "").toLowerCase()) {
          matchedRepo = r;
          break;
        }
      }
      if (!matchedRepo) return null;
      var targetPath = subPath ? decodeURIComponent(subPath).replace(/^\/+|\/+$/g, "") : "README.md";
      if (!targetPath) targetPath = "README.md";
      if (!/\.(?:md|html?|png|jpe?g|gif|webp|avif|svg)$/i.test(targetPath)) {
        if (!/\.[A-Za-z0-9]{1,6}$/.test(targetPath)) targetPath = targetPath.replace(/\/+$/, "") + "/README.md";
        else return null;
      }
      return {
        repoId: matchedRepo.primary ? "" : matchedRepo.id,
        path: targetPath,
        section: frag
      };
    }
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw) && raw.indexOf("/_blob/") !== 0 && raw.indexOf("/_api/") !== 0) {
      var hIdx = raw.indexOf("#");
      var pPart = hIdx >= 0 ? raw.slice(0, hIdx) : raw;
      var sPart = hIdx >= 0 ? decodeURIComponent(raw.slice(hIdx + 1)) : "";
      pPart = pPart.split("?")[0];
      if (!pPart && sPart) return { anchorOnly: true, section: sPart };
      if (/\.(?:md|html?|png|jpe?g|gif|webp|avif|svg)$/i.test(pPart)) {
        return {
          repoId: undefined,
          path: resolveRelativeDocPath(pPart, baseDocPath),
          section: sPart
        };
      }
    }
    return null;
  }

  function renderRichDocMarkdown(mdText, baseDocPath, repoOverride) {
    var text = String(mdText || "").replace(/\r/g, "");
    function escH(s) {
      return String(s || "").replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function sanitizeSafeHtmlTag(tagMatch, slash, tagName, attrs) {
      var t = String(tagName || "").toLowerCase();
      var allowed = { p: 1, h1: 1, h2: 1, h3: 1, h4: 1, img: 1, a: 1, strong: 1, b: 1, em: 1, i: 1, code: 1, pre: 1, br: 1, hr: 1, span: 1, div: 1, details: 1, summary: 1, ul: 1, ol: 1, li: 1, table: 1, thead: 1, tbody: 1, tr: 1, th: 1, td: 1, blockquote: 1, sup: 1, sub: 1, kbd: 1 };
      if (!allowed[t]) return escH(tagMatch);
      if (slash) return "</" + t + ">";
      var outAttrs = [];
      var attrRe = /([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))/g;
      var am;
      while ((am = attrRe.exec(attrs || "")) !== null) {
        var ak = am[1].toLowerCase();
        var av = am[2] !== undefined ? am[2] : (am[3] !== undefined ? am[3] : (am[4] || ""));
        if (ak === "align" && (av === "center" || av === "right" || av === "left")) {
          outAttrs.push('style="text-align:' + av + '"');
        } else if (ak === "src" && t === "img") {
          outAttrs.push('src="' + escH(resolveAssetUrl(av, baseDocPath, repoOverride)) + '"');
        } else if (ak === "href" && t === "a") {
          if (!/^\s*javascript:/i.test(av)) outAttrs.push('href="' + escH(av) + '"');
        } else if (ak === "alt" || ak === "title" || ak === "width" || ak === "height" || ak === "id") {
          outAttrs.push(ak + '="' + escH(av) + '"');
        }
      }
      var selfClose = (t === "img" || t === "br" || t === "hr") ? "" : "";
      return "<" + t + (outAttrs.length ? " " + outAttrs.join(" ") : "") + selfClose + ">";
    }
    function renderInlineRich(s) {
      var codeSlots = [];
      var withSlots = String(s || "").replace(/`([^`]+)`/g, function (_, c) {
        var idx = codeSlots.length;
        codeSlots.push("<code>" + escH(c) + "</code>");
        return "\u0000CODE" + idx + "\u0000";
      });
      var htmlSlots = [];
      withSlots = withSlots.replace(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*?)(\/?)>/g, function (full, sl, tn, at) {
        var idx = htmlSlots.length;
        htmlSlots.push(sanitizeSafeHtmlTag(full, sl, tn, at));
        return "\u0000HTML" + idx + "\u0000";
      });
      var out = escH(withSlots);
      out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g, function (_, alt, src) {
        var rSrc = resolveAssetUrl(src, baseDocPath, repoOverride);
        return '<img src="' + escH(rSrc) + '" alt="' + alt + '">';
      });
      out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, lbl, href) {
        var parsed = parseAssociatedDocLink(href, baseDocPath);
        if (parsed) {
          return '<a href="' + href + '" class="kit-doc-link">' + lbl + '</a>';
        }
        return '<a href="' + href + '" target="_blank" rel="noopener">' + lbl + '</a>';
      });
      out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
      out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
      out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:])/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
      out = out.replace(/\u0000HTML(\d+)\u0000/g, function (_, n) { return htmlSlots[Number(n)] || ""; });
      out = out.replace(/\u0000CODE(\d+)\u0000/g, function (_, n) { return codeSlots[Number(n)] || ""; });
      return out;
    }

    var lines = text.split("\n");
    var html = "", para = [], i = 0;
    function flush() {
      if (para.length) {
        var joined = para.map(renderInlineRich).join("<br>");
        if (/^\s*<(?:p|h[1-4]|div|ul|ol|table|blockquote|pre|hr)\b/i.test(joined)) html += joined;
        else html += "<p>" + joined + "</p>";
        para = [];
      }
    }
    while (i < lines.length) {
      var l = lines[i];
      if (/^\s*<!--/.test(l)) {
        while (i < lines.length && !/-->/.test(lines[i])) i++;
        i++;
        continue;
      }
      var fence = /^\s*```([A-Za-z0-9_-]*)\s*$/.exec(l);
      if (fence) {
        flush();
        var lang = (fence[1] || "").toLowerCase();
        i++;
        var code = [];
        while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i++; }
        if (i < lines.length) i++;
        if (lang === "mermaid") {
          html += renderMermaidBlock(code.join("\n"));
        } else {
          html += "<pre><code>" + escH(code.join("\n")) + "</code></pre>";
        }
        continue;
      }
      if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(l)) {
        flush(); html += "<hr>"; i++; continue;
      }
      if (/^\s*<(p|h[1-4]|div|details|table|ul|ol|blockquote)\b/i.test(l)) {
        flush();
        var tagOpen = /^\s*<([a-zA-Z0-9]+)\b/i.exec(l)[1].toLowerCase();
        var closeRe = new RegExp("</" + tagOpen + "\\s*>", "i");
        var blockLines = [l];
        while (i + 1 < lines.length && !closeRe.test(lines[i])) {
          i++;
          blockLines.push(lines[i]);
        }
        i++;
        html += blockLines.map(renderInlineRich).join("\n");
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(l)) {
        flush();
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        var cells = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var hasHead = rows.length > 1 && /^[\s|:-]+$/.test(rows[1]);
        var t = '<div class="tbl"><table>';
        rows.forEach(function (r, k) {
          if (hasHead && k === 1) return;
          var tag = hasHead && k === 0 ? "th" : "td";
          t += "<tr>" + cells(r).map(function (c) { return "<" + tag + ">" + renderInlineRich(c) + "</" + tag + ">"; }).join("") + "</tr>";
        });
        html += t + "</table></div>";
        continue;
      }
      if (/^\s*>\s?/.test(l)) {
        flush();
        var bq = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) { bq.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
        html += "<blockquote>" + bq.map(renderInlineRich).join("<br>") + "</blockquote>";
        continue;
      }
      if (/^\s*[-*]\s+/.test(l)) {
        flush();
        var ul = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          var item = lines[i].replace(/^\s*[-*]\s+/, "");
          var tk = /^\[([ xX])\]\s+(.*)$/.exec(item);
          if (tk) ul.push('<li class="md-task"><span class="md-chk' + (tk[1] !== " " ? " on" : "") + '" aria-hidden="true"></span><span>' + renderInlineRich(tk[2]) + "</span></li>");
          else ul.push("<li>" + renderInlineRich(item) + "</li>");
          i++;
        }
        html += "<ul>" + ul.join("") + "</ul>";
        continue;
      }
      if (/^\s*\d+\.\s+/.test(l)) {
        flush();
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + renderInlineRich(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>");
          i++;
        }
        html += "<ol>" + ol.join("") + "</ol>";
        continue;
      }
      if (!l.trim()) { flush(); i++; continue; }
      var h = /^(#{1,4})\s+(.*?)\s*#*$/.exec(l);
      if (h) { flush(); var lv = h[1].length; html += "<h" + lv + ">" + renderInlineRich(h[2]) + "</h" + lv + ">"; i++; continue; }
      para.push(l); i++;
    }
    flush();
    return html;
  }

  function renderMermaidBlock(src) {
    function escM(s) {
      return String(s || "").replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function cleanNodeLabel(raw) {
      var s = String(raw || "").trim();
      var m = /^[A-Za-z0-9_-]+\s*(?:\["([^"]*)"\]|\("([^"]*)"\)|\[\("([^"]*)"\)\]|\["?([^"\]]*)"?\]|\(([^)]*)\))/.exec(s);
      var lbl = m ? (m[1] || m[2] || m[3] || m[4] || m[5] || s) : s;
      return lbl.replace(/<br\s*\/?>/gi, " · ").replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "").trim();
    }
    var lines = String(src || "").split("\n").map(function (l) { return l.trim(); }).filter(function (l) {
      return l && !/^(?:flowchart|graph)\b/i.test(l);
    });
    var defs = {};
    lines.forEach(function (l) {
      var re = /([A-Za-z0-9_-]+)\s*(?:\["([^"]*)"\]|\[\("([^"]*)"\)\]|\("([^"]*)"\)|\[([^\]]*)\])/g;
      var m;
      while ((m = re.exec(l)) !== null) {
        var id = m[1];
        var txt = (m[2] || m[3] || m[4] || m[5] || id).replace(/<br\s*\/?>/gi, "\n").replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "").trim();
        defs[id] = txt;
      }
    });
    var edges = [];
    lines.forEach(function (l) {
      var em = /^([A-Za-z0-9_-]+)(?:\s*(?:\[[^\]]*\]|\([^)]*\)))?\s*(?:<-->|-->|-\.->|==>)\s*(?:\|"([^"]*)"\||\|([^|]*)\|)?\s*([A-Za-z0-9_-]+)/.exec(l);
      if (em) {
        var fromId = em[1], edgeLbl = (em[2] || em[3] || "").trim(), toId = em[4];
        var bi = l.indexOf("<-->") >= 0;
        var dashed = l.indexOf("-.->") >= 0;
        edges.push({ from: fromId, to: toId, label: edgeLbl, bi: bi, dashed: dashed });
      }
    });
    if (!edges.length) {
      return '<pre><code>' + escM(src) + '</code></pre>';
    }
    var rowsHtml = edges.map(function (e) {
      var fTxt = (defs[e.from] || cleanNodeLabel(e.from)).split("\n");
      var tTxt = (defs[e.to] || cleanNodeLabel(e.to)).split("\n");
      var arrow = e.bi ? "\u2194" : (e.dashed ? "\u21e2" : "\u2192");
      return '<div class="kit-mmd-row">' +
        '<div class="kit-mmd-node"><b>' + escM(fTxt[0]) + '</b>' + (fTxt[1] ? '<span>' + escM(fTxt.slice(1).join(" · ")) + '</span>' : '') + '</div>' +
        '<div class="kit-mmd-edge"><span class="arr">' + arrow + '</span>' + (e.label ? '<span class="elbl">' + escM(e.label) + '</span>' : '') + '</div>' +
        '<div class="kit-mmd-node"><b>' + escM(tTxt[0]) + '</b>' + (tTxt[1] ? '<span>' + escM(tTxt.slice(1).join(" · ")) + '</span>' : '') + '</div>' +
      '</div>';
    }).join("");
    return '<div class="kit-mmd-box">' + rowsHtml + '</div>';
  }

  function initRepoDocBrowser() {
    var tools = document.querySelector("header.top .tools");
    if (!tools || document.getElementById("kit-docs-btn")) return;
    try {
      var savedZ = Number(localStorage.getItem("kit_docs_zoom") || "100");
      if (savedZ >= 60 && savedZ <= 240) _kitDocsState.zoom = savedZ;
    } catch (e) {}
    var SVG_BOOK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>';
    var SVG_PIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>';
    var SVG_CMT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>';
    var SVG_CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    var SVG_PHONE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>';
    var SVG_DESK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
    var SVG_CODE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="14" y1="4" x2="10" y2="20"/></svg>';
    var SVG_GAL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>';
    var SVG_DRAW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';
    var SVG_UNDO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>';
    var SVG_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
    var SVG_SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
    var SVG_CMP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="8" height="16" rx="1"/><rect x="13" y="4" width="8" height="16" rx="1"/></svg>';
    var SVG_CHK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
    if (!document.getElementById("kit-docs-css")) {
      var st = document.createElement("style");
      st.id = "kit-docs-css";
      st.textContent =
        "html,body{overscroll-behavior-x:none;}" +
        ".kit-doc-link{color:var(--ember);text-decoration:underline dotted;cursor:pointer;border-radius:4px;padding:0 2px;}" +
        ".kit-doc-link:hover{background:var(--sunk);}" +
        "#kit-docs-btn{position:relative;}" +
        "#kit-docs-btn .newdot{position:absolute;top:-2px;right:-2px;margin:0;width:9px;height:9px;border:2px solid var(--bg);}" +
        ".kit-docs-modal{--docs-scale:1;position:fixed;left:max(24px,calc(50% - 490px));top:32px;width:min(980px,calc(100vw - 48px));height:min(760px,calc(100vh - 64px));z-index:55;background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 64px rgba(0,0,0,.42);display:grid;grid-template-rows:auto 1fr;overflow:hidden;overscroll-behavior:none;}" +
        ".kit-docs-modal[hidden]{display:none !important;}" +
        ".kit-docs-hdr{display:flex;align-items:center;gap:6px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--sunk);user-select:none;min-width:0;max-width:100%;overflow:hidden;}" +
        ".kit-docs-hdr b{font-family:var(--display);font-size:1.08rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:0 1 auto;min-width:0;}" +
        ".kit-docs-hdr .hsub{font-size:.76rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:4px;flex:1 1 0;min-width:0;}" +
        ".kit-docs-hdr .sp{margin-left:auto;flex:none;}" +
        ".kit-docs-hdr button{flex:none;}" +
        ".kit-docs-stage{position:relative;min-height:0;min-width:0;max-width:100%;overflow:hidden;touch-action:pan-y;}" +
        ".kit-docs-view{height:100%;width:100%;max-width:100%;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:calc(22px * var(--docs-scale, 1)) calc(44px * var(--docs-scale, 1)) calc(48px * var(--docs-scale, 1));min-width:0;box-sizing:border-box;touch-action:pan-y;}" +
        ".kit-docs-md{margin:0 auto;max-width:min(74ch,100%);width:100%;min-width:0;font-size:calc(.98rem * var(--docs-scale, 1)) !important;line-height:1.65;overflow-wrap:anywhere;word-break:break-word;box-sizing:border-box;}" +
        ".kit-docs-md h1{font-size:1.65em !important;overflow-wrap:anywhere;}" +
        ".kit-docs-md h2{font-size:1.34em !important;overflow-wrap:anywhere;}" +
        ".kit-docs-md h3{font-size:1.12em !important;overflow-wrap:anywhere;}" +
        ".kit-docs-md h4{font-size:1em !important;overflow-wrap:anywhere;}" +
        ".kit-docs-md pre{font-size:.86em !important;padding:calc(12px * var(--docs-scale, 1)) calc(14px * var(--docs-scale, 1));max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;box-sizing:border-box;}" +
        ".kit-docs-md .tbl{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;}" +
        ".kit-docs-md table{font-size:.9em !important;}" +
        ".kit-docs-md img{max-width:100%;height:auto;border-radius:8px;vertical-align:middle;margin:2px;}" +
        ".kit-docs-md p[style*='text-align:center'] img{display:inline-block;margin:3px 4px;}" +
        ".kit-docs-md .kit-sec-hit{background:color-mix(in srgb, var(--ember) 18%, transparent);border-radius:6px;padding:2px 6px;}" +
        ".kit-mmd-box{display:flex;flex-direction:column;gap:8px;margin:1em 0;padding:14px;border-radius:12px;background:var(--sunk);border:1px solid var(--line);max-width:100%;overflow-x:auto;box-sizing:border-box;}" +
        ".kit-mmd-row{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;gap:10px;min-width:0;}" +
        ".kit-mmd-node{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 12px;display:flex;flex-direction:column;gap:2px;min-width:0;box-shadow:var(--shadow);}" +
        ".kit-mmd-node b{font-size:.88em;color:var(--ink);overflow-wrap:anywhere;}" +
        ".kit-mmd-node span{font-size:.76em;color:var(--muted);overflow-wrap:anywhere;}" +
        ".kit-mmd-edge{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:var(--ember);font-family:var(--mono);font-size:.75em;min-width:40px;text-align:center;}" +
        ".kit-mmd-edge .arr{font-size:1.25em;font-weight:800;line-height:1;}" +
        ".kit-mmd-edge .elbl{color:var(--muted);max-width:130px;overflow-wrap:anywhere;}" +
        ".kit-docs-drawer{position:absolute;top:0;bottom:0;z-index:6;background:color-mix(in srgb, var(--surface) 95%, var(--sunk));backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);box-shadow:0 10px 32px rgba(0,0,0,.22);overflow-y:auto;overscroll-behavior:contain;transition:transform .18s cubic-bezier(.22,.85,.32,1),opacity .15s ease;touch-action:pan-y;}" +
        ".kit-docs-list{left:0;width:min(300px,84%);border-right:1px solid var(--line);padding:10px;display:flex;flex-direction:column;gap:8px;transform:translateX(-100%);opacity:0;pointer-events:none;}" +
        ".kit-docs-list.open{transform:translateX(0);opacity:1;pointer-events:auto;}" +
        ".kit-docs-list input{width:100%;font:inherit;font-size:.86rem;padding:6px 10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--ink);box-sizing:border-box;}" +
        ".kit-docs-outline{right:0;width:min(256px,78%);border-left:1px solid var(--line);padding:14px 10px;display:flex;flex-direction:column;gap:2px;transform:translateX(100%);opacity:0;pointer-events:none;}" +
        ".kit-docs-outline.open{transform:translateX(0);opacity:1;pointer-events:auto;}" +
        ".kit-docs-edge-hint{position:absolute;top:50%;transform:translateY(-50%);width:5px;height:52px;border-radius:99px;background:color-mix(in srgb, var(--ink) 38%, var(--line));opacity:.75;pointer-events:none;z-index:8;transition:opacity .15s;}" +
        ".kit-docs-edge-hint.l{left:4px;} .kit-docs-edge-hint.r{right:4px;}" +
        ".kit-docs-grp{display:flex;flex-direction:column;gap:2px;}" +
        ".kit-docs-grp summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;padding:4px 6px;font-size:.73rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);border-radius:6px;user-select:none;}" +
        ".kit-docs-grp summary::-webkit-details-marker{display:none;}" +
        ".kit-docs-grp summary:hover{color:var(--ink);background:var(--sunk);}" +
        ".kit-docs-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:2px;border-radius:8px;border:1.5px solid transparent;transition:background .12s,border-color .12s;}" +
        ".kit-docs-row:hover,.kit-docs-row.on{background:var(--sunk);}" +
        ".kit-docs-row.scrub-hi{background:color-mix(in srgb, var(--ember) 18%, var(--sunk)) !important;border-color:var(--ember) !important;}" +
        ".kit-docs-item{text-align:left;background:none;border:0;padding:6px 8px;cursor:pointer;color:var(--ink);font-size:.83rem;display:flex;flex-direction:column;gap:2px;min-width:0;}" +
        ".kit-docs-item .dp{display:flex;align-items:center;gap:5px;font-weight:600;font-size:.84rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
        ".kit-docs-item .dp .newdot{width:7px;height:7px;margin:0;flex:none;}" +
        ".kit-docs-item .dm{font-size:.71rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
        ".kit-docs-pin{background:none;border:0;width:28px;height:28px;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;color:var(--muted);cursor:pointer;opacity:0;transition:opacity .12s,color .12s,background .12s;margin-right:3px;}" +
        ".kit-docs-row:hover .kit-docs-pin,.kit-docs-pin.on{opacity:1;}" +
        ".kit-docs-pin.on{color:var(--ember);}" +
        ".kit-docs-pin:hover{background:var(--surface);color:var(--ink);}" +
        ".kit-docs-pin svg{width:14px;height:14px;}" +
        ".kit-docs-outline .otitle{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:0 8px 8px;}" +
        ".kit-docs-outline button{text-align:left;background:none;border:0;border-left:2px solid transparent;padding:5px 8px;border-radius:0 6px 6px 0;cursor:pointer;color:var(--muted);font-size:.8rem;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:background .12s,color .12s,border-color .12s;}" +
        ".kit-docs-outline button:hover{color:var(--ink);background:var(--sunk);}" +
        ".kit-docs-outline button.active{color:var(--ember);border-left-color:var(--ember);font-weight:700;background:color-mix(in srgb, var(--ember) 8%, transparent);}" +
        ".kit-docs-outline button.scrub-hi{color:var(--ember) !important;border-left-color:var(--ember) !important;font-weight:800;background:color-mix(in srgb, var(--ember) 20%, var(--sunk)) !important;}" +
        ".kit-docs-outline button.lvl-2{padding-left:16px;}" +
        ".kit-docs-outline button.lvl-3,.kit-docs-outline button.lvl-4{padding-left:26px;font-size:.76rem;}" +
        ".kit-docs-hdr .kit-icon-btn.on{background:color-mix(in srgb, var(--ember) 18%, var(--surface));border-color:var(--ember);color:var(--ember);}" +
        ".kit-docs-view.media-mode{padding:0 !important;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;background:var(--sunk);overflow:auto;}" +
        ".kit-docs-iframe-wrap{width:100%;height:100%;display:flex;align-items:center;justify-content:center;padding:0;box-sizing:border-box;flex:1 1 auto;min-height:0;}" +
        ".kit-docs-iframe-wrap.phone-view{padding:14px;}" +
        ".kit-docs-iframe{width:100%;height:100%;border:0;background:#fff;display:block;}" +
        ".kit-docs-iframe-wrap.phone-view .kit-docs-iframe{width:min(390px,100%);height:min(760px,100%);border-radius:22px;border:4px solid var(--line);box-shadow:0 16px 40px rgba(0,0,0,.28);}" +
        ".kit-docs-code-wrap{width:100%;height:100%;overflow:auto;padding:14px 18px 40px;box-sizing:border-box;background:var(--surface);}" +
        ".kit-docs-code{margin:0;font-family:var(--mono);font-size:calc(.82rem * var(--docs-scale, 1));line-height:1.55;color:var(--ink);white-space:pre;tab-size:2;}" +
        ".kit-docs-code .cln{display:inline-block;min-width:3.2em;padding-right:12px;margin-right:10px;border-right:1px solid var(--line);color:var(--muted);text-align:right;user-select:none;}" +
        ".kit-docs-img-stage{flex:1 1 auto;width:100%;min-height:100%;display:flex;align-items:center;justify-content:center;padding:18px;box-sizing:border-box;position:relative;overflow:auto;}" +
        ".kit-docs-img-wrap{position:relative;display:inline-flex;align-items:center;justify-content:center;max-width:100%;line-height:0;transform-origin:center center;}" +
        ".kit-docs-img-wrap img{display:block;max-width:100%;max-height:calc(100vh - 140px);width:auto;height:auto;border-radius:8px;box-shadow:0 10px 30px rgba(0,0,0,.22);user-select:none;-webkit-user-drag:none;}" +
        ".kit-docs-img-wrap img[src*='.svg']{width:min(760px,84vw);height:auto;}" +
        ".kit-docs-draw-cv{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair;touch-action:none;border-radius:8px;z-index:3;}" +
        ".kit-docs-draw-bar{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);z-index:9;display:flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;background:color-mix(in srgb, var(--surface) 94%, var(--sunk));border:1px solid var(--line);box-shadow:0 10px 28px rgba(0,0,0,.3);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}" +
        ".kit-draw-dot{width:18px;height:18px;border-radius:50%;border:2px solid transparent;padding:0;cursor:pointer;flex:none;}" +
        ".kit-draw-dot.on{border-color:var(--ink);transform:scale(1.15);}" +
        ".kit-docs-gal-grid{width:100%;padding:14px 16px 76px;box-sizing:border-box;display:grid;grid-template-columns:repeat(auto-fill,minmax(156px,1fr));gap:10px;align-content:start;}" +
        ".kit-docs-gal-card{position:relative;border-radius:10px;overflow:hidden;border:2px solid var(--line);background:var(--surface);cursor:pointer;display:flex;flex-direction:column;transition:border-color .12s,transform .12s;user-select:none;-webkit-user-select:none;}" +
        ".kit-docs-gal-card:hover{border-color:color-mix(in srgb, var(--ember) 60%, var(--line));transform:translateY(-1px);}" +
        ".kit-docs-gal-card.sel{border-color:var(--ember);box-shadow:0 0 0 2px color-mix(in srgb, var(--ember) 28%, transparent);}" +
        ".kit-docs-gal-card .gthumb{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;background:var(--sunk);}" +
        ".kit-docs-gal-card .gcap{padding:5px 7px;font-size:.73rem;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
        ".kit-docs-gal-card .gchk{position:absolute;top:6px;left:6px;width:22px;height:22px;border-radius:50%;border:2px solid rgba(255,255,255,.9);background:rgba(15,20,30,.48);color:#fff;display:inline-flex;align-items:center;justify-content:center;padding:0;opacity:0;transition:opacity .12s,background .12s;cursor:pointer;z-index:2;}" +
        ".kit-docs-gal-card:hover .gchk,.kit-docs-gal-card.sel .gchk,.kit-docs-gal-grid.selecting .gchk{opacity:1;}" +
        ".kit-docs-gal-card.sel .gchk{background:var(--ember);border-color:var(--ember);}" +
        ".kit-docs-gal-card .gchk svg{width:13px;height:13px;}" +
        ".kit-docs-gal-bar{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);z-index:9;display:flex;align-items:center;gap:8px;padding:7px 12px;border-radius:999px;background:color-mix(in srgb, var(--surface) 95%, var(--sunk));border:1px solid var(--line);box-shadow:0 12px 32px rgba(0,0,0,.32);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);white-space:nowrap;}" +
        ".kit-docs-gal-bar .gcnt{font-size:.8rem;font-weight:700;color:var(--ink);padding:0 4px;}" +
        ".kit-docs-cmp-grid{width:100%;min-height:100%;padding:14px;box-sizing:border-box;display:grid;gap:12px;align-items:stretch;}" +
        ".kit-docs-cmp-item{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;min-width:0;}" +
        ".kit-docs-cmp-item .cmphdr{padding:6px 10px;font-size:.76rem;font-weight:700;color:var(--muted);border-bottom:1px solid var(--line);background:var(--sunk);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
        ".kit-docs-cmp-item .cmpbody{flex:1 1 auto;display:flex;align-items:center;justify-content:center;padding:10px;overflow:auto;background:var(--sunk);}" +
        ".kit-docs-cmp-item img{max-width:100%;max-height:100%;width:auto;height:auto;border-radius:6px;display:block;}" +
        ".kit-docs-edge-strip{position:absolute;top:0;bottom:0;width:24px;z-index:5;background:transparent;pointer-events:none;}" +
        ".kit-docs-edge-strip.l{left:0;} .kit-docs-edge-strip.r{right:0;}" +
        ".kit-docs-stage.iframe-active .kit-docs-edge-strip{pointer-events:auto;touch-action:none;}" +
        ".kit-docs-sim-stage{width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;background:var(--sunk);touch-action:none;isolation:isolate;}" +
        ".kit-docs-sim-frame{width:1280px;height:800px;flex:none;transform-origin:center center;border-radius:14px;overflow:hidden;border:3px solid var(--line);box-shadow:0 16px 42px rgba(0,0,0,.32);background:#fff;backface-visibility:hidden;-webkit-backface-visibility:hidden;transform-style:flat;will-change:transform;}" +
        ".kit-docs-sim-frame iframe{width:1280px;height:800px;border:0;display:block;background:#fff;backface-visibility:hidden;-webkit-backface-visibility:hidden;}" +
        ".kit-docs-modal .kit-docs-hdr{transition:background .35s ease,border-color .35s ease;}" +
        ".kit-docs-modal .kit-docs-hdr b,.kit-docs-modal .kit-docs-hdr .hsub{transition:opacity .35s ease;}" +
        ".kit-docs-modal .kit-docs-hdr .kit-icon-btn{transition:opacity .35s ease,background .35s ease,border-color .35s ease,color .35s ease;}" +
        ".kit-docs-modal.photo-mode{grid-template-rows:1fr;}" +
        ".kit-docs-modal.photo-mode .kit-docs-hdr{position:absolute;top:0;left:0;right:0;z-index:10;background:color-mix(in srgb, var(--sunk) 86%, transparent);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}" +
        ".kit-docs-modal.photo-mode .kit-docs-stage{height:100%;}" +
        ".kit-docs-modal.photo-idle .kit-docs-hdr{background:transparent !important;border-bottom-color:transparent !important;backdrop-filter:none !important;-webkit-backdrop-filter:none !important;}" +
        ".kit-docs-modal.photo-idle .kit-docs-hdr b,.kit-docs-modal.photo-idle .kit-docs-hdr .hsub{opacity:0 !important;pointer-events:none;}" +
        ".kit-docs-modal.photo-idle .kit-docs-hdr .kit-icon-btn{background:transparent !important;border-color:transparent !important;box-shadow:none !important;color:var(--ink) !important;opacity:.45;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45));}" +
        ".kit-docs-modal.photo-idle .kit-docs-hdr .kit-icon-btn:hover{opacity:1;}" +
        ".kit-inline-cmp{display:grid;gap:10px;margin:10px 0 14px;width:100%;align-items:start;}" +
        ".kit-inline-cmp-item{background:var(--sunk);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;min-width:0;}" +
        ".kit-inline-cmp-item .icbody{overflow:auto;display:flex;align-items:center;justify-content:center;padding:8px;min-height:100px;}" +
        ".kit-inline-cmp-item img{max-width:100%;height:auto;border-radius:6px;display:block;cursor:zoom-in;transform-origin:center center;transition:transform .08s ease;}" +
        ".kit-inline-cmp-item .iccap{padding:5px 8px;font-size:.76rem;color:var(--muted);border-top:1px solid var(--line);background:var(--surface);overflow-wrap:anywhere;}" +
        "#p-imgs .grp-row{display:grid !important;grid-template-columns:repeat(var(--n,2),minmax(0,1fr)) !important;gap:12px !important;align-items:start;}" +
        "#p-imgs .grp-row .pic{min-width:0;width:100%;}" +
        "#p-imgs .grp-row .pic img{max-width:100% !important;width:100%;height:auto;object-fit:contain;}" +
        ".kit-inline-embed{margin:10px 0 14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden;box-shadow:0 4px 14px rgba(0,0,0,.08);}" +
        ".kit-inline-embed-hdr{display:flex;align-items:center;gap:6px;padding:6px 10px;background:var(--sunk);border-bottom:1px solid var(--line);min-width:0;}" +
        ".kit-inline-embed-hdr .iettl{font-family:var(--mono);font-size:.76rem;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 0;min-width:0;}" +
        ".kit-inline-embed-hdr .kit-icon-btn{width:26px;height:26px;padding:0;flex:none;}" +
        ".kit-inline-embed-hdr .kit-icon-btn svg{width:14px;height:14px;}" +
        ".kit-inline-embed-body{--docs-scale:.92;max-height:380px;overflow:auto;overscroll-behavior:contain;padding:12px 16px;background:var(--surface);box-sizing:border-box;}" +
        ".kit-inline-embed-body.media{padding:0;background:var(--sunk);display:flex;align-items:center;justify-content:center;}" +
        ".kit-inline-embed-body iframe{width:100%;height:340px;border:0;display:block;background:#fff;}" +
        ".kit-inline-embed-body.phone iframe{width:100%;height:100%;margin:0;border-radius:16px;border:3px solid var(--line);box-sizing:border-box;}" +
        ".kit-inline-embed-body img{max-width:100%;max-height:360px;width:auto;height:auto;display:block;margin:8px auto;border-radius:6px;cursor:zoom-in;}" +
        ".kit-inline-embed.is-img{position:relative;}" +
        ".kit-inline-embed.is-img .kit-inline-embed-hdr{position:absolute;top:0;left:0;right:0;z-index:4;background:transparent !important;border-bottom-color:transparent !important;pointer-events:none;}" +
        ".kit-inline-embed.is-img .kit-inline-embed-hdr .iettl{opacity:0;pointer-events:none;}" +
        ".kit-inline-embed.is-img .kit-inline-embed-hdr .kit-icon-btn{pointer-events:auto;background:transparent !important;border-color:transparent !important;box-shadow:none !important;color:var(--ink) !important;opacity:.45;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45));}" +
        ".kit-inline-embed.is-img .kit-inline-embed-hdr .kit-icon-btn:hover{opacity:1;}" +
        ".pics-head .size{display:none !important;}" +
        "#p-imgs .pic{position:relative;overflow:hidden;border-radius:8px;}" +
        "#p-imgs .pic .pic-side .pic-form,#p-imgs .pic .pic-side .n,#p-imgs .pic .pic-side .cap{display:none !important;}" +
        "#p-imgs .pic .pic-side .pic-cmts:empty{display:none !important;}" +
        ".kit-pic-hdr{position:absolute;top:0;left:0;right:0;z-index:5;display:flex;align-items:center;gap:4px;padding:6px 8px;background:color-mix(in srgb, var(--sunk) 86%, transparent);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);transition:background .35s ease,border-color .35s ease;pointer-events:auto;max-width:100%;box-sizing:border-box;overflow:hidden;}" +
        ".kit-pic-hdr .pttl{font-size:.76rem;font-weight:700;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 0;min-width:0;transition:opacity .35s ease;}" +
        ".kit-pic-hdr button{width:28px;height:28px;padding:0;border:0 !important;background:transparent !important;box-shadow:none !important;color:var(--ink);opacity:.85;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex:none;transition:opacity .35s ease,color .2s;}" +
        ".kit-pic-hdr button svg{width:15px;height:15px;}" +
        ".kit-pic-hdr.idle{background:transparent !important;border-bottom-color:transparent !important;backdrop-filter:none !important;-webkit-backdrop-filter:none !important;pointer-events:none;}" +
        ".kit-pic-hdr.idle .pttl{opacity:0 !important;pointer-events:none;}" +
        ".kit-pic-hdr.idle button{pointer-events:auto;opacity:.45;filter:drop-shadow(0 1px 2px rgba(0,0,0,.5));}" +
        ".kit-pic-hdr.idle button:hover{opacity:1;}" +
        ".kit-pic-hdr button.liked{color:var(--good) !important;opacity:.92;}" +
        ".kit-tl-win{position:fixed;left:max(16px,calc(50% - 340px));top:32px;width:min(680px,calc(100vw - 32px));height:min(720px,calc(100vh - 64px));z-index:58;background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:0 24px 64px rgba(0,0,0,.42);display:flex;flex-direction:column;overflow:hidden;overscroll-behavior:none;}" +
        ".kit-tl-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line);background:var(--sunk);user-select:none;flex:none;min-width:0;}" +
        ".kit-tl-hdr b{font-family:var(--display);font-size:1.06rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 0;min-width:0;}" +
        ".kit-tl-hdr .tlsub{font-size:.76rem;color:var(--muted);white-space:nowrap;flex:none;}" +
        ".kit-tl-body{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:14px 18px 24px;display:flex;flex-direction:column;gap:4px;}" +
        ".kit-tl-item{position:relative;padding:9px 12px 9px 30px;border-radius:10px;border:1px solid transparent;transition:background .16s ease,border-color .16s ease;cursor:default;}" +
        ".kit-tl-item::before{content:'';position:absolute;left:13px;top:24px;bottom:-8px;width:2px;background:var(--line);opacity:.65;}" +
        ".kit-tl-item:last-child::before{display:none;}" +
        ".kit-tl-item.open{background:var(--sunk);border-color:var(--line);}" +
        ".kit-tl-dot{position:absolute;left:8px;top:14px;width:12px;height:12px;border-radius:50%;background:var(--st-c,var(--muted));box-shadow:0 0 0 3px var(--surface);cursor:pointer;border:0;padding:0;}" +
        ".kit-tl-dot.st-running{animation:kitTlBlink 1.3s ease-in-out infinite;box-shadow:0 0 10px var(--info);}" +
        ".kit-tl-dot.st-default{animation:kitTlGlow 2.2s ease-in-out infinite;box-shadow:0 0 8px var(--question);}" +
        ".kit-tl-dot.st-you{box-shadow:0 0 9px var(--todo);}" +
        "@keyframes kitTlBlink{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.82)}}" +
        "@keyframes kitTlGlow{0%,100%{opacity:1;box-shadow:0 0 4px var(--question)}50%{opacity:.65;box-shadow:0 0 12px var(--question)}}" +
        ".kit-tl-head{display:flex;align-items:baseline;gap:8px;min-width:0;}" +
        ".kit-tl-part{font-family:var(--mono);font-size:.73rem;font-weight:700;color:var(--st-c,var(--muted));flex:none;}" +
        ".kit-tl-title{font-weight:700;font-size:.93rem;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 0;min-width:0;}" +
        ".kit-tl-item.is-done .kit-tl-title{opacity:.72;}" +
        ".kit-tl-item.is-later .kit-tl-head,.kit-tl-item.is-later .kit-tl-verdict{opacity:.52;transition:opacity .16s ease;}" +
        ".kit-tl-item.is-later.open .kit-tl-head,.kit-tl-item.is-later.open .kit-tl-verdict{opacity:.95;}" +
        ".kit-tl-item.is-later::before{border-left:2px dashed var(--line);background:transparent;width:0;}" +
        ".kit-tl-verdict{font-size:.82rem;color:var(--muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
        ".kit-tl-item.open .kit-tl-verdict{white-space:normal;color:var(--ink);}" +
        ".kit-tl-fold{display:grid;grid-template-rows:0fr;transition:grid-template-rows .2s ease,margin-top .2s ease;margin-top:0;}" +
        ".kit-tl-item.open .kit-tl-fold{grid-template-rows:1fr;margin-top:8px;}" +
        "@media (hover:hover) and (pointer:fine){" +
          ".kit-tl-item:hover{background:var(--sunk);border-color:var(--line);}" +
          ".kit-tl-item.is-later:hover .kit-tl-head,.kit-tl-item.is-later:hover .kit-tl-verdict{opacity:.95;}" +
          ".kit-tl-item:hover .kit-tl-verdict{white-space:normal;color:var(--ink);}" +
          ".kit-tl-item:hover .kit-tl-fold{grid-template-rows:1fr;margin-top:8px;}" +
        "}" +
        ".kit-tl-fold-in{overflow:hidden;display:flex;flex-direction:column;gap:6px;font-size:.79rem;}" +
        ".kit-tl-sec{display:grid;grid-template-columns:76px minmax(0,1fr);gap:8px;align-items:baseline;padding-top:4px;border-top:1px dashed color-mix(in srgb, var(--line) 70%, transparent);}" +
        ".kit-tl-sec .k{font-size:.69rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);}" +
        ".kit-tl-sec .v{color:var(--ink);min-width:0;overflow-wrap:anywhere;display:flex;flex-wrap:wrap;gap:5px;align-items:center;}" +
        ".kit-tl-chip{font-family:var(--mono);font-size:.72rem;padding:2px 7px;border-radius:999px;background:var(--surface);border:1px solid var(--line);color:var(--ink);cursor:pointer;}" +
        ".kit-tl-chip:hover{border-color:var(--ember);color:var(--ember);}" +
        ".kit-tl-acts{display:flex;align-items:center;gap:6px;margin-top:4px;justify-content:flex-end;}" +
        ".kit-tl-acts .kit-icon-btn{width:28px;height:28px;padding:0;}" +
        ".kit-tl-acts .kit-icon-btn svg{width:14px;height:14px;}" +
        ":root:not([data-collab='1']) .kit-collab-only{display:none !important;}" +
        ".kit-assign-pill{font-family:var(--mono);font-size:.71rem;padding:1px 6px;border-radius:999px;background:var(--sunk);border:1px solid var(--line);color:var(--ink);}" +
        ".kit-work-pill{font-family:var(--mono);font-size:.71rem;padding:1px 6px;border-radius:999px;background:color-mix(in srgb, var(--info) 14%, var(--sunk));border:1px solid var(--info);color:var(--info);display:inline-flex;align-items:center;gap:4px;}" +
        ".kit-work-pill .wdot{width:6px;height:6px;border-radius:50%;background:var(--info);animation:kitTlBlink 1.3s ease-in-out infinite;}" +
        ".kit-assign-sel{font:inherit;font-size:.78rem;padding:3px 7px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);max-width:110px;}" +
        ".kit-mine-btn.on{background:var(--ember) !important;border-color:var(--ember) !important;color:var(--ember-ink) !important;}" +
        "#p-pics{width:100%;overflow:visible;}" +
        "#p-pics .pics-head,#p-imgs .grp > h4{max-width:72ch;width:100%;margin-left:0;margin-right:auto;box-sizing:border-box;}" +
        "#p-imgs.groups{overflow:visible !important;}" +
        "@media (min-width:1060px){" +
          "#p-imgs .grp-row,#p-imgs.gal,.kit-inline-embed{width:min(1080px,calc(100vw - 72px));margin-left:50%;transform:translateX(-50%);box-sizing:border-box;}" +
          ".kit-inline-embed-body iframe{height:440px;}" +
        "}" +
        "@media (hover:none),(pointer:coarse){" +
          ".kit-docs-pin{opacity:.48;}" +
        "}" +
        "@media (max-width:700px){" +
          ".kit-tl-win{inset:8px !important;left:8px !important;top:8px !important;width:calc(100vw - 16px) !important;height:calc(100dvh - 16px) !important;border-radius:14px;}" +
          ".sheet .bar{gap:4px !important;flex-wrap:nowrap !important;max-width:100%;box-sizing:border-box;overflow-x:auto;scrollbar-width:none;}" +
          ".sheet .bar .row{gap:4px !important;flex-wrap:nowrap !important;min-width:0;align-items:center;}" +
          ".sheet .bar .kit-icon-btn{width:30px;height:30px;flex:none;}" +
          ".sheet .bar .btn{padding:4px 7px;font-size:.8rem;white-space:nowrap;flex:none;}" +
          ".kit-docs-modal{inset:6px !important;left:6px !important;top:6px !important;width:calc(100vw - 12px) !important;height:calc(100dvh - 12px) !important;border-radius:14px;}" +
          ".kit-docs-hdr{padding:6px 8px;gap:4px;max-width:100%;box-sizing:border-box;}" +
          ".kit-docs-hdr b{font-size:.92rem;flex:1 1 0;min-width:0;}" +
          ".kit-docs-hdr .hsub{display:none !important;}" +
          ".kit-docs-hdr .sp{display:none !important;}" +
          ".kit-docs-hdr .kit-icon-btn{width:28px;height:28px;}" +
          ".kit-docs-hdr .kit-icon-btn svg{width:15px;height:15px;}" +
          ".kit-inline-embed-hdr{padding:5px 6px;gap:3px;}" +
          ".kit-inline-embed-hdr .kit-icon-btn{width:26px;height:26px;}" +
          ".kit-pic-hdr{padding:4px 5px;gap:2px;}" +
          ".kit-pic-hdr button{width:24px;height:24px;}" +
          ".kit-pic-hdr button svg{width:13px;height:13px;}" +
          ".kit-docs-view{padding:14px 16px 36px !important;}" +
          ".kit-mmd-row{grid-template-columns:1fr;gap:4px;text-align:center;}" +
          ".kit-mmd-edge .arr{transform:rotate(90deg);display:inline-block;}" +
        "}";
      document.head.appendChild(st);
    }
    var btn = document.createElement("button");
    btn.id = "kit-docs-btn"; btn.type = "button"; btn.className = "btn small kit-icon-btn";
    btn.title = "Repo docs & memory"; btn.setAttribute("aria-label", "Repo docs & memory");
    btn.innerHTML = SVG_BOOK;
    var askEl = document.getElementById("ask");
    if (askEl && askEl.parentNode === tools) tools.insertBefore(btn, askEl); else tools.appendChild(btn);

    var allDocs = [], _kitDocInstances = [], _kitDocSeq = 0;
    function repoKey() { return window.__KIT_ACTIVE_REPO__ || "primary"; }
    function getBaseline() {
      var k = "kit_docs_baseline:" + repoKey();
      var b = "";
      try { b = localStorage.getItem(k) || ""; } catch (e) {}
      if (!b) { b = new Date().toISOString(); try { localStorage.setItem(k, b); } catch (e) {} }
      return b;
    }
    function getDocSeen() {
      try { return JSON.parse(localStorage.getItem("kit_docs_seen:" + repoKey()) || "{}"); } catch (e) { return {}; }
    }
    function markDocSeen(path, mtime) {
      var s = getDocSeen(); s[path] = mtime || new Date().toISOString();
      try { localStorage.setItem("kit_docs_seen:" + repoKey(), JSON.stringify(s)); } catch (e) {}
      syncDocsBadge();
    }
    function isDocNew(d) {
      if (!d || !d.mtime) return false;
      var base = getBaseline(), seenAt = getDocSeen()[d.path] || "";
      return d.mtime > (seenAt > base ? seenAt : base);
    }
    function syncDocsBadge() {
      var dot = btn.querySelector(".newdot"), anyNew = allDocs.some(isDocNew);
      if (anyNew && !dot) { var nd = document.createElement("span"); nd.className = "newdot"; nd.title = "Updated repo documents"; btn.appendChild(nd); }
      else if (!anyNew && dot) dot.remove();
    }
    function fmtTime(iso) {
      if (!iso) return "";
      var t = Date.parse(iso); if (isNaN(t)) return iso;
      var diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
      var rel = diff < 60 ? "just now" : (diff < 3600 ? Math.floor(diff / 60) + "m ago" : (diff < 86400 ? Math.floor(diff / 3600) + "h ago" : Math.floor(diff / 86400) + "d ago"));
      var dt = new Date(t);
      return rel + " · " + dt.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + ", " + dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
    }
    function fmtKb(n) { return n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B"; }
    function shortName(path) { var parts = String(path || "").split("/"); return parts[parts.length - 1].replace(/\.md$/i, ""); }
    function loadDocsIndex(cb) {
      api("GET", "/_api/repo-docs").then(function (r) {
        allDocs = (r && r.docs) || []; getBaseline(); syncDocsBadge();
        _kitDocInstances.forEach(function (inst) { if (inst && inst.renderList) inst.renderList(); });
        if (cb) cb();
      }, function () {});
    }

    function createDocViewerWindow(isPrimary) {
      _kitDocSeq += 1;
      var seq = _kitDocSeq;
      var modal = document.createElement("section");
      modal.id = isPrimary ? "kit-docs-modal" : ("kit-docs-modal-" + seq);
      modal.className = "kit-docs-modal";
      modal.hidden = true;
      modal.setAttribute("role", "dialog"); modal.setAttribute("aria-label", "Repo docs & memory");
      modal.innerHTML =
        '<div class="kit-docs-hdr"><b class="k-title">Docs</b><span class="hsub k-sub"></span><span class="sp"></span>' +
        '<button type="button" class="btn small kit-icon-btn k-html-phone" title="Mobile view" aria-label="Mobile view" hidden>' + SVG_PHONE + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-html-desk" title="Desktop view" aria-label="Desktop view" hidden>' + SVG_DESK + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-html-code" title="View HTML code" aria-label="View HTML code" hidden>' + SVG_CODE + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-img-gal" title="Gallery view" aria-label="Gallery view" hidden>' + SVG_GAL + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-img-draw" title="Annotate and comment" aria-label="Annotate and comment" hidden>' + SVG_DRAW + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-vpin" title="Pin file" aria-label="Pin file" hidden>' + SVG_PIN + '</button>' +
        '<button type="button" class="btn small kit-icon-btn ember k-vcmt" title="Comment on this section" aria-label="Comment on this section" hidden>' + SVG_CMT + '</button>' +
        '<button type="button" class="btn small kit-icon-btn k-close" title="Close" aria-label="Close">' + SVG_CLOSE + '</button></div>' +
        '<div class="kit-docs-stage">' +
          '<div class="kit-docs-edge-strip l"></div><div class="kit-docs-edge-strip r"></div>' +
          '<span class="kit-docs-edge-hint l k-hint-l"></span><span class="kit-docs-edge-hint r k-hint-r" hidden></span>' +
          '<aside class="kit-docs-drawer kit-docs-list k-list"><input type="search" class="k-q" placeholder="Filter documents, HTML, images..."><div class="k-groups"></div></aside>' +
          '<div class="kit-docs-view k-view"><div class="note" style="padding-top:16px">Move pointer to the left edge to pick a document.</div></div>' +
          '<aside class="kit-docs-drawer kit-docs-outline k-outline"></aside>' +
        '</div>';
      document.body.appendChild(modal);

      var hdrEl = modal.querySelector(".kit-docs-hdr");
      var stageEl = modal.querySelector(".kit-docs-stage");
      var titleEl = modal.querySelector(".k-title");
      var subEl = modal.querySelector(".k-sub");
      var htmlPhoneBtn = modal.querySelector(".k-html-phone");
      var htmlDeskBtn = modal.querySelector(".k-html-desk");
      var htmlCodeBtn = modal.querySelector(".k-html-code");
      var imgGalBtn = modal.querySelector(".k-img-gal");
      var imgDrawBtn = modal.querySelector(".k-img-draw");
      var pinHdrBtn = modal.querySelector(".k-vpin");
      var cmtHdrBtn = modal.querySelector(".k-vcmt");
      var closeHdrBtn = modal.querySelector(".k-close");
      var qEl = modal.querySelector(".k-q");
      var listDrawer = modal.querySelector(".k-list");
      var groupsEl = modal.querySelector(".k-groups");
      var viewEl = modal.querySelector(".k-view");
      var outlineDrawer = modal.querySelector(".k-outline");
      var hintR = modal.querySelector(".k-hint-r");

      makeWindow(modal, hdrEl, { minW: 360, minH: 280, winKey: isPrimary ? "docs" : ("docs:" + seq), alsoKey: "docs" });
      if (isPrimary) {
        applySavedWindowGeom(modal, "docs", { minW: 400, minH: 300 });
      }
      syncDocsScale(modal);

      var activePath = "", activeKind = "md", activeViewMode = "view", htmlMode = "desktop";
      var activeHtmlUrl = "", activeHtmlSource = "", activeImgUrl = "", activeRepoOverride = undefined;
      var activeHeadings = [], leftTimer = null, rightTimer = null;
      var selectedHeadingId = "", scrollLockUntil = 0, activeDocMetaText = "";
      var localZoom = _kitDocsState.zoom || 100, savedDesktopWidth = 0;
      var galSelected = [], drawActive = false, activeImgDimKey = "", customImgList = null;
      var photoIdleTimer = null, htmlErrors = [], pausedIframe = null;

      function bringOrchAboveViewer() {
        _kitWinZ = Math.max(_kitWinZ || 60, window._kitTopZ || 60) + 2;
        window._kitTopZ = _kitWinZ;
        var sh = document.getElementById("sheet");
        if (sh) sh.style.zIndex = String(_kitWinZ);
      }
      function imgDimStorageKey(w, h) {
        if (!w || !h) return "";
        return "kit_img_zoom:" + (window.innerWidth <= 720 ? "phone" : "desk") + ":" + w + "x" + h;
      }
      function clearPhotoIdle() {
        clearTimeout(photoIdleTimer);
        modal.classList.remove("photo-idle");
      }
      function bumpPhotoActivity() {
        clearPhotoIdle();
        if (activeKind === "img" && activeViewMode === "view" && !drawActive && !modal.hidden) {
          photoIdleTimer = setTimeout(function () {
            if (activeKind === "img" && activeViewMode === "view" && !drawActive && !modal.hidden) {
              modal.classList.add("photo-idle");
            }
          }, 2200);
        }
      }
      modal.addEventListener("mousemove", bumpPhotoActivity, { passive: true });
      modal.addEventListener("touchstart", bumpPhotoActivity, { passive: true });
      modal.addEventListener("click", bumpPhotoActivity, { passive: true });

      function clearFloatingBars() {
        var oldBars = stageEl.querySelectorAll(".kit-docs-draw-bar, .kit-docs-gal-bar");
        for (var i = 0; i < oldBars.length; i++) oldBars[i].remove();
      }
      function updateHeaderModeButtons() {
        var isHtml = activeKind === "html" && activeViewMode === "view";
        var isImg = activeKind === "img" && activeViewMode === "view";
        var isGalOrCmp = activeViewMode === "gallery" || activeViewMode === "compare";
        stageEl.classList.toggle("iframe-active", isHtml && htmlMode !== "code");
        modal.classList.toggle("photo-mode", isImg && !drawActive);
        htmlPhoneBtn.hidden = !isHtml;
        htmlDeskBtn.hidden = !isHtml;
        htmlCodeBtn.hidden = !isHtml;
        htmlPhoneBtn.classList.toggle("on", isHtml && htmlMode === "mobile");
        htmlDeskBtn.classList.toggle("on", isHtml && htmlMode === "desktop");
        htmlCodeBtn.classList.toggle("on", isHtml && htmlMode === "code");
        imgGalBtn.hidden = !(isImg || isGalOrCmp);
        imgGalBtn.classList.toggle("on", activeViewMode === "gallery");
        imgDrawBtn.hidden = !isImg;
        imgDrawBtn.classList.toggle("on", drawActive);
        pinHdrBtn.hidden = !activePath || isGalOrCmp || !!customImgList;
        cmtHdrBtn.hidden = !(activePath || (activeViewMode === "compare" && galSelected.length));
        if (isImg && !drawActive) bumpPhotoActivity();
        else clearPhotoIdle();
      }
      function applyLocalZoom(nextZoom, skipUrl) {
        var z = Math.max(55, Math.min(300, Math.round(nextZoom)));
        localZoom = z;
        if (activeKind === "img" && activeViewMode === "view" && activeImgDimKey) {
          try { localStorage.setItem(activeImgDimKey, String(z)); } catch (e) {}
        } else if (isPrimary && activeKind !== "img") {
          _kitDocsState.zoom = z;
          try { localStorage.setItem("kit_docs_zoom", String(z)); } catch (e) {}
        }
        var scaleVal = z / 100;
        modal.style.setProperty("--docs-scale", String(scaleVal));
        var imgWrap = viewEl.querySelector(".kit-docs-img-wrap");
        if (imgWrap) {
          imgWrap.style.transform = scaleVal === 1 ? "" : ("scale(" + scaleVal + ")");
        }
        if (!skipUrl && isPrimary) syncUrlWorkspaceState();
      }
      function setDocsZoom(nextZoom, skipUrl) { applyLocalZoom(nextZoom, skipUrl); }
      modal.addEventListener("wheel", function (e) {
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          e.stopPropagation();
          var step = Math.max(2, Math.min(18, Math.abs(e.deltaY) * 0.8));
          applyLocalZoom(localZoom + (e.deltaY < 0 ? step : -step));
        }
      }, { passive: false, capture: true });
      var gestureBaseZoom = 100;
      modal.addEventListener("gesturestart", function (e) {
        e.preventDefault();
        gestureBaseZoom = localZoom || 100;
      }, { passive: false });
      modal.addEventListener("gesturechange", function (e) {
        e.preventDefault();
        if (e.scale && e.scale > 0) applyLocalZoom(gestureBaseZoom * e.scale, true);
      }, { passive: false });
      modal.addEventListener("gestureend", function () { if (isPrimary) syncUrlWorkspaceState(); }, { passive: false });

      /* Pointer-aware edge drawers: left edge -> document list, right edge -> document outline */
      function setLeft(open) {
        clearTimeout(leftTimer);
        if (!open && (!activePath && activeViewMode === "view" || document.activeElement === qEl)) return;
        listDrawer.classList.toggle("open", !!open);
      }
      function setRight(open) {
        clearTimeout(rightTimer);
        if (open && activeHeadings.length < 1) return;
        outlineDrawer.classList.toggle("open", !!open);
        if (open) syncOutlineActive();
      }
    stageEl.addEventListener("mousemove", function (e) {
      if (modal.dataset.kitDragging || touchGesture || (window.matchMedia && window.matchMedia("(pointer: coarse)").matches)) return;
      var r = stageEl.getBoundingClientRect();
      var x = e.clientX - r.left, edge = 28;
      if (x <= edge) setLeft(true);
      else if (listDrawer.classList.contains("open") && !listDrawer.contains(e.target) && x > listDrawer.offsetWidth + 14) {
        clearTimeout(leftTimer); leftTimer = setTimeout(function () { setLeft(false); }, 180);
      }
      if (activeHeadings.length >= 1) {
        if (x >= r.width - edge) setRight(true);
        else if (outlineDrawer.classList.contains("open") && !outlineDrawer.contains(e.target) && x < r.width - outlineDrawer.offsetWidth - 14) {
          clearTimeout(rightTimer); rightTimer = setTimeout(function () { setRight(false); }, 180);
        }
      }
    });
    stageEl.addEventListener("mouseleave", function () {
      if (touchGesture || (window.matchMedia && window.matchMedia("(pointer: coarse)").matches)) return;
      clearTimeout(leftTimer); leftTimer = setTimeout(function () { setLeft(false); }, 220);
      clearTimeout(rightTimer); rightTimer = setTimeout(function () { setRight(false); }, 220);
    });
    listDrawer.addEventListener("mouseenter", function () { clearTimeout(leftTimer); });
    outlineDrawer.addEventListener("mouseenter", function () { clearTimeout(rightTimer); });

    /* Mobile & touch gestures:
       - 2-finger pinch outward/inward zooms only the document content (--docs-scale), keeping UI chrome fixed size.
       - Slide inward from left edge (or left hint bar) -> opens left document selector pane; sliding thumb up/down scrubs
         document highlight; holding slide down past the last document of a category automatically opens the next category
         and closes the previous category (and vice versa going up); releasing after vertical scrub opens that document
         and closes the pane; releasing without vertical scrub keeps the left pane open until the user slides from its
         opened right edge back to the left.
       - Mirrored for the right edge / right outline pane.
       - History back-trap + non-passive touch prevention blocks Android OS back navigation while the viewer is open. */
    var touchGesture = null;
    var pinchState = null;
    var docsBackTrapArmed = false;
    function armDocsBackTrap() {
      if (docsBackTrapArmed || !window.history || !history.pushState) return;
      try {
        history.pushState({ kitDocsBackTrap: true }, "", location.href);
        docsBackTrapArmed = true;
      } catch (e) {}
    }
    window.addEventListener("popstate", function () {
      if (!modal || modal.hidden) { docsBackTrapArmed = false; return; }
      docsBackTrapArmed = false;
      if (listDrawer.classList.contains("open")) {
        setLeft(false);
        armDocsBackTrap();
        return;
      }
      if (outlineDrawer.classList.contains("open")) {
        setRight(false);
        armDocsBackTrap();
        return;
      }
      armDocsBackTrap();
    });

    function getDocGroups() {
      return Array.prototype.slice.call(groupsEl.querySelectorAll("details.kit-docs-grp"));
    }
    function getVisibleDocRows() {
      var allRows = groupsEl.querySelectorAll(".kit-docs-row");
      var out = [];
      for (var i = 0; i < allRows.length; i++) {
        var r = allRows[i];
        var det = r.closest ? r.closest("details") : null;
        if (!det || det.open) out.push(r);
      }
      return out;
    }
    function switchToNextCategory(curRow, dir) {
      var curDet = curRow && curRow.closest ? curRow.closest("details.kit-docs-grp") : null;
      var grps = getDocGroups();
      if (!grps.length) return null;
      var gIdx = curDet ? grps.indexOf(curDet) : 0;
      var nextGIdx = gIdx + (dir > 0 ? 1 : -1);
      if (nextGIdx < 0 || nextGIdx >= grps.length) return null;
      var nextDet = grps[nextGIdx];
      if (curDet && curDet !== nextDet) curDet.open = false;
      nextDet.open = true;
      var nextRows = nextDet.querySelectorAll(".kit-docs-row");
      if (!nextRows.length) return null;
      return dir > 0 ? nextRows[0] : nextRows[nextRows.length - 1];
    }
    function getVisibleOutlineBtns() {
      return Array.prototype.slice.call(outlineDrawer.querySelectorAll("button[data-hid]"));
    }
    function clearScrubHighlights() {
      var h1 = groupsEl.querySelectorAll(".scrub-hi");
      for (var i = 0; i < h1.length; i++) h1[i].classList.remove("scrub-hi");
      var h2 = outlineDrawer.querySelectorAll(".scrub-hi");
      for (var j = 0; j < h2.length; j++) h2[j].classList.remove("scrub-hi");
    }
    function touchDist(t0, t1) {
      var dx = t0.clientX - t1.clientX, dy = t0.clientY - t1.clientY;
      return Math.sqrt(dx * dx + dy * dy);
    }

    stageEl.addEventListener("touchstart", function (e) {
      if (!e.touches) return;
      if (e.touches.length === 2) {
        if (viewEl.querySelector(".kit-docs-sim-stage")) return;
        e.preventDefault();
        touchGesture = null;
        clearScrubHighlights();
        pinchState = {
          startDist: Math.max(10, touchDist(e.touches[0], e.touches[1])),
          baseZoom: localZoom || 100
        };
        return;
      }
      if (e.touches.length !== 1) return;
      pinchState = null;
      var t = e.touches[0];
      var sr = stageEl.getBoundingClientRect();
      var x = t.clientX - sr.left;
      var edgeZone = 60;
      var leftOpen = listDrawer.classList.contains("open");
      var rightOpen = outlineDrawer.classList.contains("open");
      var lw = listDrawer.offsetWidth || 260;
      var rw = outlineDrawer.offsetWidth || 230;
      var mode = "";
      if (!leftOpen && x <= edgeZone) {
        mode = "open-left";
      } else if (!rightOpen && activeHeadings.length >= 1 && x >= sr.width - edgeZone) {
        mode = "open-right";
      } else if (leftOpen && x >= lw - 64 && x <= lw + 48) {
        mode = "close-left";
      } else if (rightOpen && x >= sr.width - rw - 48 && x <= sr.width - rw + 64) {
        mode = "close-right";
      } else if (x <= 44 || x >= sr.width - 44) {
        mode = "guard-edge";
      }
      if (!mode) { touchGesture = null; return; }
      if (mode === "open-left" || mode === "open-right" || mode === "guard-edge") {
        e.preventDefault();
      }
      var curRow = null;
      var startIdx = 0;
      if (mode === "open-left") {
        var rows = getVisibleDocRows();
        for (var i = 0; i < rows.length; i++) {
          if (rows[i].dataset.path === activePath) { startIdx = i; curRow = rows[i]; break; }
        }
        if (!curRow && rows.length) curRow = rows[0];
      } else if (mode === "open-right") {
        var btns = getVisibleOutlineBtns();
        var curH = visibleHeading();
        for (var j = 0; j < btns.length; j++) {
          if (curH && btns[j].dataset.hid === curH.id) { startIdx = j; break; }
        }
      }
      touchGesture = {
        mode: mode,
        sx: t.clientX,
        sy: t.clientY,
        lastY: t.clientY,
        accY: 0,
        opened: false,
        scrubbed: false,
        curRow: curRow,
        startIdx: startIdx,
        curIdx: startIdx,
        anchorY: t.clientY
      };
    }, { passive: false });

    stageEl.addEventListener("touchmove", function (e) {
      if (pinchState && e.touches && e.touches.length === 2) {
        e.preventDefault();
        var d = Math.max(10, touchDist(e.touches[0], e.touches[1]));
        setDocsZoom(pinchState.baseZoom * (d / pinchState.startDist), true);
        return;
      }
      if (!touchGesture || !e.touches || e.touches.length !== 1) return;
      var t = e.touches[0];
      var dx = t.clientX - touchGesture.sx;
      var dy = t.clientY - touchGesture.sy;
      if (touchGesture.mode === "guard-edge") {
        if (Math.abs(dx) > Math.abs(dy)) e.preventDefault();
        return;
      }
      if (touchGesture.mode === "open-left") {
        e.preventDefault();
        if (!touchGesture.opened && dx > 10) {
          touchGesture.opened = true;
          touchGesture.anchorY = t.clientY;
          touchGesture.lastY = t.clientY;
          touchGesture.accY = 0;
          setRight(false);
          setLeft(true);
          var initRows = getVisibleDocRows();
          if (!touchGesture.curRow && initRows.length) touchGesture.curRow = initRows[0];
        }
        if (touchGesture.opened) {
          var stepY = 32;
          touchGesture.accY += (t.clientY - touchGesture.lastY);
          touchGesture.lastY = t.clientY;
          if (Math.abs(t.clientY - touchGesture.anchorY) > 14) touchGesture.scrubbed = true;
          while (Math.abs(touchGesture.accY) >= stepY) {
            var dir = touchGesture.accY > 0 ? 1 : -1;
            touchGesture.accY -= dir * stepY;
            var rows = getVisibleDocRows();
            if (!rows.length) break;
            var curI = touchGesture.curRow ? rows.indexOf(touchGesture.curRow) : 0;
            if (curI < 0) curI = 0;
            var curRowEl = rows[curI];
            var curDet = curRowEl && curRowEl.closest ? curRowEl.closest("details.kit-docs-grp") : null;
            var detRows = curDet ? Array.prototype.slice.call(curDet.querySelectorAll(".kit-docs-row")) : rows;
            var inDetIdx = detRows.indexOf(curRowEl);
            if (dir > 0 && inDetIdx === detRows.length - 1) {
              var nextCatRow = switchToNextCategory(curRowEl, 1);
              if (nextCatRow) touchGesture.curRow = nextCatRow;
              else if (curI + 1 < rows.length) touchGesture.curRow = rows[curI + 1];
            } else if (dir < 0 && inDetIdx === 0) {
              var prevCatRow = switchToNextCategory(curRowEl, -1);
              if (prevCatRow) touchGesture.curRow = prevCatRow;
              else if (curI - 1 >= 0) touchGesture.curRow = rows[curI - 1];
            } else {
              var nextI = Math.max(0, Math.min(rows.length - 1, curI + dir));
              touchGesture.curRow = rows[nextI];
            }
          }
          clearScrubHighlights();
          if (touchGesture.scrubbed && touchGesture.curRow) {
            touchGesture.curRow.classList.add("scrub-hi");
            if (touchGesture.curRow.scrollIntoView) touchGesture.curRow.scrollIntoView({ block: "nearest" });
          }
        }
      } else if (touchGesture.mode === "open-right") {
        e.preventDefault();
        if (!touchGesture.opened && dx < -10) {
          touchGesture.opened = true;
          touchGesture.anchorY = t.clientY;
          setLeft(false);
          setRight(true);
          var initBtns = getVisibleOutlineBtns();
          var curHInit = visibleHeading();
          touchGesture.startIdx = 0;
          for (var bi = 0; bi < initBtns.length; bi++) {
            if (curHInit && initBtns[bi].dataset.hid === curHInit.id) { touchGesture.startIdx = bi; break; }
          }
          touchGesture.curIdx = touchGesture.startIdx;
        }
        if (touchGesture.opened) {
          var vDeltaR = t.clientY - touchGesture.anchorY;
          var btns = getVisibleOutlineBtns();
          if (btns.length) {
            if (Math.abs(vDeltaR) > 14) touchGesture.scrubbed = true;
            var stepR = 30;
            var nextIdxR = Math.max(0, Math.min(btns.length - 1, touchGesture.startIdx + Math.round(vDeltaR / stepR)));
            touchGesture.curIdx = nextIdxR;
            clearScrubHighlights();
            if (touchGesture.scrubbed && btns[nextIdxR]) {
              btns[nextIdxR].classList.add("scrub-hi");
              if (btns[nextIdxR].scrollIntoView) btns[nextIdxR].scrollIntoView({ block: "nearest" });
            }
          }
        }
      } else if (touchGesture.mode === "close-left") {
        if (dx < -14 && Math.abs(dx) > Math.abs(dy)) {
          e.preventDefault();
          setLeft(false);
          touchGesture.closed = true;
        }
      } else if (touchGesture.mode === "close-right") {
        if (dx > 14 && Math.abs(dx) > Math.abs(dy)) {
          e.preventDefault();
          setRight(false);
          touchGesture.closed = true;
        }
      }
    }, { passive: false });

    function finishTouchGesture() {
      if (pinchState) {
        pinchState = null;
        syncUrlWorkspaceState();
      }
      if (!touchGesture) return;
      var g = touchGesture;
      touchGesture = null;
      clearScrubHighlights();
      if (g.mode === "open-left" && g.opened) {
        var pickedRow = g.curRow;
        var pickedPath = pickedRow && pickedRow.dataset.path ? pickedRow.dataset.path : "";
        if (g.scrubbed && pickedPath && pickedPath !== activePath) {
          openRepoDoc(pickedPath, "");
          setLeft(false);
        } else {
          setLeft(true);
        }
      } else if (g.mode === "open-right" && g.opened) {
        var btns = getVisibleOutlineBtns();
        var pickedBtn = btns[g.curIdx];
        var pickedHid = pickedBtn && pickedBtn.dataset.hid ? pickedBtn.dataset.hid : "";
        var curH = visibleHeading();
        if (g.scrubbed && pickedHid && (!curH || pickedHid !== curH.id)) {
          selectAndScrollToHeading(pickedHid, true);
          setRight(false);
        } else {
          setRight(true);
        }
      }
    }
    stageEl.addEventListener("touchend", finishTouchGesture, { passive: true });
    stageEl.addEventListener("touchcancel", finishTouchGesture, { passive: true });
    function findHeadingById(hid) {
      if (!hid) return null;
      for (var i = 0; i < activeHeadings.length; i++) {
        if (activeHeadings[i].id === hid) return activeHeadings[i];
      }
      return null;
    }
    function headingForDomNode(node) {
      if (!node || !activeHeadings.length) return null;
      var el = node.nodeType === 1 ? node : node.parentElement;
      if (!el || !viewEl.contains(el)) return null;
      var hSelf = el.closest ? el.closest("h1,h2,h3,h4") : null;
      if (hSelf && hSelf.id) {
        var direct = findHeadingById(hSelf.id);
        if (direct) return direct;
      }
      var nRect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      if (!nRect) return null;
      var best = activeHeadings[0];
      for (var i = 0; i < activeHeadings.length; i++) {
        var hEl = document.getElementById(activeHeadings[i].id);
        if (!hEl) continue;
        if (hEl.getBoundingClientRect().top <= nRect.top + 8) best = activeHeadings[i];
        else break;
      }
      return best;
    }
    function selectAndScrollToHeading(hid, doScroll) {
      var hObj = findHeadingById(hid);
      if (!hObj) return;
      selectedHeadingId = hObj.id;
      _kitDocsState.section = hObj.slug || hObj.text || "";
      syncUrlWorkspaceState();
      if (doScroll) {
        var el = document.getElementById(hObj.id);
        if (el) {
          scrollLockUntil = Date.now() + 700;
          var vRect = viewEl.getBoundingClientRect();
          var eRect = el.getBoundingClientRect();
          var targetTop = Math.max(0, viewEl.scrollTop + (eRect.top - vRect.top) - 18);
          if (typeof viewEl.scrollTo === "function") {
            viewEl.scrollTo({ top: targetTop, behavior: "smooth" });
          } else {
            viewEl.scrollTop = targetTop;
          }
          setTimeout(syncOutlineActive, 120);
          setTimeout(syncOutlineActive, 450);
          setTimeout(syncOutlineActive, 720);
        }
      }
      syncOutlineActive();
    }
    function visibleHeading() {
      if (!activeHeadings.length) return null;
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        if (sel && !sel.isCollapsed && sel.toString().trim() && viewEl.contains(sel.anchorNode)) {
          var selH = headingForDomNode(sel.anchorNode);
          if (selH) return selH;
        }
      } catch (e) {}
      var vRect = viewEl.getBoundingClientRect();
      if (selectedHeadingId) {
        var locked = findHeadingById(selectedHeadingId);
        var lockedEl = locked ? document.getElementById(locked.id) : null;
        if (locked && lockedEl) {
          if (Date.now() < scrollLockUntil) return locked;
          var lRect = lockedEl.getBoundingClientRect();
          if (lRect.bottom >= vRect.top + 4 && lRect.top <= vRect.bottom - 16) {
            return locked;
          }
        }
        selectedHeadingId = "";
      }
      var triggerY = vRect.top + Math.max(64, Math.min(160, vRect.height * 0.26));
      var atBottom = (viewEl.scrollTop + viewEl.clientHeight) >= (viewEl.scrollHeight - 12);
      if (atBottom) triggerY = vRect.top + Math.max(120, vRect.height * 0.65);
      var best = activeHeadings[0];
      for (var i = 0; i < activeHeadings.length; i++) {
        var el = document.getElementById(activeHeadings[i].id);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= triggerY) best = activeHeadings[i]; else break;
      }
      return best;
    }
    function syncOutlineActive() {
      if (!activeHeadings.length) return;
      var cur = visibleHeading(), btns = outlineDrawer.querySelectorAll("button[data-hid]");
      for (var i = 0; i < btns.length; i++) btns[i].classList.toggle("active", !!(cur && btns[i].dataset.hid === cur.id));
      for (var j = 0; j < activeHeadings.length; j++) {
        var hEl = document.getElementById(activeHeadings[j].id);
        if (hEl) hEl.classList.toggle("kit-sec-hit", !!(cur && activeHeadings[j].id === cur.id));
      }
      if (cur) {
        cmtHdrBtn.title = "Comment on § " + cur.text;
        cmtHdrBtn.setAttribute("aria-label", cmtHdrBtn.title);
        if (activeDocMetaText) subEl.textContent = activeDocMetaText + " · § " + cur.text;
      }
    }
    viewEl.addEventListener("wheel", function (e) {
      if (!e.ctrlKey && !e.metaKey && Date.now() >= scrollLockUntil) selectedHeadingId = "";
    }, { passive: true });
    viewEl.addEventListener("touchmove", function (e) {
      if (e.touches && e.touches.length === 1 && Date.now() >= scrollLockUntil) selectedHeadingId = "";
    }, { passive: true });
    viewEl.addEventListener("scroll", syncOutlineActive, { passive: true });
    viewEl.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest("a,button")) return;
      var clickedH = headingForDomNode(e.target);
      if (clickedH) {
        selectedHeadingId = clickedH.id;
        _kitDocsState.section = clickedH.slug || clickedH.text || "";
        syncOutlineActive();
      }
    });
    document.addEventListener("selectionchange", function () {
      if (modal.hidden || !activeHeadings.length) return;
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        if (sel && !sel.isCollapsed && sel.toString().trim() && viewEl.contains(sel.anchorNode)) {
          var sh = headingForDomNode(sel.anchorNode);
          if (sh) {
            selectedHeadingId = sh.id;
            syncOutlineActive();
          }
        }
      } catch (e) {}
    });

    function togglePin(relPath, curPinned) {
      api("POST", "/_api/repo-docs/pin", { path: relPath, pinned: !curPinned }).then(function (res) {
        allDocs.forEach(function (d) { if (d.path === relPath) d.pinned = !!(res && res.pinned); });
        renderList();
        if (activePath === relPath) {
          var on = !!(res && res.pinned);
          pinHdrBtn.classList.toggle("ember", on);
          pinHdrBtn.title = on ? "Unpin document" : "Pin document";
        }
      }, function () {});
    }

    function makeDocRow(d) {
      var row = document.createElement("div"); row.className = "kit-docs-row" + (d.path === activePath ? " on" : "");
      row.dataset.path = d.path;
      var it = document.createElement("button"); it.type = "button"; it.className = "kit-docs-item";
      var dp = document.createElement("span"); dp.className = "dp";
      if (isDocNew(d)) { var nd = document.createElement("span"); nd.className = "newdot"; dp.appendChild(nd); }
      var nm = document.createElement("span"); nm.textContent = shortName(d.path); dp.appendChild(nm);
      var dm = document.createElement("span"); dm.className = "dm"; dm.textContent = fmtTime(d.mtime) + " · " + d.path;
      it.appendChild(dp); it.appendChild(dm);
      it.addEventListener("click", function (ev) {
        if (ev.shiftKey && window.innerWidth >= 720) {
          ev.preventDefault(); ev.stopPropagation();
          window._kitOpenRepoDoc(d.path, "", undefined, { newWindow: true });
        } else {
          openRepoDoc(d.path, "");
        }
      });
      var pb = document.createElement("button"); pb.type = "button"; pb.className = "kit-docs-pin" + (d.pinned ? " on" : "");
      pb.title = d.pinned ? "Unpin document" : "Pin document"; pb.setAttribute("aria-label", pb.title); pb.innerHTML = SVG_PIN;
      pb.addEventListener("click", function (ev) { ev.stopPropagation(); togglePin(d.path, !!d.pinned); });
      row.appendChild(it); row.appendChild(pb);
      return row;
    }

    function addGroup(title, docs, openByDefault) {
      if (!docs.length) return;
      var det = document.createElement("details"); det.className = "kit-docs-grp"; det.open = !!openByDefault;
      var sm = document.createElement("summary"); sm.innerHTML = '<span></span><span></span>';
      sm.children[0].textContent = title; sm.children[1].textContent = String(docs.length);
      det.appendChild(sm); docs.forEach(function (d) { det.appendChild(makeDocRow(d)); });
      groupsEl.appendChild(det);
    }

    function renderList() {
      var ql = (qEl.value || "").trim().toLowerCase();
      groupsEl.textContent = "";
      if (ql) {
        var hits = allDocs.filter(function (d) { return d.path.toLowerCase().indexOf(ql) >= 0; });
        if (!hits.length) { groupsEl.innerHTML = '<div class="note" style="padding:8px">No matching documents.</div>'; return; }
        addGroup("Matches", hits.slice(0, 120), true);
        return;
      }
      var pinned = allDocs.filter(function (d) { return !!d.pinned; });
      var byMtime = allDocs.slice().sort(function (a, b) { return String(b.mtime || "").localeCompare(String(a.mtime || "")); });
      addGroup("Pinned", pinned, true);
      addGroup("Recently updated", byMtime.filter(function (d) { return !d.pinned; }).slice(0, 10), true);
      [["memory", "Memory"], ["docs", "Docs"], ["html", "HTML"], ["images", "Images"], ["prompts", "Prompts"], ["root", "Root"], ["kit", "Kit"], ["other", "Other"]].forEach(function (pair) {
        addGroup(pair[1], byMtime.filter(function (d) { return d.category === pair[0]; }), false);
      });
    }

    function renderOutline() {
      outlineDrawer.textContent = "";
      hintR.hidden = activeHeadings.length < 1;
      if (activeHeadings.length < 1) { outlineDrawer.classList.remove("open"); return; }
      var t = document.createElement("div"); t.className = "otitle"; t.textContent = "Outline";
      outlineDrawer.appendChild(t);
      activeHeadings.slice(0, 60).forEach(function (h) {
        var b = document.createElement("button"); b.type = "button";
        b.className = "lvl-" + Math.min(4, Math.max(1, h.level || 1));
        b.dataset.hid = h.id; b.textContent = h.text; b.title = h.text;
        b.addEventListener("click", function () {
          selectAndScrollToHeading(h.id, true);
          if (isPhoneScreen()) setRight(false);
        });
        outlineDrawer.appendChild(b);
      });
      syncOutlineActive();
    }

    pinHdrBtn.addEventListener("click", function () {
      if (!activePath) return;
      var cur = allDocs.some(function (d) { return d.path === activePath && d.pinned; });
      togglePin(activePath, cur);
    });
    function setHtmlPaused(ifr, pause) {
      if (!ifr) return;
      try {
        var doc = ifr.contentDocument;
        var win = ifr.contentWindow;
        if (!doc) return;
        var pst = doc.getElementById("kit-pause-style");
        if (pause) {
          if (!pst) {
            pst = doc.createElement("style");
            pst.id = "kit-pause-style";
            pst.textContent = "*,*::before,*::after{animation-play-state:paused !important;transition:none !important;}";
            (doc.head || doc.documentElement).appendChild(pst);
          }
          var media = doc.querySelectorAll("video,audio");
          for (var i = 0; i < media.length; i++) {
            if (!media[i].paused) { media[i].dataset.kitWasPlaying = "1"; media[i].pause(); }
          }
          if (win && typeof win.dispatchEvent === "function") win.dispatchEvent(new CustomEvent("kit:pause"));
          pausedIframe = ifr;
        } else {
          if (pst) pst.remove();
          var media2 = doc.querySelectorAll("video[data-kit-was-playing],audio[data-kit-was-playing]");
          for (var j = 0; j < media2.length; j++) {
            delete media2[j].dataset.kitWasPlaying;
            try { media2[j].play(); } catch (e) {}
          }
          if (win && typeof win.dispatchEvent === "function") win.dispatchEvent(new CustomEvent("kit:resume"));
          if (pausedIframe === ifr) pausedIframe = null;
        }
      } catch (e) {}
    }
    function watchOrchCloseForResume() {
      var sh = document.getElementById("sheet");
      if (!sh || !pausedIframe) return;
      var checkT = setInterval(function () {
        if (!pausedIframe) { clearInterval(checkT); return; }
        if (sh.hidden) {
          setHtmlPaused(pausedIframe, false);
          clearInterval(checkT);
        }
      }, 300);
    }
    function captureHtmlLiveState(ifr) {
      var parts = [];
      var selTxt = "";
      try {
        var win = ifr && ifr.contentWindow;
        var doc = ifr && ifr.contentDocument;
        if (win && doc) {
          if (win.location && win.location.hash) parts.push("hash `" + win.location.hash + "`");
          var maxS = Math.max(0, (doc.documentElement.scrollHeight || 0) - (win.innerHeight || 0));
          if (maxS > 20) {
            var pct = Math.round(((win.scrollY || doc.documentElement.scrollTop || 0) / maxS) * 100);
            parts.push("scroll " + pct + "%");
          }
          var hs = doc.querySelectorAll("h1,h2,h3,h4,[aria-current],[aria-selected='true']");
          var bestH = "";
          for (var i = 0; i < hs.length; i++) {
            var hr = hs[i].getBoundingClientRect();
            if (hr.height > 0 && hr.top >= -20 && hr.top <= (win.innerHeight || 600) * 0.45) {
              var ht = (hs[i].textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
              if (ht) { bestH = ht; break; }
            }
          }
          if (bestH) parts.push("view `" + bestH + "`");
          var isel = win.getSelection ? win.getSelection() : null;
          if (isel && isel.toString().trim()) selTxt = isel.toString().trim().slice(0, 320);
        }
      } catch (e) {}
      if (htmlErrors.length) parts.push("last error: `" + htmlErrors[htmlErrors.length - 1].slice(0, 120) + "`");
      return { meta: parts.length ? (" (" + parts.join(", ") + ")") : "", sel: selTxt };
    }

    cmtHdrBtn.addEventListener("click", function () {
      if (activeViewMode === "compare" && galSelected.length) {
        var cmpRefs = galSelected.map(function (p) { return "`" + p + "`"; }).join(", ");
        if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew("Re " + cmpRefs + " (compare):\n"); bringOrchAboveViewer(); }
        return;
      }
      if (!activePath) return;
      if (activeKind === "html" && activeViewMode === "view" && htmlMode !== "code") {
        var ifr = viewEl.querySelector("iframe.kit-docs-iframe");
        var st = captureHtmlLiveState(ifr);
        setHtmlPaused(ifr, true);
        var hPre = "Re `" + activePath + "`" + st.meta + ":\n";
        if (st.sel) hPre += "> " + st.sel.replace(/\n+/g, "\n> ") + "\n\n";
        if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew(hPre); bringOrchAboveViewer(); watchOrchCloseForResume(); }
        return;
      }
      var vh = activeKind === "md" ? visibleHeading() : null, secTxt = vh ? vh.text : "", selTxt = "";
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        if (sel && sel.toString().trim() && viewEl.contains(sel.anchorNode)) selTxt = sel.toString().trim().slice(0, 320);
      } catch (e) {}
      var prefill = "Re `" + activePath + (secTxt ? " § " + secTxt : "") + "`:\n";
      if (selTxt) prefill += "> " + selTxt.replace(/\n+/g, "\n> ") + "\n\n";
      if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew(prefill); bringOrchAboveViewer(); }
    });

    function renderHtmlView() {
      clearFloatingBars();
      drawActive = false;
      activeViewMode = "view";
      activeHeadings = [];
      htmlErrors = [];
      renderOutline();
      viewEl.textContent = "";
      if (htmlMode === "code") {
        viewEl.classList.remove("media-mode");
        var cw = document.createElement("div"); cw.className = "kit-docs-code-wrap";
        var pre = document.createElement("pre"); pre.className = "kit-docs-code";
        var lines = String(activeHtmlSource || "").replace(/\r/g, "").split("\n");
        for (var i = 0; i < lines.length; i++) {
          var ln = document.createElement("span"); ln.className = "cln"; ln.textContent = String(i + 1);
          pre.appendChild(ln);
          pre.appendChild(document.createTextNode(lines[i] + "\n"));
        }
        cw.appendChild(pre);
        viewEl.appendChild(cw);
      } else if (htmlMode === "desktop" && isPhoneScreen() && window.innerWidth < window.innerHeight) {
        viewEl.classList.add("media-mode");
        var simStage = document.createElement("div"); simStage.className = "kit-docs-sim-stage";
        var simFrame = document.createElement("div"); simFrame.className = "kit-docs-sim-frame";
        var ifrS = document.createElement("iframe");
        ifrS.className = "kit-docs-iframe";
        ifrS.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-same-origin");
        ifrS.srcdoc = buildHtmlSrcdoc(activeHtmlSource, activeHtmlUrl);
        simFrame.appendChild(ifrS);
        simStage.appendChild(simFrame);
        viewEl.appendChild(simStage);
        var simZoom = 1, simPanX = 0, simPanY = 0, simTouch = null, simRaf = 0;
        function applySimTransform() {
          simRaf = 0;
          var sw = Math.max(260, simStage.clientWidth - 16);
          var baseScale = sw / 1280;
          var totalScale = baseScale * simZoom;
          var maxPanX = Math.max(0, (1280 * totalScale - sw) / 2 + 40);
          var maxPanY = Math.max(0, (800 * totalScale - Math.max(200, simStage.clientHeight - 16)) / 2 + 40);
          simPanX = Math.max(-maxPanX, Math.min(maxPanX, simPanX));
          simPanY = Math.max(-maxPanY, Math.min(maxPanY, simPanY));
          simFrame.style.transform = "translate3d(" + Math.round(simPanX) + "px," + Math.round(simPanY) + "px,0) scale(" + totalScale.toFixed(4) + ")";
        }
        function scheduleSimTransform() {
          if (!simRaf) simRaf = requestAnimationFrame(applySimTransform);
        }
        function bindSimPinch(targetDoc) {
          if (!targetDoc) return;
          targetDoc.addEventListener("touchstart", function (ev) {
            if (ev.touches && ev.touches.length === 2) {
              ev.preventDefault();
              ev.stopPropagation();
              var t0 = ev.touches[0], t1 = ev.touches[1];
              simTouch = {
                d0: Math.hypot(t1.screenX - t0.screenX, t1.screenY - t0.screenY) || 1,
                z0: simZoom,
                mx0: (t0.screenX + t1.screenX) / 2,
                my0: (t0.screenY + t1.screenY) / 2,
                px0: simPanX,
                py0: simPanY
              };
            }
          }, { passive: false });
          targetDoc.addEventListener("touchmove", function (ev) {
            if (simTouch && ev.touches && ev.touches.length === 2) {
              ev.preventDefault();
              ev.stopPropagation();
              var t0 = ev.touches[0], t1 = ev.touches[1];
              var d1 = Math.hypot(t1.screenX - t0.screenX, t1.screenY - t0.screenY) || simTouch.d0;
              var mx = (t0.screenX + t1.screenX) / 2, my = (t0.screenY + t1.screenY) / 2;
              simZoom = Math.max(1, Math.min(4.5, simTouch.z0 * (d1 / simTouch.d0)));
              simPanX = simTouch.px0 + (mx - simTouch.mx0);
              simPanY = simTouch.py0 + (my - simTouch.my0);
              scheduleSimTransform();
            }
          }, { passive: false });
          targetDoc.addEventListener("touchend", function (ev) {
            if (!ev.touches || ev.touches.length < 2) simTouch = null;
          }, { passive: true });
        }
        bindSimPinch(simStage);
        ifrS.addEventListener("load", function () {
          try {
            bindSimPinch(ifrS.contentDocument);
            ifrS.contentWindow.addEventListener("error", function (err) {
              htmlErrors.push(String((err && err.message) || "runtime error"));
            });
          } catch (e) {}
        });
        requestAnimationFrame(applySimTransform);
      } else {
        viewEl.classList.add("media-mode");
        var wrap = document.createElement("div");
        wrap.className = "kit-docs-iframe-wrap" + (htmlMode === "mobile" ? " phone-view" : "");
        var ifr = document.createElement("iframe");
        ifr.className = "kit-docs-iframe";
        ifr.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-same-origin");
        ifr.srcdoc = buildHtmlSrcdoc(activeHtmlSource, activeHtmlUrl);
        ifr.addEventListener("load", function () {
          try {
            ifr.contentWindow.addEventListener("error", function (err) {
              htmlErrors.push(String((err && err.message) || "runtime error"));
            });
          } catch (e) {}
        });
        wrap.appendChild(ifr);
        viewEl.appendChild(wrap);
      }
      updateHeaderModeButtons();
    }

    htmlPhoneBtn.addEventListener("click", function () {
      if (activeKind !== "html") return;
      if (window.innerWidth >= 720 && htmlMode !== "mobile") {
        var r = modal.getBoundingClientRect();
        if (r.width > 460) savedDesktopWidth = Math.round(r.width);
        modal.dataset.kitDeskH = String(Math.round(r.height));
        modal.style.width = "436px";
        modal.style.height = Math.min(840, window.innerHeight - 24) + "px";
      }
      htmlMode = "mobile";
      renderHtmlView();
    });
    htmlDeskBtn.addEventListener("click", function () {
      if (activeKind !== "html") return;
      if (window.innerWidth >= 720) {
        if (savedDesktopWidth > 460) modal.style.width = Math.min(savedDesktopWidth, window.innerWidth - 24) + "px";
        if (Number(modal.dataset.kitDeskH || 0) > 300) modal.style.height = Math.min(Number(modal.dataset.kitDeskH), window.innerHeight - 24) + "px";
      }
      htmlMode = "desktop";
      renderHtmlView();
    });
    htmlCodeBtn.addEventListener("click", function () {
      if (activeKind !== "html") return;
      htmlMode = htmlMode === "code" ? "desktop" : "code";
      if (htmlMode !== "mobile" && window.innerWidth >= 720 && savedDesktopWidth > 460) {
        modal.style.width = Math.min(savedDesktopWidth, window.innerWidth - 24) + "px";
      }
      renderHtmlView();
    });

    function startImageDrawMode(imgEl, wrapEl) {
      if (drawActive || !imgEl || !wrapEl) return;
      drawActive = true;
      updateHeaderModeButtons();
      clearFloatingBars();
      var cv = document.createElement("canvas");
      cv.className = "kit-docs-draw-cv";
      var cw = imgEl.naturalWidth || imgEl.clientWidth || 800;
      var ch = imgEl.naturalHeight || imgEl.clientHeight || 600;
      cv.width = cw;
      cv.height = ch;
      wrapEl.appendChild(cv);
      var ctx = cv.getContext("2d");
      var strokes = [], curStroke = null, drawColor = "#e8894a", drawing = false;
      function redraw() {
        ctx.clearRect(0, 0, cv.width, cv.height);
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        for (var i = 0; i < strokes.length; i++) {
          var s = strokes[i];
          if (!s.pts.length) continue;
          ctx.strokeStyle = s.color;
          ctx.lineWidth = Math.max(3, Math.round(Math.min(cv.width, cv.height) * 0.007));
          ctx.beginPath();
          ctx.moveTo(s.pts[0].x, s.pts[0].y);
          for (var j = 1; j < s.pts.length; j++) ctx.lineTo(s.pts[j].x, s.pts[j].y);
          ctx.stroke();
        }
      }
      function ptFromEvent(ev) {
        var r = cv.getBoundingClientRect();
        var cx = ev.touches ? ev.touches[0].clientX : ev.clientX;
        var cy = ev.touches ? ev.touches[0].clientY : ev.clientY;
        return { x: ((cx - r.left) / Math.max(1, r.width)) * cv.width, y: ((cy - r.top) / Math.max(1, r.height)) * cv.height };
      }
      function onDown(ev) {
        if (ev.touches && ev.touches.length > 1) return;
        ev.preventDefault(); ev.stopPropagation();
        drawing = true;
        curStroke = { color: drawColor, pts: [ptFromEvent(ev)] };
        strokes.push(curStroke);
        redraw();
      }
      function onMove(ev) {
        if (!drawing || !curStroke) return;
        if (ev.touches && ev.touches.length > 1) return;
        ev.preventDefault(); ev.stopPropagation();
        curStroke.pts.push(ptFromEvent(ev));
        redraw();
      }
      function onUp() { drawing = false; curStroke = null; }
      cv.addEventListener("mousedown", onDown);
      cv.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      cv.addEventListener("touchstart", onDown, { passive: false });
      cv.addEventListener("touchmove", onMove, { passive: false });
      cv.addEventListener("touchend", onUp, { passive: true });

      var bar = document.createElement("div"); bar.className = "kit-docs-draw-bar";
      var colors = [["#e8894a", "Ember"], ["#ef4444", "Red"], ["#facc15", "Yellow"], ["#38bdf8", "Cyan"]];
      colors.forEach(function (pair, idx) {
        var dBtn = document.createElement("button"); dBtn.type = "button";
        dBtn.className = "kit-draw-dot" + (idx === 0 ? " on" : "");
        dBtn.style.background = pair[0];
        dBtn.title = pair[1]; dBtn.setAttribute("aria-label", pair[1]);
        dBtn.addEventListener("click", function () {
          drawColor = pair[0];
          var allD = bar.querySelectorAll(".kit-draw-dot");
          for (var k = 0; k < allD.length; k++) allD[k].classList.remove("on");
          dBtn.classList.add("on");
        });
        bar.appendChild(dBtn);
      });
      var undoB = document.createElement("button"); undoB.type = "button"; undoB.className = "btn small kit-icon-btn";
      undoB.title = "Undo"; undoB.setAttribute("aria-label", "Undo"); undoB.innerHTML = SVG_UNDO;
      undoB.addEventListener("click", function () { strokes.pop(); redraw(); });
      var clrB = document.createElement("button"); clrB.type = "button"; clrB.className = "btn small kit-icon-btn";
      clrB.title = "Clear"; clrB.setAttribute("aria-label", "Clear"); clrB.innerHTML = SVG_TRASH;
      clrB.addEventListener("click", function () { strokes = []; redraw(); });
      var cancelB = document.createElement("button"); cancelB.type = "button"; cancelB.className = "btn small kit-icon-btn";
      cancelB.title = "Cancel annotation"; cancelB.setAttribute("aria-label", "Cancel annotation"); cancelB.innerHTML = SVG_CLOSE;
      function exitDraw() {
        drawActive = false;
        window.removeEventListener("mouseup", onUp);
        if (cv.parentNode) cv.remove();
        if (bar.parentNode) bar.remove();
        updateHeaderModeButtons();
      }
      cancelB.addEventListener("click", exitDraw);
      var sendB = document.createElement("button"); sendB.type = "button"; sendB.className = "btn small kit-icon-btn ember";
      sendB.title = "Send annotated image to orchestrator"; sendB.setAttribute("aria-label", "Send annotated image to orchestrator"); sendB.innerHTML = SVG_SEND;
      sendB.addEventListener("click", function () {
        sendB.disabled = true;
        var out = document.createElement("canvas");
        out.width = cw; out.height = ch;
        var octx = out.getContext("2d");
        octx.drawImage(imgEl, 0, 0, cw, ch);
        octx.drawImage(cv, 0, 0, cw, ch);
        out.toBlob(function (blob) {
          if (!blob) { sendB.disabled = false; return; }
          var baseName = shortName(activePath).replace(/[^A-Za-z0-9._-]+/g, "_") + "-annotated.png";
          var fileObj = typeof File === "function" ? new File([blob], baseName, { type: "image/png" }) : blob;
          if (!fileObj.name) fileObj.name = baseName;
          assets.upload(fileObj).then(function (up) {
            exitDraw();
            var att = [{ id: (up && up.id) || up, name: baseName, type: "image/png" }];
            if (typeof window._kitOpenOrchNew === "function") {
              window._kitOpenOrchNew("Re `" + activePath + "` (annotated):\n", att);
              bringOrchAboveViewer();
            }
          }, function () {
            sendB.disabled = false;
          });
        }, "image/png");
      });
      bar.appendChild(undoB); bar.appendChild(clrB); bar.appendChild(cancelB); bar.appendChild(sendB);
      stageEl.appendChild(bar);
    }

    function renderImageView() {
      clearFloatingBars();
      drawActive = false;
      activeViewMode = "view";
      activeHeadings = [];
      renderOutline();
      viewEl.classList.add("media-mode");
      viewEl.textContent = "";
      var stg = document.createElement("div"); stg.className = "kit-docs-img-stage";
      var wrap = document.createElement("div"); wrap.className = "kit-docs-img-wrap";
      var img = document.createElement("img");
      img.src = activeImgUrl;
      img.alt = activePath;
      img.addEventListener("load", function () {
        var k = imgDimStorageKey(img.naturalWidth, img.naturalHeight);
        activeImgDimKey = k;
        if (k) {
          try {
            var savedZ = Number(localStorage.getItem(k) || "0");
            if (savedZ >= 55 && savedZ <= 300) {
              applyLocalZoom(savedZ, true);
              return;
            }
          } catch (e) {}
        }
        applyLocalZoom(100, true);
      });
      wrap.appendChild(img);
      stg.appendChild(wrap);
      viewEl.appendChild(stg);
      applyLocalZoom(localZoom, true);
      updateHeaderModeButtons();
    }

    function renderCompareView(paths) {
      clearFloatingBars();
      drawActive = false;
      activeViewMode = "compare";
      activeHeadings = [];
      renderOutline();
      viewEl.classList.add("media-mode");
      viewEl.textContent = "";
      titleEl.textContent = "Compare (" + paths.length + ")";
      subEl.textContent = paths.map(shortName).join(" vs ");
      var grid = document.createElement("div");
      grid.className = "kit-docs-cmp-grid";
      var cols = paths.length <= 2 ? paths.length : (paths.length <= 4 ? 2 : 3);
      grid.style.gridTemplateColumns = "repeat(" + cols + ", minmax(0, 1fr))";
      paths.forEach(function (p) {
        var card = document.createElement("div"); card.className = "kit-docs-cmp-item";
        var ch = document.createElement("div"); ch.className = "cmphdr"; ch.textContent = p; ch.title = p;
        var cb = document.createElement("div"); cb.className = "cmpbody";
        var im = document.createElement("img");
        im.src = (p.indexOf("/_blob/") === 0 || p.indexOf("/_api/") === 0 || /^https?:\/\//i.test(p)) ? p : resolveAssetUrl(p, "", activeRepoOverride);
        im.alt = p;
        im.addEventListener("click", function (ev) {
          if (p.indexOf("/_blob/") === 0 || p.indexOf("/_api/") === 0 || /^https?:\/\//i.test(p)) {
            openDirectImage(p, p, customImgList);
          } else if (ev.shiftKey && window.innerWidth >= 720) window._kitOpenRepoDoc(p, "", activeRepoOverride, { newWindow: true });
          else openRepoDoc(p, "", activeRepoOverride);
        });
        cb.appendChild(im); card.appendChild(ch); card.appendChild(cb);
        grid.appendChild(card);
      });
      viewEl.appendChild(grid);
      updateHeaderModeButtons();
    }

    function renderGalleryView() {
      clearFloatingBars();
      drawActive = false;
      activeViewMode = "gallery";
      activeHeadings = [];
      renderOutline();
      viewEl.classList.add("media-mode");
      viewEl.textContent = "";
      titleEl.textContent = "Gallery";
      var imgs = customImgList && customImgList.length
        ? customImgList.map(function (item) { return { path: item.src, label: item.label || shortName(item.src), direct: true }; })
        : allDocs.filter(function (d) { return d.kind === "img" || /\.(?:png|jpe?g|gif|webp|avif|svg)$/i.test(d.path || ""); });
      subEl.textContent = imgs.length + (customImgList ? " images in post" : " images in repo");
      var grid = document.createElement("div");
      grid.className = "kit-docs-gal-grid" + (galSelected.length ? " selecting" : "");
      if (!imgs.length) {
        viewEl.innerHTML = '<div class="note" style="padding:24px">No images found in this repository.</div>';
        updateHeaderModeButtons();
        return;
      }
      function syncGalBar() {
        var old = stageEl.querySelector(".kit-docs-gal-bar");
        if (old) old.remove();
        grid.classList.toggle("selecting", galSelected.length > 0);
        if (!galSelected.length) return;
        var bar = document.createElement("div"); bar.className = "kit-docs-gal-bar";
        var cnt = document.createElement("span"); cnt.className = "gcnt"; cnt.textContent = galSelected.length + " selected";
        bar.appendChild(cnt);
        if (galSelected.length >= 2) {
          var cmpB = document.createElement("button"); cmpB.type = "button"; cmpB.className = "btn small kit-icon-btn";
          cmpB.title = "Compare selected side-by-side"; cmpB.setAttribute("aria-label", "Compare selected side-by-side"); cmpB.innerHTML = SVG_CMP;
          cmpB.addEventListener("click", function () { renderCompareView(galSelected.slice()); });
          bar.appendChild(cmpB);
        }
        var cmtB = document.createElement("button"); cmtB.type = "button"; cmtB.className = "btn small kit-icon-btn ember";
        cmtB.title = "Comment on selected images"; cmtB.setAttribute("aria-label", "Comment on selected images"); cmtB.innerHTML = SVG_CMT;
        cmtB.addEventListener("click", function () {
          var refs = galSelected.map(function (p) { return "`" + p + "`"; }).join(", ");
          if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew("Re " + refs + ":\n"); bringOrchAboveViewer(); }
        });
        bar.appendChild(cmtB);
        var clrB = document.createElement("button"); clrB.type = "button"; clrB.className = "btn small kit-icon-btn";
        clrB.title = "Clear selection"; clrB.setAttribute("aria-label", "Clear selection"); clrB.innerHTML = SVG_CLOSE;
        clrB.addEventListener("click", function () {
          galSelected = [];
          var cards = grid.querySelectorAll(".kit-docs-gal-card.sel");
          for (var k = 0; k < cards.length; k++) cards[k].classList.remove("sel");
          syncGalBar();
        });
        bar.appendChild(clrB);
        stageEl.appendChild(bar);
      }
      function toggleSelect(path, cardEl) {
        var idx = galSelected.indexOf(path);
        if (idx >= 0) galSelected.splice(idx, 1);
        else galSelected.push(path);
        cardEl.classList.toggle("sel", idx < 0);
        syncGalBar();
      }
      imgs.forEach(function (d) {
        var card = document.createElement("div");
        card.className = "kit-docs-gal-card" + (galSelected.indexOf(d.path) >= 0 ? " sel" : "");
        var chk = document.createElement("button"); chk.type = "button"; chk.className = "gchk";
        chk.title = "Select image"; chk.setAttribute("aria-label", "Select " + d.path); chk.innerHTML = SVG_CHK;
        chk.addEventListener("click", function (ev) { ev.stopPropagation(); toggleSelect(d.path, card); });
        var im = document.createElement("img"); im.className = "gthumb"; im.loading = "lazy";
        im.src = d.direct ? d.path : resolveAssetUrl(d.path, "", activeRepoOverride); im.alt = d.label || d.path;
        var cap = document.createElement("div"); cap.className = "gcap"; cap.textContent = d.label || shortName(d.path); cap.title = d.label || d.path;
        var holdTimer = null, holdFired = false;
        card.addEventListener("touchstart", function (ev) {
          if (ev.touches.length !== 1) return;
          holdFired = false;
          holdTimer = setTimeout(function () {
            holdFired = true;
            toggleSelect(d.path, card);
          }, 420);
        }, { passive: true });
        card.addEventListener("touchmove", function () { clearTimeout(holdTimer); }, { passive: true });
        card.addEventListener("touchend", function () { clearTimeout(holdTimer); }, { passive: true });
        card.addEventListener("click", function (ev) {
          if (holdFired) { holdFired = false; return; }
          if (ev.ctrlKey || ev.metaKey || galSelected.length > 0) {
            ev.preventDefault(); ev.stopPropagation();
            toggleSelect(d.path, card);
            return;
          }
          if (d.direct) {
            if (ev.shiftKey && window.innerWidth >= 720) window._kitOpenImageViewer(d.path, d.label || d.path, customImgList, { newWindow: true });
            else openDirectImage(d.path, d.label || d.path, customImgList);
            return;
          }
          if (ev.shiftKey && window.innerWidth >= 720) {
            ev.preventDefault(); ev.stopPropagation();
            window._kitOpenRepoDoc(d.path, "", activeRepoOverride, { newWindow: true });
            return;
          }
          openRepoDoc(d.path, "", activeRepoOverride);
        });
        card.appendChild(chk); card.appendChild(im); card.appendChild(cap);
        grid.appendChild(card);
      });
      viewEl.appendChild(grid);
      syncGalBar();
      updateHeaderModeButtons();
    }

    imgGalBtn.addEventListener("click", function () {
      if (activeViewMode === "gallery") {
        if (activePath && activeKind === "img") renderImageView();
      } else {
        renderGalleryView();
      }
    });
    imgDrawBtn.addEventListener("click", function () {
      if (activeKind !== "img" || activeViewMode !== "view") return;
      if (drawActive) {
        renderImageView();
        return;
      }
      var imgEl = viewEl.querySelector(".kit-docs-img-wrap img");
      var wrapEl = viewEl.querySelector(".kit-docs-img-wrap");
      if (imgEl && wrapEl) startImageDrawMode(imgEl, wrapEl);
    });

    function openRepoDoc(relPath, section, repoOverride) {
      modal.hidden = false; _kitWinZ += 1; modal.style.zIndex = String(_kitWinZ);
      armDocsBackTrap();
      if (!modal.dataset.kitUserGeom && !modal.dataset.kitUrlGeom) {
        if (isPrimary) {
          placeWindowLeftOfOthers(modal, 920, 740, "docs", "docs", 400, 300);
        } else {
          placeWindowLeftOfOthers(modal, 840, 700, "docs:" + seq, "docs", 360, 280);
        }
      }
      syncDocsScale(modal);
      activePath = relPath || ""; activeRepoOverride = repoOverride; activeHeadings = [];
      activeViewMode = "view"; drawActive = false; customImgList = null; activeImgDimKey = ""; clearFloatingBars();
      if (isPrimary) {
        _kitDocsState.open = true; _kitDocsState.path = activePath; _kitDocsState.section = section || "";
        syncUrlWorkspaceState();
      }
      if (!allDocs.length) loadDocsIndex(renderList); else renderList();
      if (!relPath) { setLeft(true); updateHeaderModeButtons(); return; }
      setLeft(false);
      titleEl.textContent = shortName(relPath);
      subEl.textContent = relPath;
      viewEl.classList.remove("media-mode");
      viewEl.innerHTML = '<div class="note" style="padding-top:16px">Loading ' + relPath + '...</div>';
      var u = "/_api/repo-doc?path=" + encodeURIComponent(relPath) + (section ? "&section=" + encodeURIComponent(section) : "");
      if (repoOverride !== undefined) {
        u += "&repo=" + encodeURIComponent(repoOverride || "");
      }
      api("GET", u).then(function (res) {
        markDocSeen(res.path, res.mtime);
        renderList();
        var isPinned = allDocs.some(function (d) { return d.path === res.path && d.pinned; });
        pinHdrBtn.classList.toggle("ember", isPinned);
        pinHdrBtn.title = isPinned ? "Unpin file" : "Pin file";
        activeDocMetaText = res.path + " · " + fmtTime(res.mtime) + " · " + fmtKb(res.size);
        subEl.textContent = activeDocMetaText;
        selectedHeadingId = "";
        activeKind = res.kind || "md";
        if (activeKind === "img") {
          activeImgUrl = res.url || resolveAssetUrl(res.path, "", repoOverride);
          cmtHdrBtn.title = "Comment on " + shortName(res.path);
          cmtHdrBtn.setAttribute("aria-label", cmtHdrBtn.title);
          renderImageView();
          return;
        }
        if (activeKind === "html") {
          activeHtmlUrl = withKitToken(res.url || "");
          activeHtmlSource = res.content || "";
          htmlMode = "desktop";
          cmtHdrBtn.title = "Comment on " + shortName(res.path);
          cmtHdrBtn.setAttribute("aria-label", cmtHdrBtn.title);
          renderHtmlView();
          return;
        }
        viewEl.classList.remove("media-mode");
        viewEl.textContent = "";
        var mdBox = document.createElement("div"); mdBox.className = "kit-docs-md body";
        mdBox.innerHTML = renderRichDocMarkdown(res.content || "", res.path || relPath, repoOverride);
        var hNodes = mdBox.querySelectorAll("h1,h2,h3,h4");
        var srvHeadings = res.headings || [];
        var domHeadings = [];
        for (var i = 0; i < hNodes.length; i++) {
          var hn = hNodes[i];
          var txt = (hn.textContent || "").replace(/\s+/g, " ").trim() || ("Section " + (i + 1));
          var slug = txt.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || ("sec-" + i);
          var hid = (isPrimary ? "h-" : ("h" + seq + "-")) + i + "-" + slug;
          var lvl = Number((hn.tagName || "H2").slice(1)) || 2;
          hn.id = hid;
          hn.dataset.slug = slug;
          domHeadings.push({ id: hid, text: txt, level: lvl, slug: slug });
        }
        activeHeadings = domHeadings.length ? domHeadings : srvHeadings;
        viewEl.appendChild(mdBox);
        renderOutline();
        updateHeaderModeButtons();
        var matchTarget = null;
        if (res.match_heading) matchTarget = document.getElementById(res.match_heading);
        if (!matchTarget && section) {
          var sq = String(section).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
          for (var j = 0; j < activeHeadings.length; j++) {
            if (activeHeadings[j].slug === sq || activeHeadings[j].text.toLowerCase().indexOf(String(section).toLowerCase()) >= 0) {
              matchTarget = document.getElementById(activeHeadings[j].id);
              break;
            }
          }
        }
        if (matchTarget && matchTarget.id) {
          setTimeout(function () { selectAndScrollToHeading(matchTarget.id, true); }, 40);
        } else {
          syncOutlineActive();
        }
      }, function (e) {
        viewEl.innerHTML = '<div class="note" style="padding-top:16px">Could not load ' + relPath + ' (' + (e && e.message || "error") + ').</div>';
      });
    }

    function closeWin() {
      if (isPrimary) {
        modal.hidden = true;
        _kitDocsState.open = false;
        syncUrlWorkspaceState();
      } else {
        modal.remove();
        _kitDocInstances = _kitDocInstances.filter(function (x) { return x !== inst; });
        syncUrlWorkspaceState();
      }
    }
    closeHdrBtn.addEventListener("click", closeWin);
    qEl.addEventListener("input", renderList);

    function openDirectImage(imgSrc, label, siblings, opts) {
      opts = opts || {};
      modal.hidden = false; _kitWinZ += 1; modal.style.zIndex = String(_kitWinZ);
      armDocsBackTrap();
      if (!modal.dataset.kitUserGeom && !modal.dataset.kitUrlGeom) {
        placeWindowLeftOfOthers(modal, 860, 700, isPrimary ? "docs" : ("docs:" + seq), "docs", 360, 280);
      }
      activePath = label || imgSrc;
      activeKind = "img";
      activeImgUrl = imgSrc;
      activeImgDimKey = "";
      listDrawer.classList.remove("open");
      setLeft(false);
      customImgList = Array.isArray(siblings) && siblings.length > 1 ? siblings : null;
      titleEl.textContent = shortName(label || imgSrc);
      subEl.textContent = customImgList ? (customImgList.length + " images in post") : (label || imgSrc);
      cmtHdrBtn.title = "Comment on " + shortName(label || imgSrc);
      cmtHdrBtn.setAttribute("aria-label", cmtHdrBtn.title);
      if (opts.startGallery) {
        renderGalleryView();
        return;
      }
      renderImageView();
      if (opts.startDraw) {
        var imgEl = viewEl.querySelector(".kit-docs-img-wrap img");
        var wrapEl = viewEl.querySelector(".kit-docs-img-wrap");
        if (imgEl && wrapEl) {
          if (imgEl.complete) startImageDrawMode(imgEl, wrapEl);
          else imgEl.addEventListener("load", function () { startImageDrawMode(imgEl, wrapEl); }, { once: true });
        }
      }
    }

    var inst = {
      modal: modal,
      isPrimary: isPrimary,
      openRepoDoc: openRepoDoc,
      openDirectImage: openDirectImage,
      renderList: renderList,
      setLeft: setLeft,
      closeWin: closeWin,
      getActivePath: function () { return activePath; },
      jumpAnchor: function (sec) {
        var secClean = String(sec || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        for (var hi = 0; hi < activeHeadings.length; hi++) {
          if (activeHeadings[hi].slug === secClean || activeHeadings[hi].id === sec || activeHeadings[hi].text.toLowerCase().indexOf(secClean.replace(/-/g, " ")) >= 0) {
            selectAndScrollToHeading(activeHeadings[hi].id, true);
            return;
          }
        }
      }
    };
    _kitDocInstances.push(inst);
    return inst;
    }

    var primaryInst = createDocViewerWindow(true);
    window._kitOpenRepoDoc = function (relPath, section, repoOverride, opts) {
      opts = opts || {};
      if (opts.newWindow && window.innerWidth >= 720) {
        var nw = createDocViewerWindow(false);
        nw.openRepoDoc(relPath, section, repoOverride);
        return nw;
      }
      primaryInst.openRepoDoc(relPath, section, repoOverride);
      return primaryInst;
    };
    window._kitOpenImageViewer = function (imgSrc, label, siblings, opts) {
      opts = opts || {};
      if (opts.newWindow && window.innerWidth >= 720) {
        var nw = createDocViewerWindow(false);
        nw.openDirectImage(imgSrc, label, siblings, opts);
        return nw;
      }
      primaryInst.openDirectImage(imgSrc, label, siblings, opts);
      return primaryInst;
    };
    /* Clicking any image inside a post or comment opens it in the Image Viewer (with post-siblings gallery & compare) */
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey) return;
      var img = e.target && e.target.closest ? e.target.closest("#p-imgs .pic img, #p-body img, .kit-post-win-body img, .cmt img, .ffeed-card img") : null;
      if (!img || !img.getAttribute("src")) return;
      if (img.closest && img.closest(".kit-docs-modal")) return;
      e.preventDefault();
      e.stopPropagation();
      var container = img.closest("#p-imgs, #sheet, .kit-post-win, .ffeed-card") || document;
      var allImgs = container.querySelectorAll(".pic img, #p-body img, .kit-post-win-body img, .cmt img");
      var siblings = [], seenSrc = {};
      for (var i = 0; i < allImgs.length; i++) {
        var s = allImgs[i].getAttribute("src") || "";
        if (!s || seenSrc[s]) continue;
        seenSrc[s] = true;
        siblings.push({ src: s, label: allImgs[i].getAttribute("alt") || shortName(s) });
      }
      var src = img.getAttribute("src");
      var lbl = img.getAttribute("alt") || shortName(src);
      window._kitOpenImageViewer(src, lbl, siblings, { newWindow: !!(e.shiftKey && window.innerWidth >= 720) });
    }, true);
    btn.addEventListener("click", function (ev) {
      if (ev.shiftKey && window.innerWidth >= 720) {
        var nw = createDocViewerWindow(false);
        nw.openRepoDoc("", "");
        loadDocsIndex();
        return;
      }
      primaryInst.modal.hidden = false; _kitWinZ += 1; primaryInst.modal.style.zIndex = String(_kitWinZ);
      if (!primaryInst.modal.dataset.kitUserGeom && !primaryInst.modal.dataset.kitUrlGeom) {
        placeWindowLeftOfOthers(primaryInst.modal, 920, 740, "docs", "docs", 400, 300);
      }
      syncDocsScale(primaryInst.modal);
      _kitDocsState.open = true;
      syncUrlWorkspaceState();
      loadDocsIndex();
      if (!primaryInst.getActivePath()) primaryInst.setLeft(true);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var topInst = null, topZ = -1;
      for (var i = 0; i < _kitDocInstances.length; i++) {
        var it = _kitDocInstances[i];
        if (!it.modal.hidden) {
          var z = Number(it.modal.style.zIndex || 0);
          if (z >= topZ) { topZ = z; topInst = it; }
        }
      }
      if (topInst) topInst.closeWin();
    });
    /* Intercept clicks on any GitHub link to an associated repo or relative doc/image/html link and open directly in Document Viewer */
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey) return;
      var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (a) {
        var href = a.getAttribute("href") || "";
        var hostInst = null;
        for (var k = 0; k < _kitDocInstances.length; k++) {
          if (!_kitDocInstances[k].modal.hidden && _kitDocInstances[k].modal.contains(a)) { hostInst = _kitDocInstances[k]; break; }
        }
        var parsedLink = parseAssociatedDocLink(href, hostInst ? hostInst.getActivePath() : "");
        if (parsedLink) {
          e.preventDefault();
          e.stopPropagation();
          if (parsedLink.anchorOnly) {
            if (hostInst && parsedLink.section) hostInst.jumpAnchor(parsedLink.section);
            return;
          }
          if (e.shiftKey && window.innerWidth >= 720) {
            window._kitOpenRepoDoc(parsedLink.path, parsedLink.section || "", parsedLink.repoId, { newWindow: true });
          } else if (hostInst) {
            hostInst.openRepoDoc(parsedLink.path, parsedLink.section || "", parsedLink.repoId);
          } else {
            window._kitOpenRepoDoc(parsedLink.path, parsedLink.section || "", parsedLink.repoId);
          }
          return;
        }
      }
      if (!e.shiftKey) return;
      var card = e.target && e.target.closest ? e.target.closest(".card[data-id], .ffeed-card[data-id]") : null;
      if (card && card.dataset.id) { e.preventDefault(); e.stopPropagation(); openPostWindow(card.dataset.id); }
    }, true);
    loadDocsIndex();
  }

  function makeInlineResizable(card, bodyEl, kindKey, postSpecificKey) {
    if (!card || card.dataset.kitInlineRes) return null;
    card.dataset.kitInlineRes = "1";
    var curMode = "default";
    function keysFor(mode) {
      var devBucket = window.innerWidth <= 720 ? "phone" : "desk";
      var k = mode && mode !== "default" ? (kindKey + ":" + mode) : kindKey;
      return {
        typeKey: "kit_inline_size:" + devBucket + ":" + k,
        specKey: postSpecificKey ? ("kit_inline_size:" + devBucket + ":" + k + ":" + postSpecificKey) : ""
      };
    }
    function applyMode(mode, fallbackW, fallbackH) {
      curMode = mode || "default";
      var ks = keysFor(curMode);
      var raw = "";
      try { raw = (ks.specKey && localStorage.getItem(ks.specKey)) || localStorage.getItem(ks.typeKey) || ""; } catch (e) {}
      var g = null;
      if (raw) { try { g = JSON.parse(raw); } catch (e) { g = null; } }
      var w = (g && g.w > 220) ? g.w : (fallbackW || 0);
      var h = (g && g.h > 140) ? g.h : (fallbackH || 0);
      if (w > 220 && window.innerWidth >= 720) {
        card.style.width = Math.min(w, window.innerWidth - 36) + "px";
        card.style.marginLeft = "50%";
        card.style.transform = "translateX(-50%)";
      } else {
        card.style.width = "";
        card.style.marginLeft = "";
        card.style.transform = "";
      }
      if (h > 140 && bodyEl) {
        bodyEl.style.maxHeight = "none";
        bodyEl.style.height = Math.min(h, Math.max(420, window.innerHeight - 90)) + "px";
        var ifr = bodyEl.querySelector("iframe");
        if (ifr) ifr.style.height = "100%";
      } else if (bodyEl) {
        bodyEl.style.maxHeight = "";
        bodyEl.style.height = "";
      }
      return { w: w, h: h, fromSaved: !!g };
    }
    applyMode("default");
    var edgePx = 8;
    function hitInlineEdge(e) {
      if (window.innerWidth < 720) return "";
      var r = card.getBoundingClientRect();
      var x = e.clientX - r.left, y = e.clientY - r.top, s = "";
      if (y >= r.height - edgePx) s += "s";
      if (x <= edgePx) s += "w"; else if (x >= r.width - edgePx) s += "e";
      return s;
    }
    var cmap = { s: "ns-resize", e: "ew-resize", w: "ew-resize", se: "nwse-resize", sw: "nesw-resize" };
    card.addEventListener("mousemove", function (e) {
      if (card.dataset.kitResizing) return;
      var ed = hitInlineEdge(e);
      card.style.cursor = ed ? cmap[ed] : "";
    });
    card.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      var ed = hitInlineEdge(e);
      if (!ed) return;
      e.preventDefault(); e.stopPropagation();
      card.dataset.kitResizing = "1";
      var r0 = card.getBoundingClientRect(), b0 = bodyEl ? bodyEl.getBoundingClientRect() : r0;
      var sx = e.clientX, sy = e.clientY;
      var iframes = card.querySelectorAll("iframe");
      for (var k = 0; k < iframes.length; k++) iframes[k].style.pointerEvents = "none";
      function onMove(ev) {
        var dx = ev.clientX - sx, dy = ev.clientY - sy;
        if (ed.indexOf("e") >= 0 || ed.indexOf("w") >= 0) {
          var deltaW = ed.indexOf("e") >= 0 ? dx * 2 : -dx * 2;
          var nw = Math.max(280, Math.min(window.innerWidth - 32, Math.round(r0.width + deltaW)));
          card.style.width = nw + "px";
          card.style.marginLeft = "50%";
          card.style.transform = "translateX(-50%)";
        }
        if (ed.indexOf("s") >= 0 && bodyEl) {
          var nh = Math.max(180, Math.min(window.innerHeight - 60, Math.round(b0.height + dy)));
          bodyEl.style.maxHeight = "none";
          bodyEl.style.height = nh + "px";
          var ifr = bodyEl.querySelector("iframe");
          if (ifr) ifr.style.height = "100%";
        }
      }
      function onUp() {
        delete card.dataset.kitResizing;
        for (var k = 0; k < iframes.length; k++) iframes[k].style.pointerEvents = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        var rf = card.getBoundingClientRect(), bf = bodyEl ? bodyEl.getBoundingClientRect() : rf;
        var payload = JSON.stringify({ w: Math.round(rf.width), h: Math.round(bf.height) });
        var ks = keysFor(curMode);
        try {
          if (ks.specKey) localStorage.setItem(ks.specKey, payload);
          var sameKindCount = document.querySelectorAll('.kit-inline-embed[data-kind="' + kindKey + '"]').length;
          if (sameKindCount <= 1 || !localStorage.getItem(ks.typeKey)) localStorage.setItem(ks.typeKey, payload);
        } catch (err) {}
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    return { applyMode: applyMode };
  }

  function mountInlineViewerEmbed(anchorEl, fp, sec, repoOverride) {
    var postBody = anchorEl && anchorEl.closest ? anchorEl.closest("#p-body, .kit-post-win-body") : null;
    if (!postBody || (anchorEl.closest && anchorEl.closest(".kit-inline-embed, .kit-docs-modal"))) return;
    var key = String(repoOverride || "") + "|" + fp + "|" + (sec || "");
    var existingEmbeds = postBody.querySelectorAll(".kit-inline-embed");
    if (existingEmbeds.length >= 6) return;
    for (var ei = 0; ei < existingEmbeds.length; ei++) {
      if (existingEmbeds[ei].dataset.ekey === key) return;
    }
    var isImg = /\.(?:png|jpe?g|gif|webp|avif|svg)$/i.test(fp);
    var isHtml = /\.html?$/i.test(fp);
    var isMd = /\.md$/i.test(fp);
    /* Embed images, HTML pages, and Markdown section links inline right under their referencing block */
    if (!isImg && !isHtml && !(isMd && sec)) return;

    var SVG_POP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
    var SVG_CMT2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>';
    var SVG_PH2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>';
    var SVG_DK2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
    var SVG_CD2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="14" y1="4" x2="10" y2="20"/></svg>';

    var kindTag = isHtml ? "html" : (isImg ? "img" : "md");
    var postIdCtx = "";
    try { postIdCtx = (postBody.closest(".kit-post-win") && postBody.closest(".kit-post-win").dataset.pid) || sessionStorage.getItem("board-open") || ""; } catch (e) {}
    var card = document.createElement("div"); card.className = "kit-inline-embed"; card.dataset.kind = kindTag; card.dataset.ekey = key;
    var hdr = document.createElement("div"); hdr.className = "kit-inline-embed-hdr";
    var ttl = document.createElement("span"); ttl.className = "iettl";
    ttl.textContent = fp + (sec ? " § " + sec : ""); ttl.title = ttl.textContent;
    hdr.appendChild(ttl);

    var body = document.createElement("div"); body.className = "kit-inline-embed-body";
    body.innerHTML = '<div class="note">Loading ' + fp + '...</div>';
    var resCtrl = makeInlineResizable(card, body, kindTag, postIdCtx + ":" + fp);

    var cmtB = document.createElement("button"); cmtB.type = "button"; cmtB.className = "btn small kit-icon-btn";
    cmtB.title = "Comment on " + ttl.textContent; cmtB.setAttribute("aria-label", cmtB.title); cmtB.innerHTML = SVG_CMT2;
    cmtB.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (typeof window._kitOpenOrchNew === "function") {
        window._kitOpenOrchNew("Re `" + fp + (sec ? " § " + sec : "") + "`:\n");
      }
    });
    var popB = document.createElement("button"); popB.type = "button"; popB.className = "btn small kit-icon-btn";
    popB.title = "Open in Document Viewer (Shift+click for new window)"; popB.setAttribute("aria-label", "Open in Document Viewer"); popB.innerHTML = SVG_POP;
    popB.addEventListener("click", function (ev) {
      ev.stopPropagation();
      window._kitOpenRepoDoc(fp, sec || "", repoOverride, { newWindow: !!(ev.shiftKey && window.innerWidth >= 720) });
    });

    if (isHtml) {
      var hMode = "desktop", hUrl = "", hSrc = "";
      var phB = document.createElement("button"); phB.type = "button"; phB.className = "btn small kit-icon-btn"; phB.title = "Mobile view"; phB.innerHTML = SVG_PH2;
      var dkB = document.createElement("button"); dkB.type = "button"; dkB.className = "btn small kit-icon-btn on"; dkB.title = "Desktop view"; dkB.innerHTML = SVG_DK2;
      var cdB = document.createElement("button"); cdB.type = "button"; cdB.className = "btn small kit-icon-btn"; cdB.title = "View HTML code"; cdB.innerHTML = SVG_CD2;
      function drawInlineHtml() {
        phB.classList.toggle("on", hMode === "mobile");
        dkB.classList.toggle("on", hMode === "desktop");
        cdB.classList.toggle("on", hMode === "code");
        body.textContent = "";
        body.classList.toggle("media", hMode !== "code");
        body.classList.toggle("phone", hMode === "mobile");
        if (hMode === "mobile") {
          var defMobH = Math.min(760, Math.max(600, window.innerHeight - 140));
          if (resCtrl) resCtrl.applyMode("mobile", 416, defMobH);
        } else {
          if (resCtrl) resCtrl.applyMode("desktop", 0, 0);
        }
        if (hMode === "code") {
          var pre = document.createElement("pre"); pre.className = "kit-docs-code"; pre.textContent = hSrc;
          body.appendChild(pre);
        } else {
          var ifr = document.createElement("iframe");
          ifr.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-same-origin");
          ifr.srcdoc = buildHtmlSrcdoc(hSrc, hUrl);
          if (body.style.height) ifr.style.height = "100%";
          body.appendChild(ifr);
        }
      }
      phB.addEventListener("click", function () { hMode = "mobile"; drawInlineHtml(); });
      dkB.addEventListener("click", function () { hMode = "desktop"; drawInlineHtml(); });
      cdB.addEventListener("click", function () { hMode = hMode === "code" ? "desktop" : "code"; drawInlineHtml(); });
      hdr.appendChild(phB); hdr.appendChild(dkB); hdr.appendChild(cdB);
      var uH = "/_api/repo-doc?path=" + encodeURIComponent(fp) + (repoOverride !== undefined ? "&repo=" + encodeURIComponent(repoOverride || "") : "");
      api("GET", uH).then(function (res) {
        hUrl = withKitToken(res.url || ""); hSrc = res.content || "";
        drawInlineHtml();
      }, function () { card.remove(); });
    } else if (isImg) {
      card.classList.add("is-img");
      body.classList.add("media");
      body.textContent = "";
      var im = document.createElement("img");
      im.src = resolveAssetUrl(fp, "", repoOverride);
      im.alt = fp;
      body.appendChild(im);
    } else {
      var uM = "/_api/repo-doc?path=" + encodeURIComponent(fp) + (sec ? "&section=" + encodeURIComponent(sec) : "") + (repoOverride !== undefined ? "&repo=" + encodeURIComponent(repoOverride || "") : "");
      api("GET", uM).then(function (res) {
        body.textContent = "";
        var md = document.createElement("div"); md.className = "kit-docs-md body";
        md.innerHTML = renderRichDocMarkdown(res.content || "", res.path || fp, repoOverride);
        body.appendChild(md);
        if (sec) {
          var sq = String(sec).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
          var hs = md.querySelectorAll("h1,h2,h3,h4");
          for (var i = 0; i < hs.length; i++) {
            var ht = (hs[i].textContent || "").toLowerCase();
            var hslug = ht.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            if (hslug === sq || ht.indexOf(String(sec).toLowerCase()) >= 0) {
              hs[i].classList.add("kit-sec-hit");
              var targetH = hs[i];
              setTimeout(function () { body.scrollTop = Math.max(0, targetH.offsetTop - 10); }, 20);
              break;
            }
          }
        }
      }, function () { card.remove(); });
    }

    hdr.appendChild(cmtB); hdr.appendChild(popB);
    card.appendChild(hdr); card.appendChild(body);
    var hostBlk = anchorEl.closest("p, li, blockquote, pre, div");
    if (hostBlk && hostBlk.parentNode && postBody.contains(hostBlk) && hostBlk !== postBody) {
      hostBlk.parentNode.insertBefore(card, hostBlk.nextSibling);
    } else {
      postBody.appendChild(card);
    }
  }

  function linkRepoDocs(root) {
    if (!root || typeof window._kitOpenRepoDoc !== "function") return;
    var codes = root.querySelectorAll("#p-body code, .cmt .txt code, .kit-post-win-body code, .ffeed-card .body code");
    var fileRe = /^(?:docs\/|\.story\/|\.board\/|\.kit\/)?[A-Za-z0-9_./-]+\.(?:md|html?|png|jpe?g|gif|webp|avif|svg)$/i;
    for (var i = 0; i < codes.length; i++) {
      var c = codes[i];
      if (c.dataset.kitDocLink) continue;
      if (c.closest && c.closest("a[href]")) continue;
      var raw = (c.textContent || "").trim();
      var sec = "";
      var sm = raw.match(/^([A-Za-z0-9_./-]+\.md)\s+§\s*([A-Za-z0-9._\s-]+)$/);
      if (sm) { raw = sm[1]; sec = sm[2].trim(); }
      if (!fileRe.test(raw)) continue;
      c.dataset.kitDocLink = "1";
      c.classList.add("kit-doc-link");
      c.title = "Open " + raw + (sec ? " § " + sec : "") + " from repo (Shift+click for new window)";
      (function (fp, sc) {
        c.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window._kitOpenRepoDoc(fp, sc, undefined, { newWindow: !!(ev.shiftKey && window.innerWidth >= 720) });
        });
      })(raw, sec);
      mountInlineViewerEmbed(c, raw, sec, undefined);
    }
    var anchors = root.querySelectorAll("a[href]");
    for (var j = 0; j < anchors.length; j++) {
      var a = anchors[j];
      if (a.dataset.kitGhChecked) continue;
      a.dataset.kitGhChecked = "1";
      var pl = parseAssociatedDocLink(a.getAttribute("href") || "", "");
      if (pl && !pl.anchorOnly) {
        a.classList.add("kit-doc-link");
        a.removeAttribute("target");
        a.title = "Open " + pl.path + (pl.section ? " § " + pl.section : "") + " in Document Viewer";
        mountInlineViewerEmbed(a, pl.path, pl.section || "", pl.repoId);
      }
    }
    /* Unified inline post media viewer: resolve repo images, follow zoomed image size, and synchronize pinch-to-zoom across side-by-side compare groups */
    var postImgs = root.querySelectorAll("#p-imgs .pic img, .kit-inline-cmp img, #p-body img, .kit-post-win-body img, .cmt img");
    for (var m = 0; m < postImgs.length; m++) {
      var im = postImgs[m];
      if (im.dataset.kitInlineZoom) continue;
      im.dataset.kitInlineZoom = "1";
      var rawSrc = im.getAttribute("src") || "";
      if (rawSrc && !/^(?:https?:\/\/|data:|blob:|\/_blob\/|\/_api\/|\/_repo\/)/i.test(rawSrc)) {
        im.src = resolveAssetUrl(rawSrc, "");
      }
      (function (imgEl) {
        var dimKey = "", pTouch = null;
        function applyZoomToTarget(targetImg, nz, save) {
          var z = Math.max(50, Math.min(260, Math.round(nz)));
          targetImg.dataset.kitZoom = String(z);
          var cmpRow = targetImg.closest ? targetImg.closest("#p-imgs .grp-row, .kit-inline-cmp, .kit-docs-cmp-grid") : null;
          if (cmpRow) {
            var baseW = Math.min(1080, window.innerWidth - 72);
            var scaledRowW = Math.max(280, Math.min(window.innerWidth - 20, Math.round(baseW * (z / 100))));
            cmpRow.style.width = scaledRowW + "px";
            if (window.innerWidth >= 720) {
              cmpRow.style.marginLeft = "50%";
              cmpRow.style.transform = "translateX(-50%)";
            }
            var sibImgs = cmpRow.querySelectorAll("img");
            for (var si = 0; si < sibImgs.length; si++) {
              sibImgs[si].dataset.kitZoom = String(z);
              sibImgs[si].style.transform = "";
            }
          } else {
            var embedCard = targetImg.closest ? targetImg.closest(".kit-inline-embed.is-img") : null;
            var natW = targetImg.naturalWidth || targetImg.clientWidth || 480;
            var scaledW = Math.max(180, Math.min(window.innerWidth - 28, Math.round(natW * (z / 100))));
            targetImg.style.transform = "";
            targetImg.style.maxHeight = "none";
            targetImg.style.width = z === 100 ? "" : (scaledW + "px");
            if (embedCard) {
              var eb = embedCard.querySelector(".kit-inline-embed-body");
              if (eb) eb.style.maxHeight = "none";
              if (z !== 100 && window.innerWidth >= 720) {
                embedCard.style.width = Math.min(window.innerWidth - 24, scaledW + 24) + "px";
                embedCard.style.marginLeft = "50%";
                embedCard.style.transform = "translateX(-50%)";
              }
            }
          }
          if (save && dimKey) { try { localStorage.setItem(dimKey, String(z)); } catch (e) {} }
        }
        function initDim() {
          if (imgEl.naturalWidth > 0 && imgEl.naturalHeight > 0) {
            dimKey = "kit_img_zoom:" + (window.innerWidth <= 720 ? "phone" : "desk") + ":" + imgEl.naturalWidth + "x" + imgEl.naturalHeight;
            try {
              var sz = Number(localStorage.getItem(dimKey) || "0");
              if (sz >= 50 && sz <= 260 && sz !== 100) applyZoomToTarget(imgEl, sz, false);
            } catch (e) {}
          }
        }
        if (imgEl.complete) initDim(); else imgEl.addEventListener("load", initDim);
        imgEl.addEventListener("wheel", function (ev) {
          if (ev.ctrlKey || ev.metaKey) {
            ev.preventDefault(); ev.stopPropagation();
            var cur = Number(imgEl.dataset.kitZoom || "100");
            var step = Math.max(3, Math.min(18, Math.abs(ev.deltaY) * 0.8));
            applyZoomToTarget(imgEl, cur + (ev.deltaY < 0 ? step : -step), true);
          }
        }, { passive: false });
        imgEl.addEventListener("touchstart", function (ev) {
          if (ev.touches && ev.touches.length === 2) {
            ev.preventDefault();
            var d0 = Math.hypot(ev.touches[1].clientX - ev.touches[0].clientX, ev.touches[1].clientY - ev.touches[0].clientY) || 1;
            pTouch = { d0: d0, z0: Number(imgEl.dataset.kitZoom || "100") };
          }
        }, { passive: false });
        imgEl.addEventListener("touchmove", function (ev) {
          if (pTouch && ev.touches && ev.touches.length === 2) {
            ev.preventDefault(); ev.stopPropagation();
            var d1 = Math.hypot(ev.touches[1].clientX - ev.touches[0].clientX, ev.touches[1].clientY - ev.touches[0].clientY) || pTouch.d0;
            applyZoomToTarget(imgEl, pTouch.z0 * (d1 / pTouch.d0), true);
          }
        }, { passive: false });
        imgEl.addEventListener("touchend", function (ev) {
          if (!ev.touches || ev.touches.length < 2) pTouch = null;
        }, { passive: true });
      })(im);
    }
    /* Fading semi-transparent Image Viewer header (Like + Gallery + Draw + Comment + Open in Viewer) on every inline post picture */
    var SVG_LIKE_O = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
    var SVG_GAL_O = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>';
    var SVG_DRAW_O = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';
    var SVG_CMT_O = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>';
    var SVG_POP_O = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
    var pics = root.querySelectorAll("#p-imgs .pic");
    var allPicSibs = [];
    for (var sIdx = 0; sIdx < pics.length; sIdx++) {
      var sIm = pics[sIdx].querySelector("img");
      if (sIm && sIm.getAttribute("src")) {
        var sCap = pics[sIdx].querySelector(".cap");
        allPicSibs.push({ src: sIm.getAttribute("src"), label: (sCap && sCap.textContent) || pics[sIdx].dataset.image || sIm.getAttribute("alt") || ("Picture " + (sIdx + 1)) });
      }
    }
    for (var pIdx = 0; pIdx < pics.length; pIdx++) {
      var picEl = pics[pIdx];
      if (picEl.querySelector(".kit-pic-hdr")) continue;
      var picImg = picEl.querySelector("img");
      if (!picImg) continue;
      var capEl = picEl.querySelector(".cap");
      var picLabel = (capEl && capEl.textContent) || picEl.dataset.image || picImg.getAttribute("alt") || ("Picture " + (pIdx + 1));
      var picRef = picEl.dataset.image || picLabel;
      var hdrBar = document.createElement("div"); hdrBar.className = "kit-pic-hdr";
      var ttlSpan = document.createElement("span"); ttlSpan.className = "pttl"; ttlSpan.textContent = picLabel; ttlSpan.title = picLabel;
      hdrBar.appendChild(ttlSpan);

      var lkBtn = document.createElement("button"); lkBtn.type = "button";
      lkBtn.title = "Like picture"; lkBtn.setAttribute("aria-label", "Like picture"); lkBtn.innerHTML = SVG_LIKE_O;
      var nativeLk = picEl.querySelector(".like-slot button");
      if (nativeLk && nativeLk.classList.contains("on")) lkBtn.classList.add("liked");

      var galBtn = document.createElement("button"); galBtn.type = "button";
      galBtn.title = "Gallery view"; galBtn.setAttribute("aria-label", "Gallery view"); galBtn.innerHTML = SVG_GAL_O;

      var drawBtn = document.createElement("button"); drawBtn.type = "button";
      drawBtn.title = "Annotate and comment"; drawBtn.setAttribute("aria-label", "Annotate and comment"); drawBtn.innerHTML = SVG_DRAW_O;

      var cmtBtn = document.createElement("button"); cmtBtn.type = "button";
      cmtBtn.title = "Comment on picture"; cmtBtn.setAttribute("aria-label", "Comment on picture"); cmtBtn.innerHTML = SVG_CMT_O;

      var popBtn = document.createElement("button"); popBtn.type = "button";
      popBtn.title = "Open in Image Viewer"; popBtn.setAttribute("aria-label", "Open in Image Viewer"); popBtn.innerHTML = SVG_POP_O;

      (function (pNode, imgNode, hBar, lBtn, gBtn, dBtn, cBtn, oBtn, refStr, lblStr, sibs) {
        var idleT = setTimeout(function () { hBar.classList.add("idle"); }, 2200);
        function wakeHdr() {
          clearTimeout(idleT);
          hBar.classList.remove("idle");
          idleT = setTimeout(function () { hBar.classList.add("idle"); }, 2200);
        }
        pNode.addEventListener("mousemove", wakeHdr, { passive: true });
        pNode.addEventListener("touchstart", wakeHdr, { passive: true });
        lBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          var nl = pNode.querySelector(".like-slot button");
          if (nl) { nl.click(); setTimeout(function () { var nl2 = pNode.querySelector(".like-slot button"); lBtn.classList.toggle("liked", !!(nl2 && nl2.classList.contains("on"))); }, 80); }
        });
        gBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window._kitOpenImageViewer(imgNode.getAttribute("src"), lblStr, sibs, { startGallery: true });
        });
        dBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window._kitOpenImageViewer(imgNode.getAttribute("src"), refStr, sibs, { startDraw: true });
        });
        cBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (typeof window._kitOpenOrchNew === "function") window._kitOpenOrchNew("Re `" + refStr + "`:\n");
        });
        oBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          window._kitOpenImageViewer(imgNode.getAttribute("src"), refStr, sibs, { newWindow: !!(ev.shiftKey && window.innerWidth >= 720) });
        });
      })(picEl, picImg, hdrBar, lkBtn, galBtn, drawBtn, cmtBtn, popBtn, picRef, picLabel, allPicSibs);

      hdrBar.appendChild(lkBtn);
      if (allPicSibs.length > 1) hdrBar.appendChild(galBtn);
      hdrBar.appendChild(drawBtn);
      hdrBar.appendChild(cmtBtn);
      hdrBar.appendChild(popBtn);
      picEl.appendChild(hdrBar);
    }
  }

  var _kitMineOnly = false;
  function applyCollabCardBadges() {
    var hasCollab = document.documentElement.dataset.collab === "1";
    var uid = curUserId();
    if (!hasCollab) {
      if (_kitMineOnly) {
        _kitMineOnly = false;
        var mb = document.getElementById("kit-mine-btn");
        if (mb) mb.classList.remove("on");
        var allC = document.querySelectorAll(".card[data-id], .ffeed-card[data-id]");
        for (var i = 0; i < allC.length; i++) allC[i].style.display = "";
      }
      return;
    }
    api("GET", "/_api/db/posts").then(function (res) {
      var byId = {};
      ((res && res.docs) || []).forEach(function (d) { byId[d.id] = d.data || {}; });
      var cards = document.querySelectorAll(".card[data-id], .ffeed-card[data-id]");
      for (var i = 0; i < cards.length; i++) {
        var c = cards[i], pid = c.dataset.id, doc = byId[pid] || {};
        var assigned = String(doc.assigned_to || "").trim();
        var working = String(doc.working_by || "").trim();
        var meta = c.querySelector(".meta");
        if (meta) {
          var oldA = meta.querySelector(".kit-assign-pill"); if (oldA) oldA.remove();
          var oldW = meta.querySelector(".kit-work-pill"); if (oldW) oldW.remove();
          if (working) {
            var wp = document.createElement("span"); wp.className = "kit-work-pill kit-collab-only";
            wp.innerHTML = '<span class="wdot" aria-hidden="true"></span><span></span>';
            wp.children[1].textContent = "@" + working;
            wp.title = "Actively worked on by @" + working;
            meta.appendChild(wp);
          }
          if (assigned && assigned !== working) {
            var ap = document.createElement("span"); ap.className = "kit-assign-pill kit-collab-only";
            ap.textContent = "@" + assigned;
            ap.title = "Assigned to @" + assigned;
            meta.appendChild(ap);
          }
        }
        if (_kitMineOnly) {
          c.style.display = (assigned === uid || working === uid) ? "" : "none";
        } else {
          c.style.display = "";
        }
      }
    }, function () {});
  }
  function renderPostArtifacts(sheet, postId) {
    if (!sheet || !postId || postId.charAt(0) === "_") {
      var oldBox = document.getElementById("kit-post-artifacts");
      if (oldBox) oldBox.remove();
      return;
    }
    var bodyEl = sheet.querySelector("#p-body");
    if (!bodyEl) return;
    var artKey = (window.__KIT_ACTIVE_REPO__ || "") + "|" + postId;
    if (bodyEl.dataset.kitArtPost === artKey && (bodyEl.dataset.kitArtEmpty === "1" || document.getElementById("kit-post-artifacts"))) return;
    bodyEl.dataset.kitArtPost = artKey;
    bodyEl.dataset.kitArtEmpty = "0";
    var staleBox = document.getElementById("kit-post-artifacts");
    if (staleBox && staleBox.dataset.artKey !== artKey) staleBox.remove();
    api("GET", "/_api/posts/" + encodeURIComponent(postId) + "/artifacts").then(function (res) {
      if (bodyEl.dataset.kitArtPost !== artKey) return;
      var list = (res && res.artifacts) || [];
      var exist = document.getElementById("kit-post-artifacts");
      if (!list.length) {
        bodyEl.dataset.kitArtEmpty = "1";
        if (exist) exist.remove();
        return;
      }
      if (exist) exist.remove();
      var box = document.createElement("div"); box.id = "kit-post-artifacts";
      box.dataset.artKey = artKey;
      box.style.cssText = "margin:12px 0;padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:var(--sunk);display:flex;flex-direction:column;gap:6px;";
      var hdr = document.createElement("div"); hdr.style.cssText = "font-size:.74rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);";
      hdr.textContent = "Submitted Deliverables (" + list.length + ")";
      box.appendChild(hdr);
      list.forEach(function (a) {
        var row = document.createElement("button"); row.type = "button";
        row.style.cssText = "text-align:left;background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:7px 10px;cursor:pointer;display:flex;align-items:center;gap:8px;min-width:0;";
        var m = a.agent_meta || {};
        var metaStr = [m.harness, m.model, m.effort].filter(Boolean).join(" · ");
        row.title = "@" + (a.by_user || "owner") + (metaStr ? " (" + metaStr + ")" : "") + (a.summary ? " — " + a.summary : "");
        var pSp = document.createElement("span"); pSp.style.cssText = "font-family:var(--mono);font-size:.8rem;font-weight:700;color:var(--ember);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
        pSp.textContent = a.path;
        var sSp = document.createElement("span"); sSp.className = "note"; sSp.style.cssText = "flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
        sSp.textContent = a.summary || ("@" + (a.by_user || "owner"));
        var szSp = document.createElement("span"); szSp.className = "note"; szSp.style.cssText = "font-family:var(--mono);font-size:.72rem;flex:none;";
        szSp.textContent = a.size >= 1024 ? (a.size / 1024).toFixed(1) + " KB" : (a.size + " B");
        row.appendChild(pSp); row.appendChild(sSp); row.appendChild(szSp);
        row.addEventListener("click", function () {
          api("GET", "/_api/artifacts/" + encodeURIComponent(a.artifact_id)).then(function (full) {
            var oldM = document.getElementById("kit-art-modal"); if (oldM) oldM.remove();
            var mWin = document.createElement("section"); mWin.id = "kit-art-modal"; mWin.className = "kit-tl-win";
            var mh = document.createElement("div"); mh.className = "kit-tl-hdr";
            var mb = document.createElement("b"); mb.textContent = full.path;
            var ms = document.createElement("span"); ms.className = "tlsub";
            ms.textContent = "@" + (full.by_user || "owner") + (metaStr ? " · " + metaStr : "");
            var mc = document.createElement("button"); mc.type = "button"; mc.className = "btn small kit-icon-btn";
            mc.title = "Close"; mc.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
            mc.addEventListener("click", function () { mWin.remove(); });
            mh.appendChild(mb); mh.appendChild(ms); mh.appendChild(mc); mWin.appendChild(mh);
            var mBody = document.createElement("div"); mBody.className = "kit-tl-body";
            if (full.diff) {
              var dDet = document.createElement("details"); dDet.open = true;
              var dSum = document.createElement("summary"); dSum.style.cssText = "cursor:pointer;font-weight:700;font-size:.82rem;color:var(--ember);margin-bottom:6px;";
              dSum.textContent = "Diff vs repo " + (full.base_path || full.path);
              var dPre = document.createElement("pre"); dPre.className = "kit-docs-code"; dPre.textContent = full.diff;
              dDet.appendChild(dSum); dDet.appendChild(dPre); mBody.appendChild(dDet);
            }
            if (/\.html?$/i.test(full.path)) {
              var ifr = document.createElement("iframe");
              ifr.style.cssText = "width:100%;height:420px;border:1px solid var(--line);border-radius:10px;background:#fff;";
              ifr.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-same-origin");
              ifr.srcdoc = full.content || "";
              mBody.appendChild(ifr);
            } else if (/\.md$/i.test(full.path)) {
              var mdDiv = document.createElement("div"); mdDiv.className = "kit-docs-md body";
              mdDiv.innerHTML = renderRichDocMarkdown(full.content || "", full.path);
              mBody.appendChild(mdDiv);
            } else {
              var cPre = document.createElement("pre"); cPre.className = "kit-docs-code"; cPre.textContent = full.content || "";
              mBody.appendChild(cPre);
            }
            mWin.appendChild(mBody);
            document.body.appendChild(mWin);
            makeWindow(mWin, mh, { minW: 360, minH: 260, winKey: "artifact" });
            placeWindowLeftOfOthers(mWin, 740, 620, "artifact", "artifact", 360, 260);
          });
        });
        box.appendChild(row);
      });
      bodyEl.insertAdjacentElement("afterend", box);
    }, function () {});
  }
  function syncPostCollabControls(sheet, postId, barRow) {
    var oldWrap = document.getElementById("kit-collab-post-ctrls");
    if (document.documentElement.dataset.collab !== "1" || !postId || postId.charAt(0) === "_" || !barRow) {
      if (oldWrap) oldWrap.remove();
      return;
    }
    var me = window.__KIT_USER__ || { id: "owner", role: "owner", collaborators: [] };
    if (me.role === "viewer") {
      if (oldWrap) oldWrap.remove();
      return;
    }
    var cKey = (window.__KIT_ACTIVE_REPO__ || "") + "|" + postId + "|" + (me.collaborators || []).length;
    if (oldWrap && oldWrap.dataset.collabKey === cKey && oldWrap.parentNode === barRow) return;
    if (oldWrap) oldWrap.remove();
    var wrap = document.createElement("span"); wrap.id = "kit-collab-post-ctrls"; wrap.className = "kit-collab-only";
    wrap.dataset.collabKey = cKey;
    wrap.style.cssText = "display:inline-flex;align-items:center;gap:4px;";
    var sel = document.createElement("select"); sel.className = "kit-assign-sel"; sel.title = "Assign post"; sel.setAttribute("aria-label", "Assign post");
    var opts = [["", "Unassigned"], ["owner", "@owner"]];
    (me.collaborators || []).forEach(function (u) { opts.push([u.id, "@" + u.id]); });
    opts.forEach(function (pair) {
      var o = document.createElement("option"); o.value = pair[0]; o.textContent = pair[1]; sel.appendChild(o);
    });
    var claimB = document.createElement("button"); claimB.type = "button"; claimB.className = "btn small kit-icon-btn";
    claimB.title = "Mark as actively working on this"; claimB.setAttribute("aria-label", claimB.title);
    claimB.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';
    api("GET", "/_api/db/posts/" + encodeURIComponent(postId)).then(function (r) {
      var d = (r && r.data) || {};
      sel.value = String(d.assigned_to || "");
      claimB.classList.toggle("ember", d.working_by === curUserId());
    }, function () {});
    sel.addEventListener("change", function () {
      api("PATCH", "/_api/db/posts/" + encodeURIComponent(postId), { data: { assigned_to: sel.value } }).then(applyCollabCardBadges, function () {});
    });
    claimB.addEventListener("click", function () {
      var on = !claimB.classList.contains("ember");
      claimB.classList.toggle("ember", on);
      api("PATCH", "/_api/db/posts/" + encodeURIComponent(postId), { data: { working_by: on ? curUserId() : "" } }).then(applyCollabCardBadges, function () {});
    });
    wrap.appendChild(sel); wrap.appendChild(claimB);
    barRow.insertBefore(wrap, barRow.firstChild);
  }
  function refreshCollabUiState(cb) {
    api("GET", "/_api/me").then(function (me) {
      if (me && me.id) window.__KIT_USER__ = me;
      var hasC = !!(me && me.has_collaborators);
      document.documentElement.dataset.collab = hasC ? "1" : "0";
      var q = document.getElementById("q");
      if (q && q.parentNode && !document.getElementById("kit-mine-btn")) {
        var mb = document.createElement("button");
        mb.id = "kit-mine-btn"; mb.type = "button"; mb.className = "btn small kit-mine-btn kit-collab-only";
        mb.textContent = "Mine"; mb.title = "Show posts assigned to or worked on by you";
        mb.addEventListener("click", function () {
          _kitMineOnly = !_kitMineOnly;
          mb.classList.toggle("on", _kitMineOnly);
          applyCollabCardBadges();
        });
        q.parentNode.insertBefore(mb, q.nextSibling);
      }
      applyCollabCardBadges();
      if (cb) cb(me);
    }, function () {});
  }
  function curUserId() {
    return (window.__KIT_USER__ && window.__KIT_USER__.id) || "owner";
  }
  function initCollaboratorShare() {
    refreshCollabUiState(function (me) {
      var role = (me && me.role) || "owner";
      if (role !== "owner") return;
      var tools = document.querySelector("header.top .tools");
      if (!tools || document.getElementById("kit-users-btn")) return;
      var SVG_USR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/></svg>';
      var SVG_LNK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
      var SVG_TERM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
      var SVG_DEL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';
      var SVG_X2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
      var uBtn = document.createElement("button");
      uBtn.id = "kit-users-btn"; uBtn.type = "button"; uBtn.className = "btn small kit-icon-btn";
      uBtn.title = "Share board & manage collaborators"; uBtn.setAttribute("aria-label", uBtn.title);
      uBtn.innerHTML = SVG_USR;
      var docsBtn = document.getElementById("kit-docs-btn");
      if (docsBtn && docsBtn.parentNode === tools) tools.insertBefore(uBtn, docsBtn.nextSibling);
      else tools.appendChild(uBtn);

      uBtn.addEventListener("click", function () {
        var mod = document.getElementById("kit-users-modal");
        if (mod) { mod.remove(); return; }
        mod = document.createElement("section"); mod.id = "kit-users-modal"; mod.className = "kit-tl-win";
        mod.setAttribute("role", "dialog"); mod.setAttribute("aria-label", "Share and collaborators");
        var hdr = document.createElement("div"); hdr.className = "kit-tl-hdr";
        var bT = document.createElement("b"); bT.textContent = "Share & Collaborators";
        var cls = document.createElement("button"); cls.type = "button"; cls.className = "btn small kit-icon-btn";
        cls.title = "Close"; cls.setAttribute("aria-label", "Close"); cls.innerHTML = SVG_X2;
        cls.addEventListener("click", function () { mod.remove(); });
        hdr.appendChild(bT); hdr.appendChild(cls); mod.appendChild(hdr);

        var body = document.createElement("div"); body.className = "kit-tl-body"; body.style.gap = "12px";
        var note = document.createElement("div"); note.className = "note";
        note.textContent = "Mint a scoped link for a human collaborator or their AI (works over Tailscale Funnel without GitHub repo access).";
        body.appendChild(note);

        var form = document.createElement("form");
        form.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;align-items:center;";
        var nmIn = document.createElement("input"); nmIn.type = "text"; nmIn.placeholder = "Collaborator name (e.g. alex)"; nmIn.required = true;
        nmIn.style.cssText = "flex:1 1 150px;font:inherit;font-size:.88rem;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);";
        var roleSel = document.createElement("select");
        roleSel.style.cssText = "font:inherit;font-size:.86rem;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);";
        roleSel.innerHTML = '<option value="member">Member (post & comment)</option><option value="viewer">Viewer (read-only)</option><option value="owner">Owner (full admin)</option>';
        var addB = document.createElement("button"); addB.type = "submit"; addB.className = "btn small ember"; addB.textContent = "Create link";
        form.appendChild(nmIn); form.appendChild(roleSel); form.appendChild(addB);
        body.appendChild(form);

        var statusEl = document.createElement("div"); statusEl.className = "note"; statusEl.style.color = "var(--ember)";
        body.appendChild(statusEl);

        var listBox = document.createElement("div"); listBox.style.cssText = "display:flex;flex-direction:column;gap:8px;";
        body.appendChild(listBox);

        var shHdr = document.createElement("div"); shHdr.style.cssText = "font-size:.76rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-top:8px;padding-top:10px;border-top:1px solid var(--line);";
        shHdr.textContent = "Active AI Share Links (auto-expire in 24h)";
        body.appendChild(shHdr);
        var sharesBox = document.createElement("div"); sharesBox.style.cssText = "display:flex;flex-direction:column;gap:6px;";
        body.appendChild(sharesBox);

        function loadShares() {
          api("GET", "/_api/shares").then(function (res) {
            sharesBox.textContent = "";
            var shs = ((res && res.shares) || []).filter(function (s) { return !s.revoked; });
            if (!shs.length) { sharesBox.innerHTML = '<div class="note">No active AI share links.</div>'; return; }
            shs.forEach(function (s) {
              var row = document.createElement("div");
              row.style.cssText = "display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:10px;background:var(--sunk);border:1px solid var(--line);";
              var info = document.createElement("div"); info.style.cssText = "flex:1 1 0;min-width:0;";
              var t1 = document.createElement("div"); t1.style.cssText = "font-weight:700;font-size:.84rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
              t1.textContent = s.post_id;
              var t2 = document.createElement("div"); t2.className = "note"; t2.style.fontSize = ".72rem";
              t2.textContent = (s.submissions || 0) + "/" + (s.max_submissions || 5) + " replies · expires " + String(s.expires_at || "").slice(0, 16).replace("T", " ");
              info.appendChild(t1); info.appendChild(t2);
              var bCopy = document.createElement("button"); bCopy.type = "button"; bCopy.className = "btn small kit-icon-btn";
              bCopy.title = "Copy share link"; bCopy.innerHTML = SVG_LNK;
              bCopy.addEventListener("click", function () { copyText(location.origin + s.path, "Copied AI share link for " + s.post_id); });
              var bRev = document.createElement("button"); bRev.type = "button"; bRev.className = "btn small kit-icon-btn";
              bRev.title = "Revoke share link"; bRev.innerHTML = SVG_DEL;
              bRev.addEventListener("click", function () {
                api("DELETE", "/_api/shares/" + encodeURIComponent(s.token)).then(function () {
                  statusEl.textContent = "Revoked share link for " + s.post_id;
                  loadShares();
                });
              });
              row.appendChild(info); row.appendChild(bCopy); row.appendChild(bRev);
              sharesBox.appendChild(row);
            });
          }, function () {});
        }

        function copyText(txt, msg) {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).then(function () { statusEl.textContent = msg; }, function () { window.prompt(msg, txt); });
          } else {
            window.prompt(msg, txt);
          }
        }
        function loadUsers() {
          api("GET", "/_api/users").then(function (res) {
            listBox.textContent = "";
            var us = (res && res.users) || [];
            if (!us.length) { listBox.innerHTML = '<div class="note">No collaborators yet. Create one above to generate a link.</div>'; return; }
            us.forEach(function (u) {
              var row = document.createElement("div");
              row.style.cssText = "display:flex;align-items:center;gap:8px;padding:9px 11px;border-radius:10px;background:var(--sunk);border:1px solid var(--line);";
              var info = document.createElement("div"); info.style.cssText = "flex:1 1 0;min-width:0;";
              var nEl = document.createElement("div"); nEl.style.cssText = "font-weight:700;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
              nEl.textContent = u.name + " ";
              var rBadge = document.createElement("span"); rBadge.className = "badge"; rBadge.textContent = u.role;
              nEl.appendChild(rBadge);
              if (u.repo) {
                var bBadge = document.createElement("span"); bBadge.className = "badge"; bBadge.style.marginLeft = "4px"; bBadge.textContent = u.repo;
                nEl.appendChild(bBadge);
              }
              var idEl = document.createElement("div"); idEl.className = "note"; idEl.style.fontFamily = "var(--mono)"; idEl.style.fontSize = ".73rem";
              idEl.textContent = "@" + u.user_id;
              info.appendChild(nEl); info.appendChild(idEl);

              var linkUrl = location.origin + "/?token=" + u.token + (u.repo ? ("&repo=" + encodeURIComponent(u.repo)) : "");
              var aiInviteUrl = location.origin + "/_invite/" + u.token + ".md";
              var bLnk = document.createElement("button"); bLnk.type = "button"; bLnk.className = "btn small kit-icon-btn";
              bLnk.title = "Copy browser invite link"; bLnk.setAttribute("aria-label", bLnk.title); bLnk.innerHTML = SVG_LNK;
              bLnk.addEventListener("click", function () { copyText(linkUrl, "Copied browser invite link for " + u.name); });

              var bAi = document.createElement("button"); bAi.type = "button"; bAi.className = "btn small kit-icon-btn";
              bAi.title = "Copy 1-link AI bootstrap URL"; bAi.setAttribute("aria-label", bAi.title); bAi.innerHTML = SVG_TERM;
              bAi.addEventListener("click", function () { copyText(aiInviteUrl, "Copied 1-link AI bootstrap URL for " + u.name); });

              var bRev = document.createElement("button"); bRev.type = "button"; bRev.className = "btn small kit-icon-btn";
              bRev.title = "Revoke access"; bRev.setAttribute("aria-label", bRev.title); bRev.innerHTML = SVG_DEL;
              bRev.addEventListener("click", function () {
                api("DELETE", "/_api/users/" + encodeURIComponent(u.user_id)).then(function () {
                  statusEl.textContent = "Revoked " + u.name;
                  loadUsers();
                  refreshCollabUiState();
                });
              });
              row.appendChild(info); row.appendChild(bLnk); row.appendChild(bAi); row.appendChild(bRev);
              listBox.appendChild(row);
            });
          }, function () {});
        }
        form.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var nm = nmIn.value.trim();
          if (!nm) return;
          addB.disabled = true;
          var activeRepo = (window.__KIT_ACTIVE_REPO__ && window.__KIT_ACTIVE_REPO__ !== "all") ? window.__KIT_ACTIVE_REPO__ : "";
          api("POST", "/_api/users", { name: nm, role: roleSel.value, repo: activeRepo }).then(function (created) {
            addB.disabled = false; nmIn.value = "";
            if (created && created.link) copyText(created.link, "Created & copied link for " + created.name + " (" + created.role + ")");
            loadUsers();
            refreshCollabUiState();
          }, function () { addB.disabled = false; statusEl.textContent = "Could not create collaborator."; });
        });
        mod.appendChild(body);
        document.body.appendChild(mod);
        makeWindow(mod, hdr, { minW: 340, minH: 260, winKey: "users" });
        placeWindowLeftOfOthers(mod, 520, 520, "users", "users", 340, 260);
        loadUsers();
        loadShares();
      });
    }, function () {});
  }

  if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    var _kitMoTimer = null;
    var _kitMo = new MutationObserver(function () {
      if (_kitMoTimer) return;
      _kitMoTimer = setTimeout(function () {
        _kitMoTimer = null;
        enhancePostSheet();
        if (document.body) linkRepoDocs(document.body);
      }, 50);
    });
    var _attachMo = function () {
      var initialHash = location.hash || "";
      initRepoDocBrowser();
      initCollaboratorShare();
      loadSharedWindowGeoms();
      var target = document.body || document.documentElement;
      if (target) {
        _kitMo.observe(target, { attributes: true, childList: true, subtree: true, attributeFilter: ["hidden"] });
        enhancePostSheet();
        linkRepoDocs(target);
      }
      setTimeout(function () { restoreWorkspaceFromHash(initialHash); }, 30);
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
        if base in ("owner", "all", "system"):
            base = base + "-" + secrets.token_hex(2)
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


def _repo_file_kind(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".md":
        return "md"
    if ext in REPO_IMAGE_EXTS:
        return "img"
    if ext in REPO_HTML_EXTS:
        return "html"
    return ""


def _classify_repo_doc(rel):
    kind = _repo_file_kind(rel)
    if kind == "img":
        return "images"
    if kind == "html":
        return "html"
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
    if kind == "img":
        if st.st_size > MAX_BLOB_BYTES:
            return None, "too_large"
        return {
            "path": norm,
            "kind": "img",
            "url": "/_api/repo-asset?path=" + urllib.parse.quote(norm, safe="/") + ("&repo=" + urllib.parse.quote(repo_id) if repo_id else ""),
            "size": st.st_size,
            "mtime": mtime_iso,
            "headings": [],
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


class BoardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


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
        "img-src 'self' data: blob: https: /_blob/ /_api/repo-asset; "
        "media-src 'self' /_blob/; "
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
        cmts = [c for c in tstore.list_docs("comments")["docs"] if (c["data"] or {}).get("post_id") == sh["post_id"]]
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
                    if rp.lower().endswith((".html", ".htm")):
                        html_dirs.add(os.path.dirname(rp))

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
        cmts = [c for c in tstore.list_docs("comments")["docs"] if (c["data"] or {}).get("post_id") == sh["post_id"]]
        cmts.sort(key=lambda c: str((c["data"] or {}).get("created_at") or ""))
        blob_ids, repo_paths, _ = _share_allowed_assets(sh)
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

        def _scoped_user_repo(self):
            u = getattr(self, "_auth_user", None)
            if u and u.get("role") != "owner":
                ur = str(u.get("repo") or "").strip()
                if ur and ur in stores_map:
                    return ur
            return ""

        def _is_all_repos(self, parsed):
            if self._scoped_user_repo():
                return False
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
            scoped = self._scoped_user_repo()
            if scoped:
                return scoped
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

        def _cur_uid(self):
            u = getattr(self, "_auth_user", None) or {}
            return str(u.get("id") or "owner")

        def _ensure_user_orch(self, st, uid):
            oid = "_orchestrator_" + uid
            if not st.get_doc("posts", oid).get("exists"):
                st.set_doc("posts", oid, {
                    "title": "Ask the orchestrator (@" + uid + ")",
                    "column": "info",
                    "part": "orchestrator",
                    "priority": 3,
                    "owner_user": uid,
                    "created_at": utc_now(),
                    "body": "Private orchestrator channel for @" + uid + ".",
                })
            return st.get_doc("posts", oid)

        def _scope_list_docs(self, st, coll, res):
            uid = self._cur_uid()
            if uid == "owner":
                if coll == "posts":
                    res["docs"] = [d for d in (res.get("docs") or []) if not str(d.get("id") or "").startswith("_orchestrator_")]
                elif coll == "comments":
                    res["docs"] = [d for d in (res.get("docs") or []) if not str((d.get("data") or {}).get("post_id") or "").startswith("_orchestrator_")]
                return res
            my_oid = "_orchestrator_" + uid
            if coll == "posts":
                self._ensure_user_orch(st, uid)
                out = []
                for d in (res.get("docs") or []):
                    did = str(d.get("id") or "")
                    if did == "_orchestrator":
                        continue
                    if did == my_oid:
                        out.append({**d, "id": "_orchestrator"})
                    elif did.startswith("_orchestrator_"):
                        continue
                    else:
                        out.append(d)
                res["docs"] = out
            elif coll == "comments":
                out = []
                for d in (res.get("docs") or []):
                    pid = str((d.get("data") or {}).get("post_id") or "")
                    if pid == "_orchestrator":
                        continue
                    if pid == my_oid:
                        dd = dict(d.get("data") or {})
                        dd["post_id"] = "_orchestrator"
                        out.append({**d, "data": dd})
                    elif pid.startswith("_orchestrator_"):
                        continue
                    else:
                        out.append(d)
                res["docs"] = out
            return res

        def _scope_write_target(self, coll, doc_id, data):
            uid = self._cur_uid()
            if uid == "owner":
                return doc_id, data, None
            my_oid = "_orchestrator_" + uid
            if coll == "posts":
                if doc_id == "_orchestrator":
                    return my_oid, data, None
                if doc_id.startswith("_orchestrator_") and doc_id != my_oid:
                    return doc_id, data, "forbidden"
            elif coll == "comments" and isinstance(data, dict):
                nd = dict(data)
                pid = str(nd.get("post_id") or "")
                if pid == "_orchestrator":
                    nd["post_id"] = my_oid
                elif pid.startswith("_orchestrator_") and pid != my_oid:
                    return doc_id, data, "forbidden"
                if nd.get("from") in (None, "", "owner"):
                    nd["from"] = uid
                return doc_id, nd, None
            return doc_id, data, None

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
            self._auth_user = None
            return False, False

        def _require_auth(self, parsed, min_role="viewer"):
            ok, set_cookie = self._check_auth(parsed)
            if ok:
                ranks = {"viewer": 1, "member": 2, "owner": 3}
                u = getattr(self, "_auth_user", None) or {"role": "owner"}
                if ranks.get(u.get("role", "viewer"), 1) < ranks.get(min_role, 1):
                    self._send_json(403, {"error": "forbidden", "required_role": min_role})
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
                    "- **Shared project posts:** read/list all posts with `python3 /tmp/board_cli.py list posts`.",
                    "- **Attribution & AI metadata:** set `\"from\": \"%s\"`, `\"working_by\": \"%s\"`, and `\"agent_meta\": {\"harness\": \"<cli>\", \"model\": \"<model>\", \"effort\": \"<low|medium|high>\"}` on posts/comments." % (uid, uid),
                    "- **Submit scripts, improved docs & HTML (no GitHub write access needed):** `POST $KIT_BOARD_URL/_api/posts/<post_id>/artifacts` with JSON `{\"path\": \"docs/foo.md\", \"base_path\": \"docs/foo.md\", \"summary\": \"1-line summary\", \"content\": \"...\", \"agent_meta\": {\"harness\": \"...\", \"model\": \"...\", \"effort\": \"...\"}}`. Your deliverables are stored safely in the board, diffed against `base_path`, previewable in the Document/HTML Viewer, and queryable in compact manifests (`GET /_api/posts/<post_id>/artifacts`).",
                    "- **Permissions (`%s`):** %s" % (role, "read-only" if role == "viewer" else ("read + post/comment/upload blobs" if role == "member" else "full admin")),
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
                    if rrel not in allowed_repo and rdir not in html_dirs:
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

            if path.startswith("/_api/posts/") and path.endswith("/artifacts"):
                pid_a = urllib.parse.unquote(path[len("/_api/posts/"):-len("/artifacts")]).strip("/")
                tstore, _ = self._store_for_doc(parsed, "posts", pid_a)
                self._send_json(200, {"post_id": pid_a, "artifacts": tstore.list_artifacts(post_id=pid_a)})
                return

            if path.startswith("/_api/artifacts/"):
                aid_req = urllib.parse.unquote(path[len("/_api/artifacts/"):]).strip("/")
                art = self._store(parsed).get_artifact(aid_req)
                if not art:
                    for st_a in stores_map.values():
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
                    only = [dict(rm, primary=True) for rm in repo_meta if rm.get("id") == scoped]
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
                rid = (qs.get("repo") or [primary_repo_id])[0]
                if rid == "all" or rid not in roots_map:
                    rid = primary_repo_id
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
                rroot = roots_map.get(rid_req) or root_dir
                adata, actype, aerr = read_repo_asset(rroot, rrel)
                if aerr:
                    code = 404 if aerr == "not_found" else (413 if aerr == "too_large" else 400)
                    self._send_json(code, {"error": aerr})
                    return
                self.send_response(200)
                self.send_header("Content-Type", actype)
                self.send_header("X-Content-Type-Options", "nosniff")
                if token and (qs.get("token") or [""])[0]:
                    self.send_header("Set-Cookie", "kit_token=%s; Path=/; HttpOnly; SameSite=Lax" % token)
                self.send_header("Content-Length", str(len(adata)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(adata)
                return

            if path == "/_api/repo-asset":
                rpath = (qs.get("path") or [""])[0]
                adata, actype, aerr = read_repo_asset(self._repo_root(parsed), rpath)
                if aerr:
                    code = 404 if aerr == "not_found" else (413 if aerr == "too_large" else 400)
                    self._send_json(code, {"error": aerr})
                    return
                self.send_response(200)
                self.send_header("Content-Type", actype)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(adata)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(adata)
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
                    res_list = cur_store.list_docs(rest[0], where=where, order_by=order_by, direction=direction, limit=limit, cursor=cursor)
                    self._send_json(200, self._scope_list_docs(cur_store, rest[0], res_list))
                    return
                if len(rest) == 2:
                    tstore, _ = self._store_for_doc(parsed, rest[0], rest[1])
                    did_req = rest[1]
                    uid_cur = self._cur_uid()
                    if rest[0] == "posts" and uid_cur != "owner":
                        if did_req == "_orchestrator":
                            doc_o = self._ensure_user_orch(tstore, uid_cur)
                            self._send_json(200, {**doc_o, "id": "_orchestrator"})
                            return
                        if did_req.startswith("_orchestrator_") and did_req != ("_orchestrator_" + uid_cur):
                            self._send_json(403, {"error": "forbidden"})
                            return
                    self._send_json(200, tstore.get_doc(rest[0], did_req))
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
            tstore, _ = self._store_for_doc(parsed, coll, doc_id)
            if coll == "comments" and self._is_all_repos(parsed) and (body.get("data") or {}).get("post_id"):
                tstore, _ = self._store_for_doc(parsed, "posts", str(body["data"]["post_id"]))
            doc_id, put_data, sc_err = self._scope_write_target(coll, doc_id, body.get("data", {}))
            if sc_err:
                self._send_json(403, {"error": sc_err})
                return
            res, err = tstore.set_doc(coll, doc_id, put_data, if_version=body.get("if_version"))
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
            tstore, _ = self._store_for_doc(parsed, coll, doc_id)
            doc_id, patch_data, sc_err = self._scope_write_target(coll, doc_id, body.get("data", {}))
            if sc_err:
                self._send_json(403, {"error": sc_err})
                return
            res, err = tstore.update_doc(coll, doc_id, patch_data, if_version=body.get("if_version"))
            if err is not None:
                self._send_json(409, {"error": "version_mismatch", "current_version": err})
                return
            self._send_json(200, res)

        def do_DELETE(self):
            parsed = urllib.parse.urlsplit(self.path)
            if self._require_auth(parsed, min_role="owner") is None:
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
                if req_repo and (req_repo == "all" or req_repo not in stores_map):
                    req_repo = ""
                created = store.create_user(body.get("name"), role=body.get("role", "member"), repo=req_repo)
                if not created:
                    self._send_json(400, {"error": "name_required"})
                    return
                proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
                host_hdr = self.headers.get("Host") or ""
                origin = ("%s://%s" % (proto, host_hdr)) if host_hdr else ""
                is_np = bool(req_repo and req_repo != primary_repo_id)
                created["link"] = origin + "/?token=" + created["token"] + (("&repo=" + urllib.parse.quote(req_repo)) if is_np else "")
                created["ai_env"] = 'export KIT_BOARD_URL="%s" KIT_BOARD_TOKEN="%s"%s' % (
                    origin, created["token"], (' KIT_BOARD_REPO="%s"' % req_repo) if is_np else ""
                )
                self._send_json(200, created)
                return

            if path == "/_api/shares":
                pid = str(body.get("post_id") or "").strip()
                target_repo = str(body.get("repo") or "").strip()
                if target_repo and target_repo in stores_map:
                    tstore = stores_map[target_repo]
                else:
                    tstore, target_repo = self._store_for_doc(parsed, "posts", pid)
                if pid and not tstore.get_doc("posts", pid).get("exists"):
                    for rid_k, st_k in stores_map.items():
                        if st_k.get_doc("posts", pid).get("exists"):
                            tstore, target_repo = st_k, rid_k
                            break
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
    srv = BoardHTTPServer((host, port), make_handler(root_dir, store, token=token, notify_url=notify_url, embed_mode=embed_mode, extra_stores=extra_stores))
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
