"""A part starts on the newest work of its step (CL44 item 13), whoever launched it.

agy starts a part in a fresh worktree made from the orchestrator's commit; `claude -w` and the web do the same. When the
step's post names a branch holding work that checkout lacks, `resume --role part` brings it in (fast-forward, else a
merge) in the part's own checkout only, and otherwise says first thing what to run. Nothing is merged on a checkout that
is not the part's (the orchestrator's, the owner's), with uncommitted changes, or on a conflict.

Run via: python3 -m unittest tests.test_part_starts_on_its_work -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import kit  # noqa: E402


def git(d, *a):
    r = subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


class PartStartsOnItsWork(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        git(self.d, "init", "-q", "-b", "main")
        git(self.d, "config", "user.email", "t@t"); git(self.d, "config", "user.name", "t")
        Path(self.d, "a.txt").write_text("base\n"); git(self.d, "add", "."); git(self.d, "commit", "-qm", "base")
        git(self.d, "checkout", "-qb", "cl44")                              # the first part's saved work
        Path(self.d, "work.txt").write_text("saved\n"); git(self.d, "add", "."); git(self.d, "commit", "-qm", "saved work")
        self.saved = git(self.d, "rev-parse", "cl44")
        git(self.d, "checkout", "-q", "main")
        self.p = {"part": "CL44", "branch": "cl44", "session_id": "old"}
        self.root = mock.patch.object(kit, "ROOT", self.d); self.root.start()

    def tearDown(self):
        self.root.stop()

    def has_saved(self):
        return subprocess.run(["git", "-C", self.d, "merge-base", "--is-ancestor", self.saved, "HEAD"]).returncode == 0

    def test_a_fresh_part_checkout_is_fast_forwarded(self):
        git(self.d, "checkout", "-qb", "subagent-Part--CL44-self-1234")
        out = "\n".join(kit.take_step_branch(self.p))
        self.assertTrue(self.has_saved()); self.assertIn("fast-forward", out)
        self.assertEqual(kit.take_step_branch(self.p), [])                 # once in, nothing more to say

    def test_a_part_with_its_own_commit_gets_a_merge(self):
        git(self.d, "checkout", "-qb", "cl44-again")
        Path(self.d, "b.txt").write_text("new\n"); git(self.d, "add", "."); git(self.d, "commit", "-qm", "own")
        self.assertIn("(merge,", "\n".join(kit.take_step_branch(self.p)))
        self.assertTrue(self.has_saved())

    def test_uncommitted_changes_stop_it_and_say_what_to_run(self):
        git(self.d, "checkout", "-qb", "subagent-Part--CL44-x")
        Path(self.d, "a.txt").write_text("changed\n")
        out = "\n".join(kit.take_step_branch(self.p))
        self.assertIn("## FIRST", out); self.assertIn("git merge --no-edit cl44", out); self.assertFalse(self.has_saved())

    def test_a_conflict_changes_nothing(self):
        git(self.d, "checkout", "-qb", "subagent-Part--CL44-y")
        Path(self.d, "work.txt").write_text("other\n"); git(self.d, "add", "."); git(self.d, "commit", "-qm", "clash")
        before = git(self.d, "rev-parse", "HEAD")
        self.assertIn("conflict", "\n".join(kit.take_step_branch(self.p)))
        self.assertEqual(git(self.d, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.d, "status", "--porcelain"), "")

    def test_someone_elses_checkout_is_never_merged(self):
        git(self.d, "checkout", "-qb", "feat/streamline-doctor-memory")
        out = "\n".join(kit.take_step_branch(self.p))
        self.assertIn("## FIRST", out); self.assertIn("nothing was changed", out); self.assertFalse(self.has_saved())

    def test_no_branch_or_nothing_missing_says_nothing(self):
        self.assertEqual(kit.take_step_branch({"part": "CL44"}), [])
        git(self.d, "checkout", "-q", "cl44")
        self.assertEqual(kit.take_step_branch(self.p), [])


if __name__ == "__main__":
    unittest.main()
