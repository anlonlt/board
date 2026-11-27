"""CL72: one way to touch the board. The kit prints every call, the guard refuses anything else, the audit and the
guard agree.

The owner, 2026-11-27: "Why are there contradicting rules? The commands are there for a reason. No session may do
anything by hand." Orchestrator 3's usage audit (03:00-07:30) found 22 of 22 sessions doing board work by hand,
because `artifact push`/`pin` told them to make calls by hand and the audit counted the kit's own calls as manual.

The replay: orchestrator 3's real board work (its usage row, tests/fixtures/cl72-orch3-board-work.json: 3 pulls, 31
pushes, 10 of them refused once because the live card had changed) made again through `kit.py artifact sync` alone,
against a fake Artifact, every call through the guard and the PostToolUse hook: 0 manual actions in the audit, 0
refusals of a call the kit printed, every change on the live board, and a hand-edited batch refused.

Run via: python3 -m unittest tests.test_cl72_one_way -v
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req, kit  # noqa: E402

HOOKS = ROOT / "kit" / "hooks"
sys.path.insert(0, str(HOOKS))
import kit_config as kc  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load("kit_orchestrator_guard_cl72", HOOKS / "kit_orchestrator_guard.py")
usage = _load("usage_cl72", ROOT / "scripts" / "usage.py")
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "cl72-orch3-board-work.json").read_text())
URL = "https://claude.ai/artifact/TESTBOARD"
PRE = "repos/board/"
CALL = re.compile(r"^- `ArtifactData` (\{.*\})$", re.M)
COMMENT = re.compile(r"^(?:[0-9a-f]{20}|conflict-|reply-|orch3-|CR-)")


class FakeArtifact:
    """The Artifact's database as ArtifactData sees it: versions, pinned writes, all-or-nothing batches, out_dir saves,
    and replies worded like the tool's (`"<id>"  N bytes  version V  "<path>"`)."""

    def __init__(self):
        self.docs = {}                                   # "<collection>/<doc_id>" -> (version, data)

    def put(self, coll, did, data):
        v = self.docs.get("%s/%s" % (coll, did), (0, None))[0] + 1
        self.docs["%s/%s" % (coll, did)] = (v, data)
        return v

    def _save(self, ti, items):
        lines = []
        for key, (v, data) in items:
            coll, did = key.rsplit("/", 1)
            p = Path(ti["out_dir"]) / coll / (did + ".json")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data))
            lines.append('- "%s"  %d bytes  version %d  "%s"' % (did, p.stat().st_size, v, p))
        return "%d documents saved:\n%s" % (len(items), "\n".join(lines)) if items else "No documents matched."

    def call(self, ti):
        act = ti["action"]
        if act == "get":
            key = "%s/%s" % (ti["collection"], ti["doc_id"])
            return self._save(ti, [(key, self.docs[key])]) if key in self.docs else "No document"
        if act in ("list", "query"):
            where = (ti.get("query") or {}).get("where") or []
            out = []
            for key, (v, data) in sorted(self.docs.items()):
                if key.rsplit("/", 1)[0] != ti["collection"]:
                    continue
                ok = True
                for f, op, val in where:
                    x = data.get(f)
                    ok = ok and (x == val if op in ("==", "eq") else (x is not None and str(x) > str(val)) if op == ">" else True)
                if ok:
                    out.append((key, (v, data)))
            return self._save(ti, out)
        if act == "batch":
            for i, w in enumerate(ti["writes"]):
                key = "%s/%s" % (w["collection"], w["doc_id"])
                cur = self.docs.get(key, (0, None))[0]
                if cur and w.get("if_version") != cur:
                    return "Batch refused, the whole batch writes nothing: entry %d (%s) has changed since; its current " \
                           "version is %d." % (i, key, cur)
            vs = []
            for w in ti["writes"]:
                data = json.loads(Path(w["file_path"]).read_text()) if w.get("file_path") else w.get("data")
                vs.append("%s v%d" % (w["doc_id"], self.put(w["collection"], w["doc_id"], data)))
            return "Batch applied atomically: %d writes (%s)." % (len(vs), ", ".join(vs))
        if act in ("set", "update"):
            return "Written: version %d" % self.put(ti["collection"], ti["doc_id"], ti.get("data"))
        raise AssertionError(act)


