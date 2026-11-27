Board, part R3: The kit suggests where a step runs, from the devices and platforms it remembers. Train `runners`. Your step's post: `r3-the-kit-suggests-where-a-step-runs-from` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r3-the-kit-suggests-where-a-step-runs-from`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The kit suggests where a step should run, from the devices and platforms it remembers, to save time and usage: a step waiting on a full computer while a web runner is free, or a non-sensitive research step that LMArena could take instead of a paid session.

1. The kit remembers the runners: each device (its RAM, its cap, what is running) and each platform the owner has (Claude Code CLI and web, agy, LMArena, Gemini, the browser parts), each with its profile from R1 and R2 (what it reaches, what it costs, its limits), in the workflow memory and the map (M9).
2. At each plan and each refused launch, the orchestrator's resume says in one line where a waiting step could run now and why ("M5 waits on penguin's RAM: Claude Code web can take it, it needs only git"), never a choice it cannot make: sensitive work (secrets, private data, the owner's accounts) is never routed to a platform that shares it.
3. A step can say what it needs (git, the local board, a browser, secrets, a GPU); the suggestion matches needs to profiles; `kit.py launch --web` or a runner card follows from the suggestion in one command.
4. Tests on a fixture with two devices and three platforms, in tests/test_runner_suggest.py.

Bars: on today's board, the waiting steps the suggestion places, out of all waiting.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
