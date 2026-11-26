Board, part CL49: A part reaches the orchestrator running now, whoever it is. Train `takeover`. Your step's post: `cl49-a-part-reaches-the-orchestrator-running` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl49-a-part-reaches-the-orchestrator-running`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part reports to the orchestrator that started it, on the device that started it, unless another orchestrator has claimed that part. CL44's part said Done and messaged `board-orchestrator`, the fixed name in the part guide (`{{SLUG}}-orchestrator`): it was launched by the orchestrator on the owner's computer (a Claude Code CLI session), and the name matched two dead sessions on another computer, last active 3 days ago. The Done never arrived. A web orchestrator had taken over the board meanwhile, but had not claimed CL44: CL44 still belonged to the orchestrator that launched it.

1. `kit.py launch` records on the post who owns the part: the launching orchestrator's session (its address) and its device (`orchestrator`, `orchestrator_device`).
2. The owner changes only by a claim: an orchestrator takes a part explicitly (`kit.py claim <post>`: it writes its own address on the post and tells the part), or a successor orchestrator on the same device, after a hand-over (session limit, reset), inherits every part its predecessor owned there. Taking over the board alone claims nothing.
3. `kit.py resume --role part` prints where to report: the post's owner now; the part guides use it instead of a fixed name, in kit/part.template.md and kit/claude-cli/part.template.md.
4. When the send fails or the owner names no live session, the part says so in its post (`orchestrator_unreached`), and every orchestrator's resume lists such parts so one can claim them.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
