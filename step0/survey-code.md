# Kit step 0, the survey: the version for Claude Code users (Claude Code on the web, a repository attached)

Moved here from `anlonlt/harmonize` (`docs/WORKFLOW-KIT.md` § 0, part BD9) on 2026-11-20 and made project-agnostic.
The same survey as `step0/survey-chat.md`, but Claude Code can measure: it reads its own sessions' records (tokens
from `usage`, never at 4 characters per token), the repository, and a board dump if there is one, with the scripts in
`scripts/`, and it can open a separate review session itself. Harmonize's own survey (WF1 and its review CR-WF1) is
the model: every "unknown" the first pass leaves is worked on by a second, independent pass.

**How to use:**
- **On a local CLI (`agy`, Claude Code CLI, generic CLI):** run `python3 -I scripts/step0_scan.py . --out-report survey/$(date -u +%Y-%m-%d)/report.md` (which scans local `~/.gemini/antigravity-cli/brain/*/transcript.jsonl` and `~/.claude/projects/` logs, discovers sibling projects, measures autonomy/handholding vs feature sprawl, and infers 3 colour palettes), or launch the clickable TWRP-style terminal GUI wizard directly with `python3 -I install.py . --tui`.
- **On Claude Code on the web:** start a session at https://claude.ai/code on the project's repository, with this kit (`anlonlt/board`, public) cloned beside it or attached read-only, and paste everything between the two lines.

---

You are running **step 0 of a workflow kit: the survey**, on this project. Change nothing in the project: you write
only into a new folder `survey/<YYYY-MM-DD>/` of the kit's fork and the proposed config. Ask the user one go-ahead
first (the reports end in a public repository, anonymised: no names of people, organisations or accounts); accept
the first yes.

**Part 0, first question, before any measuring (issue #3 item 6):** "One board for all your projects, or one for
this project (`<repo>`)?" (one board: every repository's sessions are surveyed and imported; one project: only the
sessions whose source repository is this one, from `get_session`). Record the answer for the config and the import.

**Part 0: language.** From the user's messages in their recent sessions (`list_sessions` mine, then `list_events`
kinds user, one page each) and the repository's CLAUDE.md, find the language they talk in and the one the project is
written in. Use theirs from now on; record both for the config.

**Part 1: survey, alone (measure, do not ask).** Grade every answer measured / inferred (from what) / unknown (what
would settle it). Never estimate tokens from characters; when only characters exist, say so.
1. Roles: which sessions exist (`list_sessions`, kept to the scope of Part 0: coordinators, workers, clerks, reviewers, helpers), what each writes and reads.
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
10. Runners & Connected MCPs (the kit's README § Runners): inspect your own tool list and every place the work happens, and its kind: `cloud` (sessions here: their environment's connectors, keys, network), `computer` (the user's own machine: Remote Control, the desktop app, local files, Claude in Chrome), `chat` (claude.ai chats and connected MCPs — Gmail, Outlook, SharePoint/OneDrive, Google Drive, Calendar, Notion, Slack, Linear, GitHub), `owner` (what only they can do), `scheduled` (`list_triggers`). With the user's upfront read-only consent, sample connected MCPs (last 14–30 days: subjects, recent docs, open tasks) to discover active workstreams and output the exact `--mcp-readers` list (`sharepoint`, `outlook`, `gmail`, `gdrive`, `notion`, `linear`) to enable.
11. **Autonomy, Follow-Through & Sprawl Profile:** from past session transcripts, measure (a) tool calls per user turn and the percentage of user messages that are short handholding confirmations (`"yes"`, `"continue"`, `"ok"`), and (b) completion follow-through vs feature sprawl (`"also"`, `"by the way"`, `"what if we..."`) and awaiting decisions in `awaits.md`. Recommend one of three autonomy profiles: `full_auto` (auto-spawn Consultant, act on continue/redirect, apply `default_option` and carry on continuously when decisions wait, park side-tangents into memory), `balanced` (auto-spawn Consultant + 1-tap 👍), or `gated`.
12. **3 Inferred Colour Palettes:** from the repository's stylesheets/theme files and any aesthetic preferences or dark/light mode hints in the logs, infer 3 distinct colour palettes for the board (`project-inferred`, `ember-classic`, `oled-cyber` / `forest-sage`) with light and dark CSS variables.

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
measured tokens, what the kit would take over, what it must not touch, and the recommendation below.

**The recommendation** (the last section of Report 1; issue #3 item 7): read `docs/KIT-GOALS.md` of the kit (§ 1
the goals and problems as people said them, § 2 each part's problem, evidence, cost and when it is NOT worth it, § 3
the table). Answer § 3's four questions from your measures: scope, how many sessions ran in parallel, whether the
user answers later or from a phone, what work runs outside code. Then one line per part (memory, hooks, import, board,
clerk, orchestrator with cards and consultant, runners): **install / later / no**, the measure that decided it, and
the goal it serves, quoted from KIT-GOALS § 1 (or "none of the goals is present here"). End with the install command
it gives (`--no-board`, `--no-clerk`, `--no-orchestrator`, or nothing for all). Never recommend a part whose
problem you did not find in this project.

**Part 6: the import** (when the recommendation installs the memory): `step0/import-code.md`, after Install.
**Report 2, the dev report** (English, complete): each question with its sources, grade after Part 1 and Part 2,
and what would settle it; every script run, its exact command and output summary, every error verbatim; Part 3's
outcome; Part 4-5's outcome and how many user messages they took; what you assumed; what the kit's scripts could not
read in this project and what config field would fix it; one paragraph: does the kit's design fit this project.

Begin with the go-ahead.

---

Not tested yet on a project other than Harmonize: the scripts' Harmonize numbers are reproduced exactly with
`config/harmonize.json` (2026-11-20); a second project is the real test.
