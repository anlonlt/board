"""Regression tests for opt-in cookie-independent embedded preview mode (--embed-mode / KIT_EMBED_PREVIEW=1).

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "kit" / "board") not in sys.path:
    sys.path.append(str(ROOT / "kit" / "board"))
import board_server  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(url, headers=None, data=None, method=None):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=10) as res:
            return res.status, dict(res.headers.items()), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()


class TestEmbedMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, ".kit"), exist_ok=True)
        with open(os.path.join(self.root, ".kit", "board.html"), "w", encoding="utf-8") as fh:
            fh.write("<!doctype html><html><head><title>Board</title></head><body><main>board-body</main></body></html>")

    def _start(self, token=None, embed_mode=False):
        srv, store = board_server.start_server(self.root, "127.0.0.1", 0, token=token, embed_mode=embed_mode)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        def _stop():
            srv.shutdown()
            srv.server_close()
            store.close()
        self.addCleanup(_stop)
        return "http://127.0.0.1:%d" % srv.server_address[1], store

    def test_embed_mode_unlock_and_token_header_flow(self):
        tok = "embed-secret-123"
        url, _ = self._start(token=tok, embed_mode=True)

        # Health advertises embed_mode=True
        code, _, raw = _open(url + "/_api/health")
        self.assertEqual(code, 200)
        health = json.loads(raw.decode("utf-8"))
        self.assertTrue(health["ok"])
        self.assertTrue(health["auth"])
        self.assertTrue(health["embed_mode"])

        # Unauthenticated GET / returns unlock page without redirect loop or cookies
        code, hdrs, raw = _open(url + "/", headers={"Accept": "text/html"})
        html = raw.decode("utf-8")
        self.assertEqual(code, 200)
        self.assertNotIn("Set-Cookie", hdrs)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("location.replace('/?token=", html)
        self.assertIn("application/x-kit-board", html)
        self.assertIn("X-Kit-Board-Doc", html)
        self.assertIn("X-Kit-Token", html)

        # Query-string ?token= is ignored in embed mode (no cookie, no redirect)
        code_q, hdrs_q, raw_q = _open(url + "/?token=" + tok, headers={"Accept": "text/html"})
        self.assertEqual(code_q, 200)
        self.assertNotIn("Set-Cookie", hdrs_q)
        self.assertNotIn("X-Kit-Board-Doc", hdrs_q)
        self.assertIn("Unlock board preview", raw_q.decode("utf-8"))

        # Unauthenticated API returns 401
        code_api, _, _ = _open(url + "/_api/db/posts")
        self.assertEqual(code_api, 401)

        # Wrong token with Accept: application/x-kit-board returns 401 JSON (no retry/redirect)
        code_bad, hdrs_bad, raw_bad = _open(
            url + "/",
            headers={"Accept": "application/x-kit-board", "X-Kit-Token": "wrong-token"},
        )
        self.assertEqual(code_bad, 401)
        self.assertNotIn("Set-Cookie", hdrs_bad)
        self.assertNotIn("X-Kit-Board-Doc", hdrs_bad)
        self.assertEqual(json.loads(raw_bad.decode("utf-8"))["error"], "unauthorized")

        # Valid X-Kit-Token + Accept: application/x-kit-board returns authenticated HTML with marker + inline shim
        code_ok, hdrs_ok, raw_ok = _open(
            url + "/",
            headers={"Accept": "application/x-kit-board", "X-Kit-Token": tok},
        )
        doc_html = raw_ok.decode("utf-8")
        self.assertEqual(code_ok, 200)
        self.assertEqual(hdrs_ok.get("X-Kit-Board-Doc"), "1")
        self.assertNotIn("Set-Cookie", hdrs_ok)
        self.assertIn("board-body", doc_html)
        self.assertIn("window.claude", doc_html)
        self.assertIn("window.__KIT_TOKEN__", doc_html)
        self.assertNotIn('/_shim.js', doc_html)
        self.assertNotIn("/_manifest.webmanifest", doc_html)
        self.assertNotIn("/_sw.js", doc_html)
        self.assertNotIn("/_icon.svg", doc_html)

        # API reads/writes work with X-Kit-Token alone (no cookies)
        code_w, _, _ = _open(
            url + "/_api/doc/posts/E1",
            headers={"Content-Type": "application/json", "X-Kit-Token": tok},
            data=json.dumps({"data": {"title": "Embed card", "column": "info"}}).encode("utf-8"),
            method="PUT",
        )
        self.assertEqual(code_w, 200)
        code_r, _, raw_r = _open(url + "/_api/db/posts", headers={"X-Kit-Token": tok})
        self.assertEqual(code_r, 200)
        posts = json.loads(raw_r.decode("utf-8"))["docs"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "E1")

        # Authenticated change poll picks up a comment written by a second client
        code_ch0, _, raw_ch0 = _open(url + "/_api/changes?since=0&limit=50", headers={"X-Kit-Token": tok})
        self.assertEqual(code_ch0, 200)
        seq0 = json.loads(raw_ch0.decode("utf-8"))["latest_seq"]

        code_c, _, _ = _open(
            url + "/_api/doc/comments/EC1",
            headers={"Content-Type": "application/json", "X-Kit-Token": tok},
            data=json.dumps({"data": {"post_id": "E1", "from": "owner", "text": "from second client"}}).encode("utf-8"),
            method="PUT",
        )
        self.assertEqual(code_c, 200)

        code_ch1, _, raw_ch1 = _open(url + "/_api/changes?since=%d&limit=50" % seq0, headers={"X-Kit-Token": tok})
        self.assertEqual(code_ch1, 200)
        ch1 = json.loads(raw_ch1.decode("utf-8"))
        self.assertGreater(ch1["latest_seq"], seq0)
        self.assertTrue(any(c["collection"] == "comments" and c["doc_id"] == "EC1" for c in ch1["changes"]))

    def test_default_non_embed_mode_unchanged(self):
        tok = "std-secret-456"
        url, _ = self._start(token=tok, embed_mode=False)

        # Health reports embed_mode=False by default
        code, _, raw = _open(url + "/_api/health")
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(raw.decode("utf-8"))["embed_mode"])

        # Unauthenticated GET / returns 401 with localStorage unlock form
        code_u, _, raw_u = _open(url + "/", headers={"Accept": "text/html"})
        self.assertEqual(code_u, 401)
        self.assertIn("localStorage.getItem('kit_board_token')", raw_u.decode("utf-8"))

        # Unauthenticated API returns 401
        code_api, _, _ = _open(url + "/_api/db/posts")
        self.assertEqual(code_api, 401)

        # /?token= sets HttpOnly cookie and redirects 302
        code_t, hdrs_t, _ = _open(url + "/?token=" + tok)
        self.assertEqual(code_t, 302)
        self.assertIn("kit_token=" + tok, hdrs_t.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", hdrs_t.get("Set-Cookie", ""))

        # Cookie-authenticated GET / serves /_shim.js and PWA tags (no X-Kit-Board-Doc)
        code_ok, hdrs_ok, raw_ok = _open(url + "/", headers={"Cookie": "kit_token=" + tok})
        html = raw_ok.decode("utf-8")
        self.assertEqual(code_ok, 200)
        self.assertNotIn("X-Kit-Board-Doc", hdrs_ok)
        self.assertIn('<script src="/_shim.js"></script>', html)
        self.assertIn("/_manifest.webmanifest", html)
        self.assertIn("/_sw.js", html)

        # /_shim.js accessible with cookie
        code_s, _, raw_s = _open(url + "/_shim.js", headers={"Cookie": "kit_token=" + tok})
        self.assertEqual(code_s, 200)
        self.assertIn("window.claude", raw_s.decode("utf-8"))


    def test_embed_mode_env_var_and_cli_flag(self):
        import subprocess
        # Verify KIT_EMBED_PREVIEW=1 activates embed mode via CLI main()
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import board_server\n"
            "import threading, urllib.request, json\n"
            "orig = board_server.start_server\n"
            "captured = {}\n"
            "def fake(root, host='127.0.0.1', port=8765, token=None, notify_url=None, embed_mode=False):\n"
            "    captured['embed_mode'] = embed_mode\n"
            "    raise KeyboardInterrupt()\n"
            "board_server.start_server = fake\n"
            "try:\n"
            "    board_server.main(sys.argv[2:])\n"
            "except KeyboardInterrupt:\n"
            "    pass\n"
            "print(json.dumps(captured))\n"
        )
        # 1. Default OFF
        env_off = dict(os.environ)
        env_off.pop("KIT_EMBED_PREVIEW", None)
        r_off = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "kit" / "board"), "--root", self.root],
            capture_output=True, text=True, check=True, env=env_off,
        )
        self.assertFalse(json.loads(r_off.stdout)["embed_mode"])

        # 2. --embed-mode CLI flag ON
        r_cli = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "kit" / "board"), "--root", self.root, "--embed-mode"],
            capture_output=True, text=True, check=True, env=env_off,
        )
        self.assertTrue(json.loads(r_cli.stdout)["embed_mode"])

        # 3. KIT_EMBED_PREVIEW=1 env var ON
        env_on = dict(env_off, KIT_EMBED_PREVIEW="1")
        r_env = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "kit" / "board"), "--root", self.root],
            capture_output=True, text=True, check=True, env=env_on,
        )
        self.assertTrue(json.loads(r_env.stdout)["embed_mode"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
