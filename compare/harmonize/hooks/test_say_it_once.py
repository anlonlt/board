#!/usr/bin/env python3
"""Test of say_it_once.py: each case runs the hook as Claude Code does (JSON on stdin, the exit code read)."""
import json
import os
import subprocess
import sys
import tempfile
import uuid

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "say_it_once.py")


def run(payload, role=None):
    env = dict(os.environ)
    env.pop("HARMONIZE_ROLE", None)
    env["CLAUDE_PROJECT_DIR"] = tempfile.gettempdir()  # not a git checkout: no orchestrator branch
    if role:
        env["HARMONIZE_ROLE"] = role
    p = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), text=True, capture_output=True, env=env)
    if payload.get("hook_event_name") == "Stop":
        # Since 2026-11-14 10:33: 2 = blocked (once a turn, the retry may only be "·"), "miss" = recorded after it.
        if p.returncode == 2:
            assert "a single" in p.stderr, ("a block names the retry", p.stderr)
            return 2
        assert p.returncode == 0, (p.returncode, p.stderr)
        return "miss" if "SAY-IT-ONCE MISS:" in p.stdout else 0
    return p.returncode


def transcript(entries):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for kind, content in entries:
        f.write(json.dumps({"type": kind, "message": {"content": content}}) + "\n")
    f.close()
    return f.name


def text(n):
    return [{"type": "text", "text": "x" * n}]


def stop(entries, sid=None, active=False):
    return {"hook_event_name": "Stop", "session_id": sid or uuid.uuid4().hex, "transcript_path": transcript(entries), "stop_hook_active": active}


def msg(m, sid=None):
    return {"hook_event_name": "PreToolUse", "session_id": sid or POSTED, "tool_name": "mcp__claude-code-remote__send_message", "tool_input": {"message": m}}


def board(action, sid):
    return {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "ArtifactData", "tool_input": {"action": action, "collection": "posts"}}


POSTED = uuid.uuid4().hex  # a session whose post exists
run(board("set", POSTED))


cases = [
    ("a short message passes", run(msg("Done. Check post X3-his-takes.")), 0),
    ("a long message is refused", run(msg("y" * 601)), 2),
    ("the clerk's forward of his words passes whole", run(msg("Board comment from the owner on your post p: " + "y" * 3000)), 0),
    ("an orchestrator's long message passes", run(msg("y" * 3000), role="orchestrator"), 0),
    ("a short closing message passes", run(stop([("user", "go"), ("assistant", text(300))])), 0),
    ("a 700-character closing message blocks (limit 600)", run(stop([("user", "go"), ("assistant", text(700))])), 2),
    ("a long closing message blocks", run(stop([("user", "go"), ("assistant", text(1300))])), 2),
    ("long text BEFORE a tool call is not the closing message",
     run(stop([("user", "go"), ("assistant", text(1300) + [{"type": "tool_use", "name": "Bash"}]),
               ("user", [{"type": "tool_result", "content": "ok"}]), ("assistant", text(40))])), 0),
    ("an earlier turn's long message does not count", run(stop([("user", "go"), ("assistant", text(1300)), ("user", "next"), ("assistant", text(40))])), 0),
    ("a stop continued by a hook is checked again (the owner, 2026-11-13)", run(stop([("user", "go"), ("assistant", text(1300))], active=True)), 2),
    ("an orchestrator's long closing message blocks too (the owner, 2026-11-12 19:25)", run(stop([("user", "go"), ("assistant", text(1300))]), role="orchestrator"), 2),
    ("an orchestrator's short closing message passes", run(stop([("user", "go"), ("assistant", text(300))]), role="orchestrator"), 0),
]
sid = uuid.uuid4().hex
first = run(stop([("user", "go"), ("assistant", text(1300))], sid=sid))
second = run(stop([("user", "go"), ("assistant", text(1300))], sid=sid))
third = run(stop([("user", "go"), ("assistant", text(1))], sid=sid))
cases.append(("a long answer is blocked once; a long retry is recorded, not blocked", (first, second), (2, "miss")))
cases.append(("a '·' retry after the block passes", third, 0))

env = dict(os.environ)
env.pop("HARMONIZE_ROLE", None)
env["CLAUDE_PROJECT_DIR"] = tempfile.gettempdir()
out = subprocess.run([sys.executable, "-I", HOOK], text=True, capture_output=True, env=env,
                     input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "mcp__claude-code-remote__send_message"})).stdout
