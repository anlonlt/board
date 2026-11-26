#!/usr/bin/env python3
"""Tests for Antigravity-on-the-web direct kit support, blob mirror sync, and antigravity_web.py adapter."""
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    path = os.path.join(ROOT, rel)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wt = _load("kit_wt_aw_test", "kit/web_takeover.py")
kit = _load("kit_core_aw_test", "scripts/kit.py")
aw = _load("kit_aw_test", "kit/antigravity_web.py")


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, r.stderr or r.stdout))
    return r.stdout.strip()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DetectionTests(unittest.TestCase):
    def test_is_antigravity_web_env_variants(self):
        base = {k: v for k, v in os.environ.items()
                if k not in ("CLAUDE_CODE_REMOTE", "ANTIGRAVITY_APP_DATA_DIR", "JULES_SESSION_ID", "AGY_WEB")}
        with mock.patch.dict(os.environ, base, clear=True):
            self.assertFalse(wt.is_antigravity_web())
            self.assertFalse(kit._is_antigravity_web())

        for extra in ({"ANTIGRAVITY_APP_DATA_DIR": "/home/antigravity/.gemini/jetski"},
                      {"JULES_SESSION_ID": "abc-123"},
                      {"AGY_WEB": "1"}):
            env = dict(base, **extra)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertTrue(wt.is_antigravity_web(), extra)
                self.assertTrue(kit._is_antigravity_web(), extra)

        # Claude Code remote takes precedence over Antigravity detection
        env = dict(base, CLAUDE_CODE_REMOTE="true", ANTIGRAVITY_APP_DATA_DIR="/tmp/x")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(wt.is_antigravity_web())
            self.assertFalse(kit._is_antigravity_web())


