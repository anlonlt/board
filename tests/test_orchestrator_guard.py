"""kit_orchestrator_guard.py: the orchestrator works the board through the kit (raw board reads, hand-written step
posts and inline-prompt launches are refused for it, and only for it).

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import temp_cleanup  # noqa: E402,F401  CL52: the folders this file makes go at exit

HOOK = Path(__file__).resolve().parent.parent / "kit" / "hooks" / "kit_orchestrator_guard.py"


class Project(unittest.TestCase):
    """A project with the launch marks, and the guard run on it; no test of its own, so a class built on it repeats none."""

    def setUp(self):
        self.proj = os.path.join(tempfile.mkdtemp(), "proj")   # CL52: a clone made next to it stays in the temp folder
        os.makedirs(os.path.join(self.proj, ".kit"))
        # CL30: a part starts only after `kit.py launch <post>`, which leaves this mark for each prompt below
        for pid in ("cl22", "cl24-orchestrator-guard-gaps", "cl9-x"):
            self.mark(pid)

    def mark(self, pid):
        d = os.path.join(self.proj, ".kit", "run", "launch")
        os.makedirs(d, exist_ok=True)
        json.dump({"prompt": "Your prompt is docs/prompts/%s.md: read it and do it." % pid, "at": time.time()},
                  open(os.path.join(d, pid + ".json"), "w"))

    def pulled(self, *calls, root=None, at=None):
        """M11: what `kit.py artifact pull` printed (its record), so the guard lets those whole-collection reads pass."""
        d = os.path.join(root or self.proj, ".kit", "run", "artifact")
        self.printed([{"action": a, "collection": c, "out_dir": os.path.join(d, "in")} for a, c in calls], root=root)
        if at is not None:
            p = os.path.join(d, "printed.json")
            rec = json.load(open(p))
            rec["at"] = at
            json.dump(rec, open(p, "w"))

    def printed(self, calls, root=None):
        """CL72: what the kit printed last (.kit/run/artifact/printed.json), the only board calls that pass."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("kc_test", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "kit", "hooks", "kit_config.py"))
        kc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kc)
        kc.ledger_record(os.path.join(root or self.proj, ".kit", "run", "artifact"), calls, "artifact sync")

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


