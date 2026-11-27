"""One prompt: `kit.py resume` prints what this session needs, by role, platform and situation.

- An orchestrator's guide is cut in blocks; resume prints the ones of its situation (first here, taking over, same) and
  names the cases, read one at a time with `kit.py guide <case>`.
- The guide is the one of the platform the session runs on, not of the runtime the config names.
- Every role but the clerk is told what the owner can do where he is.
- Git is the record: resume and doctor say how far its copy is behind; the last batch sent to the Artifact is kept in git.
- A part that continues another session's step is told so.

Run via: python3 -m unittest tests.test_resume_one_prompt -v
"""
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

GUIDES = {"web": "kit/claude/orchestrator.template.md", "cli": "kit/claude-cli/orchestrator.template.md",
          "agy": "kit/orchestrator.template.md"}
_spec = importlib.util.spec_from_file_location("one_prompt_handover", str(ROOT / "kit" / "handover.py"))
handover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handover)
GUIDE = ("head\n\n<!-- start: first -->\nSETUP\n\n<!-- start: first, takeover -->\nTAKE THE BOARD\n\n<!-- start: always -->\nALWAYS\n\n"
         "<!-- start: takeover -->\nTELL THEM\n\n<!-- start: same -->\nAFTER COMPACTION\n\n<!-- case: like | a Like came -->\nTHE LIKE TEXT\n")


class Guides(unittest.TestCase):
    def test_every_guide_is_whole_and_every_mark_is_known(self):
        for pl, rel in GUIDES.items():
            text = (ROOT / rel).read_text(encoding="utf-8")
            start, cases = kit.guide_parts(text)
            self.assertEqual(len(re.findall(r"^<!--", text, re.M)), len(start) + len(cases), pl)     # none malformed
            for when, body in start:
                self.assertTrue(body and set(when) <= {"first", "takeover", "same", "always"}, (pl, when))
            ids = [c for c, _, _ in cases]
            self.assertEqual(len(ids), len(set(ids)), pl)
            for need in ("like", "words", "done", "away", "archive", "hand-over", "runners"):
                self.assertIn(need, ids, pl)
            self.assertTrue(all(t and b for _, t, b in cases), pl)
            for c in re.findall(r"kit\.py guide ([a-z-]+)", text):
                self.assertIn(c, ids, "%s names the case %s, which it does not have" % (pl, c))

    def test_the_timer_and_the_brief_rule_reach_every_session(self):
        for pl, rel in GUIDES.items():
            start, _ = kit.guide_parts((ROOT / rel).read_text(encoding="utf-8"))
            always = "\n".join(b for w, b in start if "always" in w)
            self.assertIn("Before you answer the owner", always, pl)
            self.assertRegex(always, r"30.min", pl)


class Situation(unittest.TestCase):
    def sit(self, named, comments, session="me", pl="web"):
        return kit.situation({"_orchestrator": {"session_id": named}} if named else {}, comments, session, pl)

    def test_first_takeover_same(self):
        self.assertEqual(self.sit("", {}), ["first"])
        self.assertEqual(self.sit("other", {"welcome-claude-1": {}}), ["takeover"])
        self.assertEqual(self.sit("other", {}), ["first", "takeover"])            # running elsewhere, new platform here
        self.assertEqual(self.sit("me", {"welcome-claude-1": {}}), ["same"])
        self.assertEqual(self.sit("", {"welcome-claude-1": {}}), ["takeover"])    # the welcome says: not the first
        self.assertEqual(self.sit("", {"welcome-claude-1": {}}, session=""), ["takeover"])

    def test_a_board_older_than_the_welcome_is_not_a_first(self):
        self.assertEqual(self.sit("session_01A", {}), ["takeover"])
        self.assertEqual(self.sit("p-orchestrator", {}, pl="cli"), ["takeover"])
        self.assertEqual(self.sit("session_01A", {}, pl="cli"), ["first", "takeover"])
        self.assertEqual(self.sit("p-orchestrator", {}, pl="web"), ["first", "takeover"])
        self.assertEqual(self.sit("session_01A", {}, session="session_01A"), ["same"])

    def test_a_welcome_of_one_platform_is_not_anothers(self):
        self.assertEqual(self.sit("", {"welcome-claude-cli-1": {}}, pl="web"), ["first"])
        self.assertEqual(self.sit("", {"welcome-claude-1": {}}, pl="cli"), ["first"])
        self.assertEqual(self.sit("", {"welcome-claude-cli-1": {}}, pl="cli"), ["takeover"])


