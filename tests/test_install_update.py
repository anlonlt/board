"""install.py --update: an existing install brought to this kit, the project's own changes kept.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import tempfile as _tf, atexit as _ax, shutil as _sh; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-"); _ax.register(_sh.rmtree, os.environ["XDG_CONFIG_HOME"], True)  # CL52: removed at exit  # CL11: install.py writes the machine token there, never in the real ~/.config

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, KIT_NO_FUNNEL="1")


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600)


class InstallUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "proj"
        self.t.mkdir()
        subprocess.run(["git", "init", "-q", str(self.t)], check=True)
        r = install(self.t, "--quick", "--project", "Proj", "--runtime", "agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_an_install(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        r = install(empty, "--update")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing installed here", r.stdout + r.stderr)

    def test_refreshes_keeps_and_adds(self):
        kit = self.t / ".kit" / "kit.py"
        guard = self.t / ".agents" / "hooks" / "kit" / "kit_guard.py"
        guard.write_text(guard.read_text() + "\n# the project's own rule\n")       # changed in the project
        kit.unlink()                                                             # gone: comes back
        cfg_p = self.t / ".kit" / "config.json"
        cfg = json.loads(cfg_p.read_text())
        cfg["kit"].update({"artifact_url": "https://claude.ai/artifact/ABC", "page_out": "docs/board/page.html",
                           "board_guide": "docs/BOARD.md"})
        cfg_p.write_text(json.dumps(cfg))
        r = install(self.t, "--update")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertTrue(kit.exists())
        self.assertIn("# the project's own rule", guard.read_text())
        self.assertIn("KEPT", out)
        self.assertIn("were KEPT", out)
        for f in ("orchestrator.md", "clerk.md", "part.md"):                    # CLI-agnostic: up opens claude too
            self.assertTrue((self.t / ".kit" / "claude-cli" / f).exists(), f)
        for f in ("kit_artifact_sync.py", "board_server.py", "board_cli.py", "up", "handover.py"):
            self.assertTrue((self.t / ".kit" / f).exists(), f)
        page = (self.t / "docs" / "board" / "page.html").read_text()
        self.assertIn('window.__KIT_BOARD_URL__="https://claude.ai/artifact/ABC"', page)
        self.assertIn("<code>docs/BOARD.md</code>", page)
        self.assertFalse((self.t / ".claude" / "settings.json").exists())        # agy runtime: no Claude hooks added
        r2 = install(self.t, "--update")                                         # a second run: no file written
        self.assertFalse([l for l in r2.stdout.splitlines() if l.startswith(("wrote ", "updated "))], r2.stdout)


if __name__ == "__main__":
    unittest.main()
