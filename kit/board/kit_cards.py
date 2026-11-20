#!/usr/bin/env python3
"""kit cards: the board's "Where we are" card (posts/_dashboard), built from the memory, never hand-written.

    python3 -I .kit/kit_cards.py [--write /tmp/kit-dashboard.json] [--now ISO]

One row per workstream, from .story/state/<id>.md: its `# title`, `Next:`, `Priority:`, and an optional `## Steps`
section ("- [x] done step" / "- [ ] step to come"; without it the steps are just the next one). A row is `you` when
.story/awaits.md has a line "- [<id>] ..." (those lines go into its note), else `ready` with a next step, `done`
when every step is ticked, `idle` otherwise: the page's own states. Rows go most urgent first. Then: `ArtifactData` `set`, collection
"posts", doc_id "_dashboard", `file_path` the written file (with `if_version` when it exists). The page shows it as
the card at the top; a tap on a row opens its post `P-<id>` (the import's) when there is one.
(The "Waiting for you" card is left out on purpose: what waits on the owner shows as the rows' `waits` mark and as
`question` posts.)
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def row(path, awaits):
    wid = os.path.basename(path)[:-3]
    text = open(path, encoding="utf-8").read()
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), wid)
    nxt = next((l[5:].strip() for l in text.splitlines() if l.startswith("Next:")), "")
    m = re.search(r"^Priority:\s*([123])", text, re.M)
    prio = int(m.group(1)) if m else 2
    steps, done = [], 0
    sm = re.search(r"^## Steps\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
    if sm:
        for l in sm.group(1).splitlines():
            x = re.match(r"\s*- \[( |x|X)\]\s*(.+)", l)
            if x:
                steps.append(x.group(2).strip())
                done += x.group(1) != " "
    if not steps:
        steps = [nxt or "next step"]
    mine = [l[len("- [%s]" % wid):].strip() for l in awaits.splitlines() if l.startswith("- [%s]" % wid)]
    # the page's states (CHIP/ORDER in board.html): you (waits on the owner), running, ready, idle, done
    state = "you" if mine else ("done" if steps and done == len(steps) else ("ready" if nxt else "idle"))
    note = ("Next: " + nxt) if nxt else ""
    if mine:
        note += "".join("\n- Waits on you: " + m for m in mine)
    return prio, {"key": wid, "name": title, "steps": steps, "at": min(done, len(steps) - 1), "state": state,
                  "note": note.strip(), "post": "P-" + wid}


def build(now):
    aw = os.path.join(ROOT, ".story", "awaits.md")
    awaits = open(aw, encoding="utf-8").read() if os.path.exists(aw) else ""
    rows = [row(p, awaits) for p in sorted(glob.glob(os.path.join(ROOT, ".story", "state", "*.md")))]
    rows = [r for _, r in sorted(rows, key=lambda x: x[0])]
    for i, r in enumerate(rows):
        r["key"] = KEYS[i] if i < len(KEYS) else str(i)
    return {"title": "Where we are", "column": "info", "rows": rows, "updated_at": now, "created_at": now}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="/tmp/kit-dashboard.json")
    ap.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    a = ap.parse_args(argv)
    d = build(a.now)
    with open(a.write, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    print("dashboard: %d rows -> %s" % (len(d["rows"]), a.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
