# Export your chats' state for the kit (paste into a NEW claude.ai chat)

Paste everything between the two lines as your first message. (Reading this file from GitHub: open it by its latest
commit, https://github.com/anlonlt/board/commits/main/step0/export-chat.md, not a cached raw link.) Claude reads your past chats itself and asks you
nothing until the end. It needs chat search on (Settings → Memory → "Search and reference chats"). Nothing is sent anywhere: the result is a
private claude.ai artifact that your Claude Code session reads into your PRIVATE repository. **The test of a good
export: a new session could resume each project from it without you re-explaining anything.**

---
You are preparing my work's state for the board kit (https://github.com/anlonlt/board). Do it alone, then show me.
If you read any kit file from GitHub, read it by the latest commit's SHA (`raw.githubusercontent.com/anlonlt/board/<sha>/…`,
the SHA from https://api.github.com/repos/anlonlt/board/commits/main): the `main` raw link can serve an older copy for minutes.

1. With your chat-history tools (recent chats, chat search), go through my conversations of the last 3 months.
   Page back until nothing more comes; note in `unread` the periods you could not reach. Group them into projects:
   ongoing pieces of work I come back to. **Count as projects:** a recurring document (the same task in several
   chats, e.g. a weekly bulletin), and work done THROUGH tools (a connector, the browser extension) even when the
   chat itself is short. Leave out one-off questions and anything personal unless I come back to it as a project.
2. For each project, from the chats themselves. The artifact stays private to my account and goes only into my
   private repository, so **write everything a new session needs to resume the work**: addresses, internal names and
   who holds which role are allowed; passwords, keys and tokens are never written.
   - `id` (short, lowercase), `title`;
   - `next`: the one next step, as the latest chat left it;
   - `priority`: 1 (urgent: a deadline within a week, something broken in use, people waiting on it), 2 (active: worked on
     in the last 2 weeks), 3 (paused or someday); with `why` in a few words (the deadline, who waits, the last chat's date);
   - `resume`: how a new session resumes it, in order (what to open first, what to check, then `next`);
   - `where`: 2-6 facts on where it stands (what exists, where it lives, which tool reaches it);
   - `decisions`: what I decided and should not be asked again;
   - `ids`: an OBJECT, name -> what it is AND where: `{"List Y": "SharePoint list of requests, https://…/Lists/Y"}`:
     environment, apps, flows, lists, sites, with their addresses or ids;
   - `technical`: the details that took work to find: internal column names, formulas and expressions in place,
     the rules of roles in the apps;
   - `accounts`: who runs what (a service account or mine) and which browser profile to use;
   - `people`: role -> who holds it (approvers, technicians, accounting), as the chats name them;
   - `tests`: what was verified, and what was never verified;
   - `risks`: known risks and fragile parts;
   - `lessons` (or `pitfalls`): the traps found (the editors' quirks, what fails and what works instead);
   - `waiting`: what waits for MY approval or choice: `text`; `priority` 1-3 as above; `options` (2-4 short answers) when it is a choice;
     `default`: what happens if I say nothing, and **when there are options, `default` is one of them, word for word**;
   - `needs_me`: what a session will still need me for (a login, an approval, a file only I have);
   - `chats`: a LIST OF STRINGS, `"<YYYY-MM-DD> <title>"`.
3. Across projects:
   - `preferences`: the instructions I repeat (how short, which language, what to ask before doing);
   - `approved`: what I have already approved once and do not want to be asked again (e.g. "editing the test list");
   - `never`: what Claude must never touch or do without me (production data, personal data, sending real emails…);
   - `owner_language`: the language I write to you in; `unread`: what you could not read and why;
   - `workflow`: HOW the work gets done, which no project says alone: `tools` (each tool or method used and for
     what: the browser extension, a connector, scripts run from a page, an editor's code view, artifacts as hand-off),
     `accounts` (each account or browser profile, what it is used for, how to tell which one is open), `cycle` (the
     steps a change goes through, e.g. build, check, save, publish only with my go), `limits` (what slows or stops the
     work: one long action per answer, waits, sessions that expire, never a password).
   - `workflow.runners`: every PLACE the work happens, each `{name, kind, reaches, tools, accounts, setup}`, `kind`
     one of `cloud` (a Claude Code session in the cloud), `computer` (my own computer: its files, apps, Claude in
     Chrome in my browser…), `chat` (a claude.ai chat with its connectors), `owner` (only I can: phone, signature,
     login), `scheduled`; `setup`: what is already set up and what is not (README § Runners of the kit lists them).
   And per project, `runs_in`: the `name` of the runner its work needs (`cloud` when it is code or files a cloud session
   reaches), with `runs_in_why`.
4. Publish ONE private artifact, titled "kit state", whose whole content is a `<pre id="kit-state">` holding the JSON,
   exactly this shape (the values are an example):
```
{"owner_language": "French", "unread": ["chats before 2026-10-14: not reachable"],
 "preferences": ["short steps", "answer in French"], "approved": ["editing the test list"],
 "never": ["send real emails without asking", "touch personal data"],
 "workflow": {"tools": ["Claude in Chrome in my browser: all intranet editing"], "accounts": ["profile 'work': the intranet editor"],
   "cycle": ["build", "check", "save", "publish only with my go"], "limits": ["one long action per answer"],
   "runners": [{"name": "laptop", "kind": "computer", "reaches": "the intranet editor through Claude in Chrome",
                "accounts": "profile 'work'", "setup": "Claude in Chrome on; no Claude desktop app yet"}]},
 "projects": [{"id": "bulletin", "title": "Weekly bulletin", "next": "draft the 24 Nov edition",
   "priority": 1, "why": "edition due Friday 24 Nov", "runs_in": "laptop", "runs_in_why": "edited in the intranet through Chrome",
   "resume": ["open the intranet page News/Bulletin", "read last week's edition", "then: next"],
   "where": ["published every Friday on the intranet"], "decisions": ["no emojis"],
   "ids": {"News/Bulletin": "intranet page, https://example.sharepoint.com/sites/news/bulletin"},
   "technical": ["column 'Statut' is internally 'field_3'"], "accounts": ["the flow runs as the service account"],
   "people": {"approver": "the principal"}, "tests": ["verified: publishing on Friday", "never verified: the archive"],
   "risks": ["the archive flow breaks if the title has a slash"], "lessons": ["the editor loses formatting on paste: paste as text"],
   "needs_me": ["my login to the intranet editor"],
   "waiting": [{"text": "Use the new layout?", "options": ["New layout", "Old layout"], "default": "Old layout"}],
   "chats": ["2026-11-14 Bulletin 46"]}]}
```
5. Check the artifact against the test above: for each project, list what a new session could NOT resume from it.
   **Do not ask me those in chat.** Put each gap into that project's `waiting` (with `options` and a `default` the
   session can apply or check by itself, e.g. "Look it up in the list's settings"), so it reaches my board as a one-tap
   question. Ask me in chat ONLY what no session could find or decide without me (a login, a choice only I can make).
   Then show me the projects in a short table, most urgent first (priority and why, title, next step, what waits for
   me, what will still need me) and the artifact's link; republish it with any correction I give.
6. Then tell me, in one line, what to do in Claude Code. If I have a private repository with the kit installed:
   "Import my chats' state: read the artifact <link> and follow the kit's README § Import." If not (nothing in my
   chats shows one): create a private repository at https://github.com/new (Visibility: Private), open
   https://claude.ai/code on it, and paste: "Install the board kit (https://github.com/anlonlt/board README § Install),
   then import my chats' state from the artifact <link> (README § Import)."
---
