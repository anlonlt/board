# Step 0 survey, Part 2: second, independent pass (2026-11-20)

Method: this pass recounted from primary sources (git in the project clone, the GitHub API read-only, `get_session` on all 49
sessions, the 17 raw transcript pages in `data/pages/` re-parsed with a fresh script) and read `part1.md` only after the recount.
Nothing in the project was changed (a `git fetch` refreshed remote refs only). Scope: sessions on the project repository only.

## Numbers

| number | first pass | this pass | source |
|---|---|---|---|
| sessions in scope | 49 | **49, all 49 confirmed** by `get_session` sources/outcomes (first pass: 9 confirmed, 40 inferred) | get_session x49 |
| survey session itself | excluded | is a project session too (sources include the repo; pushed 2 commits on 11-20; $10.53 so far) | get_session (self), git |
| total cost, 49 sessions | unknown | **$2,493.64** (mean $50.89, range $4.07 to $191.49) | get_session `usage.cost_usd` |
| most expensive session | not given | $191.49 (a landing-scene session); then $108.41, $100.55, $95.99, $92.35 | get_session |
| coordinating session cost | $53.30 | $53.30 (same); it ranks 21st of 49 by cost | get_session |
| coordinating session tokens | 287 req, 103.8 M cache read, context 41k to 728k, mean 369k | same; plus cache write 2.29 M. Output tokens in the records sum to only 5.3k: not usable (logged at stream start) | pages, `usage` |
| models (served) | not given | opus-4-8 31, fable-5 12, sonnet-5 4, haiku-4-5 2 (both configured as fable-5) | get_session |
| origins / buckets | 41 web, 8 android / 39, 8, 2 | same | get_session |
| commits, all branches | 721 (633 Claude, 85 owner, 3 bot) | same | GitHub API (clone is shallow, 13 boundaries) |
| commits on default branch | not given | 697 (610 Claude, 85 owner, 2 bot) | GitHub API |
| PRs | 67 (#1 to #67) | 67: 66 merged, **1 still open** (#4, analytics install) | GitHub API |
| merges on default | 68 | 68 = 66 PR merges + 2 default-into-branch syncs | GitHub API |
| remote branches | 51 | 51 | ls-remote, API |
| branches ahead of default | 7 | 7 pre-existing + 1 (the survey's own branch, 2 commits) = 8 | API compare |
| session branches without a PR | not given | 6 (2 of them no longer exist on the remote) | PRs vs get_session |
| Markdown files / size | 46, 1.30 MB | same on default branch (56, 1.33 MB on the survey branch with the kit) | git ls-tree |
| `docs/` | ~212 MB, 772 JPG | 984 files, 212.7 MB, 772 JPG | git ls-tree |
| revamp hand-over doc | 1,273 lines, 9 HANDOFF sections 07-18 to 07-20 | 1,273 lines, **13 dated HANDOFF sections 07-17 to 07-20** | git show |
| owner typed turns (coordinating) | 29 | **22**: the 29 included 4 Stop-hook feedback messages, 2 skill bodies, 1 image note | pages, re-parsed |
| first reply after owner turn | median 50 s, p90 280 s, max 323 s | median 57 s, p90 177 s, max 323 s | pages |
| whole turn | median 145 s, p90 ~25 min, max 7.6 h | median 247 s, p90 ~26 min, max 7.6 h | pages |
| gap between owner turns | median 8 min, p90 27 min | median 8 min, p90 40 min | pages |
| language word counts | 1,806 FR / 125 EN | inflated by 2 skill bodies (one is 46 KB); conclusion (French) unchanged | pages |
| hooks before today | none | **an environment Stop hook (git check) fired 4 times** in the coordinating session; none in the repo | pages |

**Changed: 14** (attribution, total cost, top cost, survey session in scope, model mix, open PR, branches ahead 7 to 8,
HANDOFF sections, owner turns, first-reply and turn waits, gap p90, word-count inflation, hooks) plus 2 additions (default-branch
commits, branches without PR). **Confirmed unchanged: 13.**

## Unknowns settled

1. Attribution of the 40 branch-matched sessions: **settled**, all 40 are on the project repository.
2. Cost of the 39 unqueried sessions: **settled**, total above. Token fields exist on only 2 of the 49 records (the rest give cost only).
3. The 8 blocked July questions: **mostly settled**. Every one of the 8 branches was merged by the owner through a PR (07-08 to 07-19);
   one merge (#12) was reverted (#13). The drip question was resolved in a later hand-over (07-19). The sessions still show
   "blocked" only because they were never archived: an archive, not 8 questions.
4. Database variables: **partly**. A Vercel project linked to the repository exists; reading its variable names was refused by the
   permission classifier, so ownership stays unknown (owner, or a permitted read of the project's env names).
5. Still open: decisions made in claude.ai chats; whether sessions from 07-25 to 11-19 touch the repo under unrelated titles
   (only `get_session` on each would close it).

Also missed by the first pass: one published artifact (a live mockup) appears on 2 sessions, a channel to the owner not listed; the
survey session committed and pushed the kit into the project (2 commits) before the survey ran, despite "change nothing".

## Corrections to the auto-import draft

Workstreams (8 to 9):
- `entretien`: **stale**. Ingestion is fully closed (bridges 27/27, modes 27/27); production lot 1 exists. Next: owner's look at pilot
  lessons and the 2 audit points "to nuance", then series production.
- `landing`: **wrong** ("none open"). Its hand-over lists ordered next waves, first the owner's verdict on the phone cinematics.
- **missing** `arbres` (3D tree gallery / canopy, own hand-over 07-18, 2 sessions, $204): blocked on the owner's reference-photo choice.
- `revamp-heure-bleue`, `resultats`, `serre`, `soil-glsl`, `persistance`, `mobile-adaptation`: consistent with the repository.

Owner-waiting items (5 to 7):
- "Confirm or close the 8 blocked questions": **replace** with "archive 8 sessions whose work was merged".
- Unmerged branches: add **open PR #4** (merge or close).
- **Add**: landing phone verdict; tree reference-photo choice; Entretien pilot lessons and 2 audit points.
- v4 revamp captures, soil-lab next wave, database variables: kept.
