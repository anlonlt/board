Board, part R8: Queue and launch enforce cross-step prerequisites so a step never shows [start now] while its dependency is open. Train `runners`, after `R6`. Your step's post: `r8-queue-and-launch-enforce-cross-step-prer` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r8-queue-and-launch-enforce-cross-step-prer`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
R3 explicitly depended on M9 ("in the workflow memory and the map (M9)") and R1/R2, yet R6's `kit.py queue` marked R3 `[start now]` while M9 was still open because dependencies only lived in prose and `blocked_by` was neither populated nor checked by the queue ranker.

1. Add `--blocked-by <part-or-post>` to `kit.py step new` and auto-extract explicit prerequisite references from the task text (e.g. `Waits on <PART>`, `after <PART>`, `(<PART>)` matching open/existing step parts) into the post's `blocked_by` list (deduped, skipping self-refs), in `scripts/kit.py`.
2. In R6's queue ranker (`rank_queue` / `cmd_queue`) and `kit.py launch`, treat any unresolved `blocked_by` step as a hard waiter: show `[waits: blocked by <PART> (not done)]` instead of `[start now]`, rank prerequisites ahead of the steps they unblock ("unblocks R3"), and refuse an out-of-order launch unless overridden.
3. Add unit tests in `tests/test_queue_prereqs.py` replaying the R3-depends-on-M9 case: M9 ranks ahead of R3 with reason "unblocks R3", R3 shows `[waits: ... M9]` instead of `[start now]`, and once M9 is complete R3 flips to `[start now]`.

## What this step answers (the owner's words, or a review's)
- comment `ea0ffa2eae664637891f`: read it with `python3 -I .kit/board_cli.py get comments ea0ffa2eae664637891f --out-dir /tmp/src`
