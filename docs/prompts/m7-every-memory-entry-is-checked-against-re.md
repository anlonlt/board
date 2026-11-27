Board, part M7: Every memory entry is checked against reality. Train `memory`. Your step's post: `m7-every-memory-entry-is-checked-against-re` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m7-every-memory-entry-is-checked-against-re`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Every memory entry is checked against reality where it can be, so the memory cannot rot the way the French game's docs did (a doc older than 149 of the 168 commits under it, still ranked first).

1. A lesson or a decision names what enforces it: a test, a hook, a refusal in a command, or the code that implements it; `kit.py lessons check` runs those and flags an entry whose test is gone or fails, or whose code no longer exists. In scripts/kit.py and kit/ledger.py, tests in tests/test_memory_checks.py.
2. An entry with nothing to run says so: "unchecked since <date>", shown when it is given to a session (the memory hook, resume), never presented as certain.
3. A lesson whose remedy is "be careful" is refused (the memory guide already says so); a new lesson proposes its check.
4. The orchestrator's resume carries one line: entries checked, failing, unchecked.

Bars: on the kit's own memory, entries with a check out of all, and how many fail today.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
