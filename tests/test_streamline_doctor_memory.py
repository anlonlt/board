"""Tests for the streamlined kit additions:
1. 3-layer Episodic -> Semantic memory ledger (kit/ledger.py + kit.py ledger + resume_role).
2. Just-in-time file-scoped memory hook (kit/hooks/kit_memory_jit.py).
3. Unified lifecycle hook dispatcher (kit/hook.py + install.py --unified-hooks).
4. Extended kit.py doctor [--fix] (hook parity, legacy conflicts, stale portfolio paths, unsent batches).

Run via: python3 -m unittest tests.test_streamline_doctor_memory -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "kit"))
import kit  # noqa: E402
import ledger  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook_disp = _load("kit_hook_disp", "kit/hook.py")


class MemoryLedgerFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mdir = self.root / ".board" / "memory"
        self.mdir.mkdir(parents=True)

    def test_observe_distil_propose_confirm_subsumes_from_resume(self):
        r1 = ledger.observe(str(self.mdir), "No extra confirmation modal on quick actions",
                            quote="why is there a modal here", source="c1", scope="all", theme="ui")
        r2 = ledger.observe(str(self.mdir), "Prefer swipe gestures over toolbar buttons",
                            quote="gesture before a button", source="c2", scope="all", theme="ui")
        r3 = ledger.observe(str(self.mdir), "Keep header chrome minimal on phone widths",
                            quote="too much chrome on mobile", source="c3", scope="all", theme="ui")
        self.assertTrue(r1["ok"] and r2["ok"] and r3["ok"])
        self.assertEqual(r1["entry"]["status"], "active")

        # Cluster ready to distil (>= 3 observations on theme 'ui')
        clusters = ledger.distil_candidates(str(self.mdir), min_cluster=3)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["theme"], "ui")
        self.assertEqual(clusters[0]["ids"], ["O1", "O2", "O3"])

        # Propose 1 compact Principle citing all 3 quotes
        pr = ledger.propose_principle(str(self.mdir), "Zero chrome: gestures before buttons, minimal phone UI",
                                      ["O1", "O2", "O3"], scope="all", theme="ui")
        self.assertTrue(pr["ok"])
        self.assertEqual(pr["principle"]["status"], "proposed")
        self.assertIn("gesture before a button", pr["principle"]["quote"])
        self.assertEqual(pr["principle"]["source"], "c1, c2, c3")

        # Before confirmation: orchestrator sees proposed principle; observations still active
        before = ledger.for_resume(str(self.mdir), "orchestrator")
        self.assertEqual(len(before["proposed_principles"]), 1)
        self.assertEqual(len(before["active"]), 3)

        # Owner confirms (with refined wording): principle becomes active, O1-O3 become subsumed
        cf = ledger.confirm_principle(str(self.mdir), "P1", summary="Zero chrome: gestures over buttons on every screen")
        self.assertTrue(cf["ok"])
        self.assertEqual(cf["subsumed"], ["O1", "O2", "O3"])

        after = ledger.for_resume(str(self.mdir), "part")
        self.assertEqual([p["id"] for p in after["principles"]], ["P1"])
        self.assertEqual(after["active"], [])  # O1-O3 retired from startup prompt!

        # Traceable evidence remains in git ledger file
        all_e = ledger.load_entries(str(self.mdir))
        sub = [e for e in all_e if e["status"] == "subsumed"]
        self.assertEqual(len(sub), 3)
        self.assertTrue(all(e["subsumed_by"] == "P1" for e in sub))

    def test_hook_enforced_invariants_not_dumped_in_resume_and_decisions_shown(self):
        ledger.observe(str(self.mdir), "Never force-push main", kind="rule", scope="all",
                       enforced_by="hook:kit_guard.py")
        ledger.observe(str(self.mdir), "Train VR2 on karaoke filter v3", kind="decision", scope="project",
                       because="kept 14/16 on owner round", rejected="skipping training")
        res = ledger.for_resume(str(self.mdir), "part")
        self.assertEqual(res["hook_enforced_count"], 1)
        self.assertEqual(res["active"], [])
        self.assertEqual(len(res["decisions"]), 1)
        rendered = "\n".join(ledger.format_resume(str(self.mdir), "part"))
        self.assertNotIn("Never force-push main", rendered)
        self.assertIn("+ 1 hook/test-enforced invariants active silently", rendered)
        self.assertIn("rejected: skipping training", rendered)

    def test_conflict_detection_and_ledger_check(self):
        ledger.observe(str(self.mdir), "Keep board post body concise and structured", scope="project", theme="board")
        r2 = ledger.observe(str(self.mdir), "Expand board post body with full raw logs", scope="project", theme="board")
        self.assertTrue(r2["conflicts"])
        self.assertEqual(r2["conflicts"][0]["id"], "O1")

        # Missing hook turns check red
        (self.root / ".claude" / "hooks" / "kit").mkdir(parents=True)
        ledger.observe(str(self.mdir), "Guard missing", kind="rule", enforced_by="hook:nonexistent_guard.py")
        chk = ledger.check_ledger(str(self.mdir), root=str(self.root))
        self.assertFalse(chk["ok"])
        self.assertTrue(any("nonexistent_guard.py" in e for e in chk["errors"]))

        # Verbatim quote mismatch turns check red
        ledger.observe(str(self.mdir), "Checked quote", quote="exact words here", source="cmt-99")
        chk2 = ledger.check_ledger(str(self.mdir), comments={"cmt-99": {"body": "something totally different"}})
        self.assertFalse(chk2["ok"])
        self.assertTrue(any("cmt-99" in e for e in chk2["errors"]))

    def test_init_imports_legacy_principles_md(self):
        (self.mdir / "principles.md").write_text("- Keep replies short (scope: all)\n- Clerk only routes (scope: clerk)\n")
        res = ledger.init_ledger(str(self.mdir))
        self.assertTrue(res["ok"])
        self.assertEqual(res["imported"], 2)
        entries = ledger.load_entries(str(self.mdir))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["kind"], "principle")


class MemoryJitHookAndDispatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".kit").mkdir()
        (self.root / ".kit" / "config.json").write_text(json.dumps({"project": "Demo", "kit": {}}))
        (self.root / ".kit" / "ledger.py").write_text((ROOT / "kit" / "ledger.py").read_text())
        self.mdir = self.root / ".board" / "memory"
        self.mdir.mkdir(parents=True)
        ledger.observe(str(self.mdir), "Test phone width 411px on every HTML change",
                       quote="phone first", scope="files:kit/board/*.html")

    def _run_jit(self, payload):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_memory_jit.py")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_jit_injects_once_per_session_on_matching_file_claude_and_agy(self):
        p_claude = {
            "cwd": str(self.root),
            "session_id": "sess-jit-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.root / "kit" / "board" / "board.template.html")},
        }
        out1 = self._run_jit(p_claude)
        self.assertIsNotNone(out1)
        self.assertIn("Test phone width 411px", out1["hookSpecificOutput"]["additionalContext"])
        # Second edit in same session: deduplicated (silent)
        self.assertIsNone(self._run_jit(p_claude))

        # Non-matching file: silent
        p_other = dict(p_claude, session_id="sess-jit-2", tool_input={"file_path": str(self.root / "README.md")})
        self.assertIsNone(self._run_jit(p_other))

        # agy payload
        p_agy = {
            "cwd": str(self.root),
            "conversationId": "conv-jit-1",
            "hookEventName": "PreToolUse",
            "toolCall": {"name": "edit_file", "args": {"Path": str(self.root / "kit" / "board" / "board.template.html")}},
        }
        out_agy = self._run_jit(p_agy)
        self.assertIsNotNone(out_agy)
        self.assertEqual(out_agy["decision"], "allow")
        self.assertIn("Test phone width 411px", out_agy["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_unified_hook_dispatcher_merges_and_enforces(self):
        # Copy hooks into .kit/hooks so dispatcher finds them
        hdir = self.root / ".kit" / "hooks"
        hdir.mkdir(parents=True)
        for f in ("kit_config.py", "kit_guard.py", "kit_memory_jit.py"):
            (hdir / f).write_text((ROOT / "kit" / "hooks" / f).read_text())

        # Destructive Bash command denied via unified dispatcher (kit_guard exits 2 + stderr on Claude Code)
        bad_bash = {
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf src"},
        }
        out, rc, err = hook_disp.dispatch("PreToolUse", bad_bash, root=str(self.root))
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", err)

        # Edit matching files: glob gets JIT context via unified dispatcher
        edit_ev = {
            "cwd": str(self.root),
            "session_id": "sess-disp-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.root / "kit" / "board" / "board.template.html")},
        }
        out2, rc2, _ = hook_disp.dispatch("PreToolUse", edit_ev, root=str(self.root))
        self.assertEqual(rc2, 0)
        self.assertIsNotNone(out2)
        self.assertIn("Test phone width 411px", out2["hookSpecificOutput"]["additionalContext"])


class ExtendedDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.proj = self.base / "proj"
        (self.proj / ".kit").mkdir(parents=True)
        (self.proj / ".kit" / "kit.py").write_text("# kit\n")
        (self.proj / ".claude" / "hooks" / "kit").mkdir(parents=True)
        (self.proj / ".agents" / "hooks" / "kit").mkdir(parents=True)

    def test_doctor_detects_drift_conflicts_stale_portfolio_and_unsent_batches_then_fixes(self):
        # Plant hook drift
        (self.proj / ".claude" / "hooks" / "kit" / "kit_guard.py").write_text("# v2\n")
        (self.proj / ".agents" / "hooks" / "kit" / "kit_guard.py").write_text("# v1\n")
        # Plant unwired guard + overlapping legacy stop.sh + missing add_repo
        (self.proj / ".claude" / "hooks" / "kit" / "kit_orchestrator_guard.py").write_text("# g\n")
        (self.proj / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "python3 .claude/hooks/kit/kit_stop.py"}]},
                    {"hooks": [{"type": "command", "command": "bash .claude/hooks/stop.sh"}]},
                ]
            }
        }))
        # Plant legacy scripts/kit.py shadow
        (self.proj / "scripts").mkdir()
        (self.proj / "scripts" / "kit.py").write_text("# old\n")
        # Plant stale local portfolio path with a sibling clone available
        sib = self.base / "other"
        sib.mkdir()
        (self.proj / ".kit" / "config.json").write_text(json.dumps({
            "project": "Proj",
            "kit": {},
            "portfolio": [{"name": "other", "path": "/nonexistent/home/other", "github": "o/other"}],
        }))
        # Plant unsent Artifact batch
        art_out = self.proj / ".kit" / "run" / "artifact" / "out"
        art_out.mkdir(parents=True)
        (art_out / "batch-001.json").write_text("{}")

        out, rc = kit.doctor(str(self.base / "no-mirror"), root=str(self.proj))
        self.assertEqual(rc, 1)
        self.assertIn("hook drift", out)
        self.assertIn("unwired kit guard", out)
        self.assertIn("legacy scripts/kit.py", out)
        self.assertIn("overlapping legacy hooks", out)
        self.assertIn("hardcoded/stale portfolio path", out)
        self.assertIn("unsent Artifact batch", out)
        self.assertIn("missing from .claude/settings.json permissions.allow", out)

        # Now run doctor --fix: add_repo, hook drift, and sibling portfolio path are repaired
        out_fix, _ = kit.doctor(str(self.base / "no-mirror"), fix=True, root=str(self.proj))
        self.assertIn("add_repo: FIXED", out_fix)
        self.assertIn("hooks parity: FIXED", out_fix)
        self.assertIn("portfolio: FIXED", out_fix)
        self.assertEqual(
            (self.proj / ".claude" / "hooks" / "kit" / "kit_guard.py").read_bytes(),
            (self.proj / ".agents" / "hooks" / "kit" / "kit_guard.py").read_bytes(),
        )
        cfg = json.loads((self.proj / ".kit" / "config.json").read_text())
        self.assertEqual(cfg["portfolio"][0]["path"], str(sib))
        s = json.loads((self.proj / ".claude" / "settings.json").read_text())
        self.assertIn(kit.ADD_REPO_RULE, s["permissions"]["allow"])


class UnifiedHooksInstall(unittest.TestCase):
    def test_install_unified_hooks_wires_single_dispatcher(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "p"
            proj.mkdir()
            subprocess.run(["git", "init", "-q", str(proj)], check=True)
            env = dict(os.environ, KIT_NO_FUNNEL="1", KIT_BIN_DIR=str(Path(d) / "bin"),
                       XDG_CONFIG_HOME=str(Path(d) / "xdg"))
            r = subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(proj),
                                "--quick", "--project", "P", "--runtime", "claude", "--no-survey", "t",
                                "--no-cmd", "--unified-hooks"],
                               capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((proj / ".kit" / "hook.py").is_file())
            self.assertTrue((proj / ".kit" / "ledger.py").is_file())
            s = json.loads((proj / ".claude" / "settings.json").read_text())
            for ev in ("SessionStart", "PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionEnd", "PreCompact"):
                cmds = [h["command"] for g in s["hooks"].get(ev, []) for h in g.get("hooks", [])]
                self.assertTrue(any(".kit/hook.py" in c and ev in c for c in cmds), (ev, cmds))


if __name__ == "__main__":
    unittest.main()
