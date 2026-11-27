Board, part CL62: The orchestrator cannot end a turn with a start-now step not launched or held. Train `takeover`. Your step's post: `cl62-the-orchestrator-cannot-end-a-turn-with` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl62-the-orchestrator-cannot-end-a-turn-with`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The orchestrator cannot end a turn while a "start now" step is neither launched nor held with a reason: the queue is enforced, not read.

Owner, 2026-11-27: "Why didn't you launch it when it told you? This needs a permanent fix that doesn't depend on your stupidity." Measured: web orchestrator 2's resume at 02:28 listed M2, M4, M3 (and CL50, CL52, CL54, CL56) as [start now]; it followed the previous hand-over's stale line "M2 and M3 wait on M1" (M1 had landed at 02:19) and its own split, and launched them only at 03:09 and 03:11 after the owner asked.

1. A Stop hook for the orchestrator role (same role function as M12/M13): while `kit.py queue` has a [start now] step that is not launched and not held (`kit.py hold <post> --why "..."`, recorded on the post, shown in resume and to the owner), the stop is refused once with the list and the exact launch commands. Steps owned by another orchestrator (a split recorded on _orchestrator) count as held for this one.
2. A hand-over line never overrides the queue: resume compares each "waits on X" claim in the last hand-over with the board and prints the stale ones first ("M2 waits on M1: M1 landed 02:19, start now").
3. The queue's out-of-order refusal stays; the Stop check is what makes "start now" impossible to skip.
4. Tests: a fixture board with 2 start-now steps, one launched, one not: Stop refused, naming it; after hold: passes. A stale hand-over claim is flagged.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
CL62: builds on R6 (the queue ranks every step) and CL53; R6 only lists and refuses out-of-order launches, CL62 makes an unlaunched start-now step block the orchestrator's stop and flags stale hand-over claims.
