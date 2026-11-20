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


def inst(t, *a, bin_dir=None):
    env = dict(os.environ)
    if bin_dir:
        env["KIT_BIN_DIR"] = bin_dir
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "install.py"), t] + list(a), capture_output=True, text=True, env=env)
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
    os.environ["KIT_BIN_DIR"] = os.path.join(d, "bin")
    t = os.path.join(d, "proj")
    os.makedirs(os.path.join(t, ".agents", "skills", "memory"))
    mine = {"my-linter": {"Stop": [{"type": "command", "command": "echo mine"}]}}
    json.dump(mine, open(os.path.join(t, ".agents", "hooks.json"), "w"))
    open(os.path.join(t, ".agents", "skills", "memory", "SKILL.md"), "w").write("my own memory skill\n")
    open(os.path.join(t, ".gitignore"), "w").write("build/")
    rep = os.path.join(d, "report.md")
    open(rep, "w").write("# step 0 report\n")
    rc, out = inst(t, "--project", "Tide Clock", "--talk", "French", "--survey", rep)
    check("install runs", rc == 0)
    s = json.load(open(os.path.join(t, ".agents", "hooks.json")))
    check("hooks.json: the project's own named hook kept", s.get("my-linter") == mine["my-linter"])
    cmds = all_hook_cmds(s)
    check("hooks.json: 10 kit handlers merged into kit-board", sum("hooks/kit/" in c for c in cmds) == 10)
    check("a project's own skill is not overwritten", open(os.path.join(t, ".agents", "skills", "memory", "SKILL.md")).read() == "my own memory skill\n"
          and "KEPT" in out)
    page = open(os.path.join(t, ".kit", "board.html"), encoding="utf-8").read()
    check("page: the project's name, no Harmonize, no placeholder", "Tide Clock board" in page and "armoniz" not in page and "{{" not in page)
    check(".gitignore appended, not replaced", open(os.path.join(t, ".gitignore")).read() == "build/\n.board/memory/before-compaction.md\n.kit/board_data/\n")
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
    check("one-word launcher installed in bin dir and named in orchestrator welcome",
          os.access(os.path.join(t, ".kit", "up"), os.X_OK)
          and os.access(os.path.join(d, "bin", "tide-clock"), os.X_OK)
          and "`tide-clock`" in orch and "--mode auto" in orch)
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

    # 1-command --bootstrap seeds _orchestrator and KIT-installed into local SQLite DB
    t_boot = os.path.join(d, "boot")
    os.makedirs(t_boot)
    rc, out = inst(t_boot, "--project", "QuickApp", "--no-survey", "bootstrap test", "--bootstrap")
    sys.path.insert(0, os.path.join(HERE, "kit", "board"))
    import board_server as _bs
    st = _bs.BoardStore(os.path.join(t_boot, ".kit", "board_data"))
    check("1-command --bootstrap seeds _orchestrator and KIT-installed into SQLite",
          rc == 0 and st.get_doc("posts", "_orchestrator")["exists"] and st.get_doc("posts", "KIT-installed")["exists"])
    st.close()

    # P1-5: fresh install with .board/memory is consistent across templates, kit_import, SessionStart, and kit_cards
    orch_b = open(os.path.join(t_boot, ".kit", "orchestrator.md"), encoding="utf-8").read()
    guide_b = open(os.path.join(t_boot, ".kit", "ORCHESTRATOR.md"), encoding="utf-8").read()
    check("fresh install renders .board/memory (not .story) into orchestrator templates",
          ".board/memory/state/" in orch_b and ".story" not in orch_b and ".board/memory/state/" in guide_b and ".story" not in guide_b)
    sf_b = os.path.join(d, "state_b.json")
    json.dump({"projects": [{"id": "alpha", "title": "Alpha", "next": "ship beta", "priority": 1}]}, open(sf_b, "w"))
    r_imp = subprocess.run([sys.executable, "-I", os.path.join(t_boot, ".kit", "kit_import.py"), sf_b, "--no-posts"],
                           capture_output=True, text=True)
    check("fresh install kit_import writes to .board/memory/state/alpha.md",
          r_imp.returncode == 0 and os.path.isfile(os.path.join(t_boot, ".board", "memory", "state", "alpha.md"))
          and not os.path.exists(os.path.join(t_boot, ".story")))
    r_ss_b = subprocess.run([sys.executable, "-I", os.path.join(t_boot, ".agents", "hooks", "kit", "kit_session_start.py")],
                            input=json.dumps({"hookEventName": "SessionStart", "cwd": t_boot}), capture_output=True, text=True)
    check("fresh install SessionStart reads .board/memory/state/alpha.md",
          r_ss_b.returncode == 0 and "alpha: ship beta" in r_ss_b.stdout)
    dash_b = os.path.join(d, "dash_b.json")
    r_kc = subprocess.run([sys.executable, "-I", os.path.join(t_boot, ".kit", "kit_cards.py"), "--write", dash_b,
                           "--trains", os.path.join(d, "trains_b.json")], capture_output=True, text=True)
    rows_b = json.load(open(dash_b))["rows"] if r_kc.returncode == 0 else []
    check("fresh install kit_cards reads .board/memory/state/alpha.md",
          r_kc.returncode == 0 and len(rows_b) == 1 and rows_b[0]["name"] == "Alpha")

    # Native Claude Code Web support under --runtime claude
    t_cl = os.path.join(d, "claude_web")
    os.makedirs(t_cl)
    rc, out = inst(t_cl, "--project", "WebProj", "--talk", "English", "--survey", rep,
                   "--runtime", "claude", "--board-url", "https://claude.ai/artifact/WEB1")
    cl_cfg = json.load(open(os.path.join(t_cl, ".kit", "config.json")))
    cl_orch = open(os.path.join(t_cl, ".kit", "orchestrator.md"), encoding="utf-8").read()
    cl_clerk = open(os.path.join(t_cl, ".kit", "clerk.md"), encoding="utf-8").read()
    cl_board = open(os.path.join(t_cl, ".kit", "BOARD.md"), encoding="utf-8").read()
    cl_cards = open(os.path.join(t_cl, ".claude", "agents", "kit-cards.md"), encoding="utf-8").read()
    check("--runtime claude installs .claude/ hooks, settings, agents, and no .agents/ or local board_server",
          rc == 0
          and os.path.isfile(os.path.join(t_cl, ".claude", "settings.json"))
          and os.path.isfile(os.path.join(t_cl, ".claude", "agents", "kit-cards.md"))
          and not os.path.exists(os.path.join(t_cl, ".agents"))
          and not os.path.exists(os.path.join(t_cl, ".kit", "board_server.py"))
          and cl_cfg["kit"]["roles"]["clerk"]["model"] == "haiku")
    check("--runtime claude renders native ArtifactData/ArtifactComments/create_session templates with .board/memory",
          "ArtifactData" in cl_board and "ArtifactComments" in cl_clerk
          and "create_session" in cl_orch and "archive_session" in cl_orch
          and "ArtifactData" in cl_cards and ".board/memory/state/" in cl_orch
          and "{{" not in cl_orch and "{{" not in cl_clerk and "{{" not in cl_board)
    r_cl_hooks = subprocess.run([sys.executable, "-I", os.path.join(t_cl, ".claude", "hooks", "kit", "test_hooks.py")],
                                capture_output=True, text=True)
    check("--runtime claude installed hooks pass self-test", r_cl_hooks.returncode == 0)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
