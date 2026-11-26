#!/usr/bin/env python3
"""Antigravity on the web runner adapter for the Board Kit (R1).

Bridges dedicated cloud Linux VMs (no local board LAN/Tailscale, no Claude ArtifactData MCP,
no runtime hooks, single-branch default clones) to the board's git copy (`board-mirror`),
local `board_server.py`, tasklists/trains, subtasks, comments, and `/_blob/<id>` attachments.

Commands:
    python3 -I .kit/antigravity_web.py start [--post <id>] [--doing "<line>"] [--session <id>]
    python3 -I .kit/antigravity_web.py resume [--role part|orchestrator|review] [--post <id>]
    python3 -I .kit/antigravity_web.py tasklist [--train <name>] [--zoom <part>|--brief]
    python3 -I .kit/antigravity_web.py train <name> [--zoom <part>|--sections <part>|--full <part>|--brief]
    python3 -I .kit/antigravity_web.py sub <post> <n...> [--status done|open|drop] [--why "..."]
    python3 -I .kit/antigravity_web.py comment <post> --from <who> --text "<text>" [--id <cid>]
    python3 -I .kit/antigravity_web.py attach <post> <file...> [--comment "<text>"] [--from <who>] [--no-body-links]
    python3 -I .kit/antigravity_web.py done <post>
    python3 -I .kit/antigravity_web.py save [--no-blobs] [--no-push]
    python3 -I .kit/antigravity_web.py status [--json]
"""
import argparse
import datetime
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) in (".kit", "kit") else HERE
KIT = os.path.join(ROOT, ".kit") if os.path.isdir(os.path.join(ROOT, ".kit")) else HERE


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wt():
    for cand in (os.path.join(KIT, "web_takeover.py"), os.path.join(ROOT, "kit", "web_takeover.py")):
        if os.path.isfile(cand):
            return _load("kit_web_takeover_aw", cand)
    raise SystemExit("antigravity_web: web_takeover.py not found")


def _cfg():
    for cand in (os.path.join(KIT, "config.json"), os.path.join(ROOT, ".kit", "config.json")):
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                pass
    return {}


def _sh(args, cwd=ROOT, timeout=180, env=None):
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=merged_env)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def _kit_script(name):
    for cand in (
        os.path.join(KIT, name),
        os.path.join(ROOT, ".kit", name),
        os.path.join(ROOT, "scripts", name),
        os.path.join(ROOT, "kit", "board", name),
    ):
        if os.path.isfile(cand):
            return cand
    raise SystemExit("antigravity_web: %s not found" % name)


def _mirror_branch():
    return (_cfg().get("mirror") or {}).get("branch") or "board-mirror"


def _main_branch():
    return (_cfg().get("kit") or {}).get("main_branch") or "main"


def _mirror_path():
    p = (_cfg().get("mirror") or {}).get("path") or "../board-mirror"
    return os.path.normpath(os.path.join(ROOT, p))


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_refs(branches=None, cwd=ROOT):
    """Fetch required branches explicitly so single-branch clones still resolve origin/<branch>."""
    targets = list(branches or (_mirror_branch(), _main_branch(), "kit-usage"))
    results = {}
    for br in targets:
        rc, _, err = _sh(
            ["git", "fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br)],
            cwd=cwd,
            timeout=120,
        )
        results[br] = (rc == 0, err.strip())
    return results


def ensure_board_running():
    wt = _wt()
    wt.git_identity()
    ensure_refs((_mirror_branch(),))
    msg = wt.start_board()
    wt.ensure_mirror_worktree()
    return msg


def _board_cli(*args, env=None):
    return _sh([sys.executable, "-I", _kit_script("board_cli.py")] + list(args), env=env)


def _kit_py(*args, env=None):
    return _sh([sys.executable, "-I", _kit_script("kit.py")] + list(args), env=env)


def _check_dashboard_non_empty():
    """Safety guard: refuse exporting an empty _dashboard over a non-empty one on origin/board-mirror."""
    br = _mirror_branch()
    rc, remote_raw, _ = _sh(["git", "show", "origin/%s:board/posts/_dashboard.json" % br])
    if rc != 0 or not remote_raw.strip():
        return True, ""
    try:
        remote_rows = json.loads(remote_raw).get("rows") or []
    except ValueError:
        remote_rows = []
    if not remote_rows:
        return True, ""
    rc2, local_raw, _ = _board_cli("get", "posts", "_dashboard")
    if rc2 != 0:
        return True, ""
    try:
        local_doc = json.loads(local_raw)
        local_rows = (local_doc.get("data") or {}).get("rows") or []
    except ValueError:
        return False, "local _dashboard JSON unreadable while origin/%s has %d rows" % (br, len(remote_rows))
    if not local_rows:
        return False, "refusing to overwrite non-empty _dashboard (%d rows on origin/%s) with empty local rows" % (
            len(remote_rows), br
        )
    return True, ""


def cmd_start(a):
    refs = ensure_refs()
    fetched = [b for b, (ok, _) in refs.items() if ok]
    print("refs fetched: %s" % (", ".join(fetched) or "none"))
    print(ensure_board_running())
    rc, out, err = _kit_py("reconcile", "--no-push")
    if out.strip():
        print(out.strip().splitlines()[-1])
    elif err.strip():
        print("reconcile: " + err.strip()[-200:])
    if a.post and a.doing:
        env = {"KIT_SESSION_ID": a.session} if a.session else None
        rc_f, out_f, err_f = _kit_py("form", a.post, "--doing", a.doing, env=env)
        if out_f.strip():
            print(out_f.strip())
        if rc_f != 0 and err_f.strip():
            print(err_f.strip(), file=sys.stderr)
            return rc_f
    if a.post:
        rc_r, out_r, err_r = _kit_py("resume", "--role", "part", "--post", a.post)
    else:
        rc_r, out_r, err_r = _kit_py("resume")
    if out_r:
        print(out_r, end="" if out_r.endswith("\n") else "\n")
    if rc_r != 0 and err_r:
        print(err_r, file=sys.stderr)
    return rc_r


def cmd_resume(a):
    ensure_refs((_mirror_branch(),))
    args = ["resume"]
    if a.role:
        args += ["--role", a.role]
    if a.post:
        args += ["--post", a.post]
    rc, out, err = _kit_py(*args)
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr)
    return rc


