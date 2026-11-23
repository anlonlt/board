"""CL12 (orchestrator's check, 2026-11-23): the kit's own install (.kit/) must run the kit as it is in kit/.

A fix merged into kit/board/ did not reach the owner's board because .kit/board.html, .kit/strings.py and
.kit/strings.en.json were older copies. This fails when any copy differs; the fix is `python3 -I install.py . --update`.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

COPIES = {  # .kit/<name> is a byte copy of kit/<path>
    "strings.py": "kit/board/strings.py",
    "strings.en.json": "kit/board/strings.en.json",
    "shim.js": "kit/board/shim.js",
    "board_cli.py": "kit/board/board_cli.py",
    "board_server.py": "kit/board/board_server.py",
}
FIX = "rerun `python3 -I install.py . --update` and commit .kit/"


class _Args:
    def __getattr__(self, k):  # install.load_config reads optional flags; none is given here
        return None


def read(rel, mode="r"):
    with open(os.path.join(ROOT, rel), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, ".kit", "config.json")), "the kit is not installed on its own repo")
class KitCopiesCurrent(unittest.TestCase):
    def test_byte_copies_match(self):
        for name, src in COPIES.items():
            if not os.path.isfile(os.path.join(ROOT, ".kit", name)):
                continue
            self.assertEqual(read(".kit/" + name, "rb"), read(src, "rb"), ".kit/%s differs from %s: %s" % (name, src, FIX))

    def test_board_page_is_the_rendered_template(self):
        import install
        c = install.load_config(ROOT, _Args())
        if (c.get("kit") or {}).get("palette") or os.path.exists(os.path.join(ROOT, ".kit", "strings.json")):
            self.skipTest("a palette or a translation changes the page; compare by install --update instead")
        page = install.render(read("kit/board/board.template.html"), c)
        self.assertTrue(page == read(".kit/board.html"), ".kit/board.html is not the current kit/board/board.template.html: " + FIX)


if __name__ == "__main__":
    unittest.main()
