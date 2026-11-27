#!/usr/bin/env python3
"""CL51: a session on a computer never writes to the Artifact.

  1. `kit.py done` checks the Artifact only on the web; on a computer it checks the local board and the git copy.
  2. No Artifact step on a computer: the STALE gate, launch's batch, the `artifact` command, the task-train hook's push.
  3. `artifact load` never writes an older copy over a newer one (the 20:23Z overwrite of 11 posts of the local board).
python3 -m unittest tests.test_cl51_computer_artifact -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "scripts", "kit.py")
HOOK = os.path.join(ROOT, "kit", "hooks", "kit_task_train.py")
ART = {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}
WEB = {"CLAUDE_CODE_REMOTE": "true"}


def load_kit():
    spec = importlib.util.spec_from_file_location("kit_cl51", KIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def computer():
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_REMOTE", "KIT_STALE_OK", "KIT_ARTIFACT_ON_COMPUTER")}
    return mock.patch.dict(os.environ, env, clear=True)


def no_artifact(*a, **k):
    raise AssertionError("an Artifact call on a computer")


def kit_for_done(post, d):
    kit = load_kit()
    kit._get_post = lambda pid, db_path=None: post
    kit.send_form = lambda pid: []
    kit.attached_board = lambda: dict(ART)                  # the config names an Artifact, as on CL44's computer
    kit.artifact_url = lambda: ART["url"]
    kit.check_asked = lambda p, db_path=None: ([], [])
    kit.check_his_words = lambda pid, p, db_path=None: ([], [])
    kit.check_done = lambda p, root=None, body_max=1500: ([], [])
    kit.check_intent = lambda p, root=None: []        # CL76: its own tests (test_intent_first)
    kit.plain_notes = lambda p: []
    kit.check_real_project = lambda p, root=None: []
    kit.check_trunk_merged = lambda root=None: []
    kit.prove_check = lambda p, root=None, timeout=900: ([], [])
    kit._post_gate = lambda: None
    kit._art_dir = lambda: os.path.join(d, "art")
    kit.artifact_push = kit.artifact_pull = kit.artifact_load = no_artifact
    kit._mirror_path = lambda: os.path.join(d, "mirror")
    return kit


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(doc, fh)


class DoneOnAComputer(unittest.TestCase):
    """CL44's part, on a computer, was told by done to push to the Artifact ("the post is not on the board yet")."""

    def test_local_done_passes_without_any_artifact_call(self):
        post = {"title": "x", "status": "complete", "verdict": "v", "updated_at": "2026-11-26T20:00:00Z"}
        with tempfile.TemporaryDirectory() as d, computer():
            kit = kit_for_done(post, d)
            write(os.path.join(d, "mirror", "board", "posts", "p1.json"), post)      # in the git copy
            rc, out = kit.done("p1")
            self.assertEqual(rc, 0, out)
            self.assertNotIn("not on the board yet", out)
            self.assertNotIn("artifact push", out)

    def test_local_done_keeps_the_post_in_git_when_the_copy_is_behind(self):
        post = {"title": "x", "status": "complete", "verdict": "v"}
        with tempfile.TemporaryDirectory() as d, computer():
            kit = kit_for_done(post, d)
            f = os.path.join(d, "mirror", "board", "posts", "p1.json")
            write(f, dict(post, status="planned"))
            calls = []
            kit.keep = lambda m, db_path=None: (calls.append(m), write(f, post), ("git copy: kept", True))[2]
            rc, out = kit.done("p1")
            self.assertEqual((rc, len(calls)), (0, 1), out)

    def test_local_done_refuses_a_post_git_has_not_got(self):
        post = {"title": "x", "status": "complete", "verdict": "v"}
        with tempfile.TemporaryDirectory() as d, computer():
            kit = kit_for_done(post, d)
            write(os.path.join(d, "mirror", "board", "posts", "p1.json"), dict(post, status="planned"))
            kit.keep = lambda m, db_path=None: ("git copy: NOT KEPT (no remote)", False)
            rc, out = kit.done("p1")
            self.assertEqual(rc, 1)
            self.assertIn("not in the board's git copy", out)
            self.assertNotIn("Artifact", out.split("NOT READY")[1].split("note:")[0])

    def test_web_done_still_asks_for_the_push(self):
        post = {"title": "x", "status": "complete", "verdict": "v"}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, WEB):
            kit = kit_for_done(post, d)
            rc, out = kit.done("p1")
            self.assertEqual(rc, 1)
            self.assertIn("not on the board yet", out)
            self.assertIn("artifact sync", out)                    # CL72: the one door


