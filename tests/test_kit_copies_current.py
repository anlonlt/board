"""The kit's own install (.kit/) runs the code of kit/: after a change to kit/, `python3 -I install.py . --update`
refreshes .kit/ in the same commit. CL10 check (orchestrator, 2026-11-23): .kit/shim.js, .kit/board.html and
.kit/strings.py kept the old bar width, baseline chevron, orange clock and old strings after kit/ was fixed."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPIES = {  # .kit/<file>: its source in kit/, copied byte for byte by install.py
    "shim.js": "kit/board/shim.js",
    "strings.py": "kit/board/strings.py",
    "board_server.py": "kit/board/board_server.py",
    "board_cli.py": "kit/board/board_cli.py",
    "kit_artifact_sync.py": "kit/claude/kit_artifact_sync.py",
}
HINT = " is older than kit/: run `python3 -I install.py . --update` and commit .kit/ with the change"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, ".kit", "board.html")), "the kit is not installed on itself here")
class TestKitCopiesCurrent(unittest.TestCase):
    def test_copied_files_match(self):
        for dst, src in COPIES.items():
            if os.path.isfile(os.path.join(ROOT, ".kit", dst)):
                self.assertTrue(read(".kit/" + dst) == read(src), ".kit/" + dst + HINT)

    def test_page_has_every_template_line(self):
        # board.html is the template with {{...}} filled in (and, when a palette or translation is set, recoloured or
        # translated): every template line without a placeholder must be in it, unless .kit/strings.json translates it
        if os.path.isfile(os.path.join(ROOT, ".kit", "strings.json")):
            self.skipTest("translated page")
        page = set(read(".kit/board.html").splitlines())
        missing = [l for l in read("kit/board/board.template.html").splitlines() if "{{" not in l and l not in page]
        self.assertEqual(missing[:3], [], ".kit/board.html" + HINT + " (%d template lines missing)" % len(missing))


if __name__ == "__main__":
    unittest.main()
