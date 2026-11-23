#!/usr/bin/env python3
"""SAY IT ONCE — a result is written in one place: the board post (docs/BOARD.md).

The owner, 2026-11-12: "a hook that basically makes sure agents aren't spending triple tokens, replying
in chat, to you, and in the board [...] So that everyone just replies on the board".

A hook cannot take back tokens already written; it can make the second and third copies short:
  · PreToolUse on send_message: a message to another session over MESSAGE_MAX characters is refused
    (the orchestrator reads the post; the message is "Done. Check post <id>." or a short question).
    Not refused: the board clerk's forward of the owner's words (never shortened), an orchestrator's own
    messages (it carries his words and prompts to the parts).
    Before the session's first board write the limit is BEFORE_POST_MAX (part M1, 2026-11-12: turn-1 reports
    went out as messages, in several pieces, with no post): the post comes first, the message points to it.
  · PostToolUse on send_message and on a board write: one line of context, "the chat now says ONE line", so the
    rule arrives before the closing message is written (Stop can only speak after).
  · Stop: a closing chat message that breaks the rule is BLOCKED ONCE a turn, and the retry may only be "·" (the
    owner, 2026-11-14 10:33); a retry that is not "·" is recorded as a miss, never blocked again (09:52: a block
    three times a turn made three messages out of one; 09:52-10:33 no block at all let every summary through).
    An orchestrator (branch orchestrator-*) is bound too, at EVERY stop (the owner, 2026-11-12 19:25); only its
    messages to the parts stay free (they carry his words and prompts).
    The owner, 2026-11-13 03:52 ("Why are you writing so much ?", then "Fix the hook so that it actually works"):
    a turn the owner did not type (a session's message, a timer) ends with NO text for an orchestrator, unless it
    opens with "Done and checked" or "Broken" in at most three short lines; the block holds on the retry too
    (up to ORCH_MAX_BLOCKS a turn, so a session can never loop), and the rule is given before the stop.
Its test: python3 -I .claude/hooks/test_say_it_once.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

MESSAGE_MAX = 600
BEFORE_POST_MAX = 240  # until the session has written to the board: a pointer or a short question, no report
BOARD_WRITES = ("set", "update", "batch", "str_replace")
CHAT_MAX = 600
CLERK_PREFIX = "Board comment from the owner"
ORCH_ALLOWED_OPENERS = ("Done and checked", "Broken")  # what may reach him on a turn he did not type
ORCH_NOTICE_MAX = 300
ORCH_SILENT_MAX = 1  # a single "·"
ORCH_NOTICE_LINES = 3
ORCH_MAX_BLOCKS = 3  # per turn: misses recorded at most this often (it used to cap the blocks)
MISS_MARK = "SAY-IT-ONCE MISS:"
TYPED_MAX = 500  # a question he typed in a chat; a launch prompt is longer
# The owner, 2026-11-16, after a test page's link and a delete question in chat: "Why wasn't this a comment on the
# post ???", then: "No, not also, that goes against the whole purpose of say it once !!!! If it's anything I have to
# wait for and can be sent as a link, it should be in the board, only the board. The . hook is still supposed to
# apply. What happened to it ?" On a turn he typed, a link or an ask blocks, board write or not; "·" passes.
BOARD_URL_ID = "XXXXXXXXXXXXXXXXXXXXXX"
ARTIFACT_LINK = re.compile(r"claude\.ai/(?:code/)?artifact/([A-Za-z0-9_-]+)")
ASKS_HIM = re.compile(
    r"\b(?:should i|shall i|do you want|would you like|want me to|can you|could you|would you|please|let me know|"
    r"tell me (?:if|whether|which|when)|your call|up to you|you (?:decide|confirm|choose|pick)|"
    r"(?:test|try|listen to|rate|check|open|review|approve|delete) (?:it|them|this|these|the)|"
    r"(?:wait|waiting) for|once you(?:'ve| have)|when you(?:'ve| have a| can| get))\b", re.I)
ACT_ON_IT = ("SAY IT ONCE — what he must act on or wait for goes on the board only, under its post (docs/BOARD.md); "
             "chat ends with a single '·'.")


def asks_or_links(text):
    """True when a closing message to the owner holds an artifact link (not the board's own) or asks him to act."""
    if any(m != BOARD_URL_ID for m in ARTIFACT_LINK.findall(text)):
        return True
    return bool(ASKS_HIM.search(text))


def is_orchestrator():
    if os.environ.get("HARMONIZE_ROLE") == "orchestrator":
        return True
    try:
        b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=5,
                           cwd=os.environ.get("CLAUDE_PROJECT_DIR") or ".").stdout.strip()
    except Exception:
        return False
    return b.startswith("orchestrator-")


