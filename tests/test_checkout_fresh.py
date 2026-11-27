"""CL53: a session always starts on the newest code from GitHub, in every checkout.

~/harmonize sat on branch agy-board-install from 11-22 while Harmonize's main on GitHub moved on (11-24): a session there
worked on two-day-old code with nothing saying so. At SessionStart and at the first resume of any role the kit fetches
origin and says first when the checkout is behind its trunk, or on a merged or stale branch; it moves it forward by
itself only when that loses nothing (clean, and the trunk or a branch merged into it). A web clone is fresh: nothing.

Run via: python3 -m unittest tests.test_checkout_fresh -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

WEB_VARS = ("CLAUDE_CODE_REMOTE", "KIT_REMOTE", "AGY_REMOTE", "CLAUDE_CODE_SESSION_ID", "KIT_SESSION_ID")


def git(d, *a, env=None):
    r = subprocess.run(["git", "-C", str(d)] + list(a), capture_output=True, text=True, env=env)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


def commit(d, name, text, days_ago=0):
    Path(d, name).write_text(text)
    git(d, "add", name)
    env = dict(os.environ)
    if days_ago:
        stamp = "@%d +0000" % int(time.time() - days_ago * 86400)
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    git(d, "commit", "-qm", name, env=env)


class CheckoutFresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmp, "origin.git")
        self.up = os.path.join(self.tmp, "upstream")     # where GitHub's main moves on
        self.d = os.path.join(self.tmp, "project")       # the computer's checkout, left behind
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.tmp, "clone", "-q", self.origin, self.up)
        for c in (self.up,):
            git(c, "config", "user.email", "t@t"); git(c, "config", "user.name", "t")
        git(self.up, "checkout", "-qb", "main")
        commit(self.up, "a.txt", "base\n", days_ago=3)
        git(self.up, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", self.origin, self.d)
        git(self.d, "config", "user.email", "t@t"); git(self.d, "config", "user.name", "t")
        self.env = mock.patch.dict(os.environ, {v: "" for v in WEB_VARS}); self.env.start()
        self.root = mock.patch.object(kit, "ROOT", self.d); self.root.start()

    def tearDown(self):
        self.root.stop(); self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def main_moves(self, n=2):
        for i in range(n):
            self.n = getattr(self, "n", 0) + 1
            commit(self.up, "new%d.txt" % self.n, "newer %d\n" % self.n)
        git(self.up, "push", "-q", "origin", "main")
        return git(self.up, "rev-parse", "HEAD")

    def head(self):
        return git(self.d, "rev-parse", "HEAD")

    def test_a_clone_behind_origin_is_moved_forward_at_start(self):
        newest = self.main_moves()
        lines, blocked = kit.checkout_fresh_once(session="s1")
        self.assertEqual(self.head(), newest)
        self.assertFalse(blocked)
        self.assertEqual(len(lines), 1)
        self.assertIn("moved main forward: 2 newer commits on origin/main", lines[0])
        self.assertEqual(kit.checkout_fresh_once(session="s1"), ([], False))   # once in, nothing more to say

    def test_uncommitted_work_is_only_warned(self):
        old = self.head()
        self.main_moves()
        Path(self.d, "a.txt").write_text("my unsaved edit\n")
        lines, blocked = kit.checkout_fresh_once(session="s2")
        self.assertEqual(self.head(), old)
        self.assertEqual(Path(self.d, "a.txt").read_text(), "my unsaved edit\n")
        self.assertTrue(blocked)
        self.assertIn("Not moved: it has uncommitted changes", lines[0])
        again, blocked = kit.checkout_fresh_once(session="s2")             # repeated while it still asks something
        self.assertTrue(blocked); self.assertIn("uncommitted", again[0])

    def test_a_branch_with_no_commit_of_its_own_is_told_never_switched(self):
        """CR-cl53: a merged branch (the harmonize case) and a part's new branch look the same; neither is switched."""
        git(self.d, "checkout", "-qb", "agy-board-install")
        old = self.head()
        self.main_moves()
        lines, blocked = kit.checkout_fresh_once(session="s3")
        self.assertEqual(git(self.d, "symbolic-ref", "--short", "HEAD"), "agy-board-install")
        self.assertEqual(self.head(), old); self.assertFalse(blocked)
        self.assertIn("branch agy-board-install is behind: 2 newer commits on origin/main", lines[0])
        self.assertIn("it has no commit of its own: `git checkout main", lines[0])

    def test_a_detached_head_is_told_never_switched(self):
        """A review's worktree: a detached HEAD on an older trunk commit."""
        git(self.d, "checkout", "-q", "--detach", "main")
        old = self.head()
        self.main_moves(1)
        lines, _ = kit.checkout_fresh_once(session="s3b")
        self.assertEqual(self.head(), old)
        self.assertEqual(subprocess.run(["git", "-C", self.d, "symbolic-ref", "-q", "HEAD"]).returncode, 1)
        self.assertIn("a detached HEAD is behind", lines[0])

    def test_a_stale_branch_with_its_own_commits_is_warned_not_moved(self):
        git(self.d, "checkout", "-qb", "old-work")
        commit(self.d, "mine.txt", "mine\n", days_ago=3)
        mine = self.head()
        self.main_moves()
        lines, blocked = kit.checkout_fresh_once(session="s4")
        self.assertEqual(self.head(), mine)
        self.assertTrue(blocked)
        self.assertIn("branch old-work is stale: no commit for 3 days while main moved", lines[0])

    def test_a_fresh_branch_with_its_own_commits_is_told_it_is_behind(self):
        git(self.d, "checkout", "-qb", "today")
        commit(self.d, "mine.txt", "mine\n")
        mine = self.head()
        self.main_moves(1)
        lines, blocked = kit.checkout_fresh_once(session="s5")
        self.assertEqual(self.head(), mine); self.assertFalse(blocked)
        self.assertIn("behind its trunk: 1 newer commit on origin/main", lines[0])

    def test_up_to_date_says_nothing(self):
        self.assertEqual(kit.checkout_fresh_once(session="s6"), ([], False))

    def test_a_web_clone_is_left_alone(self):
        old = self.head()
        self.main_moves()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}):
            self.assertEqual(kit.checkout_fresh_once(session="s7"), ([], False))
        self.assertEqual(self.head(), old)

    def test_install_adds_fresh_where_session_start_is_the_project_s_own(self):
        """CR-cl53: Harmonize's SessionStart is its own session-start.sh; install gives it a `kit.py fresh` line, once,
        and none where the kit's SessionStart hook already runs."""
        sys.path.insert(0, str(ROOT))
        import install
        st = Path(self.d, ".claude", "settings.json"); st.parent.mkdir()
        own = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "bash .claude/session-start.sh"}]}]}}
        st.write_text(json.dumps(own))
        log = []
        install.merge_fresh_line(self.d, False, log); install.merge_fresh_line(self.d, False, log)
        cmds = [h["command"] for g in json.loads(st.read_text())["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(cmds, ["bash .claude/session-start.sh", install.FRESH_CMD]); self.assertEqual(len(log), 1)
        kit_line = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": install.DISPATCH["claude"] % ("cli", "SessionStart")}]}]}}
        st.write_text(json.dumps(kit_line))
        install.merge_fresh_line(self.d, False, log)
        self.assertEqual(json.loads(st.read_text()), kit_line)

    def test_session_start_and_resume_say_it_first(self):
        """The installed kit end to end: the SessionStart hook moves the checkout and its first line says so; a
        checkout with uncommitted work gets the warning as resume's first line."""
        os.makedirs(os.path.join(self.d, ".kit", "hooks"))
        shutil.copy(str(ROOT / "scripts" / "kit.py"), os.path.join(self.d, ".kit", "kit.py"))
        for f in ("kit_session_start.py", "kit_config.py", "kit_pre_compact.py"):
            shutil.copy(str(ROOT / "kit" / "hooks" / f), os.path.join(self.d, ".kit", "hooks", f))
        Path(self.d, ".git", "info", "exclude").write_text(".kit/\n")
        newest = self.main_moves()
        env = {k: v for k, v in os.environ.items() if k not in WEB_VARS and k != "KIT_ROLE"}
        env["CLAUDE_PROJECT_DIR"] = self.d
        r = subprocess.run([sys.executable, "-I", os.path.join(self.d, ".kit", "hooks", "kit_session_start.py")],
                           input=json.dumps({"session_id": "hook-1", "cwd": self.d, "hook_event_name": "SessionStart"}),
                           capture_output=True, text=True, cwd=self.d, env=env, timeout=120)
        self.assertEqual(self.head(), newest, r.stdout + r.stderr)
        first = (r.stdout.strip().splitlines() or [""])[0]
        if first.startswith("{"):                                         # agy's payload shape
            first = json.loads(first)["injectSteps"][0]["systemMessage"]["systemMessage"].splitlines()[0]
        self.assertTrue(first.startswith("CHECKOUT: moved main forward"), r.stdout)
        self.main_moves(1)
        Path(self.d, "a.txt").write_text("unsaved\n")
        r = subprocess.run([sys.executable, "-I", os.path.join(self.d, ".kit", "kit.py"), "resume", "--role", "part",
                            "--session", "resume-1"], capture_output=True, text=True, cwd=self.d, env=env, timeout=120)
        self.assertTrue(r.stdout.startswith("CHECKOUT: branch main is behind: 1 newer commit"), r.stdout[:400])


if __name__ == "__main__":
    unittest.main()
