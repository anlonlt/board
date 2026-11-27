Board, part CL57: The board's flowchart draws the post's graph: each node once, every edge. Train `cli`. Your step's post: `cl57-the-board-s-flowchart-draws-the-post-s-g` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl57-the-board-s-flowchart-draws-the-post-s-g`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
The board's flowchart renderer draws the graph a post's mermaid block says: each node once, every edge, picture labels kept.

CR-cl36 (redirect, 2026-11-27): on CL36's own post at 390 px, `renderMermaidBlock` (kit/board/shim.js) drew 8 boxes in one column for a 6-node, 5-edge hub; "Post body" appeared 4 times and the edge `E["![picture](repo/path.png)"] --> B` was missing. Four-backtick inline code (```` ```mermaid ````) shows as broken backtick fragments; the table's "now" column is cut at phone width.

1. `renderMermaidBlock` draws a graph: one box per node id, every edge, a label holding `![..](..)` or brackets kept as text.
2. Inline code spans with 3+ backticks render as code.
3. Tests (kit/board/test_page_body.js) on CL36's post body as the fixture: count nodes (6, each once) and edges (5); they must fail on today's renderer before the fix.
4. Check it at 390 px and desktop on the local board page; a picture of each in the post.

## What this step answers (the owner's words, or a review's)
- comment `CR-cl36-a-part-s-result-lives-in-its-post-post-b`: read it with `python3 -I .kit/board_cli.py get comments CR-cl36-a-part-s-result-lives-in-its-post-post-b --out-dir /tmp/src`

## What this does differently
CL57: builds on CL36, which made post bodies render mermaid at all; CL57 makes the renderer draw the graph itself (each node once, every edge, picture labels), tested on CL36's own post.