def cmd_tasklist(a):
    ensure_refs((_mirror_branch(),))
    if a.train:
        args = ["train", a.train]
        if a.zoom:
            args += ["--zoom", a.zoom]
        elif a.sections:
            args += ["--sections", a.sections]
        elif a.full:
            args += ["--full", a.full]
        elif a.brief:
            args.append("--brief")
        rc, out, err = _kit_py(*args)
    else:
        rc, out, err = _kit_py("resume")
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr)
    return rc


def cmd_sub(a):
    ensure_board_running()
    args = ["sub", a.post] + list(a.numbers) + ["--status", a.status]
    if a.why:
        args += ["--why", a.why]
    rc, out, err = _kit_py(*args)
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr)
    return rc


def cmd_comment(a):
    ensure_board_running()
    cid = a.id or ("aw-" + re.sub(r"[^a-z0-9]+", "-", a.post.lower())[:20].strip("-") + "-" + datetime.datetime.now(datetime.timezone.utc).strftime("%H%M%S"))
    payload = {
        "post_id": a.post,
        "from": a.sender,
        "text": a.text,
        "created_at": _utc_now(),
    }
    rc, out, err = _board_cli("set", "comments", cid, "--data", json.dumps(payload, ensure_ascii=False))
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr)
    return rc


def upload_and_attach(post_id, file_paths, comment_text="", sender="R1", add_body_links=True):
    """Upload files as `/_blob/<id>` assets and wire them onto post_id (`files`, `artifacts`, body links)."""
    ensure_board_running()
    uploaded = []
    for path in file_paths:
        if not os.path.isfile(path):
            raise SystemExit("antigravity_web attach: file not found: %s" % path)
        rc, out, err = _board_cli("asset", "upload", path)
        if rc != 0:
            raise SystemExit("antigravity_web attach: upload failed for %s: %s" % (path, (err or out).strip()))
        res = json.loads(out)
        bid = str(res.get("id") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", bid):
            raise SystemExit("antigravity_web attach: unexpected blob response for %s: %s" % (path, out.strip()))
        ctype = res.get("contentType") or mimetypes.guess_type(path)[0] or "application/octet-stream"
        uploaded.append({
            "id": bid,
            "url": "/_blob/" + bid,
            "name": os.path.basename(path),
            "type": ctype,
            "size": int(res.get("size") or os.path.getsize(path)),
        })

    rc, out, err = _board_cli("get", "posts", post_id)
    if rc != 0:
        raise SystemExit("antigravity_web attach: cannot read post %s: %s" % (post_id, (err or out).strip()))
    doc = json.loads(out)
    if not doc.get("exists"):
        raise SystemExit("antigravity_web attach: post %s does not exist on local board" % post_id)
    ver = doc["version"]
    data = doc.get("data") or {}

    files = list(data.get("files") or [])
    by_name = {f.get("name"): i for i, f in enumerate(files) if isinstance(f, dict)}
    for item in uploaded:
        entry = {"id": item["id"], "name": item["name"], "type": item["type"], "size": item["size"]}
        if item["name"] in by_name:
            files[by_name[item["name"]]] = entry
        else:
            files.append(entry)

    artifacts = list(data.get("artifacts") or [])
    for item in uploaded:
        if item["url"] not in artifacts:
            artifacts.append(item["url"])

    patch = {"files": files, "artifacts": artifacts}
    if add_body_links:
        body = str(data.get("body") or "")
        section_lines = ["### Attached review blobs"] + [
            "- [%s](%s) (`%s`, %d bytes)" % (f["name"], "/_blob/" + f["id"], f["type"], int(f.get("size") or 0))
            for f in files if isinstance(f, dict) and f.get("id")
        ]
        section = "\n".join(section_lines)
        if re.search(r"(?m)^### Attached review blobs\s*$", body):
            body = re.sub(r"(?ms)^### Attached review blobs\s*\n.*?(?=^### |\Z)", section + "\n\n", body).rstrip()
        else:
            body = body.rstrip() + "\n\n" + section
        patch["body"] = body

    rc2, out2, err2 = _board_cli("update", "posts", post_id, "--if-version", str(ver), "--data", json.dumps(patch, ensure_ascii=False))
    if rc2 != 0:
        raise SystemExit("antigravity_web attach: post update failed: %s" % (err2 or out2).strip())

    comment_res = None
    if comment_text:
        cid = "aw-blobs-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        c_payload = {
            "post_id": post_id,
            "from": sender,
            "text": comment_text,
            "files": [{"id": u["id"], "name": u["name"], "type": u["type"], "size": u["size"]} for u in uploaded],
            "created_at": _utc_now(),
        }
        rc3, out3, err3 = _board_cli("set", "comments", cid, "--data", json.dumps(c_payload, ensure_ascii=False))
        if rc3 != 0:
            raise SystemExit("antigravity_web attach: comment failed: %s" % (err3 or out3).strip())
        comment_res = json.loads(out3)

    return {"post": post_id, "uploaded": uploaded, "comment": comment_res}


