#!/usr/bin/env python3
"""The string table (issue #3 item 4): every row is found in the page, a full pseudo-translation leaves every id, field
and text sent to Claude as it was, and the translated page still runs (test_page.js on it). python3 -I kit/board/test_strings.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strings  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


page = open(os.path.join(HERE, "board.template.html"), encoding="utf-8").read()
table = strings.load_table()
fake = {r["en"]: "⟦" + r["en"] + "⟧" for r in table}
out, used, missing = strings.apply(page, table, fake)
check("every row of the table is found in the page (%d rows)" % len(table), used == len(table) and not missing)
ids = lambda p: sorted(re.findall(r'\bid="([^"]+)"', p))
check("ids unchanged", ids(out) == ids(page))
for keep in ('"Answer: "', '"\\uD83D\\uDC4D Like: he approves this"', '" [post "', '"_orchestrator"', 'from: state.isOwner ? "owner" : "viewer"'):
    check("kept as is: " + keep, keep in out)
check("the visible words changed", ">⟦Waiting for you⟧<" in out and 'placeholder="⟦Write a comment⟧"' in out)
check("an empty translation changes nothing", strings.apply(page, table, {})[0] == page)
check("the table is current: a fresh extract finds no visible string it lacks",
      not ({(r["en"], r["kind"]) for r in strings.extract(page)} - {(r["en"], r["kind"]) for r in table}
           - {(e, "js") for e in strings.extract_exclusions()}))
node = shutil.which("node")
if node:
    repo_root = os.path.dirname(os.path.dirname(HERE))
    has_pw = subprocess.run(
        [node, "-e", "require.resolve('playwright')"],
        capture_output=True, text=True, cwd=repo_root,
    ).returncode == 0
    if not has_pw:
        print("SKIP the translated page in a browser: playwright not installed in node (say so; not a pass)")
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(out)
            tmp_html = fh.name
        try:
            r = subprocess.run([node, os.path.join(HERE, "test_page.js")], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, KIT_PAGE=tmp_html), cwd=repo_root)
            check("the translated page runs: test_page.js passes on it (%s)" % (r.stdout.strip().splitlines() or ["?"])[-1],
                  r.returncode == 0 and "0 failed" in r.stdout)
        finally:
            try:
                os.unlink(tmp_html)
            except OSError:
                pass
else:
    print("SKIP the translated page in a browser: no node here (say so; not a pass)")
print("%d failed" % fails)
sys.exit(1 if fails else 0)
