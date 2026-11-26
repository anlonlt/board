#!/usr/bin/env python3
"""CL30: every agent uses the kit's scripts, enforced by hooks (Harmonize docs/KIT-SCRIPTS.md § Gaps, 3138c3f8).

One test class per gap of the prompt, each red on kit main a7ce380 and green after:
  1. the kit guard holds parts and the clerk too: no raw ArtifactData board write where a kit command exists;
  2. a reply to the owner needs a fresh `kit.py resume` whatever the tool (ArtifactData, any board_cli write);
  3. pull + load before resume and the reads; push + sent after local writes (reminder, Stop check, task-train hint);
  4. orchestrator guard holes: tagged launches checked, `kit.py launch` required, its --session-id half reminded;
  5. agy: the board-kit MCP tools obey the brief guard (board_resume stamps, board_reply checks);
  6. kit_cards_reminder.py is registered (CLI set, and an --update wires a present unwired kit guard);
  7. CLAUDE_CODE_ENABLE_TODO_TOOLS: the web set carries kit_task_train.py, so the installer sets it;
  8. `artifact push` refuses a copy that was never loaded (CL28's open item).
python3 -m unittest tests.test_cl30_kit_scripts -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "kit" / "hooks"
GUARD = HOOKS / "kit_orchestrator_guard.py"
BOARD_GUARD = HOOKS / "kit_board_guard.py"
BRIEF = HOOKS / "kit_brief_guard.py"
TASK_TRAIN = HOOKS / "kit_task_train.py"
ART = "https://claude.ai/artifact/X"
CS = "mcp__claude-code-remote__create_session"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_hook(hook, proj, data, role=None, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "HARMONIZE_ROLE", "KIT_PROJECT_DIR")}
    env["CLAUDE_PROJECT_DIR"] = str(proj)
    if role:
        env["KIT_ROLE"] = role
    env.update(env_extra or {})
    data = dict({"session_id": "s-" + uuid.uuid4().hex, "cwd": str(proj)}, **data)
    r = subprocess.run([sys.executable, "-I", str(hook)], input=json.dumps(data), capture_output=True, text=True,
                       env=env, timeout=60)
    return r.returncode, r.stdout, r.stderr


def project(artifact=True):
    p = Path(tempfile.mkdtemp(prefix="cl30-"))
    (p / ".kit").mkdir()
    cfg = {"project": "P", "board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local"}}
    if artifact:
        cfg["kit"]["artifact"] = {"url": ART, "prefix": ""}
    (p / ".kit" / "config.json").write_text(json.dumps(cfg))
    return p


class Gap1PartsAndClerk(unittest.TestCase):
    """The guard used to hold the orchestrator alone; a part or the clerk wrote posts and comments by hand."""

    def setUp(self):
        self.p = project()

    def pre(self, ti, role="part", **kw):
        return run_hook(GUARD, self.p, {"hook_event_name": "PreToolUse", "tool_name": "ArtifactData", "tool_input": ti},
                        role=role, **kw)

    def test_a_part_writing_its_post_by_hand_is_refused(self):
        for ti in ({"action": "set", "collection": "posts", "doc_id": "cl30-x", "data": {"title": "t"}},
                   {"action": "update", "collection": "repos/board/posts", "doc_id": "cl30-x", "data": {"body": "b"}},
                   {"action": "str_replace", "collection": "posts", "doc_id": "cl30-x", "old_str": "a", "new_str": "b"},
                   {"action": "set", "collection": "comments", "doc_id": "c9", "data": {"post_id": "x", "text": "hi"}},
                   {"action": "batch", "writes": [{"op": "set", "collection": "posts", "doc_id": "y", "data": {}}]}):
            rc, _, err = self.pre(ti)
            self.assertEqual(rc, 2, ti)
            self.assertIn("board_cli.py", err)
            self.assertIn("artifact push", err)

    def test_the_clerk_and_a_review_too(self):
        for role in ("clerk", "consultant", "runner"):
            rc, _, _ = self.pre({"action": "set", "collection": "comments", "doc_id": "c", "data": {"text": "x"}}, role=role)
            self.assertEqual(rc, 2, role)

    def test_what_has_no_kit_command_passes(self):
        out = str(self.p / ".kit" / "run" / "artifact" / "out" / "docs" / "0000.json")
        for ti in ({"action": "batch", "writes": [{"op": "set", "collection": "posts", "doc_id": "y", "file_path": out}]},
                   {"action": "set", "collection": "typing", "doc_id": "cl30-x", "data": {"on": True}},
                   {"action": "get", "collection": "posts", "doc_id": "cl30-x"},
                   # the clerk's marks: read / waiting note on the owner's comment, the orchestrator's new session id
                   {"action": "update", "collection": "comments", "doc_id": "c1", "data": {"read": True, "read_at": "t"}},
                   {"action": "update", "collection": "comments", "doc_id": "c1", "data": {"waiting_note": "n"}},
                   {"action": "update", "collection": "posts", "doc_id": "_orchestrator",
                    "data": {"session_id": "s", "started_by": "clerk", "started_at": "t"}}):
            self.assertEqual(self.pre(ti, role="clerk")[0], 0, ti)

    def test_a_raw_dump_by_a_part_is_refused(self):
        rc, _, err = self.pre({"action": "list", "collection": "posts", "query": {"limit": 1000}})
        self.assertEqual(rc, 2)
        self.assertIn("artifact pull", err)

    def test_subagents_and_the_owner_pass(self):
        ti = {"action": "set", "collection": "posts", "doc_id": "x", "data": {}}
        self.assertEqual(run_hook(GUARD, self.p, {"hook_event_name": "PreToolUse", "tool_name": "ArtifactData",
                                                  "tool_input": ti, "agent_id": "a1"}, role="part")[0], 0)
        self.assertEqual(self.pre(ti, role="owner")[0], 0)


class Gap2ReplyNeedsAFreshResume(unittest.TestCase):
    def setUp(self):
        self.p = project()

    def test_the_web_set_matches_artifact_replies(self):
        inst = load("install_cl30_g2", ROOT / "install.py")
        pre = [m for ev, m, f in inst.CLAUDE_SETTINGS if ev == "PreToolUse" and f == "kit_brief_guard.py"]
        self.assertTrue(pre and "ArtifactData" in pre[0])

    def test_a_reply_written_with_board_cli_set_is_a_reply(self):
        for cmd in ("python3 -I .kit/board_cli.py set comments c7 --data '{\"post_id\":\"p\",\"reply_to\":\"c1\",\"text\":\"ok\"}'",
                    "python3 -I .kit/board_cli.py batch --file w.json --reply-to c1",
                    "python3 -I .kit/board_cli.py reply p --text ok"):
            rc, _, err = run_hook(BRIEF, self.p, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                  "tool_input": {"command": cmd}}, role="orchestrator")
            self.assertEqual(rc, 2, cmd)
            self.assertIn("kit.py resume", err)

    def test_an_artifact_str_replace_of_a_reply(self):
        rc, _, _ = run_hook(BRIEF, self.p, {"hook_event_name": "PreToolUse", "tool_name": "ArtifactData", "tool_input": {
            "action": "set", "collection": "comments", "doc_id": "r", "data": {"reply_to": "c1", "text": "x"}}},
            role="orchestrator")
        self.assertEqual(rc, 2)

    def test_an_update_into_a_present_runtime_widens_the_brief_guard_to_artifact_replies(self):
        t = Path(tempfile.mkdtemp(prefix="cl30-inst-")) / "proj"
        t.mkdir()
        subprocess.run(["git", "init", "-q", str(t)], check=True)
        env = dict(os.environ, KIT_NO_FUNNEL="1", XDG_CONFIG_HOME=tempfile.mkdtemp())
        r = subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", "--quick", "--project",
                            "Proj", "--runtime", "agy", "--cli", "agy"], capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cl = t / ".claude" / "hooks" / "kit"
        cl.mkdir(parents=True)
        for h in ("kit_config.py", "kit_brief_guard.py", "kit_orchestrator_guard.py", "kit_cards_reminder.py",
                  "kit_task_train.py", "kit_pre_compact.py"):
            (cl / h).write_bytes((HOOKS / h).read_bytes())
        cmd = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/kit/%s"'
        # Harmonize's settings.json on 3138c3f8: the brief guard on Bash only, the cards reminder nowhere, no env
        harm = {"hooks": {
            "PreToolUse": [{"matcher": "ArtifactData|mcp__claude-code-remote__create_session",
                            "hooks": [{"type": "command", "command": cmd % "kit_orchestrator_guard.py"}]},
                           {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd % "kit_brief_guard.py"}]}],
            "PostToolUse": [{"matcher": "TaskCreate|TaskUpdate", "hooks": [{"type": "command", "command": cmd % "kit_task_train.py"}]},
                            {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd % "kit_brief_guard.py"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        st = t / ".claude" / "settings.json"
        st.write_text(json.dumps(harm))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(t), "--no-cmd", "--update"],
                           capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = json.loads(st.read_text())
        brief = [g["matcher"] for g in s["hooks"]["PreToolUse"] if any("kit_brief_guard" in h["command"] for h in g["hooks"])]
        self.assertEqual(len(brief), 1, brief)                                   # widened, not doubled
        self.assertIn("ArtifactData", brief[0])
        self.assertIn("Bash", brief[0])
        # gap 6: the cards reminder, present and unwired, is wired; gap 7: the task tools are on
        cmds = [h["command"] for ev in s["hooks"].values() for g in ev for h in g["hooks"]]
        self.assertTrue(any("kit_cards_reminder.py" in c for c in cmds), cmds)
        self.assertEqual((s.get("env") or {}).get("CLAUDE_CODE_ENABLE_TODO_TOOLS"), "1")
        self.assertIn({"hooks": [{"type": "command", "command": "echo mine"}]}, s["hooks"]["Stop"])
        self.assertFalse(any("kit_pre_compact.py" in c for c in cmds), "Harmonize's own hand-over stays the only one")


class Gap3FreshCopyAndSentChanges(unittest.TestCase):
    def setUp(self):
        self.p = project()
        self.art = self.p / ".kit" / "run" / "artifact"

    def kit_env(self):
        env = dict(os.environ, KIT_ARTIFACT_DIR=str(self.art), KIT_ARTIFACT_URL=ART, KIT_ARTIFACT_PREFIX="")
        env.pop("KIT_STALE_OK", None)
        return env

    def test_resume_refuses_a_copy_never_loaded_or_stale(self):
        cfg = self.p / ".kit" / "config.json"
        for cmd in (["resume"], ["post", "x"], ["open"], ["train", "cli"]):
            r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "--config", str(cfg)] + cmd,
                               capture_output=True, text=True, env=self.kit_env(), timeout=60)
            self.assertEqual(r.returncode, 3, cmd)
            self.assertIn("artifact pull", r.stdout + r.stderr)
        self.art.mkdir(parents=True, exist_ok=True)
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        (self.art / "state.json").write_text(json.dumps({"loaded_at": old, "versions": {}}))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "--config", str(cfg), "resume"],
                           capture_output=True, text=True, env=self.kit_env(), timeout=60)
        self.assertEqual(r.returncode, 3)
        self.assertIn("60 min", r.stdout + r.stderr)

    def test_a_fresh_copy_passes_the_gate(self):
        k = load("kit_cl30_g3", ROOT / "scripts" / "kit.py")
        prev_stale = os.environ.pop("KIT_STALE_OK", None)
        if prev_stale is not None:
            self.addCleanup(os.environ.__setitem__, "KIT_STALE_OK", prev_stale)
        os.environ.update(KIT_ARTIFACT_DIR=str(self.art), KIT_ARTIFACT_URL=ART)
        self.addCleanup(os.environ.pop, "KIT_ARTIFACT_DIR", None)
        self.addCleanup(os.environ.pop, "KIT_ARTIFACT_URL", None)
        self.assertIn("never loaded", k.stale_copy())
        self.art.mkdir(parents=True, exist_ok=True)
        (self.art / "state.json").write_text(json.dumps({"loaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                         "versions": {}}))
        self.assertEqual(k.stale_copy(), "")

    def test_a_local_write_is_followed_by_the_push_reminder(self):
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {
            "command": "python3 -I .kit/board_cli.py update posts cl30-x --data '{\"status\":\"done\"}'"},
            "tool_response": {"exit_code": 0}}, role="part")
        self.assertEqual(rc, 0)
        self.assertIn("kit.py artifact push", out)
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                                    "tool_input": {"command": "git status"}}, role="part")
        self.assertEqual(out.strip(), "")

    def test_stop_with_a_batch_never_sent_is_blocked_once(self):
        (self.art / "out").mkdir(parents=True)
        (self.art / "out" / "batch-01.json").write_text("[]")
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "Stop", "stop_hook_active": False}, role="part")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["decision"], "block")
        self.assertIn("artifact sent", d["reason"])
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "Stop", "stop_hook_active": True}, role="part")
        self.assertEqual(out.strip(), "")

    def test_task_train_names_the_push_when_the_artifact_is_in_kit_artifact(self):
        fake = self.p / ".kit" / "kit.py"
        fake.write_text("import json\nprint(json.dumps({'post': 'cl30-x', 'prompt': 'docs/prompts/cl30-x.md', "
                        "'launch': 'kit.py launch cl30-x', 'prompt_written': True}))\n")
        rc, out, _ = run_hook(TASK_TRAIN, self.p, {"hook_event_name": "PostToolUse", "tool_name": "TaskCreate",
                                                   "tool_input": {"subject": "CL30 x", "metadata": {"train": "cli", "part": "CL30"}},
                                                   "tool_response": {"task": {"id": "1"}}}, role="orchestrator")
        self.assertEqual(rc, 0)
        self.assertIn("artifact push", out)


class Gap4OrchestratorHoles(unittest.TestCase):
    def setUp(self):
        self.p = project()

    def cs(self, ti):
        return run_hook(GUARD, self.p, {"hook_event_name": "PreToolUse", "tool_name": CS, "tool_input": ti}, role="orchestrator")

    def test_a_tag_no_longer_lets_an_inline_prompt_through(self):
        for tag in ("orchestrator", "board-clerk", "review"):
            rc, _, err = self.cs({"prompt": "Do whatever: " + "x" * 50, "tags": [tag]})
            self.assertEqual(rc, 2, tag)
        for ti in ({"prompt": "Read .kit/claude-cli/orchestrator.md and start as the orchestrator for this repository: "
                              "you are orchestrator 53.", "tags": ["orchestrator"]},
                   {"prompt": "Your prompt is .kit/claude-cli/clerk.md: read it and follow it.", "tags": ["board-clerk"]},
                   {"prompt": "Your prompt is .kit/run/review-cl30.md: read it and do it.", "tags": ["review"]}):
            self.assertEqual(self.cs(ti)[0], 0, ti)

    def test_a_part_starts_only_through_kit_launch(self):
        ti = {"prompt": "Your prompt is docs/prompts/cl30-x.md: read it and do it."}
        rc, _, err = self.cs(ti)
        self.assertEqual(rc, 2)
        self.assertIn("kit.py launch cl30-x", err)
        d = self.p / ".kit" / "run" / "launch"
        d.mkdir(parents=True)
        (d / "cl30-x.json").write_text(json.dumps({"prompt": ti["prompt"], "at": time.time()}))
        self.assertEqual(self.cs(ti)[0], 0)

    def test_after_create_session_the_second_half_is_named(self):
        d = self.p / ".kit" / "run" / "launch"
        d.mkdir(parents=True)
        (d / "cl30-x.json").write_text(json.dumps({"prompt": "Your prompt is docs/prompts/cl30-x.md: read it and do it.",
                                                   "at": time.time()}))
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "PostToolUse", "tool_name": CS, "tool_input": {
            "prompt": "Your prompt is docs/prompts/cl30-x.md: read it and do it."},
            "tool_response": {"session_id": "session_01NEW"}}, role="orchestrator")
        self.assertIn("kit.py launch cl30-x --session-id session_01NEW", out)
        rc, out, _ = run_hook(BOARD_GUARD, self.p, {"hook_event_name": "Stop", "stop_hook_active": False}, role="orchestrator")
        self.assertIn("--session-id", json.loads(out)["reason"])

    def test_kit_launch_on_the_web_leaves_the_mark_the_guard_reads(self):
        k = load("kit_cl30_g4", ROOT / "scripts" / "kit.py")
        k.ROOT = str(self.p)
        k.launch_mark("cl30-x", "Your prompt is docs/prompts/cl30-x.md: read it and do it.")
        self.assertTrue((self.p / ".kit" / "run" / "launch" / "cl30-x.json").is_file())
        k.launch_mark("cl30-x", None)
        self.assertFalse((self.p / ".kit" / "run" / "launch" / "cl30-x.json").exists())


class Gap5AgyMcp(unittest.TestCase):
    def test_board_reply_needs_a_fresh_board_resume(self):
        p = project(artifact=False)
        mcp = load("mcp_cl30", ROOT / "kit" / "board" / "mcp_stdio.py")
        srv = mcp.MCPServer(str(p))
        calls = []
        srv._run_py = lambda script, args: (calls.append(args) or (True, "ok"))
        ok, msg = srv.call_tool("board_reply", {"post_id": "x", "text": "hi", "sender": "orchestrator"})
        self.assertFalse(ok)
        self.assertIn("board_resume", msg)
        self.assertEqual(calls, [])
        self.assertTrue(srv.call_tool("board_resume", {})[0])
        self.assertTrue((p / ".kit" / "run" / "brief" / "last").is_file())
        self.assertTrue(srv.call_tool("board_reply", {"post_id": "x", "text": "hi", "sender": "orchestrator"})[0])
        # a part's reply on its own post is not the orchestrator's
        q = project(artifact=False)
        srv2 = mcp.MCPServer(str(q))
        srv2._run_py = lambda script, args: (True, "ok")
        self.assertTrue(srv2.call_tool("board_reply", {"post_id": "x", "text": "hi", "sender": "CL30"})[0])


class Gap6And7Registration(unittest.TestCase):
    def test_the_sets(self):
        inst = load("install_cl30_g6", ROOT / "install.py")
        self.assertNotIn("kit_cards_reminder.py", [f for _, _, f in inst.CLAUDE_CLI_SETTINGS])   # the CLI's board server builds the cards
        self.assertIn("kit_cards_reminder.py", [f for _, _, f in inst.CLAUDE_SETTINGS])          # the web's Artifact has none
        self.assertIn("kit_task_train.py", [f for _, _, f in inst.CLAUDE_SETTINGS])
        for spec in (inst.CLAUDE_SETTINGS, inst.CLAUDE_CLI_SETTINGS):
            self.assertIn("kit_board_guard.py", [f for _, _, f in spec])
        self.assertIn("kit_board_guard.py", inst.HOOKS)

    def test_the_reminder_finds_the_orchestrator_by_its_first_prompt(self):
        p = project()
        tp = p / "t.jsonl"
        tp.write_text(json.dumps({"type": "user", "message": {"content": "Read .kit/claude-cli/orchestrator.md and start "
                                                                         "as the orchestrator"}}) + "\n")
        env = {"CLAUDE_CODE_SESSION_ID": "none-" + uuid.uuid4().hex}
        rc, out, _ = run_hook(HOOKS / "kit_cards_reminder.py", p, {"hook_event_name": "UserPromptSubmit",
                                                                   "prompt": "Done. Check post cl30-x.",
                                                                   "transcript_path": str(tp)}, env_extra=env)
        self.assertIn("kit-cards", out)


class Gap8PushNeedsALoad(unittest.TestCase):
    def test_push_refuses_a_never_loaded_copy(self):
        k = load("kit_cl30_g8", ROOT / "scripts" / "kit.py")
        tmp = Path(tempfile.mkdtemp())
        os.environ.update(KIT_ARTIFACT_DIR=str(tmp / "art"), KIT_ARTIFACT_URL=ART, KIT_ARTIFACT_PREFIX="")
        for v in ("KIT_ARTIFACT_DIR", "KIT_ARTIFACT_URL", "KIT_ARTIFACT_PREFIX"):
            self.addCleanup(os.environ.pop, v, None)
        with self.assertRaises(k.Conflict) as cm:
            k.artifact_push(url="http://127.0.0.1:9", token="t")
        self.assertIn("never loaded", str(cm.exception))
        self.assertFalse((tmp / "art" / "out").exists())


if __name__ == "__main__":
    unittest.main()
