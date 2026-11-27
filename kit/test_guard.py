#!/usr/bin/env python3
"""CL48: a test run never writes to a real board, its git copy (board-mirror) or a remote.

On 2026-11-20 the made-up project of the install tests (Tide Clock, branch test-install of the kit's own repository) wrote
its `_orchestrator` and `KIT-installed` posts to the kit's real board-mirror (commit 738a5f3): the test project was a
branch of the real clone, so its `kit sync` pushed to the real origin's board-mirror.

A test run is `KIT_TEST` set (scripts/run_all_tests.py sets it for every suite; set it by hand for a live install test).
In a test run every write the kit makes to a board, a board's git copy or a remote is refused unless it lands under a
temp folder, and the refusal names the path (or the URL) it refused:

  - a board server opens its data folder (BoardStore), the folder must be under a temp folder;
  - a board client writes over HTTP, the server must say its data is under a temp folder (`/_api/health` "temp");
  - a git commit, merge or push, the repository must be under a temp folder, and a push's remote must be a local
    path under a temp folder (never a URL: github.com is the real board's remote).

Outside a test run nothing here does anything.

    python3 -I .kit/test_guard.py check <path>       exit 1 and the refusal when a test run may not write there

A script that writes (kit.py, board_server.py, board_cli.py, end_session.py, web_takeover.py) loads it in a test run
from next to itself (.kit/ once installed; kit/ in the kit's repository) and calls install(): from then on every git
command it starts is checked first. A test run whose script cannot find this file ($KIT_TEST_GUARD names it for a
test's hand-made copy of a few scripts; run_all_tests.py sets it) refuses to start.
"""
import os
import subprocess
import sys
import tempfile

# git subcommands that write a repository or its remote
GIT_WRITES = ("commit", "push", "merge", "pull", "rebase", "reset", "cherry-pick", "revert", "am", "tag")


class Refused(SystemExit):
    """A test run tried to write outside a temp folder (SystemExit: a command stops there, with the message)."""


def active(env=None):
    v = str((os.environ if env is None else env).get("KIT_TEST") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def temp_roots():
    roots = []
    for r in (tempfile.gettempdir(), os.environ.get("TMPDIR") or "", "/tmp", "/var/tmp"):
        if r:
            rr = os.path.realpath(r)
            if rr not in roots and rr != os.sep:
                roots.append(rr)
    return roots


def under_temp(path):
    if not path:
        return False
    p = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in temp_roots())


def refusal(what, where):
    return "KIT_TEST: REFUSED: a test run writes %s outside a temp folder: %s" % (what, where)


def check_path(path, what="to a board"):
    """Raises Refused when this is a test run and path is not under a temp folder."""
    if active() and not under_temp(path):
        raise Refused(refusal(what, os.path.realpath(os.path.abspath(str(path or "."))) if path else "(no path)"))


def _local_path(url, repo):
    """The local folder a git remote URL names, or None when it is a network URL (https, ssh, host:path)."""
    u = str(url or "").strip()
    if u.startswith("file://"):
        return u[len("file://"):]
    if "://" in u:
        return None
    if ":" in u.split("/", 1)[0] and not (len(u) > 1 and u[1] == ":"):   # scp-like git@host:owner/repo
        return None
    return u if os.path.isabs(u) else os.path.join(repo, u)


def check_remote(repo, remote="origin"):
    """Raises Refused when this is a test run and the push target is not a local folder under a temp folder."""
    if not active():
        return
    url = remote
    if remote and "/" not in remote and ":" not in remote and not remote.startswith("."):
        try:
            r = subprocess.run(["git", "-C", repo, "remote", "get-url", "--push", remote], capture_output=True, text=True,
                               timeout=20)
            url = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            url = ""
        if not url:
            return      # no such remote: git itself fails, nothing is written
    local = _local_path(url, repo)
    if local is None or not under_temp(local):
        raise Refused(refusal("to a git remote", url))


def check_git(repo, args):
    """Before `git -C repo <args>`: refuses a write (commit, merge, push...) in a test run outside a temp folder."""
    if not active():
        return
    a = [str(x) for x in args]
    i = 0
    while i < len(a) and a[i].startswith("-"):       # git -c k=v / -C dir before the subcommand
        if a[i] in ("-c", "-C") and i + 1 < len(a):
            if a[i] == "-C":
                repo = os.path.join(repo or ".", a[i + 1])
            i += 2
        else:
            i += 1
    if i >= len(a) or a[i] not in GIT_WRITES:
        return
    sub = a[i]
    check_path(repo or os.getcwd(), "a git %s in" % sub)
    if sub == "push":
        rest = [x for x in a[i + 1:] if not x.startswith("-")]
        check_remote(repo or os.getcwd(), rest[0] if rest else "origin")


_BOARDS_OK = set()


def check_board(base_url, method="POST"):
    """Before an HTTP write to a board server: in a test run, the server must say its data is under a temp folder
    (`/_api/health` "temp": a board server started in a test run says so; the real board never does)."""
    if not active() or str(method or "GET").upper() in ("GET", "HEAD", "OPTIONS"):
        return
    base = str(base_url or "").rstrip("/")
    if base in _BOARDS_OK:
        return
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(base + "/_api/health", timeout=10) as r:
            temp = json.loads(r.read().decode("utf-8")).get("temp") is True
    except Exception:
        temp = False
    if not temp:
        raise Refused(refusal("to a board", base + " (its data is not under a temp folder: the real board?)"))
    _BOARDS_OK.add(base)


_REAL_POPEN = subprocess.Popen


class _GuardedPopen(_REAL_POPEN):
    """subprocess.Popen that runs check_git first on every `git ...` it starts (every wrapper of a script goes through it)."""

    def __init__(self, args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and os.path.basename(str(args[0])) in ("git", "git.exe"):
            check_git(kw.get("cwd") or os.getcwd(), list(args[1:]))
        super().__init__(args, *a, **kw)


def install():
    """In a test run, every git command this process starts is checked (check_git) before it runs. No-op otherwise."""
    if active() and subprocess.Popen is _REAL_POPEN:
        subprocess.Popen = _GuardedPopen


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "check":
        try:
            check_path(argv[1], "to")
        except Refused as e:
            print(e.code)
            return 1
        print("ok: %s" % ("a test run may write there" if active() else "not a test run (KIT_TEST unset)"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
