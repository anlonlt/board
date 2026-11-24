"""Tests for scripts/memory_bench.py (board vs beads vs ProjectArchitect memory simulator)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import memory_bench as mb  # noqa: E402


class MemoryBenchTests(unittest.TestCase):
    def test_synthetic_project_benchmark_and_scaling_curve(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "demo-proj"
            (proj / "docs").mkdir(parents=True)
            (proj / ".story" / "lessons").mkdir(parents=True)
            (proj / ".story" / "state").mkdir(parents=True)
            (proj / "docs" / "RULES.md").write_text(
                "# Rules\n\n## Audio\n\n"
                "| id | rule | his words | when | source | carried by | match |\n"
                "|---|---|---|---|---|---|---|\n"
                "| R50 | Phone speaker only, never earphones | no earphones | 2026-11-12 | board:q3b | `docs/BOARD.md` | speaker |\n"
                "| R51 | Ignore dataset licence catch | ignore licence | 2026-11-12 | board:by3 | `docs/BOARD.md` | licence |\n"
                "| R01 | Say it once on the board | say it once | 2026-11-12 | chat:say | `.claude/hooks/say_it_once.py` | once |\n"
                "| R14 | Modal training budget $0 cap | modal budget | 2026-11-15 | board:5v6 | `docs/BOARD.md` | modal |\n"
                "| R23 | Any picture readable on phone width without zoom | readable on phone | 2026-11-12 | board:or6 | `docs/BOARD.md` | phone |\n"
            )
            (proj / ".story" / "lessons" / "2026-11-19-test.md").write_text("# Lesson: always read board first\n")
            (proj / ".story" / "state" / "voice.md").write_text("Next: train VR2\n")

            rep = mb.run_benchmark(str(proj), scales=[20, 100])
            self.assertEqual(rep["ingested"]["rules"], 5)
            # board must use fewer tokens and fewer leaks than flat beads / ProjectArchitect
            b_sum = rep["summary"]["board"]
            beads_sum = rep["summary"]["beads"]
            pa_sum = rep["summary"]["ProjectArchitect"]
            self.assertLessEqual(b_sum["avg_tokens"], beads_sum["avg_tokens"])
            self.assertLessEqual(b_sum["total_leaks"], beads_sum["total_leaks"])
            self.assertLessEqual(b_sum["total_leaks"], pa_sum["total_leaks"])
            # Scaling curve: at 100 rules, board stays bounded while beads & PA grow linearly
            s100 = rep["scaling"][1]
            self.assertEqual(s100["n_rules"], 100)
            self.assertLess(s100["board"], s100["beads"])
            self.assertLess(s100["board"], s100["ProjectArchitect"])
            txt = mb.format_report(rep)
            self.assertIn("Memory Benchmark:", txt)
            self.assertIn("Scaling Curve", txt)


if __name__ == "__main__":
    unittest.main()
