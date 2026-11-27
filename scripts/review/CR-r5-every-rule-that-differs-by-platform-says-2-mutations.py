"""CR-r5 review: mutation check of R5's matrix. Copies the r5 checkout per mutation, applies it, runs the step's check."""
import shutil, subprocess, sys, os, re
SRC = sys.argv[1]
CHECK = ["python3", "-m", "unittest", "tests.test_platform_matrix", "tests.test_cl51_computer_artifact",
         "tests.test_launch_runtime", "tests.test_resume_one_prompt", "tests.test_kit_copies_current"]
def rows(name, pl, val):
    def f(s):
        i = s.index('    "%s": {' % name); j = s.index('\n    },', i)
        blk = s[i:j]; blk2 = re.sub(r'("%s": )[^,\n]+,' % pl, r'\g<1>%s,' % val, blk, count=1)
        assert blk != blk2; return s[:i] + blk2 + s[j:]
    return f
MUT = {
  "code ignores stale_gate (always gate)": lambda s: s.replace('if not _rule("stale_gate") or', 'if False or', 1),
  "code ignores reply_format (first site hardcoded agy)": lambda s: s.replace('rfmt = _rule("reply_format")', 'rfmt = "agy"', 1),
  "table: stale_gate cli -> True": rows("stale_gate", "cli", "True"),
  "table: artifact_steps cli -> True": rows("artifact_steps", "cli", "True"),
  "table: launcher agy -> self": rows("launcher", "agy", '"self"'),
  "table: real_project_run cli -> orchestrator": rows("real_project_run", "cli", '"orchestrator"'),
}
for name, f in MUT.items():
    d = "/tmp/review-r5-mut"; shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns(".git"))
    p = os.path.join(d, "scripts/kit.py"); s = open(p).read(); s2 = f(s); assert s2 != s, name
    open(p, "w").write(s2)
    r = subprocess.run(CHECK, cwd=d, capture_output=True, text=True, timeout=900)
    fails = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", r.stderr, re.M)))
    print("%-48s %s %s" % (name, "CAUGHT" if r.returncode else "MISSED", fails[:4]), flush=True)
