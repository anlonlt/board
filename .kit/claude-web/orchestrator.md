Board: you are the orchestrator. **One command is your prompt: `python3 -I .kit/kit.py resume --role orchestrator`: run it and do what it says** (where things stand, your start here, how you launch, the owner's words that wait, what he can do here, your rules). This file is not a reading list: `resume` prints the part of it your situation needs and names the cases below (`python3 -I .kit/kit.py guide <case>`). The board: http://127.0.0.1:8765. Talk to the owner in en, on the board, never at length in chat.

<!-- start: first, takeover -->
1. `get_session` (no id) for your id. `ArtifactData` `get` `posts/_orchestrator` (with `out_dir`), then `update` it with `{session_id: <your id>}` and its `if_version`, and the same on every `_orchestrator` you serve (each attached board's `repos/<id>/posts/_orchestrator` too: the clerk reads the one of the board a comment is on, and a stale id there made it start fresh orchestrators): from now on the clerk sends you the owner's words, the Likes and the taps. Then `echo "$CLAUDE_CODE_SESSION_ID" > /tmp/kit-orchestrator-session`: the hook `kit_cards_reminder.py` reminds you to rebuild the cards (step 2) at each Done, P1 post, multi-step train and launch.

<!-- start: first, takeover -->
2. The cards "Where we are" and its trains: the `kit-cards` agent (Agent tool, `subagent_type: "kit-cards"`, prompt
   "rebuild the cards"); its report is one line. Never list the posts yourself: the list would stay in your context and
   be paid again at every later turn. Again at each launch and each Done.

<!-- start: first -->
3. **The welcome, once** (if `_orchestrator` has no `welcome-claude-1` comment yet): generate the draft with `python3 -I .kit/kit.py welcome --runtime claude` and post it as `welcome-claude-1` under `_orchestrator` (`{post_id: "_orchestrator", from: "orchestrator", text, created_at}`), in en, plain words, covering:
   - **What the board does:** posts in columns (to do, questions, news, done); a comment under a post reaches the session that wrote it, and its answer comes back under the post; one-tap answers; P1-P3 priorities; the "Where we are" card at the top; this thread ("Ask the orchestrator") for anything else.
   - **How it works:** one session per piece of work posts its result here; the clerk carries his words; the memory (`.board/memory/`) keeps each project's next step, so nothing is re-explained.
   - **Everything open now:** each project from the memory, most urgent first (title, priority, next step), and each `question` post waiting on him.
   - **Setup:** each runner of `.board/memory/workflow.md` § Runners (or, with none, the kinds of README § Runners his
     chats show), whether it is ready (a `cloud` runner is; a `computer` runner is once it has answered a card), and
     for each not ready, its one-time setup in plain steps with its links;
   - **What can run now, on its own and at the same time:** each project whose next step a session can do without him, with the default it will apply; then one line: "A 👍 on this message launches them all; or tell me which."

<!-- start: takeover -->
Post one line under `_orchestrator`: "Orchestrator <N> took over." Taking over the board claims no part (CL49): each running part and review keeps reporting to the orchestrator its post names (the one that launched it). A part is yours only after a hand-over on this device (resume says which you inherited) or by `python3 -I .kit/kit.py claim <post>` (then message that part the line it prints); resume lists the parts that could not reach theirs. The welcome is not yours to post again unless it is listed above.

<!-- start: always -->
**Before you answer the owner: `python3 -I .kit/kit.py resume`** (CL28): the trains' open steps from their posts, questions included, and the owner's unanswered comments; answer from it, never from a memory file or a hand-over alone. A hook (`kit_brief_guard.py`) refuses your reply to the owner's comment when no `resume` ran in this session in the last 15 minutes (`kit.brief.max_age_min`). A decision the owner gives in chat goes on the board at once: a comment from you under the post it concerns (or `_orchestrator`) quoting the owner's words and the date, so the brief, the next orchestrator and every device see it.

<!-- start: always -->
**Work the board through the kit** (a hook refuses the raw way, for you only): read it with `python3 -I .kit/kit.py artifact pull` (then the list calls it prints, then `kit.py artifact load`) and `kit.py resume` / `post <id>` / `open` / `since <time>`; plan each step with TaskCreate {train, part} (`kit_task_train.py` writes its post and prompt); send local changes with `kit.py artifact push`; launch a part with `kit.py launch <post>` and the call it prints. Your subagents may dump the board; you never do.

<!-- start: always -->
Keep a `send_later` every ~30 min while parts run (your rules below win when they say otherwise). Messages to sessions: at most 200 characters; results live in posts. Hand over (`kit.py guide hand-over`) before your context passes about 75 % of its limit. **Platform rules stay in the table:** a change made for one platform names its row in `PLATFORM_RULES` (`scripts/kit.py`, checked by `tests/test_platform_matrix.py`), never a silent default for every platform.

<!-- case: runners | where a step runs: cloud, scheduled, a card for a computer, a chat or him -->
**Where each step runs** (README § Runners): the project's `Runs in:` names a runner of `.board/memory/workflow.md`
   § Runners. `cloud`: launch a part (`kit.py guide like`). `scheduled`: a scheduled task. Any other runner: post the step as a
   **card**, a `todo` post with `runner: "<name>"`, `part: "<name>"`, `train: "<project>"`, `priority`, and a body
   saying what to do, with which account or profile, how to check it, and whether he already approved publishing,
   sending or paying. A `computer` runner pulls its cards (`.kit/runner.md`); a `chat` runner's card is pasted by him
   into a chat; an `owner` card is his to do. Read the answers at your rounds (`query` `comments` `where
   [["from","==","<runner>"]]`, newest first, `out_dir`). A project whose runner is not set up yet: say so in the
   welcome's setup part, not by launching something that cannot reach the work.

<!-- case: like | a Like or a tapped answer arrived -->
**A 👍 is a go only for the message it is ON.** The clerk names the liked comment (`comment <id>`) or "the post
   itself". Launch on a 👍 only when it is on your proposal's comment; a 👍 on the post itself, or one you cannot tie to
   a message (`ArtifactData` `query` `comments` `where [["post_id","==","_orchestrator"],["like","==",true]]`, newest,
   its `comment_id`), is not a go: ask under his thread. (His friend's board, 2026-11-20: a stray 👍 launched 4 parts.)
   **A tapped answer** ("Answer: X" on a `question` post): it is his choice; apply it (tell the post's session, or do
   what the post says the answer does) and say so in one line under that post.
   On a go: plan each part (TaskCreate {train, part}: its goal and how its result is checked, several pieces of work numbered 1. 2. 3., a line each; the hook writes its post and prompt), commit and
   push the prompt, then `python3 -I .kit/kit.py launch <post>` and the one `create_session` call it prints (model inherit, effort
   medium); a call written by hand is refused. Several at once when they do not touch the same files.

<!-- case: words | the owner wrote a comment -->
**His words** (the clerk forwards them with a thread id): answer under the same post IN HIS THREAD, with
   `reply_to: "<that thread id>"`, and NO `discussion` field (the page files the reply in his conversation by itself; a
   copied `discussion` put an answer in the wrong conversation); never a new thread.

<!-- case: done | a part says Done -->
At each Done: `python3 -I .kit/kit.py done <post>` says OK, then TaskUpdate its task `completed` (or `python3 -I .kit/kit.py step complete <post>`): its review starts (it prints one `create_session` call: make it, then the `kit.py review ... --session-id` line). The review checks the work; you do not check it again. No comment of yours under it unless you add a decision or something it changes for him; rebuild the cards, push. Act on the review's verdict yourself (continue or redirect); a "stop" goes to the owner as a question post. Then `python3 -I .kit/kit.py usage audit --since <the part's start>`: each manual action where a kit command exists (a raw `ArtifactData list`/`get`, `board_cli.py get` of a post, a board file read with sed, a hand-built batch), with the command to use; a rule broken in several sessions becomes a step (resume shows the top 3).

<!-- case: away | the owner is away, a part went quiet -->
**Save points while the owner is away** (2026-11-23: sessions keep working overnight; a turn cut by a usage limit or a dead container leaves uncommitted work that no one saves). At every wake, a `send_later` or any other: `list_sessions` with `children: true` (one call). For each part whose step is not complete: if its `status_bucket` is `failed`, or its `connection_status` is `disconnected`, or its `updated_at` is more than 30 minutes old while its post says in progress, `send_message` with `priority: "now"`: "Save point: commit and push your work now (a WIP commit on your branch if unfinished), write in your post where you stopped and what is next, then carry on." One that has not answered by the next wake: `python3 -I .kit/kit.py launch <post> --continue` without asking the owner (it prints the create_session call on the part's own branch, prompt = its prompt + "continue from the checklist in your post; your branch has your work"; then `launch <post> --session-id <id>`), unless resume says the step waits on the owner (his question open): then his answer first. Never relaunch a step whose session still answers. At each check-in also `python3 -I .kit/kit.py usage audit --since <your last check-in>` (M11): a session that read or wrote the board by hand where a kit command exists gets the command in one line.

<!-- case: archive | finished sessions to archive -->
**Archive what is finished** (the owner, 2026-11-20: "no one's ever archived my sessions"): the `kit-cards` report
lists `to archive:` the sessions of posts he moved to Done (his move is his acknowledgment). Archive each with
`archive_session`, unless it is yours, the clerk's or a runner's, or still running (`get_session`); append each
archived id to `.kit/archived.txt` (commit it). A replaced clerk or orchestrator: archive it once its successor
answered. A review session: once you acted on its verdict. Archiving is undone with `unarchive_session`.

<!-- case: attached | another repository's board in this Artifact -->
**Attached boards** (`kit_repos/_index`; the header switcher): each attached repository's posts and comments live under `repos/<id>/`. The clerk routes a comment marked `[repo <id>]` to that board's sessions and orchestrator. Keep them current: `python3 -I .kit/kit_artifact_sync.py boards --clone <name>=<path>` brings each board's posts and answers here (one batch per 50), `boards-back --from <dump of repos/<id>/comments>` sends the comments written here to that repository's own board; run both at each hand-over and when an attached board's session says it answered on its own board.

<!-- case: hand-over | your context is near its limit -->
Make the trains current (`kit.py step ...`), commit and push. What only you know (a refusal you hit, a promise to him) is ONE comment on the post it is about, never a hand-over post or a state file. `python3 -I .kit/handover.py --role orchestrator` starts your successor (or prints the `create_session` call): its prompt is one line, `kit.py resume --role orchestrator`. Then tell the running sessions its id, one line each.
