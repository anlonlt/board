// CL21: a post he has read stays read after a reload, on the primary board and on an attached one, wherever he read it.
// The built Artifact page in Chromium with a stub window.claude whose db keeps what the page writes; each "reload" is a
// fresh browser (no localStorage) opened on the db the previous one left, as his phone after his computer.
//   KIT_PAGE=<built page> node kit/claude/test_seen_marks.js
// Checks (each "read" = on screen for more than a second, BD3; nothing is clicked open unless said):
//   1. primary board: a post opened in its sheet is not new after a reload; an unread post stays new (control);
//   2. attached board: the same, and its post "P-a" read there leaves the primary board's own "P-a" new (one mark
//      per board, kept as repos/<board>/<id>; both "P-a" have the same text, as the demo posts every install adds);
//   3. attached board, Feed view or a post window: a post read there is not new after a reload;
//   4. a mark written before CL21 under the bare id still holds for the primary board, and for an attached post
//      when it was made on that very text.
const { launch, syntaxCheck } = require('./page_driver');
const fs = require('fs');

function stub(seed, repo, view) {
  return `(function () {
  const now = "2026-11-23T01:00:00.000Z";
  try { localStorage.setItem("kit_active_repo", ${JSON.stringify(repo)}); ${view ? `localStorage.setItem("board-view", ${JSON.stringify(view)});` : ""} } catch (e) {}
  const post = (t) => ({ title: t, column: "info", created_at: now, body: t + " body" });
  const store = {
    posts: { "P-a": post("Same A"), "P-b": post("Primary B"), "P-old": post("Primary old"), "_orchestrator": { title: "Ask", column: "info", created_at: now } },
    comments: {}, typing: {}, kit_users: {},
    "kit_repos": { _index: { repos: [{ id: "board", name: "Kit" }], primary: { name: "Harmonize" } } },
    "repos/board/posts": { "P-a": post("Same A"), "cl1-x": post("Kit X"), "cl2-y": post("Kit Y") },
    "repos/board/comments": {}, "repos/board/typing": {}
  };
  const seed = ${JSON.stringify(seed || {})};
  Object.keys(seed).forEach(function (c) { store[c] = Object.assign(store[c] || {}, seed[c]); });
  window.__store = store; window.__writes = [];
  const subs = [];
  const docsOf = (c, ws) => Object.entries(store[c] || {}).filter(([id, d]) => (ws || []).every(([f, op, v]) => op === "==" ? d[f] === v : true));
  const snap = (c, ws) => ({ metadata: { fromCache: false, hasPendingWrites: false }, docs: docsOf(c, ws).map(([id, d]) => ({ id, data: () => d, metadata: { fromCache: false } })), docChanges: () => [] });
  const dsnap = (c, id) => { const d = (store[c] || {})[id]; return { id, exists: !!d, data: () => d, metadata: { fromCache: false, hasPendingWrites: false } }; };
  const fire = () => subs.forEach((s) => s());
  const merge = (a, b) => { const o = Object.assign({}, a); Object.keys(b).forEach((k) => { o[k] = b[k] && typeof b[k] === "object" && !Array.isArray(b[k]) && o[k] && typeof o[k] === "object" ? merge(o[k], b[k]) : b[k]; }); return o; };
  const q = (c, ws) => ({ orderBy: () => q(c, ws), where: (f, op, v) => q(c, (ws || []).concat([[f, op, v]])), limit: () => q(c, ws),
    get: async () => snap(c, ws),
    onSnapshot: (f) => { const s = () => f(snap(c, ws)); subs.push(s); setTimeout(s, 0); return () => {}; } });
  let n = 0;
  const docRef = (path) => { const parts = path.split("/"); const id = parts.pop(); const c = parts.join("/");
    return { id, path,
      get: async () => dsnap(c, id),
      set: async (o) => { window.__writes.push(["set", path, o]); (store[c] = store[c] || {})[id] = o; fire(); },
      update: async (o) => { if (!(store[c] || {})[id]) throw Object.assign(new Error("missing"), { code: "invalid_argument" });
        window.__writes.push(["update", path, o]); store[c][id] = merge(store[c][id], o); fire(); },
      delete: async () => { if (store[c]) delete store[c][id]; fire(); },
      onSnapshot: (f) => { const s = () => f(dsnap(c, id)); subs.push(s); setTimeout(s, 0); return () => {}; } }; };
  const coll = (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))),
    add: async (o) => { const id = "new" + (++n); (store[c] = store[c] || {})[id] = o; fire(); return { id }; } });
  const caps = { db: { collection: coll, doc: docRef },
    user: { isOwner: async () => true, id: async () => "u1", me: async () => ({ id: "u1", name: "Owner" }), canEdit: async () => true } };
  window.claude = { use: async (k) => caps[k] || null };
})();`;
}

