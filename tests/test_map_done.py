"""M3: Done keeps the map. `kit.py done` rebuilds the map and reruns every link's check (refused on one that fails,
the link named), refuses a file the step changed that is not on the map and a thing added with no check, and shows a
thing only a browser sees as unconfirmed once its probe is older than the kit config's `map.confirm_days`.

Each test is a small git repo with a `main` trunk and a step branch, the way a part's work sits when it runs done.
"""
import datetime
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import kit_map  # noqa: E402

KIT = os.path.join(ROOT, "scripts", "kit.py")


def git(d, *a):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@localhost"] + list(a),
                          capture_output=True, text=True, check=True).stdout


def write(d, rel, text):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


class StepRepo(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m3-")
        git(self.d, "init", "-q", "-b", "main")
        write(self.d, "app/core.py", '"""The core."""\ndef run():\n    return 1\n')
        write(self.d, "app/cli.py", '"""The command line."""\nfrom app.core import run\nprint(run())\n')
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "trunk")
        git(self.d, "checkout", "-qb", "m3")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def added(self, things=(), links=()):
        write(self.d, kit_map.ADDED_REL, json.dumps({"things": list(things), "links": list(links)}))

    def changed(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import kit
        return kit._step_changed(self.d)

    def stretch(self):
        return kit_map.stretch(self.d, self.changed() or [], run_gens=False)


class TestDoneRunsTheMapCheck(StepRepo):
    def test_a_link_whose_check_fails_refuses_and_names_the_link(self):
        write(self.d, "docs/guide.md", "Run `app/cli.py` to start.\n")
        self.added([{"id": "doc:guide", "kind": "doc", "files": ["docs/guide.md"],
                     "check": {"file": "docs/guide.md", "contains": "app/cli.py"}}],
                   [{"from": "doc:guide", "to": "file:app/cli.py", "kind": "names",
                     "check": {"file": "docs/guide.md", "contains": "Run `app/cli.py`"}}])
        err, _, _ = self.stretch()
        self.assertEqual(err, [])
        write(self.d, "docs/guide.md", "Run the program to start.\n")
        err, _, _ = self.stretch()
        self.assertTrue(any("doc:guide -names-> file:app/cli.py" in e for e in err), err)

    def test_kit_check_map_is_called_by_done_and_says_how_long_it_took(self):
        import kit
        self.assertIn("check_map(root)", inspect.getsource(kit.done))
        err, notes = kit.check_map(self.d)
        self.assertEqual(err, [])
        self.assertTrue(any(n.startswith("map: checked in") for n in notes), notes)
        self.assertTrue(os.path.isfile(os.path.join(self.d, ".board", "map", "map.json")))


class TestWhatTheStepChangedIsOnTheMap(StepRepo):
    def test_a_changed_code_file_is_on_the_map_by_itself(self):
        write(self.d, "app/core.py", '"""The core."""\ndef run():\n    return 2\n')
        self.assertEqual(self.changed(), ["app/core.py"])
        err, _, _ = self.stretch()
        self.assertEqual(err, [])

    def test_a_changed_file_no_reader_sees_is_refused_until_the_part_adds_it(self):
        write(self.d, "templates/part.md", "Confirm your stretch.\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "M3: a guide")
        err, _, _ = self.stretch()
        self.assertTrue(any("not on the map: templates/part.md" in e for e in err), err)
        self.added([{"id": "guide:part", "kind": "guide", "files": ["templates/*.md"],
                     "check": {"file": "templates/part.md", "contains": "Confirm your stretch"}}])
        err, _, _ = self.stretch()
        self.assertEqual(err, [])

    def test_a_new_thing_with_no_check_is_refused(self):
        write(self.d, "templates/part.md", "x\n")
        self.added([{"id": "guide:part", "kind": "guide", "files": ["templates/part.md"]}])
        err, _, _ = self.stretch()
        self.assertTrue(any("guide:part is on the map with no check" in e for e in err), err)
        self.added([], [{"from": "a", "to": "b", "kind": "calls", "check": {"contains": "x"}}])
        self.assertEqual(kit_map.unchecked(kit_map.load_added(self.d)), ["a -calls-> b"])

    def test_dot_folders_and_deleted_files_are_not_asked_for(self):
        write(self.d, ".kit/copy.py", "x = 1\n")
        os.remove(os.path.join(self.d, "app/cli.py"))
        err, _, _ = self.stretch()
        self.assertEqual(err, [])


class TestOnlyABrowserSeesIt(StepRepo):
    def probe(self, confirmed):
        return {"id": "page:settings", "kind": "page", "files": [],
                "check": {"probe": "Open Settings > Sharing: only the owner", "confirmed": confirmed}}

    def test_never_confirmed_or_too_old_is_unconfirmed_never_fine(self):
        old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        for when in (None, old):
            self.added([self.probe(when)])
            err, notes, _ = self.stretch()
            self.assertEqual(err, [])          # a part with no browser is not refused for it
            self.assertTrue(any("unconfirmed: page:settings" in n and "Open Settings" in n for n in notes), notes)
            rc, msg = kit_map.check(self.d, run_gens=False)
            self.assertEqual(rc, 0)
            self.assertTrue(msg.startswith("UNCONFIRMED: 1 thing"), msg)
            self.assertNotIn("map fresh", msg)
            with open(os.path.join(self.d, ".board", "map", "map.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            self.assertIn("unconfirmed", [f["kind"] for f in d["flags"]])

    def test_confirmed_recently_is_fine_and_the_days_come_from_the_kit_config(self):
        recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        self.added([self.probe(recent)])
        self.assertEqual(kit_map.unconfirmed(kit_map.load_added(self.d)), [])
        rc, msg = kit_map.check(self.d, run_gens=False) if kit_map.build(self.d, run_gens=False) else (1, "")
        self.assertTrue(msg.startswith("map fresh"), msg)
        write(self.d, ".kit/config.json", json.dumps({"map": {"confirm_days": 3}}))
        _, notes, _ = self.stretch()
        self.assertTrue(any("last confirmed %s" % recent in n for n in notes), notes)


class TestTheGuidesAndTheBar(unittest.TestCase):
    def test_the_part_guides_say_it_in_two_lines(self):
        for rel in ("kit/part.template.md", "kit/claude-cli/part.template.md", "kit/claude/part.template.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("Confirm your stretch of the map", text, rel)
            self.assertIn("Add what you built with its check", text, rel)

    def test_the_kits_own_added_entries_all_have_a_check_that_holds(self):
        added = kit_map.load_added(ROOT)
        self.assertGreater(len(added["things"]), 5)
        self.assertEqual(kit_map.unchecked(added), [])
        for t in added["things"]:
            c = t["check"]
            if not c.get("probe"):
                self.assertIsNone(kit_map.run_check(ROOT, c), t["id"])

    def test_map_steps_counts_the_last_steps_changed_files(self):
        out = tempfile.mkdtemp(prefix="m3-map-")
        try:
            r = subprocess.run([sys.executable, "-I", KIT, "map", "--steps", "10", "--out", out], cwd=ROOT,
                               capture_output=True, text=True, timeout=300)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"last 10 steps: \d+ of \d+ changed files on the map")


if __name__ == "__main__":
    unittest.main()
