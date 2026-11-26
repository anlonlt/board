"""CL45: a web session reconciles its Artifact with GitHub (origin/board-mirror) at its start, both ways.

The case: the computer's board (git) and the web's Artifact each gain posts the other lacks (the memory train's M5 to
M8 were on git only, a web session called the git copy out of date). `kit.py artifact reconcile`, after the pull:
each side gets what it lacks, a document changed on one side since the last reconcile takes that side, and one changed
on BOTH sides is never overwritten silently: the newer copy shows, the other is kept whole in a conflict comment.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, KIT, ROOT, TOKEN, Replica, api_req, git, kit  # noqa: E402


class ArtifactReconcile(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = root
        self.remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "board-mirror", str(self.remote))
        seed = root / "seed"
        git(root, "clone", "-q", str(self.remote), str(seed))
        git(seed, "-c", "user.name=seed", "-c", "user.email=seed@example.invalid", "commit", "-q", "--allow-empty", "-m", "Empty")
        git(seed, "push", "-q", "origin", "HEAD:board-mirror")
        self.computer = Replica(root / "computer", self.remote, stack)
        # the web session: its own board server (the local copy of the Artifact) and its clone of board-mirror
        (root / "web").mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(root / "web")],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=60), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.mirror = root / "web-mirror"
        git(root, "clone", "-q", "-b", "board-mirror", str(self.remote), str(self.mirror))
        git(self.mirror, "config", "user.name", "web")
        git(self.mirror, "config", "user.email", "web@example.invalid")
        self.art = root / "art"
        for k, v in (("KIT_ARTIFACT_DIR", str(self.art)), ("KIT_ARTIFACT_PREFIX", ""),
                     ("KIT_ARTIFACT_URL", "https://claude.ai/artifact/test-board")):
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    # the Artifact, as the pull's ArtifactData calls save it
    def artifact(self, coll, did, data):
        d = self.art / "in" / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def web(self, path):
        return api_req(self.url, "doc/" + path, token=TOKEN)

    def reconcile(self):
        return kit.artifact_reconcile(str(self.mirror), url=self.url, token=TOKEN, branch="board-mirror")

    def sent_all(self, r):
        """The ArtifactData batches go out (what the Artifact then holds), recorded with `artifact sent`."""
        docs = {}
        for p in r["push"]["batches"]:
            for e in json.loads(Path(p).read_text()):
                docs["%s/%s" % (e["collection"], e["doc_id"])] = (e, json.loads(Path(e["file_path"]).read_text()))
            kit.artifact_sent(p)
        return docs

    def git_docs(self):
        check = self.root / ("check%d" % len(list(self.root.glob("check*"))))
        git(self.root, "clone", "-q", "-b", "board-mirror", str(self.remote), str(check))
        out = {}
        for coll in ("posts", "comments"):
            for p in (check / "board" / coll).glob("*.json"):
                out["%s/%s" % (coll, p.stem)] = json.loads(p.read_text())
        return out

    def test_each_side_gets_what_it_lacks_and_a_both_sides_change_is_kept_twice(self):
        c = self.computer
        p0 = {"title": "Shared", "column": "info", "updated_at": "2026-11-26T10:00:00Z"}
        both = {"title": "Both", "column": "info", "body": "the original", "updated_at": "2026-11-26T10:00:00Z"}
        c.write("posts/P0", p0)
        c.write("posts/Pboth", both)
        c.publish()
        self.artifact("posts", "P0", p0)
        self.artifact("posts", "Pboth", both)
        r = self.reconcile()                                   # the first reconcile: both hold the same, the base
        self.assertEqual((r["from_git"], r["conflicts"]), ([], []), r)
        base = json.loads(git(self.mirror, "show", "origin/board-mirror:board/artifact-reconciled.json"))["docs"]
        self.assertEqual(set(base), {"posts/P0", "posts/Pboth"})

        # meanwhile: the computer adds M5 and edits Pboth and P0; the web adds W1 and edits Pboth (later)
        c.read_in()
        c.write("posts/M5", {"title": "Made on the computer", "column": "info", "updated_at": "2026-11-26T11:00:00Z"})
        c.write("posts/Pboth", dict(both, body="the computer's edit", updated_at="2026-11-26T11:00:00Z"))
        c.write("posts/P0", dict(p0, title="Shared, renamed on the computer", updated_at="2026-11-26T09:00:00Z"))
        c.publish()
        self.artifact("posts", "W1", {"title": "Made on the web", "column": "info", "updated_at": "2026-11-26T11:30:00Z"})
        self.artifact("posts", "Pboth", dict(both, body="the web's edit", updated_at="2026-11-26T12:00:00Z"))

        r = self.reconcile()
        line = kit.artifact_reconcile_line(r)
        self.assertIn("posts/M5", r["from_git"])
        self.assertIn("posts/P0", r["from_git"])          # changed on git only: git wins although its date is older
        self.assertIn("posts/W1", r["to_git"])
        self.assertEqual(r["conflicts"], ["posts/Pboth"], line)
        self.assertIn("1 conflict(s)", line)
        self.assertIn("from GitHub", line)

        # the board here: both sides' posts, the newer copy of Pboth, the other one kept in a comment under it
        self.assertEqual(self.web("posts/M5")["data"]["title"], "Made on the computer")
        self.assertEqual(self.web("posts/P0")["data"]["title"], "Shared, renamed on the computer")
        self.assertEqual(self.web("posts/Pboth")["data"]["body"], "the web's edit")
        conflicts = [d for d in api_req(self.url, "db/comments", token=TOKEN)["docs"]
                     if (d["data"] or {}).get("kind") == "conflict"]
        self.assertEqual(len(conflicts), 1)
        cdoc = conflicts[0]["data"]
        self.assertEqual(cdoc["post_id"], "Pboth")
        self.assertEqual(cdoc["conflict"]["other"]["body"], "the computer's edit")
        self.assertIn("the computer's edit", cdoc["text"])

        # GitHub: the web's post, the newer Pboth, the conflict comment; nothing of the computer's lost
        g = self.git_docs()
        self.assertEqual(g["posts/W1"]["title"], "Made on the web")
        self.assertEqual(g["posts/M5"]["title"], "Made on the computer")
        self.assertEqual(g["posts/Pboth"]["body"], "the web's edit")
        self.assertIn("comments/" + conflicts[0]["id"], g)

        # the Artifact: the batches carry M5 (a create), P0 (git's edit) and the conflict comment, never W1 or Pboth
        out = r["push"]
        self.assertIn("posts/P0", out["need_pin"])         # exists there: its version is fetched first
        sent = self.sent_all(r)
        self.assertIn("posts/M5", sent)
        self.assertNotIn("if_version", sent["posts/M5"][0])
        self.assertIn("comments/" + conflicts[0]["id"], sent)
        self.assertNotIn("posts/W1", sent)
        self.assertNotIn("posts/Pboth", sent)

        # a second run (the Artifact now holds what was sent): nothing new, the conflict is not doubled
        self.artifact("posts", "M5", sent["posts/M5"][1])
        self.artifact("comments", conflicts[0]["id"], cdoc)
        r2 = self.reconcile()
        self.assertEqual((r2["from_git"], r2["conflicts"]), ([], []), kit.artifact_reconcile_line(r2))
        self.assertEqual(len([d for d in api_req(self.url, "db/comments", token=TOKEN)["docs"]
                              if (d["data"] or {}).get("kind") == "conflict"]), 1)

    def test_a_post_only_git_held_before_the_load_reaches_the_artifact(self):
        """The bug's own shape: a git seed put M6 in the local copy before the load; load marked it never there and
        push never sent it. The reconcile sends it."""
        c = self.computer
        c.write("posts/M6", {"title": "On git only", "column": "info", "updated_at": "2026-11-26T11:00:00Z"})
        c.publish()
        api_req(self.url, "doc/posts/M6", {"data": self.git_docs()["posts/M6"]}, TOKEN, "PUT")   # the git seed
        self.artifact("posts", "A1", {"title": "On the Artifact", "column": "info"})
        r = self.reconcile()
        entries = [e for p in r["push"]["batches"] for e in json.loads(Path(p).read_text())]
        self.assertIn(("posts", "M6"), [(e["collection"], e["doc_id"]) for e in entries])
        self.assertIn("posts/A1", r["to_git"])

    def test_cli_refuses_a_local_session(self):
        env = dict(os.environ)
        for k in ("CLAUDE_CODE_REMOTE", "KIT_ARTIFACT_RECONCILE_LOCAL"):
            env.pop(k, None)
        out = subprocess.run([sys.executable, "-I", str(KIT), "--mirror", str(self.mirror), "artifact", "reconcile",
                              "--board-url", self.url, "--token", TOKEN], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("not in a local session", out.stdout)
        self.assertFalse((self.art / "state.json").exists())     # nothing loaded, nothing reconciled


if __name__ == "__main__":
    unittest.main()
