#!/usr/bin/env python3
"""Tests for the local agy board server (board_server.py) and CLI (board_cli.py).

    python3 -I kit/board/test_board_local.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_server  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def cli(url, *args):
    r = subprocess.run(
        [sys.executable, "-I", os.path.join(HERE, "board_cli.py"), "--url", url] + list(args),
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, ".kit"), exist_ok=True)
    with open(os.path.join(tmp, ".kit", "board.html"), "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><html><head><title>Board</title></head><body>ok</body></html>")

    srv, store = board_server.start_server(tmp, "127.0.0.1", 0)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d" % port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # 1. HTML + shim injection + health
        html = urllib.request.urlopen(url + "/").read().decode("utf-8")
        check("board.html served with /_shim.js injected", '<script src="/_shim.js"></script>' in html)
        shim = urllib.request.urlopen(url + "/_shim.js").read().decode("utf-8")
        check("shim exposes window.claude.use", "window.claude" in shim)
        health = json.loads(urllib.request.urlopen(url + "/_api/health").read().decode("utf-8"))
        check("health endpoint ok", health.get("ok") is True)

        # 2. Set, get, update, str-replace, optimistic concurrency (if_version)
        rc, out, _ = cli(url, "set", "posts", "P-1", "--data", json.dumps({
            "title": "First", "body": "Line 1\nLine 2", "column": "info", "created_at": "2026-11-20T00:00:00Z", "priority": 1
        }))
        res = json.loads(out) if rc == 0 else {}
        check("set post returns version 1", rc == 0 and res.get("version") == 1)

        rc, out, _ = cli(url, "update", "posts", "P-1", "--data", '{"priority": 2}', "--if-version", "1")
        check("update with matching if_version succeeds (v2)", rc == 0 and json.loads(out).get("version") == 2)

        # 2b. Deep merge on nested dicts (critical for data/users/owner/board-seen)
        store.set_doc("data/users/owner", "board-seen", {"posts": {"A": {"at": "2026-11-20T01:00:00Z"}, "B": {"at": "2026-11-20T01:00:00Z"}}})
        store.update_doc("data/users/owner", "board-seen", {"posts": {"C": {"at": "2026-11-20T02:00:00Z"}}})
        merged_seen = store.get_doc("data/users/owner", "board-seen")["data"].get("posts") or {}
        check("update_doc deep-merges nested dicts (board-seen preserves prior posts)",
              set(merged_seen.keys()) == {"A", "B", "C"})

        rc, _, err = cli(url, "update", "posts", "P-1", "--data", '{"priority": 3}', "--if-version", "1")
        check("update with stale if_version fails with 409", rc != 0 and "409" in err)

        rc, out, _ = cli(url, "str-replace", "posts", "P-1", "--field", "body", "--old", "Line 1", "--new", "Top\nLine 1", "--if-version", "2")
        check("str-replace updates body (v3)", rc == 0 and json.loads(out).get("version") == 3)

        # 3. Get / list / query with --out-dir produces flat JSON files
        out_dir = os.path.join(tmp, "dump")
        rc, out, _ = cli(url, "get", "posts", "P-1", "--out-dir", out_dir)
        saved = json.load(open(os.path.join(out_dir, "posts", "P-1.json"), encoding="utf-8"))
        check("get --out-dir writes flat doc JSON", rc == 0 and saved.get("body", "").startswith("Top\nLine 1") and saved.get("priority") == 2)

        # 4. Batch writes
        batch = [
            {"op": "set", "collection": "posts", "doc_id": "_orchestrator", "data": {"title": "Ask", "column": "info", "created_at": "2026-11-20T00:01:00Z"}},
            {"op": "set", "collection": "comments", "doc_id": "c-1", "data": {"post_id": "P-1", "from": "owner", "text": "hello", "created_at": "2026-11-20T00:02:00Z"}},
        ]
        rc, out, _ = cli(url, "batch", "--data", json.dumps(batch))
        check("batch writes posts and comments", rc == 0 and json.loads(out).get("ok") is True)

        rc, out, _ = cli(url, "query", "comments", "--where", '[["post_id","==","P-1"]]', "--out-dir", out_dir)
        check("query comments with where + out-dir", rc == 0 and json.loads(out).get("count") == 1 and os.path.isfile(os.path.join(out_dir, "comments", "c-1.json")))

        # 5. Blobs upload & read
        blob_src = os.path.join(tmp, "pic.txt")
        open(blob_src, "wb").write(b"hello-blob-bytes")
        rc, out, _ = cli(url, "asset", "upload", blob_src)
        bid = json.loads(out).get("id") if rc == 0 else ""
        check("asset upload returns 32-hex id", rc == 0 and len(bid) == 32)
        blob_dst = os.path.join(tmp, "pic_out.txt")
        rc, _, _ = cli(url, "asset", "read", bid, "--out", blob_dst)
        check("asset read downloads exact bytes", rc == 0 and open(blob_dst, "rb").read() == b"hello-blob-bytes")

        # 6. Comments watch, send, long-poll wait, read, reply
        rc, out, _ = cli(url, "comments", "watch", "--list")
        check("before watch: status is no_session", rc == 0 and json.loads(out).get("status") == "no_session")

        rc, out, _ = cli(url, "comments", "watch", "--on", "--watcher", "clerk-1")
        check("watch --on registers clerk", rc == 0 and json.loads(out).get("watching") is True)

        rc, out, _ = cli(url, "comments", "watch", "--list")
        check("after watch: status is available", rc == 0 and json.loads(out).get("status") == "available")

        # Simulate browser sending a comment while clerk waits
        wait_res = {}
        def waiter():
            wait_res["r"] = cli(url, "comments", "wait", "--timeout", "10")
        wt = threading.Thread(target=waiter)
        wt.start()
        time.sleep(0.15)
        req_send = urllib.request.Request(
            url + "/_api/comments/send",
            data=json.dumps({"anchor": {"id": "p-title"}, "text": "[post P-1] test comment"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        sent = json.loads(urllib.request.urlopen(req_send).read().decode("utf-8"))
        wt.join(timeout=5)
        wrc, wout, _ = wait_res["r"]
        wdata = json.loads(wout) if wrc == 0 else {}
        check("comments wait wakes immediately with thread_id and text", wrc == 0 and wdata.get("thread_id") == sent["thread_id"] and "[post P-1] test comment" in wdata.get("text", ""))

        rc, out, _ = cli(url, "comments", "reply", sent["thread_id"], "--text", "Passed to P-1.")
        check("comments reply updates thread status", rc == 0 and json.loads(out).get("status") == "replied")

        rc, out, _ = cli(url, "comments", "read", sent["thread_id"])
        check("comments read returns replies", rc == 0 and len(json.loads(out).get("replies", [])) == 1)

        # 6b. Replied threads are never popped by wait_comment (prevents clerk synchronous-exit stall)
        sent2 = store.send_comment({"id": "p-title"}, "[post P-1] already replied")
        store.reply_thread(sent2["thread_id"], "Passed to P-1.")
        w_replied = store.wait_comment(timeout=0.2)
        check("wait_comment skips already-replied threads", w_replied.get("event") == "timeout")

        # 7. seed_from_mirror (failover from Claude Code Web's board-mirror branch)
        mrepo = os.path.join(tmp, "mrepo")
        subprocess.run(["git", "init", "-q", "-b", "board-mirror", mrepo], check=True)
        os.makedirs(os.path.join(mrepo, "board", "posts"), exist_ok=True)
        os.makedirs(os.path.join(mrepo, "board", "comments"), exist_ok=True)
        json.dump({"title": "From Claude", "column": "todo", "created_at": "2026-11-20T01:00:00Z"},
                  open(os.path.join(mrepo, "board", "posts", "M-1.json"), "w"))
        json.dump({"post_id": "M-1", "from": "owner", "text": "seeded comment", "created_at": "2026-11-20T01:05:00Z"},
                  open(os.path.join(mrepo, "board", "comments", "mc-1.json"), "w"))
        subprocess.run(["git", "-C", mrepo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
        subprocess.run(["git", "-C", mrepo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"], check=True)
        info1 = board_server.seed_from_mirror(mrepo, store, branch="board-mirror")
        info2 = board_server.seed_from_mirror(mrepo, store, branch="board-mirror")
        check("seed_from_mirror imports posts and comments from git",
              info1["imported"] == 2 and store.get_doc("posts", "M-1")["data"].get("title") == "From Claude"
              and store.get_doc("comments", "mc-1")["data"].get("text") == "seeded comment")
        check("seed_from_mirror skips existing docs on rerun without --force-seed",
              info2["imported"] == 0 and info2["skipped"] == 2)
        seen_doc = store.get_doc("data/users/owner", "board-seen")
        check("seed_from_mirror seeds data/users/owner/board-seen for imported posts",
              seen_doc["exists"] and "M-1" in (seen_doc["data"].get("posts") or {})
              and seen_doc["data"]["posts"]["M-1"].get("sig") == board_server._post_sig(store.get_doc("posts", "M-1")["data"]))

        # 7b. Unlimited GET /_api/db/<coll> returns >1000 docs (no silent truncation)
        for i in range(1005):
            store.set_doc("bulk", "doc-%04d" % i, {"i": i})
        bulk_all = json.loads(urllib.request.urlopen(url + "/_api/db/bulk").read().decode("utf-8"))
        bulk_lim = json.loads(urllib.request.urlopen(url + "/_api/db/bulk?limit=10").read().decode("utf-8"))
        check("GET /_api/db/<coll> without limit returns all >1000 docs",
              len(bulk_all["docs"]) == 1005 and bulk_all["next_cursor"] is None)
        check("GET /_api/db/<coll>?limit=10 honors explicit limit",
              len(bulk_lim["docs"]) == 10 and bulk_lim["next_cursor"] == "10")

        # 8. Monotonic changes log (zero sync holes)
        rc, out, _ = cli(url, "changes", "--since", "0")
        ch = json.loads(out) if rc == 0 else {}
        check("changes --since 0 returns monotonic change log",
              rc == 0 and ch.get("latest_seq", 0) >= 5 and len(ch.get("changes", [])) >= 5)
        rc2, out2, _ = cli(url, "changes", "--since", str(ch.get("latest_seq", 0)))
        check("changes --since latest_seq returns empty list", rc2 == 0 and json.loads(out2).get("changes") == [])

        # 8b. Comment counts endpoint & inline git diff endpoint & UX shim
        cnts = json.loads(urllib.request.urlopen(url + "/_api/db/comments/counts").read().decode("utf-8"))
        check("comments/counts returns per-post totals", cnts.get("counts", {}).get("P-1", {}).get("total") == 1)
        check("shim includes Cmd+K shortcut, column switcher, and git diff loader",
              "kit-col-move" in shim and "/_api/git/diff" in shim and "Escape" in shim)
        sha = subprocess.run(["git", "-C", mrepo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        diff_res, diff_err = board_server.read_git_diff(mrepo, sha)
        check("read_git_diff returns commit stat and diff for valid sha", diff_err is None and "M-1.json" in diff_res["diff"])
        _, bad_err = board_server.read_git_diff(mrepo, "; rm -rf /")
        check("read_git_diff rejects unsafe ref", bad_err == "invalid_ref")
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()

    # 9. Token authentication (blocks unauthorized Wi-Fi/LAN access)
    srv_a, store_a = board_server.start_server(tmp, "127.0.0.1", 0, token="secret123")
    url_a = "http://127.0.0.1:%d" % srv_a.server_address[1]
    ta = threading.Thread(target=srv_a.serve_forever, daemon=True)
    ta.start()
    try:
        rc_no, _, err_no = cli(url_a, "list", "posts")
        check("token auth: rejects request without token (401)", rc_no != 0 and "401" in err_no)
        rc_ok, out_ok, _ = cli(url_a, "--token", "secret123", "list", "posts")
        check("token auth: accepts request with --token", rc_ok == 0 and "docs" in out_ok)
        # /?token=... sets HttpOnly cookie and redirects to /
        class NoRedir(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedir)
        try:
            opener.open(url_a + "/?token=secret123")
            got_cookie = False
        except urllib.error.HTTPError as e:
            got_cookie = e.code == 302 and "kit_token=secret123" in (e.headers.get("Set-Cookie") or "")
        check("token auth: /?token=... sets HttpOnly cookie and redirects (302)", got_cookie)
    finally:
        srv_a.shutdown()
        srv_a.server_close()
        store_a.close()

print("%d failed" % fails)
sys.exit(1 if fails else 0)
