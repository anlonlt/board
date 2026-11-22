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
        txt = self.kit.train_context(self.tmp, "planner", db_path=self.db)
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
        self.assertIn("check: missing", " ".join(warn))
        self.assertEqual(self.kit.check_done(self.good(bars=["none: a design step, nothing measured"], check=""),
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

    def transcript(self, opener):
        with open(self.tf, "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": opener}}) + "\n")

    def call(self, sid, name, ti, tr=None):
        st = os.path.join(tempfile.gettempdir(), "kit-task-train-%s.json" % sid)
        payload = {"hook_event_name": "PostToolUse", "session_id": sid, "cwd": self.proj, "tool_name": name,
                   "tool_input": ti, "tool_response": tr or {}, "transcript_path": self.tf}
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit", "hooks", "kit_task_train.py")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, KIT_NO_LAUNCH="1",
                                    KIT_BOARD_URL="http://127.0.0.1:9"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.addCleanup(lambda: [os.path.exists(p) and os.remove(p) for p in (st, st + ".lock")])
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
        self.transcript("Read .kit/claude-cli/orchestrator.md and start as the orchestrator for this repository.")
        sid = "orch-tt-%d" % os.getpid()
        msg = self.call(sid, "TaskCreate", {"subject": "PL4 fit the model", "description": "fit it",
                                            "metadata": {"train": "planner", "part": "PL4", "parent": "PL2"}},
                        {"task": {"id": "1"}})
        self.assertIn("post pl4-fit-the-model created", msg)
        self.assertIn('KIT_ROLE=part claude --bg -n demo-pl4 -w pl4 --permission-mode auto "Your prompt is docs/prompts/pl4-fit-the-model.md', msg)
        prompt = rd(os.path.join(self.proj, "docs", "prompts", "pl4-fit-the-model.md"))
        self.assertIn("Demo, part PL4: fit the model. Train `planner`, after `PL2`.", prompt)
        self.assertIn("## The task\nfit it", prompt)
        self.assertIn("kit.py train planner --zoom PL4", prompt)
        self.assertIn("kit.py done pl4-fit-the-model", prompt)
        self.assertIn('"session_id":"demo-pl4"', prompt)
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
        self.call(sid, "TaskUpdate", {"taskId": "2", "status": "completed"})
        p2 = self.post("PL2-b")
        self.assertEqual((p2["status"], p2["column"]), ("complete", "info"))  # the owner moves it to Done
        msg = self.call(sid, "TaskUpdate", {"taskId": "1", "status": "completed"})
        run = os.path.join(self.proj, ".kit", "run")
        # each completed step's review prompt, and a cross review at 2 of the train complete (train_every 2)
        self.assertEqual(sorted(os.listdir(run)), ["CR-PL2-b.md", "CR-pl4-fit-the-model.md", "CR-train-planner-2.md"])
        self.assertIn("CROSS REVIEW of the whole train `planner`", rd(os.path.join(run, "CR-train-planner-2.md")))
        self.assertIn("review CR-train-planner-2", msg)
        # a task without train metadata stays the orchestrator's own
        self.assertEqual(self.call(sid, "TaskCreate", {"subject": "tidy notes", "description": "x"}, {"task": {"id": "3"}}), "")

    def test_deleting_removes_only_a_step_not_yet_started(self):
        self.transcript("Read .kit/claude-cli/orchestrator.md and start as the orchestrator for this repository.")
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
