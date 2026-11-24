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
     startup prompts.

Also supports `kind=decision` (with `because:` and `rejected:`) so settled architectural/product
choices are never re-litigated.

Storage: Markdown at `<memory_dir>/ledger.md` (and optional kit-global `kit/ledger.md` / `.kit/ledger.md`).
No external dependencies (stdlib only).
"""
import fnmatch
import json
import os
import re
from datetime import datetime, timezone

KINDS = ("observation", "rule", "decision", "principle", "lesson")
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


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_scopes(scope):
    if isinstance(scope, (list, tuple, set)):
        raw = list(scope)
    else:
        raw = re.split(r"[,;]+", str(scope or "project"))
    out = []
    for s in raw:
        t = s.strip()
        if t and t not in out:
            out.append(t)
    return out or ["project"]


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
    for line in str(text or "").splitlines():
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
                "created_at": "",
                "_file": source,
            }
            entries.append(cur)
            continue
        if cur is None:
            continue
        kv = KV_LINE.match(line.strip())
        if not kv:
            continue
        k, v = kv.group(1).lower(), kv.group(2).strip()
        if k == "scope":
            cur["scope"] = _norm_scopes(v)
        elif k == "subsumes":
            cur["subsumes"] = [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]
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
        out.append("## %s (%s, %s): %s" % (e["id"], e["kind"], e["status"], e["summary"]))
        out.append("scope: %s" % ", ".join(_norm_scopes(e.get("scope"))))
        if e.get("enforced_by"):
            out.append("enforced_by: %s" % e["enforced_by"])
        if e.get("theme"):
            out.append("theme: %s" % e["theme"])
        if e.get("quote"):
            out.append("quote: %s" % e["quote"])
        if e.get("source"):
            out.append("source: %s" % e["source"])
        if e.get("because"):
            out.append("because: %s" % e["because"])
        if e.get("rejected"):
            out.append("rejected: %s" % e["rejected"])
        if e.get("subsumes"):
            out.append("subsumes: %s" % ", ".join(e["subsumes"]))
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
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(render_ledger(entries))
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
    a, b = set(_norm_scopes(s1)), set(_norm_scopes(s2))
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
    summary = str(summary or "").strip()
    if not summary:
        return {"error": "summary is required"}
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
        "quote": str(quote or "").strip(),
        "source": str(source or "").strip(),
        "theme": str(theme or "").strip().lower(),
        "because": str(because or "").strip(),
        "rejected": str(rejected or "").strip(),
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

    Clusters first by explicit `theme`, then by shared scope + keyword overlap for unthemed entries.
    """
    entries = load_entries(memory_dir)
    pool = [e for e in entries if e.get("status") == "active" and e.get("kind") in ("observation", "rule", "lesson")
            and not e.get("subsumed_by") and not str(e.get("enforced_by") or "").startswith(("hook:", "test:"))]
    by_theme = {}
    unthemed = []
    for e in pool:
        th = (e.get("theme") or "").strip().lower()
        if th:
            by_theme.setdefault(th, []).append(e)
        else:
            unthemed.append(e)

    clusters = []
    for th, items in sorted(by_theme.items()):
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


def propose_principle(memory_dir, summary, subsumes_ids, scope="all", theme="", pid=None):
    """Create a `proposed` Principle citing all quotes/sources of `subsumes_ids`.
    Underlying rows remain active until the owner confirms the principle."""
    entries = load_entries(memory_dir)
    by_id = {e["id"]: e for e in entries}
    missing = [i for i in subsumes_ids if i not in by_id]
    if missing:
        return {"error": "unknown observation/rule id(s): %s" % ", ".join(missing)}
    if not subsumes_ids:
        return {"error": "subsumes_ids must name at least one underlying observation or rule"}
    quotes = [by_id[i]["quote"] for i in subsumes_ids if by_id[i].get("quote")]
    sources = [by_id[i]["source"] for i in subsumes_ids if by_id[i].get("source")]
    pid = pid or _next_id(entries, "P")
    if pid in by_id:
        return {"error": "principle %s already exists" % pid}
    entry = {
        "id": pid,
        "kind": "principle",
        "status": "proposed",
        "summary": str(summary).strip(),
        "scope": _norm_scopes(scope),
        "enforced_by": "review",
        "quote": " | ".join(quotes),
        "source": ", ".join(dict.fromkeys(sources)),
        "theme": str(theme or by_id[subsumes_ids[0]].get("theme") or "").strip().lower(),
        "because": "",
        "rejected": "",
        "subsumes": list(subsumes_ids),
        "subsumed_by": "",
        "replaces": "",
        "created_at": _now_iso(),
    }
    entries.append(entry)
    path = save_entries(memory_dir, entries)
    return {"ok": True, "principle": entry, "path": path}


def confirm_principle(memory_dir, pid, summary=None):
    """Owner confirms (and optionally refines) a proposed Principle.
    Marks the principle active and retires its `subsumes` rows (`status=subsumed`, `subsumed_by=pid`)
    so they stay in git as evidence and stop loading into `resume`."""
    entries = load_entries(memory_dir)
    target = next((e for e in entries if e["id"] == pid), None)
    if not target:
        return {"error": "principle %s not found" % pid}
    if target["kind"] != "principle":
        return {"error": "%s is %s, not a principle" % (pid, target["kind"])}
    if summary and str(summary).strip():
        target["summary"] = str(summary).strip()
    target["status"] = "active"
    subsumed = []
    want = set(target.get("subsumes") or [])
    for e in entries:
        if e["id"] in want and e["status"] == "active":
            e["status"] = "subsumed"
            e["subsumed_by"] = pid
            subsumed.append(e["id"])
    path = save_entries(memory_dir, entries)
    return {"ok": True, "principle": target, "subsumed": subsumed, "path": path}


