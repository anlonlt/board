"""CL50: a part launched with `claude --bg` knows its own step, whatever its environment says.

CL44's part ran with M1's KIT_POST (a `claude --bg` launch can start in a warm process made for an earlier launch), and
its saves were labelled with M1's name. Here: the launch records the worktree's step before the session starts; the save,
`kit.py checkpoint` and the hooks read that first, KIT_POST only when it agrees; a disagreement is said once.

Run via: python3 -m unittest tests.test_bg_step -v
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_landing import Repo  # noqa: E402
from test_launch import Base, POST, TOKEN, TRAINS, make_repo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
M1, CL44 = "m1-the-other-step", "cl44-this-step"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LaunchRecordsTheStep(Base):
    def test_the_worktree_names_its_step_before_claude_starts(self):
        repo = make_repo(self.tmp / "repo", {"project": "Board", "board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local"}})
        self.local("posts/cl99-launch-me", POST)
        self.local("posts/_trains", TRAINS)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        seen = self.tmp / "seen.txt"
        f = bindir / "claude"            # what the session finds at its start: the step file, and the env it was given
        f.write_text("#!/bin/sh\n{ cat .kit/run/step; echo \"env=$KIT_POST\"; } > %s\n" % seen)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        k = self.kit_in(repo)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=False, gate=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(seen.read_text().splitlines(), ["cl99-launch-me", "env=cl99-launch-me"])
        self.assertEqual((Path(r["worktree"]) / ".kit" / "run" / "step").read_text().strip(), "cl99-launch-me")


class WarmSpare(Repo):
    """CL44's case replayed: the worktree is CL44's; the process came with M1's environment."""

    def setUp(self):
        super().setUp()
        self.g("checkout", "-q", "-b", "cl44")
        Path(self.d, ".kit", "run").mkdir(parents=True, exist_ok=True)
        Path(self.d, ".kit", "run", "step").write_text(CL44 + "\n")

    def checkpoint(self, **env):
        self.put("work.txt", "more %s\n" % len(self.g("log", "--oneline").stdout), commit=False)
        r = self.run_("kit.py", "checkpoint", "--message", "saved", KIT_POST=M1, KIT_ROLE="part", **env)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_save_is_labelled_with_the_worktrees_step_not_the_environments(self):
        res = self.checkpoint()
        self.assertTrue(res["committed"], res)
        subject = self.g("log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(subject, "checkpoint(%s): saved" % CL44)
        self.assertNotIn(M1, self.g("log", "--format=%B").stdout)

    def test_the_disagreement_is_said_once(self):
        first, second = self.checkpoint(), self.checkpoint()
        self.assertIn("names step %s (KIT_POST), but this worktree is step %s" % (M1, CL44), first.get("step_note", ""))
        self.assertNotIn("step_note", second)

    def test_the_hooks_name_the_worktrees_step(self):
        os.environ["KIT_POST"] = M1
        self.addCleanup(os.environ.pop, "KIT_POST", None)
        sys.path.insert(0, str(ROOT / "kit" / "hooks"))
        tt = load(ROOT / "kit" / "hooks" / "kit_task_train.py", "kit_task_train_cl50")
        shutil_copy(ROOT / "kit" / "landing.py", Path(self.d, ".kit", "landing.py"))
        st = {}
        self.assertEqual(tt.part_post({"cwd": self.d}, st), CL44)
        self.assertIn("this worktree is step %s" % CL44, st.get("step_note", ""))
        st2 = {}
        self.assertEqual(tt.part_post({"cwd": self.d}, st2), CL44)
        self.assertNotIn("step_note", st2)                                   # once

    def test_agreeing_or_unrecorded_keeps_the_old_way(self):
        la = load(ROOT / "kit" / "landing.py", "landing_cl50")
        os.environ["KIT_POST"] = CL44
        self.addCleanup(os.environ.pop, "KIT_POST", None)
        self.assertEqual(la.own_step(self.d), (CL44, ""))                    # agrees: nothing said
        os.remove(os.path.join(self.d, ".kit", "run", "step"))
        os.environ["KIT_POST"] = M1
        self.assertEqual(la.own_step(self.d), (M1, ""))                      # nothing recorded: the environment
        step, note = la.own_step(self.d, branch_post=CL44)                   # the branch's post, when it is known
        self.assertEqual(step, CL44)
        self.assertIn(M1, note)


def shutil_copy(a, b):
    Path(b).write_bytes(Path(a).read_bytes())


if __name__ == "__main__":
    unittest.main()
