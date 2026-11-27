"""CL9b: cheap breaks for any work. 1. `breaks: revert` makes the old version the made-to-fail one; 2. `asked` ties each
step to his words; 3. `kit.py pair` shows a blind before/after pair; 4. the step review rechecks a random sample of bars.
Each runs on a demo project with the kit installed, as a part runs it."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "scripts", "kit.py")

MUL_OK = "python3 -c \"import calc; assert calc.mul(2, 3) == 6\""
QUOTE = "Any way to do this without wasting tokens"
# a stand-in for a render: the "picture" is the bytes calc draws (PNG header + what draw() returns)
SHOT = "python3 -c \"import os, calc; open(os.environ['KIT_SHOT'], 'wb').write(b'\\x89PNG\\r\\n' + calc.draw().encode())\""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                          cwd=cwd, capture_output=True, text=True, check=True)


# CL76: a demo step written the new way: his intent first, a test naming each case and a stress variant
DEMO_INTENT = {"goal": "\"the demo works\"", "real": "none: a demo", "at": "2000-01-01T00:00:00Z",
               "cases": [{"id": "I1", "text": "a", "outside": False, "not_covered": None},
                         {"id": "I2", "text": "b", "outside": False, "not_covered": None},
                         {"id": "I3", "text": "c", "outside": True, "not_covered": None}]}
DEMO_TEST = "# I1 stress I1 I2 stress I2 I3 stress I3\n"


def post(**kw):
    p = {"title": "demo", "part": "DM2", "train": "demo", "column": "info",
         "verdict": "mul() multiplies two numbers.", "bars": ["6 of 6 products right"], "artifacts": ["calc.py"],
         "not_checked": ["floats"], "check": MUL_OK, "breaks": "revert", "handoff": "use calc.mul",
         "body": "For you: nothing to do.", "intent": DEMO_INTENT}
    p.update(kw)
    return p


class Demo(unittest.TestCase):
    """A trunk with add() and a dark sky; the step's branch adds mul() and turns the sky to aurora green."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kit-cheap-")
        self.repo = os.path.join(self.tmp, "demo")
        os.makedirs(os.path.join(self.repo, ".kit"))
        shutil.copy(KIT, os.path.join(self.repo, ".kit", "kit.py"))
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write(".kit/run/\n__pycache__/\n")
        self.write("def add(a, b):\n    return a + b\n\ndef draw():\n    return 'sky:dark'\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "trunk")
        git(self.repo, "checkout", "-q", "-b", "step")
        self.write("def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n\ndef draw():\n    return 'sky:aurora-green'\n")
        with open(os.path.join(self.repo, "test_calc.py"), "w") as fh:
            fh.write(DEMO_TEST)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qam", "step: mul and the aurora")
        self.db = os.path.join(self.tmp, "board.db")
        self.comments = {}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, src):
        with open(os.path.join(self.repo, "calc.py"), "w") as fh:
            fh.write(src)

    def kit(self, p, *args):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT "
                     "NULL DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR REPLACE INTO docs VALUES ('posts', 'dm2', 1, ?, ?)", (json.dumps(p), now))
        for cid, c in self.comments.items():
            conn.execute("INSERT OR REPLACE INTO docs VALUES ('comments', ?, 1, ?, ?)", (cid, json.dumps(c), now))
        conn.commit()
        conn.close()
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_BOARD_TOKEN", "KIT_BREAKS_COPY")}
        env["KIT_BOARD_URL"] = "http://127.0.0.1:9"  # no board server: the post comes from the local database
        env["KIT_SAMPLE_SEED"] = "7"
        r = subprocess.run([sys.executable, "-I", ".kit/kit.py"] + list(args) + ["--db", self.db], cwd=self.repo,
                           capture_output=True, text=True, env=env, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def done(self, p):
        return self.kit(p, "done", "dm2")


class Revert(Demo):
    """Step 1: the made-to-fail version is the old one, no new art."""

    def test_revert_makes_a_check_of_the_new_work_fail(self):
        rc, out = self.done(post())
        self.assertEqual(rc, 0, out)
        self.assertIn("fails after breaks", out)
        with open(os.path.join(self.repo, "calc.py")) as fh:
            self.assertIn("def mul", fh.read(), "the revert never touches the work itself")
        self.assertEqual(len(git(self.repo, "worktree", "list").stdout.strip().splitlines()), 1)

    def test_a_check_that_passes_on_the_old_version_too_is_refused(self):
        rc, out = self.done(post(check="python3 -c \"import calc; assert calc.add(2, 2) == 4\""))
        self.assertEqual(rc, 1, out)
        self.assertIn("still passes after breaks", out)

    def test_uncommitted_work_is_reverted_too(self):
        git(self.repo, "reset", "-q", "--soft", "main")  # the step's change, not committed yet
        rc, out = self.done(post())
        self.assertEqual(rc, 0, out)

    def test_nothing_to_revert_is_refused(self):
        git(self.repo, "checkout", "-q", "main")
        with open(os.path.join(self.repo, "test_calc.py"), "w") as fh:   # CL76: a part on the trunk, its test committed there
            fh.write(DEMO_TEST)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "the test")
        rc, out = self.done(post(check="python3 -c \"import calc; assert calc.add(2, 2) == 4\"", started_at="2000-01-01T00:00:00Z"))
        self.assertEqual(rc, 1, out)
        self.assertIn("nothing to revert", out)

    def test_an_explicit_base(self):
        rc, out = self.done(post(breaks="revert main"))
        self.assertEqual(rc, 0, out)


