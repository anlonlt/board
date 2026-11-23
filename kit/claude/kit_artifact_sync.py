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
  kit_repos/_index          {repos: [{id, name, github}], primary: {name}, mirrored_at}: the attached boards
  repos/<id>/posts|comments each attached repo's board (install.py --attach), read from its board-mirror branch
`boards` mirrors the attached repos (artifact_repos.js shows them behind the header switcher and "All boards"):

    python3 -I kit_artifact_sync.py boards --repo . --out /tmp/kas [--clone harmonize=/home/user/harmonize]

It reads .kit/config.json's portfolio (and --attach name=owner/repo, for a board without the kit's config); each attached repo must be cloned here (add_repo, then git clone): --clone,
else its recorded path, else ../<name>. Only what changed since the last run is written (boards-state.json in --out;
--full rewrites all), and a post or comment gone from a mirror is deleted. A write to a document already in the
Artifact needs its version: pass --versions (a {"<collection>/<doc_id>": version} map from ArtifactData list). `docs` and `diffs` take --into <id> to
fill an attached board's Repo Docs reader and diffs (repos/<id>/kit_docs, repos/<id>/kit_git) from its clone.
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


def repo_id(name):
    """Same id as board_server.py gives an attached repo."""
    return re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")


def prefix(a):
    into = getattr(a, "into", "") or ""
    return ("repos/%s/" % repo_id(into)) if into else ""


def read_mirror(clone, branch=None):
    """{coll: {doc_id: data}} for posts and comments of a clone's board-mirror branch (git objects only, nothing
    checked out). The same files board_server.seed_from_mirror imports."""
    import io
    import subprocess
    import tarfile
    if not branch:
        try:
            branch = json.load(open(os.path.join(clone, ".kit", "config.json"), encoding="utf-8")).get("mirror", {}).get("branch")
        except (OSError, ValueError):
            branch = None
        branch = branch or "board-mirror"

    def git(*args, **kw):
        return subprocess.run(["git", "-C", clone] + list(args), capture_output=True, timeout=180, **kw)
    git("fetch", "-q", "origin", "%s:refs/remotes/origin/%s" % (branch, branch))
    ref = next((c for c in ("origin/" + branch, branch) if git("rev-parse", "--verify", "--quiet", c).returncode == 0), None)
    out = {"posts": {}, "comments": {}}
    if not ref:
        return None, out
    arc = git("archive", "--format=tar", ref, "board")
    if arc.returncode != 0 or not arc.stdout:
        return ref, out
    with tarfile.open(fileobj=io.BytesIO(arc.stdout), mode="r:*") as tf:
        for m in tf.getmembers():
            parts = m.name.lstrip("./").split("/")
            if not m.isfile() or len(parts) != 3 or parts[0] != "board" or parts[1] not in out or not parts[2].endswith(".json"):
                continue
            try:
                data = json.loads(tf.extractfile(m).read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("data"), dict) and set(data) <= {"id", "data", "version", "updated_at"}:
                data = data["data"]                    # an exported {id, data} row
            if isinstance(data, dict):
                out[parts[1]][parts[2][:-5]] = data
    return ref, out


