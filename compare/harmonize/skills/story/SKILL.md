---
name: story
description: Keeps Harmonize's memory between sessions in the committed .story/ directory — one state file per workstream in .story/state/, what awaits the owner in .story/awaits-anlon.md, issues with a status line, lessons. Use at the start of a session to know where to resume (the SessionStart hook has already printed each workstream's next step), and at every save point to rewrite the workstream's state, update awaits-anlon.md, check with scripts/check.py, commit and push. Use when the user says "/story", "where were we", "catch me up", "save", "hand off", "log an issue", "log a lesson", « où on en est », « reprends », « sauvegarde », « fais le point ».
metadata:
  version: 1.0.0
---

# story — memory between sessions (Harmonize)

## Critical — before anything else

- **Commit and push at the end of every turn that changed something.** There is no knowing which
  turn is the last: a compaction loses the conversation, a rebuilt container loses what was not
  pushed. The Stop hook (`.claude/hooks/stop.sh`) blocks the stop once when something is
  uncommitted or unpushed. The previous source of this app was lost to an agent deleting it
  (`CLAUDE.md`, "Never"); pushed commits are what survived.
- **Rewrite the workstream's state once per session** (or when its next step changes), not after
  every commit. `.claude/skills/story/scripts/end-session.sh` checks the tree, the state and the
  checker, then pushes.
- **Rewrite the state, never append to it.** 60 lines at most. What git already knows (what was
  done) is written nowhere else.
- **No keys or passwords in `.story/`** — it is committed like the code (`CLAUDE.md`, "Never"). Audio and
  song maps may be committed (the owner, 2026-11-10, removed both audio rules).
- **What is the owner's is not decided here — and what is not the owner's is not asked.** A
  technical choice is made and said (in the commit and the state). A question of taste, feel on
  the voice, pedagogy, money, the Play listing or release signing goes into `awaits-anlon.md`
  with `Default:` and `· deadline YYYY-MM-DD`; past the deadline the default applies (the
  checker names it).

## Layout

```
.story/
├── state/WORKSTREAM.md  one per workstream, active or paused; REWRITTEN at each save point
├── awaits-anlon.md      what awaits the owner's decision, ears or eyes; one point per line
├── issues/              line 2 of each is its "Status:" line
└── lessons/             one durable lesson per file, one-line summary on line 1
```

`PLAN.md` (and `docs/SONGS.md`, `docs/BOOK.md` for their pathways) are the contracts; their
status lines are the board. The truth about what works is the tests (`./gradlew :core:test`),
the CI run on the pushed commit, and the APK on a phone. `.story/` is working memory: it never
outranks `CLAUDE.md`, a plan or a test.

