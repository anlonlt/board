"""Trains as the orchestrator's task list: scripts/kit.py (train, resume) and kit/hooks/kit_task_train.py."""
import importlib.util
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rd(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def make_db(path, posts):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, doc_id TEXT NOT NULL, version INTEGER NOT NULL"
                 " DEFAULT 1, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, doc_id))")
    now = datetime.now(timezone.utc).isoformat()
    for pid, p in posts.items():
        conn.execute("INSERT OR REPLACE INTO docs VALUES ('posts', ?, 1, ?, ?)", (pid, json.dumps(p), now))
    conn.commit()
    conn.close()


POSTS = {
    "PL1-a": {"title": "first", "part": "PL1", "train": "planner", "column": "done", "created_at": "2026-01-01",
              "check": "python3 bench.py --n 50", "handoff": "use out/model.bin"},
    "PL2-b": {"title": "second", "part": "PL2", "train": "planner", "column": "info", "status": "in progress",
              "created_at": "2026-01-02", "session_id": "demo-pl2"},
    "PL3-c": {"title": "third", "part": "PL3", "train": "planner", "column": "info", "status": "planned",
              "created_at": "2026-01-03"},
    "OLD1-x": {"title": "finished", "part": "OLD1", "train": "old", "column": "done", "created_at": "2025-12-01"},
}


