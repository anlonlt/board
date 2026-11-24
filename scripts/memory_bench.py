#!/usr/bin/env python3
"""Memory benchmark & fresh-session simulator: `board` vs `gastownhall/beads` vs `Druthulu/ProjectArchitect`.

Ingests a real project's sprawled state read-only (board.db, docs/RULES.md, .story/lessons,
.story/state, docs/DESIGN-PRINCIPLES.md) into one canonical snapshot, adapts that exact snapshot
into all three tools' native formats inside isolated temp directories, and measures what a fresh
session receives at Turn 1 across concrete historical probes and scaling curves (20 / 100 / 300 rules).

Usage:
    python3 -I scripts/memory_bench.py --project /home/anon/harmonize
    python3 -I scripts/memory_bench.py --project /home/anon/harmonize --scale 20,100,300 --json
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "kit"))
sys.path.insert(0, os.path.join(ROOT, "kit", "hooks"))

import kit  # noqa: E402
import ledger  # noqa: E402
import kit_memory_jit  # noqa: E402


def est_tokens(text):
    """Deterministic ~4 chars/token estimate (no external tokenizer dependency)."""
    return max(1, (len(str(text or "")) + 3) // 4) if text else 0


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-") or "general"


# ---------------------------------------------------------------------------
# 1. Canonical Ingestor (read-only on any existing project)
# ---------------------------------------------------------------------------

def ingest_rules_md(path):
    """Parse Harmonize-style docs/RULES.md table into normalized rule dicts."""
    if not os.path.isfile(path):
        return []
    out = []
    theme = "general"
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("## "):
                theme = _slug(s[3:])
                continue
            if not s.startswith("|") or s.startswith("|---") or s.lower().startswith("| id "):
                continue
            cols = [c.strip() for c in s.strip("|").split("|")]
            if len(cols) < 6:
                continue
            rid, rule, quote, when, source, carried = cols[:6]
            if not re.match(r"^[RX]\d+[a-z]?$", rid):
                continue
            if rid.startswith("X"):
                continue  # dropped rule
            carried_l = carried.lower()
            hook_m = re.search(r"(kit_[a-z_]+\.py|say_it_once\.py|pre_tool_use\.py|pre_push_checks\.py|stop\.sh|board_typing\.py)", carried_l)
            enforced_by = ("hook:" + hook_m.group(1)) if hook_m else "review"
            scope = "project"
            rule_l = rule.lower()
            if "clerk" in rule_l and "orchestrator" not in rule_l:
                scope = "role:clerk"
            elif "orchestrator" in rule_l and "part" not in rule_l and "session" not in rule_l:
                scope = "role:orchestrator"
            elif "modal" in rule_l or "budget" in rule_l:
                scope = "files:pipeline/modal*"
            elif "picture" in rule_l or "phone" in rule_l or "width" in rule_l or "zoom" in rule_l:
                scope = "files:kit/board/*.html"
            out.append({
                "id": rid,
                "summary": rule,
                "quote": quote,
                "when": when,
                "source": source,
                "carried_by": carried,
                "theme": theme,
                "enforced_by": enforced_by,
                "scope": scope,
            })
    return out


def ingest_lessons(project_dir):
    out = []
    for pat in (".story/lessons/*.md", ".board/memory/lessons/*.md"):
        for p in sorted(glob.glob(os.path.join(project_dir, pat))):
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read().strip()
            except OSError:
                continue
            first = next((l.lstrip("# ").strip() for l in txt.splitlines() if l.strip()), os.path.basename(p))
            out.append({
                "id": "L-" + _slug(os.path.splitext(os.path.basename(p))[0])[:24],
                "title": first[:140],
                "text": txt[:600],
                "source": os.path.relpath(p, project_dir),
            })
    return out


def ingest_board_db(project_dir):
    db = os.path.join(project_dir, ".kit", "board_data", "board.db")
    posts, questions, trains = {}, [], {}
    if not os.path.isfile(db):
        return posts, questions, trains
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        for did, raw in conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts'"):
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            posts[did] = d
            if d.get("column") == "question" and str(d.get("status") or "").lower() not in ("done", "closed", "answered"):
                questions.append({"id": did, "title": d.get("title") or did, "train": d.get("train") or "",
                                  "status": d.get("status") or "open"})
            if did == "_trains":
                trains = d
    finally:
        conn.close()
    return posts, questions, trains


def ingest_states(project_dir):
    out = []
    for pat in (".story/state/*.md", ".board/memory/state/*.md"):
        for p in sorted(glob.glob(os.path.join(project_dir, pat))):
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read().strip()
            except OSError:
                continue
            name = os.path.splitext(os.path.basename(p))[0]
            first = next((l.strip() for l in txt.splitlines() if l.strip()), name)
            out.append({"name": name, "head": first[:160], "chars": len(txt)})
    return out


def ingest_project(project_dir):
    """Read a real repo's sprawled artifacts into one normalized canonical snapshot."""
    project_dir = os.path.abspath(project_dir)
    rules = ingest_rules_md(os.path.join(project_dir, "docs", "RULES.md"))
    lessons = ingest_lessons(project_dir)
    posts, questions, trains = ingest_board_db(project_dir)
    states = ingest_states(project_dir)
    # Canonical settled decisions (seeded from project history if not already in rules)
    decisions = [
        {
            "id": "D1",
            "summary": "Train VR2 on karaoke filter v3; do not skip training",
            "scope": "project",
            "because": "owner round kept 14/16 on v3; R2 approved training",
            "rejected": "skipping VR2 training",
            "source": "board:4o0u7mrhr79phpae9rc0",
        },
        {
            "id": "D2",
            "summary": "Keep app niche on phone speaker only — never require earphones",
            "scope": "all",
            "because": "core product differentiator",
            "rejected": "requiring headphones or earphones for pitch/harmony",
            "source": "board:q3bqybde38i449uge7il",
        },
    ]
    return {
        "project": os.path.basename(project_dir),
        "project_dir": project_dir,
        "rules": rules,
        "lessons": lessons,
        "decisions": decisions,
        "posts": posts,
        "questions": questions,
        "trains": trains,
        "states": states,
    }


