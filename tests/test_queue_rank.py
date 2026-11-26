"""R6: every new step is measured against every open step, for its place in the queue and for what can run beside it.

On a fixture board (no real board is read or written):
- a new damage step goes to the top, and a running step it would undo is asked to pause;
- a step two others wait on goes before them;
- a bug goes before a feature built on the same files; date only breaks a tie;
- two steps on the same file never both run (the later one waits, named);
- a launch out of order is refused with the step that should go first named; --anyway records why.

Run via: python3 -m unittest discover -s tests -p test_queue_rank.py
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_kit():
    spec = importlib.util.spec_from_file_location("kit_queue_rank", str(ROOT / "scripts" / "kit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def step(part, title, task="", created="2026-11-26T10:00:00Z", status="planned", files=(), **kw):
    d = {"part": part, "title": title, "task": task, "train": "t", "status": status, "created_at": created, "column": "info"}
    if files:
        d["subtasks"] = [{"n": 1, "title": title, "files": list(files)}]
    d.update(kw)
    return d


class QueueRank(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load_kit()

    def order(self, posts):
        return [r["part"] for r in self.kit.queue_rank(posts, areas={})]

    def test_damage_goes_to_the_top_and_pauses_what_it_would_undo(self):
        posts = {
            "f1": step("F1", "A new view", "Add a view.", created="2026-11-26T08:00:00Z", files=["board.html"]),
            "f2": step("F2", "Faster resume", "Resume reads less.", created="2026-11-26T09:00:00Z", status="in progress",
                       files=["store.py"], session_id="session_f2"),
            "d1": step("D1", "Artifact load", "Artifact load overwrote 11 posts with an older copy.",
                       created="2026-11-26T12:00:00Z", files=["store.py"]),
        }
        rows = self.kit.queue_rank(posts, areas={})
        self.assertEqual(rows[0]["part"], "D1", rows)
        self.assertIn("repairs damage", rows[0]["why"])
        # it shares store.py with F2, which runs: D1 waits for it, named
        self.assertFalse(rows[0]["can_start"])
        self.assertIn("F2 runs (shares store.py)", "; ".join(rows[0]["waits"]))
        pauses = self.kit.pause_messages(posts, "d1", areas={})
        self.assertEqual([p["post"] for p in pauses], ["f2"])
        self.assertEqual(pauses[0]["session_id"], "session_f2")
        self.assertIn("next save point", pauses[0]["message"])
        # "never overwrites" is no damage; a feature step names none either
        quiet = {"x": step("X1", "Prompts generated", "The kit never overwrites the prompt.")}
        self.assertFalse(self.kit.queue_rank(quiet, areas={})[0]["damage"])
        # the orchestrator's --damage says it when the words do not
        told = {"x": step("X1", "Done on a computer", "Done checks the local board.", damage="the review: 11 posts lost")}
        self.assertTrue(self.kit.queue_rank(told, areas={})[0]["damage"])

    def test_a_step_two_others_wait_on_goes_before_them(self):
        posts = {
            "a": step("A1", "Early feature", "Something.", created="2026-11-26T01:00:00Z"),
            "b": step("B1", "Second", "Builds on M1.", created="2026-11-26T02:00:00Z"),
            "c": step("C1", "Third", "Waits on M1 (its format).", created="2026-11-26T03:00:00Z"),
            "m": step("M1", "The map's format", "Things, links, flags.", created="2026-11-26T05:00:00Z"),
        }
        order = self.order(posts)
        self.assertLess(order.index("M1"), order.index("B1"))
        self.assertLess(order.index("M1"), order.index("C1"))
        self.assertEqual(order[0], "M1", order)          # it unblocks two: before the older A1
        rows = {r["part"]: r for r in self.kit.queue_rank(posts, areas={})}
        self.assertIn("before B1, C1", rows["M1"]["why"])
        self.assertIn("M1 first", "; ".join(rows["C1"]["waits"]))
        # a mere mention is history, not a wait
        hist = {"m": posts["m"], "h": step("H1", "Later", "M1's part found this.", created="2026-11-26T06:00:00Z")}
        self.assertEqual(self.kit.queue_rank(hist, areas={})[1]["waits"], [])

    def test_bug_before_feature_and_date_only_for_a_tie(self):
        posts = {
            "f": step("F1", "A new panel", "A panel for trains.", created="2026-11-26T01:00:00Z", files=["panel.js"]),
            "b": step("B1", "Fix the panel's crash", "The panel crashes on an empty train.", created="2026-11-26T05:00:00Z",
                      files=["panel.js"]),
            "g": step("G1", "Another feature", "Something else.", created="2026-11-26T00:30:00Z", files=["other.py"]),
            "h": step("H1", "A later feature", "Something more.", created="2026-11-26T02:00:00Z", files=["more.py"]),
        }
        order = self.order(posts)
        self.assertEqual(order[0], "B1", order)
        self.assertLess(order.index("G1"), order.index("H1"))   # two features, nothing apart: the older first
        rows = {r["part"]: r for r in self.kit.queue_rank(posts, areas={})}
        self.assertIn("a bug in what F1 builds", rows["B1"]["why"])

    def test_two_steps_on_the_same_file_never_both_run(self):
        posts = {
            "r": step("R1", "Running", "A feature.", status="in progress", files=["scripts/store.py"]),
            "n": step("N1", "Next", "Another feature.", created="2026-11-26T11:00:00Z", files=["store.py"]),
            "o": step("O1", "Other", "A third feature.", created="2026-11-26T12:00:00Z", files=["view.js"]),
            "p": step("P1", "Pair", "A fourth feature.", created="2026-11-26T13:00:00Z", files=["view.js"]),
        }
        rows = {r["part"]: r for r in self.kit.queue_rank(posts, areas={})}
        self.assertFalse(rows["N1"]["can_start"])
        self.assertIn("R1 runs (shares store.py)", "; ".join(rows["N1"]["waits"]))
        self.assertTrue(rows["O1"]["can_start"])
        self.assertFalse(rows["P1"]["can_start"])               # O1 goes first on view.js
        self.assertIn("O1 goes first (shares view.js)", "; ".join(rows["P1"]["waits"]))
        # an area of the map (M1's `map.areas` globs) is shared like a file
        area = {"x": step("X1", "One", "f.", files=["kit/board/a.py"]),
                "y": step("Y1", "Two", "g.", created="2026-11-26T11:00:00Z", files=["kit/board/b.py"])}
        r2 = {r["part"]: r for r in self.kit.queue_rank(area, areas={"board": ["kit/board/*"]})}
        self.assertIn("area board", "; ".join(r2["Y1"]["waits"]))

    def test_a_launch_out_of_order_is_refused_and_anyway_is_recorded(self):
        kit = self.kit
        posts = {
            "d": step("D1", "Repair", "The sync wiped the comments.", created="2026-11-26T12:00:00Z", files=["sync.py"]),
            "f": step("F1", "Feature", "A new view.", created="2026-11-26T08:00:00Z", files=["view.js"]),
        }
        self.assertIn("D1 goes first", kit.order_problem(posts, "f", areas={}))
        self.assertIn("launch `d`", kit.order_problem(posts, "f", areas={}))
        self.assertIsNone(kit.order_problem(posts, "d", areas={}))
        # through `kit.py launch` itself: refused, then --anyway goes on and writes why on the post
        kit._launch_posts = lambda post: posts
        kit._prompt_problem = lambda rel: None
        kit.circle_gate = lambda *a, **k: None
        kit.step_holder = lambda *a, **k: None
        kit.launch_mark = lambda *a, **k: None
        kit._kit_config = lambda: {}
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "f.json")
            with open(pf, "w") as fh:
                json.dump(posts["f"], fh)
            r = kit.launch("f", url="http://127.0.0.1:9", token="x", web=True, post_file=pf)
            self.assertFalse(r["ok"])
            self.assertIn("out of order", r["error"])
            self.assertIn("D1 goes first", r["error"])
            self.assertIn("--anyway", r["error"])
            r = kit.launch("f", url="http://127.0.0.1:9", token="x", web=True, post_file=pf, anyway="the owner asked for it now")
            self.assertTrue(r["ok"], r)
            self.assertIn("D1 goes first", r["out_of_order"])
            self.assertEqual(r["launched_out_of_order"]["why"], "the owner asked for it now")

    def test_a_new_step_re_ranks_the_whole_queue(self):
        posts = {"f": step("F1", "Feature", "A view.", created="2026-11-26T08:00:00Z")}
        self.assertEqual(self.order(posts), ["F1"])
        posts["d"] = step("D1", "Repair", "Posts lost on load.", created="2026-11-26T12:00:00Z")
        self.assertEqual(self.order(posts), ["D1", "F1"])
        lines = self.kit.queue_lines(posts, areas={})
        self.assertTrue(lines[0].startswith("1. D1 `d` [start now] repairs damage"), lines)


if __name__ == "__main__":
    unittest.main()
