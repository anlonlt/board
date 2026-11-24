#!/usr/bin/env python3
"""What fills an orchestrator's context, by source: the measure WF1's first build step needs as its bar.

Review CR-WF1-workflow-inventory (2026-11-19). WF1 says "the biggest cost is context" and proposes a lookup script
that makes board reads shorter, but leaves "how many post reads an orchestrator does per life" unmeasured. This
reads one orchestrator's whole transcript and says where its context came from, so a saving can be stated in the
orchestrator's own tokens instead of characters per query.

    python3 -I scripts/what_fills.py PAGES_DIR [--config config/<project>.json]

Kit version (anlonlt/board, BD9, 2026-11-20): the shell-call kinds come from the config's `shell_buckets`
(an example: config/example.json); everything else is generic Claude Code. Origin: anlonlt/harmonize
scripts/review/WF1-workflow-inventory-what-fills-an-orchestrator.py (review CR-WF1).

PAGES_DIR: every page of `list_events` (kinds assistant + user, limit 100, paged back with before_id) for one
session, each saved as a file (a page is too large to return inline, so the harness saves it: copy that file).
Only the main thread counts: an event with a parent_tool_use_id belongs to a subagent and never enters the
orchestrator's context (its last report does, as the Agent tool's result).

Characters are counted per block; tokens come from the session's own `usage` (context at a request = cache read
+ cache write + input). "re-read cost" of a block = its characters x the number of later main-thread requests:
what it is paid again for, the thing the kit is meant to cut.

RESULT (orchestrator 46, its whole life 15:22 -> 20:37, 17 pages, 1,045 events):
  main thread 227 requests, 58.7 M tokens re-read (of the session's 70.8 M: the other 12.1 M are its subagents,
  10 Agent calls); context 103k -> 395k; 503,757 characters entered, 1.7 characters per token (not 4).
  What it re-reads: its own writing 23 %, what it was sent or read 77 %.
  Board content: shell reads of dumped board files 79,866 chars (39 calls) + ArtifactData reads 30,426 chars
  (26 calls) = 22 % of what entered. Peer notifications (ReadNotifications) 93,356 chars = 18.5 %.
  Posts fetched with `get`: 8 calls in the life, 7 of them to files already.
"""
import collections
import glob
import json
import os
import re
import sys


def pages(d):
    for f in sorted(glob.glob(os.path.join(d, "*.txt"))):
        s = open(f, encoding="utf-8").read()
        a, b = s.find("{"), s.rfind("}")
        if a < 0:
            continue
        try:
            yield json.loads(s[a:b + 1])["ccr"]
        except (ValueError, KeyError):
            print("unreadable page:", f, file=sys.stderr)


def text_len(c):
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(text_len(x.get("text") if isinstance(x, dict) and "text" in x else json.dumps(x)) if not isinstance(x, str) else len(x) for x in c)
    return len(json.dumps(c))


CONFIG = {"shell_buckets": []}


def bucket_use(name, inp):
    if name == "ArtifactData":
        act = inp.get("action", "?")
        col = inp.get("collection", "")
        if act in ("get", "list", "query"):
            return "board read (ArtifactData %s%s)" % (act, ", to files" if inp.get("out_dir") else ", inline")
        return "board write (ArtifactData %s %s)" % (act, col.split("/")[0])
    if name.startswith("mcp__claude-code-remote__"):
        return "sessions: " + name.rsplit("__", 1)[1]
    if name == "Bash":
        c = inp.get("command", "")
        for bk in CONFIG.get("shell_buckets", []):  # the project's own kinds of shell call, first match wins
            if all(re.search(rx, c) for rx in bk["all"]):
                return bk["name"]
        return "Bash: other"
    if name in ("Edit", "Write"):
        return "file edits (Edit/Write)"
    if name in ("Agent", "Task"):
        return "subagent (Agent)"
    return name


