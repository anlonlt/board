---
name: story
description: Keeps this project's memory between sessions in the committed .story/ directory: one state file per workstream (.story/state/<ws>.md, first line "Next: ..."), what awaits the owner (.story/awaits.md), issues with a status line (.story/issues/), lessons (.story/lessons/). Use at the start of a session to know where to resume (the SessionStart hook has already printed each workstream's next step), and at every save point. Use when the user says "/story", "where were we", "catch me up", "save", "hand off", "log an issue", "log a lesson".
---

# story: memory between sessions (from the kit, anlonlt/board — agy local)

**Commit and push at the end of every turn that changed something.** The `Stop` hook blocks a stop once while work is uncommitted or unpushed.

## Layout
```
.story/state/<workstream>.md   line 1 "Next: <the one next step>"; then "## Where it stands" (facts, with commits) and "## Done" (newest first)
.story/awaits.md               "- [<workstream>] <what the owner must do or choose> (default: <what happens if he says nothing>, by <date>)"
.story/issues/<date>-<slug>.md line 1 the defect in one sentence; "Status: open|closed (<why, commit>)"; how to reproduce
.story/lessons/<date>-<slug>.md  a symptom, its cause, the remedy (often: rewrite the tool so it cannot happen again)
.story/before-compaction.md    written by kit_pre_compact.py on Stop; gitignored
```
One fact, one file. A workstream's next step lives ONLY in its state file; other places point to it.

## Load (/story)
The `SessionStart` hook injected every workstream's `Next:` line, the awaits count, open issues and the lessons' count. Open in full the state file of the workstream you work on; `grep -ril '<symptom>' .story/lessons/` before diagnosing anything twice.

## Save (/story save)
1. Rewrite `.story/state/<ws>.md`: line 1 `Next: …` (one step, concrete, who does it); `## Where it stands` as facts with their evidence (a commit, a file, a measure), not intentions.
2. Update `.story/awaits.md`: add what now waits on the owner (with a default), remove what he answered.
3. `git add .story && git commit -m "story: <ws> — <what changed>"`, then `python3 -I .kit/end_session.py`.

## Issue (/story issue)
A defect found and not fixed now: `.story/issues/<date>-<slug>.md`, its first line the defect, `Status: open`. Closing it: `Status: closed (<commit>)`, never delete the file.

## Lesson (/story lesson)
A mistake that cost time: `.story/lessons/<date>-<slug>.md`: the symptom (words someone would grep), the cause, the remedy. A lesson whose remedy is "be careful" is not done: change the tool, the hook or the prompt so the mistake cannot recur, and name that change.

## Hand-over
A session about to stop or hand over writes its state as if the next session knows nothing: what runs (conversation IDs), what is half done, the next step, then pushes.
