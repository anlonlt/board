#!/usr/bin/env python3
"""kit_map.py: a map of any project, generated from its code, never written by hand (M1).

`kit.py map [--root DIR] [--out DIR] [--config FILE] [--check]` writes, into DIR/.board/map/ by default:
  overview.md   one screen (60 lines at most): the areas, what each is for (one line taken from code), size, entry
                points, the features that cross areas, and the flags at the joins between layers
  areas/*.md    one card per area (and per feature): files with line counts, exports, imports in and out, tests
  joins.md      for each route or action: fields the UI sends, fields the schema accepts, handler, tables, tests
  map.json      the same facts as things, links and flags; every link carries a check that `--check` reruns

Why (the owner's French repo, 2026-11-25): 7,100 lines of hand-written docs about the game, ordered by date and
partly stale, while the three serious bugs sat at the joins between layers, where no doc looked: a field the UI sends
that the schema drops, a projection that sends what no screen reads, a write route with no CSRF check.

Readers are plug-ins (READERS): code is the first (Python by ast, TypeScript/JavaScript by a light lexer); others
(documents, dashboards) add things and links of their own later. Nothing is installed in the project, nothing is
written in it except the out folder.
"""
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time

MAX_OVERVIEW = 60          # lines: one screen
BIG_FILE = 1500            # lines: a file too big to read whole
MAX_PARSE = 400_000        # characters: above, a bundle or minified file, counted but not parsed
PY_EXT = (".py",)
TS_EXT = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
CODE_EXT = PY_EXT + TS_EXT
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", ".next", "out", "coverage", "venv", ".venv",
             "vendor", "target", "site-packages"}
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
HTTP_METHODS = ("GET", "HEAD", "OPTIONS") + WRITE_METHODS
# a write route's guards, by the name of what it calls; a project names its own in config map.csrf / map.auth
CSRF_RE = r"\b\w*(?:csrf|xsrf|origin|origine|samesite)\w*\s*\("
AUTH_RE = (r"\b\w*(?:auth|session|user|utilisateur|connecte|login|token|jwt|role|permission|enseignant|admin|"
           r"current|courant|requireuser|getuser)\w*\s*\(")
PUBLIC_RE = r"(login|logout|signin|signup|sign-in|sign-up|register|connexion|deconnexion|inscription|password|mdp|health|webhook|cron)"
STOP_SEGMENTS = {"app", "api", "src", "lib", "libs", "test", "tests", "spec", "specs", "unit", "e2e", "index", "page",
                 "route", "layout", "components", "component", "utils", "util", "scripts", "script", "main", "types",
                 "type", "public", "static", "assets", "fixtures", "helpers", "common", "core", "shared", "config",
                 "init", "server", "client", "ui", "id", "slug", "outils", "kit", "mod", "the", "de", "la", "le", "et"}


# ── small helpers ──────────────────────────────────────────────────────────────────────────────────────────────