def _matches_scope(entry, role, train=None, files=None):
    """Check whether entry's scope applies to (role, train, files)."""
    scopes = _norm_scopes(entry.get("scope"))
    role_l = str(role or "").lower()
    train_l = str(train or "").lower()
    has_role_constraint = any(s.lower().startswith("role:") for s in scopes)
    if has_role_constraint and ("role:" + role_l) not in {s.lower() for s in scopes}:
        return False
    has_train_constraint = any(s.lower().startswith("train:") for s in scopes)
    if has_train_constraint and (not train_l or ("train:" + train_l) not in {s.lower() for s in scopes}):
        return False
    file_globs = [s.split(":", 1)[1].strip() for s in scopes if s.lower().startswith("files:")]
    if file_globs:
        if files is None:
            return False
        if not any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g)
                   for g in file_globs for f in files):
            return False
    if role_l == "clerk":
        return any(s.lower() in ("role:clerk",) for s in scopes)
    return True


def is_hook_enforced(entry):
    enf = str(entry.get("enforced_by") or "").strip().lower()
    return enf.startswith("hook:") or enf.startswith("test:")


def for_files(memory_dir, file_paths, kit_root=None):
    """Return active `files:<glob>` entries matching any path in `file_paths` (for JIT PreToolUse injection)."""
    if not file_paths:
        return []
    entries = load_entries(memory_dir, kit_root=kit_root)
    hits = []
    for e in entries:
        if e.get("status") != "active" or is_hook_enforced(e):
            continue
        globs = [s.split(":", 1)[1].strip() for s in _norm_scopes(e.get("scope")) if s.lower().startswith("files:")]
        if not globs:
            continue
        if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in globs for f in file_paths):
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

    principles, decisions, active, proposed = [], [], [], []
    hook_count = 0
    for e in entries:
        st = e.get("status")
        if st == "proposed" and e.get("kind") == "principle":
            if role == "orchestrator":
                proposed.append(e)
            continue
        if st != "active":
            continue
        if is_hook_enforced(e):
            hook_count += 1
            continue
        scopes = _norm_scopes(e.get("scope"))
        if skip_file_scoped and any(s.lower().startswith("files:") for s in scopes):
            continue
        if not _matches_scope(e, role, train=train, files=files):
            continue
        if e["kind"] == "principle":
            principles.append(e)
        elif e["kind"] == "decision":
            decisions.append(e)
        else:
            active.append(e)

    for bucket in (principles, decisions, active):
        bucket.sort(key=lambda x: (-scope_priority(x.get("scope")), x["id"]))

    distil_ready = distil_candidates(memory_dir) if role == "orchestrator" else []
    return {
        "principles": principles,
        "decisions": decisions,
        "active": active,
        "proposed_principles": proposed,
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
        for p in data["principles"]:
            sub = " (subsumes %s)" % ", ".join(p["subsumes"]) if p.get("subsumes") else ""
            lines.append("- **%s**: %s%s" % (p["id"], p["summary"], sub))
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
        for r in data["active"]:
            q = (' — "%s"' % r["quote"][:120]) if r.get("quote") else ""
            lines.append("- **%s** (%s): %s%s" % (r["id"], ", ".join(r["scope"]), r["summary"], q))
    if data["proposed_principles"]:
        lines.append("")
        lines.append("## Proposed Principles awaiting owner confirmation")
        for p in data["proposed_principles"]:
            lines.append("- **%s**: %s (subsumes %s)" % (p["id"], p["summary"], ", ".join(p.get("subsumes") or [])))
    if data["distil_ready"]:
        lines.append("")
        lines.append("## Ready to distil into Principles")
        for c in data["distil_ready"]:
            lines.append("- theme `%s` (%d observations: %s) -> `kit.py ledger distil`" % (
                c["theme"], c["count"], ", ".join(c["ids"])))
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
        for rel in (".claude/hooks/kit", ".agents/hooks/kit", "kit/hooks", ".kit"):
            d = os.path.join(root, rel)
            if os.path.isdir(d):
                hook_dirs.append(d)
    for e in entries:
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
                q = e["quote"].split("|")[0].strip()
                if q and q.lower() not in body.lower():
                    errors.append("%s: quote not found verbatim in comment %s" % (e["id"], src))
        if e["status"] == "proposed" and e["kind"] == "principle":
            warnings.append("%s: proposed principle waiting on owner confirmation" % e["id"])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "count": len(entries)}


def init_ledger(memory_dir):
    """Bootstrap `<memory_dir>/ledger.md`, importing any legacy `<memory_dir>/principles.md` lines."""
    p = ledger_path(memory_dir)
    if os.path.isfile(p):
        return {"ok": True, "path": p, "imported": 0, "note": "ledger already exists"}
    entries = []
    legacy = os.path.join(memory_dir, "principles.md")
    imported = 0
    if os.path.isfile(legacy):
        try:
            with open(legacy, encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s.startswith(("-", "*")):
                        continue
                    text = s.lstrip("-* ").strip()
                    m = re.search(r"\(scope:\s*([^)]+)\)", text, re.I)
                    scopes = _norm_scopes(m.group(1)) if m else ["all"]
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
    return {"ok": True, "path": p, "imported": imported}
