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
import tempfile as _tf; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-")  # CL11: install.py writes the machine token there, never in the real ~/.config

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "kit" / "board") not in sys.path:
    sys.path.append(str(ROOT / "kit" / "board"))
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

    def test_clerk_atomic_route_seen_pending_and_auto_sync_db(self):
        srv, store = board_server.start_server(self.root, "127.0.0.1", 0, preview=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()

        self.addCleanup(_stop)
        url = "http://127.0.0.1:%d" % srv.server_address[1]

        # Set orchestrator and part post session IDs
        store.update_doc("posts", "_orchestrator", {"session_id": "conv-orch-1"})
        store.set_doc("posts", "B1-work", {
            "title": "Part B1",
            "column": "info",
            "part": "B1",
            "session_id": "conv-part-b1",
            "created_at": "2026-11-20T12:00:00Z",
            "body": "Work post.",
        })

        # 1. Normal comment on B1-work routes to conv-part-b1
        thr1 = store.send_comment(anchor={"id": "B1-work"}, text="[post B1-work] Looks good, check edge case")
        store.set_doc("comments", "c-b1-1", {
            "post_id": "B1-work",
            "from": "owner",
            "text": "Looks good, check edge case",
            "thread_id": thr1["thread_id"],
            "read": False,
            "created_at": "2026-11-20T12:01:00Z",
        })
        r_rt1 = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "comments", "route", thr1["thread_id"]],
            capture_output=True, text=True, check=True,
        )
        rt1 = json.loads(r_rt1.stdout)
        self.assertEqual(rt1["kind"], "comment")
        self.assertEqual(rt1["post_id"], "B1-work")
        self.assertEqual(rt1["comment_doc_id"], "c-b1-1")
        self.assertEqual(rt1["target_role"], "part")
        self.assertEqual(rt1["target_part"], "B1")
        self.assertEqual(rt1["target_session_id"], "conv-part-b1")
        self.assertIn('verbatim: "Looks good, check edge case"', rt1["message"])
        # the forward carries the one command that answers in his thread
        self.assertIn("board_cli.py reply B1-work --comment-id c-b1-1", rt1["message"])

        # 1b. His reply to a review routes to the orchestrator (the review's session has ended)
        store.set_doc("comments", "CR-B1-work", {"post_id": "B1-work", "from": "consultant", "kind": "review",
                                                 "verdict": "redirect", "text": "Verdict: redirect.",
                                                 "created_at": "2026-11-20T12:01:30Z"})
        thr1b = store.send_comment(anchor={"id": "B1-work"}, text="[post B1-work] why redirect?")
        store.set_doc("comments", "c-b1-rv", {"post_id": "B1-work", "from": "owner", "text": "why redirect?",
                                              "reply_to": "CR-B1-work", "thread_id": thr1b["thread_id"], "read": False,
                                              "created_at": "2026-11-20T12:01:40Z"})
        rt1b = json.loads(subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "comments", "route", thr1b["thread_id"]],
            capture_output=True, text=True, check=True).stdout)
        self.assertEqual((rt1b["target_role"], rt1b["target_session_id"]), ("orchestrator", "conv-orch-1"))

        # 2. Comment with @o on B1-work overrides and routes to orchestrator (conv-orch-1)
        thr2 = store.send_comment(anchor={"id": "B1-work"}, text="[post B1-work] @o pause this train")
        store.set_doc("comments", "c-b1-2", {
            "post_id": "B1-work",
            "from": "owner",
            "text": "@o pause this train",
            "thread_id": thr2["thread_id"],
            "read": False,
            "created_at": "2026-11-20T12:02:00Z",
        })
        r_rt2 = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "comments", "route", thr2["thread_id"]],
            capture_output=True, text=True, check=True,
        )
        rt2 = json.loads(r_rt2.stdout)
        self.assertEqual(rt2["target_role"], "orchestrator")
        self.assertEqual(rt2["target_session_id"], "conv-orch-1")

        # 3. Like routes to orchestrator
        thr3 = store.send_comment(anchor={"id": "B1-work"}, text="[post B1-work] 👍 Like: he approves this")
        store.set_doc("comments", "c-b1-like", {
            "post_id": "B1-work",
            "from": "owner",
            "like": True,
            "comment_id": "c-prop-1",
            "thread_id": thr3["thread_id"],
            "read": False,
            "created_at": "2026-11-20T12:03:00Z",
        })
        r_rt3 = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "comments", "route", thr3["thread_id"]],
            capture_output=True, text=True, check=True,
        )
        rt3 = json.loads(r_rt3.stdout)
        self.assertEqual(rt3["kind"], "like")
        self.assertEqual(rt3["target_session_id"], "conv-orch-1")
        self.assertIn("on comment c-prop-1", rt3["message"])

        # 4. comments seen marks read, acks thread, replies Passed to, and appends pending.txt
        pdir = os.path.join(self.root, "clerk_pending_dir")
        r_sn = subprocess.run(
            [
                sys.executable, "-I", BOARD_CLI, "--url", url,
                "comments", "seen", thr1["thread_id"],
                "--doc-id", "c-b1-1",
                "--passed-to", "Passed to B1 (12:01 UTC).",
                "--pending-dir", pdir,
                "--post-id", "B1-work",
                "--part", "B1",
                "--created-at", "2026-11-20T12:01:00Z",
            ],
            capture_output=True, text=True, check=True,
        )
        sn = json.loads(r_sn.stdout)
        self.assertTrue(sn["marked_read"])
        self.assertTrue(sn["acked"])
        self.assertTrue(sn["replied"])
        self.assertTrue(sn["pending_added"])
        self.assertTrue(store.get_doc("comments", "c-b1-1")["data"]["read"])

        # 5. comments pending fetches post comments and runs clerk_pending.py in one call
        r_pd = subprocess.run(
            [
                sys.executable, "-I", BOARD_CLI, "--url", url,
                "comments", "pending",
                "--dir", pdir,
                "--now", "2026-11-20T12:07:00Z",
            ],
            capture_output=True, text=True, check=True,
        )
        pd = json.loads(r_pd.stdout)
        self.assertTrue(any(a.startswith("TELL c-b1-1 B1-work") for a in pd["actions"]), pd)

        # 6. Debounced auto-sync-db writes to board-mirror worktree when present
        mirror_dir = os.path.join(self.root, "board-mirror")
        os.makedirs(mirror_dir, exist_ok=True)
        subprocess.run(["git", "-C", mirror_dir, "init", "-q", "-b", "board-mirror"], check=True)
        subprocess.run(["git", "-C", mirror_dir, "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", mirror_dir, "config", "user.email", "test@example.invalid"], check=True)
        with open(os.path.join(self.root, ".kit", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"mirror": {"branch": "board-mirror", "path": mirror_dir}}, fh)
        store.enable_auto_sync(self.root, interval_sec=0.05)
        store.set_doc("posts", "P-autosync", {"title": "Auto Synced", "column": "info"})
        import time as _time
        for _ in range(30):
            if store.auto_sync_count > 0:
                break
            _time.sleep(0.05)
        self.assertGreater(store.auto_sync_count, 0)
        self.assertTrue(os.path.isfile(os.path.join(mirror_dir, "board", "posts", "P-autosync.json")))

    def test_step0_scan_palettes_autonomy_and_twrp_wizard_headless(self):
        with tempfile.TemporaryDirectory() as d:
            home_dir = os.path.join(d, "home")
            proj_a = os.path.join(home_dir, "proj_alpha")
            proj_b = os.path.join(home_dir, "proj_beta")
            os.makedirs(os.path.join(proj_a, ".git"), exist_ok=True)
            os.makedirs(os.path.join(proj_b, ".git"), exist_ok=True)
            os.makedirs(os.path.join(proj_a, ".board", "memory", "state"), exist_ok=True)

            # Seed CSS with custom brand color #ff3366 and awaiting decisions
            with open(os.path.join(proj_a, "theme.css"), "w", encoding="utf-8") as fh:
                fh.write(":root { --brand: #ff3366; --accent: #00b4d8; }\n")
            with open(os.path.join(proj_a, ".board", "memory", "awaits.md"), "w", encoding="utf-8") as fh:
                fh.write("# Awaits\n- [a] Q1\n- [a] Q2\n- [a] Q3\n- [a] Q4\n- [a] Q5\n")
            for i in range(6):
                with open(os.path.join(proj_a, ".board", "memory", "state", "w%d.md" % i), "w", encoding="utf-8") as fh:
                    fh.write("# W%d\nNext: step %d\nPriority: 1\n" % (i, i))

            # Seed a synthetic agy transcript with high tool-per-turn ratio, short approvals, and tangents
            log_dir = os.path.join(home_dir, ".gemini", "antigravity-cli", "brain", "conv-1", ".system_generated", "logs")
            os.makedirs(log_dir, exist_ok=True)
            events = [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>Build dark mode UI with #ff3366</USER_REQUEST>"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]},
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>yes</USER_REQUEST>"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]},
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>also i wonder what if we add another feature</USER_REQUEST>"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]},
            ]
            with open(os.path.join(log_dir, "transcript.jsonl"), "w", encoding="utf-8") as fh:
                for ev in events:
                    fh.write(json.dumps(ev) + "\n")

            # 1. Run step0_scan.py
            step0_py = str(ROOT / "scripts" / "step0_scan.py")
            out_json = os.path.join(d, "step0.json")
            r_scan = subprocess.run(
                [sys.executable, "-I", step0_py, proj_a, "--home", home_dir, "--out-json", out_json],
                capture_output=True, text=True, check=True,
            )
            prof = json.loads(r_scan.stdout)
            self.assertGreaterEqual(len(prof["discovered_projects"]), 2)
            self.assertEqual(len(prof["palettes"]), 3)
            self.assertEqual(prof["palettes"][0]["id"], "project-inferred")
            self.assertEqual(prof["palettes"][0]["light"]["ember"], "#ff3366")
            self.assertEqual(prof["autonomy_assessment"]["recommended_level"], "full_auto")

            # 2. Run twrp_wizard.py --headless (via install.py --tui shortcut or direct)
            twrp_py = str(ROOT / "scripts" / "twrp_wizard.py")
            env = dict(os.environ, KIT_BIN_DIR=os.path.join(d, "bin"))
            r_wiz = subprocess.run(
                [sys.executable, "-I", twrp_py, proj_a, "--step0-json", out_json, "--headless", "--runtime", "agy"],
                capture_output=True, text=True, env=env, check=True,
            )
            self.assertIn("TWRP-STYLE STEP 0 BOARD INSTALLER", r_wiz.stdout)
            with open(os.path.join(proj_a, ".kit", "config.json"), encoding="utf-8") as fh:
                cfg = json.load(fh)
            self.assertEqual(cfg["kit"]["autonomy"]["level"], "full_auto")
            self.assertTrue(cfg["kit"]["autonomy"]["auto_defaults"])
            self.assertTrue(cfg["kit"]["autonomy"]["carry_on"])
            self.assertEqual(cfg["kit"]["palette"]["id"], "project-inferred")

            with open(os.path.join(proj_a, ".kit", "board.html"), encoding="utf-8") as fh:
                bhtml = fh.read()
            self.assertIn("--ember: #ff3366", bhtml)

            with open(os.path.join(proj_a, ".kit", "orchestrator.md"), encoding="utf-8") as fh:
                orch_md = fh.read()
            self.assertIn("full_auto", orch_md)
            self.assertIn("default_option", orch_md)

    def test_auto_links_table_and_dashboard_trains_materialization(self):
        srv, store = board_server.start_server(self.root, "127.0.0.1", 0, preview=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()

        self.addCleanup(_stop)
        url = "http://127.0.0.1:%d" % srv.server_address[1]

        # 1. Create a first post on a brand-new train ("payments") with no .board/memory/state/payments.md file
        store.set_doc("posts", "PAY1-init", {
            "title": "Design Stripe webhook schema",
            "column": "done",
            "part": "PAY1",
            "train": "payments",
            "priority": 1,
            "created_at": "2026-11-20T14:00:00Z",
            "body": "Schema designed.",
        })

        # 2. Create a second post that only names `parent: "PAY1"` (no explicit `train` field!)
        store.set_doc("posts", "PAY2-verify", {
            "title": "Approve live Stripe keys",
            "column": "question",
            "part": "PAY2",
            "parent": "PAY1",
            "priority": 1,
            "created_at": "2026-11-20T14:05:00Z",
            "body": "Waiting on key approval.",
        })

        # Verify links table inherited `train: "payments"` for PAY2 and materialized _dashboard & _trains automatically
        r_lk = subprocess.run(
            [sys.executable, "-I", BOARD_CLI, "--url", url, "links", "--train", "payments"],
            capture_output=True, text=True, check=True,
        )
        lk_data = json.loads(r_lk.stdout)
        self.assertEqual(lk_data["total"], 2)
        self.assertEqual(lk_data["trains"]["payments"], ["PAY1", "PAY2"])

        dash = store.get_doc("posts", "_dashboard")
        self.assertTrue(dash["exists"])
        pay_rows = [r for r in dash["data"]["rows"] if r.get("train") == "P-payments"]
        self.assertEqual(len(pay_rows), 1)
        self.assertEqual(pay_rows[0]["state"], "you")
        self.assertEqual(pay_rows[0]["steps"], ["Design Stripe webhook schema", "Approve live Stripe keys"])
        self.assertEqual(pay_rows[0]["at"], 1)

        trains = store.get_doc("posts", "_trains")
        self.assertTrue(trains["exists"])
        by_part = {r["part"]: r for r in trains["data"]["rows"]}
        self.assertIn("P-payments", by_part)
        self.assertIn("PAY1", by_part["P-payments"]["children"])
        self.assertIn("PAY2", by_part["PAY1"]["children"])
        self.assertEqual(by_part["PAY2"]["state"], "you")


if __name__ == "__main__":
    unittest.main()
