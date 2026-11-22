"""Collapsible ## sections in post bodies and comments (board page renderBody), run in node without a browser."""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Sections(unittest.TestCase):
    def test_render_and_learn(self):
        out = subprocess.run(["node", os.path.join(ROOT, "tests", "sections_render.js"),
                              os.path.join(ROOT, "kit", "board", "board.template.html")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        # learned folded (numbers, score -2) beats nothing; audit name folds; {fold} folds when learning is weak (design +1)
        self.assertEqual(lines[0], "verdict:open | numbers:folded | what i checked:folded | design:folded | plan:folded")
        self.assertEqual(lines[1:], ["lead before first section: true", "balanced: true", "h1 outside sections: true",
                                     "learned verdict score: -2 writes: 2 posts/_prefs", "verdict now: folded",
                                     "viewer does not teach: true"])


if __name__ == "__main__":
    unittest.main()
