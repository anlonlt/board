#!/usr/bin/env python3
"""Install the board kit on a computer, for agy and/or the Claude Code CLI:
    python3 install.py <project dir> --project "<Name>" --survey <step 0 report>
                       [--talk English] [--board-url URL] [--board-port 8765] [--main-branch <trunk>] [--dry-run]
                       [--no-board | --no-clerk | --no-orchestrator | --parts memory,board,...]
                       [--setup-done]

Copies, renders and merges; never deletes, never replaces what it did not write:
  .kit/               config.json, kit.py, board_server.py, board_cli.py, clerk_pending.py,
                      board.html, clerk.md, BOARD.md, ORCHESTRATOR.md, orchestrator.md,
                      consultant.md, runner.md, agents/kit-cards.md, manifest.json
  .agents/hooks/kit/  the agy lifecycle hooks and their self-test
  .agents/hooks.json  the kit's `kit-board` hook block MERGED in (other named hooks kept untouched)
  .agents/skills/story, .agents/skills/board   (skipped if the project has its own file there)
  .claude/hooks/kit/, .claude/settings.json, .claude/skills/*, .claude/agents/kit-cards.md
                      (--runtime local, the default: the same hooks for the Claude Code CLI, merged into settings.json)
  .kit/claude-cli/    the orchestrator / consultant / guide for a Claude Code CLI session (--runtime local)
  .board/memory/      state/, issues/, lessons/, awaits.md (or .story/ if already present)
"""
import argparse
import hashlib
import json
import os
import re
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(KIT, "kit", "board"))
import strings as page_strings  # noqa: E402

PARTS = ("memory", "board", "clerk", "orchestrator")
MEMORY_HOOKS = {"kit_config.py", "kit_guard.py", "kit_session_start.py", "kit_stop.py", "kit_pre_compact.py", "test_hooks.py"}
HOOKS = ["kit_config.py", "kit_guard.py", "kit_session_start.py", "kit_stop.py", "kit_pre_compact.py",
         "kit_say_it_once.py", "kit_board_typing.py", "kit_cards_reminder.py", "kit_notify.py", "kit_task_train.py",
         "test_hooks.py"]
# agy runs hook commands with working directory = .agents/ (the directory containing hooks.json)
CMD = "python3 -I hooks/kit/%s"
CLAUDE_CMD = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/kit/%s"'
CLAUDE_SETTINGS = [
    ("SessionStart", None, "kit_session_start.py"),
    ("PreToolUse", "Bash", "kit_guard.py"),
    ("PreToolUse", "mcp__claude-code-remote__send_message|SendMessage", "kit_say_it_once.py"),
    ("PostToolUse", "ArtifactData|mcp__claude-code-remote__send_message", "kit_say_it_once.py"),
    ("UserPromptSubmit", None, "kit_say_it_once.py"),
    ("UserPromptSubmit", None, "kit_board_typing.py"),
    ("UserPromptSubmit", None, "kit_cards_reminder.py"),
    ("PostToolUse", "ArtifactData|mcp__claude-code-remote__create_session", "kit_cards_reminder.py"),
    ("PostToolUse", "ArtifactData", "kit_notify.py"),
    ("Stop", None, "kit_stop.py"),
    ("Stop", None, "kit_say_it_once.py"),
    ("PreCompact", None, "kit_pre_compact.py"),
]
# The Claude Code CLI on a computer with the local board server (--runtime local): the agy hook set,
# mapped onto Claude Code's events and tool names (run_command -> Bash, send_message -> SendMessage).
CLAUDE_CLI_SETTINGS = [
    ("SessionStart", None, "kit_session_start.py"),
    ("PreToolUse", "Bash", "kit_guard.py"),
    ("PreToolUse", "SendMessage", "kit_say_it_once.py"),
    ("PostToolUse", "SendMessage|Bash", "kit_say_it_once.py"),
    ("PostToolUse", "TaskCreate|TaskUpdate", "kit_task_train.py"),
    ("UserPromptSubmit", None, "kit_say_it_once.py"),
    ("UserPromptSubmit", None, "kit_board_typing.py"),
    ("Stop", None, "kit_stop.py"),
    ("Stop", None, "kit_say_it_once.py"),
    ("Stop", None, "kit_pre_compact.py"),
    ("PreCompact", None, "kit_pre_compact.py"),
]
# Runtimes served by the local board server; `local` = agy + the Claude Code CLI in one install.
LOCAL_RUNTIMES = ("local", "agy", "both", "arena", "generic")

