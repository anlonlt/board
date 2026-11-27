Board, part R4: On a computer, a part runs its own real-project check before Done. Train `runners`. Your step's post: `r4-on-a-computer-a-part-runs-its-own-real-p` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post r4-on-a-computer-a-part-runs-its-own-real-p`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
On a computer, a part runs its own real-project check before Done; only on the web does the orchestrator run it. The rule "a part never attaches a repo, the orchestrator runs the real-project check" (K2, CL38, CL39) comes from the web: a launched web session cannot attach another repo, and only the web orchestrator's environment holds every repo. On a computer every project is a folder: M1's part read ~/french itself, and the orchestrator ran Harmonize's map in 13 s. Yet `kit.py done` says "pending: the orchestrator runs it" on every platform (check_real_project in scripts/kit.py), so CL44 and M1 waited on the orchestrator for a run they could have done.

1. `kit.py done` on a computer (CLI or agy): the part runs the changed command on the consuming project itself, in a scratch clone or read-only, never pushing to it, and writes {"project", "command", "output"} in real_project; done refuses Done until it is there. On the web, it stays the orchestrator's (pending), as now.
2. The part guides and the lesson K2 say which platform does what, in kit/part.template.md, kit/claude-cli/part.template.md and the lessons.
3. Tests: a local done with real_project missing is refused with how to run it; a web done keeps "pending: the orchestrator runs it".

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
Builds on R5's central PLATFORM_RULES table and branches on top of origin/main + origin/r7 so R7 and R4 can be reviewed together. Unlike R5 (centralized platform detection, kept real_project_run='computer') and R7 (wired kit.py launch to PLATFORM_RULES and agy), R4 switches real_project_run for local computer runners (cli/agy) to 'part', enforces real_project in kit.py done on a computer, updates part templates and lesson K2, and adds tests.
