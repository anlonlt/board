"""What an agent writes on the board is checked by the script that writes it: lanes, questions, autonomy, completion;
and a role hands over before a compaction on every runtime.

Run via: python3 -m unittest tests.test_post_gate -v
"""
import importlib.util
import json
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
                mock.patch.object(kit, "git_copy_lines", return_value=[]), mock.patch.object(kit, "_kit_config", return_value=FULL):
            for role in ("orchestrator", "part"):
                self.assertIn("`full_auto`: do it, don't ask", kit.resume_role("m", role, post="p1"))
            self.assertNotIn("full_auto", kit.resume_role("m", "clerk"))


if __name__ == "__main__":
    unittest.main()
