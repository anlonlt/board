# {{PROJECT}} board: say it once, here

The board: {{BOARD_URL}} (served locally by `python3 -I .kit/board_server.py`, source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **{{TALK}}**.

**Your post exists from turn 1.** Create it with `board_cli.py set` in column `info`, `status: "in progress"`, your full turn-1 report as its body; add each finding to it (newest first); at the end set its final column. The message to the orchestrator (`send_message`) only points: `Done. Check post <id>.`. Each chat turn ends with a single `·` (the hooks remind you).

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post (one command)
Run `python3 -I .kit/board_cli.py set posts <part>-<slug> --data '<json>'` with `{title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `python3 -I .kit/board_cli.py asset upload <file>`), `captions`.
- `column`: `todo` (the owner does something), `question` (the owner chooses), `info` (news, a finished report), `done`.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- `train`: the project your prompt names (its id in `.story/state/`), and `parent` when you continue another part:
  your post then shows in that project's train on "Where we are".
- `session_id`: your `agy` conversation ID (from your system prompt / `<user_information>`).
- A `todo` post says how long it takes him: `minutes`. Pictures: `captions` (one line per picture, in order);
  `image_groups: [{title, count}]` puts the pictures meant to be compared side by side; unrelated pictures:
  `layout_phone` and `layout_computer` `"rows"`.
- **Never `set` a post that exists:** the page writes its own fields on it (`column_before`, `done_at` when he moves
  it, `priority` when he taps 1-3); `set` would erase them. Change your post with `board_cli.py update` or
  `board_cli.py str-replace` + `--if-version <V>` (the version the last write returned). Add, do not resend:
  `board_cli.py str-replace posts <id> --field body --old "<first line>" --new "<new lines>\n<first line>" --if-version <V>`.

## Comments
- The owner's words reach you from the board clerk by `send_message`, naming the post and a thread id. A 👍 Like or a tapped answer goes to the orchestrator only.
- **Answer IN HIS THREAD:** put `reply_to: "<the thread id the clerk named>"` (the page accepts it as well as his comment's doc_id). **Never write `discussion`:** under `_orchestrator` the page files a reply in its parent's conversation by itself; a copied `discussion` files it in the wrong one. A comment without `reply_to` starts a new thread.
- Answer under the post: `python3 -I .kit/board_cli.py set comments <fresh_id> --data '{post_id, text, from: "<part>", created_at, reply_to}'`. To answer ONE comment add `reply_to: "<its doc_id or thread_id>"`.
- **Typing dots:** when you start writing an answer, `python3 -I .kit/board_cli.py set typing <post_id> --data '{"from":"<part>","at":"<now>"}'`; the page shows dots until your comment lands or 10 minutes pass.
- `created_at` is the real UTC time, run right before the write.
- A post's comments without the whole board: `python3 -I .kit/kit.py post <id>` on a fresh copy (`.kit/ORCHESTRATOR.md` § Reading), or `python3 -I .kit/board_cli.py query comments --where '[["post_id","==","<id>"]]' --out-dir /tmp/kd`.
- **His attachments:** his comment's text ends with `[attached: <name> (<type>) at /_blob/<id>]`; fetch one with
  `python3 -I .kit/board_cli.py asset read <id> --out /tmp/<name>`.
- **An answer under one of your pictures:** add `image: "/_blob/<id>"` to your comment.
- **A 👍 or a tapped answer** goes to the orchestrator only (the clerk sends it there), never to you: the orchestrator
  relays a go.
- Never edit `.kit/board.html` directly (translations go in `.kit/strings.json`).
