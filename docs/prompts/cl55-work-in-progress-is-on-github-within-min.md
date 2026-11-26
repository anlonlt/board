Board, part CL55: Work in progress is on GitHub within minutes, public repos included. Train `takeover`. Your step's post: `cl55-work-in-progress-is-on-github-within-min` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl55-work-in-progress-is-on-github-within-min`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Work in progress is always on GitHub within minutes, on every platform, public repos included: a session that crashes or hits its usage limit is continued from there by any platform. The save hook (kit/hooks/kit_wip.py, `.kit/landing.py save`, every 5 minutes, every runtime) pushes only to a repo known to be private; anlonlt/board is public (.kit/run/wip-private.json), so every save stays in the clone ("kept in this clone"): on a web session that is the container, lost with it. GitHub had no wip branch at all on 2026-11-26.

1. A save on a public repo is pushed too, after the secret check CL44's checkpoint already does (secret files and secret-looking values left out, named in the save's message); a save that would carry a secret is refused, said, never silent. In kit/landing.py.
2. A part's save goes to its step's branch (CL44 item 10's checkpoint, on a timer, not only when the part remembers), a non-part session's to wip/<branch>; both pushed, at most every 5 minutes, never waited for.
3. A takeover (CL49's claim, `launch --take`, web_takeover) starts from the newest of the step's branch and its last save, and says which one.
4. Tests: a public-repo fixture's save is pushed; one with a planted token is refused and says why; a takeover after a lost session continues from the save.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
