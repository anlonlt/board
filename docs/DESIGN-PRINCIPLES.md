# Board UI Design Principles

Every change to `kit/board/board.template.html`, `kit/board/board_server.py` (`SHIM_JS`), or any board UI component **must** follow these principles:

1. **Zero emojis anywhere in the UI.**
   Use crisp, monochrome inline SVGs (`stroke="currentColor"`, `stroke-width="2"`) or pure CSS shapes. Never insert Unicode emojis (`📌`, `☑`, `⚡`, `💬`, etc.) into buttons, badges, lists, or headings.

2. **Icon-first, low-chrome controls.**
   Toolbar actions use compact `.kit-icon-btn` monochrome SVG buttons with accessible `title` and `aria-label` tooltips — never verbose text buttons where a clear icon suffices.

3. **Pointer-aware / progressive chrome.**
   Sidebars, outlines, and secondary panels reveal smoothly when the pointer approaches their edge (e.g., left edge → document selector, right edge → document outline) instead of adding permanent toggle buttons or shrinking the reading canvas.

4. **Strict scroll containment.**
   Scrolling inside any floating window, drawer, or modal must **never** scroll the board behind it (`overscroll-behavior: contain` plus wheel-boundary containment).

5. **OS-style floating windows (movable, edge-resizable, edge-snappable, multi-window).**
   Floating views (Repo Docs Reader, Ask the Orchestrator, and popped-out Post windows) behave like native desktop windows:
   - Drag the window header bar to move; clicking brings the window to front.
   - Hover any of the 8 edges/corners for standard OS resize cursors (`ew-resize`, `ns-resize`, `nwse-resize`, `nesw-resize`) and drag to resize — no resize buttons.
   - Dragging near screen edges or corners shows a translucent snap preview and snaps cleanly into halves, quadrants, or maximized view, supporting multiple simultaneously open windows side by side.

6. **Editorial Markdown typography.**
   Posts, comments, and Repo Docs share Google-Docs-grade Markdown rendering: real `<h1>`–`<h4>` hierarchy with anchors, `72ch` comfortable measure, `1.65` line-height, real `<ul>`/`<ol>` lists and CSS task checkboxes, fenced `<pre><code>` blocks, styled `<blockquote>`s, `<hr>` dividers, and clean tables.

7. **Unified 5-state semantic color language.**
   Consistent across Lanes, "Where we are", and "All trains":
   - `you` → `--todo` (orange)
   - `running` → `--info` (blue, blinking)
   - `default` / `ready` → `--question` (purple, fading)
   - `done` → `--done` (green)
   - `blocked` / `idle` ("later") → `--muted` (gray)

8. **Zero dependencies & live auto-reload.**
   Stdlib Python + vanilla HTML/CSS/JS only. Server and UI edits hot-reload automatically without manual restarts or manual browser refreshes.
