"""Who may do what on a local board: one place, for every route (board_server.py asks it, deny by default).

People: the owner (the board's own token, or a person with the role owner: full rights, the same as the owner), a member
(reads and writes a board, as themself) and a viewer (reads it). A guest has a 24 h link to one card (`/_share/`).

- A route that is not in ROUTES does not exist for anyone but the owner.
- A member or viewer reads and writes through a View of one board: the one they were invited to ("all" when the owner
  invited them to all of them; nothing said: the server's own board; a board not attached now: none).
- The owner's private channel (posts/_orchestrator) and everyone else's are not there: each person has their own.
- Whoever writes is named by the server: `by` always, `from` on comments. Nobody writes as the owner, an agent or
  another person. Only the person a post is for answers it; only the owner's side moves the rest of it.
- The gates an agent's command line applies (post_gate.py) are applied here to a member's writes.
- The Artifact has none of this: there, whoever claude.ai lets edit can write anything."""
import re

RANKS = {"viewer": 1, "member": 2, "owner": 3}

# (method, path, lowest role) for people other than the owner.
ROUTES = [(m, re.compile("^(?:%s)$" % p), r) for m, p, r in (
    ("GET", r"/|/index\.html|/_shim\.js", "viewer"),
    ("GET", r"/_api/(?:me|repos|repo-docs|repo-doc|repo-asset|changes|links|export|events|git/diff)", "viewer"),
    ("GET", r"/_api/client/board_cli\.py", "viewer"),
    ("GET", r"/_api/db/[^/]+(?:/[^/]+)?", "viewer"),
    ("GET", r"/_api/doc/.+", "viewer"),
    ("GET", r"/_api/comments/status", "viewer"),
    ("GET", r"/_api/posts/[^/]+/artifacts|/_api/artifacts/[^/]+", "viewer"),
    ("GET", r"/_blob/.+|/_repo/.+", "viewer"),
    ("GET", r"/_api/comments/threads/[^/]+", "member"),
    ("GET", r"/_api/shares", "member"),
    ("PUT", r"/_api/doc/.+|/_api/db/[^/]+/[^/]+", "member"),
    ("PATCH", r"/_api/doc/.+|/_api/db/[^/]+/[^/]+", "member"),
    ("DELETE", r"/_api/shares/[^/]+", "member"),
    ("POST", r"/_api/blobs|/_api/shares|/_api/comments/send|/_api/db/batch|/_api/posts/[^/]+/artifacts", "member"),
    ("POST", r"/_api/db/[^/]+/[^/]+/str_replace", "member"),
)]

# Names nobody can carry: they mean something else on a board.
RESERVED = {"owner", "all", "system", "viewer", "clerk", "orchestrator", "consultant", "reviewer", "agent", "guest", "kit",
            "external-ai", "you", "me"}

POST_FIELDS = {"title", "body", "column", "priority", "images", "captions", "files", "minutes", "options", "default_option",
               "default", "deadline", "tags", "links", "status", "working_by", "for", "assignee", "assigned_to", "agent_meta",
               "parent", "created_at", "updated_at", "done_at", "column_before", "snoozed_until", "removed", "badges", "url",
               "discussions", "answer", "artifacts"}
FOR_FIELDS = {"for", "assignee", "assigned_to"}
FOR_ME_FIELDS = FOR_FIELDS | {"column", "column_before", "done_at", "status", "working_by", "updated_at", "answer", "snoozed_until"}
COMMENT_FIELDS = {"post_id", "text", "created_at", "updated_at", "reply_to", "thread_id", "image", "images", "files", "like",
                  "comment_id", "answer", "agent_meta", "delivered", "delivery_error", "arrived_at", "disc", "discussion",
                  "quote", "section", "removed", "sent_at"}
OWN_COMMENT_FIELDS = COMMENT_FIELDS - {"post_id", "answer", "created_at"}


class Denied(Exception):
    def __init__(self, why, code=403):
        super().__init__(why)
        self.why, self.code = why, code


def full(user):
    return bool(user) and user.get("role") == "owner"


def route_role(method, path):
    """The lowest role a person needs for this route, or None: the owner's alone."""
    roles = [r for m, rx, r in ROUTES if m == method and rx.match(path)]
    return min(roles, key=RANKS.get) if roles else None


def may_call(user, method, path):
    if full(user):
        return True
    need = route_role(method, path)
    return bool(need) and RANKS.get(str((user or {}).get("role")), 0) >= RANKS[need]


