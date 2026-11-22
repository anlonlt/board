#!/usr/bin/env python3
"""Optional GitHub PR / CI badge sync for board (zero external dependencies; uses `gh` CLI).

Keeps board strictly as the control & memory plane: any open post carrying `"pr": 123` (or URL)
or `"branch": "feat/x"` gets clean status badges (`badges: [{label, state, url}]`) pushed via
board_cli.py so PR review & CI check states render directly on the card.

Usage:
    python3 -I .kit/gh_sync.py [--auto-done] [--dry-run]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _pr_badges(pr_info):
    badges = []
    num = pr_info.get("number")
    url = pr_info.get("url") or ""
    st = str(pr_info.get("state") or "").upper()
    rev = str(pr_info.get("reviewDecision") or "").upper()
    if st == "MERGED":
        badges.append({"label": "PR #%s · Merged" % num, "state": "done", "url": url})
    elif st == "CLOSED":
        badges.append({"label": "PR #%s · Closed" % num, "state": "blocked", "url": url})
    elif rev == "APPROVED":
        badges.append({"label": "PR #%s · Approved" % num, "state": "done", "url": url})
    elif rev == "CHANGES_REQUESTED":
        badges.append({"label": "PR #%s · Changes requested" % num, "state": "todo", "url": url})
    else:
        badges.append({"label": "PR #%s · Open" % num, "state": "running", "url": url})

    checks = pr_info.get("statusCheckRollup") or []
    if isinstance(checks, list) and checks:
        concs = [str(c.get("conclusion") or c.get("state") or "").upper() for c in checks if isinstance(c, dict)]
        if any(x in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED") for x in concs):
            badges.append({"label": "CI · Failing", "state": "todo", "url": url})
        elif any(x in ("PENDING", "IN_PROGRESS", "QUEUED", "") for x in concs):
            badges.append({"label": "CI · Running", "state": "running", "url": url})
        else:
            badges.append({"label": "CI · Passing", "state": "done", "url": url})
    return badges, st == "MERGED"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync GitHub PR & CI status badges onto board cards")
    ap.add_argument("--root", default=".", help="Project root containing .kit/board_cli.py")
    ap.add_argument("--auto-done", action="store_true", help="Move cards with merged PRs to column=done")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not shutil.which("gh"):
        print("gh_sync: `gh` CLI not found in PATH; skipping", file=sys.stderr)
        return 0
    root = os.path.abspath(a.root)
    cli = os.path.join(root, ".kit", "board_cli.py")
    if not os.path.isfile(cli):
        print("gh_sync: %s not found" % cli, file=sys.stderr)
        return 1
    r = _run([sys.executable, "-I", cli, "list", "posts"], cwd=root)
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
        return r.returncode
    docs = (json.loads(r.stdout).get("docs") or [])
    updated = 0
    for d in docs:
        pid = d.get("id") or ""
        data = d.get("data") or {}
        if pid.startswith("_") or data.get("removed") or data.get("column") == "done":
            continue
        ref = str(data.get("pr") or data.get("branch") or "").strip()
        if not ref:
            continue
        gh = _run(["gh", "pr", "view", ref, "--json", "number,state,url,statusCheckRollup,reviewDecision"], cwd=root)
        if gh.returncode != 0:
            continue
        try:
            pr_info = json.loads(gh.stdout)
        except ValueError:
            continue
        badges, merged = _pr_badges(pr_info)
        patch = {"badges": badges}
        if merged and a.auto_done:
            patch["column"] = "done"
        if data.get("badges") == badges and (not merged or not a.auto_done):
            continue
        if a.dry_run:
            print("DRY-RUN %s -> %s" % (pid, json.dumps(patch)))
        else:
            ur = _run([sys.executable, "-I", cli, "update", "posts", pid, "--data", json.dumps(patch)], cwd=root)
            if ur.returncode == 0:
                print("UPDATED %s (%s)" % (pid, ", ".join(b["label"] for b in badges)))
                updated += 1
    print("gh_sync: %d card(s) updated" % updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
