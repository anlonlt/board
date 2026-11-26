Board, part CL44: Fix agy runtime gaps: Artifact STALE gate, empty board.db shadowing, worktree MCP config, agy usage ledger, and kit/up mode flag. Train `cli`. Your step's post: `cl44-agy-runtime-startup-mcp-usage-and-mirror-fixes` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl44-agy-runtime-startup-mcp-usage-and-mirror-fixes`: it prints this and how a part works here.

## The task
Fix the six agy runtime bugs surfaced during the live CL43 run on `feat/streamline-doctor-memory` (`3746aa8`):

1. `scripts/kit.py` (`stale_copy()`): do not block local `agy` or local CLI sessions with the Claude-Web `STALE: Artifact board` gate when `kit.artifact.url` is set alongside `kit.mode: "local"`.
2. `scripts/kit.py` (`_load_board_docs()`) & `kit/board/board_server.py`: fall back to `board-mirror` if `board.db` has 0 docs, and don't leave a 0-doc `board.db` behind when `board_server.py` fails to bind its port.
3. `install.py` (`merge_agy_mcp()`), `kit/board/mcp_stdio.py`, and `scripts/kit.py`: make `.agents/mcp_config.json` worktree-safe (no hardcoded parent checkout path), include `runner`-assigned planned/in-progress train steps in `board_queue`, and handle offline fallback cleanly in `board_inbox`.
4. `kit/usage.py` & `kit/hook.py`: parse `agy` `transcript.jsonl` (`PLANNER_RESPONSE` token counts + `tool_calls` + child subagents), support Gemini/agy models, and wire `kit_usage.py` into the `agy` `Stop` row of `TABLE`.
5. `kit/up`: handle `agy --mode auto` unavailability cleanly instead of silently falling back to interactive mode.
6. Run `python3 scripts/run_all_tests.py` (0 failures) plus a live local `board --agy` smoke test confirming `kit.py resume`, `board_queue(runner="agy")`, and a recorded `kit-usage` row.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
