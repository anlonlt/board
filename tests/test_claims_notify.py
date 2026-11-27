"""Two sessions never take one step; the owner's phone hears once, with links it can open; tests never move the funnel.

- A step's holder is read from what its post already carries: `for` / `working_by` (people), `runner` (who executes it),
  `session_id` (the session on it), and the automatic saves at GitHub, which every platform shares.
- Both boards notify one topic through kit/notify.py.
- `up` under the temp folder or with KIT_NO_FUNNEL leaves Tailscale alone; a board takes back a funnel whose port is dead.

Run via: python3 -m unittest tests.test_claims_notify -v
"""
import http.server
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, kit  # noqa: E402

os.environ["KIT_NO_FUNNEL"] = "1"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


notify = load("claims_notify_rules", "kit/notify.py")
server = load("claims_board_server", "kit/board/board_server.py")
gate = load("claims_post_gate", "kit/board/post_gate.py")


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def running(**kw):
    return dict({"part": "P1", "status": "in progress", "session_id": "session_A", "branch": "p1", "updated_at": ago(3)}, **kw)


class Holder(unittest.TestCase):
    def held(self, p, **kw):
        kw = dict({"me": "owner", "people": {"owner", "alex"}, "alive": set(), "saved": None}, **kw)
        with mock.patch.object(kit, "_launch_traces", return_value=kw.pop("traces", None)):
            return kit.step_holder("P1", p, **kw)

    def test_a_free_step_is_free(self):
        self.assertEqual(self.held({"status": "planned"}), "")
        self.assertEqual(self.held({"status": "stalled", "session_id": "session_A", "updated_at": ago(1)}), "")
        self.assertEqual(self.held({"status": "done", "session_id": "session_A", "updated_at": ago(1)}, saved=2), "")

    def test_a_running_session_holds_it_whatever_the_platform(self):
        self.assertIn("get_session", self.held(running()))
        self.assertIn("3 min ago", self.held(running()))
        self.assertIn("session_A", self.held(running(updated_at=ago(300))))         # quiet is not ended: look first
        self.assertIn("running on this computer", self.held(running(session_id="x-p1"), alive={"x-p1"}))
        self.assertIn("x-p1 is on it", self.held(running(session_id="x-p1"), alive=set()))   # another computer's
        self.assertIn("x-p1 is on it", self.held(running(session_id="x-p1"), alive=None, traces={"found": ["branch"]}))

    def test_one_that_ended_here_is_free_unless_work_is_still_being_saved(self):
        p = running(session_id="x-p1")
        self.assertEqual(self.held(p, traces={"found": ["worktree"]}), "")
        self.assertNotEqual(self.held(p, traces={"found": ["worktree"]}, saved=4), "")
        self.assertEqual(self.held(p, traces={"found": ["worktree"]}, saved=kit.CLAIM_MIN), "")
        self.assertNotEqual(self.held(running(), traces={"found": ["branch"]}), "")          # a web session never ends here

    def test_its_own_session_holds_nothing_against_itself(self):
        self.assertEqual(self.held(running(), session="session_A"), "")
        self.assertNotEqual(self.held(running(), session="session_B"), "")

    def test_a_prepared_launch_holds_ten_minutes(self):
        p = running(session_id="pending: create_session (x)", launched_at=ago(2))
        self.assertIn("--session-id", self.held(p))
        self.assertEqual(self.held(dict(p, launched_at=ago(11))), "")

    def test_saves_at_github_show_a_session_this_board_does_not(self):
        self.assertIn("saved work", self.held({"status": "planned"}, saved=5))
        self.assertEqual(self.held({"status": "planned"}, saved=kit.CLAIM_MIN), "")

    def test_people(self):
        self.assertIn("@alex", self.held({"status": "planned", "for": "alex"}))
        self.assertIn("@alex", self.held({"status": "planned", "assigned_to": "alex"}))     # the page's old name
        self.assertIn("@alex", self.held({"status": "planned", "working_by": "alex"}))
        self.assertEqual(self.held({"status": "planned", "for": "alex", "working_by": "alex"}, me="alex"), "")
        self.assertEqual(self.held({"status": "planned", "for": "designer"}), "")            # a role holds nothing
        self.assertIn("@owner", self.held({"status": "planned", "for": "owner"}, me="alex"))

    def test_another_runners_card_is_not_launched(self):
        for r in ("lmarena", "claude-chrome", "owner"):
            self.assertIn(r, self.held({"status": "planned", "runner": r}))
        for r in ("", "agy", "computer", "cloud", "claude-web"):
            self.assertEqual(self.held({"status": "planned", "runner": r}), "", r)


