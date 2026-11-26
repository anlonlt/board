"""CL49: a part reaches the orchestrator running now, whoever it is.

CL44's part said Done and messaged `board-orchestrator`, the fixed name in the part guide: it was launched by the
orchestrator on the owner's computer, and that name matched two dead sessions on another computer. A web orchestrator
had taken over the board meanwhile without claiming CL44. These tests replay that case:

1. `kit.py launch` records the part's owner on its post: the launching orchestrator's address and device.
2. The owner changes only by a claim (`kit.py claim`) or a hand-over on the same device; a board takeover claims nothing.
3. `kit.py resume --role part` prints where to report (the owner now, its device); the part guides use it, never a
   fixed name.
4. A failed send: `kit.py unreached` writes `orchestrator_unreached`; every orchestrator's resume lists it to claim.
5. Reviews and the clerk: a review reports to the orchestrator that launched it, unless another claimed the step.

Run via: python3 -m unittest tests.test_cl49_orchestrator_owner -v
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, TOKEN  # noqa: E402
from test_launch import POST, TRAINS, Base, load_kit, make_repo  # noqa: E402

LAPTOP = {"CLAUDE_CODE_SESSION_ID": "a1b2-laptop-orch", "KIT_DEVICE": "owners-laptop"}
WEB = {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_web0rch"}
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "KIT_SESSION_ID", "KIT_DEVICE", "CLAUDE_CODE_REMOTE")


def as_session(env):
    """The environment of one session: only its own ids (this test may itself run in a web session)."""
    clean = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    clean.update(env)
    return mock.patch.dict(os.environ, clean, clear=True)


class Board(Base):
    """A real local board server; the kit runs as one session or another."""

    def seed(self):
        repo = make_repo(self.tmp / "repo", {"project": "Board", "board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local"}})
        self.local("posts/cl99-launch-me", POST)
        self.local("posts/_trains", TRAINS)
        self.local("posts/_orchestrator", {"title": "Ask the orchestrator", "column": "info", "session_id": "board-orchestrator"})
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "claude").write_text("#!/bin/sh\nexit 0\n")
        (bindir / "claude").chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        return self.kit_in(repo)

    def post(self):
        return self.local("posts/cl99-launch-me")["data"]

    def resume_part(self, k):
        posts = {"_orchestrator": self.local("posts/_orchestrator")["data"], "cl99-launch-me": self.post()}
        with mock.patch.object(k, "_load_board_docs", return_value=(posts, {})), \
                mock.patch.object(k, "_attached_boards", return_value=([], [])), \
                mock.patch.object(k, "take_step_branch", return_value=[]):
            return k.resume_role(None, "part", post="cl99-launch-me")

    def launch_on_laptop(self, k):
        with as_session(LAPTOP):
            r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=False, gate=False)
        self.assertTrue(r["ok"], r)
        return r

    def test_launch_records_the_launching_orchestrator_and_its_device(self):
        k = self.seed()
        r = self.launch_on_laptop(k)
        p = self.post()
        self.assertEqual(p["orchestrator"], "a1b2-laptop-orch")
        self.assertEqual(p["orchestrator_name"], "board-orchestrator")
        self.assertEqual(p["orchestrator_device"], "owners-laptop")
        self.assertEqual(r["orchestrator"]["orchestrator_device"], "owners-laptop")

    def test_cl44_a_board_takeover_claims_nothing_the_part_reports_to_its_launcher(self):
        """The CL44 case: laptop launches; a web orchestrator takes the board (its id on _orchestrator); the part's
        resume still names the laptop session on the laptop, never the web one nor the bare fixed name."""
        k = self.seed()
        self.launch_on_laptop(k)
        o = self.local("posts/_orchestrator")["data"]
        self.local("posts/_orchestrator", dict(o, session_id="session_web0rch"))       # the takeover
        with as_session({"CLAUDE_CODE_SESSION_ID": "the-part", "KIT_DEVICE": "owners-laptop"}):
            out = self.resume_part(k)
        where = out.split("## Where you report", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`a1b2-laptop-orch` (session name `board-orchestrator`), device `owners-laptop` (it launched the step)", where)
        self.assertIn("the session on this computer (`owners-laptop`)", where)
        self.assertIn("a row of another machine", where)
        self.assertNotIn("session_web0rch", where)
        self.assertIn("kit.py unreached cl99-launch-me", where)
        # the same part read from another machine: only the laptop's row, never a look-alike here
        with as_session({"CLAUDE_CODE_SESSION_ID": "x", "KIT_DEVICE": "other-box"}):
            where2 = self.resume_part(k).split("## Where you report", 1)[1]
        self.assertIn("only the row of device `owners-laptop`", where2)

    def test_claim_moves_the_part_and_its_review_to_the_claimer(self):
        k = self.seed()
        self.launch_on_laptop(k)
        with as_session(WEB):
            r = k.claim("cl99-launch-me", url=self.url, token=TOKEN)
        self.assertTrue(r["ok"], r)
        p = self.post()
        self.assertEqual((p["orchestrator"], p["orchestrator_device"]), ("session_web0rch", "web"))
        self.assertEqual(p["review_orchestrator"], "session_web0rch")
        self.assertTrue(p["claimed_at"].endswith("Z"))
        self.assertIn("claimed post cl99-launch-me", r["tell"])
        self.assertIn("board-cl99", r["tell"])                       # the part's session, to message
        with as_session({"CLAUDE_CODE_SESSION_ID": "the-part", "KIT_DEVICE": "owners-laptop"}):
            where = self.resume_part(k).split("## Where you report", 1)[1]
        self.assertIn("`send_message` (claude-code-remote) with `session_id` `session_web0rch`", where)
        self.assertIn("it claimed the step", where)

    def test_unreached_is_written_listed_for_every_orchestrator_and_cleared_by_a_claim(self):
        k = self.seed()
        self.launch_on_laptop(k)
        with as_session({"CLAUDE_CODE_SESSION_ID": "the-part", "KIT_DEVICE": "owners-laptop"}):
            r = k.unreached("cl99-launch-me", why="SendMessage: no session board-orchestrator on this computer",
                            url=self.url, token=TOKEN)
        self.assertTrue(r["ok"], r)
        u = self.post()["orchestrator_unreached"]
        self.assertEqual((u["to"], u["device"]), ("a1b2-laptop-orch", "owners-laptop"))
        lines = "\n".join(k.unreached_lines({"cl99-launch-me": self.post()}))
        self.assertIn("could not reach `a1b2-laptop-orch` on owners-laptop", lines)
        self.assertIn("kit.py claim cl99-launch-me", lines)
        with as_session(WEB):
            k.claim("cl99-launch-me", url=self.url, token=TOKEN)
        self.assertIsNone(self.post().get("orchestrator_unreached"))
        self.assertEqual(k.unreached_lines({"cl99-launch-me": self.post()}), [])

    def test_a_successor_on_the_same_device_inherits_after_a_hand_over_another_device_never(self):
        k = self.seed()
        self.launch_on_laptop(k)
        other = dict(POST, part="CL98", orchestrator="z9-desk-orch", orchestrator_name="board-orchestrator",
                     orchestrator_device="desk")
        self.local("posts/cl98-desk", other)
        spec = __import__("importlib.util").util.spec_from_file_location("handover_t", str(ROOT / "kit" / "handover.py"))
        ho = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(ho)
        ho.ROOT = k.ROOT
        ho.record_predecessor("board-orchestrator", env=dict(LAPTOP))      # the hand-over, written by the predecessor
        posts = {"cl99-launch-me": self.post(), "cl98-desk": self.local("posts/cl98-desk")["data"]}
        with as_session({"CLAUDE_CODE_SESSION_ID": "c3-successor", "KIT_DEVICE": "owners-laptop"}):
            lines = "\n".join(k.inherit_lines(posts, url=self.url, token=TOKEN))
        self.assertIn("`cl99-launch-me`", lines)
        self.assertNotIn("cl98-desk", lines)
        self.assertEqual(self.post()["orchestrator"], "c3-successor")
        self.assertEqual(self.post()["inherited_from"], "a1b2-laptop-orch")
        self.assertEqual(self.local("posts/cl98-desk")["data"]["orchestrator"], "z9-desk-orch")
        self.assertFalse(os.path.exists(os.path.join(k.ROOT, k.HANDOVER_FROM)))
        # a hand-over recorded on another device: nothing inherited
        ho.record_predecessor("board-orchestrator", env={"CLAUDE_CODE_SESSION_ID": "z9-desk-orch", "KIT_DEVICE": "desk"})
        with as_session({"CLAUDE_CODE_SESSION_ID": "c3-successor", "KIT_DEVICE": "owners-laptop"}):
            lines = "\n".join(k.inherit_lines({"cl98-desk": self.local("posts/cl98-desk")["data"]}, url=self.url, token=TOKEN))
        self.assertIn("nothing inherited", lines)
        self.assertEqual(self.local("posts/cl98-desk")["data"]["orchestrator"], "z9-desk-orch")


class ReviewAndGuides(unittest.TestCase):
    def setUp(self):
        self.k = load_kit("kit_cl49")

    def test_a_review_reports_to_its_launcher_unless_the_step_was_claimed(self):
        p = {"orchestrator": "a1b2-laptop-orch", "orchestrator_device": "owners-laptop",
             "review_orchestrator": "session_rev1", "review_orchestrator_device": "web"}
        self.assertEqual(self.k.step_owner(p, review=True)["address"], "session_rev1")
        self.assertEqual(self.k.step_owner(p)["address"], "a1b2-laptop-orch")
        claimed = dict(p, review_orchestrator="session_claimer", claimed_at="2026-11-26T23:00:00Z")   # claim writes both
        out = "\n".join(self.k.report_lines("cl99", claimed, review=True))
        self.assertIn("`session_claimer`", out)
        self.assertIn("your review", out)

    def test_a_post_from_before_cl49_falls_back_to_the_board_orchestrator(self):
        o = self.k.step_owner({}, {"_orchestrator": {"session_id": "session_now"}})
        self.assertEqual(o["address"], "session_now")
        self.assertIn("before CL49", o["how"])

    def test_the_guides_never_send_a_report_to_the_fixed_name(self):
        """The part and review guides name the owner `resume` prints, never `{{SLUG}}-orchestrator` as a target."""
        for rel in ("kit/part.template.md", "kit/claude-cli/part.template.md", "kit/claude/part.template.md",
                    "kit/consultant.template.md", "kit/claude-cli/consultant.template.md", "kit/claude/consultant.template.md"):
            text = (ROOT / rel).read_text()
            self.assertIsNone(re.search(r"(SendMessage|send_message)[^.\n]{0,40}`\{\{SLUG\}\}-orchestrator`", text), rel)
            self.assertIn("Where you report", text, rel)
            self.assertIn("kit.py unreached", text, rel)
        clerk = (ROOT / "kit" / "claude-cli" / "clerk.template.md").read_text()
        self.assertIn("the session on THIS computer that started you", clerk)
        for rel in ("kit/orchestrator.template.md", "kit/claude-cli/orchestrator.template.md", "kit/claude/orchestrator.template.md"):
            self.assertIn("Taking over the board claims no part", (ROOT / rel).read_text(), rel)

    def test_the_review_prompt_names_its_launcher(self):
        tmp = Path(self.k.ROOT)
        with as_session(LAPTOP), mock.patch.object(self.k, "_get_post", side_effect=lambda pid, db_path=None:
                                                      {"session_id": "session_web0rch"} if pid == "_orchestrator" else
                                                      {"part": "CL99", "train": "cli"}):
            rc, txt = self.k.review("cl99-launch-me", dry_run=True, cli="claude")
        self.assertEqual(rc, 0, txt)
        self.assertIn("a1b2-laptop-orch", txt)
        self.assertNotIn("session_web0rch", txt)
        self.assertTrue(tmp.exists())


if __name__ == "__main__":
    unittest.main()
