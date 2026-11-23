"""CL32: resume per role, the lessons in the memory, and the gaps O53 hit on 2026-11-23.

- `kit.py resume --role orchestrator|part|clerk|review`: what each role needs and nothing else.
- Lessons live in the kit's memory, tagged with roles; untagged ones and ones only a project doc holds are refused.
- An attached board resolves in any container (Harmonize's portfolio names /home/anon/board): a clone next to the repo,
  or what `artifact load` put in .kit/attached/<id>; else nothing is attached and a `?repo=<id>` read is refused (it
  used to answer with the primary board's posts).
- An attached board's store never pushes its board-mirror; web_takeover pushes only where this session owns the board.
- `kit.py launch` finds a post on an attached board; `launch_text` no longer raises KeyError 'board'.
- `kit.py done` refuses a change to compact/install/resume/hooks/artifact without a real-project result.
- Every orchestrator hand-over starts from `kit.py resume --role orchestrator`, with no reading list.

Run via: python3 -m unittest tests.test_cl32_roles -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import ROOT, TOKEN, api_req, kit  # noqa: E402



def _load(name, rel):
    """By file path: putting kit/board on sys.path would let discovery import its own test_kit_cards.py."""
    spec = importlib.util.spec_from_file_location("cl32_" + name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


board_server = _load("board_server", "kit/board/board_server.py")
handover = _load("handover", "kit/handover.py")
web_takeover = _load("web_takeover", "kit/web_takeover.py")

BOOT_ATTACH = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from board_server import start_server\n"
    "s, store = start_server(sys.argv[2], port=0, token='test-only', auto_sync_interval=60, auto_sync_push=True)\n"
    "print(s.server_port, flush=True)\n"
    "print(','.join('%s:%s' % (k, v['store']._auto_sync_push) for k, v in s._extra_stores.items()), flush=True)\n"
    "s.serve_forever()\n"
)


def harmonize(tmp, portfolio):
    """A Harmonize-shaped project: its config names the kit's board by a path on the owner's computer."""
    proj = tmp / "harmonize"
    (proj / ".kit").mkdir(parents=True)
    (proj / ".kit" / "config.json").write_text(json.dumps({"project": "Harmonize", "portfolio": portfolio}))
    return proj


class Server(unittest.TestCase):
    def start(self, proj):
        stack = ExitStack()
        self.addCleanup(stack.close)
        proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT_ATTACH, str(ROOT / "kit" / "board"), str(proj)],
                                stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (proc.terminate(), proc.wait(timeout=5), proc.stdout.close()))
        url = "http://127.0.0.1:" + proc.stdout.readline().strip()
        pushes = proc.stdout.readline().strip()
        return url, pushes

    def test_unknown_repo_is_refused_not_read_from_the_primary(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [{"name": "board", "path": "/home/anon/board", "github": "github:anlonlt/board"}])
            url, _ = self.start(proj)
            api_req(url, "doc/posts/cl32-x", {"data": {"title": "Harmonize's own post"}}, TOKEN, "PUT")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                api_req(url, "doc/posts/cl32-x?repo=board", token=TOKEN)
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(json.load(cm.exception)["error"], "unknown_repo")

    def test_load_attaches_the_artifact_copy_and_launch_reads_it(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [{"name": "board", "path": "/home/anon/board", "github": "github:anlonlt/board"}])
            url, _ = self.start(proj)
            art = Path(t) / "art"
            d = art / "in" / "repos" / "board" / "posts"
            d.mkdir(parents=True)
            (d / "cl32-x.json").write_text(json.dumps({"title": "the kit's step", "part": "CL32", "train": "cli"}))
            with mock.patch.dict(os.environ, {"KIT_ARTIFACT_DIR": str(art), "KIT_ARTIFACT_PREFIX": ""}):
                got = kit._load_attached(url, TOKEN, str(art / "in"))
            self.assertEqual(got, {"board": 1})
            self.assertEqual(api_req(url, "doc/posts/cl32-x?repo=board", token=TOKEN)["data"]["part"], "CL32")
            self.assertFalse(api_req(url, "doc/posts/cl32-x", token=TOKEN).get("exists"))   # not on Harmonize's own
            # a restart finds it again: .kit/attached/board is a resolved attached board now
            aid, path, why = board_server.resolve_attached(str(proj), {"name": "board", "path": "/home/anon/board"})
            self.assertEqual((aid, path), ("board", os.path.join(str(proj), ".kit", "attached", "board")), why)

    def test_attached_store_never_pushes(self):
        with tempfile.TemporaryDirectory() as t:
            clone = Path(t) / "board"
            (clone / ".git").mkdir(parents=True)
            proj = harmonize(Path(t), [{"name": "board", "path": "/home/anon/board", "github": "github:anlonlt/board"}])
            _, pushes = self.start(proj)
            self.assertEqual(pushes, "board:False")       # found next to the repo; attached, never pushed from here


class Resolve(unittest.TestCase):
    def test_missing_path_finds_the_clone_next_to_the_repo(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [])
            item = {"name": "board", "path": "/home/anon/board", "github": "github:anlonlt/board"}
            aid, path, why = board_server.resolve_attached(str(proj), item)
            self.assertIsNone(path)
            self.assertIn("/home/anon/board", why)
            self.assertIn("artifact load", why)
            (Path(t) / "board" / ".git").mkdir(parents=True)
            self.assertEqual(board_server.resolve_attached(str(proj), item)[1], str(Path(t) / "board"))


class Lessons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "kit").mkdir()
        (self.root / "kit" / "lessons.md").write_text(
            "# lessons\n\n## K1: launch from the clone\nroles: orchestrator\nThe text.\n\n"
            "## K2: real project\nroles: part, review\nfiles: install.py\nDry run.\n\n"
            "## K3: everyone\nroles: part\nReuse.\n")
        (self.root / ".board" / "memory" / "lessons").mkdir(parents=True)
        for k, v in (("ROOT", str(self.root)), ("CONFIG", {})):
            p = mock.patch.object(kit, k, v)
            p.start()
            self.addCleanup(p.stop)

    def ids(self, les):
        return [x["id"] for x in les]

    def test_roles_and_files(self):
        self.assertEqual(self.ids(kit.lessons_for("orchestrator")), ["K1"])
        self.assertEqual(self.ids(kit.lessons_for("part", ["scripts/x.py"])), ["K3"])
        self.assertEqual(self.ids(kit.lessons_for("part", ["install.py"])), ["K2", "K3"])
        self.assertEqual(self.ids(kit.lessons_for("clerk")), [])

    def test_doc_only_lesson_and_untagged_lesson_are_refused(self):
        self.assertEqual(kit.lessons_check(), [])
        (self.root / "docs").mkdir()
        (self.root / "docs" / "ORCHESTRATION.md").write_text("# Orchestration\n\n### Lesson 51a: launch the kit way\nText.\n")
        (self.root / ".board" / "memory" / "lessons" / "x.md").write_text("## X1: no roles\nText.\n")
        err = kit.lessons_check()
        self.assertTrue(any("51a" in e and "docs/ORCHESTRATION.md" in e for e in err), err)
        self.assertTrue(any("X1" in e and "no roles" in e for e in err), err)
        self.assertIn("error", kit.lesson_add("51a", [], "t", "x"))
        r = kit.lesson_add("51a", ["orchestrator"], "launch the kit way", "Text.", frm="docs/ORCHESTRATION.md 51a")
        self.assertTrue(r.get("ok"), r)
        (self.root / ".board" / "memory" / "lessons" / "x.md").write_text("## X1: tagged\nroles: clerk\nText.\n")
        self.assertEqual(kit.lessons_check(), [])
        self.assertEqual(self.ids(kit.lessons_for("orchestrator")), ["K1", "51a"])


class Principles(unittest.TestCase):
    def test_scope_filter_hook(self):
        with tempfile.TemporaryDirectory() as t:
            mem = Path(t) / ".board" / "memory"
            mem.mkdir(parents=True)
            with mock.patch.object(kit, "ROOT", t), mock.patch.object(kit, "CONFIG", {}):
                self.assertEqual(kit.principles_for("part"), [])
                (mem / "principles.md").write_text("- A (scope: all)\n- B (scope: part)\n- C (scope: cli)\n- D (scope: clerk)\n")
                self.assertEqual(kit.principles_for("part", "cli"), ["- A (scope: all)", "- B (scope: part)", "- C (scope: cli)"])
                self.assertEqual(kit.principles_for("clerk"), ["- A (scope: all)", "- D (scope: clerk)"])


class RealProject(unittest.TestCase):
    def repo(self, path, text):
        d = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(d), "config", k, v], check=True)
        (d / "README").write_text("x")
        subprocess.run(["git", "-C", str(d), "add", "."], check=True)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "base"], check=True)
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        (d / path).write_text(text)
        return str(d)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_install_change_needs_a_real_project_result(self):
        root = self.repo("install.py", "print(1)\n")
        with mock.patch.object(kit, "_kit_config", return_value={"kit": {"consumers": [{"github": "anlonlt/harmonize"}]}}):
            err = kit.check_real_project({"verdict": "v"}, root=root)
            self.assertEqual(len(err), 1)
            self.assertIn("install", err[0])
            self.assertIn("anlonlt/harmonize", err[0])
            ok = {"real_project": {"project": "anlonlt/harmonize", "command": "python3 -I install.py . --update",
                                   "output": "update: 0 changed"}}
            self.assertEqual(kit.check_real_project(ok, root=root), [])

    def test_other_change_needs_none(self):
        root = self.repo("kit/board/strings.en.json", "{}\n")
        self.assertEqual(kit.check_real_project({}, root=root), [])


class Launch(unittest.TestCase):
    def test_launch_text_without_board(self):
        r = {"ok": True, "post": "p", "part": "CL32", "session_id": "s", "branch": "b", "trains": True,
             "batches": [], "need_pin": []}
        self.assertIn("CL32 launched", kit.launch_text(r))      # was KeyError: 'board'

    def test_attached_post_without_clone_says_how_to_clone(self):
        b = {"id": "board", "github": "anlonlt/board", "clone": None, "posts": {"cl32-x": {"part": "CL32"}}}
        with mock.patch.object(kit, "_attached_boards", return_value=([b], [])):
            self.assertEqual(kit._find_attached_post("cl32-x")[0]["id"], "board")
            r = kit._launch_attached("cl32-x", b, b["posts"]["cl32-x"])
        self.assertFalse(r["ok"])
        self.assertIn("git clone https://github.com/anlonlt/board", r["error"])

    def test_consumers_are_named_in_the_part_prompt(self):
        with mock.patch.object(kit, "_kit_config", return_value={"kit": {"consumers": ["anlonlt/harmonize"]}}):
            self.assertEqual([c["github"] for c in kit.consumers(["anlonlt/other", "anlonlt/harmonize"])],
                             ["anlonlt/harmonize", "anlonlt/other"])


class Roles(unittest.TestCase):
    POSTS = {
        "_orchestrator": {"session_id": "orch-1"},
        "cl32-x": {"title": "resume per role", "part": "CL32", "train": "cli", "status": "in progress",
                   "session_id": "part-1", "branch": "cl32", "verdict": "v", "bars": ["1 of 2"], "check": "c"},
        "cl31-y": {"title": "done one", "part": "CL31", "train": "cli", "status": "complete", "session_id": "old"},
    }

    def run_role(self, role, post=None):
        with mock.patch.object(kit, "_load_board_docs", return_value=(json.loads(json.dumps(self.POSTS)), {})), \
                mock.patch.object(kit, "_attached_boards", return_value=([], [])), \
                mock.patch.object(kit, "resume", return_value="# Resume (from the board; nothing else carries over)\n"):
            return kit.resume_role(None, role, post=post)

    def test_clerk_gets_routing_and_session_ids_only(self):
        out = self.run_role("clerk")
        self.assertIn("orchestrator: orch-1", out)
        self.assertIn("cl32-x -> part-1", out)
        self.assertNotIn("old", out)
        self.assertNotIn("Lessons", out)

    def test_part_gets_post_prompt_lessons_tests_done(self):
        out = self.run_role("part", "cl32-x")
        for s in ("docs/prompts/cl32-x.md", "Lessons that touch your files", "Your tests", "kit.py done cl32-x",
                  "SendMessage orch-1"):
            self.assertIn(s, out)

    def test_review_gets_step_acceptance_real_project(self):
        out = self.run_role("review", "cl32-x")
        for s in ("The step", "acceptance conditions", "real-project check", "NO real-project result"):
            self.assertIn(s, out)

    def test_orchestrator_gets_resume_launch_lessons_save_points(self):
        out = self.run_role("orchestrator")
        for s in ("# Resume", "How you launch", "attached board", "Lessons for the orchestrator", "Save point"):
            self.assertIn(s, out)


class Ownership(unittest.TestCase):
    def owns(self, **env):
        clean = {k: v for k, v in os.environ.items() if k not in (
            "KIT_ROLE", "KIT_NO_BOARD_PUSH", "KIT_ATTACHED_CLONE", "CLAUDE_PROJECT_DIR")}
        clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            return web_takeover.owns_board()[0]

    def test_who_may_push_the_board_mirror(self):
        self.assertTrue(self.owns())
        self.assertTrue(self.owns(CLAUDE_PROJECT_DIR=web_takeover.ROOT))
        self.assertFalse(self.owns(KIT_ROLE="review"))
        self.assertFalse(self.owns(KIT_ROLE="check"))
        self.assertFalse(self.owns(KIT_ATTACHED_CLONE="1"))
        self.assertFalse(self.owns(CLAUDE_PROJECT_DIR="/home/user/harmonize"))


class HandOver(unittest.TestCase):
    def test_no_reading_list(self):
        for cli in ("claude", "agy"):
            p = handover.successor_prompt("orchestrator", 7, cli=cli)
            self.assertIn("kit.py resume --role orchestrator", p)
            self.assertNotIn("Read ", p)
        for rel in ("kit/claude-cli/orchestrator.template.md", "kit/claude/orchestrator.template.md",
                    "kit/orchestrator.template.md", "kit/claude-cli/takeover.template.md"):
            text = (ROOT / rel).read_text()
            self.assertIn("resume --role orchestrator", text, rel)
            self.assertNotIn("Read `.kit/", text.splitlines()[0], rel)


if __name__ == "__main__":
    unittest.main()
