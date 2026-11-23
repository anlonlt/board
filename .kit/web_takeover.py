#!/usr/bin/env python3
"""Web takeover (CL18): a Claude Code web session (or any fresh clone) continues the board's work where it stopped.

    python3 -I .kit/web_takeover.py            # board up from git + what to continue (run it first)
    python3 -I .kit/web_takeover.py save       # the board back into git (board-mirror), pushed; run before you stop
    python3 -I .kit/web_takeover.py --stop-hook   # the Stop hook: `save`, only in a web session, quiet when nothing changed

`start` (the default): fetches every branch; starts a board server on 127.0.0.1 seeded from `origin/board-mirror` (the
board's git copy) unless one answers already; reconciles it with git (`kit.py reconcile`: the newer copy of each post); prints `kit.py resume` and, for each step in progress whose session is
gone, the branch that holds its saved work and how to continue it.

Nothing here needs the computer that crashed: the board, the prompts (docs/prompts/) and each part's work (its branch)
are all in git. What lives only on that computer (an uncommitted edit made after the last save) is not here.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, ".kit")
RUN = os.path.join(KIT, "run")


def sh(args, timeout=120, cwd=ROOT):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def cfg():
    try:
        with open(os.path.join(KIT, "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def port():
    m = re.search(r":(\d+)", cfg().get("board_url") or "")
    return int(os.environ.get("KIT_BOARD_PORT") or (m.group(1) if m else 8765))


def healthy(p):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/_api/health" % p, timeout=3) as r:
            return json.loads(r.read().decode()).get("ok")
    except Exception:
        return False


def mirror_branch():
    return (cfg().get("mirror") or {}).get("branch") or "board-mirror"


def mirror_path():
    p = (cfg().get("mirror") or {}).get("path") or "../board-mirror"
    return os.path.normpath(os.path.join(ROOT, p))


def is_web():
    return any(str(os.environ.get(v) or "").lower() in ("1", "true", "yes")
               for v in ("CLAUDE_CODE_REMOTE", "KIT_REMOTE", "AGY_REMOTE"))


def git_identity():
    """Commits fail without one (the cause of a two-day-stale mirror on 2026-11-22): borrow the last author's."""
    if sh(["git", "config", "user.email"])[1]:
        return
    rc, who = sh(["git", "log", "-1", "--format=%an\t%ae"])
    if rc == 0 and "\t" in who:
        n, e = who.split("\t", 1)
        sh(["git", "config", "user.name", n])
        sh(["git", "config", "user.email", e])


def start_board():
    p = port()
    if healthy(p):
        return "Board: already running on port %d." % p
    os.makedirs(os.path.join(KIT, "board_data"), exist_ok=True)
    os.makedirs(RUN, exist_ok=True)
    tok_path = os.path.join(KIT, "board_data", "token")
    if not os.path.exists(tok_path):
        with open(tok_path, "w") as fh:
            fh.write(secrets.token_hex(16))
    with open(tok_path) as fh:
        tok = fh.read().strip()
    log = open(os.path.join(RUN, "web-board.log"), "a")
    seed = [] if os.path.exists(os.path.join(KIT, "board_data", "board.db")) else ["--seed-from-mirror"]
    subprocess.Popen([sys.executable, "-I", os.path.join(KIT, "board_server.py"), "--host", "127.0.0.1", "--port", str(p),
                      "--root", ROOT, "--token", tok, "--auto-sync-interval", "15", "--auto-sync-push"] + seed,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=ROOT)
    for _ in range(60):
        if healthy(p):
            return "Board: started on 127.0.0.1:%d%s (log .kit/run/web-board.log)." % (
                p, ", seeded from origin/%s" % mirror_branch() if seed else "")
        time.sleep(0.5)
    return "Board: DID NOT START (see .kit/run/web-board.log)."


def ensure_mirror_worktree():
    if os.path.exists(os.path.join(mirror_path(), ".git")):
        return
    sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "init"])


def part_branches(remote_branches, part):
    """Branches that hold a part's work: worktree-cl8, cl8, wip-cl8, claude/...-cl8."""
    s = re.escape(part.lower())
    return [b for b in remote_branches if re.search(r"(^|[-/_])%s$" % s, b.lower())]


