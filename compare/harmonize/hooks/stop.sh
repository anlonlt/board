#!/bin/bash
#
# AT STOP — a save point at each turn, demanded only when something is in danger.
#
# One never knows which turn is the last: a compaction takes the conversation, a rebuilt container
# takes what was not pushed, a usage limit cuts a turn in the middle. The previous source of this app
# was lost (CLAUDE.md, "Never"); pushed commits are what survives.
#
# Adapted on 2026-11-08 from the owner's Aplomb project (.claude/hooks/stop.sh). It blocks the stop ONCE,
# exiting 2 with the reason on stderr (Claude sees a hook's stderr only when it exits 2), and only if:
#   · changes are not committed (tracked or new files);
#   · commits are not pushed;
#   · main does not contain the branch yet (main is the trunk).
# Not to loop (a refused push, for example), it stays silent when its input carries
# `"stop_hook_active": true`: Claude is already continuing because of a stop hook.

set -uo pipefail
input=$(cat 2>/dev/null || true)
printf '%s' "$input" | grep -Eq '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0

# The board clerk relays comments and commits nothing: no save point for it (the owner, 2026-11-12: "it keeps
# getting annoyed by the hook asking it to commit stuff"). say_it_once.py marks its session at its first forward.
sid=$(printf '%s' "$input" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("session_id") or "")' 2>/dev/null)
[ -n "$sid" ] && [ -e "$(python3 -I -c 'import tempfile; print(tempfile.gettempdir())')/harmonize-clerk-$sid" ] && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -e "$(git rev-parse --git-dir 2>/dev/null)/harmonize-clerk" ] && exit 0  # the clerk's set-up step wrote it
git rev-parse --verify --quiet HEAD >/dev/null 2>&1 || exit 0
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

reasons=""
add() { reasons="$reasons
  · $1"; }

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  add "changes are not committed ($(git status --porcelain | wc -l | tr -d ' ') file(s))"
fi

if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
  ahead=$(git rev-list --count "$upstream..HEAD" 2>/dev/null || echo 0)
  # A container rebuilt from an old shallow clone shares no commit with the fetched upstream: the count is then the
  # whole old clone (120 for the consultant, 2026-11-14), not work. Count only what this session added to the HEAD
  # session-start.sh recorded. With no record, the count stands.
  base=$(cat "$(git rev-parse --git-dir)/harmonize-session-base" 2>/dev/null)
  if [ -n "$base" ] && ! git merge-base "$upstream" HEAD >/dev/null 2>&1; then
    ahead=$(git rev-list --count HEAD "^$upstream" "^$base" 2>/dev/null || echo "$ahead")
  fi
  [ "${ahead:-0}" -gt 0 ] && add "$ahead commit(s) are not pushed to $upstream"
elif [ "$branch" != "HEAD" ] && git remote | grep -q .; then
  add "branch « $branch » has no upstream: nothing is pushed (git push -u origin $branch)"
fi

# Main is the trunk (the owner, 2026-11-09): every save point ends on main. Bounded fetch; silent on failure.
if [ -z "$reasons" ] && [ "$branch" != "main" ] && [ "$branch" != "HEAD" ]; then
  if timeout 15 git fetch -q origin main 2>/dev/null && ! git merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
    add "main does not contain this branch yet: .claude/skills/story/scripts/end-session.sh (it merges a main another session moved, then fast-forwards main)"
  fi
fi

[ -z "$reasons" ] && exit 0
{
  echo "SAVE POINT — before stopping:$reasons"
  echo "  Commit by name (never git add -A) and push. At the end of a session:"
  echo "  .claude/skills/story/scripts/end-session.sh (the state, the checker, the push)."
} >&2
exit 2
