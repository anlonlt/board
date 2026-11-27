"""CL60 (the owner, 2026-11-27: "what is up with all the sessions not respecting the character and . hook? The review
and M12, their long posts: completely unacceptable."): a part's or a review's last chat message is a single "·" the first
time. A Stop hook runs after the message is on screen, so the rule has to reach the session before it writes:

- every last step (kit.py done, end_session.py, kit.py unreached, kit.py artifact sent, the review's comment write, the
  send to the orchestrator) returns, through PostToolUse, "Your next and last message is a single ·";
- the part's and the review's resume say it once, in "Where you report";
- a launch line marked "human" by Claude Code (a create_session prompt) is not the owner typing;
- replay: the fixture of M12's end (a 2,872-character first closing) counts 1, the same end closing with "·" counts 0,
  and the hook says the line after each of its last steps.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "kit", "hooks", "kit_say_it_once.py")
FIX = os.path.join(ROOT, "tests", "fixtures", "cl60")
sys.path.insert(0, os.path.join(ROOT, "kit", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)
import closing_audit  # noqa: E402
import kit_say_it_once as say  # noqa: E402

LAST = "Your next and last message is a single"
LAUNCH = ("Your prompt is docs/prompts/m12-x.md: run `python3 -I .kit/kit.py resume --role part --post m12-x`, which "
          "prints it, and do it.")


def run(payload, env=None, setup=False):
    e = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "KIT_CHAT_VERBOSE", "KIT_PROJECT_DIR")}
    e.update(env or {})
    with tempfile.TemporaryDirectory() as d:
        if setup:                                        # Harmonize: kit.setup is true
            os.makedirs(os.path.join(d, ".kit"))
            with open(os.path.join(d, ".kit", "config.json"), "w") as fh:
                json.dump({"kit": {"setup": True}}, fh)
        e["CLAUDE_PROJECT_DIR"] = d
        p = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), capture_output=True, text=True,
                           env=e, cwd=d, timeout=30)
    return p.stdout


def transcript(opener, origin):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write(json.dumps({"type": "user", "uuid": "u1", "origin": {"kind": origin},
                        "message": {"role": "user", "content": opener}}) + "\n")
    f.close()
    return f.name


def post(tool, tp, cmd=None):
    ti = {"command": cmd} if cmd is not None else {"session_id": "session_x", "message": "Done. Check post x."}
    return {"hook_event_name": "PostToolUse", "session_id": "s", "transcript_path": tp, "tool_name": tool, "tool_input": ti}


class LastSteps(unittest.TestCase):
    def setUp(self):
        self.part = transcript(LAUNCH, "human")             # Claude Code marks a create_session prompt "human"
        self.notice = transcript("[SYSTEM NOTIFICATION] Your prompt is x", "task-notification")
        self.owner = transcript("why is the board slow?", "human")

    def tearDown(self):
        for p in (self.part, self.notice, self.owner):
            os.unlink(p)

    def test_each_last_step_says_the_line(self):
        cmds = ["python3 -I .kit/kit.py done cl60-x", "python3 -I .kit/end_session.py",
                'python3 -I .kit/kit.py unreached cl60-x --why "no session"',
                "python3 -I .kit/kit.py artifact sent /x/batch-01.json",
                "python3 -I .kit/board_cli.py set comments CR-x --data '{}'"]
        for tp in (self.part, self.notice):
            for c in cmds:
                self.assertIn(LAST, run(post("Bash", tp, c)), c)
            for tool in ("SendMessage", "mcp__claude-code-remote__send_message"):
                self.assertIn(LAST, run(post(tool, tp)), tool)

    def test_setup_mode_keeps_the_line(self):
        # CL60 on Harmonize (kit.setup true): the line after a last step and the launch rule still hold
        self.assertIn(LAST, run(post("Bash", self.part, "python3 -I .kit/kit.py done x"), setup=True))
        self.assertIn(LAST, run(post("mcp__claude-code-remote__send_message", self.part), setup=True))
        self.assertIn("did not type this turn", run({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                                                     "prompt": LAUNCH}, setup=True))
        self.assertEqual(run(post("Bash", self.part, "ls"), setup=True).strip(), "")

    def test_the_review_role_too(self):
        self.assertIn(LAST, run(post("Bash", self.owner, "python3 -I .kit/board_cli.py set comments CR-x --data '{}'"),
                                {"KIT_ROLE": "review"}))

    def test_other_commands_say_nothing(self):
        for c in ("ls", "grep -n 'kit.py done' x", 'python3 - <<EOF\nx = ["python3 -I .kit/kit.py done m"]\nEOF',
                  "python3 -I .kit/kit.py doner"):
            self.assertEqual(run(post("Bash", self.part, c)).strip(), "", c)

    def test_the_owner_typing_gets_words(self):
        self.assertEqual(run(post("Bash", self.owner, "python3 -I .kit/kit.py done x")).strip(), "")

    def test_launch_line_is_not_the_owner(self):
        self.assertFalse(say.turn_opener(self.part)[1])
        out = run({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": LAUNCH})
        self.assertIn("did not type this turn", out)
        self.assertNotIn("the owner typed", out)

    def test_notice_after_reminder_blocks_is_not_the_owner(self):
        p = ("<tools-used-before-summary>x</tools-used-before-summary>\n[SYSTEM NOTIFICATION - NOT USER INPUT]\n"
             + "y" * 500 + "\n<task-notification>\n<task-id>a</task-id>")
        self.assertIn("did not type this turn", run({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": p}))


class UpdateWires(unittest.TestCase):
    def test_update_widens_an_old_say_it_once_line(self):
        # CL60 on Harmonize: a non-unified install (runtime agy, Claude Code present) whose say-it-once PostToolUse was
        # on its old tools; --update must widen it to the last steps (Bash) and both send tools
        import install
        env = dict(os.environ, KIT_TEST="1")
        env.pop("KIT_PROJECT_DIR", None)

        def inst(t, *args):
            return subprocess.run([sys.executable, "-I", os.path.join(ROOT, "install.py"), t, "--no-cmd", *args],
                                  capture_output=True, text=True, env=env, timeout=600, stdin=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "proj")
            os.makedirs(t)
            subprocess.run(["git", "init", "-q", t], check=True)
            r = inst(t, "--quick", "--project", "Proj", "--runtime", "agy", "--no-survey", "test")
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            hk = os.path.join(t, ".claude", "hooks", "kit")
            os.makedirs(hk, exist_ok=True)
            open(os.path.join(hk, "kit_say_it_once.py"), "w").close()
            cmd = install.CLAUDE_CMD % "kit_say_it_once.py"
            with open(os.path.join(t, ".claude", "settings.json"), "w") as fh:
                json.dump({"hooks": {"PostToolUse": [{"matcher": "SendMessage",
                                                      "hooks": [{"type": "command", "command": cmd}]}]}}, fh)
            r = inst(t, "--update")
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            got = json.load(open(os.path.join(t, ".claude", "settings.json")))["hooks"]["PostToolUse"]
            m = [g["matcher"] for g in got if any(h["command"] == cmd for h in g["hooks"])]
            self.assertEqual(len(m), 1, got)
            self.assertTrue({"Bash", "SendMessage", "mcp__claude-code-remote__send_message"} <= set(m[0].split("|")), m)


class Resume(unittest.TestCase):
    def test_report_section_says_it_once(self):
        import kit
        for review in (False, True):
            p = {"owner": {"session_id": "session_abc", "device": "web"}}
            lines = kit.report_lines("cl60-x", p, {}, review=review)
            text = "\n".join(lines)
            self.assertEqual(text.count("next and last chat message is a single"), 1, text)
        self.assertIn("next and last chat message", "\n".join(kit.report_lines("cl60-x", {}, {})))


class Replay(unittest.TestCase):
    def test_before_and_after(self):
        before = closing_audit.audit(os.path.join(FIX, "before.jsonl"))
        after = closing_audit.audit(os.path.join(FIX, "after.jsonl"))
        self.assertEqual((before["turns"], before["long_first"]), (1, 1))
        self.assertGreater(before["lengths"][0], 2000)
        self.assertEqual((after["turns"], after["long_first"]), (1, 0))
        self.assertIsNone(say.stop_verdict("·", False, {}))     # the "·" closing passes the Stop hook first time

    def test_hook_says_it_after_the_fixture_last_steps(self):
        fx = os.path.join(FIX, "after.jsonl")
        said = []
        for line in open(fx, encoding="utf-8"):
            e = json.loads(line)
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    said.append(LAST in run({"hook_event_name": "PostToolUse", "session_id": "s", "transcript_path": fx,
                                             "tool_name": c["name"], "tool_input": c["input"]}))
        self.assertEqual(said, [True, True, True])


if __name__ == "__main__":
    unittest.main()
