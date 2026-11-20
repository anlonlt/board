#!/usr/bin/env python3
"""Say it once (agy): a session's result lives in its board post, not in three copies.

One script, five agy events (dispatched by `hookEventName`):
  PreToolUse (matcher: send_message): a message over `kit.limits.message_chars` (a "Done." pointer: over
      `done_message_chars`) is denied: the detail goes in the post. The clerk's verbatim forward of the
      owner's words ("Board comment from the owner ...") is never cut.
  PreInvocation: when the owner typed a new prompt, injects a systemMessage reminder (once per prompt)
      to answer in at most `chat_answer_chars`, in `language.talk`.
  PostToolUse (matcher: send_message|run_command): records a marker when `send_message` or a `board_cli.py`
      write (set/update/str-replace/batch) runs; outputs `{}` per agy's PostToolUse contract.
  PostInvocation: if PostToolUse recorded a board write or send_message, injects the reminder to end
      the turn with a single "·".
  Stop: parses agy's transcript.jsonl (`USER_INPUT` / `PLANNER_RESPONSE`) to enforce `chat_answer_chars`
      (human turn) or a single "·" (non-human turn), blocking ONCE per turn via `{"decision": "continue"}`.
"""
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, in_setup, limit  # noqa: E402

FORWARD = "Board comment from the owner"
BOARD_WRITE_RE = re.compile(r"board_cli\.py\b.*\b(set|update|str-replace|batch)\b")


def talk(data):
    return config(data).get("language", {}).get("talk") or "the owner's language"


def closing_text(path):
    """The assistant's last message in agy's transcript.jsonl: text after the last USER_INPUT and last tool call."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                kind = e.get("type")
                if kind == "USER_INPUT":
                    out = []
                elif kind == "PLANNER_RESPONSE":
                    if e.get("tool_calls"):
                        out = []
                    txt = e.get("content") or ""
                    if txt:
                        out.append(txt)
    except OSError:
        return ""
    return "\n".join(out)


def turn_opener(path):
    """(id, typed by the owner) of the last USER_INPUT in agy's transcript.jsonl."""
    opener, human = "", True
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") != "USER_INPUT":
                    continue
                c = str(e.get("content") or "")
                src = str(e.get("source") or "")
                opener = str(e.get("step_index", n))
                if src and src != "USER_EXPLICIT":
                    human = False
                else:
                    human = not (
                        c.lstrip().startswith("<task-notification>")
                        or "SYSTEM NOTIFICATION" in c[:300]
                        or "<cross-session" in c[:300]
                        or c.startswith(FORWARD)
                    )
    except OSError:
        pass
    return opener, human


def stop_verdict(text, human, data):
    t = text.strip()
    if len(t) <= 3:
        return None
    if human:
        cap = limit("chat_answer_chars", 600, data)
        if len(t) <= cap:
            return None
        return ("SAY IT ONCE: your answer to the owner is %d characters (limit %d), in %s. Answer what was asked in a "
                "few lines; the detail goes in your board post." % (len(t), cap, talk(data)))
    return ("SAY IT ONCE: the owner did not write this turn (a session's message, a timer, a hook), and your closing "
            "message is %d characters. The result is in your board post: end the turn with a single \"·\"." % len(t))


def inject(text):
    print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    ev = data.get("hookEventName", "")
    cid = str(data.get("conversationId") or "x")

    if in_setup(data) and ev in ("PreInvocation", "PostToolUse", "PostInvocation", "Stop"):
        if ev == "Stop":
            print(json.dumps({"decision": "allow"}))
        else:
            print("{}")
        return 0

    if ev == "PreToolUse":
        tc = data.get("toolCall") or {}
        args = tc.get("args") or {}
        msg = args.get("Message") or args.get("message") or ""
        if not isinstance(msg, str) or msg.startswith(FORWARD):
            print(json.dumps({"decision": "allow"}))
            return 0
        cap = limit("done_message_chars", 240, data) if msg.lstrip().startswith("Done.") else limit("message_chars", 600, data)
        if len(msg) > cap:
            reason = ("REFUSED: this message is %d characters (limit %d). Say it once: put the detail in your board post "
                      "and send a pointer (\"Done. Check post <id>.\")." % (len(msg), cap))
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
            return 0
        print(json.dumps({"decision": "allow"}))
        return 0

    if ev == "PreInvocation":
        p = str(data.get("lastUserInput") or "")
        if not p or p.startswith(FORWARD) or "<cross-session" in p[:200] or "SYSTEM NOTIFICATION" in p[:200]:
            print("{}")
            return 0
        h = hashlib.sha256(p.encode("utf-8")).hexdigest()[:16]
        mark = os.path.join(tempfile.gettempdir(), "kit-prompt-%s" % cid)
        try:
            if open(mark, encoding="utf-8").read() == h:
                print("{}")
                return 0
        except OSError:
            pass
        try:
            open(mark, "w", encoding="utf-8").write(h)
        except OSError:
            pass
        inject("SAY IT ONCE: the owner typed this in your chat: answer what was asked in at most %d characters, "
               "in %s; the detail goes in your board post." % (limit("chat_answer_chars", 600, data), talk(data)))
        return 0

    if ev == "PostToolUse":
        tc = data.get("toolCall") or {}
        name = tc.get("name") or ""
        args = tc.get("args") or {}
        cmd = str(args.get("CommandLine") or "")
        if name == "send_message" or (name == "run_command" and BOARD_WRITE_RE.search(cmd)):
            mark = os.path.join(tempfile.gettempdir(), "kit-post-%s" % cid)
            try:
                open(mark, "w", encoding="utf-8").write(str(data.get("stepIdx", "1")))
            except OSError:
                pass
        print("{}")
        return 0

    if ev == "PostInvocation":
        mark = os.path.join(tempfile.gettempdir(), "kit-post-%s" % cid)
        if os.path.exists(mark):
            try:
                os.remove(mark)
            except OSError:
                pass
            inject("SAY IT ONCE: it is on the board or sent: if you end your turn now, end it with a single \"·\" "
                   "and nothing else.")
            return 0
        print("{}")
        return 0

    if ev == "Stop":
        term = str(data.get("terminationReason") or "NO_TOOL_CALL")
        if term not in ("", "NO_TOOL_CALL"):
            print(json.dumps({"decision": "allow"}))
            return 0
        tp = str(data.get("transcriptPath") or "")
        opener, human = turn_opener(tp)
        reason = stop_verdict(closing_text(tp), human, data)
        if reason is None:
            print(json.dumps({"decision": "allow"}))
            return 0
        mark = os.path.join(tempfile.gettempdir(), "kit-say-%s" % cid)
        try:
            seen = open(mark, encoding="utf-8").read()
        except OSError:
            seen = ""
        if seen == opener:
            print(json.dumps({"decision": "allow"}))
            return 0
        try:
            open(mark, "w", encoding="utf-8").write(opener)
        except OSError:
            pass
        full = reason + "\nYOUR RETRY: a single \"·\" (the detail is already read; put anything missing in the post)."
        print(json.dumps({"decision": "continue", "reason": full}, ensure_ascii=False))
        return 0

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
