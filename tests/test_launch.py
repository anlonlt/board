"""CL13: a launched part is always visible on the board, on the right base.

- `kit.py launch <post>` writes status in progress, session_id, launched_at and branch on the post and marks its
  Trains row running, in the CLI (claude --bg in a worktree made from the current branch) and on the web (the
  create_session call, faked here), with no hand edit.
- A repo whose board is attached to an Artifact (config `kit.artifact: {url, prefix}`) finds it from config alone:
  pull, load, push and the launch write under that prefix (`repos/board/posts`).
- `kit.py resume` flags a part that was launched and died (a branch, worktree or transcript, no live session) and
  says what is uncommitted in its worktree.

Run via: python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req  # noqa: E402

ART_URL = "https://claude.ai/artifact/Ek79P6H3WTPJjJVYPe9rsf"


def load_kit(name="kit_launch"):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / "kit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_repo(path, config):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "t")
    git(path, "config", "user.email", "t@example.invalid")
    (path / ".kit").mkdir()
    (path / ".kit" / "config.json").write_text(json.dumps(config))
    (path / ".kit" / "part.md").write_text("part guide\n")
    (path / "docs" / "prompts").mkdir(parents=True)
    (path / "docs" / "prompts" / "cl99-launch-me.md").write_text("Board, part CL99.\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")
    git(path, "checkout", "-q", "-b", "work")          # the current branch: it carries .kit and the prompt
    return path


POST = {"title": "Launch me", "part": "CL99", "train": "cli", "column": "info", "status": "planned",
        "from": "orchestrator", "created_at": "2026-11-23T00:00:00Z"}
TRAINS = {"title": "Trains", "column": "info", "created_at": "2026-11-23T00:00:00Z",
          "rows": [{"part": "P-cli", "parent": "", "also": [], "children": [], "post": "", "prompt": "", "session": "",
                    "state": "idle", "store": []}]}


class Base(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.board_dir = self.tmp / "boarddata"
        self.board_dir.mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.board_dir)],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=5), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.env = {}
        for k in ("KIT_ARTIFACT_DIR", "KIT_BOARD_URL", "KIT_BOARD_TOKEN", "CLAUDE_CODE_REMOTE", "KIT_ARTIFACT_URL",
                  "KIT_ARTIFACT_PREFIX", "PATH"):
            self.env[k] = os.environ.get(k)
        self.addCleanup(self.restore_env)
        os.environ["KIT_ARTIFACT_DIR"] = str(self.tmp / "art")
        os.environ["KIT_BOARD_URL"] = self.url
        os.environ["KIT_BOARD_TOKEN"] = TOKEN
        for k in ("CLAUDE_CODE_REMOTE", "KIT_ARTIFACT_URL", "KIT_ARTIFACT_PREFIX"):
            os.environ.pop(k, None)

    def restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def local(self, path, data=None):
        if data is None:
            return api_req(self.url, "doc/" + path, token=TOKEN)
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def kit_in(self, repo):
        k = load_kit()
        k.ROOT = str(repo)
        return k


class AttachedBoardFromConfig(Base):
    def test_kit_repo_config_names_its_artifact_board(self):
        """The kit repo itself (its committed .kit/config.json) is attached to Harmonize's Artifact board."""
        k = load_kit()
        a = k.attached_board()
        self.assertEqual(a["url"], ART_URL)
        self.assertEqual(a["prefix"], "repos/board/")
        txt = k.artifact_pull(full=True)
        self.assertIn(ART_URL, txt)
        self.assertIn('collection: "repos/board/posts"', txt)
        self.assertIn('collection: "repos/board/comments"', txt)

    def test_board_cli_names_the_attached_board(self):
        spec = importlib.util.spec_from_file_location("bcli", str(ROOT / "kit" / "board" / "board_cli.py"))
        bcli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bcli)
        a = bcli.attached_board(str(ROOT))
        self.assertEqual((a["url"], a["prefix"]), (ART_URL, "repos/board/"))

    def test_sync_script_defaults_into_the_attached_prefix(self):
        spec = importlib.util.spec_from_file_location("kas", str(ROOT / "kit" / "claude" / "kit_artifact_sync.py"))
        kas = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kas)
        self.assertEqual(kas.attached_prefix(str(ROOT)), "repos/board/")

    def test_cloud_part_reads_and_writes_under_the_prefix(self):
        """A cloud part: pulled files land under in/repos/board/posts; load reads them; push writes repos/board/posts."""
        repo = make_repo(self.tmp / "r", {"project": "Board", "board_url": "http://127.0.0.1:8765",
                                          "kit": {"artifact": {"url": ART_URL, "prefix": "repos/board/"}}})
        k = self.kit_in(repo)
        d = self.tmp / "art" / "in" / "repos" / "board" / "posts"
        d.mkdir(parents=True)
        (d / "cl99-launch-me.json").write_text(json.dumps(POST))
        r = k.artifact_load(url=self.url, token=TOKEN)
        self.assertEqual(r["taken"], 1)
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["part"], "CL99")
        k.artifact_pin(["repos/board/posts/cl99-launch-me=5"])        # the pin names the Artifact's collection
        p = dict(POST, status="in progress")
        self.local("posts/cl99-launch-me", p)
        r = k.artifact_push(url=self.url, token=TOKEN)
        es = [e for b in r["batches"] for e in json.load(open(b))]
        self.assertEqual({e["collection"] for e in es}, {"repos/board/posts"})
        self.assertEqual([(e["doc_id"], e.get("if_version")) for e in es if e["doc_id"] == "cl99-launch-me"],
                         [("cl99-launch-me", 5)])
        r["batches"] = [b for b in r["batches"] if any(e["doc_id"] == "cl99-launch-me" for e in json.load(open(b)))]
        self.assertEqual(r["board"], ART_URL)
        k.artifact_sent(r["batches"][0])
        self.assertEqual(k._art_state()["versions"]["posts/cl99-launch-me"], 6)


