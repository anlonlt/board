Board, part CL65: A re-review's commit is the one land checks: CL57 refused after a continue at its tip. Train `takeover`. Your step's post: `cl65-a-re-review-s-commit-is-the-one-land-che` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl65-a-re-review-s-commit-is-the-one-land-che`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Found 2026-11-27 03:35 by orchestrator 3: CL57's second review (comment CR-cl57-the-board-s-flowchart-draws-the-post-s-g-2) read the branch tip 85884a8 and said continue, but the post's `review_commit` still names the first review's 103a53e, so `kit.py land` (kit/landing.py ~158, `seen` = post.review_commit) refuses with "1 commit(s) on cl57 after its review" and asks for a third review. A review session started outside `kit.py review --force` never writes the commit it read.

Done when:
1. A review records the commit it read on the post (`review_commit`) when it writes its verdict (the CR comment carries `commit`, and `kit.py review`'s verdict path writes it whatever started the session).
2. `land` takes the commit from the newest CR comment of the step when it is newer than `review_commit`.
3. Test: a second review at the tip, recorded only in its CR-...-2 comment, lets land proceed; a commit after that review is still refused. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
