#!/usr/bin/env python3
"""The kit's agy hooks on planted inputs, each refusal and each allowance seen. python3 -I kit/hooks/test_hooks.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def role_env(role=None, base=None):
    """The hooks read KIT_ROLE: a test pins it (none = a session the owner types in), whatever launched the suite."""
    env = dict(base if base is not None else os.environ)
    env.pop("KIT_ROLE", None)
    if role:
        env["KIT_ROLE"] = role
    return env


def run(hook, data, proj, role=None):
    payload = dict(data)
    payload.setdefault("cwd", proj)
    payload.setdefault("workspacePaths", [proj])
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, hook)], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=30, env=role_env(role))
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

    cid_act = "act-" + RUN
    act_call = {"hookEventName": "PostToolUse", "conversationId": cid_act,
                "toolCall": {"name": "run_command", "args": {"CommandLine": "npm test"}}}
    for _ in range(7):
        rc_a, out_a, _ = run("kit_activity.py", act_call, proj)
        check("activity: quiet before 8th repeat", rc_a == 0 and out_a == {}) if _ == 6 else None
    rc_a8, out_a8, _ = run("kit_activity.py", act_call, proj)
    rc_pi8, out_pi8, _ = run("kit_activity.py", {"hookEventName": "PostInvocation", "conversationId": cid_act}, proj)
    rc_pi9, out_pi9, _ = run("kit_activity.py", {"hookEventName": "PostInvocation", "conversationId": cid_act}, proj)
    check("activity: 8th repeat silent on PostToolUse and injected once on PostInvocation",
          rc_a8 == 0 and out_a8 == {} and rc_pi8 == 0 and "8 times in a row" in injected_text(out_pi8) and rc_pi9 == 0 and out_pi9 == {})

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
    check("session start: board, language, project line, preferences",
          all(x in start_txt for x in ("127.0.0.1:8765", "French", "BUILD: make test", "ask before any real action")))
    # CL28: where things stand comes from the board, never a memory file's Next: line or awaits count
    check("session start: no memory Next: line nor awaits count; the board's resume instead",
          "ship the login screen" not in start_txt and "2 points" not in start_txt and "kit.py resume" in start_txt
          and "OLD MEMORY (2 files" in start_txt)

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
    cc_orch = cc_transcript("orch-high", "You are the orchestrator: start as the orchestrator for this repository", 2000, 370000, 12000)
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
    check("stop: a long answer to the owner is never retried (he has read it already)", out1.get("decision") == "allow")
    check("stop: a short answer to the owner passes", run("kit_say_it_once.py", St(transcript(20, "oui"), "s2"), proj)[1].get("decision") == "allow")
    f3 = transcript(30, "I've taken over as orchestrator and nothing is running yet.", human=False)
    check("stop: a turn a session or timer opened must end with ·", run("kit_say_it_once.py", St(f3, "s3"), proj)[1].get("decision") == "continue")
    check("stop: · passes", run("kit_say_it_once.py", St(transcript(40, "·", human=False), "s4"), proj)[1].get("decision") == "allow")
    # CL14: only "·" alone passes a non-human turn; text followed by "·", or a short word, does not.
    check("stop: text followed by · fails",
          run("kit_say_it_once.py", St(transcript(41, "Merged and pushed.\n\n·", human=False), "s4t"), proj)[1].get("decision") == "continue")
    check("stop: a short word instead of · fails",
          run("kit_say_it_once.py", St(transcript(42, "ok.", human=False), "s4w"), proj)[1].get("decision") == "continue")
    check("stop: · with surrounding blank lines passes",
          run("kit_say_it_once.py", St(transcript(43, "\n·\n", human=False), "s4b"), proj)[1].get("decision") == "allow")
    # CL14: a background session (KIT_ROLE clerk, part, consultant, cards, review) never has the owner typing.
    for i, role in enumerate(("clerk", "part", "consultant", "cards", "review")):
        check("stop: a %s's turn opened as human must still end with ·" % role,
              run("kit_say_it_once.py", St(transcript(46 + 3 * i, "x" * 509, human=True), "s4r%d" % i), proj, role=role)[1].get("decision") == "continue")
    check("stop: the orchestrator's turn the owner typed may answer in words",
          run("kit_say_it_once.py", St(transcript(62, "x" * 200, human=True), "s4o"), proj, role="orchestrator")[1].get("decision") == "allow")
    check("stop: a background session's · passes",
          run("kit_say_it_once.py", St(transcript(65, "·", human=True), "s4rp"), proj, role="part")[1].get("decision") == "allow")
    pre_bg = injected_text(run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "pr-" + RUN, "lastUserInput": "launch"}, proj, role="clerk")[1])
    check("say: no 'the owner typed this' reminder in a background session", "typed" not in pre_bg)
    # CL26: a Stop block speaks after the text is on his screen; the earliest a hook can act is before the turn
    check("say: a background session gets the single-dot rule BEFORE its turn (PreInvocation)", "\u00b7" in pre_bg)
    pre_x = injected_text(run("kit_say_it_once.py", {"hookEventName": "PreInvocation", "conversationId": "px-" + RUN,
                                                     "lastUserInput": "<cross-session from=\"o\">go on</cross-session>"}, proj)[1])
    check("say: a turn another session opened gets the single-dot rule before the turn", "\u00b7" in pre_x and "typed" not in pre_x)

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
    check("setup: a background session's long closing is still blocked (CL14)",
          run("kit_say_it_once.py", St(transcript(70, "x" * 509, human=True), "s5c"), proj, role="clerk")[1].get("decision") == "continue")
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
    def run_claude(hook, data, role=None):
        env = role_env(role, dict(os.environ, CLAUDE_PROJECT_DIR=proj))
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, hook)], input=json.dumps(data),
                           capture_output=True, text=True, env=env, timeout=30)
        return r.returncode, r.stdout, r.stderr

    rc_cg, _, err_cg = run_claude("kit_guard.py", {"tool_input": {"command": "rm -rf src"}})
    check("claude hook: guard refuses with exit 2 and stderr", rc_cg == 2 and "REFUSED" in err_cg)
    rc_cg_ok, out_cg_ok, _ = run_claude("kit_guard.py", {"tool_input": {"command": "rm -rf /tmp/x"}})
    check("claude hook: guard allows with exit 0", rc_cg_ok == 0 and out_cg_ok.strip() == "")
    rc_cs, out_cs, _ = run_claude("kit_session_start.py", {"hook_event_name": "SessionStart"})
    check("claude hook: SessionStart prints plain text stdout",
          rc_cs == 0 and "kit.py resume" in out_cs and not out_cs.lstrip().startswith("{"))
    rc_ct, out_ct, _ = run_claude("kit_board_typing.py", {"hook_event_name": "UserPromptSubmit", "prompt": "Board comment from the owner on your post P1-x, thread t, 10:00, verbatim: \"a\""})
    check("claude hook: board_typing emits hookSpecificOutput with ArtifactData",
          rc_ct == 0 and "hookSpecificOutput" in out_ct and "ArtifactData" in out_ct)
    rc_csay, _, err_csay = run_claude("kit_say_it_once.py", {"hook_event_name": "PreToolUse", "tool_input": {"message": "x" * 51}})
    check("claude hook: say_it_once PreToolUse refuses long message with exit 2", rc_csay == 2 and "REFUSED" in err_csay)
    rc_cup, out_cup, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
    check("claude hook: say_it_once UserPromptSubmit emits hookSpecificOutput", rc_cup == 0 and "hookSpecificOutput" in out_cup and "123" in out_cup)
    for label, prompt in (("a launch prompt (a web part has no KIT_ROLE)", "Your prompt is docs/prompts/cl9-x.md: read it and do it."),
                          ("a session's message", "<cross-session-message from=\"o\">Check post p</cross-session-message>"),
                          ("a timer", "[SYSTEM NOTIFICATION - NOT USER INPUT] wake")):
        rc_cnh, out_cnh, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        check("claude hook: %s gets the single-dot rule at UserPromptSubmit, before the turn" % label,
              rc_cnh == 0 and "\u00b7" in out_cnh and "typed" not in out_cnh)
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
    check("claude hook: a long answer to the owner is not sent twice (no Stop retry)", rc_cst == 0 and err_cst == "")
    _, out_cph, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse", "transcript_path": cf_t})
    check("claude hook: no · reminder after a board write on a turn the owner typed", out_cph.strip() == "")
    cf_n = os.path.join(d, "claude-n.jsonl")
    with open(cf_n, "w") as fh:
        fh.write(json.dumps({"type": "user", "uuid": "cn1", "message": {"content": "<task-notification>done</task-notification>"}}) + "\n")
    _, out_cpn, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse", "transcript_path": cf_n})
    check("claude hook: · reminder after a board write on a turn a session opened", "·" in out_cpn)
    # CL14: the clerk's launch prompt has origin "human" in Claude Code, yet nobody types in its chat.
    rc_ccl, _, err_ccl = run_claude("kit_say_it_once.py", {"hook_event_name": "Stop", "transcript_path": cf_t, "session_id": "cs-clerk-" + RUN}, role="clerk")
    check("claude hook: the clerk's 200-char closing on a 'human' launch prompt is blocked (exit 2, ·)", rc_ccl == 2 and "·" in err_ccl)
    _, out_ccp, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "PostToolUse", "transcript_path": cf_t}, role="part")
    check("claude hook: a part gets the · reminder after a board write even on a 'human' opener", "·" in out_ccp)
    _, out_ccu, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "UserPromptSubmit", "prompt": "Your prompt is x: read it"}, role="part")
    check("claude hook: no chat_answer_chars reminder in a part's session", "characters" not in out_ccu)
    check("claude hook: a part's session gets the single-dot rule instead (CL26)", "\u00b7" in out_ccu)
    cf_tx = os.path.join(d, "claude-tx.jsonl")
    with open(cf_tx, "w") as fh:
        fh.write(json.dumps({"type": "user", "uuid": "cx1", "message": {"content": "<task-notification>done</task-notification>"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done, merged.\n\n·"}]}}) + "\n")
    rc_ctx, _, _ = run_claude("kit_say_it_once.py", {"hook_event_name": "Stop", "transcript_path": cf_tx, "session_id": "cs-tx-" + RUN})
    check("claude hook: text then · on a turn a session opened is blocked", rc_ctx == 2)
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
        {"op": "set", "collection": "comments", "doc_id": "c1", "data": {"post_id": "P-1", "from": "soutien-ti", "text": "done", "reply_to": "c0"}},
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
