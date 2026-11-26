"""The split check (the owner, 2026-11-26: "isn't an orchestrator supposed to dynamically create posts that allow
concurrent sessions?"): CL44 grew to 12+ unrelated items in one part. `step new` refuses a step of many pieces or of
separate groups of files, names the groups, and takes --keep-whole "<why>" (recorded); the orchestrator's resume lists
open steps that are several steps.

Run via: python3 -m unittest tests.test_split_check -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import kit  # noqa: E402

CL44 = """Fix the agy gaps:

1. `scripts/kit.py` stale_copy: skip the gate locally.
2. `scripts/kit.py` and `kit/board/board_server.py`: empty board.db.
3. `install.py`, `kit/board/mcp_stdio.py`: worktree-safe MCP config.
4. `kit/usage.py` and `kit/hook.py`: agy usage rows.
5. `kit/up`: the mode flag.
6. Run the whole suite and one live check.
7. `scripts/device.py`: the gate rechecks.
"""
SMALL = """Fix the hook:

1. `kit/hook.py`: the reply agy reads.
2. `kit/hooks/kit_activity.py` and `kit/hook.py`: the loop line on PostInvocation.
3. Run the hook suite.
"""


class SplitCheck(unittest.TestCase):
    def test_unrelated_groups_are_flagged_with_the_groups(self):
        why = kit.split_check(kit.task_pieces(CL44))
        self.assertIn("separate groups", why)
        self.assertIn("pieces 7", why); self.assertIn("scripts/device.py", why)     # its own step
        self.assertNotIn("pieces 6", why)                                           # no file: the whole step's

    def test_a_focused_step_passes(self):
        self.assertEqual(kit.split_check(kit.task_pieces(SMALL)), "")

    def test_too_many_pieces_in_one_file_is_flagged(self):
        many = "\n".join("%d. `kit/hook.py`: fix %d." % (i, i) for i in range(1, 9))
        self.assertIn("8 pieces", kit.split_check(kit.task_pieces(many)))

    def test_a_hub_file_alone_does_not_join_everything(self):
        ps = [{"n": i, "title": "t%d" % i, "files": ["scripts/kit.py", f]} for i, f in
              enumerate(["a.py", "b.py", "c.py", "d.py"], 1)]
        groups, hub = kit.split_groups(ps)
        self.assertEqual(hub, {"scripts/kit.py"}); self.assertEqual(len(groups), 4)


class StepNew(unittest.TestCase):
    def setUp(self):
        self.wrote = []
        self.p = [mock.patch.object(kit, "_all_posts", return_value={}),
                  mock.patch.object(kit, "_board_write", side_effect=lambda *a: self.wrote.append(a) or True),
                  mock.patch.object(kit, "_write_step_prompt", return_value=("docs/prompts/x.md", True))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_refused_before_anything_is_written(self):
        r = kit.step_new("cli", "CL44", "agy gaps", task=CL44)
        self.assertTrue(r.get("split")); self.assertIn("--keep-whole", r["error"]); self.assertEqual(self.wrote, [])

    def test_kept_whole_with_a_reason_is_recorded(self):
        r = kit.step_new("cli", "CL44", "agy gaps", task=CL44, keep_whole="one live run tests them all")
        self.assertNotIn("error", r)
        self.assertEqual(self.wrote[0][3]["kept_whole"], "one live run tests them all")

    def test_a_focused_step_is_written(self):
        self.assertNotIn("error", kit.step_new("cli", "CL45", "hook reply", task=SMALL))


class OrchestratorSees(unittest.TestCase):
    def test_open_big_steps_are_listed_done_or_kept_ones_not(self):
        ps = kit.task_pieces(CL44)
        posts = {"cl44": {"part": "CL44", "status": "in progress", "subtasks": ps},
                 "cl40": {"part": "CL40", "status": "complete", "subtasks": ps},
                 "cl41": {"part": "CL41", "status": "planned", "subtasks": ps, "kept_whole": "why"}}
        out = "\n".join(kit.split_lines(posts))
        self.assertIn("`cl44`", out); self.assertNotIn("cl40", out); self.assertNotIn("cl41", out)


if __name__ == "__main__":
    unittest.main()