class SyncDbAndMirrorBlobTests(unittest.TestCase):
    def test_sync_db_fresh_container_does_not_drop_writes_when_seq_smaller_or_equal(self):
        tmp = tempfile.mkdtemp(prefix="test-aw-syncdb-")
        try:
            mirror = os.path.join(tmp, "mirror")
            os.makedirs(os.path.join(mirror, "board", "posts"), exist_ok=True)
            os.makedirs(os.path.join(mirror, "board", "comments"), exist_ok=True)
            # Simulate a mirror synced previously by another host at db_seq=322
            with open(os.path.join(mirror, "board", "posts", "r1.json"), "w", encoding="utf-8") as fh:
                json.dump({"title": "R1", "status": "planned", "body": "old"}, fh)
            with open(os.path.join(mirror, "board", "sync.json"), "w", encoding="utf-8") as fh:
                json.dump({"cursor": "2026-11-26T21:16:43Z", "db_seq": 322, "db_origin": "other-host:1@2026-11-20T00:00:00Z", "log": []}, fh)

            db_dir = os.path.join(tmp, "board_data")
            os.makedirs(os.path.join(db_dir, "blobs"), exist_ok=True)
            db_path = os.path.join(db_dir, "board.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT, PRIMARY KEY (collection, doc_id))")
            conn.execute("CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT, doc_id TEXT, op TEXT, version INTEGER, at TEXT)")
            conn.execute(
                "INSERT INTO docs VALUES ('posts', 'r1', 2, ?, '2026-11-26T21:20:00Z')",
                (json.dumps({"title": "R1", "status": "in progress", "body": "updated from devbox"}),),
            )
            conn.execute("INSERT INTO changes(collection, doc_id, op, version, at) VALUES ('posts', 'r1', 'set', 2, '2026-11-26T21:20:00Z')")
            conn.commit()
            conn.close()

            # Add a local blob too
            bid = hashlib.md5(b"hello-blob").hexdigest()
            with open(os.path.join(db_dir, "blobs", bid), "wb") as fh:
                fh.write(b"hello-blob")
            with open(os.path.join(db_dir, "blobs", bid + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"id": bid, "name": "hello.txt", "contentType": "text/plain", "size": 10}, fh)

            # Plain sync_db (without full=True) must detect origin/seq mismatch and fall back to full sync
            pw, pr, new, changed, removed = kit.sync_db(mirror, db_path=db_path, at="2026-11-26T21:21:00Z", with_blobs=True)
            self.assertEqual(pw, 1)
            with open(os.path.join(mirror, "board", "posts", "r1.json"), encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["body"], "updated from devbox")
            self.assertTrue(os.path.isfile(os.path.join(mirror, "board", "blobs", bid)))
            self.assertTrue(os.path.isfile(os.path.join(mirror, "board", "blobs", bid + ".json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_restore_mirror_blobs_from_git_ref(self):
        tmp = tempfile.mkdtemp(prefix="test-aw-blobs-")
        try:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, "board", "blobs"), exist_ok=True)
            _git(repo, "init", "-b", "board-mirror")
            _git(repo, "config", "user.name", "Test")
            _git(repo, "config", "user.email", "test@example.com")

            bid = hashlib.md5(b"script-payload").hexdigest()
            with open(os.path.join(repo, "board", "blobs", bid), "wb") as fh:
                fh.write(b"script-payload")
            with open(os.path.join(repo, "board", "blobs", bid + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"id": bid, "name": "adapter.py", "contentType": "text/x-python", "size": 14}, fh)
            # Non-hex filename must be ignored
            with open(os.path.join(repo, "board", "blobs", "README.txt"), "w", encoding="utf-8") as fh:
                fh.write("ignore me")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "add mirror blob")

            db_path = os.path.join(tmp, "local_board", "board.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            restored = kit._restore_mirror_blobs(repo, "board-mirror", db_path)
            self.assertEqual(restored, 1)
            blob_dir = os.path.join(tmp, "local_board", "blobs")
            with open(os.path.join(blob_dir, bid), "rb") as fh:
                self.assertEqual(fh.read(), b"script-payload")
            with open(os.path.join(blob_dir, bid + ".json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["name"], "adapter.py")
            self.assertFalse(os.path.exists(os.path.join(blob_dir, "README.txt")))
            # Second call is a no-op (existing blobs not overwritten)
            self.assertEqual(kit._restore_mirror_blobs(repo, "board-mirror", db_path), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_single_branch_clone_ensure_refs_fetches_board_mirror(self):
        tmp = tempfile.mkdtemp(prefix="test-aw-single-")
        try:
            bare = os.path.join(tmp, "origin.git")
            seed = os.path.join(tmp, "seed")
            clone = os.path.join(tmp, "clone")
            _git(tmp, "init", "--bare", bare)
            _git(tmp, "clone", bare, seed)
            _git(seed, "config", "user.name", "Test")
            _git(seed, "config", "user.email", "test@example.com")
            with open(os.path.join(seed, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello\n")
            _git(seed, "add", "README.md")
            _git(seed, "commit", "-m", "init")
            _git(seed, "branch", "-M", "feat-work")
            _git(seed, "push", "-u", "origin", "feat-work")

            # Create orphan board-mirror on origin
            _git(seed, "checkout", "--orphan", "board-mirror")
            _git(seed, "rm", "-rf", ".")
            os.makedirs(os.path.join(seed, "board", "posts"), exist_ok=True)
            with open(os.path.join(seed, "board", "posts", "p1.json"), "w", encoding="utf-8") as fh:
                json.dump({"title": "Post 1"}, fh)
            _git(seed, "add", ".")
            _git(seed, "commit", "-m", "mirror init")
            _git(seed, "push", "origin", "board-mirror")

            # Single-branch clone (just like Antigravity on the web)
            _git(tmp, "clone", "--single-branch", "--branch", "feat-work", bare, clone)
            # Without explicit refspec, origin/board-mirror does not exist
            r0 = subprocess.run(["git", "-C", clone, "rev-parse", "--verify", "origin/board-mirror"], capture_output=True)
            self.assertNotEqual(r0.returncode, 0)

            res = aw.ensure_refs(("board-mirror",), cwd=clone)
            self.assertTrue(res["board-mirror"][0], res)
            out = _git(clone, "show", "origin/board-mirror:board/posts/p1.json")
            self.assertEqual(json.loads(out)["title"], "Post 1")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_upload_and_attach_blobs_on_ephemeral_board(self):
        tmp = tempfile.mkdtemp(prefix="test-aw-attach-")
        proc = None
        try:
            kit_dir = os.path.join(tmp, ".kit")
            os.makedirs(os.path.join(kit_dir, "board_data"), exist_ok=True)
            port = _free_port()
            tok = "testtoken1234567890abcdef1234567"
            with open(os.path.join(kit_dir, "board_data", "token"), "w", encoding="utf-8") as fh:
                fh.write(tok)
            with open(os.path.join(kit_dir, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"board_url": "http://127.0.0.1:%d" % port}, fh)

            proc = subprocess.Popen(
                [sys.executable, "-I", os.path.join(ROOT, "kit/board/board_server.py"),
                 "--host", "127.0.0.1", "--port", str(port), "--root", tmp, "--token", tok,
                 "--auto-sync-interval", "0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            url = "http://127.0.0.1:%d" % port
            for _ in range(50):
                try:
                    import urllib.request
                    with urllib.request.urlopen(url + "/_api/health", timeout=1) as r:
                        if json.loads(r.read().decode()).get("ok"):
                            break
                except Exception:
                    time.sleep(0.1)
            else:
                self.fail("ephemeral board_server did not become healthy")

            env = dict(os.environ, KIT_BOARD_URL=url, KIT_BOARD_TOKEN=tok)
            bcli = os.path.join(ROOT, "kit/board/board_cli.py")
            r_set = subprocess.run(
                [sys.executable, "-I", bcli, "set", "posts", "r1-test", "--data",
                 json.dumps({"title": "R1 Test", "column": "info", "body": "Lead line\n\n### Details\nBody text", "created_at": "2026-11-26T21:00:00Z"})],
                capture_output=True, text=True, env=env, cwd=tmp,
            )
            self.assertEqual(r_set.returncode, 0, r_set.stderr)

            f1 = os.path.join(tmp, "sample.patch")
            f2 = os.path.join(tmp, "helper.py")
            with open(f1, "w", encoding="utf-8") as fh:
                fh.write("diff --git a/x b/x\n")
            with open(f2, "w", encoding="utf-8") as fh:
                fh.write("print('hi')\n")

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(aw, "ensure_board_running", return_value="ok"), \
                 mock.patch.object(aw, "ROOT", tmp), \
                 mock.patch.object(aw, "KIT", kit_dir), \
                 mock.patch.object(aw, "_kit_script", side_effect=lambda n: bcli if n == "board_cli.py" else os.path.join(ROOT, "scripts", n)):
                res = aw.upload_and_attach("r1-test", [f1, f2], comment_text="Attached review blobs", sender="R1")

            self.assertEqual(len(res["uploaded"]), 2)
            self.assertIsNotNone(res["comment"])

            r_get = subprocess.run([sys.executable, "-I", bcli, "get", "posts", "r1-test"], capture_output=True, text=True, env=env, cwd=tmp)
            post = json.loads(r_get.stdout)["data"]
            self.assertEqual([x["name"] for x in post["files"]], ["sample.patch", "helper.py"])
            self.assertEqual(len(post["artifacts"]), 2)
            self.assertTrue(all(a.startswith("/_blob/") for a in post["artifacts"]))
            self.assertIn("### Attached review blobs", post["body"])
            self.assertIn("[sample.patch](/_blob/", post["body"])
        finally:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
