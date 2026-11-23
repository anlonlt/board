// CL12: the board page in a real browser (kit/claude/page_driver.js) on long, unbroken text, at a phone and a desktop
// width: nothing runs off its box or off the screen; a reply to the thread of a Like is drawn and its badge clears once
// seen; a train's tasklist lists its done steps. node kit/board/test_page_layout.js  (no browser: SKIP)
// KIT_PAGE=<html> runs it on another copy of the page (an installed .kit/board.html).
const { launch } = require('../claude/page_driver');
const fs = require('fs'), path = require('path');

const LONG = 'Averyveryverylongunbrokenwordthatnobrowsercanbreakbyitself'.repeat(3);
const WORDS = 'A section header written as a long sentence, the way agents write them on this board when they explain things';
const STUB = `(function () {
  const now = Date.now(), at = (m) => new Date(now - m * 60000).toISOString();
  const LONG = ${JSON.stringify(LONG)}, WORDS = ${JSON.stringify(WORDS)};
  const body = "Lead line " + LONG + "\\n\\n## " + WORDS + " " + LONG + "\\n\\nText under it.\\n\\n## " + WORDS + "\\n\\nMore text.\\n\\n### " + LONG + "\\n\\n- a list item " + LONG + "\\n\\n\`" + LONG + "\`\\n";
  const store = {
    posts: {
      "_orchestrator": { title: "Ask the orchestrator", column: "info", created_at: at(300), discussions: { "w1": "Welcome " + LONG } },
      "_trains": { column: "info", created_at: at(300), rows: [] },
      "cl1-done": { title: "CL1 done step " + LONG, part: "CL1", train: "cli", column: "done", status: "complete", created_at: at(200), done_at: at(190), verdict: "Done " + LONG, body: body },
      "cl2-done": { title: "CL2 second done step", part: "CL2", parent: "CL1", train: "cli", column: "done", status: "complete", created_at: at(180), done_at: at(170), body: "done" },
      "cl3-open": { title: WORDS + " " + LONG, part: "CL3", parent: "CL2", train: "cli", column: "info", status: "in progress", created_at: at(60), body: body },
      "other-1": { title: LONG, column: "question", created_at: at(30), body: body, options: [LONG, "No"] }
    },
    _dashboard: null,
    comments: {
      w1: { post_id: "_orchestrator", from: "orchestrator", text: "# Welcome " + LONG + "\\n\\n## " + WORDS + " " + LONG + "\\n\\nHello.", created_at: at(120) },
      like1: { post_id: "_orchestrator", from: "owner", text: "\\uD83D\\uDC4D", like: true, comment_id: "w1", thread_id: "thr_like", created_at: at(110), delivered: true },
      rlike: { post_id: "_orchestrator", from: "orchestrator", text: "Answer to your Like: " + LONG, reply_to: "thr_like", created_at: at(100) },
      o1: { post_id: "_orchestrator", from: "owner", text: "Why " + LONG, discussion: "w1", thread_id: "thr_o1", created_at: at(90), delivered: true, read: true },
      r1: { post_id: "_orchestrator", from: "orchestrator", text: "## " + WORDS + " " + LONG + "\\n\\nBecause.", reply_to: "thr_o1", created_at: at(80) },
      o2: { post_id: "_orchestrator", from: "owner", text: "And this?", discussion: "w1", thread_id: "thr_o2", created_at: at(70), delivered: true },
      c1: { post_id: "cl3-open", from: "orchestrator", text: "## " + WORDS + " " + LONG + "\\n\\n" + LONG, created_at: at(50) }
    },
    typing: {} };
  store.posts._dashboard = { column: "info", created_at: at(300), rows: [{ key: "A", name: "Claude Code CLI runtime (the kit run by Claude Code) " + LONG, train: "P-cli", post: "cl3-open", state: "running", at: 2, steps: ["CL1 done step", "CL2 second done step", "CL3 open"] }] };
  delete store._dashboard;
  window.__writes = [];
  const subs = [];
  function snap(coll) { return { metadata: { fromCache: false }, docs: Object.entries(store[coll] || {}).map(([id, d]) => ({ id, data: () => d })) }; }
  function fire() { subs.forEach(([c, f]) => f(snap(c))); }
  const q = (coll) => ({ orderBy: () => q(coll), where: () => q(coll), limit: () => q(coll),
    onSnapshot: (f) => { subs.push([coll, f]); setTimeout(() => f(snap(coll)), 0); return () => {}; } });
  let n = 0;
  const write = (path, o) => { window.__writes.push([path, o]); const [c, id] = path.split("/"); if (store[c] && store[c][id]) { store[c][id] = Object.assign({}, store[c][id], o); fire(); } };
  const docRef = (path) => ({ id: path.split("/").pop(), update: async (o) => write(path, o), set: async (o) => write(path, o) });
  const coll = (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))),
    add: async (o) => { window.__writes.push([c + "/new" + (++n), o]); return { id: "new" + n }; } });
  const db = { collection: coll, doc: (path) => ({ update: async (o) => write(path, o), set: async (o) => write(path, o),
    onSnapshot: (f) => { setTimeout(() => f({ exists: false, metadata: { fromCache: false } }), 0); return () => {}; } }) };
  window.confirm = () => true;
  window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { isOwner: async () => true, id: async () => "u1" } : null };
})();`;

