#!/usr/bin/env python3
"""CL28: the orchestrator answers only from a fresh brief.

  1. `kit.py brief`: open decisions (age, default, what each blocks), the owner's decisions of the last days, each train from
     its posts, and the memory lines a post contradicts (Harmonize, 11-23: "A3 waits on your choice" while A3 was over).
  2. kit_brief_guard.py: an orchestrator's reply to the owner is refused without a brief in the last 15 minutes.
  3. The Trains card (kit_cards.py): a done post is never running; held and closed show as such.
python3 -m unittest tests.test_cl28_brief -v
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "scripts", "kit.py")
HOOK = os.path.join(ROOT, "kit", "hooks", "kit_brief_guard.py")
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("kit_cards_cl28", os.path.join(ROOT, "kit", "board", "kit_cards.py"))
kit_cards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit_cards)

NOW = datetime.now(timezone.utc)


def ago(**kw):
    return (NOW - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Harmonize's board on 2026-11-23, reduced to the posts that misled orchestrator 51
POSTS = {
    "HL1-scoreboard-and-audit": {"part": "HL1", "column": "info", "status": "done", "title": "HL1 scoreboard",
                                 "created_at": ago(days=6), "updated_at": ago(days=6)},
    "HL3-harmony-model": {"part": "HL3", "parent": "HL1", "column": "info", "status": "in progress",
                          "title": "HL3 model", "created_at": ago(days=5)},
    "HL5-harmony-ai-question": {"part": "HL5", "column": "question", "title": "The harmony AI: the app's line?",
                                "default_option": "Yes: the app's line", "created_at": ago(days=3, hours=15)},
    "HL6-retrain-all": {"part": "HL6", "parent": "VR3", "column": "info", "status": "held: waits for VR5",
                        "title": "HL6 retrain", "created_at": ago(days=3, hours=9)},
    "VR3-reader-verdict": {"part": "VR3", "column": "done", "status": "done", "title": "VR3", "created_at": ago(days=5)},
    "VR5-second-run-question": {"part": "VR5", "column": "question", "title": "The second reader run?",
                                "default_option": "Buy nothing now; HL6 runs now", "created_at": ago(days=3, hours=7)},
    "A3-data": {"part": "A3-data", "column": "info", "status": "done", "title": "A3-data", "created_at": ago(days=9),
                "updated_at": ago(days=7)},
    "A3-recipe": {"part": "A3", "column": "done", "title": "A3 recipe", "created_at": ago(days=9)},
    "K3i-switch": {"part": "K3i", "column": "info", "status": "closed: superseded by K3j", "title": "K3i",
                   "created_at": ago(days=4)},
    "_orchestrator": {"title": "orchestrator", "column": "info", "session_id": "session_old"},
}
COMMENTS = {
    "c-old": {"from": "owner", "post_id": "_orchestrator", "text": "an old one", "created_at": ago(days=9)},
    "c-new": {"from": "owner", "post_id": "_orchestrator", "text": "I thought we had already decided to close that train.",
              "created_at": ago(hours=2)},
    "c-like": {"from": "owner", "post_id": "HL3-harmony-model", "like": True, "comment_id": "r1", "created_at": ago(hours=5)},
    "c-orch": {"from": "orchestrator", "post_id": "_orchestrator", "text": "an answer", "created_at": ago(hours=1)},
}


def write_board(d):
    for coll, docs in (("posts", POSTS), ("comments", COMMENTS)):
        os.makedirs(os.path.join(d, coll), exist_ok=True)
        for k, v in docs.items():
            with open(os.path.join(d, coll, k + ".json"), "w", encoding="utf-8") as fh:
                json.dump(v, fh)


class Brief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = os.path.join(self.tmp.name, "board")
        write_board(self.board)
        self.mem = os.path.join(self.tmp.name, "mem")
        os.makedirs(os.path.join(self.mem, "state"))
        with open(os.path.join(self.mem, "state", "speaker.md"), "w") as fh:
            fh.write("# Speaker\nNext: A3 waits on your choice: Hold (default) or Run anyway.\n"
                     "## Steps\n- [ ] A3 run anyway\n")
        with open(os.path.join(self.mem, "state", "harmony.md"), "w") as fh:
            fh.write("# Harmony\nNext: HL5 is done, nothing waits.\nHL3 runs (in progress).\n")
        r = subprocess.run([sys.executable, "-I", KIT, "brief", "--board", self.board, "--session", "test-cl28"],
                           capture_output=True, text=True, timeout=60, cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.out = r.stdout

    def tearDown(self):
        self.tmp.cleanup()

    def section(self, title):
        part = self.out.split("## " + title, 1)[1]
        return part.split("\n## ", 1)[0]

    def test_open_decisions_with_age_default_and_what_they_block(self):
        s = self.section("Open decisions")
        self.assertIn("(2)", s.splitlines()[0])
        self.assertIn("VR5-second-run-question", s)
        self.assertIn("HL5-harmony-ai-question", s)
        self.assertIn("3d 7h old", s)
        self.assertIn("default: Buy nothing now; HL6 runs now", s)
        vr5 = [l for l in s.splitlines() if "VR5" in l][0]
        self.assertIn("blocks HL6 (held: waits for VR5)", vr5)

    def test_his_decisions_of_the_last_days_only(self):
        s = self.section("The owner's decisions")
        self.assertIn("already decided to close that train", s)
        self.assertIn("Like", s)
        self.assertNotIn("an old one", s)
        self.assertNotIn("an answer", s)

    def test_trains_from_the_posts(self):
        s = self.section("Trains")
        hl = [l for l in s.splitlines() if l.startswith("- HL:")][0]
        self.assertIn("1/4 done", hl)
        self.assertIn("running: HL3", hl)
        self.assertIn("you: HL5", hl)
        self.assertIn("held: HL6", hl)
        self.assertNotIn("HL1", hl)  # HL1's post says done: never running
        self.assertIn("all done:", s)
        self.assertIn("A", s.split("all done:")[1])

    def test_reads_the_board_only(self):
        # the owner, 07:41: "everything is on the board": a memory file is never read nor compared
        self.assertNotIn("Memory", self.out)
        self.assertNotIn("speaker.md", self.out)

    def test_a_stale_orchestrator_id_is_flagged(self):
        self.assertIn("MISMATCH: `_orchestrator` names session session_old, you are test-cl28", self.out)

    def test_short(self):
        self.assertLess(len(self.out), 2500)


def hook(payload, proj, role="orchestrator"):
    env = dict(os.environ)
    env.pop("KIT_ROLE", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if role:
        env["KIT_ROLE"] = role
    payload = dict(payload, cwd=proj)
    return subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), capture_output=True, text=True,
                          timeout=30, env=env)


REPLY = {"hook_event_name": "PreToolUse", "session_id": "s-orch", "tool_name": "ArtifactData",
         "tool_input": {"action": "set", "collection": "comments", "doc_id": "o52-x",
                        "data": {"from": "orchestrator", "post_id": "_orchestrator", "reply_to": "92qwl45c30j2fyzbr3uc",
                                 "text": "A3 waits on your choice."}}}
BRIEF_RAN = {"hook_event_name": "PostToolUse", "session_id": "s-orch", "tool_name": "Bash",
             "tool_input": {"command": "python3 -I .kit/kit.py brief"}, "tool_response": {"stdout": "# Brief"}}


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = self.tmp.name
        os.makedirs(os.path.join(self.proj, ".kit"))
        with open(os.path.join(self.proj, ".kit", "config.json"), "w") as fh:
            json.dump({"kit": {"runtime": "claude"}}, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reply_without_brief_is_refused_and_names_the_command(self):
        r = hook(REPLY, self.proj)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("kit.py brief", r.stderr)
        self.assertIn("never", r.stderr)

    def test_reply_after_a_brief_passes(self):
        self.assertEqual(hook(BRIEF_RAN, self.proj).returncode, 0)
        r = hook(REPLY, self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_stale_brief_is_refused(self):
        hook(BRIEF_RAN, self.proj)
        stamp = os.path.join(self.proj, ".kit", "run", "brief", "s-orch")
        old = time.time() - 20 * 60
        os.utime(stamp, (old, old))
        r = hook(REPLY, self.proj)
        self.assertEqual(r.returncode, 2)
        self.assertIn("min ago", r.stderr)

    def test_another_sessions_brief_does_not_count(self):
        hook(dict(BRIEF_RAN, session_id="s-other"), self.proj)
        self.assertEqual(hook(REPLY, self.proj).returncode, 2)

    def test_board_cli_reply_and_batch_and_attached_board(self):
        cli = {"hook_event_name": "PreToolUse", "session_id": "s-orch", "tool_name": "Bash",
               "tool_input": {"command": "python3 -I .kit/board_cli.py reply _orchestrator c1 --text 'hi'"}}
        self.assertEqual(hook(cli, self.proj).returncode, 2)
        batch = {"hook_event_name": "PreToolUse", "session_id": "s-orch", "tool_name": "ArtifactData",
                 "tool_input": {"action": "batch", "writes": [
                     {"op": "set", "collection": "repos/board/comments", "doc_id": "x",
                      "data": {"from": "orchestrator", "reply_to": "c1", "text": "hi"}}]}}
        self.assertEqual(hook(batch, self.proj).returncode, 2)

    def test_not_a_reply_or_not_the_orchestrator_passes(self):
        post = json.loads(json.dumps(REPLY))
        post["tool_input"]["collection"] = "posts"
        self.assertEqual(hook(post, self.proj).returncode, 0)
        self.assertEqual(hook(REPLY, self.proj, role="part").returncode, 0)
        other = {"hook_event_name": "PreToolUse", "session_id": "s-orch", "tool_name": "Bash",
                 "tool_input": {"command": "git status"}}
        self.assertEqual(hook(other, self.proj).returncode, 0)

    def test_agy_run_command_is_denied_in_json(self):
        with open(os.path.join(self.proj, ".kit", "config.json"), "w") as fh:
            json.dump({"kit": {"runtime": "agy"}}, fh)
        agy = {"hookEventName": "PreToolUse", "conversationId": "c-orch",
               "toolCall": {"name": "run_command", "args": {"CommandLine": "python3 -I .kit/board_cli.py reply p c --text x"}}}
        r = hook(agy, self.proj)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["decision"], "deny")


class TrainsCard(unittest.TestCase):
    def test_states(self):
        ps = kit_cards.post_state
        self.assertEqual(ps({"column": "info", "status": "done"}), "done")
        self.assertEqual(ps({"column": "info", "status": "done (was in progress)"}), "done")
        self.assertEqual(ps({"column": "info", "status": "held: waits for VR5"}), "held")
        self.assertEqual(ps({"column": "info", "status": "on hold"}), "held")
        self.assertEqual(ps({"column": "info", "status": "closed: superseded"}), "closed")
        self.assertEqual(ps({"column": "question", "status": "done step A; priced, waiting your tap"}), "you")
        self.assertEqual(ps({"column": "info", "status": "in progress"}), "running")

    def test_trains_card_rows(self):
        t = kit_cards.build_trains(POSTS_WITH_TRAIN, [], "2026-11-23T08:00:00Z")
        by = {r["part"]: r["state"] for r in t["rows"]}
        self.assertEqual(by["HL1"], "done")
        self.assertEqual(by["HL6"], "held")
        self.assertEqual(by["K3i"], "closed")
        self.assertEqual(by["HL3"], "running")

    def test_page_shows_held_and_closed(self):
        page = open(os.path.join(ROOT, "kit", "board", "board.template.html"), encoding="utf-8").read()
        self.assertIn('held: "held"', page)
        self.assertIn('closed: "closed"', page)


def load_kit():
    spec = importlib.util.spec_from_file_location("kit_cl28", KIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Adopt(unittest.TestCase):
    """Harmonize's pre-kit posts carry a part but no train: resume and the task list could not see HL6, VR5, HL5."""

    def test_every_pre_kit_post_gets_its_train_and_resume_lists_it(self):
        kit = load_kit()
        posts = json.loads(json.dumps({k: v for k, v in POSTS.items()}))
        kit._load_board_docs = lambda m, db_path=None: (posts, {})
        kit.attached_board = lambda: None
        kit._resolve_board_token = lambda t=None: "t"
        written = []

        def upd(url, token, coll, did, fields):
            posts[did].update(fields)
            written.append(did)
            return posts[did]
        kit._local_update = upd
        r = kit.adopt_trains(url="http://x")
        self.assertEqual(r["trains"]["HL"], ["HL1-scoreboard-and-audit", "HL3-harmony-model", "HL5-harmony-ai-question",
                                             "HL6-retrain-all"])
        self.assertEqual(posts["VR5-second-run-question"]["train"], "VR")
        self.assertEqual(posts["A3-data"]["train"], "A")
        self.assertNotIn("_orchestrator", written)
        self.assertEqual(kit.adopt_trains(url="http://x")["written"], 0)  # once: nothing left to adopt
        out = kit.resume("/nonexistent", alive=set(), cli="claude")
        self.assertIn("Train HL (1/4 done)", out)
        self.assertIn("[h] HL6", out)
        self.assertIn("[?] VR5", out)
        self.assertNotIn("Train A (", out)  # A3's posts are done: no open step