class Project(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        (self.root / ".kit").mkdir()
        (self.root / ".kit" / "config.json").write_text(json.dumps({"project": "P", "kit": {"runtime": "agy", "owner_can": ["sing"], "owner_cannot": ["solder"]}}))
        (self.root / ".kit" / "orchestrator.md").write_text(GUIDE)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY"))}
        self.ps = [mock.patch.object(kit, "ROOT", str(self.root)), mock.patch.dict(os.environ, env, clear=True)]
        for p in self.ps:
            p.start()

    def tearDown(self):
        for p in reversed(self.ps):
            p.stop()
        self.t.cleanup()

    def start(self, posts, comments, session="me"):
        with mock.patch.object(kit, "_platform", return_value="web"):
            return "\n".join(kit.start_lines(posts, comments, session))

    def test_resume_prints_only_this_situations_start(self):
        first = self.start({}, {})
        for a in ("SETUP", "TAKE THE BOARD", "ALWAYS"):
            self.assertIn(a, first)
        for a in ("TELL THEM", "AFTER COMPACTION", "THE LIKE TEXT"):
            self.assertNotIn(a, first)
        self.assertIn("- like: a Like came", first)
        nxt = self.start({"_orchestrator": {"session_id": "other"}}, {"welcome-claude-1": {}})
        self.assertNotIn("SETUP", nxt)
        self.assertIn("TELL THEM", nxt)
        same = self.start({"_orchestrator": {"session_id": "me"}}, {"welcome-claude-1": {}})
        self.assertIn("AFTER COMPACTION", same)
        self.assertNotIn("TAKE THE BOARD", same)

    def test_a_guide_without_sections_is_named_not_dropped(self):
        (self.root / ".kit" / "orchestrator.md").write_text("an old guide\n")
        self.assertIn(".kit/orchestrator.md", self.start({}, {}))

    def test_guide_case(self):
        with mock.patch.object(kit, "_platform", return_value="web"):
            self.assertIn("THE LIKE TEXT", kit.guide("like"))
            self.assertNotIn("SETUP", kit.guide("like"))
            self.assertEqual(kit.guide(), "like: a Like came\n")

    def test_the_guide_is_the_platforms(self):
        (self.root / ".kit" / "claude-web").mkdir()
        (self.root / ".kit" / "claude-web" / "orchestrator.md").write_text("web")
        self.assertTrue(kit.orchestrator_guide("web").endswith("claude-web/orchestrator.md"))
        self.assertTrue(kit.orchestrator_guide("agy").endswith(".kit/orchestrator.md"))
        os.environ["CLAUDE_CODE_REMOTE"] = "true"                     # the config says agy; the session is on the web
        self.assertEqual(kit._platform(), "web")

    def test_owner_lines(self):
        for pl, no in (("web", "run a command"), ("cli", "/tmp"), ("agy", "/tmp")):
            t = "\n".join(kit.owner_lines(pl))
            self.assertIn("Like", t)
            self.assertIn(no, t.split("He cannot")[1])
            self.assertIn("sing", t.split("He cannot")[0])
            self.assertIn("solder", t.split("He cannot")[1])

    def test_every_role_but_the_clerk_is_told(self):
        posts = {"p1": {"part": "P1", "title": "t", "train": "T", "status": "planned"}, "_orchestrator": {"session_id": "o"}}
        with mock.patch.object(kit, "_load_board_docs", return_value=(posts, {})), \
                mock.patch.object(kit, "resume", return_value="R\n"), mock.patch.object(kit, "git_copy_lines", return_value=["BEHIND"]):
            subprocess.run(["git", "-C", kit.ROOT, "init", "-q"], check=True)          # a part stops where git does not work
            subprocess.run(["git", "-C", kit.ROOT, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            for role in ("orchestrator", "part", "review"):
                self.assertIn("What the owner can do here", kit.resume_role("m", role, post="p1"), role)
            self.assertIn("BEHIND", kit.resume_role("m", "orchestrator"))
            self.assertIn("TAKE THE BOARD", kit.resume_role("m", "orchestrator"))
            self.assertNotIn("What the owner can do here", kit.resume_role("m", "clerk"))

    def git(self, *a, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(cwd or self.root), check=True,
                              capture_output=True, text=True).stdout

    def mirror(self, docs):
        m = self.root / "m"
        for coll, i, d in docs:
            (m / "board" / coll).mkdir(parents=True, exist_ok=True)
            (m / "board" / coll / (i + ".json")).write_text(json.dumps(d))
        return m

    def db(self, docs):
        p = self.root / ".kit" / "board_data"
        p.mkdir()
        c = sqlite3.connect(str(p / "board.db"))
        c.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, data TEXT)")
        c.executemany("INSERT INTO docs VALUES (?,?,?)", [(a, b, json.dumps(d)) for a, b, d in docs])
        c.commit()
        c.close()

    def test_git_copy_counts_what_differs(self):
        docs = [("posts", "a", {"x": 1}), ("comments", "b", {"y": 1})]
        self.db(docs + [("kit_docs", "z", {})])
        m = self.mirror(docs)
        self.assertEqual(kit.git_copy_lines(str(m)), [])
        (m / "board" / "posts" / "a.json").write_text('{"x": 0}')
        os.remove(str(m / "board" / "comments" / "b.json"))
        self.assertIn("2 document(s)", "\n".join(kit.git_copy_lines(str(m))))

    def test_git_copy_none_and_not_here(self):
        self.assertEqual(kit.git_copy_lines(str(self.root / "none")), [])                 # no local copy: nothing to say
        self.db([("posts", "a", {})])
        self.assertIn("none in this clone", kit.git_copy_lines(str(self.root / "none"))[0])
        with mock.patch.object(kit, "attached_board", return_value={"url": "u", "prefix": "repos/x/"}):
            self.assertEqual(kit.git_copy_lines(str(self.root / "none")), [])             # its host project's to save

    def test_unpushed_commits_are_said(self):
        self.db([("posts", "a", {"x": 1})])
        m = self.mirror([("posts", "a", {"x": 1})])
        self.git("init", "-q", "-b", kit.BRANCH, cwd=m)
        self.git("add", "-A", cwd=m)
        self.git("commit", "-q", "-m", "1", cwd=m)
        self.assertEqual(kit.git_copy_lines(str(m)), [])                                   # no remote ref: no guess
        self.git("update-ref", "refs/remotes/origin/" + kit.BRANCH, "HEAD", cwd=m)
        self.assertEqual(kit.git_copy_lines(str(m)), [])
        self.git("commit", "-q", "--allow-empty", "-m", "2", cwd=m)
        self.assertIn("1 commit(s)", kit.git_copy_lines(str(m))[0])

    def test_the_last_batch_sent_is_kept_in_git(self):
        out = self.root / "art" / "out"
        out.mkdir(parents=True)
        (out / "batch-01.json").write_text("[]")
        (out / "batch-02.json").write_text("[]")
        os.environ["KIT_ARTIFACT_DIR"] = str(self.root / "art")
        with mock.patch.object(kit, "keep", return_value=("KEPT", True)) as k, mock.patch("builtins.print"), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}):           # CL51: `artifact` is the web's
            kit.main(["artifact", "sent", str(out / "batch-01.json")])
            self.assertEqual(k.call_count, 0)                                              # one more to send
            kit.main(["artifact", "sent", str(out / "batch-02.json")])
            self.assertEqual(k.call_count, 1)

    def test_keep_without_a_board_does_nothing(self):
        self.assertTrue(kit.keep("m")[1])
        self.db([])
        self.assertFalse(kit.keep("m")[1])                                                 # a board and no saver: said

    def test_a_part_that_continues_is_told(self):
        for p, want in (({"session_id": "s1", "status": "in progress"}, True), ({"session_id": "me", "status": "in progress"}, False),
                        ({"status": "in progress"}, False), ({"session_id": "pending-1", "status": "in progress"}, False),
                        ({"session_id": "s1", "status": "planned"}, False)):
            with mock.patch.object(kit, "_git_run", return_value=mock.Mock(returncode=1, stdout="")):
                self.assertEqual(bool(kit.continues_lines(p, "me")), want, p)


class OnePrompt(unittest.TestCase):
    def test_a_successor_gets_the_command_and_the_guide_it_has(self):
        for cli, path in (("claude", ".kit/claude-cli/orchestrator.md"), ("agy", ".kit/orchestrator.md"), ("web", ".kit/orchestrator.md")):
            p = handover.successor_prompt("orchestrator", 3, cli=cli)
            self.assertIn("kit.py resume --role orchestrator", p)
            self.assertIn("(%s)" % path, p)
            self.assertLess(len(p), 220)                                                   # no second copy of resume
        self.assertIn(".kit/clerk.md", handover.successor_prompt("clerk", 3, cli="web"))

    def test_the_board_is_saved_at_stop_on_the_web_too(self):
        sys.path.insert(0, str(ROOT / "kit"))
        import hook
        self.assertIn("kit_web_save.py", [r[2] for r in hook.rows("web", "Stop")])


if __name__ == "__main__":
    unittest.main()
