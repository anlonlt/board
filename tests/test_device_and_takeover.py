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


class GuardCap(unittest.TestCase):
    def test_counts_only_real_launches(self):
        self.assertEqual(guard.session_launches('KIT_ROLE=part claude --bg -n a -w b "x"'), 1)
        self.assertEqual(guard.session_launches('python3 -I .kit/device.py --gate && KIT_ROLE=part claude --bg -n a "x"'), 1)
        self.assertEqual(guard.session_launches('git commit -m "claude --bg"'), 0)
        self.assertEqual(guard.session_launches("claude agents --json"), 0)

    def test_refuses_past_the_cap(self):
        why = guard.capacity_refusal('claude --bg -n a "x"', ROOT, survey=chromebook(3))
        self.assertIn("holds 3", why or "")

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


if __name__ == "__main__":
    unittest.main()