class TrainAndResume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load("kit_mod", os.path.join(ROOT, "scripts", "kit.py"))
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "board.db")
        make_db(cls.db, POSTS)

    def ctx(self, target, zoom=None):
        return json.loads(self.kit.train_context(self.tmp, target, zoom=zoom, db_path=self.db, as_json=True))

    def test_name_starting_with_p_is_not_stripped(self):
        # lstrip("p-") turned "p-planner" into "lanner" and matched nothing
        self.assertEqual(len(self.ctx("planner")["steps"]), 3)
        self.assertEqual(len(self.ctx("P-planner")["steps"]), 3)

    def test_zoom_before_first_post_does_not_borrow_a_sibling(self):
        z = self.ctx("planner", zoom="PL9")["zoom"]
        self.assertEqual(z["part"], "PL9")
        self.assertIn("no post yet", z["title"])
        self.assertEqual(z["comments"], [])

    def test_check_and_handoff(self):
        steps = {s["part"]: s for s in self.ctx("planner")["steps"]}
        self.assertEqual(steps["PL1"]["check"], "python3 bench.py --n 50")
        txt = self.kit.train_context(self.tmp, "planner", db_path=self.db, level="sections")
        self.assertIn("check: `python3 bench.py --n 50`", txt)
        self.assertIn("*Handoff:* use out/model.bin", txt)

    def test_resume_lists_active_trains_and_live_sessions(self):
        out = self.kit.resume(self.tmp, db_path=self.db, alive={"other-session"})
        self.assertIn("## Train planner (1/3 done)", out)
        self.assertNotIn("Train old", out)  # every step done: not active
        self.assertIn("[~] PL2 `PL2-b` second · in progress · session demo-pl2 (not running)", out)
        self.assertIn("[ ] PL3 `PL3-c` third · planned, not launched", out)
        self.assertIn("Rebuild your task list", out)
        out2 = self.kit.resume(self.tmp, db_path=self.db, alive={"demo-pl2"})
        self.assertIn("session demo-pl2 (running)", out2)
        out_agy = self.kit.resume(self.tmp, db_path=self.db, alive={"demo-pl2"}, cli="agy")
        self.assertIn("Resume in agy", out_agy)
        self.assertIn("manage_subagents", out_agy)
        self.assertIn("kit.py step", out_agy)
        mcp = load("mcp_stdio_t", os.path.join(ROOT, "kit", "board", "mcp_stdio.py"))
        names = {t["name"] for t in mcp.TOOLS}
        self.assertTrue({"board_step_new", "board_step_update", "board_resume", "board_train"} <= names)
        srv = mcp.MCPServer(ROOT)
        # CL30: this repo's board is an Artifact board; resume reads its copy only once loaded (fresh): a copy never
        # loaded is refused (the gate), a loaded one answers
        art = tempfile.mkdtemp()
        old = os.environ.get("KIT_ARTIFACT_DIR")
        os.environ["KIT_ARTIFACT_DIR"] = art
        self.addCleanup(lambda: os.environ.pop("KIT_ARTIFACT_DIR", None) if old is None
                        else os.environ.__setitem__("KIT_ARTIFACT_DIR", old))
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "board_resume", "arguments": {"cli": "agy"}}}
        if self.kit.attached_board():
            stale = srv.handle(call)
            self.assertTrue(stale["result"]["isError"])
            self.assertIn("never loaded", stale["result"]["content"][0]["text"])
        from datetime import datetime, timezone
        with open(os.path.join(art, "state.json"), "w") as fh:
            json.dump({"loaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "versions": {}}, fh)
        resp = srv.handle(call)
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("Resume in agy", resp["result"]["content"][0]["text"])


class TrainDepths(unittest.TestCase):
    """The train at the depth a session needs, and the launch that shows what was already found."""

    @classmethod
    def setUpClass(cls):
        cls.kit = load("kit_mod_depth", os.path.join(ROOT, "scripts", "kit.py"))
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "board.db")
        long = "line of the body. " * 400
        cls.posts = {"b%d" % i: {"title": "step %d" % i, "part": "B%d" % i, "train": "b", "status": "complete", "column": "info",
                                 "created_at": "2026-01-%02d" % i, "verdict": "found number %d" % i, "bars": ["bar%d of 9" % i],
                                 "check": "check%d.py" % i, "breaks": "breaks%d" % i, "artifacts": ["out/a%d" % i], "handoff": "next%d" % i,
                                 "issues": ["issue%d" % i], "body": long} for i in range(1, 15)}
        cls.posts["b15"] = {"title": "a new one", "part": "B15", "train": "b", "status": "planned", "created_at": "2026-02-01", "body": "x"}
        make_db(cls.db, cls.posts)
        conn = sqlite3.connect(cls.db)
        conn.execute("INSERT INTO docs VALUES ('comments', 'c1', 1, ?, 'x')", (json.dumps(
            {"post_id": "b3", "from": "owner", "text": "his words " * 100, "created_at": "2026-01-04T00:00"}),))
        conn.commit()
        conn.close()

    def view(self, **kw):
        return self.kit.train_context(self.tmp, "b", db_path=self.db, **kw)

    def test_brief_is_two_lines_a_step_and_far_smaller(self):
        brief, sections = self.view(), self.view(level="sections")
        self.assertIn("- [x] B3 `b3` step 3\n  found number 3 · next: next3\n", brief)
        self.assertNotIn("bar3 of 9", brief)
        self.assertLess(len(brief.split("## Current Focus")[0]), 0.62 * len(sections.split("## Current Focus")[0]))

    def test_sections_are_the_seven_fields_of_the_steps_asked_for(self):
        out = self.view(level="sections", only=["B3"])
        for x in ("found number 3", "bar3 of 9", "out/a3", "issue3", "check3.py", "breaks3", "next3"):
            self.assertIn(x, out)
        self.assertNotIn("B4", out)
        self.assertNotIn("Current Focus", out)
        self.assertIn("no step ZZ", self.view(level="sections", only=["zz"]).replace("zz", "ZZ"))

    def test_full_is_uncut(self):
        out = self.view(level="full", only=["b3"])
        self.assertIn(self.posts["b3"]["body"].strip(), out)
        self.assertIn(("his words " * 100).strip(), out)
        self.assertNotIn("found number 4", out)

    def test_every_view_ticks_the_same_steps(self):
        out = self.kit.resume(self.tmp, db_path=self.db, alive=set())
        self.assertIn("## Train b (14/15 done)", out)
        self.assertEqual(sum(x.startswith("- [x]") for x in self.view().splitlines()), 14)
        steps = json.loads(self.view(as_json=True))["steps"]
        self.assertEqual(sum(s["box"] == "[x]" for s in steps), 14)

    def test_resume_carries_what_was_found(self):
        out = self.kit.resume(self.tmp, db_path=self.db, alive=set())
        self.assertIn("### Found so far", out)
        self.assertIn("found number 14", out)
        self.assertNotIn("found number 6", out)
        self.assertIn("(6 earlier: `python3 -I .kit/kit.py train b --brief`)", out)
        part = self.kit.resume_role(self.tmp, "part", post="b15", db_path=self.db)
        self.assertIn("## Found so far on train b", part)
        self.assertIn("found number 3", part)

    def test_a_part_sees_what_waits_on_the_owner_on_its_train(self):
        q = {"column": "question", "status": "open"}
        posts = dict(self.posts, q1=dict(q, title="Ours: which bar?", train="B"), q2=dict(q, title="Another: buy a run?", body="B15 waits on this", train="zz"),
                     q3=dict(q, title="Not ours", train="zz", body="B150 and XB15"), q4=dict(q, title="Answered", train="b", status="done"))
        got = "\n".join(self.kit.train_questions(posts, "b15", posts["b15"]))
        self.assertIn("Ours: which bar?", got)
        self.assertIn("Another: buy a run?", got)
        self.assertNotIn("Not ours", got)
        self.assertNotIn("Answered", got)

    def gate(self, text=None, **post):
        path = os.path.join(self.tmp, "prompt.md")
        with open(path, "w") as fh:
            fh.write("the prompt\n" + (("\n" + self.kit.CIRCLE_HEAD + "\n" + text + "\n") if text else ""))
        return self.kit.circle_gate(self.posts, "b15", dict(self.posts["b15"], **post), path)

    def test_a_launch_shows_every_conclusion_and_asks_what_differs(self):
        why = self.gate()
        for i in range(1, 15):
            self.assertIn("- B%d: found number %d" % (i, i), why)      # every one: the circle shares no words with what it repeats
        for bad in ("short", "It is different from everything that was done before", "Builds on B99, which does not exist at all"):
            self.assertIsNotNone(self.gate(bad), bad)
        self.assertIsNone(self.gate("Builds on B9 and B12 (keeps the chord weight); adds the step from its own last note."))
        self.assertIsNone(self.gate("None of them bears on this: it is the settings page only."))
        self.assertIsNone(self.gate(None, differs="Departs from B4: the tune no longer decides the voice."))

    def test_a_step_already_started_or_a_young_train_is_not_asked(self):
        self.assertIsNone(self.gate(session_id="s"))
        self.assertIsNone(self.gate(launched_at="x"))
        few = {k: self.posts[k] for k in ("b1", "b15")}
        self.assertIsNone(self.kit.circle_gate(few, "b15", self.posts["b15"], "/nowhere"))


class PostFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load("kit_mod2", os.path.join(ROOT, "scripts", "kit.py"))
        cls.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.root, "out"))
        open(os.path.join(cls.root, "out", "report.html"), "w").close()

    def good(self, **kw):
        p = {"verdict": "The parser reads 48 of 50 files.", "bars": ["48 of 50 files parsed", "latency 80 ms (bar <= 100 ms)"],
             "artifacts": ["out/report.html", "/_blob/abc"], "not_checked": ["files over 1 GB"],
             "check": "python3 parse.py --all", "breaks": "rm data/sample.csv", "handoff": "use out/parsed.json", "body": "For you: nothing to do."}
        p.update(kw)
        return p

    def test_a_good_post_passes(self):
        self.assertEqual(self.kit.check_done(self.good(), root=self.root), ([], []))

    def test_each_rule(self):
        err, warn = self.kit.check_done(self.good(verdict="", not_checked=[], bars=[], artifacts=["out/missing.png"],
                                                  body="x" * 1600), root=self.root)
        text = " | ".join(err)
        for what in ("verdict: missing", "not_checked: missing", "bars: missing", "out/missing.png does not exist"):
            self.assertIn(what, text)
        # a long body is the result itself, on the board: a nudge, never an error
        self.assertNotIn("body", text)
        self.assertIn("body: 1600 characters", " ".join(warn))
        err, warn = self.kit.check_done(self.good(bars=["it works", "41 takes"], check=""), root=self.root)
        self.assertIn("has no number", " ".join(err))
        self.assertIn("no denominator", " ".join(warn))
        self.assertIn("check: missing", " ".join(err))
        self.assertEqual(self.kit.check_done(self.good(bars=["none: a design step, nothing measured"], check="manual: read it against his words", breaks="manual: drop one"),
                                             root=self.root)[0], [])
        self.assertIn("240", " ".join(self.kit.check_done(self.good(verdict="v" * 300), root=self.root)[0]))

    def test_a_body_that_repeats_its_doc_is_flagged(self):
        lines = ["line %d of the report, long enough to count as a real sentence here" % i for i in range(4)]
        with open(os.path.join(self.root, "out", "report.md"), "w") as fh:
            fh.write("# Report\n" + "\n".join(lines) + "\n")
        _, warn = self.kit.check_done(self.good(body="See `out/report.md § Report`.\n" + "\n".join(lines[:3])), root=self.root)
        self.assertIn("3 lines repeat out/report.md", " ".join(warn))
        _, warn = self.kit.check_done(self.good(body="See `out/report.md § Report`."), root=self.root)
        self.assertNotIn("repeat", " ".join(warn))

    def test_review_prompt_is_filled_and_started_once(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".kit", "claude-cli"))
        os.makedirs(os.path.join(root, ".kit", "board_data"))
        make_db(os.path.join(root, ".kit", "board_data", "board.db"), POSTS)
        with open(os.path.join(ROOT, "kit", "claude-cli", "consultant.template.md")) as src, \
                open(os.path.join(root, ".kit", "claude-cli", "consultant.md"), "w") as dst:
            dst.write(src.read())
        with open(os.path.join(root, ".kit", "config.json"), "w") as fh:
            json.dump({"project": "Demo", "kit": {"claude_cli_roles": {"consultant": {"model": "opus"}}}}, fh)
        old_root, old_env = self.kit.ROOT, os.environ.get("KIT_BOARD_URL")
        self.kit.ROOT = root
        os.environ["KIT_BOARD_URL"] = "http://127.0.0.1:9"
        try:
            rc, out = self.kit.review("PL2-b", dry_run=True)
            self.assertEqual(rc, 0)
            self.assertIn("claude --bg -n demo-review-pl2-b --permission-mode auto --model opus", out)
            self.assertIn("The step `PL2-b` (part PL2, train `planner`) is complete", out)
            self.assertNotIn("<POST_ID>", out)
            self.assertNotIn("<REVIEW_ID>", out)
            rc, out = self.kit.review(None, train="planner", dry_run=True)
            self.assertIn("CROSS REVIEW of the whole train `planner` (1 steps complete)", out)
            # started once: a prompt already written is not started again
            os.makedirs(os.path.join(root, ".kit", "run"))
            open(os.path.join(root, ".kit", "run", "CR-PL2-b.md"), "w").close()
            self.assertIn("already started", self.kit.review("PL2-b")[1])
            # agy parity: review uses .kit/consultant.md and `agy --mode auto -p`
            with open(os.path.join(ROOT, "kit", "consultant.template.md")) as src, \
                    open(os.path.join(root, ".kit", "consultant.md"), "w") as dst:
                dst.write(src.read())
            rc_a, out_a = self.kit.review("PL2-b", dry_run=True, force=True, cli="agy")
            self.assertEqual(rc_a, 0)
            self.assertIn("agy --mode auto -p", out_a)
            self.assertIn("send_message", out_a)
            self.assertNotIn("<POST_ID>", out_a)
            # agy parity: _launch_line and _write_step_prompt use .kit/part.md + invoke_subagent
            with open(os.path.join(ROOT, "kit", "part.template.md")) as src, \
                    open(os.path.join(root, ".kit", "part.md"), "w") as dst:
                dst.write(src.read().replace("{{PROJECT}}", "Demo").replace("{{TALK}}", "English"))
            rel, made = self.kit._write_step_prompt("pl9-agy", {"part": "PL9", "title": "agy step", "train": "planner", "body": "do it"}, [], cli="agy")
            self.assertTrue(made)
            ptxt = rd(os.path.join(root, rel))
            self.assertIn("conversationId", ptxt)
            self.assertIn("send_message", ptxt)
            self.assertIn("device.py run", ptxt)
            launch_a = self.kit._launch_line("PL9", rel, cli="agy")
            self.assertIn("invoke_subagent", launch_a)
            self.assertIn('Workspace="share"', launch_a)
        finally:
            self.kit.ROOT = old_root
            if old_env is None:
                os.environ.pop("KIT_BOARD_URL", None)
            else:
                os.environ["KIT_BOARD_URL"] = old_env


