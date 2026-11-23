"""CL20: `kit.py resume` reads the attached boards too (kit_repos/_index, repos/<id>/posts|comments).

The failing case (11-23 01:50, Harmonize): the host board had no open step, the attached kit board had parts in
progress with dead sessions and an unanswered comment of the owner; resume said "No train has an open step"."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "473dea4"   # scripts/kit.py before CL20 (main it was merged with): the control compares against it byte for byte


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db(path, colls):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT NULL"
                 " DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
    now = datetime.now(timezone.utc).isoformat()
    for coll, docs in colls.items():
        for did, d in docs.items():
            conn.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, 1, ?, ?)", (coll, did, json.dumps(d), now))
    conn.commit()
    conn.close()


def dump(d, coll, did, data):
    os.makedirs(os.path.join(d, coll), exist_ok=True)
    with open(os.path.join(d, coll, did + ".json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# the host: one train, all done (no open step)
HOST = {"posts": {"H1-a": {"title": "host step", "part": "H1", "train": "app", "column": "done", "created_at": "2026-11-01T00:00:00Z"}},
        "comments": {}}
# the attached board: one step in progress, its session dead; one comment of the owner, unanswered
ATT_POSTS = {"CL9-checks": {"title": "checks that must be able to fail", "part": "CL9", "train": "cli", "column": "info",
                            "status": "in progress", "session_id": "dead-cl9", "created_at": "2026-11-22T20:00:00Z",
                            "updated_at": "2026-11-22T21:00:00Z"}}
ATT_COMMENTS = {"4hrxp33h6wg7sng04f5k": {"post_id": "CL9-checks", "from": "owner", "text": "where is CL9?",
                                         "created_at": "2026-11-23T01:40:00Z"}}


class ResumeReadsAttachedBoards(unittest.TestCase):
    def setUp(self):
        self.kit = load("kit_cl20", os.path.join(ROOT, "scripts", "kit.py"))
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "host.db")
        make_db(self.db, HOST)
        self.art = os.path.join(self.tmp, "artifact")
        os.makedirs(os.path.join(self.art, "in"))
        self.env = mock.patch.dict(os.environ, {"KIT_ARTIFACT_DIR": self.art})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cfg(self, portfolio=None):
        c = {"project": "Harmonize", "fields": {"owner": "owner", "clerk": "clerk"}}
        if portfolio is not None:
            c["portfolio"] = portfolio
        return mock.patch.object(self.kit, "_kit_config", return_value=c)

    def check(self, out):
        self.assertNotIn("No train has an open step.\n\n## Attached", out)
        self.assertIn("## Attached board board", out)
        self.assertIn("### Train cli (0/1 done)", out)
        self.assertIn("- [~] CL9 `CL9-checks` checks that must be able to fail · in progress · session dead-cl9 (not running)", out)
        self.assertIn("relaunch", out)
        self.assertIn("### The owner's unanswered comments on board (answer in repos/board/comments)", out)
        self.assertIn("[repo board] 4hrxp33h6wg7sng04f5k on CL9-checks", out)
        self.assertIn('"board": "<id>"', out)

    def test_artifact_mode_local_copy(self):
        """kit_repos/_index and repos/board/posts|comments saved in the local copy's in/ folder (ArtifactData list)."""
        ind = os.path.join(self.art, "in")
        dump(ind, "kit_repos", "_index", {"repos": [{"id": "board", "name": "board", "github": "anlonlt/board"}]})
        for did, d in ATT_POSTS.items():
            dump(ind, "repos/board/posts", did, d)
        for did, d in ATT_COMMENTS.items():
            dump(ind, "repos/board/comments", did, d)
        with self.cfg():
            out = self.kit.resume(self.tmp, db_path=self.db, alive={"someone-else"})
        print("\n--- resume, Artifact mode ---\n" + out)
        self.check(out)

    def test_git_mode_clone(self):
        """A portfolio entry whose clone holds the board (its local board database)."""
        clone = os.path.join(self.tmp, "board")
        subprocess.run(["git", "init", "-q", clone], check=True)
        make_db(os.path.join(clone, ".kit", "board_data", "board.db"), {"posts": ATT_POSTS, "comments": ATT_COMMENTS})
        with self.cfg([{"name": "board", "github": "anlonlt/board", "path": clone}]):
            out = self.kit.resume(self.tmp, db_path=self.db, alive=set())
        print("\n--- resume, git mode ---\n" + out)
        self.check(out)
        self.assertIn("Read from its clone %s." % clone, out)

    def test_control_no_attached_board_byte_for_byte(self):
        """No attached board: the output is the one the kit printed before CL20, byte for byte."""
        try:
            src = subprocess.run(["git", "-C", ROOT, "show", "%s:scripts/kit.py" % BASE], capture_output=True,
                                 text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("commit %s not in this clone" % BASE)
        old_p = os.path.join(self.tmp, "kit_old.py")
        with open(old_p, "w", encoding="utf-8") as fh:
            fh.write(src)
        old = load("kit_cl20_old", old_p)
        old.ROOT, old.CONFIG = self.kit.ROOT, self.kit.CONFIG   # loaded from a temp file: same repo as the new one
        old.device_line = self.kit.device_line   # reads paths from its own file; not changed by CL20
        posts = {"P1-a": {"title": "one", "part": "P1", "train": "t", "column": "info", "status": "in progress",
                          "session_id": "s1", "created_at": "2099-01-01T00:00:00Z"},
                 "P2-b": {"title": "two", "part": "P2", "train": "t", "column": "info", "status": "planned",
                          "created_at": "2099-01-02T00:00:00Z"}}
        db2 = os.path.join(self.tmp, "host2.db")
        make_db(db2, {"posts": posts})
        for db in (self.db, db2):
            with self.cfg(), mock.patch.object(old, "_kit_config", return_value={"project": "Harmonize"}):
                a = old.resume(self.tmp, db_path=db, alive={"s1"})
                b = self.kit.resume(self.tmp, db_path=db, alive={"s1"})
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
