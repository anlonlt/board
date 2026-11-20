#!/usr/bin/env python3
"""Install the kit into a project: python3 install.py <project dir> --project "<Name>" [--talk English]
                                     [--board-url URL] [--main-branch main] [--dry-run]

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
HOOKS = ["kit_config.py", "kit_guard.py", "kit_session_start.py", "kit_stop.py", "kit_pre_compact.py",
         "kit_say_it_once.py", "kit_board_typing.py", "test_hooks.py"]
CMD = 'python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/kit/%s"'
SETTINGS = [  # (event, matcher or None, hook file)
    ("SessionStart", None, "kit_session_start.py"),
    ("PreToolUse", "Bash", "kit_guard.py"),
    ("PreToolUse", "mcp__claude-code-remote__send_message|SendMessage", "kit_say_it_once.py"),
    ("PostToolUse", "ArtifactData|mcp__claude-code-remote__send_message", "kit_say_it_once.py"),
    ("UserPromptSubmit", None, "kit_say_it_once.py"),
    ("UserPromptSubmit", None, "kit_board_typing.py"),
    ("Stop", None, "kit_stop.py"),
    ("PreCompact", None, "kit_pre_compact.py"),
]
DEFAULT_KIT = {
    "talk_language": "English",
    "limits": {"message_chars": 600, "done_message_chars": 240, "chat_answer_chars": 600},
    "guard": {"never_add": ["*.keystore", "*.jks", "*.pem", ".env", "keystore.properties"], "allow_add": []},
    "session_start_lines": [],
    "clerk": {"wait_minutes": 5, "context_limit_k": 200},
    "main_branch": "main",
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
    for key, v in DEFAULT_KIT.items():
        k.setdefault(key, v)
    if a.project:
        c["project"] = a.project
    c.setdefault("project", os.path.basename(t))
    if a.board_url:
        c["board_url"] = a.board_url
    if a.talk:
        c.setdefault("language", {})["talk"] = a.talk
    c.setdefault("language", {}).setdefault("talk", k["talk_language"])
    if a.main_branch:
        k["main_branch"] = a.main_branch
    return c


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
            "MSG": str(k["limits"]["message_chars"]), "DONE": str(k["limits"]["done_message_chars"])}
    for n, v in vals.items():
        text = text.replace("{{%s}}" % n, v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        raise SystemExit("install: unrendered %s" % sorted(set(left)))
    return text


def merge_settings(t, dry, log):
    p = os.path.join(t, ".claude", "settings.json")
    s = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    hooks = s.setdefault("hooks", {})
    added = 0
    for ev, matcher, f in SETTINGS:
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
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    t = os.path.abspath(a.target)
    if not os.path.isdir(t):
        raise SystemExit("install: %s is not a directory" % t)
    c = load_config(t, a)
    ins = Installer(t, a.dry_run)
    cfg = json.dumps(c, indent=1, ensure_ascii=False) + "\n"
    p = os.path.join(t, ".kit", "config.json")
    if not os.path.exists(p) or open(p, encoding="utf-8").read() != cfg:
        ins.log.append(("wrote " if not os.path.exists(p) else "updated ") + ".kit/config.json")
        ins.manifest.pop(".kit/config.json", None)
        if not a.dry_run:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(cfg)
    rd = lambda *x: open(os.path.join(KIT, *x), encoding="utf-8").read()
    ins.put(".kit/kit.py", rd("scripts", "kit.py"))
    ins.put(".kit/clerk_pending.py", rd("kit", "clerk", "clerk_pending.py"))
    ins.put(".kit/board.html", render(rd("kit", "board", "board.template.html"), c))
    ins.put(".kit/clerk.md", render(rd("kit", "clerk", "clerk.template.md"), c))
    ins.put(".kit/BOARD.md", render(rd("kit", "board", "BOARD.template.md"), c))
    ins.put(".kit/ORCHESTRATOR.md", render(rd("kit", "ORCHESTRATOR.template.md"), c))
    for h in HOOKS:
        ins.put(".claude/hooks/kit/" + h, rd("kit", "hooks", h))
    for s in ("story", "board"):
        ins.put(".claude/skills/%s/SKILL.md" % s, rd("kit", "skills", s, "SKILL.md"))
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
    merge_settings(t, a.dry_run, ins.log)
    ins.finish()
    print("\n".join(ins.log + ins.kept) if ins.log + ins.kept else "", end="\n" if ins.log + ins.kept else "")
    if not ins.log:
        print("nothing to change")
    print("next: %s" % ("README step 3: publish .kit/board.html" if not c.get("board_url") else "README step 4: the clerk"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
