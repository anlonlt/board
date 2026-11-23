(function () {
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
        var docs = res.docs || [];
        var sig = (window.__KIT_ACTIVE_REPO__ || "") + "|" + JSON.stringify(docs);
        if (s._lastSig === sig) return;
        s._lastSig = sig;
        s.cb(makeSnap(docs));
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
  function activeMediaEl() {
    if (typeof document === "undefined") return null;
    var els = document.querySelectorAll("audio,video");
    for (var i = 0; i < els.length; i++) {
      if (!els[i].paused && !els[i].ended) return els[i];
    }
    return null;
  }
  function triggerUiReload() {
    if (typeof window === "undefined" || !window.location) return;
    var playing = activeMediaEl();
    if (isTypingNow() || playing) {
      if (!reloadPending) {
        reloadPending = true;
        var ae = playing || document.activeElement;
        var evName = playing ? "pause" : "blur";
        if (ae && ae.addEventListener) {
          ae.addEventListener(evName, function onDone() {
            ae.removeEventListener(evName, onDone);
            if (reloadPending && !isTypingNow() && !activeMediaEl()) { reloadPending = false; window.location.reload(); }
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
      var dlg = document.getElementById("kit-dlg");
      if (dlg) { dlg.remove(); return; }
      var back = document.getElementById("back");
      if (back && back.offsetParent !== null) back.click();
    }
  });

  /* In-page confirm() and prompt() dialogs (works in sandboxed iframes like claude.ai Artifacts and Arena
     preview frames where native modal dialogs are blocked and window.confirm() returns false immediately). */
  if (typeof window !== "undefined" && !window.__KIT_DIALOG_INSTALLED__) {
    window.__KIT_DIALOG_INSTALLED__ = true;
    var _kitLastClicked = null, _kitAllowOnce = false, _kitReclicking = false;
    window.addEventListener("click", function (ev) {
      if (_kitReclicking || !ev.target || !ev.target.closest) return;
      if (ev.target.closest("#kit-dlg")) return;
      _kitLastClicked = ev.target.closest("button, a, [role=button], [role=link], input, label") || ev.target;
    }, true);
    function showKitDialog(message, withText, okLabel, onOk) {
      var old = document.getElementById("kit-dlg"); if (old) old.remove();
      var back = document.createElement("div"); back.id = "kit-dlg";
      back.style.cssText = "position:fixed;inset:0;z-index:95;background:rgba(10,14,24,.48);display:flex;align-items:center;justify-content:center;padding:16px;overscroll-behavior:contain;";
      back.addEventListener("click", function (ev) { if (ev.target === back) back.remove(); });
      var box = document.createElement("div"); box.setAttribute("role", "alertdialog"); box.setAttribute("aria-modal", "true");
      box.style.cssText = "max-width:520px;width:100%;max-height:85vh;overflow:auto;overscroll-behavior:contain;background:var(--surface,#fff);color:var(--ink,#111);border:1px solid var(--line,#ccc);border-radius:14px;padding:16px 18px;box-shadow:0 18px 50px rgba(0,0,0,.35);display:flex;flex-direction:column;gap:12px;";
      var p = document.createElement("div"); p.style.cssText = "white-space:pre-wrap;word-break:break-word;line-height:1.5;"; p.textContent = message; box.appendChild(p);
      if (withText !== null && withText !== undefined) {
        var ta = document.createElement("textarea"); ta.readOnly = true; ta.value = String(withText);
        ta.style.cssText = "width:100%;min-height:140px;font:13px/1.4 var(--mono,monospace);background:var(--sunk,#f3f3f3);color:var(--ink,#111);border:1px solid var(--line,#ccc);border-radius:8px;padding:8px 10px;overscroll-behavior:contain;";
        box.appendChild(ta);
        setTimeout(function () { ta.focus(); ta.select(); }, 0);
      }
      var row = document.createElement("div"); row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";
      var no = document.createElement("button"); no.type = "button"; no.className = "btn small"; no.textContent = onOk ? "Cancel" : "Close";
      no.onclick = function () { back.remove(); };
      row.appendChild(no);
      if (onOk) {
        var ok = document.createElement("button"); ok.type = "button"; ok.className = "btn small ember"; ok.textContent = okLabel || "OK";
        ok.onclick = function () { back.remove(); onOk(); };
        row.appendChild(ok);
        setTimeout(function () { ok.focus(); }, 0);
      }
      box.appendChild(row); back.appendChild(box);
      document.body.appendChild(back);
    }
    window.confirm = function (message) {
      if (_kitAllowOnce) { _kitAllowOnce = false; return true; }
      var target = _kitLastClicked;
      if (!target || !document.contains(target)) return false;
      showKitDialog(String(message || ""), null, "OK", function () {
        _kitAllowOnce = true; _kitReclicking = true;
        try { target.click(); } finally { _kitAllowOnce = false; _kitReclicking = false; }
      });
      return false;
    };
    window.prompt = function (message, def) {
      showKitDialog(String(message || ""), def == null ? "" : String(def), null, null);
      return null;
    };
  }

  function enhancePostSheet() {
    var sheet = document.getElementById("sheet");
    if (!sheet || sheet.hidden) return;
    var postId = "";
    try { postId = sessionStorage.getItem("board-open") || ""; } catch (e) {}
    /* Upgrade Ask the Orchestrator popup to an OS-style window + add Pop-out window icon button on posts */
    if (sheet.classList.contains("orch-popup")) {
      var oldB = document.getElementById("kit-post-ext-badges"); if (oldB) oldB.remove();
      var oldA = document.getElementById("kit-post-artifacts"); if (oldA) oldA.remove();
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
    renderPostExtBadges(sheet, postId);
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
      if (winEl.dataset.kitNoResize === "1") return;
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
      if (window.innerWidth < 680 || winEl.dataset.kitNoResize === "1") return "";
      var ep = winEl.classList.contains("video-win") ? 16 : edgePx;
      var r = winEl.getBoundingClientRect();
      var x = e.clientX - r.left, y = e.clientY - r.top;
      var s = "";
      if (y <= ep) s += "n"; else if (y >= r.height - ep) s += "s";
      if (x <= ep) s += "w"; else if (x >= r.width - ep) s += "e";
      return s;
    }
    var curMap = { n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize" };
    winEl.addEventListener("mousemove", function (e) {
      if (winEl.dataset.kitDragging) return;
      var ed = hitEdges(e);
      winEl.style.cursor = ed ? curMap[ed] : "";
      if (ed) {
        winEl.style.setProperty("--kit-rz-cur", curMap[ed]);
        winEl.classList.add("kit-edge-hover");
      } else {
        winEl.classList.remove("kit-edge-hover");
      }
    });
    winEl.addEventListener("mouseleave", function () {
      if (!winEl.dataset.kitDragging) winEl.classList.remove("kit-edge-hover");
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
        winEl.dataset.kitJustResized = String(Date.now());
        winEl.classList.remove("kit-edge-hover");
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
          lastSnap = winEl.dataset.kitNoResize === "1" ? null : snapRectFor(ev.clientX, ev.clientY);
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
        md.dataset.seenPost = postId; md.dataset.seenK = "body";   /* CL21: read in a window counts (the page marks it) */
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
            item.dataset.seenPost = postId; item.dataset.seenK = c.id;
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
        ".kit-docs-view.video-mode{overflow:hidden !important;height:100%;min-height:0;background:#000;}" +
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
        ".kit-rv-chip{font:inherit;font-size:.72rem;line-height:1;padding:3px 8px 3px 6px;border-radius:999px;cursor:pointer;flex:none;display:inline-flex;align-items:center;gap:5px;" +
        "border:1px solid color-mix(in srgb,var(--rv-c) 55%,transparent);background:color-mix(in srgb,var(--rv-c) 12%,var(--surface));color:var(--ink);}" +
        ".kit-rv-chip::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--rv-c);}" +
        ".kit-rv-chip.rv-done{--rv-c:var(--done);}.kit-rv-chip.rv-you{--rv-c:var(--todo);}" +
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
        /* CL10: the bar bleeds 16px each side (margin-inline:-16px); a 100% cap cut 32px off it and hid the bin past an invisible scroll at 390px */
        "@media (max-width:700px){" +
          ".kit-tl-win{inset:8px !important;left:8px !important;top:8px !important;width:calc(100vw - 16px) !important;height:calc(100dvh - 16px) !important;border-radius:14px;}" +
          ".sheet .bar{gap:4px !important;flex-wrap:nowrap !important;max-width:calc(100% + 32px);box-sizing:border-box;overflow-x:auto;scrollbar-width:none;}" +
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
      var activeHtmlUrl = "", activeHtmlSource = "", activeImgUrl = "", activePdfUrl = "", activePdfPage = 1, activeRepoOverride = undefined;
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
        var isAudio = activeKind === "audio" && activeViewMode === "view";
        var isVideo = activeKind === "video" && activeViewMode === "view";
        var isGalOrCmp = activeViewMode === "gallery" || activeViewMode === "compare";
        stageEl.classList.toggle("iframe-active", isHtml && htmlMode !== "code");
        modal.classList.toggle("photo-mode", isImg && !drawActive);
        modal.classList.toggle("audio-mode", isAudio);
        modal.classList.toggle("video-win", isVideo);
        modal.dataset.kitNoResize = isAudio ? "1" : "";
        if (window.innerWidth >= 720) {
          if (isAudio) {
            if (!modal.dataset.kitPreAudioH) {
              var rA = modal.getBoundingClientRect();
              modal.dataset.kitPreAudioW = modal.style.width || (Math.round(rA.width) + "px");
              modal.dataset.kitPreAudioH = modal.style.height || (Math.round(rA.height) + "px");
            }
            modal.style.width = Math.min(520, window.innerWidth - 24) + "px";
            modal.style.height = "252px";
            modal.style.cursor = "";
          } else if (modal.dataset.kitPreAudioH) {
            modal.style.width = modal.dataset.kitPreAudioW || "";
            modal.style.height = modal.dataset.kitPreAudioH || "";
            delete modal.dataset.kitPreAudioW;
            delete modal.dataset.kitPreAudioH;
          }
        }
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
        } else if (isPrimary && activeKind !== "img" && activeKind !== "video" && activeKind !== "pdf") {
          _kitDocsState.zoom = z;
          try { localStorage.setItem("kit_docs_zoom", String(z)); } catch (e) {}
        }
        var scaleVal = z / 100;
        if (activeKind === "pdf") {
          modal.style.setProperty("--docs-scale", "1");
          var pdfWrap = viewEl.querySelector(".kit-pdf-wrap");
          if (pdfWrap && typeof pdfWrap._kitSetZoom === "function") {
            var zoomedW = pdfWrap._kitSetZoom(scaleVal);
            if (zoomedW > 0 && window.innerWidth >= 720) {
              var targetW = Math.max(420, Math.min(window.innerWidth - 24, Math.round(zoomedW + 44)));
              modal.style.width = targetW + "px";
              var curL = parseFloat(modal.style.left || "0");
              if (curL + targetW > window.innerWidth - 8) {
                modal.style.left = Math.max(8, window.innerWidth - targetW - 8) + "px";
              }
            }
          }
          if (!skipUrl && isPrimary) syncUrlWorkspaceState();
          return;
        }
        modal.style.setProperty("--docs-scale", String(scaleVal));
        var imgWrap = viewEl.querySelector(".kit-docs-img-wrap");
        if (imgWrap) {
          imgWrap.style.transform = scaleVal === 1 ? "" : ("scale(" + scaleVal + ")");
        }
        var vidEl = viewEl.querySelector(".kit-vid.full video");
        if (vidEl) {
          vidEl.style.transform = scaleVal === 1 ? "" : ("scale(" + scaleVal + ")");
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
        if (activeKind === "pdf" && hObj.page) {
          activePdfPage = hObj.page;
          var pWrapJ = viewEl.querySelector(".kit-pdf-wrap");
          if (pWrapJ && typeof pWrapJ._kitGoPage === "function") {
            pWrapJ._kitGoPage(hObj.page);
          } else {
            var pIfrJ = viewEl.querySelector("iframe.kit-pdf-iframe");
            if (pIfrJ && activePdfUrl) {
              pIfrJ.src = activePdfUrl.split("#")[0] + "#page=" + hObj.page + "&toolbar=0&navpanes=0";
            }
          }
          var pInJ = modal.querySelector(".kit-pdf-page-in");
          if (pInJ) pInJ.value = String(hObj.page);
        }
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
      if (activeKind === "pdf") {
        var pCur = activePdfPage || 1;
        var pH = selectedHeadingId ? findHeadingById(selectedHeadingId) : null;
        var pSel = "";
        try {
          var selObj = window.getSelection ? window.getSelection() : null;
          if (selObj && selObj.toString().trim() && viewEl.contains(selObj.anchorNode)) {
            pSel = selObj.toString().trim().slice(0, 400);
          }
        } catch (e) {}
        var pPre = "Re `" + activePath + "` p. " + pCur + (pH && pH.text && pH.text.indexOf("Page ") !== 0 ? " (" + pH.text.replace(/\s*\(p\.\s*\d+\)\s*$/, "") + ")" : "") + ":\n";
        if (pSel) pPre += "> " + pSel.replace(/\n+/g, "\n> ") + "\n\n";
        if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew(pPre); bringOrchAboveViewer(); }
        return;
      }
      if (activeKind === "audio") {
        var aEl = viewEl.querySelector("audio");
        if (aEl && !aEl.paused) try { aEl.pause(); } catch (e) {}
        var aTs = aEl ? fmtMediaTime(aEl.currentTime || 0) : "00:00";
        if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew("Re `" + activePath + "` @ " + aTs + ":\n"); bringOrchAboveViewer(); }
        return;
      }
      if (activeKind === "video") {
        var vEl = viewEl.querySelector("video");
        if (vEl && !vEl.paused) try { vEl.pause(); } catch (e) {}
        var vTs = vEl ? fmtMediaTime(vEl.currentTime || 0) : "00:00";
        cmtHdrBtn.disabled = true;
        captureVideoContactSheet(vEl).then(function (stripUrl) {
          cmtHdrBtn.disabled = false;
          var vPre = "Re `" + activePath + "` @ " + vTs + ":\n" + (stripUrl ? ("![Frames around " + vTs + "](" + stripUrl + ")\n\n") : "");
          if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew(vPre); bringOrchAboveViewer(); }
        });
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
      viewEl.classList.remove("video-mode");
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
      viewEl.classList.remove("video-mode");
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
        if (activeKind === "pdf" || activeKind === "audio" || activeKind === "video") {
          var mediaUrl = withKitToken(res.url || resolveAssetUrl(res.path, "", repoOverride));
          cmtHdrBtn.title = "Comment on " + shortName(res.path);
          cmtHdrBtn.setAttribute("aria-label", cmtHdrBtn.title);
          viewEl.classList.add("media-mode");
          viewEl.textContent = "";
          if (activeKind === "pdf") {
            activePdfUrl = mediaUrl;
            activePdfPage = 1;
            activeHeadings = res.headings || [];
            var dpPostId = ""; try { dpPostId = sessionStorage.getItem("board-open") || ""; } catch (e) {}
            var dpLike = makeMediaLikeBtn(res.path, dpPostId);
            var pdfViewer = buildPdfJsViewer(mediaUrl, {
              title: shortName(res.path),
              extraBtns: [dpLike],
              onPageChange: function (pg) { activePdfPage = pg; },
              onRegionCrop: function (info) {
                var pPre = "Re `" + activePath + "` p. " + (info.page || activePdfPage || 1) + ":\n";
                if (info.imgUrl) pPre += "![Selection on p. " + (info.page || 1) + "](" + info.imgUrl + ")\n";
                if (info.text) pPre += "> " + info.text.replace(/\n+/g, "\n> ") + "\n";
                pPre += "\n";
                if (typeof window._kitOpenOrchNew === "function") { window._kitOpenOrchNew(pPre); bringOrchAboveViewer(); }
              }
            });
            viewEl.appendChild(pdfViewer);
            applyLocalZoom(100, true);
          } else if (activeKind === "video") {
            viewEl.classList.add("video-mode");
            var vWrap = document.createElement("div");
            vWrap.style.cssText = "display:flex;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%;min-height:0;flex:1 1 0;overflow:hidden;background:#000;";
            var dvPostId = ""; try { dvPostId = sessionStorage.getItem("board-open") || ""; } catch (e) {}
            var dvLike = makeMediaLikeBtn(res.path, dvPostId);
            var dvCmt = document.createElement("button"); dvCmt.type = "button"; dvCmt.className = "kit-mbtn";
            dvCmt.title = "Comment on video"; dvCmt.setAttribute("aria-label", "Comment on video");
            dvCmt.innerHTML = SVG_CMT_M;
            dvCmt.addEventListener("click", function (ev) { ev.stopPropagation(); cmtHdrBtn.click(); });
            vWrap.appendChild(buildCustomVideoPlayer(mediaUrl, { fullHeight: true, extraBtns: [dvLike, dvCmt] }));
            viewEl.appendChild(vWrap);
            applyLocalZoom(100, true);
          } else {
            var aWrap = document.createElement("div");
            aWrap.style.cssText = "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;width:100%;height:100%;padding:28px;box-sizing:border-box;";
            var aIcon = document.createElement("div");
            aIcon.style.cssText = "width:56px;height:56px;border-radius:16px;background:var(--sunk);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;color:var(--ember);";
            aIcon.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
            var aName = document.createElement("div");
            aName.style.cssText = "font-weight:700;font-size:1rem;text-align:center;word-break:break-word;";
            aName.textContent = shortName(res.path);
            var aSub = document.createElement("div"); aSub.className = "note";
            aSub.textContent = activeDocMetaText;
            var daPostId = ""; try { daPostId = sessionStorage.getItem("board-open") || ""; } catch (e) {}
            var daLike = makeMediaLikeBtn(res.path, daPostId);
            var daCmt = document.createElement("button"); daCmt.type = "button"; daCmt.className = "kit-mbtn";
            daCmt.title = "Comment on audio"; daCmt.setAttribute("aria-label", "Comment on audio");
            daCmt.innerHTML = SVG_CMT_M;
            daCmt.addEventListener("click", function (ev) { ev.stopPropagation(); cmtHdrBtn.click(); });
            var audBox = buildCustomAudioPlayer(mediaUrl, { extraBtns: [daLike, daCmt] });
            audBox.style.width = "min(520px,100%)";
            aWrap.appendChild(aIcon); aWrap.appendChild(aName); aWrap.appendChild(aSub); aWrap.appendChild(audBox);
            viewEl.appendChild(aWrap);
          }
          renderOutline();
          updateHeaderModeButtons();
          return;
        }
        viewEl.classList.remove("media-mode");
      viewEl.classList.remove("video-mode");
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
    var edgePx = kindKey === "video" ? 14 : 8;
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
      if (ed) {
        card.style.setProperty("--kit-rz-cur", cmap[ed]);
        card.classList.add("kit-edge-hover");
      } else {
        card.classList.remove("kit-edge-hover");
      }
    });
    card.addEventListener("mouseleave", function () {
      if (!card.dataset.kitResizing) card.classList.remove("kit-edge-hover");
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
        card.dataset.kitJustResized = String(Date.now());
        card.classList.remove("kit-edge-hover");
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

  function ensureMediaPlayerCss() {
    if (document.getElementById("kit-media-css")) return;
    var st = document.createElement("style");
    st.id = "kit-media-css";
    st.textContent =
      ".kit-aud{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:12px;background:var(--sunk);border:1px solid var(--line);box-sizing:border-box;width:100%;user-select:none;}" +
      ".kit-mbtn{width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:0;flex:none;transition:background .15s,border-color .15s,color .15s;}" +
      ".kit-mbtn:hover{border-color:var(--ember);color:var(--ember);}" +
      ".kit-mbtn.playing{background:var(--ember);border-color:var(--ember);color:#fff;}" +
      ".kit-mbtn svg{width:14px;height:14px;}" +
      ".kit-mspd{height:26px;padding:0 6px;border-radius:6px;border:1px solid var(--line);background:var(--surface);color:var(--muted);font-family:var(--mono);font-size:.72rem;font-weight:700;cursor:pointer;flex:none;}" +
      ".kit-mspd:hover{color:var(--ink);border-color:var(--ember);}" +
      ".kit-mtime{font-family:var(--mono);font-size:.74rem;color:var(--muted);white-space:nowrap;flex:none;min-width:74px;text-align:center;}" +
      ".kit-mtrack{position:relative;flex:1 1 0;min-width:60px;height:20px;display:flex;align-items:center;cursor:pointer;touch-action:none;}" +
      ".kit-mbar{width:100%;height:5px;border-radius:999px;background:color-mix(in srgb,var(--line) 80%,transparent);overflow:hidden;position:relative;}" +
      ".kit-mfill{height:100%;width:0%;background:var(--ember);border-radius:999px;pointer-events:none;}" +
      ".kit-vid{position:relative;width:100%;height:100%;min-height:0;background:#000;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center;user-select:none;}" +
      ".kit-vid.full{flex:1 1 0;height:100%;min-height:0;border-radius:0;}" +
      ".kit-vid video{width:100%;height:100%;max-width:100%;max-height:460px;min-height:0;object-fit:contain;display:block;cursor:pointer;transform-origin:center center;}" +
      ".kit-vid.full video{max-height:100%;height:100%;width:100%;object-fit:contain;}" +
      ".kit-inline-embed-body.is-vid{overflow:hidden !important;display:flex;align-items:center;justify-content:center;background:#000;}" +
      ".kit-inline-embed-body.is-vid .kit-vid{width:100%;height:100%;max-height:100%;border-radius:0;}" +
      ".kit-inline-embed-body.is-vid .kit-vid video{max-height:100%;height:100%;width:100%;object-fit:contain;}" +
      ".kit-vid-bar{position:absolute;left:10px;right:10px;bottom:10px;z-index:6;display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:10px;background:rgba(12,17,28,.76);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.12);color:#eef2f7;transition:opacity .28s ease;}" +
      ".kit-vid.idle .kit-vid-bar{opacity:0;pointer-events:none;}" +
      ".kit-vid:hover .kit-vid-bar,.kit-vid:focus-within .kit-vid-bar{opacity:1;pointer-events:auto;}" +
      ".kit-vid-bar .kit-mbtn{background:transparent;border-color:rgba(255,255,255,.18);color:#eef2f7;width:28px;height:28px;}" +
      ".kit-vid-bar .kit-mbtn.playing{background:var(--ember);border-color:var(--ember);color:#fff;}" +
      ".kit-vid-bar .kit-mspd{background:transparent;border-color:rgba(255,255,255,.18);color:#cbd5e1;}" +
      ".kit-vid-bar .kit-mtime{color:#cbd5e1;}" +
      ".kit-vid-bar .kit-mbar{background:rgba(255,255,255,.22);}" +
      ".kit-msep{width:1px;height:16px;background:var(--line);flex:none;margin:0 1px;}" +
      ".kit-vid-bar .kit-msep{background:rgba(255,255,255,.22);}" +
      ".kit-mbtn.liked{color:var(--good) !important;border-color:var(--good) !important;}" +
      ".kit-mbtn.on{background:color-mix(in srgb,var(--ember) 18%,var(--surface));border-color:var(--ember);color:var(--ember);}" +
      ".kit-edge-hover,.kit-edge-hover *{cursor:var(--kit-rz-cur,nwse-resize) !important;}" +
      ".kit-pdf-wrap{display:flex;flex-direction:column;width:100%;height:100%;min-height:0;flex:1 1 auto;background:var(--sunk);position:relative;overflow:hidden;}" +
      ".kit-pdf-bar{display:flex;align-items:center;gap:6px;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--sunk);flex:none;font-size:.8rem;flex-wrap:wrap;}" +
      ".kit-pdf-bar .sp{flex:1 1 0;}" +
      ".kit-pdf-scroll{flex:1 1 0;min-height:0;overflow:auto;overscroll-behavior:contain;padding:12px 10px 22px;box-sizing:border-box;width:100%;}" +
      ".kit-pdf-pages{display:flex;flex-direction:column;align-items:center;gap:14px;transform-origin:top center;width:100%;}" +
      ".kit-pdf-page{position:relative;background:#fff;box-shadow:0 4px 18px rgba(0,0,0,.18);border-radius:4px;overflow:hidden;flex:none;line-height:0;margin:0 auto;}" +
      ".kit-pdf-page canvas{display:block;width:100%;height:100%;}" +
      ".kit-pdf-txt{position:absolute;top:0;left:0;overflow:hidden;line-height:1;text-size-adjust:none;-webkit-text-size-adjust:none;transform-origin:0 0;}" +
      ".kit-pdf-txt span{position:absolute;white-space:pre;color:transparent;cursor:text;transform-origin:0% 0%;user-select:text;-webkit-user-select:text;}" +
      ".kit-pdf-txt span::selection{background:rgba(232,137,74,.38);color:transparent;}" +
      ".kit-pdf-sel-layer{position:absolute;inset:0;z-index:4;cursor:crosshair;touch-action:none;display:none;}" +
      ".kit-pdf-wrap.crop-on .kit-pdf-sel-layer{display:block;}" +
      ".kit-pdf-wrap.crop-on .kit-pdf-txt{pointer-events:none;}" +
      ".kit-pdf-rect{position:absolute;border:2px solid var(--ember);background:rgba(232,137,74,.2);border-radius:3px;pointer-events:none;box-sizing:border-box;}" +
      "@media (max-width:700px){" +
        ".kit-inline-embed[data-kind='pdf'],.kit-inline-embed[data-kind='audio'],.kit-inline-embed[data-kind='video']{width:calc(100vw - 8px) !important;max-width:calc(100vw - 8px) !important;margin-left:50% !important;transform:translateX(-50%) !important;box-sizing:border-box !important;border-radius:10px;}" +
        ".kit-aud{gap:4px;padding:6px;border-radius:10px;justify-content:center;}" +
        ".kit-vid-bar{left:4px;right:4px;bottom:6px;gap:4px;padding:5px 6px;border-radius:9px;}" +
        ".kit-pdf-bar{gap:4px;padding:5px 6px;}" +
        ".kit-pdf-scroll{padding:8px 4px 18px;}" +
        ".kit-mbtn{width:26px;height:26px;border-radius:7px;}" +
        ".kit-mbtn svg{width:13px;height:13px;}" +
        ".kit-vid-bar .kit-mbtn{width:25px;height:25px;}" +
        ".kit-mspd{height:24px;padding:0 4px;font-size:.66rem;}" +
        ".kit-mtime{font-size:.66rem;min-width:0;}" +
        ".kit-mtrack{min-width:34px;}" +
        ".kit-msep{margin:0;height:14px;}" +
      "}";
    document.head.appendChild(st);
  }
  var SVG_CROP_M = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/></svg>';
  var _kitPdfJsProm = null;
  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (_kitPdfJsProm) return _kitPdfJsProm;
    function loadSrc(url, disableWorker) {
      return new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = url;
        s.onload = function () {
          if (window.pdfjsLib) {
            if (disableWorker) {
              window._kitPdfDisableWorker = true;
            } else {
              window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/_vendor/pdf.worker.min.js";
            }
            resolve(window.pdfjsLib);
          } else {
            reject(new Error("pdfjsLib missing"));
          }
        };
        s.onerror = function () { s.remove(); reject(new Error("load failed")); };
        document.head.appendChild(s);
      });
    }
    _kitPdfJsProm = loadSrc("/_vendor/pdf.min.js", false).catch(function () {
      return loadSrc("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js", true);
    });
    return _kitPdfJsProm;
  }
  function buildPdfJsViewer(pdfUrl, opts) {
    ensureMediaPlayerCss();
    opts = opts || {};
    var wrap = document.createElement("div"); wrap.className = "kit-pdf-wrap";
    var bar = document.createElement("div"); bar.className = "kit-pdf-bar";
    var prevB = document.createElement("button"); prevB.type = "button"; prevB.className = "kit-mbtn"; prevB.title = "Previous page"; prevB.setAttribute("aria-label", "Previous page");
    prevB.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>';
    var pIn = document.createElement("input"); pIn.type = "number"; pIn.min = "1"; pIn.value = "1"; pIn.className = "kit-pdf-page-in";
    pIn.style.cssText = "width:48px;height:28px;padding:0 4px;border-radius:7px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-family:var(--mono);font-size:.78rem;text-align:center;";
    var pTot = document.createElement("span"); pTot.className = "note"; pTot.textContent = "/ …";
    var nextB = document.createElement("button"); nextB.type = "button"; nextB.className = "kit-mbtn"; nextB.title = "Next page"; nextB.setAttribute("aria-label", "Next page");
    nextB.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
    var cropB = document.createElement("button"); cropB.type = "button"; cropB.className = "kit-mbtn";
    cropB.title = "Drag to highlight & crop any region to comment"; cropB.setAttribute("aria-label", "Highlight region to comment");
    cropB.innerHTML = SVG_CROP_M;
    var cropOn = false;
    function setCrop(on) {
      cropOn = !!on;
      wrap.classList.toggle("crop-on", cropOn);
      cropB.classList.toggle("on", cropOn);
    }
    cropB.addEventListener("click", function (ev) { ev.stopPropagation(); setCrop(!cropOn); });
    bar.appendChild(prevB); bar.appendChild(pIn); bar.appendChild(pTot); bar.appendChild(nextB);
    var sp = document.createElement("span"); sp.className = "sp"; bar.appendChild(sp);
    bar.appendChild(cropB);
    if (opts.extraBtns && opts.extraBtns.length) {
      var sep = document.createElement("span"); sep.className = "kit-msep"; bar.appendChild(sep);
      for (var bi = 0; bi < opts.extraBtns.length; bi++) {
        if (opts.extraBtns[bi]) bar.appendChild(opts.extraBtns[bi]);
      }
    }
    var scrollEl = document.createElement("div"); scrollEl.className = "kit-pdf-scroll";
    var pagesEl = document.createElement("div"); pagesEl.className = "kit-pdf-pages";
    pagesEl.innerHTML = '<div class="note" style="padding:24px">Loading PDF…</div>';
    scrollEl.appendChild(pagesEl);
    wrap.appendChild(bar); wrap.appendChild(scrollEl);
    var numPages = 1, curPage = 1, pageEls = [];
    function goPage(pg) {
      pg = Math.max(1, Math.min(numPages, parseInt(pg, 10) || 1));
      curPage = pg; pIn.value = String(pg);
      if (opts.onPageChange) opts.onPageChange(pg);
      var target = pageEls[pg - 1];
      if (target) scrollEl.scrollTo({ top: Math.max(0, target.offsetTop - 10), behavior: "smooth" });
    }
    wrap._kitGoPage = goPage;
    wrap._kitGetPage = function () { return curPage; };
    wrap._kitSetZoom = function (scaleVal) {
      var s = Math.max(0.55, Math.min(3, Number(scaleVal) || 1));
      var maxW = 0;
      for (var zi = 0; zi < pageEls.length; zi++) {
        var pe = pageEls[zi];
        if (!pe) continue;
        var bw = Number(pe.dataset.baseW || "0"), bh = Number(pe.dataset.baseH || "0");
        if (bw > 0 && bh > 0) {
          var nw = Math.round(bw * s), nh = Math.round(bh * s);
          pe.style.width = nw + "px";
          pe.style.height = nh + "px";
          if (nw > maxW) maxW = nw;
          var tl = pe.querySelector(".kit-pdf-txt");
          if (tl) tl.style.transform = s === 1 ? "" : ("scale(" + s + ")");
        }
      }
      return maxW;
    };
    pIn.addEventListener("change", function () { goPage(pIn.value); });
    prevB.addEventListener("click", function (ev) { ev.stopPropagation(); goPage(curPage - 1); });
    nextB.addEventListener("click", function (ev) { ev.stopPropagation(); goPage(curPage + 1); });
    scrollEl.addEventListener("scroll", function () {
      if (!pageEls.length) return;
      var mid = scrollEl.scrollTop + scrollEl.clientHeight * 0.35;
      var best = 1;
      for (var i = 0; i < pageEls.length; i++) {
        if (pageEls[i].offsetTop <= mid) best = i + 1; else break;
      }
      if (best !== curPage) {
        curPage = best; pIn.value = String(best);
        if (opts.onPageChange) opts.onPageChange(best);
      }
    }, { passive: true });
    function attachCropHandler(pgDiv, canvas, txtLayer, pgNum) {
      var selLayer = document.createElement("div"); selLayer.className = "kit-pdf-sel-layer";
      pgDiv.appendChild(selLayer);
      var drag = null, rectEl = null;
      selLayer.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0) return;
        ev.preventDefault(); ev.stopPropagation();
        var r = pgDiv.getBoundingClientRect();
        drag = { sx: ev.clientX - r.left, sy: ev.clientY - r.top, r: r };
        rectEl = document.createElement("div"); rectEl.className = "kit-pdf-rect";
        selLayer.appendChild(rectEl);
        if (selLayer.setPointerCapture) try { selLayer.setPointerCapture(ev.pointerId); } catch (e) {}
      });
      selLayer.addEventListener("pointermove", function (ev) {
        if (!drag || !rectEl) return;
        var x = Math.max(0, Math.min(drag.r.width, ev.clientX - drag.r.left));
        var y = Math.max(0, Math.min(drag.r.height, ev.clientY - drag.r.top));
        var lx = Math.min(drag.sx, x), ty = Math.min(drag.sy, y);
        var w = Math.abs(x - drag.sx), h = Math.abs(y - drag.sy);
        rectEl.style.left = lx + "px"; rectEl.style.top = ty + "px";
        rectEl.style.width = w + "px"; rectEl.style.height = h + "px";
      });
      selLayer.addEventListener("pointerup", function (ev) {
        if (!drag || !rectEl) return;
        var x = Math.max(0, Math.min(drag.r.width, ev.clientX - drag.r.left));
        var y = Math.max(0, Math.min(drag.r.height, ev.clientY - drag.r.top));
        var lx = Math.min(drag.sx, x), ty = Math.min(drag.sy, y);
        var rw = Math.abs(x - drag.sx), rh = Math.abs(y - drag.sy);
        var boxRect = { left: drag.r.left + lx, top: drag.r.top + ty, right: drag.r.left + lx + rw, bottom: drag.r.top + ty + rh };
        rectEl.remove(); rectEl = null; drag = null;
        if (rw < 10 || rh < 10) return;
        setCrop(false);
        var sxRatio = canvas.width / Math.max(1, pgDiv.clientWidth);
        var syRatio = canvas.height / Math.max(1, pgDiv.clientHeight);
        var cw = Math.max(1, Math.round(rw * sxRatio)), ch = Math.max(1, Math.round(rh * syRatio));
        var off = document.createElement("canvas"); off.width = cw; off.height = ch;
        var octx = off.getContext("2d");
        octx.drawImage(canvas, Math.round(lx * sxRatio), Math.round(ty * syRatio), cw, ch, 0, 0, cw, ch);
        var hitSpans = [];
        var spans = txtLayer.querySelectorAll("span");
        for (var si = 0; si < spans.length; si++) {
          var sr = spans[si].getBoundingClientRect();
          if (sr.width > 0 && sr.right > boxRect.left && sr.left < boxRect.right && sr.bottom > boxRect.top && sr.top < boxRect.bottom) {
            var st = (spans[si].textContent || "").trim();
            if (st) hitSpans.push(st);
          }
        }
        var boxText = hitSpans.join(" ").replace(/\s+/g, " ").trim().slice(0, 400);
        off.toBlob(function (blob) {
          var done = function (imgUrl) {
            if (opts.onRegionCrop) opts.onRegionCrop({ page: pgNum, imgUrl: imgUrl, text: boxText });
          };
          if (blob && window.claude && window.claude.assets && typeof window.claude.assets.upload === "function") {
            var fObj = new File([blob], "pdf-crop-p" + pgNum + ".png", { type: "image/png" });
            window.claude.assets.upload(fObj).then(function (up) {
              var id = (up && up.id) || up || "";
              done(id ? ("/_blob/" + id) : "");
            }, function () { done(off.toDataURL("image/png")); });
          } else {
            done(off.toDataURL("image/png"));
          }
        }, "image/png");
      });
    }
    loadPdfJs().then(function (lib) {
      return lib.getDocument({ url: pdfUrl.split("#")[0], disableWorker: !!window._kitPdfDisableWorker }).promise.then(function (pdf) {
        numPages = pdf.numPages || 1;
        pTot.textContent = "/ " + numPages;
        pIn.max = String(numPages);
        pagesEl.textContent = "";
        var availW = window.innerWidth <= 700
          ? Math.max(260, (scrollEl.clientWidth || (window.innerWidth - 12)) - 8)
          : Math.max(300, Math.min(860, (scrollEl.clientWidth || 720) - 28));
        var dpr = Math.min(2, window.devicePixelRatio || 1);
        var chain = Promise.resolve();
        for (var pNum = 1; pNum <= numPages; pNum++) {
          (function (pn) {
            chain = chain.then(function () {
              return pdf.getPage(pn).then(function (page) {
                var baseVp = page.getViewport({ scale: 1 });
                var scale = availW / Math.max(1, baseVp.width);
                var vp = page.getViewport({ scale: scale });
                var pgDiv = document.createElement("div");
                pgDiv.className = "kit-pdf-page";
                pgDiv.dataset.page = String(pn);
                pgDiv.dataset.baseW = String(Math.round(vp.width));
                pgDiv.dataset.baseH = String(Math.round(vp.height));
                pgDiv.style.width = Math.round(vp.width) + "px";
                pgDiv.style.height = Math.round(vp.height) + "px";
                var cv = document.createElement("canvas");
                cv.width = Math.round(vp.width * dpr);
                cv.height = Math.round(vp.height * dpr);
                cv.style.width = "100%";
                cv.style.height = "100%";
                var ctx = cv.getContext("2d");
                ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                var txtLayer = document.createElement("div");
                txtLayer.className = "kit-pdf-txt";
                txtLayer.style.width = Math.round(vp.width) + "px";
                txtLayer.style.height = Math.round(vp.height) + "px";
                pgDiv.appendChild(cv); pgDiv.appendChild(txtLayer);
                attachCropHandler(pgDiv, cv, txtLayer, pn);
                pagesEl.appendChild(pgDiv);
                pageEls[pn - 1] = pgDiv;
                return page.render({ canvasContext: ctx, viewport: vp }).promise.then(function () {
                  return page.getTextContent().then(function (tc) {
                    var items = (tc && tc.items) || [];
                    for (var ii = 0; ii < items.length; ii++) {
                      var it = items[ii];
                      if (!it.str) continue;
                      var tx = lib.Util.transform(vp.transform, it.transform);
                      var fh = Math.hypot(tx[2], tx[3]) || Math.abs(tx[0]) || 12;
                      var spn = document.createElement("span");
                      spn.textContent = it.str;
                      spn.style.left = tx[4].toFixed(1) + "px";
                      spn.style.top = (tx[5] - fh * 0.85).toFixed(1) + "px";
                      spn.style.fontSize = fh.toFixed(1) + "px";
                      if (it.width > 0) spn.style.width = (it.width * scale).toFixed(1) + "px";
                      txtLayer.appendChild(spn);
                    }
                  });
                });
              });
            });
          })(pNum);
        }
      });
    }).catch(function () {
      pagesEl.textContent = "";
      var ifr = document.createElement("iframe");
      ifr.className = "kit-pdf-iframe";
      ifr.src = pdfUrl.split("#")[0] + "#toolbar=0&navpanes=0";
      ifr.title = opts.title || "PDF";
      ifr.style.cssText = "width:100%;height:100%;min-height:460px;border:0;background:#fff;";
      pagesEl.style.height = "100%";
      pagesEl.appendChild(ifr);
    });
    return wrap;
  }
  var SVG_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"/></svg>';
  var SVG_PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
  var SVG_VOL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
  var SVG_MUTE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
  var SVG_FS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
  var SVG_LIKE_M = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
  var SVG_CMT_M = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>';

  function makeMediaLikeBtn(fp, postId) {
    var b = document.createElement("button"); b.type = "button"; b.className = "kit-mbtn";
    b.title = "Like"; b.setAttribute("aria-label", "Like"); b.innerHTML = SVG_LIKE_M;
    var lkKey = "kit_media_like:" + (postId || "") + ":" + (fp || "");
    try { if (localStorage.getItem(lkKey) === "1") { b.classList.add("liked"); b.title = "Liked"; } } catch (e) {}
    b.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (b.classList.contains("liked")) return;
      b.classList.add("liked"); b.title = "Liked";
      try { localStorage.setItem(lkKey, "1"); } catch (e) {}
      var pid = postId;
      if (!pid) { try { pid = sessionStorage.getItem("board-open") || ""; } catch (e) {} }
      if (pid && window.firebase && typeof window.firebase.firestore === "function") {
        try {
          window.firebase.firestore().collection("comments").doc().set({
            post_id: pid, image: fp || "", text: "Liked " + (fp || ""), like: true,
            from: "owner", created_at: new Date().toISOString(), delivered: false, delivery_error: "", read: false
          });
        } catch (e) {}
      }
    });
    return b;
  }

  function wireMediaControls(mediaEl, barEl, wrapEl, extraBtns) {
    var playB = document.createElement("button"); playB.type = "button"; playB.className = "kit-mbtn"; playB.title = "Play"; playB.setAttribute("aria-label", "Play"); playB.innerHTML = SVG_PLAY;
    var track = document.createElement("div"); track.className = "kit-mtrack";
    var bar = document.createElement("div"); bar.className = "kit-mbar";
    var fill = document.createElement("div"); fill.className = "kit-mfill";
    bar.appendChild(fill); track.appendChild(bar);
    var tm = document.createElement("span"); tm.className = "kit-mtime"; tm.textContent = "00:00 / 00:00";
    var speeds = [1, 1.5, 2], sIdx = 0;
    var spdB = document.createElement("button"); spdB.type = "button"; spdB.className = "kit-mspd"; spdB.textContent = "1x"; spdB.title = "Playback speed";
    var mutB = document.createElement("button"); mutB.type = "button"; mutB.className = "kit-mbtn"; mutB.title = "Mute"; mutB.setAttribute("aria-label", "Mute"); mutB.innerHTML = SVG_VOL;
    barEl.appendChild(playB); barEl.appendChild(track); barEl.appendChild(tm); barEl.appendChild(spdB); barEl.appendChild(mutB);
    function syncUi() {
      var cur = mediaEl.currentTime || 0, dur = isFinite(mediaEl.duration) ? mediaEl.duration : 0;
      fill.style.width = (dur > 0 ? Math.min(100, (cur / dur) * 100) : 0) + "%";
      tm.textContent = fmtMediaTime(cur).split(".")[0] + " / " + (dur > 0 ? fmtMediaTime(dur).split(".")[0] : "00:00");
      var on = !mediaEl.paused && !mediaEl.ended;
      playB.classList.toggle("playing", on);
      playB.innerHTML = on ? SVG_PAUSE : SVG_PLAY;
      playB.title = on ? "Pause" : "Play";
      mutB.innerHTML = mediaEl.muted ? SVG_MUTE : SVG_VOL;
    }
    function togglePlay() {
      if (mediaEl.paused || mediaEl.ended) mediaEl.play().catch(function () {});
      else mediaEl.pause();
    }
    playB.addEventListener("click", function (ev) { ev.stopPropagation(); togglePlay(); });
    spdB.addEventListener("click", function (ev) {
      ev.stopPropagation(); sIdx = (sIdx + 1) % speeds.length;
      mediaEl.playbackRate = speeds[sIdx]; spdB.textContent = speeds[sIdx] + "x";
    });
    mutB.addEventListener("click", function (ev) { ev.stopPropagation(); mediaEl.muted = !mediaEl.muted; syncUi(); });
    function seekAt(clientX) {
      var dur = isFinite(mediaEl.duration) ? mediaEl.duration : 0;
      if (dur <= 0) return;
      var r = track.getBoundingClientRect();
      var pct = Math.max(0, Math.min(1, (clientX - r.left) / Math.max(1, r.width)));
      mediaEl.currentTime = pct * dur;
      syncUi();
    }
    track.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation(); seekAt(ev.clientX);
      var onMove = function (e2) { seekAt(e2.clientX); };
      var onUp = function () { window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onUp); };
      window.addEventListener("pointermove", onMove); window.addEventListener("pointerup", onUp);
    });
    ["timeupdate", "loadedmetadata", "play", "pause", "ended", "volumechange"].forEach(function (e) { mediaEl.addEventListener(e, syncUi); });
    if (wrapEl && mediaEl.tagName === "VIDEO") {
      var fsB = document.createElement("button"); fsB.type = "button"; fsB.className = "kit-mbtn"; fsB.title = "Fullscreen"; fsB.setAttribute("aria-label", "Fullscreen"); fsB.innerHTML = SVG_FS;
      fsB.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (document.fullscreenElement) document.exitFullscreen && document.exitFullscreen();
        else if (wrapEl.requestFullscreen) wrapEl.requestFullscreen();
      });
      barEl.appendChild(fsB);
      mediaEl.addEventListener("click", function (ev) {
        var host = wrapEl.closest ? wrapEl.closest(".kit-docs-modal, .kit-inline-embed") : null;
        if (host) {
          if (host.classList.contains("kit-edge-hover")) return;
          if (Date.now() - Number(host.dataset.kitJustResized || 0) < 260) return;
          var hr = host.getBoundingClientRect();
          if (ev.clientX - hr.left <= 16 || hr.right - ev.clientX <= 16 || ev.clientY - hr.top <= 16 || hr.bottom - ev.clientY <= 16) return;
        }
        ev.stopPropagation();
        togglePlay();
      });
      var idleT = null;
      function wakeBar() {
        wrapEl.classList.remove("idle");
        clearTimeout(idleT);
        if (!mediaEl.paused && !mediaEl.ended) idleT = setTimeout(function () { wrapEl.classList.add("idle"); }, 2200);
      }
      wrapEl.addEventListener("pointermove", wakeBar);
      mediaEl.addEventListener("play", wakeBar);
      mediaEl.addEventListener("pause", function () { clearTimeout(idleT); wrapEl.classList.remove("idle"); });
    }
    if (extraBtns && extraBtns.length) {
      var sep = document.createElement("span"); sep.className = "kit-msep";
      barEl.appendChild(sep);
      for (var bi = 0; bi < extraBtns.length; bi++) {
        if (extraBtns[bi]) barEl.appendChild(extraBtns[bi]);
      }
    }
    syncUi();
  }
  function buildCustomAudioPlayer(src, opts) {
    ensureMediaPlayerCss();
    var wrap = document.createElement("div"); wrap.className = "kit-aud";
    var aud = document.createElement("audio"); aud.preload = "metadata"; aud.src = src; aud.style.display = "none";
    wrap.appendChild(aud);
    wireMediaControls(aud, wrap, wrap, opts && opts.extraBtns);
    return wrap;
  }
  function buildCustomVideoPlayer(src, opts) {
    ensureMediaPlayerCss();
    var wrap = document.createElement("div"); wrap.className = "kit-vid" + (opts && opts.fullHeight ? " full" : "");
    var vid = document.createElement("video"); vid.playsInline = true; vid.preload = "metadata"; vid.src = src;
    var bar = document.createElement("div"); bar.className = "kit-vid-bar";
    wrap.appendChild(vid); wrap.appendChild(bar);
    wireMediaControls(vid, bar, wrap, opts && opts.extraBtns);
    return wrap;
  }

  function fmtMediaTime(sec) {
    var s = Math.max(0, Number(sec) || 0);
    var m = Math.floor(s / 60);
    var r = s - m * 60;
    var whole = Math.floor(r);
    var tenth = Math.floor((r - whole) * 10);
    var mm = (m < 10 ? "0" : "") + m;
    var ss = (whole < 10 ? "0" : "") + whole;
    return tenth ? (mm + ":" + ss + "." + tenth) : (mm + ":" + ss);
  }
  function captureVideoContactSheet(videoEl) {
    if (!videoEl || !videoEl.src) return Promise.resolve("");
    return new Promise(function (resolve) {
      var cur = Number(videoEl.currentTime) || 0;
      var dur = Number(videoEl.duration) || 0;
      var offsets = [-2, -1, 0, 1, 2];
      var times = [];
      offsets.forEach(function (d) {
        var t = Math.max(0, dur ? Math.min(dur - 0.05, cur + d) : Math.max(0, cur + d));
        t = Math.round(t * 10) / 10;
        if (times.indexOf(t) < 0) times.push(t);
      });
      if (!times.length) times = [cur];
      var probe = document.createElement("video");
      probe.crossOrigin = "anonymous";
      probe.muted = true;
      probe.playsInline = true;
      probe.preload = "auto";
      probe.src = videoEl.currentSrc || videoEl.src;
      var fw = 200, fh = 112, pad = 8, lblH = 22;
      var canvas = document.createElement("canvas");
      canvas.width = times.length * (fw + pad) + pad;
      canvas.height = fh + lblH + pad * 2;
      var ctx = canvas.getContext("2d");
      if (!ctx) { resolve(""); return; }
      ctx.fillStyle = "#0f1624";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      var idx = 0, done = false;
      function finish() {
        if (done) return;
        done = true;
        try {
          canvas.toBlob(function (blob) {
            if (!blob || !assets || !assets.upload) { resolve(""); return; }
            var f = new File([blob], "video-frames-" + Math.round(cur) + "s.png", { type: "image/png" });
            assets.upload(f).then(function (up) { resolve((up && up.url) || ""); }, function () { resolve(""); });
          }, "image/png");
        } catch (e) { resolve(""); }
      }
      var guard = setTimeout(finish, 6000);
      function grabNext() {
        if (idx >= times.length) { clearTimeout(guard); finish(); return; }
        var t = times[idx];
        var onSeek = function () {
          probe.removeEventListener("seeked", onSeek);
          var x = pad + idx * (fw + pad), y = pad;
          try {
            ctx.drawImage(probe, x, y, fw, fh);
          } catch (e) {
            ctx.fillStyle = "#1c2638"; ctx.fillRect(x, y, fw, fh);
          }
          var isCenter = Math.abs(t - Math.round(cur * 10) / 10) < 0.15;
          ctx.strokeStyle = isCenter ? "#e8894a" : "#2e3b52";
          ctx.lineWidth = isCenter ? 2.5 : 1;
          ctx.strokeRect(x, y, fw, fh);
          ctx.fillStyle = isCenter ? "#e8894a" : "#cbd5e1";
          ctx.font = (isCenter ? "bold " : "") + "12px monospace";
          ctx.textAlign = "center";
          ctx.fillText(fmtMediaTime(t), x + fw / 2, y + fh + 16);
          idx++;
          grabNext();
        };
        probe.addEventListener("seeked", onSeek);
        try { probe.currentTime = t; } catch (e) { probe.removeEventListener("seeked", onSeek); idx++; grabNext(); }
      }
      probe.addEventListener("loadeddata", function () { grabNext(); }, { once: true });
      probe.addEventListener("error", function () { clearTimeout(guard); resolve(""); }, { once: true });
      probe.load();
    });
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
    var isPdf = /\.pdf$/i.test(fp);
    var isAudio = /\.(?:mp3|wav|ogg|m4a|flac|aac|opus)$/i.test(fp);
    var isVideo = /\.(?:mp4|webm|mov|m4v)$/i.test(fp);
    var isMd = /\.md$/i.test(fp);
    /* Embed images, HTML, PDF, audio, video, and Markdown section links inline right under their referencing block */
    if (!isImg && !isHtml && !isPdf && !isAudio && !isVideo && !(isMd && sec)) return;

    var SVG_POP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
    var SVG_CMT2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/></svg>';
    var SVG_PH2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>';
    var SVG_DK2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
    var SVG_CD2 = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="14" y1="4" x2="10" y2="20"/></svg>';

    var kindTag = isHtml ? "html" : (isImg ? "img" : (isPdf ? "pdf" : (isAudio ? "audio" : (isVideo ? "video" : "md"))));
    var postIdCtx = "";
    try { postIdCtx = (postBody.closest(".kit-post-win") && postBody.closest(".kit-post-win").dataset.pid) || sessionStorage.getItem("board-open") || ""; } catch (e) {}
    var card = document.createElement("div"); card.className = "kit-inline-embed"; card.dataset.kind = kindTag; card.dataset.ekey = key;
    var hdr = document.createElement("div"); hdr.className = "kit-inline-embed-hdr";
    var ttl = document.createElement("span"); ttl.className = "iettl";
    ttl.textContent = fp + (sec ? " § " + sec : ""); ttl.title = ttl.textContent;
    hdr.appendChild(ttl);

    var body = document.createElement("div"); body.className = "kit-inline-embed-body";
    body.innerHTML = '<div class="note">Loading ' + fp + '...</div>';
    var resCtrl = isAudio ? null : makeInlineResizable(card, body, kindTag, postIdCtx + ":" + fp);

    var cmtB = document.createElement("button"); cmtB.type = "button"; cmtB.className = "btn small kit-icon-btn";
    cmtB.title = "Comment on " + ttl.textContent; cmtB.setAttribute("aria-label", cmtB.title); cmtB.innerHTML = SVG_CMT2;
    cmtB.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (typeof window._kitOpenOrchNew !== "function") return;
      if (isAudio) {
        var ae = body.querySelector("audio");
        if (ae && !ae.paused) try { ae.pause(); } catch (e) {}
        window._kitOpenOrchNew("Re `" + fp + "` @ " + fmtMediaTime(ae ? ae.currentTime : 0) + ":\n");
      } else if (isVideo) {
        var ve = body.querySelector("video");
        if (ve && !ve.paused) try { ve.pause(); } catch (e) {}
        var vts = fmtMediaTime(ve ? ve.currentTime : 0);
        cmtB.disabled = true;
        captureVideoContactSheet(ve).then(function (stripUrl) {
          cmtB.disabled = false;
          window._kitOpenOrchNew("Re `" + fp + "` @ " + vts + ":\n" + (stripUrl ? ("![Frames around " + vts + "](" + stripUrl + ")\n\n") : ""));
        });
      } else if (isPdf) {
        var pdfW = body.querySelector(".kit-pdf-wrap");
        var pgN = (pdfW && typeof pdfW._kitGetPage === "function") ? pdfW._kitGetPage() : 1;
        var pSelI = "";
        try {
          var selI = window.getSelection ? window.getSelection() : null;
          if (selI && selI.toString().trim() && body.contains(selI.anchorNode)) pSelI = selI.toString().trim().slice(0, 400);
        } catch (e) {}
        var pPreI = "Re `" + fp + "` p. " + pgN + ":\n" + (pSelI ? ("> " + pSelI.replace(/\n+/g, "\n> ") + "\n\n") : "");
        window._kitOpenOrchNew(pPreI);
      } else {
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
    } else if (isPdf) {
      body.classList.add("media");
      body.textContent = "";
      body.style.height = "480px";
      body.style.maxHeight = "none";
      body.style.overflow = "hidden";
      cmtB.className = "kit-mbtn";
      popB.className = "kit-mbtn";
      var pLike = makeMediaLikeBtn(fp, postIdCtx);
      body.appendChild(buildPdfJsViewer(withKitToken(resolveAssetUrl(fp, "", repoOverride)), {
        title: fp,
        extraBtns: [pLike, cmtB, popB],
        onRegionCrop: function (info) {
          if (typeof window._kitOpenOrchNew !== "function") return;
          var pre = "Re `" + fp + "` p. " + (info.page || 1) + ":\n";
          if (info.imgUrl) pre += "![Selection on p. " + (info.page || 1) + "](" + info.imgUrl + ")\n";
          if (info.text) pre += "> " + info.text.replace(/\n+/g, "\n> ") + "\n";
          window._kitOpenOrchNew(pre + "\n");
        }
      }));
    } else if (isAudio) {
      card.style.cssText = "border:0;background:transparent;box-shadow:none;overflow:visible;";
      body.textContent = "";
      body.style.cssText = "padding:0;background:transparent;max-height:none;overflow:visible;";
      cmtB.className = "kit-mbtn";
      popB.className = "kit-mbtn";
      var aLike = makeMediaLikeBtn(fp, postIdCtx);
      body.appendChild(buildCustomAudioPlayer(withKitToken(resolveAssetUrl(fp, "", repoOverride)), { extraBtns: [aLike, cmtB, popB] }));
    } else if (isVideo) {
      body.classList.add("media");
      body.classList.add("is-vid");
      body.textContent = "";
      cmtB.className = "kit-mbtn";
      popB.className = "kit-mbtn";
      var vLike = makeMediaLikeBtn(fp, postIdCtx);
      var vPlayer = buildCustomVideoPlayer(withKitToken(resolveAssetUrl(fp, "", repoOverride)), { extraBtns: [vLike, cmtB, popB] });
      body.appendChild(vPlayer);
      var vTag = vPlayer.querySelector("video");
      if (vTag) {
        var vZoom = 100, vTouch = null;
        function applyInlineVidZoom(nz) {
          vZoom = Math.max(55, Math.min(260, Math.round(nz)));
          var baseW = vTag.videoWidth || vPlayer.clientWidth || 640;
          var scaledW = Math.max(240, Math.min(window.innerWidth - 28, Math.round(baseW * (vZoom / 100))));
          if (vZoom === 100) {
            vTag.style.transform = "";
            card.style.width = "";
            card.style.marginLeft = "";
            card.style.transform = "";
          } else if (window.innerWidth >= 720) {
            card.style.width = Math.min(window.innerWidth - 24, scaledW) + "px";
            card.style.marginLeft = "50%";
            card.style.transform = "translateX(-50%)";
            vTag.style.transform = "";
          } else {
            vTag.style.transform = "scale(" + (vZoom / 100) + ")";
          }
        }
        vPlayer.addEventListener("wheel", function (ev) {
          if (ev.ctrlKey || ev.metaKey) {
            ev.preventDefault(); ev.stopPropagation();
            var step = Math.max(3, Math.min(18, Math.abs(ev.deltaY) * 0.8));
            applyInlineVidZoom(vZoom + (ev.deltaY < 0 ? step : -step));
          }
        }, { passive: false });
        vPlayer.addEventListener("touchstart", function (ev) {
          if (ev.touches && ev.touches.length === 2) {
            ev.preventDefault();
            var d0 = Math.hypot(ev.touches[1].clientX - ev.touches[0].clientX, ev.touches[1].clientY - ev.touches[0].clientY) || 1;
            vTouch = { d0: d0, z0: vZoom };
          }
        }, { passive: false });
        vPlayer.addEventListener("touchmove", function (ev) {
          if (vTouch && ev.touches && ev.touches.length === 2) {
            ev.preventDefault(); ev.stopPropagation();
            var d1 = Math.hypot(ev.touches[1].clientX - ev.touches[0].clientX, ev.touches[1].clientY - ev.touches[0].clientY) || vTouch.d0;
            applyInlineVidZoom(vTouch.z0 * (d1 / vTouch.d0));
          }
        }, { passive: false });
        vPlayer.addEventListener("touchend", function (ev) {
          if (!ev.touches || ev.touches.length < 2) vTouch = null;
        }, { passive: true });
      }
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

    if (!isAudio && !isVideo && !isPdf) {
      hdr.appendChild(cmtB); hdr.appendChild(popB);
      card.appendChild(hdr);
    }
    card.appendChild(body);
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
    var fileRe = /^(?:docs\/|\.story\/|\.board\/|\.kit\/)?[A-Za-z0-9_./-]+\.(?:md|html?|png|jpe?g|gif|webp|avif|svg|pdf|mp3|wav|ogg|m4a|flac|aac|opus|mp4|webm|mov|m4v)$/i;
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
      var hrefVal = a.getAttribute("href") || "";
      var pl = parseAssociatedDocLink(hrefVal, "");
      if (pl && !pl.anchorOnly) {
        a.classList.add("kit-doc-link");
        a.removeAttribute("target");
        a.title = "Open " + pl.path + (pl.section ? " § " + pl.section : "") + " in Document Viewer";
        mountInlineViewerEmbed(a, pl.path, pl.section || "", pl.repoId);
      } else if (/^\/_blob\/[0-9a-f]{16,64}$/i.test(hrefVal)) {
        var lbl = (a.getAttribute("download") || a.textContent || "").trim();
        var bUrl = withKitToken(hrefVal);
        if (/\.(?:mp3|wav|ogg|m4a|flac|aac|opus)$/i.test(lbl) && !a.nextElementSibling) {
          var ba = buildCustomAudioPlayer(bUrl);
          ba.style.margin = "6px 0";
          a.insertAdjacentElement("afterend", ba);
        } else if (/\.(?:mp4|webm|mov|m4v)$/i.test(lbl) && !a.nextElementSibling) {
          var bv = buildCustomVideoPlayer(bUrl);
          bv.style.maxWidth = "640px";
          bv.style.margin = "6px 0";
          a.insertAdjacentElement("afterend", bv);
        } else if (/\.pdf$/i.test(lbl) && !a.nextElementSibling) {
          var bp = document.createElement("div");
          bp.style.cssText = "display:block;width:100%;height:480px;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:8px 0;";
          bp.appendChild(buildPdfJsViewer(bUrl, { title: lbl }));
          a.insertAdjacentElement("afterend", bp);
        }
      }
    }
    /* Unified inline post media viewer: resolve repo images (#p-pics + body + comments), follow zoomed image size, and synchronize pinch-to-zoom across side-by-side compare groups */
    var postImgs = root.querySelectorAll("#p-pics img, #p-imgs .pic img, .kit-inline-cmp img, #p-body img, .kit-post-win-body img, .cmt img");
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
  function ensureExtBadgeCss() {
    if (document.getElementById("kit-ext-badge-css")) return;
    var st = document.createElement("style");
    st.id = "kit-ext-badge-css";
    st.textContent =
      ".kit-ext-badges{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px;}" +
      ".kit-ext-badge{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;font-size:.72rem;font-weight:700;letter-spacing:.01em;border:1px solid var(--line);background:var(--sunk);color:var(--muted);text-decoration:none;}" +
      "a.kit-ext-badge:hover{filter:brightness(1.1);text-decoration:underline;}" +
      ".kit-ext-badge::before{content:'';width:6px;height:6px;border-radius:50%;background:currentColor;flex:none;}" +
      ".kit-ext-badge[data-st='done']{color:var(--done,#2f7a4f);border-color:color-mix(in srgb,var(--done,#2f7a4f) 38%,var(--line));}" +
      ".kit-ext-badge[data-st='running']{color:var(--info,#2b6c9e);border-color:color-mix(in srgb,var(--info,#2b6c9e) 38%,var(--line));}" +
      ".kit-ext-badge[data-st='todo'],.kit-ext-badge[data-st='you']{color:var(--todo,#c4621a);border-color:color-mix(in srgb,var(--todo,#c4621a) 38%,var(--line));}" +
      ".kit-ext-badge[data-st='question'],.kit-ext-badge[data-st='ready']{color:var(--question,#6a4fb3);border-color:color-mix(in srgb,var(--question,#6a4fb3) 38%,var(--line));}" +
      ".kit-ext-badge[data-st='blocked'],.kit-ext-badge[data-st='idle']{color:var(--muted);}";
    document.head.appendChild(st);
  }
  function buildExtBadgesEl(badges, key) {
    if (!Array.isArray(badges) || !badges.length) return null;
    ensureExtBadgeCss();
    var wrap = document.createElement("div");
    wrap.className = "kit-ext-badges";
    if (key) wrap.dataset.badgeKey = key;
    badges.slice(0, 8).forEach(function (b) {
      if (!b || !b.label) return;
      var safeUrl = (typeof b.url === "string" && /^https?:\/\//i.test(b.url)) ? b.url : "";
      var el = document.createElement(safeUrl ? "a" : "span");
      el.className = "kit-ext-badge";
      el.dataset.st = String(b.state || "idle").toLowerCase();
      el.textContent = String(b.label).slice(0, 64);
      if (safeUrl) { el.href = safeUrl; el.target = "_blank"; el.rel = "noopener"; el.addEventListener("click", function (ev) { ev.stopPropagation(); }); }
      wrap.appendChild(el);
    });
    return wrap.childNodes.length ? wrap : null;
  }
  function renderPostExtBadges(sheet, postId) {
    if (!sheet || !postId || postId.charAt(0) === "_") {
      var oldB = document.getElementById("kit-post-ext-badges");
      if (oldB) oldB.remove();
      return;
    }
    var metaEl = sheet.querySelector("#p-meta");
    if (!metaEl) return;
    var bKey = (window.__KIT_ACTIVE_REPO__ || "") + "|" + postId;
    if (metaEl.dataset.kitBadgePost === bKey) return;
    metaEl.dataset.kitBadgePost = bKey;
    var stale = document.getElementById("kit-post-ext-badges");
    if (stale) stale.remove();
    api("GET", "/_api/db/posts/" + encodeURIComponent(postId)).then(function (r) {
      if (metaEl.dataset.kitBadgePost !== bKey) return;
      var badges = (r && r.data && r.data.badges) || [];
      var cur = document.getElementById("kit-post-ext-badges");
      if (cur) cur.remove();
      var el = buildExtBadgesEl(badges, bKey);
      if (el) { el.id = "kit-post-ext-badges"; metaEl.insertAdjacentElement("afterend", el); }
    }, function () {});
  }
  function applyCollabCardBadges() {
    var hasCollab = document.documentElement.dataset.collab === "1";
    var uid = curUserId();
    if (!hasCollab && _kitMineOnly) {
      _kitMineOnly = false;
      var mb = document.getElementById("kit-mine-btn");
      if (mb) mb.classList.remove("on");
      var allC = document.querySelectorAll(".card[data-id], .ffeed-card[data-id]");
      for (var i = 0; i < allC.length; i++) allC[i].style.display = "";
    }
    var cards = document.querySelectorAll(".card[data-id], .ffeed-card[data-id]");
    if (!cards.length) return;
    api("GET", "/_api/db/posts").then(function (res) {
      var byId = {};
      ((res && res.docs) || []).forEach(function (d) { byId[d.id] = d.data || {}; });
      var cardsNow = document.querySelectorAll(".card[data-id], .ffeed-card[data-id]");
      for (var i = 0; i < cardsNow.length; i++) {
        var c = cardsNow[i], pid = c.dataset.id, doc = byId[pid] || {};
        var oldExt = c.querySelector(".kit-ext-badges");
        var sig = JSON.stringify(doc.badges || []);
        if (!oldExt || oldExt.dataset.badgeKey !== sig) {
          if (oldExt) oldExt.remove();
          var extEl = buildExtBadgesEl(doc.badges, sig);
          if (extEl) c.appendChild(extEl);
        }
        if (!hasCollab) continue;
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

        /* Pocket Push Notifications (ntfy.sh / webhook) */
        var pushWrap = document.createElement("div");
        pushWrap.style.cssText = "padding:10px 12px;border-radius:10px;background:var(--sunk);border:1px solid var(--line);display:flex;flex-direction:column;gap:8px;";
        var pushHdr = document.createElement("div");
        pushHdr.style.cssText = "display:flex;align-items:center;gap:6px;font-size:.76rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);";
        pushHdr.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg><span>Pocket Push Notifications</span>';
        var pushRow = document.createElement("div");
        pushRow.style.cssText = "display:flex;flex-wrap:wrap;gap:6px;align-items:center;";
        var pushIn = document.createElement("input");
        pushIn.type = "url"; pushIn.placeholder = "https://ntfy.sh/your-secret-topic";
        pushIn.style.cssText = "flex:1 1 180px;min-width:0;padding:6px 9px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font:inherit;font-size:.82rem;";
        var pushSave = document.createElement("button"); pushSave.type = "button"; pushSave.className = "btn small"; pushSave.textContent = "Save";
        var pushTest = document.createElement("button"); pushTest.type = "button"; pushTest.className = "btn small"; pushTest.textContent = "Test";
        var pushDetLbl = document.createElement("label");
        pushDetLbl.style.cssText = "display:inline-flex;align-items:center;gap:6px;font-size:.78rem;color:var(--muted);cursor:pointer;";
        var pushDet = document.createElement("input"); pushDet.type = "checkbox";
        pushDetLbl.appendChild(pushDet); pushDetLbl.appendChild(document.createTextNode("Include post title & preview (off by default for ntfy privacy)"));
        var pushSt = document.createElement("div"); pushSt.className = "note"; pushSt.style.fontSize = ".76rem";
        api("GET", "/_api/notify").then(function (r) {
          if (r && r.notify_url) pushIn.value = r.notify_url;
          if (r) pushDet.checked = !!r.notify_detail;
        }, function () {});
        pushSave.addEventListener("click", function () {
          pushSave.disabled = true;
          api("POST", "/_api/notify", { notify_url: pushIn.value.trim(), notify_detail: !!pushDet.checked }).then(function (r) {
            pushSave.disabled = false;
            pushSt.textContent = (r && r.notify_url) ? "Push webhook saved." : "Push notifications disabled.";
          }, function () { pushSave.disabled = false; pushSt.textContent = "Could not save push URL (must start with http:// or https://)."; });
        });
        pushTest.addEventListener("click", function () {
          var u = pushIn.value.trim();
          if (!u) { pushSt.textContent = "Enter an ntfy.sh or webhook URL first."; return; }
          pushTest.disabled = true; pushSt.textContent = "Sending test push...";
          api("POST", "/_api/notify", { notify_url: u, notify_detail: !!pushDet.checked, test: true }).then(function (r) {
            pushTest.disabled = false;
            pushSt.textContent = (r && r.sent) ? "Test push sent." : "Saved, but webhook did not accept the test push.";
          }, function () { pushTest.disabled = false; pushSt.textContent = "Test push failed."; });
        });
        pushRow.appendChild(pushIn); pushRow.appendChild(pushSave); pushRow.appendChild(pushTest);
        pushWrap.appendChild(pushHdr); pushWrap.appendChild(pushRow); pushWrap.appendChild(pushDetLbl); pushWrap.appendChild(pushSt);
        body.appendChild(pushWrap);

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
