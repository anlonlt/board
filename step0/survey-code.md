# Kit step 0, the survey: the version for Claude Code users (Claude Code on the web, a repository attached)

Moved here from `anlonlt/harmonize` (`docs/WORKFLOW-KIT.md` § 0, part BD9) on 2026-11-20 and made project-agnostic.
The same survey as `step0/survey-chat.md`, but Claude Code can measure: it reads its own sessions' records (tokens
from `usage`, never at 4 characters per token), the repository, and a board dump if there is one, with the scripts in
`scripts/`, and it can open a separate review session itself. Harmonize's own survey (WF1 and its review CR-WF1) is
the model: every "unknown" the first pass leaves is worked on by a second, independent pass.

**How to use:** start a Claude Code session at https://claude.ai/code on the project's repository, with this kit
(`anlonlt/board`, public) cloned beside it or attached read-only, and paste everything between the two lines.

---

You are running **step 0 of a workflow kit: the survey**, on this project. Change nothing in the project: you write
only into a new folder `survey/<YYYY-MM-DD>/` of the kit's fork and the proposed config. Ask the user one go-ahead
first (the reports end in a public repository, anonymised: no names of people, organisations or accounts); accept
the first yes.

**Part 0: language.** From the user's messages in their recent sessions (`list_sessions` mine, then `list_events`
kinds user, one page each) and the repository's CLAUDE.md, find the language they talk in and the one the project is
written in. Use theirs from now on; record both for the config.

**Part 1: survey, alone (measure, do not ask).** Grade every answer measured / inferred (from what) / unknown (what
would settle it). Never estimate tokens from characters; when only characters exist, say so.
1. Roles: which sessions exist (`list_sessions`: coordinators, workers, clerks, reviewers, helpers), what each writes and reads.
2. Stores: repo docs, memory folders, a board's database (dump it with `out_dir` if one exists), data stores, scheduled tasks (`list_triggers`), transcripts; sizes.
3. Channels: board comments, `send_message`, hooks' injected text, timers, pushes; how a result reaches the user.
4. Hooks: `.claude/settings.json` and each script: its job, generic or project-bound.
5. Rules: where each standing rule is written (CLAUDE.md, docs, prompts, hooks), counted; which are enforced by code.
6. Facts kept twice: each kind of fact (what waits on the user, next steps, session ids, lessons, results) and its places; any drift today.
7. Cost: for the coordinating session (the longest-lived), `get_session` usage, then every page of `list_events`
   (kinds assistant + user, limit 100, paged back) saved to files, then
   `python3 -I scripts/what_fills.py PAGES_DIR --config config/<project>.json` (write the project's `shell_buckets` first: its hand-over log, its board files, git).
8. If there is a board: `python3 -I scripts/mirror_holes.py DUMP --config config/<project>.json`; and the token
   cost of one board read, with a probe subagent and `scripts/token_probe.py` on its transcript.
9. The user's wait: how long between their message and a session's answer (a board: comments; else chat turns).
10. Runners (the kit's README § Runners): every place the work happens, and its kind: `cloud` (sessions here: their
   environment's connectors, keys, network), `computer` (the user's own machine: Remote Control, the desktop app, local
   files, Claude in Chrome), `chat` (claude.ai chats and their connectors), `owner` (what only they can do), `scheduled`
   (`list_triggers`). For each: what is set up, what is not, and which projects need it.

**Part 2: the second pass.** Launch a fresh subagent (or, better, Part 3's session) with the DATA (the files,
dumps, pages), not your conclusions: "recount every number, find what this missed, settle each unknown". List what
changed.

**Part 3: the review session test.** Open a separate session with `create_session` (the reviewer), give it the
data paths and the instruction above, and have it send its review back with `send_message`. Record: could you open
it, how long it took, what it changed (count). That count says whether a reviewer belongs in this user's kit.

**Part 4: set-up check.** `curl -s https://api.github.com/user` (the login = linked); the fork of `anlonlt/board`
attached with push access (the user's go-ahead covers it; never clone read-only for a step that must push); no
personal token anywhere (a `GH_TOKEN` variable is ignored by the proxy; never in chat).

**Part 5: the pull request.** In the fork: branch `step0/survey-<YYYY-MM-DD>`, files `survey/<YYYY-MM-DD>/report.md`
(Report 1), `dev-report.md` (Report 2), `config.json` (the proposed config, in the shape of `config/example.json`).
Search your text for account and organisation names before each commit; show the user the files and wait for their
yes before the first commit. Push with `-u`. A session cannot open a pull request into `anlonlt/board` (only its own
repository is attached): give the user `https://github.com/anlonlt/board/compare/main...<login>:board:<branch>` and
say "click Create pull request".

**Report 1** (the user's language): roles, stores, channels, hooks, rules, facts kept twice, the 3 largest costs in
measured tokens, what the kit would take over, what it must not touch.
**Report 2, the dev report** (English, complete): each question with its sources, grade after Part 1 and Part 2,
and what would settle it; every script run, its exact command and output summary, every error verbatim; Part 3's
outcome; Part 4-5's outcome and how many user messages they took; what you assumed; what the kit's scripts could not
read in this project and what config field would fix it; one paragraph: does the kit's design fit this project.

Begin with the go-ahead.

---

Not tested yet on a project other than Harmonize: the scripts' Harmonize numbers are reproduced exactly with
`config/harmonize.json` (2026-11-20); a second project is the real test.
