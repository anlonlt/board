#!/usr/bin/env python3
"""A project's hooks at user level, for Claude Code web sessions that hold several repos (CL39).

A web session started on an environment with two repos runs from their parent folder: no project's
.claude/settings.json is read there, so none of the project's hooks run (no usage row, no message cap, no guard).
This script copies every command hook of the project's .claude/settings.json into the user's
~/.claude/settings.json, each wrapped so that it:
  - runs only when the session's own project has no settings of its own (the parent folder of a multi-repo
    session); inside a repo that has them (a one-repo session) the project's own copy runs and this one is skipped;
  - runs with CLAUDE_PROJECT_DIR set to this repo, from this repo's folder, the hook's input on its stdin.

    python3 -I .kit/user_hooks.py install [--settings ~/.claude/settings.json]   (idempotent: 0 changed on a rerun)
    python3 -I .kit/user_hooks.py remove  [--settings ...]
    python3 -I .kit/user_hooks.py show    [--settings ...]
    python3 -I .kit/user_hooks.py run --repo <abs repo> -- <the hook's command>      (what each user-level line runs)

The environment's setup script runs `install` each time a container is built (kit.py web-env prints that line).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)          # .kit/user_hooks.py -> the repo
MARK = "user_hooks.py run --repo "


def user_settings(path=None):
    return os.path.abspath(os.path.expanduser(path or os.path.join("~", ".claude", "settings.json")))


def _load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def project_hooks(repo=REPO):
    """[(event, matcher, hook dict)] for every command hook of the repo's .claude/settings.json, in file order."""
    s = _load(os.path.join(repo, ".claude", "settings.json"))
    out = []
    for ev, groups in (s.get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                if h.get("type") == "command" and h.get("command"):
                    out.append((ev, g.get("matcher") or None, h))
    return out


def wrapped(repo, cmd):
    me = os.path.join(repo, ".kit", "user_hooks.py")
    # the hook's command as ONE quoted word: the shell that runs this line must not expand its $CLAUDE_PROJECT_DIR
    # (the session's folder); `run` hands it to sh with CLAUDE_PROJECT_DIR set to the repo
    return "python3 -I %s run --repo %s -- %s" % (shlex.quote(me), shlex.quote(repo), shlex.quote(cmd))


def _ours(cmd, repo):
    return isinstance(cmd, str) and (MARK + shlex.quote(repo) + " ") in cmd


def strip(s, repo):
    """Remove this repo's wrapped lines (and the groups and events they leave empty); returns how many."""
    n = 0
    hooks = s.get("hooks") or {}
    for ev in list(hooks):
        keep = []
        for g in hooks[ev] or []:
            hs = [h for h in g.get("hooks") or [] if not _ours(h.get("command"), repo)]
            n += len(g.get("hooks") or []) - len(hs)
            if hs:
                keep.append(dict(g, hooks=hs))
        if keep:
            hooks[ev] = keep
        else:
            del hooks[ev]
    if "hooks" in s and not s["hooks"]:
        del s["hooks"]
    return n


def install(settings=None, repo=REPO, dry=False):
    """Returns (hooks installed, changed?)."""
    p = user_settings(settings)
    s = _load(p)
    before = json.dumps(s, sort_keys=True)
    strip(s, repo)
    hs = project_hooks(repo)
    hooks = s.setdefault("hooks", {})
    for ev, matcher, h in hs:
        line = dict(h, command=wrapped(repo, h["command"]))
        g = {"hooks": [line]}
        if matcher:
            g = {"matcher": matcher, **g}
        hooks.setdefault(ev, []).append(g)
    if not hooks:
        del s["hooks"]
    changed = json.dumps(s, sort_keys=True) != before
    if changed and not dry:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return len(hs), changed


def remove(settings=None, repo=REPO):
    p = user_settings(settings)
    s = _load(p)
    n = strip(s, repo)
    if n:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return n


def count(settings=None, repo=REPO):
    s = _load(user_settings(settings))
    return sum(1 for gs in (s.get("hooks") or {}).values() for g in gs or [] for h in g.get("hooks") or []
               if _ours(h.get("command"), repo))


def has_own_settings(d):
    """The folder is a project with its own settings (the user's home holds the USER settings: not a project's)."""
    if not d or os.path.realpath(d) == os.path.realpath(os.path.expanduser("~")):
        return False
    return os.path.isfile(os.path.join(d, ".claude", "settings.json"))


def run(repo, cmd):
    """The wrapper each user-level line calls: skip where the session's project has settings of its own."""
    data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    proj = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    if not proj:
        try:
            proj = str(json.loads(data.decode("utf-8") or "{}").get("cwd") or "")
        except ValueError:
            proj = ""
    proj = proj or os.getcwd()
    if has_own_settings(proj):
        return 0                       # a one-repo session: the project's own copy of this hook runs
    env = dict(os.environ, CLAUDE_PROJECT_DIR=repo)
    r = subprocess.run(["sh", "-c", cmd], input=data, cwd=repo, env=env, stdout=sys.stdout.buffer,
                       stderr=sys.stderr.buffer)
    return r.returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["run"]:
        # run --repo R -- CMD...: everything after -- is the hook's command, as the settings line wrote it
        rest = argv[1:]
        if "--" not in rest or rest[:1] != ["--repo"]:
            print("user_hooks run: --repo <repo> -- <command>", file=sys.stderr)
            return 0                   # never block a session over a malformed line
        i = rest.index("--")
        return run(rest[1], " ".join(rest[i + 1:]))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["install", "remove", "show"])
    ap.add_argument("--settings", help="the user settings file (default ~/.claude/settings.json)")
    ap.add_argument("--repo", default=REPO, help="the repo whose hooks to install (default: this script's repo)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    p = user_settings(a.settings)
    if a.action == "install":
        n, changed = install(a.settings, repo, dry=a.dry_run)
        print("user_hooks: %d hooks of %s in %s, wrapped (run only where the session's project has no settings of "
              "its own; CLAUDE_PROJECT_DIR=%s); %s." % (n, os.path.basename(repo), p, repo,
                                                          "written" if changed and not a.dry_run else
                                                          "would change" if changed else "0 changed (already there)"))
    elif a.action == "remove":
        print("user_hooks: %d lines of %s removed from %s." % (remove(a.settings, repo), os.path.basename(repo), p))
    else:
        print("user_hooks: %d lines of %s in %s (the project has %d command hooks)." % (
            count(a.settings, repo), os.path.basename(repo), p, len(project_hooks(repo))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
