#!/usr/bin/env python3
"""install.py on a planted project: existing settings and files are kept, a rerun changes nothing, the url
reaches every file, the installed hooks pass their self-test and the installed kit.py runs. python3 -I test_install.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def inst(t, *a):
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "install.py"), t] + list(a), capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


with tempfile.TemporaryDirectory() as d:
    t = os.path.join(d, "proj")
    os.makedirs(os.path.join(t, ".claude", "skills", "story"))
    mine = {"permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    json.dump(mine, open(os.path.join(t, ".claude", "settings.json"), "w"))
    open(os.path.join(t, ".claude", "skills", "story", "SKILL.md"), "w").write("my own story skill\n")
    open(os.path.join(t, ".gitignore"), "w").write("build/")
    rc, out = inst(t, "--project", "Tide Clock", "--talk", "French")
    check("install runs", rc == 0)
    s = json.load(open(os.path.join(t, ".claude", "settings.json")))
    check("settings: the project's own lines kept", s["permissions"] == mine["permissions"]
          and {"type": "command", "command": "echo mine"} in s["hooks"]["Stop"][0]["hooks"])
    cmds = [h["command"] for ev in s["hooks"].values() for g in ev for h in g["hooks"]]
    check("settings: 9 kit lines merged", sum("hooks/kit/" in c for c in cmds) == 9)
    check("a project's own skill is not overwritten", open(os.path.join(t, ".claude", "skills", "story", "SKILL.md")).read() == "my own story skill\n"
          and "KEPT" in out)
    page = open(os.path.join(t, ".kit", "board.html"), encoding="utf-8").read()
    check("page: the project's name, no Harmonize, no placeholder", "Tide Clock board" in page and "armoniz" not in page and "{{" not in page)
    check(".gitignore appended, not replaced", open(os.path.join(t, ".gitignore")).read() == "build/\n.story/before-compaction.md\n")
    rc, out = inst(t)
    check("a rerun changes nothing", rc == 0 and "nothing to change" in out)
    open(os.path.join(t, ".kit", "BOARD.md"), "a").write("my note\n")
    rc, out = inst(t, "--board-url", "https://claude.ai/artifact/XYZ")
    clerk = open(os.path.join(t, ".kit", "clerk.md"), encoding="utf-8").read()
    check("the url reaches the clerk's prompt", "https://claude.ai/artifact/XYZ" in clerk)
    orch = open(os.path.join(t, ".kit", "orchestrator.md"), encoding="utf-8").read()
    check("the orchestrator's prompt: url, language, the welcome", "https://claude.ai/artifact/XYZ" in orch and "French" in orch and "welcome" in orch)
    check("kit_cards.py installed", os.path.exists(os.path.join(t, ".kit", "kit_cards.py")))
    check("the kit-cards agent installed", os.path.exists(os.path.join(t, ".claude", "agents", "kit-cards.md")))
    check("the orchestrator calls the agent, never lists posts itself", "kit-cards" in orch and "list` `posts`" not in orch)
    check("a kit file edited by the project is kept", "my note" in open(os.path.join(t, ".kit", "BOARD.md")).read() and "KEPT" in out)
    cfg = json.load(open(os.path.join(t, ".kit", "config.json")))
    check("config keeps earlier answers", cfg["project"] == "Tide Clock" and cfg["language"]["talk"] == "French"
          and cfg["board_url"].endswith("XYZ"))
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".claude", "hooks", "kit", "test_hooks.py")], capture_output=True, text=True)
    check("the installed hooks pass their self-test", r.returncode == 0)
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".kit", "kit.py"), "reads"], capture_output=True, text=True, cwd=t)
    check("the installed kit.py runs from its config", r.returncode == 0 and "bootstrap" in r.stdout)
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".kit", "clerk_pending.py"), "--dir", os.path.join(d, "clerk")], capture_output=True, text=True)
    check("the installed clerk_pending.py runs", r.returncode == 0)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
