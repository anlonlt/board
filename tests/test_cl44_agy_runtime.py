#!/usr/bin/env python3
"""CL44: Fix six agy runtime gaps surfaced during live CL43 run.

1. scripts/kit.py (stale_copy): do not block local agy/CLI sessions when kit.artifact.url is set alongside kit.mode: "local".
2. scripts/kit.py (_load_board_docs) & kit/board/board_server.py: fall back to board-mirror if board.db has 0 docs,
   and do not leave a 0-doc board.db behind when board_server.py fails to bind its port.
3. install.py (merge_agy_mcp), kit/board/mcp_stdio.py, scripts/kit.py: worktree-safe .agents/mcp_config.json,
   include runner-assigned planned/in-progress train steps in board_queue, and handle offline fallback cleanly in board_inbox.
4. scripts/usage.py & kit/hook.py: parse agy transcript.jsonl (PLANNER_RESPONSE + tool_calls + child subagents),
   support Gemini/agy models, and wire kit_usage.py into agy Stop in TABLE.
5. kit/up: handle agy --mode auto unavailability cleanly instead of silently falling back to interactive mode.

Run via: python3 -m unittest tests.test_cl44_agy_runtime -v
"""
import importlib.util
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Bug1StaleCopyLocalMode(unittest.TestCase):
    def test_local_mode_not_blocked_by_artifact_url(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit").mkdir()
            cfg = {
                "project": "Board",
                "board_url": "http://127.0.0.1:8765",
                "kit": {
                    "runtime": "local",
                    "mode": "local",
                    "artifact": {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"},
                },
            }
            cfg_path = p / ".kit" / "config.json"
            cfg_path.write_text(json.dumps(cfg))
            env = {k: v for k, v in os.environ.items()
                   if k not in ("CLAUDE_CODE_REMOTE", "KIT_ARTIFACT_URL", "KIT_STALE_OK", "KIT_ARTIFACT_DIR")}
            env["KIT_ARTIFACT_DIR"] = str(p / ".kit" / "run" / "artifact")
            # Local mode without CLAUDE_CODE_REMOTE passes the gate (not exit 3 STALE)
            r = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "--config", str(cfg_path), "resume"],
                capture_output=True, text=True, env=env, cwd=td, timeout=60,
            )
            self.assertNotEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertNotIn("STALE:", r.stdout + r.stderr)

            # Claude Code Web (CLAUDE_CODE_REMOTE=true) STILL enforces the Artifact STALE gate
            env_web = dict(env, CLAUDE_CODE_REMOTE="true")
            r_web = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "--config", str(cfg_path), "resume"],
                capture_output=True, text=True, env=env_web, cwd=td, timeout=60,
            )
            self.assertEqual(r_web.returncode, 3, r_web.stdout + r_web.stderr)
            self.assertIn("STALE:", r_web.stdout + r_web.stderr)


class Bug2EmptyBoardDbAndFailedBind(unittest.TestCase):
    def test_empty_board_db_falls_back_to_mirror(self):
        kit = _load("kit_cl44_b2", ROOT / "scripts" / "kit.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit" / "board_data").mkdir(parents=True)
            db = p / ".kit" / "board_data" / "board.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
            con.commit()
            con.close()
            mirror = p / "mirror"
            (mirror / "board" / "posts").mkdir(parents=True)
            (mirror / "board" / "posts" / "p1.json").write_text(json.dumps({
                "title": "From mirror", "column": "info", "train": "cli", "part": "CL44",
                "status": "in progress", "runner": "agy",
            }))
            old_root = kit.ROOT
            try:
                kit.ROOT = str(p)
                posts, comments = kit._load_board_docs(str(mirror))
                self.assertIn("p1", posts)
                self.assertEqual(posts["p1"]["title"], "From mirror")
                q = kit.queue_cards(str(mirror), runner="agy")
                self.assertIn("p1", q)
                self.assertIn("@agy", q)
            finally:
                kit.ROOT = old_root

    def test_failed_port_bind_does_not_leave_empty_board_db(self):
        bs = _load("board_server_cl44_b2", ROOT / "kit" / "board" / "board_server.py")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / ".kit").mkdir()
                with self.assertRaises(OSError):
                    bs.start_server(td, host="127.0.0.1", port=busy_port, preview=True)
                self.assertFalse((Path(td) / ".kit" / "board_data" / "board.db").exists(),
                                 "failed bind must not leave an empty board.db behind")
        finally:
            sock.close()


