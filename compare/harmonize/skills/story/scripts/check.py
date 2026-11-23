"""Checks the shape of .story/ — the states, the issues' status lines, awaits-anlon.md, and their pointers.

    python3 -I .claude/skills/story/scripts/check.py [repository root, default .]

A rule written in words is read askew; this one is checked. Exit 0 and "STORY: OK", or the list of faults
and exit 1. The rules are the story skill's (.claude/skills/story/SKILL.md):
- a state: 60 lines at most; its title "# State — <file name>"; its four sections in order; "Where it stands"
  in 10 lines at most; a "Next step" of ONE line; 5 decisions at most;
- an issue: the title on line 1, `Status: open — YYYY-MM-DD, …` or `Status: closed — YYYY-MM-DD, …` on line 2;
- awaits-anlon.md: each point starts with `- [workstream] ` naming an existing state, carries `Default: ` and
  ends with ` · deadline YYYY-MM-DD`, in 320 characters at most (continuation lines included); a paused
  workstream (its Next step starts with "Paused") has one point at most; a point past its deadline is NAMED
  (a warning, exit 0) so that the next session applies the default and records it;
- everywhere: a cited path (in backticks, with a slash) and a cited issue exist.

Adapted on 2026-11-08 from the verifier of the owner's Aplomb project (.claude/skills/story/scripts/verifier.py).
Its self-test, which plants each fault and checks that it is caught: test_check.py, next to this file.
"""
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
STORY = ROOT / ".story"
SECTIONS = ["## Where it stands", "## Next step", "## Recent decisions and why", "## Open"]
STATUS = re.compile(r"^Status: (open|closed) — \d{4}-\d{2}-\d{2}, .+$")
PATH = re.compile(r"`([^`\s]+\.(?:md|kt|kts|py|sh|json|yml|yaml|xml|toml|properties|txt|pdf|png))(?::[\d-]+)?`")
ISSUE = re.compile(r"\.story/issues/(\d{4}-\d\d-\d\d-[a-z0-9-]+)")
POINT = re.compile(r"^- \[([^\]]+)\] ")
DEADLINE = re.compile(r" · deadline (\d{4}-\d{2}-\d{2})$")

faults: list[str] = []
overdue: list[str] = []


def pointers(p: Path, text: str) -> None:
    for c in PATH.findall(text):
        # A pattern or an elision is not a path: `<name>`, `*.md`, `core/.../Grader.kt`.
        if "/" in c and not set("<*") & set(c) and "..." not in c and not (ROOT / c).exists():
            faults.append(f"{p.name}: dead pointer `{c}`")
    for n in ISSUE.findall(text):
        if not list((STORY / "issues").glob(f"{n}*")):
            faults.append(f"{p.name}: unknown issue {n}")


def section(lines: list[str], title: str) -> list[str]:
    i = lines.index(title)
    end = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("## ")), len(lines))
    return [x for x in lines[i + 1:end] if x.strip()]


states: dict[str, bool] = {}  # workstream -> paused
for p in sorted((STORY / "state").glob("*.md")):
    text = p.read_text(encoding="utf-8")
    lines = text.rstrip("\n").split("\n")
    if len(lines) > 60:
        faults.append(f"{p.name}: {len(lines)} lines, 60 at most")
    if lines[0] != f"# State — {p.stem}":
        faults.append(f"{p.name}: line 1 must be « # State — {p.stem} »")
    titles = [x for x in lines if x.startswith("## ")]
    if titles != SECTIONS:
        faults.append(f"{p.name}: sections {titles}, expected {SECTIONS}")
        states[p.stem] = False
        continue
    if len(section(lines, SECTIONS[0])) > 10:
        faults.append(f"{p.name}: « Where it stands » is longer than 10 lines")
    nxt = section(lines, SECTIONS[1])
    if len(nxt) != 1:
        faults.append(f"{p.name}: the « Next step » must fit on ONE line")
    states[p.stem] = bool(nxt) and nxt[0].startswith("Paused")
    if len([x for x in section(lines, SECTIONS[2]) if x.startswith("- ")]) > 5:
        faults.append(f"{p.name}: more than 5 decisions — the oldest leaves when one comes in")
    pointers(p, text)

for p in sorted((STORY / "issues").glob("*.md")):
    lines = p.read_text(encoding="utf-8").split("\n") + ["", ""]
    if not lines[0].startswith("# "):
        faults.append(f"{p.name}: line 1 is not the title")
    if not STATUS.match(lines[1]):
        faults.append(f"{p.name}: line 2 is neither « Status: open — date, … » nor « Status: closed — date, … »")
    pointers(p, "\n".join(lines))

aa = STORY / "awaits-anlon.md"
if aa.exists():
    today = datetime.date.today().isoformat()
    text = aa.read_text(encoding="utf-8")
    # A point: its line "- [workstream] …", then its indented lines; any other line closes it.
    points: list[list[str]] = []
    is_open = False
    for x in text.split("\n"):
        if x.startswith("- "):
            points.append([x])
            is_open = True
        elif x.startswith("  ") and is_open:
            points[-1].append(x)
        else:
            is_open = False
    per_stream: dict[str, int] = {}
    for pt in points:
        s = " ".join(x.strip() for x in pt)
        head = s[:60]
        m = POINT.match(s)
        if not m:
            faults.append(f"awaits-anlon.md: point without [workstream]: {head}")
        else:
            w = m.group(1)
            per_stream[w] = per_stream.get(w, 0) + 1
            if w not in states:
                faults.append(f"awaits-anlon.md: [{w}] has no state in .story/state/: {head}")
        if "Default: " not in s:
            faults.append(f"awaits-anlon.md: point without « Default: »: {head}")
        d = DEADLINE.search(s)
        if not d:
            faults.append(f"awaits-anlon.md: point without « · deadline YYYY-MM-DD » at its end: {head}")
        elif d.group(1) < today:
            overdue.append(f"{d.group(1)} {head}")
        if len(s) > 320:
            faults.append(f"awaits-anlon.md: point of {len(s)} characters, 320 at most: {head}")
    for w, paused in sorted(states.items()):
        if paused and per_stream.get(w, 0) > 1:
            faults.append(f"awaits-anlon.md: [{w}] is paused and has {per_stream[w]} points — one, its resumption")
    pointers(aa, text)

for o in overdue:
    print(f"OVERDUE — the default applies; record it where it acts: {o}")
if faults:
    print(f"STORY: {len(faults)} fault(s)\n  " + "\n  ".join(faults))
    sys.exit(1)
print("STORY: OK — " + ", ".join([
    f"{len(states)} states",
    f"{len(list((STORY / 'issues').glob('*.md')))} issues",
    "awaits-anlon.md" if aa.exists() else "no awaits-anlon.md",
]))
