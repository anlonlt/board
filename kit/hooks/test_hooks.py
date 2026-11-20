#!/usr/bin/env python3
"""The kit's agy hooks on planted inputs, each refusal and each allowance seen. python3 -I kit/hooks/test_hooks.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def run(hook, data, proj):
    payload = dict(data)
    payload.setdefault("cwd", proj)
    payload.setdefault("workspacePaths", [proj])
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, hook)], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=30)
    try:
        parsed = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        parsed = {"_raw": r.stdout}
    return r.returncode, parsed, r.stderr


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def git(p, *a):
    subprocess.run(["git", "-C", p] + list(a), capture_output=True, check=True)


def injected_text(out):
    return "\n".join((s.get("systemMessage") or {}).get("systemMessage", "") for s in out.get("injectSteps", []))


with tempfile.TemporaryDirectory() as d:
    proj = os.path.join(d, "p")
    os.makedirs(os.path.join(proj, ".kit"))
    with open(os.path.join(proj, ".kit", "config.json"), "w") as fh:
        json.dump({"board_url": "http://127.0.0.1:8765", "language": {"talk": "French"},
                   "kit": {"guard": {"never_add": ["*.keystore"], "allow_add": ["dev-debug.keystore"]},
                           "limits": {"message_chars": 50, "done_message_chars": 30, "chat_answer_chars": 123},
                           "session_start_lines": ["BUILD: make test"]}}, fh)

    B = lambda c: {"hookEventName": "PreToolUse", "toolCall": {"name": "run_command", "args": {"CommandLine": c}}}
    for c in ["rm -rf src", "git clean -fd", "git reset --hard HEAD~1", "git push --force", "git push origin +main",
              "git push origin --delete feature", "git add release.keystore", "bash -c 'rm -r build'", "ls | xargs rm -rf"]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard refuses: " + c, rc == 0 and out.get("decision") == "deny")
    for c in ["rm -rf /tmp/x", "git commit -m 'never rm -rf src'", "git clean -n", "git push -u origin main",
              "git add dev-debug.keystore", "git reset --soft HEAD~1", "rm file.txt"]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard allows: " + c, rc == 0 and out.get("decision") == "allow")

    RUN = os.urandom(4).hex()
    S = lambda m: {"hookEventName": "PreToolUse", "toolCall": {"name": "send_message", "args": {"Message": m}}}
    check("say: a long message is refused", run("kit_say_it_once.py", S("x" * 51), proj)[1].get("decision") == "deny")
    check("say: a short message passes", run("kit_say_it_once.py", S("x" * 50), proj)[1].get("decision") == "allow")
    check("say: a long Done pointer is refused", run("kit_say_it_once.py", S("Done. " + "x" * 30), proj)[1].get("decision") == "deny")
    check("say: the clerk's verbatim forward is never cut",
          run("kit_say_it_once.py", S("Board comment from the owner " + "x" * 900), proj)[1].get("decision") == "allow")

    rc, out, _ = run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "p1-" + RUN, "lastUserInput": "hi"}, proj)
    check("say: the owner's prompt gets the limit from config", "123" in injected_text(out))
    rc, out, _ = run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "p1-" + RUN, "lastUserInput": "hi"}, proj)
    check("say: same prompt does not spam reminder twice", injected_text(out) == "")

    cid_post = "post-" + RUN
    rc, out, _ = run("kit_say_it_once.py", {
        "hookEventName": "PostToolUse", "conversationId": cid_post, "stepIdx": 3,
        "toolCall": {"name": "run_command", "args": {"CommandLine": "python3 -I .kit/board_cli.py set posts P1 --data '{}'"}}
    }, proj)
    check("say: PostToolUse returns empty JSON {}", rc == 0 and out == {})
    rc, out, _ = run("kit_say_it_once.py", {"hookEventName": "PostInvocation", "conversationId": cid_post}, proj)
    check("say: after a board write, PostInvocation reminds to end with ·", "·" in injected_text(out))

    rc, out, _ = run("kit_board_typing.py", {
        "hookEventName": "PreInvocation", "conversationId": "t1-" + RUN,
        "lastUserInput": "Board comment from the owner on your post P1-x, thread t, 10:00, verbatim: \"a\""
    }, proj)
    check("typing: names the post", "typing/P1-x" in injected_text(out))
    check("typing: silent otherwise", injected_text(run("kit_board_typing.py", {
        "hookEventName": "PreInvocation", "conversationId": "t2-" + RUN, "lastUserInput": "hello"
    }, proj)[1]) == "")

    os.makedirs(os.path.join(proj, ".story", "state"))
    with open(os.path.join(proj, ".story", "state", "app.md"), "w") as fh:
        fh.write("# app\nNext: ship the login screen\n")
    with open(os.path.join(proj, ".story", "awaits.md"), "w") as fh:
        fh.write("# Awaits\n- choose a colour (default blue)\n- a licence\n")
    with open(os.path.join(proj, ".story", "preferences.md"), "w") as fh:
        fh.write("# Standing preferences\n\n- ask before any real action\n")
    rc, out, _ = run("kit_session_start.py", {"hookEventName": "SessionStart"}, proj)
    start_txt = injected_text(out)
    check("session start: board, language, project line, next step, awaits",
          all(x in start_txt for x in ("127.0.0.1:8765", "French", "BUILD: make test", "app: ship the login screen", "2 points", "ask before any real action")))

    git(proj, "init", "-q"); git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
    with open(os.path.join(proj, "f.txt"), "w") as fh:
        fh.write("1")
    git(proj, "add", "f.txt"); git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "b")
    with open(os.path.join(proj, "f.txt"), "w") as fh:
        fh.write("2")
    stop_cid = "stop-" + RUN
    check("stop: blocks once on uncommitted work",
          run("kit_stop.py", {"hookEventName": "Stop", "conversationId": stop_cid, "terminationReason": "NO_TOOL_CALL", "lastUserInput": "t1"}, proj)[1].get("decision") == "continue")
    check("stop: lets the second stop through",
          run("kit_stop.py", {"hookEventName": "Stop", "conversationId": stop_cid, "terminationReason": "NO_TOOL_CALL", "lastUserInput": "t1"}, proj)[1].get("decision") == "allow")
    check("stop: never blocks on USER_CANCELED",
          run("kit_stop.py", {"hookEventName": "Stop", "conversationId": "cancel-" + RUN, "terminationReason": "USER_CANCELED", "lastUserInput": "t2"}, proj)[1].get("decision") == "allow")

    rc, out, _ = run("kit_pre_compact.py", {"hookEventName": "Stop"}, proj)
    check("pre-compact (on Stop): writes .story/before-compaction.md and allows stop",
          out.get("decision") == "allow" and os.path.exists(os.path.join(proj, ".story", "before-compaction.md")))

    # Stop: closing message check using agy's transcript.jsonl format
    def transcript(step_id, closing, human=True):
        f = os.path.join(d, "t-%s.jsonl" % step_id)
        with open(f, "w") as fh:
            fh.write(json.dumps({"step_index": step_id, "source": "USER_EXPLICIT" if human else "SYSTEM", "type": "USER_INPUT", "content": "go"}) + "\n")
            fh.write(json.dumps({"step_index": step_id + 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "", "tool_calls": [{"name": "run_command"}]}) + "\n")
            fh.write(json.dumps({"step_index": step_id + 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": closing}) + "\n")
        return f

    St = lambda f, sid: {"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "transcriptPath": f, "conversationId": sid + RUN}
    f1 = transcript(10, "x" * 200)
    rc1, out1, _ = run("kit_say_it_once.py", St(f1, "s1"), proj)
    check("stop: a long answer to the owner is blocked, naming the language", out1.get("decision") == "continue" and "French" in out1.get("reason", ""))
    check("stop: blocked once per turn, then it passes", run("kit_say_it_once.py", St(f1, "s1"), proj)[1].get("decision") == "allow")
    check("stop: a short answer to the owner passes", run("kit_say_it_once.py", St(transcript(20, "oui"), "s2"), proj)[1].get("decision") == "allow")
    f3 = transcript(30, "I've taken over as orchestrator and nothing is running yet.", human=False)
    check("stop: a turn a session or timer opened must end with ·", run("kit_say_it_once.py", St(f3, "s3"), proj)[1].get("decision") == "continue")
    check("stop: · passes", run("kit_say_it_once.py", St(transcript(40, "·", human=False), "s4"), proj)[1].get("decision") == "allow")

    # Setup turns: while kit.setup is true, stop hooks and reminders stay quiet; guard still refuses.
    cfgp = os.path.join(proj, ".kit", "config.json")
    c = json.load(open(cfgp)); c["kit"]["setup"] = True; json.dump(c, open(cfgp, "w"))
    check("setup: stop does not block on uncommitted work",
          run("kit_stop.py", {"hookEventName": "Stop", "conversationId": "setup-" + RUN, "lastUserInput": "t3"}, proj)[1].get("decision") == "allow")
    f5 = transcript(50, "x" * 900)
    check("setup: a long closing message is not blocked", run("kit_say_it_once.py", St(f5, "s5"), proj)[1].get("decision") == "allow")
    check("setup: no reminder after a post",
          injected_text(run("kit_say_it_once.py", {"hookEventName": "PostInvocation", "conversationId": "setup-p-" + RUN}, proj)[1]) == "")
    check("setup: no reminder on the owner's prompt",
          injected_text(run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "setup-u-" + RUN, "lastUserInput": "hi"}, proj)[1]) == "")
    check("setup: the guard still refuses", run("kit_guard.py", B("git reset --hard"), proj)[1].get("decision") == "deny")
    rc, out, _ = run("kit_session_start.py", {"hookEventName": "SessionStart"}, proj)
    check("setup: the start says the install is in progress", "SETUP" in injected_text(out))
    c["kit"]["setup"] = False; json.dump(c, open(cfgp, "w"))
    check("after --setup-done: stop blocks again",
          run("kit_stop.py", {"hookEventName": "Stop", "conversationId": "done-" + RUN, "lastUserInput": "t4"}, proj)[1].get("decision") == "continue")

print("%d failed" % fails)
sys.exit(1 if fails else 0)
