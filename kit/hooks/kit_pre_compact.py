#!/usr/bin/env python3
"""PreCompact: writes .story/before-compaction.md (gitignored), the instant before a compaction: branch, last commits,
changed files. A session that lost detail in the summary reads it back. Origin: anlonlt/harmonize pre-compact.sh."""
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402


def git(*a):
    try:
        return subprocess.run(["git", "-C", project_dir()] + list(a), capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    d = os.path.join(project_dir(), ".story")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "before-compaction.md"), "w", encoding="utf-8") as fh:
        fh.write("# Before compaction, %s\n\nbranch: %s\n\n## last commits\n%s\n\n## changed\n%s\n" % (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), git("branch", "--show-current"),
            git("log", "--oneline", "-8"), git("status", "--short") or "(nothing)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