class Bug3WorktreeMcpQueueAndOfflineInbox(unittest.TestCase):
    def test_merge_agy_mcp_uses_worktree_safe_relative_paths(self):
        ins = _load("install_cl44_b3", ROOT / "install.py")
        with tempfile.TemporaryDirectory() as td:
            log = []
            ins.merge_agy_mcp(td, dry=False, log=log)
            cfg = json.loads((Path(td) / ".agents" / "mcp_config.json").read_text())
            args = cfg["mcpServers"]["board-kit"]["args"]
            self.assertEqual(args, ["-I", ".kit/mcp_stdio.py", "--root", "."])
            self.assertNotIn(td, " ".join(args))

    def test_queue_includes_open_train_steps_and_excludes_completed(self):
        kit = _load("kit_cl44_b3", ROOT / "scripts" / "kit.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit" / "board_data").mkdir(parents=True)
            db = p / ".kit" / "board_data" / "board.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
            rows = [
                ("cl43-done", {"title": "Done step", "column": "info", "train": "cli", "part": "CL43", "status": "complete", "runner": "agy"}),
                ("cl44-wip", {"title": "Active step", "column": "info", "train": "cli", "part": "CL44", "status": "in progress", "runner": "agy", "blocked_by": ["CL43"]}),
                ("cl45-plan", {"title": "Planned step", "column": "info", "train": "cli", "part": "CL45", "status": "planned", "runner": "agy", "blocked_by": ["CL44"]}),
            ]
            for did, doc in rows:
                con.execute("INSERT INTO docs VALUES ('posts', ?, 1, ?, '')", (did, json.dumps(doc)))
            con.commit()
            con.close()
            q = kit.queue_cards(str(p / "no-mirror"), runner="agy", db_path=str(db))
            self.assertNotIn("cl43-done", q)
            self.assertIn("cl44-wip", q)
            self.assertIn("cl45-plan", q)
            self.assertIn("[info/in progress]", q)
            self.assertIn("[info/planned]", q)
            # unblocked_only: CL43 (status=complete, column=info) unblocks CL44, while CL44 (in progress) blocks CL45
            q_unb = kit.queue_cards(str(p / "no-mirror"), runner="agy", db_path=str(db), unblocked_only=True)
            self.assertIn("cl44-wip", q_unb)
            self.assertNotIn("cl45-plan", q_unb)

    def test_mcp_board_inbox_offline_fallback(self):
        mcp = _load("mcp_cl44_b3", ROOT / "kit" / "board" / "mcp_stdio.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit" / "board_data").mkdir(parents=True)
            # Empty board.db -> should fall back to mirror worktree cleanly
            db = p / ".kit" / "board_data" / "board.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
            con.commit()
            con.close()
            mirror = p / "mirror"
            (mirror / "board" / "posts").mkdir(parents=True)
            (mirror / "board" / "comments").mkdir(parents=True)
            (mirror / "board" / "posts" / "cl44-step.json").write_text(json.dumps({
                "title": "Fix agy gaps", "column": "info", "status": "in progress", "runner": "agy", "priority": 2,
            }))
            (mirror / "board" / "posts" / "cl43-old.json").write_text(json.dumps({
                "title": "Done step", "column": "info", "status": "complete", "runner": "agy", "priority": 2,
            }))
            (mirror / "board" / "comments" / "c1.json").write_text(json.dumps({
                "post_id": "cl44-step", "from": "owner", "text": "Please check", "created_at": "2026-11-26T08:00:00Z",
            }))
            (p / ".kit" / "config.json").write_text(json.dumps({
                "board_url": "http://127.0.0.1:9",
                "mirror": {"path": str(mirror)},
            }))
            srv = mcp.MCPServer(str(p), board_url="http://127.0.0.1:9")
            ok, out = srv.call_tool("board_inbox", {"runner": "agy"})
            self.assertTrue(ok, out)
            data = json.loads(out)
            self.assertTrue(data["offline"])
            self.assertEqual(data["source"], "board-mirror")
            self.assertEqual(data["watcher_status"], "offline")
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["comments"][0]["id"], "c1")
            self.assertEqual(data["assigned_total"], 1)
            self.assertEqual(data["assigned_posts"][0]["id"], "cl44-step")


