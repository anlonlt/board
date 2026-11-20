# Step 0 survey, Parts 0 and 1: project "app" (2026-11-20)

Project: a personal React/Vite self-exploration app, deployed on Vercel, built only with Claude Code on the web
(owner alone, no other users). Repository: one GitHub repo, default branch is a `claude/...` branch (no `main`).
Kit files `.kit/`, `.claude/hooks/kit/`, `.claude/settings.json`, `.claude/agents/kit-cards.md`, `.claude/skills/{board,story}`,
`.story/` were **installed today** (commits of 2026-11-20 06:18 and 06:20 on the survey's working branch) and are not surveyed as pre-existing.
Raw data: `data/` (see the index at the end). Grades: **M** measured, **I** inferred (from what), **U** unknown (what would settle it).

**Warning for Part 5:** `data/pages/` (36 MB) and `data/part0/` are raw transcripts. They hold private app content and
screenshots, and `data/git-log-all.txt` holds commit subjects. Do not commit them to the public kit repository. Commit only
this file and the scripts' outputs.

## Part 0: language

| what | value | grade |
|---|---|---|
| owner talks (this project) | **French**. Its 29 typed turns in the coordinating session have 1,806 French against 125 English function words (`data/pages-analysis.txt`) | M |
| project language | French: CLAUDE.md, every doc, commit subjects. Served content is bilingual `{fr, en}`, with French as the source | M |
| auto-generated session titles | mostly English (41/49), even when the owner writes French. A title is not a language signal | M |
| config | `talk: "fr"`, `project: "fr"` (written to `config/app.json`). The kit install wrote `"talk": "French"` in `.kit/config.json`: same meaning, different spelling | M |

Two more app sessions were sampled, one page each (`data/part0/*.txt`, kinds user, newest 100). They had 0 and 1 typed turns
(the one turn had no function words): the newest events are almost all tool results. One page per session does not always reach a
typed turn, so the language is measured on the coordinating session's 29 turns.

**Scope (owner's correction):** only sessions on the project repository are surveyed. `list_sessions` lists 507 sessions on the
account, and they carry no repository field. The filter:
- 49 candidates by title, in the window of the app's git history (07-05 → 07-24).
- `get_session` (the session context: sources and outcomes) confirmed the project repository for 9 of them: the coordinating session and the weakest title/branch matches.
- The other 40 have an outcome branch whose slug matches the title and exists in the app's remote heads.
- `get_session` showed 4 look-alike sessions (07-24 → 08-02) belong to another repository, so they are excluded.

**458 sessions excluded**, this survey session among them. Every raw page and summary already gathered from other repositories' sessions
was deleted from `data/` (`data/sessions/pages-summary.txt`, `data/sessions/verified-repo.txt`).

## Part 1

### 1. Roles: M (sessions) / I (attribution)
- `list_sessions mine:true` paged to the end. See the scope note in Part 0 for the filter.
- **This project: 49 sessions**, 2026-07-05 → 2026-07-20, plus one on 2026-07-24 (`data/sessions/app-candidate-sessions.tsv`).
  Origins: 41 web, 8 android. Buckets: 39 review_ready, 8 blocked, 2 failed. **No parent/child links, no tags, no orchestrator, clerk or reviewer
  sessions, no `send_message`.** Each session is owner-driven: one workstream step, its own branch, a PR the owner merged.
  The in-session multi-agent runs (the rejected "25 improvements" operation) used in-session subagents, not child sessions.
- Attribution: 9 sessions were confirmed with `get_session` (**M**). The other 40 were matched by their branch slug (**I**, strong).
  `get_session` costs from those 9: $4.07–$86.15 each. Token fields are absent on most July records (only `cost_usd`).
- Roles in practice: the **owner** decides, reviews screenshots and merges every PR. The **session** codes, captures and writes hand-over docs.
  "Fable" is named in CLAUDE.md as the source of a design direction ("direction Fable"): a model name, not a session role (I).

### 2. Stores: M
- **Repo docs** (default branch head): 46 Markdown files, 1.30 MB. CLAUDE.md is 24 KB. Hand-over/state docs: `docs/maquettes/2026-07-revamp-app/PASSATION.md`
  (1,273 lines, 9 dated HANDOFF sections 07-18 → 07-20), `docs/chantier-landing-passation.md` (1,115 lines),
  `docs/entretien-passation.md` (47 lines, the entry point for one workstream), `docs/passation-session-2026-07-12.md`, `docs/carotte-*`,
  `docs/maquettes/2026-07-soil-glsl/PASSATION.md`. Doctrine docs: `doctrine-heure-bleue`, `chantier-opus-{1,2,3}-*`, `methode-revamp`,
  `direction-resultats-canopee-sol`, `vision-design-lieu`. Notebook: `entretien-carnet-resultats*.md` (88 KB).
- Skills (pre-existing): `.claude/skills/visual-skill/SKILL.md` **98.6 KB**, `.claude/skills/redaction-lecons/SKILL.md` 9 KB.
- Large binaries in `docs/`: 772 JPG (164 MB), 9 PDF + 5 EPUB sources (33 MB). `docs/` totals about 212 MB.
- Memory folders: none before today (no `.story/`, no `memory/`). **No `.claude/settings.json` before today.**
- Board database: **none** (no board exists for this project).
- Data stores: the app's own localStorage and JSON export. A Postgres/Neon "reflection" is coded but dormant (per CLAUDE.md).
  The cloud environment holds variables named `DATABASE_URL` and `NEON_API`, but their values were not read and their owner is unknown (see item 6).
- Scheduled tasks: none for this project (item 10).
- Transcripts: the 49 Claude Code sessions (readable with `list_events`). The coordinating one is 17 pages of 0.08–4.9 MB, mostly base64 screenshots.
- Git: **721 commits** across all branches (633 Claude, 85 the owner's account (merge commits), 3 Vercel bot). 67 PRs (#1–#67), 68 merges on the default branch,
  **51 remote branches**, 7 of them with commits not merged (`data/branches-unmerged-commits.txt`). The session clone is shallow (493 commits visible), so full counts come from a bare clone in the scratchpad.

### 3. Channels: M (coordinating session) / I (others)
- Owner → session: typed chat turns (29 in the coordinating session), from the web (41 sessions) or the Android app (8).
- Session → owner: chat text, `SendUserFile` (6 calls, screenshots), `AskUserQuestion` (4), and the
  session's `needs_action` summary (8 sessions end "blocked" on an owner question). PRs are the delivery channel: the owner merges them himself.
  Vercel previews (bot commits) let him check on his phone.
- Session → session: only through committed docs (PASSATION/handoff files, CLAUDE.md "État & horizon"). No `send_message`, no
  board, no hooks, no timers.

### 4. Hooks: M
- Before today: **none** (`.claude/` held only two skills; no settings.json, no `.github/` CI).
- Installed today by the kit (not surveyed): SessionStart, PreToolUse(Bash guard; send_message), PostToolUse, UserPromptSubmit (×2),
  Stop (×2), PreCompact. All are generic kit hooks.

### 5. Rules: M (counts) / I (enforcement)
- CLAUDE.md: 6 non-negotiable directives, 6 visual-protocol rules (P1–P6), 8 "laws" of the design doctrine, plus conventions and verrous
  (decision locks: D13, D15, D18–D20, an item-collage contract). That is 14 numbered and 17 bulleted lines.
- visual-skill: 161 heading/list items in 14 sections, including lessons from each visual chantier (worksite). redaction-lecons: 41 items.
- Doctrine docs repeat or extend rules: "Heure bleue" laws, `methode-revamp`, the chantier doctrines.
- **Enforced by code**: only the npm checks (`banc`, `temoins`, `garde`, `frontiere`, `guichets`, `entretien`, `verre`, `e2e`)
  plus a start-up content assertion and an item-collage validator. They run **only when a session runs them**: no CI, no hook.
  In the coordinating session: temoins 17×, banc 12×, build 7×, garde 4×, frontiere 3× (`data/pages-analysis.txt`).
  Nothing enforces the visual protocol (captures before/after, one wave per owner look) except the skill text.

### 6. Facts kept twice: I (from docs and session summaries)
| fact | places | drift today |
|---|---|---|
| next step per workstream | CLAUDE.md "État & horizon"; each PASSATION/handoff doc; chantier doctrines; session `needs_action` | CLAUDE.md last changed 07-16, but revamp PASSATION has handoffs to 07-20 (v4 "real revamp of results"): CLAUDE.md lags 4 days |
| what waits on the owner | session `needs_action` (8 blocked sessions); "owner decision required" sections in PASSATION docs; CLAUDE.md | Blocked sessions from July were never closed. Some were settled later in docs (I) |
| decisions | CLAUDE.md (dated "actée"), D01–D24 journal (in docs), doctrine docs | Some decisions say they came from outside the repo ("direction Fable"; chats?) |
| lessons | visual-skill §§8–13, `chantier-landing-passation.md` § Leçons, doctrine docs | Same lesson in skill + passation (I) |
| ids (branches, sessions) | branch names in CLAUDE.md; session ids nowhere in repo | CLAUDE.md names a revamp branch (a `claude/...` branch), but later work landed via other branches |
| database state | CLAUDE.md: "no database configured" (2026-07-09) | Cloud env has `DATABASE_URL`/`NEON_API` names (values unread) — possible drift, U |

### 7. Cost: M (the coordinating session)
Coordinating session = the longest-lived one (a hand-over session, 50 h, 2026-07-12 19:37 → 07-14 21:49). get_session: model opus-4-8,
effort xhigh, `usage` = **cost_usd 53.30** (no token fields). All 17 pages read (no cap needed), in `data/pages/`.
Command: `python3 -I /home/user/board/scripts/what_fills.py data/pages --config /home/user/board/config/app.json` (from `survey/2026-11-20/`). Exit 0, **stderr empty**. Output verbatim:
```
events: 955 (main thread 955, subagents 0); 2026-07-12T19:37:37 -> 2026-07-14T21:36:15
main-thread requests: 287; cache re-read summed 103.8 M tokens; context first 41k, last 728k, mean 369k

characters that entered the main thread: 7605228 (context grew 687k tokens: 11.1 characters per token)
source                                                         blocks     chars  share  re-read
IN  result: Read                                                   42   7161424  94.2%    94.0%
IN  user turn: typed or delivered message                          29     59967   0.8%     1.2%
OUT its own chat text                                             192     80086   1.1%     0.9%
OUT its own call: file edits (Edit/Write)                          65     83621   1.1%     0.7%

re-read share: its own writing (OUT) 3%, what it was sent or read (IN) 97%

posts fetched with `get`: 0 calls on 0 posts (0 inline, 0 to files)
  most fetched: 
```
Reading it: the tokens (287 requests, 103.8 M cache read, context to 728k) come from `usage` and are measured. The **character shares do not mean
much here**. 18 of the 42 Read results carry images, and `text_len` counts their base64 as characters. That is why it gives
11.1 characters per token, and why Read shows as 94%. **Script limitation:** an image block should be counted by its token cost, or skipped. A config
field cannot fix this; it is a code change in `text_len`. The shell buckets in `config/app.json` all fell under the 0.5% print threshold.
By call count: Bash 102 (npm checks, git, capture scripts), Edit 61, Read 42, Task tools 33. The 3 largest costs:
(1) screenshots read back into context, (2) a context carried to 728k, (3) the long life itself (287 requests, mean 369k context).

### 8. Board: M
There is **no board**: no board database, no dump, and `.kit/config.json` has `board_url: null`. `mirror_holes.py` and the probe were not run.

### 9. Owner's wait: M (one session)
Coordinating session, 29 typed owner turns: the first assistant event after an owner turn comes at a median of **50 s** (p90 280 s, max 323 s). The whole
turn, up to the last assistant event before the next owner turn, takes a median of **145 s** (p90 ~25 min, max 7.6 h). Owner turns come a median of 8 min apart.
Across sessions, the wait for merges and next steps is days (08 blocked sessions never closed). U: the other 48 sessions were not paged.

### 10. Runners: M
| kind | state | this project needs it? |
|---|---|---|
| cloud | Default environment (personal, network unrestricted, no setup script). 7 env var names (some belong to another project), one network secret for a third-party host. Connectors in this session: Gmail, Google Calendar, Google Drive, Vercel, GitHub MCP, Claude Docs | yes: all work so far ran here. Vercel connector is relevant (deploys); Google/Gmail not |
| computer | `list_computer_folders`: none offered, none attached. No app session ran on a computer (all 49 are `anthropic_cloud`) | maybe: CLAUDE.md names a device-only bug class (the owner's phone/Chromebook GPU) that headless capture cannot see |
| chat | claude.ai chats exist but cannot be read from here | yes for decisions taken there (U) |
| owner | merges every PR, judges every visual wave (P3), arms keys (CLE_SERRE, DB) | yes, central |
| scheduled | `list_triggers`: none bound to an app session (the first page's entries all belong to another repository: out of scope) | no today |

### Unknowns (what would settle each)
- Attribution of 40 sessions matched by branch only: run `get_session` on each (strong match, not checked one by one).
- Cost of the 39 sessions not queried: run `get_session` (cost_usd) on each, and page the largest.
- Decisions taken in claude.ai chats ("direction Fable", "settled by the owner"): the export-chat import.
- Whether `DATABASE_URL`/`NEON_API` in the cloud env belong to this app: Vercel connector, or ask the owner.
- Which of the 8 blocked July questions are still open: the owner, or matching against later commits.

## Auto-import draft (repo + Claude Code transcripts only)

**How much can be rebuilt without the chats:** most of it, because this project hands over **in the repo**. Every session ends with a
committed handoff, and CLAUDE.md keeps a dated "État & horizon".
- Next steps: high. PASSATION "HANDOFF <date>" sections, "Prochaines vagues possibles", CLAUDE.md État, chantier doctrines.
- Decisions: high for the dated ones (CLAUDE.md "actée", D-journal, doctrine docs). Their *reasons*, when given in chats, are lost.
- Waits on the owner: medium. Session `needs_action` (8), "owner decision required" in docs, P3 ("one wave, one look"). Staleness cannot be judged from the repo alone.
- Ids: high (branches, PR numbers, session ids from `list_sessions`); session ids never appear in the repo.
- Lessons: high (visual-skill §8–13, passation § Leçons).

Sources per fact: CLAUDE.md; `docs/maquettes/2026-07-revamp-app/PASSATION.md` (latest, 07-20); `docs/entretien-passation.md` +
`docs/chantier-opus-3-entretien.md` + carnet; `docs/chantier-opus-2-serre.md`; `docs/chantier-opus-1-suite-resultats.md` +
`direction-resultats-canopee-sol.md`; `docs/chantier-landing-passation.md`; `docs/maquettes/2026-07-soil-glsl/PASSATION.md`;
`data/branches-unmerged-commits.txt`; session list `needs_action` fields.

Draft `.story/state/<ws>.md` (first line only):
- `revamp-heure-bleue.md`: Next: show the owner the v4 captures (descent + results revamp, handoff 07-20) and get his go for the next wave (P3).
- `resultats.md`: Next: resume the results backlog from `chantier-opus-1-suite-resultats.md`, one narrow worksite with before/after captures.
- `entretien.md`: Next: continue source ingestion (last committed lot: 8/27 bridges), then merge or drop the unmerged first-arc branch (lot 1, 3/5).
- `serre.md`: Next: phase 4 (hollow shell refined); phase 5 dormant; arming waits on the owner's key.
- `landing.md`: Next: none open in repo after 07-18 handoff; confirm closed.
- `soil-glsl.md`: Next: owner picks among "possible next waves" in the soil-lab PASSATION (blocked on owner).
- `persistance.md`: Next: dormant by owner decision (2026-07-09); reopen only with per-person keys.
- `mobile-adaptation.md`: Next: unmerged one-commit docs branch (07-24) meant for another project; owner decides where it goes.

Draft `.story/awaits.md` items:
- Look at the v4 revamp captures and say continue or adjust (revamp-heure-bleue).
- Choose the next soil-lab wave (soil-glsl).
- Decide the 7 unmerged branches: merge, keep or delete (results-review rejected 11 commits; liquid-glass 3; first-arc 3; etape-3 2; fork-essence 1; mobile-adaptation 1; vercel speed-insights 1).
- Confirm or close the 8 July "blocked" session questions (fallback rendering choice, merge scope, recrop captures, efficiency-audit lot, content stop point, phone re-test, results review, essence drag).
- Say whether the Postgres/Neon variables in the cloud environment are this app's (CLAUDE.md says no database).

## Data index (`data/`)
`sessions/` (filter summary, repo checks, the 49 app sessions) · `pages/p01–p17.txt` (the coordinating session, whole life) · `part0/` (2 app user pages) ·
`what_fills-output.txt`, `what_fills-stderr.txt` (empty) · `pages-analysis.txt` (tool counts, language, waits) · `coordinating-session.txt` ·
`triggers-summary.txt` · `environment.txt` · `git-*.txt`, `ls-remote-heads.txt`, `branches-*.txt` · `repo-md-inventory.txt`, `repo-docs-sizes.txt` ·
`state-docs-keyword-counts.txt`. Config written: `/home/user/board/config/app.json`.
