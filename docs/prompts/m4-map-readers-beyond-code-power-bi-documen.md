Board, part M4: Map readers beyond code: Power BI, documents, any folder. Train `memory`. Your step's post: `m4-map-readers-beyond-code-power-bi-documen` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post m4-map-readers-beyond-code-power-bi-documen`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Readers beyond code, so the map fits any project: Power BI apps made in a browser, a thesis, a folder of documents. Waits on M1.

1. A Power BI reader, in scripts/kit_map.py: from a project saved as PBIP (model files and report files), things are sources, tables, relationships, measures, pages, visuals, apps; links are a visual using a measure, a measure reading a table, a table loading a source; flags are a visual using a missing measure, an unused measure, an ambiguous or missing relationship, a source on a personal file. When the project lives only in the service, say what is needed (PBIP export or the service's API) instead of mapping nothing.
2. A documents reader (Markdown, LaTeX, Word): sections, claims with citations, figures, data and the script that made each figure, research questions; flags are a citation missing from the bibliography, a figure never referenced, a question no chapter answers, a figure whose script is gone.
3. A generic reader for any folder: headings, links, references, sizes; a thing too big to read whole is flagged.
4. Small fixtures for each, in tests/test_map_readers.py with scripts/kit_map.py.

Bars: per fixture, the flags found out of the faults planted in it.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
M4: builds on M1 (a map generated from code); M1 reads code only, M4 adds readers for Power BI, documents and any folder to the same map.
