Board, part M3: Done keeps the map: each part checks its stretch and adds what it built. Train `memory`. Your step's post: `m3-done-keeps-the-map-each-part-checks-its` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m3-done-keeps-the-map-each-part-checks-its`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Done keeps the map: every part checks its stretch of the plumbing and adds what it built or found, or its step is refused. Waits on M1.

1. `kit.py done` runs `kit.py map --check` and refuses on a check that fails, naming the link, in scripts/kit.py with tests/test_map_done.py.
2. It refuses when files the step changed are not on the map, or a new thing has no check (the part adds them, each link with its check).
3. A thing only a browser sees (a page in a web app, a sharing setting) carries a probe, an instruction the next browser part performs, and "last confirmed" with its date; past a number of days set in the kit config it shows as unconfirmed, never as fine.
4. The part's guides say it in two lines: confirm your stretch, add what you built with its check, in kit/part.template.md and kit/claude-cli/part.template.md.

Bars: on the kit itself, how many of the last 10 steps' changed files the map covers; how many seconds `done` spends on the map check.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
M3: builds on M1 (the map) and M7 (entries checked against reality); M3 makes kit.py done check the part's stretch of the map and add what it built.