class OrchestratorGuard(Project):
    def test_raw_reads(self):
        rc, err = self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "url": "u"})
        self.assertEqual(rc, 2)
        self.assertIn("kit.py artifact sync", err)
        rc, _ = self.run_hook("ArtifactData", {"action": "query", "collection": "repos/board/comments", "query": {"limit": 1000}})
        self.assertEqual(rc, 2)
        kit_pull = os.path.join(self.proj, ".kit", "run", "artifact", "in")
        # M11: the pull's folder alone no longer passes (a hand-made list copied it); a call the pull printed does
        rc, err = self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "out_dir": kit_pull})
        self.assertEqual(rc, 2)
        self.assertIn("the kit printed no ArtifactData call here", err)
        self.pulled(("list", "posts"))
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "out_dir": kit_pull})[0], 0)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "comments", "out_dir": kit_pull})[0], 2)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "out_dir": "/tmp/x"})[0], 2)
        self.pulled(("list", "posts"), at=time.time() - 61 * 60)          # printed more than an hour ago
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts", "out_dir": kit_pull})[0], 2)
        # CL72: a lookup or a get the kit did not print is refused too
        self.assertEqual(self.run_hook("ArtifactData", {"action": "query", "collection": "comments",
                                                        "query": {"where": [["thread_id", "==", "t"]], "limit": 1}})[0], 2)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "get", "collection": "posts", "doc_id": "X"})[0], 2)

    def test_hand_written_step_posts(self):
        rc, err = self.run_hook("ArtifactData", {"action": "set", "collection": "posts", "doc_id": "CL22-x", "data": {}})
        self.assertEqual(rc, 2)
        self.assertIn("TaskCreate", err)
        # CL30: a card or a comment by hand is refused too (kit_cards.py / board_cli.py write them, then artifact push)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "set", "collection": "posts", "doc_id": "_trains", "data": {}})[0], 2)
        self.assertEqual(self.run_hook("ArtifactData", {"action": "set", "collection": "comments", "doc_id": "c", "data": {}})[0], 2)
        out = os.path.join(self.proj, ".kit", "run", "artifact", "out", "docs", "0000.json")
        batch = {"action": "batch", "writes": [{"op": "set", "collection": "posts", "doc_id": "CL22-x", "file_path": out}]}
        self.assertEqual(self.run_hook("ArtifactData", batch)[0], 2)          # CL72: under out/ is not enough
        self.printed([batch])
        self.assertEqual(self.run_hook("ArtifactData", batch)[0], 0)          # the batch the kit printed
        rc, _ = self.run_hook("ArtifactData", {"action": "batch", "writes": [
            {"op": "set", "collection": "repos/board/posts", "doc_id": "CL22-x", "data": {}}]})
        self.assertEqual(rc, 2)

    def test_launches(self):
        rc, err = self.run_hook("mcp__claude-code-remote__create_session", {"prompt": "Do CL22: keep permissions...", "title": "CL22"})
        self.assertEqual(rc, 2)
        self.assertIn("docs/prompts/<id>.md", err)
        for ti in ({"prompt": "Your prompt is docs/prompts/cl22.md: read it and do it."},
                   {"prompt": "Read .kit/claude-cli/orchestrator.md and start as the orchestrator: you are 53.",
                    "tags": ["orchestrator"]},
                   {"prompt": "Your prompt is .kit/claude-cli/clerk.md: read it and follow it.", "tags": ["board-clerk"]},
                   {"prompt": "Your prompt is .kit/run/review-cl22.md: read it and do it.", "title": "CR-CL22 review",
                    "tags": ["review"]}):
            self.assertEqual(self.run_hook("mcp__claude-code-remote__create_session", ti)[0], 0, ti)

    def test_inline_prompt_naming_a_path_is_refused(self):
        """CL24 gap 1: a long prompt that merely names docs/prompts/ is still an inline prompt."""
        cs = "mcp__claude-code-remote__create_session"
        long_one = "Do CL24: fix the guard. " + "Keep every hold, test it first. " * 20 + "See docs/prompts/cl24.md."
        rc, err = self.run_hook(cs, {"prompt": long_one, "title": "CL24"})
        self.assertEqual(rc, 2)
        self.assertIn("400", err)
        rc, _ = self.run_hook(cs, {"prompt": "Read docs/prompts/cl24.md, then also rewrite the hook inline as follows."})
        self.assertEqual(rc, 2)
        too_long = "Your prompt is docs/prompts/cl24.md: read it and do it. " + "Also: " + "x" * 400
        self.assertEqual(self.run_hook(cs, {"prompt": too_long})[0], 2)
        ok = "Your prompt is docs/prompts/cl24-orchestrator-guard-gaps.md: read it and do it."
        self.assertEqual(self.run_hook(cs, {"prompt": ok, "title": "CL24"})[0], 0)

    def test_title_does_not_bypass(self):
        """CL24 gap 2: only tags pass; a title saying review/clerk/orchestrator does not."""
        cs = "mcp__claude-code-remote__create_session"
        for title in ("CR-CL22 review", "Harmonize board clerk", "Harmonize orchestrator"):
            self.assertEqual(self.run_hook(cs, {"prompt": "Do CL22 inline...", "title": title})[0], 2, title)
        # CL30: nor does a tag any more, with an inline prompt (test_cl30_kit_scripts: what each tag's prompt must be)
        for tag in ("review", "board-clerk", "orchestrator"):
            self.assertEqual(self.run_hook(cs, {"prompt": "Do CL22 inline...", "tags": [tag]})[0], 2, tag)

    def test_attached_boards_reads(self):
        """CL24 gap 3: an attached board's repos/<id>/posts|comments is read through the kit like the host's."""
        ad = "ArtifactData"
        for coll in ("repos/board/posts", "repos/board/comments"):
            self.assertEqual(self.run_hook(ad, {"action": "list", "collection": coll})[0], 2, coll)
            self.assertEqual(self.run_hook(ad, {"action": "query", "collection": coll,
                                                "query": {"where": [["train", "==", "cli"]], "limit": 50}})[0], 2, coll)
            # a targeted lookup does not page through the board five at a time
            self.assertEqual(self.run_hook(ad, {"action": "query", "collection": coll, "query": {
                "where": [["created_at", ">", ""]], "limit": 5, "cursor": "c2"}})[0], 2, coll)
            # CL72: a targeted lookup or a get the kit did not print is refused too
            self.assertEqual(self.run_hook(ad, {"action": "query", "collection": coll,
                                                "query": {"where": [["post_id", "==", "x"]], "limit": 5}})[0], 2, coll)
            self.assertEqual(self.run_hook(ad, {"action": "get", "collection": coll, "doc_id": "x"})[0], 2, coll)
            pull = os.path.join(self.proj, ".kit", "run", "artifact", "in")
            self.pulled(("list", coll))
            self.assertEqual(self.run_hook(ad, {"action": "list", "collection": coll, "out_dir": pull})[0], 0, coll)
        # a malformed limit is a broad read, not a crash that lets it through
        self.assertEqual(self.run_hook(ad, {"action": "query", "collection": "posts",
                                            "query": {"where": [["a", "==", 1]], "limit": "five"}})[0], 2)

    def test_attached_board_step_post(self):
        """CL24 gap 4: a hand-written step post on an attached board is refused like the host's."""
        ad = "ArtifactData"
        rc, err = self.run_hook(ad, {"action": "set", "collection": "repos/board/posts", "doc_id": "cl24-x", "data": {}})
        self.assertEqual(rc, 2)
        self.assertIn("TaskCreate", err)
        # CL30: a card or a comment by hand too; the new session on _orchestrator has no kit form and passes
        for did in ("_orchestrator", "_trains", "_dashboard"):
            self.assertEqual(self.run_hook(ad, {"action": "set", "collection": "repos/board/posts", "doc_id": did,
                                                "data": {}})[0], 2, did)
        self.assertEqual(self.run_hook(ad, {"action": "update", "collection": "repos/board/posts", "doc_id": "_orchestrator",
                                            "data": {"session_id": "s"}})[0], 0)
        self.assertEqual(self.run_hook(ad, {"action": "set", "collection": "repos/board/comments", "doc_id": "c",
                                            "data": {}})[0], 2)

    def test_docstring_says_subagents_are_exempt(self):
        """CL24 gap 5: the exemption of the orchestrator's subagents is by design and written down."""
        doc = HOOK.read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("subagents are exempt", doc)
        self.assertIn("by design", doc)

    def test_its_subagents_pass(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, KIT_ROLE="orchestrator")
        data = {"tool_name": "ArtifactData", "tool_input": {"action": "list", "collection": "posts"}, "agent_id": "a1",
                "agent_type": "board-cards", "session_id": "s"}
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)

    def test_only_the_orchestrator(self):
        # CL30: the board rules hold every session (a part's raw dump is refused too); the launch rules stay the
        # orchestrator's, and it is still recognised without KIT_ROLE
        self.assertEqual(self.run_hook("ArtifactData", {"action": "list", "collection": "posts"}, role="part")[0], 2)
        cs, inline = "mcp__claude-code-remote__create_session", {"prompt": "Do CL9 inline, all of it."}
        self.assertEqual(self.run_hook(cs, inline, role="part")[0], 0)
        # no KIT_ROLE: recognised from its launch prompt (Harmonize's "you are the fiftieth orchestrator")
        rc, _ = self.run_hook(cs, inline, role="", prompt="Harmonize: you are the fiftieth orchestrator, after orchestrator 49.")
        self.assertEqual(rc, 2)
        rc, _ = self.run_hook(cs, inline, role="", prompt="Your prompt is docs/prompts/cl9.md: read it and do it.")
        self.assertEqual(rc, 0)


