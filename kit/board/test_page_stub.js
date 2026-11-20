// A stub of window.claude: an in-memory db with onSnapshot, enough for the board page.
(function () {
  const now = new Date().toISOString();
  const store = {
    posts: { "P-a": { title: "Kept post", column: "info", created_at: now, body: "hello", priority: 1 },
             "P-b": { title: "Removed post", column: "info", created_at: now, body: "x", removed: true },
             "_orchestrator": { title: "Ask the orchestrator", column: "info", created_at: now } },
    comments: { c1: { post_id: "P-a", from: "owner", text: "first message", created_at: now },
                c2: { post_id: "P-a", from: "orchestrator", text: "removed message", created_at: now, removed: true },
                c3: { post_id: "_orchestrator", from: "owner", text: "a discussion", created_at: now } },
    typing: {} };
  window.__writes = [];
  const subs = [];
  function snap(coll) { return { metadata: { fromCache: false }, docs: Object.entries(store[coll] || {}).map(([id, d]) => ({ id, data: () => d })) }; }
  function fire() { subs.forEach(([c, f]) => f(snap(c))); }
  const q = (coll) => ({ orderBy: () => q(coll), where: () => q(coll), limit: () => q(coll),
    onSnapshot: (f) => { subs.push([coll, f]); setTimeout(() => f(snap(coll)), 0); return () => {}; } });
  let n = 0;
  const docRef = (path) => ({ id: path.split("/").pop(),
    update: async (o) => { window.__writes.push([path, o]); }, set: async (o) => { window.__writes.push([path, o]); } });
  const coll = (c) => Object.assign(q(c), { doc: (id) => docRef(c + "/" + (id || "new" + (++n))),
    add: async (o) => { window.__writes.push([c + "/new" + (++n), o]); return { id: "new" + n }; } });
  const db = { collection: coll, doc: (path) => ({
    update: async (o) => { window.__writes.push([path, o]); const [c, id] = path.split("/"); if (store[c]) { store[c][id] = Object.assign({}, store[c][id], o); fire(); } },
    set: async (o) => { window.__writes.push([path, o]); }, onSnapshot: (f) => { setTimeout(() => f({ exists: false, metadata: { fromCache: false } }), 0); return () => {}; } }) };
  window.confirm = () => true;
  window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { isOwner: async () => true, id: async () => "u1" } : null };
})();
