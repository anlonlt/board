"""kit.py reconcile: the board here against its git copy, the newer copy of each document wins.

The case it is for: the computer's board stops (a usage limit, a crash), a web session carries on from git and saves
there; when the terminal keyword opens the board again, both sides' work must be on it, and in git.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import Replica, TOKEN, git, kit  # noqa: E402


class TestReconcile(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "board-mirror", str(self.remote))
        seed = root / "seed"
        git(root, "clone", "-q", str(self.remote), str(seed))
        git(seed, "-c", "user.name=seed", "-c", "user.email=seed@example.invalid", "commit", "-q", "--allow-empty", "-m", "Empty")
        git(seed, "push", "-q", "origin", "HEAD:board-mirror")
        self.root = root
        # the computer (A) and the web session (B)
        self.a, self.b = (Replica(root / n, self.remote, stack) for n in ("A", "B"))
        self.a.write("posts/P1", {"title": "Original", "column": "info"})
        self.a.write("posts/P3", {"title": "P3 original", "column": "info"})
        self.a.publish()
        self.b.read_in()

    def reconcile(self, r):
        return kit.reconcile(str(r.mirror), url=r.url, token=TOKEN, branch="board-mirror", db_path=str(r.db))

    def test_web_work_comes_back_and_local_work_stays(self):
        a, b = self.a, self.b
        a.write("posts/local-only", {"title": "Only on the computer"})          # never reached git (it stopped)
        time.sleep(1.1)
        b.write("posts/P1", {"title": "Done on the web", "column": "info", "train": "t", "part": "T1", "status": "complete"})
        b.write("posts/P3", {"title": "P3 by the web", "column": "info"})
        b.write("comments/R1", {"post_id": "P1", "text": "finished on the web", "from": "T1"})
        b.write("posts/W1", {"title": "A step the web planned", "column": "info", "train": "t", "part": "T2"})
        b.publish()
        time.sleep(1.1)
        a.write("posts/P3", {"title": "P3 edited here, after the web", "column": "info"})

        r = self.reconcile(a)
        # added: R1, W1, and the cards the web board derived from W1 (posts/_trains)
        self.assertEqual((r["from_git"], r["kept_here"]), (1, 1), r)
        self.assertGreaterEqual(r["added"], 2, r)
        self.assertEqual(a.get("posts/P1")["data"]["title"], "Done on the web")            # newer in git
        self.assertEqual(a.get("comments/R1")["data"]["text"], "finished on the web")       # only in git
        self.assertEqual(a.get("posts/W1")["data"]["part"], "T2")
        self.assertEqual(a.get("posts/P3")["data"]["title"], "P3 edited here, after the web")  # newer here
        self.assertTrue(a.get("posts/local-only")["exists"])

        # git now holds both sides
        check = self.root / "check"
        git(self.root, "clone", "-q", "-b", "board-mirror", str(self.remote), str(check))
        posts = {p.stem: json.loads(p.read_text()) for p in (check / "board" / "posts").glob("*.json")}
        self.assertEqual(set(posts) >= {"P1", "P3", "W1", "local-only"}, True, sorted(posts))
        self.assertEqual(posts["P3"]["title"], "P3 edited here, after the web")
        self.assertEqual(posts["P1"]["title"], "Done on the web")

        # the task list rebuilt from the reconciled board names the web's step
        out = kit.resume(str(a.mirror), db_path=str(a.db), alive=set())
        self.assertIn("T2", out)

        # a second run has nothing to do
        r2 = self.reconcile(a)
        self.assertEqual((r2["from_git"], r2["added"], r2["kept_here"]), (0, 0, 0), r2)

    def test_crash_inverse_web_takes_the_computers_saved_board(self):
        a, b = self.a, self.b
        time.sleep(1.1)
        a.write("posts/P1", {"title": "Saved by the computer before it crashed", "column": "info"})
        a.publish()
        r = self.reconcile(b)
        self.assertEqual(r["from_git"], 1, r)
        self.assertEqual(b.get("posts/P1")["data"]["title"], "Saved by the computer before it crashed")

    def test_cli_wiring(self):
        res = self.a.kit_cli("reconcile", "--board-url", self.a.url, "--token", TOKEN, "--branch", "board-mirror",
                             "--db", str(self.a.db))
        self.assertIn("Board vs git: reconciled", res.stdout)


if __name__ == "__main__":
    unittest.main()
