#!/usr/bin/env python3
"""CLI client for the local agy board server (replaces ArtifactData, ArtifactComments, Artifact).

    python3 -I .kit/board_cli.py get posts <id> [--out-dir /tmp/kd]
    python3 -I .kit/board_cli.py list posts [--limit N] [--cursor C] [--all] [--out-dir /tmp/kd]
    python3 -I .kit/board_cli.py query comments --where '[["post_id","==","P1"]]' [--out-dir /tmp/kd]
    python3 -I .kit/board_cli.py set posts <id> (--data '{...}' | --file f.json) [--if-version N]
    python3 -I .kit/board_cli.py update posts <id> (--data '{...}' | --file f.json) [--if-version N]
    python3 -I .kit/board_cli.py str-replace posts <id> --field body --old '...' --new '...' [--if-version N]
    python3 -I .kit/board_cli.py batch (--data '[...]' | --file writes.json)
    python3 -I .kit/board_cli.py comments watch [--on | --off | --list] [--watcher clerk]
    python3 -I .kit/board_cli.py comments wait [--timeout 300]
    python3 -I .kit/board_cli.py comments read <thread_id>
    python3 -I .kit/board_cli.py comments reply <thread_id> --text '...'
    python3 -I .kit/board_cli.py asset upload <path>
    python3 -I .kit/board_cli.py asset read <blob_id> --out <path>
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_url():
    if os.environ.get("KIT_BOARD_URL"):
        return os.environ["KIT_BOARD_URL"].rstrip("/")
    cfg_path = os.path.join(ROOT, ".kit", "config.json")
    try:
        u = json.load(open(cfg_path, encoding="utf-8")).get("board_url") or ""
        if u.startswith("http://") or u.startswith("https://"):
            return u.rstrip("/")
    except (OSError, ValueError):
        pass
    return "http://127.0.0.1:8765"


def default_token():
    if os.environ.get("KIT_BOARD_TOKEN"):
        return os.environ["KIT_BOARD_TOKEN"].strip()
    tp = os.path.join(ROOT, ".kit", "board_data", "token")
    try:
        return open(tp, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def req(base, method, path, body=None, headers=None, timeout=60, token=None):
    url = base.rstrip("/") + path
    hdrs = dict(headers or {})
    if token:
        hdrs.setdefault("X-Kit-Token", token)
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return json.loads(raw.decode("utf-8"))
            return raw
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise SystemExit("board_cli: HTTP %d %s: %s" % (e.code, path, msg))
    except urllib.error.URLError as e:
        raise SystemExit("board_cli: cannot reach board server at %s (%s). Start it with: python3 -I .kit/board_server.py" % (base, e.reason))


def load_json_arg(data_str, file_path):
    if file_path:
        if file_path == "-":
            return json.load(sys.stdin)
        with open(file_path, encoding="utf-8") as fh:
            return json.load(fh)
    if data_str is not None:
        return json.loads(data_str)
    raise SystemExit("board_cli: pass --data or --file")


def write_out(out_dir, collection, doc_id, data):
    d = os.path.join(out_dir, collection)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, doc_id + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local board CLI for agy")
    ap.add_argument("--url", default=None, help="Board server URL (default: from .kit/config.json)")
    ap.add_argument("--token", default=None, help="Auth token (default: $KIT_BOARD_TOKEN or .kit/board_data/token)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("get")
    p.add_argument("collection")
    p.add_argument("doc_id")
    p.add_argument("--out-dir")

    p = sub.add_parser("list")
    p.add_argument("collection")
    p.add_argument("--limit", type=int, default=None, help="Max docs per page (default: all docs)")
    p.add_argument("--cursor", default="0")
    p.add_argument("--all", action="store_true", help="Follow pagination to fetch all docs")
    p.add_argument("--out-dir")

    p = sub.add_parser("query")
    p.add_argument("collection")
    p.add_argument("--where", default="[]")
    p.add_argument("--order-by", default=None)
    p.add_argument("--dir", default="asc")
    p.add_argument("--limit", type=int, default=None, help="Max docs per page (default: all docs)")
    p.add_argument("--cursor", default="0")
    p.add_argument("--all", action="store_true", help="Follow pagination to fetch all docs")
    p.add_argument("--out-dir")

    p = sub.add_parser("set")
    p.add_argument("collection")
    p.add_argument("doc_id")
    p.add_argument("--data")
    p.add_argument("--file")
    p.add_argument("--if-version", type=int, default=None)

    p = sub.add_parser("update")
    p.add_argument("collection")
    p.add_argument("doc_id")
    p.add_argument("--data")
    p.add_argument("--file")
    p.add_argument("--if-version", type=int, default=None)

    p = sub.add_parser("str-replace")
    p.add_argument("collection")
    p.add_argument("doc_id")
    p.add_argument("--field", default="body")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--if-version", type=int, default=None)

    p = sub.add_parser("batch")
    p.add_argument("--data")
    p.add_argument("--file")

    p = sub.add_parser("changes")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--limit", type=int, default=1000)

    pc = sub.add_parser("comments")
    csub = pc.add_subparsers(dest="ccmd", required=True)
    pw = csub.add_parser("watch")
    pw.add_argument("--on", action="store_true")
    pw.add_argument("--off", action="store_true")
    pw.add_argument("--list", action="store_true")
    pw.add_argument("--watcher", default="clerk")
    pwt = csub.add_parser("wait")
    pwt.add_argument("--timeout", type=int, default=300)
    pr = csub.add_parser("read")
    pr.add_argument("thread_id")
    prp = csub.add_parser("reply")
    prp.add_argument("thread_id")
    prp.add_argument("--text", required=True)

    pa = sub.add_parser("asset")
    asub = pa.add_subparsers(dest="acmd", required=True)
    pu = asub.add_parser("upload")
    pu.add_argument("path")
    prd = asub.add_parser("read")
    prd.add_argument("blob_id")
    prd.add_argument("--out", required=True)

    a = ap.parse_args(argv)
    base = (a.url or default_url()).rstrip("/")
    tok = a.token if a.token is not None else default_token()
    call = lambda m, p, body=None, headers=None, timeout=60: req(base, m, p, body=body, headers=headers, timeout=timeout, token=tok)

    if a.cmd == "changes":
        res = call("GET", "/_api/changes?since=%d&limit=%d" % (a.since, a.limit))
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if a.cmd == "get":
        res = call("GET", "/_api/db/%s/%s" % (urllib.parse.quote(a.collection), urllib.parse.quote(a.doc_id)))
        if a.out_dir and res.get("exists"):
            path = write_out(a.out_dir, a.collection, a.doc_id, res["data"])
            print(json.dumps({"exists": True, "id": a.doc_id, "version": res["version"], "path": path}))
        else:
            print(json.dumps(res, ensure_ascii=False))
        return 0

    if a.cmd in ("list", "query"):
        fetch_all = a.all or a.limit is None
        docs = []
        cursor = str(a.cursor)
        res = {}
        while True:
            qs = {"cursor": cursor}
            if a.limit is not None:
                qs["limit"] = str(a.limit)
            if a.cmd == "query":
                qs["where"] = a.where
                if a.order_by:
                    qs["order_by"] = a.order_by
                    qs["dir"] = a.dir
            path = "/_api/db/%s?%s" % (urllib.parse.quote(a.collection), urllib.parse.urlencode(qs))
            res = call("GET", path)
            docs.extend(res.get("docs") or [])
            nxt = res.get("next_cursor")
            if not fetch_all or not nxt:
                break
            cursor = str(nxt)
        next_cursor = None if fetch_all else res.get("next_cursor")
        if a.out_dir:
            summary = []
            for d in docs:
                p = write_out(a.out_dir, a.collection, d["id"], d["data"])
                summary.append({"id": d["id"], "version": d["version"], "path": p})
            print(json.dumps({"count": len(summary), "docs": summary, "next_cursor": next_cursor}))
        else:
            out_obj = dict(res)
            out_obj["docs"] = docs
            out_obj["next_cursor"] = next_cursor
            print(json.dumps(out_obj, ensure_ascii=False))
        return 0

    if a.cmd == "set":
        payload = {"data": load_json_arg(a.data, a.file)}
        if a.if_version is not None:
            payload["if_version"] = a.if_version
        res = call("PUT", "/_api/db/%s/%s" % (urllib.parse.quote(a.collection), urllib.parse.quote(a.doc_id)), payload)
        print(json.dumps({"id": res["id"], "version": res["version"]}))
        return 0

    if a.cmd == "update":
        payload = {"data": load_json_arg(a.data, a.file)}
        if a.if_version is not None:
            payload["if_version"] = a.if_version
        res = call("PATCH", "/_api/db/%s/%s" % (urllib.parse.quote(a.collection), urllib.parse.quote(a.doc_id)), payload)
        print(json.dumps({"id": res["id"], "version": res["version"]}))
        return 0

    if a.cmd == "str-replace":
        payload = {"field": a.field, "old_str": a.old, "new_str": a.new}
        if a.if_version is not None:
            payload["if_version"] = a.if_version
        res = call("POST", "/_api/db/%s/%s/str_replace" % (urllib.parse.quote(a.collection), urllib.parse.quote(a.doc_id)), payload)
        print(json.dumps({"id": res["id"], "version": res["version"]}))
        return 0

    if a.cmd == "batch":
        writes = load_json_arg(a.data, a.file)
        if isinstance(writes, list):
            writes = {"writes": writes}
        res = call("POST", "/_api/db/batch", writes)
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if a.cmd == "comments":
        if a.ccmd == "watch":
            if a.list or (not a.on and not a.off):
                res = call("GET", "/_api/comments/status")
            else:
                res = call("POST", "/_api/comments/watch", {"watcher": a.watcher, "on": not a.off})
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if a.ccmd == "wait":
            res = call("GET", "/_api/comments/wait?timeout=%d" % a.timeout, timeout=a.timeout + 15)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if a.ccmd == "read":
            res = call("GET", "/_api/comments/threads/" + urllib.parse.quote(a.thread_id))
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if a.ccmd == "reply":
            res = call("POST", "/_api/comments/threads/%s/reply" % urllib.parse.quote(a.thread_id), {"text": a.text})
            print(json.dumps(res, ensure_ascii=False))
            return 0

    if a.cmd == "asset":
        if a.acmd == "upload":
            raw = open(a.path, "rb").read()
            ctype = mimetypes.guess_type(a.path)[0] or "application/octet-stream"
            hdrs = {"Content-Type": ctype, "X-Filename": urllib.parse.quote(os.path.basename(a.path))}
            res = call("POST", "/_api/blobs", raw, headers=hdrs)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if a.acmd == "read":
            raw = call("GET", "/_blob/" + urllib.parse.quote(a.blob_id))
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            with open(a.out, "wb") as fh:
                fh.write(raw)
            print(json.dumps({"ok": True, "path": a.out, "bytes": len(raw)}))
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
