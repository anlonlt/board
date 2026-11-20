"""Tests for Arena out-of-box enhancements and zero-regression guarantees for agy & Claude Code Web.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit" / "board"))
import board_server  # noqa: E402

BOARD_CLI = str(ROOT / "kit" / "board" / "board_cli.py")
INSTALL_PY = str(ROOT / "install.py")


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as res:
        return res.status, dict(res.headers.items()), res.read()


class TestArenaOutOfBox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, ".kit"), exist_ok=True)
        with open(os.path.join(self.root, ".kit", "board.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><html><head><title>Board</title></head><body>board</body></html>")

    def test_preview_mode_allows_0000_auto_seeds_and_registers_watcher(self):
        # Default (preview=False) still strictly refuses 0.0.0.0 without token
        with self.assertRaises(ValueError):
            board_server.start_server(self.root, "0.0.0.0", 0, token=None, preview=False)

        # Opt-in preview=True allows 0.0.0.0 without token, seeds _orchestrator, registers default watcher
        srv, store = board_server.start_server(self.root, "0.0.0.0", 0, token=None, embed_mode=True, preview=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()

        self.addCleanup(_stop)
        url = "http://127.0.0.1:%d" % srv.server_address[1]

        self.assertTrue(store.get_doc("posts", "_orchestrator")["exists"])
        code, _, raw = _get(url + "/_api/comments/status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw.decode("utf-8"))["status"], "available")

        # SHIM_JS includes iframe detection + Copy Markdown modal fallback while keeping <a download>
        code, _, shim_raw = _get(url + "/_shim.js")
        self.assertEqual(code, 200)
        shim = shim_raw.decode("utf-8")
        self.assertIn("window.self !== window.top", shim)
        self.assertIn("openExportModal", shim)
        self.assertIn("Copy Markdown", shim)
        self.assertIn("/_api/export", shim)

    def test_export_route_and_cli_inbox_reply(self):
        srv, store = board_server.start_server(self.root, "127.0.0.1", 0, preview=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()

        self.addCleanup(_stop)
        url = "http://127.0.0.1:%d" % srv.server_address[1]

        store.set_doc("posts", "p-demo", {
            "title": "Demo Post",
            "column": "question",
            "part": "demo",
            "priority": 1,
            "created_at": "2026-11-20T10:00:00Z",
            "body": "Hello export.",
        })
        thr = store.send_comment(anchor={"id": "p-demo"}, text="Owner comment")
        store.set_doc("comments", "c-own-1", {
            "post_id": "p-demo",
            "from": "owner",
            "text": "Owner comment",
            "thread_id": thr["thread_id"],
            "read": False,
            "created_at": "2026-11-20T10:01:00Z",
        })
        store.set_doc("comments", "c-like-1", {
            "post_id": "p-demo",
            "from": "owner",
            "text": "👍",
            "like": True,
            "comment_id": "welcome-arena-1",
            "read": False,
            "created_at": "2026-11-20T10:02:00Z",
        })
        store.set_doc("comments", "c-ans-1", {
            "post_id": "p-demo",
            "from": "owner",
            "text": "Answer: Live 30-min listener",
            "answer": "Live 30-min listener",
            "read": False,
            "created_at": "2026-11-20T10:03:00Z",
        })

        # GET /_api/export single post (attachment) and raw inline
        code, hdrs, raw = _get(url + "/_api/export?post=p-demo")
        self.assertEqual(code, 200)
        self.assertIn("text/markdown", hdrs.get("Content-Type", ""))
        self.assertIn("attachment", hdrs.get("Content-Disposition", ""))
        md = raw.decode("utf-8")
        self.assertIn("# Demo Post", md)
        self.assertIn("Owner comment", md)

        code_r, hdrs_r, _ = _get(url + "/_api/export?all=1&raw=1")
        self.assertEqual(code_r, 200)
        self.assertEqual(hdrs_r.get("Content-Disposition"), "inline")

        # board_cli.py inbox returns all 3 unread owner items
        r_in = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "inbox"],
            capture_output=True, text=True, check=True,
        )
        inbox = json.loads(r_in.stdout)
        self.assertEqual(inbox["total"], 3)
        self.assertEqual(len(inbox["comments"]), 1)
        self.assertEqual(len(inbox["likes"]), 1)
        self.assertEqual(len(inbox["answers"]), 1)
        self.assertEqual(inbox["comments"][0]["id"], "c-own-1")

        # board_cli.py reply marks owner comment read, acks thread, and posts reply with reply_to
        r_rep = subprocess.run(
            [
                sys.executable, "-I", BOARD_CLI, "--url", url,
                "reply", "p-demo",
                "--comment-id", "c-own-1",
                "--text", "Got it, replying in-thread.",
            ],
            capture_output=True, text=True, check=True,
        )
        rep = json.loads(r_rep.stdout)
        self.assertTrue(rep["marked_read"])
        self.assertEqual(rep["acked_thread"], thr["thread_id"])
        self.assertEqual(rep["reply_to"], thr["thread_id"])

        own_doc = store.get_doc("comments", "c-own-1")["data"]
        self.assertTrue(own_doc["read"])
        self.assertTrue(own_doc.get("read_at"))
        rep_doc = store.get_doc("comments", rep["id"])["data"]
        self.assertEqual(rep_doc["reply_to"], thr["thread_id"])
        self.assertEqual(rep_doc["text"], "Got it, replying in-thread.")

        # Inbox now has 2 remaining unread items (like + answer)
        r_in2 = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "inbox"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(r_in2.stdout)["total"], 2)

    def test_install_arena_agents_md_bootstrap_demo_and_no_regression_on_agy_claude(self):
        with tempfile.TemporaryDirectory() as d:
            # 1. Arena default installs AGENTS.md + auto-bootstraps SQLite DB
            t_arena = os.path.join(d, "arena_app")
            os.makedirs(t_arena)
            r = subprocess.run(
                [sys.executable, "-I", INSTALL_PY, t_arena, "--project", "ArenaApp", "--no-survey", "test", "--runtime", "arena", "--demo"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            agents_md = os.path.join(t_arena, "AGENTS.md")
            self.assertTrue(os.path.isfile(agents_md))
            with open(agents_md, encoding="utf-8") as fh:
                agents_txt = fh.read()
            self.assertIn("board_cli.py inbox", agents_txt)
            self.assertIn("comments wait --timeout 1750", agents_txt)
            self.assertIn("--preview --embed-mode", agents_txt)
            self.assertNotIn("{{", agents_txt)

            st = board_server.BoardStore(os.path.join(t_arena, ".kit", "board_data"))
            try:
                for pid in ("_orchestrator", "KIT-installed", "demo-todo", "demo-question", "demo-done", "_dashboard", "_trains"):
                    self.assertTrue(st.get_doc("posts", pid)["exists"], "missing seeded post " + pid)
            finally:
                st.close()

            # 2. agy and claude do NOT generate AGENTS.md
            for rt in ("agy", "claude"):
                t_other = os.path.join(d, "proj_" + rt)
                os.makedirs(t_other)
                env = dict(os.environ, KIT_BIN_DIR=os.path.join(d, "bin"))
                r2 = subprocess.run(
                    [sys.executable, "-I", INSTALL_PY, t_other, "--project", "Other", "--no-survey", "test", "--runtime", rt],
                    capture_output=True, text=True, env=env,
                )
                self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
                self.assertFalse(os.path.exists(os.path.join(t_other, "AGENTS.md")))


if __name__ == "__main__":
    unittest.main()