class Launch(unittest.TestCase):
    def launch(self, post, people=(), **kw):
        wrote = []
        me = {"id": "owner", "collaborators": [{"id": x} for x in people]}

        def req(url, path, *a, **k):
            return me if path == "me" else {"exists": True, "data": dict(post), "version": 1}
        with mock.patch.multiple(kit, _board_request=req, _resolve_board_token=lambda t: None, _prompt_problem=lambda r: None,
                                 _launch_posts=lambda p: {}, circle_gate=lambda *a: None, launch_mark=lambda *a, **k: None,
                                 _local_update=lambda *a: wrote.append(a[3:]) or {}, attached_board=lambda: None,
                                 _alive_sessions=lambda: set(), _saved_mins=lambda b: None):
            return kit.launch("P1", web=True, **kw), wrote

    def test_a_second_launch_of_a_running_step_is_refused_and_writes_nothing(self):
        r, wrote = self.launch(running())
        self.assertFalse(r["ok"])
        self.assertIn("session_A", r["error"])
        self.assertIn("--take", r["error"])
        self.assertFalse(wrote or r.get("create_session"))

    def test_taking_it_over_is_said(self):
        r, wrote = self.launch(running(), take=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["create_session"])

    def test_it_names_who_works_on_it(self):
        r, wrote = self.launch({"part": "P1", "status": "planned"})
        self.assertEqual(wrote[0][1]["working_by"], "owner")
        r, wrote = self.launch({"part": "P1", "status": "planned", "for": "alex"}, people=("alex",))
        self.assertIn("@alex", r["error"])

    def test_the_second_half_passes_and_says_when_another_session_came_between(self):
        r, _ = self.launch(running(session_id="pending: create_session (x)", launched_at=ago(1)), session_id="session_B")
        self.assertTrue(r["ok"] and not r.get("warning"), r)
        r, _ = self.launch(running(), session_id="session_B")
        self.assertIn("session_A", r["warning"])
        r, _ = self.launch(running(), session_id="session_A")
        self.assertFalse(r.get("warning"))

    def test_the_command_line_takes_it(self):
        with mock.patch.object(kit, "launch", return_value={"ok": False, "post": "P1", "error": "x"}) as f, \
                mock.patch.object(sys, "argv", ["kit.py", "launch", "P1", "--json"]):
            kit.main(["launch", "P1", "--json"]) if kit.main.__code__.co_argcount else kit.main()
            self.assertFalse(f.call_args.kwargs["take"])
        with mock.patch.object(kit, "launch", return_value={"ok": False, "post": "P1", "error": "x"}) as f, \
                mock.patch.object(sys, "argv", ["kit.py", "launch", "P1", "--take", "--json"]):
            kit.main(["launch", "P1", "--take", "--json"]) if kit.main.__code__.co_argcount else kit.main()
            self.assertTrue(f.call_args.kwargs["take"])


class LaunchInAClone(unittest.TestCase):
    def test_taking_a_step_over_reaches_the_attached_boards_own_launch(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(d + "/docs/prompts")
            Path(d, "docs/prompts/P1.md").write_text("x")
            cmds = []

            def run(cmd, **k):
                cmds.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, '{"ok": false}', "")
            for take in (True, False):
                with mock.patch.object(kit.subprocess, "run", run), mock.patch.object(kit, "_github_url", return_value=""):
                    kit._launch_in_clone("P1", {"id": "kit"}, {}, d, "docs/prompts/P1.md", "", "b", lambda *a, **k: None, {}, [],
                                         None, None, True, False, take)
                self.assertEqual("--take" in cmds[-1], take)


