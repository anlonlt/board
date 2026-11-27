Board, part M9: The workflow on the map: what lives outside the repo, with checks. Train `memory`. Your step's post: `m9-the-workflow-on-the-map-what-lives-outsi` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m9-the-workflow-on-the-map-what-lives-outsi`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The workflow on the map: what lives outside the repo (runners, accounts, browser profiles, external services, their ids and links) becomes things and links with checks, and step 0 fills it at install. Today the workflow memory is wired everywhere (session start, the orchestrator's choice of runner, the runner prompt) but nothing writes it, and none of the owner's projects has one. Waits on M1.

1. Step 0 writes the workflow's first version from what it finds: CLIs and runners on the computer, git remotes, deploy configs, services named in the code's config, data sources; the owner confirms or corrects it in one board question. In scripts/step0_scan.py and scripts/kit_map.py.
2. Each becomes a thing on the map with links to the code that uses it (a data source in the code linked to the external database; a deploy config linked to its project) and a check: reachable, logged in, still exists; for what only a browser sees, a probe with its last confirmed date.
3. Direct references live there too: URLs, project and workspace ids, which account or profile reaches what, where a secret is kept (never the secret).
4. The workflow memory file keeps only what cannot be checked: the cycle of a change, the limits, the owner's habits; short, generated headings, pointing at the map for the rest.
5. A part that finds a new service or data source adds it at done, with its check (M3's rule).

Bars: on the French repo and on Harmonize, the external things found, and how many checks pass.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
Builds on M1 (map format: things, links, flags, runnable checks) and M4 (non-code map readers). Unlike M1/M4 (which map files inside a repo) and M3 (which enforces map updates at done), M9 maps what lives outside the repo (CLIs/runners, git remotes, deploy configs, external services, data sources, accounts/URLs) into .board/map and generates the concise workflow memory file via scripts/step0_scan.py and scripts/kit_map.py, measured on ~/french and ~/harmonize.
