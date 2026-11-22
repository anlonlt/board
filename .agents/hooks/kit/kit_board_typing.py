#!/usr/bin/env python3
"""agy PreInvocation: when the owner's forwarded comment reaches a session, remind it to set the typing dots first.
Silent on every other prompt (and fires at most once per distinct prompt)."""
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import is_claude_payload  # noqa: E402

PAT = re.compile(r"Board comment from the owner on your post (\S+?),")


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    claude = is_claude_payload(data)
    prompt = str(data.get("lastUserInput") or data.get("prompt") or "")
    m = PAT.search(prompt)
    if not m:
        if not claude:
            print("{}")
        return 0
    if claude:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
              "TYPING: before you write the answer, `ArtifactData` `set` `typing/%s` {from: \"<your part>\", at: <now>} "
              "(.kit/BOARD.md § Comments); the owner sees dots until your comment lands." % m.group(1)}}))
        return 0
    cid = str(data.get("conversationId") or "x")
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    mark = os.path.join(tempfile.gettempdir(), "kit-typing-%s" % cid)
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
    msg = ("TYPING: before you write the answer, set `typing/%s` with "
           "`python3 -I .kit/board_cli.py set typing %s --data '{\"from\":\"<your part>\",\"at\":\"<now>\"}'` "
           "(.kit/BOARD.md § Comments); the owner sees dots until your comment lands." % (m.group(1), m.group(1)))
    print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": msg}}]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