class OneWay(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.proj)],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=60), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.art = self.tmp / "art"
        for k, v in (("KIT_ARTIFACT_DIR", str(self.art)), ("KIT_ARTIFACT_PREFIX", PRE), ("KIT_ARTIFACT_URL", URL)):
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.__setitem__(k, old) if old is not None else os.environ.pop(k, None))
        self.live = FakeArtifact()
        self.seq, self.refused, self.t = [], [], 0
        self.changed_live, self.batch_refusals = set(), 0

    # ---- the session's two gestures: run the kit's command, make the calls it printed
    def local(self, path, data):
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def stamp(self):
        """A change made here now (after the last load), a second apart."""
        import time
        self.t += 1
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600 + self.t))

    def make(self, ti, before=None):
        why = guard.board_verdict(ti, str(self.proj), True)
        if why:
            self.refused.append((ti, why))
            return None
        if before:
            before()
        resp = self.live.call(ti)
        self.batch_refusals += ti["action"] == "batch" and "refused" in resp
        kc.seen_record(str(self.art), ti, resp)                 # the PostToolUse hook
        note = ("batch " + ",".join(w["doc_id"] for w in ti["writes"])) if ti["action"] == "batch" else \
            "%s %s" % (ti.get("collection"), ti.get("doc_id") or "")
        self.seq.append(["ArtifactData " + ti["action"], 0.0, 10, note.strip()])
        return resp

    def sync(self, before_batch=None):
        """`kit.py artifact sync` until it says in sync, each printed call made exactly as printed."""
        for _ in range(12):
            self.seq.append(["kit.py artifact", 0.0, 10, "artifact sync"])
            txt, done = kit.artifact_sync(url=self.url, token=TOKEN)
            if done:
                return txt
            calls = [json.loads(m) for m in CALL.findall(txt)]
            self.assertTrue(calls, txt)
            self.assertLessEqual(len({c["action"] == "batch" for c in calls}), 1)      # one kind of set at a time
            for c in calls:
                hook = before_batch if c["action"] == "batch" else None
                self.make(c, hook)
                if hook:
                    before_batch = None
        self.fail("never in sync")

    # ---- the replay
    def seed(self):
        for did in ("_dashboard", "_trains", "_orchestrator", "_prefs"):
            self.live.put(PRE + "posts", did, {"title": did, "column": "info", "updated_at": "2026-11-27T02:00:00Z",
                                               "rows": [{"key": "a", "note": "live"}]})
        for i in range(12):
            self.live.put(PRE + "posts", "seed-%d" % i, {"title": "seed %d" % i, "updated_at": "2026-11-27T02:00:00Z"})

    def change(self, did):
        if "+2F" in did or did.startswith("."):
            return                                               # a mirrored kit_docs file: not a post or comment
        if COMMENT.match(did):
            self.local("comments/" + did, {"post_id": "_orchestrator", "text": "reply %s" % did, "from": "orchestrator",
                                            "created_at": self.stamp()})
        else:
            self.local("posts/" + did, {"title": did, "column": "info" if did.startswith("_") else "doing",
                                         "updated_at": self.stamp(), "rows": [{"key": "a", "note": "here %d" % self.t}]})

    def someone_else_changes(self, did):
        def go():
            key = PRE + ("comments/" if COMMENT.match(did) else "posts/") + did
            v, data = self.live.docs.get(key, (0, None))
            if data is not None:
                self.changed_live.add(key)
                self.live.put(key.rsplit("/", 1)[0], did, dict(data, owner_note="written live meanwhile",
                                                               updated_at="2026-11-27T01:00:00Z"))
        return go

    def test_replay_orchestrator_3_board_work(self):
        """(asked: the owner, 2026-11-27, "No session may do anything by hand"): orchestrator 3's board work through
        `kit.py artifact sync` alone: 0 manual actions, 0 refusals of a printed call, everything on the live board."""
        self.seed()
        before = usage.audit_row(dict(FIXTURE["row"]))
        self.assertGreater(len(before), 10, "the real row had manual actions before CL72")
        self.sync()                                              # the first pull
        n_push = n_refused = 0
        for ev in FIXTURE["events"]:
            if ev["kind"] == "pull":
                self.sync()
                continue
            docs = [d for d in ev["docs"] if "+2F" not in d and not d.startswith(".")]
            if not docs:
                continue
            for d in docs:
                self.change(d)
            hook = self.someone_else_changes(docs[0]) if ev["refused_first"] else None
            self.sync(before_batch=hook)
            n_push += 1
            n_refused += bool(hook)
        self.assertGreaterEqual(n_push, 25)
        self.assertGreaterEqual(n_refused, 8)
        self.assertEqual(self.refused, [], "the guard refused a call the kit printed")
        found = usage.audit_row({"model": FIXTURE["row"].get("model"), "cmd_seq": self.seq})
        self.assertEqual(found, [], "the audit counted a kit-printed call as manual")
        # every change made here is on the live board, and what was written there meanwhile is kept
        for coll in ("posts", "comments"):
            for row in api_req(self.url, "db/" + coll, token=TOKEN).get("docs") or []:
                live = self.live.docs.get(PRE + coll + "/" + row["id"])
                self.assertIsNotNone(live, row["id"])
                for k, v in (row.get("data") or {}).items():
                    if k != "updated_at":
                        self.assertEqual(live[1].get(k), v, "%s/%s.%s" % (coll, row["id"], k))
        self.assertGreaterEqual(self.batch_refusals, 4)          # the live card changed under the batch: refused, merged, sent
        self.assertGreaterEqual(len(self.changed_live), 2)
        for key in self.changed_live:
            self.assertEqual(self.live.docs[key][1].get("owner_note"), "written live meanwhile", key)

    def until_batch(self, post=None):
        """Sync, making the reads it prints, up to the batch it prints (returned, not made)."""
        for _ in range(6):
            txt, done = kit.artifact_sync(post=post, url=self.url, token=TOKEN)
            self.assertFalse(done, txt)
            calls = [json.loads(m) for m in CALL.findall(txt)]
            if calls[0]["action"] == "batch":
                return calls[0]
            for c in calls:
                self.make(c)
        self.fail("no batch")

    def test_a_hand_edited_batch_is_refused(self):
        self.seed()
        self.sync()
        self.change("cl72-x")
        batch = self.until_batch()
        self.assertEqual(batch["action"], "batch")
        edited = json.loads(json.dumps(batch))
        edited["writes"][0]["if_version"] = 99                  # a version typed by hand
        why = guard.board_verdict(edited, str(self.proj), True)
        self.assertIn("not the batch the kit printed", why)
        self.assertIn("kit.py artifact sync", why)
        Path(batch["writes"][0]["file_path"]).write_text(json.dumps({"title": "edited by hand"}))   # its file
        self.assertIn("not the batch the kit printed", guard.board_verdict(batch, str(self.proj), True))

    def test_every_other_board_call_is_refused_for_every_role(self):
        self.seed()
        self.sync()
        raw = [{"action": "get", "url": URL, "collection": PRE + "posts", "doc_id": "_dashboard", "out_dir": "/tmp/x"},
               {"action": "list", "url": URL, "collection": PRE + "comments", "query": {"limit": 1000}},
               {"action": "query", "url": URL, "collection": PRE + "posts", "query": {"where": [["a", "==", 1]], "limit": 5}},
               {"action": "set", "url": URL, "collection": PRE + "posts", "doc_id": "cl72-x", "data": {"t": 1}},
               {"action": "update", "url": URL, "collection": PRE + "posts", "doc_id": "_dashboard", "data": {"rows": []},
                "if_version": 1}]
        for orch in (True, False):
            for ti in raw:
                why = guard.board_verdict(ti, str(self.proj), orch)
                self.assertTrue(why, ti)
                self.assertIn("kit.py artifact sync", why)
        # what has no kit form passes, the same list for the guard and the audit
        free = [{"action": "set", "url": URL, "collection": PRE + "typing", "doc_id": "cl72", "data": {"from": "x"}},
                {"action": "update", "url": URL, "collection": PRE + "comments", "doc_id": "c1", "data": {"read": True},
                 "if_version": 2},
                {"action": "update", "url": URL, "collection": PRE + "posts", "doc_id": "_orchestrator",
                 "data": {"session_id": "s"}, "if_version": 2},
                {"action": "get", "url": "https://claude.ai/artifact/OTHER", "collection": "rows", "doc_id": "a"}]
        for ti in free:
            self.assertIsNone(guard.board_verdict(ti, str(self.proj), True), ti)
        self.assertEqual(usage.audit_row({"cmd_seq": [["web_takeover.py", 0, 0, ""],
                                                      ["ArtifactData update", 0, 0, PRE + "comments c1"],
                                                      ["ArtifactData update", 0, 0, PRE + "posts _orchestrator"],
                                                      ["ArtifactData set", 0, 0, PRE + "typing cl72"]]}), [])
        # a printed call goes out once
        self.change("cl72-y")
        batch = self.until_batch()
        self.assertIsNone(guard.board_verdict(batch, str(self.proj), False))
        self.assertIn("made already", guard.board_verdict(batch, str(self.proj), False))

    def test_a_part_sends_only_its_own_documents(self):
        self.seed()
        self.sync()
        self.local("posts/cl72-mine", {"title": "mine", "updated_at": self.stamp()})
        self.local("comments/c-mine", {"post_id": "cl72-mine", "text": "x", "created_at": self.stamp()})
        self.local("posts/cl99-other", {"title": "not mine", "updated_at": self.stamp()})
        batch = self.until_batch(post="cl72-mine")
        self.assertEqual(sorted(w["doc_id"] for w in batch["writes"]), ["c-mine", "cl72-mine"])

    def test_a_raw_post_read_on_the_local_copy_is_refused_and_counted(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "python3 -I .kit/board_cli.py get posts cl54-x --out-dir /tmp/a"},
                "cwd": str(self.proj)}
        why = guard.verdict(data, str(self.proj), False)
        self.assertIn("kit.py post <id>", why)
        self.assertIsNone(guard.verdict(dict(data, tool_input={"command": "python3 -I .kit/board_cli.py reply p --text hi"}),
                                        str(self.proj), False))
        self.assertEqual([f["rule"] for f in usage.audit_row({"cmd_seq": [["board_cli.py get", 0, 0, "posts cl54-x"]]})],
                         ["cli-get-post"])