def boards(user, stores, primary):
    """The boards this person may open, the first one by default."""
    if full(user):
        return [primary] + [k for k in stores if k != primary]
    repo = str(user.get("repo") or "").strip()
    if repo == "all":
        return [primary] + [k for k in stores if k != primary]
    repo = repo or primary
    return [repo] if repo in stores else []


def guest_name(raw, taken=()):
    """The name a guest writes under: never a person's, an agent's or a reserved one."""
    n = re.sub(r"[^A-Za-z0-9._-]", "", str(raw or ""))[:32] or "external-ai"
    low = n.lower()
    if low != "external-ai" and (low in RESERVED or low in {str(t).lower() for t in taken}
                                 or low.startswith(("owner", "orchestrator", "clerk", "kit", "session_"))):
        return ("guest-" + n)[:32]
    return n


def file_ok(rel):
    """A file of the repo someone other than the owner may open: never the kit's, the agents' or git's own folders."""
    parts = [x for x in str(rel or "").replace("\\", "/").split("/") if x and x != "."]
    return not any(x in (".git", ".kit", ".claude", ".agents", ".gemini", ".ssh") or x.startswith(".env") for x in parts)


def private_channel(doc_id):
    d = str(doc_id or "")
    return d == "_orchestrator" or d.startswith("_orchestrator_")