// Every line of visible text that runs past the screen's edges or is cut by a box that clips it (overflow hidden/clip),
// and every visible box that runs off screen; a box made to scroll sideways (overflow auto/scroll: a code block, a
// table, the trains matrix) and an intended ellipsis are exempt. Returns short descriptions, at most 12.
function overflows() {
  const W = document.documentElement.clientWidth, out = [];
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
  const shown = (e) => { for (let a = e; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return false; } return true; };
  // the boxes made to scroll sideways (their content may be wider); any other box clips or scrolls only by accident
  const SIDEWAYS = '.tabs, .tbl, .strip, .pic-scroll, .zimg, .grp-row, .tnet-matrix, .tnet-rows, .ffeed-imgs';
  const scrolls = (e) => !!(e.parentElement && e.parentElement.closest(SIDEWAYS));
  const SKIP = '[hidden], pre, textarea, input, select, .kit-sr-only, [aria-hidden="true"]';
  const seen = new Set();
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t; (t = walk.nextNode()) && out.length < 12;) {
    const e = t.parentElement; if (!e || !t.textContent.trim() || seen.has(e)) continue;
    if (/^(SCRIPT|STYLE|TEMPLATE|OPTION)$/.test(e.tagName) || e.closest(SKIP) || !shown(e) || scrolls(e)) continue;
    const rg = document.createRange(); rg.selectNodeContents(t);
    const rects = [...rg.getClientRects()].filter((r) => r.width > 0 && r.height > 0); if (!rects.length) continue;
    const L = Math.min(...rects.map((r) => r.left)), R = Math.max(...rects.map((r) => r.right));
    let bad = '', clipped = false;
    for (let a = e; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a); if (cs.overflowX === 'visible') continue;
      if (cs.textOverflow === 'ellipsis') { clipped = true; break; }  // an intended ellipsis: the line ends in "..."
      const b = a.getBoundingClientRect(); if (!b.width || !b.height) break;
      if (L < b.left - 1 || R > b.right + 1) bad = 'cut by ' + name(a) + ' (' + Math.round(L) + '..' + Math.round(R) + ' in ' + Math.round(b.left) + '..' + Math.round(b.right) + ')';
      break;
    }
    if (!bad && !clipped && (L < -1 || R > W + 1)) bad = 'off screen ' + Math.round(L) + '..' + Math.round(R) + ' of ' + W;
    if (bad) { seen.add(e); out.push(name(e) + ' ' + bad + ': ' + t.textContent.trim().slice(0, 40)); }
  }
  for (const e of document.querySelectorAll('body *')) {
    if (out.length >= 12) break;
    if (/^(SCRIPT|STYLE|OPTION|TEMPLATE|BR)$/i.test(e.tagName) || e.closest(SKIP) || e.closest('svg') && e.tagName.toLowerCase() !== 'svg') continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(e); if (cs.position === 'fixed' || cs.position === 'absolute') continue;
    if (!shown(e) || scrolls(e)) continue;
    if (r.left < -1 || r.right > W + 1) out.push(name(e) + ' box off screen ' + Math.round(r.left) + '..' + Math.round(r.right) + ' of ' + W + ': ' + (e.textContent || '').trim().slice(0, 40));
  }
  return out;
}

