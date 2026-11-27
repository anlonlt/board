Board, part CL78: A part's branch holds only its own work and main: a push or done that carries another unlanded step is refused, so every reviewed step lands at once. Train `reviews`. Your step's post: `cl78-a-part-s-branch-holds-only-its-own-work` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl78-a-part-s-branch-holds-only-its-own-work`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
Owner, 2026-11-27: "Shouldn't every part work cleanly on their own part so that things can be merged instantly? This isn't the first time a part has been held back by some other part's shit." Twice today: CL59's branch carried CL16's unlanded commits (held ~5 h), and CL61's part ran `git merge origin/m12` into cl61 (4208a7d8) to use M12's role function, so CL61, reviewed `continue`, cannot land until M12's redirect is fixed. The pre-push guard already refuses main holding an unreviewed step; nothing refuses a part's own branch taking one.

Change:
1. The pre-push / save hook refuses a push of a step's branch that contains commits reachable only from another open step's branch (not from main), naming the step and the merge commit; the part's way out is printed: build on main only, or say it needs the other step and wait (`kit.py step differs` / `after`), or copy the one function it needs in its own commit.
2. `kit.py done` refuses the same, so it never reaches review.
3. The part guide says it in one line.
4. M10's repeat counter gets the kind ("a branch carries another step's work") so a third case is flagged.
Fix now: CL61 drops M12's merge (rebuild cl61 on main with its own commits, copying what it uses from M12 or waiting), CL59 drops CL16's.

Check: fixture repo: a part branch merging another open step's branch is refused at push and at done, naming it; a branch merging only main passes; a step marked `after` the other may build on it only once that one lands. Replay cl61 at 4208a7d8: refused. Fails on main.


5. Why the repeat check missed it (owner asked 2026-11-27): `ledger.fix_kinds()` (kit/ledger.py) counts only `fix` entries someone writes into the memory ledger by hand; a land refusal, a review redirect or a blocked step only ever lands in a post or comment, so the counter had nothing to count. Fix: every refusal of `kit.py land` / `done` and every review `redirect` writes a ledger `fix` entry by itself (no model call), its theme from the refusal's kind (`branch carries another step`, `conflict in generated files`, `commits after review`, `stale board copy`...) and its step, so the third of a kind raises "what keeps coming back" on its own. Check adds: replaying today's two cases writes two entries of one theme; a third raises it in resume.

6. `kit.py done` still refuses whenever main moved (scripts/kit.py ~4842, "the trunk moved since you started: merge it first"), so every landing sends every running part into a merge-and-rerun loop (CL72 at 04:02 today, CL60 three times). CL71 fixed the generated files and land after review, not this. Change: done does the merge itself in a scratch worktree and runs the check there; it asks the part to merge only when that merge conflicts or the check fails merged. Check adds: a part whose files don't overlap main's new commits passes done with no merge of its own.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
