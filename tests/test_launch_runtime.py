"""CL26: every launcher uses the runtime it runs in, never a binary that is absent, and says so loudly when it cannot.

The bug (CL25, 2026-11-23): Harmonize's config names `kit.cli: agy`; a step completed from a Claude Code web session
ran `agy` to start its review, got `[Errno 2] No such file or directory: 'agy'`, and no review ran.

- review (`kit.py review`, started by `step complete`): web -> the prompt is written and the create_session call is
  handed to the orchestrator; CLI with `claude` on PATH -> `claude --bg`, whatever `kit.cli` says; agy only where agy
  runs; nothing runnable -> "NOT STARTED" and the prompt's path, exit 1.
- part (`kit.py launch`): with no `claude` on PATH and not on the web, a loud refusal, never an OSError.
- clerk / orchestrator (`handover.py`): on the web, the create_session call, never `kill CLAUDE_PID` or `claude --bg`.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_launch import POST, Base, git, make_repo  # noqa: E402
from test_handoff import ROOT, TOKEN  # noqa: E402

AGY_CONFIG = {"project": "Harmonize", "board_url": "http://127.0.0.1:8765",
              "kit": {"runtime": "agy", "cli": "agy", "review": {"auto": "all", "train_every": 0}}}
# CL71: every variable _platform() reads, or a web container (CLAUDE_CODE_REMOTE_SESSION_ID) turns "not on the web" into web
LAUNCH_ENV = ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_REMOTE_SESSION_ID", "KIT_PLATFORM", "CLAUDE_PROJECT_DIR", "KIT_CLI", "KIT_NO_LAUNCH", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_CODE_ENTRYPOINT", "AGY_PID")


def tool_dir(tmp, *names):
    """A PATH holding only git, python and the fake tools `names` (each logs its argv)."""
    d = tmp / ("bin-" + "-".join(names or ("none",)))
    d.mkdir()
    for real in ("git", "sh", "env"):
        src = shutil.which(real)
        if src:
            os.symlink(src, d / real)
    for n in names:
        f = d / n
        f.write_text("#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\n" % (tmp / (n + ".log")))
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(d)


class Runtime(Base):
    def setUp(self):
        super().setUp()
        for k in LAUNCH_ENV:
            self.env.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)

    def repo(self, config=AGY_CONFIG):
        repo = make_repo(self.tmp / "repo", config)
        (repo / ".kit" / "consultant.md").write_text("review <REVIEW_ID> of <POST_ID>\n")
        (repo / ".kit" / "claude-cli").mkdir()
        (repo / ".kit" / "claude-cli" / "consultant.md").write_text("review <REVIEW_ID> of <POST_ID>\n")
        os.environ["KIT_BOARD_URL"] = self.url
        self.local("posts/cl99-launch-me", dict(POST, status="complete"))
        return repo

    def review(self, path_dirs):
        k = self.kit_in(self.repo())
        os.environ["PATH"] = path_dirs
        return k.review("cl99-launch-me")

    def wait_log(self, name):
        import time
        p = self.tmp / (name + ".log")
        for _ in range(50):
            if p.exists() and p.read_text():
                return p.read_text()
            time.sleep(0.1)
        return ""

    # ---- review
    def test_review_on_the_web_hands_over_the_create_session_call_even_with_cli_agy(self):
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        k = self.kit_in(self.repo())
        repo, bare = str(self.tmp / "repo"), str(self.tmp / "o.git")
        subprocess.run(["git", "-C", repo, "remote", "remove", "origin"], capture_output=True)
        os.environ["PATH"] = tool_dir(self.tmp)                   # no agy, no claude on PATH
        rc, msg = k.review("cl99-launch-me")                      # nowhere to push its prompt: said, never a call that starts blind
        self.assertEqual(rc, 1, msg)
        self.assertIn("could not be pushed", msg)
        self.assertNotIn("create_session", msg)
        subprocess.run(["git", "init", "-q", "--bare", bare], capture_output=True)
        subprocess.run(["git", "-C", repo, "remote", "add", "origin", bare], capture_output=True)
        rc, msg = k.review("cl99-launch-me")
        self.assertEqual(rc, 0, msg)
        self.assertNotIn("Errno", msg)
        self.assertIn("create_session", msg)
        self.assertIn("NOT STARTED", msg)                         # the orchestrator must make the call: said loudly
        self.assertIn("docs/prompts/CR-cl99-launch-me.md", msg)
        self.assertEqual(subprocess.run(["git", "-C", bare, "cat-file", "-e", "review-cl99-launch-me:docs/prompts/CR-cl99-launch-me.md"]).returncode, 0)

    def test_review_in_the_claude_cli_runs_claude_bg_when_agy_is_absent(self):
        rc, msg = self.review(tool_dir(self.tmp, "claude"))
        self.assertEqual(rc, 0, msg)
        self.assertIn("--bg", self.wait_log("claude").splitlines())
        self.assertFalse((self.tmp / "agy.log").exists())

    def test_review_runs_agy_where_agy_runs(self):
        os.environ["AGY_PID"] = "1"      # CL71: where agy runs, said; not left to the parent processes (claude in a web container)
        rc, msg = self.review(tool_dir(self.tmp, "agy", "claude"))
        self.assertEqual(rc, 0, msg)
        self.assertIn("-p", self.wait_log("agy").splitlines())

    def test_review_with_no_runtime_says_not_started(self):
        rc, msg = self.review(tool_dir(self.tmp))
        self.assertEqual(rc, 1, msg)
        self.assertIn("NOT STARTED", msg)
        self.assertNotIn("Errno", msg)
        self.assertIn(".kit/run/CR-cl99-launch-me.md", msg)

    # ---- part
    def test_part_launch_with_no_claude_and_not_on_the_web_refuses_loudly(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "agy"}})
        self.local("posts/cl99-launch-me", POST)
        k = self.kit_in(repo)
        os.environ["PATH"] = tool_dir(self.tmp)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=None, gate=False)
        self.assertFalse(r["ok"], r)
        self.assertIn("NOT STARTED", r["error"])
        self.assertNotIn("Errno", r["error"])
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["status"], "planned")

    def test_part_launch_on_the_web_is_found_without_the_web_flag(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "agy"}})
        self.local("posts/cl99-launch-me", POST)
        k = self.kit_in(repo)
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        os.environ["PATH"] = tool_dir(self.tmp)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, gate=False)
        self.assertTrue(r["ok"], r)
        self.assertIn("create_session", r)


class HandoverOnTheWeb(unittest.TestCase):
    """handover.py (orchestrator and clerk successors): on the web it never kills the session's own process."""

    def run_handover(self, role, extra_env, names=(), dry=True):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / ".kit").mkdir()
            shutil.copy(ROOT / "kit" / "handover.py", tmp / ".kit" / "handover.py")
            (tmp / ".kit" / "config.json").write_text(json.dumps(AGY_CONFIG))
            env = {k: v for k, v in os.environ.items() if k not in LAUNCH_ENV}
            env.update(extra_env, PATH=tool_dir(tmp, *names))
            r = subprocess.run([sys.executable, "-I", str(tmp / ".kit" / "handover.py"), "--role", role] + (["--dry-run"] if dry else []),
                               capture_output=True, text=True, env=env, timeout=30)
            return r.returncode, r.stdout + r.stderr

    def test_web_orchestrator_handover_names_create_session_not_a_kill(self):
        rc, out = self.run_handover("orchestrator", {"CLAUDE_CODE_REMOTE": "true", "CLAUDE_PID": "4242"})
        self.assertEqual(rc, 0, out)
        self.assertIn("create_session", out)
        self.assertNotIn("4242", out)

    def test_web_clerk_handover_names_create_session(self):
        rc, out = self.run_handover("clerk", {"CLAUDE_CODE_REMOTE": "true", "CLAUDE_PID": "4242"})
        self.assertEqual(rc, 0, out)
        self.assertIn("create_session", out)

    def test_cli_background_handover_without_claude_says_not_started(self):
        rc, out = self.run_handover("clerk", {"KIT_CLI": "claude"}, dry=False)   # no CLAUDE_PID: nothing to kill
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT STARTED", out)


if __name__ == "__main__":
    unittest.main()
