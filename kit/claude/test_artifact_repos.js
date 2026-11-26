// Cross-repo boards in the built Artifact page (artifact_repos.js), with a stub window.claude db holding a primary
// board and one attached board under repos/songs/. Checks: the header switcher appears, a switch re-opens the live
// subscriptions on the attached board (no reload), "All boards" merges both, and a comment written in "All boards"
// lands on the board that holds its post.   KIT_PAGE=<built page> node kit/claude/test_artifact_repos.js
const { launch, syntaxCheck } = require('./page_driver');
const fs = require('fs');

const STUB = `(function () {
  const t = (h) => "2026-11-2" + h + "T10:00:00Z";
  const store = {
    "posts": { "P-main": { title: "Main post", column: "info", created_at: t(1) } },
    "comments": {},
    "repos/songs/posts": { "P-song": { title: "Song post", column: "info", created_at: t(2), images: ["docs/song.png"] },
                           "P-run": { title: "A running step", column: "info", status: "in progress", created_at: t(3) } },
    /* CL41: a web part's live line, sent to the Artifact by its push: read on the board that holds the step */
    "repos/songs/activity": { "P-run": { at: new Date().toISOString().slice(0, 19) + "Z", actions: 4, what: "Running the tests", state: "working" } },
    "activity": { "P-run": { at: new Date().toISOString().slice(0, 19) + "Z", actions: 1, what: "Wrong board", state: "working" } },
    "kit_docs": { "_index": { assets: { "docs/main.png": "/_blob/" + "b".repeat(32) } } },   /* the primary has its own map, as Harmonize does */
    "repos/songs/kit_docs": { "_index": { assets: { "docs/song.png": "/_blob/" + "a".repeat(32) } } },
    "repos/songs/comments": {},
    "kit_repos": { "_index": { repos: [{ id: "songs", name: "Songs", github: "o/songs" }], primary: { name: "Harmonize" } } }
  };
  window.__writes = []; window.__subs = 0;
  const subs = [];
  const snap = (c) => ({ metadata: { fromCache: false, hasPendingWrites: false },
    docs: Object.entries(store[c] || {}).map(([id, d]) => ({ id, exists: true, data: () => d, metadata: {} })) });
  const fire = () => subs.forEach((s) => s.live && s.f(snap(s.c)));
  const q = (c) => ({ orderBy: () => q(c), where: () => q(c), limit: () => q(c), get: async () => snap(c),
    onSnapshot: (f) => { const s = { c, f, live: true }; subs.push(s); window.__subs++; setTimeout(() => s.live && f(snap(c)), 0); return () => { s.live = false; }; } });
  let n = 0;
  const docRef = (path) => { const p = path.split("/"); const id = p.pop(); const c = p.join("/");
    return { id, path,
      get: async () => { const d = (store[c] || {})[id]; return { id, exists: !!d, data: () => d, metadata: {} }; },
      set: async (o) => { window.__writes.push(["set", path, o]); (store[c] = store[c] || {})[id] = o; fire(); },
      update: async (o) => { window.__writes.push(["update", path, o]); (store[c] = store[c] || {})[id] = Object.assign({}, store[c][id], o); fire(); },
      delete: async () => { window.__writes.push(["delete", path]); },
      onSnapshot: (f) => { setTimeout(() => f({ id, exists: false, data: () => undefined, metadata: { fromCache: false } }), 0); return () => {}; } }; };
  const db = { collection: (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))) }), doc: docRef };
  window.__sent = [];
  const comments = { sendToClaude: async (x) => { window.__sent.push(x.text); return { threadId: "t1" }; },
                     anchorFor: async () => ({}), canSendToClaude: async () => "available" };
  const caps = { db, comments, user: { isOwner: async () => true, id: async () => "u1", me: async () => ({ id: "u1", name: "Owner" }) } };
  window.claude = { use: async (k) => caps[k] || null };
})();`;