class Launch(Base):
    def seed(self, config):
        repo = make_repo(self.tmp / "repo", config)
        self.local("posts/cl99-launch-me", POST)
        self.local("posts/_trains", TRAINS)
        return repo

    def fake_claude(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        log = self.tmp / "claude.log"
        f = bindir / "claude"
        f.write_text("#!/bin/sh\nprintf '%%s\\n' \"$PWD\" \"$@\" > %s\n" % log)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        return log

    def running_on_board(self, k, pid="cl99-launch-me"):
        p = self.local("posts/" + pid)["data"]
        self.assertEqual(p["status"], "in progress")
        self.assertTrue(p["launched_at"].endswith("Z"))
        self.assertEqual(p["branch"], "cl99")
        rows = {r["part"]: r for r in self.local("posts/_trains")["data"]["rows"]}
        self.assertEqual(rows["CL99"]["state"], "running")
        self.assertEqual(rows["CL99"]["post"], pid)
        self.assertEqual(rows["CL99"]["session"], p["session_id"])
        self.assertIn("CL99", rows["P-cli"]["children"])
        self.assertEqual(rows["P-cli"]["state"], "running")
        return p

    def test_cli_launch_writes_the_post_and_runs_in_a_worktree_from_the_current_branch(self):
        repo = self.seed({"project": "Board", "board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local"}})
        log = self.fake_claude()
        k = self.kit_in(repo)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=False, gate=False)
        self.assertTrue(r["ok"], r)
        p = self.running_on_board(k)
        self.assertEqual(p["session_id"], "board-cl99")
        wt = Path(r["worktree"])
        self.assertTrue((wt / ".kit" / "config.json").exists())        # made from `work`, not main... both have it
        self.assertEqual(git(wt, "rev-parse", "HEAD"), git(repo, "rev-parse", "work"))
        args = log.read_text().splitlines()
        self.assertEqual(Path(args[0]).resolve(), wt.resolve())          # claude runs inside that worktree
        self.assertIn("--bg", args)
        self.assertNotIn("-w", args)                                    # never `claude -w` (a worktree from main)
        self.assertIn("Your prompt is docs/prompts/cl99-launch-me.md: read it and do it.", args)

    def test_cli_launch_refuses_an_uncommitted_prompt(self):
        repo = self.seed({"project": "Board", "kit": {"runtime": "local"}})
        (repo / "docs" / "prompts" / "cl99-launch-me.md").write_text("changed, not committed\n")
        self.fake_claude()
        k = self.kit_in(repo)
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN, web=False, gate=False)
        self.assertFalse(r["ok"])
        self.assertIn("not committed", r["error"])
        self.assertEqual(self.local("posts/cl99-launch-me")["data"]["status"], "planned")

    def test_web_launch_writes_the_post_trains_and_the_artifact_batch(self):
        repo = self.seed({"project": "Board", "board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local",
                          "github": "anlonlt/board", "artifact": {"url": ART_URL, "prefix": "repos/board/"}}})
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        k = self.kit_in(repo)
        # the copy was loaded from the Artifact: same content, versions known
        for did, data in (("cl99-launch-me", POST), ("_trains", TRAINS)):
            base = Path(k._base_path("posts", did))
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_text(json.dumps(data))
        k.artifact_pin(["posts/cl99-launch-me=2", "posts/_trains=7"])
        r = k.launch("cl99-launch-me", url=self.url, token=TOKEN)
        self.assertTrue(r["ok"], r)
        call = r["create_session"]                                      # what the session passes to create_session
        self.assertEqual(call["source_url"], "https://github.com/anlonlt/board")
        self.assertEqual(call["source_revision"], "work")
        self.assertEqual(call["outcome_branch"], "cl99")
        self.assertIn("docs/prompts/cl99-launch-me.md", call["prompt"])
        self.running_on_board(k)
        # the faked create_session answered: its id goes on the post and the Trains row
        r2 = k.launch("cl99-launch-me", url=self.url, token=TOKEN, session_id="session_01FAKE")
        self.assertTrue(r2["ok"], r2)
        p = self.running_on_board(k)
        self.assertEqual(p["session_id"], "session_01FAKE")
        es = {(e["collection"], e["doc_id"]): e for b in r2["batches"] for e in json.load(open(b))}
        post_e = es[("repos/board/posts", "cl99-launch-me")]
        trains_e = es[("repos/board/posts", "_trains")]
        self.assertEqual((post_e["if_version"], trains_e["if_version"]), (2, 7))
        # apply the batch to a fake Artifact: the board shows the part running, nobody edited anything by hand
        art = {"posts/cl99-launch-me": POST, "posts/_trains": TRAINS}
        for e in (post_e, trains_e):
            art["posts/" + e["doc_id"]] = json.load(open(e["file_path"]))
        self.assertEqual(art["posts/cl99-launch-me"]["status"], "in progress")
        self.assertEqual(art["posts/cl99-launch-me"]["session_id"], "session_01FAKE")
        rows = {x["part"]: x for x in art["posts/_trains"]["rows"]}
        self.assertEqual((rows["CL99"]["state"], rows["CL99"]["session"]), ("running", "session_01FAKE"))
        self.assertIn("create_session", k.launch_text(r))
        self.assertIn("ArtifactData", k.launch_text(r2))

    def test_launch_line_on_the_web_names_the_launch_command(self):
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        k = load_kit()
        self.assertIn("kit.py launch", k._launch_line("CL99", "docs/prompts/cl99-launch-me.md", post="cl99-launch-me"))


