#!/usr/bin/env python3
"""CL28: the orchestrator answers only from a fresh brief.

  1. `kit.py brief`: open decisions (age, default, what each blocks), the owner's decisions of the last days, each train from
     its posts, and the memory lines a post contradicts (Harmonize, 11-23: "A3 waits on your choice" while A3 was over).
  2. kit_brief_guard.py: an orchestrator's reply to the owner is refused without a `resume` in the last 15 minutes.
  3. The Trains card (kit_cards.py): a done post is never running; held and closed show as such.
python3 -m unittest tests.test_cl28_brief -v
"""
import json
import re
import os
import shutil
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
             "tool_input": {"command": "python3 -I .kit/kit.py resume"}, "tool_response": {"stdout": "# Resume"}}


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
        self.assertIn("kit.py resume", r.stderr)
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


    def test_agy_without_a_session_id_a_resume_unlocks_the_reply(self):
        with open(os.path.join(self.proj, ".kit", "config.json"), "w") as fh:
            json.dump({"kit": {"runtime": "agy"}}, fh)
        reply = {"hookEventName": "PreToolUse",
                 "toolCall": {"name": "run_command", "args": {"CommandLine": "python3 -I .kit/board_cli.py reply p c --text x"}}}
        self.assertEqual(json.loads(hook(reply, self.proj).stdout)["decision"], "deny")
        ran = {"hookEventName": "PostToolUse",
               "toolCall": {"name": "run_command", "args": {"CommandLine": "python3 -I .kit/kit.py resume"}}}
        hook(ran, self.proj)
        self.assertEqual(json.loads(hook(reply, self.proj).stdout)["decision"], "allow")


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
        kit._ensure_local_board = lambda url: True
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