(async () => {
  const html = fs.readFileSync(process.env.KIT_PAGE || '.kit/board.html', 'utf8').replace('<script>', '<script>' + STUB + '</script><script>');
  const syn = syntaxCheck(html);
  const p = await launch();
  if (!p) { console.log(JSON.stringify({ skipped: 'no browser', errors: syn.errors })); process.exit(syn.errors.length || process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
  const titles = () => p.evaluate(() => [...document.querySelectorAll('.card')].map(c => (c.textContent.match(/(Main|Song) post/) || [''])[0]).filter(Boolean).sort().join(','));
  const pick = (label) => p.evaluate((l) => { document.getElementById('kit-repo-chev').click(); const b = [...document.querySelectorAll('.kit-repo-item')].find(x => x.textContent === l); if (b) b.click(); return !!b; }, label);
  const res = { driver: p.kind, checks: {} };
  await p.evaluate(() => { try { localStorage.removeItem('kit_active_repo'); } catch (e) {} });
  await p.open(html);
  await p.wait(1500);
  res.checks.switcher = await p.evaluate(() => !!document.getElementById('kit-repo-chev'));
  res.checks.primaryOnly = (await titles()) === 'Main post';
  res.checks.pickedSongs = await pick('Songs board');
  await p.wait(500);
  res.checks.songsOnly = (await titles()) === 'Song post';
  res.live = await p.evaluate(() => [...document.querySelectorAll('.kit-now')].filter(e => !e.hidden).map(e => e.textContent));
  res.checks.liveLineFromItsBoard = res.live.some(t => /Running the tests · 4 actions/.test(t)) && !res.live.some(t => /Wrong board/.test(t));
  // a comment on the attached board's post names its board for the clerk: [repo songs] [post P-song]
  await p.evaluate(() => { const c = [...document.querySelectorAll('.card')].find(x => /Song post/.test(x.textContent)); (c.querySelector('.card-open') || c).click(); });
  await p.wait(400);
  await p.evaluate(() => { const t = document.getElementById('c-text'); t.value = 'hello songs'; document.getElementById('composer').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true })); });
  await p.wait(500);
  res.sent = await p.evaluate(() => window.__sent.slice());
  res.checks.sentWithRepo = await p.evaluate(() => window.__sent.some(t => t.indexOf('[repo songs] [post P-song] ') === 0 && /hello songs/.test(t)));
  res.checks.savedOnItsBoard = await p.evaluate(() => window.__writes.some(w => w[0] === 'set' && /^repos\/songs\/comments\//.test(w[1]) && w[2].text === 'hello songs'));
  await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); });
  await p.wait(300);
  res.checks.title = await p.evaluate(() => document.querySelector('header.top h1').textContent);
  res.checks.pickedAll = await pick('All boards');
  await p.wait(500);
  res.checks.allMerged = (await titles()) === 'Main post,Song post';
  // CL10 (his 02:22): under "All boards" a picture of the attached board's post resolves through ITS index
  await p.evaluate(() => { const c = [...document.querySelectorAll('.card')].find(x => /Song post/.test(x.textContent)); (c.querySelector('.card-open') || c).click(); });
  await p.wait(800);
  res.checks.attachedPictureInAll = await p.evaluate(() => [...document.querySelectorAll('#p-imgs img')].some(i => (i.getAttribute('src') || '').indexOf('/_blob/' + 'a'.repeat(32)) === 0));
  await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); });
  await p.wait(300);
  // a comment on the attached board's post, written through the page's wrapped db while in "All boards"
  await p.evaluate(async () => { const w = window.__KIT_WRAP_DB__(); const r = w.collection('comments').doc(); await r.set({ post_id: 'P-song', text: 'hi' }); });
  await p.wait(200);
  res.checks.routedComment = await p.evaluate(() => window.__writes.some(w => w[0] === 'set' && /^repos\/songs\/comments\//.test(w[1])));
  await pick('Harmonize board');
  await p.wait(500);
  res.checks.backToPrimary = (await titles()) === 'Main post';
  res.errors = syn.errors.concat(p.errors.filter(e => !/net::ERR_|Failed to load resource/.test(e)));
  const failed = Object.entries(res.checks).filter(([k, v]) => v === false).map(([k]) => k);
  res.failed = failed;
  console.log(JSON.stringify(res, null, 1));
  await p.close();
  process.exit(res.errors.length || failed.length ? 1 : 0);
})();
