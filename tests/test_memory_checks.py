"""M7: every memory entry is checked against reality where it can be.

- A lesson (`check:`) or a ledger row (`enforced_by:`) names what enforces it: test:, hook:, code:, refusal:.
  `kit.py lessons check` runs them and flags an entry whose test is gone or fails, or whose code no longer exists.
- An entry with nothing to run is "unchecked since <date>" where a session is given it (resume, the memory hook).
- A lesson whose remedy is "be careful" is refused; a new lesson without a check gets one proposed.
- The orchestrator's resume carries one line: entries checked, failing, unchecked.

Run via: python3 -m unittest tests.test_memory_checks -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

led = kit._load_ledger_mod()

PASSING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_holds(self):\n        self.assertTrue(True)\n"
FAILING = "import unittest\n\nclass T(unittest.TestCase):\n    def test_holds(self):\n        self.assertTrue(False, 'broken')\n"


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "kit").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src").mkdir()
        (self.root / "tests" / "test_rule.py").write_text(PASSING)
        (self.root / "src" / "cmd.py").write_text("def go():\n    raise SystemExit('REFUSED: write the post with kit.py form')\n")
        (self.root / "kit" / "lessons.md").write_text(
            "# lessons\n\n"
            "## K1: tested\nroles: part\nfrom: CL1 2026-11-01\ncheck: test:tests/test_rule.py::T.test_holds\nThe text.\n\n"
            "## K2: code\nroles: part\ncheck: code:src/cmd.py#def go\nThe code does it.\n\n"
            "## K3: refusal\nroles: part\ncheck: refusal:src/cmd.py#REFUSED: write the post\nThe command refuses.\n\n"
            "## K4: nothing runs it\nroles: part, orchestrator\nfrom: CL4 2026-10-05\nRead the guide first.\n")
        (self.root / ".board" / "memory" / "lessons").mkdir(parents=True)
        for k, v in (("ROOT", str(self.root)), ("CONFIG", {})):
            p = mock.patch.object(kit, k, v)
            p.start()
            self.addCleanup(p.stop)

    def checks(self, run=True):
        return {r["id"]: r for r in kit.memory_checks(run=run)}

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = kit.main(list(argv))
        return rc, buf.getvalue()


class ChecksRun(Project):
    def test_each_kind_holds_and_the_one_with_nothing_is_unchecked_since_its_date(self):
        r = self.checks()
        self.assertEqual({k: v["status"] for k, v in r.items()}, {"K1": "ok", "K2": "ok", "K3": "ok", "K4": "unchecked"})
        self.assertEqual(r["K4"]["since"], "2026-10-05")
        rc, out = self.cli("lessons", "check")
        self.assertEqual(rc, 0, out)
        self.assertIn("UNCHECKED: K4 since 2026-10-05", out)

    def test_a_failing_test_is_flagged_and_the_command_goes_red(self):
        (self.root / "tests" / "test_rule.py").write_text(FAILING)
        r = self.checks()
        self.assertEqual(r["K1"]["status"], "fail")
        self.assertIn("test_rule.py", r["K1"]["why"])
        rc, out = self.cli("lessons", "check")
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED: K1", out)

    def test_a_test_gone_or_renamed_is_flagged(self):
        (self.root / "tests" / "test_rule.py").write_text(PASSING.replace("test_holds", "test_other"))
        self.assertIn("no test_holds", self.checks(run=False)["K1"]["why"])
        (self.root / "tests" / "test_rule.py").unlink()
        r = self.checks(run=False)                       # no run needed to see it is gone
        self.assertEqual(r["K1"]["status"], "fail")
        self.assertIn("no longer exists", r["K1"]["why"])

    def test_code_or_refusal_no_longer_there_is_flagged(self):
        (self.root / "src" / "cmd.py").write_text("def stop():\n    pass\n")
        r = self.checks(run=False)
        self.assertEqual((r["K2"]["status"], r["K3"]["status"]), ("fail", "fail"))
        self.assertIn("no longer holds", r["K3"]["why"])
        (self.root / "src" / "cmd.py").unlink()
        self.assertIn("src/cmd.py no longer exists", self.checks(run=False)["K2"]["why"])

    def test_a_hook_check(self):
        (self.root / "kit" / "lessons.md").write_text("## H1: hooked\nroles: part\ncheck: hook:kit_guard.py\nText.\n")
        self.assertEqual(self.checks(run=False)["H1"]["status"], "fail")
        (self.root / "kit" / "hooks").mkdir()
        (self.root / "kit" / "hooks" / "kit_guard.py").write_text("")
        self.assertEqual(self.checks(run=False)["H1"]["status"], "ok")

    def test_a_ledger_decision_is_checked_by_its_enforced_by(self):
        mdir = str(self.root / ".board" / "memory")
        self.assertTrue(led.observe(mdir, "Posts are written by kit.py form", kind="decision", entry_id="D1",
                                    enforced_by="refusal:src/cmd.py#REFUSED: write the post").get("ok"))
        self.assertTrue(led.observe(mdir, "Answer in English", kind="observation", entry_id="O1").get("ok"))
        r = self.checks()
        self.assertEqual((r["D1"]["status"], r["O1"]["status"]), ("ok", "unchecked"))
        (self.root / "src" / "cmd.py").write_text("")
        self.assertEqual(self.checks(run=False)["D1"]["status"], "fail")


class GivenAsNotCertain(Project):
    def test_resume_lessons_say_unchecked_since_and_failing(self):
        lines = "\n".join(kit._lesson_lines(kit.lessons_for("part")))
        self.assertIn("**K4** nothing runs it: Read the guide first. (unchecked since 2026-10-05: nothing runs it, not certain)", lines)
        self.assertNotIn("K1** tested: The text. (", lines)
        (self.root / "tests" / "test_rule.py").write_text(FAILING)
        self.cli("lessons", "check")                                              # the run is kept for the resume
        lines = "\n".join(kit._lesson_lines(kit.lessons_for("part")))
        self.assertIn("K1** tested: The text. (its check FAILS:", lines)

    def test_the_ledger_rows_in_resume_say_it_too(self):
        mdir = str(self.root / ".board" / "memory")
        led.observe(mdir, "Answer in English", kind="rule", entry_id="R1", scope="role:part")
        txt = "\n".join(led.format_resume(mdir, "part", kit_root=str(self.root)))
        self.assertRegex(txt, r"\*\*R1\*\*: Answer in English \(unchecked since \d{4}-\d{2}-\d{2}: nothing runs it, not certain\)")

    def test_the_memory_hook_says_it(self):
        hook = kit._load_ledger_mod  # noqa: F841  (the hook loads the ledger from the project; point it at the kit's)
        import importlib.util
        spec = importlib.util.spec_from_file_location("m7_jit", str(ROOT / "kit" / "hooks" / "kit_memory_jit.py"))
        jit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jit)
        (self.root / "kit" / "lessons.md").write_text(
            "## F1: files lesson\nroles: part\nfiles: src/*.py\nfrom: X 2026-09-09\nTouch it gently.\n\n"
            "## F2: checked files lesson\nroles: part\nfiles: src/*.py\ncheck: code:src/cmd.py\nIt holds.\n")
        (self.root / ".kit").mkdir()
        (self.root / ".kit" / "ledger.py").write_text((ROOT / "kit" / "ledger.py").read_text())
        data = {"session_id": "m7-%s" % os.getpid(), "cwd": str(self.root), "hook_event_name": "PreToolUse",
                "tool_name": "Edit", "tool_input": {"file_path": str(self.root / "src" / "cmd.py")}}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root), "KIT_ROLE": "part"}):
            out = jit.handle(data)
        seen = jit._seen_file(str(self.root), data)
        if seen and os.path.exists(seen):
            os.unlink(seen)
        msg = json.dumps(out)
        self.assertIn("F1", msg)
        self.assertIn("unchecked since 2026-09-09", msg)
        self.assertNotIn("It holds. (", msg)


class BeCarefulRefused(Project):
    def test_a_be_careful_remedy_is_refused_and_a_check_proposed(self):
        r = kit.lesson_add("L1", ["part"], "Rule passes", "Be careful to check the rule before the commit.")
        self.assertIn("error", r)
        self.assertIn("be careful", r["error"].lower())
        self.assertEqual(r["proposed_check"], "test:tests/test_rule.py::T.test_holds")
        self.assertFalse((self.root / ".board" / "memory" / "lessons" / "L1.md").exists())
        r = kit.lesson_add("L1", ["part"], "Rule passes", "Be careful to check the rule.", check="test:tests/test_rule.py::T.test_holds")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("check: test:tests/test_rule.py::T.test_holds", (self.root / r["path"]).read_text())

    def test_a_new_lesson_without_check_is_unchecked_with_one_proposed(self):
        r = kit.lesson_add("L2", ["part"], "The holds rule", "A test named holds keeps it.")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["proposed_check"], "test:tests/test_rule.py::T.test_holds")
        self.assertIn("unchecked", r["unchecked"])

    def test_a_check_naming_nothing_real_is_refused_at_add(self):
        r = kit.lesson_add("L3", ["part"], "t", "x", check="code:src/gone.py")
        self.assertIn("no longer exists", r.get("error", ""))
        self.assertIn("error", kit.lesson_add("L3", ["part"], "t", "x", check="whatever"))

    def test_an_existing_be_careful_lesson_is_refused_by_check(self):
        (self.root / ".board" / "memory" / "lessons" / "x.md").write_text("## X1: vague\nroles: part\nPay attention to the board.\n")
        err = kit.lessons_check()
        self.assertTrue(any("X1" in e and "pay attention" in e.lower() for e in err), err)

    def test_the_ledger_refuses_it_too(self):
        r = led.observe(str(self.root / ".board" / "memory"), "Double-check the post", kind="lesson")
        self.assertIn("error", r)
        self.assertTrue(led.observe(str(self.root / ".board" / "memory"), "Double-check the post", kind="lesson",
                                    enforced_by="code:src/cmd.py").get("ok"))


class OrchestratorLine(Project):
    def test_one_line_checked_failing_unchecked(self):
        self.cli("lessons", "check")
        line = kit.memory_line()
        self.assertTrue(line.startswith("Memory: 4 entries: 3 checked, 0 failing, 1 unchecked"), line)
        (self.root / "src" / "cmd.py").write_text("")
        self.assertIn("2 failing (K2, K3)", kit.memory_line())

    def test_tests_never_run_here_are_said_so(self):
        self.assertIn("1 whose tests have not run here", kit.memory_line())


class KitsOwnMemory(unittest.TestCase):
    def test_the_kits_lessons_name_their_checks_and_each_names_something_real(self):
        les = kit.parse_lessons((ROOT / "kit" / "lessons.md").read_text(), "kit/lessons.md")
        checked = [x for x in les if x["check"]]
        self.assertGreaterEqual(len(checked), 8, [x["id"] for x in les if not x["check"]])
        for x in checked:
            r = led.check_entry(str(ROOT), x, run=False, cache={})
            self.assertNotEqual(r["status"], "fail", (x["id"], r["why"]))


if __name__ == "__main__":
    unittest.main()
