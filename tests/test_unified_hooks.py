"""kit/hook.py, one settings line per event: what a runtime sees is what the separate hooks showed it, and a guard that
fails refuses the call. Plus install.py --unified-hooks / --no-unified-hooks, moved and back, on real installs.

Run via: python3 -m unittest tests.test_unified_hooks -v
"""
import datetime
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
import ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("kit_hook_under_test", str(ROOT / "kit" / "hook.py"))
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_", "CLAUDE", "AGY_"))}


def sh(cwd, *a):
    subprocess.run(list(a), cwd=str(cwd), check=True, capture_output=True)


class Project:
    """A small git project with a config, a files: rule and an uncommitted file; `.kit/hook.py` and `.kit/hooks`."""

    def __init__(self, base, name, hooks=None):
        self.root = Path(base) / name
        self.kit = self.root / ".kit"
        (self.kit / "hooks").mkdir(parents=True)
        (self.kit / "config.json").write_text(json.dumps({
            "project": "P", "board_url": "http://127.0.0.1:9", "kit": {
                "guard": {"never_add": ["*.keystore"]}, "limits": {"message_chars": 60, "done_message_chars": 40},
                "session_start_lines": ["BUILD: make test"]}}))
        (self.kit / "ledger.py").write_text((ROOT / "kit" / "ledger.py").read_text())
        (self.kit / "hook.py").write_text((ROOT / "kit" / "hook.py").read_text())
        for f in (ROOT / "kit" / "hooks").glob("*.py"):
            (self.kit / "hooks" / f.name).write_text(f.read_text())
        ledger.observe(str(self.root / ".board" / "memory"), "Check 411px", scope="files:app/*.html")
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
        self.tmp = Path(base) / (name + "-tmp")
        self.tmp.mkdir()

    def call(self, args, payload, role):
        env = dict(BASE_ENV, TMPDIR=str(self.tmp), CLAUDE_PROJECT_DIR=str(self.root), HOME=str(self.tmp), KIT_BOARD_URL="http://127.0.0.1:9")
        if role:
            env["KIT_ROLE"] = role
        if "toolCall" in payload or "workspacePaths" in payload:
            env.pop("CLAUDE_PROJECT_DIR")
        r = subprocess.run([sys.executable, "-I"] + args, input=json.dumps(payload).replace("@ROOT@", str(self.root)),
                           capture_output=True, text=True, env=env, cwd=str(self.root), timeout=120)
        return r.returncode, self.norm(r.stdout), self.norm(r.stderr)

    def norm(self, s):
        s = s.replace(str(self.root), "@ROOT@").replace(str(self.tmp), "@TMP@")
        # the pre-move path and the timing of a listing are not what the runtime acts on
        return re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:\d\d)", "@T@", s)

    def separate(self, runtime, event, payload, role):
        tool = hook._tool(payload, runtime)
        return [self.call([str(self.kit / "hooks" / f)], payload, role) for _, tools, f, _, _ in hook.rows(runtime, event)
                if tools is None or re.fullmatch(tools, tool or "")]

    def unified(self, runtime, event, payload, role):
        return self.call([str(self.kit / "hook.py"), runtime, event], payload, role)


def seen(event, runtime, results):
    """What the runtime acts on: is the call stopped, and the pieces of text it is shown or fed."""
    stop, texts = False, []
    for rc, out, err in results:
        if rc == 2:
            stop = True
            texts.append(err.strip())
            if runtime != "agy":
                continue                                   # Claude Code ignores stdout on exit 2
        try:
            d = json.loads(out) if out.strip() else {}
        except ValueError:
            d = {}
            if event in hook.TEXT_IS_CONTEXT:
                texts.append(out.strip())
        h = d.get("hookSpecificOutput") or {}
        if d.get("decision") in ("deny", "block", "continue") or h.get("permissionDecision") == "deny":
            stop = True
        texts += [d.get("reason") if d.get("decision") not in (None, "allow") else "", h.get("permissionDecisionReason"),
                  h.get("additionalContext"), d.get("systemMessage")]
        texts += [(s.get("systemMessage") or {}).get("systemMessage") for s in d.get("injectSteps") or []]
    return stop, [t.strip() for t in texts if t and t.strip()]


LONG = "Done. " + "every detail of the work " * 20


def claude(tool=None, inp=None, **k):
    d = {"session_id": "s1", "cwd": "@ROOT@", **k}
    if tool:
        d.update(tool_name=tool, tool_input=inp or {}, tool_response={})
    return d