class Asked(Demo):
    """Step 2: his words come with the step, and each one is answered somewhere."""

    def setUp(self):
        super().setUp()
        self.comments = {"c1": {"post_id": "x", "from": "owner", "text": "That sounds awfully token expensive. " + QUOTE + "?"}}

    def test_an_ask_answered_nowhere_is_refused(self):
        rc, out = self.done(post(asked=[{"id": "c1", "quote": QUOTE}]))
        self.assertEqual(rc, 1, out)
        self.assertIn("asked c1: answered by no bar and no not_checked", out)

    def test_an_ask_a_bar_answers_is_accepted(self):
        rc, out = self.done(post(asked=[{"id": "c1", "quote": QUOTE}], bars=["6 of 6 products right (asked c1)"]))
        self.assertEqual(rc, 0, out)

    def test_a_quote_that_is_not_in_his_comment_is_refused(self):
        rc, out = self.done(post(asked=[{"id": "c1", "quote": "make it purple"}], bars=["6 of 6 products right (asked c1)"]))
        self.assertEqual(rc, 1, out)
        self.assertIn("asked c1: the quote is not in that comment", out)

    def test_a_comment_not_on_this_board_is_a_note_not_a_refusal(self):
        rc, out = self.done(post(asked=[{"id": "c9", "quote": "x y z"}], not_checked=["floats (asked c9)"]))
        self.assertEqual(rc, 0, out)
        self.assertIn("asked c9: comment not on this board", out)


class Pair(Demo):
    """Step 3: a blind before/after pair: his words and two pictures, nothing else."""

    def test_the_pair_is_blind_and_scored(self):
        p = post(shot=SHOT, asked=[{"id": "c1", "quote": QUOTE}], bars=["6 of 6 products right (asked c1)"])
        rc, out = self.kit(p, "pair", "dm2")
        self.assertEqual(rc, 0, out)
        self.assertIn(QUOTE, out)
        d = os.path.join(self.repo, ".kit", "run", "pairs", "dm2")
        a, b = open(os.path.join(d, "A.png"), "rb").read(), open(os.path.join(d, "B.png"), "rb").read()
        self.assertNotEqual(a, b)
        self.assertNotIn("aurora", out, "the brief never says which picture is new")
        new = "A" if b"aurora" in a else "B"
        rc, out = self.kit(p, "pair", "dm2", "--answer", new)
        self.assertEqual(rc, 0, out)
        self.assertIn("right: %s is the new version" % new, out)
        rc, out = self.kit(p, "pair", "dm2", "--answer", "B" if new == "A" else "A")
        self.assertEqual(rc, 1, out)
        self.assertIn("wrong", out)

    def test_a_change_that_does_not_show_is_refused(self):
        # the step changes calc.mul only: the picture is the same before and after
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", "-b", "step2")
        with open(os.path.join(self.repo, "calc.py"), "a") as fh:
            fh.write("\ndef mul(a, b):\n    return a * b\n")
        git(self.repo, "commit", "-qam", "mul only")
        rc, out = self.kit(post(shot=SHOT), "pair", "dm2")
        self.assertEqual(rc, 1, out)
        self.assertIn("the change is not visible", out)

    def test_no_shot_says_how(self):
        rc, out = self.kit(post(), "pair", "dm2")
        self.assertEqual(rc, 1, out)
        self.assertIn("shot:", out)


class Sample(unittest.TestCase):
    """Step 4: the review rechecks one bar in five at random (at least one), plus every [visual] bar."""

    def kit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_cheap", KIT)
        k = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(k)
        return k

    def test_one_in_five_plus_every_visual(self):
        k = self.kit()
        bars = ["%d of 10 right" % i for i in range(10)] + ["[visual] the aurora reads as ribbons, 1 of 1"]
        picked = k.review_sample(bars, seed=7)
        self.assertIn(bars[-1], picked)
        self.assertEqual(len([b for b in picked if "[visual]" not in b]), 2)
        self.assertNotEqual(picked, k.review_sample(bars, seed=8), "the pick changes with the draw")

    def test_at_least_one(self):
        self.assertEqual(self.kit().review_sample(["1 of 1 right"], seed=1), ["1 of 1 right"])

    def test_the_step_review_brief_carries_the_sample_and_his_words(self):
        k = self.kit()
        p = post(asked=[{"id": "c1", "quote": QUOTE}], bars=["%d of 10 right" % i for i in range(10)])
        txt = k.review_brief(p, seed=7)
        self.assertIn(QUOTE, txt)
        self.assertIn("c1", txt)
        self.assertEqual(txt.count("\n- recheck: "), 2)


if __name__ == "__main__":
    unittest.main()
