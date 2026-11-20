# Import a Claude Code project's state into the kit (no chat export)

For a project built in Claude Code (issue #3 item 2): its state already lives in the repository (CLAUDE.md, hand-over
docs, decision logs) and in its sessions' transcripts. Step 0's survey gathers most of it; this is its last part, or a
step of its own after Install. **For Claude, in the project's session:**

1. **Scope:** the answer to step 0's first question (this repository, or every project). List the sessions
   (`list_sessions` mine, paged) and keep only those whose source repository is in scope (`get_session` names it).
2. **Read, to files, never whole into context:** the repository's CLAUDE.md, its hand-over / handoff / state /
   decision docs (`git ls-files '*.md'`, newest first), and for each in-scope session its last user and assistant
   events (`list_events` kinds `["user","assistant","result"]`, limit 100, saved to files) and its state (`blocked`,
   `idle`, merged PR or not).
3. **Group into workstreams** (one per line of work: a feature, a branch family, a hand-over doc). For each, write:
   `next` (the next step, from the newest hand-over or session), `where` (where it stands), `decisions`, `ids`
   (branches, PRs, deploy urls), `waiting` (what waits on the owner, each with `default` and `options` when it is a
   choice), `sources` (each file § and session id it came from), `source_of_truth` (the repository's own docs that
   stay authoritative: `.story/` points to them, it never replaces them), `priority` (1 urgent, 2 active, 3 paused).
   Top level: `owner_language`, `preferences` (standing rules the owner repeated), `never` (what the kit must not touch,
   from CLAUDE.md), `unread` (what you could not read, and why).
4. **Check yourself:** for each workstream, could a new session resume from it alone? If not, add what is missing
   from the sources; ask the owner only what no file or session holds (one question at a time).
5. Write it as `/tmp/kit-state.json` (the shape in `.kit/kit_import.py`'s header), then
   `python3 -I .kit/kit_import.py /tmp/kit-state.json` (add `--no-posts` when the install has no board; else
   `--session <the orchestrator's id>` and send the printed `writes` with `ArtifactData` `batch`, 50 per call).
6. Commit `.story` and push. Report: the workstreams, the waiting items, what stayed unread.
