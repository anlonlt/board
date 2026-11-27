"""CL73: the stop check (`kit.py artifact status`, CL30's guard) and `artifact push` count only what this session changed.

A web session's local board is seeded from git (board-mirror): some 360 posts and comments with no sync base. The check
counted each one as "changed here" (`base != data` with `base is None`), so a Stop was blocked with "NOT SENT: 361
document(s)" and the session was told to push old copies of posts it never touched. A document with no base counts only
when this copy wrote it (the local board's changes log, after the mark `artifact load` leaves).

Run via: python3 -m unittest tests.test_cl73_stop_counts_own -v
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

POST = "cl73-x"
SEEDED = 300


class StopCountsOwn(unittest.TestCase):
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
        # the git seed (board-mirror): 300 posts and a comment each, none ever read from the Artifact here
        for i in range(SEEDED):
            self.local("posts/old-%03d" % i, {"title": "old %d" % i, "updated_at": "2026-11-20T00:00:00Z"})
        self.local("comments/old-c", {"post_id": "old-001", "text": "old", "created_at": "2026-11-20T00:00:00Z"})

    def local(self, path, data):
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def remote(self, coll, did, data):
        d = self.art / "in" / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def status(self):
        return kit.artifact_unsent(url=self.url, token=TOKEN)["changed"]

    def test_seeded_load_one_post_edit_it_counts_one(self):
        """The task's check: seeded from git with 300 posts, `pull --post` one, edit it: status says 1 changed."""
        kit.artifact_pull(post=POST)
        post = {"title": "CL73", "status": "in progress", "updated_at": "2026-11-27T01:00:00Z"}
        self.remote("posts", POST, post)
        kit.artifact_load(url=self.url, token=TOKEN, versions=["posts/%s=2" % POST])
        self.assertEqual(self.status(), 0)
        self.local("posts/" + POST, dict(post, body="done", updated_at="2026-11-27T02:00:00Z"))
        self.assertEqual(self.status(), 1)
        r = kit.artifact_push(url=self.url, token=TOKEN)
        got = [(e["collection"], e["doc_id"]) for p in r["batches"] for e in json.loads(Path(p).read_text())]
        self.assertEqual(got, [("posts", POST)])

    def test_stop_in_a_session_that_changed_nothing_is_not_blocked(self):
        """A copy seeded from git that changed nothing: no document counts, `artifact status` says sent (exit 0);
        a document this copy then writes, with no base, counts."""
        self.assertEqual(self.status(), 0)
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "artifact", "status",
                              "--board-url", self.url, "--token", TOKEN], capture_output=True, text=True,
                             env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("sent:", out.stdout)
        self.local("comments/new-c", {"post_id": POST, "text": "mine", "created_at": "2026-11-27T02:00:00Z"})
        self.assertEqual(self.status(), 1)

    def test_f41_rebuilt_cards_at_start_not_counted_unsent(self):
        """F41: a fresh session whose _dashboard and _trains were rebuilt at start (zero real post/comment edits)
        reports 0 unsent so Stop is never blocked."""
        self.remote("posts", "_dashboard", {"title": "Where we are", "updated_at": "2026-11-27T01:00:00Z", "rows": [{"key": "A"}]})
        self.remote("posts", "_trains", {"title": "Trains", "updated_at": "2026-11-27T01:00:00Z", "rows": [{"part": "CL1"}]})
        kit.artifact_load(url=self.url, token=TOKEN)
        self.local("posts/_dashboard", {"title": "Where we are", "updated_at": "2026-11-27T09:20:00Z", "rows": [{"key": "A"}]})
        self.local("posts/_trains", {"title": "Trains", "updated_at": "2026-11-27T09:20:00Z", "rows": [{"part": "CL1"}, {"part": "CL2"}]})
        self.assertEqual(self.status(), 0)

    def test_f42_live_kit_docs_never_blind_set_without_version(self):
        """F42: a doc already listed in live kit_docs/_index.docs with unknown local version goes to need_pin, never a
        blind set without if_version; untouched git-seeded posts never trigger doc mirroring."""
        kit.artifact_pull(post=POST)
        post = {"title": "CL73", "body": "Read `README.md`.", "updated_at": "2026-11-27T01:00:00Z"}
        self.remote("posts", POST, post)
        self.remote("kit_docs", "_index", {"docs": [{"path": "README.md"}], "assets": {}})
        kit.artifact_load(url=self.url, token=TOKEN, versions=["posts/%s=2" % POST])
        # Put _index back in in/ so _artifact_files reads the pulled index (as after artifact pull)
        self.remote("kit_docs", "_index", {"docs": [{"path": "README.md"}], "assets": {}})
        r = kit.artifact_push(url=self.url, token=TOKEN)
        writes = [e for p in r["batches"] for e in json.loads(Path(p).read_text())]
        blind_kit_docs = [e for e in writes if e["collection"].endswith("kit_docs") and "if_version" not in e]
        self.assertEqual(blind_kit_docs, [], writes)
        self.assertIn("kit_docs/README.md", r["need_pin"])


if __name__ == "__main__":
    unittest.main()
