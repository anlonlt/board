#!/usr/bin/env python3
"""Minimal zero-dependency MCP (Model Context Protocol) stdio server for board kit.

Speaks JSON-RPC 2.0 over stdin/stdout (newline-delimited JSON) using Python 3 stdlib only.
Exposes:
  - board_inbox(runner?, for_owner?, since?)
  - board_queue(runner?, for_owner?)
  - board_post(id, title, body, column?, part?, runner?, for_owner?, priority?)
  - board_reply(post_id, text, comment_id?, sender?)
  - board_search(query, limit?)

Run:
    python3 -I .kit/mcp_stdio.py [--root .] [--board-url http://127.0.0.1:8765] [--token TOK]
"""
import argparse
import json
import os
import subprocess
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "board-kit-mcp", "version": "0.1.0"}

TOOLS = [
    {
        "name": "board_inbox",
        "description": "Fetch unread owner comments, likes, option taps, and optionally open cards attributed to a runner or person.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runner": {"type": "string", "description": "Filter assigned cards to this runner (e.g. lmarena, claude-chrome, agy)"},
                "for_owner": {"type": "string", "description": "Filter assigned cards to this person/role"},
                "since": {"type": "string", "description": "ISO timestamp lower bound"},
            },
        },
    },
    {
        "name": "board_queue",
        "description": "List open cards attributed to a runner or person from local SQLite or board-mirror (no running server required).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runner": {"type": "string"},
                "for_owner": {"type": "string"},
                "unblocked_only": {"type": "boolean", "description": "Exclude cards with unresolved blocked_by dependencies"},
            },
        },
    },
    {
        "name": "board_post",
        "description": "Create a board post via board_cli.py set posts <id>.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "title", "body"],
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "column": {"type": "string", "enum": ["todo", "question", "info", "done"]},
                "options": {"type": "array", "items": {"type": "string"}},
                "default_option": {"type": "string"},
                "deadline": {"type": "string"},
                "needs": {"type": "string"},
                "minutes": {"type": "integer"},
                "part": {"type": "string"},
                "runner": {"type": "string"},
                "for_owner": {"type": "string"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
            },
        },
    },
    {
        "name": "board_reply",
        "description": "Post a reply under a board card and optionally mark the owner's comment read and ack its thread.",
        "inputSchema": {
            "type": "object",
            "required": ["post_id", "text"],
            "properties": {
                "post_id": {"type": "string"},
                "text": {"type": "string"},
                "comment_id": {"type": "string"},
                "sender": {"type": "string", "default": "agent"},
            },
        },
    },
    {
        "name": "board_search",
        "description": "Search posts, comments, and committed memory markdown files.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "board_step_new",
        "description": "Create or adopt a train step post and generate its prompt (docs/prompts/<post-id>.md). The train IS the task list.",
        "inputSchema": {
            "type": "object",
            "required": ["train", "part", "title"],
            "properties": {
                "train": {"type": "string", "description": "Project/train id (e.g. planner)"},
                "part": {"type": "string", "description": "Step/part code (e.g. PL4)"},
                "title": {"type": "string", "description": "Short step title"},
                "task": {"type": "string", "description": "Goal, bars, and holds (the task only; kit adds how a part works)"},
                "parent": {"type": "string", "description": "Optional preceding part code"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Owner or review comment ids this step answers"},
                "model": {"type": "string", "description": "Optional model override"},
                "review": {"type": "boolean", "default": True, "description": "Set false to skip automatic review on completion"},
                "cli": {"type": "string", "enum": ["agy", "claude"], "description": "Target CLI for prompt/launch format"},
            },
        },
    },
    {
        "name": "board_step_update",
        "description": "Update a train step's lifecycle: start (checks device session cap), complete (marks complete + starts review), or drop (only if unstarted).",
        "inputSchema": {
            "type": "object",
            "required": ["post_id", "action"],
            "properties": {
                "post_id": {"type": "string", "description": "Step post id"},
                "action": {"type": "string", "enum": ["start", "complete", "drop"]},
                "cli": {"type": "string", "enum": ["agy", "claude"]},
            },
        },
    },
    {
        "name": "board_resume",
        "description": "Rebuild orchestrator state from the board alone: active trains, open steps, session status, device capacity, and unanswered owner comments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cli": {"type": "string", "enum": ["agy", "claude"]},
            },
        },
    },
    {
        "name": "board_train",
        "description": "Build a low-token Train-as-Tasklist context pack: ancestor zoom-out + current-step zoom-in.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {"type": "string", "description": "Train id, part code, or post id"},
                "zoom": {"type": "string", "description": "Optional part or post id to zoom into"},
                "as_json": {"type": "boolean", "default": False},
            },
        },
    },
]


