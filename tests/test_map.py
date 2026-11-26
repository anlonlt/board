"""M1: `kit.py map`, a map of any project generated from its code (scripts/kit_map.py).

Fixtures are small Python and TypeScript projects that carry, in miniature, the three join bugs the 2026-11-25 audit
of the owner's French repo found and no hand-written doc showed: a field the UI sends that the Zod schema drops, a
field the server sends that no screen reads, a write route with no CSRF check. The map must flag each, fit its
overview in one screen, and refuse to pass `--check` once the code moved.
"""
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

TS_FILES = {
    "tsconfig.json": '{ "compilerOptions": { "paths": { "@/*": ["./*"] } } }',
    "db/schema.ts": '''import { pgTable, integer, text } from "drizzle-orm/pg-core";
export const equipe = pgTable("equipe", { id: integer("id"), nom: text("nom") });
export const partie = pgTable("partie", { id: integer("id"), etat: text("etat") });
''',
    "lib/auth.ts": '''/* Who is asking, and from where. */
export function utilisateurCourant() { return null; }
export function origineValide(req: Request) { return true; }
''',
    "lib/moteur/faussaire.ts": '''/* The engine: pure rules of the game. */
export const COUT = 3;
export function appliquer(etat: unknown) { return etat; }
''',
    "lib/moteur/index.ts": '''export * from "./faussaire";
''',
    "lib/jeu.ts": '''/* The game's persistence. */
import { partie } from "@/db/schema";
export async function lirePartie(bd: any, id: number) { return bd.select().from(partie); }
''',
    "app/api/jeu/action/route.ts": '''import { z } from "zod";
import { origineValide, utilisateurCourant } from "@/lib/auth";
import { lirePartie } from "@/lib/jeu";
const Corps = z.object({
  partieId: z.number(),
  action: z.discriminatedUnion("type", [
    z.object({ type: z.literal("corrompre"), domaine: z.string() }),
    z.object({ type: z.literal("voter"), cible: z.number() }),
  ]),
});
export async function POST(req: Request) {
  if (!origineValide(req)) return Response.json({}, { status: 403 });
  const moi = utilisateurCourant();
  const corps = Corps.safeParse(await req.json());
  await lirePartie(null, 1);
  return Response.json({ ok: true });
}
''',
    "app/api/jeu/[id]/etat/route.ts": '''import { utilisateurCourant } from "@/lib/auth";
export async function GET(req: Request) {
  utilisateurCourant();
  return Response.json({ joueurs: [], activiteParFenetre: {} });
}
''',
    "app/api/equipes/route.ts": '''import { z } from "zod";
import { equipe } from "@/db/schema";
import { utilisateurCourant } from "@/lib/auth";
const Corps = z.object({ groupeId: z.number(), equipes: z.array(z.string()) });
export async function POST(requete: Request) {
  const moi = utilisateurCourant();
  const c = Corps.safeParse(await requete.json());
  await bd.delete(equipe);
  return Response.json({ ok: true });
}
''',
    "app/api/vieux/route.ts": '''export async function GET() { return Response.json({}); }
''',
    "app/jeu/ronde.tsx": '''"use client";
/* The round screen: what a player sees and does during a round. */
import { COUT } from "@/lib/moteur";
type Vue = {
  joueurs: number[];
  activiteParFenetre: Record<string, number[]>;
};
export function Ronde({ partieId, lot }: { partieId: number; lot: number }) {
  async function agir(action: Record<string, unknown>) {
    await fetch("/api/jeu/action", {
      method: "POST",
      body: JSON.stringify({ partieId, action }),
    });
  }
  async function sonder(vue: Vue) {
    const r = await fetch(`/api/jeu/${partieId}/etat`);
    return vue.joueurs.length + COUT;
  }
  return <button onClick={() => agir({ type: "corrompre", domaine: "x", ...(lot > 1 ? { feuilles: lot } : {}) })} />;
}
''',
    "app/equipes/composer.tsx": '''export async function composer(groupeId: number) {
  await fetch("/api/equipes", { method: "POST", body: JSON.stringify({ groupeId, equipes: [] }) });
}
''',
    "tests/unit/faussaire.test.ts": '''import { appliquer } from "@/lib/moteur/faussaire";
test("x", () => appliquer({}));
''',
    "app/big.ts": "export const A = 1;\n" + "// filler\n" * 1501,
}

