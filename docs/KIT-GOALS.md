# Why the kit exists: the goals, the problems, and which part serves which

This file is what an installing agent reads to say, for **its** project, which parts are worth installing (README
§ Why, step 0's recommendation). Every goal and problem below was stated by a person, quoted with its date and source;
every number was measured, with its source. A part is worth installing only when its problem is present in the project.

## 0. The owner's goals in one list (his founding comment, 2026-11-19 23:35, and the comments before it; quotes in § 1)

1. **One UI for everything agents make**: no digging through GitHub folders to stay in the loop.
2. **No recap to ask for**: every new session, and the owner, see where we are and what waits on him, at a glance.
3. **Continuity across sessions and context limits**: automatic hand-overs and agent spawning, so it feels like one
   unified team that manages itself; nothing lost to a crash or a reset.
4. **A record of every discussion**: nothing said is lost, whatever session it was said in.
5. **No sprawl, no double copies**: git is the one store; every post and comment a file; facts pulled by script, each
   with an anchor to its source.
6. **Phone-first and token-efficient**: answer from the phone in a tap; a Like costs no more than "yes" in chat; one
   place per result.
7. **Anyone can install it** (his friend first), adapted to how they already work (step 0).

## 1. The goals and problems, as they were said

**The kit's owner** (Harmonize, a project run by many Claude Code sessions in parallel, answered from his phone):

| when, where | his words |
|---|---|
| 2026-11-08, Harmonize `CLAUDE.md` | "The previous source of this app was lost to an agent deleting it." |
| 2026-11-12 09:20, chat | "we'd need a hook that basically makes sure agents aren't spending triple tokens, replying in chat, to you, and in the board" |
| 2026-11-12 09:20, chat | "a card at the top of the board, says where we are at, what step is next, in all the workflows … Basically an at a glance card." |
| 2026-11-12 09:20, chat | "so that it uses virtually as many tokens if not less to post or comment for an agent as it would've to just reply in chat" |
| 2026-11-12 09:29, board | "If you receive this immediately after limit reset, good." (a comment written during a usage limit must still reach its session) |
| 2026-11-12, board | "if they're not posting anything on the board, shouldn't they be giving you a full report instead of being limited by what they can send you ?" |
| 2026-11-12 20:00, chat | "Shouldn't the board clerk be limited to 0 character messages, since all it does is relay stuff ?" |
| 2026-11-13 00:29, board | "likes should be the equivalent token cost of me saying yes in chat" |
| 2026-11-15 23:30, board | "Why is everyone telling me to do it ? If it notifies such things, it should do it itself" |
| 2026-11-12 21:35, board | "How much work has been lost because of these crashes, and are agents saving their work … so that they can continue exactly from where the crash happened ?" |
| 2026-11-15 21:15, board | "Shouldn't what's waiting for me available at a glance ? I feel like with all these comment threads, and open news posts, and stuff, it's getting hard to navigate around" |
| 2026-11-15 21:23, board | "allowing me to see everything without having to dig through GitHub" ; "in the beginning, things were kind of being done automatically by the orchestrator, the project was running itself" |
| 2026-11-15 23:51, board | "Any way to keep a log of context drift ? … that tracks the source of everything that's being copy and pasted from prompt to prompt, message to message, with always an anchor to its source" |
| 2026-11-16 00:58, board | "the original at a glance view should be primary, clicking on a train uncollapses the view into the train view of that train, and the waiting for you should be posts in the waiting for you page" |
| 2026-11-19 23:35, board, **the kit's founding comment** | "using GitHub as the front UX is annoying because the owner has to go and dig through folders, files and stuff to find whatever has been created in a project to stay in the loop, or waste tokens asking an AI for a recap at every new discussion" |
| 2026-11-19 23:35, same comment | "The story skill … with the added benefit of cross conversation continuity" ; "the possibility of having a record of every single discussion" ; "saving context by automatic discussion and agent spawning and review workflow" |
| 2026-11-19 23:35, same comment | "keeping the impression that the owner talks to one unified team no matter the discussion, and a familiar, social media kind of UI and UX, with the impression that everything manages itself fluidly" |
| 2026-11-19 23:35, same comment | the tradeoff to remove: "doubling everything that's already made and being updated in GitHub, context sprawl that creates the need of a rule ledger" ; the answer: "stores everything in the GitHub, but with a UI that shows it all … every post was a file … every comment was a seperate file … pulled through scripts in an instant" |
| 2026-11-19 23:35, board | "merge the story skill, the board, the hooks and GitHub into one thing … git as the only store … a skill/hook/UI collection anyone could use" |
| 2026-11-20 00:05, board | "there would first have to be a step to evaluate the current architecture of the project, see how it runs, how it uses its tokens, to properly adapt the skill/board/hooks to their own project" |
| 2026-11-20 00:19, board | "a seamless experience - a Claude independently goes through all its previous chats … it should guide the user in everything it needs to do" |
| 2026-11-20 02:39, board | "The goal is so that my friend (and anyone else) can use it, and to optimize my use of it in parallel" |
| 2026-11-20 03:05, board | "so that the data of his board stays private, but if he complains about stuff … are automatically created in my public board" ; "is it a simple install + link to my repo" |
| 2026-11-20 03:13, board | "Why are there branches ? Shouldn't everything be merged to main ?" |
| 2026-11-20 06:3x, chat | "It doesn't seem like all the problems I mentioned, and the goals I had mentioned, were clear enough for the agent to find a reason for the board." |

**His friend** (claude.ai chat only; step 0 report of 2026-11-19, PR #1, and issue #2; his words are French, quoted from the reports):

| when, where | what was said or measured |
|---|---|
| 2026-11-19, report § costs | "Relancer à la main": 84 of his 288 messages in one long chat are "continue" |
| 2026-11-19, report § costs | "Pas de mémoire entre conversations": "comme la dernière fois" asked twice; style rules re-said each week; ≥ 24 "oui / ok" and 19 authorisations |
| 2026-11-19, report § costs | a context filled by tools (59 % tool results), compacted 8 times in 3 days, each time summarising the state |
| 2026-11-19, report § must not touch | production data and the school's personal data; any real action (publish, share, send) without his approval |
| 2026-11-19, dev report | a privacy warning made him give his consent twice, "which he flags as a problem" |
| 2026-11-20, issue #2 | the export did not carry enough to resume "sans réexpliquer"; his work runs in Microsoft 365 through Claude in Chrome, not in code |

**An outside Claude Code project** (issue #3 and its survey, 2026-11-20; one owner, one session at a time):

| when, where | what was said or measured |
|---|---|
| 2026-11-20, issue #3 item 7 | "The owner works one session at a time, in chat. A session's first reply comes about 57 s after the owner's message (median), so a clerk relaying board comments adds a hop and saves nothing." |
| 2026-11-20, issue #3 item 7 | "What does help here is the kit's memory": 9 workstreams, 8 things waiting on the owner "scattered across hand-over docs and 'blocked' session states" |
| 2026-11-20, survey report | CLAUDE.md's state section 4 days behind the last hand-over; one session carried 728k tokens of context; 49 sessions cost $2,493.64 |
| 2026-11-20, issue #3 item 6 | step 0 never asks "One board for all your projects, or one for this project?" |

## 2. Each part: the problem it solves, the evidence, its cost, and when it is NOT worth it

Harmonize's numbers are from `anlonlt/harmonize` (`docs/WORKFLOW-KIT.md`, `docs/ORCHESTRATION.md`, the board's post
`M1-board-cost`); "not measured" says so.

| part | the problem (whose words, § 1) | evidence | its cost | NOT worth it when |
|---|---|---|---|---|
| **memory** (`.story/`: one state per workstream with its `Next:`, `awaits.md`, issues, lessons; skill `story`; the start hook prints it) | "Pas de mémoire entre conversations" (friend); 8 waiting items "scattered" (issue #3); a session starts by re-reading hand-overs | Harmonize: 22 workstreams and 31 waiting points served in 6.6k characters at each start, instead of re-reading 1.3 MB of docs (issue #3's repo) or 492k of log; the issue-#3 survey rebuilt 9 workstreams from the repo alone | a save point per step (a file rewrite, a commit); 6.6k characters per session start | never: install it first, always |
| **guard** hook (refuses `rm -r`, `git clean`, `reset --hard`, force pushes, secrets) | "lost to an agent deleting it" (2026-11-08) | not measured (no count of refusals kept); its tests plant each command | none (a refusal costs a turn) | never |
| **stop / pre-compact hooks, `end_session.py`** | a reclaimed container loses what is not pushed; "Shouldn't everything be merged to main ?" | Harmonize: 5 of 180 sessions ever archived, by hand; his question came from finding work on branches, not on main | one blocked stop when work is unpushed | never (quiet during the install itself, `kit.setup`) |
| **"Where we are" + "Waiting for you"** (the cards, built from the memory and the posts; the start hook prints the same) | goal 2: "waste tokens asking an AI for a recap at every new discussion"; "Shouldn't what's waiting for me available at a glance ?" | Harmonize's start hook serves 22 workstreams' next steps and 31 waiting points in 6.6k characters; "waits on him" was in 5 places before (WORKFLOW-KIT § 1.6) | built by a script or a subagent, not by hand | never for the memory side; the cards need the board |
| **the board as the UI to the repository** (posts, their files and pictures, the repo docs reader, deliverables) | goal 1: "dig through folders, files and stuff to find whatever has been created" | every Harmonize result has a post: 173 posts, 1,279 comments in 8 days (WORKFLOW-KIT § 1.2) | the page and its upkeep | the owner reads the repository himself and is happy to |
| **hand-overs at context limits** (`kit_pre_compact.py` + `handover.py`: the orchestrator at its limit, the clerk at its limit; `.story` and the post carry the state) | goal 3: "automatic discussion and agent spawning"; "continue exactly from where the crash happened" | Harmonize: 47 orchestrators and 27 clerks handed over in 10 days, each takeover ~150k for the orchestrator; a container reclaimed loses only what was not pushed (the stop hook) | a successor's first read (~150k for an orchestrator) | one short session at a time (no limit is reached) |
| **one store, built copies** (git holds every post and comment as a file: `kit.py` copy and lookups; cards built, never hand-written) | goal 5: "doubling everything … context sprawl that creates the need of a rule ledger" | Harmonize before: 8 kinds of fact in 2 to 5 places each, 52 extra copies of rules, 5 drifts measured on one day | one sync per launch | a board small enough to read whole |
| **board page** (posts in columns, comments, one-tap answers, pictures) | "triple tokens, replying in chat, to you, and in the board"; answering from the phone; "an at a glance card" | his wait for an answer: median 0.8 min, 18 of 398 comments over 15 min since 2026-11-12; recent hand-over windows 0 over 15 min; 1,279 comments in 8 days, 449 his. **Tokens: about even, not fewer** (M1b: parts report half as much, the orchestrator's reviews grew as much) | publishing reads the 146 KB page once; each post write; the clerk below | **one session at a time, the owner in that chat** (issue #3: 57 s to a first reply; the chat is already the channel). It pays once sessions run in parallel, or the owner answers later or from a phone |
| **clerk** (forwards each comment to the session that wrote the post) | a page cannot message a session; "If you receive this immediately after limit reset, good." | forwarded within about a minute of a usage-limit reset (one sample, 2026-11-12); his median wait 0.8 min | Haiku, low: BC27 $1.20 for 4 h 10 min, 209k context; replaced past 200k (22 clerks in 8 days) | no board; or only one running session at a time (he answers in its chat) |
| **orchestrator** (launches parts, relays, checks each Done, hands over) | "to optimize my use of it in parallel"; "Why is everyone telling me to do it ?" | Harmonize runs many parts at once through it; 47 orchestrators since 2026-11-10 | the largest: orchestrator 46 re-read 70.8 M cached tokens, $24.88 in 5 h 15 min; a takeover ~150k | **no parallel work**: one session at a time needs nobody to launch or check others |
| **cards** (`kit-cards` agent: "Where we are", "Trains") | "an at a glance card" | a full board dump costs 119,869 tokens when a subagent does it, kept out of the orchestrator's context; the orchestrator's own estimate for its `board-cards` agent: −20 % of its context (not re-measured here) | one Sonnet subagent per launch and Done | no orchestrator |
| **consultant** (one fresh review at a Done that sets a route) | a Done that holds only on paper | CR-WF1 found what WF1 left "unknown" (1.7 characters per token, not 4; the board 22 % of context, not most); the friend's second pass changed 4 points | one review per route-setting Done | small work with no route decisions |
| **`kit` copy** (`board-mirror` branch, `kit.py` lookups) | reading the board costs whole dumps | a post read 35.9k → 2.9k characters; his open comments: a ~120k-token dump → 290 characters | one sync per launch | no board, or a board small enough to read whole |
| **import** (`step0/export-chat.md` for chats, `step0/import-code.md` for Claude Code) | the kit starts empty; "comme la dernière fois" | the issue-#3 survey rebuilt 9 workstreams and 8 waiting items from the repo and transcripts | one pass at install | a new project with no history |
| **runners** (README § Runners) | the friend's work runs in Microsoft 365 through Claude in Chrome, not in code | his step 0 found it; a cloud session cannot reach his browser unless his computer is attached | a one-time setup per place | all the work runs in cloud sessions |
| **step 0** (the survey, then this recommendation) | "evaluate the current architecture … to properly adapt" | the issue-#3 survey is what found that the board adds little there | one survey session (+ a reviewer) | never skipped; `--no-survey "<why>"` records the owner's refusal |

## 3. The recommendation, from what step 0 measured

Step 0 answers four questions, then recommends; the install takes exactly that (`--no-board`, `--no-clerk`,
`--no-orchestrator`, or `--parts`):

1. **Scope** (asked first): one board for all the owner's projects, or one for this repository? It sets which sessions
   the survey reads (only this repository's, unless "all").
2. **Parallel:** how many sessions ran at the same time (from `list_sessions` start/end times or the chat dates)?
3. **Away:** does the owner answer later, or from a phone (answers hours after the question; messages typed on mobile)?
4. **Outside code:** does any work run somewhere a cloud session cannot reach (a browser, a desktop app, chat connectors)?

| what step 0 found | install | why (§ 1) |
|---|---|---|
| one session at a time, the owner answers in its chat | **memory + hooks + import** (`--no-board`) | "Pas de mémoire entre conversations"; 8 items scattered; the chat is already the channel (57 s) |
| the same, but the owner wants approvals or questions waiting for him on his phone | memory + board, no clerk or orchestrator (`--no-clerk --no-orchestrator`); each session reads its post's comments at its next turn | answering from the phone; one-tap answers |
| sessions run in parallel, or he answers later or from a phone | memory + board + clerk | "triple tokens"; a comment must reach the session that wrote the post |
| more parts than the owner can launch and check by hand (about 3 or more at once) | everything: + orchestrator, cards, consultant | "to optimize my use of it in parallel"; "it should do it itself" |
| work outside code | + the runners it needs | Microsoft 365 through Chrome |

**Goal 4 is only half served (named, not hidden):** what a session writes on the board, and every comment, is kept as a
file in git. **What the owner types in a session's chat is not**: it lives only in that session's transcript (in
Harmonize, 19 standing rules quote chat words found nowhere on the board, WORKFLOW-KIT § 1.6). The say-it-once hook
keeps the answers on the board, but his own chat words stay in the chat. The fix proposed: a UserPromptSubmit hook that
appends each typed message, verbatim with its time and session, to the session's post as a comment `from: "owner",
via: "chat"` (or to `.story/said/<date>.md` without a board). Not built; the owner decides.
**The drift log with anchors** ("an anchor to its source", 2026-11-15 23:51): partly, every post names its
`session_id`, every comment its `post_id`, a step its `sources`; a full source chain from prompt to prompt is not built.

**Not served by a kit part:** typing "continue" (84 of the friend's 288 messages) is a limit of the chat itself; a
Claude Code session runs a step to its end without it, so the answer is where the work runs (§ Runners), not a part.
The old reports (PR #1, issue #3) predate § 3's questions on parallel sessions and away answers: a survey run now
measures both.

Each line of the survey's recommendation quotes the goal it serves, so the owner sees why, and can say no.
