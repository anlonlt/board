#!/usr/bin/env python3
"""PostToolUse (what changes files) and Stop, every runtime: the work in progress is saved to git every few minutes
(`.kit/landing.py save`, kit.wip_minutes, default 5), detached, so a session cut in the middle of a turn (a usage limit, a
container gone) loses at most that. It never waits, says nothing and blocks nothing."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, project_dir  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    root = project_dir(data)
    script = os.path.join(root, ".kit", "landing.py")
    k = config(data).get("kit") or {}
    if os.environ.get("KIT_WIP") != "0" and str(k.get("wip") or "auto") != "off" and os.path.isfile(script):
        stamp = os.path.join(root, ".kit", "run", "wip-last")
        stop = "Stop" in (str(data.get("hook_event_name") or ""), str(data.get("hookEventName") or ""))
        try:
            age = time.time() - os.path.getmtime(stamp)
        except OSError:
            age = None
        try:
            if age is None:                      # the first call starts the clock
                os.makedirs(os.path.dirname(stamp), exist_ok=True)
                open(stamp, "w").close()
            if stop or (age is not None and age >= 60 * float(k.get("wip_minutes") or 5)):
                os.utime(stamp, None)
                subprocess.Popen([sys.executable, "-I", script, "save", "--quiet"], cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, ValueError):
            pass
    if not is_claude_payload(data) and data.get("hookEventName"):
        print(json.dumps({"decision": "allow"} if "Stop" in str(data.get("hookEventName")) else {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
