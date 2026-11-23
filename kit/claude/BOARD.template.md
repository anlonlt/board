# {{PROJECT}} board: say it once, here

The board: {{BOARD_URL}} (source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **{{TALK}}**.

**Your post exists from turn 1.** `set` it in column `info`, `status: "in progress"`, your full turn-1 report as its body; add each finding to it (newest first); at the end set its final column. The message to the orchestrator only points: `Done. Check post <id>.`. A turn the board or another session opened ends with a single `·` (the hooks remind you); when the owner types in your chat, answer him briefly in words, never a lone `·`.

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post (one call)
`ToolSearch` `select:ArtifactData` once, then `ArtifactData` `set`, `url` the board, `collection: "posts"`, `doc_id: "<part>-<slug>"`, `data: {title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `Artifact` `publish` `asset: true` to the board's url), `captions`.
- `column`: `todo` (the owner does something), `question` (the owner chooses), `info` (news, a finished report), `done`.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- `train`: the project your prompt names (its id in `{{MEMORY_DIR}}/state/`), and `parent` when you continue another part:
  your post then shows in that project's train on "Where we are".
- `session_id`: yours (the `session_…` in your `Claude-Session` line, or `get_session`).
- A `todo` post says how long it takes him: `minutes`. Pictures: `captions` (one line per picture, in order);
  `image_groups: [{title, count}]` puts the pictures meant to be compared side by side; unrelated pictures:
  `layout_phone` and `layout_computer` `"rows"`.
- **Never `set` a post that exists:** the page writes its own fields on it (`column_before`, `done_at` when he moves
  it, `priority` when he taps 1-3); `set` would erase them. Change your post with `update` / `str_replace` + `if_version` (the version the last write returned). Add, do not resend: `str_replace` `field: "body"`, `old_str` its first line, `new_str` the new lines + that first line.

## The post format (checked by `the format below` before Done)
The owner and the train view read fields, not prose. A finished post carries:
| field | what | shown as |
|---|---|---|
| `verdict` | ONE plain sentence that answers the task (at most 240 characters) | the card's line, the train step's headline |
| `bars` | a list: each measured number with its denominator or reference (`"41 of 50 takes in key"`, `"latency 80 ms (bar 100 ms)"`), or `["none: <why>"]` | the train step's Bars |
| `artifacts` | a list of repo paths (committed) or `/_blob/<id>`: what you made | chips that open in the board's viewers |
| `not_checked` | what you did NOT check (`["nothing left unchecked"]` if so) | the step's Issues |
| `issues` | what is wrong or open | the step's Issues |
| `check` | the one command that reproduces your main number | the step's Check: the orchestrator and the review rerun it |
| `breaks` | how to make `check` fail: a planted fault or a bad input (`sed -i 's/a + b/a - b/' calc.py`), or `manual: <what to do>` for a browser or Microsoft 365 step | the step's Breaks: the review applies it in a throwaway copy and sees `check` fail; a check that cannot fail holds nothing |
| `handoff` | what the next step needs from yours | the step's Handoff, and the next part's context |
| `body` | the whole result, after a first line saying what he must know or do; `##` sections, tables | the post |
**Say it once, on the board:** the post is the one place your result lives, complete; messages and chat only point to it. The fields come first so the answer is never buried; the body holds the rest, as long as it needs. A repo document (`docs/<part>.md`) only for what is a document to keep or needs what a post body cannot show (pictures inline, diagrams): commit it and embed its sections as `` `docs/<part>.md § Section` `` (the page shows them inline and he comments on them in place); then the body points to it and never restates it. Update the post as you go (`update` / `str_replace`); a post is never rewritten in full.

**Sections fold.** In a post body or a comment, each `##` / `###` heading folds what follows it; the lead (the text above the first heading) always shows: put there what he must know or do. The board learns which section names he opens and which he folds, on every post and comment ; an audit-like section (What I checked, Not checked, Commits, Details, Log) folds unless he opens such sections; end a heading with `{fold}` to fold it yourself. So the detail stays on the board, complete, without crowding what he reads first.

## Comments
- The owner's words reach you from the board clerk by `send_message`, naming the post and a thread id. A 👍 Like or a tapped answer goes to the orchestrator only.
- **Answer IN HIS THREAD:** put `reply_to: "<the thread id the clerk named>"` (the page accepts it as well as his comment's doc_id). **Never write `discussion`:** under `_orchestrator` the page files a reply in its parent's conversation by itself; a copied `discussion` files it in the wrong one (his friend's board, 2026-11-20). A comment without `reply_to` starts a new thread.
- Answer under the post: `ArtifactData` `set` in `comments`, a fresh `doc_id`, `{post_id, text, from: "<part>", created_at}`. To answer ONE comment add `reply_to: "<its doc_id>"`; the clerk names a thread id, so first `query` `comments` `where [["thread_id","==","<thread id>"]]` for the doc_id.
- **Typing dots:** when you start writing an answer, `set` `typing/<post_id>` `{from: "<part>", at: <now>}`; the page shows dots until your comment lands or 10 minutes pass.
- `created_at` is the real UTC time, run right before the write.
- A post's comments without the whole board: `python3 -I .kit/kit.py post <id>` on a fresh copy (`.kit/ORCHESTRATOR.md` § Reading), or `query` `comments` `where [["post_id","==","<id>"]]` with `out_dir`.
- **His attachments:** his comment's text ends with `[attached: <name> (<type>) at /_blob/<id>]`; fetch one with
  `Artifact` `read`, the board's `url`, `path: "<id>"`.
- **An answer under one of your pictures:** add `image: "/_blob/<id>"` to your comment.
- **A 👍 or a tapped answer** goes to the orchestrator only (the clerk sends it there), never to you: the orchestrator
  relays a go.
- You never republish the page.
