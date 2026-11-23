/* artifact_repos.js — cross-repo boards in a claude.ai Artifact (the local server's --attach / "All boards").

   One Artifact holds every board: the primary repo's at the root of the db (posts, comments, typing, kit_docs,
   kit_git, kit_artifacts) and each attached repo's under repos/<id>/<collection>, mirrored there by a Claude Code
   Web session with `kit_artifact_sync.py boards`. kit_repos/_index lists them: {repos: [{id, name, github}],
   primary: {name}, mirrored_at}. With no attached repo the page is unchanged (no switcher, the db untouched).

   window.__KIT_WRAP_DB__(db) returns a db whose board collections follow the board picked in the header:
     ""      the primary board (the db as it is);
     "<id>"  an attached board (repos/<id>/...);
     "all"   every board at once: queries merge, a write goes to the board that holds the post (comments by
             post_id), a new post to the primary board.
   Live subscriptions follow a switch without a reload: each one is re-opened on the new board.
   The platform's db namespace is never modified: this wraps it.  */
(function () {
  "use strict";
  if (typeof window === "undefined" || window.__KIT_WRAP_DB__) return;
  var COLLS = { posts: 1, comments: 1, typing: 1, kit_docs: 1, kit_git: 1, kit_artifacts: 1 };
  var raw = null, wrapped = null, repos = [], primaryName = "", live = [], owners = {}, listeners = [];
  var active = "";
  try { active = new URLSearchParams(location.search).get("repo") || localStorage.getItem("kit_active_repo") || ""; } catch (e) {}
  window.__KIT_ACTIVE_REPO__ = active;
  var indexReady = null;

  function at(key, rel) { return key ? "repos/" + key + "/" + rel : rel; }
  function sources() { return active === "all" ? [""].concat(repos.map(function (r) { return r.id; })) : [active]; }
  function known(key) { return key === "" || key === "all" && repos.length > 0 || repos.some(function (r) { return r.id === key; }); }

  function loadIndex() {
    if (indexReady) return indexReady;
    indexReady = raw.doc("kit_repos/_index").get().then(function (s) {
      var d = (s && s.exists && s.data()) || {};
      repos = (Array.isArray(d.repos) ? d.repos : []).filter(function (r) { return r && r.id && /^[A-Za-z0-9_.-]+$/.test(r.id) && r.id !== "all"; });
      primaryName = (d.primary && d.primary.name) || "";
    }, function () { repos = []; }).then(function () {
      if (!known(active)) setRepo("");
      listeners.forEach(function (f) { try { f(); } catch (e) {} });
      return repos;
    });
    return indexReady;
  }

  /* a merged QuerySnapshot over several boards */
  function merged(parts, ops) {
    var docs = [], cache = false, pending = false;
    parts.forEach(function (p) {
      if (!p.snap) { cache = true; return; }
      cache = cache || !!(p.snap.metadata && p.snap.metadata.fromCache);
      pending = pending || !!(p.snap.metadata && p.snap.metadata.hasPendingWrites);
      (p.snap.docs || []).forEach(function (d) { docs.push(d); });
    });
    var ob = null; ops.forEach(function (o) { if (o[0] === "orderBy") ob = o; });
    if (ob) docs.sort(function (a, b) {
      var x = (a.data() || {})[ob[1]], y = (b.data() || {})[ob[1]];
      var c = x === y ? 0 : (x === undefined ? -1 : y === undefined ? 1 : x < y ? -1 : 1);
      return ob[2] === "desc" ? -c : c;
    });
    ops.forEach(function (o) { if (o[0] === "limit") docs = docs.slice(0, o[1]); });
    return { docs: docs, size: docs.length, empty: !docs.length, docChanges: function () { return []; },
             metadata: { fromCache: cache, hasPendingWrites: pending } };
  }
  function note(coll, key, snap) {
    if (active !== "all") return;
    (snap.docs || []).forEach(function (d) { owners[coll + "/" + d.id] = key; });
  }
  function apply(ref, ops) { ops.forEach(function (o) { ref = ref[o[0]].apply(ref, o.slice(1)); }); return ref; }

  function query(coll, ops) {
    var api = {
      where: function (f, op, v) { return query(coll, ops.concat([["where", f, op, v]])); },
      orderBy: function (f, dir) { return query(coll, ops.concat([["orderBy", f, dir || "asc"]])); },
      limit: function (n) { return query(coll, ops.concat([["limit", n]])); },
      get: function () {
        return loadIndex().then(function () {
          var src = sources();
          return Promise.all(src.map(function (k) {
            return apply(raw.collection(at(k, coll)), ops).get().then(function (s) { note(coll, k, s); return { snap: s }; }, function () { return { snap: { docs: [] } }; });
          })).then(function (parts) { return src.length === 1 ? parts[0].snap : merged(parts, ops); });
        });
      },
      onSnapshot: function (next, error) {
        var sub = { unsubs: [], closed: false };
        sub.open = function () {
          sub.unsubs.forEach(function (u) { try { u(); } catch (e) {} });
          sub.unsubs = [];
          var gen = sub.gen = (sub.gen || 0) + 1;
          var src = sources(), parts = src.map(function () { return { snap: null }; });
          src.forEach(function (k, i) {
            sub.unsubs.push(apply(raw.collection(at(k, coll)), ops).onSnapshot(function (s) {
              if (sub.closed || sub.gen !== gen) return;
              note(coll, k, s);
              parts[i].snap = s;
              next(src.length === 1 ? s : merged(parts, ops));
            }, function (e) {
              if (src.length === 1) { if (error) error(e); return; }
              parts[i].snap = { docs: [], metadata: { fromCache: false } };   /* one board unreadable: the others still show */
              next(merged(parts, ops));
            }));
          });
        };
        live.push(sub);
        if (active === "all") loadIndex().then(function () { if (!sub.closed) sub.open(); }); else sub.open();
        return function () {
          sub.closed = true;
          sub.unsubs.forEach(function (u) { try { u(); } catch (e) {} });
          live = live.filter(function (x) { return x !== sub; });
        };
      }
    };
    return api;
  }

  /* where a write to coll/id goes in "all": the board that holds it, a comment to its post's board */
  function target(coll, id, data) {
    if (active !== "all") return active;
    if (owners[coll + "/" + id] !== undefined) return owners[coll + "/" + id];
    var pid = data && data.post_id;
    if (pid && owners["posts/" + pid] !== undefined) return owners["posts/" + pid];
    return "";
  }
  function docRef(coll, id) {
    function on(data) { return raw.doc(at(target(coll, id, data), coll + "/" + id)); }
    return {
      id: id, path: coll + "/" + id,
      get: function () {
        if (active !== "all") return raw.doc(at(active, coll + "/" + id)).get();
        return loadIndex().then(function () {
          if (owners[coll + "/" + id] !== undefined) return on().get();
          var src = sources();
          return (function tryAt(i) {
            return raw.doc(at(src[i], coll + "/" + id)).get().then(function (s) {
              if ((s && s.exists) || i === src.length - 1) { if (s && s.exists) owners[coll + "/" + id] = src[i]; return s; }
              return tryAt(i + 1);
            });
          })(0);
        });
      },
      set: function (d, o) { return o === undefined ? on(d).set(d) : on(d).set(d, o); },
      update: function (d) { return on(d).update(d); },
      delete: function () { return on().delete(); },
      acquire: function (o) { return on().acquire(o); },
      collection: function (p) { return raw.doc(at(target(coll, id), coll + "/" + id)).collection(p); },
      onSnapshot: function (next, error) {
        var sub = { unsubs: [], closed: false };
        sub.open = function () {
          sub.unsubs.forEach(function (u) { try { u(); } catch (e) {} });
          sub.unsubs = [on().onSnapshot(function (s) { if (!sub.closed) next(s); }, error)];
        };
        live.push(sub); sub.open();
        return function () { sub.closed = true; sub.unsubs.forEach(function (u) { try { u(); } catch (e) {} }); live = live.filter(function (x) { return x !== sub; }); };
      }
    };
  }
  function collRef(coll) {
    var c = query(coll, []);
    c.path = coll;
    c.doc = function (id) { return docRef(coll, id || raw.collection(coll).doc().id); };
    c.add = function (d) { var r = c.doc(); return r.set(d).then(function () { return r; }); };
    return c;
  }
  function split(path) { var s = String(path || "").split("/").filter(Boolean); return COLLS[s[0]] ? s : null; }

  function setRepo(key) {
    key = key || "";
    if (key === active) return;
    active = key; owners = {};
    window.__KIT_ACTIVE_REPO__ = key;
    try { localStorage.setItem("kit_active_repo", key); } catch (e) {}
    live.slice().forEach(function (s) { if (!s.closed) s.open(); });
    listeners.forEach(function (f) { try { f(); } catch (e) {} });
  }

  window.__KIT_WRAP_DB__ = function (db) {
    if (wrapped) return wrapped;
    if (!db) return db;
    raw = db;
    wrapped = {
      collection: function (p) { var s = split(p); return s && s.length === 1 ? collRef(s[0]) : raw.collection(p); },
      doc: function (p) { var s = split(p); return s && s.length === 2 ? docRef(s[0], s[1]) : raw.doc(p); }
    };
    loadIndex();
    return wrapped;
  };
  window.__KIT_REPOS__ = {
    list: function () { return repos.slice(); },
    primaryName: function () { return primaryName; },
    active: function () { return active; },
    /* the db prefix of the board holding coll/id ("" primary, "repos/<id>/"), for briefs an agent follows */
    prefixOf: function (coll, id) { var k = active === "all" ? (owners[coll + "/" + id] || "") : active; return k ? "repos/" + k + "/" : ""; },
    set: setRepo,
    onChange: function (f) { listeners.push(f); },
    ready: function () { return indexReady || Promise.resolve([]); }
  };

  /* the header switcher, the same as the local server's (kit/board/shim.js initRepoSwitcher) */
  function switcher() {
    if (!repos.length || document.getElementById("kit-repo-wrap")) return;
    var hdr = document.querySelector("header.top"), h1 = hdr && hdr.querySelector("h1");
    if (!h1) return;
    var opts = [{ id: "", name: primaryName || window.__KIT_PROJECT__ || "Board" }]
      .concat(repos.map(function (r) { return { id: r.id, name: r.name || r.id }; }))
      .concat([{ id: "all", name: "All boards" }]);
    if (!document.getElementById("kit-repo-css")) {
      var st = document.createElement("style"); st.id = "kit-repo-css";
      st.textContent =
        ".kit-repo-wrap{position:relative;display:inline-flex;align-items:center;gap:4px;}" +
        ".kit-repo-chev{background:none;border:0;padding:4px;margin-top:2px;color:var(--muted);border-radius:8px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;opacity:.65;transition:opacity .16s ease,background .15s ease;}" +
        ".kit-repo-chev:hover{opacity:1;color:var(--ink);background:var(--sunk);}" +
        ".kit-repo-chev[aria-expanded='true'] svg{transform:rotate(180deg);}" +
        ".kit-repo-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:190px;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow-lg,0 10px 28px rgba(0,0,0,.18));padding:4px;z-index:40;display:flex;flex-direction:column;gap:2px;}" +
        ".kit-repo-menu[hidden]{display:none;}" +
        ".kit-repo-item{background:none;border:0;text-align:left;padding:7px 12px;border-radius:8px;cursor:pointer;font-family:var(--display);font-weight:800;letter-spacing:-0.025em;font-size:1.35rem;line-height:1.2;color:var(--ink);white-space:nowrap;}" +
        ".kit-repo-item:hover{background:var(--sunk);}";
      document.head.appendChild(st);
    }
    var wrap = document.createElement("span"); wrap.id = "kit-repo-wrap"; wrap.className = "kit-repo-wrap";
    h1.parentNode.insertBefore(wrap, h1); wrap.appendChild(h1);
    var btn = document.createElement("button");
    btn.id = "kit-repo-chev"; btn.className = "kit-repo-chev"; btn.type = "button";
    btn.setAttribute("aria-label", "Switch board"); btn.title = "Switch board";
    btn.setAttribute("aria-haspopup", "menu"); btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
    wrap.appendChild(btn);
    var menu = document.createElement("div");
    menu.id = "kit-repo-menu"; menu.className = "kit-repo-menu"; menu.setAttribute("role", "menu"); menu.hidden = true;
    wrap.appendChild(menu);
    function label(r) { var n = String(r.name || "Board"); return /boards?$/i.test(n) ? n : n + " board"; }
    function title() {
      var cur = opts.filter(function (r) { return r.id === active; })[0];
      if (cur && document.activeElement !== h1) { h1.textContent = label(cur); document.title = label(cur); window.__KIT_REPO_TITLE__ = label(cur); }
    }
    function close() { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (!menu.hidden) { close(); return; }
      menu.textContent = "";
      opts.forEach(function (r) {
        if (r.id === active) return;
        var it = document.createElement("button");
        it.type = "button"; it.className = "kit-repo-item"; it.setAttribute("role", "menuitem"); it.textContent = label(r);
        it.addEventListener("click", function () { close(); setRepo(r.id); title(); });
        menu.appendChild(it);
      });
      menu.hidden = false; btn.setAttribute("aria-expanded", "true");
    });
    h1.addEventListener("focus", close);
    document.addEventListener("click", function (e) { if (!wrap.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    if (active) title();
  }
  listeners.push(function () {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", switcher); else switcher();
  });
})();
