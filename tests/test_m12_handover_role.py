"""M12: the orchestrator hands over at its context limit however its session began.

2026-11-27: orchestrator session 9919b5f1 reached 528,590 tokens (limit 380k) with no hand-over. kit_pre_compact.py took
the role from the session's first user message only; 9919b5f1 began with the owner's question ("Are there any posts for
a Claude code web runners?"), took over as orchestrator later, so the hook said "other" and never blocked the Stop.

1. Every hook that decides a role (kit_pre_compact's hand-over, kit_session_start's compaction notice, kit_task_train)
   uses usage.py `session_role` -> `role_of`, the function M11 gave the usage row: the `_orchestrator` post's
   session_id, a hand-over line, a takeover mid-session all count.
2. A transcript that starts with an owner question, then takes over, then passes 380k: the Stop is blocked ONCE with
   the hand-over steps.
3. `python3 tests/test_m12_handover_role.py --replay`: 9919b5f1 as its ledger row records it (first words, cwd, branch,
   peak 528,590, its orchestrator commands) through the hook before M12 (git HEAD~ or a given file) and now.

Run via: python3 -m unittest tests.test_m12_handover_role -v
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "kit" / "hooks" / "kit_pre_compact.py"
START = ROOT / "kit" / "hooks" / "kit_session_start.py"

spec = importlib.util.spec_from_file_location("kit_usage_m12", ROOT / "scripts" / "usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)

QUESTION = "Are there any posts for a Claude code web runners?"      # 9919b5f1's first words (ledger row, 11-26)
PEAK = 528590                                                       # 9919b5f1's context_peak in the ledger
TAKEOVER = "cd /home/user/board && python3 -I .kit/kit.py resume --role orchestrator"


def _usage(total):
    return {"input_tokens": 2, "output_tokens": 50, "cache_read_input_tokens": total - 2000,
            "cache_creation_input_tokens": 1998}


def transcript(d, sid, prompt, cmds, peak, cwd="/home/user", branch="HEAD"):
    """A Claude Code transcript: the first prompt, an answer, then each command as a Bash call, context rising to peak."""
    lines = [{"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": "2026-11-26T21:05:55.543Z",
              "message": {"role": "user", "content": prompt}},
             {"type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": "2026-11-26T21:06:00Z",
              "message": {"id": "a0", "model": "claude-opus-5-5", "usage": _usage(30000),
                          "content": [{"type": "text", "text": "Yes, three posts."}]}}]
    n = max(1, len(cmds))
    for i, cmd in enumerate(cmds):
        t = "2026-11-26T21:%02d:00.000Z" % (7 + i)
        lines.append({"type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": t,
                      "message": {"id": "m%d" % i, "model": "claude-opus-5-5", "usage": _usage(30000 + (peak - 30000) * (i + 1) // (n + 1)),
                                  "content": [{"type": "tool_use", "id": "t%d" % i, "name": "Bash", "input": {"command": cmd}}]}})
        lines.append({"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": t, "toolUseResult": {},
                      "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t%d" % i, "content": "ok"}]}})
    lines.append({"type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": "2026-11-26T23:40:30Z",
                  "message": {"id": "end", "model": "claude-opus-5-5", "usage": _usage(peak),
                              "content": [{"type": "text", "text": "."}]}})
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def project(d, orchestrator_sid=None):
    root = os.path.join(d, "proj")
    os.makedirs(os.path.join(root, ".kit", "board_data"))
    subprocess.run(["git", "init", "-q", root], check=True)
    if orchestrator_sid:
        con = sqlite3.connect(os.path.join(root, ".kit", "board_data", "board.db"))
        con.execute("create table docs (collection text, doc_id text, data text)")
        con.execute("insert into docs values ('posts', '_orchestrator', ?)", (json.dumps({"session_id": orchestrator_sid}),))
        con.commit()
        con.close()
    return root


def stop(hook, root, sid, tp, env=None):
    """Run a Stop hook as Claude Code does; its JSON answer ({} when it prints nothing)."""
    e = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "KIT_SESSION_NAME", "CLAUDE_CODE_REMOTE")}
    e.update(CLAUDE_PROJECT_DIR=root, **(env or {}))
    payload = {"hook_event_name": "Stop", "session_id": sid, "transcript_path": tp, "cwd": root, "stop_hook_active": False}
    out = subprocess.run([sys.executable, "-I", str(hook)], input=json.dumps(payload), capture_output=True, text=True,
                         env=e, cwd=root, timeout=60).stdout.strip()
    return json.loads(out) if out else {}


def _flag_clear(sid):
    p = os.path.join(tempfile.gettempdir(), "kit-handover-%s.flag" % sid)
    if os.path.exists(p):
        os.remove(p)


class HandOverHowever(unittest.TestCase):
    def run_case(self, prompt, cmds, peak, orch_post=False, env=None, twice=False):
        sid = "m12-" + uuid.uuid4().hex[:12]
        self.addCleanup(_flag_clear, sid)
        with tempfile.TemporaryDirectory() as d:
            root = project(d, sid if orch_post else None)
            tp = transcript(d, sid, prompt, cmds, peak)
            first = stop(HOOK, root, sid, tp, env)
            return (first, stop(HOOK, root, sid, tp, env)) if twice else first

    def test_question_then_takeover_past_380k_blocked_once(self):
        """(asked: owner chat 2026-11-27 "why hasn't the hook made you hand over?") 9919b5f1's shape: blocked once."""
        first, second = self.run_case(QUESTION, ["ls", TAKEOVER, "python3 -I .kit/kit.py launch cl55"], PEAK, twice=True)
        self.assertEqual(first.get("decision"), "block", first)
        r = first.get("reason", "")
        self.assertIn("HANDOVER REQUIRED (context at 528k >= 380k)", r)
        self.assertIn("handover.py --role orchestrator", r)
        self.assertIn("Hand-over:", r)
        self.assertEqual(second, {}, "the second stop passes (once only)")

    def test_takeover_by_launch_and_land_only(self):
        self.assertEqual(self.run_case(QUESTION, ["python3 -I .kit/kit.py launch m12", "python3 -I .kit/kit.py land m11"],
                                       400000).get("decision"), "block")

    def test_the_orchestrator_post_names_it(self):
        """No orchestrator command in this transcript, but the board's `_orchestrator` post names the session."""
        self.assertEqual(self.run_case(QUESTION, ["ls"], 390000, orch_post=True).get("decision"), "block")

    def test_a_hand_over_line_first(self):
        self.assertEqual(self.run_case("Run `python3 -I .kit/kit.py resume --role orchestrator` and continue.", ["ls"],
                                       390000).get("decision"), "block")

    def test_took_over_although_launched_as_a_part(self):
        self.assertEqual(self.run_case(QUESTION, [TAKEOVER], 390000, env={"KIT_ROLE": "part"}).get("decision"), "block")

    def test_below_the_limit_stops_normally(self):
        self.assertEqual(self.run_case(QUESTION, [TAKEOVER], 370000), {})

    def test_a_question_without_a_takeover_is_never_blocked(self):
        self.assertEqual(self.run_case(QUESTION, ["ls", "git status"], PEAK), {})

    def test_a_part_past_380k_is_never_blocked(self):
        self.assertEqual(self.run_case("Your prompt is docs/prompts/m12-x.md: read it and do it.",
                                       ["python3 -I .kit/kit.py resume --role part --post m12-x"], PEAK), {})

    def test_one_role_function(self):
        """The hooks and the usage row answer the same: kit_pre_compact.session_role is usage.session_role."""
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            tp = transcript(d, "s1", QUESTION, [TAKEOVER], 100000)
            sys.path.insert(0, str(HOOK.parent))
            try:
                hspec = importlib.util.spec_from_file_location("kit_pre_compact_m12", HOOK)
                hook = importlib.util.module_from_spec(hspec)
                hspec.loader.exec_module(hook)
            finally:
                sys.path.remove(str(HOOK.parent))
            self.assertEqual(hook.session_role({"transcript_path": tp, "session_id": "s1"}, root), "orchestrator")
            self.assertEqual(usage.session_role(tp, root, "s1"), "orchestrator")
            self.assertEqual(hook.inspect_claude_transcript(tp)[1], None, "the first message alone says nothing")
        for f in ("kit_pre_compact.py", "kit_session_start.py", "kit_task_train.py"):
            self.assertIn("session_role(", (HOOK.parent / f).read_text(), f)

    def _guard_hooks(self):
        """kit_orchestrator_guard, kit_brief_guard, kit_cards_reminder, loaded from kit/hooks (their own imports)."""
        mods = {}
        sys.path.insert(0, str(HOOK.parent))
        try:
            for name in ("kit_orchestrator_guard", "kit_brief_guard", "kit_cards_reminder"):
                sp = importlib.util.spec_from_file_location(name, HOOK.parent / (name + ".py"))
                mods[name] = importlib.util.module_from_spec(sp)
                sys.modules[name] = mods[name]
                sp.loader.exec_module(mods[name])
        finally:
            sys.path.remove(str(HOOK.parent))
        return mods

    def _roles(self, prompt, cmds, env=None):
        """What the three guard hooks say of a transcript (a fresh session id each, no mark, no cache)."""
        mods = self._guard_hooks()
        out = {}
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            clean = {k: v for k, v in os.environ.items()
                     if k not in ("KIT_ROLE", "HARMONIZE_ROLE", "KIT_SESSION_NAME", "CLAUDE_CODE_SESSION_ID")}
            clean.update(CLAUDE_PROJECT_DIR=root, **(env or {}))
            with mock.patch.dict(os.environ, clean, clear=True), \
                    mock.patch.object(mods["kit_cards_reminder"], "MARK", os.path.join(d, "no-mark")):
                for name, mod in mods.items():
                    sid = "m12-%s-%s" % (name, uuid.uuid4().hex[:8])
                    tp = transcript(d, sid, prompt, cmds, 100000)
                    out[name] = "orchestrator" if mod.is_orchestrator(
                        {"session_id": sid, "transcript_path": tp, "cwd": root}) else "other"
        return out

    def test_takeover_reads_orchestrator_in_the_three_guard_hooks(self):
        """CR-m12 redirect: kit_orchestrator_guard, kit_brief_guard and kit_cards_reminder use session_role: the
        takeover fixture (an owner question, then `kit.py resume --role orchestrator`) reads `orchestrator` in all three,
        even launched as a part (KIT_ROLE=part); a question alone and a part read `other`."""
        both = {"kit_orchestrator_guard": "orchestrator", "kit_brief_guard": "orchestrator",
                "kit_cards_reminder": "orchestrator"}
        self.assertEqual(self._roles(QUESTION, [TAKEOVER]), both)
        self.assertEqual(self._roles(QUESTION, [TAKEOVER], {"KIT_ROLE": "part"}), both)
        self.assertEqual(set(self._roles(QUESTION, ["ls"]).values()), {"other"})
        self.assertEqual(set(self._roles("Your prompt is docs/prompts/m12-x.md: read it and do it.",
                                         ["python3 -I .kit/kit.py resume --role part --post m12-x"]).values()), {"other"})
        for f in ("kit_orchestrator_guard.py",):
            self.assertIn("session_role", (HOOK.parent / f).read_text(), f)

    def test_a_no_is_read_again_after_a_takeover(self):
        """The guard caches a no until the transcript's new lines hold a takeover's mark."""
        g = self._guard_hooks()["kit_orchestrator_guard"]
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            clean = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "HARMONIZE_ROLE")}
            clean["CLAUDE_PROJECT_DIR"] = root
            sid = "m12-later-%s" % uuid.uuid4().hex[:8]
            with mock.patch.dict(os.environ, clean, clear=True):
                tp = transcript(d, sid, QUESTION, ["ls"], 100000)
                self.assertFalse(g.is_orchestrator({"session_id": sid, "transcript_path": tp, "cwd": root}))
                tp = transcript(d, sid, QUESTION, ["ls", TAKEOVER], 100000)
                self.assertTrue(g.is_orchestrator({"session_id": sid, "transcript_path": tp, "cwd": root}))

    def test_compaction_notice_for_a_takeover(self):
        """kit_session_start after a compaction tells a session that took over to hand over (the same role)."""
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            tp = transcript(d, "s2", QUESTION, [TAKEOVER], 200000)
            e = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE",)}
            e["CLAUDE_PROJECT_DIR"] = root
            out = subprocess.run([sys.executable, "-I", str(START)], capture_output=True, text=True, env=e, cwd=root,
                                 input=json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "s2",
                                                   "transcript_path": tp, "cwd": root}), timeout=60).stdout
            self.assertIn("handover.py --role orchestrator", out)


