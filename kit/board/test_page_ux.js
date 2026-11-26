// CL27: the owner's UI and UX audit (his comment of 2026-11-23 07:08, docs/CL27.md), each problem as a measure in a
// real browser on the real page (a board server on a throwaway root, its posts seeded), at phone widths with touch
// and on a desktop. Every check fails on the page before CL27.
//   node kit/board/test_page_ux.js            (KIT_KIT=<another checkout> runs it on that checkout's page)
// Playwright only (a phone needs touch and a visual viewport); without it: SKIP, never a pass.
let pw; try { pw = require('playwright'); } catch (e) { console.log('SKIP no Playwright'); process.exit(process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
const fs = require('fs'), os = require('os'), path = require('path'), http = require('http'), net = require('net');
const { spawn } = require('child_process');
const KIT = process.env.KIT_KIT || path.join(__dirname, '..', '..');

let fails = 0;
const check = (n, ok, more) => { console.log((ok ? 'ok   ' : 'FAIL ') + n + (ok || more === undefined ? '' : '\n       ' + (typeof more === 'string' ? more : JSON.stringify(more)))); fails += !ok; };
const freePort = () => new Promise((r) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
const req = (port, method, p, body, hdrs) => new Promise((res, rej) => {
  const d = body ? Buffer.from(JSON.stringify(body)) : null;
  const q = http.request({ host: '127.0.0.1', port, method, path: p, headers: Object.assign(d ? { 'content-type': 'application/json', 'content-length': d.length } : {}, hdrs || {}) }, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => res([r.statusCode, b])); });
  q.on('error', rej); if (d) q.write(d); q.end();
});

// A stand-in for the phone's visual viewport: window.__setVV(height, offsetTop) is the keyboard opening (or the
// browser panning the page above it); __setVV(0) closes it.
const VV = `(() => {
  const real = window.visualViewport; let h = 0, top = 0; const t = new EventTarget();
  const vv = { addEventListener: (...a) => t.addEventListener(...a), removeEventListener: (...a) => t.removeEventListener(...a),
    get height() { return h || window.innerHeight; }, get offsetTop() { return h ? top : 0; }, get width() { return window.innerWidth; },
    get offsetLeft() { return 0; }, get pageTop() { return window.scrollY + this.offsetTop; }, get scale() { return 1; } };
  Object.defineProperty(window, 'visualViewport', { get: () => vv, configurable: true });
  window.__setVV = (nh, nt) => { h = nh || 0; top = nt || 0; t.dispatchEvent(new Event('resize')); };
})();`;

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kit-ux-'));
  fs.mkdirSync(path.join(root, '.kit'));
  const tpl = fs.readFileSync(path.join(KIT, 'kit', 'board', 'board.template.html'), 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  fs.writeFileSync(path.join(root, '.kit', 'board.html'), tpl);
  fs.writeFileSync(path.join(root, '.kit', 'config.json'), JSON.stringify({ project: 'Test' }));
  // a repo doc for the viewer checks (his 08:20): wrapped list items, a section name with a comma, pictures above it
  fs.mkdirSync(path.join(root, 'docs'));
  const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');
  fs.writeFileSync(path.join(root, 'docs', 'pic.png'), PNG);
  const filler = Array(14).fill('A paragraph of filler that takes room in the viewer so the sections below sit far down.').join('\n\n');
  fs.writeFileSync(path.join(root, 'docs', 't.md'), '# T\n\nIntro.\n\n## First section\n\n![a picture](pic.png)\n\n![another](pic.png)\n\n- A list item whose text\n  runs on a second source line that stays in the item.\n- Second item.\n\n' + filler +
    '\n\n## Looked at, not a problem\n\nText of it.\n\n' + filler + '\n\n## Third\n\nThe end.\n\n' + filler + '\n\n## What is left\n\nA short last section.\n');
  const port = await freePort();
  const env = Object.assign({}, process.env); delete env.KIT_BOARD_TOKEN;
  const srv = spawn('python3', ['-I', path.join(KIT, 'kit', 'board', 'board_server.py'), '--host', '127.0.0.1', '--port', String(port), '--root', root], { env, stdio: 'ignore' });
  const URL0 = 'http://127.0.0.1:' + port;
  let up = false; for (let i = 0; i < 200 && !up; i++) { try { up = (await req(port, 'GET', '/_api/health'))[0] === 200; } catch (e) { await new Promise((r) => setTimeout(r, 150)); } }
  if (!up) { console.log('FAIL the board server did not start'); srv.kill(); process.exit(1); }
  const now = Date.now(), at = (m) => new Date(now - m * 60000).toISOString();
  const LONG = 'A comment long enough to wrap over several lines of the phone, the way he writes them when something is off. ';
  // seeded as a copy of a board (X-Kit-Gate: copy): the proof gate is for live writes, and a refused seed must stop the test, not drop a post
  const put = async (c, id, data) => { const r = await req(port, 'PUT', '/_api/db/' + c + '/' + id, { data }, { 'X-Kit-Gate': 'copy' }); if (r[0] >= 300) throw new Error('seed ' + id + ' refused: ' + r[0] + ' ' + r[1]); return r; };
  await put('posts', '_orchestrator', { title: 'Ask the orchestrator', column: 'info', created_at: at(500) });
  await put('posts', '_trains', { column: 'info', created_at: at(500), rows: [] });
  await put('posts', 'cl1-done', { title: 'CL1 a done step', part: 'CL1', train: 'cli', column: 'done', status: 'complete', created_at: at(400), done_at: at(390), verdict: 'Done and checked.', body: 'done' });
  await put('posts', 'cl2-open', { title: 'CL2 the step running now', part: 'CL2', parent: 'CL1', train: 'cli', column: 'info', status: 'in progress', created_at: at(300), verdict: 'Working on it.', body: 'Lead.\n\nSee `docs/t.md § Looked at, not a problem`, `docs/t.md § Third` and `docs/t.md § What is left`.\n\n## What I did\n\n' + LONG.repeat(12) });
  for (let k = 1; k <= 7; k++) await put('posts', 'clz' + k, { title: 'CLZ' + k + ' an older done step', part: 'CLZ' + k, train: 'cli', column: 'done', status: 'complete', created_at: at(480 - k), done_at: at(470 - k), verdict: 'Done.', body: 'x' });
  await put('posts', 'cl0-past', { title: 'CL0 a step the train moved past', part: 'CL0', train: 'cli', column: 'info', status: 'complete', created_at: at(450), verdict: 'Finished, never moved to Done.', body: 'x' });
  await put('posts', 'cl3-next', { title: 'CL3 the next step', part: 'CL3', parent: 'CL2', train: 'cli', column: 'info', status: 'planned', created_at: at(250), body: 'later' });
  await put('posts', 'd1-q', { title: 'D1 a question for him', part: 'D1', train: 'demo', column: 'question', created_at: at(200), body: 'Which one?', options: ['A', 'B'] });
  for (let i = 0; i < 8; i++) await put('comments', 'c' + i, { post_id: 'cl2-open', from: i % 2 ? 'owner' : 'orchestrator', text: LONG.repeat(3), created_at: at(100 - i), delivered: true, read: true });
  await put('comments', 'w1', { post_id: '_orchestrator', from: 'orchestrator', text: 'Welcome. ' + LONG.repeat(4), created_at: at(90) });
  for (let i = 0; i < 6; i++) await put('comments', 'o' + i, { post_id: '_orchestrator', from: i % 2 ? 'owner' : 'orchestrator', text: LONG.repeat(3), discussion: 'w1', created_at: at(80 - i), delivered: true });

  const b = await pw.chromium.launch(); const { settle } = require('./test_settle'); require('./test_settle').arm(b);   // waits for the page, never a fixed time
  // the board drawn (its cards are on the page), then a short settle: a fixed 1.6 s was not enough on a busy computer
  const ready = async (p) => { await p.waitForFunction(() => document.querySelectorAll('.card').length > 0, null, { timeout: 20000 }).catch(() => {}); await settle(p); };
  const open = async (w, h, mobile, hash) => {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 1 });
    await ctx.addInitScript(VV);
    // the page's poll runs every second here, not every 15 s: what it does is seen by every run, not only by a slow one
    await ctx.addInitScript('window.__KIT_LIVE_REFRESH_MS__ = 1000;');
    const p = await ctx.newPage(); p.errs = []; p.on('pageerror', (e) => p.errs.push(String(e)));
    await p.goto(URL0 + '/' + (hash || '')); await ready(p);
    return p;
  };
  const view = async (p, name) => { await p.evaluate((n) => { const x = document.querySelector('.kit-focus-exit'); if (x && x.offsetParent) x.click(); const b = [...document.querySelectorAll('#kit-views button')].find((e) => e.textContent.trim() === n); if (b) b.click(); }, name); await settle(p); };
  // the header: its visible controls, in how many rows, any off screen, any two overlapping
  const header = (p) => p.evaluate(() => {
    const W = document.documentElement.clientWidth, hs = [...document.querySelectorAll('header.top h1, header.top button, header.top #kit-views, header.top .status')]
      .filter((e) => e.offsetParent && !e.closest('#kit-views button') && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).opacity !== '0' && !e.closest('.kit-search-wrap #q'));
    const rs = hs.filter((e) => !hs.some((o) => o !== e && o.contains(e))).map((e) => { const r = e.getBoundingClientRect(); return { n: e.id || e.tagName, l: r.left, r: r.right, t: r.top, b: r.bottom }; }).filter((r) => r.r - r.l > 0);
    const rows = []; rs.forEach((r) => { const c = (r.t + r.b) / 2; if (!rows.some((x) => Math.abs(x - c) < 14)) rows.push(c); });
    const off = rs.filter((r) => r.l < -1 || r.r > W + 1).map((r) => r.n);
    const over = []; rs.forEach((a, i) => rs.slice(i + 1).forEach((c) => { if (Math.min(a.r, c.r) - Math.max(a.l, c.l) > 2 && Math.min(a.b, c.b) - Math.max(a.t, c.t) > 2) over.push(a.n + '/' + c.n); }));
    return { rows: rows.length, off, over };
  });
  const rect = (p, sel) => p.evaluate((s) => { const e = document.querySelector(s); if (!e || !e.getClientRects().length) return null; const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height }; }, sel);
  const hit = (a, c) => a && c && Math.min(a.r, c.r) - Math.max(a.l, c.l) > 1 && Math.min(a.b, c.b) - Math.max(a.t, c.t) > 1;
  const typeLong = (p) => p.evaluate((t) => { const x = document.getElementById('c-text'); x.focus(); x.value = t; x.dispatchEvent(new Event('input', { bubbles: true })); }, LONG.repeat(6));
  // text of the post or its comments that shows below the comment box, inside the open sheet
  const under = (p) => p.evaluate(() => {
    const c = document.getElementById('composer'), sh = document.getElementById('sheet'); if (!c || !sh) return 'no composer';
    const cr = c.getBoundingClientRect(), sr = sh.getBoundingClientRect(), out = [];
    for (let y = cr.bottom + 2; y < Math.min(sr.bottom, innerHeight) - 1; y += 4) for (const x of [cr.left + 30, (cr.left + cr.right) / 2, cr.right - 30]) {
      const e = document.elementFromPoint(x, y); if (e && e.closest && e.closest('.cmt, #p-body, .disc, .thread p') && !c.contains(e)) out.push(Math.round(y) + ':' + (e.className || e.tagName));
    }
    return out.slice(0, 3).join(' ');
  });

  for (const W of [411, 360]) {
    const p = await open(W, 860, true);
    // 1. "blue highlights appearing whenever you tap something on mobile"
    const tap = await p.evaluate(() => [document.querySelector('header.top button'), document.querySelector('.card')].filter(Boolean).map((e) => getComputedStyle(e).webkitTapHighlightColor));
    check(W + ' px: no tap highlight on a button or a card (' + tap.join(', ') + ')', tap.length === 2 && tap.every((c) => /rgba\(.*,\s*0\)$|transparent/.test(c)));
    // 2. "All the buttons on the header that sometimes don't seem to fit on mobile"
    for (const v of ['Lanes', 'Feed', 'Trains']) {
      await view(p, v); const hd = await header(p);
      check(W + ' px, ' + v + ': the header is at most two clean rows, every control on screen, none over another', hd.rows <= 2 && !hd.off.length && !hd.over.length, hd);
    }
    // 3. "the random bar at the top of the feed view with a post count and a sorting button"
    await view(p, 'Feed');
    const bar = await p.evaluate(() => [...document.querySelectorAll('.ffeed-bar')].filter((e) => e.offsetParent).length);
    check(W + ' px: the feed has no bar with a post count and a second sort button', bar === 0, bar + ' shown');
    // 4. the train view "half baked", the task list "more informative, less scroll heavy"
    await view(p, 'Trains');
    const tr = await p.evaluate(() => ({ lists: [...document.querySelectorAll('.kit-tl-inline')].map((s) => s.textContent), matrix: !!document.querySelector('.tnet-matrix') }));
    // his 08:30: folded, a train card shows its name and its open steps; a tap on the name pulls the done ones out above
    if (W === 411) {
      const vis = () => p.evaluate(() => { const s = [...document.querySelectorAll('.kit-tl-inline')].find((x) => /CL2/.test(x.textContent)); if (!s) return null;
        return [...s.querySelectorAll('.kit-tl-item')].map((i) => ({ t: (i.querySelector('.kit-tl-part') || {}).textContent, done: i.classList.contains('is-done'), h: Math.round(i.getBoundingClientRect().height), y: Math.round(i.getBoundingClientRect().top) })); });
      const before = await vis();
      const shown = before ? before.filter((x) => x.h > 2).map((x) => x.t).join() : '';
      check('411 px: a folded train card shows only its open steps (CL2, CL3), not its done ones (CL1, nor CL0 that the train moved past: his 08:55)', shown === 'CL2,CL3', before.map((x) => x.t + ':' + x.h).join(' '));
      const gapF = await p.evaluate(() => { const s = [...document.querySelectorAll('.kit-tl-inline')].find((x) => /CL2/.test(x.textContent)); const bd = s.querySelector('.kit-tl-body'); const its = [...bd.querySelectorAll('.kit-tl-item')].filter((i) => i.getBoundingClientRect().height > 2); return Math.round(its[0].getBoundingClientRect().top - bd.getBoundingClientRect().top); });
      check('411 px: no blank band where the done steps are folded (first open step ' + gapF + ' px under the card\'s list top, at most 16; his 08:55)', gapF <= 16, gapF);
      await p.evaluate(() => { const s = [...document.querySelectorAll('.kit-tl-inline')].find((x) => /CL2/.test(x.textContent)); s.querySelector('.kit-tl-hdr b').click(); }); await settle(p);
      const after = await vis();
      const cl1 = after && after.find((x) => x.t === 'CL1'), cl2 = after && after.find((x) => x.t === 'CL2');
      const hid = before && before.find((x) => x.t === 'CL1');
      check('411 px: a tap on the train\'s name pulls its hidden done step out above the open one', !!(hid && hid.h <= 2 && cl1 && cl2 && cl1.h > 10 && cl1.y < cl2.y), { before, after });
      // remembered per train on this device: after a reload the card is still unfolded
      await p.reload(); await ready(p); await view(p, 'Trains');
      const again = await vis(), cl1b = again && again.find((x) => x.t === 'CL1');
      await p.evaluate(() => { const s = [...document.querySelectorAll('.kit-tl-inline')].find((x) => /CL2/.test(x.textContent)); if (s) s.querySelector('.kit-tl-hdr b').click(); }); await settle(p);
      await p.reload(); await ready(p); await view(p, 'Trains');
      const third = await vis(), cl1c = third && third.find((x) => x.t === 'CL1');
      check('411 px: the fold is remembered per train across a reload (unfolded stays unfolded, folded stays folded)', !!(cl1b && cl1b.h > 10 && cl1c && cl1c.h <= 2), { again, third });
    }
    check(W + ' px: the Trains view is each train\'s task list, done steps included (CL1, CL2; D1)', tr.lists.length === 2 && !tr.matrix && tr.lists.some((t) => /CL1/.test(t) && /CL2/.test(t)), tr);
    await p.close();
  }

  {
    const p = await open(411, 860, true, '#/post/cl2-open');
    await p.evaluate(() => document.getElementById('composer').scrollIntoView({ block: 'end' })); await settle(p);
    // 5. "the send button and attachment buttons that cover up text"
    await typeLong(p); await settle(p);
    const ta = await rect(p, '#c-text'), snd = await rect(p, '#c-send'), att = await rect(p, '#c-attach');
    check('phone: the send and attach buttons sit beside the text, never over it', ta && snd && !hit(ta, snd) && !hit(ta, att || null), { ta, snd, att });
    check('phone: the comment box grows with the text (taller than one line)', ta && ta.h > 60, ta);
    // 6. "the comment box that feels off and not adapted to the mobile keyboard appearing, there being a huge margin on the top"
    for (const [h, top] of [[470, 0], [470, 300]]) {
      await p.evaluate(([a, c]) => window.__setVV(a, c), [h, top]); await settle(p);
      const c = await rect(p, '#composer'), br = await rect(p, '#sheet .bar');
      check('phone, keyboard open (visible ' + top + '..' + (top + h) + '): the comment box sits in the visible part, on the keyboard', c && c.t >= top - 1 && c.b <= top + h + 1 && c.b >= top + h - 40, c);
      check('phone, keyboard open (visible ' + top + '..' + (top + h) + '): the post bar is at the top of it, no margin', br && Math.abs(br.t - top) <= 2, br);
    }
    // the grown box: its text starts right at the box's top and next to the paperclip (no empty grid rows or gaps)
    const tg = await rect(p, '#c-text'), cg = await rect(p, '#composer');
    check('phone: no blank above the text in a grown box (text box top ' + Math.round(tg.t - cg.t) + ' px under the box top, at most 6)', tg.t - cg.t <= 6 && tg.l - cg.l <= 46, { tg, cg });
    // "New below" with the keyboard open and the page panned: the pill is above the box, not on its text
    await p.evaluate(() => { const t = document.getElementById('c-text'); t.value = ''; t.dispatchEvent(new Event('input', { bubbles: true })); try { localStorage.clear(); } catch (e) {} });
    await p.evaluate(() => { const sh = document.getElementById('sheet'); const c = document.querySelector('.cmt'); if (c) sh.scrollTop += c.getBoundingClientRect().top - 120; });
    await settle(p);
    const pj = await rect(p, '#p-newjump'), pc = await rect(p, '#composer');
    check('phone, keyboard open and panned: the "New below" pill shows and is not over the box', !!pj && !hit(pj, pc), { pj, pc });
    await p.evaluate(() => window.__setVV(0)); await settle(p);
    // 7. a word tapped in the box keeps the keyboard: nothing on the page blurs it, and the browser's spelling menu
    //    (Chrome's, which hides the keyboard) is not asked for on a touch screen
    const t0 = await rect(p, '#c-text');
    await p.touchscreen.tap(t0.l + 40, t0.t + 14); await settle(p);
    await put('comments', 'live1', { post_id: 'cl2-open', from: 'orchestrator', text: 'A live reply', created_at: new Date().toISOString() });
    await settle(p);
    const fstate = await p.evaluate(() => ({ focus: document.activeElement && document.activeElement.id, spell: document.getElementById('c-text').spellcheck }));
    check('phone: a tap in the text and a live reply keep the box focused (the keyboard stays)', fstate.focus === 'c-text', fstate);
    check('phone: the box does not ask for the browser\'s spelling menu (the keyboard\'s strip corrects)', fstate.spell === false, fstate);
    // 8. nothing of the post shows under or below the comment box
    await p.evaluate(() => { const t = document.getElementById('c-text'); t.value = ''; t.dispatchEvent(new Event('input', { bubbles: true })); document.getElementById('sheet').scrollTop = 0; document.getElementById('composer').scrollIntoView({ block: 'end' }); });
    await p.evaluate(() => { const sh = document.getElementById('sheet'); sh.scrollTop = Math.max(0, sh.scrollTop - 200); }); await settle(p);
    const u = await under(p);
    check('phone: no text of the post shows below the comment box', !u, u);
    await p.close();
  }

  {
    // 9. the orchestrator and a feed post's comments: full sheets on a phone, the box at the bottom, the pill above it
    const p = await open(411, 860, true);
    await p.evaluate(() => document.getElementById('ask').click()); await settle(p);
    await p.evaluate(() => { const d = document.querySelector('.disc[data-disc]'); if (d) d.click(); }); await settle(p);
    const s = await rect(p, '#sheet');
    check('phone: the orchestrator is a full sheet, edge to edge (no 8 px frame)', s && s.l <= 0 && s.t <= 0 && s.r >= 411 && s.b >= 860, s);
    const u = await under(p);
    check('phone: no message shows below the orchestrator\'s comment box', !u, u);
    await p.evaluate(() => { const si = document.querySelector('#sheet .sheet-in'); if (si) si.scrollTop = 0; }); await settle(p);
    const nj = await rect(p, '#p-newjump'), cp = await rect(p, '#composer');
    check('phone: the "New below" pill is not over the comment box', !hit(nj, cp), { nj, cp });
    await p.evaluate(() => { const t = document.getElementById('c-text'); t.focus(); });
    await p.evaluate(() => window.__setVV(470, 0)); await settle(p);
    const c2 = await rect(p, '#composer'), b2 = await rect(p, '#sheet .bar');
    check('phone, keyboard open: the orchestrator\'s box is on the keyboard and its bar at the top', c2 && b2 && c2.b <= 471 && c2.b >= 430 && Math.abs(b2.t) <= 2, { c2, b2 });
    await p.evaluate(() => window.__setVV(0));
    // a git diff drawn for a post is not carried into the orchestrator
    await p.evaluate(() => { const d = document.createElement('details'); d.id = 'kit-git-diff'; d.textContent = 'Inspect git diff (abc1234)'; const b = document.getElementById('p-body'); b.dataset.kitDiffPost = 'cl2-open'; b.after(d); });
    await p.evaluate(() => { document.getElementById('back') && document.getElementById('back').click(); }); await settle(p);
    await p.goto(URL0 + '/#/lanes'); await settle(p);
    await p.evaluate(() => { const d = document.createElement('details'); d.id = 'kit-git-diff'; d.textContent = 'Inspect git diff (abc1234)'; const b = document.getElementById('p-body'); b.dataset.kitDiffPost = 'cl2-open'; b.after(d); document.getElementById('ask').click(); }); await settle(p);
    const gd = await p.evaluate(() => { const d = document.getElementById('kit-git-diff'); return !!(d && d.offsetParent); });
    check('phone: the orchestrator does not show the git diff of the post opened before it', !gd);
    await p.goto(URL0 + '/#/lanes'); await settle(p);
    await view(p, 'Feed');
    await p.evaluate(() => { const c = document.querySelector('.ffeed-card'); const b = c && [...c.querySelectorAll('button')].find((x) => /comment/i.test((x.getAttribute('aria-label') || '') + (x.title || ''))); if (b) b.click(); }); await settle(p);
    const fs2 = await rect(p, '#sheet');
    check('phone: a feed post\'s comments open as a full sheet (no 12 px frame)', fs2 && fs2.l <= 0 && fs2.t <= 0 && fs2.r >= 411 && fs2.b >= 860, fs2);
    check('phone: no page error', p.errs.length === 0, p.errs.slice(0, 3));
    await p.close();
  }

  {
    // his 08:20: a list item's wrapped line stays in the item; a section with a comma is linked and embedded; the
    // section asked for is the first thing in its viewer, also once the pictures above it have loaded (slowly)
    const ctx = await b.newContext({ viewport: { width: 411, height: 860 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    await ctx.route('**/_api/repo-asset**', async (r) => { await new Promise((x) => setTimeout(x, 900)); await r.continue(); });
    await ctx.route('**/pic.png**', async (r) => { await new Promise((x) => setTimeout(x, 900)); await r.continue(); });
    const p = await ctx.newPage(); p.errs = []; p.on('pageerror', (e) => p.errs.push(String(e)));
    await p.goto(URL0 + '/#/post/cl2-open'); await settle(p);
    // the three sections it embeds are drawn, their pictures loaded, and each stays where it is for 300 ms (they load on their own, after the post)
    await p.waitForFunction(() => {
      const cards = [...document.querySelectorAll('.kit-inline-embed')];
      const ready = ['Third', 'Looked at, not a problem', 'What is left'].every((n) => cards.some((c) => [...c.querySelectorAll('h1,h2,h3,h4')].some((h) => h.textContent.trim() === n)));
      if (!ready || [...document.querySelectorAll('.kit-inline-embed img')].some((i) => !i.complete)) { window.__kitPos = null; return false; }
      const pos = cards.map((c) => { const b = c.querySelector('.kit-inline-embed-body'); return b ? b.scrollTop : 0; }).join();
      if (pos !== (window.__kitPos || {}).v) { window.__kitPos = { v: pos, at: performance.now() }; return false; }
      return performance.now() - window.__kitPos.at >= 300;
    }, null, { timeout: 30000, polling: 50 }).catch(() => {});
    const v = await p.evaluate(() => {
      const codes = [...document.querySelectorAll('#p-body code')];
      const comma = codes.find((c) => /Looked at, not a problem/.test(c.textContent));
      const cards = [...document.querySelectorAll('.kit-inline-embed')];
      const card = (re) => cards.find((c) => re.test((c.querySelector('.iettl') || {}).textContent || ''));
      const top = (re, name) => { const c = card(re); if (!c) return 'no embed'; const bd = c.querySelector('.kit-inline-embed-body'); const h = [...bd.querySelectorAll('h1,h2,h3,h4')].find((x) => x.textContent.trim() === name); return h ? Math.round(h.getBoundingClientRect().top - bd.getBoundingClientRect().top) : 'no heading'; };
      const li = [...document.querySelectorAll('.kit-inline-embed li')].find((x) => /A list item/.test(x.textContent));
      return { linked: !!(comma && comma.classList.contains('kit-doc-link')), commaEmbed: !!card(/Looked at, not a problem/), third: top(/§ Third/, 'Third'), looked: top(/Looked at/, 'Looked at, not a problem'), last: top(/What is left/, 'What is left'),
        li: li ? li.textContent : 'no item', stray: [...document.querySelectorAll('.kit-inline-embed p')].some((x) => /^runs on a second/.test(x.textContent.trim())) };
    });
    check('a section name with a comma is linked and shown in its viewer (his 08:20)', v.linked && v.commaEmbed, v);
    check('a list item\'s wrapped source line stays in the item, not a paragraph of its own (his 08:20)', /stays in the item/.test(v.li) && !v.stray, v);
    check('the section asked for is the first thing in its viewer, after slow pictures loaded, the last one too (his 08:20): ' + v.third + ' / ' + v.looked + ' / ' + v.last + ' px from the top', typeof v.third === 'number' && v.third >= 0 && v.third <= 14 && typeof v.looked === 'number' && v.looked >= 0 && v.looked <= 14 && typeof v.last === 'number' && v.last >= 0 && v.last <= 14, v);
    // the same in the document viewer opened on that section
    await p.evaluate(() => window._kitOpenRepoDoc('docs/t.md', 'What is left')); await settle(p);
    // the viewer shows that heading and has stopped scrolling to it (the same place for 300 ms)
    await p.waitForFunction(() => {
      const hs = [...document.querySelectorAll('.kit-docs-modal h2')].filter((h) => h.offsetParent && h.textContent.trim() === 'What is left');
      const h = hs[hs.length - 1]; if (!h || [...document.querySelectorAll('.kit-docs-modal img')].some((i) => !i.complete)) return false;
      const y = Math.round(h.getBoundingClientRect().top);
      if (y !== (window.__kitDv || {}).y) { window.__kitDv = { y, at: performance.now() }; return false; }
      return performance.now() - window.__kitDv.at >= 300;
    }, null, { timeout: 30000, polling: 50 }).catch(() => {});
    const dv = await p.evaluate(() => { const hs = [...document.querySelectorAll('.kit-docs-modal h2')].filter((h) => h.offsetParent && h.textContent.trim() === 'What is left'); const h = hs[hs.length - 1]; if (!h) return 'no heading'; let sc = h.parentElement; while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement; return sc ? Math.round(h.getBoundingClientRect().top - sc.getBoundingClientRect().top) : 'no scroller'; });
    check('the document viewer opened on its last, short section shows that heading first (' + dv + ' px from its top)', typeof dv === 'number' && dv >= 0 && dv <= 30, dv);
    check('viewer checks: no page error', p.errs.length === 0, p.errs.slice(0, 3));
    await ctx.close();
  }

  {
    const p = await open(1300, 900, false, '#/post/cl2-open');
    const sp = await p.evaluate(() => document.getElementById('c-text').spellcheck);
    check('desktop: the comment box keeps the browser\'s spelling underline (a right-click menu, no keyboard to hide)', sp === true);
    await p.goto(URL0 + '/#/lanes'); await settle(p);
    const hd = await header(p);
    check('desktop: the header is one row, nothing over anything', hd.rows === 1 && !hd.off.length && !hd.over.length, hd);
    check('desktop: no page error', p.errs.length === 0, p.errs.slice(0, 3));
    await p.close();
  }
  await b.close(); srv.kill();
  try { fs.rmSync(root, { recursive: true, force: true }); } catch (e) { /* left in tmp */ }
  console.log(fails + ' failed'); process.exit(fails ? 1 : 0);
})();
