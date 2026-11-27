# Memory Ledger

Three-layer memory: silent scoped observations -> mechanical invariants (hooks) -> distilled principles.
Managed by `python3 -I .kit/kit.py ledger ...`.

## F1 (fix, active): The review launcher worked on one platform only: a rule made for one platform applied to the others
scope: project
enforced_by: review
theme: platform leak
source: CL26
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:03Z

## F2 (fix, active): The orchestrator's brief rule written for one platform applied to the others
scope: project
enforced_by: review
theme: platform leak
source: CL28
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:03Z

## F3 (fix, active): The usage ledger read one platform's session files on every platform
scope: project
enforced_by: review
theme: platform leak
source: CL37
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:03Z

## F4 (fix, active): agy: the STALE gate made for the web stopped agy's runtime
scope: project
enforced_by: review
theme: platform leak
source: CL44 item 1
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:04Z

## F5 (fix, active): agy: the PreToolUse schema of Claude Code given to agy
scope: project
enforced_by: review
theme: platform leak
source: CL44 item 9
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:04Z

## F6 (fix, active): A session on a computer wrote to the Artifact, a rule for the web applied to computers
scope: project
enforced_by: review
theme: platform leak
source: CL51
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:04Z

## F7 (fix, active): On a computer a part ran the web's real-project rule
scope: project
enforced_by: review
theme: platform leak
source: R4
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:04Z

## F8 (fix, active): The cards agent retired on computer installs broke the web's orchestrator on the same repo (fixed again by 38b1803)
scope: project
enforced_by: review
theme: platform leak
source: commit 106bba6
design: r5-every-rule-that-differs-by-platform-says
created_at: 2026-11-27T02:42:04Z

## F11 (fix, active): resume --role part printed the task and exited 0 when its sandbox hid git and the board
scope: project
enforced_by: review
theme: silent error
source: commit bd6791b
design: cl71-no-merge-friction-generated-files-are-no
created_at: 2026-11-27T02:42:05Z

## F12 (fix, active): M1: the map's overview said nothing of the 568 files it could not read
scope: project
enforced_by: review
theme: silent error
source: commit 376fa17
design: cl71-no-merge-friction-generated-files-are-no
created_at: 2026-11-27T02:42:05Z

## F13 (fix, active): Page tests went on when a seeded post was refused
scope: project
enforced_by: review
theme: silent error
source: commit 0703d5e
design: cl71-no-merge-friction-generated-files-are-no
created_at: 2026-11-27T02:42:06Z

## F14 (fix, active): A post updated on the local copy only: the Artifact still showed it planned (K4)
scope: project
enforced_by: review
theme: two copies out of sync
source: CL28
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:06Z

## F15 (fix, active): artifact load wrote the Artifact's older copies over 11 newer local posts
scope: project
enforced_by: review
theme: two copies out of sync
source: CL51
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:06Z

## F16 (fix, active): The Artifact and the board's git copy drifted apart with nothing reconciling them
scope: project
enforced_by: review
theme: two copies out of sync
source: CL45
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:06Z

## F17 (fix, active): The installed .kit copies were older than kit/ after a merge (CL44)
scope: project
enforced_by: review
theme: two copies out of sync
source: commit cfe798c
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:06Z

## F18 (fix, active): The kit's own .kit copies were older than kit/ (CL33)
scope: project
enforced_by: review
theme: two copies out of sync
source: commit 7821ba3
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:07Z

## F19 (fix, active): A second copy of the orchestrator's guide beside resume
scope: project
enforced_by: review
theme: two copies out of sync
source: commit 22e889c
design: cl72-one-way-to-touch-the-board-the-kit-print
created_at: 2026-11-27T02:42:07Z

## R1 (rule, active): Git is the one store of the board and the memory: no second copy that nothing keeps equal
scope: project
enforced_by: review
theme: git is the one source
quote: No sprawl, no double copies: git is the one store; every post and comment a file
source: docs/KIT-GOALS.md (the owner's goals, f189539 2026-11-24)
created_at: 2026-11-27T02:42:14Z

## R2 (rule, active): Git is the one source: a board, a mirror or an Artifact copy follows it
scope: project
enforced_by: review
theme: git is the one source
quote: git is the one source
source: the M10 task: said about ten times by 2026-11-26 (the orchestrator's count, chat)
created_at: 2026-11-27T02:42:14Z

## R3 (rule, active): Never hide an error: what failed is said, with why, never a quiet success
scope: project
enforced_by: review
theme: never hide an error
quote: never hide an error
source: the M10 task: said several times by 2026-11-26 (the orchestrator's count, chat)
created_at: 2026-11-27T02:42:15Z

## R4 (rule, active): An error is said, never a task printed as if all were well
scope: project
enforced_by: review
theme: never hide an error
quote: an agy part's sandbox hid git and the board, resume printed the task and exited 0, and the part redid saved work
source: the owner, 2026-11-26 (scripts/kit.py PartStop)
created_at: 2026-11-27T02:42:15Z

## D1 (decision, active): Every AI link reaches its post's drop box: an outside AI attaches files, never pastes them
scope: files:kit/board/board_server.py
enforced_by: code:kit/board/board_server.py#The post's drop box (b52f5b6, the owner's design)
quote: post-scoped artifact drop-box
source: commit b52f5b6 2026-11-22; commit 75a4f41 2026-11-26
because: one place per result: files land on their post, not in a chat (b52f5b6, wired to 'Link for AI' by 75a4f41)
rejected: files pasted into the text; a share link that reaches no drop box
created_at: 2026-11-27T02:42:43Z

## D2 (decision, active): The web's cards agent is installed in every repo, computer installs too
scope: files:install.py
enforced_by: code:install.py#ins.put(".claude/agents/kit-cards.md"
source: commit 38b1803 2026-11-26
because: Claude Code on the web opens any repo on GitHub and has no board server to build the cards (38b1803)
rejected: the agent only on web-only installs (106bba6 retired it, the web orchestrator then found it missing)
created_at: 2026-11-27T02:42:43Z

## D3 (decision, active): One board token per machine, kept across projects and checkouts
scope: files:kit/board/board_token.py
enforced_by: test:tests/test_board_token.py::TestBoardToken.test_new_project_reuses_machine_token
source: commit 88b4f3a 2026-11-26; CL11
because: every checkout reaches the running board with the same token; a device that saved an older one is handed the current one (88b4f3a, d9250d4)
rejected: a token per project
created_at: 2026-11-27T02:42:43Z

## D4 (decision, active): Git is the one source: the board, its mirror and the Artifact are copies that follow it
scope: project
enforced_by: review
quote: No sprawl, no double copies: git is the one store
source: docs/KIT-GOALS.md (f189539 2026-11-24)
because: the owner's goal 5: no sprawl, no double copies
rejected: a board or an Artifact as its own record
created_at: 2026-11-27T02:42:43Z
