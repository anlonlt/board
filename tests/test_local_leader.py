"""Tests for local-leader precedence, web-subordinate git-only mode, and Artifact sync on release.

Run via: python3 -m unittest tests.test_local_leader -v
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import temp_cleanup  # noqa: E402,F401


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LocalLeaderWorkflow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="kit-leader-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.m = os.path.join(self.d, "mirror")
        os.makedirs(os.path.join(self.m, "board", "posts"), exist_ok=True)
        os.makedirs(os.path.join(self.m, "board", "comments"), exist_ok=True)
        self.kit = load_mod("kit_leader_test", ROOT / "scripts" / "kit.py")
        self.kit._mirror_path = lambda: self.m
        self.kit._TEST_GUARD = None

    def test_leader_claim_status_release_and_ttl_expiry(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KIT_LOCAL_LEADER", None)
            self.assertFalse(self.kit.local_leader_active(self.m))
            claimed = self.kit.leader_claim(self.m, session_id="agy-orch-1", active_parts=2, push=False)
            self.assertTrue(claimed["active"])
            self.assertEqual(claimed["active_parts"], 2)
            st = self.kit.read_leader(self.m)
            self.assertTrue(st["active"])
            self.assertEqual(st["session_id"], "agy-orch-1")
            # Past TTL -> stale and inactive
            future = datetime.now(timezone.utc) + timedelta(minutes=st["ttl_minutes"] + 5)
            st_old = self.kit.read_leader(self.m, now=future)
            self.assertFalse(st_old["active"])
            self.assertTrue(st_old["stale"])
            # Explicit release -> inactive + artifact_sync_due
            rel = self.kit.leader_release(self.m, push=False)
            self.assertFalse(rel["active"])
            self.assertTrue(rel["artifact_sync_due"])
            self.assertFalse(self.kit.local_leader_active(self.m))

    def test_web_artifact_here_paused_while_local_leads_and_resumes_on_release(self):
        art = {"url": "https://claude.ai/artifact/TEST123", "prefix": "repos/board/"}
        self.kit.attached_board = lambda: dict(art)
        self.kit._rule = lambda name, cli=None: True if name == "artifact_steps" else False
        # Active local lease pauses Artifact writes on web
        self.kit.leader_claim(self.m, session_id="agy-1", push=False)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}, clear=False):
            os.environ.pop("KIT_LOCAL_LEADER", None)
            os.environ.pop("KIT_FORCE_ARTIFACT", None)
            self.assertIsNone(self.kit.artifact_here())
            # Explicit handoff override works
            os.environ["KIT_FORCE_ARTIFACT"] = "1"
            self.assertEqual(self.kit.artifact_here(), art)
            os.environ.pop("KIT_FORCE_ARTIFACT", None)
            # Releasing leader re-enables Artifact on web
            self.kit.leader_release(self.m, push=False)
            self.assertEqual(self.kit.artifact_here(), art)

    def test_web_resume_shows_subordinate_banner_when_local_leads(self):
        self.kit.leader_claim(self.m, session_id="agy-1", push=False)
        self.kit._load_board_docs = lambda m, db_path=None: ({}, {})
        self.kit._all_posts = lambda db_path=None: {}
        self.kit.resume = lambda *a, **kw: "# Resume\n"
        self.kit._attached_boards = lambda: ([], [])
        self.kit.stale_handover_lines = lambda p, c: []
        self.kit.alarm_lines = lambda: ([], [])
        self.kit.web_env_state = lambda p, c: ("", "")
        self.kit.land_lines = lambda p: []
        self.kit.git_copy_lines = lambda m, db_path=None: []
        self.kit.split_lines = lambda p: []
        self.kit.train_lines = lambda p: []
        self.kit.start_lines = lambda p, c, s: []
        self.kit.inherit_lines = lambda p: []
        self.kit.unreached_lines = lambda p: []
        self.kit.owner_lines = lambda: []
        self.kit.autonomy_lines = lambda: []
        self.kit._launch_workflow = lambda: []
        self.kit.audit_lines = lambda: []
        self.kit.lessons_for = lambda r: []
        self.kit.memory_line = lambda: ""
        self.kit._principle_lines = lambda r: []
        self.kit._refused_lines = lambda: []
        self.kit.memory_check_text = lambda: []
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}, clear=False):
            os.environ.pop("KIT_LOCAL_LEADER", None)
            txt = self.kit.resume_role(self.m, "orchestrator")
        self.assertIn("Subordinate mode: local orchestrator leads", txt)
        self.assertIn("Do NOT overwrite `_orchestrator.session_id`", txt)
        self.assertIn("Artifact", txt)

    def test_web_launch_refuses_local_runner_and_overlapping_local_step(self):
        self.kit.leader_claim(self.m, session_id="agy-1", push=False)
        local_post = {"part": "A9", "title": "Local step", "status": "planned", "runner": "agy", "task": "Edit `scripts/kit.py`."}
        running_local = {"part": "A8", "title": "Running local", "status": "in progress", "runner": "agy", "task": "Edit `scripts/kit.py`."}
        overlap_web = {"part": "W1", "title": "Overlap web", "status": "planned", "runner": "claude-web", "task": "Also edit `scripts/kit.py`."}
        disjoint_web = {"part": "W2", "title": "Disjoint web", "status": "planned", "runner": "claude-web", "task": "Edit `docs/unrelated.md`."}
        posts_map = {"a9": local_post, "w1": overlap_web, "w2": disjoint_web}
        self.kit._my_person = lambda u, t: (None, {})
        self.kit._board_request = lambda url, path, token=None, **kw: (
            {"exists": True, "data": posts_map[path.split("/")[-1]]}
            if path.startswith("doc/posts/") and path.split("/")[-1] in posts_map
            else {"exists": False}
        )
        self.kit._launch_posts = lambda p: {"a8": running_local, "w1": overlap_web, "w2": disjoint_web}
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}, clear=False):
            os.environ.pop("KIT_LOCAL_LEADER", None)
            r1 = self.kit.launch("a9", web=True, post_file=None)
            self.assertFalse(r1["ok"])
            self.assertIn("assigned to local runner 'agy'", r1["error"])
            r2 = self.kit.launch("w1", web=True, post_file=None)
            self.assertFalse(r2["ok"])
            self.assertIn("shares files", r2["error"])
            self.assertIn("scripts/kit.py", r2["error"])
            # Disjoint unclaimed web step passes the local-leader guard and reaches prompt sync
            self.kit.sync_step_prompt = lambda *a, **kw: {"error": "reached_prompt_sync"}
            r3 = self.kit.launch("w2", web=True, post_file=None)
            self.assertEqual(r3.get("error"), "reached_prompt_sync")

    def test_sync_db_no_cards_preserves_leader_cards(self):
        db = os.path.join(self.d, "board.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INT, data TEXT, updated_at TEXT, PRIMARY KEY (collection, doc_id))")
        conn.execute("CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT, doc_id TEXT, op TEXT, version INT, at TEXT)")
        leader_trains = {"title": "Trains", "rows": [{"part": "LOCAL"}]}
        web_trains = {"title": "Trains", "rows": [{"part": "WEB_STALE"}]}
        post_w = {"title": "Web step", "status": "in progress"}
        Path(self.m, "board", "posts", "_trains.json").write_text(json.dumps(leader_trains))
        conn.execute("INSERT INTO docs VALUES ('posts', '_trains', 1, ?, '2026-11-27T20:00:00Z')", (json.dumps(web_trains),))
        conn.execute("INSERT INTO docs VALUES ('posts', 'w1', 1, ?, '2026-11-27T20:00:00Z')", (json.dumps(post_w),))
        conn.commit()
        conn.close()
        self.kit.sync_db(self.m, db_path=db, at="2026-11-27T20:00:00Z", full=True, no_cards=True)
        # Leader's _trains untouched; web post w1 written
        self.assertEqual(json.loads(Path(self.m, "board", "posts", "_trains.json").read_text()), leader_trains)
        self.assertEqual(json.loads(Path(self.m, "board", "posts", "w1.json").read_text()), post_w)

    def test_web_takeover_owns_board_allows_git_push_when_local_leads(self):
        wt = load_mod("wt_leader_test", ROOT / "kit" / "web_takeover.py")
        wt.ROOT = self.d
        wt.KIT = os.path.join(self.d, ".kit")
        os.makedirs(wt.KIT, exist_ok=True)
        Path(wt.KIT, "config.json").write_text(json.dumps({
            "kit": {"artifact": {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}}
        }))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true", "KIT_LOCAL_LEADER": "0"}, clear=False):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            os.environ.pop("KIT_ROLE", None)
            os.environ.pop("KIT_NO_BOARD_PUSH", None)
            os.environ.pop("KIT_ATTACHED_CLONE", None)
            self.assertFalse(wt.owns_board()[0])
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true", "KIT_LOCAL_LEADER": "1"}, clear=False):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            self.assertTrue(wt.owns_board()[0])


if __name__ == "__main__":
    unittest.main()
