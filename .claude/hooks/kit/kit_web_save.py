#!/usr/bin/env python3
"""Claude Code Stop, web sessions only (CLAUDE_CODE_REMOTE=true): the board back into git (`.kit/web_takeover.py save`),
so a web session that ends or is recycled loses nothing of the board. Elsewhere: does nothing. Never blocks."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402


def main():
    try:
        import json
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    if str(os.environ.get("CLAUDE_CODE_REMOTE") or "").lower() not in ("1", "true", "yes"):
        return 0
    script = os.path.join(project_dir(data), ".kit", "web_takeover.py")
    if os.path.isfile(script):
        try:
            subprocess.run([sys.executable, "-I", script, "save"], capture_output=True, text=True, timeout=120,
                           cwd=project_dir(data))
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
