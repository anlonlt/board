Board, part A2: Point _launch_line() and kit/orchestrator.template.md at kit.py launch for agy. Train `agy`, after `R7`. Your step's post: `a2-point-launch-line-and-kit-orchestrator-t` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post a2-point-launch-line-and-kit-orchestrator-t`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Follow-up from combined review CR-r7-r4-combined (`continue`):

1. Update `_launch_line()` in `scripts/kit.py` (and `.kit/kit.py`) and `kit/orchestrator.template.md` (plus its `.kit/orchestrator.md` copy via `install.py . --update --no-cmd`) so `agy` orchestrator guides and `step new` output point directly at `python3 -I .kit/kit.py launch <post>` instead of raw `invoke_subagent` instructions.
2. Update or add unit tests in `tests/test_launch_runtime.py` verifying `_launch_line()` returns `kit.py launch` for `agy`.

## What this step answers (the owner's words, or a review's)
- comment `CR-r7-r4-combined`: read it with `python3 -I .kit/board_cli.py get comments CR-r7-r4-combined --out-dir /tmp/src`
