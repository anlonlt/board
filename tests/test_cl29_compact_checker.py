#!/usr/bin/env python3
"""CL29: `kit.py compact` keeps a project's memory checker green and moves only what is settled.

On Harmonize, compact archived 23 state files, awaits.md and 38 closed issues but left `.story/awaits-anlon.md`
tagging the archived workstreams: its story checker (.claude/skills/story/scripts/check.py, a copy is in
compare/harmonize/) gave 31 faults and the push gate refused. O53's review of the first fix: a dry run on Harmonize
archived all 31 open awaits points (one OVERDUE) and left 0 states; the SessionStart STATES section listed nothing; a
dry run moved files and undid them. Compact now
  1. never moves an open awaits point (`awaits*.md` in the memory dir, plus `kit.awaits_files` in .kit/config.json):
     a point is open until it is removed; an awaits file is archived only when it has no point left;
  2. keeps a state whose workstream has an open point, or whose next step is not settled (done, closed...);
  3. keeps a closed issue that a staying file cites;
  4. checks its plan on a shadow copy of the repo with the project's memory checker (`kit.memory_check`, else a
     story/memory skill's scripts/check.py): red, nothing moves; a dry run prints it and never touches the tree.

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
          "- [app] Calibrate on the speaker. Default: skip · deadline 2020-01-01\n"
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
        "state/wip.md": state("wip", nxt="Wire the export button."),
        "state/old.md": state("old", nxt="Done — shipped on 2026-11-01."),
        "state/gone.md": state("gone", nxt="Closed: superseded by sync."),
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


STATES_AWK = "/^## Next step/ { inside = 1; next } /^## / { inside = 0 } inside && NF { print; exit }"


def session_start_states(d):
    """Harmonize's SessionStart STATES section (.claude/hooks/session-start.sh:131), the same loop."""
    import glob
    import subprocess
    out = []
    for f in sorted(glob.glob(os.path.join(d, ".story", "state", "*.md"))):
        step = subprocess.run(["awk", STATES_AWK, f], capture_output=True, text=True).stdout.strip()
        out.append("%s — %s" % (os.path.basename(f)[:-3], step or "NO NEXT STEP WRITTEN"))
    return out


class CompactKeepsCheckerGreen(unittest.TestCase):
    def setUp(self):
        self.kit = load_kit()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.applied = []
        real = getattr(self.kit, "_apply_compaction", None)
        if real:
            self.kit._apply_compaction = lambda mdir, ops: (self.applied.append(mdir), real(mdir, ops))
        else:  # an older kit: any move it makes counts as touching the tree
            real_replace = self.kit.os.replace
            self.kit.os.replace = lambda a, b: (self.applied.append(a), real_replace(a, b))

    def real_tree_touched(self):
        return [m for m in self.applied if os.path.realpath(m).startswith(os.path.realpath(self.d))]

    def test_fixture_green_before(self):
        tree(self.d)
        self.assertEqual(run_checker(self.d).returncode, 0, run_checker(self.d).stdout)

    def test_checker_green_after_compact(self):
        tree(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        r = run_checker(self.d)
        self.assertEqual(r.returncode, 0, out + "\n" + r.stdout)
        self.assertIn("STORY: OK", out)

    def test_only_settled_states_move(self):
        mem = tree(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        left = sorted(os.listdir(os.path.join(mem, "state")))
        self.assertEqual(left, ["app.md", "orchestrator.md", "sync.md", "wip.md"], out)
        self.assertEqual(sorted(os.listdir(os.path.join(mem, "archive", "state"))), ["gone.md", "old.md"], out)

    def test_open_points_never_move(self):
        mem = tree(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertEqual(open(os.path.join(mem, "awaits-anlon.md"), encoding="utf-8").read(), AWAITS)
        self.assertFalse(os.path.exists(os.path.join(mem, "archive", "awaits-anlon.md")))
        self.assertIn("4 open awaits point(s) stay", out)
        self.assertIn("state/app.md: 2 open awaits point(s)", out)  # the OVERDUE one included

    def test_session_start_still_lists_the_open_work(self):
        tree(self.d)
        self.kit.compact_memory(root_dir=self.d)
        listed = session_start_states(self.d)
        self.assertEqual([x.split(" — ")[0] for x in listed], ["app", "orchestrator", "sync", "wip"])
        self.assertIn("wip — Wire the export button.", listed)

    def test_empty_awaits_file_is_archived(self):
        mem = tree(self.d)
        with open(os.path.join(mem, "awaits-anlon.md"), "w", encoding="utf-8") as fh:
            fh.write("# What awaits Anlon\n\n")
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertFalse(os.path.exists(os.path.join(mem, "awaits-anlon.md")), out)
        self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "awaits-anlon.md")))
        self.assertEqual(run_checker(self.d).returncode, 0)

    def test_config_names_another_awaits_file(self):
        mem = tree(self.d)
        os.makedirs(os.path.join(self.d, ".kit"))
        with open(os.path.join(self.d, ".kit", "config.json"), "w") as fh:
            json.dump({"kit": {"memory_dir": ".story", "awaits_files": ["owner-queue.md"]}}, fh)
        with open(os.path.join(mem, "owner-queue.md"), "w", encoding="utf-8") as fh:
            fh.write("- [old] reopen it? Default: no · deadline 2099-12-31\n")
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertTrue(os.path.isfile(os.path.join(mem, "state", "old.md")), out)
        self.assertTrue(os.path.isfile(os.path.join(mem, "owner-queue.md")), out)

    def test_dry_run_never_touches_the_tree(self):
        tree(self.d)
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d, dry_run=True)
        self.assertEqual(self.real_tree_touched(), [], out)
        self.assertEqual(snapshot(self.d), before)
        self.assertIn("compact (dry-run): would archive 4 file(s)", out)
        self.assertIn("memory checker (.claude/skills/story/scripts/check.py): ", out)
        self.assertIn("STORY: OK", out)

    def test_red_checker_moves_nothing(self):
        # orchestrator.md cites a settled state that compact would archive: a dead pointer the checker catches
        tree(self.d, orchestrator_extra="- see `.story/state/old.md`")
        before = snapshot(self.d)
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertIn("compact: REFUSED", out)
        self.assertIn("dead pointer `.story/state/old.md`", out)
        self.assertEqual(self.real_tree_touched(), [])
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
        self.assertTrue(os.path.isfile(os.path.join(mem, "archive", "state", "old.md")))

    def test_kit_next_line_format(self):
        mem = os.path.join(self.d, ".board", "memory")
        os.makedirs(os.path.join(mem, "state"))
        for n, t in (("a", "Next: done\n"), ("b", "Next: build it\n")):
            with open(os.path.join(mem, "state", n + ".md"), "w") as fh:
                fh.write(t)
        out = self.kit.compact_memory(root_dir=self.d)
        self.assertEqual(os.listdir(os.path.join(mem, "state")), ["b.md"], out)


if __name__ == "__main__":
    unittest.main()
