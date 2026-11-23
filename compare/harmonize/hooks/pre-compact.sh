#!/bin/bash
#
# BEFORE A COMPACTION — put on DISK what the harness is about to lose.
#
# In the owner's Aplomb project, a compaction restarted the container: background jobs died with their
# task ids, and the next turn waited for notifications that never came. This hook writes the instant
# before: the branch, the commits since main, what is being written, the long jobs alive (Gradle test
# runs, the songs pipeline). The file is gitignored: it describes an instant, not a history.
# Adapted on 2026-11-08 from Aplomb's .claude/hooks/pre-compact.sh.

set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -d .story ] || exit 0
out=.story/before-compaction.md

{
  echo "# State before a compaction"
  echo
  echo "Written by \`.claude/hooks/pre-compact.sh\`, overwritten at each compaction."
  echo
  echo "- branch: \`$(git rev-parse --abbrev-ref HEAD 2>/dev/null)\`"
  base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null)
  [ -n "${base:-}" ] && echo "- commits since main: $(git rev-list --count "$base..HEAD" 2>/dev/null)"
  echo
  echo "## Being written (not committed)"
  echo '```'
  git status --porcelain 2>/dev/null | head -20
  echo '```'
  echo
  echo "## Long jobs alive at the compaction"
  echo '```'
  # The harness's own launcher carries a huge command line; it is excluded, and the rest is bounded.
  pgrep -af "GradleWrapperMain|GradleDaemon|gradle .*test|process_song\.py|demucs|melody\.py" 2>/dev/null \
    | grep -v -- "--sdk-url" | cut -c1-120 | head -10 | grep . || echo "(none)"
  echo '```'
  echo
  echo "**They will not survive** if the container restarts: rerun rather than wait for a notification."
} > "$out"
exit 0
