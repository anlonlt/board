# Board: the orchestrator's guide (short, Claude Code CLI on a computer)

The orchestrator is one Claude Code CLI session (`board-orchestrator`) that launches background sessions ("parts", `claude --bg -w`) in their own git worktrees, relays what the owner decides, checks each Done, and keeps its memory file current so a compaction loses nothing. A separate small session, the clerk (`board-clerk`, model `haiku`), listens to the board (0 tokens while waiting) and forwards each comment to the session that wrote the post, so the orchestrator only hears what is for it. Everything it carries is paid again on each of its later turns, so **it reads little and points a lot**.

## 1. Start
Your prompt is one command, `python3 -I .kit/kit.py resume --role orchestrator` (this file is a reference, not a reading list): you ensure the local board server (`.kit/board_server.py`) is running in the background, write `board-orchestrator` into `_orchestrator.session_id`, start the clerk session, check the "Where we are" card and its trains with `kit.py cards` (the board server builds them; one line back, so the list of posts stays out of your context), and post the welcome once.

## 2. A part
- A part is one task (`TaskCreate`, metadata `{train, part}`): you write only the task (goal, bars, holds) and the owner's comment ids it answers; the kit writes the step's post and its prompt `docs/prompts/<post id>.md` from `.kit/claude-cli/part.md` (context via `kit.py train --zoom`, the post format, `kit.py done`, the Done message) and gives you the launch command (`.kit/claude-cli/orchestrator.md` rule 8). Its result lives in its post; the clerk forwards the owner's comments on its post to it directly, with the one `board_cli.py reply` command that answers them.
- Completing its task starts its review (rule 10); a cross review of the whole train follows every `kit.review.train_every` completed steps.
- Role models from `.kit/config.json` (`kit.claude_cli_roles`): clerk `haiku`, part `inherit`, consultant `opus`, cards `haiku` (`inherit` = no `model` on the call).
- At a Done the review checks the work (it reruns, you do not); act on its verdict, then one comment under the post: what it means for the owner.

## 3. Reading the board (through `kit`, never whole dumps)
The local board server (`.kit/board_data/board.db`) is the live inbox; `.kit/kit.py` keeps a git copy of it on the branch `board-mirror` (worktree `../board-board-mirror`, never the main branch) and answers in hundreds of characters.
1. Once per clone: `python3 -I .kit/kit.py init`. Then sync in one O(delta) step: `python3 -I .kit/kit.py sync-db --commit`.
2. Lookups: `kit.py post <id> [--head N]` (a post and its comments, one line each), `kit.py open` (the owner's comments with words and no answer), `kit.py since <ISO>` (every change), `kit.py session <post>` (who wrote it), `board_cli.py inbox`.
3. Delegate a full dump to a subagent (its output never enters your context).

## 4. Memory and compaction
- **Where things stand is on the board, never in memory** (the owner, 2026-11-23: "everything is on the board"): each step's state is its post (its train is the task list), what waits on the owner is a `question` post, a defect is a post (`kind: "issue"`, status open/closed). `python3 -I .kit/kit.py resume` reads it. `.board/memory/` holds only what no post holds: `lessons/` (one file per lesson: symptom, cause, the change that stops it recurring), `workflow.md` (runners, tools, accounts), `preferences.md`, and `state/orchestrator.md` (open timers only). `kit.py compact` archives an older project's state files, awaits.md and issues/. Skill `memory` says how.
- **The task list is the train:** the orchestrator plans with `TaskCreate` / `TaskUpdate`; a task with `metadata: {train, part}` is that train's step post (`kit_task_train.py`), so the plan is always on the board.
- `.board/memory/state/orchestrator.md`: only what the board cannot hold (open timers).
- **Context reset, not compaction:** at 380k (`kit.orchestrator.context_limit_k`) the Stop hook (`kit_pre_compact.py`) asks for the hand-over; `.kit/handover.py --role orchestrator` ends the session and `up` reopens a fresh one under the same name, which resumes from `kit.py resume` (active trains, live part sessions, the owner's unanswered comments). The clerk resets itself the same way at 80k. Claude Code's own compaction is set just past each limit, only as a safety net.

## 5. Limits (from `.kit/config.json`, enforced by `.claude/settings.json` hooks)
A `SendMessage`: at most 200 characters (a Done pointer: 200); the result goes in the post. Destructive git and `rm -r` outside /tmp are refused by `kit_guard.py` (it guards every `Bash` call).

**A train is never walked in a circle.** `kit.py resume` carries what each active train found (`kit.py train <train> --brief` for all of it). Before a step of a train with finished steps is launched, say which of them it builds on and what it does differently: `kit.py step differs <post> --task "<PART>: ..."` (launch asks for it and shows every earlier conclusion). A step marked `[r]` in resume is finished and unreviewed: complete its task (or `kit.py step complete <post>`) and its review starts, whoever launched it.
