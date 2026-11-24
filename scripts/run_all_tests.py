#!/usr/bin/env python3
"""Unified test runner for the board kit repository.

Runs every regression suite in one command, prints a clear pass/fail summary,
and exits non-zero if any suite fails.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUITES = [
    ("unittest (tests/)", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ("kit/board/test_board_local.py", [sys.executable, "kit/board/test_board_local.py"]),
    ("kit/board/test_strings.py", [sys.executable, "kit/board/test_strings.py"]),
    ("kit/board/test_kit_cards.py", [sys.executable, "kit/board/test_kit_cards.py"]),
    ("test_install.py", [sys.executable, "test_install.py"]),
    ("test_install_bd11.py", [sys.executable, "test_install_bd11.py"]),
    # CL12: the page in a real browser at a phone and a desktop width: no text off screen or cut by its box, the badge
    # clears once seen, a train lists its done steps (prints SKIP without a browser; KIT_REQUIRE_BROWSER=1 makes it fail)
    ("kit/board/test_page_layout.js", [shutil.which("node") or "node", "kit/board/test_page_layout.js"]),
    # CL23: a thread nested deep opens without freezing the page (it was nested grids: seconds per layout)
    ("kit/board/test_page_speed.js", [shutil.which("node") or "node", "kit/board/test_page_speed.js"]),
    ("kit/board/test_page_ux.js", [shutil.which("node") or "node", "kit/board/test_page_ux.js"]),
    # CL36: a post body draws a mermaid block, an svg block and an inline picture, as the document viewer does
    ("kit/board/test_page_body.js", [shutil.which("node") or "node", "kit/board/test_page_body.js"]),
]


# Variables a kit session exports (its role, a board token or url). Inherited, they make the
# suites depend on who runs them (KIT_ROLE=clerk fails the hook tests), so every suite runs without them.
# CL26: so are the runtime markers the launchers read (a suite run from a web session must not launch as one).
SESSION_VARS = ("KIT_ROLE", "KIT_BOARD_TOKEN", "KIT_BOARD_URL", "KIT_BOARD_REPO", "KIT_BREAKS_COPY", "CLAUDE_PROJECT_DIR",
                "KIT_CLI", "CLAUDE_CODE_REMOTE", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "AGY_PID")


def bin_snapshot(path):
    """{name: (size, mtime_ns, link target)} of the files in path; {} when it does not exist."""
    out = {}
    try:
        names = os.listdir(path)
    except OSError:
        return out
    for n in names:
        f = os.path.join(path, n)
        try:
            st = os.lstat(f)
        except OSError:
            continue
        out[n] = (st.st_size, st.st_mtime_ns, os.readlink(f) if os.path.islink(f) else "")
    return out


def bin_changes(before, after):
    """Names added, removed or rewritten between two bin_snapshot results."""
    return sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))


def main():
    results = []
    t0 = time.monotonic()
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    env["KIT_NO_FUNNEL"] = "1"
    # Every launcher a test install writes goes to a temp dir; the real ~/.local/bin is watched.
    tmp_bin = tempfile.mkdtemp(prefix="kit-test-bin-")
    env["KIT_BIN_DIR"] = tmp_bin
    real_bin = os.path.expanduser("~/.local/bin")
    before = bin_snapshot(real_bin)
    for name, cmd in SUITES:
        print("=== %s ===" % name, flush=True)
        if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]):
            print("SKIP %s: %s is not installed (say so; not a pass)\n" % (name, cmd[0]), flush=True)
            continue
        st = time.monotonic()
        r = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
        dt = time.monotonic() - st
        ok = r.returncode == 0
        results.append((name, ok, r.returncode, dt))
        print("[%s] %s (%.2fs)\n" % ("PASS" if ok else "FAIL", name, dt), flush=True)

    changed = bin_changes(before, bin_snapshot(real_bin))
    results.append(("~/.local/bin untouched", not changed, 1 if changed else 0, 0.0))
    print("[%s] %d files written to %s by the suites%s (their launchers went to %s)\n" % (
        "PASS" if not changed else "FAIL", len(changed), real_bin,
        (": " + ", ".join(changed)) if changed else "", tmp_bin), flush=True)
    shutil.rmtree(tmp_bin, ignore_errors=True)

    total_dt = time.monotonic() - t0
    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = len(results) - passed
    print("=" * 60)
    print("SUMMARY: %d/%d suites passed, %d failed (%.2fs total)" % (passed, len(results), failed, total_dt))
    for name, ok, rc, dt in results:
        status = "PASS" if ok else ("FAIL (rc=%d)" % rc)
        print("  %-34s %s (%.2fs)" % (name, status, dt))
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
