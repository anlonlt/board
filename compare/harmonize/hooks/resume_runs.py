"""SessionStart: relaunch the long runs a container restart stopped (asked for in chat during part VR2-D, after a
restart at 00:05 on 2026-11-19 stopped its bench for an hour).

Registry: .claude/resume-runs.json, {"runs": [{name, command, running, done, needs}]}, paths from the repo root:
  command  the run, started detached (its own session, output to /dev/null: the run writes its own logs). It must be
           safe to start again: skip what is done (VR2-D's stepd_queue.sh is).
  running  a pattern for `pgrep -f`: if a process matches, nothing is started.
  done     a file; once it exists the run is finished: nothing is started (remove the entry in the part's last commit).
  needs    files that must exist (a venv, weights): if one is missing the run is NOT started and the line says which
           (a fresh container: the part's session sets them up and starts the command itself).
Prints one line per run for the session to read. Never fails the session start (exit 0).
Its test: python3 -I .claude/hooks/test_resume_runs.py
"""
import json, os, subprocess, sys


def check(root, runs, start, running):
    """-> lines; calls start(command) for each run to relaunch. running(pattern) -> bool."""
    out = []
    for r in runs:
        name = r.get("name", "?")
        done = r.get("done")
        if done and os.path.exists(os.path.join(root, done)):
            out.append("  %s: finished (%s); remove its entry from .claude/resume-runs.json" % (name, done))
            continue
        if running(r["running"]):
            out.append("  %s: running" % name)
            continue
        miss = [p for p in r.get("needs", []) if not os.path.exists(p if os.path.isabs(p) else os.path.join(root, p))]
        if miss:
            out.append("  %s: NOT relaunched, missing %s: set them up, then run %s" % (name, ", ".join(miss), r["command"]))
            continue
        start(r["command"])
        out.append("  %s: RELAUNCHED after a restart (%s)" % (name, r["command"]))
    return out


def _running(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def _start(root):
    def go(cmd):
        subprocess.Popen(["bash", "-c", cmd], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return go


def main():
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    f = os.path.join(root, ".claude", "resume-runs.json")
    try:
        runs = json.load(open(f)).get("runs", [])
    except (OSError, ValueError):
        return
    if runs:
        print("\nRUNS — long runs a restart may have stopped (.claude/resume-runs.json):")
        print("\n".join(check(root, runs, _start(root), _running)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001  a session start never fails on this
        print("resume_runs: %s" % e, file=sys.stderr)
    sys.exit(0)
