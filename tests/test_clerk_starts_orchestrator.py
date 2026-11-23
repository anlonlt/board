"""The web clerk starts a fresh orchestrator when the last one is gone or idle (tokens, dying containers).

Run via: python3 -m unittest discover -s tests -v
"""
import copy
import sys
import unittest
from pathlib import Path
import os
import tempfile as _tf; os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="kit-xdg-")  # CL11: install.py writes the machine token there, never in the real ~/.config

ROOT = Path(__file__).resolve().parent.parent
_path = list(sys.path)
sys.path.insert(0, str(ROOT))
import install  # noqa: E402
sys.path[:] = _path


def config(**kit):
    c = {"project": "Harmonize", "language": {"talk": "en"}, "board_url": "https://claude.ai/artifact/X",
         "mirror": {"branch": "board-mirror", "path": "../board-mirror"}, "fields": {},
         "kit": copy.deepcopy(install.DEFAULT_KIT)}
    c["kit"]["main_branch"] = "main"
    c["kit"].update(kit)
    return c


class ClerkStartsOrchestrator(unittest.TestCase):
    def render(self, c):
        return install.render((ROOT / "kit" / "claude" / "clerk.template.md").read_text(), c)

    def test_rule_with_repository(self):
        out = self.render(config(github="anlonlt/harmonize"))
        self.assertIn("more than 30 minutes ago", out)
        self.assertIn("`create_session`", out)
        self.assertIn('`source_url: "https://github.com/anlonlt/harmonize"`', out)
        self.assertIn('doc_id: "_orchestrator"', out)
        self.assertNotIn("{{", out)

    def test_idle_minutes_from_config_and_no_repository(self):
        c = config()
        c["kit"]["clerk"]["orchestrator_idle_minutes"] = 45
        out = self.render(c)
        self.assertIn("more than 45 minutes ago", out)
        self.assertIn("no `source_url`", out)


if __name__ == "__main__":
    unittest.main()
