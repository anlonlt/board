#!/usr/bin/env python3
"""Tests for kit/git_watch.py on a planted bare origin + mirror branch. python3 -I kit/test_git_watch.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def g(d, *args):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"] + list(args),
                          capture_output=True, text=True, check=True)


def run_watch(repo, *args):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "git_watch.py"), "--root", repo] + list(args),
                       capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout) if r.stdout.strip() else {}


with tempfile.TemporaryDirectory() as tmp:
    origin = os.path.join(tmp, "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    work = os.path.join(tmp, "work")
    subprocess.run(["git", "clone", "-q", origin, work], check=True)
    g(work, "checkout", "-q", "-b", "main")
    open(os.path.join(work, "README.md"), "w").write("hi\n")
    g(work, "add", "README.md")
    g(work, "commit", "-qm", "init")
    g(work, "push", "-q", "origin", "main")

    # Create orphan board-mirror branch on origin
    mirror = os.path.join(tmp, "mirror")
    subprocess.run(["git", "clone", "-q", origin, mirror], check=True)
    g(mirror, "checkout", "-q", "--orphan", "board-mirror")
    g(mirror, "rm", "-rfq", ".")
    os.makedirs(os.path.join(mirror, "board", "posts"), exist_ok=True)
    os.makedirs(os.path.join(mirror, "board", "comments"), exist_ok=True)
    json.dump({"title": "Old", "column": "info"}, open(os.path.join(mirror, "board", "posts", "P0.json"), "w"))
    g(mirror, "add", ".")
    g(mirror, "commit", "-qm", "mirror init")
    g(mirror, "push", "-q", "origin", "board-mirror")

    # Local clone watching origin
    local = os.path.join(tmp, "local")
    subprocess.run(["git", "clone", "-q", origin, local], check=True)
    state = os.path.join(tmp, "state.json")

    rc, ev = run_watch(local, "--branches", "board-mirror,main", "--once", "--state", state)
    check("first run records baseline and returns idle", rc == 0 and ev.get("event") == "idle" and os.path.isfile(state))

    rc, ev = run_watch(local, "--branches", "board-mirror,main", "--once", "--state", state)
    check("no new commits stays idle", rc == 0 and ev.get("event") == "idle")

    # Push a new card for runner 'computer' and an owner comment to board-mirror
    json.dump({"title": "Run local build", "column": "todo", "runner": "computer", "priority": 1},
              open(os.path.join(mirror, "board", "posts", "C1.json"), "w"))
    json.dump({"post_id": "C1", "from": "owner", "text": "please run tests", "created_at": "2026-11-20T03:00:00Z"},
              open(os.path.join(mirror, "board", "comments", "m1.json"), "w"))
    g(mirror, "add", ".")
    g(mirror, "commit", "-qm", "card C1")
    g(mirror, "push", "-q", "origin", "board-mirror")

    rc, ev = run_watch(local, "--branches", "board-mirror,main", "--runner", "computer", "--once", "--state", state)
    ch = (ev.get("changes") or [{}])[0]
    check("wakes on board-mirror push with runner post and owner comment",
          rc == 0 and ev.get("event") == "git_update"
          and len(ch.get("posts", [])) == 1 and ch["posts"][0]["id"] == "C1"
          and len(ch.get("comments", [])) == 1 and ch["comments"][0]["id"] == "m1")

    rc, ev = run_watch(local, "--branches", "board-mirror,main", "--once", "--state", state)
    check("subsequent check is idle again", rc == 0 and ev.get("event") == "idle")

print("%d failed" % fails)
sys.exit(1 if fails else 0)