cases.append(("after a message or a board write, the one-line rule is given as context",
              "single" in (json.loads(out or "{}").get("hookSpecificOutput") or {}).get("additionalContext", ""), True))

fresh, reader = uuid.uuid4().hex, uuid.uuid4().hex
cases.append(("before its post exists, a 300-character message is refused (M1: the turn-1 leak)", run(msg("y" * 300, sid=fresh)), 2))
cases.append(("before its post exists, a pointer or a short question passes", run(msg("y" * 200, sid=fresh)), 0))
run(board("query", reader))
cases.append(("a board READ is not a post", run(msg("y" * 300, sid=reader)), 2))
run(board("set", fresh))
cases.append(("once the post is written, 300 characters pass", run(msg("y" * 300, sid=fresh)), 0))

osid = uuid.uuid4().hex
o1 = run(stop([("user", "go"), ("assistant", text(1300))], sid=osid), role="orchestrator")
o2 = run(stop([("user", "go"), ("assistant", text(9)), ("user", "next"), ("assistant", text(1300))], sid=osid), role="orchestrator")
cases.append(("an orchestrator is blocked in every turn, not once a session", (o1, o2), (2, 2)))

# the board clerk (the owner, 2026-11-12 20:00: "limited to 0 character messages, since all it does is relay stuff")
clerk = uuid.uuid4().hex
run(msg("Board comment from the owner on your post p: hello", sid=clerk))  # its forward marks the session as the clerk
out = subprocess.run([sys.executable, "-I", HOOK], text=True, capture_output=True, env=env,
                     input=json.dumps({"hook_event_name": "PostToolUse", "session_id": clerk, "tool_name": "mcp__claude-code-remote__send_message"})).stdout
cases.append(("after its forward, the clerk is told to end with no text", "no text" in (json.loads(out or "{}").get("hookSpecificOutput") or {}).get("additionalContext", ""), True))
cases.append(("the clerk's 100-character closing message blocks", run(stop([("user", "go"), ("assistant", text(100))], sid=clerk)), 2))
cases.append(("the clerk's '·' passes", run(stop([("user", "go"), ("assistant", [{"type": "text", "text": "·"}])], sid=clerk)), 0))
cases.append(("a part's 100-character closing message passes", run(stop([("user", "go"), ("assistant", text(100))])), 0))
sh = os.path.join(os.path.dirname(HOOK), "stop.sh")
dirty = tempfile.mkdtemp()
subprocess.run("git init -q . && git -c user.email=a@b -c user.name=t commit -q --allow-empty -m x && touch new-file", shell=True, cwd=dirty)
def save_point(sid):
    e = dict(env); e["CLAUDE_PROJECT_DIR"] = dirty
    return subprocess.run(["bash", sh], input=json.dumps({"session_id": sid}), text=True, capture_output=True, env=e).returncode
cases.append(("the save-point hook asks a part to commit, and leaves the clerk alone", (save_point(uuid.uuid4().hex), save_point(clerk)), (2, 0)))

# the consultant, 2026-11-14 (the owner: "fix the hook so that it doesn't bother you as consultant anymore"): a container
# rebuilt from an old shallow clone shares no commit with the fetched main, so "ahead" counted the whole old clone (120).
stale = tempfile.mkdtemp()
G = "git -c user.email=a@b -c user.name=t "
subprocess.run("git init -q -b main . && " + G + "commit -q --allow-empty -m old && git checkout -q --orphan tip && " + G + "commit -q --allow-empty -m tip"
               " && git update-ref refs/remotes/origin/main HEAD && git checkout -q main && git remote add origin /nonexistent"
               " && git config branch.main.remote origin && git config branch.main.merge refs/heads/main", shell=True, cwd=stale)
def stale_stop():
    e = dict(env); e["CLAUDE_PROJECT_DIR"] = stale
    return subprocess.run(["bash", sh], input=json.dumps({"session_id": uuid.uuid4().hex}), text=True, capture_output=True, env=e).returncode
