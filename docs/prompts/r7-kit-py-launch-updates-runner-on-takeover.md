Board, part R7: kit.py launch updates runner on takeover and supports agy launches. Train `runners`, after `R5`. Your step's post: `r7-kit-py-launch-updates-runner-on-takeover` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r7-kit-py-launch-updates-runner-on-takeover`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
When a step is launched or taken over on a computer (CLI or agy), kit.py launch must update the post runner field to the actual runner taking it (agy, claude-cli, or claude-web) and support agy launches/takeovers without hard-coding claude --bg.

1. In scripts/kit.py (`launch`), update `runner` in the post fields on every launch and `--take` (`claude-web`, `agy`, or `claude-cli`) so a step planned for one runner never stays stale after another runner takes it over.
2. When running on agy (or with `--cli agy`), `launch` in scripts/kit.py checks device capacity, prepares or adopts the step branch, records the takeover cleanly (`runner: "agy"`), and prints the exact `invoke_subagent` call instead of spawning `claude --bg`.
3. Tests in `tests/test_launch_runtime.py`: takeover updates `runner` from `claude-web` to `agy` and `claude-cli`; agy launch/takeover never invokes `claude --bg`; `--web` launch keeps `runner: "claude-web"`.

## What this step answers (the owner's words, or a review's)
- comment `a8fe6866519d4719b283`: read it with `python3 -I .kit/board_cli.py get comments a8fe6866519d4719b283 --out-dir /tmp/src`
