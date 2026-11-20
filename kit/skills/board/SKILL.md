---
name: board
description: How an agy session reports on this project's local board and answers the owner there: the post at turn 1, columns, comments, typing dots, the git copy read through kit. Use at the start of any session that works on a task, when a "Board comment from the owner" arrives, or when asked about the board, the clerk or kit.
---

# board (from the kit, anlonlt/board — agy local)

Read `.kit/BOARD.md` (how to post and answer via `.kit/board_cli.py`) before your first board write. The orchestrator also reads `.kit/ORCHESTRATOR.md`. The local board server is `.kit/board_server.py`; the board's url and every limit live in `.kit/config.json`.

Lookups without dumping the board (`.kit/ORCHESTRATOR.md` § 3): `python3 -I .kit/kit.py post <id>`, `open`, `since <ISO>`, `session <post>`, after a sync.
