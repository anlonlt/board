Board, part CL71: No merge friction: generated files out of git, a reviewed step lands without another review, main green on the web. Train `board-data`. Your step's post: `cl71-no-merge-friction-generated-files-are-no` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl71-no-merge-friction-generated-files-are-no`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
Owner, 2026-11-27 (screenshots of CL17, CL54, CL61 and a review): "why is there so much friction?"

Found (orchestrator 3, 03:40-07:30): every landing today hit the same loop. `.kit/manifest.json` and `.kit/artifact-files/files-board.json` are generated files committed in git. Every branch regenerates them, so every merge with a moved main conflicts in them (CL17, CL44, CL54, CL56). The save hook twice committed them WITH conflict markers. The part's fix (a regenerate commit) lands after its review, so `land` refuses and asks for a new review (CL17, CL54, CL56, CL57). Meanwhile two tests are red on main in a web container (test_launch_runtime, test_cl48_test_guard), so every part and review spends a run proving "fails on main too".

Done when:
1. The manifest and the files map are not tracked: built at install/update and at `artifact push` from the tree, never merged (.gitignore + `git rm --cached`, readers rebuild on demand).
2. The save hook never commits a file with conflict markers (refuses, says which).
3. `land` ignores commits after the review that touch only generated files.
4. The two red tests pass on main in a web container (or say precisely why they cannot).
5. Test: two branches that both change the kit merge with no manifest conflict; a post-review regenerate commit does not block land. `python3 scripts/run_all_tests.py --all` green.

Folded in (owner's go, 2026-11-27): CL65 and CL70, same goal: a reviewed step reaches main without another review.
6. (was CL65) A review records the commit it read on the post (`review_commit`) when it writes its verdict, whatever started the session; `land` takes the newest CR comment's commit when it is newer. CL57 needed 3 reviews because the second's commit was never recorded.
7. (was CL70) `land` and resume count a step landed when its reviewed commit is on the trunk, or a trunk merge names its branch, and mark it themselves; later commits on the branch are listed apart ("2 newer commits, not reviewed"). CL44, M5-M8 and CL41 were merged yet shown "not landed" (shared branch feat/streamline-doctor-memory; two test commits on cl44).
8. Tests for 6 and 7: a re-review recorded only in CR-...-2 lets land proceed; a merged branch with one later commit shows landed.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
CL71: builds on CL48 (test runs) and CL59 (part pushes), but touches neither: it takes the generated manifest and files map out of git, makes land count a reviewed commit on main (not the branch tip) and record a re-review's commit, and fixes the two tests red on main in a web container.
