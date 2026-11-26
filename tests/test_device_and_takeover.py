"""CL15 (device session cap) and CL18 (web takeover): the checks that must fail when either is broken."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


device = load("kit_device_t", "scripts/device.py")
sys.path.insert(0, os.path.join(ROOT, "kit", "hooks"))
guard = load("kit_guard_t", "kit/hooks/kit_guard.py")
takeover = load("kit_takeover_t", "kit/web_takeover.py")

CHROMEBOOK = {"total_mb": 2779, "available_mb": 2300, "swap_total_mb": 0, "swap_free_mb": 0}


def chromebook(running, avail=2300, env=None, store=None):
    mem = dict(CHROMEBOOK, available_mb=avail)
    return device.survey(env=env or {}, mem=mem, procs=[(1, 450, "claude")], running=running, store=store or {})


class DeviceCap(unittest.TestCase):
    def setUp(self):
        # the gate counts the memory real jobs reserved (a test run registers itself as one): none here
        self._jobs, device.running_jobs = device.running_jobs, lambda: []

    def tearDown(self):
        device.running_jobs = self._jobs

    def test_meminfo_parsed(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("MemTotal:        2845696 kB\nMemFree: 100 kB\nMemAvailable:    2359296 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
        m = device.memory(fh.name)
        os.unlink(fh.name)
        self.assertEqual((m["total_mb"], m["available_mb"], m["swap_total_mb"]), (2779, 2304, 0))

    def test_chromebook_holds_three(self):
        self.assertEqual(chromebook(1)["max_sessions"], 3)

    def test_full_device_refuses(self):
        ok, why = device.gate(chromebook(3))
        self.assertFalse(ok)
        self.assertIn("holds 3", why)

    def test_low_memory_refuses_under_the_cap(self):
        ok, why = device.gate(chromebook(1, avail=800))
        self.assertFalse(ok)
        self.assertIn("MB of RAM free", why)

    def test_free_slot_allows(self):
        self.assertTrue(device.gate(chromebook(1))[0])

    def test_cloud_has_no_cap(self):
        s = chromebook(50, avail=100, env={"CLAUDE_CODE_REMOTE": "true"})
        self.assertIsNone(s["max_sessions"])
        self.assertTrue(device.gate(s, extra=10)[0])

    def test_owner_override_and_env(self):
        self.assertEqual(chromebook(1, store={"max_sessions": 5})["max_sessions"], 5)
        self.assertEqual(chromebook(1, env={"KIT_MAX_SESSIONS": "1"})["max_sessions"], 1)
        self.assertFalse(device.gate(chromebook(1, env={"KIT_MAX_SESSIONS": "1"}))[0])

    def test_big_machine_holds_more(self):
        s = device.survey(env={}, mem={"total_mb": 32768, "available_mb": 28000}, procs=[], running=2, store={})
        self.assertGreaterEqual(s["max_sessions"], 60)


class SessionsAskedOnce(unittest.TestCase):
    def test_resume_and_the_device_line_share_one_answer(self):
        from unittest import mock
        kit = load("kit_once_t", "scripts/kit.py")
        calls, real = [], subprocess.run

        def run(cmd, *a, **k):
            if list(cmd)[:2] == ["claude", "agents"]:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, '[{"name": "x-p1", "sessionId": "s1", "status": "busy"}]', "")
            return real(cmd, *a, **k)
        with mock.patch("subprocess.run", run):
            self.assertEqual(kit._alive_sessions(), {"x-p1", "s1"})
            self.assertIn("kit_device", type(kit._device()).__name__ + kit._device().__name__)
            kit._device().running_sessions([(1, 450, "claude")])
            self.assertEqual(len(calls), 1)
            kit._agents["at"] -= 60                                   # an old answer is asked again
            self.assertEqual(kit._alive_sessions(), {"x-p1", "s1"})
            self.assertEqual(len(calls), 2)

    def test_no_claude_is_not_no_session(self):
        from unittest import mock
        kit = load("kit_once_t2", "scripts/kit.py")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(kit._alive_sessions())
            self.assertIsNone(kit._alive_sessions())


class GuardCap(unittest.TestCase):
    def test_counts_only_real_launches(self):
        self.assertEqual(guard.session_launches('KIT_ROLE=part claude --bg -n a -w b "x"'), 1)
        self.assertEqual(guard.session_launches('python3 -I .kit/device.py --gate && KIT_ROLE=part claude --bg -n a "x"'), 1)
        self.assertEqual(guard.session_launches('agy --mode auto -p "Your prompt is docs/prompts/x.md"'), 1)
        self.assertEqual(guard.session_launches('git commit -m "claude --bg"'), 0)
        self.assertEqual(guard.session_launches("claude agents --json"), 0)
        self.assertEqual(guard.subagent_launch_count("invoke_subagent", {"TypeName": "self", "Role": "Part: CL9"}), 1)
        self.assertEqual(guard.subagent_launch_count("invoke_subagent", {"TypeName": "kit-cards", "Role": "Board Cards Builder"}), 0)
        self.assertEqual(guard.subagent_launch_count("invoke_subagent", {"TypeName": "self", "Role": "Board Clerk"}), 0)

    def test_refuses_past_the_cap(self):
        why = guard.capacity_refusal('claude --bg -n a "x"', ROOT, survey=chromebook(3))
        self.assertIn("holds 3", why or "")
        why_sub = guard.capacity_refusal("", ROOT, survey=chromebook(3), extra=1)
        self.assertIn("holds 3", why_sub or "")

    def test_allows_with_a_slot_and_in_the_cloud(self):
        self.assertIsNone(guard.capacity_refusal('claude --bg -n a "x"', ROOT, survey=chromebook(1)))
        cloud = chromebook(9, env={"CLAUDE_CODE_REMOTE": "true"})
        self.assertIsNone(guard.capacity_refusal('claude --bg -n a "x"', ROOT, survey=cloud))


class WebTakeover(unittest.TestCase):
    def test_part_branches(self):
        brs = ["main", "cli-agnostic", "worktree-cl8", "cl10", "wip-cl11", "worktree-cl1", "claude/fix-cl12"]
        self.assertEqual(takeover.part_branches(brs, "CL1"), ["worktree-cl1"])
        self.assertEqual(takeover.part_branches(brs, "CL10"), ["cl10"])
        self.assertEqual(takeover.part_branches(brs, "CL11"), ["wip-cl11"])
        self.assertEqual(takeover.part_branches(brs, "CL12"), ["claude/fix-cl12"])

    def test_stop_hook_quiet_off_the_web(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
        for script in ("kit/hooks/kit_web_save.py",):
            r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, script)], input="{}", capture_output=True,
                               text=True, env=env, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit/web_takeover.py"), "--stop-hook"], input="{}",
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        # agy Stop contract returns {"decision": "allow"}
        r_agy = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit/hooks/kit_web_save.py")],
                               input=json.dumps({"hookEventName": "Stop", "cwd": ROOT}),
                               capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r_agy.returncode, 0)
        self.assertEqual(json.loads(r_agy.stdout).get("decision"), "allow")
        # agy handover --dry-run
        r_ho = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "kit/handover.py"),
                               "--role", "orchestrator", "--cli", "agy", "--dry-run"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(r_ho.returncode, 0, r_ho.stderr)
        plan = json.loads(r_ho.stdout)
        self.assertEqual(plan["cli"], "agy")
        self.assertIn(".kit/orchestrator.md", plan["prompt"])
        self.assertIn("kit.py resume", plan["prompt"])

    def test_session_start_briefs_web_only(self):
        hook = os.path.join(ROOT, "kit", "hooks", "kit_session_start.py")
        payload = json.dumps({"session_id": "t", "hook_event_name": "SessionStart", "cwd": ROOT})
        base = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
        base["CLAUDE_PROJECT_DIR"] = ROOT
        off = subprocess.run([sys.executable, "-I", hook], input=payload, capture_output=True, text=True, env=base)
        on = subprocess.run([sys.executable, "-I", hook], input=payload, capture_output=True, text=True,
                            env=dict(base, CLAUDE_CODE_REMOTE="true"))
        self.assertNotIn("WEB:", off.stdout)
        self.assertIn("WEB:", on.stdout)


class TaskLoad(unittest.TestCase):
    """CL19: the load per task, learned on the device."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_CONFIG_HOME": self.tmp}
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old

    def test_kinds_inferred(self):
        self.assertEqual(device.infer_kind(["yt-dlp", "u"]), "fetch")
        self.assertEqual(device.infer_kind(["python3", "pipeline/bench/vr2/fetch_missing.py"]), "fetch")
        self.assertEqual(device.infer_kind(["pip", "install", "-U", "yt-dlp"]), "install")
        self.assertEqual(device.infer_kind(["python3", "chord_train.py", "train"]), "train")
        self.assertEqual(device.infer_kind(["python3", "-m", "pytest"]), "tests")
        self.assertEqual(device.infer_kind(["ls", "-la"]), "other")

    def test_footprint_learns_and_default_holds_until_three_runs(self):
        st = {}
        device.learn(st, "fetch", 10, 1, False, 300)
        self.assertEqual(device.footprint("fetch", st), 300)  # one tiny run does not make fetch light
        device.learn(st, "fetch", 900, 1, False, 300)
        device.learn(st, "fetch", 1000, 1, False, 300)
        self.assertEqual(device.footprint("fetch", st), 1200)

    def test_oom_raises_the_footprint(self):  # bar (b)
        st = {}
        for _ in range(3):
            device.learn(st, "import", 700, 5, False, 800)
        before = device.footprint("import", st)
        device.learn(st, "import", 900, 5, True, before)
        self.assertGreater(device.footprint("import", st), before)
        self.assertIn("killed for memory", st["lessons"][-1])

    def test_sigkill_with_memory_gone_is_an_oom(self):
        self.assertTrue(device._was_oom(137, 100, 500))  # killed with memory nearly gone
        self.assertFalse(device._was_oom(0, 100, 500))
        self.assertFalse(device._was_oom(1, 100, 500))

    def test_fit(self):  # bar (a), the rule
        self.assertTrue(device.fits(2000, 0, 1000, 500, 0)[0])
        self.assertFalse(device.fits(1400, 180, 1000, 500, 1)[0])
        self.assertTrue(device.fits(600, 0, 1000, 500, 0)[0])  # alone it always runs: it can never fit better

    def test_two_jobs_that_do_not_fit_run_one_after_the_other(self):  # bar (a), for real
        import threading
        import time
        st = {"kinds": {"big": {"runs": 3, "ooms": 0, "peaks": [500, 500, 500]}}}
        device.save_store(st)
        orig = device.memory
        device.memory = lambda *a, **k: {"total_mb": 2000, "available_mb": 1000, "swap_total_mb": 0,
                                         "swap_free_mb": 0, "cgroup_limit_mb": None}
        times = {}
        # each job writes when it really ran: the check is that the two never overlap, whichever the computer started first
        mark = os.path.join(self.tmp, "ran")
        jobf = os.path.join(self.tmp, "job.py")
        with open(jobf, "w") as fh:
            fh.write("import sys, time\nt0 = time.time()\ntime.sleep(1.5)\n"
                     "open(sys.argv[1], 'a').write('%s %r %r\\n' % (sys.argv[2], t0, time.time()))\n")

        def go(name):
            job = [sys.executable, jobf, mark, name]
            times[name + "0"] = time.time()
            device.run(job, kind="big", quiet=True)
            times[name + "1"] = time.time()
        try:
            a = threading.Thread(target=go, args=("a",))
            a.start()
            time.sleep(0.5)
            b = threading.Thread(target=go, args=("b",))
            b.start()
            a.join()
            b.join()
        finally:
            device.memory = orig
        ran = {}
        with open(mark) as fh:
            for line in fh:
                k, t0, t1 = line.split()
                ran[k] = (float(t0), float(t1))
        (s1, e1), (s2, e2) = sorted(ran.values())
        self.assertEqual(sorted(ran), ["a", "b"])
        self.assertGreaterEqual(s2, e1 - 0.01, "the second big job must start only after the first ended: %r" % ran)

    def test_cgroup_limit_beats_host_ram(self):  # bar (c)
        root = os.path.join(self.tmp, "cg")
        v1 = os.path.join(root, "memory", "user.slice")
        os.makedirs(v1)
        with open(os.path.join(v1, "memory.limit_in_bytes"), "w") as fh:
            fh.write(str(1024 << 20))
        with open(os.path.join(v1, "memory.usage_in_bytes"), "w") as fh:
            fh.write(str(300 << 20))
        pc = os.path.join(self.tmp, "cgroup")
        with open(pc, "w") as fh:
            fh.write("7:memory:/user.slice\n0::/\n")
        self.assertEqual(device.cgroup_limit(pc, root), (1024, 300))
        with open(os.path.join(self.tmp, "meminfo"), "w") as fh:
            fh.write("MemTotal: 33554432 kB\nMemAvailable: 30000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
        m = device.memory(os.path.join(self.tmp, "meminfo"), cgroup=(1024, 300))
        self.assertEqual((m["total_mb"], m["available_mb"], m["cgroup_limit_mb"]), (1024, 724, 1024))
        v2 = os.path.join(root, "app")
        os.makedirs(v2)
        with open(os.path.join(v2, "memory.max"), "w") as fh:
            fh.write("max")
        with open(os.path.join(v2, "memory.current"), "w") as fh:
            fh.write("1")
        with open(pc, "w") as fh:
            fh.write("0::/app\n")
        self.assertIsNone(device.cgroup_limit(pc, root))  # unlimited

    def test_load_line_in_plain_words(self):
        st = {"kinds": {"train": {"runs": 3, "ooms": 1, "peaks": [1500] * 3, "oom_floor_mb": 2400}}}
        s = device.survey(env={}, mem=dict(CHROMEBOOK), procs=[(1, 450, "claude")], running=1, store=st)
        self.assertIn("train one at a time", device.load_line(s, st))


if __name__ == "__main__":
    unittest.main()
