#!/usr/bin/env python3
"""The board kit's tests, in one command: what the changes affect, in parallel, stopped at the first failure.

    python3 scripts/run_all_tests.py             the suites whose files changed since they last passed here
    python3 scripts/run_all_tests.py --all       every suite, not listened to: faster (before a push, a merge, a release)
    python3 scripts/run_all_tests.py --list      what would run, and why; runs nothing
    python3 scripts/run_all_tests.py access notify     the suites whose name has one of these words, whatever changed
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
                "KIT_CLI", "CLAUDE_CODE_REMOTE", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "AGY_PID",
                "CLAUDE_CODE_REMOTE_SESSION_ID")   # CL37: usage.py reads it as the session's commit trailer
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


class Files:
    """The repo's files as they are now: contents hashed once, names looked up once."""

    def __init__(self, root=REPO_ROOT):
        self.root, self._h, self._p, self._names = root, {}, {}, None

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
        """{"": the file without its functions, "f": function f, "C.m": a method}, hashed; {} when it does not parse."""
        if rel not in self._p:
            out = {}
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
                            out[q] = out.get(q, "") + "".join(lines[first - 1:b])
                            cut.append((first, b))
                        elif isinstance(n, ast.ClassDef):
                            out.setdefault(pre + n.name, "class")           # its body but for its methods is in the top level
                            walk(n.body, pre + n.name + ".")
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
            self._p[rel] = out
        return self._p[rel]

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
    for f, names in deps.get("fns", {}).items():
        for n in names:
            out["%s %s" % (f, n + "()" if n else "top level")] = files.part(f, n)
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


def tail(text, n=60):
    lines = text.rstrip().splitlines()
    i = next((k for k, x in enumerate(lines) if x.startswith(("ERROR: ", "FAIL: ", "FAIL "))), None)
    if i is not None:
        fails = [x for x in lines if x.startswith("FAIL ")]
        lines = fails[:20] if fails and not any(x.startswith("Traceback") for x in lines[i:]) else lines[max(0, i - 1):]
    return "\n".join(lines[:n] if i is not None else lines[-n:])


def run(todo, memo, files, jobs, keep_going, env, out=print, listen=True):
    """(results, stopped): the suites run, at most `jobs` at once; the first failure stops the others."""
    work = tempfile.mkdtemp(prefix="kit-tests-")
    live, results, queue, stopped, ears = {}, [], list(todo), False, Listeners(out)

    def start(name, cmd, folders):
        base = os.path.join(work, re.sub(r"\W+", "-", name))
        log = open(base + ".log", "wb")
        e, venv = dict(env), None
        if folders is None and listen:
            py, venv = ears.take(base + ".trace")
            if py:
                cmd = [py] + cmd[1:]
                e["PATH"] = os.path.dirname(py) + os.pathsep + env.get("PATH", "")
        p = subprocess.Popen(cmd, cwd=REPO_ROOT, env=e, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        live[p] = (name, folders, base, log, time.monotonic(), venv)

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
                name, folders, base, log, t0, venv = live.pop(p)
                log.close()
                if venv:
                    ears.free.append(venv)
                with open(base + ".log", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                dt, ok = time.monotonic() - t0, p.returncode == 0
                results.append((name, ok, dt))
                n = re.search(r"^Ran (\d+) tests?", text, re.M)
                skip = re.findall(r"^SKIP.*$", text, re.M)
                out("[%s] %s (%s%.1f s)%s" % ("PASS" if ok else "FAIL", name, (n.group(1) + " tests, ") if n else "", dt, (" " + skip[0][:90]) if skip else ""))
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
                    out("\n" + tail(text) + "\n")
                    if not keep_going:
                        stopped = True
                save_memo(memo)
            if stopped:
                for p in list(live):
                    kill(p)
                    live.pop(p)[3].close()
                break
    finally:
        for p in list(live):
            kill(p)
        shutil.rmtree(work, ignore_errors=True)
    return results, stopped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("words", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    t0, memo, files, todo, same = time.monotonic(), load_memo(), Files(), [], []
    for s in suites():
        if a.words:
            reason = "named" if any(w in s[0] for w in a.words) else None
        else:
            reason = "all" if a.all else why(memo, s[0], files)
        if reason:
            todo.append((s, reason))
        elif reason == "":
            same.append(s[0])
    # what failed last time first (it tells soonest), then the long ones (the short ones fill the gaps)
    todo.sort(key=lambda x: (not (memo.get(x[0][0]) or {}).get("failed"), -float((memo.get(x[0][0]) or {}).get("seconds") or 999)))
    if a.list:
        for s, reason in todo:
            print("%-44s %s" % (s[0], reason))
        print("%d to run, %d unchanged since they passed" % (len(todo), len(same)))
        return 0
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    env["KIT_NO_FUNNEL"] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # Every launcher a test install writes goes to a temp dir; the real ~/.local/bin is watched.
    tmp_bin = tempfile.mkdtemp(prefix="kit-test-bin-")
    env["KIT_BIN_DIR"] = tmp_bin
    real_bin = os.path.expanduser("~/.local/bin")
    before = bin_snapshot(real_bin)
    results, stopped = run([s for s, _ in todo], memo, files, max(1, a.j), a.keep_going, env, lambda *x: print(*x, flush=True), listen=not a.all)
    shutil.rmtree(tmp_bin, ignore_errors=True)
    changed = bin_changes(before, bin_snapshot(real_bin))
    if changed:
        print("[FAIL] files written to %s by the suites: %s" % (real_bin, ", ".join(changed)))
    failed = [n for n, ok, _ in results if not ok]
    print("=" * 60)
    print("SUMMARY: %d passed, %d failed%s, %d unchanged since they passed (%.0f s)" % (
        len(results) - len(failed), len(failed) + bool(changed),
        (", %d not run (stopped at the first failure)" % (len(todo) - len(results))) if stopped else "", len(same), time.monotonic() - t0))
    for n in failed:
        print("  FAIL " + n)
    return 1 if failed or changed else 0


if __name__ == "__main__":
    sys.exit(main())
