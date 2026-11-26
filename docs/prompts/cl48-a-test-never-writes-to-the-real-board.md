Board, part CL48: A test never writes to the real board. Train `board-data`. Your step's post: `cl48-a-test-never-writes-to-the-real-board` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl48-a-test-never-writes-to-the-real-board`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A test never writes to the real board. The real board's `_orchestrator` post says "the install session of the board kit on Tide Clock (branch test-install)": Tide Clock is the made-up project of the install tests, and that text reached the real board's git copy on 2026-11-20 (board-mirror commit 738a5f3, "kit sync ... (full)").

1. Find how: which test or install run wrote to the real board or its board-mirror (a test whose board URL, token or mirror path fell back to the real one).
2. Make it impossible: a test run (run_all_tests.py, or KIT_TEST set) refuses any write to a board, mirror or remote that is not under a temp folder, with the path it refused.
3. Put the `_orchestrator` post's text right (what it says for this repo), keeping its session id.
4. A test that tries to write to the real board from a test run and is refused.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
