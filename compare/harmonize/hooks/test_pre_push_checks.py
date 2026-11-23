"""Test of pre_push_checks.py: which commands are pushes, and a failing check refuses the push."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_push_checks as h  # noqa: E402

PUSHES = [
    "git push -u origin ccr-x",
    "git push",
    "cd /x && git push -q origin HEAD 2>&1 | tail -1",
    "git -C repo push origin x",
]
NOT_PUSHES = [
    'git commit -m "then git push"',
    "git status",
    "echo git push",
    "./gradlew :core:test",
]


def main() -> int:
    bad = [c for c in PUSHES if not h.is_push(c)] + [c for c in NOT_PUSHES if h.is_push(c)]
    # A check that fails refuses; one that passes lets the push through.
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, ".claude/skills/story/scripts"))
        script = os.path.join(root, ".claude/skills/story/scripts/check.py")
        with open(script, "w") as f:
            f.write("import sys\nprint('STORY: 1 fault(s)')\nsys.exit(1)\n")
        if not h.failures(root):
            bad.append("a failing check was not reported")
        with open(script, "w") as f:
            f.write("print('STORY: OK')\n")
        if h.failures(root):
            bad.append("a passing check was reported")
    if bad:
        print("FAILED:", *bad, sep="\n  ")
        return 1
    print(f"OK — {len(PUSHES)} pushes gated, {len(NOT_PUSHES)} other commands passed, a failing check refuses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
