Board, part CL53: A session always starts on the newest code from GitHub, in every checkout. Train `takeover`. Your step's post: `cl53-a-session-always-starts-on-the-newest-co` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl53-a-session-always-starts-on-the-newest-co`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A session always starts on the newest code from GitHub, in every checkout: the rule "use git" covers the board's data (reconcile at start) and parts (6b69f1e: a part starts on its step's newest work), but not a project's own checkout. ~/harmonize sat on branch agy-board-install from 11-22 while Harmonize's main on GitHub had moved on (0d020efb, 11-24): a session opened there, and the kit's own real-project check, worked on two-day-old code with nothing saying so.

1. At `board` start and at the first resume of any role, the kit fetches origin and says, before anything else, when this checkout is behind its trunk or sits on a branch that is merged or stale (no commit for N days while the trunk moved): one line with what is newer.
2. When the checkout is clean and its branch is the trunk or merged into it, it moves forward by itself (fast-forward, or switch to the trunk); with uncommitted work or commits of its own, it never moves it: it says what to do, and resume names it as the first thing.
3. The same on every platform (a web clone is fresh already: nothing to do there), with a test: a clone behind origin is moved forward at start; one with uncommitted work is only warned.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
