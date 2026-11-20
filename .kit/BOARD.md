# Tide Clock board: say it once, here

The board: https://claude.ai/artifact/Etco5hYbtiiSHtV53saYB7 (source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **English**.

**Your post exists from turn 1.** `set` it in column `info`, `status: "in progress"`, your full turn-1 report as its body; add each finding to it (newest first); at the end set its final column. The message to the orchestrator only points: `Done. Check post <id>.`. Each chat turn ends with a single `·` (the hooks remind you).

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post (one call)
`ToolSearch` `select:ArtifactData` once, then `ArtifactData` `set`, `url` the board, `collection: "posts"`, `doc_id: "<part>-<slug>"`, `data: {title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `Artifact` `publish` `asset: true` to the board's url), `captions`.
- `column`: `todo` (the owner does something), `question` (the owner chooses), `info` (news, a finished report), `done`.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- `session_id`: yours (the `session_…` in your `Claude-Session` line, or `get_session`).
- Change your post with `update` / `str_replace` + `if_version` (the version the last write returned). Add, do not resend: `str_replace` `field: "body"`, `old_str` its first line, `new_str` the new lines + that first line.

## Comments
- The owner's words reach you from the board clerk by `send_message`, naming the post and a thread id. A 👍 Like or a tapped answer goes to the orchestrator only.
- Answer under the post: `ArtifactData` `set` in `comments`, a fresh `doc_id`, `{post_id, text, from: "<part>", created_at}`. To answer ONE comment add `reply_to: "<its doc_id>"`; the clerk names a thread id, so first `query` `comments` `where [["thread_id","==","<thread id>"]]` for the doc_id.
- **Typing dots:** when you start writing an answer, `set` `typing/<post_id>` `{from: "<part>", at: <now>}`; the page shows dots until your comment lands or 10 minutes pass.
- `created_at` is the real UTC time, run right before the write.
- A post's comments without the whole board: `python3 -I .kit/kit.py post <id>` on a fresh copy (`.kit/ORCHESTRATOR.md` § Reading), or `query` `comments` `where [["post_id","==","<id>"]]` with `out_dir`.
- You never republish the page.
