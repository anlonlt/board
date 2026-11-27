#!/usr/bin/env python3
"""CL56: an outside AI gets one link for a card, the invite scoped to its post, and attaches files to the post with it.

The R1 runner card handed out the old share link (/_share/<token>: text only); the Antigravity desktop app could not
attach its patch and pasted 61.7 KB of it as a post's text. Now every link an outside AI gets (`kit.py link`, a card
runner's `kit.py launch`, the board's "Link for AI", `board_cli.py share create`) is /_invite/<token>.md: its first
section is the post's drop box (one command) and how to write the result; an old /_share/<token> answers the same page
and reaches the same drop box.
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
import urllib.request

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="kit-xdg-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, os.environ["XDG_CONFIG_HOME"], True)  # CL52: removed at exit
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("kit_board_server_cl56", os.path.join(ROOT, "kit", "board", "board_server.py"))
board_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board_server)

PATCH = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n" * 400     # ~20 KB: a file, not a post's text


class OneLink(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="kit-cl56-")
        os.makedirs(os.path.join(self.td, ".kit"))
        self.srv, self.store = board_server.start_server(self.td, host="127.0.0.1", port=0, token="owner-tok")
        self.store.set_doc("posts", "r9-runner-step", {"title": "Run the kit on another app", "column": "todo",
                                                       "runner": "antigravity-web", "body": "Test it and attach your patch."})
        self.store.set_doc("posts", "other-post", {"title": "Someone else's", "column": "todo"})
        self.port = self.srv.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.store.close()
        shutil.rmtree(self.td, ignore_errors=True)

    def _req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body) if not isinstance(body, (bytes, str)) else body
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read().decode("utf-8")
        c.close()
        return r.status, raw

    def _runner_card_link(self):
        """What the orchestrator puts on a runner card: `kit.py link <post>` (the address an outside AI reaches)."""
        env = dict(os.environ, KIT_FUNNEL_URL=self.base)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kit.py"), "link", "r9-runner-step",
                            "--url", self.base, "--token", "owner-tok"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        link = r.stdout.strip().splitlines()[0]
        self.assertRegex(link, r"^%s/_invite/[^/]+\.md$" % re.escape(self.base), "the runner card's link is the post's invite")
        return link

    def test_an_outside_session_with_only_the_runner_card_link_attaches_a_file_to_its_post(self):
        link = self._runner_card_link()
        page = urllib.request.urlopen(link, timeout=30).read().decode("utf-8")       # no token, no account: the link only
        first = page.index("## 1. Attach your files to the card")
        self.assertLess(first, page.index("## 2. Write the card's result"))
        self.assertLess(page.index("## 2. Write the card's result"), page.index("## Task & Context"),
                        "the drop box and the result come first, the task after")
        self.assertIn("Test it and attach your patch.", page)
        cmd = re.search(r"```bash\n(.+?)\n```", page[first:], re.S).group(1)
        self.assertIn("/_api/posts/r9-runner-step/artifacts", cmd, "the drop box route of the invite, for this post")
        work = tempfile.mkdtemp(prefix="kit-cl56-ai-")
        with open(os.path.join(work, "fix.patch"), "w", encoding="utf-8") as fh:
            fh.write(PATCH)
        if shutil.which("curl") and shutil.which("bash"):
            env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
            r = subprocess.run(["bash", "-c", cmd.replace('"one line: what it is"', '"the fix"')], cwd=work,
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            ans = json.loads(r.stdout)
        else:                                             # the page's "no shell" form: the same route, the same header
            url = re.search(r"to `(http[^`]+/artifacts)` with the header `X-Kit-Token: ([^`]+)`", page).groups()
            st, raw = self._req("POST", url[0][len(self.base):], {"path": "fix.patch", "content": PATCH, "summary": "the fix"},
                                {"X-Kit-Token": url[1]})
            self.assertEqual(st, 200, raw)
            ans = json.loads(raw)
        self.assertNotIn("error", ans)
        shutil.rmtree(work, ignore_errors=True)
        # it shows on its post: the owner's view of the post's drop box
        st, raw = self._req("GET", "/_api/posts/r9-runner-step/artifacts", headers={"X-Kit-Token": "owner-tok"})
        self.assertEqual(st, 200)
        arts = json.loads(raw)["artifacts"]
        self.assertEqual([a["path"] for a in arts], ["fix.patch"])
        full = self.store.get_artifact(arts[0]["artifact_id"])
        self.assertEqual(full["content"], PATCH, "the whole file, attached, never pasted into a post's text")
        self.assertFalse(any((c["data"] or {}).get("post_id") == "r9-runner-step" for c in self.store.list_docs("comments")["docs"]))

    def test_the_link_opens_its_post_only(self):
        tok = self._runner_card_link().rsplit("/", 1)[1][:-3]
        st, raw = self._req("POST", "/_api/posts/other-post/artifacts", {"path": "x.txt", "content": "x"}, {"X-Kit-Token": tok})
        self.assertEqual(st, 403, raw)
        st, _ = self._req("GET", "/_api/db/posts", headers={"X-Kit-Token": tok})
        self.assertEqual(st, 401, "a card's link reads no other part of the board")
        st, raw = self._req("GET", "/_api/posts/r9-runner-step/artifacts", headers={"X-Kit-Token": tok})
        self.assertEqual(st, 200, raw)

    def test_a_file_over_the_cap_is_refused_with_the_cap_named(self):
        tok = self._runner_card_link().rsplit("/", 1)[1][:-3]
        big = "x" * (board_server.MAX_REPO_DOC_BYTES + 1)
        st, raw = self._req("POST", "/_api/posts/r9-runner-step/artifacts", {"path": "big.txt", "content": big}, {"X-Kit-Token": tok})
        self.assertEqual(st, 413, raw)
        ans = json.loads(raw)
        self.assertEqual(ans["max_bytes"], board_server.MAX_REPO_DOC_BYTES)
        self.assertIn("2 MB", ans["why"])
        self.assertEqual(self.store.list_artifacts(post_id="r9-runner-step"), [])

    def test_an_old_share_link_reaches_the_same_page_and_drop_box(self):
        st, raw = self._req("POST", "/_api/shares", {"post_id": "r9-runner-step"}, {"X-Kit-Token": "owner-tok"})
        self.assertEqual(st, 200, raw)
        sh = json.loads(raw)
        self.assertEqual(sh["invite"], "/_invite/%s.md" % sh["token"], "the board's Link for AI copies this one")
        st_old, old_page = self._req("GET", sh["path"])            # /_share/<token>, handed out before CL56
        st_new, new_page = self._req("GET", sh["invite"])
        self.assertEqual((st_old, st_new), (200, 200))
        self.assertEqual(old_page, new_page, "one page, not two")
        st, raw = self._req("POST", sh["path"] + "/artifacts", {"path": "old.patch", "content": "x", "from": "old-link"})
        self.assertEqual(st, 200, raw)
        st, raw = self._req("POST", "/_api/posts/r9-runner-step/artifacts", {"path": "new.patch", "content": "y"},
                            {"X-Kit-Token": sh["token"]})
        self.assertEqual(st, 200, raw)
        self.assertEqual(sorted(a["path"] for a in self.store.list_artifacts(post_id="r9-runner-step")), ["new.patch", "old.patch"])

    def test_the_link_for_ai_button_and_the_cli_give_the_invite(self):
        with open(os.path.join(ROOT, "kit", "board", "board.template.html"), encoding="utf-8") as fh:
            html = fh.read()
        btn = html[html.index('$("ai-share").addEventListener'):]
        self.assertIn("j.invite", btn[:2000])
        cli = os.path.join(ROOT, "kit", "board", "board_cli.py")
        r = subprocess.run([sys.executable, cli, "--url", self.base, "--token", "owner-tok", "share", "create", "r9-runner-step"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(json.loads(r.stdout)["url"], r"/_invite/[^/]+\.md$")

    def test_a_collaborator_invite_for_a_post_says_first_how_to_attach_and_answer(self):
        u = self.store.create_user("helper", role="member")
        st, page = self._req("GET", "/_invite/%s.md?post=r9-runner-step" % u["token"])
        self.assertEqual(st, 200, page)
        head = page.index("## First: attach your files to post `r9-runner-step`")
        self.assertLess(head, page.index("## 0."))
        self.assertIn("/_api/posts/r9-runner-step/artifacts", page[head:page.index("## 0.")])


if __name__ == "__main__":
    unittest.main()
