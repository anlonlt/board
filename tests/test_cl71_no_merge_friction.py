"""CL71: no merge friction. The owner, 2026-11-27, on CL17, CL54, CL61 and a review: "why is there so much friction?"

Every landing that day hit the same loop: the generated manifest and files map were in git, so two branches that each
regenerated them conflicted at every merge; the save hook committed them with conflict markers; a regenerate commit
after the review made `land` ask for another review; a re-review's commit was never recorded (CL57: three reviews);
and steps merged by hand stayed "not landed" (CL44, M5-M8, CL41). Each case below is one of those, on real
repositories and a real board.

Run via: python3 -m unittest tests.test_cl71_no_merge_friction -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_landing import Board, ROOT  # noqa: E402
from test_post_gate import TOKEN, api_req  # noqa: E402

GENERATED = (".kit/manifest.json", ".kit/artifact-files/")


def sh(*a, cwd=None, env=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, timeout=300, env=env)


class GeneratedFilesOutOfGit(unittest.TestCase):
    """1 and 5: the kit's own repository keeps neither file in git, and two branches that both change the kit merge
    with no conflict in them, each clone's manifest rebuilt from its tree."""

    def test_the_kits_repo_tracks_neither(self):
        tracked = sh("git", "ls-files", "--", *GENERATED, cwd=str(ROOT)).stdout.split()
        self.assertEqual(tracked, [], "still in git: %s" % tracked)
        ignored = [g for g in GENERATED if sh("git", "check-ignore", "-q", "--no-index", g.rstrip("/") + ("/x.json" if g.endswith("/") else ""),
                                              cwd=str(ROOT)).returncode == 0]
        self.assertEqual(ignored, list(GENERATED))

    def test_two_branches_that_both_change_the_kit_merge_with_no_manifest_conflict(self):
        t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, t, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "GIT_"))}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        kit = os.path.join(t, "kit")
        shutil.copytree(str(ROOT), kit, ignore=shutil.ignore_patterns(".git", "run", "board_data", "__pycache__", "node_modules"))
        for f in GENERATED:                                         # a kit clone with neither (a fresh web container)
            shutil.rmtree(os.path.join(kit, f), True) if f.endswith("/") else (os.path.exists(os.path.join(kit, f)) and os.remove(os.path.join(kit, f)))
        proj = os.path.join(t, "proj")
        os.makedirs(proj)
        sh("git", "init", "-q", "-b", "main", cwd=proj, env=env)
        Path(proj, "app.py").write_text("print(1)\n")
        r = sh(sys.executable, os.path.join(kit, "install.py"), proj, "--project", "P", "--talk", "en", cwd=t, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertTrue(os.path.isfile(os.path.join(proj, ".kit", "manifest.json")))     # built at install
        sh("git", "add", "-A", cwd=proj, env=env)
        sh("git", "commit", "-qm", "kit", cwd=proj, env=env)
        self.assertEqual(sh("git", "ls-files", "--", *GENERATED, cwd=proj).stdout.split(), [])
        # two branches, each with a different kit file changed and the install run again (the manifest regenerated)
        for br, f in (("a", "kit/lessons.md"), ("b", "kit/ledger.py")):
            sh("git", "checkout", "-q", "-B", br, "main", cwd=proj, env=env)
            with open(os.path.join(kit, f), "a") as fh:
                fh.write("\n# changed on %s\n" % br)
            r = sh(sys.executable, os.path.join(kit, "install.py"), proj, "--update", cwd=t, env=env)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            sh("git", "add", "-A", cwd=proj, env=env)
            self.assertEqual(sh("git", "commit", "-qm", "kit on " + br, cwd=proj, env=env).returncode, 0)
            with open(os.path.join(kit, f)) as fh:                  # the kit back as it was for the other branch
                txt = fh.read()
            Path(kit, f).write_text(txt.replace("\n# changed on %s\n" % br, ""))
        sh("git", "checkout", "-q", "a", cwd=proj, env=env)
        m = sh("git", "merge", "--no-edit", "b", cwd=proj, env=env)
        self.assertEqual(m.returncode, 0, m.stdout + m.stderr)
        self.assertNotIn("manifest", m.stdout + m.stderr)
        # the clone has none: a reader rebuilds it from the tree, the kit's files in it
        os.remove(os.path.join(proj, ".kit", "manifest.json"))
        r = sh(sys.executable, "-I", os.path.join(proj, ".kit", "kit_manifest.py"), proj)
        got = json.loads(r.stdout)
        self.assertIn(".kit/kit.py", got)
        self.assertIn(".kit/ledger.py", got)
        self.assertNotIn(".kit/config.json", got)
        # and an update in that clone keeps a project's uncommitted edit to a kit file
        Path(proj, ".kit", "lessons.md").write_text("mine\n")
        r = sh(sys.executable, os.path.join(kit, "install.py"), proj, "--update", cwd=t, env=env)
        self.assertIn("KEPT", r.stdout)
        self.assertEqual(Path(proj, ".kit", "lessons.md").read_text(), "mine\n")


class Saves(Board):
    """2: the save hook never commits a file with conflict markers: it refuses and names the file."""

    def conflict(self):
        o = self.clone("other")
        self.put("new.txt", "theirs\n", d=o)
        self.g("push", "-q", "origin", "main", d=o)
        self.g("fetch", "-q", "origin")
        m = self.g("merge", "--no-edit", "origin/main")
        self.assertNotEqual(m.returncode, 0)
        self.assertIn("<<<<<<<", Path(self.d, "new.txt").read_text())

    def test_a_checkpoint_refuses_a_file_with_markers(self):
        self.conflict()
        head = self.g("rev-parse", "HEAD").stdout.strip()
        r = self.run_("kit.py", "checkpoint", "s1")
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertFalse(res.get("committed"), res)
        self.assertIn("new.txt", res.get("why", ""))
        self.assertIn("conflict markers", res.get("why", ""))
        self.assertEqual(self.g("rev-parse", "HEAD").stdout.strip(), head)

    def test_the_save_hook_refuses_and_says_which(self):
        self.conflict()
        head = self.g("rev-parse", "HEAD").stdout.strip()
        r = self.run_("landing.py", "save")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("new.txt", r.stdout)
        self.assertIn("conflict markers", r.stdout)
        self.assertEqual(self.g("rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(self.remote("s1"), self.g("rev-parse", "origin/s1").stdout.strip())

    def test_a_file_that_quotes_one_marker_is_saved(self):
        self.put("doc.md", "Title\n=======\n", commit=False)
        r = self.run_("kit.py", "checkpoint", "s1")
        self.assertTrue(json.loads(r.stdout.strip().splitlines()[-1]).get("committed"), r.stdout)


class Landing(Board):
    """3, 6, 7, 8: land merges the commit a review read, whatever came after it that is only generated, and a step on
    the trunk already is counted landed."""

    def comment(self, cid, **d):
        api_req(self.url, "db/comments/" + cid, {"data": dict({"post_id": "s1", "from": "consultant", "kind": "review",
                                                               "text": "x", "created_at": "2026-11-27T05:00:00Z"}, **d)},
                token=TOKEN, method="PUT")

    def test_a_regenerate_commit_after_the_review_does_not_block_land(self):
        self.complete("continue")
        self.put(".kit-gen.txt", "", commit=False)
        Path(self.d, ".kit", "manifest.json").write_text('{"x": "1"}\n')
        self.g("add", "-f", ".kit/manifest.json")
        self.g("commit", "-qm", "manifest regenerated")
        self.g("push", "-q", "origin", "s1")
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("only generated files changed", r.stdout)
        self.assertTrue(self.has("new.txt"))

    def test_a_real_commit_after_the_review_still_waits(self):
        self.complete("continue")
        self.put("late.txt", "x\n")
        self.g("push", "-q", "origin", "s1")
        self.assertIn("after its review", self.land().stdout)

    def test_a_re_review_recorded_only_in_its_comment_lets_land_proceed(self):
        self.complete("continue")
        self.comment("CR-s1", verdict="redirect", commit=self.get()["review_commit"])
        self.put("fix.txt", "fixed\n")                                 # the part's fix, after the first review
        self.g("push", "-q", "origin", "s1")
        self.comment("CR-s1-2", verdict="continue", commit=self.remote("s1"), created_at="2026-11-27T06:00:00Z")
        self.assertNotEqual(self.get()["review_commit"], self.remote("s1"))       # the post never got it (CL57)
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.has("fix.txt"))

    def test_reviewed_records_the_commit_it_read(self):
        self.complete()
        self.put("fix.txt", "fixed\n")
        self.g("push", "-q", "origin", "s1")
        self.comment("CR-s1-2", verdict="continue")
        r = self.run_("kit.py", "reviewed", "s1", "continue", "--review-id", "CR-s1-2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.get()["review_commit"], self.remote("s1"))
        self.assertEqual(self.get()["review"], "continue")
        self.assertEqual(api_req(self.url, "doc/comments/CR-s1-2", token=TOKEN)["data"]["commit"], self.remote("s1"))
        self.assertEqual(self.land().returncode, 0)
        self.assertTrue(self.has("fix.txt"))

    def test_a_merged_branch_with_one_later_commit_shows_landed(self):
        self.complete("continue")
        seen = self.get()["review_commit"]
        self.g("checkout", "-q", "main")
        self.g("merge", "-q", "--no-ff", "--no-edit", "s1")                   # merged by hand
        self.g("push", "-q", "origin", "main", env={"KIT_SKIP": "1"})
        self.g("checkout", "-q", "s1")
        self.put("test_more.txt", "t\n")                                     # a later commit on its branch
        self.g("push", "-q", "origin", "s1")
        self.g("fetch", "-q", "origin")
        out = self.run_("kit.py", "resume", "--role", "orchestrator").stdout
        self.assertIn("is on the trunk", out)
        self.assertIn("1 newer commit(s) on `s1`, not reviewed", out)
        self.assertEqual(self.get().get("landed"), seen)
        self.assertNotIn("kit.py land s1", self.run_("kit.py", "resume", "--role", "orchestrator").stdout)
        self.assertEqual(self.remote("s1") != "", True)                      # its unreviewed commit is kept

    def test_a_trunk_merge_that_names_its_branch_counts(self):
        self.patch(status="complete", review="continue")                     # a step with no review_commit recorded
        self.g("checkout", "-q", "main")
        self.g("merge", "-q", "--no-ff", "-m", "Merge branch 's1'", "s1")
        self.put("after.txt", "a\n")
        self.g("push", "-q", "origin", "main")
        self.g("checkout", "-q", "s1")
        self.put("later.txt", "l\n")
        self.g("push", "-q", "origin", "s1")
        self.g("fetch", "-q", "origin")
        got = json.loads(self.run_("kit.py", "landing", "--post", "s1").stdout)["steps"][0]
        self.assertTrue(got["landed"], got)
        self.assertEqual(len(got["newer"]), 1)
        self.assertTrue(self.get().get("landed"))


if __name__ == "__main__":
    unittest.main()
