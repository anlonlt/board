# Export your chats' state for the kit (paste into a NEW claude.ai chat)

Paste everything between the two lines as your first message. Claude reads your past chats itself and asks you
nothing until the end. It needs chat search on (Settings → Capabilities). Nothing is sent anywhere: the result is a
private claude.ai artifact that your Claude Code session reads.

---
You are preparing my work's state for the board kit (https://github.com/anlonlt/board). Do it alone, then show me.

1. With your chat-history tools (recent chats, chat search), go through my conversations of the last 3 months.
   Page back until nothing more comes; note in `unread` the periods you could not reach. Group them into projects:
   ongoing pieces of work I come back to. **Count as projects:** a recurring document (the same task in several
   chats, e.g. a weekly bulletin), and work done THROUGH tools (a connector, the browser extension) even when the
   chat itself is short. Leave out one-off questions and anything personal unless I come back to it as a project.
2. For each project, from the chats themselves (no names of people, no personal data):
   - `id` (short, lowercase), `title`;
   - `next`: the one next step, as the latest chat left it;
   - `where`: 2-6 facts on where it stands (what exists, where it lives, which tool reaches it);
   - `decisions`: what I decided and should not be asked again;
   - `ids`: an OBJECT, name -> what it is: `{"List Y": "SharePoint list of requests", "Flow Z": "Power Automate flow"}`
     (no passwords, no keys);
   - `waiting`: what waits for MY approval or choice: `text`; `options` (2-4 short answers) when it is a choice;
     `default`: what happens if I say nothing, and **when there are options, `default` is one of them, word for word**;
   - `chats`: a LIST OF STRINGS, `"<YYYY-MM-DD> <title>"`.
3. Across projects:
   - `preferences`: the instructions I repeat (how short, which language, what to ask before doing);
   - `approved`: what I have already approved once and do not want to be asked again (e.g. "editing the test list");
   - `never`: what Claude must never touch or do without me (production data, personal data, sending real emails…);
   - `owner_language`: the language I write to you in; `unread`: what you could not read and why.
4. Publish ONE private artifact, titled "kit state", whose whole content is a `<pre id="kit-state">` holding the JSON,
   exactly this shape (the values are an example):
```
{"owner_language": "French", "unread": ["chats before 2026-10-14: not reachable"],
 "preferences": ["short steps", "answer in French"], "approved": ["editing the test list"],
 "never": ["send real emails without asking", "touch personal data"],
 "projects": [{"id": "bulletin", "title": "Weekly bulletin", "next": "draft the 24 Nov edition",
   "where": ["published every Friday on the intranet"], "decisions": ["no emojis"],
   "ids": {"News/Bulletin": "intranet page"},
   "waiting": [{"text": "Use the new layout?", "options": ["New layout", "Old layout"], "default": "Old layout"}],
   "chats": ["2026-11-14 Bulletin 46"]}]}
```
5. Show me the projects in a short table (title, next step, what waits for me) and the artifact's link. Ask me to
   correct anything wrong; republish the artifact with my corrections.
6. Then tell me, in one line: in Claude Code, on my private repository with the kit installed, paste
   "Import my chats' state: read the artifact <link> and follow the kit's README § Import."
---
