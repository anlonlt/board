#!/usr/bin/env python3
"""Make the kit's board page (agy-local) behave in a claude.ai Artifact as it does on the local board server.

    python3 -I build_artifact_page.py --kit /tmp/board-kit --page .kit/board.html \
        --board-url https://claude.ai/artifact/<id> --project "<name>"

The local server injects SHIM_JS (kit/board/board_server.py) into the page: a window.claude emulation over its HTTP
API, then the UI layer (Repo Docs reader, windows, viewer, deliverables, collaborators, git diff, column switcher...).
In an Artifact the real window.claude exists, so this script injects only the UI layer, wrapped with
artifact_adapter.js (its api() answers from the Artifact's db, assets, comments and user), then artifact_ui.js
(Link for AI, repository pictures). It rewrites the page in place and is idempotent (a marker comment).
Run it after every install.py, before publishing; check the result with test_artifact_page.js.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "<!-- kit:artifact-adapter -->"
UI_START = "/* UX enhancements"


def shim_ui(kit):
    src = open(os.path.join(kit, "kit", "board", "board_server.py"), encoding="utf-8").read()
    i = src.index('SHIM_JS = r"""') + len('SHIM_JS = r"""')
    shim = src[i:src.index('"""', i)]
    k = shim.index(UI_START)
    ui = shim[k:].rstrip()
    if not ui.endswith("})();"):
        raise SystemExit("SHIM_JS no longer ends with '})();': the cut point moved, update build_artifact_page.py")
    return ui[:-len("})();")].rstrip()


def build(page, kit, board_url, project):
    if MARK in page:                                   # rebuild from a fresh install.py page, never stack twice
        page = re.sub(re.escape(MARK) + r".*?" + re.escape(MARK + "end") + r"\n?", "", page, flags=re.S)
    adapter = open(os.path.join(HERE, "artifact_adapter.js"), encoding="utf-8").read()
    ui_fix = open(os.path.join(HERE, "artifact_ui.js"), encoding="utf-8").read()
    ui = shim_ui(kit)
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "board"
    head = ("<script>window.__KIT_BOARD_URL__=%s;window.__KIT_PROJECT__=%s;</script>\n"
            % (json.dumps(board_url), json.dumps(project)))
    block = (MARK + "\n" + head + "<script>\n(function () {\n" + adapter + "\n" + ui + "\n})();\n</script>\n"
             + MARK + "end\n")
    pos = page.find("<script>")
    if pos < 0:
        raise SystemExit("no <script> in the page")
    page = page[:pos] + block + page[pos:]
    page = page.rstrip("\n") + "\n" + MARK + "\n<script>\n" + ui_fix + "\n</script>\n" + MARK + "end\n"
    # file names hard-coded to the kit author's project in the template
    page = page.replace('"harmonize-orchestrator-"', json.dumps(slug + "-orchestrator-"))
    return page.replace('"harmonize-board-"', json.dumps(slug + "-board-")).replace('"harmonize-"', json.dumps(slug + "-"))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, help="a checkout of anlonlt/board (branch agy-local)")
    ap.add_argument("--page", default=".kit/board.html")
    ap.add_argument("--board-url", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args(argv)
    page = open(a.page, encoding="utf-8").read()
    out = build(page, a.kit, a.board_url, a.project)
    open(a.page, "w", encoding="utf-8").write(out)
    print("artifact page: %s (%d KB), adapter + %d lines of the server UI layer + artifact_ui"
          % (a.page, len(out.encode("utf-8")) // 1024, shim_ui(a.kit).count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
