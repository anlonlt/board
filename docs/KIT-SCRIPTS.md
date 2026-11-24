# The kit's scripts, their Artifact form, and the hooks that hold them

From Harmonize `docs/KIT-SCRIPTS.md` (orchestrator 52, research of 2026-11-23 at anlonlt/harmonize 3138c3f8), kept in
the kit since CL30. "Same, after pull + load": run it as on a local board once the copy is fresh; "+ push": its
writes reach the Artifact only after `kit.py artifact push`, the `ArtifactData batch` calls, `kit.py artifact sent`.
Roles: O orchestrator, P part, C clerk, R review. Runtimes: CC Claude Code (CLI or web), agy.

## The gaps CL30 closed

Each row has a test in `tests/test_cl30_kit_scripts.py`, red on kit main a7ce380 and green after
(`python3 -m unittest tests.test_cl30_kit_scripts`).

| # | Gap (before, a7ce380) | After CL30 | Where |
|---|---|---|---|
| 1 | Every kit guard held the orchestrator only; parts, the clerk and reviews wrote posts and comments with raw `ArtifactData` (the kit's own templates told them to) | The guard holds every session: a hand `set` / `update` / `str_replace` / `delete` of a post or comment (alone or in a `batch`) and a raw dump are refused, naming pull + load, `board_cli.py set/update/reply`, `artifact push` + `sent`. What has no kit form passes: other collections (`typing`, `kit_artifacts`), the clerk's read marks, a new session on `_orchestrator`, a batch from `.kit/run/artifact/out`, the orchestrator's subagents, `KIT_ROLE=owner`. Templates (BOARD, skill, runner, consultant) say the kit's way | `kit/hooks/kit_orchestrator_guard.py`, `kit/claude/*.md` |
| 2 | A reply to the owner needed a fresh `resume` only as `board_cli.py reply` in Bash; Harmonize wired the brief guard on Bash only, so `ArtifactData` replies passed | Any board_cli write naming `reply_to` counts as a reply; `install.py --update` widens a kit guard's narrower matcher to the kit's (`ArtifactData\|Bash`) instead of leaving it or doubling it | `kit_brief_guard.py`, `install.py` `merge_claude_settings` |
| 3 | `resume` and the reads could read a copy never loaded or hours old; nothing named `artifact push` after a local write (the task-train hint fired only when `board_url` was claude.ai); a session could stop with its changes local only | The reads (`resume`, `train`, `post`, `open`, `since`, `session`, `search`, `queue`, `sections`, `report`) exit 3 on a copy never loaded or older than `kit.artifact.fresh_min` (15 min), so no brief stamp either; a local write is followed by the push line; a Stop is held once while a batch is unsent, the copy holds unsent changes (`kit.py artifact status`) or a launch lacks its second half; task-train reads `kit.artifact.url` too | `scripts/kit.py` `stale_copy`, `artifact_unsent`; `kit/hooks/kit_board_guard.py`; `kit_task_train.py` |
| 4 | Orchestrator holes: `update` / `str_replace` of a step post passed; any launch tagged `orchestrator` / `board-clerk` / `review` passed unchecked; a part could start without `kit.py launch` (post not in progress, Trains row not running) and its `--session-id` half was never asked | Every hand write of a post refused (gap 1); a tag passes only with its kit prompt (successor: names `.kit/claude-cli/orchestrator.md` / `takeover.md`, at most 1,200 chars; clerk: `Your prompt is .kit/claude-cli/clerk.md`; review: `Your prompt is .kit/run/<id>.md`); a part's `create_session` needs the mark `kit.py launch <post>` leaves (`.kit/run/launch/<post>.json`); after it the hook names `kit.py launch <post> --session-id <id>`, and a Stop holds until it ran. The kit's own calls (review, handover) now carry their tag | `kit_orchestrator_guard.py`, `kit_board_guard.py`, `scripts/kit.py` `launch_mark`, `kit/handover.py` |
| 5 | agy: the board-kit MCP tools passed every hook: `board_reply` skipped the brief guard, `board_resume` left no stamp | The MCP server holds the guard itself: `board_resume` stamps `.kit/run/brief/last`; an orchestrator's `board_reply` (sender `orchestrator` or `KIT_ROLE`) is refused without a stamp younger than `kit.brief.max_age_min` | `kit/board/mcp_stdio.py` |
| 6 | `kit_cards_reminder.py` registered nowhere (Harmonize); not in the CLI set; it found the orchestrator only by a /tmp mark web orchestrators never write | In the CLI set (UserPromptSubmit); `--update` wires a kit guard whose file is in the present runtime but has no line; it finds the orchestrator as the guard does (first prompt, branch, `HARMONIZE_ROLE`); its quiet time is per session | `install.py` `GUARD_HOOKS`, `kit_cards_reminder.py` |
| 7 | `CLAUDE_CODE_ENABLE_TODO_TOOLS` unset where the web hook set was installed (it lacked `kit_task_train.py`), so TaskCreate may be off | The web set carries `kit_task_train.py`, so the installer sets the variable; `--update` into a present runtime does the same | `install.py` `CLAUDE_SETTINGS` |
| 9 | The owner's item (10:3x) and Harmonize's parity check: `artifact push` planned the files of the posts it sent only (Harmonize: 130 of 196 posts name repo files, `kit_docs` empty); documents needed a second command; on an attached board the steps named the host's `kit_docs/_index`; a local copy from the Artifact had none of its pictures (`/_blob` 404); the files state lived in `.kit/run/` (lost with each container); a failed `§` embed vanished silently | `artifact push` plans the files of every post and comment of the copy; Markdown/HTML go in its own batches (`<prefix>kit_docs`, recorded at `artifact sent`); pictures, PDF, video: the `Artifact publish` calls + `files-sent --into <board>`; `artifact pull` also gets `<prefix>kit_docs/_index`; `artifact load` puts every `/_blob/<id>` the posts name in the local store (same id) from the repo file the map names or from `Artifact read` (it prints the calls); the state is `.kit/artifact-files/` (in git); the embed says "Not on this board yet: <path>" | `scripts/kit.py` `_artifact_files`, `_load_blobs`; `kit/claude/kit_artifact_sync.py` `mirror_doc`; `kit/board/board_server.py` (`X-Blob-Id`); `kit/board/shim.js`; test `tests/test_cl30_files.py` (7 of 7 red on 0a45d26) |
| 8 | CL28's open item: `artifact push` from a copy never loaded (a part's board from git) built 78 writes without `if_version` | `artifact push` refuses until `artifact load` ran once | `scripts/kit.py` `artifact_push` |

