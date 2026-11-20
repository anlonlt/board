# {{PROJECT}} — Agent Board Instructions (`{{RUNTIME}}`)

This repository uses the local board kit (`.kit/`). Read `.kit/BOARD.md`, `.kit/orchestrator.md`, and memory files under `{{MEMORY_DIR}}/state/*.md` and `{{MEMORY_DIR}}/awaits.md` before acting. Talk to the owner in **{{TALK}}**, on the board, and end each chat turn with a single `·`.

## 1. Start the board (if not already running)
1. Check `curl -sf {{BOARD_URL}}/_api/health`.
2. If not running, start in sandbox preview mode (`--preview` allows `0.0.0.0` without `--token` behind a trusted sandbox proxy, auto-seeds `_orchestrator`, and registers the default watcher; `--embed-mode` enables cookie-independent iframe support):
   ```bash
   python3 -I .kit/board_server.py --host 0.0.0.0 --port 8765 --preview --embed-mode
   ```
   *(For LAN/Tailscale outside a sandbox, omit `--preview` and pass `--token <secret>`.)*
3. Post the idempotent runtime welcome once:
   ```bash
   python3 -I .kit/kit.py welcome --runtime {{RUNTIME}} --post
   ```

## 2. Check loop (on `.` or each turn)
1. Fetch all unread owner comments, 👍 likes, and option taps in one command:
   ```bash
   python3 -I .kit/board_cli.py inbox
   ```
2. Reply in-thread (marks the owner's comment `read: true`, acks its thread, and posts your reply):
   ```bash
   python3 -I .kit/board_cli.py reply <post_id> --comment-id <owner_comment_id> --text "..."
   ```
3. Rebuild `_dashboard` / `_trains` after state changes (`python3 -I .kit/kit_cards.py`).
4. If a Git `origin` remote is configured, sync the mirror: `python3 -I .kit/kit.py sync-db --commit && python3 -I .kit/kit.py push` (or `kit.py receive` / `kit.py publish` for warm cross-runtime handoff).

## 3. Live listener (Arena / interactive turns)
- While in an active turn, arm the ~30-minute listener so incoming board comments wake you without requiring `.` in chat:
  ```bash
  python3 -I .kit/board_cli.py comments wait --timeout 1750
  ```
- **Platform limit:** once a turn ends, there is no 0-token background wake across turn boundaries — the owner sends `.` in chat to start the next turn.
