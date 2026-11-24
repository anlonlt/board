// CL35: a write made from OUTSIDE the page (an agent's ArtifactData batch) shows on an open page within seconds.
// The built Artifact page in Chromium, a stub window.claude db holding a primary board and an attached one under
// repos/board/, the page open on a post of the attached board (the owner's case on 2026-11-24 01:24, CL34 open).
// Three outside writes: (1) the stream tells the page's subscriptions, as the platform's realtime stream does;
// (2) the stream stays silent (a write it never delivers): only the page's own re-read can show it;
// (3) the comments listener first dies with a terminal `unavailable` (the host stopped answering, e.g. a phone that
// slept): only a fresh subscription shows the next write. The page re-reads every 3 s here (__KIT_LIVE_REFRESH_MS__;
// 15 s in use).                                KIT_PAGE=<built page> node kit/claude/test_artifact_live.js
const { launch, syntaxCheck } = require('./page_driver');
const fs = require('fs');

// KIT_LIVE_DATA=<dir> (e.g. .kit/run/artifact/base after `kit.py artifact load`): the attached board holds the real
// posts and comments in <dir>/posts and <dir>/comments, and the O54 reply of 01:24 (o54-docs-in-posts-0130) is taken
// out, then written back from outside with the stream silent: his exact case on his data.
const path = require('path');
function realData(dir) {
  const read = (sub) => { const o = {}; for (const f of fs.readdirSync(path.join(dir, sub))) { if (!f.endsWith('.json')) continue;
    const d = JSON.parse(fs.readFileSync(path.join(dir, sub, f), 'utf8')); o[f.slice(0, -5)] = d.data || d; } return o; };
  const posts = read('posts'), comments = read('comments'), o54 = comments['o54-docs-in-posts-0130'];
  delete comments['o54-docs-in-posts-0130'];
  if (o54) delete o54.arrived_at;
  return { posts, comments, o54 };
}
const REAL = process.env.KIT_LIVE_DATA ? realData(process.env.KIT_LIVE_DATA) : null;

const STUB = `(function () {
  const REAL = ${JSON.stringify(REAL).replace(/</g, '\\u003c')};
  window.__KIT_LIVE_REFRESH_MS__ = 3000;
  const iso = (ms) => new Date(Date.now() + ms).toISOString();
  const store = {
    "posts": { "P-main": { title: "Main post", column: "info", created_at: iso(-86400000) } },
    "comments": {},
    "repos/board/posts": REAL ? REAL.posts : { "cl34": { title: "CL34 memory flow", column: "done", created_at: iso(-3600000), body: "A design." } },
    "repos/board/comments": REAL ? REAL.comments : { "parent1": { post_id: "cl34", from: "owner", text: "Why is it in docs?", created_at: iso(-600000) } },
    "kit_repos": { "_index": { repos: [{ id: "board", name: "Board", github: "anlonlt/board" }], primary: { name: "Harmonize" } } }
  };
  window.__real = REAL; window.__store = store; window.__reads = 0; window.__writes = [];
  const subs = [];
  const snap = (c) => ({ metadata: { fromCache: false, hasPendingWrites: false },
    docs: Object.entries(store[c] || {}).map(([id, d]) => ({ id, exists: true, data: () => d, metadata: {} })) });
  const fire = (c) => subs.forEach((s) => s.live && (!c || s.c === c) && s.f(snap(s.c)));
  const q = (c) => ({ orderBy: () => q(c), where: () => q(c), limit: () => q(c), get: async () => { window.__reads++; return snap(c); },
    onSnapshot: (f, e) => { const s = { c, f, e, live: true }; subs.push(s); setTimeout(() => s.live && f(snap(c)), 0); return () => { s.live = false; }; } });
  /* the host stops answering: each listener on c gets one terminal error and is dead */
  window.__kill = (c) => subs.forEach((s) => { if (s.live && s.c === c) { s.live = false; if (s.e) s.e({ code: "unavailable", message: "host gone" }); } });
  let n = 0;
  const docRef = (path) => { const p = path.split("/"); const id = p.pop(); const c = p.join("/");
    return { id, path,
      get: async () => { window.__reads++; const d = (store[c] || {})[id]; return { id, exists: !!d, data: () => d, metadata: { fromCache: false } }; },
      set: async (o) => { window.__writes.push(["set", path]); (store[c] = store[c] || {})[id] = o; fire(c); },
      update: async (o) => { window.__writes.push(["update", path]); (store[c] = store[c] || {})[id] = Object.assign({}, store[c][id], o); fire(c); },
      delete: async () => {},
      onSnapshot: (f) => { setTimeout(() => f({ id, exists: false, data: () => undefined, metadata: { fromCache: false } }), 0); return () => {}; } }; };
  const db = { collection: (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))) }), doc: docRef };
  /* an agent's ArtifactData write: not through the page */
  window.__outside = (c, id, d, silent) => { (store[c] = store[c] || {})[id] = d; if (!silent) fire(c); };
  const caps = { db, user: { isOwner: async () => true, id: async () => "u1", me: async () => ({ id: "u1", name: "Owner" }) } };
  window.claude = { use: async (k) => caps[k] || null };
})();`;

