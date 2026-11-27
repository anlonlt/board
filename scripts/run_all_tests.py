#!/usr/bin/env python3
"""The board kit's tests, in one command: what the changes affect, in parallel, stopped at the first failure.

    python3 scripts/run_all_tests.py             the suites whose files changed since they last passed here
    python3 scripts/run_all_tests.py --all       every suite, not listened to: faster (before a push, a merge, a release)
    python3 scripts/run_all_tests.py --list      what would run, and why; runs nothing
    python3 scripts/run_all_tests.py access notify     the suites whose name has one of these words, whatever changed
    python3 scripts/run_all_tests.py --clean     the folders past test runs left in the temp folder (--yes removes them)
    --keep-going: past a failure    -j N: how many at once (default: the cores but one)

A suite is one test file. While it runs, scripts/test_trace.py notes the files of this repo that it and its child processes
read, and which functions they execute; its pass is remembered with their contents (.kit/run/tests.json, git-ignored). It
runs again when one of them changed: a Python file that was only executed counts by its top level and by the functions that
ran, any other by its whole text. A file install.py copied into a test's project counts by what that copy did; the
installer's own suites (test_install*) count every copy's text but a Python file's functions. A suite that failed runs
first the next time. A change that is not a file of the repo (a new commit, a tool updated, the time
of day) is not seen: that is what --all is for."""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMO = os.path.join(REPO_ROOT, ".kit", "run", "tests.json")
PAGE = ("kit/board", "kit/claude")          # a browser suite's files are not traced: the page's folders

SCRIPTS = [
    ("kit/board/test_board_local.py", [sys.executable, "kit/board/test_board_local.py"]),
    ("kit/board/test_strings.py", [sys.executable, "kit/board/test_strings.py"]),
    ("kit/board/test_kit_cards.py", [sys.executable, "kit/board/test_kit_cards.py"]),
    ("test_install.py", [sys.executable, "test_install.py"]),
    ("test_install_bd11.py", [sys.executable, "test_install_bd11.py"]),
    # CL12: the page in a real browser at a phone and a desktop width: no text off screen or cut by its box, the badge
    # clears once seen, a train lists its done steps (prints SKIP without a browser; KIT_REQUIRE_BROWSER=1 makes it fail)
    ("kit/board/test_page_layout.js", [shutil.which("node") or "node", "kit/board/test_page_layout.js"]),
    # CL23: a thread nested deep opens without freezing the page (it was nested grids: seconds per layout)
    ("kit/board/test_page_speed.js", [shutil.which("node") or "node", "kit/board/test_page_speed.js"]),
    ("kit/board/test_page_ux.js", [shutil.which("node") or "node", "kit/board/test_page_ux.js"]),
    # CL36: a post body draws a mermaid block, an svg block and an inline picture, as the document viewer does
    ("kit/board/test_page_body.js", [shutil.which("node") or "node", "kit/board/test_page_body.js"]),
]


def suites(root=REPO_ROOT):
    """[(name, command, untraced folders or None)]: one per file of tests/, then the scripts."""
    out = []
    for f in sorted(os.listdir(os.path.join(root, "tests"))):
        if re.match(r"^test_\w+\.py$", f):
            out.append(("tests/" + f, [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", f, "-f"], None))
    return out + [(n, c, PAGE if n.endswith(".js") else None) for n, c in SCRIPTS if os.path.exists(os.path.join(root, n))]


# Variables a kit session exports (its role, a board token or url). Inherited, they make the
# suites depend on who runs them (KIT_ROLE=clerk fails the hook tests), so every suite runs without them.
# CL26: so are the runtime markers the launchers read (a suite run from a web session must not launch as one).
SESSION_VARS = ("KIT_ROLE", "KIT_BOARD_TOKEN", "KIT_BOARD_URL", "KIT_BOARD_REPO", "KIT_BREAKS_COPY", "CLAUDE_PROJECT_DIR",
                "KIT_CLI", "KIT_PLATFORM", "KIT_EMBED_PREVIEW", "KIT_STALE_OK", "LMARENA", "ARENA_SANDBOX",
                "CLAUDE_CODE_REMOTE", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "AGY_PID",
                "ANTIGRAVITY_AGENT", "ANTIGRAVITY_CONVERSATION_ID", "ANTIGRAVITY_AGENTAPI_EXE", "ANTIGRAVITY_APP_DATA_DIR",
                "CLAUDE_CODE_REMOTE_SESSION_ID")   # CL37/R5: runtime and session markers stripped so suites are platform-neutral
# What a run writes, or what is not the kit's: never a reason to run again.
NOISE = re.compile(r"^(?:\.git|\.kit/run|\.kit/board_data|\.claude/worktrees)(?:/|$)|(?:^|/)__pycache__(?:/|$)|\.pyc$")


def bin_snapshot(path):
    """{name: (size, mtime_ns, link target)} of the files in path; {} when it does not exist."""
    out = {}
    try:
        names = os.listdir(path)
    except OSError:
        return out
    for n in names:
        f = os.path.join(path, n)
        try:
            st = os.lstat(f)
        except OSError:
            continue
        out[n] = (st.st_size, st.st_mtime_ns, os.readlink(f) if os.path.islink(f) else "")
    return out


def bin_changes(before, after):
    """Names added, removed or rewritten between two bin_snapshot results."""
    return sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))


def used_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def set_names(n):
    """The names a top-level assignment or import sets; [] when it sets something else (a[0], x.y)."""
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in n.names if a.name != "*"]
    out = []
    for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
        e = list(t.elts) if isinstance(t, (ast.Tuple, ast.List)) else [t]
        if not all(isinstance(x, ast.Name) for x in e):
            return []
        out += [x.id for x in e]
    return out


