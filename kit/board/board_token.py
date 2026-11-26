#!/usr/bin/env python3
"""One board token per machine (CL11): every project on this computer serves the board on the same origin
(127.0.0.1 / 0.0.0.0:8765), so a phone or browser that saved the token once must keep matching when `board` is
pointed at another project or another CLI.

    python3 -I .kit/board_token.py ensure [--root DIR]   # the project's token, from the machine token if it has none

Rules:
  - a project that already has `.kit/board_data/token` (non-empty) keeps it, unchanged;
  - otherwise it gets the machine token, `$XDG_CONFIG_HOME/board-kit/board_token` (default ~/.config/board-kit/),
    created once (mode 600) when absent;
  - the project copy is written mode 600; nothing here prints or logs a token.

The server's auth is unchanged: it still reads the token it is given (`--token` / KIT_BOARD_TOKEN), and the scripts
still read `.kit/board_data/token`; only where a NEW project's token comes from changes.
"""
import argparse
import os
import secrets
import sys


def machine_dir(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "board-kit")


def machine_token_path(env=None):
    return os.path.join(machine_dir(env), "board_token")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _write_600(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def machine_token(env=None):
    """The machine token, created once when absent. Two projects started at the same time agree: the first file wins."""
    path = machine_token_path(env)
    tok = _read(path)
    if tok:
        return tok
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.chmod(os.path.dirname(path), 0o700)
    except OSError:
        pass
    new = secrets.token_hex(16)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # no race: only one creator
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new + "\n")
        return new
    except FileExistsError:
        return _read(path) or new


def project_token_path(root):
    return os.path.join(root, ".kit", "board_data", "token")


def _main_worktree_root(root):
    """If `root` is a linked git worktree, return its main checkout root; else None."""
    dot_git = os.path.join(root, ".git")
    if not os.path.isfile(dot_git):
        return None
    raw = _read(dot_git)
    if not raw.startswith("gitdir:"):
        return None
    gitdir = raw.split(":", 1)[1].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(root, gitdir))
    cdir = _read(os.path.join(gitdir, "commondir"))
    if cdir:
        common = cdir if os.path.isabs(cdir) else os.path.normpath(os.path.join(gitdir, cdir))
    elif os.path.basename(os.path.dirname(gitdir)) == "worktrees":
        common = os.path.dirname(os.path.dirname(gitdir))
    else:
        return None
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return None


def read_token(root, env=None):
    """Read the board token for `root` without ever creating a file:
    KIT_BOARD_TOKEN -> <root>/.kit/board_data/token -> main worktree's token -> machine token."""
    env = os.environ if env is None else env
    tok = (env.get("KIT_BOARD_TOKEN") or "").strip()
    if tok:
        return tok
    if root:
        tok = _read(project_token_path(root))
        if tok:
            return tok
        main_root = _main_worktree_root(root)
        if main_root and os.path.abspath(main_root) != os.path.abspath(root):
            tok = _read(project_token_path(main_root))
            if tok:
                return tok
    tok = _read(machine_token_path(env))
    return tok or None


def ensure(root, env=None):
    """The project's token path, filled from the machine token when the project has none. Returns (path, source)
    where source is "project" (kept) or "machine" (copied)."""
    path = project_token_path(root)
    if _read(path):
        return path, "project"
    _write_600(path, machine_token(env))
    return path, "machine"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="give the project a token (its own, else the machine's); prints only where it came from")
    e.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    e.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "ensure":
        path, src = ensure(a.root)
        if not a.quiet:
            print("board token: %s (%s)" % ("kept the project's own" if src == "project" else "the machine's", os.path.relpath(path, a.root)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
