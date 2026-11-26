"""CL39: a project that manages several repos from Claude Code web sessions.

install.py --web-multi yes installs .kit/user_hooks.py and records in kit.web_env the environment to create (repos,
setup script line); user_hooks.py puts every project hook at user level, wrapped to run only where the session's
project has no settings of its own, with CLAUDE_PROJECT_DIR set to the repo (idempotent); the hand-over starts the
successor in that environment (environment_id) and never says add_repo; resume carries no add_repo line and names the
environment's state; `kit.py web-env` prints the create_environment call, posts the question, records the id.

Run via: python3 -m unittest tests.test_cl39_web_multi
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, KIT_NO_FUNNEL="1")
for k_ in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_REMOTE"):
    ENV.pop(k_, None)
OWN_HOOKS = 3        # Harmonize's own hooks next to the kit's (its 23 = the kit's 20 + 3)


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600, stdin=subprocess.DEVNULL)


def harmonize_like(parent):
    """A scratch project shaped like Harmonize: the kit (runtime claude) plus 3 hooks of its own, two repos on the web."""
    t = Path(parent) / "harmonize"
    t.mkdir()
    subprocess.run(["git", "init", "-q", str(t)], check=True)
    r = install(t, "--quick", "--project", "Harmonize", "--runtime", "claude", "--no-survey", "test",
                "--web-multi", "yes", "--web-repos", "anlonlt/harmonize,anlonlt/board")
    assert r.returncode == 0, r.stdout + r.stderr
    sp = t / ".claude" / "settings.json"
    s = json.loads(sp.read_text())
    s["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": "python3 -I \"$CLAUDE_PROJECT_DIR/own.py\""}]})
    s["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "echo own-start", "timeout": 10}]})
    s["hooks"].setdefault("PostToolUse", []).append({"matcher": "Edit", "hooks": [{"type": "command", "command": "true"}]})
    sp.write_text(json.dumps(s, indent=2))
    return t, r


def n_hooks(s):
    return sum(len(g.get("hooks") or []) for gs in (s.get("hooks") or {}).values() for g in gs)


class InstallMultiRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t, self.r = harmonize_like(self.tmp.name)
        self.user = Path(self.tmp.name) / "home" / ".claude" / "settings.json"
        self.user.parent.mkdir(parents=True)
        self.user.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "echo mine"}]}]}}))

    def tearDown(self):
        self.tmp.cleanup()

    def uh(self, *args):
        return subprocess.run([sys.executable, "-I", str(self.t / ".kit" / "user_hooks.py"), *args,
                               "--settings", str(self.user)], capture_output=True, text=True, env=ENV, timeout=60)

    def test_install_records_the_environment_and_the_script(self):
        self.assertTrue((self.t / ".kit" / "user_hooks.py").is_file())
        we = json.loads((self.t / ".kit" / "config.json").read_text())["kit"]["web_env"]
        self.assertEqual(we["repos"], ["anlonlt/harmonize", "anlonlt/board"])
        self.assertEqual(we["setup_script"],
                         "if [ -f harmonize/.kit/user_hooks.py ]; then python3 -I harmonize/.kit/user_hooks.py install; fi")
        self.assertIsNone(we["environment_id"])
        self.assertIn("web, several repos: on", self.r.stdout)
        # --update keeps it, and the answer is asked once: no --web-multi needed again
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((self.t / ".kit" / "config.json").read_text())["kit"]["web_env"], we)

    def test_no_answer_installs_nothing(self):
        t = Path(self.tmp.name) / "single"
        t.mkdir()
        subprocess.run(["git", "init", "-q", str(t)], check=True)
        r = install(t, "--quick", "--project", "Single", "--runtime", "claude", "--no-survey", "test", "--web-multi", "no")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((t / ".kit" / "user_hooks.py").exists())
        self.assertNotIn("web_env", json.loads((t / ".kit" / "config.json").read_text())["kit"])

    def test_user_hooks_33_idempotent_user_lines_kept(self):
        r = self.uh("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("33 hooks", r.stdout)
        first = self.user.read_bytes()
        s = json.loads(first)
        self.assertEqual(n_hooks(s), 33 + 1)
        self.assertEqual(s["model"], "opus")
        self.assertIn("echo mine", json.dumps(s))
        r = self.uh("install")
        self.assertIn("0 changed", r.stdout)
        self.assertEqual(self.user.read_bytes(), first)
        r = self.uh("remove")
        self.assertIn("33 lines", r.stdout)
        s = json.loads(self.user.read_text())
        self.assertEqual(n_hooks(s), 1)

    def _say(self, project_dir, chars=300):
        self.uh("install")
        s = json.loads(self.user.read_text())
        cmd = next(h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"] if "kit_say_it_once.py" in h["command"])
        inp = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
                          "tool_input": {"to": "x", "message": "y" * chars}, "cwd": str(project_dir)})
        return subprocess.run(["sh", "-c", cmd], input=inp, capture_output=True, text=True, cwd=str(project_dir),
                              env=dict(ENV, CLAUDE_PROJECT_DIR=str(project_dir)), timeout=60)

    def test_300_char_message_refused_from_the_parent_folder(self):
        r = self._say(self.t.parent)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("300 characters", r.stdout + r.stderr)

    def test_short_message_passes_from_the_parent_folder(self):
        r = self._say(self.t.parent, chars=40)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_skipped_inside_the_repo(self):
        r = self._say(self.t)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


class HandoverAndResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t, _ = harmonize_like(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def kit(self, *args, env=None):
        return subprocess.run([sys.executable, "-I", str(self.t / ".kit" / "kit.py"), *args], capture_output=True,
                              text=True, env=dict(ENV, **(env or {})), cwd=str(self.t), timeout=120)

    def handover(self):
        r = subprocess.run([sys.executable, "-I", str(self.t / ".kit" / "handover.py"), "--role", "orchestrator",
                            "--dry-run"], capture_output=True, text=True, cwd=str(self.t), timeout=60,
                           env=dict(ENV, CLAUDE_CODE_REMOTE="true"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["create_session"]

    def test_web_env_flow_then_handover_starts_there(self):
        self.assertNotIn("environment_id", self.handover())
        r = self.kit("web-env")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(call["repositories"], ["anlonlt/harmonize", "anlonlt/board"])
        self.assertIn("user_hooks.py install", call["setup_script"])
        self.assertEqual(call["scope"], "personal")
        self.assertEqual(self.kit("web-env", "--id", "not-an-id").returncode, 2)
        r = self.kit("web-env", "--id", "env_01TestAbc")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.handover()
        self.assertEqual(call["environment_id"], "env_01TestAbc")
        self.assertNotIn("add_repo", json.dumps(call))
        # the clerk's start of an orchestrator, rendered at --update, names it too
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        clerk = next(p for p in (self.t / ".kit" / "clerk.md", self.t / ".kit" / "claude-cli" / "clerk.md") if p.exists())
        self.assertIn('environment_id: "env_01TestAbc"', clerk.read_text())

    def test_resume_has_no_add_repo_line(self):
        r = self.kit("resume", "--role", "orchestrator", env={"KIT_STALE_OK": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("add_repo", r.stdout)
        self.assertIn("web-env ask", r.stdout)            # not asked yet: post the question
        self.kit("web-env", "--id", "env_01TestAbc")
        r = self.kit("resume", "--role", "orchestrator", env={"KIT_STALE_OK": "1"})
        self.assertNotIn("add_repo read", r.stdout)
        self.assertIn("env_01TestAbc", r.stdout)


class StateFromTheBoard(unittest.TestCase):
    """The owner's Like on the question post is the go (never chat)."""

    def test_states(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import kit
        cfg = {"project": "H", "kit": {"web_env": {"on": True, "repos": ["a/h", "a/b"], "setup_script": "x",
                                                   "environment_id": None}}}
        self.assertEqual(kit.web_env_state({}, {}, cfg)[0], "unasked")
        posts = {kit.WEB_ENV_POST: {"title": "q"}}
        self.assertEqual(kit.web_env_state(posts, {}, cfg)[0], "asked")
        other = {"c1": {"like": True, "post_id": "other", "from": "owner"}}
        self.assertEqual(kit.web_env_state(posts, other, cfg)[0], "asked")
        liked = {"c1": {"like": True, "post_id": kit.WEB_ENV_POST, "from": "owner"}}
        st, txt = kit.web_env_state(posts, liked, cfg)
        self.assertEqual(st, "liked")
        self.assertIn("create_environment", txt)
        cfg["kit"]["web_env"]["environment_id"] = "env_1"
        self.assertEqual(kit.web_env_state(posts, liked, cfg)[0], "ready")
        self.assertEqual(kit.web_env_state(posts, liked, {"kit": {}})[0], "off")


if __name__ == "__main__":
    unittest.main()
