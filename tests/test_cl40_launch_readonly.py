"""CL40: launch a step of an attached board from a read-only clone (no push access to the board).

O57 (2026-11-24): an orchestrator on Harmonize clones public anlonlt/board read-only; `kit.py launch <post>` refused
because the prompt commit could not be pushed ("not in this session's authorized repository set"). Now a push refused
for access is no failure: the create_session prompt carries the prompt's full text (the session commits it to
docs/prompts/<post>.md on its branch first), source_url stays the board, source_revision is its trunk, and the clone
keeps no commit origin lacks. A push that fails for another reason (network) still fails. The push path is unchanged.

Run via: python3 -m unittest tests.test_cl40_launch_readonly -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_handoff import kit  # noqa: E402

PROMPT = "Harmonize, part CL40x: a step of the board.\n\n## The task\nDo the thing, then say Done.\n"

# the clone's kit.py: answers what a web launch answers (the prompt path, its branch as source_revision)
FAKE_KIT = (
    "import json, subprocess\n"
    "b = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True).stdout.strip()\n"
    "t = 'Your prompt is docs/prompts/cl40-x.md: read it and do it. Never attach a repo: the real-project run on "
    "anlonlt/harmonize is the orchestrator\\'s (done says pending until then).'\n"
    "print(json.dumps({'ok': True, 'post': 'cl40-x', 'part': 'CL40x', 'branch': 'cl40x', 'web': True,\n"
    "    'session_id': 'pending: create_session (board-cl40x)', 'warning': 'branch %s is not pushed' % b,\n"
    "    'create_session': {'title': 'CL40x', 'source_url': 'https://github.com/anlonlt/board', 'source_revision': b,\n"
    "                       'outcome_branch': 'cl40x', 'prompt': t}}))\n"
)

DENIED = "#!/bin/sh\necho \"remote: repository anlonlt/board is not in this session's authorized repository set\" >&2\nexit 1\n"
NETWORK = "#!/bin/sh\necho 'fatal: unable to access: Connection reset by peer' >&2\nexit 1\n"


class ReadOnlyLaunch(unittest.TestCase):
    def git(self, d, *a):
        return subprocess.run(["git", "-C", str(d)] + list(a), capture_output=True, text=True, check=True).stdout

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self.tmp.name)
        self.git(t, "init", "-q", "--bare", "-b", "main", "remote.git")
        clone = self.clone = t / "board"
        subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            self.git(clone, "config", k, v)
        (clone / ".kit").mkdir()
        (clone / ".kit" / "kit.py").write_text(FAKE_KIT)
        (clone / ".gitignore").write_text(".kit/run/\n")
        self.git(clone, "add", ".")
        self.git(clone, "commit", "-qm", "base")
        self.git(clone, "remote", "add", "origin", str(t / "remote.git"))
        self.git(clone, "push", "-q", "origin", "main")
        self.base = self.git(clone, "rev-parse", "HEAD").strip()
        host = self.host = t / "harmonize"
        (host / "docs" / "prompts").mkdir(parents=True)
        (host / "docs" / "prompts" / "cl40-x.md").write_text(PROMPT)
        self.b = {"id": "board", "github": "anlonlt/board", "clone": str(clone)}

    def tearDown(self):
        self.tmp.cleanup()

    def refuse(self, hook):
        h = self.t / "remote.git" / "hooks" / "pre-receive"
        h.write_text(hook)
        os.chmod(h, 0o755)

    def launch(self):
        with mock.patch.object(kit, "ROOT", str(self.host)), mock.patch.object(kit, "_github_url", return_value=""), \
                mock.patch.object(kit, "local_board_url", return_value="http://127.0.0.1:9"), \
                mock.patch.object(kit, "_art_dir", return_value=str(self.t / "art")):
            return kit._launch_attached("cl40-x", self.b, {"part": "CL40x"}, web=True)

    def test_push_refused_for_access_carries_the_prompt_in_create_session(self):
        self.refuse(DENIED)
        r = self.launch()
        self.assertTrue(r.get("ok"), r)
        cs = r["create_session"]
        self.assertIn("first commit it to docs/prompts/cl40-x.md on your branch", cs["prompt"])
        self.assertIn(PROMPT.strip(), cs["prompt"])                       # the full text, not a path
        self.assertIn("Never attach a repo", cs["prompt"])                 # the rest of the start message stays
        self.assertEqual(cs["source_revision"], "main")                    # the trunk the session can clone
        self.assertEqual(cs["source_url"], "https://github.com/anlonlt/board")
        self.assertEqual(cs["outcome_branch"], "cl40x")
        self.assertNotIn("warning", r)
        self.assertIn("authorized repository set", r["prompt_inline"]["reason"])
        self.assertIn("could not be pushed", kit.launch_text(r))
        # the clone holds no commit origin lacks, and is back on its branch
        self.assertEqual(self.git(self.clone, "branch", "--show-current").strip(), "main")
        self.assertEqual(self.git(self.clone, "rev-parse", "part-cl40-x").strip(), self.base)
        self.assertNotIn("part-cl40-x", self.git(self.t / "remote.git", "branch"))

    def test_push_refused_for_another_reason_still_fails(self):
        self.refuse(NETWORK)
        r = self.launch()
        self.assertFalse(r.get("ok"))
        self.assertIn("could not push", r["error"])

    def test_push_that_works_keeps_the_path(self):
        r = self.launch()
        self.assertTrue(r.get("ok"), r)
        self.assertIn("part-cl40-x", self.git(self.t / "remote.git", "branch"))
        self.assertTrue(r["create_session"]["prompt"].startswith("Your prompt is docs/prompts/cl40-x.md"))
        self.assertNotIn("prompt_inline", r)


if __name__ == "__main__":
    unittest.main()
