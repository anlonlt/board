#!/usr/bin/env python3
"""PreToolUse, PostToolUse, PostInvocation (agy) and Stop, a part's session: what it is doing now, on its step, for the owner to see.
Works on Claude Code (CLI and web) and agy. On a computer the page reads it from the local board within seconds; on the web (CL41)
the hook writes the same document to the container's local copy, and `kit.py artifact push`, which the part runs at each change of
its post and its Stop check asks for, carries it to the Artifact (a hook cannot call ArtifactData).
And, in every session, a loop breaker: the same call with the same input 8 times in a row is said once to the session (and shown on
the step). On Claude Code it is returned as PostToolUse additionalContext; on agy, PostToolUse records it and PostInvocation injects
it via injectSteps (agy ignores injectSteps on PostToolUse). That line is the only thing it ever says; nothing is refused.

No model call. At most one write every 15 seconds (and one when a turn ends, a piece changes, or a loop is detected) of
activity/<post> on the board: {at, actions, what, state, pieces}. `what` is the call starting (before it) or done (after), in the
plain words of a Bash/run_command call's own description/toolSummary ("Run the tests"), or the file it edits/writes; `actions` the
calls counted since the session began; `state` "working" or "stopped" (the turn ended). `pieces`: the numbers of the step's pieces
whose file (a path the task names in that piece's line, `.kit/run/pieces/<post>.json`) changed last, which the page shows as "Now".
It never writes a piece's status: the part ticks it done, since a file that exists proves nothing finished. The post is KIT_POST
(set by `kit.py launch`), else a part's first prompt.
The write is a detached `board_cli.py set` (activity/ is not a post: no gate, no git copy, no "new" mark).
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import is_claude_payload, project_dir  # noqa: E402

EVERY = 15
LOOP = 8                                       # the same call this many times in a row: said once, and on the board


KIT_WORDS = {"form": "Filling in its result", "done": "Checking its result", "resume": "Reading its task", "sub": "Ticking a piece",
             "train": "Reading the train", "ask": "Asking you a question", "commands": "Looking up a kit command",
             "artifact": "Sending its changes to the board"}
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


def _sid(data):
    return re.sub(r"[^\w.-]", "_", str(data.get("session_id") or data.get("conversationId") or data.get("conversation_id") or "session"))


def _file_path(ti):
    for k in ("file_path", "notebook_path", "Path", "TargetFile", "path"):
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _tool_and_input(data):
    tc = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else {}
    name = str(data.get("tool_name") or tc.get("name") or "")
    raw = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else tc.get("args")
    return name, raw if isinstance(raw, dict) else {}


def what_of(name, ti):
    if name in ("Bash", "run_command"):
        d = re.sub(r"\s+", " ", str(ti.get("description") or ti.get("toolSummary") or "")).strip()
        return d[:90] or bash_words(ti.get("command") or ti.get("CommandLine"))
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit", "edit_file", "write_file"):
        verb = "Writing" if name == "write_file" else "Editing"
        return "%s %s" % (verb, os.path.basename(_file_path(ti) or "a file"))
    if name in ("SendMessage", "mcp__claude-code-remote__send_message", "send_message"):
        return "Sending a message"
    return ""


def changed_pieces(root, pieces, since, ti):
    """The numbers of the pieces whose file this call edits or that changed on disk since `since`."""
    edited = _file_path(ti)
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


def call_key(name, ti):
    return name + ":" + json.dumps(ti, sort_keys=True, ensure_ascii=False)[:4000]


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    role = str(os.environ.get("KIT_ROLE") or "").strip().lower()
    if os.environ.get("KIT_ACTIVITY") == "0":
        return 0
    root = project_dir(data)
    claude = is_claude_payload(data)
    ev = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    if ev == "PostInvocation":
        return _say_agy(_pop_pending(root, data))
    name, ti = _tool_and_input(data)
    said = loop_check(root, data, ev, name, ti, claude)         # every session: silent unless the same call repeats
    if role not in ("", "part"):
        return _say(said, claude)
    run = os.path.join(root, ".kit", "run", "activity")
    sid = _sid(data)
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
        return _say(said, claude)
    now = time.time()
    stop, before = ev == "Stop", ev == "PreToolUse"
    if not stop:
        if not before:
            st["actions"] = st.get("actions", 0) + 1
        # before a call: what it starts (a test run of minutes shows while it runs); after: what it did
        st["what"] = what_of(name, ti) or st.get("what") or ""
    pieces, pf = [], os.path.join(root, ".kit", "run", "pieces", post + ".json")
    try:
        with open(pf, encoding="utf-8") as fh:
            pieces = [x for x in json.load(fh) if isinstance(x, dict) and x.get("files")]
        if not st.get("checked"):                    # its first call: what changed since turn 1 wrote the file
            st["checked"] = os.path.getmtime(pf)
    except (OSError, ValueError):
        pass
    moved = changed_pieces(root, pieces, st.get("checked") or st["since"], ti) if pieces and not stop and not before else []
    st["checked"] = now
    if moved and moved != st.get("pieces"):
        st["pieces"] = moved
    if stop or moved or said or now - st.get("written", 0) >= EVERY:
        st["written"] = now
        doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "actions": st["actions"], "what": st.get("what") or "",
               "state": "stopped" if stop else "working", "pieces": st.get("pieces") or []}
        if said:
            doc["loop"] = LOOP
        cli = os.path.join(root, ".kit", "board_cli.py")
        if os.path.isfile(cli):
            try:
                subprocess.Popen([sys.executable, "-I", cli, "set", "activity", post, "--data", json.dumps(doc, ensure_ascii=False)],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except OSError:
                pass
    _save(path, st)
    return _say(said, claude)


def _loop_path(root, data):
    return os.path.join(root, ".kit", "run", "activity", "loop-%s.json" % _sid(data))


def loop_check(root, data, ev, name, ti, claude=True):
    """The same tool with the same input LOOP times in a row (after each call): one line to the session, once per run of them."""
    if ev != "PostToolUse" or not name:
        return ""
    path = _loop_path(root, data)
    try:
        with open(path, encoding="utf-8") as fh:
            lp = json.load(fh)
    except (OSError, ValueError):
        lp = {}
    key = call_key(name, ti)
    n = lp.get("n", 0) + 1 if lp.get("key") == key else 1
    msg = ""
    if n == LOOP:
        msg = ("The same %s call, with the same input, %d times in a row: it is not getting anywhere. Change the approach, or stop and say "
               "in your post what blocks you." % (name, LOOP))
    new_lp = {"key": key, "n": n}
    if msg and not claude:
        new_lp["pending"] = msg                                 # agy ignores injectSteps on PostToolUse; PostInvocation delivers it
    elif not claude and lp.get("key") == key and lp.get("pending"):
        new_lp["pending"] = lp["pending"]
    _save(path, new_lp)
    return msg


def _pop_pending(root, data):
    """Consume a pending agy loop-breaker message recorded by PostToolUse, so PostInvocation injects it once."""
    path = _loop_path(root, data)
    try:
        with open(path, encoding="utf-8") as fh:
            lp = json.load(fh)
    except (OSError, ValueError):
        return ""
    msg = str(lp.pop("pending", "") or "")
    if msg:
        _save(path, lp)
    return msg


def _say(text, claude=True):
    if text and claude:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
    return 0


def _say_agy(text):
    if text:
        print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]}, ensure_ascii=False))
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
