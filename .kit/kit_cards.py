#!/usr/bin/env python3
"""kit cards: the board's "Where we are" card (posts/_dashboard) and its trains (posts/_trains), built, never hand-written.

    python3 -I .kit/kit_cards.py [--board DIR] [--write /tmp/kit-dashboard.json] [--trains /tmp/kit-trains.json] [--now ISO]

TRAINS (the owner, 2026-11-20: "shouldn't trains be built automatically, since it's integrated into the where we are
card?"): one train per project, its root the project (part `P-<id>`, its post the import's `P-<id>`); under it every
board post that names the project in `train` (each part's post carries `train: "<project id>"`, and `parent: "<part>"`
when it continues another part). A part's state comes from its post: column `done` or a status
"done ..." -> done (never running), status "closed ..." -> closed, "held" / "on hold" / "paused" -> held,
`question`/`todo` -> you, else running while `status` says "in progress", else idle. Its prompt: `docs/prompts/<part>*.md` when one exists.
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
import tempfile
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def memory_dir(root=ROOT):
    try:
        with open(os.path.join(root, ".kit", "config.json"), encoding="utf-8") as fh:
            md = json.load(fh).get("kit", {}).get("memory_dir")
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
    out["_explicit_steps"] = bool(sm)
    out["_next"] = _step_summary(nxt) if nxt else ""
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


# CL28: a post's status says more than its column. Harmonize's HL1 (column info, status "done" since 11-17) showed as
# running on the Trains card, and a held part (status "held: waits for VR5") had no state of its own.
DONE_STATUS = re.compile(r"^\s*(done|complete|completed|finished|merged)\s*($|[,:;.(\-])", re.I)
CLOSED_STATUS = re.compile(r"^\s*(closed|dropped|superseded|abandoned)\b", re.I)
HELD_STATUS = re.compile(r"^\s*(held|on[ -]?hold|hold|paused|parked)\b", re.I)
FINISHED = ("done", "closed")


def post_state(p):
    """done (column done, or a status that says done), closed, held (held / on hold / paused), you (a question or a
    todo for the owner), running (status in progress), else idle. A done post is never running."""
    col, st = p.get("column"), str(p.get("status") or "")
    if col == "done" or DONE_STATUS.match(st):
        return "done"
    if CLOSED_STATUS.match(st):
        return "closed"
    if HELD_STATUS.match(st):
        return "held"
    if col in ("question", "todo"):
        return "you"
    return "running" if "progress" in st.lower() else "idle"


def _load_board_posts(board):
    posts = {}
    if isinstance(board, dict):
        for k, v in board.items():
            if isinstance(v, dict):
                posts[k] = v
        return posts
    for f in glob.glob(os.path.join(board, "posts", "*.json")) if board else []:
        try:
            with open(f, encoding="utf-8") as fh:
                posts[os.path.basename(f)[:-5]] = json.load(fh)
        except (OSError, ValueError):
            pass
    return posts


def resolve_links(posts):
    """Build normalized link edges [{post_id, part, train, parent, title, column, status, priority, session_id, created_at}]
    where a post with `parent` pointing to an existing part/post inherits its `train` automatically."""
    raw = []
    by_part = {}
    by_post = {}
    for pid, p in (posts or {}).items():
        if pid.startswith("_") or p.get("removed"):
            continue
        part = str(p.get("part") or pid)
        train = str(p.get("train") or "").strip()
        if train.startswith("P-"):
            train = train[2:]
        parent = str(p.get("parent") or "").strip()
        item = {
            "post_id": pid,
            "part": part,
            "train": train,
            "parent": parent,
            "title": str(p.get("title") or pid),
            "column": str(p.get("column") or "info"),
            "status": str(p.get("status") or ""),
            "priority": int(p.get("priority")) if str(p.get("priority") or "") in ("1", "2", "3") else 2,
            "session_id": str(p.get("session_id") or ""),
            "created_at": str(p.get("created_at") or ""),
        }
        raw.append(item)
        by_part[part] = item
        by_post[pid] = item

    # Propagate train from parent chain if a post only specified parent
    for _ in range(10):
        changed = False
        for item in raw:
            if not item["train"] and item["parent"]:
                par_key = item["parent"][2:] if item["parent"].startswith("P-") else item["parent"]
                par = by_part.get(item["parent"]) or by_post.get(item["parent"]) or by_part.get(par_key) or by_post.get("P-" + par_key)
                if par and par["train"]:
                    item["train"] = par["train"]
                    changed = True
                elif item["parent"].startswith("P-"):
                    item["train"] = par_key
                    changed = True
        if not changed:
            break

    raw.sort(key=lambda x: (x["created_at"], x["post_id"]))
    return [x for x in raw if x["train"]]


def synthesize_linked_rows(board, existing_rows):
    """Synthesize dashboard rows for trains present in post links that do not have a .story/state/<train>.md file yet."""
    posts = _load_board_posts(board)
    if not posts:
        return []
    links = resolve_links(posts)
    known_trains = {str(r.get("train") or "") for r in existing_rows}
    by_train = {}
    for lk in links:
        t_root = "P-" + lk["train"]
        if t_root in known_trains:
            continue
        by_train.setdefault(lk["train"], []).append(lk)

    synth = []
    for wid in sorted(by_train.keys()):
        t_links = by_train[wid]
        root_pid = "P-" + wid
        root_doc = posts.get(root_pid) or {}
        raw_title = str(root_doc.get("title") or wid.replace("-", " ").capitalize())
        title = _clean_title(raw_title, wid)
        prio = min((lk["priority"] for lk in t_links), default=2)
        steps = []
        done_count = 0
        has_you = False
        has_running = False
        latest_active = None
        for lk in t_links:
            if lk["post_id"] == root_pid and len(t_links) > 1:
                continue
            step_label = _step_summary(lk["title"] or lk["part"], max_len=90)
            steps.append(step_label)
            st = post_state({"column": lk["column"], "status": lk["status"]})
            if st in FINISHED:
                done_count += 1
            elif st == "you":
                has_you = True
                latest_active = lk
            elif st == "running":
                has_running = True
                if not latest_active:
                    latest_active = lk
            else:
                if not latest_active:
                    latest_active = lk
        if not steps:
            steps = [_step_summary(title)]
        if has_you:
            state = "you"
        elif has_running:
            state = "running"
        elif done_count == len(steps):
            state = "done"
        else:
            state = "ready"
        at_idx = min(done_count, len(steps) - 1)
        nxt_label = latest_active["title"] if latest_active else steps[at_idx]
        note = "Next: " + nxt_label
        primary_post = root_pid if root_pid in posts else (latest_active["post_id"] if latest_active else t_links[-1]["post_id"])
        synth.append((prio, {
            "key": wid,
            "name": title,
            "steps": steps,
            "at": at_idx,
            "state": state,
            "note": note,
            "post": primary_post,
            "train": root_pid,
        }))
    return synth


def build_trains(board, rows, now):
    posts = _load_board_posts(board)
    prompts = sorted(glob.glob(os.path.join(ROOT, "docs", "prompts", "*.md")))
    out, by = [], {}
    for r in rows:
        root = {"part": r["train"], "parent": "", "also": [], "children": [], "post": r["post"] if r["post"] in posts else "",
                "prompt": "", "session": "", "state": {"you": "you", "done": "done"}.get(r["state"], "idle"), "store": []}
        out.append(root)
        by[root["part"]] = root
    links = resolve_links(posts)
    for lk in links:
        pid = lk["post_id"]
        p = posts.get(pid) or {}
        part = lk["part"]
        root = "P-" + lk["train"]
        if root not in by:
            root_row = {"part": root, "parent": "", "also": [], "children": [], "post": root if root in posts else pid,
                        "prompt": "", "session": "", "state": "idle", "store": []}
            out.append(root_row)
            by[root] = root_row
        if part == root:
            by[root]["post"] = pid
            by[root]["state"] = post_state(p)
            continue
        parent = str(lk["parent"] or root)
        row = {"part": part, "parent": parent if parent in by else root, "also": [], "children": [], "post": pid,
               "prompt": next((os.path.relpath(x, ROOT) for x in prompts if os.path.basename(x).startswith(part)), ""),
               "session": p.get("session_id") or "", "state": post_state(p), "store": []}
        out.append(row)
        by[part] = row
    for row in out:
        if row["parent"] and row["parent"] in by:
            if row["part"] not in by[row["parent"]]["children"]:
                by[row["parent"]]["children"].append(row["part"])
    for row in out:  # a root runs while one of its parts does
        if not row["parent"] and row["state"] not in ("you", "done") and any(by[c]["state"] == "running" for c in row["children"]):
            row["state"] = "running"
    return {"title": "Trains", "column": "info", "rows": out, "updated_at": now, "created_at": now}


def steps_from_train(r, links_by_train):
    """A workstream with a state file but no `## Steps` section: its steps are its train's parts on the board, in
    order (done ones first, then the ones still open), then its Next if it is not one of them. So "Where we are"
    shows the same sequence as the train (the owner, 2026-11-22: "why doesn't Where we are follow the trains?").
    Say it once: the train is the plan (the orchestrator's task list puts every step on it), so when the train has
    steps they win over a state file's `## Steps`, which is only the fallback for a workstream with no step yet."""
    wid = str(r.get("train") or "")[2:]
    parts = [lk for lk in links_by_train.get(wid, []) if lk["post_id"] != "P-" + wid]
    if not parts:
        return r
    done = [lk for lk in parts if post_state({"column": lk["column"], "status": lk["status"]}) in FINISHED]
    open_ = [lk for lk in parts if lk not in done]
    steps = [_step_summary(lk["title"] or lk["part"], max_len=90) for lk in done + open_]
    nxt = r.get("_next") or ""
    if nxt and nxt not in steps:
        steps.append(nxt)
    r["steps"] = steps
    r["at"] = min(len(done), len(steps) - 1)
    if any(post_state({"column": lk["column"], "status": lk["status"]}) == "running" for lk in open_) and r.get("state") in ("ready", "idle"):
        r["state"] = "running"
    return r


def build(now, board=None):
    sd = memory_dir(ROOT)
    aw = os.path.join(sd, "awaits.md")
    awaits = open(aw, encoding="utf-8").read() if os.path.exists(aw) else ""
    scored_rows = [row(p, awaits) for p in sorted(glob.glob(os.path.join(sd, "state", "*.md")))]
    if board:
        existing_only = [r for _, r in scored_rows]
        scored_rows.extend(synthesize_linked_rows(board, existing_only))
    rows = [r for _, r in sorted(scored_rows, key=lambda x: x[0])]
    if board:
        by_train = {}
        for lk in resolve_links(_load_board_posts(board)):
            by_train.setdefault(lk["train"], []).append(lk)
        rows = [steps_from_train(r, by_train) for r in rows]
    for r in rows:
        r.pop("_explicit_steps", None)
        r.pop("_next", None)
    for i, r in enumerate(rows):
        r["key"] = KEYS[i] if i < len(KEYS) else str(i)
    return {"title": "Where we are", "column": "info", "rows": rows, "updated_at": now, "created_at": now}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default=os.path.join(tempfile.gettempdir(), "kit-dashboard.json"))   # CL52: TMPDIR, not /tmp
    ap.add_argument("--trains", default=os.path.join(tempfile.gettempdir(), "kit-trains.json"))
    ap.add_argument("--board", help="posts saved by ArtifactData list (DIR/posts/*.json) or the board's git copy")
    ap.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--force", action="store_true", help="allow overwriting a non-empty _dashboard with 0 or placeholder rows")
    a = ap.parse_args(argv)
    d = build(a.now, board=a.board)
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
