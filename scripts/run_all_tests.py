#!/usr/bin/env python3
"""Unified test runner for the board kit repository.

Runs every regression suite in one command, prints a clear pass/fail summary,
and exits non-zero if any suite fails.
"""
import os
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
]

SESSION_ENV = ("KIT_ROLE", "KIT_BOARD_TOKEN", "KIT_BOARD_URL", "KIT_BREAKS_COPY", "CLAUDE_PROJECT_DIR")


def main():
    results = []
    t0 = time.monotonic()
    # The suites start without the calling session's identity: a part, clerk or orchestrator that runs them would
    # otherwise hand its KIT_ROLE (or board token and URL) to every hook and kit command the tests launch.
    env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV}
    env["KIT_NO_FUNNEL"] = "1"
    for name, cmd in SUITES:
        print("=== %s ===" % name, flush=True)
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
