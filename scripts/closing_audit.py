#!/usr/bin/env python3
"""CL60: how many turns of a session closed first with more than a single "·".

Reads a Claude Code transcript (.jsonl, one event per line: the local ~/.claude/projects/... file, or the user/assistant
events of `list_events` saved one per line). A turn is everything from a user message to the next one; its FIRST
closing is the assistant text after its last tool call, before a "Stop hook feedback" retry (when the Stop hook refused
it, that retry is the second closing). A Stop hook runs after the closing is on screen, so a long first closing was read
by the owner whatever the retry says.

    python3 -I scripts/closing_audit.py <transcript.jsonl> [...]     # one line per file, then the totals
    python3 -I scripts/closing_audit.py --json <transcript.jsonl>
"""
import json
import sys

DOT = "·"


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text") or "" for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def turns(path):
    """[{opener, first, retried}] of one transcript: each turn's opening text, first closing and whether the Stop
    hook made it retry."""
    out, cur = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            msg = e.get("message") or {}
            if kind == "user" and not e.get("isMeta"):
                c = msg.get("content")
                if isinstance(c, list) and not any(isinstance(x, dict) and x.get("type") == "text" for x in c):
                    continue                     # a tool result: the turn goes on
                t = _text(c)
                if cur is not None and t.startswith("Stop hook feedback"):
                    cur["retried"] = True
                    cur["closing_done"] = True
                    continue
                cur = {"opener": t[:120], "first": "", "retried": False, "closing_done": False}
                out.append(cur)
            elif kind == "assistant" and cur is not None and not cur["closing_done"]:
                for c in msg.get("content") or []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        cur["first"] = ""
                    elif c.get("type") == "text":
                        cur["first"] = (cur["first"] + "\n" + (c.get("text") or "")).strip()
    for t in out:
        t.pop("closing_done", None)
    return out


def audit(path):
    ts = [t for t in turns(path) if t["first"] or t["retried"]]
    long_first = [t for t in ts if t["first"].strip() not in ("", DOT)]
    return {"file": path, "turns": len(ts), "long_first": len(long_first), "retried": sum(t["retried"] for t in ts),
            "lengths": [len(t["first"]) for t in long_first]}


def main(argv):
    as_json = "--json" in argv
    files = [a for a in argv if a != "--json"]
    if not files:
        print(__doc__.strip().splitlines()[-2].strip())
        return 2
    rows = [audit(p) for p in files]
    tot = {"turns": sum(r["turns"] for r in rows), "long_first": sum(r["long_first"] for r in rows)}
    if as_json:
        print(json.dumps({"files": rows, "totals": tot}))
        return 0
    for r in rows:
        print("%s: %d turn(s), %d first closing(s) longer than \"%s\" %s" % (r["file"], r["turns"], r["long_first"], DOT, r["lengths"] or ""))
    print("total: %d of %d turn(s) closed first with more than \"%s\"" % (tot["long_first"], tot["turns"], DOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
