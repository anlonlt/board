"""Usage: python3 -I <this> <checkout root> [runner]; CFG=<other mcp_config.json> tries an old config.
CR-cl44: launch the MCP server exactly as .agents/mcp_config.json says (relative paths, cwd=root),
call board_queue(runner='agy') over stdio JSON-RPC, print the steps it returns."""
import json, subprocess, sys, os
root = sys.argv[1]
cfg = json.load(open(os.environ.get("CFG") or os.path.join(root, ".agents/mcp_config.json")))["mcpServers"]["board-kit"]
p = subprocess.Popen([cfg["command"], *cfg["args"]], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(i, method, params):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n"); p.stdin.flush()
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit("server closed")
        m = json.loads(line)
        if m.get("id") == i: return m
call(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cr", "version": "0"}})
p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); p.stdin.flush()
tools = [t["name"] for t in call(2, "tools/list", {})["result"]["tools"]]
print("tools:", len(tools), "board_queue" in tools)
r = call(3, "tools/call", {"name": "board_queue", "arguments": {"runner": sys.argv[2] if len(sys.argv) > 2 else "agy"}})
print(json.dumps(r.get("result", r), indent=1)[:3000])
p.terminate()
