// The built Artifact page in Chromium with a stub window.claude (db with get/where/onSnapshot, assets, comments,
// downloads, user). Fails on any page error and checks what the adapter replaces.
//   KIT_PAGE=.kit/board.html node docs/claude-web/test_artifact_page.js
const { chromium } = require('playwright');
const fs = require('fs');

const STUB = `(function () {
  const now = new Date().toISOString();
  const store = {
    posts: { "P-a": { title: "A post", column: "info", created_at: now, body: "See docs/guide.md and commit abc1234def", priority: 1 },
             "_orchestrator": { title: "Ask the orchestrator", column: "info", created_at: now } },
    comments: { c1: { post_id: "P-a", from: "owner", text: "first", created_at: now } },
    kit_docs: { "_index": { docs: [{ path: "docs/guide.md", kind: "md", category: "docs", pinned: false, size: 20, mtime: now }],
                            assets: { "docs/pic.png": "/_blob/0123456789abcdef0123456789abcdef" }, mirrored_at: now },
                "docs+2F~guide.md": { path: "docs/guide.md", kind: "md", category: "docs", size: 20, mtime: now,
                                      headings: [{ level: 1, text: "Guide", id: "h-0-guide", slug: "guide" }], content: "# Guide\\nhello" } },
    kit_artifacts: { a1: { post_id: "P-a", path: "docs/guide.md", base_path: "docs/guide.md", summary: "better guide",
                           content: "# Guide\\nhello world", by_user: "victor", created_at: now } },
    kit_users: {}, typing: {} };
  window.__writes = []; window.__store = store;
  const subs = [];
  const docsOf = (c, ws) => Object.entries(store[c] || {}).filter(([id, d]) => (ws || []).every(([f, op, v]) => op === "==" ? d[f] === v : true));
  function snap(c, ws) { return { metadata: { fromCache: false }, docs: docsOf(c, ws).map(([id, d]) => ({ id, data: () => d })) }; }
  function fire() { subs.forEach(([c, ws, f]) => f(snap(c, ws))); }
  const q = (c, ws) => ({ orderBy: () => q(c, ws), where: (f, op, v) => q(c, (ws || []).concat([[f, op, v]])), limit: () => q(c, ws),
    get: async () => snap(c, ws),
    onSnapshot: (f) => { subs.push([c, ws, f]); setTimeout(() => f(snap(c, ws)), 0); return () => {}; } });
  let n = 0;
  const docRef = (path) => { const parts = path.split("/"); const id = parts.pop(); const c = parts.join("/");
    return { id,
      get: async () => { const d = (store[c] || {})[id]; return { id, exists: !!d, data: () => d, metadata: { fromCache: false } }; },
      set: async (o) => { window.__writes.push(["set", path, o]); (store[c] = store[c] || {})[id] = o; fire(); },
      update: async (o) => { window.__writes.push(["update", path, o]); (store[c] = store[c] || {})[id] = Object.assign({}, store[c][id], o); fire(); },
      delete: async () => { window.__writes.push(["delete", path]); if (store[c]) delete store[c][id]; fire(); },
      onSnapshot: (f) => { setTimeout(() => { const d = (store[c] || {})[id]; f({ id, exists: !!d, data: () => d, metadata: { fromCache: false } }); }, 0); return () => {}; } }; };
  const coll = (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))),
    add: async (o) => { const id = "new" + (++n); (store[c] = store[c] || {})[id] = o; fire(); return { id }; } });
  const db = { collection: coll, doc: docRef };
  window.__saved = [];
  window.confirm = () => true; window.prompt = (m, t) => { window.__prompted = t; return t; };
  const caps = { db,
    user: { isOwner: async () => true, id: async () => "u1", me: async () => ({ id: "u1", name: "Owner" }), canEdit: async () => true },
    assets: { upload: async () => ({ id: "f".repeat(32), url: "/_blob/" + "f".repeat(32) }) },
    comments: { sendToClaude: async () => ({ threadId: "t1" }), anchorFor: async () => ({}), canSendToClaude: async () => "available" },
    downloads: { save: async (f) => { window.__saved.push(f); } } };
  window.claude = { use: async (k) => caps[k] || null };
})();`;

(async () => {
  const html = fs.readFileSync(process.env.KIT_PAGE || '.kit/board.html', 'utf8')
    .replace('<script>', '<script>' + STUB + '</script><script>');
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1300, height: 900 } });
  const p = await ctx.newPage();
  const errors = [];
  p.on('pageerror', e => errors.push(String(e)));
  p.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await p.route('**/*', r => r.request().url().startsWith('http://kit.test') ? r.fulfill({ body: html, contentType: 'text/html; charset=utf-8' }) : r.abort());
  await p.goto('http://kit.test/');
  await p.waitForTimeout(1500);
  const res = {};
  res.status = await p.textContent('#status').catch(() => '');
  // the adapter, through the same api() the UI layer uses
  res.apiChecks = await p.evaluate(async () => {
    const out = {};
    // reach the adapter through a window hook the build does not expose: call it via the brief + db
    out.brief = (window.__KIT_AI_BRIEF__ || (() => ''))('P-a').slice(0, 60);
    return out;
  });
  // open a post, then the "Link for AI" button
  await p.evaluate(() => { const c = document.querySelector('.card .card-open'); if (c) c.click(); });
  await p.waitForTimeout(600);
  const hasAi = await p.$('#ai-share');
  if (hasAi) { await p.evaluate(() => document.getElementById('ai-share').click()); await p.waitForTimeout(500); }
  res.aiShare = await p.evaluate(() => ({ note: (document.getElementById('c-note') || {}).textContent || '', saved: window.__saved.map(f => f.filename), prompted: !!window.__prompted }));
  res.deliverables = await p.evaluate(() => !!document.getElementById('kit-post-artifacts'));
  // Repo Docs reader, fed by kit_docs/* through the adapter
  await p.evaluate(() => { const b = document.getElementById('back'); if (b) b.click(); });
  await p.waitForTimeout(300);
  await p.evaluate(() => { const d = document.getElementById('kit-docs-btn'); if (d) d.click(); });
  await p.waitForTimeout(800);
  res.docsReader = await p.evaluate(() => { const m = document.getElementById('kit-docs-modal'); return m ? (m.textContent.indexOf('guide.md') >= 0 ? 'lists docs/guide.md' : 'open, empty') : 'not opened'; });
  res.errors = errors.filter(e => !/net::ERR_FAILED|Failed to load resource/.test(e));
  console.log(JSON.stringify(res, null, 1));
  await p.screenshot({ path: process.env.SHOT || '/tmp/artifact-page.png' });
  await b.close();
  process.exit(res.errors.length ? 1 : 0);
})();