# ---------------------------------------------------------------------------
# 2. Three Faithful Tool Adapters (isolated temp dirs, apples-to-apples)
# ---------------------------------------------------------------------------

def build_board_context(snap, probe, tmpdir):
    """Adapter 1: `board` (3-layer distilled ledger + kit.py resume --role + JIT files: hook)."""
    proj = Path(tmpdir) / "board-sim"
    if not proj.exists():
        (proj / ".kit").mkdir(parents=True)
        (proj / ".kit" / "config.json").write_text(json.dumps({"project": snap["project"], "kit": {}}))
        (proj / ".kit" / "ledger.py").write_text((Path(ROOT) / "kit" / "ledger.py").read_text())
        mdir = proj / ".board" / "memory"
        mdir.mkdir(parents=True)
        # Copy real board.db if present so resume reads real posts/questions
        src_db = Path(snap["project_dir"]) / ".kit" / "board_data" / "board.db"
        if src_db.is_file():
            (proj / ".kit" / "board_data").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_db, proj / ".kit" / "board_data" / "board.db")
        # Populate 3-layer ledger
        for r in snap["rules"]:
            ledger.observe(str(mdir), r["summary"], quote=r["quote"], source=r["source"],
                           scope=r["scope"], theme=r["theme"], kind="rule", enforced_by=r["enforced_by"])
        for d in snap["decisions"]:
            ledger.observe(str(mdir), d["summary"], source=d["source"], scope=d["scope"],
                           kind="decision", because=d["because"], rejected=d["rejected"])
        for les in snap["lessons"]:
            ledger.observe(str(mdir), les["title"], source=les["source"], scope="role:orchestrator",
                           theme="lessons", kind="lesson")
        # Run automatic Rule -> Principle distillation on every theme cluster >= 3
        for cl in ledger.distil_candidates(str(mdir), min_cluster=3):
            # Compact theme principle summarizing the cluster while preserving all quotes
            sample = "; ".join(it["summary"][:70] for it in cl["items"][:2])
            pr = ledger.propose_principle(
                str(mdir),
                "Theme %s (%d rules consolidated): %s" % (cl["theme"], cl["count"], sample),
                cl["ids"], scope="project", theme=cl["theme"],
            )
            if pr.get("ok"):
                ledger.confirm_principle(str(mdir), pr["principle"]["id"])

    mdir = proj / ".board" / "memory"
    role = probe["role"]
    files = probe.get("files") or []
    # 1. Role-scoped resume from ledger (skipping files: scoped rows since JIT hook injects them!)
    parts = []
    if role == "orchestrator":
        q_lines = ["## Open questions on the board"] + [
            "- `%s`: %s (%s)" % (q["id"], q["title"], q["status"]) for q in snap["questions"][:20]
        ]
        parts.extend(q_lines)
    elif role == "clerk":
        parts.append("# Resume: clerk (routing & session ids only)")
    else:
        parts.append("# Resume: part %s (%s)" % (probe["id"], probe["prompt"]))
    parts.extend(ledger.format_resume(str(mdir), role, train=probe.get("train"), files=files,
                                      kit_root=str(proj), skip_file_scoped=True))
    # 2. JIT PreToolUse hook injection for target files (if any)
    if files:
        for f in files:
            out = kit_memory_jit.handle({
                "cwd": str(proj),
                "session_id": "bench-" + probe["id"],
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(proj / f)},
            })
            if out and out.get("hookSpecificOutput", {}).get("additionalContext"):
                parts.append("")
                parts.append(out["hookSpecificOutput"]["additionalContext"])
    return "\n".join(parts)


