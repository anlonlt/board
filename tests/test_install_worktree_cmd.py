"""A1: install_cmd skips linked git worktrees so ~/.local/bin/<cmd> is never overwritten by a part.

Run via: python3 -m unittest tests.test_install_worktree_cmd -v
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load_install():
    spec = importlib.util.spec_from_file_location("kit_install_a1", str(ROOT / "install.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


install_mod = _load_install()


def _git(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return r.stdout


def _make_repo_and_worktree(base):
    main = Path(base) / "main-repo"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "test@example.com")
    _git(main, "config", "user.name", "Test")
    (main / "README.md").write_text("hello\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "commit", "-q", "-m", "init")
    wt = Path(base) / "linked-wt"
    _git(main, "worktree", "add", "-q", "-b", "part-a1", str(wt))
    return main, wt


class IsGitWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kit-a1-wt-")
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_linked_worktree_root_and_subdir_but_not_main_checkout(self):
        main, wt = _make_repo_and_worktree(self.base)
        main_sub = main / "sub"
        main_sub.mkdir()
        wt_sub = wt / "sub"
        wt_sub.mkdir()

        self.assertFalse(install_mod.is_git_worktree(str(main)))
        self.assertFalse(install_mod.is_git_worktree(str(main_sub)))
        self.assertTrue(install_mod.is_git_worktree(str(wt)))
        # Subdirectory of a linked worktree has no .git file directly inside it;
        # git rev-parse --git-dir vs --git-common-dir catches it.
        self.assertFalse((wt_sub / ".git").exists())
        self.assertTrue(install_mod.is_git_worktree(str(wt_sub)))

    def test_detects_dot_git_file_and_agent_worktree_paths(self):
        plain = self.base / "plain"
        plain.mkdir()
        self.assertFalse(install_mod.is_git_worktree(str(plain)))

        dotgit_file = self.base / "dotgit-file"
        dotgit_file.mkdir()
        (dotgit_file / ".git").write_text("gitdir: /nonexistent/.git/worktrees/x\n", encoding="utf-8")
        self.assertTrue(install_mod.is_git_worktree(str(dotgit_file)))

        agy_wt = self.base / "home" / ".gemini" / "antigravity-cli" / "worktrees" / "repo" / "p1"
        agy_wt.mkdir(parents=True)
        self.assertTrue(install_mod.is_git_worktree(str(agy_wt)))

        claude_wt = self.base / "repo" / ".claude" / "worktrees" / "p1"
        claude_wt.mkdir(parents=True)
        self.assertTrue(install_mod.is_git_worktree(str(claude_wt)))


class InstallCmdWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kit-a1-cmd-")
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.xdg = self.base / "xdg"
        self.xdg.mkdir()
        self.main, self.wt = _make_repo_and_worktree(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def _clean_env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k != "KIT_BIN_DIR"}
        env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "KIT_NO_FUNNEL": "1",
        })
        env.update(extra)
        return env

    def test_linked_worktree_skips_default_bin_and_leaves_existing_launcher_untouched(self):
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        launcher = local_bin / "myproj"
        existing = '#!/usr/bin/env bash\n# kit-board launcher\nexec "%s/.kit/up" "$@"\n' % self.main
        launcher.write_text(existing, encoding="utf-8")

        log = []
        with mock.patch.dict(os.environ, self._clean_env(), clear=True), \
                mock.patch.object(install_mod, "under_tempdir", return_value=False):
            install_mod.install_cmd(str(self.wt), "myproj", dry=False, log=log)

        self.assertEqual(launcher.read_text(encoding="utf-8"), existing)
        self.assertTrue(any("SKIPPED command `myproj`" in line and "is a git worktree" in line for line in log), log)

    def test_main_checkout_installs_and_updates_default_bin_launcher(self):
        local_bin = self.home / ".local" / "bin"
        launcher = local_bin / "myproj"

        log1 = []
        with mock.patch.dict(os.environ, self._clean_env(), clear=True), \
                mock.patch.object(install_mod, "under_tempdir", return_value=False):
            install_mod.install_cmd(str(self.main), "myproj", dry=False, log=log1)

        self.assertTrue(launcher.is_file())
        self.assertIn(str(self.main / ".kit" / "up"), launcher.read_text(encoding="utf-8"))
        self.assertTrue(any("command: `myproj`" in line for line in log1), log1)

        # Updating an existing kit launcher from the main checkout still works.
        stale = '#!/usr/bin/env bash\n# kit-board launcher\nexec "/old/path/.kit/up" "$@"\n'
        launcher.write_text(stale, encoding="utf-8")
        log2 = []
        with mock.patch.dict(os.environ, self._clean_env(), clear=True), \
                mock.patch.object(install_mod, "under_tempdir", return_value=False):
            install_mod.install_cmd(str(self.main), "myproj", dry=False, log=log2)

        self.assertIn(str(self.main / ".kit" / "up"), launcher.read_text(encoding="utf-8"))
        self.assertTrue(any("command: `myproj`" in line for line in log2), log2)

    def test_explicit_kit_bin_dir_overrides_worktree_skip(self):
        override_bin = self.base / "override-bin"
        log = []
        with mock.patch.dict(os.environ, self._clean_env(KIT_BIN_DIR=str(override_bin)), clear=True), \
                mock.patch.object(install_mod, "under_tempdir", return_value=False):
            install_mod.install_cmd(str(self.wt), "myproj", dry=False, log=log)

        dst = override_bin / "myproj"
        self.assertTrue(dst.is_file())
        self.assertIn(str(self.wt / ".kit" / "up"), dst.read_text(encoding="utf-8"))
        self.assertFalse(any("SKIPPED" in line for line in log), log)

    def test_install_update_subprocess_in_worktree_preserves_launchers_and_respects_override(self):
        # Seed a real kit install in the main repo and mirror it into the linked worktree.
        seed_bin = self.base / "seed-bin"
        r0 = subprocess.run(
            [sys.executable, "-I", str(ROOT / "install.py"), str(self.main),
             "--quick", "--project", "MyProj", "--runtime", "agy", "--cmd", "myproj"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env=self._clean_env(KIT_BIN_DIR=str(seed_bin)), timeout=120,
        )
        self.assertEqual(r0.returncode, 0, r0.stdout + r0.stderr)
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-q", "-m", "install kit")
        _git(self.wt, "merge", "-q", "--ff-only", "main")

        # Pre-populate ~/.local/bin/myproj and ~/.local/bin/board pointing to main.
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        myproj_bin = local_bin / "myproj"
        board_bin = local_bin / "board"
        myproj_orig = '#!/usr/bin/env bash\n# kit-board launcher\nexec "%s/.kit/up" "$@"\n' % self.main
        board_orig = '#!/usr/bin/env bash\n# kit-board launcher\nexec "%s/.kit/up" --board-only "$@"\n' % self.main
        myproj_bin.write_text(myproj_orig, encoding="utf-8")
        board_bin.write_text(board_orig, encoding="utf-8")

        # Running `install.py . --update` inside the linked worktree without KIT_BIN_DIR must skip both launchers.
        r1 = subprocess.run(
            [sys.executable, "-I", str(ROOT / "install.py"), str(self.wt), "--update"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env=self._clean_env(), timeout=120,
        )
        out1 = r1.stdout + r1.stderr
        self.assertEqual(r1.returncode, 0, out1)
        self.assertIn("SKIPPED command `myproj`", out1)
        self.assertIn("SKIPPED command `board`", out1)
        self.assertIn("is a git worktree", out1)
        self.assertEqual(myproj_bin.read_text(encoding="utf-8"), myproj_orig)
        self.assertEqual(board_bin.read_text(encoding="utf-8"), board_orig)

        # With explicit KIT_BIN_DIR, the linked worktree still installs launchers into that override dir.
        override_bin = self.base / "explicit-bin"
        r2 = subprocess.run(
            [sys.executable, "-I", str(ROOT / "install.py"), str(self.wt), "--update"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env=self._clean_env(KIT_BIN_DIR=str(override_bin)), timeout=120,
        )
        out2 = r2.stdout + r2.stderr
        self.assertEqual(r2.returncode, 0, out2)
        self.assertTrue((override_bin / "myproj").is_file())
        self.assertTrue((override_bin / "board").is_file())
        self.assertIn(str(self.wt / ".kit" / "up"), (override_bin / "myproj").read_text(encoding="utf-8"))
        # Default ~/.local/bin launchers remain untouched.
        self.assertEqual(myproj_bin.read_text(encoding="utf-8"), myproj_orig)
        self.assertEqual(board_bin.read_text(encoding="utf-8"), board_orig)


if __name__ == "__main__":
    unittest.main()
