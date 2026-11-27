Board, part A1: install_cmd skips linked git worktrees so ~/.local/bin/<cmd> is never overwritten by a part. Train `agy`. Your step's post: `a1-install-cmd-skips-linked-git-worktrees-s` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post a1-install-cmd-skips-linked-git-worktrees-s`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
When a part subagent runs `install.py . --update` inside a linked git worktree (e.g. `~/.gemini/antigravity-cli/worktrees/...` or `.claude/worktrees/...`), `install_cmd()` in `install.py` only checks `under_tempdir(t)` and overwrites `~/.local/bin/<cmd>` to point at the ephemeral worktree, breaking the one-word launcher once the worktree is removed.

1. In `install.py` (`install_cmd`), detect linked git worktrees (`.git` is a file, or `git rev-parse --git-dir` differs from `--git-common-dir`, or path under `/.gemini/antigravity-cli/worktrees/` or `/.claude/worktrees/`) and skip writing `~/.local/bin/<cmd>` when `KIT_BIN_DIR` is not explicitly set, logging a clear `SKIPPED command ... is a git worktree` line (matching `under_tempdir`).
2. Add unit tests in `tests/test_install_worktree_cmd.py` covering: a linked git worktree skips `~/.local/bin/<cmd>` and leaves an existing launcher untouched; a main checkout still installs/updates it; explicit `KIT_BIN_DIR` still overrides the skip.

## What this step answers (the owner's words, or a review's)
- comment `a1b2d992af8d48c8af39`: read it with `python3 -I .kit/board_cli.py get comments a1b2d992af8d48c8af39 --out-dir /tmp/src`
