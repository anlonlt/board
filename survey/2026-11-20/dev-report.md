# Kit step 0 survey, dev report (Report 2), 2026-11-20

Project: a personal self-exploration web app (React/Vite, Vercel), built only with Claude Code on the web by its
owner alone. Spec followed: `step0/survey-code.md` (the Claude Code version). First pass: `part1.md`. Second pass:
`part2.md` (independent recount from primary data; it wins wherever the two differ). Raw data (`data/`, transcripts,
commit subjects, private content) is **not** committed. Grades: M measured, I inferred, U unknown.

## Part 0: language
- Owner talks **French** (M; 22 typed turns in the coordinating session, after part2 removed 4 Stop-hook feedback
  messages, 2 skill bodies and 1 image note from the 29 counted first; part1's 1,806 FR / 125 EN function words
  were inflated by the skill bodies, conclusion unchanged). Project written in French; served content bilingual.
- Session titles are auto-generated and mostly English (41/49): not a language signal.
- One page of `list_events` per session often reaches no typed turn (2 sampled sessions: 0 and 1 typed turns);
  the language had to be measured on the longest session.
- Config: `talk: "fr"`, `project: "fr"`. The install wrote `"talk": "French"` in `.kit/config.json`: same meaning,
  different spelling (the kit should pick one form).

## Scope
`list_sessions` (mine) returned **507** sessions on the account, with no repository field. Narrowing to this
project's **49**: titles within the app's git window (07-05 → 07-24), then outcome-branch slugs against the remote
heads, then `get_session` (the session context: sources and outcomes). Part1 confirmed 9 with `get_session` and inferred 40;
part2 confirmed **all 49**. 4 look-alike sessions (07-24 → 08-02) belong to another repository. The survey session
itself is also a project session (its sources include the repo). Data gathered from other repositories' sessions was
deleted. Still U: whether sessions from 07-25 to 11-19 touched the repo under unrelated titles (settle: `get_session`
on each).

## Part 1 questions

| # | question | sources | grade P1 | grade P2 | what would settle the rest |
|---|---|---|---|---|---|
| 1 | Roles | `list_sessions` paged to the end, `get_session` ×49 | M sessions / I attribution (40) | **M** (49/49 confirmed) | settled |
| 2 | Stores | git ls-tree on default branch, bare clone (session clone shallow), repo inventory | M | M (hand-over doc: 13 dated HANDOFF sections, not 9; `docs/` 984 files, 212.7 MB) | settled |
| 3 | Channels | coordinating session's pages, session list fields | M (one session) / I (others) | M + one missed channel: a published artifact (live mockup) seen on 2 sessions | page the other 48 sessions' tool calls |
| 4 | Hooks | `.claude/` tree before install, pages | M ("none") | **M, corrected**: none in the repo, but an environment Stop hook (git check) fired 4× in the coordinating session | settled |
| 5 | Rules | CLAUDE.md, skills, doctrine docs, pages (check-command counts) | M counts / I enforcement | unchanged | settled enough; enforcement is by npm checks only, run by hand |
| 6 | Facts kept twice | docs, session `needs_action` | I | I, with the "8 blocked questions" row corrected: all 8 branches merged via PR (07-08 → 07-19, one reverted); an archive, not 8 open questions | DB variables: a permitted read of the Vercel project's env names, or the owner |
| 7 | Cost | `get_session` usage, 17 pages of `list_events`, `what_fills.py` | M (coordinating session: $53.30) | **M for all 49**: $2,493.64 total, mean $50.89, range $4.07–$191.49; coordinating session ranks 21st | token fields exist on only 2 of 49 records (cost only); nothing more to fetch |
| 8 | Board | `.kit/config.json`, stores | M (no board) | unchanged | n/a |
| 9 | Owner's wait | coordinating session pages | M (one session): first reply median 50 s, p90 280 s; whole turn median 145 s | **M, recounted**: first reply median 57 s, p90 177 s, max 323 s; whole turn median 247 s, p90 ~26 min, max 7.6 h; gap between owner turns median 8 min, p90 40 min | page the other 48 sessions |
| 10 | Runners | `get_environment`, `list_computer_folders`, `list_triggers`, connectors list | M | unchanged | chat decisions: U (export-chat import) |

