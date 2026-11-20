# board

A workflow kit for working with Claude Code: git as the one store, a board (a claude.ai page) as its face, a clerk that
carries your comments to the session that wrote the post, a lookup reader (`kit.py`), a memory (`.story/`) and a few
hooks. Its design and measured inventory come from the Harmonize project (`anlonlt/harmonize`, `docs/WORKFLOW-KIT.md`).

## Install (Claude Code on the web, any repository)

Open a Claude Code session on **your** repository (https://claude.ai/code) and paste this one line:

> Install the board kit: read https://github.com/anlonlt/board/blob/main/README.md § "Install" and do every step for this repository.

Steps marked **YOU** need a person; Claude says each one, with its link, when it gets there. Everything else is Claude's.

**For Claude, in the project's session.** Do the steps in order; report each step's outcome in one line.

1. **Get the kit** (public repository, no token): `git clone --depth 1 https://github.com/anlonlt/board /tmp/board-kit`
   (if `/tmp/board-kit` exists, `git -C /tmp/board-kit pull`).
2. **Install it** into the project root: `python3 -I /tmp/board-kit/install.py . --project "<the project's name>"
   --talk "<the language the owner writes to you in>"` (add `--main-branch <name>` if the default branch is not `main`).
   It never deletes and never replaces a file it did not write: it lists what it wrote, and `KEPT` for what it left alone.
   Your `.claude/settings.json` keeps every line it had; the kit's hook lines are merged in.
   Check: `python3 -I .claude/hooks/kit/test_hooks.py` ends `0 failed`.
3. **Publish the board:** `Artifact` `publish`, `file_path` `.kit/board.html`, `icon` `board`, `capabilities`
   `{"assets": {}, "comments": {}, "db": {}, "user": {}, "downloads": true}`. Then rerun step 2's command with
   `--board-url <the url it returned>` (the url goes into the clerk's prompt and the rules), commit
   `.kit .claude .story .gitignore` and push.
4. **Post your first two posts** (`.kit/BOARD.md` § Post): `_orchestrator` with `session_id` = this session's id
   (`get_session` with no id) and the body "Orchestrator: <what runs>"; then `KIT-installed`, column `info`: what step 2
   wrote and kept, and the board's url.
5. **Start the clerk:** `create_session` with `source_url` the repository's GitHub url (`source_revision` its main
   branch), `prompt` the whole text of `.kit/clerk.md`, a small model if one is offered, `title` "board clerk". It
   answers "board clerk ready, watching" to this session. If `create_session` is not available here, tell the owner:
   **YOU:** open https://claude.ai/code, start a session on this repository and paste the content of `.kit/clerk.md`.
6. **YOU, the first comment:** open the board's url, open the post `KIT-installed`, write "test" in its comment box
   and send it. The clerk passes it to this session ("Board comment from the owner on your post KIT-installed …"),
   marks it "✓ Seen" and replies "Passed to …" in the thread. Answer it under the post (`.kit/BOARD.md` § Comments).
7. **The board's copy in git** (`.kit/ORCHESTRATOR.md` § 3): `python3 -I .kit/kit.py init`; `ArtifactData` `list`
   `posts` and `list` `comments` (`query.limit` 1000) with `out_dir` D; `python3 -I .kit/kit.py sync D --at <UTC time
   before the reads> --full --commit`; `python3 -I .kit/kit.py open` lists comments of the owner's still unanswered,
   `kit.py post KIT-installed` shows the post and its comments; `kit.py push` keeps the copy on its own branch.
8. **Report** under `KIT-installed`: each step's outcome, every step that needed the owner, anything that failed with
   its exact message. From now on every session posts there at turn 1 (the SessionStart hook says so).

**What you get:** the page (posts in columns, comments, one-tap answers, pictures, typing dots, export to Markdown);
the clerk; `.kit/kit.py` (`post`, `open`, `since`, `session` in hundreds of characters instead of whole dumps);
the memory `.story/` and skills `story`, `board`; hooks: `kit_guard` (refuses `rm -r` outside /tmp, `git clean`,
`reset --hard`, force pushes, adding secrets), `kit_session_start` (memory and board rule at each start),
`kit_stop` (no stop with unpushed work, once), `kit_pre_compact`, `kit_say_it_once` (message limits), `kit_board_typing`.
Every limit and name is in `.kit/config.json`. The orchestrator's guide: `.kit/ORCHESTRATOR.md`.

Tests of the kit itself: `python3 -I test_install.py`, `python3 -I kit/hooks/test_hooks.py`,
`python3 -I kit/clerk/test_clerk_pending.py`, `python3 -I scripts/test_kit.py` (+ `scripts/kit_mutations.py`).

## Step 0: the survey

Before anything is installed, the kit surveys how a person works with Claude today, then adapts to it.

- **Chat-only users** (Claude on claude.ai): `step0/survey-chat.md`. Paste its block into a new chat; Claude reads
  your past chats itself, guides you through the GitHub set-up with direct links, and ends with a pull request to
  this repository, opened from Claude Code, that proves the set-up works and carries the survey and its dev report
  (`survey/<date>/`).
- **Claude Code users**: `step0/survey-code.md`. Claude measures instead of asking: its sessions' token records, the
  repository, a board dump, with the scripts below; it opens its own review session; it ends in the same pull request.

## Scripts (`scripts/`, Python 3, no dependencies; names of a project's fields and files in `config/<project>.json`)

| script | what it says |
|---|---|
| `what_fills.py PAGES [--config]` | what fills a coordinating session's context, by source, from its transcript pages; tokens from `usage` |
| `mirror_holes.py DUMP [--config]` | what a one-way copy of a board would miss (typed stamps, fields written later) |
| `token_probe.py TRANSCRIPT` | tokens one read costs, from a probe subagent's own usage |
| `kit.py [--config] init/sync/diff/post/open/since/session/reads` | a git copy of the board on branch `board-mirror`, its gate (`diff` against a full dump) and lookups |

Tests: `python3 -I scripts/test_kit.py`, `python3 -I scripts/test_survey_scripts.py`; `scripts/kit_mutations.py`
breaks `kit.py` 20 ways, each must turn a test red. `config/harmonize.json` reproduces Harmonize's measured numbers
exactly; `config/example.json` is the shape for a new project.

Each survey's pull request is reviewed and its dev report used to improve the step.
