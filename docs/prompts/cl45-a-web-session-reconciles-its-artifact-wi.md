Board, part CL45: A web session reconciles its Artifact with GitHub at its start. Train `board-data`. Your step's post: `cl45-a-web-session-reconciles-its-artifact-wi` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl45-a-web-session-reconciles-its-artifact-wi`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A Claude Code web session reconciles its Artifact with GitHub at its start, the same way `board` reconciles the local board with GitHub on a computer: each post and comment, the newer copy wins, nothing lost on either side.

Today the pieces exist (`kit.py artifact pull`, `load`, `kit.py reconcile` against board-mirror, `artifact push`), but nothing runs them together, so a web session found its Artifact missing the posts made on the computer (the memory train's M5 to M8, the runners train) and called the git copy out of date.

1. `kit.py artifact reconcile`: pull and load the Artifact, reconcile with origin/board-mirror (newer wins, per document), push what changed both ways (the Artifact batch, and board-mirror), and print one line: what came from GitHub, what went to it.
2. It runs by itself at a web session's start (the web's session-start hook or resume on the web), never in a local session (a local session reconciles its own board).
3. A document changed on both sides since the last reconcile is never overwritten silently: both versions are kept and the board shows the conflict.
4. A test with an Artifact copy and a board-mirror that each hold a post the other lacks, and one changed on both.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
