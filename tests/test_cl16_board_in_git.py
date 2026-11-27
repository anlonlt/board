"""CL16: the board travels in git, always current (real repositories, a real board server, a bare origin).

The 2026-11-22 loss: four step posts made while the board server ran with --auto-sync-push never reached
origin/board-mirror. Cause: the delta cursor `db_seq` lived in git (board/sync.json) but counts ONE board.db's changes;
another machine's (or a web session's) higher number made every delta ask for seq > it and write nothing, while the
commit and the push reported success. These tests fail on that code.

Run via: python3 -m unittest tests.test_cl16_board_in_git -v
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"board_data", "run", "__pycache__", "artifact-files"}
TOKEN = "cl16-test-token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Board(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="cl16-")
        self.addCleanup(shutil.rmtree, self.t, True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY", "GIT_"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, KIT_NO_LAUNCH="1", KIT_TEST="1",
                        KIT_TEST_GUARD=str(ROOT / "kit" / "test_guard.py"), KIT_USAGE_SELF="0", KIT_IMPORT_AT_START="0",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.o = os.path.join(self.t, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.o], check=True)
        self.src = os.path.join(self.t, "src")
        os.makedirs(self.src)
        self.g("init", "-q", "-b", "main")
        shutil.copytree(str(ROOT / ".kit"), os.path.join(self.src, ".kit"), ignore=lambda d, names: [n for n in names if n in SKIP])
        shutil.copy(str(ROOT / "kit" / "test_guard.py"), os.path.join(self.src, ".kit", "test_guard.py"))
        cfg = json.loads(Path(ROOT, ".kit", "config.json").read_text())
        cfg["mirror"] = {"branch": "board-mirror", "path": "../src-mirror", "overlap_hours": 1}
        cfg.pop("board_url", None)
        k = cfg.setdefault("kit", {})
        for key in ("artifact", "artifact_url", "portfolio", "web_env"):
            k.pop(key, None)
            cfg.pop(key, None)
        Path(self.src, ".kit", "config.json").write_text(json.dumps(cfg, indent=1))
        Path(self.src, ".gitignore").write_text(".kit/board_data/\n.kit/run/\n__pycache__/\n")
        self.g("add", "-A")
        self.g("commit", "-qm", "init")
        self.g("remote", "add", "origin", self.o)
        self.g("push", "-q", "origin", "main")
        self.g("push", "-q", "origin", "main:board-mirror")
        self.kit("init", d=self.src)
        self.servers = []
        self.addCleanup(self._stop_servers)

    def _stop_servers(self):
        for p, log in self.servers:
            p.kill()
            p.wait()
            log.close()

    # ------------------------------------------------------------------ helpers
    def g(self, *a, d=None):
        return subprocess.run(["git", "-C", d or self.src] + list(a), capture_output=True, text=True, env=self.env)

    def run_(self, script, *a, d=None, **env):
        d = d or self.src
        return subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", script)] + list(a), capture_output=True,
                              text=True, env=dict(self.env, **env), cwd=d, timeout=180)

    def kit(self, *a, d=None, **env):
        return self.run_("kit.py", *a, d=d, **env)

    def serve(self, d=None, sync="0.2", push=True, **env):
        d = d or self.src
        port = free_port()
        log = open(os.path.join(self.t, "server-%d.log" % port), "w+")
        args = [sys.executable, "-I", os.path.join(d, ".kit", "board_server.py"), "--port", str(port), "--root", d,
                "--token", TOKEN, "--auto-sync-interval", sync] + (["--auto-sync-push"] if push else [])
        p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=dict(self.env, **env), cwd=d)
        self.servers.append((p, log))
        url = "http://127.0.0.1:%d" % port
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        return url, log

    def on_origin(self, pid, timeout=20.0):
        """The post as origin/board-mirror holds it, waiting up to timeout; None if it never arrives."""
        end = time.time() + timeout
        while time.time() < end:
            r = subprocess.run(["git", "-C", self.o, "show", "board-mirror:board/posts/%s.json" % pid], capture_output=True, text=True)
            if r.returncode == 0:
                return json.loads(r.stdout)
            time.sleep(0.2)
        return None

    def foreign_cursor(self, m, seq=9999):
        """sync.json in git as another machine's board left it: a delta cursor far above this board.db's."""
        p = os.path.join(m, "board", "sync.json")
        st = json.loads(Path(p).read_text()) if os.path.exists(p) else {"cursor": "2026-11-22T00:00:00Z", "log": []}
        st.update(db_seq=seq, db_seqs={"another-machine": seq}, cursor=st.get("cursor") or "2026-11-22T00:00:00Z")
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps(st))
        self.g("add", "-A", "board", d=m)
        self.g("commit", "-qm", "another machine's sync", d=m)
        self.assertEqual(self.g("push", "-q", "origin", "HEAD:board-mirror", d=m).returncode, 0)

    # ------------------------------------------------------------------ the diagnosis, as tests
    def test_a_step_change_reaches_origin_even_after_another_machines_cursor(self):
        url, log = self.serve()
        env = dict(KIT_BOARD_URL=url, KIT_BOARD_TOKEN=TOKEN)
        self.assertEqual(self.run_("board_cli.py", "set", "posts", "p-first", "--data", '{"title":"first","column":"info"}', **env).returncode, 0)
        self.assertIsNotNone(self.on_origin("p-first"), "the first write never reached origin/board-mirror")
        self.foreign_cursor(os.path.join(self.t, "src-mirror"))
        r = self.kit("step", "new", "--train", "cli", "--part", "CL97", "--title", "after the other machine",
                     "--task", "do one thing", "--keep-train", "test", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pid = json.loads(r.stdout)["post"]
        got = self.on_origin(pid)
        log.seek(0)
        self.assertIsNotNone(got, "step %s never reached origin/board-mirror (server log: %s)" % (pid, log.read()[-800:]))
        self.assertEqual(got["part"], "CL97")

    def test_a_failed_sync_is_said_in_the_server_log_and_retried(self):
        m = os.path.join(self.t, "src-mirror")
        self.g("checkout", "-q", "--detach", d=m)          # not on board-mirror: the kit refuses to commit there
        url, log = self.serve(KIT_AUTO_SYNC_RETRY_MAX="0.5")
        env = dict(KIT_BOARD_URL=url, KIT_BOARD_TOKEN=TOKEN)
        self.run_("board_cli.py", "set", "posts", "p-held", "--data", '{"title":"held","column":"info"}', **env)
        text = ""
        for _ in range(60):
            log.seek(0)
            text = log.read()
            if "[auto-sync]" in text and "FAILED" in text:
                break
            time.sleep(0.2)
        self.assertIn("FAILED at sync-db", text)
        self.assertIn("not on branch board-mirror", text)
        self.g("checkout", "-q", "board-mirror", d=m)       # fixed by hand: the retry sends it, with no new write
        self.assertIsNotNone(self.on_origin("p-held"), "the retry never sent the held post")
        for _ in range(30):
            log.seek(0)
            if "recovered" in log.read():
                break
            time.sleep(0.2)
        log.seek(0)
        self.assertIn("recovered", log.read())

    def test_a_refused_commit_is_an_error_not_committed(self):
        m = os.path.join(self.t, "src-mirror")
        hooks = os.path.join(self.t, "hooks")
        os.makedirs(hooks)
        Path(hooks, "pre-commit").write_text("#!/bin/sh\necho refused-by-hook >&2\nexit 1\n")
        os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
        self.g("config", "core.hooksPath", hooks, d=m)
        Path(m, "board", "posts").mkdir(parents=True, exist_ok=True)
        Path(m, "board", "posts", "x.json").write_text('{"title": "x"}\n')
        r = self.kit("sync", os.path.join(m, "board"), "--at", "2026-11-27T00:00:00Z", "--commit", d=self.src)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("refused-by-hook", r.stdout + r.stderr)

    # ------------------------------------------------------------------ the kit's own path (no server auto-sync)
    def test_kit_step_changes_push_the_board_without_the_servers_auto_sync(self):
        url, _ = self.serve(sync="0", push=False)
        env = dict(KIT_BOARD_URL=url, KIT_BOARD_TOKEN=TOKEN, KIT_MIRROR_SYNC="inline")
        r = self.kit("step", "new", "--train", "cli", "--part", "CL96", "--title", "kit path", "--task", "one thing",
                     "--keep-train", "test", **env)
        pid = json.loads(r.stdout)["post"]
        self.assertEqual(self.on_origin(pid, timeout=5)["status"], "planned")
        self.assertEqual(self.kit("step", "start", pid, **env).returncode, 0)
        self.assertEqual(self.on_origin(pid, timeout=5)["status"], "in progress")
        self.assertIn("pushed", self.kit("mirror-sync", "--final", **env).stdout)

    # ------------------------------------------------------------------ a fresh clone reads the same board
    def test_a_fresh_clone_without_a_local_board_resumes_the_same(self):
        url, _ = self.serve(sync="0.2")
        env = dict(KIT_BOARD_URL=url, KIT_BOARD_TOKEN=TOKEN)
        for part in ("CL91", "CL92"):
            r = self.kit("step", "new", "--train", "cli", "--part", part, "--title", "step " + part, "--task", "a thing",
                         "--keep-train", "test", **env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            pid = json.loads(r.stdout)["post"]
        self.kit("step", "start", pid, **env)
        for _ in range(100):
            got = self.on_origin(pid, timeout=0.2)
            if got and got.get("status") == "in progress":
                break
        here = self.kit("resume", **dict(env, KIT_MIRROR_FETCH="1"))
        fresh = os.path.join(self.t, "fresh")
        subprocess.run(["git", "clone", "-q", self.o, fresh], check=True, env=self.env)
        self.assertFalse(os.path.exists(os.path.join(fresh, ".kit", "board_data")))
        there = self.kit("resume", d=fresh, KIT_MIRROR_FETCH="1", KIT_BOARD_URL="http://127.0.0.1:9")

        def body(out):
            return "\n".join(l for l in out.splitlines()                # the machine's free memory moves between runs
                             if not l.startswith("(board read from") and "MB free" not in l).strip()
        self.assertIn("CL91", body(here.stdout))
        self.assertEqual(body(here.stdout), body(there.stdout))
        self.assertIn("(board read from its git copy origin/board-mirror", there.stdout)

    # ------------------------------------------------------------------ a write with no server, imported later
    def test_a_write_from_the_git_side_shows_on_the_local_board_after_import(self):
        fresh = os.path.join(self.t, "fresh")
        subprocess.run(["git", "clone", "-q", self.o, fresh], check=True, env=self.env)
        env = dict(KIT_MIRROR_SYNC="inline", KIT_BOARD_URL="http://127.0.0.1:9")   # no board server on this machine
        r = self.run_("board_cli.py", "set", "posts", "p-from-web", "--data",
                      '{"title":"written where no server runs","column":"info"}', d=fresh, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("git copy", r.stderr)
        self.assertEqual(self.on_origin("p-from-web", timeout=5)["title"], "written where no server runs")
        got = json.loads(self.run_("board_cli.py", "get", "posts", "p-from-web", d=fresh, **env).stdout)
        self.assertTrue(got["exists"])
        url, log = self.serve(sync="0.2", KIT_IMPORT_AT_START="1")   # the computer's board server starts
        found = None
        for _ in range(100):
            out = self.run_("board_cli.py", "--url", url, "--token", TOKEN, "get", "posts", "p-from-web").stdout
            if out and json.loads(out).get("exists"):
                found = json.loads(out)["data"]
                break
            time.sleep(0.2)
        log.seek(0)
        self.assertIsNotNone(found, "the git-side post never reached the local board (log: %s)" % log.read()[-600:])
        self.assertEqual(found["title"], "written where no server runs")

    # ------------------------------------------------------------------ CR-cl16: a named database reads only itself
    def test_resume_with_db_never_shows_an_owner_comment_from_origin(self):
        m = os.path.join(self.t, "src-mirror")
        Path(m, "board", "posts").mkdir(parents=True, exist_ok=True)
        Path(m, "board", "comments").mkdir(parents=True, exist_ok=True)
        Path(m, "board", "posts", "p-real.json").write_text(json.dumps(
            {"title": "the real board", "column": "info", "created_at": "2026-11-27T01:00:00Z"}))
        Path(m, "board", "comments", "c-owner.json").write_text(json.dumps(
            {"post_id": "p-real", "from": "owner", "text": "LEAK-MARKER-CL16 is this on?", "created_at": "2026-11-27T02:00:00Z"}))
        self.g("add", "-A", "board", d=m)
        self.g("commit", "-qm", "the owner's comment", d=m)
        self.assertEqual(self.g("push", "-q", "origin", "HEAD:board-mirror", d=m).returncode, 0)
        import sqlite3
        db = os.path.join(self.t, "named.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
        c.execute("INSERT INTO docs VALUES ('posts', 'p-named', 1, ?, '2026-11-27T00:00:00Z')",
                  (json.dumps({"title": "named db only", "column": "info", "created_at": "2026-11-27T00:00:00Z"}),))
        c.commit()
        c.close()
        named = self.kit("resume", "--db", db, KIT_MIRROR_FETCH="1")
        self.assertNotIn("LEAK-MARKER-CL16", named.stdout + named.stderr)
        fresh = os.path.join(self.t, "fresh")                  # the control: the project's own read does show it
        subprocess.run(["git", "clone", "-q", self.o, fresh], check=True, env=self.env)
        own = self.kit("resume", d=fresh, KIT_MIRROR_FETCH="1", KIT_BOARD_URL="http://127.0.0.1:9")
        self.assertIn("LEAK-MARKER-CL16", own.stdout)

    # ------------------------------------------------------------------ both sides wrote: per post, newer wins
    def test_both_sides_wrote_the_newer_copy_of_each_post_wins_nothing_lost(self):
        m = os.path.join(self.t, "src-mirror")
        other = os.path.join(self.t, "other")
        subprocess.run(["git", "clone", "-q", "-b", "board-mirror", self.o, other], check=True, env=self.env)

        def put(d, pid, doc, msg):
            Path(d, "board", "posts").mkdir(parents=True, exist_ok=True)
            Path(d, "board", "posts", pid + ".json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
            self.g("add", "-A", "board", d=d)
            self.g("commit", "-qm", msg, d=d)
        put(m, "shared", {"title": "base", "updated_at": "2026-11-27T01:00:00Z"}, "base")
        self.assertEqual(self.g("push", "-q", "origin", "HEAD:board-mirror", d=m).returncode, 0)
        self.g("pull", "-q", "origin", "board-mirror", d=other)
        put(other, "shared", {"title": "theirs, newer", "updated_at": "2026-11-27T03:00:00Z"}, "theirs")
        put(other, "only-theirs", {"title": "t", "updated_at": "2026-11-27T03:00:00Z"}, "theirs 2")
        self.assertEqual(self.g("push", "-q", "origin", "HEAD:board-mirror", d=other).returncode, 0)
        put(m, "shared", {"title": "ours, older", "updated_at": "2026-11-27T02:00:00Z"}, "ours")
        put(m, "only-ours", {"title": "o", "updated_at": "2026-11-27T02:00:00Z"}, "ours 2")
        r = self.kit("mirror-sync", "--final", KIT_MIRROR_SYNC="inline")
        self.assertIn("merged origin/board-mirror post by post", r.stdout)
        self.assertEqual(self.on_origin("shared", timeout=2)["title"], "theirs, newer")
        self.assertIsNotNone(self.on_origin("only-ours", timeout=2))
        self.assertIsNotNone(self.on_origin("only-theirs", timeout=2))
        log = self.g("log", "--format=%s", "board-mirror", d=self.o).stdout
        self.assertIn("ours 2", log)                       # never forced: both histories are there
        self.assertIn("theirs 2", log)


if __name__ == "__main__":
    unittest.main()
