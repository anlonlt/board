#!/usr/bin/env python3
"""Claude Code PreToolUse (ArtifactData, create_session): the orchestrator works the board through the kit.

Orchestrators 48 to 50 read the board by raw queries, wrote step posts by hand and launched parts with inline
prompts, although the kit scripts all of it for a fraction of the tokens (the owner, 2026-11-23: "we've literally
taken the time to script everything for token efficiency"). Rules in a guide were skipped; this hook refuses, for
the orchestrator only, the three gestures that bypass the kit, and says the kit's way:
  - a `list` or broad `query` of posts or comments (also an attached board's repos/<id>/...), except the kit's own
    pull (each call `kit.py artifact pull` printed, whole, once, in the last 30 minutes, M11) and a targeted lookup;
    while a batch of `kit.py artifact push` is out (not recorded sent), only `get` of the documents it names passes (a `where`, limit <= 5): use
    `kit.py artifact pull` / `load`, then `kit.py resume`, `post <id>`, `open`, `since <time>`;
  - a `set` of a step post (not a `_` card, not `_orchestrator`): plan it with TaskCreate {train, part}: the
    task-train hook writes the post and its prompt; a batch from `kit.py artifact push` (.kit/run/artifact/out) passes;
  - `create_session` with an inline prompt: a part's prompt starts with `Your prompt is docs/prompts/<id>.md` and
    stays short (<= 400 characters; a long prompt that merely names a path is still an inline prompt); only a tag
    lets another launch through (`orchestrator` a successor, `board-clerk` the clerk, `review` a review): the title
    is never read for this (CL24); and its `source_url` is the step's repo (CL26: its post on an attached board in
    the local copy names it; else a known clone holding the prompt file), so the part can push its work.
A targeted lookup pages no further: a `where` query with limit <= 5 that carries a `cursor` is a broad read.
CL30 (Harmonize docs/KIT-SCRIPTS.md § Gaps, the owner's yes on o52-q-close-gaps-1022): the board rules hold EVERY
session, parts, the clerk and reviews too, not only the orchestrator:
  - a raw `list` / broad `query` of posts or comments: as above, for everyone;
  - a hand write of a post or a comment (`set`, `update`, `str_replace`, `delete`, or in a `batch`) where the kit
    has the command: `board_cli.py update/set/reply` on the local copy, then `kit.py artifact push` and its batch
    (a batch whose writes come from .kit/run/artifact/out passes). What has no kit form passes: other collections
    (`typing`, `kit_artifacts`...), the clerk's marks on a comment (`read`, `read_at`, `waiting_note`...) and the
    orchestrator's new session on `_orchestrator` (`session_id`, `started_by`, `started_at`).
and the orchestrator's launches are checked whatever their tag: a successor (`orchestrator`) names the kit's
orchestrator guide, a clerk (`board-clerk`) and a review (`review`) start `Your prompt is <the kit's file>`; a part
starts only after `kit.py launch <post>` (it leaves .kit/run/launch/<post>.json, the post in progress and its Trains
row running). The owner's own sessions: KIT_ROLE=owner. The orchestrator's subagents are exempt by design: they exist
to keep a dump (a full list, a card rebuild) out of the orchestrator's context, so the hook lets them read and write
the board raw. Exit 2 with the reason on stderr.
"""
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, project_dir  # noqa: E402

