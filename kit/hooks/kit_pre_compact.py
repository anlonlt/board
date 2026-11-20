#!/usr/bin/env python3
"""agy Stop (snapshot before compaction): writes .story/before-compaction.md (gitignored) with branch,
last commits, and changed files. agy has no PreCompact event, so running this on Stop keeps the
snapshot current before any /compact or auto-compaction. Always allows stop."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402


def git(root, *a):
    try:
        return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    root = project_dir(data)
    d = os.path.join(root, ".story")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "before-compaction.md"), "w", encoding="utf-8") as fh:
        fh.write("# Before compaction, %s\n\nbranch: %s\n\n## last commits\n%s\n\n## changed\n%s\n" % (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), git(root, "branch", "--show-current"),
            git(root, "log", "--oneline", "-8"), git(root, "status", "--short") or "(nothing)"))
    print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
