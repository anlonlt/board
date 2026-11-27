Board, part CL70: A merged step is shown landed: the reviewed commit on main counts, not the branch tip. Train `board-data`. Your step's post: `cl70-a-merged-step-is-shown-landed-the-review` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl70-a-merged-step-is-shown-landed-the-review`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "CL44 was already integrated, no? Why is that the third time I'm telling someone this?"

Found (orchestrator 3): CL44 was merged on main at 047e67dc ("Merge cl44 ... items 1-12"), M5-M8 and CL41's reviewed commits (6b20a2f, 313466a, 309ba6c) are on main, yet `kit.py resume` listed all six under "Work not on the trunk yet" and `kit.py land` refused them: land (kit/landing.py) only counts a step landed when its branch TIP is on main, so two later test commits on cl44, or the agy orchestrator's 15 newer commits on the shared branch feat/streamline-doctor-memory, keep a merged step "not landed" forever. Each new orchestrator rediscovers it and asks the owner.

Done when:
1. `land` and resume count a step landed when the commit its review read (`review_commit`) is on the trunk, or a trunk merge commit names its branch; they mark it (`landed`) themselves, once, and say so.
2. Commits on the branch after that point are listed separately ("2 newer commits on cl44, not reviewed"), never as the step being unlanded.
3. Test: a branch merged then given one more commit is shown landed with one newer commit; a shared branch with a reviewed ancestor on main is landed. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