def build_beads_context(snap, probe, tmpdir):
    """Adapter 2: `gastownhall/beads` (bd prime + bd ready + flat bd remember memories)."""
    proj = Path(tmpdir) / "beads-sim"
    bdir = proj / ".beads"
    if not bdir.exists():
        bdir.mkdir(parents=True)
        # Issues graph (open questions + active workstreams)
        issues = []
        for q in snap["questions"]:
            issues.append({"id": "bd-" + _slug(q["id"])[:12], "title": q["title"], "ref": q["id"],
                           "status": "ready", "type": "question", "deps": []})
        for st in snap["states"][:15]:
            issues.append({"id": "bd-" + _slug(st["name"])[:12], "title": st["head"],
                           "status": "open", "type": "workstream", "deps": []})
        (bdir / "issues.jsonl").write_text("\n".join(json.dumps(x) for x in issues) + "\n")
        # Flat `bd remember` memories (beads has no role-scoping, hook-stripping, or distillation)
        mems = []
        for r in snap["rules"]:
            mems.append({"id": r["id"], "memory": r["summary"], "source": r["source"]})
        for d in snap["decisions"]:
            mems.append({"id": d["id"], "memory": "%s (rejected: %s)" % (d["summary"], d["rejected"])})
        for les in snap["lessons"]:
            mems.append({"id": les["id"], "memory": les["title"]})
        (bdir / "memories.jsonl").write_text("\n".join(json.dumps(x) for x in mems) + "\n")

    # Fresh session runs: `bd prime` + `bd ready` + `bd memories`
    lines = [
        "# bd prime (gastownhall/beads)",
        "Use `bd ready` to list unblocked beads, `bd update <id> --claim` to claim, `bd remember` to store insights.",
        "",
        "## bd ready",
    ]
    for raw in (bdir / "issues.jsonl").read_text().splitlines():
        if not raw.strip():
            continue
        it = json.loads(raw)
        if it.get("status") == "ready":
            lines.append("- [%s] %s (%s)" % (it["id"], it["title"], it.get("ref", "")))
    lines += ["", "## bd memories (flat durable facts)"]
    for raw in (bdir / "memories.jsonl").read_text().splitlines():
        if not raw.strip():
            continue
        m = json.loads(raw)
        lines.append("- %s: %s" % (m["id"], m["memory"]))
    return "\n".join(lines)


def build_pa_context(snap, probe, tmpdir):
    """Adapter 3: `Druthulu/ProjectArchitect` (ROADMAP + PHASE-PLAN + DECISIONS + RULES + HANDOFF)."""
    proj = Path(tmpdir) / "pa-sim"
    pdir = proj / ".project-architect"
    if not pdir.exists():
        pdir.mkdir(parents=True)
        # ROADMAP.md (Generational Plan)
        rm = ["# Generational Plan (Roadmap)", ""] + [
            "- **%s**: %s" % (st["name"], st["head"]) for st in snap["states"]
        ]
        (pdir / "ROADMAP.md").write_text("\n".join(rm) + "\n")
        # PHASE-PLAN.md (Active open items & gates)
        pp = ["# Active Phase Plan & Gates", ""] + [
            "- [ ] `%s`: %s" % (q["id"], q["title"]) for q in snap["questions"]
        ]
        (pdir / "PHASE-PLAN.md").write_text("\n".join(pp) + "\n")
        # DECISIONS.md (Decision Ledger)
        dl = ["# Decision Ledger", ""] + [
            "- **%s**: %s (because: %s; rejected: %s)" % (d["id"], d["summary"], d["because"], d["rejected"])
            for d in snap["decisions"]
        ]
        (pdir / "DECISIONS.md").write_text("\n".join(dl) + "\n")
        # RULES.md (Rules & Techniques — full repo ledger)
        rl = ["# Rules & Techniques", ""] + [
            "- **%s** [%s]: %s%s" % (r["id"], r["theme"], r["summary"],
                                     (' — "%s"' % r["quote"][:100]) if r["quote"] else "")
            for r in snap["rules"]
        ] + [
            "- **%s**: %s" % (les["id"], les["title"]) for les in snap["lessons"]
        ]
        (pdir / "RULES.md").write_text("\n".join(rl) + "\n")
        # HANDOFF.md (Subagent boot journal)
        (pdir / "HANDOFF.md").write_text(
            "# Fresh Subagent Handoff\n\n"
            "Project: %s. Read PHASE-PLAN.md, DECISIONS.md, and RULES.md before starting.\n" % snap["project"]
        )

    # Fresh subagent loads HANDOFF + PHASE-PLAN + DECISIONS + RULES (+ ROADMAP for orchestrator)
    files = ["HANDOFF.md", "PHASE-PLAN.md", "DECISIONS.md", "RULES.md"]
    if probe["role"] == "orchestrator":
        files.insert(1, "ROADMAP.md")
    return "\n\n".join((pdir / f).read_text().strip() for f in files)


