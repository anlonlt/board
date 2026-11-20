#!/usr/bin/env python3
"""What one board read costs in tokens, measured from a probe subagent's own usage records (part BD9).

The probe (a subagent, the orchestrator's model) makes ONE tool call per request and writes no text, so the context
of request k+1 minus the context of request k (input + cache read + cache write, from `usage`) is what call k added:
its tool_use block and its result. Tokens are measured, never estimated at 4 characters.

    python3 -I scripts/review/BD9-kit-mirror-token-probe.py TRANSCRIPT.jsonl

TRANSCRIPT: the subagent's JSONL transcript (the Agent tool's output file). Prints one row per call: the tool, its
target, the characters of its result, and the tokens it added; then, per post, today's way (get to a file + Read
it; + the comments by query + reading their files) against `kit post`.
"""
import collections, json, re, sys

rows, ctx, order, uses, results = [], {}, [], {}, {}
for line in open(sys.argv[1], encoding="utf-8"):
    d = json.loads(line)
    m = d.get("message")
    if not isinstance(m, dict):
        continue
    if d.get("type") == "assistant":
        mid = m.get("id")
        u = m.get("usage") or {}
        if mid not in ctx:
            order.append(mid)
        ctx[mid] = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                uses[b["id"]] = (mid, b["name"], b.get("input") or {})
    elif d.get("type") == "user":
        for b in m.get("content") or [] if isinstance(m.get("content"), list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                results[b["tool_use_id"]] = len(c) if isinstance(c, str) else sum(len(x.get("text", "")) for x in c or [] if isinstance(x, dict))
idx = {mid: i for i, mid in enumerate(order)}
out = []
for tid, (mid, name, inp) in uses.items():
    i = idx[mid]
    if i + 1 >= len(order):
        continue
    tok = ctx[order[i + 1]] - ctx[mid]
    if name == "Bash":
        c = inp.get("command", "")
        what = "kit " + c.split("kit.py ", 1)[1] if "kit.py " in c else ("cat comments " + c.split("/")[-3] if c.startswith("cat ") else c[:40])
    elif name == "ArtifactData":
        what = "%s %s %s" % (inp.get("action"), inp.get("collection"), inp.get("doc_id") or json.dumps(inp.get("query", {}).get("where", ""))[:60])
    elif name == "Read":
        what = "Read " + inp.get("file_path", "").rsplit("/", 1)[-1]
    else:
        what = name
    out.append((i, name, what, results.get(tid, 0), tok))
out.sort()
print("%-4s %-12s %-70s %8s %8s" % ("req", "tool", "call", "chars", "tokens"))
for i, name, what, ch, tok in out:
    print("%-4d %-12s %-70s %8d %8d" % (i, name, what[:70], ch, tok))
# per post: A kit post, B get, C Read, D query comments, E cat comments
per = collections.OrderedDict()
cur = None
for i, name, what, ch, tok in out:
    m = re.match(r"kit post (\S+)", what)
    if m:
        cur = m.group(1); per[cur] = {"kit": tok}
    elif cur and name == "ArtifactData" and what.startswith("get"):
        per[cur]["get"] = tok
    elif cur and name == "Read":
        per[cur]["read"] = tok
    elif cur and name == "ArtifactData" and what.startswith("query"):
        per[cur]["query"] = tok
    elif cur and what.startswith("cat comments"):
        per[cur]["cat"] = tok
        cur = None
print("\n%-34s %9s %9s %9s %9s" % ("post", "today:post", "+comments", "kit post", "kit/today"))
tp = tk = 0
for p, v in per.items():
    post_only = v.get("get", 0) + v.get("read", 0)
    full = post_only + v.get("query", 0) + v.get("cat", 0)
    tp += full; tk += v["kit"]
    print("%-34s %9d %9d %9d %8.0f%%" % (p[:34], post_only, full, v["kit"], 100 * v["kit"] / max(full, 1)))
print("%-34s %9s %9d %9d %8.0f%%" % ("all ten", "", tp, tk, 100 * tk / max(tp, 1)))