def agy(tool=None, args=None, **k):
    d = {"conversationId": "c1", "workspacePaths": ["@ROOT@"], "cwd": "@ROOT@", **k}
    if tool:
        d["toolCall"] = {"name": tool, "args": args or {}}
    return d


CLAUDE_CASES = [(e, dict(p, hook_event_name=e)) for e, p in [
    ("SessionStart", claude(source="startup")),
    ("PreToolUse", claude("Bash", {"command": "ls"})),
    ("PreToolUse", claude("Bash", {"command": "rm -rf src"})),
    ("PreToolUse", claude("Bash", {"command": "git push --force"})),
    ("PreToolUse", claude("Bash", {"command": "python3 -I .kit/board_cli.py comments reply t1 --text hi"})),
    ("PreToolUse", claude("SendMessage", {"to": "x", "message": LONG})),
    ("PreToolUse", claude("SendMessage", {"to": "x", "message": "Done. Check post P1."})),
    ("PreToolUse", claude("mcp__claude-code-remote__send_message", {"session_id": "x", "message": LONG})),
    ("PreToolUse", claude("mcp__claude-code-remote__create_session", {"title": "t", "prompt": LONG})),
    ("PreToolUse", claude("ArtifactData", {"action": "update", "collection": "posts", "doc_id": "P1", "data": {"title": "t"}})),
    ("PreToolUse", claude("ArtifactData", {"action": "create", "collection": "comments", "data": {"text": "x", "reply_to": "c"}})),
    ("PreToolUse", claude("Edit", {"file_path": "@ROOT@/app/a.html"})),
    ("PreToolUse", claude("Write", {"file_path": "@ROOT@/b.txt"})),
    ("PreToolUse", claude("Read", {"file_path": "@ROOT@/b.txt"})),
    ("PostToolUse", claude("Bash", {"command": "python3 -I .kit/board_cli.py update posts P1 --data '{}'"})),
    ("PostToolUse", claude("Bash", {"command": "python3 -I .kit/kit.py resume"})),
    ("PostToolUse", claude("SendMessage", {"to": "x", "message": "hi"})),
    ("PostToolUse", claude("ArtifactData", {"action": "update", "collection": "posts", "doc_id": "P1", "data": {"status": "done", "priority": 1}})),
    ("PostToolUse", claude("mcp__claude-code-remote__create_session", {"title": "t", "prompt": "Your prompt is docs/prompts/x.md: read it."})),
    ("PostToolUse", claude("TaskCreate", {"subject": "x"})),
    ("UserPromptSubmit", claude(prompt="please do the thing")),
    ("Stop", claude(stop_hook_active=False, last_assistant_message=LONG)),
    ("Stop", claude(stop_hook_active=True, last_assistant_message="·")),
    ("SessionEnd", claude(reason="exit")),
    ("PreCompact", claude(trigger="auto")),
]]
AGY_CASES = [(e, dict(p, hookEventName=e)) for e, p in [
    ("SessionStart", agy()),
    ("PreToolUse", agy("run_command", {"CommandLine": "ls"})),
    ("PreToolUse", agy("run_command", {"CommandLine": "rm -rf src"})),
    ("PreToolUse", agy("invoke_subagent", {"Subagents": [{"Role": "board clerk"}]})),
    ("PreToolUse", agy("send_message", {"Recipient": "x", "Message": LONG})),
    ("PreToolUse", agy("send_message", {"Recipient": "x", "Message": "Done. Check post P1."})),
    ("PreToolUse", agy("edit_file", {"Path": "@ROOT@/app/a.html"})),
    ("PreToolUse", agy("read_file", {"Path": "@ROOT@/app/a.html"})),
    ("PostToolUse", agy("run_command", {"CommandLine": "ls"})),
    ("PostToolUse", agy("edit_file", {"Path": "@ROOT@/app/a.html"})),
    ("PostToolUse", agy("write_file", {"Path": "@ROOT@/b.txt"})),
    ("PostToolUse", agy("send_message", {"Recipient": "x", "Message": "hi"})),
    ("PreInvocation", agy(lastUserInput="please do the thing")),
    ("PostInvocation", agy()),
    ("Stop", agy(terminationReason="model_stop", fullyIdle=True)),
]]


