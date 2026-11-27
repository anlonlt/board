"""CL76: every part gets the idea first. The owner, 2026-11-27: "Every part needs to extrapolate, needs to stress test the
shit out of things, needs to get the idea of what I'm saying."

R6 was asked that every new step be measured against every open one; it linked steps by names, `after` and shared files,
wrote 7 fixture cases that all used those links, passed its own tests and its review, and missed the plain case: two steps
on one subject that name nothing in common (M14, then CL75). These tests are written from his intent, case by case
(I1..I7 on the CL76 post), each with a stress variant, and the real case: R6's own post replayed through `done`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import kit  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "r6_post.json")

R6_INTENT = """goal: "every new step is measured against every open one" (owner, 2026-11-26)
- I1: a step that names another (after, waits on) is ranked behind it
- I2: two steps touching the same files never run together
- I3 [outside]: two steps on the same subject that name nothing in common (M14, then CL75) are seen as overlapping
real: the live board's open steps, ranked
"""
# a topic-only test: written from the subject of the two steps, not from the links R6 built
TOPIC_TEST = '''
def test_same_subject_no_shared_names():   # I3, stress I3: M14 and CL75 share no name, file or `after`
    assert overlap("the usage ledger says what each review finds", "review only what a test cannot judge")
def test_named_wait():                      # I1, stress I1: a wait written in the task, no `after` field
    pass
def test_same_files():                      # I2, stress I2: kit.py and .kit/kit.py are one file
    pass
'''


def r6():
    with open(FIX, encoding="utf-8") as fh:
        p = json.load(fh)
    p["_id"] = "r6-the-next-step-is-the-most-important-one"
    return p


def r6_tests_as_added():
    """The tests R6 added, as they are in this repo (R6 landed them)."""
    with open(os.path.join(ROOT, "tests", "test_queue_rank.py"), encoding="utf-8") as fh:
        return fh.read()


def with_intent(p, text=R6_INTENT, at="2026-11-26T23:00:00Z"):
    it = kit.parse_intent(text)
    it["at"] = at
    return dict(p, intent=it)


class ReplayR6(unittest.TestCase):
    """real: R6's post as it was, through done's intent check."""

    def test_r6_as_it_was_is_refused(self):
        """I1: no intent on the post: refused, and the refusal says what to write and how to send it."""
        err = kit.check_intent(r6(), added=r6_tests_as_added(), code_at="")
        self.assertTrue(err)
        self.assertIn("intent: missing", err[0])
        self.assertIn("kit.py intent", err[0])
        self.assertIn("[outside]", err[0])

    def test_stress_I1_r6_tests_with_a_restated_intent_still_refused(self):
        """stress I1: R6 writes an intent after the fact but its tests (the real ones) name no case: each case refused by id."""
        err = kit.check_intent(with_intent(r6()), added=r6_tests_as_added(), code_at="")
        for cid in ("I1", "I2", "I3"):
            self.assertTrue(any(e.startswith("intent: %s has no test" % cid) for e in err), (cid, err))

    def test_r6_with_intent_and_topic_only_test_passes(self):
        """real: the same task, with the intent section and a topic-only test: accepted."""
        self.assertEqual(kit.check_intent(with_intent(r6()), added=TOPIC_TEST, code_at="2026-11-26T23:30:00Z"), [])


class Cases(unittest.TestCase):
    def test_I2_each_missing_case_is_named(self):
        """I2: tests cover I1 only: I2 and I3 are each named with their text, not a generic "something is missing"."""
        added = "def test_a():  # I1, stress I1\n    pass\n"
        err = kit.check_intent(with_intent(r6()), added=added, code_at="")
        self.assertFalse(any("I1 " in e for e in err), err)
        self.assertTrue(any("I2 has no test" in e and "same files" in e for e in err), err)
        self.assertTrue(any("I3 has no test" in e and "same subject" in e for e in err), err)

    def test_stress_I2_tested_but_not_stressed(self):
        """stress I2: each case tested on its happy path only: each named as having no stress test."""
        added = "# I1\n# I2\n# I3\n"
        err = kit.check_intent(with_intent(r6()), added=added, code_at="")
        self.assertEqual(sorted(e.split()[1] for e in err), ["I1", "I2", "I3"], err)
        self.assertTrue(all("no stress test" in e for e in err), err)

    def test_stress_I2_ids_do_not_match_inside_longer_ids(self):
        """stress I2: `I10` and `I1x` do not count as a test of I1."""
        added = "# I10 stress I10 I1x stress I1x I2 stress I2 I3 stress I3\n"
        err = kit.check_intent(with_intent(r6()), added=added, code_at="")
        self.assertEqual(len(err), 1, err)
        self.assertIn("I1 has no test", err[0])

    def test_I4_not_covered_with_a_reason_needs_no_test(self):
        """I4: a case the plan leaves, with its reason, is accepted without a test."""
        text = R6_INTENT + "- I4: a step on another repo | not covered: no cross-repo board yet (CL56)\n"
        err = kit.check_intent(with_intent(r6(), text), added=TOPIC_TEST, code_at="")
        self.assertEqual(err, [])

    def test_stress_I4_every_case_not_covered_is_refused(self):
        """stress I4: a plan that "honestly" covers none of the cases is a plan that ignores what he meant: refused."""
        text = ('goal: "g"\n- I1: a | not covered: later\n- I2: b | not covered: later\n- I3 [outside]: c | not covered: '
                'later\nreal: none: no data\n')
        err = kit.check_intent(with_intent(r6(), text), added="", code_at="")
        self.assertTrue(any("every case is `not covered`" in e for e in err), err)

    def test_stress_I4_not_covered_without_a_reason_is_refused(self):
        """stress I4: `not covered:` with nothing after it is refused, and the case is not silently skipped."""
        text = R6_INTENT + "- I4: a step on another repo | not covered:\n"
        err = kit.check_intent(with_intent(r6(), text), added=TOPIC_TEST, code_at="")
        self.assertTrue(any("I4 says `not covered` without a reason" in e for e in err), err)

    def test_I5_two_cases_refused(self):
        """I5: fewer than 3 cases is refused."""
        text = 'goal: "x"\n- I1: a\n- I2 [outside]: b\nreal: none: no data\n'
        err = kit.check_intent(with_intent(r6(), text), added="I1 stress I1 I2 stress I2", code_at="")
        self.assertTrue(any("2 case(s); at least 3" in e for e in err), err)

    def test_stress_I5_no_outside_case_no_goal_no_real(self):
        """stress I5: three cases that only restate the task (none [outside]), no goal, no real line: each refused."""
        text = "- I1: a\n- I2: b\n- I3: c\n"
        err = kit.check_intent(with_intent(r6(), text), added="I1 stress I1 I2 stress I2 I3 stress I3", code_at="")
        self.assertTrue(any("no case marked [outside]" in e for e in err), err)
        self.assertTrue(any("no `goal:` line" in e for e in err), err)
        self.assertTrue(any("no `real:` line" in e for e in err), err)

    def test_stress_I5_duplicate_ids(self):
        """stress I5: two cases with one id would let one test count for both: refused."""
        text = R6_INTENT + "- I3: again\n"
        err = kit.check_intent(with_intent(r6(), text), added=TOPIC_TEST, code_at="")
        self.assertTrue(any("used twice: I3" in e for e in err), err)

    def test_I6_intent_after_code_refused(self):
        """I6: the first code commit is older than the intent: refused."""
        err = kit.check_intent(with_intent(r6(), at="2026-11-27T01:00:00Z"), added=TOPIC_TEST, code_at="2026-11-27T00:10:00+00:00")
        self.assertTrue(any("after the step's first code commit" in e for e in err), err)

    def test_stress_I6_timezones_compared_as_instants(self):
        """stress I6: a commit at 20:30 -04:00 is 00:30Z, after an intent at 00:10Z: accepted (a string compare would refuse)."""
        err = kit.check_intent(with_intent(r6(), at="2026-11-27T00:10:00Z"), added=TOPIC_TEST, code_at="2026-11-26T20:30:00-04:00")
        self.assertEqual(err, [])

    def test_stress_I6_editing_the_intent_keeps_its_first_date(self):
        """stress I6: an intent refined later keeps the date it was first written; the change is dated apart."""
        first = kit.stamp_intent(kit.parse_intent(R6_INTENT), None)
        later = kit.stamp_intent(kit.parse_intent(R6_INTENT + "- I4: more\n"), dict(first, at="2026-11-26T22:00:00Z"))
        self.assertEqual(later["at"], "2026-11-26T22:00:00Z")
        self.assertNotEqual(later["updated_at"], "2026-11-26T22:00:00Z")

    def test_I7_review_asks_first_with_the_cases(self):
        """I7: the review prompt opens with "does it do what he meant", with every case, before any rerun of the bars."""
        p = with_intent(r6())
        brief = kit.review_brief(p, seed=1)
        first = brief.strip().splitlines()[0]
        self.assertIn("does it do what he meant", first)
        for cid in ("I1", "I2", "I3 [outside]"):
            self.assertIn("- %s" % cid, brief)
        self.assertLess(brief.index("I3 [outside]"), brief.index("recheck:"))

    def test_stress_I7_review_of_a_step_with_no_intent_still_asks(self):
        """stress I7: an old step with no intent: the review still asks his meaning first, and says the intent is missing."""
        brief = kit.review_brief(r6(), seed=1)
        self.assertIn("does it do what he meant", brief.strip().splitlines()[0])
        self.assertIn("no intent written", brief)

    def test_stress_I7_every_platform_review_guide_asks_first(self):
        """stress I7: the review guides of agy, claude web and claude-cli ask it before "Does it hold"."""
        for rel in ("kit/consultant.template.md", "kit/claude/consultant.template.md", "kit/claude-cli/consultant.template.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                t = fh.read()
            self.assertIn("does it do what he meant?", t, rel)
            self.assertLess(t.index("does it do what he meant?"), t.index("1. **Does"), rel)


class Templates(unittest.TestCase):
    def test_I1_every_part_template_asks_intent_before_code(self):
        """I1: the part prompt on every platform asks the intent first, with the command that sends it."""
        for rel in ("kit/part.template.md", "kit/claude/part.template.md", "kit/claude-cli/part.template.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                t = fh.read()
            self.assertIn("Intent first, before any code", t, rel)
            self.assertIn("kit.py intent <POST_ID>", t, rel)
            self.assertIn("[outside]", t, rel)
            self.assertIn("stress I1", t, rel)

    def test_stress_I1_a_new_form_has_the_intent_section_and_an_old_one_is_refused(self):
        """stress I1: the form made at turn 1 has `## intent`; a form from before CL76 (no such section) is refused by name."""
        self.assertIn("intent", kit.FORM_PARTS)
        old = "".join("## %s\nx\n\n" % k for k in kit.FORM_PARTS if k not in ("intent", "body")) + "## body\nx\n"
        f, err = kit.read_form(old)
        self.assertIsNone(f)
        self.assertIn("form: `## intent` is missing", err)

    def test_stress_I2_a_case_written_over_two_lines_keeps_its_text(self):
        """stress I2: a case that wraps onto a second line is one case, with all its words."""
        it = kit.parse_intent('goal: "g"\n- I1: two steps on one subject\n  that name nothing in common\n- I2 [outside]: b\nreal: r\n')
        self.assertEqual(len(it["cases"]), 2)
        self.assertIn("name nothing in common", it["cases"][0]["text"])
        self.assertTrue(it["cases"][1]["outside"])


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(a),
                          cwd=cwd, capture_output=True, text=True, check=True)


class Gaming(unittest.TestCase):
    """I3 [outside]: what counts as a test is what the step itself added to a test file, read from git."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cl76-")
        git(self.d, "init", "-q", "-b", "main")
        os.makedirs(os.path.join(self.d, "tests"))
        with open(os.path.join(self.d, "tests", "test_old.py"), "w") as fh:
            fh.write("# I1 stress I1: on the trunk before the step\n")
        with open(os.path.join(self.d, "app.py"), "w") as fh:
            fh.write("x = 1\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "trunk")
        git(self.d, "checkout", "-qb", "step")

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.d])

    def write(self, rel, text, commit=True):
        path = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(text)
        if commit:
            git(self.d, "add", "-A")
            git(self.d, "commit", "-qm", rel)

    def test_I3_ids_only_on_the_trunk_post_or_code_do_not_count(self):
        """I3: the ids are in a trunk test, in the post's own intent and in a code file: none of that is a test."""
        self.write("app.py", "# I1 stress I1 I2 stress I2 I3 stress I3\n")
        self.write("docs/notes_test.md", "I1 stress I1 I2 stress I2 I3 stress I3\n")
        added = kit.step_tests_added(self.d)
        self.assertNotIn("I1", added)
        err = kit.check_intent(with_intent(r6()), root=self.d, code_at="")
        self.assertEqual(sum("has no test" in e for e in err), 3, err)

    def test_stress_I3_an_edited_trunk_test_counts_only_its_new_lines(self):
        """stress I3: the step appends to the trunk's test file: its new lines count, the old I1 line does not."""
        self.write("tests/test_old.py", "# I2 stress I2\n# I3 stress I3\n")
        err = kit.check_intent(with_intent(r6()), root=self.d, code_at="")
        self.assertEqual(len(err), 1, err)
        self.assertIn("I1 has no test", err[0])

    def test_stress_I3_uncommitted_and_untracked_tests_count(self):
        """stress I3: a new test file not committed yet still counts (done runs before the last commit)."""
        self.write("tests/test_new.py", TOPIC_TEST, commit=False)
        self.assertEqual(kit.check_intent(with_intent(r6()), root=self.d, code_at=""), [])

    def test_I6_first_code_commit_is_read_from_git(self):
        """I6: the step's first commit that touches code is found; one touching only docs/ is not code."""
        self.assertIsNone(kit.first_code_commit_at(self.d))
        self.write("docs/plan.md", "plan\n")
        self.assertIsNone(kit.first_code_commit_at(self.d))
        self.write("app.py", "y = 2\n")
        at = kit.first_code_commit_at(self.d)
        self.assertTrue(at)
        err = kit.check_intent(with_intent(r6(), at="2999-01-01T00:00:00Z"), root=self.d, added=TOPIC_TEST)
        self.assertTrue(any("after the step's first code commit" in e for e in err), err)

    def test_stress_I6_on_the_trunk_no_start_date_said_plainly(self):
        """stress I6: run on the trunk itself with no start date on the post: said plainly, never a silent pass."""
        git(self.d, "checkout", "-q", "main")
        err = kit.check_intent(with_intent(r6()), root=self.d, code_at="")
        self.assertTrue(any("no trunk to compare with" in e for e in err), err)

    def test_stress_I3_a_part_working_on_the_trunk_counts_its_commits_since_it_started(self):
        """stress I3: a part on the trunk itself (the web parts' `main`): only its commits since `started_at` count; the
        trunk's old I1 test still does not."""
        git(self.d, "checkout", "-q", "main")
        p = dict(with_intent(r6()), started_at="2000-01-01T00:00:00Z")
        old = subprocess.run(["git", "log", "-1", "--format=%cI"], cwd=self.d, capture_output=True, text=True).stdout.strip()
        p["started_at"] = old                                        # the trunk commit is at (not after) the start
        env = dict(os.environ, GIT_COMMITTER_DATE="2999-01-01T00:00:00Z", GIT_AUTHOR_DATE="2999-01-01T00:00:00Z")
        with open(os.path.join(self.d, "tests", "test_new.py"), "w") as fh:
            fh.write("# I2 stress I2 I3 stress I3\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x"], cwd=self.d, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=self.d)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "step test"], cwd=self.d, env=env)
        p["started_at"] = "2998-01-01T00:00:00Z"
        err = kit.check_intent(p, root=self.d, code_at="")
        self.assertEqual(len(err), 1, err)
        self.assertIn("I1 has no test", err[0])


class DoneRefuses(unittest.TestCase):
    """real: `done` itself calls the intent check (not only the helper)."""

    def test_I1_done_calls_check_intent(self):
        import inspect
        src = inspect.getsource(kit.done)
        self.assertIn("check_intent(", src)

    def test_stress_I1_kit_commands_lists_intent(self):
        out = kit.commands_text("intent")
        self.assertIn("kit.py intent <post>", out)


if __name__ == "__main__":
    unittest.main()
