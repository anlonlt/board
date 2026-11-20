# Kit step 0, the survey: the version for a chat-only user (Claude on claude.ai, no Claude Code yet)

Moved here from `anlonlt/harmonize` (`docs/kit/step0-survey-chat.md`, part BD9) on 2026-11-20. Written on the owner's comments under post `WF1-kit-design` (00:10, 00:19, 00:29): a friend who works
only inside Claude's chat tests step 0. **Seamless:** the Claude that runs it finds the user's language, goes through
the user's previous chats itself, answers the survey's questions from what it finds, then **works harder on every
unknown** (a second, adversarial pass, as the consultant's review CR-WF1 did on WF1), tests whether an agent can open
a separate review conversation, guides the user through every set-up step with direct links, and writes a full dev
report so the step can be improved. Its last part ends in a **pull request** into this repository (public: from the user's fork), opened by Claude Code: the PR proves the GitHub set-up
works (connected account, app installed, push allowed) and carries both reports. The Claude Code version of the
survey (it runs scripts) is `docs/WORKFLOW-KIT.md` § 0 in `anlonlt/harmonize`.

**From the owner's own step 0 (WF1 + CR-WF1), what carries over to chat:**

| Harmonize tool | what it did | in chat |
|---|---|---|
| WF1's inventory questions (§ 1-2) | roles, stores, channels, rules, facts kept twice, costs | the 10 questions below |
| `what-fills-an-orchestrator.py` | context by source, from transcripts | the same count on the chats it can read (pasted material vs its own replies vs files), run in the code tool if available |
| `answer-delay.py` | how long he waits | how long between his message and a usable answer, where chats show retries or "that's wrong" |
| `mirror-holes.py` | what a copy would miss | not applicable (no board yet); kept for the Claude Code step |
| CR-WF1's review | recounted every number on fresh data, found what WF1 left "unknown" (1.7 chars per token, not 4; the board 22 % of context, not most) | Part 2: the second pass |
| tokens from `usage` | measured, never at 4 chars | chat shows no usage: characters are counted; a token figure is marked "estimated" with the ratio used |

**How to use:** the tester opens a NEW conversation on claude.ai and pastes everything between the two lines below.
Chat search must be on (Settings → Capabilities: "Search and reference chats"). The code tool (analysis) helps if on.

---

You are running **step 0 of a workflow kit: the survey**, by yourself. The user does nothing until Part 4.

**Part 0: language.** From the user's recent chats, find the language they write in and any language they ask
Claude to answer in (custom instructions, "answer in …"). Write everything after this in that language. Record it
(and any split, e.g. "talks French, writes code and docs in English") for the config. If the chats disagree, say
which you chose and why, in one line, and continue.

**Part 1: survey, alone.** Use your chat-history tools (`recent_chats`, paging back as far as they go;
`conversation_search` with many searches). If they are not available, say so in one line with how to enable them,
and stop. Ask the user nothing. For each question, answer from evidence, cite the chats (title, date), grade each
answer **seen**, **inferred** (from what) or **unknown** (which searches found nothing). Never invent a number.
Count what you can (chats per week, message lengths, repeats); if a code tool is available, use it for the counts.

Searches to run (add your own): project names you find; "continue", "where were we", "as I said", "remember";
"GitHub", "repo", "token", "Claude Code", "API"; "limit", "too long", "new chat"; "instructions", "rules", "wrong".

1. What do they use Claude for, how often (from dates), in how many projects?
2. Where does their work live between chats (Projects, knowledge files, custom instructions, pasted notes, documents)?
3. How does a new chat learn where they left off? Quote one opening.
4. How long do chats get before a new one starts, and why?
5. Which instructions or facts do they repeat, in how many chats (count)?
6. What did they re-explain, re-check or correct because Claude forgot or contradicted something (list cases)?
7. Do several chats serve one goal at once; how do results move between them?
8. Usage limits hit: when, doing what?
9. What do they produce, and where does it end up?
10. What fills a long chat: for the 3 longest chats you can read, characters by source (their pasted material, their own words, your replies, files), and which part is repeated from earlier chats.
11. Any trace of GitHub, Claude Code, a terminal, an API key, a repository?

**Part 2: the second pass (work harder on the unknowns).** Act as a fresh reviewer of Part 1. For every answer graded
unknown or inferred: run at least 3 more searches with different words, open the full chats behind the best hits,
and try to settle it. For every number: recount it from the evidence another way. List what changed from Part 1
(answer, grade, number) and what is still unknown, with what would settle it (a data export: Settings → Privacy →
Export data; a question to the user; a tool you lacked).

**Part 3: the review test (does this user need a separate reviewer?).** Try to open a separate conversation for an
independent review (a tool that creates a chat or a session). Record whether you could, with the tool's name or
"no such tool". If you cannot, write a review prompt (under 3,000 characters: Part 1 and 2's tables and the
instruction "recount, find what this missed, do not trust it") and ask the user to paste it into a new chat and paste
the answer back. Record: whether they did; how many answers or numbers the review changed. That count is the evidence
for or against making a reviewer part of their kit.

**Part 4: set-up guide, with the user.** From question 11, skip what is done (say how you know). One step at a time;
wait for "done"; if a step fails, ask for the exact error text.
1. A GitHub account, if none is recorded: https://github.com/signup
2. Claude Code on the web (where the kit runs): https://claude.ai/code
3. Connect GitHub to Claude: https://claude.ai/connect-github
4. Install the Claude GitHub App on the kit's repository: https://github.com/apps/claude/installations/select_target
5. A repository if none (https://github.com/new, private, with a README), then select it when starting a session at https://claude.ai/code.
6. A personal token only if a later step needs one (the app covers git): https://github.com/settings/personal-access-tokens/new, fine-grained, that repository only. Never pasted into a chat or a plain environment variable: in Claude Code on the web it goes in the environment's network secrets, which the session asks for with a secure form.

**Part 5: the pull request (the set-up's proof, and where the reports land).** When Part 4 is done, write ONE block
for the user to paste as the first message of a new Claude Code session at https://claude.ai/code, with the kit's
repository selected. Tell them: "Open claude.ai/code, pick the repository, paste this, send; then come back and tell
me the PR link or the error." The block, filled in:

> You are finishing step 0 of a workflow kit. The reports go to the kit's public repository `anlonlt/board`: work in
> the user's fork of it (if the selected repository is not a fork of `anlonlt/board`, say so and how to fork it:
> https://github.com/anlonlt/board/fork, then start a session on the fork). Create a branch `step0/survey-<YYYY-MM-DD>`. Write `survey/<YYYY-MM-DD>/report.md`
> (Report 1, below) and `survey/<YYYY-MM-DD>/dev-report.md` (Report 2, below), then add to the dev report a section
> "Set-up check (Claude Code)": the repository you see, the branch, whether the commit, the push and the pull request
> each worked on the first try, and any error verbatim. Commit, push to the fork, and open a pull request INTO
> `anlonlt/board` (base main, head <user>:<branch>) titled
> "Step 0 survey <YYYY-MM-DD>" whose body is the dev report in full (with that section) and a link to the report.
> If the pull request into `anlonlt/board` is refused, open it inside the fork instead and say why in the dev report.
> If any step fails, do not work around it: write the failure in the dev report, and if the pull request cannot be
> opened, print the dev report here so the user can copy it. Reply with the PR link.
> <Report 1>  <Report 2>

When the user comes back: a PR link means the GitHub set-up is proven (say so, in their language); an error means
Part 4's failing step is named from it, and the guide resumes there. Add the outcome to the dev report.

**Report 1: the survey** (for the user and the owner, in the user's language): a table of part · what they do ·
where it lives · copies of the same fact · cost or friction · seen / inferred / unknown. The 3 largest costs with
numbers, the facts kept twice, what a kit could take over, what it should not touch, and the language setting.

**Report 2: the dev report** (for whoever improves this step, in English; complete, it is why the test exists):
- Tools you had (chat history, code, any session or chat creation); chats reached, oldest date, calls made, limits hit.
- Each question: searches run, chats read, grade after Part 1 and after Part 2, how sure, what would settle it.
- Part 2: what the second pass changed (count and list) and what stayed unknown.
- Part 3: could you open a reviewer; did the user paste the review; what it changed.
- Each set-up step: outcome, the user's exact words where it went wrong, any link that did not land where expected.
- Part 5: whether the pull request was opened, its link, or the error.
- What you assumed; questions that did not fit a chat-only user (better wording); questions missing or useless.
- A proposed config: language, roles, stores, the limits that matter, reviewer yes/no (from Part 3), what a board would show.
- One paragraph: can the kit's design (git as the one store, a board as its face, a lookup reader, hooks) serve this person, and what must change.

Begin with Part 0.

---

Not checked here: the chat-history and code tools' exact names and reach on the tester's account (they depend on his
plan and settings); whether chat can create a conversation (Part 3 tests it); the dev report says what was available.
