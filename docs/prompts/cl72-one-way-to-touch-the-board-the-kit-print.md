Board, part CL72: One way to touch the board: the kit prints every call, the guard refuses anything else, the audit and guard agree. Train `board-data`. Your step's post: `cl72-one-way-to-touch-the-board-the-kit-print` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl72-one-way-to-touch-the-board-the-kit-print`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
Owner, 2026-11-27: "Why are there contradicting rules? The commands are there for a reason. No session may do anything by hand."

Found (orchestrator 3, usage audit 03:00-07:30): 22 of 22 sessions did board work by hand, because three kit pieces disagree. The guard (CL30, M11) refuses only a raw whole-board list and a raw post write. `kit.py artifact push` and `pin` (CL31) TELL the session to make `ArtifactData get` calls by hand (to learn a live version) and to send the batch by hand. The first `artifact pull` prints a whole-board list. The audit (M11) then counts all of those as manual. So a session following the kit word for word is flagged, and a session working around it is not stopped.

Done when:
1. Every board read and write a session needs goes through one kit command with no hand step between: `kit.py artifact sync` (pull the changes, load, push the session's own documents, pin a refused one by re-reading it, retry, record sent), printing at most one ArtifactData call set the model must make, with exact args, and nothing to edit.
2. The guard refuses every ArtifactData call and board_cli read/write that is not exactly the one the kit just printed (same collection, doc_id, version, file), for every role, the orchestrator included; the refusal names the command.
3. The audit and the guard use one shared list of allowed calls, so nothing the kit tells a session to do is ever counted as manual.
4. Test: replay today's orchestrator transcript's board work: 0 manual actions, 0 refusals of kit-printed calls, a hand-edited batch refused. `python3 scripts/run_all_tests.py --all` green.

5. Owner, 2026-11-27: "Why do you skip stuff? It's not the first time." Orchestrator 3 skipped resume's "First: what keeps coming back" (plan a design step for "two copies out of sync", fixed 6 times) because it was text, not a gate. `kit.py launch` and `kit.py land` refuse while an item of that section has no `kit.py ledger design` entry, naming the item and the command. Test: an open recurring item blocks launch; recording its design unblocks it.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
CL72: builds on CL30 (guard), CL31 (pin), M11 (audit) and CL59 (part push), which each patched one path; it replaces the hand steps with one kit command, makes the guard refuse anything the kit did not print, gives guard and audit one list, and gates launch/land on recorded design steps.