before = stale_stop()
head = subprocess.run("git rev-parse HEAD", shell=True, cwd=stale, text=True, capture_output=True).stdout
open(os.path.join(stale, ".git", "harmonize-session-base"), "w").write(head)
quiet = stale_stop()
subprocess.run(G + "commit -q --allow-empty -m mine", shell=True, cwd=stale)
cases.append(("a rebuilt old clone: with no session base the old commits count; with it they do not; a commit of the session does",
              (before, quiet, stale_stop()), (2, 0, 2)))

open(os.path.join(dirty, ".git", "harmonize-clerk"), "w").close()
cases.append(("the clerk's set-up file alone silences the save point, before any forward", save_point(uuid.uuid4().hex), 0))
e2 = dict(env); e2["CLAUDE_PROJECT_DIR"] = dirty
cases.append(("and its chat is limited from its first turn",
              (lambda p: (p.returncode, "SAY-IT-ONCE MISS:" in p.stdout))(subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(stop([("user", "go"), ("assistant", text(100))])), text=True, capture_output=True, env=e2)), (2, False)))

# the owner, 2026-11-13 03:52: "Why are you writing so much ?" — an orchestrator's turn he did not type ends with no text
def notif_stop(closing, sid, active=False, opener="<task-notification>\n<summary>1 unread notification</summary>"):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write(json.dumps({"type": "user", "uuid": "h1", "origin": {"kind": "human"}, "message": {"content": "go"}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}) + "\n")
    f.write(json.dumps({"type": "user", "uuid": "n1", "origin": {"kind": "task-notification"}, "message": {"content": opener}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "ReadNotifications"}]}}) + "\n")
    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}) + "\n")
    if active:
        f.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback: ..."}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": closing}] if closing else []}}) + "\n")
    f.close()
    return {"hook_event_name": "Stop", "session_id": sid, "transcript_path": f.name, "stop_hook_active": active}

ns = uuid.uuid4().hex
cases.append(("orchestrator, a session's message: a one-line relay note blocks",
              run(notif_stop("Your question went to Z2, which will answer under your comment.", ns), role="orchestrator"), 2))
cases.append(("orchestrator, a session's message: the retry is checked too (recorded when not '·')",
              run(notif_stop("Relayed to Z2.", ns, active=True), role="orchestrator"), "miss"))
cases.append(("orchestrator, a session's message: no text passes", run(notif_stop("", uuid.uuid4().hex), role="orchestrator"), 0))
cases.append(("orchestrator, a session's message: a single dot passes", run(notif_stop("·", uuid.uuid4().hex), role="orchestrator"), 0))
cases.append(("orchestrator, a session's message: two words block", run(notif_stop("Noted.", uuid.uuid4().hex), role="orchestrator"), 2))
cases.append(("orchestrator, a session's message: 'Done and checked: …' in three lines passes",
              run(notif_stop("Done and checked: V5.\nThe bank is 40 MB.\nNothing for you to do.", uuid.uuid4().hex), role="orchestrator"), 0))
cases.append(("orchestrator, a session's message: 'Broken: …' passes", run(notif_stop("Broken: the store refuses pushes.", uuid.uuid4().hex), role="orchestrator"), 0))
cases.append(("orchestrator, a session's message: a long 'Done and checked' blocks",
              run(notif_stop("Done and checked: " + "x" * 400, uuid.uuid4().hex), role="orchestrator"), 2))
cap = uuid.uuid4().hex
caps = tuple(run(notif_stop("Relayed.", cap, active=i > 0), role="orchestrator") for i in range(4))
cases.append(("blocked once a turn, then recorded (no loop)", caps, (2, "miss", "miss", "miss")))
cases.append(("orchestrator, a turn he typed: a short answer passes",
              run(stop([("user", "Why so much?"), ("assistant", text(200))]), role="orchestrator"), 0))
tp = notif_stop("", uuid.uuid4().hex)["transcript_path"]
out = subprocess.run([sys.executable, "-I", HOOK], text=True, capture_output=True, env=dict(env, HARMONIZE_ROLE="orchestrator"),
                     input=json.dumps({"hook_event_name": "PostToolUse", "transcript_path": tp, "tool_name": "mcp__claude-code-remote__send_message"})).stdout
cases.append(("orchestrator, a session's message: the no-text rule arrives before the stop",
              "NO text" in (json.loads(out or "{}").get("hookSpecificOutput") or {}).get("additionalContext", ""), True))

