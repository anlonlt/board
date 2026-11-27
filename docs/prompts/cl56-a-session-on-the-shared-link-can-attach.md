Board, part CL56: A session on the shared link can attach files to its post. Train `board-data`. Your step's post: `cl56-a-session-on-the-shared-link-can-attach` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl56-a-session-on-the-shared-link-can-attach`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
An outside AI gets one link, the owner's: the invite (`/_invite/<token>.md`, b52f5b6, 2026-11-22: "1-link AI bootstrap & post-scoped artifact drop-box"), which gives it a collaborator token and the post's drop box for files (`POST /_api/posts/<post>/artifacts`). The older share link (`/_share/<token>`, d7c448c, 2026-11-21: read a post, submit text up to 500 KB, no files) still exists beside it, and the R1 runner card handed out that one: the Antigravity desktop app (another account's Mac) could not attach its patch and pasted 61.7 KB of it as a post's text (r1-antigravity-on-the-web-how-far-it-antigravity-app--ed2a38).

Work on anlonlt/board, branch feat/streamline-doctor-memory.

1. Every card, prompt or page that gives an outside AI a link (a runner card, "Link for AI", the clerk) gives the invite, scoped to its post; in kit/board/board_server.py and scripts/kit.py.
2. The share link leads to the same: an old `/_share/<token>` answers with the invite's instructions and its drop box (or is retired with a redirect), so one way exists, not two.
3. The invite's page says, first, how to attach files to the post (the drop box route, one command) and how to write the post's result.
4. Tests: an outside session with only the link a runner card gives attaches a file to its post and it shows there; an old share link reaches the same drop box.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)

## What this does differently
CL56: builds on CL40 (launch without push access) and CL45 (Artifact board on the web); none lets an outside AI with only the shared link attach files to its post. CL56 gives it one invite link with the post's drop box.
