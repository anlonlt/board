"""M14: the usage ledger says what each review found.

The owner asked (chat, 2026-11-27, orch4-review-ledger-0935): "is the reviews uselessness logged in the usage
ledger?" The ledger had each review's cost, not its outcome. Now each review row gets, from the review's own board
comment (CR-<post>[-N]) and never from a model call: `verdict` (continue | redirect | stop), `found_new` (yes | no)
and `found` (one line). `usage show --role review` prints them per row with a summary line (reviews n, cost $,
found_new k of n, cost per review that found something); `usage outcomes` writes them into the rows (the backfill).

Run via: python3 -m unittest tests.test_m14_review_outcomes -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "usage.py"

spec = importlib.util.spec_from_file_location("kit_usage_m14", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)

# Three reviews, as the board holds them: a redirect, a continue that only reran the step's check and break, and a
# continue whose findings name a real gap.
COMMENTS = {
    "CR-a1-the-first-step": {
        "post_id": "a1-the-first-step", "kind": "review", "from": "consultant", "verdict": "redirect",
        "created_at": "2026-11-20T10:20:00Z",
        "text": "**redirect**: before landing, fix the read so an explicit --db never reads the real board.\n\n"
                "## Holds\n- The check passes.\n\n## Goal\nThe train.\n\n## Verdict\n**redirect.**"},
    "CR-a2-the-second-step": {
        "post_id": "a2-the-second-step", "kind": "review", "from": "consultant", "verdict": "continue",
        "created_at": "2026-11-20T11:20:00Z",
        "text": "**continue**: the claim holds and its check fails when broken.\n\n## Holds\n"
                "- **Sampled bar, rerun** (\"0 gaps left\"): holds.\n"
                "- **Breaks, performed in a scratch copy:** with the bug put back, the check reports the bug.\n"
                "- **Not checked:** the gap on macOS the post already names.\n\n"
                "## Goal\nNo owner try yet: a gap in the train.\n\n## Verdict\n**continue.**"},
    "CR-a3-the-third-step": {
        "post_id": "a3-the-third-step", "kind": "review", "from": "consultant",
        "created_at": "2026-11-20T12:20:00Z",
        "text": "**continue** — it holds (5/5 pass, 5/5 fail on revert). One claim is false: `launch --continue` "
                "does not refuse an open question.\n\n## Holds\n- tests pass.\n\n## Verdict\n**continue.**"},
}


def row(sid, part, started, cost, role="review", **kw):
    return dict({"session_id": sid, "source": "transcript", "role": role, "part": part, "started": started,
                 "recorded_at": "2026-11-20T13:00:00Z", "cost_usd": cost, "cost_usd_all": cost,
                 "tokens": {"output": 1}}, **kw)


ROWS = [
    row("r-redirect", "CR-A1-THE-FIRST-STEP", "2026-11-20T10:00:00Z", 3.00),
    row("r-tests-only", "CR-A2-THE-SECOND-STEP", "2026-11-20T11:00:00Z", 2.00),
    row("r-gap", None, "2026-11-20T12:00:00Z", 1.00, branch="review-a3-the-third-step",
        first_prompt="Your prompt is docs/prompts/CR-a3-the-third-step.md: read it and do it."),
    row("p-part", "A3", "2026-11-20T09:00:00Z", 9.00, role="part",
        first_prompt="You are part A3: review-and-adopt a3-the-third-step"),
]


class ReviewOutcomes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        cdir = self.d / "comments"
        cdir.mkdir()
        for did, c in COMMENTS.items():
            (cdir / ("%s.json" % did)).write_text(json.dumps(c), encoding="utf-8")
        self.comments = str(cdir)
        self.rows = self.d / "rows.jsonl"
        self.rows.write_text("".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
        self.proj = self.d / "proj"
        self.proj.mkdir()
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_usage(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_USAGE", "CLAUDE_CODE"))}
        env["KIT_USAGE_PUSH"] = "0"
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--root", str(self.proj)] + list(args),
                              capture_output=True, text=True, env=env, timeout=60)

    def test_each_review_gets_its_verdict_and_found_new(self):
        res = usage.review_outcomes(ROWS, usage.review_comments(str(self.proj), self.comments))
        got = {sid: (o["verdict"], o["found_new"]) for sid, o in res.items()}
        self.assertEqual(got, {"r-redirect": ("redirect", "yes"), "r-tests-only": ("continue", "no"),
                               "r-gap": ("continue", "yes")})
        self.assertIn("One claim is false", res["r-gap"]["found"])
        self.assertTrue(res["r-tests-only"]["found"].startswith("confirmed the step"))
        self.assertNotIn("p-part", res)                      # a part whose prompt says "review" is not a review

    def test_show_role_review_prints_rows_and_the_summary_line(self):
        p = self.run_usage("show", "--role", "review", "--rows", str(self.rows), "--only", "--comments", self.comments)
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(len(lines), 4, p.stdout)
        self.assertRegex(lines[0], r"r-redire.*redirect\s+found_new=yes")
        self.assertRegex(lines[1], r"r-tests-.*continue\s+found_new=no ")
        self.assertRegex(lines[2], r"r-gap.*continue\s+found_new=yes .*One claim is false")
        self.assertEqual(lines[3], "reviews 3, cost $6.00, found_new 2 of 3, cost per review that found something $3.00")

    def test_several_reviews_of_one_step_go_to_their_rows_in_time_order(self):
        cs = {"CR-b-step": dict(COMMENTS["CR-a2-the-second-step"], post_id="b-step", created_at="2026-11-21T10:10:00Z"),
              "CR-b-step-2": dict(COMMENTS["CR-a1-the-first-step"], post_id="b-step", created_at="2026-11-21T11:10:00Z")}
        rows = [row("first", "CR-B-STEP", "2026-11-21T10:00:00Z", 1.0), row("second", "CR-B-STEP", "2026-11-21T11:00:00Z", 1.0)]
        res = usage.review_outcomes(rows, cs)
        self.assertEqual(res["first"]["review_of"], ["CR-b-step"])
        self.assertEqual(res["second"]["review_of"], ["CR-b-step-2"])
        self.assertEqual((res["second"]["verdict"], res["second"]["found_new"]), ("redirect", "yes"))

    def test_one_session_over_several_steps(self):
        r = row("multi", None, "2026-11-20T09:30:00Z", 4.0, branch="review-a2-the-second-step",
                first_prompt="You are ONE review session for two steps: a2-the-second-step, a1-the-first-step.")
        res = usage.review_outcomes([r], usage.review_comments(str(self.proj), self.comments))
        self.assertEqual(res["multi"]["verdict"], "redirect")    # the worst verdict of the two
        self.assertEqual(res["multi"]["found_new"], "yes")
        self.assertTrue(res["multi"]["found"].startswith("1 of 2 found something"))

    def test_outcomes_backfills_the_rows_and_a_rerecord_keeps_them(self):
        p = self.run_usage("outcomes", "--rows", str(self.rows), "--only", "--comments", self.comments)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("3 of 3 review rows written", p.stdout)
        saved = json.loads((self.proj / ".kit" / "run" / "usage" / "r-gap.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["verdict"], saved["found_new"]), ("continue", "yes"))
        newer = dict(ROWS[2], recorded_at="2099-01-01T00:00:00Z")   # the hook records the session again
        merged = usage.merge_rows([saved], [newer])[0]
        self.assertEqual(merged["found_new"], "yes")
        # with no comments at all, show still prints the stored outcome
        p = self.run_usage("show", "--role", "review", "--comments", str(self.d / "empty"))
        self.assertIn("found_new=yes", p.stdout)


if __name__ == "__main__":
    unittest.main()
