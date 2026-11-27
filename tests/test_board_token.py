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
import importlib.util  # noqa: E402  (by path: kit/board on sys.path would shadow tests/ modules of the same name)
_spec = importlib.util.spec_from_file_location("board_token", os.path.join(ROOT, "kit", "board", "board_token.py"))
board_token = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(board_token)

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

    def test_a_project_token_that_differs_is_rewritten_to_the_machine_token(self):
        a = self.project("own")
        os.makedirs(os.path.join(a, ".kit", "board_data"))
        tp = os.path.join(a, ".kit", "board_data", "token")
        with open(tp, "w") as fh:
            fh.write("projectownvalue\n")
        m = board_token.machine_token(self.env)   # a machine token exists: it is THE token
        p, src = board_token.ensure(a, self.env)
        self.assertEqual(src, "machine")
        self.assertTrue(read(p) == m, "one token per machine: the project's copy is the machine's")
        self.assertEqual(board_token.ensure(a, self.env)[1], "same")

    def test_with_no_machine_token_the_first_project_token_becomes_it(self):
        a, b = self.project("first"), self.project("second")
        os.makedirs(os.path.join(a, ".kit", "board_data"))
        with open(os.path.join(a, ".kit", "board_data", "token"), "w") as fh:
            fh.write("thephonehasthis\n")
        self.assertEqual(board_token.ensure(a, self.env)[1], "adopted")
        self.assertTrue(board_token.machine_token(self.env) == "thephonehasthis")
        p, _ = board_token.ensure(b, self.env)
        self.assertTrue(read(p) == "thephonehasthis", "every other project takes the same token")

    def test_use_makes_a_project_token_the_machine_token(self):
        a, b = self.project("served"), self.project("other")
        for d, v in ((a, "served-one"), (b, "other-one")):
            os.makedirs(os.path.join(d, ".kit", "board_data"))
            with open(os.path.join(d, ".kit", "board_data", "token"), "w") as fh:
                fh.write(v + "\n")
        board_token.machine_token(self.env)
        self.assertTrue(board_token.use(a, self.env))
        self.assertFalse(board_token.use(a, self.env))
        board_token.ensure(b, self.env)
        self.assertTrue(read(os.path.join(b, ".kit", "board_data", "token")) == "served-one")

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

    def test_read_token_falls_back_to_worktree_parent_and_machine_token(self):
        # 1. Machine token fallback when worktree/project has no .kit/board_data/token (read-only: never creates files)
        bare = self.project("no-tok")
        self.assertIsNone(board_token.read_token(bare, self.env))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "board-kit", "board_token")),
                         "read_token must never create a machine token")
        mtok = board_token.machine_token(self.env)
        self.assertEqual(board_token.read_token(bare, self.env), mtok)
        self.assertFalse(os.path.exists(os.path.join(bare, ".kit", "board_data", "token")),
                         "read_token must never create a project token")

        # 2. Linked git worktree inherits its main checkout's custom token before machine token
        main_repo = self.project("main-repo")
        os.makedirs(os.path.join(main_repo, ".git", "worktrees", "wt1"))
        with open(os.path.join(main_repo, ".git", "worktrees", "wt1", "commondir"), "w") as fh:
            fh.write("../..\n")
        os.makedirs(os.path.join(main_repo, ".kit", "board_data"))
        with open(os.path.join(main_repo, ".kit", "board_data", "token"), "w") as fh:
            fh.write("mainworktreetok\n")
        wt = self.project("wt1")
        with open(os.path.join(wt, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % os.path.join(main_repo, ".git", "worktrees", "wt1"))
        self.assertEqual(board_token.read_token(wt, self.env), "mainworktreetok")

        # 3. Project's own token beats main worktree; KIT_BOARD_TOKEN env beats all
        os.makedirs(os.path.join(wt, ".kit", "board_data"))
        with open(os.path.join(wt, ".kit", "board_data", "token"), "w") as fh:
            fh.write("wttoken\n")
        self.assertEqual(board_token.read_token(wt, self.env), "wttoken")
        self.assertEqual(board_token.read_token(wt, dict(self.env, KIT_BOARD_TOKEN="envtok")), "envtok")

    def test_board_cli_and_kit_resolve_worktree_and_machine_token(self):
        wt = self.project("fresh-wt")
        shutil.copy(os.path.join(ROOT, "kit", "board", "board_token.py"), os.path.join(wt, ".kit", "board_token.py"))
        shutil.copy(os.path.join(ROOT, "kit", "board", "board_cli.py"), os.path.join(wt, ".kit", "board_cli.py"))
        shutil.copy(os.path.join(ROOT, "scripts", "kit.py"), os.path.join(wt, ".kit", "kit.py"))
        mtok = board_token.machine_token(self.env)
        env = dict(os.environ, HOME=self.home)
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("KIT_BOARD_TOKEN", None)
        r = subprocess.run(
            [sys.executable, "-I", "-c",
             "import importlib.util, sys;"
             "s1=importlib.util.spec_from_file_location('bc', sys.argv[1]); m1=importlib.util.module_from_spec(s1); s1.loader.exec_module(m1);"
             "s2=importlib.util.spec_from_file_location('kt', sys.argv[2]); m2=importlib.util.module_from_spec(s2); s2.loader.exec_module(m2);"
             "print('OK' if (m1.default_token() == sys.argv[3] and m2._resolve_board_token() == sys.argv[3]) else 'MISMATCH')",
             os.path.join(wt, ".kit", "board_cli.py"),
             os.path.join(wt, ".kit", "kit.py"),
             mtok],
            capture_output=True, text=True, env=env, cwd=wt, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertEqual(r.stdout.strip(), "OK")


class TestRetiredTokens(Sandbox):
    def test_a_rewritten_project_token_is_kept_as_retired_never_the_current_one(self):
        a = self.project("old")
        os.makedirs(os.path.join(a, ".kit", "board_data"))
        with open(os.path.join(a, ".kit", "board_data", "token"), "w") as fh:
            fh.write("aphonehasthis\n")
        m = board_token.machine_token(self.env)
        board_token.ensure(a, self.env)
        self.assertEqual(board_token.retired(self.env), ["aphonehasthis"])
        self.assertEqual(mode(board_token.retired_path(self.env)), 0o600)
        self.assertFalse(board_token.retire(m, self.env), "the current token is never retired")
        self.assertFalse(board_token.retire("aphonehasthis", self.env), "kept once")


class TestServedToken(Sandbox):
    """The running server's token reaches every checkout of its repo, and only those (a separate checkout with its
    own token file got 401: CL44's token fallback picked the main checkout's token, not the server's)."""

    def repo(self, name):
        r = os.path.join(self.d, name)
        os.makedirs(os.path.join(r, ".kit", "board_data"))
        subprocess.run(["git", "init", "-q", r], check=True)
        subprocess.run(["git", "-C", r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                        "-m", "init"], check=True)
        return r

    def write(self, root, tok):
        with open(board_token.project_token_path(root), "w") as fh:
            fh.write(tok + "\n")

    def test_every_worktree_of_the_repo_gets_the_served_token_and_no_other_project(self):
        a, c = self.repo("a"), self.repo("c")
        b = os.path.join(self.d, "b")
        subprocess.run(["git", "-C", a, "worktree", "add", "-q", "--detach", b], check=True)
        os.makedirs(os.path.join(b, ".kit", "board_data"), exist_ok=True)
        self.write(a, "tok-a"), self.write(b, "tok-b-own"), self.write(c, "tok-c")
        self.assertEqual(board_token.read_token(b, self.env), "tok-b-own")       # no server: its own, as before
        path = board_token.write_served(8765, "tok-a", a, self.env)
        self.assertEqual(mode(path), 0o600)
        self.assertEqual(board_token.read_token(b, self.env), "tok-a")           # the server's, not its own file
        self.assertEqual(board_token.read_token(a, self.env), "tok-a")
        self.assertEqual(board_token.read_token(c, self.env), "tok-c")           # another repo never gets it
        self.assertEqual(board_token.read_token(b, dict(self.env, KIT_BOARD_TOKEN="explicit")), "explicit")
        board_token.clear_served(8765, self.env)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(board_token.read_token(b, self.env), "tok-b-own")

    def test_a_dead_server_record_is_ignored(self):
        a = self.repo("a")
        self.write(a, "own")
        import json
        path = board_token.write_served(8765, "gone", a, self.env)
        rec = json.load(open(path))
        rec["pid"] = 2 ** 22 + 12345
        open(path, "w").write(json.dumps(rec))
        self.assertEqual(board_token.read_token(a, self.env), "own")

    def test_the_server_writes_it_and_removes_it(self):
        import json
        import signal
        import socket
        import time
        a = self.repo("a")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = dict(os.environ, HOME=self.home)
        env.pop("XDG_CONFIG_HOME", None)
        p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "kit", "board", "board_server.py"), "--port",
                              str(port), "--root", a, "--token", "srv-tok"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        path = board_token.served_path(port, self.env)
        try:
            for _ in range(100):
                if os.path.exists(path):
                    break
                time.sleep(0.1)
            rec = json.load(open(path))
            self.assertEqual((rec["token"] == "srv-tok", rec["pid"], rec["repo"]), (True, p.pid, os.path.realpath(a)))
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=20)
        self.assertFalse(os.path.exists(path))