class Saves(unittest.TestCase):
    def test_the_age_of_a_save_at_the_remote(self):
        with tempfile.TemporaryDirectory() as d:
            g = lambda *a, **k: subprocess.run(["git", "-C", k.get("cwd", d + "/w")] + list(a), check=True, capture_output=True)  # noqa: E731
            subprocess.run(["git", "init", "-q", "--bare", d + "/o"], check=True)
            subprocess.run(["git", "init", "-q", d + "/w"], check=True)
            env = {"GIT_COMMITTER_DATE": ago(12), "GIT_AUTHOR_DATE": ago(12)}
            with mock.patch.dict(os.environ, env):
                g("-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "--allow-empty", "-m", "x")
            g("remote", "add", "origin", d + "/o")
            g("push", "-q", "origin", "HEAD:refs/heads/wip/p1")
            g("update-ref", "-d", "refs/remotes/origin/wip/p1")            # another clone: it never saw that save
            with mock.patch.object(kit, "ROOT", d + "/w"):
                self.assertIn(kit._saved_mins("p1"), (11, 12, 13))
                self.assertIsNone(kit._saved_mins("none"))


class PartStart(unittest.TestCase):
    def lines(self, p, session="", web=False, saved=None):
        env = {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_9"} if web else {"CLAUDE_CODE_REMOTE_SESSION_ID": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(kit, "_saved_mins", return_value=saved):
            return "\n".join(kit.held_lines("P1", p, session))

    def test_another_platforms_live_session_is_said_first(self):
        self.assertIn("session_A", self.lines(running()))                                   # a computer, agy too (no id)
        self.assertIn("x-p1", self.lines(running(session_id="x-p1"), web=True))
        self.assertEqual(self.lines(running(updated_at=ago(50))), "")
        self.assertIn("2 min ago", self.lines(running(updated_at=ago(50)), saved=2))

    def test_a_hand_over_on_one_platform_is_silent(self):
        self.assertEqual(self.lines(running(), "session_B", web=True), "")
        self.assertEqual(self.lines(running(session_id="x-p1"), "0f-uuid"), "")
        self.assertEqual(self.lines(running(session_id="pending: x"), "0f-uuid"), "")
        self.assertEqual(self.lines(running(status="planned")), "")


class Person(unittest.TestCase):
    def test_the_menu_and_the_agents_queue_use_one_field(self):
        js = (ROOT / "kit/board/shim.js").read_text(encoding="utf-8")
        self.assertIn('data: { for: sel.value, assignee: "", assigned_to: "" }', js)
        self.assertNotIn("data: { assigned_to:", js)
        self.assertEqual(kit.post_person({"assigned_to": "alex"}), "alex")
        self.assertEqual(kit.post_person({"for": "", "assignee": "", "assigned_to": ""}), "")

    def test_a_finished_post_is_nobodys_current_work(self):
        new, _, err = gate.check_post("P1", {"status": "done"}, {"column": "info", "status": "in progress", "working_by": "alex"})
        self.assertFalse(err)
        self.assertEqual(new["working_by"], "")
        new, _, _ = gate.check_post("P1", {"body": "x"}, {"column": "info", "status": "in progress", "working_by": "alex"})
        self.assertNotIn("working_by", new)


CFG = {"url": "http://x/t", "detail": False, "project": "Demo", "lang": "en", "artifact": "https://claude.ai/artifact/A",
       "board_url": "http://127.0.0.1:8765", "funnel_url": "", "replies": True}
FUNNEL = {"Web": {"m.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}, "AllowFunnel": {"m.ts.net:443": True}}


class Rules(unittest.TestCase):
    def ev(self, d, prev=None, coll="posts", did="Q1", **cfg):
        return notify.event(dict(CFG, **cfg), coll, did, d, prev)

    def test_a_post_that_enters_his_lanes(self):
        self.assertEqual(self.ev({"column": "question"})["kind"], "question")
        self.assertEqual(self.ev({"column": "todo"}, prev="info")["kind"], "todo")
        for d, prev, did in (({"column": "question"}, "question", "Q1"), ({"column": "info"}, None, "Q1"),
                             ({"column": "todo"}, None, "_x"), ({"column": "todo", "from": "owner"}, None, "Q1"),
                             ({"column": "todo", "removed": True}, None, "Q1"), ({"column": "todo", "updated_at": ago(70)}, None, "Q1")):
            self.assertIsNone(self.ev(d, prev, did=did), (d, prev, did))
        self.assertIsNotNone(self.ev({"column": "todo", "updated_at": ago(50)}))
        self.assertIsNotNone(self.ev({"column": "todo", "created_at": ago(5000), "updated_at": ago(1)}))     # an old post that moves

    def test_an_answer_but_never_a_parts_done(self):
        c = {"post_id": "P1", "from": "S1", "text": "here", "reply_to": "c0"}
        self.assertEqual(self.ev(c, coll="comments")["post"], "P1")
        self.assertIsNone(self.ev(c, coll="comments", replies=False))                       # explicitly turned off
        self.assertIsNone(self.ev({k: v for k, v in c.items() if k != "reply_to"}, coll="comments"))
        for who in ("owner", "clerk", "viewer", ""):
            self.assertIsNone(self.ev(dict(c, **{"from": who}), coll="comments"))
        # full_auto still notifies on replies and on non-reply messages in Ask the orchestrator (_orchestrator)
        self.assertTrue(notify.settings(".", {"kit": {"autonomy": {"level": "full_auto"}}})["replies"])
        self.assertFalse(notify.settings(".", {"kit": {"notify_replies": False}})["replies"])
        om = {"post_id": "_orchestrator", "from": "orchestrator", "text": "status update"}
        ev_om = self.ev(om, coll="comments")
        self.assertIsNotNone(ev_om)
        self.assertEqual((ev_om["post"], ev_om["kind"]), ("_orchestrator", "message"))
        self.assertIsNone(self.ev(om, coll="comments", replies=False))
        for who in ("owner", "clerk", "viewer", ""):
            self.assertIsNone(self.ev(dict(om, **{"from": who}), coll="comments"))
        self.assertIsNone(self.ev(dict(om, like=True), coll="comments"))

    def test_attached_boards_and_one_id_on_both_boards(self):
        e = self.ev({"column": "todo"}, coll="repos/kit/posts")
        self.assertEqual((e["repo"], e["title"]), ("kit", "Demo/kit: To do"))
        self.assertEqual(self.ev({"column": "todo"}, coll="repos/demo/posts")["seq"], self.ev({"column": "todo"})["seq"])
        self.assertNotEqual(e["seq"], self.ev({"column": "todo"})["seq"])
        self.assertNotEqual(self.ev({"column": "question"})["seq"], self.ev({"column": "todo"})["seq"])
        self.assertRegex(e["seq"], r"^[a-z0-9]{20,40}$")
        self.assertIsNone(self.ev({"column": "todo"}, coll="repos/kit/kit_docs"))

    def test_no_post_text_unless_asked(self):
        d = {"column": "question", "title": "Secret", "body": "private"}
        self.assertNotIn("Secret", json.dumps(self.ev(d)))
        self.assertIn("Secret", self.ev(d, detail=True)["body"])

    def test_links_open_where_it_was_written_then_the_other_board(self):
        with mock.patch.object(notify, "funnel_status", return_value=FUNNEL):
            tap, more = notify.links(CFG, "Q1", port=8765)
            self.assertEqual(tap, "https://m.ts.net/#/post/Q1")
            self.assertEqual(more, [("Claude board", "https://claude.ai/artifact/A#/post/Q1")])
            tap, more = notify.links(CFG, "Q1", port=8766)                                   # the funnel is another board's
            self.assertEqual((tap, more), ("https://claude.ai/artifact/A#/post/Q1", []))
            tap, more = notify.links(dict(CFG, funnel_url="https://m.ts.net"), "Q1")         # from the web
            self.assertEqual(tap, "https://claude.ai/artifact/A#/post/Q1")
            self.assertEqual(more, [("Local board", "https://m.ts.net/#/post/Q1")])
            tap, more = notify.links(dict(CFG, artifact=""), "Q1", port=8766)
            self.assertEqual((tap, more), ("", []))                                          # never a loopback address
            self.assertIn("?repo=kit#/post/Q1", notify.links(CFG, "Q1", port=8765, repo="kit")[0])
            self.assertEqual(notify.links(CFG, "_orchestrator", port=8765, disc="d123")[0],
                             "https://m.ts.net/#/post/_orchestrator?disc=d123")

    def test_the_funnel_address(self):
        self.assertEqual(notify.funnel_target(FUNNEL), ("https://m.ts.net", 8765))
        self.assertIsNone(notify.funnel_target(FUNNEL, 8766))
        self.assertIsNone(notify.funnel_target(dict(FUNNEL, AllowFunnel={"m.ts.net:443": False})))                # the tailnet only
        self.assertIsNone(notify.funnel_target({}))
        other = json.loads(json.dumps(FUNNEL).replace("http://127.0.0.1:8765", "http://10.0.0.9:80"))
        self.assertIsNone(notify.funnel_target(other))

    def test_where_the_topic_comes_from(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ):
            os.environ.pop("KIT_NOTIFY_URL", None)
            self.assertEqual(notify.topic(d, {"notify_url": "https://n/config"}), "https://n/config")
            os.makedirs(d + "/.kit/board_data")
            Path(notify.topic_file(d)).write_text("https://n/local\n")
            self.assertEqual(notify.topic(d, {"notify_url": "https://n/config"}), "https://n/local")
            Path(notify.topic_file(d)).write_text("\n")
            self.assertEqual(notify.topic(d, {"notify_url": "https://n/config"}), "")        # turned off here
            os.environ["KIT_NOTIFY_URL"] = "https://n/env"
            self.assertEqual(notify.topic(d, {}), "https://n/env")
            os.environ["KIT_NOTIFY_URL"] = "not a url"
            self.assertEqual(notify.topic(d, {}), "")

    def test_once_an_hour_on_a_machine_and_again_after_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(notify.claim(d, "a"))
            self.assertFalse(notify.claim(d, "a"))
            self.assertTrue(notify.claim(d, "a", always=True))
            notify.forget(d, "a")
            self.assertTrue(notify.claim(d, "a"))


class Ntfy:
    """A fake ntfy: what it received, and the answer it gives."""
    def __init__(self, code=200):
        me = self
        self.got, self.code = [], code

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                me.got.append(({k.title(): v for k, v in self.headers.items()}, self.rfile.read(n).decode()))
                self.send_response(me.code)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/topic" % self.srv.server_port

    def wait(self, n, t=60.0):   # until n came (a busy computer is slower, not wrong); then a moment for one too many
        end = time.time() + t
        while len(self.got) < n and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.35)                     # one too many would come now
        return len(self.got)


class BothBoards(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(self.d + "/.kit")
        self.n = Ntfy()
        self.addCleanup(self.n.srv.shutdown)
        self.config(self.n.url)
        shutil.copy(str(ROOT / "kit/notify.py"), self.d + "/.kit/notify.py")
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        for k in ("KIT_NOTIFY_URL", "KIT_FUNNEL_URL", "CLAUDE_PROJECT_DIR"):
            os.environ.pop(k, None)
        os.environ["KIT_PROJECT_DIR"] = self.d
        self.srv, self.store = server.start_server(self.d, port=0)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        f = mock.patch.object(server.NOTIFY, "funnel_status", return_value={
            "Web": {"m.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:%d" % self.srv.server_address[1]}}}},
            "AllowFunnel": {"m.ts.net:443": True}})
        f.start()
        self.addCleanup(f.stop)

    def config(self, url, **k):
        json.dump({"project": "Demo", "board_url": "http://127.0.0.1:8766",
                   "kit": dict({"notify_url": url, "artifact_url": "https://claude.ai/artifact/A"}, **k)}, open(self.d + "/.kit/config.json", "w"))

    def call(self, method, path, body):
        q = urllib.request.Request("http://127.0.0.1:%d/_api/%s" % (self.srv.server_address[1], path), data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json", "X-Kit-Request": "1"}, method=method)
        return json.load(urllib.request.urlopen(q))

    def hook(self, ti):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.d)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "kit/hooks/kit_notify.py")], cwd=self.d, env=env, capture_output=True, text=True,
                           input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "ArtifactData", "cwd": self.d, "tool_input": ti}))
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout)

    Q = {"title": "Secret", "column": "question", "body": "x"}

    def test_the_local_board_says_it_once_with_links_a_phone_opens(self):
        self.call("PUT", "db/posts/Q1", {"data": self.Q})
        self.assertEqual(self.n.wait(1), 1)
        h = self.n.got[0][0]
        self.assertEqual(h["Click"], "https://m.ts.net/#/post/Q1")
        self.assertEqual(h["Actions"], "view, Claude board, https://claude.ai/artifact/A#/post/Q1")
        self.assertTrue(h["X-Sequence-Id"].startswith("kit"))
        self.assertNotIn("Secret", str(self.n.got))
        self.call("PUT", "db/posts/Q1", {"data": dict(self.Q, body="y")})                     # still waiting: not said again
        self.call("PATCH", "db/posts/Q1", {"data": {"column": "question"}})
        self.call("POST", "db/batch", {"writes": [{"op": "set", "collection": "posts", "doc_id": "Q1", "data": dict(self.Q, body="z")}]})
        self.assertEqual(self.n.wait(2, 0.5), 1)
        self.call("PATCH", "db/posts/Q1", {"data": {"column": "done"}})
        self.call("PATCH", "db/posts/Q1", {"data": {"column": "question"}})                   # asked again: said again
        self.assertEqual(self.n.wait(2), 2)

    def test_a_copy_from_the_other_board_is_silent_and_a_batch_written_here_is_not(self):
        w = [{"op": "set", "collection": "posts", "doc_id": "Q2", "data": self.Q}]
        self.call("POST", "db/batch", {"writes": w, "sync": True})
        self.assertEqual(self.n.wait(1, 0.5), 0)
        self.call("POST", "db/batch", {"writes": [dict(w[0], doc_id="Q3")]})
        self.assertEqual(self.n.wait(1), 1)

    def test_every_copy_the_kit_makes_says_so(self):
        src = (ROOT / "scripts/kit.py").read_text(encoding="utf-8")
        calls = [m.group(0) for m in re.finditer(r'"db/batch", \{"writes".*?token=tok(?:en)?\)', src, re.S)]
        plain = [c for c in calls if '"sync": True' not in c]
        self.assertEqual(len(calls) - len(plain), 6)                # CL45: artifact reconcile copies git in, silent
        self.assertEqual(len(plain), 2)                            # a receipt, and this machine's own writes
        self.assertTrue(any("kit-receipts" in c for c in plain))

    def test_the_artifact_hook_by_the_same_rules(self):
        self.hook({"action": "set", "collection": "posts", "doc_id": "Q4", "data": self.Q})
        self.assertEqual(self.n.wait(1), 1)
        h = self.n.got[0][0]
        self.assertEqual(h["Click"], "https://claude.ai/artifact/A#/post/Q4")
        self.hook({"action": "set", "collection": "posts", "doc_id": "Q4", "data": self.Q})   # the same again
        self.hook({"action": "set", "collection": "posts", "doc_id": "I1", "data": {"column": "info"}})
        self.hook({"action": "set", "collection": "comments", "doc_id": "c", "data": {"post_id": "Q4", "from": "P1", "text": "done"}})
        self.assertEqual(self.n.wait(2, 0.4), 1)
        self.hook({"action": "set", "collection": "repos/kit/posts", "doc_id": "Q4", "data": self.Q})
        self.assertEqual(self.n.wait(2), 2)

    def test_written_here_then_pushed_to_the_artifact_is_one(self):
        self.call("PUT", "db/posts/Q5", {"data": self.Q})
        self.assertEqual(self.n.wait(1), 1)
        self.hook({"action": "batch", "writes": [{"op": "set", "collection": "posts", "doc_id": "Q5", "data": self.Q}]})
        self.assertEqual(self.n.wait(2, 0.4), 1)

    def test_a_post_the_last_sync_saw_in_its_lane_is_silent(self):
        b = Path(self.d, ".kit", "run", "artifact", "base", "posts")
        b.mkdir(parents=True)
        (b / "Q6.json").write_text(json.dumps({"column": "question"}))
        (b / "Q7.json").write_text(json.dumps({"column": "info"}))
        self.hook({"action": "set", "collection": "posts", "doc_id": "Q6", "data": self.Q})
        self.hook({"action": "set", "collection": "repos/demo/posts", "doc_id": "Q6", "data": self.Q})   # its own board, under its prefix
        self.assertEqual(self.n.wait(1, 0.4), 0)
        self.hook({"action": "set", "collection": "posts", "doc_id": "Q7", "data": self.Q})
        self.assertEqual(self.n.wait(1), 1)

    def test_one_that_did_not_leave_is_told_and_can_be_sent_later(self):
        self.n.code = 500
        out = self.hook({"action": "set", "collection": "posts", "doc_id": "Q8", "data": self.Q})
        self.assertIn("did not leave (HTTP 500)", out["hookSpecificOutput"]["additionalContext"])
        self.n.code = 200
        self.assertEqual(self.hook({"action": "set", "collection": "posts", "doc_id": "Q8", "data": self.Q}), {})
        self.assertEqual(self.n.wait(2), 2)

    def test_the_share_panel_keeps_the_topic_out_of_the_committed_config(self):
        self.config("")
        u = self.n.url + "-secret"
        self.call("POST", "notify", {"notify_url": u, "notify_detail": True, "test": True})
        self.assertNotIn("secret", Path(self.d, ".kit", "config.json").read_text())
        self.assertTrue(json.load(open(self.d + "/.kit/config.json"))["kit"]["notify_detail"])
        self.assertEqual(Path(notify.topic_file(self.d)).read_text().strip(), u)
        self.assertEqual(os.stat(notify.topic_file(self.d)).st_mode & 0o77, 0)
        self.assertEqual(self.n.got[0][0]["Click"], "https://m.ts.net/")

    def test_orchestrator_reply_links_directly_to_its_discussion(self):
        self.call("PUT", "db/comments/c-owner-1", {"data": {
            "post_id": "_orchestrator", "from": "owner", "text": "question",
            "thread_id": "thr_disc1", "discussion": "disc-42"}})
        self.call("PUT", "db/comments/c-reply-1", {"data": {
            "post_id": "_orchestrator", "from": "orchestrator", "text": "answer",
            "reply_to": "thr_disc1"}})
        self.assertEqual(self.n.wait(1), 1)
        self.assertEqual(self.n.got[0][0]["Click"], "https://m.ts.net/#/post/_orchestrator?disc=disc-42")