class AdoptOnAnArtifactBoard(unittest.TestCase):
    """A web container has no board server: adopt prints the ArtifactData writes, pinned, and never calls the server."""

    def test_batches_without_a_server(self):
        kit = load_kit()
        posts = json.loads(json.dumps(POSTS))
        kit._load_board_docs = lambda m, db_path=None: (posts, {})
        kit.attached_board = lambda: {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}
        kit.artifact_url = lambda: "https://claude.ai/artifact/X"
        versions = {"posts/%s" % k: 3 for k in posts if k != "K3i-switch"}
        kit._art_state = lambda: {"versions": versions}

        def no_server(*a, **k):
            raise AssertionError("adopt called the board server")
        kit._local_update = kit._board_request = no_server
        with tempfile.TemporaryDirectory() as d:
            r = kit.adopt_trains(out_dir=d)
            writes = [w for pth in r["batches"] for w in json.load(open(pth))]
        hl6 = [w for w in writes if w["doc_id"] == "HL6-retrain-all"][0]
        self.assertEqual(hl6, {"op": "update", "collection": "repos/board/posts", "doc_id": "HL6-retrain-all",
                               "data": {"train": "HL"}, "if_version": 3})
        self.assertEqual(r["need_pin"], ["posts/K3i-switch"])
        self.assertIn("ArtifactData` `batch`", kit.adopt_text(r))