# ---------------------------------------------------------------------------
# 3. Real Harmonize Probes & Evaluation
# ---------------------------------------------------------------------------

DEFAULT_PROBES = [
    {
        "id": "S11c",
        "role": "part",
        "train": "audio",
        "files": ["app/audio/feedback.kt"],
        "prompt": "Implement audio feedback for pitch training",
        "must_contain": ["speaker", "earphone"],
        "must_not_leak": ["modal", "say_it_once", "git push"],
    },
    {
        "id": "AC1",
        "role": "part",
        "train": "backing",
        "files": ["pipeline/datasets.py"],
        "prompt": "Check dataset licensing blockers for backing tracks",
        "must_contain": ["licen"],
        "must_not_leak": ["modal", "say_it_once"],
    },
    {
        "id": "VR2",
        "role": "part",
        "train": "voice",
        "files": ["pipeline/vr2.py"],
        "prompt": "Should we skip training on VR2?",
        "must_contain": ["vr2", "rejected"],
        "must_not_leak": ["modal", "say_it_once"],
    },
    {
        "id": "O51",
        "role": "orchestrator",
        "train": None,
        "files": [],
        "prompt": "What is currently waiting on the owner?",
        "must_contain": ["vr5-second-run-question", "hl5-harmony-ai-question"],
        "must_not_leak": ["say_it_once"],
    },
    {
        "id": "UI-Phone",
        "role": "part",
        "train": "cli",
        "files": ["kit/board/board.template.html"],
        "prompt": "Add a delete action to a board post on mobile",
        "must_contain": ["phone"],
        "must_not_leak": ["modal", "say_it_once"],
    },
    {
        "id": "Clerk",
        "role": "clerk",
        "train": None,
        "files": [],
        "prompt": "Forward an owner comment to a running part",
        "must_contain": ["clerk"],
        "must_not_leak": ["vr2", "modal", "earphone"],
    },
]


def score_packet(text, probe):
    low = str(text or "").lower()
    req = probe.get("must_contain") or []
    forb = probe.get("must_not_leak") or []
    hits = [k for k in req if k.lower() in low]
    leaks = [k for k in forb if k.lower() in low]
    return {
        "tokens": est_tokens(text),
        "chars": len(text or ""),
        "recall_hit": len(hits),
        "recall_total": len(req),
        "recall_pct": round(100.0 * len(hits) / len(req), 1) if req else 100.0,
        "leaks": leaks,
        "leak_count": len(leaks),
    }


