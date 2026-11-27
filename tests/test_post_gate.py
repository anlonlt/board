"""What an agent writes on the board is checked by the script that writes it: lanes, questions, autonomy, completion;
and a role hands over before a compaction on every runtime.

Run via: python3 -m unittest tests.test_post_gate -v
"""
import importlib.util
import json
import time
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, TOKEN, api_req, kit  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = load("pg_t", "kit/board/post_gate.py")
FULL = {"kit": {"autonomy": {"level": "full_auto"}}}
BAL = {"kit": {"autonomy": {"level": "balanced"}}}
BOOT = ("import sys; sys.path.insert(0, sys.argv[1])\nfrom board_server import start_server\n"
        "s, store = start_server(sys.argv[2], port=0, token='%s')\nprint(s.server_port, flush=True)\ns.serve_forever()\n" % TOKEN)
STEP = {"title": "s", "part": "S1", "train": "t", "column": "info", "status": "in progress", "body": "b", "verdict": "It adds.", "bars": ["4 of 4 sums"],
        "artifacts": ["calc.py"], "not_checked": ["floats"], "check": "python3 -c \"import calc; assert calc.add(2, 2) == 4\"",
        "breaks": "python3 -c \"p='calc.py'; open(p,'w').write(open(p).read().replace('a + b', 'a - b'))\"", "handoff": "h"}


class Lanes(unittest.TestCase):
    def lane(self, new, old=None, cfg=BAL):
        d, notes, err = g.check_post("p", new, old, cfg)
        self.assertIsNone(err)
        return d.get("column"), notes

    def err(self, new, old=None, cfg=BAL, pid="p"):
        return g.check_post(pid, new, old, cfg)[2]

    def test_the_lane_follows_what_the_post_is(self):
        self.assertEqual(self.lane({"title": "n"})[0], "info")
        col, notes = self.lane({"column": "info", "options": ["A", "B"], "default_option": "A"})
        self.assertEqual(col, "question")
        self.assertIn("info -> question", notes[0])
        self.assertEqual(self.lane({"column": "info", "minutes": 5})[0], "todo")
        self.assertEqual(self.lane({"runner": "owner"})[0], "todo")
        self.assertEqual(self.lane({"column": "done", "options": ["A"], "default_option": "A"})[0], "done")

    def test_a_later_write_never_moves_it_back(self):
        old = {"column": "done", "options": ["A", "B"], "default_option": "A", "minutes": 3}
        self.assertEqual(self.lane({"body": "x"}, old)[0], None)
        self.assertEqual(self.lane({"body": "x", "options": ["A", "B"]}, dict(old, column="info"))[0], None)

    def test_unknown_lane(self):
        self.assertIn("doing", self.err({"column": "doing"}))
        self.assertIsNone(self.err({"column": "doing"}, pid="_dashboard"))            # a card
        self.assertIsNone(self.err({"body": "x"}, {"column": "doing"}))               # an old post is not stuck

    def test_a_question_has_a_default_and_under_full_auto_a_reason(self):
        self.assertIn("default", self.err({"column": "question", "title": "q"}))
        q = {"column": "question", "default_option": "A", "options": ["A", "B"]}
        self.assertIsNone(self.err(q))
        self.assertIn("full_auto", self.err(q, cfg=FULL))
        self.assertIn("full_auto", self.err(dict(q, needs="my feelings"), cfg=FULL))
        for n in g.NEEDS:
            self.assertIsNone(self.err(dict(q, needs=n), cfg=FULL))
        self.assertIsNone(self.err(dict(q, **{"from": "kit compact"}), cfg=FULL))     # an import of his own open points
        self.assertIsNone(self.err({"status": "open"}, q, cfg=FULL))                  # already asked

    def test_autonomy_levels(self):
        self.assertEqual([g.autonomy(c) for c in (FULL, BAL, {}, {"kit": {"autonomy": {"level": "balanced", "carry_on": True}}})],
                         ["full_auto", "balanced", "balanced", "full_auto"])
        text = "\n".join(g.autonomy_lines(FULL))
        for w in g.NEEDS.values():
            self.assertIn(w, text)
        self.assertNotIn("launch what he said yes to", text)


class Threads(unittest.TestCase):
    def test_asking_in_a_thread(self):
        for t in ("A \U0001F44D here launches it too.", "Should I merge them?", "Do you want the pilot first?", "I can plan CL13; tell me which.",
                  "Question: launch one part? Default: yes.", "On your yes I merge the parts.", "A thumbs up is yes", "One \U0001F44D launches it all", "A Like here = go.", "A Like on this comment = go for it", "Like this to go.", "Like this and the next step builds it",
                  "tap \U0001F44D if it looks good", "Confirm before I merge.", "Give me the green light.", "Pick A or B.", "Do it? Want me to?", "Let me know if you want it."):
            self.assertIsNotNone(g.check_comment("c", {"from": "orchestrator", "text": t}), t)

    def test_saying_is_not_asking(self):
        for t in ("Done: 4 of 4. The bar is full width now.", "He asked \"should I merge?\" and the answer is in the post.", "> Should I?\nYes: merged.",
                  "\U0001F44D", "Relayed your Like on the first offer.", "The test catches the ghost \U0001F44D.", "It is now running on your go.", "Your Like is the go: the step launched.",
                  "Why it failed? The cache.", "The test used to want me to wait.", "He may pick one later.", "`do you want` is the string that broke.", "It waits in your Decisions lane.", "Which is why it broke."):
            self.assertIsNone(g.check_comment("c", {"from": "orchestrator", "text": t}), t)

    def test_where_asking_is_right(self):
        t = {"from": "orchestrator", "text": "Should I?"}
        self.assertIsNone(g.check_comment("welcome-claude-1", t))
        self.assertIsNone(g.check_comment("c", dict(t, kind="review")))
        self.assertIsNone(g.check_comment("c", t, {"column": "question"}))
        self.assertIsNone(g.check_comment("c", {"from": "owner", "text": "Should I?"}))