def cmd_boards(a):
    import hashlib
    try:
        cfg = json.load(open(os.path.join(a.repo, ".kit", "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    clones = dict(x.split("=", 1) for x in (a.clone or []) if "=" in x)
    portfolio = list(cfg.get("portfolio") or [])
    for spec in a.attach or []:                        # a board without the kit's config (or one more repo)
        name, _, gh = spec.partition("=")
        gh = gh[len("github:"):] if gh.startswith("github:") else gh
        if name and not any(isinstance(x, dict) and repo_id(x.get("name") or "") == repo_id(name) for x in portfolio):
            portfolio.append({"name": name, "github": gh.strip("/")})
    state_p = os.path.join(a.out, "boards-state.json")
    old = {} if a.full or not os.path.exists(state_p) else json.load(open(state_p, encoding="utf-8"))
    new, writes, rows, report = {}, [], [], []
    for item in portfolio:
        if not isinstance(item, dict):
            continue
        gh = str(item.get("github") or "")
        name = str(item.get("name") or gh.split("/")[-1] or os.path.basename(str(item.get("path") or "")))
        rid = repo_id(name)
        if not rid or rid == "all":
            continue
        cands = [clones.get(name), clones.get(rid), item.get("path"),
                 os.path.join(os.path.dirname(os.path.abspath(a.repo)), gh.split("/")[-1] or name)]
        clone = next((c for c in cands if c and os.path.isdir(os.path.join(c, ".git"))), None)
        rows.append({"id": rid, "name": name, "github": gh})
        if not clone:
            report.append("%s: no clone here (add_repo %s, git clone, then --clone %s=<path>); its board is kept as it was"
                          % (name, gh or "<owner/repo>", name))
            new.update({k: v for k, v in old.items() if k.startswith(rid + "/")})
            continue
        ref, board = read_mirror(clone)
        if not ref:
            report.append("%s: no board-mirror branch in %s (run kit.py sync-db --commit there)" % (name, clone))
            new.update({k: v for k, v in old.items() if k.startswith(rid + "/")})
            continue
        n_changed = 0
        for coll, docs in board.items():
            for did, data in docs.items():
                body = json.dumps(data, ensure_ascii=False, sort_keys=True)
                if len(body.encode("utf-8")) > MAX_DOC:
                    report.append("%s: %s/%s is over 240 KiB, skipped" % (name, coll, did))
                    continue
                key = "%s/%s/%s" % (rid, coll, doc_id(did))
                new[key] = hashlib.sha1(body.encode("utf-8")).hexdigest()
                if old.get(key) != new[key]:
                    writes.append({"op": "set", "collection": "repos/%s/%s" % (rid, coll), "doc_id": doc_id(did), "data": data})
                    n_changed += 1
        gone = [k for k in old if k.startswith(rid + "/") and k not in new]
        for k in gone:
            _, coll, did = k.split("/", 2)
            writes.append({"op": "delete", "collection": "repos/%s/%s" % (rid, coll), "doc_id": did})
        report.append("%s: %d posts, %d comments from %s; %d to write, %d to delete"
                      % (name, len(board["posts"]), len(board["comments"]), ref, n_changed, len(gone)))
    gone_repos = sorted({k.split("/", 1)[0] for k in old} - {r["id"] for r in rows})
    for k in old:
        if k.split("/", 1)[0] in gone_repos:
            _, coll, did = k.split("/", 2)
            writes.append({"op": "delete", "collection": "repos/%s/%s" % (k.split("/", 1)[0], coll), "doc_id": did})
    index = {"repos": rows, "primary": {"name": a.project or cfg.get("project") or os.path.basename(os.path.abspath(a.repo))},
             "mirrored_at": now()}
    writes.insert(0, {"op": "set", "collection": "kit_repos", "doc_id": "_index", "data": index})
    vers = json.load(open(a.versions, encoding="utf-8")) if a.versions else {}
    for w in writes:                                   # ArtifactData refuses a write to an existing doc without its version
        v = vers.get("%s/%s" % (w["collection"], w["doc_id"]))
        if v:
            w["if_version"] = int(v)
    write_batches(a.out, writes, "boards")
    with open(state_p + ".next", "w", encoding="utf-8") as fh:
        json.dump(new, fh)
    for line in report:
        print(line)
    print("%d attached boards, %d writes. Once every batch is written: mv %s.next %s (the next run then sends only "
          "what changed)." % (len(rows), len(writes), state_p, state_p))
    return rows, writes


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
        writes.append({"op": "set", "collection": prefix(a) + "kit_docs", "doc_id": doc_id(row["path"]), "data": res})
        kept.append(row)
    index = {"docs": kept, "assets": {}, "mirrored_at": now(), "total": len(kept)}
    prev = os.path.join(a.out, "uploaded.json")
    if os.path.exists(prev):
        index["assets"] = json.load(open(prev, encoding="utf-8"))
    writes.insert(0, {"op": "set", "collection": prefix(a) + "kit_docs", "doc_id": "_index", "data": index})
    write_batches(a.out, writes, "docs" + ("-" + repo_id(a.into) if a.into else ""))
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
        writes.append({"op": "set", "collection": prefix(a) + "kit_git", "doc_id": doc_id(sha), "data": res})
    write_batches(a.out, writes, "diffs" + ("-" + repo_id(a.into) if a.into else ""))
    print("%d commits named in posts, %d diffs mirrored." % (len(shas), len(writes)))


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("docs"); d.add_argument("--kit", required=True); d.add_argument("--repo", default="."); d.add_argument("--out", default="/tmp/kas")
    s = sub.add_parser("assets"); s.add_argument("--uploaded", required=True); s.add_argument("--out", default="/tmp/kas")
    g = sub.add_parser("diffs"); g.add_argument("--kit", required=True); g.add_argument("--repo", default=".")
    g.add_argument("--posts", required=True); g.add_argument("--out", default="/tmp/kas")
    for x in (d, g):
        x.add_argument("--into", default="", help="an attached repo's name: write under repos/<id>/ (run from its clone)")
    b = sub.add_parser("boards"); b.add_argument("--repo", default="."); b.add_argument("--out", default="/tmp/kas")
    b.add_argument("--clone", action="append", default=[], help="name=path of an attached repo's clone")
    b.add_argument("--full", action="store_true", help="write every post and comment again")
    b.add_argument("--attach", action="append", default=[], help="name=owner/repo: attach a repo not in .kit/config.json "
                   "(every run lists every attached repo: a repo left out disappears from the board)")
    b.add_argument("--project", default="", help="the primary board's name (default: .kit/config.json project)")
    b.add_argument("--versions", default="", help='JSON {"<collection>/<doc_id>": version} of the documents already in the Artifact '
                   "(from ArtifactData list/get), added as if_version so existing documents can be rewritten")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    {"docs": cmd_docs, "assets": cmd_assets, "diffs": cmd_diffs, "boards": cmd_boards}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
