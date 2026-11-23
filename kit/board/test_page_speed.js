// CL23: opening a thread must never freeze the page. A thread whose replies nest deep (each reply answers the one
// before) was laid out in nested grids, and each level multiplied the cost: 6 levels took 6 s per layout on his board.
// Opens such a thread (in the orchestrator's window and as a post's comments) and fails when the page stays blocked
// for over a second.   node kit/board/test_page_speed.js   (no browser: SKIP; KIT_PAGE=<html> for another copy)
const { launch } = require('../claude/page_driver');
const fs = require('fs'), path = require('path');

const DEPTH = 7;
const STUB = `(function () {
  const now = Date.now(), at = (m) => new Date(now - m * 60000).toISOString();
  const text = "## What I checked\\n\\nA reply with a few lines of text, **bold** words and \`code\`, as agents write them.\\n\\n- one\\n- two";
  const comments = {};
  let prev = "";
  for (const post of ["_orchestrator", "p1"]) {
    prev = "";
    for (let i = 0; i < ${DEPTH}; i++) {
      const id = post.replace(/_/g, "") + "-c" + i, owner = i % 2 === 0;
      comments[id] = { post_id: post, from: owner ? "owner" : "orchestrator", text: (owner ? "Question " : text + " ") + i,
        created_at: at(200 - i), reply_to: prev || undefined,
        delivered: true, read: true };
      prev = id;
    }
  }
  const store = { posts: {
      "_orchestrator": { title: "Ask the orchestrator", column: "info", created_at: at(300) },
      "p1": { title: "A post with a deep thread", column: "info", created_at: at(250), body: "Body." } },
    comments, typing: {} };
  window.__writes = [];
  const subs = [];
  const snap = (c) => ({ metadata: { fromCache: false }, docs: Object.entries(store[c] || {}).map(([id, d]) => ({ id, data: () => d })) });
  const q = (c) => ({ orderBy: () => q(c), where: () => q(c), limit: () => q(c), get: async () => snap(c),
    onSnapshot: (f) => { subs.push([c, f]); setTimeout(() => f(snap(c)), 0); return () => {}; } });
  const doc = (p) => ({ id: p.split("/").pop(), get: async () => ({ exists: false, data: () => undefined }),
    update: async (o) => window.__writes.push([p, o]), set: async (o) => window.__writes.push([p, o]),
    onSnapshot: (f) => { setTimeout(() => f({ exists: false, metadata: { fromCache: false } }), 0); return () => {}; } });
  const db = { collection: (c) => Object.assign(q(c), { doc: (id) => doc(c + "/" + id), add: async () => ({ id: "n" }) }), doc };
  window.confirm = () => true;
  window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { isOwner: async () => true, id: async () => "u1" } : null };
})();`;

(async () => {
  let html = fs.readFileSync(process.env.KIT_PAGE || path.join(__dirname, 'board.template.html'), 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  html = html.replace('<script>', '<script>' + STUB + '</script><script>');
  const p = await launch();
  if (!p) { console.log('SKIP no Playwright and no Chrome/Edge/Brave/Chromium found (set KIT_BROWSER=<path>)'); process.exit(process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
  let fails = 0; const check = (n, ok, more) => { console.log((ok ? 'ok   ' : 'FAIL ') + n + (ok || !more ? '' : '\n       ' + more)); fails += !ok; };
  // the longest the page stays blocked over `ms` after `act` runs (a frozen page answers nothing until it is done)
  async function blocked(act, ms) {
    await p.evaluate(`(${act.toString()})()`);
    const t0 = Date.now(); let worst = 0;
    while (Date.now() - t0 < ms) {
      const t = Date.now();
      const alive = await Promise.race([p.evaluate('1').then(() => true), new Promise((r) => setTimeout(() => r(false), 5000))]);
      worst = Math.max(worst, Date.now() - t);
      if (!alive) return 5000;  // still frozen after 5 s: no need to wait for the end of it
      await p.wait(50);
    }
    return worst;
  }
  for (const [label, w, h] of [['desktop', 1300, 900], ['phone', 390, 844]]) {
    if (p.viewport) await p.viewport(w, h);
    await p.open(html); await p.wait(900);
    await p.evaluate(() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) { /* none */ } });
    await p.open(html); await p.wait(900);
    await p.evaluate(() => document.getElementById('ask').click()); await p.wait(500);
    let ms = await blocked(() => setTimeout(() => { const d = document.querySelector('.disc'); if (d) d.click(); }, 0), 2500);
    if (ms >= 5000) { check(label + `: the orchestrator's first thread (${DEPTH} replies deep) opens without freezing`, false, 'the page was still frozen after 5 s'); break; }
    const shown = await p.evaluate((n) => document.querySelectorAll('#p-comments .cmt').length >= n, DEPTH);
    check(label + `: the orchestrator's first thread (${DEPTH} replies deep) opens without freezing (blocked ${ms} ms, under 1000)`, ms < 1000 && shown, shown ? '' : 'its replies are not all drawn');
    await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); }); await p.wait(300);
    await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); document.querySelectorAll('.kit-win-close').forEach((x) => x.click()); }); await p.wait(300);
    ms = await blocked(() => setTimeout(() => { const c = [...document.querySelectorAll('#lanes .card')].find((x) => /deep thread/.test(x.textContent)); if (c) (c.querySelector('.card-open') || c).click(); }, 0), 2500);
    check(label + `: a post whose comments nest ${DEPTH} deep opens without freezing (blocked ${ms >= 5000 ? 'over 5000' : ms} ms, under 1000)`, ms < 1000);
    if (ms >= 5000) break;
  }
  check('no page error', p.errors.length === 0, p.errors.slice(0, 3).join(' | '));
  console.log(fails + ' failed');
  if (!fails) await p.close();  // a frozen page cannot be closed politely: the process ends it
  process.exit(fails ? 1 : 0);
})();
