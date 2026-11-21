#!/usr/bin/env python3
"""Regression tests for checklist features: Markdown XSS fuzz, gzip/HEAD/CSP, card attribution, doctor/search/queue, verify/uninstall, and MCP stdio."""
import contextlib
import gzip
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


board_server = _load_mod("kit_board_server_chk", os.path.join(ROOT, "kit", "board", "board_server.py"))
mcp_stdio = _load_mod("kit_mcp_stdio_chk", os.path.join(ROOT, "kit", "board", "mcp_stdio.py"))


class TestChecklistFeatures(unittest.TestCase):
    def test_markdown_xss_regression(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with open(os.path.join(ROOT, "kit", "board", "board.template.html"), encoding="utf-8") as fh:
            html = fh.read()
        m_esc = re.search(r"(function esc\(s\)\s*\{.*?\n)", html)
        m_inl = re.search(r"(function inline\(s\)\s*\{.*?\n  \})", html, re.S)
        m_rb = re.search(r"(function renderBody\(text\)\s*\{.*?\n  \})", html, re.S)
        self.assertTrue(m_esc and m_inl and m_rb, "could not extract Markdown renderer functions")
        payloads = [
            "<script>alert(1)</script>",
            '<img src=x onerror="alert(1)">',
            "[click](javascript:alert(1))",
            "[data](data:text/html,<script>alert(1)</script>)",
            "[[nested](javascript:alert(1))](https://example.com)",
            "&#60;script&#62;alert(1)&#60;/script&#62;",
            "```html\n<script>alert(1)</script>\n```",
        ]
        js = (
            'function str(v) { return typeof v === "string" ? v : (v == null ? "" : String(v)); }\n'
            + m_esc.group(1) + "\n" + m_inl.group(1) + "\n" + m_rb.group(1) + "\n"
            + "const payloads = " + json.dumps(payloads) + ";\n"
            + "for (const p of payloads) {\n"
            + "  const out = renderBody(p);\n"
            + "  if (/<script\\b/i.test(out) || /<img\\b/i.test(out) || /<[^>]+\\bon\\w+\\s*=/i.test(out) || /href=[\"']?javascript:/i.test(out) || /href=[\"']?data:/i.test(out)) {\n"
            + "    console.error('XSS LEAK:', p, '=>', out); process.exit(1);\n"
            + "  }\n"
            + "}\nconsole.log('XSS_OK');\n"
        )
        r = subprocess.run([node, "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("XSS_OK", r.stdout)

    def test_server_gzip_head_csp_lang_and_preview_warning(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".kit"), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, "kit", "board", "board.template.html"), os.path.join(td, ".kit", "board.html"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                srv, store = board_server.start_server(td, host="127.0.0.1", port=0, preview=True)
            self.assertIn("WARNING: --preview disables authentication", err.getvalue())
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/", headers={"Accept-Encoding": "gzip"})
                r = c.getresponse()
                body = r.read()
                self.assertEqual(r.status, 200)
                self.assertEqual(r.getheader("Content-Encoding"), "gzip")
                self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
                self.assertIn("object-src 'none'", r.getheader("Content-Security-Policy") or "")
                html = gzip.decompress(body).decode("utf-8")
                self.assertIn('<html lang="en">', html)
                self.assertLess(len(body), len(html.encode("utf-8")) // 2)
                for path in ("/", "/_api/health", "/_shim.js"):
                    c.request("HEAD", path)
                    rh = c.getresponse()
                    rh.read()
                    self.assertEqual(rh.status, 200, path)
            finally:
                srv.shutdown()
                srv.server_close()
                store.close()

    def test_card_attribution_inbox_queue_search_and_doctor(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".kit"), exist_ok=True)
            srv, store = board_server.start_server(td, host="127.0.0.1", port=0, preview=True)
            store.set_doc("posts", "lm-1", {"title": "Benchmark models", "column": "todo", "priority": 1, "runner": "lmarena", "for": "anlon", "body": "Run eval"})
            store.set_doc("posts", "ch-1", {"title": "Check Outlook", "column": "todo", "priority": 2, "runner": "claude-chrome", "for": "friend", "body": "Open M365"})
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                r1 = subprocess.run([sys.executable, os.path.join(ROOT, "kit", "board", "board_cli.py"), "--url", f"http://127.0.0.1:{port}", "inbox", "--runner", "lmarena"], capture_output=True, text=True)
                self.assertEqual(r1.returncode, 0, r1.stderr)
                d1 = json.loads(r1.stdout)
                self.assertEqual(d1["assigned_total"], 1)
                self.assertEqual(d1["assigned_posts"][0]["id"], "lm-1")

                r2 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kit.py"), "queue", "--runner", "claude-chrome", "--for", "friend", "--db", store.db_path], capture_output=True, text=True)
                self.assertEqual(r2.returncode, 0, r2.stderr)
                self.assertIn("ch-1", r2.stdout)
                self.assertIn("@claude-chrome", r2.stdout)

                r3 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kit.py"), "search", "Outlook", "--db", store.db_path], capture_output=True, text=True)
                self.assertEqual(r3.returncode, 0, r3.stderr)
                self.assertIn("ch-1", r3.stdout)

                r4 = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kit.py"), "doctor", "--db", store.db_path], capture_output=True, text=True)
                self.assertEqual(r4.returncode, 0, r4.stderr + r4.stdout)
                self.assertIn("doctor: OK", r4.stdout)
            finally:
                srv.shutdown()
                srv.server_close()
                store.close()

    def test_install_title_dedup_verify_and_uninstall(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), td, "--project", "Acme Board", "--runtime", "arena", "--no-survey", "test"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            html = open(os.path.join(td, ".kit", "board.html"), encoding="utf-8").read()
            self.assertIn("<title>Acme Board</title>", html)
            self.assertNotIn("Acme Board board", html)

            rv = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), td, "--verify"], capture_output=True, text=True)
            self.assertEqual(rv.returncode, 0, rv.stderr + rv.stdout)
            self.assertIn("0 modified, 0 missing", rv.stdout)

            ru = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), td, "--uninstall"], capture_output=True, text=True)
            self.assertEqual(ru.returncode, 0, ru.stderr + ru.stdout)
            self.assertIn("0 kept", ru.stdout)

    def test_mcp_stdio_protocol(self):
        srv = mcp_stdio.MCPServer(ROOT)
        r_init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r_init["result"]["serverInfo"]["name"], "board-kit-mcp")
        r_list = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {t["name"] for t in r_list["result"]["tools"]}
        self.assertEqual(names, {"board_inbox", "board_queue", "board_post", "board_reply", "board_search"})

    def test_post_unlock_and_kit_report(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".kit"), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, "kit", "board", "board.template.html"), os.path.join(td, ".kit", "board.html"))
            srv, store = board_server.start_server(td, host="127.0.0.1", port=0, token="secret-xyz")
            store.set_doc("posts", "q1", {"title": "Pick one", "column": "question", "created_at": "2026-11-20T10:00:00Z"})
            store.set_doc("comments", "c1", {"post_id": "q1", "from": "owner", "text": "go", "created_at": "2026-11-20T10:01:00Z"})
            store.set_doc("comments", "c2", {"post_id": "q1", "from": "agent", "text": "done", "created_at": "2026-11-20T10:03:00Z"})
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("POST", "/_api/unlock", body=json.dumps({"token": "wrong"}), headers={"Content-Type": "application/json"})
                r_bad = c.getresponse(); r_bad.read()
                self.assertEqual(r_bad.status, 401)
                c.request("POST", "/_api/unlock", body=json.dumps({"token": "secret-xyz"}), headers={"Content-Type": "application/json"})
                r_ok = c.getresponse(); r_ok.read()
                self.assertEqual(r_ok.status, 200)
                self.assertIn("kit_token=secret-xyz", r_ok.getheader("Set-Cookie") or "")
                c.close()

                rep = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kit.py"), "report", "--db", store.db_path], capture_output=True, text=True)
                self.assertEqual(rep.returncode, 0, rep.stderr)
                self.assertIn("2.0 / 2.0 min", rep.stdout)
                self.assertIn("Board Self-Audit & ROI Report", rep.stdout)
            finally:
                srv.shutdown()
                srv.server_close()
                store.close()


if __name__ == "__main__":
    unittest.main()
