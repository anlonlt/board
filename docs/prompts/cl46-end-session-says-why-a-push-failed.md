Board, part CL46: end_session says why a push failed. Train `board-data`. Your step's post: `cl46-end-session-says-why-a-push-failed` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl46-end-session-says-why-a-push-failed`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
end_session says why a push to the trunk failed, never a reason it did not see.

A Claude Code web session pushed to main; the kit's pre-push guard refused (CL41's review said redirect), and `kit/end_session.py` printed "the trunk kept moving; run again": it retries the push and, when every try fails, prints that one sentence whatever git said (kit/end_session.py, near its last lines).

1. Print git's own refusal (the guard's reason, or the remote's) when the push fails, in kit/end_session.py.
2. Retry only when the trunk really moved (the push was rejected as not a fast-forward and a fetch shows new commits); a refusal by the guard or the remote is said at once, with what to do, never retried.
3. A test where the pre-push guard refuses: the guard's reason is printed and there is no retry; and one where the trunk moves once: it merges and pushes, in kit/test_end_session.py.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
