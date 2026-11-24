#!/usr/bin/env python3
"""CL33: `kit.py compact` posts every open awaits point and every open issue on the board before it archives.

The owner, 2026-11-23: "wasn't the point that things like awaits and issues get hidden in GitHub, forgotten, and pile
up, whereas if they were posts, they could be treated as they come". CL29's compact kept Harmonize's 31 open awaits
points in memory because they were not on the board. Now compact
  1. makes one question post per open awaits point (its text, its workstream as part and train, its default, its
     deadline; an OVERDUE point says the default applies and where), never doubled on a second run;
  2. makes one issue post per open issue not yet posted, matched with the issue posts already there, never doubled,
     and marks the issue file `Status: closed — <date>, posted as board post <id>`;
  3. then archives the awaits file whole and every state whose open work is now on the board (a state whose next
     step is not settled still stays), the project's memory checker green on the result;
  4. on an Artifact board the posts are only in the local copy until `kit.py artifact push` sends them: nothing is
     archived until they are sent;
  5. a dry run lists the planned posts and writes nothing, on the board or in the tree.

python3 -m unittest tests.test_cl33_compact_posts -v
"""
import os
import shutil
import tempfile
import unittest

from tests.test_cl29_compact_checker import AWAITS, load_kit, run_checker, snapshot, tree


class FakeBoard:
    """The board compact writes to: posts by id; `pending` ids are in the local copy but not yet sent."""

    def __init__(self, posts=None, artifact=False):
        self.docs = dict(posts or {})
        self.writes = []
        self.artifact = artifact
        self.pending = set()

    def posts(self):
        return dict(self.docs)

    def write(self, pid, doc):
        self.writes.append(pid)
        self.docs[pid] = doc
        if self.artifact:
            self.pending.add(pid)
        return True

    def sent(self, pid):
        return pid not in self.pending

    def push_hint(self):
        return "python3 -I .kit/kit.py artifact push" if self.artifact else ""


def questions(board):
    return {k: v for k, v in board.docs.items() if v.get("kind") == "question"}


def issues(board):
    return {k: v for k, v in board.docs.items() if v.get("kind") == "issue"}