(async () => {
  const here = __dirname;
  let html = fs.readFileSync(process.env.KIT_PAGE || path.join(here, 'board.template.html'), 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  html = html.replace('<script>', '<script>' + STUB + '</script><script>');
  const p = await launch();
  if (!p) { console.log('SKIP no Playwright and no Chrome/Edge/Brave/Chromium found (set KIT_BROWSER=<path>)'); process.exit(process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
  if (!p.viewport) { console.log('SKIP this page driver cannot set the window size'); await p.close(); process.exit(0); }
  let fails = 0; const check = (n, ok, more) => { console.log((ok ? 'ok   ' : 'FAIL ') + n + (ok || !more ? '' : '\n       ' + [].concat(more).join('\n       '))); fails += !ok; };
  const ev = (fn, a) => p.evaluate(fn, a);
  const settle = () => p.wait(450);
  const SHOTS = process.env.KIT_SHOTS; let shotN = 0;
  const shot = async (n) => { if (SHOTS && p.shot) await p.shot(path.join(SHOTS, String(++shotN).padStart(2, '0') + '-' + n + '.png')); };
  for (const [label, w, h] of [['phone', 390, 844], ['desktop', 1300, 900]]) {
    await p.viewport(w, h); await p.open(html); await p.wait(900);
    check(label + ': the lanes, Where we are and All trains stay on screen with long text', true);
    let o = await ev(overflows); check(label + ': nothing overflows on the board itself', !o.length, o);
    // a post with long headers, words and an unbroken word, opened
    await ev(() => { const c = [...document.querySelectorAll('#lanes .card')].find((x) => /CL3|section header/.test(x.textContent)) || document.querySelector('#lanes .card'); if (c) (c.querySelector('.card-open') || c).click(); });
    await settle();
    await shot(label + '-post');
    o = await ev(overflows); check(label + ': nothing overflows in an open post (title, ## sections, comment headers)', !o.length, o);
    await ev(() => { const b = document.getElementById('back'); if (b) b.click(); document.querySelectorAll('.kit-win-close, [data-win-close]').forEach((x) => x.click()); }); await settle();
    // the orchestrator: its list of discussions, then the thread
    await ev(() => { const a = document.getElementById('ask'); if (a) a.click(); }); await settle();
    o = await ev(overflows); check(label + ': nothing overflows in the orchestrator\'s list of discussions', !o.length, o);
    await ev(() => { const d = document.querySelector('.disc[data-disc="w1"]'); if (d) d.click(); }); await p.wait(700);
    await shot(label + '-discussion');
    o = await ev(overflows); check(label + ': nothing overflows in a discussion with long headers', !o.length, o);
    await ev(() => { const b = document.getElementById('back'); if (b) b.click(); }); await settle();
  }

  // the badge (his 22:04): a reply to the thread of his Like is drawn in its discussion, and once on screen it clears
  await p.viewport(1300, 900); await p.open(html); await p.wait(900);
  await ev(() => document.getElementById('ask').click()); await settle();
  check('the discussion has the new-badge before he reads it', await ev(() => !!document.querySelector('.disc.unread[data-disc="w1"]')));
  await ev(() => document.querySelector('.disc[data-disc="w1"]').click()); await p.wait(500);
  check('a reply to the thread of a Like is drawn (it was hidden under the Like)', await ev(() => !!document.querySelector('.cmt[data-cid="rlike"]')));
  check('the reply to his comment is drawn under it', await ev(() => !!document.querySelector('.cmt[data-cid="r1"]')));
  // scroll each unseen comment into view for longer than SEEN_MS
  for (const cid of ['rlike', 'r1']) { await ev((c) => { const n = document.querySelector('.cmt[data-cid="' + c + '"]'); if (n) n.scrollIntoView({ block: 'center' }); }, cid); await p.wait(1500); }
  await ev(() => document.getElementById('back').click()); await settle();
  await ev(() => { const a = document.getElementById('ask'); if (a && document.getElementById('sheet').hidden) a.click(); }); await settle();
  const still = await ev(() => { const d = document.querySelector('.disc[data-disc="w1"]'); return d ? d.className : 'missing'; });
  check('the badge clears once its replies have been on screen', !/unread/.test(still), still);

  // the delivery marks (his 22:18): sent, seen and answered each say so
  await ev(() => document.querySelector('.disc[data-disc="w1"]').click()); await p.wait(400);
  const marks = await ev(() => ['o1', 'o2'].map((id) => { const n = document.querySelector('.cmt[data-cid="' + id + '"] .deliv'); return n ? n.textContent : ''; }));
  check('an answered comment says "answered", a passed-on one says "sent"', /answered/.test(marks[0]) && /sent/.test(marks[1]) && !/seen|answered/.test(marks[1]), JSON.stringify(marks));
  await ev(() => document.getElementById('back').click()); await settle();

  // the train's tasklist (his 22:03): its done steps are listed with the open one
  const tl = await ev(() => {
    if (typeof window._kitTrainPosts !== 'function') return 'no _kitTrainPosts';
    const open = (window.__kitState ? [] : []);
    return window._kitTrainPosts({ cli: true }, open).map((x) => x.id).join(',');
  });
  check('a train\'s tasklist holds its done steps (cl1-done, cl2-done) and the open one', /cl1-done/.test(tl) && /cl2-done/.test(tl) && /cl3-open/.test(tl), tl);
  // and through the page: the Trains view's tasklist button on the cli train
  const viaUi = await ev(async () => {
    const tab = [...document.querySelectorAll('button, [role=tab]')].find((b) => /^\s*Trains\s*$/i.test(b.textContent || '') || b.dataset.tab === 'trains');
    if (tab) tab.click();
    await new Promise((r) => setTimeout(r, 400));
    const btn = document.querySelector('.train-tl-btn'); if (!btn) return 'no tasklist button';
    btn.click(); await new Promise((r) => setTimeout(r, 300));
    const m = document.getElementById('kit-tl-modal'); return m ? m.textContent : 'no tasklist';
  });
  check('the Trains view\'s tasklist shows the done steps (2 / 3 done)', /CL1/.test(viaUi) && /CL2/.test(viaUi) && /2 \/ 3 done/.test(viaUi), viaUi.slice(0, 160));
  if (/2 \/ 3 done/.test(viaUi)) { await p.viewport(390, 844); await p.wait(300); const o = await ev(overflows); check('phone: nothing overflows in the tasklist', !o.length, o); }

  check('no page error', p.errors.length === 0, p.errors.slice(0, 5));
  console.log(fails + ' failed'); await p.close(); process.exit(fails ? 1 : 0);
})();
