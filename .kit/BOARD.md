# Board: say it once, here

The board: http://127.0.0.1:8765 (served locally by `python3 -I .kit/board_server.py`, source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **en**.

**Your post exists from turn 1.** When your prompt names your post, it exists already (the orchestrator planned it): `update` it with your `session_id` and `status: "in progress"`. Otherwise create it with `board_cli.py set` in column `info`, `status: "in progress"`. Add each finding to it (newest first); at the end fill it in the post format below. The message to the orchestrator (agy `send_message`; Claude Code: your last line, or `SendMessage`) only points: `Done. Check post <id>.`. Each chat turn ends with a single `·` (the hooks remind you).

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post (one command)
Run `python3 -I .kit/board_cli.py set posts <part>-<slug> --data '<json>'` with `{title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `python3 -I .kit/board_cli.py asset upload <file>`), `captions`.
- `column`: `todo` (the owner does something), `question` (the owner chooses), `info` (news, a finished report), `done`.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- **Repo files, images & HTML:** any committed repo `.md`, `.html`, or image (`.png`/`.jpg`/`.webp`/`.svg`) can be referenced directly by repo path (e.g. `` `docs/assets/shot.png` `` or GitHub blob link) and opens directly in the board's Document/Image/HTML Viewer — you only need `board_cli.py asset upload` (`/_blob/<id>`) for generated/temporary images not in the repo.
- `train`: the project your prompt names (its id in `.board/memory/state/`), and `parent` when you continue another part:
  your post then shows in that project's train on "Where we are".
- `session_id`: agy: your conversation ID (from your system prompt / `<user_information>`); Claude Code: the agent `name` the orchestrator gave you (else the session id the `SessionStart` hook printed). The clerk routes the owner's comments on your post there.
- A `todo` post says how long it takes him: `minutes`. Pictures: `captions` (one line per picture, in order);
  `image_groups: [{title, count}]` puts the pictures meant to be compared side by side; unrelated pictures:
  `layout_phone` and `layout_computer` `"rows"`.
- **Never `set` a post that exists:** the page writes its own fields on it (`column_before`, `done_at` when he moves
  it, `priority` when he taps 1-3); `set` would erase them. Change your post with `board_cli.py update` or
  `board_cli.py str-replace` + `--if-version <V>` (the version the last write returned). Add, do not resend:
  `board_cli.py str-replace posts <id> --field body --old "<first line>" --new "<new lines>\n<first line>" --if-version <V>`.

## The post format (checked by `python3 -I .kit/kit.py done <post>` before Done)
The owner and the train view read fields, not prose. A finished post carries:
| field | what | shown as |
|---|---|---|
| `verdict` | ONE plain sentence that answers the task (at most 240 characters) | the card's line, the train step's headline |
| `bars` | a list: each measured number with its denominator or reference (`"41 of 50 takes in key"`, `"latency 80 ms (bar 100 ms)"`), or `["none: <why>"]` | the train step's Bars |
| `artifacts` | a list of repo paths (committed) or `/_blob/<id>`: what you made | chips that open in the board's viewers |
| `not_checked` | what you did NOT check (`["nothing left unchecked"]` if so) | the step's Issues |
| `issues` | what is wrong or open | the step's Issues |
| `check` | the one command that reproduces your main number | the step's Check: the orchestrator and the review rerun it |
| `handoff` | what the next step needs from yours | the step's Handoff, and the next part's context |
| `body` | the whole result, after a first line saying what he must know or do; `##` sections, tables | the post |
**Say it once, on the board:** the post is the one place your result lives, complete; messages and chat only point to it. The fields come first so the answer is never buried; the body holds the rest, as long as it needs. A repo document (`docs/<part>.md`) only for what is a document to keep or needs what a post body cannot show (pictures inline, diagrams): commit it and embed its sections as `` `docs/<part>.md § Section` `` (the page shows them inline and he comments on them in place); then the body points to it and never restates it. Update the post as you go (`board_cli.py update` / `str-replace`); a post is never rewritten in full.

