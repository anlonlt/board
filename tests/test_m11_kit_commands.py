"""M11: sessions use the kit's commands, the ledger shows when they don't, and the orchestrator is labelled as such.

1. The usage row labels the orchestrator `orchestrator` whatever its start: the first prompt need not name it (11-26:
   session 9919b5f1 began "Are there any posts for a Claude code web runners?", in /home/user, on HEAD, and the
   `_orchestrator` post named another session for part of it). What it ran says it: `kit.py resume --role
   orchestrator`, `kit.py launch|land`, a write of the `_orchestrator` post. A newer row without the command sequence
   never wipes an older one's.
2. A part's first pull reads only its own documents by id (no full `ArtifactData list`); a later pull reads changes
   only; a refused batch's text names `get` by id, never a `list`.
3. `usage audit` lists each session's manual actions where a kit command exists, with the command to use and what
   they carried; `resume --role orchestrator` prints its top 3.

Run via: python3 -m unittest tests.test_m11_kit_commands -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "usage.py"

spec = importlib.util.spec_from_file_location("kit_usage_m11", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)

ART = "https://claude.ai/artifact/X"


def _u():
    return {"input_tokens": 10, "output_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}


def transcript(d, sid, prompt, calls, cwd="/home/user", branch="HEAD"):
    """A Claude Code transcript: one owner prompt, then one API call per (tool, input, result) with its result."""
    lines = [{"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "version": "2.1.332",
              "timestamp": "2026-11-26T21:05:55.543Z", "message": {"role": "user", "content": prompt}}]
    for i, (name, inp, result) in enumerate(calls):
        t = "2026-11-26T21:%02d:00.000Z" % (6 + i)
        lines.append({"type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": t,
                      "message": {"id": "m%d" % i, "model": "claude-opus-5-5", "usage": _u(),
                                  "content": [{"type": "tool_use", "id": "t%d" % i, "name": name, "input": inp}]}})
        lines.append({"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": branch, "timestamp": t,
                      "toolUseResult": {}, "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": "t%d" % i, "content": result}]}})
    lines.append({"type": "assistant", "sessionId": sid, "timestamp": "2026-11-26T23:40:30.917Z",
                  "message": {"id": "end", "model": "claude-opus-5-5", "usage": _u(), "content": [{"type": "text", "text": "."}]}})
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def bash(cmd, out="ok"):
    return ("Bash", {"command": cmd}, out)


def project(d):
    root = os.path.join(d, "proj")
    os.makedirs(os.path.join(root, ".kit"))
    subprocess.run(["git", "init", "-q", root], check=True)
    return root


QUESTION = "Are there any posts for a Claude code web runners?"     # 9919b5f1's first words, 11-26


class OrchestratorLabel(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("KIT_ROLE", "CLAUDE_CODE_REMOTE_SESSION_ID", "KIT_SESSION_NAME")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in self._env.items() if v is not None])

    def record(self, calls, prompt=QUESTION, sid="9919b5f1-6688-5d2d-8494-8c5ecc3fb564"):
        with tempfile.TemporaryDirectory() as d:
            root = project(d)                  # no board here: the `_orchestrator` post cannot name it
            return usage.record(root, transcript(d, sid, prompt, calls), conf={"push": False})

    def test_9919b5f1_a_question_first_then_the_orchestrator_s_resume(self):
        """(asked: the role label) the 11-26 orchestrator, as its transcript went: a question, then its resume."""
        row = self.record([bash("cd /home/user/board && python3 -I .kit/kit.py resume --role orchestrator"),
                           ("ArtifactData", {"action": "list", "collection": "repos/board/posts"}, "x" * 4000),
                           bash("python3 -I .kit/board_cli.py get posts cl45-a-web", "{}" * 300),
                           bash("python3 -I .kit/kit.py launch cl55")])
        self.assertEqual(row["role"], "orchestrator")
        self.assertEqual(row["roles_run"].get("orchestrator"), 2)

    def test_took_over_mid_way_launch_and_land_only(self):
        """A session that took over without a resume line: it launched and landed steps, only the orchestrator does."""
        row = self.record([bash("git status"), bash("python3 -I .kit/kit.py launch m11"),
                           bash("python3 -I .kit/kit.py land m8")])
        self.assertEqual(row["role"], "orchestrator")

    def test_a_hand_over_that_writes_the_orchestrator_post(self):
        row = self.record([("ArtifactData", {"action": "update", "collection": "repos/board/posts",
                                             "doc_id": "_orchestrator", "data": {"session_id": "s"}}, "ok")])
        self.assertEqual(row["role"], "orchestrator")

    def test_the_post_names_it_on_a_computer(self):
        """On a computer the `_orchestrator` post of the local board still labels it (is_orchestrator)."""
        row = {"session_id": "s1", "is_orchestrator": True, "first_prompt": "hi"}
        self.assertEqual(usage.classify(row), ("orchestrator", None))

    def test_a_part_stays_a_part(self):
        row = self.record([bash("python3 -I .kit/kit.py resume --role part --post m11-x"),
                           bash("python3 -I .kit/kit.py land m8")],
                          prompt="Your prompt is docs/prompts/m11-x.md: read it and do it.", sid="p1")
        self.assertEqual((row["role"], row["part"]), ("part", "M11"))

    def test_nothing_says_a_role_stays_other(self):
        self.assertEqual(self.record([bash("ls")], sid="o1")["role"], "other")

    def test_a_newer_row_without_the_sequence_keeps_the_older_one_s(self):
        """11-26: 9919b5f1's own rows (role other, 324 commands) were replaced by a row from a kit without M6
        (role orchestrator, no commands): both facts are kept."""
        old = {"session_id": "s", "source": "transcript", "recorded_at": "2026-11-26T23:40:12+00:00", "role": "other",
               "cmd_seq": [["ArtifactData list", 0.1]], "commands": {"ArtifactData list": {"n": 1}}}
        new = {"session_id": "s", "source": "transcript", "recorded_at": "2026-11-26T23:40:32+00:00",
               "role": "orchestrator", "is_orchestrator": True}
        for order in ((old, new), (new, old)):
            got = usage.merge_rows(list(order))[0]
            self.assertEqual((got["role"], got["cmd_seq"]), ("orchestrator", [["ArtifactData list", 0.1]]))
        got = usage.merge_rows([dict(old, recorded_at="2026-11-27"), dict(new, role="part", is_orchestrator=None)])[0]
        self.assertEqual(got["role"], "other")


class Audit(unittest.TestCase):
    def test_rules_from_a_recorded_transcript(self):
        """(asked: check sessions used the kit's commands) each manual action where a kit command exists is flagged
        with the command to use; what the kit itself printed is not."""
        calls = [
            bash("python3 -I .kit/web_takeover.py"),
            ("ArtifactData", {"action": "list", "collection": "repos/board/posts"}, "L" * 8000),     # printed: first pull
            bash("python3 -I .kit/kit.py artifact push"),
            ("ArtifactData", {"action": "batch", "writes": [{"doc_id": "m11"}]}, "ok"),               # printed by push
            bash("python3 - <<'EOF'\nprint(1)\nEOF"),
            ("ArtifactData", {"action": "batch", "writes": [{"doc_id": "_dashboard"}]}, "ok"),        # hand-built
            ("ArtifactData", {"action": "list", "collection": "repos/board/posts"}, "L" * 8000),     # by hand
            ("ArtifactData", {"action": "get", "collection": "repos/board/posts", "doc_id": "x"}, "G" * 900),
            bash("python3 -I .kit/board_cli.py get posts cl45-x", "P" * 2000),
            bash("python3 -I .kit/board_cli.py get comments abc", "C" * 200),                       # the prompt's line
            bash("sed -n 1,80p .kit/run/artifact/in/repos/board/posts/cl45-x.json", "S" * 3000),
            bash("sed -n 1,80p scripts/kit.py", "code" * 100),                                        # code: not the board
        ]
        with tempfile.TemporaryDirectory() as d:
            row = usage.parse_transcript(transcript(d, "a1", "Your prompt is docs/prompts/m11-x.md", calls))
        row["role"] = "part"
        got = [(f["rule"], f["command"]) for f in usage.audit_row(row)]
        self.assertEqual(got, [("list-first-pull", "ArtifactData list"), ("write-by-hand", "ArtifactData batch"),
                               ("list-by-hand", "ArtifactData list"), ("get-by-hand", "ArtifactData get"),
                               ("cli-get-post", "board_cli.py get"), ("file-read", "sh:sed")])
        found = usage.audit_row(row)
        self.assertEqual(found[2]["tokens"], 2000)                 # 8000 characters, about 2000 tokens
        self.assertGreater(found[2]["carry_tokens"], 0)
        txt = usage.render_audit(usage.audit([row]))
        for want in ("| list-by-hand |", "`kit.py artifact pull` (changes only)", "| a1 | part |", "6 of 12"):
            self.assertIn(want, txt)

    def test_old_rows_without_notes(self):
        """A row recorded before M11 (key and carry only): a list after a kit script is the first pull's; a
        `board_cli.py get` whose target was not recorded says so."""
        row = {"session_id": "o", "role": "orchestrator", "model": "claude-opus-5-5", "cmd_seq": [
            ["kit.py artifact", 0.01], ["ArtifactData list", 0.2], ["git status", 0.0], ["ArtifactData list", 0.4],
            ["board_cli.py get", 0.05], ["kit.py done", 0.0], ["ToolSearch", 0.0], ["ArtifactData batch", 0.0]]}
        got = [f["rule"] for f in usage.audit_row(row)]
        self.assertEqual(got, ["list-first-pull", "list-by-hand", "cli-get-unknown"])
        self.assertEqual(usage.audit_row(row)[1]["carry_tokens"], 2000000)   # $0.40 at $0.20/M cache read

    def test_since_role_and_top3(self):
        rows = [{"session_id": "a", "role": "part", "started": "2026-11-26T10:00", "model": "claude-opus-5-5",
                 "cmd_seq": [["sh:grep", 0], ["ArtifactData list", 1.0]]},
                {"session_id": "b", "role": "orchestrator", "started": "2026-11-25T10:00", "model": "claude-opus-5-5",
                 "cmd_seq": [["sh:grep", 0], ["ArtifactData get", 1.0]]}]
        self.assertEqual([x["row"]["session_id"] for x in usage.audit(rows, since="2026-11-26")], ["a"])
        self.assertEqual([x["row"]["session_id"] for x in usage.audit(rows, role="orchestrator")], ["b"])
        top = usage.audit_top(rows)
        self.assertEqual(len(top), 2)
        self.assertTrue(top[0].startswith("- a full-board `ArtifactData list` made by hand: 1 times in 1 sessions"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "rows.jsonl")
            with open(f, "w") as fh:
                fh.write(json.dumps({"session_id": "a", "role": "part", "model": "claude-opus-5-5",
                                     "cmd_seq": [["sh:grep", 0], ["ArtifactData list", 0.5]]}) + "\n")
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--root", d, "audit", "--only", "--rows", f],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("| list-by-hand |", r.stdout)


def load_kit(root):
    s = importlib.util.spec_from_file_location("kit_m11_%d" % id(root), str(ROOT / "scripts" / "kit.py"))
    k = importlib.util.module_from_spec(s)
    s.loader.exec_module(k)
    k.ROOT = str(root)
    return k


class Pull(unittest.TestCase):
    """(asked: full board pulls) the calls a pull prints."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = {k: os.environ.get(k) for k in ("KIT_ARTIFACT_DIR", "KIT_ARTIFACT_URL", "KIT_ARTIFACT_PREFIX", "KIT_BOARD_URL")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])
        self.art = os.path.join(self.tmp.name, "art")
        os.environ.update(KIT_ARTIFACT_DIR=self.art, KIT_ARTIFACT_URL=ART, KIT_ARTIFACT_PREFIX="repos/board/",
                          KIT_BOARD_URL="http://127.0.0.1:9")          # no local board: the train is not looked up
        os.makedirs(os.path.join(self.tmp.name, "repo", ".kit"))
        self.k = load_kit(os.path.join(self.tmp.name, "repo"))

    @staticmethod
    def calls(txt):
        return [ln for ln in txt.splitlines() if ln.startswith("- `ArtifactData`")]

    def test_a_part_s_first_pull_reads_its_documents_by_id(self):
        before = self.calls(self.k.artifact_pull())
        self.assertEqual(sum("`list`" in c for c in before), 2)              # the whole board: posts and comments
        after = self.k.artifact_pull(post="m11-x")
        self.assertNotIn("`list`", after)
        self.assertIn('`doc_id: "m11-x"`', after)
        self.assertIn('[["post_id", "==", "m11-x"]]', after)
        self.assertIn('`collection: "repos/board/posts"`, `doc_id: "_orchestrator"`', after)

    def test_resume_remembers_the_post_and_full_overrides(self):
        st = self.k._art_state()
        st["scope_post"] = "m11-x"
        self.k._art_save(st)
        self.assertNotIn("`list`", self.k.artifact_pull())
        self.assertIn("`list`", self.k.artifact_pull(full=True))

    def test_a_later_pull_is_changes_only(self):
        st = self.k._art_state()
        st["loaded_at"] = "2026-11-26T23:00:00Z"
        self.k._art_save(st)
        txt = self.k.artifact_pull(post="m11-x")
        self.assertNotIn("`list`", txt)
        self.assertIn('"updated_at", ">", "2026-11-26T22:50:00Z"', txt)

    def test_a_refused_batch_re_reads_only_what_it_names(self):
        txt = self.k.artifact_push_text({"board": ART, "need_pin": ["posts/m11-x", "posts/_trains"], "batches": ["b.json"]})
        self.assertNotIn("`list`", txt)
        self.assertIn("`get`", txt)


class GuardBeforeTheCall(unittest.TestCase):
    """(asked: piece 5, the owner via the orchestrator 2026-11-26) a raw read of a whole collection is refused before
    it runs, naming `kit.py artifact pull`; the calls a pull printed pass."""
    HOOK = ROOT / "kit" / "hooks" / "kit_orchestrator_guard.py"

    def hook(self, proj, ti, role="part"):
        env = {k: v for k, v in os.environ.items() if k not in ("KIT_ROLE", "KIT_ARTIFACT_DIR")}
        env.update(CLAUDE_PROJECT_DIR=proj, KIT_ROLE=role)
        r = subprocess.run([sys.executable, "-I", str(self.HOOK)], capture_output=True, text=True, env=env, timeout=30,
                           input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "ArtifactData",
                                             "tool_input": ti, "session_id": "m11"}))
        return r.returncode, r.stderr

    def test_hand_list_refused_pull_passes(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "repo")
            os.makedirs(os.path.join(proj, ".kit"))
            inn = os.path.join(proj, ".kit", "run", "artifact", "in")
            ti = {"action": "list", "url": ART, "collection": "repos/board/posts", "query": {"limit": 1000}, "out_dir": inn}
            for role in ("part", "orchestrator", "review", "clerk"):
                rc, err = self.hook(proj, ti, role)
                self.assertEqual(rc, 2, role)
                self.assertIn("kit.py artifact pull", err)
            env = dict(os.environ, KIT_ARTIFACT_URL=ART, KIT_ARTIFACT_PREFIX="repos/board/", KIT_BOARD_URL="http://127.0.0.1:9")
            env.pop("KIT_ARTIFACT_DIR", None)
            kit = os.path.join(proj, ".kit", "kit.py")
            with open(kit, "w") as fh:
                fh.write((ROOT / "scripts" / "kit.py").read_text())
            r = subprocess.run([sys.executable, "-I", kit, "artifact", "pull", "--full"], capture_output=True, text=True,
                               env=env, cwd=proj, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.hook(proj, ti)[0], 0)                       # printed by the pull: passes
            r = subprocess.run([sys.executable, "-I", kit, "artifact", "pull", "--post", "m11-x"], capture_output=True,
                               text=True, env=env, cwd=proj, timeout=60)
            self.assertEqual(self.hook(proj, ti)[0], 2)                       # this pull printed no list
            q = {"action": "query", "url": ART, "collection": "repos/board/comments", "out_dir": inn,
                 "query": {"where": [["post_id", "==", "m11-x"]], "limit": 1000}}
            self.assertEqual(self.hook(proj, q)[0], 0)
            self.assertEqual(self.hook(proj, {"action": "get", "url": ART, "collection": "repos/board/posts",
                                              "doc_id": "m11-x"})[0], 0)          # one document by id: always


if __name__ == "__main__":
    unittest.main()
