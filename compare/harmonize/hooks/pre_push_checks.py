"""BEFORE A PUSH — run the checks CI's fast job runs, and refuse the push while they fail.

Every push runs .github/workflows/build.yml, and a red run emails the owner. On 2026-11-09 a story
state written with short paths and six decisions failed CI's story checker on four pushes in a row,
and the owner asked why he kept getting failed-build emails that waste Actions. The checker takes a
fraction of a second here; so a push waits for it, and a fault is fixed before it reaches GitHub.

Only `git push` is gated (parsed as pre_tool_use.py parses, so a commit message that names a push
is not); every other command passes untouched. Its test: python3 -I .claude/hooks/test_pre_push_checks.py.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pre_tool_use import segments, strip_prefix  # noqa: E402

# The fast checks of CI's python-checks job that read only the repository. Since 2026-11-10 the
# owner's Actions minutes ran out and CI runs only by hand: the push gate is where these now run.
CHECKS = [
    [".claude/skills/story/scripts/check.py"],
    [".claude/skills/story/scripts/test_check.py"],
    [".claude/hooks/test_pre_tool_use.py"],
    [".claude/tools/harness_check.py"],
    # No work done twice (docs/DATA.md § Sources, part D2): every download registered, nothing stored fetched
    # again, store pushes by their owner. The store's listing is cached; offline it says PARTIAL, not OK.
    ["scripts/check-double-work.py"],
    # Every rule in force has its row (part M1e; docs/RULES.md, the owner's 23:51 on drift): a rule copied into a
    # prompt in force, docs/BOARD*.md, CLAUDE.md or a hook without its row stops the push; since part M1f a retired
    # wording ("$0", "Like = go") used anywhere in their text too, and the 23:28 replay stays red. ~1.7 s.
    ["scripts/test_rules_check.py"],
]


def is_push(command: str) -> bool:
    for seg in segments(command):
        words = strip_prefix(seg)
        if not words or os.path.basename(words[0]) != "git":
            continue
        args = words[1:]
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-C", "-c") else 1
        if i < len(args) and args[i] == "push":
            return True
    return False


# check-double-work.py lists the store over HTTPS: 62 s alone on 2026-11-11, and at 60 s the gate crashed with a
# traceback instead of an answer. A slow check is now a named failure, never a crash.
TIMEOUT_S = 240


def failures(root: str) -> list[str]:
    out = []
    for script in CHECKS:
        path = os.path.join(root, script[0])
        if not os.path.exists(path):
            continue
        try:
            r = subprocess.run([sys.executable, "-I", path, *script[1:]], cwd=root, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            out.append(f"{script[0]}:\nno answer after {TIMEOUT_S} s (the store's listing can be slow: run it again)")
            continue
        if r.returncode != 0:
            out.append(f"{script[0]}:\n{(r.stdout + r.stderr).strip()}")
    return out


def main() -> int:
    # `--all`: run the checks now, outside a tool call. end-session.sh pushes from inside a script,
    # which this hook never sees (found 2026-11-10: a /goal file over its limit reached main that way).
    if sys.argv[1:] == ["--all"]:
        bad = failures(os.getcwd())
        for b in bad:
            print(b, file=sys.stderr)
        return 1 if bad else 0
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    if not is_push(command):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    bad = failures(root)
    if not bad:
        return 0
    print("REFUSED — a push that CI would fail (and email the owner about).\n\n" + "\n\n".join(bad)
          + "\n\nFix these, commit, then push again.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
