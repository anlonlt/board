Board, part CL9b: Cheap breaks for any work (revert, his words, a blind pair, a sample). Train `cli`, after `CL9`. Your step's post: `cl9b-cheap-breaks-revert-asked-pair-sample`.

## The task
Make `breaks` work for visual and other non-code steps without spending tokens on throwaway art, as the plan he Liked (05:31 under CL9) says. Each step is built with its test written first and seen red.

1. **The made-to-fail version is the old version** (about 0 tokens). `breaks: revert` (or `revert <base>`): `kit.py done` reverses the step's own diff against its base (by default where the branch left the trunk) in the throwaway copy, and reruns `check`, which must fail. A revert with nothing to revert is refused.
2. **His words come with the step** (about 200 tokens, once). The post's `asked` field holds `[{"id": "<comment id>", "quote": "<his words>"}]`, copied at turn 1 from the prompt's "What this step answers". `kit.py done` refuses an `asked` entry that no bar or `not_checked` answers (naming its id), and a quote that is not in that comment's text when the comment is on the board.
3. **A small blind reviewer** (about 3 to 5k tokens per picture pair). `shot`: a command that writes a PNG to `$KIT_SHOT`. `kit.py pair <post>` renders the work as it is and as reverted (step 1), refuses when the two are the same file (the change is not visible), and saves them as A and B in random order with the key kept aside. It prints a short brief: his quoted words and the two pictures, nothing else. `kit.py pair <post> --answer A` scores the reviewer's pick.
4. **A sample, not every claim.** The step review's brief names one bar in five, picked at random (at least one), plus every bar marked `[visual]`. The reviewer cannot know which ones will be picked.
5. **Drop:** hand-made decoys, reference photos, and reviewing claims he did not ask for. Nothing to build.

Bars: each of steps 1 to 4 has a test that was red before and is green after. All suites pass with 0 failed.

## What this step answers (the owner's words)
- `dl1h64cikwxfyjg8rt6n` (02:56): "That sounds awfully token expensive. Any way to do this without wasting tokens creating stuff you'd throw away?"
- His Like (05:31) on the reply `cl9-reply-breaks-cheap`: the go for the plan above.

## How you work
As in `kit/claude-cli/part.template.md`: the post at turn 1, the result in the post's format, `kit.py done` OK, the suites run, save points pushed.