def main():
    global CONFIG
    args = sys.argv[1:]
    if "--config" in args:
        i = args.index("--config")
        CONFIG = json.load(open(args[i + 1], encoding="utf-8"))
        del args[i:i + 2]
    d = args[0]
    seen, events = set(), []
    for p in pages(d):
        for e in p.get("data", []):
            kind = "assistant" if "assistant" in e else "user" if "user" in e else None
            if not kind:
                continue
            m = e[kind]
            if m.get("uuid") in seen:
                continue
            seen.add(m.get("uuid"))
            c = m.get("internal_anthropic_catchall", m)
            events.append((e.get("created_at", ""), kind, c))
    events.sort(key=lambda x: x[0])
    main_ev = [x for x in events if not x[2].get("parent_tool_use_id")]
    print("events: %d (main thread %d, subagents %d); %s -> %s" % (
        len(events), len(main_ev), len(events) - len(main_ev), events[0][0][:19], events[-1][0][:19]))

    # requests of the main thread, in order, with the context each one saw
    reqs, order = {}, []
    for t, kind, c in main_ev:
        if kind != "assistant":
            continue
        u = (c.get("message") or {}).get("usage") or {}
        r = c.get("request_id") or t
        if r not in reqs:
            order.append(r)
        reqs[r] = (t, u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0), u.get("input_tokens", 0))
    n = len(order)
    ctx = [sum(reqs[r][1:]) for r in order]
    read = sum(reqs[r][1] for r in order)
    print("main-thread requests: %d; cache re-read summed %.1f M tokens; context first %dk, last %dk, mean %dk" % (
        n, read / 1e6, ctx[0] // 1000, ctx[-1] // 1000, sum(ctx) // n // 1000))
    idx = {r: i for i, r in enumerate(order)}

    # every block of the main thread, with how many later requests re-read it
    use_name, rows = {}, collections.defaultdict(lambda: [0, 0, 0])  # bucket -> [blocks, chars, chars x later requests]
    posts_read = collections.Counter()
    cur = 0
    for t, kind, c in main_ev:
        msg = c.get("message") or {}
        if kind == "assistant":
            cur = idx.get(c.get("request_id") or t, cur)
        later = n - 1 - cur
        cont = msg.get("content")
        blocks = cont if isinstance(cont, list) else [{"type": "text", "text": cont or ""}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            ty = b.get("type")
            if kind == "assistant":
                if ty == "tool_use":
                    inp = b.get("input") or {}
                    bk = bucket_use(b.get("name", "?"), inp)
                    use_name[b.get("id")] = bk
                    if b.get("name") == "ArtifactData" and inp.get("action") == "get" and inp.get("collection") == "posts":
                        posts_read[(inp.get("doc_id"), bool(inp.get("out_dir")))] += 1
                    key, ln = "OUT its own call: " + bk, len(json.dumps(inp))
                elif ty == "text":
                    key, ln = "OUT its own chat text", len(b.get("text") or "")
                else:
                    continue  # thinking is not carried as text
            else:
                if ty == "tool_result":
                    key, ln = "IN  result: " + use_name.get(b.get("tool_use_id"), "?"), text_len(b.get("content"))
                else:
                    s = b.get("text") or ""
                    head = s.lstrip()[:60]
                    who = "peer message" if "session" in head.lower() and "<" in head else "hook / system text" if "hook" in s[:300].lower() or "system-reminder" in s[:300] else "typed or delivered message"
                    key, ln = "IN  user turn: " + who, len(s)
            rows[key][0] += 1
            rows[key][1] += ln
            rows[key][2] += ln * later
    tot = sum(v[1] for v in rows.values())
    totr = sum(v[2] for v in rows.values())
    grow = ctx[-1] - ctx[0]
    print("\ncharacters that entered the main thread: %d (context grew %dk tokens: %.1f characters per token)" % (tot, grow // 1000, tot / max(grow, 1)))
    print("%-62s %6s %9s %6s %8s" % ("source", "blocks", "chars", "share", "re-read"))
    for k, v in sorted(rows.items(), key=lambda kv: -kv[1][2]):
        if v[1] * 200 < tot:
            continue
        print("%-62s %6d %9d %5.1f%% %7.1f%%" % (k[:62], v[0], v[1], 100 * v[1] / tot, 100 * v[2] / max(totr, 1)))
    io = collections.Counter()
    for k, v in rows.items():
        io[k[:3]] += v[2]
    print("\nre-read share: its own writing (OUT) %.0f%%, what it was sent or read (IN) %.0f%%" % (100 * io["OUT"] / totr, 100 * io["IN "] / totr))
    b = collections.Counter()
    for k, v in rows.items():
        if "board read" in k:
            b["board reads"] += v[2]
        elif "board write" in k:
            b["board writes"] += v[2]
    for k, v in b.items():
        print("  %s: %.1f%% of what is re-read" % (k, 100 * v / totr))
    inline = sum(c for (i, f), c in posts_read.items() if not f)
    print("\nposts fetched with `get`: %d calls on %d posts (%d inline, %d to files)" % (
        sum(posts_read.values()), len({i for i, _ in posts_read}), inline, sum(posts_read.values()) - inline))
    print("  most fetched:", ", ".join("%s x%d" % (i, c) for (i, f), c in posts_read.most_common(6)))


if __name__ == "__main__":
    main()
