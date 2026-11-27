"""Work reaches the trunk after its review, and git while it is being done: real repositories, a real board.

Run via: python3 -m unittest tests.test_landing -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_post_gate import BOOT, STEP, TOKEN, api_req, g as gate, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("kit/board/board_cli.py", "kit/board/post_gate.py", "kit/board/board_token.py", "scripts/kit.py", "kit/landing.py", "kit/end_session.py")


class Repo(unittest.TestCase):
    conf = {"trunk_push": True, "main_branch": "main", "wip": "push", "review": {"auto": "all", "train_every": 0}}

    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY", "GIT_"))}
        self.env.update(KIT_NO_LAUNCH="1", GIT_CONFIG_GLOBAL=os.devnull, KIT_BOARD_URL="http://127.0.0.1:9")
        self.o = os.path.join(self.t, "o.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.o], check=True)
        self.d = self.clone("d")
        self.put("calc.py", "def add(a, b):\n    return a + b\n")
        self.g("push", "-q", "origin", "main")

    def clone(self, name):
        d = os.path.join(self.t, name)
        subprocess.run(["git", "clone", "-q", self.o, d], capture_output=True)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            self.g("config", k, v, d=d)
        self.g("checkout", "-q", "-B", "main", d=d)
        if self.g("ls-remote", "origin", "main", d=d).stdout:
            self.g("pull", "-q", "origin", "main", d=d)
        os.makedirs(os.path.join(d, ".kit"), exist_ok=True)
        for rel in SCRIPTS:
            shutil.copy(str(ROOT / rel), os.path.join(d, ".kit", Path(rel).name))
        Path(d, ".kit", "config.json").write_text(json.dumps({"project": "D", "kit": self.conf}))
        with open(os.path.join(d, ".git", "info", "exclude"), "a") as fh:
            fh.write(".kit/\n")
        return d

    def g(self, *a, d=None, env=None):
        return subprocess.run(["git", "-C", d or self.d] + list(a), capture_output=True, text=True, env=dict(self.env, **(env or {})))

    def put(self, f, text, d=None, commit=True):
        p = Path(d or self.d, f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if commit:
            self.g("add", f, d=d)
            self.g("commit", "-qm", f, d=d)

    def run_(self, script, *a, d=None, data=None, **env):
        return subprocess.run([sys.executable, "-I", os.path.join(d or self.d, ".kit", script)] + list(a), capture_output=True, text=True, input=data,
                              env=dict(self.env, **env), cwd=d or self.d, timeout=120)

    def remote(self, ref="main"):
        return (self.g("ls-remote", "origin", "refs/heads/" + ref).stdout.split() or [""])[0]

    def has(self, f, ref="main"):
        self.g("fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
        return self.g("cat-file", "-e", "origin/%s:%s" % (ref, f)).returncode == 0


class Board(Repo):
    def setUp(self):
        super().setUp()
        self.srv = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), os.path.join(self.d, ".kit", "board_data")],
                                    stdout=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(lambda: (self.srv.kill(), self.srv.wait(), self.srv.stdout.close()))
        self.url = "http://127.0.0.1:%s" % self.srv.stdout.readline().strip()
        self.env.update(KIT_BOARD_URL=self.url, KIT_BOARD_TOKEN=TOKEN)
        self.g("checkout", "-q", "-b", "s1")
        self.put("new.txt", "work\n")
        self.g("push", "-q", "-u", "origin", "s1")
        self.post("s1", dict(STEP, status="in progress", branch="s1"))

    def post(self, pid, d):
        api_req(self.url, "db/posts/" + pid, {"data": d}, token=TOKEN, method="PUT")

    def get(self, pid="s1"):
        return api_req(self.url, "doc/posts/" + pid, token=TOKEN)["data"]

    def patch(self, pid="s1", **f):
        self.post(pid, dict(self.get(pid), **f))

    def complete(self, review=None):
        r = self.run_("kit.py", "done", "s1")
        self.assertIn("OK: s1", r.stdout, r.stdout + r.stderr)
        r = self.run_("kit.py", "step", "complete", "s1")
        self.assertIn('"ok": true', r.stdout, r.stdout + r.stderr)
        if review:
            self.patch(review=review)

    def land(self):
        return self.run_("kit.py", "land", "s1")


class AfterReview(Board):
    def test_a_parts_end_pushes_its_branch_only(self):
        before = self.remote()
        r = self.run_("end_session.py")
        self.assertEqual(r.returncode, 0)
        self.assertIn("waits for the review of s1", r.stdout)
        self.assertEqual(self.remote(), before)
        self.assertEqual(self.remote("s1"), self.g("rev-parse", "HEAD").stdout.strip())

    def test_the_whole_way(self):
        self.assertIn("not complete yet", self.land().stdout)
        self.complete()
        self.assertEqual(self.get()["review_commit"], self.remote("s1"))
        r = self.land()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no verdict yet", r.stdout)
        self.patch(review="redirect")
        self.assertIn("it said redirect", self.land().stdout)
        self.assertFalse(self.has("new.txt"))
        self.patch(review="continue")
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.has("new.txt"))
        self.assertTrue(self.get().get("landed"))
        self.assertEqual(self.remote("s1"), "")                                  # its branch is gone: all of it is on main
        self.assertIn("already on the trunk", self.land().stdout)
        self.assertEqual(self.g("worktree", "list").stdout.count("\n"), 1)       # nothing left behind

    def test_a_commit_after_the_review_does_not_land(self):
        self.complete("continue")
        self.put("late.txt", "x\n")
        self.g("push", "-q", "origin", "s1")
        r = self.land()
        self.assertEqual(r.returncode, 1)
        self.assertIn("after its review", r.stdout)
        self.assertFalse(self.has("new.txt"))

    def test_a_trunk_merged_in_after_the_review_lands(self):
        self.complete("continue")
        o = self.clone("other")
        self.put("other.txt", "o\n", d=o)
        self.g("push", "-q", "origin", "main", d=o)
        self.g("fetch", "-q", "origin")
        self.g("merge", "-q", "--no-edit", "origin/main")
        self.g("push", "-q", "origin", "s1")
        self.assertEqual(self.land().returncode, 0)
        self.assertTrue(self.has("new.txt") and self.has("other.txt"))

    def test_a_conflict_pushes_nothing(self):
        self.complete("continue")
        o = self.clone("other")
        self.put("new.txt", "theirs\n", d=o)
        self.g("push", "-q", "origin", "main", d=o)
        before = self.remote()
        r = self.land()
        self.assertEqual(r.returncode, 1)
        self.assertIn("conflict with main in new.txt", r.stdout)
        self.assertEqual(self.remote(), before)
        self.assertFalse(self.get().get("landed"))

    def test_two_that_pass_alone_and_fail_together(self):
        self.complete("continue")
        o = self.clone("other")
        self.put("calc.py", "def add(a, b):\n    return a - b\n", d=o)
        self.g("push", "-q", "origin", "main", d=o)
        before = self.remote()
        r = self.land()
        self.assertEqual(r.returncode, 1)
        self.assertIn("fails merged with main", r.stdout)
        self.assertEqual(self.remote(), before)

    def test_a_check_that_cannot_run_in_a_checkout_does_not_hold_it(self):
        self.patch(check="test -e data.local && python3 -c \"import calc; assert calc.add(2, 2) == 4\"")
        self.put("data.local", "big\n", commit=False)
        self.complete("continue")
        o = self.clone("other")
        self.put("other.txt", "o\n", d=o)
        self.g("push", "-q", "origin", "main", d=o)
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("cannot run in a bare checkout", r.stdout)

    def test_a_step_with_no_review_lands_at_complete(self):
        self.patch(review_auto=False)
        self.complete()
        self.assertTrue(self.has("new.txt"))
        self.assertTrue(self.get().get("landed"))

    def test_resume_says_what_is_not_on_the_trunk(self):
        self.complete()
        self.post("s2", dict(STEP, part="S2", status="planned"))
        self.assertIn("no verdict yet", self.run_("kit.py", "resume", "--role", "part", "--post", "s2").stdout)
        self.patch(review="continue")
        self.assertIn("kit.py land s1", self.run_("kit.py", "resume", "--role", "orchestrator").stdout)
        self.land()
        self.assertNotIn("kit.py land s1", self.run_("kit.py", "resume", "--role", "orchestrator").stdout)

    def test_a_step_from_before_the_rule_is_nobodys_to_hold(self):
        self.patch(status="complete")
        got = json.loads(self.run_("kit.py", "landing").stdout)["steps"]
        self.assertEqual([(s["held"], s["ready"]) for s in got], [(False, False)])
        self.patch(status="in progress")
        self.assertTrue(json.loads(self.run_("kit.py", "landing", "--branch", "s1").stdout)["steps"][0]["held"])
        self.assertEqual(json.loads(self.run_("kit.py", "landing", "--branch", "zz").stdout)["steps"], [])


class AtDone(Board):
    conf = dict(Board.conf, land="at_done")

    def test_the_old_way_stays_possible(self):
        self.assertEqual(self.run_("end_session.py").returncode, 0)
        self.assertTrue(self.has("new.txt"))


class NoPermission(Board):
    conf = dict(Board.conf, trunk_push=False, land="after_review")

    def test_landing_needs_the_owners_permission(self):
        self.complete("continue")
        self.assertIn("kit.trunk_push", self.land().stdout)
        self.assertFalse(self.has("new.txt"))


class PrePush(Board):
    def setUp(self):
        super().setUp()
        r = self.run_("landing.py", "setup")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "pre-push: set"))

    def to_main(self, *a):
        return self.g("push", *(a or ("origin", "HEAD:main")))

    def test_unreviewed_work_is_refused_whoever_pushes(self):
        r = self.to_main()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not complete yet", r.stderr)
        self.complete()
        r = self.to_main()
        self.assertIn("holds step S1 (s1), which is not reviewed yet", r.stderr)
        self.assertFalse(self.has("new.txt"))
        self.assertEqual(self.g("push", "origin", "s1").returncode, 0)           # its branch: always
        self.patch(review="continue")
        self.assertEqual(self.to_main().returncode, 0)

    def test_other_work_and_a_trunk_that_holds_it_already_pass(self):
        self.complete("redirect")
        self.g("checkout", "-q", "main")
        self.put("card.txt", "c\n")
        self.assertEqual(self.g("push", "origin", "main").returncode, 0)
        self.g("merge", "-q", "--no-edit", "s1")
        self.assertNotEqual(self.g("push", "origin", "main").returncode, 0)

    def test_the_trunk_is_never_deleted_or_rewound(self):
        r = self.g("push", "origin", ":main")
        self.assertIn("deletes main", r.stderr)
        self.g("checkout", "-q", "main")
        self.g("commit", "-q", "--amend", "-m", "other")
        self.assertIn("rewrites main", self.g("push", "--force", "origin", "main").stderr)

    def test_a_board_that_cannot_be_read_blocks_nobody(self):
        self.complete()
        os.remove(os.path.join(self.d, ".kit", "kit.py"))
        r = self.to_main()
        self.assertEqual(r.returncode, 0)
        self.assertIn("cannot be read", r.stderr)

    def test_a_hook_of_the_projects_own_runs_first(self):
        p = os.path.join(self.d, ".git", "hooks", "pre-push")
        Path(p).write_text("#!/bin/sh\ncat > %s/seen\nexit 3\n" % self.t)
        os.chmod(p, 0o755)
        self.run_("landing.py", "setup")
        self.assertNotEqual(self.g("push", "origin", "s1:refs/heads/zz").returncode, 0)
        self.assertIn("refs/heads/zz", Path(self.t, "seen").read_text())
        Path(p + ".local").write_text("#!/bin/sh\nexit 0\n")
        self.assertEqual(self.g("push", "origin", "s1:refs/heads/zz").returncode, 0)
        self.assertEqual(self.run_("landing.py", "setup").stdout.strip(), "pre-push: set")

    def test_a_project_with_its_own_hooks_folder_is_told_not_touched(self):
        os.makedirs(os.path.join(self.d, ".husky"))
        self.g("config", "core.hooksPath", ".husky")
        r = self.run_("landing.py", "setup")
        self.assertEqual(r.returncode, 1)
        self.assertIn("core.hooksPath", r.stdout)
        self.assertEqual(os.listdir(os.path.join(self.d, ".husky")), [])
        Path(self.d, ".husky", "pre-push").write_text("python3 -I .kit/landing.py pre-push\n")
        self.assertEqual(self.run_("landing.py", "setup").returncode, 0)


class Save(Repo):
    def setUp(self):
        super().setUp()
        self.g("checkout", "-q", "-b", "s1")
        self.put("a.txt", "one\n")
        self.g("push", "-q", "-u", "origin", "s1")

    def save(self, d=None):
        return self.run_("landing.py", "save", d=d).stdout.strip()

    def state(self):
        return (self.g("rev-parse", "HEAD").stdout, self.g("status", "--porcelain").stdout, self.g("diff", "--cached").stdout,
                self.g("symbolic-ref", "HEAD").stdout)

    def test_all_of_it_and_nothing_touched(self):
        self.put("a.txt", "two\n", commit=False)
        self.put("untracked.txt", "u\n", commit=False)
        self.put("staged.txt", "s\n", commit=False)
        self.g("add", "staged.txt")
        self.put("sub/é ü.txt", "e\n", commit=False)
        os.remove(os.path.join(self.d, "calc.py"))
        before = self.state()
        self.assertIn("on origin/wip/s1", self.save())
        self.assertEqual(self.state(), before)
        for f in ("untracked.txt", "staged.txt", "sub/é ü.txt"):
            self.assertTrue(self.has(f, "wip/s1"), f)
        self.assertFalse(self.has("calc.py", "wip/s1"))
        self.assertEqual(self.g("show", "origin/wip/s1:a.txt").stdout, "two\n")
        self.assertEqual(self.remote("s1"), before[0].strip())                   # the branch itself did not move

    def test_secrets_big_and_ignored_files_stay_here(self):
        self.put(".gitignore", "ign.txt\n")
        for f in (".env", "x.pem", "deploy/service-credentials.json", "ign.txt", "my.keystore", ".env.local", "ID_RSA"):
            self.put(f, "s3cret\n", commit=False)
        self.put("big.bin", "x" * (2 * 1024 * 1024 + 1), commit=False)
        self.put("ok.txt", "ok\n", commit=False)
        self.save()
        got = self.g("ls-tree", "-r", "--name-only", "origin/wip/s1").stdout.split()
        self.assertEqual(sorted(got), [".gitignore", "a.txt", "calc.py", "ok.txt"])

    def test_saves_chain_and_are_never_forced(self):
        self.put("a.txt", "two\n", commit=False)
        self.save()
        first = self.remote("wip/s1")
        self.assertIn(first[:9], self.save())                                    # nothing new: the same save
        self.put("a.txt", "three\n", commit=False)
        self.save()
        second = self.remote("wip/s1")
        self.assertNotEqual(first, second)
        self.assertEqual(self.g("merge-base", "--is-ancestor", first, second).returncode, 0)

    def test_two_clones_on_one_branch_lose_nothing(self):
        o = self.clone("other")
        self.g("fetch", "-q", "origin", d=o)
        self.g("checkout", "-q", "s1", d=o)
        self.put("theirs.txt", "t\n", d=o, commit=False)
        self.save(d=o)
        theirs = self.remote("wip/s1")
        self.put("ours.txt", "o\n", commit=False)
        self.assertIn("on origin/wip/s1", self.save())
        self.g("fetch", "-q", "origin", "+refs/heads/wip/s1:refs/remotes/origin/wip/s1")
        self.assertEqual(self.g("merge-base", "--is-ancestor", theirs, self.remote("wip/s1")).returncode, 0)
        self.assertEqual(len(self.g("rev-list", "--parents", "-1", self.remote("wip/s1")).stdout.split()), 3)    # ours and theirs
        self.assertTrue(self.has("ours.txt", "wip/s1"))

    def test_nothing_to_save(self):
        self.assertIn("all of it is committed", self.save())
        self.assertEqual(self.remote("wip/s1"), "")

    def test_a_commit_not_pushed_is_saved(self):
        self.put("b.txt", "b\n")
        self.save()
        self.assertTrue(self.has("b.txt", "wip/s1"))

    def test_the_trunk_saves_under_the_sessions_name(self):
        self.g("checkout", "-q", "main")
        self.put("m.txt", "m\n", commit=False)
        self.run_("landing.py", "save", CLAUDE_CODE_SESSION_ID="abc-12345678")
        self.assertTrue(self.has("m.txt", "wip/main-12345678"))

    def test_the_next_session_finds_it(self):
        self.put("a.txt", "two\n", commit=False)
        os.remove(os.path.join(self.d, "calc.py"))
        self.save()
        o = self.clone("other")
        self.g("fetch", "-q", "origin", d=o)
        self.g("checkout", "-q", "s1", d=o)
        line = self.run_("landing.py", "wip", "s1", d=o).stdout
        self.assertIn("unfinished work saved", line)
        cmd = line.split("`git ")[1].split("`")[0].split()
        self.assertEqual(self.g(*cmd, d=o).returncode, 0)
        self.assertEqual(Path(o, "a.txt").read_text(), "two\n")
        self.assertFalse(Path(o, "calc.py").exists())
        self.assertEqual(self.run_("landing.py", "wip", "nope", d=o).stdout, "")

    def conf_(self, **k):
        Path(self.d, ".kit", "config.json").write_text(json.dumps({"kit": dict(self.conf, **k)}))

    def test_auto_pushes_a_public_repo_too(self):
        self.put("a.txt", "two\n", commit=False)
        self.conf_(wip="auto")                                                   # CL55: not known to be private, pushed all the same
        self.assertIn("on origin/wip/s1", self.save())
        self.conf_(wip="local")
        self.put("a.txt", "three\n", commit=False)
        self.assertIn("kept in this clone", self.save())
        self.conf_(wip="off")
        self.assertIn("off", self.save())


class Hook(Repo):
    def fire(self, **k):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_wip.py")], capture_output=True, text=True, timeout=20,
                           input=json.dumps(dict({"hook_event_name": "PostToolUse", "tool_name": "Edit", "session_id": "s", "cwd": self.d}, **k)),
                           env=dict(self.env, CLAUDE_PROJECT_DIR=self.d))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def saved(self, secs=15):
        end = time.time() + secs
        while time.time() < end:
            if self.remote("wip/s1"):
                return True
            time.sleep(0.2)
        return False

    def test_every_few_minutes_and_at_stop(self):
        self.g("checkout", "-q", "-b", "s1")
        self.put("x.txt", "x\n", commit=False)
        self.fire()                                                              # starts the clock
        self.fire()
        self.assertFalse(self.saved(1.5))
        stamp = os.path.join(self.d, ".kit", "run", "wip-last")
        os.utime(stamp, (time.time() - 400,) * 2)
        self.fire()
        self.assertTrue(self.saved())

    def test_at_stop(self):
        self.g("checkout", "-q", "-b", "s1")
        self.put("x.txt", "x\n", commit=False)
        self.fire(hook_event_name="Stop")
        self.assertTrue(self.saved())


class WebReview(Board):
    def test_its_prompt_is_on_a_branch_a_cloud_session_can_read(self):
        shutil.copy(str(ROOT / "kit" / "claude" / "consultant.template.md"), os.path.join(self.d, ".kit", "consultant.md"))
        self.env.pop("KIT_NO_LAUNCH")
        self.env["CLAUDE_CODE_REMOTE"] = "true"
        before = (self.g("status", "--porcelain").stdout, self.g("rev-parse", "HEAD").stdout)
        self.run_("kit.py", "done", "s1")
        out = self.run_("kit.py", "step", "complete", "s1").stdout
        self.assertIn('\\"source_revision\\": \\"review-s1\\"', out)
        self.assertIn("docs/prompts/CR-s1.md", out)
        self.assertTrue(self.has("docs/prompts/CR-s1.md", "review-s1"))
        self.assertTrue(self.has("new.txt", "review-s1"))                        # and the work it reviews
        self.assertEqual((self.g("status", "--porcelain").stdout, self.g("rev-parse", "HEAD").stdout), before)
        og = load("og_l", "kit/hooks/kit_orchestrator_guard.py")
        self.assertIsNone(og.launch_verdict({"prompt": "Your prompt is docs/prompts/CR-s1.md: read it and do it.", "tags": ["review"]}, self.d))
        self.patch(review="continue")
        self.land()
        self.assertFalse(self.has("docs/prompts/CR-s1.md"))                      # the prompt never reaches the trunk
        self.assertTrue(self.has("new.txt"))
        self.assertEqual(self.remote("review-s1"), "")

    def test_a_push_that_is_refused_is_said(self):
        shutil.copy(str(ROOT / "kit" / "claude" / "consultant.template.md"), os.path.join(self.d, ".kit", "consultant.md"))
        self.env.pop("KIT_NO_LAUNCH")
        self.env["CLAUDE_CODE_REMOTE"] = "true"
        self.g("remote", "set-url", "--push", "origin", os.path.join(self.t, "nowhere"))
        self.assertIn("could not be pushed to a branch", self.run_("kit.py", "review", "s1").stdout)


class Told(unittest.TestCase):
    def test_every_review_lands_and_marks_its_step(self):
        for rel in ("kit/claude/consultant.template.md", "kit/claude-cli/consultant.template.md", "kit/consultant.template.md"):
            t = (ROOT / rel).read_text()
            self.assertIn("kit.py land <POST_ID>", t, rel)
            self.assertRegex(t, r"kit\.py reviewed <POST_ID> <continue\|redirect\|stop> --review-id", rel)   # CL71: with its commit

    def test_the_guard_refuses_ways_round(self):
        kg = load("kg_l", "kit/hooks/kit_guard.py")
        for c in ("git push --no-verify origin main", "git -c core.hooksPath=/dev/null push origin main", "git config core.hooksPath /dev/null"):
            self.assertIsNotNone(kg.verdict(c.split()), c)
        for c in ("git push origin main", "git config core.hooksPath", "git config user.name x"):
            self.assertIsNone(kg.verdict(c.split()), c)

    def test_wired_everywhere(self):
        hook = load("hook_l", "kit/hook.py")
        for rt in hook.RUNTIMES:
            for ev in ("PostToolUse", "Stop"):
                self.assertIn("kit_wip.py", [r[2] for r in hook.rows(rt, ev)], (rt, ev))


if __name__ == "__main__":
    unittest.main()
