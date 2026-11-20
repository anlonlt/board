# board

A workflow kit for working with Claude Code: git as the one store, a board (a claude.ai page) as its face, a clerk that
carries your comments to the session that wrote the post, a lookup reader (`kit.py`), a memory (`.story/`) and a few
hooks. Its design and measured inventory come from the Harmonize project (`anlonlt/harmonize`, `docs/WORKFLOW-KIT.md`).

## Why (read this first; the detail, with every quote and number, is `docs/KIT-GOALS.md`)

The kit answers problems people actually had. Install a part only where its problem is present:

- **Claude forgets between sessions or chats** ("Pas de mémoire entre conversations"; "comme la dernière fois"; next
  steps and things waiting on the owner scattered across hand-over docs). → **the memory** (`.story/`: each
  workstream's next step, one list of what waits on the owner, printed at every start) **and the import** that fills
  it. Worth it for everyone: install it first.
- **Work gets lost or broken by a session** ("The previous source of this app was lost to an agent deleting it";
  unpushed work in a reclaimed container; "Why are there branches ? Shouldn't everything be merged to main ?").
  → **the hooks** (guard, stop, compact) and `end_session.py`. Worth it for everyone.
- **Several sessions at once, and the owner answers later or from his phone** ("triple tokens, replying in chat, to
  you, and in the board"; "an at a glance card"). → **the board** (one post per result, one-tap answers) and **the
  clerk** (carries each comment to the session that wrote the post). Measured in Harmonize: his median wait 0.8 min;
  tokens about even with chat, not fewer. **Not worth it for one session at a time** with the owner in its chat: the
  chat already is the channel (issue #3: a first reply in 57 s).
- **More parallel work than the owner can launch and check by hand** ("to optimize my use of it in parallel"; "If it
  notifies such things, it should do it itself"). → **the orchestrator**, its cards and its consultant. The most
  expensive part (one orchestrator: $24.88 in 5 h): only for parallel work.
- **Work outside code** (Microsoft 365 through Claude in Chrome). → **runners** (§ Runners).

Step 0 measures which of these the project has, and recommends; the install takes only what it recommends.

## Install (`agy` on a computer, any repository)

Open an `agy` session in **your** repository on this computer and paste this one line:

> Install the board kit: read README.md § "Install" and do every step for this repository.

The hooks' "end your turn with ·" reminder is about chat turns: your step reports go in the post.
Steps marked **YOU** need a person; `agy` says each one, with its local URL, when it gets there. Everything else is `agy`'s.

**How `agy` on a computer replaces Claude Code on the web (same functionality, zero external dependencies):**
- **Hooks & Skills:** installed into `.agents/hooks.json` (`kit-board` named hook), `.agents/hooks/kit/*.py` (speaking `agy`'s camelCase JSON stdin/stdout contract), and `.agents/skills/{story,board}/SKILL.md`.
- **Board UI & Data Store:** `.kit/board_server.py` serves `.kit/board.html` locally (default `http://127.0.0.1:8765`, or LAN/Tailscale via `--host 0.0.0.0`) with an injected `window.claude` shim (`/_shim.js`) backed by SQLite (`.kit/board_data/board.db`, gitignored) + Server-Sent Events (`/_api/events`) + local blob storage (`/_blob/<id>`).
- **Board Tools:** `.kit/board_cli.py` replaces `ArtifactData`, `ArtifactComments`, and `Artifact` (`get`/`list`/`query` with `--out-dir`, `set`/`update`/`str-replace` with `--if-version`, `batch`, `comments watch/wait/read/reply`, `asset upload/read`).
- **Multi-Session Orchestration & Timers:** `invoke_subagent` (with `Workspace: "share"` for parallel worktrees), `define_subagent` (`kit-cards`), `send_message`, `manage_subagents`, `schedule` (`DurationSeconds` / `CronExpression`), and `run_command(IsDaemon=true)`.

**For `agy`, in the project's session.** Do the steps in order; report each step's outcome in one line.

0. **Step 0 first, always:** run the survey (`step0/survey-code.md` or `step0/survey-chat.md`). Show its report to the owner; his yes or changes decide the parts. `install.py` refuses a first install without `--survey <path>` (or `--no-survey "<why>"`).
1. **Get the kit:** `git clone --depth 1 -b agy-local https://github.com/anlonlt/board /tmp/board-kit` (or use your local checkout).
2. **Install it** into the project root: `python3 -I /tmp/board-kit/install.py . --project "<the project's name>" --talk "<language>" --survey survey/<date>/report.md` plus optional `--board-port 8765` and the parts step 0 recommended (`--no-board`, `--no-clerk`, `--no-orchestrator`).
   It never deletes and never replaces a file it did not write: it lists what it wrote, and `KEPT` for what it left alone.
   Your `.agents/hooks.json` keeps every other named hook; `kit-board` is merged in.
   Check: `python3 -I .agents/hooks/kit/test_hooks.py` ends `0 failed`.
   **Ask the owner once:** "Every session ends by merging its work onto `<main branch>` and pushing it, never forced (a conflict stops it). Do you give that standing permission?" Yes: rerun with `--trunk-push yes`. No: sessions push only their own branch. Either way each session ends with `python3 -I .kit/end_session.py`.
3. **Translate (if non-English), then start the local board server** (with a board only):
   - If `--talk` is not English: `python3 -I .kit/strings.py template .kit/strings.json`, fill in translations, rerun step 2 with no options.
   - Start the local board server as a background daemon (`run_command` with `CommandLine: "python3 -I .kit/board_server.py"`, `IsDaemon: true`, `WaitMsBeforeAsync: 1000`). Verify `curl -sf http://127.0.0.1:8765/_api/health`.
   - Commit `.kit .agents .story .gitignore` and push.
4. **Post your first two posts** in one `board_cli.py batch` (`.kit/BOARD.md` § Post; `created_at` from `date -u +%Y-%m-%dT%H:%M:%SZ`):
   `_orchestrator` (`{title: "Ask the orchestrator", column: "info", part: "orchestrator", priority: 3, created_at, body: "Write here for anything that is not about one post."}`) and
   `KIT-installed` (`{title: "The board kit is installed", column: "info", part: "KIT", priority: 2, session_id: "<your conversationId>", created_at, body: "<what step 2 wrote and kept>"}`).
5. **Start the orchestrator and the clerk** (only those installed):
   - Launch the clerk via `invoke_subagent(TypeName="self", Role="Board Clerk", Model="flash_lite", Workspace="inherit", Prompt=<text of .kit/clerk.md>)`. It registers `comments watch --on`, listens in the background with `board_cli.py comments wait`, and notifies the orchestrator.
   - Run the orchestrator (in this session or via `invoke_subagent`) with `.kit/orchestrator.md`: it writes its conversation ID into `_orchestrator`, builds "Where we are" via `kit-cards`, and posts its welcome under `_orchestrator`.
6. **YOU, the first comment:** open the board's URL (e.g. `http://127.0.0.1:8765`) in your browser, open `KIT-installed`, write "test" in its comment box and send it. The clerk wakes immediately, passes it to this session via `send_message`, marks it "✓ Seen" and replies "Passed to …". Answer it under the post (`board_cli.py set comments ...`).
7. **The board's copy in git** (`.kit/ORCHESTRATOR.md` § 3):
   `python3 -I .kit/kit.py init`; `T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)`; `python3 -I .kit/board_cli.py list posts --limit 1000 --out-dir /tmp/kd`; `python3 -I .kit/board_cli.py list comments --limit 1000 --out-dir /tmp/kd`; `python3 -I .kit/kit.py sync /tmp/kd --at "$T0" --full --commit`; `python3 -I .kit/kit.py post KIT-installed`; `python3 -I .kit/kit.py push`.
8. **Report**, then end the setup: `python3 -I /tmp/board-kit/install.py . --setup-done`, commit, push. Update `KIT-installed` (`board_cli.py update`) with each step's outcome.
9. **Tell the person what now**, in their language (`language.talk`): their local board URL (`http://127.0.0.1:8765`), what runners are ready (`computer` is ready immediately on this machine), and how to import existing state.

## Import (your project's history into the kit)

The kit's memory and board start empty. **A Claude Code project** already holds its state in its repository and its
sessions' transcripts: `step0/import-code.md` (step 0's last part; no chat export; `--no-posts` without a board).
**Chats** hold the state of a chat-only user's projects. Two steps:
1. **In a new claude.ai chat**, paste the block of `step0/export-chat.md`. Claude goes through your chats, fills one
   private artifact "kit state" (per project: next step, where it stands, decisions, ids, what waits for you), shows it
   to you for corrections, and gives you one line for Claude Code.
2. **For Claude, in the project's session** (after Install): `Artifact` `read` the link (it saves the page as a file);
   `python3 -I .kit/kit_import.py <that file> --session <the
   orchestrator's session id>`; it writes `.story/state/<project>.md`, `.story/awaits.md`, `.story/preferences.md`
   (never over a file it did not create) and prints an `ArtifactData` `batch` of posts: one per project (`info`) and one
   one-tap `question` per thing waiting for you. Send it to the board (50 writes per call), commit `.story`, push.

## Update (a newer kit into a project that has it)

For Claude, in the project's session: **get the kit from `anlonlt/board` itself, never from a fork** (a fork, like the
one a survey's pull request came from, never updates by itself): `git -C /tmp/board-kit pull` (or step 1's clone), and
check `git -C /tmp/board-kit log -1 --format=%h` equals the first word of `git ls-remote
https://github.com/anlonlt/board main`; `.kit/manifest.json` `_kit_commit` says which kit the project has now. Then
step 2's command again
with no options (`python3 -I /tmp/board-kit/install.py .`): it keeps `.kit/config.json`, updates only the files the kit
wrote and nobody changed since (`KEPT` lists the others), and merges any new hook line. If `.kit/board.html` changed (a translated page shows as
`KEPT`: redo the translation on the new `.kit/board.html` from `/tmp/board-kit`, the same way as step 3), republish it to the board's url (`Artifact` `publish` with `url`, no `capabilities`: the page keeps its own). Commit,
push. If the running clerk or orchestrator was started at another level than `kit.roles` says (e.g. a clerk on
medium effort), start a new one at the right level with the same prompt and stop the old one. The guard keeps refusing `git reset --hard`, `git clean` and force pushes on purpose: undo with `git revert`.

## Runners: where the work can happen (any setup)

The kit does not assume where work runs. Step 0 and the export list the person's **runners**, each of one kind; each
project says which runner it needs (`Runs in:` in its state file); the orchestrator sends each step to that runner.

| kind | what it reaches | how a step gets there | one-time setup (the orchestrator's welcome says which are done) |
|---|---|---|---|
| `cloud` | the repository, the web, the connectors and keys of the cloud environment | the orchestrator starts a session (`create_session`) | none beyond Install; connectors: claude.ai Settings → Connectors; an API key: the environment's secrets |
| `computer` | the person's computer: its files, apps, Claude in Chrome in their browser, local servers, a VPN or intranet | a card with `runner: "<name>"`; the runner on that computer pulls it | the Claude desktop app (or Claude Code) on that computer, the repository cloned there, the tools it needs on (e.g. the Claude in Chrome extension, signed in to the right profiles); then paste `.kit/runner.md` with its name, and `/loop 10m` |
| `chat` | a claude.ai chat: its connectors (Microsoft 365, Google, Slack…), Projects, Claude in Chrome | a card with `runner: "<name>"`; the person pastes it into a chat (a chat cannot pull) | the connectors in claude.ai Settings → Connectors; the right Project open |
| `owner` | what only the person can do: their phone, their ears, a signature, money, a login | a `todo` or `question` card for them | none |
| `scheduled` | a cloud session started on a schedule (a weekly draft, a nightly check) | the orchestrator creates a scheduled task | none (the orchestrator says what it scheduled) |

A runner of kind `computer` can also be reached from a cloud session when that computer is attached to it (the Claude
desktop app → Remote Control); the orchestrator tests that once before relying on it.

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

Tests of the kit itself: `python3 -I test_install.py`, `python3 -I test_install_bd11.py`, `python3 -I kit/board/test_strings.py`, `python3 -I kit/import/test_kit_import.py`, `python3 -I kit/board/test_kit_cards.py`, `python3 -I kit/test_end_session.py`, `python3 -I kit/hooks/test_hooks.py`,
`python3 -I kit/clerk/test_clerk_pending.py`, `python3 -I scripts/test_kit.py` (+ `scripts/kit_mutations.py`).

## Step 0: the survey (Install step 0)

Before anything is installed, the kit surveys how a person works with Claude today, then recommends which parts to
install, each with the goal it serves (`docs/KIT-GOALS.md` § 3).

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
