// The board page in a real browser with a stub db: node kit/board/test_page.js
// Playwright if installed, else any Chrome/Edge/Brave/Chromium on the computer (kit/claude/page_driver.js); none: SKIP.
// No keyboard shortcut writes anything; removed posts, messages and discussions are hidden; each Remove writes removed: true.
const { launch } = require('../claude/page_driver');
const fs = require('fs'), path = require('path');
(async () => {
  const here = __dirname;
  let html = fs.readFileSync(process.env.KIT_PAGE || path.join(here, 'board.template.html'), 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  html = html.replace('<script>', '<script>' + fs.readFileSync(path.join(here, 'test_page_stub.js'), 'utf8') + '</script><script>');
  const p = await launch();
  if (!p) { console.log('SKIP no Playwright and no Chrome/Edge/Brave/Chromium found (set KIT_BROWSER=<path>)'); process.exit(process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
  const errs = p.errors;
  const text = (sel) => p.evaluate((s) => { const e = document.querySelector(s); return e ? e.textContent : ''; }, sel);
  // the writes a key or a button made: a read mark (data/users/<id>/board-seen) is the page noting he opened a post, about a second later
  const acts = () => p.evaluate(() => window.__writes.filter((x) => !/\/board-seen$/.test(x[0])));
  let fails = 0; const check = (n, ok) => { console.log((ok ? 'ok   ' : 'FAIL ') + n); fails += !ok; };
  await p.open(html); await p.wait(800);
  const lanes = await text('#lanes');
  check('a removed post is hidden, a kept one shown', lanes.includes('Kept post') && !lanes.includes('Removed post'));
  // the old shortcuts, pressed the way they hit (2026-11-20: a stray "l" liked a hidden button and launched 4 sessions)
  await p.evaluate(() => { const c = document.querySelector('#lanes .card'); if (c) c.focus(); });
  await p.press('l'); await p.press('d'); await p.wait(200);
  check('no keyboard shortcut on a focused card (L likes, D moves to Done on the old page)', (await acts()).length === 0);
  await p.evaluate(() => { const a = document.getElementById('ask'); if (a) a.click(); }); await p.wait(400);
  await p.evaluate(() => { if (document.activeElement) document.activeElement.blur(); });
  await p.press('l'); await p.wait(200);
  check('no ghost Like on Ask the orchestrator, whose Like is hidden', (await acts()).length === 0);
  await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); }); await p.wait(300);
  await p.evaluate(() => { const c = [...document.querySelectorAll('#lanes .card')].find(x => /Kept post/.test(x.textContent)); if (c) (c.querySelector('.card-open') || c).click(); }); await p.wait(400);
  const sheet = await text('#sheet');
  check('a removed message is hidden', sheet.includes('first message') && !sheet.includes('removed message'));
  await p.evaluate(() => { if (document.activeElement) document.activeElement.blur(); });
  await p.press('l'); await p.press('d'); await p.wait(200);
  check('no keyboard shortcut in an open post (L likes it on the old page)', (await acts()).length === 0);
  await p.evaluate(() => document.getElementById('remove').click()); await p.wait(300);
  const w = await acts();
  check('Remove on a post writes removed: true', w.length === 1 && w[0][0] === 'posts/P-a' && w[0][1].removed === true);
  await p.evaluate(() => { const a = document.getElementById('ask'); if (a) a.click(); }); await p.wait(400);
  // A discussion is removed in select mode (the pencil, last in #orch-hdr-tools): tick its square, then the bin (2nd tool).
  await p.evaluate(() => document.querySelector('#orch-hdr-tools button:last-child').click()); await p.wait(200);
  await p.evaluate(() => document.querySelector('.discrow .kit-pick-sq').click()); await p.wait(200);
  await p.evaluate(() => document.querySelector('#orch-hdr-tools button:nth-child(2)').click()); await p.wait(300);
  check('Remove on a discussion hides all its messages', (await p.evaluate(() => window.__writes.map(x => x[0]))).includes('comments/c3')
        && !(await text('#sheet')).includes('a discussion'));
  check('no page error', errs.length === 0);
  console.log(fails + ' failed'); await p.close(); process.exit(fails ? 1 : 0);
})();
