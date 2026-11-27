Board, part CL52: Tests clean up after themselves. Train `board-data`. Your step's post: `cl52-tests-clean-up-after-themselves` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl52-tests-clean-up-after-themselves`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Tests clean up after themselves: /tmp held about 5,600 folders left by test runs (cl30-inst-*, tmp*, kit-tests-*, scratch boards), each with a project copy and its own board token, on a computer with little room.

1. Count what each suite leaves in the temp folder (run it, compare before and after) and fix the suites that leave anything: TemporaryDirectory or cleanup on every path, failures included.
2. run_all_tests.py runs suites with their own temp folder (TMPDIR under one run folder) and removes it at the end; what a suite left is named in its result line, like the files it writes to ~/.local/bin.
3. One command removes the leftovers of past runs (only folders the test runs make, by their prefixes and age), with a dry run listing them first.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
