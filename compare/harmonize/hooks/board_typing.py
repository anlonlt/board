#!/usr/bin/env python3
"""UserPromptSubmit: when a session RECEIVES the owner's forwarded board comment, tell it to show the typing dots first.

The owner, 2026-11-14 (post ED4-editor-tutorial): the "a session is writing" dots were set inconsistently, because each
session had to remember a rule in docs/BOARD.md. Dots set by the clerk would lie (a busy session reads his words only
at the end of its turn), so the signal is given here, at the moment the session actually reads the comment: one small
write, no extra reading. The page clears the dots when the answer from the same `from` lands, or after 10 minutes.

Matches the clerk's forward ("Board comment from the owner ... on [your] post <id>"). Silent on anything else.
Its test: python3 -I .claude/hooks/test_board_typing.py
"""
import json
import re
import sys

FORWARD = re.compile(r"Board comment from the owner\b.{0,80}?\bon (?:your )?post ([A-Za-z0-9_\-.~:@+]{1,200})", re.S)


def advice(prompt):
    m = FORWARD.search(prompt or "")
    if not m:
        return None
    post = m.group(1).rstrip(".,;:")
    return (
        "BOARD TYPING DOTS: the owner's comment on post `" + post + "` has reached you. Your FIRST action, before "
        "reading or doing anything else: ArtifactData `set` in collection `typing`, doc_id `" + post + "`, data "
        "{from: <the `from` you will answer with>, at: <date -u +%Y-%m-%dT%H:%M:%SZ, run now>} (if it exists, the "
        "refusal names its version: set again with that if_version). The page shows three dots until your comment "
        "lands. If you end up not answering, delete it. docs/BOARD.md § Comments."
    )


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    text = advice(str(data.get("prompt") or ""))
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
