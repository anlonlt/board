"""`kit.py init` makes the git copy of the board on a project that has none, whatever the git (before 2.42, `worktree add`
has no --orphan: Debian 12), and says so with its exit code when it cannot.

Run via: python3 -m unittest tests.test_mirror_init -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = shutil.which("git")
# a git that has no `worktree add --orphan`, as before 2.42
OLD = "#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --orphan) echo \"error: unknown option \\`orphan'\" >&2; exit 129;; esac; done\nexec %s \"$@\"\n" % REAL


class MirrorInit(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="kit-mirror-"))
        self.addCleanup(shutil.rmtree, str(self.t), ignore_errors=True)
        self.p = self.t / "proj"
        (self.p / ".kit").mkdir(parents=True)
        (self.p / ".kit" / "config.json").write_text('{"project": "D"}')
        shutil.copy(str(ROOT / "scripts" / "kit.py"), str(self.p / ".kit" / "kit.py"))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY", "GIT_"))}
        self.git("init", "-q", "--bare", str(self.t / "o.git"), at=self.t)
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", str(self.t / "o.git"))

    def git(self, *a, at=None):
        return subprocess.run([REAL, "-C", str(at or self.p)] + list(a), capture_output=True, text=True, check=True, env=self.env).stdout.strip()

    def commit(self):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1")

    def init(self, old=False):
        env = self.env
        if old:
            (self.t / "bin").mkdir(exist_ok=True)
            (self.t / "bin" / "git").write_text(OLD)
            (self.t / "bin" / "git").chmod(0o755)
            env = dict(env, PATH="%s:%s" % (self.t / "bin", env["PATH"]))
        return subprocess.run([sys.executable, "-I", str(self.p / ".kit" / "kit.py"), "init"], capture_output=True, text=True, env=env, cwd=str(self.p))

    def mirror(self):
        return next(Path(x[9:]) for x in self.git("worktree", "list", "--porcelain").splitlines() if x.startswith("worktree ") and x.endswith("board-mirror"))

    def check(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = self.mirror()
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", at=m), "board-mirror")
        self.assertEqual(sorted(x.name for x in m.iterdir()), [".git"])           # nothing of the project in it
        (m / "x").write_text("1")
        self.git("add", "-A", at=m)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "m", at=m)
        self.assertEqual(self.git("ls-tree", "--name-only", "-r", "HEAD", at=m), "x")
        self.assertEqual(self.git("rev-list", "--count", "HEAD", at=m), "1")       # no history of the project's
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_this_git(self):
        self.commit()
        self.check(self.init())

    def test_a_git_without_orphan(self):
        self.commit()
        self.check(self.init(old=True))

    def test_a_branch_made_here_and_never_pushed(self):
        self.commit()
        self.check(self.init(old=True))
        m = self.mirror()
        self.git("worktree", "remove", "--force", str(m))
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((self.mirror() / "x").is_file())

    def test_a_failure_is_one(self):
        r = self.init(old=True)                                                   # no commit at all and an old git
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", r.stdout)


if __name__ == "__main__":
    unittest.main()
