#!/usr/bin/env python3
"""Issue #3's install items, each on a planted project (BD11, 2026-11-20). python3 -I test_install_bd11.py
  1. step 0 first: a first install without the survey's report is refused (or `--no-survey "<why>"`, recorded)
  3. the trunk: detected from origin's HEAD; a branch missing on origin fails loudly
  4. translation: `.kit/strings.json` is applied to the page at install; an update keeps it, never KEPT
  7. a partial install: --no-board installs the memory and its hooks only
"""
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


def git(p, *a):
    return subprocess.run(["git", "-C", p] + list(a), capture_output=True, text=True)


def survey(d):
    f = os.path.join(d, "report.md")
    open(f, "w").write("# Step 0 report\n## Recommendation\n- memory: install\n- board: later\n")
    return f


def hooks_of(t):
    s = json.load(open(os.path.join(t, ".agents", "hooks.json")))
    kb = s.get("kit-board", {})
    cmds = []
    for ev in ("SessionStart", "PreInvocation", "PostInvocation", "Stop"):
        cmds += [h["command"] for h in kb.get(ev, [])]
    for ev in ("PreToolUse", "PostToolUse"):
        cmds += [h["command"] for g in kb.get(ev, []) for h in g.get("hooks", [])]
    return cmds


with tempfile.TemporaryDirectory() as d:
    os.environ["KIT_BIN_DIR"] = os.path.join(d, "bin")
    rep = survey(d)
    # 1. step 0 first
    t = os.path.join(d, "a"); os.makedirs(t)
    rc, out = inst(t, "--project", "A")
    check("1: a first install without the survey's report is refused, naming step 0",
          rc != 0 and "step 0" in out.lower() and not os.path.exists(os.path.join(t, ".kit")))
    rc, out = inst(t, "--project", "A", "--survey", os.path.join(d, "nope.md"))
    check("1: nothing written by a refused install", not os.path.exists(os.path.join(t, ".kit")))
    check("1: a survey path that does not exist is refused", rc != 0)
    rc, out = inst(t, "--project", "A", "--survey", rep)
    cfg = json.load(open(os.path.join(t, ".kit", "config.json")))
    check("1: with the report it installs and records it", rc == 0 and cfg["kit"]["survey"].endswith("report.md"))
    check("5: a first install starts in setup (the stop hooks quiet)", cfg["kit"]["setup"] is True)
    rc, out = inst(t)
    check("1: an update (config present) needs no survey", rc == 0)
    rc, out = inst(t, "--setup-done")
    check("5: --setup-done ends it", rc == 0 and json.load(open(os.path.join(t, ".kit", "config.json")))["kit"]["setup"] is False)
    t2 = os.path.join(d, "a2"); os.makedirs(t2)
    rc, out = inst(t2, "--project", "A2", "--no-survey", "the owner said skip it")
    cfg = json.load(open(os.path.join(t2, ".kit", "config.json")))
    check("1: --no-survey installs and records why", rc == 0 and "skip" in cfg["kit"]["survey"])

    # 7. partial install
    t = os.path.join(d, "mem"); os.makedirs(t)
    rc, out = inst(t, "--project", "Mem", "--survey", rep, "--no-board")
    kit = os.path.join(t, ".kit")
    cmds = hooks_of(t)
    check("7: --no-board: memory, end_session and the memory's hooks are installed",
          rc == 0 and os.path.isdir(os.path.join(t, ".story", "state")) and os.path.exists(os.path.join(kit, "end_session.py"))
          and any("kit_session_start" in c for c in cmds) and any("kit_stop" in c for c in cmds) and any("kit_guard" in c for c in cmds))
    check("7: --no-board: no page, no clerk, no orchestrator, no board hooks",
          not any(os.path.exists(os.path.join(kit, f)) for f in ("board.html", "clerk.md", "orchestrator.md", "BOARD.md"))
          and not any("say_it_once" in c or "board_typing" in c for c in cmds))
    cfg = json.load(open(os.path.join(kit, "config.json")))
    check("7: the parts are recorded", cfg["kit"]["parts"] == {"memory": True, "board": False, "clerk": False, "orchestrator": False})
    check("7: the next step names the memory, not publishing a board", "publish" not in out.lower() and "memory" in out.lower())
    rc, out = inst(t)
    check("7: a rerun keeps the choice", rc == 0 and not os.path.exists(os.path.join(kit, "board.html")))
    rc, out = inst(t, "--parts", "memory,board")
    check("7: the board added later, without clerk or orchestrator",
          rc == 0 and os.path.exists(os.path.join(kit, "board.html")) and not os.path.exists(os.path.join(kit, "clerk.md")))
    t = os.path.join(d, "noc"); os.makedirs(t)
    rc, out = inst(t, "--project", "N", "--survey", rep, "--no-clerk", "--no-orchestrator")
    check("7: board without clerk and orchestrator", rc == 0 and os.path.exists(os.path.join(t, ".kit", "board.html"))
          and not os.path.exists(os.path.join(t, ".kit", "clerk.md")) and not os.path.exists(os.path.join(t, ".kit", "orchestrator.md")))
    rc, out = inst(os.path.join(d, "a"), "--parts", "board")
    check("7: the memory cannot be left out", rc != 0)

    # 3. trunk
    bare = os.path.join(d, "origin.git")
    git(d, "init", "-q", "--bare", bare)
    git(bare, "symbolic-ref", "HEAD", "refs/heads/claude/trunk")
    t = os.path.join(d, "tr")
    git(d, "clone", "-q", bare, t)
    git(t, "checkout", "-q", "-b", "claude/trunk")
    git(t, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    git(t, "push", "-q", "origin", "claude/trunk")
    rc, out = inst(t, "--project", "T", "--survey", rep)
    cfg = json.load(open(os.path.join(t, ".kit", "config.json")))
    check("3: the trunk is origin's HEAD, not 'main'", rc == 0 and cfg["kit"]["main_branch"] == "claude/trunk")
    t3 = os.path.join(d, "tr3")
    git(d, "clone", "-q", bare, t3)
    rc, out = inst(t3, "--project", "T", "--survey", rep, "--main-branch", "main")
    check("3: a branch missing on origin fails loudly", rc != 0 and "main" in out and "origin" in out
          and not os.path.exists(os.path.join(t3, ".kit", "config.json")))

    # 4. translation by a string table
    t = os.path.join(d, "fr"); os.makedirs(os.path.join(t, ".kit"))
    json.dump({"language": "French", "strings": {"Waiting for you": "Ce qui vous attend", "Move to Done": "Classer",
                                                 "Write a comment": "Écrire un \"commentaire\"", "Nothing waits on you.": "Rien ne vous attend."}},
              open(os.path.join(t, ".kit", "strings.json"), "w"), ensure_ascii=False)
    rc, out = inst(t, "--project", "Fr", "--talk", "French", "--survey", rep)
    page = open(os.path.join(t, ".kit", "board.html"), encoding="utf-8").read()
    check("4: the page carries the translation (HTML, attribute, script)",
          ">Ce qui vous attend<" in page and 'placeholder="Écrire un &quot;commentaire&quot;"' in page and '"Rien ne vous attend."' in page)
    check("4: what goes to Claude stays English", '"Answer: "' in page)
    check("4: the install says how many strings are left", rc == 0 and "strings" in out and "untranslated" in out)
    rc, out = inst(t)
    check("4: a rerun keeps the translated page, nothing KEPT", rc == 0 and "KEPT" not in out and "nothing to change" in out)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
