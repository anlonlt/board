Board, part CL18: Web takeover: Claude Code web continues where the board says. Train `cli`. Your step's post: `cl18-web-takeover-claude-code-web-continues-w` (it exists already).

## The task
Goal: web takeover. Open Claude Code on the web (claude.ai/code) on this repo, say "take over", and the orchestrator and any crashed parts continue exactly where the board says they were, as if the web session had run the task list from the start. Builds on CL16 (board in git) and CL17 (parts checkpoint).

Do:
- Detect a web/cloud session in the SessionStart hook (e.g. the CLAUDE_CODE_REMOTE env var; verify what Claude Code web actually sets) and brief it for mode "web": no local board server, no Tailscale, no `claude --bg`; the board is read from and written to `board-mirror`.
- A takeover prompt `.kit/claude-cli/takeover.md` (and a `board takeover` / `kit.py takeover` that prints it): pull, `kit.py resume` from the mirror, rebuild the task list (TaskCreate with {train, part}: existing posts adopted), mark the old orchestrator session as replaced, post the takeover notice under `_orchestrator`, then continue. Crashed parts run as the web session's subagents (Agent tool, worktree isolation) on their branches, within the device cap that step 0 measures inside the web container.
- The owner's comments still reach it: from the phone board via the mirror (git_watch on the web side or a poll), and the clerk's role is done by a polling loop when no clerk runs.
- Hand-back: when the local CLI starts again, it sees the web session's work on the mirror and resumes from it (no double runs: a step claimed by a live session is not relaunched).
- README § Web takeover: the three steps for the owner.

Bars: an end-to-end test in a fresh clone with no .kit/board_data and the web env set: takeover rebuilds the same task list as the local orchestrator had, a crashed part's step continues from its checkpoint, and the result reaches the board after import locally. Mirror .kit/ changes into kit/.
Holds: the web session never pushes the trunk unless kit.trunk_push allows it; ask the owner before any change to how the phone board authenticates.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## How you work (the same for every part; written by the kit, not the orchestrator)
1. **Context, not a summary:** `python3 -I .kit/kit.py train cli --zoom CL18` shows the train so far: what each earlier step found, its `check` and `handoff`. Read the owner's words above yourself (never trust a paraphrase of them, this prompt's included); a number from an earlier step is rechecked with its `check` command before you build on it.
2. **Turn 1:** `python3 -I .kit/board_cli.py update posts cl18-web-takeover-claude-code-web-continues-w --data '{"session_id":"board-cl18","status":"in progress","body":"<one line: what you are doing first>"}'`. Keep your own working checklist with TaskCreate/TaskUpdate if it helps you; it stays yours.
3. **Your result is the post, in the kit's format** (`.kit/BOARD.md` § The post format): `verdict` (one plain sentence answering the task), `bars` (each number with its denominator), `artifacts` (repo paths: they open in the board's viewers), `not_checked`, `check` (the one command that reproduces your main number), `handoff` (what the next step needs), and the `body`: the whole result, after a first line saying what the owner must know or do. Say it once, on the board: the post is complete, messages only point to it; the lead says what he must know, the rest under `##` sections, which fold as he likes (`.kit/BOARD.md` § Sections fold, `kit.py sections`); a repo document (`docs/CL18.md`) only for a report to keep or what a post body cannot show (pictures inline, diagrams), embedded as `` `docs/CL18.md § Section` `` and never restated. Pick the board feature for each kind of content from `.kit/BOARD.md` § Which feature for what (audio takes, pictures to compare, a question for the owner).
4. **Done:** `python3 -I .kit/kit.py done cl18-web-takeover-claude-code-web-continues-w` must say OK (it checks the format and that your artifacts exist); commit and push your work; `python3 -I .kit/end_session.py`; then `SendMessage` to `board-orchestrator`: `Done. Check post cl18-web-takeover-claude-code-web-continues-w.` (if the send fails, the orchestrator is mid hand-over: wait 30 s and send again). A review of your step starts by itself.
5. **Then stay available:** the board clerk forwards the owner's comments on your post to you, each with the one command that answers it in his thread (`board_cli.py reply`). Talk to him in en, plainly, on the board, never at length in chat; each chat turn ends with `·`.
