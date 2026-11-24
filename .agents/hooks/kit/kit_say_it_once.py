#!/usr/bin/env python3
"""Say it once (agy): a session's result lives in its board post, not in three copies.

One script, five agy events (dispatched by `hookEventName`):
  PreToolUse (matcher: send_message; Claude Code: SendMessage and mcp__claude-code-remote__send_message): a message
      over `kit.limits.message_chars` (a "Done." pointer: over `done_message_chars`), never more than 200 (CL38), or
      one holding a table, a quote or a code block, is denied: the detail goes in the post. The clerk's verbatim forward of the
      owner's words ("Board comment from the owner ...") is never cut.
  PreInvocation: when the owner typed a new prompt, injects a systemMessage reminder (once per prompt)
      to answer in at most `chat_answer_chars`, in `language.talk`.
  PostToolUse (matcher: send_message|run_command; Claude Code: SendMessage|Bash|ArtifactData): records a marker when `send_message` or a `board_cli.py`
      write (set/update/str-replace/batch) runs; outputs `{}` per agy's PostToolUse contract.
  PostInvocation: if PostToolUse recorded a board write or send_message, injects the reminder to end
      the turn with a single "·" (only on a turn the owner did not type: his own chat always gets words).
  Stop: parses agy's transcript.jsonl (`USER_INPUT` / `PLANNER_RESPONSE`) to enforce `chat_answer_chars`
      only on a non-human turn (a single "·" alone; text then "·" fails), blocking ONCE per turn via
      `{"decision": "continue"}`. Every turn of a background session (KIT_ROLE other than orchestrator: the clerk, a
      part, a consultant, the cards agent, a review) is non-human, and its "·" rule holds even while kit.setup is true. An answer to
      the owner is never retried: it is already on his screen; the limit is said before his turn starts.
"""
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, in_setup, is_claude_payload, limit  # noqa: E402

FORWARD = "Board comment from the owner"
MESSAGE_CEILING = 200
TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
QUOTE_RE = re.compile(r"^\s*>", re.M)


def message_shape(msg):
    """What a message between sessions must not carry (CL38): a table, a quoted block or a code block."""
    if TABLE_RE.search(msg):
        return "a table"
    if QUOTE_RE.search(msg):
        return "a quote"
    if "```" in msg:
        return "a code block"
    return ""
# Sessions the kit launches in the background (the clerk, parts, consultants, the cards agent, reviews): the owner never
# types in their chat, even though Claude Code marks their launch prompt with origin "human". Only the orchestrator
# (and a session started by hand, no KIT_ROLE) has the owner typing.
FOREGROUND_ROLES = ("", "orchestrator")
BOARD_WRITE_RE = re.compile(r"board_cli\.py\b.*\b(set|update|str-replace|batch)\b")


def talk(data):
    return config(data).get("language", {}).get("talk") or "the owner's language"


def closing_text(path):
    """The assistant's last message in agy or Claude Code transcript.jsonl: text after the last user entry and last tool call."""
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
                elif kind == "user":
                    content = (e.get("message") or {}).get("content")
                    if isinstance(content, str) or any(isinstance(c, dict) and c.get("type") == "text" for c in content or []):
                        out = []
                elif kind == "assistant":
                    content = (e.get("message") or {}).get("content")
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                out.append(c.get("text") or "")
                            elif isinstance(c, dict) and c.get("type") == "tool_use":
                                out = []
    except OSError:
        return ""
    return "\n".join(out)


def turn_opener(path):
    """(id, typed by the owner) of the last user entry in agy or Claude Code transcript.jsonl."""
    opener, human = "", True
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                kind = e.get("type")
                if kind == "USER_INPUT":
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
                elif kind == "user" and not e.get("isMeta"):
                    c = (e.get("message") or {}).get("content")
                    if isinstance(c, list):
                        texts = [x.get("text") or "" for x in c if isinstance(x, dict) and x.get("type") == "text"]
                        if not texts:
                            continue
                        c = "\n".join(texts)
                    c = str(c or "")
                    okind = (e.get("origin") or {}).get("kind")
                    opener = str(e.get("uuid") or n)
                    human = (okind == "human") if okind else not (
                        c.lstrip().startswith("<task-notification>")
                        or "SYSTEM NOTIFICATION" in c[:300]
                        or "<cross-session" in c[:300]
                        or c.startswith(FORWARD)
                    )
    except OSError:
        pass
    return opener, human


