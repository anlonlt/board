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
    check("waits on the owner, from awaits", b.get("state") == "waits" and b.get("waits") is True)
    check("its post, its next step", b.get("post") == "P-bulletin" and b.get("note") == "Next: draft 24 Nov")
    check("a row without steps shows its next step", rows[1]["steps"] == ["someday"] if len(rows) > 1 else False)
print("%d failed" % fails); sys.exit(1 if fails else 0)
