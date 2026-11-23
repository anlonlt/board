"""Self-test of check.py: the real .story/ passes, and each planted fault is caught.

    python3 -I .claude/skills/story/scripts/test_check.py

A checker that has never been seen failing proves nothing. Each case copies .story/ into a temporary root
(the rest of the repository is linked, so that real pointers still resolve), plants one fault, runs the
checker there and compares its exit code and message with what is expected. Adapted on 2026-11-08 from the
Aplomb project's eprouver-file.py.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CHECK = Path(__file__).resolve().parent / "check.py"

POINT = "- [app] A question? Default: yes. — PLAN.md · deadline 2099-01-01"
STATE = """# State — app

Updated: 2099-01-01. Governing documents: `PLAN.md`.

## Where it stands
One fact.

## Next step
The smallest gesture.

## Recent decisions and why
- 2099-01-01 — a decision — a reason — `PLAN.md`

## Open
- nothing
"""
ISSUE = "# A defect\nStatus: open — 2099-01-01, what remains\n\nWhere: `PLAN.md`.\n"


def run(files: dict[str, str], code: int, expected: str, name: str) -> bool:
    d = Path(tempfile.mkdtemp())
    try:
        for x in REPO.iterdir():
            if x.name not in (".story", ".git"):
                os.symlink(x, d / x.name)
        shutil.copytree(REPO / ".story", d / ".story")
        for rel, content in files.items():
            f = d / ".story" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                f.unlink()
            else:
                f.write_text(content, encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(CHECK), str(d)], capture_output=True, text=True)
        out = r.stdout + r.stderr
        ok = r.returncode == code and expected in out
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n   code={r.returncode}\n   {out[:600]}"))
        return ok
    finally:
        shutil.rmtree(d)


head = "# What awaits Anlon\n\n"
st = "state/app.md"
results = [
    run({}, 0, "STORY: OK", "witness: the real .story/ passes"),
    run({st: STATE, "awaits-anlon.md": head + POINT + "\n"}, 0, "STORY: OK", "witness: a well-formed state and point pass"),
    run({st: STATE, "issues/2099-01-01-x.md": ISSUE}, 0, "STORY: OK", "witness: a well-formed issue passes"),
    # awaits-anlon.md
    run({st: STATE, "awaits-anlon.md": head + POINT.replace("Default: yes. ", "") + "\n"}, 1, "without « Default: »", "point without a default"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace(" · deadline 2099-01-01", "") + "\n"}, 1, "without « · deadline", "point without a deadline"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace(" — PLAN.md", "\n  " + "x" * 300 + " — PLAN.md") + "\n"}, 1, "320 at most", "point too long (continuation counted)"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace("- [app] ", "- ") + "\n"}, 1, "without [workstream]", "point without a workstream"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace("[app]", "[nope]") + "\n"}, 1, "[nope] has no state", "point for an unknown workstream"),
    run({st: STATE.replace("The smallest gesture.", "Paused — resumes after step 2."), "awaits-anlon.md": head + POINT + "\n" + POINT + "\n"}, 1, "is paused and has 2 points", "two points for a paused workstream"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace("2099-01-01", "2020-01-01") + "\n"}, 0, "OVERDUE — the default applies", "overdue: named, exit 0"),
    run({st: STATE, "awaits-anlon.md": head + POINT + "\nProse.\n  " + "y" * 300 + "\n"}, 0, "STORY: OK", "a prose line closes the point"),
    run({st: STATE, "awaits-anlon.md": head + POINT.replace("PLAN.md", "`docs/NOPE.md`") + "\n"}, 1, "dead pointer `docs/NOPE.md`", "dead pointer in awaits-anlon.md"),
    # states
    run({st: STATE.replace("The smallest gesture.", "The smallest gesture.\nAnd another."), }, 1, "fit on ONE line", "a next step on two lines"),
    run({st: STATE.replace("One fact.", "\n".join(["fact"] * 11))}, 1, "longer than 10 lines", "« Where it stands » too long"),
    run({st: STATE.replace("## Open\n- nothing\n", "## Open\n" + "- x\n" * 50)}, 1, "60 at most", "a state over 60 lines"),
    run({st: STATE.replace("## Open", "## Later")}, 1, "sections", "a section renamed"),
    run({st: STATE.replace("# State — app", "# State — other")}, 1, "line 1 must be", "a title that does not match the file"),
    run({st: STATE.replace("- 2099-01-01 — a decision", "- d\n- d\n- d\n- d\n- d\n- 2099-01-01 — a decision")}, 1, "more than 5 decisions", "six decisions"),
    run({st: STATE.replace("`PLAN.md`.", "`core/NOPE.kt`.")}, 1, "dead pointer `core/NOPE.kt`", "dead pointer in a state"),
    run({st: STATE.replace("One fact.", "One fact (`core/.../Grader.kt`, `.story/issues/<date>-x.md`).")}, 0, "STORY: OK", "an elision or a pattern is not a pointer"),
    run({st: STATE.replace("One fact.", "See .story/issues/2099-12-31-missing.")}, 1, "unknown issue 2099-12-31-missing", "a cited issue that does not exist"),
    # issues
    run({st: STATE, "issues/2099-01-01-x.md": ISSUE.replace("Status: open — 2099-01-01, what remains", "Status: open")}, 1, "line 2 is neither", "an issue without a dated status"),
    run({st: STATE, "issues/2099-01-01-x.md": ISSUE.replace("# A defect", "A defect")}, 1, "line 1 is not the title", "an issue without a title"),
]
print(f"{sum(results)}/{len(results)} cases as expected")
sys.exit(0 if all(results) else 1)
