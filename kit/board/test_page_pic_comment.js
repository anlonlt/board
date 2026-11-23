// CL10 fix (the owner, 2026-11-23 02:35): a comment made on a post's picture goes to that post (and its session),
// not to the orchestrator. Opens a post with pictures, taps a picture (the Image Viewer opens), taps the viewer's
// comment button, and checks the composer that opens is the post's, prefilled "Re `<picture>`". Then a comment
// started outside any post (the page header) still opens the orchestrator.
//   KIT_URL=http://127.0.0.1:8799 KIT_TOKEN=<token> KIT_POST=<post with images> node kit/board/test_page_pic_comment.js
// Playwright only; without it: SKIP, never a pass.
let pw; try { pw = require('playwright'); } catch (e) { console.log('SKIP no Playwright'); process.exit(0); }
const URL0 = process.env.KIT_URL || 'http://127.0.0.1:8765', TOK = process.env.KIT_TOKEN || '', POST = process.env.KIT_POST;
let fails = 0;
const check = (name, ok) => { console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fails++; };
(async () => {
  if (!POST) { console.log('SKIP set KIT_POST'); process.exit(0); }
  const b = await pw.chromium.launch();
  for (const [name, opt] of [['phone', pw.devices['iPhone 13']], ['computer', { viewport: { width: 1280, height: 800 } }]]) {
    const ctx = await b.newContext(opt); const p = await ctx.newPage();
    const errs = []; p.on('pageerror', (e) => errs.push(String(e)));
    await p.goto(URL0 + '/?token=' + encodeURIComponent(TOK)); await p.waitForTimeout(1500);
    await p.goto(URL0 + '/#/post/' + encodeURIComponent(POST)); await p.waitForTimeout(2500);
    const pic = await p.$('#p-imgs .pic img');
    check(name + ': the post has a picture', !!pic);
    if (!pic) { await ctx.close(); continue; }
    await pic.scrollIntoViewIfNeeded();
    if (name === 'phone') await pic.tap(); else await pic.click();
    await p.waitForTimeout(900);
    const cmt = await p.evaluateHandle(() => Array.from(document.querySelectorAll('.kit-docs-modal .k-vcmt')).find((x) => x.offsetParent && !x.hidden) || null);
    check(name + ': the Image Viewer shows its comment button', !!cmt.asElement());
    if (cmt.asElement()) {
      if (name === 'phone') await cmt.asElement().tap(); else await cmt.asElement().click();
      await p.waitForTimeout(700);
      const st = await p.evaluate(() => ({ hash: location.hash, text: (document.getElementById('c-text') || {}).value || '' }));
      check(name + ': the comment opens on the post itself (' + st.hash + ')', st.hash.indexOf('#/post/') === 0 && st.hash.indexOf('_orchestrator') < 0);
      check(name + ': prefilled with the picture it is about (' + JSON.stringify(st.text.slice(0, 50)) + ')', /^Re /.test(st.text));
    }
    // outside any post: the header, then a viewer comment, still goes to the orchestrator
    await p.goto(URL0 + '/#/lanes'); await p.waitForTimeout(1500);
    await p.evaluate(() => document.querySelectorAll('.kit-docs-modal').forEach((m) => { m.hidden = true; }));   // the viewer closed
    await p.locator('header.top h1').first().click({ force: true });
    await p.evaluate(() => window._kitOpenOrchNew('Re `README.md`:\n'));
    await p.waitForTimeout(600);
    const h2 = await p.evaluate(() => location.hash);
    check(name + ': a comment started outside any post still goes to the orchestrator (' + h2 + ')', h2.indexOf('_orchestrator') >= 0);
    check(name + ': no page error', errs.length === 0);
    await ctx.close();
  }
  await b.close();
  console.log(fails + ' failed'); process.exit(fails ? 1 : 0);
})();
