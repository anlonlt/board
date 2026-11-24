// The built Artifact page in Chromium with a stub window.claude (db with get/where/onSnapshot, assets, comments,
// downloads, user, mcp). Fails on any page error and checks the adapter + pluggable MCP Link Reader (SharePoint & Gmail).
//   KIT_PAGE=.kit/board.html node kit/claude/test_artifact_page.js
// Runs in Playwright if installed, else in any Chrome/Edge/Brave/Chromium on the computer (no npm package needed);
// with no browser at all it only compiles the page's scripts and says SKIPPED (KIT_REQUIRE_BROWSER=1 makes that a failure).
const { launch, syntaxCheck } = require('./page_driver');
const fs = require('fs');

const STUB = `(function () {
  const now = new Date().toISOString();
  const store = {
    posts: {
      "P-a": {
        title: "A post", column: "info", created_at: now, priority: 1,
        body: "See docs/guide.md, commit abc1234def, [Budget](https://contoso.sharepoint.com/sites/fin/Shared%20Documents/Budget.xlsx) and [Thread](https://mail.google.com/mail/u/0/#inbox/FMfcgzQXJwThread123)." +
          "\\n\\n\\u0060\\u0060\\u0060mermaid\\nflowchart LR\\n  A[\\"Session\\"] --> B[\\"Post\\"]\\n\\u0060\\u0060\\u0060\\n\\n![pic](docs/pic.png)\\n\\n" +
          "\\u0060\\u0060\\u0060svg\\n<svg viewBox='0 0 10 10'><rect width='10' height='10'/></svg>\\n\\u0060\\u0060\\u0060"
      },
      "_orchestrator": { title: "Ask the orchestrator", column: "info", created_at: now }
    },
    comments: { c1: { post_id: "P-a", from: "owner", text: "first", created_at: now } },
    kit_docs: {
      "_index": {
        docs: [{ path: "docs/guide.md", kind: "md", category: "docs", pinned: false, size: 20, mtime: now }],
        assets: { "docs/pic.png": "/_blob/0123456789abcdef0123456789abcdef" }, mirrored_at: now
      },
      "docs+2F~guide.md": {
        path: "docs/guide.md", kind: "md", category: "docs", size: 20, mtime: now,
        headings: [{ level: 1, text: "Guide", id: "h-0-guide", slug: "guide" }], content: "# Guide\\nhello"
      }
    },
    kit_artifacts: {
      a1: { post_id: "P-a", path: "docs/guide.md", base_path: "docs/guide.md", summary: "better guide",
            content: "# Guide\\nhello world", by_user: "victor", created_at: now }
    },
    kit_users: {}, typing: {}
  };
  window.__writes = []; window.__store = store; window.__mcp = [];
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
  const mcp = {
    callTool: async (srv, tool, args) => {
      window.__mcp.push([srv, tool, args]);
      if (tool === "sharepoint_search") {
        const h1 = JSON.stringify({ uri: "sp://wrong", name: "Budget.xlsx", webUrl: "https://contoso.sharepoint.com/sites/other/Budget.xlsx" });
        const h2 = JSON.stringify({ uri: "sp://right", name: "Budget.xlsx", webUrl: "https://contoso.sharepoint.com/sites/fin/Shared%20Documents/Budget.xlsx", lastModifiedDateTime: now });
        return { content: [{ type: "text", text: h1 + "\\n" + h2 }] };
      }
      if (tool === "read_resource") {
        return { content: [{ type: "text", text: args.startPage === 2 ? "Page two body" : "Page one body\\nre-call with startPage=2" }] };
      }
      if (tool === "gmail_read_thread") {
        return { content: [{ type: "text", text: JSON.stringify({ subject: "Q4 Budget Review", messages: [{ from: "alice@example.com", body: "Looks good!" }] }) }] };
      }
      return { content: [{ type: "text", text: "" }] };
    }
  };
  const caps = { db, mcp,
    user: { isOwner: async () => true, id: async () => "u1", me: async () => ({ id: "u1", name: "Owner" }), canEdit: async () => true },
    assets: { upload: async () => ({ id: "f".repeat(32), url: "/_blob/" + "f".repeat(32) }) },
    comments: { sendToClaude: async () => ({ threadId: "t1" }), anchorFor: async () => ({}), canSendToClaude: async () => "available" },
    downloads: { save: async (f) => { window.__saved.push(f); } } };
  window.claude = { use: async (k) => caps[k] || null };
})();`;

