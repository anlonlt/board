Board, part M14: The usage ledger says what each review found: verdict and found-new per review row, cost per useful review. Train `memory`. Your step's post: `m14-the-usage-ledger-says-what-each-review-f` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m14-the-usage-ledger-says-what-each-review-f`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
The usage ledger (scripts/usage.py, `kit.py usage record|show|compare`) records each review session's cost but not what it found: 25 review rows, $81.42, none say whether the review was worth it. The owner asked for the ledger to show it.

Each review row gets two fields, filled from the review's own board comment (CR-<post>[-N]) and the step's post, never by a model call:
- `verdict`: continue | redirect | stop (parsed from the review comment's first line).
- `found_new`: yes | no, and `found` (one line): yes when the review named a problem that the part's post and `kit.py done` did not (a redirect, or a continue whose comment lists a gap/bug under its findings); no when it only re-ran the step's check and break and sampled a bar that the post already reported.

`kit.py usage show --role review` prints them per row, and a summary line: reviews n, cost $, found_new k of n, cost per review that found something. Backfill the 25 existing rows from the board's CR- comments (`usage import`-style, no transcripts needed). Test: seeded rows + CR comments (one redirect, one continue that only repeats the tests, one continue with a real gap) give verdicts and found_new yes/no/yes, and the summary line; it fails on main.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
M6: M6 counts each session's cost per command; M14 adds what a review session found (verdict, found-new) to its row, so cost per useful review shows.
