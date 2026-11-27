Board, part CL51: A session on a computer never writes to the Artifact. Train `board-data`. Your step's post: `cl51-a-session-on-a-computer-never-writes-to` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl51-a-session-on-a-computer-never-writes-to`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A session on a computer never writes to the Artifact. CL44's part (a local Claude Code session) sent its post to the web board: `kit.py done` refuses Done when the config names an Artifact and the post differs from the Artifact's last copy ("the post is not on the board yet"), whatever the platform. The owner's rule: the Artifact is the Claude Code web session's; a local session works with the local board, which reaches GitHub, and the web session reconciles its Artifact with GitHub (CL45).

1. `kit.py done` checks the Artifact only on the web; on a computer it checks that the post is on the local board and in the git copy (board-mirror) instead.
2. The same for every Artifact step a local session is told to do (the STALE gate, launch's Artifact batch, the task-train hook's "push"): none on a computer.
3. Tests: a local done with an Artifact named in the config passes without any Artifact call; on the web it still asks for the push.
4. The damage it already did: CL44's part (a local session) ran `kit.py artifact load` at 2026-11-26T20:23Z, made to by done; load wrote the Artifact's older copies over 11 newer posts of the local board (CL43 back to planned, CL32 and CL39 to in progress, CL16 and CL17's train moves undone, _orchestrator and _prefs), and the server pushed that to board-mirror (47cd73a). `artifact load` keeps the newer copy of each post and comment (by updated_at), takes only fields the other copy lacks, and never lowers a status; a test loads an older Artifact copy over a newer local board and nothing goes back.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
