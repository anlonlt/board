#!/usr/bin/env python3
"""PostToolUse (what changes files) and Stop, every runtime: the work in progress is saved to git every few minutes
(`.kit/landing.py save`, kit.wip_minutes, default 5), detached, so a session cut in the middle of a turn (a usage limit, a
container gone) loses at most that, public repos included (CL55). It never waits and blocks nothing; it says something
only when a save was refused (a secret-looking value) or origin did not take it, once."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, is_claude_payload, project_dir  # noqa: E402


def refused(root):
    """The last save's refusal, once (landing.py writes .kit/run/wip-status.json): '' when there is none to tell."""
    path = os.path.join(root, ".kit", "run", "wip-status.json")
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
        if st.get("told") or not st.get("rc"):
            return ""
        st["told"] = True
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        return "Work in progress NOT on GitHub: " + str(st.get("text") or "the last save failed")
    except (OSError, ValueError, AttributeError):
        return ""


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
    # CL55: a part's save is CL44's checkpoint on its step's branch (landing.py save decides, on the same 5-minute
    # clock), so the checkpoint has no clock of its own any more. A save that was refused is told here, once.
    told = refused(root)
    if told:
        if is_claude_payload(data):
            ev = str(data.get("hook_event_name") or "PostToolUse")
            print(json.dumps({"systemMessage": told} if ev == "Stop" else
                             {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": told}}))
            return 0
        print(told, file=sys.stderr)
    if not is_claude_payload(data) and data.get("hookEventName"):
        print(json.dumps({"decision": "allow"} if "Stop" in str(data.get("hookEventName")) else {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
