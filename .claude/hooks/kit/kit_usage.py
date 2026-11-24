#!/usr/bin/env python3
"""Stop / SessionEnd (CL37): records this session's usage row with .kit/usage.py (no model call, no output to the
model, never blocks). The row: tokens, cost, turns, tool calls by name, subagents and their tokens, messages,
board reads/writes and bytes, context peak, wall time, model, the kit commit, role and part. It lands in
.kit/run/usage/ at once and on the ledger branch `kit-usage` at every Stop (a detached push) and at SessionEnd."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import is_claude_payload, project_dir  # noqa: E402


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    root = project_dir(data)
    script = os.path.join(root, ".kit", "usage.py")
    if os.path.isfile(script):
        try:
            subprocess.run([sys.executable, "-I", script, "--root", root, "hook"], input=raw, text=True,
                           capture_output=True, timeout=90, cwd=root)
        except (OSError, subprocess.SubprocessError):
            pass
    if not is_claude_payload(data) and data.get("hookEventName"):
        print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
