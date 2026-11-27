"""CR of CL16: the kit path as it runs for real (KIT_MIRROR_SYNC=bg: detached worker, debounced), not the
test's `inline` mode. Two step changes inside one debounce window: both must reach origin/board-mirror.
Run from a checkout of the cl16 branch: python3 <this> (it imports that branch's tests/test_cl16_board_in_git.py)."""
import json, os, sys, time, unittest
sys.path.insert(0, os.getcwd())
from tests.test_cl16_board_in_git import Board, TOKEN


class BgPath(Board):
    test_a_step_change_reaches_origin_even_after_another_machines_cursor = None
    test_a_failed_sync_is_said_in_the_server_log_and_retried = None
    test_a_refused_commit_is_an_error_not_committed = None
    test_kit_step_changes_push_the_board_without_the_servers_auto_sync = None
    test_a_fresh_clone_without_a_local_board_resumes_the_same = None
    test_a_write_from_the_git_side_shows_on_the_local_board_after_import = None
    test_both_sides_wrote_the_newer_copy_of_each_post_wins_nothing_lost = None

    def test_bg_two_changes_in_one_window_both_reach_origin(self):
        url, _ = self.serve(sync="0", push=False)
        env = dict(KIT_BOARD_URL=url, KIT_BOARD_TOKEN=TOKEN, KIT_MIRROR_SYNC="bg", KIT_MIRROR_SYNC_DEBOUNCE="3")
        t0 = time.time()
        r = self.kit("step", "new", "--train", "cli", "--part", "CL95", "--title", "bg path", "--task", "one",
                     "--keep-train", "test", **env)
        pid = json.loads(r.stdout)["post"]
        self.assertEqual(self.kit("step", "start", pid, **env).returncode, 0)
        end = time.time() + 30
        got = None
        while time.time() < end:
            got = self.on_origin(pid, timeout=0.5)
            if got and got.get("status") == "in progress":
                break
            time.sleep(0.3)
        print("\n[bg] origin status after %.1f s: %s" % (time.time() - t0, got and got.get("status")))
        self.assertEqual((got or {}).get("status"), "in progress")


if __name__ == "__main__":
    unittest.main(defaultTest="BgPath", verbosity=2)
