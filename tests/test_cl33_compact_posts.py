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
import re
import shutil
import tempfile
import unittest

from tests.test_cl29_compact_checker import AWAITS, load_kit, run_checker, snapshot, tree


class FakeBoard:
    """The board compact writes to: posts by id; `pending` ids are in the local copy but not yet sent."""

    def __init__(self, posts=None, artifact=False):
        self.docs = dict(posts or {})
        self.comments = {}
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

    def comment(self, cid, doc):
        self.comments[cid] = doc
        if self.artifact:
            self.pending.add(cid)
        return True

    def has_comment(self, cid):
        return cid in self.comments

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
        self.write_awaits("- [app] The harmony AI: answer on the board `MX5-soil-mix-question`. Default: wait "
                          "· deadline 2099-12-31\n")
        b = FakeBoard({"MX5-soil-mix-question": {"column": "question", "title": "Which model for harmony?"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_point_with_a_close_title_is_matched(self):
        self.write_awaits("- [app] The aids in the night: keep them on? Default: on · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-at-night": {"column": "question", "part": "F5", "title": "Aids in the night"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_unrelated_question_is_not_matched(self):
        self.write_awaits("- [app] Pick the colour of the play button. Default: blue · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-at-night": {"column": "question", "part": "F5", "title": "Aids in the night"}})
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

    def test_named_id_of_a_missing_post_matches_the_question_by_its_words(self):
        # O54 rerun: the point names `q-look-aids-in-the-night` (no such post); F5's question is that one
        self.write_awaits("- [docs] Look: the aids in the night, do they stay on the docs screen while the score "
                          "plays and the owner scrolls back (asked under `q-look-aids-in-the-night`)? Default: they "
                          "stay · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-at-night": {"column": "question", "part": "F5",
                                                "title": "Aids in the night: keep them?"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_named_step_or_report_post_is_never_the_match(self):
        # O54 rerun: 9 points named step posts (done/info) and were archived with no question post: lost
        self.write_awaits("- [app] Ship V6b's screen to the phone? See `V6b-phone-screen`. Default: yes "
                          "· deadline 2099-12-31\n"
                          "- [app] Report Z4: keep the sampler? Default: keep · deadline 2099-12-31\n")
        b = FakeBoard({"V6b-phone-screen": {"column": "done", "part": "V6b", "title": "Phone screen", "status": "done"},
                       "Z4-sampler": {"column": "info", "part": "Z4", "title": "Report Z4: keep the sampler?",
                                      "kind": "report"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len([w for w in b.writes if w.startswith("aw-")]), 2, out)

    def test_answered_question_named_by_the_point_is_not_the_match(self):
        self.write_awaits("- [app] Colour, see `Q1-colour`. Default: blue · deadline 2099-12-31\n")
        b = FakeBoard({"Q1-colour": {"column": "done", "kind": "question", "title": "Colour"}})
        self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual(len([w for w in b.writes if w.startswith("aw-")]), 1)

    def test_shallow_clone_never_dates_a_point_at_its_cut(self):
        import subprocess
        src = os.path.join(self.d, "src")
        os.makedirs(src)
        shutil.move(self.mem, os.path.join(src, ".story"))
        shutil.move(os.path.join(self.d, ".claude"), os.path.join(src, ".claude"))
        def commit(date, msg):
            env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            subprocess.run(["git", "-C", src, "add", "-A"], check=True)
            subprocess.run(["git", "-C", src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg],
                           check=True, env=env)
        subprocess.run(["git", "-C", src, "init", "-q"], check=True)
        commit("2026-11-02T10:00:00Z", "points")
        with open(os.path.join(src, "README.md"), "w") as fh:
            fh.write("x\n")
        commit("2026-11-23T08:04:00Z", "later")
        clone = os.path.join(self.d, "clone")
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + src, clone], check=True)
        with open(os.path.join(clone, ".story", "awaits-anlon.md"), "a", encoding="utf-8") as fh:
            fh.write("- [app] Since 2026-11-10, the tempo. Default: 90 · deadline 2099-12-31\n")
        b = FakeBoard()
        self.kit.compact_memory(root_dir=clone, board=b)
        dates = {v["title"]: v["created_at"] for v in questions(b).values()}
        self.assertFalse([t for t, c in dates.items() if c.startswith("2026-11-23")], dates)
        tempo = next(v for v in questions(b).values() if v["title"].startswith("Since 2026-11-10"))
        self.assertTrue(tempo.get("date_unknown"), tempo)  # never guessed from a date in the text (O54)

    def test_default_without_its_trailing_reference_and_title_not_cut_mid_parenthesis(self):
        long = "Choose the night layout " + "with many words " * 9 + "(the one in the docs screen, long note)"
        self.write_awaits("- [app] %s. Default: the dark one — `docs/WORKPLAN.md` § C1 item 5 · deadline 2099-12-31\n"
                          % long)
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        (v,) = questions(b).values()
        self.assertEqual(v["default_option"], "the dark one")
        self.assertEqual(v["title"].count("("), v["title"].count(")"), v["title"])

    def test_bare_named_id_without_digit_matches(self):
        # O54, third check: the real point names q-look-aids-in-the-night bare, no backticks, no digit
        self.write_awaits("- [docs] Look at the aids in the night on the docs screen, then say if they stay; asked "
                          "on the board as q-look-aids-in-the-night. Default: they stay · deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-at-night": {"column": "question", "part": "F5",
                                                "title": "Aids in the night: keep them?"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertEqual([w for w in b.writes if w.startswith("aw-")], [], out)

    def test_words_like_you_and_keep_alone_are_no_double(self):
        self.write_awaits("- [app] The heard card: do you keep it under the score? Default: keep · "
                          "deadline 2099-12-31\n")
        b = FakeBoard({"F5-aids-at-night": {"column": "question", "part": "F5",
                                                "title": "Aids in the night: do you keep them?"}})
        out = self.kit.compact_memory(root_dir=self.d, board=b, dry_run=True)
        self.assertIn("0 possible double(s)", out)

    def test_every_reference_stays_in_the_body(self):
        self.write_awaits("- [app] Open the window, T4-s1-write-gate decides. Default: open it — board E11-window "
                          "· deadline 2099-12-31\n"
                          "- [app] Held vibrato. Default: off — step `s1-held-vibrato`, board Z4-speaker "
                          "· deadline 2099-12-31\n"
                          "- [app] Layout. Default: dark — `docs/WORKPLAN.md` § C1 item 5 · deadline 2099-12-31\n")
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)
        q = {v["title"]: v for v in questions(b).values()}
        self.assertEqual(sorted(v["default_option"] for v in q.values()), ["dark", "off", "open it"])
        self.assertIn("board E11-window", q["Open the window, T4-s1-write-gate decides."]["body"])
        self.assertIn("T4-s1-write-gate", q["Open the window, T4-s1-write-gate decides."]["body"])
        self.assertIn("step `s1-held-vibrato`, board Z4-speaker", q["Held vibrato."]["body"])
        self.assertIn("`docs/WORKPLAN.md` § C1 item 5", q["Layout."]["body"])

    def test_no_reliable_date_says_date_unknown(self):
        b = FakeBoard()
        self.kit.compact_memory(root_dir=self.d, board=b)   # not a git repo: no blame date
        for pid, v in questions(b).items():
            self.assertTrue(v.get("date_unknown"), v)
        out = "\n".join(self.kit._resume_extras(dict(b.docs), {}))
        self.assertIn("date unknown", out)
        self.assertNotIn("0min old", out)


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
        self.assertIn("0 possible double(s)", out)
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
        self.assertIn("1 issue post(s) planned", out)
        self.assertIn("0 possible double(s)", out)
        self.assertIn("[sync] Keep the cloud copy?", out)
        self.assertIn("OVERDUE", out)
        self.assertIn("STORY: OK", out)

    def test_no_board_keeps_cl29_behaviour(self):
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertIn("4 open awaits point(s) stay", out)
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "awaits-anlon.md")))


LONG_AWAITS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cl33_awaits.md")
# invented, with the shapes of a real list and board: question posts (kind unset) and the step posts its points name
LONG_QUESTIONS = {
    "F5-aids-at-night": {"title": "The greenhouse at night: which do you keep?", "column": "question",
                         "status": "open", "created_at": "2026-11-12T08:05:00Z"},
    "MX5-soil-mix-question": {"title": "The soil mix: the app's recipe with the bed's acidity?", "column": "question",
                              "status": None, "default_option": "Yes: the app's recipe, the model stays stored",
                              "created_at": "2026-11-19T16:33:43Z"},
    "R2-training-plan": {"title": "R2 · The training plan: mix, roots, a two-row reader, rented time, and who does "
                                  "what", "column": "question", "status": "done", "created_at": "2026-11-17T03:25:00Z"},
    "VQ2-two-row-reader-train": {"title": "VQ2 · Train the two-row reader on rented time: about $5.8, at most $8?",
                                 "column": "question", "status": "done step A; priced, waiting your tap",
                                 "created_at": "2026-11-17T19:01:25Z"},
    "VQ5-second-run-question": {"title": "The second reader run: buy nothing now, and MX6 runs now?",
                                "column": "question", "status": None, "created_at": "2026-11-19T23:53:03Z"},
}
LONG_STEPS = ["N6b-nozzle-fixes", "Q4-bench", "E31-window", "S38c-doubt-marks", "EX7-gesture-map-built",
              "PQ2-plots-on-beds", "T4-s1-plant-gate", "PQ1-find-the-row", "B29-eye-three-edges"]


class LongAwaits(unittest.TestCase):
    """A long list (31 points) against a board that holds two of them as questions already."""

    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.mem = tree(self.d, checker=False)   # its cited docs/ paths are not in this fixture
        shutil.copy(LONG_AWAITS, os.path.join(self.mem, "awaits-anlon.md"))
        posts = {k: dict(v) for k, v in LONG_QUESTIONS.items()}
        for i, k in enumerate(LONG_STEPS):
            posts[k] = {"title": k, "part": k.split("-")[0], "column": "done" if i % 2 else "info",
                        "status": "done" if i % 2 else "in progress"}
        self.board = FakeBoard(posts)

    def test_29_posted_2_matched_0_possible_doubles(self):
        out = self.kit.compact_memory(root_dir=self.d, board=self.board, dry_run=True)
        self.assertIn("29 question post(s) planned", out)
        self.assertIn("2 already on the board (matched, not posted again: ", out)
        self.assertIn("F5-aids-at-night", out)
        self.assertIn("MX5-soil-mix-question", out)
        self.assertIn("0 possible double(s)", out)

    def test_defaults_clean_and_every_reference_in_the_body(self):
        self.kit.compact_memory(root_dir=self.d, board=self.board)
        q = questions(self.board)
        self.assertEqual(len(q), 29)
        for v in q.values():
            self.assertNotIn("\u2014", v["default_option"], v["default_option"])
            self.assertIn("Reference: ", v["body"], v["title"])
            self.assertTrue(v.get("date_unknown"))
        refs = " ".join(v["body"] for v in q.values())
        for r in ("`docs/PLAN.md` § C1 item 5", "board E31-window", "step `s1-held-pulse`, board Q4-bench",
                  "board T4-s1-plant-gate", "board B29-eye-three-edges"):
            self.assertIn(r, refs)


class O54FourthCheck(unittest.TestCase):
    """O54's fourth check (58056f9 passes on Harmonize): the page ignores date_unknown; a matched point's reference,
    default and deadline end up on no post; resume prints each aw- id twice; push's link lines name no post."""

    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.mem = tree(self.d, checker=False)
        shutil.copy(LONG_AWAITS, os.path.join(self.mem, "awaits-anlon.md"))
        self.board = FakeBoard({k: dict(v) for k, v in LONG_QUESTIONS.items()})

    def test_matched_point_leaves_one_comment_under_its_post(self):
        self.kit.compact_memory(root_dir=self.d, board=self.board)
        on = {c["post_id"]: c for c in self.board.comments.values()}
        self.assertEqual(sorted(on), ["F5-aids-at-night", "MX5-soil-mix-question"])
        f5 = on["F5-aids-at-night"]["text"]
        for x in ("`NightLooks`, q-look-aids-at-night", "your fallbacks, the needle and the columns", "2026-11-30"):
            self.assertIn(x, f5)
        self.assertEqual(self.board.docs["F5-aids-at-night"], LONG_QUESTIONS["F5-aids-at-night"])  # body untouched
        n = len(self.board.comments)
        shutil.copy(LONG_AWAITS, os.path.join(self.mem, "awaits-anlon.md"))
        self.kit.compact_memory(root_dir=self.d, board=self.board)
        self.assertEqual(len(self.board.comments), n)

    def test_unsent_comment_holds_the_archive(self):
        b = FakeBoard({k: dict(v) for k, v in LONG_QUESTIONS.items()}, artifact=True)
        self.kit.compact_memory(root_dir=self.d, board=b)
        b.pending = {x for x in b.pending if x in b.comments}   # posts sent, comments not
        self.kit.compact_memory(root_dir=self.d, board=b)
        self.assertTrue(os.path.isfile(os.path.join(self.mem, "awaits-anlon.md")))

    def test_resume_names_an_aw_post_once(self):
        self.kit.compact_memory(root_dir=self.d, board=self.board)
        out = "\n".join(self.kit._resume_extras(dict(self.board.docs), {}))
        self.assertNotRegex(out, r"- (aw-[\w-]+) `\1`")
        self.assertIn("`aw-", out)

    def test_page_shows_date_unknown_and_no_waiting_count(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        page = open(os.path.join(root, "kit", "board", "board.template.html"), encoding="utf-8").read()
        self.assertFalse("when(p.created_at)" in page, "a card still dates a post by created_at alone")
        self.assertTrue("date unknown" in page)
        self.assertTrue(re.search(r"!p\.date_unknown[^\n]*p\.created_at\)[^\n]*\{\s*\n\s*var ageH", page),
                        "the waiting count still counts a post whose date is unknown")

    def test_push_link_lines_name_their_post_under_a_heading(self):
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("kas33", os.path.join(root, "kit", "claude", "kit_artifact_sync.py"))
        kas = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kas)
        txt = kas.plan_text({"upload": [], "mirror": [], "links": [["pipeline/x.py", "the board's viewers do not open .py "
                                                                    "files", ["aw-chords-billboard"]]]}, "u", "/o", "")
        lines = txt.strip().split("\n")
        self.assertFalse(lines[0].startswith("- "), txt)
        self.assertIn("post aw-chords-billboard", txt)


if __name__ == "__main__":
    unittest.main()
