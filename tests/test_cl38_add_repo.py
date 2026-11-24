"""CL38: attaching repos on Claude Code on the web.

install.py (first install and --update) merges mcp__claude-code-remote__add_repo into .claude/settings.json
permissions.allow, keeping the project's own allow/deny rules and never doubling it; the kit repo's own settings carry
it; `kit.py doctor` warns when a project with an attached board lacks it; `kit.py attach owner/repo` prints the
add_repo call when there is no clone yet, and records a clone next to the project in .kit/config.json portfolio.

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
RULE = "mcp__claude-code-remote__add_repo"
sys.path.insert(0, str(ROOT / "scripts"))
import kit  # noqa: E402


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600, stdin=subprocess.DEVNULL)


def settings(t):
    return json.loads((Path(t) / ".claude" / "settings.json").read_text())


class InstallAllowsAddRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "proj"
        self.t.mkdir()
        subprocess.run(["git", "init", "-q", str(self.t)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_install_then_update_keeps_own_rules(self):
        (self.t / ".claude").mkdir()
        (self.t / ".claude" / "settings.json").write_text(json.dumps(
            {"permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Bash(rm -rf:*)"]}}))
        r = install(self.t, "--quick", "--project", "Proj", "--runtime", "claude", "--no-survey", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = settings(self.t)
        self.assertIn(RULE, s["permissions"]["allow"])
        self.assertIn("Bash(npm test:*)", s["permissions"]["allow"])
        self.assertEqual(s["permissions"]["deny"], ["Bash(rm -rf:*)"])
        # --update: the rule once, the project's rules still there
        s["permissions"]["allow"].append("WebFetch(domain:example.com)")
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = settings(self.t)
        self.assertEqual(s["permissions"]["allow"].count(RULE), 1)
        self.assertIn("WebFetch(domain:example.com)", s["permissions"]["allow"])
        self.assertIn("Bash(npm test:*)", s["permissions"]["allow"])
        self.assertEqual(s["permissions"]["deny"], ["Bash(rm -rf:*)"])

    def test_update_adds_it_where_an_older_kit_did_not(self):
        r = install(self.t, "--quick", "--project", "Proj", "--runtime", "claude", "--no-survey", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = settings(self.t)
        s.pop("permissions")                                  # an install from before CL38
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(settings(self.t)["permissions"]["allow"], [RULE])
        self.assertEqual(set(settings(self.t)["hooks"]), set(s["hooks"]))   # the hooks untouched

    def test_kit_repo_itself_allows_it(self):
        self.assertIn(RULE, settings(ROOT)["permissions"]["allow"])


class DoctorAndAttach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.t = self.base / "proj"
        (self.t / ".kit").mkdir(parents=True)
        (self.t / ".claude").mkdir()
        (self.t / ".kit" / "config.json").write_text(json.dumps({"kit": {}, "portfolio": [{"name": "x", "github": "o/x"}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_doctor_check_warns_without_the_rule(self):
        (self.t / ".claude" / "settings.json").write_text(json.dumps({"hooks": {}}))
        self.assertEqual(kit.add_repo_allowed(str(self.t)), (True, False))
        (self.t / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": [RULE]}}))
        self.assertEqual(kit.add_repo_allowed(str(self.t)), (True, True))

    def test_no_attached_board_no_warning(self):
        (self.t / ".kit" / "config.json").write_text(json.dumps({"kit": {}}))
        (self.t / ".claude" / "settings.json").write_text("{}")
        self.assertEqual(kit.add_repo_allowed(str(self.t)), (False, False))

    def test_attach_without_clone_prints_the_add_repo_call(self):
        (self.t / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": [RULE]}}))
        out, rc = kit.attach_repo("anlonlt/harmonize", root=str(self.t), clone=False)
        self.assertEqual(rc, 1)
        self.assertIn('"owner": "anlonlt", "repo": "harmonize"', out)
        self.assertIn("mcp__claude-code-remote__add_repo", out)

    def test_attach_records_a_clone_next_to_the_project(self):
        (self.t / ".claude" / "settings.json").write_text("{}")
        dest = self.base / "harmonize"
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        out, rc = kit.attach_repo("anlonlt/harmonize", root=str(self.t), clone=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("WARNING", out)                         # this project lacks the rule
        self.assertIn("register_repo_root", out)
        port = json.loads((self.t / ".kit" / "config.json").read_text())["portfolio"]
        self.assertIn({"name": "harmonize", "path": str(dest), "github": "anlonlt/harmonize"}, port)
        kit.attach_repo("anlonlt/harmonize", root=str(self.t), clone=False)   # twice: recorded once
        port = json.loads((self.t / ".kit" / "config.json").read_text())["portfolio"]
        self.assertEqual(sum(1 for x in port if x.get("github") == "anlonlt/harmonize"), 1)


if __name__ == "__main__":
    unittest.main()
