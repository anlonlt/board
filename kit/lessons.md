# The kit's lessons

Every lesson here reaches the roles it names through `python3 -I .kit/kit.py resume --role <role>`; a part gets one
with `files:` only when its change touches them; one with `where: attached` reaches only a project that has another
repo attached, or the kit's own. A project's own lessons live in its memory (`lessons/*.md`, same
format, `kit.py lesson add`); a lesson only a project doc holds is refused (`kit.py lessons check`).

## K1: A step on another repo's board launches from that repo's clone
roles: orchestrator
where: attached
from: O53 2026-11-23 22:04
`kit.py launch <post>` from your project finds the post on the attached board and runs the launch in that board's clone next to yours. That clone starts no seeded board and never auto-pushes a board-mirror: an orchestrator's clone of anlonlt/board pushed seeded posts to its board-mirror. The clone may be read-only (a public board, nothing to attach): a push of the prompt refused for access makes launch put the prompt's full text in the create_session prompt (CL40).

## K2: A change to compact, install, resume, hooks or artifact is tried on a real project
roles: part, review, orchestrator
where: attached
files: scripts/kit.py, install.py, kit/hooks/*, kit/web_takeover.py, kit/claude/kit_artifact_sync.py, kit/board/board_server.py
from: CL29
The kit's suite passed CL29's compact while Harmonize's story checker went red. The orchestrator's session, which holds both repos, runs the changed command in a clone of the consuming project (never pushed) and puts the command and its output in the part's `real_project`; a launched part never attaches a repo (CL38: a launched session's classifier refuses to attach one unless the owner types it; CL39: on the web the orchestrator's environment holds every repo); `kit.py done` says it is pending until then.

## K3: Reuse the kit's commands before writing code
roles: part
files: kit/*, .kit/*, scripts/kit.py, install.py, *board*.py, *artifact*.py, *mirror*.py
from: CL28
`kit.py commands <word>` for each thing the task touches; CL28 rebuilt what `resume` and `artifact load` already did.

## K4: A post changed only on the local copy is not on the board
roles: part, orchestrator
from: CL28
With an Artifact board, `kit.py artifact push`, make its batch, `artifact sent`, before saying Done; the owner saw the step still planned.

## K5: Never write over a newer live card
roles: orchestrator
files: kit/board/kit_cards.py
from: CL31
`_dashboard` and `_trains` go through `artifact pin` (get the live copy first); a seeded or older local card is put back, never sent. The kit-cards agent rebuilds them.

## K6: An attached board's comment is answered on that board
roles: clerk, orchestrator
where: attached
A comment under `repos/<id>/comments` goes to the session the post on board `<id>` names; the reply goes back to `repos/<id>/comments`, never to this board.

## K7: Phone first, then every width
roles: part, review
files: kit/board/*.html, kit/board/shim.js, kit/claude/artifact_*.js
from: AGENTS.md
Read docs/DESIGN-PRINCIPLES.md before a UI change; `test_page_layout.js`, `test_page_ux.js` and `test_page_speed.js` run in the suite at a phone and a desktop width.

## K8: A board this server does not hold is refused, never read from the primary
roles: part, orchestrator
where: attached
files: kit/board/board_server.py
from: CL32
`--repo board` in Harmonize's cloud clone used to read Harmonize's own posts (the portfolio path named a folder on the owner's computer). An attached board resolves from its clone next to the repo, or from what `artifact load` put in `.kit/attached/<id>`; else a 404 naming what to do.

## K9: A part's result lives in its post body, pictures and diagrams included
roles: part, review, orchestrator
from: CL36 2026-11-24
CL34 wrote its text into `docs/CL34.md` and its post was 8 headings each embedding a section; the owner: "What was wrong with the SVG that they included in the post then?". Write the result in the body under `##` sections, pictures as `![what](path.png)`, diagrams as ```` ```mermaid ```` or ```` ```svg ```` blocks; never a `docs/*.md` file for your text (`kit.py done` refuses a body made of its section embeds).
