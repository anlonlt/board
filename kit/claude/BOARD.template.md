# {{PROJECT}} board: say it once, here

The board: {{BOARD_URL}} (source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **{{TALK}}**.

**Your post exists from turn 1.** `set` it in column `info`, `status: "in progress"`, your full turn-1 report as its body; add each finding to it (newest first); at the end set its final column. The message to the orchestrator only points: `Done. Check post <id>.`. A turn the board or another session opened ends with a single `·` (the hooks remind you); when the owner types in your chat, answer him briefly in words, never a lone `·`.

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post
**Through the kit (CL72: a hook refuses every board call the kit did not just print, for every session):** `python3 -I .kit/web_takeover.py` once, `python3 -I .kit/kit.py artifact sync` (it prints one set of `ArtifactData` calls, exactly: make them, run it again until "in sync"); then `python3 -I .kit/board_cli.py set posts <id> --data '{...}'` (or `update`), `board_cli.py reply <post> --text ...`; then `kit.py artifact sync` again (a stop is held while a change is unsent). Not through the kit (no kit command): typing dots, `kit_artifacts`, the clerk's read marks.
The post's fields (`doc_id: "<part>-<slug>"`), `data: {title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `Artifact` `publish` `asset: true` to the board's url), `captions`.
- **Files a post names, on the Artifact** (CL10, the owner 2026-11-23: "All the pictures don't show up here"): a repo path in `images`, `artifacts`, a code span or a link opens in the board's viewers only once the file is in the Artifact. `python3 -I .kit/kit.py artifact push` sends them (CL30: the files of EVERY post and comment of the copy, not only those sent now; a Markdown or HTML document goes in the push's own batch; a picture, PDF or video is uploaded by the `Artifact` `publish` calls it prints, then `files-sent`); `artifact load` puts the Artifact's pictures in the local copy (from this repo's files, or the `Artifact` `read` calls it prints). The record of what the Artifact holds is `.kit/artifact-files/` (commit it). By hand: `python3 -I .kit/kit_artifact_sync.py files <the post's JSON> --repo . --board <url> [--into <board id>]`. Make the `Artifact` `publish` `asset: true` calls it prints and save what they return in its `uploaded.json`; `ArtifactData` `get` the board's `kit_docs/_index` with an `out_dir`; then `files-sent` (same `--into`, `--index <that file> --index-version <its version>`, or `--index-missing`) and send its `ArtifactData` batch: it merges into the map, never replaces it. Pictures (png, jpg, gif, webp, svg), PDF and video (mp4, webm) are uploaded; Markdown and HTML are mirrored as text. Limits, measured 2026-11-23: an uploaded file up to 20 MiB (a 20.1 MiB one is refused before upload), SVG up to 2 MiB, 25 files a call; a mirrored document up to 2 MiB (cut to the db's 240 KiB a document); the store's total shows in `Artifact` `list` `scope: "assets"`. Every other file the post names (audio, .mov, .avif, .docx, .xlsx, .csv, code, or anything over its limit) stays a repo link: the command names each one with its reason, and the post says so.
- `column`: the script picks the lane from what the post is: `options` is a `question` (the owner chooses; `kit.py ask` writes it whole), `minutes` is a `todo` (the owner does something), else `info` (news, a report); `done` is his. A lane that does not exist, a question with no default and a question asked in a thread are refused.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- `train`: the project your prompt names (its id in `{{MEMORY_DIR}}/state/`), and `parent` when you continue another part:
  your post then shows in that project's train on "Where we are".
- `session_id`: yours (the `session_…` in your `Claude-Session` line, or `get_session`).
- A `todo` post says how long it takes him: `minutes`. Pictures: `captions` (one line per picture, in order);
  `image_groups: [{title, count}]` puts the pictures meant to be compared side by side; unrelated pictures:
  `layout_phone` and `layout_computer` `"rows"`.
- **Never `set` a post that exists:** the page writes its own fields on it (`column_before`, `done_at` when he moves
  it, `priority` when he taps 1-3); `set` would erase them. Change your post with `update` / `str_replace` + `if_version` (the version the last write returned). Add, do not resend: `str_replace` `field: "body"`, `old_str` its first line, `new_str` the new lines + that first line.

