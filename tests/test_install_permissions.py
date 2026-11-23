"""CL22: the install keeps the permissions the owner already gave.

A first install reads them from the repo's own instructions (trunk push: CLAUDE.md / AGENTS.md; permission mode:
.claude/settings.json defaultMode); a rerun or `--update` never turns off one already on in .kit/config.json; a repo
that grants nothing still starts with trunk push off.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, KIT_NO_FUNNEL="1")
# Harmonize's own line (its CLAUDE.md, 2026-11-09).
HARMONIZE_LINE = "- `main` is the trunk; pushing it is the owner's standing permission (2026-11-09).\n"


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600, stdin=subprocess.DEVNULL)


class Permissions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "proj"
        self.t.mkdir()
        subprocess.run(["git", "init", "-q", str(self.t)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def kit(self):
        return json.loads((self.t / ".kit" / "config.json").read_text())["kit"]

    def first(self, *extra):
        r = install(self.t, "--quick", "--project", "Proj", "--runtime", "agy", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def set_kit(self, **kv):
        p = self.t / ".kit" / "config.json"
        c = json.loads(p.read_text())
        c["kit"].update(kv)
        p.write_text(json.dumps(c, indent=1))

    # 1. found in the repo's own instructions
    def test_harmonize_line_in_claude_md_turns_trunk_push_on(self):
        (self.t / "CLAUDE.md").write_text("# Harmonize\n\n" + HARMONIZE_LINE)
        out = self.first()
        self.assertIs(self.kit()["trunk_push"], True)
        self.assertIn("CLAUDE.md:3", out)                     # says where it found it

    def test_agents_md_grant(self):
        (self.t / "AGENTS.md").write_text("Sessions may push directly to main once tests pass.\n")
        out = self.first()
        self.assertIs(self.kit()["trunk_push"], True)
        self.assertIn("AGENTS.md:1", out)

    def test_permission_mode_from_claude_settings(self):
        (self.t / ".claude").mkdir()
        (self.t / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
        out = self.first()
        self.assertEqual(self.kit()["claude_permission_mode"], "bypassPermissions")
        self.assertIn("settings.json", out)

    # 3. a stranger's repo: off, unless the flag
    def test_repo_with_none_stays_off(self):
        (self.t / "CLAUDE.md").write_text("# Proj\n\nPush your branch; the maintainer merges.\nNever push main.\n")
        out = self.first()
        self.assertIs(self.kit()["trunk_push"], False)
        self.assertEqual(self.kit()["claude_permission_mode"], "auto")
        self.assertIn("--trunk-push yes", out)

    def test_flag_still_turns_it_on(self):
        self.first("--trunk-push", "yes")
        self.assertIs(self.kit()["trunk_push"], True)

    # 2. a rerun or --update never turns one off
    def test_update_keeps_trunk_push(self):
        self.first()
        self.set_kit(trunk_push=True)
        r = install(self.t, "--update", "--trunk-push", "no")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIs(self.kit()["trunk_push"], True)
        self.assertIn("KEPT kit.trunk_push", r.stdout)

    def test_rerun_keeps_autonomy_booleans(self):
        self.first()
        self.set_kit(autonomy={"level": "full_auto", "auto_consultant": True, "auto_defaults": True,
                               "carry_on": True, "park_tangents": True})
        r = install(self.t, "--update", "--autonomy", "gated")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for key in ("auto_consultant", "auto_defaults", "carry_on", "park_tangents"):
            self.assertIs(self.kit()["autonomy"][key], True, key)

    def test_update_keeps_permission_mode_and_any_owner_boolean(self):
        self.first()
        self.set_kit(claude_permission_mode="bypassPermissions", owner_flag=True)
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.kit()["claude_permission_mode"], "bypassPermissions")
        self.assertIs(self.kit()["owner_flag"], True)

    def test_update_does_not_reread_the_repo_over_the_owner(self):
        self.first()                                           # nothing granted: off, and the owner keeps it off
        (self.t / "CLAUDE.md").write_text(HARMONIZE_LINE)
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIs(self.kit()["trunk_push"], False)


if __name__ == "__main__":
    unittest.main()
