Board, part CL73: The stop check counts only what this session changed: never 361 old copies of other posts. Train `board-data`. Your step's post: `cl73-the-stop-check-counts-only-what-this-ses` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl73-the-stop-check-counts-only-what-this-ses`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
The CL30 stop check (`kit/hooks/kit_board_guard.py` -> `kit.py artifact status` -> `artifact_unsent()` in scripts/kit.py) counts every local document with no sync base as "changed here": `base != data` is true when `base is None`. A session whose local board was seeded from git (board-mirror) or that pulled only its own post (`artifact pull --post`) has no base for the other ~360 posts and comments, so its Stop is blocked with "NOT SENT: 361 document(s)" and it is told to push old copies of posts it never touched (which, sent, would overwrite newer live ones or create conflict comments).

Fix: a document counts as unsent only when this session changed it: a base exists and differs, or the document was written locally (board_cli/kit write) after this copy's last load/pull. A document with no base that nobody wrote here is not unsent. `artifact push` follows the same rule (never sends a document this copy did not change).

Check: a test that seeds a board from a git mirror with 300 posts, loads one post with `--post`, edits that one, and asserts status says 1 changed (fails on main with 300+); and a stop in a session that changed nothing is not blocked.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
