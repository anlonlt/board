# {{PROJECT}} board: say it once, here

The board: {{BOARD_URL}} (served locally by `python3 -I .kit/board_server.py`, source `.kit/board.html`). A session's result lives in ONE place, its board post. The owner reads and answers there. Talk to the owner in **{{TALK}}**.

**Your post exists from turn 1.** When your prompt names your post, it exists already (the orchestrator planned it): `update` it with your `session_id` and `status: "in progress"`. Otherwise create it with `board_cli.py set` in column `info`, `status: "in progress"`. Add each finding to it (newest first); at the end fill it in the post format below. The message to the orchestrator (agy `send_message`; Claude Code: your last line, or `SendMessage`) only points: `Done. Check post <id>.`. A turn the board or another session opened ends with a single `·` (the hooks remind you); when the owner types in your chat, answer him briefly in words, never a lone `·`.

**Do it, don't ask:** what is in your reach and in the owner's direction, do and report. Ask only for a choice, money, a licence, a change of direction, deleting.

## Post (one command)
Run `python3 -I .kit/board_cli.py set posts <part>-<slug> --data '<json>'` with `{title, body, column, part, session_id, created_at, priority}`; optional `status`, `options` + `default_option` (one-tap answers), `images` (`/_blob/<id>` from `python3 -I .kit/board_cli.py asset upload <file>`), `captions`.
- `column`: the script picks the lane from what the post is: `options` is a `question` (the owner chooses; `kit.py ask` writes it whole), `minutes` is a `todo` (the owner does something), else `info` (news, a report); `done` is his. A lane that does not exist, a question with no default and a question asked in a thread are refused.
- `body`: plain text with `**bold**`, `` `code` ``, `#` headings, `| tables |`, links. Lead with what was found or what the owner must do; name what was NOT checked. `priority` 1-3. `created_at`: `date -u +%Y-%m-%dT%H:%M:%SZ`, run right before (posts without it do not show).
- **Repo files, images & HTML:** any committed repo `.md`, `.html`, or image (`.png`/`.jpg`/`.webp`/`.svg`) can be referenced directly by repo path (e.g. `` `docs/assets/shot.png` `` or GitHub blob link) and opens directly in the board's Document/Image/HTML Viewer — you only need `board_cli.py asset upload` (`/_blob/<id>`) for generated/temporary images not in the repo. On a claude.ai Artifact board, those files must also be put in the Artifact: `kit.py artifact push` lists them. Limits: pictures, PDF and video (mp4, webm) up to 20 MiB a file (SVG 2 MiB), 25 a call; Markdown and HTML mirrored up to 2 MiB; audio, .mov, .avif, Office files, code and anything larger stay repo links, each named by the command for the post.
- `train`: the project your prompt names (its id in `{{MEMORY_DIR}}/state/`), and `parent` when you continue another part:
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
| `breaks` | how to make `check` fail: a command that plants a fault or feeds a bad input (`sed -i 's/a + b/a - b/' calc.py`, `rm data/sample.csv`), or `manual: <what to do>` when the check runs in a browser or Microsoft 365; or `revert` (or `revert <base>`), the cheapest: the old version, before your change, is the made-to-fail one (no new art, about 0 tokens). | the step's Breaks: `kit.py done` runs `check` (must pass), applies `breaks` in a throwaway copy of the work (a git worktree with your uncommitted edits, or a temp dir) and reruns `check` (must fail): a check that cannot fail is refused. A `manual:` one, the review performs |
| `asked` | his words that started the step, copied at turn 1 from the prompt: `[{"id": "<comment id>", "quote": "<his words>"}]`; name each in the bar that answers it: `... (asked <id>)` | `kit.py done` refuses an ask no bar or `not_checked` answers, and a quote that is not in his comment; the review judges against them |
| `shot` | for visual work: a command that renders the step's picture and writes a PNG to `$KIT_SHOT`; mark visual bars `[visual]` | `kit.py pair <post>`: the picture now and reverted, as A and B blind, for a reviewer who reads only his words and the two pictures (3 to 5k tokens); the review reruns one bar in five at random and every `[visual]` one |
| `handoff` | what the next step needs from yours | the step's Handoff, and the next part's context |
| `real_project` | `{project, command, output}`: the dry run on a project that uses the kit (a clone, never pushed), required when the change touches compact, install, resume, hooks or artifact | `kit.py done` refuses without it; the review reruns it |
| `body` | the whole result, after a first line saying what he must know or do; `##` sections, tables | the post |
**Say it once, on the board:** the post is the one place your result lives, complete; messages and chat only point to it. The fields come first so the answer is never buried; the body holds the rest, as long as it needs. Pictures and diagrams go in the body too, where he reads: a picture as `![what it shows](docs/<part>/pic.png)` (a committed path, or `/_blob/<id>`), a diagram as a ```` ```mermaid ```` or ```` ```svg ```` block, an `.svg` file by its path in a code span; the page draws them with the document viewer's renderer. A repo `docs/*.md` file is never used for your text: `kit.py done` refuses a body that is mostly `` `docs/X.md § Section` `` embeds of a file your step wrote (CL36). Update the post as you go (`board_cli.py update` / `str-replace`); a post is never rewritten in full.

