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
        stack.callback(lambda: (proc.terminate(), proc.wait(timeout=60), proc.stdout.close()))
        url = "http://127.0.0.1:" + proc.stdout.readline().strip()
        pushes = proc.stdout.readline().strip()
        return url, pushes

    def test_unknown_repo_is_refused_not_read_from_the_primary(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [{"name": "board", "path": "/nowhere/anon/board", "github": "github:anlonlt/board"}])
            url, _ = self.start(proj)
            api_req(url, "doc/posts/cl32-x", {"data": {"title": "Harmonize's own post"}}, TOKEN, "PUT")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                api_req(url, "doc/posts/cl32-x?repo=board", token=TOKEN)
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(json.load(cm.exception)["error"], "unknown_repo")

    def test_load_attaches_the_artifact_copy_and_launch_reads_it(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [{"name": "board", "path": "/nowhere/anon/board", "github": "github:anlonlt/board"}])
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
            aid, path, why = board_server.resolve_attached(str(proj), {"name": "board", "path": "/nowhere/anon/board"})
            self.assertEqual((aid, path), ("board", os.path.join(str(proj), ".kit", "attached", "board")), why)

    def test_attached_store_never_pushes(self):
        with tempfile.TemporaryDirectory() as t:
            clone = Path(t) / "board"
            (clone / ".git").mkdir(parents=True)
            proj = harmonize(Path(t), [{"name": "board", "path": "/nowhere/anon/board", "github": "github:anlonlt/board"}])
            _, pushes = self.start(proj)
            self.assertEqual(pushes, "board:False")       # found next to the repo; attached, never pushed from here


class Resolve(unittest.TestCase):
    def test_missing_path_finds_the_clone_next_to_the_repo(self):
        with tempfile.TemporaryDirectory() as t:
            proj = harmonize(Path(t), [])
            item = {"name": "board", "path": "/nowhere/anon/board", "github": "github:anlonlt/board"}
            aid, path, why = board_server.resolve_attached(str(proj), item)
            self.assertIsNone(path)
            self.assertIn("/nowhere/anon/board", why)
            self.assertIn("artifact load", why)
            (Path(t) / "board" / ".git").mkdir(parents=True)
            self.assertEqual(board_server.resolve_attached(str(proj), item)[1], str(Path(t) / "board"))


class UpPrompt(unittest.TestCase):
    def test_up_opens_the_orchestrator_by_one_command_and_the_hooks_know_it(self):
        import re
        root = Path(__file__).resolve().parents[1]
        prompts = [m for m in re.findall(r'PROMPT="([^"]*)"', (root / "kit" / "up").read_text()) if "start as the orchestrator" in m]
        self.assertEqual(len(prompts), 3)
        guard = (root / "kit" / "hooks" / "kit_orchestrator_guard.py").read_text()
        rx = re.compile("start as the orchestrator")
        self.assertIn(rx.pattern, guard)
        for q in prompts:
            self.assertIn("kit.py resume --role orchestrator", q)
            self.assertNotIn("Read ", q)                                     # resume prints the guide's part: reading it whole is a second copy
            self.assertNotIn("orchestrator.md", q)
            self.assertIn("you are the orchestrator", q.lower())             # agy's hand-over hook knows it by these words


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

    def test_a_lesson_on_attached_repos_reaches_only_a_project_with_one(self):
        f = self.root / "kit" / "lessons.md"
        f.write_text(f.read_text() + "\n## K9: theirs\nroles: part, orchestrator\nwhere: attached\nText.\n")
        self.assertEqual(self.ids(kit.lessons_for("orchestrator")), ["K1"])
        self.assertNotIn("K9", self.ids(kit.lessons_for("part", [])))
        with mock.patch.object(kit, "_kit_config", lambda *a: {"portfolio": [{"name": "board", "path": "/gone"}]}):
            self.assertEqual(self.ids(kit.lessons_for("orchestrator")), ["K1", "K9"])
        (self.root / "install.py").write_text("")
        (self.root / "kit" / "hooks").mkdir()
        self.assertEqual(self.ids(kit.lessons_for("orchestrator")), ["K1", "K9"])          # the kit's own repo
        shipped = {x["id"]: x["where"] for x in kit.parse_lessons((Path(__file__).resolve().parents[1] / "kit" / "lessons.md").read_text())}
        self.assertEqual({k for k, v in shipped.items() if v == "attached"}, {"K1", "K2", "K6", "K8"})

    def test_resume_names_refused_lessons_once_a_doc(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "O.md").write_text("".join("### Lesson %d: t\nText.\n" % n for n in range(40, 60)))
        (self.root / ".board" / "memory" / "lessons" / "x.md").write_text("## X1: no roles\nText.\n")
        out = kit._refused_lines()
        self.assertEqual(len(out), 2, out)
        self.assertIn("X1", out[0])
        self.assertIn("20 only in docs/O.md (40, 41,", out[1])
        self.assertLess(len("\n".join(out)), 400)
        self.assertEqual(len(kit.lessons_check()), 21)                                        # each with its command

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