def posted_marker(data):
    return os.path.join(tempfile.gettempdir(), "harmonize-posted-" + str(data.get("session_id") or "x"))


def clerk_marker(data):
    """Written at the clerk's first forward; stop.sh reads it too (a clerk commits nothing: no save point)."""
    return os.path.join(tempfile.gettempdir(), "harmonize-clerk-" + str(data.get("session_id") or "x"))


def is_clerk(data):
    """The clerk's prompt has it `touch .git/harmonize-clerk` at set-up; else its first forward marks it."""
    return os.path.exists(clerk_marker(data)) or \
        os.path.exists(os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or ".", ".git", "harmonize-clerk"))


def closing_text(path):
    """The text of the assistant's last message: its text blocks after the last user entry."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                kind = e.get("type")
                content = (e.get("message") or {}).get("content")
                if kind == "user":
                    # a tool result is a user entry too: it does not end the closing message's turn
                    if isinstance(content, str) or any(isinstance(c, dict) and c.get("type") == "text" for c in content or []):
                        out = []
                elif kind == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            out.append(c.get("text") or "")
                        elif isinstance(c, dict) and c.get("type") == "tool_use":
                            out = []  # text before a tool call is a progress note, not the closing message
    except OSError:
        return ""
    return "\n".join(out)


def turn_opener(path):
    """(id, typed_by_owner) of the entry that opened the current turn: the last user entry with text that is
    not a hook's feedback (isMeta). A message from a session or a timer arrives as a <task-notification> with
    origin kind other than "human"."""
    opener, human, size = "", True, 0
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                a = e.get("attachment") or {}
                if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
                    # His words typed while a turn ran (the owner, 2026-11-13 08:42: answered with "·" because
                    # the turn had been opened by a session's message): from here the turn is his.
                    opener, human, size = str(a.get("source_uuid") or n), True, len(str(a.get("prompt") or ""))
                    continue
                if e.get("type") != "user" or e.get("isMeta"):
                    continue
                c = (e.get("message") or {}).get("content")
                if isinstance(c, list):
                    texts = [x.get("text") or "" for x in c if isinstance(x, dict) and x.get("type") == "text"]
                    if not texts:
                        continue  # a tool result
                    c = "\n".join(texts)
                c = str(c or "")
                kind = (e.get("origin") or {}).get("kind")
                opener, size = str(e.get("uuid") or n), len(c)
                human = (kind == "human") if kind else not (
                    c.lstrip().startswith("<task-notification>") or "[SYSTEM NOTIFICATION" in c)
    except OSError:
        pass
    return opener, human, size


def orchestrator_verdict(text, human):
    """None when the closing message may stand, else the reason to block."""
    if human:
        if len(text.strip()) > ORCH_SILENT_MAX and asks_or_links(text):
            return ACT_ON_IT
        if len(text) <= CHAT_MAX:
            return None
        return (f"SAY IT ONCE — your message to the owner is {len(text)} characters (limit {CHAT_MAX}). Answer what he "
                "asked in a few lines; the detail goes on the board.")
    t = text.strip()
    if len(t) <= ORCH_SILENT_MAX:
        return None  # "·": the app asks again when a turn shows nothing; a dot is the silence it accepts
    lines = [x for x in t.splitlines() if x.strip()]
    if t.startswith(ORCH_ALLOWED_OPENERS) and len(t) <= ORCH_NOTICE_MAX and len(lines) <= ORCH_NOTICE_LINES:
        return None
    return (f"SAY IT ONCE — the owner did not write this turn (a session's message or a timer), and your closing "
            f"message is {len(t)} characters. End the turn with a single \"·\" and nothing else: no relay note, no 'forwarded', no "
            f"summary. The only exceptions are 'Done and checked: …' or 'Broken: …', in at most {ORCH_NOTICE_LINES} "
            f"lines and {ORCH_NOTICE_MAX} characters (the owner, 2026-11-13: \"Why are you writing so much ?\").")


def part_verdict(text, asked_in_chat, clerk):
    """A work session or the clerk (the owner, 2026-11-13 04:2x: "I like how you limit yourself to . Could every
    discussion do that, since they all use the board ?"): every turn ends with "·", the result being on the board.
    Only a question he typed in that chat (not a launch prompt: at most TYPED_MAX characters) gets an answer there."""
    t = text.strip()
    if len(t) <= ORCH_SILENT_MAX:
        return None
    if asked_in_chat and not clerk and asks_or_links(t):
        return ACT_ON_IT
    if asked_in_chat and not clerk and len(t) <= CHAT_MAX:
        return None
    if asked_in_chat and not clerk:
        return (f"SAY IT ONCE — your answer is {len(t)} characters (limit {CHAT_MAX}). Answer what he asked in a few "
                "lines; the detail goes on the board.")
    return (f"SAY IT ONCE — your closing message is {len(t)} characters. Every Harmonize session ends its turn with a "
            "single \"·\" and nothing else: the result is in your board post, the orchestrator has your pointer. No "
            "\"Posted: …\", no summary, no answer to a hook (the owner, 2026-11-13).")


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    event = data.get("hook_event_name")
    orchestrator = is_orchestrator()
    if orchestrator and event == "PostToolUse" and str(data.get("tool_name") or "").endswith("create_session"):
        # The owner, 2026-11-13 05:5x: Z3 stopped after its turn 1 and nobody noticed. Part M1e (2026-11-16): one
        # long fallback over every running part replaces the 12-minute check per launch (M1d counted 53 over
        # orchestrators 30-32); parts report Done by message, and a failed child wakes its parent.
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
              "ORCHESTRATOR — a part was launched: no check-in for this launch. Keep ONE fallback, `send_later` 60 "
              "minutes (`list_triggers`: add it only if none is pending): at it, `get_session` every running part "
              "(the Sessions now table); idle with no background job and no Done = send it \"go on with the next "
              "item\"; then arm the next 60 minutes."}}))
        return 0
    if orchestrator and event == "UserPromptSubmit":
        prompt = str(data.get("prompt") or "")
        if prompt.lstrip().startswith("<task-notification>") or "[SYSTEM NOTIFICATION" in prompt:
            return 0  # a session's message or a timer: the no-text rule comes at PostToolUse and Stop
        # The limit arrives BEFORE the answer: a Stop block comes after he has read the long copy and makes a
        # second one (the owner, 2026-11-13 08:5x: "your hook isn't working properly anymore").
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
              f"ORCHESTRATOR — the owner typed this: answer what he asked in at most {CHAT_MAX} characters, a few "
              "lines; the detail goes on the board."}}))
        return 0
    if orchestrator and event == "PostToolUse":
        _, human, _ = turn_opener(str(data.get("transcript_path") or ""))
        if human:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                  f"ORCHESTRATOR — the owner typed in this turn: answer him in at most {CHAT_MAX} characters."}}))
            return 0
        if not human:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                  "ORCHESTRATOR — the owner did not write this turn: end it with NO text at all, unless a part is "
                  "'Done and checked: …' or something is 'Broken: …' (three lines at most)."}}))
        return 0
    if not orchestrator and event == "UserPromptSubmit":
        # The owner, 2026-11-14 10:55 (typed: "yes add it"): every session gets the rule when its turn starts, not
        # only the orchestrator: a Stop hook speaks after he has read the long copy (ED4, 10:43: 930 characters).
        prompt = str(data.get("prompt") or "")
        notified = prompt.lstrip().startswith("<task-notification>") or "[SYSTEM NOTIFICATION" in prompt
        if is_clerk(data):
            rule = "CLERK — end this turn with no text at all: nobody reads your chat."
        elif notified or len(prompt) > TYPED_MAX:
            rule = ("SAY IT ONCE — the owner did not type this: end your turn with a single \"·\" and nothing else; "
                    "the result goes in your board post.")
        else:
            rule = (f"SAY IT ONCE — the owner typed this in your chat: answer what he asked in at most {CHAT_MAX} "
                    "characters, a few lines; the detail goes in your board post.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": rule}}))
        return 0
    if orchestrator and event != "Stop":
        return 0
    if event == "PreToolUse":
        msg = str((data.get("tool_input") or {}).get("message") or "")
        if msg.startswith(CLERK_PREFIX):
            try:
                open(clerk_marker(data), "w").close()
            except OSError:
                pass
        if len(msg) > MESSAGE_MAX and not msg.startswith(CLERK_PREFIX):
            print(f"SAY IT ONCE — this message is {len(msg)} characters (limit {MESSAGE_MAX}). The result lives in your "
                  "board post (docs/BOARD.md): send \"Done. Check post <id>.\", \"Done. Continue with <prompt file>.\" "
                  "or a short question, and put the rest in the post.", file=sys.stderr)
            return 2
        if len(msg) > BEFORE_POST_MAX and not msg.startswith(CLERK_PREFIX) and not os.path.exists(posted_marker(data)):
            print(f"SAY IT ONCE — this message is {len(msg)} characters and you have not written to the board yet "
                  f"(limit {BEFORE_POST_MAX} until you have). Your post exists from turn 1 (docs/BOARD.md): `set` it "
                  "with the full report, then send \"Check post <id>: <one line>\".", file=sys.stderr)
            return 2
    elif event == "PostToolUse":
        if str(data.get("tool_name") or "").endswith("ArtifactData") and \
                (data.get("tool_input") or {}).get("action") in BOARD_WRITES:
            try:
                open(posted_marker(data), "w").close()
            except OSError:
                pass
        # The moment just before the closing message (a message sent, a board write): the rule arrives BEFORE the
        # long copy is written (the owner, 2026-11-12: "M1 still finished with a long message").
        if is_clerk(data):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                  "CLERK — it is passed. When the thread is answered, end your turn with no text at all: nobody "
                  "reads your chat."}}))
            return 0
        msg = str((data.get("tool_input") or {}).get("message") or "")
        if msg.startswith("Check post"):
            # A pointer is not an end (the owner, 2026-11-13: Z3 stopped after turn 1's "Check post").
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                  "KEEP GOING — \"Check post\" is a pointer, not an end: go on with your next item in this same "
                  "turn. End a turn only at \"Done\", the token limit, or while a background job you started will "
                  "wake you."}}))
            return 0
        _, human, size = turn_opener(str(data.get("transcript_path") or ""))
        if human and 0 < size <= TYPED_MAX:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                  f"SAY IT ONCE — the owner typed in this turn: answer him in at most {CHAT_MAX} characters, a few "
                  "lines; the detail goes in your board post."}}))
            return 0
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
              "SAY IT ONCE — it is on the board or sent: if you end your turn now, end it with a single \"·\" and "
              "nothing else: no Posted, no summary, no list."}}))
        return 0
    elif event == "Stop":
        text = closing_text(str(data.get("transcript_path") or ""))
        opener, human, size = turn_opener(str(data.get("transcript_path") or ""))
        if orchestrator:
            # The owner, 2026-11-13: the retry is checked too (it used to pass whatever it said), a few times a turn.
            reason = orchestrator_verdict(text, human)
        else:
            reason = part_verdict(text, human and size <= TYPED_MAX, is_clerk(data))
        if reason is None:
            return 0
        count = os.path.join(tempfile.gettempdir(), "harmonize-orch-blocks-" + str(data.get("session_id") or "x"))
        try:
            key, n = open(count).read().split("\t")
            n = int(n) if key == opener else 0
        except (OSError, ValueError):
            n = 0
        try:
            with open(count, "w") as f:
                f.write(f"{opener}\t{n + 1}")
        except OSError:
            pass
        if n == 0:
            # The owner, 2026-11-14 10:33 (typed: "yes build it"): the first long close of a turn is BLOCKED once, and
            # the retry may only be "·" (09:52's no-block let TB1 end every turn with a summary; the old block, three
            # times a turn, asked for a shorter copy and made three messages out of one).
            print(reason + "\nYOUR RETRY: a single \"·\" and nothing else: no apology, no shorter copy (it is already "
                  "read). This is blocked once; whatever follows is recorded as a miss.", file=sys.stderr)
            return 2
        # The retry (or later) was not "·": recorded, not blocked again (no loop, no third copy).
        try:
            os.makedirs(os.path.join(project_dir(), "build"), exist_ok=True)
            with open(os.path.join(project_dir(), "build", "say-it-once-misses.tsv"), "a", encoding="utf-8") as f:
                f.write(f"{data.get('session_id') or 'x'}\t{len(text)}\t{reason.splitlines()[0][:80]}\n")
        except OSError:
            pass
        print(json.dumps({"systemMessage": f"{MISS_MARK} a closing message of {len(text)} characters broke the rule "
                          "after its block (recorded)."}))
        return 0
    return 0


def project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or "."


if __name__ == "__main__":
    sys.exit(main())
