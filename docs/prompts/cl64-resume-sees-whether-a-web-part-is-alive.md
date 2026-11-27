Board, part CL64: Resume sees whether a web part is alive: never 'stalled' from an empty local list. Train `takeover`. Your step's post: `cl64-resume-sees-whether-a-web-part-is-alive` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl64-resume-sees-whether-a-web-part-is-alive`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "Why can't resume see web sessions? Aren't web sessions supposed to sync regularly? There was a part on that, no?"

Found: resume decides "running" with `_alive_sessions()` (scripts/kit.py ~6635), which reads `claude agents --json`: the Claude Code sessions on THIS machine only. In a cloud container that list is empty (not None), so the web fallback at ~6802 ("web session: check it with get_session") never fires, and every web part (session_...) is shown "not running", 30+ min quiet ones "stalled (continue it from its branch)". On 2026-11-27 03:25 orchestrator 3 was told to relaunch 12 parts that were alive (CL50, CL52, CL54, CL56, CL62 working; others review-ready). CL55 and CL45 sync a part's work and posts to GitHub and the board; nothing syncs whether its session is alive.

Done when:
1. A `session_` id is never "not running" from `claude agents` alone: on the web resume reads the liveness the orchestrator recorded from `list_sessions` (status_bucket working/blocked/review_ready/completed/failed), e.g. `kit.py sessions record` fed with the list_sessions JSON, kept under .kit/run with its time; without a record younger than 30 min it prints "web session: check it with list_sessions", never "stalled".
2. A part's own post carries a heartbeat (its live line or wip save time) and resume uses it: a part that wrote in the last 30 min is running whatever the platform.
3. Tests: a cloud container (empty `claude agents`) with session_ parts shows them by their recorded bucket; no record shows the check line; a local claude-cli part still uses `claude agents`. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
