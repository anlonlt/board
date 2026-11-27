Board, part M12: The orchestrator hands over at its context limit however its session began. Train `memory`. Your step's post: `m12-the-orchestrator-hands-over-at-its-conte` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m12-the-orchestrator-hands-over-at-its-conte`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The orchestrator hands over at its context limit however its session began.

Measured on 2026-11-27: orchestrator session 9919b5f1 reached 521k tokens (limit 380k) with no hand-over. kit/hooks/kit_pre_compact.py takes the role from the session's first user message only; this session began with the owner's question, took over as orchestrator later (on _orchestrator), so the hook said "other" and never blocked the stop. M11 fixed the same mistake for the usage row's label (role from what the session ran).

1. kit_pre_compact.py (and every hook that decides a role) uses one role function, the one M11 gave the usage row: the `_orchestrator` post's session_id, a hand-over, or a takeover mid-session all count.
2. A test with a transcript that starts with an owner question, then takes over, then passes 380k: the Stop is blocked once with the hand-over steps.
3. Run it on 9919b5f1's transcript and put the result in the post.

## What this step answers (the owner's words, or a review's)
- comment `owner chat 2026-11-27: why hasn't the hook made you hand over? / did you add the step?`: read it with `python3 -I .kit/board_cli.py get comments owner chat 2026-11-27: why hasn't the hook made you hand over? / did you add the step? --out-dir /tmp/src`

## What this does differently
M12: builds on M11, which fixed the role label in the usage row only; M12 makes the hand-over hook use that same role function, so an orchestrator that took over mid-session hands over at 380k.
