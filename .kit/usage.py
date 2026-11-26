#!/usr/bin/env python3
"""The usage ledger (CL37): what every session of a project cost, recorded by a script, never by a model call.

The owner, 2026-11-24: "implement scripts into the kit that automatically track token usage, tool use, agent use,
etc. in every conversation - parts, orchestrator, reviewer, etc., without using any tokens themselves, for proper
comparison and optimization pre/post kit updates and changes."

    python3 -I .kit/usage.py record --transcript T.jsonl [--role R] [--part P] [--push]   one session -> one row
    python3 -I .kit/usage.py hook                     (Stop / SessionEnd hook: the payload on stdin; never blocks)
    python3 -I .kit/usage.py import-ccr FILE.jsonl    rows from saved claude-code-remote get_session records
    python3 -I .kit/usage.py push                     the local rows onto the ledger branch (origin/kit-usage)
    python3 -I .kit/usage.py compare --before A --after B [--role part] [--json]
    python3 -I .kit/usage.py show [--session S]       the rows, one line each

Sources, measured, never estimated:
  * the Claude Code transcript (`~/.claude/projects/<dir>/<session>.jsonl`, the hook payload's transcript_path) and
    its subagents (`<session>/subagents/agent-*.jsonl` + `.meta.json`): every API call's usage (deduplicated by
    message id), tool calls by name, ArtifactData calls and bytes, messages sent and received, owner turns;
  * a claude-code-remote record (`get_session`: external_metadata.usage, context_usage), for sessions that ran
    before the hook existed. No script can fetch these: claude-code-remote is a tool of the model's harness, with no
    HTTP API a container reaches, so the records are saved to a file by one cheap subagent (CL37: a Haiku agent,
    list_sessions + get_session per session) and `import-ccr` reads that file. Cloud sessions from now on need none
    of this: their transcript is on their own container, where the hook reads it.
Cost: the API list prices below per model (cache writes 1.25x input for 5 minutes, 2x for 1 hour; `usage.prices`
in .kit/config.json overrides them); a ccr record's own cost_usd is kept as it is.

Where the rows live: `.kit/run/usage/<session>.json` (local, current at every Stop), and the committed ledger, the
branch `kit-usage` (file `usage/<YYYY-MM-DD>.jsonl`, the session's start date), written with git plumbing so the
working tree never changes (a Stop hook that dirtied it would make kit_stop and end_session refuse every turn).
`compare` reads both. Push: at SessionEnd, from end_session.py, and at every Stop, detached
(`usage.push_minutes` to space them); `usage.push: false` in .kit/config.json, or KIT_USAGE_PUSH=0, keeps it local.
"""
import argparse
import glob
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = 1
BRANCH = "kit-usage"

# $ per million tokens: input, output, cache read (claude-api skill, cached 2026-11-03; Gemini / agy list prices). Longest prefix wins.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0, 0.20), "claude-mythos-5-2": (8.0, 40.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 1.00), "claude-fable-5": (10.0, 50.0, 1.00),
    "claude-opus-5-5": (4.0, 20.0, 0.20), "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50), "claude-opus-4-7": (5.0, 25.0, 0.50), "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10), "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30), "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    # Gemini / agy models (CL44; override any via kit.usage.prices in .kit/config.json)
    "gemini-4-neon": (2.0, 12.0, 0.20),
    "gemini-4-carbon": (2.0, 12.0, 0.20),
    "gemini-4-argon": (1.25, 10.0, 0.125),
    "gemini-4-hydrogen": (1.25, 10.0, 0.125),
    "gemini-pro-orchestrator": (2.0, 12.0, 0.20),
    "gemini-3.8-flash": (0.50, 3.0, 0.05),
    "gemini-3-pro": (2.0, 12.0, 0.20),
    "gemini-3-flash": (0.50, 3.0, 0.05),
    "gemini-2.5-pro": (1.25, 10.0, 0.3125),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.025),
    "gemini-2.5-flash": (0.30, 2.50, 0.075),
    "gemini-2.0-flash-lite": (0.075, 0.30, 0.01875),
    "gemini-2.0-flash": (0.10, 0.40, 0.025),
    "gpt-oss-120b": (0.50, 2.0, 0.05),
}
# Long-prompt tiers: above this many prompt tokens (input + cache read + cache write) a call bills at these prices.
TIERS = {"claude-haiku-5-5": (100000, (0.50, 2.50, 0.05))}
WRITE_5M, WRITE_1H = 1.25, 2.0
SEND_TOOLS = re.compile(r"(^SendMessage$|send_message$)")
AGENT_TOOLS = ("Agent", "Task", "invoke_subagent")
RECEIVED = ("<cross-session-message", "<other-session", "<teammate-message")
NOTICE = ("[SYSTEM NOTIFICATION", "<task-notification", "[Request interrupted", "<system-reminder>",
          "Caveat: The messages below", "<local-command")
TEST_CMD = re.compile(r"unittest|pytest|run_all_tests|test[-_]changed|gradlew\S*\s+\S*[tT]est|npm (?:run )?test|"
                      r"node \S*test_\S*\.js|python3? (?:-I )?\S*test_\S*\.py")
KIT_CMD = re.compile(r"\.kit/([a-z_]+\.py)(?:\s+(?:-I\s+)?([a-z][a-z-]*))?")


# ---------------------------------------------------------------- the project and its config

def project_root(start=None):
    d = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, ".kit")) or os.path.isdir(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start or os.getcwd())
        d = up


def kit_conf(root):
    try:
        with open(os.path.join(root, ".kit", "config.json"), encoding="utf-8") as fh:
            return (json.load(fh).get("kit") or {}).get("usage") or {}
    except (OSError, ValueError):
        return {}


def price(model, conf=None):
    table = {k.lower(): v for k, v in PRICES.items()}
    for k, v in ((conf or {}).get("prices") or {}).items():
        table[str(k).lower()] = tuple(v)
    ml = str(model or "").strip().lower()
    best = ""
    for k in table:
        if ml.startswith(k) and len(k) > len(best):
            best = k
    return table.get(best)


