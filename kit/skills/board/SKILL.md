---
name: board
description: How an agy or Claude Code session reports on this project's local board and answers the owner there: the post at turn 1, columns, comments, typing dots, the git copy read through kit. Use at the start of any session that works on a task, when a "Board comment from the owner" arrives, or when asked about the board, the clerk or kit.
---

# board (from the kit, anlonlt/board — local: agy or the Claude Code CLI)

A step's post is a form (`python3 -I .kit/kit.py form <post>`, then `kit.py done <post>`): nothing to read first. Any other post: `.kit/BOARD.md` § Post. The orchestrator starts from `python3 -I .kit/kit.py resume --role orchestrator`, not from a guide file. The local board server is `.kit/board_server.py`; the board's url and every limit live in `.kit/config.json`.

What a finished post carries and which board feature fits which content (audio takes, pictures to compare, pictures and diagrams drawn in the body, a choice for the owner; never a docs file for your text): `.kit/BOARD.md` § The post format and § Which feature for what; `python3 -I .kit/kit.py done <post>` checks a post before Done. Answer the owner with the one `board_cli.py reply` command the clerk's message gives.

Lookups without dumping the board (`.kit/ORCHESTRATOR.md` § 3): `python3 -I .kit/kit.py post <id>`, `open`, `since <ISO>`, `session <post>`, after a sync.
