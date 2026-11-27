"""A new install on an existing project (another default branch, no Python, rules of its own): what each platform's sessions
are told is there, runs there, and is said once. Each case was met on a real project first.

Run via: python3 -m unittest tests.test_new_install -v
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS = {"cli": ["--cli", "claude"], "agy": ["--cli", "agy"], "web": ["--runtime", "claude"],
         "arena": ["--runtime", "arena", "--setup-done"], "generic": ["--runtime", "generic", "--setup-done"]}
ENV = {"cli": {"CLAUDECODE": "1"}, "agy": {"AGY_PID": "1"}, "web": {"CLAUDECODE": "1", "CLAUDE_CODE_REMOTE": "true"},
       "arena": {"KIT_EMBED_PREVIEW": "1"}, "generic": {"KIT_PLATFORM": "generic"}}
AGY = re.compile(r"invoke_subagent|define_subagent|run_command|WaitMsBeforeAsync")
TRUNK = "work/app-x1"


class NewInstall(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="kit-new-"))
        self.addCleanup(shutil.rmtree, str(self.t), ignore_errors=True)
        (self.t / "stub").mkdir()
        for b in ("claude", "agy"):
            (self.t / "stub" / b).write_text("#!/bin/sh\necho '[]'\n")
            (self.t / "stub" / b).chmod(0o755)
        keep = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "KIT_", "AGY", "ANTIGRAVITY", "LMARENA", "ARENA_", "GIT_", "XDG"))}
        path = ":".join(p for p in keep["PATH"].split(":") if not any(os.path.exists(os.path.join(p, b)) for b in ("claude", "agy")))
        self.base = dict(keep, PATH=path, HOME=str(self.t / "home"), KIT_NO_FUNNEL="1", KIT_USAGE_PUSH="0", KIT_BIN_DIR=str(self.t / "bin"),
                         KIT_BOARD_URL="http://127.0.0.1:9", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        (self.t / "home").mkdir()

    def install(self, pl):
        d = self.t / pl
        d.mkdir()
        (d / "CLAUDE.md").write_text("# Regles\n")
        (d / ".gitignore").write_text("node_modules/\n")
        (d / "package.json").write_text("{}\n")
        env = dict(self.base, **ENV[pl])
        if pl in ("cli", "agy"):
            env["PATH"] = "%s:%s" % (self.t / "stub", env["PATH"])
        self.d, self.env = d, env
        self.git("init", "-q", "-b", TRUNK)
        self.git("add", "-A")
        self.git("commit", "-qm", "1")
        self.git("clone", "-q", "--bare", str(d), str(self.t / (pl + ".git")))
        self.git("remote", "add", "origin", str(self.t / (pl + ".git")))
        r = self.run_([str(ROOT / "install.py"), ".", "--project", "Jardin", "--talk", "fr", "--no-survey", "t", "--no-cmd"] + FLAGS[pl])
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.git("add", "-A")
        self.git("commit", "-qm", "kit")
        self.git("push", "-q", "origin", "HEAD")
        return d

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.d)] + list(a), env=self.env, capture_output=True, text=True, check=True).stdout

    def run_(self, a, cwd=None):
        return subprocess.run([sys.executable, "-I"] + a, cwd=str(cwd or self.d), env=self.env, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def kit(self, *a, cwd=None):
        return self.run_([".kit/kit.py"] + list(a), cwd)

    def test_what_resume_says_is_this_platforms(self):
        commands = set(re.findall(r'sp\.add_parser\("([\w-]+)"', (ROOT / "scripts" / "kit.py").read_text()))
        for pl in FLAGS:
            with self.subTest(pl):
                d = self.install(pl)
                r = self.kit("resume", "--role", "orchestrator")
                self.assertEqual(r.returncode, 0, r.stderr)
                out = r.stdout
                self.assertEqual(set(re.findall(r"kit\.py ([a-z][a-z-]+)", out)) - commands, set())
                self.assertNotIn("{{", out)
                self.assertNotIn("Read `.kit", out)
                self.assertNotIn("A step on an attached board", out)                         # nothing is attached
                if pl != "agy":
                    self.assertIsNone(AGY.search(out), AGY.search(out))
                if pl != "cli":
                    self.assertNotIn("claude --bg", out)
                if pl in ("arena", "generic"):
                    self.assertIn("no session can be started here", out)
                for p in set(re.findall(r"(?<![\w/])(\.kit/[\w/.-]+\.(?:py|md))", out)) - {".kit/web_takeover.py"}:
                    self.assertTrue((d / p).exists(), "%s names %s" % (pl, p))

    def test_a_step_gets_its_platforms_prompt(self):
        code = ("import importlib.util as u; s=u.spec_from_file_location('k','.kit/kit.py'); k=u.module_from_spec(s); s.loader.exec_module(k); "
                "print(k._write_step_prompt('p1', {'part':'P1','title':'one','train':'t','body':'x'}, []))")
        for pl in ("cli", "agy", "web", "generic"):
            with self.subTest(pl):
                d = self.install(pl)
                r = self.run_(["-c", code])
                f = d / "docs" / "prompts" / "p1.md"
                self.assertTrue(f.is_file(), r.stdout + r.stderr)
                self.assertLess(len(f.read_text()), 600)                                     # the task and his words; how a part works is the kit's
                text = self.run_(["-c", code.split("print(")[0] + "print('\\n'.join(k.part_prompt('p1', {'part':'P1','title':'one','train':'t','body':'x'})))"]).stdout
                self.assertEqual(text.count("## The task"), 1)
                self.assertNotIn("Run `python3 -I .kit/kit.py resume --role part", text)     # it is what printed this
                self.assertEqual("`python3 -I .kit/kit.py artifact sync --post" in text, pl == "web", pl)   # CL72: the one door
                self.assertEqual("claude-code-remote" in text, pl == "web", pl)
                self.assertNotIn("<PART>", text)

    def test_the_files_of_a_branch_are_against_this_projects_trunk(self):
        d = self.install("cli")
        self.git("checkout", "-qb", "p1")
        (d / "a.js").write_text("1\n")
        self.git("add", "a.js")
        self.git("commit", "-qm", "a")
        r = self.run_(["-c", "import importlib.util as u; s=u.spec_from_file_location('k','.kit/kit.py'); k=u.module_from_spec(s); s.loader.exec_module(k); print(k._changed_files())"])
        self.assertEqual(r.stdout.strip(), "['a.js']", r.stderr)

    def test_a_part_prompt_names_no_missing_section(self):
        for tpl, board in (("kit/part.template.md", "kit/board/BOARD.template.md"), ("kit/claude-cli/part.template.md", "kit/board/BOARD.template.md"),
                           ("kit/claude/part.template.md", "kit/claude/BOARD.template.md")):
            heads = re.findall(r"(?m)^## (.+)$|^\*\*(Sections fold)", (ROOT / board).read_text())
            have = " | ".join(a or b for a, b in heads)
            for sec in re.findall(r"BOARD\.md` \u00a7 ([A-Z][\w ]+)", (ROOT / tpl).read_text()):
                self.assertIn(sec.strip(), have, tpl)

    def test_nothing_the_kit_builds_is_committed(self):
        d = self.install("cli")
        for p in (".kit/__pycache__/x.pyc", ".kit/hooks/__pycache__/x.pyc", ".claude/hooks/kit/__pycache__/x.pyc", ".agents/hooks/kit/__pycache__/x.pyc",
                  ".claude/worktrees/p1/a.js"):
            (d / p).parent.mkdir(parents=True, exist_ok=True)
            (d / p).write_text("x")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("ls-files", "CLAUDE.md").strip(), "CLAUDE.md")
        self.assertEqual((d / "CLAUDE.md").read_text(), "# Regles\n")                      # his rules are untouched

    def test_a_clone_without_the_board_says_so(self):
        d = self.install("web")
        e = self.git("commit-tree", self.git("hash-object", "-w", "-t", "tree", "/dev/null").strip(), "-m", "m").strip()
        self.git("push", "-q", "origin", e + ":refs/heads/board-mirror")
        c = self.t / "clone"
        self.git("clone", "-q", str(self.t / "web.git"), str(c))
        out = self.kit("resume", "--role", "orchestrator", cwd=c).stdout
        self.assertIn("THIS CLONE HAS NO BOARD YET", out)
        self.assertNotIn("THIS CLONE HAS NO BOARD YET", self.kit("resume", "--role", "orchestrator", cwd=d).stdout if (d / ".kit/board_data/board.db").exists() else "")

    def test_no_session_is_sent_to_read_a_guide_resume_prints(self):
        for f in ("kit/up", "kit/AGENTS.template.md", "kit/arena/orchestrator.template.md", "kit/generic/orchestrator.template.md", "kit/skills/board/SKILL.md",
                  "kit/claude/SKILL.board.md", "kit/claude/clerk.template.md", "kit/hooks/kit_session_start.py"):
            t = (ROOT / f).read_text()
            self.assertIsNone(re.search(r"[Rr]ead[s]?\s[^.\n]{0,80}(?:orchestrator|ORCHESTRATOR)\.md", t), f)
            self.assertNotIn("orchestrator.md: read it and follow it", t, f)
        for f in ("kit/ORCHESTRATOR.template.md", "kit/claude/ORCHESTRATOR.template.md", "kit/claude-cli/ORCHESTRATOR.template.md"):
            self.assertNotIn("rerunning one of its numbers yourself", (ROOT / f).read_text(), f)       # the review does


if __name__ == "__main__":
    unittest.main()