class Parity(unittest.TestCase):
    def compare(self, runtimes, cases, dirty):
        def one(job):
            runtime, role, i = job
            event, payload = cases[i]
            a, b = (Project(t, "%s%s%s%d" % (x, runtime, role, i)) for x in "ab")       # each way its own project
            for p in (a, b):
                if dirty:
                    (p.root / "work.txt").write_text("x")
            return (runtime, role, event, json.dumps(payload)[:120]), event, runtime, \
                a.separate(runtime, event, payload, role), b.unified(runtime, event, payload, role)
        stops = shown = 0
        with tempfile.TemporaryDirectory() as t, ThreadPoolExecutor(os.cpu_count() or 2) as ex:
            for what, event, runtime, sep, uni in ex.map(one, itertools.product(
                    runtimes, ("", "orchestrator", "part", "clerk"), range(len(cases)))):
                (s1, t1), (s2, t2) = seen(event, runtime, sep), seen(event, runtime, [uni])
                self.assertEqual(s1, s2, (what, sep, uni))
                left = "\n".join(t2)
                for x in sorted(set(t1), key=len, reverse=True):
                    self.assertIn(x, left, what)                # nothing lost
                    left = left.replace(x, "")
                self.assertEqual(left.strip(), "", what)        # nothing added
                stops, shown = stops + s1, shown + bool(t1)
        self.assertGreater(stops, 5)             # the cases do reach refusals and contexts
        self.assertGreater(shown, stops)
        print("parity %s: %d cases, %d stopped, %d with text" % ("/".join(runtimes), 4 * len(runtimes) * len(cases), stops, shown))

    def test_claude_web_and_cli(self):
        self.compare(("web", "cli"), CLAUDE_CASES, True)

    def test_agy(self):
        self.compare(("agy",), AGY_CASES, True)

    def test_session_start_text_reaches_the_session(self):     # the first dispatcher lost it
        with tempfile.TemporaryDirectory() as t:
            rc, out, _ = Project(t, "p").unified("web", "SessionStart", dict(claude(), hook_event_name="SessionStart"), "")
            self.assertEqual(rc, 0)
            self.assertIn("BUILD: make test", out)

    def test_the_table_is_what_each_runtime_had(self):
        """The lines as they were written by hand before the table (53f23c4): no hook gained, lost or narrowed."""
        count = {rt: len(hook.rows(rt)) for rt in hook.RUNTIMES}
        self.assertEqual(count, {"web": 30, "cli": 26, "agy": 23})          # cli 26 (no cards reminder: server builds cards), web 30 (CL41), agy 23 (CL43/CL44: + PostInvocation loop & memory JIT + PostToolUse update_steps + Stop kit_usage)
        self.assertEqual([r[2] for r in hook.rows("cli", "Stop")].count("kit_web_save.py"), 1)
        self.assertEqual([r[2] for r in hook.rows("cli", "Stop")].count("kit_pre_compact.py"), 1)
        self.assertTrue(all(g == (ev in ("PreToolUse", "TaskCompleted")) or f in ("kit_memory_jit.py", "kit_activity.py") for ev, f, _, g, _ in hook.TABLE))  # they inform, never refuse
        files = {f.name for f in (ROOT / "kit" / "hooks").glob("kit_*.py")} - {"kit_config.py"}
        self.assertEqual({f for _, f, _, _, _ in hook.TABLE}, files)         # a hook installed is a hook wired


