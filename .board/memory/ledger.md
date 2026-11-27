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

## O1 (observation, active): A part never takes another step's unlanded work into its branch: each step's branch holds only its own commits and main, so it merges the moment it is reviewed
scope: project
enforced_by: review
because: owner 2026-11-27: CL59 carried CL16, CL61 merged origin/m12; both held back by the other part
created_at: 2026-11-27T08:58:54Z

## F20 (fix, active): The stop check (CL30) counts every post with no sync base as unsent: sessions told to resend ~360-490 posts they never touched (CL76's part, this orchestrator 9-14 times)
scope: project
enforced_by: review
theme: stale-board-copie
source: CL73
created_at: 2026-11-27T09:15:12Z

## F21 (fix, active): CL59's branch carried CL16's unlanded commits; CL59 held ~5 h
scope: project
enforced_by: review
theme: branch-carries-other-step
source: CL78
created_at: 2026-11-27T09:15:13Z

## F22 (fix, active): kit.py done refuses whenever main moved: CL60 merged main 3 times, CL61 twice, CL62, M3, CL72, CL71 each again after other landings
scope: project
enforced_by: review
theme: merge-loop-main-moved
source: CL78
created_at: 2026-11-27T09:15:13Z

## F23 (fix, active): Every landing conflicted every open branch in .kit/manifest.json and .kit/artifact-files/files-board.json
scope: project
enforced_by: review
theme: conflict-generated-file
source: CL71
created_at: 2026-11-27T09:15:13Z

## F24 (fix, active): R6 compared steps only by names/after/shared files; its 7 tests used those links; missed two steps on the same subject (M14 vs CL75)
scope: project
enforced_by: review
theme: literal-task-not-intent
source: CL76
created_at: 2026-11-27T09:15:14Z

## F25 (fix, active): M5 cut the orchestrator's repeat resume; nobody extended it to parts, which still read their task twice
scope: project
enforced_by: review
theme: literal-task-not-intent
source: CL77
created_at: 2026-11-27T09:15:14Z

## F26 (fix, active): CL71 fixed generated files and land, not done's must-merge-main rule (same goal)
scope: project
enforced_by: review
theme: literal-task-not-intent
source: CL78
created_at: 2026-11-27T09:15:14Z

## F27 (fix, active): Orchestrator 4 planned CL75 (needs M14's numbers) with no link to running M14; owner caught it
scope: project
enforced_by: review
theme: orchestrator-error
source: R9
created_at: 2026-11-27T09:15:15Z

## F28 (fix, active): Orchestrator 4 overwrote CL60 and CL61 real_project (both BROKEN ON HARMONIZE) with a lighter no-regression run; restored after
scope: project
enforced_by: review
theme: orchestrator-error
source: CL76
created_at: 2026-11-27T09:15:15Z

## F29 (fix, active): Orchestrator 4 answered the owner's M-line question meant for the Antigravity orchestrator and marked it read; undone
scope: project
enforced_by: review
theme: orchestrator-error
source: CL66
created_at: 2026-11-27T09:15:15Z

## F30 (fix, active): Orchestrator 4 never wrote the review-scope step it proposed; owner assumed it existed
scope: project
enforced_by: review
theme: orchestrator-error
source: CL75
created_at: 2026-11-27T09:15:16Z

## F31 (fix, active): Orchestrator 4 started orchestrator 5 without source_url: no checkout, could not land anything
scope: project
enforced_by: review
theme: orchestrator-error
source: handover.py
created_at: 2026-11-27T09:15:16Z

## F32 (fix, active): Orchestrator 4 created a duplicate post id (cl78-...-a) by updating a wrong id; marked removed
scope: project
enforced_by: review
theme: orchestrator-error
source: board_cli
created_at: 2026-11-27T09:15:16Z

## F33 (fix, active): M12's review redirect (03:00) sat ~6 h unlaunched under 'launch nothing without go'; CL61 blocked behind it
scope: project
enforced_by: review
theme: redirect-not-acted
source: CL74
created_at: 2026-11-27T09:15:16Z

## F34 (fix, active): CL59's review redirect (hold for CL16) never split out; push fix unused all day
scope: project
enforced_by: review
theme: redirect-not-acted
source: CL73
created_at: 2026-11-27T09:15:17Z

## F35 (fix, active): 35 of 43 reviews said continue after rerunning what done proves; 25 logged reviews cost $82.96, 12 found something new
scope: project
enforced_by: review
theme: reviews-repeat-done
source: CL75
created_at: 2026-11-27T09:15:17Z

## F36 (fix, active): Every Done, review start, verdict and redirect relayed through the orchestrator; delays whenever it was busy or refused create_session
scope: project
enforced_by: review
theme: relay-overhead
source: CL74
created_at: 2026-11-27T09:15:17Z

## F37 (fix, active): create_session refused by the classifier for reviews (CL71, M14) and for add_repo Harmonize, until the owner said so in chat
scope: project
enforced_by: review
theme: session-refused
source: CL74
created_at: 2026-11-27T09:15:17Z

## F38 (fix, active): Launch prompt points to docs/prompts/<post>.md and to resume, which prints the same task; plus false alarms about the part's own session and branch
scope: project
enforced_by: review
theme: part-reads-twice
source: CL77
created_at: 2026-11-27T09:15:17Z

## F39 (fix, active): Owner ordered no review/no tests; pre-push guard and kit_guard refuse pushing CL76+CL78 to main or even to a branch (--no-verify refused)
scope: project
enforced_by: review
theme: guard-blocks-owner-order
source: CL75
created_at: 2026-11-27T09:15:18Z

## F40 (fix, active): ledger fix_kinds counts only hand-written fix entries; land refusals and redirects never reached it, so repeats were never raised
scope: project
enforced_by: review
theme: repeat-counter-manual
source: CL78
created_at: 2026-11-27T09:15:18Z
