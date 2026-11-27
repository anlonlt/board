#!/usr/bin/env python3
"""The kit's hooks behind one line per event: `python3 -I .kit/hook.py <web|cli|agy> <Event>`.

TABLE is the only list of which hook runs when; install.py builds every runtime's settings from it, and this script
runs from it. With `install.py --unified-hooks` a project's settings hold one line per event, and a hook added to the
kit runs in every project with no settings change (a hook installed but wired nowhere has been a real hole: CL30).

What the runtime sees is what the separate hooks would have shown it: every block, denial, reason, context and plain
text passes through (tests/test_unified_hooks.py compares the two). Beyond that, one rule: a guard (PreToolUse, and a step's TaskCompleted) that
hangs, crashes or is missing REFUSES the call and says why; any other hook that fails is reported and blocks nothing.
A guard that is broken for good: fix .kit/hooks/<it>, or `install.py . --no-unified-hooks` goes back to separate lines.
"""
import json
import os
import re
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SEND = "mcp__claude-code-remote__send_message|SendMessage"
SESSION = "mcp__claude-code-remote__create_session"
GUARD, PLAIN = True, False
ANY = None                        # an event without tools
# (event, hook, {runtime: tools it runs on}, guard?, seconds). web: Claude Code with an Artifact board; cli: Claude Code
# on a computer with the local board; agy. PreInvocation and PostInvocation are agy's own events.
TABLE = [
    ("SessionStart", "kit_session_start.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 50),
    ("PreToolUse", "kit_guard.py", {"web": "Bash", "cli": "Bash", "agy": "run_command|invoke_subagent"}, GUARD, 25),
    ("PreToolUse", "kit_memory_jit.py", {"web": "Edit|Write|MultiEdit", "cli": "Edit|Write|MultiEdit",
                                         "agy": "edit_file|write_file"}, PLAIN, 25),
    ("PreToolUse", "kit_say_it_once.py", {"web": SEND, "cli": SEND, "agy": "send_message"}, GUARD, 25),
    # M11: "cli" too: a repo installed for a computer (this kit's own) also works its Artifact board on the web, and its
    # sessions' ArtifactData calls never reached the guard
    ("PreToolUse", "kit_orchestrator_guard.py", {"web": "ArtifactData|" + SESSION, "cli": "ArtifactData"}, GUARD, 25),
    ("PreToolUse", "kit_brief_guard.py", {"web": "ArtifactData|Bash", "cli": "Bash", "agy": "run_command"}, GUARD, 25),
    # CL60: a part's last steps (Bash: kit.py done, end_session.py, unreached; the send to its orchestrator) get the
    # "next and last message is a single ·" line here, before the closing message is written
    ("PostToolUse", "kit_say_it_once.py", {"web": "ArtifactData|Bash|" + SEND, "cli": "Bash|" + SEND,
                                           "agy": "send_message|run_command"}, PLAIN, 50),
    ("PostToolUse", "kit_brief_guard.py", {"web": "Bash", "cli": "Bash", "agy": "run_command"}, PLAIN, 50),
    ("PostToolUse", "kit_cards_reminder.py", {"web": "ArtifactData|" + SESSION}, PLAIN, 50),
    ("PostToolUse", "kit_notify.py", {"web": "ArtifactData"}, PLAIN, 50),
    ("PostToolUse", "kit_task_train.py", {"web": "TaskCreate|TaskUpdate", "cli": "TaskCreate|TaskUpdate",
                                          "agy": "update_steps"}, PLAIN, 130),
    ("PostToolUse", "kit_board_guard.py", {"web": "Bash|" + SESSION, "cli": "Bash"}, PLAIN, 50),
    ("PreToolUse", "kit_activity.py", {"web": "Bash|Edit|Write|MultiEdit", "cli": "Bash|Edit|Write|MultiEdit",
                                       "agy": "run_command|edit_file|write_file"}, PLAIN, 25),
    ("PostToolUse", "kit_activity.py", {"web": "Bash|Edit|Write|MultiEdit|mcp__claude-code-remote__send_message",
                                        "cli": "Bash|Edit|Write|MultiEdit|SendMessage",
                                        "agy": "run_command|edit_file|write_file|send_message"}, PLAIN, 25),
    ("PostToolUse", "kit_wip.py", {"web": "Bash|Edit|Write|MultiEdit", "cli": "Bash|Edit|Write|MultiEdit",
                                   "agy": "run_command|edit_file|write_file"}, PLAIN, 25),
    ("UserPromptSubmit", "kit_say_it_once.py", {"web": ANY, "cli": ANY}, PLAIN, 50),
    ("UserPromptSubmit", "kit_board_typing.py", {"web": ANY, "cli": ANY}, PLAIN, 50),
    ("UserPromptSubmit", "kit_cards_reminder.py", {"web": ANY}, PLAIN, 50),  # the CLI's board server builds the cards
    ("UserPromptSubmit", "kit_task_train.py", {"web": ANY, "cli": ANY}, PLAIN, 50),
    ("TaskCompleted", "kit_task_train.py", {"web": ANY, "cli": ANY}, GUARD, 50),
    ("PreInvocation", "kit_say_it_once.py", {"agy": ANY}, PLAIN, 50),
    ("PreInvocation", "kit_board_typing.py", {"agy": ANY}, PLAIN, 50),
    ("PostInvocation", "kit_say_it_once.py", {"agy": ANY}, PLAIN, 50),
    ("PostInvocation", "kit_activity.py", {"agy": ANY}, PLAIN, 25),
    ("PostInvocation", "kit_memory_jit.py", {"agy": ANY}, PLAIN, 25),
    ("Stop", "kit_board_guard.py", {"web": ANY, "cli": ANY}, PLAIN, 50),
    ("Stop", "kit_stop.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 50),
    ("Stop", "kit_pre_compact.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 50),
    ("Stop", "kit_activity.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 25),
    ("Stop", "kit_wip.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 25),
    ("Stop", "kit_web_save.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 130),
    ("Stop", "kit_say_it_once.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 50),
    ("Stop", "kit_usage.py", {"web": ANY, "cli": ANY, "agy": ANY}, PLAIN, 100),      # CL37/CL44: the usage ledger (no model call)
    ("SessionEnd", "kit_usage.py", {"web": ANY, "cli": ANY}, PLAIN, 100),
    ("PreCompact", "kit_pre_compact.py", {"web": ANY, "cli": ANY}, PLAIN, 50),
]
RUNTIMES = ("web", "cli", "agy")
REFUSES = ("PreToolUse", "TaskCompleted")                    # the events a kit guard decides
TEXT_IS_CONTEXT = ("SessionStart", "UserPromptSubmit")       # Claude Code reads plain stdout as context there


def rows(runtime, event=None):
    """[(event, tools, hook, guard, seconds)] of one runtime, in table order."""
    return [(ev, on[runtime], f, guard, sec) for ev, f, on, guard, sec in TABLE
            if runtime in on and event in (None, ev)]


def tools(runtime, event):
    """The tools one line of this event must cover (None: an event without tools). Every other tool starts nothing."""
    got = [t for r in rows(runtime, event) if r[1] for t in r[1].split("|")]
    return "|".join(dict.fromkeys(got)) or None


def events(runtime):
    return list(dict.fromkeys(r[0] for r in rows(runtime)))


# ---------------------------------------------------------------- run

def _tool(data, runtime):
    if not isinstance(data, dict):
        return None
    if runtime == "agy":
        tc = data.get("toolCall")
        return str(tc.get("name") or "") if isinstance(tc, dict) else ""
    return str(data.get("tool_name") or "")


def _installed(f):
    """The manifest lists it: the kit put it here, so a missing file is a fault (a part not installed is not)."""
    try:
        with open(os.path.join(HERE, "manifest.json"), encoding="utf-8") as fh:
            return ".kit/hooks/" + f in json.load(fh)
    except (OSError, ValueError):
        return False


def _buffer(data=b""):
    """An unlinked file: a hook's stdin, stdout or stderr (a pipe would be held open by what the hook detaches)."""
    if hasattr(os, "memfd_create"):
        fd = os.memfd_create("kit-hook")
    else:
        import tempfile
        fd, path = tempfile.mkstemp()
        os.unlink(path)
    done = 0
    while done < len(data):
        done += os.write(fd, data[done:])
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def _drain(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    out = b""
    while True:
        b = os.read(fd, 1 << 16)
        if not b:
            os.close(fd)
            return out.decode("utf-8", "replace")
        out += b


def _child(path, fds):
    """In the fork: the hook, exactly as `python3 -I <hook>` runs it; never returns."""
    code = 1
    try:
        os.setsid()
        for n, fd in enumerate(fds):
            os.dup2(fd, n)
        import io
        sys.stdin = io.TextIOWrapper(io.BufferedReader(io.FileIO(0, closefd=False)))
        sys.stdout = io.TextIOWrapper(io.BufferedWriter(io.FileIO(1, "w", closefd=False)), encoding=sys.__stdout__.encoding)
        sys.stderr = io.TextIOWrapper(io.BufferedWriter(io.FileIO(2, "w", closefd=False)), encoding=sys.__stdout__.encoding,
                                      errors="backslashreplace", write_through=True)
        import runpy
        sys.argv = [path]
        try:
            runpy.run_path(path, run_name="__main__")
            code = 0
        except SystemExit as e:
            code = 0 if e.code is None else e.code
            if not isinstance(code, int):
                print(code, file=sys.stderr)
                code = 1
        except BaseException:
            import traceback
            traceback.print_exc()
            code = 1
        sys.stdout.flush()
        sys.stderr.flush()
    except BaseException:
        code = code or 1
    finally:
        os._exit(code & 0xFF)


class _Hook:
    """One hook, started; `result()` waits for it: (exit code or None, stdout, stderr, why it failed or "")."""

    def __init__(self, f, raw, seconds):
        self.end = time.monotonic() + seconds
        self.seconds, self.error = seconds, ""
        path = os.path.join(HERE, "hooks", f)
        self.fds = [_buffer(raw), _buffer(), _buffer()]
        if hasattr(os, "fork"):
            self.p = None
            self.pid = os.fork()
            if not self.pid:
                _child(path, self.fds)
            return
        import subprocess                                   # Windows
        try:
            self.p = subprocess.Popen([sys.executable, "-I", path], stdin=self.fds[0], stdout=self.fds[1], stderr=self.fds[2])
        except OSError as e:
            self.p, self.pid, self.error = None, 0, "could not start: %s" % e

    def _poll(self):
        if self.p is not None:
            return self.p.poll()
        pid, st = os.waitpid(self.pid, os.WNOHANG)
        return None if not pid else os.WEXITSTATUS(st) if os.WIFEXITED(st) else -os.WTERMSIG(st)

    def _kill(self):
        if self.p is not None:
            self.p.kill()
            return
        for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 0)):   # first asked: git drops its index.lock on SIGTERM
            try:
                os.killpg(self.pid, sig)                    # and what it started
            except OSError:
                try:
                    os.kill(self.pid, sig)
                except OSError:
                    pass
            end = time.monotonic() + wait
            while sig == signal.SIGTERM and time.monotonic() < end:
                if os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                    time.sleep(0.2)                         # the hook is gone; what it started finishes cleaning up
                    break
                time.sleep(0.02)
        os.waitpid(self.pid, 0)

    def result(self):
        rc, nap = None, 0.001
        while not self.error:
            rc = self._poll()
            if rc is not None:
                break
            if time.monotonic() > self.end:
                self._kill()
                self.error = "no answer in %d s" % self.seconds
            time.sleep(nap)
            nap = min(nap * 1.5, 0.02)
        os.close(self.fds[0])
        out, err = _drain(self.fds[1]), _drain(self.fds[2])
        if self.error:
            return None, "", "", self.error
        if rc not in (0, 2):
            last = (err.strip().splitlines() or ["no message"])[-1]
            return rc, out, err, "exit %d: %s" % (rc, last[:300])
        return rc, out, err, ""


def run(runtime, event, raw):
    """[(hook, guard, exit code, stdout, stderr, why it failed)] of the hooks this event calls for."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        data = None
    tool = _tool(data, runtime)
    todo = []
    for _, on, f, guard, sec in rows(runtime, event):
        if on is not None and tool is not None and not re.fullmatch(on, tool):
            continue
        if os.path.isfile(os.path.join(HERE, "hooks", f)):
            todo.append((f, guard, sec))
        elif _installed(f):
            todo.append((f, guard, None))
    if not todo:
        return []

    def start(t):
        return None if t[2] is None else _Hook(t[0], raw, t[2])

    def end(t, h):
        return (t[0], t[1]) + ((None, "", "", "file missing") if h is None else h.result())
    if runtime == "agy":                                    # agy runs them one after the other
        return [end(t, start(t)) for t in todo]
    import runpy, subprocess  # noqa: E401,F401  (what the hooks import: once here, shared by the forks)
    started = [(t, start(t)) for t in todo]                 # Claude Code runs an event's hooks side by side
    return [end(t, h) for t, h in started]


# ---------------------------------------------------------------- merge

def _refusal(f, why):
    return ("REFUSED: the kit's guard %s did not decide (%s), so this call does not run (a guard that fails never lets a "
            "call through). Tell the owner; the fix is in .kit/hooks/%s, or `install.py . --no-unified-hooks`." % (f, why, f))


def _parse(out):
    try:
        d = json.loads(out)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _join(parts, sep="\n\n"):
    return sep.join(dict.fromkeys(p for p in parts if p))


def merge_claude(event, results):
    """(exit code, stdout, stderr): what Claude Code would have made of these hooks run on their own."""
    blocks, objs, texts, outs, failed = [], [], [], [], []
    for f, guard, rc, out, err, why in results:
        if why and guard:
            blocks.append(_refusal(f, why))
        elif why:
            failed.append("kit hook %s: %s" % (f, why))
        elif rc == 2:
            blocks.append(err.strip() or "blocked by %s" % f)
        elif out.strip():
            outs.append(out)
            d = _parse(out)
            (texts if d is None else objs).append(out.strip() if d is None else d)
    if event not in TEXT_IS_CONTEXT:
        texts = []
    hso = [o["hookSpecificOutput"] for o in objs if isinstance(o.get("hookSpecificOutput"), dict)]
    contexts = texts + [str(h["additionalContext"]) for h in hso if h.get("additionalContext")]
    reasons = [str(o.get("reason") or "") for o in objs if o.get("decision") == "block"]
    ranked = [h for h in hso if h.get("permissionDecision")]
    if blocks:
        denied = [str(h.get("permissionDecisionReason") or "") for h in ranked if h["permissionDecision"] == "deny"]
        return 2, "", _join(blocks + denied + reasons + contexts + failed)
    if failed and not outs:
        return 1, "", _join(failed)
    if len(outs) == 1 and not failed:
        return 0, outs[0], ""
    if outs and not objs and not failed:
        return 0, _join(texts), ""
    res, spec = {}, {}
    for o in objs:
        for k, v in o.items():
            if k != "hookSpecificOutput":
                res.setdefault(k, v)
    if any(o.get("continue") is False for o in objs):
        res["continue"] = False
    elif "continue" in res:
        res["continue"] = True
    if reasons:
        res.update(decision="block", reason=_join(reasons))
    msgs = [str(o["systemMessage"]) for o in objs if o.get("systemMessage")] + failed
    if msgs:
        res["systemMessage"] = _join(msgs)
    for h in hso:
        for k, v in h.items():
            spec.setdefault(k, v)
    if ranked:
        top = max((h["permissionDecision"] for h in ranked), key=("allow", "ask", "deny").index)
        spec["permissionDecision"] = top
        why = _join(str(h.get("permissionDecisionReason") or "") for h in ranked if h["permissionDecision"] == top)
        spec.pop("permissionDecisionReason", None)
        if why:
            spec["permissionDecisionReason"] = why
    if contexts:
        spec["additionalContext"] = _join(contexts)
    if spec:
        res["hookSpecificOutput"] = dict(spec, hookEventName=event)
    return 0, json.dumps(res, ensure_ascii=False) if res else "", ""


AGY_INJECT_EVENTS = ("SessionStart", "PreInvocation", "PostInvocation")


def merge_agy(event, results):
    """(exit code, stdout, stderr) in agy's contract: one JSON object matching the event's protojson schema.
    CL44 (item 9): agy rejects `injectSteps` on PreToolUse/PostToolUse/Stop via protojson; only emit `injectSteps`
    on events whose schema accepts it (SessionStart, PreInvocation, PostInvocation)."""
    objs, errs, rc = [], [], 0
    for f, guard, code, out, err, why in results:
        if why and guard:
            objs.append({"decision": "deny", "reason": _refusal(f, why)})
        elif why:
            errs.append("kit hook %s: %s" % (f, why))
        else:
            if code == 2:
                rc = 2
                errs.append(err.strip() or "blocked by %s" % f)
            d = _parse(out) if out.strip() else None
            if d is not None:
                objs.append(d)
    if not objs:
        return rc, "", _join(errs)
    res = {}
    for o in objs:
        for k, v in o.items():
            if k not in ("decision", "reason", "injectSteps"):
                res.setdefault(k, v)
    order = ("allow", "continue", "block", "deny")
    said = [o for o in objs if o.get("decision") in order]
    if said:
        res["decision"] = max((o["decision"] for o in said), key=order.index)
        why = _join(str(o.get("reason") or "") for o in said if o["decision"] != "allow")
        if why:
            res["reason"] = why
    if event in AGY_INJECT_EVENTS:
        steps = [s for o in objs for s in (o.get("injectSteps") or [])]
        if steps:
            res["injectSteps"] = steps
    return rc, json.dumps(res, ensure_ascii=False), _join(errs)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    runtime, event = (argv + ["", ""])[:2]
    raw = sys.stdin.buffer.read()
    try:
        if runtime not in RUNTIMES:
            raise ValueError("unknown runtime %r (%s)" % (runtime, ", ".join(RUNTIMES)))
        rc, out, err = (merge_agy if runtime == "agy" else merge_claude)(event, run(runtime, event, raw))
    except Exception as e:                        # the dispatcher itself: refuse a tool call, block nothing else
        msg = "kit hook.py failed on %s: %s: %s" % (event, type(e).__name__, e)
        if event in REFUSES and runtime == "agy":
            rc, out, err = 0, json.dumps({"decision": "deny", "reason": _refusal("hook.py", msg)}), ""
        else:
            rc, out, err = (2 if event in REFUSES else 1), "", (_refusal("hook.py", msg) if event in REFUSES else msg)
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