class Bug4AgyUsageParsingAndHookWiring(unittest.TestCase):
    def test_parse_agy_transcript_with_subagent_and_gemini_pricing(self):
        usage = _load("usage_cl44_b4", ROOT / "scripts" / "usage.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            child_dir = p / "child-uuid-1234" / ".system_generated" / "logs"
            child_dir.mkdir(parents=True)
            child_tp = child_dir / "transcript.jsonl"
            child_events = [
                {"step_index": 0, "type": "SYSTEM_MESSAGE", "created_at": "2026-11-26T08:01:00Z",
                 "content": "[Message] timestamp=2026-11-26T08:01:00Z sender=parent content=Your prompt is docs/prompts/cl44-fix.md: read it and do it."},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "created_at": "2026-11-26T08:01:05Z",
                 "model": "gemini-3.8-flash-high", "input_tokens": 1000, "cache_read_tokens": 2000, "output_tokens": 500,
                 "tool_calls": [
                     {"name": "run_command", "args": {"CommandLine": '"python3 -m unittest tests.test_x"'}},
                     {"name": "send_message", "args": {"Recipient": '"parent"', "Message": '"Done. Check post cl44-fix."'}},
                 ]},
            ]
            child_tp.write_text("".join(json.dumps(x) + "\n" for x in child_events))

            parent_dir = p / "parent-uuid-5678" / ".system_generated" / "logs"
            parent_dir.mkdir(parents=True)
            parent_tp = parent_dir / "transcript.jsonl"
            parent_events = [
                {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-11-26T08:00:00Z",
                 "content": "<USER_REQUEST>\nYou are the orchestrator: start now.\n</USER_REQUEST>"},
                {"step_index": 1, "type": "SYSTEM_MESSAGE", "created_at": "2026-11-26T08:00:01Z",
                 "content": "<SYSTEM_MESSAGE>SessionStart</SYSTEM_MESSAGE>"},
                {"step_index": 2, "type": "PLANNER_RESPONSE", "created_at": "2026-11-26T08:00:10Z",
                 "model": "gemini-4-neon-high", "input_tokens": 10000, "cache_read_tokens": 50000, "output_tokens": 1000,
                 "tool_calls": [
                     {"name": "run_command", "args": {"CommandLine": '"python3 -I .kit/board_cli.py list posts"'}},
                     {"name": "invoke_subagent", "args": {"Role": '"Part: CL44"', "TypeName": '"self"', "Model": '"inherit"'}},
                 ]},
                {"step_index": 3, "type": "GENERIC", "created_at": "2026-11-26T08:00:20Z",
                 "content": 'Created the following subagent:\n{\n  "conversationId": "child-uuid-1234",\n  "logAbsoluteUri": "file://' + str(child_tp) + '"\n}\n'},
            ]
            parent_tp.write_text("".join(json.dumps(x) + "\n" for x in parent_events))

            row = usage.parse_transcript(str(parent_tp))
            self.assertEqual(row["session_id"], "parent-uuid-5678")
            self.assertEqual(row["entrypoint"], "agy")
            self.assertEqual(row["model"], "gemini-4-neon-high")
            self.assertEqual(row["api_calls"], 1)
            self.assertEqual(row["tokens"]["input"], 10000)
            self.assertEqual(row["tokens"]["cache_read"], 50000)
            self.assertEqual(row["tokens"]["output"], 1000)
            self.assertEqual(row["context_peak"], 60000)
            self.assertIsNotNone(row["cost_usd"])
            self.assertGreater(row["cost_usd"], 0)
            self.assertEqual(row["owner_turns"], 1)
            self.assertEqual(row["notices"], 1)
            self.assertEqual(row["tools"], {"run_command": 1, "invoke_subagent": 1})
            self.assertEqual(row["agents_spawned"], 1)
            self.assertEqual(row["kit_cmds"], {"board_cli list": 1})
            self.assertEqual(row["board"]["cli"], 1)
            self.assertEqual(len(row["subagents"]), 1)
            sub = row["subagents"][0]
            self.assertEqual(sub["agent"], "child-uuid-1234")
            self.assertEqual(sub["type"], "self")
            self.assertEqual(sub["description"], "Part: CL44")
            self.assertEqual(sub["model"], "gemini-3.8-flash-high")
            self.assertEqual(sub["tokens"]["output"], 500)
            self.assertIsNotNone(sub["cost_usd"])
            self.assertEqual(row["tokens_all"]["output"], 1500)
            self.assertAlmostEqual(row["cost_usd_all"], round(row["cost_usd"] + sub["cost_usd"], 6), places=6)

            # Child subagent classifies as part CL44 via [Message] content
            child_row = usage.parse_transcript(str(child_tp))
            self.assertEqual(child_row["received"], 1)
            self.assertEqual(child_row["sent"], 1)
            self.assertEqual(child_row["tests_run"], 1)
            self.assertEqual(usage.classify(child_row), ("part", "CL44"))

    def test_agy_clerk_role_and_gemini_35_flash_lite_pricing(self):
        from unittest import mock
        usage = _load("usage_cl44_b4_clerk", ROOT / "scripts" / "usage.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit").mkdir()
            (p / ".kit" / "config.json").write_text(json.dumps({"project": "P"}))
            tp_dir = p / "clerk-uuid-9999" / ".system_generated" / "logs"
            tp_dir.mkdir(parents=True)
            tp = tp_dir / "transcript.jsonl"
            events = [
                {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-11-27T02:00:00Z",
                 "content": "<USER_REQUEST>Your prompt is .kit/clerk.md: read it and follow it.</USER_REQUEST>"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "created_at": "2026-11-27T02:00:05Z",
                 "model": "gemini-3.5-flash-lite", "input_tokens": 100000, "cache_read_tokens": 200000, "output_tokens": 5000,
                 "tool_calls": []},
            ]
            tp.write_text("".join(json.dumps(x) + "\n" for x in events))
            with mock.patch.dict(os.environ, {"KIT_ROLE": "orchestrator"}, clear=False):
                row = usage.record(str(p), str(tp))
            self.assertEqual(row["role"], "clerk")
            self.assertIsNotNone(row["cost_usd"])
            self.assertGreater(row["cost_usd"], 0)

    def test_hook_table_wires_kit_usage_for_agy_stop(self):
        hk = _load("hook_cl44_b4", ROOT / "kit" / "hook.py")
        agy_stop_hooks = [r[2] for r in hk.rows("agy", "Stop")]
        self.assertIn("kit_usage.py", agy_stop_hooks)


