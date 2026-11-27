"""CL31: `kit.py artifact push` never overwrites a newer live card (posts/_dashboard, posts/_trains).

CL30 found the hole: a card changed here (kit_cards rebuilt it) and never sent waits for its version (need_pin); `artifact
pin` took the version a fresh `get` named and the next push wrote this copy over the live card, losing what the live card
had gained since this copy's base (a row's store list, a hand-written note). Now a pin takes the live copy the `get`
saved with it and merges this copy's changes into it (rows by their key, each field three-way); a pin without the live
copy is refused. `artifact load` merges the cards the same way.

Run via: python3 -m unittest tests.test_cl31_card_push -v
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import BOOT, ROOT, TOKEN, api_req, kit  # noqa: E402

TRAINS = {"title": "Trains", "column": "info", "updated_at": "2026-11-23T20:00:00Z", "rows": [
    {"part": "CL30", "parent": "P-cli", "also": [], "children": [], "post": "cl30-x", "prompt": "", "session": "s30",
     "state": "running", "store": []},
    {"part": "CL31", "parent": "P-cli", "also": [], "children": [], "post": "cl31-x", "prompt": "", "session": "",
     "state": "idle", "store": []}]}
DASH = {"title": "Where we are", "column": "info", "updated_at": "2026-11-23T20:00:00Z", "rows": [
    {"key": "A", "name": "Cli", "note": "Next: CL31", "post": "cl31-x", "state": "running", "steps": ["one", "two"]}]}


class CardPush(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        (self.tmp / "proj").mkdir()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT, str(ROOT / "kit" / "board"), str(self.tmp / "proj")],
                                     stdout=subprocess.PIPE, text=True)
        stack.callback(lambda: (self.proc.terminate(), self.proc.wait(timeout=60), self.proc.stdout.close()))
        self.url = "http://127.0.0.1:" + self.proc.stdout.readline().strip()
        self.art = self.tmp / "art"
        for k, v in (("KIT_ARTIFACT_DIR", str(self.art)), ("KIT_ARTIFACT_PREFIX", "")):
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)

    def remote(self, coll, did, data, where="in"):
        d = self.art / where / coll
        d.mkdir(parents=True, exist_ok=True)
        (d / (did + ".json")).write_text(json.dumps(data))

    def local(self, path, data=None):
        if data is None:
            return api_req(self.url, "doc/" + path, token=TOKEN)["data"]
        return api_req(self.url, "doc/" + path, {"data": data}, TOKEN, "PUT")

    def push(self):
        return kit.artifact_push(url=self.url, token=TOKEN)

    def sent(self, r):
        return {e["doc_id"]: json.load(open(e["file_path"])) for p in r["batches"] for e in json.load(open(p))
                if e["collection"].endswith("posts")}

    def pin(self, *pairs):
        return kit.artifact_pin(list(pairs), url=self.url, token=TOKEN)

    def loaded_then_changed_both_sides(self):
        """The copy is loaded (base = the cards), kit_cards rebuilds them here, the live cards gain hand-written things."""
        self.remote("posts", "_trains", TRAINS)
        self.remote("posts", "_dashboard", DASH)
        kit.artifact_load(url=self.url, token=TOKEN)
        here_t = copy.deepcopy(TRAINS)
        here_t["rows"][1]["state"] = "running"                      # rebuilt here: CL31 launched
        here_t["rows"][1]["session"] = "s31"
        here_t["rows"].append({"part": "CL32", "parent": "P-cli", "also": [], "children": [], "post": "cl32-x",
                               "prompt": "", "session": "", "state": "idle", "store": []})
        here_t["updated_at"] = "2026-11-23T21:00:00Z"
        self.local("posts/_trains", here_t)
        here_d = copy.deepcopy(DASH)
        here_d["rows"][0]["steps"] = ["one", "two", "three"]
        here_d["updated_at"] = "2026-11-23T21:00:00Z"
        self.local("posts/_dashboard", here_d)
        live_t = copy.deepcopy(TRAINS)
        live_t["rows"][0]["store"] = ["docs/CL30.md", "tests/test_cl30_files.py"]   # a store list added live
        live_t["rows"][0]["prompt"] = "docs/prompts/cl30.md"
        live_t["updated_at"] = "2026-11-23T20:30:00Z"
        live_d = copy.deepcopy(DASH)
        live_d["rows"][0]["note"] = "Next: CL31. Hand note: wait for the owner's merge."  # a hand-written note
        live_d["owner_note"] = "keep this"
        live_d["updated_at"] = "2026-11-23T20:30:00Z"
        return live_t, live_d

    def test_the_live_line_rides_along_with_a_push(self):
        """CL41: a web part's hook writes activity/<post> to this copy only; the push carries it to the Artifact (the page reads
        it there): a create the first time, pinned to the version this copy sent after that, nothing when it did not change."""
        kit.artifact_load(url=self.url, token=TOKEN)
        act = {"at": "2026-11-26T01:00:00Z", "actions": 3, "what": "Running the tests", "state": "working", "pieces": [2]}
        self.local("activity/cl41-x", act)
        acts = lambda r: [e for p in r["batches"] for e in json.load(open(p)) if e["collection"].endswith("activity")]  # noqa: E731
        first = acts(self.push())
        self.assertEqual([(e["doc_id"], e.get("if_version")) for e in first], [("cl41-x", None)])
        self.assertEqual(json.load(open(first[0]["file_path"]))["what"], "Running the tests")
        for p in self.push()["batches"]:
            kit.artifact_sent(p)
        self.assertEqual(acts(self.push()), [])                                          # unchanged: not sent again
        self.local("activity/cl41-x", dict(act, actions=9, state="stopped"))
        again = acts(self.push())
        self.assertEqual([(e["doc_id"], e.get("if_version")) for e in again], [("cl41-x", 1)])
        self.assertEqual(kit.artifact_unsent(url=self.url, token=TOKEN)["changed"], 0)   # never counted as unsent

    def test_pin_from_a_fresh_get_keeps_the_live_cards(self):
        live_t, live_d = self.loaded_then_changed_both_sides()
        r = self.push()
        self.assertEqual(sorted(r["need_pin"]), ["posts/_dashboard", "posts/_trains"])
        # the session does the gets the push text names: each saves the live card in the pin directory
        text = kit.artifact_push_text(r)
        self.assertIn("kit.py artifact sync", text)         # CL72: sync makes these reads and pins itself
        self.remote("posts", "_trains", live_t, where="pin/v7")
        self.remote("posts", "_dashboard", live_d, where="pin/v4")
        self.assertEqual(self.pin("posts/_trains=7", "posts/_dashboard=4"), 2)
        got = self.sent(self.push())
        t = {x["part"]: x for x in got["_trains"]["rows"]}
        self.assertEqual(t["CL30"]["store"], ["docs/CL30.md", "tests/test_cl30_files.py"])   # live kept
        self.assertEqual(t["CL30"]["prompt"], "docs/prompts/cl30.md")
        self.assertEqual((t["CL31"]["state"], t["CL31"]["session"]), ("running", "s31"))      # here kept
        self.assertIn("CL32", t)
        self.assertEqual(got["_trains"]["updated_at"], "2026-11-23T21:00:00Z")
        d = got["_dashboard"]
        self.assertEqual(d["rows"][0]["note"], "Next: CL31. Hand note: wait for the owner's merge.")
        self.assertEqual(d["owner_note"], "keep this")
        self.assertEqual(d["rows"][0]["steps"], ["one", "two", "three"])

    def test_pin_without_the_live_copy_is_refused(self):
        self.loaded_then_changed_both_sides()
        report = []
        self.assertEqual(kit.artifact_pin(["posts/_trains=7"], url=self.url, token=TOKEN, report=report), 0)
        self.assertTrue(report and "_trains" in report[0], report)
        r = self.push()
        self.assertIn("posts/_trains", r["need_pin"])                     # still waits: nothing written over the live card
        self.assertNotIn("_trains", self.sent(r))

    def test_load_merges_the_cards_by_row(self):
        live_t, live_d = self.loaded_then_changed_both_sides()
        self.remote("posts", "_trains", live_t)
        self.remote("posts", "_dashboard", live_d)
        kit.artifact_load(url=self.url, token=TOKEN)
        t = {x["part"]: x for x in self.local("posts/_trains")["rows"]}
        self.assertEqual(t["CL30"]["store"], ["docs/CL30.md", "tests/test_cl30_files.py"])
        self.assertEqual(t["CL31"]["state"], "running")
        self.assertEqual(self.local("posts/_dashboard")["rows"][0]["note"],
                         "Next: CL31. Hand note: wait for the owner's merge.")

    def test_a_live_card_equal_to_the_base_is_pinned_as_before(self):
        self.loaded_then_changed_both_sides()
        self.remote("posts", "_trains", TRAINS, where="pin/v7")
        self.assertEqual(self.pin("posts/_trains=7"), 1)
        got = self.sent(self.push())
        self.assertEqual(got["_trains"]["rows"][1]["session"], "s31")

    def test_an_older_seeded_card_is_dropped_not_sent(self):
        """O53, Harmonize 22:00: the local board seeded its cards at start, older than the live ones."""
        self.remote("posts", "_trains", TRAINS)
        kit.artifact_load(url=self.url, token=TOKEN)
        seed = {"title": "Trains (seed)", "column": "notes", "updated_at": "2026-11-20T09:00:00Z", "rows": []}
        self.local("posts/_trains", seed)
        self.assertEqual(kit.artifact_unsent(url=self.url, token=TOKEN)["changed"], 0)   # the Stop hook does not block
        r = self.push()
        self.assertEqual(r["dropped"], ["posts/_trains"])
        self.assertNotIn("posts/_trains", r["need_pin"])
        self.assertNotIn("_trains", self.sent(r))
        self.assertEqual(self.local("posts/_trains"), TRAINS)                              # the Artifact's copy back
        # and a pin from a get of a newer live card takes the live card whole
        self.local("posts/_trains", seed)
        live = dict(TRAINS, updated_at="2026-11-23T22:08:00Z", title="Trains")
        self.remote("posts", "_trains", live, where="pin/v57")
        self.pin("posts/_trains=57")
        self.assertEqual(self.local("posts/_trains"), live)

    def test_pin_reads_only_this_projects_pin_dir_fresh_and_at_its_version(self):
        """O53: the saved copy is read only from this project's pin/v<version>/ (no shared /tmp fallback); a copy
        saved for another version, a stale one, or one whose own version differs is refused."""
        live_t, _ = self.loaded_then_changed_both_sides()
        rep = []
        self.assertEqual(kit.artifact_pin(["posts/_trains=7"], url=self.url, token=TOKEN, report=rep), 0)
        self.assertIn(str(self.art / "pin" / "v7"), rep[0])
        # a fresh get saved for version 6 (or a bare pin dir): the pin at 7 has no copy of its own version
        self.remote("posts", "_trains", live_t, where="pin/v6")
        self.remote("posts", "_trains", live_t, where="pin")
        rep = []
        self.assertEqual(kit.artifact_pin(["posts/_trains=7"], url=self.url, token=TOKEN, report=rep), 0)
        self.assertIn("version 7", rep[0])
        self.assertIn("posts/_trains", self.push()["need_pin"])
        # a saved copy older than 30 min
        self.remote("posts", "_trains", live_t, where="pin/v7")
        f = self.art / "pin" / "v7" / "posts" / "_trains.json"
        old = f.stat().st_mtime - 31 * 60
        os.utime(f, (old, old))
        rep = []
        self.assertEqual(kit.artifact_pin(["posts/_trains=7"], url=self.url, token=TOKEN, report=rep), 0)
        self.assertIn("older than 30 min", rep[0])
        # a saved copy that names its own version: it must be the pin's
        self.remote("posts", "_trains", {"id": "_trains", "version": 8, "data": live_t}, where="pin/v7")
        rep = []
        self.assertEqual(kit.artifact_pin(["posts/_trains=7"], url=self.url, token=TOKEN, report=rep), 0)
        self.assertIn("version 8", rep[0])
        self.remote("posts", "_trains", live_t, where="pin/v8")
        self.assertEqual(kit.artifact_pin(["posts/_trains=8"], url=self.url, token=TOKEN), 1)
        self.assertFalse((self.art / "pin" / "v8" / "posts" / "_trains.json").exists())     # used once

    def test_unkeyed_rows_changed_on_both_sides_are_reported_not_dropped(self):
        """O53: rows without a key cannot be merged by row: the pin refuses, load keeps this copy and says so."""
        base = {"title": "Where we are", "updated_at": "2026-11-23T20:00:00Z", "rows": [{"text": "a"}, {"text": "b"}]}
        self.remote("posts", "_dashboard", base)
        kit.artifact_load(url=self.url, token=TOKEN)
        here = dict(base, rows=[{"text": "a"}, {"text": "b"}, {"text": "c here"}], updated_at="2026-11-23T21:00:00Z")
        self.local("posts/_dashboard", here)
        live = dict(base, rows=[{"text": "a live"}, {"text": "b"}], updated_at="2026-11-23T20:30:00Z")
        self.remote("posts", "_dashboard", live, where="pin/v4")
        rep = []
        self.assertEqual(kit.artifact_pin(["posts/_dashboard=4"], url=self.url, token=TOKEN, report=rep), 0)
        self.assertIn("rows", rep[0])
        self.assertEqual(self.local("posts/_dashboard"), here)                     # nothing dropped here
        self.remote("posts", "_dashboard", live)
        r = kit.artifact_load(url=self.url, token=TOKEN)
        self.assertEqual(len(r["unmerged"]), 1)
        self.assertEqual(self.local("posts/_dashboard"), here)
        self.assertIn("posts/_dashboard", self.push()["need_pin"])                 # never sent over the live card


if __name__ == "__main__":
    unittest.main()
