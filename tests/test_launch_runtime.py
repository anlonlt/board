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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_launch import POST, TRAINS, Base, git, make_repo  # noqa: E402
from test_handoff import ROOT, TOKEN  # noqa: E402

AGY_CONFIG = {"project": "Harmonize", "board_url": "http://127.0.0.1:8765",
              "kit": {"runtime": "agy", "cli": "agy", "review": {"auto": "all", "train_every": 0}}}
LAUNCH_ENV = ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_REMOTE_SESSION_ID", "KIT_PLATFORM", "KIT_CLI", "KIT_NO_LAUNCH",
              "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR",
              "AGY_PID", "ANTIGRAVITY_AGENT", "ANTIGRAVITY_CONVERSATION_ID")


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
        self.assertEqual(r["runner"], "claude-web")
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "claude-web")

    def test_takeover_updates_runner_from_claude_web_to_agy_and_prints_invoke_subagent(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "agy"}})
        # Seed a running claude-web step with an existing commit on its step branch
        git(repo, "checkout", "-q", "-b", "cl99")
        (repo / "wip.txt").write_text("from web\n")
        git(repo, "add", "wip.txt")
        git(repo, "commit", "-q", "-m", "web progress")
        prior_sha = git(repo, "rev-parse", "cl99").strip()
        git(repo, "checkout", "-q", "work")
        self.local("posts/cl99-launch-me", dict(
            POST, status="in progress", runner="claude-web", session_id="session_01WEB",
            branch="cl99", updated_at="2026-11-26T00:00:00Z",
        ))
        self.local("posts/_trains", dict(TRAINS, rows=TRAINS["rows"] + [{
            "part": "CL99", "parent": "P-cli", "also": [], "children": [], "post": "cl99-launch-me",
            "prompt": "docs/prompts/cl99-launch-me.md", "session": "session_01WEB", "state": "running",
            "runner": "claude-web", "store": [],
        }]))
        k = self.kit_in(repo)
        os.environ["ANTIGRAVITY_AGENT"] = "1"
        os.environ["PATH"] = tool_dir(self.tmp, "agy", "claude")
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, take=True, gate=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["runner"], "agy")
        self.assertEqual(r["branch"], "cl99")
        self.assertEqual(r["branch_status"], "adopted")
        self.assertIn("invoke_subagent", r)
        self.assertIn("invoke_subagent(", r["invoke_subagent"])
        self.assertIn("invoke_subagent(", k.launch_text(r))
        self.assertFalse((self.tmp / "claude.log").exists())       # never spawns claude --bg
        self.assertEqual(git(repo, "rev-parse", "cl99").strip(), prior_sha)  # existing work preserved
        p = self.local("posts/cl99-launch-me")["data"]
        self.assertEqual(p["runner"], "agy")
        self.assertEqual(p["status"], "in progress")
        rows = {row["part"]: row for row in self.local("posts/_trains")["data"]["rows"]}
        self.assertEqual(rows["CL99"]["runner"], "agy")

    def test_agy_launch_with_cli_flag_checks_device_cap_and_prepares_branch(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "claude"}})
        self.local("posts/cl99-launch-me", dict(POST, runner="claude-web"))
        self.local("posts/_trains", TRAINS)
        k = self.kit_in(repo)
        os.environ["CLAUDECODE"] = "1"                             # caller is CLI, but explicitly passes --cli agy
        os.environ["PATH"] = tool_dir(self.tmp, "agy", "claude")
        fake_dev_full = mock.Mock(survey=lambda: {}, gate=lambda s: (False, "RAM full"))
        with mock.patch.object(k, "_device", return_value=fake_dev_full):
            blocked = k.launch("cl99-launch-me", url=self.url, token=TOKEN, cli="agy", gate=True)
        self.assertFalse(blocked["ok"])
        self.assertIn("device cap", blocked["error"])
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "claude-web")
        fake_dev_ok = mock.Mock(survey=lambda: {}, gate=lambda s: (True, ""))
        with mock.patch.object(k, "_device", return_value=fake_dev_ok):
            r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, cli="agy", gate=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["runner"], "agy")
        self.assertEqual(r["branch_status"], "created")
        self.assertEqual(git(repo, "rev-parse", "cl99").strip(), git(repo, "rev-parse", "work").strip())
        self.assertIn("invoke_subagent(", k.launch_text(r))
        self.assertFalse((self.tmp / "claude.log").exists())
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "agy")

    def test_takeover_updates_runner_from_claude_web_to_claude_cli(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "claude"}})
        self.local("posts/cl99-launch-me", dict(
            POST, status="in progress", runner="claude-web", session_id="session_01WEB",
            updated_at="2026-11-26T00:00:00Z",
        ))
        self.local("posts/_trains", TRAINS)
        k = self.kit_in(repo)
        os.environ["CLAUDECODE"] = "1"
        os.environ["PATH"] = tool_dir(self.tmp, "claude")
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, cli="claude", take=True, gate=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["runner"], "claude-cli")
        self.assertIn("--bg", self.wait_log("claude").splitlines())
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "claude-cli")
        rows = {row["part"]: row for row in self.local("posts/_trains")["data"]["rows"]}
        self.assertEqual(rows["CL99"]["runner"], "claude-cli")

    def test_web_launch_keeps_runner_claude_web(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "agy"}})
        self.local("posts/cl99-launch-me", dict(POST, runner="agy"))
        self.local("posts/_trains", TRAINS)
        k = self.kit_in(repo)
        os.environ["PATH"] = tool_dir(self.tmp, "agy", "claude")
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=True, gate=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["runner"], "claude-web")
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "claude-web")
        r2 = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=True, session_id="session_01WEB2")
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "claude-web")
        if (self.tmp / "claude.log").exists():
            self.assertNotIn("--bg", (self.tmp / "claude.log").read_text().splitlines())

    def test_launch_cli_parses_cli_agy_flag_and_adopts_remote_branch(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "local", "cli": "claude"}})
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "checkout", "-q", "-b", "cl99")
        (repo / "remote-wip.txt").write_text("from remote web\n")
        git(repo, "add", "remote-wip.txt")
        git(repo, "commit", "-q", "-m", "remote web commit")
        remote_sha = git(repo, "rev-parse", "cl99").strip()
        git(repo, "push", "-q", "origin", "cl99")
        git(repo, "checkout", "-q", "work")
        git(repo, "branch", "-D", "cl99")                          # no local cl99 branch yet, only origin/cl99
        self.local("posts/cl99-launch-me", dict(
            POST, status="in progress", runner="claude-web", session_id="session_01WEB",
            branch="cl99", updated_at="2026-11-26T00:00:00Z",
        ))
        self.local("posts/_trains", TRAINS)
        k = self.kit_in(repo)
        os.environ["CLAUDECODE"] = "1"
        os.environ["PATH"] = tool_dir(self.tmp, "agy", "claude")
        with mock.patch.object(k, "_device", return_value=None):
            import io
            buf = io.StringIO()
            with mock.patch.object(sys, "stdout", buf):
                rc = k.main(["launch", "cl99-launch-me", "--cli", "agy", "--take", "--board-url", self.url, "--token", TOKEN])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("invoke_subagent(", out)
        self.assertIn("runner agy", out)
        self.assertFalse((self.tmp / "claude.log").exists())
        self.assertEqual(git(repo, "rev-parse", "cl99").strip(), remote_sha)
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["runner"], "agy")

    def test_launch_line_for_agy_points_at_kit_py_launch(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "agy", "cli": "agy"}})
        k = self.kit_in(repo)
        os.environ["ANTIGRAVITY_AGENT"] = "1"
        line = k._launch_line("A2", "docs/prompts/a2-test-step.md")
        self.assertEqual(line, "python3 -I .kit/kit.py launch a2-test-step")
        self.assertNotIn("invoke_subagent", line)
        self.assertNotIn("step prompt", line)
        # Explicit model flag and explicit cli="agy" both route through kit.py launch
        line_model = k._launch_line("A2", "docs/prompts/a2-test-step.md", model="pro", cli="agy")
        self.assertEqual(line_model, "python3 -I .kit/kit.py launch a2-test-step --model pro")
        # Configured kit.roles.part.model is preserved when non-inherit
        with mock.patch.object(k, "_kit_config", return_value={
            "project": "Board", "kit": {"runtime": "agy", "cli": "agy", "roles": {"part": {"model": "pro"}}},
        }):
            self.assertEqual(k._launch_line("A2", "docs/prompts/a2-test-step.md", cli="agy"),
                             "python3 -I .kit/kit.py launch a2-test-step --model pro")
        # Without a derivable post id, falls back to invoke_subagent(...)
        fallback = k._launch_line("A2", "no-post", cli="agy")
        self.assertIn("invoke_subagent(", fallback)
        self.assertIn('Workspace="share"', fallback)

    def test_agy_orchestrator_guide_and_launch_workflow_point_at_kit_py_launch(self):
        repo = self.repo({"project": "Board", "kit": {"runtime": "agy", "cli": "agy"}})
        k = self.kit_in(repo)
        os.environ["ANTIGRAVITY_AGENT"] = "1"
        wf = "\n".join(k._launch_workflow())
        self.assertIn("python3 -I .kit/kit.py launch <post>", wf)
        self.assertIn("prints the `invoke_subagent` call", wf)
        self.assertNotIn("`step new` prints the `invoke_subagent` call", wf)
        for rel in ("kit/orchestrator.template.md", ".kit/orchestrator.md"):
            p = ROOT / rel
            if not p.is_file():
                continue
            txt = p.read_text(encoding="utf-8")
            self.assertIn("python3 -I .kit/kit.py launch <post id>", txt, rel)
            self.assertNotIn("the `Prompt` that `kit.py step new` prints", txt, rel)
            self.assertNotIn("mark it `kit.py step start <post>`, then launch each part with `invoke_subagent`", txt, rel)


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
