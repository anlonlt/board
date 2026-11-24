#!/usr/bin/env python3
"""agy Stop: blocks a stop ONCE per turn while work is uncommitted or unpushed.

Returns agy's Stop JSON contract: {"decision": "continue", "reason": "..."} or {"decision": "allow"}.
Tracks the last blocked turn per conversationId in a temp marker file so the second stop passes.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import in_setup, is_claude_payload, project_dir  # noqa: E402


def git(root, *a):
    r = subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=15)
    return r.returncode, r.stdout.strip()


def turn_key(data):
    tp = str(data.get("transcriptPath") or data.get("transcript_path") or "")
    last_user = ""
    if tp and os.path.isfile(tp):
        try:
            with open(tp, encoding="utf-8") as fh:
                for n, line in enumerate(fh):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") == "USER_INPUT":
                        last_user = str(e.get("step_index", n))
                    elif e.get("type") == "user" and not e.get("isMeta"):
                        last_user = str(e.get("uuid") or n)
        except OSError:
            pass
    return last_user or str(data.get("lastUserInput") or data.get("prompt") or "")[:80] or "0"


def _allow(claude):
    if not claude:
        print(json.dumps({"decision": "allow"}))
    return 0


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
    root = project_dir(data)
    try:
        rc, _ = git(root, "rev-parse", "--is-inside-work-tree")
        if rc:
            return _allow(claude)
        _, dirty = git(root, "status", "--porcelain", "--untracked-files=no")
        rc, ahead = git(root, "rev-list", "--count", "@{u}..HEAD")
        no_upstream_unpushed = ""
        if rc != 0:
            rrc, remotes = git(root, "remote")
            if rrc == 0 and remotes:
                urc, ucount = git(root, "rev-list", "--count", "HEAD", "--not", "--remotes")
                if urc == 0 and ucount not in ("", "0"):
                    no_upstream_unpushed = ucount
    except (OSError, subprocess.SubprocessError):
        return _allow(claude)
    why = []
    if dirty:
        why.append("%d tracked files changed and not committed" % len(dirty.splitlines()))
    if rc == 0 and ahead not in ("", "0"):
        why.append("%s commits not pushed" % ahead)
    elif no_upstream_unpushed:
        why.append("%s commits on branch with no upstream (not pushed)" % no_upstream_unpushed)
    if not why:
        return _allow(claude)
    reason = ("Before stopping: %s. Commit by name, then `python3 -I .kit/end_session.py` (it pushes, and merges onto the "
              "trunk when the owner allowed it), or say why not." % "; ".join(why))
    if claude and "conversationId" not in data and "session_id" not in data:
        print(reason, file=sys.stderr)
        return 2
    cid = str(data.get("conversationId") or data.get("session_id") or "x")
    key = turn_key(data)
    mark = os.path.join(tempfile.gettempdir(), "kit-stop-%s" % cid)
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
