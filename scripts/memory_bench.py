#!/usr/bin/env python3
"""Memory benchmark: what a fresh session of each memory tool is given, measured on a real project.

Every project item (rule, lesson, open question) is imported, unchanged and uncurated, into each tool
with that tool's OWN commands, in a temporary directory; the tool's OWN resume command is then run and its
output is measured:

  board             kit.py resume --role R (a subprocess, as a session runs it) + the JIT hook on the probe's files
  beads             bd remember / bd create / bd prime / bd ready, with a real `bd` (--bd PATH)
  ProjectArchitect  tools/rules_add.py / tools/card.py from a real PA 3 checkout (--pa DIR)
  flat              reference: every item in one file, read whole (the ceiling for recall, and for tokens)
  none              reference: no memory (the floor: its recall must be 0)

A tool that is not given is reported "not run": it is never imitated.

Rules of the measure:
- Nothing of the project is written here. Expected answers live in a probe file (--probes), as item ids, and are
  written without seeing any packet.
- Only an item's own text counts: an item is delivered when its full summary is in the packet. A probe's words in
  the packet's header, an id alone, or a cut summary are not recall.
- Memory tokens = the packet, minus the same tool's packet for the same probe with no items in it. Each tool's fixed
  instructions are so counted apart ("total").
- Nothing is distilled, confirmed, scoped or called hook-enforced on the owner's behalf ("carried by" in RULES.md
  names the files that state a rule, not what enforces it). A project's own ledger (--ledger) or its curation
  (--curation, {item id: {"scope": .., "enforced_by": ..}}) is used when given, and the report says so.
- Coverage is printed next to tokens: a small packet that dropped the rules is not a good one.

Limits: a human-written principle that paraphrases its rules earns no recall for them; ~4 chars/token is an estimate.

Usage:
    python3 -I scripts/memory_bench.py --project DIR --probes FILE [--bd PATH] [--pa DIR] [--curation FILE]
                                       [--ledger FILE] [--json]
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSED = ("done", "closed", "answered", "complete", "dropped")
ROLES = ("orchestrator", "part", "clerk", "review")
# what a running kit session exports: a bench run must not depend on who runs it (run_all_tests.py does the same)
SESSION_PREFIXES = ("KIT_", "CLAUDE", "AGY_", "BD_", "BEADS_", "PA_", "GIT_")


class ToolError(RuntimeError):
    """A tool's command failed: the run stops, it never falls back to an imitation."""


def est_tokens(text):
    return (len(text) + 3) // 4


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(SESSION_PREFIXES)}
    env.update(extra)
    return env


def _run(cmd, cwd, stdin=None, **env):
    r = subprocess.run(cmd, cwd=cwd, env=_clean_env(**env), capture_output=True, text=True, timeout=120, input=stdin)
    if r.returncode != 0:
        raise ToolError("%s\n  exit %d: %s" % (" ".join(map(str, cmd[:6])), r.returncode, (r.stderr or r.stdout)[-600:]))
    return r.stdout


# ---------------------------------------------------------------- the project, read only

def read_rules(path):
    """[{id, kind, summary, quote, source, theme}] of a docs/RULES.md table; X rows are dropped rules."""
    out, theme = [], ""
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("## "):
                theme = s[3:].strip().lower()
            if not s.startswith("|"):
                continue
            cols = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)[1:-1]]
            if len(cols) < 6 or not re.match(r"^R\d+[a-z]?$", cols[0]):
                continue
            out.append({"id": cols[0], "kind": "rule", "summary": cols[1], "quote": cols[2], "source": cols[4],
                        "theme": theme})
    return out


def read_lessons(project_dir):
    out = []
    for pat in (".story/lessons/*.md", ".board/memory/lessons/*.md"):
        for p in sorted(glob.glob(os.path.join(project_dir, pat))):
            with open(p, encoding="utf-8", errors="replace") as fh:
                txt = fh.read().strip()
            head = next((ln.lstrip("# ").strip() for ln in txt.splitlines() if ln.strip()), "")
            if head:
                out.append({"id": "L:" + os.path.splitext(os.path.basename(p))[0], "kind": "lesson", "summary": head,
                            "quote": "", "source": os.path.relpath(p, project_dir), "theme": ""})
    return out


