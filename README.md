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

0. **Step 0 first, always** (whatever the project's state: new, mid-way, full of docs): run the survey,
   `step0/survey-code.md` (a Claude Code project) or `step0/survey-chat.md` (chats only), from the kit's clone of step 1.
   Its first question is the scope (one board for all projects, or this one); its report ends with a recommendation
   per part, each with the goal it serves (`docs/KIT-GOALS.md`). Show it to the owner; his yes or changes decide the
   parts. `install.py` refuses a first install without the report.
1. **Get the kit** (public repository, no token; always a fresh `git clone`/`git pull`, never a cached `raw.githubusercontent.com/.../main/...` link: it can serve an older copy for minutes): `git clone --depth 1 https://github.com/anlonlt/board /tmp/board-kit`
   (if `/tmp/board-kit` exists, `git -C /tmp/board-kit pull`).
2. **Install it** into the project root: `python3 -I /tmp/board-kit/install.py . --project "<the project's name>"
   --talk "<the language the owner writes to you in>" --survey survey/<date>/report.md` plus the parts step 0
   recommended: `--no-board` (memory and hooks only; then skip to the Import and step 8), `--no-clerk`,
   `--no-orchestrator` (nothing: all). The trunk is origin's default branch (`git ls-remote --symref origin HEAD`);
   `--main-branch <name>` names another, and a branch missing on origin stops the install.
   It never deletes and never replaces a file it did not write: it lists what it wrote, and `KEPT` for what it left alone.
   Your `.claude/settings.json` keeps every line it had (re-indented); the kit's hook lines are merged in.
   Check: `python3 -I .claude/hooks/kit/test_hooks.py` ends `0 failed`.
   **Ask the owner once:** "Every session ends by merging its work onto `<main branch>` and pushing it, never forced
   (a conflict stops it). Do you give that standing permission?" Yes: rerun with `--trunk-push yes`. No: sessions push
   only their own branch. Either way each session ends with `python3 -I .kit/end_session.py`.
3. **Translate, then publish the board** (with a board only). If the owner's language (`--talk`) is not English:
   `python3 -I .kit/strings.py template .kit/strings.json`, fill in each English string's translation (about 140 short
   lines, a few KB; never edit `.kit/board.html` itself), rerun step 2's command with no options: it applies the table
   and prints how many strings are left. Text that goes to Claude (`Answer: `, the Like lines) stays English on purpose.
   **Publish** (the Artifact tool asks you to `Read` the whole 145 KB file first, and to load the skill `artifact-capabilities` before passing `capabilities`: both are expected): `Artifact` `publish`, `file_path` `.kit/board.html`, `icon` `board`, `capabilities`
   `{"assets": {}, "comments": {}, "db": {}, "user": {}, "downloads": true}`. Then rerun step 2's command with
   `--board-url <the url it returned>` (the url goes into the prompts and the rules). **Then stop this session's own
   watch on the page** (`ArtifactComments` `watch` with `url` and `on: false`): publishing makes the publisher listen to its
   comments, and only the clerk must. Commit
   `.kit .claude .story .gitignore` and push.
4. **Post your first two posts** in one `ArtifactData` `batch` (`.kit/BOARD.md` § Post; `created_at` from `date -u +%Y-%m-%dT%H:%M:%SZ`):
   `_orchestrator`: `{title: "Ask the orchestrator", column: "info", part: "orchestrator", priority: 3, created_at,
   body: "Write here for anything that is not about one post."}` (no `session_id` yet: the orchestrator writes its own);
   `KIT-installed`: `{title: "The board kit is installed", column: "info", part: "KIT", priority: 2, session_id: <this
   session's id>, created_at, body: <what step 2 wrote and kept>}`.
5. **Start the orchestrator, then the clerk** (only those installed: `.kit/orchestrator.md`, `.kit/clerk.md`; without a
   clerk, each session reads its post's comments at its next turn, and step 6's test comment is read by this session), each with `create_session` (`source_url` the repository's GitHub url,
   `source_revision` the branch of step 2, the prompt pasted inline after reading it):
   - the orchestrator: `prompt` the whole text of `.kit/orchestrator.md`, `title` "orchestrator", `model` and `effort`
     from `.kit/config.json` `kit.roles.orchestrator` (default Opus, medium). It writes its id into
     `_orchestrator`, builds the "Where we are" card from the memory, and posts its welcome under "Ask the
     orchestrator": what the board does, how it works, everything open, and what can run on its own at the same time
     (one 👍 launches it all);
   - the clerk: `prompt` the whole text of `.kit/clerk.md`, `title` "board clerk", `model` and `effort` from
     `kit.roles.clerk` (default Haiku, **low**: it only forwards; more effort costs more at every wake and helps nothing). It
     answers "board clerk ready, watching" to the orchestrator.
   If `create_session` is not available here, tell the owner: **YOU:** open https://claude.ai/code twice on this
   repository and paste `.kit/orchestrator.md` into one session and `.kit/clerk.md` into the other.
6. **YOU, the first comment:** open the board's url, open the post `KIT-installed`, write "test" in its comment box
   and send it. The clerk passes it to this session ("Board comment from the owner on your post KIT-installed …"),
   marks it "✓ Seen" and replies "Passed to …" in the thread. Answer it under the post (`.kit/BOARD.md` § Comments).
7. **The board's copy in git** (`.kit/ORCHESTRATOR.md` § 3; run it now, and again after step 6's comment):
   `python3 -I .kit/kit.py init` (a worktree NEXT TO the project, `../<project>-board-mirror`, on a new branch
   `board-mirror` that is never merged into main); `date -u +%Y-%m-%dT%H:%M:%SZ` (T0); `ArtifactData` `list` `posts` and
   `list` `comments` (`query.limit` 1000) with `out_dir` `/tmp/kd` (empty; short paths cost fewer tokens);
   `python3 -I .kit/kit.py sync /tmp/kd --at <T0> --full --commit`; `python3 -I .kit/kit.py open` lists comments of the owner's still unanswered,
   `kit.py post KIT-installed` shows the post and its comments; `kit.py push` pushes that branch (`board-mirror`) to your repository: it is the only branch the kit creates.
8. **Report**, then end the setup: `python3 -I /tmp/board-kit/install.py . --setup-done` (until then the stop hooks
   stay quiet, so they do not fight the install's own turns), commit, push. With a board, the report goes in the body of `KIT-installed` (`update`): each step's outcome, every step that needed the owner, anything that failed with
   its exact message. From now on every session posts there at turn 1 (the SessionStart hook says so).
9. **Tell the person what now**, as your last chat message, in their language (`.kit/config.json` `language.talk`):
   (1) where the work can run: their runners (README § Runners) as step 0 or the import found them, each with its
   one-time setup still to do; and the test for a connector: "ask me to read one of your connector's files here; if it
   answers, that work can run here (a `cloud` runner)"; (2) the Import: "open a new claude.ai chat and paste the block at
   https://github.com/anlonlt/board/blob/main/step0/export-chat.md , then paste back here the line it gives you";
   (3) where the board is: its link.

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
