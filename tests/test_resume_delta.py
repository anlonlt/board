"""M5: a resume prints only what changed since the session's last one.

1. `kit.py resume` in a session that already ran one prints what changed since then (steps, sessions, questions, the
   owner's comments, other writers), or one line "nothing new since HH:MM"; `--full` prints it all.
2. The brief guard accepts that read as fresh (kit_brief_guard.py stamps any `kit.py resume` that succeeds).
3. The first resume of a session, a hand-over (a new generation) and a takeover (another session id) print it whole;
   a compaction clears the session's state (kit_session_start.py).

python3 -m unittest tests.test_resume_delta -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

KIT = str(ROOT / "scripts" / "kit.py")
HOOK_GUARD = str(ROOT / "kit" / "hooks" / "kit_brief_guard.py")
HOOK_START = str(ROOT / "kit" / "hooks" / "kit_session_start.py")
T0 = datetime(2026, 11, 26, 14, 2, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 26, 14, 20, tzinfo=timezone.utc)

POSTS = {
    "_orchestrator": {"title": "Orchestrator", "column": "info", "session_id": "s-orch", "created_at": "2026-11-26T10:00:00Z"},
    "m5-delta": {"part": "M5", "train": "memory", "title": "A resume prints only what changed", "column": "info",
                 "status": "in progress", "session_id": "s-m5", "created_at": "2026-11-26T10:00:00Z",
                 "updated_at": "2026-11-26T11:00:00Z"},
    "m6-ledger": {"part": "M6", "train": "memory", "title": "The usage ledger", "column": "info", "status": "planned",
                  "created_at": "2026-11-26T10:00:00Z"},
    "q-trunk": {"title": "Which trunk?", "kind": "question", "column": "question", "status": "open",
                "created_at": "2026-11-26T10:00:00Z"},
}
COMMENTS = {
    "c-his-old": {"from": "owner", "post_id": "m5-delta", "text": "Is this short enough?", "created_at": "2026-11-26T12:00:00Z"},
}


def make_db(path, posts, comments):
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, data TEXT, PRIMARY KEY (collection, doc_id))")
    for coll, docs in (("posts", posts), ("comments", comments)):
        for k, v in docs.items():
            con.execute("INSERT INTO docs VALUES (?, ?, ?)", (coll, k, json.dumps(v)))
    con.commit()
    con.close()


class Delta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.db = os.path.join(self.root, "board.db")
        os.makedirs(os.path.join(self.root, ".kit"))
        make_db(self.db, POSTS, COMMENTS)
        self.patches = [mock.patch.object(kit, "ROOT", self.root),
                        mock.patch.object(kit, "_attached_boards", return_value=([], []))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def delta(self, session="s-orch", role="orchestrator", full=False, now=T1, alive=frozenset()):
        return kit.resume_delta(None, role=role, session=session, db_path=self.db, cli="claude", full=full, now=now,
                                alive=set(alive))

    def change(self, posts=None, comments=None):
        p, c = json.loads(json.dumps(POSTS)), json.loads(json.dumps(COMMENTS))
        for k, v in (posts or {}).items():
            p[k] = dict(p.get(k) or {}, **v)
        c.update(comments or {})
        make_db(self.db, p, c)

    def test_first_resume_of_a_session_is_whole_the_second_says_nothing_new(self):
        self.assertIsNone(self.delta(now=T0))                    # the first: whole
        out = self.delta()
        self.assertEqual(out.strip().splitlines()[0][:32], "Resume: nothing new since 14:02 ")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("--full", out)

    def test_full_prints_it_whole(self):
        self.delta(now=T0)
        self.assertIsNone(self.delta(full=True))

    def test_each_kind_of_change_is_named_under_its_heading(self):
        self.delta(now=T0)
        self.change(posts={"m6-ledger": {"status": "in progress", "session_id": "s-m6"},
                           "q-trunk": {"status": "answered"},
                           "m7-new": {"part": "M7", "train": "memory", "title": "Every memory entry is checked",
                                      "status": "planned", "created_at": "2026-11-26T14:10:00Z"},
                           "_trains": {"title": "Trains", "updated_at": "2026-11-26T14:11:00Z", "from": "kit trains"}},
                    comments={"c-his-new": {"from": "owner", "post_id": "m5-delta", "text": "And with one change?",
                                            "created_at": "2026-11-26T14:12:00Z"},
                              "c-review": {"from": "reviewer", "post_id": "m6-ledger", "text": "Looks good",
                                           "created_at": "2026-11-26T14:13:00Z"}})
        out = self.delta(alive={"s-m6"})
        self.assertTrue(out.startswith("# Resume: what changed since 14:02 UTC"), out)
        for head in ("## Steps", "## Sessions", "## Questions", "## The owner's comments", "## Other writers"):
            self.assertIn(head, out)
        self.assertIn("M6 The usage ledger (`m6-ledger`): status planned -> in progress", out)
        self.assertIn("new: M7 Every memory entry is checked (`m7-new`), planned", out)
        self.assertIn("session none -> s-m6", out)
        self.assertIn("now running: s-m6", out)
        self.assertIn("status open -> answered", out)
        self.assertIn("c-his-new on m5-delta: And with one change?", out)
        self.assertIn("reviewer wrote 1 comment on m6-ledger (c-review)", out)
        self.assertIn("`_trains`", out)
        self.assertNotIn("m5-delta`):", out)                      # what did not change is not printed
        self.assertIn("Still unanswered of his (from before): c-his-old", out)

    def test_his_comment_answered_since_is_said(self):
        self.delta(now=T0)
        self.change(comments={"c-ans": {"from": "orchestrator", "post_id": "m5-delta", "reply_to": "c-his-old",
                                        "text": "Yes.", "created_at": "2026-11-26T14:05:00Z"}})
        out = self.delta()
        self.assertIn("answered since: c-his-old", out)

    def test_each_resume_is_the_new_reference(self):
        self.delta(now=T0)
        self.change(posts={"m6-ledger": {"status": "in progress"}})
        self.assertIn("m6-ledger", self.delta(now=T1))
        self.assertIn("nothing new since 14:20", self.delta(now=datetime(2026, 11, 26, 14, 30, tzinfo=timezone.utc)))

    def test_a_takeover_another_session_id_is_whole(self):
        self.delta(now=T0)
        self.assertIsNone(self.delta(session="s-successor"))

    def test_a_hand_over_is_whole_even_under_the_same_name(self):
        self.delta(session="proj-orchestrator", now=T0)
        with open(os.path.join(self.root, ".kit", "handover-orchestrator.gen"), "w") as fh:
            fh.write("2")
        self.assertIsNone(self.delta(session="proj-orchestrator"))
        self.assertIn("nothing new", self.delta(session="proj-orchestrator"))

    def test_no_session_id_every_resume_is_whole(self):
        self.assertIsNone(self.delta(session=None, now=T0))
        self.assertIsNone(self.delta(session=None))

    def test_a_part_or_review_resume_is_always_whole(self):
        for role in ("part", "review", "clerk"):
            self.assertIsNone(self.delta(role=role, now=T0))
            self.assertIsNone(self.delta(role=role))

    def test_the_compaction_clears_the_sessions_state(self):
        self.delta(session="s-orch", now=T0)
        self.delta(session="s-other", now=T0)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root)
        env.pop("KIT_ROLE", None)
        subprocess.run([sys.executable, "-I", HOOK_START], capture_output=True, text=True, env=env, timeout=60,
                       input=json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s-orch",
                                         "cwd": self.root}))
        self.assertIsNone(self.delta(session="s-orch"))          # whole again after the compaction
        self.assertIn("nothing new", self.delta(session="s-other"))


class EndToEnd(unittest.TestCase):
    """The command itself, and the guard reading it as fresh."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "board.db")
        make_db(self.db, POSTS, COMMENTS)
        self.sid = "test-m5-%d" % os.getpid()
        self.env = dict(os.environ, KIT_SESSION_ID=self.sid, KIT_ROLE="orchestrator")
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        for role in ("orchestrator",):
            p = kit.resume_state_path(self.sid, role)
            if os.path.exists(p):
                os.remove(p)
        self.tmp.cleanup()

    def run_kit(self, *extra):
        return subprocess.run([sys.executable, "-I", KIT, "resume", "--role", "orchestrator", "--db", self.db] + list(extra),
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_second_resume_is_one_line_and_full_is_whole(self):
        first = self.run_kit()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_kit()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("nothing new since", second.stdout)
        self.assertLess(len(second.stdout), 200)
        full = self.run_kit("--full")
        self.assertEqual(len(full.stdout.splitlines()) > 20, True, full.stdout[:500])
        self.assertGreater(len(full.stdout), 20 * len(second.stdout))

    def test_the_guard_accepts_a_delta_read_as_fresh(self):
        self.run_kit()
        second = self.run_kit()
        self.assertIn("nothing new since", second.stdout)
        with tempfile.TemporaryDirectory() as proj:
            os.makedirs(os.path.join(proj, ".kit"))
            with open(os.path.join(proj, ".kit", "config.json"), "w") as fh:
                json.dump({"kit": {"runtime": "claude"}}, fh)
            env = dict(os.environ, KIT_ROLE="orchestrator")
            env.pop("CLAUDE_PROJECT_DIR", None)

            def hook(payload):
                return subprocess.run([sys.executable, "-I", HOOK_GUARD], input=json.dumps(dict(payload, cwd=proj)),
                                      capture_output=True, text=True, timeout=30, env=env)
            reply = {"hook_event_name": "PreToolUse", "session_id": "s-orch", "tool_name": "ArtifactData",
                     "tool_input": {"action": "set", "collection": "comments", "doc_id": "o-x",
                                    "data": {"from": "orchestrator", "post_id": "m5-delta", "reply_to": "c-his-old",
                                             "text": "Yes."}}}
            self.assertEqual(hook(reply).returncode, 2)                  # no read yet: refused
            ran = {"hook_event_name": "PostToolUse", "session_id": "s-orch", "tool_name": "Bash",
                   "tool_input": {"command": "python3 -I .kit/kit.py resume --role orchestrator"},
                   "tool_response": {"stdout": second.stdout, "exit_code": 0}}
            self.assertEqual(hook(ran).returncode, 0)
            r = hook(reply)
            self.assertEqual(r.returncode, 0, r.stderr)                  # the delta read counts as fresh


if __name__ == "__main__":
    unittest.main()
