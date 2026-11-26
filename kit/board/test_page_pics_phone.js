// CL10 fix (the owner, 2026-11-23 02:22, from his phone): a post's pictures show, and a picture row grows when its
// bottom edge is dragged down with a finger. Runs against a board server with a post that has image_groups:
//   KIT_URL=http://127.0.0.1:8799 KIT_TOKEN=<token> KIT_POST=<post id> node kit/board/test_page_pics_phone.js [out.png]
// Playwright only (it emulates a phone with touch); without it: SKIP, never a pass.
let pw; try { pw = require('playwright'); } catch (e) { console.log('SKIP no Playwright'); process.exit(0); }
const URL0 = process.env.KIT_URL || 'http://127.0.0.1:8765', TOK = process.env.KIT_TOKEN || '', POST = process.env.KIT_POST;
let fails = 0;
const check = (name, ok) => { console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fails++; };
(async () => {
  if (!POST) { console.log('SKIP set KIT_POST'); process.exit(0); }
  const b = await pw.chromium.launch(); const { settle } = require('./test_settle'); require('./test_settle').arm(b);   // waits for the page, never a fixed time
  const ctx = await b.newContext(pw.devices['iPhone 13']); const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(URL0 + '/?token=' + encodeURIComponent(TOK)); await settle(p);
  await p.goto(URL0 + '/#/post/' + encodeURIComponent(POST)); await settle(p);
  const pics = await p.evaluate(() => Array.from(document.querySelectorAll('#p-imgs img')).map(i => i.complete && i.naturalWidth > 0));
  check('the post shows its pictures (' + pics.filter(Boolean).length + ' of ' + pics.length + ' loaded)', pics.length > 0 && pics.every(Boolean));
  const grip = await p.$('#p-imgs .kit-rs-grip');
  check('each picture row has a bottom-edge grip', !!grip);
  if (grip) {
    await grip.scrollIntoViewIfNeeded(); await settle(p);
    const before = await p.evaluate(() => { const r = document.querySelector('#p-imgs .grp-row, #p-imgs .pic'); const b = r.getBoundingClientRect(); return { h: b.height, w: r.scrollWidth }; });
    const g = await grip.boundingBox();
    const x = g.x + g.width / 2, y = g.y + g.height / 2;
    const cdp = await ctx.newCDPSession(p);
    const touch = (type, yy) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y: yy }] });
    await touch('touchStart', y);
    for (let k = 1; k <= 8; k++) { await touch('touchMove', y + k * 20); await p.waitForTimeout(40); }
    await touch('touchEnd', y + 160); await settle(p);
    const after = await p.evaluate(() => { const r = document.querySelector('#p-imgs .grp-row, #p-imgs .pic'); const b = r.getBoundingClientRect(); return { h: b.height, w: r.scrollWidth, docW: document.documentElement.scrollWidth, vw: innerWidth }; });
    check('a finger dragging the bottom edge down 160 px grows the row (height ' + Math.round(before.h) + ' -> ' + Math.round(after.h) + ' px)', after.h > before.h * 1.3);
    check('the page itself never scrolls sideways (' + after.docW + ' of ' + after.vw + ' px)', after.docW <= after.vw);
    if (process.argv[2]) await p.screenshot({ path: process.argv[2] });
    // the grip moved down with the row: bring it back on screen and start from where it is now (on an iPhone 13's
    // 664 px it was at 712, below the screen, and the touch missed it)
    await grip.scrollIntoViewIfNeeded(); await settle(p);
    const g2 = await grip.boundingBox(), y2 = g2.y + g2.height / 2;
    const touch2 = (type, yy) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x: g2.x + g2.width / 2, y: yy }] });
    await touch2('touchStart', y2);
    for (let k = 1; k <= 8; k++) { await touch2('touchMove', y2 - k * 20); await p.waitForTimeout(40); }
    await touch2('touchEnd', y2 - 160); await settle(p);
    const back = await p.evaluate(() => document.querySelector('#p-imgs .grp-row, #p-imgs .pic').getBoundingClientRect().height);
    check('dragging it back up shrinks it again (' + Math.round(back) + ' px)', back < after.h * 0.85);
  }
  check('no page error', errs.length === 0);
  await b.close();
  console.log(fails + ' failed'); process.exit(fails ? 1 : 0);
})();
