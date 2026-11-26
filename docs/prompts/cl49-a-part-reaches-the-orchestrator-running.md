Board, part CL49: A part reaches the orchestrator running now, whoever it is. Train `takeover`. Your step's post: `cl49-a-part-reaches-the-orchestrator-running` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl49-a-part-reaches-the-orchestrator-running`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part reaches the orchestrator that is running now, whoever it is. CL44's part said Done and messaged `board-orchestrator`, the fixed name in the part guide (`{{SLUG}}-orchestrator`); the orchestrator was then a Claude Code web session that had taken over (its id is in `_orchestrator`'s session_id), and the name matched two dead sessions on another computer, last active 3 days ago. The Done never arrived.

1. `kit.py resume --role part` prints the address of the orchestrator now: the session in `_orchestrator`'s session_id (any platform), and the part guides use it instead of a fixed name, in kit/part.template.md and kit/claude-cli/part.template.md.
2. A takeover writes its own address there before anything else (the takeover steps already write session_id: check every platform does).
3. When the send fails or the address names no live session, the part says so in its post (`orchestrator_unreached`), and the orchestrator's resume lists it.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