def git(root, *a, env=None, inp=None):
    try:
        r = subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=60,
                           env=env, input=inp)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def kit_version(root):
    """The kit in force: the manifest's `_kit_commit` (`<sha> <iso date>`), as install.py wrote it."""
    try:
        with open(os.path.join(root, ".kit", "manifest.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("_kit_commit") or ""
    except (OSError, ValueError):
        v = ""
    parts = v.split()
    return (parts[0] if parts else None), (parts[1] if len(parts) > 1 else None)


# ---------------------------------------------------------------- reading a transcript

def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else _text(c))
    return "\n".join(out)


def _agy_arg(v):
    """Unwrap agy transcript tool_calls arg values (often JSON-encoded strings like '\"git status\"')."""
    if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _agy_session_id_from_path(path):
    """Extract conversation UUID from .../<uuid>/.system_generated/logs/transcript*.jsonl when present."""
    ab = os.path.abspath(path)
    base = os.path.basename(ab)
    if base in ("transcript.jsonl", "transcript_full.jsonl"):
        logs_dir = os.path.dirname(ab)
        sys_dir = os.path.dirname(logs_dir)
        conv_dir = os.path.dirname(sys_dir)
        if os.path.basename(logs_dir) == "logs" and os.path.basename(sys_dir) == ".system_generated":
            cand = os.path.basename(conv_dir)
            if cand and cand not in (".", ".."):
                return cand
    return base[:-6] if base.endswith(".jsonl") else base


def _file_uri_to_path(uri):
    if not uri:
        return None
    s = str(uri).strip()
    if s.startswith("file://"):
        import urllib.parse
        return urllib.parse.unquote(urllib.parse.urlparse(s).path)
    return s


def _parse_agy_subagent_result(txt):
    """Extract (conversationId, log_path) from agy's 'Created the following subagent: {...}' tool output."""
    if "Created the following subagent:" not in txt:
        return None, None
    after = txt.split("Created the following subagent:", 1)[1]
    start = after.find("{")
    if start >= 0:
        depth_b = 0
        for idx in range(start, len(after)):
            ch = after[idx]
            if ch == "{":
                depth_b += 1
            elif ch == "}":
                depth_b -= 1
                if depth_b == 0:
                    try:
                        obj = json.loads(after[start:idx + 1])
                        if isinstance(obj, dict):
                            cid = obj.get("conversationId") or obj.get("conversation_id")
                            uri = obj.get("logAbsoluteUri") or obj.get("transcript") or obj.get("transcriptPath")
                            return (str(cid) if cid else None), _file_uri_to_path(uri)
                    except ValueError:
                        break
                    break
    mc = re.search(r'"conversationId"\s*:\s*"([^"]+)"', after)
    mu = re.search(r'"logAbsoluteUri"\s*:\s*"([^"]+)"', after)
    return (mc.group(1) if mc else None), (_file_uri_to_path(mu.group(1)) if mu else None)


_AGY_TYPES = {"PLANNER_RESPONSE", "USER_INPUT", "SYSTEM_MESSAGE", "GENERIC", "CHECKPOINT", "TASK_BOUNDARY"}


def parse_transcript(path, conf=None, depth=0):
    """One Claude Code or agy transcript -> one row (its subagents' transcripts included, depth 1)."""
    calls, tools, tool_name, kit_cmds = {}, Counter(), {}, Counter()
    board = Counter()
    times, branches, prompts, msg_prompts = [], Counter(), [], []
    sent = sent_chars = received = owner_turns = notices = compactions = 0
    session_id = cwd = version = entrypoint = None
    is_agy = False
    agy_sub_meta = []
    agy_children = []
    seen_agy_children = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            etype = e.get("type")
            if etype in _AGY_TYPES or ("step_index" in e and "created_at" in e and "message" not in e):
                is_agy = True
                session_id = session_id or e.get("conversationId") or e.get("conversation_id") or e.get("sessionId")
                t = _ts(e.get("created_at")) if e.get("created_at") else None
                if t:
                    times.append(t)
                if etype == "PLANNER_RESPONSE":
                    mid = "step:%s" % e.get("step_index", line_no)
                    model = e.get("model")
                    if model != "<synthetic>" and any(
                        k in e for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                                         "cache_read_input_tokens", "cache_creation_input_tokens")
                    ):
                        calls[mid] = (model, {
                            "input_tokens": int(e.get("input_tokens") or 0),
                            "output_tokens": int(e.get("output_tokens") or 0),
                            "cache_read_input_tokens": int(e.get("cache_read_tokens") or e.get("cache_read_input_tokens") or 0),
                            "cache_creation_input_tokens": int(e.get("cache_write_tokens") or e.get("cache_creation_input_tokens") or 0),
                        })
                    for tc in e.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        name = tc.get("name") or "?"
                        raw_args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                        inp = {k: _agy_arg(v) for k, v in raw_args.items()}
                        tools[name] += 1
                        if SEND_TOOLS.search(name):
                            sent += 1
                            sent_chars += len(str(inp.get("Message") or inp.get("message") or ""))
                        elif name == "run_command":
                            cmd_str = str(inp.get("CommandLine") or inp.get("command") or "")
                            if TEST_CMD.search(cmd_str):
                                board["tests_run"] += 1
                            for script, sub in KIT_CMD.findall(cmd_str):
                                kit_cmds[(script[:-3] + (" " + sub if sub else ""))] += 1
                                if script == "board_cli.py":
                                    board["cli"] += 1
                        elif name == "invoke_subagent":
                            agy_sub_meta.append({
                                "type": inp.get("TypeName") or inp.get("type"),
                                "description": inp.get("Role") or inp.get("role"),
                            })
                        elif name == "call_mcp_tool" and str(inp.get("ServerName") or "") == "board-kit":
                            tname = str(inp.get("ToolName") or "")
                            if tname in ("board_post", "board_reply", "board_step_new", "board_step_update"):
                                board["writes"] += 1
                            else:
                                board["reads"] += 1
                elif etype == "USER_INPUT":
                    txt = re.sub(r"</?USER_REQUEST>", "", str(e.get("content") or "")).strip()
                    if txt:
                        owner_turns += 1
                        if len(prompts) < 3:
                            prompts.append(txt[:300])
                elif etype == "SYSTEM_MESSAGE":
                    txt = str(e.get("content") or "")
                    m_msg = re.search(r"\[Message\][^\n]*?\bcontent=(.*)", txt, re.S)
                    if m_msg:
                        received += 1
                        body = re.sub(r"</SYSTEM_MESSAGE>.*", "", m_msg.group(1), flags=re.S).strip()
                        if body and len(msg_prompts) < 3:
                            msg_prompts.append(body[:300])
                    elif txt.strip():
                        notices += 1
                elif etype == "GENERIC" and depth == 0:
                    txt = str(e.get("content") or "")
                    cid, cpath = _parse_agy_subagent_result(txt)
                    if cid and cid not in seen_agy_children:
                        seen_agy_children.add(cid)
                        meta = agy_sub_meta[len(agy_children)] if len(agy_children) < len(agy_sub_meta) else {}
                        agy_children.append((cid, cpath, meta))
                continue
            session_id = session_id or e.get("sessionId")
            cwd = cwd or e.get("cwd")
            version = e.get("version") or version
            entrypoint = entrypoint or e.get("entrypoint")
            t = _ts(e.get("timestamp")) if e.get("timestamp") else None
            if t:
                times.append(t)
            if e.get("gitBranch"):
                branches[e["gitBranch"]] += 1
            if e.get("type") == "system" and e.get("subtype") == "compact_boundary" or e.get("isCompactSummary"):
                compactions += 1
            m = e.get("message") if isinstance(e.get("message"), dict) else {}
            if e.get("type") == "assistant":
                mid = m.get("id") or e.get("uuid")
                if m.get("usage") and m.get("model") != "<synthetic>":
                    calls[mid] = (m.get("model"), m["usage"])
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in tool_name:
                        name = b.get("name") or "?"
                        tool_name[b.get("id")] = (name, b.get("input") or {})
                        tools[name] += 1
                        inp = b.get("input") or {}
                        if name == "ArtifactData":
                            act = inp.get("action") or "?"
                            board["artifact_" + act] += 1
                            if act in ("set", "update", "batch", "str_replace", "delete"):
                                board["write_bytes"] += len(json.dumps(inp, ensure_ascii=False))
                                board["writes"] += 1
                            else:
                                board["reads"] += 1
                        elif SEND_TOOLS.search(name):
                            sent += 1
                            sent_chars += len(str(inp.get("message") or ""))
                        elif name == "Bash":
                            if TEST_CMD.search(str(inp.get("command") or "")):
                                board["tests_run"] += 1
                            for script, sub in KIT_CMD.findall(str(inp.get("command") or "")):
                                kit_cmds[(script[:-3] + (" " + sub if sub else ""))] += 1
                                if script == "board_cli.py":
                                    board["cli"] += 1
            elif e.get("type") == "user":
                c = m.get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            name, inp = tool_name.get(b.get("tool_use_id"), ("?", {}))
                            if name == "ArtifactData" and (inp.get("action") in ("get", "list", "query")):
                                board["read_bytes"] += len(_text([b]))
                            elif name == "Bash" and "board_cli.py" in str(inp.get("command") or ""):
                                board["cli_bytes"] += len(_text([b]))
                if e.get("isMeta") or e.get("isCompactSummary") or e.get("toolUseResult") is not None:
                    continue
                txt = _text(c).lstrip()
                if not txt or (isinstance(c, list) and all(isinstance(b, dict) and b.get("type") == "tool_result"
                                                           for b in c)):
                    continue
                if txt.startswith(RECEIVED) or "<cross-session-message" in txt[:400]:
                    received += 1
                elif txt.startswith(NOTICE):
                    notices += 1
                else:
                    owner_turns += 1
                    if len(prompts) < 3:
                        prompts.append(txt[:300])
    tok = Counter()
    cost, priced, models, peak = 0.0, True, Counter(), 0
    for model, u in calls.values():
        models[model] += 1
        cc = u.get("cache_creation") or {}
        w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
        w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
        cw = int(u.get("cache_creation_input_tokens") or 0)
        if not (w5 or w1):
            w5 = cw
        i, o, cr = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
        tok["input"] += i
        tok["output"] += o
        tok["cache_read"] += cr
        tok["cache_write"] += cw
        tok["cache_write_1h"] += w1
        peak = max(peak, i + cr + cw)
        p = price(model, conf)
        for k, (limit, tp) in TIERS.items():
            if str(model or "").startswith(k) and i + cr + cw > limit:
                p = tp
        if p:
            cost += (i * p[0] + o * p[1] + cr * p[2] + w5 * p[0] * WRITE_5M + w1 * p[0] * WRITE_1H) / 1e6
        else:
            priced = False
    times.sort()
    active = 0.0
    for a, b in zip(times, times[1:]):
        gap = (b - a).total_seconds()
        if gap < 300:
            active += gap
    resolved_sid = session_id or (_agy_session_id_from_path(path) if is_agy else os.path.basename(path)[:-6])
    row = {
        "schema": SCHEMA, "source": "transcript", "session_id": resolved_sid,
        "cwd": cwd, "project": os.path.basename(cwd or ""), "cc_version": version,
        "entrypoint": entrypoint or ("agy" if is_agy else None),
        "branch": branches.most_common(1)[0][0] if branches else None,
        "model": models.most_common(1)[0][0] if models else None, "models": dict(models),
        "started": times[0].isoformat() if times else None, "ended": times[-1].isoformat() if times else None,
        "wall_s": round((times[-1] - times[0]).total_seconds()) if times else 0, "active_s": round(active),
        "api_calls": len(calls), "tokens": dict(tok), "context_peak": peak,
        "cost_usd": round(cost, 6) if priced else None,
        "owner_turns": owner_turns, "received": received, "notices": notices, "sent": sent, "sent_chars": sent_chars,
        "tool_calls": sum(tools.values()), "tools": dict(tools.most_common()), "kit_cmds": dict(kit_cmds.most_common()),
        "board": {k: v for k, v in board.items() if k != "tests_run"}, "tests_run": board.get("tests_run", 0),
        "compactions": compactions, "first_prompt": prompts[0] if prompts else (msg_prompts[0] if msg_prompts else ""),
    }
    subs = []
    if depth == 0:
        sdir = os.path.join(os.path.dirname(path), os.path.basename(path)[:-6], "subagents")
        for sp in sorted(glob.glob(os.path.join(sdir, "agent-*.jsonl"))):
            sub = parse_transcript(sp, conf, depth=1)
            try:
                with open(sp[:-6] + ".meta.json", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {}
            subs.append({"agent": os.path.basename(sp)[6:-6], "type": meta.get("agentType"),
                         "description": meta.get("description"), "model": sub["model"],
                         "api_calls": sub["api_calls"], "tokens": sub["tokens"], "cost_usd": sub["cost_usd"],
                         "tool_calls": sub["tool_calls"], "tools": sub["tools"], "context_peak": sub["context_peak"]})
        abs_self = os.path.abspath(path)
        for cid, cpath, meta in agy_children:
            if not cpath or not os.path.isfile(cpath) or os.path.abspath(cpath) == abs_self:
                continue
            sub = parse_transcript(cpath, conf, depth=1)
            subs.append({"agent": cid, "type": meta.get("type"), "description": meta.get("description"),
                         "model": sub["model"], "api_calls": sub["api_calls"], "tokens": sub["tokens"],
                         "cost_usd": sub["cost_usd"], "tool_calls": sub["tool_calls"], "tools": sub["tools"],
                         "context_peak": sub["context_peak"]})
    row["subagents"] = subs
    row["agents_spawned"] = sum(tools.get(n, 0) for n in AGENT_TOOLS)
    allc = [row["cost_usd"]] + [s["cost_usd"] for s in subs]
    row["cost_usd_all"] = round(sum(allc), 6) if None not in allc else None
    tot = Counter(tok)
    for s in subs:
        tot.update(s["tokens"])
    row["tokens_all"] = dict(tot)
    return row


# ---------------------------------------------------------------- role and part

PART_PROMPT = re.compile(r"docs/prompts/([A-Za-z0-9._-]+)\.md")
ROLE_FILE = re.compile(r"^[a-z]{1,3}\d+[a-z]?-(?:board-)?(orchestrator|clerk)$")
ROLE_POINTER = re.compile(r"--role (orchestrator|clerk|review)\b|\.kit/(?:claude-cli/)?(orchestrator|clerk|consultant)\.md|"
                          r"\.kit/claude-cli/takeover\.md|\byou are (?:the )?orchestrator\b|\bpost _(handover|orchestrator)\b", re.I)
PART_ID = re.compile(r"^([A-Za-z]{1,4}\d+[a-z]?(?:-[A-Z]\d?)?)\b")


def classify(row, title=None):
    """(role, part) from the branch, the title, the first prompt: orchestrator, clerk, review, part, other.
    A title that starts with a step id ("CL28 orchestrator answers...") is that part, whatever words follow."""
    br = str(row.get("branch") or "")
    if br == "HEAD":
        br = ""
    first = str(row.get("first_prompt") or "")
    if row.get("is_orchestrator"):
        return "orchestrator", None
    tl = re.sub(r"^(harmonize|board)\s+", "", str(title or row.get("title") or "").strip(), flags=re.I)
    words = (tl + " " + first[:300]).lower()
    m = PART_PROMPT.search(first)
    part = None
    if m:
        p = PART_ID.match(m.group(1))
        part = (p.group(1) if p else m.group(1)).upper()
    for prefix, role in (("orchestrator", "orchestrator"), ("clerk", "clerk"), ("review", "review"),
                         ("consultant", "review"), ("cr-", "review")):
        if br.startswith(prefix):
            return role, part
    if re.match(r"(CR[- ]|review\b)", tl, re.I):
        return "review", part
    if m:  # a role's own prompt file: O54-orchestrator.md, BC32-board-clerk.md, CR-x.md (not cl24-orchestrator-guard)
        name = m.group(1).lower()
        rm = ROLE_FILE.match(name)
        if rm:
            return rm.group(1), None if rm.group(1) == "orchestrator" else part
        if name.startswith("cr-"):
            return "review", part
    rp = ROLE_POINTER.search(first)
    if rp and not m:  # a hand-over line: `kit.py resume --role orchestrator`, .kit/claude-cli/clerk.md, takeover.md
        role = rp.group(1) or rp.group(2) or "orchestrator"
        return ("review" if role in ("review", "consultant") else role), part
    if m or br.startswith("part-"):
        if part is None and br.startswith("part-"):
            p = PART_ID.match(br[5:])
            part = p.group(1).upper() if p else None
        return "part", part
    p = PART_ID.match(tl)
    if p:
        if re.match(r"[\s\W]*(the\s+)?(board\s+)?clerk\b", tl[p.end():], re.I):  # "BC29 board clerk": a clerk
            return "clerk", p.group(1).upper()
        return "part", p.group(1).upper()
    for word, role in (("orchestrator", "orchestrator"), ("clerk", "clerk"), ("review", "review"),
                       ("reviewer", "review")):
        if re.search(r"\b%s\b" % word, words[:160]):
            return role, part
    return "other", part


# ---------------------------------------------------------------- the ledger

def run_dir(root):
    return os.path.join(root, ".kit", "run", "usage")


def save_local(root, row):
    d = run_dir(root)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.tmp" % row["session_id"])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(row, fh, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, "%s.json" % row["session_id"]))


def local_rows(root):
    out = []
    for p in sorted(glob.glob(os.path.join(run_dir(root), "*.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            pass
    return out


def _day(row):
    return str(row.get("started") or row.get("recorded_at") or "unknown")[:10]


def ledger_rows(root, ref=None):
    """The committed rows: every usage/*.jsonl on the ledger branch (origin's, else the local one)."""
    out = []
    for r in ([ref] if ref else ["origin/" + BRANCH, BRANCH]):
        rc, files = git(root, "ls-tree", "--name-only", r, "usage/")
        if rc:
            continue
        for f in files.splitlines():
            rc, body = git(root, "show", "%s:%s" % (r, f))
            for line in body.splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        break
    return out


def merge_rows(*groups):
    """One row per (session_id, source): the one recorded last."""
    best = {}
    for g in groups:
        for row in g:
            k = (row.get("session_id"), row.get("source"))
            if k not in best or str(row.get("recorded_at") or "") >= str(best[k].get("recorded_at") or ""):
                best[k] = row
    return list(best.values())


def push(root, rows=None, remote="origin", tries=4):
    """Upsert the rows into the ledger branch with plumbing (a temporary index: the working tree, HEAD and the
    real index are never touched), then push it, never forced; on a race, fetch and redo."""
    rows = local_rows(root) if rows is None else rows
    if not rows:
        return "nothing to push"
    for attempt in range(tries):
        git(root, "fetch", "-q", remote, "+refs/heads/%s:refs/remotes/%s/%s" % (BRANCH, remote, BRANCH))
        rc, parent = git(root, "rev-parse", "-q", "--verify", "refs/remotes/%s/%s^{commit}" % (remote, BRANCH))
        parent = parent if rc == 0 else ""
        old = ledger_rows(root, "refs/remotes/%s/%s" % (remote, BRANCH)) if parent else []
        merged = merge_rows(old, rows)
        if parent and sorted(json.dumps(r, sort_keys=True) for r in merged) == sorted(
                json.dumps(r, sort_keys=True) for r in old):
            return "up to date"
        by_day = {}
        for row in merged:
            by_day.setdefault(_day(row), []).append(row)
        fd, idx = tempfile.mkstemp(prefix="kit-usage-index-")
        os.close(fd)
        os.unlink(idx)
        env = dict(os.environ, GIT_INDEX_FILE=idx)
        try:
            for day, rs in sorted(by_day.items()):
                rs.sort(key=lambda r: (str(r.get("started") or ""), str(r.get("session_id"))))
                body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rs)
                rc, blob = git(root, "hash-object", "-w", "--stdin", inp=body)
                if rc:
                    return "hash-object failed"
                git(root, "update-index", "--add", "--cacheinfo", "100644,%s,usage/%s.jsonl" % (blob, day), env=env)
            rc, tree = git(root, "write-tree", env=env)
        finally:
            if os.path.exists(idx):
                os.unlink(idx)
        if rc:
            return "write-tree failed"
        args = ["commit-tree", tree, "-m", "usage: %d rows" % len(merged)]
        if parent:
            args[2:2] = ["-p", parent]
        cenv = dict(os.environ)
        for k, v in (("GIT_AUTHOR_NAME", "kit usage"), ("GIT_AUTHOR_EMAIL", "kit@localhost")):
            cenv.setdefault(k, v)
            cenv.setdefault(k.replace("AUTHOR", "COMMITTER"), v)
        rc, commit = git(root, "commit-tree", *args[1:], env=cenv)
        if rc:
            return "commit-tree failed"
        git(root, "update-ref", "refs/heads/" + BRANCH, commit)
        try:
            r = subprocess.run(["git", "-C", root, "push", "-q", remote, "%s:refs/heads/%s" % (commit, BRANCH)],
                               capture_output=True, text=True, timeout=60)
            rc, err = r.returncode, r.stderr
        except (OSError, subprocess.SubprocessError) as exc:
            rc, err = 1, str(exc)
        if rc == 0:
            git(root, "update-ref", "refs/remotes/%s/%s" % (remote, BRANCH), commit)
            return "pushed %d rows (%s)" % (len(merged), commit[:7])
        if not re.search(r"rejected|fetch first|non-fast-forward|cannot lock ref", err):
            return "push failed (%s); the rows stay in .kit/run/usage/ and the local branch %s" % (
                err.strip().splitlines()[-1][:160] if err.strip() else "no output", BRANCH)
        time.sleep(1 + attempt)
    return "push refused %d times; the rows stay in .kit/run/usage/" % tries


def push_allowed(root, conf):
    if str(os.environ.get("KIT_USAGE_PUSH", "")).lower() in ("0", "false", "no"):
        return False
    return conf.get("push", True) is not False


def orchestrator_session(root):
    """The session id the board's `_orchestrator` post names (the local board's store), or None."""
    try:
        import sqlite3
        db = os.path.join(root, ".kit", "board_data", "board.db")
        if not os.path.isfile(db):
            return None
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
        try:
            got = con.execute("select data from docs where collection='posts' and doc_id='_orchestrator'").fetchone()
        finally:
            con.close()
        return (json.loads(got[0]) or {}).get("session_id") if got else None
    except Exception:
        return None


def git_work(repo, started, ended, kit_cmds=None, tests_run=None, ccr_id=None):
    """What the session made, the denominator for cost: ITS commits, their files and lines, tests run, Done checks.
    A cloud session's commits are the ones carrying its own `Claude-Session` trailer (any time, any branch here).
    Without that id (a computer's CLI session): this branch's first-parent commits between its first and last event
    that carry no other session's trailer. Shallow-boundary commits are never counted (git shows their whole tree)."""
    out = {"commits": 0, "files": 0, "lines_added": 0, "lines_deleted": 0, "tests_run": int(tests_run or 0),
           "done_checks": int((kit_cmds or {}).get("kit done", 0)), "by": "trailer" if ccr_id else "window"}
    boundary = set()
    rc, sp = git(repo, "rev-parse", "--git-path", "shallow")
    if rc == 0 and sp:
        try:
            with open(sp if os.path.isabs(sp) else os.path.join(repo, sp), encoding="utf-8") as fh:
                boundary = set(fh.read().split())
        except OSError:
            pass
    if ccr_id:
        args = ["log", "--all", "--no-merges"]
    elif started and ended:
        args = ["log", "--first-parent", "--no-merges", "--since=" + str(started), "--until=" + str(ended), "HEAD"]
    else:
        return out
    rc, log = git(repo, *args, "--format=@%H %(trailers:key=Claude-Session,valueonly,separator=;)", "--numstat")
    if rc:
        return out
    files, take = set(), False
    for line in log.splitlines():
        if line.startswith("@"):
            sha, _, tr = line[1:].partition(" ")
            ids = set(re.findall(r"session_[A-Za-z0-9]+", tr))
            take = sha not in boundary and ((ccr_id in ids) if ccr_id else not ids)
            out["commits"] += take
        elif take and "\t" in line:
            a, d, f = (line.split("\t") + ["", ""])[:3]
            files.add(f)
            out["lines_added"] += int(a) if a.isdigit() else 0
            out["lines_deleted"] += int(d) if d.isdigit() else 0
    out["files"] = len(files)
    return out


def work_by_trailer(repos, refs=("--all",)):
    """{session_id: work} from the `Claude-Session: .../session_<id>` trailer of every commit (merges excluded), in
    every repo given: the work measure for ccr rows, whose transcript is gone."""
    out = {}
    seen = set()
    for repo in repos:
        rc, log = git(repo, "log", *refs, "--no-merges", "--format=@%H %(trailers:key=Claude-Session,valueonly,separator=;)",
                      "--numstat")
        if rc:
            continue
        cur = None
        for line in log.splitlines():
            if line.startswith("@"):
                sha, _, tr = line[1:].partition(" ")
                m = re.search(r"(session_[A-Za-z0-9]+)", tr)
                cur = None
                if m and sha not in seen:
                    seen.add(sha)
                    cur = out.setdefault(m.group(1), {"commits": 0, "files": 0, "lines_added": 0, "lines_deleted": 0,
                                                       "_files": set()})
                    cur["commits"] += 1
            elif cur is not None and "\t" in line:
                a, d, f = (line.split("\t") + ["", ""])[:3]
                cur["_files"].add(f)
                cur["lines_added"] += int(a) if a.isdigit() else 0
                cur["lines_deleted"] += int(d) if d.isdigit() else 0
    for w in out.values():
        w["files"] = len(w.pop("_files"))
    return out


def record(root, transcript, role=None, part=None, title=None, conf=None, session_id=None):
    conf = kit_conf(root) if conf is None else conf
    row = parse_transcript(transcript, conf)
    if session_id and row.get("session_id") in (None, "", "transcript", "transcript_full"):
        row["session_id"] = str(session_id)
    if not row.get("cwd"):
        row["cwd"] = os.path.abspath(root)
        row["project"] = os.path.basename(row["cwd"])
    if not row.get("branch"):
        rc, br = git(root, "rev-parse", "--abbrev-ref", "HEAD")
        if rc == 0 and br and br != "HEAD":
            row["branch"] = br
    rid = str(os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or "")
    # only when the transcript is this very session's (a test or a backfill run inside a cloud session is not)
    if rid.startswith("cse_") and os.environ.get("CLAUDE_CODE_SESSION_ID") == row.get("session_id"):
        row["ccr_session_id"] = "session_" + rid[4:]       # the cloud session's own id, as its commit trailers carry it
    osid = orchestrator_session(root)
    if osid and osid in (row.get("session_id"), row.get("ccr_session_id"), os.environ.get("KIT_SESSION_NAME")):
        row["is_orchestrator"] = True
    r, p = classify(row, title)
    env_role = os.environ.get("KIT_ROLE") if os.environ.get("KIT_ROLE") in ("orchestrator", "clerk", "review", "part") else None
    if env_role == "orchestrator" and r == "part" and p and not row.get("is_orchestrator"):
        env_role = None                                # agy part subagents inherit KIT_ROLE=orchestrator from kit/up
    row["role"], row["part"] = role or env_role or r, part or p
    # the work is counted in the project the row is recorded for: a transcript's cwd moves (/home/user after a second
    # repo was attached) and need not be a repository at all
    row["work"] = git_work(root,
                           row.get("started"), row.get("ended"), row.get("kit_cmds"), row.get("tests_run"),
                           row.get("ccr_session_id"))
    row["kit_commit"], row["kit_date"] = kit_version(root)
    row["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["recorded_by"] = "usage.py"
    save_local(root, row)
    return row


# ---------------------------------------------------------------- claude-code-remote records

def ccr_row(rec):
    """A saved get_session record (the `ccr` object, or the flat row the backfill wrote) -> a row."""
    c = rec.get("ccr") if isinstance(rec.get("ccr"), dict) else rec
    meta = c.get("external_metadata") or {}
    u = c.get("usage") if isinstance(c.get("usage"), dict) else (meta.get("usage") or {})
    ctx = meta.get("context_usage") or {}
    used = c.get("used_tokens") if c.get("used_tokens") is not None else ctx.get("used_tokens")
    br = c.get("branches") or list((meta.get("current_branches") or {}).values())
    model = c.get("model") or (c.get("session_context") or {}).get("model") or c.get("configured_model")
    t0, t1 = _ts(c.get("created_at") or ""), _ts(c.get("updated_at") or "")
    row = {
        "schema": SCHEMA, "source": "ccr", "session_id": c.get("id"), "title": c.get("title"),
        "parent_session_id": c.get("parent_session_id"), "tags": c.get("tags") or [],
        "branch": (br[0] if isinstance(br, list) and br else br) or None, "model": model,
        "started": t0.isoformat() if t0 else None, "ended": t1.isoformat() if t1 else None,
        "wall_s": round((t1 - t0).total_seconds()) if t0 and t1 else None,
        "tokens": {"input": u.get("input_tokens"), "output": u.get("output_tokens"),
                   "cache_read": u.get("cache_read_tokens"), "cache_write": u.get("cache_write_tokens")},
        "cost_usd": u.get("cost_usd"), "context_now": used, "first_prompt": "",
        "sources": c.get("sources"), "worker_epoch": c.get("worker_epoch"),
    }
    row["role"], row["part"] = classify(row, c.get("title"))
    row["cost_usd_all"] = row["cost_usd"]
    row["tokens_all"] = row["tokens"]
    row["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["recorded_by"] = "usage.py import-ccr"
    return row


def import_ccr(root, path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            row = ccr_row(rec)
            if row["session_id"] and (row["tokens"].get("output") is not None or row["cost_usd"] is not None):
                rows.append(row)
    d = os.path.join(run_dir(root), "ccr")
    os.makedirs(d, exist_ok=True)
    for row in rows:
        with open(os.path.join(d, "%s.json" % row["session_id"]), "w", encoding="utf-8") as fh:
            json.dump(row, fh, ensure_ascii=False)
    return rows


def _read_rows(files):
    rows = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def all_rows(root, extra=()):
    rows = local_rows(root)
    for p in glob.glob(os.path.join(run_dir(root), "ccr", "*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                rows.append(json.load(fh))
        except (OSError, ValueError):
            pass
    for f in extra:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return merge_rows(ledger_rows(root), rows)


# ---------------------------------------------------------------- compare

def cut_time(root, spec, kit_repo=None, rows=()):
    """A kit commit or a date/datetime. A commit's time: from git (the kit's repo, then the project); in a shallow
    clone that lacks it, from the rows that ran under it (their kit_date) or the project's manifest; last, one
    bounded fetch of that commit."""
    repos = [r for r in (kit_repo, os.path.dirname(HERE), root) if r]
    for repo in repos:
        rc, out = git(repo, "log", "-1", "--format=%cI", spec, "--")
        if rc == 0 and out:
            return _ts(out), "commit %s (%s)" % (spec, out)
    if re.fullmatch(r"[0-9a-f]{6,40}", spec):
        for r in rows:
            kc, kd = str(r.get("kit_commit") or ""), r.get("kit_date")
            if kd and kc and (kc.startswith(spec) or spec.startswith(kc)):
                return _ts(kd), "commit %s (%s, from the ledger's rows)" % (spec, kd)
        kc, kd = kit_version(root)
        if kd and kc and (kc.startswith(spec) or spec.startswith(kc)):
            return _ts(kd), "commit %s (%s, from .kit/manifest.json)" % (spec, kd)
        for repo in repos:
            if git(repo, "fetch", "-q", "--depth=1", "origin", spec)[0] == 0:
                rc, out = git(repo, "log", "-1", "--format=%cI", "FETCH_HEAD", "--")
                if rc == 0 and out:
                    return _ts(out), "commit %s (%s, fetched)" % (spec, out)
        raise SystemExit("usage compare: commit %s is not in this clone, the ledger or the manifest, and could not "
                         "be fetched; give its date instead (--before 2026-11-23T01:25:19+00:00)" % spec)
    t = _ts(spec if "T" in spec or len(spec) > 10 else spec + "T00:00:00+00:00")
    if t is None:
        raise SystemExit("usage compare: %r is neither a commit nor a date" % spec)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t, "date %s" % t.isoformat()


def _when(row):
    return _ts(row.get("kit_date") or "") or _ts(row.get("started") or "")


def _cost(r):
    return float(r.get("cost_usd_all") if r.get("cost_usd_all") is not None else (r.get("cost_usd") or 0))


def _tot(d):
    d = d or {}
    return sum(int(d.get(k) or 0) for k in ("input", "output", "cache_read", "cache_write"))


METRICS = [
    ("tokens (all, incl. cache)", lambda r: _tot(r.get("tokens_all") or r.get("tokens"))),
    ("output tokens", lambda r: (r.get("tokens_all") or r.get("tokens") or {}).get("output")),
    ("cache read", lambda r: (r.get("tokens_all") or r.get("tokens") or {}).get("cache_read")),
    ("cost $", lambda r: r.get("cost_usd_all") if r.get("cost_usd_all") is not None else r.get("cost_usd")),
    ("cost $ per owner turn", lambda r: (r.get("cost_usd_all") or r.get("cost_usd") or 0) / r["owner_turns"]
     if r.get("owner_turns") else None),
    ("cost $ per hour", lambda r: (r.get("cost_usd_all") if r.get("cost_usd_all") is not None else r.get("cost_usd"))
     / (r["wall_s"] / 3600.0) if (r.get("wall_s") or 0) >= 600 else None),
    ("commits", lambda r: (r.get("work") or {}).get("commits") if r.get("work") else None),
    ("lines changed", lambda r: ((r["work"].get("lines_added", 0) + r["work"].get("lines_deleted", 0))
                                 if r.get("work") else None)),
    ("cost $ per commit", lambda r: _cost(r) / r["work"]["commits"] if (r.get("work") or {}).get("commits") else None),
    ("cost $ per 100 lines changed", lambda r: 100.0 * _cost(r) / (r["work"]["lines_added"] + r["work"]["lines_deleted"])
     if r.get("work") and (r["work"].get("lines_added", 0) + r["work"].get("lines_deleted", 0)) else None),
    ("tests run", lambda r: r.get("tests_run")),
    ("API calls", lambda r: r.get("api_calls")),
    ("tool calls", lambda r: r.get("tool_calls")),
    ("board bytes (read+write)", lambda r: ((r.get("board") or {}).get("read_bytes", 0)
                                            + (r.get("board") or {}).get("write_bytes", 0)
                                            + (r.get("board") or {}).get("cli_bytes", 0)) if r.get("board") is not None else None),
    ("context (peak; ccr: last)", lambda r: r.get("context_peak") or r.get("context_now")),
    ("wall minutes", lambda r: r["wall_s"] / 60 if r.get("wall_s") is not None else None),
    ("subagents", lambda r: r.get("agents_spawned")),
]


def summarize(rows):
    out = {"n": len(rows)}
    for name, f in METRICS:
        vals = []
        for r in rows:
            try:
                v = f(r)
            except (TypeError, ZeroDivisionError, KeyError):
                v = None
            if v is not None:
                vals.append(float(v))
        out[name] = {"n": len(vals), "median": statistics.median(vals) if vals else None,
                     "total": sum(vals) if vals else None}
    return out


def compare(rows, t_before, t_after, role=None, project=None, prefixes=None):
    groups = {"before": [], "after": []}
    for r in rows:
        if role and r.get("role") != role:
            continue
        if prefixes and not str(r.get("part") or "").upper().startswith(tuple(x.upper() for x in prefixes)):
            continue
        if project and r.get("project") != project and project not in str(r.get("sources") or ""):
            continue
        w = _when(r)
        if w is None:
            continue
        if w <= t_before:
            groups["before"].append(r)
        if w >= t_after:
            groups["after"].append(r)
    res = {}
    for g, rs in groups.items():
        by_role = {}
        for r in rs:
            by_role.setdefault(r.get("role") or "other", []).append(r)
        res[g] = {k: summarize(v) for k, v in sorted(by_role.items())}
    return res


def _fmt(v, name):
    if v is None:
        return "-"
    if "$" in name:
        return "%.2f" % v
    if v >= 1e6:
        return "%.1fM" % (v / 1e6)
    if v >= 1e4:
        return "%.0fk" % (v / 1e3)
    return ("%.1f" % v) if v != int(v) else "%d" % v


def render(res, label_b, label_a):
    roles = sorted(set(res["before"]) | set(res["after"]))
    L = ["# Usage: before (%s) vs after (%s)" % (label_b, label_a), ""]
    for role in roles:
        b, a = res["before"].get(role), res["after"].get(role)
        L += ["## %s (sessions: before %d, after %d)" % (role, b["n"] if b else 0, a["n"] if a else 0), "",
              "| per session | before median (n) | after median (n) | change | before total | after total |",
              "|---|---|---|---|---|---|"]
        for name, _ in METRICS:
            mb = (b or {}).get(name) or {}
            ma = (a or {}).get(name) or {}
            ch = "-"
            if mb.get("median") and ma.get("median") is not None:
                ch = "%+.0f%%" % (100.0 * (ma["median"] - mb["median"]) / mb["median"])
            L.append("| %s | %s (%d) | %s (%d) | %s | %s | %s |" % (
                name, _fmt(mb.get("median"), name), mb.get("n", 0), _fmt(ma.get("median"), name), ma.get("n", 0), ch,
                _fmt(mb.get("total"), name), _fmt(ma.get("total"), name)))
        L.append("")
    if not roles:
        L.append("no rows on either side of the cut")
    return "\n".join(L)


# ---------------------------------------------------------------- the hook

def hook(stdin_text, root=None):
    """Stop / SessionEnd: record this session's row (silent, never blocks, never prints to the model)."""
    try:
        data = json.loads(stdin_text or "{}")
    except ValueError:
        data = {}
    cid = data.get("conversationId") or data.get("conversation_id") or data.get("session_id")
    tp = data.get("transcript_path") or data.get("transcriptPath") or (own_transcript(cid) if cid else None)
    wps = data.get("workspacePaths") if isinstance(data.get("workspacePaths"), list) else []
    root = root or project_root(data.get("cwd") or (wps[0] if wps else None))
    if not tp or not os.path.isfile(tp):
        return None
    conf = kit_conf(root)
    if conf.get("record", True) is False:
        return None
    row = record(root, tp, conf=conf, session_id=cid)
    event = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    if push_allowed(root, conf):
        stamp = os.path.join(run_dir(root), ".pushed")
        try:
            last = os.path.getmtime(stamp)
        except OSError:
            last = 0
        every = float(conf.get("push_minutes", 0)) * 60
        if event == "SessionEnd" or time.time() - last >= every:
            open(stamp, "a").close()
            os.utime(stamp, None)
            if event == "SessionEnd" or os.environ.get("KIT_USAGE_PUSH_WAIT") == "1":
                push_locked(root)
            else:  # every Stop: the push runs on, detached, so the turn never waits for the network
                subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--root", root, "push"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, cwd=root)
    return row


def push_locked(root):
    """push() unless another push of this project is running (a lock file younger than 2 minutes)."""
    os.makedirs(run_dir(root), exist_ok=True)
    lock = os.path.join(run_dir(root), ".push.lock")
    try:
        if time.time() - os.path.getmtime(lock) > 120:
            os.unlink(lock)
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return "another push is running"
    try:
        os.close(fd)
        return push(root)
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


# ---------------------------------------------------------------- a session records itself, whatever its settings

def own_transcript(session_id=None):
    """This session's transcript: Claude Code (~/.claude/projects/*/<sid>.jsonl) or agy
    (~/.gemini/antigravity-cli/brain/<sid>/.system_generated/logs/transcript.jsonl)."""
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("AGY_CONVERSATION_ID") or os.environ.get("KIT_SESSION_ID")
    if not sid or not re.fullmatch(r"[A-Za-z0-9._-]+", sid):
        return None
    home = os.path.expanduser("~")
    found = glob.glob(os.path.join(home, ".claude", "projects", "*", sid + ".jsonl"))
    agy_tp = os.path.join(home, ".gemini", "antigravity-cli", "brain", sid, ".system_generated", "logs", "transcript.jsonl")
    if os.path.isfile(agy_tp):
        found.append(agy_tp)
    return max(found, key=os.path.getmtime) if found else None


def record_self(root, push_too=True, every=60):
    """For a session whose project hooks do not run (one started on two repos reads neither repo's
    .claude/settings.json): kit.py, end_session.py and web_takeover save call this; it records this session's row
    from its own transcript (at most every `every` seconds) and pushes it detached. Silent; never raises."""
    try:
        tp = own_transcript()
        if not tp:
            return None
        conf = kit_conf(root)
        if conf.get("record", True) is False:
            return None
        stamp = os.path.join(run_dir(root), ".self")
        try:
            if time.time() - os.path.getmtime(stamp) < every:
                return None
        except OSError:
            pass
        os.makedirs(run_dir(root), exist_ok=True)
        open(stamp, "a").close()
        os.utime(stamp, None)
        row = record(root, tp, conf=conf)
        if push_too and push_allowed(root, conf):
            subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--root", root, "push"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, cwd=root)
        return row
    except Exception:
        return None


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="the project (default: from the working directory)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("record")
    s.add_argument("--transcript", required=True)
    s.add_argument("--role")
    s.add_argument("--part")
    s.add_argument("--title")
    s.add_argument("--push", action="store_true")
    s.add_argument("--json", action="store_true")
    sp.add_parser("hook")
    s = sp.add_parser("self", help="record this session's row from its own transcript (no hook needed)")
    s.add_argument("--no-push", action="store_true")
    s = sp.add_parser("import-ccr")
    s.add_argument("file")
    s.add_argument("--push", action="store_true")
    sp.add_parser("push")
    s = sp.add_parser("work-from-git", help="add each ccr row's commits and lines, by the Claude-Session trailer")
    s.add_argument("rows")
    s.add_argument("--repo", action="append", required=True)
    s.add_argument("--out", required=True)
    s = sp.add_parser("compare")
    s.add_argument("--before", required=True)
    s.add_argument("--after")
    s.add_argument("--role")
    s.add_argument("--project")
    s.add_argument("--part-prefix", action="append", default=[], help="only parts whose id starts so (e.g. CL, BD)")
    s.add_argument("--rows", action="append", default=[], help="extra JSONL rows (e.g. a backfill)")
    s.add_argument("--only", action="store_true", help="only the --rows files (a fixed set, for a reproducible check)")
    s.add_argument("--kit-repo")
    s.add_argument("--json", action="store_true")
    s = sp.add_parser("show")
    s.add_argument("--session")
    s.add_argument("--rows", action="append", default=[])
    a = ap.parse_args(argv)
    root = project_root(a.root)
    if a.cmd == "hook":
        try:
            hook(sys.stdin.read(), root if a.root else None)
        except Exception as exc:  # a hook never breaks a session
            try:
                os.makedirs(run_dir(root), exist_ok=True)
                with open(os.path.join(run_dir(root), "errors.log"), "a", encoding="utf-8") as fh:
                    fh.write("%s %r\n" % (datetime.now(timezone.utc).isoformat(), exc))
            except OSError:
                pass
        return 0
    if a.cmd == "self":
        row = record_self(root, push_too=not a.no_push, every=0)
        print(one_line(row) if row else "usage self: no transcript for CLAUDE_CODE_SESSION_ID here")
        return 0
    if a.cmd == "record":
        row = record(root, a.transcript, a.role, a.part, a.title)
        if a.json:
            print(json.dumps(row, indent=1, ensure_ascii=False))
        else:
            print(one_line(row))
        if a.push:
            print(push(root, [row]))
        return 0
    if a.cmd == "import-ccr":
        rows = import_ccr(root, a.file)
        print("import-ccr: %d rows (roles %s)" % (len(rows), dict(Counter(r["role"] for r in rows))))
        if a.push:
            print(push(root, rows))
        return 0
    if a.cmd == "work-from-git":
        work = work_by_trailer(a.repo)
        rows = _read_rows([a.rows])
        n = 0
        for r in rows:
            if r.get("source") == "ccr":
                r["work"] = dict(work.get(r["session_id"]) or {"commits": 0, "files": 0, "lines_added": 0,
                                                               "lines_deleted": 0})
                n += bool(r["work"]["commits"])
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))
        print("work-from-git: %d of %d ccr rows have commits (%d sessions in the trailers)" % (
            n, sum(r.get("source") == "ccr" for r in rows), len(work)))
        return 0
    if a.cmd == "push":                                      # end_session.py calls it too: the owner's "keep it here" holds for every caller
        print(push_locked(root) if push_allowed(root, kit_conf(root)) else "kept on this computer (usage.push is off)")
        return 0
    if a.cmd == "compare":
        rows = merge_rows(all_rows(root, a.rows) if not a.only else [], _read_rows(a.rows))
        tb, lb = cut_time(root, a.before, a.kit_repo, rows)
        ta, la = cut_time(root, a.after or a.before, a.kit_repo, rows)
        res = compare(rows, tb, ta, a.role, a.project, a.part_prefix)
        print(json.dumps(res, indent=1) if a.json else render(res, lb, la))
        return 0
    if a.cmd == "show":
        for r in sorted(all_rows(root, a.rows), key=lambda r: str(r.get("started"))):
            if not a.session or str(r.get("session_id", "")).startswith(a.session):
                print(one_line(r))
        return 0
    return 1


def one_line(r):
    t = r.get("tokens_all") or r.get("tokens") or {}
    c = r.get("cost_usd_all") if r.get("cost_usd_all") is not None else r.get("cost_usd")
    return "%s %-12s %-6s %-5s %s out=%s cread=%s calls=%s tools=%s turns=%s $%s" % (
        str(r.get("started") or "?")[:16], r.get("role"), r.get("part") or "-", r.get("source"),
        str(r.get("session_id"))[:12], t.get("output"), t.get("cache_read"), r.get("api_calls", "-"),
        r.get("tool_calls", "-"), r.get("owner_turns", "-"), ("%.2f" % c) if c is not None else "?")


if __name__ == "__main__":
    sys.exit(main())
