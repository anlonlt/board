#!/usr/bin/env python3
"""Mirror what the local board server reads from disk into the Artifact's db, so the page's Repo Docs reader, its
pictures and its inline git diffs work in a claude.ai Artifact (see artifact_adapter.js for the reading side).

    python3 -I kit_artifact_sync.py docs   --kit /tmp/board-kit --repo . --out /tmp/kas
    python3 -I kit_artifact_sync.py assets --out /tmp/kas --uploaded /tmp/kas/uploaded.json
    python3 -I kit_artifact_sync.py diffs  --kit /tmp/board-kit --repo . --posts /tmp/kd/posts --out /tmp/kas

Each command writes ArtifactData `batch` files (<=50 writes, <1 MiB each) as /tmp/kas/batch-*.json and prints the
call to make: `ArtifactData` `batch`, `url` the board, `writes` = the file's JSON (an array). Documents:
  kit_docs/_index           {docs: [list_repo_docs rows], assets: {path: "/_blob/<id>"}, mirrored_at}
  kit_docs/<encoded path>   read_repo_doc(path) as the server answers it (md and html; pictures go to assets)
  kit_git/<encoded sha>     read_git_diff(sha) for each commit a post names
`docs` also lists the pictures to upload in /tmp/kas/images.txt: upload them with `Artifact` `publish`,
`url` the board, `asset: true`, `file_paths` (25 per call), save the results as {"<repo path>": "<url>"} in
uploaded.json, then run `assets`. Nothing here writes to the Artifact by itself: the session makes the calls.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_DOC = 240 * 1024          # an Artifact db document is at most 256 KiB once serialized


def server(kit):
    sys.path.insert(0, os.path.join(kit, "kit", "board"))
    import board_server  # noqa: E402  (the kit's own readers, so the shapes match the server exactly)
    return board_server


def doc_id(path):
    """Same encoding as artifact_adapter.js docId()."""
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", lambda m: "+%X~" % ord(m.group(0)), path)[:200]


def write_batches(out, writes, tag):
    """Each document to its own JSON file, and batch files of <=50 entries that point at them with file_path
    (ArtifactData reads the file, so nothing large passes through the conversation)."""
    os.makedirs(os.path.join(out, tag), exist_ok=True)
    for f in glob.glob(os.path.join(out, "batch-%s-*.json" % tag)) + glob.glob(os.path.join(out, tag, "*.json")):
        os.remove(f)
    entries = []
    for i, w in enumerate(writes):
        e = {k: v for k, v in w.items() if k != "data"}
        if "data" in w:
            fp = os.path.join(os.path.abspath(out), tag, "%04d.json" % i)
            json.dump(w["data"], open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            e["file_path"] = fp
        entries.append(e)
    paths = []
    for n in range(0, len(entries), 50):
        p = os.path.join(out, "batch-%s-%02d.json" % (tag, n // 50 + 1))
        json.dump(entries[n:n + 50], open(p, "w", encoding="utf-8"), ensure_ascii=False)
        paths.append(p)
    for p in paths:
        print("ArtifactData batch, url the board, writes = the array in", p)
    return paths


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_docs(a):
    bs = server(a.kit)
    listing = bs.list_repo_docs(a.repo)
    writes, images, kept = [], [], []
    for row in listing["docs"]:
        if row["kind"] == "img":
            images.append(row["path"]); kept.append(row); continue
        res, err = bs.read_repo_doc(a.repo, row["path"])
        if err or not res:
            continue
        if res.get("kind") == "html":
            res["url"] = ""                       # no /_repo/ server: the viewer shows res.content
        if row["path"].startswith(".kit/board"):
            continue                              # the page itself: never useful in its own reader
        while len(json.dumps(res, ensure_ascii=False).encode("utf-8")) > MAX_DOC:
            res["content"] = res["content"][:int(len(res["content"]) * 0.8)]
            res["cut"] = True
        writes.append({"op": "set", "collection": "kit_docs", "doc_id": doc_id(row["path"]), "data": res})
        kept.append(row)
    index = {"docs": kept, "assets": {}, "mirrored_at": now(), "total": len(kept)}
    prev = os.path.join(a.out, "uploaded.json")
    if os.path.exists(prev):
        index["assets"] = json.load(open(prev, encoding="utf-8"))
    writes.insert(0, {"op": "set", "collection": "kit_docs", "doc_id": "_index", "data": index})
    write_batches(a.out, writes, "docs")
    open(os.path.join(a.out, "images.txt"), "w").write("\n".join(os.path.join(os.path.abspath(a.repo), p) for p in images) + "\n")
    print("%d docs, %d pictures to upload (images.txt). Existing documents: add if_version to their writes, or delete "
          "kit_docs/* first (ArtifactData list kit_docs, then batch delete)." % (len(kept) - len(images), len(images)))


def cmd_assets(a):
    up = json.load(open(a.uploaded, encoding="utf-8"))
    up = {k: (v if v.startswith("/_blob/") or v.startswith("https://") else "/_blob/" + v) for k, v in up.items()}
    write_batches(a.out, [{"op": "update", "collection": "kit_docs", "doc_id": "_index", "data": {"assets": up}}], "assets")
    print("%d pictures mapped (pin it with the _index version you read: if_version)." % len(up))


def cmd_diffs(a):
    bs = server(a.kit)
    shas = set()
    for f in glob.glob(os.path.join(a.posts, "*.json")):
        txt = open(f, encoding="utf-8").read()
        shas.update(re.findall(r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", txt))
    writes = []
    for sha in sorted(shas):
        res, err = bs.read_git_diff(a.repo, sha)
        if err or not res:
            continue
        writes.append({"op": "set", "collection": "kit_git", "doc_id": doc_id(sha), "data": res})
    write_batches(a.out, writes, "diffs")
    print("%d commits named in posts, %d diffs mirrored." % (len(shas), len(writes)))


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("docs"); d.add_argument("--kit", required=True); d.add_argument("--repo", default="."); d.add_argument("--out", default="/tmp/kas")
    s = sub.add_parser("assets"); s.add_argument("--uploaded", required=True); s.add_argument("--out", default="/tmp/kas")
    g = sub.add_parser("diffs"); g.add_argument("--kit", required=True); g.add_argument("--repo", default=".")
    g.add_argument("--posts", required=True); g.add_argument("--out", default="/tmp/kas")
    a = ap.parse_args(argv)
    {"docs": cmd_docs, "assets": cmd_assets, "diffs": cmd_diffs}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
