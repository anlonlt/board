Board, part CL58: A delta resume still shows the web environment: CL39's test green with a session id. Train `cli`. Your step's post: `cl58-a-delta-resume-still-shows-the-web-envir` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl58-a-delta-resume-still-shows-the-web-envir`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
CL39's test is green again: a delta resume still shows what the orchestrator must act on.

CR-cl39 (redirect, 2026-11-27): `tests.test_cl39_web_multi.test_resume_has_no_add_repo_line` fails on main in any session with a session id: M5's delta resume (`_resume_snapshot`, scripts/kit.py) holds posts, comments and sessions only, so recording a web environment id prints "nothing new" and hides the Web environment section.

1. The resume snapshot covers every section an orchestrator acts on (the web environment record at least; list the others checked), so a change in one shows in the delta.
2. The test runs with and without a session id (both must pass); it fails on today's main with one set.
3. Run the full suite with CLAUDE_CODE_SESSION_ID set, as a web session has it, and say what else that turns red.

## What this step answers (the owner's words, or a review's)
- comment `CR-cl39-install-for-several-repos-on-the-web-one`: read it with `python3 -I .kit/board_cli.py get comments CR-cl39-install-for-several-repos-on-the-web-one --out-dir /tmp/src`
