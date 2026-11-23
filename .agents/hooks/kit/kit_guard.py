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
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, project_dir  # noqa: E402

OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"}
WRAPPERS = {"sudo", "command", "env", "nohup", "time", "exec", "builtin"}
DESTRUCTIVE_EXEC = {"rm", "unlink", "shred", "sh", "bash", "zsh", "dash"}
INLINE_TREE_DELETE_RE = re.compile(
    r"\b(?:shutil\.rmtree|fs\.rmSync|fs\.rmdirSync|fs\.promises\.rm|FileUtils\.rm_rf)\s*\(\s*(?:(['\"])([^'\"]*)\1)?"
)


def _is_under_tmp(path: str, cwd: str | None = None) -> bool:
    if not path or not os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    real = os.path.realpath(path)
    tmp_real = os.path.realpath("/tmp")
    return (norm.startswith("/tmp/") or norm.startswith(tmp_real + os.sep)) and real.startswith(tmp_real + os.sep)


def _matches_never_add(path: str, never_add, allow_add) -> bool:
    norm = path.lstrip("./")
    base = os.path.basename(norm)
    if any(norm.endswith(x) or base == x for x in allow_add):
        return False
    return any(fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(base, g) or fnmatch.fnmatch(path, g) for g in never_add)


def _git_add_candidates(base_dir: str, targets: list[str], all_mode: bool, update_mode: bool) -> list[str]:
    if not base_dir or not os.path.isdir(base_dir):
        return []
    r = subprocess.run(["git", "-C", base_dir, "status", "--porcelain", "-uall"], capture_output=True, text=True, timeout=10)
    files = []
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if len(line) < 4:
                continue
            status = line[:2]
            if update_mode and not all_mode and status == "??":
                continue
            p = line[3:].strip()
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            p = p.strip('"')
            files.append(p)
    else:
        for root, dirs, fnames in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d != ".git"]
            for fn in fnames:
                files.append(os.path.relpath(os.path.join(root, fn), base_dir))
    if all_mode or not targets or any(t in (".", "./", "..", "*") for t in targets):
        return files
    norm_targets = [os.path.normpath(t).rstrip("/") for t in targets]
    out = []
    for f in files:
        nf = os.path.normpath(f)
        if any(nf == nt or nf.startswith(nt + os.sep) for nt in norm_targets):
            out.append(f)
    return out


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


def verdict(words: list[str], never_add=(), allow_add=(), cwd: str | None = None) -> str | None:
    words = strip_prefix(words)
    if not words:
        return None
    cmd = words[0].rsplit("/", 1)[-1]
    args = words[1:]

    if cmd in ("bash", "sh", "zsh") and "-c" in args:
        i = args.index("-c")
        if i + 1 < len(args):
            return check(args[i + 1], never_add, allow_add, cwd=cwd)
        return None
    if cmd in ("bash", "sh", "zsh") and args and not args[0].startswith("-"):
        return verdict(args, never_add, allow_add, cwd=cwd)

    if cmd.startswith(("python", "pypy")) or cmd in ("node", "nodejs", "bun", "deno", "ruby", "perl"):
        for flag in ("-c", "-e", "--eval"):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    code = args[i + 1]
                    for m in INLINE_TREE_DELETE_RE.finditer(code):
                        target = m.group(2) or ""
                        if not _is_under_tmp(target, cwd):
                            return f"inline {cmd} {flag} recursive delete outside /tmp ({target or 'dynamic target'})"
                break

    if cmd == "rm":
        flags, targets, after_dd = [], [], False
        for a in args:
            if not after_dd and a == "--":
                after_dd = True
                continue
            if not after_dd and a.startswith("-"):
                flags.append(a)
            else:
                targets.append(a)
        recursive = any(a in ("--recursive",) or (not a.startswith("--") and set("rR") & set(a[1:])) for a in flags)
        outside = [t for t in targets if not _is_under_tmp(t, cwd)]
        if recursive and (outside or not targets):
            return f"a recursive rm outside /tmp ({' '.join(outside[:3]) or 'targets from stdin'})"
        return None

    if cmd == "find":
        roots = []
        for a in args:
            if a.startswith("-") or a in ("!", "(", ")"):
                break
            roots.append(a)
        all_in_tmp = bool(roots) and all(_is_under_tmp(r, cwd) for r in roots)
        if "-delete" in args and not all_in_tmp:
            return "find -delete outside /tmp"
        for idx, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                sub = []
                for tok in args[idx + 1:]:
                    if tok in (";", "+"):
                        break
                    sub.append(tok)
                sub_clean = strip_prefix(sub)
                if not sub_clean:
                    continue
                scmd = sub_clean[0].rsplit("/", 1)[-1]
                if scmd == "rm" and all_in_tmp:
                    continue
                if scmd in DESTRUCTIVE_EXEC:
                    return f"find {a} {scmd} outside /tmp"
                v = verdict(sub_clean, never_add, allow_add, cwd=cwd)
                if v:
                    return f"find {a}: {v}"
        return None

    if cmd == "git":
        i = 0
        git_cwd = cwd
        while i < len(args) and args[i].startswith("-"):
            if args[i] == "-C" and i + 1 < len(args):
                val = args[i + 1]
                git_cwd = val if os.path.isabs(val) else os.path.join(git_cwd or os.getcwd(), val)
                i += 2
            elif args[i] in ("-c", "--git-dir", "--work-tree", "--namespace"):
                i += 2
            else:
                i += 1
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
        if sub == "add" and never_add:
            flags, targets, after_dd = [], [], False
            for a in rest:
                if not after_dd and a == "--":
                    after_dd = True
                    continue
                if not after_dd and a.startswith("-"):
                    flags.append(a)
                else:
                    targets.append(a)
            bad = [a for a in targets if _matches_never_add(a, never_add, allow_add)]
            if bad:
                return f"git add of a file the config never lets in ({' '.join(bad)})"
            all_mode = any(f in ("-A", "--all") or (not f.startswith("--") and "A" in f[1:]) for f in flags)
            update_mode = any(f in ("-u", "--update") or (not f.startswith("--") and "u" in f[1:]) for f in flags)
            base_dir = git_cwd or os.getcwd()
            has_broad = all_mode or update_mode or any(
                t in (".", "./", "..", "*") or os.path.isdir(os.path.join(base_dir, t))
                for t in targets
            )
            if has_broad:
                cands = _git_add_candidates(base_dir, targets, all_mode, update_mode)
                hits = [f for f in cands if _matches_never_add(f, never_add, allow_add)]
                if hits:
                    return f"git add would stage a file the config never lets in ({' '.join(hits[:5])})"
    return None


