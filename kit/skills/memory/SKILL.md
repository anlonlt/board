---
name: memory
description: Keeps this project's memory between sessions in the committed memory directory (default .board/memory/ or .story/): one state file per workstream (state/<ws>.md, first line "Next: ..."), what awaits the owner (awaits.md), issues with a status line (issues/), lessons (lessons/). Use at the start of a session to know where to resume (the SessionStart hook already injected each workstream's next step), and at every save point. Use when the user says "where were we", "catch me up", "save", "hand off", "log an issue", "log a lesson".
---

# memory: persistent project memory between sessions (from anlonlt/board)

**Commit and push at the end of every turn that changed something.** The `Stop` hook blocks a stop once while work is uncommitted or unpushed.

## Layout (inside `kit.memory_dir`, default `.board/memory/` or `.story/`)
```
state/<workstream>.md   line 1 "Next: <the one next step>"; optional "Blocked by: <ids>"; "## Where it stands" and "## Done"
awaits.md               "- [<workstream>] <what the owner must do or choose> (default: <what happens if silent>, by <date>)"
issues/<date>-<slug>.md line 1 the defect in one sentence; "Status: open|closed (<why, commit>)"; how to reproduce
lessons/<date>-<slug>.md  a symptom, its cause, the remedy (change the tool/hook/prompt so it cannot recur)
before-compaction.md    written by kit_pre_compact.py on Stop; gitignored
```
One fact, one file. A workstream's next step lives ONLY in its state file; other places point to it.

## Load
The `SessionStart` hook injected every workstream's `Next:` line, awaits count, open issues, and lessons count. Open the state file of the workstream you work on; grep `lessons/` before diagnosing anything twice.

## Save
1. Rewrite `state/<ws>.md`: line 1 `Next: …` (one concrete step, who does it); `## Where it stands` as facts with evidence (commit, file, measure).
2. Update `awaits.md`: add what waits on the owner (with a default), remove what they answered.
3. Commit the memory directory, then run `python3 -I .kit/end_session.py`.
