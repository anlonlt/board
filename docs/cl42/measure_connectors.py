"""CL42: what the account's connectors cost at the start of a Claude Code web session.

extract <transcript.jsonl> <out.json>: per-call context (input + cache read + cache creation)
  and, per MCP server, the tools it brought and the characters it adds (deferred names + instructions).
probes <probes.json>: mean 2nd-call context per variant (baseline / deniedMcpServers Vercel / disableClaudeAiConnectors); exit 1 unless denying Vercel saves > 10,000 tokens.
check <out.json>: recompute the totals from the saved extract and print them; exit 1 if they disagree.
"""
import collections, json, re, sys

def server(name):
    m = re.match(r'mcp__(.+?)__', name)
    return m.group(1) if m else 'builtin'

def extract(path, out):
    rows = [json.loads(l) for l in open(path)]
    calls, seen = [], set()
    for d in rows:
        if d.get('type') == 'assistant':
            m = d['message']
            if m.get('id') in seen:
                continue
            seen.add(m.get('id'))
            u = m.get('usage', {})
            calls.append({'t': d.get('timestamp'), 'input': u.get('input_tokens', 0),
                          'cache_read': u.get('cache_read_input_tokens', 0),
                          'cache_creation': u.get('cache_creation_input_tokens', 0)})
    deltas = []
    for d in rows:
        a = d.get('attachment') or {}
        if a.get('type') == 'deferred_tools_delta':
            before = sum(1 for x in rows[:rows.index(d)] if x.get('type') == 'assistant')
            tools = collections.Counter(server(n) for n in a.get('addedNames', []))
            chars = collections.Counter()
            for line in a.get('addedLines', []):
                chars[server(line)] += len(line) + 1
            deltas.append({'assistant_entries_before': before, 'tools': tools, 'chars': chars,
                           'pending': len(a.get('pendingMcpServers', []))})
        if a.get('type') == 'mcp_instructions_delta':
            deltas.append({'instructions': {n: len(b) for n, b in zip(a['addedNames'], a['addedBlocks'])}})
    json.dump({'calls': calls, 'deltas': deltas}, open(out, 'w'), indent=1)

def check(out):
    d = json.load(open(out))
    c = d['calls']
    ctx = [x['input'] + x['cache_read'] + x['cache_creation'] for x in c]
    tools = collections.Counter()
    for x in d['deltas']:
        tools.update(x.get('tools', {}))
    print('call 1 context', ctx[0]); print('call 2 context', ctx[1]); print('call 2 - call 1', ctx[1] - ctx[0])
    print('tools per server', dict(tools))
    ok = ctx[0] == 80934 and ctx[1] == 97920 and tools.get('992a93b1-470f-4dd6-95ed-36e07669d518') == 287
    print('OK' if ok else 'MISMATCH')
    sys.exit(0 if ok else 1)

def probes(path):
    d = json.load(open(path)); means = {}
    for k, v in d.items():
        if k == 'note':
            continue
        c2 = [sum(r['call2']) for r in v['runs'].values()]
        c1 = [sum(r['call1']) for r in v['runs'].values()]
        means[k] = sum(c2) / len(c2)
        print(f"{k}: call 1 {c1}, call 2 {c2}, call 2 mean {means[k]:,.0f} ({len(c2)} runs)")
    saved = means['A_baseline'] - means['B_denied_vercel']
    print(f"deniedMcpServers Vercel saves {saved:,.0f}; disableClaudeAiConnectors saves {means['A_baseline'] - means['C_disable_connectors']:,.0f}")
    sys.exit(0 if saved > 10000 else 1)

if __name__ == '__main__':
    {'probes': lambda: probes(sys.argv[2]), 'extract': lambda: extract(sys.argv[2], sys.argv[3]), 'check': lambda: check(sys.argv[2])}[sys.argv[1]]()
