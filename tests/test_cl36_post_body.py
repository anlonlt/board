#!/usr/bin/env python3
"""CL36: a part's result lives in its post body, not in a docs file it embeds section by section.

The owner, 2026-11-24: "Can you tell me why sessions are writing out documents and linking them inside posts section by
section instead of keeping everything in a post ?" CL34's post body was 8 headings, each only `docs/CL34.md § X`.
  1. `kit.py done` refuses a body that is mostly section embeds of a docs file the part wrote (docs/<PART>.md, or a
     docs/*.md added on the branch);
  2. a body with its text, a mermaid block and a picture passes; so does one that embeds a file the part did not write
     (the kit's guide, an older report);
  3. the guides teach the new rule and no copy of the old wording is left.

python3 -m unittest tests.test_cl36_post_body -v
"""
import glob
import os
import subprocess
import tempfile
import unittest

from tests.test_cl29_compact_checker import load_kit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = "# CL9\n\n## What\n\nText.\n\n## Why\n\nMore text.\n\n## Flow\n\n![flow](cl9/flow.svg)\n"
BASE = {"part": "CL9", "verdict": "Done.", "bars": ["3 of 3 checks"], "not_checked": ["nothing"], "handoff": "x"}


def git(root, *a):
    subprocess.run(["git", "-C", root] + list(a), check=True, capture_output=True)


class BodyInPost(unittest.TestCase):
    def setUp(self):
        self.kit = load_kit()
        self.root = tempfile.mkdtemp(prefix="kit-cl36-")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
        os.makedirs(os.path.join(self.root, "docs"))
        with open(os.path.join(self.root, "docs", "OLD.md"), "w") as fh:
            fh.write("# Old\n\n## A\n\nx\n")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "old report")
        git(self.root, "checkout", "-q", "-b", "cl9")

    def errs(self, body, part="CL9", name="CL9.md"):
        with open(os.path.join(self.root, "docs", name), "w") as fh:
            fh.write(REPORT)
        p = dict(BASE, part=part, body=body)
        return [e for e in self.kit.check_done(p, root=self.root)[0] if "CL36" in e]

    def test_refuses_a_body_of_section_embeds(self):
        body = ("The memory flow, one design.\n\n## What\n\n`docs/CL9.md § What`\n\n## Why\n\n`docs/CL9.md § Why`\n\n"
                "## Flow\n\n`docs/CL9.md § Flow`\n")
        e = self.errs(body)
        self.assertEqual(len(e), 1, e)
        self.assertIn("docs/CL9.md", e[0])

    def test_refuses_a_file_added_on_the_branch_whatever_its_name(self):
        body = "Lead.\n\n## What\n\n- `docs/memory-flow.md § What`\n- `docs/memory-flow.md § Why`\n"
        self.assertEqual(len(self.errs(body, name="memory-flow.md")), 1)

    def test_a_body_with_its_text_and_diagram_passes(self):
        body = ("The memory flow, one design.\n\n## Flow\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n"
                "![flow](docs/cl9/flow.svg)\n\n## Why\n\nBecause the owner reads posts, not files.\n")
        self.assertEqual(self.errs(body), [])

    def test_embeds_of_files_the_part_did_not_write_pass(self):
        body = "Lead.\n\n## A\n\n`docs/OLD.md § A`\n\n## Rule\n\n`.kit/BOARD.md § Which feature for what`\n"
        self.assertEqual(self.errs(body), [])

    def test_one_embed_among_text_passes(self):
        body = ("Lead.\n\n## What\n\nThe whole result here, in words, long enough to be the result.\n\nMore of it, the "
                "second paragraph of the result.\n\nAnd a third.\n\n## Kept\n\n`docs/CL9.md § Why`\n")
        self.assertEqual(self.errs(body), [])


class OldRuleGone(unittest.TestCase):
    OLD = ("no images inline", "no diagrams", "what a post body cannot show", "a report to keep, with pictures",
           "reads better as docs/<part>.md", "only for a report to keep", "only for what is a document to keep",
           "a long report as a linked doc", "a repo doc only for", "linked doc sections", "pictures or diagrams, embedded by section")

    def test_no_copy_of_the_old_wording(self):
        files = [f for pat in ("kit/**/*.md", ".claude/skills/**/*.md", ".agents/skills/**/*.md", "README.md", "kit/**/*.py", "kit/**/*.json", "scripts/*.py", ".kit/**/*.md", "install.py",
                               "docs/DESIGN-PRINCIPLES.md")
                 for f in glob.glob(os.path.join(ROOT, pat), recursive=True) if "/.kit/run/" not in f]
        left = []
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                t = fh.read()
            left += ["%s: %s" % (os.path.relpath(f, ROOT), o) for o in self.OLD if o in t]
        self.assertEqual(left, [])

    def test_the_guides_teach_the_new_rule(self):
        for rel in ("kit/board/BOARD.template.md", "kit/claude/BOARD.template.md", "kit/part.template.md",
                    "kit/claude-cli/part.template.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                t = fh.read()
            self.assertIn("```mermaid", t, rel)
            self.assertIn("never", t, rel)


if __name__ == "__main__":
    unittest.main()
