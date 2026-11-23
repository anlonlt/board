Board, part CL15: Step 0 measures each device; never more sessions than it can hold. Train `cli`. Your step's post: `cl15-step-0-measures-each-device-never-more-s` (it exists already).

## The task
Goal: never launch more sessions than the device can hold. The CLI crashed twice on this 2.7 GB / no-swap Chromebook; ~/.claude/daemon.log shows "low memory (454MB free)" right before (each Claude Code process is ~300-500 MB).

Do:
- Step 0 (`.kit/step0_scan.py`, and `step0/survey-*.md` where they describe the scan) surveys the device: total and available RAM (/proc/meminfo; `sysctl hw.memsize` + vm_stat on macOS), swap, CPU cores, and GPU only as information (sessions use the API; RAM is the bound). It derives `max_sessions` = max(1, floor((MemAvailable - 700 MB headroom) / 450 MB)), counting the orchestrator and the clerk.
- The cap is per DEVICE, not per repo: store it machine-locally (next to the per-machine board token of CL11, e.g. keyed by hostname), never in the committed .kit/config.json, so a laptop, this Chromebook and a web container each get their own. An owner override is allowed.
- Enforce it in code, not in a guide sentence: before a part launches (`kit.py step start` and/or `kit_guard.py` on a `claude --bg` Bash call), count live sessions (`_alive_sessions`) and re-read MemAvailable; over the cap or under the headroom, refuse with a clear message and the step stays queued (planned). The orchestrator launches the next queued step when a part ends.
- The welcome and `kit.py resume` state the cap and which steps wait for a free slot.

Bars: tests that fail when the cap is ignored (fake /proc/meminfo, fake alive sessions); the cap on this machine comes out at 2-3; no regression in tests/ and .claude/hooks/kit/test_hooks.py. Mirror any .kit/ change into kit/ (the source the installer copies).
Holds: do not kill running sessions to make room; ask the owner before changing launch defaults for agy.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## How you work (the same for every part; written by the kit, not the orchestrator)
1. **Context, not a summary:** `python3 -I .kit/kit.py train cli --zoom CL15` shows the train so far: what each earlier step found, its `check` and `handoff`. Read the owner's words above yourself (never trust a paraphrase of them, this prompt's included); a number from an earlier step is rechecked with its `check` command before you build on it.
2. **Turn 1:** `python3 -I .kit/board_cli.py update posts cl15-step-0-measures-each-device-never-more-s --data '{"session_id":"board-cl15","status":"in progress","body":"<one line: what you are doing first>"}'`. Keep your own working checklist with TaskCreate/TaskUpdate if it helps you; it stays yours.
3. **Your result is the post, in the kit's format** (`.kit/BOARD.md` § The post format): `verdict` (one plain sentence answering the task), `bars` (each number with its denominator), `artifacts` (repo paths: they open in the board's viewers), `not_checked`, `check` (the one command that reproduces your main number), `handoff` (what the next step needs), and the `body`: the whole result, after a first line saying what the owner must know or do. Say it once, on the board: the post is complete, messages only point to it; the lead says what he must know, the rest under `##` sections, which fold as he likes (`.kit/BOARD.md` § Sections fold, `kit.py sections`); a repo document (`docs/CL15.md`) only for a report to keep or what a post body cannot show (pictures inline, diagrams), embedded as `` `docs/CL15.md § Section` `` and never restated. Pick the board feature for each kind of content from `.kit/BOARD.md` § Which feature for what (audio takes, pictures to compare, a question for the owner).
4. **Done:** `python3 -I .kit/kit.py done cl15-step-0-measures-each-device-never-more-s` must say OK (it checks the format and that your artifacts exist); commit and push your work; `python3 -I .kit/end_session.py`; then `SendMessage` to `board-orchestrator`: `Done. Check post cl15-step-0-measures-each-device-never-more-s.` (if the send fails, the orchestrator is mid hand-over: wait 30 s and send again). A review of your step starts by itself.
5. **Then stay available:** the board clerk forwards the owner's comments on your post to you, each with the one command that answers it in his thread (`board_cli.py reply`). Talk to him in en, plainly, on the board, never at length in chat; each chat turn ends with `·`.
