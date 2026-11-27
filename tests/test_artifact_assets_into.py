"""CL10 fix (the owner, 2026-11-23 02:22: "All the pictures don't show up here"): an attached board's pictures are
mapped in its own index, repos/<id>/kit_docs/_index, the one the page reads when that board is shown."""
import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import temp_cleanup  # noqa: E402,F401  CL52: the folders this file makes go at exit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = os.path.join(ROOT, "kit", "claude", "kit_artifact_sync.py")


def batch(out):
    files = sorted(glob.glob(os.path.join(out, "batch-*.json")))
    return [e for f in files for e in json.load(open(f, encoding="utf-8"))]


class TestAssetsInto(unittest.TestCase):
    def run_assets(self, *extra):
        d = tempfile.mkdtemp()
        up = os.path.join(d, "uploaded.json")
        json.dump({"docs/cl10/a1-sections-default-phone.png": "0123456789abcdef0123456789abcdef"}, open(up, "w"))
        r = subprocess.run([sys.executable, "-I", SYNC, "assets", "--uploaded", up, "--out", d] + list(extra),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return d, batch(d)

    def test_primary_board_index(self):
        d, es = self.run_assets()
        self.assertEqual([(e["collection"], e["doc_id"]) for e in es], [("kit_docs", "_index")])

    def test_attached_board_index(self):
        d, es = self.run_assets("--into", "board")
        self.assertEqual([(e["collection"], e["doc_id"]) for e in es], [("repos/board/kit_docs", "_index")])
        data = json.load(open(es[0]["file_path"], encoding="utf-8"))
        self.assertEqual(data["assets"]["docs/cl10/a1-sections-default-phone.png"], "/_blob/0123456789abcdef0123456789abcdef")

    @unittest.skipUnless(os.path.isfile(os.path.join(ROOT, ".kit", "kit_artifact_sync.py")), "the kit is not installed on itself")
    def test_kit_copy_is_current(self):
        # the kit's own install runs this copy: `python3 -I install.py . --update` refreshes it
        self.assertTrue(open(os.path.join(ROOT, ".kit", "kit_artifact_sync.py"), "rb").read() == open(SYNC, "rb").read(),
                        ".kit/kit_artifact_sync.py is older than kit/claude/: run `python3 -I install.py . --update`")


if __name__ == "__main__":
    unittest.main()
