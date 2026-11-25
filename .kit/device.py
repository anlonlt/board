#!/usr/bin/env python3
"""The device's session cap (CL15): how many agent sessions THIS machine can hold, measured, never guessed.

Every Claude Code / agy session is its own process of ~300-500 MB, so on a small machine RAM is the bound (the API does
the thinking; a GPU never is). Two crashes on a 2.7 GB Chromebook without swap came from launching past it.

    python3 -I .kit/device.py            # the survey, one line per fact
    python3 -I .kit/device.py --json     # the same as JSON
    python3 -I .kit/device.py --gate     # exit 0 if one more session fits now, else 1 and why (launch lines use it)
    python3 -I .kit/device.py --save     # store the survey machine-locally (step 0 does this)
    python3 -I .kit/device.py --set-cap N | --set-cap auto   # the owner's override for this machine
    python3 -I .kit/device.py run [--kind K] -- <cmd>        # a heavy command: waits for room, runs, learns (CL19)

Measures (Linux /proc, macOS sysctl + vm_stat, Windows GlobalMemoryStatusEx; every probe optional): total and available
RAM, swap, cores, GPUs (information only), the sessions running now and the median memory each takes here.

  cap = max(1, (RAM total - base) // per session)        base 1000 MB: the OS, the board server, a browser tab
  one more fits now  <=>  running < cap  and  RAM available - per session >= headroom (500 MB)

A cloud session (Claude Code on the web, CLAUDE_CODE_REMOTE=true) has no cap: each new web session gets its own
container. The cap is per DEVICE: it lives in ~/.config/board-kit/device.json (never in the repo's committed config),
so a laptop, a Chromebook and a web container each get their own. KIT_MAX_SESSIONS overrides it for one shell.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

BASE_MB = 1000
HEADROOM_MB = 500
DEFAULT_SESSION_MB = 450
SESSION_NAMES = ("claude", "agy")


def store_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "board-kit", "device.json")


def load_store():
    try:
        with open(store_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_store(d):
    p = store_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)


def is_cloud(env=None):
    env = os.environ if env is None else env
    return str(env.get("CLAUDE_CODE_REMOTE") or "").lower() in ("1", "true", "yes")


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def cgroup_limit(proc_cgroup="/proc/self/cgroup", root="/sys/fs/cgroup"):
    """(limit_mb, used_mb) of this process's memory cgroup (v2 memory.max, v1 memory.limit_in_bytes), or None when
    unlimited / unreadable. A container's limit beats the host's RAM: its processes die at the limit."""
    try:
        with open(proc_cgroup, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    cands = []
    for l in lines:
        parts = l.split(":", 2)
        if len(parts) != 3:
            continue
        hid, ctrls, path = parts
        if hid == "0" and ctrls == "":
            cands.append((os.path.join(root, path.lstrip("/")), "memory.max", "memory.current"))
        elif "memory" in ctrls.split(","):
            cands.append((os.path.join(root, "memory", path.lstrip("/")), "memory.limit_in_bytes", "memory.usage_in_bytes"))
    best = None
    for base, lim_f, cur_f in cands:
        d = base
        while True:  # a parent's limit binds too: walk up to the cgroup root
            try:
                with open(os.path.join(d, lim_f)) as fh:
                    raw = fh.read().strip()
                with open(os.path.join(d, cur_f)) as fh:
                    cur = int(fh.read().strip())
                if raw != "max" and int(raw) < (1 << 50):
                    lim = int(raw)
                    if best is None or lim >> 20 < best[0]:
                        best = (lim >> 20, cur >> 20)
            except (OSError, ValueError):
                pass
            if d.rstrip("/") in (root.rstrip("/"), os.path.join(root, "memory").rstrip("/")) or d in ("/", ""):
                break
            d = os.path.dirname(d.rstrip("/"))
    return best


def memory(meminfo_path="/proc/meminfo", cgroup=None):
    """{total_mb, available_mb, swap_total_mb, swap_free_mb, cgroup_limit_mb}; None for what this OS does not tell."""
    out = {"total_mb": None, "available_mb": None, "swap_total_mb": None, "swap_free_mb": None, "cgroup_limit_mb": None}
    if os.path.exists(meminfo_path):
        kv = {}
        with open(meminfo_path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"(\w+):\s+(\d+)", line)
                if m:
                    kv[m.group(1)] = int(m.group(2)) // 1024
        avail = kv.get("MemAvailable")
        if avail is None and "MemFree" in kv:
            avail = kv["MemFree"] + kv.get("Cached", 0) + kv.get("Buffers", 0)
        out.update(total_mb=kv.get("MemTotal"), available_mb=avail, swap_total_mb=kv.get("SwapTotal"),
                   swap_free_mb=kv.get("SwapFree"))
        cg = cgroup_limit() if cgroup is None else (cgroup or None)
        if cg and out["total_mb"] and cg[0] < out["total_mb"]:
            out["cgroup_limit_mb"] = cg[0]
            out["total_mb"] = cg[0]
            out["available_mb"] = min(out["available_mb"] or cg[0], max(0, cg[0] - cg[1]))
        return out
    import platform
    sysname = platform.system()
    if sysname == "Darwin":
        total = _run(["sysctl", "-n", "hw.memsize"]).strip()
        if total.isdigit():
            out["total_mb"] = int(total) // (1 << 20)
        vm = _run(["vm_stat"])
        ps = re.search(r"page size of (\d+)", vm)
        page = int(ps.group(1)) if ps else 4096
        pages = {k: int(v) for k, v in re.findall(r"Pages (free|inactive|speculative|purgeable):\s+(\d+)", vm)}
        if pages:
            out["available_mb"] = sum(pages.values()) * page // (1 << 20)
        sw = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M\s+free = ([\d.]+)M", _run(["sysctl", "-n", "vm.swapusage"]))
        if sw:
            out["swap_total_mb"], out["swap_free_mb"] = int(float(sw.group(1))), int(float(sw.group(3)))
    elif sysname == "Windows":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        try:
            s = MS()
            s.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
                out.update(total_mb=s.ullTotalPhys >> 20, available_mb=s.ullAvailPhys >> 20,
                           swap_total_mb=max(0, (s.ullTotalPageFile - s.ullTotalPhys) >> 20))
        except (AttributeError, OSError):
            pass
    return out


def gpus():
    """GPU names, for information only (sessions use the API; RAM bounds them)."""
    import platform
    names = [l.strip() for l in _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]).splitlines()
             if l.strip()]
    if not names and platform.system() == "Darwin" and platform.machine() == "arm64":
        names = ["Apple silicon (unified memory)"]
    if not names and os.path.isdir("/dev/dri"):
        cards = sorted(x for x in os.listdir("/dev/dri") if x.startswith("card"))
        names = ["%s (integrated or unnamed)" % c for c in cards]
    return names


def processes():
    """[(pid, rss_mb, name)] of the agent sessions (claude / agy) running on this machine."""
    rows = []
    if os.path.isdir("/proc") and os.path.exists("/proc/self/status"):
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid, encoding="utf-8") as fh:
                    name = fh.read().strip()
                if name not in SESSION_NAMES:
                    continue
                with open("/proc/%s/status" % pid, encoding="utf-8") as fh:
                    rss = int((re.search(r"VmRSS:\s+(\d+)", fh.read()) or [0, 0])[1]) // 1024
                rows.append((int(pid), rss, name))
            except (OSError, ValueError, TypeError, IndexError):
                continue
        return rows
    for line in _run(["ps", "-axo", "pid=,rss=,comm="]).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            name = os.path.basename(parts[2].strip())
            if name in SESSION_NAMES:
                rows.append((int(parts[0]), int(parts[1]) // 1024, name))
    return rows


AGENTS = None            # kit.py gives its own answer of `claude agents --json`: it takes a second, and it just asked


def running_sessions(procs=None):
    """Sessions running now: `claude agents --json` when it answers (it lists each session once, including the
    background ones), plus agy processes; else every claude/agy process."""
    procs = processes() if procs is None else procs
    agy = sum(1 for _, _, n in procs if n == "agy")
    out = AGENTS() if AGENTS else _run(["claude", "agents", "--json"], timeout=15)
    try:
        listed = json.loads(out) if out.strip() else None
    except ValueError:
        listed = None
    if isinstance(listed, list):
        return len(listed) + agy
    return len(procs)


def per_session_mb(procs):
    import statistics
    sizes = [r for _, r, _ in procs if r >= 150]
    return int(statistics.median(sizes)) if sizes else DEFAULT_SESSION_MB


def survey(env=None, mem=None, procs=None, running=None, store=None):
    import platform
    import socket
    env = os.environ if env is None else env
    mem = memory() if mem is None else mem
    procs = processes() if procs is None else procs
    store = load_store() if store is None else store
    cloud = is_cloud(env)
    per = per_session_mb(procs)
    total = mem.get("total_mb")
    auto = None if cloud else (max(1, (total - BASE_MB) // per) if total else None)
    cap, source = auto, ("cloud: each web session gets its own container" if cloud else
                         "measured" if total else "unknown: this OS did not report its RAM")
    override = env.get("KIT_MAX_SESSIONS") or store.get("max_sessions")
    if override not in (None, "", "auto"):
        try:
            cap, source = max(1, int(override)), ("KIT_MAX_SESSIONS" if env.get("KIT_MAX_SESSIONS") else
                                                  "the owner's setting in %s" % store_path())
        except (TypeError, ValueError):
            pass
    return {
        "host": socket.gethostname(), "os": "%s %s" % (platform.system(), platform.machine()),
        "kind": "cloud" if cloud else "local", "cores": os.cpu_count(),
        "ram_total_mb": total, "ram_available_mb": mem.get("available_mb"),
        "swap_total_mb": mem.get("swap_total_mb"), "swap_free_mb": mem.get("swap_free_mb"),
        "gpus": gpus(), "per_session_mb": per, "base_mb": BASE_MB, "headroom_mb": HEADROOM_MB,
        "auto_cap": auto, "max_sessions": cap, "cap_source": source,
        "running": running_sessions(procs) if running is None else running,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def gate(s, extra=1):
    """(ok, why): whether `extra` more sessions fit on this device now."""
    if s["kind"] == "cloud":
        return True, "cloud session: no cap (each new web session gets its own container)"
    cap, run, avail, per = s["max_sessions"], s["running"], s["ram_available_mb"], s["per_session_mb"]
    if cap is not None and run + extra > cap:
        return False, ("this device holds %d sessions (%s) and %d run now: wait until one ends, or the owner raises "
                       "it with `python3 -I .kit/device.py --set-cap N`" % (cap, s["cap_source"], run))
    if avail is not None:
        try:
            avail -= sum(max(0, int(j.get("need_mb") or 0) - tree_rss_mb(j["pid"])) for j in running_jobs())
        except Exception:
            pass
    if avail is not None and avail - per * extra < s["headroom_mb"]:
        return False, ("%d MB of RAM free now; a session takes ~%d MB and %d MB must stay free: wait until one ends "
                       "or close something" % (avail, per, s["headroom_mb"]))
    return True, "%d of %s sessions run, %s MB free: one more fits" % (
        run, "?" if cap is None else cap, "?" if avail is None else avail)


def summary(s, with_load=True):
    """One line for the welcome, resume and the hooks (+ the heavy jobs' load, CL19)."""
    line = _summary(s)
    if with_load:
        try:
            line += " " + load_line(s)
        except Exception:
            pass
    return line


def _summary(s):
    if s["kind"] == "cloud":
        return "Device: cloud session (no cap: each web session has its own container); %s running here." % s["running"]
    cap = s["max_sessions"]
    free = None if cap is None else max(0, cap - s["running"])
    return "Device %s: holds %s sessions (%s; %s MB RAM, ~%d MB each), %d running, %s free slot%s; %s MB free now." % (
        s["host"], "?" if cap is None else cap, s["cap_source"], s["ram_total_mb"] or "?", s["per_session_mb"],
        s["running"], "?" if free is None else free, "" if free == 1 else "s", s["ram_available_mb"] or "?")


# ---- CL19: the load per task, learned on this device -------------------------------------------------------------
# A part's heavy commands run through `device.py run -- <cmd>`: it waits until the command's learned footprint fits,
# runs it, measures the peak memory of its process tree, and learns. On a small device heavy jobs serialize by
# themselves; on a big one they run side by side. Nothing is tuned by hand.

DEFAULT_KIND_MB = {"fetch": 300, "install": 600, "import": 800, "train": 2000, "tests": 500, "build": 800,
                   "other": 150}
KIND_RULES = [  # first match wins; on the command line as typed
    ("install", r"\b(pip3?|uv|poetry|npm|pnpm|yarn|apt(-get)?|brew|conda)\s+(install|add|ci|sync)\b"),
    ("tests", r"\b(pytest|unittest|run_all_tests|jest|vitest|tox|nox|go\s+test|cargo\s+test|npm\s+(run\s+)?test)\b"),
    ("fetch", r"\b(yt-dlp|youtube-dl|curl|wget|aria2c|rsync|scp|downtify|spotdl|gdown|git\s+(clone|fetch|lfs)|"
              r"huggingface-cli\s+download|hf\s+download)\b|fetch\w*\.py"),
    ("train", r"\b(train\w*\.py|accelerate\s+launch|torchrun|deepspeed)\b|\btrain\b"),
    ("import", r"\b(ffmpeg|demucs|sox|import\w*\.py|convert\w*\.py|extract\w*\.py|features?\w*\.py)\b"),
    ("build", r"\b(make|cmake|cargo\s+build|go\s+build|tsc|vite\s+build|webpack|npm\s+run\s+build|gradle)\b"),
]
KEEP_PEAKS = 20
MIN_RUNS = 3
POLL_S = 0.5


def infer_kind(cmd):
    line = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    for kind, rx in KIND_RULES:
        if re.search(rx, line):
            return kind
    return "other"


def footprint(kind, store=None):
    """MB this kind takes here: 1.2 x the 90th percentile of its measured peaks (the default until 3 runs), never
    under the floor its last out-of-memory set."""
    store = load_store() if store is None else store
    k = (store.get("kinds") or {}).get(kind) or {}
    peaks = sorted(k.get("peaks") or [])
    default = DEFAULT_KIND_MB.get(kind, DEFAULT_KIND_MB["other"])
    if peaks:
        p90 = peaks[min(len(peaks) - 1, int(round(0.9 * (len(peaks) - 1))))]
        mb = int(p90 * 1.2)
        if len(peaks) < MIN_RUNS:  # one small run does not make a kind light: the default holds until 3 runs
            mb = max(mb, default)
    else:
        mb = default
    return max(mb, int(k.get("oom_floor_mb") or 0), 50)


def learn(store, kind, peak_mb, seconds, oom, footprint_mb, cmd=""):
    """Record one run: its peak, and on an out-of-memory raise the kind's floor to 1.5 x what it was given (a lesson)."""
    kinds = store.setdefault("kinds", {})
    k = kinds.setdefault(kind, {"runs": 0, "ooms": 0, "peaks": []})
    k["runs"] = int(k.get("runs") or 0) + 1
    if oom:
        floor = int(max(peak_mb, footprint_mb) * 1.5)
        k["ooms"] = int(k.get("ooms") or 0) + 1
        k["oom_floor_mb"] = max(int(k.get("oom_floor_mb") or 0), floor)
        k["last_oom"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.setdefault("lessons", []).append(
            "%s: a `%s` job was killed for memory at %d MB peak (given %d MB): `%s` jobs now count %d MB here." % (
                k["last_oom"][:10], kind, peak_mb, footprint_mb, kind, k["oom_floor_mb"]))
        store["lessons"] = store["lessons"][-50:]
    else:
        k["peaks"] = ((k.get("peaks") or []) + [int(peak_mb)])[-KEEP_PEAKS:]
        k["last_s"] = int(seconds)
    return k


def fits(available_mb, reserved_mb, need_mb, headroom_mb, others_running):
    """(start now?, why). Alone, a job always starts (it can never fit better); with others it waits for room."""
    room = (available_mb or 0) - reserved_mb - need_mb
    if room >= headroom_mb:
        return True, "fits: %d MB free, %d MB promised to running jobs, needs %d MB" % (available_mb or 0, reserved_mb, need_mb)
    if not others_running:
        return True, ("alone: needs %d MB but only %d MB are free; it runs alone, the best this device can do" % (
            need_mb, (available_mb or 0) - headroom_mb))
    return False, "waits: needs %d MB, %d MB free, %d MB promised to %d running job%s" % (
        need_mb, available_mb or 0, reserved_mb, others_running, "" if others_running == 1 else "s")


def jobs_dir():
    return os.path.join(os.path.dirname(store_path()), "jobs")


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def running_jobs():
    """The heavy jobs running now on this machine (registered by `run`); stale entries are removed."""
    d, out = jobs_dir(), []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, encoding="utf-8") as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            continue
        if _alive(j.get("pid")):
            out.append(j)
        else:
            try:
                os.unlink(p)
            except OSError:
                pass
    return out


def tree_rss_mb(pid):
    """Resident memory of a process and all its descendants (Linux /proc; else ps), in MB."""
    kids, rss = {}, {}
    if os.path.exists("/proc/self/stat"):
        for x in os.listdir("/proc"):
            if not x.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % x) as fh:
                    st = fh.read()
                fields = st[st.rindex(")") + 2:].split()
                kids.setdefault(int(fields[1]), []).append(int(x))
                rss[int(x)] = int(fields[21]) * os.sysconf("SC_PAGE_SIZE") >> 20
            except (OSError, ValueError, IndexError):
                continue
    else:
        for line in _run(["ps", "-axo", "pid=,ppid=,rss="]).splitlines():
            a = line.split()
            if len(a) == 3 and all(v.isdigit() for v in a):
                kids.setdefault(int(a[1]), []).append(int(a[0]))
                rss[int(a[0])] = int(a[2]) >> 10
    total, todo, seen = 0, [int(pid)], set()
    while todo:
        q = todo.pop()
        if q in seen:
            continue
        seen.add(q)
        total += rss.get(q, 0)
        todo.extend(kids.get(q, []))
    return total


