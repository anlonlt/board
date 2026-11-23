#!/usr/bin/env python3
"""CL29: `kit.py compact` keeps a project's memory checker green.

On Harmonize, compact archived 23 state files, awaits.md and 38 closed issues but left `.story/awaits-anlon.md`
tagging the archived workstreams: its story checker (.claude/skills/story/scripts/check.py, a copy is in
compare/harmonize/) gave 31 faults and the push gate refused. Compact now
  1. handles every awaits file (`awaits*.md` in the memory dir, plus `kit.awaits_files` in .kit/config.json):
     a point tagging an archived workstream moves with it into archive/<file>; a point of a workstream that stays
     stays; a file with no point left is archived whole;
  2. keeps a closed issue that a staying file cites;
  3. runs the project's memory checker (`kit.memory_check` in config, else a story/memory skill's scripts/check.py)
     on the compacted tree, shows its result in a dry run, and undoes the whole compaction if it is red.

python3 -m unittest tests.test_cl29_compact_checker -v
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "compare", "harmonize", "skills", "story", "scripts", "check.py")


def load_kit():
    spec = importlib.util.spec_from_file_location("kit_cl29", os.path.join(ROOT, "scripts", "kit.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def state(name, nxt="Build the next screen.", where="It runs.", extra=""):
    return ("# State — %s\n\n## Where it stands\n%s\n\n## Next step\n%s\n\n## Recent decisions and why\n- none\n\n"
            "## Open\n%s\n" % (name, where, nxt, extra or "- nothing"))


AWAITS = ("# What awaits Anlon\n\n"
          "- [app] Pick the colour of the play button. Default: blue · deadline 2099-12-31\n"
          "- [sync] Keep the cloud copy? Default: yes\n"
          "  it costs nothing · deadline 2099-12-31\n"
          "- [orchestrator] Confirm the night runs. Default: they run · deadline 2099-12-31\n")


def tree(d, orchestrator_extra="", checker=True):
    mem = os.path.join(d, ".story")
    for sub in ("state", "issues"):
        os.makedirs(os.path.join(mem, sub))
    files = {
        "state/app.md": state("app"),
        "state/sync.md": state("sync"),
        "state/orchestrator.md": state("orchestrator", extra=orchestrator_extra),
        "awaits-anlon.md": AWAITS,
        "issues/2026-11-01-crash.md": "# crash\nStatus: closed — 2026-11-02, fixed in abc\n",
        "issues/2026-11-03-slow.md": "# slow\nStatus: closed — 2026-11-04, fixed in def\n",
        "issues/2026-11-05-open.md": "# open one\nStatus: open — 2026-11-05, seen twice\n",
    }
    for rel, text in files.items():
        with open(os.path.join(mem, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    if checker:
        dst = os.path.join(d, ".claude", "skills", "story", "scripts")
        os.makedirs(dst)
        shutil.copy(CHECKER, os.path.join(dst, "check.py"))
    return mem


def snapshot(d):
    out = {}
    for r, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(r, f)
            out[os.path.relpath(p, d)] = open(p, encoding="utf-8", errors="ignore").read()
    return out


def run_checker(d):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-I", os.path.join(d, ".claude", "skills", "story", "scripts", "check.py"), d],
                          capture_output=True, text=True)


class CompactKeepsCheckerGreen(unittest.TestCase):
    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_fixture_green_before(self):
        tree(self.d)
        r = run_checker(self.d)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_checker_green_after_compact(self):
        mem = tree(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        r = run_checker(self.d)
        self.assertEqual(r.returncode, 0, out + "\n" + r.stdout)
        self.assertIn("STORY: OK", out)

    def test_points_move_with_their_workstream(self):
        mem = tree(self.d)
        self.kit.compact_memory(root_dir=self.d)
        left = open(os.path.join(mem, "awaits-anlon.md"), encoding="utf-8").read()
        self.assertIn("[orchestrator] Confirm the night runs", left)
        self.assertNotIn("[app]", left)
        self.assertNotIn("[sync]", left)
        moved = open(os.path.join(mem, "archive", "awaits-anlon.md"), encoding="utf-8").read()
        self.assertIn("[app] Pick the colour", moved)
        self.assertIn("  it costs nothing · deadline 2099-12-31", moved)  # continuation line goes with its point
        self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "state", "app.md")))

    def test_file_with_no_point_left_is_archived(self):
        mem = tree(self.d)
        with open(os.path.join(mem, "awaits-anlon.md"), "w", encoding="utf-8") as fh:
            fh.write(AWAITS.split("- [orchestrator]")[0])
        self.kit.compact_memory(root_dir=self.d)
        self.assertFalse(os.path.exists(os.path.join(mem, "awaits-anlon.md")))
        self.assertEqual(run_checker(self.d).returncode, 0)

    def test_config_names_another_awaits_file(self):
        mem = tree(self.d)
        os.makedirs(os.path.join(self.d, ".kit"))
        with open(os.path.join(self.d, ".kit", "config.json"), "w") as fh:
            json.dump({"kit": {"memory_dir": ".story", "awaits_files": ["owner-queue.md"]}}, fh)
        with open(os.path.join(mem, "owner-queue.md"), "w", encoding="utf-8") as fh:
            fh.write("- [app] another point. Default: no · deadline 2099-12-31\n")
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertFalse(os.path.exists(os.path.join(mem, "owner-queue.md")), out)
        self.assertIn("[app] another point", open(os.path.join(mem, "archive", "owner-queue.md")).read())

    def test_dry_run_shows_checker_and_moves_nothing(self):
        tree(self.d)
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d, dry_run=True)
        self.assertEqual(snapshot(self.d), before)
        self.assertIn("compact (dry-run)", out)
        self.assertIn("checker", out)
        self.assertIn("STORY: OK", out)
        self.assertIn("awaits-anlon.md: 2 point(s)", out)

    def test_red_checker_undoes_everything(self):
        # orchestrator.md cites a state file that compact archives: a dead pointer the checker catches
        tree(self.d, orchestrator_extra="- see `.story/state/app.md`")
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertIn("REFUSED", out)
        self.assertIn("dead pointer", out)
        self.assertEqual(snapshot(self.d), before)
        out = self.kit.compact_memory(root_dir=self.d, dry_run=True)
        self.assertIn("would be REFUSED", out)
        self.assertEqual(snapshot(self.d), before)

    def test_closed_issue_a_staying_file_cites_stays(self):
        mem = tree(self.d, orchestrator_extra="- the crash: .story/issues/2026-11-01-crash")
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertTrue(os.path.isfile(os.path.join(mem, "issues", "2026-11-01-crash.md")), out)
        self.assertFalse(os.path.isfile(os.path.join(mem, "issues", "2026-11-03-slow.md")), out)
        self.assertEqual(run_checker(self.d).returncode, 0, out)

    def test_no_checker_still_compacts(self):
        mem = tree(self.d, checker=False)
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertIn("no memory checker", out)
        self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "state", "app.md")))


if __name__ == "__main__":
    unittest.main()
