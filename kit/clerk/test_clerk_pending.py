#!/usr/bin/env python3
"""kit/clerk/clerk_pending.py on planted comments (part M1f): the clerk tells the orchestrator after 5 min, writes
under his comment 5 min later if still unanswered, once; its own lines and Likes are no answer; a session's
answer (reply_to his doc_id, no thread id, as every session writes it) ends the watch.

    python3 -I kit/clerk/test_clerk_pending.py
"""
import importlib.util, json, os, sys, tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cp", os.path.join(HERE, "clerk_pending.py"))
cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)
fails = 0


def case(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got {got}\n     want {want}"))


HIS = {"doc_id": "h1", "post_id": "P", "from": "owner", "thread_id": "th1", "created_at": "2026-11-16T03:00:10.500Z", "text": "why?"}


def setup(comments, pending="h1 th1 2026-11-16T03:00:10.500Z P B12\n"):
    d = tempfile.mkdtemp()
    open(os.path.join(d, "pending.txt"), "w").write(pending)
    os.makedirs(os.path.join(d, "t", "h1", "comments"))
    for c in comments:
        json.dump(c, open(os.path.join(d, "t", "h1", "comments", c["doc_id"] + ".json"), "w"))
    return d


def at(hhmm):
    return datetime.fromisoformat(f"2026-11-16T{hhmm}:00+00:00").astimezone(timezone.utc)


d = setup([HIS])
case("under 5 min: nothing yet, next check in 3 min (rounded up)", cp.run(d, at("03:03")), ["NEXT 3"])
case("5 min, no answer: tell the orchestrator", cp.run(d, at("03:06")), ["TELL h1 P th1 03:00 B12", "NEXT 5"])
case("the line is marked told", open(os.path.join(d, "pending.txt")).read().split()[-1], "told=2026-11-16T03:06:00Z")
case("under 5 min after telling: nothing", cp.run(d, at("03:09")), ["NEXT 2"])
case("5 min after telling, still none: the clerk writes under his", cp.run(d, at("03:11")), ["WRITE h1 P 03:06"])
case("once: the line is gone", (cp.run(d, at("03:20")), open(os.path.join(d, "pending.txt")).read()), ([], ""))

CLERK = {"doc_id": "k1", "post_id": "P", "from": "clerk", "reply_to": "h1", "created_at": "2026-11-16T03:05:00Z",
         "text": "Not answered yet: the orchestrator has been silent since 03:06."}
PASSED = dict(CLERK, doc_id="k2", **{"from": "orchestrator", "text": "Passed to B12 (03:00 UTC)."})
LIKE = {"doc_id": "l1", "post_id": "P", "from": "orchestrator", "like": True, "comment_id": "h1", "created_at": "2026-11-16T03:04:00Z"}
d = setup([HIS, CLERK, PASSED, LIKE])
case("the clerk's lines and a Like are no answer", cp.run(d, at("03:06")), ["TELL h1 P th1 03:00 B12", "NEXT 5"])

ANS = {"doc_id": "a1", "post_id": "P", "from": "B12", "reply_to": "h1", "created_at": "2026-11-16T03:04:00Z", "text": "Because."}
d = setup([HIS, ANS])
case("a session's reply_to his doc_id (no thread id) answers it", cp.run(d, at("03:06")), ["ANSWERED h1"])
d = setup([HIS, dict(ANS, reply_to=None, created_at="2026-11-16T03:01:00Z")])
case("a later session comment on the post answers it", cp.run(d, at("03:06")), ["ANSWERED h1"])
d = setup([HIS, dict(ANS, reply_to=None, created_at="2026-11-16T02:50:00Z")])
case("an earlier one does not", cp.run(d, at("03:06")), ["TELL h1 P th1 03:00 B12", "NEXT 5"])
d = setup([HIS], pending="h1 th1 2026-11-16T03:00:10.500Z P B12 told=2026-11-16T03:06:00Z\n")
os.rename(os.path.join(d, "t", "h1"), os.path.join(d, "t", "x"))
case("no saved comments: query first, the line stays", cp.run(d, at("03:20")), ["NOSAVE h1", "NEXT 1"])

# CR-M1f-the-watch (1): his time comes from his saved row, not from what the clerk typed (to the minute)
TYPED = "h1 th1 2026-11-16T04:57Z P B12\n"
d = setup([dict(HIS, created_at="2026-11-16T04:57:45.120Z"),
           dict(ANS, reply_to=None, created_at="2026-11-16T04:57:10Z")], pending=TYPED)
case("a session comment 35 s BEFORE his real time is no answer (line typed 04:57Z)", cp.run(d, at("05:03")),
     ["TELL h1 P th1 04:57 B12", "NEXT 5"])
# (2) the line at 10 minutes as promised: NEXT rounds up, no extra minute
d = setup([HIS])
case("NEXT is the minutes left, rounded up (2 min 10 s -> 3)", cp.run(d, at("03:03")), ["NEXT 3"])

# board_cli `comments ... --pending-dir` passed an ISO string as now: TypeError str - datetime, the listener crashed
d = setup([HIS])
case("now as an ISO string works like a datetime", cp.run(d, "2026-11-16T03:06:00Z"), ["TELL h1 P th1 03:00 B12", "NEXT 5"])

print(f"{'FAIL' if fails else 'ok'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