class View:
    """A BoardStore as one person sees it: the same calls, only what is theirs to read, only what is theirs to write."""

    def __init__(self, store, user, gate=None, cfg=None, now=None):
        self.store, self.user, self.gate, self.cfg = store, user, gate, cfg or {}
        self.uid = str(user.get("id") or "")
        self.mine = "_orchestrator_" + self.uid
        self.now = now or (lambda: "")

    # ---- names: "_orchestrator" is each person's own channel ----
    def _in(self, coll, doc_id):
        return self.mine if coll == "posts" and str(doc_id) == "_orchestrator" else doc_id

    def _hidden(self, coll, doc_id, data):
        doc_id, data = str(doc_id or ""), data if isinstance(data, dict) else {}
        if coll == "posts":
            return private_channel(doc_id) and doc_id != self.mine
        if coll == "comments":
            pid = str(data.get("post_id") or "")
            return private_channel(pid) and pid != self.mine
        if coll.startswith("data/users/"):
            return coll.split("/")[2] != self.uid
        return coll not in ("typing", "activity")

    def _out(self, coll, doc_id, data):
        if coll == "posts" and doc_id == self.mine:
            return "_orchestrator", data
        if coll == "comments" and isinstance(data, dict) and data.get("post_id") == self.mine:
            return doc_id, dict(data, post_id="_orchestrator")
        return doc_id, data

    def ensure_channel(self):
        if not self.store.get_doc("posts", self.mine).get("exists"):
            self.store.set_doc("posts", self.mine, {
                "title": "Ask the orchestrator (@%s)" % self.uid, "column": "info", "part": "orchestrator", "priority": 3,
                "owner_user": self.uid, "created_at": self.now(), "body": "Private orchestrator channel for @%s." % self.uid})

    # ---- reads ----
    def get_doc(self, coll, doc_id):
        rid = self._in(coll, doc_id)
        if coll == "posts" and rid == self.mine:
            self.ensure_channel()
        d = self.store.get_doc(coll, rid)
        if not d.get("exists"):
            if self._hidden(coll, rid, {}) and coll != "comments":
                raise Denied("not yours to read")
            return d
        if self._hidden(coll, rid, d.get("data")):
            raise Denied("not yours to read")
        oid, data = self._out(coll, rid, d.get("data"))
        return dict(d, data=data, **({"id": oid} if "id" in d or oid != rid else {}))

    def list_docs(self, coll, where=None, order_by=None, direction="asc", limit=None, cursor=0):
        if coll == "posts":
            self.ensure_channel()
        if where and coll == "comments":
            where = [[w[0], w[1], self.mine if w[0] == "post_id" and w[2] == "_orchestrator" else w[2]] if len(w) == 3 else w for w in where]
        got = self.store.list_docs(coll, where=where, order_by=order_by, direction=direction)["docs"]
        docs = []
        for d in got:
            if not self._hidden(coll, d["id"], d.get("data")):
                oid, data = self._out(coll, d["id"], d.get("data"))
                docs.append(dict(d, id=oid, data=data))
        start, n = max(0, int(cursor or 0)), len(docs)
        page = docs[start:] if limit is None else docs[start:start + max(1, int(limit))]
        more = limit is not None and start + max(1, int(limit)) < n
        return {"docs": page, "next_cursor": str(start + max(1, int(limit))) if more else None, "total": n}

    def list_changes(self, since=0, limit=1000):
        res = self.store.list_changes(since=since, limit=limit)
        ok = []
        for c in res["changes"]:
            coll, did = c["collection"], c["doc_id"]
            if coll == "comments":
                d = self.store.get_doc(coll, did)
                hide = not d.get("exists") or self._hidden(coll, did, d.get("data"))
            else:
                hide = self._hidden(coll, did, {})
            if not hide:
                ok.append(dict(c, doc_id=self._out(coll, did, {})[0]))
        return dict(res, changes=ok)

    def list_links(self, train=None):
        return self.store.list_links(train=train)

    def comment_counts(self):
        counts = {}
        for pid, v in self.store.comment_counts()["counts"].items():
            if pid == self.mine:
                counts["_orchestrator"] = v
            elif not private_channel(pid):
                counts[pid] = v
        return {"counts": counts}

    def list_artifacts(self, post_id=None):
        return [] if private_channel(post_id) and post_id not in ("_orchestrator", self.mine) else self.store.list_artifacts(post_id=self._in("posts", post_id))

    def get_artifact(self, aid):
        a = self.store.get_artifact(aid)
        return None if a and private_channel(a.get("post_id")) and a.get("post_id") != self.mine else a

    def get_thread(self, tid):
        t = self.store.get_thread(tid)
        return t if t and t.get("by") == self.uid else None

    def sees_event(self, coll, doc_id):
        """A change's name on the live feed: it carries no content, but other people's documents are not named."""
        if coll.startswith("data/users/"):
            return coll.split("/")[2] == self.uid
        return not (coll == "posts" and private_channel(doc_id) and doc_id != self.mine)

    # ---- writes ----
    def _member(self):
        if RANKS.get(str(self.user.get("role")), 0) < RANKS["member"]:
            raise Denied("a viewer reads only")

    def _is_mine(self, d):
        return bool(self.uid) and str((d or {}).get("by") or "") == self.uid

    def _post(self, doc_id, new, old, patch):
        """The document or patch to write for a post, or Denied."""
        if not isinstance(new, dict):
            raise Denied("a post is an object", 400)
        did = str(doc_id)
        if did.startswith("_") and did != self.mine:
            raise Denied("%s is the kit's" % did)
        exists = old is not None
        old = old or {}
        new = dict(new)
        for k in ("from", "by"):
            new.pop(k, None)
        changed = {k for k in new if new.get(k) != old.get(k)}
        if not patch:
            changed |= {k for k in old if k not in new and k not in ("from", "by", "updated_at")}
        own = not exists or self._is_mine(old) or did == self.mine
        person = str(old.get("for") or old.get("assignee") or old.get("assigned_to") or "")
        allowed = POST_FIELDS if own else FOR_ME_FIELDS if person == self.uid else {"working_by", "updated_at"}
        bad = sorted(changed - allowed)
        if bad:
            raise Denied("not yours to change on this post: " + ", ".join(bad[:6]))
        if "working_by" in changed:
            held, to = str(old.get("working_by") or ""), str(new.get("working_by") or "")
            if not (held in ("", self.uid) and to in ("", self.uid)):
                raise Denied("@%s is working on it" % held if held not in ("", self.uid) else "claim it for yourself only", 409 if held not in ("", self.uid) else 403)
        if not own:
            # whoever a post is for may answer it: nobody makes it theirs, they can only hand it back
            if any(k in changed and str(new.get(k) or "") for k in FOR_FIELDS):
                raise Denied("only the owner assigns a post; take it with working_by")
        if self.gate is None and (old.get("train") or new.get("train")) and not did.startswith("_") and "status" in changed \
                and str(new.get("status") or "").strip().lower() in ("complete", "completed", "done"):
            raise Denied("a step is completed by the owner's side")          # a kit with no proof gate to ask
        if self.gate is not None and not did.startswith("_"):
            put, _, err = self.gate.check_post(did, dict(new, **{"from": self.uid}), old if exists else None, None)
            if err and "default" in err and own:
                err = None                          # how a question is asked is the agents' rule, not a person's
            if err:
                raise Denied(err)
            for k in ("column", "working_by"):
                if k in put:
                    new[k] = put[k]
        if not exists:
            new["from"] = new["by"] = self.uid
        elif not patch:
            for k in ("from", "by"):
                if k in old:
                    new[k] = old[k]
        return new

    def _comment(self, new, old, patch):
        if not isinstance(new, dict):
            raise Denied("a comment is an object", 400)
        new = dict(new)
        if old is not None:
            if not self._is_mine(old):
                raise Denied("not your comment")
            if "post_id" in new:
                new["post_id"] = self._in("posts", new["post_id"])
            new = {k: v for k, v in new.items() if k in OWN_COMMENT_FIELDS or old.get(k) == v}
            return new if patch else dict({k: v for k, v in old.items() if k not in OWN_COMMENT_FIELDS}, **new)
        who = str(new.get("from") or "")
        new = {k: v for k, v in new.items() if k in COMMENT_FIELDS}
        pid = new["post_id"] = self._in("posts", str(new.get("post_id") or ""))
        if private_channel(pid) and pid != self.mine:
            raise Denied("not your channel")
        post = self.store.get_doc("posts", pid)
        if not post.get("exists") and pid != self.mine:
            raise Denied("no post %s" % pid, 404)
        if new.get("answer"):
            p = post.get("data") or {}
            if str(p.get("for") or p.get("assignee") or p.get("assigned_to") or "") != self.uid:
                raise Denied("only the owner, or the person this post is for, answers it")
        new["from"] = who if who.startswith(self.uid + "/") else self.uid         # an agent of theirs: linh/codex
        new["by"] = self.uid
        return new

    def _check(self, coll, doc_id, data, patch):
        """(doc_id, data) as they may be written, or Denied."""
        self._member()
        rid = str(self._in(coll, doc_id))
        if coll == "posts" and rid == self.mine:
            self.ensure_channel()
        cur = self.store.get_doc(coll, rid)
        old = cur.get("data") if cur.get("exists") else None
        if coll == "posts":
            return rid, self._post(rid, data, old, patch)
        if coll == "comments":
            if old is None and rid.startswith("welcome-"):
                raise Denied("%s is the kit's" % rid)
            return rid, self._comment(data, old, patch)
        if coll.startswith("data/users/") and coll.split("/")[2] == self.uid:
            return rid, data
        raise Denied("not yours to write: " + coll)

    def set_doc(self, coll, doc_id, data, if_version=None):
        with self.store.lock:
            rid, data = self._check(coll, doc_id, data, False)
            return self.store.set_doc(coll, rid, data, if_version=if_version)

    def update_doc(self, coll, doc_id, patch, if_version=None):
        with self.store.lock:
            rid, patch = self._check(coll, doc_id, patch, True)
            return self.store.update_doc(coll, rid, patch, if_version=if_version)

    def str_replace(self, coll, doc_id, field, old_str, new_str, if_version=None):
        with self.store.lock:
            rid = self._in(coll, doc_id)
            cur = self.store.get_doc(coll, rid)
            if not cur.get("exists"):
                return None, "not_found"
            val = (cur.get("data") or {}).get(field)
            if isinstance(val, str) and old_str in val:
                _, out = self._check(coll, doc_id, {field: val.replace(old_str, new_str, 1)}, True)
                if field not in out:
                    raise Denied("not yours to change: %s" % field)
            return self.store.str_replace(coll, rid, field, old_str, new_str, if_version=if_version)

    def batch_ops(self, ops):
        """All of it or none: every write is checked before the first is made."""
        with self.store.lock:
            done, seen = [], set()
            for w in ops or []:
                if not isinstance(w, dict) or w.get("op", "set") not in ("set", "update"):
                    raise Denied("a member's batch sets and updates only")
                key = (str(w.get("collection")), str(self._in(str(w.get("collection")), w.get("doc_id"))))
                if key in seen:
                    raise Denied("the same document twice in one batch")
                seen.add(key)
                rid, data = self._check(key[0], w.get("doc_id"), w.get("data"), w.get("op") == "update")
                done.append(dict(w, doc_id=rid, data=data))
            return self.store.batch_ops(done)

    def save_artifact(self, post_id, *a, **kw):
        self._member()
        pid = self._in("posts", post_id)
        if private_channel(pid) and pid != self.mine:
            raise Denied("not your channel")
        kw["by_user"] = self.uid
        return self.store.save_artifact(pid, *a, **kw)

    def save_blob(self, *a, **kw):
        self._member()
        kw.pop("blob_id", None)                      # an existing file's id is never theirs to replace
        return self.store.save_blob(*a, **kw)

    def read_blob(self, blob_id):
        return self.store.read_blob(blob_id)

    def send_comment(self, anchor, text):
        """A wake for the owner's agents, named as this person's. Their private channel wakes nobody but their own AI."""
        self._member()
        anchor = anchor if isinstance(anchor, dict) else {}
        own = str(anchor.get("id") or anchor.get("postId") or "") in ("_orchestrator", self.mine) or \
            re.match(r"^\s*\[post\s+_orchestrator(?:_[^\]]*)?\]", str(text or "")) is not None
        return self.store.send_comment(anchor, text, by=self.uid, quiet=own)
