#!/usr/bin/env python3
"""kit_cards.py on planted memory: one row per workstream, most urgent first, steps, waits."""
import json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0
def check(n, ok):
    global fails; print(("ok   " if ok else "FAIL ") + n); fails += not ok
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, ".kit")); os.makedirs(os.path.join(d, ".story", "state"))
    shutil.copy(os.path.join(HERE, "kit_cards.py"), os.path.join(d, ".kit"))
    open(os.path.join(d, ".story", "state", "later.md"), "w").write("# Later\nNext: someday\nPriority: 3\n")
    open(os.path.join(d, ".story", "state", "bulletin.md"), "w").write(
        "# Bulletin\nNext: draft 24 Nov\nPriority: 1 (Friday)\n\n## Steps\n- [x] plan\n- [ ] draft\n- [ ] publish\n")
    open(os.path.join(d, ".story", "awaits.md"), "w").write("# Awaits\n- [bulletin] P1 layout? (default: old)\n")
    out = os.path.join(d, "dash.json")
    r = subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_cards.py"), "--write", out, "--now", "2026-11-20T00:00:00Z"], capture_output=True, text=True)
    dd = json.load(open(out)) if r.returncode == 0 else {"rows": []}
    rows = dd["rows"]
    check("two rows, most urgent first", [x["name"] for x in rows] == ["Bulletin", "Later"])
    b = rows[0] if rows else {}
    check("steps and the current one", b.get("steps") == ["plan", "draft", "publish"] and b.get("at") == 1)
    check("waits on the owner, from awaits, in the page's state name", b.get("state") == "you" and "Waits on you: P1 layout?" in b.get("note", ""))
    check("a row with a next step is ready (a page state)", rows[1]["state"] == "ready" if len(rows) > 1 else False)
    check("its post, its next step", b.get("post") == "P-bulletin" and b.get("note", "").startswith("Next: draft 24 Nov"))
    check("a row without steps shows its next step", rows[1]["steps"] == ["someday"] if len(rows) > 1 else False)
    # trains (the owner, 2026-11-20): a project's parts under it, from their posts' `train`
    os.makedirs(os.path.join(d, "b", "posts")); os.makedirs(os.path.join(d, "docs", "prompts"))
    open(os.path.join(d, "docs", "prompts", "B1-draft.md"), "w").write("x")
    for pid, p in {"P-bulletin": {"column": "info", "title": "Bulletin"},
                   "B1-draft": {"part": "B1", "train": "bulletin", "column": "info", "status": "in progress", "created_at": "2026-11-20T01:00:00Z", "session_id": "s1"},
                   "B2-check": {"part": "B2", "train": "bulletin", "parent": "B1", "column": "question", "created_at": "2026-11-20T02:00:00Z"},
                   "X-other": {"part": "X", "column": "info"}}.items():
        json.dump(p, open(os.path.join(d, "b", "posts", pid + ".json"), "w"))
    tr = os.path.join(d, "trains.json")
    r = subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_cards.py"), "--write", out, "--trains", tr,
                        "--board", os.path.join(d, "b"), "--now", "2026-11-20T00:00:00Z"], capture_output=True, text=True)
    T = {x["part"]: x for x in json.load(open(tr))["rows"]} if r.returncode == 0 else {}
    check("trains: one root per project, its parts under it, the chain kept",
          T.get("P-bulletin", {}).get("children") == ["B1"] and T.get("B1", {}).get("children") == ["B2"] and "X" not in T)
    check("trains: states from the posts, prompt and session", T.get("B1", {}).get("state") == "running" and T.get("B2", {}).get("state") == "you"
          and T.get("B1", {}).get("prompt") == "docs/prompts/B1-draft.md" and T.get("B1", {}).get("session") == "s1")
    dd2 = json.load(open(out)) if r.returncode == 0 else {"rows": []}
    check("the dashboard row names its train", any(x.get("train") == "P-bulletin" for x in dd2["rows"]))
print("%d failed" % fails); sys.exit(1 if fails else 0)
