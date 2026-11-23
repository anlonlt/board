# CL24: close the orchestrator guard's gaps

Read first: `CLAUDE.md`, `docs/WHAT-NOT-TO-BREAK.md`, `kit/hooks/kit_orchestrator_guard.py`, `tests/test_orchestrator_guard.py`.

**His words (2026-11-23 05:2x, chat to the orchestrator):** "fix the guard gaps" — the gaps the orchestrator listed under Harmonize's kit-board `_orchestrator` (comment `o50-guard-gaps-0345`, discussion "changes").

**Condition for Done (each gap by a test red on main before your change, green after):**
1. **Inline prompt passing on a path:** `create_session` passes today when `docs/prompts/` appears anywhere in the prompt. Now a part's prompt must *start* with `Your prompt is docs/prompts/<id>.md` and stay short (≤ 400 characters); a long prompt that merely names a path is refused.
2. **Title bypass:** a session titled "review"/"clerk"/"orchestrator" passes today. Now only tags pass (`orchestrator`, `board-clerk`, `review`); the title is not read for this.
3. **Attached boards:** a broad `list`/`query` of `repos/<id>/posts` or `repos/<id>/comments` (attached boards in the Artifact) is refused like `posts`/`comments`; a targeted `where` with limit ≤ 5 and a `get` still pass; the kit pull's `out_dir` under `.kit/run/artifact/` still passes. `kit.py artifact pull` prints the `repos/<id>/…` list calls too, so the kit way covers attached boards.
4. **A step post on an attached board:** a `set` of `repos/<id>/posts/<step>` is refused like a host-board step post (not `_` cards, not `_orchestrator`).
5. Subagents stay exempt (by design: they keep dumps out of the orchestrator); say so in the hook's docstring.
6. `python3 scripts/run_all_tests.py`: 0 new failures; the installed copies (`.claude/hooks/kit/`, `.agents/hooks/kit/`, `.kit/`) refreshed by `python3 install.py . --update`; nothing on WHAT-NOT-TO-BREAK removed.

**Holds:** do not loosen anything the guard refuses today; delete nothing outside your change. Trunk push is on: merge origin/main into your branch, test, then fast-forward main (never force). Afterwards, in Harmonize (`anlonlt/harmonize`, if you can reach it) the orchestrator runs `install.py --update`; if you cannot, say so in your post.

**Board:** at turn 1 write your post `repos/board/posts/cl24-orchestrator-guard-gaps` in Harmonize's Artifact (https://claude.ai/artifact/Ek79P6H3WTPJjJVYPe9rsf, ArtifactData; train `cli`, part `CL24`, session_id, branch, status) and keep it current (body = result in full, bars, not_checked). Never set its column to done (only he does). Answer his comments there (a doc in `repos/board/comments`, reply_to his doc id, same thread_id/post_id). Chat ends with a single "·"; to the orchestrator (session_01AE1HGgmrkiQ2sxTnPiSRWU) by send_message: "Done. Check post cl24-orchestrator-guard-gaps."