(async () => {
  const html = fs.readFileSync(process.env.KIT_PAGE || '.kit/board.html', 'utf8')
    .replace('<script>', '<script>' + STUB + '</script><script>');
  const syn = syntaxCheck(html);
  const p = await launch();
  if (!p) {
    const out = { driver: 'none', scripts: syn.scripts, errors: syn.errors,
      skipped: 'no Playwright and no Chrome/Edge/Brave/Chromium found (set KIT_BROWSER=<path>): scripts compiled, nothing executed' };
    console.log(JSON.stringify(out, null, 1));
    process.exit(syn.errors.length || process.env.KIT_REQUIRE_BROWSER === '1' ? 1 : 0);
  }
  const res = { driver: p.kind };
  await p.open(html);
  await p.wait(1500);
  res.status = await p.evaluate(() => { const s = document.getElementById('status'); return s ? s.textContent : ''; });
  await p.evaluate(() => { const c = document.querySelector('.card .card-open'); if (c) c.click(); });
  await p.wait(600);
  const hasAi = await p.evaluate(() => !!document.getElementById('ai-share'));
  if (hasAi) { await p.evaluate(() => document.getElementById('ai-share').click()); await p.wait(500); }
  res.aiShare = await p.evaluate(() => ({ note: (document.getElementById('c-note') || {}).textContent || '', saved: window.__saved.map(f => f.filename), prompted: !!window.__prompted }));
  res.deliverables = await p.evaluate(() => !!document.getElementById('kit-post-artifacts'));
  // CL36: the post body draws a mermaid block, an inline picture (mapped to its uploaded asset) and an svg block, as on the local page
  await p.wait(400);
  res.cl36 = await p.evaluate(() => { const b = document.getElementById('p-body'); if (!b) return 'no body';
    const pic = b.querySelector('img.md-img');
    return { mermaid: b.querySelectorAll('.kit-mmd-box .kit-mmd-row').length, pic: pic ? pic.getAttribute('src') : '', svg: !!b.querySelector('.kit-svg-box img') }; });
  if (!(res.cl36 && res.cl36.mermaid === 1 && /^\/_blob\/0123456789abcdef/.test(res.cl36.pic) && res.cl36.svg)) syn.errors.push('CL36: post body diagrams/pictures not drawn: ' + JSON.stringify(res.cl36));
  // Check disarmed SharePoint + Gmail links (no href attribute)
  res.disarmed = await p.evaluate(() => [...document.querySelectorAll('#p-body a[data-mcp-href]')].map(a => ({ adapter: a.getAttribute('data-mcp-adapter'), hasHref: a.hasAttribute('href') })));
  await p.evaluate(() => { const a = document.querySelector('#p-body a[data-mcp-adapter="sharepoint"]'); if (a) a.click(); });
  await p.wait(500);
  res.sharepoint1 = await p.evaluate(() => { const w = document.getElementById('kit-mcp-win'); return w ? w.querySelector('pre').textContent.slice(0, 15) : 'no window'; });
  await p.evaluate(() => { const b = [...document.querySelectorAll('#kit-mcp-win button')].find(x => /Page 2/.test(x.textContent)); if (b) b.click(); });
  await p.wait(400);
  res.sharepoint2 = await p.evaluate(() => { const w = document.getElementById('kit-mcp-win'); return w ? w.querySelector('pre').textContent : 'no window'; });
  await p.evaluate(() => { const a = document.querySelector('#p-body a[data-mcp-adapter="gmail"]'); if (a) a.click(); });
  await p.wait(500);
  res.gmail = await p.evaluate(() => { const w = document.getElementById('kit-mcp-win'); return w ? { title: w.querySelector('b').textContent, hasBody: /Looks good/.test(w.querySelector('pre').textContent) } : 'no window'; });
  res.mcpCalls = await p.evaluate(() => (window.__mcp || []).map(c => c[0] + ':' + c[1]));
  // Check in-page confirm() dialog on Remove from the board
  await p.evaluate(() => { const w = document.getElementById('kit-mcp-win'); if (w) w.remove(); });
  await p.evaluate(() => { const b = document.getElementById('rm'); if (b) b.click(); });
  await p.wait(300);
  res.confirmDialog = await p.evaluate(() => !!document.getElementById('kit-dlg'));
  await p.evaluate(() => { const ok = document.querySelector('#kit-dlg button.ember'); if (ok) ok.click(); });
  await p.wait(400);
  res.removedWrite = await p.evaluate(() => (window.__writes || []).some(w => w[1] === 'posts/P-a' && w[2] && w[2].removed === true));
  res.errors = syn.errors.concat(p.errors.filter(e => !/net::ERR_|Failed to load resource/.test(e)));
  console.log(JSON.stringify(res, null, 1));
  await p.close();
  process.exit(res.errors.length ? 1 : 0);
})();
