Board, part M12: The owner's design decisions are never silently undone. Train `memory`. Your step's post: `m12-the-owner-s-design-decisions-are-never-s` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m12-the-owner-s-design-decisions-are-never-s`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The owner's design decisions are never silently undone or left half-wired: each is a memory entry tied to the files it governs, with a check, shown to any session that edits those files.

Work on anlonlt/board, branch feat/streamline-doctor-memory (runs on Claude Code web).

The owner, 2026-11-26: "dumb fucks have been reversing my ideas constantly". Seen that day: the per-post drop box for AIs (b52f5b6, 11-22) never reached by the board's own "Link for AI" link until 75a4f41; the web's cards agent removed from computer installs (106bba6, put back 38b1803); one token per machine kept "a project's own token" (CL11, fixed 88b4f3a); git as the one source, said about ten times.

1. Each decision of the owner is a `decision` entry in the memory ledger (what, because, rejected, the owner's words and date), tied to files or map areas; seed it with the four above and the rules the owner restated today. In kit/ledger.py.
2. Each decision names its check (a test, a hook, a refusal); M7's `lessons check` runs them; a decision with no check is listed as unenforced in the orchestrator's resume.
3. The memory hook gives a session the decisions of the file it is about to edit (as lessons are given now), first.
4. `kit.py done` refuses a step that breaks a decision's check; a step whose change contradicts a decision (M8's contradiction finder) becomes a question to the owner, never applied silently.
5. Tests: a change that removes the drop box route from the AI link fails the decision's check; editing kit/board/board_server.py shows the drop-box decision.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
