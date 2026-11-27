Board, part M11: Sessions use the kit's commands, the ledger shows when they don't, and the orchestrator is labelled as such. Train `memory`. Your step's post: `m11-sessions-use-the-kit-s-commands-the-ledg` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m11-sessions-use-the-kit-s-commands-the-ledg`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Every session uses the kit's own commands where one exists, the ledger shows when it does not, and the orchestrator is counted as the orchestrator.

Measured on 2026-11-26 (kit-usage rows, `kit.py usage show --commands`): the orchestrator session 9919b5f1 is labelled role `other`; it made 9 `ArtifactData list` calls of the whole board (116k chars) and parts made 31 (318k chars, 12 of 12 sessions), although `kit.py artifact pull` prints only what changed by default (`--full` for all). Parts read code with `sed` 99 times (507k chars). The orchestrator also read posts raw (`board_cli.py get` 15 times) and built pins by hand from full lists. Nobody checked whether sessions used the kit's commands.

1. The usage row labels the orchestrator `orchestrator` on the web and on a computer, whatever its start (the `_orchestrator` post's session_id, a hand-over, or a session that took over mid-way); a test with a transcript like 9919b5f1's.
2. Every full-board read goes through `kit.py artifact pull` (changes only); the pin and version-mismatch path re-reads only the documents it names (`get`/`query` by id), never a full `list`. Measure: the calls a pull and a refused batch print, before and after, on the real board.
3. `kit.py usage audit [--since T] [--role R]`: from the ledger's `cmd_seq`, lists each session's manual actions where a kit command exists (raw `ArtifactData list`/`get` of posts, `board_cli.py get`, `sed`/`cat` of board files, hand-built pins), with the command it should have used and the tokens it carried. The orchestrator runs it at each Done and at each check-in; resume --role orchestrator prints its top 3.
4. Run it on today's rows and put the table in the post.

## What this step answers (the owner's words, or a review's)
- comment `owner chat 2026-11-26: make a step to fix the role label and full board pulls; check sessions used the kit's commands`: read it with `python3 -I .kit/board_cli.py get comments owner chat 2026-11-26: make a step to fix the role label and full board pulls; check sessions used the kit's commands --out-dir /tmp/src`

## What this does differently
M11: builds on M6's per-command ledger (it measures cost) and M7's K3 (reuse the kit's commands, unchecked): M11 turns them into a check that flags each manual action where a kit command exists, fixes the orchestrator's role label, and stops full-board reads in pulls and pins.
