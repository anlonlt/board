Board, part M6: The usage ledger counts cost per command. Train `memory`. Your step's post: `m6-the-usage-ledger-counts-cost-per-command` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m6-the-usage-ledger-counts-cost-per-command`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The usage ledger counts cost per command, so a waste like rereading the board shows up as a number.

1. For each tool result, the ledger records its size (characters and estimated tokens) grouped by command: `kit.py resume`, `kit.py post`, `kit.py train`, the tests, git, reads of files; in scripts/usage.py with tests/test_cl37_usage.py.
2. It estimates what each one costs afterwards by staying in the context (its tokens times the calls that followed, at the cache read price).
3. `usage.py show --commands` and `compare --commands` print the top commands by cost, per role.
4. Commands repeated across sessions (the same sequence in 3 sessions or more) are listed as candidates for a script, with how often and what they cost.

Bars: on this computer's transcripts, the share of an orchestrator's tokens spent on resumes and the five most repeated sequences.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
