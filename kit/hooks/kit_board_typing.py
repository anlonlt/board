#!/usr/bin/env python3
"""agy PreInvocation: when the owner's forwarded comment reaches a session, remind it to set the typing dots first.
Silent on every other prompt (and fires at most once per distinct prompt)."""
import hashlib
import json
import os
import re
import sys
import tempfile

PAT = re.compile(r"Board comment from the owner on your post (\S+?),")


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return 0
    prompt = str(data.get("lastUserInput") or "")
    m = PAT.search(prompt)
    if not m:
        print("{}")
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
