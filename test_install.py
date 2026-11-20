#!/usr/bin/env python3
"""install.py on a planted project (agy on a computer): existing hooks and files are kept, a rerun changes nothing,
the url reaches every file, the installed hooks pass their self-test and the installed kit.py runs. python3 -I test_install.py"""
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


def all_hook_cmds(hooks_json):
    kb = hooks_json.get("kit-board", {})
    cmds = []
    for ev in ("SessionStart", "PreInvocation", "PostInvocation", "Stop"):
        for h in kb.get(ev, []):
            cmds.append(h["command"])
    for ev in ("PreToolUse", "PostToolUse"):
        for g in kb.get(ev, []):
            for h in g.get("hooks", []):
                cmds.append(h["command"])
    return cmds


with tempfile.TemporaryDirectory() as d:
    t = os.path.join(d, "proj")
    os.makedirs(os.path.join(t, ".agents", "skills", "story"))
    mine = {"my-linter": {"Stop": [{"type": "command", "command": "echo mine"}]}}
    json.dump(mine, open(os.path.join(t, ".agents", "hooks.json"), "w"))
    open(os.path.join(t, ".agents", "skills", "story", "SKILL.md"), "w").write("my own story skill\n")
    open(os.path.join(t, ".gitignore"), "w").write("build/")
    rep = os.path.join(d, "report.md")
    open(rep, "w").write("# step 0 report\n")
    rc, out = inst(t, "--project", "Tide Clock", "--talk", "French", "--survey", rep)
    check("install runs", rc == 0)
    s = json.load(open(os.path.join(t, ".agents", "hooks.json")))
    check("hooks.json: the project's own named hook kept", s.get("my-linter") == mine["my-linter"])
    cmds = all_hook_cmds(s)
    check("hooks.json: 10 kit handlers merged into kit-board", sum("hooks/kit/" in c for c in cmds) == 10)
    check("a project's own skill is not overwritten", open(os.path.join(t, ".agents", "skills", "story", "SKILL.md")).read() == "my own story skill\n"
          and "KEPT" in out)
    page = open(os.path.join(t, ".kit", "board.html"), encoding="utf-8").read()
    check("page: the project's name, no Harmonize, no placeholder", "Tide Clock board" in page and "armoniz" not in page and "{{" not in page)
    check(".gitignore appended, not replaced", open(os.path.join(t, ".gitignore")).read() == "build/\n.story/before-compaction.md\n.kit/board_data/\n")
    rc, out = inst(t)
    check("a rerun changes nothing", rc == 0 and "nothing to change" in out)
    open(os.path.join(t, ".kit", "BOARD.md"), "a").write("my note\n")
    rc, out = inst(t, "--board-url", "http://127.0.0.1:9999")
    clerk = open(os.path.join(t, ".kit", "clerk.md"), encoding="utf-8").read()
    check("the url reaches the clerk's prompt", "http://127.0.0.1:9999" in clerk)
    orch = open(os.path.join(t, ".kit", "orchestrator.md"), encoding="utf-8").read()
    check("the orchestrator's prompt: url, language, the welcome", "http://127.0.0.1:9999" in orch and "French" in orch and "welcome" in orch)
    check("kit_cards.py, board_server.py and board_cli.py installed",
          all(os.path.exists(os.path.join(t, ".kit", f)) for f in ("kit_cards.py", "board_server.py", "board_cli.py")))
    check("parts launched via invoke_subagent with Workspace share", "invoke_subagent" in orch and 'Workspace: "share"' in orch)
    cfgr = json.load(open(os.path.join(t, ".kit", "config.json")))["kit"]["roles"]
    check("roles in config: clerk flash_lite low", cfgr["clerk"] == {"model": "flash_lite", "effort": "low"})
    check("the consultant's prompt installed, the orchestrator launches it at its level",
          os.path.exists(os.path.join(t, ".kit", "consultant.md")) and 'Model="pro"' in orch and "consultant.md" in orch)
    check("the kit's commit recorded", "_kit_commit" in json.load(open(os.path.join(t, ".kit", "manifest.json"))))
    cw = open(os.path.join(t, ".kit", "runner.md"), encoding="utf-8").read()
    check("one runner prompt for any place: pulls its cards, answers in its name, in the owner's language",
          '"runner","==","<RUNNER>"' in cw and '"from":"<RUNNER>"' in cw and "French" in cw)
    check("the orchestrator sends each step to its runner, and its welcome has a Setup part",
          'runner: "<name>"' in orch and "**Setup:**" in orch)
    check("end_session.py installed", os.path.exists(os.path.join(t, ".kit", "end_session.py")))
    check("the orchestrator cleans up what he moved to Done", "manage_subagents" in orch)
    check("the kit-cards agent prompt installed", os.path.exists(os.path.join(t, ".kit", "agents", "kit-cards.md")))
    check("the orchestrator calls the agent, never lists posts itself", "kit-cards" in orch and "list posts" not in orch)
    check("a kit file edited by the project is kept", "my note" in open(os.path.join(t, ".kit", "BOARD.md")).read() and "KEPT" in out)
    cfg = json.load(open(os.path.join(t, ".kit", "config.json")))
    check("config keeps earlier answers", cfg["project"] == "Tide Clock" and cfg["language"]["talk"] == "French"
          and cfg["board_url"].endswith(":9999"))
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".agents", "hooks", "kit", "test_hooks.py")], capture_output=True, text=True)
    check("the installed hooks pass their self-test", r.returncode == 0)
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".kit", "kit.py"), "reads"], capture_output=True, text=True, cwd=t)
    check("the installed kit.py runs from its config", r.returncode == 0 and "bootstrap" in r.stdout)
    r = subprocess.run([sys.executable, "-I", os.path.join(t, ".kit", "clerk_pending.py"), "--dir", os.path.join(d, "clerk")], capture_output=True, text=True)
    check("the installed clerk_pending.py runs", r.returncode == 0)

    # Dual runtime (both .agents/ and .claude/) + hybrid mode (Claude Artifact + git_watch.py)
    t_hyb = os.path.join(d, "hyb")
    os.makedirs(t_hyb)
    rc, out = inst(t_hyb, "--project", "Harmonize", "--survey", rep, "--runtime", "both", "--mode", "hybrid",
                   "--board-url", "https://claude.ai/artifact/H1")
    check("dual runtime + hybrid mode installs both .agents/ and .claude/ plus git_watch.py",
          rc == 0
          and os.path.isfile(os.path.join(t_hyb, ".agents", "hooks.json"))
          and os.path.isfile(os.path.join(t_hyb, ".claude", "settings.json"))
          and os.path.isfile(os.path.join(t_hyb, ".kit", "git_watch.py"))
          and json.load(open(os.path.join(t_hyb, ".kit", "config.json")))["kit"]["mode"] == "hybrid")
    r_ss = subprocess.run([sys.executable, "-I", os.path.join(t_hyb, ".agents", "hooks", "kit", "kit_session_start.py")],
                          input=json.dumps({"hookEventName": "SessionStart", "cwd": t_hyb}), capture_output=True, text=True)
    check("hybrid SessionStart automatically briefs new agy discussions on git_watch.py and skills",
          r_ss.returncode == 0 and "MODE (hybrid)" in r_ss.stdout and "git_watch.py" in r_ss.stdout)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
