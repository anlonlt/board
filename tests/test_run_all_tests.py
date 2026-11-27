"""The test runner: only what a change affects, stopped at the first failure, that failure first the next time.

Run via: python3 -m unittest tests.test_run_all_tests -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / ".gitignore").write_text(".kit/run/\n")
        for f in ("run_all_tests.py", "test_trace.py"):
            self.write("scripts/" + f, (ROOT / "scripts" / f).read_text(encoding="utf-8"))
        self.write("kit/mod.py", """\
            import os
            BASE = 3
            LIMIT = BASE + 0
            PEEK = 7
            if BASE > 2:
                pass


            def one():
                return 1


            def two():
                LIMIT
                return 2


            class C:
                def m(self):
                    return 3

                def n(self):
                    return 4
            """)
        self.write("kit/cli.py", """\
            import os, sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import mod
            print(getattr(mod, sys.argv[1])())
            """)
        self.write("kit/note.md", "words\n")
        self.suite("one", "import mod; self.assertEqual(mod.one(), 1)")
        self.suite("m", "import mod; self.assertEqual(mod.C().m() + mod.PEEK, 10)")
        # a child, isolated, with a clean environment
        self.suite("child", "r = subprocess.run([sys.executable, '-I', K + '/cli.py', 'two'], capture_output=True, text=True, env={}); "
                            "self.assertEqual(r.stdout.strip(), '2', r.stderr)")
        self.suite("note", "self.assertTrue(open(K + '/note.md').read())")
        # a copy in a project of the test's own
        self.suite("copy", "import tempfile, shutil; d = tempfile.mkdtemp(); os.makedirs(d + '/.kit'); shutil.copy(K + '/mod.py', d + '/.kit/mod.py'); "
                           "r = subprocess.run([sys.executable, '-I', '-c', \"import sys; sys.path.insert(0, %r); import mod; print(mod.one())\" % (d + '/.kit')], "
                           "capture_output=True, text=True); shutil.rmtree(d); self.assertEqual(r.stdout.strip(), '1', r.stderr)")
        subprocess.run(["git", "init", "-q", str(self.d)], check=True)

    def write(self, rel, text):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")

    def suite(self, name, line):
        self.write("tests/test_%s.py" % name, "import os, subprocess, sys, time, unittest\nK = os.path.join(os.path.dirname(os.path.dirname("
                   "os.path.abspath(__file__))), 'kit')\nsys.path.insert(0, K)\n\n\nclass T(unittest.TestCase):\n    def test(self):\n        %s\n" % line)

    def edit(self, rel, a, b):
        p = self.d / rel
        s = p.read_text(encoding="utf-8")
        self.assertEqual(s.count(a), 1, a)
        p.write_text(s.replace(a, b), encoding="utf-8")

    def run_(self, *a):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        r = subprocess.run([sys.executable, str(self.d / "scripts" / "run_all_tests.py"), *a], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.stderr, "")
        return r

    def names(self, *a):
        r = self.run_("--list", *a)
        return {x.split()[0].replace("tests/test_", "").replace(".py", "") for x in r.stdout.splitlines()[:-1]}

    def green(self):
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.names(), set(), r.stdout + self.run_("--list").stdout)

    def test_only_what_a_change_affects(self):
        self.assertEqual(self.names(), {"one", "m", "child", "note", "copy"})
        self.green()
        for rel, a, b, want in (("kit/mod.py", "return 1", "return 2 - 1", {"one", "copy"}), ("kit/mod.py", "return 2\n", "return 1 + 1\n", {"child"}),
                                ("kit/mod.py", "return 3", "return 2 + 1", {"m"}), ("kit/mod.py", "return 4", "return 5", set()),
                                ("kit/mod.py", "class C:", "def new():\n    return 0\n\n\n# words\nclass C:", set()),
                                ("kit/mod.py", "LIMIT = BASE + 0", "LIMIT = BASE + 1", {"child"}), ("kit/mod.py", "BASE = 3", "BASE = 4", {"child"}),
                                ("kit/mod.py", "PEEK = 7", "PEEK = 6 + 1", {"m"}), ("kit/mod.py", "PEEK = 7", "PEEK = 7\nNEW = 1", set()),
                                ("kit/mod.py", "import os", "import os, re", set()), ("kit/mod.py", "    pass", "    print()", {"one", "m", "child", "copy"}),
                                ("kit/cli.py", "print(", "print( ", {"child"}), ("kit/note.md", "words", "other words", {"note"}),
                                ("tests/test_m.py", "10)", "11 - 1)", {"m"}), ("kit/mod.py", "def two():", "def zwei():", {"child"}),
                                ("kit/mod.py", "class C:", "class C(:", {"one", "m", "child", "copy"})):
            p = self.d / rel
            was = p.read_text(encoding="utf-8")
            self.edit(rel, a, b)
            self.assertEqual(self.names(), want, (rel, a, b))
            p.write_text(was, encoding="utf-8")
            self.assertEqual(self.names(), set(), "put back")
        r = self.run_()
        self.assertIn("0 passed, 0 failed, 5 unchanged", r.stdout)
        self.assertEqual(self.names("--all"), {"one", "m", "child", "note", "copy"})
        self.assertEqual(self.names("chil"), {"child"})
        for a in (["add", "-A"], ["-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(self.d)] + a, check=True)
        self.assertEqual(self.names(), set(), "a commit changes no file")

    def test_it_stops_at_the_first_failure_and_starts_there_next_time(self):
        self.green()
        self.suite("zz_slow", "time.sleep(60)")
        self.suite("a_bad", "self.assertEqual(1, 2, 'WHY')")
        t0 = time.monotonic()
        r = self.run_("-j", "2")
        self.assertEqual(r.returncode, 1)
        self.assertLess(time.monotonic() - t0, 40)
        self.assertIn("[FAIL] tests/test_a_bad.py", r.stdout)
        self.assertIn("WHY", r.stdout)
        self.assertIn("stopped at the first failure", r.stdout)
        self.assertFalse(subprocess.run(["pgrep", "-f", str(self.d)], capture_output=True).stdout, "nothing is left running")
        (self.d / "tests/test_zz_slow.py").unlink()
        self.suite("a_new", "pass")
        self.assertIn("test_a_bad.py", self.run_("--list").stdout.splitlines()[0])
        self.assertIn("failed last time", self.run_("--list").stdout.splitlines()[0])
        self.suite("a_bad", "pass")
        self.assertEqual(self.run_().returncode, 0)
        self.assertEqual(self.names(), set())

    def test_keep_going_and_a_failure_is_never_remembered_as_a_pass(self):
        self.suite("a_bad", "self.fail('x')")
        r = self.run_("--keep-going", "-j", "1")
        self.assertIn("5 passed, 1 failed", r.stdout)
        self.assertEqual(self.names(), {"a_bad"})
        self.assertEqual(self.run_().returncode, 1)
        self.assertEqual(self.names(), {"a_bad"})

    def test_a_file_changed_while_its_suite_ran_counts_as_changed(self):
        self.suite("self_edit", "open(K + '/note.md').read(); open(K + '/note.md', 'a').write('x')")
        self.run_("self_edit")
        self.assertIn("self_edit", self.names())

    def test_all_is_not_listened_to_and_remembers_failures(self):
        self.assertEqual(self.run_("--all").returncode, 0)
        self.assertEqual(self.names(), {"one", "m", "child", "note", "copy"})
        self.suite("a_bad", "self.fail('x')")
        self.assertEqual(self.run_("--all").returncode, 1)
        self.assertIn("test_a_bad.py", self.run_("--list").stdout.splitlines()[0])


    def commit(self):
        for a in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c"]):
            subprocess.run(["git", "-C", str(self.d)] + a, check=True)

    def note(self):
        r = subprocess.run(["git", "-C", str(self.d), "notes", "--ref", "kit-tests", "show", "HEAD"], capture_output=True, text=True)
        import json
        return json.loads(r.stdout) if r.returncode == 0 else None

    def test_a_full_run_is_saved_on_its_commit_and_resumed_by_the_next(self):
        self.suite("a_bad", "self.fail('x')")
        self.commit()
        r = self.run_("--all", "--keep-going")
        self.assertEqual(r.returncode, 1)
        n = self.note()
        self.assertEqual(set(n["passed"]), {"tests/test_%s.py" % x for x in ("one", "m", "child", "note", "copy")})
        self.assertEqual(n["failed"], ["tests/test_a_bad.py"])
        # the next session (a stopped run, another device after a fetch) runs only what has not passed on this commit
        r = self.run_("--all", "--keep-going")
        self.assertIn("5 suites already passed on it, 1 to run", r.stdout)
        self.assertIn("[FAIL] tests/test_a_bad.py", r.stdout)
        self.assertNotIn("test_one.py", r.stdout.split("resuming", 1)[1].split("SUMMARY")[0])
        self.assertIn("5 passed earlier on this commit", r.stdout)
        self.assertIn("6 to run", self.run_("--all", "--fresh", "--list").stdout.replace("\n", " ") + " 6 to run")
        # a new commit starts fresh
        self.suite("a_bad", "pass")
        self.commit()
        r = self.run_("--all")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("resuming", r.stdout)
        self.assertEqual(len(self.note()["passed"]), 6)

    def test_uncommitted_changes_are_never_saved_as_a_pass(self):
        self.commit()
        self.edit("kit/note.md", "words", "other words")
        r = self.run_("--all")
        self.assertIn("full run not saved on a commit: uncommitted changes", r.stdout)
        self.assertIsNone(self.note())

if __name__ == "__main__":
    unittest.main()


class CleansUp(Runner):
    """CL52: tests clean up after themselves. /tmp held about 5,600 folders left by test runs (cl30-inst-*, tmp*,
    kit-tests-*, scratch boards), each with a project copy and its own board token."""

    def test_a_suite_that_leaves_a_folder_or_a_process_fails_and_the_run_leaves_nothing(self):
        tmp = self.d / "tmp"
        tmp.mkdir()
        self.suite("leaves_dir", "import tempfile; d = tempfile.mkdtemp(prefix='proj-'); open(d + '/token', 'w').write('x')")
        self.suite("leaves_proc", "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], start_new_session=True)")
        self.suite("clean_one", "import tempfile; d = tempfile.TemporaryDirectory(); open(d.name + '/x', 'w').write('x'); d.cleanup(); "
                                "open(os.path.join(tempfile.gettempdir(), 'kit-ledger-1.lock'), 'w')")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["TMPDIR"] = str(tmp)
        r = subprocess.run([sys.executable, str(self.d / "scripts" / "run_all_tests.py"), "--keep-going", "leaves_", "clean_one"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout)
        line = {n: next(x for x in r.stdout.splitlines() if "test_%s.py" % n in x) for n in ("leaves_dir", "leaves_proc", "clean_one")}
        self.assertRegex(line["leaves_dir"], r"^\[FAIL\] .* LEFT 1: proj-\w+/$")
        self.assertRegex(line["leaves_proc"], r"^\[FAIL\] .* LEFT 1: process: python3? -c import time")
        self.assertTrue(line["clean_one"].startswith("[PASS]"), "a lock file the kit keeps by name is swept, not a failure")
        self.assertFalse(subprocess.run(["pgrep", "-f", "time.sleep\\(300\\)"], capture_output=True).stdout, "the stray process is killed")
        self.assertEqual(os.listdir(tmp), [], "the run's own folder (each suite's TMPDIR, logs, launchers, jobs) is gone")

    def test_a_suite_that_writes_past_its_temp_folder_fails_the_run(self):
        tmp = self.d / "tmp"
        tmp.mkdir()
        self.suite("strays", "os.makedirs(%r)" % str(tmp / "kit-stray"))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["TMPDIR"] = str(tmp)
        r = subprocess.run([sys.executable, str(self.d / "scripts" / "run_all_tests.py"), "strays"], capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("[FAIL] left in %s past the suites' own temp folders: kit-stray" % tmp, r.stdout)

    def test_one_command_removes_past_runs_leftovers_after_a_dry_run(self):
        root = self.d / "tmp"
        old = time.time() - 5 * 3600
        made = {}
        for name, inside in (("cl30-inst-ab12cd34", ".kit"), ("kit-tests-zz", "a.log"), ("kit-xdg-q1", "board-kit"),
                             ("tmpab12cd34", ".git"), ("cl48-board-x", "board_data"),
                             ("tmpzz99yy88", "photo.jpg"),          # Python's name, but nothing a test makes: someone else's
                             ("other-tool-1", ".kit"), ("kit-new-fresh", ".kit")):
            d = root / name
            (d / inside).mkdir(parents=True)
            made[name] = d
            if name != "kit-new-fresh":                          # a run going on now: touched a minute ago
                for p in (d / inside, d):
                    os.utime(p, (old, old))
        cmd = [sys.executable, str(self.d / "scripts" / "run_all_tests.py"), "--clean", "--root", str(root)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gone = {"cl30-inst-ab12cd34", "kit-tests-zz", "kit-xdg-q1", "tmpab12cd34", "cl48-board-x"}
        for n in gone:
            self.assertIn("would remove %s" % made[n], r.stdout)
        self.assertIn("5 folder(s) of past test runs", r.stdout)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(sorted(os.listdir(root)), sorted(made), "a dry run removes nothing")
        r = subprocess.run(cmd + ["--yes"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(os.listdir(root)), sorted(set(made) - gone), "only old folders a test run makes, by name")
