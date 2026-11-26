// CL36 (the owner, 2026-11-24: "the board posts can show inline pictures and diagrams, no ... ?", "What was wrong with
// the SVG that they included in the post then?"): a post body draws what the document viewer draws, with its renderer:
// a ```mermaid block, a ```svg block, an inline ![picture](repo path) and an .svg by its path; a post that still embeds
// a docs file by section (`docs/x.md § S`, every part before CL36) still shows that section inline.
//   node kit/board/test_page_body.js          (KIT_KIT=<another checkout> runs it on that checkout's page;
//   KIT_INSTALL=<project> on a project's installed page and server, <project>/.kit/board.html and board_server.py)
// Offline: a board server on a throwaway root, its posts seeded. Playwright only; without it: SKIP, never a pass.
let pw; try { pw = require('playwright'); } catch (e) { console.log('SKIP no Playwright'); process.exit(process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0); }
const fs = require('fs'), os = require('os'), path = require('path'), http = require('http'), net = require('net');
const { spawn } = require('child_process');
const KIT = process.env.KIT_KIT || path.join(__dirname, '..', '..');
const INST = process.env.KIT_INSTALL ? path.join(process.env.KIT_INSTALL, '.kit') : '';
const PAGE = INST ? path.join(INST, 'board.html') : path.join(KIT, 'kit', 'board', 'board.template.html');
const SERVER = INST ? path.join(INST, 'board_server.py') : path.join(KIT, 'kit', 'board', 'board_server.py');

