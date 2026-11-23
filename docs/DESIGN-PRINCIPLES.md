# Board UI Design Principles

Every change to `kit/board/board.template.html`, `kit/board/shim.js`, `kit/board/board_server.py` or anything else the
owner sees on the board **must** follow these principles. Each one says the rule, then the owner's own words it comes
from, then how to check it. The owner asked for this file twice: "where are the design principles, and why do i keep
having to remention them ?" (agy chat, 2026-11-22 02:32 UTC) and "Do your feel like it correctly distillated all my
ideas, my principles, -stuff like zero chrome, mobile first, icon first, semi-transparent fade, user learning interface
stuff, based on all my design decisions ?" (Claude Code chat, 2026-11-22 21:31 UTC).

Sources: **agy** = his antigravity-cli chats (`~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`,
first 8 characters of the id given); **Claude** = his Claude Code chats on this repo; **board** = his comments on the
board; **goals** = `docs/KIT-GOALS.md` § 1. Times are UTC. His words are quoted as typed.

## 1. Mobile first, and nothing ever leaves the screen

The phone is the main screen: he answers from it (goals: "answered from his phone"). Design for the phone, then widen.
Every header, title, button row and table fits the device's width: text wraps or truncates with an ellipsis, a button
row stays one row, tables scroll sideways inside their own box. Every hover has a touch equivalent.

- "make sure to regularly take screenshots and look at stuff while designing it on mobile and computer view ... so that it really looks perfect" (agy 9158af55, 2026-11-20 09:58)
- "everything should fit in one row on mobile header instead of having to scroll" (agy fb577f1d, 2026-11-21 04:24)
- "headers and its buttons always adapt to the width of the device, so that on phones, buttons are never pushed out of the screen, and the header is just resized accordingly." (agy a4e03644, 2026-11-22 05:32)
- "things that are hover dependant always need an equivalent for mobile" (agy a4e03644, 2026-11-22 06:29)
- "the section headers on posts and comments ... just continue to the left, out of the screen, when they're too long ... is there a way to make sure it never happens again, no matter what is changed or added ?" (board, `_orchestrator`, 2026-11-22 22:03)

Check: screenshots at 390 px and 1280 px wide, light and dark, before calling a UI change done.

## 2. Zero chrome: a gesture before a button

If an action can be a gesture, it is a gesture and gets no button: pinch to zoom (the content zooms, the chrome stays
the same size), edge swipes for side panes, shift-click to open in a window, long-press to select, swipe in Focus.

- "it shoudn't be icon first zoom - if things can be associated to gestures (the zero chome), then they should, so the pinch outward to zoom in ... (so the ui elements stay the same size, but the content zooms." (agy a4e03644, 2026-11-22 03:22)
- "there shouldn't be a button to pop out a post - we already have the shift click as i said." (agy a4e03644, 2026-11-22 05:32)
- "the android slide to go back gestures should be disabled, so the sliding from the left edge inward should open the left document browser pane" (agy a4e03644, 2026-11-22 03:14)
- "holding a post should select it (so no need for the select posts button in that view) on mobile" (agy fb577f1d, 2026-11-21 05:41)

## 3. Icon first, zero emoji

What still needs a control is a compact monochrome inline SVG (`stroke="currentColor"`, `stroke-width="2"`), with
`aria-label` and `title` saying the word. Never the word drawn next to the icon, never a Unicode emoji or dingbat
(👍 ✓ ⚡ 💬 📌 ☑) in a button, badge, list, heading or tooltip. Stored protocol text that is never drawn (the Like row's
`text`, the "👍 Like: ..." message the clerk parses) may keep its emoji.

- "and what is up with all the emojis" (agy fb577f1d, 2026-11-21 03:37)
- "see what you can transform into icons (not emojis), to clean up the space." (agy fb577f1d, 2026-11-21 03:48)
- "the like button that can only be the icon and not the word, share that can be changed to a md download icon, done that can be changed to a check mark icon" (agy fb577f1d, 2026-11-21 04:24)
- "(and why did i say about no emojies. that should carry out for anything we build here)" (agy c296b8f6, 2026-11-22 02:16)
- "the link for ai should be a link icon, and the snooze, and alarm clock with zzz icon - weren't all elements supposed to respect my design principls ?" (agy a4e03644, 2026-11-22 05:24)

Check: `python3 -m unittest tests.test_no_emoji` scans every string the page and the shim can draw, through
`\uXXXX`, `\u{...}`, HTML-entity and CSS escapes (`python3 -I tests/test_no_emoji.py --list` prints each hit).

## 4. Progressive disclosure, with fade

