"""Integration tests for warm Git-based cross-runtime handoff (kit.py receive + publish).

Run via: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kit  # noqa: E402

KIT = ROOT / "scripts" / "kit.py"
BOOT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from board_server import start_server\n"
    "s, store = start_server(sys.argv[2], port=0, token='test-only')\n"
    "print(s.server_port, flush=True)\n"
    "s.serve_forever()\n"
)
TOKEN = "test-only"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def api_req(url, path, body=None, token=None, method=None, gate="copy"):
    """`gate` "copy": a test seeds a board's history, a finished step included; "" writes as anyone with the token does."""
    hdr = {"Content-Type": "application/json"}
    if gate:
        hdr["X-Kit-Gate"] = gate
    if token:
        hdr["X-Kit-Token"] = token
    req = urllib.request.Request(
        url.rstrip("/") + "/_api/" + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers=hdr,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.load(res)


class Replica:
    def __init__(self, directory, remote, stack):
        self.root = directory
        self.mirror = directory / "mirror"
        self.db = directory / ".kit" / "board_data" / "board.db"
        directory.mkdir()
        git(directory, "clone", "-q", "-b", "board-mirror", str(remote), "mirror")
        git(self.mirror, "config", "user.name", directory.name)
        git(self.mirror, "config", "user.email", directory.name + "@example.invalid")
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(directory)],
            stdout=subprocess.PIPE,
            text=True,
        )
        stack.callback(self.stop)
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.read_in()

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=60)
        self.proc.stdout.close()

    def read_in(self):
        return kit.receive(str(self.mirror), url=self.url, branch="board-mirror", token=TOKEN)

    def write(self, path, data):
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def get(self, path):
        return api_req(self.url, "doc/" + path, token=TOKEN)

    def kit_cli(self, *args, check=True):
        return subprocess.run(
            [sys.executable, "-I", str(KIT), "--mirror", str(self.mirror), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def publish(self):
        return kit.publish(
            str(self.mirror),
            url=self.url,
            db_path=str(self.db),
            branch="board-mirror",
            token=TOKEN,
        )["sha"]


class TestHandoff(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.remote = root / "remote.git"
        git(root, "init", "-q", "--bare", "-b", "board-mirror", str(self.remote))
        seed = root / "seed"
        git(root, "clone", "-q", str(self.remote), str(seed))
        git(
            seed,
            "-c", "user.name=seed",
            "-c", "user.email=seed@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "Empty mirror",
        )
        git(seed, "push", "origin", "HEAD:board-mirror")
        self.a, self.b = (Replica(root / n, self.remote, stack) for n in ("A", "B"))
        self.a.write("posts/P1", {"title": "Original", "column": "info"})
        self.a.publish()
        self.b.read_in()

    def test_warm_round_trip_and_replay(self):
        a, b = self.a, self.b
        pid = api_req(a.url, "health")["pid"]
        a.write("posts/local-only", {"title": "Keep unpublished"})
        b.write("posts/P1", {"title": "Edited by B", "column": "info"})
        b.write("comments/R1", {"post_id": "P1", "text": "Research complete", "from": "B"})
        tip = b.publish()

        # Receive via CLI to verify subcommand wiring as well
        res_cli = a.kit_cli("receive", "--board-url", a.url, "--token", TOKEN)
        self.assertEqual(json.loads(res_cli.stdout), {"applied": 2, "sha": tip, "status": "received"})
        card, reply = a.get("posts/P1"), a.get("comments/R1")
        self.assertEqual(card["data"]["title"], "Edited by B")
        self.assertEqual(reply["data"]["text"], "Research complete")

        # Replay idempotency
        self.assertEqual(a.read_in()["status"], "up to date")
        self.assertEqual((a.get("posts/P1"), a.get("comments/R1")), (card, reply))
        self.assertEqual(api_req(a.url, "db/comments", token=TOKEN)["total"], 1)
        self.assertEqual(a.get("posts/local-only")["data"]["title"], "Keep unpublished")
        self.assertEqual(api_req(a.url, "health")["pid"], pid)

    def test_conflict_atomic(self):
        a, b = self.a, self.b
        receipt = "kit-receipts/mirror-" + "board-mirror".encode("utf-8").hex()
        before = a.get(receipt)
        a.write("posts/P1", {"title": "A draft"})
        b.write("posts/P1", {"title": "B draft"})
        b.write("comments/R2", {"post_id": "P1", "text": "Hold until resolved"})
        tip = b.publish()

        with self.assertRaisesRegex(kit.Conflict, "posts/P1.json"):
            a.publish()

        self.assertEqual(a.get(receipt), before)
        self.assertFalse(a.get("comments/R2")["exists"])
        self.assertEqual(a.get("posts/P1")["data"]["title"], "A draft")
        self.assertEqual(json.loads(git(self.remote, "show", tip + ":board/posts/P1.json"))["title"], "B draft")
        self.assertEqual(git(self.remote, "rev-parse", "board-mirror").strip(), tip)

    def test_stale_push_rejected(self):
        a, b = self.a, self.b
        a.write("posts/P1", {"title": "Unpublished A"})
        a.kit_cli("sync-db", "--db", str(a.db), "--full", "--commit")
        b.write("posts/P1", {"title": "Published B"})
        tip = b.publish()

        result = a.kit_cli("push", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(git(self.remote, "rev-parse", "board-mirror").strip(), tip)
        self.assertEqual(json.loads(git(a.mirror, "show", "HEAD:board/posts/P1.json"))["title"], "Unpublished A")

    def test_board_seen_monotonic_merge(self):
        a, b = self.a, self.b
        # Both devices mark different posts and the same post at different times
        a.write("data/users/owner/board-seen", {
            "posts": {
                "P1": {"at": "2026-11-20T10:00:00Z", "sig": "old.1", "bt": "2026-11-20T10:00:00Z"},
                "PA": {"at": "2026-11-20T11:00:00Z", "sig": "a.1", "bt": "2026-11-20T11:00:00Z"},
            }
        })
        b.write("data/users/owner/board-seen", {
            "posts": {
                "P1": {"at": "2026-11-20T12:00:00Z", "sig": "new.2", "bt": "2026-11-20T12:00:00Z"},
                "PB": {"at": "2026-11-20T09:00:00Z", "sig": "b.1", "bt": "2026-11-20T09:00:00Z"},
            }
        })
        b.publish()
        res = a.read_in()
        self.assertEqual(res["status"], "received")
        self.assertEqual(res["applied"], 1)
        merged = a.get("data/users/owner/board-seen")["data"]["posts"]
        # Both PA and PB preserved, and P1 takes newer timestamp monotonically
        self.assertEqual(set(merged.keys()), {"P1", "PA", "PB"})
        self.assertEqual(merged["P1"]["at"], "2026-11-20T12:00:00Z")
        self.assertEqual(merged["P1"]["sig"], "new.2")
        self.assertEqual(merged["PA"]["at"], "2026-11-20T11:00:00Z")
        self.assertEqual(merged["PB"]["at"], "2026-11-20T09:00:00Z")
        # Replay is a no-op
        self.assertEqual(a.read_in()["status"], "up to date")

    def test_hard_delete_refused(self):
        a, b = self.a, self.b
        # Simulate a hard-deleted post on remote
        git(b.mirror, "merge", "--ff-only", "origin/board-mirror")
        os.remove(b.mirror / "board" / "posts" / "P1.json")
        git(b.mirror, "add", "-A", "board")
        git(b.mirror, "commit", "-q", "-m", "Hard delete P1")
        git(b.mirror, "push", "origin", "board-mirror")
        with self.assertRaisesRegex(kit.Conflict, "Hard deletion"):
            a.read_in()
        # Local P1 still intact
        self.assertEqual(a.get("posts/P1")["data"]["title"], "Original")

    def test_rewritten_history_refused(self):
        a, b = self.a, self.b
        # Force-rewrite remote history so A's receipt is no longer an ancestor
        git(b.mirror, "merge", "--ff-only", "origin/board-mirror")
        git(b.mirror, "commit", "-q", "--amend", "--allow-empty", "-m", "Rewritten history")
        git(b.mirror, "push", "-q", "--force", "origin", "board-mirror")
        with self.assertRaisesRegex(kit.Conflict, "not an ancestor|rewritten"):
            a.read_in()

    def test_runtime_neutral_status_message(self):
        status = api_req(self.a.url, "comments/status", token=TOKEN)
        self.assertEqual(status["status"], "no_session")
        self.assertIn("agent session", status.get("message", "").lower())
        self.assertNotIn("claude", status.get("message", "").lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
