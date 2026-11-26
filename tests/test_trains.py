"""A train is one goal (the owner, 2026-11-26: "isn't the train kind of long?" - `cli` held 46 steps of a dozen goals).
`kit.py trains new <id> --goal` writes its record (`_train-<id>`); `trains close` stops new steps on it; `step new`
refuses a closed train, a train with no record and no step yet, and a train at TRAIN_MAX_STEPS; the orchestrator's
resume names trains past their size or age and steps "in progress" nobody touched for a day.

Run via: python3 -m unittest tests.test_trains -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import kit  # noqa: E402

NOW = "2026-11-26T12:00:00Z"


def steps(train, n, status="complete", day="2026-11-25"):
    return {"%s-%d" % (train, i): {"part": "S%d" % i, "train": train, "status": status, "created_at": day + "T00:00:%02dZ" % i}
            for i in range(n)}


class Board:
    def __init__(self, posts):
        self.posts = dict(posts)

    def __enter__(self):
        self.p = [mock.patch.object(kit, "_all_posts", side_effect=lambda *a, **k: dict(self.posts)),
                  mock.patch.object(kit, "_board_write", side_effect=self.write),
                  mock.patch.object(kit, "_write_step_prompt", return_value=("docs/prompts/x.md", True))]
        for x in self.p:
            x.start()
        return self

    def write(self, cmd, coll, did, data, env=None):
        self.posts[did] = data
        return True

    def __exit__(self, *a):
        for x in self.p:
            x.stop()


class Trains(unittest.TestCase):
    def setUp(self):
        del kit.BOARD_ERRORS[:]

    def test_a_new_train_needs_its_goal_first(self):
        with Board({}) as b:
            r = kit.step_new("agy", "A1", "x", task="one thing")
            self.assertIn("trains new agy --goal", r["error"])
            self.assertTrue(kit.trains_new("agy", "agy runs the kit as Claude Code does")["ok"])
            r = kit.step_new("agy", "A1", "x", task="one thing")
            self.assertNotIn("error", r); self.assertEqual(r["train_goal"], "agy runs the kit as Claude Code does")
            self.assertIn("error", kit.trains_new("agy", "again"))          # one record per train
            self.assertIn("error", kit.trains_new("x", ""))                 # no goal, no train

    def test_a_closed_train_takes_no_step_and_keeps_its_open_ones(self):
        with Board(dict(steps("cli", 3), **{"cli-open": {"part": "CL9", "train": "cli", "status": "in progress"}})) as b:
            r = kit.trains_close("cli", "its goal is met")
            self.assertEqual(r["open_steps_kept"], ["CL9"]); self.assertEqual(b.posts["cli-open"]["train"], "cli")
            self.assertIn("is closed (its goal is met)", kit.step_new("cli", "CL50", "x", task="t")["error"])

    def test_a_full_train_is_refused_unless_kept_with_a_reason(self):
        with Board(steps("cli", kit.TRAIN_MAX_STEPS)) as b:
            self.assertTrue(kit.step_new("cli", "CL50", "x", task="t").get("train_full"))
            r = kit.step_new("cli", "CL50", "x", task="t", keep_train="the last piece of this goal")
            self.assertNotIn("error", r); self.assertEqual(b.posts[r["post"]]["kept_on_train"], "the last piece of this goal")

    def test_an_unreadable_board_is_an_error_not_an_empty_one(self):
        kit.BOARD_ERRORS.append("the board server refused or failed: HTTP Error 401")
        with Board({}):
            self.assertIn("401", kit.trains_new("agy", "g")["error"])
            self.assertIn("401", kit.step_new("agy", "A1", "x", task="t")["error"])


class OrchestratorSees(unittest.TestCase):
    def test_long_old_trains_and_untouched_steps_are_named(self):
        posts = dict(steps("cli", 14, day="2026-11-20"))
        posts["cli-live"] = {"part": "CL8", "train": "cli", "status": "in progress", "created_at": "2026-11-20T00:00:00Z",
                             "updated_at": "2026-11-22T00:00:00Z"}
        posts.update(steps("demo", 3))
        posts["demo-now"] = {"part": "D9", "train": "demo", "status": "in progress", "updated_at": "2026-11-26T11:00:00Z"}
        out = "\n".join(kit.train_lines(posts, now=NOW))
        self.assertIn("train `cli`: 15 steps", out); self.assertNotIn("train `demo`", out)
        self.assertIn("`cli-live` (CL8)", out); self.assertNotIn("demo-now", out)

    def test_a_closed_train_is_not_named_again(self):
        posts = dict(steps("cli", 14, day="2026-11-20"), **{"_train-cli": {"kind": "train", "train": "cli", "status": "closed"}})
        self.assertNotIn("train `cli`", "\n".join(kit.train_lines(posts, now=NOW)))


if __name__ == "__main__":
    unittest.main()
