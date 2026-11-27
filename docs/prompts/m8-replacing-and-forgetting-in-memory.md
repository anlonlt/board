Board, part M8: Replacing and forgetting in memory. Train `memory`. Your step's post: `m8-replacing-and-forgetting-in-memory` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m8-replacing-and-forgetting-in-memory`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Replacing and forgetting: an entry that replaces another names it, and the old one leaves the memory, so a session never meets two rules that disagree (the French repo: two docs each claiming to rank first).

1. A lesson, decision or principle can say `replaces: <id>`; the replaced one moves to the archive with a pointer to its successor, in kit/ledger.py with tests/test_memory_replace.py.
2. `kit.py lessons check` flags two live entries on the same files or theme that contradict each other (opposite verbs on the same subject, the same setting with different values) and asks which one wins.
3. An entry unconfirmed for longer than a number of days set in the kit config goes to a review list on the board, never deleted silently.
4. Guides and memory files say which one is the authority in one place only; a second claim of authority is flagged.

Bars: on the kit's and the French repo's memory, contradicting pairs found.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
