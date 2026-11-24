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
import unittest.mock
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


HOOK = ROOT / "kit" / "hooks" / "kit_say_it_once.py"


def say(msg, tool="mcp__claude-code-remote__send_message"):
    data = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": tool, "tool_input": {"message": msg}}
    return subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(data), capture_output=True, text=True,
                          env=dict(ENV, KIT_ROLE="part"), timeout=60)


class MessagesArePointers(unittest.TestCase):
    """CL38 (the owner, 02:14): messages between sessions are pointers, said once on the board."""

    def test_pointer_passes(self):
        self.assertEqual(say("Done. Check post cl38-attaching-repos-on-the-web-install-py-al.").returncode, 0)
        self.assertEqual(say("Gaps under your post.", tool="SendMessage").returncode, 0)

    def test_over_200_refused_whatever_the_config(self):
        r = say("x" * 201)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("limit 200", r.stderr)

    def test_table_quote_code_refused(self):
        for msg, what in (("| a | b |\n|---|---|", "a table"), ("see:\n> his words", "a quote"), ("```x```", "a code block")):
            r = say(msg)
            self.assertEqual(r.returncode, 2, msg)
            self.assertIn(what, r.stderr)

    def test_owner_forward_never_cut(self):
        self.assertEqual(say("Board comment from the owner on post x: " + "y" * 900).returncode, 0)

    def test_cli_install_hooks_both_send_tools(self):
        sys.path.insert(0, str(ROOT))
        import importlib
        inst = importlib.import_module("install")
        pre = [m for ev, m, f in inst.CLAUDE_CLI_SETTINGS if ev == "PreToolUse" and f == "kit_say_it_once.py"]
        self.assertIn("mcp__claude-code-remote__send_message", pre[0].split("|"))
        self.assertIn("SendMessage", pre[0].split("|"))


class Branches(unittest.TestCase):
    """CL38: the prompt goes on the trunk, Done needs the trunk merged, finished branches are swept."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        b = Path(self.tmp.name)
        self.remote, self.t = b / "remote.git", b / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.t)], check=True, capture_output=True)
        self.git("checkout", "-q", "-B", "main")
        (self.t / ".kit").mkdir()
        (self.t / ".kit" / "config.json").write_text(json.dumps({"kit": {"main_branch": "main", "trunk_push": True}}))
        self.commit("a")
        self.git("push", "-q", "origin", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.t), "-c", "user.email=t@t", "-c", "user.name=t"] + list(a),
                              capture_output=True, text=True)

    def commit(self, name):
        (self.t / name).write_text(name)
        self.git("add", name)
        self.git("commit", "-q", "-m", name)

    def test_done_refuses_until_the_trunk_is_merged(self):
        self.git("checkout", "-q", "-b", "cl99")
        self.commit("mine")
        self.assertEqual(kit.check_trunk_merged(str(self.t)), [])
        self.git("checkout", "-q", "main")
        self.commit("newer-rule")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "cl99")
        err = kit.check_trunk_merged(str(self.t))
        self.assertEqual(len(err), 1)
        self.assertIn("merge it first", err[0])
        self.git("merge", "-q", "--no-edit", "origin/main")
        self.assertEqual(kit.check_trunk_merged(str(self.t)), [])

    def test_prompt_goes_on_the_trunk(self):
        g = lambda *a, **kw: self.git(*a)  # noqa: E731
        self.assertEqual(kit._prompt_branch(str(self.t), "cl99-x", g), "main")
        (self.t / ".kit" / "config.json").write_text(json.dumps({"kit": {"trunk_push": False}}))
        self.assertEqual(kit._prompt_branch(str(self.t), "cl99-x", g), "part-cl99-x")

    def test_branches_lists_and_deletes_only_merged(self):
        self.git("checkout", "-q", "-b", "cl97")
        self.commit("merged-work")
        self.git("push", "-q", "origin", "cl97")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "cl97")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "cl98")
        self.commit("open-work")
        self.git("push", "-q", "origin", "cl98")
        self.git("checkout", "-q", "main")
        with unittest.mock.patch.object(kit, "_all_posts", return_value={}):
            out, rc = kit.merged_branches(str(self.t))
            self.assertEqual(rc, 0, out)
            self.assertIn("fully on main: cl97", out)
            self.assertIn("kept (work not on main): cl98", out)
            out, rc = kit.merged_branches(str(self.t), delete=True)
        self.assertEqual(rc, 0, out)
        heads = subprocess.run(["git", "-C", str(self.remote), "branch"], capture_output=True, text=True).stdout
        self.assertNotIn("cl97", heads)
        self.assertIn("cl98", heads)
        self.assertIn("main", heads)


class O55Gaps(unittest.TestCase):
    """O55's check of 99f6ccf: session_id, Harmonize's 600, a trunk that cannot be fetched."""

    def test_whoami_names_the_web_session(self):
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_01ABC"}):
            self.assertEqual(kit.whoami("harmonize-cl38"), "session_01ABC")
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_REMOTE_SESSION_ID", "KIT_SESSION_ID")}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(kit.whoami("harmonize-cl38"), "harmonize-cl38")

    def test_trunk_that_cannot_be_fetched_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "-b", "cl99", d], check=True)
            subprocess.run(["git", "-C", d, "remote", "add", "origin", os.path.join(d, "no-such-remote.git")], check=True)
            err = kit.check_trunk_merged(d)
            self.assertEqual(len(err), 1)
            self.assertIn("cannot fetch", err[0])

    def test_own_say_it_once_project_gets_the_message_guard(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "proj"
            t.mkdir()
            subprocess.run(["git", "init", "-q", str(t)], check=True)
            r = install(t, "--quick", "--project", "Proj", "--runtime", "agy", "--no-survey", "test")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            hk = t / ".claude" / "hooks" / "kit"
            hk.mkdir(parents=True)
            (hk / "kit_config.py").write_text((ROOT / "kit" / "hooks" / "kit_config.py").read_text())
            own = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/say_it_once.py"'
            (t / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
                "PreToolUse": [{"matcher": "mcp__claude-code-remote__send_message", "hooks": [{"type": "command", "command": own}]}],
                "Stop": [{"hooks": [{"type": "command", "command": own}]}]}}))
            cp = t / ".kit" / "config.json"
            c = json.loads(cp.read_text())
            c["kit"]["limits"] = {"message_chars": 600, "done_message_chars": 240, "chat_answer_chars": 600}
            cp.write_text(json.dumps(c))
            r = install(t, "--update")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((hk / "kit_say_it_once.py").is_file())
            s = settings(t)
            kit_evs = [(ev, g.get("matcher")) for ev, gs in s["hooks"].items() for g in gs
                       for h in g["hooks"] if "kit/kit_say_it_once.py" in h["command"]]
            self.assertEqual(kit_evs, [("PreToolUse", "mcp__claude-code-remote__send_message|SendMessage")])
            self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], own)     # its own hook kept
            lim = json.loads(cp.read_text())["kit"]["limits"]
            self.assertEqual((lim["message_chars"], lim["done_message_chars"]), (200, 200))


if __name__ == "__main__":
    unittest.main()
