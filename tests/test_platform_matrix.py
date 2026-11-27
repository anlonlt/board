#!/usr/bin/env python3
"""R5: Every rule that differs by platform says where it applies, checked by a test.

Verifies:
  1. `_platform()` is the one answer to "where does this session run" (web, cli, agy, arena, generic),
     detected from environment and parent process (faked as in a real agy run) and NEVER inferred from config.
  2. `PLATFORM_RULES` declares every platform-dependent rule across all 5 platforms; `_rule()` refuses unknown rows,
     hook.TABLE agrees with the table (cards reminder only where cards == "agent", etc.), and an AST audit of
     scripts/kit.py refuses any ad-hoc platform check outside the table.
  3. `resume`, `done`, `launch` and `step_new` run under all 5 platforms (with an Artifact URL and consumers in config)
     and match `PLATFORM_RULES` on every row.
  4. All part and orchestrator guide templates state that a change made for one platform names its row in PLATFORM_RULES.

Run via: python3 -m unittest tests.test_platform_matrix -v
"""
import ast
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
KIT_PATH = ROOT / "scripts" / "kit.py"

sys.path.insert(0, str(ROOT / "tests"))
from test_launch import POST, TRAINS, Base, make_repo  # noqa: E402
from test_handoff import TOKEN  # noqa: E402

PLATFORM_ENV_VARS = (
    "KIT_PLATFORM", "KIT_CLI", "KIT_EMBED_PREVIEW", "LMARENA", "ARENA_SANDBOX",
    "CLAUDE_CODE_REMOTE", "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_PROJECT_DIR",
    "AGY_PID", "ANTIGRAVITY_AGENT", "ANTIGRAVITY_CONVERSATION_ID",
    "ANTIGRAVITY_AGENTAPI_EXE", "ANTIGRAVITY_APP_DATA_DIR",
    "KIT_STALE_OK", "KIT_ARTIFACT_ON_COMPUTER", "KIT_ARTIFACT_RECONCILE_LOCAL",
)

# Realistic environment + parent process ("_ancestor") fixtures for each platform.
# `agy` fakes a real agy run: ANTIGRAVITY_AGENT=1 + ANTIGRAVITY_CONVERSATION_ID + parent process `agy` (no AGY_PID).
PLATFORM_FIXTURES = {
    "web": ({"CLAUDE_CODE_REMOTE": "true", "CLAUDE_CODE_REMOTE_SESSION_ID": "cse_web01"}, None),
    "cli": ({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "cli-sess-01", "KIT_DEVICE": "dev-box"}, "claude"),
    "agy": ({"ANTIGRAVITY_AGENT": "1", "ANTIGRAVITY_CONVERSATION_ID": "agy-conv-01", "KIT_DEVICE": "dev-box"}, "agy"),
    "arena": ({"KIT_EMBED_PREVIEW": "1"}, None),
    "generic": ({}, None),
}

ART = {"url": "https://claude.ai/artifact/TEST", "prefix": "repos/board/"}

# Functions allowed to read raw platform env vars or inspect _platform() directly in scripts/kit.py.
# Any OTHER function branching on a platform env var, _is_web(), _active_cli(), _ancestor(), or `_platform() == "..."`
# must instead declare a row in PLATFORM_RULES and read it through _rule(...).
RAW_ENV_ALLOWLIST = {
    "_platform", "_norm_platform", "_truthy_env", "_ancestor", "_is_web", "_active_cli", "_rule",
    "whoami", "checkout_fresh_once", "_usage_self", "main",
}
DIRECT_PLATFORM_ALLOWLIST = {
    "_platform", "_is_web", "_active_cli", "_rule", "_launcher", "my_device", "held_lines",
}
GUARDED_ENV_KEYS = {
    "CLAUDE_CODE_REMOTE", "CLAUDECODE", "AGY_PID",
    "ANTIGRAVITY_AGENT", "ANTIGRAVITY_CONVERSATION_ID",
    "KIT_PLATFORM", "KIT_EMBED_PREVIEW", "LMARENA", "ARENA_SANDBOX",
}


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_platform(kit_mod, env_extra=None, ancestor=None):
    """Context manager: clean platform env vars, apply `env_extra`, and fake parent process `_ancestor`."""
    clean = {k: v for k, v in os.environ.items() if k not in PLATFORM_ENV_VARS}
    if env_extra:
        clean.update(env_extra)
    p_env = mock.patch.dict(os.environ, clean, clear=True)
    p_anc = mock.patch.object(kit_mod, "_ancestor", side_effect=lambda name: bool(ancestor and name == ancestor))
    return _Combined(p_env, p_anc)