def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _sha(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _is_test(path):
    p = "/" + path.replace("\\", "/").lower()
    base = os.path.basename(p)
    return ("/tests/" in p or "/test/" in p or "/__tests__/" in p or base.startswith("test_") or
            re.search(r"[._-](test|spec)\.[a-z]+$", base) is not None or base.endswith("_test.py") or
            base == "conftest.py")


def _is_ui(path):
    """A file a person's screen runs: .tsx/.jsx outside API routes, or under components/pages."""
    p = path.replace("\\", "/")
    if _is_test(p) or re.search(r"(^|/)route\.[a-z]+$", p) or "/api/" in "/" + p:
        return False
    return p.endswith((".tsx", ".jsx", ".vue", ".svelte")) or p.startswith(("components/", "src/components/"))


def list_files(root, out_rel=None):
    """The project's code files, relative to root: git's list when it is a repo, else a walk. Dot folders
    (tooling, memory) and dependency folders are not the project's code."""
    listed = _git(root, "ls-files", "-co", "--exclude-standard")
    if listed is not None:
        files = [f for f in listed.splitlines() if f]
    else:
        files = []
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            for n in names:
                files.append(os.path.relpath(os.path.join(d, n), root))
    out = []
    for f in files:
        f = f.replace("\\", "/")
        parts = f.split("/")
        if any(p.startswith(".") or p in SKIP_DIRS for p in parts[:-1]):
            continue
        if out_rel and (f == out_rel or f.startswith(out_rel.rstrip("/") + "/")):
            continue
        if f.endswith(CODE_EXT) and not f.endswith(".d.ts") and os.path.isfile(os.path.join(root, f)):
            out.append(f)
    return sorted(set(out))


# ── TypeScript / JavaScript: a light lexer, no parser to install ───────────────────────────────────────────────

_TOK = re.compile(r"""//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`"""
                  r"""|(?:(?<=[(,=:\[!&|?{};])|(?<=return))[ \t]*/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/""")


def strip_ts(src):
    """Comments blanked (newlines kept, so line numbers hold); strings, template and regex literals kept."""
    def blank(m):
        t = m.group(0)
        return re.sub(r"[^\n]", " ", t) if t[:2] in ("//", "/*") else t
    return _TOK.sub(blank, src)


def _match(s, i, open_c="(", close_c=")"):
    """Index just past the bracket that closes s[i] (s[i] == open_c); strings skipped."""
    depth, n = 0, len(s)
    pairs = {"(": ")", "[": "]", "{": "}"}
    while i < n:
        c = s[i]
        if c in "\"'`":
            j = i + 1
            while j < n and s[j] != c:
                if s[j] == "\\":
                    j += 1
                j += 1
            i = j + 1; continue
        if c in pairs:
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _line(s, i):
    return s.count("\n", 0, i) + 1


def _top_keys(body):
    """Keys of an object literal's body at depth 0: `a: 1`, `"b": 2`, shorthand `c`, `...(x ? {d: 1} : {})`."""
    keys, spread_keys = [], []
    depth, i, n, start = 0, 0, len(body), 0
    items = []
    while i < n:
        c = body[i]
        if c in "\"'`":
            j = i + 1
            while j < n and body[j] != c:
                if body[j] == "\\":
                    j += 1
                j += 1
            i = j + 1; continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(body[start:i]); start = i + 1
        i += 1
    items.append(body[start:])
    for it in items:
        t = it.strip()
        if not t:
            continue
        if t.startswith("..."):
            j = 3
            while j < len(t):            # the outer object literals of `...(c ? {a} : {b})`, not those nested in them
                if t[j] == "{":
                    e = _match(t, j, "{", "}")
                    spread_keys.extend(k for k, _ in _top_keys(t[j + 1:e - 1]) if k)
                    j = e
                else:
                    j += 1
            continue
        m = re.match(r"""(?:async\s+)?(?:get\s+|set\s+)?(?:["']([^"']+)["']|([A-Za-z_$][\w$]*))\s*(\??\s*:|\(|$)""", t)
        if m:
            keys.append((m.group(1) or m.group(2), t[m.end():].strip() if m.group(3).strip().endswith(":") else ""))
    return keys + [(k, "...") for k in spread_keys]


def _zod_schema(s, i):
    """The Zod schema starting at s[i] (`z.object(`...): {"fields": {name: sub|None}, "union": ..., "mode": ...}."""
    m = re.compile(r"z\s*\.\s*(object|strictObject|looseObject|discriminatedUnion|array|union)\s*\(").match(s, i)
    if not m:
        return None, i
    kind = m.group(1)
    p_open = m.end() - 1
    p_end = _match(s, p_open)
    inner = s[p_open + 1:p_end - 1]
    sch = {"fields": {}, "mode": "strict" if kind == "strictObject" else "passthrough" if kind == "looseObject" else "strip"}
    if kind in ("object", "strictObject", "looseObject"):
        b = inner.strip()
        if b.startswith("{"):
            body = b[1:_match(b, 0, "{", "}") - 1]
            for k, val in _top_keys(body):
                sub, _ = _zod_schema(val, 0) if val.startswith("z") else (None, 0)
                sch["fields"][k] = sub
    elif kind == "discriminatedUnion":
        km = re.match(r"""\s*["']([^"']+)["']\s*,\s*\[""", inner)
        if km:
            arr_start = km.end() - 1
            arr = inner[arr_start + 1:_match(inner, arr_start, "[", "]") - 1]
            members = {}
            for zm in re.finditer(r"z\s*\.\s*(?:object|strictObject)\s*\(", arr):
                if any(a <= zm.start() < b for a, b in members.get("__spans", [])):
                    continue
                sub, end = _zod_schema(arr, zm.start())
                members.setdefault("__spans", []).append((zm.start(), end))
                lit = re.search(r"""%s\s*:\s*z\s*\.\s*literal\(\s*["']([^"']+)["']""" % re.escape(km.group(1)), arr[zm.start():end])
                if sub and lit:
                    members[lit.group(1)] = sub
            members.pop("__spans", None)
            sch = {"fields": {}, "union": {"key": km.group(1), "members": members}, "mode": "strip"}
    elif kind == "array":
        sub, _ = _zod_schema(inner.strip(), 0)
        sch = {"fields": {}, "items": sub, "mode": "strip"}
    else:
        return None, p_end
    # chained calls: .extend({...}) adds keys, .strict()/.passthrough() change what an unknown key does
    j = p_end
    while True:
        cm = re.compile(r"\s*\.\s*(\w+)\s*\(").match(s, j)
        if not cm:
            break
        name, po = cm.group(1), cm.end() - 1
        pe = _match(s, po)
        if name == "extend":
            b = s[po + 1:pe - 1].strip()
            if b.startswith("{"):
                for k, val in _top_keys(b[1:_match(b, 0, "{", "}") - 1]):
                    sch["fields"][k] = _zod_schema(val, 0)[0] if val.startswith("z") else None
        elif name in ("strict", "passthrough", "strip"):
            sch["mode"] = name if name != "strip" else "strip"
        j = pe
    return sch, j


def read_ts(path, src):
    s = strip_ts(src)
    f = {"lang": "ts", "imports": [], "exports": [], "zod": {}, "tables": {}, "routes": [], "calls": [],
         "objects": [], "types": {}, "parses": [], "doc": _ts_doc(src), "reexports": [], "funcs": {}}
    for m in re.finditer(r"""(?:^|[;\n}])\s*(import|export)\s+(type\s+)?([\w*{}\s,$]*?)\s*from\s*["']([^"']+)["']""", s):
        names = re.findall(r"[\w$]+(?:\s+as\s+[\w$]+)?", m.group(3).replace("type ", ""))
        names = [n.split()[0] for n in names if n not in ("as",)]
        f["imports"].append({"from": m.group(4), "names": names, "line": _line(s, m.start(4)), "type": bool(m.group(2))})
        if m.group(1) == "export":
            f["reexports"].append({"from": m.group(4), "names": names, "star": "*" in m.group(3)})
    for m in re.finditer(r"""(?:^|[;\n])\s*import\s*["']([^"']+)["']|(?:require|import)\s*\(\s*["']([^"']+)["']\s*\)""", s):
        f["imports"].append({"from": m.group(1) or m.group(2), "names": [], "line": _line(s, m.start()), "type": False})
    for m in re.finditer(r"(?:^|\n)\s*export\s+(default\s+)?(?:declare\s+)?(async\s+)?(function\*?|const|let|var|class|"
                         r"type|interface|enum|abstract\s+class)\s+([\w$]+)", s):
        f["exports"].append(m.group(4))
    for m in re.finditer(r"(?:^|\n)\s*export\s+default\b", s):
        if "default" not in f["exports"]:
            f["exports"].append("default")
    for m in re.finditer(r"(?:^|\n)\s*export\s*\{([^}]*)\}(?!\s*from)", s):
        for n in m.group(1).split(","):
            n = n.strip().split(" as ")[-1].strip()
            if n:
                f["exports"].append(n)
    # functions with their bodies (the handler of a route method is one)
    for m in re.finditer(r"(?:^|\n)\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([\w$]+)\s*(?:<[^>]*>)?\s*\(", s):
        pe = _match(s, m.end() - 1)
        b = s.find("{", pe)
        if b >= 0:
            f["funcs"][m.group(1)] = (b, _match(s, b, "{", "}"))
    for m in re.finditer(r"(?:^|\n)\s*(?:export\s+)?const\s+([\w$]+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*(?::[^=]+)?=>\s*\{", s):
        b = m.end() - 1
        f["funcs"].setdefault(m.group(1), (b, _match(s, b, "{", "}")))
    # Zod schemas declared as consts
    for m in re.finditer(r"(?:^|\n)\s*(?:export\s+)?const\s+([\w$]+)\s*(?::[^=]+)?=\s*(?=z\s*\.)", s):
        sch, _ = _zod_schema(s, m.end())
        if sch:
            sch["line"] = _line(s, m.start(1))
            f["zod"][m.group(1)] = sch
    for m in re.finditer(r"\b([\w$]+)\s*\.\s*(safeParse|parse|safeParseAsync|parseAsync)\s*\(", s):
        f["parses"].append(m.group(1))
    # Drizzle tables
    for m in re.finditer(r"(?:^|\n)\s*export\s+const\s+([\w$]+)\s*=\s*(pg|sqlite|mysql)Table\s*\(\s*[\"']([^\"']+)[\"']\s*,\s*\{", s):
        b = m.end() - 1
        body = s[b + 1:_match(s, b, "{", "}") - 1]
        f["tables"][m.group(1)] = {"sql": m.group(3), "columns": [k for k, _ in _top_keys(body)], "line": _line(s, m.start(1))}
    # Next routes: app/**/route.ts and their methods
    rp = next_route_path(path)
    if rp is not None:
        for mth in HTTP_METHODS:
            if re.search(r"(?:^|\n)\s*export\s+(?:async\s+)?(?:function\s+%s\b|const\s+%s\s*=)" % (mth, mth), s):
                f["routes"].append({"method": mth, "path": rp})
    # calls to an API: a "/api/..." string, with its method and body when it sits in a fetch(...)
    for m in re.finditer(r"""(["'`])(/api/[^"'`\s?#]*)""", s):
        url = m.group(2)
        call = {"url": url, "line": _line(s, m.start()), "method": None, "body": None, "raw_at": m.start()}
        fm = s.rfind("fetch", max(0, m.start() - 60), m.start())
        if fm >= 0 and re.match(r"fetch\s*\(\s*$", s[fm:m.start()]):
            pe = _match(s, s.find("(", fm))
            args = s[m.start():pe]
            mm = re.search(r"""method\s*:\s*["'`](\w+)""", args)
            call["method"] = (mm.group(1).upper() if mm else "GET")
            bm = re.search(r"body\s*:\s*JSON\.stringify\s*\(", args)
            if bm:
                bo = bm.end() - 1
                expr = args[bo + 1:_match(args, bo) - 1].strip()
                if expr.startswith("{"):
                    call["body"] = [k for k, _ in _top_keys(expr[1:_match(expr, 0, "{", "}") - 1])]
                elif re.fullmatch(r"[\w$]+", expr):
                    dm = re.search(r"(?:const|let|var)\s+%s\s*(?::[^=]+)?=\s*\{" % re.escape(expr), s)
                    if dm:
                        b = dm.end() - 1
                        call["body"] = [k for k, _ in _top_keys(s[b + 1:_match(s, b, "{", "}") - 1])]
        f["calls"].append(call)
    # object literals that carry a discriminant (`type: "x"`), for unions nested in a body
    for m in re.finditer(r"""\{\s*(?:\.\.\.[^,]+,\s*)?([\w$]+)\s*:\s*["']([\w-]+)["']""", s):
        b = m.start()
        body = s[b + 1:_match(s, b, "{", "}") - 1]
        f["objects"].append({"key": m.group(1), "value": m.group(2), "keys": [k for k, _ in _top_keys(body)],
                             "line": _line(s, b)})
    # declared shapes (type/interface) and the names this file reads, for "received, never read"
    for m in re.finditer(r"(?:^|\n)\s*(?:export\s+)?(?:type\s+([\w$]+)\s*(?:<[^>]*>)?\s*=\s*\{|interface\s+([\w$]+)[^{]*\{)", s):
        b = m.end() - 1
        body = s[b + 1:_match(s, b, "{", "}") - 1]
        f["types"][m.group(1) or m.group(2)] = {"fields": [k for k, _ in _top_keys(body.replace(";", ","))],
                                               "line": _line(s, m.start()), "span": (b, b + len(body) + 2)}
    f["_stripped"] = s
    return f


def _ts_doc(src):
    """What a TS/JS file is for: the first sentence of its leading comment."""
    m = re.match(r"\s*(?:[\"']use (?:client|server)[\"'];?\s*)?(?:/\*+([\s\S]*?)\*/|((?:\s*//[^\n]*\n)+))", src)
    if not m:
        return ""
    text = m.group(1) if m.group(1) is not None else re.sub(r"^\s*//", "", m.group(2), flags=re.M)
    text = re.sub(r"^\s*\*\s?", "", text, flags=re.M)
    return _first_sentence(text)


def _first_sentence(text):
    text = " ".join(text.split())
    text = re.sub(r"^(eslint-disable\S*|@\w+)\s*", "", text)
    m = re.match(r"(.{20,}?[.!?:])(\s|$)", text)
    out = (m.group(1) if m else text).strip()
    return out[:117] + "..." if len(out) > 120 else out


def next_route_path(path):
    """`app/api/jeu/[id]/etat/route.ts` -> `/api/jeu/[id]/etat`; None when it is not a Next route file."""
    p = path.replace("\\", "/")
    m = re.match(r"^(?:src/)?app/(.*?)/?route\.(?:ts|js|tsx|jsx|mts|mjs)$", p)
    if not m:
        return None
    segs = [x for x in m.group(1).split("/") if x and not (x.startswith("(") and x.endswith(")")) and not x.startswith("@")]
    return "/" + "/".join(segs)


def next_page_path(path):
    p = path.replace("\\", "/")
    m = re.match(r"^(?:src/)?app/(.*?)/?page\.(?:tsx|jsx|ts|js|mdx)$", p)
    if not m:
        return None
    segs = [x for x in m.group(1).split("/") if x and not (x.startswith("(") and x.endswith(")")) and not x.startswith("@")]
    return "/" + "/".join(segs)


# ── Python: ast ────────────────────────────────────────────────────────────────────────────────────────────────

def read_py(path, src):
    f = {"lang": "py", "imports": [], "exports": [], "zod": {}, "tables": {}, "routes": [], "calls": [], "objects": [],
         "types": {}, "parses": [], "doc": "", "reexports": [], "funcs": {}, "main": False}
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        f["error"] = "does not parse"
        return f
    f["doc"] = _first_sentence(ast.get_docstring(tree) or "")
    pkg = os.path.dirname(path).replace("/", ".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                f["imports"].append({"from": a.name, "names": [], "line": node.lineno, "type": False})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                base = pkg.split(".") if pkg else []
                base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
                mod = ".".join([x for x in base if x] + ([mod] if mod else []))
                f["imports"].append({"from": mod, "names": [a.name for a in node.names], "line": node.lineno,
                                     "type": False, "relative": True})
            else:
                f["imports"].append({"from": mod, "names": [a.name for a in node.names], "line": node.lineno, "type": False})
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                f["exports"].append(node.name + ("()" if not isinstance(node, ast.ClassDef) else ""))
            for d in getattr(node, "decorator_list", []):
                # Flask / FastAPI: @app.route("/x", methods=[...]), @router.post("/x")
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args and \
                        isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                    verb = d.func.attr.upper()
                    if verb == "ROUTE":
                        ms = ["GET"]
                        for kw in d.keywords:
                            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                                ms = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
                        for mth in ms:
                            f["routes"].append({"method": mth, "path": d.args[0].value, "handler": node.name})
                    elif verb in HTTP_METHODS:
                        f["routes"].append({"method": verb, "path": d.args[0].value, "handler": node.name})
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and \
                isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            f["main"] = True
    return f


# ── readers: code is the first; others plug in with the same shape ─────────────────────────────────────────────

def _code_reader(root, files):
    facts = {}
    for p in files:
        src = _read(os.path.join(root, p))
        if len(src) > MAX_PARSE:   # a bundle or a minified mock-up: counted, not read
            f = {"lang": "py" if p.endswith(PY_EXT) else "ts", "imports": [], "exports": [], "zod": {}, "tables": {},
                 "routes": [], "calls": [], "objects": [], "types": {}, "parses": [], "doc": "(not read: %d KB)" % (len(src) // 1024),
                 "reexports": [], "funcs": {}, "_stripped": ""}
        else:
            f = read_py(p, src) if p.endswith(PY_EXT) else read_ts(p, src)
        f["lines"] = src.count("\n") + (1 if src and not src.endswith("\n") else 0)
        f["sha"] = _sha(src)
        st = os.stat(os.path.join(root, p))
        f["stat"] = [st.st_size, st.st_mtime_ns]
        facts[p] = f
    return facts


READERS = {"code": _code_reader}


# ── resolution: an import to a file ────────────────────────────────────────────────────────────────────────────

def _tsconfig_paths(root):
    for name in ("tsconfig.json", "jsconfig.json"):
        txt = _read(os.path.join(root, name))
        if not txt:
            continue
        txt = re.sub(r"//[^\n]*", "", strip_ts(txt))
        txt = re.sub(r",(\s*[}\]])", r"\1", txt)
        try:
            co = (json.loads(txt).get("compilerOptions") or {})
        except ValueError:
            continue
        base = co.get("baseUrl", ".")
        return {k: [os.path.normpath(os.path.join(base, v)).replace("\\", "/") for v in vs]
                for k, vs in (co.get("paths") or {}).items()}
    return {}


class Resolver:
    def __init__(self, root, files):
        self.files = set(files)
        self.paths = _tsconfig_paths(root)
        self.py_mods = {}
        for p in files:
            if p.endswith(".py"):
                mod = p[:-3].replace("/", ".")
                if mod.endswith(".__init__"):
                    mod = mod[:-9]
                for pre in ("", "src.", "scripts.", "lib."):
                    if mod.startswith(pre):
                        self.py_mods.setdefault(mod[len(pre):], p)
                self.py_mods.setdefault(mod.split(".")[-1], p) if "/" in p and p.split("/")[0] in ("scripts", "kit") else None

    def _ts_file(self, base):
        base = os.path.normpath(base).replace("\\", "/")
        if base in self.files:
            return base
        stem = re.sub(r"\.(js|mjs|cjs|jsx)$", "", base)
        for ext in TS_EXT:
            if stem + ext in self.files:
                return stem + ext
        for ext in TS_EXT:
            if base + "/index" + ext in self.files:
                return base + "/index" + ext
        return None

    def resolve(self, frm, spec, lang):
        if lang == "py":
            if spec in self.py_mods:
                return self.py_mods[spec]
            parts = spec.split(".")
            while parts:
                if ".".join(parts) in self.py_mods:
                    return self.py_mods[".".join(parts)]
                parts.pop()
            return None
        if spec.startswith("."):
            return self._ts_file(os.path.join(os.path.dirname(frm), spec))
        for pat, targets in self.paths.items():
            if pat.endswith("*") and spec.startswith(pat[:-1]):
                for t in targets:
                    r = self._ts_file(t.replace("*", spec[len(pat) - 1:]))
                    if r:
                        return r
            elif spec == pat:
                for t in targets:
                    r = self._ts_file(t)
                    if r:
                        return r
        return None


def _through_barrel(facts, res, target, names, seen=None):
    """`import {X} from "@/lib/moteur"` lands on an index that re-exports: the file that really holds X."""
    seen = seen or set()
    f = facts.get(target)
    if not f or target in seen or not f.get("reexports") or not names:
        return [target]
    seen.add(target)
    out = []
    for n in names:
        if n in f["exports"]:
            out.append(target); continue
        for rx in f["reexports"]:
            sub = res.resolve(target, rx["from"], f["lang"])
            if not sub:
                continue
            if n in rx["names"] or (rx["star"] and n in (facts.get(sub) or {}).get("exports", [])):
                out.append(sub); break
            if rx["star"] and (facts.get(sub) or {}).get("reexports"):
                deeper = _through_barrel(facts, res, sub, [n], seen)
                if deeper and deeper != [sub]:
                    out.extend(deeper); break
        else:
            out.append(target)
    return sorted(set(out))


# ── areas and features ─────────────────────────────────────────────────────────────────────────────────────────

def make_areas(files, split_over=30, min_sub=3):
    """An area is a top folder; a big one (over `split_over` files) is split into its subfolders, the small ones
    gathered as `<top>/*`. Files at the root form `(root)`."""
    tops = {}
    for p in files:
        parts = p.split("/")
        tops.setdefault(parts[0] if len(parts) > 1 else "(root)", []).append(p)
    areas = {}
    for top, fs in tops.items():
        if top == "(root)" or len(fs) <= split_over:
            areas[top] = fs; continue
        subs = {}
        for p in fs:
            parts = p.split("/")
            subs.setdefault(parts[1] if len(parts) > 2 else "", []).append(p)
        rest = []
        for sub, sfs in subs.items():
            if sub and len(sfs) >= min_sub:
                areas[top + "/" + sub] = sfs
            else:
                rest.extend(sfs)
        if rest:
            areas[top + "/*"] = rest
    return {a: sorted(fs) for a, fs in sorted(areas.items())}


def area_of(areas_index, path):
    return areas_index.get(path)


def make_features(files, areas_index, min_files=4, min_areas=2, cap=10):
    """A feature is a word in folder or file names that crosses areas (`jeu`: app/jeu, app/api/jeu, lib/jeu.ts...)."""
    by_seg = {}
    tops = {p.split("/")[0] for p in files if "/" in p}
    for p in files:
        parts = p.split("/")
        words = set()
        for d in parts[:-1]:
            words.add(d.lower())
        stem = os.path.splitext(parts[-1])[0]
        stem = re.sub(r"\.(test|spec)$", "", stem)
        words.update(w.lower() for w in re.split(r"[-_.]", stem) if w)
        for w in words:
            if len(w) < 3 or w in STOP_SEGMENTS or w in tops or w.startswith(("[", "(")) or w.isdigit():
                continue
            by_seg.setdefault(w, set()).add(p)
    feats = {}
    for w, fs in by_seg.items():
        code = [p for p in fs if not _is_test(p)]
        if len(code) >= min_files and len({areas_index[p] for p in code}) >= min_areas:
            feats[w] = sorted(fs)
    best = sorted(feats.items(), key=lambda kv: (-len({areas_index[p] for p in kv[1]}), -len(kv[1]), kv[0]))[:cap]
    return dict(best)


# ── the build ──────────────────────────────────────────────────────────────────────────────────────────────────

def _route_regex(path):
    pat = re.sub(r"\\\[\\\.\\\.\\\.[^/]*?\\\]", ".+", re.escape(path))
    pat = re.sub(r"\\\[[^/]*?\\\]", "[^/]+", pat)
    return re.compile("^" + pat + "/?$")


def _url_regex_text(url):
    return re.sub(r"\$\{[^}]*\}", "X", url)


def load_config(root, config_path=None):
    """The project's `map` settings, from --config or <root>/.kit/config.json (`map` or `kit.map`): a list of
    generator commands, or {"generators": [...], "csrf": [regex], "auth": [regex], "areas": {...}}."""
    cfg = {}
    for p in ([config_path] if config_path else [os.path.join(root, ".kit", "config.json")]):
        try:
            with open(p, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
    m = cfg.get("map", (cfg.get("kit") or {}).get("map", {}))
    if isinstance(m, list):
        m = {"generators": m}
    gens = []
    for g in m.get("generators") or []:
        if isinstance(g, str):
            g = {"run": g}
        g.setdefault("name", re.sub(r"[^\w-]+", "-", g["run"]).strip("-")[:40])
        gens.append(g)
    m["generators"] = gens
    return m


def run_generators(root, out, gens, facts, areas_index):
    done = []
    os.makedirs(os.path.join(out, "gen"), exist_ok=True)
    for g in gens:
        t0 = time.time()
        try:
            r = subprocess.run(g["run"], shell=True, cwd=root, capture_output=True, text=True,
                               timeout=int(g.get("timeout", 300)))
            rc, text = r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr.strip() else ""))
        except (OSError, subprocess.SubprocessError) as e:
            rc, text = 127, str(e)
        rel = "gen/%s.txt" % g["name"]
        with open(os.path.join(out, rel), "w", encoding="utf-8") as fh:
            fh.write("$ %s\n(exit %d)\n%s" % (g["run"], rc, text))
        area = g.get("area")
        cmd = g["run"]
        pm = re.match(r"\s*(?:pnpm|npm|yarn|bun)\s+(?:-s\s+|--silent\s+)?(?:run\s+)?([\w:.-]+)", cmd)
        if pm:   # `pnpm carte` runs what package.json says: `tsx scripts/carte-du-jeu.mts`
            try:
                with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
                    cmd += " " + ((json.load(fh).get("scripts") or {}).get(pm.group(1)) or "")
            except (OSError, ValueError):
                pass
        files_run = [tok for tok in re.findall(r"[\w./-]+\.\w+", cmd) if tok in areas_index]
        if not area and files_run:
            area = areas_index[files_run[0]]
        done.append({"name": g["name"], "run": g["run"], "rc": rc, "out": rel, "area": area, "files": files_run,
                     "seconds": round(time.time() - t0, 1), "lines": text.count("\n")})
    return done


def fingerprint(facts):
    h = hashlib.sha1()
    for p in sorted(facts):
        h.update(("%s %s\n" % (p, facts[p]["sha"])).encode())
    return h.hexdigest()[:16]


def build(root, out=None, config_path=None, run_gens=True):
    t0 = time.time()
    root = os.path.abspath(root)
    out = os.path.abspath(out or os.path.join(root, ".board", "map"))
    out_rel = os.path.relpath(out, root).replace("\\", "/") if out.startswith(root + os.sep) else None
    cfg = load_config(root, config_path)
    files = list_files(root, out_rel)
    facts = {}
    for name, reader in READERS.items():
        facts.update(reader(root, files))
    res = Resolver(root, files)
    areas = make_areas(files)
    for a, globs in (cfg.get("areas") or {}).items():   # a project may name an area by globs
        fs = [p for p in files if any(fnmatch.fnmatch(p, g) for g in globs)]
        if fs:
            for other in list(areas):
                areas[other] = [p for p in areas[other] if p not in fs]
                if not areas[other]:
                    del areas[other]
            areas[a] = fs
    areas_index = {p: a for a, fs in areas.items() for p in fs}
    features = make_features(files, areas_index)

    things, links, flags = [], [], []

    def link(frm, to, kind, file, evidence, line=None):
        links.append({"from": frm, "to": to, "kind": kind,
                      "check": {"reader": "code", "file": file, "contains": evidence}, **({"line": line} if line else {})})

    # files, imports (through barrels), tables, schemas
    imported_by = {p: set() for p in files}
    imports_of = {p: set() for p in files}
    tables, schemas = {}, {}
    for p, f in facts.items():
        things.append({"id": "file:" + p, "kind": "file", "path": p, "lines": f["lines"], "area": areas_index[p],
                       "exports": f["exports"][:40], "doc": f["doc"], "test": _is_test(p)})
        for imp in f["imports"]:
            t = res.resolve(p, imp["from"], f["lang"])
            if not t or t == p:
                continue
            for real in _through_barrel(facts, res, t, imp["names"]) if f["lang"] == "ts" else [t]:
                if real != p and real not in imports_of[p]:
                    imports_of[p].add(real); imported_by[real].add(p)
                    link("file:" + p, "file:" + real, "imports", p, imp["from"], imp["line"])
        for tn, t in f["tables"].items():
            tables[tn] = dict(t, file=p)
            things.append({"id": "table:" + t["sql"], "kind": "table", "name": tn, "file": p, "columns": t["columns"]})
        for zn, z in f["zod"].items():
            schemas.setdefault(zn, []).append((p, z))
    for p, f in facts.items():
        if f["lines"] > BIG_FILE:
            flags.append({"kind": "big-file", "thing": "file:" + p,
                          "text": "%s is %d lines: too big to read whole (over %d)" % (p, f["lines"], BIG_FILE)})

    # tests of each file: a test that imports it, or names its stem
    tests_of = {p: set() for p in files}
    test_files = [p for p in files if _is_test(p)]
    for tp in test_files:
        for real in imports_of[tp]:
            tests_of[real].add(tp)
    stems = {}
    for p in files:
        if not _is_test(p):
            stems.setdefault(os.path.splitext(os.path.basename(p))[0].lower(), []).append(p)
    for tp in test_files:
        base = os.path.basename(tp).lower()
        stem = re.sub(r"^test_|(_test)?\.py$|\.(test|spec)\.[a-z]+$|\.[a-z]+$", "", base)
        for p in stems.get(stem, []):
            if tp not in tests_of[p]:
                tests_of[p].add(tp)
                ev = os.path.splitext(os.path.basename(p))[0]
                if ev in facts[tp].get("_stripped", _read(os.path.join(root, tp))):
                    link("file:" + tp, "file:" + p, "tests", tp, ev)
                else:   # matched by its name only: the check is that both files are still there
                    links.append({"from": "file:" + tp, "to": "file:" + p, "kind": "tests",
                                  "check": {"reader": "code", "file": tp, "exists": [tp, p]}})
    for p, ts in tests_of.items():
        for tp in ts:
            if p in imports_of[tp]:
                links.append({"from": "file:" + tp, "to": "file:" + p, "kind": "tests",
                              "check": {"reader": "code", "file": tp, "imports": p}})

    # routes and the joins
    routes = []
    for p, f in facts.items():
        for r in f["routes"]:
            routes.append(dict(r, file=p, id="route:%s %s" % (r["method"], r["path"])))
    crons = []
    vj = _read(os.path.join(root, "vercel.json"))
    if vj:
        try:
            crons = [c.get("path", "") for c in json.loads(vj).get("crons") or []]
        except ValueError:
            pass
    all_calls = []
    for p, f in facts.items():
        if next_route_path(p) is not None:
            continue
        for c in f["calls"]:
            all_calls.append(dict(c, file=p))
    csrf_re = re.compile("|".join(cfg.get("csrf") or [CSRF_RE]), re.I)
    auth_re = re.compile("|".join(cfg.get("auth") or [AUTH_RE]), re.I)
    mw = [p for p in files if re.match(r"^(src/)?(middleware|proxy)\.(ts|js)$", p)]
    mw_csrf = any(csrf_re.search(facts[p]["_stripped"]) for p in mw if "_stripped" in facts[p])
    joins = []
    for r in sorted(routes, key=lambda r: (r["path"], r["method"])):
        f = facts[r["file"]]
        rx = _route_regex(r["path"])
        callers = [c for c in all_calls if rx.match(_url_regex_text(c["url"]).rstrip("/") or "/")
                   and (c["method"] in (None, r["method"]) or (c["method"] is None and r["method"] == "GET"))]
        callers = [c for c in callers if c["method"] == r["method"] or (c["method"] is None)]
        ui_callers = [c for c in callers if not _is_test(c["file"])]
        test_callers = sorted({c["file"] for c in callers if _is_test(c["file"])} | tests_of[r["file"]])
        things.append({"id": r["id"], "kind": "route", "method": r["method"], "path": r["path"], "file": r["file"]})
        for c in ui_callers:
            link("file:" + c["file"], r["id"], "calls", c["file"], c["url"], c["line"])
        link(r["id"], "file:" + r["file"], "handled-by", r["file"],
             "function %s" % r["method"] if f["lang"] == "ts" else r.get("handler", r["path"]))
        # the schema the handler parses with: a local one, else one of that name in a file it imports
        schema, schema_file, schema_name = None, None, None
        span = f["funcs"].get(r["method"]) if f["lang"] == "ts" else None
        body_txt = f.get("_stripped", "")[span[0]:span[1]] if span else f.get("_stripped", "")
        parsed = [n for n in dict.fromkeys(f["parses"]) if re.search(r"\b%s\s*\.\s*(safe)?[pP]arse" % re.escape(n), body_txt)]
        found = []
        for n in parsed:
            if n in f["zod"]:
                found.append((f["zod"][n], r["file"], n)); continue
            for sp_, z in schemas.get(n, []):
                if sp_ in imports_of[r["file"]]:
                    found.append((z, sp_, n)); break
        if len(found) == 1:
            schema, schema_file, schema_name = found[0]
        elif found:   # tried one after another (a creation, else a vote): the route accepts any of them
            schema = {"fields": {}, "mode": "strict" if all(z.get("mode") == "strict" for z, _, _ in found) else
                      "passthrough" if any(z.get("mode") == "passthrough" for z, _, _ in found) else "strip"}
            for z, _, _ in found:
                for k, v in z.get("fields", {}).items():
                    schema["fields"].setdefault(k, v)
            schema_file, schema_name = found[0][1], "+".join(n for _, _, n in found)
        if schema:
            things.append({"id": "schema:%s#%s" % (schema_file, schema_name), "kind": "schema", "file": schema_file,
                           "fields": _schema_fields(schema)})
            link(r["id"], "schema:%s#%s" % (schema_file, schema_name), "validates", r["file"], schema_name.split("+")[0] + ".")
        # tables: those named in the handler file, then one step through what it imports
        touched = sorted(tn for tn in tables if re.search(r"\b%s\b" % re.escape(tn), body_txt))
        via = {}
        for dep in imports_of[r["file"]]:
            df = facts.get(dep, {})
            if dep in {t["file"] for t in tables.values()}:
                continue
            for tn in tables:
                if tn not in touched and re.search(r"\.(from|insert|update|delete|into)\s*\(\s*%s\b" % re.escape(tn), df.get("_stripped", "")):
                    via.setdefault(tn, dep)
        for tn in touched:
            link(r["id"], "table:" + tables[tn]["sql"], "touches", r["file"], tn)
        for tn, dep in via.items():
            link(r["id"], "table:" + tables[tn]["sql"], "touches", dep, tn)
        # what the UI sends against what the schema keeps
        sent, dropped = set(), []
        for c in ui_callers:
            if c["body"]:
                sent.update(c["body"])
                if schema and schema.get("fields"):
                    for k in c["body"]:
                        if k not in schema["fields"] and schema["mode"] != "passthrough":
                            dropped.append({"field": k, "file": c["file"], "line": c["line"], "where": "body"})
        if schema:
            for fld, sub in schema.get("fields", {}).items():
                if sub and sub.get("union"):
                    key, members = sub["union"]["key"], sub["union"]["members"]
                    caller_files = {c["file"] for c in ui_callers}
                    for cf in caller_files:
                        for o in facts[cf]["objects"]:
                            if o["key"] == key and o["value"] in members:
                                keep = members[o["value"]]["fields"]
                                sent.update("%s.%s" % (fld, k) for k in o["keys"])
                                for k in o["keys"]:
                                    if k not in keep and members[o["value"]].get("mode") != "passthrough":
                                        dropped.append({"field": "%s.%s" % (fld, k), "file": cf, "line": o["line"],
                                                        "where": '%s "%s"' % (key, o["value"])})
        for d in dropped:
            mode = schema.get("mode", "strip")
            flags.append({"kind": "dropped-field", "thing": r["id"],
                          "text": "%s %s: `%s` is sent (%s:%d, %s) but the schema %s %s it" % (
                              r["method"], r["path"], d["field"], d["file"], d["line"], d["where"], schema_name,
                              "refuses" if mode == "strict" else "silently drops"),
                          "check": {"reader": "code", "file": d["file"], "contains": d["field"].split(".")[-1]}})
        guards = {}
        if r["method"] in WRITE_METHODS and f["lang"] == "ts":
            guards = {"csrf": bool(csrf_re.search(body_txt)) or mw_csrf, "auth": bool(auth_re.search(body_txt))}
            if re.search(cfg.get("public") or PUBLIC_RE, r["path"], re.I):
                guards["auth"] = "public"
            missing = [g for g in ("csrf", "auth") if not guards[g]]
            if missing and not r["path"].startswith(("/api/cron", "/api/webhook")):
                flags.append({"kind": "no-" + "-".join(missing), "thing": r["id"],
                              "text": "%s %s writes with no %s check (%s)" % (
                                  r["method"], r["path"], " and no ".join(m.upper() if m == "csrf" else m for m in missing),
                                  r["file"])})
        if not ui_callers and r["path"] not in crons:
            flags.append({"kind": "uncalled", "thing": r["id"],
                          "text": "%s %s: nothing in the code calls it%s" % (r["method"], r["path"],
                                                                            " (only tests)" if test_callers else "")})
        joins.append({"route": r["id"], "method": r["method"], "path": r["path"], "handler": r["file"],
                      "callers": sorted({"%s:%d" % (c["file"], c["line"]) for c in ui_callers}),
                      "sent": sorted(sent), "accepts": _schema_fields(schema) if schema else None,
                      "schema": "%s#%s" % (schema_file, schema_name) if schema else None,
                      "tables": touched + ["%s (via %s)" % (tn, d) for tn, d in sorted(via.items())],
                      "tests": test_callers, "guards": guards, "dropped": dropped})

    # received, never read: a field a screen declares for what a GET returns, read nowhere on any screen
    ui_files = [p for p in files if _is_ui(p) and facts[p]["lang"] == "ts"]
    ui_text = {p: facts[p]["_stripped"] for p in ui_files}
    get_callers = {}
    for j in joins:
        if j["method"] == "GET":
            for c in j["callers"]:
                get_callers.setdefault(c.rsplit(":", 1)[0], []).append(j["route"])
    for cf, rids in get_callers.items():
        if cf not in ui_text:
            continue
        for tname, t in facts[cf]["types"].items():
            for fld in t["fields"]:
                if len(fld) < 4:
                    continue
                pat = re.compile(r"\b%s\b" % re.escape(fld))
                reads = 0
                for p, s in ui_text.items():
                    for m in pat.finditer(s):
                        if p == cf and t["span"][0] <= m.start() < t["span"][1]:
                            continue
                        reads += 1
                        break
                    if reads:
                        break
                if not reads:
                    flags.append({"kind": "never-read", "thing": "file:" + cf,
                                  "text": "`%s` (type %s, %s:%d) comes from %s but no screen reads it" % (
                                      fld, tname, cf, t["line"], ", ".join(x.split(":", 1)[1] for x in sorted(set(rids))[:2])),
                                  "check": {"reader": "code", "file": cf, "contains": fld}})

    gens = run_generators(root, out, cfg["generators"], facts, areas_index) if run_gens and cfg["generators"] else []
    for g in gens:
        things.append({"id": "generator:" + g["name"], "kind": "generator", "run": g["run"], "area": g["area"]})
        links.append({"from": "generator:" + g["name"], "to": "area:%s" % g["area"], "kind": "generates",
                      "check": {"run": g["run"]}})
        if g["rc"] != 0:
            flags.append({"kind": "generator-failed", "thing": "generator:" + g["name"],
                          "text": "`%s` exits %d: its output says the code and its generated part disagree" % (g["run"], g["rc"])})

    # one line per area: what it is for (the entry file's leading comment), size, entry points
    area_rows = []
    for a, fs in areas.items():
        code = [p for p in fs if not _is_test(p)]
        ext_in = {p: len([q for q in imported_by[p] if areas_index[q] != a]) for p in fs}
        entry = sorted(fs, key=lambda p: (-(os.path.basename(p).split(".")[0] in ("index", "__init__", "main")),
                                          -ext_in[p], -facts[p]["lines"]))
        doc = next((facts[p]["doc"] for p in entry if facts[p]["doc"] and not facts[p]["doc"].startswith("(not read")), "")
        entries = []
        for p in fs:
            for r in facts[p]["routes"]:
                entries.append("%s %s" % (r["method"], r["path"]))
            pg = next_page_path(p)
            if pg:
                entries.append(pg)
            if facts[p].get("main"):
                entries.append(p)
        tests = sorted({t for p in fs for t in tests_of[p] if areas_index[t] != a} | {p for p in fs if _is_test(p)})
        area_rows.append({"area": a, "files": len(fs), "code": len(code), "lines": sum(facts[p]["lines"] for p in fs),
                          "for": doc, "entries": entries, "tests": tests,
                          "card": "areas/%s.md" % _card_name(a)})
        things.append({"id": "area:" + a, "kind": "area", "files": fs, "for": doc})
    feature_rows = []
    for w, fs in features.items():
        feature_rows.append({"feature": w, "files": len(fs), "lines": sum(facts[p]["lines"] for p in fs),
                             "areas": sorted({areas_index[p] for p in fs}), "card": "areas/feature-%s.md" % _card_name(w)})
        things.append({"id": "feature:" + w, "kind": "feature", "files": fs})

    commit = (_git(root, "rev-parse", "--short", "HEAD") or "").strip() or None
    dirty = bool((_git(root, "status", "--porcelain", "--untracked-files=no") or "").strip())
    data = {"version": 1, "root": root, "commit": commit, "dirty": dirty, "fingerprint": fingerprint(facts),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": None,
            "files": {p: facts[p]["sha"] for p in sorted(facts)},
            "stat": {p: facts[p].get("stat") for p in sorted(facts)},
            "areas": area_rows, "features": feature_rows, "joins": joins, "generators": gens,
            "things": things, "links": links, "flags": flags}
    os.makedirs(os.path.join(out, "areas"), exist_ok=True)
    for row in area_rows:
        _write(out, row["card"], area_card(row, areas[row["area"]], facts, imports_of, imported_by, tests_of,
                                           areas_index, joins, gens, flags))
    for row in feature_rows:
        _write(out, row["card"], area_card({"area": "feature " + row["feature"], "for": "", "entries": [],
                                            "tests": [], "lines": row["lines"]}, features[row["feature"]], facts,
                                           imports_of, imported_by, tests_of, areas_index, joins, gens, flags,
                                           feature=True))
    _write(out, "joins.md", joins_md(data))
    data["seconds"] = round(time.time() - t0, 2)
    ov = overview_md(data)
    _write(out, "overview.md", ov)
    data["overview_lines"] = ov.count("\n")
    _write(out, "map.json", json.dumps(data, indent=1, ensure_ascii=False, default=list) + "\n")
    return data, out


def _card_name(a):
    return re.sub(r"[^\w.-]+", "_", a.replace("/*", "-other").replace("/", "-")).strip("_") or "root"


def _write(out, rel, text):
    p = os.path.join(out, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _schema_fields(schema, prefix=""):
    if not schema:
        return []
    out = []
    for k, sub in schema.get("fields", {}).items():
        out.append(prefix + k)
        if sub and sub.get("union"):
            for lit, mem in sub["union"]["members"].items():
                out.extend(_schema_fields(mem, "%s%s[%s=%s]." % (prefix, k, sub["union"]["key"], lit)))
        elif sub and sub.get("fields"):
            out.extend(_schema_fields(sub, prefix + k + "."))
    if schema.get("union"):
        for lit, mem in schema["union"]["members"].items():
            out.extend(_schema_fields(mem, "%s[%s=%s]." % (prefix, schema["union"]["key"], lit)))
    return out


# ── the pages ──────────────────────────────────────────────────────────────────────────────────────────────────

def overview_md(d):
    L = []
    name = os.path.basename(d["root"].rstrip("/"))
    nfiles = len(d["files"])
    nlines = sum(r["lines"] for r in d["areas"])
    L.append("# Map of %s, built from %s%s in %.1fs" % (name, d["commit"] or "a folder (no git)",
                                                       " + uncommitted changes" if d["dirty"] else "", d["seconds"] or 0))
    L.append("%d code files, %d lines; generated by `kit.py map`, never edited by hand. `kit.py map --check`: still true?" % (nfiles, nlines))
    L.append("")
    L.append("## Areas (card: areas/<name>.md)")
    L.append("| area | for | files | lines | entry points |")
    L.append("|---|---|---|---|---|")
    rows = sorted(d["areas"], key=lambda r: -r["lines"])
    budget_areas = 22
    for r in rows[:budget_areas]:
        ent = ", ".join(r["entries"][:2]) + (" +%d" % (len(r["entries"]) - 2) if len(r["entries"]) > 2 else "")
        L.append("| %s | %s | %d | %d | %s |" % (r["area"], (r["for"] or "-")[:70].replace("|", "/"), r["files"], r["lines"], ent or "-"))
    if len(rows) > budget_areas:
        L.append("| +%d smaller areas | in map.json | %d | %d | |" % (len(rows) - budget_areas, sum(r["files"] for r in rows[budget_areas:]),
                                                                     sum(r["lines"] for r in rows[budget_areas:])))
    if d["features"]:
        L.append("")
        L.append("## Features across areas (card: areas/feature-<name>.md)")
        L.append("; ".join("**%s** %d files in %s" % (f["feature"], f["files"], ", ".join(f["areas"][:4]) +
                                                       (" +%d" % (len(f["areas"]) - 4) if len(f["areas"]) > 4 else ""))
                           for f in d["features"][:8]))
    if d["generators"]:
        L.append("")
        L.append("Generators: " + "; ".join("`%s` -> %s (exit %d)" % (g["run"], g["out"], g["rc"]) for g in d["generators"]))
    L.append("")
    order = ["dropped-field", "no-csrf-auth", "no-csrf", "no-auth", "never-read", "generator-failed", "uncalled", "big-file"]
    counts = {}
    for f in d["flags"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    L.append("## Flags at the joins: %s (all in joins.md)" % (", ".join("%d %s" % (counts[k], k) for k in order if k in counts) or "none"))
    room = MAX_OVERVIEW - len(L) - 1
    shown = 0
    flags = sorted(d["flags"], key=lambda f: order.index(f["kind"]) if f["kind"] in order else 99)
    per_kind = {}
    for f in flags:
        if shown >= room:
            break
        per_kind[f["kind"]] = per_kind.get(f["kind"], 0) + 1
        if per_kind[f["kind"]] > (6 if f["kind"] in ("dropped-field", "no-csrf", "no-csrf-auth") else 4 if f["kind"] == "never-read" else 3):
            continue
        L.append("- **%s** %s" % (f["kind"], f["text"]))
        shown += 1
    L.append("Joins table (route, fields sent / accepted, handler, tables, tests): joins.md")
    return "\n".join(L[:MAX_OVERVIEW]) + "\n"


def area_card(row, fs, facts, imports_of, imported_by, tests_of, areas_index, joins, gens, flags, feature=False):
    a = row["area"]
    L = ["# %s" % a, ""]
    if row.get("for"):
        L += ["For: " + row["for"], ""]
    L.append("%d files, %d lines." % (len(fs), sum(facts[p]["lines"] for p in fs)))
    fset0 = set(fs)
    code = [p for p in fs if not _is_test(p)]
    screens = sorted([p for p in code if _is_ui(p)], key=lambda p: -facts[p]["lines"])[:2]
    ncall = {j["route"]: len(j["callers"]) for j in joins}
    writes = sorted({"%s %s" % (r["method"], r["path"]) for p in code for r in facts[p]["routes"] if r["method"] in WRITE_METHODS},
                    key=lambda w: (-ncall.get("route:" + w, 0), w))[:3]
    lean = {}
    for p in code:
        for q in imports_of[p]:
            if q not in fset0 and not _is_test(q):
                lean[q] = lean.get(q, 0) + 1
    core = [q for q, _ in sorted(lean.items(), key=lambda kv: (-kv[1] * max(1, facts[kv[0]]["lines"]) ** 0.5, kv[0]))[:2]]
    start = (["screens " + ", ".join(screens)] if screens else []) + (["writes " + ", ".join(writes)] if writes else []) + \
        (["leans on " + ", ".join(core)] if core else [])
    if start:
        L.append("Start here: " + "; ".join(start) + ".")
    if row.get("entries"):
        L += ["", "## Entry points"] + ["- " + e for e in row["entries"]]
    L += ["", "## Files", "| file | lines | exports | what it is |", "|---|---|---|---|"]
    for p in sorted(fs, key=lambda p: -facts[p]["lines"]):
        ex = ", ".join(facts[p]["exports"][:8]) + (" +%d" % (len(facts[p]["exports"]) - 8) if len(facts[p]["exports"]) > 8 else "")
        L.append("| %s | %d%s | %s | %s |" % (p, facts[p]["lines"], " (big)" if facts[p]["lines"] > BIG_FILE else "",
                                            ex.replace("|", "/") or "-", (facts[p]["doc"] or "")[:80].replace("|", "/")))
    fset = set(fs)
    out_counts, in_counts = {}, {}
    for p in fs:
        for q in imports_of[p]:
            if q not in fset:
                out_counts[q] = out_counts.get(q, 0) + 1
        for q in imported_by[p]:
            if q not in fset:
                in_counts[q] = in_counts.get(q, 0) + 1
    if out_counts:
        L += ["", "## It leans on (imports out, by use)"]
        L += ["- %s (%d lines, %s) x%d" % (q, facts[q]["lines"], areas_index[q], n)
              for q, n in sorted(out_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
    if in_counts:
        L += ["", "## What leans on it (imported by)"]
        L += ["- %s (%s) x%d" % (q, areas_index[q], n) for q, n in sorted(in_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
    tests = sorted({t for p in fs for t in tests_of[p]} | {p for p in fs if _is_test(p)})
    L += ["", "## Tests"] + (["- " + t for t in tests[:40]] or ["- none found"])
    js = [j for j in joins if j["handler"] in fset or any(c.rsplit(":", 1)[0] in fset for c in j["callers"])]
    if js:
        L += ["", "## Routes it serves or calls (joins.md has the fields)"]
        L += ["- %s %s -> %s%s" % (j["method"], j["path"], j["handler"], (" [schema %s]" % j["schema"]) if j["schema"] else "") for j in js[:30]]
    g_here = [g for g in gens if g["area"] in (a, a.replace("feature ", "")) or fset & set(g.get("files") or [])]
    if g_here:
        L += ["", "## Its own generators"] + ["- `%s` (exit %d): [output](../%s)" % (g["run"], g["rc"], g["out"]) for g in g_here]
    fl = [f for f in flags if f["thing"].split(":", 1)[-1] in fset or any(j["route"] == f["thing"] for j in js)]
    if fl:
        L += ["", "## Flags"] + ["- **%s** %s" % (f["kind"], f["text"]) for f in fl[:30]]
    return "\n".join(L) + "\n"


def joins_md(d):
    L = ["# Joins: each route, what the UI sends, what the schema keeps, the handler, its tables, its tests", "",
         "Built from %s. A route with no caller, a field sent but dropped, a write with no CSRF or auth check is in Flags." % (d["commit"] or "a folder"), ""]
    L += ["## Flags (%d)" % len(d["flags"])] + ["- **%s** %s" % (f["kind"], f["text"]) for f in d["flags"]] + [""]
    L += ["## Routes (%d)" % len(d["joins"]), "| route | called from | sends | schema accepts | handler | tables | tests | guards |",
          "|---|---|---|---|---|---|---|---|"]
    for j in d["joins"]:
        acc = j["accepts"]
        L.append("| %s %s | %s | %s | %s | %s | %s | %s | %s |" % (
            j["method"], j["path"], ", ".join(j["callers"][:3]) + (" +%d" % (len(j["callers"]) - 3) if len(j["callers"]) > 3 else "") or "**nothing**",
            ", ".join(j["sent"][:12]) or "-",
            ("%s: " % j["schema"].split("#")[-1] + ", ".join([x for x in acc if "[" not in x][:12]) +
             (" (+%d in union members)" % len([x for x in acc if "[" in x]) if any("[" in x for x in acc) else "")) if acc is not None else "no schema found",
            j["handler"], ", ".join(j["tables"][:6]) or "-", ", ".join(j["tests"][:3]) + (" +%d" % (len(j["tests"]) - 3) if len(j["tests"]) > 3 else "") or "**none**",
            ", ".join("%s %s" % (k, v if isinstance(v, str) else "yes" if v else "**no**") for k, v in j["guards"].items()) or "-"))
    return "\n".join(L) + "\n"


# ── fresh or refused ───────────────────────────────────────────────────────────────────────────────────────────

def check(root, out=None, config_path=None, run_gens=True):
    """0 when the map still tells the truth: the code's fingerprint matches, and every link's check passes
    (each import, call, schema, table still written where the map says; each generator still exits 0)."""
    root = os.path.abspath(root)
    out = os.path.abspath(out or os.path.join(root, ".board", "map"))
    try:
        with open(os.path.join(out, "map.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return 1, "STALE: no map at %s: run `kit.py map`" % out
    out_rel = os.path.relpath(out, root).replace("\\", "/") if out.startswith(root + os.sep) else None
    files = list_files(root, out_rel)
    old, stat = d.get("files") or {}, d.get("stat") or {}
    now = {}
    for p in files:   # a file whose size and time did not move is not read again (a slow disk reads 800 files in a minute)
        try:
            st = os.stat(os.path.join(root, p))
        except OSError:
            continue
        now[p] = old[p] if p in old and stat.get(p) == [st.st_size, st.st_mtime_ns] else _sha(_read(os.path.join(root, p)))
    changed = sorted(p for p in set(now) | set(old) if now.get(p) != old.get(p))
    problems = []
    if changed:
        problems.append("%d code file(s) changed since the map was built from %s: %s" % (
            len(changed), d.get("commit") or "this folder", ", ".join(changed[:8]) + (" ..." if len(changed) > 8 else "")))
    cache, failed, ran = {}, [], 0
    for ln in d.get("links") or []:
        c = ln.get("check") or {}
        if "run" in c:
            if not run_gens:
                continue
            ran += 1
            try:
                r = subprocess.run(c["run"], shell=True, cwd=root, capture_output=True, text=True, timeout=300)
                ok = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
            if not ok:
                failed.append("%s -> %s: `%s` fails" % (ln["from"], ln["to"], c["run"]))
            continue
        fp = c.get("file")
        if not fp:
            continue
        ran += 1
        if fp not in cache:
            cache[fp] = strip_ts(_read(os.path.join(root, fp))) if fp.endswith(TS_EXT) else _read(os.path.join(root, fp))
        txt = cache[fp]
        if "contains" in c and c["contains"] not in txt:
            failed.append("%s -%s-> %s: `%s` is no longer in %s" % (ln["from"], ln["kind"], ln["to"], c["contains"], fp))
        elif "imports" in c and not (txt and os.path.isfile(os.path.join(root, c["imports"]))):
            failed.append("%s -%s-> %s: a file is gone" % (ln["from"], ln["kind"], ln["to"]))
        elif "exists" in c and not all(os.path.isfile(os.path.join(root, x)) for x in c["exists"]):
            failed.append("%s -%s-> %s: a file is gone" % (ln["from"], ln["kind"], ln["to"]))
    if failed:
        problems.append("%d of %d link checks fail: %s" % (len(failed), ran, "; ".join(failed[:6]) + (" ..." if len(failed) > 6 else "")))
    if problems:
        return 1, "STALE: " + " | ".join(problems) + "\nRun `kit.py map` to rebuild it."
    return 0, "map fresh: built from %s, %d files unchanged, %d link checks pass" % (d.get("commit") or "a folder", len(now), ran)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="kit.py map", description="a map of the project, generated from its code")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None, help="default <root>/.board/map")
    ap.add_argument("--config", default=None, help="a kit config holding `map` (default <root>/.kit/config.json)")
    ap.add_argument("--check", action="store_true", help="fail when the code changed since the map, or a link check fails")
    ap.add_argument("--no-generators", action="store_true")
    a = ap.parse_args(argv)
    if a.check:
        rc, msg = check(a.root, a.out, a.config, run_gens=not a.no_generators)
        print(msg)
        return rc
    d, out = build(a.root, a.out, a.config, run_gens=not a.no_generators)
    counts = {}
    for f in d["flags"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    print("map: %s (%d files, %d areas, %d routes, %d links, flags: %s) in %.1fs; overview %d lines" % (
        out, len(d["files"]), len(d["areas"]), len(d["joins"]), len(d["links"]),
        ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items())) or "none", d["seconds"], d["overview_lines"]))
    print("read first: %s" % os.path.join(out, "overview.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
