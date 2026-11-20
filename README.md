# board

A workflow kit for working with Claude: git as the one store, a board as its face, a lookup reader, a few hooks.
Its design and measured inventory come from the Harmonize project (`anlonlt/harmonize`, `docs/WORKFLOW-KIT.md`).

## Step 0: the survey

Before anything is installed, the kit surveys how a person works with Claude today, then adapts to it.

- **Chat-only users** (Claude on claude.ai): `step0/survey-chat.md`. Paste its block into a new chat; Claude reads
  your past chats itself, guides you through the GitHub set-up with direct links, and ends with a pull request to
  this repository, opened from Claude Code, that proves the set-up works and carries the survey and its dev report
  (`survey/<date>/`).
- **Claude Code users**: `docs/WORKFLOW-KIT.md` § 0 in `anlonlt/harmonize` (scripts on session records); not moved yet.

Each survey's pull request is reviewed and its dev report used to improve the step.