async function visit(html, { seed, repo, view, act }) {
  const p = await launch();
  if (!p) return null;
  await p.open(html.replace('<script>', '<script>' + stub(seed, repo, view) + '</script><script>'));
  await p.wait(1800);
  if (act) { await act(p); await p.wait(2600); }
  const out = await p.evaluate(() => {
    const isNew = {};
    document.querySelectorAll('.card[data-id], .ffeed-card[data-id]').forEach((c) => { isNew[c.dataset.id] = !!c.querySelector('.newdot'); });
    const sh = document.getElementById('sheet'); if (sh && !sh.hidden) isNew.__sheetOpen = true;
    return { isNew, seen: (((window.__store.data || {})["users/u1"] || {})["board-seen"]) || (window.__store["data/users/u1"] || {})["board-seen"] || null };
  });
  out.errors = p.errors.filter((e) => !/net::ERR_|Failed to load resource/.test(e));
  await p.close();
  return out;
}
const openCard = (id) => async (p) => {
  await p.evaluate((id) => { const t = document.querySelector('.tab[data-col="info"]'); if (t) t.click(); }, id);
  await p.wait(200);
  await p.evaluate((id) => { const c = document.querySelector('.card[data-id="' + id + '"] .card-open'); if (c) c.click(); }, id);
};
const seedOf = (seen) => (seen ? { "data/users/u1": { "board-seen": seen } } : {});

(async () => {
  const html = fs.readFileSync(process.env.KIT_PAGE || '.kit/board.html', 'utf8');
  const syn = syntaxCheck(html);
  const res = { checks: [], errors: syn.errors.slice() };
  const check = (name, ok, detail) => res.checks.push({ name, ok: !!ok, detail });
  const probe = await launch();
  if (!probe) {
    console.log(JSON.stringify({ skipped: 'no browser', errors: syn.errors }, null, 1));
    process.exit(syn.errors.length || process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0);
  }
  await probe.close();

  // 1. primary board, sheet
  const a1 = await visit(html, { repo: "", act: openCard("P-a") });
  const a2 = await visit(html, { repo: "", seed: seedOf(a1.seen) });
  check("primary: read post not new after reload", a2.isNew["P-a"] === false, a2.isNew);
  check("primary: unread post stays new (control)", a2.isNew["P-b"] === true, a2.isNew);

  // 2. attached board, sheet; the same id on the primary board stays new
  const b1 = await visit(html, { repo: "board", act: openCard("cl1-x") });
  const b2 = await visit(html, { repo: "board", seed: seedOf(b1.seen) });
  check("attached: read post not new after reload", b2.isNew["cl1-x"] === false, b2.isNew);
  check("attached: unread post stays new (control)", b2.isNew["cl2-y"] === true, b2.isNew);
  const c1 = await visit(html, { repo: "board", act: openCard("P-a") });
  const c2 = await visit(html, { repo: "", seed: seedOf(c1.seen) });
  check("attached P-a read leaves primary P-a new (one mark per board)", c2.isNew["P-a"] === true, { keys: Object.keys((c1.seen || {}).posts || {}), primary: c2.isNew });

  // 3. attached board, read in the Feed view (never opened)
  const d1 = await visit(html, { repo: "board", view: "feed", act: async (p) => { await p.wait(200); } });
  const d2 = await visit(html, { repo: "board", seed: seedOf(d1.seen) });
  check("attached: post read in the Feed is not new after reload", d2.isNew["cl1-x"] === false, { keys: Object.keys((d1.seen || {}).posts || {}), after: d2.isNew });

  // 3b. attached board, read in a post window (shift-click), never opened in the sheet
  const w1 = await visit(html, { repo: "board", act: async (p) => { await p.evaluate(() => window._kitOpenPostWindow && window._kitOpenPostWindow("cl2-y")); } });
  const w2 = await visit(html, { repo: "board", seed: seedOf(w1.seen) });
  check("attached: post read in a window is not new after reload", w2.isNew["cl2-y"] === false, { keys: Object.keys((w1.seen || {}).posts || {}), after: w2.isNew });

  // 3c. a bare-id mark the old page wrote for an attached post (sig of that very text) still holds on that board
  const sigOf = (q) => { const t = q.title + "\u0001" + q.body + "\u0001" + q.column + "\u0001" + "" + "\u0001" + ""; let h = 5381; for (let i = 0; i < t.length; i++) h = ((h * 33) ^ t.charCodeAt(i)) >>> 0; return h.toString(36) + "." + t.length.toString(36); };
  const f = await visit(html, { repo: "board", seed: seedOf({ posts: { "cl2-y": { at: "2026-11-23T02:00:00.000Z", sig: sigOf({ title: "Kit Y", body: "Kit Y body", column: "info" }), v: [] } } }) });
  check("attached: an old bare-id mark made on the same text is honoured", f.isNew["cl2-y"] === false && f.isNew["cl1-x"] === true, f.isNew);

  // 4. a mark from before CL21 (bare id) still holds on the primary board
  const e = await visit(html, { repo: "", seed: seedOf({ posts: { "P-old": { at: "2026-11-23T02:00:00.000Z", sig: "", v: [] } } }) });
  check("primary: an old bare-id mark is honoured", e.isNew["P-old"] === false, e.isNew);

  [a1, a2, b1, b2, c1, c2, d1, d2, w1, w2, f, e].forEach((x) => { res.errors = res.errors.concat(x.errors); });
  const failed = res.checks.filter((c) => !c.ok);
  console.log(JSON.stringify(res, null, 1));
  console.log((res.checks.length - failed.length) + " of " + res.checks.length + " seen checks pass");
  process.exit(failed.length || res.errors.length ? 1 : 0);
})();
