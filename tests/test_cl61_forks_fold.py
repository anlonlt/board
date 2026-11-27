"""CL61: a kit fix reaches every project on update: no project keeps a fork of a kit hook.

(asked: the owner, 2026-11-27, "Shouldn't anything that's happening in the kit automatically get fixed on kit update in
harmonize anyway?") Harmonize ran its own `.claude/hooks/context_handover.py` as its Stop hand-over hook; the kit's
`kit_pre_compact.py` was installed beside it, so M12's fix (a session that took over mid-way hands over too) never
reached Harmonize: its fork decided.

1. `install.py --update` lists each project hook doing a kit hook's job and folds it: what it adds becomes kit settings
   (its limits, its rules: "no fallback timers", the clerk at 80k), its line leaves settings.json, the kit's hook is
   wired in its place. A fork it cannot fold is REFUSED with what differs (printed, doctor flags it), its lines untouched; kept on purpose
   only with kit.forks.keep, named every time.
2. The fork's own test cases pass against the kit's hook after the fold; M12's takeover case blocks there.
3. `kit.py doctor` flags a project hook that shadows a kit hook.

The fork below is Harmonize-shaped (its file is in Harmonize, not in this repo): the same event, job, limits and rules.
Run via: python3 -m unittest tests.test_cl61_forks_fold -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, KIT_NO_FUNNEL="1")
sys.path.insert(0, str(ROOT / "tests"))
import test_m12_handover_role as m12  # noqa: E402

_spec = importlib.util.spec_from_file_location("kit_forks_t", ROOT / "kit" / "forks.py")
forks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forks)

TIMERS = "No fallback timers: never schedule a send_later or a wakeup as a safety net before you hand over."
FORK = '''#!/usr/bin/env python3
"""Stop: Harmonize's hand-over at the context limit (its own copy)."""
import json
import os
import sys
import tempfile

ORCH_LIMIT = 300_000
CLERK_LIMIT = 80_000


def role_of(path):
    with open(path) as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("type") == "user":
                text = str(e.get("message", {}).get("content", "")).lower()
                return "clerk" if "clerk" in text else ("orchestrator" if "orchestrator" in text else None)
    return None


def main():
    data = json.load(sys.stdin)
    role = role_of(data.get("transcript_path", ""))
    if role == "orchestrator":
        print(json.dumps({"decision": "block", "reason": "HANDOVER REQUIRED: write .story/state/orchestrator.md, "
                          "commit and push. %s"}))
    elif role == "clerk":
        print(json.dumps({"decision": "block", "reason": "HANDOVER REQUIRED: the clerk never re-arms its listener past 80k."}))


if __name__ == "__main__":
    sys.exit(main())
