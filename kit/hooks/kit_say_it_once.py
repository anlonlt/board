#!/usr/bin/env python3
"""Say it once: a session's result lives in its board post, not in three copies (origin: anlonlt/harmonize say_it_once.py).

One script, three events (Claude Code passes `hook_event_name`):
  PreToolUse on send_message / SendMessage: a message over `kit.limits.message_chars` (a "Done." pointer: over
      `done_message_chars`) is refused (exit 2): the detail goes in the post. The clerk's verbatim forward of the
      owner's words ("Board comment from the owner ...") is never cut.
  UserPromptSubmit: the owner typed in this chat: answer in at most `chat_answer_chars`; the detail goes in the post.
  PostToolUse on ArtifactData / send_message: the result is on the board or sent: end the turn with a single "·".
  Stop: the closing chat message is checked, not only reminded (Harmonize's rule, the owner 2026-11-13/14): when the
      owner typed the turn, at most `chat_answer_chars`; when a session's message, a timer or a hook opened it, a
      single "·". A message that breaks it is BLOCKED ONCE per turn (the retry must be "·"); after that it passes.
      Every reminder names the owner's language (`language.talk`).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, limit  # noqa: E402

FORWARD = "Board comment from the owner"


def talk():
    return config().get("language", {}).get("talk") or "the owner's language"


def closing_text(path):
    """The assistant's last message: its text blocks after the last user entry with text, and after its last tool call."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                kind, content = e.get("type"), (e.get("message") or {}).get("content")
                if kind == "user":
                    if isinstance(content, str) or any(isinstance(c, dict) and c.get("type") == "text" for c in content or []):
                        out = []
                elif kind == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            out.append(c.get("text") or "")
                        elif isinstance(c, dict) and c.get("type") == "tool_use":
                            out = []
    except OSError:
        return ""
    return "\n".join(out)


def turn_opener(path):
    """(id, typed by the owner) of the entry that opened the turn: the last user entry with text, not a hook's."""
    opener, human = "", True
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") != "user" or e.get("isMeta"):
                    continue
                c = (e.get("message") or {}).get("content")
                if isinstance(c, list):
                    texts = [x.get("text") or "" for x in c if isinstance(x, dict) and x.get("type") == "text"]
                    if not texts:
                        continue
                    c = "\n".join(texts)
                c = str(c or "")
                kind = (e.get("origin") or {}).get("kind")
                opener = str(e.get("uuid") or n)
                human = (kind == "human") if kind else not (c.lstrip().startswith("<task-notification>")
                                                             or "SYSTEM NOTIFICATION" in c[:300] or "<cross-session" in c[:300]
                                                             or c.startswith(FORWARD))
    except OSError:
        pass
    return opener, human


def stop_verdict(text, human):
    t = text.strip()
    if len(t) <= 3:
        return None
    if human:
        cap = limit("chat_answer_chars", 600)
        if len(t) <= cap:
            return None
        return ("SAY IT ONCE: your answer to the owner is %d characters (limit %d), in %s. Answer what was asked in a "
                "few lines; the detail goes in your board post." % (len(t), cap, talk()))
    return ("SAY IT ONCE: the owner did not write this turn (a session's message, a timer, a hook), and your closing "
            "message is %d characters. The result is in your board post: end the turn with a single \"·\"." % len(t))


def context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    ev = data.get("hook_event_name", "")
    if ev == "PreToolUse":
        ti = data.get("tool_input") or {}
        msg = ti.get("message") or ""
        if not isinstance(msg, str) or msg.startswith(FORWARD):
            return 0
        cap = limit("done_message_chars", 240) if msg.lstrip().startswith("Done.") else limit("message_chars", 600)
        if len(msg) > cap:
            print("REFUSED: this message is %d characters (limit %d). Say it once: put the detail in your board post "
                  "and send a pointer (\"Done. Check post <id>.\")." % (len(msg), cap), file=sys.stderr)
            return 2
        return 0
    if ev == "UserPromptSubmit":
        p = data.get("prompt") or ""
        if p.startswith(FORWARD) or "<cross-session" in p[:200] or "SYSTEM NOTIFICATION" in p[:200]:
            return 0
        context(ev, "SAY IT ONCE: the owner typed this in your chat: answer what was asked in at most %d characters, "
                    "in %s; the detail goes in your board post." % (limit("chat_answer_chars", 600), talk()))
        return 0
    if ev == "Stop":
        tp = str(data.get("transcript_path") or "")
        opener, human = turn_opener(tp)
        reason = stop_verdict(closing_text(tp), human)
        if reason is None:
            return 0
        mark = os.path.join(tempfile.gettempdir(), "kit-say-%s" % (data.get("session_id") or "x"))
        try:
            seen = open(mark, encoding="utf-8").read()
        except OSError:
            seen = ""
        if seen == opener:
            return 0  # blocked once this turn already: let it through, no loop
        try:
            open(mark, "w", encoding="utf-8").write(opener)
        except OSError:
            pass
        print(reason + "\nYOUR RETRY: a single \"·\" (the detail is already read; put anything missing in the post).",
              file=sys.stderr)
        return 2
    if ev == "PostToolUse":
        context(ev, "SAY IT ONCE: it is on the board or sent: if you end your turn now, end it with a single \"·\" "
                    "and nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
