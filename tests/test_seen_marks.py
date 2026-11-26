"""CL21: a post he has read stops showing as new, on every board, in the Artifact page and on the local server.

    python3 -m unittest tests.test_seen_marks -v

1. The local server keeps ONE read doc (data/users/<user>/board-seen) for every board: whichever board the page has
   picked (X-Kit-Repo), the doc is the primary store's, an attached board's marks under "repos/<board>/<post>"; marks
   an attached board kept in its own store before CL21 show under that prefix.
2. The built Artifact page in Chromium (kit/claude/test_seen_marks.js): a post read in its sheet, in the Feed or in a
   window is not new after a reload, on the primary board and on an attached one; an unread post stays new; reading
   an attached board's "P-a" leaves the primary board's "P-a" new; an old bare-id mark still holds.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kit" / "board"))
import board_server  # noqa: E402

SEEN = "/_api/doc/data/users/owner/board-seen"


# a browser run that has not ended by then is hung: a guard against hanging, never a speed check (a busy computer
# made a 400 s limit fail an honest run of 437 s)
HANG_S = 1800


def call(url, method, path, repo="", body=None):
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if repo:
        req.add_header("X-Kit-Repo", repo)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


class LocalServerOneSeenDoc(unittest.TestCase):
    def test_one_doc_for_every_board(self):
        tmp = tempfile.mkdtemp()
        try:
            a, b = os.path.join(tmp, "harm"), os.path.join(tmp, "kit")
            for r in (a, b):
                os.makedirs(os.path.join(r, ".kit"))
                Path(r, ".kit", "board.html").write_text("<!doctype html><title>b</title>", encoding="utf-8")
            old = board_server.BoardStore(os.path.join(b, ".kit", "board_data"))
            old.set_doc("posts", "K-old", {"title": "k", "column": "info", "created_at": "2026-11-21T00:00:00Z"})
            old.set_doc("data/users/owner", "board-seen", {"posts": {"K-old": {"at": "2026-11-21T00:00:00Z", "sig": "s"}}})
            old.close()
            srv, store = board_server.start_server(a, "127.0.0.1", 0, attach={"kit": b})
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = "http://127.0.0.1:%d" % srv.server_address[1]
            try:
                call(url, "PUT", SEEN, body={"data": {"posts": {"P-1": {"at": "2026-11-23T01:00:00Z", "sig": "x"}}}})
                call(url, "PATCH", SEEN, repo="kit", body={"data": {"posts": {"repos/kit/K-1": {"at": "2026-11-23T02:00:00Z", "sig": "y"}}}})
                for repo in ("", "kit", "all"):
                    posts = (call(url, "GET", SEEN, repo=repo).get("data") or {}).get("posts") or {}
                    self.assertIn("P-1", posts, repo)
                    self.assertIn("repos/kit/K-1", posts, repo)
                    self.assertIn("repos/kit/K-old", posts, "old mark of the attached board, under its prefix (%r)" % repo)
                kept = (store.get_doc("data/users/owner", "board-seen").get("data") or {}).get("posts") or {}
                self.assertIn("repos/kit/K-1", kept, "the write from the attached board lands in the primary store")
            finally:
                srv.shutdown()
                srv.server_close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ArtifactPageSeen(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_built_page(self):
        tmp = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            env = dict(os.environ, KIT_NO_FUNNEL="1")
            subprocess.run([sys.executable, str(ROOT / "install.py"), tmp, "--quick"], capture_output=True, env=env, check=True, timeout=300)
            page = os.path.join(tmp, ".kit", "board.html")
            subprocess.run([sys.executable, str(ROOT / "kit" / "claude" / "build_artifact_page.py"), "--kit", str(ROOT), "--page", page,
                            "--board-url", "https://claude.ai/artifact/test", "--project", "Harmonize"], capture_output=True, check=True)
            r = subprocess.run(["node", str(ROOT / "kit" / "claude" / "test_seen_marks.js")], capture_output=True, text=True,
                               timeout=HANG_S, env=dict(env, KIT_PAGE=page), cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
