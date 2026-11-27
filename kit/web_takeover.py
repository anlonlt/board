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

def _test_guard():
    """CL48: in a test run (KIT_TEST set) a write to a board, a mirror or a remote outside a temp folder is refused
    (kit/test_guard.py, every git command checked first); None outside a test run."""
    if not os.environ.get("KIT_TEST"):
        return None
    if sys.modules.get("kit_test_guard"):
        return sys.modules["kit_test_guard"]
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    for f in [os.path.join(d, "test_guard.py") for d in (here, os.path.join(here, "..", "kit"), os.path.join(here, ".."))] + \
            [os.environ.get("KIT_TEST_GUARD") or ""]:     # a test's hand-made copy of a few scripts: the runner's
        if f and os.path.isfile(f):
            spec = importlib.util.spec_from_file_location("kit_test_guard", f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["kit_test_guard"] = mod
            spec.loader.exec_module(mod)
            mod.install()
            return mod
    raise SystemExit("KIT_TEST: REFUSED: test_guard.py is not next to %s: a test run writes nothing without it" % __file__)


_TEST_GUARD = _test_guard()


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


def owns_board():
    """(True, "") when this session may push this clone's board to its board-mirror, else (False, why) (CL32: an
    orchestrator's clone of anlonlt/board and a check's clone pushed seeded posts to its board-mirror on 11-23 22:04).
    A review or a check never pushes; a clone a launch from another project uses (KIT_ATTACHED_CLONE) never pushes;
    a clone that is not this session's project (CLAUDE_PROJECT_DIR names another) never pushes."""
    role = str(os.environ.get("KIT_ROLE") or "").strip().lower()
    if str(os.environ.get("KIT_NO_BOARD_PUSH") or "").lower() in ("1", "true", "yes"):
        return False, "KIT_NO_BOARD_PUSH is set"
    if role in ("review", "check", "consultant"):
        return False, "a %s never pushes a board" % role
    if os.environ.get("KIT_ATTACHED_CLONE"):
        return False, "this clone serves a launch from another project (%s)" % (os.environ.get("KIT_HOST_ROOT") or "?")
    art = (cfg().get("kit") or {}).get("artifact")
    if isinstance(art, dict) and str(art.get("prefix") or "").strip("/"):
        # O53: this board lives in another project's Artifact board (kit.artifact.prefix, e.g. the kit's repos/board/):
        # the Artifact is its record; a part's web session must not seed it from git nor push its board-mirror
        return False, "this board is attached to the Artifact board %s (%s)" % (art.get("url") or "?", art["prefix"])
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj and os.path.realpath(proj) != os.path.realpath(ROOT):
        return False, "this clone (%s) is not this session's project (%s)" % (ROOT, proj)
    return True, ""


def artifact_board():
    """The claude.ai Artifact this board lives in (kit.artifact.url, or a claude.ai board_url), '' for a git-only board."""
    c = cfg()
    a = (c.get("kit") or {}).get("artifact")
    for u in ((a or {}).get("url") if isinstance(a, dict) else "", os.environ.get("KIT_ARTIFACT_URL"), c.get("board_url"),
              (c.get("kit") or {}).get("artifact_url")):
        if u and "claude.ai/" in str(u):
            return str(u)
    return ""


def start_board():
    p = port()
    if healthy(p):
        return "Board: already running on port %d." % p
    os.makedirs(os.path.join(KIT, "board_data"), exist_ok=True)
    os.makedirs(RUN, exist_ok=True)
    tok_path = os.path.join(KIT, "board_data", "token")
    bt = os.path.join(KIT, "board_token.py")
    if os.path.exists(bt):   # one token per machine (CL11): the project's own, else the machine's
        sh([sys.executable, "-I", bt, "ensure", "--root", ROOT, "--quiet"])
    if not os.path.exists(tok_path):
        with open(tok_path, "w") as fh:
            fh.write(secrets.token_hex(16))
    with open(tok_path) as fh:
        tok = fh.read().strip()
    log = open(os.path.join(RUN, "web-board.log"), "a")
    seed = [] if os.path.exists(os.path.join(KIT, "board_data", "board.db")) else ["--seed-from-mirror"]
    own, why = owns_board()
    art = (cfg().get("kit") or {}).get("artifact")
    if os.environ.get("KIT_ATTACHED_CLONE") or (isinstance(art, dict) and str(art.get("prefix") or "").strip("/")):
        seed = []          # the posts it needs come from the Artifact (the launch hands it the post), never a git seed
    sync = ["--auto-sync-interval", "15", "--auto-sync-push"] if own else ["--auto-sync-interval", "0"]
    env = dict(os.environ)
    if not own:
        env.pop("KIT_AUTO_SYNC_PUSH", None)
    subprocess.Popen([sys.executable, "-I", os.path.join(KIT, "board_server.py"), "--host", "127.0.0.1", "--port", str(p),
                      "--root", ROOT, "--token", tok] + sync + seed,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd=ROOT, env=env)
    for _ in range(60):
        if healthy(p):
            return "Board: started on 127.0.0.1:%d%s%s (log .kit/run/web-board.log)." % (
                p, ", seeded from origin/%s" % mirror_branch() if seed else "",
                "" if own else ", no push to origin/%s (%s)" % (mirror_branch(), why))
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
            "may be a checkpoint). `python3 -I .kit/kit.py resume --role part --post <id>` starts you from the newest of that branch and its last save (wip/<branch>) and says which. Your prompt is <prompt>: read it, compare with `git log origin/<trunk>..HEAD` and "
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
    own, why = owns_board()
    if artifact_board():
        # CL45: an Artifact board is reconciled with git by `kit.py artifact reconcile` once its pull is made (the
        # ArtifactData calls are this session's): a git reconcile here first would put git's copies in before the
        # Artifact's, and the documents only git had would never reach the Artifact
        print("Board vs git: after the pull below, `python3 -I .kit/kit.py artifact reconcile` (the Artifact against "
              "origin/%s, both ways, newer wins, a both-sides change kept twice)." % mirror_branch())
        rc, pull = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "artifact", "pull"], timeout=60)
        print(pull)
    else:
        rc, rec = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "reconcile"] + ([] if own else ["--no-push"]),
                     timeout=240)
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
    own, why = owns_board()
    if not own:
        if not quiet:
            print("save: not pushed: %s. This session's board is its own project's." % why)
        return 0
    ensure_mirror_worktree()
    # CL16: one path for every save (kit.py mirror-sync): sync-db + commit, push, and when both sides wrote (web +
    # computer) a merge post by post (the newer updated_at wins, nothing lost), then push again; never forced.
    rc, out = sh([sys.executable, "-I", os.path.join(KIT, "kit.py"), "mirror-sync", "--final"], timeout=240)
    line = (out.strip().splitlines() or ["mirror-sync: no output"])[-1]
    if rc != 0 or "NOT DONE" in line:
        print("save: THE BOARD IS NOT ON GITHUB (origin/%s): %s\nIts changes are committed in %s; the next save or "
              "sync retries. If this web session may only push its own branch, tell the owner: the board cannot leave "
              "this session." % (mirror_branch(), line, mirror_path()))
        return 1
    if not quiet or "pushed" not in line:
        print("save: the board is in git (origin/%s): %s" % (mirror_branch(), line.split(": ", 1)[-1]))
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
