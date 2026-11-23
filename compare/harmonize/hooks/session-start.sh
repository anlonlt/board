#!/bin/bash
#
# SESSION START — the memory is served, not fetched.
#
# Adapted on 2026-11-08 from the owner's Aplomb project (.claude/hooks/session-start.sh). What this
# hook's output says enters the session's context: the memory is already there instead of being
# something to go and read. That is the only check that holds for a reading — making it unavoidable
# rather than verifying it afterwards.
#
# Short on purpose: the build tools, whether this clone is behind its branch, other branches that
# rewrote a state, each state's next step, what awaits the owner, the open issues, the lesson count.
# The detail is read on demand, and the hook says where.

set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# Without a locale, `cut -c` cuts at the BYTE: an "é" cut at the end of a line came out as "�".
if command -v iconv >/dev/null 2>&1; then
  trim() { cut -c1-"$1" | iconv -c -f utf-8 -t utf-8; }
else
  trim() { cut -c1-"$1"; }
fi

# ————— 1 · The build tools —————
# CLAUDE.md, "Commands": where the wrapper download fails, the preinstalled gradle works the same, with
# ANDROID_HOME set. `:core:test` needs no Android SDK; `:app:*` does.
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$sdk" ]; then
  for d in /opt/android-sdk /opt/android /usr/lib/android-sdk "$HOME/Android/Sdk"; do
    [ -d "$d/platforms" ] && { sdk="$d"; break; }
  done
fi
g=$(command -v gradle 2>/dev/null)
echo "BOARD — say it once (the owner, 2026-11-12): your result goes in ONE place, your board post (docs/BOARD.md), made at turn 1 and updated, in full."
echo "  Every chat turn then ends with a single \"·\"; to the orchestrator: \"Done. Check post <id>.\" or \"Done. Continue with <prompt>.\""
echo "  A question the orchestrator can answer goes to it by send_message (short), not to the owner. A hook refuses long copies."
echo
echo "BUILD — engine tests: scripts/test-changed.sh (only what your changes can break); all: ./gradlew :core:test (or ${g:-gradle} :core:test if the wrapper download fails)."
if [ -n "$sdk" ]; then
  echo "  Android SDK: $sdk — export ANDROID_HOME=$sdk for :app:assembleDebug and :app:testDebugUnitTest."
else
  echo "  No Android SDK found: :core:test runs; :app:* needs one (CI builds the APK on every push)."
fi

# ————— 2 · Is this clone at the tip of its branch? —————
# In Aplomb, a container rebuilt from an old clone showed a HEAD five commits behind, with no symptom:
# the work was on the remote, intact. Without a fetch, one measures the clone believing one measures the
# work. A fetch, never a pull: nobody wants a merge at session start.
# The HEAD this container started with, kept once: stop.sh counts a rebuilt old clone's unpushed commits from it.
sb="$(git rev-parse --git-dir 2>/dev/null)/harmonize-session-base"
[ -s "$sb" ] || git rev-parse --verify --quiet HEAD > "$sb" 2>/dev/null
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "${branch:-}" ] && [ "$branch" != "HEAD" ]; then
  timeout 20 git fetch --quiet origin "$branch" 2>/dev/null
  upstream=$(git rev-parse --quiet --verify "refs/remotes/origin/$branch" 2>/dev/null)
  here=$(git rev-parse HEAD 2>/dev/null)
  if [ -n "${upstream:-}" ] && [ "$upstream" != "$here" ]; then
    behind=$(git rev-list --count "HEAD..origin/$branch" 2>/dev/null)
    ahead=$(git rev-list --count "origin/$branch..HEAD" 2>/dev/null)
    echo
    echo "CLONE — this clone is NOT at the tip of its own branch: $branch is $behind behind, $ahead ahead."
    if ! git merge-base HEAD "origin/$branch" >/dev/null 2>&1; then
      echo "  The two share no commit: a container rebuilt from an old shallow clone. The counts mean nothing and its files are old."
      echo "  With a clean tree: git branch stale-clone HEAD && git checkout -B $branch origin/$branch — before reading anything."
    fi
    if [ "${behind:-0}" -gt 0 ] && [ "${ahead:-0}" -eq 0 ]; then
      echo "  The remote holds work this clone does not have — probably yours, from an earlier container."
      echo "  NOTHING IS LOST: git checkout -B $branch origin/$branch — and conclude nothing before that."
    fi
  fi
fi