# every session ends its turns with "·" (the owner, 2026-11-13: "Could every discussion do that ?")
cases.append(("a part, a session's message: 'Posted: X' blocks", run(notif_stop("Posted: V5-neural-voice-where", uuid.uuid4().hex)), 2))
cases.append(("a part, a session's message: '·' passes", run(notif_stop("·", uuid.uuid4().hex)), 0))
cases.append(("a part, its launch prompt (long): a one-line close blocks",
              run(stop([("user", "P" * 3000), ("assistant", text(30))])), 2))
cases.append(("a part, a short question he typed: a short answer passes", run(stop([("user", "Why?"), ("assistant", text(200))])), 0))
cases.append(("the clerk, a question he typed: still only '·'", run(stop([("user", "go"), ("assistant", text(1)), ("user", "Why?"), ("assistant", text(30))], sid=clerk)), 2))
pc = uuid.uuid4().hex
cases.append(("a part is blocked once a turn, then recorded (no loop)",
              tuple(run(notif_stop("Posted: X", pc, active=i > 0)) for i in range(4)), (2, "miss", "miss", "miss")))

def ctx(payload, role=None):
    env = dict(os.environ)
    env.pop("HARMONIZE_ROLE", None)
    env["CLAUDE_PROJECT_DIR"] = tempfile.gettempdir()
    if role:
        env["HARMONIZE_ROLE"] = role
    return subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), text=True, capture_output=True, env=env).stdout

_sm = {"hook_event_name": "PostToolUse", "tool_name": "mcp__claude-code-remote__send_message", "session_id": "kg"}
cases.append(("a part's Check post says keep going",
              "KEEP GOING" in ctx(dict(_sm, tool_input={"message": "Check post Z3-speaker: turn 1 read"})), True))
cases.append(("a part's Done does not say keep going",
              "KEEP GOING" in ctx(dict(_sm, tool_input={"message": "Done. Check post Z3-speaker."})), False))
# part M1e: one 60-minute fallback that checks every running part, not a 12-minute check per launch (M1d: 53 check-ins)
_launch = ctx({"hook_event_name": "PostToolUse", "tool_name": "mcp__claude-code-remote__create_session",
               "tool_input": {}, "session_id": "kg2"}, role="orchestrator")
cases.append(("the orchestrator's create_session keeps ONE 60-minute fallback over every running part",
              "60 minutes" in _launch and "every running part" in _launch and "12 minutes" not in _launch, True))


# the owner, 2026-11-13 08:42: his words typed while a session's turn ran were answered as a timer's turn
def midturn(closing):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write(json.dumps({"type": "user", "uuid": "n1", "origin": {"kind": "task-notification"}, "message": {"content": "<task-notification>x"}}) + "\n")
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "What is the consultant doing?",
                        "source_uuid": "q1", "origin": {"kind": "human"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": closing}]}}) + "\n")
    f.close()
    return {"hook_event_name": "Stop", "session_id": uuid.uuid4().hex, "transcript_path": f.name}
cases.append(("orchestrator, his words typed mid-turn: a short answer passes", run(midturn("x" * 400), role="orchestrator"), 0))
cases.append(("orchestrator, his words typed mid-turn: a long answer blocks", run(midturn("x" * 900), role="orchestrator"), 2))
cases.append(("orchestrator, a turn he typed: the limit arrives before the answer",
              "600 characters" in ctx({"hook_event_name": "UserPromptSubmit", "session_id": "u1"}, role="orchestrator"), True))

cases.append(("orchestrator, a notification's prompt: no 'owner typed' context",
              "owner typed" in ctx({"hook_event_name": "UserPromptSubmit", "session_id": "u2",
                                    "prompt": "<task-notification>\n<summary>1 unread</summary>"}, role="orchestrator"), False))

# every session gets the rule when its turn starts (the owner, 2026-11-14 10:55: "yes add it")
cases.append(("a part, a question he typed: the 600-character rule arrives before the answer",
              "600 characters" in ctx({"hook_event_name": "UserPromptSubmit", "session_id": "p1", "prompt": "Why?"}), True))
cases.append(("a part, a session's message: the '·' rule arrives before the answer",
              "single" in ctx({"hook_event_name": "UserPromptSubmit", "session_id": "p2",
                               "prompt": "<task-notification>\n<summary>1 unread</summary>"}), True))
cases.append(("a part, its launch prompt (long): the '·' rule",
              "did not type" in ctx({"hook_event_name": "UserPromptSubmit", "session_id": "p3", "prompt": "P" * 3000}), True))
_tp = transcript([("user", "Why?")])
cases.append(("a part, after a tool in a turn he typed: the 600-character rule, not '·'",
              "600 characters" in ctx(dict(_sm, session_id="p4", transcript_path=_tp, tool_input={"message": "x"})), True))

# the owner, 2026-11-16: "If it's anything I have to wait for and can be sent as a link, it should be in the board,
# only the board." On a turn he typed: a link to an artifact, or anything he must do, decide, test or wait for, blocks.
SPEC = "SAY IT ONCE — what he must act on or wait for goes on the board only, under its post (docs/BOARD.md); chat ends with a single '·'."
def stderr_of(payload, role=None):
    e = dict(env)
    if role:
        e["HARMONIZE_ROLE"] = role
    p = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload), text=True, capture_output=True, env=e)
    return p.returncode, SPEC in p.stderr
