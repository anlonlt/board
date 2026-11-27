"""Unit and integration tests for universal + runtime-specific welcome messages.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
import tempfile as _tf, atexit as _ax, shutil as _sh; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-"); _ax.register(_sh.rmtree, os.environ["XDG_CONFIG_HOME"], True)  # CL52: removed at exit  # CL11: install.py writes the machine token there, never in the real ~/.config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "kit" / "board") not in sys.path:
    sys.path.append(str(ROOT / "kit" / "board"))
import board_server  # noqa: E402
import kit  # noqa: E402

KIT_PY = str(ROOT / "scripts" / "kit.py")
INSTALL_PY = str(ROOT / "install.py")


def kit_is_full_auto():
    """This repository's own setting, which the render reads."""
    cfg = json.loads((Path(__file__).resolve().parent.parent / ".kit" / "config.json").read_text())
    return cfg["kit"]["autonomy"]["level"] == "full_auto"


class TestWelcomeRender(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mem = os.path.join(self._tmp.name, ".board", "memory")
        os.makedirs(os.path.join(self.mem, "state"), exist_ok=True)
        with open(os.path.join(self.mem, "state", "beta.md"), "w", encoding="utf-8") as fh:
            fh.write("Next: Polish onboarding flow\nPriority: 2\n\n# Beta Onboarding\n")
        with open(os.path.join(self.mem, "state", "alpha.md"), "w", encoding="utf-8") as fh:
            fh.write("Next: Fix critical crash\nPriority: 1\n\n# Alpha Stability\n")
        with open(os.path.join(self.mem, "state", "gamma.md"), "w", encoding="utf-8") as fh:
            fh.write("Next: Publish release notes\nPriority: 3\n\n# Gamma Release\n")
        with open(os.path.join(self.mem, "awaits.md"), "w", encoding="utf-8") as fh:
            fh.write("# What awaits the owner\n\n- [gamma] Approve final release notes\n")

    def test_render_all_four_runtimes_universal_and_specific(self):
        expectations = {
            "agy": [
                "board_server.py",
                "Tailscale",
                "myproj",
                "kit_guard.py",
                "flash_lite",
                "kit.py receive",
            ],
            "claude": [
                "claude.ai",
                "window.storage",
                "Auto-wakeup",
                "board-mirror",
            ],
            "arena": [
                "--embed-mode",
                "KIT_EMBED_PREVIEW=1",
                "X-Kit-Token",
                "5s",
                "Pull-on-turn",
                "no background clerk",
                "no auto-wake",
                "ping the Arena chat",
                "patch/zip",
            ],
            "generic": [
                "board_server.py",
                "board_cli.py",
                "kit.py receive",
            ],
        }
        for rt, needles in expectations.items():
            with self.subTest(runtime=rt):
                text = kit.render_welcome(
                    runtime=rt,
                    project="DemoApp",
                    board_url="http://127.0.0.1:8765",
                    memory_dir=self.mem,
                    cmd="myproj",
                )
                # Universal sections
                self.assertIn("DemoApp", text)
                self.assertIn("`todo`", text)
                self.assertIn("`question`", text)
                self.assertIn("`info`", text)
                self.assertIn("`done`", text)
                self.assertIn("Where we are", text)
                self.assertIn("_orchestrator", text)
                self.assertIn("These start now; say which to hold." if kit_is_full_auto() else "A Like on this message launches them all; or tell me which.", text)
                # Memory items pulled in priority order (P1 Alpha before P2 Beta before P3 Gamma)
                self.assertIn("Alpha Stability", text)
                self.assertIn("Fix critical crash", text)
                self.assertIn("Beta Onboarding", text)
                self.assertIn("Polish onboarding flow", text)
                self.assertIn("Approve final release notes", text)
                self.assertLess(text.index("Alpha Stability"), text.index("Beta Onboarding"))
                self.assertLess(text.index("Beta Onboarding"), text.index("Gamma Release"))
                # gamma is in awaits.md so it must not appear in "What can run now"
                run_now = text.split("## 5. What can run now", 1)[1]
                self.assertIn("Alpha Stability", run_now)
                self.assertIn("Beta Onboarding", run_now)
                self.assertNotIn("Gamma Release", run_now)
                # Runtime-specific capabilities
                for needle in needles:
                    self.assertIn(needle, text)

    def test_unsupported_runtime_raises(self):
        with self.assertRaises(ValueError):
            kit.render_welcome(runtime="unknown-rt")


class TestWelcomePostIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, ".kit"), exist_ok=True)
        with open(os.path.join(self.root, ".kit", "board.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><html><body>board</body></html>")
        self.mem = os.path.join(self.root, ".board", "memory")
        os.makedirs(os.path.join(self.mem, "state"), exist_ok=True)
        with open(os.path.join(self.mem, "state", "core.md"), "w", encoding="utf-8") as fh:
            fh.write("Next: Ship v1\nPriority: 1\n\n# Core\n")
        self.token = "welcome-test-token"
        srv, store = board_server.start_server(self.root, "127.0.0.1", 0, token=self.token)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()

        self.addCleanup(_stop)
        self.url = "http://127.0.0.1:%d" % srv.server_address[1]
        self.store = store
        self.store.set_doc("posts", "_orchestrator", {
            "title": "Ask the orchestrator",
            "column": "info",
            "part": "orchestrator",
            "priority": 3,
            "created_at": "2026-11-20T00:00:00Z",
            "body": "Orchestrator thread.",
        })

    def test_post_idempotent_and_multi_runtime_coexist(self):
        # First agy post via CLI
        r1 = subprocess.run(
            [
                sys.executable, "-I", KIT_PY, "welcome",
                "--runtime", "agy", "--post",
                "--board-url", self.url, "--token", self.token,
                "--project", "SyncApp", "--memory-dir", self.mem,
            ],
            capture_output=True, text=True, check=True,
        )
        res1 = json.loads(r1.stdout)
        self.assertEqual(res1["id"], "welcome-agy-1")
        self.assertTrue(res1["posted"])

        # Rerun agy post is idempotent (no duplicate, version unchanged)
        r2 = subprocess.run(
            [
                sys.executable, "-I", KIT_PY, "welcome",
                "--runtime", "agy", "--post",
                "--board-url", self.url, "--token", self.token,
                "--project", "SyncApp", "--memory-dir", self.mem,
            ],
            capture_output=True, text=True, check=True,
        )
        res2 = json.loads(r2.stdout)
        self.assertEqual(res2["id"], "welcome-agy-1")
        self.assertFalse(res2["posted"])
        self.assertEqual(res2["status"], "already exists")

        # Switching to arena posts welcome-arena-1 alongside welcome-agy-1
        r3 = subprocess.run(
            [
                sys.executable, "-I", KIT_PY, "welcome",
                "--runtime", "arena", "--post",
                "--board-url", self.url, "--token", self.token,
                "--project", "SyncApp", "--memory-dir", self.mem,
            ],
            capture_output=True, text=True, check=True,
        )
        res3 = json.loads(r3.stdout)
        self.assertEqual(res3["id"], "welcome-arena-1")
        self.assertTrue(res3["posted"])

        # Both comments exist under _orchestrator with distinct runtime metadata
        agy_doc = self.store.get_doc("comments", "welcome-agy-1")
        arena_doc = self.store.get_doc("comments", "welcome-arena-1")
        self.assertTrue(agy_doc["exists"])
        self.assertTrue(arena_doc["exists"])
        self.assertEqual(agy_doc["version"], 1)
        self.assertEqual(arena_doc["version"], 1)
        self.assertEqual(agy_doc["data"]["post_id"], "_orchestrator")
        self.assertEqual(arena_doc["data"]["post_id"], "_orchestrator")
        self.assertEqual(agy_doc["data"]["runtime"], "agy")
        self.assertEqual(arena_doc["data"]["runtime"], "arena")
        self.assertIn("--embed-mode", arena_doc["data"]["text"])
        self.assertIn("Ship v1", arena_doc["data"]["text"])


class TestInstallArenaAndGeneric(unittest.TestCase):
    def test_install_runtime_arena_and_generic(self):
        with tempfile.TemporaryDirectory() as d:
            for rt in ("arena", "generic"):
                with self.subTest(runtime=rt):
                    target = os.path.join(d, "proj_" + rt)
                    os.makedirs(target)
                    r = subprocess.run(
                        [
                            sys.executable, "-I", INSTALL_PY, target,
                            "--project", "Box-" + rt,
                            "--no-survey", "unit test",
                            "--runtime", rt, "--no-cmd",
                        ],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    with open(os.path.join(target, ".kit", "config.json"), encoding="utf-8") as fh:
                        cfg = json.load(fh)
                    self.assertEqual(cfg["kit"]["runtime"], rt)
                    self.assertTrue(os.path.isfile(os.path.join(target, ".kit", "kit.py")))
                    self.assertTrue(os.path.isfile(os.path.join(target, ".kit", "board_server.py")))
                    self.assertTrue(os.path.isfile(os.path.join(target, ".kit", "orchestrator.md")))
                    self.assertFalse(os.path.exists(os.path.join(target, ".agents")))
                    self.assertFalse(os.path.exists(os.path.join(target, ".claude")))
                    with open(os.path.join(target, ".kit", "orchestrator.md"), encoding="utf-8") as fh:
                        orch = fh.read()
                    self.assertNotIn("{{", orch)
                    if rt == "arena":
                        self.assertIn("--embed-mode", orch)
                        self.assertIn("Pull-on-turn", orch)
                        self.assertIn("kit.py welcome --runtime arena --post", orch)
                    # Installed kit.py welcome auto-detects runtime from .kit/config.json
                    rw = subprocess.run(
                        [sys.executable, "-I", os.path.join(target, ".kit", "kit.py"), "welcome"],
                        capture_output=True, text=True, cwd=target,
                    )
                    self.assertEqual(rw.returncode, 0, rw.stdout + rw.stderr)
                    self.assertIn("Box-" + rt, rw.stdout)
                    self.assertIn("`%s`" % rt, rw.stdout)


if __name__ == "__main__":
    unittest.main()
