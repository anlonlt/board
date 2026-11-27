"""kit.py artifact: the kit's scripts on a local copy of an Artifact board, carried across in batch files.

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req, kit  # noqa: E402
import subprocess  # noqa: E402


class ArtifactMode(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (self.tmp / "proj").mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.tmp / "proj")],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=60), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.art = self.tmp / "art"
        os.environ["KIT_ARTIFACT_DIR"] = str(self.art)
        self.addCleanup(os.environ.pop, "KIT_ARTIFACT_DIR", None)
        os.environ["KIT_ARTIFACT_PREFIX"] = ""        # a board of its own (the kit repo's config attaches it under repos/board/)
        self.addCleanup(os.environ.pop, "KIT_ARTIFACT_PREFIX", None)

    def remote(self, coll, did, data, where="in"):
        d = self.art / where / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def local(self, path, data=None):
        if data is None:
            return api_req(self.url, "doc/" + path, token=TOKEN)
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def push(self):
        return kit.artifact_push(url=self.url, token=TOKEN)

    def entries(self, r):
        return [e for p in r["batches"] for e in json.load(open(p))]

    def test_cycle(self):
        self.remote("posts", "P1", {"title": "Step one", "column": "info", "status": "planned"})
        self.remote("comments", "c1", {"post_id": "P1", "text": "go", "from": "owner"})
        r = kit.artifact_load(url=self.url, token=TOKEN)
        self.assertEqual(r["taken"], 2)
        self.assertEqual(self.local("posts/P1")["data"]["title"], "Step one")
        self.assertEqual(self.entries(self.push()), [])                         # nothing changed here

        # the scripts change the local copy: a step starts, a new step is planned
        self.local("posts/P1", {"title": "Step one", "column": "info", "status": "in progress"})
        self.local("posts/P2", {"title": "Step two", "column": "info", "status": "planned"})
        r = self.push()
        self.assertEqual(r["need_pin"], ["posts/P1"])                           # exists there, version unknown
        self.assertEqual([(e["doc_id"], "if_version" in e) for e in self.entries(r)], [("P2", False)])
        self.assertIn("artifact pin posts/P1=<N>", kit.artifact_push_text(r))

        self.remote("posts", "P1", {"title": "Step one", "column": "info", "status": "planned"}, where="pin/v3")  # CL31: the get
        kit.artifact_pin(["posts/P1=3"])
        r = self.push()
        got = {e["doc_id"]: e for e in self.entries(r)}
        self.assertEqual(got["P1"]["if_version"], 3)
        self.assertNotIn("if_version", got["P2"])
        self.assertEqual(json.load(open(got["P1"]["file_path"]))["status"], "in progress")
        for p in r["batches"]:
            kit.artifact_sent(p)
        self.assertEqual(kit._art_state()["versions"], {"posts/P1": 4, "posts/P2": 1})
        self.assertEqual(self.entries(self.push()), [])                         # all sent

        # both sides change P1 meanwhile: the owner sets a priority on the page, a script completes the step
        self.remote("posts", "P1", {"title": "Step one", "column": "info", "status": "in progress", "priority": 1})
        self.remote("posts", "P2", {"title": "Step two", "column": "info", "status": "planned"})
        self.local("posts/P1", {"title": "Step one", "column": "info", "status": "complete"})
        r = kit.artifact_load(url=self.url, token=TOKEN)
        self.assertEqual(r["merged"], 1)
        p1 = self.local("posts/P1")["data"]
        self.assertEqual((p1["status"], p1.get("priority")), ("complete", 1))   # both kept
        sent = {e["doc_id"]: e for e in self.entries(self.push())}
        self.assertEqual(list(sent), ["P1"])

    def test_cli(self):
        out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "kit.py"), "artifact", "pull"],
                             capture_output=True, text=True, env=dict(os.environ, CLAUDE_CODE_REMOTE="true")).stdout  # CL51: web only
        self.assertIn("`ArtifactData` `list`", out)
        self.assertIn("artifact load", out)


if __name__ == "__main__":
    unittest.main()