def continuation(resume_text, trunk="<trunk>"):
    rc, out = sh(["git", "branch", "-r", "--format=%(refname:short)"])
    remote = [b.split("/", 1)[1] for b in out.splitlines() if b.startswith("origin/") and "HEAD" not in b]
    lines = []
    for m in re.finditer(r"^- \[([~ ?!])\] (\S+) `([^`]+)` (.*)$", resume_text, re.M):
        box, part, pid, rest = m.groups()
        prompt = "docs/prompts/%s.md" % pid
        if not os.path.exists(os.path.join(ROOT, prompt)):
            continue  # no prompt: not a part's step (a card, a demo post)
        if re.search(r"· (complete|done)\b", rest):
            continue  # finished; it waits for the owner's Done, not for a session
        brs = part_branches(remote, part)
        crashed = "not running" in rest or "unknown" in rest
        if box == "~" or crashed:
            state = "in progress when its session died"
        elif brs:
            state = "planned, but has saved work"
        else:
            continue
        lines.append("- %s `%s` (%s): branch %s; prompt %s" % (
            part, pid, state, ", ".join("`%s`" % b for b in brs) or "(none: start from the trunk)", prompt))
    if not lines:
        return ""
    return ("\n## Steps to continue\n" + "\n".join(lines) +
            "\nContinue each as its own part (a subagent, or a new web session; no device cap in the cloud): \"You continue "
            "step <PART> (post <id>). git fetch origin && git checkout <branch>: it holds your earlier work (the last commit "
            "may be a WIP save). Your prompt is <prompt>: read it, compare with `git log origin/<trunk>..HEAD` and "
            "`git diff origin/<trunk>` to see what is done, finish it, push your branch (if a push to that branch is "
            "refused, push the branch you are allowed to and name it in your post), then `kit.py done`.\"\n").replace("<trunk>", trunk)


def cmd_start():
    git_identity()
    rc, out = sh(["git", "fetch", "--all", "--prune", "-q"], timeout=300)
    print("git fetch: %s" % ("ok" if rc == 0 else "FAILED: " + out[-300:]))
    rc, br = sh(["git", "branch", "--show-current"])
    print("Branch: %s" % br)
    print(start_board())
    ensure_mirror_worktree()
    # the board here against git: what the computer saved before it stopped (or crashed) comes in, newer copy wins
    rc, rec = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "reconcile"], timeout=240)
    print(rec.splitlines()[-1] if rec else "reconcile: no output")
    rc, res = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "resume"], timeout=180)
    print()
    print(res)
    print(continuation(res, trunk=br or "<trunk>"))
    print("Before you stop: python3 -I .kit/web_takeover.py save (the Stop hook does it too, in a web session).")
    return 0


def cmd_save(quiet=False):
    git_identity()
    if not healthy(port()) and not os.path.exists(os.path.join(KIT, "board_data", "board.db")):
        if not quiet:
            print("save: no board here (run `python3 -I .kit/web_takeover.py` first).")
        return 0
    ensure_mirror_worktree()
    rc, out = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "sync-db", "--commit"], timeout=180)
    if rc != 0:
        print("save: sync-db FAILED: %s" % out[-400:])
        return 1
    m = mirror_path()
    rc, ahead = sh(["git", "-C", m, "rev-list", "--count", "origin/%s..HEAD" % mirror_branch()])
    if rc == 0 and ahead.strip() == "0":
        if not quiet:
            print("save: the board is already in git (origin/%s)." % mirror_branch())
        return 0
    rc, out = sh(["git", "-C", m, "push", "origin", "HEAD:%s" % mirror_branch()], timeout=120)
    if rc != 0:
        # Both sides wrote (web + computer): merge; a conflicting hunk keeps this side (board/sync.json, the sync log,
        # always conflicts). A per-post merge by updated_at is CL16's.
        rc2, out2 = sh(["git", "-C", m, "pull", "--no-rebase", "--no-edit", "-X", "ours", "origin", mirror_branch()],
                       timeout=120)
        if rc2 != 0:
            sh(["git", "-C", m, "merge", "--abort"])
        if rc2 == 0:
            rc, out = sh(["git", "-C", m, "push", "origin", "HEAD:%s" % mirror_branch()], timeout=120)
    if rc != 0:
        print("save: PUSH OF %s REFUSED: %s\nThe board's changes are committed in %s but not on GitHub. If this web "
              "session may only push its own branch, tell the owner: the board cannot leave this session." % (
                  mirror_branch(), out[-400:], m))
        return 1
    print("save: the board is in git (origin/%s, %s commit%s pushed)." % (mirror_branch(), ahead, "" if ahead == "1" else "s"))
    return 0


def main(argv):
    if "--stop-hook" in argv:
        if not is_web():
            return 0
        try:
            sys.stdin.read()
        except OSError:
            pass
        cmd_save(quiet=True)
        return 0
    if argv and argv[0] == "save":
        return cmd_save()
    return cmd_start()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
