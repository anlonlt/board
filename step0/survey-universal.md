# Kit Step 0 — Universal Survey (Platform-, Workflow-, and MCP-Agnostic)

Use this survey for **anyone setting up `board`** — whether it is their **first day using AI** or they run multi-agent codebases every day, across **any AI client** (`agy`, Claude, Claude Code, Gemini, ChatGPT, Cursor, Codex, Desktop, or Web) and **any kind of work** (code, research, operations, administration, writing, or personal projects).

> **How to use:** Open a new AI session (in your project folder if you have one, or in any chat client with your MCPs/connectors enabled) and paste everything between the two lines below.

---

You are running **Step 0 of the `board` workflow kit: the universal survey**. Do not modify any project files, emails, docs, or tasks — this entire step is **strictly read-only** until the user approves the final setup.

### Part 0 — Upfront Discovery, Language & Single Consent

1. **Detect language:** From the user's message (and any visible chats/docs), determine the language they communicate in and write everything from here on in that language. Record any split (e.g. "talks French, writes code in English").
2. **Inspect your own tool list first** (never guess or claim a tool is missing without checking):
   - **AI chat / session history** (e.g. `recent_chats`, `conversation_search`, `list_sessions`, local transcript logs)
   - **Connected MCPs & SaaS connectors** (e.g. Gmail, Outlook, Microsoft 365 / SharePoint / OneDrive, Google Drive / Docs, Google/Outlook Calendar, Notion, Slack, Teams, Linear, Jira, GitHub, Trello, browser / Claude in Chrome)
   - **Local filesystem / terminal** (if running in a CLI/IDE, `scripts/step0_scan.py` is available)
3. **Ask ONE upfront go-ahead message** covering:
   - List the exact connectors/sources you see available.
   - Ask for permission to run a **read-only scan of the last 14–30 days** across those connectors (titles, subjects, recent files, open tasks, calendar cadence — never editing, sending, or sharing anything) to map their active work.
   - Ask: **"One board for all your projects, or one board per project?"**
   - Explain clearly that **their own private Git repository** will serve as the board's permanent, platform-agnostic memory (so they own their data and can switch between AI tools anytime). Any anonymized feedback to `anlonlt/board` is strictly optional.
   - Accept the first "yes" and proceed autonomously through Parts 1 and 2.

---

### Part 1 — Evidence-First Discovery (Works Even With 0 Prior AI Chats)

Grade every finding as **seen** (cite source + date), **inferred** (from what), or **unknown**. Never invent numbers.

#### 1A. Connected MCPs & Connectors (Read-Only, Last 14–30 Days)
For every approved connector, pull a lightweight read-only sample:
- **Email (`Gmail`, `Outlook` / `Microsoft 365`):** recent active threads, flagged messages, and threads waiting on a reply → identify active workstreams, recurring stakeholders, and items waiting on the user (`awaits.md`).
- **Documents & Knowledge (`Google Drive`, `SharePoint / OneDrive`, `Notion`):** recently edited files/folders → identify active projects, key reference docs to pin, and where outputs actually land.
- **Tasks, Code & Messaging (`Linear`, `Jira`, `GitHub`, `Slack`, `Teams`, `Trello`):** open issues/PRs assigned to the user, blockers, and active channels → identify multi-step trains and blockers.
- **Calendar (`Google Calendar`, `Outlook Calendar`):** recurring meetings/deadlines → identify ongoing workstreams and weekly cadence.
- **Record exact MCP Link Reader adapters needed:** map every connector found to `board`'s pluggable reader IDs (`sharepoint`, `outlook`, `gmail`, `gdrive`, `notion`, `linear`) and generate the matching `--mcp-readers` list.

#### 1B. Past AI Chats & Local Workspace (If Available)
- If chat/session history exists: measure active projects, repeated instructions, forgotten context, usage/action-budget limits, re-approval friction, and autonomy vs. feature-sprawl habits.
- If running in a local repository: run `python3 -I scripts/step0_scan.py .` (if available) to gather local transcript metrics, sibling projects, colour palettes, and repo connector links.
- If the user is **brand new to AI (0 prior chats)**: skip chat metrics cleanly — rely on Part 1A (connected MCPs) and ask at most 2 short clarifying questions if any top project is unclear.

#### 1C. Runners & Aesthetics
- **Runners (`README.md § Runners`):** list where each type of work actually happens (`computer`, `cloud`, `chat`/MCP connectors, `owner`/phone, `scheduled`) and what is already connected vs. not yet set up.
- **3 Inferred Colour Palettes:** from brand docs, stylesheets, or user preferences, propose 3 light/dark colour palettes for the board UI.

---

### Part 2 — Adversarial Verification Pass

Before presenting the report, review Part 1 skeptically (or launch a fresh read-only subagent if your runtime supports it):
- Double-check every `unknown` or `inferred` item with at least one more targeted read-only query.
- Verify no real names, emails, or secrets are included if the user plans to share an anonymized report publicly.
- Confirm every recommended board layer solves a real problem actually observed in Part 1 (consult `docs/KIT-GOALS.md` if available).

---

### Part 3 — Private Git Memory Setup (Platform-Agnostic Backbone)

`board` stores your durable memory (`.board/memory/`) and board backup (`board-mirror`) in **your own private Git repository** — keeping your workflow 100% platform-agnostic so you can use `agy`, Claude, Gemini, ChatGPT, Cursor, or your phone without vendor lock-in.

Check what the user already has and guide them only through missing steps (never paste personal tokens into chat):
1. **GitHub account:** if none yet, guide them to https://github.com/signup.
2. **Private memory repository:** use an existing project repo, or create one new **Private** repo (e.g. `<username>/my-board` or `<username>/<project>`).
3. **Connect the user's chosen runtime:**
   - **Local (`agy` / CLI / Desktop):** clone locally (`git clone ...`) and run `install.py` (with `--mcp-readers ...` if connectors were detected).
   - **Cloud (`claude.ai/code` + Artifact):** connect GitHub at https://claude.ai/connect-github + install the Claude GitHub App at https://github.com/apps/claude on their private repo, then follow `README.md § Install (Claude Code Web)`.
   - **Sandbox / Generic (`arena` / Codex / Cursor):** follow `README.md § Install (LMArena / Sandbox / Generic Agent)`.
4. *(Optional)* Only if the user explicitly wants to contribute anonymized feedback back to `anlonlt/board`, fork `anlonlt/board` and open a PR with anonymized `survey/<YYYY-MM-DD>/report.md`.

---

### Part 4 — Present the Survey Report & Install Plan

Present a concise, scannable report in the user's language containing:
1. **Active Workstreams Discovered** (from MCPs, docs, and/or chats) — ready to become initial `.board/memory/state/<workstream>.md` files and `awaits.md` items.
2. **Connected MCPs & Recommended MCP Link Readers** — exact `--mcp-readers` flag (e.g. `--mcp-readers sharepoint,gmail,gdrive`) and Artifact `capabilities.mcp` snippet if using Claude Code Web.
3. **Recommended Runtime & Layers** — `agy` local / `claude.ai` Artifact / `arena` generic; which layers to install (`memory`, `board`, `clerk`, `orchestrator`, `consultant`) and why.
4. **Autonomy Profile & 3 Colour Palettes** (`full_auto` / `balanced` / `gated`).
5. **Exact Ready-to-Run Install Command** (plus next step to seed the discovered workstreams onto the board).

Begin with Part 0.
