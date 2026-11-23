#!/usr/bin/env python3
"""Unified test runner for the board kit repository.

Runs every regression suite in one command, prints a clear pass/fail summary,
and exits non-zero if any suite fails.
"""
import os
import shutil
import subprocess
import sys
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
]


def main():
    results = []
    t0 = time.monotonic()
    env = dict(os.environ)
    env["KIT_NO_FUNNEL"] = "1"
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
