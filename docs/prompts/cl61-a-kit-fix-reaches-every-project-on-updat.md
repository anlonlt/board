Board, part CL61: A kit fix reaches every project on update: no project keeps a fork of a kit hook. Train `takeover`. Your step's post: `cl61-a-kit-fix-reaches-every-project-on-updat` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl61-a-kit-fix-reaches-every-project-on-updat`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A kit fix reaches every project at its next `install.py --update`: no project keeps its own copy of a kit hook.

Owner, 2026-11-27: "Shouldn't anything that's happening in the kit automatically get fixed on kit update in harmonize anyway?" Found: Harmonize runs its own `.claude/hooks/context_handover.py` (last changed 0d020ef, 11-24) as its Stop hand-over hook, wired in `.claude/settings.json`; the kit's `kit_pre_compact.py` is installed but M12's fix does not reach Harmonize because its fork decides.

1. `install.py --update` lists every project hook that does a kit hook's job (same event and purpose: hand-over, role, board post, say-it-once...), and moves what the fork adds into the kit's hook as a project setting, then wires the kit's hook only; a fork it cannot fold is refused with what differs, never kept silently.
2. Harmonize's context_handover.py folded: its rules (no fallback timers, the clerk at 80k) become kit settings; its test passes against the kit's hook.
3. doctor flags a project hook that shadows a kit hook.
4. Dry run on a Harmonize clone (by the orchestrator): after update, M12's takeover case blocks there.

## What this step answers (the owner's words, or a review's)
- comment `m12-the-orchestrator-hands-over-at-its-conte`: read it with `python3 -I .kit/board_cli.py get comments m12-the-orchestrator-hands-over-at-its-conte --out-dir /tmp/src`

## What this does differently
CL61: builds on CL25 (update refreshes every installed file) and M12; CL25 refreshes only the kit's own files, a project's fork of a kit hook stays and decides. CL61 folds forks into the kit's hook.
