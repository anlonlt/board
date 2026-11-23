#!/bin/bash
#
# end-session.sh — a session's save point in one gesture.
#
#   .claude/skills/story/scripts/end-session.sh             check, push the branch, then bring it onto main
#   .claude/skills/story/scripts/end-session.sh --branch-only   … push the branch and leave main alone
#   .claude/skills/story/scripts/end-session.sh --tests     … first the engine tests that the unpushed
#                                                            changes can break, quick tier (scripts/test-changed.sh)
#   .claude/skills/story/scripts/end-session.sh --final     … the session's last save: the same, full tier, over
#                                                            every change quick-tested since the last full pass
#   .claude/skills/story/scripts/end-session.sh --all-tests … first the whole engine suite, full tier
#
# Adapted on 2026-11-08 from the owner's Aplomb project (scripts/fin-de-seance.sh). It NEVER runs
# `git add -A` (it refuses a dirty tree and names the files), never forces a push.
#
# MAIN IS THE TRUNK (the owner, 2026-11-09: pushing main is his standing permission, as in his `french`
# repository's fin-de-seance.sh --main). A session starts from main and ends on it. If another session
# moved main, origin/main is merged into the branch first; a conflict aborts the merge and pushes nothing.
# Then main moves forward by fast-forward only. Nothing deploys from main: CI builds an APK per push.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

TESTS=0; MAIN=1
for a in "$@"; do
  case "$a" in
    --branch-only) MAIN=0 ;;
    --tests) TESTS=1 ;;
    --all-tests) TESTS=2 ;;
    --final) TESTS=3 ;;
    -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
    *) echo "end-session: unknown option « $a »" >&2; exit 2 ;;
  esac
done

branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "HEAD" ] && { echo "end-session: detached HEAD — nothing to push" >&2; exit 1; }
faults=0
fault() { printf 'FAULT — %s\n' "$*"; faults=$((faults + 1)); }

# 1 · The tree is clean: what must leave is committed, by name.
dirty=$(git status --porcelain)
if [ -n "$dirty" ]; then
  fault "changes are not committed — commit them by name (never git add -A):"
  printf '%s\n' "$dirty" | sed 's/^/    /' | head -20
fi

# 2 · The memory holds.
#     The push gate's checks too (the story checker among them): this script's own push is one the
#     gate never sees (.claude/hooks/pre_push_checks.py --all).
python3 -I .claude/hooks/pre_push_checks.py --all || faults=$((faults + 1))

# 3 · A state was rewritten on this branch (once is enough, not at every commit). With main moving at
#     every save point, the base is the last save point: a reminder, not a fault (as in `french`).
git fetch -q origin main 2>/dev/null || echo "WARNING — cannot fetch main; comparing with the local copy"
base=$(git merge-base HEAD origin/main 2>/dev/null || true)
if [ "$branch" != "main" ] && [ -n "$base" ] && [ "$(git rev-list --count "$base..HEAD")" -gt 0 ]; then
  if [ -z "$(git diff --name-only "$base" HEAD -- .story/state/)" ]; then
    echo "REMINDER — no state in .story/state/ rewritten since the last save point: if your next step changed, rewrite it"
  fi
fi