class Bug5KitUpAgyModeHandling(unittest.TestCase):
    def _run_up(self, td, fake_agy_script, env_extra=None):
        p = Path(td)
        (p / ".kit").mkdir(exist_ok=True)
        (p / ".kit" / "config.json").write_text(json.dumps({
            "project": "TestUp",
            "kit": {"runtime": "agy", "cli": "agy", "parts": {"board": False, "clerk": False, "orchestrator": False}},
        }))
        up = p / ".kit" / "up"
        up.write_text((ROOT / "kit" / "up").read_text())
        up.chmod(0o755)
        bin_dir = p / "bin"
        bin_dir.mkdir(exist_ok=True)
        agy_bin = bin_dir / "agy"
        agy_bin.write_text(fake_agy_script)
        agy_bin.chmod(0o755)
        log_file = p / "agy-invoked.log"
        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["AGY_LOG"] = str(log_file)
        env["KIT_AGY_MODE_CACHE"] = str(p / "cache" / "agy-mode-auto")
        env.pop("KIT_AGY_MODE", None)
        env.pop("KIT_AGY_AUTO_PROBE", None)
        env.update(env_extra or {})
        r = subprocess.run([str(up), "--agy"], capture_output=True, text=True, env=env, cwd=td, timeout=30)
        invoked = log_file.read_text().splitlines() if log_file.exists() else []
        return r, invoked

    def test_unavailable_mode_auto_uses_accept_edits_with_explicit_notice(self):
        fake = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"--help\" ]; then\n"
            "  echo '  --mode  Set mode (accept-edits, auto, plan)'\n"
            "  echo '  --print-timeout  Timeout'\n"
            "  exit 0\n"
            "fi\n"
            "if printf '%s\\n' \"$*\" | grep -q -- '--print-timeout'; then\n"
            "  echo 'warning: --mode auto is not available for this account; continuing in the default mode.' >&2\n"
            "  exit 0\n"
            "fi\n"
            "echo \"$*\" >> \"$AGY_LOG\"\n"
        )
        with tempfile.TemporaryDirectory() as td:
            r, invoked = self._run_up(td, fake)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("agy --mode auto is unavailable", r.stderr)
            self.assertIn("--mode accept-edits", r.stderr)
            self.assertEqual(len(invoked), 1)
            self.assertIn("--mode accept-edits", invoked[0])
            self.assertNotIn("--mode auto", invoked[0])

    def test_unavailable_mode_auto_without_fallback_exits_nonzero(self):
        fake = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"--help\" ]; then\n"
            "  echo 'Usage: agy [prompt]'\n"
            "  exit 0\n"
            "fi\n"
            "echo \"$*\" >> \"$AGY_LOG\"\n"
        )
        with tempfile.TemporaryDirectory() as td:
            r, invoked = self._run_up(td, fake)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ERROR: agy --mode auto is unavailable", r.stderr)
            self.assertEqual(invoked, [], "must not silently fall back to interactive mode")

    def test_available_mode_auto_launches_with_mode_auto(self):
        fake = (
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"--help\" ]; then\n"
            "  echo '  --mode  Set mode (accept-edits, auto, plan)'\n"
            "  echo '  --print-timeout  Timeout'\n"
            "  exit 0\n"
            "fi\n"
            "if printf '%s\\n' \"$*\" | grep -q -- '--print-timeout'; then\n"
            "  echo 'timed out' >&2\n"
            "  exit 124\n"
            "fi\n"
            "echo \"$*\" >> \"$AGY_LOG\"\n"
        )
        with tempfile.TemporaryDirectory() as td:
            r, invoked = self._run_up(td, fake)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(len(invoked), 1)
            self.assertIn("--mode auto", invoked[0])