Other part2 numbers: models served opus-4-8 31, fable-5 12, sonnet-5 4, haiku-4-5 2; commits 721 all branches
(633 Claude, 85 owner merges, 3 bot), 697 on the default branch; 67 PRs, 66 merged, **1 open (#4)**; 68 merges on
default = 66 PR merges + 2 syncs; 51 remote branches; 8 branches ahead of default (7 + the survey's own); 6 session
branches without a PR; cache write 2.29 M tokens; output tokens in records sum to 5.3k (logged at stream start, not
usable).

## Scripts run
1. `python3 -I /home/user/board/scripts/what_fills.py data/pages --config /home/user/board/config/app.json`
   (from `survey/2026-11-20/`). Exit 0, stderr empty. Summary: 955 events (main thread, 0 subagents);
   287 main-thread requests; cache re-read summed 103.8 M tokens; context first 41k, last 728k, mean 369k;
   7,605,228 characters entered, "11.1 characters per token"; `result: Read` 94.2% of characters; re-read share
   OUT 3% / IN 97%; 0 board `get` calls. All shell buckets of `config/app.json` fell under the 0.5% print threshold.
   **Script limitation (no error raised):** it ran clean, but `text_len` counts inline images (base64) as
   characters: 18 of the 42 Read results carry screenshots, hence the absurd 11.1 chars/token and Read at 94%.
   Needs a **code change in `text_len`** (count an image block at its token cost, or skip it); no config field
   can fix it.
2. `mirror_holes.py` and `token_probe.py`: **not run** (no board, no dump).
3. Part2's recount used its own fresh parsing script over `data/pages/` plus git and the GitHub API (read-only);
   no errors recorded. One read was refused: listing the linked Vercel project's environment variable names was
   blocked by the permission classifier (so DB-variable ownership stays U).

## Part 2 outcome
A fresh subagent was given the data paths, not part1's conclusions, and read `part1.md` only after recounting.
**14 numbers changed** (attribution, total cost, top cost, survey session in scope, model mix, open PR, branches
ahead 7 → 8, HANDOFF sections, owner turns, first-reply and turn waits, gap p90, word-count inflation, hooks),
**2 new** (default-branch commits, branches without PR), **13 held**. It also settled 3 of part1's 5 unknowns
(attribution, cost, the 8 blocked questions), partly a 4th (DB variables), and found that the survey session had
pushed the kit into the project (2 commits) before the survey ran, despite "change nothing".

## Part 3 outcome: NOT RUN
A separate review session (`create_session`) cannot read this survey's data unless the private transcripts
(`data/pages/`, 36 MB, with screenshots and private app content) are committed somewhere it can clone, and the kit
repository is public. So the second pass used a fresh subagent instead (Part 2's fallback). Its yield (14 changed,
2 new, 3 unknowns settled) says an independent reviewer is worth it for this owner. **Kit finding:** the review
session needs a private data channel (e.g. a private branch or repo, or an attached private store) before Part 3 can
run on a project whose data cannot be public.

## Part 4: set-up check
- GitHub linked: `curl -s https://api.github.com/user` returns a login (not written here).
- The kit repository is attached **with push access**; the owner owns it, so no fork was needed.
- No personal token anywhere (none in env, none in chat).

## Part 5: the pull request
- Branch `step0/survey-2026-11-20`, files `survey/2026-11-20/{report.md, dev-report.md, config.json}` plus the
  scrubbed `part1.md` and `part2.md`; `data/` excluded.
- Anonymity: files grepped for names, logins, emails, session ids, artifact URLs and random branch suffixes before
  commit; branch names with random ids are written as "a claude/... branch".
- The pull request is to be **opened by the owner** (compare link given in chat, since it contains the login).
- Owner messages taken by Parts 4–5: **0 so far**, pending the owner's yes on the files.

## Config notes (`config.json`)
- Started from `config/app.json`; `talk: "fr"`, `project: "fr"`; `board_url: null` (no board); five project
  `shell_buckets` (captures, npm checks, hand-over docs, git, image work).
- **Main branch:** the repo's default branch is a `claude/...` branch, there is no `main`. `config/example.json` has
  no `main_branch` field, so it is noted here; the kit should add one (the install defaulted it to "main", see
  findings).

## Assumptions
- The longest-lived session (50 h, 07-12 → 07-14) is "the coordinating session", although this project has no
  coordinator: every session is owner-driven.
- Hand-over docs in the repo are current up to their last dated HANDOFF; chats hold only decision reasons.
- Kit files installed today are not pre-existing state.
- Part2 states owner-waiting items "5 to 7", but its own list (3 kept, 1 replaced, 3 added, PR #4 folded into the
  branches item) gives 8; Report 1 lists the 8.

## What the scripts could not read, and the fix
- Inline images in transcripts counted as characters (`what_fills.py`, `text_len`): code change.
- Token fields absent from most session records (cost only): nothing in config can fix it; report cost in dollars.
- Session records carry no repository field: scoping needs titles, branch names, then `get_session` on each. A
  config field such as `repo_match` (title/branch patterns) would make the first cut repeatable; confirmation still
  needs `get_session`.
- No board, so the board fields of the config are unused here.

## Kit findings (filed as issue #3 on the kit repository)
1. **The install skipped step 0**: the kit was installed and pushed before any survey.
2. **Import is chat-only**, while a project built with Claude Code can be auto-imported from repo + transcripts.
   This survey proves it: all **9 workstreams** and **8 owner-waiting items** were recovered without any chat
   (first pass 8 workstreams, 6 right, 1 stale, 1 wrong, 1 missing; 5 waiting items, 3 kept, 1 replaced, 1 extended,
   3 added by the second pass). Only decision *reasons* given in chats are lost.
3. **`main_branch` defaulted to "main"** though the repo has no `main` (its default is a `claude/...` branch).
4. **board.html translation cost**: translating the board page into the owner's language is a cost paid at
   install, even for a project that ends with no board.
5. **Stop hooks fire during the install** (they are active before the install has finished).
6. **Step 0 never asks "one board for all projects or one for this project"**: the survey swept all 507 account
   sessions before being narrowed to this repo's 49. Session records carry no repository field, so filtering needs
   titles, branch names, then `get_session`.
Also: Part 3 needs a private data channel (above); `talk` spelling differs between install ("French") and config
("fr").

## Does the kit's design fit this project?
Partly. The project already does by hand what the kit automates: every session ends with a committed hand-over,
CLAUDE.md keeps a dated state, and the owner is the only reviewer and merger. What it lacks is one short,
current place per workstream and one list of what waits on the owner: today those drift (CLAUDE.md 4 days behind,
8 "blocked" sessions that are only unarchived, one workstream absent from every summary), and `.story/` plus the
SessionStart hook fit that gap well. The board, clerk and reviewer roles do not fit yet: there is one owner, no
parallel sessions, no `send_message`, and the owner's checkpoints are visual (screenshots at two sizes) and must stay
inside the project's visual protocol. The biggest cost (screenshot-heavy contexts carried to 728k tokens) is not
something the kit addresses; a rule to start fresh sessions earlier would. Recommended: adopt `.story/` and the
hooks as a summary layer over the hand-over docs (which stay the source of truth), keep the board off.
