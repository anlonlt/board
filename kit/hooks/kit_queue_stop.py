#!/usr/bin/env python3
"""Stop, the orchestrator only (CL62): "start now" is enforced, not read.

The owner, 2026-11-27: "Why didn't you launch it when it told you? This needs a permanent fix that doesn't depend on
your stupidity." Web orchestrator 2's resume at 02:28 listed M2, M3, M4 as [start now]; it followed a stale hand-over
line ("M2 and M3 wait on M1", M1 had landed at 02:19) and launched them at 03:09 when the owner asked.

While `kit.py queue --gate` names a start-now step this orchestrator has neither launched nor held
(`kit.py hold <post> --why "..."`), the stop is refused ONCE per turn with the list and the exact launch commands. A
step the split on `_orchestrator` gives another orchestrator counts as held. Every other session stops freely; a gate
that fails (no board, a timeout) blocks nothing.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kit_config import in_setup, is_claude_payload, project_dir  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location("kq_" + name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _allow(claude):
    if not claude:
        print(json.dumps({"decision": "allow"}))
    return 0


def gate(root):
    """(refusal text or "", ok): the kit's own queue check, run in the project."""
    kit = os.path.join(root, ".kit", "kit.py")
    if not os.path.isfile(kit):
        return "", False
    env = dict(os.environ, KIT_STALE_OK="1")
    try:
        r = subprocess.run([sys.executable, "-I", kit, "queue", "--gate"], cwd=root, capture_output=True, text=True,
                           timeout=40, env=env)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if r.returncode == 1 and r.stdout.strip():
        return r.stdout.strip(), True
    return "", r.returncode == 0


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    claude = is_claude_payload(data)
    if data.get("stop_hook_active"):
        return _allow(claude)
    term = str(data.get("terminationReason") or "NO_TOOL_CALL")
    if term not in ("", "NO_TOOL_CALL") or in_setup(data):
        return _allow(claude)
    try:
        if not _load("kit_orchestrator_guard").is_orchestrator(data):
            return _allow(claude)
    except Exception:
        return _allow(claude)
    root = project_dir(data)
    reason, _ok = gate(root)
    if not reason:
        return _allow(claude)
    try:
        key = _load("kit_stop").turn_key(data)
    except Exception:
        key = "0"
    cid = str(data.get("conversationId") or data.get("session_id") or "x")
    mark = os.path.join(tempfile.gettempdir(), "kit-queue-stop-%s" % "".join(c if c.isalnum() else "_" for c in cid))
    try:
        seen = open(mark, encoding="utf-8").read()
    except OSError:
        seen = ""
    if seen == key:
        return _allow(claude)
    try:
        open(mark, "w", encoding="utf-8").write(key)
    except OSError:
        pass
    if claude:
        print(reason, file=sys.stderr)
        return 2
    print(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