Show what matters now; the rest is one gesture away. Chrome that is not in use fades: a viewer's header fades out after
a moment and its buttons become semi-transparent icon outlines (no background, no fill); secondary controls appear on
hover near them (a touch equivalent on the phone, § 1); planned, not-yet items are gray and semi-transparent. Long
text is not faded: it ends with "... see more" and a tap opens it.

- "a semi-transparent paperclip icon on the lower left of the box for attach, and a semi-transparent send icon on the lower right" (agy fb577f1d, 2026-11-21 03:48)
- "in feed view, show everything should be more discreet (so no fade, text ends with ...see more, and tapping anywhere on the post shows th[e rest]" (agy fb577f1d, 2026-11-21 04:24)
- "the rest of the view selector only shows up when you hover you mouse over it, and on mobile, a semi transparent x button on the top right" (agy fb577f1d, 2026-11-21 05:14)
- "the photo viewer should fade out the viewer's header after a bit, with the button icons going semi-transparent." (agy a4e03644, 2026-11-22 04:37)
- "i said the header literally fades out, and the buttons become transparent, which mean they only become icon outlines, so obvioulsy they wouldn't keep whatever button or colour element that was behind the icon..." (agy a4e03644, 2026-11-22 04:53)
- "future ones planned would be later (the gray dot, and maybe semi-transparent text" (agy a4e03644, 2026-11-22 06:10)

## 5. Pointer-aware drawers, each with its touch gesture

Side panes open when the pointer reaches their edge (left edge: document selector; right edge: outline), not with a
toggle button, and never shrink the reading canvas. On the phone the same panes open with an edge swipe (§ 1, § 2),
with the usual edge handle showing that a pane is there. Nothing opens by itself.

- "there shouldn't be a sidebar button on the document viewer. it should again be pointer aware ... moving the mouse to the right edge shows the header bar, and to the left edge, the document selector bar." (agy c296b8f6, 2026-11-22 02:32)
- "the slide from the right for the section viewer tab on mobile doesn't work at all (there isn't even the usual like to signal there is a navigation bar)" (agy a4e03644, 2026-11-22 03:22)
- "no sidebar should automatically open until i to the usual mouse or phone gestures." (agy a4e03644, 2026-11-22 05:49)

## 6. Scroll containment

Scrolling inside a window, drawer, pane or modal never scrolls the board behind it (`overscroll-behavior: contain` plus
wheel-boundary containment).

- "scrolling when the mouse is on the document browser should never scroll the board open in the background." (agy c296b8f6, 2026-11-22 02:32)

## 7. OS-style windows

Floating views (Repo Docs reader, Ask the orchestrator, post windows, document viewers) behave like desktop windows:
drag the header to move, click to bring to front; the 8 edges and corners show the usual resize cursors, with no
resize buttons; snapping to halves, quadrants or full screen happens only when a header is dragged to a screen edge,
and new windows open side by side without snapping. Windows stay open across views. On the phone a window is a full
sheet.

- "be resizble and movable (basically like any window on computer) ... so no buttons, just having the mouse on the edge of the window turns the pointer to the usual resize arrows, and dragging the heading of a window allows us to move it around." (agy c296b8f6, 2026-11-22 02:32)
- "opening two windows shouldn't automatically snap to the full screen. the automatic snapping should only happen if we take the header of a window and drag it along any edge" (agy a4e03644, 2026-11-22 04:05)
- "windows should stay open no matter what page you're on" (agy a4e03644, 2026-11-22 06:10)

## 8. The interface learns him

What he sets once, the board keeps and reapplies, per device: window sizes and places, text size, image and viewer
sizes (per document type, and per post when he sizes one there), which `##` sections he opens or folds (after two
same-way toggles of a heading name it applies everywhere, CL6). A size set on one device applies to devices with a
similar screen ratio until he changes it there; a phone keeps its own. A reload, even a forced one during an update,
shows exactly the same screen: same view, same windows, same documents open, same places.

- "the window resizes should be included in the save state ... it should always open proportionately sized on any device with a similar screen ratio (but not a phone), until the user changes the resize themselfs on a new device, then that ratio is applied to that device only." (agy a4e03644, 2026-11-22 02:57)
- "so that on a forced refresh during update, the screen actually stays exactly the same, with all the same windws opened at the same spots with the same content" (agy a4e03644, 2026-11-22 02:57)
- "an image resized in the viewer should automatically set that size for any other image of the same dimensions anytime the user reuses the board (and this would obviously be different on big screens)." (agy a4e03644, 2026-11-22 04:37)
- "there would need to be an inter post and inter-review save state so that the board can learn what types of headers the user likes to have revealed and ones it likes to keep hidden." (Claude, 2026-11-22 21:19)

