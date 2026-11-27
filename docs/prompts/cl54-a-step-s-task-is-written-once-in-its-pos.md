Board, part CL54: A step's task is written once, in its post; the prompt is generated at launch. Train `takeover`. Your step's post: `cl54-a-step-s-task-is-written-once-in-its-pos` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl54-a-step-s-task-is-written-once-in-its-pos`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A step's task is written once, in its post; the prompt file is generated from the post at launch, never edited by hand. Today `step new` writes the post and docs/prompts/<step>.md at once, and the kit never overwrites the prompt: changing a task means editing both by hand (on 2026-11-26 CL49, M10, R6 and CL51 were each edited twice), and the two can drift apart.

1. `step new` writes the post only; `kit.py launch` (every platform, the web's create_session included) generates the prompt from the post as it is then, commits it, and launches: the record of what each part was told stays in git next to what it did, made by the script, never by hand. In scripts/kit.py.
2. A relaunch or takeover regenerates it from the post when the post changed since, as a new commit (the history shows each version).
3. `done` and the reviews read the task from the post; a prompt file that differs from its post's task is named by doctor.
4. Tests: a task edited on the post reaches the next launch's prompt with no other edit; a prompt edited by hand is named by doctor.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
