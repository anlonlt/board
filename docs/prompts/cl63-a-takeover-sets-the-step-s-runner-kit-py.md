Board, part CL63: A takeover sets the step's runner: kit.py launch --take records who runs it. Train `takeover`. Your step's post: `cl63-a-takeover-sets-the-step-s-runner-kit-py` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl63-a-takeover-sets-the-step-s-runner-kit-py`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner asked (2026-11-27 03:22, 03:24): why was R5 still shown as claude-web after an agy took it over, and why say "a good gap to close" instead of adding a part.

Found: `kit.py launch --take` (scripts/kit.py ~5335) writes status, session_id, branch, working_by, updated_at but never `runner`; and on a computer `kit.py launch` hard-codes `claude --bg`, so an agy takeover (invoke_subagent) has no kit command to record itself and the orchestrator edited session_id by hand.

Done when:
1. `kit.py launch <post> --take [--session-id ID] [--runner R]` writes the post's `runner` (detected from the running platform when not given) along with session_id.
2. On a computer under agy, `kit.py launch` prints the agy launch (invoke_subagent) instead of `claude --bg`, then records the take with the subagent's id.
3. `kit.py resume` flags a step whose runner disagrees with its session's platform (runner claude-web, session id an agy uuid).
4. Tests: a take from agy on a post planned claude-web ends with runner agy; a mismatch shows in resume. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
- comment `87cc065f6207400db520`: read it with `python3 -I .kit/board_cli.py get comments 87cc065f6207400db520 --out-dir /tmp/src`
- comment `a8fe6866519d4719b283`: read it with `python3 -I .kit/board_cli.py get comments a8fe6866519d4719b283 --out-dir /tmp/src`
