Board, part CL67: The orchestrator's push never sends the cards: no refused batch when another session rebuilt them. Train `board-data`. Your step's post: `cl67-the-orchestrator-s-push-never-sends-the` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl67-the-orchestrator-s-push-never-sends-the`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "why do you keep getting failed commands? Do you not learn?"

Found (orchestrator 3, 03:25-03:40): 5 of 9 ArtifactData batches were refused with version_mismatch, every time on `_dashboard` or `_trains`. `kit.py artifact push` puts the orchestrator's local copy of the two cards in every batch, pinned to the version it last read, while the kit-cards agent, the Antigravity orchestrator and the parts rewrite them on the live board every few minutes. Each refusal cost a get, a pin and a resend; one pin could not merge (CL17 row) at all. CL59 makes a part push only its own post; the orchestrator's push has no such rule.

Done when:
1. `kit.py artifact push` never sends `_dashboard` or `_trains` from a local copy: the cards are written only by the kit-cards rebuild, which reads the live copy and writes with its version in one step.
2. A batch whose only refusal is a card goes through without it (the card is dropped from the batch and named).
3. Test: two pushes racing a card rewrite both commit their posts and comments; no card conflict comment is made. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