Check: `python3 -I .kit/kit.py sections` prints what he opens and folds.

## 9. Two layers in every post and comment

First what he must know or do, in a line or a short paragraph (and the picture, table or options he decides on);
then, under `##` sections that fold, the agents' audit: what was checked and how, what was not checked, commits, files
read. The audit stays complete, so the next agent and the reviewer can trust it, and stays out of his way.

- "the author can always surface the information it things the user should know, the important stuff, and the rest can be revealed" (Claude, 2026-11-22 21:19)
- "the goal always being token saving while keeping full useful context and transparency without overcrowding what shouldn't be in a board's post/comment format" (Claude, 2026-11-22 21:19)
- Evidence: in 185 Harmonize posts and 1,415 comments the bottom blocks are agent bookkeeping ("Not checked:" 102 times in posts, 50 in comments; "What I checked, and how" 12 times) (agy artifact `fb577f1d/board_ux_overhaul_tasklist.md`).

Check: `python3 -I .kit/kit.py done <post>` refuses a post without its lead and fields (`.kit/BOARD.md` § The post format).

## 10. What it costs him and the agents

- **A Like costs what "yes" costs.** One tap, no words, read as a go on the message it is on. "likes should be the equivalent token cost of me saying yes in chat" (board, 2026-11-13 00:29, goals)
- **Say it once, on the board.** A result is written once, complete, in the post; chat and messages only point to it. "we'd need a hook that basically makes sure agents aren't spending triple tokens, replying in chat, to you, and in the board" (chat, 2026-11-12 09:20, goals); "so that it uses virtually as many tokens if not less to post or comment for an agent as it would've to just reply in chat" (chat, 2026-11-12 09:20, goals); "Shouldn't the board clerk be limited to 0 character messages, since all it does is relay stuff ?" (chat, 2026-11-12 20:00, goals)
- **Do it, don't ask.** What an agent can do itself, it does; the board shows him only what only he can decide. "Why is everyone telling me to do it ? If it notifies such things, it should do it itself" (board, 2026-11-15 23:30, goals)

## 11. Editorial Markdown typography

Posts, comments and Repo Docs read like a Google Doc: real `<h1>`-`<h4>` with anchors, a `72ch` measure, `1.65`
line-height, real lists and CSS task checkboxes, fenced code blocks, styled blockquotes, `<hr>`, and tables whose
columns fit their content and scroll sideways on the phone instead of cutting words.

- "why does our markdown viewer look so ugly, with text spacing and everything (even our posts look quite ugly), compared to lets say any google docs document ?" (agy c296b8f6, 2026-11-22 02:13)
- "tables aren't formated in a way that colums adapt to content and can scroll horizontally on mobile so that words can't be cut up" (agy fb577f1d, 2026-11-21 04:24)

## 12. One 5-state color language

The same colors in Lanes, "Where we are", trains and the task list:

- `you` (waits on him) → `--todo` (orange), steady
- `running` → `--info` (blue), blinking
- `default` (applies by itself at its due time) → `--question` (purple), fading on and off
- `done` → `--done` (green)
- `later` / `blocked` (waiting on another step) → `--muted` (gray), semi-transparent

- "for the where we are, it should be using the same action (you), decision (default), update (runs), done colours, with the added later (when there are prerequisites waiting on another train - then it says waiting on X) ... the labels you stays stuck, runs blinks, default fades on and off" (agy fb577f1d, 2026-11-21 06:48)
- "you can hide all the Ps, since the color is already the indicator." (agy fb577f1d, 2026-11-21 04:24)

## 13. Zero dependencies, live updates

Stdlib Python and vanilla HTML/CSS/JS only; no CDN, npm or browser driver required to run or test the board (a test
that needs Playwright says SKIP, never pass). The `board` keyword is typed once: server and page changes reach the open
page by themselves, with no restart and no manual refresh, and the page keeps its state (§ 8).

- "the goal is to think about the user here, when they use the board keyword, they should only have to use it once, and everything stays up until they close their terminal, and any update is automatically put through without having to ever restart anything" (agy c296b8f6, 2026-11-22 01:05)
- "how come i still need to physically press the refresh button in my browser instead of the funnel auto refreshing the site when theres a fix or update ?" (agy c296b8f6, 2026-11-22 01:21)
- "how do you remove the playwrite dependancy ? is there a universal alternative ?" (Claude, 2026-11-22 19:28)

## Known breaks, not fixed yet

Found while writing this file; each needs its own step:

- § 1: long `##` headers in posts and comments overflow to the left on the phone (board, 2026-11-22 22:03). No
  width check guards every header and button row yet.
- § 1 / § 3: the page is checked in node, not on a real phone (CL10 is that step).
