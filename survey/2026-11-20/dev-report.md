# Step 0 dev report, 2026-11-20

> Report 2 from the chat (Parts 0-4) is not here yet: the Part 5 block was not pasted into this session.
> This file holds the Claude Code side only. The chat's Report 2 goes above this section when it arrives.

## Set-up check (Claude Code)

- **Repository seen:** `victorcheng28/board`, a public fork of `anlonlt/board`. Permissions reported by the REST API:
  push, maintain, admin.
- **Branch:** `step0/survey-2026-11-20`.
- **Commit:** worked on the first try.
- **Push to the fork:** worked on the first try.
- **Pull request:** not opened yet. It waits for the chat's reports.

### How access was obtained (it took several tries)

1. **Personal token in an environment variable did not work.** The user had put a GitHub token in the cloud
   environment's variables (`GH_TOKEN`). The session's egress proxy ignores it and answers every GitHub call
   itself. `gh auth status` reported "The token in GH_TOKEN is invalid". `curl https://api.github.com/user` returned
   403 with this message, verbatim:
   `No linked GitHub account. Connect your GitHub account at https://claude.ai/customize/connectors?auth_start=github&auth_start_force=1&auth_start_org=<org id> and retry.`
   This matches Part 4 step 6 (never a plain environment variable). The token is useless here and should be removed.
2. **The GitHub MCP connector worked from the start, but it is a separate link.** It identified the user
   (`victorcheng28`). Repository search still returned 0 results for the brand-new account, probably because
   indexing had not caught up. Git in the session still had no access: the connector and Claude Code's GitHub link
   are two different connections.
3. **Reconnecting GitHub (Part 4 step 3) did not take effect at first.** The user redid the connection twice. Each
   time, the session still reported `github_link_required`. Read access worked in the meantime because the fork is
   public: anonymous clones go through the proxy.
4. **What unblocked it: the organization admin page.** The user went to
   **claude.ai/admin-settings/claude-code** (organization settings → Products → Claude Code → *Code review* →
   *Dépôts* / Repositories) and added the repository there. `victorcheng28/test` and `victorcheng28/board` are listed,
   with "review on every push" at the default level. After that, the proxy recognized `victorcheng28`. Attaching the
   repository with push access succeeded, after one refusal by the session's automatic permission check, which the
   user then approved explicitly.

### What to change in step 0 (Part 4)

- For a user on a Team or Enterprise organization, Part 4 should include the admin page
  `https://claude.ai/admin-settings/claude-code` (Code review → Repositories → Add). Without it, step 3
  (connect-github) alone left the session unlinked. Note: it is unproven whether adding the repository there caused
  the link, or whether the link simply landed at the same time. A second tester would settle it.
- Part 4 step 6 should say plainly that a token in the environment's variables is ignored by Claude Code on the web.
  Testers will try it, as this one did.
- Make it clear that the GitHub connector (MCP) working does not mean git push works.
- Add a check the user can run, such as "ask Claude to run `curl -s https://api.github.com/user`". The proxy's
  answer names the exact missing step.
