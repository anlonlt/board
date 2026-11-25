"""CL25: install.py --update refreshes every runtime installed in the target, whatever kit.runtime / kit.cli say.

Harmonize names runtime/cli agy, yet its live Claude Code hooks are in .claude/hooks/kit/: --update skipped them, so
they went stale (kit_pre_compact.py) or were copied by hand (CL24's kit_orchestrator_guard.py). A runtime is installed
when its hooks directory is there; its kit files are refreshed, the project's own changes still KEPT, and nothing is
added to it but a hook the kit added since (CL28: its file and its own settings.json lines); an absent runtime is never
installed (CL22).

Run via: python3 -m unittest tests.test_install_present_runtimes -v
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import tempfile as _tf; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-")

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, KIT_NO_FUNNEL="1")
HOOKS = ROOT / "kit" / "hooks"


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600)


def past_version(name):
    """An earlier committed version of kit/hooks/<name> that differs from the current one, or None."""
    r = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", "kit/hooks/" + name],
                       capture_output=True, text=True)
    cur = (HOOKS / name).read_bytes()
    for c in r.stdout.split()[1:]:
        s = subprocess.run(["git", "-C", str(ROOT), "show", "%s:kit/hooks/%s" % (c, name)], capture_output=True)
        if s.returncode == 0 and s.stdout and s.stdout != cur:
            return s.stdout
    return None


class PresentRuntimes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "proj"
        self.t.mkdir()
        subprocess.run(["git", "init", "-q", str(self.t)], check=True)
        r = install(self.t, "--quick", "--project", "Proj", "--runtime", "agy", "--cli", "agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.cl = self.t / ".claude" / "hooks" / "kit"
        self.assertFalse(self.cl.exists(), "runtime agy installs no .claude hooks")
        self.cl.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_claude_hooks_refreshed_under_cli_agy(self):
        old = past_version("kit_pre_compact.py")
        if old is None:
            self.skipTest("no git history of kit/hooks/kit_pre_compact.py here")
        stale = self.cl / "kit_pre_compact.py"            # a past kit version, never in the manifest (copied by hand)
        stale.write_bytes(old)
        recorded = self.cl / "kit_task_train.py"          # stale, but recorded in the manifest by an earlier install
        recorded.write_text("# an earlier kit's task-train hook\n")
        man_p = self.t / ".kit" / "manifest.json"
        man = json.loads(man_p.read_text())
        man[".claude/hooks/kit/kit_task_train.py"] = hashlib.sha256(recorded.read_bytes()).hexdigest()
        man_p.write_text(json.dumps(man))
        own = self.cl / "kit_config.py"                   # the project's own change: kept
        own.write_text((HOOKS / "kit_config.py").read_text() + "\n# the project's own rule\n")
        r = install(self.t, "--update")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(stale.read_bytes(), (HOOKS / "kit_pre_compact.py").read_bytes(), out)
        self.assertEqual(recorded.read_bytes(), (HOOKS / "kit_task_train.py").read_bytes(), out)
        self.assertIn("# the project's own rule", own.read_text())
        self.assertIn("KEPT (not the kit's, or changed since): .claude/hooks/kit/kit_config.py", out)
        # nothing added to the present runtime but the hook the kit added since (CL28), and no absent runtime installed
        # (CL30: kit_board_guard.py is such a hook too; and the kit's guards present there are wired, kit_task_train.py
        # here, while its other hooks, kit_pre_compact.py, stay as the project has them)
        self.assertEqual(sorted(p.name for p in self.cl.iterdir()),
                         ["kit_activity.py", "kit_board_guard.py", "kit_brief_guard.py", "kit_config.py", "kit_memory_jit.py",
                          "kit_pre_compact.py", "kit_say_it_once.py", "kit_task_train.py", "kit_usage.py", "kit_wip.py"])  # CL37's usage hook, CL38's message guard, the JIT memory
        st = json.loads((self.t / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for ev in st["hooks"].values() for g in ev for h in g["hooks"]]
        self.assertTrue(cmds and all(any(g in c for g in ("kit_brief_guard.py", "kit_board_guard.py", "kit_task_train.py",
                                                          "kit_usage.py", "kit_say_it_once.py", "kit_memory_jit.py", "kit_wip.py", "kit_activity.py"))
                                     for c in cmds), cmds)
        self.assertEqual(st["env"]["CLAUDE_CODE_ENABLE_TODO_TOOLS"], "1")
        cfg = json.loads((self.t / ".kit" / "config.json").read_text())
        self.assertEqual((cfg["kit"]["runtime"], cfg["kit"]["cli"]), ("agy", "agy"))
        man = json.loads(man_p.read_text())
        self.assertEqual(man[".claude/hooks/kit/kit_pre_compact.py"],
                         hashlib.sha256(stale.read_bytes()).hexdigest())

    def test_a_hook_new_since_the_install_reaches_the_present_claude_runtime(self):
        # CL28: Harmonize (cli agy, Claude Code hooks in .claude/hooks/kit/) got kit_brief_guard.py in .agents only
        before = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%h", "-1", "--diff-filter=A", "--",
                                 "kit/hooks/kit_brief_guard.py"], capture_output=True, text=True).stdout.strip()
        if not before:
            self.skipTest("no git history of kit/hooks/kit_brief_guard.py here")
        man_p = self.t / ".kit" / "manifest.json"
        man = json.loads(man_p.read_text())
        # Harmonize's case: last updated from a kit that already had the hook, which went to .agents/hooks/kit/ only
        man["_kit_commit"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                                            text=True).stdout.strip() + " 2026-11-23T08:00:00+00:00"
        man_p.write_text(json.dumps(man))
        (self.cl / "kit_config.py").write_text((HOOKS / "kit_config.py").read_text())
        st = self.t / ".claude" / "settings.json"
        mine = {"permissions": {"allow": ["Bash(git status)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        st.write_text(json.dumps(mine))
        r = install(self.t, "--update")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual((self.cl / "kit_brief_guard.py").read_bytes(), (HOOKS / "kit_brief_guard.py").read_bytes(), out)
        self.assertEqual(sorted(p.name for p in self.cl.iterdir()), ["kit_activity.py", "kit_board_guard.py", "kit_brief_guard.py",
                                                                      "kit_config.py", "kit_memory_jit.py", "kit_say_it_once.py",
                                                                      "kit_usage.py", "kit_wip.py"])  # CL30's, CL37's, CL38's, the JIT memory
        s = json.loads(st.read_text())
        # the project's own rules kept, first; CL38 adds the add_repo rule once
        self.assertEqual(s["permissions"], {"allow": ["Bash(git status)", "mcp__claude-code-remote__add_repo"]})
        self.assertEqual(s["hooks"]["Stop"][0], mine["hooks"]["Stop"][0])         # the project's own Stop kept, first
        cmds = [h["command"] for ev in s["hooks"].values() for g in ev for h in g["hooks"]]
        self.assertEqual(sum("kit_brief_guard.py" in c for c in cmds), 2, cmds)
        self.assertEqual(sum("kit_board_guard.py" in c for c in cmds), 2, cmds)   # CL30's new hook: Post Bash, Stop
        self.assertEqual(sum("kit_usage.py" in c for c in cmds), 2, cmds)         # CL37: Stop and SessionEnd
        self.assertEqual(sum("/hooks/kit/" in c for c in cmds), 12, cmds)  # + what a part does now (PostToolUse, Stop) + CL38's message guard, the JIT memory (PreToolUse)  # no other kit hook added
        # the target's Claude Code hooks are the web ones (an ArtifactData guard is there): the reply tool is matched too
        st.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "ArtifactData|mcp__claude-code-remote__create_session",
                      "hooks": [{"type": "command", "command": "python3 -I x/.claude/hooks/kit/kit_orchestrator_guard.py"}]}]}}))
        (self.cl / "kit_brief_guard.py").unlink()
        man = json.loads(man_p.read_text())
        man.pop(".claude/hooks/kit/kit_brief_guard.py", None)
        man["_kit_commit"] = before + "^ x"
        man_p.write_text(json.dumps(man))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = json.loads(st.read_text())
        pre = [g["matcher"] for g in s["hooks"]["PreToolUse"] if any("kit_brief_guard.py" in h["command"] for h in g["hooks"])]
        self.assertEqual(len(pre), 1)
        self.assertIn("ArtifactData", pre[0])

    def test_a_kit_hook_the_project_removed_is_not_put_back(self):
        man_p = self.t / ".kit" / "manifest.json"
        man = json.loads(man_p.read_text())
        man["_kit_commit"] = "911718f x"
        man[".claude/hooks/kit/kit_brief_guard.py"] = "0" * 64   # installed once, then removed by the project
        man_p.write_text(json.dumps(man))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.cl / "kit_brief_guard.py").exists())

    def test_no_claude_dir_none_installed(self):
        self.cl.rmdir()
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.cl.exists())


if __name__ == "__main__":
    unittest.main()
