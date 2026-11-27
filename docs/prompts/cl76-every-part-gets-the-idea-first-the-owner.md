Board, part CL76: Every part gets the idea first: the owner's goal and cases beyond the literal task, tested by stress cases from that intent, refused at done otherwise. Train `reviews`. Your step's post: `cl76-every-part-gets-the-idea-first-the-owner` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl76-every-part-gets-the-idea-first-the-owner`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## Where to work
Repo anlonlt/board, branch `main`: `git clone -b main https://github.com/anlonlt/board && cd board` (already cloned: `git fetch origin main && git checkout main`).

## The task
Owner, 2026-11-27: "Every part needs to extrapolate, needs to stress test the shit out of things, needs to get the idea of what I'm saying." R6 was asked that every new step be "measured against every open one"; it built links by names, `after` and shared files only, wrote 7 fixture cases that all used those links, passed its own tests and its review, and missed the plain case (two steps about the same thing that name nothing in common: M14 then CL75 on 2026-11-27). Its tests tested what it built, not what was asked.

Change, for every part (part templates for claude, claude-cli and agy; `kit.py done`; the review prompt):
1. Intent first: before any code the part writes, on its post, the owner's goal in his own words (from the task, the train goal and his comments) and at least 3 cases beyond the literal task that he would expect to work, including one that its likely mechanism would NOT cover. The plan must cover them or say why not.
2. Stress tests: the check includes cases derived from the intent list, not from the implementation: at least one adversarial or out-of-pattern case per intent case, and the real data case where there is one (the live board, Harmonize). A fixture written only from the code's own mechanism does not count.
3. `kit.py done` refuses a post with no intent section or whose check names none of the intent cases, and prints which intent case has no test.
4. The review (and CL75's reduced review) asks first: does it do what he meant, with the intent cases, before any rerun of the tests.

Check: replay R6: its post and tests as they were are refused by done (no intent case for two steps on the same subject that name nothing in common); the same task with the intent section and a topic-only test passes. Fails on main.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
