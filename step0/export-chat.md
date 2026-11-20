# Export your chats' state for the kit (paste into a NEW claude.ai chat)

Paste everything between the two lines as your first message. Claude reads your past chats itself and asks you
nothing until the end. It needs chat search on (Settings → Capabilities). Nothing is sent anywhere: the result is a
private claude.ai artifact that your Claude Code session reads.

---
You are preparing my work's state for the board kit (https://github.com/anlonlt/board). Do it alone, then show me.

1. With your chat-history tools (recent chats, chat search), go through my conversations of the last 3 months.
   Group them into projects: ongoing pieces of work that I come back to (a recurring document, an automation, a
   plan). Leave out one-off questions and anything personal unless I come back to it as a project.
2. For each project, from the chats themselves (quote nothing private: no names of people, no personal data):
   - `id` (short, lowercase), `title`;
   - `next`: the one next step, as the latest chat left it;
   - `where`: 2-6 facts on where it stands (what exists, where it lives);
   - `decisions`: what I decided and should not be asked again;
   - `ids`: names of pages, lists, files, flows that the work refers to (no passwords, no keys);
   - `waiting`: what waits for MY approval or choice, each with `default` (what happens if I say nothing) and
     `options` when it is a choice between a few answers;
   - `chats`: the dates and titles of the chats it comes from.
3. `preferences`: the instructions I repeat across chats (how short, which language, what to ask before doing).
   `owner_language`: the language I write to you in.
4. Publish ONE private artifact, titled "kit state", whose whole content is a `<pre id="kit-state">` holding that JSON:
   `{"owner_language": ..., "preferences": [...], "projects": [{"id", "title", "next", "where", "decisions", "ids",
   "waiting": [{"text", "default", "options"}], "chats"}]}`.
5. Show me the projects in a short table (title, next step, what waits for me) and the artifact's link. Ask me to
   correct anything wrong; republish the artifact with my corrections.
6. Then tell me, in one line: in Claude Code, on my private repository with the kit installed, paste
   "Import my chats' state: read the artifact <link> and follow the kit's README § Import."
---
