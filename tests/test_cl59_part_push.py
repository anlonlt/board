"""CL59: a part pushes only its own post; a pull never deadlocks on waiting batches.

The owner, 2026-11-27 (CL17's session): "How did it have so many failed commands? Was it stupid?" CL17 pulled only its
own post (`artifact pull --post`), then `artifact push` diffed the whole local copy against that partial pull and built
330 unpinned writes; the push had no single-post option, the guard refused a hand-made write and refused a pull while
built batches waited. These tests replay that case and fail on the code before CL59.

Run via: python3 -m unittest tests.test_cl59_part_push -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req, kit  # noqa: E402
import test_cl45_artifact_reconcile as t45  # noqa: E402

POST = "cl17-x"
SEEDED = 330                       # CL17's count: the documents the push built without having read them
TRAINS = {"title": "Trains", "column": "info", "updated_at": "2026-11-27T01:00:00Z", "rows": [
    {"part": "CL17", "post": POST, "state": "running", "store": []}]}


class PartPush(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (self.tmp / "proj").mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.tmp / "proj")],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=60), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.art = self.tmp / "art"
        for k, v in (("KIT_ARTIFACT_DIR", str(self.art)), ("KIT_ARTIFACT_PREFIX", ""), ("KIT_BOARD_URL", self.url)):
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def remote(self, coll, did, data):
        d = self.art / "in" / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def local(self, path, data):
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def writes(self, r):
        return [e for p in r["batches"] for e in json.loads(Path(p).read_text())]

    def cl17(self):
        """CL17's copy: `pull --post` (its post and the cards, by id), `load` with the versions the gets named; then
        330 documents this copy never read from the Artifact land on the local board (a git seed, an old copy), and the
        part edits its post and writes two comments under it."""
        text = kit.artifact_pull(post=POST)
        self.assertIn("doc_id: \"%s\"" % POST, text)
        self.assertIn("artifact load posts/%s=<N>" % POST, text)
        post = {"title": "CL17", "status": "in progress", "updated_at": "2026-11-27T01:00:00Z", "train": "board-data"}
        self.remote("posts", POST, post)
        self.remote("posts", "_trains", TRAINS)
        kit.artifact_load(url=self.url, token=TOKEN, versions=["posts/%s=4" % POST, "posts/_trains=60"])
        for i in range(SEEDED):
            self.local("posts/old-%03d" % i, {"title": "old %d" % i, "updated_at": "2026-11-20T00:00:00Z"})
        self.local("posts/" + POST, dict(post, body="CL17's result", updated_at="2026-11-27T02:00:00Z"))
        for c in ("c1", "c2"):
            self.local("comments/" + c, {"post_id": POST, "text": "note " + c, "created_at": "2026-11-27T02:00:00Z"})
        self.local("comments/other", {"post_id": "old-001", "text": "not CL17's", "created_at": "2026-11-27T02:00:00Z"})

    def test_push_post_builds_only_its_post_and_comments_pinned(self):
        """1 and 5: `artifact push --post` builds 1 post + its 2 comments, the post pinned to the version read."""
        self.cl17()
        r = kit.artifact_push(url=self.url, token=TOKEN, post=POST)
        got = sorted((e["collection"], e["doc_id"], e.get("if_version")) for e in self.writes(r))
        self.assertEqual(got, [("comments", "c1", None), ("comments", "c2", None), ("posts", POST, 4)])
        self.assertEqual((r["writes"], r["need_pin"]), (3, []))

    def test_cli_push_post(self):
        """1: the command itself (`kit.py artifact push --post <id>`), as a part runs it."""
        self.cl17()
        env = dict(os.environ, KIT_ARTIFACT_ON_COMPUTER="1", KIT_BOARD_TOKEN=TOKEN, KIT_TEST="1")
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "artifact", "push", "--post", POST,
                              "--board-url", self.url, "--token", TOKEN], capture_output=True, text=True, env=env,
                             cwd=str(self.tmp / "proj"), timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        batches = [b for b in (self.art / "out").glob("batch-*.json")]
        self.assertEqual(len(batches), 1, out.stdout)
        self.assertEqual(sorted(e["doc_id"] for e in json.loads(batches[0].read_text())), ["c1", "c2", POST])

    def test_a_push_after_a_partial_pull_never_sends_what_it_did_not_read(self):
        """2: a plain push after `pull --post` sends the same 3 writes; the 330 unread documents and the comment under
        another post are named, never sent, and never counted unsent."""
        self.cl17()
        r = kit.artifact_push(url=self.url, token=TOKEN)
        # the cards its pull fetched by id go too when the board rebuilt them here; nothing it did not read
        self.assertEqual(sorted(e["doc_id"] for e in self.writes(r) if not e["doc_id"].startswith("_")), ["c1", "c2", POST])
        self.assertEqual(len(r["unread"]), SEEDED + 1)
        self.assertIn("comments/other", r["unread"])
        self.assertIn("never read from the Artifact", kit.artifact_push_text(r))
        for p in r["batches"]:
            kit.artifact_sent(p)
        self.assertEqual(kit.artifact_unsent(url=self.url, token=TOKEN)["changed"], 0)

    def test_a_full_pull_reads_the_whole_board_again(self):
        """2, the other side: after `pull --full` the copy is no longer partial and a push sends what changed."""
        self.cl17()
        kit.artifact_pull(full=True)
        kit.artifact_load(url=self.url, token=TOKEN)
        self.local("posts/old-007", {"title": "edited after a full pull", "updated_at": "2026-11-27T03:00:00Z"})
        r = kit.artifact_push(url=self.url, token=TOKEN)
        self.assertIn("old-007", [e["doc_id"] for e in self.writes(r)])

    def test_a_pull_while_batches_wait_rebuilds_them(self):
        """3: batches built and not sent (pending.json out, the guard's lock) never block a pull: it drops them and
        frees the guard; the load rebuilds them the same way (--post kept) against what the pull brought."""
        self.cl17()
        r = kit._pending_record(kit.artifact_push(url=self.url, token=TOKEN, post=POST))
        self.assertTrue(r["batches"])
        self.assertTrue((self.art / "pending.json").exists())
        text = kit.artifact_pull()
        self.assertIn("1 batch(es) built and not recorded sent were waiting: dropped", text)
        self.assertEqual(list((self.art / "out").glob("batch-*.json")), [])
        self.assertFalse((self.art / "pending.json").exists())       # the guard lets the pull's calls through
        self.remote("posts", "_trains", dict(TRAINS, updated_at="2026-11-27T02:30:00Z"))
        loaded = kit.artifact_load(url=self.url, token=TOKEN)
        again = loaded["rebuilt"]
        self.assertEqual(sorted(e["doc_id"] for e in self.writes(again)), ["c1", "c2", POST])
        self.assertTrue((self.art / "pending.json").exists())
        self.assertNotIn("rebuild", kit._art_state())                 # once

    def test_load_refuses_a_bad_version(self):
        with self.assertRaises(kit.Conflict):
            kit.artifact_load(url=self.url, token=TOKEN, versions=["posts/x=latest"])


class CardsNeverConflict(t45.ArtifactReconcile):
    """4: _dashboard and _trains changed on both sides since the last reconcile: the newer build wins, no comment."""

    def test_generated_cards_take_the_newer_build(self):
        old = {"title": "Trains", "column": "info", "updated_at": "2026-11-27T00:00:00Z", "rows": []}
        c = self.computer
        c.write("posts/_trains", old)
        c.publish()
        self.artifact("posts", "_trains", old)
        self.assertEqual(self.reconcile()["conflicts"], [])
        c.read_in()
        c.write("posts/_trains", dict(old, rows=[{"part": "A"}], updated_at="2026-11-27T02:00:00Z"))
        c.publish()
        self.artifact("posts", "_trains", dict(old, rows=[{"part": "B"}], updated_at="2026-11-27T01:00:00Z"))
        r = self.reconcile()
        self.assertEqual(r["conflicts"], [], kit.artifact_reconcile_line(r))
        self.assertEqual(self.web("posts/_trains")["data"]["rows"], [{"part": "A"}])
        self.assertEqual([d for d in api_req(self.url, "db/comments", token=TOKEN)["docs"]
                          if (d["data"] or {}).get("kind") == "conflict"], [])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(PartPush))
    suite.addTest(CardsNeverConflict("test_generated_cards_take_the_newer_build"))
    return suite


if __name__ == "__main__":
    unittest.main()
