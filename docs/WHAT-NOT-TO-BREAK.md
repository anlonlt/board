# Before merging a step into main: what must keep working (2026-11-23)

The session "Cross repo board functionality" (2026-11-22/23) added to `main`: cross-repo boards in the Artifact,
the Artifact mode (`kit.py artifact`), `kit.py reconcile`, `install.py --update`, `update_board.py`, the clerk
starting an orchestrator only when needed, save points, and say-it-once fixes. A step branch made before them, or
one that touches the same files, can undo them silently. For every step (CL9 and later) before it reaches `main`:

1. **Merge `origin/main` into the step's branch first** (main moves several times an hour), never the other way
   round without it.
2. **Run `python3 scripts/run_all_tests.py`: no new failure.** The suites that guard this work:
   `tests/test_artifact_cross_repo.py`, `tests/test_artifact_mode.py`, `tests/test_reconcile.py`,
   `tests/test_install_update.py`, `tests/test_clerk_starts_orchestrator.py`, `tests/test_update_board.py`,
   `kit/hooks/test_hooks.py`, and in Chromium (`tests/test_artifact_cross_repo.py` PageSwitcher runs them):
   `kit/claude/test_artifact_repos.js`, `kit/claude/test_artifact_page.js`.
3. **Do not remove or rename** (each is load-bearing):
   - `kit/board/board.template.html`: `prefixFor()` sending `[repo <id>] [post <id>]`, and the line
     `if (db && window.__KIT_WRAP_DB__) db = window.__KIT_WRAP_DB__(db);` after `w.use("db")`.
   - `kit/claude/build_artifact_page.py`: `artifact_repos.js` injected before the adapter.
   - `scripts/kit.py`: `local_board_url()` / `_resolve_board_url()` mapping a `claude.ai` board_url to the local copy;
     `board_docs()` (lookups without a git copy); `reconcile`, `artifact_*`, the `quiet N min` marks in `resume`.
   - `.kit/board_data/token`: the scripts (kit.py, web_takeover, the server's auto-reconcile) read the token there.
   - `install.py`: `claude_cli` (guides for any local runtime) vs `claude_cli_hooks` (`.claude` hooks only for
     `local`); the Artifact mode files for every runtime; `--update`'s KEPT logic; `kit.page_out`.
   - `kit/hooks/kit_say_it_once.py`: an answer to the owner (a turn he typed) is never retried at Stop, and the "·"
     reminder after a board write is only for turns he did not type. **CL14** may tighten background sessions
     (KIT_ROLE) and "· alone", but must keep both of these (`kit/hooks/test_hooks.py` checks them).
   - `kit/claude/clerk.template.md`: an orchestrator is started only to pass on the owner's words, never at set-up;
     `[repo <id>]` comments use `repos/<id>/...` and that board's orchestrator.
4. **CL12** (old base): its delivery-ticks conflict in the page is not this work; resolve it, then rerun
   `test_artifact_repos.js` on a built page (it checks the `[repo ...]` send).
   **CL20** (resume reads attached boards): read them from `kit_repos/_index` and `repos/<id>/posts` (or each repo's
   board-mirror, as `kit_artifact_sync.py boards` does); keep `kit.py artifact pull/load/push` working.
5. **After a merge into main that changes the page or `.kit/`**: in Harmonize run
   `python3 -I <board>/install.py <harmonize> --update` (it rebuilds `docs/board/harmonize-board.html` from
   `kit.page_out`), check the page offline (`KIT_PAGE=docs/board/harmonize-board.html node <board>/kit/claude/test_artifact_page.js`
   and `test_artifact_repos.js`), then republish it to the board's url (agent `board-publish`), and push Harmonize.
