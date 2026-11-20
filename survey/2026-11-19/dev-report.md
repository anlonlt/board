# Step 0 survey — dev report (chat-only user), 2026-11-19

Anonymised: no names of people, organisations, vendors or identifiers.

## Set-up friction (reported at the user's request, in his own terms)

- **Wrong settings location.** The survey text says "Settings → Capabilities"; the assisting Claude repeated it ("Paramètres → Fonctionnalités"). In this claude.ai version the toggle "Search and reference chats" is under **Settings → Memory**. The user had to find it himself and flagged it.
- **Wrong tool status.** The assisting Claude told the user the chat-history tools were unavailable in the current conversation. They were in its tool list. One extra round trip.
- **Warning and double consent.** The assisting Claude added a privacy warning (public repository, Quebec privacy law). When the user answered with a truncated go-ahead, it asked again with a choice card. The user had to state his acceptance twice and repeat himself, which he flags as a problem.
- **Suggested fix:** correct the settings path in `step0/survey-chat.md`; state the privacy handling once, up front, and accept a single go-ahead; when a message is cut off but the previous turn makes the intent clear, proceed instead of asking again.

## Tools available

- Chat history: `recent_chats` (3 calls; 14 chats, oldest 2026-10-14; paging back returned nothing; the reviewer counted 16 including the long chat), `conversation_search` (6 calls) and `read_conversation` (1 call, to fetch the review).
- Code tool: yes (bash). Used on the current conversation's own compaction transcripts (8 files, 288 user messages, 2026-10-28 → 2026-11-20).
- Chat or session creation: **no such tool** (see Part 3).
- Survey started inside an existing, very long work conversation rather than a new chat; this is what the user did, and it worked once the history tools were used.

## Questions (Part 1 grade → Part 2 grade)

| # | Searches / sources | Part 1 | Part 2 | What would settle it |
|---|---|---|---|---|
| 1 Uses, frequency, projects | recent_chats ×2 | seen | seen (long chat start corrected to 2026-10-28) | — |
| 2 Where work lives | recent_chats, transcripts | seen; custom instructions unknown | same | ask the user / settings screenshot |
| 3 How a new chat resumes | "continue où on était" | seen ("comme la dernière fois", pasted summary from another AI) | seen | — |
| 4 Chat length | transcripts | seen | seen (8 compactions in 3 days) | — |
| 5 Repeated instructions | searches + transcripts | inferred | partly seen: "comme la dernière fois" ×2 in one chat; standing rules in each of 8 summaries | full data export |
| 6 Corrections | searches | seen | seen | — |
| 7 Several chats, one goal | recent_chats | inferred | inferred; corrected by the reviewer: 3 automation chats, **different** processes; transfer method unknown | ask the user |
| 8 Usage limits | "limite d'utilisation atteinte" | unknown | no usage-limit message found; per-turn action budget forced 84 "continue" relaunches (seen) | data export |
| 9 Outputs | recent_chats, transcripts | seen | seen | — |
| 10 What fills a long chat | code tool on transcripts | unknown | seen: tool results 58.7 %, tool calls 31.8 %, Claude text 7.4 %, thinking 1.9 %, user words 0.3 % of ~7.1 M parsed characters | — |
| 11 GitHub / Claude Code / API | "GitHub Claude Code API" | seen: none | seen: none; heavy use of the browser extension | — |

Characters are counted, not tokens; no usage data is visible in chat.

## Part 2 — what the second pass changed

4 changes: Q10 unknown → seen (numbers); Q8 unknown → partly seen (relaunch count); Q5 inferred → partly seen; Q1 start date of the long chat corrected (2026-11-17 → 2026-10-28). Still unknown: custom instructions, how results move between chats, any real usage-limit hit.

## Part 3 — reviewer

- No tool to open a separate conversation. A review prompt (under 3,000 characters) was written; the user pasted it into a new chat.
- **The reviewer's results stayed in its own chat.** It did not update the report, and the user had to say so ("the review session does not seem to have added anything to the report, it just put its results in the discussion"). The assisting Claude then fetched them itself with `recent_chats` + `read_conversation`, without asking the user to paste them back. **Fix:** either tell the user the main chat will fetch the review through chat history, or have the review prompt end with a block formatted for the report.
- **What it changed: 4** — (1) chat count: 15 short + 1 long = 16, not 14 + 1; (2) the long chat's total: `read_conversation` reports 616 turns, about 308 user messages, not 288 (the gap matches messages sent after the last compaction transcript); (3) Q5: "as last time" was asked 2 times, Claude failed only the first; a third request (highlight what was already published last week) also assumed memory; (4) Q7: the three automation chats serve **different** processes, not one goal.
- **Missed by the survey, per the reviewer:** a second recurring chat (the weekly bulletin, several weeks of editions); a strongly agentic use (browser extension, Microsoft 365 connector, Claude Docs acting directly in the environment).
- **Unverifiable for the reviewer** (it could not read the compaction transcripts): the long chat's start date, the 8 compactions, the 84 "continue", the 24-character median. The reviewer found only a 2026-11-17 timestamp on the first message; the transcripts give 2026-10-28 for it. Both are recorded; the transcripts are the primary source.
- **Verdict:** a reviewer is worth keeping in this user's kit (4 changes out of 9 lines), provided its results flow back without the user copying them.

