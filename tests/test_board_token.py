#!/usr/bin/env python3
"""CL11: one board token per machine, kept across projects and CLIs.

Every case runs with a temp HOME and a temp XDG_CONFIG_HOME: no test reads or writes the real ~/.config, and no token
is printed (assertions compare values, failure messages never include them).
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit", "board"))
import board_token  # noqa: E402

REAL_CFG = os.path.join(os.path.expanduser("~"), ".config", "board-kit", "board_token")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read().strip()


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="kit-cl11-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)
        self.env = {"HOME": self.home}   # XDG unset: the ~/.config fallback, under the temp HOME
        self.real_before = os.stat(REAL_CFG).st_mtime if os.path.exists(REAL_CFG) else None

    def tearDown(self):
        real_after = os.stat(REAL_CFG).st_mtime if os.path.exists(REAL_CFG) else None
        shutil.rmtree(self.d, ignore_errors=True)
        self.assertEqual(self.real_before, real_after, "the real ~/.config/board-kit/board_token was touched")

    def project(self, name):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.join(p, ".kit"), exist_ok=True)
        return p


class TestBoardToken(Sandbox):
    def test_new_project_reuses_machine_token(self):
        a, b = self.project("alpha"), self.project("beta")
        pa, src_a = board_token.ensure(a, self.env)
        pb, src_b = board_token.ensure(b, self.env)
        machine = os.path.join(self.home, ".config", "board-kit", "board_token")
        self.assertEqual((src_a, src_b), ("machine", "machine"))
        self.assertTrue(os.path.isfile(machine), "the machine token was created under the temp HOME")
        self.assertTrue(read(pa) == read(pb) == read(machine), "both projects carry the machine token")
        self.assertEqual(len(read(machine)), 32)
        for p in (machine, pa, pb):
            self.assertEqual(mode(p), 0o600, p)

    def test_machine_token_created_once(self):
        first = board_token.machine_token(self.env)
        self.assertTrue(first == board_token.machine_token(self.env), "a second call reuses the token, never mints a new one")

    def test_existing_project_token_kept(self):
        a = self.project("own")
        os.makedirs(os.path.join(a, ".kit", "board_data"))
        tp = os.path.join(a, ".kit", "board_data", "token")
        with open(tp, "w") as fh:
            fh.write("projectownvalue\n")
        board_token.machine_token(self.env)   # a machine token exists too
        p, src = board_token.ensure(a, self.env)
        self.assertEqual(src, "project")
        self.assertTrue(read(p) == "projectownvalue", "the project's own token is unchanged")

    def test_empty_project_token_replaced(self):
        a = self.project("empty")
        os.makedirs(os.path.join(a, ".kit", "board_data"))
        open(os.path.join(a, ".kit", "board_data", "token"), "w").close()
        p, src = board_token.ensure(a, self.env)
        self.assertEqual(src, "machine")
        self.assertTrue(read(p) == board_token.machine_token(self.env))

    def test_xdg_config_home_wins(self):
        xdg = os.path.join(self.d, "xdg")
        env = dict(self.env, XDG_CONFIG_HOME=xdg)
        board_token.ensure(self.project("x"), env)
        self.assertTrue(os.path.isfile(os.path.join(xdg, "board-kit", "board_token")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "board-kit")))

    def test_cli_never_prints_token(self):
        a = self.project("cli")
        env = dict(os.environ, HOME=self.home)
        env.pop("XDG_CONFIG_HOME", None)
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit", "board", "board_token.py"), "ensure", "--root", a],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        tok = read(os.path.join(a, ".kit", "board_data", "token"))
        self.assertNotIn(tok, r.stdout + r.stderr, "the CLI printed the token")
        self.assertIn("the machine's", r.stdout)


class TestUpAndInstall(Sandbox):
    """`up` for both CLIs, and install.py, go through the same helper."""

    def env_for(self):
        env = dict(os.environ, HOME=self.home, KIT_NO_FUNNEL="1")
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("KIT_BOARD_TOKEN", None)
        return env

    def test_install_gives_new_project_the_machine_token_and_keeps_its_own(self):
        env = self.env_for()
        a, b = os.path.join(self.d, "inst-a"), os.path.join(self.d, "inst-b")
        for t in (a, b):
            os.makedirs(t)
            subprocess.run(["git", "init", "-q", t], check=True)
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "install.py"), a, "--quick"],
                           capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        machine = os.path.join(self.home, ".config", "board-kit", "board_token")
        ta = os.path.join(a, ".kit", "board_data", "token")
        self.assertTrue(os.path.isfile(ta) and read(ta) == read(machine), "install gave the project the machine token")
        self.assertNotIn(read(machine), r.stdout + r.stderr, "install printed the token")
        self.assertTrue(os.path.isfile(os.path.join(a, ".kit", "board_token.py")))
        # b already has its own: install keeps it
        os.makedirs(os.path.join(b, ".kit", "board_data"))
        with open(os.path.join(b, ".kit", "board_data", "token"), "w") as fh:
            fh.write("ownofb\n")
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "install.py"), b, "--quick"],
                           capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertTrue(read(os.path.join(b, ".kit", "board_data", "token")) == "ownofb")
        self.assertFalse(subprocess.run(["git", "-C", a, "check-ignore", "-q", ".kit/board_data/token"]).returncode,
                         "the project token is git-ignored, never committed")

    def _up(self, cli, project):
        """Run kit/up in a copy of a minimal project with a fake CLI that records whether KIT_BOARD_TOKEN matches."""
        kit = os.path.join(project, ".kit")
        os.makedirs(kit, exist_ok=True)
        shutil.copy(os.path.join(ROOT, "kit", "up"), os.path.join(kit, "up"))
        shutil.copy(os.path.join(ROOT, "kit", "board", "board_token.py"), os.path.join(kit, "board_token.py"))
        # a board that answers health with auth and accepts the token: a tiny stand-in, so `up` does not start one
        bin_d = os.path.join(self.d, "bin-" + cli)
        os.makedirs(bin_d)
        rec = os.path.join(self.d, "rec-" + cli)
        fake = os.path.join(bin_d, cli)
        with open(fake, "w") as fh:
            fh.write('#!/bin/sh\npython3 -I -c "import os,sys; t=open(sys.argv[1]).read().strip(); '
                     'open(sys.argv[2],\'w\').write(\'same\' if os.environ.get(\'KIT_BOARD_TOKEN\')==t else \'differs\')" '
                     '"%s" "%s"\n' % (os.path.join(kit, "board_data", "token"), rec))
        os.chmod(fake, 0o755)
        curl = os.path.join(bin_d, "curl")
        with open(curl, "w") as fh:
            fh.write('#!/bin/sh\necho \'{"ok": true, "auth": true}\'\n')
        os.chmod(curl, 0o755)
        with open(os.path.join(kit, "board_server.py"), "w") as fh:
            fh.write("# stand-in: up only checks it exists\n")
        if cli == "claude":
            os.makedirs(os.path.join(project, ".claude"), exist_ok=True)
            with open(os.path.join(project, ".claude", "settings.json"), "w") as fh:
                fh.write("{}")
        env = self.env_for()
        env["PATH"] = bin_d + os.pathsep + env.get("PATH", "")
        env["KIT_BOARD_PORT"] = "1"
        r = subprocess.run(["bash", os.path.join(kit, "up"), "--" + cli], capture_output=True, text=True, env=env,
                           cwd=project, timeout=120)
        return r, rec

    def test_up_agy_and_claude_share_the_machine_token(self):
        pa, pb = self.project("up-agy"), self.project("up-claude")
        ra, rec_a = self._up("agy", pa)
        rb, rec_b = self._up("claude", pb)
        for r in (ra, rb):
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
        machine = read(os.path.join(self.home, ".config", "board-kit", "board_token"))
        ta, tb = read(os.path.join(pa, ".kit", "board_data", "token")), read(os.path.join(pb, ".kit", "board_data", "token"))
        self.assertTrue(ta == tb == machine, "both projects, one per CLI, carry the machine token")
        self.assertEqual(read(rec_a), "same", "agy got KIT_BOARD_TOKEN = the board token")
        self.assertEqual(read(rec_b), "same", "claude got KIT_BOARD_TOKEN = the board token")

    def test_up_keeps_project_token(self):
        p = self.project("up-own")
        os.makedirs(os.path.join(p, ".kit", "board_data"))
        with open(os.path.join(p, ".kit", "board_data", "token"), "w") as fh:
            fh.write("ownofup\n")
        r, rec = self._up("agy", p)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertTrue(read(os.path.join(p, ".kit", "board_data", "token")) == "ownofup")
        self.assertEqual(read(rec), "same")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "board-kit", "board_token")),
                         "a project with its own token does not mint a machine token")


if __name__ == "__main__":
    unittest.main()