class ResumeCarriesWhatTheBriefAdded(unittest.TestCase):
    """The owner, 2026-11-23: "fold them in to resume": question age, default and what each blocks; the owner's recent
    decisions; a stale _orchestrator id."""

    def setUp(self):
        kit = load_kit()
        posts = {k: dict(v, train="harmony") for k, v in POSTS.items()}
        kit._load_board_docs = lambda m, db_path=None: (posts, COMMENTS)
        kit._attached_boards = lambda: ([], [])
        self.out = kit.resume("/nonexistent", alive=set(), cli="claude", session="test-cl28")

    def section(self, title):
        return self.out.split("## " + title, 1)[1].split("\n## ", 1)[0]

    def test_open_questions_with_age_default_and_what_they_block(self):
        s = self.section("Open questions")
        self.assertIn("(2)", s.splitlines()[0])
        self.assertIn("3d 7h old", s)
        self.assertIn("default: Buy nothing now; HL6 runs now", s)
        vr5 = [l for l in s.splitlines() if "VR5" in l][0]
        self.assertIn("blocks HL6 (held: waits for VR5)", vr5)

    def test_the_owners_recent_decisions_only(self):
        s = self.section("The owner's decisions")
        self.assertIn("already decided to close that train", s)
        self.assertIn("Like", s)
        self.assertNotIn("an old one", s)
        self.assertNotIn("an answer", s)

    def test_a_stale_orchestrator_id_is_flagged(self):
        self.assertIn("MISMATCH: `_orchestrator` names session session_old, you are test-cl28", self.out)

    def test_brief_is_gone(self):
        r = subprocess.run([sys.executable, "-I", KIT, "brief"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


class AdoptOnAnArtifactBoard(unittest.TestCase):
    """A web container: adopt goes the kit's own way (the local board started as `artifact load` does, then
    `artifact push`), never a write path of its own."""

    def test_reuses_the_local_board_and_artifact_push(self):
        kit = load_kit()
        posts = json.loads(json.dumps(POSTS))
        kit._load_board_docs = lambda m, db_path=None: (posts, {})
        kit.attached_board = lambda: {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}
        started, pushed = [], []
        kit._ensure_local_board = lambda url: started.append(url) or True
        kit._resolve_board_token = lambda t=None: "t"
        kit._local_update = lambda url, token, coll, did, fields: posts[did].update(fields) or posts[did]
        kit.artifact_push = lambda url=None, token=None: pushed.append(url) or {
            "batches": ["b1.json"], "need_pin": [], "board": "https://claude.ai/artifact/X", "writes": 9, "files": ""}
        r = kit.adopt_trains(url="http://127.0.0.1:8766")
        self.assertEqual(started, ["http://127.0.0.1:8766"])
        self.assertEqual(pushed, ["http://127.0.0.1:8766"])
        self.assertEqual(posts["HL6-retrain-all"]["train"], "HL")
        self.assertIn("b1.json", kit.adopt_text(r))

    def test_no_board_says_how_instead_of_crashing(self):
        kit = load_kit()
        kit._load_board_docs = lambda m, db_path=None: (json.loads(json.dumps(POSTS)), {})
        kit._ensure_local_board = lambda url: False
        with self.assertRaises(kit.Conflict):
            kit.adopt_trains(url="http://127.0.0.1:8766")


class DoneOnlyWhenOnTheBoard(unittest.TestCase):
    """The owner, 2026-11-23: "How come you said you posted when you didn't?": a part's post changed only on the
    local copy; `kit.py done` said OK and the live board (the Artifact) still showed it planned."""

    def test_done_refuses_a_post_the_artifact_has_not_got(self):
        kit = load_kit()
        post = {"title": "x", "status": "complete", "verdict": "v"}
        kit._get_post = lambda pid, db_path=None: post
        kit.attached_board = lambda: {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}
        kit.artifact_url = lambda: "https://claude.ai/artifact/X"
        kit.check_asked = lambda p, db_path=None: ([], [])
        with tempfile.TemporaryDirectory() as d:
            kit._art_dir = lambda: d
            rc, out = kit.done("p1")
            self.assertEqual(rc, 1)
            self.assertIn("not on the board yet", out)
            os.makedirs(os.path.join(d, "base", "posts"))
            with open(os.path.join(d, "base", "posts", "p1.json"), "w") as fh:
                json.dump(post, fh)       # what `artifact sent` records once the batch went
            rc, out = kit.done("p1")
            self.assertNotIn("not on the board yet", out)


class CommandIndex(unittest.TestCase):
    """The owner, 2026-11-23: "Why are all the sessions skipping the kit's scripts???": an index read before any code."""

    def test_every_kit_command_is_in_the_index(self):
        kit = load_kit()
        import argparse
        names = set()
        orig = argparse.ArgumentParser.parse_args

        def grab(self, args=None, namespace=None):
            for act in self._actions:
                if isinstance(act, argparse._SubParsersAction):
                    names.update(act.choices)
            raise SystemExit(0)
        argparse.ArgumentParser.parse_args = grab
        try:
            kit.main(["resume"])
        except SystemExit:
            pass
        finally:
            argparse.ArgumentParser.parse_args = orig
        self.assertGreater(len(names), 25)
        indexed = " ".join(c for _, c, _ in kit.COMMANDS)
        missing = sorted(n for n in names if not re.search(r"\b%s\b" % re.escape(n), indexed))
        self.assertEqual(missing, [])

    def test_a_word_finds_the_existing_command(self):
        out = subprocess.run([sys.executable, "-I", KIT, "commands", "artifact"], capture_output=True, text=True).stdout
        self.assertIn("kit.py artifact pull|load|push", out)
        self.assertIn("starts the local board", out)

    def test_the_index_is_not_dumped_on_who_does_not_need_it(self):
        out = subprocess.run([sys.executable, "-I", KIT, "commands"], capture_output=True, text=True).stdout
        self.assertLess(len(out), 300)
        self.assertIn("write the board", out)
        self.assertIn("kit.py whoami", subprocess.run([sys.executable, "-I", KIT, "commands", "all"], capture_output=True, text=True).stdout)
        for p in ("kit/part.template.md", "kit/claude-cli/part.template.md", "kit/claude/part.template.md"):
            with open(os.path.join(ROOT, p), encoding="utf-8") as fh:
                self.assertNotIn("kit.py commands", fh.read())

    def test_it_comes_with_the_kits_own_files(self):
        kit = load_kit()
        k3 = next(x for x in kit.parse_lessons(open(os.path.join(ROOT, "kit", "lessons.md"), encoding="utf-8").read()) if x["id"] == "K3")
        for f, want in ((".kit/board_cli.py", True), ("scripts/kit.py", True), ("scripts/new_board_sync.py", True), ("src/app.jsx", False),
                        ("scripts/compte-lignes.cjs", False), ("pipeline/train.py", False)):
            self.assertEqual(kit._touches(k3["files"], [f]), want, f)
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, ".kit"))
                shutil.copy(os.path.join(ROOT, "kit", "lessons.md"), os.path.join(d, ".kit", "lessons.md"))
                env = dict({k: v for k, v in os.environ.items() if not k.startswith("KIT_")}, CLAUDE_PROJECT_DIR=d, KIT_ROLE="part")
                r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit", "hooks", "kit_memory_jit.py")], capture_output=True, text=True, env=env,
                                   input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": d, "session_id": "k3-%s-%s" % (os.getpid(), f),
                                                     "tool_input": {"file_path": os.path.join(d, f)}}))
                self.assertEqual("**K3** (lesson)" in r.stdout, want, f + r.stdout + r.stderr)

    def test_session_start_names_it_in_the_kits_own_repo(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".kit"))
            open(os.path.join(d, ".kit", "kit.py"), "w").close()
            os.makedirs(os.path.join(d, "kit", "hooks"))
            open(os.path.join(d, "install.py"), "w").close()
            env = dict(os.environ, CLAUDE_PROJECT_DIR=d)
            env.pop("KIT_ROLE", None)
            r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit", "hooks", "kit_session_start.py")],
                               input=json.dumps({"hook_event_name": "SessionStart", "cwd": d}), capture_output=True,
                               text=True, env=env)
            self.assertIn(".kit/kit.py commands", r.stdout)


class MemoryHoldsWhatNoPostHolds(unittest.TestCase):
    """The owner, 2026-11-23: "everything is on the board": compact archives the state files, awaits.md and closed
    issues once; the orchestrator's timers, lessons and open issues (until posted) stay."""

    def test_compact_archives_what_the_board_holds(self):
        kit = load_kit()
        with tempfile.TemporaryDirectory() as d:
            mem = os.path.join(d, ".board", "memory")
            for sub in ("state", "issues", "lessons"):
                os.makedirs(os.path.join(mem, sub))
            # CL29 (O53): only settled work moves; an open awaits point and an unsettled state stay (test_cl29_compact_checker)
            files = {"state/app.md": "Next: done, shipped the login screen\n", "state/orchestrator.md": "timer at 09:00\n",
                     "awaits.md": "# What awaits the owner\n\n", "issues/2026-11-01-a.md": "# crash\nStatus: closed (abc)\n",
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
