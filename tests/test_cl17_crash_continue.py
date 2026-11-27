"""CL17: a part that crashes is continued by a fresh session from its last checkpoint, without the owner re-explaining.

Real git (a bare origin, the part's worktree), the real board server, a fake `claude` whose launched "part" is a real
process that is killed mid-step (SIGKILL). The part checkpoints once (`checkpoint <post>`: a WIP commit on its branch,
pushed, the post stamped), then dies with more work uncommitted. Its worktree and local branch are then deleted (the
laptop is gone): the relaunch must find the work on origin. `resume` lists the checkpoint, the checklist's next item
and the relaunch line; `launch --continue` starts a new part on that branch, told to continue from the checklist.

Run via: python3 -m unittest tests.test_cl17_crash_continue -v
"""
import json
import os
import signal
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_launch import Base, POST, TRAINS, git, make_repo  # noqa: E402
from test_handoff import TOKEN  # noqa: E402

PID = "cl99-launch-me"
PIECES = [{"n": 1, "title": "write the parser", "status": "open"},
          {"n": 2, "title": "wire it into the CLI", "status": "open"},
          {"n": 3, "title": "tests", "status": "open"}]


class CrashedPartContinues(Base):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.tmp / "repo", {"project": "Board", "board_url": "http://127.0.0.1:8765",
                                                  "kit": {"runtime": "local"}})
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main", "work")
        self.local("posts/" + PID, dict(POST, subtasks=PIECES))
        self.local("posts/_trains", TRAINS)
        # the fake claude: `agents --json` lists what alive.json says; `--bg` logs its args and starts the part, a real
        # process that runs until it is killed
        self.alive = self.tmp / "alive.json"
        self.alive.write_text("[]")
        self.log = self.tmp / "claude.log"
        self.pidfile = self.tmp / "part.pid"
        bindir = self.tmp / "bin"
        bindir.mkdir()
        f = bindir / "claude"
        f.write_text("#!/bin/sh\n"
                     "if [ \"$1\" = agents ]; then cat %s; exit 0; fi\n"
                     "printf '%%s\\n' \"$PWD\" \"$@\" > %s\n"
                     "sleep 600 >/dev/null 2>&1 &\n"
                     "echo $! > %s\n" % (self.alive, self.log, self.pidfile))
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        self.addCleanup(self.kill_part)

    def kill_part(self):
        try:
            os.kill(int(self.pidfile.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass

    def post(self):
        return self.local("posts/" + PID)["data"]

    def sub(self, n, status):
        p = self.post()
        p["subtasks"] = [dict(x, status=status) if x["n"] == n else x for x in p["subtasks"]]
        self.local("posts/" + PID, p)

    def crash_mid_step(self):
        """Launch, do piece 1, checkpoint, start piece 2, then the part is killed and its machine is gone."""
        k = self.kit_in(self.repo)
        r = k.launch(PID, url=self.url, token=TOKEN, web=False, gate=False)
        self.assertTrue(r["ok"], r)
        wt = Path(r["worktree"])
        part_pid = int(self.pidfile.read_text())
        self.alive.write_text(json.dumps([{"name": self.post()["session_id"]}]))
        (wt / "parser.py").write_text("def parse(s):\n    return s.split()\n")
        self.sub(1, "done")
        self.sub(2, "doing")
        kw = self.kit_in(wt)
        ck = kw.checkpoint(post=PID, message="piece 1 done", push=True, stamp=True, url=self.url, token=TOKEN)
        self.assertTrue(ck["committed"] and ck["pushed"], ck)
        self.assertEqual(ck["stamped"]["sha"], ck["sha"])
        (wt / "cli.py").write_text("half written\n")              # after the checkpoint: lost with the machine
        os.kill(part_pid, signal.SIGKILL)                          # the crash
        for _ in range(50):
            if subprocess.run(["kill", "-0", str(part_pid)], capture_output=True).returncode != 0:
                break
            time.sleep(0.1)
        self.alive.write_text("[]")
        git(self.repo, "worktree", "remove", "--force", str(wt))   # the laptop is gone: only origin has the work
        git(self.repo, "branch", "-D", "cl99")
        return ck

    def test_resume_lists_checkpoint_checklist_and_relaunch_line(self):
        ck = self.crash_mid_step()
        p = self.post()
        self.assertEqual(p["checkpoint"]["branch"], "cl99")
        self.assertTrue(p["checkpoint"]["pushed"])
        k = self.kit_in(self.repo)
        out = k.resume(str(self.tmp), db_path=str(self.board_dir / ".kit" / "board_data" / "board.db"), alive=set())
        block = out.split("`%s`" % PID, 1)[1].split("\n- ", 1)[0]
        self.assertIn("not running", block)
        self.assertIn("last checkpoint `%s` on `cl99`" % ck["sha"], block)
        self.assertIn("pushed", block)
        self.assertIn("checklist 1/3 done · doing: 2. wire it into the CLI · next: 3. tests", block)
        self.assertIn("relaunch: `python3 -I .kit/kit.py launch %s --continue`" % PID, block)

    def test_a_relaunched_part_continues_from_the_checkpoint(self):
        ck = self.crash_mid_step()
        k = self.kit_in(self.repo)
        plain = k.launch(PID, url=self.url, token=TOKEN, web=False, gate=False, take=True)
        self.assertTrue(plain["ok"], plain)        # no local branch left: a plain launch would start over from `work`
        self.kill_part()
        git(self.repo, "worktree", "remove", "--force", plain["worktree"])
        git(self.repo, "branch", "-D", "cl99")
        self.assertFalse((Path(plain["worktree"]) / "parser.py").exists())

        r = k.launch(PID, url=self.url, token=TOKEN, web=False, gate=False, cont=True)
        self.assertTrue(r["ok"], r)
        wt = Path(r["worktree"])
        self.assertEqual(git(wt, "rev-parse", "--short", "HEAD").strip(), ck["sha"])     # the branch has the work
        self.assertTrue((wt / "parser.py").exists())
        self.assertFalse((wt / "cli.py").exists())                 # what came after the checkpoint is lost, as said
        args = self.log.read_text()
        self.assertIn("continue from the checklist in your post", args)
        self.assertIn("next: 2. wire it into the CLI", args)
        self.assertIn("your branch `cl99` has your work (last checkpoint %s" % ck["sha"], args)
        p = self.post()
        self.assertEqual(p["status"], "in progress")
        self.assertEqual(p["relaunched"]["n"], 1)
        self.assertEqual([x["status"] for x in p["subtasks"]], ["done", "doing", "open"])   # the post kept the checklist

    def test_a_running_part_is_not_relaunched(self):
        k = self.kit_in(self.repo)
        self.assertTrue(k.launch(PID, url=self.url, token=TOKEN, web=False, gate=False)["ok"])
        self.alive.write_text(json.dumps([{"name": self.post()["session_id"]}]))
        k._agents["at"] = None                     # `claude agents` is kept 5 s: ask it again
        r = k.launch(PID, url=self.url, token=TOKEN, web=False, gate=False, cont=True)
        self.assertFalse(r["ok"])
        self.assertIn("still runs", r["error"])

    def test_a_step_waiting_on_the_owner_is_not_relaunched(self):
        self.crash_mid_step()
        self.local("posts/cl99-q", {"title": "CL99: keep the old flag?", "column": "question", "status": "open",
                                    "created_at": "2026-11-23T00:02:00Z"})
        k = self.kit_in(self.repo)
        out = k.resume(str(self.tmp), db_path=str(self.board_dir / ".kit" / "board_data" / "board.db"), alive=set())
        block = out.split("`%s`" % PID, 1)[1].split("\n- ", 1)[0]
        self.assertIn("waits on the owner (question `cl99-q` is open)", block)
        self.assertNotIn("--continue", block)
        self.assertEqual(k.waits_on_owner(PID, self.post(), {"cl99-q": self.local("posts/cl99-q")["data"]}),
                         "question `cl99-q` is open")

    def test_the_wip_commit_never_lands_on_the_trunk(self):
        k = self.kit_in(self.repo)
        git(self.repo, "checkout", "-q", "main")
        (self.repo / "x.txt").write_text("x\n")
        r = k.checkpoint(post=PID, push=True, stamp=True, url=self.url, token=TOKEN)
        self.assertFalse(r["ok"])
        self.assertIn("refusing", r["why"])
        self.assertNotIn("checkpoint", self.post())


if __name__ == "__main__":
    unittest.main()