class MemoryHoldsWhatNoPostHolds(unittest.TestCase):
    """The owner, 2026-11-23: "everything is on the board": compact archives the state files, awaits.md and closed
    issues once; the orchestrator's timers, lessons and open issues (until posted) stay."""

    def test_compact_archives_what_the_board_holds(self):
        kit = load_kit()
        with tempfile.TemporaryDirectory() as d:
            mem = os.path.join(d, ".board", "memory")
            for sub in ("state", "issues", "lessons"):
                os.makedirs(os.path.join(mem, sub))
            files = {"state/app.md": "Next: ship the login screen\n", "state/orchestrator.md": "timer at 09:00\n",
                     "awaits.md": "- [app] pick a colour\n", "issues/2026-11-01-a.md": "# crash\nStatus: closed (abc)\n",
                     "issues/2026-11-02-b.md": "# slow page\nStatus: open\n", "lessons/l.md": "# a lesson\n"}
            for rel, text in files.items():
                with open(os.path.join(mem, rel), "w") as fh:
                    fh.write(text)
            out = kit.compact_memory(root_dir=d)
            left = sorted(os.path.relpath(os.path.join(r, f), mem) for r, _, fs in os.walk(mem) for f in fs
                          if "archive" not in r)
            self.assertEqual(left, ["issues/2026-11-02-b.md", "lessons/l.md", "state/orchestrator.md"], out)
            self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "state", "app.md")))
            self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "awaits.md")))
            self.assertIn("1 open issue(s) stay", out)
            self.assertIn("slow page", out)


POSTS_WITH_TRAIN = {k: dict(v, train="harmony") for k, v in POSTS.items() if not k.startswith("_")}

if __name__ == "__main__":
    unittest.main()