LINK = "The test page is up: https://claude.ai/artifact/9f3Kq2LmZx7Tb4 . Open it on the phone."
_wrote = [("user", "Is the test page up?"),
          ("assistant", [{"type": "tool_use", "name": "ArtifactData", "input": {"action": "set"}}]),
          ("user", [{"type": "tool_result", "content": "ok"}]), ("assistant", [{"type": "text", "text": LINK}])]
for role in (None, "orchestrator"):
    who = "orchestrator" if role else "a part"
    cases.append((f"{who}, a turn he typed: another artifact's link in chat blocks, with his rule",
                  stderr_of(stop([("user", "Is the test page up?"), ("assistant", [{"type": "text", "text": LINK}])]), role), (2, True)))
    cases.append((f"{who}, a turn he typed: the link blocks even after a board write in the turn",
                  stderr_of(stop(_wrote), role), (2, True)))
    cases.append((f"{who}, a turn he typed: a question asking him to delete/decide blocks",
                  stderr_of(stop([("user", "What about the old page?"), ("assistant", [{"type": "text", "text":
                      "The old test page is superseded. Should I delete it, or do you want to keep it?"}])]), role), (2, True)))
    cases.append((f"{who}, a turn he typed: asking him to listen and rate blocks",
                  stderr_of(stop([("user", "Ready?"), ("assistant", [{"type": "text", "text":
                      "Yes. Please listen to the three takes and rate them when you can."}])]), role), (2, True)))
    cases.append((f"{who}, a turn he typed: '·' passes", run(stop([("user", "Is it up?"), ("assistant", [{"type": "text", "text": "·"}])]), role), 0))
    cases.append((f"{who}, a turn he typed: a plain explanatory answer under 600 passes",
                  run(stop([("user", "What happened to the hook?"), ("assistant", [{"type": "text", "text":
                      "It still runs at every stop. It let the message through because a turn you typed was only held "
                      "to 600 characters, whatever they said; the link fit under that limit."}])]), role), 0))
    cases.append((f"{who}, a turn he typed: a bare mention of the board's own URL passes",
                  run(stop([("user", "Where is it?"), ("assistant", [{"type": "text", "text":
                      "On the board, https://claude.ai/artifact/XXXXXXXXXXXXXXXXXXXXXX, under post V5."}])]), role), 0))
cases.append(("a part, a session's message: a link close blocks with the old '·' rule, unchanged",
              (lambda r: (r[0], r[1]))(stderr_of(notif_stop(LINK, uuid.uuid4().hex))), (2, False)))
cases.append(("orchestrator, a timer's turn: 'Done and checked: …' still passes, unchanged",
              run(notif_stop("Done and checked: V5.\nThe bank is 40 MB.", uuid.uuid4().hex), role="orchestrator"), 0))

bad = [(name, got, want) for name, got, want in cases if got != want]
for name, got, want in cases:
    print(("ok   " if got == want else "FAIL ") + name + ("" if got == want else f" (got {got}, want {want})"))
print(f"{len(cases) - len(bad)} of {len(cases)} cases pass")
sys.exit(1 if bad else 0)
