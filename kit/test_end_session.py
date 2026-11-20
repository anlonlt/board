#!/usr/bin/env python3
"""end_session.py on planted repositories: a branch lands on the trunk; a moved trunk is merged first; a conflict
stops with nothing pushed; without the standing permission only the branch is pushed. python3 -I kit/test_end_session.py"""
import json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0
def check(n, ok):
    global fails; print(("ok   " if ok else "FAIL ") + n); fails += not ok
def g(d, *a):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True)
def clone(origin, d, perm=True):
    subprocess.run(["git", "clone", "-q", origin, d], capture_output=True)
    os.makedirs(os.path.join(d, ".kit"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "end_session.py"), os.path.join(d, ".kit"))
    json.dump({"kit": {"main_branch": "main", "trunk_push": perm}}, open(os.path.join(d, ".kit", "config.json"), "w"))
    open(os.path.join(d, ".git", "info", "exclude"), "a").write(".kit/\n")
def write(d, f, t):
    open(os.path.join(d, f), "w").write(t); g(d, "add", f); g(d, "commit", "-qm", f)
def run(d):
    return subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "end_session.py")], capture_output=True, text=True)
with tempfile.TemporaryDirectory() as t:
    origin = os.path.join(t, "o.git"); subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin])
    seed = os.path.join(t, "seed"); subprocess.run(["git", "clone", "-q", origin, seed], capture_output=True)
    g(seed, "checkout", "-q", "-b", "main"); write(seed, "a.txt", "1\n"); g(seed, "push", "-q", "origin", "main")
    a = os.path.join(t, "a"); clone(origin, a); g(a, "checkout", "-q", "-b", "work"); write(a, "b.txt", "b\n")
    r = run(a)
    check("a branch lands on the trunk", r.returncode == 0 and g(seed, "ls-remote", "origin", "main").stdout.split()[0] == g(a, "rev-parse", "HEAD").stdout.strip())
    c = os.path.join(t, "c"); clone(origin, c); write(c, "c.txt", "c\n"); g(c, "push", "-q", "origin", "main")
    write(a, "d.txt", "d\n"); r = run(a)
    files = g(seed, "fetch", "-q") and g(seed, "ls-tree", "--name-only", "origin/main").stdout.split()
    check("a moved trunk is merged first, nothing lost", r.returncode == 0 and {"c.txt", "d.txt"} <= set(files))
    write(c, "x.txt", "one\n"); g(c, "pull", "-q", "--no-rebase", "origin", "main"); g(c, "push", "-q", "origin", "main")
    write(a, "x.txt", "two\n"); before = g(seed, "ls-remote", "origin", "main").stdout.split()[0]
    r = run(a)
    check("a conflict stops, names the file, pushes nothing to the trunk", r.returncode == 1 and "x.txt" in r.stdout
          and g(seed, "ls-remote", "origin", "main").stdout.split()[0] == before and not g(a, "status", "--porcelain").stdout.strip())
    e = os.path.join(t, "e"); clone(origin, e, perm=False); g(e, "checkout", "-q", "-b", "only"); write(e, "e.txt", "e\n")
    before = g(seed, "ls-remote", "origin", "main").stdout.split()[0]; r = run(e)
    check("without the standing permission, only the branch is pushed", r.returncode == 0 and "only" in g(seed, "ls-remote", "origin").stdout
          and g(seed, "ls-remote", "origin", "main").stdout.split()[0] == before)
    open(os.path.join(e, "e.txt"), "w").write("dirty"); r = run(e)
    check("uncommitted changes: nothing pushed", r.returncode == 1 and "not committed" in r.stdout)
print("%d failed" % fails); sys.exit(1 if fails else 0)