BOARD_COLL = re.compile(r"^(repos/[^/]+/)?(posts|comments)$")
PART_PROMPT = re.compile(r"^Your prompt is docs/prompts/[\w.-]+\.md\b")
PROMPT_MAX = 400
PASS_TAGS = ("orchestrator", "board-clerk", "review")
# CL30: what each tag's prompt must be (a tag alone used to let any prompt through)
TAG_PROMPTS = {
    "orchestrator": (re.compile(r"\.kit/(?:claude-cli/|claude/)?(?:orchestrator|takeover)\.md\b"), 1200,
                     "names the kit's guide (.kit/claude-cli/orchestrator.md, .kit/orchestrator.md or takeover.md): "
                     "`python3 -I .kit/handover.py --role orchestrator` prints the call"),
    "board-clerk": (re.compile(r"^Your prompt is \.kit/(?:claude-cli/|claude/)?clerk\.md\b"), PROMPT_MAX,
                    "starts `Your prompt is .kit/claude-cli/clerk.md` (or .kit/clerk.md): `python3 -I .kit/handover.py "
                    "--role clerk` prints the call"),
    "review": (re.compile(r"^Your prompt is (?:\.kit/run|docs/prompts)/[\w.-]+\.md\b"), PROMPT_MAX,
               "starts `Your prompt is .kit/run/<review id>.md`: `python3 -I .kit/kit.py review <post>` writes it and "
               "prints the call"),
}
WRITE_OPS = ("set", "update", "str_replace", "delete")
# fields with no kit command on the Artifact: the clerk's marks on a comment, a new session on _orchestrator
MARK_FIELDS = {"read", "read_at", "seen", "seen_at", "waiting_note", "acked", "acked_at"}
ORCH_FIELDS = {"session_id", "started_by", "started_at"}
LAUNCH_MAX_AGE_S = 6 * 3600
ORCH_TEXT = re.compile(r"start as the orchestrator|claude-cli/orchestrator\.md|\.kit/orchestrator\.md|"
                       r"you are the [\w-]+ orchestrator|you are the orchestrator", re.I)


