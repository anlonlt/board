"""`kit.py cards`: the cards checked by a script, not an agent (the kit-cards agent rewrote what the board server had
already built, at each launch and Done). It writes only a card that differs from the posts, says so in one line, and
lists the sessions to archive whatever platform started them.

Run via: python3 -m unittest tests.test_cards_command -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import temp_cleanup  # noqa: E402,F401  CL52: the folders this file makes go at exit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import kit  # noqa: E402

NOW = "2026-11-26T09:00:00Z"
POSTS = {"cl1": {"title": "One", "part": "CL1", "train": "t", "column": "done", "status": "complete", "session_id": "5e2a32e5-agy"},
         "cl2": {"title": "Two", "part": "CL2", "train": "t", "column": "doing", "status": "in progress", "session_id": "session_01web"},
         "cl3": {"title": "Old", "part": "CL3", "train": "t", "column": "done", "session_id": "archived-one"}}


class Cards(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, ".kit"))
        Path(self.d, ".kit", "archived.txt").write_text("archived-one\n")
        self.patches = [mock.patch.object(kit, "ROOT", self.d)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_cards(self, posts):
        sent = []
        with mock.patch.object(kit, "_all_posts", return_value=dict(posts)), \
                mock.patch.object(kit, "_board_request", side_effect=lambda u, path, body, **k: sent.append(path) or {}):
            rc, line = kit.cards(url="http://x", token="t", now=NOW)
        return rc, line, sent

    def test_stale_cards_are_rewritten_then_left_alone(self):
        rc, line, sent = self.run_cards(POSTS)
        self.assertEqual(rc, 0, line)
        self.assertEqual(sent, ["db/posts/_dashboard", "db/posts/_trains"])
        kc = kit._kit_cards_mod()
        real = dict(POSTS)
        dash = kc.build(NOW, board=dict(real)); trains = kc.build_trains(dict(real), dash["rows"], NOW)
        for r in dash["rows"]:
            if r["state"] in ("ready", "idle") and any(x["state"] == "running" for x in trains["rows"] if x["parent"] == r["train"]):
                r["state"] = "running"
        rc, line, sent = self.run_cards(dict(POSTS, _dashboard=dash, _trains=trains))
        self.assertEqual(sent, []); self.assertIn("already current", line)

    def test_sessions_to_archive_from_any_platform_once(self):
        line = self.run_cards(POSTS)[1]
        self.assertIn("to archive: 5e2a32e5-agy", line)       # an agy id (the old list kept only session_*)
        self.assertNotIn("archived-one", line); self.assertNotIn("session_01web", line)   # archived / not done

    def test_one_line_no_post_list(self):
        self.assertEqual(len(self.run_cards(POSTS)[1].splitlines()), 1)

    def test_cli_main_cards_does_not_shadow_function(self):
        """Regression: kit.main(['cards']) must not raise UnboundLocalError from local `cards` in queue branch."""
        with mock.patch.object(kit, "cards", return_value=(0, "cards: 1 rows, trains 1 (already current); to archive: none")) as m:
            rc = kit.main(["cards"])
        self.assertEqual(rc, 0)
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