def background():
    """True in a session the kit launched in the background (KIT_ROLE clerk, part, consultant, cards, review...)."""
    return os.environ.get("KIT_ROLE", "").strip().lower() not in FOREGROUND_ROLES


LAUNCH_RE = re.compile(r"^\s*Your prompt is \S+: read it and (do|follow) it\.?\s*$")


def not_typed(prompt):
    """CL26: a turn the owner did not type, judged from its opening prompt alone (before the turn): a background
    session's, the clerk's forward, another session's message, a timer or task notice, or the kit's launch line (a web
    part, started by create_session, has no KIT_ROLE to tell it is in the background)."""
    head = prompt.lstrip()[:400]
    return (background() or prompt.startswith(FORWARD) or "<cross-session" in head or "SYSTEM NOTIFICATION" in head
            or head.startswith("<task-notification>") or bool(LAUNCH_RE.match(prompt)))


def owner_typed(path):
    """True only when the transcript shows the owner typed this turn (no transcript: unknown, so False)."""
    if background():
        return False
    return bool(path) and os.path.isfile(path) and turn_opener(path)[1]


def chat_verbose(data):
    if os.environ.get("KIT_CHAT_VERBOSE", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(config(data).get("kit", {}).get("limits", {}).get("chat_verbose"))


def stop_verdict(text, human, data):
    t = text.strip()
    if t in ("", "\u00b7"):
        return None                              # nothing, or the single "·" alone: the only closing that passes
    if human:
        # The owner has already read it: a Stop hook runs after the answer is on his screen, so a retry only sends
        # it a second time (his 2026-11-23 report). The limit is said BEFORE the turn (UserPromptSubmit) instead.
        return None
    return ("SAY IT ONCE: the owner did not write this turn (a session's message, a timer, a hook), and your closing "
            "message is %d characters. The result is in your board post: end the turn with a single \"·\"." % len(t))


def inject(text):
    print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": text}}]}, ensure_ascii=False))


def claude_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    claude = is_claude_payload(data)
    ev = data.get("hookEventName") or data.get("hook_event_name") or ""
    cid = str(data.get("conversationId") or data.get("session_id") or "x")

    # Setup turns the checks off, except the "·" rule of a background session (the clerk talked unchecked in setup).
    if in_setup(data) and not (background() and ev == "Stop") and ev in ("PreInvocation", "UserPromptSubmit", "PostToolUse", "PostInvocation", "Stop"):
        if claude:
            return 0
        if ev == "Stop":
            print(json.dumps({"decision": "allow"}))
        else:
            print("{}")
        return 0

    if ev == "PreToolUse":
        tc = data.get("toolCall") or {}
        args = tc.get("args") or {}
        ti = data.get("tool_input") or {}
        msg = args.get("Message") or args.get("message") or ti.get("message") or ""
        if not isinstance(msg, str) or msg.startswith(FORWARD):
            if not claude:
                print(json.dumps({"decision": "allow"}))
            return 0
        # CL38 (the owner, 02:14: messages between sessions wasted his usage): every message between sessions is a
        # pointer, at most MESSAGE_CEILING characters whatever the config says, and never a table or a quote: the
        # detail is said once, in a post or a comment, and the message names where.
        cap = min(limit("done_message_chars", 240, data) if msg.lstrip().startswith("Done.") else limit("message_chars", 600, data),
                  MESSAGE_CEILING)
        shape = message_shape(msg)
        if len(msg) > cap or shape:
            reason = ("REFUSED: this message %s. Say it once: put the detail in a board post or comment and send a "
                      "pointer (\"Done. Check post <id>.\", \"Gaps under your post.\")." % (
                          ("holds %s" % shape) if shape else "is %d characters (limit %d)" % (len(msg), cap)))
            if claude:
                print(reason, file=sys.stderr)
                return 2
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
            return 0
        if not claude:
            print(json.dumps({"decision": "allow"}))
        return 0

    if ev in ("PreInvocation", "UserPromptSubmit"):
        p = str(data.get("lastUserInput") or data.get("prompt") or "")
        cap = limit("chat_answer_chars", 600, data)
        if p and not_typed(p):
            # CL26: neither platform has a hook between the model's text and the screen (Claude Code: Stop and
            # SubagentStop run after the closing text is shown; agy: PostInvocation and Stop likewise), so a Stop
            # block always comes after a long relay was read. The earliest the rule can act is here, before the turn.
            rule = ("SAY IT ONCE: the owner did not type this turn (a session's message, a timer, a launch): end it "
                    "with a single \"\u00b7\" and nothing else; the result goes in your board post.")
            if claude and ev == "UserPromptSubmit":
                claude_context(ev, rule)
            else:
                inject(rule)
            return 0
        if not p or chat_verbose(data) or cap <= 0:
            if not claude:
                print("{}")
            return 0
        reminder = ("SAY IT ONCE: the owner typed this in your chat: answer what was asked in at most %d characters, "
                    "in %s; the detail goes in your board post." % (cap, talk(data)))
        if claude and ev == "UserPromptSubmit":
            claude_context(ev, reminder)
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
        inject(reminder)
        return 0

    if ev == "PostToolUse":
        if claude and "toolCall" not in data:
            # Claude Code CLI (--runtime local): Bash fires on every command; only a board_cli.py write counts.
            if data.get("tool_name") == "Bash" and not BOARD_WRITE_RE.search(str((data.get("tool_input") or {}).get("command") or "")):
                return 0
            if owner_typed(str(data.get("transcript_path") or "")):
                return 0                         # the owner typed this turn: he gets a short answer, not "·"
            claude_context(ev, "SAY IT ONCE: it is on the board or sent: if you end your turn now, end it with a single \"·\" "
                               "and nothing else.")
            return 0
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
            if owner_typed(str(data.get("transcriptPath") or data.get("transcript_path") or "")):
                print("{}")                      # the owner typed this turn: he gets a short answer, not "·"
                return 0
            inject("SAY IT ONCE: it is on the board or sent: if you end your turn now, end it with a single \"·\" "
                   "and nothing else.")
            return 0
        print("{}")
        return 0

    if ev == "Stop":
        term = str(data.get("terminationReason") or "NO_TOOL_CALL")
        if term not in ("", "NO_TOOL_CALL"):
            if not claude:
                print(json.dumps({"decision": "allow"}))
            return 0
        tp = str(data.get("transcriptPath") or data.get("transcript_path") or "")
        opener, human = turn_opener(tp)
        human = human and not background()
        reason = stop_verdict(closing_text(tp), human, data)
        if reason is None:
            if not claude:
                print(json.dumps({"decision": "allow"}))
            return 0
        mark = os.path.join(tempfile.gettempdir(), "kit-say-%s" % cid)
        try:
            seen = open(mark, encoding="utf-8").read()
        except OSError:
            seen = ""
        if seen == opener:
            if not claude:
                print(json.dumps({"decision": "allow"}))
            return 0
        try:
            open(mark, "w", encoding="utf-8").write(opener)
        except OSError:
            pass
        if human:
            full = reason + ("\nYOUR RETRY: the same answer to the owner, shorter (at most %d characters), in words; never "
                             "a lone \"·\" to him." % limit("chat_answer_chars", 600, data))
        else:
            full = reason + "\nYOUR RETRY: a single \"·\" (the detail is already read; put anything missing in the post)."
        if claude:
            print(full, file=sys.stderr)
            return 2
        print(json.dumps({"decision": "continue", "reason": full}, ensure_ascii=False))
        return 0

    if not claude:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