def cmd_attach(a):
    res = upload_and_attach(
        a.post,
        a.files,
        comment_text=a.comment or "",
        sender=a.sender,
        add_body_links=not a.no_body_links,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_done(a):
    ensure_board_running()
    rc, out, err = _kit_py("done", a.post, env={"KIT_BOARD_TRANSPORT": "mirror"})
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, file=sys.stderr)
    return rc


def cmd_save(a):
    wt = _wt()
    wt.git_identity()
    ensure_refs((_mirror_branch(),))
    wt.ensure_mirror_worktree()
    m = _mirror_path()
    br = _mirror_branch()

    if wt.healthy(wt.port()):
        rc_r, out_r, err_r = _kit_py("reconcile", "--no-push")
        if out_r.strip():
            print(out_r.strip().splitlines()[-1])
        elif rc_r != 0 and err_r.strip():
            print("save: reconcile warning: " + err_r.strip()[-200:], file=sys.stderr)

    ok_dash, why_dash = _check_dashboard_non_empty()
    if not ok_dash:
        print("save: ABORTED: " + why_dash, file=sys.stderr)
        return 1

    if _sh(["git", "-C", m, "fetch", "-q", "--no-tags", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br)])[0] == 0:
        _sh(["git", "-C", m, "reset", "--hard", "origin/%s" % br])

    sync_args = ["sync-db", "--full", "--commit"]
    if not a.no_blobs:
        sync_args.append("--with-blobs")
    rc_s, out_s, err_s = _kit_py(*sync_args)
    if out_s.strip():
        print(out_s.strip())
    if rc_s != 0:
        print("save: sync-db FAILED: " + (err_s or out_s).strip()[-400:], file=sys.stderr)
        return rc_s

    if a.no_push:
        print("save: committed locally in %s (--no-push)" % m)
        return 0

    rc_a, ahead, _ = _sh(["git", "-C", m, "rev-list", "--count", "origin/%s..HEAD" % br])
    if rc_a == 0 and ahead.strip() == "0":
        print("save: origin/%s is already up to date" % br)
        return 0

    rc_p, out_p, err_p = _sh(["git", "-C", m, "push", "origin", "HEAD:%s" % br])
    if rc_p != 0:
        rc_m, _, _ = _sh(["git", "-C", m, "pull", "--no-rebase", "--no-edit", "-X", "ours", "origin", br])
        if rc_m == 0:
            rc_p, out_p, err_p = _sh(["git", "-C", m, "push", "origin", "HEAD:%s" % br])
        else:
            _sh(["git", "-C", m, "merge", "--abort"])
    if rc_p != 0:
        print("save: PUSH FAILED: " + (err_p or out_p).strip()[-400:], file=sys.stderr)
        return 1
    rc_sha, sha, _ = _sh(["git", "-C", m, "rev-parse", "--short", "HEAD"])
    print("save: pushed %s to origin/%s" % (sha.strip() if rc_sha == 0 else "HEAD", br))
    return 0


