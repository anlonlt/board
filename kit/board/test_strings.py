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

# Issue #4: decoded emojis/arrows, ternary branches (on ? "A" : "B"), real newlines, and backward-compat old \uXXXX tables
check("table keys are decoded plain text (no \\\\u escapes in strings.en.json)",
      all("\\u" not in r["en"] and "\\n" not in r["en"] for r in table)
      and any(r["en"] == "👍 Like" for r in table)
      and any(r["en"] == "👍 Liked" for r in table)
      and any(r["en"] == "← Discussions" for r in table)
      and any(r["en"] == " posts\n*Exported " for r in table))

fr_issue4 = {
    "👍 Like": "👍 J'aime",
    "👍 Liked": "👍 Aimé",
    "← Board": "← Tableau",
    "← Discussions": "← Discussions FR",
    "You approved this": "Vous avez approuvé",
    "New discussion": "Nouvelle discussion",
    " posts\n*Exported ": " billets\n*Exporté ",
    # Also test old-style \uXXXX key and value compatibility:
    "\\u2713 Seen": "\\u2713 Vu",
    "New above \\u2191": "Nouveau plus haut \\u2191",
}
out_i4, used_i4, _ = strings.apply(page, table, fr_issue4)
check("issue #4: both ternary branches translated (👍 Liked / 👍 Like, ← Discussions / ← Board)",
      '"👍 Aimé"' in out_i4 and '"👍 J\'aime"' in out_i4
      and '"← Discussions FR"' in out_i4 and '"← Tableau"' in out_i4
      and '"Vous avez approuvé"' in out_i4 and '"Nouvelle discussion"' in out_i4)
check("issue #4: old \\\\uXXXX keys and values decode cleanly without double-escaping \\\\u in output",
      '"✓ Vu"' in out_i4 and '"Nouveau plus haut ↑"' in out_i4 and "\\\\u" not in out_i4)
check("issue #4: newline in translation is valid JS escape (\\n, never raw newline inside literal)",
      '" billets\\n*Exporté "' in out_i4)

# Unit test on synthetic snippet with single & double quotes, ternary, and node syntax check
mini_page = '<head></head><script>var s = on ? "First branch" : \'Second branch\'; var nl = " posts\\n*Exported ";</script><body></body>'
mini_tbl = strings.extract(mini_page)
mini_out, mini_used, _ = strings.apply(mini_page, mini_tbl, {"First branch": "Alpha", "Second branch": "L'arbre", " posts\n*Exported ": " billets\n*Exporté "})
check("issue #4: synthetic ternary & single-quote & newline translation",
      mini_used == 3 and 'on ? "Alpha" : \'L\\\'arbre\'' in mini_out and '" billets\\n*Exporté "' in mini_out)

node = shutil.which("node")
if node:
    _, js_block, _ = strings.split(out)
    js_code = re.sub(r"^<script[^>]*>|</script>$", "", js_block)
    r_syn = subprocess.run([node, "-e", "const fs=require('fs'); new Function(fs.readFileSync(0,'utf8'))"], input=js_code, capture_output=True, text=True)
    check("issue #4: pseudo-translated script (including multiline strings) parses as valid JS in node",
          r_syn.returncode == 0)
    _, js_i4, _ = strings.split(out_i4)
    js_i4_code = re.sub(r"^<script[^>]*>|</script>$", "", js_i4)
    r_syn2 = subprocess.run([node, "-e", "const fs=require('fs'); new Function(fs.readFileSync(0,'utf8'))"], input=js_i4_code, capture_output=True, text=True)
    check("issue #4: French issue-4 translated script parses as valid JS in node",
          r_syn2.returncode == 0)
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
