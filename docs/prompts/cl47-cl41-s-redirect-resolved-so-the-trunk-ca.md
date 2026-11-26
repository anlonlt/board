Board, part CL47: CL41's redirect resolved, so the trunk can move. Train `board-data`. Your step's post: `cl47-cl41-s-redirect-resolved-so-the-trunk-ca` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl47-cl41-s-redirect-resolved-so-the-trunk-ca`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
CL41's redirect, so the trunk can move again: nothing reaches main until it is resolved (the pre-push guard refuses), and every step since 11-24 waits behind it on feat/streamline-doctor-memory.

CL41's review (comment CR-cl41-review-and-adopt-four-local-commits, 2026-11-26 01:36Z): "do not merge cl41 to main yet: it carries 33 commits main lacks (not 4), and with them 2 phone checks that pass on main fail on cl41". Its suggestion: make test_page_ux.js and test_install_present_runtimes.py pass as on main (bisect 590c15b..95f0eab for the 411 px fold), list the earlier computer commits on the post, and merge only after a full run shows 0 failed.

1. Make the two phone checks pass on the branch as they do on main (find the commit that broke the 411 px fold).
2. List on CL41's post the commits main lacks, one line each, so its review sees all of them.
3. A full run (`run_all_tests.py --all`, it resumes from where a stopped run left off on the same commit) with 0 failed, its result on the post.
4. Then ask for CL41's review again; on continue, `kit.py land` merges onto main.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
