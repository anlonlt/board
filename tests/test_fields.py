"""Rules, principles, fields: an exact rule stays under its principle, a principle hardens with new rows, a field opens by a
script (a file, a moment), every step up is the owner's yes, and `moves` finds the next ones with no model.

Run via: python3 -m unittest tests.test_fields -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("ledger_f", str(ROOT / "kit" / "ledger.py"))
led = importlib.util.module_from_spec(_s)
_s.loader.exec_module(led)


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.m = os.path.join(self.t.name, ".board", "memory")
        for i, (s, th) in enumerate((("No link in chat", "report"), ("Results go on the board", "report"), ("A claim has a count", "report"),
                                     ("Text is 16px", "design")), 1):
            led.observe(self.m, s, kind="rule", theme=th, entry_id="R%d" % i, source="c%d" % i)

    def ok(self, r):
        self.assertTrue(r.get("ok"), r)
        return r

    def row(self, i):
        return next(e for e in led.load_entries(self.m) + led.load_archive(self.m) if e["id"] == i)   # M8: a replaced one is archived

    def text(self, role="part", **k):
        return "\n".join(led.format_resume(self.m, role, **k))

    def principle(self):
        self.ok(led.propose_principle(self.m, "Say it once, on the board", ["R1", "R2", "R3"], keeps=["R1"], not_for="his own chat"))
        return self.ok(led.confirm_principle(self.m, "P1", "his-yes"))


class Principles(Base):
    def test_an_exact_rule_stays_under_its_principle(self):
        self.principle()
        self.assertEqual([self.row(i)["status"] for i in ("R1", "R2", "R3")], ["active", "subsumed", "subsumed"])
        t = self.text()
        self.assertIn("- **P1**: Say it once, on the board Not for: his own chat.\n  - exactly: **R1**: No link in chat", t)
        self.assertEqual(t.count("No link in chat"), 1)
        self.assertNotIn("Results go on the board", t)
        self.assertIn("a decision, then the exact rule, then the principle", t)
        self.assertEqual(led.check_ledger(self.m)["errors"], [])

    def test_it_keeps_only_what_it_stands_on(self):
        self.assertIn("R4", led.propose_principle(self.m, "x", ["R1"], keeps=["R4"])["error"])

    def test_it_hardens(self):
        self.principle()
        led.observe(self.m, "A message only points", kind="rule", theme="report", entry_id="R9")
        mv = led.moves(self.m)
        self.assertEqual([(x["move"], x["ids"], x["to"]) for x in mv], [("join", ["R9"], "P1")])
        self.assertIn("P9", led.propose_principle(self.m, "y", ["R9"], refines="P9")["error"])
        self.ok(led.propose_principle(self.m, "Say it once, whole; messages point", ["R9"], refines="P1"))
        self.assertEqual(self.row("P1")["status"], "active")                    # nothing changes before his yes
        self.assertIn("Say it once, on the board", self.text())
        self.ok(led.confirm_principle(self.m, "P2", "his-yes-2"))
        self.assertEqual((self.row("P1")["status"], self.row("P2")["keeps"], self.row("P2")["not_for"]), ("replaced", ["R1"], "his own chat"))
        self.assertEqual((self.row("P1")["replaced_by"], [e["id"] for e in led.load_entries(self.m) if e["id"] == "P1"]), ("P2", []))
        self.assertEqual([self.row(i)["subsumed_by"] for i in ("R2", "R9")], ["P2", "P2"])
        self.assertEqual(led.moves(self.m), [])
        self.assertEqual(led.check_ledger(self.m)["errors"], [])

    def test_a_refinement_of_another_reach_needs_widen(self):
        self.principle()
        led.observe(self.m, "Only for reviews", kind="rule", theme="report", scope="role:review", entry_id="R8")
        self.assertEqual(led.moves(self.m), [])
        self.assertIn("scope", led.propose_principle(self.m, "z", ["R8"], refines="P1")["error"])


class Fields(Base):
    def field(self, members="R4", opens="files:*.css, moment:report", theme="design"):
        r = self.ok(led.propose_field(self.m, "anything visual", members.split(","), theme, opens, not_for="plain text"))
        return r["field"]["id"]

    def test_closed_until_a_file_or_a_moment(self):
        f = self.field()
        self.assertIn("Text is 16px", self.text())                              # proposed: loads as before
        self.ok(led.confirm_principle(self.m, f, "yes"))
        t = self.text()
        self.assertNotIn("Text is 16px", t)
        self.assertIn("- design (1): anything visual; opens at files:*.css, moment:report", t)
        self.assertIn("No link in chat", t)
        opened = lambda **k: [r["id"] for _, rows in led.open_fields(self.m, k.pop("role", "part"), **k) for r in rows]     # noqa: E731
        self.assertEqual(opened(files=["web/a.css"]), ["R4"])
        self.assertEqual(opened(files=["a.py"]), [])
        self.assertEqual(opened(moment="report"), ["R4"])
        self.assertEqual(opened(moment="ask"), [])
        self.assertEqual(opened(role="clerk", moment="report"), [])
        self.assertIn("(not for: plain text)", "\n".join(led.field_lines(led.open_fields(self.m, "part", moment="report"))))

    def test_a_field_open_at_start_prints_its_rows(self):
        self.ok(led.confirm_principle(self.m, self.field(opens="start, moment:ask"), "yes"))
        self.assertIn("Text is 16px", self.text())

    def test_a_row_in_two_fields_is_shut_only_when_both_are(self):
        self.ok(led.confirm_principle(self.m, self.field(), "yes"))
        self.ok(led.confirm_principle(self.m, self.field(opens="start", theme="phone"), "yes"))
        self.assertIn("Text is 16px", self.text())

    def test_a_principles_exact_rows_come_and_go_with_it(self):
        self.principle()
        self.ok(led.confirm_principle(self.m, self.field("P1", "moment:ask", "report"), "yes"))
        self.assertNotIn("No link in chat", self.text())
        t = "\n".join(led.field_lines(led.open_fields(self.m, "part", moment="ask")))
        self.assertIn("exactly: **R1**: No link in chat", t)

    def test_a_refined_principle_stays_in_its_field(self):
        self.principle()
        f = self.field("P1", "moment:ask", "report")
        self.ok(led.confirm_principle(self.m, f, "yes"))
        led.observe(self.m, "more", kind="rule", theme="report", entry_id="R9")
        self.ok(led.propose_principle(self.m, "wider words", ["R9"], refines="P1"))
        self.ok(led.confirm_principle(self.m, "P2", "yes"))
        self.assertEqual(self.row(f)["subsumes"], ["P2"])
        self.assertEqual(led.check_ledger(self.m)["errors"], [])

    def test_scope_still_holds_inside_a_field(self):
        led.observe(self.m, "Only reviews", kind="rule", scope="role:review", theme="design", entry_id="R7")
        self.ok(led.confirm_principle(self.m, self.field("R4,R7"), "yes"))
        ids = lambda role: [r["id"] for _, rows in led.open_fields(self.m, role, moment="report") for r in rows]      # noqa: E731
        self.assertEqual((ids("part"), ids("review")), (["R4"], ["R4", "R7"]))

    def test_refused(self):
        for opens in ("", "moment:lunch", "whenever", "files:"):
            self.assertIn("error", led.propose_field(self.m, "s", ["R4"], "d", opens), opens)
        self.assertIn("error", led.propose_field(self.m, "s", ["R99"], "d", "start"))
        self.assertIn("error", led.propose_field(self.m, "s", [], "d", "start"))

    def test_the_file_survives_a_round_trip(self):
        self.principle()
        self.field()
        a = led.load_entries(self.m)
        strip = lambda es: [{k: v for k, v in e.items() if k != "_file"} for e in es]                                # noqa: E731
        self.assertEqual(strip(led.parse_ledger(led.render_ledger(a))), strip(a))


class Moves(Base):
    def test_distil_then_field_then_hook(self):
        self.assertEqual([(m["move"], m["to"]) for m in led.moves(self.m)], [("distil", "report")])
        self.principle()
        led.observe(self.m, "x", kind="rule", theme="report", scope="role:review", entry_id="R8")
        self.ok(led.propose_principle(self.m, "for reviews", ["R8"]))
        self.ok(led.confirm_principle(self.m, "P2", "y"))
        self.assertEqual([(m["move"], m["ids"]) for m in led.moves(self.m)], [("field", ["P1", "P2"])])
        self.assertIn("error", led.broke(self.m, "R1", ""))
        for src in ("c1", "c1"):
            self.ok(led.broke(self.m, "R1", src))
        self.assertNotIn("hook", [m["move"] for m in led.moves(self.m)])        # the same breach told twice is one
        self.ok(led.broke(self.m, "R1", "c2"))
        self.assertIn(("hook", ["R1"]), [(m["move"], m["ids"]) for m in led.moves(self.m)])

    def test_the_orchestrator_is_told_in_one_line(self):
        self.assertIn("Rules that could move a step (1)", self.text("orchestrator"))
        self.assertNotIn("could move", self.text("part"))


class HisYes(unittest.TestCase):
    def test_his_no_confirms_nothing(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_handoff import kit
        from unittest import mock
        c = {"y": {"from": "owner", "text": "Answer: Yes to all"}, "n": {"from": "owner", "text": "Answer: Leave them"}, "l": {"from": "owner", "like": True, "text": ""},
             "m": {"from": "owner", "answer": "No, keep R1"}, "a": {"from": "orchestrator", "text": "yes"}}
        with mock.patch.object(kit, "_load_board_docs", return_value=({}, c)):
            self.assertEqual([i for i in c if kit._not_his_yes("m", i) is None], ["y", "l"])


class Opened(Base):
    """By the scripts, not by a judgement."""

    def setUp(self):
        super().setUp()
        r = led.propose_field(self.m, "anything visual", ["R4"], "design", "files:*.css, moment:review, moment:report")
        led.confirm_principle(self.m, r["field"]["id"], "yes")
        self.d = Path(self.t.name)
        (self.d / ".kit").mkdir()
        (self.d / ".kit" / "config.json").write_text(json.dumps({"project": "D", "kit": {"memory_dir": ".board/memory"}}))
        for rel in ("scripts/kit.py", "kit/ledger.py"):
            (self.d / ".kit" / Path(rel).name).write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY"))}
        self.env.update(CLAUDE_PROJECT_DIR=str(self.d), KIT_BOARD_URL="http://127.0.0.1:9")

    def jit(self, path):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_memory_jit.py")], capture_output=True, text=True, env=self.env,
                           input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": os.path.basename(str(self.d)),
                                             "cwd": str(self.d), "tool_input": {"file_path": str(self.d / path)}}))
        return r.stdout

    def test_the_edit_hook_opens_it(self):
        self.assertIn("Text is 16px", self.jit("a.css"))
        self.assertEqual(self.jit("b.css").strip(), "")                         # once a session
        self.assertEqual(self.jit("a.py").strip(), "")

    def test_a_moment_opens_it(self):
        run = lambda *a: subprocess.run([sys.executable, "-I", str(self.d / ".kit" / "kit.py")] + list(a), capture_output=True, text=True, env=self.env,   # noqa: E731
                                        cwd=str(self.d)).stdout
        self.assertIn("Text is 16px", run("ledger", "open", "report"))
        self.assertIn("no field opens", run("ledger", "open", "ask"))
        self.assertIn("Text is 16px", run("done", "nope"))
        self.assertIn('"move": "distil"', run("ledger", "moves"))


if __name__ == "__main__":
    unittest.main()
