"""Who may do what on a local board (kit/board/access.py, asked by board_server.py for every route).

A link gives its own rights; a guest writes as a guest and reads its card; a member writes as themself; nobody but the
owner's side answers for the owner, completes a step, reads the owner's channel or opens another board; what is written
is signed and logged; agents are told whose words they get.

Run via: python3 -m unittest tests.test_access -v
"""
import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

os.environ["KIT_NO_FUNNEL"] = "1"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = load("access_board_server", "kit/board/board_server.py")
access = load("access_rules", "kit/board/access.py")


class Case(unittest.TestCase):
    """A server on two boards (prim, other); the owner, a member, a viewer and a member of the other board only."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.roots = {n: os.path.join(cls.d, n) for n in ("prim", "other")}
        for n, r in cls.roots.items():
            os.makedirs(r + "/.kit")
            os.makedirs(r + "/docs")
            os.makedirs(r + "/page")
            for rel, txt in ((".kit/config.json", json.dumps({"project": n.title(), "kit": {"tailscale_users": {"amy@x.com": "member"}}})),
                             (".kit/board.html", "<html><head></head><body></body></html>"), ("docs/private.md", "SECRET-" + n),
                             ("docs/card.md", "CARD"), ("settings.json", '{"k": 1}'), ("page/a.html", "<p>"), ("page/a.css", "p{}"),
                             ("page/data.json", "{}"), ("x.html", "<p>"), ("x.css", "p{}")):
                with open(os.path.join(r, rel), "w") as fh:
                    fh.write(txt)
        cls.srv, cls.store = bs.start_server(cls.roots["prim"], port=0, token="MASTER", attach={"other": cls.roots["other"]})
        cls.other = cls.srv._extra_stores["other"]["store"]
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.d, True)

    def setUp(self):
        for st in (self.store, self.other):
            with st.lock:
                for t in ("docs", "users", "shares", "threads", "changes"):
                    st.conn.execute("DELETE FROM " + t)
                st.conn.commit()
        put = self.store.set_doc
        put("posts", "p1", {"title": "P1", "column": "question", "body": "see `docs/card.md` and `page/a.html` and `x.html`",
                            "options": ["A", "B"], "default_option": "A"})
        put("posts", "step1", {"title": "S", "train": "t", "part": "x", "status": "in progress", "column": "info"})
        put("posts", "_orchestrator", {"title": "o", "body": "owner private"})
        put("comments", "oc", {"post_id": "_orchestrator", "from": "owner", "text": "OWNER-PRIVATE"})
        put("comments", "c1", {"post_id": "p1", "from": "owner", "text": "owner words"})
        put("comments", "a1", {"post_id": "p1", "from": "orchestrator", "text": "agent words"})
        self.other.set_doc("posts", "op", {"title": "other post"})
        self.lin, self.vi = self.user("Lin", "member"), self.user("Vi", "viewer")
        self.sco = self.user("Sco", "member", "other")

    def rq(self, method, path, tok=None, body=None, raw=None, **hdr):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {k.replace("_", "-"): v for k, v in hdr.items()}
        if tok:
            h["X-Kit-Token"] = tok
        c.request(method, path, body=raw if raw is not None else json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        text = r.read().decode(errors="replace")
        self.headers = dict(r.getheaders())
        c.close()
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text

    def user(self, name, role, repo=""):
        return self.rq("POST", "/_api/users", "MASTER", {"name": name, "role": role, "repo": repo})[1]["token"]

    def doc(self, coll, did, st=None):
        return (st or self.store).get_doc(coll, did).get("data") or {}

    def put(self, coll, did, data, tok, **q):
        return self.rq("PUT", "/_api/db/%s/%s" % (coll, did), tok, dict({"data": data}, **q))[0]

    def patch(self, coll, did, data, tok):
        return self.rq("PATCH", "/_api/db/%s/%s" % (coll, did), tok, {"data": data})


class Links(Case):
    def test_a_link_gives_its_own_rights(self):
        for tok, role in ((self.vi, "viewer"), (self.lin, "member"), ("MASTER", "owner")):
            for accept in ("text/html", "*/*"):
                _, page = self.rq("GET", "/?token=" + tok, Accept=accept)
                cookie = self.headers["Set-Cookie"].split(";")[0]
                self.assertEqual(cookie, "kit_token=" + tok)
                if role != "owner":
                    self.assertNotIn("MASTER", str(page) + str(self.headers))
                self.assertEqual(self.rq("GET", "/_api/me", Cookie=cookie)[1]["role"], role)
        self.rq("GET", "/_repo/prim/docs/card.md?token=" + self.vi)
        self.assertNotIn("MASTER", str(self.headers))

    def test_revoking_ends_the_browser_too_and_the_links_they_gave(self):
        self.rq("GET", "/?token=" + self.lin, Accept="text/html")
        cookie = self.headers["Set-Cookie"].split(";")[0]
        sh = self.rq("POST", "/_api/shares", self.lin, {"post_id": "p1"})[1]
        self.assertEqual(self.rq("GET", "/_share/" + sh["token"])[0], 200)
        self.assertEqual(self.rq("DELETE", "/_api/users/lin", "MASTER")[0], 200)
        self.assertEqual(self.rq("GET", "/_api/me", Cookie=cookie)[0], 401)
        self.assertEqual(self.rq("GET", "/_share/" + sh["token"])[0], 410)

    def test_a_name_that_means_something_else_is_not_given(self):
        for n in ("owner", "Orchestrator", "clerk", "viewer", "guest", "kit", "session_01", "owner2"):
            uid = self.rq("POST", "/_api/users", "MASTER", {"name": n, "role": "member"})[1]["user_id"]
            self.assertTrue(uid.startswith("u-"), uid)

    def test_a_request_through_the_funnel_is_never_a_tailnet_person(self):
        self.assertEqual(self.rq("GET", "/_api/me", Tailscale_User_Login="amy@x.com")[0], 200)
        self.assertEqual(self.rq("GET", "/_api/me", Tailscale_User_Login="amy@x.com", Tailscale_Funnel_Request="?1")[0], 401)


class Routes(Case):
    def test_a_route_not_in_the_table_is_the_owners(self):
        src = (ROOT / "kit/board/board_server.py").read_text(encoding="utf-8")
        src = src[src.index("        def do_GET(self):"):src.index("    return Handler\n")]
        bodies = re.split(r"\n        def do_(GET|PUT|PATCH|DELETE|POST)\(self\):", "\n" + src)
        seen = 0
        for verb, body in zip(bodies[1::2], bodies[2::2]):
            if verb in ("PUT", "PATCH"):
                continue                                   # documents: checked one by one in View
            for path in re.findall(r'path == "(/_api/[^"]+)"', body):
                if path in ("/_api/health", "/_api/unlock"):
                    continue
                seen += 1
                need = access.route_role(verb, path)
                for tok, rank in ((self.vi, 1), (self.lin, 2)):
                    if need and access.RANKS[need] <= rank:
                        continue
                    self.assertEqual(self.rq(verb, path, tok, {} if verb == "POST" else None)[0], 403, (verb, path, rank))
        self.assertGreater(seen, 20)

    def test_what_stays_the_owners(self):
        for verb, path in (("GET", "/_api/users"), ("GET", "/_api/notify"), ("GET", "/_api/comments/wait?timeout=1"), ("POST", "/_api/users"),
                           ("POST", "/_api/comments/watch"), ("POST", "/_api/repo-docs/pin"), ("POST", "/_api/attach"), ("POST", "/_api/notify"),
                           ("DELETE", "/_api/users/vi"), ("POST", "/_api/comments/threads/t/ack"), ("POST", "/_api/comments/threads/t/reply")):
            self.assertEqual(self.rq(verb, path, self.lin, {} if verb == "POST" else None)[0], 403, path)
        self.assertEqual(self.rq("GET", "/_api/new-thing", self.lin)[0], 403)
        self.assertEqual(self.rq("GET", "/_api/new-thing", "MASTER")[0], 404)

    def test_a_viewer_writes_nothing(self):
        for verb, path, body in (("PUT", "/_api/db/comments/x", {"data": {"post_id": "p1", "text": "t"}}),
                                 ("PATCH", "/_api/db/posts/p1", {"data": {"title": "x"}}), ("POST", "/_api/comments/send", {"text": "x"}),
                                 ("POST", "/_api/shares", {"post_id": "p1"}), ("POST", "/_api/db/batch", {"writes": []}),
                                 ("POST", "/_api/blobs", {})):
            self.assertEqual(self.rq(verb, path, self.vi, body)[0], 403, path)
        self.assertEqual(self.rq("GET", "/_api/db/posts/p1", self.vi)[0], 200)

    def test_no_access_rules_no_access(self):
        keep, bs.ACCESS = bs.ACCESS, None
        try:
            self.assertEqual(self.rq("GET", "/_api/db/posts", self.lin)[0], 403)
            self.assertEqual(self.rq("GET", "/_api/db/posts", "MASTER")[0], 200)
        finally:
            bs.ACCESS = keep


class Reading(Case):
    def test_the_owners_channel_is_not_there(self):
        self.put("comments", "lp", {"post_id": "_orchestrator", "text": "LIN-PRIVATE"}, self.lin)
        self.assertEqual(self.doc("comments", "lp")["post_id"], "_orchestrator_lin")
        paths = ["/_api/db/comments", "/_api/db/comments?repo=all", "/_api/db/posts", "/_api/db/comments/oc", "/_api/doc/comments/oc",
                 "/_api/doc/posts/_orchestrator", "/_api/db/posts/_orchestrator", "/_api/export?format=json", "/_api/export?format=md",
                 "/_api/export?post=_orchestrator&format=json", "/_api/changes", "/_api/db/comments/counts"]
        for tok in (self.lin, self.vi):
            for p in paths:
                got = json.dumps(self.rq("GET", p, tok)[1])
                self.assertNotIn("OWNER-PRIVATE", got, p)
                self.assertNotIn("owner private", got, p)
                self.assertNotIn('"oc"', got, p)
                if tok == self.vi:
                    self.assertNotIn("LIN-PRIVATE", got, p)
                    self.assertNotIn("_orchestrator_lin", got, p)
        self.assertIn("LIN-PRIVATE", json.dumps(self.rq("GET", "/_api/db/comments", self.lin)[1]))
        got = self.rq("GET", "/_api/db/comments?where=" + '[["post_id","==","_orchestrator"]]'.replace('"', "%22"), self.lin)[1]["docs"]
        self.assertEqual([(d["id"], d["data"]["post_id"]) for d in got], [("lp", "_orchestrator")])
        self.assertEqual(self.rq("GET", "/_api/doc/posts/_orchestrator_lin", self.vi)[0], 403)
        self.assertNotIn("LIN-PRIVATE", json.dumps(self.rq("GET", "/_api/db/comments", "MASTER")[1]))

    def test_lists_are_paged_after_what_is_hidden(self):
        for i in range(5):
            self.store.set_doc("comments", "k%d" % i, {"post_id": "p1", "from": "x", "text": "t", "created_at": str(i)})
        got = self.rq("GET", "/_api/db/comments?limit=3&order_by=created_at&dir=desc", self.vi)[1]
        self.assertEqual(([d["id"] for d in got["docs"]], got["next_cursor"], got["total"]), (["k4", "k3", "k2"], "3", 7))

    def test_one_board_is_one_board(self):
        S = self.sco
        self.assertEqual({d["id"] for d in self.rq("GET", "/_api/db/posts", S)[1]["docs"]}, {"op", "_orchestrator"})
        for p in ("/_api/db/posts/p1", "/_api/doc/posts/p1", "/_api/export?format=json", "/_api/links", "/_api/db/posts?repo=all",
                  "/_api/changes", "/_api/db/comments"):
            got = json.dumps(self.rq("GET", p, S)[1])
            self.assertFalse("P1" in got or "step1" in got, p)
        self.assertEqual(self.rq("GET", "/_api/db/posts?repo=prim", S)[0], 404)
        for p in ("/_repo/prim/docs/private.md", "/_api/repo-doc?path=docs/private.md&repo=prim", "/_api/repo-asset?path=docs/private.md"):
            self.assertNotIn("SECRET-prim", json.dumps(self.rq("GET", p, S)[1]), p)
        self.assertEqual(self.rq("POST", "/_api/shares", S, {"post_id": "p1", "repo": "prim"})[0], 404)
        self.rq("POST", "/_api/db/batch", S, {"writes": [{"op": "set", "collection": "posts", "doc_id": "bs", "data": {"title": "z"}}]})
        self.assertFalse(self.store.get_doc("posts", "bs")["exists"])
        self.assertEqual([r["id"] for r in self.rq("GET", "/_api/repos", S)[1]["repos"]], ["other"])
        # invited from this board, with no board named: this one, not every one
        self.assertEqual(self.rq("GET", "/_api/db/posts?repo=other", self.lin)[0], 404)
        everyone = self.rq("POST", "/_api/users?repo=all", "MASTER", {"name": "All", "role": "member"})[1]
        self.assertEqual(everyone["repo"], "all")
        self.assertEqual(self.rq("GET", "/_api/db/posts?repo=other", everyone["token"])[0], 200)
        self.assertNotIn("OWNER-PRIVATE", json.dumps(self.rq("GET", "/_api/db/comments?repo=all", everyone["token"])[1]))

    def test_an_invite_to_a_board_that_is_gone_opens_nothing(self):
        with self.store.lock:
            self.store.conn.execute("UPDATE users SET repo='gone' WHERE user_id='lin'")
        self.assertEqual(self.rq("GET", "/_api/db/posts", self.lin)[0], 403)
        with self.store.lock:
            self.store.conn.execute("UPDATE users SET repo='' WHERE user_id='lin'")        # an older invite: this board only
        self.assertEqual(self.rq("GET", "/_api/db/posts", self.lin)[0], 200)
        self.assertEqual(self.rq("GET", "/_api/db/posts?repo=other", self.lin)[0], 404)
        self.assertEqual(self.rq("POST", "/_api/users", "MASTER", {"name": "Z", "repo": "nope"})[0], 400)

    def test_the_kits_own_files_are_not_the_boards_documents(self):
        for p in ("/_repo/prim/.kit/config.json", "/_api/repo-doc?path=.kit/config.json", "/_api/repo-asset?path=.kit/config.json",
                  "/_api/repo-asset?path=./.kit/config.json"):
            self.assertEqual(self.rq("GET", p, self.lin)[0], 403, p)
        self.assertEqual(self.rq("GET", "/_repo/prim/.kit/config.json", "MASTER")[0], 200)
        self.assertEqual(self.rq("GET", "/_repo/prim/docs/card.md", self.lin)[0], 200)

    def test_anothers_own_documents(self):
        self.store.set_doc("data/users/owner", "board-ui", {"x": 1})
        self.assertEqual(self.rq("GET", "/_api/doc/data/users/owner/board-ui", self.lin)[0], 403)
        self.assertEqual(self.rq("PUT", "/_api/doc/data/users/owner/board-ui", self.lin, {"data": {}})[0], 403)
        self.assertEqual(self.rq("PUT", "/_api/doc/data/users/lin/board-ui", self.lin, {"data": {"y": 1}})[0], 200)
        self.assertEqual(self.rq("GET", "/_api/doc/data/users/lin/board-ui", self.lin)[1]["data"], {"y": 1})
        self.assertEqual(self.rq("GET", "/_api/doc/kit-receipts/x", self.lin)[0], 403)


class Writing(Case):
    def test_nobody_writes_as_someone_else(self):
        for who in ("owner", "orchestrator", "clerk", "vi", "", "linda/codex", "lin"):
            self.assertEqual(self.put("comments", "x", {"post_id": "p1", "from": who, "by": "owner", "text": "t"}, self.lin), 200)
            c = self.doc("comments", "x")
            self.assertEqual((c["from"], c["by"]), ("lin", "lin"), who)
            self.store.conn.execute("DELETE FROM docs WHERE doc_id='x'")
        self.put("comments", "x", {"post_id": "p1", "from": "lin/codex", "text": "t"}, self.lin)
        self.assertEqual((self.doc("comments", "x")["from"], self.doc("comments", "x")["by"]), ("lin/codex", "lin"))
        self.assertEqual(self.put("posts", "np", {"title": "T", "from": "owner", "by": "owner", "column": "todo"}, self.lin), 200)
        self.assertEqual((self.doc("posts", "np")["from"], self.doc("posts", "np")["by"]), ("lin", "lin"))
        w = [{"op": "set", "collection": "comments", "doc_id": "y", "data": {"post_id": "p1", "from": "owner", "text": "t"}}]
        self.rq("POST", "/_api/db/batch", self.lin, {"writes": w})
        self.assertEqual(self.doc("comments", "y")["from"], "lin")

    def test_what_is_not_a_persons_to_write_is_dropped_from_a_comment(self):
        self.put("comments", "x", {"post_id": "p1", "text": "t", "kind": "review", "read": True, "proof": 1}, self.lin)
        self.assertEqual(set(self.doc("comments", "x")), {"post_id", "text", "from", "by"})
        self.assertEqual(self.put("comments", "welcome-x", {"post_id": "p1", "text": "t"}, self.lin), 403)
        self.assertEqual(self.put("comments", "z", {"post_id": "_orchestrator_vi", "text": "t"}, self.lin), 403)
        self.assertEqual(self.put("comments", "z", {"post_id": "nope", "text": "t"}, self.lin), 404)

    def test_other_peoples_words_stay_theirs(self):
        for did in ("c1", "a1"):
            was = self.doc("comments", did)
            for verb, path, body in (("PATCH", "", {"data": {"read": True}}), ("PATCH", "", {"data": {"text": "forged"}}),
                                     ("PUT", "", {"data": {"post_id": "p1", "text": "forged"}}),
                                     ("POST", "/str_replace", {"field": "text", "old_str": "words", "new_str": "forged"})):
                self.assertEqual(self.rq(verb, "/_api/db/comments/%s%s" % (did, path), self.lin, body)[0], 403, (did, verb))
            self.assertEqual(self.rq("POST", "/_api/db/batch", self.lin, {"writes": [
                {"op": "update", "collection": "comments", "doc_id": did, "data": {"removed": True}}]})[0], 403)
            self.assertEqual(self.doc("comments", did), was)

    def test_their_own_words_are_theirs_and_stay_signed(self):
        self.put("comments", "x", {"post_id": "p1", "text": "one"}, self.lin)
        self.assertEqual(self.patch("comments", "x", {"text": "two", "from": "owner", "by": "owner", "answer": "A", "post_id": "step1"}, self.lin)[0], 200)
        self.assertEqual(self.rq("POST", "/_api/db/comments/x/str_replace", self.lin, {"field": "text", "old_str": "two", "new_str": "three"})[0], 200)
        self.assertEqual(self.doc("comments", "x"), {"post_id": "p1", "text": "three", "from": "lin", "by": "lin"})
        self.assertEqual(self.rq("POST", "/_api/db/comments/x/str_replace", self.lin, {"field": "from", "old_str": "lin", "new_str": "owner"})[0], 403)
        self.put("comments", "x", {"post_id": "step1", "text": "four", "by": "vi"}, self.lin)
        self.assertEqual(self.doc("comments", "x"), {"post_id": "p1", "text": "four", "from": "lin", "by": "lin"})

    def test_only_the_one_a_post_is_for_answers_it(self):
        self.assertEqual(self.put("comments", "x", {"post_id": "p1", "text": "Answer: B", "answer": "B"}, self.lin), 403)
        self.assertEqual(self.patch("posts", "p1", {"column": "done", "answer": "B"}, self.lin)[0], 403)
        self.store.update_doc("posts", "p1", {"for": "lin"})
        self.assertEqual(self.put("comments", "x", {"post_id": "p1", "text": "Answer: B", "answer": "B"}, self.lin), 200)
        self.assertEqual(self.patch("posts", "p1", {"column": "done"}, self.lin)[0], 200)
        self.assertEqual(self.patch("posts", "p1", {"title": "mine now"}, self.lin)[0], 403)

    def test_a_step_is_completed_by_its_proof_only(self):
        for extra in ({}, {"for": "lin"}):
            self.store.update_doc("posts", "step1", extra)
            for body in ({"status": "complete", "proof": {"key": "x"}}, {"status": "complete"}, {"proof": {"key": "x"}}, {"session_id": "s"},
                         {"review": "continue"}, {"verdict": "x"}, {"train": "z"}):
                self.assertEqual(self.patch("posts", "step1", body, self.lin)[0], 403, (extra, body))
        self.assertEqual(self.patch("posts", "step1", {"status": "held"}, self.lin)[0], 200)
        self.assertEqual(self.put("posts", "s2", {"title": "x", "train": "t", "part": "y"}, self.lin), 403)      # no step of their own either
        for did in ("_trains", "_dashboard", "_handover", "_orchestrator_vi"):
            self.assertEqual(self.put("posts", did, {"title": "x"}, self.lin), 403, did)

    def test_a_kit_with_no_proof_gate_lets_no_member_complete_a_step(self):
        self.store.update_doc("posts", "step1", {"for": "lin"})
        v = access.View(self.store, {"id": "lin", "role": "member"}, gate=None)
        for st in ("complete", " Done ", "completed"):
            with self.assertRaises(access.Denied):
                v.update_doc("posts", "step1", {"status": st})
        self.assertIsNone(v.update_doc("posts", "step1", {"status": "held"})[1])

    def test_replacing_a_post_cannot_drop_what_is_not_theirs(self):
        self.assertEqual(self.put("posts", "step1", {"title": "S", "status": "in progress", "column": "info"}, self.lin), 403)
        self.put("posts", "np", {"title": "T", "column": "info"}, self.lin)
        self.assertEqual(self.put("posts", "np", {"title": "T2", "column": "info"}, self.lin), 200)
        self.assertEqual((self.doc("posts", "np")["by"], self.doc("posts", "np")["title"]), ("lin", "T2"))
        self.assertEqual(self.rq("POST", "/_api/db/posts/p1/str_replace", self.lin, {"field": "body", "old_str": "see", "new_str": "x"})[0], 403)
        self.assertEqual(self.rq("POST", "/_api/db/posts/np/str_replace", self.lin, {"field": "title", "old_str": "T2", "new_str": "T3"})[0], 200)

    def test_claims_and_assignments(self):
        self.assertEqual(self.patch("posts", "p1", {"working_by": "vi"}, self.lin)[0], 403)
        self.assertEqual(self.patch("posts", "p1", {"working_by": "lin"}, self.lin)[0], 200)
        code, why = self.patch("posts", "p1", {"working_by": "bo"}, self.user("Bo", "member"))
        self.assertEqual((code, why["error"]), (409, "taken"))
        self.assertIn("@lin", why["why"])
        self.assertEqual(self.patch("posts", "p1", {"working_by": ""}, self.lin)[0], 200)
        self.assertEqual(self.patch("posts", "p1", {"for": "vi"}, self.lin)[0], 403)
        for k in ("for", "assignee", "assigned_to"):                        # it would make the owner's question theirs to answer
            self.assertEqual(self.patch("posts", "p1", {k: "lin"}, self.lin)[0], 403)
        self.store.update_doc("posts", "p1", {"for": "lin"})
        self.assertEqual(self.patch("posts", "p1", {"for": "vi"}, self.lin)[0], 403)
        self.assertEqual(self.patch("posts", "p1", {"for": ""}, self.lin)[0], 200)
        self.assertEqual(self.patch("posts", "p1", {"for": ""}, self.lin)[0], 200)
        self.assertEqual(self.put("posts", "np", {"title": "T", "column": "todo", "for": "vi"}, self.lin), 200)

    def test_a_batch_is_all_or_nothing(self):
        ok = {"op": "set", "collection": "comments", "doc_id": "b1", "data": {"post_id": "p1", "text": "t"}}
        for bad in ({"op": "update", "collection": "posts", "doc_id": "p1", "data": {"title": "x"}}, {"op": "delete", "collection": "comments", "doc_id": "c1"},
                    {"op": "set", "collection": "kit-receipts", "doc_id": "x", "data": {}}, dict(ok)):
            self.assertEqual(self.rq("POST", "/_api/db/batch", self.lin, {"writes": [ok, bad], "sync": True})[0], 403, bad)
            self.assertFalse(self.store.get_doc("comments", "b1")["exists"])
            self.assertTrue(self.store.get_doc("comments", "c1")["exists"])

    def test_files(self):
        bid = self.rq("POST", "/_api/blobs", "MASTER", raw=b"orig", Content_Type="image/png")[1]["id"]
        self.assertEqual(self.rq("POST", "/_api/blobs", self.lin, raw=b"evil", Content_Type="image/png", X_Blob_Id=bid)[0], 403)
        self.assertEqual(self.rq("GET", "/_blob/" + bid, "MASTER")[1], "orig")
        self.assertEqual(self.rq("POST", "/_api/blobs", self.lin, raw=b"mine", Content_Type="image/png")[0], 200)
        code, a = self.rq("POST", "/_api/posts/p1/artifacts", self.lin, {"path": "x.html", "content": "<script>1</script>"})
        self.assertEqual(code, 200)
        self.rq("GET", "/_api/artifacts/%s?raw=1" % a["artifact_id"], "MASTER")
        self.assertIn("sandbox", self.headers["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", self.headers["Content-Security-Policy"])

    def test_a_co_owner_signs_and_the_owners_own_writes_are_as_before(self):
        co = self.user("Co", "owner")
        self.assertEqual(self.put("comments", "x", {"post_id": "p1", "from": "owner", "text": "t"}, co), 200)
        self.assertEqual(self.doc("comments", "x"), {"post_id": "p1", "from": "owner", "text": "t", "by": "co"})
        self.put("comments", "y", {"post_id": "p1", "from": "S1", "text": "t", "kind": "review"}, "MASTER")
        self.assertEqual(self.doc("comments", "y"), {"post_id": "p1", "from": "S1", "text": "t", "kind": "review"})


class Guests(Case):
    def share(self, tok="MASTER", **kw):
        return self.rq("POST", "/_api/shares", tok, dict({"post_id": "p1"}, **kw))[1]["token"]

    def test_a_guest_writes_as_a_guest(self):
        t = self.share()
        for who in ("owner", "owner2", "consultant", "OWNER", "viewer", "lin", "x", "session_01", "kit"):
            self.rq("POST", "/_share/%s/submit" % t, None, {"from": who, "text": "Answer: B"})
            with self.store.lock:
                self.store.conn.execute("UPDATE shares SET submissions=0")
        cs = [d["data"] for d in self.store.list_docs("comments")["docs"] if d["id"].startswith("ext-")]
        self.assertEqual(len(cs), 9)
        self.assertTrue(all(c["by"] == "guest" and c["from"].startswith("guest-") for c in cs), cs)
        self.rq("POST", "/_share/%s/submit" % t, None, {"from": "lmarena", "text": "t"})
        self.assertIn("lmarena", [d["data"]["from"] for d in self.store.list_docs("comments")["docs"]])
        r = self.rq("POST", "/_share/%s/submit" % t, None, {"from": "owner", "text": "t", "mode": "child_post"})[1]
        self.assertEqual((self.doc("posts", r["post_id"])["by"], self.doc("posts", r["post_id"])["runner"]), ("guest", "guest-owner"))

    def test_a_guest_reads_its_card_and_what_the_owner_put_there(self):
        t = self.share()
        get = lambda p: self.rq("GET", "/_share/%s/repo/%s" % (t, p))[0]  # noqa: E731
        self.assertEqual([get(p) for p in ("docs/card.md", "page/a.html", "page/a.css")], [200, 200, 200])
        shut = ("docs/private.md", "settings.json", "x.css", "page/data.json", ".kit/config.json", "docs/../docs/private.md")
        self.assertEqual([get(p) for p in shut], [403] * len(shut))
        for text in ("see `docs/private.md`", "`y.html`", "![x](/_api/repo-asset?path=docs/private.md)", "`docs/y.html`"):
            self.rq("POST", "/_share/%s/submit" % t, None, {"from": "x9", "text": text})
        self.put("comments", "m", {"post_id": "p1", "text": "`docs/private.md`"}, self.lin)
        self.store.set_doc("comments", "ext-old", {"post_id": "p1", "from": "lmarena", "text": "`docs/private.md`"})      # from before the signature
        self.assertEqual([get(p) for p in shut], [403] * len(shut))
        self.store.set_doc("comments", "o", {"post_id": "p1", "from": "owner", "text": "`docs/private.md`"})
        self.assertEqual(get("docs/private.md"), 200)

    def test_a_members_link_hands_out_no_file_of_the_repo(self):
        t = self.share(self.lin)
        self.assertEqual(self.rq("GET", "/_share/%s/repo/docs/card.md" % t)[0], 403)
        self.put("posts", "np", {"title": "T", "column": "info", "body": "`docs/private.md`"}, self.lin)
        t = self.share("MASTER", post_id="np")
        self.assertEqual(self.rq("GET", "/_share/%s/repo/docs/private.md" % t)[0], 403)
        self.assertEqual(self.rq("POST", "/_api/shares", self.lin, {"post_id": "_orchestrator"})[0], 403)

    def test_each_sees_and_ends_their_own_links(self):
        mine, his = self.share(self.lin), self.share()
        self.assertEqual([s["token"] for s in self.rq("GET", "/_api/shares", self.lin)[1]["shares"]], [mine])
        self.assertEqual(self.rq("DELETE", "/_api/shares/" + his, self.lin)[0], 403)
        self.assertEqual(self.rq("DELETE", "/_api/shares/" + mine, self.lin)[0], 200)
        self.assertEqual(len(self.rq("GET", "/_api/shares", "MASTER")[1]["shares"]), 2)
        self.assertEqual(self.store.get_share(self.share(max_submissions=10 ** 6))["max_submissions"], 100)

    def test_a_runner_keeps_its_name_on_its_own_card(self):
        self.store.update_doc("posts", "p1", {"runner": "lmarena", "part": "lmarena"})
        t = self.share()
        self.rq("POST", "/_share/%s/submit" % t, None, {"from": "lmarena", "text": "t"})
        self.assertIn("lmarena", [d["data"]["from"] for d in self.store.list_docs("comments")["docs"]])


class Agents(Case):
    def cli(self, *a):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit/board/board_cli.py"), "--url", "http://127.0.0.1:%d" % self.port,
                            "--token", "MASTER", *a], capture_output=True, text=True, cwd=self.roots["prim"],
                           env=dict(os.environ, KIT_ACTOR="sess-1", KIT_BOARD_TOKEN="MASTER"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def send(self, tok, text, anchor=None):
        return self.rq("POST", "/_api/comments/send", tok, {"text": text, "anchor": anchor or {"id": "p1"}})[1]["thread_id"]

    def test_the_clerk_is_told_whose_words_they_are(self):
        r = self.cli("comments", "route", self.send(self.lin, "[post p1] do X"))
        self.assertIn("@lin, a collaborator (not the owner)", r["message"])
        self.assertIn("the owner's", r["message"])
        self.assertIn("lin", r["author"])
        r = self.cli("comments", "route", self.send(self.lin, "Answer: B"))
        self.assertIn("from @lin", r["message"])
        r = self.cli("comments", "route", self.send("MASTER", "[post p1] do Y"))
        self.assertIn("Board comment from the owner", r["message"])
        self.assertEqual(r["author"], "owner")

    def test_a_private_channel_wakes_nobody_elses_agents(self):
        for a, t in (({"id": "_orchestrator"}, "x"), ({}, "[post _orchestrator] x"), ({"postId": "_orchestrator_lin"}, "x")):
            self.send(self.lin, t, a)
        self.assertEqual(self.rq("GET", "/_api/comments/wait?timeout=0.2", "MASTER")[1]["event"], "timeout")
        tid = self.send(self.lin, "y")
        self.assertEqual(self.rq("GET", "/_api/comments/wait?timeout=1", "MASTER")[1]["by"], "lin")
        self.assertEqual(self.rq("GET", "/_api/comments/threads/" + tid, self.lin)[0], 200)
        self.assertEqual(self.rq("GET", "/_api/comments/threads/" + self.send("MASTER", "z"), self.lin)[0], 404)

    def test_the_inbox_has_them_and_says_who(self):
        self.put("comments", "m", {"post_id": "p1", "text": "MEMBER-WORDS"}, self.lin)
        self.store.set_doc("comments", "v", {"post_id": "p1", "from": "viewer", "text": "VIEWER-WORDS"})
        self.store.set_doc("comments", "g", {"post_id": "p1", "from": "lmarena", "by": "guest", "text": "GUEST"})
        got = {c["text"]: c.get("author", "") for c in self.cli("inbox")["comments"]}
        self.assertIn("@lin", got["MEMBER-WORDS"])
        self.assertIn("not the owner", got["VIEWER-WORDS"])
        self.assertEqual(got["owner words"], "")
        self.assertNotIn("GUEST", got)
        self.assertNotIn("agent words", got)

    def test_the_log_says_who_changed_what(self):
        self.put("comments", "m", {"post_id": "p1", "text": "t"}, self.lin)
        self.cli("set", "posts", "np", "--data", '{"title": "T", "column": "info"}')
        self.rq("PATCH", "/_api/db/posts/p1", "MASTER", {"data": {"priority": 1}})
        t = self.rq("POST", "/_api/shares", "MASTER", {"post_id": "p1"})[1]["token"]
        self.rq("POST", "/_share/%s/submit" % t, None, {"from": "owner", "text": "t"})
        log = self.rq("GET", "/_api/changes", "MASTER")[1]["changes"]
        who = {c["doc_id"]: c["actor"] for c in log}
        self.assertEqual((who["m"], who["np"], who["p1"]), ("lin", "owner (sess-1)", "owner"))
        self.assertIn("guest:guest-owner", who.values())
        self.rq("PATCH", "/_api/db/posts/p1", self.lin, {"data": {"working_by": "lin"}}, X_Kit_Actor="owner")
        self.assertEqual(self.rq("GET", "/_api/changes", "MASTER")[1]["changes"][-1]["actor"], "lin")

    def test_the_owners_words_are_the_owners_in_resume(self):
        src = (ROOT / "scripts/kit.py").read_text(encoding="utf-8")
        self.assertIn('c.get("from") != owner or not c.get("created_at")', src)


class Pages(unittest.TestCase):
    def test_someone_elses_page_never_runs_as_the_board(self):
        js = (ROOT / "kit/board/shim.js").read_text(encoding="utf-8")
        i = js.index("ifr.srcdoc = full.content")
        self.assertNotIn("allow-same-origin", js[i - 400:i].rsplit("setAttribute", 1)[1])

    def test_a_document_links_to_no_script(self):
        if not shutil.which("node"):
            self.skipTest("no node")
        js = (ROOT / "kit/board/shim.js").read_text(encoding="utf-8")
        i = js.index("  function renderRichDocMarkdown(")
        j = js.index("\n  }\n", i) + 5
        md = ["[a](javascript:alert(1))", "[a](JaVaScript:alert(1))", "[a](java&#115;cript:alert(1))", '<a href="javascript:x">b</a>', '<a href="java\tscript:x">b</a>',
              '<a href=" &#106;avascript:x">b</a>', "[ok](https://x.y/z)"]
        code = ("function resolveAssetUrl(a){return a}function parseAssociatedDocLink(){return null}function renderMermaidBlock(s){return ''}"
                "function renderSvgBlock(s){return ''}" + js[i:j] + "\nconsole.log(JSON.stringify(%s.map(function(m){return renderRichDocMarkdown(m,'')})))" % json.dumps(md))
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        out = json.loads(r.stdout)
        for m, h in zip(md[:-1], out[:-1]):
            href = re.search(r'href="([^"]*)"', h)
            self.assertFalse(href and re.match(r"\s*java\s*script:", href.group(1).replace("\t", ""), re.I), (m, h))
        self.assertIn('href="https://x.y/z"', out[-1])

    def test_the_local_page_knows_its_reader(self):
        js = (ROOT / "kit/board/shim.js").read_text(encoding="utf-8")
        self.assertNotIn("isOwner: function () { return Promise.resolve(true); }", js)
        html = (ROOT / "kit/board/board.template.html").read_text(encoding="utf-8")
        rows = re.findall(r'(\S*)\(?\{[^{}]*from: state\.isOwner \? "owner" : "viewer"', html)
        self.assertEqual(len(rows), 9)
        self.assertEqual(html.count("signed({"), 9)


if __name__ == "__main__":
    unittest.main()
