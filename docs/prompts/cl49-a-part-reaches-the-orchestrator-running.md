Board, part CL49: A part reaches the orchestrator running now, whoever it is. Train `takeover`. Your step's post: `cl49-a-part-reaches-the-orchestrator-running` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl49-a-part-reaches-the-orchestrator-running`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part reports to the orchestrator that started it, on the device that started it, unless it has been taken over since. CL44's part said Done and messaged `board-orchestrator`, the fixed name in the part guide (`{{SLUG}}-orchestrator`): it was launched by the orchestrator on the owner's computer (a Claude Code CLI session), and the name matched two dead sessions on another computer, last active 3 days ago. The Done never arrived.

1. `kit.py launch` records on the post who launched it: the orchestrator's session (its address) and its device (`launched_by`, `launched_on`).
2. `kit.py resume --role part` prints where to report: the launcher, unless `_orchestrator` shows a takeover after the launch, then the orchestrator now; the part guides use it instead of a fixed name, in kit/part.template.md and kit/claude-cli/part.template.md.
3. A takeover writes its own address and time in `_orchestrator` before anything else (check every platform does), and tells the running parts its address.
4. When the send fails or the address names no live session, the part says so in its post (`orchestrator_unreached`), and the orchestrator's resume lists it.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