def first_prompt(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "user" and not e.get("isMeta") and not e.get("isSidechain"):
                    c = (e.get("message") or {}).get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text") or "" for x in c if isinstance(x, dict))
                    c = str(c or "")
                    if c and not c.lstrip().startswith("<"):
                        return c[:4000]
    except OSError:
        pass
    return ""


def is_orchestrator(data):
    role = os.environ.get("KIT_ROLE") or ""
    if role:
        return role == "orchestrator"
    if os.environ.get("HARMONIZE_ROLE") == "orchestrator":
        return True
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", str(data.get("session_id") or "x"))
    cache = os.path.join(tempfile.gettempdir(), "kit-orch-role-%s" % sid)
    try:
        return open(cache).read() == "orchestrator"
    except OSError:
        pass
    found = False
    prefix = str((config(data).get("kit") or {}).get("orchestrator_branch_prefix") or "")
    if prefix:
        try:
            br = subprocess.run(["git", "-C", project_dir(data), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
            found = br.startswith(prefix)
        except (OSError, subprocess.SubprocessError):
            pass
    if not found:
        found = bool(ORCH_TEXT.search(first_prompt(str(data.get("transcript_path") or ""))))
    try:
        open(cache, "w").write("orchestrator" if found else "other")
    except OSError:
        pass
    return found


def under(path, root, *parts):
    if not path:
        return False
    base = os.path.realpath(os.path.join(root, *parts))
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    return p == base or p.startswith(base + os.sep)


READ_WAY = ("a whole collection is read only through the kit: `python3 -I .kit/kit.py artifact pull` (it prints the "
            "ArtifactData calls, changes only after the first load; a part's first: `--post <id>`, its own documents; "
            "the attached boards' repos/<id>/posts and comments too, "
            "saved under .kit/run/artifact/in), `python3 -I .kit/kit.py artifact load`, then `kit.py resume`, "
            "`kit.py post <id>`, `kit.py open`, `kit.py since <time>`; one document: ArtifactData `get`; a targeted "
            "lookup: `query` with a `where` and limit <= 5")
STEP_WAY = ("plan the step with TaskCreate, metadata {\"train\": ..., \"part\": ...}: the task-train hook writes its post "
            "(the task, written once) and answers with the launch line; `kit.py launch` generates docs/prompts/<id>.md from it; local changes go to the Artifact "
            "with `python3 -I .kit/kit.py artifact push`")
LAUNCH_WAY = ("launch a part from its prompt file: TaskCreate {train, part} first (the hook writes "
              "its post), `python3 -I .kit/kit.py launch <id>` (it generates and commits docs/prompts/<id>.md from the post, and prints the call), then create_session with the prompt \"Your prompt is docs/prompts/<id>.md: read "
              "it and do it.\" (at most %d characters, starting so; a successor orchestrator: tag `orchestrator`; a clerk: "
              "tag `board-clerk`; a review: tag `review`; the title is not read)" % PROMPT_MAX)


WRITE_WAY = ("write the board through the kit: on the local copy (`python3 -I .kit/kit.py artifact pull`, the calls, "
             "`kit.py artifact load` first), `python3 -I .kit/board_cli.py update posts <id> --data '{...}'` (your "
             "post), `board_cli.py reply <post> --text ...` (an answer), `board_cli.py set comments <id> --data ...`; "
             "then `python3 -I .kit/kit.py artifact push`, the ArtifactData batch it prints, and `kit.py artifact sent "
             "<batch>`")


def gh_repo(u):
    """owner/name of a GitHub URL or slug (lower case), '' if none."""
    u = str(u or "").strip()
    m = re.search(r"(?:github\.com[/:])?([\w.-]+/[\w.-]+?)(?:\.git)?/?$", u)
    return m.group(1).lower() if u and m else ""


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def known_repos(root):
    """{owner/name: local clone or None}: this repo, the config's portfolio and the Artifact's kit_repos/_index (as
    `kit.py artifact pull` saved it), with a clone next to this repo when there is one."""
    cfg = _read(os.path.join(root, ".kit", "config.json")) or {}
    k = cfg.get("kit") or {}
    own = gh_repo(k.get("github") or cfg.get("github"))
    if not own:
        try:
            own = gh_repo(subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True,
                                         text=True, timeout=5).stdout)
        except (OSError, subprocess.SubprocessError):
            own = ""
    repos, ids = {}, {}
    if own:
        repos[own] = root
    rows = [r for r in (cfg.get("portfolio") or []) if isinstance(r, dict)]
    idx = _read(os.path.join(art_in(root), "kit_repos", "_index.json")) or {}
    idx = idx.get("data") if isinstance(idx.get("data"), dict) else idx
    rows += [r for r in (idx.get("repos") or []) if isinstance(r, dict)]
    up = os.path.dirname(os.path.abspath(root))
    for r in rows:
        gh = gh_repo(str(r.get("github") or "").replace("github:", ""))
        if not gh:
            continue
        cands = [r.get("path"), os.path.join(up, gh.split("/")[1])]
        clone = next((c for c in cands if c and os.path.isdir(os.path.join(c, ".git"))), None)
        repos[gh] = repos.get(gh) or clone
        for key in (r.get("id"), r.get("name")):
            if key:
                ids[re.sub(r"[^a-z0-9_-]+", "-", str(key).lower()).strip("-")] = gh
    return own, repos, ids


def kit_roots(root):
    """This repo and the clones of the boards attached to it (K1: a step or an answer on an attached board runs the kit
    from that board's clone, so its pull and push live under the clone's .kit/run/artifact)."""
    try:
        clones = [c for c in known_repos(root)[1].values() if c]
    except Exception:
        clones = []
    return [root] + [c for c in clones if os.path.abspath(c) != os.path.abspath(root)]


def art_in(root):
    return os.path.join(os.environ.get("KIT_ARTIFACT_DIR") or os.path.join(root, ".kit", "run", "artifact"), "in")


def launch_repo(prompt, src, root):
    """CL26: a part's session clones the repo of its step. None, or why this source_url is the wrong one."""
    m = PART_PROMPT.match(prompt)
    if not m:
        return None
    rel = prompt[len("Your prompt is "):m.end()]
    pid = os.path.basename(rel)[:-3]
    own, repos, ids = known_repos(root)
    target = gh_repo(src) or own
    if not target:
        return None
    # the step's board: its post in the local copy of an attached board (repos/<id>/posts/<pid>.json)
    for f in glob.glob(os.path.join(art_in(root), "repos", "*", "posts", pid + ".json")):
        step = ids.get(os.path.basename(os.path.dirname(os.path.dirname(f))))
        if step and step != target:
            return ("step %s is on the board of %s, so its session clones that repo: source_url "
                    "\"https://github.com/%s\" (its prompt %s must be committed there: write it with `kit.py step "
                    "new` run in that repo's clone)" % (pid, step, step, rel))
        if step:
            return None
    holders = [r for r, c in repos.items() if c and os.path.isfile(os.path.join(c, rel))]
    if holders and target not in holders and repos.get(target):
        return ("its prompt %s is in %s, not in %s: launch from %s" % (rel, ", ".join(holders), target, holders[0]))
    return None


def _hand_write(w, root):
    """None, or what this one write is when it is a hand write of the board that the kit's scripts make."""
    op = str(w.get("op") or w.get("action") or "")
    m = BOARD_COLL.match(str(w.get("collection") or ""))
    if op not in WRITE_OPS or not m:
        return None
    if any(under(str(w.get("file_path") or ""), r, ".kit", "run", "artifact", "out") for r in kit_roots(root)):
        return None                                   # a batch from `kit.py artifact push` (here or an attached clone)
    keys = set((w.get("data") or {}).keys()) if isinstance(w.get("data"), dict) else None
    did = str(w.get("doc_id") or "")
    if op == "update" and keys:
        if m.group(2) == "comments" and keys <= MARK_FIELDS:
            return None                               # the clerk's mark on the owner's comment
        if m.group(2) == "posts" and did == "_orchestrator" and keys <= ORCH_FIELDS:
            return None                               # the orchestrator's new session
    return (m.group(2), "%s/%s" % (w.get("collection"), did))


PULL_FRESH_S = 30 * 60
PULL_V = 2


def _art_bases(root):
    bases = [os.path.join(r, ".kit", "run", "artifact") for r in kit_roots(root)]
    if os.environ.get("KIT_ARTIFACT_DIR"):
        bases.insert(0, os.environ["KIT_ARTIFACT_DIR"])
    return bases


def _same_call(c, ti):
    """A printed call (pull.json) and this one: the same action, collection, `where` and `limit` (a cursor aside)."""
    q = ti.get("query") if isinstance(ti.get("query"), dict) else {}
    return (c.get("action") == ti.get("action") and c.get("collection") == ti.get("collection")
            and (c.get("where") or None) == (q.get("where") or None) and c.get("limit") == q.get("limit"))


def pulled(ti, root):
    """M11: this whole-collection read is one `kit.py artifact pull` printed, whole (its record .kit/run/artifact/
    pull.json, version 2, under 30 minutes old: action, collection, where, limit), saved under that copy's
    .kit/run/artifact, and not used yet: each printed call passes once (CR-m11: a record of {action, collection}
    let any broad query of that collection through for 30 minutes). The next pages of a printed list (its `cursor`)
    pass after it. Used calls are marked in the record."""
    out = str(ti.get("out_dir") or "")
    q = ti.get("query") if isinstance(ti.get("query"), dict) else {}
    for base in _art_bases(root):
        if not under(out, base):
            continue
        p = os.path.join(base, "pull.json")
        rec = _read(p) or {}
        try:
            fresh = rec.get("v") == PULL_V and time.time() - float(rec.get("at") or 0) <= PULL_FRESH_S
        except (TypeError, ValueError):
            fresh = False
        if not fresh:
            continue
        for c in rec.get("calls") or []:
            if not isinstance(c, dict) or not _same_call(c, ti):
                continue
            if q.get("cursor"):
                if c.get("used"):
                    return True                   # the next page of a list the pull printed
                continue
            if c.get("used"):
                continue
            c["used"] = time.time()
            try:
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(rec, fh)
            except OSError:
                pass
            return True
    return False


def pending_batch(root):
    """M11 (CR-m11): a batch `kit.py artifact push` built that is not recorded sent yet (its record
    .kit/run/artifact/pending.json, 30 minutes): {ids: ["<collection>/<doc_id>", ...]}, else None. While it is out,
    a refused batch is followed only by `get` of the documents it names (then `artifact pin`), never a list."""
    for base in _art_bases(root):
        rec = _read(os.path.join(base, "pending.json"))
        if not isinstance(rec, dict):
            continue
        try:
            if time.time() - float(rec.get("at") or 0) <= PULL_FRESH_S and rec.get("ids"):
                return rec
        except (TypeError, ValueError):
            pass
    return None


def board_verdict(ti, root, orch):
    """CL30: the board rules for every session (the orchestrator, parts, the clerk, reviews)."""
    act = str(ti.get("action") or "")
    pend = pending_batch(root) if act in ("list", "query", "get") else None
    if pend:
        key = "%s/%s" % (ti.get("collection"), ti.get("doc_id"))
        if act == "get" and key in pend["ids"]:
            return None
        return ("a %s while a batch of `kit.py artifact push` is out (%s): after a refused batch read only the "
                "documents it names, each by `get` (%s), then `python3 -I .kit/kit.py artifact pin <collection>/<id>=<N>` "
                "and push again; a sent batch: `kit.py artifact sent <batch>` first." % (
                    act, key if act == "get" else ti.get("collection"), ", ".join(pend["ids"][:6])))
    if act in ("list", "query") and BOARD_COLL.match(str(ti.get("collection") or "")):
        if pulled(ti, root):
            return None
        q = ti.get("query") or {}
        try:
            lim = int(q.get("limit") or 100)
        except (TypeError, ValueError):
            lim = 100
        if act == "query" and q.get("where") and lim <= 5 and not q.get("cursor"):
            return None
        return "a raw %s of %s: %s." % (act, ti.get("collection"), READ_WAY)
    writes = ti.get("writes") if act == "batch" else [dict(ti, op=act)]
    for w in writes or []:
        if not isinstance(w, dict):
            continue
        hw = _hand_write(w, root)
        if not hw:
            continue
        kind, where = hw
        if orch and kind == "posts" and not str(w.get("doc_id") or "").startswith("_"):
            return "a step post written by hand (%s): %s; any other change: %s." % (where, STEP_WAY, WRITE_WAY)
        return "a %s written by hand (%s, `%s`): %s." % ("post" if kind == "posts" else "comment", where,
                                                        w.get("op") or w.get("action"), WRITE_WAY)
    return None


INLINE = re.compile(r"^Your prompt follows; first commit it to docs/prompts/([\w.-]+)\.md on your branch")


def launch_marked(pid, prompt, root, inline=False):
    """CL30: `kit.py launch <post>` ran for this part (its mark in .kit/run/launch/ of this repo or the step's clone)."""
    _, repos, _ = known_repos(root)
    for r in [root] + [c for c in repos.values() if c and c != root]:
        d = _read(os.path.join(r, ".kit", "run", "launch", pid + ".json"))
        if not isinstance(d, dict):
            continue
        try:
            fresh = time.time() - float(d.get("at") or 0) <= LAUNCH_MAX_AGE_S
        except (TypeError, ValueError):
            fresh = False
        if fresh and str(d.get("prompt") or "").strip() == prompt and bool(d.get("inline")) == inline:
            return True
    return False


def launch_verdict(ti, root):
    prompt = str(ti.get("prompt") or "").strip()
    tags = [str(t) for t in (ti.get("tags") or [])]
    for t in tags:
        if t in TAG_PROMPTS:
            rx, cap, way = TAG_PROMPTS[t]
            if rx.search(prompt) and len(prompt) <= cap:
                return None
            return "a launch tagged `%s` whose prompt is not the kit's (the tag no longer lets any prompt through): it " \
                   "%s, at most %d characters." % (t, way, cap)
    if PART_PROMPT.match(prompt) and len(prompt) <= PROMPT_MAX:
        why = launch_repo(prompt, ti.get("source_url"), root)
        if why:
            return "a part launched from the wrong repo: %s." % why
        pid = os.path.basename(prompt[len("Your prompt is "):PART_PROMPT.match(prompt).end()])[:-3]
        if not launch_marked(pid, prompt, root):
            return ("a part started without the kit's launch: run `python3 -I .kit/kit.py launch %s` first (it puts the "
                    "post in progress and its Trains row running, and prints this create_session call), then `kit.py "
                    "launch %s --session-id <the id create_session returns>`." % (pid, pid))
        return None
    m = INLINE.match(prompt)          # CL40: the board could not be pushed, `kit.py launch` wrote this text and marked it
    if m and launch_marked(m.group(1), prompt, root, inline=True):
        return None
    return "a part launched with an inline prompt: %s." % LAUNCH_WAY


def subagent_verdict(ti, root):
    """A subagent reads and writes the board raw (it keeps a dump out of the orchestrator's context), but what it writes
    by hand under the owner's eyes is gated like anyone's: a question in a thread, an unknown lane, a step completed
    without proof. A sync's files of an attached board (history, copied as it is) pass."""
    p = next((x for x in (os.path.join(root, ".kit", "post_gate.py"), os.path.join(root, "kit", "board", "post_gate.py")) if os.path.isfile(x)), None)
    if not p:
        return None
    spec = importlib.util.spec_from_file_location("kit_post_gate", p)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    act = str(ti.get("action") or "")
    for w in (ti.get("writes") if act == "batch" else [dict(ti, op=act)]) or []:
        m = isinstance(w, dict) and BOARD_COLL.match(str(w.get("collection") or ""))
        if not m or str(w.get("op") or w.get("action") or "") not in ("set", "update"):
            continue
        fp = str(w.get("file_path") or "")
        if fp and (m.group(1) or any(under(fp, r, ".kit", "run", "artifact", "out") for r in kit_roots(root))):
            continue
        d = w.get("data") if isinstance(w.get("data"), dict) else _read(fp) if fp else None
        if not isinstance(d, dict):
            continue
        did = str(w.get("doc_id") or "")
        if m.group(2) == "comments":
            why = g.check_comment(did, d) if "text" in d else None
        else:
            why = None if did.startswith("_") else "lane %r does not exist: %s" % (d["column"], ", ".join(g.LANES)) if d.get("column") and d["column"] not in g.LANES \
                else "a step is completed by `kit.py step complete`, after `kit.py done`" if g.is_complete(d) and not g.proved(d) and d.get("column") != "done" \
                else "`proof` is what `kit.py done` leaves, never written by hand" if "proof" in d and act != "batch" else None
        if why:
            return "%s/%s: %s" % (w.get("collection"), did, why)
    return None


def verdict(data, root, orch=True):
    name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") or {}
    if name == "ArtifactData":
        return board_verdict(ti, root, orch)
    if name.endswith("create_session") and orch:
        return launch_verdict(ti, root)
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    # a subagent of the orchestrator (board-cards, board-publish, a check) exists to keep a dump out of its context
    if data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path") or ""):
        why = subagent_verdict(data.get("tool_input") or {}, project_dir(data)) if data.get("tool_name") == "ArtifactData" else None
        if why:
            print("REFUSED: " + why, file=sys.stderr)
        return 2 if why else 0
    if (os.environ.get("KIT_ROLE") or "") == "owner":
        return 0
    orch = is_orchestrator(data)
    why = verdict(data, project_dir(data), orch)
    if not why:
        return 0
    print("REFUSED%s: " % (" for the orchestrator" if orch else " (the kit's way holds every session, CL30)") + why + " The kit does this for a fraction of the tokens (README "
          "§ Artifact mode).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
