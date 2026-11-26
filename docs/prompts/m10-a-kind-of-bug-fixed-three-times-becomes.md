Board, part M10: A kind of bug fixed three times becomes a design question. Train `memory`. Your step's post: `m10-a-kind-of-bug-fixed-three-times-becomes` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m10-a-kind-of-bug-fixed-three-times-becomes`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A kind of bug fixed three times becomes a design question, never a fourth patch. Eight fixes since 11-23 were the same kind (a rule made for one platform applied to the others: CL26, CL28, CL37, CL44 items 1 and 9, CL51, R4, 106bba6) and nobody saw it until the owner asked; the memory's ledger already turns about 3 observations on one theme into a principle (`distil`, kit/ledger.py), but fixes never reach it: they stay in commits and posts.

1. Every fix records an observation in the ledger with its kind (a short theme, e.g. "platform leak", "silent error", "two copies out of sync"), at `kit.py done` for a step that fixes a bug, and from a commit whose message says it fixes one; in kit/ledger.py and scripts/kit.py.
2. When a kind reaches 3, the orchestrator's resume says so in one line and plans a design step for it (what all of them share and the change that makes the kind impossible), instead of the next patch.
3. Seed it from history: today's three kinds (platform leaks, silent errors, two copies with nothing keeping them equal) with their fixes, so the counts start true.
4. Tests on a fixture ledger: two fixes of one kind say nothing, the third raises it.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
