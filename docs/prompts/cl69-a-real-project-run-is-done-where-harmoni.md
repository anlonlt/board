Board, part CL69: A real-project run is done where Harmonize already is: the computer orchestrator, never add_repo on the web. Train `board-data`. Your step's post: `cl69-a-real-project-run-is-done-where-harmoni` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl69-a-real-project-run-is-done-where-harmoni`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "39 had already failed." Getting Harmonize into a web session has now failed four ways: CL38 (allow rule: auto mode still refuses add_repo, 2026-11-27 03:36), CL39 (one web environment: its review found the chain never ran once, test red, no env id recorded), CL40 (read-only clone: launches only, no Harmonize), and `kit.py attach` (refused too). No fourth patch on the web side.

Design: a step's run on a real project is done where Harmonize already is: the computer orchestrator (agy on penguin, which has the clone). A web orchestrator never attaches a repo.

Done when:
1. `kit.py step complete` on the web, when the real-project run is pending, hands it to the computer orchestrator: a queued item on the board (`real_project_todo` on the post, listed in that orchestrator's resume) instead of a refusal the web orchestrator cannot act on.
2. The computer orchestrator's resume lists those items with the command each post names; running them writes `real_project` and starts the review (no web step in between).
3. Resume on the web never prints add_repo or attach; CL38's allow rule and CL39's web-env are marked superseded in their posts.
4. Test: a web `step complete` with a pending run queues it; the computer resume shows it; filling it lets the review start. `python3 scripts/run_all_tests.py` green.
First items: CL17, CL50, CL54, CL56, CL59, CL60.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
