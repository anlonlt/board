#!/usr/bin/env python3
"""3-layer Episodic -> Semantic memory ledger for the board kit (CL34 + distillation).

Layers:
  1. Observations (kind=observation|rule|lesson): silently logged with verbatim owner quote + source ID,
     active immediately in their narrow scope (zero approval friction).
  2. Mechanical invariants (enforced_by=hook:<name>|test:<name>): enforced silently by hooks/tests;
     never dumped into `kit.py resume` (only a 1-line count is shown, keeping startup tokens bounded).
  3. Distilled Principles (kind=principle): when ~3+ observations cluster around a theme, `distil`
     surfaces them and `propose` creates 1 compact Principle citing all underlying quotes. When the
     owner confirms (`confirm`), the Principle becomes active everywhere and the granular observations
     are marked `subsumed_by: <pid>` — staying in git as traceable evidence while disappearing from
     startup prompts. A row the principle `keeps` (an exact line: "never a link in chat") stays, printed under it: a
     principle says why and covers what no row names, it does not replace an exact line. `--refines P1` offers a
     principle new rows in its vein: its wording hardens, the owner confirms, the old one is replaced.
  4. Fields (kind=field): a theme's principles and rows, opened by a script and never by an agent's judgement of what is
     relevant: `opens: start | files:<glob> | moment:ask|report|launch|review`. Closed, it costs one line; a member
     loads whole when a file or a moment opens it. `not_for` is its fence (a principle has one too). Membership and
     opening are the owner's yes, like a principle.
  `moves` lists what could go one step further (rows to a principle, a row to an existing principle or field, a row
  broken twice to a hook), as one batch for the owner.

Also supports `kind=decision` (with `because:` and `rejected:`) so settled architectural/product
choices are never re-litigated.

A row never goes to the clerk: it only routes, and its rules are the kit's own prompt (kit/*/clerk.template.md).

Storage: Markdown at `<memory_dir>/ledger.md` (and optional kit-global `kit/ledger.md` / `.kit/ledger.md`).
No external dependencies (stdlib only).
"""
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:      # Windows
    fcntl = None
    import msvcrt

