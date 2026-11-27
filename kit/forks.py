#!/usr/bin/env python3
"""CL61: a project hook that does a kit hook's job (a fork) and how `install.py --update` folds it into the kit's.

A fork is a line in the project's .claude/settings.json that is not the kit's, on an event a kit hook serves, whose
script's name or text says it does that hook's job (hand-over, say it once, session start, stop). While it is wired,
a fix in the kit's hook never reaches the project: its fork decides (Harmonize's `context_handover.py` kept M12's
takeover case passing).

Folding: what the fork adds is read out of its source as kit settings (its context limits, its rule sentences); the
fork's line leaves settings.json and the kit's hook is wired in its place. A fork with anything the kit's hook cannot
take as a setting (code that runs commands, a test the kit does not make, a string that is no rule) is REFUSED with
what differs, and nothing of it changes. `kit.forks.keep` {path: reason} keeps one on purpose: it stays wired and every
update and `kit.py doctor` name it with that reason (never kept silently).

Used by install.py (fold at --update) and kit.py doctor (flags a shadowing hook). Standard library only."""
import ast
import json
import os
import re

# job, the kit hook doing it, the events it serves, a fork's name, words in a fork's text (both case-insensitive)
JOBS = (
    {"job": "hand-over", "kit": "kit_pre_compact.py", "events": ("Stop", "PreCompact"),
     "name": r"hand.?over|context.?(?:limit|reset)|pre.?compact",
     "text": r"context_limit|HANDOVER REQUIRED|hand.?over"},
    {"job": "say it once", "kit": "kit_say_it_once.py", "events": ("Stop", "PreToolUse"),
     "name": r"say.?it.?once", "text": r"say it once|message_chars"},
    {"job": "session start", "kit": "kit_session_start.py", "events": ("SessionStart",),
     "name": r"session.?start", "text": None},
    {"job": "stop", "kit": "kit_stop.py", "events": ("Stop",), "name": r"^stop(?:[._-]|$)", "text": None},
)

# Lines that are the kit's own (per-hook, the dispatcher, kit.py fresh, the user-level wrapper)
KIT_MARKS = ("/hooks/kit/", "/.kit/hook.py", "/.kit/hooks/", "/.kit/kit.py", "/.kit/user_hooks.py")
ROLES = ("orchestrator", "clerk")
# Words a rule sentence has: what a session must or must not do
RULE_WORDS = re.compile(r"\b(never|no|not|don't|do not|must|always|only|without|instead)\b", re.I)


def is_kit_line(cmd):
    return any(m in cmd for m in KIT_MARKS)


def script_of(cmd):
    """The project-relative path of the script a settings.json command runs, or ''."""
    c = cmd.replace("$CLAUDE_PROJECT_DIR/", "").replace("${CLAUDE_PROJECT_DIR}/", "").replace('"', " ").replace("'", " ")
    for tok in c.split():
        if re.search(r"\.(py|sh|js|mjs|rb)$", tok):
            return tok[2:] if tok.startswith("./") else tok
    return ""


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def job_of(event, rel, text):
    """The kit job a project script on `event` does, or None."""
    name = os.path.basename(rel)
    for j in JOBS:
        if event not in j["events"]:
            continue
        if re.search(j["name"], name, re.I) or (j["text"] and text and re.search(j["text"], text, re.I)):
            return j
    return None


def find(root, settings=None):
    """Every wired project hook that shadows a kit hook: [{path, job, kit, events: [..], commands: [..]}], one per script."""
    if settings is None:
        try:
            with open(os.path.join(root, ".claude", "settings.json"), encoding="utf-8") as fh:
                settings = json.load(fh)
        except (OSError, ValueError):
            return []
    out = {}
    for ev, groups in (settings.get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                cmd = str(h.get("command") or "")
                if not cmd or is_kit_line(cmd):
                    continue
                rel = script_of(cmd)
                if not rel:
                    continue
                j = job_of(ev, rel, _read(os.path.join(root, rel)))
                if not j:
                    continue
                f = out.setdefault(rel, {"path": rel, "job": j["job"], "kit": j["kit"], "events": [], "commands": []})
                if ev not in f["events"]:
                    f["events"].append(ev)
                if cmd not in f["commands"]:
                    f["commands"].append(cmd)
    return list(out.values())


# ---- folding --------------------------------------------------------------------------------------------------

def _strings_py(src):
    """String constants of a Python source that are not docstrings (what it can say), and the names it calls."""
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                docs.add(id(first.value))
    strs, calls = [], set()
    own = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            strs.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            strs.append("".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)))
        elif isinstance(node, ast.Call):
            f = node.func
            calls.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return strs, calls - own


def _sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", s) if x.strip()]


