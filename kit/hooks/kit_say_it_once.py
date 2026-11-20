#!/usr/bin/env python3
"""Say it once: a session's result lives in its board post, not in three copies (origin: anlonlt/harmonize say_it_once.py).

One script, three events (Claude Code passes `hook_event_name`):
  PreToolUse on send_message / SendMessage: a message over `kit.limits.message_chars` (a "Done." pointer: over
      `done_message_chars`) is refused (exit 2): the detail goes in the post. The clerk's verbatim forward of the
      owner's words ("Board comment from the owner ...") is never cut.
  UserPromptSubmit: the owner typed in this chat: answer in at most `chat_answer_chars`; the detail goes in the post.
  PostToolUse on ArtifactData / send_message: the result is on the board or sent: end the turn with a single "·".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import limit  # noqa: E402

FORWARD = "Board comment from the owner"


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
        context(ev, "SAY IT ONCE: the owner typed this in your chat: answer what was asked in at most %d characters; "
                    "the detail goes in your board post." % limit("chat_answer_chars", 600))
        return 0
    if ev == "PostToolUse":
        context(ev, "SAY IT ONCE: it is on the board or sent: if you end your turn now, end it with a single \"·\" "
                    "and nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
