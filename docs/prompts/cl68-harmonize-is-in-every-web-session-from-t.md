Board, part CL68: Harmonize is in every web session from the start: no add_repo, which auto mode refuses despite CL38's rule. Train `board-data`. Your step's post: `cl68-harmonize-is-in-every-web-session-from-t` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl68-harmonize-is-in-every-web-session-from-t`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "Not this problem again? The add repo problem?"

Found (orchestrator 3, 03:36): `.claude/settings.json` has the CL38 allow rule `mcp__claude-code-remote__add_repo`, yet in auto mode the classifier refused `add_repo anlonlt/harmonize` ("Unauthorized Access"), and then refused `kit.py attach anlonlt/harmonize` too. So CL38's fix (an allow rule) does not hold under auto mode. Six Done steps (CL17, CL50, CL54, CL56, CL59, CL60) are stuck on their Harmonize real-project run because of it. CL39 built the other way (one web environment holding every repo, `install.py --web-multi`), but the kit's own repo has `kit.web_env` off, so its sessions still start with anlonlt/board alone.

Done when:
1. The kit repo runs `install.py . --update --web-multi yes --web-repos anlonlt/board,anlonlt/harmonize`; `kit.py web-env` prints the create_environment call; the owner approves it once; new orchestrator and part sessions start with Harmonize already checked out (no add_repo at all).
2. The real-project gate, on the web, finds Harmonize in that checkout and runs the post's command there; when Harmonize is missing it says which environment to use, never "attach it".
3. README § Runners and CL38's post say that an allow rule does not pass the auto-mode classifier, so add_repo is never the kit's way on the web.
4. Test: a web session with `kit.web_env` on and both repos present runs the real-project check without add_repo. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
