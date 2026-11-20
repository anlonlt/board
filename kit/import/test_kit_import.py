#!/usr/bin/env python3
"""kit_import.py on a planted state: memory files, awaits, preferences, posts; a rerun keeps what exists."""
import json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0
def check(n, ok):
    global fails; print(("ok   " if ok else "FAIL ") + n); fails += not ok
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, ".kit")); shutil.copy(os.path.join(HERE, "kit_import.py"), os.path.join(d, ".kit"))
    st = {"preferences": ["short steps"], "projects": [{"id": "Weekly Bulletin", "title": "Weekly bulletin", "next": "draft 24 Nov",
          "where": ["every Friday"], "ids": {"page": "News"}, "waiting": [{"text": "new layout?", "default": "Keep", "options": ["New", "Keep"]}]}]}
    sf = os.path.join(d, "s.json"); json.dump(st, open(sf, "w"))
    run = lambda: subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_import.py"), sf, "--out", os.path.join(d, "o"),
                                  "--now", "2026-11-20T00:00:00Z", "--session", "S1"], capture_output=True, text=True)
    r = run()
    s = open(os.path.join(d, ".story", "state", "weekly-bulletin.md")).read()
    check("state file: Next line second, ids kept", s.splitlines()[1] == "Next: draft 24 Nov" and "page: News" in s)
    check("awaits: the waiting item with its default", "- [weekly-bulletin] new layout? (default: Keep)" in open(os.path.join(d, ".story", "awaits.md")).read())
    check("preferences written", "short steps" in open(os.path.join(d, ".story", "preferences.md")).read())
    q = json.load(open(os.path.join(d, "o", "posts", "Q-weekly-bulletin-1.json")))
    check("question post with one-tap options and the default", q["column"] == "question" and q["options"] == ["New", "Keep"] and q["default_option"] == "Keep")
    w = json.loads(r.stdout.strip().splitlines()[-1])
    check("batch writes printed, 2 posts", len(w) == 2 and w[0]["doc_id"] == "P-weekly-bulletin")
    open(os.path.join(d, ".story", "state", "weekly-bulletin.md"), "a").write("my edit\n")
    r = run()
    check("a rerun keeps an edited state and adds no duplicate await", "KEPT" in r.stdout and "my edit" in open(os.path.join(d, ".story", "state", "weekly-bulletin.md")).read()
          and open(os.path.join(d, ".story", "awaits.md")).read().count("new layout?") == 1)
print("%d failed" % fails); sys.exit(1 if fails else 0)
