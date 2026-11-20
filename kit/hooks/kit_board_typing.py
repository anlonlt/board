#!/usr/bin/env python3
"""UserPromptSubmit: when the owner's forwarded comment reaches a session, remind it to set the typing dots first
(origin: anlonlt/harmonize board_typing.py). Silent on every other prompt."""
import json
import re
import sys

PAT = re.compile(r"Board comment from the owner on your post (\S+?),")


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    m = PAT.search(data.get("prompt") or "")
    if not m:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
          "TYPING: before you write the answer, `ArtifactData` `set` `typing/%s` {from: \"<your part>\", at: <now>} "
          "(.kit/BOARD.md § Comments); the owner sees dots until your comment lands." % m.group(1)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