class LaunchRepo(Project):
    """CL26 item 3 (the owner, via orchestrator 51): a part's session must clone the repo that holds its step. CL25 and
    CL26 (train `cli`, on the attached board `board` = anlonlt/board) were launched with source_url anlonlt/harmonize
    and could not push their work. The guard refuses a create_session whose source_url is not the step's repo (its
    post on an attached board, in the local copy) or not a repo holding the named prompt file."""
    CS = "mcp__claude-code-remote__create_session"
    PROMPT = "Your prompt is docs/prompts/cl9-x.md: read it and do it."

    def setUp(self):
        super().setUp()
        json.dump({"project": "Harmonize", "kit": {"github": "anlonlt/harmonize"}},
                  open(os.path.join(self.proj, ".kit", "config.json"), "w"))
        os.makedirs(os.path.join(self.proj, "docs", "prompts"))
        os.environ.pop("KIT_ARTIFACT_DIR", None)
        self.art = os.path.join(self.proj, ".kit", "run", "artifact", "in")

    def attach_board(self, post_id=None):
        idx = os.path.join(self.art, "kit_repos", "_index.json")
        os.makedirs(os.path.dirname(idx))
        json.dump({"repos": [{"id": "board", "name": "board", "github": "anlonlt/board"}]}, open(idx, "w"))
        if post_id:
            pp = os.path.join(self.art, "repos", "board", "posts", post_id + ".json")
            os.makedirs(os.path.dirname(pp))
            json.dump({"id": post_id, "data": {"train": "cli", "part": "CL9"}, "version": 1}, open(pp, "w"))

    def launch(self, src):
        ti = {"prompt": self.PROMPT, "title": "CL9"}
        if src:
            ti["source_url"] = src
        return self.run_hook(self.CS, ti)

    def test_an_attached_clones_own_pull_and_push_pass(self):
        """O55 (2026-11-24): an orchestrator answering on the kit's board runs the kit from that board's clone (K1), so
        its pull and its push batch live under the clone's .kit/run/artifact: they are the kit's, not raw reads or hand
        writes. A path under a folder that is not a known clone stays refused."""
        self.attach_board()
        clone = os.path.join(os.path.dirname(self.proj), "board")
        os.makedirs(os.path.join(clone, ".git"), exist_ok=True)
        try:
            ad = "ArtifactData"
            pull = os.path.join(clone, ".kit", "run", "artifact", "in")
            self.pulled(("list", "repos/board/comments"), root=clone)
            self.assertEqual(self.run_hook(ad, {"action": "list", "collection": "repos/board/comments", "out_dir": pull})[0], 0)
            out = os.path.join(clone, ".kit", "run", "artifact", "out", "docs", "0000.json")
            batch = {"action": "batch", "writes": [{"op": "set", "collection": "repos/board/comments", "doc_id": "c",
                                                    "file_path": out}]}
            self.printed([batch], root=clone)
            self.assertEqual(self.run_hook(ad, batch)[0], 0)
            stray = os.path.join(tempfile.mkdtemp(), ".kit", "run", "artifact", "out", "docs", "0000.json")
            self.assertEqual(self.run_hook(ad, {"action": "batch", "writes": [
                {"op": "set", "collection": "repos/board/comments", "doc_id": "c", "file_path": stray}]})[0], 2)
        finally:
            import shutil
            shutil.rmtree(clone, ignore_errors=True)

    def test_a_step_on_an_attached_board_launches_from_that_repo(self):
        self.attach_board("cl9-x")
        rc, err = self.launch("https://github.com/anlonlt/harmonize")
        self.assertEqual(rc, 2, err)
        self.assertIn("anlonlt/board", err)
        self.assertEqual(self.launch("https://github.com/anlonlt/board")[0], 0)
        self.assertEqual(self.launch("anlonlt/board.git")[0], 0)

    def test_a_prompt_in_this_repo_launches_from_this_repo(self):
        open(os.path.join(self.proj, "docs", "prompts", "cl9-x.md"), "w").write("x\n")
        other = os.path.join(os.path.dirname(self.proj), "other")       # a clone next to it, without the prompt
        os.makedirs(os.path.join(other, ".git"), exist_ok=True)
        cfg = json.load(open(os.path.join(self.proj, ".kit", "config.json")))
        cfg["portfolio"] = [{"name": "other", "github": "anlonlt/other", "path": other}]
        json.dump(cfg, open(os.path.join(self.proj, ".kit", "config.json"), "w"))
        rc, err = self.launch("https://github.com/anlonlt/other")
        self.assertEqual(rc, 2, err)
        self.assertIn("docs/prompts/cl9-x.md", err)
        self.assertEqual(self.launch("https://github.com/anlonlt/harmonize")[0], 0)
        self.assertEqual(self.launch(None)[0], 0)

    def test_unknown_step_and_no_clone_passes(self):
        self.attach_board()
        self.assertEqual(self.launch("https://github.com/anlonlt/board")[0], 0)   # nothing here to check against


if __name__ == "__main__":
    unittest.main()
