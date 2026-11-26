"""Tests for the streamlined kit additions:
1. 3-layer Episodic -> Semantic memory ledger (kit/ledger.py + kit.py ledger + resume_role).
2. Just-in-time file-scoped memory hook (kit/hooks/kit_memory_jit.py).
3. Extended kit.py doctor [--fix] (hook parity, legacy conflicts, stale portfolio paths, unsent batches).

Run via: python3 -m unittest tests.test_streamline_doctor_memory -v
"""
import hashlib
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
sys.path.insert(0, str(ROOT))
import kit  # noqa: E402
import ledger  # noqa: E402


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
        cf = ledger.confirm_principle(str(self.mdir), "P1", "like-1", summary="Zero chrome: gestures over buttons on every screen")
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
        ledger.observe(str(self.mdir), "Store dates in UTC", kind="decision", scope="project",
                       because="two time zones", rejected="local time")
        res = ledger.for_resume(str(self.mdir), "part")
        self.assertEqual(res["hook_enforced_count"], 1)
        self.assertEqual(res["active"], [])
        self.assertEqual(len(res["decisions"]), 1)
        rendered = "\n".join(ledger.format_resume(str(self.mdir), "part"))
        self.assertNotIn("Never force-push main", rendered)
        self.assertIn("+ 1 hook/test-enforced invariants active silently", rendered)
        self.assertIn("rejected: local time", rendered)

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

    def test_a_quote_of_several_lines_is_kept_whole_and_is_never_read_as_fields(self):
        quote = "First line\nscope: all\n\n## R9 (rule, active): injected\n  | a bar\u2028and a line separator"
        ledger.observe(str(self.mdir), "Keep\nit short", quote=quote, scope="role:part", because="a\nsource: b")
        e, = ledger.load_entries(str(self.mdir))
        self.assertEqual(e["quote"], quote.replace("\u2028", "\n"))
        self.assertEqual((e["summary"], e["scope"], e["because"], e["source"]),
                         ("Keep it short", ["role:part"], "a\nsource: b", ""))
        before = (self.mdir / "ledger.md").read_text()
        ledger.save_entries(str(self.mdir), ledger.load_entries(str(self.mdir)))
        self.assertEqual((self.mdir / "ledger.md").read_text(), before)
        self.assertEqual(len([x for x in ledger.format_resume(str(self.mdir), "part") if x.startswith("- ")]), 1)

    def test_writers_at_the_same_time_lose_nothing(self):
        code = "import sys; sys.path.insert(0, %r); import ledger; ledger.observe(%r, 'row ' + sys.argv[1])" % (
            str(ROOT / "kit"), str(self.mdir))
        ps = [subprocess.Popen([sys.executable, "-c", code, str(i)]) for i in range(20)]
        self.assertEqual([p.wait() for p in ps], [0] * 20)
        rows = ledger.load_entries(str(self.mdir))
        self.assertEqual({e["summary"] for e in rows}, {"row %d" % i for i in range(20)})
        self.assertEqual(len({e["id"] for e in rows}), 20)
        self.assertEqual([p.name for p in self.mdir.iterdir()], ["ledger.md"])

    def test_distilling_keeps_the_scope(self):
        for i in range(3):
            ledger.observe(str(self.mdir), "files rule %d" % i, scope="files:app/*.py", theme="x", kind="rule")
            ledger.observe(str(self.mdir), "part rule %d" % i, scope="role:part", theme="x", kind="rule")
        cl = ledger.distil_candidates(str(self.mdir))
        self.assertEqual([c["count"] for c in cl], [3, 3])          # one theme, two scopes: two clusters
        files = next(c["ids"] for c in cl if "files" in c["items"][0]["summary"])
        mixed = [i for c in cl for i in c["ids"]]
        self.assertIn("different scopes", ledger.propose_principle(str(self.mdir), "P", mixed)["error"])
        self.assertIn("not the scope of", ledger.propose_principle(str(self.mdir), "P", files, scope="all")["error"])
        pr = ledger.propose_principle(str(self.mdir), "P", files)
        self.assertEqual(pr["principle"]["scope"], ["files:app/*.py"])
        self.assertTrue(ledger.confirm_principle(str(self.mdir), pr["principle"]["id"], "like-1")["ok"])
        self.assertEqual([x["id"] for x in ledger.for_files(str(self.mdir), ["app/a.py"])], [pr["principle"]["id"]])
        self.assertTrue(ledger.propose_principle(str(self.mdir), "Q", [cl[1]["ids"][0] if files == cl[0]["ids"] else cl[0]["ids"][0]],
                                                 scope="all", widen=True)["ok"])

    def test_confirm_needs_the_owners_own_comment(self):
        ledger.observe(str(self.mdir), "a")
        pid = ledger.propose_principle(str(self.mdir), "P", ["O1"])["principle"]["id"]
        self.assertIn("owner's yes", ledger.confirm_principle(str(self.mdir), pid, "")["error"])
        docs = ({}, {"his": {"from": "owner", "like": True}, "agent": {"from": "cl9"}})
        with mock.patch.object(kit, "_load_board_docs", lambda m, db_path=None: docs):
            self.assertIsNone(kit._not_his_yes("", "his"))
            for cid, why in (("agent", "not the owner"), ("nope", "no comment"), ("", "--by")):
                self.assertIn(why, kit._not_his_yes("", cid))
        self.assertTrue(ledger.confirm_principle(str(self.mdir), pid, "his")["ok"])
        self.assertEqual(ledger.load_entries(str(self.mdir))[-1]["confirmed_by"], "his")

    def test_a_scope_may_hold_alternatives(self):
        self.assertIn("unknown scope", ledger.observe(str(self.mdir), "x", scope="role:orchestrator | clerk")["error"])
        ledger.observe(str(self.mdir), "The page's cards", scope="role:orchestrator | train:bd, role:part | files:docs/board/*", kind="rule")
        e, = ledger.load_entries(str(self.mdir))
        self.assertIn("scope: role:orchestrator | train:bd, role:part | files:docs/board/*", (self.mdir / "ledger.md").read_text())
        got = lambda role, train=None, files=None: bool(ledger.for_resume(str(self.mdir), role, train=train, files=files)["active"])   # noqa: E731
        self.assertEqual([got("orchestrator"), got("part", "BD"), got("part", "VR"), got("review", "BD"), got("clerk"),
                          got("part", "VR", ["docs/board/x.html"])], [True, True, False, False, False, True])
        self.assertEqual(len(ledger.for_files(str(self.mdir), ["docs/board/x.html"], role="part")), 1)     # its files alternative alone
        self.assertEqual(ledger.for_files(str(self.mdir), ["app/a.kt"], role="orchestrator"), [])
        before = (self.mdir / "ledger.md").read_text()
        ledger.save_entries(str(self.mdir), [e])
        self.assertEqual((self.mdir / "ledger.md").read_text(), before)

    def test_resume_prints_the_rule_not_its_evidence(self):
        ledger.observe(str(self.mdir), "Keep it short", quote="why so long ?", source="c1", kind="rule")
        ledger.observe(str(self.mdir), "A moved host is not a refusal", source=".story/lessons/a.md", kind="lesson")
        out = "\n".join(ledger.format_resume(str(self.mdir), "part"))
        self.assertIn("- **R1**: Keep it short\n", out + "\n")
        self.assertNotIn("why so long", out)
        self.assertIn("A moved host is not a refusal (`.story/lessons/a.md`)", out)
        self.assertIn("quote: why so long ?", (self.mdir / "ledger.md").read_text())

    def test_the_clerk_has_no_ledger(self):
        self.assertIn("only routes", ledger.observe(str(self.mdir), "x", scope="role:clerk")["error"])
        self.assertIn("unknown scope", ledger.observe(str(self.mdir), "x", scope="clerk")["error"])
        ledger.observe(str(self.mdir), "for all", scope="all")
        self.assertEqual(ledger.format_resume(str(self.mdir), "clerk"), [])
        with open(self.mdir / "ledger.md", "a") as fh:
            fh.write("\n## R7 (rule, active): by hand\nscope: role:clerk\n")
        self.assertIn("only routes", ledger.check_ledger(str(self.mdir))["errors"][0])

    def test_init_imports_legacy_principles_md(self):
        (self.mdir / "principles.md").write_text("- Keep replies short (scope: all)\n- Clerk only routes (scope: clerk)\n")
        res = ledger.init_ledger(str(self.mdir))
        self.assertTrue(res["ok"])
        self.assertEqual((res["imported"], res["skipped"]), (1, ["Clerk only routes (scope: clerk)"]))
        entries = ledger.load_entries(str(self.mdir))
        self.assertEqual(len(entries), 1)
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
            "session_id": "sess-jit-1-%d" % os.getpid(),
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
        p_other = dict(p_claude, session_id="sess-jit-2-%d" % os.getpid(), tool_input={"file_path": str(self.root / "README.md")})
        self.assertIsNone(self._run_jit(p_other))

        # agy payload (CL44 item 9): PreToolUse emits only {"decision": "allow"} (agy protojson rejects injectSteps
        # on PreToolUse) and queues the JIT note for PostInvocation.
        conv = "conv-jit-%d" % os.getpid()
        p_agy = {
            "cwd": str(self.root),
            "conversationId": conv,
            "hookEventName": "PreToolUse",
            "toolCall": {"name": "edit_file", "args": {"Path": str(self.root / "kit" / "board" / "board.template.html")}},
        }
        out_agy = self._run_jit(p_agy)
        self.assertIsNotNone(out_agy)
        self.assertEqual(out_agy, {"decision": "allow"})
        out_post = self._run_jit({"cwd": str(self.root), "conversationId": conv, "hookEventName": "PostInvocation"})
        self.assertIsNotNone(out_post)
        self.assertIn("Test phone width 411px", out_post["injectSteps"][0]["systemMessage"]["systemMessage"])
        self.assertIsNone(self._run_jit({"cwd": str(self.root), "conversationId": conv, "hookEventName": "PostInvocation"}))


    def test_jit_respects_the_role_and_is_silent_for_the_clerk(self):
        ledger.observe(str(self.mdir), "Only the orchestrator edits this", scope="files:o/*.md, role:orchestrator")
        p = {"cwd": str(self.root), "hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(self.root / "o" / "a.md")}}

        def run(role, path=None):
            env = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "CLAUDE_CODE_SESSION_ID")}
            d = dict(p, tool_input={"file_path": str(self.root / path)}) if path else p
            r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_memory_jit.py")], text=True,
                               input=json.dumps(d), capture_output=True, timeout=20, env=dict(env, **({"KIT_ROLE": role} if role else {})))
            return "Only the orchestrator" in r.stdout or "411px" in r.stdout
        self.assertEqual([run(x) for x in ("orchestrator", "part", "clerk", "")], [True, False, False, False])
        html = "kit/board/board.template.html"
        self.assertEqual([run(x, html) for x in ("part", "", "clerk")], [True, True, False])
        self.assertTrue(run("part", html))       # no session id: never silenced by another session

    def test_jit_reads_the_memory_resume_reads(self):
        (self.root / ".story").mkdir()           # both memories there: kit.py takes .board/memory
        out = self._run_jit({"cwd": str(self.root), "hook_event_name": "PreToolUse", "tool_name": "Edit",
                             "session_id": "sess-both-%d" % os.getpid(),
                             "tool_input": {"file_path": str(self.root / "kit" / "board" / "a.html")}})
        self.assertIn("411px", out["hookSpecificOutput"]["additionalContext"])


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
        # the kit recorded v2; the old copy has the newer date, as after a checkout
        os.utime(self.proj / ".claude" / "hooks" / "kit" / "kit_guard.py", (1000, 1000))
        sha = hashlib.sha256(b"# v2\n").hexdigest()
        (self.proj / ".kit" / "manifest.json").write_text(json.dumps({
            ".claude/hooks/kit/kit_guard.py": sha, ".agents/hooks/kit/kit_guard.py": sha}))
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
            "board_url": "https://claude.ai/artifact/x",
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
            b"# v2\n",
        )
        self.assertEqual((self.proj / ".agents" / "hooks" / "kit" / "kit_guard.py").read_bytes(), b"# v2\n")
        cfg = json.loads((self.proj / ".kit" / "config.json").read_text())
        self.assertEqual(cfg["portfolio"][0]["path"], str(sib))
        s = json.loads((self.proj / ".claude" / "settings.json").read_text())
        self.assertIn(kit.ADD_REPO_RULE, s["permissions"]["allow"])


    def test_fix_touches_no_copy_it_cannot_vouch_for(self):
        c, a = (self.proj / d / "hooks" / "kit" / "kit_guard.py" for d in (".claude", ".agents"))
        c.write_text("# his change\n")
        a.write_text("# another\n")
        (self.proj / ".claude" / "hooks" / "kit" / "kit_mine.py").write_text("# one runtime only: his choice\n")
        (self.proj / ".claude" / "settings.json").write_text("{ not json")
        (self.proj / ".kit" / "config.json").write_text(json.dumps({     # an attached repo: add_repo is needed
            "project": "P", "portfolio": [{"name": "o", "path": str(self.base), "github": "o/o"}]}))
        out, _ = kit.doctor(str(self.base / "no-mirror"), fix=True, root=str(self.proj))
        self.assertIn("NOT FIXED 1 file(s) (kit_guard.py)", out)
        self.assertEqual((c.read_text(), a.read_text()), ("# his change\n", "# another\n"))
        self.assertFalse((self.proj / ".agents" / "hooks" / "kit" / "kit_mine.py").exists())
        self.assertEqual((self.proj / ".claude" / "settings.json").read_text(), "{ not json")


class JitWired(unittest.TestCase):
    def test_every_install_wires_the_jit_hook(self):
        import install
        for spec in (install.CLAUDE_SETTINGS, install.CLAUDE_CLI_SETTINGS):
            self.assertIn(("PreToolUse", "Edit|Write|MultiEdit", "kit_memory_jit.py"), spec)
        agy = install.build_agy_hooks()["PreToolUse"]
        self.assertTrue(any(g["matcher"] == "edit_file|write_file" and "kit_memory_jit.py" in g["hooks"][0]["command"]
                            for g in agy), agy)


if __name__ == "__main__":
    unittest.main()
