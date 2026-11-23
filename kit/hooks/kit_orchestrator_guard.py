#!/usr/bin/env python3
"""Claude Code PreToolUse (ArtifactData, create_session): the orchestrator works the board through the kit.

Orchestrators 48 to 50 read the board by raw queries, wrote step posts by hand and launched parts with inline
prompts, although the kit scripts all of it for a fraction of the tokens (the owner, 2026-11-23: "we've literally
taken the time to script everything for token efficiency"). Rules in a guide were skipped; this hook refuses, for
the orchestrator only, the three gestures that bypass the kit, and says the kit's way:
  - a `list` or broad `query` of posts or comments (also an attached board's repos/<id>/...), except the kit's own
    pull (`out_dir` under .kit/run/artifact/) and a targeted lookup (a `where`, limit <= 5): use
    `kit.py artifact pull` / `load`, then `kit.py resume`, `post <id>`, `open`, `since <time>`;
  - a `set` of a step post (not a `_` card, not `_orchestrator`): plan it with TaskCreate {train, part}: the
    task-train hook writes the post and its prompt; a batch from `kit.py artifact push` (.kit/run/artifact/out) passes;
  - `create_session` with an inline prompt: a part's prompt starts with `Your prompt is docs/prompts/<id>.md` and
    stays short (<= 400 characters; a long prompt that merely names a path is still an inline prompt); only a tag
    lets another launch through (`orchestrator` a successor, `board-clerk` the clerk, `review` a review): the title
    is never read for this (CL24).
A targeted lookup pages no further: a `where` query with limit <= 5 that carries a `cursor` is a broad read.
Everyone else (parts, the clerk, the owner's own sessions) is never touched. The orchestrator's subagents are exempt
by design: they exist to keep a dump (a full list, a card rebuild) out of the orchestrator's context, so the hook
lets them read and write the board raw. Exit 2 with the reason on stderr.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit_config import config, project_dir  # noqa: E402

BOARD_COLL = re.compile(r"^(repos/[^/]+/)?(posts|comments)$")
PART_PROMPT = re.compile(r"^Your prompt is docs/prompts/[\w.-]+\.md\b")
PROMPT_MAX = 400
PASS_TAGS = ("orchestrator", "board-clerk", "review")
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


READ_WAY = ("read the board through the kit: `python3 -I .kit/kit.py artifact pull` (it prints the ArtifactData calls, "
            "the attached boards' repos/<id>/posts and comments too, "
            "saved under .kit/run/artifact/in), `python3 -I .kit/kit.py artifact load`, then `kit.py resume`, "
            "`kit.py post <id>`, `kit.py open`, `kit.py since <time>`; one document: ArtifactData `get`; a targeted "
            "lookup: `query` with a `where` and limit <= 5")
STEP_WAY = ("plan the step with TaskCreate, metadata {\"train\": ..., \"part\": ...}: the task-train hook writes its post "
            "and its prompt docs/prompts/<id>.md and answers with the launch line; local changes go to the Artifact "
            "with `python3 -I .kit/kit.py artifact push`")
LAUNCH_WAY = ("launch a part from its prompt file: TaskCreate {train, part} first (the hook writes "
              "docs/prompts/<id>.md), then create_session with the prompt \"Your prompt is docs/prompts/<id>.md: read "
              "it and do it.\" (at most %d characters, starting so; a successor orchestrator: tag `orchestrator`; a clerk: "
              "tag `board-clerk`; a review: tag `review`; the title is not read)" % PROMPT_MAX)


def verdict(data, root):
    name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") or {}
    if name == "ArtifactData":
        act = str(ti.get("action") or "")
        writes = ti.get("writes") if act == "batch" else [dict(ti, op=act)]
        if act in ("list", "query") and BOARD_COLL.match(str(ti.get("collection") or "")):
            if under(str(ti.get("out_dir") or ""), root, ".kit", "run", "artifact"):
                return None
            q = ti.get("query") or {}
            try:
                lim = int(q.get("limit") or 100)
            except (TypeError, ValueError):
                lim = 100
            if act == "query" and q.get("where") and lim <= 5 and not q.get("cursor"):
                return None
            return "a raw %s of %s: %s." % (act, ti.get("collection"), READ_WAY)
        for w in writes or []:
            if not isinstance(w, dict) or w.get("op") != "set":
                continue
            m = BOARD_COLL.match(str(w.get("collection") or ""))
            if not m or m.group(2) != "posts" or str(w.get("doc_id") or "").startswith("_"):
                continue
            if under(str(w.get("file_path") or ""), root, ".kit", "run", "artifact", "out"):
                continue
            return "a step post written by hand (%s/%s): %s." % (w.get("collection"), w.get("doc_id"), STEP_WAY)
        return None
    if name.endswith("create_session"):
        prompt = str(ti.get("prompt") or "").strip()
        tags = [str(t) for t in (ti.get("tags") or [])]
        if any(t in PASS_TAGS for t in tags):
            return None
        if PART_PROMPT.match(prompt) and len(prompt) <= PROMPT_MAX:
            return None
        return "a part launched with an inline prompt: %s." % LAUNCH_WAY
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    # a subagent of the orchestrator (board-cards, board-publish, a check) exists to keep a dump out of its context
    if data.get("agent_id") or data.get("agent_type") or "/subagents/" in str(data.get("transcript_path") or ""):
        return 0
    if not is_orchestrator(data):
        return 0
    why = verdict(data, project_dir(data))
    if not why:
        return 0
    print("REFUSED for the orchestrator: " + why + " The kit does this for a fraction of the tokens (README "
          "§ Artifact mode).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