## Which feature for what
Pick the board feature for each kind of content; the page does the rest.
| you have | do this | he gets |
|---|---|---|
| a number | a `bars` item with its denominator | the train's Bars, comparable across steps |
| a file you made (`.md`, `.html`, `.pdf`, image) | its repo path in `artifacts` and in a code span in the body: `` `out/report.html` `` | an inline viewer under the paragraph (HTML: phone/desktop toggle; PDF: page comments) and a chip in the train |
| a picture or a diagram | in the body: `![what it shows](docs/<part>/pic.png)`, a ```` ```mermaid ```` or ```` ```svg ```` block, or the `.svg` path in a code span | drawn in the post where he reads it, by the document viewer's renderer (local page and Artifact) |
| audio or video takes (`.mp3`, `.wav`, `.m4a`, `.flac`, `.mp4`, ...) | repo paths in code spans, or `[take-1.mp3](/_blob/<id>)` (the link text must end in the extension) | an inline player; his "Comment @ mm:ss" comes back with the time |
| pictures to compare | `images` in order + `image_groups: [{title, count}]` + `captions` | rows side by side with synced zoom; he can select any two for a compare grid |
| unrelated pictures | `images` + `layout_phone` / `layout_computer`: `"rows"`, `"strip"` or `"gallery:N"` | the layout you chose |
| a choice for him | `column: "question"`, `options` + `default_option` | one-tap buttons; the default applies if he taps nothing in Focus |
| something he must do | `column: "todo"`, `minutes` | an Action card with its time |
| a code change | the commit sha in the body | a foldable diff under the body |
| what he should read first | a paragraph starting "For you:" | highlighted in Feed and Focus |
| your audit trail | paragraphs starting "Not checked:", "What I checked:", "Commits:" at the end | folded into "Checks (n)" |
Post bodies take `**bold**`, `` `code` ``, `#` headings, `| tables |`, `>` quotes, lists, `- [ ]` task lists, links, pictures inline (`![alt](repo/path.png)`) and diagrams (```` ```mermaid ```` and ```` ```svg ```` blocks), drawn as the document viewer draws them; no italics.

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

## Attaching another repo on Claude Code on the web
A web session sees only the repos it started with. To work on another one (an attached board, the kit repo, a real project to test on):
1. `python3 -I .kit/kit.py attach <owner/repo>`: with no clone yet it prints the one call to make, `mcp__claude-code-remote__add_repo` `{"owner", "repo", "access": "read"}` (`"push"` only to push to it).
2. Make that call, then run `kit.py attach <owner/repo>` again: it clones the repo next to this project (`../<repo>`), records it in `.kit/config.json` `portfolio` (commit that), and prints the `register_repo_root` call that loads its CLAUDE.md and skills.
- The kit allows `add_repo` in `.claude/settings.json` `permissions.allow` (install and `install.py --update` add it; `kit.py doctor` warns when it is missing). Only a session started on ONE repo reads that file: start the session on the repo whose hooks and settings must run. A session already running never rereads it: a settings change counts from the next session.
- A session the kit launches (a part, a review, the clerk) never attaches a repo: its classifier refuses `add_repo` ("Unauthorized Access") whatever the allow rule says, unless the owner types it, and the owner does not type in chat. The orchestrator's session holds the repos: it runs a part's real-project check (by subagent) and writes it in the part's `real_project` (`kit.py done` says it is pending until then). A file of another project a part needs is in the Artifact's `kit_docs` (`ArtifactData get`, doc_id = its path with `/` written `+2F~`); else ask under your post. `kit.py attach` is for a session where the owner is typing anyway.

## Several repos on the web: one environment with every repo (CL39)
A project that manages several repos from web sessions (install asked once, or `--web-multi yes [--web-repos owner/a,owner/b]`) never needs `add_repo` or a line typed in chat:
- Install puts `.kit/user_hooks.py` in the project and records in `.kit/config.json` `kit.web_env` the environment to create: its repos and its setup script line, `python3 -I <repo>/.kit/user_hooks.py install`. That script copies every hook of the project's `.claude/settings.json` into `~/.claude/settings.json`, each wrapped to run only where the session's project has no settings of its own (the parent folder of a two-repo session), with `CLAUDE_PROJECT_DIR` set to the repo; inside the repo the project's own copy runs and the wrapped one is skipped. A rerun changes nothing.
- The orchestrator proposes the environment once, on the owner's tap, never in chat: `kit.py web-env ask` posts the question (`KIT-web-env`); on his Like, `kit.py resume` says so; make the `create_environment` call `kit.py web-env` prints, then `kit.py web-env --id <env_...>` (commit the config).
- From then on the hand-over (`handover.py`) and the clerk start the orchestrator with that `environment_id`: every repo and the hooks from its first turn.

## Messages between sessions are pointers
A message between sessions (`SendMessage`, `send_message`) is at most 200 characters and never holds a table, a quote or a code block (the hook refuses it): the detail is said once, in a post or a comment, and the message names where: "Done. Check post <id>.", "Gaps under your post.". Every message is paid twice, by the writer and by the reader. The clerk's forward of the owner's words is the one exception.

## Branches
- Before `kit.py done`, merge the trunk (`git fetch origin main && git merge --no-edit origin/main`) and rerun the tests: `done` refuses a branch that lacks the trunk, so a part that started on an older kit is judged by the kit as it is now.
- `kit.py launch` puts an attached board's prompt on its trunk (no `part-<post>` branch) when the owner gave the trunk permission. `python3 -I .kit/kit.py branches` lists origin's branches fully on the trunk (`--delete` removes them, the owner's go); a branch with work not on the trunk, or of a part still at work, is kept.