if __name__ == "__main__":
    unittest.main()


class DesignGate(unittest.TestCase):
    """(asked: the owner, 2026-11-27, "Why do you skip stuff? It's not the first time."): an item of resume's "First:
    what keeps coming back" with no `kit.py ledger design` entry blocks `kit.py launch` and `kit.py land`."""

    def setUp(self):
        from unittest import mock
        self.mem = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.mem, ignore_errors=True))
        self.led = kit._load_ledger_mod()
        for p in (mock.patch.object(kit, "_resolve_memory_dir", lambda *a: str(self.mem)),
                  mock.patch.object(kit, "ROOT", str(self.mem))):
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *argv):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = kit.main(list(argv))
        return rc, buf.getvalue()

    def test_an_open_recurring_item_blocks_launch_and_land_until_its_design_is_recorded(self):
        self.assertEqual(kit.design_gate("launch"), "")
        for i in range(3):                               # "two copies out of sync", fixed again and again
            self.led.observe(str(self.mem), "fix %d" % i, kind="fix", theme="two copies out of sync", source="cl%d" % i)
        for cmd in ("launch", "land"):
            rc, out = self.run_main(cmd, "cl99-next-step")
            self.assertEqual(rc, 1, out)
            self.assertIn("NOT %s" % cmd.upper(), out)
            self.assertIn("two copies out of sync", out)
            self.assertIn('kit.py ledger design "two copies out of sync" --post <step>', out)
        r = self.led.mark_designed(str(self.mem), "two copies out of sync", "cl73-one-copy")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(kit.design_gate("launch"), "")
        self.assertEqual(kit.design_gate("land"), "")
