"""M10: a kind of bug fixed three times, a rule the owner says twice, a decision of his: each becomes a check.

1. Every fix is one observation of its kind in the ledger (`kit.py done` for a step whose form says `fixes:`, a commit
   with `Fix-kind:`), recorded once per source.
2. The third fix of one kind raises it in the orchestrator's resume, first, with the design step to plan; two say nothing.
3. The kit's own ledger is seeded from history: platform leaks, silent errors, two copies out of sync.
5. The owner's rule said once with no check: plan its check; said twice with none: said first. A check on the theme
   quiets it.
6. His decisions: shown first to a session editing their files; one with no check is named; done refuses a branch
   where a decision's check fails.

Run via: python3 -m unittest tests.test_m10_fix_kinds -v
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("ledger_m10", str(ROOT / "kit" / "ledger.py"))
led = importlib.util.module_from_spec(_s)
_s.loader.exec_module(led)
sys.path.insert(0, str(ROOT / "scripts"))
import kit  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.m = str(self.root / ".board" / "memory")

    def first(self):
        return led.alarm_lines(self.m)[0]


class FixKinds(Tmp):
    def test_two_fixes_of_one_kind_say_nothing_the_third_raises_it(self):
        led.record_fix(self.m, "platform leak", "the review launcher on one platform only", "CL26")
        led.record_fix(self.m, "platform leak", "the brief rule for one platform", "CL28")
        self.assertEqual(self.first(), [])
        led.record_fix(self.m, "platform leaks", "the usage ledger read one platform's files", "CL37")
        first = self.first()
        self.assertEqual(len(first), 1, first)
        self.assertIn("Fixed 3 times, one kind of bug: platform leak (CL26, CL28, CL37)", first[0])
        self.assertIn("design step, not a fourth patch", first[0])

    def test_a_source_is_recorded_once(self):
        for _ in range(3):
            r = led.record_fix(self.m, "silent error", "push failed without a word", "CL46")
        self.assertTrue(r.get("existing"))
        self.assertEqual([k["count"] for k in led.fix_kinds(self.m)], [1])
        self.assertEqual(self.first(), [])

    def test_a_fix_needs_its_kind_and_source(self):
        self.assertIn("error", led.record_fix(self.m, "", "x", "CL1"))
        self.assertIn("error", led.record_fix(self.m, "silent error", "x", ""))

    def test_a_planned_design_quiets_the_kind_until_three_new_fixes(self):
        for s in ("A", "B", "C"):
            led.record_fix(self.m, "two copies out of sync", "copy " + s, s)
        self.assertEqual(len(self.first()), 1)
        r = led.mark_designed(self.m, "two copies out of sync", "D-two-copies")
        self.assertEqual(r["ids"], ["F1", "F2", "F3"])
        self.assertEqual(self.first(), [])
        for s in ("D", "E"):
            led.record_fix(self.m, "two copies out of sync", "copy " + s, s)
        self.assertEqual(self.first(), [])
        led.record_fix(self.m, "two copies out of sync", "copy F", "F")
        self.assertIn("(D, E, F)", self.first()[0])

    def test_fixes_are_counted_never_printed_as_rules(self):
        for s in ("A", "B", "C"):
            led.record_fix(self.m, "silent error", "hidden " + s, s)
        data = led.for_resume(self.m, "part")
        self.assertEqual(data["active"], [])
        self.assertEqual(led.distil_candidates(self.m), [])
        self.assertEqual(led.entry_items(led.load_entries(self.m)), [])

    def test_commits_name_their_kind(self):
        log = ("aaa111\x1fFix the launcher on Windows\n\nFix-kind: platform leak\n\x1e"
               "bbb222\x1fCL46: fixes the push error\n\x1e"
               "ccc333\x1fAdd a page\n\x1e")
        named, unnamed = led.commit_fixes(log)
        self.assertEqual(named, [("aaa111", "platform leak", "Fix the launcher on Windows")])
        self.assertEqual(unnamed, [("bbb222", "CL46: fixes the push error")])


class OwnerRules(Tmp):
    def rule(self, quote, theme, check="review"):
        return led.observe(self.m, "Git is the one source", quote=quote, source="chat", theme=theme, kind="rule", enforced_by=check)

    def test_said_once_plan_its_check_said_twice_said_first(self):
        self.rule("git is the one source", "git is the one source")
        first, then = led.alarm_lines(self.m)
        self.assertEqual(first, [])
        self.assertEqual(len(then), 1)
        self.assertIn("plan its check now", then[0])
        self.rule("git is the only record, again", "git is the one source")
        first, then = led.alarm_lines(self.m)
        self.assertEqual(len(first), 1, first)
        self.assertIn("The owner said it 2 times and nothing enforces it", first[0])
        self.assertIn("never another memory line", first[0])
        self.assertEqual(then, [])

    def test_a_check_on_the_theme_quiets_it(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_git.py").write_text("def test_x():\n    pass\n")
        self.rule("git is the one source", "git is the one source")
        self.rule("git is the one source", "git is the one source")
        led.observe(self.m, "The board follows git", kind="decision", theme="git is the one source",
                    enforced_by="test:tests/test_git.py")
        self.assertEqual(led.alarm_lines(self.m), ([], []))

    def test_a_rule_without_his_words_is_not_his(self):
        led.observe(self.m, "Use short names", kind="rule")
        self.assertEqual(led.owner_rules(self.m), [])


class Decisions(Tmp):
    def test_a_decision_without_a_check_is_named(self):
        led.observe(self.m, "One board token per machine", kind="decision", scope="files:kit/board/board_token.py")
        self.assertIn("has no check", led.alarm_lines(self.m)[1][0])

    def test_his_decision_comes_first_to_a_session_editing_its_files(self):
        led.observe(self.m, "Tokens are short", kind="observation", scope="files:kit/board/*.py", entry_id="A1")
        led.observe(self.m, "One board token per machine", kind="decision", scope="files:kit/board/board_token.py")
        hits = led.for_files(self.m, ["kit/board/board_token.py"])
        self.assertEqual([h["kind"] for h in hits], ["decision", "observation"])

    def test_done_refuses_a_branch_where_his_decision_does_not_hold(self):
        (self.root / "server.py").write_text("# the drop box\n")
        led.observe(self.m, "Every AI link reaches its post's drop box", kind="decision", scope="files:server.py",
                    enforced_by="code:server.py#the drop box")
        with mock.patch.object(kit, "_resolve_memory_dir", return_value=self.m):
            self.assertEqual(kit.decision_breaks(["server.py"], root=str(self.root)), [])
            (self.root / "server.py").write_text("# gone\n")
            why = kit.decision_breaks(["server.py"], root=str(self.root))
            self.assertEqual(len(why), 1)
            self.assertIn("does not hold with this branch", why[0])
            self.assertEqual(kit.decision_breaks(["other.py"], root=str(self.root)), [])


class DoneRecordsTheFix(Tmp):
    def test_the_form_s_fixes_line_is_one_fix_of_its_kind(self):
        form = "".join("## %s\n%s\n\n" % (k, "(nothing)" if k == "asked" else "x") for k in kit.FORM_PARTS if k != "body") + "## fixes\nsilent error: the push failed without a word\n\n## body\nx\n"
        f, err = kit.read_form(form)
        self.assertEqual(err, [])
        self.assertEqual(f["fixes"], "silent error: the push failed without a word")
        with mock.patch.object(kit, "_resolve_memory_dir", return_value=self.m):
            self.assertEqual(kit.record_step_fix(f, "cl46-end-session"), [])
            self.assertEqual(kit.record_step_fix(f, "cl46-end-session"), [])       # once
            self.assertIn("fixes:", kit.record_step_fix({"fixes": "no kind here"}, "x")[0])
        k = led.fix_kinds(self.m)
        self.assertEqual([(x["kind"], x["count"], x["sources"]) for x in k], [("silent error", 1, ["cl46-end-session"])])


class Seed(unittest.TestCase):
    """The kit's own ledger starts true: today's three kinds with their fixes."""
    def test_the_three_kinds_start_with_their_fixes(self):
        m = str(ROOT / ".board" / "memory")
        k = {x["kind"]: x for x in led.fix_kinds(m)}
        self.assertEqual(k["platform leak"]["total"], 8)                       # CL26, CL28, CL37, CL44 x2, CL51, R4, 106bba6
        self.assertEqual(k["platform leak"]["design"], "r5-every-rule-that-differs-by-platform-says")
        # 11-27: the orchestrator gave the two other kinds their design steps (CL71, CL72): no alarm for them any more
        self.assertGreaterEqual(k["silent error"]["total"], 3)
        self.assertTrue(k["silent error"]["design"])
        self.assertGreaterEqual(k["two copies out of sync"]["total"], 3)
        self.assertTrue(k["two copies out of sync"]["design"])
        first = led.alarm_lines(m)[0]
        self.assertFalse(any("silent error" in l or "two copies out of sync" in l for l in first), first)
        self.assertTrue(any("git is the one source" in l for l in first) and any("never hide an error" in l for l in first), first)

    def test_the_orchestrator_s_resume_says_them_first(self):
        with mock.patch.dict(os.environ, {"KIT_STALE_OK": "1"}):
            first, _ = kit.alarm_lines()
        self.assertTrue(first)


if __name__ == "__main__":
    unittest.main()