class _Combined:
    def __init__(self, *cms):
        self.cms = cms

    def __enter__(self):
        return [cm.__enter__() for cm in self.cms]

    def __exit__(self, *exc):
        for cm in reversed(self.cms):
            cm.__exit__(*exc)


def audit_platform_rules_source(src, declared_rows):
    """AST auditor: returns a list of violations where code branches on a platform without a row in PLATFORM_RULES."""
    tree = ast.parse(src)
    issues = []
    platform_literals = {"web", "cli", "agy", "arena", "generic"}

    def visit_fn(fn_name, node):
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                fn = child.func
                fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                # 1. Every _rule("...") must name a declared row in PLATFORM_RULES
                if fname == "_rule" and child.args:
                    arg0 = child.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        if arg0.value not in declared_rows:
                            issues.append("line %d (%s): _rule(%r) has no row in PLATFORM_RULES" % (child.lineno, fn_name, arg0.value))
                # 2. Direct calls to _is_web / _active_cli / _ancestor outside allowlist
                if fname in ("_is_web", "_active_cli", "_ancestor") and fn_name not in DIRECT_PLATFORM_ALLOWLIST:
                    issues.append("line %d (%s): direct call to %s(); declare a row in PLATFORM_RULES and read _rule()" % (
                        child.lineno, fn_name, fname))
                # 3. Direct reads of platform-detection env vars outside allowlist
                if fn_name not in RAW_ENV_ALLOWLIST and fname in ("get", "_truthy_env"):
                    if child.args and isinstance(child.args[0], ast.Constant) and child.args[0].value in GUARDED_ENV_KEYS:
                        issues.append("line %d (%s): direct platform env check %r; use PLATFORM_RULES + _rule()" % (
                            child.lineno, fn_name, child.args[0].value))
            # 4. Direct comparison `_platform(...) == "web"` (or `in (...)`) outside allowlist
            if isinstance(child, ast.Compare) and fn_name not in DIRECT_PLATFORM_ALLOWLIST:
                left = child.left
                is_plat = isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "_platform"
                if is_plat:
                    for comp in child.comparators:
                        if isinstance(comp, ast.Constant) and comp.value in platform_literals:
                            issues.append("line %d (%s): direct _platform() == %r; add a row in PLATFORM_RULES" % (
                                child.lineno, fn_name, comp.value))
                        elif isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                            vals = {e.value for e in comp.elts if isinstance(e, ast.Constant)}
                            if vals & platform_literals:
                                issues.append("line %d (%s): direct _platform() in %r; add a row in PLATFORM_RULES" % (
                                    child.lineno, fn_name, sorted(vals)))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            visit_fn(node.name, node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef):
                    visit_fn("%s.%s" % (node.name, sub.name), sub)
    return issues


