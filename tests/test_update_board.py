"""kit/claude/update_board.py: an existing Artifact board updated to the kit's page, the project's repo untouched.

Run via: python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UpdateBoard(unittest.TestCase):
    def test_page_for_an_existing_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "docs", "board", "page.html")
            r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "claude" / "update_board.py"),
                                "--board-url", "https://claude.ai/artifact/TESTBOARD", "--project", "Tide Clock",
                                "--guide", "docs/BOARD.md", "--out", out], capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            html = open(out, encoding="utf-8").read()
            self.assertIn("kit:artifact-adapter", html)                       # built for the Artifact
            self.assertIn('window.__KIT_BOARD_URL__="https://claude.ai/artifact/TESTBOARD"', html)
            self.assertIn("window.__KIT_WRAP_DB__", html)                     # cross-repo switcher
            self.assertIn("Tide Clock", html)
            self.assertIn("<code>docs/BOARD.md</code>", html)
            self.assertNotIn("<code>.kit/BOARD.md</code>", html)
            self.assertEqual(sorted(os.listdir(tmp)), ["docs"])               # nothing else written
            self.assertIn("next: Artifact publish", r.stdout)


if __name__ == "__main__":
    unittest.main()
