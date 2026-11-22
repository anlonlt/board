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
              "git push origin --delete feature", "git add release.keystore", "bash -c 'rm -r build'", "ls | xargs rm -rf",
              "git branch -D feature", "find src -name '*.py' -delete", "git --git-dir .git reset --hard",
              "rm -rf /tmp/../home/user/project", "find . -exec rm -rf {} \\;", "find . -execdir rm {} +",
              "find . -ok sh -c 'rm -rf {}' \\;",
              "python3 -c \"import shutil; shutil.rmtree('src')\"",
              "node -e \"fs.rmSync('src', {recursive: true})\""]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard refuses: " + c, rc == 0 and out.get("decision") == "deny")
    for c in ["rm -rf /tmp/x", "git commit -m 'never rm -rf src'", "git clean -n", "git push -u origin main",
              "git add dev-debug.keystore", "git reset --soft HEAD~1", "rm file.txt",
              "git branch -d merged-feature", "find /tmp/scratch -delete", "find /tmp/scratch -exec rm -rf {} \\;",
              "python3 -c \"import shutil; shutil.rmtree('/tmp/scratch')\""]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard allows: " + c, rc == 0 and out.get("decision") == "allow")

    # P1-3: broad git add (. / -A / --all / dir) refused when a never_add secret file is present
    os.makedirs(os.path.join(proj, "sub"), exist_ok=True)
    open(os.path.join(proj, "release.keystore"), "w").write("secret")
    open(os.path.join(proj, "sub", "app.keystore"), "w").write("secret")
    open(os.path.join(proj, "dev-debug.keystore"), "w").write("ok")
    for c in ["git add .", "git add -A", "git add --all", "git add sub/"]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard refuses broad add with secret present: " + c, rc == 0 and out.get("decision") == "deny")
    os.remove(os.path.join(proj, "release.keystore"))
    os.remove(os.path.join(proj, "sub", "app.keystore"))
    for c in ["git add .", "git add -A"]:
        rc, out, _ = run("kit_guard.py", B(c), proj)
        check("guard allows broad add when only allowed files exist: " + c, rc == 0 and out.get("decision") == "allow")
    os.remove(os.path.join(proj, "dev-debug.keystore"))

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

    # Context-threshold handover trigger in kit_pre_compact.py
    def ctx_transcript(name, opener_text, in_tok, cache_tok, tool_cmd=None):
        f = os.path.join(d, "ctx-%s.jsonl" % name)
        with open(f, "w") as fh:
            fh.write(json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": opener_text}) + "\n")
            ev = {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
                  "input_tokens": in_tok, "cache_read_tokens": cache_tok, "content": "ok"}
            if tool_cmd:
                ev["tool_calls"] = [{"name": "run_command", "args": {"CommandLine": tool_cmd}}]
            fh.write(json.dumps(ev) + "\n")
        return f

    # Below threshold (orchestrator at 100k < 300k default) -> allow
    tf_low = ctx_transcript("orch-low", "MyProj: you are the orchestrator.", 60000, 40000)
    rc, out, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": "orch-low-" + RUN, "transcript_path": tf_low}, proj)
    check("pre-compact: orchestrator below threshold is allowed", rc == 0 and out.get("decision") == "allow")

    # Normal part above threshold (350k) -> allow
    tf_part = ctx_transcript("part-high", "Your prompt is docs/prompts/p1.md: do it.", 200000, 150000)
    rc, out, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": "part-high-" + RUN, "transcript_path": tf_part}, proj)
    check("pre-compact: normal part above threshold is allowed", rc == 0 and out.get("decision") == "allow")

    # Orchestrator above threshold (390k >= 380k) -> deny once with handover instructions, then allow
    tf_orch = ctx_transcript("orch-high", "MyProj: you are the orchestrator.", 190000, 200000)
    orch_cid = "orch-high-" + RUN
    rc1, out1, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": orch_cid, "transcript_path": tf_orch}, proj)
    check("pre-compact: orchestrator above threshold is denied with handover & invoke_subagent instructions",
          rc1 == 0 and out1.get("decision") == "deny"
          and "HANDOVER REQUIRED" in out1.get("reason", "")
          and "390k >= 380k" in out1.get("reason", "")
          and "invoke_subagent" in out1.get("reason", "")
          and "state/orchestrator.md" in out1.get("reason", ""))
    rc2, out2, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": orch_cid, "transcript_path": tf_orch}, proj)
    check("pre-compact: orchestrator second stop is allowed (flag prevents infinite loop)",
          rc2 == 0 and out2.get("decision") == "allow")

    # Clerk above threshold (90k >= 80k) -> deny once with rotation instructions, then allow
    tf_clerk = ctx_transcript("clerk-high", "MyProj: you are the board clerk.", 10000, 80000,
                              tool_cmd="python3 -I .kit/board_cli.py comments wait --timeout 300")
    clerk_cid = "clerk-high-" + RUN
    rc1, out1, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": clerk_cid, "transcript_path": tf_clerk}, proj)
    check("pre-compact: clerk above threshold is denied with rotation instructions",
          rc1 == 0 and out1.get("decision") == "deny"
          and "HANDOVER REQUIRED" in out1.get("reason", "")
          and "90k >= 80k" in out1.get("reason", "")
          and "ready for replacement" in out1.get("reason", ""))
    rc2, out2, _ = run("kit_pre_compact.py", {"hookEventName": "Stop", "conversation_id": clerk_cid, "transcript_path": tf_clerk}, proj)
    check("pre-compact: clerk second stop is allowed (flag prevents infinite loop)",
          rc2 == 0 and out2.get("decision") == "allow")

    # Claude Code CLI: the same limits, read from the transcript's usage; a context RESET via .kit/handover.py
    def cc_transcript(name, opener_text, in_tok, cache_tok, creation_tok=0):
        f = os.path.join(d, "cc-%s.jsonl" % name)
        with open(f, "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": opener_text}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                     "usage": {"input_tokens": in_tok, "cache_read_input_tokens": cache_tok,
                               "cache_creation_input_tokens": creation_tok}}}) + "\n")
        return f

    def cc_stop(sid, tf, active=False):
        return run("kit_pre_compact.py", {"hook_event_name": "Stop", "session_id": sid, "transcript_path": tf,
                                          "stop_hook_active": active}, proj)

    cc_low = cc_transcript("orch-low", "Read .kit/claude-cli/orchestrator.md and start as the orchestrator for this repository", 5000, 90000)
    check("claude-cli: orchestrator below its limit stops normally", cc_stop("cc-low-" + RUN, cc_low)[1] == {})
    cc_part = cc_transcript("part", "Your prompt is docs/prompts/p1.md: read it and do it.", 50000, 300000)
    check("claude-cli: a part above the limit is never handed over", cc_stop("cc-part-" + RUN, cc_part)[1] == {})
    cc_orch = cc_transcript("orch-high", "Read .kit/claude-cli/orchestrator.md and start as the orchestrator for this repository", 2000, 370000, 12000)
    o1 = cc_stop("cc-orch-" + RUN, cc_orch)[1]
    check("claude-cli: orchestrator at 384k is blocked once with the reset steps (trains current, kit.py resume, handover.py)",
          o1.get("decision") == "block" and "384k >= 380k" in o1.get("reason", "") and "kit.py resume" in o1.get("reason", "")
          and "state/orchestrator.md" in o1.get("reason", "") and "handover.py --role orchestrator" in o1.get("reason", ""))
    check("claude-cli: orchestrator's next stop passes (flag)", cc_stop("cc-orch-" + RUN, cc_orch)[1] == {})
    check("claude-cli: stop_hook_active never blocks", cc_stop("cc-orch2-" + RUN, cc_orch, active=True)[1] == {})
    cc_clerk = cc_transcript("clerk-high", "Your prompt is .kit/claude-cli/clerk.md: read it and follow it.", 1000, 85000)
    o1 = cc_stop("cc-clerk-" + RUN, cc_clerk)[1]
    check("claude-cli: clerk at 86k is blocked once: no re-arm, handover.py --role clerk",
          o1.get("decision") == "block" and "86k >= 80k" in o1.get("reason", "")
          and "handover.py --role clerk" in o1.get("reason", "") and "re-arm" in o1.get("reason", ""))

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

    # chat_verbose mode allows long human chat answers and skips PreInvocation length reminder
    cfgp_v = os.path.join(proj, ".kit", "config.json")
    cv = json.load(open(cfgp_v)); cv["kit"]["limits"]["chat_verbose"] = True; json.dump(cv, open(cfgp_v, "w"))
    check("say: chat_verbose allows long answer to human in chat",
          run("kit_say_it_once.py", St(transcript(45, "x" * 500, human=True), "s4v"), proj)[1].get("decision") == "allow")
    check("say: chat_verbose skips PreInvocation length reminder",
          injected_text(run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "pv-" + RUN, "lastUserInput": "verbose hi"}, proj)[1]) == "")
    cv["kit"]["limits"]["chat_verbose"] = False; json.dump(cv, open(cfgp_v, "w"))

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

    # P1-4: kit_stop detects unpushed local commits on a branch with no upstream @{u}
    bare = os.path.join(d, "bare.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    git(proj, "remote", "add", "origin", bare)
    git(proj, "add", "f.txt")
    git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
    rc_nu, out_nu, _ = run("kit_stop.py", {"hookEventName": "Stop", "conversationId": "noup-" + RUN, "terminationReason": "NO_TOOL_CALL", "lastUserInput": "t5"}, proj)
    check("stop: blocks on unpushed commits when branch has no upstream",
          out_nu.get("decision") == "continue" and "no upstream" in out_nu.get("reason", ""))
    git(proj, "push", "-q", "-u", "origin", "HEAD")
    rc_pu, out_pu, _ = run("kit_stop.py", {"hookEventName": "Stop", "conversationId": "noup2-" + RUN, "terminationReason": "NO_TOOL_CALL", "lastUserInput": "t6"}, proj)
    check("stop: allows after pushing branch with upstream", out_pu.get("decision") == "allow")

    # Claude Code Web hook contract compatibility (exit 2 + stderr on refusal, hookSpecificOutput, plain stdout SessionStart)
    def run_claude(hook, data):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, hook)], input=json.dumps(data),
                           capture_output=True, text=True, env=env, timeout=30)
        return r.returncode, r.stdout, r.stderr

    rc_cg, _, err_cg = run_claude("kit_guard.py", {"tool_input": {"command": "rm -rf src"}})
    check("claude hook: guard refuses with exit 2 and stderr", rc_cg == 2 and "REFUSED" in err_cg)
    rc_cg_ok, out_cg_ok, _ = run_claude("kit_guard.py", {"tool_input": {"command": "rm -rf /tmp/x"}})
    check("claude hook: guard allows with exit 0", rc_cg_ok == 0 and out_cg_ok.strip() == "")
    rc_cs, out_cs, _ = run_claude("kit_session_start.py", {"hook_event_name": "SessionStart"})
    check("claude hook: SessionStart prints plain text stdout",
          rc_cs == 0 and "app: ship the login screen" in out_cs and not out_cs.lstrip().startswith("{"))
    rc_ct, out_ct, _ = run_claude("kit_board_typing.py", {"hook_event_name": "UserPromptSubmit", "prompt": "Board comment from the owner on your post P1-x, thread t, 10:00, verbatim: \"a\""})
    check("claude hook: board_typing emits hookSpecificOutput with ArtifactData",
          rc_ct == 0 and "hookSpecificOutput" in out_ct and "ArtifactData" in out_ct)
    rc_csay, _, err_csay = run_claude("kit_say_it_once.py", {"hook_event_name": "PreToolUse", "tool_input": {"message": "x" * 51}})
    check("claude hook: say_it_once PreToolUse refuses long message with exit 2", rc_csay == 2 and "REFUSED" in err_csay)
    rc_cup, out_cup, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    check("claude hook: say_it_once UserPromptSubmit emits hookSpecificOutput", rc_cup == 0 and "hookSpecificOutput" in out_cup and "123" in out_cup)
    rc_cpt, out_cpt, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse"})
    check("claude hook: say_it_once PostToolUse emits hookSpecificOutput with ·", rc_cpt == 0 and "hookSpecificOutput" in out_cpt and "·" in out_cpt)
    _, out_cb, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}})
    _, out_cbw, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                                      "tool_input": {"command": "python3 -I .kit/board_cli.py set comments c1 --data '{}'"}})
    check("claude CLI hook: say_it_once PostToolUse on Bash stays silent unless it is a board write",
          out_cb.strip() == "" and "·" in out_cbw)
    cf_t = os.path.join(d, "claude-t.jsonl")
    with open(cf_t, "w") as fh:
        fh.write(json.dumps({"type": "user", "uuid": "cu1", "origin": {"kind": "human"}, "message": {"content": "go"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 200}]}}) + "\n")
    rc_cst, _, err_cst = run_claude("kit_say_it_once.py", {"hook_event_name": "Stop", "transcript_path": cf_t, "session_id": "cs1-" + RUN})
    check("claude hook: say_it_once Stop parses Claude transcript and blocks with exit 2", rc_cst == 2 and "French" in err_cst)
    rc_cst2, _, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "Stop", "transcript_path": cf_t, "session_id": "cs1-" + RUN})
    check("claude hook: say_it_once Stop passes on second try", rc_cst2 == 0)
    with open(os.path.join(proj, "f.txt"), "w") as fh:
        fh.write("dirty-claude")
    rc_cstop1, _, err_cstop1 = run_claude("kit_stop.py", {"hook_event_name": "Stop"})
    rc_cstop2, _, _ = run_claude("kit_stop.py", {"hook_event_name": "Stop", "stop_hook_active": True})
    check("claude hook: kit_stop blocks with exit 2 on dirty tree and honors stop_hook_active",
          rc_cstop1 == 2 and "Before stopping" in err_cstop1 and rc_cstop2 == 0)

    # kit_cards_reminder: only in the orchestrator's session, on Done / P1 / multi-step train / launch, once per burst
    import tempfile as _tf
    _mk = os.path.join(_tf.gettempdir(), "kit-orchestrator-session"); _last = os.path.join(_tf.gettempdir(), "kit-cards-reminder-last")
    for _f in (_mk, _last):
        try: os.remove(_f)
        except OSError: pass
    _done = {"hook_event_name": "PostToolUse", "tool_name": "ArtifactData",
             "tool_input": {"action": "update", "collection": "posts", "doc_id": "P-x", "data": {"column": "done"}}}
    _, o0, _ = run_claude("kit_cards_reminder.py", _done)
    check("cards reminder: silent outside the orchestrator", o0.strip() == "{}")
    open(_mk, "w").write("sess-orch")
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-orch"
    _, o1, _ = run_claude("kit_cards_reminder.py", _done)
    check("cards reminder: a post moved to Done reminds kit-cards", "kit-cards" in o1 and "moved to Done" in o1)
    _, o2, _ = run_claude("kit_cards_reminder.py", _done)
    check("cards reminder: once per burst", o2.strip() == "{}")
    os.remove(_last)
    _train = {"hook_event_name": "PostToolUse", "tool_name": "ArtifactData", "tool_input": {"action": "batch", "writes": [
        {"op": "set", "collection": "posts", "doc_id": "a", "data": {"train": "t"}},
        {"op": "set", "collection": "posts", "doc_id": "b", "data": {"train": "t", "parent": "a"}}]}}
    _, o3, _ = run_claude("kit_cards_reminder.py", _train)
    check("cards reminder: a multi-step train planned reminds", "several steps" in o3)
    os.remove(_last)
    _, o4, _ = run_claude("kit_cards_reminder.py", {"hook_event_name": "UserPromptSubmit", "prompt": "Done. Check post S-1."})
    check("cards reminder: a part's Done message reminds", "S-1" in o4)
    os.remove(_last)
    _, o5, _ = run_claude("kit_cards_reminder.py", {"hook_event_name": "PostToolUse", "tool_name": "ArtifactData",
        "tool_input": {"action": "set", "collection": "comments", "doc_id": "c", "data": {"column": "done"}}})
    check("cards reminder: comments and other writes stay silent", o5.strip() == "{}")
    for _f in (_mk, _last):
        try: os.remove(_f)
        except OSError: pass
    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    # kit_notify: an Artifact board's ntfy pushes, from the session that writes
    import http.server as _hs, threading as _th
    _got = []
    class _H(_hs.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            _got.append((self.headers.get("Title", ""), self.rfile.read(n).decode("utf-8"), self.headers.get("Click", "")))
            self.send_response(200); self.end_headers()
        def log_message(self, *a): pass
    _srv = _hs.HTTPServer(("127.0.0.1", 0), _H); _th.Thread(target=_srv.serve_forever, daemon=True).start()
    _cfgp = os.path.join(proj, ".kit", "config.json"); os.makedirs(os.path.dirname(_cfgp), exist_ok=True)
    _old = open(_cfgp).read() if os.path.exists(_cfgp) else None
    _c = json.loads(_old) if _old else {}
    _c.setdefault("kit", {})["notify_url"] = "http://127.0.0.1:%d/topic" % _srv.server_port
    _c["board_url"] = "https://claude.ai/artifact/X"; _c["project"] = "Demo"
    open(_cfgp, "w").write(json.dumps(_c))
    run_claude("kit_notify.py", {"hook_event_name": "PostToolUse", "tool_name": "ArtifactData", "tool_input": {"action": "batch", "writes": [
        {"op": "set", "collection": "posts", "doc_id": "P-1", "data": {"column": "question", "title": "Secret title", "body": "school data"}},
        {"op": "set", "collection": "posts", "doc_id": "P-2", "data": {"column": "info", "title": "news"}},
        {"op": "set", "collection": "comments", "doc_id": "c1", "data": {"post_id": "P-1", "from": "soutien-ti", "text": "done"}},
        {"op": "set", "collection": "comments", "doc_id": "c2", "data": {"post_id": "P-1", "from": "owner", "text": "mine"}}]}})
    check("notify: a question post and a session's answer are pushed, news and the owner's own words are not", len(_got) == 2)
    check("notify: no post text by default, a link to the post", bool(_got) and "Secret" not in _got[0][1] and "school" not in _got[0][1] and _got[0][2].endswith("#/post/P-1"))
    _c["kit"]["notify_detail"] = True; open(_cfgp, "w").write(json.dumps(_c)); del _got[:]
    run_claude("kit_notify.py", {"hook_event_name": "PostToolUse", "tool_name": "ArtifactData", "tool_input": {"action": "set", "collection": "posts", "doc_id": "P-3", "data": {"column": "todo", "title": "Titre é", "body": "corps"}}})
    check("notify: notify_detail adds the title and text", bool(_got) and "Titre é" in _got[0][1])
    _srv.shutdown()
    if _old is None: os.remove(_cfgp)
    else: open(_cfgp, "w").write(_old)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
