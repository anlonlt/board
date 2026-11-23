"""kit_orchestrator_guard.py: the orchestrator works the board through the kit (raw board reads, hand-written step
posts and inline-prompt launches are refused for it, and only for it).

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "kit" / "hooks" / "kit_orchestrator_guard.py"


class OrchestratorGuard(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.proj, ".kit"))

    def run_hook(self, tool, ti, role="orchestrator", prompt=None):
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "HARMONIZE_ROLE")}
        env["CLAUDE_PROJECT_DIR"] = self.proj
        data = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "session_id": "g-" + uuid.uuid4().hex}
        if role:
            env["KIT_ROLE"] = role
        if prompt is not None:
            tp = os.path.join(self.proj, "t.jsonl")
            open(tp, "w").write(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n")
            data["transcript_path"] = tp
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(data), capture_output=True, text=True,
                           env=env, timeout=30)
        return r.returncode, r.stderr

    def test_raw_reads(self):
        rc, err = self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "url": "u"})
        self.assertEqual(rc, 2)
        self.assertIn("kit.py artifact pull", err)
        rc, _ = self.run_hook("ArtifactData", {"action": "query", "collection": "repos/board/comments", "query": {"limit": 1000}})
        self.assertEqual(rc, 2)
        kit_pull = os.path.join(self.proj, ".kit", "run", "artifact", "in")
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "out_dir": kit_pull})[0], 0)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "query", "collection": "comments",
                                                        "query": {"where": [["thread_id", "==", "t"]], "limit": 1}})[0], 0)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "get", "collection": "posts", "doc_id": "X"})[0], 0)

    def test_hand_written_step_posts(self):
        rc, err = self.run_hook("ArtifactData", {"action": "set", "collection": "posts", "doc_id": "CL22-x", "data": {}})
        self.assertEqual(rc, 2)
        self.assertIn("TaskCreate", err)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "set", "collection": "posts", "doc_id": "_trains", "data": {}})[0], 0)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "set", "collection": "comments", "doc_id": "c", "data": {}})[0], 0)
        out = os.path.join(self.proj, ".kit", "run", "artifact", "out", "docs", "0000.json")
        self.assertEqual(self.run_hook("ArtifactData", {"action": "batch", "writes": [
            {"op": "set", "collection": "posts", "doc_id": "CL22-x", "file_path": out}]})[0], 0)
        rc, _ = self.run_hook("ArtifactData", {"action": "batch", "writes": [
            {"op": "set", "collection": "repos/board/posts", "doc_id": "CL22-x", "data": {}}]})
        self.assertEqual(rc, 2)

    def test_launches(self):
        rc, err = self.run_hook("mcp__claude-code-remote__create_session", {"prompt": "Do CL22: keep permissions...", "title": "CL22"})
        self.assertEqual(rc, 2)
        self.assertIn("docs/prompts/<id>.md", err)
        for ti in ({"prompt": "Your prompt is docs/prompts/cl22.md: read it and do it."},
                   {"prompt": "...", "tags": ["orchestrator"]}, {"prompt": "...", "tags": ["board-clerk"]},
                   {"prompt": "...", "title": "CR-CL22 review"}):
            self.assertEqual(self.run_hook("mcp__claude-code-remote__create_session", ti)[0], 0, ti)

    def test_its_subagents_pass(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, KIT_ROLE="orchestrator")
        data = {"tool_name": "ArtifactData", "tool_input": {"action": "list", "collection": "posts"}, "agent_id": "a1",
                "agent_type": "board-cards", "session_id": "s"}
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_only_the_orchestrator(self):
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts"}, role="part")[0], 0)
        # no KIT_ROLE: recognised from its launch prompt (Harmonize's "you are the fiftieth orchestrator")
        rc, _ = self.run_hook("ArtifactData", {"action": "list", "collection": "posts"}, role="",
                              prompt="Harmonize: you are the fiftieth orchestrator, after orchestrator 49.")
        self.assertEqual(rc, 2)
        rc, _ = self.run_hook("ArtifactData", {"action": "list", "collection": "posts"}, role="",
                              prompt="Your prompt is docs/prompts/cl9.md: read it and do it.")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
