Board, part R1: Antigravity on the web: how far it runs the board kit, and its profile. Train `runners`. Your step's post: `r1-antigravity-on-the-web-how-far-it-runs-t` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r1-antigravity-on-the-web-how-far-it-runs-t`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Find out how far Antigravity on the web runs the board kit, and which profile it fits: like the Claude Code CLI (a computer: git, the local board, hooks), like Claude Code on the web (a GitHub clone: the board's git copy, no local server), like LMArena (no GitHub: the board's shared link only), or in between. This step runs on that platform itself (runner `antigravity-web`): the owner starts it there with this prompt.

1. Reach: can it clone the repo from GitHub, read `docs/prompts/` and the board's git copy (branch board-mirror), run python3, commit and push a branch? Say what worked, with the command and its output.
2. The board: can it run `python3 -I .kit/kit.py resume --role part --post <this post>`; write its result into its post (board_cli, or a commit to board-mirror); comment; tick a piece (`kit.py sub`)? For each: works, works another way (which), or not at all.
3. The rest of the kit: hooks (does the platform run any?), the live line, the usage ledger row, the memory given at the moment a file is edited, a save point, a takeover by another platform from where it stopped.
4. Limits that matter for routing: session length and usage limits, whether it can run in the background, files and secrets it can or cannot reach, what it costs.
5. The profile: one table, feature by feature (yes / another way / no), and the verdict: CLI-like, web-like, LMArena-like or in between, and which kinds of steps it should get (research, code, browser work, review).

Bars: board features that work out of all tried; minutes from start to the first write that reached the board.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
