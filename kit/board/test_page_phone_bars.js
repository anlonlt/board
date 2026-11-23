// CL10 fix (the owner, 2026-11-23 02:34, on a phone picture): "Look at the off centered header bar, that doesn't
// stretch to the width ... why are there margins all around the header bar, instead of it fully filling the top".
// On a phone a window is a full sheet (docs/DESIGN-PRINCIPLES.md § 7) and nothing leaves the screen (§ 1).
// Checks, at 411 px (and 390 px): an open post's bar and the train task list's window and header span the screen
// edge to edge from the top, and no text starts left of the screen.
//   KIT_URL=http://127.0.0.1:8799 KIT_TOKEN=<token> KIT_POST=<post id> KIT_TRAIN=<train name> node kit/board/test_page_phone_bars.js [out-prefix]
// Playwright only (a phone with touch); without it: SKIP, never a pass.
let pw; try { pw = require('playwright'); } catch (e) { console.log('SKIP no Playwright'); process.exit(0); }
const URL0 = process.env.KIT_URL || 'http://127.0.0.1:8765', TOK = process.env.KIT_TOKEN || '';
const POST = process.env.KIT_POST, TRAIN = process.env.KIT_TRAIN || '';
let fails = 0;
const check = (name, ok) => { console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fails++; };
const span = (sel) => (p) => p.evaluate((s) => {
  const e = document.querySelector(s); if (!e) return null;
  const r = e.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), vw: innerWidth };
}, sel);
const fullWidth = (b) => b && b.l <= 0 && b.r >= b.vw;
// text that starts left of the screen, outside a box that scrolls sideways (a table or a picture row may)
const leftText = (p) => p.evaluate(() => {
  const out = [];
  const scrolls = (e) => { for (let x = e.parentElement; x; x = x.parentElement) { const o = getComputedStyle(x).overflowX; if ((o === 'auto' || o === 'scroll') && x.scrollWidth > x.clientWidth) return true; } return false; };
  document.querySelectorAll('body *').forEach((e) => {
    if (!e.offsetParent && getComputedStyle(e).position !== 'fixed') return;
    const own = Array.from(e.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) return;
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.left < -1 && r.right > 0 && !scrolls(e)) out.push((e.className || e.tagName) + ': ' + e.textContent.trim().slice(0, 40));
  });
  return out;
});
(async () => {
  if (!POST) { console.log('SKIP set KIT_POST'); process.exit(0); }
  const b = await pw.chromium.launch();
  for (const width of [411, 390]) {
    const ctx = await b.newContext({ viewport: { width, height: 860 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
    const p = await ctx.newPage();
    await p.goto(URL0 + '/?token=' + encodeURIComponent(TOK)); await p.waitForTimeout(1500);
    await p.goto(URL0 + '/#/post/' + encodeURIComponent(POST)); await p.waitForTimeout(2500);
    const bar = await span('#sheet .bar')(p);
    check(width + ' px: the post bar spans the screen edge to edge, from the top (' + JSON.stringify(bar) + ')', fullWidth(bar) && bar.t <= 0);
    const lt = await leftText(p);
    check(width + ' px: no text of the post starts left of the screen' + (lt.length ? ' (' + lt.slice(0, 3).join(' | ') + ')' : ''), lt.length === 0);
    if (process.argv[2]) await p.screenshot({ path: process.argv[2] + '-post-' + width + '.png' });
    await p.goto(URL0 + '/#/trains'); await p.waitForTimeout(2000);
    const opened = await p.evaluate((t) => {
      const bs = Array.from(document.querySelectorAll('.train-tl-btn')).filter((x) => x.offsetParent);
      const btn = bs.find((x) => !t || x.parentElement.parentElement.textContent.trim().toLowerCase().indexOf(t.toLowerCase()) === 0) || bs[0];
      if (btn) btn.click(); return !!btn;
    }, TRAIN);
    await p.waitForTimeout(700);
    if (opened) {
      const win = await span('#kit-tl-modal')(p), hdr = await span('#kit-tl-modal .kit-tl-hdr')(p);
      const radius = await p.evaluate(() => getComputedStyle(document.getElementById('kit-tl-modal')).borderTopLeftRadius);
      check(width + ' px: the task list is a full sheet from the top, square corners (' + JSON.stringify(win) + ', radius ' + radius + ')', fullWidth(win) && win.t <= 0 && parseFloat(radius) === 0);
      check(width + ' px: its header bar fills the top edge to edge (' + JSON.stringify(hdr) + ')', fullWidth(hdr) && hdr.t <= 1);
      const lt2 = await leftText(p);
      check(width + ' px: no text of the task list starts left of the screen' + (lt2.length ? ' (' + lt2.slice(0, 3).join(' | ') + ')' : ''), lt2.length === 0);
      if (process.argv[2]) await p.screenshot({ path: process.argv[2] + '-tasklist-' + width + '.png' });
    } else check(width + ' px: a train task list button exists', false);
    await ctx.close();
  }
  await b.close();
  console.log(fails + ' failed'); process.exit(fails ? 1 : 0);
})();