## Part 4 — set-up steps

| Step | Outcome | User's words where it went wrong |
|---|---|---|
| 1 GitHub account (github.com/signup) | done | — |
| 2 Claude Code on the web (claude.ai/code) | done | — |
| 3 Connect GitHub (claude.ai/connect-github) | done, **but not sufficient on its own** | "nothing works unless you also go to https://github.com/apps/claude to install it" |
| 4 Install the Claude GitHub App | done by the user himself, via https://github.com/apps/claude | the guide presents it as a separate later step; it should be merged with step 3 or announced at step 3 |
| 5 Fork `anlonlt/board` and give the app access to the fork | done | the user asked for the Claude Code block already at this step, to save a round trip: the guide should hand over the final block with the fork step |

## Set-up check (Claude Code)

- Repository seen: the user's fork of `anlonlt/board`. Branch created and pushed: `step0/survey-2026-11-20` (first try).
- **The reports did not reach Claude Code.** The text it received did not contain them, and its branch name (`-11-20`) differs from the one the filled block specified (`-11-19`): most likely the survey's unfilled template (with `<Report 1> <Report 2>` placeholders) was pasted instead of the filled block the chat had prepared as a text file. Claude Code searched the user's ~55 claude.ai artifacts and the fork, found no report, and stopped rather than invent content (correct behaviour). It also had no API access to `anlonlt/board` to check pull requests. It said plainly that it cannot read claude.ai chats.
- The user's reaction: "why wasn't it added?!" Two different blocks (the template in `step0/survey-chat.md` and the filled one) invite this mistake.
- **Recovery, first try:** the chat Claude, which holds the reports, tried to push both files to the same branch through claude.ai's GitHub connector: refused, `403 Resource not accessible by integration` (the connector has no write access to the fork).
- **Recovery, second try:** since Claude Code can read the user's claude.ai artifacts (it listed them), the chat Claude published the filled block (instructions + both reports) as a private claude.ai artifact, and the user gave Claude Code a one-line instruction to read it and finish.
- **Fix:** hand the reports to Claude Code as a claude.ai artifact (or a file in the fork) rather than as a 14,000-character paste; never show the user the unfilled template, only the filled block; have Claude Code refuse to run if the block still contains `<Report 1>`. A GitHub connector in the chat is not enough on its own: it needs write access to the fork.
- **Not anticipated (the assisting Claude's error, flagged by the user):** the assisting Claude should have planned this hand-off before Part 5: checked whether the GitHub connector could write to the fork, and used the artifact route from the start, instead of a 14,000-character paste. The user had to go through a failed Claude Code run, a failed connector push and a third attempt before the reports could land.

## Part 5 — pull request

Commit: worked on the first try. Push to the fork (branch `step0/survey-2026-11-20`): worked on the first try. Pull request into `anlonlt/board`: **refused on the first try**, not worked around. Exact error (REST `POST /repos/anlonlt/board/pulls`, HTTP 403): `GitHub access to this repository is not enabled for this session. Use add_repo to request access. If add_repo answers that read access is already available and you need GitHub API or write access, call add_repo again with access:"push" to attach the repository with credentials.` The Claude Code session can write only to the repository attached to it (the fork), not to the upstream repository. No pull request link: the user was given the compare link (`anlonlt/board` main ... fork:`step0/survey-2026-11-20`) to open it himself.

## Assumptions; questions that did not fit

- Q10 assumes readable long chats; here only the current chat's own compaction transcripts were readable at full length.
- "Usage limits hit" does not separate plan limits from the per-turn action budget that forces manual relaunches; the second is the real cost for this user.
- Missing question: how often the user must confirm or re-approve, and whether that is a cost (here it is).

## Proposed config

- Language: French; English for code and technical reports.
- Roles: school administrator and network administrator; Claude as builder working in the browser.
- Stores: Microsoft 365 environment (production), compaction summaries (de facto project memory), Claude documents.
- Limits that matter: per-turn action budget; context filled by tool payloads; no memory across chats until now.
- Reviewer: yes (Part 3 changed 4 of 9 lines), provided its results flow back without the user copying them.
- Board would show: per project, decisions, identifiers, current state, next steps, and actions waiting for the user's approval.

## Can the kit serve this person?

Partly. A board as the face of a git-held state would replace the compaction summaries he now relies on and the "as last time" re-asks. But his work lives in a Microsoft 365 environment driven through a browser extension, not in a code repository; the kit must mirror that state rather than own it. It must also keep his approval gates (publishing, sharing, real emails) and his privacy constraints (no personal or school data in a public repository).