let fails = 0;
const check = (n, ok, more) => { console.log((ok ? 'ok   ' : 'FAIL ') + n + (ok || more === undefined ? '' : '\n       ' + JSON.stringify(more))); fails += !ok; };
const freePort = () => new Promise((r) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
const req = (port, method, p, body, hdrs) => new Promise((res, rej) => {
  const d = body ? Buffer.from(JSON.stringify(body)) : null;
  const q = http.request({ host: '127.0.0.1', port, method, path: p, headers: Object.assign(d ? { 'content-type': 'application/json', 'content-length': d.length } : {}, hdrs || {}) }, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => res([r.statusCode, b])); });
  q.on('error', rej); if (d) q.write(d); q.end();
});

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40" width="120" height="40"><rect x="1" y="1" width="118" height="38" rx="6" fill="#eee" stroke="#333"/><text x="60" y="25" text-anchor="middle" font-size="14">flow</text></svg>';

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kit-body-'));
  fs.mkdirSync(path.join(root, '.kit'));
  const tpl = fs.readFileSync(PAGE, 'utf8').replace(/\{\{[A-Z_]+\}\}/g, 'Test');
  fs.writeFileSync(path.join(root, '.kit', 'board.html'), tpl);
  fs.writeFileSync(path.join(root, '.kit', 'config.json'), JSON.stringify({ project: 'Test' }));
  fs.mkdirSync(path.join(root, 'docs'));
  fs.writeFileSync(path.join(root, 'docs', 'pic.png'), Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64'));
  fs.writeFileSync(path.join(root, 'docs', 'flow.svg'), SVG);
  fs.writeFileSync(path.join(root, 'docs', 'old.md'), '# Old\n\n## The flow\n\nText of the old section.\n');
  const port = await freePort();
  const env = Object.assign({}, process.env); delete env.KIT_BOARD_TOKEN;
  const srv = spawn('python3', ['-I', SERVER, '--host', '127.0.0.1', '--port', String(port), '--root', root], { env, stdio: 'ignore' });
  let up = false; for (let i = 0; i < 200 && !up; i++) { try { up = (await req(port, 'GET', '/_api/health'))[0] === 200; } catch (e) { await new Promise((r) => setTimeout(r, 150)); } }
  if (!up) { console.log('FAIL the board server did not start'); srv.kill(); process.exit(1); }
  const at = new Date().toISOString();
  // seeded as a copy of a board (X-Kit-Gate: copy): the proof gate is for live writes, and a refused seed must stop the test, not drop a post
  const put = async (c, id, data) => { const r = await req(port, 'PUT', '/_api/db/' + c + '/' + id, { data }, { 'X-Kit-Gate': 'copy' }); if (r[0] >= 300) throw new Error('seed ' + id + ' refused: ' + r[0] + ' ' + r[1]); return r; };
  await put('posts', '_orchestrator', { title: 'Ask the orchestrator', column: 'info', created_at: at });
  await put('posts', 'cl9-new', { title: 'CL9 a result in its post', column: 'info', status: 'complete', created_at: at, verdict: 'Shown in the post.',
    body: 'The lead.\n\n## The flow\n\n```mermaid\nflowchart LR\n  A["Session"] --> B["Board post"]\n  B -->|"he reads"| C["Owner"]\n```\n\n' +
          '## A picture\n\n![the picture](docs/pic.png)\n\n![the flow, as a picture](docs/flow.svg)\n\nNot a picture: `![x](docs/pic.png)` stays text.\n\n## An SVG written here\n\n```svg\n' + SVG + '\n```\n\n## An SVG file\n\n`docs/flow.svg`\n' });
  await put('posts', 'cl8-old', { title: 'CL8 an older post that embeds a docs file', column: 'info', status: 'complete', created_at: at, verdict: 'Old way.',
    body: 'Lead.\n\n## The flow\n\n`docs/old.md § The flow`\n' });

  const b = await pw.chromium.launch();
  for (const [w, h, mobile] of [[390, 844, true], [1280, 900, false]]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 1 });
    const p = await ctx.newPage(); const errs = []; p.on('pageerror', (e) => errs.push(String(e)));
    const tag = mobile ? 'phone' : 'desktop';
    await p.goto('http://127.0.0.1:' + port + '/#/post/cl9-new');
    // drawn, not a fixed sleep: on a busy computer 2.2 s was not enough (its pictures, diagram and svg each load on their own)
    await p.waitForFunction(() => { const b = document.querySelector('#p-body, .kit-post-win-body'); if (!b) return false;
      const ok = (i) => i && i.complete && i.naturalWidth > 0; const pics = [...b.querySelectorAll('img.md-img')];
      return b.querySelectorAll('.kit-mmd-row').length >= 2 && pics.length >= 2 && pics.every(ok) && ok(b.querySelector('.kit-svg-box img'))
        && [...b.querySelectorAll('.kit-inline-embed[data-kind="img"] img')].some(ok); }, null, { timeout: 20000 }).catch(() => {});
    const r = await p.evaluate(() => {
      const box = document.querySelector('#p-body, .kit-post-win-body'); if (!box) return { noBody: true };
      box.querySelectorAll('details').forEach((d) => { d.open = true; });
      const pic = [...box.querySelectorAll('img.md-img')][0];
      const svgImg = box.querySelector('.kit-svg-box img');
      const mmd = box.querySelector('.kit-mmd-box');
      const W = document.documentElement.clientWidth;
      return { mmdRows: mmd ? mmd.querySelectorAll('.kit-mmd-row').length : 0, mmdText: mmd ? mmd.textContent : '',
        rawFence: /```|flowchart LR/.test(box.textContent),
        pics: box.querySelectorAll('img.md-img').length, picSrc: pic ? pic.getAttribute('src') : '', picLoaded: pic ? pic.complete && pic.naturalWidth > 0 : false,
        codeKept: [...box.querySelectorAll('code')].some((c) => c.textContent === '![x](docs/pic.png)'),
        svgLoaded: svgImg ? svgImg.complete && svgImg.naturalWidth > 0 : false,
        svgFileEmbed: [...box.querySelectorAll('.kit-inline-embed[data-kind="img"] img')].some((i) => i.complete && i.naturalWidth > 0),
        wide: [...box.querySelectorAll('img, .kit-mmd-box, .kit-svg-box')].filter((e) => e.getBoundingClientRect().right > W + 1).length };
    });
    check(tag + ': a ```mermaid block in a post body draws as a diagram (2 edges), not as code', r.mmdRows === 2 && /Board post/.test(r.mmdText) && !r.rawFence, r);
    check(tag + ': ![picture](docs/pic.png) in a post body shows the picture inline', r.pics === 2 && /repo-asset\?path=docs%2Fpic\.png/.test(r.picSrc) && r.picLoaded, r);
    check(tag + ': a picture written inside a code span stays text', r.codeKept, r);
    check(tag + ': a ```svg block in a post body draws as a picture', r.svgLoaded, r);
    check(tag + ': an .svg named by its path shows inline', r.svgFileEmbed, r);
    check(tag + ': nothing drawn wider than the screen', r.wide === 0, r);
    if (process.env.KIT_SHOT && !mobile) await p.screenshot({ path: process.env.KIT_SHOT, fullPage: true });   // `shot`: the post as he sees it
    await p.goto('http://127.0.0.1:' + port + '/#/post/cl8-old');
    await p.waitForFunction(() => { const b = document.querySelector('#p-body, .kit-post-win-body'); return !!(b && b.querySelector('.kit-inline-embed[data-kind="md"]')); }, null, { timeout: 20000 }).catch(() => {});
    const o = await p.evaluate(() => { const box = document.querySelector('#p-body, .kit-post-win-body'); const e = box && box.querySelector('.kit-inline-embed[data-kind="md"]');
      return { embed: !!e, text: e ? e.textContent : '' }; });
    await p.waitForFunction(() => { const e = document.querySelector('.kit-inline-embed[data-kind="md"]'); return e && /Text of the old section/.test(e.textContent); }, null, { timeout: 20000 }).catch(() => {});
    const o2 = await p.evaluate(() => { const e = document.querySelector('.kit-inline-embed[data-kind="md"]'); return e ? e.textContent : ''; });
    check(tag + ': an older post that embeds `docs/old.md § The flow` still shows that section inline', o.embed && /Text of the old section/.test(o2), { o, o2: o2.slice(0, 120) });
    check(tag + ': no page error', !errs.length, errs);
    await ctx.close();
  }
  await b.close(); srv.kill();
  fs.rmSync(root, { recursive: true, force: true });
  console.log(fails ? fails + ' failed' : 'all passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL', e); process.exit(1); });