class MCPServer:
    def __init__(self, root_dir, board_url=None, token=None):
        self.root = os.path.abspath(root_dir)
        self.board_url = board_url
        self.token = token
        self.cli_py = self._find("board_cli.py", [os.path.join(self.root, ".kit", "board_cli.py"), os.path.join(os.path.dirname(__file__), "board_cli.py")])
        self.kit_py = self._find("kit.py", [os.path.join(self.root, ".kit", "kit.py"), os.path.join(self.root, "scripts", "kit.py")])

    @staticmethod
    def _find(name, candidates):
        for p in candidates:
            if os.path.isfile(p):
                return p
        return candidates[0]

    def _run_py(self, script, args):
        cmd = [sys.executable, "-I", script] + args
        r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or ("exit %d" % r.returncode)).strip()
        return True, r.stdout.strip()

    # CL30: an MCP call passes no agy hook, so the brief guard (kit_brief_guard.py, CL28) is held here: board_resume
    # stamps .kit/run/brief/last, and the orchestrator's board_reply waits for a stamp younger than kit.brief.max_age_min
    def _brief_dir(self):
        return os.path.join(self.root, ".kit", "run", "brief")

    def _stamp_brief(self):
        from datetime import datetime, timezone
        try:
            os.makedirs(self._brief_dir(), exist_ok=True)
            with open(os.path.join(self._brief_dir(), "last"), "w", encoding="utf-8") as fh:
                fh.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
        except OSError:
            pass

    def _brief_refusal(self, args):
        role = os.environ.get("KIT_ROLE") or ""
        if str(args.get("sender") or "") != "orchestrator" and role != "orchestrator":
            return ""
        try:
            with open(os.path.join(self.root, ".kit", "config.json"), encoding="utf-8") as fh:
                cap = float(((json.load(fh).get("kit") or {}).get("brief") or {}).get("max_age_min", 15))
        except (OSError, ValueError, TypeError, AttributeError):
            cap = 15.0
        try:
            import time
            age = (time.time() - os.path.getmtime(os.path.join(self._brief_dir(), "last"))) / 60.0
        except OSError:
            age = None
        if age is not None and age <= cap:
            return ""
        return ("NOT SENT: you answer the owner only from a fresh read of the board (the kit's guard, CL28/CL30). Last "
                "read: %s. Call board_resume first (every train's open steps, questions, the owner's unanswered "
                "comments), answer from it, then board_reply again." % (
                    "never" if age is None else "%d min ago (more than %d)" % (age, cap)))

    def _cli_base_args(self):
        out = []
        if self.board_url:
            out.extend(["--url", self.board_url])
        if self.token:
            out.extend(["--token", self.token])
        return out

    def call_tool(self, name, args):
        args = args or {}
        if name == "board_inbox":
            cmd = self._cli_base_args() + ["inbox"]
            if args.get("since"):
                cmd.extend(["--since", str(args["since"])])
            if args.get("runner"):
                cmd.extend(["--runner", str(args["runner"])])
            if args.get("for_owner"):
                cmd.extend(["--for", str(args["for_owner"])])
            return self._run_py(self.cli_py, cmd)
        if name == "board_queue":
            cmd = ["queue"]
            if args.get("runner"):
                cmd.extend(["--runner", str(args["runner"])])
            if args.get("for_owner"):
                cmd.extend(["--for", str(args["for_owner"])])
            if args.get("unblocked_only"):
                cmd.append("--unblocked")
            return self._run_py(self.kit_py, cmd)
        if name == "board_search":
            cmd = ["search", str(args.get("query") or ""), "--limit", str(int(args.get("limit") or 20))]
            return self._run_py(self.kit_py, cmd)
        if name == "board_step_new":
            cmd = ["step", "new", "--train", str(args["train"]), "--part", str(args["part"]),
                   "--title", str(args.get("title") or args["part"]), "--task", str(args.get("task") or "")]
            if args.get("parent"):
                cmd.extend(["--parent", str(args["parent"])])
            for src in (args.get("sources") or []):
                cmd.extend(["--source", str(src)])
            if args.get("model"):
                cmd.extend(["--model", str(args["model"])])
            if args.get("review") is False:
                cmd.append("--no-review")
            if args.get("cli"):
                cmd.extend(["--cli", str(args["cli"])])
            return self._run_py(self.kit_py, cmd)
        if name == "board_step_update":
            action = str(args.get("action") or "")
            pid = str(args.get("post_id") or "")
            if action not in ("start", "complete", "drop") or not pid:
                return False, "board_step_update requires post_id and action in (start, complete, drop)"
            if action == "start":
                cap_ok, cap_msg = self._run_py(self.kit_py, ["capacity", "--gate"])
                if not cap_ok and "NO FREE SLOT" in cap_msg:
                    return False, "Post %s stays planned: %s" % (pid, cap_msg)
            cmd = ["step", action, pid]
            if args.get("cli"):
                cmd.extend(["--cli", str(args["cli"])])
            return self._run_py(self.kit_py, cmd)
        if name == "board_resume":
            cmd = ["resume"]
            if args.get("cli"):
                cmd.extend(["--cli", str(args["cli"])])
            ok, out = self._run_py(self.kit_py, cmd)
            if ok:
                self._stamp_brief()
            return ok, out
        if name == "board_train":
            cmd = ["train", str(args["target"])]
            if args.get("zoom"):
                cmd.extend(["--zoom", str(args["zoom"])])
            if args.get("as_json"):
                cmd.append("--json")
            return self._run_py(self.kit_py, cmd)
        if name == "board_reply":
            why = self._brief_refusal(args)
            if why:
                return False, why
            cmd = self._cli_base_args() + ["reply", str(args["post_id"]), "--text", str(args["text"]), "--from", str(args.get("sender") or "agent")]
            if args.get("comment_id"):
                cmd.extend(["--comment-id", str(args["comment_id"])])
            return self._run_py(self.cli_py, cmd)
        if name == "board_post":
            from datetime import datetime, timezone
            doc = {
                "title": str(args["title"]),
                "body": str(args["body"]),
                "priority": int(args.get("priority") or 2),
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for k in ("column", "options", "default_option", "deadline", "needs", "minutes"):      # no column: the gate picks the lane
                if args.get(k):
                    doc[k] = args[k]
            if args.get("part"):
                doc["part"] = str(args["part"])
            if args.get("runner"):
                doc["runner"] = str(args["runner"])
            if args.get("for_owner"):
                doc["for"] = str(args["for_owner"])
            cmd = self._cli_base_args() + ["set", "posts", str(args["id"]), "--data", json.dumps(doc, ensure_ascii=False)]
            return self._run_py(self.cli_py, cmd)
        return False, "unknown tool: %s" % name

    def handle(self, req):
        method = req.get("method")
        rid = req.get("id")
        if method == "notifications/initialized" or rid is None:
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            }
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = req.get("params") or {}
            ok, text = self.call_tool(params.get("name"), params.get("arguments"))
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": not ok,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32601, "message": "Method not found: %s" % method},
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stdlib MCP stdio adapter for board kit")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--board-url", default=None)
    ap.add_argument("--token", default=None)
    a = ap.parse_args(argv)
    srv = MCPServer(a.root, board_url=a.board_url, token=a.token)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            resp = srv.handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
