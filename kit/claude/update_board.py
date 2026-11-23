#!/usr/bin/env python3
"""Update an existing claude.ai Artifact board to the kit's current page, without touching the project's repo.

    python3 -I kit/claude/update_board.py --board-url https://claude.ai/artifact/<id> --project Harmonize \
        --out docs/board/harmonize-board.html [--talk en] [--guide docs/BOARD.md] [--mcp-readers none]

For a project that already has its board (its own hooks, skills and guides): nothing is installed in it. The page
is rendered by install.py in a scratch folder, built for the Artifact (build_artifact_page.py: the UI layer, the
Artifact adapter, the cross-repo switcher), its guide link pointed at --guide, and written to --out. Then publish
--out to the same Artifact url (`Artifact` publish, `url` the board, no `capabilities`: the stored ones stay). The
board's posts and comments are untouched: they live in the Artifact's database, not in the page.

Run it from a checkout of anlonlt/board (the kit) after each kit change; check the page offline with
`KIT_PAGE=<out> node kit/claude/test_artifact_page.js`.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build(board_url, project, out, talk="en", guide="", mcp_readers=None, kit=KIT):
    tmp = tempfile.mkdtemp(prefix="kit-update-board-")
    try:
        subprocess.run(["git", "init", "-q", tmp], check=True)
        env = dict(os.environ, KIT_NO_FUNNEL="1")
        cmd = [sys.executable, "-I", os.path.join(kit, "install.py"), tmp, "--quick", "--project", project, "--talk", talk,
               "--runtime", "claude", "--no-cmd"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
        page = os.path.join(tmp, ".kit", "board.html")
        if r.returncode != 0 or not os.path.isfile(page):
            raise SystemExit("update_board: install.py did not render the page:\n" + (r.stdout + r.stderr)[-2000:])
        bcmd = [sys.executable, "-I", os.path.join(kit, "kit", "claude", "build_artifact_page.py"), "--kit", kit,
                "--page", page, "--board-url", board_url, "--project", project, "--talk", talk]
        if mcp_readers is not None:
            bcmd += ["--mcp-readers", mcp_readers]
        b = subprocess.run(bcmd, capture_output=True, text=True, timeout=300)
        if b.returncode != 0:
            raise SystemExit("update_board: build_artifact_page.py failed:\n" + (b.stdout + b.stderr)[-2000:])
        html = open(page, encoding="utf-8").read()
        if guide:
            html = html.replace("<code>.kit/BOARD.md</code>", "<code>%s</code>" % guide)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        return len(html.encode("utf-8")), b.stdout.strip().splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board-url", required=True, help="the Artifact board to update (claude.ai/artifact/<id>)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True, help="where to write the page (the file you publish, e.g. the project's page source)")
    ap.add_argument("--talk", default="en", help="the board's language")
    ap.add_argument("--guide", default="", help="the project's board guide the page points to (default .kit/BOARD.md)")
    ap.add_argument("--mcp-readers", default=None, help="as build_artifact_page.py (e.g. none, sharepoint,gmail)")
    a = ap.parse_args(argv)
    size, notes = build(a.board_url, a.project, a.out, a.talk, a.guide, a.mcp_readers)
    print("board page: %s (%d KB) for %s" % (a.out, size // 1024, a.board_url))
    for line in notes:
        if line.startswith("publish capabilities"):
            print(line)
    print("next: Artifact publish, file_path %s, url %s (no capabilities: the stored ones stay)" % (a.out, a.board_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
