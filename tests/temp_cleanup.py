"""CL52: a test removes what it makes. Imported by a test file, it makes every folder that file's process creates with
tempfile.mkdtemp go away when the process ends, a failure or an error included (atexit runs on unittest's exit). A
process killed outright leaves its folders to run_all_tests.py, which gives each suite its own TMPDIR and removes it.

/tmp held about 5,600 folders left by test runs, each with a project copy and its own board token."""
import atexit
import shutil
import tempfile

_made = []
_real = getattr(tempfile.mkdtemp, "kit_real", tempfile.mkdtemp)


def mkdtemp(*a, **k):
    d = _real(*a, **k)
    _made.append(d)
    return d


mkdtemp.kit_real = _real


@atexit.register
def _remove():
    while _made:
        shutil.rmtree(_made.pop(), ignore_errors=True)


tempfile.mkdtemp = mkdtemp