KINDS = ("observation", "rule", "decision", "principle", "lesson", "field")
MOMENTS = ("ask", "report", "launch", "review")
STATUSES = ("active", "proposed", "subsumed", "replaced", "archived", "dropped")
# Scope precedence: more specific scopes sort first when resolving overlaps.
SCOPE_RANK = {"files": 4, "train": 3, "role": 2, "project": 1, "all": 0}
STOP_WORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "into", "when", "only", "never", "always",
    "must", "should", "not", "are", "was", "were", "have", "has", "had", "not", "all", "any", "each",
    "every", "use", "using", "used", "make", "keep", "run", "runs", "its", "their", "your", "our",
    "you", "they", "them", "she", "his", "her", "one", "two", "new", "old", "also", "then", "than",
})
ENTRY_HEAD = re.compile(r"^##\s+([A-Za-z][A-Za-z0-9_.-]*)\s*\(([^)]+)\)\s*:\s*(.+?)\s*$")
KV_LINE = re.compile(r"^([a-z_]+)\s*:\s*(.*)$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
MULTILINE = ("quote", "because", "rejected")     # kept line by line: "  | <line>" under the first
CONT = "  |"
SCOPE_ROLES = ("orchestrator", "part", "review")


def _lines(value):
    """Verbatim text as lines: every line break a file or an editor could read as one becomes \\n."""
    return re.sub(r"\r\n|[\r\v\f\x1c-\x1e\x85\u2028\u2029]", "\n", str(value or "")).strip().split("\n")


def _flat(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


@contextlib.contextmanager
def _locked(memory_dir):
    """One writer at a time: read, change and write happen under this lock (a machine-local file, never in git)."""
    key = hashlib.sha1(os.path.realpath(memory_dir).encode("utf-8")).hexdigest()[:16]
    fh = open(os.path.join(tempfile.gettempdir(), "kit-ledger-%s.lock" % key), "a+")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        fh.close()                                # closing frees the lock


def scope_error(scope):
    """Why a scope is refused (None: fine). An unknown scope would reach every role: it is never guessed."""
    for s in (x for g in scope_groups(scope) for x in g):
        head, _, val = s.partition(":")
        head, val = head.strip().lower(), val.strip()
        if head == "role" and val.lower() == "clerk":
            return ("scope role:clerk refused: the clerk only routes and reads no ledger; its rules are the kit's "
                    "(kit/*/clerk.template.md)")
        if head in ("all", "project") and not val:
            continue
        if head == "role" and val.lower() in SCOPE_ROLES:
            continue
        if head in ("train", "files") and val:
            continue
        return "unknown scope %r (all, project, role:%s, train:<name>, files:<glob>)" % (s, "|".join(SCOPE_ROLES))
    return None


def _reach(scope):
    """A scope as a comparable set; `project` and `all` reach the same sessions of one project."""
    return frozenset(frozenset("all" if s.lower() == "project" else s.lower() if s.lower().startswith(("role:", "all")) else s
                               for s in g) for g in scope_groups(scope))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scope_groups(scope):
    """A scope as its alternatives, each a list that must hold together: `role:orchestrator | train:bd, role:part` is
    the orchestrator, or a part of train bd."""
    text = ", ".join(str(x) for x in scope) if isinstance(scope, (list, tuple, set)) else str(scope or "project")
    out = []
    for alt in text.split("|"):
        g = [t.strip() for t in re.split(r"[,;]+", alt) if t.strip()]
        if g:
            out.append(list(dict.fromkeys(g)))
    return out or [["project"]]


def _norm_scopes(scope):
    """The scope as it is kept: its terms, with "|" between alternatives."""
    out = []
    for g in scope_groups(scope):
        out += (["|"] if out else []) + g
    return out


def scope_text(scope):
    return " | ".join(", ".join(g) for g in scope_groups(scope))


def _is_files_group(g):
    return any(s.lower().startswith("files:") for s in g)


def scope_priority(scopes):
    best = 0
    for s in _norm_scopes(scopes):
        head = s.split(":", 1)[0].strip().lower()
        best = max(best, SCOPE_RANK.get(head, 1))
    return best


def _keywords(text):
    words = re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
    return {w for w in words if w not in STOP_WORDS}


def ledger_path(memory_dir):
    return os.path.join(memory_dir, "ledger.md")


def parse_ledger(text, source=""):
    """Parse Markdown ledger into list of entry dicts."""
    entries = []
    cur = None
    last = None
    for line in str(text or "").split("\n"):
        if cur is not None and last in MULTILINE and line.startswith(CONT):
            rest = line[len(CONT):]
            cur[last] += "\n" + (rest[1:] if rest.startswith(" ") else rest)
            continue
        m = ENTRY_HEAD.match(line)
        if m:
            meta = [x.strip().lower() for x in m.group(2).split(",")]
            kind = meta[0] if meta and meta[0] in KINDS else "observation"
            status = meta[1] if len(meta) > 1 and meta[1] in STATUSES else "active"
            cur = {
                "id": m.group(1),
                "kind": kind,
                "status": status,
                "summary": m.group(3).strip(),
                "scope": ["project"],
                "enforced_by": "review",
                "quote": "",
                "source": "",
                "theme": "",
                "because": "",
                "rejected": "",
                "subsumes": [],
                "subsumed_by": "",
                "replaces": "",
                "confirmed_by": "",
                "keeps": [],
                "opens": "",
                "not_for": "",
                "broke": "",
                "created_at": "",
                "_file": source,
            }
            entries.append(cur)
            last = None
            continue
        if cur is None:
            continue
        kv = KV_LINE.match(line.rstrip())
        if not kv:
            continue
        k, v = kv.group(1).lower(), kv.group(2).strip()
        last = k
        if k == "scope":
            cur["scope"] = _norm_scopes(v)
        elif k in ("subsumes", "keeps"):
            cur[k] = [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
        elif k in cur:
            cur[k] = v
    return entries


def render_ledger(entries):
    """Serialize entry dicts back to clean, deterministic Markdown."""
    out = [
        "# Memory Ledger",
        "",
        "Three-layer memory: silent scoped observations -> mechanical invariants (hooks) -> distilled principles.",
        "Managed by `python3 -I .kit/kit.py ledger ...`.",
        "",
    ]
    for e in entries:
        out.append("## %s (%s, %s): %s" % (e["id"], e["kind"], e["status"], _flat(e["summary"])))
        out.append("scope: %s" % scope_text(e.get("scope")))
        if e.get("enforced_by"):
            out.append("enforced_by: %s" % e["enforced_by"])
        if e.get("theme"):
            out.append("theme: %s" % e["theme"])
        for k in ("quote", "source", "because", "rejected", "opens", "not_for", "broke", "confirmed_by"):
            if not e.get(k):
                continue
            if k not in MULTILINE:
                out.append("%s: %s" % (k, _flat(e[k])))
                continue
            first, *more = _lines(e[k])
            out.append("%s: %s" % (k, first))
            out += [(CONT + " " + x) if x else CONT for x in more]
        if e.get("subsumes"):
            out.append("subsumes: %s" % ", ".join(e["subsumes"]))
        if e.get("keeps"):
            out.append("keeps: %s" % ", ".join(e["keeps"]))
        if e.get("subsumed_by"):
            out.append("subsumed_by: %s" % e["subsumed_by"])
        if e.get("replaces"):
            out.append("replaces: %s" % e["replaces"])
        if e.get("created_at"):
            out.append("created_at: %s" % e["created_at"])
        out.append("")
    return "\n".join(out)


def load_entries(memory_dir, kit_root=None):
    """Load project ledger entries (plus kit-global ledger if present)."""
    out = []
    paths = []
    if kit_root:
        for rel in (os.path.join("kit", "ledger.md"), os.path.join(".kit", "ledger.md")):
            p = os.path.join(kit_root, rel)
            if os.path.isfile(p):
                paths.append(p)
                break
    lp = ledger_path(memory_dir)
    if os.path.isfile(lp):
        paths.append(lp)
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                out.extend(parse_ledger(fh.read(), source=p))
        except OSError:
            continue
    return out


def save_entries(memory_dir, entries):
    os.makedirs(memory_dir, exist_ok=True)
    p = ledger_path(memory_dir)
    fd, tmp = tempfile.mkstemp(prefix=".ledger-", dir=memory_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_ledger(entries))
        os.chmod(tmp, (os.stat(p).st_mode & 0o777) if os.path.exists(p) else 0o644)
        os.replace(tmp, p)                        # a reader sees the old file or the new one, never half
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return p


def _next_id(entries, prefix):
    mx = 0
    pat = re.compile(r"^%s(\d+)$" % re.escape(prefix))
    for e in entries:
        m = pat.match(str(e.get("id") or ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return "%s%d" % (prefix, mx + 1)


def _scopes_overlap(s1, s2):
    a, b = set(_norm_scopes(s1)) - {"|"}, set(_norm_scopes(s2)) - {"|"}
    if "all" in a or "all" in b:
        return True
    if a & b:
        return True
    return False


def find_conflicts(entries, summary, scope, theme=""):
    """Find active entries with overlapping scope and strong keyword or theme overlap."""
    kw = _keywords(summary)
    conflicts = []
    for e in entries:
        if e.get("status") != "active":
            continue
        if not _scopes_overlap(e.get("scope"), scope):
            continue
        ekw = _keywords(e.get("summary"))
        overlap = kw & ekw
        if len(overlap) >= 2 or (theme and e.get("theme") == theme and len(overlap) >= 1):
            conflicts.append({"id": e["id"], "kind": e["kind"], "summary": e["summary"],
                              "scope": e["scope"], "overlap": sorted(overlap)})
    return conflicts


def observe(memory_dir, summary, quote="", source="", scope="project", theme="", kind="observation",
            enforced_by="review", because="", rejected="", replaces="", entry_id=None):
    """Silently log an observation/rule/decision/lesson as active immediately (zero approval friction).
    Returns {ok, entry, conflicts, path}."""
    kind = str(kind or "observation").strip().lower()
    if kind not in KINDS:
        return {"error": "unknown kind %r (one of %s)" % (kind, ", ".join(KINDS))}
    summary = _flat(summary)
    if not summary:
        return {"error": "summary is required"}
    err = scope_error(scope)
    if err:
        return {"error": err}
    if entry_id and not ID_RE.match(str(entry_id)):
        return {"error": "bad id %r" % entry_id}
    with _locked(memory_dir):
        return _observe(memory_dir, summary, quote, source, scope, theme, kind, enforced_by, because, rejected,
                        replaces, entry_id)


def _observe(memory_dir, summary, quote, source, scope, theme, kind, enforced_by, because, rejected, replaces, entry_id):
    entries = load_entries(memory_dir)
    scopes = _norm_scopes(scope)
    conflicts = find_conflicts(entries, summary, scopes, theme=theme.strip().lower())
    prefix = {"observation": "O", "rule": "R", "decision": "D", "principle": "P", "lesson": "L"}[kind]
    eid = entry_id or _next_id(entries, prefix)
    if any(e["id"] == eid for e in entries):
        return {"error": "entry %s already exists" % eid}
    if replaces:
        for e in entries:
            if e["id"] == replaces and e["status"] == "active":
                e["status"] = "replaced"
    entry = {
        "id": eid,
        "kind": kind,
        "status": "proposed" if kind == "principle" else "active",
        "summary": summary,
        "scope": scopes,
        "enforced_by": str(enforced_by or "review").strip(),
        "quote": "\n".join(_lines(quote)),
        "source": _flat(source),
        "theme": str(theme or "").strip().lower(),
        "because": "\n".join(_lines(because)),
        "rejected": "\n".join(_lines(rejected)),
        "subsumes": [],
        "subsumed_by": "",
        "replaces": str(replaces or "").strip(),
        "created_at": _now_iso(),
    }
    entries.append(entry)
    path = save_entries(memory_dir, entries)
    return {"ok": True, "entry": entry, "conflicts": conflicts, "path": path}


def distil_candidates(memory_dir, min_cluster=3):
    """Find clusters of active, un-subsumed observations/rules ready to distil into a Principle.

    Only rows of the same scope cluster together (by `theme`, else by keyword overlap): one principle replaces
    them, and it reaches exactly the sessions they reached.
    """
    entries = load_entries(memory_dir)
    pool = [e for e in entries if e.get("status") == "active" and e.get("kind") in ("observation", "rule", "lesson")
            and not e.get("subsumed_by") and not str(e.get("enforced_by") or "").startswith(("hook:", "test:"))]
    by_theme = {}
    unthemed = []
    for e in pool:
        th = (e.get("theme") or "").strip().lower()
        if th:
            by_theme.setdefault((th, _reach(e["scope"])), []).append(e)
        else:
            unthemed.append(e)

    clusters = []
    for (th, _), items in sorted(by_theme.items(), key=lambda kv: (kv[0][0], sorted(sorted(g) for g in kv[0][1]))):
        if len(items) >= min_cluster:
            clusters.append({
                "theme": th,
                "count": len(items),
                "ids": [x["id"] for x in items],
                "items": [{"id": x["id"], "summary": x["summary"], "quote": x.get("quote", ""),
                           "source": x.get("source", "")} for x in items],
            })

    # Greedy keyword clustering for unthemed observations
    used = set()
    for i, a in enumerate(unthemed):
        if a["id"] in used:
            continue
        akw = _keywords(a["summary"] + " " + a.get("quote", ""))
        if not akw:
            continue
        grp = [a]
        shared = set(akw)
        for b in unthemed[i + 1:]:
            if b["id"] in used:
                continue
            if _reach(b["scope"]) != _reach(a["scope"]):
                continue
            bkw = _keywords(b["summary"] + " " + b.get("quote", ""))
            if len(akw & bkw) >= 2:
                grp.append(b)
                shared &= bkw
        if len(grp) >= min_cluster:
            for x in grp:
                used.add(x["id"])
            tag = "-".join(sorted(shared)[:2]) or "general"
            clusters.append({
                "theme": tag,
                "count": len(grp),
                "ids": [x["id"] for x in grp],
                "items": [{"id": x["id"], "summary": x["summary"], "quote": x.get("quote", ""),
                           "source": x.get("source", "")} for x in grp],
            })
    return clusters


def opens_error(opens):
    """Why a field's `opens` is refused (None: fine)."""
    terms = [t.strip() for t in re.split(r"[,;|]+", str(opens or "")) if t.strip()]
    if not terms:
        return "a field says what opens it: start, files:<glob>, moment:%s" % "|".join(MOMENTS)
    for t in terms:
        head, _, val = t.partition(":")
        if not (t == "start" or (head == "files" and val) or (head == "moment" and val in MOMENTS)):
            return "unknown opening %r (start, files:<glob>, moment:%s)" % (t, "|".join(MOMENTS))
    return None


def _open_terms(e):
    return [t.strip() for t in re.split(r"[,;|]+", str(e.get("opens") or "")) if t.strip()]


def fields(entries):
    return [e for e in entries if e.get("kind") == "field" and e.get("status") == "active"]


def _closed(entries):
    """{row id: its fields} of the rows that wait for a field to open (not those a field opens at start)."""
    out = {}
    for f in fields(entries):
        for i in f.get("subsumes") or []:
            out.setdefault(i, []).append(f)
    by_id = {e["id"]: e for e in entries}
    for f in fields(entries):                                    # a principle's kept rows come and go with it
        for i in f.get("subsumes") or []:
            for k in (by_id.get(i) or {}).get("keeps") or []:
                out.setdefault(k, []).append(f)
    return {i: fs for i, fs in out.items() if not any("start" in _open_terms(f) for f in fs)}


def _field_members(entries, f, role, train=None):
    ids = set(f.get("subsumes") or [])
    for e in entries:
        if e["id"] in set(f.get("subsumes") or []):
            ids |= set(e.get("keeps") or [])
    return [e for e in entries if e["id"] in ids and e.get("status") == "active" and not is_hook_enforced(e)
            and _matches_scope(e, role, train=train)]


def open_fields(memory_dir, role, files=None, moment=None, kit_root=None, train=None):
    """[(field, its rows for this role)] that these files or this moment open."""
    if str(role or "").lower() == "clerk":
        return []
    entries, out = load_entries(memory_dir, kit_root=kit_root), []
    for f in fields(entries):
        hit = any((t.startswith("moment:") and t.split(":", 1)[1] == moment) or
                  (t.startswith("files:") and any(fnmatch.fnmatch(x, t[6:]) or fnmatch.fnmatch(os.path.basename(x), t[6:]) for x in files or []))
                  for t in _open_terms(f))
        rows = _field_members(entries, f, role, train) if hit else []
        if rows:
            out.append((f, rows))
    return out


def field_lines(opened):
    out = []
    for f, rows in opened:
        out += ["", "## Field `%s`: %s%s" % (f.get("theme") or f["id"], f["summary"], (" (not for: %s)" % f["not_for"]) if f.get("not_for") else "")]
        out += _grouped(rows)
    return out


def _grouped(rows):
    """Principles first, each with the exact rows it keeps under it, then the rest."""
    ids = {r["id"]: r for r in rows}
    kept = {k for r in rows if r["kind"] == "principle" for k in r.get("keeps") or [] if k in ids}
    out = []
    for r in rows:
        if r["kind"] == "principle":
            out.append("- **%s**: %s%s" % (r["id"], r["summary"], (" Not for: %s." % r["not_for"].rstrip(".")) if r.get("not_for") else ""))
            out += ["  - exactly: **%s**: %s" % (k, ids[k]["summary"]) for k in r.get("keeps") or [] if k in ids]
    out += ["- **%s**: %s%s" % (r["id"], r["summary"], (" (`%s`)" % r["source"]) if r["kind"] == "lesson" and r.get("source") else "")
            for r in rows if r["kind"] != "principle" and r["id"] not in kept]
    return out


def broke(memory_dir, eid, source):
    """A row was broken again: where (the owner's comment, a review). Twice, and it wants a hook, not firmer words."""
    if not _flat(source):
        return {"error": "broke needs its source: the comment or review that says so"}
    with _locked(memory_dir):
        entries = load_entries(memory_dir)
        e = next((x for x in entries if x["id"] == eid and x["status"] == "active"), None)
        if not e:
            return {"error": "%s is not an active row" % eid}
        seen = [x.strip() for x in str(e.get("broke") or "").split(",") if x.strip()]
        if _flat(source) not in seen:
            seen.append(_flat(source))
        e["broke"] = ", ".join(seen)
        save_entries(memory_dir, entries)
        return {"ok": True, "id": eid, "broke": seen}


def moves(memory_dir, min_cluster=3):
    """What could go one step further, deterministic, no model: [{"move", "ids", "to", "why"}]."""
    entries = load_entries(memory_dir)
    live = [e for e in entries if e.get("status") == "active"]
    held = {i for e in live if e["kind"] == "principle" for i in (e.get("keeps") or [])}
    rows = [e for e in live if e["kind"] in ("observation", "rule", "lesson") and not is_hook_enforced(e) and e["id"] not in held]
    out, taken = [], set()
    for p in (e for e in live if e["kind"] == "principle" and e.get("theme")):
        ids = [r["id"] for r in rows if r.get("theme") == p["theme"] and _reach(r["scope"]) == _reach(p["scope"]) and r["id"] not in taken]
        if ids:
            taken |= set(ids)
            out.append({"move": "join", "ids": ids, "to": p["id"], "why": "same theme and reach as %s: does its wording cover them, or does it need refining?" % p["id"]})
    for c in distil_candidates(memory_dir, min_cluster=min_cluster):
        ids = [i for i in c["ids"] if i not in taken and i not in held]
        if len(ids) >= min_cluster:
            out.append({"move": "distil", "ids": ids, "to": c["theme"], "why": "%d rows of one theme and reach" % len(ids)})
    for f in fields(entries):
        ids = [e["id"] for e in live if e["kind"] == "principle" and e.get("theme") == f.get("theme") and e["id"] not in (f.get("subsumes") or [])]
        if ids:
            out.append({"move": "enter", "ids": ids, "to": f["id"], "why": "a principle of this field's theme, outside it"})
    have = {f.get("theme") for f in fields(entries)} | {e.get("theme") for e in entries if e["kind"] == "field" and e["status"] == "proposed"}
    by = {}
    for e in live:
        if e["kind"] == "principle" and e.get("theme") and e["theme"] not in have:
            by.setdefault(e["theme"], []).append(e["id"])
    out += [{"move": "field", "ids": v, "to": t, "why": "%d principles of one theme" % len(v)} for t, v in sorted(by.items()) if len(v) >= 2]
    out += [{"move": "hook", "ids": [e["id"]], "to": "hook", "why": "broken %d times: %s" % (len(b), ", ".join(b))}
            for e in live for b in [[x for x in str(e.get("broke") or "").split(",") if x.strip()]] if len(b) >= 2 and not is_hook_enforced(e)]
    return out


def propose_field(memory_dir, summary, members, theme, opens, not_for="", refines=""):
    """A `proposed` field over active rows and principles. Nothing loads differently until the owner confirms it."""
    err = opens_error(opens)
    if err:
        return {"error": err}
    if not _flat(summary) or not _flat(theme):
        return {"error": "a field has a summary and a theme"}
    with _locked(memory_dir):
        entries = load_entries(memory_dir)
        by_id = {e["id"]: e for e in entries}
        old = by_id.get(refines) if refines else None
        if refines and not (old and old["kind"] == "field" and old["status"] == "active"):
            return {"error": "%s is not an active field" % refines}
        ids = list(dict.fromkeys(list((old or {}).get("subsumes") or []) + list(members)))
        bad = [i for i in ids if i not in by_id or by_id[i]["status"] != "active" or by_id[i]["kind"] == "field"]
        if bad or not ids:
            return {"error": "not active rows or principles: %s" % (", ".join(bad) or "(none named)")}
        e = {"id": _next_id(entries, "F"), "kind": "field", "status": "proposed", "summary": _flat(summary), "scope": ["project"],
             "enforced_by": "review", "quote": "", "source": "", "theme": _flat(theme).lower(), "because": "", "rejected": "", "subsumes": ids,
             "subsumed_by": "", "replaces": refines, "opens": ", ".join(t.strip() for t in re.split(r"[,;|]+", opens) if t.strip()),
             "not_for": _flat(not_for), "created_at": _now_iso()}
        entries.append(e)
        return {"ok": True, "field": e, "path": save_entries(memory_dir, entries)}


def propose_principle(memory_dir, summary, subsumes_ids, scope=None, theme="", pid=None, widen=False, keeps=(), not_for="", refines=""):
    """Create a `proposed` Principle citing all quotes/sources of `subsumes_ids`.
    Underlying rows remain active until the owner confirms the principle. Its scope is theirs; another one is refused
    unless `widen` (a files: or role: rule spread to every session is noise there, and lost to the JIT hook)."""
    err = scope and scope_error(scope)
    if err:
        return {"error": err}
    if not _flat(summary):
        return {"error": "summary is required"}
    with _locked(memory_dir):
        return _propose(memory_dir, summary, subsumes_ids, scope, theme, pid, widen, list(keeps), not_for, refines)


def _propose(memory_dir, summary, subsumes_ids, scope, theme, pid, widen, keeps=(), not_for="", refines=""):
    entries = load_entries(memory_dir)
    by_id = {e["id"]: e for e in entries}
    old = by_id.get(refines) if refines else None
    if refines and not (old and old["kind"] == "principle" and old["status"] == "active"):
        return {"error": "%s is not an active principle" % refines}
    stray = [k for k in keeps if k not in subsumes_ids]
    if stray:
        return {"error": "keeps a row it does not stand on: %s" % ", ".join(stray)}
    missing = [i for i in subsumes_ids if i not in by_id]
    if missing:
        return {"error": "unknown observation/rule id(s): %s" % ", ".join(missing)}
    if not subsumes_ids:
        return {"error": "subsumes_ids must name at least one underlying observation or rule"}
    gone = [i for i in subsumes_ids if by_id[i]["status"] != "active"]
    if gone:
        return {"error": "not active: %s" % ", ".join(gone)}
    reaches = {_reach(by_id[i]["scope"]) for i in subsumes_ids}
    if old:
        reaches.add(_reach(old["scope"]))
    if scope is None:
        if len(reaches) > 1:
            return {"error": "the rows have different scopes: one principle per scope, or name --scope with --widen"}
        scope = by_id[subsumes_ids[0]]["scope"]
    elif reaches != {_reach(scope)} and not widen:
        return {"error": "scope %s is not the scope of %s: keep theirs, or --widen" % (
            ", ".join(_norm_scopes(scope)), ", ".join(i for i in subsumes_ids if _reach(by_id[i]["scope"]) != _reach(scope)))}
    before = [i for i in (old or {}).get("subsumes") or [] if i in by_id and i not in subsumes_ids]
    subsumes_ids = before + list(subsumes_ids)
    keeps = list(dict.fromkeys(list((old or {}).get("keeps") or []) + list(keeps)))
    quotes = [by_id[i]["quote"] for i in subsumes_ids if by_id[i].get("quote")]
    sources = [by_id[i]["source"] for i in subsumes_ids if by_id[i].get("source")]
    pid = pid or _next_id(entries, "P")
    if pid in by_id:
        return {"error": "principle %s already exists" % pid}
    entry = {
        "id": pid,
        "kind": "principle",
        "status": "proposed",
        "summary": _flat(summary),
        "scope": _norm_scopes(scope),
        "enforced_by": "review",
        "quote": " | ".join(quotes),
        "source": ", ".join(dict.fromkeys(sources)),
        "theme": str(theme or (old or {}).get("theme") or by_id[subsumes_ids[0]].get("theme") or "").strip().lower(),
        "because": "",
        "rejected": "",
        "subsumes": list(subsumes_ids),
        "keeps": keeps,
        "not_for": _flat(not_for) or (old or {}).get("not_for") or "",
        "subsumed_by": "",
        "replaces": refines,
        "created_at": _now_iso(),
    }
    entries.append(entry)
    path = save_entries(memory_dir, entries)
    return {"ok": True, "principle": entry, "path": path}


def confirm_principle(memory_dir, pid, by, summary=None):
    """Owner confirms (and optionally refines) a proposed Principle.
    Marks the principle active and retires its `subsumes` rows (`status=subsumed`, `subsumed_by=pid`)
    so they stay in git as evidence and stop loading into `resume`. `by`: the id of the owner's comment or Like that
    says yes (kit.py checks it is his), kept on the row."""
    if not _flat(by):
        return {"error": "confirm needs the owner's yes: the id of his comment or Like"}
    with _locked(memory_dir):
        return _confirm(memory_dir, pid, _flat(by), summary)


def _confirm(memory_dir, pid, by, summary):
    entries = load_entries(memory_dir)
    target = next((e for e in entries if e["id"] == pid), None)
    if not target:
        return {"error": "principle %s not found" % pid}
    if target["kind"] not in ("principle", "field"):
        return {"error": "%s is %s, not a principle or a field" % (pid, target["kind"])}
    if target["status"] != "proposed":
        return {"error": "%s is %s, not proposed" % (pid, target["status"])}
    if _flat(summary):
        target["summary"] = _flat(summary)
    target["status"] = "active"
    target["confirmed_by"] = by
    subsumed = []
    old = target.get("replaces")
    for e in entries:
        if old and e["id"] == old and e["kind"] == target["kind"]:
            e["status"] = "replaced"
        elif old and e["kind"] == "field" and e["status"] in ("active", "proposed") and old in (e.get("subsumes") or []):
            e["subsumes"] = [pid if i == old else i for i in e["subsumes"]]        # the refined principle stays in its field
        elif old and e.get("subsumed_by") == old:
            e["subsumed_by"] = pid
    if target["kind"] == "field":                                                   # its members stay, opened by it
        return {"ok": True, "field": target, "subsumed": [], "path": save_entries(memory_dir, entries)}
    want = set(target.get("subsumes") or []) - set(target.get("keeps") or [])
    for e in entries:
        if e["id"] in want and e["status"] == "active":
            e["status"] = "subsumed"
            e["subsumed_by"] = pid
            subsumed.append(e["id"])
    path = save_entries(memory_dir, entries)
    return {"ok": True, "principle": target, "subsumed": subsumed, "path": path}


def _matches_scope(entry, role, train=None, files=None, resume=False):
    """Check whether entry's scope applies to (role, train, files): one of its alternatives holds whole. `resume`: a
    `files:` alternative is the JIT hook's, at the edit, and does not count."""
    role_l, train_l = str(role or "").lower(), str(train or "").lower()
    if role_l == "clerk":
        return False
    for g in scope_groups(entry.get("scope")):
        low = [s.lower() for s in g]
        if resume and _is_files_group(g):
            continue
        roles = [s for s in low if s.startswith("role:")]
        if roles and "role:" + role_l not in roles:
            continue
        trains = [s for s in low if s.startswith("train:")]
        if trains and "train:" + train_l not in trains:
            continue
        globs = [s.split(":", 1)[1].strip() for s in g if s.lower().startswith("files:")]
        if globs and not any(fnmatch.fnmatch(f, x) or fnmatch.fnmatch(os.path.basename(f), x) for x in globs for f in files or []):
            continue
        return True
    return False


def is_hook_enforced(entry):
    enf = str(entry.get("enforced_by") or "").strip().lower()
    return enf.startswith("hook:") or enf.startswith("test:")


def for_files(memory_dir, file_paths, kit_root=None, role=None, train=None):
    """Return active `files:<glob>` entries matching any path in `file_paths` (for JIT PreToolUse injection). A row also
    scoped to a role or a train comes only to that role or train: a session whose role is not known gets neither."""
    if not file_paths:
        return []
    entries = load_entries(memory_dir, kit_root=kit_root)
    hits = []
    for e in entries:
        if e.get("status") != "active" or is_hook_enforced(e):
            continue
        groups = [g for g in scope_groups(e.get("scope")) if _is_files_group(g)]
        if groups and _matches_scope({"scope": " | ".join(", ".join(g) for g in groups)}, role, train=train, files=file_paths):
            hits.append(e)
    hits.sort(key=lambda x: (-scope_priority(x.get("scope")), x["id"]))
    return hits


def for_resume(memory_dir, role, train=None, files=None, kit_root=None, skip_file_scoped=False):
    """Build compact, bounded resume sections from the ledger.

    - Hook/test-enforced rows are NEVER printed (only counted in a 1-line summary).
    - Subsumed/replaced/archived/dropped rows are NEVER printed.
    - Active Distilled Principles come first, then Decisions, then un-subsumed Observations/Rules.
    - Proposed Principles are surfaced to the orchestrator for owner confirmation.
    """
    entries = load_entries(memory_dir, kit_root=kit_root)
    if not entries:
        return {"principles": [], "decisions": [], "active": [], "proposed_principles": [],
                "hook_enforced_count": 0, "distil_ready": []}

    shut = _closed(entries)
    closed = {}
    principles, decisions, active, proposed = [], [], [], []
    hook_count = 0
    for e in entries:
        st = e.get("status")
        if st == "proposed" and e.get("kind") in ("principle", "field"):
            if role == "orchestrator":
                proposed.append(e)
            continue
        if st != "active" or e.get("kind") == "field":
            continue
        if is_hook_enforced(e):
            hook_count += 1
            continue
        if not _matches_scope(e, role, train=train, files=files, resume=skip_file_scoped):
            continue
        if e["id"] in shut:
            for f in shut[e["id"]]:
                closed.setdefault(f["id"], [f, 0])[1] += 1
            continue
        if e["kind"] == "principle":
            principles.append(e)
        elif e["kind"] == "decision":
            decisions.append(e)
        else:
            active.append(e)

    for bucket in (principles, decisions, active):
        bucket.sort(key=lambda x: (-scope_priority(x.get("scope")), x["id"]))

    distil_ready = moves(memory_dir) if role == "orchestrator" else []
    return {
        "principles": principles,
        "decisions": decisions,
        "active": active,
        "proposed_principles": proposed,
        "closed_fields": [closed[k] for k in sorted(closed)],
        "hook_enforced_count": hook_count,
        "distil_ready": distil_ready,
    }


def format_resume(memory_dir, role, train=None, files=None, kit_root=None, skip_file_scoped=False):
    """Render compact Markdown lines for `kit.py resume --role <role>`."""
    data = for_resume(memory_dir, role, train=train, files=files, kit_root=kit_root,
                      skip_file_scoped=skip_file_scoped)
    lines = []
    if data["principles"]:
        lines.append("")
        lines.append("## Distilled Principles (User Model)")
        lines += _grouped(data["principles"] + [r for r in data["active"] if any(r["id"] in (p.get("keeps") or []) for p in data["principles"])])
        kept = {k for p in data["principles"] for k in p.get("keeps") or []}
        data["active"] = [r for r in data["active"] if r["id"] not in kept]
    if data["decisions"]:
        lines.append("")
        lines.append("## Settled Decisions")
        for d in data["decisions"]:
            extra = []
            if d.get("because"):
                extra.append("because: %s" % d["because"])
            if d.get("rejected"):
                extra.append("rejected: %s" % d["rejected"])
            tail = (" [%s]" % "; ".join(extra)) if extra else ""
            lines.append("- **%s**: %s%s" % (d["id"], d["summary"], tail))
    if data["active"]:
        lines.append("")
        lines.append("## Active Rules & Observations (un-subsumed)")
        for r in data["active"]:                # the rule alone: its words and source stay in the ledger (`kit.py ledger show`)
            lines.append("- **%s**: %s%s" % (r["id"], r["summary"], (" (`%s`)" % r["source"]) if r["kind"] == "lesson" and r.get("source") else ""))
    if data.get("closed_fields"):
        lines += ["", "## Fields (a script opens each when you touch its files or reach its moment; until then nothing to read)"]
        lines += ["- %s (%d): %s; opens at %s" % (f.get("theme") or f["id"], n, f["summary"], f["opens"]) for f, n in data["closed_fields"]]
    if data["principles"] and (data["active"] or data["decisions"]):
        lines += ["", "*When two disagree: a decision, then the exact rule, then the principle; between two of a kind, his newest word.*"]
    if data["proposed_principles"]:
        lines.append("")
        lines.append("## Proposed, waiting on the owner (one question for all: `kit.py ledger moves --ask --deadline <date>`)")
        for p in data["proposed_principles"]:
            lines.append("- **%s**: %s (subsumes %s)" % (p["id"], p["summary"], ", ".join(p.get("subsumes") or [])))
    if data["distil_ready"]:
        lines.append("")
        lines.append("## Rules that could move a step (%d): `kit.py ledger moves`" % len(data["distil_ready"]))
    if data["hook_enforced_count"]:
        lines.append("")
        lines.append("*(+ %d hook/test-enforced invariants active silently; not repeated here)*" % data["hook_enforced_count"])
    return lines


def check_ledger(memory_dir, root=None, comments=None):
    """Validate ledger integrity:
    1. Every `enforced_by: hook:<name>` exists in installed hooks (turns red if missing).
    2. Every `quote` whose `source` is a board comment ID present in `comments` appears in that comment.
    3. Every `subsumed` entry points to an existing active Principle.
    """
    entries = load_entries(memory_dir, kit_root=root)
    by_id = {e["id"]: e for e in entries}
    errors = []
    warnings = []
    hook_dirs = []
    if root:
        for rel in (".kit/hooks", ".claude/hooks/kit", ".agents/hooks/kit", "kit/hooks", ".kit"):
            d = os.path.join(root, rel)
            if os.path.isdir(d):
                hook_dirs.append(d)
    for e in entries:
        err = scope_error(e.get("scope"))
        if err and e["status"] in ("active", "proposed"):
            errors.append("%s: %s" % (e["id"], err))
        if e["status"] == "active" and str(e.get("enforced_by") or "").startswith("hook:"):
            hname = e["enforced_by"].split(":", 1)[1].strip()
            if hook_dirs and not any(os.path.isfile(os.path.join(d, hname)) for d in hook_dirs):
                errors.append("%s: enforced_by hook:%s not found in installed hooks" % (e["id"], hname))
        if e["status"] == "subsumed":
            pid = e.get("subsumed_by") or ""
            if pid not in by_id or by_id[pid].get("status") != "active":
                errors.append("%s: subsumed_by %r is not an active principle" % (e["id"], pid))
        if comments and e.get("quote") and e.get("source"):
            src = e["source"].split(",")[0].strip()
            if src in comments:
                body = str((comments[src] or {}).get("body") or (comments[src] or {}).get("text") or "")
                q = _flat(e["quote"].split("|")[0])
                if q and q.lower() not in _flat(body).lower():
                    errors.append("%s: quote not found verbatim in comment %s" % (e["id"], src))
        if e["kind"] == "field" and e["status"] in ("active", "proposed"):
            why = opens_error(e.get("opens"))
            if why:
                errors.append("%s: %s" % (e["id"], why))
            gone = [i for i in e.get("subsumes") or [] if (by_id.get(i) or {}).get("status") != "active"]
            if gone:
                errors.append("%s: members not active: %s" % (e["id"], ", ".join(gone)))
        for k in (e.get("keeps") or []) if e["status"] == "active" else []:
            if (by_id.get(k) or {}).get("status") not in ("active", "replaced"):
                errors.append("%s: keeps %s, which is not active" % (e["id"], k))
        if e["status"] == "proposed" and e["kind"] == "principle":
            warnings.append("%s: proposed principle waiting on owner confirmation" % e["id"])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "count": len(entries)}


def init_ledger(memory_dir):
    """Bootstrap `<memory_dir>/ledger.md`, importing any legacy `<memory_dir>/principles.md` lines. Its scopes are bare
    words (`part`, a train): a role becomes role:<r>, another word train:<w>; a clerk line is named in `skipped`."""
    with _locked(memory_dir):
        return _init(memory_dir)


def _init(memory_dir):
    p = ledger_path(memory_dir)
    if os.path.isfile(p):
        return {"ok": True, "path": p, "imported": 0, "note": "ledger already exists"}
    entries = []
    legacy = os.path.join(memory_dir, "principles.md")
    imported, skipped = 0, []
    if os.path.isfile(legacy):
        try:
            with open(legacy, encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s.startswith(("-", "*")):
                        continue
                    text = s.lstrip("-* ").strip()
                    m = re.search(r"\(scope:\s*([^)]+)\)", text, re.I)
                    scopes = []
                    for w in (_norm_scopes(m.group(1)) if m else ["all"]):
                        lw = w.lower()
                        scopes.append(lw if lw in ("all", "project") else "role:" + lw if lw in SCOPE_ROLES + ("clerk",)
                                      else w if ":" in w else "train:" + lw)
                    scopes = [x for x in scopes if x != "role:clerk"]
                    if not scopes or scope_error(scopes):
                        skipped.append(text)
                        continue
                    clean = re.sub(r"\s*\(scope:\s*[^)]+\)", "", text, flags=re.I).strip()
                    if clean:
                        imported += 1
                        entries.append({
                            "id": "P%d" % imported,
                            "kind": "principle",
                            "status": "active",
                            "summary": clean,
                            "scope": scopes,
                            "enforced_by": "review",
                            "quote": "",
                            "source": "principles.md",
                            "theme": "",
                            "because": "",
                            "rejected": "",
                            "subsumes": [],
                            "subsumed_by": "",
                            "replaces": "",
                            "created_at": _now_iso(),
                        })
        except OSError:
            pass
    save_entries(memory_dir, entries)
    return {"ok": True, "path": p, "imported": imported, "skipped": skipped}
