# board

A workflow kit for working with Claude Code: git as the one store, a board (a claude.ai page) as its face, a clerk that
carries your comments to the session that wrote the post, a lookup reader (`kit.py`), a memory (`.story/`) and a few
hooks. Its design and measured inventory come from the Harmonize project (`anlonlt/harmonize`, `docs/WORKFLOW-KIT.md`).

## Install (Claude Code on the web, any repository)

Open a Claude Code session on **your** repository (https://claude.ai/code) and paste this one line:

> Install the board kit: read https://github.com/anlonlt/board/blob/main/README.md § "Install" and do every step for this repository.

The hooks' "end your turn with ·" reminder is about chat turns: your step reports go in the post.
Steps marked **YOU** need a person (no agent can start a comment on a page: only a person's tap does); Claude says each one, with its link, when it gets there. Everything else is Claude's.

**Before installing, two checks** (from the first outside survey, PR #1):
- **Private by default.** The kit keeps the project's state, posts and comments in the project's OWN repository
  (`.story/`, branch `board-mirror`), never in `anlonlt/board`. If the project touches people's data (a school, clients),
  that repository must be private. The board page itself is private to its owner until shared.
- **Work that is not code.** If the work lives elsewhere (Microsoft 365, a website driven through the browser
  extension), the repository holds the work's STATE (decisions, ids, next steps, what waits for approval), not the
  work. Check first that the tools the work needs answer from a Claude Code session (its connectors; the browser
  extension only reaches a computer attached to the session): if they do not, the kit gives memory and the board,
  and the actions stay in chat.

**For Claude, in the project's session.** Do the steps in order; report each step's outcome in one line.

1. **Get the kit** (public repository, no token): `git clone --depth 1 https://github.com/anlonlt/board /tmp/board-kit`
   (if `/tmp/board-kit` exists, `git -C /tmp/board-kit pull`).
2. **Install it** into the project root: `python3 -I /tmp/board-kit/install.py . --project "<the project's name>"
   --talk "<the language the owner writes to you in>"` (add `--main-branch <name>` when your sessions start from another branch than `main`: the branch the clerk will read; usually GitHub's default).
   It never deletes and never replaces a file it did not write: it lists what it wrote, and `KEPT` for what it left alone.
   Your `.claude/settings.json` keeps every line it had (re-indented); the kit's hook lines are merged in.
   Check: `python3 -I .claude/hooks/kit/test_hooks.py` ends `0 failed`.
3. **Translate, then publish the board.** If the owner's language (`--talk`) is not English, translate first
   every text the person SEES in `.kit/board.html` (headings, buttons, labels, placeholders, `aria-label`s, the status
   and empty-state lines, the strings the script shows), with Edit, never touching ids, class names, field names,
   `"owner"`/`"viewer"`, `Answer: `, or code. Then open the page once after publishing and fix any line left in English.
   **Publish** (the Artifact tool asks you to `Read` the whole 145 KB file first, and to load the skill `artifact-capabilities` before passing `capabilities`: both are expected): `Artifact` `publish`, `file_path` `.kit/board.html`, `icon` `board`, `capabilities`
   `{"assets": {}, "comments": {}, "db": {}, "user": {}, "downloads": true}`. Then rerun step 2's command with
   `--board-url <the url it returned>` (the url goes into the clerk's prompt and the rules), commit
   `.kit .claude .story .gitignore` and push.
4. **Post your first two posts** in one `ArtifactData` `batch` (`.kit/BOARD.md` § Post; `created_at` from `date -u +%Y-%m-%dT%H:%M:%SZ`):
   `_orchestrator`: `{title: "Orchestrator", column: "info", part: "orchestrator", priority: 3, session_id, created_at,
   body: "Orchestrator: <what runs>"}`, where `session_id` is the id of the session the owner talks to (`get_session` with
   no id; from a subagent, it is the parent's): the clerk sends there. Then `KIT-installed`: `{title: "The board kit is
   installed", column: "info", part: "KIT", priority: 2, session_id, created_at, body: <what step 2 wrote and kept>}`.
5. **Start the clerk:** `create_session` with `source_url` the repository's GitHub url (`source_revision` the branch
   of step 2), `prompt` the whole text of `.kit/clerk.md` pasted inline (Read it first; about 5 KB), a small model if one is offered, `title` "board clerk". It
   answers "board clerk ready, watching" to this session. If `create_session` is not available here, tell the owner:
   **YOU:** open https://claude.ai/code, start a session on this repository and paste the content of `.kit/clerk.md`.
6. **YOU, the first comment:** open the board's url, open the post `KIT-installed`, write "test" in its comment box
   and send it. The clerk passes it to this session ("Board comment from the owner on your post KIT-installed …"),
   marks it "✓ Seen" and replies "Passed to …" in the thread. Answer it under the post (`.kit/BOARD.md` § Comments).
7. **The board's copy in git** (`.kit/ORCHESTRATOR.md` § 3; run it now, and again after step 6's comment):
   `python3 -I .kit/kit.py init` (a worktree NEXT TO the project, `../<project>-board-mirror`, on a new branch
   `board-mirror` that is never merged into main); `date -u +%Y-%m-%dT%H:%M:%SZ` (T0); `ArtifactData` `list` `posts` and
   `list` `comments` (`query.limit` 1000) with `out_dir` `/tmp/kd` (empty; short paths cost fewer tokens);
   `python3 -I .kit/kit.py sync /tmp/kd --at <T0> --full --commit`; `python3 -I .kit/kit.py open` lists comments of the owner's still unanswered,
   `kit.py post KIT-installed` shows the post and its comments; `kit.py push` pushes that branch (`board-mirror`) to your repository: it is the only branch the kit creates.
8. **Report** in the body of `KIT-installed` (`update`): each step's outcome, every step that needed the owner, anything that failed with
   its exact message. From now on every session posts there at turn 1 (the SessionStart hook says so).
9. **Tell the person what now**, as your last chat message, in their language (`.kit/config.json` `language.talk`):
   (1) the connector test: "ask me to read one of your Microsoft 365 / Google / other connector files here; if it
   answers, your work can move here"; (2) the Import: "open a new claude.ai chat and paste the block at
   https://github.com/anlonlt/board/blob/main/step0/export-chat.md , then paste back here the line it gives you";
   (3) where the board is: its link.

## Import (your claude.ai chats into the kit)

Your chats hold your projects' state; the kit's memory and board start empty. Two steps:
1. **In a new claude.ai chat**, paste the block of `step0/export-chat.md`. Claude goes through your chats, fills one
   private artifact "kit state" (per project: next step, where it stands, decisions, ids, what waits for you), shows it
   to you for corrections, and gives you one line for Claude Code.
2. **For Claude, in the project's session** (after Install): `Artifact` `read` the link (it saves the page as a file);
   `python3 -I .kit/kit_import.py <that file> --session <the
   orchestrator's session id>`; it writes `.story/state/<project>.md`, `.story/awaits.md`, `.story/preferences.md`
   (never over a file it did not create) and prints an `ArtifactData` `batch` of posts: one per project (`info`) and one
   one-tap `question` per thing waiting for you. Send it to the board (50 writes per call), commit `.story`, push.

## Update (a newer kit into a project that has it)

For Claude, in the project's session: `git -C /tmp/board-kit pull` (or step 1's clone), then step 2's command again
with no options (`python3 -I /tmp/board-kit/install.py .`): it keeps `.kit/config.json`, updates only the files the kit
wrote and nobody changed since (`KEPT` lists the others), and merges any new hook line. If `.kit/board.html` changed (a translated page shows as
`KEPT`: redo the translation on the new `.kit/board.html` from `/tmp/board-kit`, the same way as step 3), republish it to the board's url (`Artifact` `publish` with `url`, no `capabilities`: the page keeps its own). Commit,
push. The guard keeps refusing `git reset --hard`, `git clean` and force pushes on purpose: undo with `git revert`.

## Feedback (a complaint or a dev report, to anlonlt/board)

The project and its board stay private; only the report goes to the public kit repository.
- Write it as `survey/<YYYY-MM-DD>/<short-slug>.md` (what happened, the exact errors, what you expected; no personal
  data, no names of people or organisations).
- If this session can push to `anlonlt/board` (its owner added you as a collaborator): clone it, branch
  `feedback/<date>-<slug>`, add only that file, push, open a pull request into `main`.
- Otherwise: tell the person to open https://github.com/anlonlt/board/issues/new and paste the report (or attach the
  file). A session cannot write to a repository it was not given (PR #1's 403).

**What you get:** the page (posts in columns, comments, one-tap answers, pictures, typing dots, export to Markdown);
the clerk; `.kit/kit.py` (`post`, `open`, `since`, `session` in hundreds of characters instead of whole dumps);
the memory `.story/` and skills `story`, `board`; hooks: `kit_guard` (refuses `rm -r` outside /tmp, `git clean`,
`reset --hard`, force pushes, adding secrets), `kit_session_start` (memory and board rule at each start),
`kit_stop` (no stop with unpushed work, once), `kit_pre_compact`, `kit_say_it_once` (message limits), `kit_board_typing`.
Every limit and name is in `.kit/config.json`. The orchestrator's guide: `.kit/ORCHESTRATOR.md`.

Tests of the kit itself: `python3 -I test_install.py`, `python3 -I kit/import/test_kit_import.py`, `python3 -I kit/hooks/test_hooks.py`,
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
