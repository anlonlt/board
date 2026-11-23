Board, part CL17: A crashed part is continued from its last checkpoint. Train `cli`. Your step's post: `cl17-a-crashed-part-is-continued-from-its-las` (it exists already).

## The task
Goal: a part that crashes (CLI crash, OOM, closed laptop) is continued by a fresh session, here or elsewhere, from where it stopped, without the owner re-explaining. Today CL8, CL9, CL10, CL12, CL14 show "in progress, session not running" after the two crashes, and their progress exists only in uncommitted worktrees and dead transcripts.

Do:
- Parts checkpoint: `.kit/claude-cli/part.md` (and the agy part prompt) makes a part keep a short checklist in its post (done / doing / next, updated as each sub-task ends; the post IS the part's task list, no hook mirroring of sub-steps) and commit + push its branch at each checkpoint (`kit.py checkpoint <post>`: commits the worktree as WIP on the part's branch, pushes, stamps the post with branch + commit + time).
- `kit.py resume` shows, for each in-progress step whose session is not running: its branch, last checkpoint, the checklist's next item, and the relaunch command (a new part on that branch, prompt = the step's prompt + "continue from the checklist in your post; your branch has your work"). On the same machine, also offer `claude --resume <session id>` when the transcript still exists.
- The orchestrator relaunches crashed parts within the device cap (CL15) instead of asking, unless the step was waiting on the owner.

Bars: a test that kills a part mid-step and a relaunched part continues from the checkpoint (branch has the work, post has the checklist); resume output lists the relaunch line. Mirror .kit/ changes into kit/.
Holds: WIP commits stay on the part's branch, never on the trunk.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## How you work (the same for every part; written by the kit, not the orchestrator)
1. **Context, not a summary:** `python3 -I .kit/kit.py train cli --zoom CL17` shows the train so far: what each earlier step found, its `check` and `handoff`. Read the owner's words above yourself (never trust a paraphrase of them, this prompt's included); a number from an earlier step is rechecked with its `check` command before you build on it.
2. **Turn 1:** `python3 -I .kit/board_cli.py update posts cl17-a-crashed-part-is-continued-from-its-las --data '{"session_id":"board-cl17","status":"in progress","body":"<one line: what you are doing first>"}'`. Keep your own working checklist with TaskCreate/TaskUpdate if it helps you; it stays yours.
3. **Your result is the post, in the kit's format** (`.kit/BOARD.md` § The post format): `verdict` (one plain sentence answering the task), `bars` (each number with its denominator), `artifacts` (repo paths: they open in the board's viewers), `not_checked`, `check` (the one command that reproduces your main number), `handoff` (what the next step needs), and the `body`: the whole result, after a first line saying what the owner must know or do. Say it once, on the board: the post is complete, messages only point to it; the lead says what he must know, the rest under `##` sections, which fold as he likes (`.kit/BOARD.md` § Sections fold, `kit.py sections`); a repo document (`docs/CL17.md`) only for a report to keep or what a post body cannot show (pictures inline, diagrams), embedded as `` `docs/CL17.md § Section` `` and never restated. Pick the board feature for each kind of content from `.kit/BOARD.md` § Which feature for what (audio takes, pictures to compare, a question for the owner).
4. **Done:** `python3 -I .kit/kit.py done cl17-a-crashed-part-is-continued-from-its-las` must say OK (it checks the format and that your artifacts exist); commit and push your work; `python3 -I .kit/end_session.py`; then `SendMessage` to `board-orchestrator`: `Done. Check post cl17-a-crashed-part-is-continued-from-its-las.` (if the send fails, the orchestrator is mid hand-over: wait 30 s and send again). A review of your step starts by itself.
5. **Then stay available:** the board clerk forwards the owner's comments on your post to you, each with the one command that answers it in his thread (`board_cli.py reply`). Talk to him in en, plainly, on the board, never at length in chat; each chat turn ends with `·`.
