#!/usr/bin/env python3
"""CL30, the owner's item (10:3x, via O52) and Harmonize's parity check (docs/BOARD-PARITY.md): every file a post
names opens on the Artifact board as it does on a local board, and the other way round.

On the Artifact the viewers read a mirrored document from <prefix>kit_docs/<docId(path)> and a picture from
<prefix>kit_docs/_index.assets {path: /_blob/<id>} (kit/claude/artifact_adapter.js, artifact_ui.js); on a local board
from the repo itself and the local blob store (/_blob/<id>). Before (kit main 0a45d26):
  1. `artifact push` planned the files of the posts it was sending only: an unchanged post never got its files
     (Harmonize: 130 of 196 posts name repo files, its kit_docs empty);
  2. a document was mirrored only by a second command (`kit_artifact_sync.py files-sent`), not by the push;
  3. on an attached board the steps named the host's kit_docs/_index and no --into;
  4. a local copy loaded from the Artifact had none of its pictures (/_blob 404);
  5. the files state lived in .kit/run/ (not in git), so a new container mirrored everything again without versions;
  6. a § embed whose document is not there vanished without a word.
python3 -m unittest tests.test_cl30_files -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req  # noqa: E402

ART = "https://claude.ai/artifact/X"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def load_kit(root):
    spec = importlib.util.spec_from_file_location("kit_cl30_files_%d" % id(root), str(ROOT / "scripts" / "kit.py"))
    k = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k)
    k.ROOT = str(root)
    return k


class Base(unittest.TestCase):
    prefix = ""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.repo = self.tmp / "repo"
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "pics").mkdir()
        (self.repo / ".kit").mkdir()
        (self.repo / "docs" / "a.md").write_text("# A\n\n## Part one\nhello\n")
        (self.repo / "pics" / "p.png").write_bytes(PNG)
        (self.repo / "board").mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.repo / "board")],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=5), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.art = self.tmp / "art"
        saved = {k: os.environ.get(k) for k in ("KIT_ARTIFACT_DIR", "KIT_ARTIFACT_URL", "KIT_ARTIFACT_PREFIX",
                                                "KIT_ARTIFACT_FILES_DIR")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        os.environ.update(KIT_ARTIFACT_DIR=str(self.art), KIT_ARTIFACT_URL=ART, KIT_ARTIFACT_PREFIX=self.prefix)
        os.environ.pop("KIT_ARTIFACT_FILES_DIR", None)
        self.k = load_kit(self.repo)

    def remote(self, coll, did, data):
        d = self.art / "in" / Path(*(self.prefix + coll).split("/"))
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def entries(self, r):
        return [e for p in r["batches"] for e in json.load(open(p))]

    def loaded_post(self, **extra):
        post = dict({"title": "Old step", "column": "info", "body": "See `docs/a.md § Part one` and the picture.",
                     "images": ["pics/p.png"]}, **extra)
        self.remote("posts", "P1", post)
        self.k.artifact_load(url=self.url, token=TOKEN)
        return post


class PushSendsEveryFile(Base):
    def test_an_unchanged_post_gets_its_files(self):
        self.loaded_post()
        r = self.k.artifact_push(url=self.url, token=TOKEN)
        es = self.entries(r)
        self.assertEqual([e["doc_id"] for e in es if e["collection"].endswith("posts")], [])      # nothing changed
        mirror = [e for e in es if e["collection"] == self.prefix + "kit_docs"]
        self.assertEqual([e["doc_id"] for e in mirror], ["docs+2F~a.md"])                        # 2. part of the push
        doc = json.load(open(mirror[0]["file_path"]))
        self.assertIn("Part one", doc["content"])
        self.assertIn("pics/p.png", r["files"])                                                   # 1. the picture: upload
        self.assertIn("asset: true", r["files"])

    def test_sent_once_then_only_when_the_file_changes(self):
        self.loaded_post()
        r = self.k.artifact_push(url=self.url, token=TOKEN)
        for b in r["batches"]:
            self.k.artifact_sent(b)
        self.assertEqual([e for e in self.entries(self.k.artifact_push(url=self.url, token=TOKEN))
                          if e["collection"].endswith("kit_docs")], [])
        (self.repo / "docs" / "a.md").write_text("# A\n\n## Part one\nchanged\n")
        again = [e for e in self.entries(self.k.artifact_push(url=self.url, token=TOKEN)) if e["collection"].endswith("kit_docs")]
        self.assertEqual([(e["doc_id"], e.get("if_version")) for e in again], [("docs+2F~a.md", 1)])

    def test_the_files_state_is_kept_in_git_not_in_run(self):
        os.environ.pop("KIT_ARTIFACT_DIR")
        self.k = load_kit(self.repo)
        kas = self.k._kas()
        p = kas.files_state_path(str(self.repo), "")
        self.assertNotIn(os.sep + "run" + os.sep, p)
        self.assertTrue(p.startswith(str(self.repo / ".kit")))


class AttachedBoard(Base):
    prefix = "repos/board/"

    def test_the_steps_name_the_attached_board(self):
        self.loaded_post()
        r = self.k.artifact_push(url=self.url, token=TOKEN)
        self.assertIn('"repos/board/kit_docs"', r["files"])
        self.assertIn("--into board", r["files"])
        self.assertTrue(any(e["collection"] == "repos/board/kit_docs" for e in self.entries(r)))


class LocalCopyGetsThePictures(Base):
    A, B, C = "a" * 32, "b" * 32, "c" * 32

    def blob(self, bid):
        try:
            with urllib.request.urlopen(self.url + "/_blob/" + bid + "?token=" + TOKEN, timeout=10) as res:
                return res.read()
        except Exception:  # noqa: BLE001
            return None

    def test_load_restores_the_blobs_the_posts_name(self):
        self.remote("posts", "P2", {"title": "Pictures", "column": "info",
                                    "images": ["/_blob/" + self.A, "/_blob/" + self.B],
                                    "body": "and ![x](/_blob/%s)" % self.C})
        self.remote("kit_docs", "_index", {"assets": {"pics/p.png": "/_blob/" + self.B}})   # pulled with the posts
        blobs = self.art / "in" / "blobs"
        blobs.mkdir(parents=True)
        (blobs / (self.A + ".png")).write_bytes(PNG + b"A")                                   # `Artifact` `read`, path A
        r = self.k.artifact_load(url=self.url, token=TOKEN)
        self.assertEqual(self.blob(self.A), PNG + b"A")
        self.assertEqual(self.blob(self.B), PNG)                                              # from the repo file
        self.assertEqual(r["blobs_missing"], [self.C])
        txt = self.k.artifact_blobs_text(r)
        self.assertIn('"path": "%s"' % self.C, txt)
        self.assertIn("Artifact", txt)

    def test_pull_brings_the_map(self):
        txt = self.k.artifact_pull(full=True)
        self.assertIn('`collection: "kit_docs"`, `doc_id: "_index"`', txt)


class EmbedSaysWhatIsMissing(unittest.TestCase):
    def test_a_missing_embed_is_named_not_removed(self):
        js = (ROOT / "kit" / "board" / "shim.js").read_text(encoding="utf-8")
        i = js.index('var uM = "/_api/repo-doc?path="')
        seg = js[i:i + 2500]
        self.assertNotIn("function () { card.remove(); }", seg)
        self.assertIn("kit-embed-missing", seg)


if __name__ == "__main__":
    unittest.main()