def check(command: str, never_add=(), allow_add=(), cwd: str | None = None) -> str | None:
    for seg in segments(command):
        v = verdict(seg, never_add, allow_add, cwd=cwd)
        if v:
            return v
    return None


def session_launches(command: str) -> int:
    """How many new agent sessions a command starts: `claude ... --bg` (a part), counted per segment."""
    n = 0
    for seg in segments(command):
        w = strip_prefix(seg)
        if w and w[0].rsplit("/", 1)[-1] == "claude" and "--bg" in w[1:]:
            n += 1
    return n


def _device_module(root: str):
    import importlib.util
    for rel in (".kit/device.py", "scripts/device.py"):
        path = os.path.join(root or ".", rel)
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("kit_device", path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                return mod
            except Exception:
                return None
    return None


def capacity_refusal(command: str, root: str, survey=None) -> str | None:
    """CL15: refuse a launch that would run more sessions than this device holds (never blocks without the module)."""
    n = session_launches(command)
    if not n:
        return None
    d = _device_module(root)
    if d is None:
        return None
    try:
        s = survey if survey is not None else d.survey()
        ok, why = d.gate(s, extra=n)
    except Exception:
        return None
    return None if ok else why


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print(json.dumps({"decision": "allow"}))
        return 0
    claude = is_claude_payload(data)
    g = config(data).get("kit", {}).get("guard", {})
    never_add = g.get("never_add", [])
    allow_add = g.get("allow_add", [])
    tc = data.get("toolCall") or {}
    args = tc.get("args") or {}
    command = args.get("CommandLine") or args.get("command") or (data.get("tool_input") or {}).get("command") or ""
    reason = check(command, never_add, allow_add, cwd=project_dir(data))
    full = capacity_refusal(command, project_dir(data)) if not reason else None
    if full:
        msg = ("NOT LAUNCHED: %s.\n\nThe kit's guard keeps this device under its session cap (`python3 -I .kit/device.py`): "
               "past it the computer runs out of memory and every session crashes. Leave the step planned (do not mark it "
               "in progress) and launch it when a part ends." % full)
        if claude:
            print(msg, file=sys.stderr)
            return 2
        print(json.dumps({"decision": "deny", "reason": msg}, ensure_ascii=False))
        return 0
    if not reason:
        if not claude:
            print(json.dumps({"decision": "allow"}))
        return 0
    hook_path = ".claude/hooks/kit/kit_guard.py" if claude else ".agents/hooks/kit/kit_guard.py"
    msg = (
        f"REFUSED: {reason}.\n\n"
        f"The kit's guard ({hook_path}): work lost to a deletion cannot be recovered. The ways out:\n"
        "  - a tracked file or folder: git rm <path>, in a commit of its own;\n"
        "  - a commit to undo: git revert <commit> (history stays; nothing is forced);\n"
        "  - scratch files: keep them under /tmp, where rm -r is allowed.\n"
        "If the owner explicitly asked for this exact command, say so and ask them to run it."
    )
    if claude:
        print(msg, file=sys.stderr)
        return 2
    print(json.dumps({"decision": "deny", "reason": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
