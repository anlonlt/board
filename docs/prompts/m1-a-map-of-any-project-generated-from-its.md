Board, part M1: A map of any project, generated from its code. Train `memory`. Your step's post: `m1-a-map-of-any-project-generated-from-its` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m1-a-map-of-any-project-generated-from-its`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Build `kit.py map`: a map of any project, generated from its code, never written by hand, that a session reads in one screen and zooms into.

Why: in the owner's French repo the game ("jeu") never fit in a session's context. It had about 7,100 lines of hand-written docs about it, ordered by date, partly stale (its main doc predates 149 of the 168 commits touching the game), with two docs each claiming to be the authority. The one part that stayed true was generated from code and checked by a test (`pnpm carte`, the engine's actions). The 11-25 audit found three serious bugs, all at the joins between layers, which no doc showed: the UI sends a `feuilles` field that the API's Zod schema silently drops; the projection still sends students data the screen no longer shows; a teams route has no CSRF check and no membership check.

1. `kit.py map [--root DIR] [--out DIR]` writes the map, in scripts/kit.py and scripts/kit_map.py: `overview.md` (one screen, at most 60 lines: the areas of the project, what each is for in one line taken from code, its size, its entry points), one card per area (files with line counts, exports, what it imports and what imports it, its tests), and `map.json` with the same facts. Default out: `.board/map/`.
2. Readers for Python (ast: functions, classes, imports) and TypeScript/JavaScript (exports, imports, Next routes under the app folder with their methods, Zod objects, Drizzle tables), without installing anything in the project.
3. The joins table, the part no doc had: for each route or action, the fields the UI sends, the fields the schema accepts, the handler, the tables it touches, its tests. A mismatch is flagged in the overview: a field sent but dropped, a write route with no auth or CSRF check, a handler nothing calls, a file too big to read whole (over 1,500 lines).
4. A project's own generators: a list of commands under "map" in the kit config (French's `pnpm carte`); the map runs them and links their output from the area's card.
5. Fresh or refused: the map records the commit it was built from, and `kit.py map --check` fails when the code changed since; tests in tests/test_map.py on small Python and TypeScript fixtures, with scripts/kit_map.py.
6. The real case, read-only: build the map of ~/french into a scratch folder (never write in ~/french). Its overview fits one screen; the game's area card names the engine, the round screen and the action route; the joins table flags the dropped `feuilles` field and the teams route with no CSRF check.

Bars: how many of the audit's three join bugs the map flags (out of 3), the overview's length in lines, the build time on ~/french. Keep `map` in the kit's command index.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
