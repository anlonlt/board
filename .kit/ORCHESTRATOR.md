# Board: the orchestrator's guide (short, agy on a computer)

The orchestrator is one `agy` session that launches subagents ("parts") in shared git worktrees, relays what the owner decides, checks each Done, and hands over (or compacts) before its context grows too large. Everything it carries is paid again on each of its later turns, so **it reads little and points a lot**.

## 1. Start
Your prompt is `.kit/orchestrator.md`: you ensure the local board server (`.kit/board_server.py`) is running as a daemon, write your conversation ID into `_orchestrator` (the one place it lives: the clerk and the page find you there; a successor rewrites it), build the "Where we are" card and its trains with the `kit-cards` subagent (so the list of posts stays out of your context), and post the welcome under `_orchestrator` once. Keep a `schedule(DurationSeconds=1800, ...)` timer while parts run.

## 2. A part
- Write its prompt as a file (`docs/prompts/<part>.md`): goal, bars measured before building, holds, "post `<part>-<slug>` at turn 1". Launch it with `invoke_subagent(TypeName="self", Model="inherit", Workspace="share", ...)` (`Prompt`: "Your prompt is `docs/prompts/<file>`: read it and do it."). Its result lives in its post; it ends with `python3 -I .kit/end_session.py` and sends you `Done. Check post <id>.` via `send_message`.
- Role models from `.kit/config.json` (`kit.roles`): orchestrator `inherit`, clerk `flash_lite`, part `inherit`, consultant `pro`, cards `flash`.
- Check a Done by rerunning one of its numbers yourself (not by rereading its words), then one comment under its post: what it means for the owner.

## 3. Reading the board (through `kit`, never whole dumps)
The local board server (`.kit/board_data/board.db`) is the live inbox; `.kit/kit.py` keeps a git copy of it on the branch `board-mirror` (worktree `../board-board-mirror`, never the main branch) and answers in hundreds of characters.
1. Once per clone: `python3 -I .kit/kit.py init`. In local SQLite mode, sync directly in one O(delta) step with `python3 -I .kit/kit.py sync-db --commit` (or dump via `board_cli.py list posts --limit 1000 --out-dir /tmp/kd` and `board_cli.py list comments --limit 1000 --out-dir /tmp/kd`, then `python3 -I .kit/kit.py sync /tmp/kd --at <UTC time taken before the reads> --full --commit`).
2. Each later sync: `python3 -I .kit/kit.py reads` prints the reads (posts in full; comments by stamp since the last sync minus an hour). Run them with `board_cli.py` `--out-dir` into a fresh D, then `kit.py sync D --at <time before the reads> --commit`, and `kit.py push` now and then.
3. Lookups: `kit.py post <id> [--head N]` (a post and its comments, one line each), `kit.py open` (the owner's comments with words and no answer), `kit.py since <ISO>` (every change), `kit.py session <post>` (who wrote it).
4. A gate before anything relies on the copy: `kit.py diff D` against a full dump taken the same minute must print `0 differences`. Delegate a full dump to a subagent (its output never enters your context).

## 4. Memory and hand-over
- **Where things stand is on the board, never in memory** (the owner, 2026-11-23: "everything is on the board"): each step's state is its post (its train is the task list), what waits on the owner is a `question` post, a defect is a post (`kind: "issue"`, status open/closed). `python3 -I .kit/kit.py resume` reads it. `.board/memory/` holds only what no post holds: `lessons/` (one file per lesson: symptom, cause, the change that stops it recurring), `workflow.md` (runners, tools, accounts), `preferences.md`, and `state/orchestrator.md` (open timers only). `kit.py compact` archives an older project's state files, awaits.md and issues/. Skill `story` says how.
- **Automatic handover (at `380`k context, enforced by `kit_pre_compact.py`):** write `.board/memory/state/orchestrator.md` (running parts + conversation IDs, clerk conversation ID, open timers, next step), commit and push, post `"Hand-over: orchestrator <N+1>..."` under `_orchestrator`, and immediately spawn your successor via `invoke_subagent(TypeName="self", Role="Orchestrator <N+1>", Model="inherit", Workspace="inherit", Prompt="Your prompt is .board/memory/state/orchestrator.md: read it, rewrite _orchestrator.session_id to your conversation ID, tell the clerk and any running parts your new ID, post your takeover notice, and continue.")`. Never tell the owner to manually open a new session.
- **Clerk rotation (at `80`k context):** when the clerk reports `"clerk context at <N>k (>= 80k): ready for replacement"`, spawn a fresh clerk via `invoke_subagent(TypeName="self", Role="Board Clerk", Model="flash_lite", Workspace="inherit", Prompt="Your prompt is .kit/clerk.md: read it and follow it.")`, wait for `"board clerk ready, watching"`, and kill the old clerk with `manage_subagents(Action="kill", ...)`.

## 5. Limits (from `.kit/config.json`, enforced by `.agents/hooks.json`)
A message via `send_message`: at most 600 characters (a Done pointer: 240); the result goes in the post. Destructive git and `rm -r` outside /tmp are refused by `kit_guard.py`.
