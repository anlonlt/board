"""CL37: the usage ledger: tokens, tools, agents per session, recorded by a script (no model call).

- `usage.py record` reads a Claude Code transcript (and its subagents) into one row: tokens deduplicated by message id,
  cost from the price table (cache writes 5m/1h apart), tool calls by name, ArtifactData reads/writes and bytes,
  messages sent and received, owner turns, context peak, wall time, the kit commit, role and part.
- The hook (kit_usage.py, Stop/SessionEnd) records silently and never dirties the working tree; the ledger branch
  `kit-usage` is written with plumbing and pushed, never forced, merging with a concurrent writer.
- `usage.py compare --before A --after B [--role R]` gives per-role medians and totals with n on each side.
- `import-ccr` turns saved claude-code-remote get_session records into rows (cost_usd kept as recorded).
- install.py puts .kit/usage.py and wires kit_usage.py on Stop and SessionEnd.

Run via: python3 -m unittest tests.test_cl37_usage -v
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
HOOK = ROOT / "kit" / "hooks" / "kit_usage.py"

spec = importlib.util.spec_from_file_location("kit_usage_mod", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def _u(i, o, cr, w5=0, w1=0):
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}


def fixture(d, sid="s-part", branch="cl99", prompt="Your prompt is docs/prompts/cl99-do-it.md: read it and do it.",
            model="claude-opus-5-5", sub=True, t0="2026-11-20T10:00:00.000Z"):
    """A transcript: 2 owner turns, 1 received message, 1 notice, 3 API calls (one split over two lines), tools."""
    def ts(m):
        return t0[:14] + "%02d:00.000Z" % m
    L = []
    base = {"sessionId": sid, "cwd": "/w/proj", "gitBranch": branch, "version": "2.1.331", "entrypoint": "remote"}
    L.append(dict(base, type="user", timestamp=ts(0), uuid="u1", message={"role": "user", "content": prompt}))
    # call 1 is written as two lines (thinking, then a tool_use) with the same message id and usage
    m1 = {"id": "msg1", "model": model, "role": "assistant", "usage": _u(10, 100, 1000, w1=500)}
    L.append(dict(base, type="assistant", timestamp=ts(1), uuid="a1", message=dict(m1, content=[{"type": "thinking", "thinking": ""}])))
    L.append(dict(base, type="assistant", timestamp=ts(1), uuid="a1b", message=dict(m1, content=[
        {"type": "tool_use", "id": "t1", "name": "ArtifactData", "input": {"action": "list", "collection": "repos/x/posts"}},
        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "python3 -I .kit/board_cli.py update posts p --data '{}'"}}])))
    L.append(dict(base, type="user", timestamp=ts(2), uuid="r1", toolUseResult={}, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 400},
        {"type": "tool_result", "tool_use_id": "t2", "content": "y" * 40}]}))
    m2 = {"id": "msg2", "model": model, "role": "assistant", "usage": _u(5, 200, 2000, w5=100)}
    L.append(dict(base, type="assistant", timestamp=ts(3), uuid="a2", message=dict(m2, content=[
        {"type": "tool_use", "id": "t3", "name": "ArtifactData", "input": {"action": "update", "collection": "c", "doc_id": "d", "data": {"body": "z" * 100}}},
        {"type": "tool_use", "id": "t4", "name": "SendMessage", "input": {"to": "orch", "message": "Done. Check post p."}},
        {"type": "tool_use", "id": "t5", "name": "Agent", "input": {"prompt": "go"}}])))
    L.append(dict(base, type="user", timestamp=ts(4), uuid="n1", message={"role": "user", "content": "[SYSTEM NOTIFICATION - NOT USER INPUT] agent done"}))
    L.append(dict(base, type="user", timestamp=ts(5), uuid="c1", message={"role": "user", "content": "<cross-session-message from=\"o\">hi</cross-session-message>"}))
    L.append(dict(base, type="user", timestamp=ts(6), uuid="m1", isMeta=True, message={"role": "user", "content": "skill text"}))
    L.append(dict(base, type="user", timestamp=ts(7), uuid="u2", message={"role": "user", "content": [{"type": "text", "text": "Owner asks again"}]}))
    m3 = {"id": "msg3", "model": model, "role": "assistant", "usage": _u(1, 50, 3000)}
    L.append(dict(base, type="assistant", timestamp=ts(8), uuid="a3", message=dict(m3, content=[{"type": "text", "text": "·"}])))
    L.append({"type": "assistant", "timestamp": ts(8), "message": {"id": "syn", "model": "<synthetic>", "usage": _u(0, 0, 0), "content": []}})
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w") as fh:
        fh.write("".join(json.dumps(x) + "\n" for x in L))
        fh.write("not json\n")
    if sub:
        sd = os.path.join(d, sid, "subagents")
        os.makedirs(sd)
        with open(os.path.join(sd, "agent-abc.jsonl"), "w") as fh:
            fh.write(json.dumps({"type": "assistant", "sessionId": sid, "isSidechain": True, "timestamp": ts(4),
                                 "message": {"id": "sub1", "model": "claude-haiku-5-5", "usage": _u(2, 40, 900, w5=10),
                                             "content": [{"type": "tool_use", "id": "s1", "name": "Read", "input": {}}]}}) + "\n")
        with open(os.path.join(sd, "agent-abc.meta.json"), "w") as fh:
            json.dump({"agentType": "Explore", "description": "find it"}, fh)
    return p


def project(d, kit_commit="abc1234 2026-11-19T08:00:00+00:00"):
    root = os.path.join(d, "proj")
    os.makedirs(os.path.join(root, ".kit"))
    with open(os.path.join(root, ".kit", "manifest.json"), "w") as fh:
        json.dump({"_kit_commit": kit_commit}, fh)
    subprocess.run(["git", "init", "-q", root], check=True)
    return root


class Record(unittest.TestCase):
    def test_row_from_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            row = usage.parse_transcript(fixture(d))
            self.assertEqual(row["api_calls"], 3)                      # msg1 counted once, <synthetic> skipped
            self.assertEqual(row["tokens"], {"input": 16, "output": 350, "cache_read": 6000, "cache_write": 600,
                                             "cache_write_1h": 500})
            # opus-5-5: 4/20/0.2 per M; writes 1h 2x, 5m 1.25x
            want = (16 * 4 + 350 * 20 + 6000 * 0.2 + 500 * 4 * 2 + 100 * 4 * 1.25) / 1e6
            self.assertAlmostEqual(row["cost_usd"], round(want, 6), places=6)
            self.assertEqual(row["context_peak"], 3001)
            self.assertEqual((row["owner_turns"], row["received"], row["notices"]), (2, 1, 1))
            self.assertEqual((row["sent"], row["sent_chars"]), (1, len("Done. Check post p.")))
            self.assertEqual(row["tools"], {"ArtifactData": 2, "Bash": 1, "SendMessage": 1, "Agent": 1})
            self.assertEqual(row["board"]["read_bytes"], 400)
            self.assertEqual(row["board"]["writes"], 1)
            self.assertGreater(row["board"]["write_bytes"], 100)
            self.assertEqual((row["board"]["cli"], row["board"]["cli_bytes"]), (1, 40))
            self.assertEqual(row["kit_cmds"], {"board_cli update": 1})
            self.assertEqual(row["wall_s"], 8 * 60)
            self.assertEqual(row["agents_spawned"], 1)
            self.assertEqual(len(row["subagents"]), 1)
            s = row["subagents"][0]
            self.assertEqual((s["type"], s["model"], s["tokens"]["output"]), ("Explore", "claude-haiku-5-5", 40))
            self.assertEqual(row["tokens_all"]["output"], 390)
            self.assertGreater(row["cost_usd_all"], row["cost_usd"])

    def test_haiku_long_prompt_tier(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            Path(p).write_text(json.dumps({"type": "assistant", "sessionId": "h", "timestamp": "2026-11-20T10:00:00Z",
                                           "message": {"id": "m", "model": "claude-haiku-5-5",
                                                       "usage": _u(0, 1000, 200000)}}) + "\n")
            self.assertAlmostEqual(usage.parse_transcript(p)["cost_usd"], (1000 * 2.5 + 200000 * 0.05) / 1e6, places=6)

    def test_unknown_model_has_no_cost(self):
        with tempfile.TemporaryDirectory() as d:
            row = usage.parse_transcript(fixture(d, model="mystery-1", sub=False))
            self.assertIsNone(row["cost_usd"])
            self.assertIsNone(row["cost_usd_all"])

    def test_roles(self):
        self.assertEqual(usage.classify({"branch": "cl37", "first_prompt": "Your prompt is docs/prompts/cl37-usage.md"}),
                         ("part", "CL37"))
        self.assertEqual(usage.classify({"branch": "orchestrator-53"}), ("orchestrator", None))
        self.assertEqual(usage.classify({"branch": "main"}, "Harmonize orchestrator 53"), ("orchestrator", None))
        self.assertEqual(usage.classify({"branch": "main"}, "Harmonize clerk BC32"), ("clerk", None))
        self.assertEqual(usage.classify({"branch": "main"}, "Review of CL30 the kit's scripts"), ("review", None))
        self.assertEqual(usage.classify({"branch": "x"}, "M1b finish the count"), ("part", "M1B"))
        self.assertEqual(usage.classify({"branch": "x"}, "hello"), ("other", None))
        self.assertEqual(usage.classify({"branch": "main"}, "Harmonize TB1: bench sittings"), ("part", "TB1"))
        self.assertEqual(usage.classify({"branch": "main"}, "Harmonize VR2-D bench the trained reader"), ("part", "VR2-D"))
        self.assertEqual(usage.classify({"branch": "x"}, "Board CL9 continue (breaks)"), ("part", "CL9"))
        self.assertEqual(usage.classify({"branch": "x"}, "CR-B13 review"), ("review", None))
        self.assertEqual(usage.classify({"branch": "x"}, "Harmonize CR-HL1 review"), ("review", None))
        self.assertEqual(usage.classify({"branch": "x"}, "CL28 orchestrator answers from a fresh brief"), ("part", "CL28"))
        self.assertEqual(usage.classify({"branch": "x"}, "Harmonize BC29 board clerk"), ("clerk", "BC29"))
        self.assertEqual(usage.classify({"branch": "x"}, "Harmonize BC5 \u2014 the board clerk (small context)"), ("clerk", "BC5"))


class Roles(unittest.TestCase):
    def test_orchestrator_from_its_prompt_file_head_branch_and_the_board(self):
        row = {"branch": "HEAD", "first_prompt": "Your prompt is docs/prompts/O54-orchestrator.md: take over."}
        self.assertEqual(usage.classify(row), ("orchestrator", None))
        self.assertEqual(usage.classify({"branch": "HEAD", "first_prompt": "docs/prompts/BC33-clerk.md"}),
                         ("clerk", "BC33"))
        self.assertEqual(usage.classify({"branch": "HEAD", "first_prompt": "docs/prompts/CR-CL30.md"}), ("review", "CR-CL30"))
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            os.makedirs(os.path.join(root, ".kit", "board_data"))
            import sqlite3
            con = sqlite3.connect(os.path.join(root, ".kit", "board_data", "board.db"))
            con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
            con.execute("INSERT INTO docs VALUES ('posts', '_orchestrator', 1, ?, '')", (json.dumps({"session_id": "s-orch"}),))
            con.commit()
            con.close()
            # its first prompt names a part's prompt (a launch it made), its branch is HEAD: still the orchestrator
            row = usage.record(root, fixture(d, sid="s-orch", branch="HEAD"))
            self.assertEqual((row["role"], row["part"]), ("orchestrator", None))
            os.makedirs(os.path.join(d, "o2"))
            other = usage.record(root, fixture(os.path.join(d, "o2"), sid="s-part"))
            self.assertEqual(other["role"], "part")


class O55Gaps(unittest.TestCase):
    def test_a_role_word_inside_a_part_prompt_name_is_a_part(self):
        for name, part in (("cl24-orchestrator-guard-gaps", "CL24"), ("cl28-the-orchestrator-answers-only", "CL28"),
                           ("cl5-automatic-reviews-said-once", "CL5")):
            row = {"branch": "cl24", "first_prompt": "Your prompt is docs/prompts/%s.md: read it." % name}
            self.assertEqual(usage.classify(row), ("part", part), name)
        self.assertEqual(usage.classify({"branch": "x", "first_prompt": "docs/prompts/BC32-board-clerk.md"}),
                         ("clerk", "BC32"))

    def test_a_one_line_hand_over_on_a_claude_branch(self):
        for prompt, role in (("You are orchestrator 56 of this repository (.kit/claude-cli/orchestrator.md), after a "
                              "context reset", "orchestrator"),
                             ("Take over: python3 -I .kit/kit.py resume --role orchestrator", "orchestrator"),
                             ("Read .kit/claude-cli/takeover.md and take over.", "orchestrator"),
                             ("Your prompt is .kit/claude-cli/clerk.md: read it and follow it.", "clerk")):
            self.assertEqual(usage.classify({"branch": "claude/x-y", "first_prompt": prompt})[0], role, prompt)

    def test_kit_role_env_wins(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            os.environ["KIT_ROLE"] = "clerk"
            try:
                row = usage.record(root, fixture(d))
            finally:
                del os.environ["KIT_ROLE"]
            self.assertEqual(row["role"], "clerk")

    def test_work_measure_and_cost_per_unit(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            g = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t"]
            env = dict(os.environ, GIT_COMMITTER_DATE="2026-11-20T10:03:00+00:00", GIT_AUTHOR_DATE="2026-11-20T10:03:00+00:00")
            Path(root, "a.py").write_text("1\n2\n3\n")
            subprocess.run(g + ["add", "a.py"], check=True)
            subprocess.run(g + ["commit", "-qm", "a"], check=True, env=env)
            env2 = dict(env, GIT_COMMITTER_DATE="2026-11-21T10:00:00+00:00", GIT_AUTHOR_DATE="2026-11-21T10:00:00+00:00")
            Path(root, "b.py").write_text("x\n")
            subprocess.run(g + ["add", "b.py"], check=True)
            subprocess.run(g + ["commit", "-qm", "later: outside the session"], check=True, env=env2)
            tp = fixture(d)
            lines = Path(tp).read_text().splitlines()
            e = json.loads(lines[2])
            e["message"]["content"].append({"type": "tool_use", "id": "t9", "name": "Bash",
                                            "input": {"command": "python3 -m unittest tests.test_x"}})
            lines[2] = json.dumps(e)
            Path(tp).write_text("\n".join(lines) + "\n")
            row = usage.record(root, tp)
            row["cwd"] = root
            self.assertEqual(row["work"]["commits"], 1)
            self.assertEqual((row["work"]["lines_added"], row["work"]["files"]), (3, 1))
            self.assertEqual(row["tests_run"], 1)
            res = usage.summarize([row])
            self.assertAlmostEqual(res["cost $ per commit"]["median"], row["cost_usd_all"])
            self.assertAlmostEqual(res["cost $ per 100 lines changed"]["median"], 100 * row["cost_usd_all"] / 3)


class O55Gaps2(unittest.TestCase):
    def _repo(self, d):
        root = project(d)
        g = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t"]
        env = dict(os.environ, GIT_COMMITTER_DATE="2026-11-20T10:03:00+00:00", GIT_AUTHOR_DATE="2026-11-20T10:03:00+00:00")
        for i, (f, n, trailer) in enumerate((("a.py", 3, "session_MINE"), ("b.py", 5, "session_OTHER"),
                                             ("c.py", 7, None))):
            Path(root, f).write_text("x\n" * n)
            subprocess.run(g + ["add", f], check=True)
            msg = "c%d" % i + ("\n\nClaude-Session: https://claude.ai/code/%s" % trailer if trailer else "")
            subprocess.run(g + ["commit", "-qm", msg], check=True, env=env)
        return root

    def test_git_work_counts_only_this_sessions_commits(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(d)
            w = usage.git_work(root, "2026-11-20T10:00:00+00:00", "2026-11-20T10:08:00+00:00", ccr_id="session_MINE")
            self.assertEqual((w["commits"], w["lines_added"], w["by"]), (1, 3, "trailer"))
            w = usage.git_work(root, "2026-11-20T10:00:00+00:00", "2026-11-20T10:08:00+00:00")
            self.assertEqual((w["commits"], w["lines_added"], w["by"]), (1, 7, "window"))  # no other session's commit

    def test_shallow_boundary_commit_is_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._repo(d)
            sh = os.path.join(d, "shallow")
            subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + src, sh], check=True)
            w = usage.git_work(sh, "2026-11-20T10:00:00+00:00", "2026-11-20T10:08:00+00:00")
            self.assertEqual((w["commits"], w["lines_added"]), (0, 0))       # the boundary shows the whole tree

    def test_the_hook_takes_the_cloud_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._repo(d)
            old = {k: os.environ.get(k) for k in ("CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")}
            os.environ["CLAUDE_CODE_REMOTE_SESSION_ID"] = "cse_MINE"
            os.environ["CLAUDE_CODE_SESSION_ID"] = "s-part"
            try:
                tp = fixture(d)
                row = usage.record(root, tp)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(row["ccr_session_id"], "session_MINE")
            # the transcript's cwd (/w/proj) is gone, so the project is read: only this session's trailer counts
            self.assertEqual((row["work"]["commits"], row["work"]["by"], row["work"]["lines_added"]), (1, "trailer", 3))

    def test_a_hand_over_with_no_role_word(self):
        self.assertEqual(usage.classify({"branch": "claude/x", "first_prompt": "Run kit.py resume, then read post "
                                         "_handover."})[0], "orchestrator")


class InstallShallow(unittest.TestCase):
    def test_a_hook_new_after_the_manifest_date_is_new_without_kit_history(self):
        spec2 = importlib.util.spec_from_file_location("kit_install_mod", ROOT / "install.py")
        ins = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(ins)
        orig = ins._in_kit_at
        ins._in_kit_at = lambda c, h: None                  # a shallow kit clone: neither commit is here
        try:
            self.assertTrue(ins.new_since_install("kit_usage.py", "219f713", "2026-11-23T23:32:43+00:00"))
            self.assertFalse(ins.new_since_install("kit_usage.py", "abc", "2026-11-25T00:00:00+00:00"))
            self.assertFalse(ins.new_since_install("kit_stop.py", "219f713", "2026-11-23T23:32:43+00:00"))
            self.assertFalse(ins.new_since_install("kit_usage.py", "", "2026-11-23T23:32:43+00:00"))
        finally:
            ins._in_kit_at = orig


class Ledger(unittest.TestCase):
    def test_hook_records_silently_and_tree_stays_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            tp = fixture(d)
            payload = json.dumps({"transcript_path": tp, "cwd": root, "hook_event_name": "Stop", "session_id": "s-part"})
            env = dict(os.environ, KIT_USAGE_PUSH="0")
            # the installed layout: .kit/usage.py next to the hook's project
            os.makedirs(os.path.join(root, ".claude", "hooks", "kit"))
            for f in ("kit_usage.py", "kit_config.py"):
                Path(root, ".claude", "hooks", "kit", f).write_text((ROOT / "kit" / "hooks" / f).read_text())
            Path(root, ".kit", "usage.py").write_text(SCRIPT.read_text())
            r = subprocess.run([sys.executable, "-I", os.path.join(root, ".claude", "hooks", "kit", "kit_usage.py")],
                               input=payload, capture_output=True, text=True, env=env, cwd=root, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "")                      # nothing reaches the model
            row = json.loads(Path(root, ".kit", "run", "usage", "s-part.json").read_text())
            self.assertEqual((row["role"], row["part"], row["kit_commit"]), ("part", "CL99", "abc1234"))
            st = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout
            self.assertEqual(st.strip(), "")

    def test_every_stop_pushes_detached_and_the_turn_does_not_wait(self):
        import time as _t
        with tempfile.TemporaryDirectory() as d:
            bare = os.path.join(d, "origin.git")
            subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
            root = project(d)
            subprocess.run(["git", "-C", root, "remote", "add", "origin", bare], check=True)
            Path(root, ".kit", "usage.py").write_text(SCRIPT.read_text())
            payload = json.dumps({"transcript_path": fixture(d), "cwd": root, "hook_event_name": "Stop"})
            env = {k: v for k, v in os.environ.items() if not k.startswith("KIT_USAGE")}
            t0 = _t.time()
            r = subprocess.run([sys.executable, "-I", os.path.join(root, ".kit", "usage.py"), "--root", root, "hook"],
                               input=payload, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            for _ in range(100):
                ls = subprocess.run(["git", "-C", bare, "ls-tree", "--name-only", "kit-usage", "usage/"],
                                    capture_output=True, text=True)
                if ls.returncode == 0 and ls.stdout.strip():
                    break
                _t.sleep(0.1)
            self.assertEqual(ls.stdout.split(), ["usage/2026-11-20.jsonl"])

    def test_hook_never_breaks_on_garbage(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "hook"], input="{not json", capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "hook"],
                           input=json.dumps({"transcript_path": "/nope.jsonl"}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)

    def test_push_to_ledger_branch_merges_and_never_touches_head(self):
        with tempfile.TemporaryDirectory() as d:
            bare = os.path.join(d, "origin.git")
            subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
            root = project(d)
            subprocess.run(["git", "-C", root, "remote", "add", "origin", bare], check=True)
            a = usage.record(root, fixture(d, sid="s-a"))
            print_ = usage.push(root, [a])
            self.assertIn("pushed 1 rows", print_)
            # a second clone (another session) pushes its own row: both are kept
            other = os.path.join(d, "other")
            os.makedirs(os.path.join(other, ".kit"))
            subprocess.run(["git", "init", "-q", other], check=True)
            subprocess.run(["git", "-C", other, "remote", "add", "origin", bare], check=True)
            os.makedirs(os.path.join(d, "t2"))
            b = usage.record(other, fixture(os.path.join(d, "t2"), sid="s-b", t0="2026-11-21T10:00:00.000Z"))
            self.assertIn("pushed 2 rows", usage.push(other, [b]))
            self.assertEqual(usage.push(root, [a]), "up to date")
            subprocess.run(["git", "-C", root, "fetch", "-q", "origin", "+refs/heads/kit-usage:refs/remotes/origin/kit-usage"])
            rows = usage.ledger_rows(root)
            self.assertEqual(sorted(r["session_id"] for r in rows), ["s-a", "s-b"])
            files = subprocess.run(["git", "-C", root, "ls-tree", "--name-only", "origin/kit-usage", "usage/"],
                                   capture_output=True, text=True).stdout.split()
            self.assertEqual(files, ["usage/2026-11-20.jsonl", "usage/2026-11-21.jsonl"])
            head = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True)
            self.assertNotEqual(head.returncode, 0)                    # no commit on the working branch
            self.assertEqual(subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                                            capture_output=True, text=True).stdout.strip(), "")


class Compare(unittest.TestCase):
    def rows(self):
        out = []
        for i, (t, cost, role) in enumerate([("2026-11-10", 2.0, "part"), ("2026-11-11", 4.0, "part"),
                                              ("2026-11-12", 9.0, "orchestrator"),
                                              ("2026-11-21", 1.0, "part"), ("2026-11-22", 3.0, "part"),
                                              ("2026-11-23", 5.0, "part")]):
            out.append({"session_id": "s%d" % i, "source": "transcript", "role": role, "started": t + "T00:00:00+00:00",
                        "cost_usd": cost, "owner_turns": 2, "tokens": {"input": 1, "output": 10 * (i + 1),
                                                                         "cache_read": 100, "cache_write": 0},
                        "tool_calls": i, "board": {"read_bytes": 100 * i, "write_bytes": 0}, "wall_s": 600})
        return out

    def test_medians_totals_and_n(self):
        res = usage.compare(self.rows(), usage._ts("2026-11-15T00:00:00+00:00"), usage._ts("2026-11-20T00:00:00+00:00"))
        self.assertEqual(res["before"]["part"]["n"], 2)
        self.assertEqual(res["after"]["part"]["n"], 3)
        self.assertEqual(res["before"]["part"]["cost $"]["median"], 3.0)
        self.assertEqual(res["after"]["part"]["cost $"]["median"], 3.0)
        self.assertEqual(res["after"]["part"]["cost $"]["total"], 9.0)
        self.assertEqual(res["after"]["part"]["cost $ per owner turn"]["median"], 1.5)
        self.assertEqual(res["before"]["orchestrator"]["n"], 1)
        only = usage.compare(self.rows(), usage._ts("2026-11-15T00:00:00+00:00"),
                             usage._ts("2026-11-20T00:00:00+00:00"), role="orchestrator")
        self.assertEqual(list(only["after"]), [])
        text = usage.render(res, "B", "A")
        self.assertIn("## part (sessions: before 2, after 3)", text)
        self.assertIn("| cost $ | 3.00 (2) | 3.00 (3) | +0% | 6.00 | 9.00 |", text)

    def test_cli_compare_with_a_date_and_kit_date_wins(self):
        with tempfile.TemporaryDirectory() as d:
            rows = self.rows()
            rows[3]["kit_date"] = "2026-11-12T00:00:00+00:00"           # an old kit still in force: counts as before
            f = os.path.join(d, "rows.jsonl")
            Path(f).write_text("".join(json.dumps(r) + "\n" for r in rows))
            root = project(d)
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--root", root, "compare", "--before", "2026-11-15",
                                "--after", "2026-11-20", "--role", "part", "--rows", f, "--json"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            res = json.loads(r.stdout)
            self.assertEqual((res["before"]["part"]["n"], res["after"]["part"]["n"]), (3, 2))

    def test_a_commit_missing_from_a_shallow_clone_falls_back_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d)
            rows = [{"kit_commit": "abc1234", "kit_date": "2026-11-23T01:25:19+00:00"}]
            t, label = usage.cut_time(root, "abc1234", kit_repo=root, rows=rows)
            self.assertEqual(t.isoformat(), "2026-11-23T01:25:19+00:00")
            self.assertIn("ledger", label)
            t, label = usage.cut_time(root, "abc1234ffff", kit_repo=root, rows=[])   # the manifest's commit
            self.assertEqual(t.isoformat(), "2026-11-19T08:00:00+00:00")
            with self.assertRaises(SystemExit) as e:
                usage.cut_time(root, "dead999", kit_repo=root, rows=rows)
            self.assertIn("give its date", str(e.exception))

    def test_bad_cut_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--root", project(d), "compare", "--before", "nope"],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


class Ccr(unittest.TestCase):
    def test_import_saved_get_session(self):
        rec = {"ccr": {"id": "session_X", "title": "Harmonize orchestrator 53", "created_at": "2026-11-23T21:58:57Z",
                       "updated_at": "2026-11-24T00:42:36Z", "parent_session_id": "session_P",
                       "session_context": {"model": "claude-opus-5-5"},
                       "external_metadata": {"context_usage": {"used_tokens": 345944},
                                             "current_branches": {"": "orchestrator-53"},
                                             "usage": {"cache_read_tokens": 80911625, "cache_write_tokens": 1447786,
                                                       "cost_usd": 27.25751352, "input_tokens": 21920,
                                                       "output_tokens": 162558}}}}
        flat = {"id": "session_Y", "title": "CL30 every agent uses the kit's scripts", "created_at": "2026-11-22T01:00:00Z",
                "updated_at": "2026-11-22T02:00:00Z", "usage": {"output_tokens": 5, "cost_usd": 1.5}, "used_tokens": 9}
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "ccr.jsonl")
            Path(f).write_text(json.dumps(rec) + "\n" + json.dumps(flat) + "\n" + json.dumps({"id": "z"}) + "\n")
            root = project(d)
            rows = usage.import_ccr(root, f)
            self.assertEqual(len(rows), 2)
            o = rows[0]
            self.assertEqual((o["role"], o["cost_usd"], o["context_now"], o["branch"]),
                             ("orchestrator", 27.25751352, 345944, "orchestrator-53"))
            self.assertEqual(o["wall_s"], 2 * 3600 + 43 * 60 + 39)
            self.assertEqual((rows[1]["role"], rows[1]["part"]), ("part", "CL30"))
            self.assertEqual(len(usage.all_rows(root)), 2)


class Install(unittest.TestCase):
    def test_install_wires_the_hook_and_puts_usage(self):
        src = (ROOT / "install.py").read_text()
        self.assertIn('ins.put(".kit/usage.py", rd("scripts", "usage.py"))', src)
        self.assertIn('("SessionEnd", None, "kit_usage.py")', src)
        self.assertEqual(src.count('("Stop", None, "kit_usage.py")'), 2)
        self.assertIn("usage.py", (ROOT / "kit" / "end_session.py").read_text())
        self.assertTrue(HOOK.is_file())


if __name__ == "__main__":
    unittest.main()
