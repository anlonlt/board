For you: post bodies now draw diagrams and pictures themselves, and `kit.py done` refuses a post that is only section links to a docs file the part wrote. Nothing to do; old posts render exactly as before.

You were right: nothing forced CL34's `docs/CL34.md`. The kit's own rule told parts to put "a report to keep" in a docs file and embed it section by section, and `BOARD.md` said post bodies take "no images inline, no diagrams". Both are gone. This post is written the new way: its diagram and its picture are in the body.

## What changed

```mermaid
flowchart LR
  A["Part's result"] --> B["Post body"]
  B -->|"text, ## sections"| C["Owner reads one place"]
  D["```mermaid / ```svg block"] --> B
  E["![picture](repo/path.png)"] --> B
  F["docs/X.md § Section embeds only"] -.->|"kit.py done refuses"| B
```

| | before | now |
|---|---|---|
| ```` ```mermaid ```` in a post | shown as code | drawn as a diagram |
| ```` ```svg ```` in a post | shown as code | drawn as a picture (an image of it; its scripts never run) |
| `![alt](docs/x.png)` in a post | shown as text | the picture inline (zoom like any post picture) |
| `` `docs/x.svg` `` by its path | inline viewer | unchanged |
| `` `docs/x.md § S` `` (old posts) | that section inline | unchanged |

The body uses the document viewer's own functions (`renderMermaidBlock`, a new `renderSvgBlock`, `resolveAssetUrl` in `kit/board/shim.js`), so the local page and the Artifact page draw the same thing; the document viewer gained ```` ```svg ```` too.

## The rule, everywhere it was taught

The result lives in the post body; a repo `docs/*.md` file is never used for a part's text; pictures go inline (or in `images`); diagrams are ```` ```mermaid ```` / ```` ```svg ```` blocks or an `.svg` by path. Changed in: `kit/board/BOARD.template.md` (Say it once, Which feature for what, the old line 58), `kit/claude/BOARD.template.md`, `kit/part.template.md`, `kit/claude-cli/part.template.md`, `kit/skills/board/SKILL.md`, `resume --role part` (item 0 of How you report Done), lesson K9 in `kit/lessons.md`, and the installed copies in `.kit/`. `tests/test_cl36_post_body.py` greps kit/, scripts/, .kit/, the skills and install.py for 8 phrasings of the old rule: none left. O54's check found one more copy in `README.md` (the "Say it once" line); it now teaches the new rule and the test greps `README.md` too (red on the old README, commit 201a5c0). Only the old generated prompts in `docs/prompts/` keep it (history; moving old docs is out of scope).

## The done guard

`kit.py done` now refuses a body where at least 2 embeds point to a docs file this step wrote (`docs/<PART>.md`, or any `docs/*.md` added on the branch) and those embed lines are half the body or more. Embeds of files the step did not write (the kit's guides, an older report) pass, and so does one embed among real text. CL34's body (8 headings, each one embed) is exactly the refused shape.

## What it looks like

![This post's renderer on the test page: a mermaid diagram, an inline picture, an svg block and an svg file](docs/cl36/post-body.png)

## Harmonize (real project)

Installed on a clone (branch `cl36-dryrun`, never pushed): its `BOARD.md` and part guides carry the new rule, `resume --role part` prints it, the page test passes on Harmonize's installed page (6 failures on its page before the update). All 193 existing post bodies of Harmonize and this board render byte-identical with the old and new renderer: no existing post breaks.

## What I checked {fold}

- `python3 scripts/run_all_tests.py`: 11/11 suites, 0 failed (`test_page_body.js` added to it).
- Red before: `test_page_body.js` on the page before the change: 6 FAIL (mermaid, picture, svg, at phone and desktop); `test_cl36_post_body.py`: the guard and wording tests fail on the old kit.
- The Artifact page: `test_artifact_page.js` now checks a post body draws a mermaid block, a picture mapped to its uploaded asset (`/_blob/...`) and an svg block; passes on a freshly built page.

## Not checked {fold}

- A real phone: checked in Chromium at 390 px with touch, not on a device.
- The Artifact page published at claude.ai: checked on the built page with a stub `window.claude`, not republished.
- Full Mermaid syntax: the viewer's renderer draws flowchart edges (as the document viewer always has); other Mermaid kinds show as code, in both places alike.