def run_benchmark(project_dir, probes=None, scales=()):
    snap = ingest_project(project_dir)
    probes = probes or DEFAULT_PROBES
    adapters = (
        ("board", build_board_context),
        ("beads", build_beads_context),
        ("ProjectArchitect", build_pa_context),
    )
    results = {name: [] for name, _ in adapters}
    with tempfile.TemporaryDirectory(prefix="mem-bench-") as tmp:
        for pr in probes:
            for name, fn in adapters:
                ctx = fn(snap, pr, tmp)
                sc = score_packet(ctx, pr)
                sc["probe"] = pr["id"]
                sc["role"] = pr["role"]
                results[name].append(sc)

    summary = {}
    for name, rows in results.items():
        avg_tok = round(sum(r["tokens"] for r in rows) / max(1, len(rows)))
        tot_hit = sum(r["recall_hit"] for r in rows)
        tot_req = sum(r["recall_total"] for r in rows)
        tot_leaks = sum(r["leak_count"] for r in rows)
        summary[name] = {
            "avg_tokens": avg_tok,
            "recall": "%d/%d (%.0f%%)" % (tot_hit, tot_req, 100.0 * tot_hit / max(1, tot_req)),
            "total_leaks": tot_leaks,
            "probes": rows,
        }

    # Scaling curve: synthetic observations across 5 themes at N = scales
    scale_rows = []
    themes = ("ui", "reporting", "testing", "audio", "git")
    for n in scales:
        syn = {
            "project": "scale-%d" % n,
            "project_dir": project_dir,
            "rules": [
                {
                    "id": "R%d" % i,
                    "summary": "Rule %d on %s behavior for step quality check" % (i, themes[i % len(themes)]),
                    "quote": "owner quote %d" % i,
                    "when": "2026-11-23",
                    "source": "board:c%d" % i,
                    "carried_by": ".claude/hooks/kit_guard.py" if (i % 3 == 0) else "docs/BOARD.md",
                    "theme": themes[i % len(themes)],
                    "enforced_by": "hook:kit_guard.py" if (i % 3 == 0) else "review",
                    "scope": "project",
                }
                for i in range(1, n + 1)
            ],
            "lessons": [],
            "decisions": snap["decisions"],
            "posts": {},
            "questions": snap["questions"][:5],
            "trains": {},
            "states": snap["states"][:5],
        }
        row = {"n_rules": n}
        with tempfile.TemporaryDirectory(prefix="mem-scale-") as tmp:
            p0 = {"id": "Scale", "role": "part", "train": "cli", "files": [], "prompt": "Implement feature"}
            for name, fn in adapters:
                row[name] = est_tokens(fn(syn, p0, tmp))
        scale_rows.append(row)

    return {
        "project": snap["project"],
        "ingested": {
            "rules": len(snap["rules"]),
            "lessons": len(snap["lessons"]),
            "decisions": len(snap["decisions"]),
            "open_questions": len(snap["questions"]),
            "states": len(snap["states"]),
        },
        "summary": summary,
        "scaling": scale_rows,
    }


def format_report(rep):
    ing = rep["ingested"]
    lines = [
        "# Memory Benchmark: %s (board vs beads vs ProjectArchitect)" % rep["project"],
        "Ingested read-only: %d rules, %d lessons, %d decisions, %d open questions, %d states" % (
            ing["rules"], ing["lessons"], ing["decisions"], ing["open_questions"], ing["states"]),
        "",
        "## Overall Scorecard (6 fresh-session probes)",
        "| System | Avg Turn-1 Tokens | Signal Recall | Scope/Noise Leaks |",
        "|---|---:|---:|---:|",
    ]
    for name in ("board", "beads", "ProjectArchitect"):
        s = rep["summary"][name]
        lines.append("| %s | ~%d | %s | %d |" % (name, s["avg_tokens"], s["recall"], s["total_leaks"]))

    lines += [
        "",
        "## Per-Probe Breakdown (Tokens / Recall / Leaks)",
        "| Probe (Role) | board | beads | ProjectArchitect |",
        "|---|---|---|---|",
    ]
    n_probes = len(rep["summary"]["board"]["probes"])
    for i in range(n_probes):
        p = rep["summary"]["board"]["probes"][i]
        cells = []
        for name in ("board", "beads", "ProjectArchitect"):
            r = rep["summary"][name]["probes"][i]
            cells.append("~%dt · %d/%d · %dL" % (r["tokens"], r["recall_hit"], r["recall_total"], r["leak_count"]))
        lines.append("| %s (%s) | %s | %s | %s |" % (p["probe"], p["role"], cells[0], cells[1], cells[2]))

    if rep["scaling"]:
        lines += [
            "",
            "## Scaling Curve (Turn-1 Part Context Tokens as Rules Grow)",
            "| Rules Accumulated | board (distilled + hooks silent) | beads (flat memories) | ProjectArchitect (full RULES.md) |",
            "|---:|---:|---:|---:|",
        ]
        for row in rep["scaling"]:
            lines.append("| %d | ~%d | ~%d | ~%d |" % (
                row["n_rules"], row["board"], row["beads"], row["ProjectArchitect"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True, help="Path to an existing project (e.g. /home/anon/harmonize)")
    ap.add_argument("--scale", default="20,100,300", help="Comma-separated rule counts for scaling curve (or empty)")
    ap.add_argument("--json", dest="as_json", action="store_true")
    a = ap.parse_args(argv)
    scales = [int(x) for x in a.scale.split(",") if x.strip().isdigit()] if a.scale else []
    rep = run_benchmark(a.project, scales=scales)
    if a.as_json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(format_report(rep), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
