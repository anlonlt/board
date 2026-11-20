#!/usr/bin/env python3
"""The kit's guard, agy PreToolUse on run_command: refuses the gestures that destroy work, and only those.

A recursive rm outside /tmp; git clean (not a dry run); git reset --hard; a force push (--force, -f, --force-with-lease,
a +refspec); deleting a remote branch; git add of a file matching `kit.guard.never_add` in .kit/config.json.
The command is parsed with shlex, so a commit message that MENTIONS rm -rf is not refused (a guard that refuses too
much gets disabled). Output follows agy's PreToolUse JSON contract: {"decision": "allow"|"deny", "reason": "..."}.
"""
import fnmatch
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config  # noqa: E402

OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"}
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "exec", "builtin"}


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    out, cur = [], []
    try:
        tokens = list(lexer)
    except ValueError:  # an unbalanced quote: let the shell report it, judge the words we have
        tokens = command.split()
    for t in tokens:
        if t in OPERATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def strip_prefix(words: list[str]) -> list[str]:
    while words and ("=" in words[0] and not words[0].startswith("-") and words[0].split("=")[0].isidentifier()):
        words = words[1:]  # FOO=bar cmd
    while words and words[0] in WRAPPERS:
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
    if words and words[0] == "xargs":
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
    return words


def verdict(words: list[str], never_add=(), allow_add=()) -> str | None:
    words = strip_prefix(words)
    if not words:
        return None
    cmd = words[0].rsplit("/", 1)[-1]
    args = words[1:]

    if cmd in ("bash", "sh", "zsh") and "-c" in args:
        i = args.index("-c")
        if i + 1 < len(args):
            return check(args[i + 1], never_add, allow_add)
        return None
    if cmd in ("bash", "sh", "zsh") and args and not args[0].startswith("-"):
        return verdict(args, never_add, allow_add)

    if cmd == "rm":
        flags = [a for a in args if a.startswith("-") and a != "--"]
        targets = [a for a in args if not a.startswith("-")]
        recursive = any(a in ("--recursive",) or (not a.startswith("--") and set("rR") & set(a[1:])) for a in flags)
        outside = [t for t in targets if not t.startswith("/tmp/")]
        if recursive and (outside or not targets):
            return f"a recursive rm outside /tmp ({' '.join(outside[:3]) or 'targets from stdin'})"
        return None

    if cmd == "find":
        if "-delete" in args:
            outside = [a for a in args if not a.startswith("-") and not a.startswith("/tmp/")]
            if outside or not any(a.startswith("/tmp/") for a in args):
                return "find -delete outside /tmp"
        return None

    if cmd == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
        if i >= len(args):
            return None
        sub, rest = args[i], args[i + 1:]
        if sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
            return "git branch -D (force-deleting a branch)"
        if sub == "clean" and not ({"-n", "--dry-run"} & set(rest)) and not any(
                a.startswith("-") and not a.startswith("--") and "n" in a for a in rest):
            return "git clean"
        if sub == "reset" and "--hard" in rest:
            return "git reset --hard"
        if sub == "push":
            if any(a in ("--force", "-f", "--force-with-lease", "--force-if-includes") or a.startswith("--force-with-lease=")
                   or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]) for a in rest):
                return "a force push"
            if any(a.startswith("+") for a in rest):
                return "a force push (a +refspec)"
            if any(a == "--delete" or (a.startswith("-") and not a.startswith("--") and "d" in a[1:])
                   or (a.startswith(":") and len(a) > 1) for a in rest):
                return "deleting a remote branch (the owner's call)"
        if sub == "add":
            bad = [a for a in rest if any(fnmatch.fnmatch(a, g) or fnmatch.fnmatch(os.path.basename(a), g) for g in never_add)
                   and not any(a.endswith(x) for x in allow_add)]
            if bad:
                return f"git add of a file the config never lets in ({' '.join(bad)})"
    return None


def check(command: str, never_add=(), allow_add=()) -> str | None:
    for seg in segments(command):
        v = verdict(seg, never_add, allow_add)
        if v:
            return v
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print(json.dumps({"decision": "allow"}))
        return 0
    g = config(data).get("kit", {}).get("guard", {})
    never_add = g.get("never_add", [])
    allow_add = g.get("allow_add", [])
    tc = data.get("toolCall") or {}
    args = tc.get("args") or {}
    command = args.get("CommandLine") or args.get("command") or (data.get("tool_input") or {}).get("command") or ""
    reason = check(command, never_add, allow_add)
    if not reason:
        print(json.dumps({"decision": "allow"}))
        return 0
    msg = (
        f"REFUSED: {reason}.\n\n"
        "The kit's guard (.agents/hooks/kit/kit_guard.py): work lost to a deletion cannot be recovered. The ways out:\n"
        "  - a tracked file or folder: git rm <path>, in a commit of its own;\n"
        "  - a commit to undo: git revert <commit> (history stays; nothing is forced);\n"
        "  - scratch files: keep them under /tmp, where rm -r is allowed.\n"
        "If the owner explicitly asked for this exact command, say so and ask them to run it."
    )
    print(json.dumps({"decision": "deny", "reason": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
