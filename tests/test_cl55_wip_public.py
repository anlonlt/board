"""CL55: work in progress is on GitHub within minutes, public repos included, and never with a secret in it.

Real repositories (a bare origin, clones), the real save (`.kit/landing.py save`), the real hook and the real takeover.
Run via: python3 -m unittest tests.test_cl55_wip_public -v
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_landing import ROOT, Repo  # noqa: E402
from test_post_gate import load  # noqa: E402

# Built at run time, so this file itself carries no secret-looking line (the save would refuse it).
TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0" * 2


class Public(Repo):
    """No `wip` in its config: the default, on a repo GitHub cannot call private (a bare origin is not even on GitHub)."""
    conf = {k: v for k, v in Repo.conf.items() if k != "wip"}

    def setUp(self):
        super().setUp()
        self.g("checkout", "-q", "-b", "s1")
        self.put("a.txt", "one\n")
        self.g("push", "-q", "-u", "origin", "s1")

    def save(self, d=None, **env):
        r = self.run_("landing.py", "save", d=d, **env)
        return r.returncode, r.stdout.strip()

    def status(self, d=None):
        return json.loads(Path(d or self.d, ".kit", "run", "wip-status.json").read_text())

    def test_a_public_repos_save_is_pushed(self):
        self.put("a.txt", "two\n", commit=False)
        self.put(".env", "X=1\n", commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 0, txt)
        self.assertIn("on origin/wip/s1", txt)
        self.assertIn("left out: .env", txt)
        self.assertEqual(self.g("show", "origin/wip/s1:a.txt").stdout, "two\n")
        self.assertFalse(self.has(".env", "wip/s1"))
        self.assertIn(".env", self.g("log", "-1", "--format=%B", "origin/wip/s1").stdout)       # named in the save's message

    def test_a_planted_token_is_refused_and_says_why(self):
        self.put("config.py", "API = %r\n" % TOKEN, commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", txt)
        self.assertIn("config.py:1 (a GitHub token)", txt)
        self.assertEqual(self.remote("wip/s1"), "")                               # nothing reached origin
        self.assertEqual(self.status()["rc"], 1)
        Path(self.d, "config.py").write_text("API = os.environ['API']\n")
        rc, txt = self.save()
        self.assertEqual(rc, 0, txt)                                              # taken out: the next save pushes
        self.assertTrue(self.remote("wip/s1"))

    def test_a_token_in_a_commit_not_pushed_is_refused_too(self):
        self.put("k.txt", "aws AKIA" + "ABCDEFGHIJKLMNOP\n")                    # committed here, never pushed
        self.put("a.txt", "two\n", commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 1)
        self.assertIn("k.txt:1 (an AWS access key)", txt)

    def test_a_line_that_says_it_is_no_secret_is_pushed(self):
        self.put("t.py", "FAKE = %r  # kit: not a secret\n" % TOKEN, commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 0, txt)

    def test_what_origin_has_already_is_not_refused_again(self):
        self.put("old.txt", "x = %r  # kit: not a secret\n" % TOKEN)
        self.g("push", "-q", "origin", "s1")
        Path(self.d, "old.txt").write_text("x = %r\n" % TOKEN)                  # the same token, already public; the marker dropped
        self.put("a.txt", "two\n", commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 1)                                                   # a line it adds: read
        self.g("checkout", "--", "old.txt")
        rc, txt = self.save()
        self.assertEqual(rc, 0, txt)

    # ---- a part: its save is CL44's checkpoint on its step's branch, pushed there
    def part(self):
        os.makedirs(os.path.join(self.d, ".kit", "run"), exist_ok=True)
        Path(self.d, ".kit", "run", "part-branch.json").write_text(json.dumps({"branch": "s1", "post": "s1"}))

    def test_a_parts_save_is_a_checkpoint_pushed_to_its_branch(self):
        self.part()
        self.put("a.txt", "two\n", commit=False)
        self.put("n.txt", "new\n", commit=False)
        self.put("id_rsa", "k\n", commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 0, txt)
        self.assertIn("on origin/s1", txt)
        self.assertEqual(self.remote("s1"), self.g("rev-parse", "HEAD").stdout.strip())
        self.assertTrue(self.g("log", "-1", "--format=%s", "HEAD").stdout.startswith("checkpoint("))
        self.assertIn("id_rsa", self.g("log", "-1", "--format=%B", "HEAD").stdout)       # left out, named
        self.assertFalse(self.has("id_rsa", "s1"))
        self.assertEqual(self.remote("wip/s1"), "")

    def test_a_parts_save_with_a_token_commits_and_pushes_nothing(self):
        self.part()
        before = self.g("rev-parse", "HEAD").stdout
        self.put("c.py", "K = %r\n" % TOKEN, commit=False)
        rc, txt = self.save()
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", txt)
        self.assertIn("c.py:1", txt)
        self.assertEqual(self.g("rev-parse", "HEAD").stdout, before)
        self.assertEqual(self.g("diff", "--cached", "--name-only").stdout, "")   # nothing left staged
        self.assertEqual(self.remote("wip/s1"), "")

    def test_a_non_part_on_the_same_branch_saves_to_wip(self):
        self.put("a.txt", "two\n", commit=False)
        rc, txt = self.save()
        self.assertIn("origin/wip/s1", txt)
        self.assertEqual(self.g("status", "--porcelain", "--untracked-files=no").stdout.strip(), "M a.txt")   # nothing committed

    # ---- the hook tells a refusal, once
    def fire(self, **k):
        return subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_wip.py")], capture_output=True, text=True, timeout=20,
                              input=json.dumps(dict({"hook_event_name": "PostToolUse", "tool_name": "Edit", "session_id": "s",
                                                     "cwd": self.d}, **k)),
                              env=dict(self.env, CLAUDE_PROJECT_DIR=self.d, KIT_WIP="0"))

    def test_the_hook_says_a_refusal_once(self):
        self.put("c.py", "K = %r\n" % TOKEN, commit=False)
        self.save()
        out = self.fire().stdout
        self.assertIn("NOT on GitHub", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("c.py:1", out)
        self.assertEqual(self.fire().stdout, "")

    # ---- a takeover after a lost session continues from the save
    def take(self, d):
        kit = load("kit_cl55_%d" % id(d), "scripts/kit.py")
        with mock.patch.object(kit, "ROOT", d):
            return "\n".join(kit.take_step_branch({"branch": "s1", "part": "S1"}))

    def test_a_takeover_after_a_lost_session_continues_from_its_save(self):
        self.put("a.txt", "two\n", commit=False)
        self.put("new.txt", "half done\n", commit=False)
        time.sleep(1.1)                                                           # the save is newer than the branch's commit
        self.assertEqual(self.save()[0], 0)
        shutil.rmtree(self.d)                                                     # the container is gone
        o = self.clone("other")
        self.g("fetch", "-q", "origin", d=o)
        self.g("checkout", "-q", "s1", d=o)
        txt = self.take(o)
        self.assertIn("Started from your step's last save `origin/wip/s1`", txt)
        self.assertEqual(Path(o, "a.txt").read_text(), "two\n")
        self.assertEqual(Path(o, "new.txt").read_text(), "half done\n")
        self.assertEqual(self.g("rev-parse", "HEAD", d=o).stdout, self.g("rev-parse", "origin/s1", d=o).stdout)   # not committed for it

    def test_a_takeover_says_the_branch_when_it_is_the_newest(self):
        self.put("a.txt", "two\n", commit=False)
        self.save()
        self.g("checkout", "--", "a.txt")
        time.sleep(1.1)
        self.put("a.txt", "three\n")
        self.g("push", "-q", "origin", "s1")
        o = self.clone("other")
        self.g("fetch", "-q", "origin", d=o)
        self.g("checkout", "-q", "s1", d=o)
        txt = self.take(o)
        self.assertIn("Started from your step's branch `s1`", txt)
        self.assertIn("the newest", txt)
        self.assertEqual(Path(o, "a.txt").read_text(), "three\n")

    def test_a_takeover_with_changes_here_is_told_not_overwritten(self):
        self.put("a.txt", "two\n", commit=False)
        time.sleep(1.1)
        self.save()
        o = self.clone("other")
        self.g("fetch", "-q", "origin", d=o)
        self.g("checkout", "-q", "s1", d=o)
        Path(o, "a.txt").write_text("mine\n")
        txt = self.take(o)
        self.assertIn("was not put in (uncommitted changes here)", txt)
        self.assertEqual(Path(o, "a.txt").read_text(), "mine\n")


if __name__ == "__main__":
    unittest.main()