def copy_db(project_dir, dest):
    """A copy of the board database (with its -wal), or None. The project's own is never opened: in WAL mode even a
    read-only open writes -shm and -wal files next to it."""
    src = os.path.join(project_dir, ".kit", "board_data", "board.db")
    if not os.path.isfile(src):
        return None
    for suf in ("", "-wal"):
        if os.path.isfile(src + suf):
            shutil.copyfile(src + suf, dest + suf)
    return dest


def read_questions(db):
    out = []
    if not db:
        return out
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT doc_id, data FROM docs WHERE collection='posts' ORDER BY doc_id").fetchall()
    finally:
        conn.close()
    for did, raw in rows:
        d = json.loads(raw)
        if isinstance(d, dict) and d.get("column") == "question" and str(d.get("status") or "").lower() not in CLOSED \
                and not d.get("removed"):
            out.append({"id": "Q:" + did, "kind": "question", "summary": str(d.get("title") or did), "quote": "",
                        "source": did, "theme": ""})
    return out


def ingest(project_dir, tmp):
    project_dir = os.path.abspath(project_dir)
    db = copy_db(project_dir, os.path.join(tmp, "project.db"))
    items = read_rules(os.path.join(project_dir, "docs", "RULES.md")) + read_lessons(project_dir) + read_questions(db)
    ids = [i["id"] for i in items]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ToolError("duplicate item ids in the project: %s" % ", ".join(dup))
    return {"project": os.path.basename(project_dir), "dir": project_dir, "db": db, "items": items}


def load_probes(path, snap):
    """The probe file, refused when an expected id is not an item of the project (a probe that can never pass)."""
    with open(path, encoding="utf-8") as fh:
        probes = json.load(fh)["probes"]
    known = {i["id"] for i in snap["items"]}
    for p in probes:
        if p.get("role") not in ROLES:
            raise ToolError("probe %s: unknown role %r" % (p.get("id"), p.get("role")))
        if not p.get("gold"):
            raise ToolError("probe %s: no gold items" % p["id"])
        bad = [g for g in p["gold"] if g not in known]
        if bad:
            raise ToolError("probe %s: not an item of %s: %s" % (p["id"], snap["project"], ", ".join(bad)))
    return probes


# ---------------------------------------------------------------- the tools
# Each: setup(snap, items, dir) once, with the items to hold (or none, for the empty control), then
# turn1(probe) and lookup(probe): what a session is given at its start, and what its tool's search then adds.

