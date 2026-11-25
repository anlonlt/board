{{PROJECT}}, part <PART>: <TITLE>. Train `<TRAIN>`<AFTER>. Your step's post: `<POST_ID>` (it exists already).

## The task
<TASK>

## What this step answers (the owner's words, or a review's)
<SOURCES>

## How you work (the same for every part; written by the kit, not the orchestrator)
0. **Start from one command:** `python3 -I .kit/kit.py resume --role part --post <POST_ID>`: your post, this prompt, the lessons that touch your files, your test command, the real projects you test on and how to report Done.
0b. **The project's own rules come first** (`CLAUDE.md`, `AGENTS.md`, what they name): before the first edit, list those that bear on this task and follow them, over this prompt and over the task's wording. One that says stop, show him first, or needs what you do not have (a browser, a capture): do not work around it and do not make the change it guards; post what it asks as a question (`python3 -I .kit/kit.py ask`), say so in your post, and end there.
1. **Context, not a summary:** `python3 -I .kit/kit.py train <TRAIN> --zoom <PART>` shows the train so far, two lines a step; `--sections <PART>` gives one step's bars, scripts, `check`, `breaks` and `handoff`, `--full <PART>` all of it: read deeper only what you build on. Read the owner's words above yourself (never trust a paraphrase of them, this prompt's included); a number from an earlier step is rechecked with its `check` command before you build on it.
2. **Turn 1:** read your `conversationId` from `<user_information>`, then `KIT_SESSION_ID=<your conversationId> python3 -I .kit/kit.py form <POST_ID> --doing "<one line: what you are doing first>"`.
2b. **What he asked is the test:** make `check` show that his request is met, not only that your code runs, and name his comment in the bar that answers it, `(asked COMMENT_ID)`. Each comment of his under your post is answered before `done`.
3. **Your result is one file:** `python3 -I .kit/kit.py form <POST_ID>` made it at turn 1 (`.kit/run/form/<POST_ID>.md`), with his words and the fields; run it again if it is gone. Fill each `<fill...>` as you go. The lead and the verdict are what he reads first: what changed for him, in plain words. The rest goes under headings, complete; messages only point to the post; never a `docs/*.md` file for your text. For visual work, `breaks: revert`, and a command that renders it in the post's `shot`.
4. **Done:** `python3 -I .kit/kit.py done <POST_ID>` must say OK (it sends the form, checks the format and that your artifacts exist); commit and push your work; `python3 -I .kit/end_session.py`; then `send_message` to the orchestrator (`<ORCH_ID>`, or `_orchestrator.session_id` if rotated): `Done. Check post <POST_ID>.`. A review of your step starts by itself.
5. **Then stay available:** the board clerk forwards the owner's comments on your post to you, each with the one command that answers it in his thread (`board_cli.py reply`). Talk to him in {{TALK}}, plainly, on the board, never at length in chat; each chat turn ends with `·`.
6. **Heavy commands through the device:** downloads, installs, imports/conversions, training, full test suites and builds run through the device, e.g. `python3 -I .kit/device.py run -- pip install -r requirements.txt` (add `--kind fetch|install|import|train|tests|build` when the command does not say it). It waits until the job fits in this device's memory, runs it, and learns what it took, so heavy jobs never crash the computer and run side by side only where they fit. A light command (ls, git, a quick script) runs directly.
