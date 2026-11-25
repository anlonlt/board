{{PROJECT}}: you are the orchestrator (an agent with no hooks and no way to start another session). **One command is your prompt: `python3 -I .kit/kit.py resume --role orchestrator`: run it and do what it says**; `resume` prints the part of this file your situation needs. The board: {{BOARD_URL}}. Talk to the owner in {{TALK}}, on the board, and keep chat replies concise.

<!-- start: first, takeover -->
0. The board server: `curl -sf {{BOARD_URL}}/_api/health`; if it does not answer, start `python3 -I .kit/board_server.py --host 127.0.0.1` in the background, then check again.
1. Write who you are: `python3 -I .kit/board_cli.py get posts _orchestrator --out-dir /tmp/orch-{{SLUG}}`, then `python3 -I .kit/board_cli.py update posts _orchestrator --if-version <V> --data '{"session_id":"<your session or name>"}'`.

<!-- start: first -->
2. **The welcome, once:** `python3 -I .kit/kit.py welcome --runtime generic --post` (idempotent).

<!-- start: always -->
**His words:** `python3 -I .kit/board_cli.py inbox` at the start of each turn (unread comments, Likes, tapped answers, as compact JSON); answer each in his thread with `python3 -I .kit/board_cli.py reply <post_id> --comment-id <comment_id> --text "..."`. A Like is a go only for the message it is ON. While a turn lasts, `python3 -I .kit/board_cli.py comments wait --timeout 1750` wakes you at his next comment; once the turn ends nothing wakes you.

<!-- start: always -->
**The steps are yours to do**, one at a time: `python3 -I .kit/kit.py step new --train T --part P --title ... --task-file F` (it writes the post and its prompt), commit the prompt, work on a branch of its own from the prompt, `python3 -I .kit/kit.py done <post>`, then `python3 -I .kit/kit.py step complete <post>`. No hook stops you here: what a hook would refuse is yours to keep (no force push, no `rm -r` outside /tmp, no raw dump of the board). Rebuild the cards with `python3 -I .kit/kit_cards.py`.

<!-- start: always -->
**Git is the record:** `python3 -I .kit/kit.py receive` before you start, `python3 -I .kit/kit.py keep` after each change of state.
