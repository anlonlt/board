#!/usr/bin/env python3
"""Regression tests for kit/board/kit_cards.py: new format, Harmonize legacy format, and _dashboard safety guard."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_CARDS = os.path.join(REPO_ROOT, "kit", "board", "kit_cards.py")


def _run_kit_cards(workdir, *extra_args):
    return subprocess.run(
        [sys.executable, "-I", os.path.join(workdir, ".kit", "kit_cards.py"), *extra_args],
        capture_output=True,
        text=True,
    )


class TestKitCards(unittest.TestCase):
    def _make_repo(self, d):
        os.makedirs(os.path.join(d, ".kit"), exist_ok=True)
        os.makedirs(os.path.join(d, ".story", "state"), exist_ok=True)
        shutil.copy(KIT_CARDS, os.path.join(d, ".kit", "kit_cards.py"))

    def test_new_format_next_and_steps(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d)
            with open(os.path.join(d, ".story", "state", "bulletin.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "# Bulletin\n"
                    "Next: draft 24 Nov\n"
                    "Priority: 1\n\n"
                    "## Steps\n"
                    "- [x] plan\n"
                    "- [ ] draft\n"
                    "- [ ] publish\n"
                )
            with open(os.path.join(d, ".story", "state", "later.md"), "w", encoding="utf-8") as fh:
                fh.write("# Later\nNext: someday\nPriority: 3\n")
            with open(os.path.join(d, ".story", "awaits.md"), "w", encoding="utf-8") as fh:
                fh.write("# Awaits\n- [bulletin] P1 layout?\n")

            out = os.path.join(d, "dash.json")
            tr = os.path.join(d, "trains.json")
            r = _run_kit_cards(d, "--write", out, "--trains", tr, "--now", "2026-11-20T00:00:00Z")
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as fh:
                rows = json.load(fh)["rows"]
            self.assertEqual([x["name"] for x in rows], ["Bulletin", "Later"])
            self.assertEqual(rows[0]["steps"], ["plan", "draft", "publish"])
            self.assertEqual(rows[0]["at"], 1)
            self.assertEqual(rows[0]["state"], "you")
            self.assertIn("Next: draft 24 Nov", rows[0]["note"])
            self.assertIn("Waits on you: P1 layout?", rows[0]["note"])
            self.assertEqual(rows[1]["state"], "ready")
            self.assertEqual(rows[1]["steps"], ["someday"])

    def test_legacy_harmonize_format(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d)
            with open(os.path.join(d, ".story", "state", "app.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "# State — app\n\n"
                    "Updated: 2026-11-14 (TB1c).\n\n"
                    "## Where it stands\n"
                    "TB1: the bench is one list of sitting cards.\n\n"
                    "## Next step\n"
                    "Waiting: the next APK (TB1: the sittings, Add it) and his sitting 1.\n"
                )
            with open(os.path.join(d, ".story", "state", "chords-measures.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "# State — chords-measures\n\n"
                    "Priority: 3\n\n"
                    "## Next step\n"
                    "Nothing waits here: K3i switched the phone to K2b on his yes.\n"
                )
            with open(os.path.join(d, ".story", "state", "custom.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "# State — Song import\n\n"
                    "Priority: 1\n\n"
                    "## Done\n"
                    "- S14 scoreboard\n"
                    "- S16 bars\n\n"
                    "## Next step\n"
                    "Ship ED8 fixes and verify on Pixel.\n"
                )

            out = os.path.join(d, "dash.json")
            tr = os.path.join(d, "trains.json")
            r = _run_kit_cards(d, "--write", out, "--trains", tr, "--now", "2026-11-20T00:00:00Z")
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as fh:
                rows = json.load(fh)["rows"]
            by_train = {x["train"]: x for x in rows}

            # Priority ordering: custom (1), app (default 2), chords-measures (3)
            self.assertEqual([x["name"] for x in rows], ["Song import", "App", "Chords measures"])

            # Legacy prefix stripped and real next step extracted (no "State —" or "next step" placeholder)
            for row in rows:
                self.assertFalse(row["name"].startswith("State"))
                self.assertNotEqual(row["steps"], ["next step"])

            app = by_train["P-app"]
            self.assertEqual(app["state"], "ready")
            self.assertIn("Waiting: the next APK", app["note"])
            self.assertIn("Waiting: the next APK", app["steps"][0])

            cm = by_train["P-chords-measures"]
            self.assertEqual(cm["state"], "idle")
            self.assertIn("Nothing waits here", cm["note"])

            custom = by_train["P-custom"]
            self.assertEqual(custom["steps"], ["S14 scoreboard", "S16 bars", "Ship ED8 fixes and verify on Pixel."])
            self.assertEqual(custom["at"], 2)
            self.assertEqual(custom["state"], "ready")

    def test_safety_guard_refuses_empty_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d)
            # Empty .story/state/ -> 0 rows generated
            board_dir = os.path.join(d, "board")
            os.makedirs(os.path.join(board_dir, "posts"), exist_ok=True)
            with open(os.path.join(board_dir, "posts", "_dashboard.json"), "w", encoding="utf-8") as fh:
                json.dump({"title": "Where we are", "rows": [{"key": "A", "name": "App", "steps": ["ship"]}]}, fh)

            out = os.path.join(d, "dash.json")
            tr = os.path.join(d, "trains.json")
            r = _run_kit_cards(d, "--board", board_dir, "--write", out, "--trains", tr)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("SAFETY REFUSAL", r.stderr)
            self.assertFalse(os.path.exists(out))
            self.assertFalse(os.path.exists(tr))

            # With --force it succeeds
            r2 = _run_kit_cards(d, "--board", board_dir, "--write", out, "--trains", tr, "--force")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["rows"], [])

    def test_safety_guard_refuses_mostly_placeholders_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            self._make_repo(d)
            # State files with no Next / Next step / Where it stands -> generic ["next step"] placeholders
            for name in ("a.md", "b.md", "c.md"):
                with open(os.path.join(d, ".story", "state", name), "w", encoding="utf-8") as fh:
                    fh.write("# Empty stream\n")
            board_dir = os.path.join(d, "board")
            os.makedirs(os.path.join(board_dir, "posts"), exist_ok=True)
            with open(os.path.join(board_dir, "posts", "_dashboard.json"), "w", encoding="utf-8") as fh:
                json.dump({"title": "Where we are", "rows": [{"key": "A", "name": "Real", "steps": ["ship"]}]}, fh)

            out = os.path.join(d, "dash.json")
            tr = os.path.join(d, "trains.json")
            r = _run_kit_cards(d, "--board", board_dir, "--write", out, "--trains", tr)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("SAFETY REFUSAL", r.stderr)
            self.assertIn("placeholders", r.stderr)
            self.assertFalse(os.path.exists(out))

            # With --force it succeeds
            r2 = _run_kit_cards(d, "--board", board_dir, "--write", out, "--trains", tr, "--force")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