class Board:
    name = "board"

    def __init__(self, curation=None, ledger=None):
        self.curation, self.ledger = curation or {}, ledger

    def setup(self, snap, items, d):
        self.d = d
        kd = os.path.join(d, ".kit")
        os.makedirs(os.path.join(kd, "board_data"))
        for f in ("scripts/kit.py", "kit/ledger.py", "kit/lessons.md"):
            shutil.copyfile(os.path.join(ROOT, f), os.path.join(kd, os.path.basename(f)))
        with open(os.path.join(kd, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"project": snap["project"], "kit": {"memory_dir": ".board/memory"}}, fh)
        self.mem = os.path.join(d, ".board", "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(kd, "board_data", "board.db")
        keep = {i["source"] for i in items if i["kind"] == "question"}
        drop = {i["source"] for i in snap["items"] if i["kind"] == "question"} - keep
        self._db(snap["db"], drop)
        _run(["git", "init", "-q", "."], d)
        if self.ledger and items:
            shutil.copyfile(self.ledger, os.path.join(self.mem, "ledger.md"))
            return
        led = self._ledger()
        for i in items:
            if i["kind"] == "question":
                continue        # a question is a post: it is in the database
            cur = self.curation.get(i["id"], {})
            res = led.observe(self.mem, i["summary"], quote=i["quote"], source=i["source"], theme=i["theme"],
                              scope=cur.get("scope", "project"), kind=i["kind"], enforced_by=cur.get("enforced_by", "review"))
            if res.get("error"):
                raise ToolError("ledger observe %s: %s" % (i["id"], res["error"]))

    def _ledger(self):
        sys.path.insert(0, os.path.join(ROOT, "kit"))
        import ledger
        return ledger

    def _db(self, src, drop):
        if src:
            for suf in ("", "-wal"):
                if os.path.isfile(src + suf):
                    shutil.copyfile(src + suf, self.db + suf)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT, doc_id TEXT, data TEXT)")
        conn.executemany("DELETE FROM docs WHERE collection='posts' AND doc_id=?", [(x,) for x in drop])
        conn.commit()
        conn.close()

    def turn1(self, probe):
        cmd = [sys.executable, "-I", os.path.join(self.d, ".kit", "kit.py"), "resume", "--role", probe["role"], "--db", self.db]
        if probe["role"] in ("part", "review"):
            if not probe.get("post"):
                raise ToolError("probe %s: role %s needs a post" % (probe["id"], probe["role"]))
            cmd += ["--post", probe["post"]]
        out = _run(cmd, self.d)
        if out.startswith(("No post found", "Review: name the step")):
            raise ToolError("probe %s: kit.py resume: %s" % (probe["id"], out.strip()))
        return out

    def lookup(self, probe):
        """The JIT hook on each file the probe edits, then a grep of the ledger (no kit command searches it)."""
        out = []
        for f in probe.get("files") or []:
            # a TMPDIR of its own: the hook remembers there what a session has already seen
            tmp = tempfile.mkdtemp(dir=self.d)
            r = _run([sys.executable, "-I", os.path.join(ROOT, "kit", "hooks", "kit_memory_jit.py")], self.d, TMPDIR=tmp,
                     CLAUDE_PROJECT_DIR=self.d, stdin=json.dumps({
                         "cwd": self.d, "session_id": "bench", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": os.path.join(self.d, f)}}))
            if r.strip():
                out.append(json.loads(r)["hookSpecificOutput"]["additionalContext"])
        return "\n".join(out + [_grep(os.path.join(self.mem, "ledger.md"), probe)])


class Beads:
    name = "beads"

    def __init__(self, bd):
        self.bd = os.path.abspath(bd)

    def setup(self, snap, items, d):
        self.d = d
        _run(["git", "init", "-q", "."], d)
        self._bd("init", "--non-interactive", "--quiet", "--skip-hooks", "--skip-agents", "--prefix", "bench")
        for i in items:
            if i["kind"] == "question":
                self._bd("create", i["summary"], "--type", "task")
            else:
                self._bd("remember", i["summary"], "--key", re.sub(r"[^a-z0-9]+", "-", i["id"].lower()).strip("-"))

    def _bd(self, *args):
        # no telemetry, and dolt's global config in the sandbox, not in the home directory
        return _run([self.bd] + list(args), self.d, BD_DISABLE_METRICS="1", DO_NOT_TRACK="1",
                    DOLT_ROOT_PATH=self.d)

    def turn1(self, probe):
        return self._bd("prime") + "\n" + self._bd("ready")

    def lookup(self, probe):
        return "\n".join(self._bd("memories", t) for t in probe.get("lookup") or [])


class ProjectArchitect:
    """The kit role -> the PA role with the same place. An expert starts from CLAUDE.md, the project-architect skill,
    its agent file and its card slice; the rules are on disk (rules/INDEX.md), to be grepped. Open questions are not
    imported: PA keeps none but its own plan's."""
    name = "ProjectArchitect"
    AGENT = {"part": ("expert", "pa-expert"), "orchestrator": ("claude", "pa-claude"), "clerk": ("coder", "pa-coder"),
             "review": ("review", "auditor")}

    def __init__(self, pa):
        pa = os.path.abspath(pa)
        inner = os.path.join(pa, "project-architect-3.0")
        self.pa = inner if os.path.isdir(inner) else pa

    def _read(self, *rel):
        with open(os.path.join(self.pa, *rel), encoding="utf-8") as fh:
            return fh.read()

    def _tool(self, name, *args):
        return _run([sys.executable, os.path.join(self.pa, "tools", name)] + list(args), self.d, PA_PROJECT_ROOT=self.d,
                    PYTHONDONTWRITEBYTECODE="1")

    def setup(self, snap, items, d):
        self.d = d
        os.makedirs(os.path.join(d, ".claude"))
        for name, text in ((".claude/pa.json", "{}"), ("HOW_WE_WORK.md", self._read("templates", "HOW_WE_WORK.template.md")),
                           ("rules/INDEX.md", self._read("templates", "INDEX.rules.md"))):
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
        self._tool("card.py", "interview", "--defaults", "--name", "owner")
        n = 0
        for i in items:
            if i["kind"] == "question":
                continue
            n += 1
            self._tool("rules_add.py", "add", "--id", "N%d" % n, "--title", i["summary"].replace("|", "/"), "--tags",
                       re.sub(r"[^a-z0-9]+", "-", i["theme"]).strip("-") or i["kind"], "--origin", i["id"])

    def turn1(self, probe):
        role, agent = self.AGENT[probe["role"]]
        return "\n\n".join((self._read("templates", "CLAUDE.template.md"),
                            self._read("skills", "project-architect", "SKILL.template.md"),
                            self._read("agents", agent + ".md"), self._tool("card.py", "slice", role)))

    def lookup(self, probe):
        return _grep(os.path.join(self.d, "rules", "INDEX.md"), probe)


class Flat:
    name = "flat"

    def setup(self, snap, items, d):
        self.text = "\n".join("- %s: %s" % (i["id"], i["summary"]) for i in items)

    def turn1(self, probe):
        return self.text

    def lookup(self, probe):
        return ""


class Nothing:
    name = "none"

    def setup(self, snap, items, d):
        pass

    def turn1(self, probe):
        return "# Resume: %s %s\n%s\n" % (probe["role"], probe["id"], probe.get("question", ""))

    def lookup(self, probe):
        return ""


def _grep(path, probe):
    terms = [t.lower() for t in probe.get("lookup") or []]
    if not terms or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return "\n".join(ln.rstrip("\n") for ln in fh if any(t in ln.lower() for t in terms))


# ---------------------------------------------------------------- the measure

def delivered(text, items):
    """The ids whose full summary is in the text (whitespace and case aside; PA writes `|` as `/` in its index)."""
    hay = _norm(text)
    return {i["id"] for i in items if _norm(i["summary"]) in hay or _norm(i["summary"]).replace("|", "/") in hay}


def measure(tool, snap, probes, tmp):
    """One row per probe. The empty control is the same tool set up with no items."""
    full_d, empty_d = (os.path.join(tmp, tool.name + s) for s in ("-full", "-empty"))
    os.makedirs(full_d)
    os.makedirs(empty_d)
    tool.setup(snap, [], empty_d)
    base = [tool.turn1(p) for p in probes]
    # What a tool's packet holds with no items in it is not memory the bench can credit: a post's own words.
    base_hits = [delivered(b, snap["items"]) for b in base]
    tool.setup(snap, snap["items"], full_d)
    rows = []
    for p, b, bh in zip(probes, base, base_hits):
        gold = set(p["gold"])
        t1, lk = tool.turn1(p), tool.lookup(p)
        hit1 = delivered(t1, snap["items"]) - bh
        hit2 = hit1 | (delivered(lk, snap["items"]) - bh)
        rows.append({"probe": p["id"], "role": p["role"], "tokens_total": est_tokens(t1),
                     "tokens_memory": max(0, est_tokens(t1) - est_tokens(b)), "tokens_lookup": est_tokens(lk),
                     "gold": len(gold), "hit_turn1": len(hit1 & gold), "hit_lookup": len(hit2 & gold),
                     "delivered_turn1": len(hit1), "missed": sorted(gold - hit2),
                     "control_hits": len(bh & gold)})
    return rows


def summarize(rows, n_items):
    n, gold = len(rows), sum(r["gold"] for r in rows)
    s = lambda k: sum(r[k] for r in rows)   # noqa: E731
    return {"tokens_total": round(s("tokens_total") / n), "tokens_memory": round(s("tokens_memory") / n),
            "tokens_lookup": round(s("tokens_lookup") / n), "gold": gold, "hit_turn1": s("hit_turn1"),
            "hit_lookup": s("hit_lookup"), "items": n_items, "coverage": round(100.0 * s("delivered_turn1") / (n * n_items), 1),
            "precision": round(100.0 * s("hit_turn1") / s("delivered_turn1"), 1) if s("delivered_turn1") else None,
            "control_hits": s("control_hits")}


def run_benchmark(project_dir, probes_path, tools, not_run=()):
    with tempfile.TemporaryDirectory(prefix="mem-bench-") as tmp:
        snap = ingest(project_dir, tmp)
        if not snap["items"]:
            raise ToolError("no rules, lessons or open questions found in %s" % project_dir)
        probes = load_probes(probes_path, snap)
        kinds = [i["kind"] for i in snap["items"]]
        rep = {"project": snap["project"], "items": {k: kinds.count(k) for k in ("rule", "lesson", "question")},
               "probes": len(probes), "tools": {}, "not_run": list(not_run)}
        for t in tools:
            rows = measure(t, snap, probes, tmp)
            rep["tools"][t.name] = dict(summarize(rows, len(snap["items"])), rows=rows)
    return rep


def format_report(rep):
    pct = lambda a, b: "%d/%d (%.0f%%)" % (a, b, 100.0 * a / b)   # noqa: E731
    out = ["# Memory benchmark: %s" % rep["project"],
           "%d rules, %d lessons, %d open questions; %d probes" % (rep["items"]["rule"], rep["items"]["lesson"],
                                                                    rep["items"]["question"], rep["probes"])]
    out += list(rep.get("notes") or [])
    out += ["", "| tool | recall, turn 1 | recall, after its lookup | items in packet (coverage) | precision | memory tokens | "
            "lookup tokens | total tokens |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, s in rep["tools"].items():
        out.append("| %s | %s | %s | %.0f%% | %s | %d | %d | %d |" % (
            name, pct(s["hit_turn1"], s["gold"]), pct(s["hit_lookup"], s["gold"]), s["coverage"],
            "-" if s["precision"] is None else "%.0f%%" % s["precision"], s["tokens_memory"], s["tokens_lookup"], s["tokens_total"]))
    out += ["| %s | not run: %s | | | | | | |" % (n, why) for n, why in rep["not_run"]]
    names = list(rep["tools"])
    out += ["", "Per probe: gold found at turn 1 > after lookup / gold, memory tokens", "",
            "| probe (role) | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in range(rep["probes"]):
        rows = [rep["tools"][n]["rows"][k] for n in names]
        out.append("| %s (%s) | %s |" % (rows[0]["probe"], rows[0]["role"], " | ".join(
            "%d>%d/%d %dt" % (r["hit_turn1"], r["hit_lookup"], r["gold"], r["tokens_memory"]) for r in rows)))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--probes", required=True, help="JSON {probes: [{id, role, post, files, question, gold, lookup}]}")
    ap.add_argument("--bd", default=None, help="a real beads `bd` binary (default: beads is not run)")
    ap.add_argument("--pa", default=None, help="a real ProjectArchitect 3 checkout (default: not run)")
    ap.add_argument("--curation", default=None, help="JSON {item id: {scope, enforced_by}} for the board (default: none)")
    ap.add_argument("--ledger", default=None, help="the project's own, owner-confirmed ledger.md, for the board")
    ap.add_argument("--json", dest="as_json", action="store_true")
    a = ap.parse_args(argv)
    curation, notes = None, []
    if a.curation:
        with open(a.curation, encoding="utf-8") as fh:
            curation = json.load(fh)
        notes.append("board curation: %s" % a.curation)
    if a.ledger:
        notes.append("board ledger: %s" % a.ledger)
    tools, not_run = [Nothing(), Flat(), Board(curation, a.ledger)], []
    for cls, val, flag in ((Beads, a.bd, "--bd"), (ProjectArchitect, a.pa, "--pa")):
        if val:
            tools.append(cls(val))
        else:
            not_run.append((cls.name, "no %s" % flag))
    try:
        rep = run_benchmark(a.project, a.probes, tools, not_run)
    except ToolError as e:
        print("memory_bench: %s" % e, file=sys.stderr)
        return 1
    rep["notes"] = notes
    print(json.dumps(rep, indent=2, ensure_ascii=False) if a.as_json else format_report(rep), end="\n" if a.as_json else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
