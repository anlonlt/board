#!/usr/bin/env python3
"""Claude Code PostToolUse (Bash, create_session) and Stop, every session: what the kit's scripts wrote here reaches
the Artifact (CL30, Harmonize docs/KIT-SCRIPTS.md § Gaps 3 and 5).

With the board in an Artifact the scripts write a LOCAL copy; the live board gets a change only through
`kit.py artifact push`, the ArtifactData batch it prints, then `kit.py artifact sent <batch>`. Nothing said so after
a write, and a session could end with its changes here only (CL28: "How come you said you posted when you didn't?"):

  - PostToolUse Bash: a local board write (`board_cli.py set/update/str-replace/batch/reply/import`, `kit.py step`,
    `adopt`, `welcome`, `pair`) is followed by the push line;
  - PostToolUse create_session: a part started from `kit.py launch <post>` (its mark .kit/run/launch/<post>.json) gets
    the second half named: `kit.py launch <post> --session-id <id>` (the post and Trains row then carry the session);
  - PostToolUse ArtifactData (CL72): the versions and the batch outcome the reply names go to .kit/run/artifact/
    seen.json, where `kit.py artifact sync` reads them (no version is ever copied by hand);
  - Stop (once; `stop_hook_active` lets the second stop through): blocked while a batch file was built and never
    recorded sent, the local copy holds changes the Artifact has not got (`kit.py artifact status`), or a launch
    still waits for its --session-id half.
Silent on a local board (no Artifact in the config) and for the orchestrator's subagents. Never refuses a tool.
"""
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, project_dir  # noqa: E402
import kit_config as kc  # noqa: E402

LOCAL_WRITE = re.compile(r"board_cli\.py\b[^\n;&|]*\b(?:set|update|str-replace|batch|reply|import)\b|"
                         r"kit\.py\b[^\n;&|]*\b(?:step\s+(?:new|start|complete|drop)|adopt|welcome|pair)\b")
PART_PROMPT = re.compile(r"^Your prompt is docs/prompts/([\w.-]+)\.md\b")
PUSH = ("`python3 -I .kit/kit.py artifact sync` (CL72: it prints the calls to make, exactly; run it again until it says "
        "\"in sync\")")


def artifact_board(data):
    if os.environ.get("KIT_ARTIFACT_URL"):
        return True
    c = config(data)
    k = c.get("kit") or {}
    a = k.get("artifact") if isinstance(k.get("artifact"), dict) else {}
    return any("claude.ai/" in str(u or "") for u in (c.get("board_url"), k.get("artifact_url"), a.get("url")))


def art_dir(root):
    return os.environ.get("KIT_ARTIFACT_DIR") or os.path.join(root, ".kit", "run", "artifact")


def launch_dir(root):
    return os.path.join(root, ".kit", "run", "launch")


def say(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def session_of(resp):
    m = re.search(r"\bsession_[A-Za-z0-9]{4,}\b", json.dumps(resp) if not isinstance(resp, str) else resp)
    return m.group(0) if m else ""


def on_create_session(root, ti, resp):
    m = PART_PROMPT.match(str(ti.get("prompt") or "").strip())
    if not m:
        return
    pid = m.group(1)
    p = os.path.join(launch_dir(root), pid + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        return
    sid = session_of(resp)
    mark.update(called=True, session_id=sid)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(mark, fh)
    except OSError:
        pass
    say("PostToolUse", "LAUNCH, second half: run `python3 -I .kit/kit.py launch %s --session-id %s` now: the post and "
        "its Trains row then carry this session (and its batch goes to the Artifact)." % (pid, sid or "<the session_id>"))


def unsent(root):
    """Why a stop must wait: batch files never recorded sent, changes the Artifact has not got, launches half done."""
    why = []
    batches = sorted(glob.glob(os.path.join(art_dir(root), "out", "batch-*.json")))
    if batches:
        why.append("%d batch file(s) built and never recorded sent (%s): make each ArtifactData batch, then `python3 -I "
                   ".kit/kit.py artifact sent <batch>`" % (len(batches), ", ".join(os.path.relpath(b, root) for b in batches)))
    kit = os.path.join(root, ".kit", "kit.py")
    if not batches and os.path.isfile(kit):
        try:
            r = subprocess.run([sys.executable, "-I", kit, "artifact", "status"], capture_output=True, text=True,
                               timeout=30, cwd=root)
            if r.returncode == 1 and r.stdout.startswith("NOT SENT"):
                why.append(r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    for f in sorted(glob.glob(os.path.join(launch_dir(root), "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                mark = json.load(fh)
        except (OSError, ValueError):
            continue
        if mark.get("called"):
            pid = os.path.basename(f)[:-5]
            why.append("part %s was started but `python3 -I .kit/kit.py launch %s --session-id %s` never ran" % (
                pid, pid, mark.get("session_id") or "<its session id>"))
    return why


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path") or ""):
        return 0
    if not artifact_board(data):
        return 0
    root = project_dir(data)
    ev = str(data.get("hook_event_name") or "")
    name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") or {}
    if ev == "PostToolUse" and name.endswith("create_session"):
        on_create_session(root, ti, data.get("tool_response"))
    elif ev == "PostToolUse" and name == "ArtifactData":
        # CL72: the versions and the batch outcome this reply names, read here so no session copies a number by hand
        resp = data.get("tool_response")
        err = isinstance(resp, dict) and bool(resp.get("is_error") or resp.get("isError"))
        if kc.board_call(ti):
            try:
                kc.seen_record(art_dir(root), ti, resp, error=err)
            except (OSError, ValueError):
                pass
    elif ev == "PostToolUse" and name == "Bash":
        resp = data.get("tool_response") or {}
        failed = isinstance(resp, dict) and (resp.get("interrupted") or resp.get("exit_code") not in (None, 0))
        if not failed and LOCAL_WRITE.search(str(ti.get("command") or "")) and "artifact push" not in str(ti.get("command")):
            say("PostToolUse", "BOARD: that changed the local copy only; the Artifact (the live board) gets it with %s. "
                "Batch several changes, then push once; a stop checks it (CL30)." % PUSH)
    elif ev == "Stop" and not data.get("stop_hook_active"):
        why = unsent(root)
        if why:
            print(json.dumps({"decision": "block", "reason": "NOT ON THE LIVE BOARD YET (CL30): " + "; ".join(why) +
                              ". Then stop again."}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