## The post format (a step's: `python3 -I .kit/kit.py form <post>`; checked by `kit.py done <post>`)
The owner and the train view read fields, not prose. A finished post carries:
| field | what | shown as |
|---|---|---|
| `verdict` | ONE plain sentence that answers the task (at most 240 characters) | the card's line, the train step's headline |
| `bars` | a list: each measured number with its denominator or reference (`"41 of 50 takes in key"`, `"latency 80 ms (bar 100 ms)"`), or `["none: <why>"]` | the train step's Bars |
| `artifacts` | a list of repo paths (committed) or `/_blob/<id>`: what you made | chips that open in the board's viewers |
| `not_checked` | what you did NOT check (`["nothing left unchecked"]` if so) | the step's Issues |
| `issues` | what is wrong or open | the step's Issues |
| `check` | the one command that reproduces your main number | the step's Check: the orchestrator and the review rerun it |
| `breaks` | how to make `check` fail: a planted fault or a bad input (`sed -i 's/a + b/a - b/' calc.py`), or `manual: <what to do>` for a browser or Microsoft 365 step; or `revert` (or `revert <base>`), the cheapest: the old version, before your change, is the made-to-fail one (no new art, about 0 tokens). | the step's Breaks: the review applies it in a throwaway copy and sees `check` fail; a check that cannot fail holds nothing |
| `asked` | his words that started the step, copied at turn 1 from the prompt: `[{"id": "<comment id>", "quote": "<his words>"}]`; name each in the bar that answers it: `... (asked <id>)` | `kit.py done` refuses an ask no bar or `not_checked` answers, and a quote that is not in his comment; the review judges against them |
| `fixes` | optional, for a step that fixes a bug: `<kind>: <what was broken>`, the kind a short theme (platform leak, silent error, two copies out of sync); a commit says it with a `Fix-kind: <kind>` line | `kit.py done` records it in the ledger, once; the third fix of one kind is raised in the orchestrator's resume as a design step, never a fourth patch |
| `shot` | for visual work: a command that renders the step's picture and writes a PNG to `$KIT_SHOT`; mark visual bars `[visual]` | `kit.py pair <post>`: the picture now and reverted, as A and B blind, for a reviewer who reads only his words and the two pictures (3 to 5k tokens); the review reruns one bar in five at random and every `[visual]` one |
| `handoff` | what the next step needs from yours | the step's Handoff, and the next part's context |
| `real_project` | `{project, command, output}`: the dry run on a project that uses the kit (a clone, never pushed), required when the change touches compact, install, resume, hooks or artifact | `kit.py done` refuses without it; the review reruns it |
| `body` | the whole result, after a first line saying what he must know or do; `##` sections, tables | the post |
**Say it once, on the board:** the post is the one place your result lives, complete; messages and chat only point to it. The fields come first so the answer is never buried; the body holds the rest, as long as it needs. Pictures and diagrams go in the body too, where he reads: a picture as `![what it shows](docs/<part>/pic.png)` (a committed path, or `/_blob/<id>`), a diagram as a ```` ```mermaid ```` or ```` ```svg ```` block, an `.svg` file by its path in a code span; the page draws them with the document viewer's renderer. A repo `docs/*.md` file is never used for your text: `kit.py done` refuses a body that is mostly `` `docs/X.md § Section` `` embeds of a file your step wrote (CL36). Update the post as you go (`update` / `str_replace`); a post is never rewritten in full.

**Sections fold.** In a post body or a comment, each `##` / `###` heading folds what follows it; the lead (the text above the first heading) always shows: put there what he must know or do. The board learns which section names he opens and which he folds, on every post and comment ; an audit-like section (What I checked, Not checked, Commits, Details, Log) folds unless he opens such sections; end a heading with `{fold}` to fold it yourself. So the detail stays on the board, complete, without crowding what he reads first.

## Comments
- The owner's words reach you from the board clerk by `send_message`, naming the post and a thread id. A 👍 Like or a tapped answer goes to the orchestrator only.
- **Answer IN HIS THREAD:** put `reply_to: "<the thread id the clerk named>"` (the page accepts it as well as his comment's doc_id). **Never write `discussion`:** under `_orchestrator` the page files a reply in its parent's conversation by itself; a copied `discussion` files it in the wrong one (his friend's board, 2026-11-20). A comment without `reply_to` starts a new thread.
- Answer under the post: `board_cli.py reply <post> --text ... --from <part>` on the local copy (or `board_cli.py set comments <fresh id>` with `{post_id, text, from: "<part>", created_at}`), then push. To answer ONE comment add `reply_to: "<its doc_id>"`; the clerk names a thread id, so first `query` `comments` `where [["thread_id","==","<thread id>"]]` for the doc_id.
- **Typing dots:** when you start writing an answer, `set` `typing/<post_id>` `{from: "<part>", at: <now>}`; the page shows dots until your comment lands or 10 minutes pass.
- `created_at` is the real UTC time, run right before the write.
- A post's comments without the whole board: `python3 -I .kit/kit.py post <id>` on a fresh copy (`.kit/ORCHESTRATOR.md` § Reading), or `query` `comments` `where [["post_id","==","<id>"]]` with `out_dir`.
- **His attachments:** his comment's text ends with `[attached: <name> (<type>) at /_blob/<id>]`; fetch one with
  `Artifact` `read`, the board's `url`, `path: "<id>"`.
- **An answer under one of your pictures:** add `image: "/_blob/<id>"` to your comment.
- **A 👍 or a tapped answer** goes to the orchestrator only (the clerk sends it there), never to you: the orchestrator
  relays a go.
- You never republish the page.