def replay(old_hook=None):
    """9919b5f1 from its ledger row, through the hook before M12 and now."""
    with tempfile.TemporaryDirectory() as d:
        if not old_hook:
            old_hook = os.path.join(d, "old_kit_pre_compact.py")
            src = subprocess.run(["git", "-C", str(ROOT), "show", "origin/main:kit/hooks/kit_pre_compact.py"],
                                 capture_output=True, text=True).stdout
            with open(old_hook, "w") as fh:
                fh.write(src)
            os.symlink(str(HOOK.parent / "kit_config.py"), os.path.join(d, "kit_config.py"))
        sid = "9919b5f1-6688-5d2d-8494-8c5ecc3fb564"
        root = project(d)
        tp = transcript(d, sid, QUESTION, ["ls", TAKEOVER, "python3 -I .kit/kit.py launch cl55",
                                           "python3 -I .kit/kit.py land cl53"], PEAK)
        print("9919b5f1 (ledger row 2026-11-26): first words %r, cwd /home/user, branch HEAD, peak %s tokens" % (QUESTION, PEAK))
        for name, h in (("before M12", old_hook), ("M12", str(HOOK))):
            _flag_clear(sid)
            out = stop(h, root, sid, tp)
            print("- %s: %s" % (name, ("BLOCKED: " + out["reason"][:90] + "...") if out.get("decision") == "block"
                                 else "stop allowed (no hand-over)"))
        _flag_clear(sid)
        print("- usage row role: %s" % usage.session_role(tp, root, sid))


if __name__ == "__main__":
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        replay(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
    else:
        unittest.main()
