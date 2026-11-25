#!/usr/bin/env python3
"""Claude Code PostToolUse and Stop, a part's session on a computer: what it is doing now, on its step, for the owner to see.

No model call, nothing said back, nothing refused. At most one write every 15 seconds (and one when a turn ends) of
activity/<post> on the board: {at, actions, what, state, pieces}. `what` is the plain words of the last Bash call's own
description ("Run the tests"), or the file it edits; `actions` the calls counted since the session began; `state` "working"
or "stopped" (the turn ended). `pieces`: the numbers of the step's pieces whose file (a path the task names in that piece's line,
`.kit/run/pieces/<post>.json`) changed last, which the page shows as "Now". It never writes a piece's status: the part ticks it
done, since a file that exists proves nothing finished. The post is KIT_POST (set by `kit.py launch`), else a part's first prompt.
The write is a detached `board_cli.py set` (activity/ is not a post: no gate, no git copy, no "new" mark).
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import project_dir  # noqa: E402

EVERY = 15


KIT_WORDS = {"form": "Filling in its result", "done": "Checking its result", "resume": "Reading its task", "sub": "Ticking a piece",
             "train": "Reading the train", "ask": "Asking you a question", "commands": "Looking up a kit command"}
READ = {"grep", "rg", "cat", "head", "tail", "less", "wc", "ls", "find", "awk", "sed", "nl", "diff", "tree"}


def bash_words(cmd):
    """Plain words for a shell command the session gave no description of: the program and file names only, never its arguments
    (a token or a password can be one)."""
    c = str(cmd or "")
    base = lambda p: os.path.basename(p.rstrip("/")) or p  # noqa: E731
    m = re.search(r"\.kit/kit\.py\s+([\w-]+)", c)
    if m:
        return KIT_WORDS.get(m.group(1), "Running the kit's %s" % m.group(1))
    if re.search(r"\bgit\s+commit\b", c):
        return "Committing"
    if re.search(r"\bgit\s+push\b", c):
        return "Pushing"
    if re.search(r"\b(?:pytest|unittest|jest|vitest|node\s+--test|npm\s+(?:run\s+)?test|run_all_tests|cargo\s+test|go\s+test)\b|[\w-]*test[\w-]*\.(?:py|c?js|mjs|ts|sh)\b", c):
        return "Running the tests"
    m = re.search(r"(?:\bcat|\btee(?:\s+-a)?)\s*>{0,2}\s*([\w./-]+\.\w+)", c) or re.search(r">{1,2}\s*([\w./-]+\.\w+)", c)
    if m:
        return "Writing %s" % base(m.group(1))
    m = re.search(r"\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+([\w./-]+)", c)
    if m:
        return "Editing %s" % base(m.group(1))
    words = [w for w in re.split(r"[\s;&|()]+", c) if w and not re.match(r"^[A-Z_][A-Z0-9_]*=", w)]
    while words and words[0] in ("cd", "for", "do", "done", "then", "if", "time", "timeout", "env", "nohup", "sudo") or (words and re.match(r"^[\d.]+[smh]?$", words[0])):
        words = (words[words.index("do") + 1:] if "do" in words else []) if words[0] == "for" else words[2:] if words[0] == "cd" else words[1:]
    prog = base(words[0]) if words else ""
    if prog in ("python", "python3", "node", "bash", "sh") and len(words) > 1:
        f = next((w for w in words[1:] if re.search(r"\.(?:py|c?js|mjs|ts|sh)$", w)), "")
        return "Running %s" % base(f) if f else "Running a %s script" % ("Python" if prog.startswith("python") else prog)
    if prog == "git":
        return "Checking git"
    if prog in READ:
        f = next((w for w in words[1:] if re.search(r"\w\.\w{1,5}$", w) and not w.startswith("-")), "")
        return "Reading %s" % base(f) if f else "Reading the code"
    return ("Running %s" % prog[:30]) if re.match(r"^[\w.+-]+$", prog) else "Running a command"


def what_of(name, ti):
    if name == "Bash":
        d = re.sub(r"\s+", " ", str(ti.get("description") or "")).strip()
        return d[:90] or bash_words(ti.get("command"))
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return "Editing %s" % os.path.basename(str(ti.get("file_path") or ti.get("notebook_path") or "a file"))
    if name in ("SendMessage", "mcp__claude-code-remote__send_message"):
        return "Sending a message"
    return ""


def changed_pieces(root, pieces, since, ti):
    """The numbers of the pieces whose file this call edits or that changed on disk since `since`."""
    edited = str(ti.get("file_path") or "")
    hit = []
    for x in pieces:
        for f in x.get("files") or []:
            p = os.path.join(root, f)
            try:
                new = os.path.getmtime(p) > since
            except OSError:
                new = False
            if new or (edited and (edited == p or edited.endswith("/" + f))):
                hit.append(x["n"])
                break
    return hit


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    role = str(os.environ.get("KIT_ROLE") or "").strip().lower()
    if role not in ("", "part") or os.environ.get("KIT_ACTIVITY") == "0":
        return 0
    root = project_dir(data)
    run = os.path.join(root, ".kit", "run", "activity")
    sid = re.sub(r"[^\w.-]", "_", str(data.get("session_id") or "session"))
    path = os.path.join(run, sid + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {"since": time.time(), "actions": 0, "written": 0, "pieces": []}
    post = os.environ.get("KIT_POST") or st.get("post")
    if post is None:
        try:
            import kit_task_train
            post = kit_task_train.part_post(data, st.setdefault("tt", {})) or ""
        except Exception:
            post = ""
        if post or role == "part":
            st["post"] = post
    if not post:
        if "post" in st:
            _save(path, st)
        return 0
    ev = str(data.get("hook_event_name") or "")
    name, ti = str(data.get("tool_name") or ""), data.get("tool_input") or {}
    now = time.time()
    stop = ev == "Stop"
    if not stop:
        st["actions"] = st.get("actions", 0) + 1
        st["what"] = what_of(name, ti) or st.get("what") or ""
    pieces, pf = [], os.path.join(root, ".kit", "run", "pieces", post + ".json")
    try:
        with open(pf, encoding="utf-8") as fh:
            pieces = [x for x in json.load(fh) if isinstance(x, dict) and x.get("files")]
        if not st.get("checked"):                    # its first call: what changed since turn 1 wrote the file
            st["checked"] = os.path.getmtime(pf)
    except (OSError, ValueError):
        pass
    moved = changed_pieces(root, pieces, st.get("checked") or st["since"], ti) if pieces and not stop else []
    st["checked"] = now
    if moved and moved != st.get("pieces"):
        st["pieces"] = moved
    if stop or moved or now - st.get("written", 0) >= EVERY:
        st["written"] = now
        doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "actions": st["actions"], "what": st.get("what") or "",
               "state": "stopped" if stop else "working", "pieces": st.get("pieces") or []}
        cli = os.path.join(root, ".kit", "board_cli.py")
        if os.path.isfile(cli):
            try:
                subprocess.Popen([sys.executable, "-I", cli, "set", "activity", post, "--data", json.dumps(doc, ensure_ascii=False)],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except OSError:
                pass
    _save(path, st)
    return 0


def _save(path, st):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