class NoArtifactStepOnAComputer(unittest.TestCase):

    def test_stale_gate_never_holds_a_computer(self):
        kit = load_kit()
        kit.attached_board = lambda: dict(ART)
        kit._art_state = lambda: {}                           # never loaded: on the web this is STALE
        with computer():
            self.assertEqual(kit.stale_copy(), "")
        with mock.patch.dict(os.environ, WEB):
            os.environ.pop("KIT_STALE_OK", None)
            self.assertIn("STALE", kit.stale_copy())

    def test_artifact_here_is_none_on_a_computer(self):
        kit = load_kit()
        kit.attached_board = lambda: dict(ART)
        with computer():
            self.assertIsNone(kit.artifact_here())
            self.assertFalse(kit._LocalBoard().artifact)
        with mock.patch.dict(os.environ, WEB):
            self.assertEqual(kit.artifact_here(), ART)

    def test_the_artifact_command_refuses_on_a_computer(self):
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_REMOTE", "KIT_ARTIFACT_ON_COMPUTER")}
        for action in ("pull", "load", "push"):
            r = subprocess.run([sys.executable, "-I", KIT, "artifact", action], capture_output=True, text=True, env=env,
                               cwd=ROOT, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("not on a computer", r.stdout)

    def test_launch_and_adopt_send_nothing_from_a_computer(self):
        with open(KIT, encoding="utf-8") as fh:
            src = fh.read()
        for marker in ('out["trains"] = tr is not None\n    if artifact_here():', 'out = {"trains": plan, "written": n}\n    if artifact_here():'):
            self.assertIn(marker, src)

    def test_task_train_hook_asks_no_push_on_a_computer(self):
        sys.path.insert(0, os.path.dirname(HOOK))
        try:
            spec = importlib.util.spec_from_file_location("kit_task_train_cl51", HOOK)
            hook = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(hook)
        finally:
            sys.path.pop(0)
        with computer():
            self.assertFalse(hook.on_web())
        with mock.patch.dict(os.environ, WEB):
            self.assertTrue(hook.on_web())


# the 20:23Z overwrite, reduced: the local board's newer posts and the Artifact's older copies
LOCAL = {
    "cl43": {"title": "CL43", "status": "complete", "column": "done", "verdict": "shipped", "updated_at": "2026-11-26T19:40:00Z"},
    "cl32": {"title": "CL32", "status": "complete", "verdict": "ok", "updated_at": "2026-11-26T18:00:00Z"},
    "cl16": {"title": "CL16", "status": "planned", "train": "board-data", "updated_at": "2026-11-26T17:00:00Z"},
    "_prefs": {"lang": "en", "updated_at": "2026-11-26T16:00:00Z"},
}
ARTIFACT = {
    "cl43": {"title": "CL43", "status": "planned", "column": "info", "updated_at": "2026-11-25T10:00:00Z", "only_there": 1},
    "cl32": {"title": "CL32", "status": "in progress", "updated_at": "2026-11-25T10:00:00Z"},
    "cl16": {"title": "CL16", "status": "planned", "train": "old-train", "updated_at": "2026-11-24T10:00:00Z"},
    "_prefs": {"lang": "fr", "updated_at": "2026-11-20T10:00:00Z"},
    "cl99": {"title": "new there", "status": "planned", "updated_at": "2026-11-26T21:00:00Z"},
}


class LoadKeepsTheNewerCopy(unittest.TestCase):

    def test_keep_newer_rules(self):
        kit = load_kit()
        out = kit.keep_newer(ARTIFACT["cl43"], LOCAL["cl43"])
        self.assertEqual((out["status"], out["column"], out["verdict"]), ("complete", "done", "shipped"))
        self.assertEqual(out["only_there"], 1)                  # a field this copy lacks is taken
        newer_there = dict(LOCAL["cl32"], status="in progress", updated_at="2026-11-27T00:00:00Z", note="n")
        out = kit.keep_newer(newer_there, LOCAL["cl32"])
        self.assertEqual((out["note"], out["status"]), ("n", "complete"))   # newer there, but a status never lowered

    def test_an_older_artifact_loaded_over_a_newer_local_board_changes_nothing(self):
        kit = load_kit()
        board = {k: json.loads(json.dumps(v)) for k, v in LOCAL.items()}
        writes = []

        def req(url, path, body=None, token=None, method=None):
            if path.startswith("doc/posts/"):
                did = path.split("/")[-1]
                return {"exists": did in board, "data": board.get(did), "version": 1}
            if path == "db/batch":
                for w in body["writes"]:
                    writes.append(w["doc_id"])
                    board[w["doc_id"]] = w["data"]
                return {}
            if path.startswith("db/"):
                return {"docs": [{"id": k, "data": v} for k, v in board.items()] if path.startswith("db/posts") else []}
            return {}

        with tempfile.TemporaryDirectory() as d:
            kit._ensure_local_board = lambda url: True
            kit._resolve_board_token = lambda t: "t"
            kit._board_request = req
            kit._art_dir = lambda: os.path.join(d, "art")
            kit._load_blobs = lambda url, token, in_dir: (0, [])
            kit._load_attached = lambda url, token, in_dir: {}
            in_dir = os.path.join(d, "in")
            for did, doc in ARTIFACT.items():
                write(os.path.join(in_dir, "repos", "board", "posts", did + ".json"), doc)
            kit._art_prefix = lambda: "repos/board/"
            kit.artifact_load(url="http://127.0.0.1:1", in_dir=in_dir)
        for did, doc in LOCAL.items():
            for k, v in doc.items():
                self.assertEqual(board[did][k], v, "%s.%s went back" % (did, k))
        self.assertEqual(board["cl99"]["title"], "new there")       # a post only the Artifact has still comes in
        self.assertNotIn("cl32", writes)                             # nothing to write: the local copy stands


if __name__ == "__main__":
    unittest.main()