The workstreams so far: `app` (PLAN steps 0–5), `songs` (the My songs pathway), `book` (the
transposition of *The Songwriter's Workshop: Harmony*). A new one gets a state from the template.

## Load — /story

1. **The hook's output is already in your context** (`.claude/hooks/session-start.sh`): the build
   tools, whether this clone is behind its branch, other branches that rewrote a state, each
   state's next step, the points awaiting the owner, the open issues, the lesson count. Do not
   re-read what it served.
2. **Open your workstream's state in full**: `.story/state/WORKSTREAM.md`. Follow its pointers
   only as the work needs them.
3. Open a lesson when a symptom resembles its title (`grep -ril '<word>' .story/lessons/`); an
   issue when the work touches it.
4. `git log --oneline -10`.

Then give a 2–3 sentence catch-up: the workstream, where it stands, its next step, any blocker.

## Save — /story save

1. **Rewrite `.story/state/WORKSTREAM.md`** on this template:

   ```markdown
   # State — WORKSTREAM

   Updated: YYYY-MM-DD. Governing documents: the plans, with their paths.

   ## Where it stands
   10 lines at most: the status in a sentence, what is in place, what is missing — each fact
   with its pointer (path, §, issue). No list of commits.

   ## Next step
   ONE line: the smallest next gesture, small enough to do without hesitating, with its pointer;
   or "Waiting: … (awaits-anlon.md)".

   ## Recent decisions and why
   5 at most, newest first; the oldest leaves when one comes in:
   "- YYYY-MM-DD — the decision — why — pointer", the owner's words in quotes when it is theirs.

   ## Open
   Pointers to the workstream's open issues and its awaits-anlon.md points, one line each.
   ```

2. **Update `.story/awaits-anlon.md`**: add
   `- [workstream] the question? Default: … — pointer · deadline YYYY-MM-DD` (320 characters at
   most, continuation lines indented by two spaces); remove a line once the answer is recorded
   where it acts (the plan, the code, the state's decisions). A paused workstream has one line
   there, its resumption.
3. **The plan's status line**, if a step changed status (`CLAUDE.md`, "How to work").
4. **Check:** `python3 -I .claude/skills/story/scripts/check.py` must print `STORY: OK`.
5. **Commit and push** — by name, never `git add -A`. At the end of a session:
   `.claude/skills/story/scripts/end-session.sh` (clean tree, checker, a state rewritten on this
   branch, push with retries). `--tests` first runs the engine tests the unpushed changes can break, the slow
   probes on their quick subset (`scripts/test-changed.sh`, `Tier.kt`); the session's last save is `--final`: the
   same, every case, over every change since the last full pass; `--all-tests`, all of them, every case.

## Issue — /story issue

Out of scope for the current work? Don't fix it, don't lose it:
`.story/issues/YYYY-MM-DD-slug.md` (dated, so parallel branches never collide). Line 1 is the
title (`# …`), line 2 exactly one of:

- `Status: open — YYYY-MM-DD, what remains`
- `Status: closed — YYYY-MM-DD, what closed it (commit or file:line)`

Then severity, impact, where it was found (file:line, test name, phone model). Closing rewrites
line 2; the file stays. If its next move is the owner's decision, add a line to
`awaits-anlon.md`. The hook lists only the issues whose line 2 reads `Status: open`.

## Lesson — /story lesson

A non-obvious learning that should carry forward: `.story/lessons/YYYY-MM-DD-slug.md`, a
one-line summary as the first line. Update an existing lesson rather than duplicate it. A
lesson names a **gesture** (last section); for audio, the gesture is usually a measurement kept
as a test in `core/src/test` (`CLAUDE.md`, "Layout").

## Common issues

- **Stop hook: "SAVE POINT"** — do the save point it lists, then stop again. It blocks only once
  per stop: when Claude is already continuing because of it, it stays silent.
- **Checker: "the Next step must fit on ONE line"** — pick the smallest gesture; the rest goes
  under "Open".
- **Checker: "dead pointer"** — a cited path or issue does not exist: fix the pointer, never
  delete the fact to silence it.
- **PreToolUse: "REFUSED"** — `rm -rf`, `git clean`, `git reset --hard` and force pushes are
  refused in this repo (`CLAUDE.md`, "Never"). Remove a file with `git rm` in its own commit;
  undo a commit with `git revert`; scratch files live in the scratchpad under `/tmp`, where
  `rm -rf` is allowed.
- **Two sessions at once never share a state file** (orchestrator 5, 2026-11-10: K1 and S11 both
  rewrote `songs.md` and K1 spent its turn hand-merging it). A part running beside another of the
  same workstream keeps its own state, named after its subject (`chords.md`, `lyrics.md`), and
  touches only that file and its own points in `awaits-anlon.md`; the orchestrator names the file in
  its prompt. A merge conflict on a state anyway: take main's version of the other's file
  (`git checkout origin/main -- <file>`) and keep yours; never hand-merge two sessions' states.
- **No "STATES" block in your context** — the hook did not run: read `.story/awaits-anlon.md`
  and your state directly.
- **No state for your workstream** — create one from the template.
- **A push refused with HTTP 403** on a ref outside `refs/heads/` — the container's proxy; do
  not retry from a cloud session.

## Example — one save point

The turn moved the reference volume check of PLAN step 2 forward. The save point: in
`.story/state/app.md`, "Where it stands" loses what is no longer true, "Next step" becomes the
next gesture, the decision enters "Recent decisions" (the oldest of five leaves); the question
"is the speaker loud enough to clip the microphone on your phone?" goes to `awaits-anlon.md`
with its default and deadline; the checker prints `STORY: OK`; one commit, one push.

## Discipline

- The board is a convenience; the tests, CI and the phone are the truth about what works. When
  they disagree, fix the board.
- Keep every `.story/` file short and plain. This is memory, not documentation.
- Commit `.story/` with the work it belongs to. One step of `PLAN.md` at a time.

History: adapted on 2026-11-08 from the `story` skill of the owner's Aplomb project (version
2.2.0), itself adapted from Storybloq. Dropped for Harmonize: the corpus and student-data laws,
the database, the signed-phase ledger, the roadmap file (PLAN.md's status lines do that job),
handovers, the decision-map tickets.

## A lesson names the remedy, and the remedy is often to REWRITE the tool

A lesson that only says "watch out for X" is reread and changes nothing. The lessons that pay
name a **gesture**: measure instead of arguing, keep the probe as a test, refuse the command in
a hook instead of trusting attention (the lost source, `.story/lessons/`).

When the cause is a TOOL — a probe that lies, a script that is rerun hoping for another result,
a threshold hard-coded in the UI — **the remedy to write is "rewrite it", not "be careful"**.
Three signs that a tool should be rewritten rather than worked around:

- you **rerun** it hoping for a different result;
- you **ignore** one of its outputs because "it always says that";
- you **avoid** running it because it costs too much for what it returns.
