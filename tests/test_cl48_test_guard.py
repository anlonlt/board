"""CL48: a test run (KIT_TEST set, as scripts/run_all_tests.py sets it) never writes to the real board, its git copy or a
remote: each write outside a temp folder is refused, with the path or the URL it refused.

On 2026-11-20 the install test's made-up project (Tide Clock, branch test-install of the kit's own repository) pushed its
posts to the kit's real board-mirror (commit 738a5f3). Each case below is a write a test could make by falling back to
the real board: each is refused, and the same write to a temp folder still goes through."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "scripts", "kit.py")
CLI = os.path.join(ROOT, "kit", "board", "board_cli.py")
SERVER = os.path.join(ROOT, "kit", "board", "board_server.py")
REAL_MIRROR = os.path.join(os.path.dirname(ROOT), "board-mirror")      # kit.py's fallback: the real board's git copy


def env(test=True, **kw):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_BOARD", "KIT_ROLE", "CLAUDE"))}
    e.pop("KIT_TEST", None)
    if test:
        e["KIT_TEST"] = "1"
    e.update(kw)
    return e


def run(cmd, test=True, cwd=ROOT, **kw):
    return subprocess.run(cmd, cwd=cwd, env=env(test, **kw), capture_output=True, text=True, timeout=120)


def git(repo, *a):
    return subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, timeout=60, env=env(False))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class GitCopyAndRemote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cl48-")
        self.mirror = os.path.join(self.tmp, "mirror")
        os.makedirs(os.path.join(self.mirror, "board", "posts"))
        git(self.mirror, "init", "-q", "-b", "board-mirror")
        git(self.mirror, "config", "user.email", "t@example.com")
        git(self.mirror, "config", "user.name", "t")
        with open(os.path.join(self.mirror, "board", "posts", "p.json"), "w") as fh:
            json.dump({"title": "a test post"}, fh)

    def test_the_real_mirror_is_refused_with_its_path(self):
        before = os.path.exists(REAL_MIRROR)
        r = run([sys.executable, "-I", KIT, "--mirror", REAL_MIRROR, "init"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KIT_TEST: REFUSED", r.stdout + r.stderr)
        self.assertIn(os.path.realpath(REAL_MIRROR), r.stdout + r.stderr)
        self.assertEqual(os.path.exists(REAL_MIRROR), before, "nothing was created")

    def test_a_push_to_the_real_remote_is_refused_with_its_url(self):
        url = "https://github.com/anlonlt/board.git"
        git(self.mirror, "remote", "add", "origin", url)
        r = run([sys.executable, "-I", KIT, "--mirror", self.mirror, "push"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KIT_TEST: REFUSED", r.stdout + r.stderr)
        self.assertIn(url, r.stdout + r.stderr)

    def test_a_push_to_a_temp_remote_goes_through(self):
        bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        git(self.mirror, "remote", "add", "origin", bare)
        git(self.mirror, "add", "-A")
        git(self.mirror, "commit", "-q", "-m", "seed")
        r = run([sys.executable, "-I", KIT, "--mirror", self.mirror, "push"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(bare, "rev-parse", "board-mirror").returncode, 0)

    def test_outside_a_test_run_nothing_is_checked(self):
        sys.path.insert(0, os.path.join(ROOT, "kit"))
        try:
            import test_guard
        finally:
            sys.path.pop(0)
        self.assertFalse(test_guard.active({}))
        self.assertTrue(test_guard.active({"KIT_TEST": "1"}))
        self.assertTrue(test_guard.under_temp(self.mirror))
        self.assertFalse(test_guard.under_temp(ROOT) and not ROOT.startswith(tempfile.gettempdir()))


class Board(unittest.TestCase):
    """The real board stands in as a board server started outside a test run: it never says its data is a test's."""

    def start(self, test):
        root = tempfile.mkdtemp(prefix="cl48-board-")
        os.makedirs(os.path.join(root, ".kit", "board_data"))
        port = free_port()
        p = subprocess.Popen([sys.executable, "-I", SERVER, "--root", root, "--port", str(port), "--auto-sync-interval", "0"],
                             env=env(test), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)
        url = "http://127.0.0.1:%d" % port
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/_api/health", timeout=2) as r:
                    return url, json.loads(r.read().decode("utf-8"))
            except OSError:
                time.sleep(0.1)
        self.fail("board server did not start")

    def write(self, url):
        return run([sys.executable, "-I", CLI, "set", "posts", "cl48-probe", "--data", '{"title": "probe", "column": "info"}'],
                   KIT_BOARD_URL=url)

    def test_a_write_to_the_real_board_is_refused(self):
        url, health = self.start(test=False)
        self.assertIsNot(health.get("temp"), True)
        r = self.write(url)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KIT_TEST: REFUSED", r.stdout + r.stderr)
        self.assertIn(url, r.stdout + r.stderr)
        with urllib.request.urlopen(url + "/_api/db/posts/cl48-probe", timeout=5) as resp:
            self.assertFalse(json.loads(resp.read().decode("utf-8")).get("exists"), "nothing was written")

    def test_the_default_board_url_is_refused_in_a_test_run(self):
        # no KIT_BOARD_URL: board_cli falls back to the project's own board (127.0.0.1:8765), the real one
        r = run([sys.executable, "-I", CLI, "set", "posts", "cl48-probe", "--data", '{"title": "probe"}'])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KIT_TEST: REFUSED", r.stdout + r.stderr)

    def test_a_write_to_a_test_board_goes_through(self):
        url, health = self.start(test=True)
        self.assertIs(health.get("temp"), True)
        r = self.write(url)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_board_server_on_the_real_data_is_refused(self):
        r = run([sys.executable, "-I", SERVER, "--root", ROOT, "--port", str(free_port()), "--auto-sync-interval", "0"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KIT_TEST: REFUSED", r.stdout + r.stderr)
        self.assertIn(os.path.join(os.path.realpath(ROOT), ".kit", "board_data"), r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