class PlatformDetection(unittest.TestCase):
    """Piece 1: _platform() is the one answer to where this session runs, never inferred from config."""

    def setUp(self):
        self.kit = load_mod("kit_pm_det", KIT_PATH)

    def test_platform_never_reads_config(self):
        traps = [
            {"board_url": "https://claude.ai/artifact/X", "kit": {"runtime": "claude", "cli": "claude", "artifact": ART}},
            {"board_url": "http://127.0.0.1:8765", "kit": {"runtime": "agy", "cli": "agy", "artifact": ART}},
            {"board_url": "http://127.0.0.1:8765", "kit": {"runtime": "arena"}},
            {"board_url": "http://127.0.0.1:8765", "kit": {"runtime": "local", "cli": "agy"}},
        ]
        for cfg in traps:
            with mock.patch.object(self.kit, "_kit_config", return_value=cfg), \
                    mock.patch.object(self.kit, "CONFIG", cfg):
                # Clean env + no matching parent process -> always "generic", regardless of config
                with fake_platform(self.kit, {}, ancestor=None):
                    self.assertEqual(self.kit._platform(), "generic", cfg)
                # Every platform fixture resolves to itself regardless of config
                for pl, (env, anc) in PLATFORM_FIXTURES.items():
                    with fake_platform(self.kit, env, ancestor=anc):
                        self.assertEqual(self.kit._platform(), pl, (pl, cfg))

    def test_platform_env_and_parent_process_signals(self):
        cases = [
            ("web", {"CLAUDE_CODE_REMOTE": "true"}, None),
            ("web", {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_abc"}, None),
            ("web", {"CLAUDE_CODE_REMOTE": "true"}, "agy"),                 # web wins over agy ancestor
            ("cli", {"CLAUDECODE": "1"}, None),
            ("cli", {"CLAUDE_CODE_SESSION_ID": "s1"}, None),
            ("cli", {"CLAUDE_PID": "99"}, None),
            ("cli", {"CLAUDE_PROJECT_DIR": "/tmp"}, None),
            ("cli", {"KIT_CLI": "claude"}, None),
            ("cli", {}, "claude"),                                          # parent process claude
            ("cli", {"CLAUDECODE": "1"}, "agy"),                            # explicit CLAUDECODE wins over agy ancestor (e17e953)
            ("agy", {"AGY_PID": "1234"}, None),
            ("agy", {"ANTIGRAVITY_AGENT": "1"}, None),                      # real agy session without AGY_PID
            ("agy", {"ANTIGRAVITY_CONVERSATION_ID": "conv-1"}, None),
            ("agy", {"KIT_CLI": "agy"}, None),
            ("agy", {}, "agy"),                                             # parent process agy alone (no env vars)
            ("arena", {"KIT_EMBED_PREVIEW": "1"}, None),
            ("arena", {"LMARENA": "1"}, None),
            ("arena", {"ARENA_SANDBOX": "1"}, None),
            ("generic", {}, None),
            ("generic", {"KIT_PLATFORM": "generic"}, "agy"),                # explicit KIT_PLATFORM wins
        ]
        for want, env, anc in cases:
            with fake_platform(self.kit, env, ancestor=anc):
                self.assertEqual(self.kit._platform(), want, (want, env, anc))


class TableContract(unittest.TestCase):
    """Piece 2 & Piece 3: PLATFORM_RULES schema, hook parity, and AST refusal of un-rowed rules."""

    def setUp(self):
        self.kit = load_mod("kit_pm_tbl", KIT_PATH)
        self.hook = load_mod("hook_pm_tbl", ROOT / "kit" / "hook.py")

    def test_every_row_declares_every_platform(self):
        expected_platforms = set(self.kit.PLATFORMS)
        self.assertEqual(expected_platforms, {"web", "cli", "agy", "arena", "generic"})
        required_rows = {
            "artifact_steps", "stale_gate", "device_cap_gate", "real_project_run",
            "cards", "launcher", "reply_format", "hook_runtime",
            "task_list", "alive_check", "part_template", "orchestrator_guide",
            "consultant_template", "checkout_refetch", "welcome_runtime", "owner_surface",
        }
        self.assertTrue(required_rows <= set(self.kit.PLATFORM_RULES), required_rows - set(self.kit.PLATFORM_RULES))
        for name, row in self.kit.PLATFORM_RULES.items():
            self.assertEqual(set(row.keys()), expected_platforms, "row %r must declare every platform" % name)

    def test_unknown_rule_is_refused(self):
        with self.assertRaises(KeyError):
            self.kit._rule("unregistered_platform_rule")

    def test_hook_table_and_cards_agent_agree_with_platform_rules(self):
        hook_rts = {r for r in self.kit.PLATFORM_RULES["hook_runtime"].values() if r}
        self.assertEqual(set(self.hook.RUNTIMES), hook_rts)
        for pl in self.kit.PLATFORMS:
            rt = self.kit.PLATFORM_RULES["hook_runtime"][pl]
            cards = self.kit.PLATFORM_RULES["cards"][pl]
            if rt is None:
                continue
            wired_hooks = {r[2] for r in self.hook.rows(rt)}
            # Leak #8: kit_cards_reminder.py belongs ONLY where cards == "agent" (web), never CLI or agy
            self.assertEqual("kit_cards_reminder.py" in wired_hooks, cards == "agent", (pl, rt, cards))
        # Leak #7 (106bba6): web's kit-cards agent template stays shipped
        self.assertTrue((ROOT / "kit" / "claude" / "kit-cards.md").is_file())

    def test_no_unrowed_platform_checks_in_kit_py(self):
        src = KIT_PATH.read_text(encoding="utf-8")
        issues = audit_platform_rules_source(src, set(self.kit.PLATFORM_RULES))
        self.assertEqual(issues, [], "\n".join(issues))

    def test_auditor_refuses_new_unrowed_rules(self):
        bad_snippets = [
            'def foo():\n    if _platform() == "web":\n        return 1\n',
            'def foo():\n    if os.environ.get("CLAUDE_CODE_REMOTE"):\n        return 1\n',
            'def foo():\n    if _is_web():\n        return 1\n',
            'def foo():\n    return _rule("brand_new_unregistered_row")\n',
        ]
        for snippet in bad_snippets:
            self.assertTrue(audit_platform_rules_source(snippet, set(self.kit.PLATFORM_RULES)),
                            "auditor failed to refuse: %s" % snippet)


class ResumeMatrix(unittest.TestCase):
    """Runs resume, stale_copy, and resume_role across all 5 platforms and compares against PLATFORM_RULES."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        (self.root / ".kit" / "claude-web").mkdir(parents=True)
        (self.root / ".kit" / "claude-cli").mkdir(parents=True)
        # Copy real part & orchestrator templates so rendered outputs reflect real kit content
        (self.root / ".kit" / "part.md").write_text((ROOT / "kit" / "part.template.md").read_text(encoding="utf-8"))
        (self.root / ".kit" / "claude-cli" / "part.md").write_text((ROOT / "kit" / "claude-cli" / "part.template.md").read_text(encoding="utf-8"))
        (self.root / ".kit" / "claude-web" / "part.md").write_text((ROOT / "kit" / "claude" / "part.template.md").read_text(encoding="utf-8"))
        (self.root / ".kit" / "orchestrator.md").write_text((ROOT / "kit" / "orchestrator.template.md").read_text(encoding="utf-8"))
        (self.root / ".kit" / "claude-cli" / "orchestrator.md").write_text((ROOT / "kit" / "claude-cli" / "orchestrator.template.md").read_text(encoding="utf-8"))
        (self.root / ".kit" / "claude-web" / "orchestrator.md").write_text((ROOT / "kit" / "claude" / "orchestrator.template.md").read_text(encoding="utf-8"))
        # Trap config: names an Artifact URL, consumers, and cli=agy
        self.cfg = {
            "project": "Board",
            "board_url": "http://127.0.0.1:8765",
            "kit": {
                "runtime": "local",
                "cli": "agy",
                "artifact": dict(ART),
                "consumers": [{"github": "anlonlt/harmonize"}],
            },
        }
        (self.root / ".kit" / "config.json").write_text(json.dumps(self.cfg))
        for a in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(["git", "-C", str(self.root)] + a, check=True, capture_output=True)
        self.db = str(self.root / ".kit" / "board.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT)")
        self.posts = {
            "_orchestrator": {"session_id": "orch-dev-1", "device": "dev-box"},
            "r5-step": {
                "title": "Matrix step", "part": "R5", "train": "runners", "column": "info",
                "status": "in progress", "session_id": "part-1", "branch": "r5",
                "orchestrator": "orch-dev-1", "orchestrator_name": "board-orchestrator", "orchestrator_device": "dev-box",
                "created_at": "2026-11-26T10:00:00Z", "body": "do it",
            },
        }
        for pid, doc in self.posts.items():
            con.execute("INSERT INTO docs VALUES ('posts', ?, 1, ?, '2026-11-26T10:00:00Z')", (pid, json.dumps(doc)))
        con.commit()
        con.close()
        self.kit = load_mod("kit_pm_res", KIT_PATH)
        self.kit.ROOT = str(self.root)
        self.kit.CONFIG = dict(self.cfg)

    def test_resume_stale_and_roles_match_table_on_every_platform(self):
        for pl, (env, anc) in PLATFORM_FIXTURES.items():
            with self.subTest(pl), fake_platform(self.kit, env, ancestor=anc), \
                    mock.patch.object(self.kit, "_kit_config", return_value=self.cfg), \
                    mock.patch.object(self.kit, "_art_dir", return_value=str(self.root / "art")), \
                    mock.patch.object(self.kit, "_alive_sessions", return_value=set()) as m_alive, \
                    mock.patch.object(self.kit, "_attached_boards", return_value=([], [])), \
                    mock.patch.object(self.kit, "take_step_branch", return_value=[]), \
                    mock.patch.object(self.kit, "real_project_areas", return_value=["resume"]):
                self.assertEqual(self.kit._platform(), pl)
                # 1. STALE gate matches PLATFORM_RULES['stale_gate']
                stale = self.kit.stale_copy()
                self.assertEqual("STALE" in stale, self.kit.PLATFORM_RULES["stale_gate"][pl], (pl, stale))
                # 2. resume() footer & alive check match PLATFORM_RULES['task_list'] and ['alive_check']
                res = self.kit.resume(str(self.root / "m"), db_path=self.db)
                self.assertEqual(m_alive.called, self.kit.PLATFORM_RULES["alive_check"][pl] == "claude_agents", pl)
                tlist = self.kit.PLATFORM_RULES["task_list"][pl]
                self.assertEqual("TaskCreate" in res, tlist == "claude_tasks", (pl, res))
                self.assertEqual("Resume in agy" in res, tlist == "agy_step", (pl, res))
                self.assertEqual("no session can be started here" in res, tlist == "step_cli", (pl, res))
                # 3. resume_role('orchestrator') workflow matches launcher, artifact_steps, cards
                orch = self.kit.resume_role(str(self.root / "m"), "orchestrator", db_path=self.db)
                lmode = self.kit.PLATFORM_RULES["launcher"][pl]
                self.assertEqual("`create_session` call" in orch, lmode == "create_session", pl)
                self.assertEqual("starts `claude --bg`" in orch, lmode == "claude_bg", pl)
                self.assertEqual("prints the `invoke_subagent` call" in orch, lmode == "invoke_subagent", pl)
                self.assertEqual("no session can be started here" in orch, lmode == "self", pl)
                self.assertEqual("ArtifactData batch launch prints" in orch, self.kit.PLATFORM_RULES["artifact_steps"][pl], pl)
                self.assertEqual("rebuild with the `kit-cards` agent" in orch, self.kit.PLATFORM_RULES["cards"][pl] == "agent", pl)
                # 4. resume_role('part') matches part_template, real_project_run, reply_format
                part = self.kit.resume_role(str(self.root / "m"), "part", post="r5-step", db_path=self.db)
                self.assertEqual("`kit.py artifact load`" in part, self.kit.PLATFORM_RULES["artifact_steps"][pl], pl)
                self.assertEqual("ArtifactData get" in part, self.kit.PLATFORM_RULES["real_project_run"][pl] == "orchestrator", pl)
                rfmt = self.kit.PLATFORM_RULES["reply_format"][pl]
                where = part.split("## Where you report", 1)[1].split("\n## ", 1)[0]
                self.assertEqual("`SendMessage`" in where, rfmt in ("claude_cli", "claude_remote"), (pl, where))
                self.assertEqual("`send_message` to `orch-dev-1`" in where, rfmt == "agy", (pl, where))
                self.assertEqual("no inter-session message tool" in where, rfmt == "none", (pl, where))


class DoneMatrix(unittest.TestCase):
    """Runs done() across all 5 platforms and compares artifact push, real_project_run, and reply_format to the table."""

    def _make_kit(self, post, d):
        kit = load_mod("kit_pm_done", KIT_PATH)
        kit._get_post = lambda pid, db_path=None: post
        kit.send_form = lambda pid: []
        kit.attached_board = lambda: dict(ART)
        kit.artifact_url = lambda: ART["url"]
        kit.check_asked = lambda p, db_path=None: ([], [])
        kit.check_his_words = lambda pid, p, db_path=None: ([], [])
        kit.check_done = lambda p, root=None, body_max=1500: ([], [])
        kit.plain_notes = lambda p: []
        kit.real_project_areas = lambda root=None, base=None: ["resume"]
        kit.consumers = lambda extra=None: [{"github": "anlonlt/harmonize", "name": "harmonize"}]
        kit.check_trunk_merged = lambda root=None: []
        kit.prove_check = lambda p, root=None, timeout=900: ([], [])
        kit._post_gate = lambda: None
        kit._art_dir = lambda: os.path.join(d, "art")
        kit._mirror_path = lambda: os.path.join(d, "mirror")
        return kit

    def test_done_matches_table_on_every_platform(self):
        post = {"title": "x", "status": "complete", "verdict": "v", "updated_at": "2026-11-26T20:00:00Z"}
        for pl, (env, anc) in PLATFORM_FIXTURES.items():
            with self.subTest(pl), tempfile.TemporaryDirectory() as d:
                kit = self._make_kit(post, d)
                mp = Path(d) / "mirror" / "board" / "posts"
                mp.mkdir(parents=True)
                (mp / "p1.json").write_text(json.dumps(post))
                with fake_platform(kit, env, ancestor=anc):
                    rc, out = kit.done("p1")
                    art = kit.PLATFORM_RULES["artifact_steps"][pl]
                    # Artifact push gate applies iff artifact_steps is True
                    self.assertEqual(rc, 1 if art else 0, (pl, out))
                    self.assertEqual("artifact sync" in out, art, (pl, out))
                    # real_project wording matches PLATFORM_RULES['real_project_run']
                    rp_mode = kit.PLATFORM_RULES["real_project_run"][pl]
                    self.assertEqual("a part never attaches a repo (CL38)" in out, rp_mode == "orchestrator", (pl, out))
                    self.assertEqual("in a local clone" in out, rp_mode == "computer", (pl, out))
                    # Next messaging tool matches PLATFORM_RULES['reply_format'] when done passes
                    if not art:
                        rfmt = kit.PLATFORM_RULES["reply_format"][pl]
                        self.assertEqual("SendMessage" in out, rfmt == "claude_cli", (pl, out))
                        self.assertEqual("send_message" in out, rfmt in ("claude_remote", "agy"), (pl, out))
                        self.assertEqual("is complete on the board" in out, rfmt == "none", (pl, out))

    def test_real_project_part_mode_refuses_done(self):
        post = {"title": "x", "status": "complete", "verdict": "v"}
        with tempfile.TemporaryDirectory() as d:
            kit = self._make_kit(post, d)
            mp = Path(d) / "mirror" / "board" / "posts"
            mp.mkdir(parents=True)
            (mp / "p1.json").write_text(json.dumps(post))
            patched = dict(kit.PLATFORM_RULES["real_project_run"], cli="part")
            with fake_platform(kit, *PLATFORM_FIXTURES["cli"]), \
                    mock.patch.dict(kit.PLATFORM_RULES, {"real_project_run": patched}):
                rc, out = kit.done("p1")
                self.assertEqual(rc, 1, out)
                self.assertIn("fix: real_project:", out)


class LaunchAndStepNewMatrix(Base):
    """Runs step_new and launch under all 5 platforms against a real local board and compares against PLATFORM_RULES."""

    def setUp(self):
        super().setUp()
        self.cfg = {
            "project": "Board",
            "board_url": self.url,
            "kit": {
                "runtime": "local",
                "cli": "agy",
                "artifact": dict(ART),
                "consumers": [{"github": "anlonlt/harmonize"}],
            },
        }
        self.repo = make_repo(self.tmp / "repo", self.cfg)
        for rel in ("kit/board/board_cli.py", "kit/board/board_token.py", "kit/board/post_gate.py"):
            shutil.copy(str(ROOT / rel), str(self.repo / ".kit" / Path(rel).name))
        (self.repo / ".kit" / "part.md").write_text((ROOT / "kit" / "part.template.md").read_text(encoding="utf-8"))
        (self.repo / ".kit" / "claude-cli").mkdir(exist_ok=True)
        (self.repo / ".kit" / "claude-cli" / "part.md").write_text((ROOT / "kit" / "claude-cli" / "part.template.md").read_text(encoding="utf-8"))
        (self.repo / ".kit" / "claude-web").mkdir(exist_ok=True)
        (self.repo / ".kit" / "claude-web" / "part.md").write_text((ROOT / "kit" / "claude" / "part.template.md").read_text(encoding="utf-8"))
        os.environ["KIT_BOARD_URL"] = self.url
        os.environ["KIT_BOARD_TOKEN"] = TOKEN
        self.local("posts/_trains", dict(TRAINS, trains={"cli": {"goal": "matrix test train", "created_at": "2026-11-26T00:00:00Z"}}))
        self.local("posts/_orchestrator", {"title": "Ask", "column": "info", "session_id": "orch-1"})
        # Seed a lower-priority running step touching scripts/kit.py so step_new with --damage emits next_pause
        self.local("posts/p0-running", {
            "title": "low feature", "part": "P0", "train": "cli", "column": "info",
            "status": "in progress", "session_id": "sess-p0", "kind": "feature",
            "body": "edit scripts/kit.py for feature", "task": "edit scripts/kit.py for feature",
            "created_at": "2026-11-26T00:00:00Z",
        })
        self.bindir_claude = self.tmp / "bin-claude"
        self.bindir_claude.mkdir()
        (self.bindir_claude / "claude").write_text("#!/bin/sh\nexit 0\n")
        (self.bindir_claude / "claude").chmod(0o755)
        for real in ("git", "sh", "env"):
            src = shutil.which(real)
            if src:
                os.symlink(src, self.bindir_claude / real)
        self.bindir_empty = self.tmp / "bin-empty"
        self.bindir_empty.mkdir()
        for real in ("git", "sh", "env"):
            src = shutil.which(real)
            if src:
                os.symlink(src, self.bindir_empty / real)

    def test_step_new_matches_table_on_every_platform(self):
        k = self.kit_in(self.repo)
        for idx, (pl, (env, anc)) in enumerate(PLATFORM_FIXTURES.items(), 1):
            with self.subTest(pl), fake_platform(k, env, ancestor=anc):
                r = k.step_new("cli", "PM%d" % idx, "matrix check %s" % pl, task="fix scripts/kit.py",
                               damage="lost data (owner)")
                self.assertNotIn("error", r, r)
                lmode = k.PLATFORM_RULES["launcher"][pl]
                if lmode == "invoke_subagent":
                    self.assertIn("invoke_subagent", r["launch"], pl)
                elif lmode in ("create_session", "claude_bg"):
                    self.assertIn("kit.py launch", r["launch"], pl)
                else:
                    self.assertIn("work on a branch of its own", r["launch"], pl)
                    self.assertNotIn("invoke_subagent", r["launch"], pl)
                    self.assertNotIn("claude --bg", r["launch"], pl)
                rfmt = k.PLATFORM_RULES["reply_format"][pl]
                np = r.get("next_pause") or ""
                self.assertEqual("SendMessage" in np, rfmt == "claude_cli", (pl, np))
                self.assertEqual("claude-code-remote" in np, rfmt == "claude_remote", (pl, np))
                self.assertEqual("to its conversation" in np, rfmt == "agy", (pl, np))
                self.assertEqual("no inter-session message" in np, rfmt == "none", (pl, np))
                self.local("posts/" + r["post"], {"title": "done", "removed": True})

    def test_launch_matches_table_on_every_platform(self):
        k = self.kit_in(self.repo)
        for idx, (pl, (env, anc)) in enumerate(PLATFORM_FIXTURES.items(), 1):
            pid = "cl99-launch-me"
            self.local("posts/" + pid, dict(POST, part="CL99", status="planned", session_id=None, launched_at=None))
            pushed = []

            def fake_push(*a, **kw):
                pushed.append(pl)
                return {"batches": [], "need_pin": [], "board": ART["url"]}

            # Give CLI fake `claude` on PATH; arena/generic/agy get no `claude` so self/agy refusal is exercised cleanly
            path_dir = str(self.bindir_claude) if pl == "cli" else str(self.bindir_empty)
            with self.subTest(pl), fake_platform(k, dict(env, PATH=path_dir), ancestor=anc), \
                    mock.patch.object(k, "artifact_push", side_effect=fake_push), \
                    mock.patch.object(k, "_device", return_value=None):
                r = k.launch(pid, url=self.url, token=TOKEN, gate=True, reset=True, anyway="matrix test")
                lmode = k.PLATFORM_RULES["launcher"][pl]
                art = k.PLATFORM_RULES["artifact_steps"][pl]
                if lmode == "create_session":
                    self.assertTrue(r["ok"], r)
                    self.assertIn("create_session", r)
                    self.assertIn("Never attach a repo", r["create_session"]["prompt"])
                    self.assertEqual(pushed, [pl])
                elif lmode == "claude_bg":
                    self.assertTrue(r["ok"], r)
                    self.assertIn("worktree", r)
                    self.assertEqual(pushed, [])
                else:
                    # agy / arena / generic without claude: loud refusal, never an OSError, never touches Artifact
                    self.assertFalse(r["ok"], r)
                    self.assertIn("NOT STARTED", r["error"])
                    self.assertEqual(pushed, [])
                self.assertEqual(bool(pushed), art, pl)


class GuidesNameTheTable(unittest.TestCase):
    """Piece 4: part and orchestrator guides state that a change made for one platform names its row in the table."""

    GUIDES = [
        "kit/part.template.md",
        "kit/claude-cli/part.template.md",
        "kit/claude/part.template.md",
        "kit/orchestrator.template.md",
        "kit/claude-cli/orchestrator.template.md",
        "kit/claude/orchestrator.template.md",
        "kit/arena/orchestrator.template.md",
        "kit/generic/orchestrator.template.md",
    ]

    def test_every_part_and_orchestrator_guide_names_platform_rules(self):
        for rel in self.GUIDES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("PLATFORM_RULES", text, rel)
            self.assertIn("one platform", text.lower(), rel)


if __name__ == "__main__":
    unittest.main()
