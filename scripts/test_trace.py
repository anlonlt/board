"""Which files of the kit a test suite really used: loaded by every Python process of the run (run_all_tests.py puts
this file in a small venv's site-packages, so a child with a clean environment or `-I` has it too). It only listens: an
audit hook that appends one line per use to the file named in kit_test_trace.txt beside it, for the repo named there.

    open <path>   read as text, here or by a child           copy <path>   read by install.py or shutil: copied
    mod <path>    its top level was executed                 fn <path>:<function>   so was this function
    served <path> the page's text, handed out by the board server
    use <name>    a copy of it, in some test's project, was read; usemod, usefn: was executed, its function too
    list <dir>    its names were read                        git / root    git ran on the repo / the repo was copied whole
    blind <path>  given to a process that is not listened to (node, another python): the runner looks inside it"""
import os
import sys
import threading


def _install():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit_test_trace.txt"), encoding="utf-8") as fh:
            out, repo = fh.read().splitlines()[:2]
    except (OSError, ValueError):
        return
    real, join, sep = os.path.realpath, os.path.join, os.sep
    repo = real(repo)
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return
    seen, busy = set(), [False]
    installer = os.path.basename((getattr(sys, "orig_argv", None) or [""])[0:] and next(
        (a for a in getattr(sys, "orig_argv", [])[1:] if a.endswith(".py")), "")) == "install.py"
    venv = real(os.path.dirname(os.path.dirname(sys.executable)))
    MARKS = (sep + ".kit" + sep, sep + ".claude" + sep, sep + ".agents" + sep, sep + ".gemini" + sep, sep + "scripts" + sep)

    def note(kind, path):
        if (kind, path) not in seen:
            seen.add((kind, path))
            os.write(fd, ("%s %s\n" % (kind, path)).encode("utf-8", "replace"))

    def place(path, base=None):
        """('in', path in the repo), ('copy', its name) for a file of a test's project, or None."""
        p = real(join(base or os.getcwd(), os.fsdecode(path)))
        if p == repo or p.startswith(repo + sep):
            return "in", os.path.relpath(p, repo)
        if any(m in p for m in MARKS) or p.endswith((".py", ".js")):
            return "copy", os.path.basename(p)
        return None

    def copying():
        try:
            return os.path.basename(sys._getframe(3).f_code.co_filename) == "shutil.py"
        except ValueError:
            return False

    def saw(path):
        w = place(path)
        if not w:
            return
        if w[1].endswith(".py"):
            # the interpreter reading a module it runs (noted as mod), or a traceback showing a line: not a test reading it
            try:
                by = os.path.basename(sys._getframe(2).f_code.co_filename)
            except ValueError:
                return
            if by.startswith(("<frozen importlib", "<frozen zipimport")) or by in ("linecache.py", "tokenize.py", "traceback.py", "inspect.py"):
                return
            if by.startswith("<frozen runpy") or by == "runpy.py":
                return
        if w[1].endswith(("shim.js", "board.html")) and os.path.basename(sys._getframe(2).f_code.co_filename) == "board_server.py":
            note("served", w[1] if w[0] == "in" else "kit/board/" + w[1].replace("board.html", "board.template.html"))
        elif w[0] == "copy":
            if not installer:
                note("use", w[1])
        else:
            note("copy" if installer or copying() else "open", w[1])

    places, codes, LIB = {}, set(), (os.path.dirname(os.__file__) + sep, sys.prefix + sep)

    def ran(code):
        """One function or module, the first time it runs in this process."""
        f = code.co_filename
        if f not in places:
            places[f] = None if f.startswith("<") else place(f)
        w = places[f]
        if not w or not w[1].endswith(".py"):
            return
        name = getattr(code, "co_qualname", code.co_name)
        if name == "<module>":
            note("mod" if w[0] == "in" else "usemod", w[1])
        else:
            note("fn" if w[0] == "in" else "usefn", w[1] + ":" + name.split(".<locals>")[0])

    def tracer(frame, event, arg):
        c = frame.f_code                       # one call per function call, and no line of it: None below
        if id(c) not in codes:
            codes.add(id(c))
            keep.append(c)
            if not busy[0] and c.co_filename[:1] == "/" and not c.co_filename.startswith(LIB):
                busy[0] = True
                try:
                    ran(c)
                except Exception:
                    pass
                finally:
                    busy[0] = False
        return None
    keep = []

    def started(argv, cwd, env):
        base = real(os.fsdecode(cwd)) if cwd else os.getcwd()
        words = [os.fsdecode(w) for w in (argv if isinstance(argv, (list, tuple)) else [argv]) if isinstance(w, (str, bytes, os.PathLike))]
        if not words:
            return
        prog = os.path.basename(words[0])
        shell = prog in ("bash", "sh", "env", "timeout", "nohup")
        # a python of this venv (by its path, or first on the PATH) listens too
        mine = prog.startswith("python") and (real(os.path.dirname(os.path.dirname(words[0]))) == venv if sep in words[0]
                                              else (env or os.environ).get("PATH", "").startswith(venv))
        ears = mine or (shell and (env or os.environ).get("PATH", "").startswith(venv))
        for w in words[1:] if not prog.endswith((".py", ".sh")) else words:
            if not w or len(w) > 400 or "\n" in w or w.startswith("-"):
                continue
            at = place(w, base)
            if not at or at[0] != "in":
                if at and not ears and prog != "git":
                    note("use", at[1])
                continue
            if at[1] == ".":
                note("git" if prog == "git" else "target" if prog.startswith("python") and len(words) > 2 else "root", ".")
            elif os.path.exists(join(repo, at[1])):
                if not ears and prog != "git":
                    note("blind", at[1])
                elif not at[1].endswith(".py"):
                    note("open", at[1])                     # a python file says itself what of it runs
        if prog == "git" and (base == repo or base.startswith(repo + sep)) and "-C" not in words:
            note("git", ".")

    def hook(event, a):
        if busy[0] or event not in ("open", "exec", "os.listdir", "os.scandir", "subprocess.Popen", "os.exec", "os.posix_spawn"):
            return
        busy[0] = True
        try:
            if event == "open":
                if isinstance(a[0], (str, bytes, os.PathLike)) and (not isinstance(a[1], str) or "r" in a[1]):
                    saw(a[0])
            elif event == "exec":
                c = a[0]
                if getattr(c, "co_name", "") == "<module>":
                    ran(c)
            elif event in ("os.listdir", "os.scandir"):
                if sys._getframe(1).f_code.co_filename.startswith("<frozen importlib"):
                    return                                  # an import looking along sys.path: what it loads is noted
                w = place("." if not a or a[0] is None else a[0]) if not a or not isinstance(a[0], int) else None
                if w and w[0] == "in":
                    note("copylist" if installer else "list", w[1])
            elif event == "subprocess.Popen":
                started(a[1], a[2], a[3])
            else:
                started(a[1], None, a[2])
        except Exception:
            pass
        finally:
            busy[0] = False

    sys.addaudithook(hook)
    threading.settrace(tracer)
    sys.settrace(tracer)


try:
    _install()
except Exception:
    pass