Not changed here (Harmonize's own, or not in this step): Harmonize's `say_it_once.py` / `context_handover.py` role
detection and limits (its gaps 8 and 9), its `stop.sh` naming its own end script (gap 7), its older `scripts/kit.py`
(gap 13). Harmonize gets CL30 with `install.py --update` from kit main; the brief guard's matcher, the cards
reminder and the task-tools variable then change in its `.claude/settings.json` (its own hooks kept).

## Scripts (what changed is marked CL30)

| Script / command | Local board | Artifact board | Hook that makes agents use it |
|---|---|---|---|
| `kit.py commands [word]` | same | same | agy SessionStart names it; CC: the kit's SessionStart where installed |
| `kit.py resume` | same | after pull + load: **CL30 exit 3 on a stale or never-loaded copy** | `kit_brief_guard.py`: O's reply needs a `resume` in the last 15 min (Bash, ArtifactData; agy run_command; **CL30 MCP `board_reply`**) |
| `kit.py train/post/open/since/session/search/queue/sections/report` | same | **CL30 refused on a stale copy (exit 3)** | `kit_orchestrator_guard.py` refuses a raw dump (**CL30 every session**) |
| `board_cli.py get/list/query` | same | `ArtifactData get` / targeted `query` | as above |
| Plan a step: `TaskCreate {train, part}` / `kit.py step new` | `kit.py step new` | after pull + load, then push | `kit_task_train.py` (**CL30 in the web set; push hint reads `kit.artifact.url`**) |
| `kit.py step start/complete/drop` | same | then push | `kit_task_train.py` |
| `board_cli.py set/update/batch/str-replace/reply` | same | on the local copy, then push (**CL30: a hand `ArtifactData` write is refused for every session**) | `kit_orchestrator_guard.py`; `kit_board_guard.py` names the push after it |
| `kit.py launch <post>` | `claude --bg` in a worktree; refuses a step that a live session, another person (`for`, `working_by`) or another runner holds, and names them (`--take` once that session ended) | `--web` prints the call and leaves `.kit/run/launch/<post>.json`; then `--session-id <id>` | **CL30: required by `kit_orchestrator_guard.py`; its second half named and held at Stop by `kit_board_guard.py`** |
| `kit.py review <post>` | `claude --bg` / agy | prints the `create_session` call, **CL30 tagged `review`** | tagged launch checked |
| `handover.py --role R` | terminal | web: prints the call, **CL30 tagged** | tagged launch checked |
| `kit.py artifact pull` / `load` | n/a | the `ArtifactData` calls, then load | named by every refusal above |
| `kit.py artifact push` / `sent` / `pin` | n/a | **CL30 push refuses a never-loaded copy** | `kit_board_guard.py` (Post Bash, Stop) |
| `kit.py artifact status` | n/a | **CL30 new**: what is unsent (changed documents, batch files not recorded sent) | read by `kit_board_guard.py` at Stop |
| `kit_cards.py` (agent `kit-cards`) | same | the agent | `kit_cards_reminder.py` (**CL30 CLI set too; wired by --update**) |
| `mcp_stdio.py` (agy MCP `board-kit`) | agy | n/a | **CL30: holds the brief guard itself** |

## Hooks

| Hook | Event, matcher | Roles | CC web set | CC CLI set | agy |
|---|---|---|---|---|---|
| `kit_orchestrator_guard.py` | PreToolUse `ArtifactData\|create_session` | **all (CL30)**; launches O | yes | no | no ArtifactData |
| `kit_brief_guard.py` | Pre `ArtifactData\|Bash`, Post `Bash` | O | yes | Bash | run_command |
| `kit_board_guard.py` (**CL30 new**) | PostToolUse `Bash\|create_session`; Stop | all | yes | Bash, Stop | no (no Artifact) |
| `kit_task_train.py` | PostToolUse `TaskCreate\|TaskUpdate` | O | **yes (CL30)** | yes | by hand |
| `kit_cards_reminder.py` | UserPromptSubmit; Post `ArtifactData\|create_session` | O | yes | **UserPromptSubmit (CL30)** | no |
| `kit_notify.py`, `kit_say_it_once.py`, `kit_board_typing.py`, `kit_stop.py`, `kit_pre_compact.py`, `kit_session_start.py`, `kit_guard.py`, `kit_web_save.py` | unchanged | | | | |
