# Board: the orchestrator's guide (short, Claude Code CLI on a computer)

The orchestrator is one Claude Code CLI session (`board-orchestrator`) that launches background sessions ("parts", `claude --bg -w`) in their own git worktrees, relays what the owner decides, checks each Done, and keeps its memory file current so a compaction loses nothing. A separate small session, the clerk (`board-clerk`, model `haiku`), listens to the board (0 tokens while waiting) and forwards each comment to the session that wrote the post, so the orchestrator only hears what is for it. Everything it carries is paid again on each of its later turns, so **it reads little and points a lot**.

## 1. Start
Your prompt is `.kit/claude-cli/orchestrator.md`: you ensure the local board server (`.kit/board_server.py`) is running in the background, write `board-orchestrator` into `_orchestrator.session_id`, start the clerk session, build the "Where we are" card and its trains with the `kit-cards` subagent (so the list of posts stays out of your context), and post the welcome once.

## 2. A part
- A part is one task (`TaskCreate`, metadata `{train, part}`): you write only the task (goal, bars, holds) and the owner's comment ids it answers; the kit writes the step's post and its prompt `docs/prompts/<post id>.md` from `.kit/claude-cli/part.md` (context via `kit.py train --zoom`, the post format, `kit.py done`, the Done message) and gives you the launch command (`.kit/claude-cli/orchestrator.md` rule 8). Its result lives in its post; the clerk forwards the owner's comments on its post to it directly, with the one `board_cli.py reply` command that answers them.
- Completing its task starts its review (rule 10); a cross review of the whole train follows every `kit.review.train_every` completed steps.
- Role models from `.kit/config.json` (`kit.claude_cli_roles`): clerk `haiku`, part `inherit`, consultant `opus`, cards `haiku` (`inherit` = no `model` on the call).
- Check a Done by rerunning one of its numbers yourself (not by rereading its words), then one comment under its post: what it means for the owner.

## 3. Reading the board (through `kit`, never whole dumps)
The local board server (`.kit/board_data/board.db`) is the live inbox; `.kit/kit.py` keeps a git copy of it on the branch `board-mirror` (worktree `../board-board-mirror`, never the main branch) and answers in hundreds of characters.
1. Once per clone: `python3 -I .kit/kit.py init`. Then sync in one O(delta) step: `python3 -I .kit/kit.py sync-db --commit`.
2. Lookups: `kit.py post <id> [--head N]` (a post and its comments, one line each), `kit.py open` (the owner's comments with words and no answer), `kit.py since <ISO>` (every change), `kit.py session <post>` (who wrote it), `board_cli.py inbox`.
3. Delegate a full dump to a subagent (its output never enters your context).

## 4. Memory and compaction
- `.board/memory/state/<workstream>.md`: one file per workstream, its first line `Next: …`; the `SessionStart` hook prints every `Next:` line (also after a compaction), so a session knows where things stand. `.board/memory/awaits.md`: what waits on the owner, each with its default. `.board/memory/lessons/`: one file per lesson. Skill `memory` says how.
- **The task list is the train:** the orchestrator plans with `TaskCreate` / `TaskUpdate`; a task with `metadata: {train, part}` is that train's step post (`kit_task_train.py`), so the plan is always on the board.
- `.board/memory/state/orchestrator.md`: only what the board cannot hold (open timers).
- **Context reset, not compaction:** at 380k (`kit.orchestrator.context_limit_k`) the Stop hook (`kit_pre_compact.py`) asks for the hand-over; `.kit/handover.py --role orchestrator` ends the session and `up` reopens a fresh one under the same name, which resumes from `kit.py resume` (active trains, live part sessions, the owner's unanswered comments). The clerk resets itself the same way at 80k. Claude Code's own compaction is set just past each limit, only as a safety net.

## 5. Limits (from `.kit/config.json`, enforced by `.claude/settings.json` hooks)
A `SendMessage`: at most 600 characters (a Done pointer: 240); the result goes in the post. Destructive git and `rm -r` outside /tmp are refused by `kit_guard.py` (it guards every `Bash` call).
