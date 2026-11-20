#!/usr/bin/env python3
"""Install the kit into a project: python3 install.py <project dir> --project "<Name>" --survey <step 0 report>
                                     [--talk English] [--board-url URL] [--main-branch <trunk>] [--dry-run]
                                     [--no-board | --no-clerk | --no-orchestrator | --parts memory,board,...]
                                     [--setup-done]

Step 0 comes first (docs/KIT-GOALS.md, README § Step 0): a first install needs the survey's report (`--survey PATH`),
or `--no-survey "<why>"`, recorded in the config. The parts it installs are the report's recommendation: `memory`
(always: .story, the story skill, the start/stop/compact/guard hooks, end_session.py), `board` (the page, its rules,
kit.py, the cards, the say-it-once and typing hooks), `clerk`, `orchestrator` (with the consultant and runners); a
clerk or an orchestrator needs the board. The trunk is origin's HEAD unless `--main-branch` names one, and a branch
missing on origin stops the install. A translation is `.kit/strings.json` (kit/board/strings.py), applied at every
install, so the page is never edited by hand. Until `--setup-done` (README step 8), the stop hooks stay quiet.

Copies, renders and merges; never deletes, never replaces what it did not write:
  .kit/            config.json (written once, then only the fields you pass change), kit.py, clerk_pending.py,
                   board.html (the page to publish), clerk.md (the clerk's prompt), BOARD.md, ORCHESTRATOR.md,
                   manifest.json (what the kit wrote, with hashes)
  .claude/hooks/kit/   the generic hooks and their self-test
  .claude/skills/story, .claude/skills/board   (skipped, and said, when the project has its own file there)
  .claude/settings.json   the kit's hook lines MERGED in: every existing line kept, a kit line added only if absent
  .story/          state/, issues/, lessons/, awaits.md, if absent
A file the kit wrote before is updated only if nobody changed it since (its hash is in the manifest); otherwise it is
left alone and listed. Run it again with --board-url once the page is published: the url goes into every file.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(KIT, "kit", "board"))
import strings as page_strings  # noqa: E402
PARTS = ("memory", "board", "clerk", "orchestrator")
MEMORY_HOOKS = {"kit_config.py", "kit_guard.py", "kit_session_start.py", "kit_stop.py", "kit_pre_compact.py", "test_hooks.py"}
HOOKS = ["kit_config.py", "kit_guard.py", "kit_session_start.py", "kit_stop.py", "kit_pre_compact.py",
         "kit_say_it_once.py", "kit_board_typing.py", "test_hooks.py"]
CMD = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/kit/%s"'
SETTINGS = [  # (event, matcher or None, hook file); the say-it-once and typing lines belong to the board
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
    "talk_language": "English",
    "limits": {"message_chars": 600, "done_message_chars": 240, "chat_answer_chars": 600},
    "guard": {"never_add": ["*.keystore", "*.jks", "*.pem", ".env", "keystore.properties"], "allow_add": []},
    "session_start_lines": [],
    "clerk": {"wait_minutes": 5, "context_limit_k": 200},
    # Each role's model and effort, as Harmonize runs them (2026-11-20): the clerk only forwards (Haiku, low); the
    # orchestrator and the parts think (Opus, medium); the cards agent runs scripts (Sonnet, its file's frontmatter).
    "roles": {"orchestrator": {"model": "claude-opus-5-5", "effort": "medium"},
              "clerk": {"model": "claude-haiku-5-5", "effort": "low"},
              "part": {"model": "claude-opus-5-5", "effort": "medium"},
              "consultant": {"model": "claude-fable-5-2", "effort": "medium"}},
    "main_branch": "main",
    # The owner's standing permission for sessions to push the trunk (asked once, README step 2: --trunk-push).
    "trunk_push": False,
}


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
            if rel.endswith(".py"):
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
        k["setup"] = True  # the stop hooks stay quiet until README step 8 runs --setup-done (issue #3 item 5)
        k["main_branch"] = None  # detected below unless --main-branch names it
    for role, v in DEFAULT_KIT["roles"].items():  # a role added to the kit after this project's install
        k["roles"].setdefault(role, v)
    if a.project:
        c["project"] = a.project
    c.setdefault("project", os.path.basename(t))
    if a.board_url:
        c["board_url"] = a.board_url
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
    return c


def trunk(t, k, named, log):
    """origin's HEAD unless named; a named or detected branch missing on origin stops the install (issue #3 item 3)."""
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
            "BOARD_URL": c.get("board_url") or "(not published yet: README step 3)",
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


