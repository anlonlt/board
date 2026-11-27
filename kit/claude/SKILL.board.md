---
name: board
description: How a Claude Code Web session reports on this project's board (a claude.ai Artifact) and answers the owner there: the post at turn 1, columns, comments, typing dots, deliverables, the git copy read through kit. Use at the start of any session that works on a task, when a "Board comment from the owner" arrives, or when asked about the board, the clerk or kit.
---

# board (from the kit, anlonlt/board — Claude Code Web)

The board is a claude.ai Artifact: its url is `board_url` in `.kit/config.json`. A step's post is a form (`python3 -I .kit/kit.py form <post>`, then `kit.py done <post>`): nothing to read first; any other post: `.kit/BOARD.md` § Post; the orchestrator starts from `python3 -I .kit/kit.py resume --role orchestrator`, not from a guide file.

- Posts and comments go through the kit (CL72, a hook refuses every board call the kit did not just print, for every session): `kit.py artifact sync`, `board_cli.py set/update/reply` on the local copy, then `kit.py artifact sync` again until "in sync"; `reply_to` = the doc_id of the comment you answer. Typing dots (`typing/<post_id>`) stay a direct `ArtifactData` `set`.
- Files you deliver for a post (scripts, docs, HTML): `ArtifactData` `set` in `kit_artifacts`, `{post_id, path, base_path, summary, content, by_user, agent_meta: {harness: "claude-code-web", model, effort}, created_at}`; the page lists them under the post with a diff against `base_path`.
- Never republish the page from a working session; the installing session does that with `build_artifact_page.py`.
- Lookups without dumping the board (`.kit/ORCHESTRATOR.md` § 3): `python3 -I .kit/kit.py post <id>`, `open`, `since <ISO>`, `session <post>`, after a sync.
- The Repo Docs reader and inline git diffs read a mirror: after changing docs, run `.kit/kit_artifact_sync.py docs` (and `diffs`) and send the batches it prints.
- **The kit's scripts on the Artifact (steps, prompts, cards, `resume`, lookups):** `python3 -I .kit/kit.py artifact pull` prints the `ArtifactData list` calls that save the board to files; `artifact load` puts them in a local copy (it starts the local board); then the scripts work as on a computer; `artifact push` prints one `ArtifactData batch` per 50 changes, and `artifact sent <batch>` after each. Fewer tool calls and no board content in your context: prefer it to writing post by post.
- Attached repos' boards (`.kit/config.json` `portfolio`) live under `repos/<id>/posts|comments`, mirrored by `.kit/kit_artifact_sync.py boards` from each repo's board-mirror branch; a post of an attached board is answered in `repos/<id>/comments` (the page's Link for AI and the clerk's forward name the right collection); comments written here go back to that repository's board with `kit_artifact_sync.py boards-back`.
