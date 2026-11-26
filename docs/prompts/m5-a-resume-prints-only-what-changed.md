Board, part M5: A resume prints only what changed. Train `memory`. Your step's post: `m5-a-resume-prints-only-what-changed` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m5-a-resume-prints-only-what-changed`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A resume prints only what changed since the session's last one: the orchestrator stays current before answering the owner, without rereading the whole board each time.

1. `kit.py resume` in a session that already ran one prints what changed since then (steps, sessions, questions, the owner's comments, other writers), or one line "nothing new since HH:MM"; `--full` prints it all. In scripts/kit.py with tests/test_resume_delta.py.
2. The brief guard accepts that read as fresh, in kit/hooks/kit_brief_guard.py.
3. The first resume of a session, a hand-over and a takeover still print it whole.

Bars: characters of a second resume with nothing new and with one change, against the full one (19,800 characters for the orchestrator's now).

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