class Funnel(unittest.TestCase):
    def status(self, port):
        return json.loads(json.dumps(FUNNEL).replace("8765", str(port)))

    def test_a_board_takes_back_a_dead_funnel_and_leaves_a_live_one(self):
        live = Ntfy()
        self.addCleanup(live.srv.shutdown)
        n = server.NOTIFY
        with mock.patch.object(n, "funnel_status", return_value=self.status(1)):
            self.assertTrue(server._funnel_lost(8765))
        with mock.patch.object(n, "funnel_status", return_value=self.status(live.srv.server_port)):
            self.assertFalse(server._funnel_lost(8765))
        with mock.patch.object(n, "funnel_status", return_value=self.status(1)):
            self.assertFalse(server._funnel_lost(1))                                         # already its own
        with mock.patch.object(n, "funnel_status", return_value={}):
            self.assertFalse(server._funnel_lost(8765))                                      # none: never opened by itself

    def test_the_running_board_does_it(self):
        took = []
        with mock.patch.object(server, "_funnel_allowed", return_value=True), mock.patch.object(server, "_take_funnel", took.append), \
                mock.patch.object(server, "_funnel_lost", side_effect=[False, True] + [False] * 999):
            server._ensure_funnel_async(8765, "/x", every=0.02)
            end = time.time() + 60                    # until it took it twice (at start, then once lost), not a fixed 0.4 s
            while len(took) < 2 and time.time() < end:
                time.sleep(0.02)
            time.sleep(0.1)                           # and no third time
        self.assertEqual(took, [8765, 8765])
        with mock.patch.object(server, "_funnel_allowed", return_value=False), mock.patch.object(server, "_take_funnel", took.append):
            server._ensure_funnel_async(8765, "/x", every=0.02)
            time.sleep(0.1)
        self.assertEqual(len(took), 2)

    def test_the_server_never_moves_it_from_a_test(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(shutil, "which", return_value="/bin/true"):
            self.assertFalse(server._funnel_allowed(d))                                      # under the temp folder
            with mock.patch.dict(os.environ, {"KIT_NO_FUNNEL": "1"}):
                self.assertFalse(server._funnel_allowed("/nowhere"))
            with mock.patch.dict(os.environ, {"KIT_NO_FUNNEL": ""}):
                self.assertTrue(server._funnel_allowed("/nowhere"))

    def up(self, project, **env):
        """Run kit/up with a fake tailscale, curl and agy; the tailscale calls it made."""
        kd, bd = os.path.join(project, ".kit"), os.path.join(project, "bin")
        os.makedirs(kd + "/board_data")
        os.makedirs(bd)
        log = os.path.join(project, "ts.log")
        shutil.copy(str(ROOT / "kit/up"), kd + "/up")
        Path(kd, "board_server.py").write_text("")
        Path(kd, "board_data", "token").write_text("t\n")
        for name, body in (("tailscale", 'echo "$@" >> "%s"' % log), ("curl", "echo '{\"auth\": true}'"), ("agy", "true")):
            Path(bd, name).write_text("#!/bin/sh\n%s\n" % body)
            os.chmod(os.path.join(bd, name), 0o755)
        e = {k: v for k, v in os.environ.items() if k not in ("KIT_NO_FUNNEL", "KIT_BOARD_TOKEN", "TMPDIR")}
        e.update(env, PATH=bd + os.pathsep + os.environ["PATH"], HOME=project, KIT_BOARD_PORT="1")
        r = subprocess.run(["bash", kd + "/up", "--agy"], cwd=project, env=e, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        return [x for x in (Path(log).read_text().splitlines() if os.path.exists(log) else []) if x.startswith("funnel --bg")]

    def test_up_never_moves_it_from_a_test_and_does_from_a_project(self):
        home = tempfile.mkdtemp(dir=str(ROOT / ".kit" / "run") if os.path.isdir(ROOT / ".kit" / "run") else str(ROOT))
        self.addCleanup(shutil.rmtree, home, True)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for d in ("a", "b"):
            os.makedirs(os.path.join(home, d))
        self.assertEqual(self.up(os.path.join(home, "a"), KIT_NO_FUNNEL="1"), [])
        if not home.startswith(("/tmp/", tempfile.gettempdir() + os.sep)):                   # a checkout there is a test run
            self.assertEqual(self.up(os.path.join(home, "b")), ["funnel --bg 1"])            # the fake tailscale
        self.assertEqual(self.up(tmp), [])


if __name__ == "__main__":
    unittest.main()