def cmd_status(a):
    wt = _wt()
    br = _mirror_branch()
    rc_m, m_sha, _ = _sh(["git", "rev-parse", "--short", "origin/%s" % br])
    rc_b, cur_br, _ = _sh(["git", "branch", "--show-current"])
    blob_dir = os.path.join(KIT, "board_data", "blobs")
    blobs = [f for f in os.listdir(blob_dir) if re.fullmatch(r"[0-9a-f]{32}", f)] if os.path.isdir(blob_dir) else []
    info = {
        "runner": "antigravity-web" if wt.is_antigravity_web() else ("claude-web" if wt.is_web() else "local"),
        "antigravity_web": wt.is_antigravity_web(),
        "board_port": wt.port(),
        "board_healthy": wt.healthy(wt.port()),
        "branch": cur_br.strip() if rc_b == 0 else "",
        "mirror_branch": br,
        "mirror_ref": m_sha.strip() if rc_m == 0 else None,
        "mirror_worktree": os.path.exists(os.path.join(_mirror_path(), ".git")),
        "local_blobs": len(blobs),
    }
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print("runner=%s board=%s(:%d) branch=%s mirror=%s blobs=%d" % (
            info["runner"], "up" if info["board_healthy"] else "down", info["board_port"],
            info["branch"] or "?", info["mirror_ref"] or "missing", info["local_blobs"],
        ))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="antigravity_web.py", description="Board Kit adapter for Antigravity on the web")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p_start = sp.add_parser("start", help="Fetch board-mirror, boot local board, reconcile, and print resume")
    p_start.add_argument("--post", help="Part post id to resume")
    p_start.add_argument("--doing", help="One-line Turn 1 status to claim the post via kit.py form")
    p_start.add_argument("--session", help="Session/conversation ID to record on the post")
    p_start.set_defaults(fn=cmd_start)

    p_res = sp.add_parser("resume", help="Fetch board-mirror ref and run kit.py resume")
    p_res.add_argument("--role", choices=("part", "orchestrator", "review", "clerk"))
    p_res.add_argument("--post")
    p_res.set_defaults(fn=cmd_resume)

    p_tl = sp.add_parser("tasklist", help="Show the board tasklist or a train's tasklist")
    p_tl.add_argument("--train", help="Train id (omit for full resume tasklist)")
    p_tl.add_argument("--zoom")
    p_tl.add_argument("--sections")
    p_tl.add_argument("--full")
    p_tl.add_argument("--brief", action="store_true")
    p_tl.set_defaults(fn=cmd_tasklist)

    p_tr = sp.add_parser("train", help="Inspect a train's tasklist and steps")
    p_tr.add_argument("train", help="Train id")
    p_tr.add_argument("--zoom")
    p_tr.add_argument("--sections")
    p_tr.add_argument("--full")
    p_tr.add_argument("--brief", action="store_true")
    p_tr.set_defaults(fn=cmd_tasklist)

    p_sub = sp.add_parser("sub", help="Tick a post's numbered subtasks")
    p_sub.add_argument("post")
    p_sub.add_argument("numbers", nargs="+")
    p_sub.add_argument("--status", default="done", choices=("done", "open", "doing", "drop"))
    p_sub.add_argument("--why", default="")
    p_sub.set_defaults(fn=cmd_sub)

    p_cmt = sp.add_parser("comment", help="Add a comment to a post")
    p_cmt.add_argument("post")
    p_cmt.add_argument("--from", dest="sender", required=True)
    p_cmt.add_argument("--text", required=True)
    p_cmt.add_argument("--id", default="")
    p_cmt.set_defaults(fn=cmd_comment)

    p_att = sp.add_parser("attach", help="Upload files as /_blob/<id> and attach them to a post")
    p_att.add_argument("post")
    p_att.add_argument("files", nargs="+")
    p_att.add_argument("--comment", default="", help="Optional comment carrying the attached blobs")
    p_att.add_argument("--from", dest="sender", default="R1")
    p_att.add_argument("--no-body-links", action="store_true")
    p_att.set_defaults(fn=cmd_attach)

    p_done = sp.add_parser("done", help="Run kit.py done using mirror transport")
    p_done.add_argument("post")
    p_done.set_defaults(fn=cmd_done)

    p_save = sp.add_parser("save", help="Reconcile, export full board + blobs, and push origin/board-mirror")
    p_save.add_argument("--no-blobs", action="store_true")
    p_save.add_argument("--no-push", action="store_true")
    p_save.set_defaults(fn=cmd_save)

    p_st = sp.add_parser("status", help="Show runner, board, mirror, and blob status")
    p_st.add_argument("--json", action="store_true")
    p_st.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
