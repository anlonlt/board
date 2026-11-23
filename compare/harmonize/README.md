# Harmonize's own hooks, for comparison with the kit's

The kit (`kit/hooks/`, `kit/claude/`, `kit/skills`) was generalized from Harmonize's setup. A side-by-side reading
(2026-11-23) found that for a project like Harmonize the kit is **better in a few places and weaker in more**. This
folder holds Harmonize's versions, unchanged except for its board's Artifact id (`XXXXXXXXXXXXXXXXXXXXXX`), so anyone
can read both, run both test suites, and judge. Snapshot of `anlonlt/harmonize` at `cbc11b6`.

```
hooks/                 .claude/hooks/ of Harmonize (each with its test where it has one)
skills/story/          its memory skill (SKILL.md, scripts/check.py, end-session.sh, test_check.py)
settings.hooks.json    how its .claude/settings.json wires them
```

Run their tests: `python3 -I hooks/test_say_it_once.py` (72 cases), `python3 -I hooks/test_pre_push_checks.py`,
`python3 -I hooks/test_resume_runs.py`, `python3 -I hooks/test_board_typing.py`, `python3 -I skills/story/scripts/test_check.py`.

## Hook by hook

| Concern | Harmonize | Kit | Better for Harmonize | Why |
|---|---|---|---|---|
| Say it once | `hooks/say_it_once.py` | `kit/hooks/kit_say_it_once.py` | **Harmonize** | Blocks, even on the owner's own turn, a link to another Artifact than the board and a request put to him in chat (`ARTIFACT_LINK`, `ASKS_HIM`, lines 51-63): those belong on the board. Holds a session to 240 characters before its first board write, a stricter rule for the orchestrator, a clerk exemption. The kit adds configurable limits and no longer retries an answer to the owner at Stop (it was sent twice). |
| Board typing dots | `hooks/board_typing.py` | `kit/hooks/kit_board_typing.py` | **Harmonize** (slightly) | Its pattern for the clerk's forward is lenient; the kit's needs exactly "on your post X,". The kit also dropped the `if_version` and "delete the dots if you do not answer" advice. Otherwise a copy. |
| Session start | `hooks/session-start.sh` | `kit/hooks/kit_session_start.py` | **Harmonize** | Has what the kit lacks: SDK/build lines, detection of a rebuilt old clone and the session base, a scan of other branches for state changes, the PLAN.md line, OVERDUE deadlines. The kit reads `awaits.md` (line 131) where Harmonize keeps `awaits-anlon.md`: the owner's list would vanish silently. The kit counts an issue closed if "status: closed" appears anywhere in it; Harmonize reads line 2 and flags an issue with no status. The kit adds per-role trimming and config-driven lines. |
| Stop | `hooks/stop.sh` | `kit/hooks/kit_stop.py` | **Harmonize** | Checks that main contains the branch (lines 55-58), exempts the clerk, counts from the session base for rebuilt clones. The kit ignores new untracked files (`--untracked-files=no`, line 64). The kit is better at blocking only once per turn. |
| Context limit | `hooks/pre-compact.sh` | `kit/hooks/kit_pre_compact.py` | **Kit** | The kit hands the orchestrator over at a context limit (380k; the clerk at 80k) to a fresh session; Harmonize's orchestrators ran to about 700k. The kit dropped Harmonize's list of live long jobs at compaction. |
| Guard | `pre_tool_use.py` (not copied: the kit's is a superset) | `kit/hooks/kit_guard.py` | **Kit** | Also catches `find -delete`/`-exec`, recursive deletes inside `python -c`/`node -e`, `git branch -D`, `git add -A` staging never-add files, and the per-device session cap. Lacks Harmonize's `branches.sh --delete` rule; its keystore rules move to config. |
| Pre-push checks | `hooks/pre_push_checks.py` | none | **Harmonize** | Runs CI's story and rules checks before a push. |
| Runs after a restart | `hooks/resume_runs.py` | none (`kit.py resume` is different) | **Harmonize** | Relaunches long jobs a container restart stopped. `kit.py resume` rebuilds the orchestrator's task list: not the same job; both are useful. |
| Long-run progress, /goal count | `hooks/user-prompt-submit.sh`, `hooks/post-tool-use.sh` | none | **Harmonize** | A progress line for a long run at each prompt; the 4,000-character /goal count. |
| Memory skill | `skills/story/` (176 lines + check.py, end-session.sh) | memory skill (26 lines) | **Harmonize** | The kit's is generic and has no checker or end-of-session script; it expects a `Next:` first line where Harmonize's states use `## Next step` (read only through a fallback). |
| Board guide | `docs/BOARD.md` (not copied) | `kit/board/BOARD.template.md` | **Mixed** | The kit adds a structured post format (verdict, bars, artifacts, not_checked); it drops Harmonize's cost rules, and has a broken placeholder ("checked by `the format below`", line 22). |

## What the kit has that Harmonize does not

Phone push for the Artifact board (`kit_notify.py`), the "Where we are" card reminder (`kit_cards_reminder.py`),
TaskCreate becoming a train step (`kit_task_train.py`), the context hand-over, the consultant and runner guides.

## What to do with this

1. Into the kit, from here: say-it-once's link and request rules, the lenient board-typing pattern, session start's
   branch scan, rebuilt-clone handling and OVERDUE, stop's main-contains-branch check and untracked files, pre-push
   checks, resume_runs, and the story skill's checker and end-of-session script (generalized: the awaits file name and
   the state format read from config).
2. Into Harmonize, from the kit: the guard's extra rules, the context hand-over, phone push and the card reminder,
   wired so that no concern has two hooks (installing the kit beside these would run stop and say-it-once twice).

Source of the table: an agent's reading of both trees, spot-checked by hand (line numbers above); treat any row you
rely on as a claim to verify against the two files.
