"""The kit's config, read by every hook: resolved from agy stdin (`cwd` / `workspacePaths`),
$KIT_PROJECT_DIR / $CLAUDE_PROJECT_DIR, parent of .agents/, or current directory."""
import json
import os


def project_dir(data=None):
    if isinstance(data, dict):
        cwd = data.get("cwd")
        if cwd and os.path.isdir(cwd):
            # the session's shell may have moved into a subfolder (`cd src`): the project is the folder above it that holds .kit
            # (2026-11-25: a hook wrote src/.kit/run/..., and the part spent turns removing it)
            cur = os.path.abspath(cwd)
            for _ in range(8):
                if os.path.isdir(os.path.join(cur, ".kit")) or os.path.exists(os.path.join(cur, ".git")):
                    return cur
                up = os.path.dirname(cur)
                if up == cur:
                    break
                cur = up
            return cwd
        wps = data.get("workspacePaths") or []
        if wps and isinstance(wps[0], str) and os.path.isdir(wps[0]):
            return wps[0]
    env = os.environ.get("KIT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return env
    here = os.getcwd()
    # agy / claude may run hooks from inside .agents/ or .claude/
    cur = here
    for _ in range(4):
        if os.path.isdir(os.path.join(cur, ".kit")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return here


def config(data=None):
    p = os.path.join(project_dir(data), ".kit", "config.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def limit(name, default, data=None):
    return int(config(data).get("kit", {}).get("limits", {}).get(name, default))


def in_setup(data=None):
    """README steps 1-8: install.py sets kit.setup at the first install, --setup-done clears it."""
    return bool(config(data).get("kit", {}).get("setup"))


def is_claude_payload(data=None):
    """True when stdin payload came from Claude Code (or runtime is claude without agy fields)."""
    if isinstance(data, dict):
        if "hook_event_name" in data or "tool_input" in data or "stop_hook_active" in data or "prompt" in data:
            return True
        if ("hookEventName" in data or "toolCall" in data or "lastUserInput" in data or "terminationReason" in data
                or "conversationId" in data or "conversation_id" in data or "workspacePaths" in data):
            return False
    if os.environ.get("CLAUDE_PROJECT_DIR") and not os.environ.get("KIT_PROJECT_DIR"):
        return True
    return config(data).get("kit", {}).get("runtime") == "claude"


def memory_dir(data=None):
    """Memory directory (configurable via kit.memory_dir to avoid Storybloq .story/ collisions)."""
    root = project_dir(data)
    md = config(data).get("kit", {}).get("memory_dir")
    if md:
        return os.path.join(root, md)
    if os.path.isdir(os.path.join(root, ".story")):
        return os.path.join(root, ".story")
    return os.path.join(root, ".board", "memory")
