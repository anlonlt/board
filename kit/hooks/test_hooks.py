#!/usr/bin/env python3
"""The kit's hooks on planted inputs, each refusal and each allowance seen. python3 -I kit/hooks/test_hooks.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def run(hook, data, proj):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, hook)], input=json.dumps(data), capture_output=True,
                       text=True, env=env, timeout=30)
    return r.returncode, r.stdout, r.stderr


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def git(p, *a):
    subprocess.run(["git", "-C", p] + list(a), capture_output=True, check=True)


with tempfile.TemporaryDirectory() as d:
    proj = os.path.join(d, "p")
    os.makedirs(os.path.join(proj, ".kit"))
    with open(os.path.join(proj, ".kit", "config.json"), "w") as fh:
        json.dump({"board_url": "https://claude.ai/artifact/TEST", "language": {"talk": "French"},
                   "kit": {"guard": {"never_add": ["*.keystore"], "allow_add": ["dev-debug.keystore"]},
                           "limits": {"message_chars": 50, "done_message_chars": 30, "chat_answer_chars": 123},
                           "session_start_lines": ["BUILD: make test"]}}, fh)
    B = lambda c: {"tool_input": {"command": c}}
    for c in ["rm -rf src", "git clean -fd", "git reset --hard HEAD~1", "git push --force", "git push origin +main",
              "git push origin --delete feature", "git add release.keystore", "bash -c 'rm -r build'", "ls | xargs rm -rf"]:
        check("guard refuses: " + c, run("kit_guard.py", B(c), proj)[0] == 2)
    for c in ["rm -rf /tmp/x", "git commit -m 'never rm -rf src'", "git clean -n", "git push -u origin main",
              "git add dev-debug.keystore", "git reset --soft HEAD~1", "rm file.txt"]:
        check("guard allows: " + c, run("kit_guard.py", B(c), proj)[0] == 0)
    S = lambda m: {"hook_event_name": "PreToolUse", "tool_input": {"message": m}}
    check("say: a long message is refused", run("kit_say_it_once.py", S("x" * 51), proj)[0] == 2)
    check("say: a short message passes", run("kit_say_it_once.py", S("x" * 50), proj)[0] == 0)
    check("say: a long Done pointer is refused", run("kit_say_it_once.py", S("Done. " + "x" * 30), proj)[0] == 2)
    check("say: the clerk's verbatim forward is never cut",
          run("kit_say_it_once.py", S("Board comment from the owner " + "x" * 900), proj)[0] == 0)
    rc, out, _ = run("kit_say_it_once.py", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}, proj)
    check("say: the owner's prompt gets the limit from config", "123" in out)
    rc, out, _ = run("kit_say_it_once.py", {"hook_event_name": "PostToolUse"}, proj)
    check("say: after a post, the turn ends with ·", "·" in out)
    rc, out, _ = run("kit_board_typing.py", {"prompt": "Board comment from the owner on your post P1-x, thread t, 10:00, verbatim: \"a\""}, proj)
    check("typing: names the post", "typing/P1-x" in out)
    check("typing: silent otherwise", run("kit_board_typing.py", {"prompt": "hello"}, proj)[1] == "")
    os.makedirs(os.path.join(proj, ".story", "state"))
    with open(os.path.join(proj, ".story", "state", "app.md"), "w") as fh:
        fh.write("# app\nNext: ship the login screen\n")
    with open(os.path.join(proj, ".story", "awaits.md"), "w") as fh:
        fh.write("# Awaits\n- choose a colour (default blue)\n- a licence\n")
    with open(os.path.join(proj, ".story", "preferences.md"), "w") as fh:
        fh.write("# Standing preferences\n\n- ask before any real action\n")
    rc, out, _ = run("kit_session_start.py", {}, proj)
    check("session start: board, language, project line, next step, awaits",
          all(x in out for x in ("artifact/TEST", "French", "BUILD: make test", "app: ship the login screen", "2 points", "ask before any real action")))
    git(proj, "init", "-q"); git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    with open(os.path.join(proj, "f.txt"), "w") as fh:
        fh.write("1")
    git(proj, "add", "f.txt"); git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "b")
    with open(os.path.join(proj, "f.txt"), "w") as fh:
        fh.write("2")
    check("stop: blocks once on uncommitted work", run("kit_stop.py", {}, proj)[0] == 2)
    check("stop: lets the second stop through", run("kit_stop.py", {"stop_hook_active": True}, proj)[0] == 0)
    run("kit_pre_compact.py", {}, proj)
    check("pre-compact: writes .story/before-compaction.md", os.path.exists(os.path.join(proj, ".story", "before-compaction.md")))

    # Stop: the closing message is checked (the owner, 2026-11-20: a friend's orchestrator closed with a long English recap)
    def transcript(opener, closing, human=True):
        f = os.path.join(d, "t-%d.jsonl" % abs(hash(opener + closing)))
        with open(f, "w") as fh:
            fh.write(json.dumps({"type": "user", "uuid": opener, "origin": {"kind": "human" if human else "task"},
                                 "message": {"content": "go"}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": closing}]}}) + "\n")
        return f
    RUN = os.urandom(4).hex()  # the once-per-turn mark lives in /tmp: a fresh session id per run
    St = lambda f, sid: {"hook_event_name": "Stop", "transcript_path": f, "session_id": sid + RUN}
    f1 = transcript("u1", "x" * 200)
    rc1, _, err1 = run("kit_say_it_once.py", St(f1, "s1"), proj)
    check("stop: a long answer to the owner is blocked, naming the language", rc1 == 2 and "French" in err1)
    check("stop: blocked once per turn, then it passes", run("kit_say_it_once.py", St(f1, "s1"), proj)[0] == 0)
    check("stop: a short answer to the owner passes", run("kit_say_it_once.py", St(transcript("u2", "oui"), "s2"), proj)[0] == 0)
    f3 = transcript("u3", "I've taken over as orchestrator and nothing is running yet.", human=False)
    check("stop: a turn a session or timer opened must end with ·", run("kit_say_it_once.py", St(f3, "s3"), proj)[0] == 2)
    check("stop: · passes", run("kit_say_it_once.py", St(transcript("u4", "·", human=False), "s4"), proj)[0] == 0)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
