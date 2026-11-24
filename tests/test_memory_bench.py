"""scripts/memory_bench.py: the measure must fail on a broken memory, and must not touch the project it reads."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import memory_bench as mb  # noqa: E402

RULES = """# Rules

## Audio

| id | rule | his words | when | source | carried by | match |
|---|---|---|---|---|---|---|
| R1 | Phone speaker only, never earphones | no earphones | 2026-11-12 | board:a | `docs/BOARD.md` | x |
| R2 | Say the licence \\| ignore its catch | ignore it | 2026-11-12 | board:b | `.claude/hooks/say_it_once.py` | x |
| X3 | A dropped rule | gone | 2026-11-12 | board:c | `docs/BOARD.md` | x |

## Money

| id | rule | his words | when | source | carried by | match |
|---|---|---|---|---|---|---|
| R4 | Modal spends need his yes | ask me | 2026-11-12 | board:d | `docs/BOARD.md` | x |
| R5 | Budget cap is zero | none | 2026-11-12 | board:e | `docs/BOARD.md` | x |
| R6 | Report the price with the result | price | 2026-11-12 | board:f | `docs/BOARD.md` | x |
"""
PROBES = [
    {"id": "speaker", "role": "part", "post": "P1", "files": ["app/a.py"], "question": "which speaker, which licence?",
     "gold": ["R1", "R2"], "lookup": ["speaker"]},
    {"id": "money", "role": "orchestrator", "post": None, "files": [], "question": "may I spend on modal?",
     "gold": ["R4", "R6", "Q:Q1", "L:2026-11-19-read"], "lookup": ["modal"]},
]


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)
        self.proj = self.tmp / "demo"
        for d in ("docs", ".story/lessons", ".kit/board_data"):
            (self.proj / d).mkdir(parents=True)
        (self.proj / "docs" / "RULES.md").write_text(RULES)
        (self.proj / ".story" / "lessons" / "2026-11-19-read.md").write_text("# Read the board before answering\n\nwhy\n")
        self.db = self.proj / ".kit" / "board_data" / "board.db"
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, data TEXT)")
        for did, d in (("P1", {"part": "P1", "title": "A part", "status": "in progress", "train": "t"}),
                       ("Q1", {"column": "question", "title": "Buy the second run or wait?", "status": "open"}),
                       ("Q2", {"column": "question", "title": "An answered one", "status": "done"})):
            c.execute("INSERT INTO docs VALUES ('posts', ?, ?)", (did, json.dumps(d)))
        c.commit()
        c.close()
        self.probes = self.tmp / "probes.json"
        self.probes.write_text(json.dumps({"probes": PROBES}))

    def bench(self, *tools):
        return mb.run_benchmark(str(self.proj), str(self.probes), list(tools))["tools"]


class Ingest(Base):
    def test_items(self):
        items = mb.ingest(str(self.proj), str(self.tmp))["items"]
        self.assertEqual([i["id"] for i in items], ["R1", "R2", "R4", "R5", "R6", "L:2026-11-19-read", "Q:Q1"])
        self.assertEqual(items[1]["summary"], "Say the licence | ignore its catch")

    def test_the_project_is_untouched(self):
        def snapshot():
            return sorted((str(p.relative_to(self.proj)), p.stat().st_size, p.stat().st_mtime_ns)
                          for p in self.proj.rglob("*") if p.is_file())
        before, opened, connect = snapshot(), [], sqlite3.connect

        def spy(path, *a, **k):
            opened.append(str(path))
            return connect(path, *a, **k)
        with mock.patch.object(mb.sqlite3, "connect", spy):     # an open in place can leave no trace once closed
            self.bench(mb.Nothing(), mb.Flat(), mb.Board())
        self.assertEqual(before, snapshot())
        self.assertTrue(opened)
        self.assertEqual([p for p in opened if str(self.proj) in p], [])

    def test_a_probe_with_an_unknown_item_is_refused(self):
        self.probes.write_text(json.dumps({"probes": [dict(PROBES[0], gold=["R99"])]}))
        with self.assertRaisesRegex(mb.ToolError, "R99"):
            self.bench(mb.Flat())


class Measure(Base):
    def test_no_memory_scores_zero_though_its_header_holds_the_question(self):
        s = self.bench(mb.Nothing())["none"]
        self.assertEqual((s["hit_turn1"], s["hit_lookup"], s["tokens_memory"]), (0, 0, 0))

    def test_flat_is_the_ceiling(self):
        s = self.bench(mb.Flat())["flat"]
        self.assertEqual((s["hit_turn1"], s["gold"], s["coverage"]), (6, 6, 100.0))

    def test_board_delivers_what_it_holds_through_the_real_resume(self):
        s = self.bench(mb.Board())["board"]
        self.assertEqual((s["hit_turn1"], s["control_hits"]), (6, 0))
        self.assertGreater(s["tokens_memory"], 0)
        self.assertGreater(s["tokens_total"], s["tokens_memory"])

    def test_a_board_that_gives_nothing_scores_zero(self):
        class Empty(mb.Board):
            def setup(self, snap, items, d):
                super().setup(snap, [], d)
        s = self.bench(Empty())["board"]
        self.assertEqual((s["hit_turn1"], s["tokens_memory"], s["coverage"]), (0, 0, 0.0))

    def test_a_rule_squashed_into_a_cut_principle_is_not_recall(self):
        """What the first version of this bench scored 100%: themes cut to two 70-character samples."""
        class Squashed(mb.Board):
            def setup(self, snap, items, d):
                super().setup(snap, items, d)
                led = self._ledger()
                for cl in led.distil_candidates(self.mem, min_cluster=3):
                    pr = led.propose_principle(self.mem, "Theme %s: %s" % (cl["theme"], cl["items"][0]["summary"][:9]),
                                               cl["ids"], theme=cl["theme"])
                    led.confirm_principle(self.mem, pr["principle"]["id"], "like-1")
        s = self.bench(Squashed())["board"]
        self.assertEqual(s["rows"][1]["missed"], ["R6"])      # R4 comes back with the lookup, R6 does not
        self.assertLess(s["coverage"], self.bench(mb.Board())["board"]["coverage"])

    def test_curation_is_used_only_when_given(self):
        s = self.bench(mb.Board({"R1": {"scope": "role:orchestrator"}}))["board"]
        self.assertEqual(s["rows"][0]["hit_turn1"], 1)

    def test_a_files_rule_comes_from_the_jit_hook(self):
        s = self.bench(mb.Board({"R2": {"scope": "files:app/*.py"}}))["board"]
        self.assertEqual((s["rows"][0]["hit_lookup"], s["rows"][0]["missed"]), (2, []))

    def test_whatever_the_clerk_is_given_is_noise(self):
        clerk = {"id": "c", "role": "clerk", "post": None, "files": [], "question": "a comment came", "gold": [], "lookup": []}
        self.probes.write_text(json.dumps({"probes": PROBES + [clerk]}))
        t = self.bench(mb.Board(), mb.Flat())
        self.assertEqual([t[n]["roles"]["clerk"]["noise"] for n in ("board", "flat")], [0, 7])
        self.assertGreater(t["flat"]["roles"]["clerk"]["noise_tokens"], 0)
        self.assertEqual(t["board"]["roles"]["part"], dict(t["board"]["roles"]["part"], gold=2, hit_turn1=2, noise=4))
        self.assertIn("| clerk (1) |", mb.format_report(mb.run_benchmark(str(self.proj), str(self.probes), [mb.Flat()])))
        self.probes.write_text(json.dumps({"probes": [dict(clerk, gold=["R1"])]}))
        with self.assertRaisesRegex(mb.ToolError, "only routes"):
            self.bench(mb.Flat())

    def test_a_failing_tool_stops_the_run(self):
        bad = self.tmp / "bd"
        bad.write_text("#!/bin/sh\nexit 3\n")
        bad.chmod(0o755)
        with self.assertRaisesRegex(mb.ToolError, "exit 3"):
            self.bench(mb.Beads(str(bad)))


class Cli(Base):
    def test_a_tool_not_given_is_reported_not_run(self):
        rep = mb.run_benchmark(str(self.proj), str(self.probes), [mb.Nothing()], [("beads", "no --bd")])
        self.assertNotIn("beads", rep["tools"])
        self.assertIn("| beads | not run: no --bd |", mb.format_report(rep))

    def test_a_bad_probe_file_exits_1(self):
        self.probes.write_text(json.dumps({"probes": [dict(PROBES[0], role="nobody")]}))
        err, sys.stderr = sys.stderr, open(os.devnull, "w")
        try:
            self.assertEqual(mb.main(["--project", str(self.proj), "--probes", str(self.probes)]), 1)
        finally:
            sys.stderr.close()
            sys.stderr = err


if __name__ == "__main__":
    unittest.main()