class Live(unittest.TestCase):
    """The real board_cli.py and kit.py against a real server."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        (self.d / ".kit").mkdir()
        for rel in ("kit/board/board_cli.py", "kit/board/post_gate.py", "kit/board/board_token.py", "scripts/kit.py"):
            if (ROOT / rel).exists():
                shutil.copy(str(ROOT / rel), str(self.d / ".kit" / Path(rel).name))
        self.conf({"kit": {"autonomy": {"level": "full_auto"}, "review": {"auto": "off"}}})
        (self.d / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY"))}
        for a in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1"]):
            subprocess.run(["git", "-C", str(self.d)] + a, check=True, capture_output=True)
        self.srv = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.d / ".kit" / "board_data")],
                                    stdout=subprocess.PIPE, text=True, env=env)
        self.addCleanup(lambda: (self.srv.kill(), self.srv.wait(), self.srv.stdout.close()))
        self.url = "http://127.0.0.1:%s" % self.srv.stdout.readline().strip()
        self.env = dict(env, KIT_BOARD_URL=self.url, KIT_BOARD_TOKEN=TOKEN, KIT_NO_LAUNCH="1")
        # a train is one goal, written before its first step (tests/test_trains.py)
        self.assertEqual(self.run_("kit.py", "trains", "new", "t", "--goal", "the fixture's steps").returncode, 0)

    def conf(self, c):
        (self.d / ".kit" / "config.json").write_text(json.dumps(dict({"project": "D"}, **c)))

    def run_(self, script, *a):
        return subprocess.run([sys.executable, "-I", str(self.d / ".kit" / script)] + list(a), capture_output=True, text=True, env=self.env,
                              cwd=str(self.d), timeout=120)

    def cli(self, *a):
        return self.run_("board_cli.py", *a)

    def put(self, coll, i, d):                                        # the page's way: only a step's closing is gated there
        api_req(self.url, "db/%s/%s" % (coll, i), {"data": d}, token=TOKEN, method="PUT", gate="")

    def get(self, i):
        return json.loads(self.cli("get", "posts", i).stdout)["data"]

    def test_the_cli_refuses_and_moves(self):
        r = self.cli("set", "posts", "a", "--data", json.dumps({"title": "x", "column": "doing"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED", r.stderr)
        self.assertEqual(self.cli("set", "posts", "b", "--data", json.dumps({"title": "x", "column": "info", "minutes": 3})).returncode, 0)
        self.assertEqual(self.get("b")["column"], "todo")
        r = self.cli("batch", "--data", json.dumps([{"op": "set", "collection": "posts", "doc_id": "c", "data": {"column": "doing"}}]))
        self.assertEqual(r.returncode, 2)

    def test_the_cli_refuses_a_question_in_a_thread(self):
        self.put("posts", "n", {"title": "news", "column": "info"})
        self.put("comments", "his", {"post_id": "n", "from": "owner", "text": "hm", "created_at": "2026-01-01T00:00:00Z"})
        r = self.cli("reply", "n", "--comment-id", "his", "--from", "orchestrator", "--text", "Should I merge it?")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(self.cli("get", "comments", "his").stdout)["data"].get("read"))   # nothing half done
        self.assertEqual(self.cli("reply", "n", "--comment-id", "his", "--from", "orchestrator", "--text", "Merged.").returncode, 0)
        self.put("posts", "q", {"title": "q", "column": "question"})
        self.assertEqual(self.cli("reply", "q", "--from", "orchestrator", "--text", "Should I add C?").returncode, 0)

    def test_the_cli_takes_no_owner_and_no_proof(self):
        self.put("posts", "n", {"title": "n", "column": "info"})
        c = json.dumps({"post_id": "n", "from": "owner", "text": "yes, do it"})
        self.assertEqual(self.cli("set", "comments", "fake", "--data", c).returncode, 2)
        self.assertEqual(self.cli("set", "comments", "f2", "--data", json.dumps({"post_id": "n", "text": "Should I?"})).returncode, 2)
        self.assertEqual(self.cli("update", "posts", "n", "--data", '{"proof":{"key":"x"}}').returncode, 2)

    def raw(self, path, body, method="PUT", **hdrs):
        import urllib.error
        import urllib.request
        r = urllib.request.Request(self.url + "/_api/" + path, method=method, data=json.dumps(body).encode(),
                                   headers=dict({"X-Kit-Token": TOKEN, "Content-Type": "application/json"}, **hdrs))
        try:
            with urllib.request.urlopen(r, timeout=20) as res:
                return res.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_the_server_refuses_a_step_completed_past_the_cli(self):
        self.put("posts", "s9", STEP)
        done = dict(STEP, status="complete")
        forged = dict(done, proof={"key": g.proof_key(done)})
        new = dict(forged, body="other")
        self.assertEqual(self.raw("db/posts/s9", {"data": done}), 403)
        self.assertEqual(self.raw("db/posts/s9", {"data": forged}), 403)
        self.assertEqual(self.raw("db/posts/fresh", {"data": forged}), 403)
        self.assertEqual(self.raw("doc/posts/s9", {"data": {"status": "complete"}}, "PATCH"), 403)
        self.assertEqual(self.raw("db/posts/s9", {"data": {"proof": {"key": "x"}}}, "PATCH"), 403)
        self.assertEqual(self.raw("db/posts/s9/str_replace", {"field": "status", "old_str": "in progress", "new_str": "complete"}, "POST"), 403)
        self.assertEqual(self.raw("db/batch", {"writes": [{"op": "update", "collection": "posts", "doc_id": "s9", "data": {"status": "done"}}]}, "POST"), 403)
        two = [{"op": "set", "collection": "posts", "doc_id": "s8", "data": dict(STEP, status="planned")},
               {"op": "update", "collection": "posts", "doc_id": "s8", "data": {"status": "complete"}}]
        self.assertEqual(self.raw("db/batch", two, "POST"), 403)
        self.assertEqual(self.get("s9")["status"], "in progress")
        self.assertNotIn("proof", self.get("s9"))
        # what is not a step closing goes through: his page's writes, a copy from git, an import
        self.assertEqual(self.raw("db/posts/s9", {"data": {"column": "done", "note": "his"}}, "PATCH"), 200)
        self.assertEqual(self.raw("db/batch", {"writes": [{"op": "set", "collection": "posts", "doc_id": "old", "data": new}], "sync": True}, "POST"), 200)
        self.assertEqual(self.raw("db/posts/old", {"data": {"title": "t2"}}, "PATCH"), 200)     # complete already
        (self.d / "b.json").write_text(json.dumps({"posts": [{"id": "imp", "data": done}], "comments": []}))
        self.assertEqual(self.cli("import", str(self.d / "b.json")).returncode, 0)

    def test_the_form_is_filled_once_and_sent_by_done(self):
        self.put("posts", "f1", {"title": "f", "part": "F1", "train": "t", "column": "info", "status": "planned", "body": "b", "sources": ["his1"]})
        self.put("comments", "his1", {"post_id": "_orchestrator", "from": "owner", "text": "L'addition, \"vite\" s'il te pla\u00eet", "created_at": "2026-01-01T00:00:00Z"})
        r = self.run_("kit.py", "form", "f1", "--doing", "Je commence par l'addition")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.get("f1")["status"], self.get("f1")["body"]), ("in progress", "Je commence par l'addition"))
        f = self.d / ".kit" / "run" / "form" / "f1.md"
        text = f.read_text()
        self.assertIn("- his1: L'addition, \"vite\" s'il te pla\u00eet", text)                         # his words, as he wrote them
        r = self.run_("kit.py", "done", "f1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count("still has <fill"), 8, r.stdout)                             # each blank, in one answer
        self.assertNotIn("verdict", self.get("f1"))                                                  # nothing half sent
        import re
        say = {"lead": "L'addition marche, c'est pr\u00eat.", "verdict": "Elle additionne.", "bars": "- 4 of 4 sums (asked his1)\n- 2 of 2 signs\n  read twice",
               "not_checked": "- floats", "check": "```sh\n%s\n```" % STEP["check"], "breaks": "`%s`" % STEP["breaks"].replace("`", ""), "handoff": "h",
               "body": "### How\n## not a field\nIt's `calc.py`, d'accord."}
        for k, v in say.items():
            text = re.sub(r"(?ms)^## %s\n.*?(?=^## \w+$|\Z)" % k, lambda m: "## %s\n%s\n\n" % (k, v), text, count=1)
        f.write_text(text)
        (self.d / "calc.py").write_text((self.d / "calc.py").read_text() + "# touched\n")
        subprocess.run(["git", "-C", str(self.d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "c"], check=True)
        r = self.run_("kit.py", "done", "f1")
        p = self.get("f1")
        self.assertEqual((p["verdict"], p["bars"], p["check"], p["breaks"]), ("Elle additionne.", ["4 of 4 sums (asked his1)", "2 of 2 signs\n  read twice"], STEP["check"], STEP["breaks"]))
        self.assertEqual(p["asked"], [{"id": "his1", "quote": "L'addition, \"vite\" s'il te pla\u00eet"}])
        self.assertTrue(p["body"].startswith("L'addition marche, c'est pr\u00eat.\n\n### How\n## not a field"), p["body"])
        self.assertIn("OK: f1", r.stdout, r.stdout + r.stderr)
        self.cli("update", "posts", "f1", "--data", '{"handoff":"changed on the board"}')               # an unchanged form is not sent again
        self.run_("kit.py", "done", "f1")
        self.assertEqual(self.get("f1")["handoff"], "changed on the board")

    def task(self, sid, name, ti, tid=None, event="PostToolUse", opener="Your prompt is docs/prompts/w1.md: run `x`", role="part", **extra):
        tf = self.d / ("tr-%s.jsonl" % sid)
        tf.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": opener}}) + "\n")
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_task_train.py")], capture_output=True, text=True, cwd=str(self.d), timeout=60,
                           env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.d), **({"KIT_ROLE": role} if role else {})),
                           input=json.dumps(dict({"hook_event_name": event, "session_id": sid, "cwd": str(self.d), "transcript_path": str(tf), "tool_name": name,
                                                  "tool_input": ti, "tool_response": {"task": {"id": tid}} if tid else {}}, **extra)))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)                                # nothing said back, nothing refused

    def test_a_parts_task_list_is_the_pieces_of_its_step(self):
        self.put("posts", "w1", {"title": "w", "part": "W1", "train": "t", "column": "info", "status": "in progress", "body": "b", "task": "Do w", "session_id": "part-a"})
        rows = lambda: [(x["title"], x["status"]) for x in self.get("w1").get("subtasks") or []]
        for i, t in enumerate(("Read d'abord", "Build", "Check"), 1):
            self.task("s-a", "TaskCreate", {"subject": t}, str(i))
        self.task("s-a", "TaskUpdate", {"taskId": "1", "status": "in_progress"})
        self.task("s-a", "TaskUpdate", {"taskId": "1", "status": "completed"})
        self.task("s-a", "TaskUpdate", {"taskId": "2", "status": "in_progress", "subject": "Build it"})
        self.assertEqual(rows(), [("Read d'abord", "done"), ("Build it", "doing"), ("Check", "open")])
        self.assertEqual(self.run_("kit.py", "step", "open").stdout.count("Build"), 0)                # no step, no post of its own
        # its session dies; another, with an empty list, goes on from there
        out = self.run_("kit.py", "resume", "--role", "part", "--post", "w1").stdout
        self.assertIn("## Its pieces (1 of 3 done)\n- [x] 1. Read d'abord\n- [~] 2. Build it\n- [ ] 3. Check", out)
        self.assertIn("## The task\nDo w", out)
        self.task("s-b", "TaskCreate", {"subject": "Build it"}, "1")                                  # another list's number 1
        self.task("s-b", "TaskCreate", {"subject": "Check"}, "2")
        self.assertEqual(rows(), [("Read d'abord", "done"), ("Build it", "doing"), ("Check", "open")])
        self.task("s-b", "TaskUpdate", {"taskId": "1", "status": "completed"})
        self.task("s-c", None, {}, event="TaskCompleted", task_id="9", task_subject="Check")           # the same list, its record elsewhere
        self.assertEqual(rows(), [("Read d'abord", "done"), ("Build it", "done"), ("Check", "done")])
        self.task("s-b", "TaskUpdate", {"taskId": "2", "status": "deleted"})
        self.assertEqual(len(rows()), 2)

    def test_a_numbered_task_is_its_pieces_and_done_waits_for_them(self):
        k = load("kit_pieces", "scripts/kit.py")
        task = "Goal: sums.\n\n1. **Add** the `plus` key. It is blue.\n2. Test it\n3. Say so, e.g. on the page\n\nHolds: none."
        self.assertEqual([(x["n"], x["title"]) for x in k.task_pieces(task)], [(1, "Add the plus key"), (2, "Test it"), (3, "Say so, e.g. on the page")])
        for prose in ("1. one only", "2. two\n3. three", "1. a\n1. b", "- a\n- b", "```\n1. a\n2. b\n```", "  1. a\n  2. b", ""):
            self.assertEqual(k.task_pieces(prose), [], prose)
        r = json.loads(self.run_("kit.py", "step", "new", "--train", "t", "--part", "N1", "--title", "sums", "--task", task).stdout.splitlines()[-1])
        pid = r["post"]
        self.assertEqual(r["pieces"], 3)
        rows = lambda: [(x["n"], x["status"]) for x in self.get(pid)["subtasks"]]
        # the same task list in other words is the part's own; the same words are the same line
        self.task("n", "TaskCreate", {"subject": "my own plan"}, "1", opener="Your prompt is docs/prompts/%s.md: run `x`" % pid)
        self.task("n", "TaskCreate", {"subject": "Test it"}, "2", opener="Your prompt is docs/prompts/%s.md: run `x`" % pid)
        self.task("n", "TaskUpdate", {"taskId": "2", "status": "completed"}, opener="Your prompt is docs/prompts/%s.md: run `x`" % pid)
        self.assertEqual(rows(), [(1, "open"), (2, "done"), (3, "open")])
        self.assertIn("pieces still open: 1. Add the plus key; 3. Say so", self.run_("kit.py", "done", pid).stdout)
        self.assertIn("--why", self.run_("kit.py", "sub", pid, "3", "--status", "drop").stdout)          # the task's: never dropped in silence
        self.assertEqual(self.run_("kit.py", "sub", pid, "9", "--status", "done").returncode, 1)
        self.assertEqual(json.loads(self.run_("kit.py", "sub", pid, "3", "--status", "drop", "--why", "no page").stdout), {"post": pid, "done": 1, "of": 2})
        self.run_("kit.py", "sub", pid, "1", "2", "--status", "done")
        self.assertEqual(rows(), [(1, "done"), (2, "done"), (3, "dropped")])
        self.assertEqual(self.get(pid)["subtasks"][2]["why"], "no page")
        self.assertNotIn("pieces still open", self.run_("kit.py", "done", pid).stdout)
        self.run_("kit.py", "sub", pid, "One", "more")                                                   # a new one takes the next number
        self.assertEqual((self.get(pid)["subtasks"][3]["title"], rows()[3]), ("One more", (4, "open")))

    def test_a_piece_done_that_not_checked_names_is_refused(self):
        # CL42: piece 3 asked for "another repository, another environment, network off"; not_checked listed exactly those, piece done
        k = load("kit_unchecked", "scripts/kit.py")
        task = ("1. List the connectors with their number of tools\n2. Change one thing at a time (another repository, another environment, "
                "network off) and say which decides\n3. Answer in this post")
        p = {"id": "c42", "task": task, "subtasks": [dict(x, status="done") for x in k.task_pieces(task)],
             "not_checked": ["network off, another repository, another environment (stored settings)", "exact tokens for each connector"]}
        why = k.pieces_unchecked(p)
        self.assertEqual(len(why), 1, why)
        self.assertTrue(why[0].startswith("piece 2 is marked done") and "--status drop --why" in why[0], why)
        p["subtasks"][1]["status"] = "dropped"                                     # said not done, with its reason: passes
        self.assertEqual(k.pieces_unchecked(p), [])
        p["subtasks"][1]["status"] = "done"
        p["not_checked"] = ["the page on a phone", "nothing else"]                 # a limit that is no piece: passes
        self.assertEqual(k.pieces_unchecked(p), [])

    def test_what_a_part_does_now_reaches_its_step_with_no_model_call(self):
        task = "Maps.\n\n1. docs/a.md : the sky\n2. Write `docs/b.md`\n3. Check them"
        pid = json.loads(self.run_("kit.py", "step", "new", "--train", "t", "--part", "M1", "--title", "maps", "--task", task).stdout.splitlines()[-1])["post"]
        self.run_("kit.py", "form", pid)                                                          # turn 1: the files of its pieces, for the hook
        self.assertEqual(json.loads((self.d / ".kit" / "run" / "pieces" / (pid + ".json")).read_text()), [{"n": 1, "files": ["docs/a.md"]}, {"n": 2, "files": ["docs/b.md"]}])
        hook = str(ROOT / "kit" / "hooks" / "kit_activity.py")

        def call(payload, role="part", post=pid, says=""):
            env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.d), KIT_ROLE=role, KIT_POST=post)
            r = subprocess.run([sys.executable, "-I", hook], input=json.dumps(dict({"session_id": "s1", "cwd": str(self.d)}, **payload)),
                               capture_output=True, text=True, env=env, cwd=str(self.d), timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)                                          # nothing refused
            if says:
                self.assertIn(says, json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
            else:
                self.assertEqual(r.stdout, "")                                                   # nothing said

        def seen(want):
            for _ in range(50):
                r = self.cli("get", "activity", pid)
                d = json.loads(r.stdout).get("data") if r.returncode == 0 and r.stdout.strip() else None
                if d and all(d.get(k) == v for k, v in want.items()):
                    return d
                time.sleep(0.2)
            self.fail("activity/%s never became %r: %s" % (pid, want, d))

        (self.d / "docs").mkdir()
        time.sleep(0.05)
        (self.d / "docs" / "b.md").write_text("b")                                               # written by a shell command
        call({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "cat > docs/b.md", "description": "Write the second map"}})
        seen({"actions": 1, "what": "Write the second map", "state": "working", "pieces": [2]})
        call({"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(self.d / "x.py")}})
        time.sleep(1)
        self.assertEqual(seen({"actions": 1})["what"], "Write the second map")                    # one write every 15 s at most
        call({"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(self.d / "docs" / "a.md")}})
        seen({"actions": 3, "what": "Editing a.md", "pieces": [1]})                                # a piece it moves to is said at once
        (self.d / "src").mkdir()
        call({"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(self.d / "src"), "tool_input": {"command": "cat > docs/c.md <<'EOF'\nx\nEOF"}})
        self.assertFalse((self.d / "src" / ".kit").exists())                                       # a shell gone into src/ is still this project
        call({"hook_event_name": "Stop"})
        seen({"state": "stopped", "actions": 4, "what": "Writing c.md"})                            # no description: plain words from the command
        # the loop breaker: the same call 8 times in a row, said once on the 8th, in any session; a different call starts again
        same = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "o1", "tool_input": {"command": "npm test"}}
        for i in range(1, 10):
            call(same, role="orchestrator", says="8 times in a row" if i == 8 else "")
        call(dict(same, tool_input={"command": "npm run lint"}), role="orchestrator")
        for i in range(1, 9):
            call(same, says="8 times in a row" if i == 8 else "")
        seen({"loop": 8})
        # before a call: what it starts shows while it runs (a long test run)
        call({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "p2", "tool_input": {"command": "pytest -q"}})
        seen({"what": "Running the tests"})
        import importlib.util
        spec = importlib.util.spec_from_file_location("kit_activity", hook)
        ka = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ka)
        for cmd, said in (("TOKEN=s3cret curl -H 'Authorization: x' https://a", "Running curl"), ("cd src; for n in 1 2; do sed -n \"${n}p\" app.jsx; done", "Reading app.jsx"),
                          ("python3 -I .kit/kit.py done m1", "Checking its result"), ("npm test", "Running the tests"), ("git log -3", "Checking git")):
            self.assertEqual(ka.bash_words(cmd), said, cmd)
        self.assertEqual([x["status"] for x in self.get(pid)["subtasks"]], ["open"] * 3)           # the part ticks a piece done, never the hook
        call({"hook_event_name": "Stop", "session_id": "s2"}, role="review")
        call({"hook_event_name": "Stop", "session_id": "s3"}, post="")
        self.assertFalse((self.d / ".kit" / "run" / "activity" / "s2.json").exists())               # a review is not a part
        docs = api_req(self.url, "db/activity", token=TOKEN, gate="").get("docs") or []
        self.assertEqual([x["id"] for x in docs], [pid])                                           # nothing from the others

    def test_what_an_agy_part_does_now_reaches_its_step_with_no_model_call(self):
        task = "Maps.\n\n1. docs/a.md : the sky\n2. Write `docs/b.md`\n3. Check them"
        pid = json.loads(self.run_("kit.py", "step", "new", "--train", "t", "--part", "M2", "--title", "agy maps", "--task", task).stdout.splitlines()[-1])["post"]
        self.run_("kit.py", "form", pid)
        hook = str(ROOT / "kit" / "hooks" / "kit_activity.py")
        # Real agy subagent transcript (SYSTEM_MESSAGE with [Message] ... content=Your prompt is <abs>/docs/prompts/<pid>.md)
        tf_sub = self.d / "agy-subagent-transcript.jsonl"
        tf_sub.write_text(json.dumps({
            "step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE",
            "content": "The following is a <SYSTEM_MESSAGE>.\n\n<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-26T03:09:04Z sender=orch priority=MESSAGE_PRIORITY_HIGH content=Your prompt is %s/docs/prompts/%s.md: read it and do it.\n</SYSTEM_MESSAGE>" % (self.d, pid)
        }) + "\n")
        # Real agy -p transcript (USER_INPUT with <USER_REQUEST> wrapper)
        tf_cli = self.d / "agy-cli-transcript.jsonl"
        tf_cli.write_text(json.dumps({
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
            "content": "<USER_REQUEST>\nYour prompt is docs/prompts/%s.md: read it and do it.\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nnow\n</ADDITIONAL_METADATA>" % pid
        }) + "\n")
        tf_cr = self.d / "agy-cr-transcript.jsonl"
        tf_cr.write_text(json.dumps({
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
            "content": "<USER_REQUEST>\nYour prompt is docs/prompts/CR-%s.md: read it.\n</USER_REQUEST>" % pid
        }) + "\n")

        def call(payload, role="", says=""):
            env = dict(self.env)
            if role:
                env["KIT_ROLE"] = role
            base = {"conversationId": "agy-c1", "cwd": str(self.d), "workspacePaths": [str(self.d)], "transcriptPath": str(tf_sub)}
            base.update(payload)
            r = subprocess.run([sys.executable, "-I", hook], input=json.dumps(base),
                               capture_output=True, text=True, env=env, cwd=str(self.d), timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            if says:
                steps = json.loads(r.stdout)["injectSteps"]
                self.assertIn(says, steps[0]["systemMessage"]["systemMessage"])
            else:
                self.assertEqual(r.stdout, "")

        def seen(want):
            d = None
            for _ in range(50):
                r = self.cli("get", "activity", pid)
                d = json.loads(r.stdout).get("data") if r.returncode == 0 and r.stdout.strip() else None
                if d and all(d.get(k) == v for k, v in want.items()):
                    return d
                time.sleep(0.2)
            self.fail("activity/%s never became %r: %s" % (pid, want, d))

        (self.d / "docs").mkdir()
        time.sleep(0.05)
        (self.d / "docs" / "b.md").write_text("b")
        # 1. PostToolUse run_command with toolSummary (discovered via agy subagent transcript, no KIT_POST/KIT_ROLE)
        call({"hookEventName": "PostToolUse", "toolCall": {"id": "c1", "name": "run_command",
              "args": {"CommandLine": "cat > docs/b.md", "toolSummary": "Write the second map", "WaitMsBeforeAsync": 5000}}})
        seen({"actions": 1, "what": "Write the second map", "state": "working", "pieces": [2]})
        # 2. Rate-limited (< 15s) on unrelated edit_file
        call({"hookEventName": "PostToolUse", "toolCall": {"id": "c2", "name": "edit_file", "args": {"Path": str(self.d / "x.py")}}})
        time.sleep(0.6)
        self.assertEqual(seen({"actions": 1})["what"], "Write the second map")
        # 3. Piece move via write_file (Path) flushes immediately
        call({"hookEventName": "PostToolUse", "toolCall": {"id": "c3", "name": "write_file", "args": {"Path": str(self.d / "docs" / "a.md")}}})
        seen({"actions": 3, "what": "Writing a.md", "pieces": [1]})
        # 4. Subfolder cwd + no toolSummary falls back to bash_words, never creates src/.kit
        (self.d / "src").mkdir()
        call({"hookEventName": "PostToolUse", "cwd": str(self.d / "src"),
              "toolCall": {"id": "c4", "name": "run_command", "args": {"CommandLine": "cat > docs/c.md <<'EOF'\nx\nEOF"}}})
        self.assertFalse((self.d / "src" / ".kit").exists())
        # 5. Stop writes immediately
        call({"hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL", "fullyIdle": True})
        seen({"state": "stopped", "actions": 4, "what": "Writing c.md"})
        # 6. Loop breaker on agy: 8 identical calls -> PostToolUse records & writes loop:8 (stdout empty); PostInvocation injects once
        same = {"hookEventName": "PostToolUse", "conversationId": "agy-o1", "transcriptPath": "",
                "toolCall": {"id": "c", "name": "run_command", "args": {"CommandLine": "npm test", "WaitMsBeforeAsync": 5000}}}
        post_inv = {"hookEventName": "PostInvocation", "conversationId": "agy-o1", "transcriptPath": ""}
        for i in range(1, 10):
            call(same, role="orchestrator")
            call(post_inv, role="orchestrator", says="8 times in a row" if i == 8 else "")
        call(post_inv, role="orchestrator")                                                        # already consumed: silent
        # Different call resets; part session writes loop:8 on the board and delivers once even if 8th & 9th run before PostInvocation
        call(dict(same, toolCall={"id": "d", "name": "run_command", "args": {"CommandLine": "npm run lint"}}), role="orchestrator")
        part_same = {"hookEventName": "PostToolUse", "conversationId": "agy-p2", "transcriptPath": str(tf_cli),
                     "toolCall": {"id": "c", "name": "run_command", "args": {"CommandLine": "npm test"}}}
        for i in range(1, 10):
            call(part_same)
        seen({"loop": 8})
        call({"hookEventName": "PostInvocation", "conversationId": "agy-p2", "transcriptPath": str(tf_cli)}, says="8 times in a row")
        call({"hookEventName": "PostInvocation", "conversationId": "agy-p2", "transcriptPath": str(tf_cli)})
        # 7. PreToolUse shows what starts before it finishes (discovered via lastUserInput when no transcript file exists)
        call({"hookEventName": "PreToolUse", "conversationId": "agy-p3", "transcriptPath": "",
              "lastUserInput": "Your prompt is docs/prompts/%s.md: go" % pid,
              "toolCall": {"id": "p3", "name": "run_command", "args": {"CommandLine": "pytest -q"}}})
        seen({"what": "Running the tests"})
        # 8. Non-parts (review role, CR- prompt, plain user prompt) stay quiet and create no activity doc or state file
        self.assertEqual([x["status"] for x in self.get(pid)["subtasks"]], ["open"] * 3)
        call({"hookEventName": "Stop", "conversationId": "agy-rev"}, role="review")
        call({"hookEventName": "Stop", "conversationId": "agy-cr", "transcriptPath": str(tf_cr)})
        call({"hookEventName": "Stop", "conversationId": "agy-human", "transcriptPath": "", "lastUserInput": "Look at docs/prompts/%s.md" % pid})
        for cid in ("agy-rev", "agy-cr", "agy-human"):
            self.assertFalse((self.d / ".kit" / "run" / "activity" / (cid + ".json")).exists())
        docs = api_req(self.url, "db/activity", token=TOKEN, gate="").get("docs") or []
        self.assertEqual([x["id"] for x in docs], [pid])

    def test_review_cl41_names_and_numbers(self):
        k = load("kit_cl41", "scripts/kit.py")
        self.assertEqual(k.plain_notes({"verdict": "The board works in Node.js and on a phone.", "body": "x"}), [])  # a product's name
        self.assertTrue(k.plain_notes({"verdict": "See kit.py for it.", "body": "x"}))                               # a file still is refused
        ns = [x["n"] for x in k.pieces_of({"subtasks": [{"title": "a"}, {"title": "b", "n": 1}]})]
        self.assertEqual(len(set(ns)), 2, ns)                                                                     # never two "1."

    def test_every_check_off_once_he_accepted_it(self):
        k = load("kit_mode", "scripts/kit.py")
        with tempfile.TemporaryDirectory() as home:
            old = {x: os.environ.get(x) for x in ("CLAUDE_CONFIG_DIR", "KIT_CLAUDE_PERMISSION_MODE")}
            os.environ.pop("KIT_CLAUDE_PERMISSION_MODE", None)
            os.environ["CLAUDE_CONFIG_DIR"] = home
            real = getattr(os, "geteuid", None)
            os.geteuid = lambda: 1000                                                              # a web container runs as root
            try:
                self.assertEqual(k.permission_mode({}), "auto")                                   # not accepted by him: auto
                Path(home, "settings.json").write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
                self.assertEqual(k.permission_mode({}), "bypassPermissions")
                os.geteuid = lambda: 0
                self.assertEqual(k.permission_mode({}), "auto")                                   # Claude Code refuses bypass as root
                os.geteuid = lambda: 1000
                self.assertEqual(k.permission_mode({"claude_permission_mode": "auto!"}), "auto")   # his choice, kept
                self.assertEqual(k.permission_mode({"claude_permission_mode": "acceptEdits"}), "acceptEdits")
            finally:
                if real is None:
                    del os.geteuid
                else:
                    os.geteuid = real
                for x, v in old.items():
                    os.environ.pop(x, None) if v is None else os.environ.__setitem__(x, v)

    def test_only_a_part_writes_pieces(self):
        self.put("posts", "w1", {"title": "w", "part": "W1", "train": "t", "column": "info", "status": "in progress", "body": "b"})
        for sid, role, opener in (("c", "clerk", "Your prompt is docs/prompts/w1.md"), ("r", "review", "Your prompt is docs/prompts/w1.md"),
                                  ("o", "", "Look at docs/prompts/w1.md and tell me"), ("cr", "", "Your prompt is docs/prompts/CR-w1.md: read it")):
            self.task(sid, "TaskCreate", {"subject": "x"}, "1", opener=opener, role=role)
        self.assertNotIn("subtasks", self.get("w1"))
        self.task("p", "TaskCreate", {"subject": "x"}, "1", role="")                                  # a part by its first prompt (the web sets no KIT_ROLE)
        self.assertEqual(len(self.get("w1")["subtasks"]), 1)
        web = 'You are part W1 (runner claude-web). Run `python3 -I .kit/kit.py resume --role part --post w1` and do it.'
        self.task("wp", "TaskCreate", {"subject": "y"}, "1", opener=web, role="")                     # CL41: a web part in the orchestrator's words
        self.assertEqual(len(self.get("w1")["subtasks"]), 2)
        self.task("wq", "TaskCreate", {"subject": "z"}, "1", opener="Tell me about --role part --post w1", role="")
        self.assertEqual(len(self.get("w1")["subtasks"]), 2)                                         # not a part's opening: nothing

    def test_what_he_reads_first_is_for_him(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        k = load("kit_plain", "scripts/kit.py")
        self.assertEqual(k.plain_notes({"verdict": "The button is darker.", "body": "You see it on the home page.\n\n### How\n`a.py` 0a45d26f `b.py`"}), [])
        n = k.plain_notes({"verdict": "All 8 gaps closed in kit.py and install.py (0a45d26)", "body": "`tests/x.py` then `kit.py done`"})
        self.assertEqual([x.split(":")[0] for x in n], ["verdict", "lead"])
        one = lambda **p: len(k.plain_notes(p))                                                          # noqa: E731
        self.assertEqual(one(verdict="Run `npm test` to see it"), 1)
        self.assertEqual(one(verdict="See docs/plan.md"), 1)
        for p in ({"verdict": "plan.md is shorter", "asked": [{"id": "c", "quote": "make Plan.md shorter"}]}, {"verdict": "ok", "title": "fix a.py", "body": "a.py works"},
                  {"verdict": "ok", "body": "[The page](out/page.html) is ready: https://x.org/a.html ![it](shots/a1b2c3d.png) `take 1.wav`"},
                  {"verdict": "3 of 4 takes, 2.5 s, 1920x1080, v2.0, and 250000 rows"}):
            self.assertEqual(k.plain_notes(p), [], p)
        self.put("posts", "tech", dict(STEP, verdict="Closed in calc.py"))
        self.assertIn("fix: verdict: he reads this first, and it names calc.py", self.run_("kit.py", "done", "tech").stdout)

    def test_ask_writes_the_question_whole(self):
        base = ["ask", "--title", "Which one", "--option", "A", "--option", "B", "--default", "A", "--deadline", "2027-01-01"]
        self.assertIn("full_auto", self.run_("kit.py", *base).stdout)
        r = json.loads(self.run_("kit.py", *(base + ["--needs", "taste"])).stdout)
        p = self.get(r["post"])
        self.assertEqual((p["column"], p["options"], p["default_option"], p["needs"]), ("question", ["A", "B"], "A", "taste"))
        self.assertIn("error", self.run_("kit.py", "ask", "--title", "t", "--option", "A", "--default", "A", "--deadline", "d").stdout)
        self.assertIn("error", self.run_("kit.py", *base[:-2]).stdout)

    def test_complete_only_after_done_and_his_words(self):
        self.put("posts", "s1", dict(STEP, sources=["his1"]))
        self.put("comments", "his1", {"post_id": "_orchestrator", "from": "owner", "text": "make it add", "created_at": "2026-01-01T00:00:00Z"})
        self.put("comments", "his2", {"post_id": "s1", "from": "owner", "text": "and zero?", "created_at": "2026-01-02T00:00:00Z"})
        self.assertIn("kit.py done s1", self.run_("kit.py", "step", "complete", "s1").stdout)
        self.assertEqual(self.cli("update", "posts", "s1", "--data", '{"status":"complete"}').returncode, 2)      # no way round
        out = self.run_("kit.py", "done", "s1").stdout
        self.assertIn("his1 started this step", out)
        self.assertIn("his2 has no answer", out)
        self.cli("update", "posts", "s1", "--data", json.dumps({"asked": [{"id": "his1", "quote": "make it add"}], "bars": ["4 of 4 sums (asked his1)"]}))
        self.cli("reply", "s1", "--comment-id", "his2", "--from", "S1", "--text", "Zero adds too.")
        r = self.run_("kit.py", "done", "s1")
        self.assertIn("OK: s1", r.stdout)
        self.cli("update", "posts", "s1", "--data", '{"bars":["5 of 5 sums (asked his1)"]}')                       # changed since
        self.assertIn("the post changed", self.run_("kit.py", "step", "complete", "s1").stdout)
        self.run_("kit.py", "done", "s1")
        self.put("comments", "his3", {"post_id": "s1", "from": "owner", "text": "wait", "created_at": "2026-01-03T00:00:00Z"})
        self.assertIn("his3", self.run_("kit.py", "step", "complete", "s1").stdout)
        self.cli("reply", "s1", "--comment-id", "his3", "--from", "S1", "--text", "Waiting is over: done.")
        self.assertIn('"ok": true', self.run_("kit.py", "step", "complete", "s1").stdout)
        self.assertEqual(self.get("s1")["status"], "complete")

    def test_no_check_no_done(self):
        self.put("posts", "s2", dict(STEP, check="", breaks=""))
        self.assertIn("check: missing", self.run_("kit.py", "done", "s2").stdout)

    def test_a_break_that_does_not_bite_gives_no_proof(self):
        self.put("posts", "s3", dict(STEP, check="python3 -c \"import calc\""))
        self.assertIn("still passes after breaks", self.run_("kit.py", "done", "s3").stdout)
        self.assertNotIn("proof", self.get("s3"))

    def test_a_missing_gate_completes_nothing(self):
        self.put("posts", "s4", STEP)
        os.remove(str(self.d / ".kit" / "post_gate.py"))
        self.assertIn("post_gate.py is missing", self.run_("kit.py", "step", "complete", "s4").stdout)


class NoWayRound(unittest.TestCase):
    """The holes an adversarial reading found (2026-11-24), each shut."""
    P = {"train": "t", "part": "P", "verdict": "v", "check": "x", "breaks": "y", "body": "b"}

    def err(self, new, old=None, **k):
        return g.check_post("p", new, old if old is not None else self.P, {}, **k)[2]

    def test_a_comment_with_no_author_is_an_agents(self):
        self.assertIsNotNone(g.check_comment("c", {"text": "Should I merge it?"}))
        self.assertIsNotNone(g.check_comment("c", {"from": "kitty", "text": "Should I merge it?"}))

    def test_a_proof_is_not_written_by_hand(self):
        pr = {"key": g.proof_key(self.P)}
        self.assertIn("never written by hand", self.err({"proof": pr}))
        self.assertIn("never written by hand", self.err({"proof": pr, "status": "complete"}))
        self.assertIsNone(self.err({"proof": pr}, may_prove=True))
        self.assertIsNone(self.err({"proof": pr, "handoff": "h"}, dict(self.P, proof=pr)))           # carried along unchanged

    def test_a_step_does_not_stop_being_one_to_close(self):
        for also in ({"train": ""}, {"part": ""}, {"kind": "issue"}, {"kind": "question", "default_option": "A"}):
            self.assertIn("kit.py done", self.err(dict(also, status="complete")) or "", also)

    def test_a_pending_real_run_is_no_proof(self):
        p = dict(self.P, proof={"key": g.proof_key(self.P), "needs_real": True})
        self.assertFalse(g.proved(p))
        self.assertFalse(g.proved(dict(p, real_project={"project": "x"})))
        done = dict(p, real_project=[{"project": "x", "command": "c", "output": "o"}])
        self.assertTrue(g.proved(done))
        self.assertIn("kit.py done", self.err({"status": "complete", "real_project": None}, done) or "")

    def test_only_the_kits_own_posts_skip_the_question_rules(self):
        q = {"column": "question", "options": ["A", "B"], "default_option": "A"}
        self.assertIsNotNone(g.check_post("q", dict(q, **{"from": "kit x"}), None, FULL)[2])

    def test_a_subagent_is_gated_where_it_writes(self):
        og = load("og_pg", "kit/hooks/kit_orchestrator_guard.py")
        w = lambda **k: og.subagent_verdict(dict({"action": "set", "collection": "comments", "doc_id": "c"}, **k), str(ROOT))      # noqa: E731
        self.assertIn("in a thread", w(data={"from": "a", "text": "Should I?"}))
        self.assertIsNone(w(data={"from": "a", "text": "Done."}))
        self.assertIn("lane", w(collection="repos/x/posts", data={"column": "doing"}))
        self.assertIn("step complete", w(collection="posts", action="update", data={"status": "complete"}))
        self.assertIsNone(w(collection="posts", doc_id="_dashboard", data={"column": "doing"}))
        self.assertIn("in a thread", w(action="batch", writes=[{"op": "set", "collection": "comments", "doc_id": "c", "data": {"text": "Should I?"}}]))
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "x.json")
            Path(f).write_text(json.dumps({"from": "a", "text": "Should I?"}))
            self.assertIn("in a thread", w(file_path=f))
            self.assertIsNone(w(collection="repos/x/comments", file_path=f))                         # a sync's copy of history
            r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / "kit_orchestrator_guard.py")], capture_output=True, text=True,
                               input=json.dumps({"tool_name": "ArtifactData", "agent_id": "s", "cwd": str(ROOT), "tool_input": {
                                   "action": "set", "collection": "comments", "doc_id": "c", "data": {"text": "Should I?"}}}),
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT)))
            self.assertEqual(r.returncode, 2, r.stderr)

    def test_the_kits_inline_launch_passes_its_own_guard(self):
        og = load("og_pg2", "kit/hooks/kit_orchestrator_guard.py")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(kit, "ROOT", d):
            pr = kit._inline_prompt("Your prompt is docs/prompts/x.md: read it and do it.", "docs/prompts/x.md", "A task. " * 90)
            self.assertIn("inline prompt", og.launch_verdict({"prompt": pr}, d))                     # not marked: refused
            kit.launch_mark("x", pr, inline="docs/prompts/x.md")
            self.assertIsNone(og.launch_verdict({"prompt": pr}, d))
            self.assertIn("inline prompt", og.launch_verdict({"prompt": pr + " and more"}, d))       # this very text only
            kit.launch_mark("x", pr)
            self.assertIn("inline prompt", og.launch_verdict({"prompt": pr}, d))


class HookDies(unittest.TestCase):
    def project(self, body, seconds=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, ".kit", "hooks"))
        src = (ROOT / "kit" / "hook.py").read_text()
        if seconds:
            src = src.replace('GUARD, 50),', 'GUARD, %d),' % seconds)
        Path(d, ".kit", "hook.py").write_text(src)
        Path(d, ".kit", "hooks", "kit_task_train.py").write_text(body)
        return d

    def run_(self, d):
        return subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "hook.py"), "cli", "TaskCompleted"], input="{}", capture_output=True,
                              text=True, timeout=60)

    def test_a_crash_never_closes_a_step(self):
        r = self.run_(self.project("raise RuntimeError('boom')"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("boom", r.stderr)

    def test_a_timeout_lets_git_clean_up(self):
        d = self.project("", 1)
        lock = os.path.join(d, "lock")
        Path(d, ".kit", "hooks", "kit_task_train.py").write_text(
            "import signal, sys, time, os\np = %r\nopen(p, 'w').close()\nsignal.signal(signal.SIGTERM, lambda *a: (os.remove(p), sys.exit(1)))\ntime.sleep(30)\n" % lock)
        r = self.run_(d)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(lock))

    def test_one_that_ignores_it_is_killed(self):
        d = self.project("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n", 1)
        t = __import__("time").monotonic()
        self.assertEqual(self.run_(d).returncode, 2)
        self.assertLess(__import__("time").monotonic() - t, 10)


class Words(unittest.TestCase):
    def test_unanswered_under(self):
        own = lambda **k: dict({"from": "owner", "text": "x", "created_at": "2026-01-01T00:00:00Z"}, **k)    # noqa: E731
        ans = lambda **k: dict({"from": "a", "text": "y", "created_at": "2026-01-02T00:00:00Z"}, **k)        # noqa: E731
        left = lambda **c: [i for i, _ in kit.unanswered_under(c)]                                           # noqa: E731
        self.assertEqual(left(a=own()), ["a"])
        self.assertEqual(left(a=own(), b=ans(reply_to="a")), [])
        self.assertEqual(left(a=own(thread_id="t"), b=ans(reply_to="t")), [])
        self.assertEqual(left(a=own(), b=ans(reply_to="zz")), ["a"])                     # another thread
        self.assertEqual(left(a=own(created_at="2026-01-03"), b=ans(reply_to="a")), ["a"])   # he spoke last
        self.assertEqual(left(a=own(like=True), b=own(answer="A"), c=own(text="Answer: A"), d=own(text="\U0001F44D")), [])


class NoCompaction(unittest.TestCase):
    def test_every_runtime_hands_over_at_stop(self):
        hook = load("hook_pg", "kit/hook.py")
        for rt in hook.RUNTIMES:
            self.assertIn("kit_pre_compact.py", [r[2] for r in hook.rows(rt, "Stop")], rt)

    def run_hook(self, name, payload, opener, tokens=0, **env):
        with tempfile.TemporaryDirectory() as d:
            tf = os.path.join(d, "t.jsonl")
            with open(tf, "w") as fh:
                fh.write(json.dumps({"type": "user", "message": {"content": opener}}) + "\n")
                fh.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}) + "\n")
            os.makedirs(os.path.join(d, ".kit"))
            Path(d, ".kit", "config.json").write_text('{"project":"D","kit":{}}')
            e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY"))}
            r = subprocess.run([sys.executable, "-I", str(ROOT / "kit" / "hooks" / name)], capture_output=True, text=True, cwd=d, timeout=60,
                               input=json.dumps(dict(payload, transcript_path=tf, cwd=d, session_id=os.path.basename(d))),
                               env=dict(e, CLAUDE_PROJECT_DIR=d, **env))
            return r.stdout

    def test_a_web_orchestrator_is_told_at_its_limit(self):
        for opener in (load("ho_pg", "kit/handover.py").successor_prompt("orchestrator", 4, cli="web"),
                       "Your prompt is .kit/orchestrator.md: read it and follow it."):
            out = self.run_hook("kit_pre_compact.py", {"hook_event_name": "Stop"}, opener, 400000, CLAUDE_CODE_REMOTE="true")
            self.assertIn("HANDOVER REQUIRED", out)
            self.assertIn("create_session", out)
            self.assertNotIn("`up` opens", out)
        self.assertEqual(self.run_hook("kit_pre_compact.py", {"hook_event_name": "Stop"}, opener, 1000, CLAUDE_CODE_REMOTE="true"), "")
        self.assertEqual(self.run_hook("kit_pre_compact.py", {"hook_event_name": "Stop"}, "Your prompt is docs/prompts/x.md", 400000), "")

    def test_after_a_compaction_the_first_word_is_hand_over(self):
        out = self.run_hook("kit_session_start.py", {"hook_event_name": "SessionStart", "source": "compact"}, "Your prompt is .kit/orchestrator.md")
        self.assertIn("handover.py --role orchestrator", out.splitlines()[0])
        out = self.run_hook("kit_session_start.py", {"hook_event_name": "SessionStart", "source": "compact"}, "Your prompt is docs/prompts/x.md")
        self.assertIn("resume --role part", out.splitlines()[0])
        self.assertNotIn("COMPACTED", self.run_hook("kit_session_start.py", {"hook_event_name": "SessionStart", "source": "startup"}, "hi"))

    def test_no_guide_has_a_start_for_it(self):
        for rel in ("kit/claude/orchestrator.template.md", "kit/claude-cli/orchestrator.template.md", "kit/orchestrator.template.md"):
            start, _ = kit.guide_parts((ROOT / rel).read_text(encoding="utf-8"))
            self.assertFalse([w for w, _ in start if "same" in w], rel)


class Told(unittest.TestCase):
    def test_the_level_is_in_resume(self):
        posts = {"p1": {"part": "P1", "title": "t", "train": "T", "status": "planned"}, "_orchestrator": {"session_id": "o"}}
        with mock.patch.object(kit, "_load_board_docs", return_value=(posts, {})), mock.patch.object(kit, "resume", return_value="R\n"), \
                mock.patch.object(kit, "git_copy_lines", return_value=[]), mock.patch.object(kit, "_kit_config", return_value=FULL), \
                mock.patch.object(kit, "mark_part_branch"):     # CL50: never mark this checkout's .kit/run as part p1's
            for role in ("orchestrator", "part"):
                self.assertIn("`full_auto`: do it, don't ask", kit.resume_role("m", role, post="p1"))
            self.assertNotIn("full_auto", kit.resume_role("m", "clerk"))


if __name__ == "__main__":
    unittest.main()
