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
Artifact needs its version: pass --versions (a {"<collection>/<doc_id>": version} map from ArtifactData list). `docs`, `assets` and `diffs` take --into <id> to
fill an attached board's Repo Docs reader and diffs (repos/<id>/kit_docs, repos/<id>/kit_git) from its clone.
`boards-back` is the other way: comments written in the Artifact on an attached board's posts (dump them with
`ArtifactData list collection repos/<id>/comments, out_dir <dump>`) go to that repo's board-mirror branch, where its
next `kit.py reconcile` adds them to its board:

    python3 -I kit_artifact_sync.py boards-back --repo . --from <dump> --clone board=/home/user/board

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
    d = os.path.join(kit, "kit", "board")
    sys.path.insert(0, d if os.path.isdir(d) else os.path.join(kit, ".kit") if os.path.isdir(os.path.join(kit, ".kit")) else kit)
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


def attached_prefix(repo):
    """The prefix of the Artifact board a repo is attached to (`kit.artifact.prefix` or `.repo` in its
    .kit/config.json, e.g. "repos/board/" for the kit's own repo, CL13), or "" for a board of its own."""
    try:
        c = json.load(open(os.path.join(repo, ".kit", "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    a = (c.get("kit") or {}).get("artifact")
    if not isinstance(a, dict):
        return ""
    if a.get("prefix") is not None:
        p = str(a["prefix"])
        return p if not p or p.endswith("/") else p + "/"
    return ("repos/%s/" % repo_id(a["repo"])) if a.get("repo") else ""


def prefix(a):
    into = getattr(a, "into", "") or ""
    if into:
        return "repos/%s/" % repo_id(into)
    return attached_prefix(getattr(a, "repo", "") or ".") if getattr(a, "repo", None) else ""


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


def cmd_boards_back(a):
    """The other way: comments written in this Artifact on an attached board's posts (the owner's, or a session's that
    answered here) go to that repository's own board, as files on its board-mirror branch (committed, pushed), where
    its next `kit.py reconcile` (its terminal keyword, its web takeover) adds them. Read the Artifact first:
    `ArtifactData list collection repos/<id>/comments, out_dir <dump>` for each attached board, then --from <dump>."""
    import hashlib
    import subprocess
    import tempfile
    try:
        cfg = json.load(open(os.path.join(a.repo, ".kit", "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    clones = dict(x.split("=", 1) for x in (a.clone or []) if "=" in x)
    portfolio = list(cfg.get("portfolio") or []) + [{"name": n, "github": g} for n, _, g in
                                                    (x.partition("=") for x in (a.attach or []))]
    state_p = os.path.join(a.out, "boards-state.json")
    state = json.load(open(state_p, encoding="utf-8")) if os.path.exists(state_p) else {}
    total = 0
    for item in portfolio:
        name = str(item.get("name") or "")
        rid = repo_id(name)
        dump = os.path.join(a.from_dir, "repos", rid, "comments")
        clone = next((c for c in (clones.get(name), clones.get(rid), item.get("path"))
                      if c and os.path.isdir(os.path.join(c, ".git"))), None)
        if not os.path.isdir(dump) or not clone:
            print("%s: %s" % (name, "no clone here" if not clone else "nothing read from the Artifact (%s)" % dump))
            continue
        ref, board = read_mirror(clone)
        if not ref:
            print("%s: no board-mirror branch" % name)
            continue
        new = {}
        for f in glob.glob(os.path.join(dump, "*.json")):
            did = os.path.basename(f)[:-5]
            data = _read_json_file(f)
            if isinstance(data, dict) and did not in board["comments"]:
                new[did] = data
        if not new:
            print("%s: nothing new to send back" % name)
            continue
        branch = ref.split("/", 1)[-1] if ref.startswith("origin/") else ref
        wt = tempfile.mkdtemp(prefix="kit-back-")
        os.rmdir(wt)
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, ref], check=True, capture_output=True)
        try:
            os.makedirs(os.path.join(wt, "board", "comments"), exist_ok=True)
            for did, data in new.items():
                with open(os.path.join(wt, "board", "comments", did + ".json"), "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
                    fh.write("\n")
                body = json.dumps(data, ensure_ascii=False, sort_keys=True)
                state["%s/comments/%s" % (rid, doc_id(did))] = hashlib.sha1(body.encode("utf-8")).hexdigest()
            subprocess.run(["git", "-C", wt, "add", "board/comments"], check=True, capture_output=True)
            msg = "board: %d comment(s) written in %s's Artifact board" % (len(new), cfg.get("project") or "the primary")
            subprocess.run(["git", "-C", wt, "commit", "-q", "-m", msg], check=True, capture_output=True)
            r = subprocess.run(["git", "-C", wt, "push", "-q", "origin", "HEAD:" + branch], capture_output=True, text=True)
            print("%s: %d comment(s) to its %s branch: %s" % (name, len(new), branch,
                                                            "pushed" if r.returncode == 0 else "PUSH REFUSED: " + r.stderr.strip()[-200:]))
            total += len(new) if r.returncode == 0 else 0
        finally:
            subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
    os.makedirs(a.out, exist_ok=True)
    with open(state_p, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    return total


def _read_json_file(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


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
    # CL10: an attached board's pictures go to its own index (repos/<id>/kit_docs/_index): the page reads that one when
    # the board is shown, so a map written to the primary's index left every picture of the attached board broken
    write_batches(a.out, [{"op": "update", "collection": prefix(a) + "kit_docs", "doc_id": "_index", "data": {"assets": up}}],
                  "assets" + ("-" + repo_id(a.into) if a.into else ""))
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


# ---------------------------------------------------------------- files a post names (CL10, the owner 2026-11-23)
# "All the pictures don't show up here": every file a post or comment names that the board's viewers open is put in
# the Artifact when the post is written (`kit.py artifact push` prints these steps; `files` alone does the same for
# posts written with ArtifactData). Limits measured on the Artifact's asset store (2026-11-23): 20 MiB a file (SVG
# 2 MiB), up to 25 files a call (a text file in a call of its own); the store's total shows in `Artifact` `list`
# `scope: "assets"` (a store-full refusal comes from the upload call itself); accepted types
# png jpg jpeg gif webp svg mp4 webm pdf docx xlsx woff woff2 ttf otf csv md markdown json txt ts css js mjs cjs.
# Markdown and HTML documents are not uploaded: they are mirrored as text in kit_docs (2 MiB a file, cut to fit a db
# document). Anything else (audio, .mov, .avif, a larger file) stays a repo link, and the steps say so for the post.
UPLOAD_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp4", ".webm"}   # viewer kinds the store takes
MIRROR_EXT = {".md", ".html", ".htm"}
MAX_ASSET, MAX_SVG, PER_CALL = 20 * 1024 * 1024, 2 * 1024 * 1024, 25
REF_RE = re.compile(r"`([^`\s]+?\.[A-Za-z0-9]{2,5})(?:\s*§[^`]*)?`|\]\((?!https?:|/_blob/|#)([^)\s]+?\.[A-Za-z0-9]{2,5})\)")


def post_refs(doc):
    """The repo paths a post or comment names: its images and artifacts, code spans and links in its text."""
    out = []
    for k in ("images", "artifacts"):
        for v in doc.get(k) or []:
            if isinstance(v, str):
                out.append(v)
    for k in ("body", "text"):
        for m in REF_RE.finditer(str(doc.get(k) or "")):
            out.append(m.group(1) or m.group(2))
    seen, refs = set(), []
    for r in out:
        r = r.split("#")[0].split("?")[0].strip().lstrip("./")
        if r and r not in seen and not r.startswith(("/_blob/", "http:", "https:", "data:")) and ".." not in r.split("/"):
            seen.add(r); refs.append(r)
    return refs


def files_state_path(repo, into):
    base = os.environ.get("KIT_ARTIFACT_DIR") or os.path.join(repo, ".kit", "run", "artifact")   # kit.py's Artifact copy
    return os.path.join(base, "files-%s.json" % (repo_id(into) if into else "primary"))


def load_files_state(repo, into):
    try:
        st = json.load(open(files_state_path(repo, into), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("assets", {}); st.setdefault("docs", {})
    return st


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_files(repo, docs, st):
    """{upload: [[path, abs, sha]], mirror: [path], have: {path: url}, links: [[path, why]]} for what the docs name."""
    plan = {"upload": [], "mirror": [], "have": {}, "links": []}
    for d in docs:
        for ref in post_refs(d):
            full = os.path.join(repo, ref)
            ext = os.path.splitext(ref)[1].lower()
            if not os.path.isfile(full):
                continue                                   # not a repo file (a word with a dot, a URL path)
            if any(ref == x[0] for x in plan["upload"]) or ref in plan["mirror"] or ref in plan["have"]:
                continue
            size = os.path.getsize(full)
            if ext in MIRROR_EXT:
                if size > 2 * 1024 * 1024:
                    plan["links"].append([ref, "over the 2 MiB a mirrored document can hold"])
                elif st["docs"].get(doc_id(ref), {}).get("sha") != sha256_of(full):
                    plan["mirror"].append(ref)
            elif ext in UPLOAD_EXT:
                cap = MAX_SVG if ext == ".svg" else MAX_ASSET
                if size > cap:
                    plan["links"].append([ref, "%.1f MiB, over the store's %d MiB a file" % (size / 1048576.0, cap // 1048576)])
                    continue
                sha = sha256_of(full)
                known = st["assets"].get(ref) or {}
                if known.get("sha") == sha and known.get("url"):
                    plan["have"][ref] = known["url"]
                else:
                    plan["upload"].append([ref, os.path.abspath(full), sha])
            elif ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".mov", ".m4v", ".avif"):
                plan["links"].append([ref, "the Artifact's store does not take %s files" % ext])
            elif ref not in (x[0] for x in plan["links"]):
                plan["links"].append([ref, "the board's viewers do not open %s files" % (ext or "extension-less")])
    return plan


def plan_text(plan, board, out, into):
    """The steps, in order: the uploads, then `files-sent`."""
    lines = []
    ups = plan["upload"]
    if ups:
        lines.append("Files the posts name, for the Artifact (%d to upload):" % len(ups))
        for n in range(0, len(ups), PER_CALL):
            lines.append("- `Artifact` `publish`, `url` %s, `asset: true`, `file_paths`: %s" % (
                board, json.dumps([u[1] for u in ups[n:n + PER_CALL]])))
        lines.append("  Save what each call returns as {\"<repo path>\": \"/_blob/<id>\"} in %s (paths: %s)." % (
            os.path.join(out, "uploaded.json"), ", ".join(u[0] for u in ups)))
    if ups or plan["mirror"]:
        lines.append("Then: `ArtifactData` `get`, `url` %s, `collection: \"%skit_docs\"`, `doc_id: \"_index\"`, `out_dir: \"%s\"`; "
                     "and `python3 -I .kit/kit_artifact_sync.py files-sent --repo . --out %s%s --index <the saved _index.json> "
                     "--index-version <its version>` (or `--index-missing` if there is none): it records them and writes the "
                     "batch that merges them into the map (kit_docs) for the page%s." % (
                         board, ("repos/%s/" % repo_id(into)) if into else "", os.path.join(out, "index"), out,
                         " --into %s" % into if into else "", " and mirrors %d document(s)" % len(plan["mirror"]) if plan["mirror"] else ""))
    for ref, why in plan["links"]:
        lines.append("- `%s` stays a repo link (%s): say so in the post." % (ref, why))
    return "\n".join(lines) + ("\n" if lines else "")


def read_docs(paths):
    docs = []
    for p in paths:
        for f in ([p] if os.path.isfile(p) else glob.glob(os.path.join(p, "**", "*.json"), recursive=True)):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict):
                docs.append(d.get("data") if isinstance(d.get("data"), dict) else d)
    return docs


def cmd_files(a):
    st = load_files_state(a.repo, a.into)
    plan = plan_files(a.repo, read_docs(a.docs), st)
    os.makedirs(a.out, exist_ok=True)
    json.dump(plan, open(os.path.join(a.out, "plan.json"), "w", encoding="utf-8"), indent=1)
    txt = plan_text(plan, a.board or "<the board's claude.ai/artifact url>", a.out, a.into)
    print(txt or "Nothing to put in the Artifact: every file these posts name is there already (or is not a repo file).", end="")


def cmd_files_sent(a):
    st = load_files_state(a.repo, a.into)
    try:
        plan = json.load(open(os.path.join(a.out, "plan.json"), encoding="utf-8"))
    except (OSError, ValueError):
        plan = {"upload": [], "mirror": [], "have": {}}
    up = {}
    if os.path.exists(os.path.join(a.out, "uploaded.json")):
        up = json.load(open(os.path.join(a.out, "uploaded.json"), encoding="utf-8"))
    sha = {u[0]: u[2] for u in plan.get("upload") or []}
    for path, url in up.items():
        url = url if url.startswith(("/_blob/", "https://")) else "/_blob/" + url
        st["assets"][path] = {"url": url, "sha": sha.get(path) or ""}
    missing = [u[0] for u in plan.get("upload") or [] if u[0] not in up]
    writes = []
    amap = {k: v["url"] for k, v in st["assets"].items() if v.get("url")}
    if up or amap:
        # The index also holds the Repo Docs reader's list (docs, total): it is never replaced. Its current copy is read
        # (ArtifactData get, out_dir) and the map merged into it with an update pinned to its version; only an index
        # confirmed missing is created. (O50's check, 2026-11-23: a `set` here would have wiped docs/total.)
        if a.index_missing:
            e = {"op": "set", "collection": prefix(a) + "kit_docs", "doc_id": "_index", "data": {"assets": amap, "mirrored_at": now()}}
        elif a.index and a.index_version:
            cur = json.load(open(a.index, encoding="utf-8"))
            cur = cur.get("data") if isinstance(cur.get("data"), dict) else cur
            merged = dict(cur.get("assets") or {}); merged.update(amap)
            e = {"op": "update", "collection": prefix(a) + "kit_docs", "doc_id": "_index", "if_version": int(a.index_version),
                 "data": {"assets": merged, "mirrored_at": now()}}
        else:
            raise SystemExit("files-sent needs the board's current %skit_docs/_index: `ArtifactData` `get` it with an out_dir, "
                             "then pass --index <that file> --index-version <its version>; or --index-missing if the get "
                             "says there is none. Nothing was written." % prefix(a))
        writes.append(e)
    here = os.path.dirname(os.path.abspath(__file__))       # kit/claude in the kit, .kit in an install
    bs = server(a.kit or (os.path.dirname(os.path.dirname(here)) if os.path.basename(here) == "claude" else here))
    for ref in list(plan.get("mirror") or []) + list(up):
        res, err = bs.read_repo_doc(a.repo, ref)
        if err or not res:
            continue
        if res.get("kind") == "html":
            res["url"] = ""
        while len(json.dumps(res, ensure_ascii=False).encode("utf-8")) > MAX_DOC:
            res["content"] = res["content"][:int(len(res["content"]) * 0.8)]
            res["cut"] = True
        did = doc_id(ref)
        e = {"op": "set", "collection": prefix(a) + "kit_docs", "doc_id": did, "data": res}
        v = (st["docs"].get(did) or {}).get("version")
        if v:
            e["if_version"] = int(v)
        writes.append(e)
        st["docs"][did] = {"version": int(v or 0) + 1, "sha": sha256_of(os.path.join(a.repo, ref))}
    os.makedirs(os.path.dirname(files_state_path(a.repo, a.into)), exist_ok=True)
    json.dump(st, open(files_state_path(a.repo, a.into), "w", encoding="utf-8"), indent=1)
    write_batches(a.out, writes, "files" + ("-" + repo_id(a.into) if a.into else ""))
    if missing:
        print("Not in uploaded.json, so not mapped: %s" % ", ".join(missing))
    print("A refused write names the document's real version: `files-pin --into <id> <doc_id>=<version>` for a "
          "mirrored document, or get _index again for the map, then files-sent again.")


def cmd_files_pin(a):
    st = load_files_state(a.repo, a.into)
    for pair in a.pairs:
        k, _, v = pair.partition("=")
        if not v.strip().isdigit():
            continue
        if k == "_index":
            st["index_version"] = int(v)
        else:
            st["docs"].setdefault(k, {})["version"] = int(v)
    os.makedirs(os.path.dirname(files_state_path(a.repo, a.into)), exist_ok=True)
    json.dump(st, open(files_state_path(a.repo, a.into), "w", encoding="utf-8"), indent=1)
    print("pinned %d" % len(a.pairs))


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("docs"); d.add_argument("--kit", required=True); d.add_argument("--repo", default="."); d.add_argument("--out", default="/tmp/kas")
    s = sub.add_parser("assets"); s.add_argument("--uploaded", required=True); s.add_argument("--out", default="/tmp/kas")
    g = sub.add_parser("diffs"); g.add_argument("--kit", required=True); g.add_argument("--repo", default=".")
    g.add_argument("--posts", required=True); g.add_argument("--out", default="/tmp/kas")
    f = sub.add_parser("files", help="what the posts (ArtifactData JSON, files or folders) name, for the Artifact")
    f.add_argument("docs", nargs="+"); f.add_argument("--repo", default="."); f.add_argument("--out", default="/tmp/kas-files")
    f.add_argument("--board", default="")
    fs = sub.add_parser("files-sent", help="after the uploads: record them, and the batch that maps them")
    fs.add_argument("--repo", default="."); fs.add_argument("--out", default="/tmp/kas-files"); fs.add_argument("--kit", default="")
    fs.add_argument("--index", default="", help="the current kit_docs/_index, saved by ArtifactData get with out_dir")
    fs.add_argument("--index-version", type=int, default=0); fs.add_argument("--index-missing", action="store_true")
    fp = sub.add_parser("files-pin"); fp.add_argument("pairs", nargs="+"); fp.add_argument("--repo", default=".")
    fp.add_argument("--out", default="/tmp/kas-files")
    for x in (d, g, s, f, fs, fp):
        x.add_argument("--into", default="", help="an attached repo's name: write under repos/<id>/ (run from its clone)")
    b = sub.add_parser("boards"); b.add_argument("--repo", default="."); b.add_argument("--out", default="/tmp/kas")
    b.add_argument("--clone", action="append", default=[], help="name=path of an attached repo's clone")
    b.add_argument("--full", action="store_true", help="write every post and comment again")
    b.add_argument("--attach", action="append", default=[], help="name=owner/repo: attach a repo not in .kit/config.json "
                   "(every run lists every attached repo: a repo left out disappears from the board)")
    b.add_argument("--project", default="", help="the primary board's name (default: .kit/config.json project)")
    b.add_argument("--versions", default="", help='JSON {"<collection>/<doc_id>": version} of the documents already in the Artifact '
                   "(from ArtifactData list/get), added as if_version so existing documents can be rewritten")
    k = sub.add_parser("boards-back", help="comments written in the Artifact on an attached board -> that repo's board-mirror")
    k.add_argument("--repo", default="."); k.add_argument("--out", default="/tmp/kas")
    k.add_argument("--from", dest="from_dir", required=True, help="the ArtifactData out_dir holding repos/<id>/comments")
    k.add_argument("--clone", action="append", default=[]); k.add_argument("--attach", action="append", default=[])
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    {"files": cmd_files, "files-sent": cmd_files_sent, "files-pin": cmd_files_pin, "docs": cmd_docs, "assets": cmd_assets, "diffs": cmd_diffs, "boards": cmd_boards, "boards-back": cmd_boards_back}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
