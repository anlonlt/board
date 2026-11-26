"""M8: replacing and forgetting in memory.

1. An entry that replaces another names it (`replaces: <id>`); the old one leaves the memory for the archive, with a
   pointer to its successor (ledger rows and lessons alike).
2. `kit.py lessons check` flags two live entries on the same files or theme that contradict each other (opposite verbs
   on one subject, one setting with two values) and asks which one wins.
3. An entry unconfirmed for longer than `kit.memory.review_days` goes to a review list on the board, never deleted.
4. A second claim of authority in the guides is flagged (the French repo: two docs each claiming to rank first).

Run via: python3 -m unittest tests.test_memory_replace -v
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("ledger_m8", str(ROOT / "kit" / "ledger.py"))
led = importlib.util.module_from_spec(_s)
_s.loader.exec_module(led)
sys.path.insert(0, str(ROOT / "scripts"))
import kit  # noqa: E402


def item(i, text, files=(), theme="", confirmed=""):
    return {"id": i, "text": text, "files": list(files), "theme": theme, "source": "x", "confirmed": confirmed}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.m = str(self.root / ".board" / "memory")


class LedgerReplace(Tmp):
    def ids(self):
        return [e["id"] for e in led.load_entries(self.m)]

    def test_replaces_moves_the_old_row_to_the_archive_with_its_successor(self):
        led.observe(self.m, "Reviews run every 4 steps", kind="decision", entry_id="D1")
        r = led.observe(self.m, "Reviews run every 6 steps", kind="decision", replaces="D1")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.ids(), ["D2"])                                # a session never meets D1 again
        self.assertEqual(led.load_entries(self.m)[0]["replaces"], "D1")
        gone = led.load_archive(self.m)
        self.assertEqual([(e["id"], e["status"], e["replaced_by"]) for e in gone], [("D1", "replaced", "D2")])
        self.assertIn("replaced_by: D2", Path(led.archive_path(self.m)).read_text())
        self.assertEqual(led.check_ledger(self.m)["errors"], [])
        self.assertNotIn("every 4 steps", "\n".join(led.format_resume(self.m, "orchestrator")))

    def test_a_replaces_that_names_nothing_live_is_refused(self):
        self.assertIn("error", led.observe(self.m, "x y z", kind="lesson", replaces="L9"))
        self.assertEqual(self.ids(), [])

    def test_replace_after_the_fact_and_pointers_follow(self):
        led.observe(self.m, "Text is 16px on phones", kind="rule", entry_id="R1", theme="design")
        led.observe(self.m, "Text is 14px on phones", kind="rule", entry_id="R2", theme="design")
        r = led.replace(self.m, "R2", "R1")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.ids(), ["R2"])
        self.assertIn("error", led.replace(self.m, "R2", "R2"))
        self.assertIn("error", led.replace(self.m, "R9", "R2"))

    def test_a_replaced_row_left_in_the_ledger_or_a_live_one_named_is_flagged(self):
        Path(self.m).mkdir(parents=True)
        Path(led.ledger_path(self.m)).write_text(
            "# Memory Ledger\n\n## R1 (rule, active): Never push to main\nscope: project\n\n"
            "## R2 (rule, active): Push to main after review\nscope: project\nreplaces: R1\n\n"
            "## R3 (rule, replaced): old\nscope: project\nreplaced_by: R2\n")
        err = "\n".join(led.check_ledger(self.m)["errors"])
        self.assertIn("R2 replaces R1, which is still live", err)
        self.assertIn("R3 is replaced but still in the ledger", err)

    def test_keep_dates_a_confirmation(self):
        led.observe(self.m, "Text is 16px", kind="rule", entry_id="R1")
        r = led.keep(self.m, "R1", when="2026-11-26T00:00:00Z")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(led.load_entries(self.m)[0]["confirmed_at"], "2026-11-26T00:00:00Z")
        self.assertEqual(led.entry_items(led.load_entries(self.m))[0]["confirmed"], "2026-11-26T00:00:00Z")
        self.assertIn("error", led.keep(self.m, "R9"))


class Contradictions(unittest.TestCase):
    def test_opposite_verbs_on_the_same_files(self):
        a = item("K1", "Push the board mirror after each save.", files=["kit/web_takeover.py"])
        b = item("K2", "Never push the board mirror from a part.", files=["kit/web_takeover.py"])
        why = led.contradiction(a, b)
        self.assertIn("K1 says push", why)
        self.assertIn("K2 says never push", why)

    def test_one_setting_two_values(self):
        a = item("D1", "Reviews: `review.train_every = 4` steps", theme="review")
        b = item("D2", "Reviews: `review.train_every = 6` steps", theme="review")
        self.assertIn("review.train_every = 4", led.contradiction(a, b))
        c = item("R1", "Body text is 16px on phones", theme="design")
        d = item("R2", "Body text is 14px on phones", theme="design")
        self.assertIn("text (px)", led.contradiction(c, d))

    def test_opposite_pair_of_verbs(self):
        a = item("R1", "Show the header row on phones", theme="design")
        b = item("R2", "Hide the header row on phones", theme="design")
        self.assertIn("R1 says show, R2 says hide", led.contradiction(a, b))

    def test_agreeing_or_unrelated_entries_are_not_flagged(self):
        a = item("R1", "Never push to main without a review", theme="git")
        b = item("R2", "Never push to main from a part", theme="git")
        c = item("R3", "Text is 16px", theme="design")
        d = item("R4", "Never use emoji in the board", theme="design2")
        self.assertEqual(led.contradictions([a, b, c, d]), [])

    def test_common_words_do_not_make_a_subject(self):
        many = [item("K%d" % n, "The kit board post %d rule %s" % (n, w)) for n, w in enumerate("abcdefg")]
        many += [item("X1", "Always push the kit board post."), item("X2", "Never push the kit board post.")]
        self.assertEqual(led.contradictions(many), [])                      # kit, board, post: everyone's words


class Authority(Tmp):
    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_two_docs_each_claiming_to_rank_first(self):
        a = self.write("docs/REGLES.md", "# Règles\n\nCe document fait foi : il prime sur tous les autres.\n")
        b = self.write("docs/GUIDE.md", "# Guide\n\nThis guide ranks first over every other doc.\n")
        c = self.write("README.md", "# Game\n\nPlay it.\n")
        claims = led.authority_claims([a, b, c], root=str(self.root))
        self.assertEqual(sorted((x["file"], x["authority"]) for x in claims),
                         [("docs/GUIDE.md", "docs/guide.md"), ("docs/REGLES.md", "docs/regles.md")])
        out = led.authority_conflicts(claims)
        self.assertEqual([x["kind"] for x in out], ["conflict"])
        self.assertIn("2 files each claim to rank first", out[0]["why"])

    def test_one_authority_said_once_is_fine_said_twice_is_a_repeat(self):
        a = self.write("AGENTS.md", "The board is the one record of where things stand.\n")
        b = self.write("docs/x.md", "Nothing here.\n```\nthis file ranks first\n```\n")
        self.assertEqual(led.authority_conflicts(led.authority_claims([a, b], root=str(self.root))), [])
        c = self.write("CLAUDE.md", "Where things stand: the board is the source of truth.\n")
        out = led.authority_conflicts(led.authority_claims([a, b, c], root=str(self.root)))
        self.assertEqual([(x["kind"], x["authority"]) for x in out], [("repeat", "the board")])


class Stale(unittest.TestCase):
    def test_unconfirmed_for_more_than_days(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        got = led.stale([item("A", "a", confirmed="2026-11-20"), item("B", "b", confirmed="2026-01-02T10:00:00Z"),
                         item("C", "c", confirmed=""), item("D", "d", confirmed="O53 2026-08-01 22:04")], 90, now=now)
        self.assertEqual([(x["id"], x["age"]) for x in got], [("C", None), ("B", 328), ("D", 117)])


class KitSide(Tmp):
    """The lessons (kit/lessons.md and the memory's lessons/*.md) through kit.py: replace, check, review, keep."""

    def setUp(self):
        super().setUp()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (self.root / "kit").mkdir()
        (self.root / "kit" / "lessons.md").write_text(
            "# lessons\n\n## K1: launch from the clone\nroles: orchestrator\nfrom: O53 2026-01-02 10:00\nThe text.\n\n"
            "## K2: push the mirror\nroles: part\nfiles: kit/web_takeover.py\nfrom: CL1 %s\n"
            "Push the board mirror after each save.\n" % today)
        (self.root / ".board" / "memory" / "lessons").mkdir(parents=True)
        (self.root / ".board" / "memory" / "lessons" / "L1.md").write_text(
            "## L1: no mirror push\nroles: part\nfiles: kit/web_takeover.py\nfrom: CL2 %s\n"
            "Never push the board mirror from a part.\n" % today)
        self.cfg = {"kit": {"memory": {"review_days": 30}}}
        for k, v in (("ROOT", str(self.root)), ("CONFIG", {}), ("_kit_config", lambda *a: self.cfg)):
            p = mock.patch.object(kit, k, v)
            p.start()
            self.addCleanup(p.stop)

    def ids(self):
        return [x["id"] for x in kit.all_lessons()]

    def test_check_flags_the_pair_and_asks_which_wins(self):
        out = "\n".join(kit.memory_check_text())
        self.assertIn("CONTRADICTS: both on kit/web_takeover.py: K2 says push, L1 says never push", out)
        self.assertIn("Which one wins? `python3 -I .kit/kit.py lessons replace K2 L1`", out)
        self.assertIn("REVIEW: 1 entries unconfirmed for more than 30 days (K1)", out)

    def test_replace_a_lesson_it_leaves_for_the_archive(self):
        r = kit.memory_replace("L1", "K2")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.ids(), ["K1", "L1"])
        self.assertNotIn("K2", (self.root / "kit" / "lessons.md").read_text())
        arch = (self.root / ".board" / "memory" / "archive" / "lessons.md").read_text()
        self.assertIn("## K2: push the mirror\nreplaced_by: L1\nroles: part", arch)
        self.assertIn("replaces: K2", (self.root / ".board" / "memory" / "lessons" / "L1.md").read_text())
        self.assertEqual([c for c in kit.memory_conflicts() if c["kind"] == "contradiction"], [])
        self.assertIn("error", kit.memory_replace("L1", "K9"))

    def test_an_installed_kit_lesson_stays_in_its_file_but_is_gone(self):
        (self.root / "kit" / "lessons.md").rename(self.root / "lessons.tmp")
        (self.root / "kit").rmdir()
        (self.root / ".kit").mkdir()
        (self.root / "lessons.tmp").rename(self.root / ".kit" / "lessons.md")
        self.assertTrue(kit.memory_replace("L1", "K2").get("ok"))
        self.assertIn("K2", (self.root / ".kit" / "lessons.md").read_text())   # the kit's update would put it back
        self.assertEqual(self.ids(), ["K1", "L1"])

    def test_lesson_add_with_replaces(self):
        r = kit.lesson_add("L2", ["part"], "mirror", "Push the mirror only from the orchestrator.", replaces="L1")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.ids(), ["K1", "K2", "L2"])
        self.assertFalse((self.root / ".board" / "memory" / "lessons" / "L1.md").exists())     # its file emptied: gone

    def test_a_lesson_naming_a_live_one_it_replaces_is_flagged(self):
        f = self.root / ".board" / "memory" / "lessons" / "L1.md"
        f.write_text(f.read_text().replace("roles: part\n", "roles: part\nreplaces: K1\n"))
        self.assertTrue(any(c["kind"] == "still-live" and c["ids"] == ["L1", "K1"] for c in kit.memory_conflicts()))

    def test_review_goes_to_one_question_post_and_keep_takes_it_off(self):
        writes, posts = [], {}

        def bw(cmd, coll, pid, data, env=None):
            writes.append((cmd, pid, data))
            posts[pid] = dict(posts.get(pid) or {}, **data)
            return True
        with mock.patch.object(kit, "_board_write", bw), mock.patch.object(kit, "_all_posts", lambda *a, **k: posts), \
                mock.patch.object(kit, "_post_gate", lambda: None):
            r = kit.memory_review_post()
            self.assertEqual((r["post"], r["count"], r["days"]), ("q-memory-to-review", 1, 30))
            doc = posts["q-memory-to-review"]
            self.assertEqual((doc["kind"], doc["default_option"]), ("question", "Keep them all"))
            self.assertIn("**K1**", doc["body"])
            self.assertIn("Nothing leaves the memory unless you say so", doc["body"])
            self.assertEqual(kit.memory_review_post()["updated"], True)       # updated, never doubled
            self.assertEqual([w[0] for w in writes], ["set", "update"])
            self.assertTrue(kit.memory_keep("K1").get("ok"))
            self.assertEqual(kit.memory_stale()[1], [])
            self.assertEqual(kit.memory_review_post()["count"], 0)
        self.assertIn("K1", self.ids())                                         # nothing deleted


if __name__ == "__main__":
    unittest.main()
