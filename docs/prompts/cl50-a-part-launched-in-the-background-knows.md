Board, part CL50: A part launched in the background knows its own step. Train `takeover`. Your step's post: `cl50-a-part-launched-in-the-background-knows` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl50-a-part-launched-in-the-background-knows`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part launched with `claude --bg` knows its own step. CL44's part was started while M1's part had just been launched, and its environment named M1's post (KIT_POST): its automatic save commits were labelled with M1's name. A likely cause: `claude --bg` hands the work to a warm spare process made earlier, whose environment is not the one the launch passed (the launch sets KIT_POST, KIT_ROLE and the task list id in its subprocess environment).

1. Confirm the cause: launch two parts one after the other and read each session's KIT_POST.
2. A part's step never rests on the environment alone: the launch records the worktree's step (`.kit/run/step` in the worktree, or the branch's post), and the hooks read that first; KIT_POST only when it agrees.
3. When the two disagree, the hooks say so once, never label commits with the other step.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