(async () => {
  const html = fs.readFileSync(process.env.KIT_PAGE || '.kit/board.html', 'utf8').replace('<script>', '<script>' + STUB + '</script><script>');
  const syn = syntaxCheck(html);
  const p = await launch();
  if (!p) { console.log(JSON.stringify({ skipped: 'no browser', errors: syn.errors })); process.exit(syn.errors.length || process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
  const res = { driver: p.kind, checks: {}, ms: {} };
  const shown = (txt) => p.evaluate((t) => (document.getElementById('p-comments') || document.body).textContent.indexOf(t) >= 0, txt);
  await p.open(html);
  await p.wait(1500);
  // the attached board, picked in the header switcher as he does
  res.checks.pickedBoard = await p.evaluate(() => { const c = document.getElementById('kit-repo-chev'); if (!c) return false; c.click();
    const b = [...document.querySelectorAll('.kit-repo-item')].find(x => x.textContent === 'Board'); if (b) b.click(); return !!b; });
  await p.wait(600);
  const want = REAL ? /CL34/ : /CL34 memory flow/;
  await p.evaluate((w) => { const re = new RegExp(w); const c = [...document.querySelectorAll('.card')].find(x => re.test(x.textContent)); if (c) (c.querySelector('.card-open') || c).click(); }, want.source);
  await p.wait(1200);
  res.checks.postOpen = await shown(REAL ? 'seeing quite a bit of this' : 'Why is it in docs?');
  if (REAL) {
    // his case: O54's real reply, written from outside while the stream says nothing
    await p.evaluate(() => window.__outside('repos/board/comments', 'o54-docs-in-posts-0130', window.__real.o54, true));
    const t0 = Date.now(); let ok = false;
    while (Date.now() - t0 < 8000 && !(ok = await shown('the kit tells them to'))) await p.wait(250);
    res.ms.o54 = ok ? Date.now() - t0 : -1;
    res.checks.o54ShownWithin8s = ok;
    res.data = { posts: Object.keys(REAL.posts).length, comments: Object.keys(REAL.comments).length + 1 };
    res.errors = syn.errors.concat(p.errors.filter(e => !/net::ERR_|Failed to load resource/.test(e)));
    res.failed = Object.entries(res.checks).filter(([k, v]) => v === false).map(([k]) => k);
    console.log(JSON.stringify(res, null, 1));
    await p.close();
    process.exit(res.errors.length || res.failed.length ? 1 : 0);
  }
  const until = async (txt, limit) => { const t0 = Date.now(); while (Date.now() - t0 < limit) { if (await shown(txt)) return Date.now() - t0; await p.wait(250); } return -1; };
  // (1) O54's reply as written at 01:24: a reply, from the orchestrator, stamped 5 min ahead; the stream delivers it
  await p.evaluate(() => window.__outside('repos/board/comments', 'o54-reply', { post_id: 'cl34', from: 'orchestrator', reply_to: 'parent1',
    text: 'O54: the kit tells them to.', created_at: new Date(Date.now() + 300000).toISOString() }));
  res.ms.streamed = await until('O54: the kit tells them to.', 2000);
  res.checks.streamedShown = res.ms.streamed >= 0;
  // (2) the stream never tells the page: the re-read shows it within seconds
  await p.evaluate(() => window.__outside('repos/board/comments', 'agent-silent', { post_id: 'cl34', from: 'harmonize-cl34', text: 'A write the stream never delivered.', created_at: new Date().toISOString() }, true));
  res.ms.silent = await until('A write the stream never delivered.', 8000);
  res.checks.silentShownWithin8s = res.ms.silent >= 0;
  // (3) the listener dies, then a write the stream delivers to live listeners only
  await p.evaluate(() => window.__kill('repos/board/comments'));
  await p.wait(300);
  await p.evaluate(() => window.__outside('repos/board/comments', 'agent-after-kill', { post_id: 'cl34', from: 'harmonize-cl34', text: 'A write after the listener died.', created_at: new Date().toISOString() }));
  res.ms.afterKill = await until('A write after the listener died.', 8000);
  res.checks.afterKillShownWithin8s = res.ms.afterKill >= 0;
  res.errors = syn.errors.concat(p.errors.filter(e => !/net::ERR_|Failed to load resource/.test(e)));
  res.failed = Object.entries(res.checks).filter(([k, v]) => v === false).map(([k]) => k);
  console.log(JSON.stringify(res, null, 1));
  await p.close();
  process.exit(res.errors.length || res.failed.length ? 1 : 0);
})();