## Which feature for what
Pick the board feature for each kind of content; the page does the rest.
| you have | do this | he gets |
|---|---|---|
| a number | a `bars` item with its denominator | the train's Bars, comparable across steps |
| a file you made (`.md`, `.html`, `.pdf`, image) | its repo path in `artifacts` and in a code span in the body: `` `out/report.html` `` | an inline viewer under the paragraph (HTML: phone/desktop toggle; PDF: page comments) and a chip in the train |
| a report to keep, with pictures or diagrams | `docs/<part>.md`, embedded as `` `docs/<part>.md § Section` `` | that section inline; his comment comes back quoting the section |
| audio or video takes (`.mp3`, `.wav`, `.m4a`, `.flac`, `.mp4`, ...) | repo paths in code spans, or `[take-1.mp3](/_blob/<id>)` (the link text must end in the extension) | an inline player; his "Comment @ mm:ss" comes back with the time |
| pictures to compare | `images` in order + `image_groups: [{title, count}]` + `captions` | rows side by side with synced zoom; he can select any two for a compare grid |
| unrelated pictures | `images` + `layout_phone` / `layout_computer`: `"rows"`, `"strip"` or `"gallery:N"` | the layout you chose |
| a choice for him | `column: "question"`, `options` + `default_option` | one-tap buttons; the default applies if he taps nothing in Focus |
| something he must do | `column: "todo"`, `minutes` | an Action card with its time |
| a code change | the commit sha in the body | a foldable diff under the body |
| what he should read first | a paragraph starting "For you:" | highlighted in Feed and Focus |
| your audit trail | paragraphs starting "Not checked:", "What I checked:", "Commits:" at the end | folded into "Checks (n)" |
Post bodies take `**bold**`, `` `code` ``, `#` headings, `| tables |`, `>` quotes, lists, `- [ ]` task lists and links; no images inline (use `images`), no italics, no diagrams (those work in `docs/` files, opened in the viewer).

**Sections fold.** In a post body or a comment, each `##` / `###` heading folds what follows it; the lead (the text above the first heading) always shows: put there what he must know or do. The board learns which section names he opens and which he folds, on every post and comment (`python3 -I .kit/kit.py sections`); an audit-like section (What I checked, Not checked, Commits, Details, Log) folds unless he opens such sections; end a heading with `{fold}` to fold it yourself. So the detail stays on the board, complete, without crowding what he reads first.

## Comments
- The owner's words reach you from the board clerk (agy `send_message`; Claude Code: the orchestrator's `SendMessage`), naming the post and a thread id. A 👍 Like or a tapped answer goes to the orchestrator only.
- **Answer with ONE command, the one the clerk's message gives:** `python3 -I .kit/board_cli.py reply <post> --comment-id <his comment's id> --from <part> --text "<answer>"`. It lands in his thread, marks his comment read and acknowledges the thread: never work out `reply_to` or `thread_id` yourself, and **never write `discussion`** (the page files a reply in its conversation by itself).
- A new remark of yours (not an answer): `python3 -I .kit/board_cli.py set comments <fresh_id> --data '{post_id, text, from: "<part>", created_at}'`; it starts a new thread.
- **Typing dots:** when you start writing an answer, `python3 -I .kit/board_cli.py set typing <post_id> --data '{"from":"<part>","at":"<now>"}'`; the page shows dots until your comment lands or 10 minutes pass.
- `created_at` is the real UTC time, run right before the write.
- A post's comments without the whole board: `python3 -I .kit/kit.py post <id>` on a fresh copy (`.kit/ORCHESTRATOR.md` § Reading), or `python3 -I .kit/board_cli.py query comments --where '[["post_id","==","<id>"]]' --out-dir /tmp/kd`.
- **His attachments:** his comment's text ends with `[attached: <name> (<type>) at /_blob/<id>]`; fetch one with
  `python3 -I .kit/board_cli.py asset read <id> --out /tmp/<name>`.
- **An answer under one of your pictures:** add `image: "/_blob/<id>"` to your comment.
- **A 👍 or a tapped answer** goes to the orchestrator only (the clerk sends it there), never to you: the orchestrator
  relays a go.
- Never edit `.kit/board.html` directly (translations go in `.kit/strings.json`).