# ————— 3 · Did another session go on elsewhere? —————
# Remote branches active in the last two days, not contained here, that rewrote a state or the owner's
# list: that is where one learns what is already done. A failed fetch is SAID: its silence would read
# "no other branch moved".
if timeout 20 git fetch --quiet origin 2>/dev/null; then
  since=$(( $(date +%s) - 172800 ))
  elsewhere=""
  seen=0
  for b in $(git for-each-ref --sort=-committerdate --format='%(committerdate:unix) %(refname:short)' refs/remotes/origin |
    awk -v d="$since" '$1 > d { print $2 }'); do
    case "$b" in origin/HEAD|origin|"origin/${branch:-}") continue ;; esac
    git merge-base --is-ancestor "$b" HEAD 2>/dev/null && continue
    states=$(git diff --name-only "HEAD...$b" -- .story/state/ .story/awaits-anlon.md 2>/dev/null | sort |
      while read -r e; do
        [ "$(git rev-parse -q --verify "$b:$e" 2>/dev/null)" != "$(git rev-parse -q --verify "HEAD:$e" 2>/dev/null)" ] && echo "$e"
      done | tail -3)
    [ -z "$states" ] && continue
    seen=$((seen + 1))
    [ "$seen" -gt 3 ] && break
    elsewhere="$elsewhere
  ${b#origin/} — $(git log -1 --date=format:'%Y-%m-%d %H:%M' --format='%cd · %s' "$b" | trim 90)"
    for e in $states; do elsewhere="$elsewhere
      differs from here: $e"; done
  done
  if [ -n "$elsewhere" ]; then
    echo
    echo "ELSEWHERE — other remote branches carry states this clone does not have:$elsewhere"
    echo "  Read them BEFORE starting (git show origin/<branch>:<file>): another session may have done it."
  fi
else
  echo
  echo "ELSEWHERE — NOT CHECKED: fetching the remote failed or took over 20 s. This silence does NOT mean"
  echo "  no other branch moved: git fetch origin && git for-each-ref --sort=-committerdate --count=8 refs/remotes"
fi

# ————— 4 · The plan's board, in one line —————
if [ -f PLAN.md ]; then
  board=$(python3 -I - PLAN.md <<'PY' 2>/dev/null
import re, sys
out = []
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"^(\d+)\. \*\*(.+?)\*\* — (.*)", line)
    if m:
        status = m.group(3).split(".")[0].strip()
        status = status if re.match(r"(done|built|in progress|wip)\b", status) else "not started"
        out.append(f"{m.group(1)} {m.group(2)}: {status}")
print(" · ".join(out))
PY
)
  [ -n "$board" ] && { echo; echo "PLAN — $board"; }
fi

# ————— 5 · The memory —————
echo
echo "MEMORY — .story/ is read FOR you (skill story). What follows replaces the gesture, not the"
echo "  reading: open in full the state of the workstream you work on."
if [ -d .story/state ]; then
  echo
  echo "  STATES — one per workstream (.story/state/), rewritten at each save point. Next step of each:"
  for f in .story/state/*.md; do
    [ -f "$f" ] || continue
    step=$(awk '/^## Next step/ { inside = 1; next } /^## / { inside = 0 } inside && NF { print; exit }' "$f")
    echo "    $(basename "$f" .md) — ${step:-NO NEXT STEP WRITTEN}" | trim 200
  done
fi

if [ -f .story/awaits-anlon.md ]; then
  echo
  echo "  AWAITS ANLON ($(grep -c '^- \[' .story/awaits-anlon.md)) — .story/awaits-anlon.md, each with its default and"
  echo "  deadline; open the points of your workstream. Per workstream:"
  grep -o '^- \[[^]]*\]' .story/awaits-anlon.md | sort | uniq -c | sort -rn \
    | awk '{ printf "%s%s %s", (NR > 1 ? " · " : "    "), $3, $1 } END { print "" }'
  python3 -I .claude/skills/story/scripts/check.py 2>/dev/null | grep '^OVERDUE' | trim 110 | sed 's/^/    /'
fi

# The OPEN issues, and only those, read from their line 2. An issue without a status is printed too,
# marked: better one title too many than an open defect nobody sees.
if [ -d .story/issues ]; then
  open=0; closed=0; list=""
  for f in .story/issues/*.md; do
    [ -f "$f" ] || continue
    case "$(sed -n 2p "$f")" in
      "Status: closed"*) closed=$((closed + 1)); continue ;;
      "Status: open"*) mark="" ;;
      *) mark="NO STATUS · " ;;
    esac
    open=$((open + 1))
    list="$list    $(basename "$f" | cut -c1-10)  $mark$(sed -n 1p "$f" | sed -E 's/^# *//')
"
  done
  echo
  echo "  OPEN ISSUES ($open; $closed closed) — a logged defect is not diagnosed again:"
  printf '%s' "$list" | head -10 | trim 100
fi

if [ -d .story/lessons ]; then
  echo
  echo "  LESSONS ($(ls -1 .story/lessons/ | wc -l)) — search by symptom: grep -ril '<word>' .story/lessons/"
fi

# Long runs a container restart stopped: relaunched (part VR2-D; .claude/resume-runs.json)
python3 -I .claude/hooks/resume_runs.py
exit 0