class Bug7MirrorLoss(unittest.TestCase):
    def test_local_only_mirror_post_survives_seed_and_reconcile(self):
        bs = _load("bs_cl44_b7", ROOT / "kit" / "board" / "board_server.py")
        kit = _load("kit_cl44_b7", ROOT / "scripts" / "kit.py")
        import threading
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            origin = p / "origin.git"
            repo = p / "repo"
            mirror = p / "mirror"
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            g = lambda cwd, *a: subprocess.run(
                ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t"] + list(a),
                check=True, capture_output=True, text=True,
            )
            (repo / ".kit").mkdir()
            (repo / ".kit" / "config.json").write_text(json.dumps({"project": "T", "mirror": {"branch": "board-mirror"}}))
            (repo / "README.md").write_text("hi\n")
            g(repo, "add", ".")
            g(repo, "commit", "-qm", "init")
            g(repo, "remote", "add", "origin", str(origin))
            g(repo, "push", "-q", "-u", "origin", "main")

            # Create board-mirror orphan branch + worktree with base post and push to origin
            empty_tree = g(repo, "hash-object", "-t", "tree", "/dev/null").stdout.strip()
            init_c = g(repo, "commit-tree", empty_tree, "-m", "init mirror").stdout.strip()
            g(repo, "branch", "board-mirror", init_c)
            g(repo, "worktree", "add", "-q", str(mirror), "board-mirror")
            (mirror / "board" / "posts").mkdir(parents=True)
            (mirror / "board" / "comments").mkdir(parents=True)
            (mirror / "board" / "posts" / "p-origin.json").write_text(json.dumps({
                "title": "On origin", "column": "info", "created_at": "2026-11-26T01:00:00Z",
            }))
            g(mirror, "add", "board")
            g(mirror, "commit", "-qm", "origin post")
            g(mirror, "push", "-q", "-u", "origin", "board-mirror")

            # Add a second post ONLY to the local board-mirror branch (unpushed!)
            (mirror / "board" / "posts" / "p-local-only.json").write_text(json.dumps({
                "title": "Local only post", "column": "info", "created_at": "2026-11-26T02:00:00Z",
            }))
            g(mirror, "add", "board")
            g(mirror, "commit", "-qm", "local only post")

            # Start fresh board server from empty board.db and seed_from_mirror
            srv, store = bs.start_server(str(repo), host="127.0.0.1", port=0, preview=True)
            try:
                info = bs.seed_from_mirror(str(repo), store, branch="board-mirror")
                self.assertGreaterEqual(info["imported"], 2)
                self.assertTrue(store.get_doc("posts", "p-local-only")["exists"])
                self.assertTrue(store.get_doc("posts", "p-origin")["exists"])

                port = srv.server_address[1]
                th = threading.Thread(target=srv.serve_forever, daemon=True)
                th.start()
                old_root = kit.ROOT
                try:
                    kit.ROOT = str(repo)
                    rec = kit.reconcile(str(mirror), url=f"http://127.0.0.1:{port}", branch="board-mirror", db_path=store.db_path)
                    self.assertEqual(rec["git"], "pushed")
                finally:
                    kit.ROOT = old_root
                # Both posts stay on the board AND in git (including origin/board-mirror)
                self.assertTrue(store.get_doc("posts", "p-local-only")["exists"])
                self.assertTrue((mirror / "board" / "posts" / "p-local-only.json").is_file())
                ls = subprocess.run(["git", "-C", str(origin), "ls-tree", "-r", "--name-only", "board-mirror"],
                                    capture_output=True, text=True, check=True).stdout
                self.assertIn("board/posts/p-local-only.json", ls)
            finally:
                srv.shutdown()
                srv.server_close()
                store.close()