DEFAULT_KIT = {
    "runtime": "local",
    "cli": "auto",
    "mode": "local",
    "talk_language": "English",
    "limits": {"message_chars": 600, "done_message_chars": 240, "chat_answer_chars": 600},
    "guard": {"never_add": ["*.keystore", "*.jks", "*.pem", ".env", "keystore.properties"], "allow_add": []},
    "session_start_lines": [],
    # Context RESET points (kit_pre_compact.py; README § Context Reset): the orchestrator at 380k (a takeover costs
    # ~150k, so a lower limit spends half of each session getting going; past ~400k every turn re-reads too much), the
    # clerk at 80k (its takeover is a 1k prompt, and every wake re-reads its whole context: small is cheap).
    "orchestrator": {"context_limit_k": 380},
    # Reviews (Claude Code CLI): each completed step, and every train_every completed steps of a train a cross review.
    "review": {"auto": "all", "train_every": 4},
    # Above this a post body gets a nudge from `kit.py done` (never an error: the post is where the detail goes).
    "post_body_max": 1500,
    "clerk": {"wait_minutes": 5, "context_limit_k": 80},
    # agy subagent model tiers: clerk only forwards (flash_lite), cards runs scripts (flash),
    # orchestrator/parts inherit the session model, consultant uses pro.
    "roles": {"orchestrator": {"model": "inherit", "effort": "medium"},
              "clerk": {"model": "flash_lite", "effort": "low"},
              "part": {"model": "inherit", "effort": "medium"},
              "consultant": {"model": "pro", "effort": "medium"},
              "cards": {"model": "flash", "effort": "low"}},
    "autonomy": {
        "level": "balanced",
        "auto_consultant": True,
        "auto_defaults": False,
        "carry_on": False,
        "park_tangents": True,
    },
    "scope": "single",
    "palette": None,
    "funnel": True,
    "main_branch": "main",
    "trunk_push": False,
    # Claude Code CLI role models (--runtime local): `inherit` = the session's model (no `model` on the Agent call).
    "claude_cli_roles": {"clerk": {"model": "haiku"}, "part": {"model": "inherit"}, "consultant": {"model": "opus"},
                         "cards": {"model": "haiku"}},
    # Every Claude Code CLI session of the kit runs in this mode (sessions in different modes hold each other's messages).
    "claude_permission_mode": "auto",
}
CLAUDE_ROLES = {"part": {"model": "opus", "effort": "high"},
                "clerk": {"model": "haiku", "effort": "low"},
                "orchestrator": {"model": "opus", "effort": "medium"},
                "consultant": {"model": "opus", "effort": "high"}}


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Installer:
    def __init__(self, target, dry):
        self.t, self.dry, self.log, self.kept = os.path.abspath(target), dry, [], []
        mp = os.path.join(self.t, ".kit", "manifest.json")
        self.manifest = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}

    def put(self, rel, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        p = os.path.join(self.t, rel)
        if os.path.exists(p):
            cur = open(p, "rb").read()
            if cur == data:
                self.manifest[rel] = sha(data)
                return
            if self.manifest.get(rel) != sha(cur):
                self.kept.append("KEPT (not the kit's, or changed since): " + rel)
                return
            self.log.append("updated " + rel)
        else:
            self.log.append("wrote " + rel)
        self.manifest[rel] = sha(data)
        if not self.dry:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
            if rel.endswith(".py") or rel == ".kit/up":
                os.chmod(p, 0o755)

    def finish(self):
        if not self.dry:
            os.makedirs(os.path.join(self.t, ".kit"), exist_ok=True)
            with open(os.path.join(self.t, ".kit", "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump(self.manifest, fh, indent=1, sort_keys=True)
                fh.write("\n")


def load_config(t, a):
    p = os.path.join(t, ".kit", "config.json")
    if os.path.exists(p):
        c = json.load(open(p, encoding="utf-8"))
    else:
        c = json.load(open(os.path.join(KIT, "config", "example.json"), encoding="utf-8"))
        c["mirror"] = {"branch": "board-mirror", "path": "../%s-board-mirror" % slug(a.project or os.path.basename(t)),
                       "overlap_hours": 1}
    k = c.setdefault("kit", {})
    first = not os.path.exists(p)
    for key, v in DEFAULT_KIT.items():
        k.setdefault(key, json.loads(json.dumps(v)))
    if first:
        k["setup"] = True
        k["main_branch"] = None
    k.setdefault("orchestrator", {}).setdefault("context_limit_k", DEFAULT_KIT["orchestrator"]["context_limit_k"])
    k.setdefault("clerk", {}).setdefault("context_limit_k", DEFAULT_KIT["clerk"]["context_limit_k"])
    for role, v in DEFAULT_KIT["roles"].items():
        k["roles"].setdefault(role, dict(v))
    for role, v in DEFAULT_KIT["claude_cli_roles"].items():
        k["claude_cli_roles"].setdefault(role, dict(v))
    if a.project:
        c["project"] = a.project
    c.setdefault("project", os.path.basename(t))
    if a.board_url:
        c["board_url"] = a.board_url
    elif a.board_port:
        c["board_url"] = "http://127.0.0.1:%d" % a.board_port
    if a.talk:
        c.setdefault("language", {})["talk"] = a.talk
    c.setdefault("language", {}).setdefault("talk", k["talk_language"])
    parts = k.get("parts") or {p: True for p in PARTS}
    if a.parts:
        want = [x.strip() for x in a.parts.split(",") if x.strip()]
        bad = [x for x in want if x not in PARTS]
        if bad or "memory" not in want:
            raise SystemExit("install: --parts takes %s, and always memory (got %s)" % (",".join(PARTS), a.parts))
        parts = {p: p in want for p in PARTS}
    if a.no_board:
        parts["board"] = False
    if a.no_clerk:
        parts["clerk"] = False
    if a.no_orchestrator:
        parts["orchestrator"] = False
    if not parts["board"]:
        parts["clerk"] = parts["orchestrator"] = False
    k["parts"] = parts
    if a.runtime:
        k["runtime"] = a.runtime
    if first and k.get("runtime") == "claude":
        k["roles"] = {r: dict(v) for r, v in CLAUDE_ROLES.items()}
    if a.mode:
        k["mode"] = a.mode
    if getattr(a, "cli", None):
        k["cli"] = a.cli
    if k.get("runtime") in ("local", "agy", "both") and k.get("cli", "auto") == "auto":
        # The launcher's CLI is chosen here, once, not guessed at each launch: the CLI running this install,
        # else the one installed (agy first). `--cli agy|claude` (or `<cmd> --agy|--claude`) overrides it.
        sys.path.insert(0, os.path.join(KIT, "scripts"))
        import step0_scan  # noqa: E402
        k["cli"] = "agy" if k.get("runtime") in ("agy", "both") else step0_scan.pick_cli()
    if parts["board"] and not c.get("board_url") and k.get("mode", "local") == "local" and k.get("runtime", "local") != "claude":
        c["board_url"] = "http://127.0.0.1:%d" % (a.board_port or 8765)
    if a.survey:
        k["survey"] = a.survey
    elif a.no_survey:
        k["survey"] = "skipped: " + a.no_survey
    if a.setup_done:
        k["setup"] = False
    if a.main_branch:
        k["main_branch"] = a.main_branch
    if a.trunk_push:
        k["trunk_push"] = a.trunk_push == "yes"
    if a.cmd:
        k["cmd"] = a.cmd
    elif a.no_cmd:
        k["cmd"] = ""
    else:
        k.setdefault("cmd", slug(c["project"]))
    if a.memory_dir:
        k["memory_dir"] = a.memory_dir
    elif "memory_dir" not in k:
        k["memory_dir"] = ".story" if os.path.isdir(os.path.join(t, ".story")) else ".board/memory"
    if a.notify_url:
        k["notify_url"] = a.notify_url
    if getattr(a, "scope", None):
        k["scope"] = a.scope
    if getattr(a, "autonomy", None):
        lvl = a.autonomy
        k["autonomy"] = {
            "level": lvl,
            "auto_consultant": lvl in ("full_auto", "balanced"),
            "auto_defaults": lvl == "full_auto",
            "carry_on": lvl == "full_auto",
            "park_tangents": lvl in ("full_auto", "balanced"),
        }
    if getattr(a, "palette_json", None):
        try:
            k["palette"] = json.loads(a.palette_json)
        except ValueError:
            raise SystemExit("install: --palette-json is not valid JSON")
    elif getattr(a, "palette", None):
        sys.path.insert(0, os.path.join(KIT, "scripts"))
        import step0_scan as _s0
        if a.palette in _s0.BUILTIN_PALETTES:
            k["palette"] = json.loads(json.dumps(_s0.BUILTIN_PALETTES[a.palette]))
        else:
            prof = _s0.build_step0_profile(t)
            matched = next((p for p in prof.get("palettes", []) if p["id"] == a.palette), None)
            k["palette"] = matched or (prof.get("palettes") or [None])[0]
    return c


def trunk(t, k, named, log):
    """origin's HEAD unless named; a named or detected branch missing on origin stops the install."""
    import subprocess

    def git(*a):
        r = subprocess.run(["git", "-C", t] + list(a), capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout

    rc, url = git("remote", "get-url", "origin")
    if rc:
        k["main_branch"] = k.get("main_branch") or "main"
        log.append("trunk: %s (UNCHECKED: no git remote 'origin' here; pass --main-branch)" % k["main_branch"])
        return
    if not k.get("main_branch"):
        rc, out = git("ls-remote", "--symref", "origin", "HEAD")
        m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", out)
        if rc or not m:
            raise SystemExit("install: could not read origin's default branch (git ls-remote --symref origin HEAD); "
                             "pass --main-branch <the branch sessions start from>")
        k["main_branch"] = m.group(1)
        log.append("trunk: %s (origin's HEAD)" % k["main_branch"])
    if named or log is not None:
        rc, out = git("ls-remote", "--heads", "origin", k["main_branch"])
        if rc or not out.strip():
            raise SystemExit("install: the trunk '%s' does not exist on origin (git ls-remote --heads origin %s is empty); "
                             "name an existing branch with --main-branch" % (k["main_branch"], k["main_branch"]))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "project"


def render_vars(text, mapping):
    for k, v in mapping.items():
        text = text.replace("{{%s}}" % k, v)
    return text


def apply_palette(page_html, palette):
    """Apply an inferred Step 0 colour palette (light + dark CSS variables) to board.html."""
    if not isinstance(palette, dict):
        return page_html
    light = palette.get("light") or {}
    dark = palette.get("dark") or {}
    if not light or not dark:
        return page_html

    def _fmt_vars(d):
        keys = ("bg", "surface", "sunk", "ink", "muted", "line", "ember", "ember-ink", "good", "warn", "todo", "question", "info", "done")
        pairs = ["--%s: %s" % (k, d[k]) for k in keys if d.get(k)]
        return "; ".join(pairs) + ";"

    light_block = (
        ":root {\n  " + _fmt_vars(light) + "\n"
        "  --scrim: rgba(18, 24, 38, .55);\n"
        '  --display: "Alegreya", Georgia, "Times New Roman", serif;\n'
        '  --text: "Alegreya Sans", "Segoe UI", system-ui, sans-serif;\n'
        '  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;\n}'
    )
    dark_vars = _fmt_vars(dark)
    dark_media_block = (
        '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {\n  '
        + dark_vars + "\n  --scrim: rgba(0, 0, 0, .6); color-scheme: dark; } }"
    )
    dark_attr_block = (
        ':root[data-theme="dark"] {\n  '
        + dark_vars + "\n  --scrim: rgba(0, 0, 0, .6); color-scheme: dark; }"
    )

    page_html = re.sub(r":root\s*\{\s*--bg:.*?\n\}", light_block, page_html, count=1, flags=re.S)
    page_html = re.sub(r'@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)\s*\{.*?\}\s*\}', dark_media_block, page_html, count=1, flags=re.S)
    page_html = re.sub(r':root\[data-theme="dark"\]\s*\{.*?\}', dark_attr_block, page_html, count=1, flags=re.S)
    return page_html


def render(text, c):
    k = c["kit"]
    roles = k.get("roles") or {}
    role_cfg = lambda r: roles.get(r, DEFAULT_KIT["roles"][r])
    cli_roles = k.get("claude_cli_roles") or {}
    cli_role = lambda r: (cli_roles.get(r) or DEFAULT_KIT["claude_cli_roles"][r]).get("model") or "inherit"
    orch_limit_k = str((k.get("orchestrator") or {}).get("context_limit_k", DEFAULT_KIT["orchestrator"]["context_limit_k"]))
    clerk_limit_k = str((k.get("clerk") or {}).get("context_limit_k", DEFAULT_KIT["clerk"]["context_limit_k"]))
    autonomy = k.get("autonomy") or DEFAULT_KIT["autonomy"]
    auto_level = autonomy.get("level") or "balanced"
    if autonomy.get("auto_defaults") or autonomy.get("carry_on"):
        autonomy_rule = (
            "Continuous autonomous mode (`full_auto` — Step 0 anti-sprawl & auto-defaults active): "
            "every `question` post MUST include `default_option`; when a decision can proceed safely with its `default_option`, "
            "apply `default_option` and carry on continuously without stalling the train (note the applied default at the top of the post so the owner can override with one tap). "
            "If the owner mentions a new side-idea or feature tangent, record it in `{{MEMORY_DIR}}/state/<new-workstream>.md` (P2/P3) instead of derailing the active P1 train."
        )
    elif auto_level == "gated":
        autonomy_rule = (
            "Hands-on gated mode (`gated`): wait for explicit owner approval on every `question` or `todo` card before launching new parts or applying choices."
        )
    else:
        autonomy_rule = (
            "Balanced autonomy (`balanced`): launch parallel parts on a 👍 on your proposal, auto-spawn the Consultant at route-setting Done milestones, and park new feature tangents in `{{MEMORY_DIR}}/state/` so active trains stay focused."
        )
    vals = {"PROJECT": c["project"], "SLUG": slug(c["project"]),
            "RUNTIME": k.get("runtime") or "local",
            "CMD": k.get("cmd") or "./.kit/up",
            "BOARD_URL": c.get("board_url") or "http://127.0.0.1:8765",
            "MEMORY_DIR": k.get("memory_dir") or ".board/memory",
            "TALK": c["language"]["talk"], "MAIN_BRANCH": k["main_branch"],
            "WAIT": str(k["clerk"]["wait_minutes"]),
            "CONTEXT_K": clerk_limit_k,
            "CLERK_LIMIT_K": clerk_limit_k,
            # Claude Code's own compaction only as a safety net just past the reset point, if a hand-over ever fails.
            "CLERK_AUTOCOMPACT": str(max(100, int(clerk_limit_k) + 40) * 1000),
            "ORCH_LIMIT_K": orch_limit_k,
            "CLERK": c.get("fields", {}).get("clerk", "clerk"),
            "MIRROR_BRANCH": c["mirror"]["branch"], "MIRROR_PATH": c["mirror"]["path"],
            "MSG": str(k["limits"]["message_chars"]), "DONE": str(k["limits"]["done_message_chars"]),
            "ORCH_MODEL": role_cfg("orchestrator")["model"],
            "CLERK_MODEL": role_cfg("clerk")["model"],
            "PART_MODEL": role_cfg("part")["model"], "PART_EFFORT": role_cfg("part")["effort"],
            "CONSULTANT_MODEL": role_cfg("consultant")["model"],
            "CONS_MODEL": role_cfg("consultant")["model"], "CONS_EFFORT": role_cfg("consultant")["effort"],
            "CARDS_MODEL": role_cfg("cards")["model"],
            "CLI_PART_MODEL": cli_role("part"), "CLI_CONS_MODEL": cli_role("consultant"), "CLI_CARDS_MODEL": cli_role("cards"),
            "CLI_CLERK_MODEL": cli_role("clerk"),
            "CLI_PART_MODEL_FLAG": "" if cli_role("part") == "inherit" else "--model %s " % cli_role("part"),
            "CLAUDE_MODE": k.get("claude_permission_mode") or "auto",
            "AUTONOMY_LEVEL": auto_level,
            "AUTONOMY_RULE": render_vars(autonomy_rule, {"MEMORY_DIR": k.get("memory_dir") or ".board/memory"})}
    if c["project"].strip().lower().endswith("board"):
        text = text.replace("{{PROJECT}} board", "{{PROJECT}}")
    for n, v in vals.items():
        text = text.replace("{{%s}}" % n, v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise SystemExit("install: unrendered %s" % sorted(set(left)))
    return text


def build_agy_hooks(board=True):
    """Build the agy `.agents/hooks.json` spec for the kit-board named hook."""
    spec = {
        "SessionStart": [{"type": "command", "command": CMD % "kit_session_start.py"}],
        "PreToolUse": [
            {"matcher": "run_command", "hooks": [{"type": "command", "command": CMD % "kit_guard.py"}]},
        ],
        "Stop": [
            {"type": "command", "command": CMD % "kit_stop.py"},
            {"type": "command", "command": CMD % "kit_pre_compact.py"},
        ],
    }
    if board:
        spec["PreToolUse"].append(
            {"matcher": "send_message", "hooks": [{"type": "command", "command": CMD % "kit_say_it_once.py"}]}
        )
        spec["PostToolUse"] = [
            {"matcher": "send_message|run_command", "hooks": [{"type": "command", "command": CMD % "kit_say_it_once.py"}]}
        ]
        spec["PreInvocation"] = [
            {"type": "command", "command": CMD % "kit_say_it_once.py"},
            {"type": "command", "command": CMD % "kit_board_typing.py"},
        ]
        spec["PostInvocation"] = [
            {"type": "command", "command": CMD % "kit_say_it_once.py"},
        ]
        spec["Stop"].append({"type": "command", "command": CMD % "kit_say_it_once.py"})
    return spec


def merge_hooks(t, dry, log, board=True):
    p = os.path.join(t, ".agents", "hooks.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    desired = build_agy_hooks(board=board)
    if s.get("kit-board") == desired:
        return
    s["kit-board"] = desired
    count = (len(desired.get("SessionStart", [])) + len(desired.get("PreToolUse", []))
             + len(desired.get("PostToolUse", [])) + len(desired.get("PreInvocation", []))
             + len(desired.get("PostInvocation", [])) + len(desired.get("Stop", [])))
    log.append(".agents/hooks.json: kit-board hook block merged in (%d handlers; other hooks kept)" % count)
    if not dry:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, ensure_ascii=False)
            fh.write("\n")


def merge_claude_settings(t, dry, log, board=True, spec=None):
    p = os.path.join(t, ".claude", "settings.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    hooks = s.setdefault("hooks", {})
    added = 0
    if any(f == "kit_task_train.py" for _, _, f in (spec or [])) and board:
        # Claude Code's task tools (TaskCreate / TaskUpdate) are off unless this is set; the orchestrator plans with
        # them and kit_task_train.py puts each step on its train.
        env = s.setdefault("env", {})
        if env.get("CLAUDE_CODE_ENABLE_TODO_TOOLS") != "1":
            env["CLAUDE_CODE_ENABLE_TODO_TOOLS"] = "1"
            added += 1
    for ev, matcher, f in (spec or CLAUDE_SETTINGS):
        if not board and f not in MEMORY_HOOKS:
            continue
        cmd = CLAUDE_CMD % f
        groups = hooks.setdefault(ev, [])
        if any(h.get("command") == cmd for g in groups for h in g.get("hooks", []) if (g.get("matcher") or None) == matcher):
            continue
        g = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            g = {"matcher": matcher, **g}
        groups.append(g)
        added += 1
    if added:
        log.append(".claude/settings.json: %d kit hook lines merged in (existing lines kept)" % added)
        if not dry:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2, ensure_ascii=False)
                fh.write("\n")


def under_tempdir(t):
    """True when t is inside the system temp folder (tempfile.gettempdir(), /tmp, $TMPDIR)."""
    import tempfile
    real = os.path.realpath(t)
    roots = {os.path.realpath(r) for r in (tempfile.gettempdir(), "/tmp", os.environ.get("TMPDIR") or "/tmp")}
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def install_cmd(t, cmd_name, dry, log, extra_args=""):
    """Install a one-word launcher in ~/.local/bin/<cmd_name> (override dir with $KIT_BIN_DIR)."""
    if not cmd_name or "/" in cmd_name or " " in cmd_name:
        return
    if not os.environ.get("KIT_BIN_DIR") and under_tempdir(t):
        # A trial install in /tmp would point the real `board` at a folder that disappears.
        log.append("SKIPPED command `%s`: %s is a temp folder (set KIT_BIN_DIR to install its launcher)" % (cmd_name, t))
        return
    bin_dir = os.environ.get("KIT_BIN_DIR") or os.path.expanduser("~/.local/bin")
    dst = os.path.join(bin_dir, cmd_name)
    marker = "# kit-board launcher"
    suffix = (" " + extra_args.strip()) if extra_args.strip() else ""
    content = "#!/usr/bin/env bash\n%s\nexec %s%s \"$@\"\n" % (marker, json.dumps(os.path.join(t, ".kit", "up")), suffix)
    if os.path.exists(dst):
        try:
            cur = open(dst, encoding="utf-8").read()
        except OSError:
            cur = ""
        if cur == content:
            return
        if marker not in cur:
            log.append("KEPT %s (already exists and not a kit launcher; pass --cmd <other-name>)" % dst)
            return
    log.append("command: `%s` -> %s" % (cmd_name, dst))
    if not dry:
        os.makedirs(bin_dir, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(dst, 0o755)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target")
    ap.add_argument("--project")
    ap.add_argument("--talk", help="the language to talk to the owner in (default English)")
    ap.add_argument("--board-url", help="override board URL (default http://127.0.0.1:<port> in local mode)")
    ap.add_argument("--board-port", type=int, help="local board server port (default 8765)")
    ap.add_argument("--runtime", choices=["local", "agy", "claude", "arena", "generic", "both"],
                    help="which harness configs to install (default local: agy + the Claude Code CLI on this computer; "
                         "agy: agy only; claude: Claude Code Web Artifact; both: agy + Claude Code Web hybrid)")
    ap.add_argument("--cli", choices=["auto", "agy", "claude"],
                    help="which CLI the one-word launcher opens (default auto: kit.cli, else the one installed, agy first)")
    ap.add_argument("--mode", choices=["local", "hybrid"], help="local: localhost board_server; hybrid: Claude Artifact + git_watch.py")
    ap.add_argument("--cmd", help="one-word terminal command installed in ~/.local/bin/<cmd> (default: project slug)")
    ap.add_argument("--no-cmd", action="store_true", help="skip installing the ~/.local/bin/<cmd> shortcut")
    ap.add_argument("--memory-dir", help="memory directory relative to repo root (default: .story if present, else .board/memory)")
    ap.add_argument("--notify-url", help="optional phone push webhook URL (e.g. https://ntfy.sh/<topic>)")
    ap.add_argument("--palette", help="Step 0 inferred colour palette id (e.g. project-inferred, ember-classic, oled-cyber, forest-sage)")
    ap.add_argument("--palette-json", help="Custom Step 0 colour palette JSON object (light & dark CSS variables)")
    ap.add_argument("--autonomy", choices=["full_auto", "balanced", "gated"], help="Step 0 autonomy & anti-sprawl profile")
    ap.add_argument("--scope", choices=["single", "all"], help="Step 0 board scope: single project or all discovered projects")
    ap.add_argument("--attach", action="append", default=[], help="Attach another local repo board (name=path or path) into .kit/config.json portfolio")
    ap.add_argument("--mcp-readers", default=None, help="Claude Artifact MCP Link Readers: 'auto' (Step 0 scan), 'none', or comma list (sharepoint,gmail,gdrive,outlook,notion,linear)")
    ap.add_argument("--no-funnel", action="store_true", help="Disable default Tailscale Funnel auto-setup in .kit/up")
    ap.add_argument("--tui", "--wizard", dest="tui", action="store_true", help="Launch TWRP-style clickable terminal GUI installer seeded by Step 0 scan")
    ap.add_argument("--bootstrap", action="store_true", help="1-command bootstrap: pre-seed _orchestrator & KIT-installed into local SQLite DB")
    ap.add_argument("--no-bootstrap", action="store_true", help="skip default SQLite bootstrap on arena/generic installs")
    ap.add_argument("--demo", action="store_true", help="seed sample memory, 4-lane demo posts, _dashboard, and _trains for instant evaluation")
    ap.add_argument("--main-branch")
    ap.add_argument("--trunk-push", choices=["yes", "no"], help="the owner's standing permission to push the trunk")
    ap.add_argument("--survey", help="step 0's report (survey/<date>/report.md): required at the first install")
    ap.add_argument("--no-survey", metavar="WHY", help="install without step 0 (the reason is recorded)")
    ap.add_argument("--quick", action="store_true", help="<60s trial install: skips Step 0 ceremony, defaults project/language, and bootstraps local DB")
    ap.add_argument("--parts", help="comma list among memory,board,clerk,orchestrator (memory always)")
    ap.add_argument("--no-board", action="store_true", help="memory and its hooks only (no page, clerk, orchestrator)")
    ap.add_argument("--no-clerk", action="store_true")
    ap.add_argument("--no-orchestrator", action="store_true")
    ap.add_argument("--setup-done", action="store_true", help="README step 8: the install is reported; hooks speak again")
    ap.add_argument("--verify", action="store_true", help="Verify installed kit files against SHA-256 hashes in .kit/manifest.json")
    ap.add_argument("--uninstall", action="store_true", help="Remove unmodified kit files recorded in .kit/manifest.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.quick:
        if not a.survey and not a.no_survey:
            a.no_survey = "quick install"
        if not a.project:
            a.project = os.path.basename(os.path.abspath(a.target)) or "board"
        if not a.talk:
            a.talk = "en"
        if not a.no_bootstrap:
            a.bootstrap = True
    t = os.path.abspath(a.target)
    if not os.path.isdir(t):
        raise SystemExit("install: %s is not a directory" % t)
    if a.verify:
        mp = os.path.join(t, ".kit", "manifest.json")
        if not os.path.isfile(mp):
            print("verify: no .kit/manifest.json found in %s" % t)
            return 1
        mf = json.load(open(mp, encoding="utf-8"))
        ok_n, mod_n, miss_n = 0, 0, 0
        for rel, expected_sha in sorted(mf.items()):
            if rel.startswith("_"):
                continue
            fp = os.path.join(t, rel)
            if not os.path.isfile(fp):
                print("MISSING  " + rel)
                miss_n += 1
            elif sha(open(fp, "rb").read()) != expected_sha:
                print("MODIFIED " + rel)
                mod_n += 1
            else:
                ok_n += 1
        print("verify: %d ok, %d modified, %d missing" % (ok_n, mod_n, miss_n))
        return 0 if (mod_n == 0 and miss_n == 0) else 1
    if a.uninstall:
        mp = os.path.join(t, ".kit", "manifest.json")
        if not os.path.isfile(mp):
            print("uninstall: no .kit/manifest.json found in %s" % t)
            return 0
        mf = json.load(open(mp, encoding="utf-8"))
        removed, kept = [], []
        for rel, expected_sha in sorted(mf.items()):
            if rel.startswith("_"):
                continue
            fp = os.path.join(t, rel)
            if not os.path.isfile(fp):
                continue
            if sha(open(fp, "rb").read()) == expected_sha:
                removed.append(rel)
                if not a.dry_run:
                    os.remove(fp)
            else:
                kept.append(rel)
        if not a.dry_run and not kept:
            try:
                os.remove(mp)
            except OSError:
                pass
        for r in removed:
            print(("would remove " if a.dry_run else "removed ") + r)
        for k in kept:
            print("KEPT (modified since install): " + k)
        print("uninstall: %d removed, %d kept" % (len(removed), len(kept)))
        return 0
    if a.tui:
        sys.path.insert(0, os.path.join(KIT, "scripts"))
        import twrp_wizard as _tw
        wiz_args = [t]
        if a.palette:
            wiz_args.extend(["--palette", a.palette])
        if a.autonomy:
            wiz_args.extend(["--autonomy", a.autonomy])
        if a.runtime:
            wiz_args.extend(["--runtime", a.runtime])
        if a.scope:
            wiz_args.extend(["--scope", a.scope])
        if a.dry_run:
            wiz_args.append("--dry-run")
        return _tw.main(wiz_args)
    first = not os.path.exists(os.path.join(t, ".kit", "config.json"))
    if first and not (a.survey or a.no_survey):
        raise SystemExit("install: step 0 first. Run the survey (step0/survey-code.md or step0/survey-chat.md) and pass "
                         "its report with --survey survey/<date>/report.md; its recommendation says which parts to install "
                         "(--no-board, --no-clerk, --no-orchestrator). Only if the owner declines step 0: --no-survey \"<why>\".")
    if a.survey and not os.path.isfile(a.survey):
        raise SystemExit("install: --survey %s: no such file" % a.survey)
    c = load_config(t, a)
    if a.no_funnel:
        c["kit"]["funnel"] = False
    elif "funnel" not in c["kit"]:
        c["kit"]["funnel"] = True
    if "pinned_docs" not in c["kit"]:
        _pin_candidates = (
            "PLAN.md", "ROADMAP.md", "TODO.md", "CLAUDE.md", "AGENTS.md", "README.md",
            "docs/WORKPLAN.md", "docs/DISCIPLINE.md", "docs/ARCHITECTURE.md",
        )
        c["kit"]["pinned_docs"] = [p for p in _pin_candidates if os.path.isfile(os.path.join(t, p))]
    if a.attach:
        port_list = list(c.get("portfolio") or [])
        seen_paths = {os.path.abspath(x["path"]) for x in port_list if isinstance(x, dict) and x.get("path")}
        for spec in a.attach:
            if "=" in spec:
                aname, apath = spec.split("=", 1)
                aname, apath = aname.strip(), os.path.abspath(apath.strip())
            else:
                apath = os.path.abspath(spec.strip())
                aname = os.path.basename(apath)
            if apath not in seen_paths:
                port_list.append({"name": aname, "path": apath})
                seen_paths.add(apath)
        c["portfolio"] = port_list
    if a.mcp_readers is not None:
        raw_mr = a.mcp_readers.strip().lower()
        if raw_mr in ("none", "off", ""):
            c["kit"]["mcp_readers"] = []
        elif raw_mr == "auto":
            sys.path.insert(0, os.path.join(KIT, "scripts"))
            import step0_scan  # noqa: E402
            c["kit"]["mcp_readers"] = step0_scan.detect_mcp_readers(t)["readers"]
        else:
            c["kit"]["mcp_readers"] = [x.strip().lower() for x in raw_mr.split(",") if x.strip()]
    elif c["kit"].get("runtime") == "claude" and "mcp_readers" not in c["kit"]:
        sys.path.insert(0, os.path.join(KIT, "scripts"))
        import step0_scan  # noqa: E402
        detected_mr = step0_scan.detect_mcp_readers(t)["readers"]
        if detected_mr:
            c["kit"]["mcp_readers"] = detected_mr
    ins = Installer(t, a.dry_run)
    trunk(t, c["kit"], a.main_branch, ins.log if (first or a.main_branch) else None) if (first or a.main_branch) else None
    parts = c["kit"]["parts"]
    notes = []
    cfg = json.dumps(c, indent=1, ensure_ascii=False) + "\n"
    p = os.path.join(t, ".kit", "config.json")
    if not os.path.exists(p) or open(p, encoding="utf-8").read() != cfg:
        ins.log.append(("wrote " if not os.path.exists(p) else "updated ") + ".kit/config.json")
        ins.manifest.pop(".kit/config.json", None)
        if not a.dry_run:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(cfg)
    rd = lambda *x: open(os.path.join(KIT, *x), encoding="utf-8").read()
    rt = c["kit"].get("runtime", "local")
    claude_only = rt == "claude"
    want_agy = rt in ("agy", "both", "local")
    want_claude = rt in ("claude", "both", "local")
    claude_cli = rt == "local"  # the Claude Code CLI next to agy, on the local board server
    ins.put(".kit/end_session.py", rd("kit", "end_session.py"))
    ins.put(".kit/kit_import.py", rd("kit", "import", "kit_import.py"))
    if claude_only:
        ins.put(".kit/kit_artifact_sync.py", rd("kit", "claude", "kit_artifact_sync.py"))
    if not claude_only:
        ins.put(".kit/git_watch.py", rd("kit", "git_watch.py"))
        ins.put(".kit/up", rd("kit", "up"))
        ins.put(".kit/handover.py", rd("kit", "handover.py"))
        ins.put(".kit/step0_scan.py", rd("scripts", "step0_scan.py"))
        ins.put(".kit/twrp_wizard.py", rd("scripts", "twrp_wizard.py"))
    for h in HOOKS:
        if parts["board"] or h in MEMORY_HOOKS:
            if want_agy:
                ins.put(".agents/hooks/kit/" + h, rd("kit", "hooks", h))
            if want_claude:
                ins.put(".claude/hooks/kit/" + h, rd("kit", "hooks", h))
    if want_agy:
        ins.put(".agents/skills/memory/SKILL.md", rd("kit", "skills", "memory", "SKILL.md"))
    if want_claude:
        ins.put(".claude/skills/memory/SKILL.md", rd("kit", "skills", "memory", "SKILL.md"))
    if parts["board"]:
        ins.put(".kit/kit.py", rd("scripts", "kit.py"))
        if not claude_only:
            ins.put(".kit/board_server.py", rd("kit", "board", "board_server.py"))
            ins.put(".kit/shim.js", rd("kit", "board", "shim.js"))
            for _vf in ("pdf.min.js", "pdf.worker.min.js"):
                _vp = os.path.join(KIT, "kit", "board", "vendor", _vf)
                if os.path.isfile(_vp):
                    ins.put(".kit/vendor/" + _vf, open(_vp, "rb").read())
            ins.put(".kit/board_cli.py", rd("kit", "board", "board_cli.py"))
            ins.put(".kit/mcp_stdio.py", rd("kit", "board", "mcp_stdio.py"))
        page = rd("kit", "board", "board.template.html")
        if c["kit"].get("palette"):
            page = apply_palette(page, c["kit"]["palette"])
        sp = os.path.join(t, ".kit", "strings.json")
        if os.path.exists(sp):
            tr = json.load(open(sp, encoding="utf-8"))
            page, used, missing = page_strings.apply(page, page_strings.load_table(), tr.get("strings") or {})
            notes.append("page: %d strings in %s, %d untranslated (.kit/strings.json; add them there, never in the page)" % (used, tr.get("language", "?"), len(missing)))
        page_out = render(page, c)
        if claude_only and c.get("board_url"):
            # Claude Code Web: the server UI layer + an adapter onto the Artifact's capabilities (kit/claude/build_artifact_page.py)
            sys.path.insert(0, os.path.join(KIT, "kit", "claude"))
            import build_artifact_page  # noqa: E402
            readers = c["kit"].get("mcp_readers")
            talk_lang = (c.get("language") or {}).get("talk") or ""
            page_out = build_artifact_page.build(page_out, KIT, c["board_url"], c.get("project") or "board", mcp_readers=readers, talk=talk_lang)
            if readers:
                notes.append("mcp readers (%s): publish with capabilities.mcp = %s" % (
                    ",".join(readers), json.dumps(build_artifact_page.mcp_capabilities(readers))))
        ins.put(".kit/board.html", page_out)
        ins.put(".kit/strings.en.json", rd("kit", "board", "strings.en.json"))
        ins.put(".kit/strings.py", rd("kit", "board", "strings.py"))
        ins.put(".kit/kit_cards.py", rd("kit", "board", "kit_cards.py"))
        board_tpl = rd("kit", "claude", "BOARD.template.md") if claude_only else rd("kit", "board", "BOARD.template.md")
        cards_tpl = rd("kit", "claude", "kit-cards.md") if claude_only else rd("kit", "agents", "kit-cards.md")
        ins.put(".kit/BOARD.md", render(board_tpl, c))
        if want_agy:
            ins.put(".kit/agents/kit-cards.md", cards_tpl)
            ins.put(".agents/skills/board/SKILL.md", rd("kit", "skills", "board", "SKILL.md"))
        if want_claude:
            ins.put(".claude/agents/kit-cards.md", render(rd("kit", "claude-cli", "kit-cards.template.md"), c) if claude_cli else cards_tpl)
            ins.put(".claude/skills/board/SKILL.md", rd("kit", "claude", "SKILL.board.md") if claude_only else rd("kit", "skills", "board", "SKILL.md"))
    if parts["clerk"]:
        ins.put(".kit/clerk_pending.py", rd("kit", "clerk", "clerk_pending.py"))
        clerk_tpl = rd("kit", "claude", "clerk.template.md") if claude_only else rd("kit", "clerk", "clerk.template.md")
        ins.put(".kit/clerk.md", render(clerk_tpl, c))
    if parts["orchestrator"]:
        if claude_only:
            ins.put(".kit/orchestrator.md", render(rd("kit", "claude", "orchestrator.template.md"), c))
            ins.put(".kit/consultant.md", render(rd("kit", "claude", "consultant.template.md"), c))
            ins.put(".kit/runner.md", render(rd("kit", "claude", "runner.template.md"), c))
            ins.put(".kit/ORCHESTRATOR.md", render(rd("kit", "claude", "ORCHESTRATOR.template.md"), c))
        else:
            orch_tpl = rd("kit", "arena", "orchestrator.template.md") if rt == "arena" else rd("kit", "orchestrator.template.md")
            ins.put(".kit/orchestrator.md", render(orch_tpl, c))
            ins.put(".kit/consultant.md", render(rd("kit", "consultant.template.md"), c))
            ins.put(".kit/runner.md", render(rd("kit", "runner.template.md"), c))
            ins.put(".kit/ORCHESTRATOR.md", render(rd("kit", "ORCHESTRATOR.template.md"), c))
            if claude_cli:
                for f in ("orchestrator", "ORCHESTRATOR", "consultant", "clerk", "part"):
                    ins.put(".kit/claude-cli/%s.md" % f, render(rd("kit", "claude-cli", f + ".template.md"), c))
    mdir = c["kit"].get("memory_dir") or ".board/memory"
    for d in ("state", "issues", "lessons"):
        if not os.path.isdir(os.path.join(t, mdir, d)):
            ins.put("%s/%s/.gitkeep" % (mdir, d), "")
    if not os.path.exists(os.path.join(t, mdir, "awaits.md")):
        ins.put("%s/awaits.md" % mdir, "# What awaits the owner\n\n")
    gi = os.path.join(t, ".gitignore")
    cur = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    gi_add = [x for x in ("%s/before-compaction.md" % mdir, ".kit/board_data/", ".kit/handover.flag", ".kit/handover.log",
                                ".kit/handover-*.gen", ".kit/run/") if x not in cur]
    if gi_add:
        ins.log.append(".gitignore: + " + ", ".join(gi_add))
        if not a.dry_run:
            prefix = "" if cur.endswith("\n") or not cur else "\n"
            open(gi, "a", encoding="utf-8").write(prefix + "\n".join(gi_add) + "\n")
    if want_agy:
        merge_hooks(t, a.dry_run, ins.log, parts["board"])
        if c["kit"].get("cmd"):
            install_cmd(t, c["kit"]["cmd"], a.dry_run, ins.log)
            if parts["board"] and c["kit"]["cmd"] != "board":
                install_cmd(t, "board", a.dry_run, ins.log, extra_args="--board-only")
    if rt in ("arena", "generic") and parts["board"]:
        ins.put("AGENTS.md", render(rd("kit", "AGENTS.template.md"), c))
    if a.demo and not a.dry_run:
        demo_state = os.path.join(t, mdir, "state", "demo.md")
        if not os.path.exists(demo_state):
            ins.put("%s/state/demo.md" % mdir, "Next: Review the sample board cards and reply with 👍\nPriority: 1\n\n# Demo Workstream\n\n## Steps\n- [x] Install board kit\n- [ ] Review sample cards\n")
        awaits_p = os.path.join(t, mdir, "awaits.md")
        cur_aw = open(awaits_p, encoding="utf-8").read() if os.path.exists(awaits_p) else "# What awaits the owner\n\n"
        if "Confirm demo setup" not in cur_aw:
            with open(awaits_p, "w", encoding="utf-8") as fh:
                fh.write(cur_aw.rstrip() + "\n- [demo] Confirm demo setup works in your browser\n")
        if not os.path.exists(os.path.join(t, "docs", "demo-guide.md")):
            ins.put("docs/demo-guide.md", (
                "# Board Demo Guide\n\n"
                "Welcome to the interactive **board** demo.\n\n"
                "## Viewer Features\n\n"
                "The unified Document Viewer handles **Markdown**, **live HTML**, and **Images** both in floating OS-style windows and directly inline inside posts:\n\n"
                "- **Zero-chrome pinch-to-zoom:** pinch on trackpad or phone to scale content without scaling UI chrome.\n"
                "- **Side-by-side image compare & Draw-to-Annotate:** compare multiple screenshots inline, circle bugs on a transparent canvas, and send annotated PNGs straight to the orchestrator.\n"
                "- **Sandboxed HTML preview:** switch between Mobile, Desktop (with crisp vector zoom on mobile), and `</>` source code; commenting captures live `#hash`, scroll %, and active section.\n\n"
                "```mermaid\nflowchart LR\n  Post[Board Post] --> Inline[Inline Viewer]\n  Inline --> Window[Floating Window]\n  Window --> Orch[Ask the Orchestrator]\n```\n\n"
                "## Section Anchoring\n\n"
                "Clicking any heading in the right outline (or highlighting text) locks the active section so commenting always references the exact `§ Section`.\n"
            ))
        if not os.path.exists(os.path.join(t, "docs", "demo-preview.html")):
            ins.put("docs/demo-preview.html", (
                "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>Interactive HTML Preview</title>"
                "<style>"
                "body{font-family:system-ui,sans-serif;margin:0;padding:20px;background:#f4f6f9;color:#1b2433;}"
                ".card{max-width:760px;margin:0 auto;background:#fff;border:1px solid #cfd7e1;border-radius:14px;padding:18px;box-shadow:0 8px 24px rgba(0,0,0,.06);}"
                ".tabs{display:flex;gap:8px;margin:12px 0 16px;}"
                ".tabs button{border:1px solid #cfd7e1;background:#eef2f6;color:#1b2433;padding:6px 12px;border-radius:999px;cursor:pointer;font-weight:600;}"
                ".tabs button[aria-selected='true']{background:#b8561c;color:#fff;border-color:#b8561c;}"
                ".pulse{display:inline-block;width:10px;height:10px;border-radius:50%;background:#2f7a4f;margin-right:6px;animation:p 1.4s ease-in-out infinite;}"
                "@keyframes p{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.45);opacity:.45}}"
                ".grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px;}"
                ".kpi{background:#eef2f6;border-radius:10px;padding:12px;}"
                ".kpi b{display:block;font-size:1.35rem;color:#b8561c;}"
                "@media (max-width:520px){.grid{grid-template-columns:1fr;}}"
                "</style></head><body><div class=\"card\">"
                "<h2 id=\"sec-hdr\"><span class=\"pulse\"></span>Interactive HTML Sandbox</h2>"
                "<p>Switch tabs below, toggle <b>Mobile / Desktop / &lt;/&gt;</b> in the header, then tap <b>Comment</b> to see live hash &amp; section capture + animation auto-pause.</p>"
                "<div class=\"tabs\" role=\"tablist\">"
                "<button type=\"button\" role=\"tab\" aria-selected=\"true\" data-tab=\"overview\">Overview</button>"
                "<button type=\"button\" role=\"tab\" aria-selected=\"false\" data-tab=\"metrics\">Metrics</button>"
                "<button type=\"button\" role=\"tab\" aria-selected=\"false\" data-tab=\"notes\">QA Notes</button>"
                "</div>"
                "<div id=\"panel-overview\"><h3>Overview Panel</h3><p>Live client-side HTML/CSS/JS running inside a sandboxed repo frame.</p>"
                "<div class=\"grid\"><div class=\"kpi\"><b>12x</b>Smaller reads</div><div class=\"kpi\"><b>0.8m</b>Median wait</div><div class=\"kpi\"><b>0</b>Dependencies</div></div></div>"
                "<div id=\"panel-metrics\" hidden><h3>Metrics Panel</h3><p>Hash is now <code>#metrics</code> — click Comment to verify it captures this exact state.</p></div>"
                "<div id=\"panel-notes\" hidden><h3>QA Notes Panel</h3><p>Try pinching to zoom in Desktop mode on portrait mobile.</p></div>"
                "</div><script>"
                "var btns=document.querySelectorAll('.tabs button');"
                "function show(k){location.hash=k;btns.forEach(function(b){var on=b.dataset.tab===k;b.setAttribute('aria-selected',on?'true':'false');document.getElementById('panel-'+b.dataset.tab).hidden=!on;});}"
                "btns.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab);});});"
                "if(location.hash&&document.getElementById('panel-'+location.hash.slice(1)))show(location.hash.slice(1));"
                "</script></body></html>\n"
            ))
        if not os.path.exists(os.path.join(t, "docs", "assets", "demo-before.svg")):
            ins.put("docs/assets/demo-before.svg", (
                "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='400' viewBox='0 0 640 400'>"
                "<rect width='640' height='400' rx='16' fill='#e2e8ef'/>"
                "<rect x='24' y='24' width='592' height='54' rx='10' fill='#ffffff' stroke='#cfd7e1'/>"
                "<text x='44' y='57' font-family='system-ui,sans-serif' font-size='20' font-weight='700' fill='#465162'>BEFORE — Dense Text Toolbar</text>"
                "<rect x='24' y='98' width='284' height='274' rx='12' fill='#ffffff' stroke='#cfd7e1'/>"
                "<rect x='332' y='98' width='284' height='274' rx='12' fill='#ffffff' stroke='#cfd7e1'/>"
                "<rect x='44' y='124' width='180' height='16' rx='6' fill='#cfd7e1'/>"
                "<rect x='44' y='154' width='240' height='12' rx='6' fill='#e2e8ef'/>"
                "<rect x='44' y='178' width='210' height='12' rx='6' fill='#e2e8ef'/>"
                "<rect x='352' y='124' width='160' height='16' rx='6' fill='#cfd7e1'/>"
                "<rect x='352' y='154' width='240' height='12' rx='6' fill='#e2e8ef'/>"
                "<text x='44' y='348' font-family='monospace' font-size='14' fill='#a33b2b'>Status: High chrome clutter</text>"
                "</svg>\n"
            ))
        if not os.path.exists(os.path.join(t, "docs", "assets", "demo-after.svg")):
            ins.put("docs/assets/demo-after.svg", (
                "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='400' viewBox='0 0 640 400'>"
                "<rect width='640' height='400' rx='16' fill='#111826'/>"
                "<rect x='24' y='24' width='592' height='54' rx='10' fill='#1a2335' stroke='#2c3850'/>"
                "<text x='44' y='57' font-family='system-ui,sans-serif' font-size='20' font-weight='700' fill='#e6ebf2'>AFTER — Zero-Chrome Floating Viewer</text>"
                "<circle cx='556' cy='51' r='12' fill='none' stroke='#e8894a' stroke-width='2.5'/>"
                "<circle cx='590' cy='51' r='12' fill='none' stroke='#aab6c8' stroke-width='2'/>"
                "<rect x='24' y='98' width='592' height='274' rx='12' fill='#1a2335' stroke='#e8894a' stroke-width='2'/>"
                "<rect x='48' y='128' width='260' height='18' rx='6' fill='#e8894a'/>"
                "<rect x='48' y='162' width='520' height='12' rx='6' fill='#2c3850'/>"
                "<rect x='48' y='186' width='460' height='12' rx='6' fill='#2c3850'/>"
                "<rect x='48' y='210' width='490' height='12' rx='6' fill='#2c3850'/>"
                "<text x='48' y='348' font-family='monospace' font-size='14' fill='#6cc492'>Status: Crisp editorial canvas + pinch zoom</text>"
                "</svg>\n"
            ))
    should_bootstrap = (a.bootstrap or a.demo or rt in ("arena", "generic")) and not a.no_bootstrap
    if should_bootstrap and parts["board"] and not claude_only and not a.dry_run:
        from datetime import datetime, timezone
        import board_server as _bs  # already on sys.path via kit/board
        store = _bs.BoardStore(os.path.join(t, ".kit", "board_data"))
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if not store.get_doc("posts", "_orchestrator").get("exists"):
                store.set_doc("posts", "_orchestrator", {
                    "title": "Ask the orchestrator", "column": "info", "part": "orchestrator",
                    "priority": 3, "created_at": now,
                    "body": "Write here for anything that is not about one post.",
                })
            if not store.get_doc("posts", "KIT-installed").get("exists"):
                store.set_doc("posts", "KIT-installed", {
                    "title": "The board kit is installed", "column": "info", "part": "KIT",
                    "priority": 2, "created_at": now,
                    "body": (
                        "Bootstrapped by `install.py`.\n\n"
                        "- **Start anytime:** run `%s` (or `python3 -I .kit/board_server.py --preview --embed-mode` in sandbox previews).\n"
                        "- **Repo Docs & Memory:** tap the book icon in the header to read any `.md` file in your repo directly from the board — and tap **Comment on this section** to start an orchestrator discussion anchored to what's on your screen.\n"
                        "- **Trains as Tasklists:** switch to **Trains** and tap the checklist icon on any train to see all waves, verdicts, and artifacts; agents can run `python3 -I .kit/kit.py train <name> --zoom <part>` for compact context."
                    ) % (c["kit"].get("cmd") or "./.kit/up"),
                })
            if a.demo:
                from datetime import timedelta
                base_dt = datetime.now(timezone.utc)
                ts = lambda mins: (base_dt - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
                demo_posts = [
                    ("demo-lmarena-research", {
                        "title": "LMArena research: compare board to competitors",
                        "column": "todo",
                        "part": "RS1",
                        "train": "research",
                        "priority": 1,
                        "minutes": 10,
                        "created_at": ts(2),
                        "body": (
                            "Run a hands-on competitive benchmark comparing **`anlonlt/board`** against adjacent tools "
                            "(e.g. **Vibe Kanban**, **Linear**, **Pad**, **FlowState**, **Conductor**, and **GitHub Projects / Trello**).\n\n"
                            "### Evaluation Criteria\n"
                            "1. **Visual & mobile UX:** desktop + phone layout, dark/light themes, floating multi-window workflow, and one-tap approval ergonomics.\n"
                            "2. **Architecture & durability:** zero-dependency footprint (Python 3 stdlib + single-file HTML), local SQLite + `board-mirror` Git backup, and sandbox/iframe embed support.\n"
                            "3. **Agent contract & token economics:** compact `kit.py` reads, `SessionStart` `.board/memory/` injection, automatic context-limit handovers, and `kit_guard` safety hooks.\n"
                            "4. **Honest gaps & roadmap:** where competitors win (e.g. built-in execution plane, multi-user RBAC, integrations) and the top prioritized upgrades for `board`.\n\n"
                            "Post your scorecard, screenshots, and prioritized recommendations directly as a reply below or a new report card."
                        ),
                    }),
                    ("demo-viewer-0", {
                        "title": "Unified Document & Media Viewer foundation",
                        "column": "done",
                        "part": "V0",
                        "train": "viewer",
                        "priority": 3,
                        "created_at": ts(260),
                        "done_at": ts(220),
                        "verdict": "Shipped OS-style floating viewer with pointer-aware drawers and zero-chrome pinch zoom.",
                        "artifacts": ["docs/demo-guide.md"],
                        "bars": ["9/9 checklist tests pass"],
                        "body": "Built the floating Document Viewer with Markdown, Mermaid, and repo asset rendering.",
                    }),
                    ("demo-html-embed", {
                        "title": "Live HTML viewer: test mobile, desktop & code modes",
                        "column": "todo",
                        "part": "V1",
                        "train": "viewer",
                        "parent": "V0",
                        "priority": 1,
                        "minutes": 3,
                        "created_at": ts(18),
                        "verdict": "Waiting on you to verify Mobile, Desktop, and </> code toggles.",
                        "artifacts": ["docs/demo-preview.html"],
                        "body": (
                            "Below is an interactive HTML prototype embedded directly inline from `docs/demo-preview.html`.\n\n"
                            "- Switch tabs inside the preview, toggle **Mobile / Desktop / `</>`** in the viewer header, or pop it out into a floating window.\n"
                            "- Tap **Comment** while on a specific tab to see live `#hash` + section capture and animation auto-pause."
                        ),
                    }),
                    ("demo-image-compare", {
                        "title": "Side-by-side visual compare: Before vs After UI",
                        "column": "question",
                        "part": "V2",
                        "train": "viewer",
                        "parent": "V1",
                        "priority": 1,
                        "created_at": ts(10),
                        "verdict": "Defaulting to After pass unless you pick another option.",
                        "artifacts": ["docs/assets/demo-before.svg", "docs/assets/demo-after.svg"],
                        "images": ["docs/assets/demo-before.svg", "docs/assets/demo-after.svg"],
                        "captions": ["Before: dense text toolbar", "After: zero-chrome floating viewer"],
                        "image_groups": [{"title": "Before vs After (Pinch to zoom, tap to open / draw-to-annotate)", "count": 2}],
                        "options": ["Approve After (Recommended)", "Keep Before", "Tweak spacing"],
                        "default_option": "Approve After (Recommended)",
                        "body": "Compare the two UI passes side-by-side inline below. Pinch to zoom directly in the post, or tap either image to open the full Image Viewer with **Gallery**, **Auto-Fade Header**, and **Draw-to-Annotate**.",
                    }),
                    ("demo-md-section", {
                        "title": "Inline Markdown section viewer & deep links",
                        "column": "info",
                        "status": "in progress",
                        "part": "V3",
                        "train": "viewer",
                        "parent": "V2",
                        "priority": 2,
                        "created_at": ts(5),
                        "verdict": "Running live verification of inline Markdown section embeds.",
                        "artifacts": ["docs/demo-guide.md"],
                        "body": (
                            "Referencing a Markdown section like `docs/demo-guide.md § Viewer Features` automatically embeds a scrollable inline viewer scrolled directly to that heading, with one-click **Comment** and **Open in Document Viewer** pop-out."
                        ),
                    }),
                    ("demo-viewer-later", {
                        "title": "Planned: PDF & audio inline preview",
                        "column": "info",
                        "status": "later",
                        "part": "V4",
                        "train": "viewer",
                        "parent": "V3",
                        "priority": 3,
                        "created_at": ts(1),
                        "verdict": "Queued for next wave after HTML and image viewer sign-off.",
                        "body": "Extend the unified viewer to preview repo PDFs and audio clips inline with timestamped comments.",
                    }),
                    ("demo-todo", {"title": "Try replying or tapping 👍", "column": "todo", "part": "D2", "train": "demo", "parent": "D1", "priority": 2, "minutes": 2, "created_at": ts(15), "body": "Leave a comment below or tap **👍 Like** to test the live round-trip."}),
                    ("demo-todo-2", {"title": "Review release checklist for v0.1", "column": "todo", "part": "R3", "train": "release", "parent": "R2", "priority": 1, "minutes": 5, "created_at": ts(16), "body": "CI is green across all 6 suites. Confirm tag name and changelog notes before publishing."}),
                    ("demo-question", {"title": "Pick your preferred workflow mode", "column": "question", "part": "D3", "train": "demo", "parent": "D2", "priority": 2, "created_at": ts(8), "options": ["Live 30-min listener", "Manual '.' check"], "default_option": "Live 30-min listener", "body": "One-tap answers let you decide from your phone without typing."}),
                    ("demo-q-2", {"title": "Default Done lane window", "column": "question", "part": "R4", "train": "release", "parent": "R3", "priority": 2, "created_at": ts(7), "options": ["Last 7 days (Recommended)", "Last 30 days", "Show all"], "default_option": "Last 7 days (Recommended)", "body": "Keeping Done scoped to recent cards prevents an infinite scroll wall at production volume."}),
                    ("demo-info-1", {"title": "Benchmark: mirror reads 12× smaller", "column": "info", "part": "R2", "train": "release", "parent": "R1", "priority": 3, "status": "in progress", "created_at": ts(12), "body": "Measured `kit.py post` vs full JSON dump:\n\n| Operation | Full dump | `kit.py` mirror |\n|---|---|---|\n| Single post | 35.9k chars | 2.9k chars |\n| Open comments | ~120k tokens | 290 chars |"}),
                    ("demo-info-2", {"title": "Nightly doctor check passed", "column": "done", "part": "D1", "train": "demo", "parent": "D0", "priority": 3, "created_at": ts(120), "done_at": ts(110), "body": "`kit.py doctor` verified SQLite integrity (`ok`), zero missing blobs, and clean mirror branch."}),
                    ("demo-demo-later", {"title": "Planned: Configure daily ntfy digest", "column": "info", "status": "later", "part": "D4", "train": "demo", "parent": "D3", "priority": 3, "created_at": ts(2), "verdict": "Scheduled after workflow mode is confirmed.", "body": "Optional push summary when questions wait longer than 30 minutes."}),
                    ("demo-release-later", {"title": "Planned: Tag v0.2 & publish release notes", "column": "info", "status": "later", "part": "R5", "train": "release", "parent": "R4", "priority": 3, "created_at": ts(1), "verdict": "Waiting on v0.1 checklist and Done window sign-off.", "body": "Cut signed tag and publish SHA-256 manifest."}),
                    ("demo-done", {"title": "Board kit installed", "column": "done", "part": "D0", "train": "demo", "priority": 3, "created_at": ts(280), "done_at": ts(275), "body": "SQLite board store, local server, and single-file UI are ready."}),
                    ("demo-done-2", {"title": "Safety hooks verified (kit_guard)", "column": "done", "part": "R0", "train": "release", "priority": 3, "created_at": ts(300), "done_at": ts(290), "body": "Destructive `rm -rf`, `git reset --hard`, force-push, and secret staging all blocked cleanly."}),
                    ("demo-done-3", {"title": "Gzip & security headers enabled", "column": "done", "part": "R1", "train": "release", "parent": "R0", "priority": 3, "created_at": ts(240), "done_at": ts(230), "body": "HTML payload compressed ~75% with `Referrer-Policy` and minimal CSP active."}),
                    ("demo-done-4", {"title": "Accessibility pass complete", "column": "done", "part": "D0b", "train": "demo", "priority": 3, "created_at": ts(260), "done_at": ts(250), "body": "Added `<html lang>`, un-nested card like buttons, and raised muted-text contrast ≥ 4.5:1."}),
                ]
                for dpid, ddoc in demo_posts:
                    if not store.get_doc("posts", dpid).get("exists"):
                        store.set_doc("posts", dpid, ddoc)
                demo_comments = [
                    ("demo-cmt-1", {"post_id": "demo-todo", "from": "D1", "text": "Ready for your check — tap 👍 if the layout looks good on your screen.", "created_at": ts(4)}),
                    ("demo-cmt-2", {"post_id": "demo-todo", "from": "owner", "text": "Looks crisp! Can we also verify dark mode?", "reply_to": "demo-cmt-1", "read": True, "delivered": True, "created_at": ts(3)}),
                    ("demo-cmt-3", {"post_id": "demo-todo", "from": "D1", "text": "Checked dark mode: per-lane tinted cards and ≥ 4.5:1 contrast both pass.", "reply_to": "demo-cmt-2", "created_at": ts(2)}),
                    ("demo-cmt-4", {"post_id": "demo-question", "from": "orchestrator", "text": "Defaulting to **Live 30-min listener** if you don't tap another option.", "created_at": ts(10)}),
                    ("demo-cmt-5", {"post_id": "demo-lmarena-research", "from": "orchestrator", "text": "Focus on what makes `board` genuinely distinct (phone-first approvals, token economics, session memory/handovers, zero-dep deployability) alongside an honest scorecard of where competitors beat it.", "created_at": ts(1)}),
                ]
                for dcid, cdoc in demo_comments:
                    if not store.get_doc("comments", dcid).get("exists"):
                        store.set_doc("comments", dcid, cdoc)
                import kit_cards as _kc
                prev_root = _kc.ROOT
                try:
                    _kc.ROOT = t
                    dash_doc = _kc.build(now)
                    trains_doc = _kc.build_trains(None, dash_doc["rows"], now)
                finally:
                    _kc.ROOT = prev_root
                store.set_doc("posts", "_dashboard", dash_doc)
                store.set_doc("posts", "_trains", trains_doc)
                ins.log.append("demo: seeded 13 sample posts, 5 comments, _dashboard, and _trains in .kit/board_data/board.db")
            ins.log.append("bootstrap: seeded _orchestrator and KIT-installed in .kit/board_data/board.db")
        finally:
            store.close()
    if want_claude:
        merge_claude_settings(t, a.dry_run, ins.log, parts["board"], CLAUDE_CLI_SETTINGS if claude_cli else CLAUDE_SETTINGS)
    import subprocess
    r = subprocess.run(["git", "-C", KIT, "log", "-1", "--format=%h %cI"], capture_output=True, text=True)
    kit_at = r.stdout.strip() or "unknown (not a git clone)"
    ins.manifest["_kit_commit"] = kit_at
    ins.log.append("kit version: " + kit_at) if ins.log else None
    ins.finish()
    print("\n".join(ins.log + ins.kept) if ins.log + ins.kept else "", end="\n" if ins.log + ins.kept else "")
    if not ins.log:
        print("nothing to change")
    print("\n".join(notes)) if notes else None
    if parts["board"] and c["language"]["talk"].lower() not in ("en", "english") and not os.path.exists(os.path.join(t, ".kit", "strings.json")):
        print("translate: `python3 -I .kit/strings.py template .kit/strings.json`, fill in %s, rerun this install (README step 3)" % c["language"]["talk"])
    if not parts["board"]:
        print("next: the memory is installed (no board): import the project's state into %s (README § Import), commit, push; then --setup-done" % mdir)
    elif claude_only:
        print("next: publish .kit/board.html as a claude.ai Artifact, rerun with --board-url <url>, then README § Install (Claude Code Web)")
        if not c["kit"].get("notify_url"):
            print("notify: ask the owner if they want phone push notifications (ntfy.sh app + account token via request_credential + --notify-url; see README § Install Claude Code Web)")
    elif rt == "arena":
        print("next: start the sandbox preview board server (`python3 -I .kit/board_server.py --host 0.0.0.0 --port 8765 --preview --embed-mode`), post `python3 -I .kit/kit.py welcome --runtime arena --post`, and immediately arm the 30-min listener (`python3 -I .kit/board_cli.py comments wait --timeout 1750`)" )
    else:
        cmd_word = c["kit"].get("cmd") or "./.kit/up"
        port_m = re.search(r":(\d+)", c.get("board_url") or "")
        bport = port_m.group(1) if port_m else "8765"
        import tempfile as _tf
        _tmp_prefix = os.path.abspath(_tf.gettempdir()) + os.sep
        _in_tmp = os.path.abspath(t).startswith(_tmp_prefix)
        if c["kit"].get("funnel", True) and not a.dry_run and not _in_tmp and os.environ.get("KIT_NO_FUNNEL") != "1":
            import shutil as _sh
            if _sh.which("tailscale"):
                rf = subprocess.run(["tailscale", "funnel", "--bg", bport], capture_output=True, text=True)
                if rf.returncode == 0:
                    print("funnel: enabled on port %s (works on phone & NextDNS without Tailscale app)" % bport)
                else:
                    print("funnel (one-time setup for phone + NextDNS + LMArena): sudo tailscale set --operator=$USER && tailscale funnel --bg %s" % bport)
        cli = c["kit"].get("cli") or "agy"
        print("cli: `%s` opens %s (the other one: `%s --%s`, or rerun with --cli %s)" % (
            cmd_word, cli, cmd_word, "claude" if cli == "agy" else "agy", "claude" if cli == "agy" else "agy"))
        print("next: run `%s` from any terminal (starts board on %s, Funnel, and orchestrator + clerk)" % (cmd_word, c.get("board_url", "http://127.0.0.1:8765")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
