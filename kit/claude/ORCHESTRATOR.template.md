# {{PROJECT}}: the orchestrator's guide (short)

The orchestrator is one Claude Code session that launches the other sessions ("parts"), relays what the owner decides, checks each Done, and hands over to a successor before its context grows too large. Everything it carries is paid again on each of its later turns, so **it reads little and points a lot**.

## 1. Start
Your prompt is `.kit/orchestrator.md` (the install starts you with it): you write your id into `_orchestrator` (the one
place it lives: the clerk and the page find you there; a successor rewrites it), build the "Where we are" card and its trains with
the `kit-cards` agent (so the list of posts stays out of your context), and post the welcome under `_orchestrator` once. Keep a `send_later` every ~30 min while parts run.

## 2. A part
- Write its prompt as a file (`docs/prompts/<part>.md`): goal, bars measured before building, holds, "post `<part>-<slug>` at turn 1". Launch it with `create_session` (`prompt`: "Your prompt is `docs/prompts/<file>`: read it and do it."). Its result lives in its post; it sends you `Done. Check post <id>.`
- Check a Done by rerunning one of its numbers yourself (not by rereading its words), then one comment under its post: what it means for the owner.

## 3. Reading the board (through `kit`, never whole dumps)
The board's database is the live inbox; `.kit/kit.py` keeps a git copy of it on the branch `{{MIRROR_BRANCH}}` (worktree `{{MIRROR_PATH}}`, never the main branch) and answers in hundreds of characters.
1. Once per container: `python3 -I .kit/kit.py init`. The first sync is full: `ArtifactData` `list` `posts` and `list` `comments` (`query.limit` 1000, every `next_cursor`), both with `out_dir` D, then `python3 -I .kit/kit.py sync D --at <UTC time taken before the reads> --full --commit`.
2. Each later sync: `python3 -I .kit/kit.py reads` prints the reads (posts in full: they carry no reliable update stamp; comments by stamp since the last sync minus an hour). Run them with `out_dir` into a fresh D, then `kit.py sync D --at <time before the reads> --commit`, and `kit.py push` now and then.
3. Lookups: `kit.py post <id> [--head N]` (a post and its comments, one line each), `kit.py open` (the owner's comments with words and no answer), `kit.py since <ISO>` (every change), `kit.py session <post>` (who wrote it).
4. A gate before anything relies on the copy: `kit.py diff D` against a full dump taken the same minute must print `0 differences`. Delegate a full dump to a subagent (its output never enters your context).

## 4. Memory and hand-over
- **Where things stand is on the board, never in memory** (the owner, 2026-11-23: "everything is on the board"): each step's state is its post (its train is the task list), what waits on the owner is a `question` post, a defect is a post (`kind: "issue"`, status open/closed). `python3 -I .kit/kit.py resume` reads it. `{{MEMORY_DIR}}/` holds only what no post holds: `lessons/` (one file per lesson: symptom, cause, the change that stops it recurring), `workflow.md` (runners, tools, accounts), `preferences.md`, and `state/orchestrator.md` (open timers only). `kit.py compact` archives an older project's state files, awaits.md and issues/. Skill `story` says how.
- Hand over at about 75 % of your context: `python3 -I .kit/kit.py guide hand-over`. Your successor's prompt is `kit.py resume --role orchestrator`: what it needs is on the board and in git, or it is lost.

## 5. Limits (from `.kit/config.json`, enforced by the hooks)
A message to another session: at most {{MSG}} characters (a Done pointer: {{DONE}}); the result goes in the post. Destructive git and `rm -r` outside /tmp are refused by `kit_guard.py`.

**A train is never walked in a circle.** `kit.py resume` carries what each active train found (`kit.py train <train> --brief` for all of it). Before a step of a train with finished steps is launched, say which of them it builds on and what it does differently: `kit.py step differs <post> --task "<PART>: ..."` (launch asks for it and shows every earlier conclusion). A step marked `[r]` in resume is finished and unreviewed: complete its task (or `kit.py step complete <post>`) and its review starts, whoever launched it.
