"""CL62: the orchestrator cannot end a turn while a "start now" step is neither launched nor held.

The owner, 2026-11-27: "Why didn't you launch it when it told you? This needs a permanent fix that doesn't depend on
your stupidity." On a fixture board (no real board is read or written):
- two start-now steps, one launched, one not: the orchestrator's Stop is refused once, naming the one not launched and
  its launch command; a second stop in the same turn passes; a session that is not the orchestrator is never refused;
- after `kit.py hold <post> --why "..."` (recorded on the post) the Stop passes, and the queue shows it held;
- a step the split on `_orchestrator` gives another orchestrator counts as held for this one;
- a stale hand-over claim ("M2 and M3 wait on M1", M1 landed) is flagged first in the orchestrator's resume;
- the queue's out-of-order refusal stays, and a held step is never the one it says goes first.

Run via: python3 -m unittest discover -s tests -p test_cl62_start_now_stop.py
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_kit():
    spec = importlib.util.spec_from_file_location("kit_cl62", str(ROOT / "scripts" / "kit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def step(part, title, task="", created="2026-11-27T01:00:00Z", status="planned", files=(), **kw):
    d = {"part": part, "title": title, "task": task, "train": "t", "status": status, "created_at": created, "column": "info"}
    d["subtasks"] = [{"n": 1, "title": title, "files": list(files or ["%s.py" % part.lower()])}]
    d.update(kw)
    return d


def board(**over):
    posts = {
        "_orchestrator": {"title": "Ask the orchestrator", "column": "info"},
        "s1": step("S1", "First step", "Adds a view.", created="2026-11-27T01:00:00Z"),
        "s2": step("S2", "Second step", "Adds a report.", created="2026-11-27T01:05:00Z"),
    }
    posts.update(over)
    return posts


class StartNowGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load_kit()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gate(self, posts, me="session_me"):
        return self.kit.start_now_unlaunched(posts, me=me, areas={}, root=self.tmp)

    def test_one_launched_one_not_names_the_one_not_launched(self):
        posts = board()
        posts["s1"]["status"] = "in progress"           # launched: running on the board
        rows = self.gate(posts)
        self.assertEqual([r["id"] for r in rows], ["s2"], rows)
        txt = self.kit.start_now_refusal(rows)
        self.assertIn("S2 `s2`", txt)
        self.assertIn("python3 -I .kit/kit.py launch s2", txt)
        self.assertIn("kit.py hold <post> --why", txt)
        self.assertNotIn("`s1`", txt)

    def test_a_launch_mark_here_counts_as_launched(self):
        os.makedirs(os.path.join(self.tmp, ".kit", "run", "launch"))
        Path(self.tmp, ".kit", "run", "launch", "s2.json").write_text("{}")
        self.assertEqual([r["id"] for r in self.gate(board())], ["s1"])

    def test_after_hold_it_passes_and_the_queue_shows_it_held(self):
        posts = board()
        posts["s1"]["status"] = "in progress"
        posts["s2"]["held"] = {"why": "waits for the owner's answer on the map format", "at": "2026-11-27T03:00:00Z"}
        posts["s2"]["status"] = "held: waits for the owner's answer on the map format"
        self.assertEqual(self.gate(posts), [])
        self.assertEqual(self.kit.start_now_refusal([]), "")
        lines = "\n".join(self.kit.queue_lines(posts, areas={}))
        self.assertIn("S2 `s2` [held: waits for the owner's answer", lines)

    def test_a_split_on_orchestrator_counts_as_held_for_this_one_only(self):
        posts = board()
        posts["_orchestrator"]["split"] = {"session_other": ["S2"], "session_me": ["S1"]}
        self.assertEqual([r["id"] for r in self.gate(posts, me="session_me")], ["s1"])
        self.assertEqual([r["id"] for r in self.gate(posts, me="session_other")], ["s2"])

    def test_hold_records_the_reason_on_the_post_and_release_undoes_it(self):
        post = dict(step("S2", "Second step"), status="planned")
        wrote = []
        kit = self.kit
        saved = (kit._board_request, kit._owner_write, kit._resolve_board_token)
        kit._board_request = lambda url, path, token=None, **k: {"data": dict(post)}
        kit._owner_write = lambda pid, fields, url=None, token=None, drop=(): (
            wrote.append((pid, fields, drop)) or {"ok": True, "post": pid, "data": dict(post, **fields)})
        kit._resolve_board_token = lambda t=None: "tok"
        try:
            self.assertFalse(kit.hold("s2", why="", url="http://x")["ok"])          # a hold says why
            r = kit.hold("s2", why="waits for the owner's answer", url="http://x")
            self.assertTrue(r["ok"])
            pid, fields, _ = wrote[-1]
            self.assertEqual(pid, "s2")
            self.assertEqual(fields["held"]["why"], "waits for the owner's answer")
            self.assertTrue(fields["status"].startswith("held: "))
            self.assertEqual(fields["held_prev_status"], "planned")
            self.assertEqual(kit.step_hold(dict(post, **fields)), "waits for the owner's answer")
            post.update(fields)
            kit.hold("s2", release=True, url="http://x")
            pid, fields, drop = wrote[-1]
            self.assertEqual(fields, {"status": "planned"})
            self.assertEqual(set(drop), {"held", "held_prev_status"})
        finally:
            kit._board_request, kit._owner_write, kit._resolve_board_token = saved

    def test_the_out_of_order_refusal_stays_and_never_names_a_held_step(self):
        posts = board()
        self.assertIn("goes first", self.kit.order_problem(posts, "s2", areas={}) or "")
        posts["s1"]["held"] = {"why": "the owner asked to wait for his tests"}
        self.assertIsNone(self.kit.order_problem(posts, "s2", areas={}))


class StaleHandover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load_kit()

    def test_a_claim_whose_step_landed_is_flagged_with_start_now(self):
        posts = {
            "_orchestrator": {"title": "Ask the orchestrator"},
            "m1": step("M1", "A map", status="complete", landed="0c8748a", landed_at="2026-11-27T02:18:41Z",
                       created="2026-11-26T19:00:00Z"),
            "m2": step("M2", "Install draws the first map", created="2026-11-26T20:00:00Z"),
            "m3": step("M3", "Done keeps the map", created="2026-11-26T20:05:00Z"),
            "m5": step("M5", "Something else", created="2026-11-26T20:10:00Z"),
            "m6": step("M6", "Waits on a running step", created="2026-11-26T20:15:00Z", task="Builds on M5."),
        }
        comments = {
            "c1": {"post_id": "_orchestrator", "from": "orchestrator", "created_at": "2026-11-26T22:31:43Z",
                   "text": "M5 is reviewed. M2 and M3 wait on M1: my question in chat. M6 waits on M5."},
            "c2": {"post_id": "_orchestrator", "from": "owner", "created_at": "2026-11-26T22:40:00Z",
                   "text": "M7 waits on M1"},
        }
        out = self.kit.stale_handover_lines(posts, comments, areas={})
        txt = "\n".join(out)
        self.assertTrue(out and out[0].startswith("## First: hand-over lines the board contradicts"), out)
        self.assertIn("M2 waits on M1 (said 22:31): M1 landed 02:18, start now: `python3 -I .kit/kit.py launch m2`", txt)
        self.assertIn("M3 waits on M1", txt)
        self.assertNotIn("M6 waits on M5", txt)        # M5 is still open: the claim holds
        self.assertNotIn("M7", txt)                    # the owner's words are not a hand-over
        self.assertEqual(self.kit.stale_handover_lines(posts, {}, areas={}), [])


class StopHook(unittest.TestCase):
    """The hook end to end: a project with the kit and a board of 2 start-now steps, one launched, one not."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".kit" / "hooks").mkdir(parents=True)
        (self.tmp / ".kit" / "board_data").mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "kit.py", self.tmp / ".kit" / "kit.py")
        for h in ("kit_queue_stop.py", "kit_config.py", "kit_orchestrator_guard.py", "kit_stop.py"):
            shutil.copy(ROOT / "kit" / "hooks" / h, self.tmp / ".kit" / "hooks" / h)
        (self.tmp / ".kit" / "config.json").write_text(json.dumps({"project": "fixture", "kit": {}}))
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        posts = board()
        posts["s1"]["status"] = "in progress"
        self.write(posts)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, posts):
        db = self.tmp / ".kit" / "board_data" / "board.db"
        if db.exists():
            db.unlink()
        c = sqlite3.connect(str(db))
        c.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, data TEXT)")
        for pid, p in posts.items():
            c.execute("INSERT INTO docs VALUES ('posts', ?, ?)", (pid, json.dumps(p)))
        c.commit()
        c.close()

    def stop(self, role="orchestrator", turn="u1"):
        tp = self.tmp / "t.jsonl"
        tp.write_text(json.dumps({"type": "user", "uuid": turn, "message": {"content": "go"}}) + "\n")
        env = dict(os.environ, KIT_ROLE=role, KIT_SESSION_ID="session_me", CLAUDE_PROJECT_DIR=str(self.tmp),
                   TMPDIR=str(self.tmp))
        env.pop("CLAUDE_CODE_REMOTE_SESSION_ID", None)
        data = {"session_id": "cl62-test", "transcript_path": str(tp), "cwd": str(self.tmp), "hook_event_name": "Stop"}
        return subprocess.run([sys.executable, "-I", str(self.tmp / ".kit" / "hooks" / "kit_queue_stop.py")],
                              input=json.dumps(data), capture_output=True, text=True, env=env, timeout=90)

    def test_refused_once_naming_the_step_then_passes_after_hold(self):
        r = self.stop()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("S2 `s2`", r.stderr)
        self.assertIn("python3 -I .kit/kit.py launch s2", r.stderr)
        self.assertNotIn("`s1`", r.stderr)
        self.assertEqual(self.stop().returncode, 0)                 # once per turn
        self.assertEqual(self.stop(turn="u2").returncode, 2)        # a new turn: refused again
        self.assertEqual(self.stop(role="part", turn="u3").returncode, 0)   # only the orchestrator
        posts = board()
        posts["s1"]["status"] = "in progress"
        posts["s2"].update(held={"why": "the owner asked to wait for his tests"}, status="held: the owner asked to wait")
        self.write(posts)
        r = self.stop(turn="u4")
        self.assertEqual(r.returncode, 0, r.stderr)
        g = subprocess.run([sys.executable, "-I", str(self.tmp / ".kit" / "kit.py"), "queue", "--gate"], cwd=str(self.tmp),
                           capture_output=True, text=True, env=dict(os.environ, KIT_STALE_OK="1", KIT_SESSION_ID="session_me"),
                           timeout=60)
        self.assertEqual(g.returncode, 0, g.stdout + g.stderr)


if __name__ == "__main__":
    unittest.main()
