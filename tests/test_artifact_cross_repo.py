"""Cross-repo boards in a claude.ai Artifact: install.py --attach records each repo's GitHub name, and
kit_artifact_sync.py boards mirrors each attached repo's board-mirror branch into repos/<id>/... incrementally.
The page side (artifact_repos.js: switcher, "All boards", writes routed to the right board) runs in Chromium
when one is available (kit/claude/test_artifact_repos.js).

Run via: python3 -m unittest discover -s tests -v
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import tempfile as _tf; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-")  # CL11: install.py writes the machine token there, never in the real ~/.config

ROOT = Path(__file__).resolve().parent.parent
_path = list(sys.path)
sys.path.insert(0, str(ROOT / "kit" / "claude"))
sys.path.insert(0, str(ROOT))
import kit_artifact_sync as kas  # noqa: E402
import install  # noqa: E402
sys.path[:] = _path        # install.py adds kit/board: it would shadow tests/test_kit_cards.py for the other suites


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd)] + list(a), capture_output=True, text=True, check=True).stdout


def make_attached(tmp, name, posts, comments):
    """A clone whose origin has a board-mirror branch with board/posts and board/comments."""
    origin = Path(tmp) / (name + "-origin.git")
    work = Path(tmp) / (name + "-seed")
    git(tmp, "init", "-q", "--bare", str(origin))
    git(tmp, "init", "-q", str(work))
    git(work, "config", "user.email", "t@t"); git(work, "config", "user.name", "t")
    git(work, "checkout", "-q", "-b", "board-mirror")
    for coll, docs in (("posts", posts), ("comments", comments)):
        d = work / "board" / coll
        d.mkdir(parents=True, exist_ok=True)
        for did, data in docs.items():
            (d / (did + ".json")).write_text(json.dumps(data))
    git(work, "add", "-A"); git(work, "commit", "-q", "-m", "board")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "board-mirror")
    clone = Path(tmp) / name
    git(tmp, "clone", "-q", str(origin), str(clone))
    return clone, work


def batch_entries(out):
    rows = []
    for f in sorted(glob.glob(os.path.join(out, "batch-boards-*.json"))):
        rows += json.load(open(f))
    return rows


class CrossRepoSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.primary = Path(self.tmp) / "primary"
        (self.primary / ".kit").mkdir(parents=True)
        self.clone, self.seed = make_attached(
            self.tmp, "songs",
            {"P1": {"title": "Chord oracle", "column": "doing", "created_at": "2026-11-20T10:00:00Z"}},
            {"c1": {"post_id": "P1", "text": "go", "created_at": "2026-11-20T11:00:00Z"}})
        cfg = {"project": "Harmonize", "portfolio": [{"name": "Songs", "github": "anlonlt/songs"}]}
        (self.primary / ".kit" / "config.json").write_text(json.dumps(cfg))
        self.out = str(Path(self.tmp) / "kas")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_boards(self, *extra):
        return kas.main(["boards", "--repo", str(self.primary), "--out", self.out, "--clone", "Songs=%s" % self.clone] + list(extra))

    def test_first_run_writes_index_posts_comments(self):
        self.run_boards()
        rows = batch_entries(self.out)
        self.assertEqual(rows[0]["collection"], "kit_repos")
        idx = json.load(open(rows[0]["file_path"]))
        self.assertEqual(idx["repos"], [{"id": "songs", "name": "Songs", "github": "anlonlt/songs"}])
        self.assertEqual(idx["primary"]["name"], "Harmonize")
        colls = sorted((r["collection"], r["doc_id"], r["op"]) for r in rows[1:])
        self.assertEqual(colls, [("repos/songs/comments", "c1", "set"), ("repos/songs/posts", "P1", "set")])
        post = json.load(open([r for r in rows if r["doc_id"] == "P1"][0]["file_path"]))
        self.assertEqual(post["title"], "Chord oracle")

    def test_second_run_sends_only_changes_and_deletes(self):
        self.run_boards()
        os.replace(os.path.join(self.out, "boards-state.json.next"), os.path.join(self.out, "boards-state.json"))
        self.run_boards()
        self.assertEqual([r["collection"] for r in batch_entries(self.out)], ["kit_repos"])   # nothing changed
        (self.seed / "board" / "posts" / "P1.json").write_text(json.dumps({"title": "Chord oracle v2", "created_at": "x"}))
        (self.seed / "board" / "comments" / "c1.json").unlink()
        git(self.seed, "add", "-A"); git(self.seed, "commit", "-q", "-m", "edit"); git(self.seed, "push", "-q", "origin", "board-mirror")
        os.replace(os.path.join(self.out, "boards-state.json.next"), os.path.join(self.out, "boards-state.json"))
        vers = Path(self.tmp) / "v.json"
        vers.write_text(json.dumps({"repos/songs/posts/P1": 3, "repos/songs/comments/c1": 1}))
        self.run_boards("--versions", str(vers))
        got = {(r["collection"], r["doc_id"]): r for r in batch_entries(self.out)[1:]}
        self.assertEqual(got[("repos/songs/posts", "P1")]["op"], "set")
        self.assertEqual(got[("repos/songs/posts", "P1")]["if_version"], 3)
        self.assertEqual(got[("repos/songs/comments", "c1")]["op"], "delete")
        self.assertEqual(got[("repos/songs/comments", "c1")]["if_version"], 1)

    def test_missing_clone_keeps_board_and_says_so(self):
        cfg = json.loads((self.primary / ".kit" / "config.json").read_text())
        cfg["portfolio"].append({"name": "Elsewhere", "github": "anlonlt/elsewhere"})
        (self.primary / ".kit" / "config.json").write_text(json.dumps(cfg))
        self.run_boards()
        idx = json.load(open(batch_entries(self.out)[0]["file_path"]))
        self.assertEqual([r["id"] for r in idx["repos"]], ["songs", "elsewhere"])
        self.assertFalse(any(r["collection"].startswith("repos/elsewhere/") for r in batch_entries(self.out)))

    def test_attach_flag_without_config(self):
        bare = Path(self.tmp) / "bare"
        bare.mkdir()
        kas.main(["boards", "--repo", str(bare), "--out", self.out, "--project", "Harmonize",
                  "--attach", "board=anlonlt/board", "--clone", "board=%s" % self.clone])
        rows = batch_entries(self.out)
        idx = json.load(open(rows[0]["file_path"]))
        self.assertEqual(idx["repos"], [{"id": "board", "name": "board", "github": "anlonlt/board"}])
        self.assertEqual(idx["primary"]["name"], "Harmonize")
        self.assertIn("repos/board/posts", {r["collection"] for r in rows})

    def test_boards_back_sends_artifact_comments_to_the_repo(self):
        dump = Path(self.tmp) / "dump" / "repos" / "songs" / "comments"
        dump.mkdir(parents=True)
        (dump / "c1.json").write_text(json.dumps({"post_id": "P1", "text": "go", "created_at": "x"}))       # already there
        (dump / "h1.json").write_text(json.dumps({"post_id": "P1", "text": "from the Artifact", "from": "owner",
                                                  "created_at": "2026-11-23T01:00:00Z"}))
        git(self.clone, "config", "user.email", "t@t"); git(self.clone, "config", "user.name", "t")   # a machine may have none
        kas.main(["boards-back", "--repo", str(self.primary), "--out", self.out, "--from", str(Path(self.tmp) / "dump"),
                  "--clone", "Songs=%s" % self.clone])
        git(self.seed, "pull", "-q", "origin", "board-mirror")
        self.assertEqual(json.loads((self.seed / "board" / "comments" / "h1.json").read_text())["text"], "from the Artifact")
        self.assertEqual(sorted(p.name for p in (self.seed / "board" / "comments").glob("*.json")), ["c1.json", "h1.json"])
        # the forward sync does not send it again (it is in the state now)
        self.run_boards()
        self.assertNotIn(("repos/songs/comments", "h1"),
                         {(r["collection"], r["doc_id"]) for r in batch_entries(self.out)})

    def test_docs_into_attached_repo(self):
        self.assertEqual(kas.prefix(type("A", (), {"into": "Songs"})()), "repos/songs/")
        self.assertEqual(kas.prefix(type("A", (), {"into": ""})()), "")


class InstallAttach(unittest.TestCase):
    def test_attach_spec_forms(self):
        tmp = tempfile.mkdtemp()
        try:
            r = Path(tmp) / "harm"
            git(tmp, "init", "-q", str(r))
            git(r, "remote", "add", "origin", "https://github.com/anlonlt/harmonize.git")
            self.assertEqual(install.attach_spec("H=%s" % r), ("H", str(r), "anlonlt/harmonize"))
            self.assertEqual(install.attach_spec("songs=github:anlonlt/songs"), ("songs", "", "anlonlt/songs"))
            self.assertEqual(install.attach_spec("github:anlonlt/songs")[0], "songs")
            git(r, "remote", "set-url", "origin", "http://local_proxy@127.0.0.1:1234/git/anlonlt/board")
            self.assertEqual(install.github_of(str(r)), "anlonlt/board")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PageSwitcher(unittest.TestCase):
    """The built page in Chromium: switcher, live re-subscription, All boards, routed writes."""

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_built_page(self):
        tmp = tempfile.mkdtemp()
        try:
            git(tmp, "init", "-q")
            env = dict(os.environ, KIT_NO_FUNNEL="1")
            subprocess.run([sys.executable, str(ROOT / "install.py"), tmp, "--quick"], capture_output=True, env=env, check=True, timeout=300)
            page = os.path.join(tmp, ".kit", "board.html")
            subprocess.run([sys.executable, str(ROOT / "kit" / "claude" / "build_artifact_page.py"), "--kit", str(ROOT), "--page", page,
                            "--board-url", "https://claude.ai/artifact/test", "--project", "Harmonize"], capture_output=True, check=True)
            for test in ("test_artifact_repos.js", "test_artifact_page.js", "test_artifact_live.js"):
                r = subprocess.run(["node", str(ROOT / "kit" / "claude" / test)], capture_output=True, text=True, timeout=240,
                                   env=dict(env, KIT_PAGE=page), cwd=str(ROOT))
                self.assertEqual(r.returncode, 0, test + "\n" + r.stdout[-2000:] + r.stderr[-2000:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
