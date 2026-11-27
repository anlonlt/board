#!/usr/bin/env python3
"""Claude Code PostToolUse (TaskCreate | TaskUpdate): the orchestrator's task list IS the train, a part's the pieces of its step.

A thin adapter over `kit.py step` (the same commands an agy orchestrator runs by hand), so both CLIs share one logic:

  TaskCreate with metadata {train, part}  -> `kit.py step new`: the step's post (an existing one for that train + part
                                             is adopted, never duplicated) and its generated prompt
                                             docs/prompts/<post id>.md; the hook answers with the launch command
  TaskUpdate in_progress                  -> `kit.py step start` when the device has a free session slot
                                             (`kit.py capacity --gate`, CL15); else the step stays planned
  TaskUpdate completed                    -> `kit.py step complete`: status complete (the owner moves it to Done), its
                                             review, and every kit.review.train_every steps a cross review of the train
  TaskUpdate deleted                      -> `kit.py step drop` (only while no part started it)
  TaskCompleted (Claude Code's own event) -> a task this hook has no record of (its record died with a container, or
                                             another session made it) finds its step again by its subject, "<PART> ..."
  UserPromptSubmit                        -> the open steps that have no task here, named (at most twice a session)
The record (task id -> post) is kept under .kit/run, per task list, not in /tmp.

Metadata keys: train, part (required for a step), board (an attached board's id: the step goes on its train, CL25), parent, sources (the owner's or a review's comment ids the step
answers), model, review (false: no review). A task without train and part stays the orchestrator's own. Other sessions
(the clerk, reviews) are ignored: KIT_ROLE, else the transcript's first prompt. Never blocks the tool.

A part (KIT_ROLE=part, or a first prompt that names docs/prompts/<post>.md): each task is one piece of its step, a line on its
post (`kit.py sub`), so he sees "3 of 5" and a session that replaces it goes on from there. Nothing is said back.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, project_dir  # noqa: E402
from kit_pre_compact import inspect_claude_transcript  # noqa: E402


def kit(root, args, raw=False):
    """`kit.py step <args>` as JSON; raw=True runs `kit.py <args>` and returns (exit code, output), or None."""
    path = os.path.join(root, ".kit", "kit.py")
    if not os.path.isfile(path):
        return None if raw else {"error": "no .kit/kit.py"}
    if raw:
        try:
            r = subprocess.run([sys.executable, "-I", path] + args, capture_output=True, text=True, timeout=60, cwd=root)
            return r.returncode, (r.stdout + r.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        r = subprocess.run([sys.executable, "-I", path, "step"] + args, capture_output=True, text=True, timeout=60, cwd=root)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"error": r.stderr.strip()[-200:]}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"error": str(e)}


def artifact_board(cfg):
    """CL30: the board is an Artifact when board_url is one, or kit.artifact_url / kit.artifact.url names one (the kit
    repo's board, attached to Harmonize's: board_url stays the local copy). The push reminder used to read board_url only."""
    k = (cfg or {}).get("kit") or {}
    a = k.get("artifact") if isinstance(k.get("artifact"), dict) else {}
    return any("claude.ai/" in str(u or "") for u in (cfg.get("board_url"), k.get("artifact_url"), a.get("url")))


def on_web():
    """CL51: the Artifact is the Claude Code web session's; on a computer no hook asks for an `artifact push`."""
    return str(os.environ.get("CLAUDE_CODE_REMOTE") or "").lower() in ("1", "true", "yes")


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
    if meta.get("board"):                      # CL25: an attached board's train (repos/<id>/ in the Artifact)
        args += ["--board", str(meta["board"])]
    res = kit(root, args)
    if res.get("error"):
        hint = ""
        cfg = {}
        try:
            cfg = json.load(open(os.path.join(root, ".kit", "config.json"), encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if artifact_board(cfg):
            hint = (" The board is an Artifact: start its local copy first (`python3 -I .kit/kit.py artifact pull`, the "
                    "calls it prints, `python3 -I .kit/kit.py artifact load`), then TaskUpdate this task or create it again.")
        return "Task %s (train %s step %s): %s; it is only in your task list.%s" % (tid, train, part, res["error"], hint)
    st["tasks"][tid] = res["post"]
    if res.get("adopted"):
        return "Task %s is train %s step %s: its post %s already exists (adopted; nothing new written).%s" % (
            tid, train, part, res["post"],
            (" Its prompt: %s; launch: %s" % (res["prompt"], res["launch"])) if res.get("prompt") else "")
    if not res.get("prompt"):
        return "Task %s is train %s step %s: post %s created (planned); no prompt template (.kit/claude-cli/part.md)." % (
            tid, train, part, res["post"])
    sent = (" It is on the attached board %s: first %s." % (res["board"], res["next"])) if res.get("artifact_write") else ""
    n = res.get("pieces") or 0
    sent = (" Its %d numbered lines are its pieces: he sees them, and the part cannot end while one is open." % n if n else "") + sent
    return ("Task %s is train %s step %s: post %s created (planned), prompt %s written from your description%s.%s "
            "To launch: commit and push it, TaskUpdate %s in_progress, then: %s") % (
        tid, train, part, res["post"], res["prompt"], "" if res.get("prompt_written") else " (it existed: kept)", sent,
        tid, res["launch"])


def on_completed_event(root, st, data):
    """TaskCompleted fires before the TaskUpdate it belongs to lands: the step is found here, and on_update completes it."""
    tid, subject = str(data.get("task_id") or ""), str(data.get("task_subject") or "").strip()
    if tid and tid not in st["tasks"] and subject:
        res = kit(root, ["find", "--part", re.split(r"[\s:·]+", subject, maxsplit=1)[0]])
        if res.get("post"):
            st["tasks"][tid] = res["post"]
    pid = st["tasks"].get(tid)
    if not pid:
        return None
    res = kit(root, ["gate", pid])
    return None if res.get("ok") is True else res.get("why") or "%s: nothing could say it is proved (`python3 -I .kit/kit.py step gate %s` gave no answer)" % (pid, pid)


def on_prompt(root, st):
    if st.get("asked", 0) >= 2:
        return None
    res = kit(root, ["open"])
    have = set(st["tasks"].values())
    miss = [x for x in res.get("steps") or [] if x["post"] not in have]
    if not miss:
        return None
    st["asked"] = st.get("asked", 0) + 1
    return ("%d open step%s of the trains %s no task in your list: %s. One TaskCreate each, subject \"<PART> <title>\", metadata "
            "{train, part%s}: the existing post is adopted. Without it, its completion, review and status never reach the board." % (
                len(miss), "" if len(miss) == 1 else "s", "has" if len(miss) == 1 else "have",
                "; ".join("%s (%s%s%s)" % (x["part"], x["train"], (", board " + x["board"]) if x["board"] else "",
                                           ", review due" if x["state"] == "review due" else "") for x in miss[:12])
                + (" and %d more" % (len(miss) - 12) if len(miss) > 12 else ""), ", board" if any(x["board"] for x in miss) else ""))


def on_update(root, st, ti):
    pid = st["tasks"].get(str(ti.get("taskId") or ""))
    if not pid:
        return None
    status = str(ti.get("status") or "")
    if status == "in_progress":
        cap = kit(root, ["capacity", "--gate"], raw=True)
        if cap is not None and cap[0] == 1:
            return ("Post %s stays planned: %s. Do not launch it now (the guard refuses it); TaskUpdate it in_progress "
                    "again when a part ends." % (pid, cap[1].replace("NO FREE SLOT: ", "no free session slot on this device: ")))
        res = kit(root, ["start", pid])
        return "Post %s: status in progress." % pid if res.get("ok") else None
    if status == "completed":
        res = kit(root, ["complete", pid])
        if not res.get("ok"):
            return ("Post %s NOT complete: %s. TaskUpdate it back to in_progress." % (pid, res["why"])) if res.get("why") else None
        rv = " ".join(res.get("reviews") or [])
        return "Post %s: status complete (the owner moves it to Done).%s" % (
            pid, (" " + rv + " Its verdict comes to you as \"Review of %s: ...\"." % pid) if rv else "")
    if status == "deleted":
        res = kit(root, ["drop", pid])
        return "Post %s: %s." % (pid, "removed" if res.get("ok") else res.get("why") or "kept")
    return None


PART_POST = re.compile(r"docs/prompts/([\w.-]+)\.md")
PART_POST_ARG = re.compile(r"--role part --post ([\w.-]+)")
PART_OPEN = re.compile(r"""^\s*Your prompt (?:is [`"']?(?:\S*/)?docs/prompts/|follows)""")
AGY_USER_REQ = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
AGY_SUBAGENT_MSG = re.compile(r"\[Message\][^\n]*?\bcontent=(.*?)(?:\n</SYSTEM_MESSAGE>|\Z)", re.S)


def _first_prompt(data):
    """The opening prompt of this session, from a Claude Code or agy transcript, else agy's lastUserInput."""
    tp = str(data.get("transcriptPath") or data.get("transcript_path") or "")
    if not tp:
        cid = str(data.get("conversationId") or data.get("conversation_id") or "")
        if cid and re.fullmatch(r"[A-Za-z0-9._-]+", cid):
            cand = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain", cid,
                                ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(cand):
                tp = cand
    try:
        if tp:
            with open(tp, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(e, dict):
                        continue
                    kind = e.get("type")
                    if kind == "user" and not e.get("isMeta") and not e.get("isSidechain"):
                        c = (e.get("message") or {}).get("content")
                        c = " ".join(x.get("text") or "" for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c or "")
                        if c.strip() and not c.lstrip().startswith("<"):
                            return c
                    elif kind == "USER_INPUT":
                        c = str(e.get("content") or "")
                        m = AGY_USER_REQ.search(c)
                        if m:
                            c = m.group(1)
                        if c.strip():
                            return c
                    elif kind == "SYSTEM_MESSAGE":
                        c = str(e.get("content") or "")
                        m = AGY_SUBAGENT_MSG.search(c)
                        if m and m.group(1).strip():
                            return m.group(1).strip()
    except OSError:
        pass
    return str(data.get("lastUserInput") or "")


def _part_post_from_branch(root):
    """Fallback when an agy subagent payload lacks transcriptPath: match branch name (e.g. subagent-Part--CL44-...)
    uniquely against docs/prompts/<post>.md."""
    try:
        br = subprocess.run(["git", "-C", root, "branch", "--show-current"], capture_output=True, text=True, timeout=5).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not br:
        return ""
    pdir = os.path.join(root, "docs", "prompts")
    if not os.path.isdir(pdir):
        return ""
    hits = []
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".md"):
            continue
        pid = fn[:-3]
        if pid.lower().startswith("cr-"):
            continue
        part = pid.split("-", 1)[0].lower()
        if pid.lower() in br or (len(part) >= 2 and re.search(r"(^|[-/_])%s($|[-/_])" % re.escape(part), br)):
            hits.append(pid)
    return hits[0] if len(hits) == 1 else ""


def _landing(root):
    """The project's .kit/landing.py as a module (CL50: one reading of a worktree's step), or None."""
    f = os.path.join(root, ".kit", "landing.py")
    if not os.path.isfile(f):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_landing_hook", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.ROOT = root
        return mod
    except Exception:
        return None


def own_step(root, branch_post=None):
    """CL50: (step, note): landing.own_step for `root`; `branch_post` (a value or a callable) is asked only when no step
    is recorded and KIT_POST is set. Without landing.py: KIT_POST, as before."""
    env = str(os.environ.get("KIT_POST") or "").strip()
    la = _landing(root)
    if la is None or not hasattr(la, "own_step"):
        return env, ""
    try:
        bp = ""
        if env and not la.recorded_step(root):
            bp = branch_post() if callable(branch_post) else (branch_post or "")
        return la.own_step(root, bp)
    except Exception:
        return env, ""


def part_post(data, st):
    """The post of the step this session is a part of, or "".
    Note (CL44): an agy part subagent inherits KIT_ROLE=orchestrator from `kit/up` in its parent process, so an explicit
    KIT_POST or a part opening prompt (`Your prompt is docs/prompts/<post>.md`) overrides an inherited role."""
    role = str(os.environ.get("KIT_ROLE") or "").strip().lower()
    if os.environ.get("KIT_POST"):
        # CL50: the worktree's recorded step first (a `claude --bg` part can run in a process another launch made, its
        # KIT_POST that launch's); KIT_POST only when nothing is recorded or it agrees. A disagreement is said once.
        step, note = own_step(project_dir(data), lambda: _part_post_from_branch(project_dir(data)))
        if note:
            st["step_note"] = note
        return step
    if role not in ("", "part", "orchestrator"):
        return ""
    if st.get("part_post") is None or (not st["part_post"] and role == "part"):
        first = _first_prompt(data)
        m = None
        if first:
            m = PART_POST.search(first) if PART_OPEN.match(first) else None
            if m is None and first.lstrip().startswith("You are part "):
                # CL41: a web part the orchestrator launched in its own words ("You are part CL41 ... resume --role part --post <id>")
                m = PART_POST_ARG.search(first)
        if m and not m.group(1).startswith("CR-"):
            st["part_post"] = m.group(1)
        elif not first:
            br_hit = _part_post_from_branch(project_dir(data)) if role != "orchestrator" else ""
            if br_hit:
                st["part_post"] = br_hit
            else:
                return ""                                    # not in the transcript yet: asked again
        else:
            st["part_post"] = ""
    return st["part_post"]


_AGY_STEP_NUM = re.compile(r"^\s*(?:(?:item|piece|step)\s+|#)?(\d+)(?:[.):\s-]+|$)", re.I)
_AGY_STEP_STATUS = {"pending": "open", "in_progress": "doing", "done": "done", "skipped": "drop", "failed": "open"}


def _agy_step_list(ti):
    raw = ti.get("steps")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [x for x in (raw or []) if isinstance(x, dict)]


def _post_subtasks(root, post):
    """Read current subtasks of `post` from board_cli.py (best-effort) so update_steps can match by title overlap or index."""
    cli = os.path.join(root, ".kit", "board_cli.py")
    if not os.path.isfile(cli):
        return []
    try:
        r = subprocess.run([sys.executable, "-I", cli, "get", "posts", post], capture_output=True, text=True, timeout=15, cwd=root)
        if r.returncode == 0 and r.stdout.strip():
            d = json.loads(r.stdout).get("data") or {}
            return [x for x in (d.get("subtasks") or []) if isinstance(x, dict)]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return []


def _match_agy_piece(title, idx, total_steps, subtasks):
    """Resolve an agy update_steps item to a piece number or title for `kit.py sub`."""
    m = _AGY_STEP_NUM.match(title)
    if m:
        return m.group(1)
    tl = title.strip().lower()
    for row in subtasks:
        if str(row.get("title") or "").strip().lower() == tl:
            return str(row.get("n") or title)
    if subtasks:
        words = {w for w in re.findall(r"[a-z0-9_]{3,}", tl)}
        best_n, best_score = None, 0
        for row in subtasks:
            rw = {w for w in re.findall(r"[a-z0-9_]{3,}", str(row.get("title") or "").lower())}
            score = len(words & rw)
            if score > best_score:
                best_score, best_n = score, row.get("n")
        if best_score >= 2 and best_n is not None:
            return str(best_n)
        if total_steps == len(subtasks) and idx < len(subtasks) and subtasks[idx].get("n") is not None:
            return str(subtasks[idx]["n"])
    return title


def on_agy_steps(root, st, ti, post):
    """CL44 (item 8): mirror agy's `update_steps` checklist onto the step's post pieces (`kit.py sub`)."""
    steps = _agy_step_list(ti)
    if not steps:
        return
    prev = st.setdefault("agy_steps", {})
    subtasks = None
    for idx, item in enumerate(steps):
        title = str(item.get("title") or "").strip()
        raw_st = str(item.get("status") or "").strip().lower()
        mapped = _AGY_STEP_STATUS.get(raw_st)
        if not title or not mapped:
            continue
        key = "%d:%s" % (idx, title.lower())
        if prev.get(key) == mapped:
            continue
        if subtasks is None:
            subtasks = _post_subtasks(root, post)
        target = _match_agy_piece(title, idx, len(steps), subtasks)
        args = ["sub", post, target, "--status", mapped, "--known"]
        if mapped == "drop":
            args += ["--why", "skipped in agy step list"]
        kit(root, args, raw=True)
        prev[key] = mapped


def on_piece(root, st, name, ti, tr, post):
    subs = st.setdefault("subs", {})
    if name == "TaskCreate":
        task = (tr or {}).get("task") if isinstance(tr, dict) else None
        tid, title = str((task or {}).get("id") or ""), str(ti.get("subject") or "").strip()
        if not tid or not title:
            return
        subs[tid] = title
        args = [title]
    else:
        tid = str(ti.get("taskId") or "")
        was, title = subs.get(tid), str(ti.get("subject") or "").strip()
        status = {"pending": "open", "in_progress": "doing", "completed": "done", "deleted": "drop"}.get(str(ti.get("status") or ""))
        if not was or not (status or title):
            return
        subs[tid] = title or was
        args = [subs[tid]] + (["--status", status] if status else []) + (["--was", was] if title and title != was else [])
    kit(root, ["sub", post] + args + ["--known"], raw=True)


def is_orchestrator(data, st):
    if not st.get("role"):
        # UserPromptSubmit comes before the first prompt is in the transcript: what it finds then is not the answer (2026-11-25:
        # "" was kept, and an orchestrator's whole task list never reached the board). "-": read since, and not an orchestrator.
        st["role"] = os.environ.get("KIT_ROLE") or inspect_claude_transcript(str(data.get("transcript_path") or ""))[1] or ""
        prefix = str((config(data).get("kit") or {}).get("orchestrator_branch_prefix") or "")
        if not st["role"] and prefix:
            # a project that names its orchestrators' branches (Harmonize: orchestrator-<n>)
            try:
                br = subprocess.run(["git", "-C", project_dir(data), "rev-parse", "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True, timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                br = ""
            st["role"] = "orchestrator" if br.startswith(prefix) else ""
        if not st["role"] and data.get("hook_event_name") != "UserPromptSubmit":
            st["role"] = "-"
    return st["role"] == "orchestrator"


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tc = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else {}
    name = str(data.get("tool_name") or tc.get("name") or "")
    event = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    is_agy = bool(tc or data.get("hookEventName") or data.get("conversationId"))
    if name not in ("TaskCreate", "TaskUpdate", "update_steps") and event not in ("TaskCompleted", "UserPromptSubmit"):
        return 0
    # a list shared between sessions (CLAUDE_CODE_TASK_LIST_ID) has one record: its task ids are the same in each
    sid = re.sub(r"[^a-zA-Z0-9._-]", "_", str(
        os.environ.get("CLAUDE_CODE_TASK_LIST_ID") or data.get("session_id") or data.get("conversationId")
        or data.get("conversation_id") or "session"))
    d = os.path.join(project_dir(data), ".kit", "run", "task-train")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = tempfile.gettempdir()
    path = os.path.join(d, "kit-task-train-%s.json" % sid)
    # Parallel tool calls run their hooks at once: one lock per session keeps every change in order.
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            st = {"tasks": {}}
        msg = refuse = None
        piece = part_post(data, st)
        msg = st.pop("step_note", None)                      # CL50: KIT_POST names another step: said once
        if piece:
            if name == "update_steps":
                args = tc.get("args") if isinstance(tc.get("args"), dict) else (data.get("tool_input") or {})
                on_agy_steps(project_dir(data), st, args, piece)
            elif name in ("TaskCreate", "TaskUpdate") and event not in ("TaskCompleted", "UserPromptSubmit"):
                on_piece(project_dir(data), st, name, data.get("tool_input") or {}, data.get("tool_response"), piece)
            elif event == "TaskCompleted" and str(data.get("task_id") or "") not in st.get("subs", {}) and str(data.get("task_subject") or "").strip():
                # a list opened by a session that replaces another: its record of the titles is not here
                kit(project_dir(data), ["sub", piece, str(data["task_subject"]).strip(), "--status", "done", "--known"], raw=True)
        elif not is_agy and is_orchestrator(data, st):
            root = project_dir(data)
            ti = data.get("tool_input") or {}
            if event == "TaskCompleted":
                refuse = on_completed_event(root, st, data)
            elif event == "UserPromptSubmit":
                msg = on_prompt(root, st)
            else:
                msg = on_create(root, st, ti, data.get("tool_response")) if name == "TaskCreate" else on_update(root, st, ti)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
        except OSError:
            pass
    if is_agy:
        return 0                                             # agy PostToolUse: never emit injectSteps
    if refuse:                                               # exit 2: Claude Code keeps the task open and says why
        print("NOT complete: %s." % refuse, file=sys.stderr)
        return 2
    if msg:
        if artifact_board(config(data)) and event != "UserPromptSubmit" and on_web():
            # an Artifact board: the step is on the local copy; one batch carries it (and every other change) across
            msg += (" The board is an Artifact: send this to it with `python3 -I .kit/kit.py artifact push` (it prints one "
                    "ArtifactData batch per 50 changes); batch several steps before you push.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event or "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
