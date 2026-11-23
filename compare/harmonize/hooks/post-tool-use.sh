#!/bin/bash
#
# AFTER A WRITE — count what was twice forgotten.
#
# Adapted on 2026-11-08 from Aplomb's .claude/hooks/post-tool-use.sh (docs/WORKPLAN.md, A3b item 5).
# `docs/GOAL-*.md` stays under 4,000 characters, the /goal limit (skill `goal-prompt`). In Aplomb the bound
# was broken twice in one day, both times for the same reason: the count was taken BEFORE the last edit.
# Arithmetic that people miss and the machine never does. It also counts the first messages of the
# discussions, `docs/prompts/*.md`, which are pasted the same way.
#
# It does not fail, it SAYS: a prompt of 4,020 characters is a bound to bring back, not a safety fault.
# It speaks through JSON `hookSpecificOutput.additionalContext`, which Claude receives. In Aplomb it first
# wrote to stderr and exited 0, and Claude never sees the stderr of a hook that exits 0: a 4,001-character
# file was written and no warning arrived.
#
# Counted in CHARACTERS (Python), not bytes: without a locale `wc -m` counts bytes.
# Its test: python3 -I .claude/hooks/test_prompt_hooks.py

set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
input=$(cat 2>/dev/null || true)

path=$(printf '%s' "$input" | python3 -I -c 'import json, sys
try:
    print((json.load(sys.stdin).get("tool_input") or {}).get("file_path", ""))
except Exception:
    pass' 2>/dev/null)
[ -n "${path:-}" ] || exit 0

case "$path" in
  *docs/GOAL-*.md) kind=goal ;;
  *docs/prompts/*.md) kind=prompt ;;
  *) exit 0 ;;
esac
[ -f "$path" ] || exit 0

say() {
  printf '%s' "$1" | python3 -I -c 'import json, sys; print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": sys.stdin.read()}}, ensure_ascii=False))'
  exit 0
}

n=$(python3 -I -c 'import sys; print(len(open(sys.argv[1], encoding="utf-8").read()))' "$path" 2>/dev/null)
case "${n:-}" in ''|*[!0-9]*) exit 0 ;; esac
rel=${path#"${CLAUDE_PROJECT_DIR:-.}/"}

if [ "$kind" = goal ] && [ "$n" -gt 4000 ]; then
  say "BOUND — $rel is $n characters, over the /goal limit of 4,000.
  Nibbling does not converge: find the CLAUSE to delete, not the word to shorten (skill goal-prompt).
  And RECOUNT after the last edit: that is how the bound was broken twice in Aplomb.
  .claude/tools/harness_check.py refuses the file until it is back under."
fi
if [ "$kind" = goal ]; then
  say "bound OK — $rel: $n / 4000 characters."
fi
say "count — $rel: $n characters (a discussion's first message; a /goal prompt would be limited to 4,000)."
