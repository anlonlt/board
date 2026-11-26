#!/usr/bin/env python3
"""Hand a Claude Code CLI session over to a fresh one: a context RESET, not a compaction.

    python3 -I .kit/handover.py --role orchestrator|clerk [--dry-run] [--name NAME] [--slug SLUG]

The successor starts with an empty context and only its saved prompt; everything it needs is on the board and in
the memory files (<memory_dir>/state/orchestrator.md), which the caller must have written, committed and pushed first.
It keeps the same session NAME (`<slug>-orchestrator` / `<slug>-clerk`), so the clerk and the parts, which reach it
by name with SendMessage, need no update.

- A foreground session (the orchestrator `./.kit/up` opened in your terminal): writes `.kit/handover.flag` with the
  successor's prompt, then ends this Claude Code process (SIGTERM to $CLAUDE_PID). `up` sees the flag and opens the
  successor in the same terminal at once.
- A background session (`claude --bg`, the clerk): a detached helper stops and removes this session, then starts the
  successor with `claude --bg -n <same name>` (same model, same permission mode).
"""
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = os.path.join(ROOT, ".kit", "handover.flag")
LOG = os.path.join(ROOT, ".kit", "handover.log")


def config():
    try:
        with open(os.path.join(ROOT, ".kit", "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def slug_of(c):
    return re.sub(r"[^a-z0-9]+", "-", str(c.get("project") or "project").lower()).strip("-") or "project"


def successor_prompt(role, gen, cli="claude"):
    if role == "orchestrator":
        # one prompt for every orchestrator: resume says whether this one is the first here, or takes over, and prints
        # that start; a summary here would be a second copy of it
        return ("You are orchestrator %d of this repository (%s). Your prompt is one command: `python3 -I .kit/kit.py "
                "resume --role orchestrator`: run it and do what it says." % (
                    gen, ".kit/claude-cli/orchestrator.md" if cli == "claude" else ".kit/orchestrator.md"))
    if cli != "claude":
        return ("Your prompt is .kit/clerk.md: read it and follow it. You are clerk %d, replacing the previous clerk "
                "after its context hand-over." % gen)
    return ("Your prompt is .kit/claude-cli/clerk.md: read it and follow it. You are clerk %d, replacing the previous "
            "clerk after its context hand-over." % gen)


def find_ancestor_pid(comm_name):
    """Walk /proc/<pid>/stat up to PID 1 to find the enclosing process named `comm_name` (e.g. 'agy')."""
    if not os.path.exists("/proc/self/stat"):
        return 0
    pid = os.getppid()
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            with open("/proc/%d/comm" % pid, encoding="utf-8") as fh:
                if fh.read().strip() == comm_name:
                    return pid
            with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
                st = fh.read()
            pid = int(st[st.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return 0


def is_web():
    return str(os.environ.get("CLAUDE_CODE_REMOTE") or "").lower() in ("1", "true", "yes")


def detect_cli(explicit, k):
    """agy, claude, or "web" (CL26: a Claude Code web/cloud session, where a successor is a create_session call)."""
    if explicit and explicit != "auto":
        return explicit
    if is_web():
        return "web"
    env_cli = os.environ.get("KIT_CLI", "").strip().lower()
    if env_cli in ("agy", "claude"):
        return env_cli
    if os.environ.get("CLAUDE_PID") or os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude"
    if find_ancestor_pid("agy"):
        return "agy"
    cfg_cli = str(k.get("cli") or "").strip().lower()
    return cfg_cli if cfg_cli in ("agy", "claude") else "claude"


def this_session():
    """(kind, short id) of the Claude Code session running us, from `claude agents --json`."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    try:
        out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30).stdout
        for s in json.loads(out or "[]"):
            if sid and str(s.get("sessionId") or "").startswith(sid):
                return str(s.get("kind") or ""), str(s.get("id") or sid[:8])
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ("interactive" if os.environ.get("CLAUDE_PID") else ""), sid[:8]


def next_gen(role):
    path = os.path.join(ROOT, ".kit", "handover-%s.gen" % role)
    try:
        with open(path, encoding="utf-8") as fh:
            gen = int(fh.read().strip() or 1) + 1
    except (OSError, ValueError):
        gen = 2
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(gen))
    except OSError:
        pass
    return gen


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--role", choices=["orchestrator", "clerk"], required=True)
    ap.add_argument("--cli", choices=["auto", "claude", "agy"], default="auto")
    ap.add_argument("--name", help="session name (default <slug>-<role>)")
    ap.add_argument("--slug")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    a = ap.parse_args()

    c = config()
    k = c.get("kit") or {}
    cli = detect_cli(a.cli, k)
    slug = a.slug or slug_of(c)
    name = a.name or "%s-%s" % (slug, a.role)
    mode = (os.environ.get("KIT_CLAUDE_PERMISSION_MODE") or k.get("claude_permission_mode") or "best").rstrip("!")
    if mode == "best":                     # kit.py permission_mode: bypass once the owner accepted it himself, else auto
        try:
            r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit.py"), "permission-mode"],
                               capture_output=True, text=True, timeout=30)
            mode = r.stdout.strip() or "auto"
        except (OSError, subprocess.SubprocessError):
            mode = "auto"
    model = ((k.get("claude_cli_roles") or {}).get(a.role) or {}).get("model") or ("haiku" if a.role == "clerk" else "inherit")
    gen = 2 if a.dry_run else next_gen(a.role)
    prompt = successor_prompt(a.role, gen, cli=cli)

    if cli == "web":
        # CL26: a script cannot start a cloud session, and killing CLAUDE_PID would end this one with no successor.
        call = {"title": "%s %d" % (name, gen), "prompt": prompt,
                "tags": ["orchestrator" if a.role == "orchestrator" else "board-clerk"]}   # CL30: the guard reads them
        we = k.get("web_env") or {}
        if we.get("on") and we.get("environment_id"):
            # CL39: the environment holding every repo (its setup script installs the hooks at user level): the
            # successor has them all from its first turn and never needs add_repo
            call["environment_id"] = we["environment_id"]
        plan = {"how": "web", "cli": "web", "create_session": call}
        if a.dry_run:
            print(json.dumps(plan, indent=1))
            return 0
        print("handover: %s %d NOT STARTED by the kit (a web session cannot start one): call the claude-code-remote "
              "`create_session` tool with %s, tell the sessions that reach this one its new id (skill "
              "orchestrator-handoff), then end your turn; this session is not killed." % (
                  name, gen, json.dumps(call, ensure_ascii=False)))
        return 0
    if not shutil.which(cli) and not a.dry_run:
        print("handover: %s %d NOT STARTED: `%s` is not on PATH here (and this is not a Claude Code web session). "
              "Start the successor by hand with: %s" % (name, gen, cli, prompt), file=sys.stderr)
        return 1

    if cli == "agy":
        if a.role == "clerk":
            print("handover: in agy, clerk rotation is handled by the orchestrator (spawn a fresh Board Clerk subagent, "
                  "then kill the old one).")
            return 0
        pid = int(os.environ.get("AGY_PID") or 0) or find_ancestor_pid("agy")
        plan = {"how": "foreground", "cli": "agy", "flag": FLAG, "kill": pid or None, "name": name, "prompt": prompt}
        if a.dry_run:
            print(json.dumps(plan, indent=1))
            return 0
        with open(FLAG, "w", encoding="utf-8") as fh:
            json.dump({"role": a.role, "cli": "agy", "name": name, "gen": gen, "prompt": prompt}, fh)
        if not pid:
            print("handover: wrote %s; exit this agy session so `up` opens orchestrator %d." % (FLAG, gen))
            return 0
        print("handover: ending this agy session; `up` opens orchestrator %d in this terminal." % gen)
        sys.stdout.flush()
        os.kill(pid, signal.SIGTERM)
        return 0

    kind, sid = this_session()
    if kind != "background":
        plan = {"how": "foreground", "cli": "claude", "flag": FLAG, "kill": os.environ.get("CLAUDE_PID"), "name": name, "prompt": prompt}
        if a.dry_run:
            print(json.dumps(plan, indent=1))
            return 0
        pid = int(os.environ.get("CLAUDE_PID") or 0)
        if not pid:
            print("handover: no CLAUDE_PID; is this a Claude Code session?", file=sys.stderr)
            return 1
        with open(FLAG, "w", encoding="utf-8") as fh:
            json.dump({"role": a.role, "cli": "claude", "name": name, "gen": gen, "prompt": prompt}, fh)
        print("handover: ending this session; `up` opens %s (generation %d) in this terminal." % (name, gen))
        sys.stdout.flush()
        os.kill(pid, signal.SIGTERM)
        return 0

    launch = ["env", "KIT_ROLE=%s" % a.role] + (["CLAUDE_CODE_TASK_LIST_ID=%s-orchestrator" % os.path.basename(ROOT)]
                                                  if a.role == "orchestrator" else []) + ["claude", "--bg", "-n", name, "--permission-mode", mode]
    limit_k = int((k.get(a.role) or {}).get("context_limit_k", 380 if a.role == "orchestrator" else 80))
    # Claude Code's own compaction only as a safety net just past the reset point.
    launch += ["--autocompact", str(max(100, limit_k + (70 if a.role == "orchestrator" else 40)) * 1000)]
    if model and model != "inherit":
        launch += ["--model", model]
    launch.append(prompt)
    # Detached: survives this session being stopped. Stop + rm first so the name is free for the successor.
    script = "sleep 2; claude stop %s; claude rm %s; cd %s && %s" % (
        shlex.quote(sid), shlex.quote(sid), shlex.quote(ROOT), " ".join(shlex.quote(x) for x in launch))
    if a.dry_run:
        print(json.dumps({"how": "background", "session": sid, "script": script}, indent=1))
        return 0
    with open(LOG, "a", encoding="utf-8") as log:
        log.write("%s %s gen %d: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), name, gen, script))
        subprocess.Popen(["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, cwd=ROOT)
    print("handover: %s (generation %d) starts in a few seconds; this session stops now. End your turn." % (name, gen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
