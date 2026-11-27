#!/usr/bin/env python3
"""CL71: `.kit/manifest.json` (what the kit installed: path -> sha256, and `_kit_commit`) is generated, so it is not in
git: two branches that each regenerated it conflicted at every merge. install.py writes it; a clone that has none
(a new web container, a fresh checkout) rebuilds it here, from the tree.

    load(root)      the manifest; rebuilt from the tree when the file is missing or unreadable (and written, once)
    rebuild(root)   what the tree says the kit installed: each file under the kit's own paths that git tracks and
                    that equals its committed copy (an edit not committed yet is the project's: left out, so an
                    update keeps it); `_kit_commit` is the last commit that changed .kit/kit.py, with its date

Read with importlib from a path (the kit's scripts run with `python3 -I`: no sibling import)."""
import hashlib
import json
import os
import subprocess

NAME = os.path.join(".kit", "manifest.json")
# the paths install.py writes the kit's own files to; what a project writes there itself is not in them
PREFIXES = (".kit/", ".agents/hooks/kit/", ".claude/hooks/kit/", ".agents/skills/board/", ".agents/skills/memory/",
            ".claude/skills/board/", ".claude/skills/memory/", ".claude/agents/kit-cards.md")
# under .kit/ but not the kit's install: the project's settings, run state, the board's copy, generated maps
NOT_KIT = (".kit/config.json", ".kit/manifest.json", ".kit/run/", ".kit/board_data/", ".kit/artifact-files/",
           ".kit/handover.flag", ".kit/handover.log", ".kit/handover-", ".kit/__pycache__/", ".kit/hooks/__pycache__/")
GENERATED = (".kit/manifest.json", ".kit/artifact-files/")      # never merged, never a reason to review again


def generated(path):
    """True for a file the kit generates and keeps out of git (CL71)."""
    p = str(path).replace(os.sep, "/")
    return any(p == g or (g.endswith("/") and p.startswith(g)) for g in GENERATED)


def _git(root, *a):
    try:
        r = subprocess.run(["git", "-C", root] + list(a), capture_output=True, timeout=60)
        return r.stdout if r.returncode == 0 else b""
    except (OSError, subprocess.SubprocessError):
        return b""


def _blob(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def rebuild(root):
    out = {}
    committed = {}
    for ln in _git(root, "ls-files", "-s", "-z", "--", *[p.rstrip("/") for p in PREFIXES]).split(b"\0"):
        if not ln:
            continue
        meta, _, path = ln.partition(b"\t")
        parts = meta.split()
        if len(parts) >= 2:
            committed[path.decode("utf-8", "replace")] = parts[1].decode()
    for rel, blob in sorted(committed.items()):
        if rel.startswith(NOT_KIT):
            continue
        if not any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PREFIXES):
            continue
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if _blob(data) != blob:                    # edited and not committed: the project's, kept by an update
            continue
        out[rel] = hashlib.sha256(data).hexdigest()
    at = _git(root, "log", "-1", "--format=%H %cI", "--", ".kit/kit.py").decode().strip()
    if at:
        out["_kit_commit"] = at
    out["_rebuilt"] = "from the tree (CL71: the manifest is not in git)"
    return out


def load(root, write=True):
    p = os.path.join(root, NAME)
    try:
        with open(p, encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            return got
    except (OSError, ValueError):
        pass
    got = rebuild(root)
    if write and len(got) > 2:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(got, fh, indent=1, sort_keys=True)
                fh.write("\n")
        except OSError:
            pass
    return got


def module(root):
    """This file loaded from `root`/.kit (or the kit's own kit/), for a reader that runs with -I; None when absent."""
    import importlib.util
    for cand in (os.path.join(root, ".kit", "kit_manifest.py"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit_manifest.py")):
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location("kit_manifest", cand)
            m = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(m)
                return m
            except Exception:  # noqa: BLE001
                continue
    return None


if __name__ == "__main__":
    import sys
    print(json.dumps(load(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "."), write=False), indent=1, sort_keys=True))
