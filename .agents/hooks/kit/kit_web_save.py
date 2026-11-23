#!/usr/bin/env python3
"""Stop hook, remote/web sessions only (CLAUDE_CODE_REMOTE=true or KIT_REMOTE/AGY_REMOTE=1):
saves the board back into git (`.kit/web_takeover.py save`), so a remote session that ends or is recycled
loses nothing of the board. Elsewhere: does nothing. Never blocks."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import is_claude_payload, project_dir  # noqa: E402


def _is_remote():
    for var in ("CLAUDE_CODE_REMOTE", "KIT_REMOTE", "AGY_REMOTE"):
        if str(os.environ.get(var) or "").lower() in ("1", "true", "yes"):
            return True
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    claude = is_claude_payload(data)
    if _is_remote():
        script = os.path.join(project_dir(data), ".kit", "web_takeover.py")
        if os.path.isfile(script):
            try:
                subprocess.run([sys.executable, "-I", script, "save"], capture_output=True, text=True, timeout=120,
                               cwd=project_dir(data))
            except (OSError, subprocess.SubprocessError):
                pass
    if not claude and data.get("hookEventName"):
        print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