class TaskTrainHook(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.proj, ".kit", "board_data"))
        self.db = os.path.join(self.proj, ".kit", "board_data", "board.db")
        make_db(self.db, {"PL2-b": POSTS["PL2-b"]})
        # Stand-in board CLI: applies set/update to the DB and records each call.
        self.log = os.path.join(self.proj, "calls.jsonl")
        with open(os.path.join(self.proj, ".kit", "board_cli.py"), "w") as fh:
            fh.write(
                "import json,sqlite3,sys\n"
                "a=sys.argv[1:]; open(%r,'a').write(json.dumps(a)+'\\n')\n"
                "d=json.loads(a[a.index('--data')+1]); c=sqlite3.connect(%r)\n"
                "r=c.execute(\"SELECT data FROM docs WHERE collection='posts' AND doc_id=?\",(a[2],)).fetchone()\n"
                "if a[0]=='update' and r: d=dict(json.loads(r[0]),**d)\n"
                "c.execute(\"INSERT OR REPLACE INTO docs VALUES ('posts',?,1,?,'x')\",(a[2],json.dumps(d))); c.commit()\n"
                % (self.log, self.db))
        self.tf = os.path.join(self.proj, "t.jsonl")
        os.makedirs(os.path.join(self.proj, ".kit", "claude-cli"))
        with open(os.path.join(ROOT, "kit", "claude-cli", "part.template.md")) as src, \
                open(os.path.join(self.proj, ".kit", "claude-cli", "part.md"), "w") as dst:
            dst.write(src.read().replace("{{PROJECT}}", "Demo").replace("{{SLUG}}", "demo").replace("{{TALK}}", "English"))
        with open(os.path.join(self.proj, ".kit", "config.json"), "w") as fh:
            json.dump({"project": "Demo", "kit": {"claude_permission_mode": "auto", "review": {"auto": "all", "train_every": 2},
                                                   "claude_cli_roles": {"part": {"model": "inherit"}}}}, fh)
        # the real kit.py (the hook is a thin adapter over `kit.py step`) and the review template; no session starts
        shutil.copy(os.path.join(ROOT, "scripts", "kit.py"), os.path.join(self.proj, ".kit", "kit.py"))
        shutil.copy(os.path.join(ROOT, "kit", "claude-cli", "consultant.template.md"),
                    os.path.join(self.proj, ".kit", "claude-cli", "consultant.md"))
        shutil.copy(os.path.join(ROOT, "kit", "board", "post_gate.py"), os.path.join(self.proj, ".kit", "post_gate.py"))

    def prove(self, pid):
        """What a part's `kit.py done` leaves on its post when it says OK."""
        spec = importlib.util.spec_from_file_location("pg", os.path.join(ROOT, "kit", "board", "post_gate.py"))
        pg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pg)
        p = self.post(pid)
        p["proof"] = {"key": pg.proof_key(p)}
        c = sqlite3.connect(self.db)
        c.execute("UPDATE docs SET data=? WHERE collection='posts' AND doc_id=?", (json.dumps(p), pid))
        c.commit()
        c.close()

    def transcript(self, opener):
        with open(self.tf, "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": opener}}) + "\n")

    def call(self, sid, name, ti, tr=None):
        return self.hook(sid, "PostToolUse", {"tool_name": name, "tool_input": ti, "tool_response": tr or {}})

    def hook(self, sid, event, fields, rc=0, **env):
        payload = {"hook_event_name": event, "session_id": sid, "cwd": self.proj, "transcript_path": self.tf, **fields}
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit", "hooks", "kit_task_train.py")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                           env=dict({k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_TASK_LIST_ID")},  # a computer, even in a web session
                                    CLAUDE_PROJECT_DIR=self.proj, KIT_NO_LAUNCH="1", KIT_BOARD_URL="http://127.0.0.1:9", **env))
        self.assertEqual(r.returncode, rc, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def post(self, pid):
        c = sqlite3.connect(self.db)
        r = c.execute("SELECT data FROM docs WHERE collection='posts' AND doc_id=?", (pid,)).fetchone()
        c.close()
        return json.loads(r[0]) if r else None

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(x) for x in fh]

    def test_orchestrator_tasks_become_train_steps(self):
        self.transcript("You are the orchestrator: start as the orchestrator for this repository (you start the board clerk session if none runs).")
        sid = "orch-tt-%d" % os.getpid()
        msg = self.call(sid, "TaskCreate", {"subject": "PL4 fit the model", "description": "fit it",
                                            "metadata": {"train": "planner", "part": "PL4", "parent": "PL2"}},
                        {"task": {"id": "1"}})
        self.assertIn("post pl4-fit-the-model created", msg)
        self.assertIn('python3 -I .kit/kit.py launch pl4-fit-the-model', msg)  # CL13: one launch command
        prompt = rd(os.path.join(self.proj, "docs", "prompts", "pl4-fit-the-model.md"))
        self.assertIn("Demo, part PL4: fit the model. Train `planner`, after `PL2`.", prompt)
        self.assertIn("## The task\nfit it", prompt)
        self.assertIn("kit.py train planner --zoom PL4", prompt)
        self.assertIn("kit.py done pl4-fit-the-model", prompt)
        self.assertIn('"session_id":"\'"$(python3 -I .kit/kit.py whoami demo-pl4)"\'"', prompt)   # CL38: the real session
        self.assertNotIn("<", prompt.replace("<one line", "").replace("<you>", "").replace("<answer>", ""))
        p = self.post("pl4-fit-the-model")
        self.assertEqual((p["title"], p["part"], p["train"], p["status"], p["parent"], p["body"]),
                         ("fit the model", "PL4", "planner", "planned", "PL2", "fit it"))
        # a step already on the board is adopted, never duplicated
        msg = self.call(sid, "TaskCreate", {"subject": "PL2 second", "description": "",
                                            "metadata": {"train": "P-planner", "part": "pl2"}}, {"task": {"id": "2"}})
        self.assertIn("PL2-b already exists", msg)
        self.assertEqual(sum(1 for c in self.calls() if c[0] == "set"), 1)
        self.call(sid, "TaskUpdate", {"taskId": "1", "status": "in_progress"})
        self.assertEqual(self.post("pl4-fit-the-model")["status"], "in progress")
        msg = self.call(sid, "TaskUpdate", {"taskId": "2", "status": "completed"})      # nothing proved it: it stays open
        self.assertIn("NOT complete", msg)
        self.assertIn("kit.py done PL2-b", msg)
        self.assertNotEqual(self.post("PL2-b")["status"], "complete")
        for pid in ("PL2-b", "pl4-fit-the-model"):
            self.prove(pid)
        self.call(sid, "TaskUpdate", {"taskId": "2", "status": "completed"})
        p2 = self.post("PL2-b")
        self.assertEqual((p2["status"], p2["column"]), ("complete", "info"))  # the owner moves it to Done
        msg = self.call(sid, "TaskUpdate", {"taskId": "1", "status": "completed"})
        run = os.path.join(self.proj, ".kit", "run")
        # each completed step's review prompt, and a cross review at 2 of the train complete (train_every 2)
        self.assertEqual(sorted(f for f in os.listdir(run) if f.endswith(".md")), ["CR-PL2-b.md", "CR-pl4-fit-the-model.md", "CR-train-planner-2.md"])
        self.assertIn("CROSS REVIEW of the whole train `planner`", rd(os.path.join(run, "CR-train-planner-2.md")))
        self.assertIn("review CR-train-planner-2", msg)
        # a task without train metadata stays the orchestrator's own
        self.assertEqual(self.call(sid, "TaskCreate", {"subject": "tidy notes", "description": "x"}, {"task": {"id": "3"}}), "")

    # ------------------------------------------------ a task list that outlives its orchestrator
    ORCH = "You are the orchestrator: start as the orchestrator for this repository (you start the board clerk session if none runs)."

    def kit_json(self, *a):
        r = subprocess.run([sys.executable, "-I", os.path.join(self.proj, ".kit", "kit.py")] + list(a), capture_output=True, text=True,
                           timeout=30, cwd=self.proj, env=dict({k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"},
                                                               KIT_NO_LAUNCH="1", KIT_BOARD_URL="http://127.0.0.1:9"))
        return json.loads(r.stdout.strip().splitlines()[-1])

    def since(self, day):
        p = os.path.join(self.proj, ".kit", "config.json")
        c = json.loads(rd(p))
        c["kit"]["review"]["since"] = day
        with open(p, "w") as fh:
            json.dump(c, fh)

    def test_hand_over_a_step_finished_while_the_orchestrator_is_replaced_is_still_reviewed(self):
        """The owner: "why does a task list die with an orchestrator?" The first one plans and launches, the part
        finishes, a new orchestrator with an empty list takes over: it is told the step, and its completion reviews it."""
        self.since("2026-01-01")
        self.transcript(self.ORCH)
        self.call("orch-A", "TaskCreate", {"subject": "PL4 fit", "description": "x", "metadata": {"train": "planner", "part": "PL4"}},
                  {"task": {"id": "1"}})
        self.call("orch-A", "TaskUpdate", {"taskId": "1", "status": "in_progress"})
        make_db(self.db, {"pl4-fit": dict(self.post("pl4-fit"), status="complete", session_id="demo-pl4", verdict="It fits.")})
        # the new one: an empty list, another session
        steps = {x["part"]: x for x in self.kit_json("step", "open")["steps"]}
        self.assertEqual((steps["PL4"]["state"], steps["PL2"]["state"]), ("review due", "in progress"))
        seen = subprocess.run([sys.executable, "-I", os.path.join(self.proj, ".kit", "kit.py"), "resume", "--db", self.db], cwd=self.proj,
                              capture_output=True, text=True, timeout=60).stdout
        self.assertIn("- [r] PL4 `pl4-fit`", seen)
        self.assertIn("kit.py step complete pl4-fit", seen)
        told = self.hook("orch-B", "UserPromptSubmit", {"prompt": "go on"})
        self.assertIn("2 open steps", told)
        self.assertIn("PL4 (planner, review due)", told)
        self.call("orch-B", "TaskCreate", {"subject": "PL4 fit", "description": "", "metadata": {"train": "planner", "part": "PL4"}},
                  {"task": {"id": "7"}})
        self.call("orch-B", "TaskCreate", {"subject": "PL2 second", "description": "", "metadata": {"train": "planner", "part": "PL2"}},
                  {"task": {"id": "8"}})
        self.assertEqual(self.hook("orch-B", "UserPromptSubmit", {"prompt": "go on"}), "")      # nothing missing: silent
        self.assertIn("CR-pl4-fit", self.call("orch-B", "TaskUpdate", {"taskId": "7", "status": "completed"}))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, ".kit", "run", "CR-pl4-fit.md")))
        self.assertEqual(sum(1 for c in self.calls() if c[0] == "set"), 1)                       # no step made twice

    def test_a_step_reviewed_or_older_than_the_setting_is_not_due_again(self):
        base = {"train": "planner", "status": "complete", "column": "info", "session_id": "s", "verdict": "v"}
        make_db(self.db, {"a1": dict(base, part="A1", created_at="2026-05-01"), "a2": dict(base, part="A2", created_at="2025-05-01"),
                          "a3": dict(base, part="A3", created_at="2026-05-01", review="continue"),
                          "a4": dict(base, part="A4", created_at="2026-05-01", review_started="x"),
                          "a5": dict(base, part="A5", created_at="2026-05-01", review_auto=False),
                          "a6": dict(base, part="A6", created_at="2026-05-01", column="done")})
        due = lambda: [x["part"] for x in self.kit_json("step", "open")["steps"] if x["state"] == "review due"]   # noqa: E731
        self.assertEqual(due(), [])                      # no setting: a project's past is left alone
        self.since("2026-01-01")
        self.assertEqual(due(), ["A1"])

    def test_a_lost_record_is_found_by_the_tasks_subject(self):
        self.transcript(self.ORCH)
        self.hook("orch-C", "TaskCompleted", {"task_id": "5", "task_subject": "PL2 second"}, rc=2)      # kept open by Claude Code
        self.prove("PL2-b")
        self.hook("orch-C", "TaskCompleted", {"task_id": "5", "task_subject": "PL2 second"})
        self.assertIn("PL2-b", self.call("orch-C", "TaskUpdate", {"taskId": "5", "status": "completed"}))
        self.assertEqual(self.post("PL2-b")["status"], "complete")
        self.hook("orch-C", "TaskCompleted", {"task_id": "6", "task_subject": "tidy my notes"})
        self.assertEqual(self.call("orch-C", "TaskUpdate", {"taskId": "6", "status": "completed"}), "")

    def test_the_record_outlives_tmp_and_a_shared_list_has_one(self):
        self.transcript(self.ORCH)
        ti = {"subject": "PL2 second", "description": "", "metadata": {"train": "planner", "part": "PL2"}}
        self.hook("orch-D", "PostToolUse", {"tool_name": "TaskCreate", "tool_input": ti, "tool_response": {"task": {"id": "1"}}},
                  CLAUDE_CODE_TASK_LIST_ID="demo-orchestrator", TMPDIR=tempfile.mkdtemp())
        self.assertIn("PL2-b", self.hook("orch-E", "PostToolUse", {
            "tool_name": "TaskUpdate", "tool_input": {"taskId": "1", "status": "completed"}, "tool_response": {}},
            CLAUDE_CODE_TASK_LIST_ID="demo-orchestrator", TMPDIR=tempfile.mkdtemp()))

    def test_the_reminder_is_for_the_orchestrator_and_at_most_twice(self):
        self.transcript("Your prompt is docs/prompts/PL2.md: read it and do it.")
        self.assertEqual(self.hook("part-F", "UserPromptSubmit", {"prompt": "x"}), "")
        self.transcript(self.ORCH)
        self.assertEqual([bool(self.hook("orch-G", "UserPromptSubmit", {"prompt": "x"})) for _ in range(4)], [True, True, False, False])

    def test_a_web_review_is_recorded_once_its_session_exists(self):
        self.since("2026-01-01")
        make_db(self.db, {"w1": {"part": "W1", "train": "planner", "status": "complete", "created_at": "2026-05-01", "session_id": "s"}})
        subprocess.run([sys.executable, "-I", os.path.join(self.proj, ".kit", "kit.py"), "review", "w1", "--session-id", "session_x"],
                       check=True, capture_output=True, cwd=self.proj)
        self.assertEqual(self.post("w1")["review_session"], "session_x")
        self.assertNotIn("W1", [x["part"] for x in self.kit_json("step", "open")["steps"]])

    def test_deleting_removes_only_a_step_not_yet_started(self):
        self.transcript("You are the orchestrator: start as the orchestrator for this repository (you start the board clerk session if none runs).")
        sid = "orch-del-%d" % os.getpid()
        msg = self.call(sid, "TaskCreate", {"subject": "PL5 later", "description": "",
                                            "metadata": {"train": "planner", "part": "PL5", "sources": ["c-123"]}},
                        {"task": {"id": "1"}})
        self.assertIn("pl5-later", msg)
        self.assertIn("board_cli.py get comments c-123", rd(os.path.join(self.proj, "docs", "prompts", "pl5-later.md")))
        self.call(sid, "TaskCreate", {"subject": "PL2", "description": "",
                                      "metadata": {"train": "planner", "part": "PL2"}}, {"task": {"id": "2"}})
        self.call(sid, "TaskUpdate", {"taskId": "1", "status": "deleted"})
        self.call(sid, "TaskUpdate", {"taskId": "2", "status": "deleted"})
        self.assertTrue(self.post("pl5-later").get("removed"))
        self.assertFalse(self.post("PL2-b").get("removed"))

    def test_other_sessions_are_ignored(self):
        self.transcript("Your prompt is docs/prompts/PL2.md: read it and do it.")
        sid = "part-tt-%d" % os.getpid()
        self.assertEqual(self.call(sid, "TaskCreate", {"subject": "PL6 x", "description": "",
                                                       "metadata": {"train": "planner", "part": "PL6"}},
                                   {"task": {"id": "1"}}), "")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