class O53Gaps(unittest.TestCase):
    def test_lesson_ids_in_list_items_and_mid_line(self):
        text = ("intro\n2. **Lesson 51a:** launch the kit way\nsee **Lesson 32a** and the rule\n### Lesson 35a\n"
                "a lesson 9 of the course\n")
        self.assertEqual(sorted(kit.DOC_LESSON.findall(text)), ["32a", "35a", "51a"])

    def test_attached_post_found_in_the_pull_without_a_server(self):
        with tempfile.TemporaryDirectory() as t:
            art = Path(t) / "art"
            d = art / "in" / "repos" / "board" / "posts"
            d.mkdir(parents=True)
            (d / "cl33-x.json").write_text(json.dumps({"id": "cl33-x", "version": 3, "data": {"part": "CL33"}}))
            with mock.patch.dict(os.environ, {"KIT_ARTIFACT_DIR": str(art)}), \
                    mock.patch.object(kit, "_attached_boards", side_effect=RuntimeError("no server")), \
                    mock.patch.object(kit, "ROOT", os.path.join(t, "harmonize")), \
                    mock.patch.object(kit, "_kit_config", return_value={"project": "Harmonize"}):
                b, p = kit._find_attached_post("cl33-x")
                r = kit.launch("cl33-x", url="http://127.0.0.1:9", web=True)
            self.assertEqual((b["id"], p["part"]), ("board", "CL33"))
            self.assertIn("git clone", r["error"])            # the right fix, not "run web_takeover.py"
            self.assertNotIn("web_takeover", r["error"])

    def test_web_is_claude_code_whatever_the_project_cli(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": "true"}), \
                mock.patch.object(kit, "_kit_config", return_value={"kit": {"cli": "agy"}}):
            self.assertEqual(kit._active_cli(), "claude")
            self.assertTrue(kit._launch_line("CL33", "docs/prompts/cl33-x.md").startswith("python3 -I .kit/kit.py launch"))


class CloneCheckout(unittest.TestCase):
    def git(self, d, *a):
        return subprocess.run(["git", "-C", str(d)] + list(a), capture_output=True, text=True, check=True).stdout

    def test_dirty_clone_refused_and_branch_restored(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            self.git(t, "init", "-q", "--bare", "remote.git")
            clone = t / "board"
            subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
            for k, v in (("user.email", "t@t"), ("user.name", "t")):
                self.git(clone, "config", k, v)
            (clone / ".kit").mkdir()
            (clone / ".kit" / "kit.py").write_text("print('{\"ok\": true, \"session_id\": \"s\", \"branch\": \"b\"}')\n")
            (clone / "work.txt").write_text("a")
            self.git(clone, "add", ".")
            self.git(clone, "commit", "-qm", "base")
            self.git(clone, "remote", "add", "origin", str(t / "remote.git"))
            self.git(clone, "push", "-q", "origin", "main")
            self.git(clone, "checkout", "-q", "-b", "someone-elses-work")
            host = t / "harmonize"
            (host / "docs" / "prompts").mkdir(parents=True)
            (host / "docs" / "prompts" / "cl33-x.md").write_text("the prompt")
            b = {"id": "board", "github": "anlonlt/board", "clone": str(clone)}
            (clone / "work.txt").write_text("uncommitted")
            with mock.patch.object(kit, "ROOT", str(host)), mock.patch.object(kit, "_github_url", return_value=""), \
                    mock.patch.object(kit, "local_board_url", return_value="http://127.0.0.1:9"):
                r = kit._launch_attached("cl33-x", b, {"part": "CL33"}, web=True)
                self.assertIn("uncommitted changes", r["error"])
                self.git(clone, "checkout", "--", "work.txt")
                r = kit._launch_attached("cl33-x", b, {"part": "CL33"}, web=True)
            self.assertEqual(self.git(clone, "branch", "--show-current").strip(), "someone-elses-work")
            self.assertIn("part-cl33-x", self.git(t / "remote.git", "branch"))


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
                  "the orchestrator, `orch-1`", "## How you work"):
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
        conf = env.pop("_cfg", {}) if "_cfg" in env else {}
        clean.pop("_cfg", None)
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(web_takeover, "cfg", return_value=conf):
            return web_takeover.owns_board()[0]

    def test_a_board_attached_to_another_artifact_is_never_pushed(self):
        """O53: a part's web session on anlonlt/board ran web_takeover and pushed board-mirror (KIT_ATTACHED_CLONE does
        not reach a new session); the kit's board lives in Harmonize's Artifact under repos/board/."""
        att = {"kit": {"artifact": {"url": "https://claude.ai/artifact/X", "prefix": "repos/board/"}}}
        self.assertFalse(self.owns(_cfg=att))
        self.assertTrue(self.owns(_cfg={"kit": {"artifact": {"url": "https://claude.ai/artifact/X", "prefix": ""}}}))

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
