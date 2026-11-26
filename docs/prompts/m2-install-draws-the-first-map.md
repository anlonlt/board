Board, part M2: Install draws the first map. Train `memory`. Your step's post: `m2-install-draws-the-first-map` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m2-install-draws-the-first-map`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Install draws the first map: the plumbing that already exists is on the map from day one. Waits on M1 (the map's format: things, links, flags; every link carries a check that runs).

1. Step 0's scan builds the first map with `kit.py map` and keeps it with the project, in scripts/step0_scan.py and install.py.
2. Install on a project with no map runs it once; `--update` refreshes it and says what changed (things and links added or gone, checks now failing).
3. The welcome and the orchestrator's resume name the map's health in one line: things, links, checks passing, checks unconfirmed.
4. Tests on a small project fixture, in tests/test_map_install.py with install.py.

Bars: build time at install on the kit itself and on a Harmonize-shaped copy; how many of its files the first map covers (out of all).

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
