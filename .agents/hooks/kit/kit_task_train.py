#!/usr/bin/env python3
"""Claude Code PostToolUse (TaskCreate | TaskUpdate), orchestrator only: its task list IS the train, on the board.

A thin adapter over `kit.py step` (the same commands an agy orchestrator runs by hand), so both CLIs share one logic:

  TaskCreate with metadata {train, part}  -> `kit.py step new`: the step's post (an existing one for that train + part
                                             is adopted, never duplicated) and its generated prompt
                                             docs/prompts/<post id>.md; the hook answers with the launch command
  TaskUpdate in_progress                  -> `kit.py step start`
  TaskUpdate completed                    -> `kit.py step complete`: status complete (the owner moves it to Done), its
                                             review, and every kit.review.train_every steps a cross review of the train
  TaskUpdate deleted                      -> `kit.py step drop` (only while no part started it)

Metadata keys: train, part (required for a step), parent, sources (the owner's or a review's comment ids the step
answers), model, review (false: no review). A task without train and part stays the orchestrator's own. Other sessions
(parts, the clerk, reviews) are ignored: KIT_ROLE, else the transcript's first prompt. Never blocks the tool.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402
from kit_pre_compact import inspect_claude_transcript  # noqa: E402


def kit(root, args):
    path = os.path.join(root, ".kit", "kit.py")
    if not os.path.isfile(path):
        return {"error": "no .kit/kit.py"}
    try:
        r = subprocess.run([sys.executable, "-I", path, "step"] + args, capture_output=True, text=True, timeout=60, cwd=root)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"error": r.stderr.strip()[-200:]}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"error": str(e)}


def on_create(root, st, ti, tr):
    task = (tr or {}).get("task") if isinstance(tr, dict) else None
    tid = str((task or {}).get("id") or "")
    meta = ti.get("metadata") if isinstance(ti.get("metadata"), dict) else {}
    train, part = str(meta.get("train") or "").strip(), str(meta.get("part") or "").strip()
    if not tid or not train or not part:
        return None
    args = ["new", "--train", train, "--part", part, "--title", str(ti.get("subject") or part),
            "--task", str(ti.get("description") or "")]
    if meta.get("parent"):
        args += ["--parent", str(meta["parent"])]
    for src in meta.get("sources") or []:
        args += ["--source", str(src)]
    if meta.get("model"):
        args += ["--model", str(meta["model"])]
    if meta.get("review") is False:
        args.append("--no-review")
    res = kit(root, args)
    if res.get("error"):
        return "Task %s (train %s step %s): %s; it is only in your task list." % (tid, train, part, res["error"])
    st["tasks"][tid] = res["post"]
    if res.get("adopted"):
        return "Task %s is train %s step %s: its post %s already exists (adopted; nothing new written).%s" % (
            tid, train, part, res["post"],
            (" Its prompt: %s; launch: %s" % (res["prompt"], res["launch"])) if res.get("prompt") else "")
    if not res.get("prompt"):
        return "Task %s is train %s step %s: post %s created (planned); no prompt template (.kit/claude-cli/part.md)." % (
            tid, train, part, res["post"])
    return ("Task %s is train %s step %s: post %s created (planned), prompt %s written from your description%s. "
            "To launch: commit and push it, TaskUpdate %s in_progress, then: %s") % (
        tid, train, part, res["post"], res["prompt"], "" if res.get("prompt_written") else " (it existed: kept)", tid,
        res["launch"])


def on_update(root, st, ti):
    pid = st["tasks"].get(str(ti.get("taskId") or ""))
    if not pid:
        return None
    status = str(ti.get("status") or "")
    if status == "in_progress":
        res = kit(root, ["start", pid])
        return "Post %s: status in progress." % pid if res.get("ok") else None
    if status == "completed":
        res = kit(root, ["complete", pid])
        if not res.get("ok"):
            return None
        rv = " ".join(res.get("reviews") or [])
        return "Post %s: status complete (the owner moves it to Done).%s" % (
            pid, (" " + rv + " Its verdict comes to you as \"Review of %s: ...\"." % pid) if rv else "")
    if status == "deleted":
        res = kit(root, ["drop", pid])
        return "Post %s: %s." % (pid, "removed" if res.get("ok") else res.get("why") or "kept")
    return None


def is_orchestrator(data, st):
    if "role" not in st:
        st["role"] = os.environ.get("KIT_ROLE") or inspect_claude_transcript(str(data.get("transcript_path") or ""))[1] or ""
    return st["role"] == "orchestrator"


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    name = str(data.get("tool_name") or "")
    if name not in ("TaskCreate", "TaskUpdate"):
        return 0
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", str(data.get("session_id") or "session"))
    path = os.path.join(tempfile.gettempdir(), "kit-task-train-%s.json" % sid)
    # Parallel tool calls run their hooks at once: one lock per session keeps every change in order.
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            st = {"tasks": {}}
        msg = None
        if is_orchestrator(data, st):
            root = project_dir(data)
            ti = data.get("tool_input") or {}
            msg = on_create(root, st, ti, data.get("tool_response")) if name == "TaskCreate" else on_update(root, st, ti)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
        except OSError:
            pass
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
