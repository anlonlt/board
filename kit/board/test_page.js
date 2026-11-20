// The board page in a real browser with a stub db (needs Playwright: node kit/board/test_page.js).
// No keyboard shortcut writes anything; removed posts, messages and discussions are hidden; each Remove writes removed: true.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path'), os = require('os');
(async () => {
  const here = __dirname;
  let html = fs.readFileSync(path.join(here, 'board.template.html'), 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  html = html.replace('<script>', '<script>' + fs.readFileSync(path.join(here, 'test_page_stub.js'), 'utf8') + '</script><script>');
  const f = path.join(os.tmpdir(), 'kit-test-page.html'); fs.writeFileSync(f, html);
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1200, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  let fails = 0; const check = (n, ok) => { console.log((ok ? 'ok   ' : 'FAIL ') + n); fails += !ok; };
  await p.goto('file://' + f); await p.waitForTimeout(800);
  const lanes = await p.textContent('#lanes');
  check('a removed post is hidden, a kept one shown', lanes.includes('Kept post') && !lanes.includes('Removed post'));
  await p.keyboard.press('l'); await p.keyboard.press('d'); await p.keyboard.press('ArrowDown');
  check('no keyboard shortcut on the board', (await p.evaluate(() => window.__writes.length)) === 0);
  await p.click('text=Kept post'); await p.waitForTimeout(400);
  const sheet = await p.textContent('#sheet');
  check('a removed message is hidden', sheet.includes('first message') && !sheet.includes('removed message'));
  await p.keyboard.press('l'); await p.keyboard.press('Escape');
  check('no keyboard shortcut in a post', (await p.evaluate(() => window.__writes.length)) === 0);
  await p.click('#remove'); await p.waitForTimeout(300);
  const w = await p.evaluate(() => window.__writes);
  check('Remove on a post writes removed: true', w.length === 1 && w[0][0] === 'posts/P-a' && w[0][1].removed === true);
  await p.evaluate(() => { const a = document.getElementById('ask'); if (a) a.click(); }); await p.waitForTimeout(400);
  await p.locator('.discrow .rm').first().click(); await p.waitForTimeout(300);
  check('Remove on a discussion hides all its messages', (await p.evaluate(() => window.__writes.map(x => x[0]))).includes('comments/c3')
        && !(await p.textContent('#sheet')).includes('a discussion'));
  check('no page error', errs.length === 0);
  console.log(fails + ' failed'); await b.close(); process.exit(fails ? 1 : 0);
})();