class Failing(unittest.TestCase):
    """Planted hooks: what the dispatcher makes of one that hangs, crashes, is missing, or disagrees with another."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.p = Project(t.name, "p")
        src = (self.p.kit / "hook.py").read_text()
        (self.p.kit / "hook.py").write_text(re.sub(r"(GUARD|PLAIN), \d+\)", r"\1, 2)", src))

    def plant(self, **bodies):
        for f in (self.p.kit / "hooks").glob("kit_*.py"):
            if f.name != "kit_config.py":
                f.write_text("")
        for name, body in bodies.items():
            (self.p.kit / "hooks" / ("kit_%s.py" % name)).write_text("import sys, json, time\n" + body)

    def go(self, event, payload, runtime="web"):
        return self.p.unified(runtime, event, dict(payload, **({"hook_event_name": event} if runtime != "agy" else {})), "")

    BASH = claude("Bash", {"command": "ls"})

    def test_a_guard_that_hangs_crashes_or_is_gone_refuses(self):
        for body, why in (("time.sleep(60)", "no answer in 2 s"), ("raise ValueError('boom')", "boom"), ("sys.exit(1)", "exit 1")):
            self.plant(guard=body)
            t0 = time.time()
            rc, out, err = self.go("PreToolUse", self.BASH)
            self.assertEqual((rc, out), (2, ""), body)
            self.assertIn("kit_guard.py did not decide", err)
            self.assertIn(why, err)
            self.assertLess(time.time() - t0, 15)
            rc, out, _ = self.go("PreToolUse", agy("run_command", {"CommandLine": "ls"}), "agy")
            self.assertEqual(json.loads(out)["decision"], "deny")
        (self.p.kit / "hooks" / "kit_guard.py").unlink()
        self.assertEqual(self.go("PreToolUse", self.BASH)[0], 0)              # a part that was never installed
        (self.p.kit / "manifest.json").write_text(json.dumps({".kit/hooks/kit_guard.py": "x"}))
        rc, _, err = self.go("PreToolUse", self.BASH)
        self.assertEqual(rc, 2)
        self.assertIn("file missing", err)

    def test_a_child_that_outlives_a_hung_guard_does_not_hold_the_call(self):
        pid = self.p.tmp / "pid"
        self.plant(guard="import subprocess\nc = subprocess.Popen(['sleep', '30'])\nopen(%r, 'w').write(str(c.pid))\ntime.sleep(60)" % str(pid))
        t0 = time.time()
        self.assertEqual(self.go("PreToolUse", self.BASH)[0], 2)
        self.assertLess(time.time() - t0, 15)
        time.sleep(0.3)
        state = Path("/proc/%s/stat" % pid.read_text())                       # gone, or a zombie nobody reaped yet
        if Path("/proc/self/stat").exists():
            self.assertTrue(not state.exists() or state.read_text().split(") ")[1][0] == "Z", "the child still runs")

    def test_another_hook_that_fails_blocks_nothing_and_is_said(self):
        self.plant(stop="raise ValueError('boom')", say_it_once="print(json.dumps({'decision': 'block', 'reason': 'one post'}))")
        rc, out, _ = self.go("Stop", claude())
        self.assertEqual((rc, json.loads(out)["decision"], json.loads(out)["reason"]), (0, "block", "one post"))
        self.assertIn("boom", json.loads(out)["systemMessage"])
        self.plant(stop="time.sleep(60)")
        rc, out, err = self.go("Stop", claude())
        self.assertEqual((rc, out), (1, ""))                                  # an error shown, never a block
        self.assertIn("no answer", err)
        self.plant(memory_jit="sys.exit(3)")
        self.assertEqual(self.go("PreToolUse", claude("Edit", {"file_path": "x"}))[0], 1)

    def test_two_hooks_that_speak_are_both_heard(self):
        ctx = "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': '%s'}}))"
        self.plant(brief_guard=ctx % "one", board_guard=ctx % "two")
        rc, out, _ = self.go("PostToolUse", self.BASH)
        self.assertEqual(json.loads(out)["hookSpecificOutput"], {"hookEventName": "PostToolUse", "additionalContext": "one\n\ntwo"})
        self.plant(guard="print('no A', file=sys.stderr); sys.exit(2)", brief_guard="print('no B', file=sys.stderr); sys.exit(2)")
        rc, out, err = self.go("PreToolUse", self.BASH)
        self.assertEqual((rc, out, err), (2, "", "no A\n\nno B\n"))
        self.plant(say_it_once="print('plain')", board_typing="print(json.dumps({'hookSpecificOutput': {'additionalContext': 'ctx'}}))")
        rc, out, _ = self.go("UserPromptSubmit", claude(prompt="x"))
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], "plain\n\nctx")

    def test_the_strictest_permission_wins(self):
        dec = "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': '%s', 'permissionDecisionReason': '%s'}}))"
        for a, b, top in (("allow", "ask", "ask"), ("ask", "deny", "deny"), ("deny", "allow", "deny")):
            self.plant(guard=dec % (a, "r-" + a), brief_guard=dec % (b, "r-" + b))
            h = json.loads(self.go("PreToolUse", self.BASH)[1])["hookSpecificOutput"]
            self.assertEqual((h["permissionDecision"], h["permissionDecisionReason"]), (top, "r-" + top))

    def test_one_hooks_output_passes_untouched(self):
        self.plant(session_start="print('line 1\\n\\n  line 2')")
        self.assertEqual(self.go("SessionStart", claude())[:2], (0, "line 1\n\n  line 2\n"))

    def test_the_dispatcher_itself_failing_refuses_a_tool_call_only(self):
        for ev, want in (("PreToolUse", 2), ("Stop", 1)):
            self.assertEqual(self.p.call([str(self.p.kit / "hook.py"), "nobody", ev], claude("Bash"), "")[0], want)

    def test_a_tool_no_hook_wants_starts_none(self):
        self.plant(guard="open(%r, 'w')" % str(self.p.tmp / "ran"))
        self.assertEqual(self.go("PreToolUse", claude("Read", {"file_path": "x"})), (0, "", ""))
        self.assertFalse((self.p.tmp / "ran").exists())


class Install(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.t = self.base / "p"
        self.t.mkdir()
        sh(self.t, "git", "init", "-q")

    def install(self, *a, runtime="local"):
        env = dict(BASE_ENV, KIT_NO_FUNNEL="1", KIT_BIN_DIR=str(self.base / "bin"), XDG_CONFIG_HOME=str(self.base / "xdg"))
        first = ["--project", "P", "--no-survey", "t", "--runtime", runtime, "--no-cmd"] if "--update" not in a else []
        r = subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(self.t)] + first + list(a), capture_output=True,
                           text=True, env=env, timeout=180, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def settings(self):
        return json.loads((self.t / ".claude" / "settings.json").read_text())

    def cmds(self):
        return [(ev, h["command"]) for ev, gs in self.settings()["hooks"].items() for g in gs for h in g["hooks"]]

    def own_line(self):
        s = self.settings()
        s["hooks"].setdefault("Stop", []).insert(0, {"hooks": [{"type": "command", "command": "echo mine"}]})
        s["hooks"]["Notification"] = [{"hooks": [{"type": "command", "command": "echo note"}]}]
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s))

    def test_move_rerun_and_back(self):
        self.assertEqual(self.install()[0], 0)
        self.own_line()
        before = sorted(self.cmds())
        agy_before = json.loads((self.t / ".agents" / "hooks.json").read_text())
        rc, out = self.install("--update", "--unified-hooks")
        self.assertEqual(rc, 0, out)
        cmds = self.cmds()
        self.assertEqual(sorted(c for c in cmds if ".kit/hook.py" not in c[1]), [("Notification", "echo note"), ("Stop", "echo mine")])
        self.assertEqual(sorted(e for e, c in cmds if ".kit/hook.py" in c), sorted(hook.events("cli")))
        agy = json.loads((self.t / ".agents" / "hooks.json").read_text())["kit-board"]
        self.assertEqual({k: len(v) for k, v in agy.items()}, {e: 1 for e in hook.events("agy")})
        self.assertTrue((self.t / ".kit" / "hooks" / "kit_guard.py").is_file())
        kept = self.settings()
        rc, out = self.install("--update")                       # the choice is kept, and a rerun changes nothing
        self.assertEqual((rc, self.settings()), (0, kept), out)
        self.assertIn(" ok, 0 modified, 0 missing", self.verify())
        rc, out = self.install("--update", "--no-unified-hooks")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.cmds()), before)
        self.assertEqual(json.loads((self.t / ".agents" / "hooks.json").read_text()), agy_before)
        self.assertFalse((self.t / ".kit" / "hook.py").exists())
        self.assertFalse((self.t / ".kit" / "hooks" / "kit_guard.py").exists())
        self.assertEqual((self.t / ".claude" / "hooks" / "kit" / "kit_guard.py").read_bytes(), (ROOT / "kit" / "hooks" / "kit_guard.py").read_bytes())
        self.assertIn(" ok, 0 modified, 0 missing", self.verify())

    def verify(self):
        return self.install("--update", "--verify")[1]

    def test_doctor_knows_the_dispatcher(self):
        self.install("--unified-hooks")
        sys.path.insert(0, str(ROOT / "scripts"))
        import kit

        def doctor():
            issues, lines = [], []
            kit._doctor_hook_parity(str(self.t), issues, lines)
            return "; ".join(issues)
        self.assertEqual(doctor(), "")
        s = self.settings()
        del s["hooks"]["Stop"]
        pre = s["hooks"]["PreToolUse"][0]
        pre["matcher"] = pre["matcher"].replace("|Edit", "")
        s["hooks"]["PreCompact"].append({"hooks": [{"type": "command", "command": 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/kit/kit_stop.py"'}]})
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s))
        self.assertIn("0 lines for Stop", doctor())
        self.assertIn("still runs", doctor())
        self.assertIn("PreToolUse misses Edit", doctor())
        self.install("--update")                                              # and --update repairs it
        self.assertEqual(doctor(), "")
        (self.t / ".kit" / "hook.py").unlink()
        self.assertIn("without a working .kit/hook.py", doctor())

    def test_fresh_install_holds_one_copy(self):
        rc, out = self.install("--unified-hooks")
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.t / ".claude" / "hooks").exists())
        self.assertFalse((self.t / ".agents" / "hooks").exists())
        r = subprocess.run([sys.executable, "-I", str(self.t / ".kit" / "hooks" / "test_hooks.py")], capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        for rt, ev in (("cli", "PreToolUse"), ("agy", "PreToolUse")):
            pay = claude("Bash", {"command": "rm -rf src"}) if rt == "cli" else agy("run_command", {"CommandLine": "rm -rf src"})
            r = subprocess.run([sys.executable, "-I", str(self.t / ".kit" / "hook.py"), rt, ev], text=True, capture_output=True, timeout=60,
                               input=json.dumps(dict(pay, hook_event_name=ev) if rt == "cli" else pay).replace("@ROOT@", str(self.t)), env=BASE_ENV)
            self.assertIn("REFUSED", r.stdout + r.stderr, (rt, r))

    def test_a_session_started_before_the_move_is_not_stopped(self):
        self.install()
        old = [c for _, c in self.cmds()]
        self.install("--update", "--unified-hooks")
        for c in old:
            r = subprocess.run(["sh", "-c", c], text=True, capture_output=True, cwd=str(self.t), timeout=120,
                               input=json.dumps(dict(claude("Bash", {"command": "ls"}), hook_event_name="PreToolUse")).replace("@ROOT@", str(self.t)),
                               env=dict(BASE_ENV, CLAUDE_PROJECT_DIR=str(self.t), KIT_BOARD_URL="http://127.0.0.1:9"))
            self.assertNotIn("No such file", r.stderr, c)
            self.assertNotIn("Traceback", r.stderr, c)
        guard = next(c for c in old if "kit_guard.py" in c)
        r = subprocess.run(["sh", "-c", guard], text=True, capture_output=True, timeout=60, env=dict(BASE_ENV, CLAUDE_PROJECT_DIR=str(self.t)),
                           input=json.dumps(dict(claude("Bash", {"command": "rm -rf src"}), hook_event_name="PreToolUse")).replace("@ROOT@", str(self.t)))
        self.assertEqual(r.returncode, 2)                       # and the guard still guards it
        c = json.loads((self.t / ".kit" / "config.json").read_text())
        c["kit"]["unified_hooks"] = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
        (self.t / ".kit" / "config.json").write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n")
        self.install("--update")
        self.assertFalse((self.t / ".claude" / "hooks" / "kit").exists())

    def test_refused_over_the_projects_own_work_and_nothing_changes(self):
        self.install()
        g = self.t / ".claude" / "hooks" / "kit" / "kit_stop.py"
        g.write_text(g.read_text() + "# my change\n")
        s = self.settings()
        s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "FOO=1 python3 .claude/hooks/kit/kit_guard.py"}]})
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s))

        def snap():
            return {str(p): p.read_bytes() for p in self.t.rglob("*") if p.is_file() and ".git/" not in str(p) and "__pycache__" not in str(p)}
        before = snap()
        rc, out = self.install("--update", "--unified-hooks")
        self.assertNotEqual(rc, 0)
        self.assertIn("kit_stop.py is not the kit's", out)
        self.assertIn("runs a kit hook its own way: FOO=1", out)
        self.assertEqual(snap(), before)

    def test_a_runtime_the_config_does_not_name_is_refused(self):
        self.install(runtime="agy")
        (self.t / ".claude" / "hooks" / "kit").mkdir(parents=True)
        rc, out = self.install("--update", "--unified-hooks")
        self.assertNotEqual(rc, 0)
        self.assertIn("Claude Code hooks are the project's own choice", out)

    def test_without_the_board_no_line_names_a_missing_hook(self):
        for extra in ((), ("--unified-hooks",)):
            with tempfile.TemporaryDirectory() as d:
                self.t = Path(d) / "p"
                self.t.mkdir()
                sh(self.t, "git", "init", "-q")
                rc, out = self.install("--no-board", *extra)
                self.assertEqual(rc, 0, out)
                text = (self.t / ".agents" / "hooks.json").read_text() + (self.t / ".claude" / "settings.json").read_text()
                for f in set(re.findall(r"hooks/kit/(kit_\w+\.py)", text)):
                    self.assertTrue((self.t / ".agents" / "hooks" / "kit" / f).is_file(), f)
                if extra:
                    self.assertNotIn("PreInvocation", text)  # board-only event; PostInvocation carries kit_memory_jit.py (memory)


if __name__ == "__main__":
    unittest.main()