def _words(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _said_by_kit(sentence, kit_words):
    """The kit's hook already says it (most of its words are in the kit's text)."""
    w = _words(sentence)
    return bool(w) and len(w & kit_words) >= 0.8 * len(w)


# What a hand-over fork may do that the kit's hook does too (its plumbing): calls and short strings we never refuse
HANDOVER_CALLS = {"open", "load", "loads", "dumps", "dump", "print", "exit", "get", "join", "exists", "isfile", "write",
                  "read", "readline", "strip", "lower", "upper", "split", "startswith", "endswith", "int", "str", "len",
                  "sum", "max", "min", "search", "match", "findall", "sub", "compile", "format", "gettempdir", "expanduser",
                  "dirname", "basename", "abspath", "isinstance", "dict", "list", "set", "any", "all", "range", "enumerate",
                  "items", "keys", "values", "append", "extend", "setdefault", "replace", "getenv", "makedirs", "main",
                  "rstrip", "lstrip", "sorted", "reversed", "bool", "float", "tuple", "next", "iter", "close", "fromkeys",
                  "encode", "decode", "now", "isoformat", "strftime", "utcnow", "time", "remove", "unlink", "touch",
                  "Path", "exists", "is_file", "read_text", "write_text", "update", "copy", "pop", "count", "index",
                  "isdigit", "splitlines", "hexdigest", "sha256", "md5", "hash", "round", "abs", "type", "repr", "id"}
NOT_A_RULE = re.compile(r"^[\w./$%{}()\[\]:*+?|\\^-]*$")   # a key, a path, a pattern, a format: plumbing, not a message


def fold_handover(src, kit_text, lang="py"):
    """A hand-over fork read as kit settings: (settings {dotted key: value}, differs [what cannot be a setting])."""
    settings, differs = {}, []
    # its limits: "ORCH_LIMIT = 380_000", "CLERK_LIMIT_K = 80", "clerk ... 80k", "orchestrator at 300k"
    for role in ROLES:
        pat = (r"(?i)\b%s\w*\s*[=:]\s*(\d{2,3})(?:[_,]?000)?\b|\b%s\w*\b[^\n\d]{0,30}?\b(\d{2,3})\s?k\b"
               % ((role[:4], role) if role == "orchestrator" else (role, role)))
        for m in re.finditer(pat, src):
            settings["kit.%s.context_limit_k" % role] = int(m.group(1) or m.group(2))
            break
    if lang == "py":
        try:
            strs, calls = _strings_py(src)
        except SyntaxError as e:
            return settings, ["its Python does not parse (%s)" % e]
        runs = sorted(c for c in calls if c and c not in HANDOVER_CALLS)
        if runs:
            differs.append("runs code the kit's hook does not: %s()" % ", ".join(runs))
    else:   # a shell fork: only echo/printf/cat/jq/test lines are a message; anything else runs a command
        strs = re.findall(r'"([^"\n]{12,})"', src) + re.findall(r"'([^'\n]{12,})'", src)
        for line in src.splitlines():
            ln = line.strip()
            if not ln or ln.startswith("#") or ln.startswith("#!"):
                continue
            head = re.split(r"[\s;|&]", ln, 1)[0]
            if head not in ("echo", "printf", "cat", "jq", "test", "[", "[[", "if", "then", "else", "fi", "exit",
                            "set", "read", "input=$(cat)", "}", "{", "esac", "case", "do", "done", "local"):
                if not re.match(r"^\w+=", ln):
                    differs.append("runs a command the kit's hook does not: %s" % ln[:80])
    kit_words = _words(kit_text)
    rules = {r: [] for r in ROLES}
    for s in strs:
        if len(s) < 25 or NOT_A_RULE.match(s.strip()):
            continue
        role = "clerk" if re.search(r"\bclerk\b", s, re.I) and not re.search(r"\borchestrator\b", s, re.I) else "orchestrator"
        for sent in _sentences(s):
            sent = re.sub(r"(?i)^handover required[^:]*:\s*", "", sent)   # its own header: the kit says it
            if len(sent) < 20 or _said_by_kit(sent, kit_words):
                continue
            if RULE_WORDS.search(sent):
                if sent not in rules[role]:
                    rules[role].append(sent)
            else:
                differs.append("says what the kit's hook does not, and it is no rule: %r" % sent[:100])
    for role in ROLES:
        if rules[role]:
            settings["kit.%s.handover_rules" % role] = rules[role]
    return settings, differs


def fold(root, fork, kit_dir):
    """(settings, differs) for one fork; differs empty = foldable."""
    src = _read(os.path.join(root, fork["path"]))
    if not src:
        return {}, ["its script %s cannot be read" % fork["path"]]
    kit_text = _read(os.path.join(kit_dir, "kit", "hooks", fork["kit"]))
    if fork["job"] == "hand-over":
        return fold_handover(src, kit_text, "py" if fork["path"].endswith(".py") else "sh")
    return {}, ["the kit's %s (%s) takes no setting from a project's own %s hook yet: fold it by hand or keep it "
                "with kit.forks.keep" % (fork["kit"], fork["job"], fork["job"])]


def set_dotted(cfg, key, value):
    """kit.orchestrator.handover_rules -> cfg['kit']['orchestrator']['handover_rules'] (lists merged, never doubled)."""
    parts = key.split(".")
    d = cfg
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    if isinstance(value, list):
        cur = d.get(parts[-1]) or []
        d[parts[-1]] = cur + [v for v in value if v not in cur]
    else:
        d[parts[-1]] = value


def unwire(settings, fork):
    """Remove the fork's command lines from settings (empty groups and events go); returns how many."""
    n = 0
    hooks = settings.get("hooks") or {}
    for ev in list(hooks):
        keep = []
        for g in hooks[ev]:
            hs = [h for h in g.get("hooks", []) if str(h.get("command") or "") not in fork["commands"]]
            n += len(g.get("hooks", [])) - len(hs)
            if hs:
                keep.append(dict(g, hooks=hs))
        if keep:
            hooks[ev] = keep
        else:
            del hooks[ev]
    return n


def kit_hook_wired(settings, kit_hook, event):
    """The kit's hook already runs on `event` (its own line or the dispatcher's)."""
    for g in (settings.get("hooks") or {}).get(event) or []:
        for h in g.get("hooks") or []:
            c = str(h.get("command") or "")
            if kit_hook in c or ("/.kit/hook.py" in c and (" %s" % event) in c):
                return True
    return False