''' % TIMERS
# A fork with something the kit's hook cannot take as a setting: it runs a command
ODD = FORK.replace("def main():", "def main():\n    import subprocess\n    subprocess.run(['./scripts/notify.sh'])")
FORK_LINE = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/context_handover.py"'


def install(t, *args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", *args],
                          capture_output=True, text=True, env=ENV, timeout=600, stdin=subprocess.DEVNULL)


def doctor(t):
    return subprocess.run([sys.executable, "-I", str(Path(t) / ".kit" / "kit.py"), "doctor"], capture_output=True,
                          text=True, env=ENV, cwd=str(t), timeout=120).stdout


class FoldAtUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "harmonize"
        self.t.mkdir()
        subprocess.run(["git", "init", "-q", str(self.t)], check=True)
        r = install(self.t, "--quick", "--project", "Harmonize", "--runtime", "claude", "--no-survey", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self):
        return json.loads((self.t / ".claude" / "settings.json").read_text())

    def config(self):
        return json.loads((self.t / ".kit" / "config.json").read_text())

    def plant(self, src=FORK):
        (self.t / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
        (self.t / ".claude" / "hooks" / "context_handover.py").write_text(src)
        s = self.settings()
        s["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command", "command": FORK_LINE}]})
        (self.t / ".claude" / "settings.json").write_text(json.dumps(s, indent=2))

    def cmds(self, ev):
        return [h["command"] for g in self.settings()["hooks"].get(ev, []) for h in g["hooks"]]

    def stop(self, prompt, cmds, peak):
        sid = "cl61-" + uuid.uuid4().hex[:12]
        self.addCleanup(m12._flag_clear, sid)
        with tempfile.TemporaryDirectory() as d:
            tp = m12.transcript(d, sid, prompt, cmds, peak)
            hook = next(Path(self.t, *c.split('"')[1].replace("$CLAUDE_PROJECT_DIR/", "").split("/"))
                        for c in self.cmds("Stop") if "kit_pre_compact" in c or ".kit/hook.py" in c)
            e = {k: v for k, v in ENV.items() if k not in ("KIT_ROLE", "CLAUDE_CODE_REMOTE")}
            e["CLAUDE_PROJECT_DIR"] = str(self.t)
            args = [sys.executable, "-I", str(hook)] + (["web", "Stop"] if hook.name == "hook.py" else [])
            out = subprocess.run(args, input=json.dumps({"hook_event_name": "Stop", "session_id": sid,
                                                         "transcript_path": tp, "cwd": str(self.t),
                                                         "stop_hook_active": False}),
                                 capture_output=True, text=True, env=e, cwd=str(self.t), timeout=60).stdout.strip()
            return json.loads(out) if out else {}

    def test_doctor_flags_the_shadow_then_update_folds_it(self):
        """(asked: the owner 2026-11-27) The fork is named, folded at --update, and a kit fix (M12) then reaches it."""
        self.plant()
        self.assertIn("SHADOW .claude/hooks/context_handover.py shadows the kit's kit_pre_compact.py (hand-over, on Stop)",
                      doctor(self.t))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fork folded: .claude/hooks/context_handover.py (hand-over) -> the kit's kit_pre_compact.py", r.stdout)
        self.assertNotIn(FORK_LINE, self.cmds("Stop"), "the fork no longer decides")
        self.assertTrue(any("kit_pre_compact" in c or ".kit/hook.py" in c for c in self.cmds("Stop")), "the kit's hook does")
        k = self.config()["kit"]
        self.assertEqual(k["orchestrator"]["context_limit_k"], 300, "its orchestrator limit, a setting now")
        self.assertEqual(k["clerk"]["context_limit_k"], 80, "the clerk at 80k")
        self.assertIn(TIMERS, k["orchestrator"]["handover_rules"], "no fallback timers, a setting now")
        self.assertIn("the clerk never re-arms its listener past 80k.", k["clerk"]["handover_rules"])
        self.assertNotIn("SHADOW", doctor(self.t))
        # the fork's own cases, against the kit's hook
        first = self.stop("Start as the orchestrator: read .kit/claude/orchestrator.md", ["ls"], 310000)
        self.assertEqual(first.get("decision"), "block", first)
        self.assertIn("(context at 310k >= 300k)", first["reason"])
        self.assertIn(TIMERS, first["reason"])
        self.assertEqual(self.stop("You are the board clerk. Read .kit/clerk.md", ["ls"], 90000).get("decision"), "block")
        self.assertEqual(self.stop("Start as the orchestrator", ["ls"], 290000), {}, "below its limit: stops")
        # M12's takeover case, which the fork let pass: blocked now
        took = self.stop(m12.QUESTION, ["ls", m12.TAKEOVER], m12.PEAK)
        self.assertEqual(took.get("decision"), "block", took)
        self.assertIn("handover.py --role orchestrator", took["reason"])
        # a second update changes nothing more and doubles no rule
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.config()["kit"]["orchestrator"]["handover_rules"].count(TIMERS), 1)

    def test_a_fork_it_cannot_fold_is_refused_with_what_differs(self):
        self.plant(ODD)
        before = self.settings()
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # the rest of the update still lands
        self.assertIn("REFUSED (CL61)", r.stdout)
        self.assertIn("SHADOW", doctor(self.t), "doctor keeps naming it")
        self.assertIn("runs code the kit's hook does not: run()", r.stdout)
        self.assertIn(FORK_LINE, self.cmds("Stop"), "nothing of it changed")
        self.assertNotIn("handover_rules", json.dumps(self.config()))
        self.assertEqual(before["hooks"]["Stop"], self.settings()["hooks"]["Stop"])

    def test_kept_on_purpose_is_named_never_silent(self):
        self.plant(ODD)
        c = self.config()
        c["kit"]["forks"] = {"keep": {".claude/hooks/context_handover.py": "pages the owner's phone; folded in CL62"}}
        (self.t / ".kit" / "config.json").write_text(json.dumps(c, indent=1))
        r = install(self.t, "--update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fork kept: .claude/hooks/context_handover.py does the job of the kit's kit_pre_compact.py "
                      "(hand-over), kept by kit.forks.keep: pages the owner's phone", r.stdout)
        self.assertIn(FORK_LINE, self.cmds("Stop"))
        self.assertIn("kept by kit.forks.keep", doctor(self.t))


class Detect(unittest.TestCase):
    def test_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude", "hooks"))
            for n, body in (("say_it_once.py", "x"), ("session-start.sh", "echo"), ("stop.sh", "echo"),
                            ("notify.sh", "echo"), ("guard.py", "HANDOVER REQUIRED")):
                Path(d, ".claude", "hooks", n).write_text(body)
            s = {"hooks": {
                "Stop": [{"hooks": [{"command": "$CLAUDE_PROJECT_DIR/.claude/hooks/stop.sh"},
                                    {"command": 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/say_it_once.py"'},
                                    {"command": 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py"'},
                                    {"command": 'python3 -I "$CLAUDE_PROJECT_DIR/.kit/hook.py" cli Stop'}]}],
                "SessionStart": [{"hooks": [{"command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"},
                                            {"command": 'python3 -I "$CLAUDE_PROJECT_DIR/.kit/kit.py" fresh'}]}],
                "PostToolUse": [{"hooks": [{"command": "$CLAUDE_PROJECT_DIR/.claude/hooks/notify.sh"}]}]}}
            got = {f["path"]: f["kit"] for f in forks.find(d, s)}
        self.assertEqual(got, {".claude/hooks/stop.sh": "kit_stop.py", ".claude/hooks/say_it_once.py": "kit_say_it_once.py",
                               ".claude/hooks/guard.py": "kit_pre_compact.py",
                               ".claude/hooks/session-start.sh": "kit_session_start.py"})

    def test_kit_repo_has_no_fork(self):
        self.assertEqual(forks.find(str(ROOT)), [])


if __name__ == "__main__":
    unittest.main()
