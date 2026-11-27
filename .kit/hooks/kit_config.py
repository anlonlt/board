"""The kit's config, read by every hook: resolved from agy stdin (`cwd` / `workspacePaths`),
$KIT_PROJECT_DIR / $CLAUDE_PROJECT_DIR, parent of .agents/, or current directory."""
import json
import os


def project_dir(data=None):
    if isinstance(data, dict):
        cwd = data.get("cwd")
        if cwd and os.path.isdir(cwd):
            # the session's shell may have moved into a subfolder (`cd src`): the project is the folder above it that holds .kit
            # (2026-11-25: a hook wrote src/.kit/run/..., and the part spent turns removing it)
            cur = os.path.abspath(cwd)
            for _ in range(8):
                if os.path.isdir(os.path.join(cur, ".kit")) or os.path.exists(os.path.join(cur, ".git")):
                    return cur
                up = os.path.dirname(cur)
                if up == cur:
                    break
                cur = up
            return cwd
        wps = data.get("workspacePaths") or []
        if wps and isinstance(wps[0], str) and os.path.isdir(wps[0]):
            return wps[0]
    env = os.environ.get("KIT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return env
    here = os.getcwd()
    # agy / claude may run hooks from inside .agents/ or .claude/
    cur = here
    for _ in range(4):
        if os.path.isdir(os.path.join(cur, ".kit")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return here


def config(data=None):
    p = os.path.join(project_dir(data), ".kit", "config.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def limit(name, default, data=None):
    return int(config(data).get("kit", {}).get("limits", {}).get(name, default))


def in_setup(data=None):
    """README steps 1-8: install.py sets kit.setup at the first install, --setup-done clears it."""
    return bool(config(data).get("kit", {}).get("setup"))


def is_claude_payload(data=None):
    """True when stdin payload came from Claude Code (or runtime is claude without agy fields)."""
    if isinstance(data, dict):
        if "hook_event_name" in data or "tool_input" in data or "stop_hook_active" in data or "prompt" in data:
            return True
        if ("hookEventName" in data or "toolCall" in data or "lastUserInput" in data or "terminationReason" in data
                or "conversationId" in data or "conversation_id" in data or "workspacePaths" in data):
            return False
    if os.environ.get("CLAUDE_PROJECT_DIR") and not os.environ.get("KIT_PROJECT_DIR"):
        return True
    return config(data).get("kit", {}).get("runtime") == "claude"


def memory_dir(data=None):
    """Memory directory (configurable via kit.memory_dir to avoid Storybloq .story/ collisions)."""
    root = project_dir(data)
    md = config(data).get("kit", {}).get("memory_dir")
    if md:
        return os.path.join(root, md)
    if os.path.isdir(os.path.join(root, ".story")):
        return os.path.join(root, ".story")
    return os.path.join(root, ".board", "memory")


# ---------------------------------------------------------------- CL72: one way to touch the board
# The owner, 2026-11-27: "The commands are there for a reason. No session may do anything by hand." Three kit pieces used
# to disagree (the guard refused two gestures, `artifact push`/`pin` told sessions to make calls by hand, the audit
# counted the kit's own calls as manual). One list now, here, read by all three:
#   - the kit RECORDS each ArtifactData call it prints, exactly (`ledger_record`, .kit/run/artifact/printed.json);
#   - the guard (kit_orchestrator_guard.py) lets a board call through only when it is one of those, unused
#     (`ledger_match`), or a call with no kit form (`free_call`), for every role;
#   - the audit (usage.py) counts a call as the kit's by the same rules (`kit_printed`, `free_call`).
import hashlib as _hashlib
import re as _re
import time as _time

BOARD_COLLS = _re.compile(r"^(?:repos/[^/]+/)?(posts|comments|kit_docs|kit_repos|activity)$")
# what has no kit command on the Artifact: the typing dots, the clerk's marks on a comment, a new orchestrator's
# session on _orchestrator (each a field-level `update` or a `set` of typing/<id>)
MARK_FIELDS = frozenset({"read", "read_at", "seen", "seen_at", "waiting_note", "acked", "acked_at"})
ORCH_FIELDS = frozenset({"session_id", "started_by", "started_at"})
FREE_COLLS = _re.compile(r"^(?:repos/[^/]+/)?typing$")
# the kit commands whose output carries the ArtifactData calls to make (usage.py's command keys)
PRINTERS = ("kit.py artifact", "web_takeover.py", "kit.py launch", "kit.py step", "kit.py done", "end_session.py")
LOCAL_WRITE_WAY = ("your post: `kit.py form|sub|checkpoint <post>`, a step: TaskCreate {train, part}; an answer: "
                   "`python3 -I .kit/board_cli.py reply <post> --text ...`; a comment: `board_cli.py set comments <id> "
                   "--data ...`")
LEDGER = "printed.json"
LEDGER_V = 3
LEDGER_FRESH_S = 60 * 60
SYNC_WAY = ("`python3 -I .kit/kit.py artifact sync`: it prints the one set of ArtifactData calls to make, exactly; "
            "make them as printed, then run it again until it says \"in sync\"")


def _sha(path):
    try:
        with open(path, "rb") as fh:
            return _hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _real(p):
    return os.path.realpath(str(p)) if p else ""


def call_writes(ti):
    """The writes of an ArtifactData call: a batch's `writes`, or the call itself as one write."""
    act = str(ti.get("action") or "")
    if act == "batch":
        return [w for w in (ti.get("writes") or []) if isinstance(w, dict)]
    if act in ("set", "update", "str_replace", "delete"):
        return [dict(ti, op=act)]
    return []


def board_call(ti):
    """True when this ArtifactData call reads or writes the board (posts, comments, the files' map, the attached boards,
    the activity line), whatever the artifact: the guard decides those, any other database passes."""
    colls = [str(w.get("collection") or "") for w in call_writes(ti)] or [str(ti.get("collection") or "")]
    return any(BOARD_COLLS.match(c) for c in colls)


def free_call(ti):
    """A board write with no kit form (the list above): every write of the call is one."""
    ws = call_writes(ti)
    if not ws:
        return False
    for w in ws:
        coll, did = str(w.get("collection") or ""), str(w.get("doc_id") or "")
        op = str(w.get("op") or w.get("action") or "")
        keys = set(w["data"]) if isinstance(w.get("data"), dict) else None
        m = BOARD_COLLS.match(coll)
        if FREE_COLLS.match(coll):
            continue
        if m and op == "update" and keys and m.group(1) == "comments" and keys <= MARK_FIELDS:
            continue
        if m and op == "update" and keys and m.group(1) == "posts" and did == "_orchestrator" and keys <= ORCH_FIELDS:
            continue
        if not m:
            continue
        return False
    return True


def _norm_query(q):
    q = q if isinstance(q, dict) else {}
    return {"where": q.get("where") or None, "limit": q.get("limit"), "order_by": q.get("order_by") or None}


def _norm_write(w):
    return {"op": str(w.get("op") or w.get("action") or ""), "collection": str(w.get("collection") or ""),
            "doc_id": str(w.get("doc_id") or ""), "if_version": w.get("if_version"),
            "file_path": _real(w.get("file_path")), "data": w.get("data"),
            "field": w.get("field"), "old_str": w.get("old_str"), "new_str": w.get("new_str")}


def call_shape(ti, url=None):
    """An ArtifactData call reduced to what must be exactly as printed: the action, the collection, the document, the
    query, the out_dir, and every write (with each file's content hash, so an edited file is not the printed call)."""
    act = str(ti.get("action") or "")
    out = {"action": act, "url": str(ti.get("url") or url or "")}
    if act in ("get", "list", "query"):
        out.update(collection=str(ti.get("collection") or ""), doc_id=str(ti.get("doc_id") or ""),
                   query=_norm_query(ti.get("query")), out_dir=_real(ti.get("out_dir")))
    else:
        ws = [_norm_write(w) for w in call_writes(ti)]
        out["writes"] = ws
        out["shas"] = [_sha(w["file_path"]) if w["file_path"] else None for w in ws]
    return out


def ledger_path(base):
    return os.path.join(base, LEDGER)


def _ledger_read(base):
    try:
        with open(ledger_path(base), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("v") == LEDGER_V else None


def ledger_record(base, calls, by, url="", step=""):
    """The kit printed these calls (ArtifactData tool inputs), and only these may go out now: the last print replaces
    the one before (one call set at a time)."""
    rec = {"v": LEDGER_V, "at": _time.time(), "by": by, "step": step, "url": url,
           "calls": [{"shape": call_shape(c, url), "input": c, "used": None} for c in calls]}
    os.makedirs(base, exist_ok=True)
    tmp = ledger_path(base) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, ledger_path(base))
    return rec


def ledger_calls(base):
    """[(call input, used)] of the set the kit printed last ([] when none or stale)."""
    rec = _ledger_read(base)
    if not rec or _time.time() - float(rec.get("at") or 0) > LEDGER_FRESH_S:
        return []
    return [(c.get("input") or {}, c.get("used")) for c in rec.get("calls") or []]


def _same(shape, printed):
    a, b = dict(shape), dict(printed)
    if not b.get("url") or not a.get("url"):
        a.pop("url", None), b.pop("url", None)
    if a.get("action") in ("list", "query"):
        qa, qb = dict(a.get("query") or {}), dict(b.get("query") or {})
        a["query"], b["query"] = qa, qb
    return a == b


def ledger_match(base, ti, consume=True):
    """None when this call is one the kit printed and has not been made yet (marked made when `consume`); else why not.
    The next pages of a printed `list` (its `query.cursor`) pass after the first page."""
    rec = _ledger_read(base)
    if not rec:
        return "the kit printed no ArtifactData call here"
    if _time.time() - float(rec.get("at") or 0) > LEDGER_FRESH_S:
        return "the calls the kit printed are over %d minutes old" % (LEDGER_FRESH_S // 60)
    q = ti.get("query") if isinstance(ti.get("query"), dict) else {}
    cursor = q.get("cursor")
    shape = call_shape(dict(ti, query=dict(q, cursor=None)) if cursor else ti, rec.get("url"))
    shape.pop("url", None) if not rec.get("url") else None
    near = None
    for c in rec.get("calls") or []:
        pr = c.get("shape") or {}
        if not _same(shape, pr):
            if pr.get("action") == shape.get("action") and (pr.get("collection") == shape.get("collection") or
                                                            pr.get("writes") and shape.get("writes")):
                near = pr
            continue
        if cursor:
            if c.get("used") and shape.get("action") == "list":
                return None
            continue
        if c.get("used"):
            return "that call was made already (each printed call goes out once)"
        if consume:
            c["used"] = _time.time()
            try:
                with open(ledger_path(base), "w", encoding="utf-8") as fh:
                    json.dump(rec, fh, ensure_ascii=False, indent=1)
            except OSError:
                pass
        return None
    if near is not None and shape.get("writes") is not None:
        return "not the batch the kit printed (a write, a version or a file differs from what it built)"
    return "not a call the kit printed (the last print was `%s`)" % (rec.get("by") or "kit.py")


def kit_printed(last_key, last_note=""):
    """The audit's side of the list: an ArtifactData call is the kit's when the last command before it (the
    ArtifactData calls of the same set aside) was one of the kit's PRINTERS, or a command line that ended in one."""
    last_key, last_note = str(last_key or ""), str(last_note or "")
    return last_key.startswith(PRINTERS) or last_note.startswith("artifact ")


SEEN = "seen.json"
_SAVED = _re.compile(r'"([^"]+)"\s+\d+\s+bytes\s+version\s+(\d+)\s+"([^"]+)"')
_VERSION_OF = _re.compile(r"\bversion\s+(\d+)")
_REFUSED = _re.compile(r"version_mismatch|batch write failed|writes? nothing|nothing (?:was )?written|not written|refused|conflict|has changed|"
                       r"changed since|version mismatch|current version|no pin|carries no", _re.I)


def _resp_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(_resp_text(x) for x in resp)
    if isinstance(resp, dict):
        for k in ("text", "content", "output", "result", "stdout"):
            if k in resp:
                return _resp_text(resp[k])
        return json.dumps(resp, ensure_ascii=False)
    return str(resp or "")


def seen_read(base):
    try:
        with open(os.path.join(base, SEEN), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    d.setdefault("versions", {})
    d.setdefault("batches", {})
    return d


def seen_record(base, ti, resp, error=False):
    """PostToolUse ArtifactData: what the reply said, so the kit never asks the model to copy a number: each saved
    document's version (`get`/`list`/`query` with `out_dir`), each write's new version, and whether a batch went in.
    {"versions": {"<collection>/<doc_id>": N}, "batches": {"<first file_path>": {"ok": bool, "text": ...}}}."""
    text = _resp_text(resp)
    d = seen_read(base)
    act = str(ti.get("action") or "")
    if act in ("get", "list", "query"):
        for did, ver, path in _SAVED.findall(text):
            out = _real(ti.get("out_dir"))
            rel = os.path.relpath(_real(path), out) if out else ""
            coll = os.path.dirname(rel).replace(os.sep, "/") if rel and not rel.startswith("..") else str(
                ti.get("collection") or "")
            d["versions"]["%s/%s" % (coll, did)] = {"version": int(ver), "path": _real(path), "at": _time.time()}
    elif act == "batch":
        ws = call_writes(ti)
        key = _real(ws[0].get("file_path")) if ws and ws[0].get("file_path") else "inline"
        ok = not error and not _REFUSED.search(text)
        d["batches"][key] = {"ok": ok, "text": text[:2000], "at": _time.time(),
                             "docs": ["%s/%s" % (w.get("collection"), w.get("doc_id")) for w in ws]}
    elif act in ("set", "update", "str_replace", "delete"):
        m = _VERSION_OF.search(text)
        if m and not error and not _REFUSED.search(text):
            d["versions"]["%s/%s" % (ti.get("collection"), ti.get("doc_id"))] = {"version": int(m.group(1)),
                                                                              "at": _time.time()}
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, SEEN), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    return d


# a raw read of a post on the local copy (the kit reads it for less: `kit.py post <id>`, `kit.py train --full <part>`):
# the guard refuses it on an Artifact board, the audit counts it (cli-get-post): one rule for both
CLI_RAW_READ = _re.compile(r"board_cli\.py\s+(?:--?[\w-]+(?:[ =]\S+)?\s+)*(get|list|query)\s+(?:repos/[^/\s]+/)?posts\b")
CLI_READ_WAY = ("`python3 -I .kit/kit.py post <id>` (one post), `kit.py train <train> --sections|--full <part>`, "
                "`kit.py resume`, `kit.py since <time>`: they read the loaded copy for a fraction of the tokens")


def cli_raw_read(command):
    """The raw post read in a shell command (`board_cli.py get|list|query posts ...`), or None."""
    m = CLI_RAW_READ.search(str(command or ""))
    return m.group(0) if m else None


def free_note(op, coll, did=""):
    """The audit's view of free_call, from what a usage row keeps (the operation and the document, not the fields):
    the typing dots, an `update` of a comment (the clerk's marks) or of _orchestrator (a new session)."""
    coll = str(coll or "")
    if FREE_COLLS.match(coll):
        return True
    m = BOARD_COLLS.match(coll)
    return bool(m and op == "update" and (m.group(1) == "comments" or (m.group(1) == "posts" and did == "_orchestrator")))