class Bug8AgyUpdateStepsToBoard(unittest.TestCase):
    def test_update_steps_ticks_pieces_via_kit_sub(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit").mkdir()
            (p / ".kit" / "config.json").write_text(json.dumps({"project": "P"}))
            log_file = p / "sub-calls.log"
            # Fake .kit/kit.py that logs `sub` calls
            (p / ".kit" / "kit.py").write_text(
                "import json, sys\n"
                "open(%r, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "print(json.dumps({'post': 'cl44-x', 'done': 1, 'of': 2}))\n" % str(log_file)
            )
            # Fake .kit/board_cli.py returning 2 subtasks
            (p / ".kit" / "board_cli.py").write_text(
                "import json\n"
                "print(json.dumps({'exists': True, 'data': {'subtasks': ["
                "{'n': 1, 'title': 'Skip Artifact STALE gate in local mode', 'status': 'open'},"
                "{'n': 2, 'title': 'Fall back to board-mirror when board.db is empty', 'status': 'open'}"
                "]}}))\n"
            )
            payload = {
                "hookEventName": "PostToolUse",
                "conversationId": "conv-cl44-8",
                "cwd": str(p),
                "toolCall": {
                    "name": "update_steps",
                    "args": {
                        "steps": [
                            {"title": "1. Fix Artifact STALE gate", "status": "done"},
                            {"title": "Fall back to board-mirror empty db", "status": "in_progress"},
                        ]
                    },
                },
            }
            env = dict(os.environ, KIT_POST="cl44-x")
            r = subprocess.run(
                [sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_task_train.py")],
                input=json.dumps(payload), capture_output=True, text=True, env=env, cwd=td, timeout=30,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "", "agy PostToolUse must never emit injectSteps")
            calls = [json.loads(line) for line in log_file.read_text().splitlines()]
            self.assertEqual(len(calls), 2, calls)
            self.assertEqual(calls[0][:5], ["sub", "cl44-x", "1", "--status", "done"])
            self.assertEqual(calls[1][:5], ["sub", "cl44-x", "2", "--status", "doing"])

    def test_part_post_matches_backticked_or_quoted_prompt_path(self):
        from unittest import mock
        tt = _load("tt_cl44_b8_bt", ROOT / "kit" / "hooks" / "kit_task_train.py")
        for opener in (
            "Your prompt is `docs/prompts/r5-example.md`: read it and do it.",
            'Your prompt is "docs/prompts/r5-example.md": read it and do it.',
            "Your prompt is 'docs/prompts/r5-example.md': read it and do it.",
            "Your prompt is docs/prompts/r5-example.md: read it and do it.",
        ):
            st = {}
            with mock.patch.dict(os.environ, {"KIT_ROLE": "orchestrator"}, clear=False):
                os.environ.pop("KIT_POST", None)
                got = tt.part_post({"lastUserInput": opener}, st)
            self.assertEqual(got, "r5-example", opener)


class Bug9AgyPreToolUseSchemaAndActivity(unittest.TestCase):
    AGY_SCHEMAS = {
        "PreToolUse": {"decision", "reason"},
        "PostToolUse": {"decision", "reason"},
        "Stop": {"decision", "reason"},
        "PreInvocation": {"decision", "reason", "injectSteps"},
        "PostInvocation": {"decision", "reason", "injectSteps"},
        "SessionStart": {"decision", "reason", "injectSteps"},
    }

    def test_pre_tool_use_never_emits_inject_steps_and_delivers_on_post_invocation(self):
        hk = _load("hook_cl44_b9", ROOT / "kit" / "hook.py")
        # Direct merge_agy schema enforcement check
        for ev in ("PreToolUse", "PostToolUse", "Stop"):
            rc, out, _ = hk.merge_agy(ev, [("fake.py", False, 0,
                                            json.dumps({"decision": "allow", "injectSteps": [{"systemMessage": {"systemMessage": "hi"}}]}),
                                            "", "")])
            self.assertEqual(rc, 0)
            obj = json.loads(out)
            self.assertTrue(set(obj) <= self.AGY_SCHEMAS[ev], (ev, obj))
            self.assertNotIn("injectSteps", obj)

        # End-to-end kit_memory_jit via hook.py: PreToolUse returns allow without injectSteps; PostInvocation delivers it
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / ".kit" / "hooks").mkdir(parents=True)
            (p / ".kit" / "config.json").write_text(json.dumps({"project": "P"}))
            (p / ".kit" / "ledger.py").write_text((ROOT / "kit" / "ledger.py").read_text())
            (p / ".kit" / "hook.py").write_text((ROOT / "kit" / "hook.py").read_text())
            for f in (ROOT / "kit" / "hooks").glob("*.py"):
                (p / ".kit" / "hooks" / f.name).write_text(f.read_text())
            (p / ".kit" / "lessons.md").write_text(
                "## K99: Check board server\nroles: all\nfiles: kit/board/board_server.py\n\nNever leave empty db.\n"
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_"))}
            pre_payload = {
                "hookEventName": "PreToolUse",
                "conversationId": "conv-jit-9",
                "cwd": str(p),
                "workspacePaths": [str(p)],
                "toolCall": {"name": "edit_file", "args": {"Path": str(p / "kit" / "board" / "board_server.py")}},
            }
            r1 = subprocess.run([sys.executable, "-I", str(p / ".kit" / "hook.py"), "agy", "PreToolUse"],
                                input=json.dumps(pre_payload), capture_output=True, text=True, env=env, cwd=td, timeout=30)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            d1 = json.loads(r1.stdout)
            self.assertTrue(set(d1) <= self.AGY_SCHEMAS["PreToolUse"], d1)
            self.assertNotIn("injectSteps", d1)

            post_inv = {
                "hookEventName": "PostInvocation",
                "conversationId": "conv-jit-9",
                "cwd": str(p),
                "workspacePaths": [str(p)],
            }
            r2 = subprocess.run([sys.executable, "-I", str(p / ".kit" / "hook.py"), "agy", "PostInvocation"],
                                input=json.dumps(post_inv), capture_output=True, text=True, env=env, cwd=td, timeout=30)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            d2 = json.loads(r2.stdout)
            self.assertTrue(set(d2) <= self.AGY_SCHEMAS["PostInvocation"], d2)
            self.assertIn("K99", json.dumps(d2.get("injectSteps") or []))

    def test_board_cli_default_token_falls_back_to_machine_token_in_worktree(self):
        bcli = _load("bcli_cl44_b9", ROOT / "kit" / "board" / "board_cli.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            xdg = p / "xdg"
            (xdg / "board-kit").mkdir(parents=True)
            (xdg / "board-kit" / "board_token").write_text("machine-secret-tok\n")
            wt = p / "wt"
            wt.mkdir()
            old_root = bcli.ROOT
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            old_tok = os.environ.pop("KIT_BOARD_TOKEN", None)
            try:
                bcli.ROOT = str(wt)
                os.environ["XDG_CONFIG_HOME"] = str(xdg)
                self.assertEqual(bcli.default_token(), "machine-secret-tok")
            finally:
                bcli.ROOT = old_root
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg
                if old_tok is not None:
                    os.environ["KIT_BOARD_TOKEN"] = old_tok


class Bug10AutoCheckpoint(unittest.TestCase):
    def test_checkpoint_commits_on_step_branch_skips_secrets_and_refuses_trunk(self):
        kit = _load("kit_cl44_b10", ROOT / "scripts" / "kit.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            g = lambda *a: subprocess.run(
                ["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t"] + list(a),
                check=True, capture_output=True, text=True,
            )
            g("init", "-q", "-b", "main")
            (p / ".kit").mkdir()
            (p / ".kit" / "config.json").write_text(json.dumps({"kit": {"main_branch": "main", "guard": {"never_add": [".env"]}}}))
            (p / "a.txt").write_text("base\n")
            g("add", ".")
            g("commit", "-qm", "base")
            old_root = kit.ROOT
            try:
                kit.ROOT = str(p)
                (p / "a.txt").write_text("changed on main\n")
                r_main = kit.checkpoint(post="cl44-x", message="nope")
                self.assertFalse(r_main["ok"])
                self.assertFalse(r_main["committed"])

                g("checkout", "-qb", "cl44")
                (p / ".env").write_text("SECRET=1\n")
                r_ck = kit.checkpoint(post="cl44-x", message="piece 1 done")
                self.assertTrue(r_ck["ok"] and r_ck["committed"], r_ck)
                self.assertEqual(r_ck["branch"], "cl44")
                files = g("show", "--name-only", "--format=", "HEAD").stdout.splitlines()
                self.assertIn("a.txt", files)
                self.assertNotIn(".env", files)
            finally:
                kit.ROOT = old_root


class Bug11ResumeNamesWorkLocations(unittest.TestCase):
    def test_resume_names_branch_last_checkpoint_and_dirty_worktree(self):
        kit = _load("kit_cl44_b11", ROOT / "scripts" / "kit.py")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            repo = p / "repo"
            wt = p / "subagent-Part--CL44-self-old"
            g = lambda cwd, *a: subprocess.run(
                ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t"] + list(a),
                check=True, capture_output=True, text=True,
            )
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / ".kit").mkdir()
            (repo / ".kit" / "config.json").write_text(json.dumps({"kit": {"main_branch": "main"}}))
            (repo / "base.txt").write_text("base\n")
            g(repo, "add", ".")
            g(repo, "commit", "-qm", "base")

            # Step branch with a checkpoint commit
            g(repo, "checkout", "-qb", "cl44")
            (repo / "fix.py").write_text("x = 1\n")
            g(repo, "add", "fix.py")
            g(repo, "commit", "-qm", "checkpoint(CL44): piece 1 done")
            g(repo, "checkout", "-q", "main")

            # Separate agy worktree with uncommitted work
            g(repo, "worktree", "add", "-q", "-b", "subagent-Part--CL44-self-old", str(wt), "main")
            (wt / "wip.py").write_text("uncommitted\n")

            posts = {
                "cl44-test": {
                    "title": "Fix agy gaps", "train": "cli", "part": "CL44",
                    "column": "info", "status": "in progress", "session_id": "prev-sess",
                }
            }
            old_root = kit.ROOT
            try:
                kit.ROOT = str(repo)
                tl, _ = kit._train_lines(posts, alive=set())
                txt_tl = "\n".join(tl)
                self.assertIn("branch `cl44`", txt_tl)
                self.assertIn("checkpoint(CL44): piece 1 done", txt_tl)
                self.assertIn("subagent-Part--CL44-self-old", txt_tl)
                self.assertIn("wip.py", txt_tl)

                cl = "\n".join(kit.continues_lines(posts["cl44-test"], session="new-sess", pid="cl44-test"))
                self.assertIn("branch `cl44`", cl)
                self.assertIn("wip.py", cl)
            finally:
                kit.ROOT = old_root


class Bug12DeviceGateRecheck(unittest.TestCase):
    def _write_meminfo(self, path, total_mb, avail_mb):
        Path(path).write_text(
            "MemTotal:        %d kB\nMemAvailable:    %d kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
            % (total_mb * 1024, avail_mb * 1024)
        )

    def test_low_then_high_meminfo_passes_within_window(self):
        dev = _load("dev_cl44_b12", ROOT / "scripts" / "device.py")
        with tempfile.TemporaryDirectory() as td:
            mi = Path(td) / "meminfo"
            self._write_meminfo(mi, 4096, 574)
            s = dev.survey(env={}, procs=[(1, 450, "agy")], running=1, store={}, meminfo_path=str(mi))
            sleeps = []

            def fake_sleep(dt):
                sleeps.append(dt)
                if len(sleeps) >= 2:
                    self._write_meminfo(mi, 4096, 1960)

            ok, why = dev.gate(s, wait_s=30, interval_s=3, sleep=fake_sleep)
            self.assertTrue(ok, why)
            self.assertEqual(len(sleeps), 2)
            self.assertIn("1960", why)
            self.assertIn("574", why)

    def test_persistent_low_meminfo_refuses_after_window_with_readings(self):
        dev = _load("dev_cl44_b12b", ROOT / "scripts" / "device.py")
        with tempfile.TemporaryDirectory() as td:
            mi = Path(td) / "meminfo"
            self._write_meminfo(mi, 4096, 574)
            s = dev.survey(env={}, procs=[(1, 450, "agy")], running=1, store={}, meminfo_path=str(mi))
            sleeps = []
            ok, why = dev.gate(s, wait_s=9, interval_s=3, sleep=lambda dt: sleeps.append(dt))
            self.assertFalse(ok)
            self.assertEqual(sum(sleeps), 9)
            self.assertIn("waited 9s", why)
            self.assertIn("readings: 574, 574, 574, 574 MB", why)

    def test_cap_refusal_is_immediate_without_waiting(self):
        dev = _load("dev_cl44_b12c", ROOT / "scripts" / "device.py")
        with tempfile.TemporaryDirectory() as td:
            mi = Path(td) / "meminfo"
            self._write_meminfo(mi, 2779, 574)
            s = dev.survey(env={}, procs=[(1, 450, "agy")], running=3, store={}, meminfo_path=str(mi))
            sleeps = []
            ok, why = dev.gate(s, wait_s=30, interval_s=3, sleep=lambda dt: sleeps.append(dt))
            self.assertFalse(ok)
            self.assertEqual(sleeps, [], "session-cap refusal must never sleep")
            self.assertIn("holds 3", why)


if __name__ == "__main__":
    unittest.main()
