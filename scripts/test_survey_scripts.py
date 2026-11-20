#!/usr/bin/env python3
"""what_fills.py and mirror_holes.py follow the config (anlonlt/board, BD9). Their numbers on Harmonize's own data
were checked equal to the originals' (anlonlt/harmonize scripts/review/WF1-*: O47's 11 pages, the board of 01:07).

    python3 -I scripts/test_survey_scripts.py
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def case(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "\n     " + str(detail)[:500]))


def run(script, *a):
    return subprocess.run([sys.executable, "-I", os.path.join(HERE, script)] + list(a), capture_output=True, text=True).stdout


t = tempfile.mkdtemp()
# a dump where the author field is "who" and the owner is "boss"
for coll, docs in (("posts", {"P1": {"body": "x" * 2000, "updated_at": "2026-01-01T00:00:00Z"}, "P2": {"body": "y"}}),
                   ("comments", {"c1": {"who": "boss", "post_id": "P1", "created_at": "2026-01-01T10:00:00Z", "seen": True},
                                 "c2": {"who": "bot", "post_id": "P1", "created_at": "2026-01-01T09:58:00Z", "reply_to": "c1", "text": "a"}})):
    os.makedirs(os.path.join(t, "dump", coll))
    for i, v in docs.items():
        json.dump(v, open(os.path.join(t, "dump", coll, i + ".json"), "w"))
cfg = os.path.join(t, "cfg.json")
json.dump({"fields": {"author": "who", "owner": "boss", "reviewer": "bot", "later_flags": ["seen"]},
           "shell_buckets": [{"name": "Bash: my log", "all": ["MYLOG"]}]}, open(cfg, "w"))
out = run("mirror_holes.py", os.path.join(t, "dump"), "--config", cfg)
case("mirror_holes: a reply stamped before what it answers, found under renamed fields", "1 of 1 replies (worst 120 s)" in out, out)
case("mirror_holes: the owner's later-written flag, by its configured name", "(seen): 1 of 1" in out, out)
case("mirror_holes: posts with an update stamp", "posts with an updated_at: 1 of 2" in out, out)

# one transcript page: two requests, a Bash call that matches the configured bucket
os.makedirs(os.path.join(t, "pages"))
ev = [{"created_at": "2026-01-01T00:00:01Z", "assistant": {"request_id": "r1", "message": {"usage": {"cache_read_input_tokens": 1000},
       "content": [{"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "cat MYLOG.md"}}]}, "uuid": "a1"}},
      {"created_at": "2026-01-01T00:00:02Z", "user": {"message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "content": "z" * 5000}]}, "uuid": "b1"}},
      {"created_at": "2026-01-01T00:00:03Z", "assistant": {"request_id": "r2", "message": {"usage": {"cache_read_input_tokens": 3000},
       "content": [{"type": "text", "text": "done"}]}, "uuid": "a2"}}]
open(os.path.join(t, "pages", "page-001.txt"), "w").write("wrapper\n" + json.dumps({"ccr": {"data": ev}}))
out = run("what_fills.py", os.path.join(t, "pages"), "--config", cfg)
case("what_fills: a shell call lands in the configured bucket", "IN  result: Bash: my log" in out, out)
out = run("what_fills.py", os.path.join(t, "pages"))
case("what_fills: without the config it is 'Bash: other'", "IN  result: Bash: other" in out, out)
print("%d failed" % fails)
sys.exit(1 if fails else 0)
