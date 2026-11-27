"""CL54: a step's task is written once, in its post; the prompt file is generated from the post at launch.

- `step new` writes the post only (with where to work: the repo and the current branch).
- `kit.py launch` generates docs/prompts/<post>.md from the post as it is then and commits it; a relaunch after the
  post changed regenerates it as a new commit (the history shows each version), with no other edit.
- `resume --role part` and the review read the task from the post; doctor names a prompt that says another task.
- A post from before CL54 that holds no task keeps its file as it is.

Run via: python3 -m unittest tests.test_cl54_prompt_from_post -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_launch as TL  # noqa: E402
from test_launch import POST, TOKEN, TRAINS, git, make_repo  # noqa: E402

TPL = ("Board, part <PART>: <TITLE>. Train `<TRAIN>`<AFTER>. Your step's post: `<POST_ID>` (it exists already).\n\n"
       "## The task\n<TASK>\n\n## What this step answers (the owner's words, or a review's)\n<SOURCES>\n\n"
       "## How you work\n0. the kit's, printed by resume\n")
TASK1 = "Make the launch write the prompt.\n1. step new writes the post only\n2. launch generates the prompt"
TASK2 = TASK1 + "\n3. doctor names a prompt edited by hand"


class PromptFromPost(TL.Base):
    fake_claude = TL.Launch.fake_claude

    def seed(self, config, task=TASK1):
        repo = make_repo(self.tmp / "repo", config)
        for sub in ("claude-cli", "claude-web"):
            (repo / ".kit" / sub).mkdir()
            (repo / ".kit" / sub / "part.md").write_text(TPL)
        (repo / ".kit" / "part.md").write_text(TPL)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "templates")
        self.local("posts/cl99-launch-me", dict(POST, task=task, body=task, work_branch="work", repo="anlonlt/board"))
        self.local("posts/_trains", TRAINS)
        return repo

    def prompt(self, repo):
        return (repo / "docs" / "prompts" / "cl99-launch-me.md").read_text()

    def test_a_task_edited_on_the_post_reaches_the_next_launch_with_no_other_edit(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local", "github": "anlonlt/board"}})
        k = self.kit_in(repo)
        self.assertEqual(self.prompt(repo), "Board, part CL99.\n")          # an old prompt, written by hand
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=True)
        self.assertTrue(r["ok"], r)
        text = self.prompt(repo)
        self.assertIn("## The task\n" + TASK1, text)
        self.assertNotIn("3. doctor names", text)
        self.assertIn("## Where to work\nRepo anlonlt/board, branch `work`: `git clone -b work https://github.com/anlonlt/board", text)
        self.assertNotIn("## How you work", text)                         # the kit's, never copied into git
        self.assertEqual(git(repo, "status", "--porcelain", "--", "docs/prompts"), "")   # committed by the script
        first = git(repo, "log", "--format=%s", "--", "docs/prompts/cl99-launch-me.md").splitlines()
        self.assertTrue(first[0].startswith("prompt(CL99): regenerated, the post changed from post cl99-launch-me"), first)
        # the orchestrator edits the task on the post only, then launches again (a takeover)
        p = self.local("posts/cl99-launch-me")["data"]
        self.local("posts/cl99-launch-me", dict(p, task=TASK2))
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=True, take=True)
        self.assertTrue(r["ok"], r)
        self.assertIn("## The task\n" + TASK2, self.prompt(repo))
        log = git(repo, "log", "--format=%s", "--", "docs/prompts/cl99-launch-me.md").splitlines()
        self.assertEqual(len(log), len(first) + 1, log)                   # each version its own commit
        self.assertEqual(git(repo, "status", "--porcelain", "--", "docs/prompts"), "")
        # an unchanged post makes no new commit
        k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=True, take=True)
        self.assertEqual(len(git(repo, "log", "--format=%s", "--", "docs/prompts/cl99-launch-me.md").splitlines()), len(log))

    def test_a_cli_launch_starts_the_part_from_the_regenerated_prompt(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        self.fake_claude()
        k = self.kit_in(repo)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=False, gate=False)
        self.assertTrue(r["ok"], r)
        wt = Path(r["worktree"])                                          # the worktree is made after the commit
        self.assertIn("## The task\n" + TASK1, (wt / "docs" / "prompts" / "cl99-launch-me.md").read_text())

    def test_a_prompt_edited_by_hand_is_named_by_doctor(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        k = self.kit_in(repo)
        post = self.local("posts/cl99-launch-me")["data"]
        self.assertTrue(k.sync_step_prompt("cl99-launch-me", post)["commit"])
        self.assertIsNone(k.prompt_drift("cl99-launch-me", post))
        path = repo / "docs" / "prompts" / "cl99-launch-me.md"
        path.write_text(path.read_text().replace("2. launch generates the prompt", "2. launch generates it\n4. a hand-added item"))
        why = k.prompt_drift("cl99-launch-me", post)
        self.assertIn("docs/prompts/cl99-launch-me.md says a task its post cl99-launch-me does not", why)
        issues, lines = [], []
        k._all_posts = lambda db_path=None: {"cl99-launch-me": post}
        k._doctor_prompts(str(repo), issues, lines)
        self.assertEqual(len(issues), 1, issues)
        self.assertIn("prompt differs from its post", issues[0])
        # a finished step is history: a line, never an issue
        k._all_posts = lambda db_path=None: {"cl99-launch-me": dict(post, status="complete", column="done")}
        issues, lines = [], []
        k._doctor_prompts(str(repo), issues, lines)
        self.assertEqual(issues, [])
        self.assertIn("1 finished step(s)", lines[-1])

    def test_resume_and_review_read_the_task_from_the_post(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        k = self.kit_in(repo)
        post = dict(self.local("posts/cl99-launch-me")["data"], task=TASK2 + "\nAcceptance: the post is the one place")
        text = "\n".join(k.part_prompt("cl99-launch-me", post))
        self.assertIn("3. doctor names a prompt edited by hand", text)       # the post's, not the stale file's
        self.assertNotIn("Board, part CL99.\n", text.split("## How you work")[0][-20:])
        self.assertEqual(k._acceptance("docs/prompts/cl99-launch-me.md", post), ["Acceptance: the post is the one place"])

    def test_a_post_from_before_cl54_keeps_its_file(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        k = self.kit_in(repo)
        r = k.sync_step_prompt("cl99-launch-me", dict(POST))                # no task on the post
        self.assertFalse(r["written"])
        self.assertEqual(self.prompt(repo), "Board, part CL99.\n")

    def test_step_new_writes_the_post_only_with_where_to_work(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local", "github": "anlonlt/board"}})
        k = self.kit_in(repo)
        k._all_posts = lambda db_path=None: {"_trains": TRAINS}
        k._board_write = lambda cmd, coll, did, data, env=None: bool(self.local("%s/%s" % (coll, did), data) or True)
        r = k.step_new("cli", "CL98", "Write once", task="Keep the task on the post.", keep_train="test")
        self.assertNotIn("error", r, r)
        self.assertFalse(r["prompt_written"])
        self.assertFalse((repo / "docs" / "prompts" / (r["post"] + ".md")).exists())
        p = self.local("posts/" + r["post"])["data"]
        self.assertEqual((p["work_branch"], p["repo"]), ("work", "anlonlt/board"))
        self.assertIn("kit.py launch %s" % r["post"], r["launch"])
        # a runner card a platform started by hand reads where first (R1/R2 said "clone the repo" with no branch)
        self.assertIn("git clone -b work https://github.com/anlonlt/board", k.step_where(p))

    def test_the_queue_says_where_a_runner_card_works(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        k = self.kit_in(repo)
        self.local("posts/r9-by-hand", {"title": "Run it by hand", "part": "R9", "train": "cli", "runner": "antigravity",
                                        "column": "todo", "status": "planned", "work_branch": "feat/x", "repo": "anlonlt/board",
                                        "created_at": "2026-11-27T00:00:00Z"})
        out = k.queue_cards(None, runner="antigravity")
        self.assertIn("r9-by-hand", out)
        self.assertIn("    where: Repo anlonlt/board, branch `feat/x`: `git clone -b feat/x https://github.com/anlonlt/board", out)


if __name__ == "__main__":
    unittest.main()
