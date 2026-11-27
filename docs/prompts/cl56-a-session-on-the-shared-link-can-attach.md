Board, part CL56: A session on the shared link can attach files to its post. Train `board-data`. Your step's post: `cl56-a-session-on-the-shared-link-can-attach` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl56-a-session-on-the-shared-link-can-attach`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A session that reaches the board through the shared link can attach files to its post, like any other: today the link (`/_share/<token>`) takes text only (submit, JSON, up to 500 KB) and files need the board's own token (`POST /_api/blob`). On 2026-11-26 the Antigravity desktop app, running on another account's Mac with only the link, could not attach its patch, test file and review guide to R1: it pasted the 61.7 KB patch as a post's text (r1-antigravity-on-the-web-how-far-it-antigravity-app--ed2a38) and explained why at length.

Work on anlonlt/board, branch feat/streamline-doctor-memory.

1. `POST /_share/<token>/blob` takes a file for the post the link was shared for (size cap in the kit config, default 5 MB a file, 20 MB a post), stores it like any attached file and returns its id; the submit route can name those ids, so they show on the post. In kit/board/board_server.py.
2. The link's own page and the runner guide for link-only platforms say how (one command or one form field), in the guide link-only sessions read.
3. Nothing else opens: a link still reads only its post's brief and the repo files it already could, and writes only under its post.
4. Tests: a file sent through a link is stored and listed on its post; one over the cap is refused with the cap named; a link for another post cannot attach to this one.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
