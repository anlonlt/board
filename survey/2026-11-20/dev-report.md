# Step 0, Claude Code side: blocker log, 2026-11-20

Anonymised: no names of people, organisations or accounts. "The fork" is the user's public fork of `anlonlt/board`.
The survey's own reports are in `survey/2026-11-19/`. This file lists, in order, every blocker hit in the Claude
Code session that finished step 0. Each entry gives what happened, the exact error where there was one, what it cost
the user, and the fix for the kit.

**Verdict: not seamless.** Getting from "can you reach my GitHub?" to a pushed branch took **7 user messages**
("peux-tu accéder", "j'ai pourtant ajouté un token", "je viens de le refaire", the repository URL, "et là" twice,
"oui"). Finishing Part 5 took 6 more (delete the old clone, a stop hook, "search the documents yourself", the
artifact link, a second stop hook, this review). That makes **13 user messages**, and the step's final proof, a pull
request into `anlonlt/board`, **still could not be opened by Claude**: the user has to open it by hand.

## Blocker log

| # | Blocker | Exact error / evidence | Cost to the user | Fix for the kit |
|---|---|---|---|---|
| 1 | **Two GitHub links that look like one.** The claude.ai GitHub connector (MCP) was connected and identified the account. Claude Code's own GitHub link (used by git and the proxy) was not. | `list_repos: no GitHub account linked` while the connector's `get_me` answered normally | Claude's first answer ("yes, I can reach your GitHub") was half true. It overstated: it said the connector could modify the repositories, which was never tested (the chat's connector later got a 403 on write, see the 2026-11-19 dev report) | Part 4 must name both links and say that only Claude Code's link lets Claude push |
| 2 | **Brand-new account invisible to search.** The account had 1 public repository, but repository search returned 0. | `search_repositories user:<account>` → `total_count: 0` | Claude could not find the repository by itself and had to ask for its URL | Ask for the fork's URL up front; do not rely on search for an account created the same day |
| 3 | **Personal token in an environment variable, ignored.** The user had put a token in the cloud environment's variables (`GH_TOKEN`), as the natural first fix. The egress proxy answers every GitHub call itself and ignores it. | `gh auth status`: "The token in GH_TOKEN is invalid". `curl https://api.github.com/user`: HTTP 403, `No linked GitHub account. Connect your GitHub account at https://claude.ai/customize/connectors?auth_start=github&auth_start_force=1&auth_start_org=<org id> and retry.` | 1 round trip, and a live token left in plain text in the environment settings (the user still has to delete it) | Part 4 step 6: say that a token in environment variables is ignored on Claude Code on the web and must not be stored there |
| 4 | **Reconnecting did not take effect.** The user redid the GitHub connection 3 times ("je viens de le refaire", then "et là" twice). The session kept reporting the account as unlinked. | `check_repo_access` → `refused`, `gate: github_link_required`; proxy: same "No linked GitHub account" message | 3 round trips with no visible progress. Claude's advice changed each time (connect-github, the exact proxy link, `/web-setup` in a local terminal, a new session), and none was verified | Give one tested path, not a list of guesses. Give the user a check that names the missing step: ask Claude to run `curl -s https://api.github.com/user` |
| 5 | **What finally worked: an organization admin page.** Access appeared after the user went to `claude.ai/admin-settings/claude-code` (organization settings → Products → Claude Code → Code review → Repositories) and added the fork there. The user found this alone and had to report it ("il a fallu passer par ici", with a screenshot). | Proxy answered `/user` with the account right after | The user had to find an admin page that no step of the kit mentions. **Unproven cause:** the link may have landed at the same time by chance | Part 4: for a Team or Enterprise organization, add this page (or the admin who can reach it). A second tester would settle whether it is required |
| 6 | **Read-only detour.** Before the link worked, Claude cloned the public fork anonymously (read only, no push) into a different folder. Once push access came, it had to clone again into the right folder and delete the first clone. | `add_repo` (read) → "Read access … is already available … this session cannot push" | 1 round trip ("tu peux supprimer l'ancien") and confusion over two copies | Claude Code should not clone read-only in a set-up step whose goal is a push; wait for the link instead |
| 7 | **Automatic permission check refused attaching the repository.** Once the account was linked, attaching the fork with push access was refused by the session's auto mode. | `Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Unauthorized Access].` | 1 round trip: the user had to answer "oui" | Have the Part 5 block say explicitly that Claude may attach the fork with push access, so the user's first message counts as consent |
| 8 | **The reports never reached Claude Code.** The Part 5 block, with the reports inside, was never pasted into this session. Claude Code cannot read claude.ai chats. It searched the user's ~55 claude.ai artifacts and the fork, found nothing, and stopped rather than invent the reports. | `anlonlt/board` API: HTTP 403, "GitHub access to this repository is not enabled for this session" | User: "tu peux chercher les documents toi-même". 2 round trips, plus the chat's failed connector push (403, see the 2026-11-19 dev report) | Hand the reports over as a claude.ai artifact from the start (this is what finally worked). Never show the user the unfilled template |
| 9 | **A stop hook forced a push before the report was ready.** The session's stop hook demanded that every untracked file be committed and pushed. | `There are untracked files in the repository. Please commit and push these changes to the remote branch.` | A partial, provisional report (this file) went to the public fork before the user had reviewed it | Fine for code. For reports that contain personal details, the step should ask the user first, or keep drafts out of the public fork |
| 10 | **False "unpushed commits" alarm.** After all commits were pushed, the stop hook reported 3 unpushed commits. The shallow single-branch clone did not track the new branch, so git could not compare. | `There are 3 unpushed commit(s) on branch 'step0/survey-2026-11-20'.` Remote head and local head were the same commit | 1 round trip for nothing | When Claude Code creates a branch in a shallow clone, it should push with `-u` and check `git status -sb` shows the upstream |
| 11 | **Pull request into `anlonlt/board` refused.** The session may write only to the repository attached to it (the fork), not to the upstream repository. Claude did not work around it, as the block asked. | `POST /repos/anlonlt/board/pulls`: HTTP 403, `GitHub access to this repository is not enabled for this session. …` | The set-up's proof is not complete: the user must open the pull request by hand from a compare link | Part 5 cannot work as written from Claude Code on the web. Either have Claude open the pull request inside the fork, or tell the user up front that they will click "Create pull request" themselves |
| 12 | **Names mismatch between the chat and Claude Code.** The chat's filled block used the date 2026-11-19. This session had already created `step0/survey-2026-11-20` from the template's date. The reports went into `survey/2026-11-19/` on the `-11-20` branch. | Branch `-11-20`, folder `-11-19` | Harmless, but confusing in review | Name the branch and folder once, in the artifact, and let Claude Code reuse them |
| 13 | **An identifier slipped into the report.** Claude Code first wrote the full compare link, which contains the account name, into the 2026-11-19 dev report, against the rule "no name or identifier". It removed it in the next commit. A first version of this file also named the account, its repositories and the admin page. | Commits `c40dc3b` (added), `c56ef94` (removed) | The account name stays in the public branch's history | Claude Code should check its own text for account names before each commit to a public repository |
| 14 | **Tools dropped during the session.** The GitHub and Meetings connectors disconnected and reconnected twice, and the working directory reset. | Session notices | None visible this time, but a step that depends on the connector could fail at random | No change to the kit; noted for the record |

## Outcome

- Commit and push to the fork: worked on the first try, once access existed.
- Pull request into `anlonlt/board`: refused (#11). The user opens it by hand.
- Blockers solved by the user alone, with no help from the kit: #3 (the token, whose real fix was elsewhere) and #5
  (the admin page).
- Blockers caused by Claude itself: the overstatement in #1, the unverified advice in #4, the read-only detour in #6,
  the name leak in #13.

## What the kit should change first

1. One tested set-up path for Claude Code on the web, with the admin page if the account belongs to an organization,
   and a one-line check (`curl -s https://api.github.com/user`) after each step.
2. Hand the reports over as an artifact, and name the branch and folder there.
3. Part 5: plan for the upstream pull request to be opened by the user, or open it inside the fork.
4. Say plainly which link does what: the claude.ai GitHub connector, Claude Code's GitHub link, the Claude GitHub
   App, and a personal token (not used).