class Files:
    """The repo's files as they are now: contents hashed once, names looked up once."""

    def __init__(self, root=REPO_ROOT):
        self.root, self._h, self._p, self._words, self._names = root, {}, {}, {}, None

    def touched(self, names, since):
        """True when one of these was written after `since`: what the suite saw is not known."""
        for f in names:
            try:
                if os.stat(os.path.join(self.root, f)).st_mtime > since:
                    return True
            except OSError:
                pass
        return False

    def all(self):
        if self._names is None:
            r = subprocess.run(["git", "-C", self.root, "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True)
            self._names = [f for f in r.stdout.splitlines() if f and not NOISE.search(f)]
        return self._names

    def under(self, *folders):
        return [f for f in self.all() if any(f == d or f.startswith(d + "/") for d in folders)]

    def hash(self, rel):
        if rel not in self._h:
            p = os.path.join(self.root, rel)
            try:
                with open(p, "rb") as fh:
                    self._h[rel] = hashlib.sha1(fh.read()).hexdigest()
            except IsADirectoryError:
                self._h[rel] = "dir"
            except OSError:
                self._h[rel] = "none"
        return self._h[rel]

    def parts(self, rel):
        """{"": the file without its functions and names, "f": function f, "C.m": a method, "=X": the name X its top level
        sets or imports}, hashed; {} when it does not parse. self._words[rel]: the words each of them uses."""
        if rel not in self._p:
            out, words = {}, {}
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                    text = fh.read()
                lines = text.splitlines(keepends=True)
                tree, cut = ast.parse(text), []

                def walk(body, pre):
                    for n in body:
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            b = n.end_lineno
                            first = min([n.lineno] + [d.lineno for d in n.decorator_list])
                            q = pre + n.name
                            words.setdefault(q, set()).update(used_names(n))
                            out[q] = out.get(q, "") + "".join(lines[first - 1:b])
                            cut.append((first, b))
                        elif isinstance(n, ast.ClassDef):
                            out.setdefault(pre + n.name, "class")           # its body but for its methods is in the top level
                            walk(n.body, pre + n.name + ".")
                        elif not pre and n in tree.body and isinstance(n, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
                            for x in set_names(n):
                                out["=" + x] = out.get("=" + x, "") + (
                                    "".join(lines[n.lineno - 1:n.end_lineno]) if not isinstance(n, (ast.Import, ast.ImportFrom)) else
                                    " ".join([getattr(n, "module", None) or ""] + [al.name for al in n.names if (al.asname or al.name).split(".")[0] == x]))
                                words.setdefault("=" + x, set()).update(used_names(n) - {x})
                            if set_names(n):
                                cut.append((n.lineno, n.end_lineno))
                        else:
                            for k in ("body", "orelse", "finalbody"):
                                walk([x for x in getattr(n, k, []) if isinstance(x, ast.stmt)], pre)
                            for h in getattr(n, "handlers", []):
                                walk(h.body, pre)
                walk(tree.body, "")
                gone = {i for a, b in cut for i in range(a, b + 1)}
                out[""] = "".join(x for i, x in enumerate(lines, 1) if i not in gone and x.strip() and x.strip()[0] != "#")
                out = {k: hashlib.sha1(v.encode()).hexdigest() for k, v in out.items()}
            except (OSError, ValueError, SyntaxError):
                out = {}
            self._p[rel], self._words[rel] = out, words
        return self._p[rel]

    def words(self, rel, name):
        """The names this part of a file uses, and those its own top-level names are made of."""
        self.parts(rel)
        w, got, todo = self._words[rel], set(), [name]
        while todo:
            for x in w.get(todo.pop(), ()):
                if x not in got:
                    got.add(x)
                    todo.append("=" + x)
        return got

    def text_words(self, rel):
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                return set(re.findall(r"[A-Za-z_]\w*", fh.read()))
        except (OSError, UnicodeDecodeError):
            return set()

    def part(self, rel, name):
        """A function's hash; the whole file's when it has no such function now, or does not parse."""
        p = self.parts(rel)
        return p.get(name) or ("top" if p and name.rsplit(".", 1)[-1].startswith("<") else "gone:" + self.hash(rel))

    def names(self, folder):
        pre = "" if folder == "." else folder + "/"
        return sorted({f[len(pre):].split("/")[0] for f in self.all() if f.startswith(pre)})

    def named_in(self, rel, seen=None):
        """A script given to a process that is not listened to: it, and the kit's scripts it names, and theirs."""
        seen = set() if seen is None else seen
        if rel in seen:
            return seen
        seen.add(rel)
        if os.path.basename(rel) == "install.py":
            seen.update(self.under("kit", "scripts", "install.py"))
            return seen
        if not rel.endswith((".py", ".js", ".sh")):
            return seen
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return seen
        words = set(re.findall(r"[\w.-]+\.(?:py|js|html|json|md|sh)\b", text))
        for f in self.under("kit", "scripts", "install.py"):
            if os.path.basename(f) in words and not os.path.basename(f).startswith("test_"):
                self.named_in(f, seen) if f.endswith((".py", ".js", ".sh")) else seen.add(f)
        return seen


def plain(name):
    """board.template.html and the board.html installed from it: one name."""
    return os.path.basename(name).replace(".template", "")


def read_trace(path, files, name=""):
    """What a suite's pass rests on, from its trace: {"files": whole texts, "fns": {file: functions run}, "lists"}."""
    got = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines():
                kind, _, p = line.partition(" ")
                if not NOISE.search(p):
                    got.setdefault(kind, set()).add(p)
    except OSError:
        return None
    opened, fns = set(got.get("open", ())), {}
    for p in got.get("blind", ()):
        opened |= set(files.under(p)) if os.path.isdir(os.path.join(files.root, p)) else files.named_in(p)
    if "root" in got:
        opened.update(files.all())
    if got.get("served"):                         # every server hands out the page: it counts for the suites that name it
        try:
            with open(os.path.join(files.root, name), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = "shim"
        if re.search(r"shim|board\.html|<script|board\.template", text):
            opened.update(p for p in got["served"])
    for p in got.get("mod", ()):
        fns.setdefault(p, set()).add("")
    for x in got.get("fn", ()):
        p, _, f = x.rpartition(":")
        fns.setdefault(p, set()).update(("", f))
    used = {plain(u) for u in got.get("use", ())}
    ran = {}
    for u in got.get("usemod", ()):
        ran.setdefault(plain(u), set()).add("")
    for x in got.get("usefn", ()):
        u, _, f = x.rpartition(":")
        ran.setdefault(plain(u), set()).update(("", f))
    own = "test_install" in name
    # a copy that ran, whoever made it (install.py, a test): by name, every file of the kit that has it
    for f in files.under("kit", "scripts", "install.py"):
        if f.endswith(".py") and plain(f) in ran and (f in got.get("copy", ()) or f in opened):
            fns.setdefault(f, set()).update(ran[plain(f)])
            if f in opened and f not in used:
                opened.discard(f)
    for c in got.get("copy", ()):
        if c.endswith(".py") and own:
            fns.setdefault(c, set()).add("")
        elif c.endswith(".py"):
            if plain(c) in used:                  # a test that reads an installed script's text: is it there, wired, the same
                fns.setdefault(c, set()).add("")
        elif plain(c) in used or own:
            opened.add(c)
    opened = {p for p in opened if os.path.isfile(os.path.join(files.root, p))}
    return {"files": sorted(opened), "fns": {f: sorted(v) for f, v in sorted(fns.items()) if f not in opened},
            "lists": sorted(set(got.get("list", ())) | (set(got.get("copylist", ())) if own else set()))}


def stamps(deps, files):
    """{what: its hash now} for everything a pass rests on."""
    out = {f: files.hash(f) for f in deps["files"]}
    # a top-level name counts for a suite whose code that ran, or whose own text, uses it
    words = set()
    for f, names in deps.get("fns", {}).items():
        for n in names:
            out["%s %s" % (f, n + "()" if n else "top level")] = files.part(f, n)
            words |= files.words(f, n)
    for f in deps["files"]:
        if f.endswith((".py", ".js")) and os.path.basename(f).startswith("test"):
            words |= files.text_words(f)
    for f in deps.get("fns", {}):
        for x in files.parts(f):
            if x[:1] == "=" and (x[1:] in words or x[1:].startswith("__")):
                out["%s %s" % (f, x[1:])] = files.parts(f)[x]
    for d in deps["lists"]:
        out["names in " + d] = hashlib.sha1(" ".join(files.names(d)).encode()).hexdigest()
    out["python"] = sys.version
    return out


def why(memo, name, files):
    """Why this suite runs ('' : it passed on these very files)."""
    m = memo.get(name) or {}
    if m.get("failed"):
        return "failed last time"
    if not m.get("deps"):
        return "never passed here"
    was, now = m.get("stamps") or {}, stamps(m["deps"], files)
    ch = [k for k in now if now[k] != was.get(k)]
    return "" if not ch else "changed: " + ", ".join(ch[:3]) + (" and %d more" % (len(ch) - 3) if len(ch) > 3 else "")


class Listeners:
    """Small venvs, one per suite at a time, whose every python loads scripts/test_trace.py (also `-I`, also with a clean
    environment) and writes to that suite's file."""

    def __init__(self, out=print):
        self.free, self.n, self.why = [], 0, ""
        self.out = out

    def take(self, trace):
        """(its python, itself) or ("", None) when a venv cannot be made or hears nothing."""
        d = self.free.pop() if self.free else self._make()
        if not d:
            return "", None
        with open(os.path.join(self._site(d), "kit_test_trace.txt"), "w", encoding="utf-8") as fh:
            fh.write(trace + "\n" + REPO_ROOT + "\n")
        return os.path.join(d, "bin", "python3"), d

    def _site(self, d):
        return os.path.join(d, "lib", "python%d.%d" % sys.version_info[:2], "site-packages")

    def _make(self):
        if self.why:
            return None
        self.n += 1
        d = os.path.join(REPO_ROOT, ".kit", "run", "test-venv-%d.%d-%d" % (sys.version_info[0], sys.version_info[1], self.n))
        py = os.path.join(d, "bin", "python3")
        try:
            if not os.path.exists(py):
                import venv
                venv.EnvBuilder(with_pip=False, symlinks=True, system_site_packages=True).create(d)
            shutil.copyfile(os.path.join(REPO_ROOT, "scripts", "test_trace.py"), os.path.join(self._site(d), "kit_test_trace.py"))
            with open(os.path.join(self._site(d), "kit_test_trace.pth"), "w", encoding="utf-8") as fh:
                fh.write("import kit_test_trace\n")
            heard = os.path.join(d, "heard")                   # it must hear an isolated child, or nothing it says counts
            with open(os.path.join(self._site(d), "kit_test_trace.txt"), "w", encoding="utf-8") as fh:
                fh.write(heard + "\n" + REPO_ROOT + "\n")
            with open(heard, "w"):
                pass
            subprocess.run([py, "-I", os.path.join(REPO_ROOT, "scripts", "test_trace.py")], timeout=30, env={"PATH": os.defpath})
            with open(heard, encoding="utf-8") as fh:
                if "mod scripts/test_trace.py" not in fh.read():
                    raise RuntimeError("it hears nothing")
            return d
        except Exception as e:
            self.why = str(e) or type(e).__name__
            self.out("no listener (%s): every suite runs again next time" % self.why)
            return None


def load_memo():
    try:
        with open(MEMO, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_memo(memo):
    try:
        os.makedirs(os.path.dirname(MEMO), exist_ok=True)
        with open(MEMO + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(memo, fh, indent=1, sort_keys=True)
        os.replace(MEMO + ".tmp", MEMO)
    except OSError:
        pass


NOTES_REF = "kit-tests"   # git notes: a full run's progress, on the commit it ran on (pushed: any session resumes it)


def _git(root, *a, timeout=30):
    try:
        return subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(a, 1, "", str(e))


def run_commit(root):
    """(commit, why_not): the commit a full run's passes count for, or None and why (uncommitted changes to tracked
    files: a pass is only worth something for code that is in a commit)."""
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return None, "no commit here"
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        return None, "uncommitted changes (%d files): this run starts fresh and is not saved" % len(dirty.splitlines())
    return head.stdout.strip(), ""


def note_read(root, commit, fetch=True):
    """What a full run on `commit` already did: {"passed": {suite: seconds}, "failed": [...], ...} ({} for none)."""
    if fetch:
        _git(root, "fetch", "-q", "origin", "+refs/notes/%s:refs/notes/%s" % (NOTES_REF, NOTES_REF), timeout=20)
    r = _git(root, "notes", "--ref", NOTES_REF, "show", commit)
    try:
        d = json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def note_write(root, commit, note):
    """Record the run's progress on the commit (local at once; pushed by note_push)."""
    return _git(root, "-c", "user.name=board kit", "-c", "user.email=kit@localhost",
                "notes", "--ref", NOTES_REF, "add", "-f", "-m", json.dumps(note, sort_keys=True), commit).returncode == 0


def note_push(root, wait=False):
    """Push the notes so another session or device resumes the run; detached unless `wait`."""
    cmd = ["git", "-C", root, "push", "-q", "origin", "refs/notes/%s:refs/notes/%s" % (NOTES_REF, NOTES_REF)]
    try:
        if wait:
            return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def tail(text, n=60):
    lines = text.rstrip().splitlines()
    i = next((k for k, x in enumerate(lines) if x.startswith(("ERROR: ", "FAIL: ", "FAIL "))), None)
    if i is not None:
        fails = [x for x in lines if x.startswith("FAIL ")]
        lines = fails[:20] if fails and not any(x.startswith("Traceback") for x in lines[i:]) else lines[max(0, i - 1):]
    return "\n".join(lines[:n] if i is not None else lines[-n:])


TOOL_DIRS = re.compile(r"^claude-\d+$")   # Claude Code's own folder ($TMPDIR/claude-<uid>), made by any `claude` it runs


def strays(tmp, pgid):
    """{pid: command} of the processes a finished suite left running: its process group, and (Linux) any process whose
    TMPDIR is the suite's own folder, even one that left the group (start_new_session)."""
    out = {}
    try:
        pids = [int(x) for x in os.listdir("/proc") if x.isdigit()]
    except OSError:
        pids = []
    mark = ("TMPDIR=" + tmp).encode()
    for pid in pids:
        try:
            with open("/proc/%d/environ" % pid, "rb") as fh:
                env = fh.read().split(b"\0")
            mine = mark in env
            if not mine and os.getpgid(pid) != pgid:
                continue
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                cmd = [x.decode("utf-8", "replace") for x in fh.read().split(b"\0") if x]
            with open("/proc/%d/stat" % pid, "rb") as fh:
                if fh.read().rsplit(b")", 1)[-1].split()[0] == b"Z":
                    continue                     # a zombie holds nothing
        except (OSError, ValueError, IndexError):
            continue
        out[pid] = " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd[:3]))[:80]
    if not pids:                                 # no /proc: the group alone
        try:
            os.killpg(pgid, 0)
            out[-pgid] = "process group %d" % pgid
        except OSError:
            pass
    return out


def leftovers(tmp, pgid):
    """CL52: (left, swept) of a finished suite: `left` what fails it, the folders it left in its own temp folder and its
    processes still running (killed here); `swept` the plain files the kit's scripts keep there by name (a lock, a
    flag), removed with the folder."""
    try:
        names = sorted(os.listdir(tmp))
    except OSError:
        names = []
    left = [n + "/" for n in names if os.path.isdir(os.path.join(tmp, n)) and not os.path.islink(os.path.join(tmp, n))
            and not TOOL_DIRS.match(n)]
    swept = [n for n in names if n + "/" not in left and not TOOL_DIRS.match(n)]
    procs = strays(tmp, pgid)
    for pid in procs:
        try:
            os.killpg(-pid, signal.SIGKILL) if pid < 0 else os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    left += ["process: " + c for c in procs.values()]
    return left, swept


def run(todo, memo, files, jobs, keep_going, env, out=print, listen=True, on_done=None, work=None):
    """(results, stopped): the suites run, at most `jobs` at once; the first failure stops the others. `on_done(name,
    ok, seconds)` after each suite (a full run records its progress on the commit). CL52: each suite has its own temp
    folder (TMPDIR) under the run's folder `work`; what it leaves there fails it, named in its result line, and the
    run's folder is removed at the end, whatever happened."""
    own_work = work is None
    work = work or tempfile.mkdtemp(prefix="kit-tests-")
    live, results, queue, stopped, ears = {}, [], list(todo), False, Listeners(out)

    def start(name, cmd, folders):
        base = os.path.join(work, re.sub(r"\W+", "-", name))
        log = open(base + ".log", "wb")
        e, venv = dict(env), None
        tmp = base + ".tmp"
        os.makedirs(tmp, exist_ok=True)
        e["TMPDIR"] = e["TMP"] = e["TEMP"] = tmp
        if folders is None and listen:
            py, venv = ears.take(base + ".trace")
            if py:
                cmd = [py] + cmd[1:]
                e["PATH"] = os.path.dirname(py) + os.pathsep + env.get("PATH", "")
        p = subprocess.Popen(cmd, cwd=REPO_ROOT, env=e, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        live[p] = (name, folders, base, log, time.monotonic(), venv, tmp)

    def kill(p):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()

    try:
        while queue or live:
            while queue and len(live) < jobs and not stopped:
                name, cmd, folders = queue.pop(0)
                if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]):
                    out("SKIP %s: %s is not installed (say so; not a pass)" % (name, cmd[0]))
                    continue
                start(name, cmd, folders)
            done = [p for p in live if p.poll() is not None]
            if not done:
                time.sleep(0.05)
                continue
            for p in done:
                name, folders, base, log, t0, venv, tmp = live.pop(p)
                log.close()
                left, swept = leftovers(tmp, p.pid)
                shutil.rmtree(tmp, ignore_errors=True)
                if venv:
                    ears.free.append(venv)
                with open(base + ".log", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                dt, ok = time.monotonic() - t0, p.returncode == 0 and not left
                results.append((name, ok, dt))
                n = re.search(r"^Ran (\d+) tests?", text, re.M)
                skip = re.findall(r"^SKIP.*$", text, re.M)
                out("[%s] %s (%s%.1f s)%s%s" % ("PASS" if ok else "FAIL", name, (n.group(1) + " tests, ") if n else "", dt,
                                              (" " + skip[0][:90]) if skip else "",
                                              (" LEFT %d: %s%s" % (len(left), ", ".join(left[:4]),
                                               ", ..." if len(left) > 4 else "")) if left else ""))
                m = memo.setdefault(name, {})
                m["seconds"] = round(dt, 1)
                if ok and (listen or folders is not None):
                    deps = {"files": sorted(files.under(*folders)), "lists": []} if folders is not None else read_trace(base + ".trace", files, name)
                    if deps:
                        deps["files"] = sorted(set(deps["files"]) | ({name} if os.path.exists(os.path.join(REPO_ROOT, name)) else set()))
                        fresh = not files.touched(deps["files"] + list(deps.get("fns", {})), time.time() - dt)
                        m.update(deps=deps, stamps=stamps(deps, files) if fresh else {}, failed=False)
                elif ok:
                    m["failed"] = False                     # not listened to: what it rests on stays as last noted
                else:
                    m["failed"] = True
                    if p.returncode != 0:
                        out("\n" + tail(text) + "\n")
                    if not keep_going:
                        stopped = True
                save_memo(memo)
                if on_done:
                    on_done(name, ok, dt)
            if stopped:
                for p in list(live):
                    kill(p)
                    live.pop(p)[3].close()
                break
    finally:
        for p in list(live):
            kill(p)
        if own_work:
            shutil.rmtree(work, ignore_errors=True)
    return results, stopped


# CL52: what test runs leave in the temp folder, by name. /tmp held about 5,600 such folders on a computer with little
# room, each with a project copy and its own board token. The kit's own prefixes are made only by its tests and its
# runner; Python's bare mkdtemp() ("tmp" and 8 letters) is anyone's, so such a folder counts only when it holds what a
# test makes there (a project copy, a board, a git repository).
TEST_PREFIXES = ("kit-tests-", "kit-test-bin-", "kit-test-jobs-", "kit-xdg-", "kit-cl11-", "kit-cl36-", "kit-body-", "kit-ux-",
                 "kit-page-", "kit-demo-", "kit-new-", "kit-back-", "kit-cheap-", "kit-compact-", "kit-mirror-", "kit-sync-db-",
                 "kit-update-board-", "kit-usage-index-", "kitmap-", "cl30-", "cl48-",
                 # the throwaway copies the kit's own commands make (done's breaks, land, pair, a wip save): gone when old
                 "kit-breaks-", "kit-land-", "kit-pair-", "kit-wip-",
                 "playwright-artifacts-", "playwright_chromiumdev_profile-")
TEST_MARKS = (".kit", ".git", ".board", ".claude", "board_data", "board.db", "AGENTS.md", "CLAUDE.md", "repo", "proj",
              "project", "home", "kit")
BARE_TMP = re.compile(r"^tmp[a-z0-9_]{8}$")
# what the kit's hooks keep in the temp folder for a live session (any session working now writes them): not a test's
SESSION_STATE = ("kit-orch-role-", "kit-handover-", "kit-memory-jit-", "kit-ledger-")


def newest(path):
    """The latest mtime of a folder and of what it holds directly (a live run writes its logs one level down)."""
    t = os.lstat(path).st_mtime
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    t = max(t, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return t


def test_leftovers(root=None, older_h=2.0, now=None):
    """[(path, why)] of the folders past test runs left in `root` (the temp folder): a test's name and untouched for
    `older_h` hours (a run going on now is never one), owned by this user."""
    root, now = root or tempfile.gettempdir(), now or time.time()
    out = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return out
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False) or e.stat(follow_symlinks=False).st_uid != os.getuid():
                continue
            if e.name.startswith(TEST_PREFIXES):
                why = "a test run's name (%s*)" % next(x for x in TEST_PREFIXES if e.name.startswith(x))
            elif BARE_TMP.match(e.name):
                inside = set(os.listdir(e.path))
                marks = sorted(inside & set(TEST_MARKS))
                if not marks:
                    continue
                why = "tmp* holding " + ", ".join(marks[:3])
            else:
                continue
            age = (now - newest(e.path)) / 3600
        except OSError:
            continue
        if age >= older_h:
            out.append((e.path, "%s, untouched for %.0f h" % (why, age)))
    return sorted(out)


def clean(argv):
    """`run_all_tests.py --clean [--yes] [--older H] [--root DIR]`: the leftovers of past test runs; a dry run (the list)
    unless --yes."""
    ap = argparse.ArgumentParser(prog="run_all_tests.py --clean")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--yes", action="store_true", help="remove them (without it: only the list)")
    ap.add_argument("--older", type=float, default=2.0, help="hours untouched (default 2: a run going on is kept)")
    ap.add_argument("--root", default=None, help="the temp folder (default: this computer's)")
    a = ap.parse_args(argv)
    found = test_leftovers(a.root, a.older)
    for path, why in found:
        print("%s %s  (%s)" % ("removed" if a.yes else "would remove", path, why))
        if a.yes:
            shutil.rmtree(path, ignore_errors=True)
    left = [p for p, _ in found if os.path.exists(p)] if a.yes else []
    print("%d folder(s) of past test runs in %s%s%s" % (
        len(found), a.root or tempfile.gettempdir(), ": removed" if a.yes and found else
        (" (dry run: --yes removes them)" if found else ""), ("; %d could not be removed: %s" % (len(left), ", ".join(left[:5]))) if left else ""))
    return 1 if left else 0


def through_device(argv):
    """Every run goes through the device's gate (`device.py run --kind tests`): it waits until a test run fits in the
    memory free now and learns what a run takes, so no session puts the computer past what it holds. None when this
    run already is the device's job, or there is no device script; else the exit code of the run it started."""
    if os.environ.get("KIT_DEVICE_JOB") or "--list" in argv:
        return None
    dev = next((p for p in (os.path.join(REPO_ROOT, ".kit", "device.py"), os.path.join(REPO_ROOT, "scripts", "device.py"))
                if os.path.isfile(p)), None)
    if not dev:
        return None
    env = dict(os.environ, KIT_DEVICE_JOB="1")
    return subprocess.run([sys.executable, "-I", dev, "run", "--kind", "tests", "--", sys.executable,
                           os.path.abspath(__file__)] + list(argv), env=env).returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--clean" in argv:
        return clean(argv)
    rc = through_device(argv)
    if rc is not None:
        return rc
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("words", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="--all from the start, not resuming this commit's run")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    t0, memo, files, todo, same = time.monotonic(), load_memo(), Files(), [], []
    # A full run is a task like any other: its progress is on the commit (git notes), so a run stopped anywhere is
    # resumed by any session on any device; only what has not passed on this commit runs again.
    commit, note, resumed = None, {}, []
    if a.all and not a.words:
        commit, why_not = run_commit(REPO_ROOT)
        if commit and not a.fresh:
            note = note_read(REPO_ROOT, commit, fetch=not a.list)
        elif not commit and not a.list:            # --list prints the suites and nothing else
            print("full run not saved on a commit: %s" % why_not, flush=True)
        note = dict(note, commit=commit, passed=dict(note.get("passed") or {}), failed=list(note.get("failed") or []))
    for s in suites():
        if a.words:
            reason = "named" if any(w in s[0] for w in a.words) else None
        elif a.all and s[0] in note.get("passed", {}):
            resumed.append(s[0])
            reason = None
        else:
            reason = "all" if a.all else why(memo, s[0], files)
        if reason:
            todo.append((s, reason))
        elif reason == "":
            same.append(s[0])
    if resumed:
        print("resuming the full run on %s: %d suites already passed on it, %d to run (--fresh: all again)"
              % (commit[:9], len(resumed), len(todo)), flush=True)
    # what failed last time first (it tells soonest), then the long ones (the short ones fill the gaps)
    todo.sort(key=lambda x: (not (memo.get(x[0][0]) or {}).get("failed"), -float((memo.get(x[0][0]) or {}).get("seconds") or 999)))
    if a.list:
        for s, reason in todo:
            print("%-44s %s" % (s[0], reason))
        print("%d to run, %d unchanged since they passed" % (len(todo), len(same)))
        return 0
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    env["KIT_NO_FUNNEL"] = "1"
    # CL48: a test run: every write to a board, a board's git copy or a remote outside a temp folder is refused
    # (kit/test_guard.py; the Tide Clock install test reached the real board-mirror on 11-20)
    env["KIT_TEST"] = "1"
    env["KIT_TEST_GUARD"] = os.path.join(REPO_ROOT, "kit", "test_guard.py")   # for a test's hand-made copy of a script
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # CL52: one folder for the whole run (each suite's TMPDIR, the launchers, the jobs, the logs), removed at the end,
    # also when the run is stopped (Ctrl-C, a kill)
    work = tempfile.mkdtemp(prefix="kit-tests-")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    # Every launcher a test install writes goes to a temp dir; the real ~/.local/bin is watched.
    tmp_bin = os.path.join(work, "bin")
    os.makedirs(tmp_bin)
    env["KIT_BIN_DIR"] = tmp_bin
    # the device's jobs the suites see: none (this run is one, and a test of the gate must not count it)
    env["KIT_JOBS_DIR"] = os.path.join(work, "jobs")
    os.makedirs(env["KIT_JOBS_DIR"])
    real_bin = os.path.expanduser("~/.local/bin")
    before = bin_snapshot(real_bin)
    # CL52: what a suite writes straight into the computer's temp folder, past its own TMPDIR (a path written as /tmp)
    temp_root = os.path.dirname(work)
    temp_before = set(os.listdir(temp_root))
    last_push = [0.0]

    def record(name, ok, dt):
        if not commit:
            return
        if ok:
            note["passed"][name] = round(dt, 1)
            note["failed"] = [n for n in note["failed"] if n != name]
        elif name not in note["failed"]:
            note["failed"].append(name)
        note["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        note["total"] = len(suites())
        note_write(REPO_ROOT, commit, note)
        if time.monotonic() - last_push[0] > 60:      # at most a push a minute, never waited for
            last_push[0] = time.monotonic()
            note_push(REPO_ROOT)

    try:
        results, stopped = run([s for s, _ in todo], memo, files, max(1, a.j), a.keep_going, env,
                               lambda *x: print(*x, flush=True), listen=not a.all, on_done=record, work=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if commit:
            note_push(REPO_ROOT, wait=True)
    changed = bin_changes(before, bin_snapshot(real_bin))
    if changed:
        print("[FAIL] files written to %s by the suites: %s" % (real_bin, ", ".join(changed)))
    strayed = sorted(n for n in set(os.listdir(temp_root)) - temp_before if (n.startswith(TEST_PREFIXES) or BARE_TMP.match(n)
                     or re.match(r"^(?:kit|cl\d)", n)) and not n.startswith(SESSION_STATE))
    if strayed:
        print("[FAIL] left in %s past the suites' own temp folders: %s" % (temp_root, ", ".join(strayed[:8])))
        changed = changed + strayed
    failed = [n for n, ok, _ in results if not ok]
    print("=" * 60)
    print("SUMMARY: %d passed, %d failed%s, %d unchanged since they passed%s (%.0f s)" % (
        len(results) - len(failed), len(failed) + bool(changed),
        (", %d not run (stopped at the first failure)" % (len(todo) - len(results))) if stopped else "", len(same),
        (", %d passed earlier on this commit" % len(resumed)) if resumed else "", time.monotonic() - t0))
    for n in failed:
        print("  FAIL " + n)
    old = test_leftovers()
    if old:
        print("%d folder(s) left in %s by past test runs: `python3 scripts/run_all_tests.py --clean` lists them, --yes removes them"
              % (len(old), tempfile.gettempdir()))
    return 1 if failed or changed else 0


if __name__ == "__main__":
    sys.exit(main())
