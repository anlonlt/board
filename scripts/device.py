#!/usr/bin/env python3
"""The device's session cap (CL15): how many agent sessions THIS machine can hold, measured, never guessed.

Every Claude Code / agy session is its own process of ~300-500 MB, so on a small machine RAM is the bound (the API does
the thinking; a GPU never is). Two crashes on a 2.7 GB Chromebook without swap came from launching past it.

    python3 -I .kit/device.py            # the survey, one line per fact
    python3 -I .kit/device.py --json     # the same as JSON
    python3 -I .kit/device.py --gate     # exit 0 if one more session fits now, else 1 and why (launch lines use it)
    python3 -I .kit/device.py --save     # store the survey machine-locally (step 0 does this)
    python3 -I .kit/device.py --set-cap N | --set-cap auto   # the owner's override for this machine

Measures (Linux /proc, macOS sysctl + vm_stat, Windows GlobalMemoryStatusEx; every probe optional): total and available
RAM, swap, cores, GPUs (information only), the sessions running now and the median memory each takes here.

  cap = max(1, (RAM total - base) // per session)        base 1000 MB: the OS, the board server, a browser tab
  one more fits now  <=>  running < cap  and  RAM available - per session >= headroom (500 MB)

A cloud session (Claude Code on the web, CLAUDE_CODE_REMOTE=true) has no cap: each new web session gets its own
container. The cap is per DEVICE: it lives in ~/.config/board-kit/device.json (never in the repo's committed config),
so a laptop, a Chromebook and a web container each get their own. KIT_MAX_SESSIONS overrides it for one shell.
"""
import ctypes
import json
import os
import platform
import re
import socket
import statistics
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


def memory(meminfo_path="/proc/meminfo"):
    """{total_mb, available_mb, swap_total_mb, swap_free_mb}; None for what this OS does not tell."""
    out = {"total_mb": None, "available_mb": None, "swap_total_mb": None, "swap_free_mb": None}
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
        return out
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


def running_sessions(procs=None):
    """Sessions running now: `claude agents --json` when it answers (it lists each session once, including the
    background ones), plus agy processes; else every claude/agy process."""
    procs = processes() if procs is None else procs
    agy = sum(1 for _, _, n in procs if n == "agy")
    out = _run(["claude", "agents", "--json"], timeout=15)
    try:
        listed = json.loads(out) if out.strip() else None
    except ValueError:
        listed = None
    if isinstance(listed, list):
        return len(listed) + agy
    return len(procs)


def per_session_mb(procs):
    sizes = [r for _, r, _ in procs if r >= 150]
    return int(statistics.median(sizes)) if sizes else DEFAULT_SESSION_MB


def survey(env=None, mem=None, procs=None, running=None, store=None):
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
    if avail is not None and avail - per * extra < s["headroom_mb"]:
        return False, ("%d MB of RAM free now; a session takes ~%d MB and %d MB must stay free: wait until one ends "
                       "or close something" % (avail, per, s["headroom_mb"]))
    return True, "%d of %s sessions run, %s MB free: one more fits" % (
        run, "?" if cap is None else cap, "?" if avail is None else avail)


def summary(s):
    """One line for the welcome, resume and the hooks."""
    if s["kind"] == "cloud":
        return "Device: cloud session (no cap: each web session has its own container); %s running here." % s["running"]
    cap = s["max_sessions"]
    free = None if cap is None else max(0, cap - s["running"])
    return "Device %s: holds %s sessions (%s; %s MB RAM, ~%d MB each), %d running, %s free slot%s; %s MB free now." % (
        s["host"], "?" if cap is None else cap, s["cap_source"], s["ram_total_mb"] or "?", s["per_session_mb"],
        s["running"], "?" if free is None else free, "" if free == 1 else "s", s["ram_available_mb"] or "?")


def main(argv=None):
    import argparse
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
        print(summary(s))
        print("  cores %s · swap %s MB · GPUs: %s" % (s["cores"], s["swap_total_mb"], ", ".join(s["gpus"]) or "none found"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