def _was_oom(rc, last_avail_mb, headroom_mb):
    """A SIGKILL (shell exit 137) with memory nearly gone, or the kernel's own words, is an out-of-memory."""
    if rc not in (-9, 137):
        return False
    log = _run(["dmesg"], timeout=5)[-20000:]
    if re.search(r"Out of memory|oom-kill|Killed process", log):
        return True
    return last_avail_mb is not None and last_avail_mb < headroom_mb * 2


def run(cmd, kind=None, max_wait_s=None, env=None, quiet=False):
    """Wait for room, run `cmd` (inherits the terminal), measure its tree's peak, learn. Returns the command's exit code."""
    import fcntl
    import time
    kind = kind or infer_kind(cmd)
    say = (lambda m: None) if quiet else (lambda m: print("[device] " + m, file=sys.stderr, flush=True))
    os.makedirs(jobs_dir(), exist_ok=True)
    lock_path = os.path.join(jobs_dir(), ".lock")
    t0, said = time.time(), None
    cloud = is_cloud(env)
    while True:
        with open(lock_path, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # decide + register as one step: two waiters never both take the last room
            store = load_store()
            need = footprint(kind, store)
            jobs = running_jobs()
            mem = memory()
            reserved = sum(max(0, int(j.get("need_mb") or 0) - tree_rss_mb(j["pid"])) for j in jobs)
            ok, why = fits(mem.get("available_mb"), reserved, need, HEADROOM_MB, len(jobs))
            if ok or (max_wait_s is not None and time.time() - t0 >= max_wait_s):
                proc = subprocess.Popen(cmd)
                with open(os.path.join(jobs_dir(), "%d.json" % proc.pid), "w", encoding="utf-8") as fh:
                    json.dump({"pid": proc.pid, "kind": kind, "need_mb": need, "cmd": " ".join(cmd)[:200],
                               "started": time.time()}, fh)
                break
        if said is None or time.time() - said >= 30:  # once, then every 30 s while it waits
            say("%s `%s` %s" % (kind, " ".join(cmd)[:60], why))
            said = time.time()
        time.sleep(2)
    if time.time() - t0 > 3:
        say("%s started after %ds" % (kind, time.time() - t0))
    peak, last_avail = 0, None
    try:
        while proc.poll() is None:
            peak = max(peak, tree_rss_mb(proc.pid))
            last_avail = memory(cgroup=False if cloud else None).get("available_mb")
            try:
                proc.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    rc = proc.returncode
    try:
        import resource
        peak = max(peak, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss >> (20 if sys.platform == "darwin" else 10))
    except (ImportError, OSError):
        pass
    oom = _was_oom(rc, last_avail, HEADROOM_MB)
    with open(lock_path, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        store = load_store()
        k = learn(store, kind, peak, time.time() - t0, oom, need, " ".join(cmd))
        save_store(store)
        try:
            os.unlink(os.path.join(jobs_dir(), "%d.json" % proc.pid))
        except OSError:
            pass
    if oom:
        say("%s was KILLED FOR MEMORY (peak %d MB): `%s` jobs now count %d MB here; rerun it, it will wait for room." % (
            kind, peak, kind, k["oom_floor_mb"]))
    elif peak > need:
        say("%s peaked at %d MB (counted %d MB); learned." % (kind, peak, need))
    return 128 + 9 if rc == -9 else (rc if rc >= 0 else 128 - rc)


def at_once(kind, s, store=None):
    """How many `kind` jobs fit side by side here, beside the sessions that run now."""
    total = s.get("ram_total_mb")
    if not total:
        return None
    budget = total - BASE_MB - s["per_session_mb"] * max(1, s.get("running") or 0)
    return max(1, budget // footprint(kind, store))


def load_line(s, store=None):
    """Plain words: 'here: fetch 4 at a time, train one at a time'; measured kinds first."""
    store = load_store() if store is None else store
    kinds = store.get("kinds") or {}
    if not kinds:
        return "Heavy jobs: nothing measured yet (parts run them through `.kit/device.py run -- <cmd>`)."
    parts = []
    for kind in sorted(kinds, key=lambda x: -int(kinds[x].get("runs") or 0)):
        n = at_once(kind, s, store)
        parts.append("%s %s (~%d MB%s)" % (kind, "one at a time" if n == 1 else "%s at a time" % n, footprint(kind, store),
                                          ", %d out-of-memory" % kinds[kind]["ooms"] if kinds[kind].get("ooms") else ""))
    return "Heavy jobs here: " + ", ".join(parts) + "."


def _other_measured():
    """`other` jobs are measured only once one of them turned out heavy (an out-of-memory)."""
    return bool(((load_store().get("kinds") or {}).get("other") or {}).get("ooms"))


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "run":
        rest = argv[1:]
        kind, max_wait = None, None
        while rest and rest[0] != "--":
            if rest[0] == "--kind" and len(rest) > 1:
                kind, rest = rest[1], rest[2:]
            elif rest[0] == "--max-wait" and len(rest) > 1:
                max_wait, rest = float(rest[1]), rest[2:]
            else:
                break
        if rest and rest[0] == "--":
            rest = rest[1:]
        if not rest:
            print("usage: device.py run [--kind K] [--max-wait S] -- <command ...>", file=sys.stderr)
            return 2
        if kind is None and infer_kind(rest) == "other" and not is_cloud() and not _other_measured():
            try:  # a light command: nothing to learn, so no wait, no measuring; it becomes the command itself
                os.execvp(rest[0], rest)
            except OSError as e:
                print("device.py run: %s: %s" % (rest[0], e), file=sys.stderr)
                return 127
        return run(rest, kind=kind, max_wait_s=max_wait)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 (and say why) if one more session does not fit now")
    ap.add_argument("--save", action="store_true", help="store the survey in %s" % store_path())
    ap.add_argument("--set-cap", default=None, help="N, or auto")
    a = ap.parse_args(argv)
    if a.set_cap is not None:
        st = load_store()
        if a.set_cap == "auto":
            st.pop("max_sessions", None)
        else:
            st["max_sessions"] = max(1, int(a.set_cap))
        save_store(st)
    s = survey()
    if a.save or a.set_cap is not None:
        st = load_store()
        st["survey"] = s
        save_store(st)
    if a.gate:
        ok, why = gate(s)
        print(("ok: " if ok else "NO FREE SLOT: ") + why, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    if a.json:
        print(json.dumps(s, indent=1))
    else:
        print(summary(s, with_load=False))
        print("  cores %s · swap %s MB · GPUs: %s" % (s["cores"], s["swap_total_mb"], ", ".join(s["gpus"]) or "none found"))
        print("  " + load_line(s))
        for ln in (load_store().get("lessons") or [])[-3:]:
            print("  lesson: " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
