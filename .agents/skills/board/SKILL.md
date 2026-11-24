---
name: board
description: How an agy or Claude Code session reports on this project's local board and answers the owner there: the post at turn 1, columns, comments, typing dots, the git copy read through kit. Use at the start of any session that works on a task, when a "Board comment from the owner" arrives, or when asked about the board, the clerk or kit.
---

# board (from the kit, anlonlt/board — local: agy or the Claude Code CLI)

Read `.kit/BOARD.md` (how to post and answer via `.kit/board_cli.py`) before your first board write. The orchestrator also reads `.kit/ORCHESTRATOR.md` (agy) or `.kit/claude-cli/ORCHESTRATOR.md` (Claude Code CLI). The local board server is `.kit/board_server.py`; the board's url and every limit live in `.kit/config.json`.

What a finished post carries and which board feature fits which content (audio takes, pictures to compare, pictures and diagrams drawn in the body, a choice for the owner; never a docs file for your text): `.kit/BOARD.md` § The post format and § Which feature for what; `python3 -I .kit/kit.py done <post>` checks a post before Done. Answer the owner with the one `board_cli.py reply` command the clerk's message gives.

Lookups without dumping the board (`.kit/ORCHESTRATOR.md` § 3): `python3 -I .kit/kit.py post <id>`, `open`, `since <ISO>`, `session <post>`, after a sync.
