---
name: story
description: What this project keeps between sessions and where: where things stand is on the board (each step's post, question posts, issue posts; `kit.py resume`), what sessions run is in git, and .story/ holds only what no post holds: lessons, the workflow, preferences, the orchestrator's open timers. Use at the start of a session, at save points, and when the user says "/story", "where were we", "catch me up", "save", "hand off", "log an issue", "log a lesson".
---

# story: what is kept between sessions, and where (from the kit, anlonlt/board — agy local)

**Commit and push at the end of every turn that changed something.** The `Stop` hook blocks a stop once while work is uncommitted or unpushed.

## What lives where (the owner, 2026-11-23: "everything is on the board")
| what | where | never |
|---|---|---|
| where a step stands, its result, what is next | its **board post** (status: planned, in progress, held, closed, done); the train is the task list | a memory file |
| what waits on the owner | a `question` post (with its default) | an awaits list |
| a defect found and not fixed now | a **board post**, `kind: "issue"`, `status: "open"`, then `"closed (<commit>)"` | an issues folder |
| a decision the owner gives in chat | a comment from you on the post it concerns (or `_orchestrator`), the owner's words and the date | only the chat |
| what sessions run or build from | **git**: code, the kit, prompts (`docs/prompts/`), reports (`docs/`) | the board |
| what no post holds | **memory** (`.story/`): lessons, the workflow, preferences, the orchestrator's open timers | |

`python3 -I .kit/kit.py resume` reads where things stand from the board: the task list, questions included.

## Layout (inside `.story/`)
```
lessons/<date>-<slug>.md  a symptom, its cause, the remedy (change the tool/hook/prompt so it cannot recur)
workflow.md               runners, tools, accounts, limits (which runner runs which project: § Runners)
preferences.md            "- <how the owner wants things>" lines, printed at session start
state/orchestrator.md     the orchestrator's open timers only
before-compaction.md      written by kit_pre_compact.py on Stop; gitignored
```
An older project's `state/<workstream>.md`, `awaits.md` and `issues/` are read by nobody: `python3 -I .kit/kit.py compact` archives them once into `archive/` (post any open issue on the board first).

## Lesson
A mistake that cost time: `lessons/<date>-<slug>.md`: the symptom (words someone would grep), the cause, the remedy. A lesson whose remedy is "be careful" is not done: change the tool, the hook or the prompt so the mistake cannot recur, and name that change. `grep -ril '<symptom>' .story/lessons/` before diagnosing anything twice.

## Save and hand-over
Your post carries where your step stands (`.kit/BOARD.md`). Memory changes only for a lesson, the workflow, a preference or an open timer; commit it, then `python3 -I .kit/end_session.py`.
