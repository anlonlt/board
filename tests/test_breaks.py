"""Checks that must be able to fail: `kit.py done` runs a post's `check` (must pass), applies its `breaks` in a throwaway
copy of the work and reruns `check` (must fail). A demo step: a tiny project with the kit installed, run as a part runs it."""
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

# the planted fault: add() subtracts (a python one-liner, so it runs the same on Linux and macOS, unlike sed -i)
PLANT = "python3 -c \"p='calc.py'; open(p,'w').write(open(p).read().replace('a + b', 'a - b'))\""
CAN_FAIL = "python3 -c \"import calc; assert calc.add(2, 2) == 4\""
CANNOT_FAIL = "python3 -c \"import calc\""  # only imports: no fault in add() can make it fail


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                          cwd=cwd, capture_output=True, text=True, check=True)


def post(**kw):
    p = {"title": "demo", "part": "DM1", "train": "demo", "column": "info",
         "verdict": "add() adds two numbers.", "bars": ["4 of 4 sums right"], "artifacts": ["calc.py"],
         "not_checked": ["floats"], "check": CAN_FAIL, "breaks": PLANT, "handoff": "use calc.add",
         "body": "For you: nothing to do."}
    p.update(kw)
    return p


class DemoStep(unittest.TestCase):
    """The bar: on a demo step, a check that cannot fail is refused, one that can is accepted."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kit-demo-")
        self.repo = os.path.join(self.tmp, "demo")
        os.makedirs(os.path.join(self.repo, ".kit"))
        shutil.copy(KIT, os.path.join(self.repo, ".kit", "kit.py"))
        with open(os.path.join(self.repo, "calc.py"), "w") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "demo")
        self.db = os.path.join(self.tmp, "board.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def done(self, p):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT "
                     "NULL DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
        conn.execute("INSERT OR REPLACE INTO docs VALUES ('posts', 'dm1', 1, ?, ?)",
                     (json.dumps(p), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_BOARD_TOKEN", "KIT_BREAKS_COPY")}
        env["KIT_BOARD_URL"] = "http://127.0.0.1:9"  # no board server: the post comes from the local database
        r = subprocess.run([sys.executable, "-I", ".kit/kit.py", "done", "dm1", "--db", self.db], cwd=self.repo,
                           capture_output=True, text=True, env=env, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def untouched(self):
        with open(os.path.join(self.repo, "calc.py")) as fh:
            self.assertIn("a + b", fh.read(), "breaks must never touch the work itself")
        wt = git(self.repo, "worktree", "list").stdout.strip().splitlines()
        self.assertEqual(len(wt), 1, "the throwaway worktree is removed: %s" % wt)

    def test_a_check_that_cannot_fail_is_refused(self):
        rc, out = self.done(post(check=CANNOT_FAIL))
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT READY", out)
        self.assertIn("check: still passes after breaks: a check that cannot fail is refused", out)
        self.untouched()

    def test_a_check_that_can_fail_is_accepted(self):
        rc, out = self.done(post())
        self.assertEqual(rc, 0, out)
        self.assertIn("OK: dm1 follows the post format", out)
        self.assertIn("fails after breaks (exit 1): it can fail", out)
        self.untouched()

    def test_a_check_without_breaks_is_refused(self):
        rc, out = self.done(post(breaks=""))
        self.assertEqual(rc, 1, out)
        self.assertIn("breaks: missing", out)

    def test_a_check_that_fails_already_is_refused(self):
        rc, out = self.done(post(check="python3 -c \"import calc; assert calc.add(2, 2) == 5\""))
        self.assertEqual(rc, 1, out)
        self.assertIn("check: fails before breaks (exit 1)", out)

    def test_breaks_written_as_prose_is_refused_with_the_way_out(self):
        rc, out = self.done(post(breaks="Change add so it subtracts"))
        self.assertEqual(rc, 1, out)
        self.assertIn("breaks: did not apply", out)
        self.assertIn("manual:", out)

    def test_a_manual_breaks_is_left_to_the_review(self):
        rc, out = self.done(post(breaks="manual: open the board on a phone, fold a section, reload: it stays open"))
        self.assertEqual(rc, 0, out)
        self.assertIn("breaks is manual: the review performs it", out)

    def test_a_manual_check_is_not_run(self):
        rc, out = self.done(post(check="manual: tap the chevron in the browser", breaks="manual: remove the chevron"))
        self.assertEqual(rc, 0, out)
        self.assertIn("check is manual", out)

    def test_uncommitted_edits_and_new_files_are_in_the_copy(self):
        # the part runs done before committing: the copy must hold the work as it stands
        with open(os.path.join(self.repo, "calc.py"), "w") as fh:
            fh.write("from helper import plus\n\ndef add(a, b):\n    return plus(a, b)\n")
        with open(os.path.join(self.repo, "helper.py"), "w") as fh:
            fh.write("def plus(a, b):\n    return a + b\n")
        breaks = "python3 -c \"p='helper.py'; open(p,'w').write(open(p).read().replace('a + b', 'a * b + 1'))\""
        rc, out = self.done(post(breaks=breaks))
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.repo, "helper.py")) as fh:
            self.assertIn("a + b", fh.read())
        self.assertEqual(len(git(self.repo, "worktree", "list").stdout.strip().splitlines()), 1)

    def test_outside_git_a_temp_copy_is_used(self):
        shutil.rmtree(os.path.join(self.repo, ".git"))
        rc, out = self.done(post(check=CANNOT_FAIL))
        self.assertIn("a check that cannot fail is refused", out)
        rc, out = self.done(post())
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.repo, "calc.py")) as fh:
            self.assertIn("a + b", fh.read())


class TrainShowsBreaks(unittest.TestCase):
    def test_breaks_line_in_the_train_view(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_breaks", KIT)
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        tmp = tempfile.mkdtemp()
        try:
            db = os.path.join(tmp, "board.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT NULL "
                         "DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
            conn.execute("INSERT INTO docs VALUES ('posts', 'DM1-a', 1, ?, '2026-01-01')",
                         (json.dumps(dict(post(), created_at="2026-01-01")),))
            conn.commit()
            conn.close()
            txt = kit.train_context(tmp, "demo", db_path=db, level="sections")
            self.assertIn("*Breaks:* " + PLANT, txt)
            steps = json.loads(kit.train_context(tmp, "demo", db_path=db, as_json=True))["steps"]
            self.assertEqual(steps[0]["breaks"], PLANT)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
