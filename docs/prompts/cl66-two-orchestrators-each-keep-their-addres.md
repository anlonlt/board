Board, part CL66: Two orchestrators each keep their address: a takeover never replaces the other one's. Train `takeover`. Your step's post: `cl66-two-orchestrators-each-keep-their-addres` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl66-two-orchestrators-each-keep-their-addres`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
Owner, 2026-11-27 in chat: "Why are you taking over antigravity orchestrator messages?" Earlier (02:31, q-two-orchestrators): two orchestrators can run at once, the agy one handles the parts it took, the web one the others.

Found: `kit.py resume --role orchestrator` "Your start" tells every new orchestrator to write its own id into `_orchestrator.session_id`, so the clerk routes the owner's words to it alone. Web orchestrator 3 did that at 03:26 and took the Antigravity orchestrator's messages until 03:50.

Done when:
1. `_orchestrator` holds one address per live orchestrator (by runtime/device), and the takeover step adds or replaces only its own platform's entry.
2. The clerk sends the owner's words to the orchestrator that owns the post the comment is on (the part's `orchestrator` field, CL49), else to every live orchestrator listed.
3. Resume's "Your start" says so, and a test shows a web takeover leaves the agy entry in place. `python3 scripts/run_all_tests.py` green.

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
