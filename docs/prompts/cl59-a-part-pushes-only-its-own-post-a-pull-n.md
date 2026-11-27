Board, part CL59: A part pushes only its own post; a pull never deadlocks on waiting batches. Train `board-data`. Your step's post: `cl59-a-part-pushes-only-its-own-post-a-pull-n` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl59-a-part-pushes-only-its-own-post-a-pull-n`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part sends its own post to the live board in one command, never other sessions' writes.

Owner, 2026-11-27 (screenshot of CL17's session): "How did it have so many failed commands? Was it stupid?" CL17 pulled only its own post (`artifact pull --post`), then `artifact push` diffed the whole local copy against that partial pull and built 330 unpinned writes; the push has no single-post option, the guard refuses a hand-made ArtifactData write, and refuses a pull while built batches wait. The part tried four ways and stopped. Web orchestrator 2 hit the same today: hand-filtering batches, conflict comments on _trains at every sync.

1. `kit.py artifact push --post <id>` (and the comments it wrote) builds only those writes, pinned to the versions read.
2. A push after a partial pull never sends documents the pull did not read.
3. A pull is allowed while batches wait: it rebuilds them after loading, never deadlocks.
4. The generated cards (_dashboard, _trains) are never a two-sided conflict: the newer build wins, no conflict comment.
5. Tests for each; replay CL17's case and count writes (1 post + its comments).

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
