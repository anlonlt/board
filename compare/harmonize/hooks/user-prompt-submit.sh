#!/bin/bash
#
# ON EACH MESSAGE — quote the progress of a long run that is still alive.
#
# Adapted on 2026-11-08 from Aplomb's .claude/hooks/user-prompt-submit.sh (docs/WORKPLAN.md, A3b item 5).
# Why it exists, in Aplomb: two screenshot campaigns of 23 and 47 minutes ran for four copy fixes. The
# campaign had announced "26/26 screens" on its EIGHTH line; nobody read it, because it ran in the
# background and the conversation saw none of it. One quoted line at the right moment would have cost a
# minute instead of seventy. Here the long runs are `scripts/verify.sh` (A3a): `:core:test`, the
# Roborazzi screens, the APK, a cold Gradle that downloads Robolectric's Android image.
#
# What a hook can do: not write the answer, but put the fact in front of Claude at the exact moment it
# answers the owner, through JSON `additionalContext` (Claude never sees the stderr of a hook that exits 0).
#
# The progress file is `build/harmonize-run.json`, written by scripts/verify.sh:
#   {"pid": 12345, "line": "core=OK screens=… (running :app:testDebugUnitTest)"}
#
# THE PID IS CHECKED, AND THAT IS THE POINT. A progress file alone would be a probe that lies: a killed
# run leaves its last line frozen, and it would be quoted as if alive. Aplomb learned twice how:
#   · `kill -0` is not enough: a ZOMBIE is still a process until reaped, and `kill -0` says yes;
#   · a zombie has an EMPTY /proc/PID/cmdline, so it is READ, not sized: procfs reports a size of zero
#     for every file, so `[ -s /proc/PID/cmdline ]` was false for the living too, and the hook was mute.
# Only a living run is quoted. An orphaned file is left alone (scripts/verify.sh owns it) and said nothing of.
# Its test: python3 -I .claude/hooks/test_prompt_hooks.py

set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
cat >/dev/null 2>&1 || true   # the prompt itself is not needed

run="build/harmonize-run.json"
[ -f "$run" ] || exit 0

# Read pid and line with a JSON parser, not sed: a line may hold quotes or braces.
read_field() {
  python3 -I -c 'import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    v = d.get(sys.argv[2], "")
    print(str(v).replace("\n", " ") if v is not None else "")
except Exception:
    pass' "$run" "$1" 2>/dev/null
}
pid=$(read_field pid)
case "${pid:-}" in ''|*[!0-9]*) exit 0 ;; esac

# Alive AND not a zombie: it exists, and it still has a command line. The redirection is tested first:
# when the process is gone, it is the shell that complains of the missing file, not `tr`.
argv=""
[ -r "/proc/$pid/cmdline" ] && argv=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
if ! kill -0 "$pid" 2>/dev/null || [ -z "${argv// /}" ]; then
  exit 0
fi

line=$(read_field line)
command=$(printf '%s' "$argv" | cut -c1-120)

message="LONG RUN IN PROGRESS (pid $pid) — \`${command:-unknown}\`

  ${line:-progress unavailable}

QUOTE THIS LINE AT THE HEAD OF YOUR ANSWER, before anything else, then answer the owner. If the run
looks out of proportion to what was asked, say so and offer to stop it."

printf '%s' "$message" | python3 -I -c 'import json, sys; print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": sys.stdin.read()}}, ensure_ascii=False))'
exit 0
