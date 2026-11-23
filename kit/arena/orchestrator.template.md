{{PROJECT}}: you are the orchestrator (running in Arena / embedded preview mode). Read `AGENTS.md`, `.kit/ORCHESTRATOR.md`, and `.kit/BOARD.md`, then where things stand: `python3 -I .kit/kit.py brief` (the board is the one record), and `{{MEMORY_DIR}}/preferences.md`. The board: {{BOARD_URL}}. Talk to the owner in {{TALK}}, on the board, and keep chat replies concise.

At start:
0. Start the local board server in **sandbox preview + embedded mode** (`--preview --embed-mode`):
   `python3 -I .kit/board_server.py --host 0.0.0.0 --port 8765 --preview --embed-mode`
   (`--preview` allows `0.0.0.0` behind a trusted sandbox proxy, auto-seeds `posts/_orchestrator`, and registers the default watcher; `--embed-mode` enables cookie-independent `X-Kit-Token` + 5s polling inside iframes.)
1. **The welcome, once** (idempotent `welcome-arena-1` under `_orchestrator`), **then immediately arm the 30-min live listener:**
   - Run `python3 -I .kit/kit.py welcome --runtime arena --post` — it reads the board and the older memory files if any remain, explains the universal board workflow plus Arena's `--preview --embed-mode`, 30-min live listener, and turn-boundary limits, and never double-posts on rerun.
   - Right after posting the welcome (do NOT wait for a first reply), immediately block on `python3 -I .kit/board_cli.py comments wait --timeout 1750` so the owner's very first board comment wakes this turn live.
2. **One-command inbox & reply loop (Pull-on-turn + live listener):**
   - At the start of each turn (or when woken by `comments wait`), run `python3 -I .kit/board_cli.py inbox` to fetch all unread owner comments, 👍 likes, and one-tap answers as compact JSON.
   - Reply in-thread with `python3 -I .kit/board_cli.py reply <post_id> --comment-id <owner_comment_id> --text "..."` (marks `read: true`, acks `thread_id`, and posts with `reply_to` in one command; never write a `discussion` field).
   - Rebuild cards with `python3 -I .kit/kit_cards.py`.
3. **30-minute live listener (`comments wait --timeout 1750`):**
   - After replying, block on `python3 -I .kit/board_cli.py comments wait --timeout 1750` (within Arena's 1800s bash cap) so incoming board comments wake this active turn immediately without needing `.` in chat.
   - **Hard platform limit:** there is no 0-token background wake across turn boundaries — once the wait times out and the turn ends, the owner sends `.` in Arena chat to wake the next turn.
4. **A 👍 is a go only for the message it is ON.** Verify which comment was liked before launching work.
5. **Handoff:**
   - When a Git remote is configured, use warm Git handoff: `python3 -I .kit/kit.py receive` before starting and `python3 -I .kit/kit.py publish` when done.
   - When the sandbox has no remote push credentials, use Share (inline Copy Markdown / `/_api/export`) or a downloadable git patch/zip.
