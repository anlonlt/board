"""resume_runs.py: a stopped run is relaunched; a running, finished or unready one is not. Each case seen red by
breaking its rule in check() (2026-11-19). Run: python3 -I .claude/hooks/test_resume_runs.py"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resume_runs as R  # noqa: E402


def run(runs, alive=()):
    started = []
    d = tempfile.mkdtemp()
    open(os.path.join(d, "finished.log"), "w").close()
    open(os.path.join(d, "venv"), "w").close()
    lines = R.check(d, runs, started.append, lambda p: p in alive)
    return started, lines


def test_all():
    base = dict(command="q.sh", running="q.sh", done="not-yet.log", needs=["venv"])
    s, l = run([dict(base, name="a")])
    assert s == ["q.sh"] and "RELAUNCHED" in l[0]                      # stopped: started
    s, l = run([dict(base, name="a")], alive=("q.sh",))
    assert s == [] and "running" in l[0]                                 # running: left alone
    s, l = run([dict(base, name="a", done="finished.log")])
    assert s == [] and "finished" in l[0]                                # finished: not started again
    s, l = run([dict(base, name="a", needs=["venv", "weights.pt"])])
    assert s == [] and "weights.pt" in l[0]                              # a fresh container: says what is missing


if __name__ == "__main__":
    test_all()
    print("test_resume_runs: OK")