class ResumeFlagsDeadPart(unittest.TestCase):
    def test_launched_died(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            repo = make_repo(t / "repo", {"project": "Board"})
            wt = t / "repo" / ".claude" / "worktrees" / "cl99"
            git(repo, "worktree", "add", "-q", "-b", "cl99", str(wt), "work")
            (wt / "half-done.py").write_text("x = 1\n")
            k = load_kit()
            k.ROOT = str(repo)
            db = t / "board.db"
            import sqlite3
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT NULL"
                         " DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
            posts = {"cl99-launch-me": dict(POST, status="planned"),
                     "cl98-other": dict(POST, part="CL98", title="never launched", created_at="2026-11-23T00:01:00Z")}
            for pid, p in posts.items():
                conn.execute("INSERT INTO docs VALUES ('posts', ?, 1, ?, '2026-11-23')", (pid, json.dumps(p)))
            conn.commit()
            conn.close()
            out = k.resume(str(t), db_path=str(db), alive=set())
            line = next(x for x in out.splitlines() if "cl99-launch-me" in x)
            self.assertIn("launched, died", line)
            self.assertIn("half-done.py", out)
            other = next(x for x in out.splitlines() if "cl98-other" in x)
            self.assertNotIn("died", other)


if __name__ == "__main__":
    unittest.main()