PY_FILES = {
    "pkg/__init__.py": '"""The package: a tiny shop."""\n',
    "pkg/store.py": '''"""Where the goods are kept."""
import json
from pkg import util


class Store:
    pass


def add(item):
    return util.clean(item)


def _private():
    pass
''',
    "pkg/util.py": '''"""Small helpers for the store."""


def clean(x):
    return x.strip()
''',
    "app.py": '''"""The web front of the shop."""
from flask import Flask
from pkg.store import add
app = Flask(__name__)


@app.route("/items", methods=["POST"])
def new_item():
    return add("x")


if __name__ == "__main__":
    app.run()
''',
    "tests/test_store.py": '''import unittest
from pkg.store import add


class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(" a "), "a")
''',
}


def rd(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def make(files, git=True):
    d = tempfile.mkdtemp(prefix="kitmap-")
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    if git:
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", d] + cmd, check=True, capture_output=True, env=env)
    return d


class TypeScriptJoins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make(TS_FILES)
        cls.out = os.path.join(cls.root, ".board", "map")
        cls.d, _ = kit_map.build(cls.root, run_gens=False)
        cls.kinds = [f["kind"] for f in cls.d["flags"]]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def flag(self, kind, word):
        return [f for f in self.d["flags"] if f["kind"] == kind and word in f["text"]]

    def test_a_field_sent_inside_a_union_member_but_dropped_by_the_schema_is_flagged(self):
        hit = self.flag("dropped-field", "action.feuilles")
        self.assertEqual(len(hit), 1, self.d["flags"])
        self.assertIn("app/jeu/ronde.tsx", hit[0]["text"])
        self.assertIn('type "corrompre"', hit[0]["text"])
        self.assertIn("silently drops", hit[0]["text"])
        self.assertFalse(self.flag("dropped-field", "domaine"))

    def test_a_write_route_with_no_csrf_check_is_flagged_and_one_with_it_is_not(self):
        self.assertTrue(self.flag("no-csrf", "POST /api/equipes"))
        self.assertFalse([f for f in self.d["flags"] if f["kind"].startswith("no-") and "/api/jeu/action" in f["text"]])

    def test_a_field_the_server_sends_that_no_screen_reads_is_flagged(self):
        self.assertTrue(self.flag("never-read", "activiteParFenetre"))
        self.assertFalse(self.flag("never-read", "joueurs"))

    def test_a_handler_nothing_calls_and_a_big_file_are_flagged(self):
        self.assertTrue(self.flag("uncalled", "GET /api/vieux"))
        self.assertFalse(self.flag("uncalled", "/api/jeu/action"))
        self.assertTrue(self.flag("big-file", "app/big.ts"))

    def test_the_joins_row_has_sent_accepted_handler_tables_tests(self):
        row = next(j for j in self.d["joins"] if j["route"] == "route:POST /api/jeu/action")
        self.assertIn("partieId", row["sent"])
        self.assertIn("action.feuilles", row["sent"])
        self.assertIn("action[type=corrompre].domaine", row["accepts"])
        self.assertEqual(row["handler"], "app/api/jeu/action/route.ts")
        self.assertIn("partie (via lib/jeu.ts)", row["tables"])
        self.assertTrue(any(c.startswith("app/jeu/ronde.tsx:") for c in row["callers"]))
        eq = next(j for j in self.d["joins"] if j["route"] == "route:POST /api/equipes")
        self.assertIn("equipe", eq["tables"])
        self.assertEqual(eq["guards"], {"csrf": False, "auth": True})
        etat = next(j for j in self.d["joins"] if j["path"] == "/api/jeu/[id]/etat")
        self.assertTrue(etat["callers"], "a template URL `/api/jeu/${id}/etat` reaches the [id] route")

    def test_the_overview_fits_one_screen_and_names_the_flags(self):
        ov = rd(os.path.join(self.out, "overview.md"))
        self.assertLessEqual(ov.count("\n"), kit_map.MAX_OVERVIEW)
        for word in ("action.feuilles", "POST /api/equipes writes with no CSRF", "activiteParFenetre"):
            self.assertIn(word, ov)
        self.assertIn("built from", ov)

    def test_a_feature_card_names_the_engine_the_screen_and_the_route(self):
        self.assertIn("jeu", [f["feature"] for f in self.d["features"]])
        card = rd(os.path.join(self.out, "areas", "feature-jeu.md"))
        self.assertIn("app/jeu/ronde.tsx", card)
        self.assertIn("POST /api/jeu/action", card)
        self.assertIn("lib/moteur/faussaire.ts", card, "the barrel @/lib/moteur resolves to the file that exports COUT")
        self.assertIn("Start here:", card)

    def test_map_json_is_things_links_flags_and_every_link_has_a_check(self):
        d = json.loads(rd(os.path.join(self.out, "map.json")))
        for key in ("things", "links", "flags", "commit", "fingerprint", "joins", "areas"):
            self.assertIn(key, d)
        self.assertTrue(d["links"])
        self.assertTrue(all(ln.get("check") for ln in d["links"]))
        kinds = {ln["kind"] for ln in d["links"]}
        self.assertTrue({"imports", "calls", "validates", "touches", "tests", "handled-by"} <= kinds, kinds)

    def test_check_passes_fresh_and_refuses_once_the_code_changed(self):
        rc, msg = kit_map.check(self.root)
        self.assertEqual(rc, 0, msg)
        p = os.path.join(self.root, "app", "equipes", "composer.tsx")
        old = rd(p)
        try:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write("\nexport const Y = 2;\n")
            rc, msg = kit_map.check(self.root)
            self.assertEqual(rc, 1)
            self.assertIn("STALE", msg)
            self.assertIn("app/equipes/composer.tsx", msg)
        finally:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(old)

    def test_check_reruns_each_link_and_fails_when_one_no_longer_holds(self):
        mp = os.path.join(self.out, "map.json")
        orig = rd(mp)
        try:
            d = json.loads(orig)
            ln = next(x for x in d["links"] if x["kind"] == "calls")
            ln["check"]["contains"] = "/api/never-written-anywhere"
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(d, fh)
            rc, msg = kit_map.check(self.root)
            self.assertEqual(rc, 1)
            self.assertIn("link checks fail", msg)
        finally:
            with open(mp, "w", encoding="utf-8") as fh:
                fh.write(orig)


class PythonAreas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make(PY_FILES, git=False)
        cls.out = os.path.join(cls.root, "mapout")
        cls.d, _ = kit_map.build(cls.root, cls.out, run_gens=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_python_reader_functions_classes_imports(self):
        f = kit_map.read_py("pkg/store.py", PY_FILES["pkg/store.py"])
        self.assertEqual(f["exports"], ["Store", "add()"])
        self.assertIn("pkg", [i["from"] for i in f["imports"]])
        self.assertEqual(f["doc"], "Where the goods are kept.")
        app = kit_map.read_py("app.py", PY_FILES["app.py"])
        self.assertEqual(app["routes"], [{"method": "POST", "path": "/items", "handler": "new_item"}])
        self.assertTrue(app["main"])

    def test_area_card_files_exports_imports_both_ways_and_tests(self):
        card = rd(os.path.join(self.out, "areas", "pkg.md"))
        self.assertIn("For: The package: a tiny shop.", card)
        self.assertIn("| pkg/store.py |", card)
        self.assertIn("Store, add()", card)
        self.assertIn("app.py ((root)) x1", card)        # what imports it
        self.assertIn("tests/test_store.py", card)        # its tests
        self.assertNotIn("_private", card)

    def test_without_git_the_map_still_builds_and_checks(self):
        self.assertIsNone(self.d["commit"])
        rc, msg = kit_map.check(self.root, self.out)
        self.assertEqual(rc, 0, msg)


class Generators(unittest.TestCase):
    def test_the_projects_own_generators_run_are_linked_and_rerun_by_check(self):
        root = make(PY_FILES, git=False)
        try:
            os.makedirs(os.path.join(root, ".kit"))
            gen = [{"name": "carte", "run": "%s -c \"print('CARTE: a jour')\"" % sys.executable, "area": "pkg"}]
            with open(os.path.join(root, ".kit", "config.json"), "w") as fh:
                json.dump({"map": gen}, fh)
            d, out = kit_map.build(root)
            self.assertEqual(d["generators"][0]["rc"], 0)
            self.assertIn("CARTE: a jour", rd(os.path.join(out, "gen", "carte.txt")))
            self.assertIn("[output](../gen/carte.txt)", rd(os.path.join(out, "areas", "pkg.md")))
            self.assertIn("Generators:", rd(os.path.join(out, "overview.md")))
            self.assertEqual(kit_map.check(root)[0], 0)
            gen[0]["run"] = "%s -c \"import sys; sys.exit(1)\"" % sys.executable
            with open(os.path.join(root, ".kit", "config.json"), "w") as fh:
                json.dump({"map": {"generators": gen}}, fh)
            d, out = kit_map.build(root)
            self.assertIn("generator-failed", [f["kind"] for f in d["flags"]])
            rc, msg = kit_map.check(root)
            self.assertEqual(rc, 1)
            self.assertIn("fails", msg)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class KitCommand(unittest.TestCase):
    def test_kit_py_map_runs_and_is_in_the_command_index(self):
        root = make(TS_FILES)
        try:
            out = os.path.join(root, "o")
            r = subprocess.run([sys.executable, "-I", KIT, "map", "--root", root, "--out", out], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("overview", r.stdout)
            for f in ("overview.md", "joins.md", "map.json"):
                self.assertTrue(os.path.isfile(os.path.join(out, f)), f)
            r = subprocess.run([sys.executable, "-I", KIT, "map", "--root", root, "--out", out, "--check"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            r = subprocess.run([sys.executable, "-I", KIT, "commands", "map"], capture_output=True, text=True)
            self.assertIn("kit.py map", r.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)


FRENCH = os.path.expanduser("~/french")


@unittest.skipUnless(os.path.isfile(os.path.join(FRENCH, "app", "jeu", "ronde.tsx")), "the owner's French repo is not on this computer")
class TheRealCase(unittest.TestCase):
    """~/french, read-only: the audit's three join bugs flagged, the overview in one screen, nothing written there."""

    def test_french(self):
        before = subprocess.run(["git", "-C", FRENCH, "status", "--porcelain"], capture_output=True, text=True).stdout
        out = tempfile.mkdtemp(prefix="kitmap-french-")
        try:
            d, _ = kit_map.build(FRENCH, out, run_gens=False)
            texts = [f["text"] for f in d["flags"]]
            hits = [any("action.feuilles" in t and "/api/jeu/action" in t for t in texts),
                    any("activiteParFenetre" in t and "/api/jeu/[id]/etat" in t for t in texts),
                    any("POST /api/enseignant/jeu/equipes writes with no CSRF" in t for t in texts)]
            self.assertEqual(sum(hits), 3, hits)
            ov = rd(os.path.join(out, "overview.md"))
            self.assertLessEqual(ov.count("\n"), kit_map.MAX_OVERVIEW)
            card = rd(os.path.join(out, "areas", "feature-jeu.md"))
            for name in ("lib/moteur/faussaire.ts", "app/jeu/ronde.tsx", "POST /api/jeu/action"):
                self.assertIn(name, card)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        after = subprocess.run(["git", "-C", FRENCH, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(before, after, "the map wrote in ~/french")


if __name__ == "__main__":
    unittest.main()
