Board, part R5: Every rule that differs by platform says where it applies, checked by a test. Train `runners`. Your step's post: `r5-every-rule-that-differs-by-platform-says` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r5-every-rule-that-differs-by-platform-says`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Every rule that differs by platform says where it applies, and a test catches a rule that leaks to another platform. At least eight times since 11-23 a rule made on one platform was applied to the others: the launchers' runtime (CL26), hooks copied into every runtime (CL28, CL37), the web's Artifact STALE gate on a computer (CL30, fixed in part by CL44 item 1), done's Artifact push on a computer (CL51), the web's "orchestrator runs the real-project check" on a computer (R4), Claude Code's hook reply sent to agy (CL44 item 9), a computer decision removing the web's cards agent (106bba6), the web's cards reminder in the CLI. Only the hooks were caught, because only they declare their platforms (the hook table's rows, and a test counting hooks per platform).

1. One answer to "where does this session run" (web, CLI, agy, LMArena-like, generic), the kit's `_platform()`, used by every rule; never inferred from the config (a config naming an Artifact says what exists, not where the session runs). In scripts/kit.py.
2. A table of what differs by platform, like the hook table: the Artifact steps, the real-project run, the cards (server or agent), the launcher, the reply format, the gates; each rule reads it instead of its own check.
3. A test that runs resume, done, launch and step new under each platform (faked as this morning's agy run: environment and parent process) and compares what each does with the table, in tests/test_platform_matrix.py; a new rule with no row is refused.
4. The part and orchestrator guides say: a change made for one platform names its row in the table.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