class O54Gaps(unittest.TestCase):
    """O54's Harmonize run (2026-11-24): 2 of 31 points doubled, default not shown, workstream as part made 12
    pseudo-trains and false 'blocks', every post 0 min old."""

    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.mem = tree(self.d)

    def write_awaits(self, text):
        with open(os.path.join(self.mem, "awaits-anlon.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_point_naming_a_post_id_is_matched(self):
        self.write_awaits("- [app] The harmony AI: answer on the board `HL5-harmony-ai-question`. Default: wait "
                          "· deadline 2099-12-31\n")
        b = FakeBoard({"HL5-harmony-ai-question": {"column": "question", "title": "Which model for harmony?"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_point_with_a_close_title_is_matched(self):
        self.write_awaits("- [app] The aids in the night: keep them on? Default: on · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-in-the-night": {"column": "question", "part": "F5", "title": "Aids in the night"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_unrelated_question_is_not_matched(self):
        self.write_awaits("- [app] Pick the colour of the play button. Default: blue · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-in-the-night": {"column": "question", "part": "F5", "title": "Aids in the night"}})
        self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len([w for w in b.writes if w.startswith("aw-")]), 1)

    def test_default_is_the_one_tap_answer(self):
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        for v in questions(b).values():
            self.assertEqual(v["default_option"], v["default"])
            self.assertIn(v["default"], v["options"])

    def test_workstream_is_not_a_part_or_train(self):
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        for pid, v in questions(b).items():
            self.assertNotIn(v.get("part"), ("app", "sync", "orchestrator"))
            self.assertFalse(v.get("train"), v)
            self.assertEqual(self.kit._train_key(pid, v), "")
        # resume: an app question no longer 'blocks' every post with the word app in it
        posts = dict(b.docs)
        posts["HL5-x"] = {"part": "HL5", "column": "info", "status": "in progress", "title": "the app screen"}
        out = "\n".join(self.kit._resume_extras(posts, {}))
        self.assertNotIn("blocks HL5", out)

    def test_post_keeps_the_point_date(self):
        import subprocess
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-11-02T10:00:00Z", GIT_COMMITTER_DATE="2026-11-02T10:00:00Z")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", self.d] + c, check=True, env=env)
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        for v in questions(b).values():
            self.assertTrue(v["created_at"].startswith("2026-11-02"), v["created_at"])


class CompactPostsOpenWork(unittest.TestCase):
    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.mem = tree(self.d)

    def test_one_question_post_per_awaits_point(self):
        b = FakeBoard()
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        q = questions(b)
        self.assertEqual(len(q), 4, out)
        by_ws = sorted(v["workstream"] for v in q.values())
        self.assertEqual(by_ws, ["app", "app", "orchestrator", "sync"])
        for v in q.values():
            self.assertEqual(v["column"], "question")
            self.assertTrue(v.get("default"), v)
            self.assertRegex(v.get("deadline", ""), r"^\d{4}-\d\d-\d\d$")
        sync = next(v for v in q.values() if v["workstream"] == "sync")
        self.assertEqual(sync["default"], "yes it costs nothing")  # the continuation line is part of the point
        self.assertIn("Keep the cloud copy?", sync["title"])

    def test_overdue_point_says_the_default_applies_and_where(self):
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        over = [v for v in questions(b).values() if v.get("overdue")]
        self.assertEqual(len(over), 1)
        self.assertIn("OVERDUE", over[0]["body"])
        self.assertIn("the default applies", over[0]["body"])
        self.assertIn("skip", over[0]["body"])
        self.assertIn(".story/state/app.md", over[0]["body"])
        self.assertFalse([v for v in questions(b).values() if v is not over[0] and "OVERDUE" in v["body"]])

    def test_open_issue_posted_once_and_closed_in_memory(self):
        b = FakeBoard()
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len(issues(b)), 1, out)
        (pid, doc), = issues(b).items()
        self.assertEqual(doc.get("status"), "open")
        self.assertIn("open one", doc["title"])
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "archive", "issues", "2026-11-05-open.md")), out)
        text = open(os.path.join(self.mem, "archive", "issues", "2026-11-05-open.md"), encoding="utf-8").read()
        self.assertRegex(text.split("\n")[1], r"^Status: closed — \d{4}-\d\d-\d\d, posted as board post %s" % pid)

    def test_issue_already_on_the_board_is_not_doubled(self):
        b = FakeBoard({"iss-open-one": {"kind": "issue", "status": "open", "title": "Open one",
                                        "body": "seen twice"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(list(issues(b)), ["iss-open-one"], out)
        self.assertIn("0 doubled", out)
        text = open(os.path.join(self.mem, "archive", "issues", "2026-11-05-open.md"), encoding="utf-8").read()
        self.assertIn("posted as board post iss-open-one", text)

    def test_second_run_doubles_nothing(self):
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        n = len(b.docs)
        # the same points written again (a restored file) are matched with their posts
        with open(os.path.join(self.mem, "awaits-anlon.md"), "w", encoding="utf-8") as fh:
            fh.write(AWAITS)
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len(b.docs), n, out)

    def test_awaits_and_freed_states_archived_checker_green(self):
        b = FakeBoard()
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertFalse(os.path.exists(os.path.join(self.mem, "awaits-anlon.md")), out)
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "archive", "awaits-anlon.md")))
        left = sorted(os.listdir(os.path.join(self.mem, "state")))
        # app and sync were kept only by their points (their next step is not "done"); now on the board they still
        # stay because their next step is open work no post holds; wip likewise; orchestrator always stays
        self.assertEqual(left, ["app.md", "orchestrator.md", "sync.md", "wip.md"], out)
        r = run_checker(self.d)
        self.assertEqual(r.returncode, 0, out + "\n" + r.stdout)
        self.assertIn("STORY: OK", out)

    def test_state_whose_only_open_work_was_its_points_is_archived(self):
        with open(os.path.join(self.mem, "state", "sync.md"), "w", encoding="utf-8") as fh:
            fh.write("# State — sync\n\n## Where it stands\nIt runs.\n\n## Next step\nDone — waits on the owner's "
                     "answer only.\n\n## Recent decisions and why\n- none\n\n## Open\n- nothing\n")
        b = FakeBoard()
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "archive", "state", "sync.md")), out)
        self.assertEqual(run_checker(self.d).returncode, 0)

    def test_artifact_board_archives_nothing_until_pushed(self):
        b = FakeBoard(artifact=True)
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len(questions(b)), 4)
        self.assertEqual(snapshot(self.d), before, out)
        self.assertIn("artifact push", out)
        b.pending.clear()  # the batches went out and were recorded sent
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len(questions(b)), 4, out)
        self.assertFalse(os.path.exists(os.path.join(self.mem, "awaits-anlon.md")), out)

    def test_dry_run_lists_the_posts_and_writes_nothing(self):
        b = FakeBoard()
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d, board=b, dry_run=True)
        self.assertEqual(b.writes, [])
        self.assertEqual(snapshot(self.d), before)
        self.assertIn("4 question post(s) planned", out)
        self.assertIn("1 issue post(s) planned, 0 doubled", out)
        self.assertIn("[sync] Keep the cloud copy?", out)
        self.assertIn("OVERDUE", out)
        self.assertIn("STORY: OK", out)

    def test_no_board_keeps_cl29_behaviour(self):
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertIn("4 open awaits point(s) stay", out)
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "awaits-anlon.md")))


if __name__ == "__main__":
    unittest.main()