# 4 · The engine tests, when asked. Only those the unpushed changes can break (--tests), or all of
#     them (--all-tests, several minutes: the audio probes). CI runs only by hand now: --all-tests is where the settled probes run.
#     Part P2 (2026-11-10): a save point (--tests) runs the quick tier, a fixed subset of the slowest probes' cases
#     (core/src/test/.../Tier.kt); the session's last save (--final) runs the full tier over every change since the
#     last full pass in this clone, so nothing quick-tested leaves the session untested in full.
#     A pass is remembered for the code the tests read (the tree of core/, app/, pipeline/, the build files and
#     the docs tests read), so a second end-session on the same code (after a conflict in .story/ or docs, a
#     refused push, a message added) does not re-run 15 minutes of tests (orchestrator 5, 2026-11-10).
#     A line of .git/harmonize-tested: "<scope> <tier> <code> <base>": scope changed|final|all, tier quick|full, the
#     code's hash, and the commit the change was measured from (HEAD for --all).
if [ "$TESTS" -gt 0 ]; then
  stamp_file="$(git rev-parse --git-dir)/harmonize-tested"
  code=$(git ls-tree -r HEAD -- core app pipeline gradle build.gradle.kts settings.gradle.kts gradle.properties \
           CLAUDE.md docs/NUMBERS.md docs/CURRICULUM.md docs/curriculum 2>/dev/null | git hash-object --stdin)
  case "$TESTS" in
    1) scope=changed; tier=quick; args=() ;;
    2) scope=all; tier=full; args=(--all) ;;
    3) scope=final; tier=full; args=(--full)
       # From the base of the first quick pass after the last full one (lines of the old format, before P2, have
       # no tier: read as quick, base unknown, so the whole suite runs).
       since=$(awk 'NF>=2 { if ($2=="full") { b=""; n=0 } else if (n==0) { b=(NF>=4 ? $4 : "?"); n=1 } } END { print b }' "$stamp_file" 2>/dev/null)
       if [ "$since" = "?" ] || { [ -n "$since" ] && ! git merge-base --is-ancestor "$since" HEAD 2>/dev/null; }; then
         echo "end-session: a quick pass since the last full one has no usable base; the whole suite runs, full."
         args=(--all)
       elif [ -n "$since" ]; then args+=(--since "$since")
       fi ;;
  esac
  # The commit the change is measured from, as test-changed.sh finds it.
  if [ "$scope" = all ]; then base=$(git rev-parse HEAD)
  elif [ -n "${since:-}" ] && [ "$since" != "?" ]; then base=$since
  else base=$(git rev-parse -q --verify '@{upstream}' 2>/dev/null || git rev-parse -q --verify "origin/$branch" 2>/dev/null || git merge-base HEAD origin/main)
  fi
  # A pass already recorded on this exact code covers this one if its tier is at least as full and its scope as wide.
  covered=$(awk -v code="$code" -v scope="$scope" -v tier="$tier" '
    function rank(s) { return s=="all" ? 3 : (s=="final" ? 2 : 1) }
    { t = (NF>=3 ? $2 : "quick"); c = (NF>=3 ? $3 : $2)
      if (c==code && (t=="full" || tier=="quick") && rank($1) >= rank(scope)) ok=1 }
    END { print ok+0 }' "$stamp_file" 2>/dev/null)
  if [ -z "$(git status --porcelain)" ] && [ "$covered" = 1 ]; then
    echo "end-session: these tests ($scope, $tier) already passed on this exact code ($code); not re-run."
  elif scripts/test-changed.sh "${args[@]}"; then
    echo "$scope $tier $code $base" >> "$stamp_file"
  else
    fault "engine tests failed ($tier tier)"
  fi
fi

[ "$faults" -gt 0 ] && { echo "end-session: $faults fault(s), nothing is pushed."; exit 1; }

# 5 · Push (never forced). The branch first, unless it is main itself.
#     A network failure is retried at 2, 4, 8, 16 s.
push() {
  for i in 1 2 3 4 5; do
    if git push "$@" 2>&1 | tail -2; [ "${PIPESTATUS[0]}" = 0 ]; then return 0; fi
    [ "$i" -lt 5 ] && sleep $((2 ** i))
  done
  return 1
}
if [ "$branch" != "main" ]; then
  push -u origin "$branch" || { fault "pushing « $branch » failed"; exit 1; }
  echo "end-session: « $branch » pushed ($(git rev-parse --short HEAD))."
  [ "$MAIN" = 0 ] && exit 0
fi

# 6 · Bring the work onto main, by fast-forward only. Many sessions push main: when another one moved it
#     (during a long test run, or in the same second), fetch, merge it (a conflict stops here), re-run
#     the quick checks on the merge, and try again, up to 8 times, with no session's turn spent on it
#     (orchestrator 5, 2026-11-10: sessions were retrying by hand). The merged commits were tested by
#     the session that pushed them; the engine tests are not re-run for a clean merge.
for attempt in 1 2 3 4 5 6 7 8; do
  git fetch -q origin main || { fault "cannot fetch main; main is not moved"; exit 1; }
  if ! git merge-base --is-ancestor origin/main HEAD; then
    behind=$(git rev-list --count HEAD..origin/main)
    if ! git merge -q --no-edit origin/main >/dev/null 2>&1; then
      conflicts=$(git diff --name-only --diff-filter=U | tr '\n' ' ')
      git merge --abort 2>/dev/null
      fault "main moved $behind commit(s) ahead and merging it conflicts in: $conflicts— git merge origin/main, resolve, commit, run again"
      exit 1
    fi
    echo "end-session: origin/main merged ($behind commit(s) from another session)."
    python3 -I .claude/hooks/pre_push_checks.py --all || { fault "the quick checks fail after merging main; fix, commit, run again"; exit 1; }
  fi
  if git push -q origin "HEAD:main" 2>/dev/null; then
    [ "$branch" != "main" ] && push origin "$branch" >/dev/null
    [ "$branch" = "main" ] && git branch -q --set-upstream-to=origin/main 2>/dev/null
    echo "end-session: main is at $(git rev-parse --short HEAD)."
    exit 0
  fi
  echo "end-session: main moved while pushing (attempt $attempt); merging it and trying again."
  sleep $((attempt + RANDOM % 4))
done
fault "main kept moving: 8 attempts; run again"
exit 1