def merge_settings(t, dry, log, board=True):
    p = os.path.join(t, ".claude", "settings.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    hooks = s.setdefault("hooks", {})
    added = 0
    for ev, matcher, f in SETTINGS:
        if not board and f not in MEMORY_HOOKS:
            continue
        cmd = CMD % f
        groups = hooks.setdefault(ev, [])
        if any(h.get("command") == cmd for g in groups for h in g.get("hooks", []) if (g.get("matcher") or None) == matcher):
            continue
        g = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            g = {"matcher": matcher, **g}
        groups.append(g)
        added += 1
    if added:
        log.append("settings.json: %d kit hook lines merged in (existing lines kept)" % added)
        if not dry:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2, ensure_ascii=False)
                fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target")
    ap.add_argument("--project")
    ap.add_argument("--talk", help="the language to talk to the owner in (default English)")
    ap.add_argument("--board-url")
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
    ins.put(".kit/end_session.py", rd("kit", "end_session.py"))
    ins.put(".kit/kit_import.py", rd("kit", "import", "kit_import.py"))
    for h in HOOKS:
        if parts["board"] or h in MEMORY_HOOKS:
            ins.put(".claude/hooks/kit/" + h, rd("kit", "hooks", h))
    ins.put(".claude/skills/story/SKILL.md", rd("kit", "skills", "story", "SKILL.md"))
    if parts["board"]:
        ins.put(".kit/kit.py", rd("scripts", "kit.py"))
        page = rd("kit", "board", "board.template.html")
        sp = os.path.join(t, ".kit", "strings.json")
        if os.path.exists(sp):  # the owner's language, from the string table (issue #3 item 4)
            tr = json.load(open(sp, encoding="utf-8"))
            page, used, missing = page_strings.apply(page, page_strings.load_table(), tr.get("strings") or {})
            notes.append("page: %d strings in %s, %d untranslated (.kit/strings.json; add them there, never in the page)" % (used, tr.get("language", "?"), len(missing)))
        ins.put(".kit/board.html", render(page, c))
        ins.put(".kit/strings.en.json", rd("kit", "board", "strings.en.json"))
        ins.put(".kit/strings.py", rd("kit", "board", "strings.py"))
        ins.put(".kit/kit_cards.py", rd("kit", "board", "kit_cards.py"))
        ins.put(".kit/BOARD.md", render(rd("kit", "board", "BOARD.template.md"), c))
        ins.put(".claude/agents/kit-cards.md", rd("kit", "agents", "kit-cards.md"))
        ins.put(".claude/skills/board/SKILL.md", rd("kit", "skills", "board", "SKILL.md"))
    if parts["clerk"]:
        ins.put(".kit/clerk_pending.py", rd("kit", "clerk", "clerk_pending.py"))
        ins.put(".kit/clerk.md", render(rd("kit", "clerk", "clerk.template.md"), c))
    if parts["orchestrator"]:
        ins.put(".kit/orchestrator.md", render(rd("kit", "orchestrator.template.md"), c))
        ins.put(".kit/consultant.md", render(rd("kit", "consultant.template.md"), c))
        ins.put(".kit/runner.md", render(rd("kit", "runner.template.md"), c))
        ins.put(".kit/ORCHESTRATOR.md", render(rd("kit", "ORCHESTRATOR.template.md"), c))
    for d in ("state", "issues", "lessons"):
        if not os.path.isdir(os.path.join(t, ".story", d)):
            ins.put(".story/%s/.gitkeep" % d, "")
    if not os.path.exists(os.path.join(t, ".story", "awaits.md")):
        ins.put(".story/awaits.md", "# What awaits the owner\n\n")
    gi = os.path.join(t, ".gitignore")
    cur = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    if ".story/before-compaction.md" not in cur:
        ins.log.append(".gitignore: + .story/before-compaction.md")
        if not a.dry_run:
            open(gi, "a", encoding="utf-8").write(("" if cur.endswith("\n") or not cur else "\n") + ".story/before-compaction.md\n")
    merge_settings(t, a.dry_run, ins.log, parts["board"])
    # Which kit this is (2026-11-20: a session said "the kit was never updated" while it was): the kit's commit, kept in
    # the manifest and printed, to compare with `git ls-remote https://github.com/anlonlt/board main`.
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
        print("next: the memory is installed (no board): import the project's state into .story (README § Import), commit, push; then --setup-done")
    else:
        print("next: %s" % ("README step 3: publish .kit/board.html" if not c.get("board_url") else "README step 4: the first posts, then step 5: %s" % (
            " and ".join(x for x in ("the orchestrator", "the clerk") if parts[x.split()[-1]]) or "README step 6")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
