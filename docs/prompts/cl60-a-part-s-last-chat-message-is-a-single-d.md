Board, part CL60: A part's last chat message is a single dot the first time, not after a refusal. Train `takeover`. Your step's post: `cl60-a-part-s-last-chat-message-is-a-single-d` (it exists already).

Run `python3 -I .kit/kit.py resume --role part --post cl60-a-part-s-last-chat-message-is-a-single-d`: it prints this and how a part works here. If it fails or prints STOP, do not start: do what it says.

## The task
A part's or review's last chat message is a single "·", the first time: the rule reaches it before it writes, not after.

Owner, 2026-11-27: "what is up with all the sessions not respecting the character and . hook? The review and M12, their long posts: completely unacceptable." Measured: M12 (session_01AE1HGigXaY4ApaiTBfLGWF) wrote a 2,900-character closing message, then the Stop hook refused it and it wrote "·". A Stop hook runs after the message is shown: it can only make a retry, never prevent the first one. The SAY IT ONCE context arrives on the owner's prompt and after some tools, but not after the part's last steps (`kit.py done`, `end_session.py`, the message to the orchestrator).

1. Every last-step command (`kit.py done`, `end_session.py`, `kit.py unreached`, the review's comment write, SendMessage / send_message to the orchestrator) returns, through a PostToolUse hook, one line: "Your next and last message is a single ·". Same for the review role.
2. The part and review prompts (resume --role part|review) say it in their "how to report" section, once.
3. Measure on the transcripts of today's parts and reviews: how many first closing messages were longer than "·", before and after; a replayed part must close with "·" on its first try (test on a fixture transcript and the hook's output).

## What this step answers (the owner's words, or a review's)
(none named: the task is the orchestrator's; ask under your post if it is unclear)