class TestUpAndInstall(Sandbox):
    """`up` for both CLIs, and install.py, go through the same helper."""

    def env_for(self):
        env = dict(os.environ, HOME=self.home, KIT_NO_FUNNEL="1")
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("KIT_BOARD_TOKEN", None)
        return env

    def test_install_gives_every_project_the_machine_token(self):
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
        # b has a token of its own: install makes it the machine's (one token per machine)
        os.makedirs(os.path.join(b, ".kit", "board_data"))
        with open(os.path.join(b, ".kit", "board_data", "token"), "w") as fh:
            fh.write("ownofb\n")
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "install.py"), b, "--quick"],
                           capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertTrue(read(os.path.join(b, ".kit", "board_data", "token")) == read(machine))
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

    def test_up_gives_a_project_with_its_own_token_the_machine_token(self):
        p = self.project("up-own")
        os.makedirs(os.path.join(p, ".kit", "board_data"))
        with open(os.path.join(p, ".kit", "board_data", "token"), "w") as fh:
            fh.write("ownofup\n")
        r, rec = self._up("agy", p)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        machine = os.path.join(self.home, ".config", "board-kit", "board_token")
        self.assertTrue(read(machine) == "ownofup", "with no machine token yet, the project's own becomes it")
        self.assertTrue(read(os.path.join(p, ".kit", "board_data", "token")) == "ownofup")
        self.assertEqual(read(rec), "same")


if __name__ == "__main__":
    unittest.main()
