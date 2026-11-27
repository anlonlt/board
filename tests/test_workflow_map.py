"""M9: The workflow on the map — what lives outside the repo, with checks (scripts/kit_map.py & scripts/step0_scan.py).

Tests all 5 pieces of M9:
1. Step 0 writes the workflow's first version from what it finds (CLIs, runners, git remotes, deploy configs,
   services named in config/code, data sources, browser profiles) and builds one board question for the owner.
2. Each external thing is on the map with links to the code that uses it and a check (reachable, logged in,
   still exists; for browser-only things, a probe with its last confirmed date).
3. Direct references live there too (URLs, project/org/repo/app/artifact ids, account/profile, where a secret
   is kept — NEVER the secret value).
4. The workflow memory file keeps only what cannot be checked (the cycle of a change, limits, owner's habits),
   short with generated headings, pointing at the map for the rest, preserving owner edits on rerun.
5. A part that finds a new service or data source adds it with its check (M3's rule); missing checks and raw
   secrets are refused.
Bars: measured read-only on ~/french and ~/harmonize when present.
"""
import datetime
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
import step0_scan  # noqa: E402


def rd(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _write(base, rel, text):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _git_init_with_bare_remote(proj_dir, bare_dir):
    subprocess.run(["git", "init", "--bare", bare_dir], check=True, capture_output=True)
    subprocess.run(["git", "init", proj_dir], check=True, capture_output=True)
    subprocess.run(["git", "-C", proj_dir, "config", "user.email", "owner@example.com"], check=True)
    subprocess.run(["git", "-C", proj_dir, "config", "user.name", "Owner"], check=True)
    subprocess.run(["git", "-C", proj_dir, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", proj_dir, "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", proj_dir, "remote", "add", "origin", bare_dir], check=True)
    subprocess.run(["git", "-C", proj_dir, "push", "-u", "origin", "HEAD"], check=True, capture_output=True)


PLANTED_SECRET_KEY = "sk_live_NEVERLEAK9876543210"
PLANTED_DB_PASS = "TOPSECRETDBPASS42"

SAMPLE_NEXT_PROJECT = {
    "package.json": json.dumps({
        "name": "demo-app",
        "dependencies": {"next": "16.0.0", "@neondatabase/serverless": "1.1.0", "drizzle-orm": "0.45.0"},
    }),
    "vercel.json": json.dumps({
        "framework": "nextjs",
        "crons": [{"path": "/api/cron/purge", "schedule": "0 6 1 * *"}],
    }),
    ".vercel/project.json": json.dumps({"projectId": "prj_demo123", "orgId": "team_abc99"}),
    "drizzle.config.ts": 'export default { dialect: "postgresql", dbCredentials: { url: process.env.DATABASE_URL ?? "" } };\n',
    "db/schema.ts": 'import { pgTable, text } from "drizzle-orm/pg-core";\nexport const items = pgTable("items", { id: text("id") });\n',
    "lib/bd.ts": 'import { neon } from "@neondatabase/serverless";\nexport const db = () => neon(process.env.DATABASE_URL!);\n',
    "app/api/cron/purge/route.ts": 'export async function GET() { const s = process.env.CRON_SECRET; return Response.json({ ok: !!s }); }\n',
    ".github/workflows/ci.yml": "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n",
    ".env.example": "DATABASE_URL=\nAUTH_SECRET=\nCRON_SECRET=\n",
    ".env.local": "DATABASE_URL=postgresql://app:%s@db.example.com/demo\nSTRIPE_SECRET_KEY=%s\n" % (PLANTED_DB_PASS, PLANTED_SECRET_KEY),
}


class WorkflowMapAndStep0(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m9-wf-")
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "demo-app")
        self.bare = os.path.join(self.tmp, "remote.git")
        os.makedirs(self.home, exist_ok=True)
        for rel, txt in SAMPLE_NEXT_PROJECT.items():
            _write(self.proj, rel, txt)
        # Seed browser profiles (metadata only)
        _write(self.home, ".config/google-chrome/Local State", json.dumps({
            "profile": {"info_cache": {"Default": {"name": "Work", "user_name": "owner@example.com"}}}
        }))
        _write(self.home, ".config/google-chrome/Default/Preferences", "{}")
        _write(self.home, ".mozilla/firefox/profiles.ini", "[Profile0]\nName=dev-ff\nPath=abc.dev-ff\n")
        _git_init_with_bare_remote(self.proj, self.bare)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_1_step0_writes_workflow_map_memory_and_one_question(self):
        res = step0_scan.write_workflow_files(self.proj, home_dir=self.home, run_checks=True)
        self.assertTrue(os.path.isfile(res["workflow_md_path"]))
        self.assertTrue(os.path.isfile(os.path.join(res["map_out"], "areas", "workflow.md")))
        self.assertTrue(os.path.isfile(os.path.join(res["map_out"], "map.json")))

        # Piece 1: one board question for the owner to confirm or correct
        q = res["confirm_question"]
        self.assertIn("Confirm workflow", q["title"])
        self.assertGreaterEqual(len(q["options"]), 2)
        self.assertEqual(q["default_option"], q["options"][0])
        self.assertIn("Vercel", q["body"])
        self.assertIn("Neon Postgres", q["body"])

        # Step 0 profile JSON also exposes workflow + report section
        prof = step0_scan.build_step0_profile(self.proj, home_dir=self.home)
        self.assertIn("workflow", prof)
        self.assertIn("confirm_question", prof["workflow"])
        rep = step0_scan.render_markdown_report(prof)
        self.assertIn("External Workflow & Services", rep)

    def test_2_things_links_to_code_and_live_checks_including_probes(self):
        d, out = kit_map.build(self.proj, run_gens=False, home_dir=self.home)
        by_id = {t["id"]: t for t in d["things"]}
        for expected in ("remote:origin", "cli:git", "service:vercel", "runner:scheduled",
                         "service:github-actions", "datasource:neon-postgres",
                         "browser:chrome-default", "browser:firefox-dev-ff", "secrets:env"):
            self.assertIn(expected, by_id, "missing external thing: %s" % expected)

        # Links to the code that uses each external thing
        links = {(l["from"], l["kind"], l["to"]) for l in d["links"]}
        self.assertIn(("file:vercel.json", "deploys-to", "service:vercel"), links)
        self.assertIn(("file:app/api/cron/purge/route.ts", "scheduled-by", "service:vercel"), links)
        self.assertIn(("file:drizzle.config.ts", "configures", "datasource:neon-postgres"), links)
        self.assertIn(("file:lib/bd.ts", "uses-datasource", "datasource:neon-postgres"), links)
        self.assertIn(("file:.github/workflows/ci.yml", "runs-on", "service:github-actions"), links)
        self.assertIn(("repo:demo-app", "pushes-to", "remote:origin"), links)

        # Live external checks pass against the real local bare remote
        rep = kit_map.check_external(self.proj, out=out, home_dir=self.home)
        res_by_id = {r["id"]: r for r in rep["results"]}
        self.assertEqual(res_by_id["remote:origin"]["status"], "pass")
        self.assertEqual(res_by_id["datasource:neon-postgres"]["status"], "pass")
        self.assertEqual(res_by_id["browser:chrome-default"]["status"], "probe_unconfirmed")
        self.assertEqual(rep["failed"], 0, rep["errors"])

        # Browser probe confirmation records date and turns probe_unconfirmed -> probe_confirmed
        today = datetime.date.today().isoformat()
        kit_map.confirm_probe(self.proj, "browser:chrome-default", confirmed_date=today)
        rep2 = kit_map.check_external(self.proj, out=out, home_dir=self.home)
        res2 = {r["id"]: r for r in rep2["results"]}
        self.assertEqual(res2["browser:chrome-default"]["status"], "probe_confirmed")

        # Stale probe (> 14 days old) goes back to probe_unconfirmed
        old_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        kit_map.confirm_probe(self.proj, "browser:chrome-default", confirmed_date=old_date)
        rep3 = kit_map.check_external(self.proj, out=out, home_dir=self.home)
        res3 = {r["id"]: r for r in rep3["results"]}
        self.assertEqual(res3["browser:chrome-default"]["status"], "probe_unconfirmed")

        # Removing the bare remote makes remote:origin check FAIL honestly
        shutil.rmtree(self.bare)
        rep4 = kit_map.check_external(self.proj, out=out, home_dir=self.home)
        res4 = {r["id"]: r for r in rep4["results"]}
        self.assertEqual(res4["remote:origin"]["status"], "fail")

    def test_3_direct_references_and_never_the_secret(self):
        res = step0_scan.write_workflow_files(self.proj, home_dir=self.home, run_checks=True)
        d = json.loads(rd(os.path.join(res["map_out"], "map.json")))
        by_id = {t["id"]: t for t in d["things"]}

        # Direct references present
        self.assertEqual(by_id["service:vercel"]["refs"].get("project_id"), "prj_demo123")
        self.assertEqual(by_id["service:vercel"]["refs"].get("org_id"), "team_abc99")
        self.assertEqual(by_id["service:vercel"]["refs"].get("url"), "https://vercel.com")
        self.assertEqual(by_id["browser:chrome-default"]["refs"].get("account"), "owner@example.com")
        self.assertTrue(by_id["datasource:neon-postgres"]["refs"].get("secret_locations"))

        # Planted secret values NEVER appear anywhere in map, card, workflow.md, or board question
        blob = "\n".join([
            rd(os.path.join(res["map_out"], "map.json")),
            rd(os.path.join(res["map_out"], "areas", "workflow.md")),
            rd(res["workflow_md_path"]),
            json.dumps(res["confirm_question"]),
        ])
        self.assertNotIn(PLANTED_SECRET_KEY, blob)
        self.assertNotIn(PLANTED_DB_PASS, blob)
        # Secret locations ARE recorded
        self.assertIn("DATABASE_URL", blob)
        self.assertIn("STRIPE_SECRET_KEY", blob)

    def test_4_workflow_md_keeps_only_uncheckable_and_preserves_owner_edits(self):
        path, text = kit_map.write_workflow_md(self.proj, home_dir=self.home)
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 40, "workflow.md should stay short")
        for heading in ("## Runners", "## The cycle of a change", "## Limits", "## The owner's habits"):
            self.assertIn(heading, text)
        self.assertIn(".board/map/areas/workflow.md", text)
        # Checkable direct refs live on the map, NOT duplicated into workflow.md
        self.assertNotIn("prj_demo123", text)
        self.assertNotIn("https://console.neon.tech", text)

        # Owner edits under uncheckable headings survive regeneration
        edited = text.replace(
            "- Branch -> tests -> commit -> push -> review -> merge to trunk (edit to match this project's cycle).",
            "- Trunk push allowed after pnpm verifier passes.",
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(edited)
        _, text2 = kit_map.write_workflow_md(self.proj, home_dir=self.home)
        self.assertIn("Trunk push allowed after pnpm verifier passes.", text2)

    def test_5_part_adds_new_service_at_done_with_check_and_refuses_without(self):
        d, out = kit_map.build(self.proj, run_gens=False, home_dir=self.home)
        rc, msg = kit_map.check(self.proj, out=out, run_gens=False, home_dir=self.home)
        self.assertEqual(rc, 0, msg)

        # Refused without a check
        with self.assertRaises(ValueError):
            kit_map.add_external_thing(self.proj, {"id": "service:posthog", "kind": "external-service", "name": "PostHog"})

        # Refused with a literal secret
        with self.assertRaises(ValueError):
            kit_map.add_external_thing(self.proj, {
                "id": "service:stripe-bad", "kind": "external-service", "name": "Stripe",
                "api_key": "sk_live_FAKE123456789012345",
                "check": {"probe": "Open Stripe", "confirmed": None},
            })

        # Accepted with a valid check and link to code
        kit_map.add_external_thing(
            self.proj,
            {
                "id": "service:posthog", "kind": "external-service", "name": "PostHog Analytics",
                "refs": {"url": "https://app.posthog.com", "project_id": "ph_42",
                         "secret_locations": [{"name": "POSTHOG_KEY", "where": "Vercel env"}]},
                "check": {"exists": ["package.json"]},
            },
            links=[{"from": "file:package.json", "to": "service:posthog", "kind": "uses-service",
                    "check": {"file": "package.json", "contains": "demo-app"}}],
        )
        d2, out2 = kit_map.build(self.proj, out=out, run_gens=False, home_dir=self.home)
        self.assertIn("service:posthog", {t["id"] for t in d2["things"]})
        rc2, msg2 = kit_map.check(self.proj, out=out2, run_gens=False, home_dir=self.home)
        self.assertEqual(rc2, 0, msg2)

        # Hand-editing .board/map-added.json to omit a check is caught by kit_map.check()
        added_path = os.path.join(self.proj, kit_map.ADDED_REL)
        with open(added_path, "w", encoding="utf-8") as fh:
            json.dump({"things": [{"id": "service:rogue", "kind": "external-service"}], "links": []}, fh)
        rc3, msg3 = kit_map.check(self.proj, out=out2, run_gens=False, home_dir=self.home)
        self.assertEqual(rc3, 1)
        self.assertIn("no check", msg3)


FRENCH = os.path.expanduser("~/french")
HARMONIZE = os.path.expanduser("~/harmonize")


class RealRepoBars(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(os.path.join(FRENCH, "vercel.json")), "~/french not present")
    def test_bar_french_read_only(self):
        before = subprocess.run(["git", "-C", FRENCH, "status", "--porcelain"], capture_output=True, text=True).stdout
        rep = kit_map.check_external(FRENCH, network=False)
        ids = {t["id"] for t in rep["things"]}
        for exp in ("remote:origin", "service:vercel", "runner:scheduled", "service:github-actions",
                    "datasource:neon-postgres", "secrets:env"):
            self.assertIn(exp, ids, ids)
        links = {(l["from"], l["kind"], l["to"]) for l in rep["links"]}
        self.assertIn(("file:drizzle.config.ts", "configures", "datasource:neon-postgres"), links)
        self.assertIn(("file:lib/bd.ts", "uses-datasource", "datasource:neon-postgres"), links)
        self.assertIn(("file:vercel.json", "deploys-to", "service:vercel"), links)
        after = subprocess.run(["git", "-C", FRENCH, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(before, after, "must never write into ~/french")

    @unittest.skipUnless(os.path.isfile(os.path.join(HARMONIZE, "scripts", "hfdata.py")), "~/harmonize not present")
    def test_bar_harmonize_read_only(self):
        before = subprocess.run(["git", "-C", HARMONIZE, "status", "--porcelain"], capture_output=True, text=True).stdout
        rep = kit_map.check_external(HARMONIZE, network=False)
        ids = {t["id"] for t in rep["things"]}
        for exp in ("remote:origin", "datasource:huggingface/altoanon/harmonize-data",
                    "service:modal/harmonize-k2", "service:spotify", "service:board-server",
                    "service:claude-artifact", "service:mcp/board-kit"):
            self.assertIn(exp, ids, ids)
        links = {(l["from"], l["kind"], l["to"]) for l in rep["links"]}
        self.assertIn(("file:scripts/hfdata.py", "uses-datasource", "datasource:huggingface/altoanon/harmonize-data"), links)
        self.assertIn(("file:pipeline/modal/k2_modal.py", "deploys-to", "service:modal/harmonize-k2"), links)
        self.assertIn(("file:pipeline/billboard_spotify.py", "uses-service", "service:spotify"), links)
        after = subprocess.run(["git", "-C", HARMONIZE, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(before, after, "must never write into ~/harmonize")


if __name__ == "__main__":
    unittest.main()
