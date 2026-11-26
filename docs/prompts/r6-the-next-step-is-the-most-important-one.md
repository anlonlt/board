Board, part R6: The next step is the most important one, never the oldest. Train `runners`. Your step's post: `r6-the-next-step-is-the-most-important-one` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r6-the-next-step-is-the-most-important-one`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The next step is the most important one, never the oldest. The kit sorts steps by train, then by the date they were made (scripts/kit.py, the train view and resume); only questions have a priority. On 2026-11-26 the web orchestrator launched M5 to M8 (features) while CL51 waited (a local session writing to the Artifact): CL51's bug then overwrote 11 posts of the board. The owner set the order by hand that day: P1 damage and what blocks everything, P2 bugs, P3 features.

1. A step has a priority (1 damage or data at risk; 1 also what blocks other steps or the trunk; 2 a bug; 3 a feature): `step new --priority`, asked when missing; a step that fixes damage found by a review or a done is 1 by itself. In scripts/kit.py.
2. One queue across every train: the orchestrator's resume and `kit.py queue` list the open steps by priority, then by how many steps each one unblocks (waits on), then by date; "what runs next" is its top, on every platform.
3. A launch of a P3 step while a P1 step waits and could run is refused with the P1 named (`--anyway "<why>"` records the reason on the post).
4. Tests on a fixture board: a newer P1 comes before an older P3; a step others wait on comes before its peers; the refused launch names the P1.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
