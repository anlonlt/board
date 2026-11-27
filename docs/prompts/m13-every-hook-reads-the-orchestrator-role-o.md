Board, part M13: Every hook reads the orchestrator role one way: 6 of 6 on the takeover fixture. Train `memory`. Your step's post: `m13-every-hook-reads-the-orchestrator-role-o` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m13-every-hook-reads-the-orchestrator-role-o`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Every hook that decides "is this the orchestrator?" reads the role one way: 6 of 6 say orchestrator on M12's takeover fixture.

CR-m12 (redirect, 2026-11-27): M12 routed 3 hooks through `kit_pre_compact.session_role`; `kit_orchestrator_guard.is_orchestrator`, `kit_brief_guard.is_orchestrator` and `kit_cards_reminder.is_orchestrator` still read KIT_ROLE / branch / the first prompt, and the orchestrator guard caches "other" in /tmp at its first call, so a later takeover is never seen. Measured on M12's fixture (owner question first, then `resume --role orchestrator`, 528k): those three say False. Script: scripts/review/CR-m12-the-orchestrator-hands-over-at-its-conte-other-hooks.py.

1. The three hooks call `session_role`; the per-session cache refreshes when the transcript grows (as kit_task_train does) or is dropped.
2. Test: the takeover fixture through all 6 role-deciding hooks: 6 of 6 orchestrator; a non-orchestrator fixture: 0 of 6. It fails on today's main.
3. This branch carries M12 (merge origin/m12 first); M12 lands with it.
(Harmonize's fork context_handover.py is CL61's.)

## What this step answers (the owner's words, or a review's)
- comment `CR-m12-the-orchestrator-hands-over-at-its-conte`: read it with `python3 -I .kit/board_cli.py get comments CR-m12-the-orchestrator-hands-over-at-its-conte --out-dir /tmp/src`

## What this does differently
M13: builds on M12 (3 hooks read the role through session_role) and M11; M13 routes the other 3 role-deciding hooks the same way and drops the stale cache.
