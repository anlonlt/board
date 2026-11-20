#!/usr/bin/env python3
"""kit cards: the board's "Where we are" card (posts/_dashboard) and its trains (posts/_trains), built, never hand-written.

    python3 -I .kit/kit_cards.py [--board DIR] [--write /tmp/kit-dashboard.json] [--trains /tmp/kit-trains.json] [--now ISO]

TRAINS (the owner, 2026-11-20: "shouldn't trains be built automatically, since it's integrated into the where we are
card?"): one train per project, its root the project (part `P-<id>`, its post the import's `P-<id>`); under it every
board post that names the project in `train` (each part's post carries `train: "<project id>"`, and `parent: "<part>"`
when it continues another part). A part's state comes from its post: column `done` -> done, `question`/`todo` -> you,
else running while `status` says "in progress", else idle. Its prompt: `docs/prompts/<part>*.md` when one exists.
DIR is a folder of posts as `ArtifactData` `list` `posts` saves them (`DIR/posts/<id>.json`), or the board's git
copy (`../<project>-board-mirror/board`). Each dashboard row names its train, so a tap on the row opens it.

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


def memory_dir(root=ROOT):
    try:
        md = json.load(open(os.path.join(root, ".kit", "config.json"), encoding="utf-8")).get("kit", {}).get("memory_dir")
        if md:
            return os.path.join(root, md)
    except (OSError, ValueError):
        pass
    if os.path.isdir(os.path.join(root, ".story")):
        return os.path.join(root, ".story")
    if os.path.isdir(os.path.join(root, ".board", "memory")):
        return os.path.join(root, ".board", "memory")
    return os.path.join(root, ".story")


def _clean_title(raw_title, wid):
    m = re.match(r"^State\s*[—–\-:]\s*(.+)$", raw_title.strip(), re.I)
    if m:
        cleaned = m.group(1).strip()
        if cleaned and cleaned == cleaned.lower():
            cleaned = cleaned.replace("-", " ").capitalize()
        return cleaned or wid
    return raw_title.strip() or wid


def _section_text(text, heading_pattern):
    m = re.search(r"^##\s+" + heading_pattern + r"\s*$(.*?)(?=^## |\Z)", text, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def _first_paragraph(section):
    if not section:
        return ""
    paras = []
    cur = []
    for line in section.splitlines():
        s = line.strip()
        if not s:
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            cur.append(re.sub(r"^[-*]\s+", "", s) if not cur else s)
    if cur:
        paras.append(" ".join(cur))
    return paras[0] if paras else ""


def _step_summary(nxt, max_len=120):
    if len(nxt) <= max_len:
        return nxt
    # Prefer first sentence/clause if concise
    m = re.match(r"^(.{15," + str(max_len) + r"}?)(?:\.\s|;\s)", nxt)
    if m:
        return m.group(1).strip()
    return nxt[: max_len - 3].rstrip() + "..."


_IDLE_NEXT_RE = re.compile(r"^(?:nothing\s+waits|none\s+queued|waiting:\s*nothing|held\b)", re.I)
_PLACEHOLDER_STEPS = {"next step", "later", "tbd", "todo", "none", ""}


def row(path, awaits):
    wid = os.path.basename(path)[:-3]
    text = open(path, encoding="utf-8").read()
    raw_title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), wid)
    title = _clean_title(raw_title, wid)
    nxt = next((l[5:].strip() for l in text.splitlines() if l.startswith("Next:")), "")
    if not nxt:
        nxt = _first_paragraph(_section_text(text, r"Next(?:\s+step)?"))
    if not nxt:
        nxt = _first_paragraph(_section_text(text, r"Where\s+it\s+stands"))
    m = re.search(r"^Priority:\s*([123])", text, re.M | re.I)
    prio = int(m.group(1)) if m else 2
    steps, done = [], 0
    sm = re.search(r"^## Steps\s*$(.*?)(?=^## |\Z)", text, re.S | re.M | re.I)
    if sm:
        for l in sm.group(1).splitlines():
            x = re.match(r"\s*- \[( |x|X)\]\s*(.+)", l)
            if x:
                steps.append(x.group(2).strip())
                done += x.group(1) != " "
    if not steps:
        done_sec = _section_text(text, r"Done")
        if done_sec:
            for l in done_sec.splitlines():
                x = re.match(r"\s*[-*]\s+(?:\[[xX]\]\s*)?(.+)", l)
                if x:
                    steps.append(x.group(1).strip())
                    done += 1
        if nxt:
            steps.append(_step_summary(nxt))
        elif not steps:
            steps = ["next step"]
    mine = [l[len("- [%s]" % wid):].strip() for l in awaits.splitlines() if l.startswith("- [%s]" % wid)]
    bm = re.search(r"^Blocked by:\s*(.+)$", text, re.M | re.I)
    blockers = [b.strip() for b in bm.group(1).split(",") if b.strip() and b.strip().lower() != "none"] if bm else []
    # the page's states (CHIP/ORDER in board.html): you (waits on the owner), blocked, running, ready, idle, done
    if mine:
        state = "you"
    elif blockers:
        state = "blocked"
    elif steps and done == len(steps):
        state = "done"
    elif nxt and not _IDLE_NEXT_RE.match(nxt):
        state = "ready"
    else:
        state = "idle"
    note = ("Next: " + nxt) if nxt else ""
    if blockers:
        note += "\n- Blocked by: " + ", ".join(blockers)
    if mine:
        note += "".join("\n- Waits on you: " + m for m in mine)
    out = {"key": wid, "name": title, "steps": steps, "at": min(done, len(steps) - 1), "state": state,
           "note": note.strip(), "post": "P-" + wid, "train": "P-" + wid}
    if blockers:
        out["blocked_by"] = blockers
    return prio, out


def existing_dashboard_rows(board_dir):
    if not board_dir:
        return []
    dash_path = os.path.join(board_dir, "posts", "_dashboard.json")
    if not os.path.isfile(dash_path):
        return []
    try:
        doc = json.load(open(dash_path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(doc, dict):
        rows = doc.get("rows")
        if isinstance(rows, list):
            return rows
        data = doc.get("data")
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            return data["rows"]
    return []


def is_placeholder_row(r):
    name = str(r.get("name") or "").strip()
    if re.match(r"^State\s*[—–\-:]", name, re.I):
        return True
    steps = [str(s).strip().lower() for s in (r.get("steps") or [])]
    if not steps or all(s in _PLACEHOLDER_STEPS for s in steps):
        return True
    return False


def check_safety(board_dir, new_rows, force=False):
    if force or not board_dir:
        return None
    existing = existing_dashboard_rows(board_dir)
    if not existing:
        return None
    if len(new_rows) == 0:
        return (
            "SAFETY REFUSAL: existing _dashboard in %s has %d rows; refusing to write 0 rows "
            "(pass --force to override)" % (board_dir, len(existing))
        )
    placeholders = sum(1 for r in new_rows if is_placeholder_row(r))
    if placeholders * 2 > len(new_rows):
        return (
            "SAFETY REFUSAL: %d/%d generated rows (>50%%) are generic placeholders while existing "
            "_dashboard in %s has %d rows (pass --force to override)"
            % (placeholders, len(new_rows), board_dir, len(existing))
        )
    return None


def post_state(p):
    col, st = p.get("column"), str(p.get("status") or "").lower()
    if col == "done":
        return "done"
    if col in ("question", "todo"):
        return "you"
    return "running" if "progress" in st else "idle"


def build_trains(board, rows, now):
    posts = {}
    for f in glob.glob(os.path.join(board, "posts", "*.json")) if board else []:
        try:
            posts[os.path.basename(f)[:-5]] = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    prompts = sorted(glob.glob(os.path.join(ROOT, "docs", "prompts", "*.md")))
    out, by = [], {}
    for r in rows:
        root = {"part": r["train"], "parent": "", "also": [], "children": [], "post": r["post"] if r["post"] in posts else "",
                "prompt": "", "session": "", "state": {"you": "you", "done": "done"}.get(r["state"], "idle"), "store": []}
        out.append(root)
        by[root["part"]] = root
    parts = sorted(((pid, p) for pid, p in posts.items() if p.get("train") and not pid.startswith("_") and not p.get("removed")),
                   key=lambda x: x[1].get("created_at") or "")
    for pid, p in parts:
        part = str(p.get("part") or pid)
        root = "P-" + str(p["train"])
        if root not in by:
            continue  # a train no project of the memory names: left out, not guessed
        parent = str(p.get("parent") or root)
        row = {"part": part, "parent": parent if parent in by else root, "also": [], "children": [], "post": pid,
               "prompt": next((os.path.relpath(x, ROOT) for x in prompts if os.path.basename(x).startswith(part)), ""),
               "session": p.get("session_id") or "", "state": post_state(p), "store": []}
        out.append(row)
        by[part] = row
    for row in out:
        if row["parent"] and row["parent"] in by:
            by[row["parent"]]["children"].append(row["part"])
    for row in out:  # a root runs while one of its parts does
        if not row["parent"] and row["state"] not in ("you", "done") and any(by[c]["state"] == "running" for c in row["children"]):
            row["state"] = "running"
    return {"title": "Trains", "column": "info", "rows": out, "updated_at": now, "created_at": now}


def build(now):
    sd = memory_dir(ROOT)
    aw = os.path.join(sd, "awaits.md")
    awaits = open(aw, encoding="utf-8").read() if os.path.exists(aw) else ""
    rows = [row(p, awaits) for p in sorted(glob.glob(os.path.join(sd, "state", "*.md")))]
    rows = [r for _, r in sorted(rows, key=lambda x: x[0])]
    for i, r in enumerate(rows):
        r["key"] = KEYS[i] if i < len(KEYS) else str(i)
    return {"title": "Where we are", "column": "info", "rows": rows, "updated_at": now, "created_at": now}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="/tmp/kit-dashboard.json")
    ap.add_argument("--trains", default="/tmp/kit-trains.json")
    ap.add_argument("--board", help="posts saved by ArtifactData list (DIR/posts/*.json) or the board's git copy")
    ap.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--force", action="store_true", help="allow overwriting a non-empty _dashboard with 0 or placeholder rows")
    a = ap.parse_args(argv)
    d = build(a.now)
    err = check_safety(a.board, d["rows"], force=a.force)
    if err:
        print(err, file=sys.stderr)
        return 2
    t = build_trains(a.board, d["rows"], a.now)
    for r in d["rows"]:  # a row whose project has a running part runs too
        if r["state"] in ("ready", "idle") and any(x["state"] == "running" for x in t["rows"] if x["parent"] == r["train"]):
            r["state"] = "running"
    with open(a.write, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    with open(a.trains, "w", encoding="utf-8") as fh:
        json.dump(t, fh, ensure_ascii=False, indent=1)
    print("dashboard: %d rows -> %s; trains: %d rows -> %s" % (len(d["rows"]), a.write, len(t["rows"]), a.trains))
    # Sessions to archive (the owner, 2026-11-20: "no one's ever archived my sessions"): a post he moved to Done is
    # his acknowledgment; its session is listed once, unless .kit/archived.txt already has it.
    done = set()
    try:
        done = set(open(os.path.join(ROOT, ".kit", "archived.txt"), encoding="utf-8").read().split())
    except OSError:
        pass
    todo = []
    for f in sorted(glob.glob(os.path.join(a.board, "posts", "*.json"))) if a.board else []:
        try:
            p = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = p.get("session_id") or ""
        if p.get("column") == "done" and sid.startswith("session_") and sid not in done and sid not in todo:
            todo.append(sid)
    print("to archive: %s" % (" ".join(todo) if todo else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
