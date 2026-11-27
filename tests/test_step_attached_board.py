"""CL25 (scope 2): `kit.py step new` from a host project can put a step on an attached board's train.

Harmonize's orchestrator (its own board at prefix "") could not plan a `cli` step the kit way: the cli train lives on
the attached kit board, repos/board/posts in the same Artifact, and `step new` only wrote the host's `posts`. Now
`--board <id>` (or the train's board, found from its existing posts) writes the post as a file under
.kit/run/artifact/out/repos/<id>/posts/ and prints the one ArtifactData set that sends it: the orchestrator guard lets
that file through and still refuses a step post written by hand.

Run via: python3 -m unittest tests.test_step_attached_board -v
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db(path, posts):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT NULL"
                 " DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
    now = datetime.now(timezone.utc).isoformat()
    for pid, p in posts.items():
        conn.execute("INSERT OR REPLACE INTO docs VALUES ('posts', ?, 1, ?, ?)", (pid, json.dumps(p), now))
    conn.commit()
    conn.close()


class StepOnAttachedBoard(unittest.TestCase):
    def setUp(self):
        self.kit = load("kit_step_att", os.path.join(ROOT, "scripts", "kit.py"))
        self.guard = load("kit_guard_att", os.path.join(ROOT, "kit", "hooks", "kit_orchestrator_guard.py"))
        self.proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.proj, True)
        os.makedirs(os.path.join(self.proj, ".kit", "board_data"))
        os.makedirs(os.path.join(self.proj, ".kit", "claude-cli"))
        self.db = os.path.join(self.proj, ".kit", "board_data", "board.db")
        make_db(self.db, {"H1-a": {"title": "host step", "part": "H1", "train": "app", "created_at": "2026-11-01"}})
        with open(os.path.join(ROOT, "kit", "claude-cli", "part.template.md")) as src, \
                open(os.path.join(self.proj, ".kit", "claude-cli", "part.md"), "w") as dst:
            dst.write(src.read().replace("{{PROJECT}}", "Host").replace("{{SLUG}}", "host").replace("{{TALK}}", "English"))
        self.log = os.path.join(self.proj, "calls.jsonl")
        with open(os.path.join(self.proj, ".kit", "board_cli.py"), "w") as fh:   # records any host board write
            fh.write("import json,sys\nopen(%r,'a').write(json.dumps(sys.argv[1:])+'\\n')\n" % self.log)
        with open(os.path.join(self.proj, ".kit", "config.json"), "w") as fh:
            json.dump({"project": "Host", "board_url": "https://claude.ai/artifact/ABC", "kit": {}}, fh)
        att = os.path.join(self.proj, ".kit", "run", "artifact", "in", "repos", "board", "posts")
        os.makedirs(att)
        with open(os.path.join(att, "cl24-guard.json"), "w") as fh:
            json.dump({"title": "guard", "part": "CL24", "train": "cli", "created_at": "2026-11-22"}, fh)
        self.old = (self.kit.ROOT, os.environ.get("KIT_ARTIFACT_DIR"))
        self.kit.ROOT = self.proj
        self.kit.configure(os.path.join(self.proj, ".kit", "config.json"))
        os.environ.pop("KIT_ARTIFACT_DIR", None)

    def tearDown(self):
        self.kit.ROOT = self.old[0]
        if self.old[1] is not None:
            os.environ["KIT_ARTIFACT_DIR"] = self.old[1]

    def host_writes(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def check_attached(self, res, part):
        self.assertFalse(res.get("error"), res)
        pid = res["post"]
        self.assertEqual(res.get("board"), "board")
        f = os.path.join(self.proj, ".kit", "run", "artifact", "out", "repos", "board", "posts", pid + ".json")
        doc = json.load(open(f))
        self.assertEqual((doc["train"], doc["part"], doc["status"]), ("cli", part, "planned"))
        w = res["artifact_write"]
        self.assertEqual((w["action"], w["collection"], w["doc_id"], w["file_path"]), ("set", "repos/board/posts", pid, f))
        self.assertEqual(self.host_writes(), [], "nothing written on the host's own posts")
        # CL54: the post only; the launch generates the prompt from it (in the board's clone)
        self.assertFalse(os.path.exists(os.path.join(self.proj, res["prompt"])))
        # the orchestrator guard lets this kit-made file through, and still refuses the same post written by hand
        self.assertIsNone(self.guard.verdict({"tool_name": "ArtifactData", "tool_input": w}, self.proj))
        hand = {"action": "set", "url": "u", "collection": "repos/board/posts", "doc_id": pid, "data": doc}
        self.assertIn("by hand", self.guard.verdict({"tool_name": "ArtifactData", "tool_input": hand}, self.proj))
        return pid

    def test_board_named(self):
        res = self.kit.step_new("cli", "CL26", "CL26 a new kit step", task="do it", board="board", db_path=self.db)
        self.check_attached(res, "CL26")

    def test_board_found_from_the_trains_posts(self):
        res = self.kit.step_new("cli", "CL27", "another", db_path=self.db)
        self.check_attached(res, "CL27")

    def test_adopts_the_attached_boards_step(self):
        res = self.kit.step_new("cli", "CL24", "guard", db_path=self.db)
        self.assertEqual((res.get("post"), res.get("adopted"), res.get("board")), ("cl24-guard", True, "board"))
        self.assertEqual(self.host_writes(), [])

    def test_host_train_stays_on_the_host(self):
        res = self.kit.step_new("app", "H2", "next host step", db_path=self.db)
        self.assertNotIn("board", res)
        self.assertEqual(len(self.host_writes()), 1)
        self.assertEqual(json.loads(self.host_writes()[0])[:3], ["set", "posts", res["post"]])


if __name__ == "__main__":
    unittest.main()
