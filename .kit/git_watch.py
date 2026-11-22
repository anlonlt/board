#!/usr/bin/env python3
"""Lightweight git listener for hybrid mode (Claude Code Web Artifact <-> git <-> agy on a computer).

    python3 -I .kit/git_watch.py [--branches board-mirror,main] [--runner computer]
                                 [--interval 30] [--timeout 300] [--once]

Polls `origin` with lightweight `git ls-remote` (no full fetch until a watched ref actually moves).
When a watched branch advances, fetches it, summarizes what changed (new commits, and on `board-mirror`
any new/updated posts or comments matching `--runner` or mentioning `@agy`), saves the last-seen SHA
in `.kit/board_data/git_watch_state.json` (gitignored), prints one JSON object to stdout, and exits 0.

In agy, run it as a background command (`WaitMsBeforeAsync: 500`): agy stays asleep (0 tokens) and
automatically wakes when this command exits with the new event.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def load_config(root):
    p = os.path.join(root, ".kit", "config.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remote_heads(root, branches):
    rc, out, err = git(root, "ls-remote", "--heads", "origin", *branches)
    if rc != 0:
        return None, err
    res = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            res[parts[1][len("refs/heads/"):]] = parts[0]
    return res, ""


def changed_files(root, old_sha, new_sha):
    if not old_sha:
        rc, out, _ = git(root, "ls-tree", "-r", "--name-only", new_sha)
    else:
        rc, out, _ = git(root, "diff", "--name-only", old_sha, new_sha)
    return [l for l in out.splitlines() if l] if rc == 0 else []


def inspect_mirror(root, new_sha, paths, runner=None):
    """Read only the changed board/posts/*.json and board/comments/*.json from git object store."""
    posts, comments = [], []
    for p in paths:
        if not p.endswith(".json"):
            continue
        if not (p.startswith("board/posts/") or p.startswith("board/comments/")):
            continue
        rc, raw, _ = git(root, "show", "%s:%s" % (new_sha, p))
        if rc != 0:
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        doc_id = os.path.basename(p)[:-5]
        if p.startswith("board/posts/"):
            if runner and doc.get("runner") != runner and doc.get("part") != runner:
                continue
            posts.append({"id": doc_id, "title": doc.get("title", ""), "column": doc.get("column", ""),
                          "runner": doc.get("runner", ""), "part": doc.get("part", ""), "priority": doc.get("priority", 0)})
        else:
            text = str(doc.get("text") or "")
            if runner and ("@agy" not in text.lower()) and (runner.lower() not in text.lower()) and doc.get("from") != "owner":
                continue
            comments.append({"id": doc_id, "post_id": doc.get("post_id", ""), "from": doc.get("from", ""),
                             "text": text[:240], "created_at": doc.get("created_at", "")})
    return posts, comments


def check_once(root, branches, mirror_branch, state_path, runner=None):
    try:
        state = json.load(open(state_path, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    heads, err = remote_heads(root, branches)
    if heads is None:
        return {"event": "error", "error": err}
    if not state:
        # First run: record baseline SHAs so we only wake on new pushes (unless --init-emit)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(heads, fh, indent=1, sort_keys=True)
        return None
    changes = []
    for b, sha in heads.items():
        old = state.get(b)
        if sha == old:
            continue
        git(root, "fetch", "-q", "origin", b)
        files = changed_files(root, old, sha)
        item = {"branch": b, "old": old or "", "new": sha, "files": files[:50]}
        if b == mirror_branch:
            posts, comments = inspect_mirror(root, sha, files, runner=runner)
            item["posts"] = posts
            item["comments"] = comments
        changes.append(item)
    if not changes:
        return None
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(heads, fh, indent=1, sort_keys=True)
    return {"event": "git_update", "changes": changes}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lightweight git listener for hybrid agy/Claude Code Web mode")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--branches", default=None, help="Comma-separated branches to watch (default: mirror + main_branch)")
    ap.add_argument("--runner", default=None, help="Filter mirror posts/comments for this runner name")
    ap.add_argument("--interval", type=float, default=30.0, help="Seconds between ls-remote checks")
    ap.add_argument("--timeout", type=float, default=300.0, help="Seconds before exiting with timeout event")
    ap.add_argument("--once", action="store_true", help="Check once and exit immediately")
    ap.add_argument("--state", default=None, help="State JSON path (default .kit/board_data/git_watch_state.json)")
    a = ap.parse_args(argv)

    root = os.path.abspath(a.root)
    cfg = load_config(root)
    mirror_branch = cfg.get("mirror", {}).get("branch", "board-mirror")
    main_branch = cfg.get("kit", {}).get("main_branch", "main")
    branches = [b.strip() for b in a.branches.split(",") if b.strip()] if a.branches else list(dict.fromkeys([mirror_branch, main_branch]))
    state_path = a.state or os.path.join(root, ".kit", "board_data", "git_watch_state.json")

    deadline = time.monotonic() + max(0.1, a.timeout)
    while True:
        ev = check_once(root, branches, mirror_branch, state_path, runner=a.runner)
        if ev is not None:
            print(json.dumps(ev, ensure_ascii=False))
            return 0 if ev.get("event") != "error" else 1
        if a.once:
            print(json.dumps({"event": "idle"}))
            return 0
        rem = deadline - time.monotonic()
        if rem <= 0:
            print(json.dumps({"event": "timeout"}))
            return 0
        time.sleep(min(a.interval, rem))


if __name__ == "__main__":
    sys.exit(main())
