"""CL10 (the owner, 2026-11-23 02:22, 02:39, 02:45): every file a post names that the board's viewers open shows in
the Artifact. `kit.py artifact push` (the post tool) and `kit_artifact_sync.py files` list the uploads; `files-sent`
records them and writes the map (kit_docs/_index assets) and the mirrored documents; anything the store does not take
stays a repo link, said for the post."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNC = ROOT / "kit" / "claude" / "kit_artifact_sync.py"
sys.path.insert(0, str(ROOT / "kit" / "claude"))
import kit_artifact_sync as kas  # noqa: E402


def run(*args, env=None, ok=True):
    r = subprocess.run([sys.executable, "-I", str(SYNC)] + [str(a) for a in args], capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    if ok:
        assert r.returncode == 0, r.stderr
    return r.stdout if ok else r


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "docs" / "p").mkdir(parents=True)
        (self.repo / "docs" / "p" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        (self.repo / "docs" / "p" / "b.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        (self.repo / "docs" / "p" / "c.mp3").write_bytes(b"ID3")
        (self.repo / "docs" / "p" / "e.mov").write_bytes(b"\0")
        (self.repo / "docs" / "p" / "d.md").write_text("# D\n\ntext\n")
        (self.repo / "docs" / "p" / "f.docx").write_bytes(b"PK")
        with open(self.repo / "docs" / "p" / "big.png", "wb") as fh:       # 20 MiB + 1: over the store's cap
            fh.truncate(20 * 1024 * 1024 + 1)
        self.post = self.tmp / "post.json"
        self.post.write_text(json.dumps({"id": "P1", "data": {
            "images": ["docs/p/a.png", "docs/p/big.png"], "artifacts": ["docs/p/b.pdf", "nothing/here.png"],
            "body": "Read `docs/p/d.md § D`, hear `docs/p/c.mp3`, see [the clip](docs/p/e.mov), `docs/p/f.docx` and `kit.py`."}}))
        self.env = {"KIT_ARTIFACT_DIR": str(self.tmp / "art")}
        self.out = self.tmp / "out"

    def test_refs(self):
        refs = kas.post_refs(json.loads(self.post.read_text())["data"])
        self.assertEqual(refs[:4], ["docs/p/a.png", "docs/p/big.png", "docs/p/b.pdf", "nothing/here.png"])
        self.assertIn("docs/p/d.md", refs); self.assertIn("docs/p/e.mov", refs)

    def test_plan_upload_mirror_and_links(self):
        txt = run("files", self.post, "--repo", self.repo, "--out", self.out, "--into", "board", "--board", "https://claude.ai/artifact/X", env=self.env)
        plan = json.loads((self.out / "plan.json").read_text())
        self.assertEqual([u[0] for u in plan["upload"]], ["docs/p/a.png", "docs/p/b.pdf"])
        self.assertEqual(plan["mirror"], ["docs/p/d.md"])
        self.assertEqual(sorted(l[0] for l in plan["links"]), ["docs/p/big.png", "docs/p/c.mp3", "docs/p/e.mov", "docs/p/f.docx"])
        self.assertIn("`docs/p/f.docx` stays a repo link (the board's viewers do not open .docx files)", txt)
        self.assertIn("`asset: true`", txt); self.assertIn("files-sent", txt); self.assertIn("--into board", txt)
        self.assertIn("`docs/p/c.mp3` stays a repo link", txt)

    def test_sent_never_replaces_the_index(self):
        run("files", self.post, "--repo", self.repo, "--out", self.out, "--into", "board", env=self.env)
        (self.out / "uploaded.json").write_text(json.dumps({"docs/p/a.png": "/_blob/" + "a" * 32, "docs/p/b.pdf": "b" * 32}))
        # without the current index it writes nothing (O50's check: a set here wiped the Repo Docs list)
        r = run("files-sent", "--repo", self.repo, "--out", self.out, "--into", "board", "--kit", ROOT, env=self.env, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list(self.out.glob("batch-files-board-*.json")), [])
        # an index that holds the reader's list and another picture: merged into, pinned, never replaced
        idx = self.tmp / "index.json"
        idx.write_text(json.dumps({"docs": [{"path": "README.md"}], "total": 1, "assets": {"docs/old.png": "/_blob/" + "c" * 32}}))
        run("files-sent", "--repo", self.repo, "--out", self.out, "--into", "board", "--kit", ROOT,
            "--index", idx, "--index-version", 7, env=self.env)
        es = [e for f in sorted(self.out.glob("batch-files-board-*.json")) for e in json.loads(f.read_text())]
        e0 = es[0]
        self.assertEqual((e0["op"], e0["collection"], e0["doc_id"], e0["if_version"]), ("update", "repos/board/kit_docs", "_index", 7))
        data = json.loads(Path(e0["file_path"]).read_text())
        self.assertNotIn("docs", data); self.assertNotIn("total", data)        # an update leaves them as they are
        self.assertEqual(data["assets"], {"docs/old.png": "/_blob/" + "c" * 32, "docs/p/a.png": "/_blob/" + "a" * 32,
                                          "docs/p/b.pdf": "/_blob/" + "b" * 32})
        mirrored = {e["doc_id"]: json.loads(Path(e["file_path"]).read_text()) for e in es[1:]}
        self.assertEqual(mirrored[kas.doc_id("docs/p/d.md")]["kind"], "md")
        self.assertIn(kas.doc_id("docs/p/b.pdf"), mirrored)                 # the viewer's lookup finds the PDF
        again = run("files", self.post, "--repo", self.repo, "--out", self.out, "--into", "board", env=self.env)
        self.assertNotIn("to upload", again)                                 # recorded: nothing uploaded twice

    def test_a_missing_index_is_created(self):
        run("files", self.post, "--repo", self.repo, "--out", self.out, env=self.env)
        (self.out / "uploaded.json").write_text(json.dumps({"docs/p/a.png": "/_blob/" + "a" * 32}))
        run("files-sent", "--repo", self.repo, "--out", self.out, "--kit", ROOT, "--index-missing", env=self.env)
        e0 = json.loads(sorted(self.out.glob("batch-files-*.json"))[0].read_text())[0]
        self.assertEqual((e0["op"], e0["collection"]), ("set", "kit_docs"))


class PostTool(unittest.TestCase):
    """kit.py artifact push: the files its posts name come with it."""

    def test_push_lists_the_files(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_handoff import kit  # noqa: E402
        tmp = Path(tempfile.mkdtemp())
        os.environ["KIT_ARTIFACT_DIR"] = str(tmp / "art")
        self.addCleanup(os.environ.pop, "KIT_ARTIFACT_DIR", None)
        txt = kit._artifact_files([{"images": ["docs/cl10/a1-sections-default-phone.png"], "body": "x"}], str(tmp / "files"))
        self.assertIn("docs/cl10/a1-sections-default-phone.png", txt)
        self.assertIn("`asset: true`", txt)
        self.assertIn("Save what each call returns", kit.artifact_push_text({"board": "u", "need_pin": [], "batches": [], "files": txt}))


if __name__ == "__main__":
    unittest.main()
