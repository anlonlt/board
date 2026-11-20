#!/usr/bin/env python3
"""Install the board kit for agy on a computer:
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
  .story/             state/, issues/, lessons/, awaits.md, if absent
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
         "kit_say_it_once.py", "kit_board_typing.py", "test_hooks.py"]
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
    ("Stop", None, "kit_stop.py"),
    ("Stop", None, "kit_say_it_once.py"),
    ("PreCompact", None, "kit_pre_compact.py"),
]

DEFAULT_KIT = {
    "runtime": "agy",
    "mode": "local",
    "talk_language": "English",
    "limits": {"message_chars": 600, "done_message_chars": 240, "chat_answer_chars": 600},
    "guard": {"never_add": ["*.keystore", "*.jks", "*.pem", ".env", "keystore.properties"], "allow_add": []},
    "session_start_lines": [],
    "clerk": {"wait_minutes": 5, "context_limit_k": 200},
    # agy subagent model tiers: clerk only forwards (flash_lite), cards runs scripts (flash),
    # orchestrator/parts inherit the session model, consultant uses pro.
    "roles": {"orchestrator": {"model": "inherit", "effort": "medium"},
              "clerk": {"model": "flash_lite", "effort": "low"},
              "part": {"model": "inherit", "effort": "medium"},
              "consultant": {"model": "pro", "effort": "medium"},
              "cards": {"model": "flash", "effort": "low"}},
    "main_branch": "main",
    "trunk_push": False,
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
        k.setdefault(key, v)
    if first:
        k["setup"] = True
        k["main_branch"] = None
    for role, v in DEFAULT_KIT["roles"].items():
        k["roles"].setdefault(role, v)
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
    if parts["board"] and not c.get("board_url") and k.get("mode", "local") == "local" and k.get("runtime", "agy") != "claude":
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


def render(text, c):
    k = c["kit"]
    vals = {"PROJECT": c["project"], "SLUG": slug(c["project"]),
            "CMD": k.get("cmd") or "./.kit/up",
            "BOARD_URL": c.get("board_url") or "http://127.0.0.1:8765",
            "MEMORY_DIR": k.get("memory_dir") or ".board/memory",
            "TALK": c["language"]["talk"], "MAIN_BRANCH": k["main_branch"],
            "WAIT": str(k["clerk"]["wait_minutes"]), "CONTEXT_K": str(k["clerk"]["context_limit_k"]),
            "CLERK": c.get("fields", {}).get("clerk", "clerk"),
            "MIRROR_BRANCH": c["mirror"]["branch"], "MIRROR_PATH": c["mirror"]["path"],
            "MSG": str(k["limits"]["message_chars"]), "DONE": str(k["limits"]["done_message_chars"]),
            "PART_MODEL": k["roles"]["part"]["model"], "PART_EFFORT": k["roles"]["part"]["effort"],
            "CONS_MODEL": k["roles"].get("consultant", DEFAULT_KIT["roles"]["consultant"])["model"],
            "CONS_EFFORT": k["roles"].get("consultant", DEFAULT_KIT["roles"]["consultant"])["effort"]}
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


def merge_claude_settings(t, dry, log, board=True):
    p = os.path.join(t, ".claude", "settings.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    hooks = s.setdefault("hooks", {})
    added = 0
    for ev, matcher, f in CLAUDE_SETTINGS:
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


def install_cmd(t, cmd_name, dry, log):
    """Install a one-word launcher in ~/.local/bin/<cmd_name> (override dir with $KIT_BIN_DIR)."""
    if not cmd_name or "/" in cmd_name or " " in cmd_name:
        return
    bin_dir = os.environ.get("KIT_BIN_DIR") or os.path.expanduser("~/.local/bin")
    dst = os.path.join(bin_dir, cmd_name)
    marker = "# kit-board launcher"
    content = "#!/usr/bin/env bash\n%s\nexec %s \"$@\"\n" % (marker, json.dumps(os.path.join(t, ".kit", "up")))
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
    ap.add_argument("--runtime", choices=["agy", "claude", "both"], help="which harness configs to install (default agy)")
    ap.add_argument("--mode", choices=["local", "hybrid"], help="local: localhost board_server; hybrid: Claude Artifact + git_watch.py")
    ap.add_argument("--cmd", help="one-word terminal command installed in ~/.local/bin/<cmd> (default: project slug)")
    ap.add_argument("--no-cmd", action="store_true", help="skip installing the ~/.local/bin/<cmd> shortcut")
    ap.add_argument("--memory-dir", help="memory directory relative to repo root (default: .story if present, else .board/memory)")
    ap.add_argument("--notify-url", help="optional phone push webhook URL (e.g. https://ntfy.sh/<topic>)")
    ap.add_argument("--bootstrap", action="store_true", help="1-command bootstrap: pre-seed _orchestrator & KIT-installed into local SQLite DB")
    ap.add_argument("--main-branch")
    ap.add_argument("--trunk-push", choices=["yes", "no"], help="the owner's standing permission to push the trunk")
    ap.add_argument("--survey", help="step 0's report (survey/<date>/report.md): required at the first install")
    ap.add_argument("--no-survey", metavar="WHY", help="install without step 0 (the reason is recorded)")
    ap.add_argument("--parts", help="comma list among memory,board,clerk,orchestrator (memory always)")
    ap.add_argument("--no-board", action="store_true", help="memory and its hooks only (no page, clerk, orchestrator)")
    ap.add_argument("--no-clerk", action="store_true")
    ap.add_argument("--no-orchestrator", action="store_true")
    ap.add_argument("--setup-done", action="store_true", help="README step 8: the install is reported; hooks speak again")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    t = os.path.abspath(a.target)
    if not os.path.isdir(t):
        raise SystemExit("install: %s is not a directory" % t)
    first = not os.path.exists(os.path.join(t, ".kit", "config.json"))
    if first and not (a.survey or a.no_survey):
        raise SystemExit("install: step 0 first. Run the survey (step0/survey-code.md or step0/survey-chat.md) and pass "
                         "its report with --survey survey/<date>/report.md; its recommendation says which parts to install "
                         "(--no-board, --no-clerk, --no-orchestrator). Only if the owner declines step 0: --no-survey \"<why>\".")
    if a.survey and not os.path.isfile(a.survey):
        raise SystemExit("install: --survey %s: no such file" % a.survey)
    c = load_config(t, a)
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
    rt = c["kit"].get("runtime", "agy")
    claude_only = rt == "claude"
    want_agy = rt in ("agy", "both")
    want_claude = rt in ("claude", "both")
    ins.put(".kit/end_session.py", rd("kit", "end_session.py"))
    ins.put(".kit/kit_import.py", rd("kit", "import", "kit_import.py"))
    if not claude_only:
        ins.put(".kit/git_watch.py", rd("kit", "git_watch.py"))
        ins.put(".kit/up", rd("kit", "up"))
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
            ins.put(".kit/board_cli.py", rd("kit", "board", "board_cli.py"))
        page = rd("kit", "board", "board.template.html")
        sp = os.path.join(t, ".kit", "strings.json")
        if os.path.exists(sp):
            tr = json.load(open(sp, encoding="utf-8"))
            page, used, missing = page_strings.apply(page, page_strings.load_table(), tr.get("strings") or {})
            notes.append("page: %d strings in %s, %d untranslated (.kit/strings.json; add them there, never in the page)" % (used, tr.get("language", "?"), len(missing)))
        ins.put(".kit/board.html", render(page, c))
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
            ins.put(".claude/agents/kit-cards.md", cards_tpl)
            ins.put(".claude/skills/board/SKILL.md", rd("kit", "skills", "board", "SKILL.md"))
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
            ins.put(".kit/orchestrator.md", render(rd("kit", "orchestrator.template.md"), c))
            ins.put(".kit/consultant.md", render(rd("kit", "consultant.template.md"), c))
            ins.put(".kit/runner.md", render(rd("kit", "runner.template.md"), c))
            ins.put(".kit/ORCHESTRATOR.md", render(rd("kit", "ORCHESTRATOR.template.md"), c))
    mdir = c["kit"].get("memory_dir") or ".board/memory"
    for d in ("state", "issues", "lessons"):
        if not os.path.isdir(os.path.join(t, mdir, d)):
            ins.put("%s/%s/.gitkeep" % (mdir, d), "")
    if not os.path.exists(os.path.join(t, mdir, "awaits.md")):
        ins.put("%s/awaits.md" % mdir, "# What awaits the owner\n\n")
    gi = os.path.join(t, ".gitignore")
    cur = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    gi_add = [x for x in ("%s/before-compaction.md" % mdir, ".kit/board_data/") if x not in cur]
    if gi_add:
        ins.log.append(".gitignore: + " + ", ".join(gi_add))
        if not a.dry_run:
            prefix = "" if cur.endswith("\n") or not cur else "\n"
            open(gi, "a", encoding="utf-8").write(prefix + "\n".join(gi_add) + "\n")
    if want_agy:
        merge_hooks(t, a.dry_run, ins.log, parts["board"])
        if c["kit"].get("cmd"):
            install_cmd(t, c["kit"]["cmd"], a.dry_run, ins.log)
    if a.bootstrap and parts["board"] and not a.dry_run:
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
                    "body": "Bootstrapped by `install.py --bootstrap`.\n\nRun `%s` from any terminal to start the board, orchestrator, and clerk." % (c["kit"].get("cmd") or "./.kit/up"),
                })
            ins.log.append("bootstrap: seeded _orchestrator and KIT-installed in .kit/board_data/board.db")
        finally:
            store.close()
    if want_claude:
        merge_claude_settings(t, a.dry_run, ins.log, parts["board"])
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
    else:
        print("next: start the local board server (`python3 -I .kit/board_server.py`), open %s, then README step 4" % c.get("board_url", "http://127.0.0.1:8765"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
